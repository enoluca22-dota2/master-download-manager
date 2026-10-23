"""
Master Download Manager
-----------------------
One app for media sites (video/audio/playlists), direct file downloads and
BitTorrent (magnet links and .torrent files), with a browser extension that
sends links to it.

Run from source:
    python master_download_manager.py [URL | magnet-link | path\\to\\file.torrent] [--minimized]

Only download content you have the right to download. DRM-protected media
is not supported and is never circumvented.
"""

import ctypes
import json
import os
import sys

# Load libtorrent before anything that might import win11toast: both ship
# their own MSVCP140.dll and Windows keeps whichever loads first for the life
# of the process. The winrt copy crashes libtorrent (reproduced, confirmed in
# Event Viewer), so libtorrent's copy must win. See ytd/notifications.py.
try:
    import libtorrent  # noqa: F401
except ImportError:
    pass

_MUTEX_NAME = "Local\\MasterDownloadManager.SingleInstance"
_ERROR_ALREADY_EXISTS = 183
_mutex_handle = None


def _another_instance_running():
    """Holds a named mutex for the life of the process; True if it already existed."""
    global _mutex_handle
    if sys.platform != "win32":
        return False
    kernel32 = ctypes.windll.kernel32
    _mutex_handle = kernel32.CreateMutexW(None, False, _MUTEX_NAME)
    return kernel32.GetLastError() == _ERROR_ALREADY_EXISTS


def _forward_to_running_instance(target):
    """Hand a link/.torrent path to the already-running window via its local API."""
    from ytd.config import SETTINGS_FILE
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            settings = json.load(f)
        import requests
        requests.post(f"http://127.0.0.1:{int(settings.get('api_port', 47821))}/app/open",
                      json={"url": target}, headers={"X-MDM-Token": settings.get("api_token", "")}, timeout=5)
        return True
    except Exception:  # noqa: BLE001 - nothing else we can do; the user sees the running window anyway
        return False


def _launch_argument(argv):
    for arg in argv[1:]:
        if not arg.startswith("--"):
            return arg.strip()
    return None


def main():
    target = _launch_argument(sys.argv)
    if _another_instance_running():
        _forward_to_running_instance(target)
        return 0

    from ytd.config import migrate_legacy_data
    copied = migrate_legacy_data()

    from ytd.logging_setup import get_logger, register_secret, setup_logging
    setup_logging()
    log = get_logger()
    from ytd import __version__
    log.info("Master Download Manager %s starting (frozen=%s)", __version__, getattr(sys, "frozen", False))
    if copied:
        log.info("Imported settings/history from a previous version: %s", ", ".join(copied))

    try:
        import customtkinter as ctk
    except ImportError:
        raise SystemExit("customtkinter is missing. Run: pip install -r requirements.txt")

    from tkinter import messagebox

    from ytd.gui.app import MasterDownloadManagerApp

    root = ctk.CTk()

    def report_callback_exception(exc_type, exc, tb):
        log.error("Unhandled UI error", exc_info=(exc_type, exc, tb))
        messagebox.showerror("Something went wrong",
                             f"{exc_type.__name__}: {exc}\n\nThe details were written to the application log "
                             "(Settings → Advanced → Open logs folder).")
    root.report_callback_exception = report_callback_exception

    app = MasterDownloadManagerApp(root)
    register_secret(app.settings.get("api_token"))
    if "--minimized" in sys.argv and app.tray_icon is not None:
        root.withdraw()
    if target:
        root.after(600, app.open_request, target)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
