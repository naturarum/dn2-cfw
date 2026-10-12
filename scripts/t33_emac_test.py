#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""T3.3: does digikit's patched Unicorn implement the ColdFire EMAC fractional mode?

Runs the two multiply-accumulates of FUN_400db28e (0x400db2c4 macw, 0x400db2c8 macl) with MACSR 0
(integer) and 0x20 (fractional, as the frame handler sets it at 0x40025e7c) and prints what the
definition of each mode gives. Run from vendor/digikit: uv run python ../../scripts/t33_emac_test.py
"""
import struct
from unicorn import Uc, UC_ARCH_M68K, UC_MODE_BIG_ENDIAN
from unicorn.m68k_const import *
def run(macsr, d0, d4, d5, d6, mem):
    uc=Uc(UC_ARCH_M68K, UC_MODE_BIG_ENDIAN); uc.ctl_set_cpu_model(UC_CPU_M68K_CFV4E)
    uc.mem_map(0x10000, 0x10000)
    code  = bytes.fromhex('a93c%08x' % macsr)  # move.l #macsr,MACSR
    code += bytes.fromhex('a1c7')              # movclr.l ACC0,D7 (clear acc0)
    code += bytes.fromhex('a891 00c6')         # macw %d6u,%d0u,%a1@,%d4,%acc0   (0x400db2c4)
    code += bytes.fromhex('a1c1')              # movclr.l %acc0,%d1
    code += bytes.fromhex('a805 0800')         # macl %d5,%d4,%acc0              (0x400db2c8)
    code += bytes.fromhex('a1c2')              # movclr.l %acc0,%d2
    uc.mem_write(0x10000, code); uc.mem_write(0x18000, struct.pack('>I', mem))
    for r,v in ((UC_M68K_REG_D0,d0),(UC_M68K_REG_D4,d4),(UC_M68K_REG_D5,d5),(UC_M68K_REG_D6,d6),(UC_M68K_REG_A1,0x18000),(UC_M68K_REG_A7,0x1f000)):
        uc.reg_write(r,v)
    uc.emu_start(0x10000, 0x10000+len(code))
    return uc.reg_read(UC_M68K_REG_D1), uc.reg_read(UC_M68K_REG_D2)
for macsr in (0x00, 0x20):
    d1,d2 = run(macsr, 0x7f000000, 0, 0x7c290000, 0x03d70000, 0x10000000)
    print('MACSR=%#04x  macw 0x03d7*0x7f00 -> %#010x   macl 0x7c290000*0x10000000 -> %#010x' % (macsr, d1, d2))
print('fractional (F/I=1) per the EMAC definition: macw -> %#010x, macl -> %#010x' % ((0x3d7*0x7f00)<<1, (0x7c290000*0x10000000)>>31))
print('integer (F/I=0): macw -> %#010x, macl -> %#010x (low 32 bits)' % (0x3d7*0x7f00, (0x7c290000*0x10000000)&0xffffffff))
