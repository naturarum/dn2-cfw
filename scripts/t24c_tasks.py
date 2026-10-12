#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""T2.4c: where each RTOS task is while emu.live runs a --script (emu/taskprof.py).

Run from vendor/digikit:
  DT2_SECTIONS=out/sections/dn2-1.12 uv run python ../../scripts/t24c_tasks.py SNAP SYX SCRIPT OUTDIR [live args]
Prints emu/taskprof.py's summary at the end. Never touches hardware.
"""
import os, sys
sys.path.insert(0, os.getcwd())
import emu.live as live
from emu import taskprof, symbols, config

orig_build = live.build
state = {}

def build(*a, **k):
    m, ev, st, pc, inq, at = orig_build(*a, **k)
    profile = symbols.resolve(open(config.main_image(), 'rb').read())
    def clock():
        emu = state.get('emu')
        return int(emu.emu_time() * 1e8) if emu is not None and hasattr(emu, '_stepper') else 0
    state['tp'] = taskprof.TaskProfile(m, at, profile, clock, ev['tasks'], ev.get('idle_spins'))
    return m, ev, st, pc, inq, at

live.build = build
orig_init = live.LiveEmulator.__init__
def init(self, *a, **k):
    orig_init(self, *a, **k)
    state['emu'] = self
live.LiveEmulator.__init__ = init
snap, syx, script, out, *rest = sys.argv[1:]
rc = live.main([snap, '--syx', syx, '--script', script, '--out', out] + rest)
emu = state['emu']
for line in state['tp'].summary(int(emu.emu_time() * 1e8)):
    print(line)
sys.exit(rc)
