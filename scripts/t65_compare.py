#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""T6.5: compare two runs of the same key script, screen by screen and LED by LED.

    venv/bin/python scripts/t65_compare.py A_DIR B_DIR [--out FILE] [--allow GLOB=x0,y0,x1,y1[;x0,y0,x1,y1] ...]

Reads the emulator's 128x64 ASCII screens (*.txt, '#' = pixel on) and *.leds.json of two runs.
For each screen: identical, or the boxes (x0..x1, y0..y1, y from the top) that hold every
differing pixel (scripts/t50b_compare.py's grouping). --allow lets the screens whose names match the
glob differ inside the given rectangles (each box of differing pixels must lie inside one of them);
any other difference makes the verdict DIFFERENT (exit 1).
"""
import argparse
import fnmatch
import glob
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scripts'))
from t50b_compare import screen, boxes          # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('a')
    ap.add_argument('b')
    ap.add_argument('--out')
    ap.add_argument('--allow', action='append', default=[])
    a = ap.parse_args()
    allow = []
    for spec in a.allow:
        nm, _, boxes_ = spec.partition('=')
        allow.append((nm, [[int(v) for v in b.split(',')] for b in boxes_.split(';')]))
    def is_screen(p):
        return not p.endswith('.leds.txt') and os.path.basename(p)[:1].isdigit()
    names = sorted(os.path.basename(p)[:-4] for p in glob.glob(os.path.join(a.a, '*.txt')) if is_screen(p))
    namesb = set(os.path.basename(p)[:-4] for p in glob.glob(os.path.join(a.b, '*.txt')) if is_screen(p))
    out, bad, same, allowed = [], [], 0, 0
    for nm in names:
        if nm not in namesb:
            bad.append('%s: missing in B' % nm)
            continue
        sa, sb = screen(os.path.join(a.a, nm + '.txt')), screen(os.path.join(a.b, nm + '.txt'))
        diff = [[sa[y][x] ^ sb[y][x] for x in range(128)] for y in range(64)]
        la = os.path.join(a.a, nm + '.leds.json')
        lb = os.path.join(a.b, nm + '.leds.json')
        leds_same = (not os.path.exists(la)) or (os.path.exists(lb) and json.load(open(la)) == json.load(open(lb)))
        bx = boxes(diff)
        if not bx:
            same += 1
            if not leds_same:
                out.append('%-34s screen identical; LEDs differ' % nm)
            continue
        inside = lambda b, r: b[0] >= r[0] and b[1] >= r[1] and b[2] <= r[2] and b[3] <= r[3]   # noqa: E731
        ok = [any(fnmatch.fnmatch(nm, pat) and all(any(inside(b, r) for r in rects) for b in bx)
                  for pat, rects in allow)]
        desc = ' '.join('x%d..%d,y%d..%d(%dpx)' % (b[0], b[2], b[1], b[3], b[4]) for b in bx)
        if ok[0]:
            allowed += 1
            out.append('%-34s differs as allowed: %s' % (nm, desc))
        else:
            bad.append('%s differs: %s' % (nm, desc))
    extra = sorted(namesb - set(names))
    txt = ['A %s' % os.path.relpath(a.a, ROOT), 'B %s' % os.path.relpath(a.b, ROOT),
           'screens: %d; identical %d; differing as allowed %d; differing otherwise %d%s' % (
               len(names), same, allowed, len(bad), ('; only in B: %s' % ' '.join(extra)) if extra else '')]
    txt += out + ['DIFF: ' + x for x in bad]
    txt.append('verdict: %s' % ('SAME' if not bad else 'DIFFERENT'))
    s = '\n'.join(txt) + '\n'
    sys.stdout.write(s)
    if a.out:
        open(a.out, 'w').write(s)
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
