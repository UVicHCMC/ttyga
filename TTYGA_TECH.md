# ttyga (/ˈtaɪ.ɡə/)

A GTK4 terminal launcher for GNOME. A sidebar holds named profiles organised into groups; clicking a profile opens a new terminal tab running an SSH session or a shell snippet. Multiple tabs share the same sidebar.

---

## Dependencies

| Package | Debian/Ubuntu name |
|---|---|
| Python 3.10+ | `python3` |
| GTK 4.10+ | `gir1.2-gtk-4.0` |
| libadwaita | `gir1.2-adw-1` |
| VTE 3.91 | `gir1.2-vte-2.91` |
| PyGObject | `python3-gi` |
| PyYAML | `python3-yaml` |

---

## Installation

Run `install.sh` to deploy the app to your local user directories:

```
bash install.sh
```

This copies `ttyga.py` to `~/.local/bin/ttyga`, installs the SVG icon, and registers the `.desktop` file so ttyga appears in the GNOME application launcher. It checks for missing dependencies and will tell you what to install before proceeding.

To remove the installation:

```
bash install.sh --uninstall
```

Use `--dry-run` with either command to preview what would happen.

For development, run the script directly without installing:

```
python3 ttyga.py
```

---

## Config files

All config lives under `~/.config/ttyga/`:

| File | Purpose |
|---|---|
| `profiles.yaml` | Profile definitions and group order |
| `settings.yaml` | User preferences (colour scheme, font, etc.) |
| `app_state.json` | Sidebar width, expander state, open tabs |

The directory and files are created automatically on first run. `app_state.json` is written on every close; you do not need to edit it manually.

---

## The interface

### Header bar

| Control | Action |
|---|---|
| Sidebar icon (left) | Toggle the sidebar (Ctrl+Shift+B) |
| Pencil icon (right) | Open the profile editor |
| Open-in split button (right) | Open working directory in Nautilus (primary) or VS Code (dropdown) |
| Hamburger menu (right) | Preferences, keyboard shortcuts, help, about |

The **+** button next to the tab bar opens a new terminal tab.

### Sidebar

Profiles are listed under their group headings. In **Expanders** layout groups collapse and expand; in **Flat** layout headings are static labels. The sidebar width is draggable via the thin handle at its right edge.

The search bar at the top filters profile names in real time. Press **Escape** to clear the search and return focus to the terminal.

Profile buttons show the profile icon, the name, and (for clippets) a `$` tag. If a profile has a `color` set, a coloured stripe appears on the left edge of its button. The active profile for the current tab is highlighted.

### Tabs

Each tab runs its own bash shell. A small coloured dot in each tab label is **green** for SSH sessions and dim for local shells. If the profile has a `color` field, the dot uses that colour instead. When a profile has an `icon` set, the dot is hidden and the icon itself is tinted green (SSH) or dim (local) instead. The SSH green colour can be overridden globally via the `ssh_color` setting in `settings.yaml` (e.g. `ssh_color: '#ff9900'`); leave it empty to use the theme default.

Right-clicking a tab label opens a context menu. Profile tabs show **Edit profile** at the top. All tabs show a **Merge with** section listing other single-pane tabs — selecting one merges that tab's terminal into the current tab as a horizontal split. The source session continues uninterrupted.

When a background tab receives output, its dot turns **amber** until you switch to it or focus one of its panes.

The tab label for profile tabs follows the profile's classifier title. Plain tabs follow the terminal's OSC 2 window title (usually the shell's `$PROMPT_COMMAND` output). If the profile has an `icon` field set, the icon appears in the tab label to the left of the title — XDG icon names, file paths, and Unicode characters (including emoji) are all supported. If the profile has no `icon` but its group does, the group icon is used as the tab icon instead.

---

## Profiles YAML

`~/.config/ttyga/profiles.yaml` has two top-level keys:

```yaml
groups:
  - name: Lab machines
    icon: computer-symbolic   # optional group icon (XDG icon name or character)
  - Commands                  # plain string also accepted (no icon)
  - Maintenance

profiles:
  - name: …
    …
```

The `groups` list controls sidebar order. Groups referenced by profiles but absent from the list appear after the listed groups.

After editing by hand, press **Ctrl+Alt+R** to reload without restarting.

### Common fields

| Field | Required | Description |
|---|---|---|
| `name` | yes | Label in the sidebar |
| `type` | yes | `ssh` or `clippet` |
| `group` | no | Sidebar group (default: `General`) |
| `icon` | no | XDG icon name, PNG/SVG path, or single Unicode character |
| `color` | no | Hex colour (e.g. `#e5a50a`) — accent stripe in sidebar, dot in tab |
| `color_scheme` | no | `light`, `dark`, or `nord` — overrides global theme for this tab |
| `terminal_font` | no | Pango font description (e.g. `Fira Code 14`) — overrides global font for this tab |
| `cwd` | no | Starting directory for the tab (e.g. `~/projects/foo`) |
| `env` | no | Extra environment variables merged at spawn (mapping of `KEY: value`) |
| `tmux` | no | `true` to enable tmux integration — runs `tmux attach -t <session> \|\| tmux new -s <session>` |
| `tmux_session` | no | tmux session name (default: `main`); only used when `tmux: true` |
| `options` | yes | Type-specific settings — see below |
| `classifier` | no | Controls the tab label |
| `extends` | no | Name of another profile to inherit from — see *Profile inheritance* below |
| `hidden` | no | `true` to hide this profile from the sidebar (useful for base profiles) |
| `layout` | no | Multi-pane split tree opened on click — see *Profile layouts* below |

### SSH profiles

Opens an SSH connection by running `ssh [-p port] [user@]host`.

**Options:**

| Key | Description |
|---|---|
| `host` | Hostname, IP address, or `~/.ssh/config` alias |
| `user` | Remote username (optional; omit to use ssh config) |
| `port` | Port number (optional; omit to use ssh config) |

**Example:**

```yaml
- name: Cadfael
  type: ssh
  group: Lab machines
  icon: computer-symbolic
  color: "#4a90d9"
  options:
    host: cadfael.local
    user: hcmc
```

**Example — SSH with tmux (persistent session):**

```yaml
- name: Prod server
  type: ssh
  group: Lab machines
  icon: network-server-symbolic
  color: "#c01c28"
  tmux: true
  tmux_session: prod
  options:
    host: prod.example.com
    user: deploy
```

Clicking this profile runs: `ssh deploy@prod.example.com -t 'tmux attach -t prod || tmux new -s prod'`

---

### Clippet profiles

Pastes a shell command into the terminal. With `auto_execute: true` a newline is appended so the command runs immediately.

**Options:**

| Key | Description |
|---|---|
| `command` | Shell command string (multi-line is fine) |
| `auto_execute` | `true` to execute immediately, `false` to paste for review (default: `false`) |
| `in_place` | `true` to paste into the current tab instead of opening a new one (default: `false`) |

**Example — paste for review:**

```yaml
- name: Disk usage
  type: clippet
  group: Commands
  icon: drive-harddisk-symbolic
  options:
    command: df -h
    auto_execute: false
```

**Example — run immediately with env and cwd:**

```yaml
- name: Deploy production
  type: clippet
  group: Projects
  icon: media-playback-start-symbolic
  color: "#e5a50a"
  color_scheme: dark
  terminal_font: Monospace 12
  cwd: ~/projects/myapp
  env:
    KUBECONFIG: ~/.kube/prod
  options:
    command: ./deploy.sh production
    auto_execute: true
```

**Multi-line commands** use YAML block scalar syntax:

```yaml
- name: Octal permissions
  type: clippet
  group: Commands
  options:
    command: |
      find . -maxdepth 1 -printf '%m %M %u:%g %f\n'
    auto_execute: true
```

---

### Classifier titles

`classifier.title` overrides the tab label. Use `@key` to interpolate values from `options` or the built-in tokens:

| Token | Value |
|---|---|
| `@user` | Local or SSH username |
| `@host` | Local machine hostname or SSH hostname |
| `@dir` | Current directory basename (requires shell to emit OSC 7) |
| `@cwd` | Full current directory path (requires OSC 7) |

Any key from `options` also works as a token.

```yaml
- name: Cadfael
  type: ssh
  group: Lab machines
  classifier:
    title: "Lab — @host"
  options:
    host: cadfael.local
    user: hcmc
```

Tab label becomes: `Lab — cadfael.local`

---

### Profile variables

Any profile can define variables that are prompted for each time the profile is launched. Variables are defined at the top level of the profile alongside `name`, `type`, etc.

```yaml
variables:
  var_name:
    prompt: Label shown in the dialog
    default: default_value   # optional
```

On launch, ttyga shows a dialog with one field per variable, pre-filled with the most recently used value or the default. The resolved values are substituted for `@var_name` tokens anywhere in:

- `command` (clippet)
- `host`, `user`, `port` (SSH options)
- `cwd`
- `classifier.title`

Recent values are persisted in `app_state.json` and restored the next time the same profile is launched.

**Example — parameterised docker logs:**

```yaml
- name: Container logs
  type: clippet
  group: Docker
  icon: utilities-system-monitor-symbolic
  variables:
    container:
      prompt: Container name
      default: nginx
  classifier:
    title: "logs: @container"
  options:
    command: docker logs -f @container
    auto_execute: true
```

**Example — SSH with a prompted namespace:**

```yaml
- name: kubectl
  type: clippet
  group: K8s
  variables:
    ns:
      prompt: Namespace
      default: default
  options:
    command: kubectl get pods -n @ns
    auto_execute: false
```

The `variables:` block is edited by hand in YAML; the profile editor does not yet have a UI for it.

---

### Profile inheritance

A profile can inherit fields from another profile using `extends`. The named parent profile is resolved at load time; the child wins on any field that is explicitly set, and dict fields (`options`, `env`, `variables`) are deep-merged so a child can add keys without repeating the entire block.

```yaml
- name: prod-base
  hidden: true          # keep this off the sidebar
  type: ssh
  color: '#e01b24'
  options:
    host: prod.example.com
    user: deploy

- name: Prod — shell
  group: Remote
  extends: prod-base    # inherits host, user, color

- name: Prod — htop
  group: Remote
  extends: prod-base
  type: clippet         # overrides the parent's type
  options:
    command: htop
    auto_execute: true
```

Rules:
- `name` and `group` are never inherited (each profile always has its own).
- `extends` is resolved away in the merged result — it does not appear at runtime.
- Chains are supported (A extends B extends C); cycles are detected and logged as warnings.
- `hidden: true` on the parent does not affect children — each controls its own visibility.

---

### Profile layouts

A profile can open a pre-arranged multi-pane tab with a single click. Add a `layout:` key at the top level of the profile (alongside `name`, `type`, etc.). Its value is a tree of split and leaf nodes.

**Split node** — divides the space and contains two child nodes:

```yaml
layout:
  split: horizontal   # or vertical
  start: <node>
  end:   <node>
```

**Leaf node** — spawns a terminal with an optional command:

```yaml
command: make watch   # optional; auto_execute: true by default
cwd: ~/projects/foo   # optional; inherits profile cwd if omitted
auto_execute: false   # set to false to paste without running
```

An empty mapping `{}` (or omitting the node) opens a plain shell.

**Example — 4-pane dev workspace:**

```yaml
- name: Dev workspace
  type: clippet
  cwd: ~/projects/myapp
  layout:
    split: horizontal
    start:
      command: vim .
    end:
      split: vertical
      start:
        command: make watch
        auto_execute: true
      end: {}
```

Clicking this profile opens one tab with three panes: vim on the left, make watch running immediately top-right, and a plain shell bottom-right.

Notes:
- Layout panes always run locally, regardless of the profile's `type`. The base profile provides appearance (font, colour scheme, env vars) but not the connection type.
- The profile editor does not yet have a UI for `layout:` — edit the YAML by hand and press **Ctrl+Alt+R** to reload.

---

## Editing profiles in the UI

Click the sidebar toggle icon → pencil icon, or the pencil icon in the header bar.

The left panel lists all profiles sorted by group then name. **+** adds a new profile; **−** deletes the selected one. All changes are held in memory until **Save**; **Cancel** discards them.

The right panel form fields:

| Field | Notes |
|---|---|
| **Name** | Label shown in the sidebar |
| **Group** | Dropdown of existing groups; **+** adds a new group; pencil sets the group icon |
| **Type** | `ssh` or `clippet`; switches which fields appear below |
| **Icon** | XDG icon name, file path, or Unicode character; **Browse…** opens a searchable picker |
| **Colour** | Hex colour for the sidebar stripe and tab dot; type a value or use the colour picker button |
| **Theme** | Default / Light / Dark / Nord — per-profile terminal colour scheme |
| **Font** | Pango font description; **Choose…** opens the system font picker; leave blank to inherit global |
| **Host / User / Port** | SSH fields; **From SSH config…** populates from `~/.ssh/config` |
| **Command / Auto-execute / Run in current tab** | Clippet fields |
| **Classifier title** | Optional tab label template with `@` tokens |
| **Working dir** | `cwd` override for the spawned shell |
| **Environment** | `KEY=value` lines, one per line; merged with the inherited environment |
| **tmux** | Toggle + session name; wraps the command in `tmux attach -t <name> \|\| tmux new -s <name>` |

---

## Preferences

Open via hamburger menu → **Preferences**.

| Setting | Key in `settings.yaml` | Description |
|---|---|---|
| **Colour scheme** | `color_scheme` | `light`, `dark`, or `nord` |
| **Terminal font** | `terminal_font` | Pango font description |
| **Sidebar layout** | `sidebar_layout` | `expanders` or `flat` |
| **Scrollback** | `scrollback` | Lines per tab (100 – 1 000 000) |
| **Scroll speed** | `scroll_speed` | Lines per wheel tick (1–10, default 3) |
| **Copy on selection** | `copy_on_selection` | `true` / `false` |
| **Restore tabs on launch** | `restore_tabs` | `true` / `false`; saves pane layouts, cwds, and active tab index |
| **SSH indicator colour** | `ssh_color` | Hex colour for the SSH tab dot / icon tint (e.g. `#ff9900`); empty = theme default |

**Session persistence detail (`app_state.json`):**

`open_tabs` is now a list of `{"layout": <node>}` objects. Each `<node>` is either:

- `{"type": "terminal", "profile": {"name": ..., "group": ...}, "cwd": "..."}` — a single pane; `profile` is `null` for plain shells
- `{"type": "paned", "orientation": "horizontal"|"vertical", "position": <int>, "start": <node>, "end": <node>}` — a split

`active_tab_index` records the focused tab (integer page index).

On restore, SSH panes reconnect automatically (init-file), tmux panes reattach to their named session. Local panes reopen in their saved cwd.

The legacy flat format (`{"name": ..., "group": ...}`) is still accepted for backward compatibility.

Welcome screen appearance can be set directly in `settings.yaml`:

| Key | Description |
|---|---|
| `welcome_image` | Path to a PNG or SVG shown on the welcome screen; empty = app icon |
| `welcome_blurb` | One-line text shown below the image |

---

## Keyboard shortcuts

### Application

| Shortcut | Action |
|---|---|
| Ctrl+Q | Quit |
| Ctrl+Alt+R | Reload profiles from disk |

### Tabs

| Shortcut | Action |
|---|---|
| Ctrl+T | New tab |
| Ctrl+W | Close active pane (closes tab if last pane) |
| Ctrl+Tab | Next tab |
| Ctrl+Shift+Tab | Previous tab |

### Panes

| Shortcut | Action |
|---|---|
| Ctrl+Shift+E | Split current pane right (side by side) |
| Ctrl+Shift+D | Split current pane down (top / bottom) |
| Ctrl+Alt+← / → | Focus previous / next pane (cycles within tab) |
| Ctrl+Alt+↑ / ↓ | Focus previous / next pane (cycles within tab) |

The tab close button (×) always closes the entire tab. `Ctrl+W` closes only the focused pane; if it is the last pane it closes the tab. Panes can be resized by dragging the separator handle.

### Sidebar

| Shortcut | Action |
|---|---|
| Ctrl+Shift+B | Show / hide sidebar |
| Ctrl+F | Focus the profile search box |
| Escape | Clear search, return focus to terminal |

### Terminal

| Shortcut | Action |
|---|---|
| Ctrl+Shift+C | Copy selection |
| Ctrl+Shift+V | Paste |
| Ctrl++ / Ctrl+= | Zoom font in |
| Ctrl+− | Zoom font out |
| Ctrl+0 | Reset font zoom to profile or global default |
| Ctrl+scroll | Zoom font in / out |
