---
name: ttyga
purpose: Single-file GTK4/Adwaita terminal emulator with a profile sidebar (SSH, clippets, layouts) — Greg's personal terminal launcher.
status: active
priority: 2
vcs: git
next: **Reload hooks, then install.** (1) Open `/hooks` once in Claude Code, or restart the session, so the new `StopFailure` registration is picked up — it was added to `~/.claude/settings.json` this session and the settings watcher may not have reloaded. (2) Run `./install.sh` — `~/.local/bin/ttyga` is at 0.6.61 (checked 2026-09-11; the long-standing "still 0.6.55" note here was stale), one version behind master's 0.6.62, so the countdown is not live for Greg until it runs. The hook binary itself is already installed and registered. (3) **Settle the push email** (see blocked_on), then `git push origin master` — three commits are queued (`a2d5945` 0.6.61, `da9aeb5`, `f719304`) and 0.6.62 is NOT yet committed. Then Greg to CONFIRM in a real session, none of it reachable from a script (no synthetic input on Wayland): (a) the countdown's appearance at the next real limit hit — digits in `term_warn`, the red pulse at expiry, and whether the sub-line stays legible at narrow sidebar widths; (b) Shift+drag selects inside a `claude` tab and Ctrl+Shift+V pastes into a `tmux-cssh` tab; (c) an ordinary drag in a plain shell STILL selects — the capture-phase mouse probe sits in front of VTE's selection gesture, and a regression there would be silent and intermittent. Also still unconfirmed from 2026-09-03: plain wheel in an actual `claude` session, the 0.6.56 Ctrl+click path, and the `.open` sidebar tint / `.active` font-weight 600 in all three themes.
blocked_on: Push rejected by GitHub — GH007, "your push would publish a private email address". All commits use `gtnewton@gmail.com` and the account now blocks command-line pushes that expose it. Note that address is ALREADY published in this repo's pushed history (`416c896` and older), so the protection was switched on afterwards. Greg is deciding tomorrow between: (1) untick the block at https://github.com/settings/emails and push unchanged — recommended, since rewriting only the last three commits buys no real privacy and splits the log across two author emails; (2) rewrite just the three unpushed commits to `5490479+gtnewton@users.noreply.github.com`; (3) filter-repo the whole history and force-push — breaks every existing clone of a shared UVicHCMC repo. Nothing is broken locally; the work is committed and clean.
horizon: null
updated: 2026-09-11
---

## 2026-09-11 (sidebar usage-limit countdown — 0.6.62)

Greg hits the Claude usage limit often — 12 distinct events in the transcript
history, three in the 48 h before this session — and wanted the sidebar to
count down to the reset instead of him guessing. Built it.

**Where the reset time comes from, because this took the whole session to
establish and is all recorded in CLAUDE.md now.** The `Notification` hook
Greg already had wired to `ttyga-claude-notify` will NEVER fire for a usage
limit: its `notification_type` values are a closed set and hitting the wall
emits no notification at all. `StopFailure` is the hook that fires. It does
not carry the structured limit data, so the hook reads `transcript_path` and
pulls `quotaLimits.resetsAt` (epoch seconds) off the last assistant message.
The rendered text is useless for this — 12-hour local, no date.

**New file `hooks/ttyga-quota-hook.py`**, installed as
`~/.local/bin/ttyga-quota-hook`, registered for `StopFailure` (catches the
limit) and `Notification` (labels the countdown with what Claude Code's own
auto-resume did: `claude resumed` / `press enter` / `not resuming`). It writes
`~/.config/ttyga/quota.json` atomically; that file is the entire interface.
Verified against Greg's real 2026-09-04 transcript, not a fixture.

Two guards in the transcript scan are load-bearing and were found by testing,
not by reading: only a **future** `resetsAt` is accepted, and the scan retries.
The hook and the transcript write are not ordered, and a long session holds
earlier limits that have since reset — without the guard it counts down to a
time already gone by.

**On the ttyga side the limit is account-wide**, which collapsed the design:
no per-pane id, no IPC, no env var, no per-tab state in `self.tabs` — one
countdown for the app, one watched file. `_clock_mode` gained a third value
`'quota'` beside `'clock'`/`'stopwatch'`, sharing the existing 1 s clock tick
and the existing `Gtk.Stack` of controls (mode names == page names). The
expiry alarm pulses by alternating colour on that same tick; there is no CSS
to animate, both clock lines are `Gtk.DrawingArea`s.

Decisions worth not relitigating: the alarm **is not cancelled** when
auto-resume fires — the alarm exists because auto-resume is unreliable, so
letting the unreliable thing suppress it reproduces the failure it was built
for; it labels itself instead. A **running** stopwatch is never taken off
screen for a new countdown, but the expiry alarm does take over and restores
the previous mode when it quiets after five minutes.

**Also established, for the next time Greg asks why auto-resume "doesn't
work".** It is not flaky, it is conservative: a >30 min gap in observed ticks
with the reset passed sets `sleptThroughReset` and downgrades to "press enter
to continue" rather than auto-continuing (so most overnight or slept-through
waits), and Esc / Ctrl+C during the wait cancels the armed episode outright.
Greg has not turned it off — the setting is absent, which means on.

New driver `tests/test_quota_timer.py` (43/43). All seven green: 27/27, 13/13,
23/23, 31/31, 19/19, 16/16, 43/43. It records a **fourth** harness trap for
the template: a deferred check must land before the next step runs, and the
template's fixed 700 ms gap is shorter than a `Gio.FileMonitor` needs
(`rate-limit` defaults to 800 ms) — the first run reported three failures that
were purely the harness overwriting its own checks.

Two things caught by testing rather than by review: the `--dev` block rebinds
the config paths and needed `QUOTA_FILE` added, or a dev run reads and the
hook writes the REAL countdown; and the startup path (quota.json already
present when `DevFrame()` builds the sidebar) was initially untested despite
being the restart-safe behaviour I had claimed — it sets the clock mode while
the sidebar is still being built. Both now covered.

`TTYGA.md` gained two sections; its first markdown links were removed again on
noticing `HelpWindow` renders no link syntax, so they would have shown as
literal brackets in the help window. `install.sh` and `package.sh` now carry
the hook.

**Not committed** — the push is still blocked on the email question below, and
nothing here touches it.

**Global settings wired at the end of the session, on Greg's instruction.**
`~/.claude/settings.json` now registers `~/.local/bin/ttyga-quota-hook` for both
`StopFailure` (new block) and `Notification` (appended beside the existing
`ttyga-claude-notify`, which is untouched). Backed up first, merged
programmatically rather than by hand, validated with `jq -e` on both event
paths, and a `jq -S 'del(.hooks)'` diff confirms no other setting moved. The
hook binary was installed and pipe-tested at its installed path BEFORE being
registered — a hook pointing at a missing file fails silently on every turn.
Neither hook can be proven to fire from inside a session: `StopFailure` needs a
turn to actually fail on a usage limit.

Incidentally confirmed from the settings schema, independently of the binary
strings: `autoContinueAtUsageLimit` is a real key ("wait for the limit to reset
and continue the task automatically"), and it is absent from Greg's file, so
auto-resume is on — as diagnosed above.

**Correction worth carrying:** this file claimed `~/.local/bin/ttyga` was at
0.6.55 for several sessions. It is 0.6.61. The earlier draft of today's `next`
repeated the stale figure verbatim before it was checked. Re-verify concrete
claims in `next` when rewriting it rather than copying them forward.

## 2026-09-09 (copy/paste across mouse-grabbed tabs — 0.6.61)

Greg could not copy from a `claude` tab and paste into a `tmux-cssh` tab, by
keyboard or by select + middle-click. Not a clipboard problem: **both tabs have
a program holding the mouse.** claude enables xterm mouse reporting; `~/.tmux.conf:2`
is `setw -g mouse on`, and `tmux-cssh` adds `synchronize-panes on`. So the drag
never became a selection and the middle click never reached VTE. VTE's own
escape hatch is Shift.

**Found a real bug while looking.** `on_key_pressed` (Ctrl+Shift+C/V) was on a
default *bubble*-phase controller. The driver test now confirms what was only
suspected: **VTE installs its own key controller at BUBBLE**, and capture always
runs first, so ours lost the key to the child — copy/paste by keyboard was dead
inside any full-screen program. `_on_terminal_scroll_key` had already hit this
exact wall and been moved to CAPTURE; the copy/paste binding never got the same
treatment. Now capture, and it uses `controller.get_widget()` rather than
`_get_active_terminal()`, which is exact in a split.

**Added the Shift hint.** VTE 0.76 has *no* API for "is the child mouse-reporting"
— no property, no signal (checked by introspection) — and ttyga never sees the
output stream, so the state cannot be read. It is inferred instead: a button-1
drag over `SELECT_HINT_MIN_PX` (20) that leaves no selection means something ate
it, and an `Adw.Toast` says so, rate-limited to one per terminal per 30 s. The
probe is a `Gtk.EventControllerLegacy` at CAPTURE that always returns `False` —
deliberately **not** a `Gtk.Gesture`, which would join VTE's grouping and could
claim the selection sequence, the same failure `PANE_GUTTER` works around. New
`Adw.ToastOverlay` wraps `split_view`; it was the first toast in the app.

The pane-bar was the obvious home for a state-aware hint and is the wrong one —
it is hidden whenever a tab has a single pane, which is the case that matters.

New driver `tests/test_mouse_grab_hint.py` (16/16) covers the controller phases
and every suppression branch. All six green: 27/27, 13/13, 23/23, 31/31, 19/19,
16/16. CLAUDE.md gained a "Mouse-reporting programs and the Shift override"
section and a sixth test row; TTYGA.md gained two shortcut rows and a paragraph.

**Committed as `a2d5945`; push BLOCKED on the email-privacy question above** — not a
code problem, and the tree is clean apart from STATE.md and the usual untracked cruft.

Late catch during the pre-commit diff review: the hint cooldown was first written
as `self._select_hint_at`, an instance dict keyed by `Vte.Terminal` and never
pruned — an unbounded dict of dead widgets, in the project that already ate 28 GB
of RSS to a leaked GSource. Moved into the pane's `self.tabs` metadata as
`select_hint_at`, which the two existing `self.tabs.pop()` sites already reap. Do
not reintroduce a lifetime-keyed dict beside `self.tabs`; there is no third reap site
to add it to.

Also noticed, not fixed: `./install.sh --dry-run` prints that it
would install `profiles.yaml.example` over `~/.config/ttyga/profiles.yaml`. The
real run is correctly guarded (`install.sh:79`) and will not touch it — but the
dry run says otherwise, which is exactly backwards for a safety net.

## 2026-09-04 (sidebar: "open elsewhere" mark — 0.6.60)

Greg asked why a sidebar row's highlight vanishes when he switches to a plain
tab, and whether that was intentional. **It was**, and it is documented: the CSS
comment at the BEL-pulse block says the accent means "currently on screen". The
original v0.5.0 code kept a single `self.active_btn`; 0.6.36 widened it to a set
only because a merged tab hosts several profiles. The semantics never changed.

The gap is real though — nothing marked a profile running in a *background*
tab except a tooltip you had to hover to find, so from a plain tab the sidebar
claimed nothing was running.

**Added a second, quieter state (`0.6.60`).** `.profile-row.open` tints label
and icon `row_open_fg` with no fill; `.active` keeps the solid accent and wins
on source order. Mutually exclusive by construction (`open_keys -= keys`).

- New palette key `row_open_fg` in all three themes. Its own key rather than
  reusing `accent` because this is text on `bg_sidebar`, not a fill: light's
  `accent` only reaches 3.2:1 there, so light darkens to `#1c64c4` (4.8:1).
  dark/nord just reuse their `accent` (7.1:1 / 6.7:1).
- NOT a left accent bar — that edge is already the per-profile `color:`
  `border-left`.
- `.active` also gained `font-weight: 600`, matching `.open`, so a row's metrics
  don't jitter as you tab around. **This is a visual change to an existing
  state; Greg has not seen it yet.**
- `.profile-row.open.attention` hands the tint back to theme `fg` — a BEL
  comes from a background tab by definition, so `.open` + pulse is now the
  common case, and `row_open_fg` over the `term_warn` peak is ~1.4:1 in light.
  The `.active` + pulse pairing Greg signed off on 2026-08-04 is untouched.

Three paths change the current tab without emitting `switch-page` and each had
to recompute by hand: closing a background page, closing the last page, and
`_switch_to_profile_tab()` landing on the page already current. The first was
previously only refreshing tooltips and attention — fixed. Added a belt-and-
braces `_refresh_sidebar_highlight()` after the startup restore loop.

New driver `tests/test_sidebar_open_marks.py` (19/19) covers exactly those
bypass paths plus a mid-session `reload_profiles()` rebuild. Existing tests
still green (27/27, 23/23). CLAUDE.md gained a "Sidebar running-state marks"
section and a third test-script row.

Committed local-only as **f719304**, not pushed.

**Also found and fixed (da9aeb5):** CLAUDE.md's test list was two scripts stale
— `test_sidebar_switch.py` and `test_stopwatch.py` landed in 416c896 / 3f2a063
without the list being updated, so this session initially wrote "three
standalone scripts" on the strength of it. There are **five**, all green as of
today: 27/27, 13/13, 23/23, 31/31, 19/19. The list now carries a note to keep
it in step with `tests/`.

**Outstanding:** install, eyeball, push. Nothing is blocked.

## 2026-09-03 (scroll — root-caused, instrumented, tested; committed + pushed 416c896)

**Wheel/trackpad now scrolls inside full-screen programs (0.6.59).** One commit
`416c896` covers 0.6.56–0.6.59 (sidebar click-to-switch + scroll fix +
`--new-instance`), pushed to `origin/master`. STATE.md left uncommitted per
contract.

Greg's complaint: in a `claude` session the wheel does nothing; only
PageUp/PageDown (claude's own pager). **Not a regression from any ttyga edit** —
`_on_terminal_scroll` is byte-identical to the v0.5.0 initial commit: always
returned True unconditionally and nudged `terminal.get_vadjustment()` (there is
no `Gtk.ScrolledWindow` anywhere in ttyga). What changed is *underneath*: Claude
Code moved to the **alternate screen**, where the VTE pane has **zero scrollback
extent** (`adj.upper == adj.page_size`). ttyga's unconditional "eat the wheel +
nudge a dead adjustment" then became pure loss — and blocked VTE from forwarding
the wheel to claude.

Proven with a live instrumented `--new-instance` build (`logging.warning` in
every scroll/key handler) + throwaway `diag_scroll3.py` driver: `less`/`claude`
pane → `extent=0`; plain flooded pane → `extent≈4962` and `set_value` scrolls
fine.

**Fix — `_has_scrollback(terminal)` gate (~ttyga.py:4079):** all three ttyga
scroll paths (`_on_terminal_scroll`, `_on_terminal_scroll_capture`,
`_on_terminal_scroll_key`) now return **False** (do not consume) when
`adj.upper - adj.page_size < 1`. The event then reaches VTE, which forwards it to
the foreground program. Only a pane with real off-screen rows gets ttyga's own
buffer scroll.

Greg tested via proxies, no real work: `seq … | less --mouse` (alt + mouse),
`seq … | less` (alt, no mouse), `seq 5000` (plain):
- plain wheel — works in **all three** now (alt-screen via VTE→program; plain via
  ttyga). This is the actual fix.
- Shift+wheel / Shift+PageUp/End — work in the **plain** pane only. In alt-screen
  programs they're forwarded but `less`/`claude` don't bind them; **unfixable and
  fine** — plain wheel + the program's own PageUp/PageDown cover it.

Also uncommitted from this session, folded into 0.6.57–0.6.59:
- CAPTURE-phase `_on_terminal_scroll_capture` (BOTH_AXES) + `_on_terminal_scroll_key`
  (Shift+Page/Home/End). Kept; only act in a plain tab now.
- `--new-instance` CLI flag (0.6.58): a second instance against the real
  `~/.config/ttyga`. Both instances race `app_state.json` on close — last wins.

SCROLLDIAG logging stripped. Headless test still 27/27.

## 2026-09-02

**Sidebar click raises an already-open profile's tab (0.6.56). Built, 31/31 on a
new driver, NOT committed and not yet seen in the real app.**

All five open questions from yesterday were settled with Greg first: cycle by
page order; Ctrl+click as the force-new-tab escape hatch; a `sidebar_click`
setting (switch | launch, default switch) in Preferences → Profiles & state;
the matching *pane* takes focus in a merged tab. He added a sixth — a
state-aware tooltip on the row, since Ctrl+click has nowhere else to be
discovered. It is absent while the profile is closed, and reads "Switch to this
tab — Ctrl+click for a new one" or "Cycle N open tabs — …".

**The `todo` entry was wrong about clippets and the plan inherited it.** It said
to exempt `type == 'clippet'` wholesale. ttyga has exactly two profile types, so
that exempts every local profile — 27 of the 51 in Greg's own config, including
the Claude and Project folders groups that are literally the "two projects open
in tabs" the request came from. Caught while seeding the test config. The rule is
now `_always_launches(p)`: clippet AND `options.in_place` — only two profiles in
his config, both genuinely tabless. One predicate shared by the handler and the
tooltip.

Mechanism worth not re-deriving: `_switch_to_profile_tab` sets
`self._active_terminal` *before* `set_current_page`, because `_on_tab_switched`
keeps that terminal when it belongs to the incoming tab. That single ordering is
what makes the merged-tab pane focus work — no new plumbing. And
`_refresh_sidebar_tooltips()` has to be called by hand in `_do_close_tab` after
`remove_page`, for the reason already commented there for the attention pulse:
closing a tab that is not on screen emits no switch-page.

`tests/test_sidebar_switch.py` is new (31/31); the other two scripts still pass
(27/27, 23/23). The Ctrl modifier itself is the one uncovered path — the driver
sets `_force_new_tab` directly, so the CAPTURE-phase gesture is verified as API
only. A real ttyga (0.6.55) is running from `~/.local/bin`, so seeing 0.6.56
means quitting it and running `python3 ttyga.py`, or `./install.sh`.

## 2026-09-01

**New priority item logged in `todo`, no code written.** Greg asked for it: with
two projects open in tabs, clicking a sidebar row spawns a third tab instead of
switching to the one already running that profile. Wanted behaviour is switch-if-
open, launch-if-not.

The mechanism is already in the file — `_update_sidebar_highlight` (~ttyga.py:4165)
computes the same `(name, group)` key per live pane that the lookup needs, so the
work is a scan of `self.tabs` plus `set_current_page` and the usual double-idle
focus grab in `on_profile_clicked` (~ttyga.py:5763).

Five open questions written into the `todo` entry rather than guessed at: tie-break
when a profile is open in several tabs, how to deliberately force a second tab once
the default changes (Ctrl/middle-click?), setting vs hardcoded, focusing the right
*pane* in a merged tab, and exempting clippets. These need Greg's answers before
implementation starts.

## 2026-08-19 (shipped)

**Sidebar stopwatch (0.6.55) — Greg looked at it, confirmed fine, no changes requested.** Committed (`3f2a063`, on top of the STATE.md-contract commit `d2c0ef7`) and pushed to `origin/master`. The narrow-sidebar digit-shrink cost flagged during review was accepted as-is — no fallback layout needed. `stopwatch-plan.md` and its memory entry are now historical record only; nothing further to do on this feature.

## 2026-08-19 (implementation)

**Sidebar stopwatch (0.6.55) implemented in `ttyga.py`, built as written from
`stopwatch-plan.md` — not committed.** All four behaviour points flagged in the
morning's review entry below were carried through unchanged: layout (single block
right of the clock, one/three buttons), the two corrected handler rules (open only
starts a genuinely fresh stopwatch; `HH:MM:SS` zero-padded), the running-in-background
indicator (`.suggested-action` on the trigger button, using the existing wired CSS
class rather than inventing one), and both custom glyphs (`ttyga-stopwatch-symbolic`,
`ttyga-clock-symbolic` — not `alarm-symbolic`).

`_draw_fitted_text`'s `pad` dropped 8→4 as the plan specified. `APP_VERSION` → 0.6.55.

New: `tests/test_stopwatch.py` (13/13, headless, pure-function — `_stopwatch_seconds`/
`_format_stopwatch` via a `SimpleNamespace` stand-in for `self` and a mocked
`time.monotonic`, no GTK instance needed). A one-off driver script (not kept)
confirmed both custom icons resolve via `Gtk.IconTheme.has_icon()` and drove the
full open→pause→switch-to-clock→reopen→reset cycle live against a real `DevFrame`
(`NON_UNIQUE` flag, per the existing driver-script gotcha) — 17/17, including the
exact regression the plan called out: reopening a paused stopwatch after switching
to clock leaves it paused at the same value rather than resuming it. Existing suites
(`test_bg_image_css.py` 27/27, `test_pane_margins.py` 23/23) still pass — nothing
disturbed.

**Not done: appearance is unverified.** No screenshot tool works here (see the GTK4
gotchas in `[[project-ttyga-state]]`). Outstanding before this ships: the manual
pass in `stopwatch-plan.md`'s Verification section, run in front of Greg — sidebar
drag between 200–360px (the known digit-shrink cost), theme switching (glyph
recolouring), and whether `.suggested-action` actually reads well combined with
`.flat` on a small icon button (untested combination, flagged as a risk in the plan
review, never rendered and looked at).

## 2026-08-19 (design + review)

**Sidebar stopwatch designed, reviewed, and specced — no code written yet.** Spec
lives in the repo root as `stopwatch-plan.md` (untracked), indexed as open item 1 in
`todo`. Greg settled the layout over three rounds of mockups, then had the first
draft critiqued; the plan in the tree is the *revised* one and is ready to build cold.

What the review changed, and why a builder shouldn't quietly "improve" it back:

- **Layout is Greg's call.** All four glyphs sit in one block to the right of the
  time/date column — one start button in clock mode, three stacked (pause/resume,
  reset, switch-to-clock) in stopwatch mode. He explicitly rejected the date-line
  inline placement and a date-sized glyph.
- **The clock digits shrink and that is known and accepted.** `_draw_fitted_text`
  sizes fonts from available *width*, so the control block costs −23% at the 200px
  `SIDEBAR_MIN_W` and nothing at 360px (height-capped there). Measured with Pango and
  tabulated in the plan — don't re-measure, don't treat it as a defect. Plan is to
  build it, drop `pad` 8→4, and have Greg look; a fallback layout that removes the
  cost entirely is documented if he dislikes it.
- **Two behaviour rules are load-bearing** because the first draft had them wrong:
  the open handler starts the count only when `_stopwatch_elapsed == 0.0 and not
  running` (otherwise reopening a deliberately paused stopwatch silently resumes it),
  and `_format_stopwatch` zero-pads to `HH:MM:SS` (character count drives font size,
  so unpadded hours jump 14% larger at 10:00:00).
- `clock-symbolic` is **not in Adwaita** (Yaru only, verified), so the plan adds a
  second custom glyph rather than using `alarm-symbolic`, which is a bell.

Outstanding on completion: bump `APP_VERSION` to 0.6.55, and have Greg confirm the
appearance by eye (screenshots don't work here). Version 0.6.54 is still what's
committed; working tree otherwise unchanged from 2026-08-17 apart from the two new
untracked files (`stopwatch-plan.md`, and the `todo` index edit).

## 2026-08-17

Working tree is clean apart from untracked scratch: `todo` (a running work log/spec file, see below), `seasonal_icon.md`, and four PNGs (`problem.png`, `solution.png`, two `loupe-*.png` screenshots) whose purpose isn't recorded anywhere in the repo, `todo`, or memory — likely design-discussion artifacts, not yet triaged.

**Current version: 0.6.54**, committed and pushed (`98c32d9`), plus one follow-up test-script commit (`cb239f7`). No code changes pending.

0.7.0 shipped Feature A (background image behind terminal panes, 0.6.53) and its gutter-camouflage fix (0.6.54). **Feature B (transparency through to the desktop) is unblocked and fully spec'd** (see the end of the repo-root `todo`) but Greg explicitly deferred it — "not exactly an everyday requirement" — and it must not be started unasked.

**`seasonal_icon.md`** (untracked, dated 2026-08-04) is a from-scratch design brief for a seasonal-palette app-icon feature (SVG regeneration by season, template-based, four palettes given). Not started, not referenced in `todo` or memory. Grepping `ttyga.py` for "season" finds nothing — this is a pure idea sitting in the working tree, not in-progress work.

No open bugs are urgent; the known-issues list (font zoom delta, OSC 7 on RHEL, no terminal scrollbar, deferred config migration) is stable and none are currently biting. Full detail lives in `[[project_ttyga_state]]` (project memory) — read that before resuming, it is far more complete than this file.
