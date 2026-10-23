"""Locating and probing the external executables the engines use (FFmpeg,
FFprobe, deno). Bundled copies win over anything on PATH so the packaged app
behaves the same on every PC; a user-chosen FFmpeg path (Settings) wins over
both. All probing uses argument lists — never a shell."""

import functools
import os
import shutil
import subprocess
import sys

from .config import TOOLS_DIR

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# Audio conversions offered to the user, keyed by yt-dlp's FFmpegExtractAudio
# codec name -> (display label, ffmpeg encoder that must exist, lossless?)
AUDIO_CODECS = {
    "mp3": ("MP3", "libmp3lame", False),
    "m4a": ("M4A (AAC)", "aac", False),
    "opus": ("Opus", "libopus", False),
    "vorbis": ("OGG Vorbis", "libvorbis", False),
    "flac": ("FLAC", "flac", True),
    "wav": ("WAV", "pcm_s16le", True),
}


def _exe(name):
    return name + (".exe" if sys.platform == "win32" else "")


def bundled_tool(*parts):
    path = os.path.join(TOOLS_DIR, *parts)
    return path if os.path.isfile(path) else None


def find_ffmpeg(custom_path=""):
    """Path to ffmpeg, or None. custom_path may be the binary or its folder."""
    if custom_path:
        candidate = custom_path
        if os.path.isdir(candidate):
            candidate = os.path.join(candidate, _exe("ffmpeg"))
        if os.path.isfile(candidate):
            return candidate
    return bundled_tool("ffmpeg", _exe("ffmpeg")) or shutil.which("ffmpeg")


def find_ffprobe(custom_path=""):
    ffmpeg = find_ffmpeg(custom_path)
    if ffmpeg:
        sibling = os.path.join(os.path.dirname(ffmpeg), _exe("ffprobe"))
        if os.path.isfile(sibling):
            return sibling
    return shutil.which("ffprobe")


def ffmpeg_location(custom_path=""):
    """Directory to hand yt-dlp as `ffmpeg_location` (it finds both ffmpeg
    and ffprobe there), or None to let yt-dlp search PATH itself."""
    ffmpeg = find_ffmpeg(custom_path)
    return os.path.dirname(ffmpeg) if ffmpeg else None


def find_deno():
    return bundled_tool("deno", _exe("deno")) or shutil.which("deno")


def js_runtimes():
    """yt-dlp `js_runtimes` param. Prefers the bundled deno; falls back to a
    node already installed on the machine. Never enables remote downloads of
    solver scripts (that's `remote_components`, which MDM leaves empty)."""
    deno = find_deno()
    if deno:
        return {"deno": {"path": deno}}
    node = shutil.which("node")
    if node:
        return {"node": {"path": node}}
    return {"deno": {}}


@functools.lru_cache(maxsize=8)
def _encoders_for(ffmpeg_path):
    try:
        out = subprocess.run(
            [ffmpeg_path, "-hide_banner", "-encoders"],
            capture_output=True, text=True, timeout=20, creationflags=_NO_WINDOW,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return frozenset()
    names = set()
    for line in out.splitlines():
        parts = line.split()
        # Encoder rows look like: " A....D libmp3lame   libmp3lame MP3 ..."
        if len(parts) >= 2 and len(parts[0]) == 6 and parts[0][0] in "VAS":
            names.add(parts[1])
    return frozenset(names)


def ffmpeg_encoders(custom_path=""):
    ffmpeg = find_ffmpeg(custom_path)
    return _encoders_for(ffmpeg) if ffmpeg else frozenset()


def available_audio_codecs(custom_path=""):
    """Only conversions the installed/bundled FFmpeg can actually perform."""
    encoders = ffmpeg_encoders(custom_path)
    return [codec for codec, (_, encoder, _) in AUDIO_CODECS.items() if encoder in encoders]


def tool_status(custom_ffmpeg=""):
    ffmpeg = find_ffmpeg(custom_ffmpeg)
    deno = find_deno()
    return {
        "ffmpeg": ffmpeg,
        "ffmpeg_bundled": bool(ffmpeg) and os.path.normcase(ffmpeg).startswith(os.path.normcase(TOOLS_DIR)),
        "ffprobe": find_ffprobe(custom_ffmpeg),
        "deno": deno,
        "deno_bundled": bool(deno) and os.path.normcase(deno).startswith(os.path.normcase(TOOLS_DIR)),
    }
