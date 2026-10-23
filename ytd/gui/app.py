"""Master Download Manager — main window.

Layout (modelled on AB Download Manager, with MDM's own branding):
    toolbar      New Download · Paste · Resume · Pause · Start/Stop queue · Queues · Stop all · Delete · Settings · search
    sidebar      category tree with counts (All / file types / Torrents / Media sites), Finished / Unfinished /
                 Failed, and the download queues
    list         virtualised DataGrid: progress bars, sortable/resizable/hideable columns, multi-select,
                 drag-and-drop reordering
    status bar   active / queued / finished, total ↓ and ↑ speed, speed-limit indicator (click: limited mode)

Threading model: engines and the local API run on background threads and
never touch Tk. They report through DownloadManager.subscribe(); the
callback only records *which* jobs changed. A short main-thread timer
(`_pump`) drains those marks plus any call_in_main() requests and redraws
only what's visible, so the UI stays responsive with thousands of rows and
many simultaneous downloads.
"""

import datetime
import gc
import os
import queue
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox
from urllib.parse import urlparse

import customtkinter as ctk

from .. import bandwidth, notifications, power, tray
from ..api.server import run_server
from ..categories import MEDIA_SITES, TORRENTS, extension_of, is_media_site, job_category
from .. import __version__
from ..config import APP_ICON_FILE, APP_NAME, INSTALL_DIR, QUEUE_FILE, QUEUES_FILE, SCHEDULER_FILE
from ..engine import DownloadManager, _format_speed
from ..history import HistoryStore, delete_entry_file
from ..logging_setup import get_logger
from ..models import (
    ACTIVE_STATUSES, PRIORITY_HIGH, PRIORITY_LABELS, PRIORITY_LOW, PRIORITY_NORMAL, RUNNING_STATUSES,
    STATUS_CANCELLED, STATUS_COMPLETED, STATUS_DOWNLOADING, STATUS_FAILED, STATUS_PAUSED, STATUS_PROCESSING,
    STATUS_QUEUED, STATUS_RETRYING, STATUS_SEEDING, TERMINAL_STATUSES,
)
from ..queues import MAIN_QUEUE_ID, QueueStore
from ..router import classify
from ..scheduler import Scheduler, SchedulerStore
from ..settings_store import SettingsStore
from .. import tools
from . import effects as glass_effects
from . import icons
from . import theme as glass_theme
from .add_dialog import AddDialog
from .common import ENGINE_LABEL, human_size, job_date_text, open_file, open_folder, truncate
from .datagrid import DataGrid
from .details import DetailsWindow
from .settings_window import SettingsWindow
from .sidebar import CATEGORY_ICONS, Sidebar
from .theme import COLORS, color, font, glass_card, glass_button

log = get_logger()

PUMP_MS = 120
GC_INTERVAL_MS = 1000  # see _collect_garbage
COUNTS_EVERY_S = 0.8

COLUMNS = [
    dict(key="name", title="Name", width=300, min=180, kind="name", stretch=True, required=True),
    dict(key="type", title="Type", width=70, visible=False),
    dict(key="size", title="Size", width=92, anchor="e"),
    dict(key="downloaded", title="Downloaded", width=96, anchor="e", visible=False),
    dict(key="progress", title="Progress", width=170, kind="progress"),
    dict(key="status", title="Status", width=150, kind="status"),
    dict(key="speed", title="Speed", width=88, anchor="e"),
    dict(key="eta", title="Time left", width=82, anchor="e"),
    dict(key="connections", title="Conn.", width=58, anchor="center", visible=False),
    dict(key="engine", title="Engine", width=70, visible=False, muted=True),
    dict(key="queue", title="Queue", width=96, visible=False, muted=True),
    dict(key="added", title="Date added", width=128, muted=True),
    dict(key="completed", title="Completed", width=128, visible=False, muted=True),
    dict(key="source", title="Source", width=150, visible=False, muted=True),
]
for _c in COLUMNS:
    _c["default_width"], _c["default_visible"] = _c["width"], _c.get("visible", True)

DATE_FILTERS = ("Any time", "Today", "Last 7 days", "Last 30 days")
VIEW_TITLES = {"all": "All Downloads", "status:finished": "Finished", "status:unfinished": "Unfinished",
               "status:failed": "Failed"}


def view_matches(view, job, queues_by_id=None):
    if view == "all":
        return True
    kind, _, arg = view.partition(":")
    if kind == "status":
        if arg == "finished":
            return job.status == STATUS_COMPLETED
        if arg == "unfinished":
            return job.status in ACTIVE_STATUSES
        if arg == "failed":
            return job.status in (STATUS_FAILED, STATUS_CANCELLED)
    if kind == "cat":
        if arg == MEDIA_SITES:
            return is_media_site(job)
        return job_category(job) == arg
    if kind == "queue":
        if job.from_history or job.status in TERMINAL_STATUSES:
            return False
        qid = job.queue_id if (queues_by_id is None or job.queue_id in queues_by_id) else MAIN_QUEUE_ID
        return qid == arg
    return True


def _job_state(job):
    if job.status in (STATUS_DOWNLOADING, STATUS_PROCESSING, STATUS_SEEDING) or job.status == "Preparing":
        return "active"
    if job.status == STATUS_COMPLETED:
        return "done"
    if job.status in (STATUS_FAILED,):
        return "failed"
    if job.status in (STATUS_PAUSED, STATUS_RETRYING):
        return "paused"
    return "idle"


def _fmt_time(ts):
    if not ts:
        return ""
    return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


class ToolButton(tk.Frame):
    """Icon-over-label toolbar button (flat, with hover and disabled states)."""

    def __init__(self, parent, app, glyph, text, command, accent=False):
        super().__init__(parent, bd=0, highlightthickness=0, cursor="hand2")
        self.app, self.command, self.accent, self.enabled = app, command, accent, True
        s = app.scale
        self.icon = tk.Label(self, text=icons.glyph(glyph), font=icons.icon_font(13), bd=0)
        self.icon.pack(pady=(round(5 * s), 0), padx=round(10 * s))
        self.label = tk.Label(self, text=text, font=("Segoe UI", 8), bd=0)
        self.label.pack(pady=(round(1 * s), round(5 * s)), padx=round(8 * s))
        self._hover = False
        for w in (self, self.icon, self.label):
            w.bind("<Enter>", lambda e: self._set_hover(True))
            w.bind("<Leave>", lambda e: self._set_hover(False))
            w.bind("<ButtonRelease-1>", lambda e: self.enabled and self.command())
        self.paint()

    def _set_hover(self, on):
        self._hover = on
        self.paint()

    def set_enabled(self, enabled):
        if enabled != self.enabled:
            self.enabled = enabled
            self.configure(cursor="hand2" if enabled else "")
            self.paint()

    def paint(self):
        c = self.app.c
        bg = c["surface_hover"] if (self._hover and self.enabled) else c["toolbar"]
        fg = (c["primary"] if self.accent else c["text"]) if self.enabled else c["faint"]
        for w in (self, self.icon, self.label):
            w.configure(bg=bg)
        self.icon.configure(fg=fg)
        self.label.configure(fg=c["text"] if self.enabled else c["faint"])


class MasterDownloadManagerApp:
    def __init__(self, root):
        self.root = root
        root.title(APP_NAME)
        root.geometry("1240x760")
        root.minsize(980, 600)

        self.settings = SettingsStore()
        self.history = HistoryStore()
        self.manager = DownloadManager(self.settings, self.history, queue_file=QUEUE_FILE,
                                       queue_store=QueueStore(QUEUES_FILE))
        self.manager.seed_from_history(self.history.all())
        if self.settings.get("restore_queue_on_start", True):
            self.manager.restore_queue()
        self.scheduler = Scheduler(self.manager, SchedulerStore(SCHEDULER_FILE))

        self.appearance_mode = self.settings.get("appearance_mode", "Dark")
        if self.appearance_mode not in glass_theme.APPEARANCE_MODES:
            self.appearance_mode = "Dark"
        glass_theme.apply_palette(self.appearance_mode, self.settings.get("accent_color", "Indigo"))
        glass_theme.install_ctk_theme()
        if os.path.isfile(APP_ICON_FILE):
            try:
                root.iconbitmap(APP_ICON_FILE)
            except tk.TclError:
                pass
        icons.family(root)
        try:
            self.scale = ctk.ScalingTracker.get_widget_scaling(root)
        except Exception:  # noqa: BLE001
            self.scale = 1.0

        self.view = "all"
        self.details_windows = {}
        self._dirty = set()
        self._structure_dirty = True
        self._dirty_lock = threading.Lock()
        self._ui_calls = queue.Queue()
        self._sort = {"column": None, "reverse": False}
        self._closing = False
        self._last_clipboard = ""
        self._clipboard_seen = set()
        self._last_counts_at = 0.0
        self._power_dialog = None
        self.tray_icon = None
        self.api_thread = self.api_server = None
        self._engine_status_text = None

        self._build_ui()

        self.manager.subscribe(self._on_engine_event)
        self.manager.start()
        self.scheduler.start()
        if self.settings.get("api_enabled", True):
            self._start_local_api()
        self._setup_tray()
        root.protocol("WM_DELETE_WINDOW", self._on_close_request)
        if self.settings.get("clipboard_monitor_enabled"):
            root.after(1500, self._poll_clipboard)
        root.after(PUMP_MS, self._pump)
        # Tk objects (fonts, images, variables) must only be freed on the Tk
        # thread. Automatic garbage collection can run in any thread that
        # happens to allocate - e.g. the update check - and freeing a Font there
        # deadlocks that thread. Collect cycles here, on the main loop, instead.
        gc.disable()
        self._gc_ticks = 0
        root.after(GC_INTERVAL_MS, self._collect_garbage)

    # ======================================================= construction === #
    def _colors(self):
        self.c = {k: color(k) for k in ("background", "sidebar", "surface", "surface_hover", "text", "muted",
                                        "faint", "primary", "border", "success", "warning", "danger", "selected")}
        self.c["toolbar"] = self.c["sidebar"]

    def _configure_ttk_style(self):
        """ttk widgets still used inside dialogs (checklists for torrent files,
        playlist items, subtitles, batch links) follow the theme too."""
        from tkinter import ttk
        c = self.c
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Treeview", background=c["background"], foreground=c["text"], fieldbackground=c["background"],
                        bordercolor=c["border"], lightcolor=c["background"], darkcolor=c["background"],
                        borderwidth=0, rowheight=round(28 * self.scale), font=("Segoe UI", 10), relief="flat")
        style.layout("Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
        style.configure("Treeview.Heading", background=c["surface"], foreground=c["muted"], relief="flat",
                        bordercolor=c["border"], lightcolor=c["surface"], darkcolor=c["surface"], padding=(8, 6),
                        font=("Segoe UI Semibold", 10))
        style.map("Treeview", background=[("selected", c["selected"])], foreground=[("selected", c["text"])])
        style.map("Treeview.Heading", background=[("active", c["surface_hover"])], foreground=[("active", c["text"])])
        style.configure("Glass.Vertical.TScrollbar", background=c["border"], troughcolor=c["background"],
                        bordercolor=c["background"], arrowsize=0, relief="flat", width=8)
        style.map("Glass.Vertical.TScrollbar", background=[("active", c["muted"])])

    def _build_ui(self):
        self._colors()
        self._configure_ttk_style()
        root = self.root
        root.configure(fg_color=COLORS["background"])
        root.grid_rowconfigure(1, weight=0)
        root.grid_rowconfigure(2, weight=1)
        root.grid_columnconfigure(1, weight=1)
        from .menubar import MenuBar
        self.menubar = MenuBar(root, self)
        self.menubar.grid(row=0, column=0, columnspan=2, sticky="ew")
        self._build_toolbar()
        self.sidebar = Sidebar(root, self, width=212)
        self.sidebar.grid(row=2, column=0, sticky="ns")
        self._build_main()
        self._build_status_bar()
        self._bind_keys()
        self.sidebar.select(self.view if self.view in self.sidebar.items else "all")
        from .backdrop import apply_glass_backdrop, set_titlebar_theme
        dark = glass_theme.is_dark()
        root.after(50, lambda: set_titlebar_theme(root, dark, self.c["toolbar"], self.c["text"]))
        if self.settings.get("mica_backdrop"):
            # Tk paints its own opaque client area, so Mica shows in the title bar/frame only.
            root.after(200, lambda: apply_glass_backdrop(root, mica=True, dark=dark))

    def _rebuild_ui(self):
        """Theme change: rebuild the main window's widgets in the new colours.
        Downloads keep running; only the views are recreated."""
        for w in (self.menubar, self._toolbar, self.sidebar, self._main, self._statusbar):
            w.destroy()
        self._build_ui()
        self._mark_structure()

    # ============================================================ toolbar === #
    def _build_toolbar(self):
        c, s = self.c, self.scale
        bar = tk.Frame(self.root, bg=c["toolbar"], height=round(62 * s), bd=0, highlightthickness=0)
        bar.grid(row=1, column=0, columnspan=2, sticky="ew")
        bar.pack_propagate(False)
        self._toolbar = bar
        tk.Frame(bar, bg=c["border"], height=1).place(relx=0, rely=1.0, relwidth=1.0, anchor="sw")

        brand = tk.Frame(bar, bg=c["toolbar"])
        brand.pack(side="left", padx=(round(14 * s), round(10 * s)))
        badge = ctk.CTkLabel(brand, text="MDM", width=40, height=32, corner_radius=8, fg_color=COLORS["primary"],
                             text_color=COLORS["on_primary"], font=font(12, "bold"))
        badge.pack(side="left")

        new = ctk.CTkButton(bar, text=f"{icons.glyph('add')}  New Download", height=36, corner_radius=10,
                            font=ctk.CTkFont(family="Segoe UI Semibold", size=12),
                            command=lambda: self.open_add_dialog(),
                            fg_color=COLORS["primary"], hover_color=COLORS["primary_hover"],
                            text_color=COLORS["on_primary"])
        new.pack(side="left", padx=(round(6 * s), round(10 * s)))

        self.tb = {}

        def add(key, glyph, text, cmd, accent=False):
            b = ToolButton(bar, self, glyph, text, cmd, accent)
            b.pack(side="left", padx=1)
            self.tb[key] = b

        def sep():
            tk.Frame(bar, bg=c["border"], width=1, height=round(30 * s)).pack(side="left", padx=round(6 * s))

        add("paste", "paste", "Paste URL", self.paste_and_add)
        sep()
        add("resume", "play", "Resume", lambda: self.act_on_selection("resume"))
        add("pause", "pause", "Pause", lambda: self.act_on_selection("pause"))
        sep()
        add("start_queue", "play", "Start Queue", lambda: self.start_current_queue())
        add("stop_queue", "stop", "Stop Queue", lambda: self.stop_current_queue())
        add("queues", "queue", "Queues", self.open_queues)
        sep()
        add("stop_all", "stop", "Stop All", self.stop_all)
        add("delete", "delete", "Delete", lambda: self.act_on_selection("remove"))
        sep()
        add("settings", "settings", "Settings", self.open_settings)

        right = tk.Frame(bar, bg=c["toolbar"])
        right.pack(side="right", padx=round(14 * s))
        if not hasattr(self, "search_var"):
            self.search_var = tk.StringVar()
            self.search_var.trace_add("write", lambda *_: self._mark_structure())
        self.search_entry = ctk.CTkEntry(right, placeholder_text=f"{icons.glyph('search')}  Search downloads",
                                         width=230, height=34, corner_radius=10, font=font(12),
                                         fg_color=COLORS["surface_deep"], border_color=COLORS["border"])
        self.search_entry.pack(side="right")
        glass_theme._sync_entry_with_variable(self.search_entry, self.search_var)

    # ================================================================ main === #
    def _build_main(self):
        main = ctk.CTkFrame(self.root, corner_radius=0, fg_color=COLORS["background"])
        main.grid(row=2, column=1, sticky="nsew")
        main.grid_rowconfigure(1, weight=1)
        main.grid_columnconfigure(0, weight=1)
        self._main = main

        head = ctk.CTkFrame(main, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=16, pady=(12, 6))
        self.view_title = ctk.CTkLabel(head, text="", font=font(16, "bold"))
        self.view_title.pack(side="left")
        self.view_sub = ctk.CTkLabel(head, text="", font=font(12), text_color=COLORS["muted"])
        self.view_sub.pack(side="left", padx=(10, 0))
        if not hasattr(self, "date_filter"):
            self.date_filter = tk.StringVar(value=DATE_FILTERS[0])
        ctk.CTkOptionMenu(head, values=list(DATE_FILTERS), variable=self.date_filter, width=128, height=28,
                          font=font(11), command=lambda _v: self._mark_structure()).pack(side="right")
        glass_button(head, "Clear completed", self._clear_completed, variant="ghost", width=116, height=28,
                     size=11).pack(side="right", padx=4)
        glass_button(head, "Retry failed", self._retry_failed, variant="ghost", width=96, height=28,
                     size=11).pack(side="right", padx=4)

        card = glass_card(main, radius="lg", fg_color=COLORS["background"])
        card.grid(row=1, column=0, sticky="nsew", padx=(12, 12), pady=(0, 8))
        card.grid_rowconfigure(0, weight=1)
        card.grid_columnconfigure(0, weight=1)
        self.grid = DataGrid(card, COLUMNS, self._cell, key=lambda j: j.job_id,
                             on_select=lambda rows: self._update_buttons(), on_activate=self._activate,
                             on_context=self._on_context, on_sort=self._sort_by, on_reorder=self._on_reorder,
                             on_columns_changed=lambda st: self.settings.set("visible_columns", st),
                             on_key=lambda k: k == "delete" and self.act_on_selection("remove"))
        self.grid.grid(row=0, column=0, sticky="nsew", padx=6, pady=6)
        self.grid.restore_column_state(self.settings.get("visible_columns") or [])
        self.grid.set_sort(self._sort["column"], self._sort["reverse"])

    # ============================================================== cells === #
    def _cell(self, job, key):
        if key == "name":
            cat = TORRENTS if job.engine == "torrent" else job_category(job)
            glyph = icons.glyph(CATEGORY_ICONS.get(cat, "file"))
            return glyph, job.display_name or job.url
        if key == "type":
            ext = extension_of(job.result_path or job.filename_override or "")
            return ext.upper() if ext else {"video": "Video", "audio": "Audio", "torrent": "Torrent"}.get(job.kind, "")
        if key == "size":
            return human_size(job.total_bytes, "") if job.total_bytes else ("" if job.status != STATUS_COMPLETED else "—")
        if key == "downloaded":
            return human_size(job.downloaded_bytes, "") if job.downloaded_bytes else ""
        if key == "progress":
            state = _job_state(job)
            if job.status == STATUS_COMPLETED:
                return 1.0, "100%", "done"
            frac = (job.progress or 0) / 100.0
            label = f"{job.progress:.0f}%" if job.total_bytes or job.progress else ""
            return frac, label, state
        if key == "status":
            text = job.status
            if job.status == STATUS_FAILED and job.error_message:
                text = f"Failed: {job.error_message}"
            elif job.status == STATUS_RETRYING:
                text = f"Retrying (#{job.retries + 1})"
            elif job.status == STATUS_QUEUED and job.start_at and job.start_at > time.time():
                text = f"Scheduled {datetime.datetime.fromtimestamp(job.start_at):%a %H:%M}"
            elif job.status == STATUS_QUEUED and not self._queue_running(job):
                text = "Waiting for queue"
            elif job.status == STATUS_SEEDING and job.speed_text:
                text = "Seeding"
            if job.priority == PRIORITY_HIGH and job.status in ACTIVE_STATUSES:
                text += "  ↑"
            return text, _job_state(job)
        if key == "speed":
            if job.status in RUNNING_STATUSES:
                return job.speed_text or "—"
            return ""
        if key == "eta":
            return job.eta_text if job.status == STATUS_DOWNLOADING else ""
        if key == "connections":
            return str(job.connections) if job.connections else ""
        if key == "engine":
            return ENGINE_LABEL.get(job.engine, job.engine or "")
        if key == "queue":
            q = self.manager.queues.get(job.queue_id)
            return q.name if q else ""
        if key == "added":
            return job_date_text(job)
        if key == "completed":
            return _fmt_time(job.finished_at) if job.status == STATUS_COMPLETED and not job.from_history else (
                job_date_text(job) if job.from_history and job.status == STATUS_COMPLETED else "")
        if key == "source":
            if job.url.startswith("magnet:"):
                return "magnet link"
            try:
                return urlparse(job.url).hostname or os.path.basename(job.url)
            except ValueError:
                return ""
        return ""

    def _queue_running(self, job):
        q = self.manager.queues.resolve(job.queue_id)
        return q.running

    # ========================================================= list logic === #
    def _visible_jobs(self):
        jobs = self.manager.list_jobs()
        order = {j.job_id: i for i, j in enumerate(jobs)}
        queues = {q.id: q for q in self.manager.queues.all()}
        jobs = [j for j in jobs if view_matches(self.view, j, queues)]
        query = self.search_var.get().strip().lower()
        if query:
            jobs = [j for j in jobs if query in (j.display_name or "").lower() or query in j.url.lower()]
        since = {"Today": 0, "Last 7 days": 7, "Last 30 days": 30}.get(self.date_filter.get())
        if since is not None:
            cutoff = datetime.datetime.combine(datetime.date.today() - datetime.timedelta(days=since),
                                               datetime.time()).timestamp()
            jobs = [j for j in jobs if self._job_time(j) >= cutoff]
        col = self._sort["column"]
        if col:
            keyfn = {
                "name": lambda j: (j.display_name or "").lower(), "engine": lambda j: j.engine or "",
                "status": lambda j: j.status, "progress": lambda j: j.progress or 0,
                "size": lambda j: j.total_bytes or 0, "downloaded": lambda j: j.downloaded_bytes or 0,
                "speed": lambda j: j.speed_bps or 0, "eta": lambda j: j.eta_seconds if j.eta_seconds is not None else 1e18,
                "added": self._job_time, "completed": lambda j: j.finished_at or 0,
                "type": lambda j: extension_of(j.result_path or ""), "connections": lambda j: j.connections or 0,
                "queue": lambda j: j.queue_id or "", "source": lambda j: j.url,
            }.get(col, lambda j: 0)
            return sorted(jobs, key=keyfn, reverse=self._sort["reverse"])
        # Default: unfinished downloads in queue order, then finished ones newest first.
        return sorted(jobs, key=lambda j: (0, order[j.job_id], 0) if j.status in ACTIVE_STATUSES
                      else (1, -(j.finished_at or j.created_at), order[j.job_id]))

    @staticmethod
    def _job_time(job):
        if job.from_history:
            date = getattr(job, "history_date", None)
            if date:
                try:
                    return datetime.datetime.strptime(date, "%Y-%m-%d %H:%M").timestamp()
                except ValueError:
                    pass
        return job.created_at

    def _rebuild_list(self):
        jobs = self._visible_jobs()
        self.grid.set_rows(jobs)
        title = VIEW_TITLES.get(self.view)
        if title is None:
            kind, _, arg = self.view.partition(":")
            if kind == "queue":
                q = self.manager.queues.get(arg)
                title = f"Queue · {q.name}" if q else "Queue"
            else:
                title = arg
        self.view_title.configure(text=title)
        self.view_sub.configure(text=f"{len(jobs):,} item{'s' if len(jobs) != 1 else ''}")
        query = self.search_var.get().strip()
        if jobs:
            self.grid.set_empty_text("")
        elif query:
            self.grid.set_empty_text(f"Nothing matches “{query}”.")
        elif self.view == "all":
            self.grid.set_empty_text("No downloads yet.\nClick New Download or press Ctrl+V to paste a link, "
                                     "magnet or .torrent.")
        else:
            self.grid.set_empty_text("Nothing here yet.")
        self._update_buttons()

    def _refresh_counts(self):
        jobs = self.manager.list_jobs()
        queues = {q.id: q for q in self.manager.queues.all()}
        counts = {"all": len(jobs)}
        for j in jobs:
            cat = TORRENTS if j.engine == "torrent" else job_category(j)
            counts[f"cat:{cat}"] = counts.get(f"cat:{cat}", 0) + 1
            if is_media_site(j):
                counts[f"cat:{MEDIA_SITES}"] = counts.get(f"cat:{MEDIA_SITES}", 0) + 1
            if j.status == STATUS_COMPLETED:
                counts["status:finished"] = counts.get("status:finished", 0) + 1
            elif j.status in ACTIVE_STATUSES:
                counts["status:unfinished"] = counts.get("status:unfinished", 0) + 1
                qid = j.queue_id if j.queue_id in queues else MAIN_QUEUE_ID
                counts[f"queue:{qid}"] = counts.get(f"queue:{qid}", 0) + 1
            elif j.status in (STATUS_FAILED, STATUS_CANCELLED):
                counts["status:failed"] = counts.get("status:failed", 0) + 1
        missing = {f"queue:{q}" for q in queues} - set(self.sidebar.items)
        if missing or len([k for k in self.sidebar.items if k.startswith("queue:")]) != len(queues):
            self.sidebar.rebuild()
        self.sidebar.update_counts(counts, {q.id for q in queues.values() if q.running})
        self._last_counts_at = time.monotonic()

    def set_view(self, view):
        self.view = view
        self._mark_structure()

    def _mark_structure(self):
        with self._dirty_lock:
            self._structure_dirty = True

    def _sort_by(self, col):
        if self._sort["column"] != col:
            self._sort = {"column": col, "reverse": col in ("size", "speed", "added", "completed", "progress")}
        elif self._sort["reverse"] == (col in ("size", "speed", "added", "completed", "progress")):
            self._sort["reverse"] = not self._sort["reverse"]
        else:
            self._sort = {"column": None, "reverse": False}  # third click: back to queue order (drag enabled)
        self.grid.set_sort(self._sort["column"], self._sort["reverse"])
        self._mark_structure()

    def _on_reorder(self, jobs, target_index):
        rows = self.grid.rows
        moving = {j.job_id for j in jobs if j.status in ACTIVE_STATUSES and not j.from_history}
        if not moving:
            return
        before = next((r.job_id for r in rows[target_index:] if r.job_id not in moving), None)
        self.manager.move_before([j.job_id for j in jobs if j.job_id in moving], before)
        self._mark_structure()

    def selected_jobs(self):
        return self.grid.selected()

    def _update_buttons(self):
        jobs = self.selected_jobs()
        statuses = {j.status for j in jobs}
        tb = self.tb
        tb["resume"].set_enabled(any(j.status == STATUS_PAUSED or (j.status in (STATUS_FAILED, STATUS_CANCELLED)
                                                                    and not j.from_history) for j in jobs))
        tb["pause"].set_enabled(bool(statuses & (set(RUNNING_STATUSES) | {STATUS_QUEUED, STATUS_RETRYING})))
        tb["delete"].set_enabled(bool(jobs))
        q = self._current_queue()
        all_jobs = self.manager.list_jobs()
        has_items = any(j.queue_id == q.id and j.status in (STATUS_QUEUED, STATUS_PAUSED) for j in all_jobs)
        tb["start_queue"].set_enabled(not q.running or has_items)
        tb["stop_queue"].set_enabled(q.running)
        tb["stop_all"].set_enabled(any(j.status in RUNNING_STATUSES or j.status in (STATUS_QUEUED, STATUS_RETRYING)
                                       for j in all_jobs))

    # =========================================================== pump/bridge === #
    def call_in_main(self, fn, *args):
        """Thread-safe: run fn(*args) on the Tk main thread soon."""
        self._ui_calls.put((fn, args))

    def _on_engine_event(self, event, job):
        """Background thread — only records what changed."""
        if event in ("queue_complete", "queue_changed"):
            if event == "queue_complete":
                self.call_in_main(self._queue_complete, job)
            self._mark_structure()
            return
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

    def _collect_garbage(self):
        self._gc_ticks += 1
        gc.collect(2 if self._gc_ticks % 60 == 0 else 1 if self._gc_ticks % 10 == 0 else 0)
        if not self._closing:
            self.root.after(GC_INTERVAL_MS, self._collect_garbage)

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
        try:
            if structure:
                self._rebuild_list()
            elif dirty:
                if self._sort["column"] in ("speed", "progress", "eta", "status"):
                    self._rebuild_list()  # live-sorted columns re-sort as values change
                else:
                    self.grid.refresh(dirty)
            if structure or dirty:
                self._refresh_status_bar()
                if structure or time.monotonic() - self._last_counts_at > COUNTS_EVERY_S:
                    self._refresh_counts()
                    self._update_buttons()
        except tk.TclError:
            pass  # window being rebuilt/destroyed
        self.root.after(PUMP_MS, self._pump)

    # ============================================================= actions === #
    def act(self, action, jobs):
        """Apply an action to jobs, explaining (not hiding) anything refused."""
        problems = []
        for job in jobs:
            try:
                if action == "pause":
                    self.manager.pause(job.job_id)
                elif action == "resume":
                    if job.status in (STATUS_FAILED, STATUS_CANCELLED):
                        self.manager.retry(job.job_id)
                    else:
                        self.manager.resume(job.job_id)
                elif action == "cancel":
                    self.manager.cancel(job.job_id)
                elif action == "retry":
                    if job.from_history:
                        self.open_add_dialog(job.url, analyze=True)
                    else:
                        self.manager.retry(job.job_id)
                elif action == "restart":
                    self.manager.restart(job.job_id)
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
        elif action == "pause":
            self.act(action, [j for j in jobs if j.status in RUNNING_STATUSES or j.status in (STATUS_QUEUED, STATUS_RETRYING)])
        elif action == "resume":
            self.act(action, [j for j in jobs if j.status == STATUS_PAUSED or (
                j.status in (STATUS_FAILED, STATUS_CANCELLED) and not j.from_history)])
        else:
            if action == "down":
                jobs = list(reversed(jobs))
            self.act(action, jobs)

    def stop_all(self):
        """Stop everything that's running or waiting (kept resumable)."""
        self.manager.pause_all()
        self._mark_structure()

    def remove_jobs(self, jobs):
        running = [j for j in jobs if j.status in RUNNING_STATUSES]
        text = (f"Remove {len(jobs)} download(s) from the list?" if len(jobs) > 1 else
                f"Remove “{truncate(jobs[0].display_name, 60)}” from the list?")
        text += "\n\nDownloaded files are kept on disk."
        if running:
            text += f"\n{len(running)} of them are downloading and will be cancelled."
        if not messagebox.askyesno("Remove downloads", text, default="yes"):
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

    def _activate(self, job):
        if job.status == STATUS_COMPLETED and job.result_path and os.path.isfile(job.result_path):
            open_file(job.result_path, self.root)
        else:
            self.open_details(job.job_id)

    def _copy(self, text):
        self.root.clipboard_clear()
        self.root.clipboard_append(text or "")

    # ======================================================== context menu === #
    def _menu(self):
        c = self.c
        return tk.Menu(self.root, tearoff=0, bg=c["surface"], fg=c["text"], activebackground=c["surface_hover"],
                       activeforeground=c["text"], bd=0, font=("Segoe UI", 10))

    def build_context_menu(self, jobs):
        menu = self._menu()
        job = jobs[0]
        single = len(jobs) == 1
        has_file = bool(job.result_path) and os.path.exists(job.result_path)
        is_file = has_file and os.path.isfile(job.result_path)
        menu.add_command(label="Open", command=lambda: open_file(job.result_path, self.root),
                         state="normal" if single and has_file else "disabled")
        menu.add_command(label="Open with…", command=lambda: self._open_with(job.result_path),
                         state="normal" if single and is_file else "disabled")
        menu.add_command(label="Open containing folder", command=lambda: open_folder(job.download_dir, job.result_path))
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
        if any(not j.from_history and j.status not in RUNNING_STATUSES for j in jobs):
            menu.add_command(label="Restart from zero", command=lambda: self._confirm_restart(jobs))
        menu.add_command(label="Download again…", command=lambda: self.open_add_dialog(job.url, analyze=True),
                         state="normal" if single else "disabled")
        active = [j for j in jobs if j.status in ACTIVE_STATUSES and not j.from_history]
        if active:
            menu.add_separator()
            qmenu = self._menu()
            for q in self.manager.queues.all():
                qmenu.add_command(label=q.name, command=lambda qid=q.id: self._move_to_queue(active, qid))
            qmenu.add_separator()
            qmenu.add_command(label="New queue…", command=lambda: self.new_queue(move_jobs=active))
            menu.add_cascade(label="Move to queue", menu=qmenu)
            pmenu = self._menu()
            for value in (PRIORITY_HIGH, PRIORITY_NORMAL, PRIORITY_LOW):
                pmenu.add_command(label=PRIORITY_LABELS[value] + ("  ✓" if single and job.priority == value else ""),
                                  command=lambda v=value: [self.manager.set_priority(j.job_id, v) for j in active])
            menu.add_cascade(label="Priority", menu=pmenu)
            menu.add_command(label="Speed limit…", command=lambda: self._ask_speed_limit(active))
            menu.add_command(label="Schedule start…", command=lambda: self._ask_start_time(active))
            if statuses & {STATUS_QUEUED, STATUS_PAUSED}:
                order = self._menu()
                order.add_command(label="Move to top", command=lambda: self.act("top", list(reversed(jobs))))
                order.add_command(label="Move up", command=lambda: self.act("up", jobs))
                order.add_command(label="Move down", command=lambda: self.act("down", list(reversed(jobs))))
                order.add_command(label="Move to bottom", command=lambda: self.act("bottom", jobs))
                menu.add_cascade(label="Order", menu=order)
        menu.add_separator()
        menu.add_command(label="Copy link", command=lambda: self._copy("\n".join(j.url for j in jobs)))
        menu.add_command(label="Copy file path", state="normal" if any(j.result_path for j in jobs) else "disabled",
                         command=lambda: self._copy("\n".join(j.result_path for j in jobs if j.result_path)))
        menu.add_command(label="Properties…", command=lambda: self.open_details(job.job_id),
                         state="normal" if single else "disabled")
        if single and job.status == STATUS_FAILED and job.error_message:
            menu.add_command(label="View error…", command=lambda: self.open_details(job.job_id))
        menu.add_separator()
        menu.add_command(label="Remove from list" + (" and history" if any(j.from_history for j in jobs) else ""),
                         command=lambda: self.remove_jobs(jobs))
        if single and is_file and job.status == STATUS_COMPLETED:
            menu.add_command(label="Delete file from disk…", command=lambda: self._delete_file(job))
        return menu

    def _on_context(self, event, jobs):
        if not jobs:
            return
        menu = self.build_context_menu(jobs)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _open_with(self, path):
        if not path or not os.path.isfile(path):
            return
        try:
            import subprocess
            subprocess.Popen(["rundll32.exe", "shell32.dll,OpenAs_RunDLL", os.path.normpath(path)])
        except OSError as e:
            messagebox.showerror("Open with", str(e))

    def _confirm_restart(self, jobs):
        targets = [j for j in jobs if not j.from_history and j.status not in RUNNING_STATUSES]
        if targets and messagebox.askyesno("Restart", f"Discard the partial data of {len(targets)} download(s) "
                                           "and download again from the beginning?", default="no"):
            self.act("restart", targets)

    def _move_to_queue(self, jobs, queue_id):
        for j in jobs:
            self.manager.move_to_queue(j.job_id, queue_id)
        self._mark_structure()

    def _ask_speed_limit(self, jobs):
        current = (jobs[0].options or {}).get("speed_limit_kbps", 0)
        dialog = ctk.CTkInputDialog(title="Speed limit", text=f"Limit for {len(jobs)} download(s) in KB/s "
                                                              f"(0 = no limit). Current: {current or 'none'}")
        value = dialog.get_input()
        if value is None:
            return
        try:
            kbps = int(str(value).strip() or 0)
            if not 0 <= kbps <= 10_000_000:
                raise ValueError
        except ValueError:
            messagebox.showerror("Speed limit", "Enter a whole number of KB/s (0 = no limit).")
            return
        for j in jobs:
            self.manager.set_speed_limit(j.job_id, kbps)
        self.toast("info", f"Speed limit {'removed' if not kbps else f'set to {kbps} KB/s'}.")

    def _ask_start_time(self, jobs):
        dialog = ctk.CTkInputDialog(title="Schedule start",
                                    text="Start at (HH:MM, 24-hour; the next time it comes round).\nLeave empty to "
                                         "start as soon as possible.")
        value = dialog.get_input()
        if value is None:
            return
        value = value.strip()
        start_at = None
        if value:
            try:
                t = datetime.datetime.strptime(value, "%H:%M").time()
            except ValueError:
                messagebox.showerror("Schedule start", "Use HH:MM, e.g. 02:30.")
                return
            when = datetime.datetime.combine(datetime.date.today(), t)
            if when <= datetime.datetime.now():
                when += datetime.timedelta(days=1)
            start_at = when.timestamp()
        for j in jobs:
            self.manager.set_start_at(j.job_id, start_at)
        self._mark_structure()

    # ============================================================== queues === #
    def _current_queue(self):
        if self.view.startswith("queue:"):
            q = self.manager.queues.get(self.view.split(":", 1)[1])
            if q:
                return q
        return self.manager.queues.get(MAIN_QUEUE_ID)

    def start_current_queue(self):
        q = self._current_queue()
        self.manager.start_queue(q.id)
        self.toast("info", f"Queue “{q.name}” started.")

    def stop_current_queue(self):
        q = self._current_queue()
        self.manager.stop_queue(q.id)
        self.toast("info", f"Queue “{q.name}” stopped — its downloads are paused.")

    def open_queues(self, queue_id=None, page=None, tab=None):
        from .queues_window import QueuesWindow
        existing = getattr(self, "_queues_window", None)
        if existing is not None and existing.win.winfo_exists():
            existing.win.lift()
            existing.show(queue_id=queue_id or self._current_queue().id if tab else queue_id, page=page, tab=tab)
            return
        self._queues_window = QueuesWindow(self, queue_id or (self._current_queue().id if tab else None),
                                           page=page, tab=tab)

    def new_queue(self, move_jobs=None):
        dialog = ctk.CTkInputDialog(title="New queue", text="Name of the new queue:")
        name = dialog.get_input()
        if not name or not name.strip():
            return None
        try:
            q = self.manager.queues.create(name.strip())
        except ValueError as e:
            messagebox.showerror("New queue", str(e))
            return None
        if move_jobs:
            self._move_to_queue(move_jobs, q.id)
        self.sidebar.rebuild()
        self._mark_structure()
        return q

    def queue_context_menu(self, queue_id, event):
        q = self.manager.queues.get(queue_id)
        if q is None:
            return
        menu = self._menu()
        if q.running:
            menu.add_command(label="Stop queue", command=lambda: (self.manager.stop_queue(q.id), self._mark_structure()))
        else:
            menu.add_command(label="Start queue", command=lambda: (self.manager.start_queue(q.id), self._mark_structure()))
        menu.add_command(label="Edit queue and schedule…", command=lambda: self.open_queues(q.id))
        if not q.is_main:
            menu.add_separator()
            menu.add_command(label="Delete queue…", command=lambda: self._delete_queue(q))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _delete_queue(self, q):
        if messagebox.askyesno("Delete queue", f"Delete the queue “{q.name}”?\nIts downloads move to Main; "
                                                "nothing is deleted from disk."):
            self.manager.delete_queue(q.id)
            if self.view == f"queue:{q.id}":
                self.view = "all"
            self.sidebar.rebuild()
            self._mark_structure()

    def _queue_complete(self, q):
        action = q.on_complete
        if action in ("none", None):
            return
        if action == "notify":
            notifications.notify(APP_NAME, f"Queue “{q.name}” has finished.")
            self.toast("success", f"Queue “{q.name}” finished.")
            return
        from .power_dialog import PowerCountdown
        if self._power_dialog is not None and self._power_dialog.win.winfo_exists():
            return
        self._power_dialog = PowerCountdown(self, q.name, action, on_confirm=lambda: self._do_completion(action))

    def _do_completion(self, action):
        if action == "exit":
            self.quit_app(force=True)
        else:
            self.manager.stop()  # partial downloads saved first
            if not power.perform(action):
                messagebox.showerror(APP_NAME, f"Windows refused the {power.ACTION_LABELS[action].lower()} request.")
                self.manager.start()

    # ========================================================== status bar === #
    def _build_status_bar(self):
        c, s = self.c, self.scale
        bar = tk.Frame(self.root, bg=c["toolbar"], height=round(30 * s))
        bar.grid(row=3, column=0, columnspan=2, sticky="ew")
        bar.pack_propagate(False)
        self._statusbar = bar
        tk.Frame(bar, bg=c["border"], height=1).place(relx=0, rely=0, relwidth=1.0, anchor="nw")

        def lab(side, **kw):
            label = tk.Label(bar, bg=c["toolbar"], fg=c["muted"], font=("Segoe UI", 9), bd=0, **kw)
            label.pack(side=side, padx=(round(14 * s), 0) if side == "left" else (0, round(14 * s)))
            return label

        self.status_down = lab("left")
        self.status_up = lab("left")
        self.status_counts = lab("left")
        self.status_version = lab("right")
        self.status_version.configure(text=f"v{__version__}", fg=c["faint"])
        self.status_engines = lab("right")
        self.status_limit = lab("right", cursor="hand2")
        self.status_limit.bind("<Button-1>", lambda e: self.toggle_limited_mode())
        self._engine_status_text = None
        self._refresh_status_bar()

    def _refresh_status_bar(self):
        jobs = self.manager.list_jobs()
        running = [j for j in jobs if j.status in RUNNING_STATUSES]
        waiting = sum(1 for j in jobs if j.status in (STATUS_QUEUED, STATUS_RETRYING))
        down = sum(j.speed_bps or 0 for j in running if j.status != STATUS_SEEDING)
        up = sum(j.upload_bps or 0 for j in running if j.engine == "torrent")
        g = icons.glyph
        self.status_down.configure(text=f"{g('download')} {_format_speed(down) or '0 B/s'}",
                                   fg=self.c["primary"] if down else self.c["muted"], font=icons.icon_font(9)
                                   if not icons.family() else ("Segoe UI", 9))
        self.status_up.configure(text=f"{g('upload')} {_format_speed(up) or '0 B/s'}")
        self.status_counts.configure(text=f"{len(running)} active   ·   {waiting} queued   ·   "
                                          f"{sum(1 for j in jobs if j.status == STATUS_COMPLETED):,} finished")
        kbps, reason = bandwidth.manager.effective_kbps()
        if kbps:
            self.status_limit.configure(text=f"{g('speed')}  Limited to {kbps:,} KB/s ({reason})", fg=self.c["warning"])
        else:
            self.status_limit.configure(text=f"{g('speed')}  No speed limit", fg=self.c["faint"])
        if self._engine_status_text is None:
            status = tools.tool_status(self.settings.get("ffmpeg_path", ""))
            try:
                import libtorrent  # noqa: F401
                torrent_ok = True
            except ImportError:
                torrent_ok = False
            api = f"Browser link :{self.settings.get('api_port')} ●" if self.api_thread else "Browser link off ○"
            self._engine_status_text = "   ".join([
                api, "Media ●", f"FFmpeg {'●' if status['ffmpeg'] else '⚠'}", f"Torrent {'●' if torrent_ok else '⚠'}",
                "HTTP ●"])
        self.status_engines.configure(text=self._engine_status_text)

    def toggle_limited_mode(self):
        bandwidth.manager.set_limited_mode(not bandwidth.manager.limited_mode)
        on = bandwidth.manager.limited_mode
        self.toast("info", f"Limited mode {'on' if on else 'off'}"
                           + (f" — {self.settings.get('limited_mode_kbps', 512)} KB/s for everything." if on else "."))
        self._refresh_status_bar()
        if self.tray_icon is not None:
            self.tray_icon.refresh()

    # ================================================================ misc === #
    def _bind_keys(self):
        r = self.root
        r.bind("<Control-v>", lambda e: self.paste_and_add() if not isinstance(e.widget, (tk.Entry, ctk.CTkEntry)) else None)
        r.bind("<Control-f>", lambda e: self.search_entry.focus_set())
        r.bind("<F5>", lambda e: self._mark_structure())

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

    # ============================================================= theme === #
    def palette(self):
        return {k: color(k) for k in COLORS}

    def apply_glass_window(self, win):
        """Plain window background + the app icon (CTkToplevel would otherwise
        replace the icon with CustomTkinter's default shortly after opening)."""
        win.configure(fg_color=COLORS["background"])
        from .backdrop import set_titlebar_theme
        win.after(30, lambda: win.winfo_exists() and set_titlebar_theme(win, glass_theme.is_dark(), self.c["toolbar"],
                                                                         self.c["text"]))
        if os.path.isfile(APP_ICON_FILE):
            def set_icon():
                try:
                    if win.winfo_exists():
                        win.iconbitmap(APP_ICON_FILE)
                except tk.TclError:
                    pass
            win.after(250, set_icon)

    def set_appearance_mode(self, mode, accent=None):
        if mode not in glass_theme.APPEARANCE_MODES:
            return
        accent = accent or self.settings.get("accent_color", "Indigo")
        self.appearance_mode = mode
        self.settings.update({"appearance_mode": mode, "accent_color": accent})
        glass_theme.apply_palette(mode, accent)
        glass_theme.install_ctk_theme()
        self._rebuild_ui()

    # ============================================================= dialogs === #
    def open_add_dialog(self, prefill_url=None, analyze=False):
        self.restore_window()
        return AddDialog(self, prefill_url=prefill_url, analyze=analyze)

    def paste_and_add(self):
        try:
            text = self.root.clipboard_get().strip()
        except tk.TclError:
            text = ""
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if len(lines) > 1:
            self.open_add_dialog("\n".join(lines), analyze=True)
            return
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
        bandwidth.manager.refresh()
        self._engine_status_text = None
        self._refresh_status_bar()
        if self.settings.get("clipboard_monitor_enabled"):
            self.root.after(1500, self._poll_clipboard)
        self.manager.wake()

    def open_request(self, url):
        """A second launch (e.g. double-clicked .torrent, magnet link) asked us to open something."""
        self.restore_window()
        if url:
            self.open_add_dialog(url, analyze=True)

    # ============================================================ updates === #
    UPDATE_CHECK_INTERVAL = 24 * 3600

    def after_start(self, update_now=False, assume_yes=False):
        """Runs once the window is up: report a finished update, then the
        startup/daily update check (or the --update-now command)."""
        from .. import update_install
        from .update_dialog import show_post_update_result
        try:
            result = update_install.post_update_check()
        except Exception:  # noqa: BLE001 - never block startup on this
            log.exception("Post-update check failed")
            result = None
        if result:
            show_post_update_result(self, result)
        if update_now:
            self.check_for_updates(auto_install=assume_yes)
            return
        due = time.time() - float(self.settings.get("update_last_check") or 0) > self.UPDATE_CHECK_INTERVAL
        if self.settings.get("update_check_on_start") or (self.settings.get("update_auto_check", True) and due):
            self.root.after(6000, self._background_update_check)
        self.root.after(3600 * 1000, self._periodic_update_check)

    def _periodic_update_check(self):
        if self._closing:
            return
        due = time.time() - float(self.settings.get("update_last_check") or 0) > self.UPDATE_CHECK_INTERVAL
        if self.settings.get("update_auto_check", True) and due:
            self._background_update_check()
        self.root.after(3600 * 1000, self._periodic_update_check)

    def _background_update_check(self):
        """Quiet check: only speaks up when there's something new (and the user
        didn't ask to be reminded later about that version)."""
        if (self.settings.get("update_source") or "github") != "github":
            return
        from ..updater import check_release

        def work():
            try:
                info = check_release(channel=self.settings.get("update_channel") or "stable")
            except Exception as e:  # noqa: BLE001 - background check is best-effort
                log.info("Update check failed: %s", e)
                self.settings.set("update_last_status", f"Check failed: {e}")
                return
            self.settings.update({"update_last_check": time.time(),
                                  "update_last_status": f"Version {info.version} available" if info.available
                                  else f"Up to date ({__version__})"})
            snoozed = (self.settings.get("update_remind_version") == info.version
                       and time.time() < float(self.settings.get("update_remind_after") or 0))
            if info.available and not snoozed:
                self.call_in_main(self.check_for_updates, False, info)
        threading.Thread(target=work, name="mdm-update-check", daemon=True).start()

    def check_for_updates(self, auto_install=False, info=None):
        from .update_dialog import UpdateDialog
        existing = getattr(self, "_update_dialog", None)
        if existing is not None and existing.win.winfo_exists():
            existing.win.lift()
            return existing
        self.restore_window()
        self._update_dialog = UpdateDialog(self, info=info, auto_install=auto_install)
        return self._update_dialog

    def install_update(self, info, installer, backup):
        """Hand over to the verified installer and exit: downloads are paused
        and saved first, then the single-instance lock is released so Setup
        can replace the program files."""
        from .. import single_instance, update_install
        self._closing = True
        try:
            self.scheduler.stop()
            self.manager.stop()  # running downloads -> Paused, queue saved
        except Exception:  # noqa: BLE001
            log.exception("Stopping downloads before the update failed")
        try:
            update_install.launch(info, installer, backup)
        except Exception:
            self._closing = False
            self.manager.start()
            self.scheduler.start()
            raise
        if self.api_server is not None:
            self.api_server.should_exit = True
        if self.tray_icon is not None:
            self.tray_icon.stop()
        try:
            self.history.close()
        except Exception:  # noqa: BLE001
            pass
        single_instance.release()
        self.root.destroy()

    # ===================================================== help / about === #
    def open_about(self):
        from .about_window import AboutWindow
        existing = getattr(self, "_about", None)
        if existing is not None and existing.win.winfo_exists():
            existing.win.lift()
            return
        self._about = AboutWindow(self)

    def open_licenses(self):
        from .licenses_window import LicensesWindow
        existing = getattr(self, "_licenses", None)
        if existing is not None and existing.win.winfo_exists():
            existing.win.lift()
            return
        self._licenses = LicensesWindow(self)

    @staticmethod
    def read_doc(name):
        """A bundled document (installation docs/ folder, or the source tree)."""
        for folder in (os.path.join(INSTALL_DIR, "docs"), INSTALL_DIR,
                       os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                                    "docs")):
            path = os.path.join(folder, name)
            if os.path.isfile(path):
                try:
                    with open(path, encoding="utf-8") as f:
                        return f.read()
                except OSError:
                    return ""
        return ""

    def whats_new_text(self):
        import re
        text = self.read_doc("CHANGELOG.md")
        m = re.search(rf"^## {re.escape(__version__)}\b.*?(?=^## |\Z)", text, re.M | re.S)
        return m.group(0).strip() if m else f"No release notes for {__version__} were found."

    # ===================================================== tray / lifetime === #
    def _start_local_api(self):
        try:
            self.api_thread, self.api_server = run_server(
                self.manager, self.settings, on_open_request=lambda url: self.call_in_main(self.open_request, url),
                on_added=lambda job: self.call_in_main(self.toast, "info", f"From browser — {truncate(job.title, 40)}"))
        except Exception:  # noqa: BLE001
            log.exception("Failed to start the local API")
            self.api_thread = self.api_server = None
        self._engine_status_text = None  # status bar shows the browser link state

    def _setup_tray(self):
        if not tray.tray_supported():
            return
        self.tray_icon = tray.TrayIcon(
            on_open=lambda: self.call_in_main(self.restore_window),
            on_pause_all=lambda: self.manager.pause_all(),
            on_resume_all=lambda: self.manager.resume_all(),
            on_settings=lambda: self.call_in_main(self.open_settings),
            on_exit=lambda: self.call_in_main(self.quit_app),
            on_new=lambda: self.call_in_main(self.open_add_dialog),
            on_toggle_limit=lambda: self.call_in_main(self.toggle_limited_mode),
            is_limited=lambda: bandwidth.manager.limited_mode,
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

    def quit_app(self, force=False):
        active = [j for j in self.manager.list_jobs() if j.status in RUNNING_STATUSES]
        if active and not force and not messagebox.askyesno(
                "Exit", f"{len(active)} download(s) are running. Exit anyway? They'll be paused and can be resumed next time."):
            return
        self._closing = True
        try:
            self.scheduler.stop()
            self.manager.stop()
        except Exception:  # noqa: BLE001
            pass
        if self.api_server is not None:
            self.api_server.should_exit = True
        if self.tray_icon is not None:
            self.tray_icon.stop()
        try:
            self.history.close()
        except Exception:  # noqa: BLE001
            pass
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
