"""Real-browser test of the Chrome extension against a running Master
Download Manager (normally the packaged exe), using Playwright's Chromium.

    python tools/browser_extension_test.py --port 47911 --token <token> [--headed]
"""

import argparse
import os
import sys
import tempfile
import time

import requests
from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
parser = argparse.ArgumentParser()
parser.add_argument("--port", type=int, required=True)
parser.add_argument("--token", required=True)
parser.add_argument("--headed", action="store_true")
parser.add_argument("--out", default=os.path.join(tempfile.gettempdir(), "mdm-browser-test"))
args = parser.parse_args()
os.makedirs(args.out, exist_ok=True)

VIDEO = "https://www.youtube.com/watch?v=aqz-KE-bpKQ"
PLAYLIST_VIDEO = "https://www.youtube.com/watch?v=u9lj-c29dxI&list=PL6B3937A5D230E335&index=2"
API = f"http://127.0.0.1:{args.port}"
H = {"X-MDM-Token": args.token}
RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}", flush=True)


def poll(fn, timeout=10):
    """Extension pages forbid eval, so wait by polling from Python."""
    end = time.time() + timeout
    while time.time() < end:
        try:
            if fn():
                return True
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.2)
    return False


def jobs():
    return requests.get(f"{API}/downloads", headers=H, timeout=10).json()["downloads"]


def accept_consent(page):
    """EU consent interstitials: choose 'Reject all' (no cookies needed for this test)."""
    for label in ("Reject all", "Reject the use of cookies", "Alle ablehnen", "Tout refuser"):
        btn = page.get_by_role("button", name=label)
        if btn.count():
            try:
                btn.first.click(timeout=3000)
                page.wait_for_load_state("domcontentloaded")
                return True
            except Exception:  # noqa: BLE001
                pass
    return False


def open_video(page, url):
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    if "consent." in page.url:
        accept_consent(page)
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_selector("#movie_player", timeout=45000)


def main():
    ext = os.path.join(ROOT, "extension-chrome")
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            tempfile.mkdtemp(prefix="mdm-chrome-"), headless=not args.headed, channel="chromium",
            args=[f"--disable-extensions-except={ext}", f"--load-extension={ext}", "--mute-audio"],
            viewport={"width": 1280, "height": 800})
        sw = ctx.service_workers[0] if ctx.service_workers else ctx.wait_for_event("serviceworker", timeout=15000)
        ext_id = sw.url.split("/")[2]
        check("extension loads (service worker running)", bool(ext_id), ext_id)

        # --- pairing via the options page ---
        page = ctx.new_page()
        page.goto(f"chrome-extension://{ext_id}/options.html")
        page.fill("#port", str(args.port))
        page.fill("#token", args.token)
        page.click("#test")
        poll(lambda: "Connected" in page.inner_text("#status") or "✕" in page.inner_text("#status"))
        status = page.inner_text("#status")
        check("options page connects to the app", "Connected" in status, status)

        # --- popup page shows connection ---
        page.goto(f"chrome-extension://{ext_id}/popup.html")
        poll(lambda: "hecking" not in page.inner_text("#ytd-conn-text"))
        check("toolbar popup reports the connection", "Connected" in page.inner_text("#ytd-conn-text"),
              page.inner_text("#ytd-conn-text"))

        # --- in-player button ---
        open_video(page, VIDEO)
        btn = page.wait_for_selector("#movie_player #ytd-download-button", timeout=30000)
        check("download button injected inside the YouTube player", btn is not None)
        page.screenshot(path=os.path.join(args.out, "01-button.png"))
        box = btn.bounding_box()
        page.mouse.move(box["x"] + 20, box["y"] + box["height"] / 2)
        page.mouse.down()
        page.mouse.move(box["x"] - 220, box["y"] - 140, steps=12)
        page.mouse.up()
        time.sleep(0.5)
        pos = sw.evaluate("chrome.storage.local.get('ytdButtonPosition').then(r => r.ytdButtonPosition)")
        check("button is draggable and its position is remembered", pos and pos.get("xPercent", 1) < 0.85, f"{pos}")
        page.reload(wait_until="domcontentloaded")
        btn = page.wait_for_selector("#movie_player #ytd-download-button", timeout=30000)
        box2 = btn.bounding_box()
        check("remembered position is restored after reload", abs(box2["x"] - (box["x"] - 240)) < 120, f"x={box2['x']:.0f}")

        # --- format popup -> queued job ---
        before = {j["id"] for j in jobs()}
        btn.click()
        page.wait_for_selector("#ytd-format-popup .ytd-row", timeout=60000)
        page.screenshot(path=os.path.join(args.out, "02-format-popup.png"))
        rows = page.query_selector_all("#ytd-format-popup .ytd-row:not(.ytd-audio-row):not(.ytd-more-link)")
        labels = [r.inner_text() for r in rows]
        check("format popup lists real formats", any("p" in l for l in labels), f"{len(labels)} rows")
        more = page.query_selector("#ytd-format-popup .ytd-more-link")
        if more:
            more.click()
            time.sleep(0.5)
        rows = page.query_selector_all("#ytd-format-popup .ytd-row:not(.ytd-audio-row):not(.ytd-more-link)")
        target = next((r for r in rows if r.inner_text().startswith("MP4 360p")), None)
        check("'More qualities' reveals lower resolutions (360p)", target is not None, f"{len(rows)} rows")
        (target or rows[-1]).click()
        deadline = time.time() + 15
        new = []
        while time.time() < deadline and not new:
            new = [j for j in jobs() if j["id"] not in before]
            time.sleep(0.5)
        check("clicking a format queues exactly that format in the app",
              bool(new) and new[0]["source"] == "extension" and new[0]["quality_label"] == "360p",
              f"{new[0]['quality_label'] if new else ''}")
        for j in new:
            requests.post(f"{API}/downloads/{j['id']}/cancel", headers=H, timeout=10)

        # --- playlist URL: list=/index= preserved, Playlist tab ---
        open_video(page, PLAYLIST_VIDEO)
        btn = page.wait_for_selector("#movie_player #ytd-download-button", timeout=30000)
        btn.click()
        page.wait_for_selector("#ytd-format-popup .ytd-tab[data-tab='playlist']", timeout=30000)
        check("playlist page shows the Playlist tab", True)
        page.click("#ytd-format-popup .ytd-tab[data-tab='playlist']")
        page.wait_for_selector("#ytd-format-popup .ytd-playlist-row, #ytd-format-popup .ytd-error-block", timeout=90000)
        if page.query_selector("#ytd-format-popup .ytd-error-block"):
            page.screenshot(path=os.path.join(args.out, "03-playlist-error.png"))
            print("      playlist tab error:", page.inner_text("#ytd-format-popup .ytd-error-block"))
        count = len(page.query_selector_all("#ytd-format-popup .ytd-playlist-row"))
        page.screenshot(path=os.path.join(args.out, "03-playlist-tab.png"))
        check("Playlist tab lists the playlist's videos", count >= 3, f"{count} entries")
        page.click("#ytd-format-popup .ytd-tab[data-tab='video']")
        page.wait_for_selector("#ytd-format-popup .ytd-row", timeout=60000)
        before = {j["id"] for j in jobs()}
        page.query_selector_all("#ytd-format-popup .ytd-row:not(.ytd-audio-row):not(.ytd-more-link)")[-1].click()
        time.sleep(3)
        new = [j for j in jobs() if j["id"] not in before]
        url = new[0]["url"] if new else ""
        check("list= and index= are preserved in the URL sent to the app", "list=PL6B3937A5D230E335" in url and "index=" in url, url)
        for j in new:
            requests.post(f"{API}/downloads/{j['id']}/cancel", headers=H, timeout=10)

        # --- dismiss for this session ---
        page.click("#ytd-download-button .ytd-btn-x")
        time.sleep(0.5)
        gone = page.query_selector("#ytd-download-button") is None
        page.reload(wait_until="domcontentloaded")
        time.sleep(4)
        still_gone = page.query_selector("#ytd-download-button") is None
        check("× hides the button for the rest of the session", gone and still_gone)
        sw.evaluate("chrome.storage.session.set({ytdSessionDismissed: false})")

        # --- right-click 'Download link with MDM' (handler invoked directly) ---
        before = {j["id"] for j in jobs()}
        sw.evaluate("""ytdOnMenuClicked({menuItemId: 'mdm-download-link',
                       linkUrl: 'https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-arm64.zip'},
                       {url: 'https://www.python.org/downloads/'})""")
        time.sleep(4)
        new = [j for j in jobs() if j["id"] not in before]
        check("context-menu link is queued with the right engine", bool(new) and new[0]["engine"] == "http",
              f"{new[0]['engine'] if new else ''}")

        # --- wrong token is diagnosed as a token problem ---
        sw.evaluate("chrome.storage.local.set({apiToken: 'wrong-token'})")
        open_video(page, VIDEO)
        page.wait_for_selector("#ytd-download-button", timeout=30000).click()
        page.wait_for_selector("#ytd-format-popup .ytd-error", timeout=15000)
        err = page.inner_text("#ytd-format-popup .ytd-error")
        check("a wrong token is reported as a token mismatch", "token" in err.lower(), err[:80])
        sw.evaluate(f"chrome.storage.local.set({{apiToken: '{args.token}'}})")
        ctx.close()

    failed = [r for r in RESULTS if not r[1]]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} browser checks passed. Screenshots: {args.out}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
