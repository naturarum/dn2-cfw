#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Digiwerk's pixel identity: one icon per effect, a mark and a wordmark, in the
Digitone II screen's style (white pixels on black, 1-pixel strokes, big pixels).

Our own drawings and our own 5x7 letters (not Elektron's font). Writes, next to
this script: <name>.svg (crisp at any size, for GitHub and the patcher),
<name>.png (8x, with the small gaps between pixels of an OLED) and sheet.png.

Usage: python3 docs/identity/make_icons.py
"""
import math
import pathlib

from PIL import Image, ImageDraw

HERE = pathlib.Path(__file__).resolve().parent
FG, BG = (242, 242, 242), (0, 0, 0)
W, H = 24, 16                      # an effect icon, about a knob icon on the screen

FONT = {
    'D': ['####.', '#...#', '#...#', '#...#', '#...#', '#...#', '####.'],
    'I': ['###', '.#.', '.#.', '.#.', '.#.', '.#.', '###'],
    'G': ['.###.', '#...#', '#....', '#.###', '#...#', '#...#', '.###.'],
    'W': ['#...#', '#...#', '#...#', '#.#.#', '#.#.#', '##.##', '#...#'],
    'E': ['#####', '#....', '#....', '####.', '#....', '#....', '#####'],
    'R': ['####.', '#...#', '#...#', '####.', '#.#..', '#..#.', '#...#'],
    'K': ['#...#', '#..#.', '#.#..', '##...', '#.#..', '#..#.', '#...#'],
    'T': ['#####', '..#..', '..#..', '..#..', '..#..', '..#..', '..#..'],
    'O': ['.###.', '#...#', '#...#', '#...#', '#...#', '#...#', '.###.'],
    'F': ['#####', '#....', '#....', '####.', '#....', '#....', '#....'],
    'L': ['#....', '#....', '#....', '#....', '#....', '#....', '#####'],
    'U': ['#...#', '#...#', '#...#', '#...#', '#...#', '#...#', '.###.'],
    'X': ['#...#', '#...#', '.#.#.', '..#..', '.#.#.', '#...#', '#...#'],
    'C': ['.###.', '#...#', '#....', '#....', '#....', '#...#', '.###.'],
    'M': ['#...#', '##.##', '#.#.#', '#.#.#', '#...#', '#...#', '#...#'],
    'P': ['####.', '#...#', '#...#', '####.', '#....', '#....', '#....'],
    'A': ['.###.', '#...#', '#...#', '#####', '#...#', '#...#', '#...#'],
    'N': ['#...#', '##..#', '#.#.#', '#.#.#', '#..##', '#...#', '#...#'],
    'S': ['.####', '#....', '#....', '.###.', '....#', '....#', '####.'],
    '3': ['####.', '....#', '....#', '.###.', '....#', '....#', '####.'],
    ' ': ['...', '...', '...', '...', '...', '...', '...'],
}


def blank(w, h):
    return [[0] * w for _ in range(h)]


def line(g, x0, y0, x1, y1):
    """Bresenham, both ends included."""
    dx, dy = abs(x1 - x0), -abs(y1 - y0)
    sx, sy = (1 if x0 < x1 else -1), (1 if y0 < y1 else -1)
    err = dx + dy
    while True:
        if 0 <= y0 < len(g) and 0 <= x0 < len(g[0]):
            g[y0][x0] = 1
        if (x0, y0) == (x1, y1):
            return
        e2 = 2 * err
        if e2 >= dy:
            err += dy
            x0 += sx
        if e2 <= dx:
            err += dx
            y0 += sy


def rect(g, x0, y0, x1, y1, fill=False):
    for y in range(y0, y1 + 1):
        for x in range(x0, x1 + 1):
            if fill or y in (y0, y1) or x in (x0, x1):
                g[y][x] = 1


def text(s):
    rows = ['' for _ in range(7)]
    for i, ch in enumerate(s):
        for r in range(7):
            rows[r] += FONT[ch][r] + ('.' if i < len(s) - 1 else '')
    return [[1 if c == '#' else 0 for c in row] for row in rows]


# --- the effects ------------------------------------------------------------

def cmp3():
    """The 3-band compressor: three gain-reduction meters hanging from the top,
    each band squeezed by a different amount."""
    g = blank(W, H)
    for x0, depth in ((1, 6), (9, 9), (17, 3)):     # LOW, MID, HIGH
        rect(g, x0, 1, x0 + 5, 14)
        rect(g, x0 + 2, 3, x0 + 3, 2 + depth, fill=True)
    return g


def flng():
    """The flanger's TIME knob: one wave whose period grows from left to right,
    phaser-like at the short end, chorus at the long end."""
    g = blank(W, H)
    p0, p1 = 5.0, 22.0

    def y_at(x):                                   # x continuous; the phase integrates 1/period
        k = math.log(p1 / p0) / (W - 1)
        ph = 2 * math.pi * (1 - math.exp(-k * x)) / (k * p0)
        return 7.5 - 6.0 * math.sin(ph)

    for x in range(W):
        a, b = y_at(max(x - 0.5, 0)), y_at(min(x + 0.5, W - 1))
        for y in range(int(round(min(a, b))), int(round(max(a, b))) + 1):
            g[y][x] = 1
    return g


def tape():
    """The tape echo: a sound and its repeats, each one quieter."""
    g = blank(W, H)
    for x0, h in ((1, 13), (6, 10), (11, 7), (16, 5), (21, 3)):
        rect(g, x0, 14 - h + 1, x0 + 1, 14, fill=True)
    line(g, 0, 15, W - 1, 15)
    return g


def spng():
    """The spring reverb: a spring held between two posts."""
    g = blank(W, H)
    rect(g, 0, 3, 1, 12, fill=True)
    rect(g, 22, 3, 23, 12, fill=True)
    pts = [(2, 8), (3, 5)] + [(x, 11 if i % 2 == 0 else 5) for i, x in enumerate(range(6, 20, 3))] + [(20, 8), (21, 8)]
    for (xa, ya), (xb, yb) in zip(pts, pts[1:]):
        line(g, xa, ya, xb, yb)
    return g


def mark():
    """Digiwerk's mark: a cog ("werk") with a pixel in its hub."""
    n, g = 16, blank(16, 16)
    c = (n - 1) / 2
    for y in range(n):
        for x in range(n):
            r, a = math.hypot(x - c, y - c), math.atan2(y - c, x - c)
            outer = 7.6 if math.cos(8 * a) > 0.25 else 5.9
            if 2.9 < r <= outer:
                g[y][x] = 1
    g[7][7] = g[7][8] = g[8][7] = g[8][8] = 1
    return g


ICONS = {'cmp3': cmp3, 'flng': flng, 'tape': tape, 'spng': spng, 'mark': mark}
LABELS = {'cmp3': '3-band compressor', 'flng': 'flanger: phaser to chorus', 'tape': 'tape echo',
          'spng': 'spring reverb', 'mark': 'Digiwerk'}


# --- output -----------------------------------------------------------------

def svg(g, px=1):
    h, w = len(g), len(g[0])
    rects = ''.join('<rect x="%d" y="%d" width="1" height="1"/>' % (x, y)
                    for y in range(h) for x in range(w) if g[y][x])
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="-1 -1 %d %d" width="%d" height="%d" '
            'shape-rendering="crispEdges"><rect x="-1" y="-1" width="%d" height="%d" fill="#000"/>'
            '<g fill="#f2f2f2">%s</g></svg>\n') % (w + 2, h + 2, (w + 2) * 8, (h + 2) * 8, w + 2, h + 2, rects)


def paint(img, g, ox, oy, s, gap):
    d = ImageDraw.Draw(img)
    for y, row in enumerate(g):
        for x, on in enumerate(row):
            if on:
                d.rectangle([ox + x * s, oy + y * s, ox + x * s + s - 1 - gap, oy + y * s + s - 1 - gap], fill=FG)


def png(g, s=8, gap=1, pad=1):
    h, w = len(g), len(g[0])
    img = Image.new('RGB', ((w + 2 * pad) * s, (h + 2 * pad) * s), BG)
    paint(img, g, pad * s, pad * s, s, gap)
    return img


def sheet(grids):
    s, gap, cell_w, cell_h = 8, 1, 34 * 8, 32 * 8
    names = ['cmp3', 'flng', 'tape', 'spng']
    img = Image.new('RGB', (len(names) * cell_w, cell_h + 26 * 8), BG)
    word = text('DIGIWERK')
    paint(img, grids['mark'], 4 * s, 4 * s, s, gap)
    paint(img, word, 24 * s, 8 * s, s, gap)
    for i, n in enumerate(names):
        ox = i * cell_w + (cell_w - W * s) // 2
        paint(img, grids[n], ox, 26 * s, s, gap)
        lab = text(n.upper())
        paint(img, lab, i * cell_w + (cell_w - len(lab[0]) * 4) // 2, 46 * s, 4, 0)
    return img


def main():
    grids = {n: f() for n, f in ICONS.items()}
    for n, g in grids.items():
        (HERE / (n + '.svg')).write_text(svg(g))
        png(g).save(HERE / (n + '.png'))
    (HERE / 'wordmark.svg').write_text(svg(text('DIGIWERK')))
    png(text('DIGIWERK')).save(HERE / 'wordmark.png')
    sheet(grids).save(HERE / 'sheet.png')
    for n, g in grids.items():
        print(n, LABELS[n])
        print('\n'.join(''.join('#' if v else '.' for v in row) for row in g))


if __name__ == '__main__':
    main()
