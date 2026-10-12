#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""T5.0b: compare the tour's screens and LEDs, stock against FTN1, pixel for pixel; write the sheet.

    venv/bin/python scripts/t50b_compare.py [--a stock] [--b FTN1] [--script t50b-tour]

Reads out/T5.0b/emu/{A,B}/{SCRIPT}/*.txt (the emulator's 128x64 ASCII screens, '#' = pixel on) and
*.leds.json. For each screen: identical, or the boxes (x0..x1, y0..y1; x from the left, y from the
TOP row) that hold every differing pixel, found by grouping differing pixels that lie within 3 px of
each other. Writes out/T5.0b/compare.txt, compare.json and before-after.png (stock | FTN1 | the
difference: changed pixels white, unchanged pixels that are on dim) for the screens that differ, plus
the reverb page as the unchanged reference.
"""
import argparse
import glob
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scripts'))
from t50_glyphs import png                       # noqa: E402

OUT = os.path.join(ROOT, 'out', 'T5.0b')


def screen(path):
    rows = [l.rstrip('\n') for l in open(path)]
    s = [[1 if c == '#' else 0 for c in r[:128]] for r in rows[:64]]
    if len(s) != 64 or any(len(r) != 128 for r in s):
        raise SystemExit('%s: not a 128x64 screen' % path)
    return s


def boxes(diff):
    pts = [(x, y) for y in range(64) for x in range(128) if diff[y][x]]
    out = []
    for p in pts:
        hit = [b for b in out if b[0] - 3 <= p[0] <= b[2] + 3 and b[1] - 3 <= p[1] <= b[3] + 3]
        nb = [p[0], p[1], p[0], p[1], 1]
        for b in hit:
            nb = [min(nb[0], b[0]), min(nb[1], b[1]), max(nb[2], b[2]), max(nb[3], b[3]), nb[4] + b[4]]
            out.remove(b)
        out.append(nb)
    merged = True                                   # boxes that grew into each other
    while merged:
        merged = False
        for i in range(len(out)):
            for j in range(i + 1, len(out)):
                a, b = out[i], out[j]
                if a[0] - 3 <= b[2] and b[0] - 3 <= a[2] and a[1] - 3 <= b[3] and b[1] - 3 <= a[3]:
                    out[i] = [min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]), a[4] + b[4]]
                    del out[j]
                    merged = True
                    break
            if merged:
                break
    return sorted(out, key=lambda b: (b[1], b[0]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--a', default='stock')
    ap.add_argument('--b', default='FTN1')
    ap.add_argument('--script', default='t50b-tour')
    a = ap.parse_args()
    da = os.path.join(OUT, 'emu', a.a, a.script)
    db = os.path.join(OUT, 'emu', a.b, a.script)
    names = sorted(os.path.basename(p)[:-4] for p in glob.glob(os.path.join(da, '*.txt')) if '.leds' not in p)
    res, lines = [], []
    for n in names:
        sa, sb = screen(os.path.join(da, n + '.txt')), screen(os.path.join(db, n + '.txt'))
        diff = [[sa[y][x] ^ sb[y][x] for x in range(128)] for y in range(64)]
        bx = boxes(diff)
        la = json.load(open(os.path.join(da, n + '.leds.json')))
        lb = json.load(open(os.path.join(db, n + '.leds.json')))
        r = {'screen': n, 'pixels': sum(map(sum, diff)), 'boxes': [b[:4] for b in bx],
             'leds_identical': la == lb}
        res.append(r)
        lines.append('%-28s %s; LEDs %s' % (
            n, 'identical' if not bx else '%d px differ in %s' % (
                r['pixels'], ', '.join('x %d-%d y %d-%d' % (b[0], b[2], b[1], b[3]) for b in bx)),
            'identical' if r['leds_identical'] else 'DIFFER'))
    with open(os.path.join(OUT, 'compare.txt'), 'w') as f:
        f.write('# %s against %s, script %s; y counted from the top row\n' % (a.b, a.a, a.script))
        f.write('\n'.join(lines) + '\n')
    with open(os.path.join(OUT, 'compare.json'), 'w') as f:
        json.dump(res, f, indent=1)
    print('\n'.join(lines))

    # the sheet: stock | FTN1 | difference
    G = 2
    keep = [r['screen'] for r in res if r['pixels'] or r['screen'] == '06-sendfx2']
    rows = []
    for n in keep:
        sa, sb = screen(os.path.join(da, n + '.txt')), screen(os.path.join(db, n + '.txt'))
        for y in range(64):
            d = [1 if sa[y][x] != sb[y][x] else (3 if sa[y][x] else 0) for x in range(128)]
            rows.append([G] * 4 + sa[y] + [G] * 6 + sb[y] + [G] * 6 + d + [G] * 4)
        rows += [[G] * len(rows[-1]) for _ in range(4)]
    if rows:
        rows = [[G] * len(rows[0]) for _ in range(4)] + rows
        png(os.path.join(OUT, 'before-after.png'), rows, 3, (0, 255, 110, 70))
    print('%d screens on the sheet: %s' % (len(keep), ' '.join(keep)))


if __name__ == '__main__':
    main()
