#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""FL01: write vendor/elekloader/examples/flanger-names-dn2/mod.json, flanger-names-dn2 0.2 (sites checked against the
stock image).

    venv/bin/python scripts/fl01_make_mod.py [--list]

flanger-names-dn2 0.2 = 0.1's 13 sites (scripts/cmp4_make_mod.py: the chorus page's title and short name, the SEND FX
page box, five descriptor rows' long names and short labels; the same bytes, checked) and three on the chorus page's
knob C, descriptor row 0x6b (group 16, k 27): FL01's DSP (tools/dspsplice/src/flg_glue2.c, notes/T5.6) plays that knob
as TIME, the centre delay, where CMP4 played it as HPF. The row's long name "High-pass" becomes "Time" (+0x28) and its
short label "HPF" becomes "TIME" (+0x30), by repointing (the stock "HPF" is also the delay's and the reverb's label),
and its new-kit default (+0x10, value << 8) 0 becomes 32 (8192): TIME 32 is CMP4's flanger, exactly. The default is a
plain value in the row; the stock code that sets a new kit, sound or machine change reads it from the row
(notes/T6.1-table-growth-study.md 2.10: 0x400e7140, 0x400e73dc, 0x400e7eb0), and param-grow's grown table copies the
image's rows at boot. Kits already saved keep their stored value: only a new kit (or a cleared page) starts at 32.
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scripts'))
sys.dont_write_bytecode = True
import cmp4_make_mod as M4  # noqa: E402  (0.1's 13 sites, built and checked at import; it writes nothing)

OUT = os.path.join(ROOT, 'vendor', 'elekloader', 'examples', 'flanger-names-dn2', 'mod.json')
DESC, ROW, LONG, SHORT = M4.DESC, M4.ROW, M4.LONG, M4.SHORT
DEFAULT = 0x10
ROW_C = 0x6b
TIME_DEFAULT = 32

sites = [dict(s) for s in M4.sites]
rd, u32, cstr = M4.rd, M4.u32, M4.cstr


def s32(a):
    v = u32(a)
    return v - (1 << 32) if v & 0x80000000 else v


# the chorus page's knob C: row 0x6b as stock has it
a = DESC + ROW_C * ROW
want = {0x00: 16, 0x04: 27, 0x08: 0, 0x0c: 127 << 8, DEFAULT: 0}
for off, v in want.items():
    if s32(a + off) != v:
        raise SystemExit('row 0x%x +0x%x: %d, expected %d' % (ROW_C, off, s32(a + off), v))
for off, old in ((LONG, 'High-pass'), (SHORT, 'HPF'), (0x2c, 'Chorus')):
    if cstr(u32(a + off)) != old:
        raise SystemExit('row 0x%x +0x%x: "%s", expected "%s"' % (ROW_C, off, cstr(u32(a + off)), old))
sites.append({'addr': '0x%08x' % (a + DEFAULT), 'stock': rd(a + DEFAULT, 4).hex(), 'op': 'bytes',
              'new': '%08x' % (TIME_DEFAULT << 8),
              'note': 'row 0x%x +0x%x (chorus page C): new-kit default 0 -> %d (TIME %d = CMP4\'s flanger)'
                      % (ROW_C, DEFAULT, TIME_DEFAULT << 8, TIME_DEFAULT)})
for off, sym, old, what in ((LONG, 'fln_time_name', 'High-pass', 'long name'), (SHORT, 'fln_time', 'HPF', 'knob label')):
    sites.append({'addr': '0x%08x' % (a + off), 'stock': rd(a + off, 4).hex(), 'op': 'ptr', 'target': sym,
                  'note': 'row 0x%x +0x%x (chorus page C, %s): "%s"' % (ROW_C, off, what, old)})
if len(sites) != 16:
    raise SystemExit('expected 16 sites, got %d' % len(sites))
strip = lambda ss: [{k: v for k, v in s.items() if k != 'note'} for s in ss]      # noqa: E731
if strip(sites[:13]) != M4.mod['sites']:
    raise SystemExit('the first 13 sites are not flanger-names 0.1\'s')

mod = dict(M4.mod)
mod.update({
    'version': '0.2',
    'title': 'Flanger names (FL01)',
    'description': 'For dn2-cfw\'s FL01 build, whose DSP runs a flanger in the chorus slot (its knob C is TIME, the '
                   'centre delay, from a phaser-like 0.05 ms through the flanger to a 25.7 ms chorus) and the stock '
                   'delay in the delay slot: renames the chorus page and every chorus send to Flanger/FLG, and the '
                   'chorus page\'s knob C from High-pass/HPF to Time/TIME, with a new-kit default of 32 (the flanger '
                   'of earlier builds); every delay name stays stock. 0.1 (CMP4) is the same without the knob C '
                   'sites. Labels and one default only: no new rows, knobs or glyphs, and every knob keeps its MIDI '
                   'CC, storage and DSP frame word. On a stock DSP the chorus would be named Flanger and its HPF '
                   'knob Time.',
    'sites': strip(sites),
})

if __name__ == '__main__':
    with open(OUT, 'w') as f:
        json.dump(mod, f, indent=1)
        f.write('\n')
    if '--list' in sys.argv:
        for s in sites:
            print('%s %-5s %-14s %s' % (s['addr'], s['op'], s.get('target', s.get('new')), s['note']))
    print('%d sites -> %s (flanger-names %s)' % (len(sites), os.path.relpath(OUT, ROOT), mod['version']))
