"""File | Tasks | Tools | Help — a themed menu bar above the toolbar.

Menus are rebuilt each time they open, so every entry reflects the current
selection and state (unavailable actions are disabled, never erroring).
Alt+F / Alt+T / Alt+O / Alt+H open them from the keyboard.
"""

import os
import tkinter as tk
import webbrowser

from .. import bandwidth
from ..config import APP_NAME, DATA_DIR, LOG_DIR, LOG_FILE
from ..models import (
    ACTIVE_STATUSES, PRIORITY_HIGH, PRIORITY_LABELS, PRIORITY_LOW, PRIORITY_NORMAL, RUNNING_STATUSES,
    STATUS_CANCELLED, STATUS_COMPLETED, STATUS_FAILED, STATUS_PAUSED, STATUS_QUEUED, STATUS_RETRYING,
)
from .common import open_file, open_folder

REPO = "https://github.com/enoluca22-dota2/master-download-manager"
ISSUES = {
    "bug": f"{REPO}/issues/new?template=bug_report.md&labels=bug",
    "feature": f"{REPO}/issues/new?template=feature_request.md&labels=enhancement",
}
GUIDES = {  # (title, bundled file in docs/)
    "user": ("User Guide", "USER_GUIDE.md"),
    "install": ("Installation Guide", "INSTALLATION.md"),
    "browser": ("Browser Integration Guide", "BROWSER_INTEGRATION.md"),
    "trouble": ("Troubleshooting", "TROUBLESHOOTING.md"),
}

class MenuBar(tk.Frame):
    def __init__(self, parent, app):
        c = app.c
        super().__init__(parent, bg=c["toolbar"], height=round(26 * app.scale))
        self.app = app
        self.pack_propagate(False)
        self.clipboard_var = tk.BooleanVar(value=bool(app.settings.get("clipboard_monitor_enabled")))
        self.limited_var = tk.BooleanVar(value=bandwidth.manager.limited_mode)
        self.labels = {}
        for key, text, underline in (("file", "File", 0), ("tasks", "Tasks", 0), ("tools", "Tools", 1),
                                     ("help", "Help", 0)):
            lab = tk.Label(self, text=text, underline=underline, font=("Segoe UI", 9), bg=c["toolbar"], fg=c["text"],
                           padx=round(10 * app.scale), cursor="hand2")
            lab.pack(side="left", fill="y")
            lab.bind("<Button-1>", lambda e, k=key: self.open(k))
            lab.bind("<Enter>", lambda e, w=lab: w.configure(bg=c["surface_hover"]))
            lab.bind("<Leave>", lambda e, w=lab: w.configure(bg=c["toolbar"]))
            self.labels[key] = lab
        root = app.root
        for seq, key in (("<Alt-f>", "file"), ("<Alt-t>", "tasks"), ("<Alt-o>", "tools"), ("<Alt-h>", "help")):
            root.bind(seq, lambda e, k=key: (self.open(k), "break")[1])
        self._bind_shortcuts()

    # ------------------------------------------------------------- plumbing -- #
    def _menu(self):
        c = self.app.c
        return tk.Menu(self, tearoff=0, bg=c["surface"], fg=c["text"], activebackground=c["selected"],
                       activeforeground=c["text"], disabledforeground=c["faint"], bd=0, font=("Segoe UI", 10),
                       relief="flat")

    def open(self, key):
        menu = getattr(self, f"_build_{key}")()
        lab = self.labels[key]
        try:
            menu.tk_popup(lab.winfo_rootx(), lab.winfo_rooty() + lab.winfo_height())
        finally:
            menu.grab_release()
        self.last_menu = menu  # tests inspect the entries

    def build(self, key):
        """The menu for `key` as it would open now (used by tests)."""
        return getattr(self, f"_build_{key}")()

    @staticmethod
    def _header(menu, text):
        menu.add_command(label=text.upper(), state="disabled", font=("Segoe UI Semibold", 8))

    def _bind_shortcuts(self):
        a, root = self.app, self.app.root

        def guard(fn):
            def handler(event):
                if isinstance(event.widget, tk.Entry) or event.widget.winfo_class() in ("Entry", "Text"):
                    return None  # typing in a field: let the field have the key
                fn()
                return "break"
            return handler
        root.bind("<Control-n>", guard(lambda: a.open_add_dialog()))
        root.bind("<Control-N>", guard(self.add_multiple))
        root.bind("<Control-o>", guard(a.add_torrent_file))
        root.bind("<Control-M>", guard(a.add_magnet))
        root.bind("<Control-i>", guard(self.import_list))
        root.bind("<Control-e>", guard(self.export_list))
        root.bind("<Control-j>", guard(self.open_downloads_folder))
        root.bind("<Control-h>", guard(self.history))
        root.bind("<Control-comma>", guard(a.open_settings))
        root.bind("<Control-q>", guard(a.quit_app))
        root.bind("<Control-r>", guard(lambda: a.act_on_selection("resume")))
        root.bind("<Control-p>", guard(lambda: a.act_on_selection("pause")))
        root.bind("<F1>", guard(lambda: self.guide("user")))

    def _sel(self):
        return self.app.selected_jobs()

    # ----------------------------------------------------------------- File -- #
    def _build_file(self):
        a, m = self.app, self._menu()
        m.add_command(label="New Download…", accelerator="Ctrl+N", command=lambda: a.open_add_dialog())
        m.add_command(label="Add Multiple URLs…", accelerator="Ctrl+Shift+N", command=self.add_multiple)
        m.add_command(label="Open Torrent File…", accelerator="Ctrl+O", command=a.add_torrent_file)
        m.add_command(label="Add Magnet Link…", accelerator="Ctrl+Shift+M", command=a.add_magnet)
        m.add_separator()
        m.add_command(label="Import Download List…", accelerator="Ctrl+I", command=self.import_list)
        m.add_command(label="Export Download List…", accelerator="Ctrl+E", command=self.export_list,
                      state="normal" if a.manager.list_jobs() else "disabled")
        m.add_separator()
        m.add_command(label="Open Downloads Folder", accelerator="Ctrl+J", command=self.open_downloads_folder)
        m.add_command(label="Download History…", accelerator="Ctrl+H", command=self.history)
        m.add_command(label="Settings…", accelerator="Ctrl+,", command=a.open_settings)
        m.add_separator()
        m.add_command(label="Minimize to Tray", command=self.minimize_to_tray,
                      state="normal" if a.tray_icon is not None else "disabled")
        m.add_command(label="Exit", accelerator="Ctrl+Q", command=a.quit_app)
        return m

    def add_multiple(self):
        from .tools_dialogs import MultiUrlDialog
        MultiUrlDialog(self.app)

    def import_list(self):
        from .tools_dialogs import import_download_list
        import_download_list(self.app, self.app.root)

    def export_list(self):
        from .tools_dialogs import export_download_list
        export_download_list(self.app, self.app.root)

    def open_downloads_folder(self):
        folder = self.app.settings.get("download_dir")
        os.makedirs(folder, exist_ok=True)
        open_folder(folder)

    def history(self):
        from .tools_dialogs import HistoryWindow
        HistoryWindow(self.app)

    def minimize_to_tray(self):
        if self.app.tray_icon is not None:
            self.app.root.withdraw()

    # ---------------------------------------------------------------- Tasks -- #
    def _build_tasks(self):
        a, m, jobs = self.app, self._menu(), self._sel()
        live = [j for j in jobs if not j.from_history]
        st = {j.status for j in live}
        startable = [j for j in live if j.status in (STATUS_PAUSED, STATUS_FAILED, STATUS_CANCELLED)]
        pausable = [j for j in live if j.status in RUNNING_STATUSES or j.status in (STATUS_QUEUED, STATUS_RETRYING)]
        all_jobs = a.manager.list_jobs()
        q = a._current_queue()
        en = lambda ok: "normal" if ok else "disabled"  # noqa: E731
        m.add_command(label="Start Selected Download", command=lambda: self.start_selected(startable),
                      state=en(startable))
        m.add_command(label="Pause Selected Download", accelerator="Ctrl+P", command=lambda: a.act("pause", pausable),
                      state=en(pausable))
        m.add_command(label="Resume Selected Download", accelerator="Ctrl+R",
                      command=lambda: a.act("resume", [j for j in live if j.status == STATUS_PAUSED]),
                      state=en(STATUS_PAUSED in st))
        failed_sel = [j for j in live if j.status in (STATUS_FAILED, STATUS_CANCELLED)]
        any_failed = any(j.status == STATUS_FAILED and not j.from_history for j in all_jobs)
        m.add_command(label="Retry Failed Download" + ("" if failed_sel else "s"),
                      command=(lambda: a.act("retry", failed_sel)) if failed_sel else a._retry_failed,
                      state=en(failed_sel or any_failed))
        m.add_separator()
        m.add_command(label="Start All", command=self.start_all,
                      state=en(any(j.status == STATUS_PAUSED and not j.from_history for j in all_jobs)
                               or any(not x.running for x in a.manager.queues.all())))
        m.add_command(label="Pause All", command=a.manager.pause_all,
                      state=en(any(j.status in RUNNING_STATUSES or j.status in (STATUS_QUEUED, STATUS_RETRYING)
                                   for j in all_jobs)))
        m.add_command(label="Stop All (stop every queue)", command=self.stop_all,
                      state=en(any(x.running for x in a.manager.queues.all())))
        m.add_separator()
        m.add_command(label=f"Start Queue “{q.name}”", command=a.start_current_queue, state=en(not q.running))
        m.add_command(label=f"Stop Queue “{q.name}”", command=a.stop_current_queue, state=en(q.running))
        m.add_command(label="Queue Manager…", command=a.open_queues)
        m.add_command(label="Download Scheduler…", command=lambda: a.open_queues(tab="Schedule"))
        m.add_separator()
        active = [j for j in live if j.status in ACTIVE_STATUSES]
        prio = self._menu()
        for value in (PRIORITY_HIGH, PRIORITY_NORMAL, PRIORITY_LOW):
            prio.add_command(label=PRIORITY_LABELS[value],
                             command=lambda v=value: [a.manager.set_priority(j.job_id, v) for j in active])
        m.add_cascade(label="Change Download Priority", menu=prio, state=en(active))
        qmenu = self._menu()
        for queue in a.manager.queues.all():
            qmenu.add_command(label=queue.name, command=lambda qid=queue.id: a._move_to_queue(active, qid))
        m.add_cascade(label="Move to Queue", menu=qmenu, state=en(active))
        m.add_separator()
        m.add_command(label="Remove Selected Entry…", accelerator="Del", command=lambda: a.remove_jobs(jobs),
                      state=en(jobs))
        done = next((j for j in jobs if j.status == STATUS_COMPLETED and j.result_path
                     and os.path.exists(j.result_path)), None)
        m.add_command(label="Open Completed File", command=lambda: open_file(done.result_path, a.root),
                      state=en(done))
        m.add_command(label="Show in Folder", command=lambda: open_folder(jobs[0].download_dir, jobs[0].result_path),
                      state=en(jobs))
        return m

    def start_selected(self, jobs):
        self.app.act("resume", [j for j in jobs if j.status == STATUS_PAUSED])
        self.app.act("retry", [j for j in jobs if j.status in (STATUS_FAILED, STATUS_CANCELLED)])

    def start_all(self):
        for q in self.app.manager.queues.all():
            if not q.running:
                self.app.manager.start_queue(q.id)
        self.app.manager.resume_all()
        self.app._mark_structure()

    def stop_all(self):
        for q in self.app.manager.queues.all():
            if q.running:
                self.app.manager.stop_queue(q.id)
        self.app._mark_structure()
        self.app.toast("info", "All queues stopped — downloads are paused. Use Start All to continue.")

    # ---------------------------------------------------------------- Tools -- #
    def _build_tools(self):
        a, m = self.app, self._menu()
        self.clipboard_var.set(bool(a.settings.get("clipboard_monitor_enabled")))
        self.limited_var.set(bandwidth.manager.limited_mode)
        m.add_command(label="Browser Integration…", command=lambda: a.open_settings("Browser Integration"))
        m.add_checkbutton(label="Clipboard Monitoring", variable=self.clipboard_var, command=self.toggle_clipboard)
        speed = self._menu()
        speed.add_checkbutton(label=f"Limit download speed ({a.settings.get('limited_mode_kbps', 512)} KB/s)",
                              variable=self.limited_var, command=a.toggle_limited_mode)
        speed.add_command(label="Speed limit settings…", command=lambda: a.open_settings("Bandwidth"))
        speed.add_command(label="Speed profiles…", command=lambda: a.open_queues(page="Speed profiles"))
        m.add_cascade(label="Speed Limiter", menu=speed)
        m.add_command(label="Network and Connection Settings…", command=lambda: a.open_settings("Network"))
        m.add_command(label="Default Download Folders…", command=lambda: a.open_settings("Downloads"))
        m.add_separator()
        m.add_command(label="Download Scheduler…", command=lambda: a.open_queues(tab="Schedule"))
        m.add_command(label="Queue Manager…", command=a.open_queues)
        m.add_command(label="Check File Integrity…", command=self.integrity)
        m.add_separator()
        m.add_command(label="Import Settings…", command=self.import_settings)
        m.add_command(label="Export Settings…", command=self.export_settings)
        m.add_command(label="View Application Logs", command=self.view_logs)
        m.add_command(label="Open Data Folder", command=lambda: open_folder(DATA_DIR))
        m.add_separator()
        m.add_command(label="Settings…", accelerator="Ctrl+,", command=a.open_settings)
        return m

    def toggle_clipboard(self):
        on = bool(self.clipboard_var.get())
        self.app.settings.set("clipboard_monitor_enabled", on)
        self.app.settings_changed()
        self.app.toast("info", f"Clipboard monitoring {'on' if on else 'off'}.")

    def integrity(self):
        from .tools_dialogs import IntegrityDialog
        jobs = [j for j in self._sel() if j.result_path and os.path.isfile(j.result_path)]
        IntegrityDialog(self.app, jobs[0].result_path if jobs else None)

    def import_settings(self):
        from .tools_dialogs import import_settings
        import_settings(self.app, self.app.root)

    def export_settings(self):
        from .tools_dialogs import export_settings
        export_settings(self.app, self.app.root)

    def view_logs(self):
        from .text_viewer import TextViewer
        try:
            with open(LOG_FILE, encoding="utf-8", errors="replace") as f:
                lines = f.readlines()[-3000:]
            text = "".join(lines) or "(the log is empty)"
        except OSError:
            text = "No application log yet."
        TextViewer(self.app, "Application Log (last 3,000 lines)", text, mono=True, size="980x620",
                   extra_buttons=[("Open logs folder", lambda: open_folder(LOG_DIR))])

    # ----------------------------------------------------------------- Help -- #
    def _build_help(self):
        a, m = self.app, self._menu()
        self._header(m, "Support & Community")
        m.add_command(label="GitHub Repository", command=lambda: webbrowser.open(REPO))
        m.add_command(label="Report a Bug", command=lambda: webbrowser.open(ISSUES["bug"]))
        m.add_command(label="Request a Feature", command=lambda: webbrowser.open(ISSUES["feature"]))
        m.add_separator()
        self._header(m, "Documentation")
        for key, (title, _file) in GUIDES.items():
            m.add_command(label=title, accelerator="F1" if key == "user" else "", command=lambda k=key: self.guide(k))
        m.add_separator()
        self._header(m, "Open Source")
        m.add_command(label="View Open-Source Licenses…", command=a.open_licenses)
        m.add_command(label="Third-Party Notices…", command=self.notices)
        m.add_command(label="Source Code Information…", command=self.source_info)
        m.add_separator()
        self._header(m, "Application")
        m.add_command(label="Check for Updates…", command=a.check_for_updates)
        m.add_command(label="What's New", command=self.whats_new)
        m.add_command(label="Changelog", command=self.changelog)
        m.add_command(label=f"About {APP_NAME}", command=a.open_about)
        return m

    def guide(self, key):
        from .text_viewer import TextViewer
        title, name = GUIDES[key]
        text = self.app.read_doc(name)
        TextViewer(self.app, title, text or f"{name} isn't included in this installation.",
                   online_url=f"{REPO}/blob/main/docs/{name}")

    def notices(self):
        from .licenses_window import show_notices
        show_notices(self.app)

    def source_info(self):
        from .text_viewer import TextViewer
        from .. import __version__
        TextViewer(self.app, "Source Code Information", (
            f"{APP_NAME} {__version__}\n\n"
            f"Source code: {REPO}\n"
            f"This version: {REPO}/tree/v{__version__}\n"
            f"Releases: {REPO}/releases\n\n"
            "Copyright (c) 2026 the Master Download Manager author. The program's own source code is published for "
            "transparency but is not released under an open-source license (all rights reserved); see "
            "docs/LICENSING.md.\n\n"
            "Third-party components are open-source software under their own licenses — Help → View Open-Source "
            "Licenses lists each with its source repository. The LGPL components (FFmpeg, pystray) are shipped as "
            "separate, replaceable files; FFmpeg's exact build and source location are in tools\\ffmpeg\\SOURCE.txt "
            "in the installation folder."), online_url=REPO)

    def whats_new(self):
        from .text_viewer import TextViewer
        from .. import __version__
        TextViewer(self.app, f"What's New in {__version__}", self.app.whats_new_text(),
                   online_url=f"{REPO}/releases/tag/v{__version__}")

    def changelog(self):
        from .text_viewer import TextViewer
        TextViewer(self.app, "Changelog", self.app.read_doc("CHANGELOG.md") or "The changelog isn't included.",
                   online_url=f"{REPO}/blob/main/CHANGELOG.md")
