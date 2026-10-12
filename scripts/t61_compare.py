#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""T6.1: compare the emulator runs of scripts/t61-emu.sh with stock's.

For each run directory under out/T6.1/emu/ that has a stock counterpart (same script), list the
screens (.txt, the emulator's pixel dump) that differ from stock, the FX values read at the end,
and what the run's t61.json says about the patch (sites, builder re-run, page slots).

    venv/bin/python scripts/t61_compare.py [--out out/T6.1/emu/summary.txt]

Pairs: VARIANT vs stock, VARIANT-SUFFIX vs stock-SUFFIX.
"""
import argparse
import glob
import json
import os
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
EMU = os.path.join(ROOT, 'out', 'T6.1', 'emu')
VARIANTS = ('inert', 'grown', 'pathc', 'grown-cc')


def screens(d):
    return {os.path.basename(p): open(p).read() for p in glob.glob(os.path.join(d, '*.txt'))
            if not p.endswith('.leds.txt')}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--out', default=os.path.join(EMU, 'summary.txt'))
    a = ap.parse_args()
    out = []
    for d in sorted(os.listdir(EMU)):
        v, _, suffix = d.partition('-') if not d.startswith('grown-cc') else ('grown-cc', '', d[9:])
        if v not in VARIANTS:
            continue
        ref = os.path.join(EMU, 'stock' + ('-' + suffix if suffix else ''))
        run = os.path.join(EMU, d)
        if not os.path.isdir(ref) or not os.path.exists(os.path.join(run, 't61.json')):
            continue
        s, r = screens(ref), screens(run)
        same = sorted(k for k in s if r.get(k) == s[k])
        diff = sorted(k for k in s if k in r and r[k] != s[k])
        rep = json.load(open(os.path.join(run, 't61.json')))
        refrep = json.load(open(os.path.join(ref, 't61.json')))
        out.append('== %s vs %s' % (d, os.path.basename(ref)))
        out.append('  patch: %s repoints, %s bounds; builder %s; page slots %s' % (
            rep.get('n_repoints', 0), rep.get('n_bounds', 0),
            {k: rep['builder'][k] for k in ('built_flag', 'returned', 'n_fx_slots')} if rep.get('builder') else '-',
            rep.get('page_slots', {})))
        out.append('  screens: %d same, %d differ%s' % (len(same), len(diff), (': ' + ' '.join(diff)) if diff else ''))
        fa, fs = rep['after']['fx_live'], refrep['after']['fx_live']
        dif = ', '.join('%s: %d/%d' % (k, fa[k] >> 8, fs[k] >> 8) for k in fa if fa[k] != fs[k])
        out.append('  FX live words at the end (k: run / stock): %s' % (dif or 'as stock'))
    txt = '\n'.join(out) + '\n'
    with open(a.out, 'w') as f:
        f.write(txt)
    sys.stdout.write(txt)


if __name__ == '__main__':
    main()
