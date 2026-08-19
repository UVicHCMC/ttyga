#!/usr/bin/env python3
"""Verify the stopwatch's pure arithmetic: _stopwatch_seconds() and
_format_stopwatch() across a start/pause/resume/reset cycle.

Pure-function test — both methods only touch the four _stopwatch_* instance
attributes and time.monotonic(), so a types.SimpleNamespace stand-in for
self is enough; no GTK display, no main loop, no DevFrame construction.
time.monotonic is patched so the cycle is deterministic and instant rather
than depending on real sleeps.

    python3 tests/test_stopwatch.py
"""
import sys
import types
from pathlib import Path
from unittest.mock import patch

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


def fresh_stopwatch():
    return types.SimpleNamespace(
        _stopwatch_running=False,
        _stopwatch_elapsed=0.0,
        _stopwatch_start_mono=None,
    )


seconds = ttyga.DevFrame._stopwatch_seconds
fmt = ttyga.DevFrame._format_stopwatch

# --- _format_stopwatch: zero-padded HH:MM:SS, hours unbounded ------------
check('formats zero as 00:00:00', fmt(None, 0) == '00:00:00')
check('formats sub-minute', fmt(None, 7) == '00:00:07')
check('formats minutes+seconds', fmt(None, 252) == '00:04:12')
check('zero-pads single-digit hours', fmt(None, 3600 + 252) == '01:04:12')
check('hours unbounded past 99', fmt(None, 100 * 3600) == '100:00:00')
check('truncates (not rounds) fractional seconds', fmt(None, 4.9) == '00:00:04')

# --- _stopwatch_seconds: stopped just reads the accumulator --------------
sw = fresh_stopwatch()
sw._stopwatch_elapsed = 42.0
check('stopped: reads accumulator directly', seconds(sw) == 42.0)

# --- running: accumulator + live segment, driven by mocked monotonic -----
with patch('ttyga.time.monotonic') as mono:
    mono.return_value = 1000.0
    sw = fresh_stopwatch()
    sw._stopwatch_running = True
    sw._stopwatch_start_mono = 1000.0
    mono.return_value = 1005.0
    check('running: live segment is included', seconds(sw) == 5.0)

    # pause: fold the segment in, as _on_stopwatch_toggle_clicked does
    sw._stopwatch_elapsed += mono.return_value - sw._stopwatch_start_mono
    sw._stopwatch_running = False
    check('pause folds the segment into the accumulator', sw._stopwatch_elapsed == 5.0)
    mono.return_value = 1050.0
    check('paused: elapsed time does not advance', seconds(sw) == 5.0)

    # resume: a fresh segment starts from the paused value
    sw._stopwatch_start_mono = mono.return_value
    sw._stopwatch_running = True
    mono.return_value = 1053.0
    check('resume continues from the paused value, not zero', seconds(sw) == 8.0)

    # reset: zero and paused (confirmed behaviour, not reset-and-continue)
    sw._stopwatch_running = False
    sw._stopwatch_elapsed = 0.0
    sw._stopwatch_start_mono = None
    check('reset zeroes the accumulator', seconds(sw) == 0.0)
    check('reset leaves it paused', sw._stopwatch_running is False)

bad_n = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(bad_n)}/{len(results)} passed")
if bad_n:
    print('FAILED: ' + '; '.join(bad_n))
sys.exit(1 if bad_n else 0)
