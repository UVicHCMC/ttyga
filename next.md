ttyga — development reference
==============================

Identity: Python + GTK4 + libadwaita + VTE + YAML. A terminal workspace launcher,
not an IDE or general-purpose terminal emulator. GNOME-native UX. Terminal-first
workflow. Prioritise implementation simplicity and operational usefulness.

---

# Completed

1.  Ctrl+Shift+T/W/B shortcuts (standard terminal keybindings)
2.  SSH tab dot — vivid green, 10px; local tab dot — dim, 10px
3.  Tab icons from profile `icon` field
4.  Terminal font zoom — Ctrl++/−/0 and Ctrl+scroll
5.  Per-profile `env` and `cwd` — runtime spawn and editor UI
6.  Profile grouping in editor sidebar
7.  Scroll speed configurable (Preferences → Terminal, default 3, range 1–10)
8.  Open-in-Nautilus — OSC 7 URI first, `spawn_dir` fallback
9.  `~/.ssh/config` integration — parser, "From SSH config…" button, command fix
10. Per-profile visual identity — `color` field, sidebar border stripe, tab dot colour, editor swatch
11. Per-profile terminal style — `color_scheme` and `terminal_font` fields; editor Theme dropdown + Font picker; zoom respects per-tab base font
12. Split panes — Ctrl+Shift+E (right) / Ctrl+Shift+D (down); Ctrl+W closes pane or tab; Ctrl+Alt+Arrow cycles focus; Gtk.Paned tree per tab_root; tab ✕ closes whole tab
13. tmux awareness — per-profile `tmux: true` + `tmux_session`; wraps command; NOT embedding
14. Session persistence — pane layouts saved/restored recursively; cwd from OSC 7 or spawn_dir; SSH reconnects via init-file; tmux reattaches; active tab index restored
15. Profile variables — `variables:` block with `prompt`/`default`; dialog on launch; `@token` substitution in command/host/user/port/cwd/classifier; recent values persisted in app_state.json
16. Profile inheritance — `extends: profile-name`; simple merge (child wins, dict fields deep-merged); cycle detection; `hidden: true` hides base profiles from sidebar
17. Subtle sidebar watermark — dim app icon + name at bottom of sidebar
18. Help search — Ctrl+F search bar in Help window with match highlighting

---

# Remaining — active

None currently queued. See exploratory section below.

---

# Parked (needs scoping or design)

- **Custom theme slot** — add a fourth `custom` entry to ttyga's theme system, defined
  in settings.yaml (colours, not dconf). Better than importing GNOME Terminal profiles
  (fragile dconf dependency). Design: what fields? Full 16-colour ANSI palette, or just
  bg/fg/accent?

- **Advanced prefs panel** — expose sidebar font size, icon size, and other CSS/Python
  constants that are currently edit-the-script. Scope: which variables actually matter?

- **Better profile metadata** — favourites (simple `favourite: true` YAML flag + sort-to-top
  is probably sufficient), tags. Full SQLite metadata store and fuzzy-search ranking is
  over-engineered for typical profile counts. Revisit if profile count grows significantly.

---

# Dropped (with reasons)

- **Command history database** — SQLite tracking of executed clippets, timestamps, exit
  status. Shell history + `history | grep` already covers the use case; capturing exit
  status from VTE is fiddly. Not worth it.

- **Lightweight remote file actions** — upload/download/drag-drop/copy remote path. Starts
  as "intentionally minimal" but upload/download requires scp/sftp subprocess management
  and file picker UI. Firmly IDE territory. Copy remote path and open-path-in-shell are
  micro-features that don't need a section; open-in-Nautilus already handles local paths.

- **Broadcast input mode** — tmux `synchronize-panes` already does this. Reimplementing
  it in ttyga's pane layer is redundant and introduces dangerous global state.

- **Dangerous command confirmation** — the user authored the clippet with auto_execute: true
  deliberately. A nag dialog undermines the UX. The "dangerous command" list is inherently
  fuzzy and will produce false positives. Adds friction without safety.

- **Inline session status (connected/active/idle)** — the SSH dot already covers
  connected vs. local. Active/idle detection means polling VTE output, producing
  unreliable signals for long-running commands. Not worth the complexity.

- **GNOME Terminal theme import** — reading dconf profile colours requires UUID-keyed
  path parsing, is fragile across GNOME versions, and is a runtime dependency on dconf.
  Custom theme slot (parked above) is the better path.

- **Rename to Taiga** — reconsidered; ttyga stays ttyga.

---

# Known limitations (not bugs)

- **Open in file manager for SSH tabs shows homedir** — OSC 7 from a remote shell emits
  `file://hostname/path`; Nautilus cannot browse remote filesystems via file:// URIs.
  Proper fix would need SFTP/GIO integration (`sftp://user@host/path`), which requires
  knowing the SSH connection parameters at button-press time. Noted in help docs.

- **Non-auto-execute pre-fill** — 300ms delay before feeding the command is pragmatic;
  very slow .bashrc could cause the text to appear before readline is ready.

---

# Exploratory / future (not yet scoped)

- Mosh / Eternal Terminal: not an implementation task — just use a clippet profile with
  `mosh user@host` or `et user@host` as the command. Documented in TTYGA.md.
- Tab clustering — launch a tab from within a remote SSH tab
- Remote machine-specific profiles
- Filesystem browser sidebar (local; tree → cd on click)
- Drag-and-drop file upload (local only; remote is out of scope — see dropped items)
- GPU acceleration
- 24-bit colour / Neovim compatibility notes
- Right-click context menu on pane: Split right / Split down / Close pane
  (discoverability improvement; no header buttons needed)

---

# Activity indicator on tabs — implementation plan

**Goal:** badge a tab when output has arrived that the user hasn't seen yet.
No idle detection in scope for the initial pass.

## Signal

VTE emits `contents-changed` every time the terminal buffer updates. Connect
once per terminal in `add_tab` / `_restore_pane`, same place the scroll
controller is attached:

```python
term.connect('contents-changed', self._on_terminal_output, term)
```

## State

Add `'has_activity': False` to each terminal's metadata dict in `self.tabs`.

In `_on_terminal_output(self, term, term_ref)`:
- If `term_ref is self._active_terminal` → do nothing (user is watching).
- Otherwise: set `self.tabs[term_ref]['has_activity'] = True` and call
  `_update_tab_activity_badge(term_ref)`.

Clear the flag (and badge) in `_on_tab_switched` and whenever a pane gains
focus. Clear for every terminal in the newly-active tab (all panes are "seen"
when the tab is switched to, not just the focused pane).

## Visual

The tab label box holds: status dot · icon · title label · close button.

Add a small activity dot (6–8px) to the right of the title label, hidden by
default. Show it when `has_activity` is True.

Colour distinct from the SSH/local status dot — amber/orange across themes:
- light: `#e5a50a`
- dark:  `#f5c211`
- nord:  `#ebcb8b`

Store the dot widget reference in tab metadata so the badge update and clear
paths can reach it without traversing the widget tree.

CSS class: `.tab-dot-activity` — same pattern as `.tab-dot-ssh` / `.tab-dot-local`.

## Debounce

`contents-changed` fires at high rate during active output. The set is
idempotent (True → no-op), so no debounce is needed for correctness. A
`GLib.idle_add` wrapper on the widget update can avoid redundant redraws
during bursts if it ever shows up in profiling.

## Out of scope for first pass

- Idle/active state (per-terminal timer resetting on contents-changed)
- Per-pane badges within a split tab — badge the tab, not individual panes
- Persistence across restarts
