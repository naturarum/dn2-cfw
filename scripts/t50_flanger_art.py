#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""T5.0: draw the flanger glyphs in the Digitone II's icon format and write them out.

    venv/bin/python scripts/t50_flanger_art.py [--out out/T5.0/art]

Three strips replace three stock strips frame for frame (same size, same count), so a mod can
write them over the stock pixel words in place (`bytes` data sites; the Bitmap objects the
static initializer 0x40151b94 builds keep pointing at the same addresses):

  flanger send  13 frames 19x17  over the chorus send (cone)   strip 0x44646bdc, data 0x402bfb1c
  FDBK          12 frames 19x17  over the chorus HPF curve     strip 0x446466d8, data 0x402b8d10
  TIME          12 frames 19x17  over the chorus WDTH ovals    strip 0x4464642c, data 0x402b48d4

Icon format (notes/T5.0-flanger-page-ui.md 2.1): column-major, stride 1 (one big-endian u32 per
column), pixel y (0 = bottom) = bit 0x80000000 >> y. Each strip's frames are 0x4c bytes apart,
right after the strip's shared mask.

Writes: <out>/art.json (per strip: address, stock bytes' sha256, new bytes hex), PNG previews
(stock above, new below), and ASCII. Reads the image only.
"""
import argparse
import hashlib
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from t50_glyphs import Img, png, sheet, PAL          # noqa: E402

W, H = 19, 17
FRAME = 0x4c


def blank():
    return [[0] * W for _ in range(H)]          # [y][x], y = 0 is the bottom


def setp(f, x, y, v=1):
    if 0 <= x < W and 0 <= y < H:
        f[y][x] = v


def vline(f, x, y0, y1, v=1):
    for y in range(min(y0, y1), max(y0, y1) + 1):
        setp(f, x, y, v)


def curve(f, ys, x0):
    """ys[i] is the curve's row in column x0 + i; steps of more than one row are joined with
    vertical runs, half in each of the two columns."""
    for i, y in enumerate(ys):
        setp(f, x0 + i, y)
        if i:
            p = ys[i - 1]
            mid = (p + y) // 2
            if y > p + 1:
                vline(f, x0 + i - 1, p + 1, mid)
                vline(f, x0 + i, mid + 1, y)
            elif p > y + 1:
                vline(f, x0 + i, y + 1, mid)
                vline(f, x0 + i - 1, mid + 1, p)


def box(f):
    """The stock knob-glyph frame (the HPF and WDTH strips' rounded box is 2..16 x 0..16)."""
    for x in range(2, 17):
        setp(f, x, 0)
        setp(f, x, 16)
    for y in range(1, 16):
        setp(f, 1, y)
        setp(f, 17, y)


def encode(f):
    out = bytearray()
    for x in range(W):
        w = 0
        for y in range(H):
            if f[y][x]:
                w |= 0x80000000 >> y
        out += w.to_bytes(4, 'big')
    return bytes(out)


def decode(b):
    f = blank()
    for x in range(W):
        w = int.from_bytes(b[4 * x:4 * x + 4], 'big')
        for y in range(H):
            f[y][x] = 1 if w & (0x80000000 >> y) else 0
    return f


def rows_top_first(f):
    return [f[y] for y in range(H - 1, -1, -1)]


# ---------------------------------------------------------------- the glyphs

def send_top():
    """Top edge of the send glyph: a wave whose period shortens left to right (a sweep)."""
    tops = {}
    for x in range(2, 17):
        t = (x - 2) / 14.0
        ph = 2 * math.pi * (0.85 * t + 0.95 * t * t)
        tops[x] = int(round(11.5 + 3.5 * math.cos(ph)))
    return tops


def send_frame(level):
    """level 0 (send off) .. 12 (full): outline, filled from the bottom up to the level."""
    f = blank()
    top = send_top()
    for x in range(3, 16):
        setp(f, x, 0)                                  # base
    setp(f, 2, 1)
    setp(f, 16, 1)
    xs = sorted(top)
    for i, x in enumerate(xs):
        y = top[x]
        setp(f, x, y)
        if i:
            p = top[xs[i - 1]]
            if y > p + 1:
                vline(f, x, p + 1, y)
            elif p > y + 1:
                vline(f, xs[i - 1], y + 1, p)
    vline(f, 2, 1, top[2])
    vline(f, 16, 1, top[16])
    if level == 0:
        for x in range(4, 15, 2):                      # dotted floor: "off", like the stock cone
            setp(f, x, 2)
        return f
    lim = 1 + int(round(level * 13 / 12.0))            # highest filled row
    for x in range(3, 16):
        for y in range(1, top[x]):
            if y <= lim:
                setp(f, x, y)
            elif y == lim + 1 and (x + y) % 2 == 0:    # dithered edge
                setp(f, x, y)
    return f


def fdbk_frame(i, n=12):
    """Feedback comb |1 / (1 - g e^-jwT)|, g from 0 to 0.9: two peaks grow sharp and tall."""
    f = blank()
    box(f)
    g = 0.9 * i / (n - 1)
    ys = []
    for x in range(3, 16):
        best = -99
        for s in range(4):                             # 4 samples per column, keep the peak
            u = (x - 3 + (s - 1.5) / 4.0) / 12.0
            w = 2 * math.pi * (2.0 * u - 0.25)         # peaks at u = 1/8 + k/2: columns 4.5, 10.5
            re, im = 1 - g * math.cos(w), g * math.sin(w)
            best = max(best, -10 * math.log10(re * re + im * im))
        ys.append(max(2, min(14, int(round(5 + best * 0.45)))))
    curve(f, ys, 3)
    return f


def time_frame(i, n=12):
    """Through-zero comb |cos(w T / 2)|: more notches as the delay time rises (0.6 to 3)."""
    f = blank()
    box(f)
    notches = 0.6 + 2.4 * i / (n - 1)
    ys = []
    for x in range(3, 16):
        lo = 99
        for s in range(4):                             # keep the notch: lowest sample
            u = (x - 3 + (s - 1.5) / 4.0) / 12.0
            lo = min(lo, 2 + 11 * abs(math.cos(math.pi * notches * u)))
        ys.append(int(round(lo)))
    curve(f, ys, 3)
    return f


STRIPS = [
    # name, strip vector, mask address, first frame, frames, generator, what it replaces
    ('flanger-send', 0x44646bdc, 0x402bfad0, 0x402bfb1c, 13, send_frame, 'chorus send (cone)'),
    ('fdbk', 0x446466d8, 0x402b8cc4, 0x402b8d10, 12, fdbk_frame, 'chorus HPF curve'),
    ('time', 0x4464642c, 0x402b4888, 0x402b48d4, 12, time_frame, 'chorus WDTH ovals'),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default=os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), 'out/T5.0/art'))
    ap.add_argument('--header', help='also write the new frames as a C header (our art only)')
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    img = Img()
    res = []
    for name, vec, mask, first, n, gen, what in STRIPS:
        stock = img.read(first, FRAME * n)
        m = img.read(mask, FRAME)
        if any(int.from_bytes(m[i:i + 4], 'big') != 0xffff8000 for i in range(0, FRAME, 4)):
            raise SystemExit('%s: the stock mask is not a full 19x17 rectangle' % name)
        frames = [gen(i) for i in range(n)]
        new = b''.join(encode(f) for f in frames)
        assert len(new) == len(stock)
        for f, enc in zip(frames, (new[k:k + FRAME] for k in range(0, len(new), FRAME))):
            assert decode(enc) == f
        old = [rows_top_first(decode(stock[k:k + FRAME])) for k in range(0, len(stock), FRAME)]
        nw = [rows_top_first(f) for f in frames]
        png(os.path.join(a.out, name + '.png'), sheet(old + nw, n), 6, PAL)
        with open(os.path.join(a.out, name + '.txt'), 'w') as fh:
            for lst, title in ((old, 'stock'), (nw, 'new')):
                fh.write('%s %s\n' % (title, name))
                for y in range(H):
                    fh.write('  '.join(''.join('.#'[c] for c in r[y]) for r in lst) + '\n')
        res.append({'name': name, 'replaces': what, 'strip': '0x%08x' % vec, 'mask': '0x%08x' % mask,
                    'addr': '0x%08x' % first, 'frames': n, 'len': len(new),
                    'stock_sha256': hashlib.sha256(stock).hexdigest(), 'stock': stock.hex(),
                    'new': new.hex()})
        print('%-13s %2d frames at 0x%08x (%d bytes), replaces the %s' % (name, n, first, len(new), what))
    with open(os.path.join(a.out, 'art.json'), 'w') as fh:
        json.dump(res, fh, indent=1)
    if a.header:
        write_header(a.header, res)


def write_header(path, res):
    """The new frames only (no stock bytes): u32 columns, one row of 19 per frame."""
    names = {'flanger-send': 'FLUI_SEND', 'fdbk': 'FLUI_FDBK', 'time': 'FLUI_TIME'}
    out = ['/* SPDX-License-Identifier: GPL-2.0-or-later */',
           '/* Generated by Elektron-CFW scripts/t50_flanger_art.py: the flanger glyphs, 19 x 17,',
           ' * one u32 per column, pixel y (0 = bottom) = bit 0x80000000 >> y. Do not edit. */',
           '#define FLUI_W 19', '#define FLUI_H 17', '']
    out.append('static const unsigned int FLUI_MASK[FLUI_W] = {%s};' % ', '.join(['0xffff8000u'] * W))
    for r in res:
        nm = names[r['name']]
        b = bytes.fromhex(r['new'])
        out.append('#define %s_N %d' % (nm, r['frames']))
        out.append('static const unsigned int %s[%s_N][FLUI_W] = {' % (nm, nm))
        for k in range(r['frames']):
            fr = b[k * FRAME:(k + 1) * FRAME]
            out.append('    {%s},' % ', '.join('0x%08xu' % int.from_bytes(fr[i:i + 4], 'big')
                                              for i in range(0, FRAME, 4)))
        out.append('};')
    with open(path, 'w') as fh:
        fh.write('\n'.join(out) + '\n')


if __name__ == '__main__':
    main()
