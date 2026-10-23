"""Persistent, thread-safe settings storage (settings.json)."""

import json
import os
import threading

from .config import DEFAULT_SETTINGS, SETTINGS_FILE, generate_token


class SettingsStore:
    def __init__(self, path=SETTINGS_FILE):
        self._path = path
        self._lock = threading.Lock()
        self._data = self._load()

    def _load(self):
        data = dict(DEFAULT_SETTINGS)
        if os.path.exists(self._path):
            try:
                with open(self._path, "r", encoding="utf-8-sig") as f:  # tolerate a BOM (Notepad, PowerShell)
                    on_disk = json.load(f)
                if isinstance(on_disk, dict):
                    data.update(on_disk)
            except (json.JSONDecodeError, UnicodeDecodeError, OSError):
                # Unreadable: keep a copy before defaults (and a new token) get
                # saved over it, so the user's settings can be recovered.
                try:
                    import shutil
                    import time
                    shutil.copy2(self._path, f"{self._path}.corrupt-{time.strftime('%Y%m%d-%H%M%S')}")
                except OSError:
                    pass
        needs_save = False
        if not data.get("api_token"):
            data["api_token"] = generate_token()
            needs_save = True  # must persist immediately, or a restart before
            # any other setting change would silently mint a new token and
            # break every paired browser extension.
        try:
            os.makedirs(data["download_dir"], exist_ok=True)
        except OSError:
            pass  # e.g. a removable drive that isn't plugged in; the engine re-checks per download
        if needs_save:
            self._data = data
            self._save()
        return data

    def _save(self):
        # Write to a temp file and atomically replace, so a crash or power
        # loss mid-write can never leave a truncated settings.json behind.
        try:
            os.makedirs(os.path.dirname(os.path.abspath(self._path)), exist_ok=True)
            tmp = f"{self._path}.tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2)
            os.replace(tmp, self._path)
        except OSError:
            pass

    def all(self):
        with self._lock:
            return dict(self._data)

    def get(self, key, default=None):
        with self._lock:
            return self._data.get(key, default)

    def set(self, key, value):
        with self._lock:
            self._data[key] = value
            self._save()

    def update(self, values):
        with self._lock:
            self._data.update(values)
            self._save()

    def regenerate_token(self):
        with self._lock:
            token = generate_token()
            self._data["api_token"] = token
            self._save()
            return token
