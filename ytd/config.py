"""Paths, identity and defaults shared across the GUI, engines and local API."""

import os
import secrets
import shutil
import sys

APP_NAME = "Master Download Manager"
APP_SHORT_NAME = "MDM"
APP_ID = "MasterDownloadManager"  # folder/registry-safe identifier


def _detect_install_dir():
    """Directory holding the program itself: next to the .exe when frozen by
    PyInstaller, the project root when running from source."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _detect_resource_dir():
    """Read-only bundled resources (icons, licenses). PyInstaller puts data
    files under sys._MEIPASS (the _internal folder in onedir builds)."""
    return getattr(sys, "_MEIPASS", _detect_install_dir())


def _detect_data_dir():
    """Per-user, writable application data. Settings/history/queue/logs must
    not live beside the .exe: an installed copy under Program Files isn't
    writable by a normal user. MDM_DATA_DIR overrides (portable use, tests)."""
    override = os.environ.get("MDM_DATA_DIR")
    if override:
        return override
    base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
    return os.path.join(base, APP_ID)


INSTALL_DIR = _detect_install_dir()
RESOURCE_DIR = _detect_resource_dir()
DATA_DIR = _detect_data_dir()
LOG_DIR = os.path.join(DATA_DIR, "logs")
CACHE_DIR = os.path.join(DATA_DIR, "cache")

HISTORY_FILE = os.path.join(DATA_DIR, "download_history.json")
SETTINGS_FILE = os.path.join(DATA_DIR, "settings.json")
QUEUE_FILE = os.path.join(DATA_DIR, "queue.json")
LOG_FILE = os.path.join(LOG_DIR, "app.log")

APP_ICON_FILE = os.path.join(RESOURCE_DIR, "mdm.ico")
LICENSES_DIR = os.path.join(RESOURCE_DIR, "licenses")
NOTICES_FILE = os.path.join(RESOURCE_DIR, "THIRD_PARTY_NOTICES.md")

# Bundled executables (ffmpeg/ffprobe/deno). In the packaged app they live in
# <install>/tools; from source they're staged into <project>/vendor by
# tools/fetch_tools.py.
TOOLS_DIR = os.path.join(INSTALL_DIR, "tools") if getattr(sys, "frozen", False) \
    else os.path.join(INSTALL_DIR, "vendor")

DEFAULT_DOWNLOAD_DIR = os.path.join(os.path.expanduser("~"), "Downloads", APP_NAME)

DEFAULT_API_PORT = 47821
MIN_FREE_SPACE_BYTES = 100 * 1024 * 1024  # refuse to start below ~100 MB free

DEFAULT_SETTINGS = {
    # General
    "appearance_mode": "Dark",
    "reduce_motion": False,
    "minimize_to_tray": True,
    "start_with_windows": False,
    "notifications_enabled": True,
    "clipboard_monitor_enabled": False,
    # Downloads
    "download_dir": DEFAULT_DOWNLOAD_DIR,
    "video_dir": "",
    "audio_dir": "",
    "torrent_dir": "",
    "ask_where_to_save": False,
    "max_concurrent_downloads": 3,
    "max_retries": 3,
    "filename_template": "%(title)s [%(id)s]",
    "restore_queue_on_start": True,
    # Media
    "default_mode": "Video",
    "default_quality": "720p (HD)",
    "default_container": "mp4",
    "prefer_h264": False,
    "default_audio_quality": "192 kbps",
    "default_audio_format": "mp3",
    "embed_metadata": True,
    "embed_thumbnail": False,
    "embed_chapters": True,
    # Subtitles
    "subtitle_languages": "en",
    "subtitle_format": "srt",
    "subtitle_include_auto": False,
    "subtitle_embed": False,
    # Network
    "speed_limit_kbps": 0,  # 0 = unlimited (applies per download)
    "download_timeout": 30,
    "proxy": "",
    "concurrent_fragments": 8,
    "http_connections": 4,
    # Torrent
    "torrent_max_connections": 200,
    "torrent_download_limit_kbps": 0,
    "torrent_upload_limit_kbps": 0,
    "torrent_seed_ratio": 0.0,  # 0 = stop as soon as the download completes
    "torrent_listen_port": 0,  # 0 = let the OS choose
    # Authentication / cookies (stored locally only; never logged or uploaded)
    "cookies_mode": "none",  # none | browser | file
    "cookies_browser": "firefox",
    "cookies_file": "",
    # Browser integration / local API
    "api_port": DEFAULT_API_PORT,
    "api_token": "",
    "api_enabled": True,
    # Advanced
    "ffmpeg_path": "",
    # Updates
    "update_manifest_url": "",
}


def generate_token():
    return secrets.token_hex(24)


def _legacy_data_dirs():
    """Where older versions (YTD / Enagrio) kept settings & history: beside the
    script or the .exe."""
    yield INSTALL_DIR


def migrate_legacy_data(data_dir=None):
    """One-time copy of settings/history from an older install so upgrading
    keeps the user's folders, defaults, history and the API token their
    browser extension is already paired with. Never moves or deletes the
    originals. Returns the list of files copied."""
    data_dir = data_dir or DATA_DIR
    os.makedirs(data_dir, exist_ok=True)
    copied = []
    for legacy in _legacy_data_dirs():
        if os.path.normcase(os.path.abspath(legacy)) == os.path.normcase(os.path.abspath(data_dir)):
            continue
        for name in ("settings.json", "download_history.json"):
            src = os.path.join(legacy, name)
            dst = os.path.join(data_dir, name)
            if os.path.isfile(src) and not os.path.exists(dst):
                try:
                    shutil.copy2(src, dst)
                    copied.append(name)
                except OSError:
                    pass
    return copied
