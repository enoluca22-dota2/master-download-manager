"""Named download queues (queues.json).

Every download belongs to exactly one queue. The built-in **Main** queue
keeps Master Download Manager's original behaviour: it is always running,
so anything added to it starts as soon as a slot is free. Custom queues
(modelled on AB Download Manager's QueueModel) only run when started — by
hand, from the tray, or by their schedule — and each can have:

    max_concurrent     how many of its downloads run at once (0 = only the global cap)
    max_connections    HTTP connections per download in this queue (0 = the Settings value)
    schedule           weekdays + optional start time + optional stop time
    stop_when_empty    stop the queue once everything in it has finished
    on_complete        none | notify | exit | sleep | hibernate | shutdown

Item order inside a queue is the order of the jobs in the download list, so
the existing move up/down/top/bottom (and drag-and-drop) reorder queues too.
"""

import dataclasses
import datetime
import json
import os
import threading
import uuid

MAIN_QUEUE_ID = "main"
COMPLETION_ACTIONS = ("none", "notify", "exit", "sleep", "hibernate", "shutdown")
POWER_ACTIONS = ("sleep", "hibernate", "shutdown")
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")  # datetime.weekday() order


def parse_hhmm(text):
    """'07:30' -> datetime.time(7, 30). Raises ValueError for anything else."""
    text = (text or "").strip()
    try:
        hours, minutes = text.split(":")
        value = datetime.time(int(hours), int(minutes))
    except (ValueError, TypeError) as e:
        raise ValueError(f"Invalid time {text!r}; use HH:MM (24-hour).") from e
    return value


@dataclasses.dataclass
class Schedule:
    days: list = dataclasses.field(default_factory=lambda: list(range(7)))  # 0 = Monday
    start_enabled: bool = False
    start_time: str = "02:30"
    stop_enabled: bool = False
    stop_time: str = "07:30"

    def validate(self):
        days = sorted({int(d) for d in self.days if 0 <= int(d) <= 6})
        if not days:
            raise ValueError("A schedule needs at least one day.")
        self.days = days
        parse_hhmm(self.start_time)
        parse_hhmm(self.stop_time)
        return self

    @property
    def enabled(self):
        return self.start_enabled or self.stop_enabled

    def _moments(self, kind, since, until):
        """Local datetimes in (since, until] at which `kind` ('start'/'stop') fires."""
        t = parse_hhmm(self.start_time if kind == "start" else self.stop_time)
        out = []
        day = since.date()
        while day <= until.date():
            if day.weekday() in self.days:
                moment = datetime.datetime.combine(day, t)
                if since < moment <= until:
                    out.append(moment)
            day += datetime.timedelta(days=1)
        return out

    def events_between(self, since, until):
        """Schedule events that fell in (since, until], oldest first, as
        (moment, 'start'|'stop'). A long sleep can span several days; only
        the last event matters for what the queue should be doing now."""
        if until <= since:
            return []  # the clock went backwards (DST fall-back / manual change): nothing new happened
        since = max(since, until - datetime.timedelta(days=8))
        events = []
        if self.start_enabled:
            events += [(m, "start") for m in self._moments("start", since, until)]
        if self.stop_enabled:
            events += [(m, "stop") for m in self._moments("stop", since, until)]
        return sorted(events)

    def in_window(self, now):
        """True when `now` is inside a start→stop window (used to catch up on a
        start that was missed because MDM wasn't running). Without a stop time
        the window runs to the end of the start day."""
        if not self.start_enabled:
            return False
        start = parse_hhmm(self.start_time)
        stop = parse_hhmm(self.stop_time) if self.stop_enabled else datetime.time(23, 59, 59)
        for offset in (0, 1):
            day = now.date() - datetime.timedelta(days=offset)
            if day.weekday() not in self.days:
                continue
            begin = datetime.datetime.combine(day, start)
            end_day = day if stop > start else day + datetime.timedelta(days=1)
            end = datetime.datetime.combine(end_day, stop)
            if begin <= now < end:
                return True
        return False

    def next_start(self, now):
        if not self.start_enabled:
            return None
        found = self._moments("start", now, now + datetime.timedelta(days=8))
        return found[0] if found else None

    def to_dict(self):
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data):
        data = data or {}
        fields = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in fields}).validate()


@dataclasses.dataclass
class Queue:
    id: str
    name: str
    max_concurrent: int = 0
    max_connections: int = 0
    running: bool = False
    stop_when_empty: bool = False
    on_complete: str = "none"
    schedule: Schedule = dataclasses.field(default_factory=Schedule)

    @property
    def is_main(self):
        return self.id == MAIN_QUEUE_ID

    def to_dict(self):
        d = dataclasses.asdict(self)
        d["schedule"] = self.schedule.to_dict()
        return d

    @classmethod
    def from_dict(cls, data):
        fields = {f.name for f in dataclasses.fields(cls)}
        values = {k: v for k, v in data.items() if k in fields}
        values["schedule"] = Schedule.from_dict(data.get("schedule"))
        q = cls(**values)
        q.max_concurrent = max(0, int(q.max_concurrent or 0))
        q.max_connections = max(0, min(16, int(q.max_connections or 0)))
        if q.on_complete not in COMPLETION_ACTIONS:
            q.on_complete = "none"
        q.name = (q.name or "").strip()[:60] or "Queue"
        return q


def _main_queue():
    return Queue(id=MAIN_QUEUE_ID, name="Main", running=True)


class QueueStore:
    """Thread-safe list of queues persisted atomically to queues.json."""

    def __init__(self, path=None):
        self._path = path
        self._lock = threading.RLock()
        self._queues = self._load()

    def _load(self):
        queues = []
        if self._path and os.path.isfile(self._path):
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for item in (data.get("queues") if isinstance(data, dict) else None) or []:
                    try:
                        queues.append(Queue.from_dict(item))
                    except (ValueError, TypeError, KeyError):
                        continue
            except (OSError, ValueError):
                queues = []
        if not any(q.is_main for q in queues):
            queues.insert(0, _main_queue())
        for q in queues:
            if q.is_main:
                q.running = True  # Main always resumes processing at startup, as MDM always did
        return queues

    def _save(self):
        if not self._path:
            return
        try:
            os.makedirs(os.path.dirname(os.path.abspath(self._path)), exist_ok=True)
            tmp = f"{self._path}.tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"version": 1, "queues": [q.to_dict() for q in self._queues]}, f, indent=1)
            os.replace(tmp, self._path)
        except OSError:
            pass

    # -- queries -- #
    def all(self):
        with self._lock:
            return list(self._queues)

    def get(self, queue_id):
        with self._lock:
            return next((q for q in self._queues if q.id == queue_id), None)

    def resolve(self, queue_id):
        """The queue a job belongs to; unknown/deleted ids fall back to Main."""
        return self.get(queue_id) or self.get(MAIN_QUEUE_ID)

    # -- mutations -- #
    def create(self, name, **fields):
        name = (name or "").strip()
        if not name:
            raise ValueError("A queue needs a name.")
        with self._lock:
            if any(q.name.lower() == name.lower() for q in self._queues):
                raise ValueError(f"There is already a queue called “{name}”.")
            q = Queue.from_dict({"id": uuid.uuid4().hex[:12], "name": name, **fields})
            self._queues.append(q)
            self._save()
            return q

    def update(self, queue_id, **fields):
        with self._lock:
            q = self.get(queue_id)
            if q is None:
                raise KeyError(f"No such queue: {queue_id}")
            if "name" in fields:
                name = (fields["name"] or "").strip()
                if not name:
                    raise ValueError("A queue needs a name.")
                if any(o.id != q.id and o.name.lower() == name.lower() for o in self._queues):
                    raise ValueError(f"There is already a queue called “{name}”.")
            data = q.to_dict()
            if "schedule" in fields and isinstance(fields["schedule"], Schedule):
                fields["schedule"] = fields["schedule"].to_dict()
            data.update(fields)
            data["id"] = q.id
            updated = Queue.from_dict(data)
            if updated.is_main:
                updated.stop_when_empty = False  # Main can't stop itself
            self._queues[self._queues.index(q)] = updated
            self._save()
            return updated

    def set_running(self, queue_id, running):
        with self._lock:
            q = self.get(queue_id)
            if q is None:
                raise KeyError(f"No such queue: {queue_id}")
            q.running = bool(running)
            self._save()
            return q

    def delete(self, queue_id):
        if queue_id == MAIN_QUEUE_ID:
            raise ValueError("The Main queue can't be deleted.")
        with self._lock:
            q = self.get(queue_id)
            if q is None:
                raise KeyError(f"No such queue: {queue_id}")
            self._queues.remove(q)
            self._save()
            return q
