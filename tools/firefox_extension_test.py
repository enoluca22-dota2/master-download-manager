"""Real-Firefox test of the Firefox extension against a running Master
Download Manager (normally the packaged exe), via geckodriver + Selenium.

    python tools/firefox_extension_test.py --port 47911 --token <token> \
        --firefox <path\\to\\firefox.exe> --geckodriver <path\\to\\geckodriver.exe> [--xpi dist\\...firefox.zip]

Firefox only lets automation open moz-extension:// pages and use real
context menus through its privileged "chrome" context, so geckodriver is
started with --allow-system-access.
"""

import argparse
import http.server
import json
import os
import sys
import threading
import time

import requests
from selenium import webdriver
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.firefox.service import Service

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
parser = argparse.ArgumentParser()
parser.add_argument("--port", type=int, required=True)
parser.add_argument("--token", required=True)
parser.add_argument("--firefox", required=True)
parser.add_argument("--geckodriver", required=True)
parser.add_argument("--xpi", default=os.path.join(ROOT, "dist", "MasterDownloadManager-extension-firefox.zip"))
parser.add_argument("--headed", action="store_true")
args = parser.parse_args()

ADDON_ID = "enagrio-download-manager@enagrio.local"  # kept from earlier versions so existing installs keep their pairing
UUID = "7c1a9e52-3c4b-4b8e-9f27-6a0d2b1c9e11"
VIDEO = "https://www.youtube.com/watch?v=aqz-KE-bpKQ"
PLAYLIST_VIDEO = "https://www.youtube.com/watch?v=u9lj-c29dxI&list=PL6B3937A5D230E335&index=2"
LINK_TARGET = "https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-win32.zip"
API = f"http://127.0.0.1:{args.port}"
H = {"X-MDM-Token": args.token}
RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}", flush=True)


def jobs():
    return requests.get(f"{API}/downloads", headers=H, timeout=10).json()["downloads"]


def new_jobs(before, timeout=15):
    end = time.time() + timeout
    while time.time() < end:
        found = [j for j in jobs() if j["id"] not in before]
        if found:
            return found
        time.sleep(0.5)
    return []


def cancel(found):
    for j in found:
        requests.post(f"{API}/downloads/{j['id']}/cancel", headers=H, timeout=10)


def wait(fn, timeout=30):
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


def open_trusted(d, url):
    d.set_context("chrome")
    d.execute_script("openTrustedLinkIn(arguments[0], 'current')", url)
    d.set_context("content")
    time.sleep(1.5)


def link_page_server():
    html = (f'<html><body><h1>MDM context-menu test</h1><a id="dl" href="{LINK_TARGET}" '
            f'style="font-size:40px">Download the embeddable zip</a></body></html>').encode()

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(html)))
            self.end_headers()
            self.wfile.write(html)

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def click_context_menu_item(d, element, label_part):
    """Right-click `element` and choose a menu item in Firefox's real context menu."""
    ActionChains(d).context_click(element).perform()
    time.sleep(1)
    d.set_context("chrome")
    try:
        clicked = d.execute_script("""
            const menu = document.getElementById('contentAreaContextMenu');
            const items = [...menu.querySelectorAll('menuitem')].filter(i => !i.hidden && (i.label || '').includes(arguments[0]));
            if (!items.length) { menu.hidePopup(); return [...menu.querySelectorAll('menuitem')].filter(i => !i.hidden).map(i => i.label).join(' | '); }
            items[0].doCommand ? items[0].doCommand() : items[0].click();
            menu.hidePopup();
            return 'clicked: ' + items[0].label;
        """, label_part)
    finally:
        d.set_context("content")
    return clicked


def main():
    opts = webdriver.FirefoxOptions()
    opts.binary_location = args.firefox
    if not args.headed:
        opts.add_argument("-headless")
    opts.set_preference("extensions.webextensions.uuids", json.dumps({ADDON_ID: UUID}))
    opts.set_preference("media.autoplay.default", 5)
    d = webdriver.Firefox(options=opts, service=Service(args.geckodriver, service_args=["--allow-system-access"]))
    d.set_window_size(1280, 900)
    try:
        addon = d.install_addon(args.xpi, temporary=True)
        check("Firefox installs the packaged extension (temporary add-on)", addon == ADDON_ID, addon)

        open_trusted(d, f"moz-extension://{UUID}/options.html")
        d.find_element(By.ID, "port").clear()
        d.find_element(By.ID, "port").send_keys(str(args.port))
        d.find_element(By.ID, "token").clear()
        d.find_element(By.ID, "token").send_keys(args.token)
        d.find_element(By.ID, "test").click()
        status = wait(lambda: (lambda t: t if ("Connected" in t or "✕" in t) else None)(d.find_element(By.ID, "status").text), 10)
        check("options page connects to the app", status and "Connected" in status, status)

        # popup + "Send this page" (the popup runs as a tab, so its active-tab lookup is pointed at a real page)
        open_trusted(d, f"moz-extension://{UUID}/popup.html")
        conn = wait(lambda: (lambda t: t if "hecking" not in t else None)(d.find_element(By.ID, "ytd-conn-text").text), 10)
        check("toolbar popup reports the connection", conn and "Connected" in conn, conn)
        before = {j["id"] for j in jobs()}
        d.execute_script(f"window.getActiveTabUrl = async () => '{LINK_TARGET}'; init();")
        btn = wait(lambda: (lambda b: b if b.is_displayed() else None)(d.find_element(By.ID, "ytd-send-page")), 10)
        btn.click()
        found = new_jobs(before)
        note = wait(lambda: (lambda t: t if ("Added" in t or "Couldn't" in t) else None)(d.find_element(By.ID, "ytd-send-status").text), 10)
        check("'Send this page' queues the page in the app", found and found[0]["engine"] == "http", note)
        cancel(found)

        # in-player button
        d.get(VIDEO)
        btn = wait(lambda: d.find_element(By.CSS_SELECTOR, "#movie_player #ytd-download-button"), 45)
        check("download button injected inside the YouTube player", btn is not None)
        d.execute_script("window.scrollTo(0, 0)")
        time.sleep(1)
        start = btn.location
        for _ in range(3):  # headless mouse synthesis occasionally fails transiently
            try:
                ActionChains(d).move_to_element_with_offset(btn, -10, 0).click_and_hold().move_by_offset(-110, -70) \
                    .move_by_offset(-110, -70).release().perform()
                break
            except Exception:  # noqa: BLE001
                time.sleep(1)
                btn = d.find_element(By.CSS_SELECTOR, "#ytd-download-button")
        time.sleep(0.7)
        moved = d.find_element(By.CSS_SELECTOR, "#ytd-download-button").location
        d.refresh()
        btn = wait(lambda: d.find_element(By.CSS_SELECTOR, "#movie_player #ytd-download-button"), 45)
        time.sleep(1.5)
        restored = btn.location
        check("button is draggable and its position is remembered after reload",
              moved["x"] < start["x"] - 100 and abs(restored["x"] - moved["x"]) < 80, f"{start} -> {moved} -> {restored}")

        before = {j["id"] for j in jobs()}
        btn.click()
        wait(lambda: d.find_elements(By.CSS_SELECTOR, "#ytd-format-popup .ytd-row"), 60)
        more = d.find_elements(By.CSS_SELECTOR, "#ytd-format-popup .ytd-more-link")
        if more:
            more[0].click()
            time.sleep(0.5)
        rows = d.find_elements(By.CSS_SELECTOR, "#ytd-format-popup .ytd-row:not(.ytd-audio-row):not(.ytd-more-link)")
        target = next((r for r in rows if r.text.startswith("MP4 360p")), None)
        check("format popup lists real formats incl. 360p", target is not None, f"{len(rows)} rows")
        (target or rows[-1]).click()
        found = new_jobs(before)
        check("clicking a format queues exactly that format", found and found[0]["quality_label"] == "360p",
              found[0]["quality_label"] if found else "")
        cancel(found)

        # playlist handling + URL transfer
        d.get(PLAYLIST_VIDEO)
        btn = wait(lambda: d.find_element(By.CSS_SELECTOR, "#movie_player #ytd-download-button"), 45)
        time.sleep(2)  # YouTube corrects index= shortly after load
        btn.click()
        tab = wait(lambda: d.find_element(By.CSS_SELECTOR, "#ytd-format-popup .ytd-tab[data-tab='playlist']"), 30)
        check("playlist page shows the Playlist tab", tab is not None)
        tab.click()
        entries = wait(lambda: d.find_elements(By.CSS_SELECTOR, "#ytd-format-popup .ytd-playlist-row"), 90)
        check("Playlist tab lists the playlist's videos", entries and len(entries) >= 3, f"{len(entries or [])} entries")
        d.find_element(By.CSS_SELECTOR, "#ytd-format-popup .ytd-tab[data-tab='video']").click()
        wait(lambda: d.find_elements(By.CSS_SELECTOR, "#ytd-format-popup .ytd-row"), 60)
        before = {j["id"] for j in jobs()}
        d.find_elements(By.CSS_SELECTOR, "#ytd-format-popup .ytd-row:not(.ytd-audio-row):not(.ytd-more-link)")[-1].click()
        found = new_jobs(before)
        url = found[0]["url"] if found else ""
        check("list= and index= are preserved in the URL sent to the app",
              "list=PL6B3937A5D230E335" in url and "index=" in url, url)
        cancel(found)

        # session dismiss
        d.find_element(By.CSS_SELECTOR, "#ytd-download-button .ytd-btn-x").click()
        time.sleep(0.5)
        gone = not d.find_elements(By.CSS_SELECTOR, "#ytd-download-button")
        d.refresh()
        time.sleep(5)
        check("× hides the button for the rest of the session", gone and not d.find_elements(By.CSS_SELECTOR, "#ytd-download-button"))

        # real right-click menus
        srv = link_page_server()
        d.get(f"http://127.0.0.1:{srv.server_address[1]}/")
        before = {j["id"] for j in jobs()}
        result = click_context_menu_item(d, d.find_element(By.ID, "dl"), "Download link with Master Download Manager")
        found = new_jobs(before)
        check("right-click → 'Download link with Master Download Manager'", found and found[0]["url"] == LINK_TARGET
              and found[0]["engine"] == "http", result)
        cancel(found)
        d.get(VIDEO)
        wait(lambda: d.find_element(By.CSS_SELECTOR, "#movie_player"), 45)
        before = {j["id"] for j in jobs()}
        title = wait(lambda: next((e for e in d.find_elements(By.CSS_SELECTOR, "ytd-watch-metadata #title h1, #title h1")
                                   if e.is_displayed()), None), 30)
        d.execute_script("arguments[0].scrollIntoView({block: 'center'})", title)
        time.sleep(0.5)
        result = click_context_menu_item(d, title, "Download this page with Master Download Manager")
        found = new_jobs(before, timeout=30)
        check("right-click → 'Download this page' sends the YouTube page (media engine)",
              found and found[0]["engine"] == "media" and "aqz-KE-bpKQ" in found[0]["url"], result)
        cancel(found)
        srv.shutdown()

        # connection diagnostics
        open_trusted(d, f"moz-extension://{UUID}/options.html")
        d.find_element(By.ID, "token").clear()
        d.find_element(By.ID, "token").send_keys("wrong-token")
        d.find_element(By.ID, "save").click()
        d.execute_async_script("const cb = arguments[arguments.length - 1];"
                               "browser.runtime.sendMessage({type: 'YTD_SET_SESSION_DISMISSED', value: false}).then(cb, cb);")
        time.sleep(0.5)
        d.get(VIDEO)
        wait(lambda: d.find_element(By.CSS_SELECTOR, "#movie_player #ytd-download-button"), 45).click()
        err = wait(lambda: d.find_element(By.CSS_SELECTOR, "#ytd-format-popup .ytd-error").text, 20)
        check("a wrong token is reported as a token mismatch", err and "token" in err.lower(), (err or "")[:80])
    finally:
        d.quit()

    failed = [r for r in RESULTS if not r[1]]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} Firefox checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
