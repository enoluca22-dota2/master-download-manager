"""Persistent download history / library (SQLite).

Each entry is one *finished* download (completed, failed or cancelled):

    {id, title, format, path (folder), file (full path or ""), url, status,
     source, engine, kind, category, file_size, date}

Stored in an SQLite database next to the old JSON file
(download_history.json -> download_history.sqlite3): indexed search and
filters stay instant with tens of thousands of entries, and every change is
a small transaction instead of rewriting the whole file.

Migration: the first time the database is created, entries from the JSON
file written by MDM 3.x (and YTD/Enagrio before it) are imported in their
original order. The JSON file itself is left untouched as a backup.

Removing an entry never touches the downloaded file; deleting the file is a
separate, explicit action (see delete_entry_file).
"""

import csv
import datetime
import json
import os
import sqlite3
import threading
import uuid

from .config import HISTORY_FILE

MAX_HISTORY_ENTRIES = 100_000  # oldest entries beyond this are pruned

# Library categories (by file extension of the result).
VIDEO_EXTS = {"mp4", "mkv", "webm", "mov", "avi", "m4v", "flv", "wmv", "mpg", "mpeg", "ts", "3gp", "ogv"}
AUDIO_EXTS = {"mp3", "m4a", "aac", "opus", "ogg", "oga", "flac", "wav", "wma", "alac", "aiff", "mka", "weba"}
ARCHIVE_EXTS = {"zip", "rar", "7z", "tar", "gz", "tgz", "bz2", "xz", "zst", "iso", "img", "dmg", "cab", "lz", "lzma"}
LIBRARY_CATEGORIES = ("All", "Videos", "Audio", "Torrents", "Archives", "Other")

FIELDS = ("id", "title", "format", "path", "file", "url", "status", "source", "engine", "kind", "category",
          "file_size", "date")
EXPORT_FORMATS = ("json", "csv")


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


def _now_text():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M")


def _clean(entry):
    """Normalise an entry from any source (old JSON, import file) to FIELDS."""
    out = {k: entry.get(k) for k in FIELDS}
    for k in FIELDS:
        if k != "file_size" and out[k] is None:
            out[k] = ""
    out["id"] = str(out["id"] or uuid.uuid4().hex)
    try:
        out["file_size"] = int(out["file_size"]) if out["file_size"] not in (None, "") else None
    except (TypeError, ValueError):
        out["file_size"] = None
    out["status"] = out["status"] or "Completed"
    out["date"] = out["date"] or _now_text()
    out["category"] = out["category"] or library_category(out)
    for k in ("title", "url", "path", "file", "format"):
        out[k] = str(out[k])[:4000]
    return out


class HistoryStore:
    def __init__(self, path=HISTORY_FILE, db_path=None):
        self._json_path = path
        self._db_path = db_path or (os.path.splitext(path)[0] + ".sqlite3")
        self._lock = threading.RLock()
        self._db = None

    # ------------------------------------------------------------ storage --- #
    def _conn(self):
        if self._db is None:
            os.makedirs(os.path.dirname(os.path.abspath(self._db_path)), exist_ok=True)
            db = sqlite3.connect(self._db_path, check_same_thread=False, timeout=10)
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=NORMAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
                CREATE TABLE IF NOT EXISTS history (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    id TEXT UNIQUE NOT NULL, title TEXT, format TEXT, path TEXT, file TEXT, url TEXT,
                    status TEXT, source TEXT, engine TEXT, kind TEXT, category TEXT, file_size INTEGER, date TEXT);
                CREATE INDEX IF NOT EXISTS ix_history_url ON history(url);
                CREATE INDEX IF NOT EXISTS ix_history_status ON history(status);
                CREATE INDEX IF NOT EXISTS ix_history_category ON history(category);
                CREATE INDEX IF NOT EXISTS ix_history_date ON history(date);
            """)
            self._db = db
            self._migrate_json()
        return self._db

    def _migrate_json(self):
        db = self._db
        if db.execute("SELECT value FROM meta WHERE key='json_migrated'").fetchone():
            return
        imported = 0
        if self._json_path and os.path.isfile(self._json_path):
            try:
                with open(self._json_path, "r", encoding="utf-8-sig") as f:
                    data = json.load(f)
            except (OSError, ValueError):
                data = []
            if isinstance(data, list):
                rows = [_clean(e) for e in reversed(data) if isinstance(e, dict)]  # JSON was newest-first
                imported = self._insert_many(rows)
        with db:
            db.execute("INSERT OR REPLACE INTO meta VALUES ('json_migrated', ?)", (f"{_now_text()} ({imported})",))

    def _insert_many(self, rows):
        if not rows:
            return 0
        with self._db:
            cur = self._db.executemany(
                f"INSERT OR IGNORE INTO history ({', '.join(FIELDS)}) VALUES ({', '.join('?' * len(FIELDS))})",
                [tuple(r[k] for k in FIELDS) for r in rows])
        self._prune()
        return cur.rowcount if cur.rowcount is not None else len(rows)

    def _prune(self):
        with self._db:
            self._db.execute("DELETE FROM history WHERE seq NOT IN "
                             "(SELECT seq FROM history ORDER BY seq DESC LIMIT ?)", (MAX_HISTORY_ENTRIES,))

    @staticmethod
    def _row(row):
        return {k: row[k] for k in FIELDS}

    def close(self):
        with self._lock:
            if self._db is not None:
                self._db.close()
                self._db = None

    # ------------------------------------------------------------- queries --- #
    def all(self):
        with self._lock:
            return [self._row(r) for r in self._conn().execute("SELECT * FROM history ORDER BY seq DESC")]

    def count(self):
        with self._lock:
            return self._conn().execute("SELECT COUNT(*) FROM history").fetchone()[0]

    def search(self, query="", status=None, category=None, since=None, until=None, limit=500, offset=0):
        """Filtered, newest-first page. since/until are 'YYYY-MM-DD' strings."""
        where, args = [], []
        if query:
            like = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            where.append("(title LIKE ? ESCAPE '\\' OR url LIKE ? ESCAPE '\\' OR file LIKE ? ESCAPE '\\')")
            args += [like, like, like]
        if status:
            where.append("status = ?")
            args.append(status)
        if category:
            where.append("category = ?")
            args.append(category)
        if since:
            where.append("date >= ?")
            args.append(since)
        if until:
            where.append("date < ?")
            args.append(until)
        sql = "SELECT * FROM history" + (" WHERE " + " AND ".join(where) if where else "")
        sql += " ORDER BY seq DESC LIMIT ? OFFSET ?"
        with self._lock:
            return [self._row(r) for r in self._conn().execute(sql, (*args, int(limit), int(offset)))]

    def find_by_url(self, url, statuses=("Completed",)):
        """Earlier downloads of the same link (duplicate warning)."""
        with self._lock:
            marks = ",".join("?" * len(statuses))
            rows = self._conn().execute(
                f"SELECT * FROM history WHERE url = ? AND status IN ({marks}) ORDER BY seq DESC LIMIT 5",
                (url, *statuses))
            return [self._row(r) for r in rows]

    def get(self, entry_id):
        with self._lock:
            row = self._conn().execute("SELECT * FROM history WHERE id = ?", (entry_id,)).fetchone()
            return self._row(row) if row else None

    # ----------------------------------------------------------- mutations --- #
    def add(self, title, fmt, save_path, url="", status="Completed", source="gui", file_size=None,
            engine="", kind="", file_path="", category=None):
        entry = _clean({
            "id": uuid.uuid4().hex, "title": title, "format": fmt, "path": save_path, "file": file_path or "",
            "url": url, "status": status, "source": source, "engine": engine or "", "kind": kind or "",
            "file_size": file_size, "date": _now_text(), "category": category,
        })
        with self._lock:
            self._conn()
            self._insert_many([entry])
        return entry

    def remove(self, url, date, title):
        """Removes the entry matching (url, date, title) exactly — kept for
        rows identified the way older versions did."""
        with self._lock:
            db = self._conn()
            with db:
                cur = db.execute("DELETE FROM history WHERE url = ? AND date = ? AND title = ?", (url, date, title))
            return cur.rowcount > 0

    def remove_by_id(self, entry_id):
        with self._lock:
            db = self._conn()
            with db:
                cur = db.execute("DELETE FROM history WHERE id = ?", (entry_id,))
            return cur.rowcount > 0

    def clear(self):
        with self._lock:
            db = self._conn()
            with db:
                db.execute("DELETE FROM history")

    # ------------------------------------------------------- export/import --- #
    def export(self, path, fmt=None):
        fmt = (fmt or os.path.splitext(path)[1].lstrip(".") or "json").lower()
        if fmt not in EXPORT_FORMATS:
            raise ValueError(f"Export format must be one of {', '.join(EXPORT_FORMATS)}.")
        entries = self.all()
        tmp = f"{path}.tmp"
        if fmt == "json":
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"app": "Master Download Manager", "version": 1, "history": entries}, f, indent=1)
        else:
            with open(tmp, "w", encoding="utf-8-sig", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=FIELDS)
                writer.writeheader()
                for e in entries:
                    # neutralise spreadsheet formula injection in exported text cells
                    writer.writerow({k: ("'" + v if isinstance(v, str) and v[:1] in ("=", "+", "-", "@") else v)
                                     for k, v in e.items()})
        os.replace(tmp, path)
        return len(entries)

    def import_file(self, path):
        """Import an MDM export (JSON or CSV) or a 3.x download_history.json.
        Entries already present (same id) are skipped. Returns how many were added."""
        ext = os.path.splitext(path)[1].lower()
        if os.path.getsize(path) > 200 * 1024 * 1024:
            raise ValueError("That file is too large to be a history export.")
        if ext == ".csv":
            with open(path, "r", encoding="utf-8-sig", newline="") as f:
                rows = list(csv.DictReader(f))
            for r in rows:
                for k, v in r.items():
                    if isinstance(v, str) and v.startswith("'") and v[1:2] in ("=", "+", "-", "@"):
                        r[k] = v[1:]
        else:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            rows = data.get("history") if isinstance(data, dict) else data
            if not isinstance(rows, list):
                raise ValueError("That file doesn't contain download history.")
        rows = [_clean(r) for r in reversed(rows) if isinstance(r, dict)]  # exports are newest-first
        with self._lock:
            self._conn()
            before = self.count()
            self._insert_many(rows)
            return self.count() - before


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
