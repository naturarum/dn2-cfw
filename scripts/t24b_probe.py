#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""T2.4b: read the audio-clock state of a Digitone II 1.12 snapshot (vector slots, INTC, eDMA 48/50).

Run from vendor/digikit:
  DT2_SECTIONS=out/sections/dn2-1.12 uv run python ../../scripts/t24b_probe.py SNAP --syx SYX
Read-only: restores the snapshot, reads memory, runs nothing. Never touches hardware.
"""
import argparse, os, struct, sys
sys.path.insert(0, os.getcwd())
from emu.longrun import build
from emu import config, symbols

TCD = lambda ch: 0xFC045000 + ch * 0x20
VBR_SLOT = lambda v: 0x40000000 + 4 * v

def main():
    p = argparse.ArgumentParser(); p.add_argument('snap'); p.add_argument('--syx')
    a = p.parse_args()
    m, ev, st, pc, inq, at = build(a.snap, unblock=True, softfloat=True, bitmap=True, dsp=True,
                                   deferred_components=('timers',), **({'syx': a.syx} if a.syx else {}))
    rd = lambda addr, n: bytes(m.uc.mem_read(addr, n))
    u32 = lambda addr: struct.unpack('>I', rd(addr, 4))[0]
    prof = symbols.resolve(open(config.main_image(), 'rb').read())
    print('pc %#x' % pc)
    print('ssi0_dma_force_rte', prof.get('ssi0_dma_force_rte'))
    for v in (64 + 40, 168, 170, 191):
        print('vector %d slot %#x -> %#010x' % (v, VBR_SLOT(v), u32(VBR_SLOT(v))))
    # INTC1: IMRH1 0xFC04C008, IMRL1 0xFC04C00C, INTFRCH1 0xFC04C010, ICR1_n 0xFC04C040+n
    for name, addr in (('IMRH1', 0xFC04C008), ('IMRL1', 0xFC04C00C), ('INTFRCH1', 0xFC04C010)):
        print(name, '%#010x' % u32(addr))
    print('ICR1_42', rd(0xFC04C040 + 42, 1).hex(), 'ICR1_40', rd(0xFC04C040 + 40, 1).hex(),
          'ICR1_63', rd(0xFC04C040 + 63, 1).hex())
    print('EDMA ERQH/ERQL', '%#010x %#010x' % (u32(0xFC044008), u32(0xFC04400C)))
    for ch in (28, 29, 48, 50):
        t = rd(TCD(ch), 0x20)
        saddr, soff, attr, nbytes, slast, daddr, doff, citer, dlast, csr, biter = struct.unpack('>IhHIiIhHiHH', t)
        print('TCD%d saddr %#x soff %d attr %#x nbytes %#x slast %d daddr %#x doff %d citer %#x dlast %#x csr %#x biter %#x'
              % (ch, saddr, soff, attr, nbytes, slast, daddr, doff, citer, dlast & 0xffffffff, csr, biter))
    # SSI0 regs 0xFC0BC000: TX0, TX1, RX0, RX1, CR(0x10), ISR(0x14), IER(0x18), TCR(0x1c), RCR(0x20), CCR(0x24)
    for off, name in ((0x10, 'SCR'), (0x18, 'IER'), (0x1c, 'TCR'), (0x20, 'RCR'), (0x24, 'CCR')):
        print('SSI0', name, '%#010x' % u32(0xFC0BC000 + off))

main()
