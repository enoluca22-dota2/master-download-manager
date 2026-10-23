"""Left navigation: a collapsible category tree with live counts and the
download queues (AB Download Manager's layout, MDM's own look).

View ids:  all | cat:<Category> | status:finished|unfinished|failed | queue:<id>
"""

import tkinter as tk

import customtkinter as ctk

from ..categories import FILE_CATEGORIES, MEDIA_SITES, TORRENTS
from . import icons
from .theme import color

CATEGORY_ICONS = {"Compressed": "zip", "Programs": "app", "Videos": "video", "Music": "music",
                  "Pictures": "picture", "Documents": "document", TORRENTS: "torrent", MEDIA_SITES: "globe"}
STATUS_VIEWS = [("status:finished", "Finished", "done"), ("status:unfinished", "Unfinished", "unfinished"),
                ("status:failed", "Failed", "error")]


class _Item(tk.Frame):
    def __init__(self, parent, sidebar, view, text, glyph, indent=0, toggle=None):
        super().__init__(parent, height=round(32 * sidebar.scale), bd=0, highlightthickness=0, cursor="hand2")
        self.sidebar, self.view = sidebar, view
        self.pack_propagate(False)
        s = sidebar.scale
        self.bar = tk.Frame(self, width=max(2, round(3 * s)), bd=0)
        self.bar.pack(side="left", fill="y", pady=round(7 * s))
        self.spacer = tk.Frame(self, width=round((6 + indent * 16) * s), bd=0)
        self.spacer.pack(side="left", fill="y")
        self.toggle = None
        if toggle is not None:
            self.toggle = tk.Label(self, text=icons.glyph("chev_down"), font=icons.icon_font(8), bd=0, width=2,
                                   cursor="hand2")
            self.toggle.pack(side="right", padx=(0, round(6 * s)))
            self.toggle.bind("<Button-1>", lambda e: (toggle(), "break")[1])
        self.icon = tk.Label(self, text=icons.glyph(glyph), font=icons.icon_font(11), bd=0, width=2)
        self.icon.pack(side="left")
        self.label = tk.Label(self, text=text, font=("Segoe UI", 10), bd=0, anchor="w")
        self.label.pack(side="left", fill="x", expand=True, padx=(round(4 * s), 0))
        self.count = tk.Label(self, text="", font=("Segoe UI", 9), bd=0, anchor="e")
        self.count.pack(side="right", padx=(0, round(10 * s)))
        self.running = None
        for w in (self, self.icon, self.label, self.count, self.bar):
            w.bind("<Button-1>", lambda e: sidebar.select(view))
            w.bind("<Enter>", lambda e: self._hover(True))
            w.bind("<Leave>", lambda e: self._hover(False))
            w.bind("<Button-3>", lambda e: sidebar.context(view, e))
        self._hovered = False

    def _hover(self, on):
        self._hovered = on
        self.paint()

    def set_running(self, running):
        self.running = running
        self.paint()

    def paint(self):
        sb = self.sidebar
        selected = sb.current == self.view
        bg = sb.c["selected"] if selected else sb.c["surface_hover"] if self._hovered else sb.c["sidebar"]
        for w in (self, self.spacer, self.icon, self.label, self.count) + ((self.toggle,) if self.toggle else ()):
            w.configure(bg=bg)
        self.bar.configure(bg=sb.c["primary"] if selected else bg)
        self.label.configure(fg=sb.c["text"] if selected else sb.c["muted"],
                             font=("Segoe UI Semibold", 10) if selected else ("Segoe UI", 10))
        self.icon.configure(fg=sb.c["primary"] if selected else sb.c["muted"])
        self.count.configure(fg=sb.c["success"] if self.running else sb.c["faint"])
        if self.toggle:
            self.toggle.configure(fg=sb.c["faint"])


class Sidebar(ctk.CTkFrame):
    def __init__(self, parent, app, width=212):
        super().__init__(parent, width=width, corner_radius=0)
        self.app = app
        try:
            self.scale = ctk.ScalingTracker.get_widget_scaling(parent)
        except Exception:  # noqa: BLE001
            self.scale = 1.0
        self.current = "all"
        self.items = {}
        self.collapsed = set(app.settings.get("sidebar_collapsed", []) or [])
        self.grid_propagate(False)
        self.pack_propagate(False)
        self.scroll = ctk.CTkScrollableFrame(self, corner_radius=0, fg_color="transparent",
                                             scrollbar_button_color=("#DDDDDD", "#2A2A2A"))
        self.scroll.pack(fill="both", expand=True)
        self.apply_colors()
        self.rebuild()

    def apply_colors(self):
        self.c = {k: color(k) for k in ("sidebar", "selected", "surface_hover", "text", "muted", "faint", "primary",
                                        "success", "border")}
        self.configure(fg_color=self.c["sidebar"])
        # scrollbar blends in until hovered (it's rarely needed)
        self.scroll.configure(fg_color=self.c["sidebar"], scrollbar_button_color=self.c["sidebar"],
                              scrollbar_button_hover_color=self.c["border"])

    # ------------------------------------------------------------ build --- #
    def rebuild(self):
        for w in self.scroll.winfo_children():
            w.destroy()
        self.items = {}
        s = self.scale
        self._section("Downloads")
        self._add("all", "All Downloads", "all", toggle=lambda: self._toggle("all"))
        if "all" not in self.collapsed:
            for cat in FILE_CATEGORIES + (TORRENTS, MEDIA_SITES):
                self._add(f"cat:{cat}", cat, CATEGORY_ICONS[cat], indent=1)
        for view, text, glyph in STATUS_VIEWS:
            self._add(view, text, glyph)

        head = self._section("Queues", action=("plus", "New queue", self.app.new_queue))
        del head
        for q in self.app.manager.queues.all():
            item = self._add(f"queue:{q.id}", q.name, "queue")
            item.set_running(q.running)
        tk.Frame(self.scroll, height=round(10 * s), bg=self.c["sidebar"]).pack(fill="x")
        self._paint_all()

    def _section(self, title, action=None):
        s = self.scale
        row = tk.Frame(self.scroll, bg=self.c["sidebar"], height=round(30 * s))
        row.pack(fill="x", pady=(round(10 * s), round(2 * s)))
        row.pack_propagate(False)
        tk.Label(row, text=title.upper(), font=("Segoe UI Semibold", 8), fg=self.c["faint"], bg=self.c["sidebar"],
                 anchor="w").pack(side="left", padx=round(14 * s))
        if action:
            glyph, tip, cmd = action
            btn = tk.Label(row, text=icons.glyph(glyph), font=icons.icon_font(9), fg=self.c["muted"],
                           bg=self.c["sidebar"], cursor="hand2")
            btn.pack(side="right", padx=round(12 * s))
            btn.bind("<Button-1>", lambda e: cmd())
            btn.bind("<Enter>", lambda e: btn.configure(fg=self.c["text"]))
            btn.bind("<Leave>", lambda e: btn.configure(fg=self.c["muted"]))
        return row

    def _add(self, view, text, glyph, indent=0, toggle=None):
        item = _Item(self.scroll, self, view, text, glyph, indent, toggle)
        item.pack(fill="x", padx=round(6 * self.scale), pady=0)
        self.items[view] = item
        if toggle and view in self.collapsed and item.toggle:
            item.toggle.configure(text=icons.glyph("chev_right"))
        return item

    def _toggle(self, view):
        self.collapsed ^= {view}
        self.app.settings.set("sidebar_collapsed", sorted(self.collapsed))
        self.rebuild()
        self.update_counts(self._last_counts if hasattr(self, "_last_counts") else {})

    def _paint_all(self):
        for item in self.items.values():
            item.paint()

    # ------------------------------------------------------------ state --- #
    def select(self, view):
        if view not in self.items and not view.startswith("queue:"):
            view = "all"
        self.current = view
        self._paint_all()
        self.app.set_view(view)

    def context(self, view, event):
        if view.startswith("queue:"):
            self.app.queue_context_menu(view.split(":", 1)[1], event)

    def update_counts(self, counts, running_queues=None):
        self._last_counts = counts
        for view, item in self.items.items():
            n = counts.get(view)
            text = "" if not n else (f"{n:,}" if n < 100_000 else "99k+")
            if item.count.cget("text") != text:
                item.count.configure(text=text)
            if running_queues is not None and view.startswith("queue:"):
                running = view.split(":", 1)[1] in running_queues
                if running != item.running:
                    item.set_running(running)
