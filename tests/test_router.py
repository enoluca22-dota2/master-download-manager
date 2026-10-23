"""Automatic engine detection."""

import pytest

from ytd.providers import find_provider
from ytd.router import classify, default_selection, detect_engine, looks_like_direct_file

from local_http_server import LocalHTTPServer


@pytest.mark.parametrize("url,expected", [
    ("magnet:?xt=urn:btih:abcdef1234567890&dn=ubuntu", "torrent"),
    (r"C:\Users\me\Downloads\debian.torrent", "torrent"),
    ("https://releases.example.org/ubuntu-24.04.iso.torrent", "torrent"),
    ("https://example.org/get/file.torrent?download=1", "torrent"),
    ("https://www.youtube.com/watch?v=aqz-KE-bpKQ", "media"),
    ("https://youtu.be/aqz-KE-bpKQ", "media"),
    ("https://www.youtube.com/watch?v=abc&list=PLxyz&index=3", "media"),
    ("https://www.youtube.com/playlist?list=PLxyz", "media"),
    ("https://vimeo.com/76979871", "media"),
    ("https://soundcloud.com/artist/track", "media"),
    ("https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe", "http"),
    ("https://cdimage.example.org/debian-12.iso", "http"),
    ("https://example.com/archive.tar.gz", "http"),
    ("https://example.com/report.pdf", "http"),
    ("https://example.com/some/article", "media"),  # unknown page -> scanned for media
    ("ftp://example.com/file.zip", None),
    ("not a url", None),
    ("", None),
])
def test_classify(url, expected):
    assert classify(url) == expected


def test_find_provider_matches_classification():
    assert find_provider("magnet:?xt=urn:btih:abc").name == "torrent"
    assert find_provider("https://www.youtube.com/watch?v=aqz-KE-bpKQ").name == "media"
    assert find_provider("https://example.com/setup.msi").name == "http"
    assert find_provider("https://example.com/page").name == "media"
    assert find_provider("https://example.com/page", engine="http").name == "http"
    assert find_provider("javascript:alert(1)") is None


def test_direct_file_detection_ignores_query_strings():
    assert looks_like_direct_file("https://x.org/a/b/file.zip?token=1#frag")
    assert not looks_like_direct_file("https://x.org/watch?file=movie.mp4")


def test_detect_engine_probes_unknown_urls():
    with LocalHTTPServer() as srv:
        srv.files["blob"] = b"x" * 100
        assert detect_engine(f"{srv.base}/page")[0] == "media"
        assert detect_engine(f"{srv.base}/files/blob")[0] == "http"  # binary content type, no extension
        assert detect_engine(f"{srv.base}/cd/blob")[0] == "http"
        assert detect_engine(f"{srv.base}/torrentfile")[0] == "torrent"
        # with probing disabled, unknown pages default to the media scan
        assert detect_engine(f"{srv.base}/files/blob", probe=False)[0] == "media"


def test_detect_engine_rejects_garbage():
    engine, reason = detect_engine("hello world")
    assert engine is None and reason


def test_default_selection_follows_settings(settings_store):
    settings_store.update({"default_mode": "Video", "default_quality": "1080p (Full HD)"})
    assert default_selection("media", settings_store)[0] == "video-1080"
    settings_store.update({"default_mode": "MP3", "default_audio_quality": "320 kbps (Maximum)",
                           "default_audio_format": "mp3"})
    assert default_selection("media", settings_store)[0] == "mp3-320"
    settings_store.update({"default_audio_format": "flac"})
    assert default_selection("media", settings_store)[0] == "audio-flac"
    assert default_selection("torrent", settings_store)[0] == "torrent-all"
    assert default_selection("http", settings_store)[0] == "file"


def test_streaming_manifests_route_to_media_engine():
    from ytd.router import classify
    assert classify("https://cdn.example.com/live/master.m3u8") == "media"
    assert classify("https://cdn.example.com/vod/manifest.mpd?token=x") == "media"
    assert classify("https://cdn.example.com/files/movie.mp4") == "http"
