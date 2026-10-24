#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""DJ01: what a kit with WAVE 7..15 keeps, saved and loaded by the stock code, on DJ01 and on a stock-LFO OS (DT01).

    cd vendor/digikit && uv run python ../../scripts/dj01_store.py DJ01_END.snap DJ01_BASE.snap DT01_WARM.snap \\
        [--json out/DJ01/store.json]

DJ01_END: the end of scripts/dj01-plock.script on DJ01 (track 1's LFO1 WAVE turned on the panel from its default, 1,
up 8, then three p-locks on it); DJ01_BASE: the warm snapshot it started from; DT01_WARM: a warm snapshot of a main OS
with stock LFO code (FL02's, = DT01's byte for byte). Each snapshot is loaded into a fresh digikit Machine with digikit's
ISA patches (FF1, MOVEC, BITREV) and no other run-time hooks, and the OS's own routines are host-called:

  1. the live kit (*0x800052a0): track 1's sound (kit + 0x34, k at + 0x14 + 2k, where the kit loader puts them):
     its three WAVE words, = the raw parameter mirror's (0x80003af0 + 0x22 + 2k); the working project image
     (0x405ce96c, the state kept across power-off and what a project save writes): kit 0's stored sound 0
     (+0xae0200 + 0x3c, stored id 17, 18, 19 at +0x1c + 2 id) holds the panel's value; the p-lock lanes the run
     made on LFO1 WAVE (t61_locks' scan: k 5 in the live pattern, stored id 17 in the working image, track 1) hold
     the locked values, every one an added wave;
  2. DJ01: the live sound's WAVE words set to 9, 12, 15 (PMAJ, WTON, OCTA) on LFO 1-3 of every track, the kit
     serializer FUN_400ddfa8(stored, kit) into a scratch record, the words cleared, the kit loader
     FUN_400dddb6(kit, stored): every word back; the record bytes noted; the value text (the row's +0x34);
  3. DT01: that record loaded by its kit loader into its live kit: the words it keeps (stock copies them, no
     clamp), its WAVE text for them (FUN_400e3610: "ERR" past 6), and a re-save by its serializer: the same
     record bytes (a stock OS keeps 7..15 on an in-place re-save).
"""
import argparse
import json
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'vendor', 'digikit'))
sys.path.insert(0, os.path.join(ROOT, 'scripts'))
from emu import snapshot, harness  # noqa: E402

KITPTR = 0x800052a0
SND0, SSTRIDE, SK = 0x34, 0x48b, 0x14           # live kit: sound t at + 0x34 + 0x48b t, k at + 0x14 + 2k (the loader's)
WORK, KITS = 0x405ce96c, 0xae0200
ST_SND0, ST_SSTRIDE, ST_K = 0x3c, 0x167, 0x1c   # stored kit: sound t at + 0x3c + 0x167 t, stored id at + 0x1c + 2 id
WAVE_K = {5: 17, 13: 18, 21: 19}                # k -> stored id (0x401fd330)
KIT_SAVE, KIT_LOAD = 0x400ddfa8, 0x400dddb6
ROW4F_FMT = 0x401f83a4 + 0x4f * 0x3c + 0x34          # LFO1 WAVE's value text (DJ01: dv_fmt)
SCRATCH, BUF = 0x10100000, 0x10200000


class Snap:
    def __init__(self, path):
        self.m, _, _ = snapshot.restore(path)
        self.m.install_isa_patches()                    # FF1, MOVEC, BITREV: the instructions Unicorn lacks
        self.uc = self.m.uc
        for a in (SCRATCH, BUF, 0x10000000):
            self.m.ensure(a)

    def rd(self, a, n):
        return bytes(self.uc.mem_read(a, n))

    def u16(self, a):
        return struct.unpack('>H', self.rd(a, 2))[0]

    def u32(self, a):
        return struct.unpack('>I', self.rd(a, 4))[0]

    def w16(self, a, v):
        self.uc.mem_write(a, struct.pack('>H', v))

    def kit(self):
        return self.u32(KITPTR)

    def live_wave(self, t, kit=None):
        kit = kit or self.kit()
        return {k: self.u16(kit + SND0 + SSTRIDE * t + SK + 2 * k) for k in WAVE_K}

    def call(self, f, args):
        return harness.call(self.m, f, args, stack_top=0x10000000 + 0xff000)

    def text(self, value):
        self.uc.mem_write(BUF, bytes(32))
        self.call(self.u32(ROW4F_FMT), [value, BUF])
        return self.rd(BUF, 32).split(b'\0')[0].decode('latin-1')


def lock_lanes(path):
    import t61_locks
    return t61_locks.lanes(path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('end')
    ap.add_argument('base')
    ap.add_argument('dt01')
    ap.add_argument('--json', default=os.path.join(ROOT, 'out', 'DJ01', 'store.json'))
    a = ap.parse_args()
    res = {}

    # 1. the panel's value and the locks, live and in the working image
    dj = Snap(a.end)
    kit = dj.kit()
    live = dj.live_wave(0)
    stored0 = {k: dj.u16(WORK + KITS + ST_SND0 + ST_K + 2 * i) for k, i in WAVE_K.items()}
    base = Snap(a.base)
    res['panel'] = {'live_kit': '0x%08x' % kit,
                    'track1_wave_words_live': {k: '0x%04x' % v for k, v in live.items()},
                    'track1_wave_words_working_image_kit0': {k: '0x%04x' % v for k, v in stored0.items()},
                    'before_the_run_live': {k: '0x%04x' % v for k, v in base.live_wave(0, base.kit()).items()}}
    lb, le = lock_lanes(a.base), lock_lanes(a.end)
    new = {ad: v for ad, v in le.items() if lb.get(ad) != v}
    lanes = sorted(set((k, tr, steps) for k, tr, steps in new.values()))
    mirror = {k: dj.u16(0x80003af0 + 0x22 + 2 * k) for k in WAVE_K}
    wl = [steps for k, tr, steps in lanes if k == 5 and tr == 0]          # the live pattern's lane (k)
    ws = [steps for k, tr, steps in lanes if k == 17 and tr == 0]         # the working image's (stored id)
    lock_vals = [v >> 8 for _, v in wl[0]] if wl else []
    res['locks'] = {'live_pattern_k5_track1': [[st_, '0x%04x' % v] for st_, v in wl[0]] if wl else None,
                    'working_image_id17_track1': [[st_, '0x%04x' % v] for st_, v in ws[0]] if ws else None}
    res['panel']['raw_mirror_row0_wave_words'] = {k: '0x%04x' % v for k, v in mirror.items()}
    res['panel']['lock_values_on_wave'] = lock_vals
    res['panel']['ok'] = (live == mirror and 7 << 8 <= live[5] <= 15 << 8 and stored0 == live and len(wl) == 1
                          and len(ws) == 1 and wl[0] == ws[0] and len(lock_vals) == 3
                          and all(7 <= v <= 15 for v in lock_vals))

    # 2. DJ01: the kit serializer and loader with 9, 12, 15 on every track's three WAVE words
    want = {5: 9 << 8, 13: 12 << 8, 21: 15 << 8}
    for t in range(16):
        for k, v in want.items():
            dj.w16(kit + SND0 + SSTRIDE * t + SK + 2 * k, v)
    dj.uc.mem_write(SCRATCH, b'\xa5' * 0x2a00)
    r1 = dj.call(KIT_SAVE, [SCRATCH, kit])
    rec = dj.rd(SCRATCH, 0x2a00)
    st = {t: {k: struct.unpack('>H', rec[ST_SND0 + ST_SSTRIDE * t + ST_K + 2 * i:][:2])[0] for k, i in WAVE_K.items()}
          for t in range(16)}
    for t in range(16):
        for k in want:
            dj.w16(kit + SND0 + SSTRIDE * t + SK + 2 * k, 0)
    r2 = dj.call(KIT_LOAD, [kit, SCRATCH])
    back = {t: dj.live_wave(t, kit) for t in range(16)}
    res['dj01_roundtrip'] = {'serializer': r1 & 0xff, 'loader': r2 & 0xff,
                             'stored_words_track1': {k: '0x%04x' % v for k, v in st[0].items()},
                             'every_track_stored_as_set': all(st[t] == want for t in range(16)),
                             'every_track_loaded_back': all(back[t] == want for t in range(16)),
                             'texts': {k: dj.text(v) for k, v in want.items()}}
    res['dj01_roundtrip']['ok'] = (res['dj01_roundtrip']['every_track_stored_as_set']
                                   and res['dj01_roundtrip']['every_track_loaded_back']
                                   and list(res['dj01_roundtrip']['texts'].values()) == ['PMAJ', 'WTON', 'OCTA'])

    # 3. DT01 (stock LFO code): the same record loaded, the words, the texts, a re-save
    dt = Snap(a.dt01)
    dkit = dt.kit()
    dt.uc.mem_write(SCRATCH, rec)
    r3 = dt.call(KIT_LOAD, [dkit, SCRATCH])
    got = {t: dt.live_wave(t, dkit) for t in range(16)}
    dt.uc.mem_write(SCRATCH + 0x3000, b'\xa5' * 0x2a00)
    r4 = dt.call(KIT_SAVE, [SCRATCH + 0x3000, dkit])
    rec2 = dt.rd(SCRATCH + 0x3000, 0x2a00)
    st2 = {t: {k: struct.unpack('>H', rec2[ST_SND0 + ST_SSTRIDE * t + ST_K + 2 * i:][:2])[0] for k, i in WAVE_K.items()}
           for t in range(16)}
    res['dt01_stock_lfo_os'] = {'loader': r3 & 0xff, 'serializer': r4 & 0xff,
                                'track1_words_after_load': {k: '0x%04x' % v for k, v in got[0].items()},
                                'every_track_kept': all(got[t] == want for t in range(16)),
                                'texts': {k: dt.text(v) for k, v in want.items()},
                                'resave_keeps_them': all(st2[t] == want for t in range(16)),
                                'resave_same_sound_records': all(
                                    rec2[ST_SND0 + ST_SSTRIDE * t:ST_SND0 + ST_SSTRIDE * (t + 1)]
                                    == rec[ST_SND0 + ST_SSTRIDE * t:ST_SND0 + ST_SSTRIDE * (t + 1)] for t in range(16))}
    res['dt01_stock_lfo_os']['ok'] = (res['dt01_stock_lfo_os']['every_track_kept']
                                      and res['dt01_stock_lfo_os']['resave_keeps_them'])
    res['ok'] = all(res[k]['ok'] for k in ('panel', 'dj01_roundtrip', 'dt01_stock_lfo_os'))
    json.dump(res, open(a.json, 'w'), indent=1)
    print(json.dumps(res, indent=1)[:4000])
    print('ALL OK' if res['ok'] else 'SOME CHECK FAILED')


if __name__ == '__main__':
    main()
