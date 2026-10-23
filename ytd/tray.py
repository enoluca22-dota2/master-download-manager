"""Windows system tray icon, with a safe no-op fallback when pystray/Pillow
aren't available."""

import os
import threading

from .config import APP_ICON_FILE, APP_NAME
from .logging_setup import get_logger

log = get_logger()

try:
    import pystray
    from PIL import Image, ImageDraw
    _available = True
except ImportError:
    _available = False


def tray_supported():
    return _available


def _make_icon_image():
    if os.path.isfile(APP_ICON_FILE):
        try:
            return Image.open(APP_ICON_FILE)
        except OSError:
            pass
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((2, 2, size - 2, size - 2), radius=14, fill=(59, 130, 246, 255))
    draw.text((size / 2, size / 2), "M", fill=(255, 255, 255, 255), anchor="mm")
    return img


class TrayIcon:
    """Wraps pystray.Icon. All callbacks are invoked on pystray's own thread,
    so callers must marshal back to their own thread (e.g. Tk's root.after)."""

    def __init__(self, on_open=None, on_pause_all=None, on_resume_all=None,
                 on_settings=None, on_exit=None, on_new=None, on_toggle_limit=None, is_limited=None):
        self._icon = None
        self._thread = None
        if not _available:
            return

        items = [pystray.MenuItem(f"Open {APP_NAME}", self._wrap(on_open), default=True)]
        if on_new:
            items.append(pystray.MenuItem("New Download…", self._wrap(on_new)))
        items += [
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Pause All", self._wrap(on_pause_all)),
            pystray.MenuItem("Resume All", self._wrap(on_resume_all)),
        ]
        if on_toggle_limit:
            # checkmark shows whether "limited mode" (a reduced speed cap) is on
            items.append(pystray.MenuItem("Limit download speed", self._wrap(on_toggle_limit),
                                          checked=lambda item: bool(is_limited and is_limited())))
        items += [
            pystray.MenuItem("Settings", self._wrap(on_settings)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Exit", self._wrap(on_exit)),
        ]
        menu = pystray.Menu(*items)
        self._icon = pystray.Icon("master-download-manager", _make_icon_image(), APP_NAME, menu)

    @staticmethod
    def _wrap(callback):
        def handler(icon, item):
            if callback:
                try:
                    callback()
                except Exception:
                    log.exception("Tray menu callback failed")
        return handler

    def start(self):
        if not _available or self._icon is None:
            return
        self._thread = threading.Thread(target=self._icon.run, daemon=True)
        self._thread.start()

    def refresh(self):
        if self._icon is not None:
            try:
                self._icon.update_menu()
            except Exception:
                pass

    def notify(self, title, message):
        if self._icon is not None:
            try:
                self._icon.notify(message, title)
            except Exception:
                pass

    def stop(self):
        if self._icon is not None:
            try:
                self._icon.stop()
            except Exception:
                pass
