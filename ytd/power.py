"""Opt-in power actions after a queue finishes (sleep / hibernate / shutdown).

Only ever triggered by a queue's "When finished" setting the user chose, and
only after the GUI's cancellable countdown (see gui/power_dialog.py). Uses
fixed Windows APIs/commands with no user-supplied arguments.
"""

import ctypes
import subprocess
import sys

from .logging_setup import get_logger

log = get_logger()

ACTION_LABELS = {"none": "Do nothing", "notify": "Show a notification", "exit": "Exit Master Download Manager",
                 "sleep": "Sleep", "hibernate": "Hibernate", "shutdown": "Shut down"}


def perform(action):
    """Returns True if the request was handed to Windows."""
    if sys.platform != "win32":
        log.warning("Power action %s is only supported on Windows", action)
        return False
    log.info("Performing power action: %s", action)
    if action in ("sleep", "hibernate"):
        # SetSuspendState(hibernate, force, wakeupEventsDisabled)
        return bool(ctypes.windll.powrprof.SetSuspendState(action == "hibernate", False, False))
    if action == "shutdown":
        # /t 30 gives Windows' own "you're about to be signed out" notice; shutdown /a cancels it.
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        return subprocess.run(["shutdown", "/s", "/t", "30", "/c", "Master Download Manager: downloads finished"],
                              creationflags=flags, check=False).returncode == 0
    raise ValueError(f"Unknown power action: {action}")
