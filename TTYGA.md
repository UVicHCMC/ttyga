# ttyga Help

ttyga is a terminal launcher for GNOME. Instead of hunting through your shell history or keeping a text file of useful commands, you keep **profiles** in a sidebar — click one and it opens a new terminal tab ready to go, whether that's an SSH session, a command to review, or a script that runs immediately.

---

## Your first tab

When ttyga opens you will see the welcome screen. The bullet points on the screen are clickable — click one to perform that action directly. Click **open a new tab** (or press **Ctrl+Shift+T**) to open a plain bash terminal. From here it works like any terminal — type commands, press Enter, get output.

To open more tabs use **Ctrl+Shift+T** or the new-tab button (tab icon) in the header bar. Switch between tabs with **Ctrl+Tab** and **Ctrl+Shift+Tab**, or click the tab label. Close a tab with **Ctrl+W**; when the last tab closes you return to the welcome screen.

---

## The sidebar

The sidebar holds your profiles, organised into groups. Show or hide it with **Ctrl+Shift+B** or the sidebar icon in the header bar. The sidebar is hidden by default — ttyga remembers whether you had it open when you last closed the app.

At the top of the sidebar is a search box. Start typing to filter profiles by name; press **Escape** to clear the search and return focus to the terminal.

Profile buttons show the profile icon on the left, the name in the middle, and a `$` tag on the right for clippet profiles. If a profile has a colour set, a coloured stripe runs down the left edge of its button. The icon also appears in the tab label when the profile is open.

---

## Profiles

A profile is a saved action — either an SSH connection or a shell command. Clicking a profile in the sidebar opens a new terminal tab and either connects to the remote host or types the command into the new shell.

### SSH profiles

An SSH profile stores a hostname and optional username and port. Clicking it opens a new tab and runs `ssh user@host`. The tab label shows `user@host` automatically.

### Mosh and Eternal Terminal

**Mosh** and **Eternal Terminal (et)** are SSH replacements that survive network drops and roaming. ttyga supports both — create a **clippet** profile with the connection command:

- Mosh: `mosh user@host`
- Eternal Terminal: `et user@host`

Both tools must be installed on the remote server. Enable **tmux** in the profile's launch settings for a fully persistent session that reconnects even if the local machine sleeps or the network changes:

```
mosh user@host -- tmux attach -t main || tmux new -s main
```

Mosh and Eternal Terminal profiles show a dim dot (not green) in the tab bar because ttyga tracks SSH sessions specifically. This is a display-only difference — the connection behaves the same.

### Clippet profiles

A clippet profile stores a shell command. Clicking it opens a new tab with the command ready in the prompt. You can review it before pressing Enter, or set **Auto-execute** to have it run immediately.

A clippet can also run in the **current tab** instead of opening a new one — useful for short utility commands. Enable **Run in current tab** in the profile editor.

### Profile variables

Any profile can define variables that are prompted for at launch time. When you click a profile that has variables defined, a small dialog appears asking for each value before the tab opens. Values are pre-filled with the default (or your last-used value) and you can change them before launching.

Variables are defined by hand in `profiles.yaml` — see `TTYGA_TECH.md` for the full syntax.

---

## Profile layouts

A profile can open a pre-arranged multi-pane tab with one click — useful for repeatable workspaces where you always want the same set of terminals side by side.

Add a `layout:` key to the profile in `profiles.yaml`. Layouts are defined as a tree of splits and leaf terminals:

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

Clicking this profile opens one tab with three panes: vim on the left, `make watch` running immediately top-right, and a plain shell bottom-right.

Each leaf node can have a `command:` (runs immediately by default), a `cwd:` override, and `auto_execute: false` to paste the command for review instead of running it. An empty mapping `{}` opens a plain shell.

For the common case of a command pane with a plain shell beside it, use **Shell split** in the profile editor — no YAML needed. Choose **Below** for a vertical split or **Beside** for a horizontal split. A hand-written `layout:` always takes precedence.

The profile editor does not have a UI for the full `layout:` tree — edit `profiles.yaml` by hand and press **Ctrl+Alt+R** to reload. See `TTYGA_TECH.md` for the full syntax.

---

## Creating profiles

Open the profile editor with the pencil icon in the header bar.

**To add a profile:**

- Click **+** at the bottom of the profile list on the left.
- Give it a name — this is what appears in the sidebar.
- Choose a group from the dropdown, or click **+** beside it to add a new group.
- Set the type: **ssh** or **clippet**.
- Fill in the options for that type (see below).
- Optionally set an icon with **Browse…**, or type an XDG icon name, a path to a PNG/SVG file, or a single Unicode character directly into the icon field.
- Optionally set a **Colour** using the colour picker button or by typing a hex value (e.g. `#e5a50a`) for a visual accent stripe in the sidebar and a matching tab dot.
- Optionally override the **Theme** (Light, Dark, or Nord) and **Font** for that profile's terminal tab — leave blank to follow the global Preferences.
- Click **Save** when done.

**To edit a profile:** select it in the list and change any field. **Save** applies all pending changes; **Cancel** discards them.

**To delete a profile:** select it and click **−**.

**To edit a profile from a tab:** right-click the tab label and choose **Edit profile**.

**To merge two tabs into a split:** right-click either tab label and choose a tab under **Merge with**. The two sessions are combined side by side in a single tab; both keep running without interruption. Only single-pane tabs appear as merge targets.

### SSH options

- **Host** — Hostname, IP address, or an alias from `~/.ssh/config`. Click **From SSH config…** to pick an alias from your existing SSH config file.
- **User** — Username to log in with (leave blank to use ssh config defaults).
- **Port** — Port number (leave blank to use ssh config defaults).

### Clippet options

- **Command** — The shell command to type. Multi-line commands are fine.
- **Auto-execute** — Run immediately without waiting for Enter.
- **Run in current tab** — Paste into the active tab instead of opening a new one.

### Launch settings (all profile types)

- **Working dir** — Starting directory for the new terminal tab (e.g. `~/projects/foo`). Leave blank to use the default home directory.
- **Environment** — Extra environment variables, one per line in `KEY=value` format. These are merged with the environment ttyga inherits.
- **tmux** — Enable tmux integration for this profile. When on, the tab will run `tmux attach -t <session> || tmux new -s <session>` — attaching to an existing session or creating a new one. For SSH profiles this runs over the SSH connection, giving you a persistent, reconnectable remote session.

---

## Organising profiles

Profiles belong to groups, which appear as headings in the sidebar. You can create groups from the profile editor. Groups can have their own icon, shown next to the group heading — click the pencil icon beside the group dropdown to set one.

In **Expanders** layout (the default) groups collapse and expand; in **Flat** layout the headings are always visible. Switch layout in Preferences.

The sidebar width is draggable — grab the thin handle at its right edge to resize it.

---

## Tab labels

Each tab shows a coloured dot and a label. The dot is **green** for SSH sessions and dim for local shells. If the profile has a **Colour** set, the dot uses that colour instead. If the profile has an icon, the dot is hidden and the icon itself is tinted green or dim instead. When a background tab receives new output, its dot turns **amber** and the entire tab label flashes until you switch to it or click one of its panes.

For a plain tab the label follows the terminal title reported by your shell (usually the current directory). For a profile tab the label is set by the profile:

- SSH profiles show `user@host` by default.
- Clippet profiles show the profile name by default.

You can customise any profile's label with a **Classifier title** in the profile editor. Use `@` tokens to pull in dynamic values:

| Token | Value |
|---|---|
| `@user` | Local or SSH username |
| `@host` | Local machine name or SSH hostname |
| `@dir` | Current directory basename |
| `@cwd` | Full path of the current directory |

Any key from the profile's `options` can also be used as a token.

**Example:** classifier title `Lab — @host` on an SSH profile with host `cadfael.local` produces `Lab — cadfael.local`.

---

## Background notifications

When ttyga's window is not focused and a background tab produces output, ttyga fires a desktop notification via `notify-send` (requires `libnotify-bin`). The notification body shows a description of the active session — for SSH profiles this is `user@host`; for clippet profiles it is the profile name. You can override this with a **Notification text** field in the profile editor; the custom text is stored as `notify_text` in `profiles.yaml`.

Each background tab fires at most one notification per episode (from when the window lost focus until it regains it or you switch to that tab). When the window regains focus all pending notifications are cleared.

The launcher icon in the dock shows a badge count equal to the number of background tabs with unseen activity. The badge clears automatically when you switch to a tab or the window regains focus.

### Claude Code notification hook

Claude Code (the CLI) can fire its own notifications when it finishes a task or needs input. To wire these through to the desktop, add a `Notification` hook in `~/.claude/settings.json`:

```json
{
  "hooks": {
    "Notification": [
      {
        "matcher": "",
        "hooks": [
          {"type": "command", "command": "/home/greg/.local/bin/ttyga-claude-notify"}
        ]
      }
    ]
  }
}
```

Create the hook script at `~/.local/bin/ttyga-claude-notify`:

```python
#!/usr/bin/env python3
import json, subprocess, sys
d = json.load(sys.stdin)
title = d.get('title', 'Claude Code')
message = d.get('message', '')
subprocess.run(['notify-send', '-a', 'ttyga', title, message], check=False)
```

Make it executable: `chmod +x ~/.local/bin/ttyga-claude-notify`.

This hook fires independently of ttyga's own activity detection — it works even when the terminal is in the foreground.

---

## Split panes

Each tab can be split into multiple panes, each running its own shell. The two split buttons next to the tab bar split the focused pane side-by-side or top/bottom with a single click.

To open a new tab that is already split, use the **↓** (down-arrow) dropdown button in the header bar beside the new-tab button. It offers three layouts — side by side, top and bottom, and a 2×2 grid — each opening as a fresh tab with plain local shells.

| Shortcut | Action |
|---|---|
| Ctrl+Shift+E | Split current pane right (side by side) |
| Ctrl+Shift+D | Split current pane down (top / bottom) |
| Ctrl+Shift+W | Close active pane (closes the tab if it is the last pane) |
| Ctrl+Alt+← / → / ↑ / ↓ | Move focus to the previous or next pane |

Panes can be resized by dragging the handle between them. The tab close button (×) always closes the whole tab regardless of how many panes it contains.

New panes inherit context from the pane that was split: SSH panes reconnect to the same host; local panes open bash in the same working directory.

You can also **merge two tabs** into a split by right-clicking either tab label and choosing a tab under **Merge with**. Both sessions continue uninterrupted — only the layout changes. The directory is taken from OSC 7 if your shell reports it, falling back to the directory implied by a leading `cd PATH` in the profile command (e.g. `cd ~/projects/foo && claude`), and then to the tab's launch directory. To run a different profile in a pane, click the profile in the sidebar while that pane has focus.

---

## Terminal font zoom

You can zoom the terminal font temporarily without changing Preferences:

| Shortcut | Action |
|---|---|
| Ctrl++ or Ctrl+= | Zoom in |
| Ctrl+− | Zoom out |
| Ctrl+0 | Reset to profile or global default |
| Ctrl+scroll | Zoom in / out |

Zoom is ephemeral — it is not saved and resets to the base size when you use Ctrl+0. If a tab has a per-profile font, zoom shifts from that font's size rather than the global default.

---

## Opening files in another app

The header bar has a split button that opens the current tab's working directory in another application. The primary action opens Nautilus (Files); the dropdown offers VS Code. The path is taken from the terminal's current directory if your shell reports it via OSC 7 (most modern shell prompts do), otherwise it falls back to the directory the tab was launched in.

---

## Preferences

Open via the hamburger menu → **Preferences**.

- **Colour scheme** — Light, Dark, or Nord (a blue-grey dark palette). Individual profiles can override this for their own tab.
- **Terminal font** — Picked via the system font chooser; applies to all tabs that do not have a per-profile font set.
- **Sidebar layout** — Expanders (collapsible groups) or Flat (static headings).
- **Scrollback** — Lines of terminal history kept per tab.
- **Scroll speed** — Lines scrolled per wheel tick (1–10).
- **Copy on selection** — Selecting text copies it to the clipboard automatically.
- **Restore tabs on launch** — Re-open the tabs from your last session on next start. ttyga saves pane layouts, working directories, and the active tab so the session is reconstructed as closely as possible. SSH tabs reconnect automatically; tmux tabs reattach to their named session.

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
| Ctrl+Shift+T | New tab |
| Ctrl+W | Close current tab |
| Ctrl+Tab | Next tab |
| Ctrl+Shift+Tab | Previous tab |

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
| Ctrl+0 | Reset font zoom |
| Ctrl+scroll | Zoom font in / out |

---

## Editing profiles by hand

All profiles are stored in `~/.config/ttyga/profiles.yaml`. You can edit this file directly in any text editor — it is plain YAML. After saving, press **Ctrl+Alt+R** in ttyga to reload without restarting.

For full details of the YAML structure and every available field, see `TTYGA_TECH.md`.

---

## Profile examples

### Basic SSH connection

```yaml
- name: Work server
  group: Remote
  type: ssh
  icon: network-server-symbolic
  options:
    host: work.example.com
    user: greg
    port: 2222
```

### SSH using a ~/.ssh/config alias

Leave `user` and `port` blank — ssh resolves them from your config file. Use **From SSH config…** in the editor to auto-populate.

```yaml
- name: Prod
  group: Remote
  type: ssh
  icon: network-server-symbolic
  color: '#e01b24'
  options:
    host: prod
```

### Clippet — paste and review

The command lands in the prompt. Press Enter when ready.

```yaml
- name: Recent git log
  group: Dev
  type: clippet
  icon: folder-symbolic
  cwd: ~/projects/myapp
  options:
    command: git log --oneline -20
```

### Clippet — run immediately

```yaml
- name: Disk usage
  group: Utilities
  type: clippet
  options:
    command: df -h
    auto_execute: true
```

### Clippet — run in current tab

Short utility commands that don't need their own tab.

```yaml
- name: Clear screen
  group: Utilities
  type: clippet
  options:
    command: clear
    auto_execute: true
    in_place: true
```

### Profile with variables

A dialog prompts for `@container` before launching.

```yaml
- name: Docker logs
  group: Containers
  type: clippet
  options:
    command: docker logs -f @container
    auto_execute: true
  variables:
    container:
      prompt: Container name
      default: nginx
```

### Mosh connection with tmux persistence

```yaml
- name: Work (Mosh)
  group: Remote
  type: clippet
  icon: network-wireless-symbolic
  options:
    command: mosh greg@work.example.com -- tmux attach -t main || tmux new -s main
    auto_execute: true
```

### Profile inheritance

Define a base profile once; child profiles inherit and extend it. Set `hidden: true` to keep the base off the sidebar.

```yaml
- name: prod-base
  hidden: true
  type: ssh
  options:
    host: prod.example.com
    user: greg

- name: Prod — shell
  group: Remote
  extends: prod-base
  color: '#e01b24'

- name: Prod — htop
  group: Remote
  extends: prod-base
  type: clippet
  color: '#e01b24'
  options:
    command: htop
    auto_execute: true
```

### SSH tab with custom classifier

```yaml
- name: Lab
  group: Remote
  type: ssh
  options:
    host: cadfael.local
    user: greg
  classifier:
    title: "Lab (@host)"
```
