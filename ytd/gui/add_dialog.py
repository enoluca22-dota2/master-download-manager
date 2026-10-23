"""The smart "Add download" dialog.

Paste anything — a media page, playlist/channel, direct file link, magnet
link or .torrent — and the dialog detects the engine, shows what the source
really offers, lets the user choose, and queues it. All network work runs
on worker threads; results come back through app.call_in_main().
"""

import io
import os
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import customtkinter as ctk

from .. import tools
from ..engine import resolve_download_dir
from ..files import sanitize_filename

from ..providers import get_provider
from ..providers.media import (
    LOSSY_BITRATES_KBPS, RESOLUTION_LABELS, _CUSTOM_FORMAT_RE, is_playlist_url, resolve_format_selection,
)
from ..providers.http import parse_checksum
from ..router import classify, default_selection, detect_engine, url_path_ext
from . import effects as glass_effects
from .common import ENGINE_LABEL, human_duration, human_size, parse_timestamp, truncate
from .theme import (
    COLORS, RADIUS, fade_in_toplevel, font, glass_button, glass_card, glass_entry, glass_format_button,
    glass_primary_action_button, glass_segmented,
)

CHECK, UNCHECK = "☑", "☐"
_AUDIO_LABELS = {"original": "Original (no conversion)", **{k: v[0] for k, v in tools.AUDIO_CODECS.items()}}
_THUMB_EMBED_AUDIO = {"mp3", "m4a"}
_CONN_CHOICES = ["Default"] + [str(n) for n in range(1, 17)]


def split_links(text):
    """Several links pasted at once (one per line or space-separated).
    Returns the list when there is more than one recognisable link."""
    tokens = [t.strip().strip('"<>') for t in (text or "").split()]
    links = [t for t in tokens if classify(t)]
    return links if len(links) > 1 else []


def _compress_indices(indices):
    """[1,2,3,5,7,8] -> '1-3,5,7-8' (yt-dlp playlist_items syntax)."""
    indices = sorted(set(indices))
    out, start, prev = [], None, None
    for i in indices:
        if start is None:
            start = prev = i
        elif i == prev + 1:
            prev = i
        else:
            out.append(f"{start}-{prev}" if prev != start else str(start))
            start = prev = i
    if start is not None:
        out.append(f"{start}-{prev}" if prev != start else str(start))
    return ",".join(out)


class CheckList:
    """A fast checkbox list on a ttk.Treeview (handles thousands of rows)."""

    def __init__(self, parent, columns, headings, widths, height=10, checked=True, anchors=None):
        frame = tk.Frame(parent, bd=0, highlightthickness=0)
        self.frame = frame
        cols = ("check",) + tuple(columns)
        self.tree = ttk.Treeview(frame, columns=cols, show="headings", height=height, selectmode="extended")
        self.tree.heading("check", text=CHECK)
        self.tree.column("check", width=34, anchor="center", stretch=False)
        for col, head, width in zip(columns, headings, widths):
            self.tree.heading(col, text=head)
            self.tree.column(col, width=width, anchor=(anchors or {}).get(col, "w"), stretch=(col == columns[1] if len(columns) > 1 else True))
        vsb = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview, style="Glass.Vertical.TScrollbar")
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.tree.bind("<Button-1>", self._on_click, add="+")
        self.tree.bind("<space>", self._toggle_selected)
        self._checked = {}
        self._default = checked
        self.on_change = None

    def add(self, key, values):
        self.tree.insert("", "end", iid=str(key), values=(CHECK if self._default else UNCHECK,) + tuple(values))
        self._checked[str(key)] = self._default

    def _set(self, iid, value):
        self._checked[iid] = value
        self.tree.set(iid, "check", CHECK if value else UNCHECK)

    def _on_click(self, event):
        region = self.tree.identify_region(event.x, event.y)
        if region == "heading" and self.tree.identify_column(event.x) == "#1":
            self.set_all(not all(self._checked.values()))
            return "break"
        iid = self.tree.identify_row(event.y)
        if iid and self.tree.identify_column(event.x) == "#1":
            self._set(iid, not self._checked[iid])
            self._changed()
            return "break"
        return None

    def _toggle_selected(self, _event=None):
        for iid in self.tree.selection():
            self._set(iid, not self._checked[iid])
        self._changed()
        return "break"

    def set_all(self, value):
        for iid in self._checked:
            self._set(iid, value)
        self._changed()

    def invert(self):
        for iid, value in list(self._checked.items()):
            self._set(iid, not value)
        self._changed()

    def set_range(self, keys, value=True):
        for key in keys:
            if str(key) in self._checked:
                self._set(str(key), value)
        self._changed()

    def checked(self):
        return [iid for iid in self.tree.get_children() if self._checked.get(iid)]

    def count(self):
        return len(self._checked)

    def _changed(self):
        if self.on_change:
            self.on_change()


class AddDialog:
    def __init__(self, app, prefill_url=None, analyze=False):
        self.app = app
        self.settings = app.settings
        self.manager = app.manager
        self.win = win = ctk.CTkToplevel(app.root)
        win.title("Add download — Master Download Manager")
        win.geometry("820x760")
        win.minsize(720, 600)
        win.transient(app.root)
        app.apply_glass_window(win)
        self._token = 0
        self._batch = []
        self.media = None
        self.engine = None
        self.save_dir_override = None
        self._thumb_image = None

        outer = ctk.CTkFrame(win, fg_color="transparent")
        outer.pack(fill="both", expand=True, padx=16, pady=16)

        # -- input row -- #
        top = glass_card(outer, elevated=True)
        top.pack(fill="x")
        row = ctk.CTkFrame(top, fg_color="transparent")
        row.pack(fill="x", padx=14, pady=(14, 6))
        self.url_var = tk.StringVar(value=" ".join((prefill_url or "").split()))
        self.url_entry = glass_entry(row, textvariable=self.url_var, height=40,
                                     placeholder="Paste a video/playlist URL, file link, magnet link or .torrent path…")
        self.url_entry.pack(side="left", fill="x", expand=True)
        self.url_entry.bind("<Return>", lambda e: self.analyze())
        glass_button(row, "Paste", self._paste, width=64, height=40).pack(side="left", padx=(8, 0))
        glass_button(row, "Torrent file…", self._browse_torrent, width=104, height=40).pack(side="left", padx=(8, 0))
        glass_button(row, "Analyze", self.analyze, variant="primary", width=88, height=40).pack(side="left", padx=(8, 0))

        status_row = ctk.CTkFrame(top, fg_color="transparent")
        status_row.pack(fill="x", padx=14, pady=(0, 12))
        self.spinner = glass_effects.GlassSpinner(status_row, size=16, bg=app.palette()["surface_raised"],
                                                  color=app.palette()["cyan"])
        self.engine_badge = ctk.CTkLabel(status_row, text="", corner_radius=RADIUS["xs"], fg_color=COLORS["surface_deep"],
                                         font=font(11, "bold"), text_color=COLORS["cyan"], padx=8)
        self.status_label = ctk.CTkLabel(status_row, text="Media sites (≈1,700), direct files, magnet links and .torrent files are supported.",
                                         text_color=COLORS["muted"], font=font(12), anchor="w", justify="left", wraplength=640)
        self.status_label.pack(side="left", fill="x", expand=True)

        # -- content -- #
        self.body = ctk.CTkScrollableFrame(outer, fg_color="transparent", corner_radius=0,
                                           scrollbar_button_color=COLORS["border"],
                                           scrollbar_button_hover_color=COLORS["primary"])
        self.body.pack(fill="both", expand=True, pady=(12, 8))

        # -- footer -- #
        footer = glass_card(outer, elevated=True)
        footer.pack(fill="x")
        frow = ctk.CTkFrame(footer, fg_color="transparent")
        frow.pack(fill="x", padx=14, pady=12)
        ctk.CTkLabel(frow, text="Save to", text_color=COLORS["muted"], font=font(12, "bold")).pack(side="left")
        self.dir_label = ctk.CTkLabel(frow, text="", text_color=COLORS["text"], font=font(12), anchor="w")
        self.dir_label.pack(side="left", padx=(8, 6), fill="x", expand=True)
        glass_button(frow, "Change…", self._browse_folder, width=80, height=32).pack(side="left")
        self.start_now_var = tk.BooleanVar(value=True)
        ctk.CTkCheckBox(frow, text="Start now", variable=self.start_now_var, font=font(12)).pack(side="left", padx=12)
        self._queues = {q.name: q.id for q in self.manager.queues.all()}
        self.queue_var = tk.StringVar(value=next(iter(self._queues)))
        ctk.CTkLabel(frow, text="Queue", text_color=COLORS["muted"], font=font(12, "bold")).pack(side="left")
        ctk.CTkOptionMenu(frow, values=list(self._queues), variable=self.queue_var, width=120, height=32,
                          font=font(12)).pack(side="left", padx=(6, 0))
        self.download_btn = glass_primary_action_button(frow, "⬇  Download", self.submit, height=40, width=150)
        self.download_btn.pack(side="right")
        self.download_btn.configure(state="disabled")
        self._update_dir_label()

        self._show_idle()
        fade_in_toplevel(win)
        win.after(60, lambda: win.winfo_exists() and (win.lift(), win.focus_force()))
        win.after(100, self.url_entry.focus_set)
        if prefill_url and analyze:
            win.after(150, self.analyze)
        elif prefill_url and split_links(prefill_url):
            win.after(150, self.analyze)

    def _add(self, **kwargs):
        """Queue one job in the chosen queue; 'Start now' unticked adds it
        paused (rather than pausing it after it may already have started)."""
        kwargs.setdefault("queue_id", self._queues.get(self.queue_var.get()))
        kwargs.setdefault("start_paused", not self.start_now_var.get())
        return self.manager.add_job(**kwargs)

    def _duplicate_note(self, url):
        """'Already in the list / downloaded on …' — a warning, never a block."""
        active = [j for j in self.manager.list_jobs() if j.url == url and not j.from_history
                  and j.status not in ("Completed", "Failed", "Cancelled")]
        if active:
            return f"Already in the list ({active[0].status.lower()})."
        try:
            done = self.app.history.find_by_url(url)
        except Exception:  # noqa: BLE001
            done = []
        if done:
            return f"Already downloaded on {done[0]['date']}."
        return None

    # ------------------------------------------------------------ helpers -- #
    def _clear_body(self):
        for child in self.body.winfo_children():
            child.destroy()

    def _section(self, title):
        card = glass_card(self.body, radius="md")
        card.pack(fill="x", pady=(0, 10))
        if title:
            ctk.CTkLabel(card, text=title, font=font(13, "bold")).pack(anchor="w", padx=14, pady=(12, 6))
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=14, pady=(0, 12))
        return inner

    def _set_status(self, text, kind="muted", busy=False):
        color = {"muted": COLORS["muted"], "error": COLORS["danger"], "ok": COLORS["text"]}[kind]
        self.status_label.configure(text=text, text_color=color)
        if busy:
            self.spinner.pack(side="left", padx=(0, 8), before=self.status_label)
            self.spinner.start()
        else:
            self.spinner.stop()
            self.spinner.pack_forget()

    def _set_badge(self, text):
        if text:
            self.engine_badge.configure(text=text)
            self.engine_badge.pack(side="left", padx=(0, 8), before=self.status_label)
        else:
            self.engine_badge.pack_forget()

    def _paste(self):
        try:
            text = self.win.clipboard_get().strip()
        except tk.TclError:
            return
        self.url_var.set(" ".join(text.split()))
        self.analyze()

    def _browse_torrent(self):
        path = filedialog.askopenfilename(parent=self.win, filetypes=[("Torrent files", "*.torrent")])
        if path:
            self.url_var.set(path)
            self.analyze()

    def _browse_folder(self):
        folder = filedialog.askdirectory(parent=self.win, initialdir=self._target_dir())
        if folder:
            self.save_dir_override = folder
            self._update_dir_label()

    def _kind_and_category(self):
        if self.engine == "torrent":
            return "torrent", "Torrent"
        if self.engine == "http":
            return "file", None
        mode = getattr(self, "mode_var", None)
        if mode is not None and mode.get() == "Audio only":
            return "audio", "Music"
        return "video", "Video"

    def _target_dir(self):
        if self.save_dir_override:
            return self.save_dir_override
        kind, category = self._kind_and_category()
        return resolve_download_dir(self.settings, kind, category)

    def _update_dir_label(self):
        self.dir_label.configure(text=truncate(self._target_dir(), 70))

    def _show_idle(self):
        self._clear_body()
        inner = self._section("What can I paste?")
        for line in (
            "•  A video or audio page — YouTube, Vimeo, SoundCloud and ~1,700 other sites",
            "•  A playlist, channel or album page — choose which items to download",
            "•  A direct file link — .zip, .iso, .exe, .pdf, .mp4 … (resumable, multi-connection)",
            "•  A magnet link or a .torrent file (local or a link to one)",
        ):
            ctk.CTkLabel(inner, text=line, font=font(12), text_color=COLORS["muted"], anchor="w").pack(anchor="w", pady=1)

    # ------------------------------------------------------------ analyze -- #
    def analyze(self):
        url = self.url_var.get().strip().strip('"')
        if not url:
            self._set_status("Paste a link first.", "error")
            return
        links = split_links(url)
        if links:
            self._show_batch(links)
            return
        self._batch = []
        self.url_var.set(url)
        self._token += 1
        token = self._token
        self.media = None
        self.download_btn.configure(state="disabled")
        self._set_badge("")
        self._clear_body()
        self._set_status("Detecting…", busy=True)

        def worker():
            try:
                engine, reason = detect_engine(url, self.settings)
                if engine is None:
                    raise ValueError(reason)
                self.app.call_in_main(self._engine_detected, token, engine, reason)
                provider = get_provider(engine)
                if engine == "media" and is_playlist_url(url) and "watch" not in url and "youtu.be" not in url:
                    media = provider.detect_playlist_media(url)
                else:
                    media = provider.detect(url)
                thumb = self._fetch_thumbnail(media.thumbnail) if getattr(media, "thumbnail", None) else None
                self.app.call_in_main(self._detected, token, engine, media, thumb)
            except Exception as e:  # noqa: BLE001 - shown to the user
                message = str(e) or e.__class__.__name__
                self.app.call_in_main(self._detect_failed, token, message)

        threading.Thread(target=worker, name="mdm-detect", daemon=True).start()

    def _fetch_thumbnail(self, url):
        try:
            import requests
            from PIL import Image
            with requests.get(url, timeout=8, stream=True) as r:
                r.raise_for_status()
                data = r.raw.read(2 * 1024 * 1024 + 1, decode_content=True)
            if len(data) > 2 * 1024 * 1024:
                return None
            img = Image.open(io.BytesIO(data)).convert("RGB")
            img.thumbnail((192, 108))
            return img
        except Exception:  # noqa: BLE001 - a thumbnail is decoration only
            return None

    def _engine_detected(self, token, engine, reason):
        if token != self._token or not self.win.winfo_exists():
            return
        self.engine = engine
        self._set_badge(f"{ENGINE_LABEL.get(engine, engine).upper()}")
        detail = {"media": "Reading the available formats…", "torrent": "Reading torrent metadata… (a magnet link can take up to a minute)",
                  "http": "Checking the file on the server…"}[engine]
        self._set_status(f"{reason}. {detail}", busy=True)
        self._update_dir_label()

    def _detect_failed(self, token, message):
        if token != self._token or not self.win.winfo_exists():
            return
        self._set_status(message, "error")
        self._show_idle()

    def _detected(self, token, engine, media, thumb):
        if token != self._token or not self.win.winfo_exists():
            return
        self.engine, self.media = engine, media
        note = self._duplicate_note(self.url_var.get().strip())
        self._set_status(media.title + (f"   ⚠ {note}" if note else ""), "ok")
        self._clear_body()
        self._header(media, thumb)
        if engine == "torrent":
            self._build_torrent_panel(media)
        elif engine == "http":
            self._build_http_panel(media)
        elif media.is_playlist:
            self._build_playlist_panel(media)
        else:
            self._build_media_panel(media)
        self._update_dir_label()
        self.download_btn.configure(state="normal")

    def _header(self, media, thumb):
        card = glass_card(self.body, radius="md")
        card.pack(fill="x", pady=(0, 10))
        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(fill="x", padx=14, pady=12)
        if thumb is not None:
            self._thumb_image = ctk.CTkImage(light_image=thumb, dark_image=thumb, size=thumb.size)
            ctk.CTkLabel(row, text="", image=self._thumb_image).pack(side="left", padx=(0, 12))
        text = ctk.CTkFrame(row, fg_color="transparent")
        text.pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(text, text=media.title, font=font(15, "bold"), anchor="w", justify="left",
                     wraplength=540).pack(anchor="w")
        facts = [media.source_site or ""]
        if media.uploader:
            facts.append(media.uploader)
        if media.duration_seconds:
            facts.append(human_duration(media.duration_seconds))
        if media.is_playlist:
            facts.append(f"{media.playlist_count} items")
        if self.engine in ("torrent", "http") and media.formats and media.formats[0].filesize_bytes:
            facts.append(human_size(media.formats[0].filesize_bytes))
        ctk.CTkLabel(text, text="  •  ".join(f for f in facts if f), font=font(12), text_color=COLORS["muted"],
                     anchor="w").pack(anchor="w", pady=(2, 0))

    # ------------------------------------------------------ media (single) -- #
    def _build_media_panel(self, media):
        cfg = self.settings
        videos = [f for f in media.formats if f.kind == "video" and f.format_id.startswith("video-")]
        audio_codecs = media.extra.get("audio_codecs", [])
        has_ffmpeg = media.extra.get("ffmpeg", False)
        self.media_formats = media.formats

        url = self.url_var.get()
        if is_playlist_url(url):
            inner = self._section(None)
            ctk.CTkLabel(inner, text="This link is part of a playlist.", font=font(12), text_color=COLORS["muted"]).pack(side="left")
            glass_button(inner, "Show the whole playlist…", self._switch_to_playlist, width=190, height=30).pack(side="right")

        what = self._section("What to download")
        self.mode_var = tk.StringVar(value="Audio only" if cfg.get("default_mode") in ("MP3", "Audio") and audio_codecs or
                                     not videos else "Video")
        modes = ["Video", "Audio only", "Subtitles only"] if videos else ["Audio only", "Subtitles only"]
        if not any(f.kind == "subtitle" for f in media.formats):
            modes.remove("Subtitles only")
        if any(f.format_id == "thumbnail" for f in media.formats):
            modes.append("Thumbnail")
        if self.mode_var.get() not in modes:
            self.mode_var.set(modes[0])
        glass_segmented(what, modes, variable=self.mode_var, command=lambda *_: self._render_mode()).pack(anchor="w")
        self.mode_frame = ctk.CTkFrame(what, fg_color="transparent")
        self.mode_frame.pack(fill="x", pady=(12, 0))

        # state shared by the mode panels
        default_q = RESOLUTION_LABELS.get(cfg.get("default_quality", "720p (HD)"), 720)
        tiers = [f for f in videos if f.format_id[6:].isdigit()]
        best = min(tiers, key=lambda f: abs(f.height - default_q)) if tiers else None
        self.video_choice = tk.StringVar(value=best.format_id if best else "video-best")
        self.container_var = tk.StringVar(value=cfg.get("default_container", "mp4"))
        self.h264_var = tk.BooleanVar(value=bool(cfg.get("prefer_h264", False)))
        self.meta_var = tk.BooleanVar(value=bool(cfg.get("embed_metadata", True)) and has_ffmpeg)
        self.chapters_var = tk.BooleanVar(value=bool(cfg.get("embed_chapters", True)) and has_ffmpeg and media.chapters > 0)
        self.thumb_var = tk.BooleanVar(value=bool(cfg.get("embed_thumbnail", False)) and has_ffmpeg)
        audio_default = cfg.get("default_audio_format", "mp3")
        self.audio_codec_var = tk.StringVar(value=audio_default if audio_default in audio_codecs else
                                            ("mp3" if "mp3" in audio_codecs else "original"))
        kbps_default = str(cfg.get("default_audio_quality", "192 kbps")).split()[0]
        self.audio_kbps_var = tk.StringVar(value=kbps_default if kbps_default.isdigit() else "192")
        self.audio_codecs = ["original"] + audio_codecs

        self._build_subtitle_picker(media)
        self._build_advanced(media)
        self._render_mode()
        if not has_ffmpeg:
            warn = self._section(None)
            ctk.CTkLabel(warn, text="⚠ FFmpeg wasn't found, so merging, audio conversion and embedding are unavailable. "
                         "Only single-file formats can be downloaded.", text_color=COLORS["warning"], font=font(12),
                         wraplength=700, justify="left").pack(anchor="w")

    def _switch_to_playlist(self):
        url = self.url_var.get()
        self._token += 1
        token = self._token
        self._clear_body()
        self.download_btn.configure(state="disabled")
        self._set_status("Loading the playlist…", busy=True)

        def worker():
            try:
                media = get_provider("media").detect_playlist_media(url)
                self.app.call_in_main(self._detected, token, "media", media, None)
            except Exception as e:  # noqa: BLE001
                self.app.call_in_main(self._detect_failed, token, str(e))

        threading.Thread(target=worker, daemon=True).start()

    def _render_mode(self):
        self.subs_holder.pack_forget()  # it's re-packed into the new panel when needed
        self._subs_visible = False
        for child in self.mode_frame.winfo_children():
            child.destroy()
        mode = self.mode_var.get()
        f = self.mode_frame
        if mode == "Video":
            ctk.CTkLabel(f, text="Quality", font=font(12, "bold"), text_color=COLORS["muted"]).pack(anchor="w")
            options = [x for x in self.media_formats if x.kind == "video" and x.format_id.startswith("video-")]
            tiers = sorted([o for o in options if o.format_id[6:].isdigit()], key=lambda o: -o.height)
            extremes = [o for o in options if not o.format_id[6:].isdigit()]
            for group in (tiers, extremes):
                if not group:
                    continue
                row = ctk.CTkFrame(f, fg_color="transparent")
                row.pack(fill="x", pady=(4, 2))
                for fmt in group:
                    label = fmt.label.split(" (")[0] if fmt.format_id[6:].isdigit() else fmt.label
                    if fmt.filesize_bytes:
                        label += f"\n{human_size(fmt.filesize_bytes)}{'≈' if fmt.filesize_is_estimate else ''}"
                    glass_format_button(row, label, lambda v=fmt.format_id: self._pick_video(v),
                                        selected=(fmt.format_id == self.video_choice.get()),
                                        height=44 if "\n" in label else 32).pack(side="left", padx=(0, 6))
            ctk.CTkFrame(f, fg_color="transparent", height=8).pack(fill="x")
            opts = ctk.CTkFrame(f, fg_color="transparent")
            opts.pack(fill="x")
            ctk.CTkLabel(opts, text="Container", font=font(12)).pack(side="left")
            ctk.CTkOptionMenu(opts, values=["mp4", "mkv", "original"], variable=self.container_var, width=110,
                              command=lambda *_: self._render_mode()).pack(side="left", padx=(8, 18))
            ctk.CTkCheckBox(opts, text="Prefer H.264 (plays everywhere)", variable=self.h264_var, font=font(12)).pack(side="left")
            emb = ctk.CTkFrame(f, fg_color="transparent")
            emb.pack(fill="x", pady=(10, 0))
            self._embed_checks(emb, video=True)
            self._subtitle_summary(f)
        elif mode == "Audio only":
            ctk.CTkLabel(f, text="Format", font=font(12, "bold"), text_color=COLORS["muted"]).pack(anchor="w")
            row = ctk.CTkFrame(f, fg_color="transparent")
            row.pack(fill="x", pady=(4, 8))
            labels = [_AUDIO_LABELS[c] for c in self.audio_codecs]
            menu = ctk.CTkOptionMenu(row, values=labels, width=220,
                                     command=lambda label: self._pick_codec(self.audio_codecs[labels.index(label)]))
            menu.set(_AUDIO_LABELS.get(self.audio_codec_var.get(), labels[0]))
            menu.pack(side="left")
            source = next((x for x in self.media_formats if x.format_id == "source-audio"), None)
            if source is not None and self.audio_codec_var.get() == "original":
                ctk.CTkLabel(row, text=f"  {source.label}  {human_size(source.filesize_bytes, '')}",
                             text_color=COLORS["muted"], font=font(12)).pack(side="left")
            codec = self.audio_codec_var.get()
            if codec in tools.AUDIO_CODECS and not tools.AUDIO_CODECS[codec][2]:
                ctk.CTkLabel(f, text="Bitrate", font=font(12, "bold"), text_color=COLORS["muted"]).pack(anchor="w")
                pills = ctk.CTkFrame(f, fg_color="transparent")
                pills.pack(fill="x", pady=(4, 8))
                for value in ["best"] + [str(k) for k in LOSSY_BITRATES_KBPS]:
                    text = "Best (VBR)" if value == "best" else f"{value} kbps"
                    glass_format_button(pills, text, lambda v=value: self._pick_kbps(v),
                                        selected=(self.audio_kbps_var.get() == value), width=92).pack(side="left", padx=(0, 6))
            emb = ctk.CTkFrame(f, fg_color="transparent")
            emb.pack(fill="x", pady=(6, 0))
            self._embed_checks(emb, video=False)
        elif mode == "Subtitles only":
            ctk.CTkLabel(f, text="Each selected language is saved as its own subtitle file.", font=font(12),
                         text_color=COLORS["muted"]).pack(anchor="w")
            self.subs_holder.pack(in_=f, fill="x", pady=(6, 0))
        elif mode == "Thumbnail":
            thumb = next(x for x in self.media_formats if x.format_id == "thumbnail")
            ctk.CTkLabel(f, text=f"Saves the largest thumbnail ({thumb.resolution}) as an image file.", font=font(12),
                         text_color=COLORS["muted"]).pack(anchor="w")
        self._update_dir_label()

    def _embed_checks(self, parent, video):
        has_ffmpeg = self.media.extra.get("ffmpeg", False)
        state = "normal" if has_ffmpeg else "disabled"
        ctk.CTkCheckBox(parent, text="Embed metadata", variable=self.meta_var, font=font(12), state=state).pack(side="left")
        if video and self.media.chapters:
            ctk.CTkCheckBox(parent, text=f"Embed chapters ({self.media.chapters})", variable=self.chapters_var,
                            font=font(12), state=state).pack(side="left", padx=(14, 0))
        can_thumb = has_ffmpeg and ((video and self.container_var.get() in ("mp4", "mkv"))
                                    or (not video and self.audio_codec_var.get() in _THUMB_EMBED_AUDIO))
        if not can_thumb:
            self.thumb_var.set(False)
        ctk.CTkCheckBox(parent, text="Embed thumbnail", variable=self.thumb_var, font=font(12),
                        state="normal" if can_thumb else "disabled").pack(side="left", padx=(14, 0))

    def _pick_video(self, format_id):
        self.video_choice.set(format_id)
        self._render_mode()

    def _pick_codec(self, codec):
        self.audio_codec_var.set(codec)
        self._render_mode()

    def _pick_kbps(self, value):
        self.audio_kbps_var.set(value)
        self._render_mode()

    # subtitles
    def _build_subtitle_picker(self, media):
        subs = [f for f in media.formats if f.kind == "subtitle"]
        self.subs_holder = ctk.CTkFrame(self.body, fg_color="transparent")
        self.sub_list = None
        if not subs:
            return
        top = ctk.CTkFrame(self.subs_holder, fg_color="transparent")
        top.pack(fill="x", pady=(0, 4))
        self.sub_filter = tk.StringVar()
        glass_entry(top, textvariable=self.sub_filter, placeholder="Filter languages…", height=30, width=200).pack(side="left")
        self.sub_format_var = tk.StringVar(value=self.settings.get("subtitle_format", "srt"))
        ctk.CTkLabel(top, text="Format", font=font(12)).pack(side="left", padx=(14, 6))
        ctk.CTkOptionMenu(top, values=["srt", "vtt", "ass"], variable=self.sub_format_var, width=80).pack(side="left")
        self.sub_embed_var = tk.BooleanVar(value=bool(self.settings.get("subtitle_embed", False)))
        ctk.CTkCheckBox(top, text="Embed into the video", variable=self.sub_embed_var, font=font(12)).pack(side="left", padx=14)
        self.sub_list = CheckList(self.subs_holder, ("lang", "name", "type"), ("Code", "Language", "Type"),
                                  (90, 330, 130), height=7, checked=False)
        self.sub_list.frame.pack(fill="x")
        wanted = {lang.strip() for lang in str(self.settings.get("subtitle_languages", "")).split(",") if lang.strip()}
        include_auto = bool(self.settings.get("subtitle_include_auto", False))
        self._sub_rows = []
        for f in sorted(subs, key=lambda f: (f.is_auto, f.lang_name or "")):
            self.sub_list.add(f.format_id, (f.lang_code, f.lang_name, "auto-generated" if f.is_auto else "uploaded"))
            self._sub_rows.append(f)
            if f.lang_code in wanted and (not f.is_auto or include_auto):
                self.sub_list.set_range([f.format_id])
        self.sub_list.on_change = self._refresh_sub_summary
        self.sub_filter.trace_add("write", lambda *_: self._filter_subs())

    def _filter_subs(self):
        needle = self.sub_filter.get().strip().lower()
        tree = self.sub_list.tree
        tree.detach(*[f.format_id for f in self._sub_rows])
        for f in self._sub_rows:  # re-attach matches in their original order
            if not needle or needle in (f.lang_code or "").lower() or needle in (f.lang_name or "").lower():
                tree.reattach(f.format_id, "", "end")

    def _subtitle_summary(self, parent):
        if self.sub_list is None:
            return
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", pady=(10, 0))
        self.sub_summary = ctk.CTkLabel(row, text="", font=font(12), text_color=COLORS["muted"])
        self.sub_summary.pack(side="left")
        self._sub_toggle = glass_button(row, "Choose subtitles…", self._toggle_subs, width=150, height=28)
        self._sub_toggle.pack(side="left", padx=10)
        self._subs_visible = False
        self._refresh_sub_summary()

    def _toggle_subs(self):
        self._subs_visible = not self._subs_visible
        if self._subs_visible:
            self.subs_holder.pack(in_=self.mode_frame, fill="x", pady=(8, 0))
        else:
            self.subs_holder.pack_forget()

    def _refresh_sub_summary(self):
        if getattr(self, "sub_summary", None) is None or not self.sub_summary.winfo_exists():
            return
        picked = self.sub_list.checked()
        self.sub_summary.configure(text=f"Subtitles: {len(picked)} selected" if picked else "Subtitles: none")

    def _selected_subs(self):
        if self.sub_list is None:
            return []
        rows = {f.format_id: f for f in self._sub_rows}
        return [rows[i] for i in self.sub_list.checked()]

    # advanced
    def _build_advanced(self, media):
        inner = self._section("Advanced")
        self.custom_var = tk.BooleanVar(value=False)
        self.custom_spec = tk.StringVar()
        top = ctk.CTkFrame(inner, fg_color="transparent")
        top.pack(fill="x")
        ctk.CTkCheckBox(top, text="Use a specific format (overrides Quality)", variable=self.custom_var,
                        font=font(12)).pack(side="left")
        glass_entry(top, textvariable=self.custom_spec, placeholder="e.g. 137+140 or bv*[height<=720]+ba",
                    height=30, width=260).pack(side="left", padx=10)
        if media.raw_formats:
            ctk.CTkLabel(inner, text="Every format the source offers — select a video and an audio row to combine them:",
                         font=font(11), text_color=COLORS["muted"]).pack(anchor="w", pady=(8, 2))
            frame = tk.Frame(inner, bd=0, highlightthickness=0)
            frame.pack(fill="x")
            cols = ("id", "ext", "res", "fps", "vcodec", "acodec", "bitrate", "size", "note")
            tree = ttk.Treeview(frame, columns=cols, show="headings", height=7, selectmode="extended")
            for col, head, w in zip(cols, ("ID", "Ext", "Resolution", "FPS", "Video codec", "Audio codec", "Bitrate", "Size", "Note"),
                                    (60, 50, 90, 45, 110, 100, 75, 80, 110)):
                tree.heading(col, text=head)
                tree.column(col, width=w, anchor="w")
            for r in media.raw_formats:
                size = human_size(r["filesize_bytes"], "") + ("≈" if r["filesize_is_estimate"] and r["filesize_bytes"] else "")
                tree.insert("", "end", iid=r["format_id"], values=(
                    r["format_id"], r["ext"] or "", r["resolution"] if r["has_video"] else "audio only",
                    f"{r['fps']:.0f}" if r["fps"] else "", (r["vcodec"] or "").split(".")[0],
                    (r["acodec"] or "").split(".")[0], f"{r['tbr_kbps']:.0f}k" if r["tbr_kbps"] else "", size,
                    r["note"] or ""))
            vsb = ttk.Scrollbar(frame, orient="vertical", command=tree.yview, style="Glass.Vertical.TScrollbar")
            tree.configure(yscrollcommand=vsb.set)
            tree.pack(side="left", fill="x", expand=True)
            vsb.pack(side="right", fill="y")

            def on_select(_e):
                ids = list(tree.selection())
                if ids:
                    rows = {r["format_id"]: r for r in media.raw_formats}
                    ids.sort(key=lambda i: (not rows[i]["has_video"], i))
                    self.custom_spec.set("+".join(ids[:2]))
                    self.custom_var.set(True)
            tree.bind("<<TreeviewSelect>>", on_select)

        sec = ctk.CTkFrame(inner, fg_color="transparent")
        sec.pack(fill="x", pady=(10, 0))
        ctk.CTkLabel(sec, text="Only a section:", font=font(12)).pack(side="left")
        self.section_start = tk.StringVar()
        self.section_end = tk.StringVar()
        glass_entry(sec, textvariable=self.section_start, placeholder="start 0:30", height=30, width=90).pack(side="left", padx=(8, 4))
        glass_entry(sec, textvariable=self.section_end, placeholder="end 1:45", height=30, width=90).pack(side="left")
        ctk.CTkLabel(sec, text="   Filename:", font=font(12)).pack(side="left")
        self.filename_var = tk.StringVar()
        glass_entry(sec, textvariable=self.filename_var, placeholder="(automatic)", height=30, width=200).pack(side="left", padx=(8, 0))
        lim = ctk.CTkFrame(inner, fg_color="transparent")
        lim.pack(fill="x", pady=(8, 0))
        ctk.CTkLabel(lim, text="Speed limit for this download (KB/s, empty = default):", font=font(12)).pack(side="left")
        self.limit_var = tk.StringVar()
        glass_entry(lim, textvariable=self.limit_var, height=30, width=90).pack(side="left", padx=8)

    # ---------------------------------------------------- media (playlist) -- #
    def _build_playlist_panel(self, media):
        cfg = self.settings
        codecs = media.extra.get("audio_codecs", [])
        what = self._section("Download as")
        self.mode_var = tk.StringVar(value="Audio only" if cfg.get("default_mode") in ("MP3", "Audio") else "Video")
        row = ctk.CTkFrame(what, fg_color="transparent")
        row.pack(fill="x")
        glass_segmented(row, ["Video", "Audio only"], variable=self.mode_var,
                        command=lambda *_: (self._render_playlist_quality(), self._update_dir_label())).pack(side="left")
        self.pl_quality_holder = ctk.CTkFrame(row, fg_color="transparent")
        self.pl_quality_holder.pack(side="left", padx=14)
        self._pl_formats = media.formats
        self._pl_codecs = codecs
        self._render_playlist_quality()
        opts = ctk.CTkFrame(what, fg_color="transparent")
        opts.pack(fill="x", pady=(10, 0))
        self.pl_subfolder = tk.BooleanVar(value=True)
        self.pl_number = tk.BooleanVar(value=True)
        self.pl_single_task = tk.BooleanVar(value=False)
        ctk.CTkCheckBox(opts, text="Save into a folder named after the playlist", variable=self.pl_subfolder,
                        font=font(12)).pack(side="left")
        ctk.CTkCheckBox(opts, text="Number files in playlist order", variable=self.pl_number, font=font(12)).pack(side="left", padx=14)
        ctk.CTkCheckBox(what, text="Queue as one task (instead of one task per item)", variable=self.pl_single_task,
                        font=font(12)).pack(anchor="w", pady=(8, 0))

        items = self._section(f"Items ({media.playlist_count})" + ("  — showing the first 1000" if media.extra.get("truncated") else ""))
        bar = ctk.CTkFrame(items, fg_color="transparent")
        bar.pack(fill="x", pady=(0, 6))
        glass_button(bar, "Select all", lambda: self.pl_list.set_all(True), width=86, height=28).pack(side="left")
        glass_button(bar, "Select none", lambda: self.pl_list.set_all(False), width=96, height=28).pack(side="left", padx=6)
        glass_button(bar, "Invert", lambda: self.pl_list.invert(), width=70, height=28).pack(side="left")
        ctk.CTkLabel(bar, text="   Range", font=font(12)).pack(side="left")
        self.range_from = tk.StringVar(value="1")
        self.range_to = tk.StringVar(value=str(len(media.entries)))
        glass_entry(bar, textvariable=self.range_from, height=28, width=56).pack(side="left", padx=(6, 2))
        ctk.CTkLabel(bar, text="–", font=font(12)).pack(side="left")
        glass_entry(bar, textvariable=self.range_to, height=28, width=56).pack(side="left", padx=(2, 6))
        glass_button(bar, "Select range", self._select_range, width=100, height=28).pack(side="left")
        self.pl_count_label = ctk.CTkLabel(bar, text="", font=font(12), text_color=COLORS["muted"])
        self.pl_count_label.pack(side="right")

        self.pl_list = CheckList(items, ("index", "title", "duration"), ("#", "Title", "Length"), (50, 520, 70),
                                 height=12, anchors={"index": "e", "duration": "e"})
        self.pl_list.frame.pack(fill="both", expand=True)
        self._pl_entries = {}
        for entry in media.entries:
            key = str(entry["index"])
            self._pl_entries[key] = entry
            self.pl_list.add(key, (entry["index"], entry["title"], human_duration(entry.get("duration_seconds"))))
        self.pl_list.on_change = self._refresh_pl_count
        self._refresh_pl_count()

    def _render_playlist_quality(self):
        for c in self.pl_quality_holder.winfo_children():
            c.destroy()
        if self.mode_var.get() == "Video":
            opts = [f for f in self._pl_formats if f.kind == "video"]
            default_q = RESOLUTION_LABELS.get(self.settings.get("default_quality", "720p (HD)"), 720)
            labels = [f.label for f in opts]
            self._pl_choice = {f.label: f.format_id for f in opts}
            menu = ctk.CTkOptionMenu(self.pl_quality_holder, values=labels, width=220)
            pick = next((f.label for f in opts if f.height == default_q), labels[0])
            menu.set(pick)
            self.pl_quality_menu = menu
        else:
            opts = [f for f in self._pl_formats if f.kind == "audio"]
            labels = [f.label for f in opts]
            self._pl_choice = {f.label: f.format_id for f in opts}
            menu = ctk.CTkOptionMenu(self.pl_quality_holder, values=labels, width=220)
            kbps = str(self.settings.get("default_audio_quality", "192")).split()[0]
            menu.set(next((f.label for f in opts if f.format_id == f"mp3-{kbps}"), labels[0]))
            self.pl_quality_menu = menu
        menu.pack(side="left")

    def _select_range(self):
        try:
            a, b = int(self.range_from.get()), int(self.range_to.get())
        except ValueError:
            messagebox.showwarning("Range", "Enter item numbers, e.g. 5 and 20.", parent=self.win)
            return
        lo, hi = min(a, b), max(a, b)
        self.pl_list.set_all(False)
        self.pl_list.set_range(range(lo, hi + 1))

    def _refresh_pl_count(self):
        self.pl_count_label.configure(text=f"{len(self.pl_list.checked())} of {self.pl_list.count()} selected")

    # ------------------------------------------------------------- torrent -- #
    def _build_torrent_panel(self, media):
        info = self._section(None)
        extra = media.extra or {}
        facts = [f"Info-hash {extra.get('info_hash', '')[:16]}…" if extra.get("info_hash") else "",
                 f"{extra.get('pieces')} pieces × {human_size(extra.get('piece_size'))}" if extra.get("pieces") else "",
                 "private torrent" if extra.get("private") else ""]
        ctk.CTkLabel(info, text="  •  ".join(f for f in facts if f) or "Torrent", font=font(12),
                     text_color=COLORS["muted"]).pack(anchor="w")
        files = self._section("Files")
        bar = ctk.CTkFrame(files, fg_color="transparent")
        bar.pack(fill="x", pady=(0, 6))
        glass_button(bar, "Select all", lambda: self.t_list.set_all(True), width=86, height=28).pack(side="left")
        glass_button(bar, "Select none", lambda: self.t_list.set_all(False), width=96, height=28).pack(side="left", padx=6)
        self.t_count = ctk.CTkLabel(bar, text="", font=font(12), text_color=COLORS["muted"])
        self.t_count.pack(side="right")
        self.t_list = CheckList(files, ("name", "size", "path"), ("File", "Size", "Path in torrent"), (300, 90, 280),
                                height=12, anchors={"size": "e"})
        self.t_list.frame.pack(fill="both", expand=True)
        self._t_files = {}
        for fmt in media.formats[1:]:
            idx = fmt.format_id.rsplit("-", 1)[-1]
            self._t_files[idx] = fmt
            self.t_list.add(idx, (fmt.label, human_size(fmt.filesize_bytes), fmt.note or ""))
        self.t_list.on_change = self._refresh_t_count
        self._refresh_t_count()

    def _refresh_t_count(self):
        picked = self.t_list.checked()
        total = sum(self._t_files[i].filesize_bytes or 0 for i in picked)
        self.t_count.configure(text=f"{len(picked)} of {len(self._t_files)} files — {human_size(total, '0 B')}")

    # ---------------------------------------------------------------- http -- #
    def _build_http_panel(self, media):
        inner = self._section("File")
        fmt = media.formats[0]
        grid = ctk.CTkFrame(inner, fg_color="transparent")
        grid.pack(fill="x")
        self.http_name = tk.StringVar(value=media.filename or media.title)
        rows = [
            ("File name", None),
            ("Size", human_size(fmt.filesize_bytes, "Unknown (the server didn't say)")),
            ("Type", media.extra.get("content_type") or "unknown"),
            ("Resume", "Supported — pause/resume and multi-connection download" if media.resumable
             else "Not supported by this server — pausing would restart it"),
            ("Server", media.source_site),
        ]
        for r, (label, value) in enumerate(rows):
            ctk.CTkLabel(grid, text=label, font=font(12, "bold"), text_color=COLORS["muted"]).grid(row=r, column=0, sticky="w", pady=3)
            if value is None:
                glass_entry(grid, textvariable=self.http_name, height=30, width=480).grid(row=r, column=1, sticky="w", padx=12)
            else:
                ctk.CTkLabel(grid, text=value, font=font(12)).grid(row=r, column=1, sticky="w", padx=12)
        opts = self._section("Options")
        row = ctk.CTkFrame(opts, fg_color="transparent")
        row.pack(fill="x", pady=2)
        self.http_conn = tk.StringVar(value="Default")
        ctk.CTkLabel(row, text="Connections", font=font(12, "bold"), text_color=COLORS["muted"], width=110,
                     anchor="w").pack(side="left")
        menu = ctk.CTkOptionMenu(row, values=_CONN_CHOICES, variable=self.http_conn, width=100)
        menu.pack(side="left")
        if not media.resumable:
            menu.configure(state="disabled")
        ctk.CTkLabel(row, text=f"Default: {self.settings.get('http_connections', 4)} — more connections help mostly "
                               "when the server limits each one.", text_color=COLORS["faint"], font=font(11)).pack(
            side="left", padx=10)
        row = ctk.CTkFrame(opts, fg_color="transparent")
        row.pack(fill="x", pady=(8, 2))
        self.http_checksum = tk.StringVar()
        ctk.CTkLabel(row, text="Checksum", font=font(12, "bold"), text_color=COLORS["muted"], width=110,
                     anchor="w").pack(side="left")
        glass_entry(row, textvariable=self.http_checksum, height=30, width=420,
                    placeholder="optional — e.g. sha256:… from the download page").pack(side="left")
        self.limit_var = tk.StringVar()
        row = ctk.CTkFrame(opts, fg_color="transparent")
        row.pack(fill="x", pady=(8, 2))
        ctk.CTkLabel(row, text="Speed limit", font=font(12, "bold"), text_color=COLORS["muted"], width=110,
                     anchor="w").pack(side="left")
        glass_entry(row, textvariable=self.limit_var, height=30, width=100, placeholder="KB/s").pack(side="left")

    # -------------------------------------------------------------- submit -- #
    def submit(self):
        if self._batch:
            self._queue_batch()
            return
        if self.media is None:
            return
        try:
            jobs = self._queue()
        except ValueError as e:
            messagebox.showwarning("Check your choices", str(e), parent=self.win)
            return
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("Couldn't queue the download", str(e), parent=self.win)
            return
        if jobs is None:
            return  # queued on a background thread (large playlist)
        self._after_queue(jobs)

    def _after_queue(self, jobs):
        self.app.toast("info", f"Added {len(jobs)} download{'s' if len(jobs) != 1 else ''}"
                       if len(jobs) != 1 else f"Added — {truncate(jobs[0].title, 40)}")
        if self.win.winfo_exists():
            self.win.destroy()

    def _queue(self):
        url = self.url_var.get().strip()
        target = self._target_dir()
        if self.engine == "torrent":
            picked = self.t_list.checked()
            if not picked:
                raise ValueError("Select at least one file.")
            key = "torrent-all" if len(picked) == len(self._t_files) else "torrent-files-" + ",".join(picked)
            label = "Entire torrent" if key == "torrent-all" else f"{len(picked)} of {len(self._t_files)} files"
            return [self._add(url=url, kind="torrent", quality_label=label, format_key=key,
                                         download_dir=target, category="Torrent", engine="torrent", title=self.media.title)]
        if self.engine == "http":
            name = sanitize_filename(self.http_name.get())
            override = name if name != (self.media.filename or "") else None
            options = self._common_options()
            if self.http_conn.get() != "Default":
                options["connections"] = int(self.http_conn.get())
            checksum = self.http_checksum.get().strip()
            if checksum:
                try:
                    algo, digest = parse_checksum(checksum)
                except ValueError as e:
                    raise ValueError(f"Checksum: {e}") from e
                options["checksum"] = f"{algo}:{digest}"
            return [self._add(url=url, kind="file", quality_label="Direct file", format_key="file",
                              download_dir=target, engine="http", title=name, filename_override=override,
                              options=options)]
        if self.media.is_playlist:
            return self._queue_playlist(target)
        return self._queue_single(url, target)

    def _common_options(self):
        limit = (self.limit_var.get() or "").strip() if hasattr(self, "limit_var") else ""
        options = {}
        if limit:
            if not limit.isdigit():
                raise ValueError("The speed limit must be a whole number of KB/s.")
            options["speed_limit_kbps"] = int(limit)
        return options

    def _queue_single(self, url, target):
        mode = self.mode_var.get()
        options = self._common_options()
        filename = (self.filename_var.get() or "").strip() or None
        start, end = parse_timestamp(self.section_start.get()), parse_timestamp(self.section_end.get())
        if start is not None or end is not None:
            if start is not None and end is not None and end <= start:
                raise ValueError("The section end must be after its start.")
            options["section"] = {"start": start, "end": end}
        title = self.media.title
        jobs = []
        if mode == "Subtitles only":
            subs = self._selected_subs()
            if not subs:
                raise ValueError("Select at least one subtitle language.")
            for f in subs:
                kind, label, key, _ = resolve_format_selection(f.format_id)
                jobs.append(self._add(url=url, kind=kind, quality_label=label, format_key=key,
                                                 download_dir=target, engine="media", title=f"{title} [{f.lang_code}]",
                                                 options={"subtitle_format": self.sub_format_var.get()}))
            return jobs
        if mode == "Thumbnail":
            kind, label, key, _ = resolve_format_selection("thumbnail")
            return [self._add(url=url, kind=kind, quality_label=label, format_key=key, download_dir=target,
                                         engine="media", title=title, filename_override=filename)]

        subs = self._selected_subs()
        if subs and mode == "Video":
            options["subtitles"] = {"langs": sorted({f.lang_code for f in subs}),
                                    "auto": any(f.is_auto for f in subs),
                                    "format": self.sub_format_var.get(), "embed": bool(self.sub_embed_var.get())}
        options["embed_metadata"] = bool(self.meta_var.get())
        options["embed_thumbnail"] = bool(self.thumb_var.get())
        if self.custom_var.get() and self.custom_spec.get().strip():
            spec = self.custom_spec.get().strip()
            if not _CUSTOM_FORMAT_RE.match(spec):
                raise ValueError("That format selector contains characters that aren't allowed.")
            format_id = f"fmt:{spec}"
            options["container"] = self.container_var.get()
            kind = "video"
        elif mode == "Video":
            format_id = self.video_choice.get()
            options.update({"container": self.container_var.get(), "prefer_h264": bool(self.h264_var.get()),
                            "embed_chapters": bool(self.chapters_var.get())})
        else:
            codec = self.audio_codec_var.get()
            kbps = self.audio_kbps_var.get()
            if codec == "original":
                format_id = "source-audio"
            elif codec == "mp3":
                format_id = f"mp3-{kbps}" if kbps.isdigit() else "audio-mp3"
            elif tools.AUDIO_CODECS[codec][2]:
                format_id = f"audio-{codec}"
            else:
                format_id = f"audio-{codec}-{kbps}" if kbps.isdigit() else f"audio-{codec}"
        kind, label, key, bitrate = resolve_format_selection(format_id)
        return [self._add(url=url, kind=kind, quality_label=label, format_key=key, download_dir=target,
                                     audio_bitrate=bitrate, engine="media", title=title, filename_override=filename,
                                     options=options)]

    def _queue_playlist(self, target):
        picked = self.pl_list.checked()
        if not picked:
            raise ValueError("Select at least one item.")
        format_id = self._pl_choice[self.pl_quality_menu.get()]
        kind, label, key, bitrate = resolve_format_selection(format_id)
        playlist_title = self.media.title
        folder = os.path.join(target, sanitize_filename(playlist_title)) if self.pl_subfolder.get() else target
        base_options = {"embed_metadata": bool(self.settings.get("embed_metadata", True))}
        if self.pl_single_task.get():
            items = _compress_indices(int(i) for i in picked) if len(picked) != self.pl_list.count() else None
            options = dict(base_options, **({"playlist_items": items} if items else {}))
            return [self._add(url=self.url_var.get().strip(), kind=kind, quality_label=label, format_key=key,
                                         is_playlist=True, download_dir=target if self.pl_subfolder.get() else target,
                                         audio_bitrate=bitrate, engine="media", title=playlist_title, options=options)]

        entries = [self._pl_entries[i] for i in picked]
        number = self.pl_number.get()
        self.download_btn.configure(state="disabled")
        self._set_status(f"Queuing {len(entries)} items…", busy=True)

        def worker():
            jobs = []
            try:
                for n, entry in enumerate(entries, 1):
                    options = dict(base_options, playlist_title=playlist_title)
                    if number:
                        options["playlist_index"] = entry["index"]
                    jobs.append(self._add(url=entry["url"], kind=kind, quality_label=label, format_key=key,
                                                     download_dir=folder, audio_bitrate=bitrate, engine="media",
                                                     title=entry["title"], options=options))
                    if n % 50 == 0:
                        self.app.call_in_main(self._set_status, f"Queuing {n}/{len(entries)} items…", "muted", True)
            except Exception as e:  # noqa: BLE001
                self.app.call_in_main(messagebox.showerror, "Couldn't queue every item", str(e))
            self.app.call_in_main(self._after_queue, jobs)

        threading.Thread(target=worker, name="mdm-queue-playlist", daemon=True).start()
        return None

    # --------------------------------------------------------------- batch -- #
    def _show_batch(self, links):
        """Several links: list them with their engine, flag duplicates and
        fetch sizes of direct files in the background (never invented)."""
        self._token += 1
        token = self._token
        self.media, self.engine = None, None
        self._clear_body()
        self._set_badge("BATCH")
        queued_urls = {j.url for j in self.manager.list_jobs() if not j.from_history
                       and j.status not in ("Completed", "Failed", "Cancelled")}
        seen, rows = set(), []
        for link in links:
            if link in seen:
                continue
            seen.add(link)
            note = "already in the list" if link in queued_urls else None
            if note is None:
                try:
                    hist = self.app.history.find_by_url(link)
                except Exception:  # noqa: BLE001
                    hist = []
                note = f"downloaded {hist[0]['date']}" if hist else None
            rows.append({"url": link, "engine": classify(link), "note": note})
        dupes = sum(1 for r in rows if r["note"])
        self._set_status(f"{len(rows)} links" + (f" — {dupes} already downloaded or queued (unticked)" if dupes else ""),
                         "ok")
        inner = self._section(f"{len(rows)} links — each is queued with your default quality")
        self.batch_list = CheckList(inner, ("name", "engine", "size", "note"), ("Link", "Engine", "Size", "Note"),
                                    (360, 70, 90, 170), height=min(14, max(4, len(rows))))
        self.batch_list.frame.pack(fill="both", expand=True)
        self._batch = rows
        for i, r in enumerate(rows):
            self.batch_list.add(str(i), (truncate(r["url"], 70), ENGINE_LABEL.get(r["engine"], r["engine"] or "?"),
                                         "", r["note"] or ""))
            if r["note"]:
                self.batch_list._set(str(i), False)
        tools_row = ctk.CTkFrame(inner, fg_color="transparent")
        tools_row.pack(fill="x", pady=(6, 0))
        glass_button(tools_row, "All", lambda: self.batch_list.set_all(True), width=60, height=28).pack(side="left")
        glass_button(tools_row, "None", lambda: self.batch_list.set_all(False), width=60, height=28).pack(side="left", padx=6)
        self.batch_total = ctk.CTkLabel(tools_row, text="", text_color=COLORS["muted"], font=font(11))
        self.batch_total.pack(side="right")
        self.download_btn.configure(state="normal")
        self._update_dir_label()

        def sizes():
            from ..providers.http import probe_url
            total, known = 0, 0
            for i, r in enumerate(rows[:60]):
                if token != self._token:
                    return
                if r["engine"] != "http" or url_path_ext(r["url"]) in ("", "html", "htm"):
                    continue
                try:
                    size = probe_url(r["url"], self.settings).get("size")
                except Exception:  # noqa: BLE001 - size is informational
                    size = None
                if size:
                    total, known = total + size, known + 1
                self.app.call_in_main(self._batch_size, token, i, size, total, known)

        threading.Thread(target=sizes, name="mdm-batch-sizes", daemon=True).start()

    def _batch_size(self, token, index, size, total, known):
        if token != self._token or not self.win.winfo_exists():
            return
        tree = self.batch_list.tree
        values = list(tree.item(str(index), "values"))
        values[3] = human_size(size, "unknown")
        tree.item(str(index), values=values)
        self.batch_total.configure(text=f"≈ {human_size(total, '0 B')} for {known} file(s) with a known size")

    def _queue_batch(self):
        picked = [self._batch[int(i)] for i in self.batch_list.checked()]
        if not picked:
            messagebox.showwarning("Nothing selected", "Tick at least one link.", parent=self.win)
            return
        jobs, failed = [], []
        for r in picked:
            try:
                engine = r["engine"]
                format_id, category, options = default_selection(engine, self.settings)
                from ..providers import resolve_format_selection as resolve_any
                kind, label, key, bitrate = resolve_any(r["url"], format_id, engine=engine)
                target = self.save_dir_override or resolve_download_dir(self.settings, kind, category)
                jobs.append(self._add(url=r["url"], kind=kind, quality_label=label, format_key=key,
                                      audio_bitrate=bitrate or "192", download_dir=target, category=category,
                                      engine=engine, options=options))
            except Exception as e:  # noqa: BLE001 - reported below
                failed.append(f"{truncate(r['url'], 60)}: {e}")
        if failed:
            messagebox.showwarning("Some links weren't added", "\n".join(failed[:12]), parent=self.win)
        if jobs:
            self._after_queue(jobs)
