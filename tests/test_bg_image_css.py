#!/usr/bin/env python3
"""Verify the one-dimmer background-image CSS: the gutter tint lives on
.pane-box, the terminal goes fully transparent, and no-image is inert.

Pure-function test — build_css() takes no app state, so this needs no
display and no main loop. Runs headless in well under a second.

    python3 tests/test_bg_image_css.py

The most important assertion here is the no-image one: with bg_image_path
empty the feature must emit NOTHING, so a terminal is byte-identical to
before the feature existed. Nothing else in the codebase checks that.
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Vte', '3.91')
gi.require_version('Adw', '1')

import ttyga

results = []


def check(name, ok, detail=''):
    results.append((name, ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}{'  — ' + detail if detail else ''}")


img = Path(tempfile.gettempdir()) / 'ttyga_fake_bg.png'
img.write_bytes(b'\x89PNG\r\n\x1a\n')          # never decoded; only its path is used

for theme in ('dark', 'light', 'nord'):
    term_bg = ttyga.THEMES[theme]['term_bg']

    # --- no image: the feature must be entirely absent -------------------
    plain = ttyga.build_css(theme)
    check(f'{theme}: no image emits no .tab-root image rule',
          'background-image: url(' not in plain)
    check(f'{theme}: no image leaves .pane-box opaque term_bg',
          f'background: {term_bg};' in plain)
    check(f'{theme}: no image emits no vte-terminal transparency rule',
          'vte-terminal {' not in plain)

    # --- image set --------------------------------------------------------
    css = ttyga.build_css(theme, bg_image_path=str(img), bg_opacity=0.75)
    want = ttyga._rgba_css(term_bg, 0.75)
    check(f'{theme}: .pane-box carries the tint at bg_opacity',
          f'background: {want};' in css, want)
    check(f'{theme}: .pane-box is NOT left fully transparent (the 0.6.53 bug)',
          '.pane-box {\n    background: transparent;\n}' not in css)
    check(f'{theme}: vte-terminal still overridden transparent',
          'background-color: transparent;' in css)
    check(f'{theme}: image lands on .tab-root', '.tab-root {' in css
          and 'background-image: url(' in css)

# --- opacity is live in the CSS, not baked ------------------------------
a = ttyga.build_css('nord', bg_image_path=str(img), bg_opacity=0.40)
b = ttyga.build_css('nord', bg_image_path=str(img), bg_opacity=0.95)
check('changing bg_opacity changes the emitted CSS', a != b)
check('low opacity emits its own alpha',
      ttyga._rgba_css(ttyga.THEMES['nord']['term_bg'], 0.40) in a)
check('high opacity emits its own alpha',
      ttyga._rgba_css(ttyga.THEMES['nord']['term_bg'], 0.95) in b)

# --- _rgba_css handles every form THEMES uses ---------------------------
check('_rgba_css parses #rrggbb', ttyga._rgba_css('#1e2430', 0.5)
      == 'rgba(30,36,48,0.500)', ttyga._rgba_css('#1e2430', 0.5))
check('_rgba_css parses rgba() input',
      ttyga._rgba_css('rgba(255,255,255,0.18)', 0.75) == 'rgba(255,255,255,0.750)')

# --- a bad path must not emit a half-configured feature -----------------
bad = ttyga.build_css('nord', bg_image_path='', bg_opacity=0.5)
check('empty path emits nothing even with a non-default opacity',
      'vte-terminal {' not in bad and 'rgba' in bad)  # rgba present from theme tokens

img.unlink()
bad_n = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(bad_n)}/{len(results)} passed")
if bad_n:
    print('FAILED: ' + '; '.join(bad_n))
sys.exit(1 if bad_n else 0)
