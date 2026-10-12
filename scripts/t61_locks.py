#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""T6.1: list the p-lock records that a run created, by comparing its end snapshot with the
snapshot it started from.

A p-lock lane (DN2 OS 1.12) is 0x102 bytes: byte k (run time) or stored id (working image),
byte track (0..15), then 128 big-endian u16 step values, 0xffff = no lock. The live pattern keeps
k; the working project image keeps the stored id (k mapped through 0x401fd330). This scans every
DDR page for records of that shape and prints the ones that differ from the start snapshot.

    venv/bin/python scripts/t61_locks.py out/T6.1/emu/grown-plock/end.snap \\
        [--base out/T2.4a/warm/warm800M.snap]
"""
import argparse
import os
import struct
import sys
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from t61_snap_tables import _U                     # noqa: E402

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')


def lanes(path):
    blob = _U(open(path, 'rb')).load()
    out = {}
    for base, z in blob['pages'].items():
        if not 0x40000000 <= base < 0x48000000:
            continue
        p = zlib.decompress(z)
        for st in range(0, len(p) - 0x102, 2):
            k, track = p[st], p[st + 1]
            if k > 106 or track > 15:
                continue
            vals = struct.unpack('>128H', p[st + 2:st + 0x102])
            if vals.count(0xffff) >= 120 and any(v != 0xffff for v in vals):
                out[base + st] = (k, track, tuple((i, v) for i, v in enumerate(vals) if v != 0xffff))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('snap')
    ap.add_argument('--base', default=os.path.join(ROOT, 'out', 'T2.4a', 'warm', 'warm800M.snap'))
    a = ap.parse_args()
    b, e = lanes(a.base), lanes(a.snap)
    new = {addr: x for addr, x in e.items() if b.get(addr) != x}
    print('%s: %d lock-like records differ from %s' % (a.snap, len(new), os.path.basename(a.base)))
    for addr, (k, track, steps) in sorted(new.items()):
        print('  0x%08x  k/id %3d  track %2d  steps %s' % (addr, k, track, ' '.join(
            '%d:%#06x' % s for s in steps)))
    print('(records near 0x412xxxxx are the live pattern: k; near 0x405dxxxx the working image: '
          'stored id; neighbours of a changed record can show up as noise)')


if __name__ == '__main__':
    main()
