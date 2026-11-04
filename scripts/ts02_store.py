#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""TS02: SLIDE's p-locks in the pattern's storage (trig-slide-dn2 0.2's ts_lmap, ts_lpre and the loader's id test),
host-called on the OS's own lock serializer 0x400de87c and loader 0x400de682 from the state scripts/ts02-setup.script
leaves (track 1: ATK and SLIDE locked on step 5; the method of scripts/t63d_store.py and ts01_store.py).

    cd vendor/digikit && DT2_SYX=../../out/TS02/TS02.syx DT2_SECTIONS=../../out/TS02/emu/sections \\
        uv run python ../../scripts/ts02_store.py ../../out/TS02/emu/ts02-setup/end.snap OUT.json

The live pattern's lock table (pattern + 0x17e50: 80 lanes of 0x102 bytes, k and track then 128 values; the lane
index per track and k at +0x50a0) holds a lane for track 1's SLIDE (k 100). "stock" = the three sites given their
stock bytes back in emulator RAM (translated code flushed). Checks:
  live      the setup made the lanes: SLIDE's (k 100) with ON (0x100) on step 5, ATK's with its lock;
  save      the hooked serializer stores SLIDE's lane as stored id 107, every other lane as the stock one does;
  round     the hooked loader gives back every lane, SLIDE's with its values, the lane index as before;
  old OS    the stock loader drops the 107 lane and loads every other lane as the hooked one does;
  hazard    (shown, not required) the stock serializer, which has no k 100, stores SLIDE's lane under id map[100].
Emulator RAM only; nothing is saved. Writes OUT.json.
"""
import json
import struct
import sys
import os

DK = os.environ.get('DIGIKIT', os.getcwd())
sys.path.insert(0, DK)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import t63d_store as S                                   # noqa: E402

LSER, LLOAD = 0x400de87c, 0x400de682
SITES = {0x400de896: '47f9401fd330', 0x400de6ec: '42824bfae734', 0x400de6f8: '786a'}
NL, LANE, IDX, LT_LEN = 80, 0x102, 0x50a0, 0x5ef0


def main():
    snap, out = sys.argv[1], sys.argv[2]
    m = S.setup(snap)
    uc, rd, wr, r32, call = S.tools(m)
    hooked = {a: rd(a, len(b) // 2) for a, b in SITES.items()}
    pat = r32(0x42c5b9a0)
    lt = pat + 0x17e50

    def lanes(base):
        out = {}
        for i in range(NL):
            b = rd(base + LANE * i, LANE)
            if b[0] != 0xff and b[1] != 0xff:
                out[i] = (b[0], b[1], b[2:])
        return out

    def index(base, t, k):
        return rd(base + IDX + 0x65 * t + k, 1)[0]

    def sites(stock):
        for a, b in SITES.items():
            wr(a, bytes.fromhex(b) if stock else hooked[a])
        uc.ctl_flush_tb()

    res, checks = {}, {}
    live = lanes(lt)
    li = index(lt, 0, 100)
    slide_vals = struct.unpack('>128h', live[li][2]) if li in live else ()
    atk = index(lt, 0, 71)
    checks['live: track 1 has a SLIDE lane (lane %d) with ON (0x100) on step 5, and an ATK lane (lane %d)' % (li, atk)] = (
        li != 0xff and li in live and live[li][0] == 100 and slide_vals[4] == 0x100 and atk != 0xff)
    res['slide_lane_values'] = [v for v in slide_vals if v != -1]

    st_h, st_s, lk_h, lk_s = S.LK[4], S.LK[5], S.LK[0], S.LK[1]     # 0x6000 apart: a stored table is 0x50a0
    sites(False)
    wr(st_h, bytes(NL * LANE))
    call(LSER, [st_h, lt])
    sites(True)
    wr(st_s, bytes(NL * LANE))
    call(LSER, [st_s, lt])
    sh, ss = lanes(st_h), lanes(st_s)
    checks['save: the hooked serializer stores SLIDE\'s lane as stored id 107 (track 1)'] = sh.get(li, (0,))[0] == 107 \
        and sh[li][1] == 0
    checks['save: every other lane as the stock serializer stores it'] = all(
        sh[i] == ss[i] for i in sh if i != li) and set(sh) == set(ss)
    res['hazard_stock_id_for_slide'] = ss.get(li, (None,))[0]

    sites(False)
    wr(lk_h, bytes(LT_LEN))
    call(LLOAD, [lk_h, st_h])
    sites(True)
    wr(lk_s, bytes(LT_LEN))
    call(LLOAD, [lk_s, st_h])
    sites(False)
    hh, hs = lanes(lk_h), lanes(lk_s)
    checks['round: the hooked loader gives back every lane (SLIDE\'s as k 100 with its values), the index as before'] = (
        hh == live and index(lk_h, 0, 100) == li and index(lk_h, 0, 71) == atk)
    checks['old OS: the stock loader drops the 107 lane (no SLIDE lane for track 1)'] = (
        li not in hs and index(lk_s, 0, 100) == 0xff)
    checks['old OS: every other lane as the hooked loader gives it'] = all(hs[i] == hh[i] for i in hs) and \
        set(hs) == set(hh) - {li}
    res['checks'] = checks
    for k, v in checks.items():
        print('%-4s %s' % ('ok' if v else 'FAIL', k))
    print('info: the stock serializer (no ts_lmap) would store the SLIDE lane under stored id %s' %
          res['hazard_stock_id_for_slide'])
    json.dump(res, open(out, 'w'), indent=1)
    return 0 if all(checks.values()) else 1


if __name__ == '__main__':
    sys.exit(main())
