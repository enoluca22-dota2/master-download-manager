"""Settings window. Every control here maps to a setting that an engine,
the queue, or the app actually reads — nothing is decorative."""

import os
import platform
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import customtkinter as ctk

from .. import __version__, bandwidth, notifications, startup, tools, tray
from ..config import (
    APP_NAME, DATA_DIR, DEFAULT_SETTINGS, INSTALL_DIR, LICENSES_DIR, LOG_DIR, NOTICES_FILE, RESOURCE_DIR,
)
from ..providers.media import DEFAULT_TEMPLATE, RESOLUTION_LABELS, sanitize_template
from .common import open_file, open_folder, truncate
from .theme import ACCENTS, APPEARANCE_MODES, COLORS, fade_in_toplevel, font, glass_button, glass_card, glass_entry, glass_segmented

SECTIONS = ("General", "Appearance", "Downloads", "Bandwidth", "Network", "Media", "Audio", "Subtitles", "Torrent",
            "Queues & Scheduler", "Browser Integration", "Authentication", "History & Storage", "Advanced", "Updates",
            "About", "Licenses")
COOKIE_BROWSERS = ("firefox", "chrome", "edge", "brave", "opera", "vivaldi", "chromium")
_PROXY_SCHEMES = ("http://", "https://", "socks4://", "socks4a://", "socks5://", "socks5h://")
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def extension_folder(browser):
    """Where the unpacked browser extension lives (packaged app or source)."""
    for candidate in (os.path.join(INSTALL_DIR, "extensions", browser), os.path.join(RESOURCE_DIR, "extensions", browser),
                      os.path.join(INSTALL_DIR, f"extension-{browser}")):
        if os.path.isdir(candidate):
            return candidate
    return None


class SettingsWindow:
    def __init__(self, app, section="General"):
        self.app = app
        self.s = app.settings
        self.vars = {}
        self.win = win = ctk.CTkToplevel(app.root)
        win.title(f"Settings — {APP_NAME}")
        win.geometry("900x680")
        win.minsize(820, 560)
        win.transient(app.root)
        app.apply_glass_window(win)

        outer = ctk.CTkFrame(win, fg_color="transparent")
        outer.pack(fill="both", expand=True, padx=16, pady=16)
        outer.grid_columnconfigure(1, weight=1)
        outer.grid_rowconfigure(0, weight=1)

        nav = glass_card(outer, radius="lg")
        nav.grid(row=0, column=0, sticky="ns", padx=(0, 12))
        self.nav_buttons = {}
        for name in SECTIONS:
            b = glass_button(nav, name, lambda n=name: self.show(n), variant="ghost", width=170, height=32,
                             anchor="w", weight="normal")
            b.pack(padx=8, pady=(8 if name == SECTIONS[0] else 1, 0))
            self.nav_buttons[name] = b

        self.page = ctk.CTkScrollableFrame(outer, fg_color="transparent", corner_radius=0,
                                           scrollbar_button_color=COLORS["border"],
                                           scrollbar_button_hover_color=COLORS["primary"])
        self.page.grid(row=0, column=1, sticky="nsew")
        self.page.grid_columnconfigure(0, weight=1)

        footer = ctk.CTkFrame(outer, fg_color="transparent")
        footer.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        glass_button(footer, "Save", self.save, variant="primary", width=120, height=36).pack(side="right")
        glass_button(footer, "Close", win.destroy, width=100, height=36).pack(side="right", padx=8)
        self.footer_note = ctk.CTkLabel(footer, text="", text_color=COLORS["muted"], font=font(12))
        self.footer_note.pack(side="left")

        self.frames = {}
        for name in SECTIONS:
            frame = ctk.CTkFrame(self.page, fg_color="transparent")
            getattr(self, "_build_" + name.lower().replace(" & ", "_").replace(" ", "_"))(frame)
            self.frames[name] = frame
        self.show(section if section in SECTIONS else "General")
        fade_in_toplevel(win)
        win.after(60, lambda: win.winfo_exists() and (win.lift(), win.focus_force()))

    # ---------------------------------------------------------------- utils -- #
    def show(self, name):
        for n, frame in self.frames.items():
            frame.pack_forget()
            self.nav_buttons[n].configure(fg_color="transparent", text_color=COLORS["muted"])
        self.frames[name].pack(fill="both", expand=True)
        self.nav_buttons[name].configure(fg_color=COLORS["surface_hover"], text_color=COLORS["text"])

    def _card(self, parent, title, note=None):
        card = glass_card(parent, radius="lg")
        card.pack(fill="x", pady=(0, 12))
        ctk.CTkLabel(card, text=title, font=font(15, "bold")).pack(anchor="w", padx=18, pady=(14, 2))
        if note:
            ctk.CTkLabel(card, text=note, font=font(11), text_color=COLORS["muted"], wraplength=600,
                         justify="left").pack(anchor="w", padx=18)
        body = ctk.CTkFrame(card, fg_color="transparent")
        body.pack(fill="x", padx=18, pady=(8, 14))
        body.grid_columnconfigure(1, weight=1)
        return body

    def _bool(self, parent, key, text, row, default=False, state="normal"):
        var = tk.BooleanVar(value=bool(self.s.get(key, default)))
        self.vars[key] = var
        ctk.CTkCheckBox(parent, text=text, variable=var, font=font(12), state=state).grid(
            row=row, column=0, columnspan=3, sticky="w", pady=3)
        return var

    def _text(self, parent, key, label, row, width=320, placeholder=""):
        value = self.s.get(key, "")
        var = tk.StringVar(value="" if value is None else str(value))  # keep 0 as "0", not ""
        self.vars[key] = var
        ctk.CTkLabel(parent, text=label, font=font(12)).grid(row=row, column=0, sticky="w", pady=4, padx=(0, 12))
        entry = glass_entry(parent, textvariable=var, height=32, width=width, placeholder=placeholder)
        entry.grid(row=row, column=1, sticky="w", pady=4)
        return var, entry

    def _choice(self, parent, key, label, values, row, width=200, labels=None):
        labels = labels or values
        current = self.s.get(key)
        var = tk.StringVar(value=labels[values.index(current)] if current in values else labels[0])
        self.vars[key] = (var, values, labels)
        ctk.CTkLabel(parent, text=label, font=font(12)).grid(row=row, column=0, sticky="w", pady=4, padx=(0, 12))
        ctk.CTkOptionMenu(parent, values=list(labels), variable=var, width=width).grid(row=row, column=1, sticky="w", pady=4)
        return var

    def _folder(self, parent, key, label, row, allow_empty=False):
        var = tk.StringVar(value=self.s.get(key, "") or "")
        self.vars[key] = var
        ctk.CTkLabel(parent, text=label, font=font(12)).grid(row=row, column=0, sticky="w", pady=4, padx=(0, 12))
        entry = glass_entry(parent, textvariable=var, height=32, width=380,
                            placeholder="(same as the main download folder)" if allow_empty else "")
        entry.grid(row=row, column=1, sticky="w", pady=4)

        def browse():
            folder = filedialog.askdirectory(parent=self.win, initialdir=var.get() or self.s.get("download_dir"))
            if folder:
                var.set(os.path.normpath(folder))
        glass_button(parent, "Browse", browse, width=80, height=30).grid(row=row, column=2, padx=8)
        return var

    # ------------------------------------------------------------- sections -- #
    def _build_general(self, f):
        b = self._card(f, "General")
        self._bool(b, "minimize_to_tray", "Closing the window keeps the app running in the system tray", 2, True,
                   state="normal" if tray.tray_supported() else "disabled")
        self._bool(b, "notifications_enabled", "Show Windows notifications when downloads finish or fail", 3, True,
                   state="normal" if notifications.notifications_supported() else "disabled")
        self._bool(b, "clipboard_monitor_enabled", "Offer to download links I copy (media sites, files, magnets)", 4)
        self.start_with_windows = tk.BooleanVar(value=startup.is_enabled())
        ctk.CTkCheckBox(b, text=f"Start {APP_NAME} with Windows (minimized)", variable=self.start_with_windows,
                        font=font(12)).grid(row=5, column=0, columnspan=3, sticky="w", pady=3)
        b = self._card(f, "Reset", "Put every setting back to its default. Your downloads, history, queues and the "
                       "browser extension pairing (security token) are kept.")
        glass_button(b, "Reset all settings…", self._reset_settings, width=170, height=30).grid(row=0, column=0, sticky="w")

    def _reset_settings(self):
        if not messagebox.askyesno("Reset settings", "Reset all settings to their defaults?\n\nDownloads, history, "
                                   "queues and the browser extension pairing are not affected.", parent=self.win):
            return
        keep = {k: self.s.get(k) for k in ("api_token", "api_port")}
        defaults = dict(DEFAULT_SETTINGS)
        defaults.update(keep)
        self.s.update(defaults)
        self.app.settings_changed()
        self.win.destroy()
        self.app.set_appearance_mode(defaults["appearance_mode"], defaults["accent_color"])
        self.app.toast("success", "Settings reset to defaults.")

    def _build_appearance(self, f):
        b = self._card(f, "Theme", "Changes apply immediately. Black is a true-black dark theme for OLED screens; "
                       "System follows Windows' light/dark setting.")
        ctk.CTkLabel(b, text="Mode", font=font(12)).grid(row=0, column=0, sticky="w", padx=(0, 12), pady=4)
        self.mode_seg = glass_segmented(b, list(APPEARANCE_MODES), command=lambda m: self._apply_theme(mode=m))
        self.mode_seg.set(self.app.appearance_mode)
        self.mode_seg.grid(row=0, column=1, sticky="w", pady=4)
        ctk.CTkLabel(b, text="Accent colour", font=font(12)).grid(row=1, column=0, sticky="w", padx=(0, 12), pady=4)
        self.accent_var = tk.StringVar(value=self.s.get("accent_color", "Indigo"))
        ctk.CTkOptionMenu(b, values=list(ACCENTS), variable=self.accent_var, width=160,
                          command=lambda a: self._apply_theme(accent=a)).grid(row=1, column=1, sticky="w", pady=4)
        self._bool(b, "mica_backdrop", "Mica title bar (Windows 11)", 2)

    def _apply_theme(self, mode=None, accent=None):
        self.app.set_appearance_mode(mode or self.app.appearance_mode, accent or self.accent_var.get())
        self.app.call_in_main(self.app.open_settings, "Appearance")
        self.win.destroy()

    def _build_bandwidth(self, f):
        kbps, reason = bandwidth.manager.effective_kbps()
        b = self._card(f, "Speed limits", "The global limit is shared fairly by all downloads at once. The tray's "
                       "“Limit download speed” switches Limited mode on and off; scheduled speed profiles can lower "
                       "the cap at certain times. The lowest active limit wins." +
                       (f"  Right now: {kbps:,} KB/s ({reason})." if kbps else "  Right now: no limit."))
        self._text(b, "global_speed_limit_kbps", "Global limit (KB/s, 0 = unlimited)", 0, width=100)
        self._text(b, "limited_mode_kbps", "Limited mode (KB/s)", 1, width=100)
        self._text(b, "speed_limit_kbps", "Default limit per download (KB/s, 0 = unlimited)", 2, width=100)
        glass_button(b, "Speed profiles…", lambda: self.app.open_queues() or self._open_profiles(), width=140,
                     height=30).grid(row=3, column=0, sticky="w", pady=(8, 0))
        ctk.CTkLabel(b, text="Torrent upload/download limits: Torrent section. Media sites (yt-dlp) and torrents get "
                     "an equal share of the global limit when it is set.", font=font(11), text_color=COLORS["muted"],
                     wraplength=600, justify="left").grid(row=4, column=0, columnspan=3, sticky="w", pady=(8, 0))

    def _open_profiles(self):
        w = getattr(self.app, "_queues_window", None)
        if w is not None and w.win.winfo_exists():
            w.page.set("Speed profiles")
            w._show_page()

    def _build_queues_scheduler(self, f):
        queues = self.app.manager.queues.all()
        b = self._card(f, "Queues", "The Main queue always runs — downloads start as soon as a slot is free. Other "
                       "queues run when you start them or at their scheduled time, each with its own limits and an "
                       "optional action when it finishes (notify, exit, sleep, shut down — always after a countdown).")
        for i, q in enumerate(queues[:8]):
            ctk.CTkLabel(b, text=f"{'●' if q.running else '○'}  {q.name}", font=font(12),
                         text_color=COLORS["success"] if q.running else COLORS["muted"]).grid(row=i, column=0, sticky="w")
        glass_button(b, "Open Queues & Scheduler…", self.app.open_queues, width=200, height=30).grid(
            row=len(queues[:8]), column=0, sticky="w", pady=(10, 0))

    def _build_history_storage(self, f):
        count = self.app.history.count()
        b = self._card(f, "Download history", f"{count:,} entries. Removing history never deletes downloaded files.")
        glass_button(b, "Export…", self._export_history, width=110, height=30).grid(row=0, column=0, sticky="w")
        glass_button(b, "Import…", self._import_history, width=110, height=30).grid(row=0, column=1, sticky="w", padx=8)
        glass_button(b, "Clear history…", self._clear_history, variant="ghost", width=130, height=30).grid(
            row=0, column=2, sticky="w")
        b = self._card(f, "Privacy", "Master Download Manager only contacts the sites you download from. The browser "
                       "extension talks to it over 127.0.0.1 with a security token; it never sends cookies or browsing "
                       "history. Checking for updates contacts GitHub only when you ask (or weekly, if you turn that "
                       "on in Updates).")
        ctk.CTkLabel(b, text=f"Your data folder: {DATA_DIR}", font=font(11), text_color=COLORS["muted"]).grid(
            row=0, column=0, sticky="w")

    def _export_history(self):
        path = filedialog.asksaveasfilename(parent=self.win, defaultextension=".json", initialfile="mdm-history.json",
                                            filetypes=[("JSON", "*.json"), ("CSV (spreadsheets)", "*.csv")])
        if not path:
            return
        try:
            n = self.app.history.export(path)
        except (OSError, ValueError) as e:
            messagebox.showerror("Export history", str(e), parent=self.win)
            return
        self.footer_note.configure(text=f"Exported {n:,} entries.", text_color=COLORS["success"])

    def _import_history(self):
        path = filedialog.askopenfilename(parent=self.win, filetypes=[("History export", "*.json *.csv")])
        if not path:
            return
        try:
            n = self.app.history.import_file(path)
        except (OSError, ValueError) as e:
            messagebox.showerror("Import history", str(e), parent=self.win)
            return
        self.footer_note.configure(text=f"Imported {n:,} new entries — restart to see them in the list.",
                                   text_color=COLORS["success"])

    def _clear_history(self):
        if messagebox.askyesno("Clear history", "Remove every entry from the download history?\n\nDownloaded files "
                               "stay on your disk. You can't undo this (export first if unsure).", parent=self.win,
                               icon="warning", default="no"):
            self.app.history.clear()
            for job in [j for j in self.app.manager.list_jobs() if j.from_history]:
                try:
                    self.app.manager.remove_job(job.job_id)
                except (KeyError, ValueError):
                    pass
            self.footer_note.configure(text="History cleared.", text_color=COLORS["success"])

    def _build_downloads(self, f):
        b = self._card(f, "Folders")
        self._folder(b, "download_dir", "Download folder", 0)
        self._folder(b, "video_dir", "Videos", 1, allow_empty=True)
        self._folder(b, "audio_dir", "Audio", 2, allow_empty=True)
        self._folder(b, "torrent_dir", "Torrents", 3, allow_empty=True)
        b = self._card(f, "File names", "Media file names are built from this template. Fields: %(title)s, %(id)s, "
                       "%(uploader)s, %(upload_date)s, %(channel)s, %(ext)s is added automatically. A '/' creates a "
                       "subfolder. Existing files are never overwritten.")
        var, _ = self._text(b, "filename_template", "Template", 0, width=360, placeholder=DEFAULT_TEMPLATE)
        self.template_preview = ctk.CTkLabel(b, text="", font=font(11), text_color=COLORS["muted"])
        self.template_preview.grid(row=1, column=1, sticky="w")
        var.trace_add("write", lambda *_: self._preview_template())
        self._preview_template()
        b = self._card(f, "Queue")
        self._choice(b, "max_concurrent_downloads", "Simultaneous downloads", list(range(1, 11)), 0, width=90,
                     labels=[str(i) for i in range(1, 11)])
        self._choice(b, "max_retries", "Automatic retries on network errors", list(range(0, 11)), 1, width=90,
                     labels=[str(i) for i in range(0, 11)])
        self._bool(b, "restore_queue_on_start", "Bring back unfinished downloads (paused) when the app starts", 2, True)

    def _preview_template(self):
        raw = self.vars["filename_template"].get()
        clean = sanitize_template(raw)
        sample = clean.replace("%(title)s", "Big Buck Bunny").replace("%(id)s", "aqz-KE-bpKQ") \
            .replace("%(uploader)s", "Blender").replace("%(channel)s", "Blender").replace("%(upload_date)s", "20140410")
        note = "" if clean == (raw.strip().replace("\\", "/") or DEFAULT_TEMPLATE) else "  (unsafe parts removed)"
        self.template_preview.configure(text=f"Example: {sample}.mp4{note}")

    def _build_media(self, f):
        b = self._card(f, "Defaults for media sites", "Used for links sent from the browser, playlists and quick adds. "
                       "The Add dialog always shows what a specific video really offers.")
        self._choice(b, "default_mode", "Download", ["Video", "Audio"], 0, labels=["Video", "Audio only"])
        qualities = ["Best available"] + list(RESOLUTION_LABELS)
        self._choice(b, "default_quality", "Video quality (nearest available)", qualities, 1, width=220)
        self._choice(b, "default_container", "Container", ["mp4", "mkv", "original"], 2, width=120,
                     labels=["MP4", "MKV", "Original (no remux)"])
        self._bool(b, "prefer_h264", "Prefer H.264/AAC (plays on every device; may cap quality at 1080p)", 3)
        has_ffmpeg = tools.find_ffmpeg(self.s.get("ffmpeg_path", "")) is not None
        state = "normal" if has_ffmpeg else "disabled"
        self._bool(b, "embed_metadata", "Embed title/artist/date metadata", 4, True, state=state)
        self._bool(b, "embed_chapters", "Embed chapters", 5, True, state=state)
        self._bool(b, "embed_thumbnail", "Embed the thumbnail as cover art (MP4, MKV, M4A, MP3)", 6, state=state)

    def _build_audio(self, f):
        codecs = tools.available_audio_codecs(self.s.get("ffmpeg_path", ""))
        values = ["original"] + codecs
        labels = ["Original (no conversion)"] + [tools.AUDIO_CODECS[c][0] for c in codecs]
        b = self._card(f, "Audio extraction", "Only formats the bundled FFmpeg can actually encode are listed.")
        self._choice(b, "default_audio_format", "Format", values, 0, width=220, labels=labels)
        kbps = ["128 kbps", "192 kbps", "256 kbps", "320 kbps (Maximum)"]
        self._choice(b, "default_audio_quality", "Bitrate (MP3/AAC/Opus/Vorbis)", kbps, 1, width=220)

    def _build_subtitles(self, f):
        b = self._card(f, "Subtitles", "Pre-selected in the Add dialog when a video offers them.")
        self._text(b, "subtitle_languages", "Languages (comma-separated codes)", 0, width=200, placeholder="en, de")
        self._choice(b, "subtitle_format", "Format", ["srt", "vtt", "ass"], 1, width=100, labels=["SRT", "VTT", "ASS"])
        self._bool(b, "subtitle_include_auto", "Also pre-select automatically generated captions", 2)
        self._bool(b, "subtitle_embed", "Embed subtitles into the video file (MP4/MKV)", 3)

    def _build_network(self, f):
        b = self._card(f, "Connections", "More connections per file help mostly when a server limits the speed of "
                       "each connection; idle connections automatically take over the slowest part of the file.")
        self._choice(b, "http_connections", "Connections per direct download", list(range(1, 17)), 1, width=90,
                     labels=[str(i) for i in range(1, 17)])
        self._bool(b, "http_adaptive_connections", "Adaptive: start with one connection and add more only while they "
                   "make the download faster (recommended)", 5, True)
        self._choice(b, "concurrent_fragments", "Parallel fragments for streaming media", list(range(1, 17)), 2,
                     width=90, labels=[str(i) for i in range(1, 17)])
        self._text(b, "download_timeout", "Network timeout (seconds)", 3, width=100)
        b = self._card(f, "Proxy", "Applies to media and direct downloads (not to BitTorrent peers). "
                       "Examples: http://host:8080, socks5://127.0.0.1:1080")
        self._text(b, "proxy", "Proxy URL", 0, width=360, placeholder="(none)")

    def _build_authentication(self, f):
        b = self._card(f, "Sign-in cookies (media sites)",
                        "Some videos need you to be signed in (age-restricted, members-only, private you have access "
                        "to). Cookies are read locally on this PC only, never logged and never uploaded anywhere. "
                        "Note: Chrome/Edge often lock their cookie store while running — Firefox works most reliably.")
        self._choice(b, "cookies_mode", "Use cookies", ["none", "browser", "file"], 0, width=220,
                     labels=["Don't use cookies", "From a browser", "From a cookies.txt file"])
        self._choice(b, "cookies_browser", "Browser", list(COOKIE_BROWSERS), 1, width=160,
                     labels=[x.capitalize() for x in COOKIE_BROWSERS])
        var, _ = self._text(b, "cookies_file", "cookies.txt file", 2, width=360)

        def browse():
            path = filedialog.askopenfilename(parent=self.win, filetypes=[("Cookie files", "*.txt"), ("All files", "*.*")])
            if path:
                var.set(os.path.normpath(path))
        glass_button(b, "Browse", browse, width=80, height=30).grid(row=2, column=2, padx=8)

    def _build_torrent(self, f):
        b = self._card(f, "BitTorrent")
        self._text(b, "torrent_max_connections", "Maximum peer connections", 0, width=100)
        self._text(b, "torrent_download_limit_kbps", "Download limit (KB/s, 0 = unlimited)", 1, width=100)
        self._text(b, "torrent_upload_limit_kbps", "Upload limit (KB/s, 0 = unlimited)", 2, width=100)
        self._text(b, "torrent_seed_ratio", "Keep seeding until ratio (0 = stop when complete)", 3, width=100)
        self._text(b, "torrent_listen_port", "Listening port (0 = automatic, needs restart)", 4, width=100)
        ctk.CTkLabel(b, text="Torrent download folder: Settings → Downloads → Torrents.", font=font(11),
                     text_color=COLORS["muted"]).grid(row=5, column=0, columnspan=3, sticky="w", pady=(6, 0))

    def _build_browser_integration(self, f):
        b = self._card(f, "Local connection", "The browser extension talks to this app over 127.0.0.1 only, "
                       "authenticated with the security token below.")
        self._bool(b, "api_enabled", "Allow the browser extension to connect (needs restart)", 0, True)
        self._text(b, "api_port", "Port", 1, width=100)
        ctk.CTkLabel(b, text="Security token", font=font(12)).grid(row=2, column=0, sticky="w", pady=4)
        row = ctk.CTkFrame(b, fg_color="transparent")
        row.grid(row=2, column=1, columnspan=2, sticky="w")
        self.token_entry = glass_entry(row, height=32, width=260, show="•")
        self.token_entry.insert(0, self.s.get("api_token", ""))
        self.token_entry.configure(state="readonly")
        self.token_entry.pack(side="left")
        glass_button(row, "Show", self._toggle_token, width=56, height=30).pack(side="left", padx=4)
        glass_button(row, "Copy", self._copy_token, width=56, height=30).pack(side="left", padx=4)
        glass_button(row, "New token", self._regenerate_token, width=86, height=30).pack(side="left", padx=4)
        self.conn_label = ctk.CTkLabel(b, text="", font=font(12), text_color=COLORS["muted"])
        self.conn_label.grid(row=3, column=1, sticky="w", pady=(8, 0))
        glass_button(b, "Test connection", self._test_connection, width=140, height=30).grid(row=3, column=0, sticky="w", pady=(8, 0))
        b = self._card(f, "Capture browser downloads", "When on, the extension hands file downloads that match these "
                       "types to MDM instead of the browser's own downloader. Off by default. If MDM isn't running, "
                       "the browser downloads the file normally.")
        self._bool(b, "browser_capture_enabled", "Let the extension send matching browser downloads to MDM", 0)
        self._text(b, "browser_capture_types", "File types (space-separated)", 1, width=420)
        self._text(b, "browser_capture_min_kb", "Only files larger than (KB)", 2, width=100)
        self._text(b, "browser_capture_blacklist", "Never capture from (sites, space-separated)", 3, width=420,
                   placeholder="e.g. mybank.com intranet.local")
        b = self._card(f, "Install the extension",
                       "Chrome / Edge / Brave: open chrome://extensions (or edge://extensions), turn on Developer "
                       "mode, click “Load unpacked” and choose the Chrome extension folder. Firefox: open "
                       "about:debugging → This Firefox → Load Temporary Add-on → choose manifest.json in the Firefox "
                       "folder. Then open the extension's settings and paste the security token.")
        for i, browser in enumerate(("chrome", "firefox")):
            folder = extension_folder(browser)
            glass_button(b, f"Open {browser.capitalize()} extension folder", (lambda p=folder: open_folder(p)),
                         width=230, height=30, state="normal" if folder else "disabled").grid(row=0, column=i, sticky="w", padx=(0, 8))

    def _build_advanced(self, f):
        b = self._card(f, "Tools", "Master Download Manager ships with its own FFmpeg and JavaScript runtime. "
                       "Only set a custom FFmpeg if you know you need one.")
        var, _ = self._text(b, "ffmpeg_path", "Custom FFmpeg (file or folder)", 0, width=360, placeholder="(use the bundled FFmpeg)")

        def browse():
            path = filedialog.askopenfilename(parent=self.win, filetypes=[("ffmpeg.exe", "ffmpeg.exe"), ("Programs", "*.exe")])
            if path:
                var.set(os.path.normpath(path))
        glass_button(b, "Browse", browse, width=80, height=30).grid(row=0, column=2, padx=8)
        status = tools.tool_status(self.s.get("ffmpeg_path", ""))
        lines = [
            ("FFmpeg", status["ffmpeg"], status["ffmpeg_bundled"]),
            ("FFprobe", status["ffprobe"], status["ffmpeg_bundled"]),
            ("JavaScript runtime (deno)", status["deno"], status["deno_bundled"]),
        ]
        for i, (name, path, bundled) in enumerate(lines, start=1):
            text = f"● {name}: {'bundled' if bundled else 'found'} — {truncate(path, 60)}" if path else f"⚠ {name}: not found"
            ctk.CTkLabel(b, text=text, font=font(12), text_color=COLORS["success"] if path else COLORS["warning"]).grid(
                row=i, column=0, columnspan=3, sticky="w", pady=2)
        b = self._card(f, "Data and logs", f"Settings, history, queue and logs are kept in {DATA_DIR}")
        glass_button(b, "Open logs folder", lambda: open_folder(LOG_DIR), width=150, height=30).grid(row=0, column=0, sticky="w")
        glass_button(b, "Open data folder", lambda: open_folder(DATA_DIR), width=150, height=30).grid(row=0, column=1, sticky="w", padx=8)

    def _build_updates(self, f):
        import datetime
        b = self._card(f, "Updates", "Updates come from the project's GitHub releases. A new version is only ever "
                       "installed after you click Update Now; it's downloaded, checked against the release's signed "
                       "manifest (size + SHA-256) and installed over this one — settings, history, queues and "
                       "unfinished downloads are kept.")
        self._bool(b, "update_auto_check", "Automatically check for updates (at most once a day)", 0, True)
        self._bool(b, "update_check_on_start", "Also check every time the app starts", 1)
        self._choice(b, "update_channel", "Channel", ["stable", "beta"], 2, width=220,
                     labels=["Stable releases", "Beta (pre-releases too)"])
        last = float(self.s.get("update_last_check") or 0)
        when = datetime.datetime.fromtimestamp(last).strftime("%d %b %Y, %H:%M") if last else "never"
        status = self.s.get("update_last_status") or "—"
        self.update_label = ctk.CTkLabel(b, text=f"Current version: {__version__}\nLast check: {when}\nStatus: {status}"
                                         + self._last_update_text(), font=font(12), text_color=COLORS["muted"],
                                         wraplength=560, justify="left")
        self.update_label.grid(row=3, column=1, sticky="w", pady=(10, 0))
        self.update_btn = glass_button(b, "Check Now", self._check_updates, variant="primary", width=130, height=30)
        self.update_btn.grid(row=3, column=0, sticky="nw", pady=(10, 0))
        b = self._card(f, "Advanced", "Only if you publish your own builds: a custom manifest URL (not signed, so "
                       "never installed automatically — you run the verified installer yourself).")
        self._choice(b, "update_source", "Source", ["github", "manifest"], 0, width=220,
                     labels=["GitHub releases", "Custom manifest URL"])
        self._text(b, "update_manifest_url", "Manifest URL", 1, width=420, placeholder="(only for a custom manifest)")

    def _last_update_text(self):
        import json
        from ..update_install import RESULT_FILE
        try:
            with open(RESULT_FILE, encoding="utf-8") as fh:
                r = json.load(fh)
            return f"\nLast update: {r.get('from')} → {r.get('to')} ({'succeeded' if r.get('ok') else 'did not complete'}, " \
                   f"{r.get('finished', '')[:16].replace('T', ' ')})"
        except (OSError, ValueError):
            return ""

    def _build_about(self, f):
        b = self._card(f, APP_NAME)
        ctk.CTkLabel(b, text=f"Version {__version__}", font=font(13, "bold")).grid(row=0, column=0, sticky="w")
        self.about_label = ctk.CTkLabel(b, text="Collecting component versions…", font=font(12), justify="left",
                                        text_color=COLORS["muted"])
        self.about_label.grid(row=1, column=0, sticky="w", pady=(6, 0))
        ctk.CTkLabel(b, text="Built on open-source components: the media engine uses yt-dlp (public domain), "
                     "torrents use libtorrent (BSD), conversion uses FFmpeg (LGPL). See Licenses.",
                     font=font(12), wraplength=600, justify="left").grid(row=2, column=0, sticky="w", pady=(10, 0))
        ctk.CTkLabel(b, text="Only download content you have the right to download. DRM-protected media is not supported.",
                     font=font(11), text_color=COLORS["muted"], wraplength=600, justify="left").grid(row=3, column=0, sticky="w", pady=(8, 0))
        threading.Thread(target=self._collect_versions, daemon=True).start()

    def _collect_versions(self):
        lines = [f"Python {platform.python_version()} ({'packaged' if getattr(sys, 'frozen', False) else 'source'})"]
        try:
            from yt_dlp.version import __version__ as ytv
            lines.append(f"Media engine: yt-dlp {ytv}")
        except ImportError:
            pass
        try:
            import libtorrent
            lines.append(f"Torrent engine: libtorrent {libtorrent.__version__}")
        except ImportError:
            lines.append("Torrent engine: libtorrent missing")
        for name, path in (("FFmpeg", tools.find_ffmpeg(self.s.get("ffmpeg_path", ""))), ("deno", tools.find_deno())):
            if path:
                try:
                    out = subprocess.run([path, "-version" if name == "FFmpeg" else "--version"], capture_output=True,
                                         text=True, timeout=10, creationflags=_NO_WINDOW).stdout.splitlines()
                    lines.append(out[0] if out else name)
                except (OSError, subprocess.SubprocessError):
                    lines.append(f"{name}: present")
        self.app.call_in_main(lambda: self.about_label.winfo_exists() and self.about_label.configure(text="\n".join(lines)))

    def _build_licenses(self, f):
        b = self._card(f, "Third-party components", "Full license texts ship with the app in the licenses folder.")
        frame = tk.Frame(b, bd=0, highlightthickness=0)
        frame.grid(row=0, column=0, columnspan=3, sticky="ew")
        tree = ttk.Treeview(frame, columns=("name", "version", "license"), show="headings", height=12)
        for col, head, w in (("name", "Component", 300), ("version", "Version", 120), ("license", "License", 230)):
            tree.heading(col, text=head)
            tree.column(col, width=w, anchor="w")
        tree.pack(side="left", fill="x", expand=True)
        from .licenses_window import load_inventory
        try:
            for item in sorted(load_inventory(), key=lambda c: str(c.get("name", "")).lower()):
                tree.insert("", "end", values=(item.get("name"), item.get("version"), item.get("license")))
        except (OSError, ValueError):
            tree.insert("", "end", values=("(license inventory not found)", "", ""))
        glass_button(b, "View licenses…", self.app.open_licenses, variant="primary", width=150,
                     height=30).grid(row=1, column=0, sticky="w", pady=(10, 0))
        glass_button(b, "Open licenses folder", lambda: open_folder(LICENSES_DIR), width=170, height=30).grid(row=1, column=1, sticky="w", pady=(10, 0))
        glass_button(b, "Third-party notices", lambda: open_file(NOTICES_FILE, self.win), width=170, height=30).grid(row=1, column=2, sticky="w", pady=(10, 0))

    # -------------------------------------------------------------- actions -- #
    def _toggle_token(self):
        self.token_entry.configure(show="" if self.token_entry.cget("show") else "•")

    def _copy_token(self):
        self.win.clipboard_clear()
        self.win.clipboard_append(self.s.get("api_token", ""))
        self.app.toast("info", "Security token copied")

    def _regenerate_token(self):
        if not messagebox.askyesno("New token", "Browser extensions will need the new token. Continue?", parent=self.win):
            return
        token = self.s.regenerate_token()
        from ..logging_setup import register_secret
        register_secret(token)
        self.token_entry.configure(state="normal")
        self.token_entry.delete(0, "end")
        self.token_entry.insert(0, token)
        self.token_entry.configure(state="readonly")

    def _test_connection(self):
        import requests
        port = self.s.get("api_port")
        try:
            r = requests.get(f"http://127.0.0.1:{port}/connection", headers={"X-MDM-Token": self.s.get("api_token")}, timeout=3)
            ok = r.status_code == 200
            text = "✓ The local connection is working." if ok else f"✕ Unexpected answer: HTTP {r.status_code}"
        except requests.RequestException as e:
            ok, text = False, f"✕ Not reachable ({e.__class__.__name__}). Is “Allow the browser extension” on?"
        self.conn_label.configure(text=text, text_color=COLORS["success"] if ok else COLORS["danger"])

    def _check_updates(self):
        from .. import updater
        source = self._value("update_source")
        if source == "github":
            self.s.set("update_channel", self._value("update_channel"))
            self.app.check_for_updates()
            return
        url = self.vars["update_manifest_url"].get().strip()
        self.update_btn.configure(state="disabled")
        self.update_label.configure(text="Checking…", text_color=COLORS["muted"])

        def worker():
            try:
                available, manifest = updater.check({"update_source": source, "update_manifest_url": url})
                self.app.call_in_main(self._update_result, available, manifest, None)
            except updater.UpdateError as e:
                self.app.call_in_main(self._update_result, False, None, str(e))
        threading.Thread(target=worker, daemon=True).start()

    def _update_result(self, available, manifest, error):
        if not self.win.winfo_exists():
            return
        self.update_btn.configure(state="normal")
        if error:
            self.update_label.configure(text=error, text_color=COLORS["danger"])
            return
        if not available:
            self.update_label.configure(text=f"You're up to date (installed {__version__}, latest {manifest['version']}).",
                                        text_color=COLORS["success"])
            return
        self.update_label.configure(text=f"Version {manifest['version']} is available.\n{manifest['notes'][:400]}",
                                    text_color=COLORS["text"])
        if messagebox.askyesno("Update available", f"Download and verify version {manifest['version']} now?", parent=self.win):
            self._download_update(manifest)

    def _download_update(self, manifest):
        from .. import updater
        dest = os.path.join(DATA_DIR, "updates")
        self.update_btn.configure(state="disabled")

        def progress(received, total):
            pct = f"{received * 100 // total}%" if total else f"{received // 1048576} MB"
            self.app.call_in_main(lambda: self.update_label.winfo_exists() and
                                  self.update_label.configure(text=f"Downloading update… {pct}"))

        def worker():
            try:
                path = updater.download_update(manifest, dest, progress)
                self.app.call_in_main(self._update_ready, path, None)
            except updater.UpdateError as e:
                self.app.call_in_main(self._update_ready, None, str(e))
        threading.Thread(target=worker, daemon=True).start()

    def _update_ready(self, path, error):
        if self.win.winfo_exists():
            self.update_btn.configure(state="normal")
        if error:
            if self.win.winfo_exists():
                self.update_label.configure(text=error, text_color=COLORS["danger"])
            return
        self.update_label.configure(text=f"Verified installer saved to {path}", text_color=COLORS["success"])
        if messagebox.askyesno("Install update", "The installer was verified. Close Master Download Manager and run it now?",
                               parent=self.win):
            os.startfile(path)  # noqa: S606 - verified installer, explicitly confirmed by the user
            self.app.quit_app()

    # ----------------------------------------------------------------- save -- #
    def _value(self, key):
        var = self.vars[key]
        if isinstance(var, tuple):
            v, values, labels = var
            return values[labels.index(v.get())] if v.get() in labels else values[0]
        return var.get()

    def save(self):
        updates = {}
        errors = []

        def as_int(key, lo, hi, label):
            raw = str(self._value(key)).strip()
            try:
                value = int(float(raw))
            except ValueError:
                errors.append(f"{label}: '{raw}' isn't a number.")
                return
            if not lo <= value <= hi:
                errors.append(f"{label}: must be between {lo} and {hi}.")
                return
            updates[key] = value

        for key in ("minimize_to_tray", "notifications_enabled", "clipboard_monitor_enabled",
                    "restore_queue_on_start", "prefer_h264", "embed_metadata", "embed_chapters", "embed_thumbnail",
                    "subtitle_include_auto", "subtitle_embed", "api_enabled", "mica_backdrop",
                    "browser_capture_enabled", "update_check_on_start", "http_adaptive_connections",
                    "update_auto_check"):
            updates[key] = bool(self._value(key))
        for key in ("default_mode", "default_quality", "default_container", "default_audio_format",
                    "default_audio_quality", "subtitle_format", "cookies_mode", "cookies_browser",
                    "max_concurrent_downloads", "max_retries", "http_connections", "concurrent_fragments",
                    "update_source", "update_channel"):
            updates[key] = self._value(key)
        for key in ("download_dir", "video_dir", "audio_dir", "torrent_dir"):
            value = self._value(key).strip()
            if value and not os.path.isabs(value):
                errors.append(f"{key.replace('_', ' ').capitalize()}: use a full path like C:\\Users\\you\\Downloads.")
            updates[key] = os.path.normpath(value) if value else ""
        if not updates.get("download_dir"):
            errors.append("Download folder can't be empty.")
        template = self._value("filename_template").strip() or DEFAULT_TEMPLATE
        updates["filename_template"] = sanitize_template(template)
        langs = [x.strip() for x in self._value("subtitle_languages").split(",") if x.strip()]
        if any(not x.replace("-", "").replace("_", "").isalnum() or len(x) > 20 for x in langs):
            errors.append("Subtitle languages: use codes like en, de, pt-BR separated by commas.")
        updates["subtitle_languages"] = ", ".join(langs)
        as_int("speed_limit_kbps", 0, 10_000_000, "Speed limit")
        as_int("global_speed_limit_kbps", 0, 10_000_000, "Global limit")
        as_int("limited_mode_kbps", 1, 10_000_000, "Limited mode")
        as_int("browser_capture_min_kb", 0, 10_000_000, "Capture minimum size")
        types = [t.lower().lstrip(".") for t in self._value("browser_capture_types").split()]
        if any(not t.isalnum() or len(t) > 12 for t in types):
            errors.append("Capture file types: extensions like zip exe iso separated by spaces.")
        updates["browser_capture_types"] = " ".join(dict.fromkeys(types))
        updates["browser_capture_blacklist"] = " ".join(self._value("browser_capture_blacklist").split())
        as_int("download_timeout", 5, 600, "Network timeout")
        as_int("torrent_max_connections", 10, 5000, "Maximum peer connections")
        as_int("torrent_download_limit_kbps", 0, 10_000_000, "Torrent download limit")
        as_int("torrent_upload_limit_kbps", 0, 10_000_000, "Torrent upload limit")
        as_int("torrent_listen_port", 0, 65535, "Listening port")
        as_int("api_port", 1024, 65535, "Port")
        try:
            ratio = float(self._value("torrent_seed_ratio") or 0)
            if not 0 <= ratio <= 100:
                raise ValueError
            updates["torrent_seed_ratio"] = ratio
        except ValueError:
            errors.append("Seed ratio: a number like 0, 1 or 2.5.")
        proxy = self._value("proxy").strip()
        if proxy and not proxy.lower().startswith(_PROXY_SCHEMES):
            errors.append("Proxy: start with http://, https://, socks4:// or socks5://.")
        updates["proxy"] = proxy
        cookie_file = self._value("cookies_file").strip()
        if updates["cookies_mode"] == "file" and not os.path.isfile(cookie_file):
            errors.append("Cookies: the cookies.txt file doesn't exist.")
        updates["cookies_file"] = cookie_file
        ffmpeg = self._value("ffmpeg_path").strip()
        if ffmpeg and not tools.find_ffmpeg(ffmpeg):
            errors.append("Custom FFmpeg: no ffmpeg.exe found there.")
        updates["ffmpeg_path"] = ffmpeg
        update_url = self._value("update_manifest_url").strip()
        if update_url and not update_url.lower().startswith("https://"):
            errors.append("Update manifest URL must start with https://.")
        if updates.get("update_source") == "manifest" and not update_url:
            errors.append("Updates: enter the manifest URL, or choose GitHub releases.")
        updates["update_manifest_url"] = update_url

        if errors:
            messagebox.showwarning("Please check these settings", "\n".join(errors), parent=self.win)
            return

        restart_needed = (updates["api_port"] != self.s.get("api_port") or updates["api_enabled"] != self.s.get("api_enabled")
                          or updates["torrent_listen_port"] != self.s.get("torrent_listen_port"))
        updates["appearance_mode"] = self.app.appearance_mode
        updates["accent_color"] = self.accent_var.get()
        self.s.update(updates)
        for folder in (updates["download_dir"],):
            try:
                os.makedirs(folder, exist_ok=True)
            except OSError:
                pass
        want_startup = self.start_with_windows.get()
        if want_startup != startup.is_enabled():
            ok, msg = startup.enable() if want_startup else startup.disable()
            if not ok:
                messagebox.showwarning("Start with Windows", msg, parent=self.win)
        self.app.settings_changed()
        note = "Saved. Restart the app for port changes to take effect." if restart_needed else "Settings saved."
        self.footer_note.configure(text=note, text_color=COLORS["success"])
