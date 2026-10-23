"""Master Download Manager — main window.

Threading model: engines and the local API run on background threads and
never touch Tk. They report through DownloadManager.subscribe(); the
callback only records *which* jobs changed. A short main-thread timer
(`_pump`) drains those marks plus any call_in_main() requests and updates
only the affected rows, so the UI stays responsive with thousands of rows
and many simultaneous downloads.
"""

import os
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import customtkinter as ctk

from .. import notifications, tray
from ..api.server import run_server
from ..config import APP_ICON_FILE, APP_NAME, QUEUE_FILE
from ..engine import DownloadManager, _format_speed
from ..history import HistoryStore, delete_entry_file, library_category
from ..logging_setup import get_logger
from ..models import (
    ACTIVE_STATUSES, RUNNING_STATUSES, STATUS_CANCELLED, STATUS_COMPLETED, STATUS_DOWNLOADING, STATUS_FAILED,
    STATUS_PAUSED, STATUS_QUEUED, STATUS_RETRYING, TERMINAL_STATUSES,
)
from ..router import classify
from ..settings_store import SettingsStore
from .. import tools
from . import effects as glass_effects
from . import theme as glass_theme
from .add_dialog import AddDialog
from .common import (
    ENGINE_LABEL, STATUS_ICON, job_date_text, open_file, open_folder, progress_text, size_text, truncate,
)
from .details import DetailsWindow
from .settings_window import SettingsWindow
from .theme import COLORS, RADIUS, fade_in_toplevel, font, glass_button, glass_card, glass_entry, glass_icon_button

log = get_logger()

DARK_COLORS = {name: pair[1] for name, pair in COLORS.items()}
LIGHT_COLORS = {name: pair[0] for name, pair in COLORS.items()}

VIEWS = [
    ("Downloads", [("All", "▣"), ("Active", "▶"), ("Completed", "✓"), ("Failed", "✕")]),
    ("Library", [("Videos", "🎬"), ("Audio", "🎵"), ("Torrents", "🧲"), ("Archives", "🗜"), ("Other", "📄")]),
    ("", [("History", "🕘")]),
]
COLUMNS = ("name", "engine", "status", "progress", "size", "speed", "eta", "added")
HEADINGS = {"name": "Name", "engine": "Engine", "status": "Status", "progress": "Progress", "size": "Size",
            "speed": "Speed", "eta": "ETA", "added": "Added"}
WIDTHS = {"name": 300, "engine": 66, "status": 140, "progress": 150, "size": 128, "speed": 84, "eta": 62, "added": 118}
PUMP_MS = 120


def _library_key(job):
    return library_category({"engine": job.engine, "kind": job.kind, "file": job.result_path or ""})


def view_matches(view, job):
    if view == "All":
        return True
    if view == "Active":
        return job.status in ACTIVE_STATUSES
    if view == "Completed":
        return job.status == STATUS_COMPLETED
    if view == "Failed":
        return job.status == STATUS_FAILED
    if view == "History":
        return job.status in TERMINAL_STATUSES
    return job.status == STATUS_COMPLETED and _library_key(job) == view


class MasterDownloadManagerApp:
    def __init__(self, root):
        self.root = root
        root.title(APP_NAME)
        root.geometry("1240x760")
        root.minsize(1000, 620)

        self.settings = SettingsStore()
        self.history = HistoryStore()
        self.manager = DownloadManager(self.settings, self.history, queue_file=QUEUE_FILE)
        self.manager.seed_from_history(self.history.all())
        if self.settings.get("restore_queue_on_start", True):
            self.manager.restore_queue()

        self.appearance_mode = self.settings.get("appearance_mode", "Dark")
        if self.appearance_mode not in ("Dark", "Light"):
            self.appearance_mode = "Dark"
        ctk.set_appearance_mode(self.appearance_mode.lower())
        glass_theme.install_ctk_theme()
        if os.path.isfile(APP_ICON_FILE):
            try:
                root.iconbitmap(APP_ICON_FILE)
            except tk.TclError:
                pass
        root.configure(fg_color=COLORS["background"])
        self._configure_ttk_style()

        self.view = "All"
        self.details_windows = {}
        self._iid_job = {}
        self._dirty = set()
        self._structure_dirty = True
        self._dirty_lock = threading.Lock()
        self._ui_calls = queue.Queue()
        self._sort = {"column": None, "reverse": False}
        self._closing = False
        self._last_clipboard = ""
        self._clipboard_seen = set()
        self.tray_icon = None
        self.api_thread = self.api_server = None

        root.grid_rowconfigure(1, weight=1)
        root.grid_columnconfigure(1, weight=1)
        self._build_toolbar()
        self._build_sidebar()
        self._build_main()
        self._build_status_bar()
        self._bind_keys()

        self.manager.subscribe(self._on_engine_event)
        self.manager.start()
        if self.settings.get("api_enabled", True):
            self._start_local_api()
        self._setup_tray()
        root.protocol("WM_DELETE_WINDOW", self._on_close_request)
        if self.settings.get("clipboard_monitor_enabled"):
            root.after(1500, self._poll_clipboard)
        root.after(PUMP_MS, self._pump)

    # ================================================== threading bridge === #
    def call_in_main(self, fn, *args):
        """Thread-safe: run fn(*args) on the Tk main thread soon."""
        self._ui_calls.put((fn, args))

    def _on_engine_event(self, event, job):
        """Background thread — only records what changed."""
        with self._dirty_lock:
            if event == "progress":
                self._dirty.add(job.job_id)
            else:
                self._structure_dirty = True
                self._dirty.add(job.job_id)
        if event == "started" and job.source == "gui":
            self.call_in_main(self.toast, "info", f"Started — {truncate(job.title, 40)}")
        elif event == "finished":
            self.call_in_main(self._job_finished, job)

    def _pump(self):
        if self._closing:
            return
        while True:
            try:
                fn, args = self._ui_calls.get_nowait()
            except queue.Empty:
                break
            try:
                fn(*args)
            except Exception:  # noqa: BLE001 - one bad callback must not stop the UI loop
                log.exception("UI callback failed")
        with self._dirty_lock:
            dirty, self._dirty = self._dirty, set()
            structure, self._structure_dirty = self._structure_dirty, False
        if structure:
            self._rebuild_list()
        elif dirty:
            self._update_rows(dirty)
        if structure or dirty:
            self._refresh_status_bar()
        self.root.after(PUMP_MS, self._pump)

    # ============================================================== theme === #
    def palette(self):
        return LIGHT_COLORS if self.appearance_mode == "Light" else DARK_COLORS

    def apply_glass_window(self, win):
        """Plain window background + the app icon (CTkToplevel would otherwise
        replace the icon with CustomTkinter's default shortly after opening)."""
        win.configure(fg_color=COLORS["background"])
        if os.path.isfile(APP_ICON_FILE):
            def set_icon():
                try:
                    if win.winfo_exists():
                        win.iconbitmap(APP_ICON_FILE)
                except tk.TclError:
                    pass
            win.after(250, set_icon)

    def set_appearance_mode(self, mode):
        if mode not in ("Dark", "Light"):
            return
        self.appearance_mode = mode
        self.settings.set("appearance_mode", mode)
        ctk.set_appearance_mode(mode.lower())
        self._configure_ttk_style()

    def _configure_ttk_style(self):
        p = self.palette()
        field_bg = p["background"]
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Treeview", background=field_bg, foreground=p["text"], fieldbackground=field_bg,
                        bordercolor=p["border"], lightcolor=p["background"], darkcolor=p["background"],
                        borderwidth=0, rowheight=32, font=("Segoe UI", 10), relief="flat")
        style.layout("Treeview", [("Treeview.treearea", {"sticky": "nswe"})])  # no outer border/focus ring
        style.configure("Treeview.Heading", background=p["surface"], foreground=p["muted"], relief="flat",
                        bordercolor=p["border"], lightcolor=p["surface"], darkcolor=p["surface"],
                        padding=(8, 8), font=("Segoe UI Semibold", 10))
        style.map("Treeview", background=[("selected", p["selected"])], foreground=[("selected", p["text"])])
        style.map("Treeview.Heading", background=[("active", p["surface_hover"])], foreground=[("active", p["text"])])
        style.configure("Glass.Vertical.TScrollbar", background=p["border"], troughcolor=field_bg, bordercolor=field_bg,
                        arrowsize=0, relief="flat", width=8)
        style.map("Glass.Vertical.TScrollbar", background=[("active", p["muted"])])
        if hasattr(self, "tree"):
            self._configure_tags()

    def _configure_tags(self):
        p = self.palette()
        self.tree.tag_configure("row_a", background=p["background"])
        self.tree.tag_configure("row_b", background=p["surface_deep"])
        self.tree.tag_configure("running", background=p["surface_hover"])
        self.tree.tag_configure("failed", foreground=p["danger"])
        self.tree.tag_configure("done", foreground=p["muted"])

    # ============================================================= toolbar === #
    def _build_toolbar(self):
        bar = ctk.CTkFrame(self.root, height=58, corner_radius=0, fg_color=COLORS["sidebar"])
        bar.grid(row=0, column=0, columnspan=2, sticky="ew")
        bar.grid_propagate(False)
        self._toolbar = bar
        ctk.CTkFrame(bar, height=1, corner_radius=0, fg_color=COLORS["border"]).place(relx=0, rely=1.0, relwidth=1.0, anchor="sw")

        brand = ctk.CTkFrame(bar, fg_color="transparent")
        brand.pack(side="left", padx=(16, 18), pady=8)
        ctk.CTkLabel(brand, text="MDM", width=44, height=36, corner_radius=RADIUS["sm"], fg_color=COLORS["primary"],
                     text_color=COLORS["on_primary"], font=font(13, "bold")).pack(side="left", padx=(0, 8))
        ctk.CTkLabel(brand, text=APP_NAME, text_color=COLORS["text"], font=font(15, "bold")).pack(side="left")

        actions = ctk.CTkFrame(bar, fg_color="transparent")
        actions.pack(side="left", pady=8)
        glass_button(actions, "+  Add URL", lambda: self.open_add_dialog(), variant="primary", width=104, height=36).pack(side="left", padx=3)
        glass_button(actions, "Paste", self.paste_and_add, width=86, height=36).pack(side="left", padx=3)
        glass_button(actions, "Torrent file", self.add_torrent_file, width=96, height=36).pack(side="left", padx=3)
        glass_button(actions, "Magnet", self.add_magnet, width=96, height=36).pack(side="left", padx=3)
        ctk.CTkFrame(actions, width=1, height=26, fg_color=COLORS["border"]).pack(side="left", padx=10)
        glass_button(actions, "▶ Resume all", self.manager.resume_all, width=104, height=36).pack(side="left", padx=3)
        glass_button(actions, "⏸ Pause all", self.manager.pause_all, width=96, height=36).pack(side="left", padx=3)

        right = ctk.CTkFrame(bar, fg_color="transparent")
        right.pack(side="right", padx=16, pady=8)
        self.search_var = tk.StringVar()
        glass_entry(right, textvariable=self.search_var, placeholder="Search…", height=36, width=200).pack(side="left", padx=(0, 8))
        self.search_var.trace_add("write", lambda *_: self._mark_structure())
        glass_icon_button(right, "⚙", self.open_settings, size=36).pack(side="left")

    # ============================================================= sidebar === #
    def _build_sidebar(self):
        rail = ctk.CTkFrame(self.root, width=176, corner_radius=0, fg_color=COLORS["sidebar"])
        rail.grid(row=1, column=0, sticky="ns")
        rail.grid_propagate(False)
        rail.pack_propagate(False)
        self._sidebar = rail
        ctk.CTkFrame(rail, width=1, corner_radius=0, fg_color=COLORS["border"]).place(relx=1.0, rely=0, relheight=1.0, anchor="ne")
        self._view_buttons = {}
        self._view_counts = {}
        for section, items in VIEWS:
            if section:
                ctk.CTkLabel(rail, text=section.upper(), font=font(10, "bold"), text_color=COLORS["faint"],
                             anchor="w").pack(fill="x", padx=18, pady=(14, 2))
            else:
                ctk.CTkFrame(rail, height=1, fg_color=COLORS["border_soft"]).pack(fill="x", padx=14, pady=(12, 4))
            for name, icon in items:
                btn = glass_button(rail, name, lambda n=name: self.set_view(n), variant="ghost",
                                   width=152, height=32, anchor="w", weight="normal")
                btn.pack(padx=12, pady=1)
                self._view_buttons[name] = btn
        self.set_view("All")

    def set_view(self, name):
        self.view = name
        for n, btn in self._view_buttons.items():
            btn.configure(fg_color=COLORS["surface_hover"] if n == name else "transparent",
                          text_color=COLORS["text"] if n == name else COLORS["muted"])
        glass_theme.apply_active_nav_indicator(self._sidebar, self._view_buttons[name])
        self._mark_structure()

    def _mark_structure(self):
        with self._dirty_lock:
            self._structure_dirty = True

    # ================================================================ list === #
    def _build_main(self):
        card = glass_card(self.root, radius="lg")
        card.grid(row=1, column=1, sticky="nsew", padx=12, pady=(12, 0))
        card.grid_rowconfigure(1, weight=1)
        card.grid_columnconfigure(0, weight=1)

        bar = ctk.CTkFrame(card, fg_color="transparent")
        bar.grid(row=0, column=0, columnspan=2, sticky="ew", padx=10, pady=(10, 4))
        self.view_title = ctk.CTkLabel(bar, text="", font=font(15, "bold"))
        self.view_title.pack(side="left", padx=(4, 14))
        self._sel_buttons = []
        for text, action, width in (("▶ Resume", "resume", 84), ("⏸ Pause", "pause", 78), ("■ Cancel", "cancel", 80),
                                    ("↻ Retry", "retry", 74), ("🗑 Remove", "remove", 88), ("📂 Folder", "folder", 84),
                                    ("▲", "up", 34), ("▼", "down", 34)):
            b = glass_button(bar, text, lambda a=action: self.act_on_selection(a), width=width, height=30, size=11)
            b.pack(side="left", padx=2)
            self._sel_buttons.append(b)
        glass_button(bar, "Clear completed", self._clear_completed, variant="ghost", width=120, height=30, size=11).pack(side="right", padx=2)
        glass_button(bar, "Retry failed", self._retry_failed, variant="ghost", width=100, height=30, size=11).pack(side="right", padx=2)

        self.empty_label = ctk.CTkLabel(card, text="", font=font(13), text_color=COLORS["faint"], justify="center")
        self.tree = ttk.Treeview(card, columns=COLUMNS, show="headings", selectmode="extended")
        for col in COLUMNS:
            self.tree.heading(col, text=HEADINGS[col], command=lambda c=col: self._sort_by(c))
            self.tree.column(col, width=WIDTHS[col], anchor="w" if col in ("name", "status", "progress") else "center",
                             stretch=(col == "name"))
        vsb = ttk.Scrollbar(card, orient="vertical", command=self.tree.yview, style="Glass.Vertical.TScrollbar")
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.grid(row=1, column=0, sticky="nsew", padx=(8, 0), pady=(0, 8))
        vsb.grid(row=1, column=1, sticky="ns", padx=(4, 8), pady=(0, 8))
        self.empty_label.place(in_=self.tree, relx=0.5, rely=0.45, anchor="center")
        self.tree.bind("<Double-1>", self._on_double_click)
        self.tree.bind("<Button-3>", self._on_right_click)
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._update_selection_buttons())
        self._configure_tags()

    def _visible_jobs(self):
        jobs = self.manager.list_jobs()
        order = {j.job_id: i for i, j in enumerate(jobs)}
        jobs = [j for j in jobs if view_matches(self.view, j)]
        query = self.search_var.get().strip().lower()
        if query:
            jobs = [j for j in jobs if query in (j.display_name or "").lower() or query in j.url.lower()]
        col = self._sort["column"]
        if col:
            key = {
                "name": lambda j: (j.display_name or "").lower(), "engine": lambda j: j.engine or "",
                "status": lambda j: j.status, "progress": lambda j: j.progress or 0,
                "size": lambda j: j.total_bytes or 0, "speed": lambda j: j.speed_bps or 0,
                "eta": lambda j: j.eta_seconds or 0, "added": lambda j: j.created_at,
            }[col]
            return sorted(jobs, key=key, reverse=self._sort["reverse"])
        # Default: unfinished downloads in queue order, then finished ones newest first.
        return sorted(jobs, key=lambda j: (0, order[j.job_id], 0) if j.status in ACTIVE_STATUSES
                      else (1, -(j.finished_at or j.created_at), order[j.job_id]))

    def _row_values(self, job):
        status = f"{STATUS_ICON.get(job.status, '')} {job.status}"
        if job.status == STATUS_FAILED and job.error_message:
            status += f": {job.error_message}"
        elif job.status == STATUS_RETRYING:
            status += f" (#{job.retries + 1})"
        speed = job.speed_text if (job.status in RUNNING_STATUSES) else ""
        eta = job.eta_text if job.status == STATUS_DOWNLOADING else ""
        return (truncate(job.display_name, 90), ENGINE_LABEL.get(job.engine, job.engine or ""), truncate(status, 60),
                progress_text(job), size_text(job), speed or "—", eta or "—", job_date_text(job))

    def _row_tags(self, job, idx):
        tags = ["row_a" if idx % 2 == 0 else "row_b"]
        if job.status in RUNNING_STATUSES:
            tags.append("running")
        elif job.status == STATUS_FAILED:
            tags.append("failed")
        elif job.status in (STATUS_COMPLETED, STATUS_CANCELLED):
            tags.append("done")
        return tuple(tags)

    def _rebuild_list(self):
        selected = {self._iid_job[i].job_id for i in self.tree.selection() if i in self._iid_job}
        jobs = self._visible_jobs()
        self.tree.delete(*self.tree.get_children())
        self._iid_job.clear()
        for idx, job in enumerate(jobs):
            iid = str(job.job_id)
            self.tree.insert("", "end", iid=iid, values=self._row_values(job), tags=self._row_tags(job, idx))
            self._iid_job[iid] = job
        keep = [str(j) for j in selected if str(j) in self._iid_job]
        if keep:
            self.tree.selection_set(keep)
        counts = len(jobs)
        self.view_title.configure(text=f"{self.view}  ·  {counts}")
        if jobs:
            self.empty_label.configure(text="")
        else:
            query = self.search_var.get().strip()
            text = (f"Nothing matches “{query}”." if query else
                    "No downloads yet.\nClick “+ Add URL” or press Ctrl+V to paste a link, magnet or torrent."
                    if self.view == "All" else f"Nothing in {self.view} yet.")
            self.empty_label.configure(text=text)
            self.empty_label.lift()
        self._update_selection_buttons()

    def _update_rows(self, job_ids):
        children = self.tree.get_children()
        index = {iid: i for i, iid in enumerate(children)}
        for job_id in job_ids:
            iid = str(job_id)
            job = self._iid_job.get(iid)
            if job is None or not self.tree.exists(iid):
                continue
            if not view_matches(self.view, job):
                self._mark_structure()
                continue
            self.tree.item(iid, values=self._row_values(job), tags=self._row_tags(job, index.get(iid, 0)))
        self._update_selection_buttons()

    def _sort_by(self, col):
        reverse = self._sort["column"] == col and not self._sort["reverse"]
        self._sort = {"column": col, "reverse": reverse}
        self._mark_structure()

    def selected_jobs(self):
        return [self._iid_job[i] for i in self.tree.selection() if i in self._iid_job]

    def _update_selection_buttons(self):
        state = "normal" if self.tree.selection() else "disabled"
        for b in self._sel_buttons:
            b.configure(state=state)

    # ============================================================= actions === #
    def act(self, action, jobs):
        """Apply an action to jobs, explaining (not hiding) anything refused."""
        problems = []
        for job in jobs:
            try:
                if action == "pause":
                    self.manager.pause(job.job_id)
                elif action == "resume":
                    self.manager.resume(job.job_id)
                elif action == "cancel":
                    self.manager.cancel(job.job_id)
                elif action == "retry":
                    if job.from_history:
                        self.open_add_dialog(job.url, analyze=True)
                    else:
                        self.manager.retry(job.job_id)
                elif action == "up":
                    self.manager.move_up(job.job_id)
                elif action == "down":
                    self.manager.move_down(job.job_id)
                elif action == "top":
                    self.manager.move_to_top(job.job_id)
                elif action == "bottom":
                    self.manager.move_to_bottom(job.job_id)
            except (ValueError, KeyError) as e:
                problems.append(f"{truncate(job.display_name, 40)}: {e}")
        if problems and len(jobs) == 1:
            messagebox.showinfo("Can't do that", problems[0])
        self._mark_structure()

    def act_on_selection(self, action):
        jobs = self.selected_jobs()
        if not jobs:
            return
        if action == "remove":
            self.remove_jobs(jobs)
        elif action == "folder":
            job = jobs[0]
            open_folder(job.download_dir, job.result_path)
        elif action in ("pause", "resume"):
            relevant = [j for j in jobs if (j.status == STATUS_PAUSED) == (action == "resume")]
            self.act(action, relevant or jobs)
        else:
            if action == "down":
                jobs = list(reversed(jobs))
            self.act(action, jobs)

    def remove_jobs(self, jobs):
        running = [j for j in jobs if j.status in RUNNING_STATUSES]
        if running and not messagebox.askyesno("Remove downloads", f"{len(running)} selected download(s) are active. "
                                               "Cancel and remove them? (Downloaded files are kept.)"):
            return
        for job in jobs:
            if job.status in RUNNING_STATUSES:
                self.manager.cancel(job.job_id)
                threading.Thread(target=self._remove_when_stopped, args=(job.job_id,), daemon=True).start()
                continue
            if job.from_history:
                self._remove_history_entry(job)
            try:
                self.manager.remove_job(job.job_id)
            except (ValueError, KeyError):
                pass
        self._mark_structure()

    def _remove_when_stopped(self, job_id):
        import time
        for _ in range(100):
            job = self.manager.get_job(job_id)
            if job is None or job.status not in RUNNING_STATUSES:
                break
            time.sleep(0.1)
        try:
            self.manager.remove_job(job_id)
        except (ValueError, KeyError):
            pass

    def _remove_history_entry(self, job):
        if job.history_id:
            self.history.remove_by_id(job.history_id)
        elif job.history_key:
            self.history.remove(*job.history_key)

    def _delete_file(self, job):
        path = job.result_path
        if not path or not os.path.isfile(path):
            messagebox.showinfo("Delete file", "There's no downloaded file at the recorded location.")
            return
        if not messagebox.askyesno("Delete file", f"Permanently delete this file from your disk?\n\n{path}",
                                   icon="warning", default="no"):
            return
        ok, message = delete_entry_file({"file": path})
        if ok:
            self._remove_history_entry(job)
            try:
                self.manager.remove_job(job.job_id)
            except (ValueError, KeyError):
                pass
        self.toast("success" if ok else "error", message)
        self._mark_structure()

    def _clear_completed(self):
        count = self.manager.clear_completed()
        self.toast("info", f"Cleared {count} completed row(s) — they stay in History.")

    def _retry_failed(self):
        count = self.manager.retry_failed()
        self.toast("info", f"Retrying {count} failed download(s)." if count else "Nothing to retry.")

    # ======================================================== context menu === #
    def build_context_menu(self, jobs):
        p = self.palette()
        menu = tk.Menu(self.root, tearoff=0, bg=p["surface"], fg=p["text"], activebackground=p["surface_hover"],
                       activeforeground=p["text"], bd=0)
        job = jobs[0]
        single = len(jobs) == 1
        has_file = bool(job.result_path) and os.path.exists(job.result_path)
        menu.add_command(label="Open file", command=lambda: open_file(job.result_path, self.root),
                         state="normal" if single and has_file else "disabled")
        menu.add_command(label="Show in folder", command=lambda: open_folder(job.download_dir, job.result_path))
        menu.add_command(label="Details…", command=lambda: self.open_details(job.job_id), state="normal" if single else "disabled")
        menu.add_command(label="Copy link", command=lambda: self._copy("\n".join(j.url for j in jobs)))
        menu.add_separator()
        statuses = {j.status for j in jobs}
        if statuses & set(RUNNING_STATUSES) or statuses & {STATUS_QUEUED, STATUS_RETRYING}:
            menu.add_command(label="Pause", command=lambda: self.act("pause", jobs))
        if STATUS_PAUSED in statuses:
            menu.add_command(label="Resume", command=lambda: self.act("resume", jobs))
        if statuses & set(ACTIVE_STATUSES):
            menu.add_command(label="Cancel", command=lambda: self.act("cancel", jobs))
        if any(j.status in (STATUS_FAILED, STATUS_CANCELLED) and not j.from_history for j in jobs):
            menu.add_command(label="Retry", command=lambda: self.act("retry", jobs))
        menu.add_command(label="Download again…", command=lambda: self.open_add_dialog(job.url, analyze=True),
                         state="normal" if single else "disabled")
        if statuses & {STATUS_QUEUED, STATUS_PAUSED}:
            menu.add_separator()
            menu.add_command(label="Move to top", command=lambda: self.act("top", list(reversed(jobs))))
            menu.add_command(label="Move up", command=lambda: self.act("up", jobs))
            menu.add_command(label="Move down", command=lambda: self.act("down", list(reversed(jobs))))
            menu.add_command(label="Move to bottom", command=lambda: self.act("bottom", jobs))
        menu.add_separator()
        menu.add_command(label="Remove from list" + (" and history" if any(j.from_history for j in jobs) else ""),
                         command=lambda: self.remove_jobs(jobs))
        if single and has_file and os.path.isfile(job.result_path) and job.status == STATUS_COMPLETED:
            menu.add_command(label="Delete file from disk…", command=lambda: self._delete_file(job))
        return menu

    def _on_right_click(self, event):
        iid = self.tree.identify_row(event.y)
        if not iid:
            return
        if iid not in self.tree.selection():
            self.tree.selection_set(iid)
        jobs = self.selected_jobs()
        if not jobs:
            return
        menu = self.build_context_menu(jobs)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _on_double_click(self, event):
        iid = self.tree.identify_row(event.y)
        job = self._iid_job.get(iid)
        if job is None:
            return
        if job.status == STATUS_COMPLETED and job.result_path and os.path.isfile(job.result_path):
            open_file(job.result_path, self.root)
        else:
            self.open_details(job.job_id)

    def _copy(self, text):
        self.root.clipboard_clear()
        self.root.clipboard_append(text or "")

    # ================================================================ misc === #
    def _bind_keys(self):
        self.root.bind("<Control-n>", lambda e: self.open_add_dialog())
        self.root.bind("<Control-v>", lambda e: self.paste_and_add() if not isinstance(e.widget, (tk.Entry, ctk.CTkEntry)) else None)
        self.tree.bind("<Delete>", lambda e: self.act_on_selection("remove"))
        self.tree.bind("<Return>", lambda e: self.selected_jobs() and self.open_details(self.selected_jobs()[0].job_id))
        self.root.bind("<Control-a>", lambda e: self.tree.selection_set(self.tree.get_children())
                       if not isinstance(e.widget, (tk.Entry, ctk.CTkEntry)) else None)

    def _build_status_bar(self):
        bar = ctk.CTkFrame(self.root, height=28, corner_radius=0, fg_color=COLORS["sidebar"])
        bar.grid(row=2, column=0, columnspan=2, sticky="ew")
        bar.grid_propagate(False)
        self._statusbar = bar
        ctk.CTkFrame(bar, height=1, corner_radius=0, fg_color=COLORS["border"]).place(relx=0, rely=0, relwidth=1.0, anchor="nw")
        self.status_left = ctk.CTkLabel(bar, text="", text_color=COLORS["muted"], font=font(11))
        self.status_left.pack(side="left", padx=14)
        self.status_right = ctk.CTkLabel(bar, text="", text_color=COLORS["muted"], font=font(11))
        self.status_right.pack(side="right", padx=14)
        self._engine_status_text = None

    def _refresh_status_bar(self):
        jobs = self.manager.list_jobs()
        downloading = [j for j in jobs if j.status in RUNNING_STATUSES]
        waiting = sum(1 for j in jobs if j.status in (STATUS_QUEUED, STATUS_RETRYING))
        total_speed = sum(j.speed_bps or 0 for j in downloading)
        parts = [f"{len(downloading)} downloading", f"{waiting} waiting",
                 f"{sum(1 for j in jobs if j.status == STATUS_COMPLETED)} completed"]
        if total_speed:
            parts.append(f"↓ {_format_speed(total_speed)}")
        self.status_left.configure(text="   ·   ".join(parts))
        if self._engine_status_text is None:
            status = tools.tool_status(self.settings.get("ffmpeg_path", ""))
            try:
                import libtorrent  # noqa: F401
                torrent_ok = True
            except ImportError:
                torrent_ok = False
            api = f"Browser link :{self.settings.get('api_port')} ●" if self.api_thread else "Browser link off ○"
            self._engine_status_text = "   ".join([
                api, "Media ●", f"FFmpeg {'●' if status['ffmpeg'] else '⚠'}", f"Torrent {'●' if torrent_ok else '⚠'}", "HTTP ●"])
        self.status_right.configure(text=self._engine_status_text)

    def toast(self, kind, message):
        try:
            glass_effects.show_toast(self.root, kind, message)
        except Exception:  # noqa: BLE001 - cosmetic only
            pass

    def _job_finished(self, job):
        notify = self.settings.get("notifications_enabled", True)
        if job.status == STATUS_COMPLETED:
            self.toast("success", f"Done — {truncate(job.display_name, 40)}")
            if notify:
                notifications.notify(f"{APP_NAME} — download complete", truncate(job.display_name, 60))
        elif job.status == STATUS_FAILED:
            self.toast("error", f"Failed — {truncate(job.display_name, 40)}")
            if notify:
                notifications.notify(f"{APP_NAME} — download failed",
                                     f"{truncate(job.display_name, 50)}\n{job.error_message or ''}")

    # ============================================================= dialogs === #
    def open_add_dialog(self, prefill_url=None, analyze=False):
        self.restore_window()
        return AddDialog(self, prefill_url=prefill_url, analyze=analyze)

    def paste_and_add(self):
        try:
            text = self.root.clipboard_get().strip()
        except tk.TclError:
            text = ""
        self.open_add_dialog(text if classify(text) else None, analyze=bool(classify(text)))

    def add_torrent_file(self):
        path = filedialog.askopenfilename(filetypes=[("Torrent files", "*.torrent")])
        if path:
            self.open_add_dialog(os.path.normpath(path), analyze=True)

    def add_magnet(self):
        try:
            text = self.root.clipboard_get().strip()
        except tk.TclError:
            text = ""
        is_magnet = text.lower().startswith("magnet:?")
        dialog = self.open_add_dialog(text if is_magnet else None, analyze=is_magnet)
        if not is_magnet:
            dialog.url_entry.configure(placeholder_text="magnet:?xt=urn:btih:…")

    def open_details(self, job_id):
        existing = self.details_windows.get(job_id)
        if existing is not None and existing.win.winfo_exists():
            existing.win.lift()
            return
        self.details_windows[job_id] = DetailsWindow(self, job_id)

    def open_settings(self, section="General"):
        existing = getattr(self, "_settings_window", None)
        if existing is not None and existing.win.winfo_exists():
            existing.win.lift()
            existing.show(section)
            return
        self._settings_window = SettingsWindow(self, section)

    def settings_changed(self):
        glass_theme.set_reduced_motion(self.settings.get("reduce_motion", False))
        self._engine_status_text = None
        self._refresh_status_bar()
        if self.settings.get("clipboard_monitor_enabled"):
            self.root.after(1500, self._poll_clipboard)

    def open_request(self, url):
        """A second launch (e.g. double-clicked .torrent, magnet link) asked us to open something."""
        self.restore_window()
        if url:
            self.open_add_dialog(url, analyze=True)

    # ===================================================== tray / lifetime === #
    def _start_local_api(self):
        try:
            self.api_thread, self.api_server = run_server(self.manager, self.settings,
                                                          on_open_request=lambda url: self.call_in_main(self.open_request, url))
        except Exception:  # noqa: BLE001
            log.exception("Failed to start the local API")
            self.api_thread = self.api_server = None

    def _setup_tray(self):
        if not tray.tray_supported():
            return
        self.tray_icon = tray.TrayIcon(
            on_open=lambda: self.call_in_main(self.restore_window),
            on_pause_all=lambda: self.manager.pause_all(),
            on_resume_all=lambda: self.manager.resume_all(),
            on_settings=lambda: self.call_in_main(self.open_settings),
            on_exit=lambda: self.call_in_main(self.quit_app),
        )
        self.tray_icon.start()

    def restore_window(self):
        self.root.deiconify()
        self.root.lift()
        try:
            self.root.focus_force()
        except tk.TclError:
            pass

    def _on_close_request(self):
        if self.settings.get("minimize_to_tray", True) and self.tray_icon is not None:
            self.root.withdraw()
            if not getattr(self, "_tray_hint_shown", False):
                self._tray_hint_shown = True
                notifications.notify(APP_NAME, "Still running in the system tray. Right-click the tray icon to exit.")
        else:
            self.quit_app()

    def quit_app(self):
        active = [j for j in self.manager.list_jobs() if j.status in RUNNING_STATUSES]
        if active and not messagebox.askyesno(
                "Exit", f"{len(active)} download(s) are running. Exit anyway? They'll be paused and can be resumed next time."):
            return
        self._closing = True
        try:
            self.manager.stop()
        except Exception:  # noqa: BLE001
            pass
        if self.api_server is not None:
            self.api_server.should_exit = True
        if self.tray_icon is not None:
            self.tray_icon.stop()
        self.root.destroy()

    # ========================================================== clipboard === #
    def _poll_clipboard(self):
        if self._closing or not self.settings.get("clipboard_monitor_enabled"):
            return
        try:
            text = self.root.clipboard_get().strip()
        except tk.TclError:
            text = ""
        if text and text != self._last_clipboard:
            self._last_clipboard = text
            if text not in self._clipboard_seen and self._is_download_link(text):
                self._clipboard_seen.add(text)
                self._clipboard_prompt(text)
        self.root.after(1500, self._poll_clipboard)

    @staticmethod
    def _is_download_link(text):
        if len(text) > 4000 or "\n" in text:
            return False
        engine = classify(text)
        if engine in ("torrent", "http"):
            return True
        if engine == "media":
            from ..providers.media import matching_extractor
            return matching_extractor(text) is not None
        return False

    def _clipboard_prompt(self, url):
        popup = ctk.CTkToplevel(self.root)
        popup.title(APP_NAME)
        popup.geometry(f"380x150+{self.root.winfo_x() + 60}+{self.root.winfo_y() + 70}")
        popup.attributes("-topmost", True)
        self.apply_glass_window(popup)
        card = glass_card(popup, elevated=True)
        card.pack(fill="both", expand=True, padx=10, pady=10)
        ctk.CTkLabel(card, text="Download the link you copied?", font=font(13, "bold")).pack(pady=(16, 4))
        ctk.CTkLabel(card, text=truncate(url, 52), text_color=COLORS["muted"]).pack()
        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(pady=12)

        def go():
            popup.destroy()
            self.open_add_dialog(url, analyze=True)
        glass_button(row, "Download…", go, variant="primary", width=110).pack(side="left", padx=6)
        glass_button(row, "Ignore", popup.destroy, width=100).pack(side="left", padx=6)
        popup.after(9000, lambda: popup.winfo_exists() and popup.destroy())
        fade_in_toplevel(popup)
