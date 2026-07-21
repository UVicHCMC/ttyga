#!/usr/bin/env python3

"""
ttyga — GTK4 + Vte + libadwaita terminal with a profile sidebar.

Single-file Adw.Application that hosts a Gtk.Notebook of Vte.Terminal widgets
beside an Adw.OverlaySplitView sidebar populated from profiles.yaml. The
headerbar carries a UserBadge reflecting the active connection, plus
edit-profiles / sidebar-toggle / new-tab / split-pane / menu buttons. A Preferences window
controls colour scheme, sidebar layout, font, scrollback, and a few terminal
toggles.

Config lives under ~/.config/ttyga/:
  profiles.yaml   — list of profiles (ssh or clippet)
  settings.yaml   — user preferences
  app_state.json  — paned position, expanded groups, optional open tabs
"""

import copy
import gi
import xml.etree.ElementTree as ET
import os
import socket
import subprocess
import sys
import tempfile
import yaml
import json
import re
import logging
from datetime import datetime

gi.require_version('Gtk', '4.0')
gi.require_version('Vte', '3.91')
gi.require_version('Gdk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('GdkPixbuf', '2.0')
gi.require_version('PangoCairo', '1.0')

from gi.repository import Gtk, Vte, GLib, Gdk, Gio, GObject, Adw, Pango, GdkPixbuf, PangoCairo
from pathlib import Path

logger = logging.getLogger(__name__)

APP_NAME    = "ttyga"
APP_ID      = "ca.greg.ttyga"
APP_VERSION = "0.6.38"
APP_AUTHOR  = "greg"

_LOCAL_USER = os.environ.get('USER') or os.environ.get('LOGNAME', '')
_LOCAL_HOST = socket.gethostname()
APP_COMMENTS = (
    "Terminal launcher with a profile sidebar. "
    "Profiles can open SSH sessions or run arbitrary commands. "
    "Supports multiple tabs, colour schemes, and per-session classifiers."
)

# XDG locations (with a fallback read from the script directory for legacy
# installs that kept profiles.yaml next to main.py).
CONFIG_DIR    = Path(GLib.get_user_config_dir()) / APP_NAME
CONFIG_FILE   = CONFIG_DIR / "profiles.yaml"
SETTINGS_FILE = CONFIG_DIR / "settings.yaml"
STATE_FILE    = CONFIG_DIR / "app_state.json"
LEGACY_CONFIG    = Path(__file__).parent / "profiles.yaml"
MONO_ICON_NAME   = "ttyga-mono"   # themed icon name (ttyga-icon-theme/.../apps)

# These will be overridden by settings in ~/.config/ttyga/settings.yaml
DEFAULT_SETTINGS = {
    'color_scheme':      'nord',      # light | dark | nord
    'sidebar_layout':    'expanders', # expanders | flat
    'terminal_font':     'Monospace 13',
    'scrollback':        10000,
    'scroll_speed':      3,             # lines per wheel tick (1–10)
    'copy_on_selection': True,
    'restore_tabs':      True,
    'welcome_image':     '',          # path to PNG/SVG; empty = monochrome icon
    'ssh_color':         '',          # SSH tab/dot colour; empty = theme default
}

# ---------------------------------------------------------------------------
# UI dimensions
# ---------------------------------------------------------------------------

WIN_DEFAULT_W    = 1100   # main window
WIN_DEFAULT_H    = 700
EDITOR_W         = 760    # Edit Profiles dialog
EDITOR_H         = 540
PREFS_W          = 640    # Preferences dialog
PREFS_H          = 560

SIDEBAR_FRACTION = 0.22   # default sidebar width as fraction of window
SIDEBAR_MIN_W    = 200    # pixels
SIDEBAR_MAX_W    = 360    # pixels
SIDEBAR_HANDLE_W = 4      # drag-handle width in pixels

CLOCK_TIME_AREA_H = 64    # sidebar clock: time drawing area height, pixels
CLOCK_TIME_MIN_PX = 20    # sidebar clock: smallest time font size, pixels
CLOCK_TIME_MAX_PX = 80    # sidebar clock: largest time font size, pixels
CLOCK_DATE_AREA_H = 26    # sidebar clock: date drawing area height, pixels
CLOCK_DATE_MIN_PX = 10    # sidebar clock: smallest date font size, pixels
CLOCK_DATE_MAX_PX = 22    # sidebar clock: largest date font size, pixels

PROFILE_ICON_PX  = 24     # sidebar profile button icon size

def _is_gtk_icon(s):
    """True if s looks like a GTK icon name (pure ASCII, lowercase/digits/hyphens)."""
    return bool(s) and bool(re.match(r'^[a-z0-9][a-z0-9_-]*$', s))

def _implied_cwd(cmd):
    """Return the directory implied by a leading 'cd PATH' in cmd, or None.

    Handles the common clippet pattern 'cd /some/path && program …' so that
    split-pane and open-in actions land in the right directory even when the
    shell never emits an OSC 7 sequence (e.g. because a program like claude
    is blocking the prompt).
    """
    if not cmd:
        return None
    m = re.match(r'cd\s+([^\s&;|]+)', cmd.strip())
    if m:
        path = str(Path(m.group(1)).expanduser())
        if os.path.isdir(path):
            return path
    return None

def _apply_vars(text, vars_dict):
    """Substitute @key tokens in text using vars_dict; unmatched tokens are left as-is."""
    if not vars_dict or not text:
        return text
    return re.sub(r'@(\w+)', lambda m: str(vars_dict.get(m.group(1), m.group(0))), text)

def _read_ssh_config():
    """Parse ~/.ssh/config; return {alias: {hostname, user, port, ...}} (lowercase keys).
    Wildcard/glob Host entries are skipped."""
    config_path = Path.home() / '.ssh' / 'config'
    if not config_path.exists():
        return {}
    hosts = {}
    current_aliases, current_opts = [], {}
    try:
        with open(config_path) as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith('#'):
                    continue
                parts = line.split(None, 1)
                if len(parts) < 2:
                    continue
                key, value = parts[0].lower(), parts[1].strip()
                if key == 'host':
                    if current_aliases:
                        for alias in current_aliases:
                            if '*' not in alias and '?' not in alias:
                                hosts[alias] = dict(current_opts)
                    current_aliases = value.split()
                    current_opts = {}
                else:
                    current_opts[key] = value
        if current_aliases:
            for alias in current_aliases:
                if '*' not in alias and '?' not in alias:
                    hosts[alias] = dict(current_opts)
    except OSError:
        pass
    return hosts

def _parse_groups(config):
    """Return (names: list[str], icons: dict[str, str]) from the groups key.

    Each entry may be a plain string or a dict with 'name' and optional 'icon'.
    """
    names, icons = [], {}
    for entry in config.get('groups', []):
        if isinstance(entry, dict):
            n = entry.get('name', '')
            i = entry.get('icon', '')
            if n:
                names.append(n)
                if i:
                    icons[n] = i
        elif isinstance(entry, str) and entry:
            names.append(entry)
    return names, icons

def _resolve_inheritance(profiles):
    """Resolve extends: chains and return a new flat list.

    Child fields win on conflict; dict fields (options, env, variables) are
    deep-merged so children can add keys without repeating the whole block.
    Profiles with hidden: true are preserved — callers filter them for display.
    """
    by_name = {p.get('name'): p for p in profiles if p.get('name')}

    def _merge(profile, seen):
        parent_name = profile.get('extends')
        if not parent_name:
            r = dict(profile)
            r.pop('extends', None)
            return r
        name = profile.get('name', '')
        if parent_name in seen:
            logger.warning("Profile inheritance cycle: %r → %r", name, parent_name)
            r = dict(profile)
            r.pop('extends', None)
            return r
        parent_raw = by_name.get(parent_name)
        if not parent_raw:
            logger.warning("Profile %r extends unknown profile %r", name, parent_name)
            r = dict(profile)
            r.pop('extends', None)
            return r
        parent = _merge(parent_raw, seen | {parent_name})
        merged = dict(parent)
        merged.pop('extends', None)
        for key, val in profile.items():
            if key == 'extends':
                continue
            if isinstance(val, dict) and isinstance(merged.get(key), dict):
                merged[key] = {**merged[key], **val}
            else:
                merged[key] = val
        return merged

    return [_merge(p, {p.get('name', '')}) for p in profiles]


SCROLLBACK_MIN   = 100
SCROLLBACK_MAX   = 1_000_000
SCROLLBACK_STEP  = 1_000

# Curated icons for the icon picker (icon_name, short display label)
CURATED_ICONS = [
    ('utilities-terminal-symbolic',       'terminal'),
    ('network-server-symbolic',           'server'),
    ('network-wired-symbolic',            'wired'),
    ('network-wireless-symbolic',         'wifi'),
    ('network-vpn-symbolic',              'vpn'),
    ('computer-symbolic',                 'desktop'),
    ('drive-harddisk-symbolic',           'disk'),
    ('drive-removable-media-symbolic',    'removable'),
    ('folder-symbolic',                   'folder'),
    ('folder-remote-symbolic',            'remote'),
    ('document-edit-symbolic',            'document'),
    ('text-x-generic-symbolic',           'text file'),
    ('system-run-symbolic',               'run'),
    ('system-shutdown-symbolic',          'shutdown'),
    ('system-reboot-symbolic',            'reboot'),
    ('process-stop-symbolic',             'kill'),
    ('media-playback-start-symbolic',     'play'),
    ('media-playback-stop-symbolic',      'stop'),
    ('media-record-symbolic',             'record'),
    ('security-high-symbolic',            'secure'),
    ('changes-prevent-symbolic',          'lock'),
    ('dialog-password-symbolic',          'password'),
    ('dialog-information-symbolic',       'info'),
    ('dialog-warning-symbolic',           'warning'),
    ('emblem-important-symbolic',         'important'),
    ('system-users-symbolic',             'users'),
    ('avatar-default-symbolic',           'person'),
    ('preferences-system-symbolic',       'prefs'),
    ('applications-system-symbolic',      'apps'),
    ('utilities-system-monitor-symbolic', 'monitor'),
    ('network-transmit-receive-symbolic', 'traffic'),
    ('network-receive-symbolic',          'receive'),
    ('network-transmit-symbolic',         'send'),
    ('mail-send-symbolic',                'mail'),
    ('go-home-symbolic',                  'home'),
    ('starred-symbolic',                  'starred'),
    ('bookmark-symbolic',                 'bookmark'),
    ('emblem-documents-symbolic',         'docs'),
    ('software-update-available-symbolic','update'),
    ('alarm-symbolic',                    'alarm'),
    ('go-up-symbolic',                    'up'),
    ('go-next-symbolic',                  'next'),
    ('view-refresh-symbolic',             'refresh'),
    ('edit-find-symbolic',                'search'),
    ('edit-copy-symbolic',                'copy'),
    ('list-add-symbolic',                 'add'),
    ('object-select-symbolic',            'select'),
    ('emblem-system-symbolic',            'sys emblem'),
    ('input-keyboard-symbolic',           'keyboard'),
]

# ---------------------------------------------------------------------------
# VTE ANSI palette slots not covered by per-theme tokens
# ---------------------------------------------------------------------------

VTE_BLACK_LIGHT  = '#000000'   # ANSI  0 light
VTE_BLACK_DARK   = '#171421'   # ANSI  0 dark/nord
VTE_CYAN         = '#2aa1b3'   # ANSI  6
VTE_WHITE        = '#d0cfcc'   # ANSI  7
VTE_BRIGHT_BLACK = '#5e5c64'   # ANSI  8
VTE_BRIGHT_CYAN  = '#33c7de'   # ANSI 14
VTE_BRIGHT_WHITE = '#ffffff'   # ANSI 15

# ---------------------------------------------------------------------------
# Design tokens — three palettes lifted directly from the design handoff.
# Each value flows into both the Gtk CSS provider (for the chrome) and the
# Vte.Terminal palette (for the terminal contents themselves).
# ---------------------------------------------------------------------------

THEMES = {
    'light': {
        'bg':              '#fafafa',
        'bg_elev':         '#ffffff',
        'bg_sidebar':      '#ebebeb',
        'bg_headerbar':    '#ebebeb',
        'bg_shaded':       '#e6e6e6',
        'fg':              '#2e3436',
        'fg_dim':          '#5e5c64',
        'fg_disabled':     '#9e9e9e',
        'border':          'rgba(0,0,0,0.10)',
        'border_strong':   'rgba(0,0,0,0.18)',
        'accent':          '#3584e4',
        'accent_fg':       '#ffffff',
        'destructive':     '#c01c28',
        'row_hover':       'rgba(0,0,0,0.05)',
        'row_active':      'rgba(0,0,0,0.08)',
        'term_bg':         '#ffffff',
        'term_fg':         '#1a1a1a',
        'term_prompt':     '#2870c0',
        'term_success':    '#26a269',
        'term_warn':       '#c64600',
        'term_err':        '#c01c28',
        'term_string':     '#813d9c',
    },
    'dark': {
        'bg':              '#242424',
        'bg_elev':         '#303030',
        'bg_sidebar':      '#1e1e1e',
        'bg_headerbar':    '#303030',
        'bg_shaded':       '#1e1e1e',
        'fg':              '#ffffff',
        'fg_dim':          '#b3b3b3',
        'fg_disabled':     '#6e6e6e',
        'border':          'rgba(255,255,255,0.10)',
        'border_strong':   'rgba(255,255,255,0.18)',
        'accent':          '#78aeed',
        'accent_fg':       '#00305c',
        'destructive':     '#ff7b63',
        'row_hover':       'rgba(255,255,255,0.05)',
        'row_active':      'rgba(255,255,255,0.10)',
        'term_bg':         '#1a1a1a',
        'term_fg':         '#ffffff',
        'term_prompt':     '#99c1f1',
        'term_success':    '#57e389',
        'term_warn':       '#ffa348',
        'term_err':        '#f66151',
        'term_string':     '#dc8add',
    },
    'nord': {
        'bg':              '#2e3440',
        'bg_elev':         '#3b4252',
        'bg_sidebar':      '#292e39',
        'bg_headerbar':    '#353b48',
        'bg_shaded':       '#262b35',
        'fg':              '#eceff4',
        'fg_dim':          '#aab2c0',
        'fg_disabled':     '#6c7383',
        'border':          'rgba(255,255,255,0.07)',
        'border_strong':   'rgba(255,255,255,0.14)',
        'accent':          '#88c0d0',
        'accent_fg':       '#1f2530',
        'destructive':     '#bf616a',
        'row_hover':       'rgba(255,255,255,0.04)',
        'row_active':      'rgba(136,192,208,0.16)',
        'term_bg':         '#232831',
        'term_fg':         '#d8dee9',
        'term_prompt':     '#88c0d0',
        'term_success':    '#a3be8c',
        'term_warn':       '#ebcb8b',
        'term_err':        '#bf616a',
        'term_string':     '#b48ead',
    },
}

DARK_LIKE = ('dark', 'nord')  # palettes that want a dark headerbar/sidebar


def _vte_palette(theme):
    """16-colour ANSI palette derived from the theme's term-* tokens.
    Bright colours are the same hues; Vte ignores bold-as-bright by default."""
    t = THEMES[theme]
    # 0-7: black, red, green, yellow, blue, magenta, cyan, white
    palette = [
        VTE_BLACK_LIGHT if theme == 'light' else VTE_BLACK_DARK,
        t['term_err'],
        t['term_success'],
        t['term_warn'],
        t['term_prompt'],
        t['term_string'],
        VTE_CYAN,
        VTE_WHITE,
        # 8-15: bright variants
        VTE_BRIGHT_BLACK,
        t['term_err'],
        t['term_success'],
        t['term_warn'],
        t['term_prompt'],
        t['term_string'],
        VTE_BRIGHT_CYAN,
        VTE_BRIGHT_WHITE,
    ]
    out = []
    for hex_ in palette:
        rgba = Gdk.RGBA()
        rgba.parse(hex_)
        out.append(rgba)
    return out


def _rgba(hex_or_rgba):
    rgba = Gdk.RGBA()
    rgba.parse(hex_or_rgba)
    return rgba


# ---------------------------------------------------------------------------
# CSS — generated per-theme. We layer a single provider above libadwaita's
# defaults and swap its content when the user changes themes.
# ---------------------------------------------------------------------------

def build_css(theme, ssh_color=''):
    t = THEMES[theme]
    is_dark = theme in DARK_LIKE
    return f"""
/* ttyga theme: {theme} */
window {{
    background-color: {t['bg']};
    color: {t['fg']};
}}

headerbar {{
    background-color: {t['bg_headerbar']};
    border-bottom: 1px solid {t['border']};
    min-height: 46px;
}}

headerbar button {{
    padding: 8px;
}}

headerbar button image,
headerbar splitbutton image {{
    -gtk-icon-size: 20px;
}}


/* Sidebar ----------------------------------------------------------------- */

.sidebar-pane {{
    background: {t['bg_sidebar']};
}}

.group-header {{
    font-size: 9pt;
    font-weight: 700;
    letter-spacing: 0.08em;
    color: {t['fg_dim']};
    padding: 14px 14px 4px 14px;
}}

.profile-row {{
    padding: 2px 6px;
    border-radius: 6px;
    margin: 1px 2px;
    font-size: 12pt;
}}
.profile-row:hover {{ background: {t['row_hover']}; }}
.profile-row.active {{
    background: {t['accent']};
    color: {t['accent_fg']};
}}
.profile-row.active image {{ color: {t['accent_fg']}; }}

/* Tabs -------------------------------------------------------------------- */

notebook header.top {{
    background: {t['bg_headerbar']};
    border-bottom: 1px solid {t['border']};
}}
notebook tab {{
    padding: 4px 8px;
    min-height: 28px;
    border-radius: 6px 6px 0 0;
}}
notebook tab:checked {{
    background: {t['bg']};
}}
notebook > header.top > tabs {{
    margin-bottom: 0;
}}
.tab-dot-ssh      {{ color: {ssh_color or ('#2ec27e' if theme == 'light' else '#57e389' if theme == 'dark' else '#a3d977')}; }}
.tab-dot-local    {{ color: {t['fg_dim']}; }}
.tab-dot-activity {{ color: {t['term_warn']}; }}
@keyframes tab-flash {{
    0%   {{ background-color: transparent; }}
    50%  {{ background-color: rgba({int(t['term_warn'][1:3],16)},{int(t['term_warn'][3:5],16)},{int(t['term_warn'][5:7],16)},0.28); }}
    100% {{ background-color: transparent; }}
}}
.tab-activity {{
    animation: tab-flash 1.2s ease-in-out infinite;
    border-radius: 4px;
}}

/* Editor list ------------------------------------------------------------- */

.editor-list row {{ padding: 4px 8px; }}
.editor-list row:selected {{
    background: {t['accent']};
    color: {t['accent_fg']};
}}
.editor-list row .dim-label,
.editor-list row .caption {{
    {'color: rgba(255,255,255,0.85);' if is_dark else 'color: ' + t['fg_dim'] + ';'}
}}
.editor-list row:selected .dim-label,
.editor-list row:selected .caption {{
    color: {t['accent_fg']};
    opacity: 0.85;
}}

/* Suggested-action buttons ------------------------------------------------ */

button.suggested-action {{
    background: {t['accent']};
    color: {t['accent_fg']};
}}
button.suggested-action:hover {{
    background: shade({t['accent']}, 1.15);
    color: {t['accent_fg']};
}}

/* Sidebar resize handle --------------------------------------------------- */

.sidebar-handle {{
    min-width: 1px;
    background: {t['border']};
    transition: background 120ms ease;
}}
.sidebar-handle:hover {{
    background: {t['accent']};
}}

/* Split panes ------------------------------------------------------------- */

paned > separator {{
    min-width: 3px;
    min-height: 3px;
    background: {t['border']};
    transition: background 120ms ease;
}}
paned > separator:hover {{
    background: {t['accent']};
}}

.pane-bar {{
    background: {t['bg_headerbar']};
    border-bottom: 1px solid {t['border']};
    min-height: 24px;
    padding: 4px 4px 2px 4px;
}}
.pane-bar label {{
    font-size: 9pt;
    color: {t['fg_dim']};
    padding-left: 4px;
}}
.pane-bar button {{
    padding: 1px;
    min-height: 18px;
    min-width: 18px;
    border-radius: 4px;
}}

/* Sidebar clock ------------------------------------------------------------ */
/* Both lines are Cairo/Pango-drawn so their font scales to width — see
   _draw_clock_time / _draw_clock_date — so there's no CSS to style here. */

/* Tab context menu popover ------------------------------------------------ */

popover > contents {{
    background: {t['bg_sidebar']};
    color: {t['fg']};
}}
popover > contents button.flat {{
    color: {t['fg']};
    border-radius: 4px;
}}
popover > contents button.flat:hover {{
    background: {t['row_hover']};
    color: {t['fg']};
}}

/* Welcome screen ---------------------------------------------------------- */

.welcome-title {{
    font-size: 1.6em;
    font-weight: 800;
}}

.welcome-bullet {{
    font-size: 0.95em;
    color: {t['fg_dim']};
}}

.welcome-hint {{
    font-size: 0.95em;
    color: {t['fg_dim']};
    font-style: italic;
}}

"""


# ---------------------------------------------------------------------------
# Icon picker dialog
# ---------------------------------------------------------------------------

class IconPickerDialog(Adw.Window):
    """Modal grid of curated symbolic icons; calls on_pick(icon_name) on selection."""

    def __init__(self, parent, on_pick):
        super().__init__(title="Choose Icon", transient_for=parent, modal=True)
        self._on_pick = on_pick
        self.set_default_size(500, 420)

        toolbar = Adw.ToolbarView()
        self.set_content(toolbar)

        header = Adw.HeaderBar()
        search = Gtk.SearchEntry()
        search.set_placeholder_text("Filter icons…")
        search.set_hexpand(True)
        search.connect('search-changed', self._on_search)
        header.set_title_widget(search)
        toolbar.add_top_bar(header)

        # Character input row — lets users type/paste any unicode character
        char_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        char_row.set_margin_top(8)
        char_row.set_margin_bottom(4)
        char_row.set_margin_start(12)
        char_row.set_margin_end(12)

        char_lbl = Gtk.Label(label="Or use a character:")
        char_lbl.add_css_class('dim-label')
        char_row.append(char_lbl)

        self._char_entry = Gtk.Entry()
        self._char_entry.set_placeholder_text("e.g. ★ 🖥 ⚡")
        self._char_entry.set_max_length(4)
        self._char_entry.set_width_chars(6)
        self._char_entry.connect('activate', self._on_char_use)
        char_row.append(self._char_entry)

        char_use_btn = Gtk.Button(label="Use")
        char_use_btn.add_css_class('suggested-action')
        char_use_btn.connect('clicked', self._on_char_use)
        char_row.append(char_use_btn)

        toolbar.add_bottom_bar(char_row)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)

        self._flowbox = Gtk.FlowBox()
        self._flowbox.set_homogeneous(True)
        self._flowbox.set_max_children_per_line(8)
        self._flowbox.set_min_children_per_line(4)
        self._flowbox.set_column_spacing(2)
        self._flowbox.set_row_spacing(2)
        self._flowbox.set_margin_top(8)
        self._flowbox.set_margin_bottom(8)
        self._flowbox.set_margin_start(8)
        self._flowbox.set_margin_end(8)
        self._flowbox.set_selection_mode(Gtk.SelectionMode.NONE)
        self._flowbox.connect('child-activated', self._on_child_activated)
        scroll.set_child(self._flowbox)
        toolbar.set_content(scroll)

        self._items = []
        for icon_name, short_label in CURATED_ICONS:
            child = self._make_cell(icon_name, short_label)
            self._flowbox.append(child)
            self._items.append((child, icon_name, short_label))

    def _make_cell(self, icon_name, short_label):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.set_margin_top(8)
        box.set_margin_bottom(8)
        box.set_margin_start(8)
        box.set_margin_end(8)
        box.set_tooltip_text(icon_name)

        img = Gtk.Image.new_from_icon_name(icon_name)
        img.set_pixel_size(32)
        box.append(img)

        lbl = Gtk.Label(label=short_label)
        lbl.add_css_class('caption')
        lbl.set_max_width_chars(10)
        lbl.set_ellipsize(Pango.EllipsizeMode.END)
        box.append(lbl)

        child = Gtk.FlowBoxChild()
        child.set_child(box)
        child.icon_name   = icon_name
        child.short_label = short_label
        return child

    def _on_search(self, entry):
        query = entry.get_text().strip().lower()
        for child, icon_name, short_label in self._items:
            child.set_visible(
                not query
                or query in icon_name.lower()
                or query in short_label.lower()
            )

    def _on_char_use(self, _widget):
        char = self._char_entry.get_text().strip()
        if char:
            self._on_pick(char)
            self.close()

    def _on_child_activated(self, _flowbox, child):
        self._on_pick(child.icon_name)
        self.close()


# ---------------------------------------------------------------------------
# Variable prompt dialog
# ---------------------------------------------------------------------------

class VariablePromptDialog(Adw.Window):
    """Modal dialog that prompts for profile variable values before launch."""

    def __init__(self, app, profile, recent_vars, on_launch):
        super().__init__(
            title=f"Launch: {profile.get('name', '')}",
            transient_for=app.window,
            modal=True)
        self._on_launch = on_launch
        self.set_default_size(380, -1)

        toolbar = Adw.ToolbarView()
        self.set_content(toolbar)

        header = Adw.HeaderBar()
        header.set_show_end_title_buttons(False)
        cancel_btn = Gtk.Button(label="Cancel")
        cancel_btn.add_css_class('flat')
        cancel_btn.connect('clicked', lambda _: self.close())
        header.pack_start(cancel_btn)
        toolbar.add_top_bar(header)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        outer.set_margin_top(16)
        outer.set_margin_bottom(16)
        outer.set_margin_start(16)
        outer.set_margin_end(16)

        variables = profile.get('variables') or {}
        self._entries = {}
        group = Adw.PreferencesGroup()
        for var_name, var_def in variables.items():
            if isinstance(var_def, dict):
                prompt  = var_def.get('prompt', var_name)
                default = str(var_def.get('default', ''))
            else:
                prompt  = var_name
                default = str(var_def) if var_def else ''
            initial = recent_vars.get(var_name) or default
            row = Adw.EntryRow(title=prompt)
            row.set_text(initial)
            row.connect('entry-activated', self._on_activate)
            self._entries[var_name] = row
            group.add(row)
        outer.append(group)

        launch_btn = Gtk.Button(label="Launch")
        launch_btn.add_css_class('suggested-action')
        launch_btn.connect('clicked', self._on_launch_clicked)
        outer.append(launch_btn)

        toolbar.set_content(outer)

    def _on_activate(self, _row):
        self._on_launch_clicked(None)

    def _on_launch_clicked(self, _btn):
        resolved = {k: row.get_text() for k, row in self._entries.items()}
        self._on_launch(resolved)
        self.close()


# Edit Profiles window
# ---------------------------------------------------------------------------

class EditorWindow(Adw.Window):
    def __init__(self, app, select_key=None):
        super().__init__(title="Edit Profiles", transient_for=app.window, modal=True)
        self._select_key = select_key
        self.app = app
        self.current_profile = None
        self.current_row = None
        self._loading = False

        self.set_default_size(EDITOR_W, EDITOR_H)

        # Snapshot for editing so Cancel discards changes.
        config = app.load_config()
        self.profiles = copy.deepcopy(config.get('profiles', []))

        # Build groups list: saved order + any referenced in profiles (de-duped).
        saved_names, self.group_icons = _parse_groups(config)
        seen: set = set()
        self.groups: list = []
        for g in saved_names + [p.get('group', 'General') for p in self.profiles]:
            if g and g not in seen:
                seen.add(g)
                self.groups.append(g)
        if not self.groups:
            self.groups = ['General']

        toolbar = Adw.ToolbarView()
        self.set_content(toolbar)

        header = Adw.HeaderBar()
        header.set_show_start_title_buttons(False)
        header.set_show_end_title_buttons(False)
        header.set_title_widget(Adw.WindowTitle.new("Edit Profiles", ""))

        cancel_btn = Gtk.Button(label="Cancel")
        cancel_btn.add_css_class('flat')
        cancel_btn.connect("clicked", lambda _b: self.close())
        header.pack_start(cancel_btn)

        save_btn = Gtk.Button(label="Save")
        save_btn.add_css_class('suggested-action')
        save_btn.connect("clicked", self._on_save)
        header.pack_end(save_btn)

        toolbar.add_top_bar(header)

        # --- Two-pane body ---------------------------------------------------
        body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        toolbar.set_content(body)

        # Left: profile list
        left_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        left_box.set_size_request(240, -1)
        left_box.add_css_class('editor-list')

        prof_scroll = Gtk.ScrolledWindow()
        prof_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        prof_scroll.set_vexpand(True)

        self.listbox = Gtk.ListBox()
        self.listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.listbox.connect("row-selected", self._on_row_selected)
        self.listbox.set_sort_func(self._sort_profiles)
        self.listbox.set_header_func(self._group_header_func)
        prof_scroll.set_child(self.listbox)
        left_box.append(prof_scroll)

        btn_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        btn_bar.set_margin_top(6)
        btn_bar.set_margin_bottom(6)
        btn_bar.set_margin_start(6)
        btn_bar.set_margin_end(6)

        add_btn = Gtk.Button(icon_name='list-add-symbolic')
        add_btn.add_css_class('flat')
        add_btn.set_tooltip_text("New profile")
        add_btn.connect("clicked", self._on_add)
        btn_bar.append(add_btn)

        del_btn = Gtk.Button(icon_name='list-remove-symbolic')
        del_btn.add_css_class('flat')
        del_btn.set_tooltip_text("Delete profile")
        del_btn.connect("clicked", self._on_delete)
        btn_bar.append(del_btn)
        left_box.append(btn_bar)

        left_sep = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        body.append(left_box)
        body.append(left_sep)

        # Right: form
        form_scroll = Gtk.ScrolledWindow()
        form_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        form_scroll.set_hexpand(True)

        self.form_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        self.form_box.set_margin_top(24)
        self.form_box.set_margin_bottom(24)
        self.form_box.set_margin_start(24)
        self.form_box.set_margin_end(24)
        self.form_box.set_sensitive(False)

        # Common fields
        common_grid = self._new_grid()
        self.name_entry = Gtk.Entry()

        # Group dropdown with + button to add new groups
        self._group_model = Gtk.StringList.new(self.groups)
        group_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.group_drop = Gtk.DropDown(model=self._group_model)
        self.group_drop.set_hexpand(True)
        edit_group_btn = Gtk.Button(icon_name='document-edit-symbolic')
        edit_group_btn.add_css_class('flat')
        edit_group_btn.set_valign(Gtk.Align.CENTER)
        edit_group_btn.set_tooltip_text("Edit group icon")
        edit_group_btn.connect('clicked', self._on_group_icon_edit)
        add_group_btn = Gtk.Button(icon_name='list-add-symbolic')
        add_group_btn.add_css_class('flat')
        add_group_btn.set_valign(Gtk.Align.CENTER)
        add_group_btn.set_tooltip_text("Add group")
        add_group_btn.connect('clicked', self._on_group_add)
        group_box.append(self.group_drop)
        group_box.append(edit_group_btn)
        group_box.append(add_group_btn)

        # Icon entry with Browse button
        icon_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.icon_entry = Gtk.Entry()
        self.icon_entry.set_placeholder_text("Icon name or character (e.g. ★ 🖥)")
        self.icon_entry.set_hexpand(True)
        browse_btn = Gtk.Button(label="Browse…")
        browse_btn.add_css_class('flat')
        browse_btn.set_valign(Gtk.Align.CENTER)
        browse_btn.connect('clicked', self._on_icon_browse)
        icon_box.append(self.icon_entry)
        icon_box.append(browse_btn)

        type_model     = Gtk.StringList.new(["ssh", "clippet"])
        self.type_drop = Gtk.DropDown(model=type_model)
        self.type_drop.connect("notify::selected", self._on_type_changed)

        color_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.color_entry = Gtk.Entry()
        self.color_entry.set_placeholder_text("#rrggbb  (optional)")
        self.color_entry.set_hexpand(True)
        self._color_dialog = Gtk.ColorDialog()
        self._color_dialog.set_with_alpha(False)
        self._color_btn = Gtk.ColorDialogButton(dialog=self._color_dialog)
        self._color_btn.set_valign(Gtk.Align.CENTER)
        self._color_btn.set_tooltip_text("Pick a colour")
        self._color_syncing = False
        self.color_entry.connect('changed', self._on_color_entry_changed)
        self._color_btn.connect('notify::rgba', self._on_color_btn_changed)
        color_box.append(self.color_entry)
        color_box.append(self._color_btn)

        theme_model = Gtk.StringList.new(["Default", "Light", "Dark", "Nord"])
        self.theme_drop = Gtk.DropDown(model=theme_model)
        self.theme_drop.set_halign(Gtk.Align.START)

        font_override_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.profile_font_entry = Gtk.Entry()
        self.profile_font_entry.set_placeholder_text(
            "e.g. Fira Code 14  (empty = global default)")
        self.profile_font_entry.set_hexpand(True)
        font_browse_btn = Gtk.Button(label="Choose…")
        font_browse_btn.add_css_class('flat')
        font_browse_btn.set_valign(Gtk.Align.CENTER)
        font_browse_btn.connect('clicked', self._on_profile_font_browse)
        font_override_box.append(self.profile_font_entry)
        font_override_box.append(font_browse_btn)

        self._attach(common_grid, 0, "Name",   self.name_entry)
        self._attach(common_grid, 1, "Group",  group_box)
        self._attach(common_grid, 2, "Type",   self.type_drop)
        self._attach(common_grid, 3, "Icon",   icon_box)
        self._attach(common_grid, 4, "Colour", color_box)
        self._attach(common_grid, 5, "Theme",  self.theme_drop)
        self._attach(common_grid, 6, "Font",   font_override_box)
        self.form_box.append(common_grid)

        self.form_box.append(Gtk.Separator())

        # Type-specific fields in a stack
        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.NONE)

        ssh_grid = self._new_grid()

        host_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.host_entry = Gtk.Entry()
        self.host_entry.set_hexpand(True)
        self.host_entry.set_placeholder_text("hostname or ssh-config alias")
        ssh_cfg_btn = Gtk.Button(label="From SSH config…")
        ssh_cfg_btn.add_css_class('flat')
        ssh_cfg_btn.set_valign(Gtk.Align.CENTER)
        ssh_cfg_btn.connect('clicked', self._on_ssh_config_pick)
        host_box.append(self.host_entry)
        host_box.append(ssh_cfg_btn)

        self.user_entry = Gtk.Entry()
        self.user_entry.set_placeholder_text("leave blank to use ssh config")
        self.port_entry = Gtk.Entry()
        self.port_entry.set_placeholder_text("leave blank to use ssh config")
        self.port_entry.set_max_width_chars(8)

        self._attach(ssh_grid, 0, "Host", host_box)
        self._attach(ssh_grid, 1, "User", self.user_entry)
        self._attach(ssh_grid, 2, "Port", self.port_entry)
        self.stack.add_named(ssh_grid, "ssh")

        clip_grid = self._new_grid()
        cmd_scroll = Gtk.ScrolledWindow()
        cmd_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        cmd_scroll.set_min_content_height(70)
        cmd_scroll.set_has_frame(True)
        self.command_view = Gtk.TextView()
        self.command_view.set_monospace(True)
        self.command_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.command_view.set_top_margin(6)
        self.command_view.set_bottom_margin(6)
        self.command_view.set_left_margin(8)
        self.command_view.set_right_margin(8)
        cmd_scroll.set_child(self.command_view)

        self.auto_exec_switch = Gtk.Switch()
        self.auto_exec_switch.set_halign(Gtk.Align.START)
        self.auto_exec_switch.set_valign(Gtk.Align.CENTER)

        self.in_place_switch = Gtk.Switch()
        self.in_place_switch.set_halign(Gtk.Align.START)
        self.in_place_switch.set_valign(Gtk.Align.CENTER)

        shell_split_model = Gtk.StringList.new(["Off", "Below", "Beside"])
        self.shell_split_drop = Gtk.DropDown(model=shell_split_model)
        self.shell_split_drop.set_halign(Gtk.Align.START)

        self._attach(clip_grid, 0, "Command",          cmd_scroll)
        self._attach(clip_grid, 1, "Auto-execute",     self.auto_exec_switch)
        self._attach(clip_grid, 2, "Run in current tab", self.in_place_switch)
        self._attach(clip_grid, 3, "Shell split",      self.shell_split_drop)
        self.stack.add_named(clip_grid, "clippet")
        self.form_box.append(self.stack)

        self.form_box.append(Gtk.Separator())

        # Classifier
        cl_grid = self._new_grid()
        self.classifier_entry = Gtk.Entry()
        self.classifier_entry.set_placeholder_text("optional — e.g. @user@@host")
        self._attach(cl_grid, 0, "Classifier title", self.classifier_entry)
        self.form_box.append(cl_grid)

        self.form_box.append(Gtk.Separator())

        # Launch settings (cwd + env)
        launch_grid = self._new_grid()
        self.cwd_entry = Gtk.Entry()
        self.cwd_entry.set_placeholder_text("~/projects/foo  (optional)")
        self._attach(launch_grid, 0, "Working dir", self.cwd_entry)

        env_scroll = Gtk.ScrolledWindow()
        env_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        env_scroll.set_min_content_height(50)
        env_scroll.set_has_frame(True)
        self.env_view = Gtk.TextView()
        self.env_view.set_monospace(True)
        self.env_view.set_wrap_mode(Gtk.WrapMode.NONE)
        self.env_view.set_top_margin(6)
        self.env_view.set_bottom_margin(6)
        self.env_view.set_left_margin(8)
        self.env_view.set_right_margin(8)
        env_scroll.set_child(self.env_view)
        self._attach(launch_grid, 1, "Environment", env_scroll)

        tmux_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        self.tmux_switch = Gtk.Switch()
        self.tmux_switch.set_halign(Gtk.Align.START)
        self.tmux_switch.set_valign(Gtk.Align.CENTER)
        self.tmux_session_entry = Gtk.Entry()
        self.tmux_session_entry.set_placeholder_text("session name (default: main)")
        self.tmux_session_entry.set_hexpand(True)
        self.tmux_switch.connect('notify::active', self._on_tmux_switch_changed)
        tmux_box.append(self.tmux_switch)
        tmux_box.append(self.tmux_session_entry)
        self._attach(launch_grid, 2, "tmux", tmux_box)

        self.notify_text_entry = Gtk.Entry()
        self.notify_text_entry.set_placeholder_text("e.g. Claude is waiting  (optional)")
        self._attach(launch_grid, 3, "Notification text", self.notify_text_entry)

        self.form_box.append(launch_grid)

        form_scroll.set_child(self.form_box)
        body.append(form_scroll)

        self.group_drop.connect('notify::selected', lambda *_: self._update_icon_placeholder())

        self._populate_list()

        target = None
        if self._select_key:
            i = 0
            while (row := self.listbox.get_row_at_index(i)):
                if (row.profile.get('name'), row.profile.get('group', 'General')) == self._select_key:
                    target = row
                    break
                i += 1
        if target is None:
            target = self.listbox.get_row_at_index(0)
        if target:
            self.listbox.select_row(target)

    def _new_grid(self):
        grid = Gtk.Grid()
        grid.set_row_spacing(10)
        grid.set_column_spacing(14)
        return grid

    def _attach(self, grid, row, label_text, widget):
        label = Gtk.Label(label=label_text)
        label.set_halign(Gtk.Align.END)
        label.set_valign(Gtk.Align.CENTER if not isinstance(widget, Gtk.ScrolledWindow)
                         else Gtk.Align.START)
        label.add_css_class('dim-label')
        label.set_size_request(120, -1)
        grid.attach(label, 0, row, 1, 1)
        widget.set_hexpand(True)
        grid.attach(widget, 1, row, 1, 1)

    def _sort_profiles(self, row_a, row_b):
        a, b = row_a.profile, row_b.profile
        ga = a.get('group', 'General').lower()
        gb = b.get('group', 'General').lower()
        if ga != gb:
            return -1 if ga < gb else 1
        na = a.get('name', '').lower()
        nb = b.get('name', '').lower()
        return -1 if na < nb else (1 if na > nb else 0)

    def _group_header_func(self, row, before):
        group = row.profile.get('group', 'General')
        if before is None or before.profile.get('group', 'General') != group:
            hdr = Gtk.Label(label=group, xalign=0)
            hdr.add_css_class('heading')
            hdr.set_margin_top(10)
            hdr.set_margin_bottom(4)
            hdr.set_margin_start(10)
            hdr.set_margin_end(10)
            row.set_header(hdr)
        else:
            row.set_header(None)

    def _populate_list(self):
        while (child := self.listbox.get_first_child()):
            self.listbox.remove(child)
        for p in self.profiles:
            self.listbox.append(self._make_row(p))

    def _update_icon_placeholder(self):
        sel = self.group_drop.get_selected()
        g_name = self.groups[sel] if 0 <= sel < len(self.groups) else ''
        g_icon = self.group_icons.get(g_name, '')
        if g_icon:
            self.icon_entry.set_placeholder_text(f"(group: {g_icon})")
        else:
            self.icon_entry.set_placeholder_text("Icon name or character (e.g. ★ 🖥)")

    def _on_group_add(self, btn):
        popover = Gtk.Popover()
        popover.set_parent(btn)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_top(12)
        box.set_margin_bottom(12)
        box.set_margin_start(12)
        box.set_margin_end(12)

        name_entry = Gtk.Entry()
        name_entry.set_placeholder_text("Group name")
        name_entry.set_width_chars(18)
        box.append(name_entry)

        icon_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        icon_entry = Gtk.Entry()
        icon_entry.set_placeholder_text("Group icon (optional)")
        icon_entry.set_hexpand(True)
        grp_browse_btn = Gtk.Button(label="Browse…")
        grp_browse_btn.add_css_class('flat')
        grp_browse_btn.set_valign(Gtk.Align.CENTER)
        icon_row.append(icon_entry)
        icon_row.append(grp_browse_btn)
        box.append(icon_row)

        confirm_btn = Gtk.Button(label="Add")
        confirm_btn.add_css_class('suggested-action')
        box.append(confirm_btn)

        def on_browse(_b):
            def on_pick(icon_name):
                icon_entry.set_text(icon_name)
            IconPickerDialog(self, on_pick).present()

        def commit(_w=None):
            name = name_entry.get_text().strip()
            icon = icon_entry.get_text().strip()
            if name and name not in self.groups:
                self.groups.append(name)
                self._group_model.append(name)
            if name in self.groups:
                if icon:
                    self.group_icons[name] = icon
                self.group_drop.set_selected(self.groups.index(name))
            popover.popdown()

        grp_browse_btn.connect('clicked', on_browse)
        name_entry.connect('activate', commit)
        confirm_btn.connect('clicked', commit)
        popover.set_child(box)
        popover.popup()
        GLib.idle_add(lambda: name_entry.grab_focus() and False)

    def _on_group_icon_edit(self, btn):
        sel = self.group_drop.get_selected()
        g_name = self.groups[sel] if 0 <= sel < len(self.groups) else None
        if g_name is None:
            return

        popover = Gtk.Popover()
        popover.set_parent(btn)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_top(12)
        box.set_margin_bottom(12)
        box.set_margin_start(12)
        box.set_margin_end(12)

        title_lbl = Gtk.Label(label=g_name, xalign=0)
        title_lbl.add_css_class('heading')
        box.append(title_lbl)

        icon_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        entry = Gtk.Entry()
        entry.set_text(self.group_icons.get(g_name, ''))
        entry.set_placeholder_text("Icon name or character")
        entry.set_hexpand(True)
        browse_btn = Gtk.Button(label="Browse…")
        browse_btn.add_css_class('flat')
        browse_btn.set_valign(Gtk.Align.CENTER)
        icon_row.append(entry)
        icon_row.append(browse_btn)
        box.append(icon_row)

        confirm_btn = Gtk.Button(label="Set")
        confirm_btn.add_css_class('suggested-action')
        box.append(confirm_btn)

        def on_browse(_b):
            def on_pick(icon_name):
                entry.set_text(icon_name)
            IconPickerDialog(self, on_pick).present()

        def commit(_w=None):
            icon = entry.get_text().strip()
            if icon:
                self.group_icons[g_name] = icon
            else:
                self.group_icons.pop(g_name, None)
            self._update_icon_placeholder()
            popover.popdown()

        browse_btn.connect('clicked', on_browse)
        entry.connect('activate', commit)
        confirm_btn.connect('clicked', commit)
        popover.set_child(box)
        popover.popup()
        GLib.idle_add(lambda: entry.grab_focus() and False)

    def _on_ssh_config_pick(self, btn):
        hosts = _read_ssh_config()
        if not hosts:
            return

        popover = Gtk.Popover()
        popover.set_parent(btn)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        outer.set_margin_top(8)
        outer.set_margin_bottom(8)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_min_content_height(min(len(hosts) * 38, 280))
        scroll.set_size_request(280, -1)

        listbox = Gtk.ListBox()
        listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        listbox.add_css_class('boxed-list')

        for alias, opts in sorted(hosts.items()):
            row = Gtk.ListBoxRow()
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
            box.set_margin_top(6)
            box.set_margin_bottom(6)
            box.set_margin_start(10)
            box.set_margin_end(10)
            box.append(Gtk.Label(label=alias, xalign=0))
            hint_parts = []
            if 'hostname' in opts:
                hint_parts.append(opts['hostname'])
            if 'user' in opts:
                hint_parts.append(opts['user'])
            if hint_parts:
                hint = Gtk.Label(label='  '.join(hint_parts), xalign=0)
                hint.add_css_class('dim-label')
                hint.add_css_class('caption')
                box.append(hint)
            row.set_child(box)
            row.alias = alias
            row.ssh_opts = opts
            listbox.append(row)

        def on_row_activated(_lb, row):
            self.host_entry.set_text(row.alias)
            user = row.ssh_opts.get('user', '')
            self.user_entry.set_text(user)
            port = row.ssh_opts.get('port', '')
            self.port_entry.set_text('' if port == '22' else port)
            if not self.name_entry.get_text().strip():
                self.name_entry.set_text(row.alias)
            self._flush_form()
            popover.popdown()

        listbox.connect('row-activated', on_row_activated)
        scroll.set_child(listbox)
        outer.append(scroll)
        popover.set_child(outer)
        popover.popup()

    def _on_icon_browse(self, _btn):
        def on_pick(icon_name):
            self.icon_entry.set_text(icon_name)
        IconPickerDialog(self, on_pick).present()

    def _make_row(self, profile):
        row = Gtk.ListBoxRow()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.set_margin_top(8)
        box.set_margin_bottom(8)
        box.set_margin_start(10)
        box.set_margin_end(10)

        name_lbl = Gtk.Label(label=profile.get('name', '(unnamed)'), xalign=0)
        box.append(name_lbl)

        row.set_child(box)
        row.profile  = profile
        row.name_lbl = name_lbl
        return row

    def _on_row_selected(self, listbox, row):
        if row is None:
            self.form_box.set_sensitive(False)
            return
        if self.current_profile is not None:
            self._flush_form()
        self.current_profile = row.profile
        self.current_row = row
        self._load_form(row)

    def _load_form(self, row):
        p = row.profile
        self._loading = True
        self.form_box.set_sensitive(True)

        self.name_entry.set_text(p.get('name', ''))
        group = p.get('group', self.groups[0] if self.groups else 'General')
        if group not in self.groups:
            self.groups.append(group)
            self._group_model.append(group)
        self.group_drop.set_selected(self.groups.index(group))
        self.icon_entry.set_text(p.get('icon', ''))

        p_type = p.get('type', 'clippet')
        self.type_drop.set_selected(0 if p_type == 'ssh' else 1)
        self.stack.set_visible_child_name(p_type)

        opts = p.get('options', {})
        self.host_entry.set_text(opts.get('host', ''))
        self.user_entry.set_text(opts.get('user', ''))
        self.port_entry.set_text(str(opts.get('port', '')))
        self.command_view.get_buffer().set_text(opts.get('command', ''))
        self.auto_exec_switch.set_active(opts.get('auto_execute', False))
        self.in_place_switch.set_active(opts.get('in_place', False))
        self.shell_split_drop.set_selected({'below': 1, 'beside': 2}.get(p.get('shell_split', ''), 0))

        classifier = p.get('classifier') or {}
        self.classifier_entry.set_text(classifier.get('title', ''))

        self.cwd_entry.set_text(p.get('cwd', ''))
        self.notify_text_entry.set_text(p.get('notify_text', ''))
        env_dict = p.get('env') or {}
        env_text = '\n'.join(f'{k}={v}' for k, v in env_dict.items())
        self.env_view.get_buffer().set_text(env_text)

        color = p.get('color', '')
        self.color_entry.set_text(color)
        rgba = Gdk.RGBA()
        if color and rgba.parse(color):
            self._color_btn.set_rgba(rgba)

        scheme = p.get('color_scheme', '')
        self.theme_drop.set_selected(
            {'light': 1, 'dark': 2, 'nord': 3}.get(scheme, 0))
        self.profile_font_entry.set_text(p.get('terminal_font', ''))

        tmux_on = bool(p.get('tmux', False))
        self.tmux_switch.set_active(tmux_on)
        self.tmux_session_entry.set_text(p.get('tmux_session', ''))
        self.tmux_session_entry.set_sensitive(tmux_on)

        self._loading = False

    def _flush_form(self):
        if self.current_profile is None or self._loading:
            return
        p = self.current_profile

        p['name']  = self.name_entry.get_text()
        sel = self.group_drop.get_selected()
        p['group'] = self.groups[sel] if 0 <= sel < len(self.groups) else (self.groups[0] if self.groups else 'General')

        p_type = 'ssh' if self.type_drop.get_selected() == 0 else 'clippet'
        p['type'] = p_type

        icon = self.icon_entry.get_text().strip()
        if icon:
            p['icon'] = icon
        else:
            p.pop('icon', None)

        if p_type == 'ssh':
            ssh_opts = {
                'host': self.host_entry.get_text().strip(),
                'user': self.user_entry.get_text().strip(),
            }
            port = self.port_entry.get_text().strip()
            if port:
                ssh_opts['port'] = port
            p['options'] = ssh_opts
        else:
            buf = self.command_view.get_buffer()
            cmd_text = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)
            p['options'] = {
                'command':      cmd_text,
                'auto_execute': self.auto_exec_switch.get_active(),
                'in_place':     self.in_place_switch.get_active(),
            }
            split_val = {1: 'below', 2: 'beside'}.get(self.shell_split_drop.get_selected())
            if split_val:
                p['shell_split'] = split_val
            else:
                p.pop('shell_split', None)

        title = self.classifier_entry.get_text().strip()
        if title:
            p['classifier'] = {'title': title}
        else:
            p.pop('classifier', None)

        cwd = self.cwd_entry.get_text().strip()
        if cwd:
            p['cwd'] = cwd
        else:
            p.pop('cwd', None)

        notify_text = self.notify_text_entry.get_text().strip()
        if notify_text:
            p['notify_text'] = notify_text
        else:
            p.pop('notify_text', None)

        buf = self.env_view.get_buffer()
        env_text = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False).strip()
        env_dict = {}
        for line in env_text.splitlines():
            line = line.strip()
            if '=' in line and not line.startswith('#'):
                k, _, v = line.partition('=')
                if k.strip():
                    env_dict[k.strip()] = v
        if env_dict:
            p['env'] = env_dict
        else:
            p.pop('env', None)

        color = self.color_entry.get_text().strip()
        if color:
            test = Gdk.RGBA()
            if test.parse(color):
                p['color'] = color
            # silently ignore unparseable values
        else:
            p.pop('color', None)

        scheme_idx = self.theme_drop.get_selected()
        scheme = {1: 'light', 2: 'dark', 3: 'nord'}.get(scheme_idx)
        if scheme:
            p['color_scheme'] = scheme
        else:
            p.pop('color_scheme', None)

        font_override = self.profile_font_entry.get_text().strip()
        if font_override:
            p['terminal_font'] = font_override
        else:
            p.pop('terminal_font', None)

        if self.tmux_switch.get_active():
            p['tmux'] = True
            session = self.tmux_session_entry.get_text().strip()
            if session:
                p['tmux_session'] = session
            else:
                p.pop('tmux_session', None)
        else:
            p.pop('tmux', None)
            p.pop('tmux_session', None)

        row = self.current_row
        if row:
            row.name_lbl.set_label(p.get('name', '(unnamed)'))
            self.listbox.invalidate_sort()
            self.listbox.invalidate_headers()

    def _on_tmux_switch_changed(self, switch, _param):
        self.tmux_session_entry.set_sensitive(switch.get_active())

    def _on_color_entry_changed(self, entry):
        if self._color_syncing:
            return
        color = entry.get_text().strip()
        rgba = Gdk.RGBA()
        if color and rgba.parse(color):
            self._color_syncing = True
            self._color_btn.set_rgba(rgba)
            self._color_syncing = False

    def _on_color_btn_changed(self, btn, _param):
        if self._color_syncing:
            return
        rgba = btn.get_rgba()
        hex_color = '#{:02x}{:02x}{:02x}'.format(
            int(rgba.red * 255), int(rgba.green * 255), int(rgba.blue * 255))
        self._color_syncing = True
        self.color_entry.set_text(hex_color)
        self._color_syncing = False

    def _on_profile_font_browse(self, btn):
        dialog = Gtk.FontDialog()
        dialog.set_title("Profile terminal font")
        current = self.profile_font_entry.get_text().strip()
        fallback = self.app.settings.get('terminal_font', DEFAULT_SETTINGS['terminal_font'])
        try:
            desc = Pango.FontDescription.from_string(current or fallback)
        except Exception:
            desc = Pango.FontDescription.from_string(fallback)
        dialog.choose_font(self, desc, None, self._on_profile_font_chosen)

    def _on_profile_font_chosen(self, dialog, result):
        try:
            desc = dialog.choose_font_finish(result)
            if desc:
                self.profile_font_entry.set_text(desc.to_string())
        except Exception:
            pass

    def _on_type_changed(self, drop, param):
        if self._loading:
            return
        self.stack.set_visible_child_name('ssh' if drop.get_selected() == 0 else 'clippet')

    def _on_add(self, btn):
        new_p = {
            'name':    'New Profile',
            'group':   'General',
            'type':    'clippet',
            'options': {'command': '', 'auto_execute': False},
        }
        self.profiles.append(new_p)
        row = self._make_row(new_p)
        self.listbox.append(row)
        self.listbox.select_row(row)
        GLib.idle_add(lambda: self.name_entry.grab_focus() and False)

    def _on_delete(self, btn):
        row = self.listbox.get_selected_row()
        if row is None:
            return
        idx = self.profiles.index(row.profile)
        self.profiles.remove(row.profile)
        self.current_profile = None
        self.current_row = None
        self.listbox.remove(row)
        n = len(self.profiles)
        if n > 0:
            next_row = self.listbox.get_row_at_index(min(idx, n - 1))
            if next_row:
                self.listbox.select_row(next_row)
        else:
            self.form_box.set_sensitive(False)

    def _on_save(self, btn):
        self._flush_form()
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            groups_out = []
            for g in self.groups:
                icon = self.group_icons.get(g, '')
                groups_out.append({'name': g, 'icon': icon} if icon else g)
            with open(CONFIG_FILE, 'w') as f:
                yaml.dump({'groups': groups_out, 'profiles': self.profiles}, f,
                          allow_unicode=True, sort_keys=False)
            self.app.reload_profiles()
            self.close()
        except Exception as e:
            logger.warning("Error saving profiles: %s", e)


# ---------------------------------------------------------------------------
# Preferences — Adw.PreferencesWindow with three groups.
# ---------------------------------------------------------------------------

class PreferencesWindow(Adw.PreferencesWindow):
    """Settings UI. Persists straight to settings.yaml via the app."""

    def __init__(self, app):
        super().__init__(transient_for=app.window, modal=True)
        self.app = app
        self.set_title("Preferences")
        self.set_default_size(PREFS_W, PREFS_H)
        self.set_search_enabled(False)

        page = Adw.PreferencesPage()
        self.add(page)

        # --- Appearance ------------------------------------------------------
        appearance = Adw.PreferencesGroup(title="Appearance")
        page.add(appearance)

        # Colour scheme — segmented control (linked toggle-button group).
        scheme_row = Adw.ActionRow(
            title="Colour scheme",
            subtitle="Light, dark, or the custom Nord-ish palette",
        )
        scheme_row.add_prefix(Gtk.Image.new_from_icon_name('preferences-system-symbolic'))
        scheme_seg = self._segmented(
            [('Light', 'light'), ('Dark', 'dark'), ('Nord', 'nord')],
            app.settings.get('color_scheme', 'dark'),
            lambda value: app.set_setting('color_scheme', value),
        )
        scheme_row.add_suffix(scheme_seg)
        appearance.add(scheme_row)

        # Terminal font — FontDialogButton
        font_row = Adw.ActionRow(
            title="Terminal font",
            subtitle=app.settings.get('terminal_font', DEFAULT_SETTINGS['terminal_font']),
        )
        font_row.add_prefix(Gtk.Image.new_from_icon_name('utilities-terminal-symbolic'))
        font_dialog = Gtk.FontDialog()
        font_dialog.set_title("Terminal font")
        font_btn = Gtk.FontDialogButton.new(font_dialog)
        font_btn.set_level(Gtk.FontLevel.FONT)
        try:
            font_btn.set_font_desc(
                Pango.FontDescription.from_string(
                    app.settings.get('terminal_font', DEFAULT_SETTINGS['terminal_font'])))
        except Exception:
            pass
        font_btn.set_valign(Gtk.Align.CENTER)
        font_btn.connect('notify::font-desc', self._on_font_changed, font_row)
        font_row.add_suffix(font_btn)
        appearance.add(font_row)

        # Sidebar layout — segmented
        layout_row = Adw.ActionRow(
            title="Sidebar layout",
            subtitle="Expandable groups, or flat list with headers",
        )
        layout_row.add_prefix(Gtk.Image.new_from_icon_name('view-list-symbolic'))
        layout_seg = self._segmented(
            [('Expanders', 'expanders'), ('Flat', 'flat')],
            app.settings.get('sidebar_layout', 'expanders'),
            lambda value: app.set_setting('sidebar_layout', value),
        )
        layout_row.add_suffix(layout_seg)
        appearance.add(layout_row)

        # --- Terminal --------------------------------------------------------
        terminal_grp = Adw.PreferencesGroup(title="Terminal")
        page.add(terminal_grp)

        scrollback_row = Adw.SpinRow.new_with_range(SCROLLBACK_MIN, SCROLLBACK_MAX, SCROLLBACK_STEP)
        scrollback_row.set_title("Scrollback")
        scrollback_row.set_subtitle("Lines kept per tab")
        scrollback_row.set_value(app.settings.get('scrollback', 10000))
        scrollback_row.connect(
            'notify::value',
            lambda r, _p: app.set_setting('scrollback', int(r.get_value())))
        terminal_grp.add(scrollback_row)

        speed_row = Adw.SpinRow.new_with_range(1, 10, 1)
        speed_row.set_title("Scroll speed")
        speed_row.set_subtitle("Lines scrolled per wheel tick")
        speed_row.set_value(app.settings.get('scroll_speed', DEFAULT_SETTINGS['scroll_speed']))
        speed_row.connect(
            'notify::value',
            lambda r, _p: app.set_setting('scroll_speed', int(r.get_value())))
        terminal_grp.add(speed_row)

        copy_row = Adw.SwitchRow(
            title="Copy on selection",
            subtitle="Selecting text copies it to the clipboard",
            active=app.settings.get('copy_on_selection', True),
        )
        copy_row.connect('notify::active',
                         lambda r, _p: app.set_setting('copy_on_selection', r.get_active()))
        terminal_grp.add(copy_row)

        # --- Profiles & state -----------------------------------------------
        prof_grp = Adw.PreferencesGroup(title="Profiles &amp; state")
        page.add(prof_grp)

        restore_row = Adw.SwitchRow(
            title="Restore tabs on launch",
            subtitle="Reopen the tabs from your last session",
            active=app.settings.get('restore_tabs', True),
        )
        restore_row.connect('notify::active',
                            lambda r, _p: app.set_setting('restore_tabs', r.get_active()))
        prof_grp.add(restore_row)

        config_row = Adw.ActionRow(
            title="Profile config",
            subtitle=str(CONFIG_FILE),
        )
        config_row.add_prefix(Gtk.Image.new_from_icon_name('network-server-symbolic'))
        reveal_btn = Gtk.Button(label="Reveal\u2026")
        reveal_btn.add_css_class('flat')
        reveal_btn.set_valign(Gtk.Align.CENTER)
        reveal_btn.connect('clicked', self._on_reveal_config)
        config_row.add_suffix(reveal_btn)
        prof_grp.add(config_row)

    # ----- helpers ---------------------------------------------------------

    def _segmented(self, options, current, on_change):
        """Linked toggle-button group used as a segmented control. libadwaita
        does not ship one natively; the .linked + ToggleButton pattern is the
        idiomatic substitute."""
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        box.add_css_class('linked')
        box.set_valign(Gtk.Align.CENTER)

        buttons = []
        suppressing = {'flag': False}

        def on_toggled(btn):
            if suppressing['flag']:
                return
            if not btn.get_active():
                # Don't allow zero-selection — flip back on.
                if not any(b.get_active() for b in buttons):
                    suppressing['flag'] = True
                    btn.set_active(True)
                    suppressing['flag'] = False
                return
            suppressing['flag'] = True
            for b in buttons:
                if b is not btn:
                    b.set_active(False)
            suppressing['flag'] = False
            on_change(btn.opt_value)

        for label, value in options:
            tb = Gtk.ToggleButton(label=label)
            tb.opt_value = value
            tb.set_active(value == current)
            tb.connect('toggled', on_toggled)
            buttons.append(tb)
            box.append(tb)
        return box

    def _on_font_changed(self, btn, _pspec, row):
        desc = btn.get_font_desc()
        if desc is None:
            return
        s = desc.to_string()
        row.set_subtitle(s)
        self.app.set_setting('terminal_font', s)

    def _on_reveal_config(self, _btn):
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        uri = GLib.filename_to_uri(str(CONFIG_DIR), None)
        try:
            Gio.AppInfo.launch_default_for_uri(uri, None)
        except GLib.Error as e:
            logger.warning("Could not open file manager: %s", e)


# ---------------------------------------------------------------------------
# Help window — renders TTYGA.md with basic formatting
# ---------------------------------------------------------------------------

class HelpWindow(Adw.Window):

    _HELP_LOCATIONS = [
        Path(__file__).parent / 'TTYGA.md',
        Path(GLib.get_user_data_dir()) / 'ttyga' / 'TTYGA.md',
    ]

    def __init__(self, parent):
        super().__init__(title="ttyga Help", transient_for=parent, modal=False)
        self.set_default_size(900, 640)
        self._search_iters = []
        self._search_pos = 0

        toolbar = Adw.ToolbarView()
        self.set_content(toolbar)

        header = Adw.HeaderBar()
        search_btn = Gtk.ToggleButton(icon_name='system-search-symbolic')
        search_btn.set_tooltip_text("Search (Ctrl+F)")
        header.pack_end(search_btn)
        toolbar.add_top_bar(header)

        self._search_bar = Gtk.SearchBar()
        self._search_bar.set_show_close_button(False)
        self._search_bar.set_key_capture_widget(self)
        self._search_entry = Gtk.SearchEntry()
        self._search_entry.set_size_request(300, -1)
        self._search_entry.connect('search-changed', self._on_search_changed)
        self._search_entry.connect('next-match', self._go_next)
        self._search_entry.connect('previous-match', self._go_prev)
        self._search_entry.connect('stop-search', self._on_search_stop)
        self._search_bar.set_child(self._search_entry)
        self._search_bar.connect_entry(self._search_entry)
        self._search_bar.bind_property('search-mode-enabled', search_btn, 'active',
                                       GObject.BindingFlags.BIDIRECTIONAL)
        search_btn.connect('toggled', lambda btn: self._search_entry.grab_focus() if btn.get_active() else None)
        toolbar.add_top_bar(self._search_bar)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        self._scroll = scroll

        self.tv = Gtk.TextView()
        self.tv.set_editable(False)
        self.tv.set_cursor_visible(False)
        self.tv.set_wrap_mode(Gtk.WrapMode.WORD)
        self.tv.set_left_margin(36)
        self.tv.set_right_margin(36)
        self.tv.set_top_margin(24)
        self.tv.set_bottom_margin(24)

        self.buf = self.tv.get_buffer()
        self._define_tags(self.buf)
        self._populate(self.buf)

        scroll.set_child(self.tv)
        toolbar.set_content(scroll)

    def _define_tags(self, buf):
        buf.create_tag('h1', weight=Pango.Weight.BOLD, scale=1.6,
                       pixels_above_lines=20, pixels_below_lines=6)
        buf.create_tag('h2', weight=Pango.Weight.BOLD, scale=1.25,
                       pixels_above_lines=16, pixels_below_lines=4)
        buf.create_tag('h3', weight=Pango.Weight.BOLD, scale=1.05,
                       pixels_above_lines=12, pixels_below_lines=2)
        buf.create_tag('code', family='Monospace', scale=0.9)
        buf.create_tag('code_block', family='Monospace', scale=0.88,
                       left_margin=60, pixels_above_lines=2, pixels_below_lines=2)
        buf.create_tag('bold', weight=Pango.Weight.BOLD)
        buf.create_tag('dim', foreground='gray', scale=0.85)
        buf.create_tag('bullet', left_margin=48, indent=-12)
        buf.create_tag('search_hit', background='#f9f06b', foreground='#1a1a2e')

    def _apply(self, buf, text, *tag_names):
        start = buf.get_end_iter().get_offset()
        buf.insert(buf.get_end_iter(), text)
        si = buf.get_iter_at_offset(start)
        for tag in tag_names:
            buf.apply_tag_by_name(tag, si, buf.get_end_iter())

    def _inline(self, buf, text, extra=()):
        for part in re.split(r'(\*\*[^*]+\*\*|`[^`]+`)', text):
            if part.startswith('**') and part.endswith('**') and len(part) > 4:
                self._apply(buf, part[2:-2], 'bold', *extra)
            elif part.startswith('`') and part.endswith('`') and len(part) > 2:
                self._apply(buf, part[1:-1], 'code', *extra)
            elif extra:
                self._apply(buf, part, *extra)
            else:
                buf.insert(buf.get_end_iter(), part)

    def _populate(self, buf):
        src = next((p for p in self._HELP_LOCATIONS if p.exists()), None)
        if src is None:
            buf.insert(buf.get_end_iter(),
                       "Help file (TTYGA.md) not found.\n\n"
                       "Expected locations:\n" +
                       '\n'.join(f'  {p}' for p in self._HELP_LOCATIONS))
            return

        in_code = False
        table_buf = []   # accumulated rows: list[list[str]]

        def strip_md(text):
            text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)
            text = re.sub(r'`([^`]+)`', r'\1', text)
            return text.strip()

        def flush_table():
            if not table_buf:
                return
            widths = []
            for row in table_buf:
                for i, cell in enumerate(row):
                    if i >= len(widths):
                        widths.append(0)
                    widths[i] = max(widths[i], len(cell))
            for r_idx, row in enumerate(table_buf):
                padded = [cell.ljust(widths[i] if i < len(widths) else 0)
                          for i, cell in enumerate(row)]
                line = '  ' + '  '.join(padded) + '\n'
                tags = ('code', 'bold') if r_idx == 0 else ('code',)
                self._apply(buf, line, *tags)
            table_buf.clear()

        for raw in src.read_text().splitlines():
            # Code fence
            if raw.startswith('```'):
                flush_table()
                in_code = not in_code
                buf.insert(buf.get_end_iter(), '\n')
                continue

            if in_code:
                self._apply(buf, raw + '\n', 'code_block')
                continue

            # Table separator rows — skip
            if '|' in raw and '-' in raw and re.fullmatch(r'[\|\-\: ]+', raw):
                continue

            # Table data rows — buffer for aligned rendering
            if raw.startswith('|') and raw.endswith('|'):
                table_buf.append([strip_md(c) for c in raw[1:-1].split('|')])
                continue

            # Non-table line: flush any buffered table first
            flush_table()

            if raw.startswith('### '):
                self._apply(buf, raw[4:] + '\n', 'h3')
                continue
            if raw.startswith('## '):
                self._apply(buf, raw[3:] + '\n', 'h2')
                continue
            if raw.startswith('# '):
                self._apply(buf, raw[2:] + '\n', 'h1')
                continue
            if raw.strip() == '---':
                self._apply(buf, '─' * 56 + '\n', 'dim')
                continue
            if raw.startswith('- '):
                start = buf.get_end_iter().get_offset()
                buf.insert(buf.get_end_iter(), '• ')
                self._inline(buf, raw[2:] + '\n')
                si = buf.get_iter_at_offset(start)
                buf.apply_tag_by_name('bullet', si, buf.get_end_iter())
                continue
            if not raw.strip():
                buf.insert(buf.get_end_iter(), '\n')
                continue
            self._inline(buf, raw + '\n')

        flush_table()

    # ----- search ------------------------------------------------------------

    def _on_search_changed(self, entry):
        query = entry.get_text().strip()
        self._clear_highlights()
        self._search_iters = []
        self._search_pos = 0
        if not query:
            return
        start = self.buf.get_start_iter()
        while True:
            result = start.forward_search(
                query, Gtk.TextSearchFlags.CASE_INSENSITIVE, None)
            if not result:
                break
            match_start, match_end = result
            self.buf.apply_tag_by_name('search_hit', match_start, match_end)
            self._search_iters.append(
                (match_start.get_offset(), match_end.get_offset()))
            start = match_end
        if self._search_iters:
            self._scroll_to_match(0)

    def _clear_highlights(self):
        self.buf.remove_tag_by_name(
            'search_hit', self.buf.get_start_iter(), self.buf.get_end_iter())

    def _go_next(self, *args):
        if not self._search_iters:
            return
        self._search_pos = (self._search_pos + 1) % len(self._search_iters)
        self._scroll_to_match(self._search_pos)

    def _go_prev(self, *args):
        if not self._search_iters:
            return
        self._search_pos = (self._search_pos - 1) % len(self._search_iters)
        self._scroll_to_match(self._search_pos)

    def _scroll_to_match(self, idx):
        off_start, _ = self._search_iters[idx]
        it = self.buf.get_iter_at_offset(off_start)
        self.tv.scroll_to_iter(it, 0.1, True, 0.0, 0.3)

    def _on_search_stop(self, *args):
        self._search_bar.set_search_mode(False)
        self._clear_highlights()
        self.tv.grab_focus()


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------

class DevFrame(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID,
                         flags=Gio.ApplicationFlags(0))
        self.expanders   = {}      # group name -> Gtk.Expander (expanders layout)
        self.window      = None
        self.active_btns = set()   # currently highlighted profile rows (one tab can host several, once merged)
        self.tab_count   = 0
        # Per-terminal metadata: {terminal: {'label': Gtk.Label, 'dot': Gtk.Image,
        #                                    'user': str, 'host': str, 'avatar': str|None,
        #                                    'kind': 'ssh'|'local'}}
        self.tabs        = {}
        self.css_provider = None
        self.settings    = self._load_settings()
        self._sidebar_toggle_btn = None
        self.active_profile_keys = set()   # {(name, group)} of highlighted profiles
        self._profile_buttons   = []     # [(btn, profile)] for search filtering
        self._flat_groups       = []     # [(header, vbox, g_name)] for flat layout
        self._font_zoom_delta   = 0      # ephemeral pt offset from saved font size
        self._var_history       = {}     # {history_key: {var_name: last_value}}
        self._active_terminal   = None   # Vte.Terminal with keyboard focus
        self._window_active     = True   # tracks whether the ttyga window has OS focus
        self._notified_roots    = set()  # tab_roots already notified this episode

    # ----- config / settings / state I/O -----------------------------------

    def load_config(self):
        for path in (CONFIG_FILE, LEGACY_CONFIG):
            try:
                with open(path, 'r') as f:
                    config = yaml.safe_load(f) or {}
            except FileNotFoundError:
                continue
            for p in config.get('profiles', []):
                if p.pop('shell_below', False) and 'shell_split' not in p:
                    p['shell_split'] = 'below'
            return config
        return {"profiles": []}

    def load_resolved_config(self):
        config = self.load_config()
        return {**config, 'profiles': _resolve_inheritance(config.get('profiles', []))}

    def _load_settings(self):
        try:
            with open(SETTINGS_FILE, 'r') as f:
                data = yaml.safe_load(f) or {}
        except (FileNotFoundError, yaml.YAMLError):
            data = {}
        out = dict(DEFAULT_SETTINGS)
        out.update({k: v for k, v in data.items() if k in DEFAULT_SETTINGS})
        return out

    def save_settings(self):
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            with open(SETTINGS_FILE, 'w') as f:
                yaml.dump(self.settings, f, sort_keys=False, allow_unicode=True)
        except Exception as e:
            logger.warning("Error saving settings: %s", e)

    def set_setting(self, key, value):
        """Update one setting, apply its side-effect, persist."""
        if self.settings.get(key) == value:
            return
        self.settings[key] = value
        self.save_settings()

        if key == 'color_scheme':
            self._apply_theme()
        elif key == 'sidebar_layout':
            self.reload_profiles()
        elif key == 'terminal_font':
            desc = Pango.FontDescription.from_string(value)
            for term, meta in self.tabs.items():
                p = meta.get('profile')
                if not (p and p.get('terminal_font')):
                    term.set_font(desc)
                    meta['base_font'] = value
        elif key == 'scrollback':
            for term in self.tabs:
                term.set_scrollback_lines(int(value))
        elif key == 'copy_on_selection':
            # Wired in on_terminal_selection; nothing to flip eagerly.
            pass

    def load_state(self):
        try:
            with open(STATE_FILE, 'r') as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {"expanded_groups": {}, "sidebar_width_fraction": SIDEBAR_FRACTION,
                    "sidebar_visible": False,
                    "window_width": WIN_DEFAULT_W, "window_height": WIN_DEFAULT_H}

    def save_state(self, *args):
        maximized = self.window.is_maximized()

        open_tabs = []
        for i in range(self.notebook.get_n_pages()):
            tab_root = self.notebook.get_nth_page(i)
            open_tabs.append({'layout': self._serialise_tab(tab_root)})

        state = {
            "expanded_groups": {
                name: exp.get_expanded() for name, exp in self.expanders.items()
            },
            "sidebar_width_fraction": self.split_view.get_sidebar_width_fraction(),
            "sidebar_visible": self.split_view.get_show_sidebar(),
            "window_maximized": maximized,
            "open_tabs": open_tabs,
            "active_tab_index": self.notebook.get_current_page(),
            "variable_history": self._var_history,
        }
        if not maximized:
            state["window_width"]  = self.window.get_width()
            state["window_height"] = self.window.get_height()
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            with open(STATE_FILE, 'w') as f:
                json.dump(state, f)
        except Exception as e:
            logger.warning("Error saving state: %s", e)
        return False

    def _serialise_pane(self, widget):
        if isinstance(widget, Gtk.Box) and 'pane-box' in widget.get_css_classes():
            widget = self._first_terminal_in(widget)
        if isinstance(widget, Vte.Terminal):
            meta = self.tabs.get(widget, {})
            p = meta.get('profile')
            cwd_uri = widget.get_current_directory_uri()
            cwd = None
            if cwd_uri:
                try:
                    cwd = GLib.filename_from_uri(cwd_uri)[0] or None
                except Exception:
                    pass
            if cwd is None:
                cwd = meta.get('spawn_dir')
            return {
                'type': 'terminal',
                'profile': {'name': p.get('name'), 'group': p.get('group', 'General')} if p else None,
                'cwd': cwd,
            }
        if isinstance(widget, Gtk.Paned):
            orientation = ('horizontal' if widget.get_orientation() == Gtk.Orientation.HORIZONTAL
                           else 'vertical')
            return {
                'type': 'paned',
                'orientation': orientation,
                'position': widget.get_position(),
                'start': self._serialise_pane(widget.get_start_child()),
                'end': self._serialise_pane(widget.get_end_child()),
            }
        return None

    def _serialise_tab(self, tab_root):
        child = tab_root.get_first_child()
        return self._serialise_pane(child) if child else None

    # ----- theme -----------------------------------------------------------

    def _apply_theme(self):
        theme = self.settings.get('color_scheme', 'dark')

        style_mgr = Adw.StyleManager.get_default()
        if theme == 'light':
            style_mgr.set_color_scheme(Adw.ColorScheme.FORCE_LIGHT)
        else:
            # Both dark and nord ride on libadwaita's dark base; our CSS
            # overrides the headerbar/sidebar/etc on top.
            style_mgr.set_color_scheme(Adw.ColorScheme.FORCE_DARK)

        if self.css_provider is None:
            self.css_provider = Gtk.CssProvider()
            display = Gdk.Display.get_default()
            if display is not None:
                Gtk.StyleContext.add_provider_for_display(
                    display, self.css_provider,
                    Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        self.css_provider.load_from_data(
            build_css(theme, self.settings.get('ssh_color', '')).encode('utf-8'))

        # Theme class on the window mirrors what the HTML mock did with
        # .theme-light/.theme-dark/.theme-nord on its root. Useful as a hook
        # for any future selector-scoped tweaks.
        if self.window is not None:
            for cls in ('theme-light', 'theme-dark', 'theme-nord'):
                self.window.remove_css_class(cls)
            self.window.add_css_class(f'theme-{theme}')

        for term, meta in self.tabs.items():
            p = meta.get('profile')
            self._apply_terminal_colors(term,
                theme=p.get('color_scheme') if p else None)

    def _apply_terminal_colors(self, terminal, theme=None):
        if theme not in THEMES:
            theme = self.settings.get('color_scheme', 'dark')
        t = THEMES[theme]
        terminal.set_colors(_rgba(t['term_fg']), _rgba(t['term_bg']),
                            _vte_palette(theme))

    # ----- lifecycle -------------------------------------------------------

    def _register_app_icon(self):
        """Stage all bundled SVGs from the icon theme into ~/.cache so custom
        icon names (app icon + action icons) resolve when running from source."""
        display = Gdk.Display.get_default()
        if display is None:
            return
        cache_root = Path(GLib.get_user_cache_dir()) / 'ttyga'
        src_root   = Path(__file__).parent / 'ttyga-icon-theme'
        # Every icon is stored under its published name (the app icon as the
        # reverse-DNS APP_ID), so the whole tree copies verbatim.
        for src in src_root.rglob('*.svg'):
            dst = cache_root / src.relative_to(src_root)
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(src.read_bytes())
        Gtk.IconTheme.get_for_display(display).add_search_path(str(cache_root))

    # ----- session restore helpers -----------------------------------------

    def _find_primary_profile(self, node, by_key):
        """Depth-first search for the first terminal's profile in a layout node."""
        if node is None:
            return None
        if node.get('type') == 'terminal':
            p_ref = node.get('profile')
            if p_ref:
                return by_key.get((p_ref.get('name'), p_ref.get('group', 'General')))
            return None
        if node.get('type') == 'paned':
            p = self._find_primary_profile(node.get('start'), by_key)
            return p or self._find_primary_profile(node.get('end'), by_key)
        return None

    def _build_pane_tree(self, node, tab_root, tab_label, dot, by_key):
        """Recursively recreate pane widgets from a serialised layout node."""
        if node is None:
            terminal = self._new_terminal()
            self.tabs[terminal] = {
                'label': tab_label, 'dot': dot, 'kind': 'local',
                'profile': None, 'tab_root': tab_root,
                'base_font': self.settings.get('terminal_font', DEFAULT_SETTINGS['terminal_font']),
                'spawn_dir': os.environ.get('HOME', '/'),
            }
            return self._make_pane_box(terminal, self.tabs[terminal])

        if node.get('type') == 'terminal':
            p_ref = node.get('profile')
            profile = by_key.get((p_ref['name'], p_ref.get('group', 'General'))) if p_ref else None
            cwd = node.get('cwd')
            p_env = profile.get('env', {}) if profile else {}

            if profile:
                p_type = profile.get('type', 'clippet')
                opts = profile.get('options', {})
                tmux_on = bool(profile.get('tmux', False))
                tmux_session = (profile.get('tmux_session') or 'main').strip()
                tmux_cmd = f"tmux attach -t {tmux_session} || tmux new -s {tmux_session}"

                if p_type == 'ssh':
                    user = opts.get('user', '').strip()
                    host = opts.get('host', 'localhost').strip()
                    port = str(opts.get('port', '')).strip()
                    parts = ['ssh']
                    if port:
                        parts += ['-p', port]
                    parts.append(f'{user}@{host}' if user else host)
                    if tmux_on:
                        parts += ['-t', f"'{tmux_cmd}'"]
                    cmd = ' '.join(parts) + '\n'
                    kind = 'ssh'
                else:
                    cmd = (tmux_cmd + '\n') if tmux_on else None
                    kind = 'local'
            else:
                cmd = None
                kind = 'local'

            if cmd:
                tmp = tempfile.NamedTemporaryFile(
                    mode='w', suffix='.sh', prefix='ttyga_', delete=False)
                tmp.write('[ -f ~/.bashrc ] && . ~/.bashrc\n')
                tmp.write(cmd.rstrip('\n') + '\n')
                tmp.close()
                def on_spawn(term, _tmp=tmp.name):
                    try:
                        os.unlink(_tmp)
                    except OSError:
                        pass
                terminal = self._new_terminal(
                    on_spawn=on_spawn,
                    shell_args=['--init-file', tmp.name],
                    cwd=cwd, env=p_env or None, profile=profile)
            else:
                terminal = self._new_terminal(cwd=cwd, env=p_env or None, profile=profile)

            base_font = ((profile.get('terminal_font') if profile else None) or
                         self.settings.get('terminal_font', DEFAULT_SETTINGS['terminal_font']))
            self.tabs[terminal] = {
                'label':     tab_label,
                'dot':       dot,
                'kind':      kind,
                'profile':   profile,
                'base_font': base_font,
                'tab_root':  tab_root,
                'spawn_dir': cwd or os.environ.get('HOME', '/'),
            }
            return self._make_pane_box(terminal, self.tabs[terminal])

        if node.get('type') == 'paned':
            orientation = (Gtk.Orientation.HORIZONTAL if node.get('orientation') == 'horizontal'
                           else Gtk.Orientation.VERTICAL)
            paned = Gtk.Paned(orientation=orientation)
            paned.set_hexpand(True)
            paned.set_vexpand(True)
            start = self._build_pane_tree(node.get('start'), tab_root, tab_label, dot, by_key)
            end   = self._build_pane_tree(node.get('end'),   tab_root, tab_label, dot, by_key)
            paned.set_start_child(start)
            paned.set_end_child(end)
            pos = node.get('position', 0)
            if pos:
                GLib.idle_add(lambda p=paned, v=pos: p.set_position(v) or False)
            return paned

        # Unknown node type — fall back to plain shell
        terminal = self._new_terminal()
        self.tabs[terminal] = {
            'label': tab_label, 'dot': dot, 'kind': 'local',
            'profile': None, 'tab_root': tab_root,
            'base_font': self.settings.get('terminal_font', DEFAULT_SETTINGS['terminal_font']),
            'spawn_dir': os.environ.get('HOME', '/'),
        }
        return self._make_pane_box(terminal, self.tabs[terminal])

    def _build_profile_layout(self, node, tab_root, tab_label, dot,
                               base_profile, base_cwd, base_env):
        """Recursively build a pane tree from a profile layout: node.

        Split node:  {'split': 'horizontal'|'vertical', 'start': <node>, 'end': <node>}
        Leaf node:   {'command': '...', 'cwd': '...', 'auto_execute': true}
        Plain shell: {} or omitted
        """
        if node and 'split' in node:
            orientation = (Gtk.Orientation.HORIZONTAL
                           if str(node['split']).lower() == 'horizontal'
                           else Gtk.Orientation.VERTICAL)
            paned = Gtk.Paned(orientation=orientation)
            paned.set_hexpand(True)
            paned.set_vexpand(True)
            start = self._build_profile_layout(
                node.get('start') or {}, tab_root, tab_label, dot,
                base_profile, base_cwd, base_env)
            end = self._build_profile_layout(
                node.get('end') or {}, tab_root, tab_label, dot,
                base_profile, base_cwd, base_env)
            paned.set_start_child(start)
            paned.set_end_child(end)
            return paned

        # Leaf node — spawn a terminal with an optional command.
        node     = node or {}
        cmd      = node.get('command', '').strip()
        cwd      = node.get('cwd', base_cwd)
        auto_exc = node.get('auto_execute', True)   # default: run immediately
        font_str = ((base_profile.get('terminal_font') if base_profile else None) or
                    self.settings.get('terminal_font', DEFAULT_SETTINGS['terminal_font']))

        if cmd and auto_exc:
            tmp = tempfile.NamedTemporaryFile(
                mode='w', suffix='.sh', prefix='ttyga_', delete=False)
            tmp.write('[ -f ~/.bashrc ] && . ~/.bashrc\n')
            tmp.write(cmd + '\n')
            tmp.close()
            def on_spawn(term, _f=tmp.name):
                try:
                    os.unlink(_f)
                except OSError:
                    pass
            terminal = self._new_terminal(
                on_spawn=on_spawn, shell_args=['--init-file', tmp.name],
                cwd=cwd, env=base_env, profile=base_profile)
        elif cmd:
            def on_spawn(term):
                GLib.timeout_add(300, lambda: term.feed_child(cmd.encode('utf-8')) or False)
            terminal = self._new_terminal(
                on_spawn=on_spawn, cwd=cwd, env=base_env, profile=base_profile)
        else:
            terminal = self._new_terminal(cwd=cwd, env=base_env, profile=base_profile)

        spawn_dir = (str(Path(cwd).expanduser()) if cwd
                     else _implied_cwd(cmd) or os.environ.get('HOME', '/'))
        self.tabs[terminal] = {
            'label':      tab_label,
            'dot':        dot,
            'kind':       'local',
            'profile':    base_profile,
            'base_font':  font_str,
            'tab_root':   tab_root,
            'spawn_dir':  spawn_dir,
            'pane_label': cmd,               # show command in pane-bar; empty = plain shell
        }
        return self._make_pane_box(terminal, self.tabs[terminal])

    def _restore_tab(self, layout, by_key, focus=False):
        """Reconstruct a tab from a serialised layout dict."""
        profile = self._find_primary_profile(layout, by_key)
        kind = 'ssh' if (profile and profile.get('type') == 'ssh') else 'local'
        title = (self._build_classifier_title(profile)
                 if profile else f"{_LOCAL_USER}@{_LOCAL_HOST}")
        tab_icon = self._profile_icon(profile)

        tab_box, tab_label, close_btn, dot = self._make_tab_label(title, kind=kind, icon=tab_icon)

        if profile:
            color = profile.get('color', '')
            if color:
                dot_prov = Gtk.CssProvider()
                dot_prov.load_from_string(f"image {{ color: {color}; }}")
                dot.get_style_context().add_provider(
                    dot_prov, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)

        tab_root = Gtk.Box()
        tab_root.set_hexpand(True)
        tab_root.set_vexpand(True)

        content = self._build_pane_tree(layout, tab_root, tab_label, dot, by_key)
        tab_root.append(content)

        primary_term = self._first_terminal_in(tab_root)
        if primary_term:
            close_btn.connect("clicked", self._on_close_tab, primary_term)
            self._attach_tab_context_menu(tab_box, primary_term)

        self._update_pane_bars(tab_root)
        self._show_notebook()
        idx = self.notebook.append_page(tab_root, tab_box)
        self.notebook.set_tab_reorderable(tab_root, True)
        if focus:
            self.notebook.set_current_page(idx)
            if primary_term:
                GLib.idle_add(lambda: GLib.idle_add(
                    lambda: primary_term.grab_focus() and False,
                    priority=GLib.PRIORITY_LOW) and False)

    def do_activate(self):
        if self.window:
            self.window.present()
            return

        self._register_app_icon()
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        state = self.load_state()
        self._var_history = state.get('variable_history', {})
        self._build_window(state)
        self._apply_theme()

        open_tabs = state.get('open_tabs', []) if self.settings.get('restore_tabs', True) else []
        active_tab_index = state.get('active_tab_index', -1)
        if open_tabs:
            config = self.load_resolved_config()
            by_key = {(p.get('name'), p.get('group', 'General')): p
                      for p in config.get('profiles', [])}
            for entry in open_tabs:
                if entry is None:
                    self.add_tab(focus=False)
                elif 'layout' in entry:
                    self._restore_tab(entry['layout'], by_key, focus=False)
                else:
                    # Legacy format: {name, group}
                    key = (entry.get('name', ''), entry.get('group', 'General'))
                    self.add_tab(profile=by_key.get(key), focus=False)
            if 0 <= active_tab_index < self.notebook.get_n_pages():
                self.notebook.set_current_page(active_tab_index)
            term = self._get_active_terminal()
            if term:
                GLib.idle_add(lambda: term.grab_focus() and False)
        else:
            self._show_welcome()

        self.window.present()

    def _build_window(self, state):
        self.window = Adw.ApplicationWindow(application=self, title=APP_NAME)
        self.window.set_default_size(
            state.get('window_width', WIN_DEFAULT_W),
            state.get('window_height', WIN_DEFAULT_H))
        if state.get('window_maximized', False):
            self.window.maximize()
        self.window.connect("close-request", self._on_window_close_request)
        self.window.connect("notify::is-active", self._on_window_active_changed)

        # Capture-phase key handler so Ctrl+T / Ctrl+Alt+R aren't swallowed
        # by the terminal child.
        win_key_ctrl = Gtk.EventControllerKey()
        win_key_ctrl.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        win_key_ctrl.connect("key-pressed", self.on_window_key_pressed)
        self.window.add_controller(win_key_ctrl)

        # Adw.ToolbarView wraps a top headerbar + content.
        toolbar = Adw.ToolbarView()
        self.window.set_content(toolbar)

        toolbar.add_top_bar(self._build_header())

        # AdwOverlaySplitView gives us a togglable sidebar.

        self.split_view = Adw.OverlaySplitView()
        self.split_view.set_sidebar_width_fraction(
            state.get('sidebar_width_fraction', SIDEBAR_FRACTION))
        self.split_view.set_min_sidebar_width(SIDEBAR_MIN_W)
        self.split_view.set_max_sidebar_width(SIDEBAR_MAX_W)
        sidebar_visible = state.get('sidebar_visible', False)
        self.split_view.set_show_sidebar(sidebar_visible)
        self._sidebar_toggle_btn.set_active(sidebar_visible)
        toolbar.set_content(self.split_view)

        # Sidebar pane — horizontal so the drag handle sits at the right edge.
        sidebar_outer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        sidebar_outer.add_css_class('sidebar-pane')

        sidebar_inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        sidebar_inner.set_hexpand(True)
        sidebar_inner.set_vexpand(True)

        self.search_entry = Gtk.SearchEntry()
        self.search_entry.set_placeholder_text("Search profiles…")
        self.search_entry.set_margin_top(4)
        self.search_entry.set_margin_bottom(4)
        self.search_entry.set_margin_start(6)
        self.search_entry.set_margin_end(6)
        self.search_entry.connect('search-changed', self._on_search_changed)
        self.search_entry.connect('stop-search', self._on_search_stop)
        sidebar_inner.append(self.search_entry)

        sidebar_scroll = Gtk.ScrolledWindow()
        sidebar_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sidebar_scroll.set_hexpand(True)
        sidebar_scroll.set_vexpand(True)

        self.sidebar_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.sidebar_box.set_margin_top(8)
        self.sidebar_box.set_margin_bottom(8)
        self.sidebar_box.set_margin_start(6)
        self.sidebar_box.set_margin_end(6)
        sidebar_scroll.set_child(self.sidebar_box)

        sidebar_inner.append(sidebar_scroll)

        # Clock — pinned to the bottom of the sidebar simply by being the
        # last child after the vexpand'd scroll area; no overlay needed.
        # Both lines are drawn (not Labels) so their font scales to fill the
        # sidebar's width, however wide the user drags it.
        sidebar_inner.append(Gtk.Separator())

        clock_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        clock_box.set_margin_top(4)
        clock_box.set_margin_bottom(8)
        clock_box.set_margin_start(4)
        clock_box.set_margin_end(4)

        self._clock_time_text = datetime.now().strftime('%H:%M:%S')
        self.clock_time_area = Gtk.DrawingArea()
        self.clock_time_area.set_hexpand(True)
        self.clock_time_area.set_content_height(CLOCK_TIME_AREA_H)
        self.clock_time_area.set_draw_func(self._draw_clock_time)
        clock_box.append(self.clock_time_area)

        self._clock_date_text = datetime.now().strftime('%a %b %d %Y')
        self.clock_date_area = Gtk.DrawingArea()
        self.clock_date_area.set_hexpand(True)
        self.clock_date_area.set_content_height(CLOCK_DATE_AREA_H)
        self.clock_date_area.set_draw_func(self._draw_clock_date)
        clock_box.append(self.clock_date_area)

        sidebar_inner.append(clock_box)
        self._update_clock()
        GLib.timeout_add_seconds(1, self._update_clock)

        sidebar_outer.append(sidebar_inner)

        # Drag handle
        handle = Gtk.Box()
        handle.set_size_request(SIDEBAR_HANDLE_W, -1)
        handle.add_css_class('sidebar-handle')
        handle.set_cursor_from_name('col-resize')
        drag = Gtk.GestureDrag()
        drag.connect('drag-begin',  self._on_sidebar_drag_begin)
        drag.connect('drag-update', self._on_sidebar_drag_update)
        handle.add_controller(drag)
        sidebar_outer.append(handle)

        self.split_view.set_sidebar(sidebar_outer)
        # Keep the toggle button's state in sync with the split view.
        self.split_view.connect('notify::show-sidebar', self._on_show_sidebar_changed)

        self._build_sidebar(state)

        # Notebook (tabs)
        self.notebook = Gtk.Notebook()
        self.notebook.set_show_tabs(True)
        self.notebook.set_scrollable(True)
        self.notebook.set_hexpand(True)
        self.notebook.set_vexpand(True)
        self.notebook.connect('switch-page', self._on_tab_switched)

        tab_actions = Gtk.Box(spacing=2)

        split_h_btn = Gtk.Button(icon_name='ttyga-split-horiz-symbolic')
        split_h_btn.add_css_class('flat')
        split_h_btn.set_tooltip_text("Split pane side by side (Ctrl+Shift+E)")
        split_h_btn.connect("clicked", lambda _: self._split_pane(Gtk.Orientation.HORIZONTAL))
        tab_actions.append(split_h_btn)

        split_v_btn = Gtk.Button(icon_name='ttyga-split-vert-symbolic')
        split_v_btn.add_css_class('flat')
        split_v_btn.set_tooltip_text("Split pane top/bottom (Ctrl+Shift+D)")
        split_v_btn.connect("clicked", lambda _: self._split_pane(Gtk.Orientation.VERTICAL))
        tab_actions.append(split_v_btn)

        self.notebook.set_action_widget(tab_actions, Gtk.PackType.END)

        # Stack switches between the welcome screen and the notebook.
        self.content_stack = Gtk.Stack()
        self.content_stack.set_hexpand(True)
        self.content_stack.set_vexpand(True)
        self.content_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.content_stack.set_transition_duration(120)
        self.content_stack.add_named(self._build_welcome_screen(), 'welcome')
        self.content_stack.add_named(self.notebook, 'notebook')

        self.split_view.set_content(self.content_stack)

    def _build_welcome_screen(self):
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        inner.set_hexpand(True)
        inner.set_vexpand(True)
        inner.set_halign(Gtk.Align.CENTER)
        inner.set_valign(Gtk.Align.CENTER)

        icon_px = 128
        img = Gtk.Image()
        icon_path = self._resolve_welcome_icon()
        loaded = False
        if icon_path is not None:
            try:
                pb = GdkPixbuf.Pixbuf.new_from_file_at_size(str(icon_path), icon_px, icon_px)
                texture = Gdk.Texture.new_for_pixbuf(pb)
                img.set_from_paintable(texture)
                img.set_size_request(icon_px, icon_px)
                loaded = True
            except Exception:
                pass
        if not loaded:
            img.set_pixel_size(icon_px)
            img.set_from_icon_name(APP_ID)
        img.add_css_class('welcome-logo')
        img.set_margin_bottom(6)
        inner.append(img)

        title_lbl = Gtk.Label(label="Open or Start a Terminal")
        title_lbl.add_css_class('welcome-title')
        inner.append(title_lbl)

        bullets = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        bullets.set_halign(Gtk.Align.CENTER)
        bullets.set_margin_top(10)
        for markup in (
            'Press Ctrl+Shift+T to <a href="new-tab">open a new tab</a>',
            'Press Ctrl+Shift+E to <a href="split-beside">split the pane side by side</a>',
            'Press Ctrl+Shift+D to <a href="split-below">split the pane top and bottom</a>',
            'Press Ctrl+Shift+B to <a href="toggle-sidebar">toggle the sidebar</a>',
        ):
            lbl = Gtk.Label(label=f"•  {markup}")
            lbl.set_use_markup(True)
            lbl.set_halign(Gtk.Align.START)
            lbl.set_xalign(0.0)
            lbl.add_css_class('welcome-bullet')
            lbl.add_css_class('dim-label')
            lbl.connect('activate-link', self._on_welcome_link)
            bullets.append(lbl)
        inner.append(bullets)

        hint_lbl = Gtk.Label(label="Or, press Ctrl+Q to quit ttyga.")
        hint_lbl.set_margin_top(14)
        hint_lbl.add_css_class('welcome-hint')
        hint_lbl.add_css_class('dim-label')
        inner.append(hint_lbl)

        return inner

    def _resolve_welcome_icon(self):
        """Locate the monochrome welcome icon. Resolution order:
           1. settings['welcome_image'] if set and the file exists
           2. the bundled 'ttyga-mono' themed icon (staged into the icon theme)
           Returns a Path, or None to fall back to the themed app icon."""
        override = self.settings.get('welcome_image', '')
        if override and Path(override).is_file():
            return Path(override)
        display = Gdk.Display.get_default()
        theme   = Gtk.IconTheme.get_for_display(display) if display else None
        icon    = (theme.lookup_icon(MONO_ICON_NAME, [], 128, 1,
                                     Gtk.TextDirection.NONE, 0)
                   if theme else None)
        gfile   = icon.get_file() if icon else None
        path    = gfile.get_path() if gfile else None
        # lookup_icon falls back to a stand-in when the name is absent; only
        # accept a hit that actually resolved to our bundled icon.
        if path and MONO_ICON_NAME in Path(path).name:
            return Path(path)
        return None

    def _on_welcome_link(self, _label, uri):
        if uri == 'new-tab':
            self.add_tab()
        elif uri == 'split-beside':
            self._split_pane(Gtk.Orientation.HORIZONTAL)
        elif uri == 'split-below':
            self._split_pane(Gtk.Orientation.VERTICAL)
        elif uri == 'toggle-sidebar':
            self.split_view.set_show_sidebar(not self.split_view.get_show_sidebar())
        return True

    def _show_welcome(self):
        self.content_stack.set_visible_child_name('welcome')

    def _show_notebook(self):
        self.content_stack.set_visible_child_name('notebook')

    def _build_header(self):
        header = Adw.HeaderBar()
        header.set_title_widget(Adw.WindowTitle.new(APP_NAME, ""))

        self._sidebar_toggle_btn = Gtk.ToggleButton()
        self._sidebar_toggle_btn.set_icon_name('sidebar-show-symbolic')
        self._sidebar_toggle_btn.add_css_class('flat')
        self._sidebar_toggle_btn.set_tooltip_text("Toggle sidebar (Ctrl+Shift+B)")
        self._sidebar_toggle_btn.connect('toggled', self._on_sidebar_toggle)
        header.pack_start(self._sidebar_toggle_btn)

        new_tab_btn = Gtk.Button()
        new_tab_btn.set_icon_name('tab-new-symbolic')
        new_tab_btn.add_css_class('flat')
        new_tab_btn.set_tooltip_text("New tab (Ctrl+Shift+T)")
        new_tab_btn.connect('clicked', self.add_tab)
        header.pack_start(new_tab_btn)

        _title = f"{_LOCAL_USER}@{_LOCAL_HOST}"
        split_popover = Gtk.Popover()
        split_popover.add_css_class('menu')
        split_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        split_box.set_margin_top(4)
        split_box.set_margin_bottom(4)
        split_box.set_margin_start(4)
        split_box.set_margin_end(4)
        for _label, _icon, layout in (
            ("Side by side",   "ttyga-split-horiz-symbolic",
             {'split': 'horizontal', 'start': {}, 'end': {}}),
            ("Top and bottom", "ttyga-split-vert-symbolic",
             {'split': 'vertical', 'start': {}, 'end': {}}),
            ("2×2 grid",       "ttyga-split-quad-symbolic",
             {'split': 'horizontal',
              'start': {'split': 'vertical', 'start': {}, 'end': {}},
              'end':   {'split': 'vertical', 'start': {}, 'end': {}}}),
        ):
            row = Gtk.Button()
            row.add_css_class('flat')
            row_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            row_box.set_margin_start(4)
            row_box.set_margin_end(8)
            icon = Gtk.Image.new_from_icon_name(_icon)
            icon.set_pixel_size(16)
            row_box.append(icon)
            row_box.append(Gtk.Label(label=_label))
            row.set_child(row_box)
            row.connect('clicked', lambda _, l=layout, t=_title:
                        (split_popover.popdown(),
                         self.add_tab(profile={'name': t, 'layout': l})))
            split_box.append(row)
        split_popover.set_child(split_box)

        split_btn = Gtk.MenuButton()
        split_btn.set_icon_name('go-down-symbolic')
        split_btn.add_css_class('flat')
        split_btn.set_tooltip_text("New split tab")
        split_btn.set_popover(split_popover)
        header.pack_start(split_btn)

        # Trailing: open-in split-button + menu
        open_menu = Gio.Menu()
        open_menu.append("Open in file manager", "app.open-folder")
        open_menu.append("Open in VS Code",       "app.open-vscode")
        for name, handler in (
            ('open-folder', self._on_open_folder),
            ('open-vscode', self._on_open_vscode),
        ):
            act = Gio.SimpleAction.new(name, None)
            act.connect('activate', handler)
            self.add_action(act)

        open_btn = Adw.SplitButton()
        open_btn.set_icon_name('folder-open-symbolic')
        open_btn.set_tooltip_text("Open current directory in file manager")
        open_btn.add_css_class('flat')
        open_btn.set_menu_model(open_menu)
        open_btn.connect('clicked', self._on_open_folder)
        header.pack_end(self._build_menu_button())
        header.pack_end(open_btn)

        return header

    def _build_menu_button(self):
        menu = Gio.Menu()
        menu.append("Edit profiles", "app.edit-profiles")
        menu.append("Preferences", "app.preferences")
        menu.append("Keyboard Shortcuts", "app.shortcuts")
        menu.append("Help", "app.help")
        menu.append("About ttyga", "app.about")

        menu_btn = Gtk.MenuButton()
        menu_btn.set_icon_name('open-menu-symbolic')
        menu_btn.add_css_class('flat')
        menu_btn.set_tooltip_text("Main menu")
        menu_btn.set_menu_model(menu)

        # Register actions on the application
        for name, handler in (
            ('edit-profiles', self._open_editor),
            ('preferences',   self._open_preferences),
            ('shortcuts',     self._open_shortcuts),
            ('help',          self._open_help),
            ('about',         self._open_about),
        ):
            act = Gio.SimpleAction.new(name, None)
            act.connect('activate', handler)
            self.add_action(act)

        return menu_btn

    # ----- menu actions ----------------------------------------------------

    def _profile_icon(self, profile):
        """Effective display icon for a profile: own icon > group icon > ''."""
        if not profile:
            return ''
        icon = profile.get('icon', '')
        if icon:
            return icon
        g_name = profile.get('group', 'General')
        _, group_icons = _parse_groups(self.load_config())
        return group_icons.get(g_name, '')

    def _get_open_path(self, terminal):
        """Return a local filesystem path for open-in actions (VS Code, etc.).

        Prefers OSC 7; skips remote URIs from SSH tabs (hostname present).
        Falls back to an implied cwd from the profile command, then spawn_dir."""
        uri = terminal.get_current_directory_uri() if terminal else None
        if uri:
            try:
                path, hostname = GLib.filename_from_uri(uri)
                if path and not hostname:
                    return path
            except Exception:
                pass
        meta = self.tabs.get(terminal, {})
        profile = meta.get('profile')
        if profile:
            implied = _implied_cwd(profile.get('options', {}).get('command', ''))
            if implied:
                return implied
        return meta.get('spawn_dir', os.environ.get('HOME', '/'))

    def _get_nautilus_uri(self, terminal):
        """Return a URI for Nautilus: sftp:// for SSH tabs, file:// for local.

        For SSH tabs, uses the profile host and the remote path from OSC 7
        (falls back to / when OSC 7 is unavailable on the remote)."""
        meta    = self.tabs.get(terminal, {})
        kind    = meta.get('kind', 'local')
        profile = meta.get('profile')

        if kind == 'ssh' and profile:
            host = profile.get('options', {}).get('host', '').strip()
            if host:
                remote_path = '/'
                uri = terminal.get_current_directory_uri() if terminal else None
                if uri:
                    try:
                        path, _hostname = GLib.filename_from_uri(uri)
                        if path:
                            remote_path = path
                    except Exception:
                        pass
                return f"sftp://{host}{remote_path}"

        return GLib.filename_to_uri(self._get_open_path(terminal), None)

    def _on_open_vscode(self, *args):
        terminal = self._get_active_terminal()
        path = self._get_open_path(terminal) if terminal else os.environ.get('HOME', '/')
        try:
            Gio.Subprocess.new(['code', path], Gio.SubprocessFlags.NONE)
        except Exception as e:
            logger.warning("Could not launch VS Code: %s", e)

    def _on_open_folder(self, *args):
        terminal = self._get_active_terminal()
        uri = (self._get_nautilus_uri(terminal) if terminal
               else GLib.filename_to_uri(os.environ.get('HOME', '/'), None))
        Gio.AppInfo.launch_default_for_uri(uri, None)

    def _open_editor(self, *args, select_key=None):
        EditorWindow(self, select_key=select_key).present()

    def _edit_profile(self, profile):
        key = (profile.get('name'), profile.get('group', 'General'))
        self._open_editor(select_key=key)

    def _open_preferences(self, *args):
        PreferencesWindow(self).present()

    def _open_shortcuts(self, *args):
        builder_str = """
        <interface>
          <object class="GtkShortcutsWindow" id="win">
            <property name="modal">true</property>
            <child>
              <object class="GtkShortcutsSection">
                <child>
                  <object class="GtkShortcutsGroup">
                    <property name="title">Application</property>
                    <child><object class="GtkShortcutsShortcut">
                      <property name="title">Quit</property>
                      <property name="accelerator">&lt;Ctrl&gt;q</property>
                    </object></child>
                    <child><object class="GtkShortcutsShortcut">
                      <property name="title">Toggle full-screen</property>
                      <property name="accelerator">F11</property>
                    </object></child>
                    <child><object class="GtkShortcutsShortcut">
                      <property name="title">Reload profiles</property>
                      <property name="accelerator">&lt;Ctrl&gt;&lt;Alt&gt;r</property>
                    </object></child>
                  </object>
                </child>
                <child>
                  <object class="GtkShortcutsGroup">
                    <property name="title">Tabs</property>
                    <child><object class="GtkShortcutsShortcut">
                      <property name="title">New tab</property>
                      <property name="accelerator">&lt;Ctrl&gt;&lt;Shift&gt;t</property>
                    </object></child>
                    <child><object class="GtkShortcutsShortcut">
                      <property name="title">Close tab</property>
                      <property name="accelerator">&lt;Ctrl&gt;&lt;Shift&gt;w</property>
                    </object></child>
                    <child><object class="GtkShortcutsShortcut">
                      <property name="title">Next tab</property>
                      <property name="accelerator">&lt;Ctrl&gt;Tab</property>
                    </object></child>
                    <child><object class="GtkShortcutsShortcut">
                      <property name="title">Previous tab</property>
                      <property name="accelerator">&lt;Ctrl&gt;&lt;Shift&gt;Tab</property>
                    </object></child>
                  </object>
                </child>
                <child>
                  <object class="GtkShortcutsGroup">
                    <property name="title">Sidebar</property>
                    <child><object class="GtkShortcutsShortcut">
                      <property name="title">Toggle sidebar</property>
                      <property name="accelerator">&lt;Ctrl&gt;&lt;Shift&gt;b</property>
                    </object></child>
                    <child><object class="GtkShortcutsShortcut">
                      <property name="title">Focus search</property>
                      <property name="accelerator">&lt;Ctrl&gt;f</property>
                    </object></child>
                    <child><object class="GtkShortcutsShortcut">
                      <property name="title">Clear search / return to terminal</property>
                      <property name="accelerator">Escape</property>
                    </object></child>
                  </object>
                </child>
                <child>
                  <object class="GtkShortcutsGroup">
                    <property name="title">Terminal</property>
                    <child><object class="GtkShortcutsShortcut">
                      <property name="title">Copy</property>
                      <property name="accelerator">&lt;Ctrl&gt;&lt;Shift&gt;c</property>
                    </object></child>
                    <child><object class="GtkShortcutsShortcut">
                      <property name="title">Paste</property>
                      <property name="accelerator">&lt;Ctrl&gt;&lt;Shift&gt;v</property>
                    </object></child>
                    <child><object class="GtkShortcutsShortcut">
                      <property name="title">Increase font size</property>
                      <property name="accelerator">&lt;Ctrl&gt;equal</property>
                    </object></child>
                    <child><object class="GtkShortcutsShortcut">
                      <property name="title">Decrease font size</property>
                      <property name="accelerator">&lt;Ctrl&gt;minus</property>
                    </object></child>
                    <child><object class="GtkShortcutsShortcut">
                      <property name="title">Reset font size</property>
                      <property name="accelerator">&lt;Ctrl&gt;0</property>
                    </object></child>
                  </object>
                </child>
              </object>
            </child>
          </object>
        </interface>
        """
        builder = Gtk.Builder.new_from_string(builder_str, -1)
        win = builder.get_object('win')
        win.set_transient_for(self.window)
        win.present()

    def _open_help(self, *args):
        HelpWindow(self.window).present()

    def _open_about(self, *args):
        about = Adw.AboutWindow(
            transient_for=self.window,
            application_name=APP_NAME,
            application_icon=APP_ID,
            developer_name=APP_AUTHOR,
            version=APP_VERSION,
            comments=APP_COMMENTS,
            license_type=Gtk.License.MIT_X11,
        )
        about.present()

    # ----- sidebar toggle wiring ------------------------------------------

    def _on_sidebar_toggle(self, btn):
        self.split_view.set_show_sidebar(btn.get_active())

    def _on_show_sidebar_changed(self, split_view, _pspec):
        if self._sidebar_toggle_btn is not None:
            self._sidebar_toggle_btn.set_active(split_view.get_show_sidebar())

    def _on_sidebar_drag_begin(self, gesture, _x, _y):
        self._drag_start_fraction = self.split_view.get_sidebar_width_fraction()
        self._drag_start_win_width = self.window.get_width()

    def _on_sidebar_drag_update(self, gesture, offset_x, _offset_y):
        w = self._drag_start_win_width
        if w <= 0:
            return
        new_px = self._drag_start_fraction * w + offset_x
        new_px = max(self.split_view.get_min_sidebar_width(),
                     min(self.split_view.get_max_sidebar_width(), new_px))
        self.split_view.set_sidebar_width_fraction(new_px / w)

    # ----- terminals & tabs ------------------------------------------------

    def _zoom_font(self, delta):
        """Shift terminal font size by delta points. Ephemeral — not saved to settings."""
        global_raw = Pango.FontDescription.from_string(
            self.settings.get('terminal_font', DEFAULT_SETTINGS['terminal_font'])
        ).get_size() / Pango.SCALE
        new_global = max(6, min(72, global_raw + self._font_zoom_delta + delta))
        self._font_zoom_delta = new_global - global_raw
        for term, meta in self.tabs.items():
            base = Pango.FontDescription.from_string(meta['base_font'])
            raw = base.get_size() / Pango.SCALE
            base.set_size(int(max(6, min(72, raw + self._font_zoom_delta)) * Pango.SCALE))
            term.set_font(base)

    def _on_terminal_scroll(self, controller, _dx, dy):
        state = controller.get_current_event_state()
        if state & Gdk.ModifierType.CONTROL_MASK:
            self._zoom_font(-1 if dy < 0 else 1)
            return True
        speed = float(self.settings.get('scroll_speed', DEFAULT_SETTINGS['scroll_speed']))
        if not hasattr(controller, '_scroll_accum'):
            controller._scroll_accum = 0.0
        controller._scroll_accum += dy * speed
        lines = int(controller._scroll_accum)
        if lines:
            controller._scroll_accum -= lines
            adj = controller.get_widget().get_vadjustment()
            if adj:
                lo, hi, page = adj.get_lower(), adj.get_upper(), adj.get_page_size()
                adj.set_value(max(lo, min(hi - page, adj.get_value() + lines)))
        return True

    def _new_terminal(self, on_spawn=None, shell_args=None, cwd=None, env=None, profile=None):
        terminal = Vte.Terminal()
        terminal.set_hexpand(True)
        terminal.set_vexpand(True)
        # Inset the terminal's left/right/bottom edges so they clear an
        # adjacent Gtk.Paned separator's drag grab zone, which otherwise
        # steals clicks meant for text selection. No top margin needed —
        # the pane-bar above every terminal already buffers that edge.
        # Applied to the terminal (not the pane-box) so the pane-bar stays flush.
        terminal.set_margin_start(12)
        terminal.set_margin_end(12)
        terminal.set_margin_bottom(12)

        font_str = ((profile.get('terminal_font') if profile else None) or
                    self.settings.get('terminal_font', DEFAULT_SETTINGS['terminal_font']))
        try:
            terminal.set_font(Pango.FontDescription.from_string(font_str))
        except Exception:
            pass
        terminal.set_scrollback_lines(int(self.settings.get('scrollback', 10000)))
        terminal.set_audible_bell(False)
        self._apply_terminal_colors(terminal,
            theme=profile.get('color_scheme') if profile else None)

        terminal.connect('selection-changed', self._on_term_selection_changed)
        terminal.connect('window-title-changed', self._on_terminal_title_changed)
        terminal.connect('notify::has-focus', self._on_terminal_focus)
        terminal.connect('bell', self._on_terminal_bell)

        key_ctrl = Gtk.EventControllerKey()
        key_ctrl.connect("key-pressed", self.on_key_pressed)
        terminal.add_controller(key_ctrl)

        scroll_ctrl = Gtk.EventControllerScroll(
            flags=Gtk.EventControllerScrollFlags.VERTICAL)
        scroll_ctrl.connect('scroll', self._on_terminal_scroll)
        terminal.add_controller(scroll_ctrl)

        spawn_dir = str(Path(cwd).expanduser()) if cwd else os.environ.get('HOME')
        spawn_env = None
        if env:
            merged = dict(os.environ)
            merged.update({k: os.path.expandvars(os.path.expanduser(str(v)))
                           for k, v in env.items()})
            spawn_env = [f'{k}={v}' for k, v in merged.items()]

        def _spawn_cb(term, pid, *_):
            if pid >= 0 and term in self.tabs:
                self.tabs[term]['child_pid'] = pid
            if on_spawn is not None and pid >= 0:
                on_spawn(term)

        terminal.spawn_async(
            Vte.PtyFlags.DEFAULT,
            spawn_dir,
            ["/bin/bash"] + (shell_args or []),
            spawn_env,
            GLib.SpawnFlags.DEFAULT,
            None, None, -1, None,
            _spawn_cb,
        )
        return terminal

    def _on_term_selection_changed(self, terminal):
        if self.settings.get('copy_on_selection', True) and terminal.get_has_selection():
            terminal.copy_clipboard_format(Vte.Format.TEXT)

    def _on_tab_switched(self, notebook, tab_root, page_num):
        # Find the right terminal: keep _active_terminal if it belongs to this tab
        if (self._active_terminal and
                self.tabs.get(self._active_terminal, {}).get('tab_root') is tab_root):
            term = self._active_terminal
        else:
            term = self._first_terminal_in(tab_root)
            self._active_terminal = term

        # Clear the activity indicator and notification state for the tab we just switched to.
        dot = self.tabs.get(term, {}).get('dot') if term else None
        if dot:
            dot.remove_css_class('tab-dot-activity')
            tab_box = dot.get_parent()
            if tab_box:
                tab_box.remove_css_class('tab-activity')
        self._notified_roots.discard(tab_root)
        self._update_launcher_badge(len(self._notified_roots))

        self._update_sidebar_highlight(tab_root)

    def _update_sidebar_highlight(self, tab_root):
        """Highlight every sidebar row whose profile is open as a pane in
        tab_root — a merged tab can host more than one profile at once."""
        keys = set()
        for t, m in self.tabs.items():
            if m.get('tab_root') is not tab_root:
                continue
            profile = m.get('profile')
            if profile:
                keys.add((profile.get('name'), profile.get('group', 'General')))

        for btn in self.active_btns:
            btn.remove_css_class('active')
        self.active_btns = set()
        self.active_profile_keys = keys

        for btn, p in self._profile_buttons:
            key = (p.get('name'), p.get('group', 'General'))
            if key in keys:
                btn.add_css_class('active')
                self.active_btns.add(btn)

    def _refresh_sidebar_highlight(self):
        """Recompute highlighting for whichever tab is currently on screen."""
        page = self.notebook.get_current_page()
        if page < 0:
            return
        self._update_sidebar_highlight(self.notebook.get_nth_page(page))

    def _on_terminal_focus(self, terminal, _param):
        if terminal.has_focus():
            self._active_terminal = terminal
            meta = self.tabs.get(terminal, {})
            dot = meta.get('dot')
            if dot:
                dot.remove_css_class('tab-dot-activity')
                tab_box = dot.get_parent()
                if tab_box:
                    tab_box.remove_css_class('tab-activity')
            self._notified_roots.discard(meta.get('tab_root'))
            self._update_launcher_badge(len(self._notified_roots))

    def _on_terminal_bell(self, terminal):
        """Fired on a BEL from the pty (e.g. Claude Code ringing the bell when
        it's waiting for input) — flag the background tab and notify."""
        meta = self.tabs.get(terminal)
        if not meta:
            return
        # Only flag background tabs — not the one currently on screen.
        current = self.notebook.get_current_page()
        current_root = self.notebook.get_nth_page(current) if current >= 0 else None
        tab_root = meta.get('tab_root')
        if tab_root is current_root:
            return
        dot = meta.get('dot')
        if dot and not dot.has_css_class('tab-dot-activity'):
            dot.add_css_class('tab-dot-activity')
            tab_box = dot.get_parent()
            if tab_box:
                tab_box.add_css_class('tab-activity')
        # Desktop notification: once per episode, only when window is in the background.
        if not self._window_active and tab_root not in self._notified_roots:
            self._notified_roots.add(tab_root)
            self._notify_tab(meta)
            self._update_launcher_badge(len(self._notified_roots))

    def _on_window_active_changed(self, window, _param):
        self._window_active = window.is_active()
        if self._window_active:
            self._notified_roots.clear()
            self._update_launcher_badge(0)

    def _update_launcher_badge(self, count):
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            props = {
                'count':         GLib.Variant('x', count),
                'count-visible': GLib.Variant('b', count > 0),
            }
            bus.emit_signal(
                None,
                '/com/canonical/Unity/LauncherEntry',
                'com.canonical.Unity.LauncherEntry',
                'Update',
                GLib.Variant('(sa{sv})', ('application://ca.greg.ttyga.desktop', props)))
        except Exception:
            pass

    def _notify_tab(self, meta):
        """Fire a desktop notification for background activity in this tab."""
        profile = meta.get('profile')
        if profile and profile.get('notify_text'):
            body = profile['notify_text']
        elif profile:
            if meta.get('kind') == 'ssh':
                opts = profile.get('options', {})
                user = opts.get('user', '').strip()
                host = opts.get('host', '').strip()
                body = f"{user}@{host}" if user else host
            else:
                body = profile.get('name') or meta['label'].get_text()
        else:
            body = meta['label'].get_text() or 'Terminal'
        try:
            subprocess.Popen(
                ['notify-send', '-a', 'ttyga', 'ttyga', body],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except FileNotFoundError:
            pass

    def _on_terminal_title_changed(self, terminal):
        title = terminal.get_window_title()
        if title and terminal is self._active_terminal:
            meta = self.tabs.get(terminal)
            if meta and meta.get('profile') is None:
                meta['label'].set_label(title)

    def _make_tab_label(self, title, kind='local', icon=None):
        """A tab: status dot · [profile icon] · title · close.

        When a profile icon is set the dot is hidden and the icon itself is
        coloured green (SSH) or dim (local) via the tab-dot-{kind} CSS class.
        """
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)

        dot = Gtk.Image.new_from_icon_name('media-record-symbolic')
        dot.set_pixel_size(10)
        dot.add_css_class(f'tab-dot-{kind}')
        dot.set_visible(not icon)
        box.append(dot)

        if icon:
            if _is_gtk_icon(icon):
                icon_widget = Gtk.Image.new_from_icon_name(icon)
                icon_widget.set_pixel_size(14)
                icon_widget.add_css_class(f'tab-dot-{kind}')
                box.append(icon_widget)
            elif Path(icon).expanduser().is_file():
                icon_widget = Gtk.Image.new_from_file(str(Path(icon).expanduser()))
                icon_widget.set_pixel_size(14)
                box.append(icon_widget)
            else:
                icon_widget = Gtk.Label()
                icon_widget.set_markup(
                    f'<span size="small">{GLib.markup_escape_text(icon)}</span>')
                icon_widget.set_size_request(14, 14)
                icon_widget.set_xalign(0.5)
                icon_widget.set_yalign(0.5)
                icon_widget.add_css_class(f'tab-dot-{kind}')
                box.append(icon_widget)

        label = Gtk.Label(label=title)
        box.append(label)

        close_btn = Gtk.Button(icon_name='window-close-symbolic')
        close_btn.add_css_class('flat')
        close_btn.set_focusable(False)
        box.append(close_btn)

        return box, label, close_btn, dot

    def _make_pane_box(self, terminal, meta):
        """Wrap a terminal in a thin header bar (profile label + close button)."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.add_css_class('pane-box')
        box.set_hexpand(True)
        box.set_vexpand(True)

        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        bar.add_css_class('pane-bar')
        bar.set_hexpand(True)

        profile    = meta.get('profile')
        kind       = meta.get('kind', 'local')
        pane_label = meta.get('pane_label')           # explicit override (layout panes)
        if pane_label is not None:
            lbl_text = pane_label
        elif profile:
            if kind == 'ssh':
                opts = profile.get('options', {})
                user = opts.get('user', '').strip()
                host = opts.get('host', '').strip()
                lbl_text = f"{user}@{host}" if user else host
            else:
                lbl_text = profile.get('name', '')
        else:
            lbl_text = ''

        lbl = Gtk.Label(label=lbl_text)
        lbl.set_hexpand(True)
        lbl.set_xalign(0.5)
        lbl.set_ellipsize(Pango.EllipsizeMode.END)
        bar.append(lbl)

        close_btn = Gtk.Button(icon_name='window-close-symbolic')
        close_btn.add_css_class('flat')
        close_btn.set_focusable(False)
        close_btn.connect('clicked', self._on_pane_close_clicked, terminal)
        bar.append(close_btn)
        meta['pane_bar'] = bar

        box.append(bar)
        box.append(terminal)
        return box

    def _update_pane_bars(self, tab_root):
        terminals = list(self._all_terminals_in(tab_root))
        show = len(terminals) > 1
        for t in terminals:
            bar = self.tabs.get(t, {}).get('pane_bar')
            if bar:
                bar.set_visible(show)

    def _on_pane_close_clicked(self, btn, terminal):
        meta = self.tabs.get(terminal)
        if meta is None:
            return
        tab_root = meta['tab_root']
        in_tab = [t for t, m in self.tabs.items() if m.get('tab_root') is tab_root]
        if len(in_tab) <= 1:
            self._on_close_tab(None, terminal)
        else:
            self._close_pane(terminal)

    def add_tab(self, *args, profile=None, focus=True, cwd=None, resolved_vars=None):
        self.tab_count += 1

        p_cwd = profile.get('cwd', '') if profile else ''
        p_env = profile.get('env', {}) if profile else {}
        rv    = resolved_vars or {}

        # --- Shell-split shorthand: synthesise a split layout on the fly -------
        _shell_split = profile.get('shell_split') if profile else None
        if (_shell_split and not profile.get('layout')
                and profile.get('type', 'clippet') != 'ssh'):
            _opts = profile.get('options', {})
            _cmd  = _apply_vars(_opts.get('command', ''), rv)
            _shell_cwd = p_cwd or cwd or _implied_cwd(_cmd) or ''
            _axis = 'horizontal' if _shell_split == 'beside' else 'vertical'
            profile = dict(profile, layout={
                'split': _axis,
                'start': {'command': _cmd, 'cwd': _shell_cwd,
                          'auto_execute': _opts.get('auto_execute', False)},
                'end': {'cwd': _shell_cwd},
            })

        # --- Layout path: profile defines a multi-pane tree --------------------
        if profile and profile.get('layout'):
            title    = self._build_classifier_title(profile, cwd=cwd, resolved_vars=rv)
            tab_icon = self._profile_icon(profile)
            tab_box, tab_label, close_btn, dot = self._make_tab_label(
                title, kind='local', icon=tab_icon)
            color = profile.get('color', '')
            if color:
                dot_prov = Gtk.CssProvider()
                dot_prov.load_from_string(f"image {{ color: {color}; }}")
                dot.get_style_context().add_provider(
                    dot_prov, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)
            tab_root = Gtk.Box()
            tab_root.set_hexpand(True)
            tab_root.set_vexpand(True)
            content = self._build_profile_layout(
                profile['layout'], tab_root, tab_label, dot,
                profile, str(Path(p_cwd or cwd).expanduser()) if (p_cwd or cwd) else None,
                p_env or None)
            tab_root.append(content)
            self._update_pane_bars(tab_root)
            first_term = self._first_terminal_in(tab_root)
            if first_term:
                close_btn.connect('clicked', self._on_close_tab, first_term)
                self._attach_tab_context_menu(tab_box, first_term)
            self._show_notebook()
            idx = self.notebook.append_page(tab_root, tab_box)
            self.notebook.set_tab_reorderable(tab_root, True)
            self.notebook.set_current_page(idx)
            if focus and first_term:
                GLib.idle_add(lambda t=first_term:
                              GLib.idle_add(lambda: t.grab_focus() and False,
                                            priority=GLib.PRIORITY_LOW) and False)
            return
        # -----------------------------------------------------------------------

        p_type = profile.get('type', 'clippet') if profile else None
        opts   = profile.get('options', {})      if profile else {}

        if profile:
            tmux_on      = bool(profile.get('tmux', False))
            tmux_session = (profile.get('tmux_session') or 'main').strip()
            tmux_cmd     = f"tmux attach -t {tmux_session} || tmux new -s {tmux_session}"

            if p_type == 'ssh':
                user = _apply_vars(opts.get('user', '').strip(), rv)
                host = _apply_vars(opts.get('host', 'localhost').strip(), rv)
                port = _apply_vars(str(opts.get('port', '')).strip(), rv)
                parts = ['ssh']
                if port:
                    parts += ['-p', port]
                parts.append(f'{user}@{host}' if user else host)
                if tmux_on:
                    parts += ['-t', f"'{tmux_cmd}'"]
                cmd = ' '.join(parts) + '\n'
            else:
                if tmux_on:
                    cmd = tmux_cmd + '\n'
                else:
                    cmd = _apply_vars(opts.get('command', ''), rv)
                    if opts.get('auto_execute', False):
                        cmd += "\n"
            title = self._build_classifier_title(profile, cwd=cwd, resolved_vars=rv)
            kind  = 'ssh' if p_type == 'ssh' else 'local'
        else:
            cmd   = ''
            title = f"{_LOCAL_USER}@{_LOCAL_HOST}"
            kind  = 'local'

        spawn_kwargs = dict(cwd=p_cwd or None, env=p_env or None)

        if cmd and cmd.endswith('\n'):
            # Auto-execute: run silently via --init-file so nothing is echoed.
            tmp = tempfile.NamedTemporaryFile(
                mode='w', suffix='.sh', prefix='ttyga_', delete=False)
            tmp.write('[ -f ~/.bashrc ] && . ~/.bashrc\n')
            tmp.write(cmd.rstrip('\n') + '\n')
            tmp.close()
            def on_spawn(term):
                try:
                    os.unlink(tmp.name)
                except OSError:
                    pass
            terminal = self._new_terminal(
                on_spawn=on_spawn,
                shell_args=['--init-file', tmp.name],
                profile=profile,
                **spawn_kwargs)
        elif cmd:
            # Non-auto-execute: pre-fill the input line. Deferred 300 ms so
            # bash has finished sourcing .bashrc and readline has taken over
            # the PTY (kernel echo off) before we write — otherwise the bytes
            # echo twice (once via kernel, once via readline).
            def on_spawn(term):
                GLib.timeout_add(300, lambda: term.feed_child(cmd.encode('utf-8')) or False)
            terminal = self._new_terminal(on_spawn=on_spawn, profile=profile, **spawn_kwargs)
        else:
            terminal = self._new_terminal(profile=profile, **spawn_kwargs)
        tab_icon = self._profile_icon(profile)
        tab_box, tab_label, close_btn, dot = self._make_tab_label(title, kind=kind, icon=tab_icon)
        close_btn.connect("clicked", self._on_close_tab, terminal)
        if profile:
            color = profile.get('color', '')
            if color:
                dot_prov = Gtk.CssProvider()
                dot_prov.load_from_string(f"image {{ color: {color}; }}")
                dot.get_style_context().add_provider(
                    dot_prov, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)
        self._attach_tab_context_menu(tab_box, terminal)

        base_font = ((profile.get('terminal_font') if profile else None) or
                     self.settings.get('terminal_font', DEFAULT_SETTINGS['terminal_font']))

        tab_root = Gtk.Box()
        tab_root.set_hexpand(True)
        tab_root.set_vexpand(True)

        self.tabs[terminal] = {
            'label':     tab_label,
            'dot':       dot,
            'kind':      kind,
            'profile':   profile,
            'base_font': base_font,
            'tab_root':  tab_root,
            'spawn_dir': (str(Path(p_cwd).expanduser()) if p_cwd
                          else _implied_cwd(opts.get('command', ''))
                          or os.environ.get('HOME', '/')),
        }
        tab_root.append(self._make_pane_box(terminal, self.tabs[terminal]))
        self._update_pane_bars(tab_root)
        self._show_notebook()
        idx = self.notebook.append_page(tab_root, tab_box)
        self.notebook.set_tab_reorderable(tab_root, True)
        self.notebook.set_current_page(idx)
        if focus:
            # Schedule grab_focus at GLib.PRIORITY_LOW (300) so GDK's display
            # flush (PRIORITY_DEFAULT_IDLE = 200) always runs first.  After a
            # tab switch, VTE renders the new terminal and enqueues a burst of
            # Wayland protocol.  If grab_focus fires before GDK drains that
            # burst, the Wayland socket buffer can overflow and GDK aborts.
            # The outer idle (default priority) still preserves the original
            # "land after GTK's own focus-management work" guarantee; the inner
            # idle at PRIORITY_LOW lands after GDK's flush.
            GLib.idle_add(lambda: GLib.idle_add(
                lambda: terminal.grab_focus() and False,
                priority=GLib.PRIORITY_LOW) and False)

    def _pane_has_foreground_process(self, terminal):
        """True if the pty's foreground process group differs from the shell's
        own — i.e. some child process (not just the login shell) is running."""
        meta = self.tabs.get(terminal)
        pid = meta.get('child_pid') if meta else None
        if not pid:
            return False
        pty = terminal.get_pty()
        if pty is None:
            return False
        try:
            return os.tcgetpgrp(pty.get_fd()) != os.getpgid(pid)
        except (OSError, ProcessLookupError):
            return False

    def _any_process_running(self):
        return any(self._pane_has_foreground_process(t) for t in self.tabs)

    def _on_window_close_request(self, window):
        if self._any_process_running():
            self._confirm_close(
                "Quit ttyga?",
                "A process is still running. Quit anyway?",
                self._quit_after_confirm)
            return True
        self.save_state()
        return False

    def _quit_after_confirm(self):
        self.save_state()
        self.window.destroy()

    def _request_quit(self):
        """Ctrl+Q entry point — self.quit() bypasses close-request, so the
        running-process check has to happen here instead."""
        if self._any_process_running():
            self._confirm_close(
                "Quit ttyga?",
                "A process is still running. Quit anyway?",
                lambda: (self.save_state(), self.quit()))
            return
        self.save_state()
        self.quit()

    def _confirm_close(self, heading, body, on_confirm):
        """Ask before a destructive close. Calls on_confirm() only if the user
        picks the destructive response."""
        dialog = Adw.AlertDialog(heading=heading, body=body)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("close", "Close Anyway")
        dialog.set_response_appearance("close", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        def on_response(_dialog, response):
            if response == "close":
                on_confirm()
        dialog.connect("response", on_response)
        dialog.present(self.window)

    def _on_close_tab(self, btn, terminal):
        """Close the whole tab (all panes). Called by the tab close button."""
        tab_root = self.tabs.get(terminal, {}).get('tab_root')
        if tab_root is None:
            return
        running = any(self._pane_has_foreground_process(t)
                      for t, m in self.tabs.items() if m.get('tab_root') is tab_root)
        if running:
            self._confirm_close(
                "Close Tab?",
                "A process is still running in this tab. Close it anyway?",
                lambda: self._do_close_tab(tab_root))
            return
        self._do_close_tab(tab_root)

    def _do_close_tab(self, tab_root):
        page_num = self.notebook.page_num(tab_root)
        if page_num < 0:
            return
        last_tab = self.notebook.get_n_pages() <= 1
        for t in [t for t, m in self.tabs.items() if m.get('tab_root') is tab_root]:
            self.tabs.pop(t, None)
        if self._active_terminal not in self.tabs:
            self._active_terminal = None
        self.notebook.remove_page(page_num)
        if last_tab:
            # switch-page won't fire when there are no pages left, so clear the
            # sidebar highlight manually — _on_tab_switched never gets called.
            for btn in self.active_btns:
                btn.remove_css_class('active')
            self.active_btns = set()
            self.active_profile_keys = set()
            self._show_welcome()

    def _get_active_terminal(self):
        if self._active_terminal and self._active_terminal in self.tabs:
            page = self.notebook.get_current_page()
            if page >= 0:
                root = self.notebook.get_nth_page(page)
                if self.tabs[self._active_terminal].get('tab_root') is root:
                    return self._active_terminal
        page = self.notebook.get_current_page()
        if page < 0:
            return None
        return self._first_terminal_in(self.notebook.get_nth_page(page))

    # ----- split panes -----------------------------------------------------

    def _first_terminal_in(self, widget):
        if isinstance(widget, Vte.Terminal):
            return widget
        if isinstance(widget, Gtk.Paned):
            t = self._first_terminal_in(widget.get_start_child())
            return t or self._first_terminal_in(widget.get_end_child())
        if isinstance(widget, Gtk.Box):
            child = widget.get_first_child()
            while child:
                t = self._first_terminal_in(child)
                if t:
                    return t
                child = child.get_next_sibling()
        return None

    def _all_terminals_in(self, widget):
        if isinstance(widget, Vte.Terminal):
            yield widget
        elif isinstance(widget, Gtk.Paned):
            yield from self._all_terminals_in(widget.get_start_child())
            yield from self._all_terminals_in(widget.get_end_child())
        elif isinstance(widget, Gtk.Box):
            child = widget.get_first_child()
            while child:
                yield from self._all_terminals_in(child)
                child = child.get_next_sibling()

    def _split_pane(self, orientation):
        terminal = self._get_active_terminal()
        if terminal is None:
            return

        meta     = self.tabs[terminal]
        tab_root = meta['tab_root']
        profile  = meta.get('profile')
        kind     = meta.get('kind', 'local')

        if kind == 'ssh' and profile and profile.get('type') == 'ssh':
            # Re-connect to the same host in the new pane.
            opts         = profile.get('options', {})
            tmux_on      = bool(profile.get('tmux', False))
            tmux_session = (profile.get('tmux_session') or 'main').strip()
            tmux_cmd     = f"tmux attach -t {tmux_session} || tmux new -s {tmux_session}"
            user = opts.get('user', '').strip()
            host = opts.get('host', 'localhost').strip()
            port = str(opts.get('port', '')).strip()
            parts = ['ssh']
            if port:
                parts += ['-p', port]
            parts.append(f'{user}@{host}' if user else host)
            if tmux_on:
                parts += ['-t', f"'{tmux_cmd}'"]
            cmd = ' '.join(parts)
            tmp = tempfile.NamedTemporaryFile(
                mode='w', suffix='.sh', prefix='ttyga_', delete=False)
            tmp.write('[ -f ~/.bashrc ] && . ~/.bashrc\n')
            tmp.write(cmd + '\n')
            tmp.close()
            def on_spawn(term):
                try:
                    os.unlink(tmp.name)
                except OSError:
                    pass
            new_term = self._new_terminal(
                on_spawn=on_spawn,
                shell_args=['--init-file', tmp.name],
                profile=profile)
            new_spawn_dir = meta.get('spawn_dir', '')
        else:
            # Local: inherit the current working directory via OSC 7.
            cwd_uri = terminal.get_current_directory_uri()
            cwd = None
            if cwd_uri:
                try:
                    cwd = GLib.filename_from_uri(cwd_uri)[0] or None
                except Exception:
                    pass
            if not cwd:
                cwd = meta.get('spawn_dir') or os.environ.get('HOME')
            new_term = self._new_terminal(profile=profile, cwd=cwd)
            new_spawn_dir = cwd or meta.get('spawn_dir', '')

        paned = Gtk.Paned(orientation=orientation)
        paned.set_hexpand(True)
        paned.set_vexpand(True)

        base_font = meta['base_font']
        self.tabs[new_term] = {
            'label':     meta['label'],
            'dot':       meta['dot'],
            'kind':      meta['kind'],
            'profile':   profile,
            'base_font': base_font,
            'tab_root':  tab_root,
            'spawn_dir': new_spawn_dir,
        }
        wrapper     = terminal.get_parent()   # pane-box
        parent      = wrapper.get_parent()    # tab_root Box or Gtk.Paned
        new_wrapper = self._make_pane_box(new_term, self.tabs[new_term])
        if isinstance(parent, Gtk.Box):
            wrapper.unparent()
            paned.set_start_child(wrapper)
            paned.set_end_child(new_wrapper)
            parent.append(paned)
        elif isinstance(parent, Gtk.Paned):
            is_start = parent.get_start_child() is wrapper
            # Use set_*_child(None) — the idiomatic GTK4 way to detach Paned
            # children. Calling wrapper.unparent() from the child side re-enters
            # GtkPaned's internal remove vfunc, which calls gtk_widget_unparent()
            # on the same widget again, causing a segfault.
            if is_start:
                parent.set_start_child(None)
            else:
                parent.set_end_child(None)
            paned.set_start_child(wrapper)
            paned.set_end_child(new_wrapper)
            if is_start:
                parent.set_start_child(paned)
            else:
                parent.set_end_child(paned)
        self._active_terminal = new_term
        GLib.idle_add(lambda: self._update_pane_bars(tab_root) or False)
        GLib.idle_add(lambda: new_term.grab_focus() and False)

    def _close_pane(self, terminal):
        """Remove one pane. The tab survives; caller ensures it isn't the last pane."""
        if self._pane_has_foreground_process(terminal):
            self._confirm_close(
                "Close Pane?",
                "A process is still running in this pane. Close it anyway?",
                lambda: self._do_close_pane(terminal))
            return
        self._do_close_pane(terminal)

    def _do_close_pane(self, terminal):
        wrapper     = terminal.get_parent()   # pane-box
        parent      = wrapper.get_parent()    # Gtk.Paned
        if not isinstance(parent, Gtk.Paned):
            return

        is_start    = parent.get_start_child() is wrapper
        sibling     = parent.get_end_child() if is_start else parent.get_start_child()
        grandparent = parent.get_parent()
        is_start_in_gp = (isinstance(grandparent, Gtk.Paned) and
                          grandparent.get_start_child() is parent)

        # Use set_*_child(None) — the idiomatic GTK4 way to detach Paned children.
        # Remove the closing pane first, then the sibling (which we will re-parent).
        # GTK emits a cosmetic "Error finding last focus widget of GtkPaned" warning
        # when the second set_*_child(None) empties the paned; this is benign — the
        # paned is immediately discarded after this block, so the focus confusion
        # has no visible consequence.
        if is_start:
            parent.set_start_child(None)
            parent.set_end_child(None)
        else:
            parent.set_end_child(None)
            parent.set_start_child(None)

        if isinstance(grandparent, Gtk.Box):
            parent.unparent()
            grandparent.append(sibling)
        elif isinstance(grandparent, Gtk.Paned):
            if is_start_in_gp:
                grandparent.set_start_child(sibling)
            else:
                grandparent.set_end_child(sibling)

        self.tabs.pop(terminal, None)
        self._refresh_sidebar_highlight()
        self._active_terminal = self._first_terminal_in(sibling)
        if self._active_terminal:
            _at  = self._active_terminal
            _tr  = self.tabs[_at]['tab_root']
            GLib.idle_add(lambda: self._update_pane_bars(_tr) or False)
            GLib.idle_add(lambda: _at.grab_focus() and False)

    def _close_active_pane(self):
        terminal = self._get_active_terminal()
        if terminal is None:
            return
        tab_root = self.tabs[terminal]['tab_root']
        in_tab   = [t for t, m in self.tabs.items() if m.get('tab_root') is tab_root]
        if len(in_tab) <= 1:
            self._on_close_tab(None, terminal)
        else:
            self._close_pane(terminal)

    # ----- tab context menu (right-click) + merge ----------------------------

    def _attach_tab_context_menu(self, tab_box, terminal):
        """Attach a right-click gesture to a tab label that shows the context menu."""
        rc = Gtk.GestureClick(button=3)
        rc.connect('pressed', lambda g, _n, x, y, _t=terminal, _b=tab_box:
                   self._show_tab_context_menu(_t, _b, x, y))
        tab_box.add_controller(rc)

    def _show_tab_context_menu(self, terminal, tab_box, x, y):
        """Build and show the tab context menu at the click position."""
        meta    = self.tabs.get(terminal, {})
        profile = meta.get('profile')

        # Collect every other open tab (single-pane or already merged) as a
        # potential merge candidate — merging just nests another nested Paned
        # split into whichever side receives it.
        other_tabs = []
        seen_roots = {meta.get('tab_root')}
        for t, m in self.tabs.items():
            root = m.get('tab_root')
            if root in seen_roots:
                continue
            seen_roots.add(root)
            title = m.get('label', Gtk.Label()).get_label() or 'Untitled'
            other_tabs.append((t, title))

        # Nothing to show for a plain tab with no mergeable peers.
        if not profile and not other_tabs:
            return

        popover = Gtk.Popover()
        popover.set_parent(tab_box)
        popover.set_has_arrow(False)
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
        popover.set_pointing_to(rect)

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        vbox.set_margin_top(4)
        vbox.set_margin_bottom(4)

        if profile:
            edit_btn = Gtk.Button(label='Edit profile')
            edit_btn.add_css_class('flat')
            edit_btn.set_halign(Gtk.Align.FILL)
            edit_btn.connect('clicked', lambda _: (popover.popdown(), self._edit_profile(profile)))
            vbox.append(edit_btn)

        if other_tabs:
            if profile:
                sep = Gtk.Separator()
                sep.set_margin_top(4)
                sep.set_margin_bottom(4)
                vbox.append(sep)
            hdr = Gtk.Label(label='Merge with')
            hdr.add_css_class('caption')
            hdr.add_css_class('dim-label')
            hdr.set_halign(Gtk.Align.START)
            hdr.set_margin_start(8)
            hdr.set_margin_bottom(2)
            vbox.append(hdr)
            for other_term, title in other_tabs:
                btn = Gtk.Button(label=title)
                btn.add_css_class('flat')
                btn.set_halign(Gtk.Align.FILL)
                btn.connect('clicked', lambda _, ot=other_term:
                            (popover.popdown(), self._merge_tab_into(ot, terminal)))
                vbox.append(btn)

        popover.set_child(vbox)
        popover.popup()

    def _merge_tab_into(self, source_terminal, target_terminal):
        """Merge source's tab into target's tab as a horizontal split.

        The source's live terminal is reparented; its PTY session is uninterrupted.
        All terminals from source are re-keyed to live in target's tab.
        Source's (now empty) notebook page is then removed."""
        source_meta = self.tabs.get(source_terminal)
        target_meta = self.tabs.get(target_terminal)
        if not source_meta or not target_meta:
            return

        source_root = source_meta['tab_root']
        target_root = target_meta['tab_root']
        if source_root is target_root:
            return

        source_content = source_root.get_first_child()
        target_content = target_root.get_first_child()
        if source_content is None or target_content is None:
            return

        # Capture both titles before any widget surgery.
        target_title = target_meta['label'].get_label()
        source_title = source_meta['label'].get_label()

        # Detach both subtrees before any surgery — Python holds refs so
        # neither is destroyed by the unparent.
        source_root.remove(source_content)
        target_root.remove(target_content)

        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        paned.set_hexpand(True)
        paned.set_vexpand(True)
        paned.set_start_child(target_content)
        paned.set_end_child(source_content)
        target_root.append(paned)

        # Re-key all source terminals: they now live in target's tab.
        target_label = target_meta['label']
        target_dot   = target_meta['dot']
        for t in list(self._all_terminals_in(source_content)):
            m = self.tabs.get(t)
            if m:
                m['tab_root'] = target_root
                m['label']    = target_label
                m['dot']      = target_dot

        # Remove the now-empty source page. source_root is empty so GTK has
        # nothing to destroy inside it when it loses its last reference.
        page_num = self.notebook.page_num(source_root)
        if page_num >= 0:
            self.notebook.remove_page(page_num)

        self._update_pane_bars(target_root)
        first = self._first_terminal_in(target_content)
        if first:
            self._active_terminal = first

        # Defer all visual tab-label updates (title, icon, CSS classes) to
        # after GDK has flushed its Wayland protocol burst from the pane
        # surgery above.  Doing these synchronously triggers a Wayland
        # compositor deadlock on some compositors (whole desktop hangs).
        # Same double-idle / PRIORITY_LOW pattern as the grab_focus sites.
        def _update_merged_label():
            # Update tab label to reflect the merged state:
            #   title  → "target | source"
            #   icon   → split-pane icon (hides any profile icon widget)
            target_label.set_label(f"{target_title} | {source_title}")
            tab_box = self.notebook.get_tab_label(target_root)
            if tab_box:
                # Hide the profile icon widget (second child, between dot and label).
                child = tab_box.get_first_child()   # dot
                if child:
                    sibling = child.get_next_sibling()
                    if sibling and sibling is not target_label:
                        sibling.set_visible(False)   # hide profile icon
            target_dot.set_from_icon_name('ttyga-split-horiz-symbolic')
            target_dot.set_pixel_size(14)
            target_dot.remove_css_class('tab-dot-ssh')
            target_dot.remove_css_class('tab-dot-local')
            target_dot.add_css_class('tab-dot-local')   # dim/neutral colour
            target_dot.set_visible(True)
            self._refresh_sidebar_highlight()
            if first:
                first.grab_focus()
            return False

        GLib.idle_add(lambda: GLib.idle_add(
            _update_merged_label, priority=GLib.PRIORITY_LOW) and False)

    def _focus_adjacent_pane(self, direction):
        terminal = self._get_active_terminal()
        if terminal is None:
            return
        tab_root  = self.tabs[terminal]['tab_root']
        terminals = list(self._all_terminals_in(tab_root))
        if len(terminals) < 2:
            return
        idx = terminals.index(terminal) if terminal in terminals else 0
        terminals[(idx + direction) % len(terminals)].grab_focus()

    # ----- sidebar ---------------------------------------------------------

    def _update_clock(self):
        now = datetime.now()
        self._clock_time_text = now.strftime('%H:%M:%S')
        self._clock_date_text = now.strftime('%a %b %d %Y')
        self.clock_time_area.queue_draw()
        self.clock_date_area.queue_draw()
        return True

    def _draw_fitted_text(self, cr, width, height, text, color, min_px, max_px, bold=False):
        """Render text as large as (width, height) allows.

        A Gtk.Label can't do this — its font size is fixed at construction,
        not recomputed as the user drags the sidebar wider or narrower.
        Drawing it ourselves means we always know our own allocated size
        and can scale a Pango layout to fit it exactly."""
        if width <= 0 or height <= 0 or not text:
            return

        layout = PangoCairo.create_layout(cr)
        layout.set_text(text, -1)

        # Measure at a reference size, then scale proportionally to fit the
        # available width (with a little breathing room on each side).
        ref_px = 100
        desc = Pango.FontDescription()
        desc.set_family('monospace')
        if bold:
            desc.set_weight(Pango.Weight.BOLD)
        desc.set_absolute_size(ref_px * Pango.SCALE)
        layout.set_font_description(desc)
        ref_w, ref_h = layout.get_pixel_size()
        if ref_w <= 0:
            return

        pad = 8
        font_px = ref_px * (width - 2 * pad) / ref_w
        font_px = min(font_px, ref_px * height / ref_h)
        font_px = max(min_px, min(max_px, font_px))

        desc.set_absolute_size(font_px * Pango.SCALE)
        layout.set_font_description(desc)
        w, h = layout.get_pixel_size()

        cr.set_source_rgba(color.red, color.green, color.blue, color.alpha)
        cr.move_to((width - w) / 2, (height - h) / 2)
        PangoCairo.show_layout(cr, layout)

    def _draw_clock_time(self, area, cr, width, height):
        theme = self.settings.get('color_scheme', 'dark')
        fg = _rgba(THEMES.get(theme, THEMES['dark'])['fg'])
        self._draw_fitted_text(cr, width, height, self._clock_time_text, fg,
                                CLOCK_TIME_MIN_PX, CLOCK_TIME_MAX_PX, bold=True)

    def _draw_clock_date(self, area, cr, width, height):
        theme = self.settings.get('color_scheme', 'dark')
        fg_dim = _rgba(THEMES.get(theme, THEMES['dark'])['fg_dim'])
        self._draw_fitted_text(cr, width, height, self._clock_date_text, fg_dim,
                                CLOCK_DATE_MIN_PX, CLOCK_DATE_MAX_PX)

    def _build_sidebar(self, state=None):
        while (child := self.sidebar_box.get_first_child()):
            self.sidebar_box.remove(child)
        self.expanders.clear()
        self.active_btns = set()
        self._profile_buttons.clear()
        self._flat_groups.clear()

        if state is None:
            state = self.load_state()

        config = self.load_resolved_config()
        _, group_icons = _parse_groups(config)
        groups = {}
        for p in config.get('profiles', []):
            if p.get('hidden'):
                continue
            g_name = p.get('group', 'General')
            groups.setdefault(g_name, []).append(p)

        layout = self.settings.get('sidebar_layout', 'expanders')
        saved_expands = state.get("expanded_groups", {})

        for g_name, profiles in groups.items():
            g_icon = group_icons.get(g_name, '')
            if layout == 'flat':
                self._build_flat_group(g_name, profiles, g_icon)
            else:
                self._build_expander_group(g_name, profiles,
                                           saved_expands.get(g_name, True), g_icon)

    def _build_expander_group(self, g_name, profiles, expanded, g_icon=''):
        expander = Gtk.Expander()
        lbl = Gtk.Label(label=g_name.upper(), xalign=0)
        lbl.add_css_class('group-header')
        expander.set_label_widget(lbl)
        expander.set_expanded(expanded)
        self.expanders[g_name] = expander

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        vbox.set_margin_start(10)
        vbox.set_margin_bottom(6)
        for p in profiles:
            vbox.append(self.create_profile_button(p, g_icon))
        expander.set_child(vbox)
        self.sidebar_box.append(expander)

    def _build_flat_group(self, g_name, profiles, g_icon=''):
        header = Gtk.Label(label=g_name.upper(), xalign=0)
        header.add_css_class('group-header')
        self.sidebar_box.append(header)

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        for p in profiles:
            vbox.append(self.create_profile_button(p, g_icon))
        self.sidebar_box.append(vbox)
        self._flat_groups.append((header, vbox, g_name))

    def reload_profiles(self):
        self.search_entry.set_text('')
        current_state = {
            "expanded_groups": {
                name: exp.get_expanded() for name, exp in self.expanders.items()
            },
            "sidebar_width_fraction": self.split_view.get_sidebar_width_fraction(),
        }
        self._build_sidebar(current_state)

    def _on_search_changed(self, entry):
        query = entry.get_text().strip().lower()
        for btn, p in self._profile_buttons:
            btn.set_visible(not query or query in p.get('name', '').lower())
        for g_name, exp in self.expanders.items():
            exp.set_visible(any(
                p.get('group', 'General') == g_name and btn.get_visible()
                for btn, p in self._profile_buttons
            ))
        for header, vbox, g_name in self._flat_groups:
            visible = any(
                p.get('group', 'General') == g_name and btn.get_visible()
                for btn, p in self._profile_buttons
            )
            header.set_visible(visible)
            vbox.set_visible(visible)

    def _on_search_stop(self, entry):
        entry.set_text('')
        terminal = self._get_active_terminal()
        if terminal:
            terminal.grab_focus()

    def create_profile_button(self, p, group_icon=''):
        btn = Gtk.Button()
        btn.add_css_class('flat')
        btn.add_css_class('profile-row')
        content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        content.set_margin_top(2)
        content.set_margin_bottom(2)
        content.set_margin_start(2)
        content.set_margin_end(2)

        icon_name = p.get('icon') or group_icon or 'utilities-terminal-symbolic'
        if _is_gtk_icon(icon_name):
            icon_widget = Gtk.Image.new_from_icon_name(icon_name)
            icon_widget.set_pixel_size(PROFILE_ICON_PX)
        elif Path(icon_name).expanduser().is_file():
            icon_widget = Gtk.Image.new_from_file(str(Path(icon_name).expanduser()))
            icon_widget.set_pixel_size(PROFILE_ICON_PX)
        else:
            icon_widget = Gtk.Label()
            icon_widget.set_markup(
                f'<span size="large">{GLib.markup_escape_text(icon_name)}</span>')
            icon_widget.set_size_request(PROFILE_ICON_PX, PROFILE_ICON_PX)
            icon_widget.set_xalign(0.5)
            icon_widget.set_yalign(0.5)
        content.append(icon_widget)

        name_label = Gtk.Label(label=p.get('name'), xalign=0)
        name_label.set_hexpand(True)
        name_label.set_ellipsize(Pango.EllipsizeMode.END)
        content.append(name_label)

        if p.get('type') == 'clippet':
            tag = Gtk.Label(label='$')
            tag.add_css_class('dim-label')
            tag.add_css_class('caption')
            content.append(tag)

        btn.set_child(content)
        btn.connect("clicked", self.on_profile_clicked, p)

        color = p.get('color', '')
        if color:
            btn.set_name(f"pb{id(btn)}")
            prov = Gtk.CssProvider()
            prov.load_from_string(
                f"#pb{id(btn)} {{ border-left: 3px solid {color}; }}")
            btn.get_style_context().add_provider(
                prov, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)

        rc = Gtk.GestureClick(button=3)
        rc.connect('pressed', lambda g, _n, _x, _y, _p=p: self._edit_profile(_p))
        btn.add_controller(rc)

        self._profile_buttons.append((btn, p))
        if (p.get('name'), p.get('group', 'General')) in self.active_profile_keys:
            btn.add_css_class('active')
            self.active_btns.add(btn)

        return btn

    # ----- classifier ------------------------------------------------------

    def _build_classifier_title(self, p, cwd=None, resolved_vars=None):
        p_type = p.get('type', 'clippet')
        opts = p.get('options', {})
        classifier = p.get('classifier')

        if classifier and 'title' in classifier:
            template = classifier['title']
        elif p_type == 'ssh':
            template = "@user@@host"
        else:
            return p.get('name', p_type)

        runtime = {
            'user': _LOCAL_USER,
            'host': _LOCAL_HOST,
        }
        if resolved_vars:
            runtime.update(resolved_vars)
        if cwd:
            runtime['cwd'] = cwd
            runtime['dir'] = os.path.basename(cwd.rstrip('/'))

        def _sub(m):
            key = m.group(1)
            return str(opts.get(key, runtime.get(key, m.group(0))))

        return re.sub(r'@(\w+)', _sub, template)

    # ----- input handlers --------------------------------------------------

    def on_window_key_pressed(self, controller, keyval, keycode, state):
        ctrl  = Gdk.ModifierType.CONTROL_MASK
        shift = Gdk.ModifierType.SHIFT_MASK
        alt   = Gdk.ModifierType.ALT_MASK

        has_ctrl  = bool(state & ctrl)
        has_shift = bool(state & shift)
        has_alt   = bool(state & alt)

        # F11 — toggle full-screen
        if keyval == Gdk.KEY_F11 and not (has_ctrl or has_shift or has_alt):
            if self.window.is_fullscreen():
                self.window.unfullscreen()
            else:
                self.window.fullscreen()
            return True

        if not has_ctrl:
            return False

        # Ctrl+Q — quit
        if not has_shift and not has_alt and keyval in (Gdk.KEY_Q, Gdk.KEY_q):
            self._request_quit()
            return True

        # Ctrl+Shift+T — new tab
        if has_shift and not has_alt and keyval in (Gdk.KEY_T, Gdk.KEY_t):
            self.add_tab()
            return True

        # Ctrl+Shift+W — close active pane (or tab if last pane)
        if has_shift and not has_alt and keyval in (Gdk.KEY_W, Gdk.KEY_w):
            self._close_active_pane()
            return True

        # Ctrl+Shift+E — split right (side by side)
        if has_shift and not has_alt and keyval in (Gdk.KEY_E, Gdk.KEY_e):
            self._split_pane(Gtk.Orientation.HORIZONTAL)
            return True

        # Ctrl+Shift+D — split down (top / bottom)
        if has_shift and not has_alt and keyval in (Gdk.KEY_D, Gdk.KEY_d):
            self._split_pane(Gtk.Orientation.VERTICAL)
            return True

        # Ctrl+Shift+B — toggle sidebar
        if has_shift and not has_alt and keyval in (Gdk.KEY_B, Gdk.KEY_b):
            self.split_view.set_show_sidebar(not self.split_view.get_show_sidebar())
            return True

        # Ctrl+F — focus sidebar search
        if not has_shift and not has_alt and keyval in (Gdk.KEY_F, Gdk.KEY_f):
            self.search_entry.grab_focus()
            return True

        # Ctrl+Tab — next tab
        if not has_shift and not has_alt and keyval == Gdk.KEY_Tab:
            n = self.notebook.get_n_pages()
            if n > 1:
                self.notebook.set_current_page(
                    (self.notebook.get_current_page() + 1) % n)
            return True

        # Ctrl+Shift+Tab — previous tab
        if has_shift and not has_alt and keyval in (Gdk.KEY_Tab, Gdk.KEY_ISO_Left_Tab):
            n = self.notebook.get_n_pages()
            if n > 1:
                self.notebook.set_current_page(
                    (self.notebook.get_current_page() - 1) % n)
            return True

        # Ctrl+Alt+R — reload profiles
        if has_alt and not has_shift and keyval in (Gdk.KEY_R, Gdk.KEY_r):
            self.reload_profiles()
            return True

        # Ctrl+Alt+Arrow — focus adjacent pane
        if has_alt and not has_shift and keyval in (Gdk.KEY_Left, Gdk.KEY_Right,
                                                     Gdk.KEY_Up, Gdk.KEY_Down):
            self._focus_adjacent_pane(
                -1 if keyval in (Gdk.KEY_Left, Gdk.KEY_Up) else 1)
            return True

        # Ctrl++ / Ctrl+= — zoom in; Ctrl+- — zoom out; Ctrl+0 — reset zoom
        if not has_alt and keyval in (Gdk.KEY_plus, Gdk.KEY_equal, Gdk.KEY_KP_Add):
            self._zoom_font(1)
            return True
        if not has_alt and keyval in (Gdk.KEY_minus, Gdk.KEY_KP_Subtract):
            self._zoom_font(-1)
            return True
        if not has_alt and keyval in (Gdk.KEY_0, Gdk.KEY_KP_0):
            self._font_zoom_delta = 0
            for term, meta in self.tabs.items():
                term.set_font(Pango.FontDescription.from_string(meta['base_font']))
            return True

        return False

    def on_key_pressed(self, controller, keyval, keycode, state):
        mask = Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK
        if (state & mask) == mask:
            terminal = self._get_active_terminal()
            if terminal:
                if keyval in (Gdk.KEY_C, Gdk.KEY_c):
                    terminal.copy_clipboard_format(Vte.Format.TEXT)
                    return True
                if keyval in (Gdk.KEY_V, Gdk.KEY_v):
                    terminal.paste_clipboard()
                    return True
        return False

    def on_profile_clicked(self, widget, p):
        for btn in self.active_btns:
            if btn is not widget:
                btn.remove_css_class('active')
        widget.add_css_class('active')
        self.active_btns = {widget}
        self.active_profile_keys = {(p.get('name'), p.get('group', 'General'))}

        active = self._get_active_terminal()
        cwd_uri = active.get_current_directory_uri() if active else None
        cwd = GLib.filename_from_uri(cwd_uri)[0] if cwd_uri else None

        variables = p.get('variables') or {}
        if variables:
            history_key = f"{p.get('name', '')}|{p.get('group', 'General')}"
            recent = self._var_history.get(history_key, {})

            def on_launch(resolved_vars):
                self._var_history[history_key] = resolved_vars
                self._launch_profile(p, active, cwd, resolved_vars)

            VariablePromptDialog(self, p, recent, on_launch).present()
        else:
            self._launch_profile(p, active, cwd, {})

    def _launch_profile(self, p, active, cwd, resolved_vars):
        opts = p.get('options', {})
        if p.get('type') == 'clippet' and opts.get('in_place', False):
            if active:
                cmd = _apply_vars(opts.get('command', ''), resolved_vars)
                if opts.get('auto_execute', False):
                    cmd += '\n'
                if cmd:
                    active.feed_child(cmd.encode('utf-8'))
                meta = self.tabs.get(active)
                if meta:
                    meta['label'].set_label(
                        self._build_classifier_title(p, cwd=cwd, resolved_vars=resolved_vars))
        else:
            self.add_tab(profile=p, cwd=cwd, resolved_vars=resolved_vars)


if __name__ == "__main__":
    import argparse, atexit, shutil, tempfile
    parser = argparse.ArgumentParser(prog='ttyga', description=APP_COMMENTS)
    parser.add_argument('--dev', action='store_true',
                        help='Run with an isolated temp config dir and DEBUG logging. '
                             'Bypasses single-instance guard. Settings, profiles, and '
                             'saved state in ~/.config/ttyga are left untouched.')
    args = parser.parse_args()

    if args.dev:
        _dev_tmp   = tempfile.TemporaryDirectory(prefix='ttyga_dev_')
        _dev_tmpdir = _dev_tmp.name
        CONFIG_DIR    = Path(_dev_tmpdir)
        CONFIG_FILE   = CONFIG_DIR / "profiles.yaml"
        SETTINGS_FILE = CONFIG_DIR / "settings.yaml"
        STATE_FILE    = CONFIG_DIR / "app_state.json"
        LEGACY_CONFIG = Path('/dev/null')   # prevent source-dir profiles.yaml fallback
        import faulthandler
        faulthandler.enable()   # dump traceback on SIGSEGV / SIGFPE / SIGABRT
        logging.basicConfig(level=logging.DEBUG,
                            format='%(levelname)s %(name)s: %(message)s')
        print(f"[dev] config dir: {_dev_tmpdir}", file=sys.stderr, flush=True)
        app = DevFrame()
        app.run(None)
        _dev_tmp.cleanup()
    else:
        logging.basicConfig(level=logging.WARNING)
        app = DevFrame()
        try:
            app.register()
        except GLib.Error:
            pass  # D-Bus unavailable; proceed and let GIO sort it out
        if app.get_is_remote():
            print("ttyga: already running", file=sys.stderr)
            sys.exit(1)
        app.run(None)
