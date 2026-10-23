"""Media Engine — Master Download Manager's adapter around the bundled yt-dlp
library (≈1,750 site extractors).

Responsibilities:
  * decide whether a URL belongs to a media site (specific extractor match)
  * detect(): read the real formats/subtitles/playlist entries a source has
  * translate a user's choice (preset, custom format, audio conversion,
    subtitles, embedding, playlist items, section) into yt-dlp options
  * run the download in-process with progress, pause/cancel and safe temp files

Every format/size/codec value shown to the user is read from what yt-dlp
reports for that specific URL — nothing here invents a resolution, bitrate,
codec or size. Unknown values stay None and the UI shows "Unknown".
"""

import functools
import os
import re
import shutil
import threading
import uuid
from urllib.parse import urlparse

import yt_dlp
from yt_dlp.utils import DownloadCancelled, DownloadError

from .. import tools
from ..config import CACHE_DIR, DEFAULT_SETTINGS
from ..errors import classify_error  # noqa: F401 (re-exported for callers/tests)
from ..logging_setup import YtDlpLogger, get_logger
from ..models import FormatInfo, MediaInfo
from .base import MediaProvider, PauseRequested, UnsupportedMediaError  # noqa: F401 (re-exported)

log = get_logger("media")

YOUTUBE_URL_RE = re.compile(r"^(https?://)?(www\.|m\.|music\.)?(youtube\.com/|youtu\.be/)", re.IGNORECASE)
_HTTP_RE = re.compile(r"^https?://", re.IGNORECASE)

RESOLUTION_LABELS = {
    "360p": 360,
    "480p (SD)": 480,
    "720p (HD)": 720,
    "1080p (Full HD)": 1080,
    "1440p (2K)": 1440,
    "2160p (4K / Ultra HD)": 2160,
}
VIDEO_QUALITIES = list(RESOLUTION_LABELS)
MP3_LABEL = "MP3 (audio only)"
AUDIO_BITRATES = ("128 kbps", "192 kbps", "256 kbps", "320 kbps (Maximum)")
LOSSY_BITRATES_KBPS = (128, 192, 256, 320)
CONTAINERS = ("mp4", "mkv", "original")
SUBTITLE_FORMATS = ("srt", "vtt", "ass")

_RESOLUTION_TIERS = sorted(set(RESOLUTION_LABELS.values()))

# Multi-connection fetching (byte-range chunks / parallel fragments) gets far
# closer to the real available bandwidth than a single connection.
HTTP_CHUNK_SIZE_BYTES = 10 * 1024 * 1024
CONCURRENT_FRAGMENT_DOWNLOADS = 8

# File types that embed a thumbnail through FFmpeg alone (no mutagen).
_THUMBNAIL_EMBED_EXTS = {"mp4", "m4a", "m4v", "mov", "mkv", "mka", "mp3"}
_SUBTITLE_EMBED_CONTAINERS = {"mp4", "mkv", "webm"}

# Partial-file suffixes yt-dlp leaves behind.
_PARTIAL_SUFFIXES = (".part", ".ytdl", ".temp", ".part-frag")
PARTIAL_DIR_NAME = ".mdm-partial"

# Characters allowed in an advanced, user-typed yt-dlp format selector.
_CUSTOM_FORMAT_RE = re.compile(r"^[A-Za-z0-9+/\[\]<>=!*?^$~.:,_ \-]{1,200}$")

DEFAULT_TEMPLATE = "%(title)s [%(id)s]"


# ------------------------------------------------------------------ URLs --- #

def is_valid_youtube_url(url):
    return bool(YOUTUBE_URL_RE.match((url or "").strip()))


def is_http_url(url):
    return bool(_HTTP_RE.match((url or "").strip()))


def is_playlist_url(url):
    """A YouTube watch URL opened from within a playlist (`&list=...`) or a
    bare playlist URL both carry this marker."""
    return "list=" in (url or "")


_extractor_lock = threading.Lock()
_extractors = None


def _specific_extractors():
    global _extractors
    with _extractor_lock:
        if _extractors is None:
            from yt_dlp.extractor import gen_extractor_classes
            _extractors = [ie for ie in gen_extractor_classes() if ie.IE_NAME != "generic"]
        return _extractors


_YOUTUBE_WATCH_RE = re.compile(r"^https?://(www\.|m\.|music\.)?(youtube\.com/(watch\?|shorts/|live/)|youtu\.be/)",
                               re.IGNORECASE)


def matching_extractor(url):
    """Name of the first site-specific yt-dlp extractor for this URL, or None
    (None means only yt-dlp's generic page scanner could try it)."""
    url = (url or "").strip()
    if not is_http_url(url):
        return None
    if _YOUTUBE_WATCH_RE.match(url) and "list=" not in url:
        return "youtube"  # fast path for the most common case (e.g. queuing a big playlist)
    return _matching_extractor_cached(url)


@functools.lru_cache(maxsize=4096)
def _matching_extractor_cached(url):
    for ie in _specific_extractors():
        try:
            if ie.suitable(url):
                return ie.IE_NAME
        except Exception:  # noqa: BLE001 - one broken regex must not break routing
            continue
    return None


def ffmpeg_available(custom_path=""):
    return tools.find_ffmpeg(custom_path) is not None


# ------------------------------------------------------- format helpers --- #

def _dedupe_video_formats(info_formats):
    """Collapse yt-dlp's raw format list into one entry per resolution tier
    we expose, keeping only tiers actually present in the source."""
    best_by_height = {}
    for f in info_formats:
        if f.get("vcodec") in (None, "none"):
            continue  # audio-only (or unknown, which yt-dlp reports as None)
        height = f.get("height")
        if not height:
            continue
        tier = min((t for t in _RESOLUTION_TIERS if t >= height), default=max(_RESOLUTION_TIERS))
        existing = best_by_height.get(tier)
        if existing is None or abs(height - tier) < abs(existing.get("height", 0) - tier):
            best_by_height[tier] = f
    return best_by_height


def _label_for_tier(tier):
    for label, height in RESOLUTION_LABELS.items():
        if height == tier:
            return label
    return f"{tier}p"


def _quality_tier_for_height(height):
    """Presentational only — derived purely from the real reported height."""
    if height is None:
        return None
    if height < 480:
        return "Low"
    if height < 1080:
        return "Medium"
    return "High"


def _best_thumbnail_url(thumbnails):
    if not thumbnails:
        return None
    best = max(thumbnails, key=lambda t: (t.get("width") or 0) * (t.get("height") or 0))
    return best.get("url")


def _entry_url(e):
    url = e.get("url") or e.get("webpage_url") or e.get("original_url")
    if url and not is_http_url(url) and e.get("ie_key") == "Youtube":
        url = None  # flat YouTube entries sometimes carry only the bare id in "url"
    if not url and e.get("id") and (e.get("ie_key") in (None, "Youtube")):
        url = f"https://www.youtube.com/watch?v={e['id']}"
    return url


def _playlist_entries_from_raw(raw_entries):
    """Pure helper (no network): yt-dlp flat-playlist entries -> plain dicts.
    Entries yt-dlp couldn't resolve (deleted/private) come back as None and
    are skipped rather than shown as a broken row."""
    entries = []
    for e in raw_entries or []:
        if not e:
            continue
        url = _entry_url(e)
        if not url:
            continue
        entries.append({
            "video_id": e.get("id"),
            "url": url,
            "title": e.get("title") or "Untitled",
            "duration_seconds": e.get("duration"),
            "thumbnail": _best_thumbnail_url(e.get("thumbnails")),
        })
    return entries


def _raw_format_rows(raw_formats):
    """Every real format the source offers, for the advanced/Custom picker."""
    rows = []
    for f in raw_formats or []:
        if f.get("format_id") is None:
            continue
        if (f.get("protocol") or "").startswith("mhtml"):
            continue  # storyboards, not media
        has_video = f.get("vcodec") not in (None, "none")
        has_audio = f.get("acodec") not in (None, "none")
        rows.append({
            "format_id": str(f.get("format_id")),
            "ext": f.get("ext"),
            "resolution": f.get("resolution") or (f"{f.get('width')}x{f.get('height')}" if f.get("height") else None),
            "height": f.get("height"),
            "fps": f.get("fps"),
            "vcodec": f.get("vcodec") if has_video else None,
            "acodec": f.get("acodec") if has_audio else None,
            "has_video": has_video,
            "has_audio": has_audio,
            "tbr_kbps": f.get("tbr"),
            "abr_kbps": f.get("abr"),
            "filesize_bytes": f.get("filesize") or f.get("filesize_approx"),
            "filesize_is_estimate": not f.get("filesize") and bool(f.get("filesize_approx")),
            "note": f.get("format_note"),
            "protocol": f.get("protocol"),
        })
    return rows


def _subtitle_formats(info):
    out = []
    for lang_code, tracks in (info.get("subtitles") or {}).items():
        if not tracks or lang_code == "live_chat":
            continue
        name = tracks[0].get("name") or lang_code
        out.append(FormatInfo(format_id=f"subtitle-{lang_code}", kind="subtitle", label=name, ext="srt",
                              lang_code=lang_code, lang_name=name))
    for lang_code, tracks in (info.get("automatic_captions") or {}).items():
        if not tracks:
            continue
        name = tracks[0].get("name") or lang_code
        out.append(FormatInfo(format_id=f"autosub-{lang_code}", kind="subtitle", label=f"{name} (auto-generated)",
                              ext="srt", lang_code=lang_code, lang_name=name, is_auto=True))
    return out


def resolve_format_selection(format_id):
    """Translate a FormatInfo.format_id (as returned by detect()) into the
    (kind, quality_label, format_key, audio_bitrate) tuple add_job() needs.
    Raises ValueError for an unrecognised format_id."""
    format_id = (format_id or "").strip()
    if format_id == "source-audio":
        return "audio", "Source Quality", format_id, None
    if format_id == "thumbnail":
        return "image", "Thumbnail", format_id, None
    if format_id.startswith("subtitle-"):
        lang = format_id.split("-", 1)[1]
        return "subtitle", f"Subtitle ({lang})", format_id, None
    if format_id.startswith("autosub-"):
        lang = format_id.split("-", 1)[1]
        return "subtitle", f"Auto subtitle ({lang})", format_id, None
    if format_id.startswith("mp3-"):
        kbps = format_id.split("-", 1)[1]
        if not kbps.isdigit():
            raise ValueError(f"Unknown format_id: {format_id}")
        return "audio", f"MP3 — {kbps} kbps", format_id, kbps
    if format_id.startswith("audio-"):
        parts = format_id.split("-")
        codec = parts[1] if len(parts) > 1 else ""
        if codec not in tools.AUDIO_CODECS:
            raise ValueError(f"Unknown format_id: {format_id}")
        label = tools.AUDIO_CODECS[codec][0]
        if len(parts) == 3 and parts[2].isdigit():
            return "audio", f"{label} — {parts[2]} kbps", format_id, parts[2]
        return "audio", f"{label} — best quality" if not tools.AUDIO_CODECS[codec][2] else label, format_id, None
    if format_id == "video-best":
        return "video", "Best available", format_id, None
    if format_id == "video-worst":
        return "video", "Smallest available", format_id, None
    if format_id.startswith("video-"):
        height = format_id.split("-", 1)[1]
        if not height.isdigit():
            raise ValueError(f"Unknown format_id: {format_id}")
        return "video", _label_for_tier(int(height)), format_id, None
    if format_id.startswith("fmt:"):
        spec = format_id[4:]
        if not _CUSTOM_FORMAT_RE.match(spec):
            raise ValueError("That custom format selector contains characters that aren't allowed.")
        return "video", f"Custom ({spec})", format_id, None
    raise ValueError(f"Unknown format_id: {format_id}")


def sanitize_template(template):
    """User filename templates may create subfolders but must stay inside the
    download folder: no drive letters, absolute paths or '..' segments."""
    template = (template or "").strip().replace("\\", "/")
    if not template:
        return DEFAULT_TEMPLATE
    if re.match(r"^[A-Za-z]:", template) or template.startswith("/"):
        return DEFAULT_TEMPLATE
    parts = [p for p in template.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts) or not parts:
        return DEFAULT_TEMPLATE
    return "/".join(parts)


class _SettingsView:
    """Read-only settings access that works with or without a SettingsStore."""

    def __init__(self, store):
        self._store = store

    def get(self, key, default=None):
        if self._store is not None:
            value = self._store.get(key, None)
            if value is not None:
                return value
        return DEFAULT_SETTINGS.get(key, default)


# ---------------------------------------------------------------- engine --- #

class MediaEngine(MediaProvider):
    name = "media"
    display_name = "Media"

    def __init__(self, settings=None):
        self.settings = settings

    # -- routing -- #
    def supports(self, url):
        from ..router import looks_like_direct_file  # local import: router imports providers
        url = (url or "").strip()
        if not is_http_url(url):
            return False
        if matching_extractor(url):
            return True
        return not looks_like_direct_file(url)

    def resolve_format_selection(self, format_id):
        return resolve_format_selection(format_id)

    # -- shared options -- #
    def _cfg(self):
        return _SettingsView(self.settings)

    def base_options(self, logger=None, cookie_holder=None):
        cfg = self._cfg()
        opts = {
            "quiet": False,
            "noprogress": True,
            "no_color": True,
            "logger": logger or YtDlpLogger(),
            "js_runtimes": tools.js_runtimes(),
            "remote_components": [],  # never fetch solver scripts from GitHub/npm at runtime
            "cachedir": os.path.join(CACHE_DIR, "media"),
            "socket_timeout": max(5, int(cfg.get("download_timeout", 30) or 30)),
            "retries": 10,
            "fragment_retries": 10,
            "extractor_retries": 3,
            "file_access_retries": 3,
            "warn_when_outdated": False,
            "windowsfilenames": True,
        }
        location = tools.ffmpeg_location(cfg.get("ffmpeg_path", ""))
        if location:
            opts["ffmpeg_location"] = location
        proxy = (cfg.get("proxy", "") or "").strip()
        if proxy:
            opts["proxy"] = proxy
        mode = cfg.get("cookies_mode", "none")
        if mode == "browser":
            browser = (cfg.get("cookies_browser", "") or "").strip().lower()
            if browser:
                opts["cookiesfrombrowser"] = (browser,)
        elif mode == "file":
            path = (cfg.get("cookies_file", "") or "").strip()
            if path and os.path.isfile(path):
                # yt-dlp writes cookies back to its cookiefile on exit; give it a
                # private copy so the user's own file is never modified.
                private = os.path.join(CACHE_DIR, f"cookies-{uuid.uuid4().hex}.txt")
                os.makedirs(CACHE_DIR, exist_ok=True)
                shutil.copyfile(path, private)
                opts["cookiefile"] = private
                if cookie_holder is not None:
                    cookie_holder.append(private)
        return opts

    @staticmethod
    def _discard_private_cookies(cookie_holder):
        for path in cookie_holder:
            try:
                os.remove(path)
            except OSError:
                pass

    def _extract(self, url, extra_opts):
        cookies = []
        logger = YtDlpLogger("detect")
        opts = self.base_options(logger, cookies)
        opts.update(extra_opts)
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                return ydl.extract_info(url, download=False)
        except DownloadError as e:
            raise UnsupportedMediaError(classify_error(e)) from e
        finally:
            self._discard_private_cookies(cookies)

    # -- detection -- #
    def detect(self, url, playlist_limit=1000):
        if not is_http_url(url):
            raise UnsupportedMediaError("That doesn't look like a web address (it should start with http:// or https://).")
        info = self._extract(url, {
            "skip_download": True, "noplaylist": True, "extract_flat": "in_playlist",
            "playlist_items": f"1:{int(playlist_limit)}",
        })
        if not info:
            raise UnsupportedMediaError("No downloadable media was found at that URL.")
        if info.get("_type") in ("playlist", "multi_video"):
            return self._playlist_media_info(url, info, playlist_limit)
        return self._single_media_info(url, info)

    def _site_name(self, info, url):
        return info.get("extractor_key") or info.get("ie_key") or urlparse(url).hostname or "Web"

    def _single_media_info(self, url, info):
        if info.get("_has_drm") or any(f.get("has_drm") for f in info.get("formats") or []):
            if not [f for f in info.get("formats") or [] if not f.get("has_drm")]:
                raise UnsupportedMediaError("This media is DRM-protected and cannot be downloaded.")
        raw_formats = [f for f in info.get("formats") or [] if not f.get("has_drm")]
        if not raw_formats and info.get("url"):
            raw_formats = [info]  # single-format result (no "formats" list)
        cfg = self._cfg()
        ffmpeg = tools.find_ffmpeg(cfg.get("ffmpeg_path", ""))
        codecs = tools.available_audio_codecs(cfg.get("ffmpeg_path", "")) if ffmpeg else []
        container = cfg.get("default_container", "mp4")
        out_ext = container if container in ("mp4", "mkv") else "mp4"

        formats = []
        tiers = _dedupe_video_formats(raw_formats)
        video_formats = [f for f in raw_formats if f.get("vcodec") not in (None, "none")]
        for tier, f in sorted(tiers.items()):
            formats.append(FormatInfo(
                format_id=f"video-{tier}", kind="video", label=_label_for_tier(tier), ext=out_ext,
                resolution=f"{f.get('width')}x{f.get('height')}" if f.get("width") else None,
                height=tier, fps=f.get("fps"), vcodec=f.get("vcodec"),
                acodec=f.get("acodec") if f.get("acodec") not in (None, "none") else None,
                bitrate_kbps=f.get("tbr") or f.get("vbr"), quality_tier=_quality_tier_for_height(tier),
                filesize_bytes=f.get("filesize") or f.get("filesize_approx"),
                filesize_is_estimate=not bool(f.get("filesize")) and bool(f.get("filesize_approx")),
            ))
        if raw_formats:
            heights = [f.get("height") for f in video_formats if f.get("height")]
            best_note = f" ({max(heights)}p)" if heights else ""
            worst_note = f" ({min(heights)}p)" if heights else ""
            kind_best = "video" if video_formats or not any(f.get("vcodec") == "none" for f in raw_formats) else "audio"
            formats.append(FormatInfo(format_id="video-best", kind=kind_best, label=f"Best available{best_note}",
                                      ext=out_ext if kind_best == "video" else (raw_formats[-1].get("ext") or ""),
                                      height=max(heights) if heights else None))
            if len(raw_formats) > 1:
                formats.append(FormatInfo(format_id="video-worst", kind=kind_best,
                                          label=f"Smallest available{worst_note}",
                                          ext=out_ext if kind_best == "video" else "",
                                          height=min(heights) if heights else None))

        audio_formats = [f for f in raw_formats if f.get("vcodec") in (None, "none") and f.get("acodec") not in (None, "none")]
        best_audio = max(audio_formats, key=lambda f: f.get("abr") or f.get("tbr") or 0) if audio_formats else None
        has_any_audio = bool(audio_formats) or any(f.get("acodec") not in (None, "none") for f in raw_formats)

        if has_any_audio and "mp3" in codecs:
            for kbps in LOSSY_BITRATES_KBPS:
                formats.append(FormatInfo(format_id=f"mp3-{kbps}", kind="audio", label=f"MP3 — {kbps} kbps",
                                          ext="mp3", acodec="mp3", abr_kbps=float(kbps)))
        if best_audio:
            formats.append(FormatInfo(
                format_id="source-audio", kind="audio",
                label=f"{(best_audio.get('ext') or 'm4a').upper()} — Source Quality",
                ext=best_audio.get("ext") or "m4a", acodec=best_audio.get("acodec"),
                abr_kbps=best_audio.get("abr"),
                filesize_bytes=best_audio.get("filesize") or best_audio.get("filesize_approx"),
                filesize_is_estimate=not bool(best_audio.get("filesize")) and bool(best_audio.get("filesize_approx")),
            ))
        if has_any_audio:
            for codec in codecs:
                if codec == "mp3":
                    continue
                label, _, lossless = tools.AUDIO_CODECS[codec]
                ext = {"vorbis": "ogg", "m4a": "m4a"}.get(codec, codec)
                formats.append(FormatInfo(format_id=f"audio-{codec}", kind="audio",
                                          label=label if lossless else f"{label} — best quality",
                                          ext=ext, acodec=codec))

        thumbnails = info.get("thumbnails") or []
        if thumbnails:
            best_thumb = max(thumbnails, key=lambda t: (t.get("width") or 0) * (t.get("height") or 0))
            if best_thumb.get("width") and best_thumb.get("height"):
                formats.append(FormatInfo(format_id="thumbnail", kind="image",
                                          label=f"JPG {best_thumb['width']}x{best_thumb['height']}", ext="jpg",
                                          resolution=f"{best_thumb['width']}x{best_thumb['height']}"))
        formats.extend(_subtitle_formats(info))

        return MediaInfo(
            title=info.get("title") or url, source_url=url, source_site=self._site_name(info, url),
            thumbnail=info.get("thumbnail") or _best_thumbnail_url(thumbnails),
            duration_seconds=info.get("duration"), is_playlist=False, formats=formats, engine=self.name,
            uploader=info.get("uploader") or info.get("channel"), raw_formats=_raw_format_rows(raw_formats),
            chapters=len(info.get("chapters") or []),
            extra={"ffmpeg": bool(ffmpeg), "audio_codecs": codecs, "extractor": info.get("extractor_key"),
                   "is_live": bool(info.get("is_live"))},
        )

    def _playlist_media_info(self, url, info, playlist_limit):
        entries = _playlist_entries_from_raw(info.get("entries"))
        for i, entry in enumerate(entries, start=1):
            entry["index"] = i
        cfg = self._cfg()
        codecs = tools.available_audio_codecs(cfg.get("ffmpeg_path", "")) if tools.find_ffmpeg(cfg.get("ffmpeg_path", "")) else []
        # Per-item formats can't be known without opening every item, so
        # playlist presets are caps ("up to 1080p"), never claims.
        formats = [FormatInfo(format_id="video-best", kind="video", label="Best available", ext="")]
        for tier in reversed(_RESOLUTION_TIERS):
            formats.append(FormatInfo(format_id=f"video-{tier}", kind="video", label=f"Up to {_label_for_tier(tier)}",
                                      ext="", height=tier))
        formats.append(FormatInfo(format_id="source-audio", kind="audio", label="Original audio", ext=""))
        if "mp3" in codecs:
            for kbps in LOSSY_BITRATES_KBPS:
                formats.append(FormatInfo(format_id=f"mp3-{kbps}", kind="audio", label=f"MP3 — {kbps} kbps",
                                          ext="mp3", abr_kbps=float(kbps)))
        for codec in codecs:
            if codec != "mp3":
                formats.append(FormatInfo(format_id=f"audio-{codec}", kind="audio", label=tools.AUDIO_CODECS[codec][0],
                                          ext=codec))
        count = info.get("playlist_count")
        return MediaInfo(
            title=info.get("title") or "Playlist", source_url=url, source_site=self._site_name(info, url),
            thumbnail=_best_thumbnail_url(info.get("thumbnails")), is_playlist=True,
            playlist_count=count if count is not None else len(entries), formats=formats, engine=self.name,
            uploader=info.get("uploader") or info.get("channel"), entries=entries,
            extra={"truncated": len(entries) >= playlist_limit, "audio_codecs": codecs,
                   "playlist_id": info.get("id")},
        )

    def _playlist_info(self, url, limit):
        if is_valid_youtube_url(url) and not is_playlist_url(url) and not re.search(r"/(@|channel/|c/|user/)", url):
            raise UnsupportedMediaError("That URL isn't part of a playlist.")
        info = self._extract(url, {"skip_download": True, "extract_flat": "in_playlist", "noplaylist": False,
                                   "playlist_items": f"1:{int(limit)}"})
        if not info or info.get("_type") not in ("playlist", "multi_video"):
            raise UnsupportedMediaError("That URL isn't part of a playlist.")
        return info

    def detect_playlist_media(self, url, limit=1000):
        """Playlist/channel listing as MediaInfo (entries + playlist presets),
        e.g. for a watch URL that also carries `list=`."""
        return self._playlist_media_info(url, self._playlist_info(url, limit), limit)

    def detect_playlist(self, url, limit=1000):
        """Flat (metadata-only) listing of a playlist/channel's entries."""
        info = self._playlist_info(url, limit)
        entries = _playlist_entries_from_raw(info.get("entries"))
        if not entries:
            raise UnsupportedMediaError("This playlist has no downloadable videos.")
        return {
            "playlist_title": info.get("title") or "Playlist",
            "entry_count": len(entries),
            "entries": entries,
        }

    # -- download options -- #
    def build_download_opts(self, job, progress_hook, logger=None, cookie_holder=None):
        cfg = self._cfg()
        o = job.options or {}
        key = job.format_key or ""
        kind = job.kind

        opts = self.base_options(logger, cookie_holder)
        opts.update({
            "progress_hooks": [progress_hook],
            "postprocessor_hooks": [lambda d: _forward_pp_hook(d, progress_hook)],
            "noplaylist": not job.is_playlist,
            "ignoreerrors": "only_download" if job.is_playlist else False,
            "overwrites": False,
            "continuedl": True,
            "http_chunk_size": HTTP_CHUNK_SIZE_BYTES,
            "concurrent_fragment_downloads": max(1, int(cfg.get("concurrent_fragments", CONCURRENT_FRAGMENT_DOWNLOADS)
                                                        or CONCURRENT_FRAGMENT_DOWNLOADS)),
            # Partial files live in a temp subfolder and only the finished file
            # is moved into the download folder ("safe finalization").
            "paths": {"home": job.download_dir, "temp": os.path.join(job.download_dir, PARTIAL_DIR_NAME)},
        })
        limit_kbps = int(o.get("speed_limit_kbps") or cfg.get("speed_limit_kbps", 0) or 0)
        if limit_kbps > 0:
            opts["ratelimit"] = limit_kbps * 1024
        if o.get("playlist_items"):
            opts["playlist_items"] = str(o["playlist_items"])
        section = o.get("section")
        if section and (section.get("start") is not None or section.get("end") is not None):
            from yt_dlp.utils import download_range_func
            start = float(section.get("start") or 0)
            end = float(section["end"]) if section.get("end") is not None else float("inf")
            opts["download_ranges"] = download_range_func(None, [(start, end)])
            opts["force_keyframes_at_cuts"] = bool(section.get("precise"))

        opts["outtmpl"] = self._output_template(job, cfg)
        postprocessors = []
        container = o.get("container") or cfg.get("default_container", "mp4")
        output_ext = None

        if kind == "image":
            opts.update({"skip_download": True, "writethumbnail": True})
            if tools.find_ffmpeg(cfg.get("ffmpeg_path", "")):
                postprocessors.append({"key": "FFmpegThumbnailsConvertor", "format": "jpg", "when": "before_dl"})
        elif kind == "subtitle":
            lang = key.split("-", 1)[1] if "-" in key else key
            subs_format = o.get("subtitle_format") or cfg.get("subtitle_format", "srt")
            opts.update({
                "skip_download": True,
                "writesubtitles": not key.startswith("autosub-"),
                "writeautomaticsub": key.startswith("autosub-"),
                "subtitleslangs": [lang],
                "subtitlesformat": f"{subs_format}/best",
            })
            if subs_format in SUBTITLE_FORMATS and tools.find_ffmpeg(cfg.get("ffmpeg_path", "")):
                postprocessors.append({"key": "FFmpegSubtitlesConvertor", "format": subs_format, "when": "before_dl"})
        elif kind == "audio":
            opts["format"] = "ba/b"
            codec, quality = None, None
            if key.startswith("mp3-"):
                codec, quality = "mp3", key.split("-", 1)[1]
            elif key.startswith("audio-"):
                parts = key.split("-")
                codec = parts[1]
                quality = parts[2] if len(parts) == 3 else ("0" if not tools.AUDIO_CODECS[codec][2] else None)
            if codec:
                pp = {"key": "FFmpegExtractAudio", "preferredcodec": codec}
                if quality:
                    pp["preferredquality"] = quality
                postprocessors.append(pp)
                output_ext = {"vorbis": "ogg", "m4a": "m4a"}.get(codec, codec)
        else:  # video (presets, best/worst, custom)
            if key == "video-best":
                opts["format"] = "bv*+ba/b"
            elif key == "video-worst":
                opts["format"] = "wv*+wa/w"
            elif key.startswith("fmt:"):
                spec = key[4:]
                if not _CUSTOM_FORMAT_RE.match(spec):
                    raise UnsupportedMediaError("That custom format selector contains characters that aren't allowed.")
                opts["format"] = spec
            else:
                height = RESOLUTION_LABELS.get(job.quality_label)
                if key.startswith("video-") and key.split("-", 1)[1].isdigit():
                    height = int(key.split("-", 1)[1])
                height = height or 720
                # Exactly the chosen tier when it exists; for playlists (caps)
                # fall back to the smallest available rather than failing.
                opts["format"] = f"bv*[height<={height}]+ba/b[height<={height}]/wv*+ba/w"
            if o.get("prefer_h264", cfg.get("prefer_h264", False)):
                opts["format_sort"] = ["res", "vcodec:h264", "acodec:aac"]
            if container in ("mp4", "mkv", "webm"):
                opts["merge_output_format"] = container
                postprocessors.append({"key": "FFmpegVideoRemuxer", "preferedformat": container})
                output_ext = container

        # Subtitles alongside a video/audio download.
        subs = o.get("subtitles") or {}
        if kind in ("video", "audio") and subs.get("langs"):
            subs_format = subs.get("format") or cfg.get("subtitle_format", "srt")
            opts.update({
                "writesubtitles": True,
                "writeautomaticsub": bool(subs.get("auto")),
                "subtitleslangs": list(subs["langs"]),
                "subtitlesformat": f"{subs_format}/best",
            })
            if subs_format in SUBTITLE_FORMATS:
                postprocessors.insert(0, {"key": "FFmpegSubtitlesConvertor", "format": subs_format, "when": "before_dl"})
            if subs.get("embed") and kind == "video" and (output_ext in _SUBTITLE_EMBED_CONTAINERS or output_ext is None):
                postprocessors.append({"key": "FFmpegEmbedSubtitle", "already_have_subtitle": False})

        if kind in ("video", "audio"):
            add_meta = o.get("embed_metadata", cfg.get("embed_metadata", True))
            add_chapters = o.get("embed_chapters", cfg.get("embed_chapters", True)) and kind == "video"
            if (add_meta or add_chapters) and output_ext != "wav":
                postprocessors.append({"key": "FFmpegMetadata", "add_metadata": bool(add_meta),
                                       "add_chapters": bool(add_chapters), "add_infojson": False})
            # Only when the final file type is known to support it: an unknown
            # ("original") container may be WebM, where embedding fails the job.
            if o.get("embed_thumbnail", cfg.get("embed_thumbnail", False)) and output_ext in _THUMBNAIL_EMBED_EXTS:
                postprocessors.append({"key": "EmbedThumbnail", "already_have_thumbnail": bool(o.get("write_thumbnail"))})
                opts["writethumbnail"] = True
            elif o.get("write_thumbnail"):
                opts["writethumbnail"] = True

        if not tools.find_ffmpeg(cfg.get("ffmpeg_path", "")):
            # Without FFmpeg only post-processing-free downloads can succeed;
            # keep the format choice but drop steps that would fail later.
            postprocessors = [pp for pp in postprocessors if pp["key"] not in (
                "FFmpegVideoRemuxer", "FFmpegMetadata", "EmbedThumbnail", "FFmpegEmbedSubtitle",
                "FFmpegSubtitlesConvertor", "FFmpegThumbnailsConvertor")]
            opts.pop("merge_output_format", None)
        if postprocessors:
            opts["postprocessors"] = postprocessors
        return opts

    def _output_template(self, job, cfg):
        if job.filename_override:
            # User-edited name; yt-dlp always appends the real extension, so a
            # typed ".mp3" for a video can't mislabel the file.
            base = job.filename_override.replace("%", "%%")
        else:
            base = sanitize_template(cfg.get("filename_template", DEFAULT_TEMPLATE))
            if job.kind == "video" and "%(height)" not in base:
                base += " [%(height)sp]"
            elif job.kind == "image":
                base += " [thumbnail]"
        if job.is_playlist:
            base = "%(playlist_title|Playlist)s/%(playlist_index)03d - " + base.split("/")[-1]
        elif (job.options or {}).get("playlist_index"):
            # One item of a playlist queued on its own: keep playlist order in the name.
            base = f"{int(job.options['playlist_index']):03d} - " + base
        return {"default": f"{base}.%(ext)s", "chapter": f"{base} - %(section_number)03d %(section_title)s.%(ext)s"}

    # -- run -- #
    def run_download(self, job, progress_hook):
        logger = YtDlpLogger(f"job {job.job_id}")
        job._engine_log = logger.last_errors  # noqa: SLF001 - read by the queue for technical details
        cookies = []

        def tracking_hook(d):
            for k in ("tmpfilename", "filename"):
                if d.get(k):
                    job.temp_files.add(d[k])
            progress_hook(d)

        try:
            info = self._download_with_fresh_retry(job, tracking_hook, logger, cookies)
            job._engine_log = logger.last_errors  # noqa: SLF001
            if not info:
                raise DownloadError("No downloadable media was found at that URL.")
            if job.is_playlist and logger.last_errors and not _collect_filepaths(info):
                raise DownloadError(logger.last_errors[-1])
            result = {"title": info.get("title"), "filepaths": _collect_filepaths(info)}
            if info.get("_type") == "playlist":
                result["playlist_title"] = info.get("title")
            return result
        finally:
            job._engine_log = logger.last_errors  # noqa: SLF001
            self._discard_private_cookies(cookies)
            _remove_empty_dir(os.path.join(job.download_dir, PARTIAL_DIR_NAME))

    FRESH_RETRIES_ON_403 = 1

    def _download_with_fresh_retry(self, job, hook, logger, cookies):
        """Sites (notably YouTube) occasionally reject a just-extracted media
        URL with HTTP 403; extracting again yields new, working URLs. Partial
        data is kept (continuedl), so the retry continues where it stopped."""
        attempt = 0
        while True:
            opts = self.build_download_opts(job, hook, logger, cookies)
            try:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    return ydl.extract_info(job.url, download=True)
            except DownloadError as e:
                if "HTTP Error 403" not in str(e) or attempt >= self.FRESH_RETRIES_ON_403 \
                        or job.cancel_event.is_set() or job.pause_event.is_set():
                    raise
                attempt += 1
                log.info("[job %s] HTTP 403 from the media server; extracting fresh URLs and retrying (%s/%s)",
                         job.job_id, attempt, self.FRESH_RETRIES_ON_403)

    def cleanup_cancelled(self, job):
        removed = 0
        for path in list(job.temp_files):
            candidates = {path, f"{path}.part", f"{path}.ytdl"}
            for candidate in candidates:
                lowered = candidate.lower()
                if os.path.isfile(candidate) and (lowered.endswith(_PARTIAL_SUFFIXES) or ".part-frag" in lowered
                                                  or PARTIAL_DIR_NAME in candidate):
                    try:
                        os.remove(candidate)
                        removed += 1
                    except OSError:
                        pass
        job.temp_files.clear()
        _remove_empty_dir(os.path.join(job.download_dir, PARTIAL_DIR_NAME))
        return removed


def _forward_pp_hook(d, progress_hook):
    status = d.get("status")
    if status in ("started", "processing"):
        progress_hook({"status": "processing", "postprocessor": d.get("postprocessor")})


def _collect_filepaths(info):
    paths = []
    if not info:
        return paths
    if info.get("_type") == "playlist":
        for entry in info.get("entries") or []:
            paths.extend(_collect_filepaths(entry))
        return paths
    for rd in info.get("requested_downloads") or []:
        path = rd.get("filepath") or rd.get("_filename")
        if path:
            paths.append(path)
    if not paths and info.get("filepath"):
        paths.append(info["filepath"])
    return paths


def _remove_empty_dir(path):
    try:
        if os.path.isdir(path) and not os.listdir(path):
            os.rmdir(path)
    except OSError:
        pass


# Back-compat alias: the engine used to be YouTube-only.
YouTubeProvider = MediaEngine

_default_engine = MediaEngine()


def detect_playlist(url, limit=1000):
    from . import PROVIDERS
    engine = next((p for p in PROVIDERS if isinstance(p, MediaEngine)), _default_engine)
    return engine.detect_playlist(url, limit=limit)
