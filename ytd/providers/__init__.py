from .base import MediaProvider, PauseRequested, UnsupportedMediaError
from .http import HttpEngine
from .media import MediaEngine, YouTubeProvider
from .torrent import TorrentProvider

# Order matters: the first engine whose supports(url) returns True wins.
#   torrent: magnet: / *.torrent
#   media:   site-specific extractor match, or a web page that isn't a file
#   http:    everything else over http(s) (direct files)
PROVIDERS = [TorrentProvider(), MediaEngine(), HttpEngine()]


def configure(settings_store):
    """Give every engine access to the user's settings (proxy, cookies,
    limits, FFmpeg location, ...). Called once by DownloadManager."""
    for provider in PROVIDERS:
        provider.settings = settings_store


def get_provider(name):
    if name == "youtube":
        name = "media"
    return next((p for p in PROVIDERS if p.name == name), None)


def find_provider(url, engine=None):
    if engine:
        provider = get_provider(engine)
        if provider is not None and provider.supports(url):
            return provider
        return None
    for provider in PROVIDERS:
        if provider.supports(url):
            return provider
    return None


def resolve_format_selection(url, format_id, engine=None):
    """Generic, engine-agnostic dispatch used by the local API: find whichever
    engine owns this URL and let it interpret the format_id."""
    if format_id in ("file", "http-file"):
        engine = engine or "http"
    if format_id and format_id.startswith("torrent-"):
        engine = engine or "torrent"
    provider = find_provider(url, engine) if engine else find_provider(url)
    if provider is None:
        raise UnsupportedMediaError("This link cannot be downloaded by Master Download Manager.")
    return provider.resolve_format_selection(format_id)


__all__ = [
    "MediaProvider", "PauseRequested", "UnsupportedMediaError",
    "MediaEngine", "YouTubeProvider", "TorrentProvider", "HttpEngine", "PROVIDERS",
    "configure", "get_provider", "find_provider", "resolve_format_selection",
]
