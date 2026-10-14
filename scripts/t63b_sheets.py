#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""T6.3 CMP1: the contact sheets for the owner, from the emulator runs (scripts/t63b_sheet.py).

    venv/bin/python scripts/t63b_sheets.py   -> out/T6.3/CMP1/sheets/*.png

Each screen's caption says what was pressed; the header line of a screen shows the knob's
"Name=value" while it turns. Screens that a run did not capture are left out.
"""
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EMU = os.path.join(ROOT, 'out', 'T6.3', 'CMP1', 'emu')
OUT = os.path.join(ROOT, 'out', 'T6.3', 'CMP1', 'sheets')
PY = os.path.join(ROOT, 'venv', 'bin', 'python')


def d(name, script):
    return os.path.join(EMU, name, script)


def have(path, scr):
    return os.path.exists(os.path.join(path, scr + '.txt'))


def make(out, title, items, cols=4):
    items = [(p, s, c) for p, s, c in items if have(p, s)]
    if not items:
        print('%s: nothing captured' % out)
        return
    args = [PY, os.path.join(ROOT, 'scripts', 't63b_sheet.py'), os.path.join(OUT, out), '--cols', str(cols),
            '--title', title] + ['%s:%s=%s' % (p, s, c) for p, s, c in items]
    print(subprocess.run(args, capture_output=True, text=True).stdout.strip())


def main():
    os.makedirs(OUT, exist_ok=True)
    pg = d('C1EM', 't63b-pages7')
    names = ['LOW', 'MID', 'HIGH', 'INT MIX 1', 'INT MIX 2', 'FX MIX', 'EXT MIX 1']
    make('1-mixer-pages.png', 'CMP1: the MIXER view (FUNC+MOD), 7 pages',
         [(pg, 'p%d' % i, '%d %s' % (i, names[i - 1])) for i in range(1, 8)] +
         [(pg, 'key-8', 'FUNC+MOD on the last page: wraps')])
    knobs = 'ABCDEFGHL'
    for i, band in ((1, 'LOW'), (2, 'MID'), (3, 'HIGH')):
        make('2-%s-knobs.png' % band.lower(), 'CMP1: %s, each knob one step up (the header shows it)' % band,
             [(pg, 'p%d' % i, band)] + [(pg, 'p%d-%s' % (i, k), '%s %s +1' % (band, k if k != 'L' else 'LEVEL'))
                                        for k in knobs] + [(pg, 'p%d-back' % i, 'all back, 3 s later')])
    v = d('C1EM', 't63b-values')
    make('3-ratio.png', 'CMP1: LOW ratio (E), down to 1.00 and up to 20.00',
         [(v, 'v02-rat-down%d' % i, 'RAT down %d' % i) for i in range(1, 7)] +
         [(v, 'v03-rat-up%d' % i, 'RAT up %d' % i) for i in range(1, 11)])
    make('4-crossovers.png', 'CMP1: the crossovers at their ends (F on LOW and MID)',
         [(v, s, c) for s, c in (('v04-xlo-bottom', 'Low/Mid at 0'), ('v04-xlo-bottom-up1', 'Low/Mid 1'),
                                 ('v05-xlo-top-down1', 'Low/Mid 126'), ('v05-xlo-top', 'Low/Mid 127'),
                                 ('v07-xhi-bottom', 'Mid/High at 0'), ('v07-xhi-bottom-up1', 'Mid/High 1'),
                                 ('v08-xhi-top-down1', 'Mid/High 126'), ('v08-xhi-top', 'Mid/High 127'))])
    df = d('C1EM', 't63b-defaults')
    items = []
    for p, band in ((1, 'low'), (2, 'mid'), (3, 'high')):
        items += [(df, 'f%d-%s-%s-default' % (p, band, k), '%s %s default' % (band.upper(), k)) for k in 'ABCDEF'
                  if not (band == 'high' and k == 'F')]
    items += [(v, 'v21-high-F-up3', 'HIGH F turned: nothing'), (v, 'v23-high-D-up', 'HIGH D +1')]
    make('5-defaults.png', 'CMP1: the band knobs at the new-kit defaults (what CLEAR PAGE sets)', items)
    du, du2 = d('C1EM', 't63b-dual'), d('C1EM', 't63b-dual2')
    make('6-dual.png', 'CMP1: DUAL on and off through the UI; SCS to In R on LOW',
         [(du, 'd01-extmix1', 'EXT MIX 1 (7/7)'), (du, 'd02-dual-on', 'B: DUAL on'), (du, 'd10-down', 'DOWN: EXT MIX 2 (8/8)'),
          (du, 'd12-pagekey', 'FUNC+MOD: LOW (1/8)')] +
         [(du2, 'e01-scs-up%02d' % i, 'LOW G up %d' % i) for i in (17, 18, 19, 20)] +
         [(du2, 'e05-dual-off', 'EXT MIX 1: DUAL off'), (du2, 'e08-pagekey', 'FUNC+MOD: LOW (1/7)')])
    ma, mb, mc = d('C1EM', 'meters-a'), d('C1EM', 'meters-b'), d('C1EM', 'meters-c')
    make('7-meters.png', 'CMP1: the band GR bars from DSP reply half-words 8, 9, 10 (faked in the emulator)',
         [(ma, 'm1', 'LOW, q 9000: 13 px'), (ma, 'm2', 'MID, q 16000: 24 px'), (ma, 'm3', 'HIGH, q 4000: 6 px'),
          (mb, 'm1', 'LOW, q 32767: 50 px'), (mb, 'm2', 'MID, q 655: 1 px'), (mb, 'm3', 'HIGH, q 654: none'),
          (mc, 'm1', 'LOW, q 0: none'), (mc, 'm2', 'MID, q 0: none'), (mc, 'm3', 'HIGH, q 0: none')], cols=3)
    cp = d('C1EM', 't63b-copy')
    make('8-func-rec-play-stop.png', 'CMP1: FUNC+REC/PLAY/STOP on a band page act on the pattern, as on GT01',
         [(cp, s, c) for s, c in (('c01-low-copied', 'LOW: FUNC+REC'), ('c03-low-cleared', 'LOW: FUNC+PLAY'),
                                 ('c04-low-pasted', 'LOW: FUNC+STOP'))], cols=3)
    ob = d('C1EM', 't63b-ob8')
    make('9-outbox8-forced.png', 'CMP1 with an Outbox 8 forced (not modelled by the emulator)',
         [(ob, s, c) for s, c in (('o01-last', 'the last page'), ('o03-dual-on', 'EXT MIX 1: DUAL on'),
                                 ('o04-next', 'DOWN'), ('o05-next', 'DOWN'), ('o07-dual-off', 'DUAL off'),
                                 ('o08-next', 'DOWN'))])
    g5 = d('GT01', 't63b-pages5')
    items = []
    for gp, cp_, nm in ((2, 4, 'INT MIX 1'), (3, 5, 'INT MIX 2'), (4, 6, 'FX MIX'), (5, 7, 'EXT MIX 1')):
        items += [(g5, 'p%d' % gp, 'GT01 %s' % nm), (pg, 'p%d' % cp_, 'CMP1 %s' % nm)]
    make('10-stock-pages-vs-gt01.png', 'GT01 (left) and CMP1 (right): the stock MIXER pages', items, cols=4)


if __name__ == '__main__':
    sys.exit(main())
