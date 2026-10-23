"""Generates the Master Download Manager icon (multi-resolution mdm.ico) and
the browser-extension PNG icons from one drawing routine, so the desktop
app, tray and extensions share one original brand mark: a blue-to-violet
rounded square with a "download into tray" arrow and, at larger sizes, the
"MDM" monogram. Not a copy of any third-party logo.

Run:
    python build_icons.py
"""

import os

from PIL import Image, ImageDraw, ImageFont

APP_DIR = os.path.dirname(os.path.abspath(__file__))
TOP = (82, 134, 255)       # COLORS["primary"] (dark theme)
BOTTOM = (124, 106, 232)   # COLORS["violet"]
WHITE = (255, 255, 255, 255)
SIZES = (16, 24, 32, 48, 64, 128, 256)


def _font(size):
    for path in (r"C:\Windows\Fonts\segoeuib.ttf", r"C:\Windows\Fonts\arialbd.ttf"):
        if os.path.isfile(path):
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default()


def draw_icon(size):
    scale = 4  # draw large, downsample: smooth edges at every size
    s = size * scale
    gradient = Image.new("RGBA", (s, s))
    gd = ImageDraw.Draw(gradient)
    for y in range(s):
        t = y / max(s - 1, 1)
        gd.line((0, y, s, y), fill=tuple(round(TOP[i] + (BOTTOM[i] - TOP[i]) * t) for i in range(3)) + (255,))
    mask = Image.new("L", (s, s), 0)
    pad = round(s * 0.04)
    ImageDraw.Draw(mask).rounded_rectangle((pad, pad, s - pad, s - pad), radius=round(s * 0.23), fill=255)
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    img.paste(gradient, (0, 0), mask)
    d = ImageDraw.Draw(img)

    with_text = size >= 64
    cx = s / 2
    top = s * (0.40 if with_text else 0.20)
    bottom = s * (0.70 if with_text else 0.66)
    stroke = max(2 * scale, round(s * 0.085))
    head = s * (0.17 if with_text else 0.22)
    d.line((cx, top, cx, bottom - head * 0.6), fill=WHITE, width=stroke)
    d.polygon([(cx - head, bottom - head), (cx + head, bottom - head), (cx, bottom)], fill=WHITE)
    tray_y = s * (0.80 if with_text else 0.80)
    tray_w = s * (0.27 if with_text else 0.32)
    d.rounded_rectangle((cx - tray_w, tray_y - stroke / 2, cx + tray_w, tray_y + stroke / 2), radius=stroke // 2, fill=WHITE)

    if with_text:
        font = _font(round(s * 0.20))
        text = "MDM"
        bbox = d.textbbox((0, 0), text, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        d.text((cx - tw / 2 - bbox[0], s * 0.20 - th / 2 - bbox[1]), text, font=font, fill=WHITE)
    return img.resize((size, size), Image.LANCZOS)


def main():
    images = {size: draw_icon(size) for size in SIZES}
    ico_path = os.path.join(APP_DIR, "mdm.ico")
    images[256].save(ico_path, sizes=[(s, s) for s in SIZES], append_images=[images[s] for s in SIZES[:-1]])
    print(f"Wrote {ico_path}")
    images[256].save(os.path.join(APP_DIR, "mdm.png"))
    for ext_dir in ("extension-chrome", "extension-firefox"):
        icon_dir = os.path.join(APP_DIR, ext_dir, "icons")
        os.makedirs(icon_dir, exist_ok=True)
        for size in (16, 32, 48, 128):
            images[size].save(os.path.join(icon_dir, f"icon-{size}.png"))
        print(f"Wrote {icon_dir}")


if __name__ == "__main__":
    main()
