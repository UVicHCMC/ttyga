---
name: ttyga
purpose: Single-file GTK4/Adwaita terminal emulator with a profile sidebar (SSH, clippets, layouts) — Greg's personal terminal launcher.
status: active
priority: 2
vcs: git
next: Decide what to build next — nothing is in flight. Candidates are the untracked seasonal_icon.md design brief (unimplemented) and 0.7.0 Feature B (desktop transparency, spec-ready but deliberately unscheduled).
blocked_on: null
horizon: null
updated: 2026-08-17
---

## 2026-08-17

Working tree is clean apart from untracked scratch: `todo` (a running work log/spec file, see below), `seasonal_icon.md`, and four PNGs (`problem.png`, `solution.png`, two `loupe-*.png` screenshots) whose purpose isn't recorded anywhere in the repo, `todo`, or memory — likely design-discussion artifacts, not yet triaged.

**Current version: 0.6.54**, committed and pushed (`98c32d9`), plus one follow-up test-script commit (`cb239f7`). No code changes pending.

0.7.0 shipped Feature A (background image behind terminal panes, 0.6.53) and its gutter-camouflage fix (0.6.54). **Feature B (transparency through to the desktop) is unblocked and fully spec'd** (see the end of the repo-root `todo`) but Greg explicitly deferred it — "not exactly an everyday requirement" — and it must not be started unasked.

**`seasonal_icon.md`** (untracked, dated 2026-08-04) is a from-scratch design brief for a seasonal-palette app-icon feature (SVG regeneration by season, template-based, four palettes given). Not started, not referenced in `todo` or memory. Grepping `ttyga.py` for "season" finds nothing — this is a pure idea sitting in the working tree, not in-progress work.

No open bugs are urgent; the known-issues list (font zoom delta, OSC 7 on RHEL, no terminal scrollbar, deferred config migration) is stable and none are currently biting. Full detail lives in `[[project_ttyga_state]]` (project memory) — read that before resuming, it is far more complete than this file.
