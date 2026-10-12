#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""T2.4b: run emu.live headless with the audio clock and report the firmware's EXCEPTION screen.

Run from vendor/digikit:
  DT2_SECTIONS=out/sections/dn2-1.12 uv run python ../../scripts/t24b_debug.py SNAP SYX SCRIPT OUTDIR [live args]
Wraps emu.live's build() to hook the exception reporter 0x4011ed4a (1.12) and to log every
vector raised, then prints the faulting frame. Never touches hardware.
"""
import collections, os, struct, sys
sys.path.insert(0, os.getcwd())
from unicorn.m68k_const import UC_M68K_REG_A7, UC_M68K_REG_PC
import emu.live as live

REPORTER = 0x4011ed4a
STEP_HANDLER = 0x400d99de
log = collections.deque(maxlen=40)
orig_build = live.build

def build(*a, **k):
    m, ev, st, pc, inq, at = orig_build(*a, **k)
    raise_vector = m.raise_vector
    def rv(vec, *x, **y):
        log.append((vec, m.uc.reg_read(UC_M68K_REG_PC)))
        return raise_vector(vec, *x, **y)
    m.raise_vector = rv
    def rep(uc, addr, size, data):
        sp = uc.reg_read(UC_M68K_REG_A7)
        words = struct.unpack('>16I', bytes(uc.mem_read(sp, 64)))
        print('[t24b] exception reporter entered, stack:', ' '.join('%08x' % w for w in words), flush=True)
        print('[t24b] last vectors:', ' '.join('%d@%08x' % v for v in log), flush=True)
    at(REPORTER, rep)
    state['ev'] = ev
    state['m'] = m
    def step(uc, addr, size, data):
        u32 = lambda a: struct.unpack('>I', bytes(uc.mem_read(a, 4)))[0]
        emu = state.get('emu')
        ssi = ev.get('ssi0_dma')
        state.setdefault('steps', []).append((
            ssi.major_loops[50] if ssi else -1,     # audio blocks so far
            u32(0x80005398), u32(0x42c4f900), u32(0x402a132c)))
    at(STEP_HANDLER, step)
    return m, ev, st, pc, inq, at

state = {'mark': 0}
orig_png = live.save_png

def save_png(emu, path):
    """Also dump the UART8 (panel MCU) bytes sent since the previous png."""
    orig_png(emu, path)
    out = state['ev'].get('uart_out')
    if out is None:
        return
    new = bytes(out[state['mark']:])
    state['mark'] = len(out)
    with open(os.path.splitext(path)[0] + '.uart.bin', 'wb') as fh:
        fh.write(new)
    print('[t24b] %s: %d UART8 bytes since last png, emulated %.2f s; first: %s'
          % (os.path.basename(path), len(new), emu.stats['emu_s'], new[:48].hex()), flush=True)

live.save_png = save_png

live.build = build
snap, syx, script, out, *rest = sys.argv[1:]
rc = live.main([snap, '--syx', syx, '--script', script, '--out', out] + rest)
st = state.get('steps', [])
print('[t24b] step handler calls: %d' % len(st))
for row in st[:8] + st[-3:]:
    print('[t24b]   block %6d  clock %#010x  next %#010x  [0x402a132c] %d' % row)
if len(st) > 1:
    blocks = st[-1][0] - st[0][0]
    print('[t24b] calls per second of audio: %.1f (%d calls over %d blocks at 1500/s)'
          % ((len(st) - 1) * 1500 / max(1, blocks), len(st) - 1, blocks))
sys.exit(rc)
