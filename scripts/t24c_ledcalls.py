#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""T2.4c: log every LED-manager call FUN_40132428(code, value) (1.12) with its caller, during a --script.

Run from vendor/digikit:
  DT2_SECTIONS=out/sections/dn2-1.12 uv run python ../../scripts/t24c_ledcalls.py SNAP SYX SCRIPT OUTDIR [live args]
Never touches hardware.
"""
import os, struct, sys
sys.path.insert(0, os.getcwd())
from unicorn.m68k_const import UC_M68K_REG_A7
import emu.live as live

orig_build = live.build
state = {}

def build(*a, **k):
    m, ev, st, pc, inq, at = orig_build(*a, **k)
    def hook(uc, addr, size, data):
        sp = uc.reg_read(UC_M68K_REG_A7)
        ret, code, value = struct.unpack('>III', bytes(uc.mem_read(sp, 12)))
        emu = state.get('emu')
        t = emu.stats['emu_s'] if emu else 0
        print('[led] t=%.3f FUN_40132428(code %d, value %d) from %#010x' % (t, code, value, ret), flush=True)
    at(0x40132428, hook)
    return m, ev, st, pc, inq, at

live.build = build
orig_init = live.LiveEmulator.__init__
def init(self, *a, **k):
    orig_init(self, *a, **k)
    state['emu'] = self
live.LiveEmulator.__init__ = init
snap, syx, script, out, *rest = sys.argv[1:]
sys.exit(live.main([snap, '--syx', syx, '--script', script, '--out', out] + rest))
