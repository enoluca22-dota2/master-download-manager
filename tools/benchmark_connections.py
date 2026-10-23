"""Measures how the number of connections and dynamic splitting affect the
direct (HTTP) engine. Prints a table; nothing here is asserted.

    .venv\\Scripts\\python tools/benchmark_connections.py [--internet] [--repeat 3]

Scenarios (local server, no internet):
  unthrottled   localhost, nothing limits a connection -> shows the overhead
  per-conn cap  every connection trickles ~2 MB/s -> what multi-connection is for
  slow region   one quarter of the file is served slowly -> dynamic splitting
                on (4.0 default) vs off (3.0 behaviour: static segments)
With --internet: python.org's 26 MB Windows installer at 1, 4 and 8 connections.
"""

import argparse
import os
import statistics
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

from local_http_server import LocalHTTPServer  # noqa: E402
from ytd.models import DownloadJob  # noqa: E402
from ytd.providers import http as http_mod  # noqa: E402
from ytd.settings_store import SettingsStore  # noqa: E402

parser = argparse.ArgumentParser()
parser.add_argument("--internet", action="store_true")
parser.add_argument("--repeat", type=int, default=3)
args = parser.parse_args()

INTERNET_URL = "https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe"


def run_once(url, connections, settings):
    """connections=int: exactly that many; ("adaptive", n): MDM's default — up to n, ramped adaptively."""
    folder = tempfile.mkdtemp(prefix="mdm-bench-")
    if isinstance(connections, tuple):
        settings.update({"http_connections": connections[1], "http_adaptive_connections": True})
        options = {}
    else:
        options = {"connections": connections}
    job = DownloadJob(url, "file", "Direct file", "file", False, folder, engine="http", options=options)
    start = time.perf_counter()
    result = http_mod.HttpEngine(settings).run_download(job, lambda d: None)
    elapsed = time.perf_counter() - start
    size = os.path.getsize(result["filepaths"][0])
    os.remove(result["filepaths"][0])
    return elapsed, size


def measure(label, url, connections, settings, repeat):
    times, size = [], 0
    for _ in range(repeat):
        t, size = run_once(url, connections, settings)
        times.append(t)
    med = statistics.median(times)
    shown = f"auto<={connections[1]}" if isinstance(connections, tuple) else str(connections)
    print(f"{label:<34} conn={shown:<6}  median {med:6.2f} s  "
          f"({size / med / 1048576:6.1f} MB/s)  runs: {', '.join(f'{t:.2f}' for t in times)}", flush=True)
    return med


def main():
    settings = SettingsStore(path=os.path.join(tempfile.mkdtemp(), "settings.json"))
    with LocalHTTPServer() as srv:
        data = os.urandom(48 * 1024 * 1024)
        srv.files["big.bin"] = data
        http_mod.SEGMENT_MIN_FILE = 4 * 1024 * 1024
        print("\n== unthrottled localhost (48 MB) ==")
        for n in (1, 4, 8):
            measure("unthrottled", f"{srv.base}/files/big.bin", n, settings, args.repeat)

        print("\n== every connection capped at ~2 MB/s (24 MB) ==")
        srv.files["capped.bin"] = data[:24 * 1024 * 1024]
        srv.set_slow_delay(16 * 1024 / (2 * 1024 * 1024))  # 16 KB per 7.8 ms ≈ 2 MB/s
        for n in (1, 2, 4, 8):
            measure("per-connection cap", f"{srv.base}/slow/capped.bin", n, settings, 1 if n == 1 else args.repeat)
        measure("per-connection cap, adaptive", f"{srv.base}/slow/capped.bin", ("adaptive", 8), settings, args.repeat)

        print("\n== one slow quarter (3rd quarter trickles ~2 MB/s), 4 connections, 24 MB ==")
        srv.files["uneven.bin"] = data[:24 * 1024 * 1024]
        safe = http_mod.SAFE_ZONE
        on = measure("dynamic splitting ON (4.0)", f"{srv.base}/uneven/uneven.bin", 4, settings, args.repeat)
        http_mod.SAFE_ZONE = 10 ** 12  # no segment ever has room beyond the safe zone -> never splits
        off = measure("dynamic splitting OFF (static)", f"{srv.base}/uneven/uneven.bin", 4, settings, args.repeat)
        http_mod.SAFE_ZONE = safe
        print(f"-> dynamic splitting: {off / on:.2f}x faster on this file")

    if args.internet:
        print(f"\n== internet: {INTERNET_URL} ==")
        for n in (1, 4, 8):
            measure("python.org (26 MB)", INTERNET_URL, n, settings, args.repeat)
        measure("python.org (26 MB), adaptive", INTERNET_URL, ("adaptive", 8), settings, args.repeat)


if __name__ == "__main__":
    main()
