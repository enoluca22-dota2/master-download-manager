"""Per-download details window: live progress, speed graph, engine facts,
and readable errors (reason + suggested action, with technical details
and a Copy button)."""

import os
import tkinter as tk

import customtkinter as ctk

from ..engine import _format_speed
from ..errors import ErrorInfo
from ..models import (
    RUNNING_STATUSES, STATUS_CANCELLED, STATUS_COMPLETED, STATUS_DOWNLOADING, STATUS_FAILED, STATUS_PAUSED,
    STATUS_QUEUED, STATUS_RETRYING,
)
from .common import ENGINE_LABEL, human_size, open_file, open_folder
from .theme import COLORS, fade_in_toplevel, font, glass_button, glass_card, glass_progressbar


class DetailsWindow:
    def __init__(self, app, job_id):
        self.app = app
        self.job_id = job_id
        self.win = win = ctk.CTkToplevel(app.root)
        win.title("Download details — Master Download Manager")
        win.geometry("560x640")
        win.minsize(480, 520)
        app.apply_glass_window(win)
        self._tech_visible = False

        body = glass_card(win, elevated=True)
        body.pack(fill="both", expand=True, padx=16, pady=16)
        inner = ctk.CTkFrame(body, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=20, pady=18)

        self.title_label = ctk.CTkLabel(inner, text="", font=font(16, "bold"), wraplength=480, justify="left", anchor="w")
        self.title_label.pack(anchor="w", fill="x")
        self.status_label = ctk.CTkLabel(inner, text="", font=font(12, "bold"), text_color=COLORS["primary"], anchor="w")
        self.status_label.pack(anchor="w", pady=(2, 10))
        self.progress = glass_progressbar(inner, height=12)
        self.progress.set(0)
        self.progress.pack(fill="x")
        self.speed_label = ctk.CTkLabel(inner, text="", font=font(12, "bold"), text_color=COLORS["cyan"], anchor="w")
        self.speed_label.pack(anchor="w", pady=(6, 8))

        self.buttons = ctk.CTkFrame(inner, fg_color="transparent")
        self.buttons.pack(fill="x", pady=(0, 10))

        self.error_card = glass_card(inner, radius="md", border_color=COLORS["danger"])
        self.error_reason = ctk.CTkLabel(self.error_card, text="", font=font(12, "bold"), text_color=COLORS["danger"],
                                         wraplength=460, justify="left", anchor="w")
        self.error_reason.pack(anchor="w", fill="x", padx=12, pady=(10, 2))
        self.error_hint = ctk.CTkLabel(self.error_card, text="", font=font(12), wraplength=460, justify="left", anchor="w")
        self.error_hint.pack(anchor="w", fill="x", padx=12)
        err_buttons = ctk.CTkFrame(self.error_card, fg_color="transparent")
        err_buttons.pack(anchor="w", padx=12, pady=8)
        self.tech_btn = glass_button(err_buttons, "View technical details", self._toggle_technical, width=170, height=28)
        self.tech_btn.pack(side="left")
        glass_button(err_buttons, "Copy error", self._copy_error, width=100, height=28).pack(side="left", padx=8)
        self.tech_box = ctk.CTkTextbox(self.error_card, height=110, font=("Consolas", 11), wrap="word")

        self.info_label = ctk.CTkLabel(inner, text="", justify="left", anchor="w", wraplength=480, font=font(12))
        self.info_label.pack(anchor="w", fill="x")
        palette = app.palette()
        self._canvas_palette = palette
        self.canvas = tk.Canvas(inner, height=64, bg=palette["surface_deep"], highlightthickness=0)
        self.canvas.pack(fill="x", pady=(10, 0))

        self._last_buttons_state = None
        self.refresh()
        fade_in_toplevel(win)
        win.after(60, lambda: win.winfo_exists() and (win.lift(), win.focus_force()))

    # ----------------------------------------------------------------------- #
    def _job(self):
        return self.app.manager.get_job(self.job_id)

    def _error_info(self, job):
        return ErrorInfo(job.error_message or "", job.error_hint or "", job.error_details or "")

    def _toggle_technical(self):
        self._tech_visible = not self._tech_visible
        if self._tech_visible:
            self.tech_box.pack(fill="x", padx=12, pady=(0, 10))
            self.tech_btn.configure(text="Hide technical details")
        else:
            self.tech_box.pack_forget()
            self.tech_btn.configure(text="View technical details")

    def _copy_error(self):
        job = self._job()
        if job is None:
            return
        text = self._error_info(job).as_text() + f"\n\nURL: {job.url}\nEngine: {job.engine}\nFormat: {job.quality_label}"
        self.win.clipboard_clear()
        self.win.clipboard_append(text)
        self.app.toast("info", "Error copied to the clipboard")

    def _render_buttons(self, job):
        state = (job.status, bool(job.result_path), job.from_history)
        if state == self._last_buttons_state:
            return
        self._last_buttons_state = state
        for child in self.buttons.winfo_children():
            child.destroy()
        act = self.app.act
        if job.status in RUNNING_STATUSES or job.status in (STATUS_QUEUED, STATUS_RETRYING):
            glass_button(self.buttons, "Pause", lambda: act("pause", [job]), width=80, height=30).pack(side="left", padx=(0, 6))
        if job.status == STATUS_PAUSED:
            glass_button(self.buttons, "Resume", lambda: act("resume", [job]), variant="primary", width=80,
                         height=30).pack(side="left", padx=(0, 6))
        if job.status in RUNNING_STATUSES or job.status in (STATUS_QUEUED, STATUS_PAUSED, STATUS_RETRYING):
            glass_button(self.buttons, "Cancel", lambda: act("cancel", [job]), variant="danger", width=80,
                         height=30).pack(side="left", padx=(0, 6))
        if job.status in (STATUS_FAILED, STATUS_CANCELLED) and not job.from_history:
            glass_button(self.buttons, "Retry", lambda: act("retry", [job]), variant="primary", width=80,
                         height=30).pack(side="left", padx=(0, 6))
        if job.result_path and os.path.exists(job.result_path):
            glass_button(self.buttons, "Open file", lambda: open_file(job.result_path, self.win), width=90,
                         height=30).pack(side="left", padx=(0, 6))
        glass_button(self.buttons, "Show in folder", lambda: open_folder(job.download_dir, job.result_path), width=120,
                     height=30).pack(side="left", padx=(0, 6))

    def _draw_graph(self, samples):
        c, p = self.canvas, self._canvas_palette
        c.delete("all")
        if len(samples) < 2:
            c.create_text(8, 32, anchor="w", fill=p["muted"], text="The speed graph appears while downloading.",
                          font=("Segoe UI", 9))
            return
        values = [s[1] for s in samples]
        peak = max(values) or 1.0
        w, h = max(c.winfo_width(), 400), 64
        step = w / max(len(values) - 1, 1)
        points = []
        for i, v in enumerate(values):
            points.extend((i * step, h - (v / peak) * (h - 12) - 4))
        c.create_line(*points, fill=p["accent"], width=2, smooth=True)
        c.create_text(6, 9, anchor="w", fill=p["muted"], text=f"Speed (peak {_format_speed(peak)})", font=("Segoe UI", 8))

    def refresh(self):
        if not self.win.winfo_exists():
            self.app.details_windows.pop(self.job_id, None)
            return
        job = self._job()
        if job is None:
            self.title_label.configure(text="(removed from the list)")
            self.status_label.configure(text="")
            return
        self.title_label.configure(text=job.display_name)
        status = job.status
        if job.message and job.message not in (status, f"{status}."):
            status = f"{status} — {job.message}"
        self.status_label.configure(text=status, text_color=COLORS["danger"] if job.status == STATUS_FAILED else COLORS["primary"])
        self.progress.set(max(0.0, min(1.0, (job.progress or 0) / 100)))
        size_line = f"{human_size(job.downloaded_bytes, '0 B')} of {human_size(job.total_bytes)}"
        if job.status == STATUS_COMPLETED:
            size_line = human_size(job.total_bytes or job.downloaded_bytes, "")
        speed = job.speed_text if job.status == STATUS_DOWNLOADING or job.speed_text.startswith("↑") else ""
        bits = [b for b in (speed, size_line, f"ETA {job.eta_text}" if job.eta_text else "") if b]
        self.speed_label.configure(text="     ".join(bits))
        self._render_buttons(job)

        if job.status == STATUS_FAILED and job.error_message:
            info = self._error_info(job)
            self.error_reason.configure(text=f"Reason: {info.reason}")
            self.error_hint.configure(text=f"Suggested action: {info.suggestion}" if info.suggestion else "")
            self.tech_box.configure(state="normal")
            self.tech_box.delete("1.0", "end")
            self.tech_box.insert("1.0", info.technical or "(no further details)")
            self.tech_box.configure(state="disabled")
            if not self.error_card.winfo_ismapped():
                self.error_card.pack(fill="x", pady=(0, 10), before=self.info_label)
        elif self.error_card.winfo_ismapped():
            self.error_card.pack_forget()

        lines = [
            f"Engine: {ENGINE_LABEL.get(job.engine, job.engine or '—')}",
            f"Source: {job.url}",
            f"Format: {job.quality_label or '—'}",
            f"Folder: {job.download_dir}",
        ]
        if job.result_path:
            lines.append(f"File: {job.result_path}")
        if len(job.result_paths) > 1:
            lines.append(f"Files: {len(job.result_paths)}")
        if job.playlist_title:
            lines.append(f"Playlist: {job.playlist_title}" + (f" (item {job.playlist_index})" if job.playlist_index else ""))
        if job.engine == "torrent" and job.peers is not None:
            lines.append(f"Peers: {job.peers}   Seeds: {job.seeds}   Upload: {_format_speed(job.upload_bps) or '0 B/s'}")
        lines += [
            f"Resumable: {'yes' if job.resumable else 'no — the server does not support resuming'}",
            f"Retries: {job.retries}",
            f"Requested from: {'browser extension' if job.source == 'extension' else 'this app'}",
        ]
        self.info_label.configure(text="\n".join(lines))
        self._draw_graph(job.speed_history)
        delay = 500 if job.status in RUNNING_STATUSES or job.status in (STATUS_QUEUED, STATUS_RETRYING) else 2000
        self.win.after(delay, self.refresh)
