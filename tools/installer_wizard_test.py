"""Drives the interactive Setup wizard of the isolated test installer page by
page (screenshots), then the interactive uninstaller, answering "Yes" to
"remove your data" and checking that only MDM's own files go.

    .venv\\Scripts\\python tools/installer_wizard_test.py build\\installer-test\\MDM-InstallTest-Setup.exe [--out DIR]

The test variant has its own name, AppId, mutex and data folder
(MDMInstallTestData), so a real installation is never touched.
"""

import argparse
import ctypes
import ctypes.wintypes as wt
import os
import shutil
import subprocess
import sys
import tempfile
import time

from PIL import ImageGrab

user32 = ctypes.windll.user32
WM_GETTEXT, WM_GETTEXTLENGTH, BM_CLICK, BM_GETCHECK = 0x000D, 0x000E, 0x00F5, 0x00F0
NAME = "MDM Install Test"
DATA = os.path.join(os.environ["LOCALAPPDATA"], "MDMInstallTestData")
RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok)))
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")


def text(hwnd):
    n = user32.SendMessageW(hwnd, WM_GETTEXTLENGTH, 0, 0)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.SendMessageW(hwnd, WM_GETTEXT, n + 1, buf)
    return buf.value


def top_windows(pid=None):
    found = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            found.append(hwnd)
        return True
    user32.EnumWindows(cb, 0)
    return found


def children(hwnd):
    found = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(h, _):
        found.append(h)
        return True
    user32.EnumChildWindows(hwnd, cb, 0)
    return found


def find_window(predicate, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        for h in top_windows():
            r = wt.RECT()
            user32.GetWindowRect(h, ctypes.byref(r))
            if predicate(text(h)) and r.right - r.left > 150 and children(h):  # skip Inno's hidden helper window
                return h
        time.sleep(0.3)
    return None


def visible_texts(hwnd):
    return [text(c) for c in children(hwnd) if user32.IsWindowVisible(c) and text(c)]


def button(hwnd, *captions):
    for c in children(hwnd):
        if user32.IsWindowVisible(c) and user32.IsWindowEnabled(c) and text(c).replace("&", "") in captions:
            return c
    return None


def click(hwnd, *captions):
    b = button(hwnd, *captions)
    if not b:
        raise RuntimeError(f"no button {captions} in {visible_texts(hwnd)[:12]}")
    user32.PostMessageW(b, BM_CLICK, 0, 0)
    time.sleep(1.2)


def shot(hwnd, out, name):
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.5)
    r = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    ImageGrab.grab((r.left, r.top, r.right, r.bottom), all_screens=True).save(os.path.join(out, name + ".png"))


def desktop_dir():
    buf = ctypes.create_unicode_buffer(260)
    ctypes.windll.shell32.SHGetFolderPathW(None, 0x10, None, 0, buf)  # CSIDL_DESKTOPDIRECTORY
    return buf.value


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("setup")
    ap.add_argument("--out", default=os.path.join(tempfile.gettempdir(), "mdm-wizard"))
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    work = tempfile.mkdtemp(prefix="mdm-wizard-")
    target = os.path.join(work, "app")
    env = dict(os.environ, MDM_DATA_DIR=os.path.join(work, "data"))  # the "Run" checkbox starts an isolated copy
    proc = subprocess.Popen([os.path.abspath(a.setup), f"/DIR={target}"], env=env)
    try:
        w = find_window(lambda t: t.startswith("Setup - " + NAME) or t == "Select Setup Install Mode")
        check("wizard opens directly (no install-mode question)", w and text(w).startswith("Setup - "), text(w or 0))
        texts = " ".join(visible_texts(w))
        check("Welcome page", "Welcome to" in texts and "Setup" in texts, texts[:90])
        shot(w, a.out, "w1-welcome")
        click(w, "Next", "Next >")
        texts = " ".join(visible_texts(w))
        check("License Agreement page (information, no forced acceptance)", "License" in texts
              and "I accept" not in texts and "All rights reserved" in texts.replace("all rights", "All rights"),
              texts[:120])
        shot(w, a.out, "w2-license")
        click(w, "Next", "Next >")
        texts = " ".join(visible_texts(w))
        check("Choose Components page with space required", "Components" in texts and "MB" in texts, texts[:120])
        shot(w, a.out, "w3-components")
        click(w, "Next", "Next >")
        texts = " ".join(visible_texts(w))
        if "Destination" in texts or "Location" in texts or "install" in texts.lower() and target.lower() in texts.lower():
            check("Install Location page (new install)", target.lower() in texts.lower(), texts[:120])
            shot(w, a.out, "w4-location")
            check("Location page is the last one (Install button)", button(w, "Install") is not None)
            click(w, "Install")
        texts = " ".join(visible_texts(w))
        if button(w, "Install"):
            shot(w, a.out, "w5-ready")
            click(w, "Install")
        time.sleep(0.8)
        details = button(w, "Show details")
        if details:
            user32.PostMessageW(details, BM_CLICK, 0, 0)
            time.sleep(0.6)
            shot(w, a.out, "w6-installing-details")
        check("Installing page has Show details", details is not None)
        end = time.time() + 120
        while time.time() < end and not button(w, "Finish"):
            time.sleep(0.5)
        texts = " ".join(visible_texts(w))
        check("Completing page", button(w, "Finish") is not None, texts[:140])
        shot(w, a.out, "w7-finish")
        click(w, "Finish")
        proc.wait(30)
        check("Setup exits 0", proc.returncode == 0, str(proc.returncode))
        exe = os.path.join(target, "MasterDownloadManager.exe")
        time.sleep(4)
        running = subprocess.run(["powershell", "-NoProfile", "-Command",
                                  f"(Get-Process MasterDownloadManager -ErrorAction SilentlyContinue | "
                                  f"Where-Object {{ $_.Path -eq '{exe}' }}).Id"], capture_output=True, text=True)
        check("'Run Master Download Manager' (ticked by default) started the app", running.stdout.strip())
        for pid in running.stdout.split():
            subprocess.run(["taskkill", "/PID", pid, "/F"], capture_output=True)
        time.sleep(1)

        # --- interactive uninstall: answer Yes to removing data ---
        shutil.rmtree(DATA, ignore_errors=True)
        os.makedirs(os.path.join(DATA, "logs"))
        os.makedirs(os.path.join(DATA, "updates", "backup-x"))
        os.makedirs(os.path.join(DATA, "my downloads"))
        for rel in ("settings.json", "queue.json", "download_history.sqlite3", "settings.json.corrupt-20260101",
                    "logs/app.log", "updates/backup-x/settings.json", "my downloads/movie.mkv", "notes.txt"):
            with open(os.path.join(DATA, rel), "w", encoding="utf-8") as f:
                f.write("x")
        unins = subprocess.Popen([os.path.join(target, "unins000.exe")])
        q = find_window(lambda t: t.endswith("Uninstall") or "Uninstall" in t, 30)
        if q and button(q, "Yes"):
            shot(q, a.out, "u1-confirm")
            click(q, "Yes")
        prompt = None
        end = time.time() + 90
        while time.time() < end and not prompt:
            for h in top_windows():
                if any("remove your Master Download Manager settings" in t for t in visible_texts(h)) or \
                        "remove your Master Download Manager settings" in text(h):
                    prompt = h
            time.sleep(0.4)
        check("uninstaller asks about user data", prompt)
        if prompt:
            shot(prompt, a.out, "u2-data-prompt")
            click(prompt, "Yes")
        done = find_window(lambda t: "Uninstall" in t, 60)
        if done and button(done, "OK"):
            shot(done, a.out, "u3-done")
            click(done, "OK")
        unins.wait(60)
        time.sleep(3)
        check("MDM's own data removed", not any(os.path.exists(os.path.join(DATA, r)) for r in
                                                ("settings.json", "queue.json", "download_history.sqlite3",
                                                 "settings.json.corrupt-20260101", "logs", "updates")))
        check("other files in the data folder kept (downloads, user files)",
              os.path.isfile(os.path.join(DATA, "my downloads", "movie.mkv"))
              and os.path.isfile(os.path.join(DATA, "notes.txt")))
        check("program removed", not os.path.exists(os.path.join(target, "MasterDownloadManager.exe")))
    finally:
        if proc.poll() is None:
            proc.kill()
        # the wizard itself runs in a child "<name>.tmp" process
        subprocess.run(["powershell", "-NoProfile", "-Command", "Get-Process | Where-Object { $_.ProcessName -eq "
                        f"'{os.path.splitext(os.path.basename(a.setup))[0]}.tmp' }} | Stop-Process -Force"],
                       capture_output=True)
        shutil.rmtree(DATA, ignore_errors=True)
        for lnk in (os.path.join(os.environ["APPDATA"], r"Microsoft\Windows\Start Menu\Programs", NAME + ".lnk"),
                    os.path.join(desktop_dir(), NAME + ".lnk")):
            if os.path.exists(lnk):
                os.remove(lnk)
    failed = [r for r in RESULTS if not r[1]]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} wizard checks passed. Screenshots: {a.out}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
