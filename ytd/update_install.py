"""One-click update installation.

    prepare()   lock -> disk-space check -> download + verify the installer
                (size + SHA-256 from the signed manifest) -> back up settings,
                queues, schedules and history
    launch()    re-verify the installer, write updates/pending.json, start the
                installer in its unattended upgrade mode:
                    /SILENT /SUPPRESSMSGBOXES /NORESTART /MDMUPDATE=1 /WAITPID=<our pid> /LOG=...
                then the caller pauses downloads, saves state and exits.
    The installer (a separate process) waits for MDM to exit, upgrades in
    place (Inno Setup rolls its own changes back if installing fails) and
    starts MDM again - the new version on success, the old one otherwise.
    post_update_check()  on the next start: compares the running version with
                pending.json, records the outcome in updates/last-update.json,
                restores settings from the backup if they were damaged, and
                reports success or failure to the user.

Only updates whose signed manifest verified (UpdateInfo.verified) are ever
installed this way; everything is user-initiated.
"""

import datetime
import json
import os
import shutil
import sqlite3
import subprocess
import time

from . import __version__
from .config import DATA_DIR
from .logging_setup import get_logger
from .updater import UpdateError, download_update, file_sha256

log = get_logger()

UPDATES_DIR = os.path.join(DATA_DIR, "updates")
PENDING_FILE = os.path.join(UPDATES_DIR, "pending.json")
RESULT_FILE = os.path.join(UPDATES_DIR, "last-update.json")
LOCK_FILE = os.path.join(UPDATES_DIR, "update.lock")
CRITICAL_FILES = ("settings.json", "queues.json", "scheduler.json", "queue.json", "download_history.json")
KEEP_BACKUPS = 3


def _now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def _pid_alive(pid):
    if not pid:
        return False
    import ctypes
    handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    code = ctypes.c_ulong()
    ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
    ctypes.windll.kernel32.CloseHandle(handle)
    return code.value == 259  # STILL_ACTIVE


class UpdateLock:
    """Prevents two update attempts at once (two windows, a double click)."""

    def __enter__(self):
        os.makedirs(UPDATES_DIR, exist_ok=True)
        if os.path.exists(LOCK_FILE):
            try:
                with open(LOCK_FILE, encoding="utf-8") as f:
                    pid = int(json.load(f).get("pid", 0))
            except (OSError, ValueError):
                pid = 0
            if _pid_alive(pid) and pid != os.getpid():
                raise UpdateError("Another update is already in progress.")
        with open(LOCK_FILE, "w", encoding="utf-8") as f:
            json.dump({"pid": os.getpid(), "since": _now()}, f)
        return self

    def __exit__(self, *exc):
        try:
            os.remove(LOCK_FILE)
        except OSError:
            pass


def check_disk_space(size, install_dir):
    """Room for the download plus an unpacked copy of the program."""
    need_data = (size or 150_000_000) + 50_000_000
    need_install = (size or 150_000_000) * 4
    os.makedirs(UPDATES_DIR, exist_ok=True)
    for path, need, what in ((UPDATES_DIR, need_data, "the update download"),
                             (install_dir, need_install, "installing the update")):
        try:
            free = shutil.disk_usage(path).free
        except OSError:
            continue
        if free < need:
            raise UpdateError(f"Not enough free disk space for {what} ({need // 1_048_576} MB needed, "
                              f"{free // 1_048_576} MB free on {os.path.splitdrive(path)[0] or path}).")


def backup_state(data_dir=DATA_DIR, label=""):
    """Copies the critical state into updates/backup-<version>-<time>/ (SQLite via its backup API)."""
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = os.path.join(data_dir, "updates", f"backup-{__version__}-{stamp}{label}")
    os.makedirs(dest, exist_ok=True)
    for name in CRITICAL_FILES:
        src = os.path.join(data_dir, name)
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(dest, name))
    db = os.path.join(data_dir, "download_history.sqlite3")
    if os.path.isfile(db):
        src = sqlite3.connect(db)
        out = sqlite3.connect(os.path.join(dest, "download_history.sqlite3"))
        with out:
            src.backup(out)
        src.close()
        out.close()
    _prune_backups(data_dir)
    return dest


def _prune_backups(data_dir):
    root = os.path.join(data_dir, "updates")
    backups = sorted(d for d in os.listdir(root) if d.startswith("backup-"))
    for old in backups[:-KEEP_BACKUPS]:
        shutil.rmtree(os.path.join(root, old), ignore_errors=True)


def prepare(info, install_dir, progress=None, cancel_event=None):
    """Download and verify; returns (installer_path, backup_dir)."""
    if not info.verified:
        raise UpdateError(info.reason or "This update isn't verified and can't be installed automatically.")
    with UpdateLock():
        check_disk_space(info.size, install_dir)
        dest = os.path.join(UPDATES_DIR, info.version)
        path = download_update(info.manifest(), dest, progress=progress, cancel_event=cancel_event)
        backup = backup_state()
    log.info("Update %s ready: %s (backup %s)", info.version, path, backup)
    return path, backup


def launch(info, installer, backup_dir):
    """Start the installer's unattended upgrade. The caller must exit promptly."""
    if file_sha256(installer) != info.sha256 or os.path.getsize(installer) != info.size:
        raise UpdateError("The downloaded installer changed after it was verified; refusing to run it.")
    log_path = os.path.join(UPDATES_DIR, f"install-{info.version}.log")
    pending = {"from": __version__, "to": info.version, "installer": installer, "sha256": info.sha256,
               "backup": backup_dir, "log": log_path, "started": _now(), "pid": os.getpid()}
    with open(PENDING_FILE, "w", encoding="utf-8") as f:
        json.dump(pending, f, indent=1)
    args = [installer, "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-", "/MDMUPDATE=1",
            f"/WAITPID={os.getpid()}", f"/LOG={log_path}"]
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen(args, close_fds=True, creationflags=flags)
    log.info("Update installer started for %s -> %s", __version__, info.version)
    return pending


def post_update_check(settings_path=None):
    """Called at startup. Returns None, or a dict describing the finished update:
    {"ok": bool, "from", "to", "running", "message", "log"}."""
    if not os.path.isfile(PENDING_FILE):
        return None
    try:
        with open(PENDING_FILE, encoding="utf-8") as f:
            pending = json.load(f)
    except (OSError, ValueError):
        os.remove(PENDING_FILE)
        return None
    if _pid_alive(pending.get("pid")) and pending.get("pid") != os.getpid():
        return None  # the old instance is still shutting down
    ok = __version__ == pending.get("to")
    restored = _restore_damaged_settings(pending.get("backup"), settings_path)
    if ok:
        message = f"Master Download Manager was updated from {pending.get('from')} to {__version__}."
    elif __version__ == pending.get("from"):
        message = (f"The update to {pending.get('to')} didn't complete; version {__version__} is still installed and "
                   "your data is unchanged. Details are in the installer log.")
    else:
        message = f"Expected version {pending.get('to')} after the update, but {__version__} is running."
    if restored:
        message += " Your settings were restored from the backup taken before the update."
    result = dict(pending, ok=ok, running=__version__, finished=_now(), message=message,
                  restored_settings=restored)
    with open(RESULT_FILE, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=1)
    try:
        os.remove(PENDING_FILE)
    except OSError:
        pass
    if ok:
        shutil.rmtree(os.path.join(UPDATES_DIR, str(pending.get("to"))), ignore_errors=True)  # the used installer
    log.info("Update result: %s", message)
    return result


def _restore_damaged_settings(backup_dir, settings_path=None):
    settings_path = settings_path or os.path.join(DATA_DIR, "settings.json")
    backup = os.path.join(backup_dir or "", "settings.json")
    if not backup_dir or not os.path.isfile(backup):
        return False
    try:
        with open(settings_path, encoding="utf-8-sig") as f:
            if isinstance(json.load(f), dict):
                return False
    except (OSError, ValueError):
        pass
    shutil.copy2(backup, settings_path)
    return True


def wait_for_exit(pid, timeout=60):
    end = time.time() + timeout
    while time.time() < end and _pid_alive(pid):
        time.sleep(0.25)
    return not _pid_alive(pid)
