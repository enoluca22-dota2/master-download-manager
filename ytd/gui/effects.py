"""Galactic Glass runtime effects: the deep-space atmosphere backdrop,
GlassSpinner (loading), and GlassToast (notifications).

Kept separate from theme.py because everything here either renders to a
Canvas or generates an image at runtime, instead of just returning a
styled CTk widget — theme.py stays pure "static style", this module is
where the few, deliberately small pieces of actual animation/rendering
code live.

Performance stance (this app is a download manager — it must stay
responsive while a transfer is in progress):
  * The nebula backdrop is a PIL image generated ONCE per (size, mode) and
    cached in memory (see _ATMOSPHERE_CACHE) — never redrawn per-frame,
    never redrawn on a timer. It only regenerates when a window is
    resized into a new 40px size bucket, debounced.
  * GlassSpinner only runs its .after() tick loop while started; it's
    idle (zero timers) the rest of the time.
  * GlassToast windows are short-lived, self-destroying Toplevels — no
    persistent per-frame cost once dismissed.
  * All decorative (non-functional) motion is skipped outright when
    ytd.gui.theme.reduced_motion() is on.
"""

import random
import tkinter as tk

from PIL import Image, ImageDraw, ImageFilter, ImageTk

from ytd.gui import theme as glass_theme
from ytd.gui.theme import COLORS, font as glass_font

# ---------------------------------------------------------------------
# Atmosphere backdrop — a cached, procedurally generated deep-space /
# nebula gradient used as the window background behind the glass UI.
# ---------------------------------------------------------------------
_ATMOSPHERE_CACHE = {}
_ATMOSPHERE_CACHE_MAX = 16  # bounds memory across repeated resizes


def _hex_to_rgb(value):
    return tuple(int(value[i:i + 2], 16) for i in (1, 3, 5))


def _generate_atmosphere_image(width, height, dark):
    palette = glass_theme._DARK if dark else glass_theme._LIGHT
    top = _hex_to_rgb(palette["bg_top"])
    mid = _hex_to_rgb(palette["bg_mid"])
    bottom = _hex_to_rgb(palette["bg_bottom"])

    # Fast vertical gradient: build a 1px-wide column with the right color
    # at every row, then stretch it horizontally — O(height), never
    # O(width * height) in pure Python.
    column = Image.new("RGB", (1, height))
    px = column.load()
    half = height / 2
    for y in range(height):
        if y <= half:
            t = y / half if half else 0
            r = tuple(round(top[i] + (mid[i] - top[i]) * t) for i in range(3))
        else:
            t = (y - half) / half if half else 0
            r = tuple(round(mid[i] + (bottom[i] - mid[i]) * t) for i in range(3))
        px[0, y] = r
    base = column.resize((width, height), Image.BILINEAR)

    # Soft nebula glow blobs — a handful of large, heavily blurred
    # ellipses in indigo/violet/magenta, composited at low opacity so they
    # read as distant light rather than colored shapes. Deterministic
    # per-size seed so the same window size always renders the same
    # backdrop (no visible "jump" on minor resizes / reopen).
    glow_layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    rng = random.Random(f"{width}x{height}x{dark}")
    blob_colors = [palette["nebula_indigo"], palette["nebula_violet"], palette["nebula_magenta"]]
    blob_alpha = 130 if dark else 100
    for i, hexcolor in enumerate(blob_colors):
        r, g, b = _hex_to_rgb(hexcolor)
        bw, bh = width * rng.uniform(0.5, 0.75), height * rng.uniform(0.55, 0.85)
        cx = width * (0.15 + 0.3 * i + rng.uniform(-0.05, 0.05))
        cy = height * rng.uniform(0.1, 0.5)
        blob = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(blob)
        draw.ellipse([cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2], fill=(r, g, b, blob_alpha))
        blob = blob.filter(ImageFilter.GaussianBlur(radius=min(width, height) * 0.12))
        glow_layer = Image.alpha_composite(glow_layer, blob)

    atmosphere = Image.alpha_composite(base.convert("RGBA"), glow_layer).convert("RGB")

    # A sparse, static starfield baked directly into the cached image —
    # deliberately NOT animated (no twinkle timer) so the background has
    # zero ongoing runtime cost.
    if dark:
        draw = ImageDraw.Draw(atmosphere)
        star_count = max(24, (width * height) // 22000)
        for _ in range(star_count):
            x, y = rng.uniform(0, width), rng.uniform(0, height * 0.85)
            brightness = rng.randint(110, 230)
            size = rng.choice((0, 0, 0, 1))
            color = (brightness, brightness, min(255, brightness + 20))
            if size == 0:
                draw.point((x, y), fill=color)
            else:
                draw.ellipse([x, y, x + 1, y + 1], fill=color)

    return ImageTk.PhotoImage(atmosphere)


def _get_atmosphere(width, height, dark):
    bucket = 40
    key = (max(bucket, round(width / bucket) * bucket), max(bucket, round(height / bucket) * bucket), dark)
    cached = _ATMOSPHERE_CACHE.get(key)
    if cached is not None:
        return cached
    image = _generate_atmosphere_image(key[0], key[1], dark)
    if len(_ATMOSPHERE_CACHE) >= _ATMOSPHERE_CACHE_MAX:
        _ATMOSPHERE_CACHE.pop(next(iter(_ATMOSPHERE_CACHE)))
    _ATMOSPHERE_CACHE[key] = image
    return image


def mount_atmosphere(win, dark=True):
    """Replaces a window's flat background with the cached galactic
    atmosphere image, kept in sync with the window's current size. Safe to
    call again on the same window (e.g. after a dark/light mode switch) —
    it reuses the existing background label instead of stacking a new one.
    """
    label = getattr(win, "_ytd_atmosphere_label", None)
    if label is None or not label.winfo_exists():
        label = tk.Label(win, bd=0, highlightthickness=0)
        label.place(x=0, y=0, relwidth=1, relheight=1)
        label.lower()
        win._ytd_atmosphere_label = label
    win._ytd_atmosphere_dark = dark
    win._ytd_atmosphere_size = None

    def render():
        if not win.winfo_exists() or not label.winfo_exists():
            return
        w, h = win.winfo_width(), win.winfo_height()
        if w < 20 or h < 20:
            return
        size_key = (round(w / 40) * 40, round(h / 40) * 40, win._ytd_atmosphere_dark)
        if win._ytd_atmosphere_size == size_key:
            return
        win._ytd_atmosphere_size = size_key
        photo = _get_atmosphere(w, h, win._ytd_atmosphere_dark)
        label.configure(image=photo)
        label.image = photo  # keep a reference alive on the widget itself

    def on_configure(_event=None):
        # Debounced: a resize fires many <Configure> events per second;
        # only the last one (100ms after motion stops) actually renders.
        pending = getattr(win, "_ytd_atmosphere_after", None)
        if pending is not None:
            try:
                win.after_cancel(pending)
            except Exception:
                pass
        win._ytd_atmosphere_after = win.after(100, render)

    if not getattr(win, "_ytd_atmosphere_bound", False):
        win.bind("<Configure>", on_configure, add="+")
        win._ytd_atmosphere_bound = True
    win.after(20, render)
    return label


def refresh_atmosphere_mode(win, dark):
    """Call after a light/dark mode switch to regenerate the backdrop for
    the new palette (cheap — just looks up/builds the new cached image)."""
    if not getattr(win, "_ytd_atmosphere_label", None):
        mount_atmosphere(win, dark=dark)
        return
    win._ytd_atmosphere_dark = dark
    win._ytd_atmosphere_size = None
    mount_atmosphere(win, dark=dark)


# ---------------------------------------------------------------------
# Dithered glass surfaces — a hybrid of real transparency and a solid
# tint, for the app's big structural chrome (toolbar, sidebar, status
# bar). Plain colorkey transparency (see backdrop.TRANSPARENT_KEY) is
# real, but its visual quality is entirely at the mercy of whatever the
# user's desktop wallpaper happens to look like at that exact spot -- on
# a flat/solid-colored desktop it just reads as a flat rectangle again,
# which defeats the point. Ordered dithering fixes this: roughly half
# the pixels are painted the literal transparent key (a genuine hole
# DWM fills with the live Mica backdrop), interleaved at fine, irregular
# grain with the other half painted a solid brand tint. The eye blends
# fine dither at normal viewing distance into a single translucent color
# — so the surface always reads as intentional tinted glass, with
# genuine backdrop-driven variation layered on top wherever the desktop
# actually has some (a photo/gradient wallpaper shows through vividly;
# a flat wallpaper still looks like deliberate dark glass, not a bug).
#
# Generated once as a small tile (cheap: tile_size^2 pixels, not the
# full surface) and repeat-pasted to fill the target size — repeat
# pasting is a handful of fast C-level blits, not a pure-Python
# per-pixel loop over the whole surface.
# ---------------------------------------------------------------------
_DITHER_TILE_CACHE = {}
_DITHER_SURFACE_CACHE = {}
_DITHER_CACHE_MAX = 24
_DITHER_TILE_SIZE = 48


def _get_dither_tile(tint_hex, density, key=None):
    key = key or glass_backdrop_key()
    cache_key = (tint_hex, density, key)
    tile = _DITHER_TILE_CACHE.get(cache_key)
    if tile is not None:
        return tile
    tint_rgb = _hex_to_rgb(tint_hex)
    key_rgb = _hex_to_rgb(key)
    rng = random.Random(str(cache_key))
    size = _DITHER_TILE_SIZE
    tile = Image.new("RGB", (size, size))
    px = tile.load()
    for y in range(size):
        for x in range(size):
            px[x, y] = key_rgb if rng.random() < density else tint_rgb
    if len(_DITHER_TILE_CACHE) >= _DITHER_CACHE_MAX:
        _DITHER_TILE_CACHE.pop(next(iter(_DITHER_TILE_CACHE)))
    _DITHER_TILE_CACHE[cache_key] = tile
    return tile


def glass_backdrop_key():
    from ytd.gui.backdrop import TRANSPARENT_KEY
    return TRANSPARENT_KEY


def _get_dither_surface(width, height, tint_hex, density):
    bucket = 24
    size_key = (max(bucket, round(width / bucket) * bucket), max(bucket, round(height / bucket) * bucket))
    cache_key = (size_key, tint_hex, density)
    cached = _DITHER_SURFACE_CACHE.get(cache_key)
    if cached is not None:
        return cached
    tile = _get_dither_tile(tint_hex, density)
    w, h = size_key
    surface = Image.new("RGB", (w, h))
    ts = _DITHER_TILE_SIZE
    for y in range(0, h, ts):
        for x in range(0, w, ts):
            surface.paste(tile, (x, y))
    photo = ImageTk.PhotoImage(surface)
    if len(_DITHER_SURFACE_CACHE) >= _DITHER_CACHE_MAX:
        _DITHER_SURFACE_CACHE.pop(next(iter(_DITHER_SURFACE_CACHE)))
    _DITHER_SURFACE_CACHE[cache_key] = photo
    return photo


def mount_dither_glass(frame, tint_hex, density=0.45, enabled=True):
    """Fills `frame` (a plain rectangular CTkFrame -- toolbar/sidebar/
    status bar, or a whole window, corner_radius=0) with the dithered
    glass blend described above. When `enabled` is False (real
    transparency unsupported on this system), just paints `frame` the
    solid tint instead -- same visual family, no dependency on a
    technique that doesn't work here. Call this right after creating the
    bare frame, before packing/placing any of its real children, so they
    stack visually on top.

    Idempotent: safe to call again on the same frame (e.g. after a dark/
    light mode switch, or a re-probe) -- reuses the existing background
    label and its resize binding instead of stacking a new one each time.
    """
    if not enabled:
        # frame is either a CTk widget (fg_color) or a plain tk.Frame
        # (bg) -- try both, ignore whichever doesn't apply.
        try:
            frame.configure(fg_color=tint_hex)
        except Exception:
            try:
                frame.configure(bg=tint_hex)
            except Exception:
                pass
        return None

    label = getattr(frame, "_ytd_dither_label", None)
    if label is None or not label.winfo_exists():
        label = tk.Label(frame, bd=0, highlightthickness=0)
        label.place(x=0, y=0, relwidth=1, relheight=1)
        label.lower()
        frame._ytd_dither_label = label
    frame._ytd_dither_tint = tint_hex
    frame._ytd_dither_density = density
    frame._ytd_dither_size = None

    def render():
        if not frame.winfo_exists() or not label.winfo_exists():
            return
        w, h = frame.winfo_width(), frame.winfo_height()
        if w < 4 or h < 4:
            return
        bucket = 24
        size_key = (round(w / bucket) * bucket, round(h / bucket) * bucket)
        if frame._ytd_dither_size == size_key:
            return
        frame._ytd_dither_size = size_key
        photo = _get_dither_surface(w, h, frame._ytd_dither_tint, frame._ytd_dither_density)
        label.configure(image=photo)
        label.image = photo
        label.lower()

    def on_configure(_event=None):
        pending = getattr(frame, "_ytd_dither_after", None)
        if pending is not None:
            try:
                frame.after_cancel(pending)
            except Exception:
                pass
        frame._ytd_dither_after = frame.after(100, render)

    if not getattr(frame, "_ytd_dither_bound", False):
        frame.bind("<Configure>", on_configure, add="+")
        frame._ytd_dither_bound = True
    frame.after(20, render)
    return label


# ---------------------------------------------------------------------
# GlassSpinner — a small rotating glass arc for real async work (torrent
# metadata detection, etc). Only ticks while started; fully idle when
# stopped. Not gated by reduced_motion — a loading indicator is functional
# feedback ("is this still working?"), not decoration.
# ---------------------------------------------------------------------
class GlassSpinner(tk.Canvas):
    def __init__(self, parent, size=18, color=None, thickness=2, **kwargs):
        bg = kwargs.pop("bg", None) or _resolve(COLORS["surface_deep"])
        super().__init__(parent, width=size, height=size, bg=bg, highlightthickness=0, **kwargs)
        self._size = size
        self._thickness = thickness
        self._color = color or _resolve(COLORS["cyan"])
        self._angle = 0
        self._running = False
        self._after_id = None

    def start(self):
        if self._running:
            return
        self._running = True
        self._tick()

    def stop(self):
        self._running = False
        if self._after_id is not None:
            try:
                self.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None
        try:
            self.delete("all")
        except Exception:
            pass

    def _tick(self):
        if not self._running or not self.winfo_exists():
            return
        self.delete("all")
        pad = self._thickness + 1
        self.create_arc(pad, pad, self._size - pad, self._size - pad, start=self._angle, extent=110,
                         style="arc", outline=self._color, width=self._thickness)
        self._angle = (self._angle - 24) % 360
        self._after_id = self.after(45, self._tick)


def _resolve(color_pair):
    """CTk widgets accept a (light, dark) tuple directly; plain tk.Canvas
    does not — this picks the value for the app's current appearance
    mode."""
    import customtkinter as ctk
    if isinstance(color_pair, tuple):
        return color_pair[1] if ctk.get_appearance_mode() == "Dark" else color_pair[0]
    return color_pair


# ---------------------------------------------------------------------
# GlassToast — a small, non-blocking, self-dismissing notification,
# replacing blocking messagebox popups for fire-and-forget events
# (download started / completed / failed). Multiple toasts stack upward
# from the bottom-right corner of the owning window.
# ---------------------------------------------------------------------
_TOAST_KIND_STYLE = {
    "success": ("✓", "success"),
    "info": ("↓", "primary"),
    "warning": ("⚠", "warning"),
    "error": ("✕", "danger"),
}


def show_toast(root, kind, message, duration_ms=3500):
    if not root.winfo_exists():
        return
    icon, color_key = _TOAST_KIND_STYLE.get(kind, _TOAST_KIND_STYLE["info"])
    accent = _resolve(COLORS[color_key])

    stack = getattr(root, "_ytd_toast_stack", None)
    if stack is None:
        stack = []
        root._ytd_toast_stack = stack

    toast = tk.Toplevel(root)
    toast.overrideredirect(True)
    toast.attributes("-topmost", True)
    try:
        toast.attributes("-alpha", 0.0 if not glass_theme.reduced_motion() else 0.97)
    except Exception:
        pass

    bg = _resolve(COLORS["surface_raised"])
    border = accent
    outer = tk.Frame(toast, bg=border, bd=0)
    outer.pack(fill="both", expand=True)
    inner = tk.Frame(outer, bg=bg, bd=0)
    inner.pack(fill="both", expand=True, padx=1, pady=1)

    row = tk.Frame(inner, bg=bg)
    row.pack(fill="both", expand=True, padx=14, pady=10)
    tk.Label(row, text=icon, bg=bg, fg=accent, font=("Segoe UI", 13, "bold")).pack(side="left", padx=(0, 10))
    tk.Label(row, text=message, bg=bg, fg=_resolve(COLORS["text"]), font=("Segoe UI", 10),
             wraplength=260, justify="left").pack(side="left")

    toast.update_idletasks()
    w, h = toast.winfo_reqwidth(), toast.winfo_reqheight()
    root_x, root_y = root.winfo_rootx(), root.winfo_rooty()
    root_w, root_h = root.winfo_width(), root.winfo_height()
    margin = 16
    stack_offset = sum(getattr(t, "_ytd_toast_height", 0) + 8 for t in stack if t.winfo_exists())
    x = root_x + root_w - w - margin
    y = root_y + root_h - h - margin - stack_offset
    toast.geometry(f"{w}x{h}+{x}+{y}")
    toast._ytd_toast_height = h

    stack.append(toast)

    def dismiss():
        if toast not in stack:
            return
        stack.remove(toast)
        if toast.winfo_exists():
            toast.destroy()

    def fade_in(step=0, steps=6):
        if not toast.winfo_exists():
            return
        try:
            toast.attributes("-alpha", min(1.0, (step + 1) / steps))
        except Exception:
            return
        if step + 1 < steps:
            toast.after(16, fade_in, step + 1, steps)

    def fade_out(step=0, steps=6):
        if not toast.winfo_exists():
            dismiss()
            return
        try:
            toast.attributes("-alpha", max(0.0, 1 - (step + 1) / steps))
        except Exception:
            dismiss()
            return
        if step + 1 < steps:
            toast.after(16, fade_out, step + 1, steps)
        else:
            dismiss()

    if glass_theme.reduced_motion():
        toast.after(duration_ms, dismiss)
    else:
        fade_in()
        toast.after(duration_ms, fade_out)

    toast.bind("<Button-1>", lambda _e: fade_out() if not glass_theme.reduced_motion() else dismiss())
    return toast
