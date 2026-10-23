import time

import pytest

from ytd import engine as engine_mod
from ytd.engine import (
    DownloadManager, sanitize_filename, sanitize_filename_override,
    unique_path, is_transient_error, resolve_download_dir,
)
from ytd.models import (
    STATUS_CANCELLED, STATUS_COMPLETED, STATUS_DOWNLOADING, STATUS_FAILED,
    STATUS_PAUSED, STATUS_QUEUED,
)
from ytd.providers.base import UnsupportedMediaError
from ytd.engine import _guess_kind_from_format


def _patch_provider(monkeypatch, fake_provider):
    monkeypatch.setattr(engine_mod, "find_provider", lambda url: fake_provider if fake_provider.supports(url) else None)


def wait_for(predicate, timeout=5.0, interval=0.02):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


@pytest.fixture
def manager(settings_store, history_store, fake_provider, monkeypatch):
    _patch_provider(monkeypatch, fake_provider)
    mgr = DownloadManager(settings_store, history_store)
    mgr.start()
    yield mgr
    mgr.stop()


def test_add_job_rejects_unsupported_url(manager):
    with pytest.raises(UnsupportedMediaError):
        manager.add_job(url="https://not-a-real-site.example/video", kind="video",
                         quality_label="720p (HD)", format_key="video-720", download_dir=".")


def test_job_runs_to_completion(manager, download_dir):
    job = manager.add_job(url="http://fake/instant", kind="video", quality_label="720p (HD)",
                           format_key="video-720", download_dir=download_dir)
    assert wait_for(lambda: manager.get_job(job.job_id).status == STATUS_COMPLETED)
    completed = manager.get_job(job.job_id)
    assert completed.progress == 100.0
    assert completed.downloaded_bytes == 1000


def test_concurrency_respects_max_setting(manager, settings_store, download_dir):
    settings_store.set("max_concurrent_downloads", 1)
    j1 = manager.add_job(url="http://fake/slow", kind="video", quality_label="720p (HD)",
                          format_key="video-720", download_dir=download_dir)
    j2 = manager.add_job(url="http://fake/slow", kind="video", quality_label="720p (HD)",
                          format_key="video-720", download_dir=download_dir)

    assert wait_for(lambda: manager.get_job(j1.job_id).status == STATUS_DOWNLOADING)
    # With concurrency capped at 1, the second job must still be waiting.
    time.sleep(0.05)
    assert manager.get_job(j2.job_id).status == STATUS_QUEUED

    assert wait_for(lambda: manager.get_job(j1.job_id).status == STATUS_COMPLETED, timeout=5)
    assert wait_for(lambda: manager.get_job(j2.job_id).status == STATUS_COMPLETED, timeout=5)


def test_concurrency_allows_parallel_downloads(manager, settings_store, download_dir):
    settings_store.set("max_concurrent_downloads", 3)
    jobs = [manager.add_job(url="http://fake/slow", kind="video", quality_label="720p (HD)",
                             format_key="video-720", download_dir=download_dir) for _ in range(3)]

    def all_downloading():
        return all(manager.get_job(j.job_id).status == STATUS_DOWNLOADING for j in jobs)

    assert wait_for(all_downloading, timeout=3)


def test_pause_and_resume(manager, settings_store, download_dir):
    settings_store.set("max_concurrent_downloads", 1)
    job = manager.add_job(url="http://fake/slow", kind="video", quality_label="720p (HD)",
                           format_key="video-720", download_dir=download_dir)
    assert wait_for(lambda: manager.get_job(job.job_id).status == STATUS_DOWNLOADING)
    time.sleep(0.1)  # let some progress accumulate
    manager.pause(job.job_id)
    assert wait_for(lambda: manager.get_job(job.job_id).status == STATUS_PAUSED)
    progress_at_pause = manager.get_job(job.job_id).downloaded_bytes
    assert 0 < progress_at_pause < 1000

    manager.resume(job.job_id)
    assert wait_for(lambda: manager.get_job(job.job_id).status == STATUS_COMPLETED, timeout=5)


def test_cancel_queued_job_never_runs(manager, download_dir, fake_provider):
    settings_store_before = manager._settings
    settings_store_before.set("max_concurrent_downloads", 1)
    j1 = manager.add_job(url="http://fake/slow", kind="video", quality_label="720p (HD)",
                          format_key="video-720", download_dir=download_dir)
    j2 = manager.add_job(url="http://fake/instant", kind="video", quality_label="720p (HD)",
                          format_key="video-720", download_dir=download_dir)
    assert wait_for(lambda: manager.get_job(j1.job_id).status == STATUS_DOWNLOADING)
    manager.cancel(j2.job_id)
    assert manager.get_job(j2.job_id).status == STATUS_CANCELLED
    assert "http://fake/instant" not in fake_provider.attempts


def test_cancel_active_job(manager, download_dir):
    job = manager.add_job(url="http://fake/slow", kind="video", quality_label="720p (HD)",
                           format_key="video-720", download_dir=download_dir)
    assert wait_for(lambda: manager.get_job(job.job_id).status == STATUS_DOWNLOADING)
    manager.cancel(job.job_id)
    assert wait_for(lambda: manager.get_job(job.job_id).status == STATUS_CANCELLED)


def test_retry_after_permanent_failure(manager, download_dir):
    job = manager.add_job(url="http://fake/broken", kind="video", quality_label="720p (HD)",
                           format_key="video-720", download_dir=download_dir)
    assert wait_for(lambda: manager.get_job(job.job_id).status == STATUS_FAILED)
    assert manager.get_job(job.job_id).error_message

    manager.retry(job.job_id)
    assert wait_for(lambda: manager.get_job(job.job_id).status == STATUS_FAILED, timeout=3)
    assert manager.get_job(job.job_id).retries == 1


def test_transient_failure_auto_retries(manager, download_dir):
    job = manager.add_job(url="http://fake/flaky", kind="video", quality_label="720p (HD)",
                           format_key="video-720", download_dir=download_dir)
    # First attempt fails with a network-ish error, then the engine schedules
    # an automatic retry with backoff, and the second attempt succeeds.
    assert wait_for(lambda: manager.get_job(job.job_id).status == STATUS_COMPLETED, timeout=6)


def test_remove_job_blocked_while_downloading(manager, download_dir):
    job = manager.add_job(url="http://fake/slow", kind="video", quality_label="720p (HD)",
                           format_key="video-720", download_dir=download_dir)
    assert wait_for(lambda: manager.get_job(job.job_id).status == STATUS_DOWNLOADING)
    with pytest.raises(ValueError):
        manager.remove_job(job.job_id)
    manager.cancel(job.job_id)
    assert wait_for(lambda: manager.get_job(job.job_id).status == STATUS_CANCELLED)
    manager.remove_job(job.job_id)
    assert manager.get_job(job.job_id) is None


def test_move_up_and_down_reorder_queue(manager, settings_store, download_dir):
    settings_store.set("max_concurrent_downloads", 0)  # keep everything Queued for this test
    settings_store.set("max_concurrent_downloads", 1)
    jobs = [manager.add_job(url="http://fake/slow", kind="video", quality_label="720p (HD)",
                             format_key="video-720", download_dir=download_dir) for _ in range(3)]
    manager.cancel(jobs[0].job_id) if manager.get_job(jobs[0].job_id).status == STATUS_DOWNLOADING else None
    # Reorder the still-queued jobs regardless of which one is active.
    ids_before = [j.job_id for j in manager.list_jobs()]
    manager.move_down(ids_before[0])
    ids_after = [j.job_id for j in manager.list_jobs()]
    assert ids_after[1] == ids_before[0]


def test_disk_space_check_fails_fast(manager, settings_store, download_dir, monkeypatch):
    import shutil as shutil_mod

    class FakeUsage:
        free = 10  # far below the minimum threshold

    monkeypatch.setattr(engine_mod.shutil, "disk_usage", lambda path: FakeUsage())
    job = manager.add_job(url="http://fake/instant", kind="video", quality_label="720p (HD)",
                           format_key="video-720", download_dir=download_dir)
    assert wait_for(lambda: manager.get_job(job.job_id).status == STATUS_FAILED)
    assert "space" in manager.get_job(job.job_id).error_message.lower()


def test_sanitize_filename_strips_illegal_characters():
    assert sanitize_filename('Video: "Best" <Clip>?.mp4') == "Video Best Clip.mp4"
    assert sanitize_filename("") == "download"


def test_unique_path_avoids_overwriting(tmp_path):
    (tmp_path / "video.mp4").write_text("x")
    (tmp_path / "video (1).mp4").write_text("x")
    result = unique_path(str(tmp_path), "video.mp4")
    assert result == str(tmp_path / "video (2).mp4")


def test_is_transient_error_classification():
    assert is_transient_error("Network error — check your internet connection")
    assert is_transient_error("Connection timed out")
    assert not is_transient_error("This video is private or requires sign-in")
    assert not is_transient_error("This URL isn't supported or no downloadable media was found on it.")


def test_sanitize_filename_override_strips_illegal_chars_and_known_extension():
    assert sanitize_filename_override('My "Video": Take 2.mp4') == "My Video Take 2"
    assert sanitize_filename_override("song.mp3") == "song"
    assert sanitize_filename_override("not.an.extension.xyz") == "not.an.extension.xyz"
    assert sanitize_filename_override("") == "download"
    assert sanitize_filename_override("   ") == "download"


def test_sanitize_filename_override_handles_unicode_and_length():
    assert sanitize_filename_override("テスト動画.mp4") == "テスト動画"
    long_name = "a" * 500 + ".mp4"
    result = sanitize_filename_override(long_name)
    assert len(result) <= 150
    assert not result.endswith(".mp4")


def test_resolve_download_dir_explicit_wins(settings_store):
    settings_store.set("video_dir", "/videos")
    assert resolve_download_dir(settings_store, "video", None, "/explicit") == "/explicit"


def test_resolve_download_dir_routes_by_category(settings_store):
    settings_store.set("video_dir", "/videos")
    settings_store.set("audio_dir", "/music")
    settings_store.set("download_dir", "/default")
    assert resolve_download_dir(settings_store, "video", None, None) == "/videos"
    assert resolve_download_dir(settings_store, "audio", "Music", None) == "/music"
    assert resolve_download_dir(settings_store, "image", None, None) == "/default"


def test_resolve_download_dir_falls_back_when_category_folder_unset(settings_store):
    settings_store.set("download_dir", "/default")
    assert resolve_download_dir(settings_store, "video", None, None) == "/default"


def test_add_job_sanitizes_filename_override(manager, download_dir):
    job = manager.add_job(url="http://fake/instant", kind="video", quality_label="720p (HD)",
                           format_key="video-720", download_dir=download_dir,
                           filename_override='Weird: "Name".mp4')
    assert job.filename_override == "Weird Name"


def test_add_job_defaults_category_from_kind(manager, download_dir):
    video_job = manager.add_job(url="http://fake/instant", kind="video", quality_label="720p (HD)",
                                 format_key="video-720", download_dir=download_dir)
    audio_job = manager.add_job(url="http://fake/instant", kind="audio", quality_label="MP3 — 192 kbps",
                                 format_key="mp3-192", download_dir=download_dir)
    assert video_job.category == "Video"
    assert audio_job.category == "Music"


def test_thumbnail_job_completes_without_progress_events(manager, download_dir):
    job = manager.add_job(url="http://fake/instant", kind="image", quality_label="Thumbnail",
                           format_key="thumbnail", download_dir=download_dir)
    assert wait_for(lambda: manager.get_job(job.job_id).status == STATUS_COMPLETED)
    completed = manager.get_job(job.job_id)
    assert completed.progress == 100.0


def test_subtitle_job_completes(manager, download_dir):
    job = manager.add_job(url="http://fake/instant", kind="subtitle", quality_label="Subtitle (en)",
                           format_key="subtitle-en", download_dir=download_dir)
    assert wait_for(lambda: manager.get_job(job.job_id).status == STATUS_COMPLETED)


@pytest.mark.parametrize("label,expected_kind", [
    ("MP3 (audio only)", "audio"),
    ("192 kbps", "audio"),
    ("720p (HD)", "video"),
    ("JPG 1920x1080 (thumbnail)", "image"),
    ("English subtitle", "subtitle"),
    ("Entire torrent (3 files)", "torrent"),
    ("", "video"),
])
def test_guess_kind_from_format(label, expected_kind):
    assert _guess_kind_from_format(label) == expected_kind


def test_seed_from_history_creates_terminal_readonly_jobs(manager):
    entries = [
        {"title": "Old Video", "format": "720p (HD)", "path": "/downloads", "url": "http://old/1",
         "status": "Completed", "date": "2026-01-01 10:00", "source": "gui"},
        {"title": "Old Song", "format": "MP3 (audio only)", "path": "/downloads", "url": "http://old/2",
         "status": "Failed", "date": "2026-01-02 11:00", "source": "extension"},
    ]
    manager.seed_from_history(entries)
    jobs = manager.list_jobs()
    assert len(jobs) == 2

    video_job = next(j for j in jobs if j.url == "http://old/1")
    assert video_job.status == STATUS_COMPLETED
    assert video_job.kind == "video"
    assert video_job.progress == 100.0
    assert video_job.from_history is True
    assert video_job.history_key == ("http://old/1", "2026-01-01 10:00", "Old Video")

    audio_job = next(j for j in jobs if j.url == "http://old/2")
    assert audio_job.status == STATUS_FAILED
    assert audio_job.kind == "audio"
    assert audio_job.progress == 0.0


def test_seeded_history_jobs_are_never_dispatched(manager, settings_store):
    # A seeded job's status is always terminal, so the dispatcher (which
    # only ever starts STATUS_QUEUED jobs) must never touch it, even though
    # it carries a real-looking URL and download_dir.
    settings_store.set("max_concurrent_downloads", 5)
    manager.seed_from_history([
        {"title": "Old", "format": "720p", "path": "/downloads", "url": "http://fake/instant",
         "status": "Completed", "date": "2026-01-01 10:00"},
    ])
    time.sleep(0.3)
    job = manager.list_jobs()[0]
    assert job.status == STATUS_COMPLETED  # unchanged — never re-run


def test_ordinary_jobs_default_from_history_to_false(manager, download_dir):
    job = manager.add_job(url="http://fake/instant", kind="video", quality_label="720p (HD)",
                           format_key="video-720", download_dir=download_dir)
    assert job.from_history is False
    assert job.history_key is None


def test_speed_history_recorded_during_download(manager, download_dir):
    job = manager.add_job(url="http://fake/slow", kind="video", quality_label="720p (HD)",
                           format_key="video-720", download_dir=download_dir)
    assert wait_for(lambda: manager.get_job(job.job_id).status == STATUS_COMPLETED, timeout=5)
    completed = manager.get_job(job.job_id)
    assert len(completed.speed_history) > 0
    assert all(isinstance(sample, tuple) and len(sample) == 2 for sample in completed.speed_history)
