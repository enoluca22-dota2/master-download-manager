"""Compatibility module: the YouTube-only provider grew into the general
Media Engine (ytd/providers/media.py). Existing imports keep working."""

import shutil  # noqa: F401  (kept: older tests/patches reference youtube.shutil)

import yt_dlp  # noqa: F401  (kept: tests patch youtube.yt_dlp.YoutubeDL)

from .media import (  # noqa: F401
    AUDIO_BITRATES, CONCURRENT_FRAGMENT_DOWNLOADS, HTTP_CHUNK_SIZE_BYTES, MP3_LABEL, RESOLUTION_LABELS,
    VIDEO_QUALITIES, MediaEngine, PauseRequested, UnsupportedMediaError, YouTubeProvider, _best_thumbnail_url,
    _dedupe_video_formats, _label_for_tier, _playlist_entries_from_raw, _quality_tier_for_height, classify_error,
    detect_playlist, ffmpeg_available, is_playlist_url, is_valid_youtube_url, resolve_format_selection,
)
