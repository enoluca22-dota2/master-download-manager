import pytest

from ytd.providers.base import UnsupportedMediaError
from ytd.providers.torrent import (
    TorrentProvider, is_magnet_uri, is_torrent_source,
    _media_info_from_file_list, resolve_format_selection, libtorrent_available,
)


@pytest.mark.parametrize("url,expected", [
    ("magnet:?xt=urn:btih:abcdef1234567890", True),
    ("MAGNET:?xt=urn:btih:ABCDEF", True),
    ("https://example.com/video.mp4", False),
    ("", False),
])
def test_is_magnet_uri(url, expected):
    assert is_magnet_uri(url) is expected


@pytest.mark.parametrize("url,expected", [
    ("magnet:?xt=urn:btih:abc", True),
    ("C:\\downloads\\linux.torrent", True),
    ("https://example.com/file.torrent", True),
    ("https://www.youtube.com/watch?v=abc", False),
])
def test_is_torrent_source(url, expected):
    assert is_torrent_source(url) is expected


def test_provider_supports_only_torrent_sources():
    provider = TorrentProvider()
    assert provider.supports("magnet:?xt=urn:btih:abc") is True
    assert provider.supports("https://www.youtube.com/watch?v=abc") is False


@pytest.mark.parametrize("format_id,expected_label_fragment", [
    ("torrent-all", "Entire torrent"),
    ("torrent-file-0", "File #0"),
    ("torrent-file-3", "File #3"),
])
def test_resolve_format_selection(format_id, expected_label_fragment):
    kind, label, key, bitrate = resolve_format_selection(format_id)
    assert kind == "torrent"
    assert expected_label_fragment in label
    assert key == format_id
    assert bitrate is None


def test_resolve_format_selection_rejects_unknown():
    with pytest.raises(ValueError):
        resolve_format_selection("not-a-real-format")


def test_media_info_from_file_list_reports_only_real_files():
    # Simulates what a real torrent_info's file storage would report — a
    # multi-file torrent with two files of known, real sizes.
    files = [("Movie/video.mkv", 1_500_000_000), ("Movie/subs.srt", 4_200)]
    media = _media_info_from_file_list("Movie Collection", files, "magnet:?xt=urn:btih:abc", 1_500_004_200)

    assert media.title == "Movie Collection"
    assert media.source_site == "Torrent"
    format_ids = {f.format_id for f in media.formats}
    assert format_ids == {"torrent-all", "torrent-file-0", "torrent-file-1"}

    whole = next(f for f in media.formats if f.format_id == "torrent-all")
    assert whole.filesize_bytes == 1_500_004_200
    assert "2 files" in whole.label

    video_file = next(f for f in media.formats if f.format_id == "torrent-file-0")
    assert video_file.filesize_bytes == 1_500_000_000
    assert video_file.ext == "mkv"
    assert video_file.label == "video.mkv"


def test_media_info_from_file_list_single_file_label_is_singular():
    media = _media_info_from_file_list("Single File Torrent", [("clip.mp4", 1000)], "magnet:?xt=urn:btih:abc", 1000)
    whole = next(f for f in media.formats if f.format_id == "torrent-all")
    assert "1 file)" in whole.label  # not "1 files"


def test_media_info_never_invents_missing_extension():
    media = _media_info_from_file_list("No Extension", [("README", 500)], "magnet:?xt=urn:btih:abc", 500)
    file_format = next(f for f in media.formats if f.format_id == "torrent-file-0")
    assert file_format.ext is None  # no extension present — must not be fabricated


def test_detect_without_libtorrent_raises_clear_error(monkeypatch):
    import ytd.providers.torrent as torrent_mod
    monkeypatch.setattr(torrent_mod, "lt", None)
    provider = TorrentProvider()
    with pytest.raises(UnsupportedMediaError, match="libtorrent"):
        provider.detect("magnet:?xt=urn:btih:abc")


def test_detect_rejects_non_torrent_url():
    provider = TorrentProvider()
    with pytest.raises(UnsupportedMediaError):
        provider.detect("https://www.youtube.com/watch?v=abc")


def test_detect_missing_torrent_file_raises_clear_error():
    if not libtorrent_available():
        pytest.skip("libtorrent not installed in this environment")
    provider = TorrentProvider()
    with pytest.raises(UnsupportedMediaError, match="not found"):
        provider.detect(r"C:\this\path\does\not\exist.torrent")
