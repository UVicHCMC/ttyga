Continue development of ttyga with the following priorities and architectural direction.

Do not redesign the application or change its core identity. Preserve:

* Python
* GTK4
* libadwaita
* VTE
* YAML configuration
* GNOME-native UX
* terminal-first workflow

ttyga should continue evolving as:
“a terminal workspace launcher”
NOT:
“an IDE” or “a general-purpose terminal emulator”.

Prioritize implementation simplicity, operational usefulness, and maintainability over feature count.

---

# Highest Priority

## 1. Split Panes

Add pane splitting support inside tabs.

Requirements:

* horizontal splits
* vertical splits
* pane resizing
* focus switching
* pane closing
* keyboard shortcuts
* state restoration compatibility

Avoid overengineering.
A lightweight nested Gtk.Paned approach is preferred.

Do not attempt arbitrary pane graph complexity initially.

---

## 2. tmux Awareness

Add optional per-profile tmux integration.

Goal:

* reconnectable remote sessions
* operational persistence
* low implementation complexity

Example:

```yaml id="40r1xz"
tmux: true
tmux_session: prod
```

Expected behavior:

```bash id="lc8i0i"
tmux attach -t prod || tmux new -s prod
```

This is tmux-aware behavior, NOT tmux embedding.

---

## 3. Improved Session Persistence

Expand current restore behavior to support:

* pane layouts
* cwd restoration
* SSH reconnect
* active profile restoration
* optional tmux session continuity

ttyga should feel suspendable.

---

## 4. Profile Variables / Prompted Parameters

Allow profiles to define variables.

Example:

```yaml id="t8fyv0"
command: docker logs -f @container
```

with:

```yaml id="pj1g0l"
variables:
  container:
    prompt: Container
    default: nginx
```

On launch:

* prompt for values
* support defaults
* optionally remember recent values

This is considered extremely high value.

---

## 5. Better Profile Metadata

Add support for:

* favourites/pinning
* tags
* recents
* usage tracking
* fuzzy search
* quick-launch ranking

Profiles should remain YAML-based.

Use SQLite only for metadata/history/state if needed.

---

## 6. Profile Inheritance

Support reusable base profiles.

Example:

```yaml id="3sazm1"
- name: prod-base
  type: ssh
  options:
    user: greg
    host: prod.example.com

- name: prod-htop
  extends: prod-base
  type: clippet
  options:
    command: htop
```

Need:

* predictable merge semantics
* cycle detection
* minimal complexity

---

# Medium Priority

## 7. ttyga Command History Database

Track:

* executed clippets
* timestamps
* target hosts
* optional exit status

Purpose:

* searchable operational history
* recent command workflows

SQLite is appropriate here.

---

## 8. Per-Profile Environment + Working Directory

Support:

```yaml id="naxn0s"
cwd: ~/projects/foo

env:
  KUBECONFIG: ~/.kube/prod
```

Should work cleanly for:

* local shells
* SSH sessions
* clippets

---

## 9. Lightweight Remote File Actions

NOT a full SFTP browser.

Only lightweight operational helpers:

* upload file
* download file
* drag/drop upload
* copy remote path
* open path in shell

Keep this intentionally minimal.

---

## 10. Broadcast Input Mode

Allow sending input to multiple tabs/panes simultaneously.

Requirements:

* visually obvious dangerous mode
* explicit enable/disable
* clear target indication

---

# UX Improvements

## 11. Dangerous Command Confirmation

Before auto-executing potentially destructive commands:

* sudo
* rm
* reboot
* shutdown
* mkfs
* dd
* etc.

Optionally show confirmation UI.

Avoid excessive false positives.

---

## 12. Per-Profile Visual Identity

Allow subtle operational indicators:

* colors
* badges
* environment markers

Example:

* production = red
* staging = yellow
* local = grey

---

## 13. Inline Session Status

Add lightweight indicators for:

* connected/disconnected
* active
* running
* idle

Avoid noisy UI.

---

## 14. ~/.ssh/config Integration

Add support for:

* Host
* User
* Port
* ProxyJump
* IdentityFile

Goal:
avoid duplicate host definitions.

---

# Architectural Direction

Preserve the existing lightweight architecture.

Good additions:

* SQLite for metadata/history/state
* lightweight abstractions where justified

Avoid:

* Electron
* plugin ecosystems
* IDE behaviors
* unnecessary complexity
* cloud/service dependencies

Prioritize:

* GNOME-native behavior
* operational workflows
* maintainability
* simplicity
* responsiveness
* keyboard-centric workflows
