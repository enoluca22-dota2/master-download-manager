"""Real Windows 11 Mica/Acrylic window backdrop via direct DWM API calls
(ctypes, no third-party dependency).

Why not a wrapper library: the most popular one (pywinstyles) has a known,
open compatibility issue specifically with CustomTkinter — the toolkit this
app uses — where its style application silently doesn't apply (see
Akascape/py-window-styles discussion #11), and the library itself has had
no release since January 2024. The underlying technique is ~20 lines of
well-documented Win32 API calls (DwmSetWindowAttribute), so implementing it
directly here is both more reliable and one fewer dependency to maintain.

Reference: https://learn.microsoft.com/en-us/windows/apps/develop/ui/system-backdrops

Gracefully does nothing (returns False) on non-Windows, on Windows versions
before the backdrop API existed (pre-11 22H2), or if anything about the
call fails — callers never need to branch on platform themselves.
"""

import sys

_DWMWA_USE_IMMERSIVE_DARK_MODE = 20
_DWMWA_WINDOW_CORNER_PREFERENCE = 33
_DWMWA_SYSTEMBACKDROP_TYPE = 38

_DWMWCP_ROUND = 2

# DWM_SYSTEMBACKDROP_TYPE values.
BACKDROP_MICA = 2
BACKDROP_ACRYLIC = 3
BACKDROP_MICA_ALT = 4


def get_hwnd(tk_root):
    """The real top-level window handle for a Tk root/Toplevel. Tk's own
    winfo_id() returns the drawing-surface child window, not the actual
    top-level frame DWM attributes apply to — GetParent resolves that."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        return ctypes.windll.user32.GetParent(tk_root.winfo_id())
    except Exception:
        return None


# ---------------------------------------------------------------------
# Real per-pixel transparency. DWMWA_SYSTEMBACKDROP_TYPE alone does NOT
# make Mica/Acrylic visible on a Tkinter window: Tk always paints its
# entire client rectangle with an opaque color (there is no "unpainted"
# region for DWM to composite the backdrop into), so every CTkFrame just
# sits on top of the backdrop as a solid rectangle no matter what DWM
# attribute is set. Confirmed empirically: enabling the backdrop alone
# produced screenshots indistinguishable from a plain dark-blue app.
#
# The fix is Windows Tk's colorkey layered-window transparency
# (`-transparentcolor`): any pixel exactly matching TRANSPARENT_KEY
# becomes a genuine hole in the window, and DWM composites the real
# system backdrop (Mica) into that hole -- verified by sampling pixel
# RGB values in the transparent region with the backdrop on (32,32,32)
# vs explicitly forced off (12,12,12): the rendered content measurably
# changes, proving this is real compositing, not a static fallback fill.
#
# TRANSPARENT_KEY is a garish, deliberately "wrong-looking" magenta: if
# it were ever painted visibly (a bug, or an unsupported platform where
# the attribute silently no-ops), it fails loudly and is easy to spot,
# rather than silently blending in as a slightly-off dark blue would.
# ---------------------------------------------------------------------
TRANSPARENT_KEY = "#FE01FE"


def enable_glass_transparency(tk_root, key=TRANSPARENT_KEY):
    """Marks this window as a colorkey-transparent layered window. Every
    pixel painted exactly `key` becomes a real hole showing whatever DWM
    composites behind the window (Mica, if apply_glass_backdrop() was
    also called). Returns False (never raises) on non-Windows or if the
    running Tk build doesn't support the attribute -- callers should fall
    back to an opaque surface in that case."""
    if sys.platform != "win32":
        return False
    try:
        tk_root.wm_attributes("-transparentcolor", key)
        return True
    except Exception:
        return False


def _colorref(hex_color):
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    return b << 16 | g << 8 | r


def set_titlebar_theme(tk_window, dark, caption=None, text=None):
    """Dark/light title bar matching the app theme (Windows 10 2004+/11).
    On Windows 11 the caption can also take the toolbar's colour, so the
    window reads as one surface even when Windows paints title bars with the
    accent colour. Forces a frame redraw so the change is visible immediately."""
    if sys.platform != "win32":
        return False
    hwnd = get_hwnd(tk_window)
    if not hwnd:
        return False
    try:
        import ctypes
        value = ctypes.c_int(1 if dark else 0)
        dwm = ctypes.windll.dwmapi
        dwm.DwmSetWindowAttribute(hwnd, _DWMWA_USE_IMMERSIVE_DARK_MODE, ctypes.byref(value), ctypes.sizeof(value))
        for attr, color in ((35, caption), (36, text)):  # DWMWA_CAPTION_COLOR / DWMWA_TEXT_COLOR (Win11; ignored before)
            if color:
                ref = ctypes.c_uint32(_colorref(color))
                dwm.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(ref), ctypes.sizeof(ref))
        # SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED
        ctypes.windll.user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x0002 | 0x0001 | 0x0004 | 0x0010 | 0x0020)
        return True
    except Exception:
        return False


def apply_glass_backdrop(tk_root, mica=True, dark=True, rounded_corners=True):
    """Applies a real DWM-composited Mica (or Acrylic) backdrop plus rounded
    corners and immersive dark titlebar to a Tk window. Safe to call on any
    platform/Windows version — returns True only if it actually applied."""
    if sys.platform != "win32":
        return False
    hwnd = get_hwnd(tk_root)
    if not hwnd:
        return False
    try:
        import ctypes
        dwmapi = ctypes.windll.dwmapi

        dark_value = ctypes.c_int(1 if dark else 0)
        dwmapi.DwmSetWindowAttribute(
            hwnd, _DWMWA_USE_IMMERSIVE_DARK_MODE, ctypes.byref(dark_value), ctypes.sizeof(dark_value)
        )

        if rounded_corners:
            corner_value = ctypes.c_int(_DWMWCP_ROUND)
            dwmapi.DwmSetWindowAttribute(
                hwnd, _DWMWA_WINDOW_CORNER_PREFERENCE, ctypes.byref(corner_value), ctypes.sizeof(corner_value)
            )

        backdrop_value = ctypes.c_int(BACKDROP_MICA if mica else BACKDROP_ACRYLIC)
        result = dwmapi.DwmSetWindowAttribute(
            hwnd, _DWMWA_SYSTEMBACKDROP_TYPE, ctypes.byref(backdrop_value), ctypes.sizeof(backdrop_value)
        )
        return result == 0
    except Exception:
        return False
