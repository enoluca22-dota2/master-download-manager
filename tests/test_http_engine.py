"""Direct HTTP engine against a real local HTTP server (no internet)."""

import hashlib
import os
import threading
import time

import pytest
from yt_dlp.utils import DownloadCancelled

from ytd.engine import DownloadManager
from ytd.models import STATUS_CANCELLED, STATUS_COMPLETED, STATUS_DOWNLOADING, STATUS_FAILED, STATUS_PAUSED, DownloadJob
from ytd.providers import http as http_mod
from ytd.providers.base import PauseRequested, UnsupportedMediaError
from ytd.providers.http import HttpEngine, TokenBucket, filename_from_headers, probe_url

from local_http_server import LocalHTTPServer


@pytest.fixture
def server():
    with LocalHTTPServer() as srv:
        yield srv


def _payload(size, seed=1):
    block = hashlib.sha256(str(seed).encode()).digest()
    return (block * (size // len(block) + 1))[:size]


def _job(url, download_dir, **kw):
    return DownloadJob(url=url, kind="file", quality_label="Direct file", format_key="file", is_playlist=False,
                       download_dir=download_dir, engine="http", **kw)


def _hook_collector():
    events = []
    return events, events.append


def test_filename_from_headers_prefers_rfc6266_filename_star():
    headers = {"Content-Disposition": "attachment; filename=\"plain.zip\"; filename*=UTF-8''r%C3%A9sum%C3%A9.zip"}
    assert filename_from_headers(headers, "https://x/whatever") == "résumé.zip"


def test_filename_from_headers_strips_paths_and_illegal_characters():
    headers = {"Content-Disposition": 'attachment; filename="..\\..\\Windows\\evil:name?.exe"'}
    assert filename_from_headers(headers, "https://x/") == "evilname.exe"
    assert filename_from_headers({}, "https://host/dir/My%20File.iso?x=1") == "My File.iso"
    assert filename_from_headers({}, "https://host/") == "download"
    assert filename_from_headers({"Content-Disposition": 'attachment; filename="CON.txt"'}, "https://x/") == "_CON.txt"


def test_probe_reports_size_range_support_and_name(server):
    server.files["a.bin"] = _payload(5000)
    info = probe_url(f"{server.base}/cd/a.bin")
    assert info["size"] == 5000
    assert info["accept_ranges"] is True
    assert info["filename"] == "résumé file.zip"
    assert info["attachment"] is True
    no_range = probe_url(f"{server.base}/norange/a.bin")
    assert no_range["accept_ranges"] is False
    assert no_range["size"] == 5000


def test_detect_returns_real_file_facts(server):
    server.files["iso.iso"] = _payload(1234)
    media = HttpEngine().detect(f"{server.base}/files/iso.iso")
    assert media.engine == "http"
    assert media.title == "iso.iso"
    assert media.formats[0].filesize_bytes == 1234
    assert media.formats[0].ext == "iso"
    assert media.resumable is True


def test_detect_404_is_a_clear_error(server):
    with pytest.raises(UnsupportedMediaError, match="404"):
        HttpEngine().detect(f"{server.base}/missing")


def test_single_connection_download_and_redirect(server, tmp_path):
    data = _payload(300_000)
    server.files["f.zip"] = data
    job = _job(f"{server.base}/redirect/f.zip", str(tmp_path))
    events, hook = _hook_collector()
    result = HttpEngine().run_download(job, hook)
    path = result["filepaths"][0]
    assert os.path.basename(path) == "f.zip"
    assert open(path, "rb").read() == data
    assert not os.path.exists(path + http_mod.PART_SUFFIX)
    assert events[-1]["status"] == "finished"
    assert any(e["status"] == "downloading" for e in events)


def test_existing_file_is_never_overwritten(server, tmp_path):
    server.files["dup.txt"] = b"new content"
    (tmp_path / "dup.txt").write_bytes(b"old content")
    job = _job(f"{server.base}/files/dup.txt", str(tmp_path))
    result = HttpEngine().run_download(job, lambda d: None)
    assert os.path.basename(result["filepaths"][0]) == "dup (1).txt"
    assert (tmp_path / "dup.txt").read_bytes() == b"old content"


def test_segmented_download_is_byte_exact(server, tmp_path, monkeypatch, settings_store):
    monkeypatch.setattr(http_mod, "SEGMENT_MIN_FILE", 100_000)
    monkeypatch.setattr(http_mod, "SEGMENT_MIN_SIZE", 64 * 1024)
    settings_store.set("http_connections", 4)
    data = _payload(1_500_000, seed=7)
    server.files["big.bin"] = data
    job = _job(f"{server.base}/files/big.bin", str(tmp_path))
    result = HttpEngine(settings_store).run_download(job, lambda d: None)
    assert hashlib.sha256(open(result["filepaths"][0], "rb").read()).digest() == hashlib.sha256(data).digest()
    ranged = [r for r in server.range_log if r[0] == "/files/big.bin" and r[1] and r[1] != "bytes=0-0"]
    assert len(ranged) >= 4  # really fetched in several parts


def _run_until(engine, job, stop_after_bytes, action):
    """Run a download and trigger pause/cancel once enough bytes arrived."""
    def hook(d):
        if d.get("status") == "downloading" and (d.get("downloaded_bytes") or 0) >= stop_after_bytes:
            action.set()
        if job.cancel_event.is_set():
            raise DownloadCancelled("cancel")
        if job.pause_event.is_set():
            raise PauseRequested("pause")
    return engine.run_download(job, hook)


@pytest.mark.parametrize("segmented", [False, True])
def test_pause_then_resume_continues_from_partial_file(server, tmp_path, monkeypatch, settings_store, segmented):
    monkeypatch.setattr(http_mod, "SEGMENT_MIN_FILE", 100_000 if segmented else 10 ** 12)
    monkeypatch.setattr(http_mod, "SEGMENT_MIN_SIZE", 64 * 1024)
    settings_store.set("http_connections", 3)
    server.set_slow_delay(0.03 if segmented else 0.01)
    data = _payload(2_000_000 if segmented else 800_000, seed=3)
    server.files["p.bin"] = data
    engine = HttpEngine(settings_store)
    job = _job(f"{server.base}/slow/p.bin", str(tmp_path))
    with pytest.raises(PauseRequested):
        _run_until(engine, job, 200_000, job.pause_event)
    target = job.options["http_target"]
    assert os.path.exists(target + http_mod.PART_SUFFIX)
    assert not os.path.exists(target)
    server.range_log.clear()
    job.pause_event.clear()
    result = engine.run_download(job, lambda d: None)
    assert result["filepaths"][0] == target
    assert open(target, "rb").read() == data
    # the second run asked only for the missing bytes, never the whole file again
    resumed = [r for r in server.range_log if r[0] == "/slow/p.bin" and r[1] and r[1] != "bytes=0-0"]
    assert resumed
    assert all(r[1] != "bytes=0-" for r in resumed)
    assert all(r[2] for r in resumed)  # If-Range validator sent


def test_cancel_removes_partial_files(server, tmp_path):
    server.set_slow_delay(0.01)
    server.files["c.bin"] = _payload(600_000)
    engine = HttpEngine()
    job = _job(f"{server.base}/slow/c.bin", str(tmp_path))
    with pytest.raises(DownloadCancelled):
        _run_until(engine, job, 50_000, job.cancel_event)
    engine.cleanup_cancelled(job)
    assert os.listdir(tmp_path) == []


def test_changed_file_on_server_restarts_instead_of_corrupting(server, tmp_path):
    server.set_slow_delay(0.01)
    server.files["v.bin"] = _payload(500_000, seed=1)
    engine = HttpEngine()
    job = _job(f"{server.base}/slow/v.bin", str(tmp_path))
    with pytest.raises(PauseRequested):
        _run_until(engine, job, 100_000, job.pause_event)
    job.pause_event.clear()
    new_data = _payload(500_000, seed=2)  # same size, different content -> different ETag
    server.files["v.bin"] = new_data
    result = engine.run_download(job, lambda d: None)
    assert open(result["filepaths"][0], "rb").read() == new_data


def test_server_without_range_support_downloads_and_reports_not_resumable(server, tmp_path):
    data = _payload(200_000)
    server.files["n.bin"] = data
    job = _job(f"{server.base}/norange/n.bin", str(tmp_path))
    result = HttpEngine().run_download(job, lambda d: None)
    assert job.resumable is False
    assert open(result["filepaths"][0], "rb").read() == data


def test_disk_space_check(server, tmp_path, monkeypatch):
    server.files["d.bin"] = _payload(10_000)

    class Tiny:
        free = 5

    monkeypatch.setattr(http_mod.shutil, "disk_usage", lambda p: Tiny())
    with pytest.raises(OSError) as err:
        HttpEngine().run_download(_job(f"{server.base}/files/d.bin", str(tmp_path)), lambda d: None)
    assert err.value.errno == 28


def test_token_bucket_limits_rate():
    bucket = TokenBucket(100_000)  # 100 KB/s
    start = time.monotonic()
    for _ in range(30):
        bucket.consume(10_000)  # 300 KB total
    elapsed = time.monotonic() - start
    assert 1.6 <= elapsed <= 3.5  # first 100 KB is the burst allowance


def test_speed_limit_applies_to_real_download(server, tmp_path, settings_store):
    settings_store.set("speed_limit_kbps", 200)
    server.files["s.bin"] = _payload(600_000)
    start = time.monotonic()
    HttpEngine(settings_store).run_download(_job(f"{server.base}/files/s.bin", str(tmp_path)), lambda d: None)
    assert time.monotonic() - start >= 1.5  # ~585 KB at 200 KB/s with a 200 KB burst


def test_full_queue_roundtrip_with_http_engine(server, tmp_path, settings_store, history_store):
    data = _payload(250_000)
    server.files["q.zip"] = data
    manager = DownloadManager(settings_store, history_store)
    manager.start()
    try:
        job = manager.add_job(url=f"{server.base}/files/q.zip", kind="file", quality_label="Direct file",
                              format_key="file", download_dir=str(tmp_path), engine="http")
        deadline = time.time() + 10
        while time.time() < deadline and manager.get_job(job.job_id).status != STATUS_COMPLETED:
            time.sleep(0.05)
        done = manager.get_job(job.job_id)
        assert done.status == STATUS_COMPLETED
        assert done.engine == "http"
        assert open(done.result_path, "rb").read() == data
        entry = history_store.all()[0]
        assert entry["engine"] == "http"
        assert entry["file"] == done.result_path
        assert entry["category"] == "Archives"
        assert entry["file_size"] == len(data)
    finally:
        manager.stop()


def test_queue_pause_resume_and_cancel_with_http_engine(server, tmp_path, settings_store, history_store):
    server.set_slow_delay(0.01)
    data = _payload(700_000)
    server.files["pr.bin"] = data
    manager = DownloadManager(settings_store, history_store)
    manager.start()
    try:
        job = manager.add_job(url=f"{server.base}/slow/pr.bin", kind="file", quality_label="Direct file",
                              format_key="file", download_dir=str(tmp_path), engine="http")
        _wait(lambda: manager.get_job(job.job_id).status == STATUS_DOWNLOADING
              and manager.get_job(job.job_id).downloaded_bytes > 50_000)
        manager.pause(job.job_id)
        _wait(lambda: manager.get_job(job.job_id).status == STATUS_PAUSED)
        manager.resume(job.job_id)
        _wait(lambda: manager.get_job(job.job_id).status == STATUS_COMPLETED, timeout=20)
        assert open(manager.get_job(job.job_id).result_path, "rb").read() == data

        job2 = manager.add_job(url=f"{server.base}/slow/pr.bin", kind="file", quality_label="Direct file",
                               format_key="file", download_dir=str(tmp_path / "second"), engine="http")
        _wait(lambda: manager.get_job(job2.job_id).downloaded_bytes > 50_000)
        manager.cancel(job2.job_id)
        _wait(lambda: manager.get_job(job2.job_id).status == STATUS_CANCELLED)
        assert os.listdir(tmp_path / "second") == []
    finally:
        manager.stop()


def test_queue_failure_has_reason_hint_and_details(server, tmp_path, settings_store, history_store):
    settings_store.set("max_retries", 0)
    manager = DownloadManager(settings_store, history_store)
    manager.start()
    try:
        job = manager.add_job(url=f"{server.base}/missing", kind="file", quality_label="Direct file",
                              format_key="file", download_dir=str(tmp_path), engine="http")
        _wait(lambda: manager.get_job(job.job_id).status == STATUS_FAILED)
        failed = manager.get_job(job.job_id)
        assert "404" in failed.error_message
        assert failed.error_hint
        assert "404" in failed.error_details
    finally:
        manager.stop()


def _wait(predicate, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    assert predicate()
