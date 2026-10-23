"""Queue load and lifecycle measurements (local server, no internet):
  * 50 queued direct downloads, 10 at a time — total time, all byte-exact
  * 30 pause/resume cycles and 20 cancels — threads and sockets released afterwards

    .venv\\Scripts\\python tools/perf_queue.py
"""

import hashlib
import os
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

from local_http_server import LocalHTTPServer  # noqa: E402
from ytd.engine import DownloadManager  # noqa: E402
from ytd.history import HistoryStore  # noqa: E402
from ytd.models import STATUS_CANCELLED, STATUS_COMPLETED, STATUS_DOWNLOADING, STATUS_PAUSED  # noqa: E402
from ytd.settings_store import SettingsStore  # noqa: E402

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}", flush=True)


def wait(pred, timeout):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.05)
    return pred()


def mdm_threads():
    return [t for t in threading.enumerate() if t.name.startswith(("mdm-job", "mdm-http"))]


def main():
    tmp = tempfile.mkdtemp(prefix="mdm-perf-")
    settings = SettingsStore(path=os.path.join(tmp, "settings.json"))
    settings.update({"max_concurrent_downloads": 10, "max_retries": 0})
    manager = DownloadManager(settings, HistoryStore(path=os.path.join(tmp, "h.json")))
    manager.start()
    peak = {"running": 0}

    def watch(event, job):
        if event in ("started", "progress"):
            peak["running"] = max(peak["running"], sum(1 for j in manager.list_jobs() if j.status == STATUS_DOWNLOADING))
    manager.subscribe(watch)
    with LocalHTTPServer() as srv:
        files = {f"f{i}.bin": os.urandom(1_000_000) for i in range(50)}
        srv.files.update(files)
        t0 = time.perf_counter()
        jobs = [manager.add_job(url=f"{srv.base}/files/{n}", kind="file", quality_label="Direct file",
                                format_key="file", download_dir=os.path.join(tmp, "dl"), engine="http") for n in files]
        queued_ms = (time.perf_counter() - t0) * 1000
        ok = wait(lambda: all(j.status == STATUS_COMPLETED for j in jobs), 120)
        total = time.perf_counter() - t0
        exact = all(hashlib.sha256(open(j.result_path, "rb").read()).digest() == hashlib.sha256(files[os.path.basename(j.url)]).digest()
                    for j in jobs if j.result_path)
        check("50 queued downloads complete byte-exact", ok and exact, f"{total:.2f} s total, queued in {queued_ms:.0f} ms")
        check("never more than 10 running at once", peak["running"] <= 10, f"peak {peak['running']}")
        check("job threads released after the queue drains", wait(lambda: not mdm_threads(), 10),
              f"{len(mdm_threads())} left")

        # pause/resume churn on a slow transfer
        srv.set_slow_delay(0.01)
        srv.files["slow.bin"] = os.urandom(8_000_000)
        job = manager.add_job(url=f"{srv.base}/slow/slow.bin", kind="file", quality_label="Direct file",
                              format_key="file", download_dir=os.path.join(tmp, "dl2"), engine="http")
        cycles = 0
        t0 = time.perf_counter()
        for _ in range(30):
            if not wait(lambda: job.status == STATUS_DOWNLOADING, 10):
                break
            manager.pause(job.job_id)
            if not wait(lambda: job.status == STATUS_PAUSED, 10):
                break
            manager.resume(job.job_id)
            cycles += 1
        ok = wait(lambda: job.status == STATUS_COMPLETED, 120)
        same = ok and open(job.result_path, "rb").read() == srv.files["slow.bin"]
        check("30 pause/resume cycles end in a byte-exact file", cycles == 30 and same,
              f"{cycles} cycles, {time.perf_counter() - t0:.1f} s, final status {job.status}")

        # cancel churn
        cancelled = 0
        for i in range(20):
            srv.files[f"c{i}.bin"] = os.urandom(1_500_000)
            j = manager.add_job(url=f"{srv.base}/slow/c{i}.bin", kind="file", quality_label="Direct file",
                                format_key="file", download_dir=os.path.join(tmp, "dl3"), engine="http")
            wait(lambda: j.status == STATUS_DOWNLOADING, 10)
            manager.cancel(j.job_id)
            if wait(lambda: j.status == STATUS_CANCELLED, 10):
                cancelled += 1
        leftovers = [f for f in os.listdir(os.path.join(tmp, "dl3"))] if os.path.isdir(os.path.join(tmp, "dl3")) else []
        check("20 cancels: every partial file removed", cancelled == 20 and not leftovers, f"{cancelled} cancelled, {leftovers}")
        check("no worker threads left running", wait(lambda: not mdm_threads(), 15), f"{[t.name for t in mdm_threads()]}")
    manager.stop()
    failed = RESULTS.count(False)
    print(f"\n{len(RESULTS) - failed}/{len(RESULTS)} load checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
