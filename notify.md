# ttyga Notifications

ttyga fires desktop notifications and updates the launcher icon badge when background tabs have activity while the window is unfocused.

## How it works

- **Window focus tracking** — ttyga watches `notify::is-active` on its window. When focus is lost, background activity can trigger notifications.
- **Activity detection** — the VTE `contents-changed` signal fires on every terminal write. If the window is unfocused and that tab hasn't already notified in the current episode, `notify-send -a ttyga` is called.
- **One notification per episode** — each tab fires at most once between the window losing focus and regaining it (or the user switching to / focusing that tab).
- **Launcher badge** — the dock badge (Unity/GNOME `com.canonical.Unity.LauncherEntry` D-Bus signal) shows a count of background tabs with unseen activity. Requires the dock to support Unity launcher APIs (GNOME Shell with a compatible extension, or KDE).
- **Tab flash** — the tab label flashes using a CSS `@keyframes` animation while there is unseen activity.

## Notification body text

The notification body defaults to:

| Profile type | Default body |
|---|---|
| SSH profile | `user@host` |
| Clippet profile | Profile name |
| Plain tab | Tab label |

Override with `notify_text` in `profiles.yaml` or the **Notification text** field in the profile editor:

```yaml
- name: My Tool
  group: Claude
  type: clippet
  notify_text: 'My Tool: Claude needs input'
  options:
    command: cd ~/projects/mytool && claude
    auto_execute: true
```

## Claude Code notification hook

Claude Code fires a `Notification` hook when it needs input or completes a task. This is independent of ttyga's activity detection — it fires even when the terminal is focused.

Add to `~/.claude/settings.json`:

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

Create `~/.local/bin/ttyga-claude-notify`:

```python
#!/usr/bin/env python3
import json, subprocess, sys
d = json.load(sys.stdin)
subprocess.run(
    ['notify-send', '-a', 'ttyga', d.get('title', 'Claude Code'), d.get('message', '')],
    check=False)
```

Make it executable: `chmod +x ~/.local/bin/ttyga-claude-notify`

## Requirements

- `libnotify-bin` (`notify-send`) for desktop notifications
- A GNOME Shell extension supporting Unity launcher APIs for the badge count (optional)
