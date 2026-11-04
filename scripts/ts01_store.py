#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""TS01: SLIDE's storage (trig-slide-dn2's ts_ser and ts_load, ts_stubs.s), host-called on the OS's own kit
serializer 0x400ddfa8 and loader 0x400dddb6 from TS01's warm snapshot (the method of scripts/t63d_store.py). Every
Save Kit, project save and load, +Drive library save, export and SysEx dump goes through these two (notes/T6.3).

    cd vendor/digikit && DT2_SYX=../../out/TS01/TS01.syx DT2_SECTIONS=../../out/TS01/emu/sections \\
        uv run python ../../scripts/ts01_store.py ../../out/TS01/emu/warm800M.snap OUT.json

The live kit's track sounds get SLIDE (k 100) on tracks 1 and 4 (0x100), off elsewhere. "stock" = the two sites
given their stock bytes back in emulator RAM. Checks:
  round trip  hooked save -> hooked load: k 100 as set on every track, every other word as the stock round trip's;
  old OS      hooked save -> stock load: k 100 off everywhere, every other word as the stock round trip's;
  old kits    stock save -> hooked load: k 100 off everywhere (the stock serializer clears stored id 107);
  record      the hooked record differs from the stock one only at stored id 107 of the tracks with SLIDE on
              (the kit record's sounds are 0x167 bytes from +0x3c; 0x0100 there: one byte).
Emulator RAM only; nothing is saved. Writes OUT.json.
"""
import json
import os
import struct
import sys

DK = os.environ.get('DIGIKIT', os.getcwd())
sys.path.insert(0, DK)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import t63d_store as S                                   # noqa: E402

KIT_SAVE, KIT_LOAD = 0x400ddfa8, 0x400dddb6
SITES = {0x400dd8b2: '156b00de00f4', 0x400dd3e6: '712a001c7406'}
SOUND = lambda kit, t: kit + 0x34 + 0x48b * t            # noqa: E731
ON = (0, 3)


def main():
    snap, out = sys.argv[1], sys.argv[2]
    m = S.setup(snap)
    uc, rd, wr, r32, call = S.tools(m)
    kit = r32(S.KITPTR)
    hooked = {a: rd(a, 6) for a in SITES}
    ok = all(hooked[a][:2] == b'\x4e\xb9' for a in SITES)
    res = {'kit': '%08x' % kit, 'sites_hooked': ok}
    for t in range(16):
        wr(SOUND(kit, t) + 0x14 + 200, struct.pack('>h', 0x100 if t in ON else 0))

    def sites(stock):
        for a, b in SITES.items():
            wr(a, bytes.fromhex(b) if stock else hooked[a])
        uc.ctl_flush_tb()                                # unicorn keeps translated code: drop it after a rewrite

    def save(rec, stock):
        sites(stock)
        wr(rec, bytes(S.RECLEN))
        return call(KIT_SAVE, [rec, kit, 0]) & 0xff

    def load(lk, rec, stock):
        sites(stock)
        wr(lk, bytes(S.KITLEN))
        return call(KIT_LOAD, [lk, rec]) & 0xff

    def sounds(lk):
        return [rd(SOUND(lk, t), 0x48b) for t in range(16)]

    def k100(lk):
        return [struct.unpack('>h', rd(SOUND(lk, t) + 0x14 + 200, 2))[0] for t in range(16)]

    rh, rs = S.REC[0], S.REC[1]
    res['save_ret'] = [save(rh, False), save(rs, True)]
    rec_h, rec_s = rd(rh, S.RECLEN), rd(rs, S.RECLEN)
    diff = [i for i in range(S.RECLEN) if rec_h[i] != rec_s[i]]
    res['record_diff_bytes'] = ['%#x' % i for i in diff]
    res['load_ret'] = [load(S.LK[0], rh, False), load(S.LK[1], rs, True), load(S.LK[2], rh, True),
                       load(S.LK[3], rs, False)]
    hh, ss, hs, sh = (sounds(S.LK[i]) for i in range(4))

    def same_but_k100(a, b):
        return all(x[:0x14 + 200] + x[0x14 + 202:] == y[:0x14 + 200] + y[0x14 + 202:] for x, y in zip(a, b))

    want = [0x100 if t in ON else 0 for t in range(16)]
    checks = {
        'the two sites are hooked (jsr) in the image': ok,
        'round trip: hooked save -> hooked load gives k 100 as set (on for tracks 1 and 4)': k100(S.LK[0]) == want,
        'round trip: every other word as the stock round trip': same_but_k100(hh, ss),
        'old OS: hooked save -> stock load gives k 100 off everywhere': k100(S.LK[2]) == [0] * 16,
        'old OS: every other word as the stock round trip': same_but_k100(hs, ss),
        'old kits: stock save -> hooked load gives k 100 off everywhere': k100(S.LK[3]) == [0] * 16,
        'old kits: every other word as the stock round trip': same_but_k100(sh, ss),
        'record: the hooked record differs from the stock one only at stored id 107 of the tracks with SLIDE on '
        '(record + 0x3c + 0x167 t + 0xf2, its high byte 1: %s)' % ', '.join('%#x' % i for i in diff):
            diff == [0x3c + 0x167 * t + 0xf2 for t in ON] and all(rec_h[i] == 0x01 for i in diff),
        'the stock round trip keeps k 100 off (stock loader clears the live words)': k100(S.LK[1]) == [0] * 16,
    }
    res['k100'] = {'hooked_hooked': k100(S.LK[0]), 'stock_stock': k100(S.LK[1]), 'hooked_stock': k100(S.LK[2]),
                   'stock_hooked': k100(S.LK[3])}
    res['checks'] = checks
    sites(False)
    for k, v in checks.items():
        print('%-4s %s' % ('ok' if v else 'FAIL', k))
    print('record diff at', ' '.join(res['record_diff_bytes'][:8]))
    json.dump(res, open(out, 'w'), indent=1)
    return 0 if all(checks.values()) else 1


if __name__ == '__main__':
    sys.exit(main())
