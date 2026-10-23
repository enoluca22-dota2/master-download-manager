"""Small GUI helpers shared by the main window and dialogs."""

import datetime
import os
import subprocess
import sys
from tkinter import messagebox

from ..files import is_executable_file
from ..models import (
    STATUS_CANCELLED, STATUS_COMPLETED, STATUS_DOWNLOADING, STATUS_FAILED, STATUS_PAUSED, STATUS_PREPARING,
    STATUS_PROCESSING, STATUS_QUEUED, STATUS_RETRYING, STATUS_SEEDING,
)

STATUS_ICON = {
    STATUS_QUEUED: "◌",
    STATUS_PREPARING: "◔",
    STATUS_DOWNLOADING: "▶",
    STATUS_PROCESSING: "⚙",
    STATUS_SEEDING: "⇅",
    STATUS_PAUSED: "⏸",
    STATUS_RETRYING: "↻",
    STATUS_COMPLETED: "✓",
    STATUS_CANCELLED: "■",
    STATUS_FAILED: "✕",
}

ENGINE_LABEL = {"media": "Media", "torrent": "Torrent", "http": "HTTP", "youtube": "Media"}


def truncate(text, max_len=64):
    text = text or ""
    return text if len(text) <= max_len else text[: max_len - 1] + "…"


def human_size(num_bytes, unknown="Unknown"):
    if not num_bytes:
        return unknown
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def human_duration(seconds):
    if seconds is None:
        return ""
    seconds = int(round(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def parse_timestamp(text):
    """'1:02:03' / '62:03' / '75' / '' -> seconds (float) or None. ValueError on garbage."""
    text = (text or "").strip()
    if not text:
        return None
    parts = text.split(":")
    if len(parts) > 3 or not all(p.strip().replace(".", "", 1).isdigit() for p in parts):
        raise ValueError(f"'{text}' isn't a time like 1:23 or 1:02:03")
    total = 0.0
    for p in parts:
        total = total * 60 + float(p)
    return total


def progress_text(job):
    if job.status == STATUS_COMPLETED:
        pct = 100.0
    elif job.status in (STATUS_DOWNLOADING, STATUS_PAUSED, STATUS_PROCESSING, STATUS_RETRYING, STATUS_SEEDING) \
            or job.progress:
        pct = job.progress or 0.0
    else:
        return "—"
    filled = int(round(pct / 12.5))
    return f"{'▰' * filled}{'▱' * (8 - filled)}  {pct:.0f}%"


def size_text(job):
    total = job.total_bytes
    done = job.downloaded_bytes
    if job.status == STATUS_COMPLETED:
        return human_size(total or done, "—")
    if total and done:
        return f"{human_size(done)} / {human_size(total)}"
    if total:
        return human_size(total)
    if done:
        return human_size(done)
    return "—"


def job_date_text(job):
    if job.from_history and getattr(job, "history_date", None):
        return job.history_date
    try:
        return datetime.datetime.fromtimestamp(job.created_at).strftime("%Y-%m-%d %H:%M")
    except (OSError, OverflowError, ValueError):
        return ""


def open_folder(path, select=None):
    """Open a folder in Explorer, optionally highlighting a file in it."""
    if select and os.path.exists(select) and sys.platform == "win32":
        subprocess.Popen(["explorer", f"/select,{os.path.normpath(select)}"])  # list args: no shell
        return True
    if not path:
        return False
    if not os.path.isdir(path):
        messagebox.showinfo("Folder not found", f"This folder doesn't exist anymore:\n{path}")
        return False
    if sys.platform == "win32":
        os.startfile(path)  # noqa: S606 - a directory, opened in Explorer
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])
    return True


def open_file(path, parent=None):
    """Open a downloaded file with its default program. Downloaded files are
    untrusted: anything that would *run* asks first."""
    if not path or not os.path.exists(path):
        messagebox.showinfo("File not found", "The downloaded file isn't there anymore.", parent=parent)
        return False
    if os.path.isdir(path):
        return open_folder(path)
    if is_executable_file(path):
        ok = messagebox.askyesno(
            "Run a program?",
            f"“{os.path.basename(path)}” is a program or script. Opening it will run it on your computer.\n\n"
            "Only continue if you trust where it came from.\n\nRun it now?",
            icon="warning", default="no", parent=parent)
        if not ok:
            return False
    if sys.platform == "win32":
        os.startfile(path)  # noqa: S606 - user-initiated, confirmed above for executables
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])
    return True
