#!/usr/bin/env python3
"""Driver: sidebar click raises an already-open profile's tab instead of
launching another one.

    python3 tests/test_sidebar_switch.py

NOT a headless unit test — it drives the REAL app, opens a window, and steps
through GLib timeouts. See tests/test_pane_margins.py for the three setup
traps this harness copies (NON_UNIQUE, faulthandler, deferred assertions).

Covers the six decisions the feature was specified against:
  cycling by page order, Ctrl+click forcing a new tab, the sidebar_click
  setting, the matching pane taking focus in a merged tab, in-place clippets
  always launching, and the state-aware tooltip.

Asserts on notebook pages, self.tabs and tooltip strings — never appearance.
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

_tmp = tempfile.TemporaryDirectory(prefix='ttyga_switchtest_')
ttyga.CONFIG_DIR    = Path(_tmp.name)
ttyga.CONFIG_FILE   = ttyga.CONFIG_DIR / 'profiles.yaml'
ttyga.SETTINGS_FILE = ttyga.CONFIG_DIR / 'settings.yaml'
ttyga.STATE_FILE    = ttyga.CONFIG_DIR / 'app_state.json'
ttyga.LEGACY_CONFIG = Path('/dev/null')

# Shaped like Greg's real config: the "projects" are plain clippets, not SSH.
ttyga.CONFIG_FILE.write_text("""
profiles:
  - name: Alpha
    group: Projects
    type: clippet
    options:
      command: 'cd /tmp'
      auto_execute: true
  - name: Beta
    group: Projects
    type: clippet
    options:
      command: 'cd /tmp'
      auto_execute: true
  - name: Snippet
    group: Commands
    type: clippet
    options:
      command: 'echo hi'
      auto_execute: true
      in_place: true
""")
ttyga.STATE_FILE.write_text('{"expanded_groups": {}, "sidebar_visible": true}')

results = []


def check(name, ok, detail=''):
    results.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}{'  — ' + detail if detail else ''}",
          flush=True)


def profile(app, name):
    for _btn, p in app._profile_buttons:
        if p.get('name') == name:
            return p
    raise KeyError(name)


def button(app, name):
    for btn, p in app._profile_buttons:
        if p.get('name') == name:
            return btn
    raise KeyError(name)


def tooltip(app, name):
    return button(app, name).get_tooltip_text()


def click(app, name, ctrl=False):
    """on_profile_clicked as the row would deliver it. The Ctrl flag is set
    here rather than synthesising an event, because the gesture only exists to
    carry the modifier across to this handler."""
    app._force_new_tab = ctrl
    app.on_profile_clicked(button(app, name), profile(app, name))


class Runner:
    def __init__(self, app):
        self.app = app
        self.steps = [
            self.step_first_launch,
            self.step_second_profile,
            self.step_switch_back,
            self.step_ctrl_click,
            self.step_cycle,
            self.step_merged_pane_focus,
            self.step_in_place_clippet,
            self.step_setting_off,
            self.step_close_clears_tooltip,
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
            GLib.timeout_add(900, lambda: self.pump() and False)
        return False

    @staticmethod
    def later(fn, ms=400):
        GLib.timeout_add(ms, lambda: fn() and False)

    # --- steps ---------------------------------------------------------
    def step_first_launch(self):
        app = self.app
        check('start: no tabs', app.notebook.get_n_pages() == 0,
              f"{app.notebook.get_n_pages()}")
        check('start: no tooltip on a closed profile', tooltip(app, 'Alpha') is None,
              repr(tooltip(app, 'Alpha')))
        click(app, 'Alpha')

        def verify():
            check('first click on Alpha: launched one tab',
                  app.notebook.get_n_pages() == 1, f"{app.notebook.get_n_pages()}")
            check('Alpha open: tooltip offers the switch',
                  tooltip(app, 'Alpha') == "Switch to this tab — Ctrl+click for a new one",
                  repr(tooltip(app, 'Alpha')))
            check('Beta still closed: no tooltip', tooltip(app, 'Beta') is None,
                  repr(tooltip(app, 'Beta')))
        self.later(verify)

    def step_second_profile(self):
        app = self.app
        click(app, 'Beta')

        def verify():
            check('click on unopened Beta: launched a second tab',
                  app.notebook.get_n_pages() == 2, f"{app.notebook.get_n_pages()}")
            check('Beta is the tab on screen', app.notebook.get_current_page() == 1,
                  f"{app.notebook.get_current_page()}")
        self.later(verify)

    def step_switch_back(self):
        app = self.app
        click(app, 'Alpha')

        def verify():
            check('click on open Alpha: no third tab',
                  app.notebook.get_n_pages() == 2, f"{app.notebook.get_n_pages()}")
            check('click on open Alpha: raised its tab',
                  app.notebook.get_current_page() == 0,
                  f"{app.notebook.get_current_page()}")
            term = app._active_terminal
            meta = app.tabs.get(term, {})
            check('switch focused Alpha\'s own terminal',
                  app._profile_key(meta.get('profile')) == ('Alpha', 'Projects'),
                  str(app._profile_key(meta.get('profile'))))
        self.later(verify)

    def step_ctrl_click(self):
        app = self.app
        click(app, 'Alpha', ctrl=True)

        def verify():
            check('Ctrl+click on open Alpha: launched a new tab',
                  app.notebook.get_n_pages() == 3, f"{app.notebook.get_n_pages()}")
            check('Alpha in two tabs: tooltip counts them',
                  tooltip(app, 'Alpha') == "Cycle 2 open tabs — Ctrl+click for a new one",
                  repr(tooltip(app, 'Alpha')))
            check('the Ctrl flag was consumed, not left set',
                  app._force_new_tab is False)
        self.later(verify)

    def step_cycle(self):
        # Pages now: 0 Alpha, 1 Beta, 2 Alpha; page 2 is current.
        app = self.app
        check('cycle setup: on the second Alpha tab',
              app.notebook.get_current_page() == 2, f"{app.notebook.get_current_page()}")
        click(app, 'Alpha')

        def first():
            check('cycle wraps past the last match to the first',
                  app.notebook.get_current_page() == 0,
                  f"{app.notebook.get_current_page()}")
            click(app, 'Alpha')
            self.later(second, 400)

        def second():
            check('cycle steps to the next match after the current page',
                  app.notebook.get_current_page() == 2,
                  f"{app.notebook.get_current_page()}")
            check('cycling never spawns a tab',
                  app.notebook.get_n_pages() == 3, f"{app.notebook.get_n_pages()}")
        self.later(first)

    def step_merged_pane_focus(self):
        # Merge the Beta tab into the second Alpha tab, then click Beta: the
        # tab is right but the *pane* is the thing that has to take focus.
        app = self.app
        alpha2 = None
        for t, m in app.tabs.items():
            if (app._profile_key(m.get('profile')) == ('Alpha', 'Projects')
                    and app.notebook.page_num(m['tab_root']) == 2):
                alpha2 = t
        beta = next(t for t, m in app.tabs.items()
                    if app._profile_key(m.get('profile')) == ('Beta', 'Projects'))
        check('merge setup: found both terminals',
              alpha2 is not None and beta is not None)
        app._merge_tab_into(beta, alpha2)

        def after_merge():
            root = app.tabs[alpha2]['tab_root']
            ts = list(app._all_terminals_in(root))
            check('merged tab holds both panes', len(ts) == 2, f"{len(ts)}")
            check('Beta tooltip back to a single tab',
                  tooltip(app, 'Beta') == "Switch to this tab — Ctrl+click for a new one",
                  repr(tooltip(app, 'Beta')))
            # Focus the Alpha pane, switch away, then click Beta.
            app._active_terminal = alpha2
            app.notebook.set_current_page(0)
            self.later(click_beta, 400)

        def click_beta():
            click(app, 'Beta')
            self.later(verify, 400)

        def verify():
            check('merged tab raised', app.notebook.get_current_page() == 1,
                  f"{app.notebook.get_current_page()}")
            check('the Beta PANE took focus, not the tab\'s last-used pane',
                  app._active_terminal is beta,
                  str(app._profile_key(app.tabs.get(app._active_terminal, {}).get('profile'))))
        self.later(after_merge, 600)

    def step_in_place_clippet(self):
        app = self.app
        before = app.notebook.get_n_pages()
        check('in-place clippet never gets a tooltip',
              tooltip(app, 'Snippet') is None, repr(tooltip(app, 'Snippet')))
        click(app, 'Snippet')

        def once():
            check('in-place clippet opens no tab',
                  app.notebook.get_n_pages() == before,
                  f"{app.notebook.get_n_pages()} vs {before}")
            check('in-place clippet still has no tooltip after firing',
                  tooltip(app, 'Snippet') is None, repr(tooltip(app, 'Snippet')))
        self.later(once)

    def step_setting_off(self):
        app = self.app
        app.set_setting('sidebar_click', 'launch')
        before = app.notebook.get_n_pages()

        def verify_tips():
            check('sidebar_click=launch clears every tooltip',
                  all(tooltip(app, n) is None for n in ('Alpha', 'Beta', 'Snippet')),
                  repr([tooltip(app, n) for n in ('Alpha', 'Beta')]))
            click(app, 'Alpha')
            self.later(verify_launch, 500)

        def verify_launch():
            check('sidebar_click=launch: an open profile launches again',
                  app.notebook.get_n_pages() == before + 1,
                  f"{app.notebook.get_n_pages()} vs {before + 1}")
            app.set_setting('sidebar_click', 'switch')
            self.later(verify_back, 300)

        def verify_back():
            check('switching the setting back restores the tooltip',
                  tooltip(app, 'Alpha', ) is not None, repr(tooltip(app, 'Alpha')))
        self.later(verify_tips, 300)

    def step_close_clears_tooltip(self):
        # Closing a tab that is NOT on screen emits no switch-page — the case
        # _do_close_tab already refreshes the pulse by hand for.
        app = self.app
        beta_root = next(m['tab_root'] for t, m in app.tabs.items()
                         if app._profile_key(m.get('profile')) == ('Beta', 'Projects'))
        beta_page = app.notebook.page_num(beta_root)
        other = 0 if beta_page != 0 else 1
        app.notebook.set_current_page(other)

        def close_it():
            check('close setup: Beta is not the tab on screen',
                  app.notebook.get_current_page() != app.notebook.page_num(beta_root))
            app._do_close_tab(beta_root)
            self.later(verify, 400)

        def verify():
            still_open = any(app._profile_key(m.get('profile')) == ('Beta', 'Projects')
                             for m in app.tabs.values())
            check('Beta tab closed', not still_open)
            check('closing an off-screen tab clears its tooltip',
                  tooltip(app, 'Beta') is None, repr(tooltip(app, 'Beta')))
        self.later(close_it, 400)

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
        GLib.timeout_add(1200, lambda: Runner(a).pump() and False)

    app.connect('activate', on_activate)
    app.run(None)
    bad = [n for n, ok, _ in results if not ok]
    sys.exit(1 if (bad or not results) else 0)


main()
