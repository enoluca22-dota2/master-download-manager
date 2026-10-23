"""'Start with Windows' toggle, implemented as a shortcut in the user's
Startup folder — reversible, no registry edits. Points at the packaged .exe
when running frozen, otherwise at pythonw + the source entry script."""

import os
import sys

from .config import APP_ICON_FILE, APP_NAME, INSTALL_DIR

STARTUP_DIR = os.path.join(
    os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Roaming"),
    "Microsoft", "Windows", "Start Menu", "Programs", "Startup",
)
SHORTCUT_PATH = os.path.join(STARTUP_DIR, f"{APP_NAME}.lnk")
LEGACY_SHORTCUTS = [os.path.join(STARTUP_DIR, "Enagrio.lnk")]
APP_SCRIPT = os.path.join(INSTALL_DIR, "master_download_manager.py")


def is_enabled():
    return os.path.isfile(SHORTCUT_PATH)


def launch_command():
    """(target, arguments) used for the shortcut — no shell involved."""
    if getattr(sys, "frozen", False):
        return sys.executable, "--minimized"
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    return (pythonw if os.path.exists(pythonw) else sys.executable), f'"{APP_SCRIPT}" --minimized'


def enable():
    """Returns (success: bool, message: str)."""
    try:
        import win32com.client
    except ImportError:
        return False, "Requires pywin32, which is missing from this installation."

    try:
        os.makedirs(STARTUP_DIR, exist_ok=True)
        shell = win32com.client.Dispatch("WScript.Shell")
        shortcut = shell.CreateShortCut(SHORTCUT_PATH)
        target, arguments = launch_command()
        shortcut.Targetpath = target
        shortcut.Arguments = arguments
        shortcut.WorkingDirectory = INSTALL_DIR
        icon = sys.executable if getattr(sys, "frozen", False) else APP_ICON_FILE
        if os.path.isfile(icon):
            shortcut.IconLocation = icon
        shortcut.save()
        _remove_legacy()
        return True, f"{APP_NAME} will now start with Windows."
    except Exception as e:  # noqa: BLE001
        return False, f"Couldn't create the startup shortcut: {e}"


def disable():
    try:
        if os.path.isfile(SHORTCUT_PATH):
            os.remove(SHORTCUT_PATH)
        _remove_legacy()
        return True, f"{APP_NAME} will no longer start with Windows."
    except OSError as e:
        return False, f"Couldn't remove the startup shortcut: {e}"


def _remove_legacy():
    for path in LEGACY_SHORTCUTS:
        try:
            if os.path.isfile(path):
                os.remove(path)
        except OSError:
            pass
