#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""T6.5: a contact sheet of a run's screens (the emulator's 128x64 ASCII screens) as one PNG.

    venv/bin/python scripts/t65_sheet.py DIR OUT.png [--cols 3] [--scale 2] [--match GLOB]
    venv/bin/python scripts/t65_sheet.py --pairs A_DIR B_DIR OUT.png [--match GLOB]

--pairs puts A, B and their difference (changed pixels white, unchanged lit pixels dim) side by
side for every screen that differs (or every screen matching --match).
"""
import argparse
import fnmatch
import glob
import os
import struct
import zlib


def load(path):
    rows = [l.rstrip('\n') for l in open(path)][:64]
    return [[1 if c == '#' else 0 for c in r[:128].ljust(128)] for r in rows]


def write_png(path, img):
    h, w = len(img), len(img[0])
    raw = b''.join(b'\0' + bytes(r) for r in img)

    def chunk(t, d):
        return struct.pack('>I', len(d)) + t + d + struct.pack('>I', zlib.crc32(t + d) & 0xffffffff)
    open(path, 'wb').write(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 0, 0, 0, 0)) +
                           chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b''))


def sheet(screens, cols, scale):
    W, H = 128 * scale + 6, 64 * scale + 6
    rows = (len(screens) + cols - 1) // cols
    img = [[40] * (W * cols) for _ in range(H * rows)]
    for i, s in enumerate(screens):
        ox, oy = (i % cols) * W + 3, (i // cols) * H + 3
        for y in range(64):
            for x in range(128):
                v = s[y][x]
                for dy in range(scale):
                    for dx in range(scale):
                        img[oy + y * scale + dy][ox + x * scale + dx] = v
    return img


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('args', nargs='+')
    ap.add_argument('--pairs', action='store_true')
    ap.add_argument('--cols', type=int, default=3)
    ap.add_argument('--scale', type=int, default=2)
    ap.add_argument('--match', default='*')
    a = ap.parse_args()
    if a.pairs:
        da, db, out = a.args
        screens = []
        for p in sorted(glob.glob(os.path.join(da, '*.txt'))):
            nm = os.path.basename(p)
            if nm.endswith('.leds.txt') or not fnmatch.fnmatch(nm[:-4], a.match):
                continue
            sa, sb = load(p), load(os.path.join(db, nm))
            if sa == sb and a.match == '*':
                continue
            d = [[255 if sa[y][x] != sb[y][x] else (70 if sa[y][x] else 0) for x in range(128)] for y in range(64)]
            screens += [[[255 * v for v in r] for r in sa], [[255 * v for v in r] for r in sb], d]
        write_png(out, sheet(screens, 3, a.scale) if screens else [[0] * 8])
        print('%s: %d screens' % (out, len(screens) // 3))
        return
    d, out = a.args
    files = sorted(p for p in glob.glob(os.path.join(d, '*.txt'))
                   if not p.endswith('.leds.txt') and fnmatch.fnmatch(os.path.basename(p)[:-4], a.match))
    screens = [[[255 * v for v in r] for r in load(p)] for p in files]
    write_png(out, sheet(screens, a.cols, a.scale))
    print('%s: %d screens: %s' % (out, len(files), ' '.join(os.path.basename(p)[:-4] for p in files)))


if __name__ == '__main__':
    main()
