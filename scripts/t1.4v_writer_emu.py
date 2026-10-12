#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Run the Digitone II OS 1.12 in-OS upgrade writer FUN_40139104 alone under Unicorn
and log every SPI NOR command it sends on DSPI0.

Purpose (T1.4 verification): check dynamically where the writer erases and
programs, for a given container length, without hardware and without a full boot.

What is real and what is stubbed:
  real    FUN_40139104 (the writer), FUN_401357ce (block erase), FUN_40134a6a (erase
          command), FUN_40134a28 (WREN), FUN_40134b04 (RDSR), FUN_401360a6 /
          FUN_401354d6 (program), FUN_4013620a / FUN_401361f6 / FUN_40135a7c (geometry)
  stubbed FUN_400dbdb0 (validator, forced to return 1), the UI text helpers
          FUN_401ceab8 / FUN_401c0ba6 / FUN_401cd008, the delay FUN_4013492c,
          FUN_400023f6, and the lock/scheduler helpers FUN_40134b9a, FUN_400015a0,
          FUN_401328c4, FUN_4013298a, FUN_400016d2 (all: moveq #0,d0; rts)
  model   DSPI0 SR reads return TCF|RXCTR=1, POPR reads return 0 (flash idle, no
          error bits), so every erase/program "succeeds" at once.

The flash geometry globals are preset to the S25FL127S branch of the probe
(page 0x200 at 0x402a12f8, erase block 0x40000 at 0x402a12f4, flag 0x4463fc64=1).

Usage (needs Unicorn with ColdFire V4e, e.g. vendor/digikit/.venv):
  vendor/digikit/.venv/bin/python scripts/t1.4v_writer_emu.py LEN [--erase-only] [--json OUT]
LEN is the container length word (*param_1), decimal or 0x hex.
Static/dynamic research only; never talks to hardware.
"""
import sys, os, json, argparse
from unicorn import Uc, UC_ARCH_M68K, UC_MODE_BIG_ENDIAN, UC_HOOK_MEM_WRITE, \
    UC_HOOK_MEM_READ, UC_HOOK_CODE
from unicorn.m68k_const import UC_M68K_REG_A7, UC_M68K_REG_PC, UC_CPU_M68K_CFV4E

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAIN = os.path.join(ROOT, 'ele3_out/02_type3.unpacked.bin')
BASE = 0x40000400
WRITER = 0x40139104
STUB0 = [0x401ceab8, 0x401c0ba6, 0x401cd008, 0x4013492c, 0x400023f6,
         0x40134b9a, 0x400015a0, 0x401328c4, 0x4013298a, 0x400016d2]
STUB1 = [0x400dbdb0]
WRITING_FLASH_UI = 0x401ceab8      # first UI call after the erase loop
DSPI0 = 0xfc05c000
PUSHR, SR, POPR = DSPI0 + 0x34, DSPI0 + 0x2c, DSPI0 + 0x38
RCR = 0xec090000
HALT = 0x48000ff0
STAGE = 0x41000000                 # where the fake staged container lives


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('length')
    ap.add_argument('--erase-only', action='store_true',
                    help='stop at the first UI call after the erase loop')
    ap.add_argument('--json')
    a = ap.parse_args()
    length = int(a.length, 0)

    uc = Uc(UC_ARCH_M68K, UC_MODE_BIG_ENDIAN)
    uc.ctl_set_cpu_model(UC_CPU_M68K_CFV4E)
    uc.mem_map(0x40000000, 0x08100000)          # DDR up to past 0x48000000
    uc.mem_map(0xfc000000, 0x00100000)          # peripheral windows
    uc.mem_map(0xec000000, 0x00100000)
    img = open(MAIN, 'rb').read()
    uc.mem_write(BASE, img)
    for f in STUB0:
        uc.mem_write(f, bytes.fromhex('70004e75'))
    for f in STUB1:
        uc.mem_write(f, bytes.fromhex('70014e75'))
    w32 = lambda ad, v: uc.mem_write(ad, (v & 0xffffffff).to_bytes(4, 'big'))
    w32(0x402a12f8, 0x200)
    w32(0x402a12f4, 0x40000)
    w32(0x4463fc64, 1)
    w32(STAGE, length)
    w32(STAGE + 4, 0)
    sp = 0x47f00000
    for v in (0x41f00000, STAGE, HALT):         # param_2, param_1, return address
        sp -= 4
        w32(sp, v)
    uc.reg_write(UC_M68K_REG_A7, sp)
    uc.mem_write(HALT, bytes.fromhex('4e714e71'))

    txns, cur = [], []
    state = {'stop': None}

    def on_write(uc_, access, addr, size, value, _):
        if addr == PUSHR:
            cur.append(value & 0xffffffff)
            if (value >> 27) & 1 or not (value >> 31) & 1:    # EOQ or CONT=0
                txns.append(list(cur)); cur.clear()
        elif addr == RCR and (value & 0x80):
            state['stop'] = 'soft reset (RCR=0x80)'; uc_.emu_stop()

    def on_read(uc_, access, addr, size, value, _):
        if addr == SR:
            w32(SR, 0x10000010)
        elif addr == POPR:
            w32(POPR, 0)

    def on_code(uc_, addr, size, _):
        if addr == HALT:
            state['stop'] = 'returned'; uc_.emu_stop()
        elif addr == WRITING_FLASH_UI and a.erase_only:
            state['stop'] = 'erase loop done (--erase-only)'; uc_.emu_stop()

    uc.hook_add(UC_HOOK_MEM_WRITE, on_write, begin=PUSHR, end=PUSHR + 3)
    uc.hook_add(UC_HOOK_MEM_WRITE, on_write, begin=RCR, end=RCR)
    uc.hook_add(UC_HOOK_MEM_READ, on_read, begin=SR, end=POPR + 3)
    uc.hook_add(UC_HOOK_CODE, on_code, begin=HALT, end=HALT + 1)
    uc.hook_add(UC_HOOK_CODE, on_code, begin=WRITING_FLASH_UI, end=WRITING_FLASH_UI + 1)
    try:
        uc.emu_start(WRITER, 0xffffffff)
    except Exception as e:
        state['stop'] = 'exception %s at pc=%#x' % (e, uc.reg_read(UC_M68K_REG_PC))

    erases, progs, other = [], [], {}
    for t in txns:
        op = t[0] & 0xff
        if op in (0xd8, 0x20, 0x02) and len(t) >= 4:
            ad = ((t[1] & 0xff) << 16) | ((t[2] & 0xff) << 8) | (t[3] & 0xff)
            (erases if op != 0x02 else progs).append((op, ad, len(t) - 4))
        else:
            other[op] = other.get(op, 0) + 1
    out = {
        'length': length, 'stop': state['stop'], 'transactions': len(txns),
        'erase_cmds': [('%#04x' % op, '%#08x' % ad) for op, ad, _ in erases],
        'program_count': len(progs),
        'program_first': '%#08x' % progs[0][1] if progs else None,
        'program_last': '%#08x' % progs[-1][1] if progs else None,
        'program_min': '%#08x' % min(p[1] for p in progs) if progs else None,
        'program_bytes_per_page': sorted({p[2] for p in progs}) if progs else [],
        'other_opcodes': {'%#04x' % k: v for k, v in sorted(other.items())},
        'erase_min': '%#08x' % min(e[1] for e in erases) if erases else None,
    }
    print(json.dumps(out, indent=1))
    if a.json:
        json.dump(out, open(a.json, 'w'), indent=1)


if __name__ == '__main__':
    main()
