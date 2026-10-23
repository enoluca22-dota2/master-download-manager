"""Media engine: format analysis and yt-dlp option building (no network)."""

import os

import pytest

from ytd import tools
from ytd.models import DownloadJob
from ytd.providers import media as media_mod
from ytd.providers.base import UnsupportedMediaError
from ytd.providers.media import (
    MediaEngine, PARTIAL_DIR_NAME, _collect_filepaths, matching_extractor, resolve_format_selection,
    sanitize_template,
)


@pytest.fixture
def with_ffmpeg(monkeypatch):
    monkeypatch.setattr(tools, "find_ffmpeg", lambda custom="": r"C:\tools\ffmpeg\ffmpeg.exe")
    monkeypatch.setattr(tools, "ffmpeg_location", lambda custom="": r"C:\tools\ffmpeg")
    monkeypatch.setattr(tools, "available_audio_codecs", lambda custom="": ["mp3", "m4a", "opus", "vorbis", "flac", "wav"])


@pytest.fixture
def without_ffmpeg(monkeypatch):
    monkeypatch.setattr(tools, "find_ffmpeg", lambda custom="": None)
    monkeypatch.setattr(tools, "ffmpeg_location", lambda custom="": None)
    monkeypatch.setattr(tools, "available_audio_codecs", lambda custom="": [])


def job_for(format_id, tmp_path, options=None, is_playlist=False, filename=None):
    kind, label, key, bitrate = resolve_format_selection(format_id)
    job = DownloadJob(url="https://www.youtube.com/watch?v=aqz-KE-bpKQ", kind=kind, quality_label=label,
                      format_key=key, is_playlist=is_playlist, download_dir=str(tmp_path), audio_bitrate=bitrate,
                      options=options, filename_override=filename)
    return job


def pp_keys(opts):
    return [pp["key"] for pp in opts.get("postprocessors", [])]


FAKE_INFO = {
    "title": "Big Buck Bunny", "extractor_key": "Youtube", "duration": 635, "uploader": "Blender",
    "thumbnails": [{"url": "s.jpg", "width": 120, "height": 90}, {"url": "b.jpg", "width": 1280, "height": 720}],
    "chapters": [{"title": "a"}, {"title": "b"}],
    "formats": [
        {"format_id": "140", "ext": "m4a", "vcodec": "none", "acodec": "mp4a.40.2", "abr": 129, "filesize": 10},
        {"format_id": "251", "ext": "webm", "vcodec": "none", "acodec": "opus", "abr": 135},
        {"format_id": "134", "ext": "mp4", "vcodec": "avc1", "acodec": "none", "height": 360, "width": 640},
        {"format_id": "137", "ext": "mp4", "vcodec": "avc1", "acodec": "none", "height": 1080, "width": 1920,
         "filesize_approx": 5000},
        {"format_id": "sb0", "ext": "mhtml", "protocol": "mhtml", "vcodec": "none", "acodec": "none"},
    ],
    "subtitles": {"en": [{"name": "English", "ext": "vtt"}], "live_chat": [{"ext": "json"}]},
    "automatic_captions": {"de": [{"name": "German", "ext": "vtt"}]},
}


def test_single_media_info_lists_only_real_formats(with_ffmpeg):
    media = MediaEngine()._single_media_info("https://www.youtube.com/watch?v=x", FAKE_INFO)
    ids = [f.format_id for f in media.formats]
    assert "video-360" in ids and "video-1080" in ids
    assert "video-720" not in ids and "video-2160" not in ids  # not offered by the source
    assert "video-best" in ids and "video-worst" in ids
    assert {"mp3-128", "mp3-320", "source-audio", "audio-flac", "audio-opus"} <= set(ids)
    assert "subtitle-en" in ids and "autosub-de" in ids
    assert "subtitle-live_chat" not in ids
    auto = next(f for f in media.formats if f.format_id == "autosub-de")
    assert auto.is_auto is True
    assert media.chapters == 2
    assert [r["format_id"] for r in media.raw_formats] == ["140", "251", "134", "137"]  # storyboard dropped
    best = next(f for f in media.formats if f.format_id == "video-best")
    assert "1080p" in best.label


def test_single_media_info_without_ffmpeg_offers_no_conversions(without_ffmpeg):
    media = MediaEngine()._single_media_info("https://x", FAKE_INFO)
    ids = {f.format_id for f in media.formats}
    assert not any(i.startswith(("mp3-", "audio-")) for i in ids)
    assert "source-audio" in ids  # original audio needs no conversion


def test_drm_only_media_is_rejected(with_ffmpeg):
    info = {"title": "x", "formats": [{"format_id": "1", "has_drm": True, "vcodec": "avc1", "height": 720}]}
    with pytest.raises(UnsupportedMediaError, match="DRM"):
        MediaEngine()._single_media_info("https://x", info)


def test_playlist_media_info_uses_caps_and_entries(with_ffmpeg):
    info = {"_type": "playlist", "title": "My List", "id": "PL1", "entries": [
        {"id": "a", "title": "A", "ie_key": "Youtube", "url": "a", "duration": 3},
        None,
        {"id": "b", "title": "B", "url": "https://vimeo.com/2", "ie_key": "Vimeo"},
    ]}
    media = MediaEngine()._playlist_media_info("https://www.youtube.com/playlist?list=PL1", info, 1000)
    assert media.is_playlist and media.playlist_count == 2
    assert [e["index"] for e in media.entries] == [1, 2]
    assert media.entries[0]["url"] == "https://www.youtube.com/watch?v=a"
    assert media.entries[1]["url"] == "https://vimeo.com/2"
    labels = {f.format_id: f.label for f in media.formats}
    assert labels["video-1080"].startswith("Up to")


@pytest.mark.parametrize("format_id,kind,bitrate", [
    ("video-best", "video", None), ("video-worst", "video", None), ("audio-flac", "audio", None),
    ("audio-opus", "audio", None), ("audio-m4a-256", "audio", "256"), ("autosub-en", "subtitle", None),
    ("fmt:137+140", "video", None),
])
def test_resolve_new_format_ids(format_id, kind, bitrate):
    k, label, key, b = resolve_format_selection(format_id)
    assert (k, key, b) == (kind, format_id, bitrate)
    assert label


@pytest.mark.parametrize("bad", ["fmt:137;rm -rf /", "fmt:$(calc)", "audio-exe", "video-abc", "mp3-xyz", "fmt:" + "1" * 300])
def test_resolve_rejects_unsafe_or_unknown(bad):
    with pytest.raises(ValueError):
        resolve_format_selection(bad)


def test_video_preset_options(with_ffmpeg, tmp_path):
    opts = MediaEngine().build_download_opts(job_for("video-1080", tmp_path), lambda d: None)
    assert opts["format"].startswith("bv*[height<=1080]+ba/b[height<=1080]")
    assert opts["merge_output_format"] == "mp4"
    assert "FFmpegVideoRemuxer" in pp_keys(opts)
    assert "FFmpegMetadata" in pp_keys(opts)
    assert opts["noplaylist"] is True
    assert opts["paths"] == {"home": str(tmp_path), "temp": os.path.join(str(tmp_path), PARTIAL_DIR_NAME)}
    assert opts["outtmpl"]["default"].endswith(" [%(height)sp].%(ext)s")
    assert opts["remote_components"] == []
    assert opts["ffmpeg_location"] == r"C:\tools\ffmpeg"
    assert opts["overwrites"] is False and opts["continuedl"] is True
    assert "ratelimit" not in opts


def test_best_worst_custom_and_h264(with_ffmpeg, tmp_path):
    engine = MediaEngine()
    assert engine.build_download_opts(job_for("video-best", tmp_path), lambda d: None)["format"] == "bv*+ba/b"
    assert engine.build_download_opts(job_for("video-worst", tmp_path), lambda d: None)["format"] == "wv*+wa/w"
    custom = engine.build_download_opts(job_for("fmt:137+140", tmp_path), lambda d: None)
    assert custom["format"] == "137+140"
    h264 = engine.build_download_opts(job_for("video-720", tmp_path, {"prefer_h264": True, "container": "mkv"}),
                                      lambda d: None)
    assert h264["format_sort"] == ["res", "vcodec:h264", "acodec:aac"]
    assert h264["merge_output_format"] == "mkv"
    original = engine.build_download_opts(job_for("video-720", tmp_path, {"container": "original"}), lambda d: None)
    assert "merge_output_format" not in original
    assert "FFmpegVideoRemuxer" not in pp_keys(original)


@pytest.mark.parametrize("format_id,codec,quality", [
    ("mp3-320", "mp3", "320"), ("audio-flac", "flac", None), ("audio-opus", "opus", "0"),
    ("audio-m4a-192", "m4a", "192"), ("audio-wav", "wav", None),
])
def test_audio_extraction_options(with_ffmpeg, tmp_path, format_id, codec, quality):
    opts = MediaEngine().build_download_opts(job_for(format_id, tmp_path), lambda d: None)
    assert opts["format"] == "ba/b"
    extract = next(pp for pp in opts["postprocessors"] if pp["key"] == "FFmpegExtractAudio")
    assert extract["preferredcodec"] == codec
    assert extract.get("preferredquality") == quality


def test_source_audio_has_no_conversion(with_ffmpeg, tmp_path):
    opts = MediaEngine().build_download_opts(job_for("source-audio", tmp_path), lambda d: None)
    assert "FFmpegExtractAudio" not in pp_keys(opts)


def test_thumbnail_embedding_only_where_ffmpeg_can_do_it(with_ffmpeg, tmp_path):
    engine = MediaEngine()
    mp3 = engine.build_download_opts(job_for("mp3-192", tmp_path, {"embed_thumbnail": True}), lambda d: None)
    assert "EmbedThumbnail" in pp_keys(mp3) and mp3["writethumbnail"] is True
    # Opus/FLAC would need mutagen (GPL, not bundled) -> not offered
    flac = engine.build_download_opts(job_for("audio-flac", tmp_path, {"embed_thumbnail": True}), lambda d: None)
    assert "EmbedThumbnail" not in pp_keys(flac)
    # unknown final container (could be WebM): not embedded rather than failing the download
    original = engine.build_download_opts(job_for("video-720", tmp_path, {"embed_thumbnail": True, "container": "original"}),
                                          lambda d: None)
    assert "EmbedThumbnail" not in pp_keys(original)
    mkv = engine.build_download_opts(job_for("video-720", tmp_path, {"embed_thumbnail": True, "container": "mkv"}), lambda d: None)
    assert "EmbedThumbnail" in pp_keys(mkv)


def test_subtitles_download_convert_and_embed(with_ffmpeg, tmp_path):
    opts = MediaEngine().build_download_opts(
        job_for("video-1080", tmp_path, {"subtitles": {"langs": ["en", "de"], "auto": True, "format": "srt",
                                                        "embed": True}}),
        lambda d: None)
    assert opts["writesubtitles"] and opts["writeautomaticsub"]
    assert opts["subtitleslangs"] == ["en", "de"]
    keys = pp_keys(opts)
    assert keys[0] == "FFmpegSubtitlesConvertor"
    assert "FFmpegEmbedSubtitle" in keys
    assert keys.index("FFmpegEmbedSubtitle") < keys.index("FFmpegMetadata")


def test_subtitle_only_job(with_ffmpeg, tmp_path):
    opts = MediaEngine().build_download_opts(job_for("autosub-en", tmp_path), lambda d: None)
    assert opts["skip_download"] and opts["writeautomaticsub"] and not opts["writesubtitles"]
    assert opts["subtitleslangs"] == ["en"]


def test_playlist_items_section_and_speed_limit(with_ffmpeg, tmp_path, settings_store):
    settings_store.set("speed_limit_kbps", 500)
    engine = MediaEngine(settings_store)
    job = job_for("video-720", tmp_path, {"playlist_items": "1,3,5-7", "section": {"start": 10, "end": 20}},
                  is_playlist=True)
    opts = engine.build_download_opts(job, lambda d: None)
    assert opts["playlist_items"] == "1,3,5-7"
    assert opts["noplaylist"] is False and opts["ignoreerrors"] == "only_download"
    assert opts["ratelimit"] == 500 * 1024
    assert callable(opts["download_ranges"])
    assert opts["outtmpl"]["default"].startswith("%(playlist_title|Playlist)s/%(playlist_index)03d - ")


def test_single_item_from_playlist_keeps_order_in_name(with_ffmpeg, tmp_path):
    job = job_for("video-720", tmp_path, {"playlist_index": 7, "playlist_title": "P"})
    opts = MediaEngine().build_download_opts(job, lambda d: None)
    assert opts["outtmpl"]["default"].startswith("007 - ")


def test_filename_override_is_used_and_escaped(with_ffmpeg, tmp_path):
    opts = MediaEngine().build_download_opts(job_for("video-720", tmp_path, filename="100% real"), lambda d: None)
    assert opts["outtmpl"]["default"] == "100%% real.%(ext)s"


def test_without_ffmpeg_postprocessing_is_dropped(without_ffmpeg, tmp_path):
    opts = MediaEngine().build_download_opts(job_for("video-720", tmp_path), lambda d: None)
    assert "postprocessors" not in opts
    assert "merge_output_format" not in opts
    assert "ffmpeg_location" not in opts


def test_proxy_and_cookie_file_never_modify_user_file(with_ffmpeg, tmp_path, settings_store):
    cookie = tmp_path / "cookies.txt"
    cookie.write_text("# Netscape HTTP Cookie File\n")
    settings_store.update({"proxy": "socks5://127.0.0.1:9050", "cookies_mode": "file", "cookies_file": str(cookie)})
    holder = []
    opts = MediaEngine(settings_store).build_download_opts(job_for("video-720", tmp_path), lambda d: None,
                                                           cookie_holder=holder)
    assert opts["proxy"] == "socks5://127.0.0.1:9050"
    assert opts["cookiefile"] != str(cookie) and os.path.isfile(opts["cookiefile"])
    MediaEngine._discard_private_cookies(holder)
    assert not os.path.exists(opts["cookiefile"])
    assert cookie.read_text() == "# Netscape HTTP Cookie File\n"


def test_browser_cookies_option(with_ffmpeg, tmp_path, settings_store):
    settings_store.update({"cookies_mode": "browser", "cookies_browser": "firefox"})
    opts = MediaEngine(settings_store).build_download_opts(job_for("video-720", tmp_path), lambda d: None)
    assert opts["cookiesfrombrowser"] == ("firefox",)


@pytest.mark.parametrize("template,expected", [
    ("%(title)s", "%(title)s"),
    ("%(uploader)s/%(title)s", "%(uploader)s/%(title)s"),
    ("../../evil/%(title)s", media_mod.DEFAULT_TEMPLATE),
    ("C:/Windows/%(title)s", media_mod.DEFAULT_TEMPLATE),
    ("/etc/%(title)s", media_mod.DEFAULT_TEMPLATE),
    ("", media_mod.DEFAULT_TEMPLATE),
])
def test_sanitize_template(template, expected):
    assert sanitize_template(template) == expected


def test_matching_extractor_identifies_sites():
    assert matching_extractor("https://www.youtube.com/watch?v=aqz-KE-bpKQ") == "youtube"
    assert matching_extractor("https://example.com/whatever") is None
    assert matching_extractor("magnet:?xt=urn:btih:abc") is None


def test_collect_filepaths_from_single_and_playlist():
    single = {"requested_downloads": [{"filepath": "a.mp4"}]}
    assert _collect_filepaths(single) == ["a.mp4"]
    playlist = {"_type": "playlist", "entries": [single, None, {"requested_downloads": [{"filepath": "b.mp4"}]}]}
    assert _collect_filepaths(playlist) == ["a.mp4", "b.mp4"]


def test_cleanup_cancelled_removes_only_partial_files(tmp_path):
    part_dir = tmp_path / PARTIAL_DIR_NAME
    part_dir.mkdir()
    partial = part_dir / "video.f137.mp4.part"
    partial.write_bytes(b"x")
    finished = tmp_path / "keep.mp4"
    finished.write_bytes(b"x")
    job = job_for("video-720", tmp_path)
    job.temp_files.update({str(partial), str(finished)})
    MediaEngine().cleanup_cancelled(job)
    assert not partial.exists()
    assert finished.exists()  # a completed file is never deleted by cancel cleanup
    assert not part_dir.exists()


def test_http_403_is_retried_once_with_fresh_extraction(with_ffmpeg, tmp_path, monkeypatch):
    from yt_dlp.utils import DownloadError

    calls = []

    class FakeYDL:
        def __init__(self, opts):
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, url, download=True):
            calls.append(url)
            if len(calls) == 1:
                raise DownloadError("ERROR: unable to download video data: HTTP Error 403: Forbidden")
            return {"title": "ok", "requested_downloads": [{"filepath": str(tmp_path / "ok.mp4")}]}

    monkeypatch.setattr(media_mod.yt_dlp, "YoutubeDL", FakeYDL)
    result = MediaEngine().run_download(job_for("video-360", tmp_path), lambda d: None)
    assert len(calls) == 2 and result["filepaths"] == [str(tmp_path / "ok.mp4")]


def test_persistent_403_still_fails(with_ffmpeg, tmp_path, monkeypatch):
    from yt_dlp.utils import DownloadError

    class AlwaysForbidden:
        def __init__(self, opts):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, url, download=True):
            raise DownloadError("ERROR: unable to download video data: HTTP Error 403: Forbidden")

    monkeypatch.setattr(media_mod.yt_dlp, "YoutubeDL", AlwaysForbidden)
    with pytest.raises(DownloadError):
        MediaEngine().run_download(job_for("video-360", tmp_path), lambda d: None)
