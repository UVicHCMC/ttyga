#!/usr/bin/env python3
"""Driver: the mouse-grab select hint, and the controller phases it rides on.

    python3 tests/test_mouse_grab_hint.py

NOT a headless unit test — it opens a window. Follows the harness rules in
test_pane_margins.py (NON_UNIQUE, faulthandler, deferred assertions).

Two seams, both invisible when they break:

  1. Ctrl+Shift+C/V lives on a CAPTURE-phase key controller. At the default
     bubble phase VTE's own controller claims the key first and writes it to
     the child, so copy/paste silently stops working inside any full-screen
     program. Regressing this looks like nothing at all from a test that only
     checks the handler.
  2. _on_terminal_mouse_event must ALWAYS return False and must never be a
     Gtk.Gesture — a gesture would join VTE's grouping and could swallow the
     selection sequence. That failure looks like "selection is broken
     sometimes", which no assertion here would otherwise catch.

The hint's own decision logic is driven with stub events; PyGObject cannot
construct a Gdk.Event, and duck typing is enough — the handler only ever
calls the four accessors stubbed below.
"""
import faulthandler
import sys
import tempfile
from pathlib import Path

faulthandler.enable()

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Vte', '3.91')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Gdk, GLib, Gio

import ttyga

_tmp = tempfile.TemporaryDirectory(prefix='ttyga_hinttest_')
ttyga.CONFIG_DIR    = Path(_tmp.name)
ttyga.CONFIG_FILE   = ttyga.CONFIG_DIR / 'profiles.yaml'
ttyga.SETTINGS_FILE = ttyga.CONFIG_DIR / 'settings.yaml'
ttyga.STATE_FILE    = ttyga.CONFIG_DIR / 'app_state.json'
ttyga.QUOTA_FILE    = ttyga.CONFIG_DIR / 'quota.json'
ttyga.LEGACY_CONFIG = Path('/dev/null')

PX = ttyga.SELECT_HINT_MIN_PX
results = []


def check(name, ok, detail=''):
    results.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}{'  — ' + detail if detail else ''}",
          flush=True)


class StubEvent:
    """Only the four accessors _on_terminal_mouse_event actually calls."""
    def __init__(self, etype, button=Gdk.BUTTON_PRIMARY, pos=(0.0, 0.0), mods=0):
        self._t, self._b, self._p, self._m = etype, button, pos, mods

    def get_event_type(self):     return self._t
    def get_button(self):         return self._b
    def get_position(self):       return (True, self._p[0], self._p[1])
    def get_modifier_state(self): return self._m


class StubController:
    """Stands in for the Gtk.EventControllerLegacy; only get_widget is used."""
    def __init__(self, widget): self._w = widget
    def get_widget(self):       return self._w


def controllers(widget, cls):
    model = widget.observe_controllers()
    return [model.get_item(i) for i in range(model.get_n_items())
            if isinstance(model.get_item(i), cls)]


class Runner:
    def __init__(self, app):
        self.app = app
        self.toasts = []
        self.steps = [
            self.step_setup,
            self.step_phases,
            self.step_hint_fires,
            self.step_hint_suppressed,
            self.step_cooldown,
            self.finish,
        ]

    def pump(self):
        step = self.steps.pop(0)
        try:
            step()
        except Exception:
            import traceback
            traceback.print_exc()
            check('driver crashed', False)
            self.steps = [self.finish]
        if self.steps:
            GLib.timeout_add(600, lambda: self.pump() and False)
        return False

    # --- helpers -------------------------------------------------------
    def drag(self, dx, dy, mods=0, button=Gdk.BUTTON_PRIMARY, press=True):
        """Feed a press/release pair; returns the handlers' return values."""
        app, term, ctl = self.app, self.term, StubController(self.term)
        rv = []
        if press:
            rv.append(app._on_terminal_mouse_event(
                ctl, StubEvent(Gdk.EventType.BUTTON_PRESS, button, (10.0, 10.0), mods)))
        rv.append(app._on_terminal_mouse_event(
            ctl, StubEvent(Gdk.EventType.BUTTON_RELEASE, button,
                           (10.0 + dx, 10.0 + dy), mods)))
        return rv

    def reset_cooldown(self):
        """The cooldown rides on tab metadata, not an instance dict — see
        _on_terminal_mouse_event."""
        self.app.tabs[self.term].pop('select_hint_at', None)

    def fired(self):
        n = len(self.toasts)
        self.toasts.clear()
        return n

    # --- steps ---------------------------------------------------------
    def step_setup(self):
        app = self.app
        app.add_tab()
        root = app.notebook.get_nth_page(app.notebook.get_current_page())
        self.term = list(app._all_terminals_in(root))[0]
        app._toast = lambda msg, timeout=4: self.toasts.append(msg)
        check('setup: one terminal', self.term is not None)

    def step_phases(self):
        term = self.term
        keys = controllers(term, Gtk.EventControllerKey)
        phases = [k.get_propagation_phase() for k in keys]
        cap = phases.count(Gtk.PropagationPhase.CAPTURE)
        # ttyga adds two: Ctrl+Shift+C/V, and Shift+Page/Home/End scrollback.
        check("ttyga's two key controllers are both CAPTURE", cap == 2,
              str([p.value_nick for p in phases]))
        # VTE installs its own at BUBBLE. Capture always runs first regardless
        # of which was added when — that is the whole reason ours are capture.
        check("VTE's own key controller is at BUBBLE, behind ours",
              Gtk.PropagationPhase.BUBBLE in phases,
              str([p.value_nick for p in phases]))

        legacy = controllers(term, Gtk.EventControllerLegacy)
        check('exactly one legacy mouse probe', len(legacy) == 1, f"{len(legacy)}")
        check('mouse probe is CAPTURE phase',
              bool(legacy) and
              legacy[0].get_propagation_phase() == Gtk.PropagationPhase.CAPTURE)
        # A Gtk.Gesture here would join VTE's grouping and could steal the
        # selection sequence — see the module docstring.
        check('mouse probe is not a Gtk.Gesture',
              bool(legacy) and not isinstance(legacy[0], Gtk.Gesture))

    def step_hint_fires(self):
        self.reset_cooldown()
        rv = self.drag(PX + 5, 0)
        check('drag past threshold with no selection hints', self.fired() == 1)
        check('handler always returns False', rv == [False, False], str(rv))

        self.reset_cooldown()
        self.drag(0, PX + 5)
        check('vertical drag counts too', self.fired() == 1)

    def step_hint_suppressed(self):
        app = self.app

        self.reset_cooldown()
        self.drag(PX - 1, PX - 1)
        check('drag under threshold stays quiet', self.fired() == 0)

        self.reset_cooldown()
        self.drag(PX + 5, 0, mods=Gdk.ModifierType.SHIFT_MASK)
        check('Shift-drag stays quiet — Shift is the fix, not the symptom',
              self.fired() == 0)

        self.reset_cooldown()
        self.drag(PX + 5, 0, button=Gdk.BUTTON_MIDDLE)
        check('middle-button drag is ignored', self.fired() == 0)

        self.reset_cooldown()
        self.drag(PX + 5, 0, press=False)
        check('release with no matching press stays quiet', self.fired() == 0)

        # A terminal that really did select something is behaving normally.
        self.reset_cooldown()
        self.term.feed(b'selectable text\r\n')
        self.term.select_all()
        if self.term.get_has_selection():
            self.drag(PX + 5, 0)
            check('a drag that produced a selection stays quiet', self.fired() == 0)
        else:
            check('a drag that produced a selection stays quiet', False,
                  'could not establish a selection to test with')
        self.term.unselect_all()

    def step_cooldown(self):
        app = self.app
        self.reset_cooldown()
        self.drag(PX + 5, 0)
        first = self.fired()
        self.drag(PX + 5, 0)
        second = self.fired()
        check('first qualifying drag hints', first == 1, f"{first}")
        check('second within the cooldown does not repeat', second == 0, f"{second}")

    def finish(self):
        def done():
            bad = [n for n, ok, _ in results if not ok]
            print(f"\n{len(results) - len(bad)}/{len(results)} passed", flush=True)
            if bad:
                print("FAILED: " + '; '.join(bad), flush=True)
            self.app.quit()
            return False
        GLib.timeout_add(700, done)


def main():
    app = ttyga.DevFrame()
    app.set_flags(Gio.ApplicationFlags.NON_UNIQUE)

    def on_activate(a):
        GLib.timeout_add(800, lambda: Runner(a).pump() and False)

    app.connect('activate', on_activate)
    app.run(None)
    bad = [n for n, ok, _ in results if not ok]
    sys.exit(1 if (bad or not results) else 0)


main()
