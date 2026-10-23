"""Named queues, priorities, scheduled starts, schedules and speed profiles."""

import datetime
import json
import time

import pytest

from ytd import bandwidth
from ytd import engine as engine_mod
from ytd.engine import AGING_SECONDS, DownloadManager
from ytd.models import (
    PRIORITY_HIGH, PRIORITY_LOW, STATUS_COMPLETED, STATUS_DOWNLOADING, STATUS_PAUSED, STATUS_QUEUED, DownloadJob,
)
from ytd.queues import MAIN_QUEUE_ID, QueueStore, Schedule
from ytd.scheduler import RecurringDownload, Scheduler, SchedulerStore, SpeedProfile

from test_engine import _patch_provider, wait_for

FAKE = dict(kind="video", quality_label="720p (HD)", format_key="video-720")


@pytest.fixture
def queues(tmp_path):
    return QueueStore(str(tmp_path / "queues.json"))


@pytest.fixture
def manager(settings_store, history_store, fake_provider, monkeypatch, queues):
    _patch_provider(monkeypatch, fake_provider)
    mgr = DownloadManager(settings_store, history_store, queue_store=queues)
    mgr.start()
    yield mgr
    mgr.stop()


# ------------------------------------------------------------ QueueStore --- #
def test_main_queue_always_exists_and_runs(tmp_path):
    path = tmp_path / "q.json"
    path.write_text(json.dumps({"queues": [{"id": "main", "name": "Main", "running": False}]}))
    store = QueueStore(str(path))
    main = store.get(MAIN_QUEUE_ID)
    assert main.running is True  # MDM's always-on behaviour survives restarts
    with pytest.raises(ValueError):
        store.delete(MAIN_QUEUE_ID)


def test_queue_crud_persists_and_validates(tmp_path):
    path = str(tmp_path / "q.json")
    store = QueueStore(path)
    night = store.create("Night", max_concurrent=2, on_complete="shutdown",
                         schedule={"days": [0, 1], "start_enabled": True, "start_time": "01:00"})
    with pytest.raises(ValueError):
        store.create("night")  # duplicate names (case-insensitive)
    with pytest.raises(ValueError):
        store.create("   ")
    store.update(night.id, name="Overnight", max_connections=99)
    reloaded = QueueStore(path)
    q = reloaded.get(night.id)
    assert q.name == "Overnight" and q.max_concurrent == 2 and q.max_connections == 16
    assert q.on_complete == "shutdown" and q.schedule.days == [0, 1] and q.schedule.start_enabled
    with pytest.raises(ValueError):
        store.update(night.id, schedule={"days": [], "start_time": "01:00"})
    with pytest.raises(ValueError):
        store.update(night.id, schedule={"days": [1], "start_time": "25:99"})
    assert reloaded.resolve("deleted-queue").id == MAIN_QUEUE_ID


def test_corrupt_queue_file_falls_back_to_main(tmp_path):
    path = tmp_path / "q.json"
    path.write_text("{not json")
    assert [q.id for q in QueueStore(str(path)).all()] == [MAIN_QUEUE_ID]


# ------------------------------------------------------- dispatch rules --- #
def test_custom_queue_waits_until_started(manager, download_dir):
    q = manager.queues.create("Later")
    job = manager.add_job(url="http://fake/instant", download_dir=download_dir, queue_id=q.id, **FAKE)
    time.sleep(0.4)
    assert manager.get_job(job.job_id).status == STATUS_QUEUED
    manager.start_queue(q.id)
    assert wait_for(lambda: manager.get_job(job.job_id).status == STATUS_COMPLETED)


def test_per_queue_concurrency(manager, settings_store, download_dir):
    settings_store.set("max_concurrent_downloads", 5)
    q = manager.queues.create("One at a time", max_concurrent=1)
    manager.start_queue(q.id)
    jobs = [manager.add_job(url="http://fake/slow", download_dir=download_dir, queue_id=q.id, **FAKE)
            for _ in range(3)]
    assert wait_for(lambda: manager.get_job(jobs[0].job_id).status == STATUS_DOWNLOADING)
    time.sleep(0.15)
    running = [j for j in jobs if manager.get_job(j.job_id).status == STATUS_DOWNLOADING]
    assert len(running) == 1
    assert wait_for(lambda: all(manager.get_job(j.job_id).status == STATUS_COMPLETED for j in jobs), timeout=10)


def test_stop_queue_pauses_and_start_resumes(manager, download_dir):
    q = manager.queues.create("Batch")
    manager.start_queue(q.id)
    job = manager.add_job(url="http://fake/slow", download_dir=download_dir, queue_id=q.id, **FAKE)
    assert wait_for(lambda: manager.get_job(job.job_id).status == STATUS_DOWNLOADING)
    manager.stop_queue(q.id)
    assert wait_for(lambda: manager.get_job(job.job_id).status == STATUS_PAUSED)
    assert manager.queues.get(q.id).running is False
    manager.start_queue(q.id)
    assert wait_for(lambda: manager.get_job(job.job_id).status == STATUS_COMPLETED, timeout=6)


def test_high_priority_jumps_ahead(manager, settings_store, download_dir):
    settings_store.set("max_concurrent_downloads", 1)
    blocker = manager.add_job(url="http://fake/slow", download_dir=download_dir, **FAKE)
    assert wait_for(lambda: manager.get_job(blocker.job_id).status == STATUS_DOWNLOADING)
    normal = manager.add_job(url="http://fake/slow", download_dir=download_dir, **FAKE)
    urgent = manager.add_job(url="http://fake/slow", download_dir=download_dir, priority=PRIORITY_HIGH, **FAKE)
    assert wait_for(lambda: manager.get_job(urgent.job_id).status == STATUS_DOWNLOADING, timeout=6)
    assert manager.get_job(normal.job_id).status == STATUS_QUEUED


def test_aging_prevents_starvation(settings_store, history_store):
    mgr = DownloadManager(settings_store, history_store)
    now = time.time()
    low = DownloadJob("http://fake/a", "video", "", "", False, ".", priority=PRIORITY_LOW)
    low.queued_at = now - 3 * AGING_SECONDS  # waited a long time
    high = DownloadJob("http://fake/b", "video", "", "", False, ".", priority=PRIORITY_HIGH)
    high.queued_at = now
    mgr._jobs = [high, low]
    with mgr._lock:
        assert mgr._next_candidate() is low
    low.queued_at = now
    with mgr._lock:
        assert mgr._next_candidate() is high


def test_scheduled_single_download_waits_for_its_time(manager, download_dir):
    job = manager.add_job(url="http://fake/instant", download_dir=download_dir, start_at=time.time() + 0.8, **FAKE)
    time.sleep(0.3)
    assert manager.get_job(job.job_id).status == STATUS_QUEUED
    assert wait_for(lambda: manager.get_job(job.job_id).status == STATUS_COMPLETED, timeout=4)


def test_move_to_queue_and_delete_queue_returns_items_to_main(manager, download_dir):
    q = manager.queues.create("Hold")
    job = manager.add_job(url="http://fake/instant", download_dir=download_dir, queue_id=q.id, **FAKE)
    manager.delete_queue(q.id)
    assert manager.get_job(job.job_id).queue_id == MAIN_QUEUE_ID
    assert wait_for(lambda: manager.get_job(job.job_id).status == STATUS_COMPLETED)


def test_queue_complete_event_and_stop_when_empty(manager, download_dir):
    events = []
    manager.subscribe(lambda e, x: events.append((e, getattr(x, "id", None))) if e == "queue_complete" else None)
    q = manager.queues.create("Once", stop_when_empty=True, on_complete="notify")
    manager.start_queue(q.id)
    for _ in range(2):
        manager.add_job(url="http://fake/instant", download_dir=download_dir, queue_id=q.id, **FAKE)
    assert wait_for(lambda: ("queue_complete", q.id) in events, timeout=6)
    assert manager.queues.get(q.id).running is False
    assert events.count(("queue_complete", q.id)) == 1


def test_stopping_a_queue_never_fires_its_completion_action(manager, download_dir):
    events = []
    manager.subscribe(lambda e, x: events.append(e) if e == "queue_complete" else None)
    q = manager.queues.create("Power", on_complete="shutdown")
    manager.start_queue(q.id)
    job = manager.add_job(url="http://fake/slow", download_dir=download_dir, queue_id=q.id, **FAKE)
    assert wait_for(lambda: manager.get_job(job.job_id).status == STATUS_DOWNLOADING)
    manager.stop_queue(q.id)
    assert wait_for(lambda: manager.get_job(job.job_id).status == STATUS_PAUSED)
    time.sleep(1.5)
    assert "queue_complete" not in events


def test_restart_and_speed_limit_changes(manager, download_dir):
    job = manager.add_job(url="http://fake/instant", download_dir=download_dir, **FAKE)
    assert wait_for(lambda: manager.get_job(job.job_id).status == STATUS_COMPLETED)
    manager.set_speed_limit(job.job_id, 300)
    assert job.options["speed_limit_kbps"] == 300
    manager.set_speed_limit(job.job_id, 0)
    assert "speed_limit_kbps" not in job.options
    manager.restart(job.job_id)
    assert wait_for(lambda: manager.get_job(job.job_id).status == STATUS_COMPLETED)


def test_queue_fields_survive_restart(settings_store, history_store, tmp_path, fake_provider, monkeypatch):
    _patch_provider(monkeypatch, fake_provider)
    qfile, queues = str(tmp_path / "queue.json"), QueueStore(str(tmp_path / "queues.json"))
    q = queues.create("Saved")
    m1 = DownloadManager(settings_store, history_store, queue_file=qfile, queue_store=queues)
    job = m1.add_job(url="http://fake/slow", download_dir=str(tmp_path), queue_id=q.id, priority=PRIORITY_HIGH,
                     start_at=time.time() + 3600, **FAKE)
    m1.stop()
    m2 = DownloadManager(settings_store, history_store, queue_file=qfile, queue_store=QueueStore(str(tmp_path / "queues.json")))
    assert m2.restore_queue() == 1
    restored = m2.get_job(job.job_id)
    assert restored.queue_id == q.id and restored.priority == PRIORITY_HIGH and restored.start_at


# --------------------------------------------------------------- Schedule --- #
D = datetime.datetime


def test_schedule_events_between_and_last_event_wins():
    s = Schedule(days=list(range(7)), start_enabled=True, start_time="02:00", stop_enabled=True, stop_time="06:00")
    assert s.events_between(D(2026, 10, 20, 1, 59), D(2026, 10, 20, 2, 0)) == [(D(2026, 10, 20, 2, 0), "start")]
    # slept from 01:00 to 07:00: both fired, last one (stop) wins
    events = s.events_between(D(2026, 10, 20, 1, 0), D(2026, 10, 20, 7, 0))
    assert [k for _, k in events] == ["start", "stop"]
    assert s.events_between(D(2026, 10, 20, 3, 0), D(2026, 10, 20, 2, 0)) == []  # clock went backwards


def test_schedule_respects_weekdays():
    s = Schedule(days=[5, 6], start_enabled=True, start_time="09:00")  # weekends
    friday, saturday = D(2026, 10, 23, 9, 0), D(2026, 10, 24, 9, 0)
    assert s.events_between(friday - datetime.timedelta(minutes=1), friday) == []
    assert s.events_between(saturday - datetime.timedelta(minutes=1), saturday)
    assert s.next_start(friday) == saturday


def test_schedule_window_overnight_and_without_stop():
    s = Schedule(start_enabled=True, start_time="23:00", stop_enabled=True, stop_time="05:00")
    assert s.in_window(D(2026, 10, 20, 23, 30)) and s.in_window(D(2026, 10, 21, 4, 0))
    assert not s.in_window(D(2026, 10, 21, 12, 0))
    s2 = Schedule(start_enabled=True, start_time="10:00")
    assert s2.in_window(D(2026, 10, 20, 18, 0)) and not s2.in_window(D(2026, 10, 20, 9, 0))


def test_dst_spring_forward_skipped_time_still_fires():
    # 02:30 doesn't exist on a spring-forward night; the naive interval still contains it
    s = Schedule(start_enabled=True, start_time="02:30")
    assert s.events_between(D(2026, 3, 29, 1, 59, 50), D(2026, 3, 29, 3, 0, 5))


# --------------------------------------------------------------- Scheduler --- #
class _FakeManager:
    def __init__(self, store):
        self.queues = store
        self.calls = []

    def start_queue(self, qid):
        self.calls.append(("start", qid))
        self.queues.set_running(qid, True)

    def stop_queue(self, qid):
        self.calls.append(("stop", qid))
        self.queues.set_running(qid, False)

    def wake(self):
        pass


def test_scheduler_starts_stops_and_catches_up(tmp_path):
    store = QueueStore(None)
    q = store.create("Night", schedule={"start_enabled": True, "start_time": "01:00",
                                        "stop_enabled": True, "stop_time": "05:00"})
    fm = _FakeManager(store)
    sch = Scheduler(fm)
    sch.tick(D(2026, 10, 20, 2, 0))  # first tick inside the window: missed start is caught up
    assert fm.calls == [("start", q.id)]
    sch.tick(D(2026, 10, 20, 5, 0, 10))
    assert fm.calls[-1] == ("stop", q.id)
    sch.tick(D(2026, 10, 21, 0, 59))
    sch.tick(D(2026, 10, 21, 1, 0, 5))
    assert fm.calls[-1] == ("start", q.id)
    # PC slept from 02:00 to 06:00: the stop that passed during sleep applies
    sch.tick(D(2026, 10, 21, 2, 0))
    sch.tick(D(2026, 10, 21, 6, 0))
    assert fm.calls[-1] == ("stop", q.id)


def test_speed_profiles_drive_global_limit():
    store = SchedulerStore(None)
    store.profiles.append(SpeedProfile(id="p", name="Work", limit_kbps=256, days=[0, 1, 2, 3, 4],
                                       start="09:00", end="17:00"))
    store.profiles.append(SpeedProfile(id="n", name="Late", limit_kbps=999, start="22:00", end="06:00"))
    bandwidth.manager.configure(None)
    sch = Scheduler(_FakeManager(QueueStore(None)), store)
    sch.tick(D(2026, 10, 20, 10, 0))  # Tuesday
    assert bandwidth.manager.effective_kbps() == (256, "schedule: Work")
    sch.tick(D(2026, 10, 24, 10, 0))  # Saturday
    assert bandwidth.manager.effective_kbps() == (0, None)
    sch.tick(D(2026, 10, 25, 3, 0))  # overnight window
    assert bandwidth.manager.effective_kbps()[0] == 999
    bandwidth.manager.set_profile(None)


def test_recurring_download_fires_once_per_slot(tmp_path):
    added = []
    store = SchedulerStore(str(tmp_path / "s.json"))
    r = store.add_recurring(url="https://example.com/nightly.zip", time="03:00")
    sch = Scheduler(_FakeManager(QueueStore(None)), store, add_recurring=added.append)
    sch.tick(D(2026, 10, 20, 2, 59))
    sch.tick(D(2026, 10, 20, 3, 0, 10))
    sch.tick(D(2026, 10, 20, 3, 0, 25))
    assert [x.id for x in added] == [r.id]
    assert SchedulerStore(str(tmp_path / "s.json")).recurring[0].last_run > 0
    with pytest.raises(ValueError):
        store.add_recurring(url="  ")
