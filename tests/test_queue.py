"""Unified queue behaviour added for Master Download Manager."""

import json
import time

import pytest
from fastapi.testclient import TestClient

from ytd import engine as engine_mod
from ytd.api import server as server_mod
from ytd.engine import DownloadManager
from ytd.models import (
    STATUS_CANCELLED, STATUS_COMPLETED, STATUS_DOWNLOADING, STATUS_FAILED, STATUS_PAUSED, STATUS_PREPARING,
    STATUS_PROCESSING, STATUS_QUEUED, STATUS_RETRYING,
)


def wait_for(predicate, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


@pytest.fixture
def make_manager(settings_store, history_store, fake_provider, monkeypatch, tmp_path):
    monkeypatch.setattr(engine_mod, "find_provider", lambda url: fake_provider if fake_provider.supports(url) else None)
    created = []

    def factory(queue_file=None):
        mgr = DownloadManager(settings_store, history_store, queue_file=queue_file)
        mgr.start()
        created.append(mgr)
        return mgr

    yield factory
    for mgr in created:
        mgr.stop()


def add(mgr, url, d):
    return mgr.add_job(url=url, kind="video", quality_label="720p (HD)", format_key="video-720", download_dir=d)


def test_status_lifecycle_visits_preparing_downloading_processing(make_manager, download_dir):
    mgr = make_manager()
    seen = []
    mgr.subscribe(lambda event, job: seen.append(job.status))
    job = add(mgr, "http://fake/slow", download_dir)
    assert wait_for(lambda: STATUS_COMPLETED in seen)  # the event, not just the status flip
    for status in (STATUS_PREPARING, STATUS_DOWNLOADING, STATUS_PROCESSING, STATUS_COMPLETED):
        assert status in seen
    assert seen.index(STATUS_PREPARING) < seen.index(STATUS_DOWNLOADING) < seen.index(STATUS_PROCESSING)


def test_auto_retry_shows_retrying_and_history_records_only_final_outcome(make_manager, download_dir, history_store):
    mgr = make_manager()
    seen = []
    mgr.subscribe(lambda event, job: seen.append(job.status))
    job = add(mgr, "http://fake/flaky", download_dir)
    assert wait_for(lambda: mgr.get_job(job.job_id).status == STATUS_COMPLETED, timeout=8)
    assert STATUS_RETRYING in seen
    assert STATUS_FAILED not in seen  # never shown as failed while a retry is pending
    statuses = [e["status"] for e in history_store.all()]
    assert statuses == ["Completed"]


def test_pausing_a_retrying_job_stops_the_retry(make_manager, download_dir, fake_provider):
    mgr = make_manager()
    job = add(mgr, "http://fake/flaky", download_dir)
    assert wait_for(lambda: mgr.get_job(job.job_id).status == STATUS_RETRYING)
    mgr.pause(job.job_id)
    time.sleep(2.5)  # longer than the first backoff
    assert mgr.get_job(job.job_id).status == STATUS_PAUSED
    assert fake_provider.attempts["http://fake/flaky"] == 1


def test_retry_failed_and_clear_completed(make_manager, download_dir):
    mgr = make_manager()
    broken = [add(mgr, "http://fake/broken", download_dir) for _ in range(2)]
    ok = add(mgr, "http://fake/instant", download_dir)
    assert wait_for(lambda: all(mgr.get_job(j.job_id).status == STATUS_FAILED for j in broken))
    assert wait_for(lambda: mgr.get_job(ok.job_id).status == STATUS_COMPLETED)
    failed = mgr.get_job(broken[0].job_id)
    assert failed.error_message and failed.error_hint and "Video unavailable" in failed.error_details
    assert mgr.retry_failed() == 2
    assert wait_for(lambda: all(mgr.get_job(j.job_id).retries == 1 for j in broken))
    assert mgr.clear_completed() == 1
    assert mgr.get_job(ok.job_id) is None


def test_move_to_top_and_bottom(make_manager, settings_store, download_dir):
    settings_store.set("max_concurrent_downloads", 1)
    mgr = make_manager()
    jobs = [add(mgr, "http://fake/slow", download_dir) for _ in range(4)]
    last = jobs[-1].job_id
    mgr.move_to_top(last)
    assert mgr.list_jobs()[0].job_id == last
    mgr.move_to_bottom(last)
    assert mgr.list_jobs()[-1].job_id == last


def test_cannot_pause_while_processing(make_manager, download_dir):
    mgr = make_manager()
    job = add(mgr, "http://fake/instant", download_dir)
    job.status = STATUS_PROCESSING
    with pytest.raises(ValueError, match="processed"):
        mgr.pause(job.job_id)


def test_queue_is_persisted_and_restored_as_paused(make_manager, settings_store, download_dir, tmp_path):
    queue_file = str(tmp_path / "queue.json")
    settings_store.set("max_concurrent_downloads", 1)
    mgr = make_manager(queue_file)
    running = add(mgr, "http://fake/slow", download_dir)
    waiting = add(mgr, "http://fake/slow", download_dir)
    assert wait_for(lambda: mgr.get_job(running.job_id).status == STATUS_DOWNLOADING)
    mgr.stop()
    data = json.load(open(queue_file, encoding="utf-8"))
    persisted = {j["job_id"]: j for j in data["jobs"]}
    assert set(persisted) == {running.job_id, waiting.job_id}
    assert all(j["status"] == STATUS_PAUSED for j in persisted.values())

    mgr2 = make_manager(queue_file)
    assert mgr2.restore_queue() == 2
    restored = mgr2.get_job(running.job_id)
    assert restored.status == STATUS_PAUSED and restored.url == "http://fake/slow"
    new_job = add(mgr2, "http://fake/instant", download_dir)
    assert new_job.job_id > max(persisted)  # ids never collide with restored ones
    mgr2.resume(restored.job_id)
    assert wait_for(lambda: mgr2.get_job(restored.job_id).status == STATUS_COMPLETED)


def test_history_rows_cannot_be_retried_but_queued_ones_can(make_manager):
    mgr = make_manager()
    mgr.seed_from_history([{"id": "h1", "title": "Old", "format": "720p", "path": "/d", "url": "http://fake/x",
                            "status": "Failed", "date": "2026-01-01 10:00", "engine": "media", "file": ""}])
    row = mgr.list_jobs()[0]
    assert row.history_id == "h1" and row.engine == "media"
    with pytest.raises(ValueError, match="Redownload"):
        mgr.retry(row.job_id)


def test_cancel_queued_job_calls_engine_cleanup(make_manager, settings_store, download_dir, fake_provider):
    settings_store.set("max_concurrent_downloads", 1)
    calls = []
    fake_provider.cleanup_cancelled = lambda job: calls.append(job.job_id)
    mgr = make_manager()
    add(mgr, "http://fake/slow", download_dir)
    queued = add(mgr, "http://fake/slow", download_dir)
    assert mgr.get_job(queued.job_id).status == STATUS_QUEUED
    mgr.cancel(queued.job_id)
    assert mgr.get_job(queued.job_id).status == STATUS_CANCELLED
    assert calls == [queued.job_id]


# ------------------------------------------------------------ local API --- #

@pytest.fixture
def client(settings_store, history_store, fake_provider, monkeypatch):
    fake = lambda url, *a, **k: fake_provider if fake_provider.supports(url) else None  # noqa: E731
    monkeypatch.setattr(server_mod, "find_provider", fake)
    monkeypatch.setattr(engine_mod, "find_provider", fake)
    import ytd.providers as providers_mod
    monkeypatch.setattr(providers_mod, "find_provider", fake)
    mgr = DownloadManager(settings_store, history_store)
    mgr.start()
    c = TestClient(server_mod.create_app(mgr, settings_store), client=("127.0.0.1", 50000))
    c.token = settings_store.get("api_token")
    yield c
    mgr.stop()


def test_new_and_legacy_token_headers_both_work(client):
    assert client.get("/connection", headers={"X-MDM-Token": client.token}).status_code == 200
    assert client.get("/connection", headers={"X-Enagrio-Token": client.token}).status_code == 200
    assert client.get("/connection", headers={"X-MDM-Token": client.token + "x"}).status_code == 401


def test_non_local_clients_are_refused(settings_store, history_store):
    mgr = DownloadManager(settings_store, history_store)
    c = TestClient(server_mod.create_app(mgr, settings_store), client=("192.168.1.50", 50000))
    assert c.get("/connection", headers={"X-MDM-Token": settings_store.get("api_token")}).status_code == 403


def test_settings_endpoint_never_exposes_secrets(client, settings_store):
    settings_store.update({"proxy": "http://user:pw@proxy:8080", "cookies_file": r"C:\secret\cookies.txt"})
    body = client.get("/settings", headers={"X-MDM-Token": client.token}).json()
    assert "api_token" not in body and "proxy" not in body and "cookies_file" not in body


@pytest.mark.parametrize("save_dir", ["relative\\folder", "\\\\evil-host\\share", "C:\\x\\..\\..\\Windows"])
def test_unsafe_save_dirs_are_rejected(client, save_dir):
    resp = client.post("/downloads", json={"url": "http://fake/instant", "format_id": "video-720", "save_dir": save_dir},
                       headers={"X-MDM-Token": client.token})
    assert resp.status_code == 422


def test_engine_detect_endpoint(client):
    resp = client.post("/engine/detect", json={"url": "magnet:?xt=urn:btih:abc"}, headers={"X-MDM-Token": client.token})
    assert resp.status_code == 200 and resp.json()["engine"] == "torrent"
    bad = client.post("/engine/detect", json={"url": "not a link"}, headers={"X-MDM-Token": client.token})
    assert bad.status_code == 422


def test_control_characters_in_url_are_rejected(client):
    resp = client.post("/downloads", json={"url": "http://fake/instant\r\nX: y", "format_id": "video-720"},
                       headers={"X-MDM-Token": client.token})
    assert resp.status_code == 422
