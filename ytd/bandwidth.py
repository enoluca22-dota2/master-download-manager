"""Bandwidth control shared by every engine.

Two levels, like AB Download Manager's chained throttlers: one *global*
limiter that every HTTP connection of every download draws from (so the sum
never exceeds the user's cap and concurrent downloads share it fairly —
each connection waits its turn on the same bucket), plus an optional
per-download limiter.

The global rate is the tightest of:
    * Settings → Bandwidth → global limit            (global_speed_limit_kbps)
    * the scheduled speed profile active right now  (set by ytd.scheduler)
    * "Limited mode" toggled from the tray           (limited_mode_kbps)

Engines that can't share a Python-level bucket (yt-dlp, libtorrent) get a
per-download rate derived from the same global value (see share_for_job).
"""

import threading
import time


class RateLimiter:
    """Thread-safe token bucket. rate <= 0 means unlimited. The rate can be
    changed while downloads are running; waiters pick the new value up on
    their next call. A burst of one second's worth of bytes is allowed."""

    def __init__(self, rate_bps=0):
        self._lock = threading.Lock()
        self.rate = float(rate_bps or 0)
        self._allowance = self.rate
        self._last = time.monotonic()

    def set_rate(self, rate_bps):
        with self._lock:
            rate = float(rate_bps or 0)
            if rate != self.rate:
                self.rate = rate
                self._allowance = min(self._allowance, rate) if rate > 0 else 0.0
                self._last = time.monotonic()

    def reserve(self, n):
        """Take n bytes of allowance and return how long the caller must wait.
        Debt carries over, so concurrent callers queue up behind each other
        and the long-run average never exceeds the configured rate."""
        with self._lock:
            if self.rate <= 0:
                return 0.0
            now = time.monotonic()
            self._allowance = min(self.rate, self._allowance + (now - self._last) * self.rate)
            self._last = now
            self._allowance -= n
            return max(0.0, -self._allowance / self.rate)

    def consume(self, n, stop_event=None):
        wait = self.reserve(n)
        if wait > 0:
            if stop_event is not None:
                stop_event.wait(wait)
            else:
                time.sleep(wait)


def consume_all(limiters, n, stop_event=None):
    """Draw n bytes from several limiters (global + per-download); waits for
    the slowest. Reserving from all of them first, then sleeping once for the
    longest wait, keeps two limiters from adding their delays together."""
    wait = 0.0
    for limiter in limiters:
        if limiter is not None:
            wait = max(wait, limiter.reserve(n))
    if wait > 0:
        if stop_event is not None:
            stop_event.wait(wait)
        else:
            time.sleep(wait)


class BandwidthManager:
    def __init__(self):
        self._lock = threading.Lock()
        self.global_limiter = RateLimiter(0)
        self._settings = None
        self._profile_kbps = None  # from the scheduler; None = no profile active
        self._profile_name = None
        self._limited_mode = False

    def configure(self, settings_store):
        self._settings = settings_store
        self._limited_mode = bool(settings_store.get("limited_mode", False)) if settings_store else False
        self.refresh()

    # -- inputs -- #
    def set_profile(self, kbps, name=None):
        with self._lock:
            self._profile_kbps = int(kbps) if kbps else None
            self._profile_name = name if kbps else None
        self.refresh()

    def set_limited_mode(self, enabled):
        with self._lock:
            self._limited_mode = bool(enabled)
        if self._settings is not None:
            self._settings.set("limited_mode", bool(enabled))
        self.refresh()

    @property
    def limited_mode(self):
        return self._limited_mode

    # -- outputs -- #
    def effective_kbps(self):
        """The global cap in KB/s right now (0 = unlimited) and why."""
        s = self._settings
        candidates = []
        if s is not None:
            base = int(s.get("global_speed_limit_kbps", 0) or 0)
            if base > 0:
                candidates.append((base, "global limit"))
            if self._limited_mode:
                limited = int(s.get("limited_mode_kbps", 512) or 0)
                if limited > 0:
                    candidates.append((limited, "limited mode"))
        if self._profile_kbps:
            candidates.append((self._profile_kbps, f"schedule: {self._profile_name}" if self._profile_name else "schedule"))
        if not candidates:
            return 0, None
        return min(candidates, key=lambda c: c[0])

    def refresh(self):
        kbps, _reason = self.effective_kbps()
        self.global_limiter.set_rate(kbps * 1024)

    def share_for_job(self, active_jobs):
        """Per-download byte rate for engines that can't share the global
        bucket (yt-dlp, libtorrent): an equal share of the global cap. 0 when
        there is no global cap."""
        kbps, _ = self.effective_kbps()
        if kbps <= 0:
            return 0
        return max(16 * 1024, int(kbps * 1024 / max(1, active_jobs)))


manager = BandwidthManager()
