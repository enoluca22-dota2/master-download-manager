"""Help → About Master Download Manager."""

import os
import tkinter as tk
import webbrowser

import customtkinter as ctk

from .. import __version__
from ..config import APP_NAME, RESOURCE_DIR
from . import icons
from .theme import color, glass_button

REPO_URL = "https://github.com/enoluca22-dota2/master-download-manager"
LINKS = {
    "github": REPO_URL,
    "docs": f"{REPO_URL}/blob/main/docs/USER_GUIDE.md",
    "changelog": f"{REPO_URL}/blob/main/CHANGELOG.md",
    "support": f"{REPO_URL}/issues",
}


class _Card(tk.Frame):
    """A clickable panel: icon, title, subtitle."""

    def __init__(self, parent, app, glyph, title, subtitle, command):
        c = app.c
        super().__init__(parent, bg=c["surface"], highlightthickness=1, highlightbackground=c["border"], cursor="hand2")
        self.c, self.command = c, command
        self.icon = tk.Label(self, text=icons.glyph(glyph), font=icons.icon_font(20), bg=c["surface"], fg=c["primary"])
        self.icon.pack(side="left", padx=(16, 12), pady=14)
        box = tk.Frame(self, bg=c["surface"])
        box.pack(side="left", fill="x", expand=True, pady=12)
        self.title = tk.Label(box, text=title, font=("Segoe UI Semibold", 11), bg=c["surface"], fg=c["text"], anchor="w")
        self.title.pack(fill="x")
        self.sub = tk.Label(box, text=subtitle, font=("Segoe UI", 9), bg=c["surface"], fg=c["muted"], anchor="w")
        self.sub.pack(fill="x")
        self.arrow = tk.Label(self, text=icons.glyph("chev_right"), font=icons.icon_font(10), bg=c["surface"],
                              fg=c["faint"])
        self.arrow.pack(side="right", padx=14)
        for w in (self, self.icon, box, self.title, self.sub, self.arrow):
            w.bind("<Button-1>", lambda e=None: command())
            w.bind("<Enter>", lambda e=None: self._paint(True))
            w.bind("<Leave>", lambda e=None: self._paint(False))
        self.widgets = (self, self.icon, box, self.title, self.sub, self.arrow)

    def _paint(self, hover):
        bg = self.c["surface_hover"] if hover else self.c["surface"]
        for w in self.widgets:
            w.configure(bg=bg)


class AboutWindow:
    def __init__(self, app):
        self.app = app
        win = self.win = ctk.CTkToplevel(app.root)
        win.title(f"About {APP_NAME}")
        win.geometry("760x430")
        win.resizable(False, False)
        app.apply_glass_window(win)
        c = app.c
        outer = tk.Frame(win, bg=c["background"])
        outer.pack(fill="both", expand=True)

        left = tk.Frame(outer, bg=c["sidebar"], width=300)
        left.pack(side="left", fill="y")
        left.pack_propagate(False)
        self._logo = None
        logo_path = os.path.join(RESOURCE_DIR, "mdm.png")
        if os.path.isfile(logo_path):
            try:
                from PIL import Image
                img = Image.open(logo_path)
                self._logo = ctk.CTkImage(light_image=img, dark_image=img, size=(96, 96))
                ctk.CTkLabel(left, text="", image=self._logo, fg_color=c["sidebar"]).pack(pady=(34, 12))
            except Exception:  # noqa: BLE001 - the logo is decoration
                pass
        tk.Label(left, text=APP_NAME, font=("Segoe UI Semibold", 15), bg=c["sidebar"], fg=c["text"]).pack()
        tk.Label(left, text=f"Version {__version__}", font=("Segoe UI", 10), bg=c["sidebar"],
                 fg=c["primary"]).pack(pady=(2, 10))
        tk.Label(left, text="Download files, videos, music and\ntorrents — with queues, a scheduler\nand a browser extension.",
                 font=("Segoe UI", 9), bg=c["sidebar"], fg=c["muted"], justify="center").pack(padx=16)
        tk.Label(left, text="© 2026 the Master Download Manager author", font=("Segoe UI", 8), bg=c["sidebar"],
                 fg=c["faint"]).pack(pady=(14, 2))
        link = tk.Label(left, text="github.com/enoluca22-dota2/master-download-manager", font=("Segoe UI", 8, "underline"),
                        bg=c["sidebar"], fg=c["primary"], cursor="hand2")
        link.pack()
        link.bind("<Button-1>", lambda e=None: webbrowser.open(REPO_URL))

        foot = tk.Frame(left, bg=c["sidebar"])
        foot.pack(side="bottom", pady=16)
        for glyph, key, tip in (("globe", "github", "GitHub"), ("document", "docs", "User guide"),
                                ("history", "changelog", "Changelog"), ("info", "support", "Support")):
            b = tk.Label(foot, text=icons.glyph(glyph), font=icons.icon_font(14), bg=c["sidebar"], fg=c["muted"],
                         cursor="hand2", padx=10)
            b.pack(side="left")
            b.bind("<Button-1>", lambda e=None, k=key: webbrowser.open(LINKS[k]))
            b.bind("<Enter>", lambda e=None, w=b: w.configure(fg=c["text"]))
            b.bind("<Leave>", lambda e=None, w=b: w.configure(fg=c["muted"]))
            self._tooltip(b, tip)

        right = tk.Frame(outer, bg=c["background"])
        right.pack(side="left", fill="both", expand=True, padx=26, pady=26)
        tk.Label(right, text="About", font=("Segoe UI Semibold", 14), bg=c["background"], fg=c["text"],
                 anchor="w").pack(fill="x", pady=(0, 12))
        _Card(right, app, "globe", "Project Information", "View source code and project information",
              lambda: webbrowser.open(REPO_URL)).pack(fill="x", pady=6)
        _Card(right, app, "document", "Powered by Open Source Software", "View third-party licenses",
              app.open_licenses).pack(fill="x", pady=6)
        _Card(right, app, "download", "Updates", "Check for the latest version",
              app.check_for_updates).pack(fill="x", pady=6)
        tk.Label(right, text="The program isn't digitally signed. Its own code isn't open-source licensed; bundled "
                 "components keep their own licenses.", font=("Segoe UI", 8), bg=c["background"], fg=c["faint"],
                 wraplength=380, justify="left", anchor="w").pack(fill="x", pady=(14, 0))
        glass_button(right, "Close", win.destroy, width=90, height=30).pack(side="bottom", anchor="e")
        win.bind("<Escape>", lambda e=None: win.destroy())

    def _tooltip(self, widget, text):
        tip = {"w": None}

        def show(_e):
            t = tk.Toplevel(widget)
            t.wm_overrideredirect(True)
            t.geometry(f"+{widget.winfo_rootx()}+{widget.winfo_rooty() - 26}")
            tk.Label(t, text=text, bg=color("surface_raised"), fg=color("text"), font=("Segoe UI", 8), padx=6,
                     pady=2).pack()
            tip["w"] = t

        def hide(_e):
            if tip["w"] is not None:
                tip["w"].destroy()
                tip["w"] = None
        widget.bind("<Enter>", show, add="+")
        widget.bind("<Leave>", hide, add="+")


