#!/usr/bin/env python3
"""T1.3: every static reference from the Digitone II 1.12 main OS into an
address range, three ways, so a "nothing names it" claim has evidence.

  1. objdump: a linear sweep of the whole image with
     `m68k-elf-objdump -m m68k:cfv4e` (out/T1.3/main.dis). Every operand that
     is an absolute value: `#imm` (printed in decimal), `0x...` addresses and
     resolved pc-relative targets. Lines that objdump prints as `.short`/
     `.long` (undecodable) are counted, not scanned.
  2. Ghidra: out/decomp/main_os/xrefs.sqlite, table data_refs (its own
     disassembly, which follows flow, so it skips data).
  3. raw words: every aligned and unaligned big-endian 32-bit word of the
     image whose value falls in the range (pointers in data tables).

    python3 scripts/t1.3_refs.py LO HI [--alias] [--map 0x1000] [--json OUT]

--alias also scans LO+0x08000000..HI+0x08000000 (the MCF5441x DDR uncached
alias the OS uses for DSP buffers). Static only: a computed address (base +
index) is invisible to all three, which is why T1.3 adds write watches.
"""
import argparse
import bisect
import collections
import json
import os
import re
import sqlite3
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMG = os.path.join(ROOT, 'ele3_out', '02_type3.unpacked.bin')
BASE = 0x40000400
DIS = os.path.join(ROOT, 'out', 'T1.3', 'main.dis')
DB = os.path.join(ROOT, 'out', 'decomp', 'main_os', 'xrefs.sqlite')

LINE = re.compile(r'^([0-9a-f]{8}):\t([0-9a-f ]+)\t(.*)$')
HEXNUM = re.compile(r'0x([0-9a-f]+)')
IMM = re.compile(r'#(-?\d+)')


def disassembly():
    if not os.path.exists(DIS):
        os.makedirs(os.path.dirname(DIS), exist_ok=True)
        with open(DIS, 'w') as fh:
            subprocess.run(['m68k-elf-objdump', '-D', '-b', 'binary', '-m', 'm68k:cfv4e',
                            '--adjust-vma=0x%x' % BASE, IMG], stdout=fh, check=True)
    return DIS


def objdump_refs(ranges):
    hits, undecoded, lines = [], 0, 0
    with open(disassembly()) as fh:
        for ln in fh:
            m = LINE.match(ln.rstrip('\n'))
            if not m:
                continue
            lines += 1
            addr, text = int(m.group(1), 16), m.group(3)
            if text.startswith(('.short', '.long', '.byte')):
                undecoded += 1
                continue
            vals = [int(h, 16) for h in HEXNUM.findall(text)]
            vals += [int(d) & 0xFFFFFFFF for d in IMM.findall(text)]
            for v in vals:
                for lo, hi in ranges:
                    if lo <= v < hi:
                        hits.append((addr, v, text.strip()))
    return hits, undecoded, lines


def ghidra_refs(ranges):
    if not os.path.exists(DB):
        return None
    db = sqlite3.connect(DB)
    out = []
    for lo, hi in ranges:
        out += db.execute('select site, func, target, kind from data_refs '
                          'where target >= ? and target < ? order by target', (lo, hi)).fetchall()
    return out


def raw_words(img, ranges):
    out = []
    for i in range(len(img) - 3):
        v = int.from_bytes(img[i:i + 4], 'big')
        for lo, hi in ranges:
            if lo <= v < hi:
                out.append((BASE + i, v))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('lo', type=lambda s: int(s, 0))
    ap.add_argument('hi', type=lambda s: int(s, 0))
    ap.add_argument('--alias', action='store_true')
    ap.add_argument('--map', type=lambda s: int(s, 0), default=0)
    ap.add_argument('--json')
    ap.add_argument('--show', type=int, default=40)
    a = ap.parse_args(argv)
    ranges = [(a.lo, a.hi)]
    if a.alias:
        ranges.append((a.lo + 0x08000000, a.hi + 0x08000000))
    img = open(IMG, 'rb').read()
    od, undecoded, lines = objdump_refs(ranges)
    gh = ghidra_refs(ranges)
    rw = raw_words(img, ranges)
    res = {'ranges': ['0x%08x-0x%08x' % r for r in ranges],
           'objdump': {'lines': lines, 'undecoded_lines': undecoded, 'hits': len(od),
                       'items': [('0x%08x' % s, '0x%08x' % v, t) for s, v, t in od]},
           'ghidra': None if gh is None else {
               'hits': len(gh),
               'items': [('0x%08x' % s, '0x%08x' % (f or 0), '0x%08x' % t, k) for s, f, t, k in gh]},
           'raw_words': {'hits': len(rw), 'items': [('0x%08x' % s, '0x%08x' % v) for s, v in rw]}}
    for r in res['ranges']:
        print('range', r)
    print('objdump: %d hits (%d lines, %d undecoded)' % (len(od), lines, undecoded))
    for s, v, t in od[:a.show]:
        print('  0x%08x -> 0x%08x  %s' % (s, v, t))
    if gh is not None:
        print('ghidra data_refs: %d hits' % len(gh))
        for s, f, t, k in gh[:a.show]:
            print('  0x%08x (fn 0x%08x) -> 0x%08x %s' % (s, f or 0, t, k))
    print('raw 32-bit words: %d hits' % len(rw))
    for s, v in rw[:a.show]:
        print('  0x%08x holds 0x%08x' % (s, v))
    if a.map:
        cnt = collections.Counter()
        for _s, v, _t in od:
            cnt[v - v % a.map] += 1
        for _s, _f, t, _k in (gh or []):
            cnt[t - t % a.map] += 1
        print('per 0x%x (objdump + ghidra):' % a.map)
        for k in sorted(cnt):
            print('  0x%08x  %d' % (k, cnt[k]))
    if a.json:
        with open(a.json, 'w') as fh:
            json.dump(res, fh, indent=1)


if __name__ == '__main__':
    main()
