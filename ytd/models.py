"""Plain data models shared by the engines, queue, GUI, and local API.

Intentionally free of any GUI toolkit or web framework dependency so they
are safe to read/write from background threads and serialize to JSON.
"""

import dataclasses
import itertools
import threading
import time

_job_id_counter = itertools.count(1)
_counter_lock = threading.Lock()


def next_job_id():
    with _counter_lock:
        return next(_job_id_counter)


def bump_job_id_counter(minimum):
    """After restoring a persisted queue, make sure new IDs never collide."""
    global _job_id_counter
    with _counter_lock:
        current = next(_job_id_counter)
        _job_id_counter = itertools.count(max(current, int(minimum) + 1))


# Status values, in the order jobs typically move through them.
STATUS_QUEUED = "Pending"
STATUS_PENDING = STATUS_QUEUED
STATUS_PREPARING = "Preparing"
STATUS_DOWNLOADING = "Downloading"
STATUS_PROCESSING = "Processing"
STATUS_SEEDING = "Seeding"
STATUS_PAUSED = "Paused"
STATUS_RETRYING = "Retrying"
STATUS_COMPLETED = "Completed"
STATUS_FAILED = "Failed"
STATUS_CANCELLED = "Cancelled"

# Jobs that currently occupy a worker thread.
RUNNING_STATUSES = (STATUS_PREPARING, STATUS_DOWNLOADING, STATUS_PROCESSING, STATUS_SEEDING)
ACTIVE_STATUSES = (STATUS_QUEUED, STATUS_PAUSED, STATUS_RETRYING) + RUNNING_STATUSES
TERMINAL_STATUSES = (STATUS_COMPLETED, STATUS_FAILED, STATUS_CANCELLED)

ENGINE_LABELS = {"media": "Media", "torrent": "Torrent", "http": "HTTP", "youtube": "Media", "fake": "Test"}


@dataclasses.dataclass
class FormatInfo:
    """A single downloadable rendition, describing only what the source
    actually reported. Never populate a field with an invented value —
    leave it None when the engine didn't supply it."""

    format_id: str
    kind: str  # "video" | "audio" | "image" | "subtitle" | "torrent" | "file"
    label: str  # e.g. "1080p (Full HD)" or "MP3 — 192 kbps"
    ext: str  # container, e.g. "mp4", "mp3", "m4a", "jpg", "srt"
    resolution: str = None  # "1920x1080" when known
    height: int = None
    fps: float = None
    vcodec: str = None
    acodec: str = None
    abr_kbps: float = None  # audio bitrate
    bitrate_kbps: float = None  # overall/video bitrate (yt-dlp's tbr), when known
    quality_tier: str = None  # "Low" | "Medium" | "High" — derived from height, never invented
    filesize_bytes: int = None
    filesize_is_estimate: bool = False
    lang_code: str = None  # for subtitle formats
    lang_name: str = None  # for subtitle formats
    is_auto: bool = False  # subtitle: automatically generated captions
    note: str = None  # free-text detail from the source (e.g. yt-dlp format_note)

    def to_dict(self):
        return dataclasses.asdict(self)


@dataclasses.dataclass
class MediaInfo:
    title: str
    source_url: str
    source_site: str
    thumbnail: str = None
    duration_seconds: float = None
    is_playlist: bool = False
    playlist_count: int = None
    formats: list = dataclasses.field(default_factory=list)  # list[FormatInfo]
    engine: str = None  # "media" | "torrent" | "http"
    uploader: str = None
    raw_formats: list = dataclasses.field(default_factory=list)  # every real source format (Custom picker)
    entries: list = dataclasses.field(default_factory=list)  # playlist entries (dicts)
    chapters: int = 0
    resumable: bool = None  # http: server supports byte ranges
    filename: str = None  # http/torrent: name the file(s) will get
    extra: dict = dataclasses.field(default_factory=dict)  # engine-specific facts (seeds, mime type, ...)

    def to_dict(self):
        return {
            "title": self.title,
            "source_url": self.source_url,
            "source_site": self.source_site,
            "thumbnail": self.thumbnail,
            "duration_seconds": self.duration_seconds,
            "is_playlist": self.is_playlist,
            "playlist_count": self.playlist_count,
            "formats": [f.to_dict() for f in self.formats],
            "engine": self.engine,
            "uploader": self.uploader,
            "raw_formats": list(self.raw_formats),
            "entries": list(self.entries),
            "chapters": self.chapters,
            "resumable": self.resumable,
            "filename": self.filename,
            "extra": dict(self.extra),
        }


# Fields persisted for queue restore (runtime-only state is excluded).
_PERSISTED_FIELDS = (
    "job_id", "url", "kind", "quality_label", "format_key", "is_playlist", "download_dir", "audio_bitrate",
    "priority", "source", "show_popup", "filename_override", "category", "engine", "options", "status",
    "title", "progress", "downloaded_bytes", "total_bytes", "resumable", "retries", "result_path",
    "result_paths", "error_message", "error_hint", "error_details", "created_at", "started_at", "finished_at",
    "playlist_title", "playlist_index",
)


class DownloadJob:
    """One entry in the download queue/history. Holds only plain data (no
    Tkinter/FastAPI objects) so it is safe to read from any thread. Mutation
    should go through DownloadManager, which holds the lock protecting the
    job list this object lives in.
    """

    def __init__(self, url, kind, quality_label, format_key, is_playlist,
                 download_dir, audio_bitrate="192", priority=0, source="gui",
                 show_popup=False, title=None, filename_override=None, category=None,
                 engine=None, options=None):
        self.job_id = next_job_id()
        self.url = url
        self.kind = kind  # "video" | "audio" | "image" | "subtitle" | "torrent" | "file"
        self.quality_label = quality_label  # human label, e.g. "720p (HD)"
        self.format_key = format_key  # internal key used by the engine
        self.is_playlist = is_playlist
        self.download_dir = download_dir
        self.audio_bitrate = audio_bitrate
        self.priority = priority
        self.source = source  # "gui" | "extension"
        self.show_popup = show_popup
        self.filename_override = filename_override  # sanitized base name, no extension
        self.category = category or {"video": "Video", "audio": "Music", "torrent": "Torrent"}.get(kind, "Other")
        self.engine = engine  # "media" | "torrent" | "http" (set when queued)
        self.options = dict(options or {})  # engine-specific choices (container, subtitles, ...)
        self.provider = None  # runtime-only: the engine object chosen at queue time
        self.from_history = False  # True for rows reconstructed from history at startup
        self.history_key = None  # (url, date, title) — lets the GUI remove the matching history entry
        self.history_id = None

        self.status = STATUS_QUEUED
        self.title = title or url
        self.message = ""
        self.error_message = None  # short reason
        self.error_hint = None  # suggested action
        self.error_details = None  # technical text
        self.progress = 0.0
        self.speed_bps = 0.0
        self.speed_text = ""
        self.eta_seconds = None
        self.eta_text = ""
        self.downloaded_bytes = 0
        self.total_bytes = None
        self.resumable = True
        self.retries = 0
        self.result_path = None  # main output file (or folder for multi-file torrents)
        self.result_paths = []
        self.temp_files = set()  # runtime: partial files to clean up on cancel
        self.speed_history = []  # list of (timestamp, speed_bps) samples actually recorded
        self.peers = None  # torrent: connected peers
        self.seeds = None  # torrent: connected seeds
        self.upload_bps = 0.0
        self.playlist_title = None
        self.playlist_index = None

        self.created_at = time.time()
        self.started_at = None
        self.finished_at = None

        # Runtime-only synchronization primitives; never serialized.
        self.cancel_event = threading.Event()
        self.pause_event = threading.Event()

    @property
    def display_name(self):
        return self.filename_override or self.title or self.url

    def to_dict(self):
        return {
            "id": self.job_id,
            "url": self.url,
            "title": self.title,
            "kind": self.kind,
            "engine": self.engine,
            "quality_label": self.quality_label,
            "is_playlist": self.is_playlist,
            "download_dir": self.download_dir,
            "status": self.status,
            "message": self.message,
            "error_message": self.error_message,
            "error_hint": self.error_hint,
            "error_details": self.error_details,
            "progress": round(self.progress, 1),
            "speed_bps": self.speed_bps,
            "speed_text": self.speed_text,
            "eta_seconds": self.eta_seconds,
            "eta_text": self.eta_text,
            "downloaded_bytes": self.downloaded_bytes,
            "total_bytes": self.total_bytes,
            "resumable": self.resumable,
            "retries": self.retries,
            "priority": self.priority,
            "source": self.source,
            "result_path": self.result_path,
            "filename_override": self.filename_override,
            "category": self.category,
            "options": dict(self.options),
            "peers": self.peers,
            "seeds": self.seeds,
            "upload_bps": self.upload_bps,
            "playlist_title": self.playlist_title,
            "playlist_index": self.playlist_index,
            "speed_history": list(self.speed_history[-40:]),
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }

    def to_persisted(self):
        return {name: getattr(self, name) for name in _PERSISTED_FIELDS}

    @classmethod
    def from_persisted(cls, data):
        job = cls(
            url=data["url"], kind=data.get("kind", "video"), quality_label=data.get("quality_label", ""),
            format_key=data.get("format_key", ""), is_playlist=data.get("is_playlist", False),
            download_dir=data.get("download_dir", ""),
        )
        for name in _PERSISTED_FIELDS:
            if name in data:
                setattr(job, name, data[name])
        job.result_paths = list(job.result_paths or [])
        job.options = dict(job.options or {})
        return job
