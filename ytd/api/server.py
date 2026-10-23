"""Master Download Manager's local API: the bridge between the browser
extensions and the download queue running inside the desktop app.
Binds to 127.0.0.1 only; every data endpoint needs the security token."""

import os
import shutil
import threading

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from .. import __version__
from ..config import APP_NAME
from ..files import validate_save_dir
from ..logging_setup import get_logger, register_secret
from ..models import ACTIVE_STATUSES
from ..providers import UnsupportedMediaError, find_provider, resolve_format_selection
from .auth import make_token_verifier
from .schemas import (
    AutoDownloadRequest, BatchAutoDownloadRequest, CreateDownloadRequest, DetectRequest, OpenRequest, SettingsUpdate,
)

log = get_logger("api")

# Browser extensions run from chrome-extension:// / moz-extension:// origins,
# never from a regular http(s) origin, so a random website can't call the
# local API from a <script> tag.
_EXTENSION_ORIGIN_REGEX = r"^(chrome|moz)-extension://.+"


def create_app(manager, settings_store, on_open_request=None, on_added=None):
    app = FastAPI(title=f"{APP_NAME} Local API", version=__version__, docs_url=None, redoc_url=None,
                  openapi_url=None)
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=_EXTENSION_ORIGIN_REGEX,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Content-Type", "X-MDM-Token", "X-Enagrio-Token"],
    )
    register_secret(settings_store.get("api_token"))
    verify_token = make_token_verifier(settings_store)

    @app.post("/app/open")
    def app_open(body: OpenRequest, _=Depends(verify_token)):
        """Used by a second launch of the app (e.g. a double-clicked .torrent
        or a magnet: link) to hand its argument to the running window."""
        if on_open_request is None:
            raise HTTPException(status_code=503, detail="The app window isn't available.")
        on_open_request(body.url)
        return {"ok": True}

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/version")
    def version():
        return {"version": __version__, "app": APP_NAME}

    @app.get("/connection")
    def connection(_=Depends(verify_token)):
        return {"connected": True, "app": APP_NAME, "version": __version__}

    def _detect_media(url):
        provider = find_provider(url)
        if provider is None:
            raise HTTPException(status_code=422, detail="This link cannot be downloaded by Master Download Manager.")
        try:
            media = provider.detect(url)
        except UnsupportedMediaError as e:
            raise HTTPException(status_code=422, detail=str(e))
        return media

    @app.post("/media/detect")
    def detect(body: DetectRequest, _=Depends(verify_token)):
        return _detect_media(body.url).to_dict()

    @app.post("/formats")
    def formats(body: DetectRequest, _=Depends(verify_token)):
        return {"formats": [f.to_dict() for f in _detect_media(body.url).formats]}

    @app.post("/engine/detect")
    def engine_detect(body: DetectRequest, _=Depends(verify_token)):
        from ..router import detect_engine
        engine, reason = detect_engine(body.url, settings_store)
        if engine is None:
            raise HTTPException(status_code=422, detail=reason)
        return {"engine": engine, "reason": reason}

    @app.post("/playlist/detect")
    def playlist_detect(body: DetectRequest, _=Depends(verify_token)):
        from ..providers import youtube as media_compat  # looked up at call time (tests patch it)
        try:
            return media_compat.detect_playlist(body.url)
        except UnsupportedMediaError as e:
            raise HTTPException(status_code=422, detail=str(e))

    @app.post("/downloads")
    def create_download(body: CreateDownloadRequest, _=Depends(verify_token)):
        try:
            kind, quality_label, format_key, audio_bitrate = resolve_format_selection(
                body.url, body.format_id, **({"engine": body.engine} if body.engine else {}))
        except (ValueError, UnsupportedMediaError) as e:
            raise HTTPException(status_code=422, detail=str(e))
        save_dir = None
        if body.save_dir:
            try:
                save_dir = validate_save_dir(body.save_dir)
            except ValueError as e:
                raise HTTPException(status_code=422, detail=str(e))
        try:
            job = manager.add_job(
                url=body.url, kind=kind, quality_label=quality_label, format_key=format_key,
                is_playlist=body.is_playlist, audio_bitrate=audio_bitrate, source="extension",
                filename_override=body.filename, download_dir=save_dir, category=body.category,
                engine=body.engine, options=body.options.to_engine_options() if body.options else None,
                queue_id=body.queue_id, priority=body.priority, start_paused=body.start_paused,
            )
        except (UnsupportedMediaError, OSError) as e:
            raise HTTPException(status_code=422, detail=str(e))
        log.info("Download queued via browser extension: job %s", job.job_id)
        return job.to_dict()

    def _queue_auto(url, referer=None, title=None, queue_id=None, filename=None, save_dir=None, start_paused=False,
                    probe=True):
        from ..router import default_selection, detect_engine
        engine, reason = detect_engine(url, settings_store, probe=probe)
        if engine is None:
            raise HTTPException(status_code=422, detail=reason)
        format_id, category, options = default_selection(engine, settings_store)
        if referer and engine == "http":
            options = dict(options, headers={"Referer": referer})
        if save_dir:
            try:
                save_dir = validate_save_dir(save_dir)
            except ValueError as e:
                raise HTTPException(status_code=422, detail=str(e))
        try:
            kind, quality_label, format_key, audio_bitrate = resolve_format_selection(url, format_id, engine=engine)
            job = manager.add_job(
                url=url, kind=kind, quality_label=quality_label, format_key=format_key, is_playlist=False,
                audio_bitrate=audio_bitrate, source="extension", category=category, engine=engine, options=options,
                title=(title or None), queue_id=queue_id, filename_override=filename, download_dir=save_dir,
                start_paused=start_paused,
            )
        except (ValueError, UnsupportedMediaError, OSError) as e:
            raise HTTPException(status_code=422, detail=str(e))
        return job, engine, reason

    @app.post("/downloads/auto")
    def create_auto_download(body: AutoDownloadRequest, _=Depends(verify_token)):
        """Queue any link (media page, file, magnet, .torrent) with the user's
        default choices — used by the extension's context menu and, when the
        user turned it on, by browser download capture."""
        if body.origin == "capture" and not settings_store.get("browser_capture_enabled", False):
            raise HTTPException(status_code=409, detail="Browser download capture is turned off in MDM.")
        job, engine, reason = _queue_auto(body.url, body.referer, body.title, body.queue_id, body.filename,
                                          body.save_dir, body.start_paused)
        log.info("Link sent from browser (%s) queued as job %s (%s: %s)", body.origin, job.job_id, engine, reason)
        if on_added is not None and body.origin == "capture":
            on_added(job)
        return dict(job.to_dict(), engine_reason=reason)

    @app.post("/downloads/batch")
    def create_batch(body: BatchAutoDownloadRequest, _=Depends(verify_token)):
        """'Download selected links': queue each link with the defaults.
        Links that can't be downloaded are reported, not fatal."""
        added, failed = [], []
        for url in body.urls:
            try:
                # no per-link network probe: selections can hold hundreds of links
                job, _engine, _reason = _queue_auto(url, body.referer, queue_id=body.queue_id,
                                                    start_paused=body.start_paused, probe=False)
                added.append(job.job_id)
            except HTTPException as e:
                failed.append({"url": url, "error": e.detail})
        log.info("Batch from browser: %s queued, %s rejected", len(added), len(failed))
        return {"added": added, "failed": failed}

    @app.get("/queues")
    def list_queues(_=Depends(verify_token)):
        jobs = [j for j in manager.list_jobs() if not j.from_history]
        return [{"id": q.id, "name": q.name, "running": q.running,
                 "items": sum(1 for j in jobs if j.queue_id == q.id and j.status in ACTIVE_STATUSES)}
                for q in manager.queues.all()]

    @app.post("/queues/{queue_id}/start")
    def start_queue(queue_id: str, _=Depends(verify_token)):
        try:
            return manager.start_queue(queue_id).to_dict()
        except KeyError:
            raise HTTPException(status_code=404, detail="No such queue")

    @app.post("/queues/{queue_id}/stop")
    def stop_queue(queue_id: str, _=Depends(verify_token)):
        try:
            return manager.stop_queue(queue_id).to_dict()
        except KeyError:
            raise HTTPException(status_code=404, detail="No such queue")

    @app.get("/browser/config")
    def browser_config(_=Depends(verify_token)):
        """What the extension needs to decide which browser downloads to hand
        over. Capture stays off unless the user enabled it in MDM."""
        types = [t.strip().lower().lstrip(".") for t in str(settings_store.get("browser_capture_types", "")).split()]
        blacklist = [b.strip() for b in str(settings_store.get("browser_capture_blacklist", "")).split() if b.strip()]
        return {"capture_enabled": bool(settings_store.get("browser_capture_enabled", False)),
                "file_types": [t for t in types if t.isalnum() and len(t) <= 12][:200],
                "min_size_kb": max(0, int(settings_store.get("browser_capture_min_kb", 0) or 0)),
                "blacklist": blacklist[:200]}

    @app.get("/downloads")
    def list_downloads(status: str = None, search: str = None, _=Depends(verify_token)):
        jobs = [j for j in manager.list_jobs(status=status, search=search) if not j.from_history]
        return {"downloads": [j.to_dict() for j in reversed(jobs)]}

    @app.get("/downloads/{job_id}")
    def get_download(job_id: int, _=Depends(verify_token)):
        job = manager.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="No such download.")
        return job.to_dict()

    def _job_action(job_id, action):
        job = manager.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="No such download.")
        try:
            action(job_id)
        except ValueError as e:
            raise HTTPException(status_code=409, detail=str(e))
        return manager.get_job(job_id).to_dict()

    @app.post("/downloads/{job_id}/pause")
    def pause_download(job_id: int, _=Depends(verify_token)):
        return _job_action(job_id, manager.pause)

    @app.post("/downloads/{job_id}/resume")
    def resume_download(job_id: int, _=Depends(verify_token)):
        return _job_action(job_id, manager.resume)

    @app.post("/downloads/{job_id}/cancel")
    def cancel_download(job_id: int, _=Depends(verify_token)):
        return _job_action(job_id, manager.cancel)

    @app.post("/downloads/{job_id}/retry")
    def retry_download(job_id: int, _=Depends(verify_token)):
        return _job_action(job_id, manager.retry)

    @app.delete("/downloads/{job_id}")
    def delete_download(job_id: int, _=Depends(verify_token)):
        try:
            manager.remove_job(job_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="No such download.")
        except ValueError as e:
            raise HTTPException(status_code=409, detail=str(e))
        return {"removed": True}

    @app.get("/system/disk-space")
    def disk_space(path: str, _=Depends(verify_token)):
        # Read-only OS call on a path — never a shell command, never writes,
        # never lists directory contents. The folder may not exist yet, so
        # walk up to the nearest existing ancestor (same drive, same answer).
        probe = path
        seen = set()
        while probe and probe not in seen and not os.path.isdir(probe):
            seen.add(probe)
            probe = os.path.dirname(probe)
        try:
            usage = shutil.disk_usage(probe or path)
        except OSError:
            raise HTTPException(status_code=422, detail="Could not read free space for that path.")
        return {"free_bytes": usage.free, "total_bytes": usage.total}

    _PUBLIC_SETTINGS = ("download_dir", "video_dir", "audio_dir", "ask_where_to_save", "max_concurrent_downloads",
                        "max_retries", "download_timeout", "appearance_mode", "minimize_to_tray",
                        "start_with_windows", "notifications_enabled", "clipboard_monitor_enabled", "ffmpeg_path",
                        "default_mode", "default_quality", "default_audio_quality", "default_audio_format",
                        "default_container", "api_port")

    def _public_settings():
        # Never hand out the token, cookie configuration or proxy credentials.
        all_settings = settings_store.all()
        return {k: all_settings.get(k) for k in _PUBLIC_SETTINGS}

    @app.get("/settings")
    def get_settings(_=Depends(verify_token)):
        return _public_settings()

    @app.post("/settings")
    def update_settings(body: SettingsUpdate, _=Depends(verify_token)):
        changes = body.changed_fields()
        for key in ("download_dir", "video_dir", "audio_dir"):
            if changes.get(key):
                try:
                    changes[key] = validate_save_dir(changes[key])
                except ValueError as e:
                    raise HTTPException(status_code=422, detail=f"{key}: {e}")
        if changes:
            settings_store.update(changes)
        return _public_settings()

    return app


def run_server(manager, settings_store, host="127.0.0.1", on_open_request=None, on_added=None):
    """Runs uvicorn in a background daemon thread. Returns (thread, server)."""
    import uvicorn

    app = create_app(manager, settings_store, on_open_request=on_open_request, on_added=on_added)
    port = int(settings_store.get("api_port"))

    # log_config=None: uvicorn's default logging probes sys.stdout.isatty(),
    # which is None in a windowed (no-console) frozen build and crashes.
    config = uvicorn.Config(app, host=host, port=port, log_level="warning",
                            log_config=None, access_log=False)
    server = uvicorn.Server(config)

    def _serve():
        try:
            server.run()
        except SystemExit:
            log.error("Local API could not start on port %s (is another copy of the app running?)", port)
        except Exception:
            log.exception("Local API server stopped unexpectedly")

    thread = threading.Thread(target=_serve, name="mdm-api", daemon=True)
    thread.start()
    log.info("Local API listening on %s:%s", host, port)
    return thread, server
