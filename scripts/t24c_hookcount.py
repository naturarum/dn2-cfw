#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""T2.4c: count how often guest addresses are reached during a headless emu.live --script run.

Run from vendor/digikit:
  DT2_SECTIONS=out/sections/dn2-1.12 uv run python ../../scripts/t24c_hookcount.py ADDRS SNAP SYX SCRIPT OUTDIR [live args]
ADDRS is a comma-separated list of hex addresses. Never touches hardware.
"""
import collections, os, sys
sys.path.insert(0, os.getcwd())
import emu.live as live

addrs = [int(a, 16) for a in sys.argv[1].split(',')]
counts = collections.Counter()
orig_build = live.build

def build(*a, **k):
    m, ev, st, pc, inq, at = orig_build(*a, **k)
    for addr in addrs:
        at(addr, lambda uc, x, s, d, addr=addr: counts.__setitem__(addr, counts[addr] + 1))
    return m, ev, st, pc, inq, at

live.build = build
snap, syx, script, out, *rest = sys.argv[2:]
rc = live.main([snap, '--syx', syx, '--script', script, '--out', out] + rest)
for addr in addrs:
    print('[count] %#010x  %d' % (addr, counts[addr]))
sys.exit(rc)
