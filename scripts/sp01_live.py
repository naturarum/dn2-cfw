#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""SP01: scripts/t65_live.py with the reverb (Spring) page's state read at every screen (as scripts/t53_live.py does for
the delay page).

    cd vendor/digikit && DT2_SECTIONS=... uv run python ../../scripts/sp01_live.py SNAP --syx SYX \\
        --identify-as STOCK.syx --speed 1 --script S --out DIR [t65_live / t19_live / emu.live options] \\
        [--map BUILD.map.json]

At every `png NAME` of the script, also records (into DIR/sp01.json, by screen name):
  - the current kit's live FX setup for the reverb page, k 41..48 (live kit + 0x48e4, u16 by k);
  - the raw parameter mirror's FX row for the same k (0x800047b2 + 2k: what the frame handler smooths into the frame
    words 0xa1a.. and 0xa28);
  - the working image's stored FX record of kit 0 and kit 1 (ids 17..24 = k 41..48);
  - page record 18's knobs (0x42433f20..+0x24) and LEVEL;
  - the display entries of rows 0x7d, 0x7e, 0x83 and 0x6c (flags, steps, curve, the picture's invoker);
  - spring-names' snm_state and snm_ticks, when --map names them (SP01's build map).
Everything else is t65_live.py's. Read-only on digikit.
"""
import json
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.getcwd())                      # vendor/digikit
import t65_live                                      # noqa: E402
from emu import live                                 # noqa: E402

KITPTR, FXOFF, RAW16 = 0x800052a0, 0x48e4, 0x800047b2
WORK, KITS, KSTRIDE, FXREC = 0x405ce96c, 0xae0200, 0x2a00, 0x16ac
PAGE18 = 0x42433f18
DISP, DROW = 0x4243425c, 0x44
K = range(41, 49)
captured = {}
syms = {}


def grab(uc):
    rd = lambda a, n: bytes(uc.mem_read(a, n))                       # noqa: E731
    u16 = lambda a: struct.unpack('>H', rd(a, 2))[0]                  # noqa: E731
    u32 = lambda a: struct.unpack('>I', rd(a, 4))[0]                  # noqa: E731
    kit = u32(KITPTR)
    out = {'live_k41_48': [u16(kit + FXOFF + 2 * k) for k in K],
           'mirror_k41_48': [u16(RAW16 + 2 * k) for k in K],
           'stored_kit0_id17_24': [u16(WORK + KITS + FXREC + 4 + 2 * i) for i in range(17, 25)],
           'stored_kit1_id17_24': [u16(WORK + KITS + KSTRIDE + FXREC + 4 + 2 * i) for i in range(17, 25)],
           'page18_rows': ['0x%x' % u32(PAGE18 + 8 + 4 * i) for i in range(9)]}
    for r in (0x7d, 0x7e, 0x83, 0x6c):
        out['disp_0x%x' % r] = ['%08x' % u32(DISP + DROW * r + 4 * i) for i in range(9)]
    for nm, a in syms.items():
        out[nm] = struct.unpack('>i', rd(a, 4))[0]
    return out


def main():
    argv = sys.argv[1:]
    out = argv[argv.index('--out') + 1] if '--out' in argv else '.'
    if '--map' in argv:
        i = argv.index('--map')
        mp = json.load(open(argv[i + 1]))
        del argv[i:i + 2]
        for nm in ('snm_state', 'snm_ticks'):
            if nm in mp:
                syms[nm] = int(mp[nm], 16)
    orig_png = live.save_png

    def save_png(emu, path):
        r = orig_png(emu, path)
        try:
            captured[os.path.basename(path)[:-4]] = grab(emu._m.uc)
        except Exception as e:                       # noqa: BLE001
            captured[os.path.basename(path)[:-4]] = {'error': str(e)}
        return r
    live.save_png = save_png
    orig_summary = live.summary

    def summary(emu):
        captured['__end__'] = grab(emu._m.uc)
        json.dump(captured, open(os.path.join(out, 'sp01.json'), 'w'), indent=1)
        print('[sp01] state at %d screens -> %s' % (len(captured) - 1, os.path.join(out, 'sp01.json')), flush=True)
        return orig_summary(emu)
    live.summary = summary
    sys.argv = [sys.argv[0]] + argv
    return t65_live.main()


if __name__ == '__main__':
    sys.exit(main())
