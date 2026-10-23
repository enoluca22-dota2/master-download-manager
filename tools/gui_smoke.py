"""Drives the real Master Download Manager window in-process and screenshots
each step. Offline parts use a local HTTP server and a local torrent seeder;
pass --online to also analyze/download a real (Creative Commons) YouTube video.

    .venv\\Scripts\\python tools/gui_smoke.py [--online] [--out DIR]

Exit code 0 = every check passed.
"""

import argparse
import ctypes
import ctypes.wintypes as wt
import os
import sys
import tempfile
import time
import warnings

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

parser = argparse.ArgumentParser()
parser.add_argument("--online", action="store_true")
parser.add_argument("--out", default=os.path.join(tempfile.gettempdir(), "mdm-gui-smoke"))
args = parser.parse_args()
os.makedirs(args.out, exist_ok=True)
os.environ["MDM_DATA_DIR"] = tempfile.mkdtemp(prefix="mdm-gui-data-")

import libtorrent as lt  # noqa: E402  (before win11toast, see master_download_manager.py)
import customtkinter as ctk  # noqa: E402
from PIL import ImageGrab  # noqa: E402

from local_http_server import LocalHTTPServer  # noqa: E402
from ytd.logging_setup import setup_logging  # noqa: E402

setup_logging()
from ytd.gui.app import MasterDownloadManagerApp  # noqa: E402
from ytd.models import STATUS_COMPLETED, STATUS_FAILED  # noqa: E402

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")


def pump(root, seconds):
    end = time.time() + seconds
    while time.time() < end:
        root.update()
        time.sleep(0.02)


def wait(root, predicate, timeout):
    end = time.time() + timeout
    while time.time() < end:
        root.update()
        if predicate():
            return True
        time.sleep(0.03)
    return False


def shot(win, name):
    win.lift()
    win.update()
    time.sleep(0.3)
    win.update()
    hwnd = ctypes.windll.user32.GetParent(win.winfo_id()) or win.winfo_id()
    rect = wt.RECT()
    ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
    path = os.path.join(args.out, f"{name}.png")
    ImageGrab.grab((rect.left, rect.top, rect.right, rect.bottom), all_screens=True).save(path)
    return path


def make_seeder(folder):
    os.makedirs(folder, exist_ok=True)
    files = {"Episode 1.mkv": os.urandom(900_000), "Episode 2.mkv": os.urandom(700_000), "notes.txt": b"hello" * 100}
    root = os.path.join(folder, "Sample Series")
    os.makedirs(root, exist_ok=True)
    for name, data in files.items():
        with open(os.path.join(root, name), "wb") as f:
            f.write(data)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        fs = lt.file_storage()
        lt.add_files(fs, root)
        ct = lt.create_torrent(fs, 32768)
        lt.set_piece_hashes(ct, folder)
    ti = lt.torrent_info(lt.bencode(ct.generate()))
    ses = lt.session({"listen_interfaces": "127.0.0.1:0", "enable_dht": False, "enable_lsd": False})
    ses.add_torrent({"ti": ti, "save_path": folder})
    time.sleep(1.5)
    magnet = f"magnet:?xt=urn:btih:{ti.info_hashes().v1}&dn=Sample%20Series&x.pe=127.0.0.1:{ses.listen_port()}"
    return ses, magnet


def main():
    downloads = tempfile.mkdtemp(prefix="mdm-gui-downloads-")
    srv = LocalHTTPServer().__enter__()
    srv.files["ubuntu-test.iso"] = os.urandom(3_000_000)
    seeder, magnet = make_seeder(tempfile.mkdtemp(prefix="mdm-gui-seed-"))

    root = ctk.CTk()
    app = MasterDownloadManagerApp(root)
    app.settings.set("download_dir", downloads)
    root.geometry("1240x760+40+40")
    pump(root, 2)
    check("main window opens", root.winfo_exists())
    shot(root, "01-main-empty")

    # --- direct HTTP file ---
    dlg = app.open_add_dialog(f"{srv.base}/cd/ubuntu-test.iso", analyze=True)
    ok = wait(root, lambda: str(dlg.download_btn.cget("state")) == "normal", 20)
    check("HTTP link analyzed", ok and dlg.engine == "http", f"engine={dlg.engine}")
    shot(dlg.win, "02-add-http")
    dlg.submit()
    job = app.manager.list_jobs()[-1]
    ok = wait(root, lambda: job.status in (STATUS_COMPLETED, STATUS_FAILED), 30)
    check("HTTP download completes from the dialog", job.status == STATUS_COMPLETED,
          f"{job.status} {job.result_path}")
    check("HTTP file is byte-exact", job.result_path and open(job.result_path, "rb").read() == srv.files["ubuntu-test.iso"])

    # --- magnet (offline seeder) with partial file selection ---
    dlg = app.open_add_dialog(magnet, analyze=True)
    ok = wait(root, lambda: str(dlg.download_btn.cget("state")) == "normal", 90)
    check("magnet analyzed (metadata from peer)", ok and dlg.engine == "torrent", f"engine={dlg.engine}")
    if ok:
        files = dlg.t_list.tree.get_children()
        check("torrent file list shows 3 real files", len(files) == 3, f"{len(files)}")
        notes = next(i for i in files if dlg.t_list.tree.set(i, "name") == "notes.txt")
        dlg.t_list._set(notes, False)
        dlg._refresh_t_count()
        shot(dlg.win, "03-add-torrent")
        dlg.submit()
        tjob = app.manager.list_jobs()[-1]
        ok = wait(root, lambda: tjob.status in (STATUS_COMPLETED, STATUS_FAILED), 120)
        check("torrent completes from the dialog", tjob.status == STATUS_COMPLETED, f"{tjob.status} {tjob.error_message}")
        if tjob.result_path:
            present = sorted(os.listdir(tjob.result_path))
            check("deselected torrent file was not downloaded", "notes.txt" not in present or
                  os.path.getsize(os.path.join(tjob.result_path, "notes.txt")) == 0, f"{present}")

    # --- failure shows reason + hint in details ---
    app.settings.set("max_retries", 0)
    dlg = app.open_add_dialog(f"{srv.base}/files/ubuntu-test.iso", analyze=True)
    wait(root, lambda: str(dlg.download_btn.cget("state")) == "normal", 20)
    srv.files.pop("ubuntu-test.iso")  # vanish before download -> 404
    dlg.submit()
    fjob = app.manager.list_jobs()[-1]
    wait(root, lambda: fjob.status in (STATUS_COMPLETED, STATUS_FAILED), 20)
    check("failed download has a readable reason and suggestion", fjob.status == STATUS_FAILED and fjob.error_message
          and fjob.error_hint, f"{fjob.error_message} / {fjob.error_hint}")
    app.open_details(fjob.job_id)
    pump(root, 1)
    details = app.details_windows[fjob.job_id]
    details._toggle_technical()
    pump(root, 0.5)
    shot(details.win, "04-details-failed")
    details.win.destroy()

    # --- online media ---
    if args.online:
        dlg = app.open_add_dialog("https://www.youtube.com/watch?v=aqz-KE-bpKQ", analyze=True)
        ok = wait(root, lambda: str(dlg.download_btn.cget("state")) == "normal", 90)
        check("YouTube video analyzed", ok and dlg.engine == "media", dlg.status_label.cget("text"))
        if ok:
            pump(root, 1)
            shot(dlg.win, "05-add-media-video")
            dlg.mode_var.set("Audio only")
            dlg._render_mode()
            pump(root, 0.5)
            shot(dlg.win, "06-add-media-audio")
            dlg.win.destroy()
        dlg = app.open_add_dialog("https://www.youtube.com/playlist?list=PL6B3937A5D230E335", analyze=True)
        ok = wait(root, lambda: str(dlg.download_btn.cget("state")) == "normal" or "rror" in dlg.status_label.cget("text"), 90)
        check("YouTube playlist analyzed", ok and getattr(dlg.media, "is_playlist", False), dlg.status_label.cget("text"))
        if ok:
            pump(root, 1)
            shot(dlg.win, "07-add-playlist")
        dlg.win.destroy()

    # --- views, settings ---
    pump(root, 1)
    shot(root, "08-main-with-jobs")
    for view in ("Active", "Completed", "Failed", "Archives", "Torrents", "History"):
        app.set_view(view)
        pump(root, 0.3)
    shot(root, "09-view-history")
    app.set_view("All")
    app.open_settings()
    pump(root, 1.5)
    sw = app._settings_window
    for i, section in enumerate(("General", "Downloads", "Network", "Torrent", "Browser Integration", "Advanced", "About", "Licenses")):
        sw.show(section)
        pump(root, 0.6 if section == "About" else 0.3)
        shot(sw.win, f"10-settings-{i:02d}-{section.replace(' ', '_')}")
    sw.save()
    pump(root, 0.5)
    check("settings save without validation errors", "Saved" in sw.footer_note.cget("text") or "saved" in sw.footer_note.cget("text"),
          sw.footer_note.cget("text"))
    sw.win.destroy()

    rows = len(app.tree.get_children())
    check("main list shows queued jobs", rows >= 3, f"{rows} rows")
    app.quit_app()
    srv.__exit__(None, None, None)
    del seeder
    failed = [r for r in RESULTS if not r[1]]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} GUI checks passed. Screenshots: {args.out}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
