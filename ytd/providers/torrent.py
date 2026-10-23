"""Torrent Engine — real BitTorrent downloads via libtorrent (magnet links,
local .torrent files and .torrent URLs). Every size/name shown to the user
comes straight from the torrent's own metadata — nothing is invented.

Follows the same job/progress-hook contract as the other engines, so the
queue, pause/resume, retry and local API work with torrents unchanged.

All torrents share one libtorrent session (one DHT bootstrap, one global
connection limit, one listening port). Fetched metadata is cached by
info-hash so a magnet that was already inspected starts immediately.
"""

import hashlib
import os
import re
import threading
import time
from urllib.parse import unquote, urlparse

try:
    import libtorrent as lt
except ImportError:
    lt = None

from yt_dlp.utils import DownloadCancelled

from .. import bandwidth
from ..config import CACHE_DIR, DEFAULT_SETTINGS
from ..logging_setup import get_logger
from ..models import FormatInfo, MediaInfo
from .base import MediaProvider, PauseRequested, UnsupportedMediaError

log = get_logger("torrent")

_MAGNET_RE = re.compile(r"^magnet:\?", re.IGNORECASE)
METADATA_TIMEOUT_SECONDS = 60
PROGRESS_POLL_INTERVAL_SECONDS = 1.0
MAX_TORRENT_FILE_BYTES = 20 * 1024 * 1024
TORRENT_CACHE_DIR = os.path.join(CACHE_DIR, "torrents")


def is_magnet_uri(url):
    return bool(_MAGNET_RE.match((url or "").strip()))


def _path_part(url):
    url = (url or "").strip()
    if re.match(r"^https?://", url, re.IGNORECASE):
        return unquote(urlparse(url).path or "")
    return url


def is_torrent_source(url):
    url = (url or "").strip()
    return is_magnet_uri(url) or _path_part(url).lower().endswith(".torrent")


def libtorrent_available():
    return lt is not None


def _media_info_from_file_list(name, files, source_url, total_size):
    """Pure and testable: turns a (name, [(path, size), ...]) pair — exactly
    what a real torrent_info reports — into a MediaInfo. Items may also be
    (index, path, size) when some files (e.g. padding) are hidden, so the
    format ids keep the torrent's real file indices."""
    files = [f if len(f) == 3 else (i, f[0], f[1]) for i, f in enumerate(files)]
    formats = [FormatInfo(
        format_id="torrent-all",
        kind="torrent",
        label=f"Entire torrent ({len(files)} file{'s' if len(files) != 1 else ''})",
        ext="",
        filesize_bytes=total_size,
    )]
    for idx, path, size in files:
        formats.append(FormatInfo(
            format_id=f"torrent-file-{idx}",
            kind="torrent",
            label=os.path.basename(path) or path,
            ext=os.path.splitext(path)[1].lstrip(".").lower() or None,
            filesize_bytes=size,
            note=path,
        ))
    return MediaInfo(
        title=name or "Torrent",
        source_url=source_url,
        source_site="Torrent",
        formats=formats,
        engine="torrent",
        filename=name,
    )


def _real_files(info):
    """(index, path, size) for every real file — v2/hybrid torrents contain
    hidden alignment 'pad files' that must never be shown or counted."""
    storage = info.files()
    pad_flag = getattr(lt.file_storage, "flag_pad_file", 0) if lt is not None else 0
    out = []
    for i in range(storage.num_files()):
        if pad_flag and storage.file_flags(i) & pad_flag:
            continue
        out.append((i, storage.file_path(i), storage.file_size(i)))
    return out


def parse_file_selection(format_key):
    """'torrent-all' -> None (everything); 'torrent-file-3' -> [3];
    'torrent-files-0,2,5' -> [0, 2, 5]."""
    if not format_key or format_key == "torrent-all":
        return None
    if format_key.startswith("torrent-file-"):
        tail = format_key.rsplit("-", 1)[-1]
        return [int(tail)] if tail.isdigit() else None
    if format_key.startswith("torrent-files-"):
        items = format_key[len("torrent-files-"):].split(",")
        picked = sorted({int(i) for i in items if i.strip().isdigit()})
        return picked or None
    return None


def resolve_format_selection(format_id):
    if format_id == "torrent-all":
        return "torrent", "Entire torrent", format_id, None
    if format_id.startswith("torrent-file-") and format_id.rsplit("-", 1)[-1].isdigit():
        idx = format_id.rsplit("-", 1)[-1]
        return "torrent", f"File #{idx}", format_id, None
    if format_id.startswith("torrent-files-"):
        picked = parse_file_selection(format_id)
        if picked:
            return "torrent", f"{len(picked)} selected file{'s' if len(picked) != 1 else ''}", format_id, None
    raise ValueError(f"Unknown format_id: {format_id}")


class _SharedSession:
    """One libtorrent session for the whole app (thread-safe API)."""

    def __init__(self):
        self._lock = threading.Lock()
        self._session = None

    def get(self, settings_store=None):
        with self._lock:
            if self._session is None:
                port = int(_cfg(settings_store, "torrent_listen_port") or 0)
                self._session = lt.session({
                    "listen_interfaces": f"0.0.0.0:{port},[::]:{port}",
                    "enable_dht": True,
                    "enable_lsd": True,
                    "enable_upnp": False,
                    "enable_natpmp": False,
                    "alert_mask": 0,
                })
                log.info("Torrent session started (listening on port %s)", self._session.listen_port())
            self._apply(settings_store)
            return self._session

    def _apply(self, settings_store):
        def kbps(key):
            return max(0, int(_cfg(settings_store, key) or 0)) * 1024
        download = kbps("torrent_download_limit_kbps")
        global_kbps, _ = bandwidth.manager.effective_kbps()  # one session = one consumer of the global cap
        if global_kbps:
            download = min(download or global_kbps * 1024, global_kbps * 1024)
        self._session.apply_settings({
            "connections_limit": max(10, int(_cfg(settings_store, "torrent_max_connections") or 200)),
            "download_rate_limit": download,
            "upload_rate_limit": kbps("torrent_upload_limit_kbps"),
        })


_shared = _SharedSession()


def _cfg(settings_store, key):
    if settings_store is not None:
        value = settings_store.get(key, None)
        if value is not None:
            return value
    return DEFAULT_SETTINGS.get(key)


def _info_hash_hex(ti):
    hashes = ti.info_hashes() if hasattr(ti, "info_hashes") else None
    if hashes is not None:
        return str(hashes.v1) if hashes.has_v1() else str(hashes.v2)
    return str(ti.info_hash())


def _magnet_info_hash(url):
    m = re.search(r"xt=urn:btih:([0-9a-zA-Z]+)", url or "")
    return m.group(1).lower() if m else None


def _cache_torrent_info(ti):
    try:
        os.makedirs(TORRENT_CACHE_DIR, exist_ok=True)
        data = lt.bencode(lt.create_torrent(ti).generate())
        with open(os.path.join(TORRENT_CACHE_DIR, f"{_info_hash_hex(ti).lower()}.torrent"), "wb") as f:
            f.write(data)
    except Exception:  # noqa: BLE001 - caching is an optimisation only
        log.debug("Could not cache torrent metadata", exc_info=True)


def _cached_torrent_for_magnet(url):
    ih = _magnet_info_hash(url)
    if not ih:
        return None
    path = os.path.join(TORRENT_CACHE_DIR, f"{ih}.torrent")
    if os.path.isfile(path):
        try:
            return lt.torrent_info(path)
        except RuntimeError:
            return None
    return None


class TorrentProvider(MediaProvider):
    name = "torrent"
    display_name = "Torrent"

    def __init__(self, settings=None):
        self.settings = settings

    def supports(self, url):
        return is_torrent_source(url)

    def resolve_format_selection(self, format_id):
        return resolve_format_selection(format_id)

    def detect(self, url):
        if lt is None:
            raise UnsupportedMediaError(
                "Torrent support requires the 'libtorrent' package, which is missing from this installation."
            )
        if not is_torrent_source(url):
            raise UnsupportedMediaError("That doesn't look like a magnet link or .torrent file.")

        info = self._fetch_torrent_info(url)
        files = _real_files(info)
        media = _media_info_from_file_list(info.name(), files, url, sum(size for _, _, size in files))
        media.extra = {"info_hash": _info_hash_hex(info), "pieces": info.num_pieces(),
                       "piece_size": info.piece_length(), "comment": info.comment() or None,
                       "private": bool(info.priv())}
        return media

    # -- metadata -- #
    def _load_torrent_file(self, path):
        try:
            if os.path.getsize(path) > MAX_TORRENT_FILE_BYTES:
                raise UnsupportedMediaError("That .torrent file is unreasonably large; refusing to open it.")
            return lt.torrent_info(path)
        except RuntimeError as e:
            raise UnsupportedMediaError(f"That isn't a valid .torrent file ({e}).") from e

    def _download_torrent_url(self, url):
        """A .torrent offered over HTTP(S): fetch it (size-capped) into the
        cache, then treat it like a local file."""
        import requests
        from .http import USER_AGENT
        os.makedirs(TORRENT_CACHE_DIR, exist_ok=True)
        path = os.path.join(TORRENT_CACHE_DIR, "url-" + hashlib.sha256(url.encode()).hexdigest()[:24] + ".torrent")
        if os.path.isfile(path):
            return path
        try:
            with requests.get(url, stream=True, timeout=30, headers={"User-Agent": USER_AGENT}) as resp:
                resp.raise_for_status()
                data = bytearray()
                for chunk in resp.iter_content(64 * 1024):
                    data.extend(chunk)
                    if len(data) > MAX_TORRENT_FILE_BYTES:
                        raise UnsupportedMediaError("That .torrent download is unreasonably large; refusing it.")
        except requests.RequestException as e:
            raise UnsupportedMediaError(f"Couldn't download the .torrent file: {e.__class__.__name__}.") from e
        try:
            lt.torrent_info(bytes(data))  # validate before caching
        except RuntimeError as e:
            raise UnsupportedMediaError("The server didn't return a valid .torrent file.") from e
        with open(path, "wb") as f:
            f.write(data)
        return path

    def _local_or_remote_torrent_path(self, url):
        if re.match(r"^https?://", url, re.IGNORECASE):
            return self._download_torrent_url(url)
        if not os.path.isfile(url):
            raise UnsupportedMediaError(f"Torrent file not found: {url}")
        return url

    def _fetch_torrent_info(self, url):
        if not is_magnet_uri(url):
            ti = self._load_torrent_file(self._local_or_remote_torrent_path(url))
            _cache_torrent_info(ti)
            return ti

        cached = _cached_torrent_for_magnet(url)
        if cached is not None:
            return cached
        ses = _shared.get(self.settings)
        params = lt.parse_magnet_uri(url)
        params.save_path = os.path.join(TORRENT_CACHE_DIR, "metadata-only")  # nothing is written in upload mode
        params.flags |= lt.torrent_flags.upload_mode
        # Not auto-managed (the queue must not pause it) — which also means
        # nothing would ever lift the default "paused" flag, so clear it too.
        params.flags &= ~(lt.torrent_flags.auto_managed | lt.torrent_flags.paused)
        handle = self._add_or_find(ses, params)
        try:
            deadline = time.time() + METADATA_TIMEOUT_SECONDS
            while time.time() < deadline:
                status = handle.status()
                if status.has_metadata:
                    ti = handle.torrent_file()
                    _cache_torrent_info(ti)
                    return ti
                time.sleep(0.5)
            raise UnsupportedMediaError(
                "Could not fetch torrent metadata in time — no peers or trackers responded. "
                "Check the magnet link, or that this network allows BitTorrent (DHT/peer) traffic."
            )
        finally:
            try:
                ses.remove_torrent(handle)
            except Exception:  # noqa: BLE001
                pass

    @staticmethod
    def _add_or_find(ses, params, wait_seconds=8.0):
        # remove_torrent() is asynchronous: right after detection removed the
        # metadata-only copy, re-adding the same info-hash can briefly report
        # "duplicate". Only a duplicate that persists is a real conflict.
        deadline = time.time() + wait_seconds
        while True:
            try:
                return ses.add_torrent(params)
            except RuntimeError as e:
                duplicate = "duplicate" in str(e).lower() or "already" in str(e).lower()
                if duplicate and time.time() < deadline:
                    time.sleep(0.25)
                    continue
                if duplicate:
                    raise UnsupportedMediaError("This torrent is already active in another download.") from e
                raise UnsupportedMediaError(f"libtorrent couldn't add this torrent: {e}") from e

    # -- download -- #
    def run_download(self, job, progress_hook):
        if lt is None:
            raise UnsupportedMediaError("Torrent support requires the 'libtorrent' package.")

        os.makedirs(job.download_dir, exist_ok=True)
        ses = _shared.get(self.settings)
        if is_magnet_uri(job.url):
            ti = _cached_torrent_for_magnet(job.url)
            if ti is not None:
                params = lt.add_torrent_params()
                params.ti = ti
                # keep the magnet's trackers/peer hints too
                magnet = lt.parse_magnet_uri(job.url)
                params.trackers = list(magnet.trackers)
                params.peers = list(magnet.peers)
            else:
                params = lt.parse_magnet_uri(job.url)
        else:
            ti = self._load_torrent_file(self._local_or_remote_torrent_path(job.url))
            params = lt.add_torrent_params()
            params.ti = ti
        params.save_path = job.download_dir
        # File selection must be in place before any piece is requested,
        # otherwise deselected files get downloaded the moment metadata
        # arrives. With metadata known up front (a .torrent, or a magnet
        # inspected via Detect) set priorities in the add params; otherwise
        # hold the torrent in upload-only mode until metadata arrives.
        picked = parse_file_selection(job.format_key)
        hold_for_selection = False
        if picked:
            known = params.ti if params.ti is not None and params.ti.is_valid() else None
            if known is not None:
                params.file_priorities = [4 if i in picked else 0 for i in range(known.num_files())]
            else:
                params.flags |= lt.torrent_flags.upload_mode
                hold_for_selection = True
        handle = self._add_or_find(ses, params)

        try:
            self._wait_for_metadata(handle, job)
            ti = handle.torrent_file()
            _cache_torrent_info(ti)
            self._select_files(handle, job.format_key)
            if hold_for_selection:
                handle.unset_flags(lt.torrent_flags.upload_mode)
            name = handle.status().name or job.title
            if job.title in (None, "", job.url):
                job.title = name
            result_path = os.path.join(job.download_dir, name)

            while True:
                if job.cancel_event.is_set():
                    raise DownloadCancelled("Cancelled by user")
                if job.pause_event.is_set():
                    raise PauseRequested("Paused by user")
                status = handle.status()
                if status.errc and status.errc.value():
                    raise OSError(f"Torrent error: {status.errc.message()}")
                total = status.total_wanted
                downloaded = status.total_wanted_done
                progress_hook({
                    "status": "downloading",
                    "downloaded_bytes": downloaded,
                    "total_bytes": total if total else None,
                    "filename": name,
                    "speed": status.download_rate,
                    "peers": status.num_peers,
                    "seeds": status.num_seeds,
                    "upload_bps": status.upload_rate,
                    "torrent_state": str(status.state),
                })
                if status.is_seeding or (total and downloaded >= total):
                    break
                time.sleep(PROGRESS_POLL_INTERVAL_SECONDS)

            progress_hook({"status": "finished", "filename": result_path})
            self._seed_until_ratio(handle, job, progress_hook)
            return {"title": name, "filepaths": [result_path]}
        finally:
            try:
                # keep partial/complete data on disk; only drop the session's handle
                ses.remove_torrent(handle)
            except Exception:  # noqa: BLE001
                pass

    def _seed_until_ratio(self, handle, job, progress_hook):
        """Optional seeding (Settings → Torrent → seed ratio). Pause or cancel
        while seeding simply stops seeding; the download stays completed."""
        target = float(_cfg(self.settings, "torrent_seed_ratio") or 0)
        if target <= 0:
            return
        while not (job.cancel_event.is_set() or job.pause_event.is_set()):
            status = handle.status()
            wanted = status.total_wanted or 1
            ratio = status.all_time_upload / wanted
            progress_hook({"status": "seeding", "ratio": ratio, "target_ratio": target,
                           "upload_bps": status.upload_rate, "peers": status.num_peers, "seeds": status.num_seeds})
            if ratio >= target:
                return
            time.sleep(PROGRESS_POLL_INTERVAL_SECONDS)
        job.cancel_event.clear()
        job.pause_event.clear()

    def _wait_for_metadata(self, handle, job):
        deadline = time.time() + METADATA_TIMEOUT_SECONDS
        while not handle.status().has_metadata:
            if job.cancel_event.is_set():
                raise DownloadCancelled("Cancelled by user")
            if job.pause_event.is_set():
                raise PauseRequested("Paused by user")
            if time.time() > deadline:
                raise UnsupportedMediaError(
                    "Could not fetch torrent metadata in time — no peers or trackers responded."
                )
            time.sleep(0.5)

    @staticmethod
    def _select_files(handle, format_key):
        """Download everything by default, or only the files the user picked
        from detect()'s file list."""
        picked = parse_file_selection(format_key)
        if not picked:
            return
        info = handle.torrent_file()
        if info is None:
            return
        priorities = [0] * info.num_files()
        for idx in picked:
            if 0 <= idx < len(priorities):
                priorities[idx] = 4  # normal priority; everything else stays at 0 (skip)
        handle.prioritize_files(priorities)
