"""The unified download queue shared by every engine (media, torrent, HTTP),
the desktop GUI and the local API: concurrency, pause/resume/cancel, retry
with backoff, rolling speed/ETA, disk checks, history recording and queue
persistence. Framework-agnostic — subscribers marshal events to their own
thread.

Job lifecycle:
    Pending -> Preparing -> Downloading -> Processing -> Completed
                    \\-> Paused / Cancelled / Failed / Retrying (-> Pending)
    Torrents may go Downloading -> Seeding -> Completed.
"""

import collections
import json
import os
import shutil
import threading
import time

from yt_dlp.utils import DownloadCancelled

from . import bandwidth
from .config import MIN_FREE_SPACE_BYTES
from .errors import explain_error, is_transient_error
from .files import sanitize_filename, sanitize_filename_override, unique_path  # noqa: F401 (re-exported)
from .history import HistoryStore
from .logging_setup import get_logger
from .models import (
    ACTIVE_STATUSES, RUNNING_STATUSES, STATUS_CANCELLED, STATUS_COMPLETED, STATUS_DOWNLOADING, STATUS_FAILED,
    STATUS_PAUSED, STATUS_PREPARING, STATUS_PROCESSING, STATUS_QUEUED, STATUS_RETRYING, STATUS_SEEDING,
    PRIORITY_HIGH, PRIORITY_LOW, DownloadJob, bump_job_id_counter,
)
from .providers import UnsupportedMediaError, configure as configure_providers, find_provider, get_provider
from .providers.base import PauseRequested
from .queues import MAIN_QUEUE_ID, QueueStore

log = get_logger("engine")

PROGRESS_EMIT_INTERVAL = 0.25  # seconds between progress events per job (status changes are immediate)
# A waiting download gains one priority level per AGING_SECONDS, so a steady
# stream of High-priority downloads can delay a Low one but never starve it.
AGING_SECONDS = 600


def _guess_kind_from_format(format_label):
    """Best-effort, display-only classification of an old history entry's
    free-text format string back into a kind — used only for icons/filters
    of rows seeded from history, never to decide how to download anything."""
    label = (format_label or "").lower()
    if "mp3" in label or "kbps" in label or "m4a" in label:
        return "audio"
    if "jpg" in label or "thumbnail" in label:
        return "image"
    if "subtitle" in label or "srt" in label:
        return "subtitle"
    if "torrent" in label:
        return "torrent"
    return "video"


def _legacy_engine(entry):
    """History written by earlier versions has no engine field; those versions
    only had the YouTube (media) and torrent engines."""
    url = (entry.get("url") or "").lower()
    if url.startswith("magnet:") or url.split("?", 1)[0].endswith(".torrent") or "torrent" in (entry.get("format") or "").lower():
        return "torrent"
    return "media" if url else None


def resolve_download_dir(settings_store, kind, category=None, explicit_dir=None):
    """An explicit directory always wins. Otherwise route by category/kind
    to the category folder from Settings when set, else the main folder."""
    if explicit_dir:
        return explicit_dir
    label = (category or kind or "").strip().lower()
    if label in ("audio", "music") and settings_store.get("audio_dir"):
        return settings_store.get("audio_dir")
    if label == "video" and settings_store.get("video_dir"):
        return settings_store.get("video_dir")
    if label == "torrent" and settings_store.get("torrent_dir"):
        return settings_store.get("torrent_dir")
    return settings_store.get("download_dir")


class DownloadManager:
    def __init__(self, settings_store, history_store=None, queue_file=None, queue_store=None):
        self._settings = settings_store
        self._history = history_store or HistoryStore()
        self._queue_file = queue_file
        self.queues = queue_store or QueueStore(None)
        self._queue_had_work = set()  # queue ids that processed something since they last went idle
        self._jobs = []
        self._lock = threading.RLock()
        self._active_count = 0
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._dispatcher_thread = None
        self._subscribers = []
        self._persist_lock = threading.Lock()
        configure_providers(settings_store)
        bandwidth.manager.configure(settings_store)

    # ---------------- lifecycle ---------------- #
    def start(self):
        if self._dispatcher_thread and self._dispatcher_thread.is_alive():
            return
        self._stop.clear()
        self._dispatcher_thread = threading.Thread(target=self._dispatch_loop, name="mdm-dispatcher", daemon=True)
        self._dispatcher_thread.start()
        log.info("Download engine started")

    def stop(self):
        """Stop dispatching; ask running jobs to pause so partial data is kept
        and they can resume next time (they're persisted as Paused)."""
        self._stop.set()
        self._wake.set()
        for job in self.list_jobs():
            if job.status in RUNNING_STATUSES and job.status != STATUS_PROCESSING:
                job.pause_event.set()
        self._persist(force_paused=True)

    def subscribe(self, callback):
        """callback(event: str, job: DownloadJob) — called from a background
        thread; subscribers must marshal to their own UI thread if needed."""
        self._subscribers.append(callback)

    def _emit(self, event, job):
        for cb in list(self._subscribers):
            try:
                cb(event, job)
            except Exception:
                log.exception("Subscriber callback raised for event %s", event)
        if event != "progress":
            self._persist()

    # ---------------- job CRUD ---------------- #
    def wake(self):
        """Re-evaluate what can start now (schedule changes, queue starts...)."""
        self._wake.set()

    def add_job(self, url, kind, quality_label, format_key, is_playlist=False,
                download_dir=None, audio_bitrate="192", source="gui", show_popup=False,
                filename_override=None, category=None, engine=None, options=None, title=None,
                queue_id=None, priority=0, start_at=None, start_paused=False):
        provider = None
        if engine:
            candidate = get_provider(engine)
            if candidate is not None and candidate.supports(url):
                provider = candidate
        if provider is None:
            provider = find_provider(url)
        if provider is None:
            raise UnsupportedMediaError("This link cannot be downloaded by Master Download Manager.")

        if category is None and getattr(provider, "name", "") == "torrent":
            category = "Torrent"
        download_dir = resolve_download_dir(self._settings, kind, category, download_dir)
        os.makedirs(download_dir, exist_ok=True)

        safe_filename = sanitize_filename_override(filename_override) if filename_override else None

        job = DownloadJob(
            url=url, kind=kind, quality_label=quality_label, format_key=format_key,
            is_playlist=is_playlist, download_dir=download_dir, audio_bitrate=audio_bitrate,
            source=source, show_popup=show_popup, filename_override=safe_filename, category=category,
            engine=getattr(provider, "name", None), options=options, title=title,
            queue_id=self.queues.resolve(queue_id).id, priority=_clamp_priority(priority),
        )
        job.start_at = float(start_at) if start_at else None
        if start_paused:
            job.status = STATUS_PAUSED
            job.message = "Added — press Resume (or start its queue) to download."
        job.provider = provider
        if options:
            job.playlist_title = options.get("playlist_title")
            job.playlist_index = options.get("playlist_index")
        with self._lock:
            self._jobs.append(job)
        log.info("Queued job %s [%s] (%s) from %s: %s", job.job_id, job.engine, job.quality_label, source, url)
        self._emit("added", job)
        self._wake.set()
        return job

    def get_job(self, job_id):
        with self._lock:
            return next((j for j in self._jobs if j.job_id == job_id), None)

    def seed_from_history(self, history_entries):
        """Rebuild read-mostly rows from persisted history so the GUI can show
        one list (this session's jobs + everything downloaded before).
        Seeded jobs are always terminal, so the dispatcher can never re-run them."""
        with self._lock:
            for entry in history_entries:
                status = entry.get("status", STATUS_COMPLETED)
                if status not in (STATUS_COMPLETED, STATUS_FAILED, STATUS_CANCELLED):
                    status = STATUS_COMPLETED
                job = DownloadJob(
                    url=entry.get("url", ""), kind=entry.get("kind") or _guess_kind_from_format(entry.get("format", "")),
                    quality_label=entry.get("format", ""), format_key="",
                    is_playlist=False, download_dir=entry.get("path", ""),
                    source=entry.get("source", "gui"), engine=entry.get("engine") or _legacy_engine(entry),
                )
                job.status = status
                job.title = entry.get("title") or job.url
                job.progress = 100.0 if status == STATUS_COMPLETED else 0.0
                job.total_bytes = entry.get("file_size")
                job.downloaded_bytes = entry.get("file_size") or 0
                job.result_path = entry.get("file") or None
                job.result_paths = [job.result_path] if job.result_path else []
                job.finished_at = job.created_at
                job.from_history = True
                job.history_key = (job.url, entry.get("date"), job.title)
                job.history_id = entry.get("id")
                job.history_date = entry.get("date")
                self._jobs.append(job)

    def list_jobs(self, status=None, search=None):
        with self._lock:
            jobs = list(self._jobs)
        if status:
            jobs = [j for j in jobs if j.status == status]
        if search:
            needle = search.lower()
            jobs = [j for j in jobs if needle in (j.title or "").lower() or needle in j.url.lower()]
        return jobs

    def remove_job(self, job_id):
        job = self.get_job(job_id)
        if job is None:
            raise KeyError(f"No such job: {job_id}")
        if job.status in RUNNING_STATUSES:
            raise ValueError("Cancel the download before removing it.")
        with self._lock:
            self._jobs.remove(job)
        self._emit("removed", job)

    def clear_finished(self):
        with self._lock:
            removed = [j for j in self._jobs if j.status in (STATUS_COMPLETED, STATUS_CANCELLED, STATUS_FAILED)]
            self._jobs = [j for j in self._jobs if j not in removed]
        for job in removed:
            self._emit("removed", job)
        return len(removed)

    def clear_completed(self):
        """Remove completed rows from the queue view (history keeps them)."""
        with self._lock:
            removed = [j for j in self._jobs if j.status == STATUS_COMPLETED]
            self._jobs = [j for j in self._jobs if j.status != STATUS_COMPLETED]
        for job in removed:
            self._emit("removed", job)
        return len(removed)

    # ---------------- transport controls ---------------- #
    def pause(self, job_id):
        job = self.get_job(job_id)
        if job is None:
            raise KeyError(f"No such job: {job_id}")
        if job.status == STATUS_PROCESSING:
            raise ValueError("This download is being processed (merging/converting) and can't be paused right now.")
        if job.status in RUNNING_STATUSES:
            if not job.resumable:
                raise ValueError("Resume is not supported for this download (the server doesn't allow it).")
            job.pause_event.set()
            job.message = "Pausing..."
            self._emit("progress", job)
        elif job.status in (STATUS_QUEUED, STATUS_RETRYING):
            job.status = STATUS_PAUSED
            job.message = "Paused."
            self._emit("paused", job)
        else:
            raise ValueError(f"Cannot pause a download that is {job.status.lower()}.")

    def resume(self, job_id):
        job = self.get_job(job_id)
        if job is None:
            raise KeyError(f"No such job: {job_id}")
        if job.status != STATUS_PAUSED:
            raise ValueError(f"Cannot resume a download that is {job.status.lower()}.")
        job.status = STATUS_QUEUED
        job.queued_at = time.time()
        job.message = "Waiting to resume..."
        self._emit("resumed", job)
        self._wake.set()

    def cancel(self, job_id):
        job = self.get_job(job_id)
        if job is None:
            raise KeyError(f"No such job: {job_id}")
        if job.status in RUNNING_STATUSES:
            job.cancel_event.set()
            job.message = "Cancelling..."
            self._emit("progress", job)
        elif job.status in (STATUS_QUEUED, STATUS_PAUSED, STATUS_RETRYING):
            job.status = STATUS_CANCELLED
            job.message = "Cancelled."
            job.finished_at = time.time()
            self._cleanup_partial(job)
            self._emit("finished", job)
        else:
            raise ValueError(f"Cannot cancel a download that is {job.status.lower()}.")

    def retry(self, job_id):
        job = self.get_job(job_id)
        if job is None:
            raise KeyError(f"No such job: {job_id}")
        if job.from_history:
            raise ValueError("This row comes from history — use Redownload to queue it again.")
        if job.status not in (STATUS_FAILED, STATUS_CANCELLED):
            raise ValueError(f"Cannot retry a download that is {job.status.lower()}.")
        job.status = STATUS_QUEUED
        job.queued_at = time.time()
        job.error_message = job.error_hint = job.error_details = None
        job.message = "Waiting to retry..."
        job.cancel_event.clear()
        job.pause_event.clear()
        job.retries += 1
        self._emit("resumed", job)
        self._wake.set()

    def retry_failed(self):
        count = 0
        for job in self.list_jobs(status=STATUS_FAILED):
            if job.from_history:
                continue
            try:
                self.retry(job.job_id)
                count += 1
            except (ValueError, KeyError):
                pass
        return count

    def move_up(self, job_id):
        self._move(job_id, -1)

    def move_down(self, job_id):
        self._move(job_id, 1)

    def move_to_top(self, job_id):
        self._move_to(job_id, 0)

    def move_to_bottom(self, job_id):
        self._move_to(job_id, None)

    def _move(self, job_id, direction):
        with self._lock:
            idx = next((i for i, j in enumerate(self._jobs) if j.job_id == job_id), None)
            if idx is None:
                raise KeyError(f"No such job: {job_id}")
            new_idx = idx + direction
            if 0 <= new_idx < len(self._jobs):
                self._jobs[idx], self._jobs[new_idx] = self._jobs[new_idx], self._jobs[idx]
        job = self.get_job(job_id)
        if job:
            self._emit("reordered", job)

    def move_before(self, job_ids, before_job_id=None):
        """Drag-and-drop: move these jobs (keeping their relative order) to
        just before `before_job_id`, or to the end when it's None."""
        ids = set(job_ids)
        with self._lock:
            moving = [j for j in self._jobs if j.job_id in ids]
            if not moving:
                return
            rest = [j for j in self._jobs if j.job_id not in ids]
            idx = next((i for i, j in enumerate(rest) if j.job_id == before_job_id), len(rest))
            self._jobs = rest[:idx] + moving + rest[idx:]
        self._emit("reordered", moving[0])
        self._wake.set()

    def _move_to(self, job_id, position):
        with self._lock:
            job = next((j for j in self._jobs if j.job_id == job_id), None)
            if job is None:
                raise KeyError(f"No such job: {job_id}")
            self._jobs.remove(job)
            if position is None:
                self._jobs.append(job)
            else:
                self._jobs.insert(position, job)
        self._emit("reordered", job)

    def pause_all(self):
        for job in self.list_jobs():
            if job.status in (STATUS_QUEUED, STATUS_RETRYING) or job.status in RUNNING_STATUSES:
                try:
                    self.pause(job.job_id)
                except ValueError:
                    pass

    def resume_all(self):
        for job in self.list_jobs():
            if job.status == STATUS_PAUSED:
                self.resume(job.job_id)

    def cancel_all(self):
        for job in self.list_jobs():
            if job.status in ACTIVE_STATUSES:
                try:
                    self.cancel(job.job_id)
                except ValueError:
                    pass

    # ---------------- dispatcher / worker ---------------- #
    def _dispatch_loop(self):
        while not self._stop.is_set():
            self._wake.wait(timeout=1.0)
            self._wake.clear()
            if not self._stop.is_set():
                self._maybe_start_jobs()

    def _maybe_start_jobs(self):
        max_concurrent = max(1, int(self._settings.get("max_concurrent_downloads", 3) or 1))
        while True:
            with self._lock:
                if self._active_count >= max_concurrent:
                    break
                candidate = self._next_candidate()
                if candidate is None:
                    break
                queue = self.queues.resolve(candidate.queue_id)
                candidate.queue_connections = queue.max_connections or None
                self._queue_had_work.add(queue.id)
                candidate.status = STATUS_PREPARING
                candidate.message = "Preparing..."
                candidate.started_at = candidate.started_at or time.time()
                self._active_count += 1
            threading.Thread(target=self._run_job, args=(candidate,), name=f"mdm-job-{candidate.job_id}",
                             daemon=True).start()
        self._check_queues_complete()

    def _next_candidate(self):
        """Caller holds self._lock. The Pending job that should start next:
        only from running queues with a free slot, not before its start time;
        highest effective priority (priority + aging) first, then list order."""
        now = time.time()
        queues = {q.id: q for q in self.queues.all()}
        running_per_queue = collections.Counter(
            self._queue_key(j, queues) for j in self._jobs if j.status in RUNNING_STATUSES)
        best, best_score = None, None
        for position, job in enumerate(self._jobs):
            if job.status != STATUS_QUEUED or (job.start_at and job.start_at > now):
                continue
            q = queues.get(self._queue_key(job, queues))
            if q is None or not q.running:
                continue
            if q.max_concurrent and running_per_queue[q.id] >= q.max_concurrent:
                continue
            waited = max(0.0, now - (job.queued_at or job.created_at))
            score = (_clamp_priority(job.priority) + waited / AGING_SECONDS, -position)
            if best_score is None or score > best_score:
                best, best_score = job, score
        return best

    @staticmethod
    def _queue_key(job, queues):
        return job.queue_id if job.queue_id in queues else MAIN_QUEUE_ID

    def _check_queues_complete(self):
        """Fire 'queue_complete' once when a running queue that did work has
        nothing left to do (its completion action / stop-when-empty)."""
        finished = []
        with self._lock:
            queues = {q.id: q for q in self.queues.all()}
            busy = {self._queue_key(j, queues) for j in self._jobs
                    if j.status in RUNNING_STATUSES or j.status in (STATUS_QUEUED, STATUS_RETRYING)}
            for qid in list(self._queue_had_work):
                if qid not in busy:
                    self._queue_had_work.discard(qid)
                    # A queue the user stopped didn't "finish": never run its completion action.
                    if qid in queues and queues[qid].running:
                        finished.append(queues[qid])
        for q in finished:
            log.info("Queue %s finished its downloads", q.name)
            if q.stop_when_empty and not q.is_main:
                self.queues.set_running(q.id, False)
            self._notify("queue_complete", q)

    def _notify(self, event, payload):
        for cb in list(self._subscribers):
            try:
                cb(event, payload)
            except Exception:  # noqa: BLE001
                log.exception("Subscriber callback raised for %s", event)

    # ---------------- queues ---------------- #
    def start_queue(self, queue_id):
        """Run a queue: its Paused downloads go back to Pending and start as
        slots free up (AB-style 'Start queue')."""
        q = self.queues.set_running(queue_id, True)
        for job in self.list_jobs():
            if job.queue_id == q.id and job.status == STATUS_PAUSED and not job.from_history:
                job.status = STATUS_QUEUED
                job.queued_at = time.time()
                job.message = "Waiting in queue..."
                self._emit("resumed", job)
        log.info("Queue %s started", q.name)
        self._notify("queue_changed", q)
        self._wake.set()
        return q

    def stop_queue(self, queue_id):
        """Stop a queue: nothing new starts from it and its running downloads
        are paused (kept resumable)."""
        q = self.queues.set_running(queue_id, False)
        with self._lock:
            self._queue_had_work.discard(q.id)
        for job in self.list_jobs():
            if job.queue_id == q.id and (job.status in RUNNING_STATUSES or job.status == STATUS_RETRYING):
                try:
                    self.pause(job.job_id)
                except ValueError:
                    pass
        log.info("Queue %s stopped", q.name)
        self._notify("queue_changed", q)
        return q

    def delete_queue(self, queue_id):
        q = self.queues.delete(queue_id)
        for job in self.list_jobs():
            if job.queue_id == q.id:
                job.queue_id = MAIN_QUEUE_ID
                self._emit("changed", job)
        self._notify("queue_changed", q)
        self._wake.set()
        return q

    def move_to_queue(self, job_id, queue_id):
        job = self._require(job_id)
        job.queue_id = self.queues.resolve(queue_id).id
        self._emit("changed", job)
        self._wake.set()

    def set_priority(self, job_id, priority):
        job = self._require(job_id)
        job.priority = _clamp_priority(priority)
        self._emit("changed", job)
        self._wake.set()

    def set_start_at(self, job_id, start_at):
        """Schedule one download: it stays Pending until start_at (epoch s)."""
        job = self._require(job_id)
        job.start_at = float(start_at) if start_at else None
        if job.status == STATUS_PAUSED and start_at:
            job.status = STATUS_QUEUED
            job.queued_at = time.time()
        if start_at:
            job.message = "Scheduled."
        self._emit("changed", job)
        self._wake.set()

    def set_speed_limit(self, job_id, kbps):
        """Per-download limit; applies immediately to a running direct (HTTP)
        download, otherwise from its next start."""
        job = self._require(job_id)
        kbps = max(0, int(kbps or 0))
        options = dict(job.options or {})
        if kbps:
            options["speed_limit_kbps"] = kbps
        else:
            options.pop("speed_limit_kbps", None)
        job.options = options
        limiter = getattr(job, "rate_limiter", None)
        if limiter is not None:
            limiter.set_rate(kbps * 1024)
        self._emit("changed", job)

    def restart(self, job_id):
        """Throw away partial data and download again from zero."""
        job = self._require(job_id)
        if job.from_history:
            raise ValueError("This row comes from history — use Download again to queue it.")
        if job.status in RUNNING_STATUSES:
            raise ValueError("Pause or cancel the download before restarting it.")
        self._cleanup_partial(job)
        job.progress, job.downloaded_bytes, job.total_bytes = 0.0, 0, None
        job.error_message = job.error_hint = job.error_details = None
        job.result_path, job.result_paths = None, []
        job.cancel_event.clear()
        job.pause_event.clear()
        job.status = STATUS_QUEUED
        job.queued_at = time.time()
        job.message = "Restarting..."
        self._emit("resumed", job)
        self._wake.set()

    def _require(self, job_id):
        job = self.get_job(job_id)
        if job is None:
            raise KeyError(f"No such job: {job_id}")
        return job

    def _provider_for(self, job):
        if job.provider is not None:
            return job.provider
        provider = get_provider(job.engine) if job.engine else None
        if provider is None or not provider.supports(job.url):
            provider = find_provider(job.url)
        job.provider = provider
        return provider

    def _run_job(self, job):
        provider = self._provider_for(job)
        self._emit("started", job)

        if provider is None:
            self._fail(job, UnsupportedMediaError("This link cannot be downloaded by Master Download Manager."))
            self._finish_active(job)
            return

        try:
            os.makedirs(job.download_dir, exist_ok=True)
            free_bytes = shutil.disk_usage(job.download_dir).free
        except OSError:
            free_bytes = None
        if free_bytes is not None and free_bytes < MIN_FREE_SPACE_BYTES:
            job.status = STATUS_FAILED
            job.error_message = "Disk space is insufficient."
            job.error_hint = "Free up space or choose a different download folder."
            job.message = job.error_message
            job.finished_at = time.time()
            self._record_history(job, "Failed")
            self._emit("finished", job)
            self._finish_active(job)
            return

        job.global_share_bps = bandwidth.manager.share_for_job(self.active_count())
        speed_samples = collections.deque(maxlen=8)
        last_emit = [0.0]

        def emit_progress(force=False):
            now = time.monotonic()
            if force or now - last_emit[0] >= PROGRESS_EMIT_INTERVAL:
                last_emit[0] = now
                self._emit("progress", job)

        def hook(d):
            if job.cancel_event.is_set():
                raise DownloadCancelled("Cancelled by user")
            if job.pause_event.is_set() and job.status != STATUS_SEEDING:
                raise PauseRequested("Paused by user")

            status = d.get("status")
            if status == "downloading":
                changed = job.status != STATUS_DOWNLOADING
                job.status = STATUS_DOWNLOADING
                downloaded = d.get("downloaded_bytes") or 0
                total = d.get("total_bytes") or d.get("total_bytes_estimate")
                now = time.time()
                if speed_samples and downloaded < speed_samples[-1][1]:
                    speed_samples.clear()  # a new file/fragment set started (e.g. audio after video)
                speed_samples.append((now, downloaded))
                job.downloaded_bytes = downloaded
                job.total_bytes = total
                speed_bps = d.get("speed") if d.get("speed") is not None and "peers" in d else _rolling_speed(speed_samples)
                speed_bps = float(speed_bps or 0.0)
                job.speed_bps = speed_bps
                job.speed_text = _format_speed(speed_bps)
                if "peers" in d:
                    job.peers, job.seeds = d.get("peers"), d.get("seeds")
                    job.upload_bps = d.get("upload_bps") or 0.0
                if not job.speed_history or now - job.speed_history[-1][0] >= 0.5:
                    job.speed_history.append((now, speed_bps))
                    if len(job.speed_history) > 120:
                        del job.speed_history[:-120]
                if total and speed_bps > 0:
                    job.eta_seconds = max(total - downloaded, 0) / speed_bps
                    job.eta_text = _format_eta(job.eta_seconds)
                else:
                    job.eta_seconds = None
                    job.eta_text = ""
                job.progress = min(100.0, (downloaded / total * 100)) if total else 0.0
                filename = os.path.basename(d.get("filename", "") or "")
                if filename and (not job.title or job.title == job.url):
                    job.title = filename
                info = d.get("info_dict") or {}
                if job.is_playlist and info.get("playlist_index"):
                    count = info.get("n_entries") or info.get("playlist_count")
                    job.message = f"Item {info['playlist_index']}" + (f" of {count}" if count else "")
                else:
                    job.message = f"Downloading ({job.progress:.0f}%)"
                emit_progress(force=changed)
            elif status in ("finished", "processing"):
                job.status = STATUS_PROCESSING
                if status == "finished":
                    job.progress = 100.0
                pp = d.get("postprocessor")
                job.message = f"Processing ({_pp_label(pp)})..." if pp else "Processing..."
                job.speed_bps, job.speed_text, job.eta_text, job.eta_seconds = 0.0, "", "", None
                emit_progress(force=True)
            elif status == "seeding":
                changed = job.status != STATUS_SEEDING
                job.status = STATUS_SEEDING
                job.progress = 100.0
                job.upload_bps = d.get("upload_bps") or 0.0
                job.peers, job.seeds = d.get("peers"), d.get("seeds")
                job.speed_text = f"↑ {_format_speed(job.upload_bps) or '0 B/s'}"
                job.message = f"Seeding — ratio {d.get('ratio', 0):.2f} / {d.get('target_ratio', 0):.2f}"
                emit_progress(force=changed)

        try:
            result = provider.run_download(job, hook) or {}
            if isinstance(result, dict):
                job.title = result.get("title") or job.title
                paths = [p for p in (result.get("filepaths") or []) if p]
                if paths:
                    job.result_paths = paths
                    job.result_path = paths[0]
                if result.get("playlist_title"):
                    job.playlist_title = result["playlist_title"]
            job.status = STATUS_COMPLETED
            job.progress = 100.0
            job.message = "Completed"
            job.speed_bps, job.speed_text, job.eta_text, job.eta_seconds = 0.0, "", "", None
            job.finished_at = time.time()
            job.temp_files.clear()
            size = _size_on_disk(job.result_path) or job.total_bytes
            if size:
                job.total_bytes = job.total_bytes or size
            self._record_history(job, "Completed", file_size=size)
            self._emit("finished", job)
            log.info("Job %s completed: %s -> %s", job.job_id, job.title, job.result_path)
        except PauseRequested:
            job.status = STATUS_PAUSED
            job.message = "Paused."
            job.speed_bps, job.speed_text, job.eta_text, job.eta_seconds = 0.0, "", "", None
            job.pause_event.clear()
            self._emit("paused", job)
            log.info("Job %s paused", job.job_id)
        except DownloadCancelled:
            job.status = STATUS_CANCELLED
            job.message = "Cancelled."
            job.speed_bps, job.speed_text, job.eta_text, job.eta_seconds = 0.0, "", "", None
            job.finished_at = time.time()
            job.cancel_event.clear()
            self._cleanup_partial(job)
            self._emit("finished", job)
            log.info("Job %s cancelled", job.job_id)
        except Exception as e:  # noqa: BLE001 - one bad job must never take down the engine
            self._fail(job, e)
        finally:
            self._finish_active(job)

    def _fail(self, job, exc):
        info = explain_error(exc, getattr(job, "_engine_log", None) or ())
        job.error_message = info.reason
        job.error_hint = info.suggestion
        job.error_details = info.technical
        job.message = info.reason
        job.speed_bps, job.speed_text, job.eta_text, job.eta_seconds = 0.0, "", "", None
        log.warning("Job %s failed: %s | %s", job.job_id, info.reason, info.technical)
        if self._schedule_auto_retry(job, info.reason):
            return
        job.status = STATUS_FAILED
        job.finished_at = time.time()
        self._record_history(job, "Failed")
        self._emit("finished", job)

    def _cleanup_partial(self, job):
        provider = job.provider
        if provider is None:
            return
        try:
            provider.cleanup_cancelled(job)
        except Exception:  # noqa: BLE001
            log.debug("Cleanup after cancel failed for job %s", job.job_id, exc_info=True)

    def _record_history(self, job, status, file_size=None):
        if job.from_history:
            return
        try:
            entry = self._history.add(
                job.title, job.quality_label, job.download_dir, url=job.url, status=status, source=job.source,
                file_size=file_size if file_size is not None else (job.total_bytes if status == "Completed" else None),
                engine=job.engine or "", kind=job.kind, file_path=job.result_path or "",
            )
            job.history_id = entry.get("id") if isinstance(entry, dict) else None
        except Exception:  # noqa: BLE001 - history must never break a download
            log.exception("Couldn't write history entry")

    def _finish_active(self, job):
        with self._lock:
            self._active_count = max(0, self._active_count - 1)
        self._wake.set()

    def active_count(self):
        with self._lock:
            return self._active_count

    def _schedule_auto_retry(self, job, message):
        max_retries = int(self._settings.get("max_retries", 3) or 0)
        if job.retries >= max_retries or not is_transient_error(message):
            return False
        delay = min(2 ** job.retries * 2, 30)
        job.status = STATUS_RETRYING
        job.message = f"{message} Retrying in {delay}s..."
        self._emit("progress", job)
        self._emit("retrying", job)

        def do_retry():
            if job.status == STATUS_RETRYING:  # user may have paused/cancelled/removed meanwhile
                job.retries += 1
                job.status = STATUS_QUEUED
                job.queued_at = time.time()
                job.message = "Waiting to retry..."
                self._emit("resumed", job)
                self._wake.set()

        timer = threading.Timer(delay, do_retry)
        timer.daemon = True
        timer.start()
        return True

    # ---------------- persistence ---------------- #
    PERSIST_DEBOUNCE_SECONDS = 1.0

    def _persist(self, force_paused=False):
        """Debounced: bursts of changes (e.g. queuing a 1,000-item playlist)
        are written once, not once per change. stop() writes immediately."""
        if not self._queue_file:
            return
        if force_paused:
            timer = getattr(self, "_persist_timer", None)
            if timer is not None:
                timer.cancel()
            self._persist_timer = None
            self._write_queue(force_paused=True)
            return
        with self._persist_lock:
            if getattr(self, "_persist_timer", None) is not None:
                return
            timer = threading.Timer(self.PERSIST_DEBOUNCE_SECONDS, self._persist_timer_fired)
            timer.daemon = True
            self._persist_timer = timer
            timer.start()

    def _persist_timer_fired(self):
        with self._persist_lock:
            self._persist_timer = None
        self._write_queue()

    def _write_queue(self, force_paused=False):
        with self._lock:
            jobs = [j for j in self._jobs if not j.from_history and j.status in ACTIVE_STATUSES]
            data = []
            for j in jobs:
                d = j.to_persisted()
                if force_paused or d["status"] in RUNNING_STATUSES or d["status"] == STATUS_RETRYING:
                    d["status"] = STATUS_PAUSED
                data.append(d)
        with self._persist_lock:
            try:
                os.makedirs(os.path.dirname(os.path.abspath(self._queue_file)), exist_ok=True)
                tmp = f"{self._queue_file}.tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump({"version": 1, "jobs": data}, f, indent=1)
                os.replace(tmp, self._queue_file)
            except OSError:
                log.debug("Couldn't save queue", exc_info=True)

    def restore_queue(self):
        """Bring back unfinished downloads from the last session as Paused
        (partial files are kept, so Resume continues where they stopped)."""
        if not self._queue_file or not os.path.isfile(self._queue_file):
            return 0
        try:
            with open(self._queue_file, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return 0
        restored = []
        for item in (data.get("jobs") if isinstance(data, dict) else []) or []:
            try:
                job = DownloadJob.from_persisted(item)
            except (KeyError, TypeError):
                continue
            if job.status not in (STATUS_QUEUED, STATUS_PAUSED):
                job.status = STATUS_PAUSED
            if job.status == STATUS_PAUSED:
                job.message = "Restored from last session — resume to continue."
            restored.append(job)
        with self._lock:
            known = {j.job_id for j in self._jobs}
            for job in restored:
                if job.job_id in known:
                    continue
                self._jobs.append(job)
        if restored:
            bump_job_id_counter(max(j.job_id for j in restored))
            log.info("Restored %s unfinished download(s) from the last session", len(restored))
            self._wake.set()
        return len(restored)


def _clamp_priority(value):
    try:
        return max(PRIORITY_LOW, min(PRIORITY_HIGH, int(value or 0)))
    except (TypeError, ValueError):
        return 0


def _pp_label(name):
    return {
        "Merger": "merging video and audio", "FFmpegMerger": "merging video and audio",
        "ExtractAudio": "converting audio", "FFmpegExtractAudio": "converting audio",
        "VideoRemuxer": "remuxing", "FFmpegVideoRemuxer": "remuxing",
        "Metadata": "writing metadata", "FFmpegMetadata": "writing metadata",
        "EmbedThumbnail": "embedding thumbnail", "EmbedSubtitle": "embedding subtitles",
        "FFmpegEmbedSubtitle": "embedding subtitles", "SubtitlesConvertor": "converting subtitles",
        "FFmpegSubtitlesConvertor": "converting subtitles", "MoveFiles": "finalizing",
        "MoveFilesAfterDownload": "finalizing", "VerifyChecksum": "verifying checksum",
    }.get(name, (name or "").lower())


def _size_on_disk(path):
    if not path:
        return None
    try:
        if os.path.isfile(path):
            return os.path.getsize(path)
        if os.path.isdir(path):
            total = 0
            for root, _dirs, files in os.walk(path):
                for f in files:
                    try:
                        total += os.path.getsize(os.path.join(root, f))
                    except OSError:
                        pass
            return total
    except OSError:
        return None
    return None


def _rolling_speed(samples):
    if len(samples) < 2:
        return 0.0
    (t0, b0) = samples[0]
    (t1, b1) = samples[-1]
    dt = t1 - t0
    if dt <= 0:
        return 0.0
    return max(0.0, (b1 - b0) / dt)


def _format_speed(bps):
    if not bps or bps <= 0:
        return ""
    for unit, divisor in (("GB/s", 1024 ** 3), ("MB/s", 1024 ** 2), ("KB/s", 1024)):
        if bps >= divisor:
            return f"{bps / divisor:.1f} {unit}"
    return f"{bps:.0f} B/s"


def _format_eta(seconds):
    if seconds is None or seconds < 0:
        return ""
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {seconds}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m"
