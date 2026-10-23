"""Download scheduler: queue start/stop times, speed-limit profiles and
recurring downloads (scheduler.json for profiles + recurring tasks; queue
schedules live with their queue in queues.json).

Robust to the PC sleeping, MDM not running and clock changes because it
never relies on a timer firing at the right moment. Every tick (15 s) it
looks at the wall-clock interval since the previous tick and applies
whatever schedule events fell inside it:

  * sleep / hibernate: on wake the interval spans the whole sleep, so a
    start time that passed meanwhile still starts the queue (and if a stop
    time passed after it, the queue ends up stopped — the last event wins)
  * MDM wasn't running: on the first tick a queue whose start→stop window
    contains "now" is started (a missed start is caught up)
  * daylight saving: times are local wall-clock times; when the clock jumps
    back the interval is empty (nothing fires twice), when it jumps forward
    the skipped times are inside the interval and still fire
"""

import dataclasses
import datetime
import json
import os
import threading
import time
import uuid

from . import bandwidth
from .logging_setup import get_logger
from .queues import Schedule, parse_hhmm

log = get_logger("engine")

TICK_SECONDS = 15


@dataclasses.dataclass
class SpeedProfile:
    """A bandwidth cap for a time window, e.g. 512 KB/s on weekdays 09:00–18:00."""
    id: str
    name: str
    limit_kbps: int
    days: list = dataclasses.field(default_factory=lambda: list(range(7)))
    start: str = "09:00"
    end: str = "18:00"
    enabled: bool = True

    def active(self, now):
        if not self.enabled or self.limit_kbps <= 0:
            return False
        start, end = parse_hhmm(self.start), parse_hhmm(self.end)
        t = now.time()
        if start <= end:
            return now.weekday() in self.days and start <= t < end
        # overnight window (e.g. 22:00–06:00): before midnight counts for today,
        # after midnight for the day the window started on
        if t >= start:
            return now.weekday() in self.days
        return (now - datetime.timedelta(days=1)).weekday() in self.days and t < end

    @classmethod
    def from_dict(cls, d):
        fields = {f.name for f in dataclasses.fields(cls)}
        p = cls(**{k: v for k, v in d.items() if k in fields})
        p.limit_kbps = max(0, int(p.limit_kbps or 0))
        p.days = sorted({int(x) for x in p.days if 0 <= int(x) <= 6}) or list(range(7))
        parse_hhmm(p.start)
        parse_hhmm(p.end)
        return p


@dataclasses.dataclass
class RecurringDownload:
    """Download the same link again on a schedule (e.g. a nightly build)."""
    id: str
    url: str
    days: list = dataclasses.field(default_factory=lambda: list(range(7)))
    time: str = "03:00"
    queue_id: str = "main"
    enabled: bool = True
    last_run: float = 0.0

    def due_between(self, since, until):
        sched = Schedule(days=self.days, start_enabled=True, start_time=self.time)
        return [m for m, kind in sched.events_between(since, until) if kind == "start"]

    @classmethod
    def from_dict(cls, d):
        fields = {f.name for f in dataclasses.fields(cls)}
        r = cls(**{k: v for k, v in d.items() if k in fields})
        r.days = sorted({int(x) for x in r.days if 0 <= int(x) <= 6}) or list(range(7))
        parse_hhmm(r.time)
        return r


class SchedulerStore:
    def __init__(self, path=None):
        self._path = path
        self._lock = threading.Lock()
        self.profiles, self.recurring = [], []
        self._load()

    def _load(self):
        if not self._path or not os.path.isfile(self._path):
            return
        try:
            with open(self._path, "r", encoding="utf-8-sig") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return
        for item in data.get("speed_profiles") or []:
            try:
                self.profiles.append(SpeedProfile.from_dict(item))
            except (ValueError, TypeError):
                continue
        for item in data.get("recurring") or []:
            try:
                self.recurring.append(RecurringDownload.from_dict(item))
            except (ValueError, TypeError):
                continue

    def save(self):
        if not self._path:
            return
        with self._lock:
            data = {"version": 1, "speed_profiles": [dataclasses.asdict(p) for p in self.profiles],
                    "recurring": [dataclasses.asdict(r) for r in self.recurring]}
        try:
            os.makedirs(os.path.dirname(os.path.abspath(self._path)), exist_ok=True)
            tmp = f"{self._path}.tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=1)
            os.replace(tmp, self._path)
        except OSError:
            pass

    def add_profile(self, **fields):
        p = SpeedProfile.from_dict({"id": uuid.uuid4().hex[:12], **fields})
        with self._lock:
            self.profiles.append(p)
        self.save()
        return p

    def add_recurring(self, **fields):
        url = (fields.get("url") or "").strip()
        if not url:
            raise ValueError("A recurring download needs a link.")
        r = RecurringDownload.from_dict({"id": uuid.uuid4().hex[:12], **fields, "url": url})
        with self._lock:
            self.recurring.append(r)
        self.save()
        return r

    def remove(self, item_id):
        with self._lock:
            self.profiles = [p for p in self.profiles if p.id != item_id]
            self.recurring = [r for r in self.recurring if r.id != item_id]
        self.save()


class Scheduler:
    def __init__(self, manager, store=None, now_fn=datetime.datetime.now, add_recurring=None):
        self.manager = manager
        self.store = store or SchedulerStore(None)
        self._now = now_fn
        self._add_recurring = add_recurring  # callable(RecurringDownload) -> job; defaults to router defaults
        self._last = None
        self._stop = threading.Event()
        self._thread = None
        self.active_profile = None

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="mdm-scheduler", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _loop(self):
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:  # noqa: BLE001 - the scheduler must keep running
                log.exception("Scheduler tick failed")
            self._stop.wait(TICK_SECONDS)

    def tick(self, now=None):
        now = now or self._now()
        first = self._last is None
        since = self._last or now
        for q in self.manager.queues.all():
            s = q.schedule
            if not s.enabled:
                continue
            if first:
                inside = s.in_window(now)
                if s.start_enabled and inside and not q.running:
                    log.info("Scheduler: catching up missed start of queue %s", q.name)
                    self.manager.start_queue(q.id)
                elif s.start_enabled and s.stop_enabled and not inside and q.running and not q.is_main:
                    self.manager.stop_queue(q.id)
                continue
            events = s.events_between(since, now)
            if not events:
                continue
            kind = events[-1][1]
            if kind == "start" and not q.running:
                log.info("Scheduler: starting queue %s", q.name)
                self.manager.start_queue(q.id)
            elif kind == "stop" and q.running:
                log.info("Scheduler: stopping queue %s", q.name)
                self.manager.stop_queue(q.id)

        active = [p for p in self.store.profiles if p.active(now)]
        best = min(active, key=lambda p: p.limit_kbps) if active else None
        if (best and best.id) != (self.active_profile and self.active_profile.id):
            log.info("Scheduler: speed profile %s", f"{best.name} ({best.limit_kbps} KB/s)" if best else "off")
        self.active_profile = best
        bandwidth.manager.set_profile(best.limit_kbps if best else None, best.name if best else None)

        changed = False
        for r in self.store.recurring:
            if not r.enabled:
                continue
            last = datetime.datetime.fromtimestamp(r.last_run) if r.last_run else since
            if r.due_between(max(last, now - datetime.timedelta(days=1)), now):
                r.last_run = time.time()
                changed = True
                try:
                    self._queue_recurring(r)
                except Exception as e:  # noqa: BLE001
                    log.warning("Recurring download %s couldn't be queued: %s", r.url, e)
        if changed:
            self.store.save()
        self.manager.wake()  # scheduled single downloads (start_at) become eligible
        self._last = now

    def _queue_recurring(self, r):
        if self._add_recurring is not None:
            return self._add_recurring(r)
        from .router import classify, default_selection
        engine = classify(r.url)
        if engine is None:
            raise ValueError("Not a downloadable link")
        format_id, category, options = default_selection(engine, self.manager._settings)
        from .providers import resolve_format_selection
        kind, label, key, bitrate = resolve_format_selection(r.url, format_id, engine)
        log.info("Scheduler: recurring download %s", r.url)
        return self.manager.add_job(url=r.url, kind=kind, quality_label=label, format_key=key,
                                    audio_bitrate=bitrate or "192", source="scheduler", category=category,
                                    engine=engine, options=options, queue_id=r.queue_id)
