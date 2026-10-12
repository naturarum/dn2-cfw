#!/usr/bin/env python3
"""T3.3: capture the ColdFire-to-DSP TX frame of Digitone II OS 1.12 from emulator snapshots.

Run from vendor/digikit (it imports digikit's emu package):

  cd vendor/digikit
  DT2_SECTIONS=out/sections/dn2-1.12 uv run python ../../scripts/t33_frame.py \
      SNAP [SNAP ...] --passes 300 --out ../../out/T3.3/frames

For each snapshot: restore it (digikit emu.longrun.build, same flags as tools/sharcframe.py),
read the raw parameter mirror (0x80003af0, 0xd8c bytes) and the vector-191 slot, then
enter the vector-191 handler FUN_40025e36 PASSES times (each pass: clear the pacing counter
0x40287c38, raise vector 191, run to the handler's rte). The DSPI2 driver FUN_400cf922 is
hooked: it records its arguments and returns at once, so no SPI or eDMA is touched.

The handler sends the frame built by the previous pass, then builds the next one in the TX
buffer 0x80005e60 (0xa80 bytes). The parameter values in it come from the smoothed copy
0x800068e4, which FUN_400db28e moves about 3% of the way to the raw mirror per pass, so many
passes are needed before a changed value settles.

Writes, per snapshot NAME: NAME.tx.bin (the TX buffer after the last pass), NAME.sent.bin (what the
driver was asked to send on the last pass), NAME.raw.bin (raw mirror before the first pass),
NAME.smooth.bin (smoothed buffer after the last pass) and NAME.json.

Addresses are for main OS 1.12 (sha256 a098f620...); the script refuses another image.
Never touches hardware.
"""
import argparse
import hashlib
import json
import os
import struct
import sys

DK = os.environ.get('DIGIKIT', os.getcwd())
sys.path.insert(0, DK)

from unicorn import UcError  # noqa: E402
from unicorn.m68k_const import UC_M68K_REG_A7, UC_M68K_REG_D0, UC_M68K_REG_PC  # noqa: E402

MAIN_SHA = 'a098f620'          # prefix of the 1.12 main OS sha256 (notes/T1.2)
VECTOR = 191
VEC_SLOT = 0x400002fc
HANDLER = 0x40025e36
DRIVER = 0x400cf922
COUNTER = 0x40287c38
TX, TX_LEN = 0x80005e60, 0xa80
RAW, RAW_LEN = 0x80003af0, 0xd8c
SMOOTH = 0x800068e4


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument('snapshots', nargs='+')
    p.add_argument('--passes', type=int, default=300)
    p.add_argument('--limit', type=lambda s: int(s, 0), default=5_000_000)
    p.add_argument('--out', required=True)
    p.add_argument('--syx')
    p.add_argument('--no-unblock', dest='unblock', action='store_false',
                   help='for ladder snapshots made without unblock (boot*M.snap)')
    p.add_argument('--fx-copy', action='store_true',
                   help='before the passes, call FUN_40025444(*(0x800052a0) + 0x48e4) once if the '
                        'copied FX setup pointer 0x800052a4 is stale (host calibration: the handler '
                        'only does this while it drains sequencer events)')
    a = p.parse_args(argv)

    from emu import config
    from emu.longrun import build
    img = config.main_image()
    sha = hashlib.sha256(open(img, 'rb').read()).hexdigest()
    if not sha.startswith(MAIN_SHA):
        raise SystemExit('%s is not the 1.12 main OS (sha256 %s)' % (img, sha))
    os.makedirs(a.out, exist_ok=True)

    for snap in a.snapshots:
        name = os.path.splitext(os.path.basename(snap))[0]
        m, ev, st, pc, inq, at = build(snap, syx=a.syx, softfloat=True, bitmap=True, dsp=True, unblock=a.unblock,
                                       deferred_components=('timers',))
        uc = m.uc
        raw = bytes(uc.mem_read(RAW, RAW_LEN))
        slot = struct.unpack('>I', uc.mem_read(VEC_SLOT, 4))[0]
        calls = []

        def driver(uc, addr, size, user):
            sp = uc.reg_read(UC_M68K_REG_A7)
            ret, tx_len, tx, rx_len, rx = struct.unpack('>IIIII', uc.mem_read(sp, 20))
            calls.append((ret, tx_len, tx, rx_len, rx, bytes(uc.mem_read(tx, tx_len))))
            uc.reg_write(UC_M68K_REG_D0, 0)
            uc.reg_write(UC_M68K_REG_A7, sp + 4)
            uc.reg_write(UC_M68K_REG_PC, ret)

        at(DRIVER, driver)
        resume = uc.reg_read(UC_M68K_REG_PC)
        fx_copied = None
        if a.fx_copy:
            kit = struct.unpack('>I', uc.mem_read(0x800052a0, 4))[0]
            cur = struct.unpack('>I', uc.mem_read(0x800052a4, 4))[0]
            if kit and cur != kit + 0x48e4:
                sp0 = uc.reg_read(UC_M68K_REG_A7)
                sp = sp0 - 8
                uc.mem_write(sp, struct.pack('>II', resume, kit + 0x48e4))
                uc.reg_write(UC_M68K_REG_A7, sp)
                uc.emu_start(0x40025444, resume, count=200_000)
                uc.reg_write(UC_M68K_REG_A7, sp0)
                fx_copied = '%#010x' % (kit + 0x48e4)
                print('%s: called FUN_40025444(%s)' % (name, fx_copied))
        stops = {}
        insns = 0
        for n in range(a.passes):
            uc.mem_write(COUNTER, bytes(4))
            uc.reg_write(UC_M68K_REG_PC, resume)
            m.halt_vec = None
            if not m.raise_vector(VECTOR):
                raise SystemExit('%s: vector %d holds no handler' % (snap, VECTOR))
            h = uc.reg_read(UC_M68K_REG_PC)
            stop = None
            try:
                uc.emu_start(h, resume, count=a.limit)
            except UcError as e:
                stop = 'UcError: %s' % e
            if stop is None:
                stop = 'returned' if uc.reg_read(UC_M68K_REG_PC) == resume else 'limit/other'
            stops[stop] = stops.get(stop, 0) + 1
            if stop != 'returned':
                print('%s pass %d: %s at %#x' % (name, n, stop, uc.reg_read(UC_M68K_REG_PC)))
                break
        tx = bytes(uc.mem_read(TX, TX_LEN))
        smooth = bytes(uc.mem_read(SMOOTH, RAW_LEN))
        m.close()
        files = {}
        for suffix, data in (('tx', tx), ('raw', raw), ('smooth', smooth),
                             ('sent', calls[-1][5] if calls else b'')):
            path = os.path.join(a.out, '%s.%s.bin' % (name, suffix))
            with open(path, 'wb') as f:
                f.write(data)
            files[suffix] = path
        info = {'snapshot': snap, 'main_sha256': sha, 'vector_slot': '%#010x' % slot,
                'passes': a.passes, 'fx_copy_called': fx_copied, 'stops': stops, 'driver_calls': len(calls),
                'driver_args': ['%#x %#x %#x %#x %#x' % c[:5] for c in calls[:1]],
                'files': files}
        with open(os.path.join(a.out, name + '.json'), 'w') as f:
            json.dump(info, f, indent=1)
        print('%s: slot %#010x, %s, %d driver calls, args %s' % (
            name, slot, stops, len(calls), info['driver_args']))
    return 0


if __name__ == '__main__':
    sys.exit(main())
