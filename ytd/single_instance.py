"""Single-instance guard: a named mutex held for the life of the process.
The installer's AppMutex uses the same name, so Setup won't overwrite files
while MDM runs. The updater releases it just before handing over to Setup."""

import ctypes
import hashlib
import os
import sys

MUTEX_NAME = "Local\\MasterDownloadManager.SingleInstance"
_ERROR_ALREADY_EXISTS = 183
_handle = None


def mutex_name():
    name = MUTEX_NAME
    if os.environ.get("MDM_DATA_DIR"):
        # A portable/test profile is a separate instance from the installed app
        # (the installer's AppMutex check only cares about the default name).
        name += "." + hashlib.sha1(os.path.abspath(os.environ["MDM_DATA_DIR"]).lower().encode()).hexdigest()[:12]
    return name


def acquire():
    """True if this is the only instance (and now holds the mutex)."""
    global _handle
    if sys.platform != "win32":
        return True
    kernel32 = ctypes.windll.kernel32
    _handle = kernel32.CreateMutexW(None, False, mutex_name())
    return kernel32.GetLastError() != _ERROR_ALREADY_EXISTS


def release():
    global _handle
    if _handle:
        ctypes.windll.kernel32.CloseHandle(_handle)
        _handle = None
