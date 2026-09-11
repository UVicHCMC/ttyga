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

No test runner and no CI — seven standalone scripts, run directly. Keep this list in step with `tests/`; it has gone stale before, and a script nobody knows about is a script nobody runs:

```bash
python3 tests/test_bg_image_css.py        # headless, <1s
python3 tests/test_stopwatch.py           # opens a window, ~4s
python3 tests/test_pane_margins.py        # opens a window, ~5s
python3 tests/test_sidebar_switch.py      # opens a window, ~7s
python3 tests/test_sidebar_open_marks.py  # opens a window, ~7s
python3 tests/test_mouse_grab_hint.py     # opens a window, ~5s
python3 tests/test_quota_timer.py         # opens a window, ~11s
```

They are **point-in-time**, written alongside the features they cover, and coupled to private methods (`_split_pane`, `_update_pane_bars`, `_all_terminals_in`, `_do_close_tab`, `_profile_buttons`, `_on_terminal_mouse_event`, `_load_quota`, `_tick_quota`) — so they will break when those internals move. That is intended: they exist to catch a silent regression in a few fragile seams, not to be a suite anyone maintains for its own sake. If one goes red, the honest options are fix it or delete it; do not leave it failing.

`test_pane_margins.py` doubles as the **template for any new driver script** — its docstring records the three setup traps (`NON_UNIQUE`, `faulthandler`, deferred assertions) that have each cost a session to rediscover. Read it before writing a new one, along with a fourth trap recorded in `test_quota_timer.py`: a deferred assertion must land **before the next step runs**. The template's inter-step gap is a fixed 700 ms, so a check deferred longer than that (a `Gio.FileMonitor` needs ~1.4 s — its `rate-limit` defaults to 800 ms) is overwritten by the following step and reports a failure that is not real. `test_quota_timer.py` widens the gap from the defer itself rather than leaving two delays to be kept in step by hand.

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
- `select_hint_at` — monotonic time the mouse-grab hint last fired for this pane (absent until it has); lives here so it is reaped with the pane

`_update_pane_bars(tab_root)` is the single hook run at every structural change (restore, both `add_tab` paths, split, close, merge). It shows/hides the pane bars **and** calls `_update_pane_margins()` per terminal.

### The pane gutter (`PANE_GUTTER`)

A `Gtk.Paned` separator's drag grab zone extends *into* the adjacent pane and claims the pointer sequence before the terminal's selection gesture sees it — so the first character of a line next to a separator can't be selected. The fix is a 12px GTK margin on the terminal, applied **only to edges that actually abut a separator**: `_update_pane_margins()` walks every ancestor `Gtk.Paned` and sets `margin_start` / `margin_end` / `margin_bottom` accordingly. A single-pane tab gets none. There is never a top margin — the pane-bar buffers that edge.

Two things not to re-derive:
- **CSS padding does not work.** VTE 0.76 shifts glyphs but still maps mouse coordinates as if the padding weren't there, so clicks land on the wrong cell. A margin works because margin space is outside the widget allocation. Do not retry CSS.
- Walking *all* ancestors, not just the immediate parent, is exact — a leaf that doesn't reach a subtree's boundary only fails to because a nearer separator already earned it the same margin.

### Sidebar running-state marks

Two CSS classes on `.profile-row`, both recomputed from `self.tabs` by
`_update_sidebar_highlight(tab_root)`:

- `.active` — the profile is open in the tab **on screen** (a merged tab can
  host several at once). Solid accent fill.
- `.open` — the profile is running in some **other** tab. Label and icon
  tinted `row_open_fg`, no fill. Mutually exclusive with `.active`.

`_apply_row_state()` puts them on one row from `active_profile_keys` /
`open_profile_keys`, so a sidebar rebuilt mid-session (`reload_profiles()`)
comes back marked.

The marks normally ride on `switch-page`. Three paths change what is current
without emitting it, and each recomputes by hand — break one and a stale tint
sticks forever:

- `_do_close_tab()` closing a **background** page (the current child never
  changes) → `_refresh_sidebar_highlight()`
- `_do_close_tab()` closing the **last** page (no page left to switch to) →
  clears both sets inline
- `_switch_to_profile_tab()` landing on the page already current

`.attention` (the BEL pulse) overrides both. An `.open` row hands its tint
back to the theme `fg` for the pulse's duration — `row_open_fg` over the
`term_warn` peak is the one illegible pairing.

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

### Mouse-reporting programs and the Shift override

A program that enables xterm mouse reporting (claude, tmux with `mouse on`,
htop) takes every button event, so **dragging selects nothing and middle-click
does not paste** — there is no clipboard bug to chase, the selection simply
never happens. VTE's own escape hatch is to hold **Shift**, which makes it
handle the event locally instead of forwarding it.

Two consequences, both already handled:

- **Every terminal key controller must be CAPTURE phase.** VTE installs its own
  key controller at BUBBLE, and capture always runs first, so a bubble-phase
  handler of ours loses the key to the child. This silently killed
  Ctrl+Shift+C/V inside any full-screen program until 0.6.61.
  `tests/test_mouse_grab_hint.py` asserts the phases.
- `_on_terminal_mouse_event` notices a button-1 drag longer than
  `SELECT_HINT_MIN_PX` that left no selection, and toasts the Shift hint
  (rate-limited by `SELECT_HINT_COOLDOWN`, stored per pane in `self.tabs` so a
  closed pane takes its state with it). VTE 0.76 exposes **no** way to ask
  whether the child enabled mouse reporting — no property, no signal — and
  ttyga never sees the output stream, so the state cannot be read; it is
  inferred from the failed drag instead. Don't go looking for the API again.

The probe is a `Gtk.EventControllerLegacy` at CAPTURE that always returns
`False`. It must **not** become a `Gtk.Gesture`: a gesture joins VTE's gesture
grouping and can claim the pointer sequence — the same failure `PANE_GUTTER`
exists to work around.

### The usage-limit countdown

When a Claude Code session hits its usage limit, the sidebar clock counts down
to the reset. The interface between the two programs is one file,
`~/.config/ttyga/quota.json`, written by `hooks/ttyga-quota-hook.py` (installed
as `~/.local/bin/ttyga-quota-hook`) and watched by ttyga. `QUOTA_FILE` is
rebound under `--dev` alongside the other config paths — miss that and a dev run
reads, and the hook writes, the real countdown.

Four things established by reading the Claude Code binary. Do not re-derive
them:

- **The `Notification` hook never fires for a usage limit.** Its
  `notification_type` values are a closed set (`permission_prompt`,
  `idle_prompt`, `agent_completed`, `auth_success`, … plus
  `quota_auto_resume_*`), and hitting the wall emits no notification at all —
  the "Credit balance too low" line is a rendered TUI message, not an event.
- **`StopFailure` is the hook that fires**, carrying `error`
  (`rate_limit` or `credit_balance_low`), `error_details` and
  `last_assistant_message`. `Stop` is not a substitute: the API-error branch
  returns before the normal turn-end `Stop` dispatch is reached.
- **`error: "rate_limit"` alone is not a usage limit** — capacity errors ("We
  are experiencing high demand for …") set it too. The hook discriminates on
  the structured data, not that field.
- **The reset time is `quotaLimits.resetsAt` on the assistant message in the
  transcript** (epoch seconds), which is why the hook is handed
  `transcript_path` and tails it. It is *not* in the hook payload. Never parse
  the rendered text: `"your session limit resets 2:40pm (America/Vancouver)"`
  is 12-hour local with no date.

Two rules inside the hook's transcript scan, both load-bearing: only a
`resetsAt` in the **future** is accepted, and the scan retries. The hook and
the transcript write are not ordered against each other, so on the first pass
the new error may not be on disk, and a long session holds earlier limits that
have since reset — without the guard the scan returns one of those and ttyga
counts down to a time that has already gone by.

On the ttyga side:

- The limit is **account-wide**, so this is one countdown for the app, not
  per-tab state in `self.tabs`. Every claude tab is blocked at the same moment.
- `self._quota_monitor` **must** stay referenced. A `Gio.FileMonitor` that goes
  out of scope is finalised and silently stops delivering `changed` — no error,
  just a countdown that never appears. `QUOTA_POLL_S` re-reads the file anyway,
  because a monitor can also miss a rename-replace on some filesystems.
- `_clock_mode` is now `'clock' | 'stopwatch' | 'quota'`, and the mode names are
  deliberately the same as the `Gtk.Stack` page names in
  `_build_stopwatch_controls()`.
- The alarm pulses by **alternating colour on the existing 1 s clock tick**.
  There is no CSS to animate here: both clock lines are `Gtk.DrawingArea`s
  (see the comment above `_draw_clock_time`).
- A countdown auto-presents **once** per `resets_at` (`_quota_shown`), so
  switching back to the clock stands. A *running* stopwatch is never taken off
  screen for a new countdown, but the expiry alarm does take over — it restores
  the previous mode when it quiets, and the stopwatch keeps counting throughout.

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
