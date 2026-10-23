"""4.0 HTTP engine upgrades: dynamic splitting, global/per-download limits,
checksum verification, Last-Modified validation, retry reset. All against
the local test server — no internet."""

import hashlib
import os
import time

import pytest

from ytd import bandwidth
from ytd.providers import http as http_mod
from ytd.providers.base import PauseRequested
from ytd.providers.http import ChecksumMismatch, HttpEngine, parse_checksum, split_to_range

from local_http_server import LocalHTTPServer
from test_http_engine import _job, _payload, _run_until


@pytest.fixture
def server():
    with LocalHTTPServer() as srv:
        yield srv


@pytest.fixture(autouse=True)
def reset_global_limiter():
    bandwidth.manager.configure(None)
    bandwidth.manager.set_profile(None)
    yield
    bandwidth.manager.configure(None)
    bandwidth.manager.set_profile(None)


@pytest.fixture
def small_segments(monkeypatch):
    monkeypatch.setattr(http_mod, "SEGMENT_MIN_FILE", 100_000)
    monkeypatch.setattr(http_mod, "SEGMENT_MIN_SIZE", 32 * 1024)
    monkeypatch.setattr(http_mod, "CHUNK", 16 * 1024)
    monkeypatch.setattr(http_mod, "SAFE_ZONE", 64 * 1024)


# ------------------------------------------------------------- helpers --- #
def test_split_to_range_is_contiguous_and_bounded():
    for size, min_part, parts in ((10, 1, 3), (1_000_000, 64 * 1024, 8), (100, 1000, 4), (7, 2, 16)):
        ranges = split_to_range(size, min_part, parts)
        assert ranges[0][0] == 0 and ranges[-1][1] == size - 1
        assert all(b[0] == a[1] + 1 for a, b in zip(ranges, ranges[1:]))
        assert len(ranges) <= parts
        assert len(ranges) == 1 or all(e - s + 1 >= min(min_part, size) // 2 for s, e in ranges)
    assert split_to_range(100, 1000, 4) == [(0, 99)]
    assert split_to_range(0, 1, 4) == []


def test_parse_checksum_forms():
    digest = hashlib.sha256(b"x").hexdigest()
    assert parse_checksum(f"sha256:{digest}") == ("sha256", digest)
    assert parse_checksum(f"SHA-256 = {digest.upper()}") == ("sha256", digest)
    assert parse_checksum(digest) == ("sha256", digest)
    assert parse_checksum(hashlib.md5(b"x").hexdigest())[0] == "md5"
    assert parse_checksum("") is None
    for bad in ("sha256:xyz", "md5:" + digest, "crc32:abcd", "12345"):
        with pytest.raises(ValueError):
            parse_checksum(bad)


# ---------------------------------------------------- dynamic splitting --- #
def test_idle_connection_splits_the_slow_segment(server, tmp_path, small_segments, settings_store):
    """Two connections: the first half is fast, the second half starts in the
    slow region. When connection 1 runs out of work it must take over the far
    end of segment 2 instead of idling — and the file must stay byte-exact."""
    server.set_slow_delay(0.03)
    data = _payload(1_200_000, seed=11)
    server.files["u.bin"] = data
    settings_store.set("http_connections", 2)
    job = _job(f"{server.base}/uneven/u.bin", str(tmp_path))
    result = HttpEngine(settings_store).run_download(job, lambda d: None)
    assert open(result["filepaths"][0], "rb").read() == data
    starts = sorted(int(r[1][6:].split("-")[0]) for r in server.range_log
                    if r[0] == "/uneven/u.bin" and r[1] and r[1] != "bytes=0-0")
    half = len(data) // 2
    # beyond the two initial segments, at least one request began after the
    # midpoint at an offset no initial segment started at: a split happened
    assert any(s > half + http_mod.SAFE_ZONE for s in starts), starts
    assert not os.path.exists(result["filepaths"][0] + http_mod.STATE_SUFFIX)


def test_split_state_survives_pause_and_resume(server, tmp_path, small_segments, settings_store):
    server.set_slow_delay(0.03)
    data = _payload(1_200_000, seed=12)
    server.files["r.bin"] = data
    settings_store.set("http_connections", 3)
    engine = HttpEngine(settings_store)
    job = _job(f"{server.base}/uneven/r.bin", str(tmp_path))
    with pytest.raises(PauseRequested):
        _run_until(engine, job, 700_000, job.pause_event)
    job.pause_event.clear()
    result = engine.run_download(job, lambda d: None)
    assert open(result["filepaths"][0], "rb").read() == data


def test_connections_option_overrides_setting(server, tmp_path, small_segments, settings_store):
    settings_store.set("http_connections", 1)
    data = _payload(600_000, seed=13)
    server.files["c.bin"] = data
    job = _job(f"{server.base}/files/c.bin", str(tmp_path), options={"connections": 4})
    HttpEngine(settings_store).run_download(job, lambda d: None)
    ranged = {r[1] for r in server.range_log if r[0] == "/files/c.bin" and r[1] and r[1] != "bytes=0-0"}
    assert len(ranged) >= 4


def test_queue_connection_limit_is_used_when_no_per_download_choice(server, tmp_path, small_segments, settings_store):
    settings_store.set("http_connections", 1)
    data = _payload(600_000, seed=14)
    server.files["q.bin"] = data
    job = _job(f"{server.base}/files/q.bin", str(tmp_path))
    job.queue_connections = 3
    HttpEngine(settings_store).run_download(job, lambda d: None)
    ranged = {r[1] for r in server.range_log if r[0] == "/files/q.bin" and r[1] and r[1] != "bytes=0-0"}
    assert len(ranged) >= 3


# ---------------------------------------------------------- validation --- #
def test_checksum_verified_and_mismatch_discards(server, tmp_path, settings_store):
    data = _payload(300_000, seed=21)
    server.files["k.bin"] = data
    good = hashlib.sha256(data).hexdigest()
    ok_job = _job(f"{server.base}/files/k.bin", str(tmp_path), options={"checksum": f"sha256:{good}"})
    events = []
    result = HttpEngine(settings_store).run_download(ok_job, events.append)
    assert open(result["filepaths"][0], "rb").read() == data
    assert any(e.get("postprocessor") == "VerifyChecksum" for e in events)

    bad_dir = tmp_path / "bad"
    bad_dir.mkdir()
    bad_job = _job(f"{server.base}/files/k.bin", str(bad_dir), options={"checksum": "md5:" + "0" * 32})
    with pytest.raises(ChecksumMismatch):
        HttpEngine(settings_store).run_download(bad_job, lambda d: None)
    assert os.listdir(bad_dir) == []  # never left behind as if complete


def test_last_modified_change_restarts(server, tmp_path):
    server.set_slow_delay(0.01)
    server.files["m.bin"] = _payload(400_000, seed=1)
    server.httpd.last_modified["m.bin"] = "Wed, 21 Oct 2026 07:28:00 GMT"
    engine = HttpEngine()
    job = _job(f"{server.base}/lastmod/m.bin", str(tmp_path))
    # /lastmod isn't slow, so pause from the hook immediately
    with pytest.raises(PauseRequested):
        _run_until(engine, job, 1, job.pause_event)
    job.pause_event.clear()
    new_data = _payload(400_000, seed=2)
    server.files["m.bin"] = new_data
    server.httpd.last_modified["m.bin"] = "Thu, 22 Oct 2026 07:28:00 GMT"
    result = engine.run_download(job, lambda d: None)
    assert open(result["filepaths"][0], "rb").read() == new_data


# ----------------------------------------------------------- bandwidth --- #
def test_global_limit_is_shared_by_concurrent_downloads(server, tmp_path, settings_store):
    """Two downloads at once under a 300 KB/s global cap: together they can't
    beat the cap (≈ 900 KB needs ≥ ~2 s after the 1 s burst)."""
    import threading
    settings_store.set("global_speed_limit_kbps", 300)
    bandwidth.manager.configure(settings_store)
    for name in ("g1.bin", "g2.bin"):
        server.files[name] = _payload(450_000, seed=len(name))
    engine = HttpEngine(settings_store)
    jobs = [_job(f"{server.base}/files/{n}", str(tmp_path)) for n in ("g1.bin", "g2.bin")]
    start = time.monotonic()
    threads = [threading.Thread(target=engine.run_download, args=(j, lambda d: None)) for j in jobs]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    elapsed = time.monotonic() - start
    assert 1.6 <= elapsed <= 8, elapsed
    for n in ("g1.bin", "g2.bin"):
        assert (tmp_path / n).read_bytes() == server.files[n]


def test_effective_limit_is_tightest_source(settings_store):
    m = bandwidth.BandwidthManager()
    settings_store.set("global_speed_limit_kbps", 1000)
    settings_store.set("limited_mode_kbps", 200)
    m.configure(settings_store)
    assert m.effective_kbps() == (1000, "global limit")
    m.set_profile(500, "Night")
    assert m.effective_kbps() == (500, "schedule: Night")
    m.set_limited_mode(True)
    assert m.effective_kbps() == (200, "limited mode")
    assert settings_store.get("limited_mode") is True
    m.set_limited_mode(False)
    m.set_profile(None)
    assert m.global_limiter.rate == 1000 * 1024
    assert m.share_for_job(4) == 250 * 1024


def test_rate_change_applies_to_running_limiter():
    lim = bandwidth.RateLimiter(50_000)
    lim.set_rate(0)
    start = time.monotonic()
    for _ in range(20):
        lim.consume(100_000)
    assert time.monotonic() - start < 0.2  # unlimited now


def test_retry_counter_resets_after_progress(server, tmp_path, monkeypatch):
    """A connection that keeps dropping *after receiving data* must not be
    failed by the old error count (AB's retry-reset-on-progress)."""
    data = _payload(300_000, seed=31)
    server.files["f.bin"] = data
    monkeypatch.setattr(http_mod, "STREAM_RETRIES", 2)
    monkeypatch.setattr(http_mod, "CHUNK", 16 * 1024)  # each connection drops after ~48 KB
    real_get = http_mod.requests.Session.get
    calls = {"n": 0}

    class Flaky:
        def __init__(self, resp):
            self._resp = resp

        def __enter__(self):
            return self

        def __exit__(self, *a):
            self._resp.close()

        def __getattr__(self, name):
            return getattr(self._resp, name)

        def iter_content(self, size):
            sent = 0
            for chunk in self._resp.iter_content(size):
                yield chunk
                sent += len(chunk)
                if sent >= 40_000:
                    raise http_mod.requests.ConnectionError("dropped")

    def flaky_get(self, url, **kw):
        calls["n"] += 1
        resp = real_get(self, url, **kw)
        return Flaky(resp) if kw.get("stream") else resp

    monkeypatch.setattr(http_mod.requests.Session, "get", flaky_get)
    monkeypatch.setattr(http_mod.time, "sleep", lambda s: None)
    job = _job(f"{server.base}/files/f.bin", str(tmp_path))
    job.cancel_event.wait = lambda t=None: False  # no real backoff sleeping in the test
    result = HttpEngine().run_download(job, lambda d: None)
    assert open(result["filepaths"][0], "rb").read() == data
    assert calls["n"] > 3  # needed many reconnects, more than STREAM_RETRIES
