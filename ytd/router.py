"""Automatic engine detection.

    magnet:? / *.torrent (file or URL) / application/x-bittorrent  -> torrent
    URL handled by a site-specific media extractor                    -> media
    URL whose path is a file (.zip .iso .exe .mp4 ...)                -> http
    anything else: probe the server (one HEAD/GET, no body read)
        HTML page / streaming manifest                                -> media (generic page scan)
        a file (attachment / binary content type)                     -> http

`classify()` never touches the network; `detect_engine()` may probe.
"""

import os
import re
from urllib.parse import unquote, urlparse

from .logging_setup import get_logger

log = get_logger("engine")

DIRECT_FILE_EXTS = {
    # archives & disk images
    "zip", "rar", "7z", "tar", "gz", "tgz", "bz2", "tbz2", "xz", "txz", "zst", "lz", "lzma", "cab", "iso", "img",
    "dmg", "vhd", "vhdx", "wim",
    # installers & executables
    "exe", "msi", "msix", "msixbundle", "appx", "appxbundle", "apk", "xapk", "deb", "rpm", "pkg", "appimage", "run",
    "bin", "jar",
    # documents
    "pdf", "epub", "mobi", "djvu", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "odt", "ods", "odp", "rtf", "txt",
    "csv", "json", "xml",
    # media files (a direct link to the file itself)
    "mp4", "mkv", "webm", "avi", "mov", "wmv", "flv", "m4v", "mpg", "mpeg", "ts", "3gp", "mp3", "m4a", "aac",
    "flac", "wav", "ogg", "oga", "opus", "wma", "aiff",
    # images & fonts
    "jpg", "jpeg", "png", "gif", "webp", "bmp", "tif", "tiff", "svg", "ico", "psd", "ttf", "otf", "woff", "woff2",
    # data / misc
    "db", "sqlite", "dat", "torrent", "sig", "asc", "sha256", "md5",
}

_HTML_TYPES = ("text/html", "application/xhtml+xml")
_MANIFEST_TYPES = ("application/vnd.apple.mpegurl", "application/x-mpegurl", "application/dash+xml", "audio/mpegurl")


def url_path_ext(url):
    try:
        path = unquote(urlparse(url).path or "")
    except ValueError:
        return ""
    return os.path.splitext(path)[1].lstrip(".").lower()


def looks_like_direct_file(url):
    return url_path_ext(url) in DIRECT_FILE_EXTS


def is_torrent_source(url):
    from .providers.torrent import is_torrent_source as _is_torrent
    return _is_torrent(url)


def classify(url):
    """Best guess without network access: 'torrent' | 'media' | 'http' | None."""
    url = (url or "").strip()
    if not url:
        return None
    if is_torrent_source(url):
        return "torrent"
    if not re.match(r"^https?://", url, re.IGNORECASE):
        return None
    from .providers.media import matching_extractor
    if matching_extractor(url):
        return "media"
    if looks_like_direct_file(url):
        return "http"
    return "media"


def detect_engine(url, settings=None, probe=True):
    """Returns (engine_name, reason). May issue one network request for URLs
    that are neither a known media site nor an obvious file."""
    url = (url or "").strip()
    engine = classify(url)
    if engine is None:
        return None, "Not a web address, magnet link or .torrent file."
    if engine == "torrent":
        return "torrent", "Magnet link or .torrent"
    from .providers.media import matching_extractor
    extractor = matching_extractor(url)
    if extractor:
        return "media", f"Supported site ({extractor})"
    if engine == "http":
        return "http", f"Direct file (.{url_path_ext(url)})"
    if not probe:
        return "media", "Web page (will be scanned for media)"

    from .providers.http import probe_url
    try:
        info = probe_url(url, settings)
    except Exception as e:  # noqa: BLE001 - probing is best-effort
        log.info("Probe failed for engine detection (%s); defaulting to media scan", e)
        return "media", "Web page (will be scanned for media)"
    ctype = (info.get("content_type") or "").lower()
    if "bittorrent" in ctype:
        return "torrent", "Server sent a .torrent file"
    if info.get("attachment") or not ctype:
        return "http", "Server offers a file download"
    if ctype.startswith(_HTML_TYPES):
        return "media", "Web page (will be scanned for media)"
    if ctype.startswith(_MANIFEST_TYPES):
        return "media", "Streaming playlist (HLS/DASH)"
    return "http", f"Direct file ({ctype.split(';')[0]})"


def default_selection(engine, settings):
    """The user's default choice for a link sent without picking a format
    (browser 'send link', clipboard quick-add, playlists). Returns
    (format_id, category, options)."""
    if engine == "torrent":
        return "torrent-all", "Torrent", {}
    if engine == "http":
        return "file", None, {}
    mode = settings.get("default_mode", "Video") if settings else "Video"
    if mode in ("MP3", "Audio"):
        audio_format = (settings.get("default_audio_format", "mp3") if settings else "mp3") or "mp3"
        kbps = str(settings.get("default_audio_quality", "192 kbps") if settings else "192").split()[0]
        if audio_format == "mp3":
            return f"mp3-{kbps if kbps.isdigit() else 192}", "Music", {}
        if audio_format == "original":
            return "source-audio", "Music", {}
        return f"audio-{audio_format}", "Music", {}
    from .providers.media import RESOLUTION_LABELS
    quality = settings.get("default_quality", "720p (HD)") if settings else "720p (HD)"
    if quality in ("Best available", "best"):
        return "video-best", "Video", {}
    return f"video-{RESOLUTION_LABELS.get(quality, 720)}", "Video", {}
