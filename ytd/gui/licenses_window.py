"""Help → View Open-Source Licenses: every third-party component bundled in
this build (from licenses/inventory.json, generated and verified against the
build by tools/collect_licenses.py), searchable and sortable, with full
license texts, repository links and copy."""

import json
import os
import re
import tkinter as tk
import webbrowser

import customtkinter as ctk

from ..config import LICENSES_DIR, NOTICES_FILE
from .datagrid import DataGrid
from .text_viewer import TextViewer
from .theme import COLORS, font, glass_button

KIND_LABELS = {"python-package": "Python package", "native-library": "Native library (bundled with Python)",
               "runtime": "Runtime", "executable": "Bundled program", "bundled-javascript": "Bundled JavaScript",
               "design-reference": "Design reference (not bundled)"}


def _natural(value):
    """Sort key that orders 8.6.15 after 8.5.0 and 13.0.0 after 4.16.0."""
    return [(0, int(p), "") if p.isdigit() else (1, 0, p) for p in re.split(r"(\d+)", str(value or "").lower()) if p]


def load_inventory(licenses_dir=LICENSES_DIR):
    path = os.path.join(licenses_dir, "inventory.json")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    comps = data.get("components") if isinstance(data, dict) else data
    return [c for c in comps or [] if isinstance(c, dict)]


def license_text(component, licenses_dir=LICENSES_DIR):
    parts = []
    for rel in component.get("license_files") or []:
        root = os.path.abspath(licenses_dir)
        path = os.path.abspath(os.path.join(root, rel))
        if os.path.commonpath([root, path]) != root:
            continue  # never read outside the licenses folder
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                parts.append(f"===== {os.path.basename(rel)} =====\n\n{f.read().strip()}\n")
        except OSError:
            parts.append(f"===== {os.path.basename(rel)} =====\n\n(missing)\n")
    return "\n\n".join(parts)


class LicensesWindow:
    COLUMNS = [dict(key="name", title="Name", width=230, min=150, stretch=True),
               dict(key="author", title="Author", width=230, muted=True),
               dict(key="license", title="License", width=190),
               dict(key="version", title="Version", width=110, muted=True)]

    def __init__(self, app):
        self.app = app
        win = self.win = ctk.CTkToplevel(app.root)
        win.title("Open Source Software Used in Master Download Manager")
        win.geometry("940x620")
        win.minsize(720, 440)
        app.apply_glass_window(win)
        try:
            self.items = load_inventory()
            error = ""
        except (OSError, ValueError) as e:
            self.items, error = [], f"The license inventory couldn't be read: {e}"
        head = ctk.CTkFrame(win, fg_color="transparent")
        head.pack(fill="x", padx=16, pady=(14, 4))
        ctk.CTkLabel(head, text="Open Source Software Used in Master Download Manager", font=font(16, "bold"),
                     anchor="w").pack(side="left")
        sub = ctk.CTkFrame(win, fg_color="transparent")
        sub.pack(fill="x", padx=16, pady=(0, 8))
        ctk.CTkLabel(sub, text=error or f"{len(self.items)} components. Each stays under its own license. "
                     "Double-click a row for its full license text.", text_color=COLORS["muted"],
                     font=font(12)).pack(side="left")
        self.search = tk.StringVar()
        # no textvariable: CTkEntry hides its placeholder when one is attached
        entry = ctk.CTkEntry(sub, placeholder_text="Search name, author or license", width=260, height=32)
        entry.pack(side="right")
        entry.bind("<KeyRelease>", lambda e=None: self.search.set(entry.get()))
        self.search.trace_add("write", lambda *_: self.refresh())

        body = ctk.CTkFrame(win, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=16)
        self.grid = DataGrid(body, self.COLUMNS, self._cell, key=lambda c: c.get("id") or c.get("name"),
                             on_activate=self.open_details, on_sort=self._sort,
                             on_context=lambda e, rows: self._context(e, rows))
        self.grid.pack(fill="both", expand=True)
        self.sort = ("name", False)
        self.grid.set_sort("name", False)

        bar = ctk.CTkFrame(win, fg_color="transparent")
        bar.pack(fill="x", padx=16, pady=12)
        glass_button(bar, "Details & license", lambda: self._with_selection(self.open_details), variant="primary",
                     width=150, height=32).pack(side="left")
        glass_button(bar, "Open repository", lambda: self._with_selection(self.open_repo), width=140,
                     height=32).pack(side="left", padx=8)
        glass_button(bar, "Copy license text", lambda: self._with_selection(self.copy_text), width=150,
                     height=32).pack(side="left")
        glass_button(bar, "Third-party notices", self.open_notices, width=160, height=32).pack(side="left", padx=8)
        glass_button(bar, "Close", win.destroy, width=90, height=32).pack(side="right")
        win.bind("<Escape>", lambda e=None: win.destroy())
        self.refresh()

    # ---------------------------------------------------------------- grid -- #
    def _cell(self, comp, key):
        return str(comp.get(key) or "")

    def _sort(self, key):
        reverse = self.sort[0] == key and not self.sort[1]
        self.sort = (key, reverse)
        self.grid.set_sort(key, reverse)
        self.refresh()

    def visible(self):
        q = self.search.get().strip().lower()
        rows = [c for c in self.items if not q or any(q in str(c.get(k, "")).lower()
                                                       for k in ("name", "author", "license", "version"))]
        key, reverse = self.sort
        return sorted(rows, key=lambda c: _natural(c.get(key)), reverse=reverse)

    def refresh(self):
        rows = self.visible()
        self.grid.set_rows(rows)
        self.grid.set_empty_text("" if rows else "No component matches your search.")

    def _with_selection(self, fn):
        rows = self.grid.selected()
        if rows:
            fn(rows[0])

    def _context(self, event, rows):
        if not rows:
            return
        c = self.app.c
        m = tk.Menu(self.win, tearoff=0, bg=c["surface"], fg=c["text"], activebackground=c["surface_hover"],
                    activeforeground=c["text"], bd=0, font=("Segoe UI", 10))
        m.add_command(label="Details & license…", command=lambda: self.open_details(rows[0]))
        m.add_command(label="Open repository", command=lambda: self.open_repo(rows[0]),
                      state="normal" if self._url(rows[0]) else "disabled")
        m.add_command(label="Copy license text", command=lambda: self.copy_text(rows[0]))
        try:
            m.tk_popup(event.x_root, event.y_root)
        finally:
            m.grab_release()

    # ------------------------------------------------------------- actions -- #
    @staticmethod
    def _url(comp):
        url = comp.get("repository") or comp.get("homepage") or ""
        return url if url.startswith(("https://", "http://")) else ""

    def open_repo(self, comp):
        if self._url(comp):
            webbrowser.open(self._url(comp))

    def copy_text(self, comp):
        self.win.clipboard_clear()
        self.win.clipboard_append(license_text(comp))
        self.app.toast("info", f"License text of {comp.get('name')} copied.")

    def open_details(self, comp):
        header = "\n".join(line for line in (
            f"Name:        {comp.get('name')}",
            f"Version:     {comp.get('version')}",
            f"Author:      {comp.get('author')}",
            f"License:     {comp.get('license')}  (SPDX)",
            f"Type:        {KIND_LABELS.get(comp.get('kind'), comp.get('kind', ''))}",
            f"Included as: {comp.get('bundled_as', '')}",
            f"Source:      {self._url(comp) or '-'}",
            f"Note:        {comp['note']}" if comp.get("note") else "",
        ) if line)
        TextViewer(self.app, f"{comp.get('name')} {comp.get('version')} — license",
                   header + "\n\n" + license_text(comp), online_url=self._url(comp) or None, mono=True)

    def open_notices(self):
        show_notices(self.app)


def show_notices(app):
    try:
        with open(NOTICES_FILE, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        text = "THIRD_PARTY_NOTICES.md wasn't found in this installation."
    TextViewer(app, "Third-Party Notices", text)
