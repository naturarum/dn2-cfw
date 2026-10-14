#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""T6.3 CMP1: contact sheets of emulator screens, each with a caption, as one PNG.

    venv/bin/python scripts/t63b_sheet.py OUT.png [--cols 4] [--scale 2] [--title TEXT] DIR:SCREEN[=CAPTION] ...

Each DIR:SCREEN is an emulator screen (DIR/SCREEN.txt, 128x64, '#' = pixel on) drawn at --scale with
its caption above it, in the unit's own title font (0x40290dd0, read from the stock image by
scripts/t50_glyphs.py). No image library is needed: the PNG is written by hand (grey levels).
"""
import argparse
import os
import struct
import sys
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scripts'))
from t50_glyphs import Img, font_info, glyph   # noqa: E402

FONT = 0x40290dd0
_img = Img()
_fi = font_info(_img, FONT)
_cache = {}


def text_rows(s):
    """-> rows (top first) of 0/1 for s in the title font, 1 px between glyphs"""
    h = _fi['height']
    cols = []
    for ch in s:
        c = ord(ch) & 0xff
        if c not in _cache:
            _cache[c] = glyph(_img, _fi, c)
        g = _cache[c]
        if g is None:
            g = [[0] * 3 for _ in range(h)]
        w = len(g[0])
        for x in range(w):
            cols.append([g[y][x] for y in range(h)])
        cols.append([0] * h)
    return [[col[y] for col in cols] for y in range(h)]


def load(path):
    rows = [l.rstrip('\n') for l in open(path)][:64]
    return [[1 if c == '#' else 0 for c in r[:128].ljust(128)] for r in rows]


def write_png(path, img):
    h, w = len(img), len(img[0])
    raw = b''.join(b'\0' + bytes(r) for r in img)

    def chunk(t, d):
        return struct.pack('>I', len(d)) + t + d + struct.pack('>I', zlib.crc32(t + d) & 0xffffffff)
    with open(path, 'wb') as fh:
        fh.write(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 0, 0, 0, 0)) +
                 chunk(b'IDAT', zlib.compress(raw, 9)) + chunk(b'IEND', b''))


def blit(img, rows, ox, oy, scale, on=255, off=None):
    for y, r in enumerate(rows):
        for x, v in enumerate(r):
            if not v and off is None:
                continue
            c = on if v else off
            for dy in range(scale):
                yy = oy + y * scale + dy
                if 0 <= yy < len(img):
                    line = img[yy]
                    for dx in range(scale):
                        xx = ox + x * scale + dx
                        if 0 <= xx < len(line):
                            line[xx] = c


def sheet(items, cols, scale, title=None):
    cap_h = (_fi['height'] + 3) * scale
    W, H = 128 * scale + 8 * scale, 64 * scale + cap_h + 6 * scale
    top = (_fi['height'] + 6) * scale if title else 0
    rows_n = (len(items) + cols - 1) // cols
    img = [[24] * (W * cols) for _ in range(top + H * rows_n)]
    if title:
        blit(img, text_rows(title), 4 * scale, 2 * scale, scale, on=255)
    for i, (scr, cap) in enumerate(items):
        ox, oy = (i % cols) * W + 4 * scale, top + (i // cols) * H + 2 * scale
        blit(img, text_rows(cap), ox, oy, scale, on=200)
        blit(img, scr, ox, oy + cap_h, scale, on=255, off=0)
    return img


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('out')
    ap.add_argument('items', nargs='+')
    ap.add_argument('--cols', type=int, default=4)
    ap.add_argument('--scale', type=int, default=2)
    ap.add_argument('--title')
    a = ap.parse_args()
    items = []
    for it in a.items:
        spec, _, cap = it.partition('=')
        d, _, scr = spec.rpartition(':')
        items.append((load(os.path.join(d, scr + '.txt')), cap or scr))
    write_png(a.out, sheet(items, a.cols, a.scale, a.title))
    print('%s: %d screens' % (a.out, len(items)))


if __name__ == '__main__':
    main()
