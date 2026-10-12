#!/usr/bin/env python3
"""T3.3: dump the active FX setup block (101 parameter words + the long at +0xca) from snapshots.

The frame handler copies it into raw-mirror row 16 with FUN_40025444 when the pointer
*(0x800052a0) + 0x48e4 differs from 0x800052a4. Run from vendor/digikit:
  DT2_SECTIONS=out/sections/dn2-1.12 uv run python ../../scripts/t33_fxsetup.py --syx SYX SNAP...
Prints, per snapshot, the words that differ from the first snapshot.
"""
import argparse
import os
import struct
import sys

sys.path.insert(0, os.getcwd())


def main():
    p = argparse.ArgumentParser()
    p.add_argument('snapshots', nargs='+')
    p.add_argument('--syx')
    a = p.parse_args()
    from emu.longrun import build
    first = None
    for snap in a.snapshots:
        m, ev, st, pc, inq, at = build(snap, syx=a.syx, softfloat=True, bitmap=True, dsp=True,
                                       unblock=True, deferred_components=('timers',))
        uc = m.uc
        kit = struct.unpack('>I', uc.mem_read(0x800052a0, 4))[0]
        cur = struct.unpack('>I', uc.mem_read(0x800052a4, 4))[0]
        fx = kit + 0x48e4
        blk = bytes(uc.mem_read(fx, 0xce))
        m.close()
        name = os.path.basename(snap)
        print('%s: kit %#010x, fx setup %#010x, copied pointer %#010x, long +0xca %s'
              % (name, kit, fx, cur, blk[0xca:].hex()))
        if first is None:
            first = blk
            print('  words 25..69:', ' '.join('%04x' % struct.unpack('>H', blk[2*k:2*k+2])
                                             for k in range(25, 70)))
        else:
            for o in range(0, 0xce, 2):
                if blk[o:o+2] != first[o:o+2]:
                    print('  +%#05x (%s) %s -> %s' % (o, 'idx%d' % (o // 2) if o < 0xca else 'flags',
                                                     first[o:o+2].hex(), blk[o:o+2].hex()))


if __name__ == '__main__':
    main()
