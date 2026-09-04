#!/usr/bin/env python3
"""Driver: verify the sidebar's two "running" marks — .active and .open.

    python3 tests/test_sidebar_open_marks.py

NOT a headless unit test. Same harness contract as test_pane_margins.py —
read ITS docstring first; the three traps it records (NON_UNIQUE,
faulthandler, deferred assertions) apply here unchanged.

What this covers: .active marks a profile open in the tab ON SCREEN, .open
marks one running in some OTHER tab. The interesting cases are all the
places that change which tab is current WITHOUT emitting switch-page, since
each one has to recompute the marks by hand:

  * closing a background tab (the current child never changes)
  * closing the last tab (there is no page left to switch to)
  * rebuilding the sidebar mid-session (_build_sidebar throws the buttons
    away, so the marks have to be re-derived from the key sets)

Asserts on CSS classes, never on appearance — screenshots cannot be
captured from a script on this setup.
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

_tmp = tempfile.TemporaryDirectory(prefix='ttyga_sidebartest_')
ttyga.CONFIG_DIR    = Path(_tmp.name)
ttyga.CONFIG_FILE   = ttyga.CONFIG_DIR / 'profiles.yaml'
ttyga.SETTINGS_FILE = ttyga.CONFIG_DIR / 'settings.yaml'
ttyga.STATE_FILE    = ttyga.CONFIG_DIR / 'app_state.json'
ttyga.LEGACY_CONFIG = Path('/dev/null')

# Two ordinary local profiles plus one in-place clippet, which owns no tab of
# its own and so must never be marked .open.
ttyga.CONFIG_FILE.write_text("""
groups:
- Lab
profiles:
- name: Alpha
  group: Lab
  type: clippet
  options:
    command: 'true'
- name: Beta
  group: Lab
  type: clippet
  options:
    command: 'true'
- name: Inline
  group: Lab
  type: clippet
  options:
    command: 'true'
    in_place: true
""")

results = []


def check(name, ok, detail=''):
    results.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}{'  — ' + detail if detail else ''}",
          flush=True)


def profile(app, name):
    cfg = app.load_resolved_config()
    return next(p for p in cfg['profiles'] if p['name'] == name)


def row(app, name):
    """The sidebar button for a profile, by name."""
    return next(b for b, p in app._profile_buttons if p.get('name') == name)


def classes(app, name):
    c = row(app, name).get_css_classes()
    return ('active' in c, 'open' in c)


def expect(label, app, name, want):
    got = classes(app, name)
    check(label, got == want, f"want {want} got {got}")


class Runner:
    def __init__(self, app):
        self.app = app
        self.steps = [
            self.step_one_tab,
            self.step_second_tab_demotes_first,
            self.step_plain_tab_keeps_both_open,
            self.step_switch_back,
            self.step_close_background_tab,
            self.step_sidebar_rebuild,
            self.step_in_place_clippet_never_open,
            self.step_close_everything,
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

    # --- steps ---------------------------------------------------------
    def step_one_tab(self):
        app = self.app
        app.add_tab(profile=profile(app, 'Alpha'))
        expect('one tab: its profile is .active', app, 'Alpha', (True, False))
        expect('one tab: the other profile is unmarked', app, 'Beta', (False, False))

    def step_second_tab_demotes_first(self):
        app = self.app
        app.add_tab(profile=profile(app, 'Beta'))
        expect('second tab: the new one is .active', app, 'Beta', (True, False))
        expect('second tab: the first demotes to .open', app, 'Alpha', (False, True))

    def step_plain_tab_keeps_both_open(self):
        # The bug this feature exists for: a plain tab used to blank the
        # sidebar entirely, reading as "nothing is running".
        app = self.app
        app.add_tab()
        expect('plain tab: Alpha stays .open', app, 'Alpha', (False, True))
        expect('plain tab: Beta stays .open',  app, 'Beta',  (False, True))

    def step_switch_back(self):
        app = self.app
        app.notebook.set_current_page(0)
        expect('back on tab 0: Alpha is .active again', app, 'Alpha', (True, False))
        expect('back on tab 0: Beta is .open',          app, 'Beta',  (False, True))
        check('a row is never both .active and .open',
              not any(a and o for a, o in
                      (classes(app, n) for n in ('Alpha', 'Beta'))))

    def step_close_background_tab(self):
        # Closing a page other than the current one emits no switch-page, so
        # Beta's .open tint has to be cleared by hand or it never goes away.
        app = self.app
        beta_root = next(m['tab_root'] for m in app.tabs.values()
                         if (m.get('profile') or {}).get('name') == 'Beta')
        app._do_close_tab(beta_root)
        expect('closed background tab: Beta is unmarked', app, 'Beta', (False, False))
        expect('closed background tab: Alpha still .active', app, 'Alpha', (True, False))

    def step_sidebar_rebuild(self):
        # _build_sidebar discards every button; the marks must come back from
        # the key sets rather than being lost until the next tab switch.
        app = self.app
        app.add_tab(profile=profile(app, 'Beta'))     # Beta current, Alpha background
        app.reload_profiles()
        expect('after rebuild: Beta is still .active', app, 'Beta',  (True, False))
        expect('after rebuild: Alpha is still .open',  app, 'Alpha', (False, True))

    def step_in_place_clippet_never_open(self):
        # An in-place clippet feeds the terminal already on screen and owns no
        # tab, so there is nowhere to switch to and nothing to mark.
        app = self.app
        expect('in-place clippet: unmarked', app, 'Inline', (False, False))

    def step_close_everything(self):
        app = self.app
        for root in [app.notebook.get_nth_page(i)
                     for i in range(app.notebook.get_n_pages())]:
            app._do_close_tab(root)
        check('all tabs closed: no pages left', app.notebook.get_n_pages() == 0,
              f"{app.notebook.get_n_pages()}")
        expect('last tab closed: Alpha unmarked', app, 'Alpha', (False, False))
        expect('last tab closed: Beta unmarked',  app, 'Beta',  (False, False))
        check('last tab closed: both key sets empty',
              not app.active_profile_keys and not app.open_profile_keys,
              f"{app.active_profile_keys} {app.open_profile_keys}")
        check('last tab closed: both button sets empty',
              not app.active_btns and not app.open_btns)

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
