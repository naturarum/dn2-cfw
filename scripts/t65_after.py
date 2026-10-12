#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""T6.5: compare what two runs of the same key script left behind.

    venv/bin/python scripts/t65_after.py state A/state.json B/state.json
    venv/bin/python scripts/t65_after.py flush A/flush.json B/flush.json
    venv/bin/python scripts/t65_after.py stored A.snap B.snap [--base BASE.snap]
    venv/bin/python scripts/t65_after.py kitfx SNAP [--kit N]

state   (from scripts/t65_live.py --state) the live FX setup of the current kit (k 0..100), the raw
        parameter mirror (17 rows of 102 words from 0x80003af2: 16 voices' sounds and row 16 = the
        FX setup), the TX frame buffer (0xa80 bytes; emu.live runs without the audio clock, so the
        frame handler does not rebuild it: use scripts/t65_frame.py for the frame) and the working
        image's kit FX records: every word that differs, named.
flush   (--flush-trace) the ParameterUpdateCache's sets (lane, row, value) and the batches its
        flush handed on, compared as multisets (two builds deliver the same MIDI bytes at slightly
        different instruction counts, so batching can differ; content must not).
stored  the working project image (0x405ce96c, 0xc4b010 bytes: the state the unit keeps across a
        power-off, and what a project save writes) byte for byte; differing ranges are named by
        kit record (+0xae0200 + 0x2a00 x kit) and field where known. With --base, only bytes that
        changed in either run relative to BASE are compared (what the run itself wrote).
kitfx   decode one kit's stored FX record (fxSetupStorage_v0_t: +0 version, +4 + 2 x id) with the
        id -> k map of the image (0x401fd278), and the reserved words +0x60..+0x97.
"""
import argparse
import collections
import json
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from t65_tables import Mem                       # noqa: E402

WORK, WORKN = 0x405ce96c, 0xc4b010
KITS, KSTRIDE, KITN, FXREC = 0xae0200, 0x2a00, 129, 0x16ac
ID2K = 0x401fd278                                  # stored FX id -> k (46 longs)
FRAME_K = {}                                       # frame byte offset -> k, from T3.3 (k 25..69)


def words(hexs, n=2):
    b = bytes.fromhex(hexs)
    return [int.from_bytes(b[i:i + n], 'big') for i in range(0, len(b), n)]


def cmd_state(a, b):
    A, B = json.load(open(a)), json.load(open(b))
    out = []
    for k, (x, y) in enumerate(zip(A['fx_live'], B['fx_live'])):
        if x != y:
            out.append('live FX k %d: %d vs %d' % (k, x, y))
    ra, rb = words(A['raw_mirror']), words(B['raw_mirror'])
    for i, (x, y) in enumerate(zip(ra, rb)):
        if x != y:                       # rows of 102 words from 0x80003af2 (row 16 = 0x800047b2 + 2k)
            out.append('raw mirror row %d k %d: 0x%04x vs 0x%04x' % ((i - 1) // 102, (i - 1) % 102, x, y))
    fa, fb = words(A['frame']), words(B['frame'])
    for i, (x, y) in enumerate(zip(fa, fb)):
        if x != y:
            out.append('frame word +0x%03x: 0x%04x vs 0x%04x' % (2 * i, x, y))
    for kit, (x, y) in enumerate(zip(A['kit_fx_records'], B['kit_fx_records'])):
        if x != y:
            wa, wb = words(x), words(y)
            d = ['+0x%02x 0x%04x/0x%04x' % (2 * i, p, q) for i, (p, q) in enumerate(zip(wa, wb)) if p != q]
            out.append('kit %d FX record: %s' % (kit, ' '.join(d)))
    if A['fx_routing'] != B['fx_routing']:
        out.append('routing long %s vs %s' % (A['fx_routing'], B['fx_routing']))
    print('\n'.join(out) or 'state identical')
    return out


def cmd_flush(a, b, ignore_rows=()):
    """The flush hands on the latest value of each (lane, row) set since the last flush, so two
    builds whose flushes fall at slightly different moments coalesce different intermediate values.
    What must hold: the sets are the same multiset in both; in each run every flushed item is a
    value that was set (nothing bogus, the T6.1 overflow showed up as flushed rows nobody set); and
    the last value flushed for each (lane, row) is the last value set for it."""
    A, B = json.load(open(a)), json.load(open(b))
    out = []
    ok = True
    if ignore_rows:
        for R in (A, B):
            extra = [x for x in R['sets'] if x[1] in ignore_rows]
            out.append('%s sets of the rows set aside (%s): %d %s' % (
                'A' if R is A else 'B', ' '.join('0x%x' % r for r in sorted(ignore_rows)), len(extra),
                ' '.join('lane %d row 0x%x v 0x%x' % tuple(x) for x in extra[:12])))
            R['sets'] = [x for x in R['sets'] if x[1] not in ignore_rows]
            R['flushes'] = [(l, [(r, v) for r, v in items if r not in ignore_rows]) for l, items in R['flushes']]
    sa = collections.Counter(tuple(x) for x in A['sets'])
    sb = collections.Counter(tuple(x) for x in B['sets'])
    out.append('sets: A %d, B %d; same multiset: %s' % (sum(sa.values()), sum(sb.values()), sa == sb))
    ok &= sa == sb
    for nm, R in (('A', A), ('B', B)):
        sets = collections.Counter(tuple(x) for x in R['sets'])
        flushed = [(l, r, v) for l, items in R['flushes'] for r, v in items]
        bogus = [f for f in flushed if f not in sets]
        last_set, last_fl = {}, {}
        for l, r, v in R['sets']:
            last_set[(l, r)] = v
        for l, r, v in flushed:
            last_fl[(l, r)] = v
        stale = {k: (last_set.get(k), v) for k, v in last_fl.items() if last_set.get(k) != v}
        never = [k for k in last_set if k not in last_fl]
        out.append('%s: %d flushed items in %d batches; not set by MIDI (bogus): %d %s; last flushed != last set: '
                   '%d %s; set but never flushed: %d %s' % (
                       nm, len(flushed), len(R['flushes']), len(bogus), bogus[:8], len(stale),
                       list(stale.items())[:8], len(never), never[:8]))
        ok &= not bogus and not stale and not never
    fa = {(l, r): v for l, items in A['flushes'] for r, v in items}
    fb = {(l, r): v for l, items in B['flushes'] for r, v in items}
    out.append('final flushed value per (lane, row) the same in A and B: %s' % (fa == fb))
    ok &= fa == fb
    rows_b = sorted({r for _, r, _ in B['sets']})
    out.append('rows set in B: %d, highest 0x%x' % (len(rows_b), max(rows_b) if rows_b else 0))
    out.append('verdict: %s' % ('SAME' if ok else 'DIFFERENT'))
    print('\n'.join(out))
    return out


def ranges(da, db, base=None):
    out, i, n = [], 0, len(da)
    while i < n:
        same = da[i] == db[i] or (base is not None and da[i] == base[i] and db[i] == base[i])
        if same:
            i += 1
            continue
        j = i
        while j < n and not (da[j] == db[j] or (base is not None and da[j] == base[j] and db[j] == base[j])):
            j += 1
        out.append((i, j - i))
        i = j
    return out


def name_of(off):
    if KITS <= off < KITS + KSTRIDE * KITN:
        k, r = divmod(off - KITS, KSTRIDE)
        if FXREC <= r < FXREC + 0xa0:
            return 'kit %d FX record +0x%x' % (k, r - FXREC)
        if 0x3c <= r < 0x3c + 16 * 0x167:
            return 'kit %d sound %d +0x%x' % (k, (r - 0x3c) // 0x167, (r - 0x3c) % 0x167)
        return 'kit %d +0x%x' % (k, r)
    return 'image +0x%x' % off


def cmd_stored(a, b, base):
    ma, mb = Mem(a), Mem(b)
    da, db = ma.read(WORK, WORKN), mb.read(WORK, WORKN)
    dbase = Mem(base).read(WORK, WORKN) if base else None
    rs = ranges(da, db, dbase)
    out = ['working image 0x%08x +0x%x: %d differing ranges%s' % (WORK, WORKN, len(rs),
                                                                  ' (bytes either run wrote)' if base else '')]
    for off, n in rs[:200]:
        out.append('  +0x%07x %5d  %-34s A %s | B %s' % (off, n, name_of(off), da[off:off + min(n, 16)].hex(),
                                                          db[off:off + min(n, 16)].hex()))
    print('\n'.join(out))
    return out


def cmd_kitfx(snap, kit):
    m = Mem(snap)
    rec = m.read(WORK + KITS + KSTRIDE * kit + FXREC, 0xa0)
    ver = struct.unpack('>I', rec[:4])[0]
    id2k = [m.s32(ID2K + 4 * i) for i in range(46)]
    vals = struct.unpack('>46H', rec[4:4 + 92])
    print('kit %d FX record at 0x%08x: version %d' % (kit, WORK + KITS + KSTRIDE * kit + FXREC, ver))
    print('  ' + ' '.join('id%d(k%d)=%d' % (i, id2k[i], v >> 8) for i, v in enumerate(vals)))
    print('  reserved +0x60..+0x97: %s' % rec[0x60:0x98].hex())
    print('  routing +0x98: %s  +0x9c: %s' % (rec[0x98:0x9c].hex(), rec[0x9c:0xa0].hex()))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('cmd', choices=['state', 'flush', 'stored', 'kitfx'])
    ap.add_argument('a')
    ap.add_argument('b', nargs='?')
    ap.add_argument('--base')
    ap.add_argument('--kit', type=int, default=0)
    ap.add_argument('--ignore-rows', default='', help='flush: rows (0x17f,0x17e) set aside, listed')
    a = ap.parse_args()
    if a.cmd == 'state':
        cmd_state(a.a, a.b)
    elif a.cmd == 'flush':
        cmd_flush(a.a, a.b, {int(x, 0) for x in a.ignore_rows.split(',') if x})
    elif a.cmd == 'stored':
        cmd_stored(a.a, a.b, a.base)
    else:
        cmd_kitfx(a.a, a.kit)


if __name__ == '__main__':
    main()
