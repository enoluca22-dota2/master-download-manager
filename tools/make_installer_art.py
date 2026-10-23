"""Draws the setup wizard artwork (MDM branding, original artwork) into
installer/art/: the tall welcome/finish image and the small header image,
light + dark variants, at 100% and 200% scale.

    python tools/make_installer_art.py
"""

import os
import sys

from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from build_icons import _font, draw_icon  # noqa: E402

OUT = os.path.join(ROOT, "installer", "art")
INDIGO = (115, 100, 232)
VIOLET = (150, 92, 230)
THEMES = {
    "light": {"bg": (250, 250, 252), "text": (20, 20, 28), "muted": (100, 100, 115)},
    "dark": {"bg": (18, 18, 22), "text": (245, 245, 250), "muted": (160, 160, 175)},
}


def tall(theme, scale):
    w, h = 164 * scale, 314 * scale
    t = THEMES[theme]
    img = Image.new("RGB", (w, h), t["bg"])
    d = ImageDraw.Draw(img)
    # soft accent band at the top
    band = round(h * 0.42)
    for y in range(band):
        k = y / band
        col = tuple(round(INDIGO[i] + (VIOLET[i] - INDIGO[i]) * k) for i in range(3))
        fade = (1 - k) ** 1.6  # fades fully into the background
        col = tuple(round(c * fade + t["bg"][i] * (1 - fade)) for i, c in enumerate(col))
        d.line((0, y, w, y), fill=col)
    icon = draw_icon(96 * scale)
    img.paste(icon, ((w - icon.width) // 2, round(38 * scale)), icon)
    f1, f2 = _font(round(17 * scale)), _font(round(10 * scale))
    for i, line in enumerate(("Master", "Download", "Manager")):
        tw = d.textlength(line, font=f1)
        d.text(((w - tw) / 2, round((168 + i * 21) * scale)), line, fill=t["text"], font=f1)
    sub = "Files · Media · Torrents"
    tw = d.textlength(sub, font=f2)
    d.text(((w - tw) / 2, round(240 * scale)), sub, fill=t["muted"], font=f2)
    return img


def small(theme, scale):
    s = 55 * scale
    img = Image.new("RGB", (s, s), THEMES[theme]["bg"])
    icon = draw_icon(48 * scale)
    img.paste(icon, ((s - icon.width) // 2, (s - icon.height) // 2), icon)
    return img


def main():
    os.makedirs(OUT, exist_ok=True)
    for theme in THEMES:
        for scale in (1, 2):
            tall(theme, scale).save(os.path.join(OUT, f"wizard-{theme}-{scale}x.bmp"))
            small(theme, scale).save(os.path.join(OUT, f"wizard-small-{theme}-{scale}x.bmp"))
    print(f"Installer art written to {OUT}")


if __name__ == "__main__":
    main()
