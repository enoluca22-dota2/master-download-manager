"""Icons from the Segoe Fluent Icons font that ships with Windows 11
(Segoe MDL2 Assets on Windows 10 has the same code points). Vector glyphs
stay crisp at any DPI and cost nothing to bundle or license; where neither
font exists a plain-text fallback is used."""

import tkinter.font as tkfont

GLYPHS = {
    "add": "", "paste": "", "play": "", "pause": "", "stop": "",
    "delete": "", "settings": "", "folder": "", "open": "", "download": "",
    "upload": "", "refresh": "", "link": "", "search": "", "queue": "",
    "queue_play": "", "clock": "", "calendar": "", "zip": "", "video": "",
    "music": "", "picture": "", "document": "", "app": "", "torrent": "",
    "globe": "", "done": "", "unfinished": "", "error": "", "all": "",
    "chev_down": "", "chev_right": "", "chev_up": "", "more": "", "speed": "",
    "up": "", "down": "", "info": "", "filter": "", "check": "",
    "copy": "", "file": "", "magnet": "", "history": "", "power": "",
    "limit": "", "edit": "", "warning": "", "retry": "", "browser": "",
    "close": "", "sort_up": "", "sort_down": "", "plus": "", "export": "",
    "import": "", "restart": "",
}
FALLBACK = {
    "add": "+", "paste": "⎘", "play": "▶", "pause": "⏸", "stop": "■", "delete": "🗑", "settings": "⚙",
    "folder": "📁", "chev_down": "▾", "chev_right": "▸", "chev_up": "▴", "check": "✓", "error": "✕",
    "done": "✓", "search": "⌕", "up": "↑", "down": "↓", "sort_up": "▴", "sort_down": "▾",
}

_FAMILY = {"value": None}


def family(root=None):
    if _FAMILY["value"] is None:
        try:
            names = set(tkfont.families(root))
        except Exception:  # noqa: BLE001 - no Tk root yet
            names = set()
        _FAMILY["value"] = next((f for f in ("Segoe Fluent Icons", "Segoe MDL2 Assets") if f in names), "")
    return _FAMILY["value"]


def glyph(name):
    if family():
        return GLYPHS.get(name, "")
    return FALLBACK.get(name, "•")


def icon_font(size=14):
    return (family() or "Segoe UI Symbol", size)
