"""Small windows behind the File / Tools menus: Add Multiple URLs, Check File
Integrity, Download History, and import/export of download lists and settings."""

import datetime
import json
import os
import threading
import tkinter as tk
from tkinter import filedialog, messagebox

import customtkinter as ctk

from ..config import DEFAULT_SETTINGS
from ..providers.http import CHECKSUM_ALGORITHMS, file_digest, parse_checksum
from ..router import classify
from .common import human_size, open_file, open_folder
from .datagrid import DataGrid
from .theme import COLORS, font, glass_button

# Never exported (secrets / machine-specific) and never imported.
PRIVATE_SETTINGS = {"api_token", "update_last_check", "update_last_status", "update_remind_after",
                    "update_remind_version", "cookies_file"}


# ------------------------------------------------------------ multiple URLs -- #
class MultiUrlDialog:
    def __init__(self, app):
        self.app = app
        win = self.win = ctk.CTkToplevel(app.root)
        win.title("Add Multiple URLs")
        win.geometry("640x420")
        app.apply_glass_window(win)
        ctk.CTkLabel(win, text="Add Multiple URLs", font=font(16, "bold"), anchor="w").pack(fill="x", padx=18,
                                                                                           pady=(16, 2))
        ctk.CTkLabel(win, text="One link per line: file links, media pages, magnet links or .torrent URLs. "
                               "You'll see the list, sizes and duplicates before anything starts.",
                     text_color=COLORS["muted"], font=font(12), anchor="w", wraplength=600,
                     justify="left").pack(fill="x", padx=18)
        self.box = ctk.CTkTextbox(win, font=("Consolas", 11))
        self.box.pack(fill="both", expand=True, padx=18, pady=10)
        try:
            clip = win.clipboard_get()
            if sum(1 for line in clip.splitlines() if classify(line.strip())) >= 1:
                self.box.insert("1.0", clip.strip() + "\n")
        except tk.TclError:
            pass
        row = ctk.CTkFrame(win, fg_color="transparent")
        row.pack(fill="x", padx=18, pady=(0, 14))
        self.count = ctk.CTkLabel(row, text="", text_color=COLORS["muted"], font=font(11))
        self.count.pack(side="left")
        glass_button(row, "Continue", self.submit, variant="primary", width=120, height=34).pack(side="right")
        glass_button(row, "Cancel", win.destroy, width=100, height=34).pack(side="right", padx=8)
        self.box.bind("<KeyRelease>", lambda e: self._update_count())
        self._update_count()
        win.after(100, self.box.focus_set)

    def links(self):
        return [line.strip() for line in self.box.get("1.0", "end").splitlines() if classify(line.strip())]

    def _update_count(self):
        n = len(self.links())
        self.count.configure(text=f"{n} link{'s' if n != 1 else ''} recognised")

    def submit(self):
        links = self.links()
        if not links:
            messagebox.showinfo("Add Multiple URLs", "No downloadable links found.", parent=self.win)
            return
        self.win.destroy()
        self.app.open_add_dialog("\n".join(links), analyze=True)


# --------------------------------------------------------- file integrity -- #
class IntegrityDialog:
    def __init__(self, app, path=None):
        self.app = app
        win = self.win = ctk.CTkToplevel(app.root)
        win.title("Check File Integrity")
        win.geometry("640x380")
        app.apply_glass_window(win)
        self.path = tk.StringVar(value=path or "")
        self.algo = tk.StringVar(value="sha256")
        self.expected = tk.StringVar()
        ctk.CTkLabel(win, text="Check File Integrity", font=font(16, "bold"), anchor="w").pack(fill="x", padx=18,
                                                                                              pady=(16, 2))
        ctk.CTkLabel(win, text="Compute a file's checksum and compare it with the one published by the download "
                               "site.", text_color=COLORS["muted"], font=font(12), anchor="w").pack(fill="x", padx=18)
        row = ctk.CTkFrame(win, fg_color="transparent")
        row.pack(fill="x", padx=18, pady=(12, 4))
        ctk.CTkEntry(row, textvariable=self.path, placeholder_text="File to check", height=32).pack(
            side="left", fill="x", expand=True)
        glass_button(row, "Browse…", self.browse, width=90, height=32).pack(side="left", padx=(8, 0))
        row = ctk.CTkFrame(win, fg_color="transparent")
        row.pack(fill="x", padx=18, pady=4)
        ctk.CTkLabel(row, text="Algorithm", width=110, anchor="w").pack(side="left")
        ctk.CTkSegmentedButton(row, values=list(CHECKSUM_ALGORITHMS), variable=self.algo).pack(side="left")
        row = ctk.CTkFrame(win, fg_color="transparent")
        row.pack(fill="x", padx=18, pady=4)
        ctk.CTkLabel(row, text="Expected", width=110, anchor="w").pack(side="left")
        ctk.CTkEntry(row, textvariable=self.expected, placeholder_text="optional — paste the published hash",
                     height=32).pack(side="left", fill="x", expand=True)
        self.result = ctk.CTkTextbox(win, height=80, font=("Consolas", 11))
        self.result.pack(fill="x", padx=18, pady=8)
        self.progress = ctk.CTkProgressBar(win, mode="indeterminate", height=6)
        row = ctk.CTkFrame(win, fg_color="transparent")
        row.pack(fill="x", padx=18, pady=(0, 14))
        self.status = ctk.CTkLabel(row, text="", font=font(12, "bold"))
        self.status.pack(side="left")
        self.go = glass_button(row, "Compute", self.compute, variant="primary", width=110, height=34)
        self.go.pack(side="right")
        glass_button(row, "Close", win.destroy, width=90, height=34).pack(side="right", padx=8)

    def browse(self):
        p = filedialog.askopenfilename(parent=self.win)
        if p:
            self.path.set(os.path.normpath(p))

    def compute(self):
        path = self.path.get().strip().strip('"')
        if not os.path.isfile(path):
            self.status.configure(text="Choose a file first.", text_color=COLORS["danger"])
            return
        expected = None
        if self.expected.get().strip():
            try:
                algo, expected = parse_checksum(self.expected.get())
                self.algo.set(algo)
            except ValueError as e:
                self.status.configure(text=str(e), text_color=COLORS["danger"])
                return
        algo = self.algo.get()
        self.go.configure(state="disabled")
        self.status.configure(text=f"Computing {algo.upper()}…", text_color=COLORS["muted"])
        self.progress.pack(fill="x", padx=18)
        self.progress.start()

        def work():
            digest = file_digest(path, algo)
            self.app.call_in_main(self._done, path, algo, digest, expected)
        threading.Thread(target=work, daemon=True).start()

    def _done(self, path, algo, digest, expected):
        if not self.win.winfo_exists():
            return
        self.progress.stop()
        self.progress.pack_forget()
        self.go.configure(state="normal")
        self.result.delete("1.0", "end")
        self.result.insert("1.0", f"{os.path.basename(path)}  ({human_size(os.path.getsize(path))})\n{algo.upper()}: {digest}")
        if expected is None:
            self.status.configure(text="Computed.", text_color=COLORS["text"])
        elif expected == digest:
            self.status.configure(text="✓ Match — the file is intact.", text_color=COLORS["success"])
        else:
            self.status.configure(text="✕ Mismatch — the file differs from the published one.",
                                  text_color=COLORS["danger"])


# ---------------------------------------------------------------- history -- #
class HistoryWindow:
    COLUMNS = [dict(key="title", title="Name", width=320, min=160, stretch=True),
               dict(key="status", title="Status", width=100),
               dict(key="category", title="Category", width=100, muted=True),
               dict(key="size", title="Size", width=90, anchor="e"),
               dict(key="date", title="Date", width=130, muted=True)]

    def __init__(self, app):
        self.app = app
        win = self.win = ctk.CTkToplevel(app.root)
        win.title("Download History")
        win.geometry("940x600")
        app.apply_glass_window(win)
        top = ctk.CTkFrame(win, fg_color="transparent")
        top.pack(fill="x", padx=16, pady=(14, 6))
        ctk.CTkLabel(top, text="Download History", font=font(16, "bold")).pack(side="left")
        self.count = ctk.CTkLabel(top, text="", text_color=COLORS["muted"], font=font(12))
        self.count.pack(side="left", padx=10)
        self.query = tk.StringVar()
        self.status = tk.StringVar(value="All")
        ctk.CTkOptionMenu(top, values=["All", "Completed", "Failed", "Cancelled"], variable=self.status, width=120,
                          command=lambda _v: self.refresh()).pack(side="right")
        e = ctk.CTkEntry(top, textvariable=self.query, placeholder_text="Search history", width=240, height=30)
        e.pack(side="right", padx=8)
        self.query.trace_add("write", lambda *_: self.refresh())
        self.grid = DataGrid(win, self.COLUMNS, self._cell, key=lambda r: r["id"], on_activate=self._open)
        self.grid.pack(fill="both", expand=True, padx=16)
        bar = ctk.CTkFrame(win, fg_color="transparent")
        bar.pack(fill="x", padx=16, pady=12)
        glass_button(bar, "Open file", lambda: self._sel(self._open), width=100, height=32).pack(side="left")
        glass_button(bar, "Show in folder", lambda: self._sel(lambda r: open_folder(r["path"], r["file"] or None)),
                     width=120, height=32).pack(side="left", padx=6)
        glass_button(bar, "Download again", lambda: self._sel(lambda r: app.open_add_dialog(r["url"], analyze=True)),
                     width=130, height=32).pack(side="left")
        glass_button(bar, "Remove entry", self._remove, width=120, height=32).pack(side="left", padx=6)
        glass_button(bar, "Export…", lambda: app.open_settings("History & Storage"), width=90,
                     height=32).pack(side="left")
        glass_button(bar, "Close", win.destroy, width=90, height=32).pack(side="right")
        self.refresh()

    def _cell(self, r, key):
        if key == "size":
            return human_size(r.get("file_size"), "")
        return str(r.get(key) or "")

    def refresh(self):
        status = None if self.status.get() == "All" else self.status.get()
        rows = self.app.history.search(self.query.get().strip(), status=status, limit=5000)
        self.grid.set_rows(rows)
        self.count.configure(text=f"{len(rows):,} shown of {self.app.history.count():,}")

    def _sel(self, fn):
        rows = self.grid.selected()
        if rows:
            fn(rows[0])

    def _open(self, r):
        if r.get("file") and os.path.exists(r["file"]):
            open_file(r["file"], self.win)
        else:
            messagebox.showinfo("Open file", "The file is no longer at its recorded location.", parent=self.win)

    def _remove(self):
        rows = self.grid.selected()
        if rows and messagebox.askyesno("Remove from history", f"Remove {len(rows)} entr{'y' if len(rows) == 1 else 'ies'} "
                                        "from the history? Downloaded files are kept.", parent=self.win):
            for r in rows:
                self.app.history.remove_by_id(r["id"])
            self.refresh()


# ------------------------------------------------------- download lists -- #
def export_download_list(app, parent):
    jobs = [j for j in app.manager.list_jobs() if j.url]
    if not jobs:
        messagebox.showinfo("Export Download List", "The download list is empty.", parent=parent)
        return
    path = filedialog.asksaveasfilename(parent=parent, defaultextension=".json", initialfile="mdm-download-list.json",
                                        filetypes=[("MDM download list", "*.json"), ("Plain list of links", "*.txt")])
    if not path:
        return
    if path.lower().endswith(".txt"):
        text = "\n".join(dict.fromkeys(j.url for j in jobs)) + "\n"
    else:
        text = json.dumps({"app": "Master Download Manager", "kind": "download-list", "version": 1,
                           "exported": datetime.datetime.now().isoformat(timespec="seconds"),
                           "downloads": [{"url": j.url, "title": j.title, "status": j.status, "engine": j.engine,
                                          "queue": j.queue_id, "folder": j.download_dir} for j in jobs]}, indent=1)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    app.toast("success", f"Exported {len(jobs)} download(s).")


def import_download_list(app, parent):
    path = filedialog.askopenfilename(parent=parent, filetypes=[("Download lists", "*.json *.txt"), ("All files", "*.*")])
    if not path:
        return
    try:
        if os.path.getsize(path) > 20 * 1024 * 1024:
            raise ValueError("That file is too large to be a download list.")
        with open(path, encoding="utf-8-sig") as f:
            text = f.read()
        if path.lower().endswith(".json"):
            data = json.loads(text)
            items = data.get("downloads", []) if isinstance(data, dict) else data
            links = [str(i.get("url", "")) if isinstance(i, dict) else str(i) for i in items]
        else:
            links = text.splitlines()
    except (OSError, ValueError) as e:
        messagebox.showerror("Import Download List", f"Couldn't read the list: {e}", parent=parent)
        return
    links = [link.strip() for link in links if classify(link.strip())]
    if not links:
        messagebox.showinfo("Import Download List", "No downloadable links were found in that file.", parent=parent)
        return
    # Shown in the New Download batch view first: nothing starts without the user's OK.
    app.open_add_dialog("\n".join(dict.fromkeys(links)), analyze=True)


# --------------------------------------------------------------- settings -- #
def export_settings(app, parent):
    path = filedialog.asksaveasfilename(parent=parent, defaultextension=".json", initialfile="mdm-settings.json",
                                        filetypes=[("MDM settings", "*.json")])
    if not path:
        return
    data = {k: v for k, v in app.settings.all().items() if k not in PRIVATE_SETTINGS and k in DEFAULT_SETTINGS}
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"app": "Master Download Manager", "kind": "settings", "version": 1, "settings": data}, f, indent=1)
    app.toast("success", "Settings exported (the browser security token is never included).")


def import_settings(app, parent):
    path = filedialog.askopenfilename(parent=parent, filetypes=[("MDM settings", "*.json")])
    if not path:
        return
    try:
        with open(path, encoding="utf-8-sig") as f:
            data = json.load(f)
        incoming = data.get("settings") if isinstance(data, dict) and data.get("kind") == "settings" else None
        if not isinstance(incoming, dict):
            raise ValueError("This isn't a Master Download Manager settings export.")
    except (OSError, ValueError) as e:
        messagebox.showerror("Import Settings", str(e), parent=parent)
        return
    # only known keys with the same type as the default; secrets and machine state are skipped
    clean = {k: v for k, v in incoming.items() if k in DEFAULT_SETTINGS and k not in PRIVATE_SETTINGS
             and (type(v) is type(DEFAULT_SETTINGS[k]) or (isinstance(DEFAULT_SETTINGS[k], float) and isinstance(v, int)))}
    if not clean:
        messagebox.showinfo("Import Settings", "Nothing usable was found in that file.", parent=parent)
        return
    if not messagebox.askyesno("Import Settings", f"Apply {len(clean)} setting(s) from this file? Your downloads, "
                               "history and the browser extension pairing aren't affected.", parent=parent):
        return
    app.settings.update(clean)
    app.settings_changed()
    app.set_appearance_mode(app.settings.get("appearance_mode", "Dark"), app.settings.get("accent_color", "Indigo"))
    app.toast("success", f"Imported {len(clean)} setting(s).")
