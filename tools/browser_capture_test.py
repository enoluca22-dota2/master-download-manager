"""Real-browser test of the 4.0 extension features against the packaged app:
download capture (handed over / not matching / app not running) and
"download selected links" into a chosen queue. Uses Playwright's Chromium
with the unpacked Chrome extension and a local web server — no internet.

    python tools/browser_capture_test.py [--exe dist\\MasterDownloadManager\\MasterDownloadManager.exe] [--headed]

What it can't automate: clicking the real right-click menu entry (Chromium
doesn't expose extension context menus to automation), so the selection test
runs the same two functions the menu handler calls.
"""

import argparse
import functools
import http.server
import json
import os
import secrets
import subprocess
import sys
import tempfile
import threading
import time

import requests
from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
parser = argparse.ArgumentParser()
parser.add_argument("--exe", default=os.path.join(ROOT, "dist", "MasterDownloadManager", "MasterDownloadManager.exe"))
parser.add_argument("--headed", action="store_true")
args = parser.parse_args()

PORT, WEB = 47941, 47942
TOKEN = secrets.token_hex(24)
API, H = f"http://127.0.0.1:{PORT}", {"X-MDM-Token": TOKEN}
RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok)))
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}", flush=True)


def poll(fn, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        try:
            value = fn()
            if value:
                return value
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.25)
    return None


def jobs():
    return requests.get(f"{API}/downloads", headers=H, timeout=5).json()["downloads"]


def start_app(data):
    os.makedirs(os.path.join(data, "downloads"), exist_ok=True)
    with open(os.path.join(data, "settings.json"), "w", encoding="utf-8") as f:
        json.dump({"api_port": PORT, "api_token": TOKEN, "minimize_to_tray": False,
                   "download_dir": os.path.join(data, "downloads"), "browser_capture_enabled": True,
                   "browser_capture_types": "zip iso", "browser_capture_min_kb": 0}, f)
    proc = subprocess.Popen([args.exe], env=dict(os.environ, MDM_DATA_DIR=data))
    if not poll(lambda: requests.get(f"{API}/version", timeout=1).ok, 40):
        sys.exit("app didn't start")
    return proc


def web_root():
    root = tempfile.mkdtemp(prefix="mdm-web-")
    for name, size in (("a.zip", 400_000), ("b.zip", 300_000), ("notes.txt", 2_000)):
        with open(os.path.join(root, name), "wb") as f:
            f.write(os.urandom(size))
    base = f"http://127.0.0.1:{WEB}"
    with open(os.path.join(root, "index.html"), "w", encoding="utf-8") as f:
        f.write(f"""<!doctype html><title>files</title>
<a id="zip" href="/a.zip">a.zip</a> <a id="zip2" href="/b.zip">b.zip</a> <a id="txt" href="/notes.txt" download>notes</a>
<p id="sel">Mirror list: <a href="/a.zip">first</a>, <a href="{base}/b.zip">second</a> and the magnet
magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567 plus {base}/notes.txt in text.</p>
<p>outside the selection: <a href="/should-not-be-sent.zip">nope</a></p>""")
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=root)
    http.server.SimpleHTTPRequestHandler.log_message = lambda *a: None
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", WEB), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, base


def main():
    data = tempfile.mkdtemp(prefix="mdm-capture-data-")
    app = start_app(data)
    srv, base = web_root()
    ext = os.path.join(ROOT, "extension-chrome")
    try:
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                tempfile.mkdtemp(prefix="mdm-chrome-"), headless=not args.headed, channel="chromium",
                accept_downloads=True,
                args=[f"--disable-extensions-except={ext}", f"--load-extension={ext}"])
            sw = ctx.service_workers[0] if ctx.service_workers else ctx.wait_for_event("serviceworker", timeout=15000)
            ext_id = sw.url.split("/")[2]
            check("4.0 extension loads", bool(ext_id), ext_id)
            sw.evaluate(f"chrome.storage.local.set({{apiPort: {PORT}, apiToken: '{TOKEN}'}})")

            page = ctx.new_page()
            page.goto(f"chrome-extension://{ext_id}/options.html")
            state = poll(lambda: (t := page.inner_text("#capture-state")) and "Checking" not in t and t)
            check("options page shows capture status from the app", state and state.startswith("On"), state)

            # --- capture: a matching download is handed to MDM ---
            page.goto(f"{base}/index.html")
            page.click("#zip")
            job = poll(lambda: next((j for j in jobs() if j["url"].endswith("/a.zip") and j["status"] == "Completed"), None), 30)
            check("browser .zip download is captured and completed by MDM", job is not None,
                  job and job.get("result_path"))
            leftovers = poll(lambda: (r := sw.evaluate(
                "chrome.downloads.search({}).then(l => l.filter(d => d.url.endsWith('/a.zip')).map(d => d.state))"))
                is not None and len(r) == 0 and "erased", 10)
            check("the browser's own copy was cancelled and removed from its list", leftovers == "erased")

            # --- not a selected type: stays in the browser ---
            before = len(jobs())
            with page.expect_download(timeout=15000) as dl:
                page.click("#txt")
            dl.value.path()
            time.sleep(1.5)
            check(".txt (type not selected) stays with the browser", len(jobs()) == before)

            # --- selected links into a chosen queue ---
            q = requests.get(f"{API}/queues", headers=H).json()
            requests.post(f"{API}/queues/main/start", headers=H)
            sw.evaluate("chrome.storage.local.set({queueId: ''})")
            page.evaluate("""() => { const r = document.createRange(); r.selectNodeContents(document.getElementById('sel'));
                                   const s = getSelection(); s.removeAllRanges(); s.addRange(r); }""")
            collector = sw.evaluate("ytdCollectSelectedLinks.toString()")
            links = page.evaluate(f"({collector})()")
            expected = {f"{base}/a.zip", f"{base}/b.zip", f"{base}/notes.txt",
                        "magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567"}
            check("selection yields exactly the selected links (not the rest of the page)", set(links) == expected,
                  f"{sorted(links)}")
            result = sw.evaluate(f"ytdSendLinks({json.dumps(links)}, '{base}/index.html')")
            check("selected links are queued in one batch", len(result["added"]) == 4 and not result["failed"],
                  f"{result}")
            del q

            # --- MDM not running: captured download falls back to the browser ---
            app.terminate()
            app.wait(10)
            with page.expect_download(timeout=30000) as dl:
                page.click("#zip2")
            path = dl.value.path()
            ok = path and os.path.getsize(path) == 300_000
            check("with MDM closed the browser keeps and finishes the download", ok, path)
            ctx.close()
    finally:
        srv.shutdown()
        if app.poll() is None:
            app.terminate()
    failed = sum(1 for _, ok in RESULTS if not ok)
    print(f"\n{len(RESULTS) - failed}/{len(RESULTS)} browser checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
