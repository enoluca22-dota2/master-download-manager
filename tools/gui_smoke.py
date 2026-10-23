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

    # --- 4.0: batch paste with duplicate detection ---
    srv.files["a.zip"] = os.urandom(200_000)
    srv.files["b.iso"] = os.urandom(300_000)
    links = f"{srv.base}/files/a.zip\n{srv.base}/files/b.iso\n{srv.base}/files/a.zip\n{srv.base}/cd/ubuntu-test.iso"
    dlg = app.open_add_dialog(links, analyze=True)
    ok = wait(root, lambda: bool(dlg._batch), 10)
    check("batch mode lists unique links", ok and len(dlg._batch) == 3, f"{len(dlg._batch)}")
    pump(root, 2)
    shot(dlg.win, "05b-add-batch")
    check("batch flags an already-downloaded link", any(r["note"] for r in dlg._batch),
          str([r["note"] for r in dlg._batch]))
    before = len(app.manager.list_jobs())
    dlg.submit()
    pump(root, 0.5)
    new_jobs = app.manager.list_jobs()[before:]
    check("batch queues the ticked links", len(new_jobs) == 2, f"{len(new_jobs)}")
    ok = wait(root, lambda: all(j.status in (STATUS_COMPLETED, STATUS_FAILED) for j in new_jobs), 30)
    check("batch downloads complete", all(j.status == STATUS_COMPLETED for j in new_jobs),
          str([j.status for j in new_jobs]))

    # --- 4.0: queues, scheduled start, per-queue concurrency ---
    q = app.manager.queues.create("Night", max_concurrent=1)
    srv.files["n1.bin"] = os.urandom(150_000)
    srv.files["n2.bin"] = os.urandom(150_000)
    qjobs = [app.manager.add_job(url=f"{srv.base}/files/{n}", kind="file", quality_label="Direct file",
                                 format_key="file", download_dir=downloads, engine="http", queue_id=q.id)
             for n in ("n1.bin", "n2.bin")]
    app.sidebar.rebuild()
    app.sidebar.select(f"queue:{q.id}")
    pump(root, 1)
    check("custom queue waits until started", all(j.status == "Pending" for j in qjobs))
    shot(root, "08a-queue-view")
    app.start_current_queue()
    ok = wait(root, lambda: all(j.status == STATUS_COMPLETED for j in qjobs), 30)
    check("starting the queue downloads its items", ok, str([j.status for j in qjobs]))
    app.open_queues(q.id)
    pump(root, 1.2)
    qw = app._queues_window
    shot(qw.win, "08b-queues-window")
    for page in ("Speed profiles", "Recurring downloads"):
        qw.page.set(page)
        qw._show_page()
        pump(root, 0.6)
        shot(qw.win, f"08c-{page.replace(' ', '_')}")
    qw.win.destroy()

    # --- 4.0: completion action countdown can be cancelled ---
    from ytd.gui.power_dialog import PowerCountdown
    fired = []
    pc = PowerCountdown(app, "Night", "shutdown", on_confirm=lambda: fired.append(1), seconds=3)
    pump(root, 0.8)
    shot(pc.win, "08d-power-countdown")
    pc.cancel()
    pump(root, 3.5)
    check("power action cancelled never fires", not fired)

    # --- 4.0: sorting, column chooser, limited mode ---
    app.sidebar.select("all")
    pump(root, 0.5)
    app._sort_by("size")
    pump(root, 0.4)
    sizes = [j.total_bytes or 0 for j in app.grid.rows]
    check("sorting by size (largest first)", sizes == sorted(sizes, reverse=True), str(sizes[:6]))
    app._sort_by("size")
    app._sort_by("size")  # third click: back to queue order
    pump(root, 0.3)
    check("third header click restores queue order (drag enabled)", app.grid.can_reorder())
    for col in app.grid.columns:
        if col["key"] in ("connections", "source", "completed"):
            app.grid._toggle_column(col, True)
    pump(root, 0.4)
    shot(root, "08e-extra-columns")
    app.toggle_limited_mode()
    pump(root, 0.4)
    from ytd import bandwidth
    check("limited mode caps the global rate", bandwidth.manager.global_limiter.rate > 0)
    app.toggle_limited_mode()

    # --- views, settings ---
    pump(root, 1)
    shot(root, "08-main-with-jobs")
    for view in ("status:unfinished", "status:finished", "status:failed", "cat:Compressed", "cat:Torrents",
                 "cat:Media sites", "cat:Videos"):
        app.sidebar.select(view)
        pump(root, 0.3)
    app.sidebar.select("cat:Compressed")
    pump(root, 0.4)
    check("Compressed category shows the .zip/.iso downloads",
          len(app.grid.rows) >= 2 and all(j.job_id for j in app.grid.rows), f"{len(app.grid.rows)} rows")
    shot(root, "09-view-compressed")
    app.sidebar.select("all")

    # --- theme switching (live rebuild) ---
    for mode in ("Light", "Black", "Dark"):
        app.set_appearance_mode(mode)
        pump(root, 0.8)
        shot(root, f"09b-theme-{mode}")
    check("theme switch rebuilds the window", root.winfo_exists() and app.grid.winfo_exists())

    app.open_settings()
    pump(root, 1.5)
    sw = app._settings_window
    from ytd.gui.settings_window import SECTIONS
    for i, section in enumerate(SECTIONS):
        sw.show(section)
        pump(root, 0.6 if section == "About" else 0.3)
        shot(sw.win, f"10-settings-{i:02d}-{section.replace(' ', '_')}")
    sw.save()
    pump(root, 0.5)
    check("settings save without validation errors", "Saved" in sw.footer_note.cget("text") or "saved" in sw.footer_note.cget("text"),
          sw.footer_note.cget("text"))
    sw.win.destroy()

    rows = len(app.grid.rows)
    check("main list shows queued jobs", rows >= 3, f"{rows} rows")

    # --- scale: thousands of history rows stay responsive ---
    from ytd.models import DownloadJob
    fake = []
    for i in range(3000):
        j = DownloadJob(f"https://host.example/f{i}.zip", "file", "Direct file", "", False, downloads)
        j.status, j.title, j.from_history, j.total_bytes, j.progress = STATUS_COMPLETED, f"Archive {i}.zip", True, 1000 + i, 100.0
        j.result_path = os.path.join(downloads, f"Archive {i}.zip")
        fake.append(j)
    with app.manager._lock:
        app.manager._jobs.extend(fake)
    t0 = time.perf_counter()
    app._mark_structure()
    pump(root, 0.05)
    app._rebuild_list()
    root.update()
    rebuild_ms = (time.perf_counter() - t0) * 1000
    t0 = time.perf_counter()
    for _ in range(40):
        app.grid._yoff += app.grid.row_h * 5
        app.grid._draw()
    scroll_ms = (time.perf_counter() - t0) * 1000 / 40
    check("3,000-row list rebuild under 1.5 s", rebuild_ms < 1500, f"{rebuild_ms:.0f} ms")
    check("scroll redraw under 40 ms per frame", scroll_ms < 40, f"{scroll_ms:.1f} ms/frame")
    print(f"[perf] rows={len(app.grid.rows)} rebuild={rebuild_ms:.0f}ms scroll_frame={scroll_ms:.1f}ms")
    shot(root, "11-many-rows")
    app.quit_app()
    srv.__exit__(None, None, None)
    del seeder
    failed = [r for r in RESULTS if not r[1]]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} GUI checks passed. Screenshots: {args.out}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
