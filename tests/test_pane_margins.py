#!/usr/bin/env python3
"""Driver: verify pane margins are applied only on edges abutting a separator.

    python3 tests/test_pane_margins.py

NOT a headless unit test. This drives the REAL app — it opens a window, so
it needs a live session, and it steps through GLib timeouts, so it takes a
few seconds and is timing-sensitive by construction.

Three things this harness gets right that cost real time to rediscover;
copy them into any new driver:

  1. Gio.ApplicationFlags.NON_UNIQUE before run(). Without it, if a real
     ttyga is already running, this becomes a remote D-Bus client and exits
     with NO OUTPUT and STATUS 0 — it looks like the script did nothing
     rather than like an error.
  2. faulthandler.enable(). Anything touching the pane tree is
     segfault-adjacent (see the Paned child-detachment note in CLAUDE.md).
  3. Assertions are DEFERRED (see Runner.later). _split_pane and
     _do_close_pane hand _update_pane_bars to a GLib idle, so asserting in
     the same main-loop turn reads pre-update state and reports a failure
     that isn't real.

Asserts on widget state and CSS classes, never on appearance — screenshots
cannot be captured from a script on this setup (Mutter has no
wlr-screencopy; gnome-screenshot returns stale frames).
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
from gi.repository import Gtk, GLib, Gio

import ttyga

_tmp = tempfile.TemporaryDirectory(prefix='ttyga_margintest_')
ttyga.CONFIG_DIR    = Path(_tmp.name)
ttyga.CONFIG_FILE   = ttyga.CONFIG_DIR / 'profiles.yaml'
ttyga.SETTINGS_FILE = ttyga.CONFIG_DIR / 'settings.yaml'
ttyga.STATE_FILE    = ttyga.CONFIG_DIR / 'app_state.json'
ttyga.LEGACY_CONFIG = Path('/dev/null')

G = ttyga.PANE_GUTTER
results = []


def check(name, ok, detail=''):
    results.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}{'  — ' + detail if detail else ''}",
          flush=True)


def margins(t):
    return (t.get_margin_start(), t.get_margin_end(), t.get_margin_bottom())


def expect(name, term, want):
    got = margins(term)
    check(name, got == want, f"want {want} got {got}")


def terms_of(app, root):
    return list(app._all_terminals_in(root))


def current_root(app):
    return app.notebook.get_nth_page(app.notebook.get_current_page())


class Runner:
    def __init__(self, app):
        self.app = app
        self.steps = [
            self.step_single,
            self.step_split_h,
            self.step_split_v,
            self.step_nested,
            self.step_close_back_to_one,
            self.step_merge,
            self.step_restore,
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
            GLib.timeout_add(700, lambda: self.pump() and False)
        return False

    @staticmethod
    def later(fn, ms=250):
        """_split_pane / _do_close_pane defer _update_pane_bars to an idle;
        assert after it has landed, not in the same main-loop turn."""
        GLib.timeout_add(ms, lambda: fn() and False)

    # --- steps ---------------------------------------------------------
    def step_single(self):
        app = self.app
        app.add_tab()
        root = current_root(app)
        ts = terms_of(app, root)
        check('single pane: one terminal', len(ts) == 1, f"{len(ts)}")
        expect('single pane: no margins at all', ts[0], (0, 0, 0))
        check('single pane: pane-bar hidden',
              not app.tabs[ts[0]]['pane_bar'].get_visible())

    def step_split_h(self):
        app = self.app
        app._split_pane(Gtk.Orientation.HORIZONTAL)

        def verify():
            ts = terms_of(app, current_root(app))
            check('h-split: two terminals', len(ts) == 2, f"{len(ts)}")
            left, right = ts
            expect('h-split left: inset on the right edge only',  left,  (0, G, 0))
            expect('h-split right: inset on the left edge only', right, (G, 0, 0))
            check('h-split: pane-bars shown',
                  all(app.tabs[t]['pane_bar'].get_visible() for t in ts))
        self.later(verify)

    def step_split_v(self):
        # New tab, vertical split.
        app = self.app
        app.add_tab()
        app._split_pane(Gtk.Orientation.VERTICAL)

        def verify():
            ts = terms_of(app, current_root(app))
            check('v-split: two terminals', len(ts) == 2, f"{len(ts)}")
            top, bottom = ts
            expect('v-split top: inset on the bottom edge only', top,    (0, 0, G))
            expect('v-split bottom: no margins',                 bottom, (0, 0, 0))
        self.later(verify)

    def step_nested(self):
        # New tab: split horizontally, then split the RIGHT pane vertically.
        app = self.app
        app.add_tab()
        app._split_pane(Gtk.Orientation.HORIZONTAL)
        root = current_root(app)
        ts = terms_of(app, root)
        app._active_terminal = ts[1]          # the right-hand pane
        app._split_pane(Gtk.Orientation.VERTICAL)

        def verify():
            ts = terms_of(app, current_root(app))
            check('nested: three terminals', len(ts) == 3, f"{len(ts)}")
            left, rtop, rbot = ts
            expect('nested left: right edge only',    left, (0, G, 0))
            expect('nested right-top: left + bottom', rtop, (G, 0, G))
            expect('nested right-bottom: left only',  rbot, (G, 0, 0))
        self.later(verify)

    def step_close_back_to_one(self):
        app = self.app
        ts = terms_of(app, current_root(app))
        app._do_close_pane(ts[1])
        app._do_close_pane(terms_of(app, current_root(app))[1])
        # _do_close_pane defers _update_pane_bars to an idle; let it land.
        def after():
            ts2 = terms_of(app, current_root(app))
            check('after closes: one terminal left', len(ts2) == 1, f"{len(ts2)}")
            if ts2:
                expect('promoted sole pane: margins cleared', ts2[0], (0, 0, 0))
            return False
        GLib.timeout_add(250, after)

    def step_merge(self):
        # The inverse case only merge produces: two single-pane tabs, both at
        # zero margins, become one split and must GAIN the facing insets.
        app = self.app
        app.add_tab()
        target = app._get_active_terminal()
        app.add_tab()
        source = app._get_active_terminal()
        pre = (margins(target), margins(source))
        check('merge: both panes start with no margins',
              pre == ((0, 0, 0), (0, 0, 0)), f"{pre}")
        app._merge_tab_into(source, target)

        def verify():
            root = app.tabs[target]['tab_root']
            ts = terms_of(app, root)
            check('merge: two terminals in the target tab', len(ts) == 2, f"{len(ts)}")
            if len(ts) == 2:
                expect('merged first pane: gained an inset', ts[0], (0, G, 0))
                expect('merged second pane: gained an inset', ts[1], (G, 0, 0))
        self.later(verify)

    def step_restore(self):
        # Serialise a split tab, restore it, and confirm the restored panes
        # get margins too — the path most likely to be forgotten.
        app = self.app
        app.add_tab()
        app._split_pane(Gtk.Orientation.HORIZONTAL)
        root = current_root(app)
        layout = app._serialise_tab(root)
        cfg = app.load_resolved_config()
        by_key = {(p.get('name'), p.get('group', 'General')): p
                  for p in cfg.get('profiles', [])}
        app._restore_tab(layout, by_key, focus=True)

        def after():
            r = current_root(app)
            ts = terms_of(app, r)
            check('restored split: two terminals', len(ts) == 2, f"{len(ts)}")
            if len(ts) == 2:
                expect('restored left: right edge only', ts[0], (0, G, 0))
                expect('restored right: left edge only', ts[1], (G, 0, 0))
            return False
        GLib.timeout_add(600, after)

    def finish(self):
        def done():
            bad = [n for n, ok, _ in results if not ok]
            print(f"\n{len(results) - len(bad)}/{len(results)} passed", flush=True)
            if bad:
                print("FAILED: " + '; '.join(bad), flush=True)
            self.app.quit()
            return False
        GLib.timeout_add(900, done)


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
