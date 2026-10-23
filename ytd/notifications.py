"""Windows toast notifications, with a safe no-op fallback off Windows or
when the optional dependency isn't installed.

IMPORTANT — DLL load order: win11toast pulls in several `winrt-*` packages,
which each bundle their own copy of the MSVC++ runtime DLLs (MSVCP140.dll
etc). libtorrent's compiled extension needs a *different* copy of the same
DLL name, and Windows keeps whichever one a process loads first for the
rest of that process's life. Importing win11toast at module load time (i.e.
before the app ever touches libtorrent) was reproduced to reliably crash
the whole app — not just on rare torrents, but confirmed with a real
17-tracker magnet link — the first time a torrent operation ran, with
Windows Event Viewer pointing at the winrt package's MSVCP140.dll (access
violation). Importing win11toast lazily, only when a notification is
actually about to be shown, avoids this in virtually all real usage: by
the time any download (let alone a notification about one) has happened,
something has already imported ytd.providers and loaded libtorrent's copy
first. youtube_downloader.py additionally imports libtorrent explicitly
before anything else, as a second, independent guarantee of the same
ordering — see the comment there.
"""

import platform

from .logging_setup import get_logger

log = get_logger()

_checked = False
_available = False
_win_notify = None


def _ensure_checked():
    global _checked, _available, _win_notify
    if _checked:
        return
    _checked = True
    if platform.system() != "Windows":
        return
    try:
        from win11toast import notify as win_notify
        _win_notify = win_notify
        _available = True
    except ImportError:
        _available = False


def notify(title, message, on_click=None):
    """Fire-and-forget a Windows toast. Never raises — a notification
    failure must never interrupt a download."""
    _ensure_checked()
    if not _available:
        return
    try:
        import threading
        threading.Thread(
            target=_win_notify,
            kwargs={"title": title, "body": message, "on_click": on_click} if on_click else {"title": title, "body": message},
            daemon=True,
        ).start()
    except Exception:
        log.exception("Failed to show notification")


def notifications_supported():
    _ensure_checked()
    return _available
