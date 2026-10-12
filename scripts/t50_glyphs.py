#!/usr/bin/env python3
"""T5.0: extract the Digitone II 1.12 UI fonts and icons to PNG atlases and a contact sheet.

    venv/bin/python scripts/t50_glyphs.py [--snap out/T5.0/snaps/chorus.snap] [--out out/T5.0/atlas]

Formats (notes/T5.0-flanger-page-ui.md):

* Screen coordinates: x 0..127 left to right, y 0..63 from the BOTTOM row up.
* Bitmap / icon object (vtable 0x402069c4, 0x1c bytes, built by Bitmap(this, w, h, data, mask) =
  0x40113e2a): +4 w, +8 h, +0xc stride (32-bit words per column), +0x10 data, +0x14 mask, +0x18 0.
  Column-major: column x is `stride` big-endian words; row y is bit (0x80000000 >> (y & 31)) of word
  (y >> 5). Blit 0x40115adc: dst = mask ? data : dst.
* Font (17 of them, registered by 0x401ba106 as {vtable 0x402069dc, font*} at 0x44508e78 + 8k):
  +0 max width, +4 (ascent << 16) | height, +8 widths (s8 x 256), +0xc offsets (s16 x 256, -1 = none),
  +0x10 glyph columns: one byte per column if height < 8, one u16 if height <= 16, else one u32;
  MSB = the glyph's bottom row. drawText 0x401153f8(bmp, font, x, y, mono, maxlen, str) draws from
  y - ascent upward, advancing width + 1.

The icon objects and their std::vector "strips" (frames picked by value, 0x40115e3e) are built at
boot by the static initializer 0x40151b94, so they are read from a snapshot's RAM; their pixel data
is in the image. Read-only: writes PNGs and JSON under --out only.
"""
import argparse
import html
import json
import os
import pickle
import struct
import sys
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMAGE = os.path.join(ROOT, 'ele3_out', '02_type3.unpacked.bin')
LOAD = 0x40000400
BMP_VT = 0x402069c4
FONT_REG = 0x401ba106          # static init: move.l #0x402069dc,D0; move.l D0,(slot).l; move.l #font,...

# What the chorus page (SEND FX 3) and the track FX page draw, from the call traces
# (out/T5.0/trace/*-calls.json): strip vector address -> name.
NAMED = {
    0x4464680c: 'knob (DPTH, SPD on the chorus page; 96 frames)',
    0x446466d8: 'high-pass curve (chorus HPF)',
    0x4464642c: 'stereo width (chorus WDTH)',
    0x44646a5c: 'delay send (DEL: track FX page E, chorus page G)',
    0x44645ca0: 'reverb send (REV: track FX page F, chorus page H)',
    0x44646bdc: 'chorus send (CHR: track FX page G)',
    0x44646980: 'fader (VOL, chorus page LEVEL)',
    0x44646c8c: 'fader (LEV, track FX page LEVEL)',
    0x44645e50: 'bit reduction (BR)',
    0x44647c44: 'overdrive knob (OVER)',
    0x446471d0: 'sample-rate reduction (SRR)',
    0x446471f8: 'routing PRE/POST (SR.RT, OD.RT)',
    0x44647db4: 'track box (SYN 1 ..)',
}
SINGLE = {
    0x446462a8: 'empty slot (dotted box, chorus page E/F)',
    0x44645f98: 'unavailable parameter box',
    0x446482a8: 'header icon (6x6)',
    0x4464790c: 'locked parameter icon',
}


class Img:
    def __init__(self, path=IMAGE):
        self.d = open(path, 'rb').read()

    def has(self, a):
        return LOAD <= a < LOAD + len(self.d)

    def read(self, a, n):
        o = a - LOAD
        return self.d[o:o + n]

    def u32(self, a):
        return struct.unpack('>I', self.read(a, 4))[0]


class _U(pickle.Unpickler):
    def find_class(self, m, n):
        raise pickle.UnpicklingError('%s.%s' % (m, n))


class Snap:
    """RAM of a digikit snapshot (pickled dict, 'pages': {base: zlib(1 MiB)})."""

    def __init__(self, path):
        blob = _U(open(path, 'rb')).load()
        self.pages = {k: zlib.decompress(v) for k, v in blob['pages'].items()}
        self.ps = len(next(iter(self.pages.values())))

    def read(self, a, n):
        out = b''
        while n > 0:
            base = a - a % self.ps
            off = a - base
            k = min(n, self.ps - off)
            p = self.pages.get(base)
            out += p[off:off + k] if p else bytes(k)
            a += k
            n -= k
        return out

    def u32(self, a):
        return struct.unpack('>I', self.read(a, 4))[0]


# ---------------------------------------------------------------- fonts

def font_registry(img):
    """-> [(slot, font)] from the static initializer 0x401ba106."""
    out, a, val = [], FONT_REG, None
    while True:
        op = struct.unpack('>H', img.read(a, 2))[0]
        if op == 0x4e75:                                   # rts
            break
        if op == 0x203c:                                   # move.l #imm,D0
            val = img.u32(a + 2)
            a += 6
        elif op == 0x23c0:                                 # move.l D0,(abs).l
            slot = img.u32(a + 2)
            if val != 0x402069dc:
                out.append((slot, val))
            a += 6
        else:
            raise SystemExit('font registry: unexpected opcode %04x at 0x%08x' % (op, a))
    return sorted(out)


def font_info(img, f):
    w = struct.unpack('>5I', img.read(f, 20))
    return {'addr': f, 'maxw': w[0], 'ascent': w[1] >> 16, 'height': w[1] & 0xffff,
            'widths': w[2], 'offsets': w[3], 'data': w[4]}


def glyph(img, fi, c):
    """-> list of rows (top first) of 0/1, or None."""
    w = struct.unpack('b', img.read(fi['widths'] + c, 1))[0]
    off = struct.unpack('>h', img.read(fi['offsets'] + 2 * c, 2))[0]
    if off < 0 or w <= 0:
        return None
    h = fi['height']
    unit = 1 if h < 8 else (2 if h <= 16 else 4)
    cols = [int.from_bytes(img.read(fi['data'] + (off + x) * unit, unit), 'big') for x in range(w)]
    top = unit * 8 - 1
    # bit (top - k) is the glyph's row k counted from its bottom; print top row first
    return [[(cols[x] >> (top - k)) & 1 for x in range(w)] for k in range(h - 1, -1, -1)]


# ---------------------------------------------------------------- icons

def icon_obj(mem, a):
    w = struct.unpack('>7I', mem.read(a, 28))
    if w[0] != BMP_VT or not (1 <= w[1] <= 128 and 1 <= w[2] <= 64 and 1 <= w[3] <= 2):
        return None
    return {'addr': a, 'w': w[1], 'h': w[2], 'stride': w[3], 'data': w[4], 'mask': w[5]}


def icon_pixels(img, mem, ic):
    """-> rows (top first) of 0 (black), 1 (white), 2 (transparent: mask 0)."""
    src = img if img.has(ic['data']) else mem
    n = ic['w'] * ic['stride'] * 4
    data = src.read(ic['data'], n)
    msrc = img if img.has(ic['mask']) else mem
    mask = msrc.read(ic['mask'], n) if ic['mask'] else b'\xff' * n
    rows = []
    for y in range(ic['h'] - 1, -1, -1):
        r = []
        for x in range(ic['w']):
            o = (x * ic['stride'] + (y >> 5)) * 4
            bit = 0x80000000 >> (y & 31)
            dv = struct.unpack('>I', data[o:o + 4])[0] & bit
            mv = struct.unpack('>I', mask[o:o + 4])[0] & bit
            r.append(2 if not mv else (1 if dv else 0))
        rows.append(r)
    return rows


def find_icons(mem):
    out = {}
    vt = struct.pack('>I', BMP_VT)
    for base, page in mem.pages.items():
        i = 0
        while True:
            i = page.find(vt, i)
            if i < 0:
                break
            if i % 2 == 0 and i + 28 <= len(page):
                ic = icon_obj(mem, base + i)
                if ic:
                    out[base + i] = ic
            i += 2
    return out


def find_strips(mem, icons):
    """std::vector<Bitmap>: {begin, end, cap} with every element an icon object."""
    strips = {}
    for base, page in mem.pages.items():
        for o in range(0, len(page) - 12, 4):
            b, e, c = struct.unpack('>3I', page[o:o + 12])
            if b in icons and e > b and (e - b) % 28 == 0 and c >= e and (e - b) // 28 <= 256:
                if all(b + 28 * k in icons for k in range((e - b) // 28)):
                    strips[base + o] = (b, (e - b) // 28)
    return strips


# ---------------------------------------------------------------- PNG

def png(path, rows, scale=1, palette=(0, 255, 96)):
    h = len(rows) * scale
    w = (len(rows[0]) if rows else 0) * scale
    raw = bytearray()
    for r in rows:
        line = bytes(palette[v] for v in r for _ in range(scale))
        for _ in range(scale):
            raw += b'\0' + line

    def chunk(tag, data):
        c = tag + data
        return struct.pack('>I', len(data)) + c + struct.pack('>I', zlib.crc32(c))
    with open(path, 'wb') as f:
        f.write(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 0, 0, 0, 0))
                + chunk(b'IDAT', zlib.compress(bytes(raw), 9)) + chunk(b'IEND', b''))


def sheet(cells, cols, pad=2, bg=3):
    """cells: list of row-lists (values 0/1/2); -> one image (value 3 = gutter)."""
    if not cells:
        return [[bg]]
    cw = max(len(c[0]) if c else 0 for c in cells) + pad
    ch = max(len(c) for c in cells) + pad
    nr = (len(cells) + cols - 1) // cols
    out = [[bg] * (cols * cw + pad) for _ in range(nr * ch + pad)]
    for i, c in enumerate(cells):
        ox, oy = pad + (i % cols) * cw, pad + (i // cols) * ch
        for y, r in enumerate(c):
            for x, v in enumerate(r):
                out[oy + y][ox + x] = v
    return out


PAL = (0, 255, 110, 40)        # black, white, transparent (mask 0), gutter


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--snap', default=os.path.join(ROOT, 'out/T5.0/snaps/chorus.snap'))
    ap.add_argument('--out', default=os.path.join(ROOT, 'out/T5.0/atlas'))
    ap.add_argument('--scale', type=int, default=4)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    img, mem = Img(), Snap(a.snap)
    index = {'fonts': [], 'strips': [], 'singles': []}

    # fonts
    for k, (slot, f) in enumerate(font_registry(img)):
        fi = font_info(img, f)
        cells, chars = [], []
        for c in range(256):
            g = glyph(img, fi, c)
            if g is not None and any(any(r) for r in g) or (g is not None and c == 32):
                cells.append(g)
                chars.append(c)
        name = 'font-%02d-%08x-%dx%d.png' % (k, f, fi['maxw'], fi['height'])
        png(os.path.join(a.out, name), sheet(cells, 32), a.scale, PAL)
        fi.update({'slot': slot, 'png': name, 'chars': chars, 'glyphs': len(chars)})
        index['fonts'].append(fi)

    # icons and strips
    icons = find_icons(mem)
    strips = find_strips(mem, icons)
    in_strip = set()
    for vaddr, (b, n) in sorted(strips.items()):
        frames = [icon_pixels(img, mem, icons[b + 28 * i]) for i in range(n)]
        in_strip.update(b + 28 * i for i in range(n))
        f0 = icons[b]
        name = 'strip-%08x-%dx%d-%d.png' % (vaddr, f0['w'], f0['h'], n)
        png(os.path.join(a.out, name), sheet(frames, 16), a.scale, PAL)
        index['strips'].append({'vector': '0x%08x' % vaddr, 'frames': n, 'w': f0['w'], 'h': f0['h'],
                                'first_data': '0x%08x' % f0['data'], 'mask': '0x%08x' % f0['mask'],
                                'name': NAMED.get(vaddr, ''), 'png': name,
                                'data': ['0x%08x' % icons[b + 28 * i]['data'] for i in range(n)]})
    singles = [ic for ad, ic in sorted(icons.items()) if ad not in in_strip
               and img.has(ic['data'])]
    groups = {}
    for ic in singles:
        groups.setdefault((ic['addr'] >> 16) << 16, []).append(ic)
    for g, lst in sorted(groups.items()):
        cells = [icon_pixels(img, mem, ic) for ic in lst]
        name = 'icons-%08x-%d.png' % (g, len(lst))
        png(os.path.join(a.out, name), sheet(cells, 16), a.scale, PAL)
        index['singles'].append({'region': '0x%08x' % g, 'count': len(lst), 'png': name,
                                 'icons': [{'addr': '0x%08x' % ic['addr'], 'w': ic['w'], 'h': ic['h'],
                                            'data': '0x%08x' % ic['data'],
                                            'name': SINGLE.get(ic['addr'], '')} for ic in lst]})
    with open(os.path.join(a.out, 'index.json'), 'w') as f:
        json.dump(index, f, indent=1)
    print('fonts %d, icon objects %d (in %d strips: %d; single: %d)' % (
        len(index['fonts']), len(icons), len(strips), len(in_strip), len(singles)))
    return index


if __name__ == '__main__':
    main()
