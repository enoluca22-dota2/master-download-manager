"""4.1 GUI checks: the menu bar (every entry wired or disabled), About,
Licenses, Tools dialogs, documentation viewer and the update dialog against a
local fake GitHub (signed test release). Screenshots each step.

    .venv\\Scripts\\python tools/gui_smoke41.py [--out DIR]

Exit code 0 = every check passed.
"""

import argparse
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

parser = argparse.ArgumentParser()
parser.add_argument("--out", default=os.path.join(tempfile.gettempdir(), "mdm-gui-smoke41"))
args = parser.parse_args()
os.makedirs(args.out, exist_ok=True)
os.environ["MDM_DATA_DIR"] = tempfile.mkdtemp(prefix="mdm-gui41-data-")

import libtorrent  # noqa: E402,F401  (before win11toast, see master_download_manager.py)
import customtkinter as ctk  # noqa: E402
import tkinter as tk  # noqa: E402
from Cryptodome.PublicKey import ECC  # noqa: E402

import ctypes  # noqa: E402
import ctypes.wintypes as wt  # noqa: E402
from PIL import ImageGrab  # noqa: E402

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
    win.attributes("-topmost", True)  # Windows may refuse to raise us over the user's foreground app
    win.lift()
    win.update()
    time.sleep(0.3)
    win.update()
    hwnd = ctypes.windll.user32.GetParent(win.winfo_id()) or win.winfo_id()
    rect = wt.RECT()
    ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
    ImageGrab.grab((rect.left, rect.top, rect.right, rect.bottom), all_screens=True).save(
        os.path.join(args.out, f"{name}.png"))
    win.attributes("-topmost", False)


import webbrowser  # noqa: E402

OPENED = []
webbrowser.open = lambda url, *a, **k: OPENED.append(url) or True  # never open the user's browser
from test_update41 import FakeGitHub, REPO  # noqa: E402

KEY = ECC.generate(curve="ed25519")
PUB = os.path.join(os.environ["MDM_DATA_DIR"], "test-pub.pem")
with open(PUB, "w", encoding="ascii") as f:
    f.write(KEY.public_key().export_key(format="PEM"))
with open(os.path.join(os.environ["MDM_DATA_DIR"], "settings.json"), "w", encoding="utf-8") as f:
    f.write('{"api_port": 47941}')  # never collide with a running MDM
GH = FakeGitHub()
os.environ.update({"MDM_UPDATE_API": GH.base, "MDM_UPDATE_REPO": REPO, "MDM_UPDATE_PUBKEY_FILE": PUB})

from ytd import __version__  # noqa: E402
from ytd.logging_setup import setup_logging  # noqa: E402

setup_logging()
from ytd.gui.app import MasterDownloadManagerApp  # noqa: E402

SAFE_TO_INVOKE = {"Add Multiple URLs…", "Download History…", "Check File Integrity…", "View Application Logs",
                  "View Open-Source Licenses…", "Third-Party Notices…", "Source Code Information…", "What's New",
                  "Changelog", "About Master Download Manager", "User Guide", "Installation Guide",
                  "Browser Integration Guide", "Troubleshooting", "Queue Manager…", "Download Scheduler…"}


def entries(menu, path=""):
    """(path, label, type, state, has_command, submenu) for every entry, recursively."""
    out = []
    end = menu.index("end")
    for i in range(0 if end is None else end + 1):
        kind = menu.type(i)
        if kind == "separator":
            continue
        label = menu.entrycget(i, "label")
        state = menu.entrycget(i, "state")
        if kind == "cascade":
            sub = menu.nametowidget(menu.entrycget(i, "menu"))
            out.append((path + label, label, kind, state, True, sub))
            out += entries(sub, path + label + " > ")
        else:
            out.append((path + label, label, kind, state, bool(str(menu.entrycget(i, "command"))), None))
    return out


def new_toplevels(root, before):
    return [w for w in root.winfo_children() if isinstance(w, tk.Toplevel) and w not in before]


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    root = ctk.CTk()
    app = MasterDownloadManagerApp(root)
    root.geometry("1240x760+40+40")
    pump(root, 2)
    check("menu bar shows File/Tasks/Tools/Help", list(app.menubar.labels) == ["file", "tasks", "tools", "help"])
    check("status bar shows the central version", app.status_version.cget("text") == f"v{__version__}")
    shot(root, "41-01-main-menubar")

    # --- every menu entry is wired, or disabled on purpose ---
    total, dead, invoked = 0, [], 0
    for key in ("file", "tasks", "tools", "help"):
        for path, label, kind, state, wired, _ in entries(app.menubar.build(key)):
            total += 1
            is_header = state == "disabled" and label.isupper()
            if not wired and not is_header and state != "disabled":
                dead.append(path)
            if label in SAFE_TO_INVOKE and state != "disabled":
                before = set(root.winfo_children())
                menu = app.menubar.build(key)
                idx = next(i for i in range(menu.index("end") + 1)
                           if menu.type(i) != "separator" and menu.entrycget(i, "label") == label)
                menu.invoke(idx)
                pump(root, 0.6)
                opened = new_toplevels(root, before)
                check(f"menu '{label}' opens a window", opened, f"{len(opened)}")
                if opened and label in ("About Master Download Manager", "View Open-Source Licenses…",
                                        "User Guide", "Check File Integrity…", "Download History…"):
                    shot(opened[0], f"41-menu-{label.strip('…').replace(' ', '_')}")
                for w in opened:
                    w.destroy()
                invoked += 1
    check("no menu entry without an action", not dead, f"{total} entries, unwired: {dead}")
    print(f"[menus] {total} entries, {invoked} invoked")

    # --- About ---
    app.open_about()
    pump(root, 0.8)
    about = app._about
    texts = [w.cget("text") for w in _walk(about.win) if isinstance(w, (ctk.CTkLabel, tk.Label))]
    check("About shows the version and copyright", any(__version__ in t for t in texts) and
          any("2026" in t for t in texts))
    shot(about.win, "41-02-about")
    about.win.destroy()

    # --- Licenses: search, sort, details ---
    app.open_licenses()
    pump(root, 0.8)
    lw = app._licenses
    n = len(lw.items)
    check("licenses window lists the inventory", n >= 50, f"{n} components")
    lw.search.set("mit")
    pump(root, 0.3)
    mit = lw.visible()
    check("license search filters", 0 < len(mit) < n and all("mit" in " ".join(str(c.get(k, "")) for k in
                                                                               ("name", "author", "license",
                                                                                "version")).lower() for c in mit))
    lw.search.set("")
    lw._sort("version")
    lw._sort("version")
    check("license sort toggles direction", lw.sort == ("version", True))
    shot(lw.win, "41-03-licenses")
    from ytd.gui.licenses_window import license_text
    empty = [c["name"] for c in lw.items if not license_text(c).strip()]
    check("every component has a license text", not empty, str(empty))
    before = set(root.winfo_children())
    lw.open_details(next(c for c in lw.items if c.get("name") == "yt-dlp"))
    pump(root, 0.5)
    det = new_toplevels(root, before)
    check("license details window opens", det)
    if det:
        shot(det[0], "41-04-license-details")
        det[0].destroy()
    lw.win.destroy()

    # --- docs bundled / readable ---
    for doc in ("USER_GUIDE.md", "INSTALLATION.md", "BROWSER_INTEGRATION.md", "TROUBLESHOOTING.md", "UPDATING.md",
                "LICENSING.md", "CHANGELOG.md"):
        check(f"doc {doc} readable", len(app.read_doc(doc)) > 200)
    check("What's New has the 4.1.0 section", app.whats_new_text().startswith(f"## {__version__}"))

    # --- update dialog: up to date, then a signed newer release ---
    GH.add_release(__version__, KEY)
    dlg = app.check_for_updates()
    wait(root, lambda: "up to date" in dlg.title.cget("text"), 15)
    check("update check: up to date", "up to date" in dlg.title.cget("text"), dlg.title.cget("text"))
    shot(dlg.win, "41-05-update-uptodate")
    dlg.close()
    GH.add_release("9.0.0", KEY)
    dlg = app.check_for_updates()
    wait(root, lambda: "Available" in dlg.title.cget("text"), 15)
    check("update dialog: Update Available", dlg.title.cget("text") == "Master Download Manager Update Available")
    labels = [b.cget("text") for b in dlg.buttons.winfo_children()]
    check("update dialog buttons", set(labels) == {"Update Now", "Remind Me Later", "View Release Notes", "Cancel"},
          str(labels))
    shot(dlg.win, "41-06-update-available")
    handed = []
    app.install_update = lambda info, installer, backup: handed.append((info.version, installer, backup))
    dlg._download()
    wait(root, lambda: dlg.title.cget("text") == "Update Ready to Install", 20)
    check("update downloaded and verified", dlg.title.cget("text") == "Update Ready to Install",
          dlg.subtitle.cget("text"))
    shot(dlg.win, "41-07-update-ready")
    dlg._install()
    check("Install and Restart hands over to the installer", handed and handed[0][0] == "9.0.0")
    dlg.close()

    # --- unverifiable release: shown, not installable ---
    GH.add_release("9.1.0", ECC.generate(curve="ed25519"))
    dlg = app.check_for_updates()
    wait(root, lambda: "Available" in dlg.title.cget("text"), 15)
    labels = [b.cget("text") for b in dlg.buttons.winfo_children()]
    check("bad signature: no Update Now, warns and offers a report", "Update Now" not in labels
          and "Open Release Page" not in labels and "Report a Problem" in labels, str(labels))
    shot(dlg.win, "41-08-update-unverified")
    dlg.close()

    # --- unsigned release (like 4.0.0): link to the release page only ---
    GH.add_release("9.2.0", KEY, sign=False)
    dlg = app.check_for_updates()
    wait(root, lambda: "Available" in dlg.title.cget("text"), 15)
    labels = [b.cget("text") for b in dlg.buttons.winfo_children()]
    check("unsigned release: Open Release Page, no Update Now", "Update Now" not in labels
          and "Open Release Page" in labels, str(labels))
    shot(dlg.win, "41-08b-update-unsigned")
    dlg._remind_later()
    check("Remind Me Later snoozes that version", app.settings.get("update_remind_version") == "9.2.0")

    # --- settings: Updates section ---
    app.open_settings()
    pump(root, 1.2)
    sw = app._settings_window
    from ytd.gui.settings_window import SECTIONS
    for section in SECTIONS:
        sw.show(section)
        pump(root, 0.2)
    check("every Settings section opens", True, f"{len(SECTIONS)} sections")
    sw.show("Licenses")
    pump(root, 0.4)
    shot(sw.win, "41-10-settings-licenses")
    sw.show("Updates")
    pump(root, 0.5)
    check("Settings → Updates shows version/last check/status", __version__ in sw.update_label.cget("text")
          and "Last check" in sw.update_label.cget("text"))
    shot(sw.win, "41-09-settings-updates")
    sw.win.destroy()

    dlg = app.check_for_updates()
    t0 = time.time()
    wait(root, lambda: "Available" in dlg.title.cget("text"), 20)
    print(f"[timing] last check took {time.time() - t0:.1f}s")
    if "Available" not in dlg.title.cget("text"):
        import faulthandler
        faulthandler.dump_traceback(all_threads=True)
    dlg._release_page()
    check("View Release Notes opens the release page", OPENED and "/releases/tag/v9.2.0" in OPENED[-1],
          f"{OPENED[-1:]} / {dlg.title.cget('text')} / {dlg.subtitle.cget('text')[:120]}")
    dlg.close()
    print(f"[browser] would have opened: {OPENED}")
    app.quit_app()
    GH.close()
    failed = [r for r in RESULTS if not r[1]]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} GUI checks passed. Screenshots: {args.out}")
    return 1 if failed else 0


def _walk(w):
    yield w
    for c in w.winfo_children():
        yield from _walk(c)


if __name__ == "__main__":
    sys.exit(main())
