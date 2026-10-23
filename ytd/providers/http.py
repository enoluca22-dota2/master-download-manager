"""Direct HTTP/HTTPS Engine — plain file downloads (ISOs, archives,
installers, documents, direct media file links).

  * probe: one HEAD (falling back to a ranged GET) to learn the real file
    name (Content-Disposition / final URL), size, type and range support
  * download into "<name>.mdm-part" next to the destination; the finished
    file is only renamed into place after its size is verified, so a
    half-written file can never be mistaken for a complete one
  * resume: Range + If-Range (ETag / Last-Modified) so a changed file on the
    server restarts cleanly instead of producing a corrupt mix
  * large range-capable files are fetched over several connections into
    disjoint regions of one pre-sized part file; per-segment progress is
    kept in "<name>.mdm-part.json" so pause/restart resumes every segment
  * speed limit (shared token bucket), retries with backoff, proxy, timeout,
    disk-space check, pause and cancel
"""

import json
import os
import re
import shutil
import threading
import time
from urllib.parse import unquote, urlparse

import requests

from ..config import DEFAULT_SETTINGS, MIN_FREE_SPACE_BYTES
from ..errors import PauseRequested, UnsupportedMediaError
from ..files import sanitize_filename, unique_path
from ..logging_setup import get_logger
from ..models import FormatInfo, MediaInfo
from .base import MediaProvider

from yt_dlp.utils import DownloadCancelled

log = get_logger("http")

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/145.0.0.0 Safari/537.36")
CHUNK = 256 * 1024
PART_SUFFIX = ".mdm-part"
STATE_SUFFIX = ".mdm-part.json"
SEGMENT_MIN_FILE = 8 * 1024 * 1024  # below this a single connection is just as fast
SEGMENT_MIN_SIZE = 2 * 1024 * 1024
MAX_CONNECTIONS = 16
STREAM_RETRIES = 5
_HEADER_NAME_RE = re.compile(r"^[A-Za-z0-9!#$%&'*+.^_`|~-]{1,64}$")


class _Settings:
    def __init__(self, store):
        self._store = store

    def get(self, key):
        value = self._store.get(key, None) if self._store is not None else None
        return DEFAULT_SETTINGS.get(key) if value is None else value


def _session(settings_store=None, extra_headers=None):
    cfg = _Settings(settings_store)
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT, "Accept": "*/*", "Accept-Encoding": "identity"})
    for name, value in (extra_headers or {}).items():
        if _HEADER_NAME_RE.match(str(name)) and "\r" not in str(value) and "\n" not in str(value):
            s.headers[str(name)] = str(value)
    proxy = (cfg.get("proxy") or "").strip()
    if proxy:
        s.proxies = {"http": proxy, "https": proxy}
    s.max_redirects = 10
    return s, max(5, int(cfg.get("download_timeout") or 30))


_CD_EXT_RE = re.compile(r"filename\*\s*=\s*([\w!#$%&+^`{}~-]*)'[\w-]*'([^;\s]+)", re.IGNORECASE)
_CD_QUOTED_RE = re.compile(r'(?<![*\w])filename\s*=\s*"((?:[^"\\]|\\.)*)"', re.IGNORECASE)
_CD_TOKEN_RE = re.compile(r"(?<![*\w])filename\s*=\s*([^;\s\"]+)", re.IGNORECASE)


def _content_disposition_filename(value):
    """RFC 6266: filename* (RFC 5987, charset-encoded) wins over filename."""
    m = _CD_EXT_RE.search(value)
    if m:
        charset = m.group(1) or "utf-8"
        try:
            return unquote(m.group(2), encoding=charset, errors="replace")
        except LookupError:
            return unquote(m.group(2), encoding="utf-8", errors="replace")
    m = _CD_QUOTED_RE.search(value)
    if m:
        return m.group(1).replace('\\"', '"')  # keep other backslashes literal, like browsers do
    m = _CD_TOKEN_RE.search(value)
    return m.group(1) if m else None


def filename_from_headers(headers, url):
    """Real file name: Content-Disposition (RFC 6266, incl. filename*=) first,
    then the last path segment of the final URL. Always sanitized."""
    cd = headers.get("Content-Disposition") or headers.get("content-disposition")
    name = _content_disposition_filename(cd) if cd else None
    if name:
        name = name.replace("\\", "/").split("/")[-1]  # basename only — never a path
    if not name:
        path = unquote(urlparse(url).path or "")
        name = path.rstrip("/").split("/")[-1] if path else ""
    return sanitize_filename(name or "download")


def _is_attachment(headers):
    return "attachment" in (headers.get("Content-Disposition") or "").lower()


def probe_url(url, settings_store=None, headers=None):
    """One request to learn name/size/type/range support. Never reads the body."""
    session, timeout = _session(settings_store, headers)
    try:
        resp = None
        try:
            resp = session.head(url, allow_redirects=True, timeout=timeout)
            # Many servers mishandle HEAD; only trust a clean answer that
            # also states range support. Otherwise ask with a 1-byte GET.
            if resp.status_code >= 400 or resp.headers.get("Accept-Ranges", "").lower() != "bytes":
                resp.close()
                resp = None
        except requests.RequestException:
            resp = None
        ranged_ok = None
        if resp is None:
            resp = session.get(url, allow_redirects=True, timeout=timeout, stream=True, headers={"Range": "bytes=0-0"})
            ranged_ok = resp.status_code == 206
            resp.close()
        resp.raise_for_status()
        h = resp.headers
        size = None
        content_range = h.get("Content-Range", "")
        if content_range and "/" in content_range and content_range.rsplit("/", 1)[1].isdigit():
            size = int(content_range.rsplit("/", 1)[1])
        elif h.get("Content-Length", "").isdigit() and not ranged_ok:
            size = int(h["Content-Length"])  # HEAD, or a GET whose Range was ignored (full length)
        accepts = (h.get("Accept-Ranges", "").lower() == "bytes" and ranged_ok is None) or bool(ranged_ok)
        return {
            "final_url": resp.url,
            "filename": filename_from_headers(h, resp.url),
            "size": size,
            "content_type": (h.get("Content-Type") or "").split(";")[0].strip().lower(),
            "accept_ranges": accepts,
            "etag": h.get("ETag"),
            "last_modified": h.get("Last-Modified"),
            "attachment": _is_attachment(h),
        }
    finally:
        session.close()


class TokenBucket:
    """Thread-safe byte-rate limiter shared by all connections of one job."""

    def __init__(self, rate_bps):
        self.rate = float(rate_bps)
        self._allowance = self.rate
        self._last = time.monotonic()
        self._lock = threading.Lock()

    def consume(self, n, stop_event=None):
        """Take n bytes of allowance; sleeps off any resulting debt. Debt
        carries over, so concurrent callers queue up behind each other and
        the long-run average never exceeds the configured rate."""
        if self.rate <= 0:
            return
        with self._lock:
            now = time.monotonic()
            self._allowance = min(self.rate, self._allowance + (now - self._last) * self.rate)
            self._last = now
            self._allowance -= n
            deficit = -self._allowance
        if deficit > 0:
            wait = deficit / self.rate
            if stop_event is not None:
                stop_event.wait(wait)
            else:
                time.sleep(wait)


class _Stop(Exception):
    pass


class HttpEngine(MediaProvider):
    name = "http"
    display_name = "HTTP"

    def __init__(self, settings=None):
        self.settings = settings

    def supports(self, url):
        return bool(re.match(r"^https?://", (url or "").strip(), re.IGNORECASE))

    def resolve_format_selection(self, format_id):
        if format_id in ("file", "http-file"):
            return "file", "Direct file", "file", None
        raise ValueError(f"Unknown format_id: {format_id}")

    def detect(self, url):
        if not self.supports(url):
            raise UnsupportedMediaError("Direct downloads need an http:// or https:// link.")
        try:
            info = probe_url(url, self.settings)
        except requests.HTTPError as e:
            code = e.response.status_code if e.response is not None else "?"
            raise UnsupportedMediaError(f"The server answered HTTP {code} for that link.") from e
        except requests.RequestException as e:
            raise UnsupportedMediaError(f"Couldn't reach the server: {e.__class__.__name__}.") from e
        name = info["filename"]
        ext = os.path.splitext(name)[1].lstrip(".").lower() or None
        fmt = FormatInfo(format_id="file", kind="file", label=name, ext=ext, filesize_bytes=info["size"])
        return MediaInfo(
            title=name, source_url=url, source_site=urlparse(info["final_url"]).hostname or "Web",
            formats=[fmt], engine=self.name, resumable=info["accept_ranges"] and bool(info["size"]),
            filename=name, extra={"content_type": info["content_type"], "final_url": info["final_url"]},
        )

    # ------------------------------------------------------------ download --- #
    def run_download(self, job, progress_hook):
        cfg = _Settings(self.settings)
        options = job.options or {}
        headers = options.get("headers") or {}
        info = probe_url(job.url, self.settings, headers)
        size = info["size"]
        job.resumable = bool(info["accept_ranges"] and size)

        target = options.get("http_target")
        if not target or os.path.dirname(target) != os.path.normpath(job.download_dir):
            name = info["filename"]
            if job.filename_override:
                ext = os.path.splitext(name)[1]
                override = sanitize_filename(job.filename_override)
                name = override if os.path.splitext(override)[1] else override + ext
            os.makedirs(job.download_dir, exist_ok=True)
            target = unique_path(job.download_dir, name)
            options["http_target"] = target
            job.options = options
        part, state_path = target + PART_SUFFIX, target + STATE_SUFFIX
        job.temp_files.update({part, state_path})
        if job.title in (None, "", job.url):
            job.title = os.path.basename(target)

        state = self._load_state(state_path)
        if state and (state.get("url") != job.url or state.get("size") != size
                      or (info["etag"] and state.get("etag") and state["etag"] != info["etag"])):
            log.info("Job %s: server file changed since last attempt; restarting from zero", job.job_id)
            state = None
            self._remove(part)
        if not state and os.path.exists(part) and not (job.resumable and size):
            self._remove(part)

        already = self._bytes_done(state, part)
        self._check_disk(job.download_dir, size, already)

        rate = int(options.get("speed_limit_kbps") or cfg.get("speed_limit_kbps") or 0) * 1024
        bucket = TokenBucket(rate)
        connections = max(1, min(MAX_CONNECTIONS, int(cfg.get("http_connections") or 1)))
        use_segments = job.resumable and size and size >= SEGMENT_MIN_FILE and connections > 1

        if use_segments:
            self._download_segmented(job, info, part, state_path, state, size, connections, bucket, progress_hook,
                                     headers)
        else:
            self._download_single(job, info, part, state_path, size, bucket, progress_hook, headers)

        final_size = os.path.getsize(part)
        if size is not None and final_size != size:
            raise OSError(f"Downloaded size {final_size} doesn't match the expected {size} bytes; "
                          "the file was kept as a partial download and will resume on retry.")
        progress_hook({"status": "finished", "filename": target})
        if os.path.exists(target):  # something appeared at the destination meanwhile
            target = unique_path(os.path.dirname(target), os.path.basename(target))
        os.replace(part, target)
        self._remove(state_path)
        job.temp_files.difference_update({part, state_path})
        return {"title": os.path.basename(target), "filepaths": [target]}

    # -- single connection -- #
    def _download_single(self, job, info, part, state_path, size, bucket, progress_hook, headers):
        attempt = 0
        while True:
            done = os.path.getsize(part) if os.path.exists(part) else 0
            if size is not None and done >= size and size > 0:
                return
            session, timeout = _session(self.settings, headers)
            req_headers = {}
            if done and job.resumable:
                req_headers["Range"] = f"bytes={done}-"
                validator = info.get("etag") or info.get("last_modified")
                if validator:
                    req_headers["If-Range"] = validator
            try:
                with session.get(job.url, headers=req_headers, stream=True, timeout=timeout) as resp:
                    resp.raise_for_status()
                    if done and resp.status_code != 206:
                        done = 0  # server ignored the range (or the file changed): start over
                    self._save_state(state_path, job, info, size, None)
                    mode = "ab" if done else "wb"
                    started = time.monotonic()
                    with open(part, mode) as f:
                        for chunk in resp.iter_content(CHUNK):
                            if not chunk:
                                continue
                            self._check_control(job)
                            bucket.consume(len(chunk), job.cancel_event)
                            f.write(chunk)
                            done += len(chunk)
                            progress_hook({"status": "downloading", "downloaded_bytes": done, "total_bytes": size,
                                           "filename": part, "elapsed": time.monotonic() - started})
                if size is None or done >= size:
                    return
                raise requests.ConnectionError("Connection closed before the whole file was received")
            except (requests.ConnectionError, requests.Timeout, requests.exceptions.ChunkedEncodingError) as e:
                attempt += 1
                if attempt > STREAM_RETRIES or not job.resumable:
                    raise
                delay = min(2 ** attempt, 20)
                log.info("Job %s: network error (%s); resuming in %ss (attempt %s/%s)", job.job_id,
                         e.__class__.__name__, delay, attempt, STREAM_RETRIES)
                if job.cancel_event.wait(delay):
                    self._check_control(job)
                self._check_control(job)
            finally:
                session.close()

    # -- segmented -- #
    def _download_segmented(self, job, info, part, state_path, state, size, connections, bucket, progress_hook,
                            headers):
        if state and state.get("segments"):
            segments = [list(s) for s in state["segments"]]
        else:
            seg_size = max(SEGMENT_MIN_SIZE, size // (connections * 4))
            segments = [[start, min(start + seg_size, size) - 1, 0] for start in range(0, size, seg_size)]
            with open(part, "wb") as f:
                f.truncate(size)  # pre-size once; segments write into disjoint regions
        if not os.path.exists(part):
            with open(part, "wb") as f:
                f.truncate(size)
            for s in segments:
                s[2] = 0

        lock = threading.Lock()
        stop = threading.Event()
        errors = []
        pending = [i for i, s in enumerate(segments) if s[0] + s[2] <= s[1]]
        validator = info.get("etag") or info.get("last_modified")

        def save_state():
            with lock:
                snapshot = [list(s) for s in segments]
            self._save_state(state_path, job, info, size, snapshot)

        def worker():
            session, timeout = _session(self.settings, headers)
            try:
                with open(part, "r+b") as f:
                    while not stop.is_set():
                        with lock:
                            if not pending:
                                return
                            idx = pending.pop(0)
                        seg = segments[idx]
                        attempt = 0
                        while seg[0] + seg[2] <= seg[1] and not stop.is_set():
                            start = seg[0] + seg[2]
                            req_headers = {"Range": f"bytes={start}-{seg[1]}"}
                            if validator:
                                req_headers["If-Range"] = validator
                            try:
                                with session.get(job.url, headers=req_headers, stream=True, timeout=timeout) as resp:
                                    resp.raise_for_status()
                                    if resp.status_code != 206:
                                        raise _Stop("The server stopped honouring byte ranges.")
                                    f.seek(start)
                                    for chunk in resp.iter_content(CHUNK):
                                        if stop.is_set():
                                            return
                                        if not chunk:
                                            continue
                                        remaining = seg[1] - (seg[0] + seg[2]) + 1
                                        chunk = chunk[:remaining]
                                        bucket.consume(len(chunk), stop)
                                        f.write(chunk)
                                        with lock:
                                            seg[2] += len(chunk)
                                        if seg[0] + seg[2] > seg[1]:
                                            break
                            except (requests.ConnectionError, requests.Timeout,
                                    requests.exceptions.ChunkedEncodingError) as e:
                                attempt += 1
                                if attempt > STREAM_RETRIES:
                                    errors.append(e)
                                    stop.set()
                                    return
                                if stop.wait(min(2 ** attempt, 20)):
                                    return
            except _Stop as e:
                errors.append(e)
                stop.set()
            except Exception as e:  # noqa: BLE001 - surfaced to the queue below
                errors.append(e)
                stop.set()
            finally:
                session.close()

        threads = [threading.Thread(target=worker, daemon=True) for _ in range(min(connections, max(1, len(pending))))]
        started = time.monotonic()
        for t in threads:
            t.start()
        last_save = time.monotonic()
        try:
            while any(t.is_alive() for t in threads):
                time.sleep(0.25)
                if job.cancel_event.is_set() or job.pause_event.is_set():
                    stop.set()
                    break
                with lock:
                    done = sum(s[2] for s in segments)
                progress_hook({"status": "downloading", "downloaded_bytes": done, "total_bytes": size,
                               "filename": part, "elapsed": time.monotonic() - started})
                if time.monotonic() - last_save > 2:
                    save_state()
                    last_save = time.monotonic()
        finally:
            stop.set()
            for t in threads:
                t.join(timeout=15)
            save_state()
        self._check_control(job)
        if errors:
            raise errors[0]
        with open(part, "r+b") as f:
            f.flush()
            os.fsync(f.fileno())
        if any(s[0] + s[2] <= s[1] for s in segments):
            raise requests.ConnectionError("Some parts of the file are still missing")

    # -- helpers -- #
    @staticmethod
    def _check_control(job):
        if job.cancel_event.is_set():
            raise DownloadCancelled("Cancelled by user")
        if job.pause_event.is_set():
            raise PauseRequested("Paused by user")

    @staticmethod
    def _check_disk(directory, size, already):
        try:
            free = shutil.disk_usage(directory).free
        except OSError:
            return
        needed = (size - already) if size else 0
        if free - needed < MIN_FREE_SPACE_BYTES:
            err = OSError(f"Not enough disk space: {needed} bytes needed, {free} free")
            err.errno = 28
            raise err

    @staticmethod
    def _load_state(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else None
        except (OSError, ValueError):
            return None

    @staticmethod
    def _save_state(path, job, info, size, segments):
        data = {"url": job.url, "size": size, "etag": info.get("etag"), "last_modified": info.get("last_modified"),
                "segments": segments}
        try:
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f)
            os.replace(tmp, path)
        except OSError:
            pass

    @staticmethod
    def _bytes_done(state, part):
        if state and state.get("segments"):
            return sum(s[2] for s in state["segments"])
        return os.path.getsize(part) if os.path.exists(part) else 0

    @staticmethod
    def _remove(path):
        try:
            os.remove(path)
        except OSError:
            pass

    def cleanup_cancelled(self, job):
        for path in list(job.temp_files):
            if path.endswith((PART_SUFFIX, STATE_SUFFIX)):
                self._remove(path)
        job.temp_files.clear()
        options = dict(job.options or {})
        options.pop("http_target", None)
        job.options = options
