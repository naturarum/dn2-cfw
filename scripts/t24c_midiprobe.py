#!/usr/bin/env python3
"""T2.4c: read the DIN MIDI (UART9) state of a Digitone II 1.12 snapshot.

Run from vendor/digikit:
  DT2_SECTIONS=out/sections/dn2-1.12 uv run python ../../scripts/t24c_midiprobe.py SNAP --syx SYX
Read-only: restores the snapshot, reads memory, runs nothing. Never touches hardware.
"""
import argparse, os, struct, sys
sys.path.insert(0, os.getcwd())
from emu.longrun import build

def main():
    p = argparse.ArgumentParser(); p.add_argument('snap'); p.add_argument('--syx')
    a = p.parse_args()
    m, ev, st, pc, inq, at = build(a.snap, unblock=True, softfloat=True, bitmap=True, dsp=True,
                                   deferred_components=('timers',), **({'syx': a.syx} if a.syx else {}))
    rd = lambda addr, n: bytes(m.uc.mem_read(addr, n))
    u32 = lambda addr: struct.unpack('>I', rd(addr, 4))[0]
    for v in (156, 157, 181):
        print('vector %d -> %#010x  ICR1_%d=%d' % (v, u32(0x40000000 + 4 * v), v - 128,
                                                rd(0xFC04C040 + v - 128, 1)[0]))
    for ch in (36, 37):
        t = rd(0xFC045000 + ch * 0x20, 0x20)
        saddr, attr, soff, nbytes, slast, daddr, citer, doff, dlast, biter, csr = struct.unpack('>IHhIiIHhiHH', t)
        print('TCD%d saddr %#x attr %#x soff %d nbytes %d slast %d daddr %#x citer %d doff %d dlast %d biter %d csr %#x'
              % (ch, saddr, attr, soff, nbytes, slast, daddr, citer, doff, dlast, biter, csr))
    print('UART9 regs', rd(0xEC074000, 0x20).hex())
    names = [('tx state e578', 0x4058e578), ('e57c', 0x4058e57c), ('tx ring e580', 0x4058e580),
             ('pio ring e584', 0x4058e584), ('rx ring e588', 0x4058e588), ('e58c', 0x4058e58c),
             ('tx write e590', 0x4058e590), ('pending e594', 0x4058e594), ('e598', 0x4058e598),
             ('pio rd e59c', 0x4058e59c), ('pio wr e5a0', 0x4058e5a0), ('pio count e5a4', 0x4058e5a4),
             ('rx consume e5a8', 0x4058e5a8), ('rx callback e5b8', 0x4058e5b8),
             ('MIDI out ports 446181f0', 0x446181f0), ('rt send gate 446488d0', 0x446488d0),
             ('MIDI track mask 8000537c', 0x8000537c)]
    for n, addr in names:
        print('%-28s %#010x' % (n, u32(addr)))

main()
