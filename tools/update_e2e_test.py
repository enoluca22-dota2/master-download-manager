"""One-click update, end to end, with real installers and a real installed app
(isolated test variant, local fake GitHub, throw-away signing key):

  install OLD silently -> run it, download a file -> close it -> start it with
  --update-now --yes -> it checks the fake GitHub, verifies the signed manifest,
  downloads NEW, backs up, closes, the installer upgrades in place and starts
  NEW -> checks version, data, settings, Installed Apps entry, result file.

    .venv\\Scripts\\python tools/update_e2e_test.py OLD_SETUP OLD_VERSION NEW_SETUP NEW_VERSION [FAIL_SETUP FAIL_VERSION]

FAIL_SETUP: a test installer built with /DUpdateFailTest=1 (it fails before
installing): the running version must come back and report the failure.

Build the two test-variant installers as described in tools/installer_test.ps1
(same AppId/name/mutex/DataFolder, different /DAppVersion).
"""

import json
import os
import secrets
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import winreg
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import requests
from Cryptodome.PublicKey import ECC

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
from test_update41 import FakeGitHub, REPO  # noqa: E402

UNINSTALL = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\{0D1B6E54-7F0A-4C55-9E7B-3C6A2E9D1F00}_is1"
PORT = 47951
RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok)))
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}", flush=True)


def reg_version():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL) as k:
            return winreg.QueryValueEx(k, "DisplayVersion")[0]
    except OSError:
        return None


def api_version(timeout=2):
    try:
        return requests.get(f"http://127.0.0.1:{PORT}/version", timeout=timeout).json().get("version")
    except (requests.RequestException, ValueError):
        return None


def wait_for(predicate, timeout, step=0.5):
    end = time.time() + timeout
    while time.time() < end:
        value = predicate()
        if value:
            return value
        time.sleep(step)
    return None


def app_pids(exe):
    out = subprocess.run(["powershell", "-NoProfile", "-Command",
                          f"(Get-Process MasterDownloadManager -ErrorAction SilentlyContinue | "
                          f"Where-Object {{ $_.Path -eq '{exe}' }}).Id"], capture_output=True, text=True).stdout
    return [int(p) for p in out.split()]


def close_app(pid):
    """Click the window's close button (WM_CLOSE to its visible main window)."""
    import ctypes
    import ctypes.wintypes as wt
    user32 = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(hwnd, _):
        owner = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        n = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        if owner.value == pid and user32.IsWindowVisible(hwnd) and buf.value == "Master Download Manager":
            found.append(hwnd)
        return True
    user32.EnumWindows(cb, 0)
    for hwnd in found:
        user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE
    return bool(found)


def main():
    old_setup, old_ver, new_setup, new_ver = sys.argv[1:5]
    old_setup, new_setup = os.path.abspath(old_setup), os.path.abspath(new_setup)
    fail_setup, fail_ver = (os.path.abspath(sys.argv[5]), sys.argv[6]) if len(sys.argv) > 6 else (None, None)
    if reg_version():
        sys.exit("A test installation already exists; uninstall it first.")
    work = tempfile.mkdtemp(prefix="mdm-update-e2e-")
    app_dir, data, downloads = (os.path.join(work, n) for n in ("app", "data", "downloads"))
    os.makedirs(data)
    os.makedirs(downloads)
    exe = os.path.join(app_dir, "MasterDownloadManager.exe")
    token = secrets.token_hex(16)
    with open(os.path.join(data, "settings.json"), "w", encoding="utf-8") as f:
        json.dump({"api_port": PORT, "api_token": token, "minimize_to_tray": False, "download_dir": downloads,
                   "update_auto_check": False, "max_concurrent_downloads": 5, "accent_color": "Teal"}, f)
    key = ECC.generate(curve="ed25519")
    pub = os.path.join(work, "test-release-key.pem")
    with open(pub, "w", encoding="ascii") as f:
        f.write(key.public_key().export_key(format="PEM"))
    gh = FakeGitHub()
    with open(old_setup, "rb") as f:
        gh.add_release(old_ver, key, installer=f.read())
    with open(new_setup, "rb") as f:
        new_bytes = f.read()
    gh.add_release(new_ver, key, installer=new_bytes)
    env = dict(os.environ, MDM_DATA_DIR=data, MDM_UPDATE_API=gh.base, MDM_UPDATE_REPO=REPO, MDM_UPDATE_PUBKEY_FILE=pub)

    srv_dir = os.path.join(work, "srv")
    os.makedirs(srv_dir)
    payload = os.urandom(3_000_000)
    with open(os.path.join(srv_dir, "payload.bin"), "wb") as f:
        f.write(payload)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), lambda *a: SimpleHTTPRequestHandler(*a, directory=srv_dir))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        # ------------------------------------------------------------ install OLD
        code = subprocess.run([old_setup, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-",
                               f"/DIR={app_dir}", '/COMPONENTS=app,startmenu']).returncode
        check(f"{old_ver} installs", code == 0 and reg_version() == old_ver, f"exit {code}, registry {reg_version()}")

        # --------------------------------------------- use it: a real download
        subprocess.Popen([exe], env=env)
        check(f"{old_ver} runs", wait_for(api_version, 40) == old_ver)
        h = {"X-MDM-Token": token}
        job = requests.post(f"http://127.0.0.1:{PORT}/downloads", headers=h, timeout=10,
                            json={"url": f"http://127.0.0.1:{httpd.server_address[1]}/payload.bin",
                                  "format_id": "file", "engine": "http"}).json()
        st = wait_for(lambda: (lambda s: s if s.get("status") in ("Completed", "Failed") else None)(
            requests.get(f"http://127.0.0.1:{PORT}/downloads/{job['id']}", headers=h, timeout=5).json()), 60)
        check("download before the update", st and st["status"] == "Completed", str(st and st.get("status")))
        close_app(app_pids(exe)[0])  # a normal exit, like clicking the close button
        if not wait_for(lambda: not app_pids(exe), 30):
            sys.exit(f"{old_ver} didn't close; aborting (the rest would be meaningless)")
        check(f"{old_ver} closes", True)
        db = os.path.join(data, "download_history.sqlite3")
        con = sqlite3.connect(db)
        tables = [r[0] for r in con.execute("select name from sqlite_master where type='table'")]
        hist_table = next(t for t in tables if "hist" in t.lower() or "download" in t.lower())
        before_rows = con.execute(f"select count(*) from {hist_table}").fetchone()[0]
        con.close()

        # ------------------------------------------------- one-click update
        t0 = time.time()
        old = subprocess.Popen([exe, "--update-now", "--yes"], env=env)
        check(f"{old_ver} starts and asks GitHub", wait_for(api_version, 40) == old_ver)
        exited = wait_for(lambda: old.poll() is not None, 180)
        check(f"{old_ver} exits to let Setup run", exited, f"{time.time() - t0:.0f} s")
        new_running = wait_for(lambda: api_version() == new_ver, 240)
        check(f"{new_ver} is running after the update", new_running, f"{time.time() - t0:.0f} s total")
        check(f"Installed Apps entry says {new_ver}", reg_version() == new_ver, str(reg_version()))
        result_file = os.path.join(data, "updates", "last-update.json")
        r = wait_for(lambda: os.path.isfile(result_file) and json.load(open(result_file, encoding="utf-8")), 20)
        check("post-update check recorded success", r and r.get("ok") and r.get("from") == old_ver
              and r.get("to") == new_ver, str(r and r.get("message")))
        check("installer log written", r and os.path.isfile(r.get("log", "")), str(r and r.get("log")))
        check("used installer removed", not any(n.endswith(".exe") for _, _, fs in
                                                os.walk(os.path.join(data, "updates")) for n in fs))
        backups = [d for d in os.listdir(os.path.join(data, "updates")) if d.startswith("backup-")]
        check("state backed up before installing", backups, str(backups))
        with open(os.path.join(data, "settings.json"), encoding="utf-8") as f:
            s = json.load(f)
        check("settings kept (token, custom values)", s.get("api_token") == token
              and s.get("max_concurrent_downloads") == 5 and s.get("accent_color") == "Teal")
        resp = requests.get(f"http://127.0.0.1:{PORT}/downloads/{job['id']}", headers=h, timeout=5)
        check("browser pairing still works and the earlier download is still listed", resp.status_code == 200,
              f"HTTP {resp.status_code}")
        con = sqlite3.connect(db)
        after_rows = con.execute(f"select count(*) from {hist_table}").fetchone()[0]
        con.close()
        check("download history kept", after_rows >= before_rows and before_rows > 0, f"{before_rows} -> {after_rows}")
        check("downloaded file untouched", open(st["result_path"], "rb").read() == payload)
        check("program folder unchanged", os.path.isfile(exe) and os.path.dirname(exe) == app_dir)

        # ---------------------------------------------- nothing newer: no loop
        for pid in app_pids(exe):
            close_app(pid)
        check(f"{new_ver} closes normally", wait_for(lambda: not app_pids(exe), 30))
        again = subprocess.Popen([exe, "--update-now", "--yes"], env=env)
        wait_for(api_version, 40)
        time.sleep(8)
        check("already up to date: no second update", again.poll() is None and api_version() == new_ver
              and reg_version() == new_ver)

        if fail_setup:
            # ------------------------- a failing update: the running version must come back
            for pid in app_pids(exe):
                close_app(pid)
            wait_for(lambda: not app_pids(exe), 30)
            os.remove(result_file)
            with open(fail_setup, "rb") as f:
                gh.add_release(fail_ver, key, installer=f.read())
            t0 = time.time()
            subprocess.Popen([exe, "--update-now", "--yes"], env=env)
            back = wait_for(lambda: os.path.isfile(result_file) and json.load(open(result_file, encoding="utf-8")),
                            240)
            check("failed update: previous version restarted and reported it", back and not back.get("ok")
                  and back.get("to") == fail_ver and back.get("running") == new_ver,
                  f"{time.time() - t0:.0f} s: {back and back.get('message')}")
            check("failed update: still the previous version, same Installed Apps entry",
                  wait_for(lambda: api_version() == new_ver, 30) and reg_version() == new_ver, str(reg_version()))
            with open(os.path.join(data, "settings.json"), encoding="utf-8") as f:
                check("failed update: settings unchanged", json.load(f).get("api_token") == token)
    finally:
        for pid in app_pids(exe):
            subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
        time.sleep(1)
        unins = os.path.join(app_dir, "unins000.exe")
        if os.path.isfile(unins):
            subprocess.run([unins, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"])
            time.sleep(3)
        gh.close()
        httpd.shutdown()
        shutil.rmtree(work, ignore_errors=True)
    failed = [r for r in RESULTS if not r[1]]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} update end-to-end checks passed.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
