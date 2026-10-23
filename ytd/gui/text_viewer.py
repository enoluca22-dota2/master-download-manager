"""A read-only text window (license texts, notices, changelog, guides, logs)
with selectable text, find, Copy all and an optional "Open online" link."""

import tkinter as tk
import webbrowser

import customtkinter as ctk

from .theme import COLORS, font, glass_button


class TextViewer:
    def __init__(self, app, title, text, *, online_url=None, mono=False, size="760x620", extra_buttons=()):
        self.app = app
        win = self.win = ctk.CTkToplevel(app.root)
        win.title(title)
        win.geometry(size)
        win.minsize(520, 360)
        app.apply_glass_window(win)
        top = ctk.CTkFrame(win, fg_color="transparent")
        top.pack(fill="x", padx=14, pady=(12, 6))
        ctk.CTkLabel(top, text=title, font=font(15, "bold"), anchor="w").pack(side="left")
        self.find_var = tk.StringVar()
        entry = ctk.CTkEntry(top, textvariable=self.find_var, placeholder_text="Find…", width=180, height=30)
        entry.pack(side="right")
        entry.bind("<Return>", lambda e=None: self.find_next())
        self.box = ctk.CTkTextbox(win, wrap="word", font=("Consolas", 11) if mono else font(12))
        self.box.pack(fill="both", expand=True, padx=14, pady=4)
        self.box.insert("1.0", text)
        self.box.configure(state="disabled")  # still selectable and copyable
        self.box.tag_config("found", background="#7364E8", foreground="#FFFFFF")
        bottom = ctk.CTkFrame(win, fg_color="transparent")
        bottom.pack(fill="x", padx=14, pady=(4, 12))
        self.note = ctk.CTkLabel(bottom, text="", text_color=COLORS["muted"], font=font(11))
        self.note.pack(side="left")
        glass_button(bottom, "Close", win.destroy, width=90, height=32).pack(side="right")
        glass_button(bottom, "Copy all", self.copy_all, width=100, height=32).pack(side="right", padx=8)
        if online_url:
            glass_button(bottom, "Open online", lambda: webbrowser.open(online_url), width=110,
                         height=32).pack(side="right")
        for label, command in extra_buttons:
            glass_button(bottom, label, command, width=140, height=32).pack(side="right", padx=(0, 8))
        self._last = "1.0"
        win.bind("<Control-f>", lambda e=None: entry.focus_set())
        win.bind("<Escape>", lambda e=None: win.destroy())
        win.after(80, lambda: win.winfo_exists() and win.lift())

    def copy_all(self):
        self.win.clipboard_clear()
        self.win.clipboard_append(self.box.get("1.0", "end-1c"))
        self.note.configure(text="Copied to the clipboard.")

    def find_next(self):
        needle = self.find_var.get().strip()
        tb = self.box._textbox
        tb.tag_remove("found", "1.0", "end")
        if not needle:
            return
        pos = tb.search(needle, self._last, nocase=True, stopindex="end") or tb.search(needle, "1.0", nocase=True,
                                                                                        stopindex="end")
        if not pos:
            self.note.configure(text="Not found.")
            return
        end = f"{pos}+{len(needle)}c"
        tb.tag_add("found", pos, end)
        tb.see(pos)
        self._last = end
        self.note.configure(text="")
