"""A virtualised download table drawn on a Tk Canvas.

ttk.Treeview can't draw progress bars, icons or per-cell colours and gets
slow when every row is rebuilt. This grid only ever draws the rows that are
on screen (≈25 however long the list is), so scrolling, sorting and live
progress stay cheap with thousands of downloads.

Features: real progress bars, category icons, coloured statuses, sortable
and resizable columns, a column chooser (right-click the header),
Ctrl/Shift multi-select, keyboard navigation, hover, and drag-and-drop
reordering (only while the list isn't sorted by a column).

Rows are arbitrary objects; `cell(row, key)` returns what to draw:
    text columns     -> str
    "name"  column   -> (icon_glyph, text)
    "progress" col   -> (fraction 0..1 or None, label, state)  state: active|done|failed|paused|idle
    "status" column  -> (text, state)
"""

import tkinter as tk
import tkinter.font as tkfont

import customtkinter as ctk

from . import icons
from .theme import color, mix


class DataGrid(tk.Frame):
    ROW_H = 34
    HEAD_H = 32
    PAD = 10

    def __init__(self, parent, columns, cell, *, key=lambda r: r, on_select=None, on_activate=None,
                 on_context=None, on_sort=None, on_reorder=None, on_columns_changed=None, on_key=None):
        super().__init__(parent, bd=0, highlightthickness=0)
        self.columns = [dict(c) for c in columns]
        self.cell = cell
        self.key = key
        self.on_select, self.on_activate, self.on_context = on_select, on_activate, on_context
        self.on_sort, self.on_reorder, self.on_columns_changed, self.on_key = on_sort, on_reorder, on_columns_changed, on_key
        try:
            self.scale = ctk.ScalingTracker.get_widget_scaling(parent)
        except Exception:  # noqa: BLE001
            self.scale = 1.0
        self.row_h = round(self.ROW_H * self.scale)
        self.head_h = round(self.HEAD_H * self.scale)
        self.pad = round(self.PAD * self.scale)

        self.rows = []
        self._index = {}
        self.selection = set()
        self._anchor = None
        self._hover = None
        self._yoff = 0
        self._xoff = 0
        self.sort_key, self.sort_reverse = None, False
        self.empty_text = ""
        self._drag = None
        self._resize = None
        self._trunc_cache = {}
        self._redraw_pending = False

        self.font = tkfont.Font(family="Segoe UI", size=10)
        self.font_bold = tkfont.Font(family="Segoe UI Semibold", size=10)
        self.font_small = tkfont.Font(family="Segoe UI", size=9)
        self.font_icon = tkfont.Font(family=icons.family() or "Segoe UI Symbol", size=11)

        self.header = tk.Canvas(self, height=self.head_h, bd=0, highlightthickness=0)
        self.body = tk.Canvas(self, bd=0, highlightthickness=0, takefocus=1)
        self.vbar = ctk.CTkScrollbar(self, orientation="vertical", command=self._on_vbar, width=12)
        self.hbar = ctk.CTkScrollbar(self, orientation="horizontal", command=self._on_hbar, height=12)
        self.header.grid(row=0, column=0, sticky="ew")
        self.body.grid(row=1, column=0, sticky="nsew")
        self.vbar.grid(row=0, column=1, rowspan=2, sticky="ns")
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)
        self._hbar_shown = False

        b = self.body
        b.bind("<Configure>", lambda e: self.redraw())
        b.bind("<Button-1>", self._on_click)
        b.bind("<Control-Button-1>", lambda e: self._on_click(e, ctrl=True))
        b.bind("<Shift-Button-1>", lambda e: self._on_click(e, shift=True))
        b.bind("<B1-Motion>", self._on_drag_motion)
        b.bind("<ButtonRelease-1>", self._on_release)
        b.bind("<Double-Button-1>", self._on_double)
        b.bind("<Button-3>", self._on_right_click)
        b.bind("<Motion>", self._on_motion)
        b.bind("<Leave>", lambda e: self._set_hover(None))
        for w in (b, self.header):
            w.bind("<MouseWheel>", self._on_wheel)
            w.bind("<Shift-MouseWheel>", lambda e: self._scroll_x(-e.delta))
        b.bind("<Up>", lambda e: self._move_sel(-1, e))
        b.bind("<Down>", lambda e: self._move_sel(1, e))
        b.bind("<Shift-Up>", lambda e: self._move_sel(-1, e, extend=True))
        b.bind("<Shift-Down>", lambda e: self._move_sel(1, e, extend=True))
        b.bind("<Prior>", lambda e: self._move_sel(-self._page(), e))
        b.bind("<Next>", lambda e: self._move_sel(self._page(), e))
        b.bind("<Home>", lambda e: self._move_sel(-len(self.rows), e))
        b.bind("<End>", lambda e: self._move_sel(len(self.rows), e))
        b.bind("<Control-a>", lambda e: (self.select_all(), "break")[1])
        b.bind("<Return>", lambda e: self._activate_selected())
        b.bind("<Delete>", lambda e: self.on_key and self.on_key("delete"))

        h = self.header
        h.bind("<Configure>", lambda e: self.redraw())
        h.bind("<Motion>", self._on_header_motion)
        h.bind("<Button-1>", self._on_header_press)
        h.bind("<B1-Motion>", self._on_header_drag)
        h.bind("<ButtonRelease-1>", self._on_header_release)
        h.bind("<Button-3>", self._on_header_menu)
        self.apply_colors()

    # ============================================================ public === #
    def apply_colors(self):
        self.c = {k: color(k) for k in ("background", "surface", "surface_hover", "surface_deep", "border",
                                        "border_soft", "text", "muted", "faint", "selected", "primary", "success",
                                        "warning", "danger", "sidebar")}
        self.c["row_alt"] = mix(self.c["background"], self.c["surface_hover"], 0.18)
        self.c["track"] = mix(self.c["background"], self.c["border"], 0.9)
        self.header.configure(bg=self.c["background"])
        self.body.configure(bg=self.c["background"])
        self.configure(bg=self.c["background"])
        self.redraw()

    def set_rows(self, rows):
        self.rows = list(rows)
        self._index = {self.key(r): i for i, r in enumerate(self.rows)}
        self.selection &= set(self._index)
        if self._anchor not in self._index:
            self._anchor = None
        self._clamp_scroll()
        self.redraw()

    def refresh(self, keys=None):
        """Redraw if any of `keys` (or anything, when None) is on screen."""
        if keys is not None:
            first, last = self._visible_range()
            if not any(first <= self._index.get(k, -1) < last for k in keys):
                return
        self.redraw()

    def selected(self):
        return [self.rows[self._index[k]] for k in sorted(self.selection, key=lambda k: self._index[k])]

    def select_keys(self, keys):
        self.selection = {k for k in keys if k in self._index}
        self.redraw()
        self._fire_select()

    def select_all(self):
        self.selection = set(self._index)
        self.redraw()
        self._fire_select()

    def set_sort(self, key, reverse):
        self.sort_key, self.sort_reverse = key, reverse
        self.redraw()

    def set_empty_text(self, text):
        if text != self.empty_text:
            self.empty_text = text
            self.redraw()

    def column_state(self):
        return [{"key": c["key"], "width": c["width"], "visible": c.get("visible", True)} for c in self.columns]

    def restore_column_state(self, state):
        by_key = {s.get("key"): s for s in state or [] if isinstance(s, dict)}
        for c in self.columns:
            s = by_key.get(c["key"])
            if s:
                c["width"] = max(c.get("min", 40), int(s.get("width") or c["width"]))
                c["visible"] = bool(s.get("visible", True)) or c.get("required", False)
        self.redraw()

    # ========================================================= geometry === #
    def _visible_columns(self):
        cols = [c for c in self.columns if c.get("visible", True)]
        avail = max(1, self.body.winfo_width())
        fixed = sum(round(c["width"] * self.scale) for c in cols if not c.get("stretch"))
        out, x = [], 0
        for c in cols:
            w = round(c["width"] * self.scale)
            if c.get("stretch"):  # takes the leftover width; never below its minimum (then the grid scrolls)
                w = max(round(c.get("min", 120) * self.scale), avail - fixed)
            out.append((c, x, w))
            x += w
        return out, x

    def _visible_range(self):
        height = max(1, self.body.winfo_height())
        first = self._yoff // self.row_h
        last = min(len(self.rows), (self._yoff + height) // self.row_h + 1)
        return first, last

    def _page(self):
        return max(1, self.body.winfo_height() // self.row_h - 1)

    def _clamp_scroll(self):
        height = max(1, self.body.winfo_height())
        self._yoff = max(0, min(self._yoff, len(self.rows) * self.row_h - height))

    def _row_at(self, y):
        idx = (self._yoff + y) // self.row_h
        return idx if 0 <= idx < len(self.rows) else None

    # ========================================================== drawing === #
    def redraw(self):
        if self._redraw_pending:
            return
        self._redraw_pending = True
        self.after_idle(self._draw)

    def _draw(self):
        self._redraw_pending = False
        if not self.winfo_exists():
            return
        cols, total_w = self._visible_columns()
        width = max(1, self.body.winfo_width())
        self._update_hbar(total_w, width)
        self._xoff = max(0, min(self._xoff, max(0, total_w - width)))
        self._draw_header(cols)
        b, c = self.body, self.c
        b.delete("all")
        height = max(1, b.winfo_height())
        self._clamp_scroll()
        first, last = self._visible_range()
        rh, x0 = self.row_h, -self._xoff
        for i in range(first, last):
            row = self.rows[i]
            k = self.key(row)
            y = i * rh - self._yoff
            selected = k in self.selection
            bg = c["selected"] if selected else c["surface_hover"] if i == self._hover else (
                c["row_alt"] if i % 2 else c["background"])
            b.create_rectangle(0, y, width, y + rh, fill=bg, width=0)
            if selected:
                b.create_rectangle(0, y + 6, round(3 * self.scale), y + rh - 6, fill=c["primary"], width=0)
            for col, cx, cw in cols:
                self._draw_cell(row, col, x0 + cx, y, cw, rh, selected)
        if self._drag and self._drag.get("active") and self._drag.get("target") is not None:
            ty = self._drag["target"] * rh - self._yoff
            b.create_line(0, ty, width, ty, fill=c["primary"], width=max(2, round(2 * self.scale)))
        if not self.rows and self.empty_text:
            b.create_text(width // 2, height // 2 - 10, text=self.empty_text, fill=c["faint"], font=self.font,
                          justify="center", width=max(200, width - 80))
        total_h = len(self.rows) * rh
        if total_h <= height:
            self.vbar.set(0, 1)
        else:
            self.vbar.set(self._yoff / total_h, (self._yoff + height) / total_h)

    def _draw_header(self, cols):
        h, c = self.header, self.c
        h.delete("all")
        width = max(1, h.winfo_width())
        h.create_rectangle(0, 0, width, self.head_h, fill=c["background"], width=0)
        h.create_line(0, self.head_h - 1, width, self.head_h - 1, fill=c["border"])
        for col, cx, cw in cols:
            x = cx - self._xoff
            sorted_here = col["key"] == self.sort_key
            title = col["title"]
            fg = c["text"] if sorted_here else c["muted"]
            anchor = col.get("anchor", "w")
            tx = x + self.pad if anchor == "w" else x + cw - self.pad if anchor == "e" else x + cw // 2
            arrow_w = round(14 * self.scale) if sorted_here else 0
            text = self._truncate(title, cw - 2 * self.pad - arrow_w, self.font_bold)
            h.create_text(tx - (arrow_w if anchor == "e" else 0), self.head_h // 2, text=text, anchor=anchor,
                          fill=fg, font=self.font_bold)
            if sorted_here:
                ax = (tx + self.font_bold.measure(text) + round(4 * self.scale)) if anchor == "w" else tx - arrow_w + 2
                if anchor == "center":
                    ax = tx + self.font_bold.measure(text) // 2 + round(4 * self.scale)
                h.create_text(ax, self.head_h // 2, anchor="w", fill=c["primary"], font=self.font_small,
                              text=icons.glyph("sort_down" if self.sort_reverse else "sort_up"))
            h.create_line(x + cw - 1, 8, x + cw - 1, self.head_h - 8, fill=c["border_soft"])

    def _draw_cell(self, row, col, x, y, w, h, selected):
        b, c, key = self.body, self.c, col["key"]
        kind = col.get("kind", "text")
        value = self.cell(row, key)
        mid = y + h // 2
        inner = w - 2 * self.pad
        if inner <= 4:
            return
        if kind == "name":
            glyph, text = value
            ix = x + self.pad
            b.create_text(ix, mid, text=glyph, anchor="w", fill=c["primary"] if selected else c["muted"],
                          font=self.font_icon)
            off = round(24 * self.scale)
            b.create_text(ix + off, mid, text=self._truncate(text, inner - off, self.font), anchor="w",
                          fill=c["text"], font=self.font)
        elif kind == "progress":
            fraction, label, state = value
            lw = round(40 * self.scale)
            bar_w = max(10, inner - lw - round(6 * self.scale))
            bx = x + self.pad
            thick = max(4, round(6 * self.scale))
            fill = {"done": c["success"], "failed": c["danger"], "paused": c["warning"]}.get(state, c["primary"])
            b.create_line(bx, mid, bx + bar_w, mid, fill=c["track"], width=thick, capstyle="round")
            if fraction:
                b.create_line(bx, mid, bx + max(1, round(bar_w * min(1.0, fraction))), mid, fill=fill, width=thick,
                              capstyle="round")
            elif state == "active":  # unknown size: a short sliver shows it's alive
                b.create_line(bx, mid, bx + round(bar_w * 0.08), mid, fill=fill, width=thick, capstyle="round")
            b.create_text(x + w - self.pad, mid, text=label, anchor="e", fill=c["muted"], font=self.font_small)
        elif kind == "status":
            text, state = value
            fg = {"active": c["primary"], "done": c["success"], "failed": c["danger"], "paused": c["warning"]}.get(
                state, c["muted"])
            dot = round(3 * self.scale)
            dx = x + self.pad + dot
            b.create_oval(dx - dot, mid - dot, dx + dot, mid + dot, fill=fg, width=0)
            off = round(12 * self.scale)
            b.create_text(dx + off, mid, text=self._truncate(text, inner - off - dot, self.font), anchor="w",
                          fill=c["text"] if state != "failed" else fg, font=self.font)
        else:
            text = value if isinstance(value, str) else ("" if value is None else str(value))
            anchor = col.get("anchor", "w")
            tx = x + self.pad if anchor == "w" else x + w - self.pad if anchor == "e" else x + w // 2
            b.create_text(tx, mid, text=self._truncate(text, inner, self.font), anchor=anchor,
                          fill=c["muted"] if col.get("muted") else c["text"], font=self.font)

    def _truncate(self, text, width, font):
        if width <= 0 or not text:
            return ""
        cache_key = (text, width, str(font))
        hit = self._trunc_cache.get(cache_key)
        if hit is not None:
            return hit
        if font.measure(text) <= width:
            out = text
        else:
            lo, hi = 0, len(text)
            while lo < hi:  # longest prefix that fits with an ellipsis
                mid = (lo + hi + 1) // 2
                if font.measure(text[:mid] + "…") <= width:
                    lo = mid
                else:
                    hi = mid - 1
            out = text[:lo] + "…" if lo else ""
        if len(self._trunc_cache) > 6000:
            self._trunc_cache.clear()
        self._trunc_cache[cache_key] = out
        return out

    def _update_hbar(self, total_w, width):
        need = total_w > width + 1
        if need != self._hbar_shown:
            self._hbar_shown = need
            if need:
                self.hbar.grid(row=2, column=0, sticky="ew")
            else:
                self.hbar.grid_remove()
                self._xoff = 0
        if need:
            self.hbar.set(self._xoff / total_w, (self._xoff + width) / total_w)

    # =========================================================== scroll === #
    def _on_vbar(self, *args):
        total = len(self.rows) * self.row_h
        height = max(1, self.body.winfo_height())
        if args[0] == "moveto":
            self._yoff = int(float(args[1]) * total)
        elif args[0] == "scroll":
            step = self.row_h if args[2] == "units" else height
            self._yoff += int(args[1]) * step
        self._yoff = max(0, min(self._yoff, max(0, total - height)))
        self.redraw()

    def _on_hbar(self, *args):
        _, total = self._visible_columns()
        if args[0] == "moveto":
            self._xoff = int(float(args[1]) * total)
        elif args[0] == "scroll":
            self._xoff += int(args[1]) * 40
        self.redraw()

    def _on_wheel(self, event):
        self._yoff -= int(event.delta / 120 * self.row_h * 3)
        self._clamp_scroll()
        self.redraw()

    def _scroll_x(self, delta):
        self._xoff = max(0, self._xoff + int(delta / 120 * 60))
        self.redraw()

    def ensure_visible(self, index):
        height = max(1, self.body.winfo_height())
        top = index * self.row_h
        if top < self._yoff:
            self._yoff = top
        elif top + self.row_h > self._yoff + height:
            self._yoff = top + self.row_h - height
        self._clamp_scroll()

    # ======================================================== selection === #
    def _fire_select(self):
        if self.on_select:
            self.on_select(self.selected())

    def _on_click(self, event, ctrl=False, shift=False):
        self.body.focus_set()
        idx = self._row_at(event.y)
        if idx is None:
            if not (ctrl or shift):
                self.selection.clear()
                self.redraw()
                self._fire_select()
            return
        k = self.key(self.rows[idx])
        if shift and self._anchor in self._index:
            a = self._index[self._anchor]
            lo, hi = sorted((a, idx))
            self.selection = {self.key(r) for r in self.rows[lo:hi + 1]}
        elif ctrl:
            self.selection ^= {k}
            self._anchor = k
        else:
            if k in self.selection and len(self.selection) > 1:
                # might be the start of a drag of the whole selection
                self._drag = {"start_y": event.y, "active": False, "target": None, "pending_single": k}
                return
            self.selection = {k}
            self._anchor = k
        self._drag = {"start_y": event.y, "active": False, "target": None} if not (ctrl or shift) else None
        self.redraw()
        self._fire_select()

    def _move_sel(self, step, event, extend=False):
        if not self.rows:
            return "break"
        current = self._index.get(self._anchor, -1 if step > 0 else len(self.rows))
        if not extend and self.selection:
            current = max(self._index[k] for k in self.selection) if step > 0 else min(
                self._index[k] for k in self.selection)
        idx = max(0, min(len(self.rows) - 1, current + step))
        k = self.key(self.rows[idx])
        if extend and self._anchor in self._index:
            a = self._index[self._anchor]
            lo, hi = sorted((a, idx))
            self.selection = {self.key(r) for r in self.rows[lo:hi + 1]}
        else:
            self.selection = {k}
            self._anchor = k
        self.ensure_visible(idx)
        self.redraw()
        self._fire_select()
        return "break"

    def _activate_selected(self):
        rows = self.selected()
        if rows and self.on_activate:
            self.on_activate(rows[0])

    def _on_double(self, event):
        idx = self._row_at(event.y)
        if idx is not None and self.on_activate:
            self.on_activate(self.rows[idx])

    def _on_right_click(self, event):
        self.body.focus_set()
        idx = self._row_at(event.y)
        if idx is None:
            return
        k = self.key(self.rows[idx])
        if k not in self.selection:
            self.selection = {k}
            self._anchor = k
            self.redraw()
            self._fire_select()
        if self.on_context:
            self.on_context(event, self.selected())

    def _on_motion(self, event):
        self._set_hover(self._row_at(event.y))

    def _set_hover(self, idx):
        if idx != self._hover:
            self._hover = idx
            self.redraw()

    # ============================================================= drag === #
    def can_reorder(self):
        return self.on_reorder is not None and self.sort_key is None

    def _on_drag_motion(self, event):
        d = self._drag
        if not d or not self.can_reorder() or not self.selection:
            return
        if not d["active"] and abs(event.y - d["start_y"]) > 6 * self.scale:
            d["active"] = True
            self.body.configure(cursor="fleur")
        if d["active"]:
            if event.y < self.row_h // 2:
                self._yoff = max(0, self._yoff - self.row_h // 2)
            elif event.y > self.body.winfo_height() - self.row_h // 2:
                self._yoff += self.row_h // 2
                self._clamp_scroll()
            target = (self._yoff + event.y + self.row_h // 2) // self.row_h
            d["target"] = max(0, min(len(self.rows), target))
            self.redraw()

    def _on_release(self, event):
        d, self._drag = self._drag, None
        self.body.configure(cursor="")
        if not d:
            return
        if d.get("active") and d.get("target") is not None:
            if self.on_reorder:
                self.on_reorder(self.selected(), d["target"])
        elif d.get("pending_single"):
            self.selection = {d["pending_single"]}
            self._anchor = d["pending_single"]
            self._fire_select()
        self.redraw()

    # =========================================================== header === #
    def _header_hit(self, x):
        cols, _ = self._visible_columns()
        x += self._xoff
        edge = 5 * self.scale
        for col, cx, cw in cols:
            if abs(x - (cx + cw)) <= edge and not col.get("stretch"):
                return col, "edge", cx
            if cx <= x < cx + cw:
                return col, "body", cx
        return None, None, 0

    def _on_header_motion(self, event):
        _, part, _ = self._header_hit(event.x)
        self.header.configure(cursor="sb_h_double_arrow" if part == "edge" else "hand2" if part else "")

    def _on_header_press(self, event):
        col, part, cx = self._header_hit(event.x)
        if part == "edge":
            self._resize = (col, event.x, col["width"])
        elif part == "body" and col.get("sortable", True):
            self._resize = None
            self._pending_sort = col["key"]

    def _on_header_drag(self, event):
        if self._resize:
            col, start_x, start_w = self._resize
            col["width"] = max(col.get("min", 40), round(start_w + (event.x - start_x) / self.scale))
            self.redraw()

    def _on_header_release(self, event):
        if self._resize:
            self._resize = None
            if self.on_columns_changed:
                self.on_columns_changed(self.column_state())
            return
        key = getattr(self, "_pending_sort", None)
        self._pending_sort = None
        if key and self.on_sort:
            self.on_sort(key)

    def _on_header_menu(self, event):
        menu = tk.Menu(self, tearoff=0, bg=self.c["surface"], fg=self.c["text"], activebackground=self.c["surface_hover"],
                       activeforeground=self.c["text"], bd=0)
        for col in self.columns:
            var = tk.BooleanVar(value=col.get("visible", True))
            menu.add_checkbutton(label=col["title"] or col["key"], variable=var,
                                 state="disabled" if col.get("required") else "normal",
                                 command=lambda c=col, v=var: self._toggle_column(c, v.get()))
            menu._vars = getattr(menu, "_vars", []) + [var]
        menu.add_separator()
        menu.add_command(label="Reset columns", command=self._reset_columns)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _toggle_column(self, col, visible):
        col["visible"] = visible
        self.redraw()
        if self.on_columns_changed:
            self.on_columns_changed(self.column_state())

    def _reset_columns(self):
        for col in self.columns:
            col["width"] = col.get("default_width", col["width"])
            col["visible"] = col.get("default_visible", True)
        self.redraw()
        if self.on_columns_changed:
            self.on_columns_changed(self.column_state())
