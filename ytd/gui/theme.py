"""Flat, minimal design system for the desktop GUI.

Dark mode: near-black surfaces with white text and white primary buttons.
Light mode: white surfaces with black text and black primary buttons.
No transparency, textures, glow, highlight lines or decorative animation.
Red / green / amber are used only to communicate status (failed / done / warning).

Function names (glass_button, glass_card, ...) are kept from the earlier design
so call sites didn't need to change; they now produce plain flat widgets.
"""

import json
import os

import customtkinter as ctk

TRANSPARENT_KEY = "#FE01FE"  # kept for compatibility; never used for transparency any more

_DARK = {
    "bg_top": "#121212", "bg_mid": "#121212", "bg_bottom": "#121212",
    "background": "#121212",
    "sidebar": "#181818",
    "surface_raised": "#1E1E1E",
    "surface": "#1A1A1A",
    "surface_hover": "#2A2A2A",
    "surface_deep": "#161616",
    "border": "#2E2E2E",
    "border_soft": "#242424",
    "border_bright": "#9A9A9A",
    "glass_highlight": "#2E2E2E",
    "text": "#FFFFFF",
    "muted": "#A3A3A3",
    "faint": "#6B6B6B",
    "primary": "#FFFFFF",
    "primary_hover": "#DADADA",
    "primary_pressed": "#BDBDBD",
    "primary_deep": "#BDBDBD",
    "on_primary": "#111111",
    "selected": "#3A3A3A",
    "violet": "#FFFFFF",
    "violet_hover": "#DADADA",
    "cyan": "#FFFFFF",
    "accent": "#FFFFFF",
    "success": "#5CB85C",
    "warning": "#E0A030",
    "danger": "#E5534B",
    "danger_hover": "#EC6F68",
    "danger_pressed": "#C9433C",
    "nebula_indigo": "#121212", "nebula_violet": "#121212", "nebula_magenta": "#121212",
}

_LIGHT = {
    "bg_top": "#FFFFFF", "bg_mid": "#FFFFFF", "bg_bottom": "#FFFFFF",
    "background": "#FFFFFF",
    "sidebar": "#F7F7F7",
    "surface_raised": "#FFFFFF",
    "surface": "#FFFFFF",
    "surface_hover": "#F0F0F0",
    "surface_deep": "#FAFAFA",
    "border": "#E0E0E0",
    "border_soft": "#EDEDED",
    "border_bright": "#555555",
    "glass_highlight": "#E0E0E0",
    "text": "#111111",
    "muted": "#5F5F5F",
    "faint": "#9A9A9A",
    "primary": "#111111",
    "primary_hover": "#333333",
    "primary_pressed": "#000000",
    "primary_deep": "#000000",
    "on_primary": "#FFFFFF",
    "selected": "#E2E2E2",
    "violet": "#111111",
    "violet_hover": "#333333",
    "cyan": "#111111",
    "accent": "#111111",
    "success": "#1E8E3E",
    "warning": "#B26A00",
    "danger": "#D93025",
    "danger_hover": "#E2554B",
    "danger_pressed": "#B3261E",
    "nebula_indigo": "#FFFFFF", "nebula_violet": "#FFFFFF", "nebula_magenta": "#FFFFFF",
}

COLORS = {name: (_LIGHT[name], _DARK[name]) for name in _DARK}

RADIUS = {"xs": 4, "sm": 6, "md": 8, "lg": 10, "xl": 12}
SPACING = {"xs": 4, "sm": 8, "md": 12, "lg": 16, "xl": 24}
BORDER_WIDTH = {"thin": 1, "bright": 1}
ANIM_MS = {"hover": 0, "press": 0, "fade": 0, "card_pop": 0, "sweep": 0}

_REDUCED_MOTION = {"value": True}


def set_reduced_motion(enabled):  # kept for the settings checkbox; the theme has no animation anyway
    _REDUCED_MOTION["value"] = True


def reduced_motion():
    return True


def set_glass_transparency_supported(enabled):
    pass


def glass_transparency_supported():
    return False


def real_glass_color(fallback_key):
    return COLORS[fallback_key]


def font(size=13, weight="normal"):
    return ctk.CTkFont(family="Segoe UI", size=size, weight=weight)


def install_ctk_theme():
    """Monochrome theme for the stock CustomTkinter widgets (checkboxes,
    dropdowns, switches, scrollbars...) so nothing falls back to blue."""
    base = os.path.join(os.path.dirname(ctk.__file__), "assets", "themes", "blue.json")
    with open(base, encoding="utf-8") as f:
        theme = json.load(f)
    L, D = _LIGHT, _DARK

    def pair(key):
        return [L[key], D[key]]

    theme["CTk"]["fg_color"] = pair("background")
    theme["CTkToplevel"]["fg_color"] = pair("background")
    theme["CTkFrame"].update(fg_color=pair("surface"), top_fg_color=pair("surface_raised"), border_color=pair("border"))
    theme["CTkButton"].update(fg_color=pair("primary"), hover_color=pair("primary_hover"), border_color=pair("border"),
                              text_color=pair("on_primary"), text_color_disabled=pair("faint"))
    theme["CTkLabel"]["text_color"] = pair("text")
    theme["CTkEntry"].update(fg_color=pair("surface_deep"), border_color=pair("border"), text_color=pair("text"),
                             placeholder_text_color=pair("faint"))
    for key in ("CTkCheckBox", "CTkRadioButton"):
        theme[key].update(fg_color=pair("primary"), border_color=pair("muted"), hover_color=pair("primary_hover"),
                          text_color=pair("text"), text_color_disabled=pair("faint"))
    theme["CTkCheckBox"]["checkmark_color"] = pair("on_primary")
    theme["CTkSwitch"].update(fg_color=pair("border"), progress_color=pair("primary"), button_color=pair("muted"),
                              button_hover_color=pair("text"), text_color=pair("text"))
    theme["CTkProgressBar"].update(fg_color=pair("border"), progress_color=pair("primary"), border_color=pair("border"))
    theme["CTkSlider"].update(fg_color=pair("border"), progress_color=pair("primary"), button_color=pair("primary"),
                              button_hover_color=pair("primary_hover"))
    for key in ("CTkOptionMenu", "CTkComboBox"):
        theme[key].update(fg_color=pair("surface_hover"), button_color=pair("selected"),
                          button_hover_color=pair("border_bright"), text_color=pair("text"))
    theme["CTkComboBox"]["border_color"] = pair("border")
    theme["CTkScrollbar"].update(button_color=pair("border"), button_hover_color=pair("muted"))
    theme["CTkSegmentedButton"].update(fg_color=pair("surface_deep"), selected_color=pair("selected"),
                                       selected_hover_color=pair("selected"), unselected_color=pair("surface_deep"),
                                       unselected_hover_color=pair("surface_hover"), text_color=pair("text"))
    theme["CTkTextbox"].update(fg_color=pair("surface_deep"), border_color=pair("border"), text_color=pair("text"),
                               scrollbar_button_color=pair("border"), scrollbar_button_hover_color=pair("muted"))
    theme["CTkScrollableFrame"]["label_fg_color"] = pair("surface")
    theme["DropdownMenu"].update(fg_color=pair("surface_raised"), hover_color=pair("surface_hover"), text_color=pair("text"))
    path = os.path.join(os.environ.get("TEMP") or os.path.expanduser("~"), "mdm-flat-theme.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(theme, f)
    ctk.set_default_color_theme(path)


def glass_card(parent, *, radius="lg", elevated=False, highlight=True, **overrides):
    """A plain bordered panel."""
    kwargs = dict(
        corner_radius=RADIUS[radius] if isinstance(radius, str) else radius,
        fg_color=COLORS["surface_raised" if elevated else "surface"],
        border_width=1,
        border_color=COLORS["border"],
    )
    kwargs.update(overrides)
    return ctk.CTkFrame(parent, **kwargs)


_BUTTON_VARIANTS = {
    "primary": dict(fg_color=COLORS["primary"], hover_color=COLORS["primary_hover"], text_color=COLORS["on_primary"],
                    border_width=0),
    "secondary": dict(fg_color=COLORS["surface_raised"], hover_color=COLORS["surface_hover"], text_color=COLORS["text"],
                      border_width=1, border_color=COLORS["border"]),
    "danger": dict(fg_color=COLORS["danger"], hover_color=COLORS["danger_hover"], text_color=("#FFFFFF", "#FFFFFF"),
                   border_width=0),
    "ghost": dict(fg_color="transparent", hover_color=COLORS["surface_hover"], text_color=COLORS["muted"],
                  border_width=0),
}


def glass_button(parent, text, command=None, *, variant="secondary", height=36, width=0,
                 radius="sm", weight="bold", size=12, **overrides):
    kwargs = dict(
        corner_radius=RADIUS[radius] if isinstance(radius, str) else radius,
        height=height, width=width, font=font(size, "normal" if variant == "ghost" else weight),
        text_color_disabled=COLORS["faint"], **_BUTTON_VARIANTS[variant],
    )
    kwargs.update(overrides)
    return ctk.CTkButton(parent, text=text, command=command, **kwargs)


def glass_icon_button(parent, icon_text, command=None, *, variant="secondary", size=36, **overrides):
    return glass_button(parent, icon_text, command, variant=variant, height=size, width=size,
                        radius="sm", weight="normal", size=14, **overrides)


def glass_primary_action_button(parent, text, command=None, *, height=48, radius="md", **overrides):
    return glass_button(parent, text, command, variant="primary", height=height, radius=radius,
                        weight="bold", size=14, **overrides)


def glass_entry(parent, textvariable=None, placeholder="", *, height=40, radius="sm", **overrides):
    kwargs = dict(
        corner_radius=RADIUS[radius] if isinstance(radius, str) else radius,
        fg_color=COLORS["surface_deep"], border_width=1,
        border_color=COLORS["border"], text_color=COLORS["text"],
        placeholder_text_color=COLORS["faint"], font=font(12),
    )
    kwargs.update(overrides)
    if textvariable is not None and placeholder:
        # CTkEntry never shows a placeholder while a textvariable is bound, so
        # keep the variable in sync manually instead of binding it.
        entry = ctk.CTkEntry(parent, placeholder_text=placeholder, height=height, **kwargs)
        _sync_entry_with_variable(entry, textvariable)
    else:
        entry = ctk.CTkEntry(parent, textvariable=textvariable, placeholder_text=placeholder or None,
                             height=height, **kwargs)
    entry.bind("<FocusIn>", lambda e: entry.configure(border_color=COLORS["border_bright"]), add="+")
    entry.bind("<FocusOut>", lambda e: entry.configure(border_color=COLORS["border"]), add="+")
    return entry


def _sync_entry_with_variable(entry, var):
    state = {"busy": False}

    def entry_to_var(_event=None):
        if state["busy"]:
            return
        state["busy"] = True
        try:
            if var.get() != entry.get():
                var.set(entry.get())
        finally:
            state["busy"] = False

    def var_to_entry(*_args):
        if state["busy"] or not entry.winfo_exists():
            return
        state["busy"] = True
        try:
            value = var.get()
            if entry.get() != value:
                entry.delete(0, "end")
                if value:
                    entry.insert(0, value)
        finally:
            state["busy"] = False

    if var.get():
        entry.insert(0, var.get())
    for sequence in ("<KeyRelease>", "<<Paste>>", "<<Cut>>", "<FocusOut>"):
        entry.bind(sequence, lambda e: entry.after_idle(entry_to_var), add="+")
    var.trace_add("write", var_to_entry)  # var_to_entry ignores destroyed entries


def glass_progressbar(parent, *, height=8, radius=None, **overrides):
    kwargs = dict(height=height, corner_radius=(radius if radius is not None else height // 2),
                  fg_color=COLORS["border"], progress_color=COLORS["primary"])
    kwargs.update(overrides)
    return ctk.CTkProgressBar(parent, **kwargs)


def glass_segmented(parent, values, variable=None, command=None, **overrides):
    kwargs = dict(
        fg_color=COLORS["surface_deep"], selected_color=COLORS["selected"],
        selected_hover_color=COLORS["selected"], unselected_color=COLORS["surface_deep"],
        unselected_hover_color=COLORS["surface_hover"], text_color=COLORS["text"],
        font=font(12, "bold"), corner_radius=RADIUS["sm"],
    )
    kwargs.update(overrides)
    return ctk.CTkSegmentedButton(parent, values=values, variable=variable, command=command, **kwargs)


def glass_format_button(parent, text, command=None, *, selected=False, **overrides):
    """A choice pill (quality / bitrate). Selected = inverted (white in dark mode, black in light)."""
    kwargs = dict(radius="sm", height=32)
    kwargs.update(overrides)
    return glass_button(parent, text, command, variant="primary" if selected else "secondary", **kwargs)


def fade_in_toplevel(win, duration_ms=None, steps=8):
    """No animation in the flat theme."""
    return None


def apply_active_nav_indicator(rail_parent, button, color=None):
    """A thin bar beside the selected sidebar item."""
    color = color or COLORS["text"]
    indicator = getattr(rail_parent, "_ytd_nav_indicator", None)
    if indicator is None or not indicator.winfo_exists():
        indicator = ctk.CTkFrame(rail_parent, width=3, fg_color=color, corner_radius=1)
        rail_parent._ytd_nav_indicator = indicator
    indicator.place(in_=button, relx=0.0, rely=0.5, anchor="w", x=-6, relheight=0.6)
    return indicator
