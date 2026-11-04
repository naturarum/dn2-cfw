#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""TS01: scripts/t19_live.py with a log of the parameter mirror, block by block (notes/T2.9-trig-slides.md).

    cd vendor/digikit && DT2_SECTIONS=... uv run python ../../scripts/ts01_live.py SNAP --syx TS01.syx \\
        --identify-as STOCK.syx --map TS01-main.syx.map.json --slide-log LOG [--slide-track T] \\
        [t19_live / emu.live options]

At the instruction after the block handler's call of ts_block (0x40027184: the slides written, the smoothing done),
appends one line a block to LOG: the audio clock, the track's held voices (those whose record at
0x40288306 + 0x14 v names it, with a note), trig-slide's state for the track (ts_sl[t]: on, t0, t1, step, n; ts_exact[t]'s generation),
then every word of the parameter mirror (0x80003b12 + 0xca r + 2k, k 0..100) that changed since the last block in a
row the track owns (0x80005308 + 4r), as r:k=value. Read-only on digikit.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.getcwd())                      # vendor/digikit
import t19_live                                      # noqa: E402

AFTER_TS_BLOCK = 0x40027184
# struct ts_slide and ts_pub as the unit's compiler lays them out (ints 2-aligned), from the map: each a 16-array


class Logged(t19_live.Patched):
    log = None
    track = 0
    debug = False
    syms = {}

    def _setup(self):
        r = super()._setup()
        m = r[0]
        from unicorn import UC_HOOK_CODE
        out = open(self.log, 'w')
        t = self.track
        PUB_SIZE = abs(self.syms['ts_exact'] - self.syms['ts_next']) // 16
        SLIDE_SIZE = abs(self.syms['ts_next'] - self.syms['ts_sl']) // 16
        REC_SIZE = (PUB_SIZE - 8) // 2
        sl = self.syms['ts_sl'] + SLIDE_SIZE * t
        ex = self.syms['ts_exact'] + PUB_SIZE * t
        prev = {}
        probe = {}

        def rd(uc, a, n=4):
            return int.from_bytes(bytes(uc.mem_read(a, n)), 'big')

        def cb(uc, addr, size, data):
            clock = rd(uc, 0x80005398)
            vt = bytes(uc.mem_read(0x40288306, 0x14 * 16))        # the voices' track and note bytes
            held = sum(1 << v for v in range(16) if vt[0x14 * v] == t and vt[0x14 * v + 1] < 0x80)
            on, t0, t1, step = (rd(uc, sl + 4 * i) for i in range(4))
            n = rd(uc, sl + 24)
            gen = rd(uc, ex + 4)
            ch = []
            for row in range(16):
                if rd(uc, 0x80005308 + 4 * row) != t:
                    continue
                raw = bytes(uc.mem_read(0x80003b12 + 0xca * row, 0xca))
                for k in range(101):
                    v = int.from_bytes(raw[2 * k:2 * k + 2], 'big', signed=True)
                    if prev.get((row, k)) != v:
                        prev[(row, k)] = v
                        ch.append('%d:%d=%d' % (row, k, v))
            if self.debug:
                pat = rd(uc, 0x42c5b9a0)
                st_ = rd(uc, 0x42c5b964 + t, 1)
                if st_ != probe.get('step'):
                    probe['step'] = st_
                    ch.append('PROBE pat=%08x len1dd54=%d mode1dd59=%d tlen48c=%d tspeed=%d gspeed=%d tick=%d '
                              'step=%d ticks=%s held=%08x tmask=%08x' % (
                                  pat, rd(uc, pat + 0x1dd54, 2), rd(uc, pat + 0x1dd59, 1),
                                  rd(uc, pat + 0x6a5 * t + 0x48c, 2), rd(uc, 0x42c5b9d9 + t, 1),
                                  rd(uc, 0x42c5b9d8, 1), rd(uc, 0x42c5b974 + t, 1), st_,
                                  ','.join(str(rd(uc, 0x401f6f18 + 4 * i)) for i in range(10)),
                                  rd(uc, 0x4059db08), rd(uc, 0x40288844 + 0x40c * t)))

                def rec(pub):
                    ix = rd(uc, pub)
                    r0 = pub + 8 + REC_SIZE * ix
                    st = rd(uc, r0 + 4)
                    return '%d/%08x/%d' % (rd(uc, pub + 4), rd(uc, r0), st - (1 << 32 if st >> 31 else 0))
                vt = bytes(uc.mem_read(0x40288306, 0x14 * 16))
                voices = ''.join('%x' % v for v in range(16) if vt[0x14 * v] == t)
                ch.insert(0, 'ex=%s nx=%s v=%s gate=%s' % (
                    rec(ex), rec(self.syms['ts_next'] + PUB_SIZE * t), voices or '-',
                    ','.join('%x' % rd(uc, 0x80005220 + 4 * v) for v in range(16) if vt[0x14 * v] == t) or '-'))
            out.write('%08x %04x %d %08x %08x %d %d %d %s\n' % (
                clock, held, on, t0, t1, step - (1 << 32 if step >> 31 else 0), n, gen, ' '.join(ch)))

        m.uc.hook_add(UC_HOOK_CODE, cb, begin=AFTER_TS_BLOCK, end=AFTER_TS_BLOCK)
        return r


def main():
    argv = sys.argv[1:]
    Logged.log = t19_live.pop_opt(argv, '--slide-log')
    Logged.track = int(t19_live.pop_opt(argv, '--slide-track') or 0)
    if '--slide-debug' in argv:
        argv.remove('--slide-debug')
        Logged.debug = True
    mp = t19_live.pop_opt(argv, '--map')
    Logged.syms = {k: int(v, 16) for k, v in json.load(open(mp)).items()}
    t19_live.Patched = Logged
    sys.argv = [sys.argv[0]] + argv
    return t19_live.main()


if __name__ == '__main__':
    sys.exit(main())
