#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""OB01: what the main OS reports as its version, read from a booted emulator snapshot (notes/OB01-overbridge.md).

    cd vendor/digikit && uv run python ../../scripts/ob01_emucheck.py SNAP [--map MAP.json] [--out report.json]

From the snapshot (a cold boot of the container in digikit's emulator, emu.checkpoint):
  - the header cache 0x4463fc2c (the container's first 0x20 bytes, read from flash by the main OS);
  - the USB vendor request 0xC0/0x02 reply, the 16 bytes at 0x40287ad5 the init task copied from the header;
  - the MidiRpcSoftwareVersionRequest reply's two copies: the handler's code from 0x40126270 (the getter's call site)
    to 0x401262b8 run on the snapshot's machine with a scratch frame, then the 4 bytes at fp-64 and the 12 at d2;
  - with --map (an OB01 link map): osv_hdr called on its own, and the 0x20 bytes it returns.
Read-only: nothing is written back to the snapshot.
"""
import argparse
import json
import os
import struct
import sys

sys.path.insert(0, os.getcwd())
from unicorn.m68k_const import (UC_M68K_REG_A6, UC_M68K_REG_A7, UC_M68K_REG_D0, UC_M68K_REG_D2,  # noqa: E402
                                UC_M68K_REG_PC)
from emu.snapshot import restore  # noqa: E402

HDR_CACHE, HDR_FLAG, USB_REPLY = 0x4463fc2c, 0x4463fc28, 0x40287ad5
RPC_SITE, RPC_END = 0x40126270, 0x401262b8
SCRATCH = 0x47f80000            # the top of the mods' DDR area: free in every link so far (ddr_spare ~1 MB)


def show(b):
    return b.decode('latin-1').replace('\0', '.')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('snap')
    ap.add_argument('--map')
    ap.add_argument('--out')
    a = ap.parse_args()
    m, _extra, _regs = restore(a.snap)
    uc = m.uc
    rd = lambda ad, n: bytes(uc.mem_read(ad, n))                          # noqa: E731
    rep = {'snap': os.path.abspath(a.snap),
           'header_flag': struct.unpack('>I', rd(HDR_FLAG, 4))[0],
           'header_cache': show(rd(HDR_CACHE + 8, 16)), 'header_cache_hex': rd(HDR_CACHE, 0x20).hex(),
           'usb_reply': show(rd(USB_REPLY, 16))}
    for base in range(SCRATCH, SCRATCH + 0x4000, 0x1000):
        m.ensure(base)
    stack, frame, buf, stop = SCRATCH + 0x1000, SCRATCH + 0x2000, SCRATCH + 0x3000, SCRATCH + 0x3800
    uc.mem_write(SCRATCH, bytes(0x4000))
    uc.mem_write(stop, bytes.fromhex('4e71'))                               # a nop to stop on
    uc.reg_write(UC_M68K_REG_A7, stack)
    uc.reg_write(UC_M68K_REG_A6, frame)
    uc.reg_write(UC_M68K_REG_D2, buf)
    uc.emu_start(RPC_SITE, RPC_END, count=20000)
    rep['rpc_reply_build'] = show(rd(frame - 64, 4))
    rep['rpc_reply_version'] = show(rd(buf, 12))
    if a.map:
        mp = json.load(open(a.map))
        osv = int(mp['osv_hdr'], 16)
        uc.reg_write(UC_M68K_REG_A7, stack - 4)
        uc.mem_write(stack - 4, struct.pack('>I', stop))
        uc.emu_start(osv, stop, count=20000)
        p = uc.reg_read(UC_M68K_REG_D0)
        rep['osv_hdr_returns'] = '0x%08x' % p
        rep['osv_hdr_copy'] = show(rd(p + 8, 16)) if p else None
        rep['osv_copy_is_osv_copy_symbol'] = p == int(mp['osv_copy'], 16)
        rep['osv_copy_rest_as_cache'] = p != 0 and rd(p, 0x14) == rd(HDR_CACHE, 0x14) and rd(p + 0x18, 8) == rd(HDR_CACHE + 0x18, 8)
    rep['pc_after'] = '0x%08x' % uc.reg_read(UC_M68K_REG_PC)
    print(json.dumps(rep, indent=1))
    if a.out:
        json.dump(rep, open(a.out, 'w'), indent=1)


if __name__ == '__main__':
    main()
