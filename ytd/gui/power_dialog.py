"""Cancellable countdown shown before a queue's completion action
(exit / sleep / hibernate / shutdown) — like AB Download Manager's power
action alert. Nothing happens unless the countdown runs out; Cancel (or
closing the window, or Esc) always wins."""

import customtkinter as ctk

from ..power import ACTION_LABELS
from .theme import COLORS, font, glass_button

COUNTDOWN_SECONDS = 60


class PowerCountdown:
    def __init__(self, app, queue_name, action, on_confirm, seconds=COUNTDOWN_SECONDS):
        self.app, self.action, self.on_confirm = app, action, on_confirm
        self.remaining = seconds
        self.total = seconds
        self._cancelled = False
        win = self.win = ctk.CTkToplevel(app.root)
        win.title("Downloads finished")
        win.geometry("420x220")
        win.resizable(False, False)
        win.attributes("-topmost", True)
        app.apply_glass_window(win)
        verb = ACTION_LABELS.get(action, action)
        ctk.CTkLabel(win, text=f"Queue “{queue_name}” has finished", font=font(15, "bold")).pack(pady=(22, 4))
        self.label = ctk.CTkLabel(win, text="", font=font(12), text_color=COLORS["muted"])
        self.label.pack()
        self.bar = ctk.CTkProgressBar(win, width=340, height=6, progress_color=COLORS["warning"])
        self.bar.pack(pady=16)
        self.bar.set(1.0)
        row = ctk.CTkFrame(win, fg_color="transparent")
        row.pack(pady=4)
        cancel = glass_button(row, "Cancel", self.cancel, variant="primary", width=140)
        cancel.pack(side="left", padx=6)
        glass_button(row, f"{verb} now", self.confirm, width=140).pack(side="left", padx=6)
        self.verb = verb
        win.protocol("WM_DELETE_WINDOW", self.cancel)
        win.bind("<Escape>", lambda e: self.cancel())
        win.after(150, cancel.focus_set)
        self._tick()

    def _tick(self):
        if self._cancelled or not self.win.winfo_exists():
            return
        self.label.configure(text=f"{self.verb} in {self.remaining} s — unless you cancel.")
        self.bar.set(self.remaining / self.total)
        if self.remaining <= 0:
            self.confirm()
            return
        self.remaining -= 1
        self.win.after(1000, self._tick)

    def cancel(self):
        self._cancelled = True
        if self.win.winfo_exists():
            self.win.destroy()
        self.app.toast("info", f"{self.verb} cancelled.")

    def confirm(self):
        if self._cancelled:
            return
        self._cancelled = True
        if self.win.winfo_exists():
            self.win.destroy()
        self.on_confirm()
