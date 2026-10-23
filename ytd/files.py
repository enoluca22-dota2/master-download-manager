"""Filename and path safety helpers shared by every engine.

Downloaded names come from untrusted sources (page titles, HTTP headers,
torrent metadata). Everything that becomes part of a path goes through here.
"""

import os
import re

_ILLEGAL_CHARS_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
KNOWN_MEDIA_EXTENSIONS = (
    "mp4", "mp3", "m4a", "webm", "mkv", "opus", "ogg", "wav", "flac", "aac",
    "jpg", "jpeg", "png", "webp", "srt", "vtt", "ass",
)
# Files that run code when opened — "Open file" asks for confirmation first.
EXECUTABLE_EXTENSIONS = {
    "exe", "msi", "msix", "appx", "bat", "cmd", "com", "scr", "pif", "ps1", "vbs", "vbe", "js", "jse", "wsf",
    "wsh", "hta", "cpl", "lnk", "reg", "jar", "msc", "dll", "sys", "gadget", "application", "appref-ms",
}


def sanitize_filename(name, max_len=180):
    """Strip characters that are illegal on Windows/macOS/Linux filesystems,
    collapse whitespace, avoid reserved device names, and never return an
    empty name."""
    name = (name or "").strip()
    name = _ILLEGAL_CHARS_RE.sub("", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    if not name:
        name = "download"
    stem = name.split(".", 1)[0].upper()
    if stem in _WINDOWS_RESERVED:
        name = f"_{name}"
    if len(name) > max_len:
        root, ext = os.path.splitext(name)
        if ext and len(ext) <= 12:
            name = root[: max_len - len(ext)].rstrip(" .") + ext
        else:
            name = name[:max_len].rstrip(" .")
    return name or "download"


def sanitize_filename_override(name, max_len=150):
    """Sanitizes a user-supplied filename for use as a base name. Any known
    media extension the user typed is stripped — the real extension is the
    one the download actually produces, so a video can't end up named ".mp3"."""
    base = sanitize_filename(name, max_len=max_len)
    root, _, ext = base.rpartition(".")
    if root and ext.lower() in KNOWN_MEDIA_EXTENSIONS:
        base = root
    return base or "download"


def unique_path(directory, filename):
    """A path that doesn't collide with an existing file, using the
    'name (1).ext' convention instead of overwriting."""
    base, ext = os.path.splitext(filename)
    candidate = os.path.join(directory, filename)
    counter = 1
    while os.path.exists(candidate):
        candidate = os.path.join(directory, f"{base} ({counter}){ext}")
        counter += 1
    return candidate


def is_within(directory, path):
    """True if `path` resolves inside `directory` (path-traversal guard)."""
    directory = os.path.normcase(os.path.realpath(directory))
    path = os.path.normcase(os.path.realpath(path))
    try:
        return os.path.commonpath([directory, path]) == directory
    except ValueError:  # different drives on Windows
        return False


def validate_save_dir(path):
    """A save folder requested through the local API must be an absolute,
    local, non-traversing path. Returns the normalized path or raises
    ValueError."""
    path = (path or "").strip()
    if not path:
        raise ValueError("Empty folder path.")
    if "\x00" in path:
        raise ValueError("Invalid folder path.")
    if not os.path.isabs(path):
        raise ValueError("The save folder must be an absolute path (for example C:\\Users\\you\\Downloads).")
    if path.startswith(("\\\\", "//")):
        raise ValueError("Network (UNC) paths aren't allowed as a save folder from the browser.")
    parts = re.split(r"[\\/]+", path)
    if any(p == ".." for p in parts):
        raise ValueError("The save folder can't contain '..'.")
    return os.path.normpath(path)


def is_executable_file(path):
    return os.path.splitext(path or "")[1].lstrip(".").lower() in EXECUTABLE_EXTENSIONS
