import pytest
from yt_dlp.utils import DownloadCancelled, DownloadError

from ytd.models import DownloadJob
from ytd.providers.base import UnsupportedMediaError
from ytd.providers.youtube import (
    classify_error, ffmpeg_available, is_valid_youtube_url,
    resolve_format_selection, _dedupe_video_formats, _label_for_tier,
    _quality_tier_for_height, PauseRequested, is_playlist_url,
    _playlist_entries_from_raw, _best_thumbnail_url, detect_playlist,
    YouTubeProvider, HTTP_CHUNK_SIZE_BYTES, CONCURRENT_FRAGMENT_DOWNLOADS,
)


@pytest.mark.parametrize("url,expected", [
    ("https://www.youtube.com/watch?v=abc123", True),
    ("https://youtu.be/abc123", True),
    ("http://m.youtube.com/watch?v=abc123", True),
    ("https://music.youtube.com/watch?v=abc123", True),
    ("youtube.com/watch?v=abc123", True),
    ("https://vimeo.com/12345", False),
    ("not a url", False),
    ("", False),
])
def test_is_valid_youtube_url(url, expected):
    assert is_valid_youtube_url(url) is expected


def test_ffmpeg_resolution_prefers_custom_then_bundled_then_path(monkeypatch, tmp_path):
    from ytd import tools

    custom = tmp_path / "custom" / "ffmpeg.exe"
    custom.parent.mkdir()
    custom.write_bytes(b"")
    bundled_dir = tmp_path / "tools"
    (bundled_dir / "ffmpeg").mkdir(parents=True)
    (bundled_dir / "ffmpeg" / "ffmpeg.exe").write_bytes(b"")
    monkeypatch.setattr(tools, "TOOLS_DIR", str(bundled_dir))
    monkeypatch.setattr(tools, "_exe", lambda name: name + ".exe")
    monkeypatch.setattr(tools.shutil, "which", lambda name: "/usr/bin/ffmpeg")

    assert tools.find_ffmpeg(str(custom)) == str(custom)
    assert tools.find_ffmpeg(str(custom.parent)) == str(custom)  # a folder works too
    assert tools.find_ffmpeg("") == str(bundled_dir / "ffmpeg" / "ffmpeg.exe")
    assert ffmpeg_available() is True


def test_ffmpeg_available_falls_back_to_path_and_reports_missing(monkeypatch, tmp_path):
    from ytd import tools

    monkeypatch.setattr(tools, "TOOLS_DIR", str(tmp_path / "no-tools-here"))
    monkeypatch.setattr(tools.shutil, "which", lambda name: "/usr/bin/ffmpeg")
    assert ffmpeg_available() is True
    monkeypatch.setattr(tools.shutil, "which", lambda name: None)
    assert ffmpeg_available() is False


def test_classify_error_maps_known_cases():
    assert classify_error(PauseRequested("x")) == "Paused."
    assert classify_error(DownloadCancelled("x")) == "Download cancelled."
    assert "FFmpeg" in classify_error(DownloadError("ffmpeg exited with code 1"))
    assert "private" in classify_error(DownloadError("ERROR: Private video. Sign in if you've been granted access"))
    assert "network" in classify_error(OSError("urlopen error timed out")).lower()
    assert "isn't supported" in classify_error(DownloadError("Unsupported URL"))


def test_classify_error_disk_space():
    err = OSError("no space")
    err.errno = 28
    assert "disk space" in classify_error(err).lower()


@pytest.mark.parametrize("format_id,expected_kind", [
    ("video-1080", "video"),
    ("mp3-320", "audio"),
    ("source-audio", "audio"),
])
def test_resolve_format_selection_kinds(format_id, expected_kind):
    kind, label, key, bitrate = resolve_format_selection(format_id)
    assert kind == expected_kind
    assert key == format_id


def test_resolve_format_selection_rejects_unknown():
    with pytest.raises(ValueError):
        resolve_format_selection("something-else")


def test_resolve_format_selection_thumbnail():
    kind, label, key, bitrate = resolve_format_selection("thumbnail")
    assert kind == "image"
    assert key == "thumbnail"


def test_resolve_format_selection_subtitle():
    kind, label, key, bitrate = resolve_format_selection("subtitle-en")
    assert kind == "subtitle"
    assert "en" in label
    assert key == "subtitle-en"


@pytest.mark.parametrize("height,expected", [
    (144, "Low"), (360, "Low"), (480, "Medium"), (720, "Medium"),
    (1080, "High"), (2160, "High"), (None, None),
])
def test_quality_tier_for_height(height, expected):
    assert _quality_tier_for_height(height) == expected


def test_dedupe_video_formats_only_lists_available_resolutions():
    # A source that only actually offers 360p and 1080p must never surface
    # 720p or 4K as if they were downloadable.
    raw = [
        {"vcodec": "avc1", "height": 360, "width": 640, "filesize": 1000},
        {"vcodec": "avc1", "height": 1080, "width": 1920, "filesize": 5000},
        {"vcodec": "none", "height": 1080, "acodec": "opus"},  # audio-only, must be ignored
    ]
    result = _dedupe_video_formats(raw)
    tiers = sorted(result.keys())
    assert tiers == [360, 1080]
    assert _label_for_tier(360) == "360p"
    assert _label_for_tier(1080) == "1080p (Full HD)"


@pytest.mark.parametrize("url,expected", [
    ("https://www.youtube.com/watch?v=abc123&list=PLxyz", True),
    ("https://www.youtube.com/playlist?list=PLxyz", True),
    ("https://www.youtube.com/watch?v=abc123", False),
    ("https://youtu.be/abc123", False),
    ("", False),
    (None, False),
])
def test_is_playlist_url(url, expected):
    assert is_playlist_url(url) is expected


def test_best_thumbnail_url_picks_largest():
    thumbs = [
        {"url": "small.jpg", "width": 120, "height": 90},
        {"url": "big.jpg", "width": 1280, "height": 720},
        {"url": "medium.jpg", "width": 480, "height": 360},
    ]
    assert _best_thumbnail_url(thumbs) == "big.jpg"
    assert _best_thumbnail_url([]) is None
    assert _best_thumbnail_url(None) is None


def test_playlist_entries_from_raw_skips_unresolvable_entries():
    raw = [
        {"id": "v1", "title": "First video", "duration": 120, "thumbnails": [{"url": "t1.jpg", "width": 100, "height": 100}]},
        None,  # a deleted/private video yt-dlp couldn't resolve
        {"id": "v2", "title": "Second video", "url": "https://www.youtube.com/watch?v=v2", "duration": 45},
        {"title": "No id or url at all"},  # unusable — must be dropped, not guessed
    ]
    entries = _playlist_entries_from_raw(raw)
    assert len(entries) == 2
    assert entries[0] == {
        "video_id": "v1", "url": "https://www.youtube.com/watch?v=v1",
        "title": "First video", "duration_seconds": 120, "thumbnail": "t1.jpg",
    }
    assert entries[1]["video_id"] == "v2"
    assert entries[1]["thumbnail"] is None


def test_playlist_entries_from_raw_empty():
    assert _playlist_entries_from_raw([]) == []
    assert _playlist_entries_from_raw(None) == []


def test_detect_playlist_rejects_non_playlist_url():
    with pytest.raises(UnsupportedMediaError):
        detect_playlist("https://www.youtube.com/watch?v=abc123")


def test_detect_playlist_returns_entries(monkeypatch):
    import ytd.providers.youtube as yt

    class FakeYDL:
        def __init__(self, opts):
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, url, download=False):
            return {
                "_type": "playlist",
                "title": "My Playlist",
                "entries": [
                    {"id": "a", "title": "Video A", "duration": 10},
                    {"id": "b", "title": "Video B", "duration": 20},
                ],
            }

    monkeypatch.setattr(yt.yt_dlp, "YoutubeDL", FakeYDL)
    result = detect_playlist("https://www.youtube.com/playlist?list=PLxyz")
    assert result["playlist_title"] == "My Playlist"
    assert result["entry_count"] == 2
    assert [e["video_id"] for e in result["entries"]] == ["a", "b"]


@pytest.mark.parametrize("kind,format_key", [
    ("video", "video-720"),
    ("audio", "mp3-192"),
    ("audio", "source-audio"),
])
def test_build_download_opts_uses_concurrent_chunked_fetch(kind, format_key):
    # Every real download (video, MP3, and source-quality audio) should use
    # multi-connection chunked fetching for max real throughput, not
    # yt-dlp's single-connection default.
    job = DownloadJob(
        url="https://www.youtube.com/watch?v=abc123", kind=kind, quality_label="720p (HD)",
        format_key=format_key, is_playlist=False, download_dir="/tmp/downloads",
    )
    opts = YouTubeProvider().build_download_opts(job, progress_hook=lambda d: None)
    assert opts["http_chunk_size"] == HTTP_CHUNK_SIZE_BYTES
    assert opts["concurrent_fragment_downloads"] == CONCURRENT_FRAGMENT_DOWNLOADS
    assert opts["http_chunk_size"] > 0
    assert opts["concurrent_fragment_downloads"] > 1
    # No artificial cap must ever be set alongside the chunking.
    assert "ratelimit" not in opts


def test_detect_playlist_rejects_non_playlist_result(monkeypatch):
    import ytd.providers.youtube as yt

    class FakeYDL:
        def __init__(self, opts):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, url, download=False):
            return {"_type": "video", "title": "Just a video"}

    monkeypatch.setattr(yt.yt_dlp, "YoutubeDL", FakeYDL)
    with pytest.raises(UnsupportedMediaError):
        detect_playlist("https://www.youtube.com/watch?v=abc&list=PLxyz")
