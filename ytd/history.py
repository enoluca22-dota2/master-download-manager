"""Persistent download history / library (download_history.json).

Each entry is one *finished* download (completed, failed or cancelled):

    {id, title, format, path (folder), file (full path or ""), url, status,
     source, engine, kind, category, file_size, date}

Removing an entry never touches the downloaded file; deleting the file is a
separate, explicit action (see delete_entry_file).
"""

import datetime
import json
import os
import threading
import uuid

from .config import HISTORY_FILE

MAX_HISTORY_ENTRIES = 2000

# Library categories (by file extension of the result).
VIDEO_EXTS = {"mp4", "mkv", "webm", "mov", "avi", "m4v", "flv", "wmv", "mpg", "mpeg", "ts", "3gp", "ogv"}
AUDIO_EXTS = {"mp3", "m4a", "aac", "opus", "ogg", "oga", "flac", "wav", "wma", "alac", "aiff", "mka", "weba"}
ARCHIVE_EXTS = {"zip", "rar", "7z", "tar", "gz", "tgz", "bz2", "xz", "zst", "iso", "img", "dmg", "cab", "lz", "lzma"}
LIBRARY_CATEGORIES = ("All", "Videos", "Audio", "Torrents", "Archives", "Other")


def library_category(entry):
    """Map a history entry (or a job's dict form) to a Library category."""
    if (entry.get("engine") == "torrent") or (entry.get("kind") == "torrent"):
        return "Torrents"
    name = entry.get("file") or ""
    ext = os.path.splitext(name)[1].lstrip(".").lower()
    if ext in VIDEO_EXTS:
        return "Videos"
    if ext in AUDIO_EXTS:
        return "Audio"
    if ext in ARCHIVE_EXTS:
        return "Archives"
    if not ext:
        kind = entry.get("kind")
        if kind == "video":
            return "Videos"
        if kind == "audio":
            return "Audio"
    return "Other"


class HistoryStore:
    def __init__(self, path=HISTORY_FILE):
        self._path = path
        self._lock = threading.Lock()
        self._cache = None  # loaded once; every change is written through atomically

    def _load(self):
        if self._cache is None:
            self._cache = self._read_file()
        return list(self._cache)

    def _read_file(self):
        if os.path.exists(self._path):
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    changed = False
                    for entry in data:
                        if isinstance(entry, dict) and not entry.get("id"):
                            entry["id"] = uuid.uuid4().hex  # entries from older versions
                            changed = True
                    if changed:
                        self._save(data)
                    return [e for e in data if isinstance(e, dict)]
            except (json.JSONDecodeError, OSError):
                pass
        return []

    def _save(self, data):
        self._cache = list(data)
        try:
            os.makedirs(os.path.dirname(os.path.abspath(self._path)), exist_ok=True)
            tmp = f"{self._path}.tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, separators=(",", ":"))
            os.replace(tmp, self._path)
        except OSError:
            pass

    def all(self):
        with self._lock:
            return self._load()

    def add(self, title, fmt, save_path, url="", status="Completed", source="gui", file_size=None,
            engine="", kind="", file_path="", category=None):
        entry = {
            "id": uuid.uuid4().hex,
            "title": title,
            "format": fmt,
            "path": save_path,
            "file": file_path or "",
            "url": url,
            "status": status,
            "source": source,
            "engine": engine or "",
            "kind": kind or "",
            "file_size": file_size,
            "date": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        }
        entry["category"] = category or library_category(entry)
        with self._lock:
            history = self._load()
            history.insert(0, entry)
            history = history[:MAX_HISTORY_ENTRIES]
            self._save(history)
            return entry

    def get(self, entry_id):
        with self._lock:
            return next((h for h in self._load() if h.get("id") == entry_id), None)

    def remove(self, url, date, title):
        """Removes the entry matching (url, date, title) exactly — kept for
        rows identified the way older versions did."""
        with self._lock:
            history = self._load()
            filtered = [
                h for h in history
                if not (h.get("url") == url and h.get("date") == date and h.get("title") == title)
            ]
            removed = len(filtered) != len(history)
            if removed:
                self._save(filtered)
            return removed

    def remove_by_id(self, entry_id):
        with self._lock:
            history = self._load()
            filtered = [h for h in history if h.get("id") != entry_id]
            if len(filtered) != len(history):
                self._save(filtered)
                return True
            return False

    def clear(self):
        with self._lock:
            self._save([])


def delete_entry_file(entry):
    """Explicit, user-confirmed deletion of the downloaded file of a history
    entry. Only deletes a regular file at exactly the recorded path; never a
    directory, never anything else. Returns (ok, message)."""
    path = entry.get("file") or ""
    if not path:
        return False, "This entry has no recorded file."
    if not os.path.isfile(path):
        return False, "The file no longer exists."
    try:
        os.remove(path)
        return True, "File deleted."
    except OSError as e:
        return False, f"Couldn't delete the file: {e}"
