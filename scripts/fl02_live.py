#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""FL02: scripts/t63b_live.py with every live FX word of the current kit (raw, value << 8 with its low byte) and the
current kit's stored FX record word of SPD (k 26 = stored id 2, the working image's FX record +8) at every png.

    cd vendor/digikit && DT2_SECTIONS=... uv run python ../../scripts/fl02_live.py SNAP --syx SYX --identify-as STOCK
        --speed 1 --script S --out DIR [t63b_live / t65_live / t19_live options]

Writes DIR/t63b.json as t63b_live does, each screen with 'fx_raw' (k 0..100, hex), 'spd_stored_kits_with_live_value'
(the kits of the working image whose FX record holds the live SPD word, raw, at +8) and 'spd_stored_kit0' (kit 0's).
Read-only on digikit.
"""
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.getcwd())
import t63b_live as B  # noqa: E402

WORK, KITS, KSTRIDE, FXREC = 0x405ce96c, 0xae0200, 0x2a00, 0x16ac
SPD_ID = 2                          # k 26's stored id (the map 0x401fd160)
og = B.grab


def grab(uc):
    out = og(uc)
    kit = B.rd32(uc, B.KITPTR)
    fx = [struct.unpack('>H', bytes(uc.mem_read(kit + B.FXOFF + 2 * k, 2)))[0] for k in range(101)]
    out['fx_raw'] = ['%04x' % v for v in fx]
    rec = {}
    for i in range(129):
        w = struct.unpack('>H', bytes(uc.mem_read(WORK + KITS + KSTRIDE * i + FXREC + 4 + 2 * SPD_ID, 2)))[0]
        rec[i] = w
    out['spd_stored_kits_with_live_value'] = sorted(i for i, w in rec.items() if w == fx[26])
    out['spd_stored_kit0'] = '%04x' % rec[0]
    return out


B.grab = grab

if __name__ == '__main__':
    sys.exit(B.main())
