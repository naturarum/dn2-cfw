#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""TE01: write vendor/elekloader/examples/echo-names-dn2/mod.json and band_table.h.

    python3 scripts/te01_make_echo.py

echo-names 0.1 = fx-echo 0.2 (SC01's delay page as the tape echo, on a unit 2026-10-21) without fx-echo's 13 flanger
sites: those are fx-rename's flanger sites, the same 13 addresses flanger-names (DJ01, BA05) writes, so fx-echo and
flanger-names cannot link together. Every other site is fx-echo 0.2's as scripts/t53_make_mod.py wrote it; each is
checked again here against the stock image (the stock bytes it names are the image's), and the 13 left out must be
exactly flanger-names' flanger sites. band_table.h is written from fx/spaceecho.c's constants as t53_make_mod.py
writes it and must hold fx-echo's values. echo_names.c and graph.s are fx-echo's sources without the flanger strings
(and comments).
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scripts'))
sys.dont_write_bytecode = True
import t53_make_mod as T53  # noqa: E402   (reads the stock image; writes nothing on import)

EX = os.path.join(ROOT, 'vendor', 'elekloader', 'examples')
FXE = os.path.join(EX, 'fx-echo-dn2')
FLN = os.path.join(EX, 'flanger-names-dn2')
OUT = os.path.join(EX, 'echo-names-dn2')
FLG_TARGETS = ('fxe_flanger', 'fxe_flg')


def main():
    fx = json.load(open(os.path.join(FXE, 'mod.json')))
    fl = json.load(open(os.path.join(FLN, 'mod.json')))
    if fx['version'] != '0.2':
        raise SystemExit('fx-echo is not 0.2')
    flg = [s for s in fx['sites'] if s.get('target', '').startswith(FLG_TARGETS)]
    keep = [s for s in fx['sites'] if s not in flg]
    fl_addr = {int(s['addr'], 16) for s in fl['sites']}
    if len(flg) != 13 or not {int(s['addr'], 16) for s in flg} <= fl_addr:
        raise SystemExit('fx-echo\'s flanger sites are not 13 of flanger-names\' sites')
    if any(int(s['addr'], 16) in fl_addr for s in keep):
        raise SystemExit('a kept site is one of flanger-names\' sites')
    for s in keep:
        a, stock = int(s['addr'], 16), bytes.fromhex(s['stock'])
        if T53.rd(a, len(stock)) != stock:
            raise SystemExit('%s: the stock image holds %s, not %s' % (s['addr'], T53.rd(a, len(stock)).hex(), s['stock']))
    k, vals = T53.band_table()
    old = open(os.path.join(FXE, 'band_table.h')).read()
    rows = ['    ' + ', '.join('%5d' % v for v in vals[i:i + 10]) + ',' for i in range(0, 128, 10)]
    body = 'static const unsigned short band_half[128] = {\n%s\n};\n' % '\n'.join(rows)
    if body not in old:
        raise SystemExit('fx/spaceecho.c\'s band law moved since fx-echo 0.2: band_table.h would change')
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, 'band_table.h'), 'w') as f:
        f.write('/* SPDX-License-Identifier: GPL-2.0-or-later */\n'
                '/* band_table.h: written by dn2-cfw\'s scripts/te01_make_echo.py from fx/spaceecho.c (do not edit).\n'
                ' * Half the tape echo band\'s width for each BW step, in 1/256 CENTER steps: width = 2^(SE_LB0 +\n'
                ' * SE_LB_RANGE x BW / 127) octaves (SE_LB0 %g, SE_LB_RANGE %g), CENTER = SE_CTR_OCT (%g) octaves\n'
                ' * over 0..127, so half = width / 2 x 127 / SE_CTR_OCT x 256. */\n%s'
                % (k['SE_LB0'], k['SE_LB_RANGE'], k['SE_CTR_OCT'], body))
    mod = {
        'id': 'echo-names', 'version': '0.1',
        'title': 'Echo page: the tape echo on one page (TE01)',
        'category': 'Examples', 'license': 'GPL-2.0-or-later', 'author': 'naturarum',
        'description': (
            'EXPERIMENT. fx-echo 0.2 (dn2-cfw SC01, on a unit) without its flanger sites, so it links with '
            'flanger-names: for dn2-cfw\'s TE01 and later builds, whose DSP runs the tape echo in the delay slot. The '
            'delay page and its sends are named Echo/ECHO (the page box says ECH); the page becomes TIME MODE WID FDBK '
            'CNTR BW AGE REV: X (Pingpong) widened to MODE 0..6 (heads 1, 2, 3, 12, 23, 13, 123), HPF/LPF named '
            'CNTR/BW with the filter picture drawing the echo\'s band, and the hidden Delay FX Routing row made AGE '
            '(0..127, its value shown while it turns) and placed on the empty knob G. Every knob keeps its MIDI CC, '
            'storage and DSP frame word; MODE and AGE are never p-lockable; AGE has no CC. On a stock DSP the names '
            'would be wrong.'),
        'device': fx['device'], 'os': fx['os'],
        'sources': ['echo_names.c', 'graph.s'],
        'subscribe': fx['subscribe'], 'exports': fx['exports'],
        'requires': ['core'], 'conflicts': ['fx-rename', 'fx-echo'],
        'sites': keep,
    }
    with open(os.path.join(OUT, 'mod.json'), 'w') as f:
        json.dump(mod, f, indent=1)
        f.write('\n')
    print('echo-names 0.1: %d sites (fx-echo 0.2\'s %d less its %d flanger sites), each stock value checked; '
          'band_table.h fx-echo\'s values -> %s' % (len(keep), len(fx['sites']), len(flg), os.path.relpath(OUT, ROOT)))


if __name__ == '__main__':
    main()
