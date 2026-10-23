"""Queues & Scheduler window.

    Queues              create / rename / delete, concurrency and connection limits,
                        what to do when the queue finishes, its weekly schedule and
                        the order of its downloads (drag to reorder)
    Speed profiles      bandwidth caps for time windows (e.g. office hours)
    Recurring downloads links fetched again on a schedule
"""

import datetime
import tkinter as tk
from tkinter import messagebox

import customtkinter as ctk

from ..models import ACTIVE_STATUSES, STATUS_PAUSED, STATUS_QUEUED
from ..power import ACTION_LABELS
from ..queues import COMPLETION_ACTIONS, MAIN_QUEUE_ID, POWER_ACTIONS, WEEKDAYS, parse_hhmm
from .common import human_size
from .datagrid import DataGrid
from .theme import COLORS, font, glass_button, glass_card

LIMIT_CHOICES = ["No limit"] + [str(n) for n in range(1, 11)]
CONN_CHOICES = ["Default"] + [str(n) for n in range(1, 17)]


def _days_text(days):
    days = sorted(days)
    if days == list(range(7)):
        return "Every day"
    if days == list(range(5)):
        return "Weekdays"
    if days == [5, 6]:
        return "Weekends"
    return ", ".join(WEEKDAYS[d] for d in days)


class QueuesWindow:
    def __init__(self, app, queue_id=None, page=None, tab=None):
        self.app = app
        self.manager = app.manager
        win = self.win = ctk.CTkToplevel(app.root)
        win.title("Queues & Scheduler — Master Download Manager")
        win.geometry("900x600")
        win.minsize(820, 520)
        app.apply_glass_window(win)
        self.page = tk.StringVar(value=page or "Queues")
        self.initial_tab = tab
        top = ctk.CTkFrame(win, fg_color="transparent")
        top.pack(fill="x", padx=16, pady=(14, 8))
        ctk.CTkSegmentedButton(top, values=["Queues", "Speed profiles", "Recurring downloads"], variable=self.page,
                               command=lambda _v: self._show_page(), font=font(12, "bold")).pack(side="left")
        self.body = ctk.CTkFrame(win, fg_color="transparent")
        self.body.pack(fill="both", expand=True, padx=16, pady=(0, 14))
        self.selected_id = queue_id or MAIN_QUEUE_ID
        self._show_page()

    def _clear(self):
        for w in self.body.winfo_children():
            w.destroy()

    def _show_page(self):
        self._clear()
        {"Queues": self._queues_page, "Speed profiles": self._profiles_page,
         "Recurring downloads": self._recurring_page}[self.page.get()]()

    def show(self, queue_id=None, page=None, tab=None):
        """Bring a page/queue/tab to the front (Tasks/Tools menus)."""
        if page:
            self.page.set(page)
        if queue_id:
            self.selected_id = queue_id
        self.initial_tab = tab
        self._show_page()

    def select(self, queue_id):
        self.page.set("Queues")
        self.selected_id = queue_id
        self._show_page()

    # =============================================================== queues === #
    def _queues_page(self):
        body = self.body
        left = glass_card(body, width=210)
        left.pack(side="left", fill="y", padx=(0, 12))
        left.pack_propagate(False)
        ctk.CTkLabel(left, text="Queues", font=font(13, "bold")).pack(anchor="w", padx=14, pady=(12, 6))
        listing = ctk.CTkScrollableFrame(left, fg_color="transparent")
        listing.pack(fill="both", expand=True, padx=6)
        for q in self.manager.queues.all():
            selected = q.id == self.selected_id
            ctk.CTkButton(listing, text=("● " if q.running else "○ ") + q.name, anchor="w", height=32,
                          fg_color=COLORS["selected"] if selected else "transparent",
                          hover_color=COLORS["surface_hover"], text_color=COLORS["text"], font=font(12),
                          command=lambda qid=q.id: self.select(qid)).pack(fill="x", pady=1)
        row = ctk.CTkFrame(left, fg_color="transparent")
        row.pack(fill="x", padx=10, pady=10)
        glass_button(row, "New", self._new_queue, width=84, height=30).pack(side="left")
        q = self.manager.queues.get(self.selected_id) or self.manager.queues.get(MAIN_QUEUE_ID)
        self.selected_id = q.id
        if not q.is_main:
            glass_button(row, "Delete", lambda: self._delete(q), variant="ghost", width=84, height=30).pack(side="right")

        right = ctk.CTkFrame(body, fg_color="transparent")
        right.pack(side="left", fill="both", expand=True)
        head = ctk.CTkFrame(right, fg_color="transparent")
        head.pack(fill="x")
        ctk.CTkLabel(head, text=q.name, font=font(17, "bold")).pack(side="left")
        state = "running" if q.running else "stopped"
        ctk.CTkLabel(head, text=f"  {state}", text_color=COLORS["success"] if q.running else COLORS["muted"],
                     font=font(12)).pack(side="left")
        if q.running:
            glass_button(head, "Stop queue", lambda: self._run(q, False), width=110, height=30).pack(side="right")
        else:
            glass_button(head, "Start queue", lambda: self._run(q, True), variant="primary", width=110,
                         height=30).pack(side="right")

        tabs = ctk.CTkTabview(right, height=420)
        self.tabs = tabs
        tabs.pack(fill="both", expand=True, pady=(8, 0))
        self._settings_tab(tabs.add("Settings"), q)
        self._schedule_tab(tabs.add("Schedule"), q)
        self._items_tab(tabs.add("Downloads"), q)
        if self.initial_tab in ("Settings", "Schedule", "Downloads"):
            tabs.set(self.initial_tab)
            self.initial_tab = None

    def _field(self, parent, label, widget_factory, hint=None):
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", pady=5)
        ctk.CTkLabel(row, text=label, width=210, anchor="w", font=font(12)).pack(side="left")
        widget = widget_factory(row)
        widget.pack(side="left")
        if hint:
            ctk.CTkLabel(row, text=hint, text_color=COLORS["faint"], font=font(11)).pack(side="left", padx=10)
        return widget

    def _settings_tab(self, tab, q):
        self.v_name = tk.StringVar(value=q.name)
        self.v_conc = tk.StringVar(value=str(q.max_concurrent) if q.max_concurrent else "No limit")
        self.v_conn = tk.StringVar(value=str(q.max_connections) if q.max_connections else "Default")
        self.v_stop = tk.BooleanVar(value=q.stop_when_empty)
        self.v_done = tk.StringVar(value=ACTION_LABELS[q.on_complete])
        name = self._field(tab, "Name", lambda p: ctk.CTkEntry(p, textvariable=self.v_name, width=240))
        if q.is_main:
            name.configure(state="disabled")
        self._field(tab, "Downloads at the same time", lambda p: ctk.CTkOptionMenu(
            p, values=LIMIT_CHOICES, variable=self.v_conc, width=120), "also capped by Settings → Downloads")
        self._field(tab, "Connections per download", lambda p: ctk.CTkOptionMenu(
            p, values=CONN_CHOICES, variable=self.v_conn, width=120), "direct (HTTP) downloads")
        stop = self._field(tab, "Stop the queue when it's empty", lambda p: ctk.CTkCheckBox(
            p, text="", variable=self.v_stop))
        if q.is_main:
            stop.configure(state="disabled")
        self._field(tab, "When everything has finished", lambda p: ctk.CTkOptionMenu(
            p, values=[ACTION_LABELS[a] for a in COMPLETION_ACTIONS], variable=self.v_done, width=220))
        ctk.CTkLabel(tab, text="Sleep, hibernate and shut down are only ever done after a 60-second countdown you "
                               "can cancel.", text_color=COLORS["faint"], font=font(11), wraplength=520,
                     justify="left").pack(anchor="w", pady=(6, 0))
        glass_button(tab, "Save", lambda: self._save_settings(q), variant="primary", width=110).pack(anchor="e",
                                                                                                    pady=12)

    def _save_settings(self, q):
        action = next(a for a in COMPLETION_ACTIONS if ACTION_LABELS[a] == self.v_done.get())
        if action in POWER_ACTIONS and q.on_complete != action and not messagebox.askyesno(
                "Power action", f"When “{q.name}” finishes, MDM will {ACTION_LABELS[action].lower()} this PC after a "
                                "60-second countdown. Continue?", parent=self.win):
            return
        fields = {"max_concurrent": 0 if self.v_conc.get() == "No limit" else int(self.v_conc.get()),
                  "max_connections": 0 if self.v_conn.get() == "Default" else int(self.v_conn.get()),
                  "stop_when_empty": self.v_stop.get(), "on_complete": action}
        if not q.is_main:
            fields["name"] = self.v_name.get()
        try:
            self.manager.queues.update(q.id, **fields)
        except ValueError as e:
            messagebox.showerror("Queue", str(e), parent=self.win)
            return
        self.manager.wake()
        self._refresh_app()
        self.app.toast("success", "Queue saved.")
        self._show_page()

    def _schedule_tab(self, tab, q):
        s = q.schedule
        self.v_start_on = tk.BooleanVar(value=s.start_enabled)
        self.v_start = tk.StringVar(value=s.start_time)
        self.v_stop_on = tk.BooleanVar(value=s.stop_enabled)
        self.v_stop_t = tk.StringVar(value=s.stop_time)
        self.v_days = [tk.BooleanVar(value=d in s.days) for d in range(7)]
        row = ctk.CTkFrame(tab, fg_color="transparent")
        row.pack(fill="x", pady=(8, 4))
        ctk.CTkCheckBox(row, text="Start the queue at", variable=self.v_start_on, width=170).pack(side="left")
        ctk.CTkEntry(row, textvariable=self.v_start, width=80).pack(side="left")
        row = ctk.CTkFrame(tab, fg_color="transparent")
        row.pack(fill="x", pady=4)
        ctk.CTkCheckBox(row, text="Stop the queue at", variable=self.v_stop_on, width=170).pack(side="left")
        ctk.CTkEntry(row, textvariable=self.v_stop_t, width=80).pack(side="left")
        ctk.CTkLabel(row, text="(24-hour HH:MM, your local time)", text_color=COLORS["faint"],
                     font=font(11)).pack(side="left", padx=10)
        ctk.CTkLabel(tab, text="On these days", font=font(12, "bold")).pack(anchor="w", pady=(14, 4))
        days = ctk.CTkFrame(tab, fg_color="transparent")
        days.pack(fill="x")
        for d, var in enumerate(self.v_days):
            ctk.CTkCheckBox(days, text=WEEKDAYS[d], variable=var, width=70).pack(side="left")
        nxt = s.next_start(datetime.datetime.now()) if s.start_enabled else None
        info = (f"Next start: {nxt:%A %d %B, %H:%M}" if nxt else "No start time set.")
        ctk.CTkLabel(tab, text=info + "\nIf MDM wasn't running (or the PC was asleep) at the start time, the queue "
                                      "starts as soon as MDM is back, as long as it's still before the stop time.",
                     text_color=COLORS["muted"], font=font(11), justify="left", wraplength=560).pack(anchor="w", pady=14)
        glass_button(tab, "Save schedule", lambda: self._save_schedule(q), variant="primary", width=130).pack(
            anchor="e")

    def _save_schedule(self, q):
        days = [d for d, v in enumerate(self.v_days) if v.get()]
        try:
            parse_hhmm(self.v_start.get())
            parse_hhmm(self.v_stop_t.get())
            self.manager.queues.update(q.id, schedule={
                "days": days, "start_enabled": self.v_start_on.get(), "start_time": self.v_start.get().strip(),
                "stop_enabled": self.v_stop_on.get(), "stop_time": self.v_stop_t.get().strip()})
        except ValueError as e:
            messagebox.showerror("Schedule", str(e), parent=self.win)
            return
        self.app.toast("success", "Schedule saved.")
        self._show_page()

    def _items_tab(self, tab, q):
        jobs = [j for j in self.manager.list_jobs() if not j.from_history and j.status in ACTIVE_STATUSES
                and (j.queue_id == q.id or (q.is_main and self.manager.queues.get(j.queue_id) is None))]
        if not jobs:
            ctk.CTkLabel(tab, text="No unfinished downloads in this queue.\nRight-click downloads in the main list → "
                                   "Move to queue.", text_color=COLORS["muted"]).pack(pady=40)
            return
        ctk.CTkLabel(tab, text="Drag to change the order downloads start in.", text_color=COLORS["faint"],
                     font=font(11)).pack(anchor="w")
        cols = [dict(key="name", title="Name", width=300, min=160, stretch=True, kind="name"),
                dict(key="status", title="Status", width=120, kind="status"),
                dict(key="size", title="Size", width=90, anchor="e")]

        def cell(job, key):
            if key == "name":
                return "", job.display_name
            if key == "status":
                return job.status, "paused" if job.status == STATUS_PAUSED else "idle" if job.status == STATUS_QUEUED else "active"
            return human_size(job.total_bytes, "") if job.total_bytes else ""

        grid = DataGrid(tab, cols, cell, key=lambda j: j.job_id, on_reorder=lambda rows, target: self._reorder(
            grid, rows, target))
        grid.pack(fill="both", expand=True, pady=6)
        grid.set_rows(jobs)

    def _reorder(self, grid, rows, target):
        ids = {r.job_id for r in rows}
        before = next((r.job_id for r in grid.rows[target:] if r.job_id not in ids), None)
        self.manager.move_before([r.job_id for r in rows], before)
        order = {j.job_id: i for i, j in enumerate(self.manager.list_jobs())}
        grid.set_rows(sorted(grid.rows, key=lambda j: order.get(j.job_id, 0)))
        self._refresh_app()

    def _run(self, q, start):
        (self.manager.start_queue if start else self.manager.stop_queue)(q.id)
        self._refresh_app()
        self._show_page()

    def _new_queue(self):
        q = self.app.new_queue()
        if q:
            self.select(q.id)

    def _delete(self, q):
        if messagebox.askyesno("Delete queue", f"Delete “{q.name}”? Its downloads move to Main.", parent=self.win):
            self.manager.delete_queue(q.id)
            self.selected_id = MAIN_QUEUE_ID
            self._refresh_app()
            self._show_page()

    def _refresh_app(self):
        try:
            self.app.sidebar.rebuild()
        except tk.TclError:
            pass
        self.app._mark_structure()

    # ======================================================= speed profiles === #
    def _profiles_page(self):
        store = self.app.scheduler.store
        ctk.CTkLabel(self.body, text="Cap the total download speed during certain hours. When several apply, the "
                                     "lowest wins; a global limit in Settings and the tray's Limited mode also count.",
                     text_color=COLORS["muted"], font=font(12), wraplength=820, justify="left").pack(anchor="w")
        listing = ctk.CTkScrollableFrame(self.body, fg_color="transparent", height=300)
        listing.pack(fill="both", expand=True, pady=8)
        if not store.profiles:
            ctk.CTkLabel(listing, text="No speed profiles yet.", text_color=COLORS["faint"]).pack(pady=20)
        for p in store.profiles:
            row = glass_card(listing)
            row.pack(fill="x", pady=3)
            var = tk.BooleanVar(value=p.enabled)
            ctk.CTkCheckBox(row, text="", variable=var, width=28,
                            command=lambda p=p, v=var: self._toggle_profile(p, v.get())).pack(side="left", padx=10)
            ctk.CTkLabel(row, text=p.name, font=font(12, "bold"), width=160, anchor="w").pack(side="left")
            ctk.CTkLabel(row, text=f"{p.limit_kbps:,} KB/s   ·   {p.start}–{p.end}   ·   {_days_text(p.days)}",
                         text_color=COLORS["muted"]).pack(side="left")
            glass_button(row, "Remove", lambda pid=p.id: self._remove_sched(pid), variant="ghost", width=80,
                         height=28).pack(side="right", padx=8, pady=6)
        form = glass_card(self.body)
        form.pack(fill="x")
        vals = {k: tk.StringVar(value=v) for k, v in (("name", "Office hours"), ("limit", "512"), ("start", "09:00"),
                                                       ("end", "18:00"))}
        days = [tk.BooleanVar(value=d < 5) for d in range(7)]
        row = ctk.CTkFrame(form, fg_color="transparent")
        row.pack(fill="x", padx=12, pady=(10, 4))
        for label, key, width in (("Name", "name", 150), ("KB/s", "limit", 80), ("From", "start", 70), ("To", "end", 70)):
            ctk.CTkLabel(row, text=label).pack(side="left", padx=(0, 4))
            ctk.CTkEntry(row, textvariable=vals[key], width=width).pack(side="left", padx=(0, 12))
        row2 = ctk.CTkFrame(form, fg_color="transparent")
        row2.pack(fill="x", padx=12, pady=(0, 10))
        for d, var in enumerate(days):
            ctk.CTkCheckBox(row2, text=WEEKDAYS[d], variable=var, width=64).pack(side="left")

        def add():
            try:
                limit = int(vals["limit"].get())
                if limit <= 0:
                    raise ValueError("The limit must be more than 0 KB/s.")
                store.add_profile(name=vals["name"].get().strip() or "Profile", limit_kbps=limit,
                                  start=vals["start"].get().strip(), end=vals["end"].get().strip(),
                                  days=[d for d, v in enumerate(days) if v.get()])
            except (ValueError, TypeError) as e:
                messagebox.showerror("Speed profile", str(e) or "Check the values.", parent=self.win)
                return
            self.app.scheduler.tick()
            self._show_page()
        glass_button(row2, "Add profile", add, variant="primary", width=110, height=30).pack(side="right")

    def _toggle_profile(self, profile, enabled):
        profile.enabled = enabled
        self.app.scheduler.store.save()
        self.app.scheduler.tick()

    def _remove_sched(self, item_id):
        self.app.scheduler.store.remove(item_id)
        self.app.scheduler.tick()
        self._show_page()

    # ==================================================== recurring downloads === #
    def _recurring_page(self):
        store = self.app.scheduler.store
        ctk.CTkLabel(self.body, text="Download a link again on a schedule (e.g. a nightly build). Each run is added as "
                                     "a new download with your default quality, into the chosen queue.",
                     text_color=COLORS["muted"], font=font(12), wraplength=820, justify="left").pack(anchor="w")
        listing = ctk.CTkScrollableFrame(self.body, fg_color="transparent", height=300)
        listing.pack(fill="both", expand=True, pady=8)
        queues = {q.id: q.name for q in self.manager.queues.all()}
        if not store.recurring:
            ctk.CTkLabel(listing, text="No recurring downloads yet.", text_color=COLORS["faint"]).pack(pady=20)
        for r in store.recurring:
            row = glass_card(listing)
            row.pack(fill="x", pady=3)
            var = tk.BooleanVar(value=r.enabled)
            ctk.CTkCheckBox(row, text="", variable=var, width=28,
                            command=lambda r=r, v=var: self._toggle_recurring(r, v.get())).pack(side="left", padx=10)
            last = datetime.datetime.fromtimestamp(r.last_run).strftime("%Y-%m-%d %H:%M") if r.last_run else "never"
            ctk.CTkLabel(row, text=r.url, anchor="w", width=380).pack(side="left")
            ctk.CTkLabel(row, text=f"{r.time} · {_days_text(r.days)} · {queues.get(r.queue_id, 'Main')} · last {last}",
                         text_color=COLORS["muted"]).pack(side="left", padx=8)
            glass_button(row, "Remove", lambda rid=r.id: self._remove_sched(rid), variant="ghost", width=80,
                         height=28).pack(side="right", padx=8, pady=6)
        form = glass_card(self.body)
        form.pack(fill="x")
        url, at = tk.StringVar(), tk.StringVar(value="03:00")
        qname = tk.StringVar(value=queues[MAIN_QUEUE_ID])
        days = [tk.BooleanVar(value=True) for _ in range(7)]
        row = ctk.CTkFrame(form, fg_color="transparent")
        row.pack(fill="x", padx=12, pady=(10, 4))
        ctk.CTkEntry(row, textvariable=url, placeholder_text="https://… link to download again", width=420).pack(side="left")
        ctk.CTkLabel(row, text="at").pack(side="left", padx=6)
        ctk.CTkEntry(row, textvariable=at, width=70).pack(side="left")
        ctk.CTkOptionMenu(row, values=list(queues.values()), variable=qname, width=140).pack(side="left", padx=8)
        row2 = ctk.CTkFrame(form, fg_color="transparent")
        row2.pack(fill="x", padx=12, pady=(0, 10))
        for d, var in enumerate(days):
            ctk.CTkCheckBox(row2, text=WEEKDAYS[d], variable=var, width=64).pack(side="left")

        def add():
            from ..router import classify
            link = url.get().strip()
            if not classify(link):
                messagebox.showerror("Recurring download", "Enter a web link, magnet link or .torrent URL.",
                                     parent=self.win)
                return
            try:
                store.add_recurring(url=link, time=at.get().strip(), days=[d for d, v in enumerate(days) if v.get()],
                                    queue_id=next(k for k, v in queues.items() if v == qname.get()))
            except (ValueError, TypeError) as e:
                messagebox.showerror("Recurring download", str(e), parent=self.win)
                return
            self._show_page()
        glass_button(row2, "Add", add, variant="primary", width=90, height=30).pack(side="right")

    def _toggle_recurring(self, r, enabled):
        r.enabled = enabled
        self.app.scheduler.store.save()
