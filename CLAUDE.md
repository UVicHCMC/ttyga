# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running and installing

```bash
# Development — run directly without installing
python3 ttyga.py

# Install to ~/.local/bin/ttyga (also installs icon, .desktop file, seeds config)
./install.sh

# Preview what install would do
./install.sh --dry-run

# Remove installation
./install.sh --uninstall

# Package a release zip (uses APP_VERSION from ttyga.py)
./package.sh
```

There is no linter and no build step. The entire app is `ttyga.py`.

Bump `APP_VERSION` in `ttyga.py` whenever meaningful changes land — don't ask, just do it.

## Tests

No test runner and no CI — two standalone scripts, run directly:

```bash
python3 tests/test_bg_image_css.py     # headless, <1s
python3 tests/test_pane_margins.py     # opens a window, ~5s
```

They are **point-in-time**, written alongside the features they cover, and coupled to private methods (`_split_pane`, `_update_pane_bars`, `_all_terminals_in`) — so they will break when those internals move. That is intended: they exist to catch a silent regression in two fragile seams, not to be a suite anyone maintains for its own sake. If one goes red, the honest options are fix it or delete it; do not leave it failing.

`test_pane_margins.py` doubles as the **template for any new driver script** — its docstring records the three setup traps (`NON_UNIQUE`, `faulthandler`, deferred assertions) that have each cost a session to rediscover. Read it before writing a new one.

Appearance cannot be verified from a script here: screenshots come back stale (Mutter has no wlr-screencopy; `gnome-screenshot` returned ten byte-identical frames over six seconds, clock seconds included). Assert on widget state and CSS classes, then ask Greg to look.

## Architecture

ttyga is a **single-file GTK4/Adwaita terminal emulator**. Everything lives in `ttyga.py`. The entry point is `DevFrame(Adw.Application)`, instantiated at the bottom.

### Classes

| Class | Role |
|---|---|
| `DevFrame` | Main application class. Owns all state, builds the window, manages tabs and panes. |
| `EditorWindow` | Modal profile editor (`Adw.Window`). Edits in-memory; saves to `profiles.yaml` on Save. |
| `PreferencesWindow` | `Adw.PreferencesWindow` for global settings. Calls `app.set_setting()` which persists and applies side-effects immediately. |
| `HelpWindow` | Renders `TTYGA.md` as styled rich text in a scrolled `Gtk.TextView`. |
| `IconPickerDialog` | Searchable grid of curated XDG icons + a free-text / Unicode entry. |
| `VariablePromptDialog` | Shown when a profile has `variables:` — collects runtime values before launch. |

### Config and state

All config lives under `~/.config/ttyga/`:

- `profiles.yaml` — profile definitions; loaded by `load_config()` / `load_resolved_config()`
- `settings.yaml` — user preferences; `_load_settings()` merges against `DEFAULT_SETTINGS`
- `app_state.json` — sidebar width, expander state, open tabs (written on every close via `save_state()`)

`DEFAULT_SETTINGS` at the top of the file documents every recognised key.

### Tab and pane model

The widget hierarchy inside each notebook page is:

```
tab_root (Gtk.Box, hexpand+vexpand)
  └─ Gtk.Paned (optional, nested arbitrarily)
       ├─ pane-box (Gtk.Box, css class "pane-box")
       │    ├─ pane-bar  (Gtk.Box, css class "pane-bar" — hidden when only one pane)
       │    └─ Vte.Terminal
       └─ pane-box / Gtk.Paned …
```

`self.tabs` is the core runtime dict: `{Vte.Terminal → metadata}`. Every terminal — across all tabs and panes — has one entry. Metadata keys:

- `label`, `dot` — shared `Gtk.Label` / `Gtk.Image` in the notebook tab label (shared across all panes of the same tab)
- `kind` — `'ssh'` or `'local'`
- `profile` — the resolved profile dict, or `None` for plain tabs
- `base_font` — Pango font string used for zoom calculations
- `tab_root` — the top-level `Gtk.Box` for this tab (used to find siblings)
- `spawn_dir` — the directory the terminal was launched from (fallback for cwd when OSC 7 is unavailable)
- `pane_bar` — the `Gtk.Box` header strip above the terminal; hidden when only one pane

`_update_pane_bars(tab_root)` is the single hook run at every structural change (restore, both `add_tab` paths, split, close, merge). It shows/hides the pane bars **and** calls `_update_pane_margins()` per terminal.

### The pane gutter (`PANE_GUTTER`)

A `Gtk.Paned` separator's drag grab zone extends *into* the adjacent pane and claims the pointer sequence before the terminal's selection gesture sees it — so the first character of a line next to a separator can't be selected. The fix is a 12px GTK margin on the terminal, applied **only to edges that actually abut a separator**: `_update_pane_margins()` walks every ancestor `Gtk.Paned` and sets `margin_start` / `margin_end` / `margin_bottom` accordingly. A single-pane tab gets none. There is never a top margin — the pane-bar buffers that edge.

Two things not to re-derive:
- **CSS padding does not work.** VTE 0.76 shifts glyphs but still maps mouse coordinates as if the padding weren't there, so clicks land on the wrong cell. A margin works because margin space is outside the widget allocation. Do not retry CSS.
- Walking *all* ancestors, not just the immediate parent, is exact — a leaf that doesn't reach a subtree's boundary only fails to because a nearer separator already earned it the same margin.

### Key methods in DevFrame

| Method | What it does |
|---|---|
| `add_tab()` | Creates a new notebook tab. Auto-execute profiles use a bash `--init-file` temp script; non-auto-execute profiles use `GLib.timeout_add(300, feed_child)` so bash has initialised readline before input arrives. |
| `_new_terminal()` | Constructs and spawns a `Vte.Terminal` via `spawn_async`. |
| `_split_pane()` | Splits the focused pane. SSH panes re-connect via `--init-file`; local panes inherit cwd from OSC 7 or `spawn_dir`. Replaces parent `Gtk.Box` or `Gtk.Paned` child with a new `Gtk.Paned`. |
| `_close_pane()` | Removes one pane, promotes its sibling up the tree. |
| `_on_child_exited()` | Fires when a pane's shell exits. Does nothing but `GLib.idle_add(_reap_exited_pane)` — reaping from inside VTE's own emission would unparent a `Gtk.Paned` child mid-teardown. `_reap_exited_pane()` re-checks `self.tabs` (a user close often wins the race) and honours the `close_on_exit` setting (`always` / `clean` / `never`). |
| `_serialise_tab()` / `_serialise_pane()` | Recursively serialises the pane tree to a JSON-friendly dict for `app_state.json`. |
| `_restore_tab()` / `_build_pane_tree()` | Reconstructs a tab from a serialised layout. |
| `_build_sidebar()` | Rebuilds the entire sidebar from the current profile list. |
| `reload_profiles()` | Re-reads `profiles.yaml` and rebuilds the sidebar without restarting. |
| `set_setting()` | Updates one setting, applies its immediate side-effect (theme, font, scrollback, etc.), and persists. |
| `build_css()` | Generates the full CSS string for the current theme. Applied as a single `Gtk.CssProvider` at the display level; swapped out on theme change. |

### Paned child detachment — critical GTK4 gotcha

To remove a child from `Gtk.Paned`, always use:
```python
paned.set_start_child(None)   # or set_end_child(None)
```
**Never** call `child.unparent()` on a Paned child. GTK4's internal remove vfunc calls `gtk_widget_unparent()` again, causing a segfault.

### Focus grab timing

A single `GLib.idle_add(lambda: widget.grab_focus())` races with GTK's own focus-management work after a tab switch. Use a nested double idle to reliably land after GTK finishes:

```python
GLib.idle_add(lambda: GLib.idle_add(lambda: terminal.grab_focus() and False,
                                    priority=GLib.PRIORITY_LOW) and False)
```

**Critical**: use `and False`, not `or False`. `GLib.idle_add()` returns a non-zero source ID; `source_id or False` = `source_id` (truthy), so the outer idle re-fires forever. `Gtk.Widget.grab_focus()` returns `True` on success; `True or False` = `True`, so the inner also re-fires forever. Either bug alone causes unbounded GLib GSource accumulation (28 GB RSS observed over ~8 h). `and False` short-circuits to `False` in both cases, correctly removing the idle.

### Profile resolution

`_resolve_inheritance()` processes `extends:` chains at load time and returns a flat resolved list. Dict fields (`options`, `env`, `variables`) are deep-merged; the child wins on scalar fields. Cycles are detected and logged. `load_resolved_config()` is the entry point that returns fully resolved profiles.

### OSC 7 and cwd tracking

The current working directory is tracked via the OSC 7 escape sequence emitted by the shell. `terminal.get_current_directory_uri()` retrieves it. When OSC 7 is unavailable (e.g. a program like `claude` is blocking the prompt), `_implied_cwd()` extracts a directory from a leading `cd PATH` in the profile command. `spawn_dir` is the final fallback.

### Themes

Three built-in themes: `light`, `dark`, `nord`. All colour values are in the `THEMES` dict. `build_css()` and `_vte_palette()` consume them. Per-profile `color_scheme` overrides the global theme for that terminal.

### Icon handling

Icons can be XDG names (`network-server-symbolic`), file paths, or single Unicode characters. `_is_gtk_icon()` distinguishes XDG names (pure ASCII lowercase/digits/hyphens). `_profile_icon()` in `DevFrame` resolves a profile's effective icon (own icon → group icon → None).

## Project state

This project maintains `STATE.md` at the root. Read it at the start of any
substantive session. Before finishing a session in which you changed code, made
a plan, or learned something that changes what comes next, update `STATE.md`:
revise `next`, `blocked_on`, and `updated`, and prepend a dated `##` entry
describing what changed and what is now outstanding. Keep entries newest-first.
Do not commit `STATE.md` unless asked.
