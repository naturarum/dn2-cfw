#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""T5.0b: run emu.live (through scripts/t19_live.py) and log which descriptor-table fields the OS reads.

    cd vendor/digikit && DT2_SECTIONS=... uv run python ../../scripts/t50b_readwatch.py SNAP --syx SYX \\
        --speed 1 --script S --out DIR [--identify-as STOCK.syx]  > DIR/readwatch.log

A read hook on the parameter descriptor table (0x401f83a4, 0x141 rows of 0x3c bytes) and on the
modulation destination lists' group-name table (0x401f7b04, 26 entries) prints the first read of
each (row, field) as it happens, between the script's own "[script] png" lines, and a count of every
(row, field) at the end. It answers: which rows' names does each screen read, and does anything
read a row's group pointer (+0x2c)? Evidence only; the screens of this run are not the ones compared
(the hook slows the emulator).
"""
import collections
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.getcwd())                      # vendor/digikit
import t19_live                                      # noqa: E402
from emu import live                                 # noqa: E402

DESC, ROW, ROWS = 0x401f83a4, 0x3c, 0x141
GROUPS, NGROUPS = 0x401f7b04, 26
seen = set()
count = collections.Counter()


def hook(uc, access, addr, size, value, user):
    if DESC <= addr < DESC + ROW * ROWS:
        r, f = divmod(addr - DESC, ROW)
        key = ('row', r, f & ~3)
    elif GROUPS <= addr < GROUPS + 4 * NGROUPS:
        key = ('group', (addr - GROUPS) // 4 + 5, 0)
    else:
        return
    count[key] += 1
    if key not in seen:
        seen.add(key)
        print('[read] %s 0x%x +0x%x' % key, flush=True)


class Watched(t19_live.Patched):
    def _setup(self):
        r = super()._setup()
        from unicorn import UC_HOOK_MEM_READ
        m = r[0]
        m.uc.hook_add(UC_HOOK_MEM_READ, hook, begin=GROUPS, end=DESC + ROW * ROWS - 1)
        return r


if __name__ == '__main__':
    t19_live.Patched = Watched
    orig = live.summary

    def summary(emu):
        print('[read] totals:')
        for k, n in sorted(count.items()):
            print('[read]   %s 0x%x +0x%x: %d' % (k + (n,)))
        return orig(emu)

    live.summary = summary
    sys.exit(t19_live.main())
