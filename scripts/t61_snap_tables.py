#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""T6.1: dump, from a warm digikit snapshot of the stock DN2 OS 1.12, every run-time table that is
built from the parameter descriptor table (0x401f83a4, 0x141 rows of 0x3c bytes) or indexed by row id.

  rows      per row: group, k, CC, CC2, NRPN, UID (+0x20), mod mask, formatter, and the display
            table entry (0x4243425c + 0x44*row): flags and the managers/invokers of its four
            std::function slots (+0x04, +0x14 glyph, +0x24 randomiser, +0x34)
  pages     the 37 page records (0x42433c00 + 0x2c*id): name, rows on A-H and LEVEL
  sets      slot -> row tables (MIDI, trig, FX, sound, filter 6x3, machine 5x40) and free slots
  ccmaps    CC -> row and NRPN -> row arrays for sound, FX and MIDI, plus the per-machine and
            per-filter std::map trees; which rows carry a CC/NRPN but are in no map
  cache     the ParameterUpdateCache singleton (vtable 0x401d9c60), its layout constants

Usage:
  venv/bin/python scripts/t61_snap_tables.py SNAP {rows,pages,sets,ccmaps,cache,all} [--out FILE]
"""
import argparse
import os
import pickle
import struct
import sys
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
IMAGE = os.path.join(HERE, '..', 'ele3_out', '02_type3.unpacked.bin')
LOAD = 0x40000400
DESC, ROWS, DROW = 0x401f83a4, 0x141, 0x3c
DISP, XROW = 0x4243425c, 0x44
PAGES, PAGE, NPAGES = 0x42433c00, 0x2c, 37
SETS = {'MIDI': (0x42c657ac, 101), 'trig': (0x42c65940, 26), 'FX': (0x42c659a8, 101),
        'sound': (0x42c65b3c, 101), 'filter6x3': (0x42c65cd0, 18), 'machine5x40': (0x42c65d18, 200)}
ARRAYS = {'MIDI NRPN': (0x42c66038, 399), 'MIDI CC': (0x42c66674, 128), 'sound NRPN': (0x42c66874, 399),
          'sound CC': (0x42c66eb0, 128), 'FX NRPN': (0x42c670b0, 399), 'FX CC': (0x42c676ec, 128)}
MAPS = {'filter NRPN': (0x42c678ec, 6), 'filter CC': (0x42c6797c, 6),
        'machine NRPN': (0x42c67a0c, 5), 'machine CC': (0x42c67a84, 5)}
CACHE_PTR = 0x44648988


class _U(pickle.Unpickler):
    def find_class(self, m, n):
        raise pickle.UnpicklingError('%s.%s' % (m, n))


class Mem:
    """The image (flash, 0x40000400..) and a snapshot's RAM pages, image first for its range."""

    def __init__(self, snap):
        self.img = open(IMAGE, 'rb').read()
        blob = _U(open(snap, 'rb')).load()
        self.pages = {k: zlib.decompress(v) for k, v in blob['pages'].items()}
        self.ps = len(next(iter(self.pages.values())))

    def read(self, a, n):
        if LOAD <= a and a + n <= LOAD + len(self.img) and a < 0x402fc000:
            return self.img[a - LOAD:a - LOAD + n]
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

    def s32(self, a):
        return struct.unpack('>i', self.read(a, 4))[0]

    def s16(self, a):
        return struct.unpack('>h', self.read(a, 2))[0]

    def cstr(self, a):
        if not LOAD <= a < LOAD + len(self.img):
            return '?'
        o = a - LOAD
        e = self.img.find(b'\0', o)
        t = self.img[o:e]
        return t.decode('latin-1') if len(t) < 64 else '?'


def row_names(m, r):
    a = DESC + DROW * r
    return m.cstr(m.u32(a + 0x28)), m.cstr(m.u32(a + 0x30))


def cmd_rows(m, out):
    out.append('row  grp  k    CC  CC2 NRPN  UID  mask     fmt         disp: flags  fn04(mgr/inv)          '
               'glyph(mgr/inv)         random(mgr/inv)        fn34(mgr/inv)          long / short')
    for r in range(ROWS):
        a = DESC + DROW * r
        g, k = m.s32(a), m.s32(a + 4)
        cc, cc2, nrpn, uid, mask, fmt = m.s16(a + 0x18), m.s16(a + 0x1a), m.s32(a + 0x1c), m.s32(a + 0x20), \
            m.u32(a + 0x24), m.u32(a + 0x34)
        d = DISP + XROW * r
        fns = []
        for off in (0x04, 0x14, 0x24, 0x34):
            mgr, inv = m.u32(d + off + 8), m.u32(d + off + 12)
            fns.append('%08x/%08x' % (mgr, inv) if mgr else '-' * 17)
        ln, sn = row_names(m, r)
        out.append('%3x %4s %3s %4s %4s %5s %4d  %08x %08x   %08x  %s  %s / %s' % (
            r, '%x' % g if g >= 0 else '-', k if k >= 0 else '-', cc if cc >= 0 else '-',
            cc2 if cc2 >= 0 else '-', nrpn if nrpn >= 0 else '-', uid, mask, fmt, m.u32(d),
            '  '.join(fns), ln, sn))


def page_rec(m, pid):
    a = PAGES + PAGE * pid

    def sstr(p):            # COW std::string: pointer to the characters
        q = m.u32(p)
        raw = m.read(q, 24)
        return raw.split(b'\0')[0].decode('latin-1') if q else ''
    rows = [m.u32(a + 8 + 4 * i) for i in range(8)]
    return sstr(a), sstr(a + 4), rows, m.u32(a + 0x28)


def cmd_pages(m, out):
    used = {}
    out.append('page short long                 A-H rows (0 = empty)                     LEVEL')
    for pid in range(NPAGES):
        s, l, rows, lev = page_rec(m, pid)
        out.append('%3d  %-5s %-20s %s  %x' % (pid, s, l, ' '.join('%3x' % r for r in rows), lev))
        for i, r in enumerate(rows + [lev]):
            if r:
                used.setdefault(r, []).append('%d%s' % (pid, 'ABCDEFGHL'[i]))
    out.append('')
    out.append('rows on no page record: %s' % ' '.join('%x' % r for r in range(ROWS) if r not in used))
    for r in (0x7a, 0x83):
        out.append('row 0x%x (%s) on pages: %s' % (r, row_names(m, r)[0], used.get(r, 'none')))
    return used


def cmd_sets(m, out):
    for name, (a, n) in SETS.items():
        vals = [m.s32(a + 4 * i) for i in range(n)]
        out.append('%s slot->row (0x%08x, %d): %s' % (name, a, n, ' '.join(
            '%d:%x' % (i, v) for i, v in enumerate(vals) if v)))
        if n == 101:
            out.append('  free slots: %s' % ' '.join(str(i) for i, v in enumerate(vals) if not v))
    a, _ = SETS['machine5x40']
    for mach in range(5):
        vals = [m.s32(a + 4 * (mach * 40 + i)) for i in range(40)]
        out.append('  machine %d: used k %s; free k %s' % (
            mach, ' '.join(str(25 + i) for i, v in enumerate(vals) if v),
            ' '.join(str(25 + i) for i, v in enumerate(vals) if not v)))
    out.append('built flag 0x42c657a8 = %d' % m.u32(0x42c657a8))


def rb_tree(m, hdr):
    """libstdc++ std::map<int,int> at hdr: +0 compare, +4 header node (+4 colour, +8 root, ..),
    +0x14 count. Nodes: +0 colour, +4 parent, +8 left, +0xc right, +0x10 key, +0x14 value."""
    root = m.u32(hdr + 8)
    out, stack, seen = [], [root], set()
    while stack:
        n = stack.pop()
        if not n or n in seen or n == hdr + 4:
            continue
        seen.add(n)
        out.append((m.s32(n + 0x10), m.s32(n + 0x14)))
        stack += [m.u32(n + 8), m.u32(n + 0xc)]
    return sorted(out), m.u32(hdr + 0x14)


def cmd_ccmaps(m, out):
    filed = set()
    for name, (a, n) in ARRAYS.items():
        vals = [(i, m.s32(a + 4 * i)) for i in range(n)]
        hit = [(i, v) for i, v in vals if v]
        filed.update(v for _, v in hit)
        out.append('%s -> row (0x%08x, %d entries, %d used): %s' % (name, a, n, len(hit), ' '.join(
            '%d:%x' % (i, v) for i, v in hit)))
        if 'CC' in name and 'NRPN' not in name:
            out.append('  free CCs: %s' % ' '.join(str(i) for i, v in vals if not v))
    for name, (a, cnt) in MAPS.items():
        for j in range(cnt):
            kv, size = rb_tree(m, a + 0x18 * j)
            filed.update(v for _, v in kv)
            out.append('%s[%d] (0x%08x, %d nodes): %s' % (name, j, a + 0x18 * j, size,
                                                          ' '.join('%d:%x' % p for p in kv)))
    out.append('')
    out.append('rows with a CC or NRPN in the descriptor that are in no CC/NRPN map:')
    for r in range(ROWS):
        a = DESC + DROW * r
        cc, cc2, nrpn = m.s16(a + 0x18), m.s16(a + 0x1a), m.s32(a + 0x1c)
        if (cc >= 0 or cc2 >= 0 or nrpn >= 0) and r not in filed:
            out.append('  %3x grp %s CC %d CC2 %d NRPN %d  %s / %s' % (
                r, '%x' % m.s32(a) if m.s32(a) >= 0 else '-', cc, cc2, nrpn, *row_names(m, r)))


def cmd_cache(m, out):
    p = m.u32(CACHE_PTR)
    out.append('ParameterUpdateCache pointer at 0x%08x = 0x%08x' % (CACHE_PTR, p))
    if p:
        out.append('  vtable 0x%08x, dirty byte %d, bank %d, pending vector %08x..%08x cap %08x' % (
            m.u32(p), m.read(p + 4, 1)[0], m.u32(p + 0xffe4), m.u32(p + 0xffe8), m.u32(p + 0xffec),
            m.u32(p + 0xfff0)))
        out.append('  size 0xfff4 = 0x18 + 2 banks x 17 lanes x 0x141 rows x 2 (u16 values, 0x5544) '
                   '+ 2 x 17 x 0x504 (dirty bits, 0xaa88) + bank + vector')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('snap')
    ap.add_argument('what', choices=['rows', 'pages', 'sets', 'ccmaps', 'cache', 'all'])
    ap.add_argument('--out')
    a = ap.parse_args()
    m = Mem(a.snap)
    out = ['# from %s' % os.path.relpath(a.snap)]
    for w in (['rows', 'pages', 'sets', 'ccmaps', 'cache'] if a.what == 'all' else [a.what]):
        out.append('## %s' % w)
        globals()['cmd_' + w](m, out)
        out.append('')
    txt = '\n'.join(out) + '\n'
    if a.out:
        with open(a.out, 'w') as f:
            f.write(txt)
    sys.stdout.write(txt)


if __name__ == '__main__':
    main()
