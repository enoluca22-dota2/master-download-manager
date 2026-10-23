"""Help → Check for Updates.

    checking → "Master Download Manager Update Available" (installed / latest /
    release date / size / what's new; Update Now · Remind Me Later · View
    Release Notes · Cancel) → downloading (progress, Cancel) → "Update Ready
    to Install" (Install and Restart · Later) → MDM closes, Setup upgrades it
    in place and starts the new version.

Only verified (signed) releases get "Update Now"; others offer the release
page. Remind Me Later hides that version for a week.
"""

import datetime
import threading
import time
import webbrowser

import customtkinter as ctk

from .. import __version__, update_install
from ..config import INSTALL_DIR
from ..updater import UpdateError, check_release
from .common import human_size
from .theme import COLORS, font, glass_button

REMIND_SECONDS = 7 * 24 * 3600


def _date(iso):
    try:
        return datetime.datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone().strftime("%d %B %Y")
    except (ValueError, AttributeError):
        return iso or "unknown"


class UpdateDialog:
    def __init__(self, app, info=None, auto_install=False):
        self.app = app
        self.info = info
        self.cancel = threading.Event()
        self.installer = self.backup = None
        self.auto_install = auto_install
        win = self.win = ctk.CTkToplevel(app.root)
        win.title("Master Download Manager Update")
        win.geometry("620x520")
        win.minsize(560, 460)
        app.apply_glass_window(win)
        win.protocol("WM_DELETE_WINDOW", self.close)
        self.title = ctk.CTkLabel(win, text="Checking for updates…", font=font(17, "bold"), anchor="w")
        self.title.pack(fill="x", padx=20, pady=(18, 4))
        self.subtitle = ctk.CTkLabel(win, text="", font=font(12), text_color=COLORS["muted"], anchor="w",
                                     justify="left", wraplength=570)
        self.subtitle.pack(fill="x", padx=20)
        self.facts = ctk.CTkFrame(win, fg_color="transparent")
        self.facts.pack(fill="x", padx=20, pady=(10, 4))
        self.notes_label = ctk.CTkLabel(win, text="What's new", font=font(13, "bold"), anchor="w")
        self.notes = ctk.CTkTextbox(win, wrap="word", font=font(12), height=200)
        self.progress = ctk.CTkProgressBar(win, height=8)
        self.progress_label = ctk.CTkLabel(win, text="", font=font(11), text_color=COLORS["muted"], anchor="w")
        self.buttons = ctk.CTkFrame(win, fg_color="transparent")
        self.buttons.pack(side="bottom", fill="x", padx=20, pady=16)
        if info is None:
            self._start_check()
        else:
            self._show(info)

    # ------------------------------------------------------------- helpers -- #
    def _alive(self):
        return self.win.winfo_exists()

    def _set_buttons(self, specs):
        for w in self.buttons.winfo_children():
            w.destroy()
        for text, cmd, variant in specs:
            glass_button(self.buttons, text, cmd, variant=variant, width=150 if variant == "primary" else 130,
                         height=34).pack(side="right", padx=(8, 0))

    def _fact(self, label, value):
        row = ctk.CTkFrame(self.facts, fg_color="transparent")
        row.pack(fill="x", pady=1)
        ctk.CTkLabel(row, text=label, width=140, anchor="w", text_color=COLORS["muted"], font=font(12)).pack(side="left")
        ctk.CTkLabel(row, text=value, anchor="w", font=font(12, "bold")).pack(side="left")

    # ---------------------------------------------------------------- check -- #
    def _start_check(self):
        self._set_buttons([("Cancel", self.close, "secondary")])
        channel = self.app.settings.get("update_channel") or "stable"

        def work():
            try:
                info = check_release(channel=channel)
                self.app.settings.set("update_last_check", time.time())
                self.app.call_in_main(self._show, info)
            except UpdateError as e:
                self.app.call_in_main(self._error, str(e))
        threading.Thread(target=work, name="mdm-update-check", daemon=True).start()

    def _error(self, message):
        if not self._alive():
            return
        self.title.configure(text="Couldn't check for updates")
        self.subtitle.configure(text=message + "\n\nYou can try again later; nothing was changed.",
                                text_color=COLORS["danger"])
        self.app.settings.set("update_last_status", f"Check failed: {message}")
        self._set_buttons([("Close", self.close, "secondary"), ("Try again", self._start_check, "primary")])

    def _show(self, info):
        if not self._alive():
            return
        self.info = info
        for w in self.facts.winfo_children():
            w.destroy()
        if not info.available:
            self.title.configure(text="Master Download Manager is up to date")
            self.subtitle.configure(text=f"You have version {__version__}, the latest "
                                         f"{'beta' if info.prerelease else 'stable'} release.")
            self.app.settings.set("update_last_status", f"Up to date ({__version__})")
            self._set_buttons([("Close", self.close, "primary")])
            return
        self.title.configure(text="Master Download Manager Update Available")
        self.subtitle.configure(text="A new version is ready. Your settings, download history, queues and unfinished "
                                     "downloads are kept when you update.", text_color=COLORS["muted"])
        self._fact("Installed version", __version__)
        self._fact("Latest version", info.version + ("  (beta)" if info.prerelease else ""))
        self._fact("Release date", _date(info.published))
        self._fact("Download size", human_size(info.size, "unknown"))
        self._fact("Verification", "Signed by the official release key ✓" if info.verified
                   else "FAILED — do not install" if info.failed else "Not signed")
        if not self.notes_label.winfo_ismapped():
            self.notes_label.pack(fill="x", padx=20, pady=(8, 2))
        self.notes.pack(fill="both", expand=True, padx=20)
        self.notes.configure(state="normal")
        self.notes.delete("1.0", "end")
        self.notes.insert("1.0", info.notes.strip() or "No release notes were published.")
        self.notes.configure(state="disabled")
        self.app.settings.set("update_last_status", f"Version {info.version} available")
        specs = [("Cancel", self.close, "secondary"), ("View Release Notes", self._release_page, "secondary"),
                 ("Remind Me Later", self._remind_later, "secondary")]
        if info.verified:
            specs.append(("Update Now", self._download, "primary"))
        elif info.failed:
            self.subtitle.configure(text=info.reason + "\n\nThis download doesn't match what the official release "
                                    "key signed, so it may have been tampered with. Don't install it; please "
                                    "report this.", text_color=COLORS["danger"])
            specs = [("Close", self.close, "secondary"), ("Report a Problem", self._report, "primary")]
        else:
            self.subtitle.configure(text=info.reason, text_color=COLORS["warning"])
            specs.append(("Open Release Page", self._release_page, "primary"))
        self._set_buttons(specs)
        if self.auto_install and info.verified:
            self.win.after(300, self._download)

    def _release_page(self):
        if self.info and self.info.release_url:
            webbrowser.open(self.info.release_url)

    def _report(self):
        from .menubar import ISSUES
        webbrowser.open(ISSUES["bug"])

    def _remind_later(self):
        self.app.settings.update({"update_remind_version": self.info.version,
                                  "update_remind_after": time.time() + REMIND_SECONDS})
        self.close()

    # ------------------------------------------------------------- download -- #
    def _download(self):
        info = self.info
        self.title.configure(text=f"Downloading Master Download Manager {info.version}…")
        self.notes.pack_forget()
        self.notes_label.pack_forget()
        self.progress.pack(fill="x", padx=20, pady=(16, 4))
        self.progress.set(0)
        self.progress_label.pack(fill="x", padx=20)
        self.cancel.clear()
        self._set_buttons([("Cancel", self._cancel_download, "secondary")])
        started = time.time()

        def progress(received, total):
            speed = received / max(0.1, time.time() - started)
            self.app.call_in_main(self._progress, received, total, speed)

        def work():
            try:
                installer, backup = update_install.prepare(info, INSTALL_DIR, progress=progress,
                                                            cancel_event=self.cancel)
                self.app.call_in_main(self._ready, installer, backup)
            except UpdateError as e:
                self.app.call_in_main(self._download_failed, str(e))
            except OSError as e:
                self.app.call_in_main(self._download_failed, f"{e.__class__.__name__}: {e}")
        threading.Thread(target=work, name="mdm-update-download", daemon=True).start()

    def _progress(self, received, total, speed):
        if not self._alive():
            return
        if total:
            self.progress.set(received / total)
        self.progress_label.configure(text=f"{human_size(received, '0 B')} of {human_size(total, '?')}  ·  "
                                           f"{human_size(speed, '0 B')}/s")

    def _cancel_download(self):
        self.cancel.set()

    def _download_failed(self, message):
        if not self._alive():
            return
        self.title.configure(text="The update couldn't be downloaded")
        self.subtitle.configure(text=message + "\n\nNothing was installed; you can try again.",
                                text_color=COLORS["danger"])
        self.progress.pack_forget()
        self.progress_label.pack_forget()
        self.app.settings.set("update_last_status", f"Download failed: {message}")
        self._set_buttons([("Close", self.close, "secondary"), ("Try again", self._download, "primary")])

    def _ready(self, installer, backup):
        if not self._alive():
            return
        self.installer, self.backup = installer, backup
        self.progress.set(1)
        self.title.configure(text="Update Ready to Install")
        running = sum(1 for j in self.app.manager.list_jobs() if j.status in ("Downloading", "Preparing", "Seeding"))
        self.subtitle.configure(text=f"Version {self.info.version} was downloaded and verified. Installing closes "
                                     "Master Download Manager, upgrades it and opens the new version."
                                     + (f"\n{running} running download(s) will be paused and resume afterwards."
                                        if running else ""), text_color=COLORS["muted"])
        self.progress_label.configure(text="Verified: size and SHA-256 match the signed release manifest.")
        self._set_buttons([("Later", self.close, "secondary"), ("Install and Restart", self._install, "primary")])
        if self.auto_install:
            self.win.after(500, self._install)

    def _install(self):
        try:
            self.app.install_update(self.info, self.installer, self.backup)
        except UpdateError as e:
            self._download_failed(str(e))

    def close(self):
        self.cancel.set()
        if self._alive():
            self.win.destroy()


def show_post_update_result(app, result):
    """After an update: tell the user what happened (success or failure)."""
    from tkinter import messagebox
    if result.get("ok"):
        app.toast("success", f"Updated to {result.get('running')}")
        from .text_viewer import TextViewer
        TextViewer(app, f"Updated to Master Download Manager {result.get('running')}",
                   result["message"] + "\n\n" + app.whats_new_text())
    else:
        messagebox.showwarning("Update not completed",
                               result["message"] + (f"\n\nInstaller log: {result.get('log')}" if result.get("log") else ""))
