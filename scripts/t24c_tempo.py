#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""T2.4c: tempo check from the sequencer position (0x42c4f900, +900,000 per sub-step, 5.4M per step).

Run from vendor/digikit:
  DT2_SECTIONS=out/sections/dn2-1.12 uv run python ../../scripts/t24c_tempo.py SNAP SYX SCRIPT OUTDIR [live args]
Hooks the step handler 0x400d99de (1.12), keeps (audio block, position) per call, and prints the rate
between the first and the last advance, in audio time (1,500 blocks a second). Never touches hardware.
"""
import os, struct, sys
sys.path.insert(0, os.getcwd())
import emu.live as live

orig_build = live.build
rows = []

def build(*a, **k):
    m, ev, st, pc, inq, at = orig_build(*a, **k)
    def step(uc, addr, size, data):
        rows.append((ev['ssi0_dma'].major_loops[50],
                     struct.unpack('>I', bytes(uc.mem_read(0x42c4f900, 4)))[0]))
    at(0x400d99de, step)
    return m, ev, st, pc, inq, at

live.build = build
snap, syx, script, out, *rest = sys.argv[1:]
rc = live.main([snap, '--syx', syx, '--script', script, '--out', out] + rest)
inc = [i for i in range(1, len(rows)) if rows[i][1] - rows[i - 1][1] == 900000]
(b0, p0), (b1, p1) = rows[inc[0]], rows[inc[-1]]
steps, secs = (p1 - p0) / 5.4e6, (b1 - b0) / 1500
print('[tempo] %.3f steps in %.4f s of audio = %.4f steps/s = %.3f BPM (%d sub-steps)'
      % (steps, secs, steps / secs, steps / secs * 15, len(inc)))
sys.exit(rc)
