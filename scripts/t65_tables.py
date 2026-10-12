#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""T6.5: dump everything built from the parameter table, from a digikit snapshot, and compare two.

    venv/bin/python scripts/t65_tables.py dump SNAP OUT.json
    venv/bin/python scripts/t65_tables.py compare A.json B.json [--expect-new 0x141-0x151,0x17f]

Everything is read from the snapshot's memory (the image as loaded included), so a modded build's
names and a relocated table are seen as the OS sees them. The table's address and row count are
read from the code itself: the operand of `lea table,a0` at 0x400174de and the bound at 0x400174d6
(two of the sites param-grow changes).

  rows     every row: group, k, range, default, bipolar, CC, CC2, NRPN, Overbridge id, modulation
           mask, the strings (long, group, short, suffix), formatter; and its display entry as the
           accessor 0x400c257c would return it (stock rows: 0x4243425c + 0x44 x row; new rows:
           param-grow's own entry when it has one, else row 0's)
  pages    the 37 page records (0x42433c00 + 0x2c x id): names, rows A-H, LEVEL
  sets     slot -> row tables built at boot (MIDI, trig, FX, sound, filter 6x3, machine 5x40)
  ccmaps   CC -> row and NRPN -> row arrays (MIDI, sound, FX) and the per-filter / per-machine maps
  cache    the ParameterUpdateCache: pointer, vtable, and its bank word for the build's row count

compare: rows below 0x141 must match field by field (strings by value), display entries byte for
byte; rows from 0x141 are listed (each one a copy of row 0, or one of --expect-new); pages, sets
and maps must match except for entries naming an expected new row.
"""
import argparse
import json
import pickle
import struct
import sys
import zlib

DESC0, ROWS0, ROW = 0x401f83a4, 0x141, 0x3c
DISP, XROW = 0x4243425c, 0x44
PG_XDISP, PG_XDISP_ON = 0x47d05a00, 0x47d06ac0     # param-grow's fixed region
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
    def __init__(self, snap):
        blob = _U(open(snap, 'rb')).load()
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

    def s32(self, a):
        return struct.unpack('>i', self.read(a, 4))[0]

    def s16(self, a):
        return struct.unpack('>h', self.read(a, 2))[0]

    def cstr(self, a):
        if not a or (a - a % self.ps) not in self.pages:
            return None
        return self.read(a, 80).split(b'\0')[0].decode('latin-1')


def table_of(m):
    lea = m.read(0x400174de, 6)
    cmp_ = m.read(0x400174d6, 6)
    assert lea[:2] == bytes.fromhex('41f9') and cmp_[:2] == bytes.fromhex('0c82'), (lea.hex(), cmp_.hex())
    return struct.unpack('>I', lea[2:])[0], struct.unpack('>I', cmp_[2:])[0]


def disp_of(m, r, grown):
    if r < ROWS0:
        return DISP + XROW * r
    if grown and m.read(PG_XDISP_ON + r - ROWS0, 1)[0]:
        return PG_XDISP + XROW * (r - ROWS0)
    return DISP


def rb_tree(m, hdr):
    root = m.u32(hdr + 8)
    out, stack, seen = [], [root], set()
    while stack:
        n = stack.pop()
        if not n or n in seen or n == hdr + 4:
            continue
        seen.add(n)
        out.append((m.s32(n + 0x10), m.s32(n + 0x14)))
        stack += [m.u32(n + 8), m.u32(n + 0xc)]
    return sorted(out)


def dump(snap):
    m = Mem(snap)
    desc, nrows = table_of(m)
    grown = desc != DESC0
    d = {'snap': snap, 'desc': '0x%08x' % desc, 'rows_n': nrows, 'rows': [], 'pages': [], 'sets': {},
         'ccmaps': {}, 'trees': {}}
    for r in range(nrows):
        a = desc + ROW * r
        g, k, mn, mx, df, bip = struct.unpack('>iiiiii', m.read(a, 0x18))
        cc, cc2 = m.s16(a + 0x18), m.s16(a + 0x1a)
        nrpn, uid, mask = m.s32(a + 0x1c), m.s32(a + 0x20), m.u32(a + 0x24)
        da = disp_of(m, r, grown)
        d['rows'].append({
            'row': r, 'group': g, 'k': k, 'min': mn, 'max': mx, 'default': df, 'bipolar': bip, 'cc': cc,
            'cc2': cc2, 'nrpn': nrpn, 'uid': uid, 'modmask': mask,
            'name': m.cstr(m.u32(a + 0x28)), 'group_name': m.cstr(m.u32(a + 0x2c)),
            'short': m.cstr(m.u32(a + 0x30)), 'fmt': '0x%08x' % m.u32(a + 0x34),
            'suffix': m.cstr(m.u32(a + 0x38)),
            'disp_at': '0x%08x' % da, 'disp': m.read(da, XROW).hex(),
            'raw': m.read(a, ROW).hex()})
    for pid in range(NPAGES):
        a = PAGES + PAGE * pid

        def sstr(p):
            q = m.u32(p)
            return m.read(q, 24).split(b'\0')[0].decode('latin-1') if q else ''
        d['pages'].append({'id': pid, 'short': sstr(a), 'long': sstr(a + 4),
                           'rows': [m.u32(a + 8 + 4 * i) for i in range(8)], 'level': m.u32(a + 0x28)})
    for name, (a, n) in SETS.items():
        d['sets'][name] = [m.s32(a + 4 * i) for i in range(n)]
    d['built_flag'] = m.u32(0x42c657a8)
    for name, (a, n) in ARRAYS.items():
        d['ccmaps'][name] = [m.s32(a + 4 * i) for i in range(n)]
    for name, (a, cnt) in MAPS.items():
        d['trees'][name] = [rb_tree(m, a + 0x18 * j) for j in range(cnt)]
    p = m.u32(CACHE_PTR)
    bank = 0x18 + 204 * nrows
    d['cache'] = {'ptr': '0x%08x' % p, 'vtable': '0x%08x' % (m.u32(p) if p else 0),
                  'bank': m.u32(p + bank) if p else None,
                  'vector': ['0x%08x' % m.u32(p + bank + 4 + 4 * i) for i in range(3)] if p else None,
                  'size': '0x%x' % (bank + 0x10)}
    return d


def norm_row(r):
    return {k: v for k, v in r.items() if k not in ('raw', 'disp_at', 'row')}


def compare(a, b, expect_new):
    out, bad = [], []
    if a['rows_n'] != ROWS0:
        bad.append('A has %d rows, not the stock 0x141' % a['rows_n'])
    na = min(a['rows_n'], ROWS0)
    for r in range(na):
        x, y = norm_row(a['rows'][r]), norm_row(b['rows'][r])
        if x != y:
            diff = {k: (x[k], y[k]) for k in x if x[k] != y[k]}
            bad.append('row 0x%x differs: %s' % (r, diff))
    row0 = norm_row(b['rows'][0])
    clones, new = 0, []
    for r in range(ROWS0, b['rows_n']):
        y = b['rows'][r]
        if r in expect_new:
            new.append(r)
            out.append('B row 0x%x: %s/%s group 0x%x k %d %d..%d def %d CC %d NRPN %d uid 0x%x disp %s flags 0x%s'
                       % (r, y['name'], y['short'], y['group'], y['k'], y['min'] >> 8, y['max'] >> 8,
                          y['default'] >> 8, y['cc'], y['nrpn'], y['uid'], y['disp_at'], y['disp'][:8]))
        elif norm_row(y) == row0:
            clones += 1
        else:
            bad.append('B row 0x%x is neither a copy of row 0 nor an expected new row: %s' % (r, norm_row(y)))
    out.append('B rows 0x141..0x%x: %d copies of row 0, %d new (%s)' % (
        b['rows_n'] - 1, clones, len(new), ' '.join('0x%x' % r for r in new)))
    missing = [r for r in expect_new if r >= b['rows_n'] or r not in new]
    if missing:
        bad.append('expected new rows missing: %s' % missing)
    for pa, pb in zip(a['pages'], b['pages']):
        if pa != pb:
            extra = [(i, v) for i, (u, v) in enumerate(zip(pa['rows'], pb['rows'])) if u != v]
            if pa['short'] == pb['short'] and pa['long'] == pb['long'] and \
                    all(u == 0 and v in expect_new for u, v in zip(pa['rows'], pb['rows']) if u != v):
                out.append('page %d %s: new row(s) placed: %s' % (pa['id'], pa['long'], ', '.join(
                    '%s=0x%x' % ('ABCDEFGH'[i], v) for i, v in extra)))
            else:
                bad.append('page %d differs: %s / %s' % (pa['id'], pa, pb))
    for kind in ('sets', 'ccmaps'):
        for name in a[kind]:
            for i, (u, v) in enumerate(zip(a[kind][name], b[kind][name])):
                if u != v:
                    if u == 0 and v in expect_new:
                        out.append('%s %s[%d]: new row 0x%x' % (kind, name, i, v))
                    elif u != 0 and v in expect_new:
                        out.append('%s %s[%d]: stock row 0x%x replaced by new row 0x%x' % (kind, name, i, u, v))
                    else:
                        bad.append('%s %s[%d]: 0x%x vs 0x%x' % (kind, name, i, u, v))
    for name in a['trees']:
        if a['trees'][name] != b['trees'][name]:
            bad.append('tree %s differs' % name)
    if a['built_flag'] != b['built_flag']:
        bad.append('built flag %d vs %d' % (a['built_flag'], b['built_flag']))
    out.append('cache: A %s (size %s) bank %s; B %s (size %s) bank %s' % (
        a['cache']['ptr'], a['cache']['size'], a['cache']['bank'], b['cache']['ptr'], b['cache']['size'],
        b['cache']['bank']))
    return bad, out


def parse_rows(spec):
    s = set()
    for part in filter(None, (spec or '').split(',')):
        lo, _, hi = part.partition('-')
        s.update(range(int(lo, 0), int(hi or lo, 0) + 1))
    return s


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('dump')
    p.add_argument('snap')
    p.add_argument('out')
    p = sub.add_parser('compare')
    p.add_argument('a')
    p.add_argument('b')
    p.add_argument('--expect-new', default='')
    p.add_argument('--out')
    a = ap.parse_args()
    if a.cmd == 'dump':
        d = dump(a.snap)
        json.dump(d, open(a.out, 'w'), indent=0)
        print('%s: table %s, %d rows; cache %s' % (a.out, d['desc'], d['rows_n'], d['cache']['ptr']))
        return 0
    A, B = json.load(open(a.a)), json.load(open(a.b))
    bad, out = compare(A, B, parse_rows(a.expect_new))
    txt = '\n'.join(['A %s (%s, %d rows)' % (a.a, A['desc'], A['rows_n']),
                     'B %s (%s, %d rows)' % (a.b, B['desc'], B['rows_n'])] + out +
                    ['PROBLEM: ' + x for x in bad] + ['verdict: %s' % ('SAME (as expected)' if not bad else 'DIFFERENT')])
    print(txt)
    if a.out:
        open(a.out, 'w').write(txt + '\n')
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
