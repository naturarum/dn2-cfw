#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""T6.3 CMP3 (final, 2026-10-21; notes/T6.3-cmp3.md): the checks that changed. CMP3 = CMP3A (the first CMP3 of
2026-10-18, frozen in src/cmp3a/, out/T6.3/superseded/CMP3A-dsp) on CP2F's layout (glue and cmp_tail.c in code4 from
0x2e2900, mbcomp.c in code3, code2 the tape delay alone) with band gain-reduction meters in dB (src/cmp_glue.c
cmp_mtr) in reply half-words 8..10.

    python3 tools/dspsplice/verify_cmp3m.py [--quick] [--only a,b,c,d]

(a) audio and every word but the meters: CMP3 against CMP3A, the two images in lockstep on the same input, frames
    and tails, on CMP3A's own verify settings (verify_cmp3.py (z) invalid tails, (1) three MIX 0, (2) three equal MIX
    and the relock, (3) one band wet, (5) the guard and re-init injections, (d) the cost cases, (s) the seeded old
    kits; verify_cmp.py CMP3 (h) zero tails, the fallback): every handler output sample bit for bit, each band's gain
    reduction after every block, both reply pages every block but word 0 (the handler's count) and half-words 8..10
    (the meters; half-word 11 compared), and at the end of each case L1 word for word (CmpSlot but its two meter
    copies word10/word14, CmpTail, the flanger and tape state, the stock state with 0x257c1c, the master overdrive)
    outside our code, data, stack and CmpMeter. Instructions per block next to CMP3A's.
(b) the meters: (b1) ballistics on tone bursts in each band (fast and slow release): every block the reply half-words
    8..10 of both pages = this script's mirror of cmp_mtr fed the band's y2 (within 1), and the state rule checked
    step by step on the VM's own state; attack in the block the gain reduction peaks, a hold of 750 blocks, then 20 dB/s
    (or the band's own release where slower); (b2) the guide's starting point (THR 64, RAT 4.00, ATK 24, REL 32, MIX 127,
    196 Hz / 2.6 kHz) on drums at three levels: the bars per band (px = q // 655, comp-ui's law); RAT 1.00: no bar
    at all; (b3) a corrupted meter state (NaN, inf, -5, 1e9, hold -1, hold 2^31-1): the next block's half-words in
    range, the audio unchanged (bit for bit against the same run without the corruption).
(c) stores of the compressor call: only CmpSlot, CmpTail, CmpMeter (32 B), stack, save area, outputs, 0x257c1c, reply
    +0x10..+0x17.
(d) stack depth (painted) and instructions per block, CMP3 next to CMP3A, typical and worst.
Results: out/T6.3/CMP3-dsp/verify3m.json (or verify3m-<only>.json).
"""
import argparse
import array
import gc
import json
import math
import random
import struct
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(HERE))
import verify_cmp3 as V3  # noqa: E402   (configures verify_cmp for CMP3; verify_cmp2's tail helpers)
import memguard  # noqa: E402

VC, V2, V = V3.VC, V3.V2, V3.V
SLOT, TST, OFF, SR = V3.SLOT, V3.TST, V3.OFF, V3.SR
TailRun, tail_words, knobs17, sched3, list3, words3 = V3.TailRun, V3.tail_words, V3.knobs17, V3.sched3, V3.list3, V3.words3
DEFAULTS, DISTINCT, LOW_ONLY = V3.DEFAULTS, V3.DISTINCT, V3.LOW_ONLY
OUT = ROOT / "out/T6.3/CMP3-dsp"
METER = 0x2e2800
METER_BYTES = 32
HOLD, FALL, MAXDB, QDB = 750, VC.f32(0.013333334), 25.0, 1310.0
START = ([(64, 4, 24, 32, 0)] * 3, (68, 44))          # FLASH-CMP2.md's starting point (RAT 4.00 = step 4), MUP 0


def fbits(xs):
    return array.array("f", xs).tobytes()


def meter_state(vm):
    w = vm.read(METER, METER_BYTES)
    out = []
    for b in range(3):
        m, h = struct.unpack_from("<fi", w, 8 * b)
        out.append((m, h))
    return out


def hw(page_bytes):
    """half-words 8..11 of a reply page"""
    return struct.unpack_from("<4H", page_bytes, 0x10)


def mirror(state, gr_db):
    """cmp_mtr in float32: state (m, hold), gr_db = the slot's gr copy (y x 6.0206, <= 0) -> (state, q)"""
    f = VC.f32
    g = -gr_db
    if not VC.finite(g):
        g = 0.0
    if g < 0.0:
        g = 0.0
    if g > MAXDB:
        g = MAXDB
    m, h = state
    if not VC.finite(m):
        m = 0.0
    if m < 0.0:
        m = 0.0
    if m > MAXDB:
        m = MAXDB
    if h < 0 or h > HOLD:
        h = 0
    if g >= m:
        m, h = g, HOLD
    elif h > 0:
        h -= 1
    else:
        m = f(m - FALL)
        if m < g:
            m = g
    x = f(f(m * QDB) + 0.5)
    q = min(max(int(x), 0), 32767)
    return (m, h), q


# ---------------------------------------------------------------- (a) lockstep CMP3A / CMP3

class Pair:
    """CMP3A (a) and CMP3 (b) on the same blocks; every block: outputs, band gain reductions, reply pages (but word 0
    and half-words 8..10)"""

    def __init__(self, imgs, words, raw, sched):
        self.a = TailRun(V.candidate_vm(imgs["CMP3A"][0], cache=imgs["CMP3A"][2]), words, raw, sched)
        self.b = TailRun(V.candidate_vm(imgs["CMP3"][0], cache=imgs["CMP3"][2]), words, raw, sched)
        self.nb = self.samples = self.same = self.gr_same = self.pages_same = 0
        self.hw11_same = 0
        self.page_offsets = set()
        self.peak = 0.0

    def block(self, L, R, b, each=None, inject=None):
        if each:
            each(b, self.a.r.d.frame)
            each(b, self.b.r.d.frame)
        self.a.tblock(L, R, b, inject=inject, tx_full=True)
        self.b.tblock(L, R, b, inject=inject, tx_full=True)
        oa = self.a.out[0] + self.a.out[1]
        ob = self.b.out[0] + self.b.out[1]
        for r in (self.a, self.b):
            r.out[0].clear()
            r.out[1].clear()
        self.samples += len(oa)
        if fbits(oa) == fbits(ob):
            self.same += len(oa)
        else:
            self.same += sum(1 for x, y in zip(oa, ob) if struct.pack("<f", x) == struct.pack("<f", y))
        self.peak = max(self.peak, max((abs(x) for x in ob if x == x), default=0.0))
        ga, gb = self.a.gr.pop(), self.b.gr.pop()
        self.gr_same += fbits(ga) == fbits(gb)
        pa, pb = self.a.tx_pages.pop(), self.b.tx_pages.pop()
        ma, mb = bytearray(pa), bytearray(pb)
        for k in (0, 0x1000):
            ma[k:k + 4] = mb[k:k + 4] = bytes(4)
            ma[k + 0x10:k + 0x16] = mb[k + 0x10:k + 0x16] = bytes(6)
        if ma == mb:
            self.pages_same += 1
        else:
            for i in range(0, len(ma), 2):
                if ma[i:i + 2] != mb[i:i + 2]:
                    self.page_offsets.add(i)
        self.hw11_same += (pa[0x16:0x18] == pb[0x16:0x18] and pa[0x1016:0x1018] == pb[0x1016:0x1018])
        self.nb += 1

    def run(self, L, R, each=None, inject=None):
        for b in range(len(L) // 32):
            self.block(L[32 * b:32 * b + 32], R[32 * b:32 * b + 32], b, each=each, inject=inject)
        return self.finish()

    def finish(self):
        va, vb = self.a.vm, self.b.vm
        ignore = [(0x2dd600, 0x2e1c40, "our block-1 code"), (0x2e2800, 0x2e7000, "code4 / CmpMeter / data / save area"),
                  (0x308400, 0x318000, "code3, code2"), (V.STACK_LO, V.STACK_TOP, "stack"),
                  (VC.TX[0], VC.TX[0] + 0x1000, "reply page 0 (per block)"), (VC.TX[1], VC.TX[1] + 0x1000, "reply page 1"),
                  (SLOT + OFF["word10"], SLOT + OFF["word10"] + 8, "CmpSlot word10/word14 (the meter words sent)")]
        mem = []
        for lo, hi in ((0x240000, 0x2f0000), (0x300000, 0x320000)):
            x, y = va.read(lo, hi - lo), vb.read(lo, hi - lo)
            if x != y:
                for i in range(0, hi - lo, 4):
                    ad = lo + i
                    if x[i:i + 4] != y[i:i + 4] and not any(p <= ad < q for p, q, _ in ignore):
                        mem.append(ad)
        pa, pb = self.a.r.per_block, self.b.r.per_block
        r = {"blocks": self.nb, "samples": self.samples, "samples_identical": self.same,
             "gain_reduction_blocks_identical": self.gr_same, "reply_pages_identical_but_word0_and_hw8_10": self.pages_same,
             "reply_byte_offsets_that_differ": sorted("%#x" % o for o in self.page_offsets),
             "half_word_11_identical_blocks": self.hw11_same,
             "l1_words_that_differ_outside_ours": ["%#x" % a for a in mem[:20]], "l1_count": len(mem),
             "trips_reinits_CMP3A_CMP3": [[v.rd32(SLOT + OFF["trips"]), v.rd32(SLOT + OFF["reinits"])] for v in (va, vb)],
             "insns_mean_CMP3A_CMP3": [round(sum(pa[1:]) / max(1, len(pa) - 1)), round(sum(pb[1:]) / max(1, len(pb) - 1))],
             "insns_max_CMP3A_CMP3": [max(pa[1:] or [0]), max(pb[1:] or [0])], "peak": round(self.peak, 4)}
        r["ok"] = (self.same == self.samples and self.gr_same == self.nb and self.pages_same == self.nb
                   and self.hw11_same == self.nb and not mem)
        return r


def proof_a(imgs, q):
    t_all = time.time()
    res, ok_all = {}, True

    def done(group, name, r, t0):
        nonlocal ok_all
        r["seconds"] = round(time.time() - t0, 1)
        res.setdefault(group, {})[name] = r
        ok_all = ok_all and r["ok"]
        print("(a) %-4s %-92s %s %d/%d samples, %d blocks, insns %s, %.0fs, rss %d MB" % (
            group, name[:92], "ok" if r["ok"] else "DIFFERS", r["samples_identical"], r["samples"], r["blocks"],
            r["insns_mean_CMP3A_CMP3"], r["seconds"], memguard.peak_rss_mb()), flush=True)
        if not r["ok"]:
            print("      ", {k: v for k, v in r.items() if k not in ("seconds",)}, flush=True)
        memguard.check()
        gc.collect()

    moving17 = lambda b: [20 + 4 * (b % 2), 3 + b % 2, 10 + 4 * (b % 2), 40 + 4 * (b % 2), 50 + 4 * (b % 2), 60 + 4 * (b % 2),   # noqa: E731
                          60 - 4 * (b % 2), 6 - b % 2, 40 + 4 * (b % 2), 60 + 4 * (b % 2), 30 + 4 * (b % 2), 50 + 4 * (b % 2),
                          90 - 4 * (b % 2), 8 - b % 2, 80 + 4 * (b % 2), 100 + 4 * (b % 2), 90 - 4 * (b % 2)]
    mixes_apart = lambda b: [0 if ((b + 13 * k) // 40) % 2 else (40, 64, 100)[k] for k in range(3)]   # noqa: E731

    # (h) zero tails, the fallback (verify_cmp.py CMP3 (h): 11 configurations)
    nb = 2000 if not q else 200
    prog = VC.looped(max(nb, 3300 if not q else 700))
    hcfg = {
        "MIX 0, stock new-kit defaults, self, FX on": (dict(VC.BASE, **VC.FXW, **VC.comp(32, 24, 32, 64, 3, 0)), VC.ROUTE_INFX, None, nb),
        "MIX 39 (typical), self, FX on": (dict(VC.BASE, **VC.FXW, **VC.TYPICAL), VC.ROUTE_INFX, None, nb),
        "MIX 64, heavy (20:1 fast, MUP 64), self": (dict(VC.BASE, **VC.NOFX, **VC.HEAVY), VC.ROUTE_IN, None, nb),
        "MIX 127, C1 (-20 dB 2:1), self, FX on": (dict(VC.BASE, **VC.FXW, **VC.C1), VC.ROUTE_INFX, None, nb),
        "MIX 127, -60 dB 20:1 MUP 127, self": (dict(VC.BASE, **VC.NOFX, **VC.comp(0, 0, 0, 127, 7, 127)), VC.ROUTE_IN, None, nb),
        "MIX 64, key SCS 18, FX on": (dict(VC.BASE, **VC.FXW, **VC.comp(40, 10, 30, 30, 5, 64, scs=18)), VC.ROUTE_INFX, None, nb),
        "MIX 127, key SCS 18, FX on": (dict(VC.BASE, **VC.FXW, **VC.comp(20, 5, 20, 64, 6, 127, scs=18)), VC.ROUTE_INFX, None, nb),
        "W4 (every knob moving every block), self, FX on": (dict(VC.BASE, **VC.FXW, **VC.W3_SELF), VC.ROUTE_INFX, VC.w4_moves, nb),
        "W5 (W4 + RAT, MIX 0 <-> 64 every 40 blocks), self, FX on": (dict(VC.BASE, **VC.FXW, **VC.W3_SELF), VC.ROUTE_INFX, VC.w5_moves, nb),
        "kit changes, MIX 0 <-> >0 (review schedule), self, FX on": (dict(VC.BASE, **VC.FXW, **VC.comp(32, 24, 32, 64, 3, 0)),
                                                                    VC.ROUTE_INFX, VC.kit_changes, len(prog[0]) // 32),
        "kit changes, MIX 0 <-> >0 (review schedule), key SCS 18, FX on": (
            dict(VC.BASE, **VC.FXW, **VC.comp(32, 24, 32, 64, 3, 0, scs=18)), VC.ROUTE_INFX, VC.kit_changes, len(prog[0]) // 32),
    }
    for name, (words, raw, each, m) in hcfg.items():
        t0 = time.time()
        done("h", name, Pair(imgs, words, raw, None).run(prog[0][:32 * m], prog[1][:32 * m], each=each), t0)

    # (z) invalid tails (verify_cmp3.py (z))
    nb = 1000 if not q else 200
    rnd = random.Random(11)
    NW = V3.NW

    def bad_tail(b):
        k = b % 6
        if k == 0:
            return [rnd.randrange(0x10000) for _ in range(NW)]
        if k == 1:
            return [V2.MAGIC] + [rnd.randrange(0x10000) for _ in range(NW - 1)]
        if k == 2:
            w = tail_words(rnd.randrange(0x10000), [rnd.randrange(0x7f01) for _ in range(27)])
            w[rnd.randrange(NW)] ^= 1 << rnd.randrange(16)
            return w
        if k == 3:
            return [0xffff] * NW
        if k == 4:
            w = tail_words(b, [0x4000] * 27)
            w[0] = 0xC3B2
            w[29] = (~sum(w[:29])) & 0xffff
            return w
        return [0] * NW
    prog = VC.looped(nb)
    zcfg = {
        "typical (defaults + MIX 39), FX on": (dict(VC.BASE, **VC.FXW, **VC.TYPICAL), VC.ROUTE_INFX, None),
        "heavy (20:1 fast, MUP 64, MIX 64)": (dict(VC.BASE, **VC.NOFX, **VC.HEAVY), VC.ROUTE_IN, None),
        "key SCS 18, MIX 127, FX on": (dict(VC.BASE, **VC.FXW, **VC.comp(20, 5, 20, 64, 6, 127, scs=18)), VC.ROUTE_INFX, None),
        "W5 (every stock knob moving, MIX 0 <-> 64), FX on": (dict(VC.BASE, **VC.FXW, **VC.W3_SELF), VC.ROUTE_INFX, VC.w5_moves),
    }
    for name, (words, raw, each) in zcfg.items():
        t0 = time.time()
        rnd.seed(hash(name) & 0xffff)
        tails = [bad_tail(b) for b in range(nb)]
        done("z", name, Pair(imgs, words, raw, lambda b, tails=tails: tails[b]).run(prog[0][:nb * 32], prog[1][:nb * 32], each=each), t0)

    # (1) the three MIX 0 in tail mode
    nb = 2000 if not q else 300
    prog = VC.looped(nb)
    L, R = prog[0][:nb * 32], prog[1][:nb * 32]
    for name, sched, words in (
            ("three distinct bands, the three MIX 0, self, FX on", sched3(DISTINCT, (0, 0, 0)),
             dict(VC.BASE, **VC.FXW, **VC.comp(32, 24, 32, 64, 3, 0))),
            ("every band knob and both crossovers moving every block, the three MIX 0, key SCS 18, FX on",
             list3([(moving17(b), (0, 0, 0)) for b in range(nb)]), dict(VC.BASE, **VC.FXW, **VC.comp(32, 24, 32, 64, 3, 0, scs=18))),
            ("the stock MIX (k 67) at 100, ignored in tail mode: the three band MIX 0, distinct bands",
             sched3(DISTINCT, (0, 0, 0)), dict(VC.BASE, **VC.FXW, **VC.comp(32, 24, 32, 64, 3, 100)))):
        t0 = time.time()
        done("1", name, Pair(imgs, words, VC.ROUTE_INFX, sched).run(L, R), t0)

    # (2) the three MIX equal, and three coming to the same value (the relock)
    nb = 1500 if not q else 300
    prog = VC.looped(nb)
    L, R = prog[0][:nb * 32], prog[1][:nb * 32]

    def mix_moves(b, fr):
        VC.put(fr, "CMP_MIX", (0 if (b // 40) % 2 else 64) << 8)
    for name, setting, m, base, raw, each in [
            ("m 39, band defaults, self, FX on", DEFAULTS, 39, dict(VC.BASE, **VC.FXW), VC.ROUTE_INFX, None),
            ("m 64, three distinct bands, self, FX on", DISTINCT, 64, dict(VC.BASE, **VC.FXW), VC.ROUTE_INFX, None),
            ("m 127, three distinct bands, key SCS 18, FX on", DISTINCT, 127, dict(VC.BASE, **VC.FXW), VC.ROUTE_INFX, None),
            ("m 1, LOW only", LOW_ONLY, 1, dict(VC.BASE, **VC.NOFX), VC.ROUTE_IN, None),
            ("m 127, heavy (20:1 fast, MUP 127 per band: the wet clip at +-1.0)", ([(0, 8, 0, 0, 127)] * 3, (68, 44)), 127,
             dict(VC.BASE, **VC.NOFX), VC.ROUTE_IN, None),
            ("m moving 0 <-> 64 every 40 blocks for all three, distinct bands", DISTINCT, None, dict(VC.BASE, **VC.FXW),
             VC.ROUTE_INFX, mix_moves)]:
        t0 = time.time()
        words = dict(base, **VC.comp(32, 24, 32, 64, 3, m if m is not None else 64, scs=18 if "key" in name else 0))
        if m is None:
            sch = list3([(knobs17(setting), [0 if (max(b - 1, 0) // 40) % 2 else 64] * 3) for b in range(nb)])
        else:
            sch = sched3(setting, (m, m, m))
        done("2", name, Pair(imgs, words, raw, sch).run(L, R, each=each), t0)
    nb2 = 1500 if not q else 600
    prog2 = VC.looped(nb2)
    t0 = time.time()
    sch = list3([(knobs17(DISTINCT), (0, 64, 127) if b < 300 else (64, 64, 64)) for b in range(nb2)])
    done("2", "three MIX coming to 64 from 0 / 64 / 127 (block 300): the relock",
         Pair(imgs, dict(VC.BASE, **VC.FXW, **VC.comp(32, 24, 32, 64, 3, 64)), VC.ROUTE_INFX, sch).run(
             prog2[0][:nb2 * 32], prog2[1][:nb2 * 32]), t0)

    # (3) one band at 127, the others at 0 (the three runs of verify_cmp3.py (3) per band)
    nb = 1500 if not q else 300
    prog = VC.looped(nb)
    L, R = prog[0][:nb * 32], prog[1][:nb * 32]
    words = dict(VC.BASE, **VC.FXW, **VC.comp(32, 24, 32, 64, 3, 0))
    heavy, alt = (10, 8, 0, 20, 64), (60, 6, 40, 60, 30)
    for band in range(3):
        mixes = [0, 0, 0]
        mixes[band] = 127
        for tag, others, mx in (("a", alt, mixes), ("b", (90, 8, 0, 0, 127), mixes), ("u", V3.UNITY, (127, 127, 127))):
            t0 = time.time()
            bands = [heavy if b == band else others for b in range(3)]
            done("3", "%s at 127 (%s)" % (("LOW", "MID", "HIGH")[band], tag),
                 Pair(imgs, words, VC.ROUTE_INFX, sched3((bands, (68, 44)), mx)).run(L, R), t0)

    # (5) the guard and the quiet re-init with the pairs apart
    Ld, Rd = VC.load_input("drums", 300 * 32 / SR)
    words = dict(VC.BASE, **VC.NOFX, **VC.comp(32, 24, 32, 64, 3, 64))
    vals = words3(knobs17(DISTINCT), (20, 64, 127))
    for label in ("NaN in comp in L at block 60", "inf in comp in R at block 60", "corrupted magic at block 60 (quiet re-init)",
                  "MID's wet gain 2.0 at block 60 (range test)", "HIGH's dry gain NaN at block 60",
                  "LOW's MIX target 7.0 at block 60 (range test)"):
        def inj(b, vm_, ptrs, label=label):
            if b != 60:
                return
            if label.startswith("NaN"):
                vm_.wr32(ptrs[0] + 4 * 5, 0x7fc00000)
            elif label.startswith("inf"):
                vm_.wr32(ptrs[1] + 4 * 9, 0x7f800000)
            elif "magic" in label:
                vm_.wr32(SLOT + OFF["magic"], 0x12345678)
            elif "MID's wet" in label:
                vm_.wrf(SLOT + OFF["mb.band[1].mw"], [2.0])
            elif "HIGH's dry" in label:
                vm_.wr32(SLOT + OFF["mb.band[2].md"], 0x7fc00000)
            else:
                vm_.wrf(SLOT + OFF["mb.band[0].t_mw"], [7.0])
        t0 = time.time()
        done("5", label, Pair(imgs, words, VC.ROUTE_IN, lambda b: tail_words(1 + b, vals)).run(Ld, Rd, inject=inj), t0)
    t0 = time.time()
    done("5", "every tail value 0xffff (valid tail): clamped, the mixes 127",
         Pair(imgs, dict(VC.BASE, **VC.NOFX, **VC.comp(32, 24, 32, 64, 3, 0)), VC.ROUTE_IN,
              V2.const_sched([0xffff] * 27)).run(Ld[:200 * 32], Rd[:200 * 32]), t0)

    # (d) the cost cases (verify_cmp3.py (d)), noise and FX jumping included
    nb = 1500 if not q else 300
    mixl = VC.load_input("mix", nb * 32 / SR)
    rn = random.Random(7)
    noise = ([rn.uniform(-1, 1) for _ in range(nb * 32)], [rn.uniform(-1, 1) for _ in range(nb * 32)])
    knames = list(VC.FL.KNOBS) + ["DEL_TIME", "DEL_WID", "DEL_FDBK", "DEL_HPF", "DEL_LPF"]
    loudfx = dict(CHR_DEL=127, CHR_REV=127, DEL_REV=127, REV_VOL=100)
    jumps = {}

    def fx_jump(b, fr):
        # the same values for both images: drawn once per block, then replayed
        if b % 20 == 0:
            if b not in jumps:
                jumps[b] = [rn.choice([0, 127, rn.randrange(128)]) for _ in knames] + [rn.randrange(2)]
            for k, v in zip(knames, jumps[b]):
                VC.put(fr, k, v << 8)
            VC.put(fr, "DEL_X", jumps[b][-1] << 8)
    worst = list3([(moving17(b), mixes_apart(b)) for b in range(nb)])
    for name, (words, raw, inp, sched, each) in {
            "typical: band defaults, the three MIX 39": (dict(VC.BASE, **VC.FXW, **VC.TYPICAL), VC.ROUTE_INFX, mixl,
                                                         sched3(DEFAULTS, (39, 39, 39)), None),
            "band defaults, MIX 20 / 39 / 64 (apart)": (dict(VC.BASE, **VC.FXW, **VC.TYPICAL), VC.ROUTE_INFX, mixl,
                                                        sched3(DEFAULTS, (20, 39, 64)), None),
            "worst: every band knob, both crossovers and each MIX moving (through 0 apart), noise, FX jumping, self": (
                dict(dict(VC.BASE, **VC.FXW, **VC.W3_SELF), **loudfx), VC.ROUTE_INFX, noise, worst, fx_jump),
            "worst, key (SCS 18)": (dict(dict(VC.BASE, **VC.FXW, **VC.W3), **loudfx), VC.ROUTE_INFX, noise, worst, fx_jump)}.items():
        t0 = time.time()
        jumps.clear()
        done("d", name, Pair(imgs, words, raw, sched).run(inp[0], inp[1], each=each), t0)

    # (s) old kits seeded (verify_cmp3.py (s)): drums and mix, four kits, tail mode, against CMP3A
    secs = 6.0 if not q else 1.0
    mats = {"drums": VC.load_input("drums", secs), "mix": VC.load_input("mix", secs)}
    kits = {"defaults, MIX 39": (32, 24, 32, 64, 3, 39), "C1: -20 dB 2:1 10/151 ms, MIX 127": (85, 40, 44, 0, 1, 127),
            "heavy: 20:1 fast, MUP 64, MIX 64": (10, 0, 0, 64, 7, 64), "MIX 0": (32, 24, 32, 64, 3, 0)}
    for mname, (Lm, Rm) in mats.items():
        for kname, (thr, atk, rel, mup, rat, mix) in kits.items():
            t0 = time.time()
            words = dict(VC.BASE, **VC.NOFX, **VC.comp(thr, atk, rel, mup, rat, mix))
            seed = ([(thr, rat + 1, atk, rel, mup)] * 3, (0, 127))
            done("s", "%s, %s" % (mname, kname), Pair(imgs, words, VC.ROUTE_IN, sched3(seed, (mix, mix, mix))).run(Lm, Rm), t0)

    res["ok"] = ok_all
    res["seconds"] = round(time.time() - t_all)
    tot = [0, 0, 0]
    for g, d in res.items():
        if isinstance(d, dict):
            for r in d.values():
                tot[0] += r["blocks"]
                tot[1] += r["samples"]
                tot[2] += r["samples_identical"]
    res["totals_blocks_samples_identical"] = tot
    print("(a)", ok_all, tot, "%.0fs" % res["seconds"], flush=True)
    return ok_all, res


# ---------------------------------------------------------------- (b) the meters

def tone(f, n, a):
    return [a * math.sin(2 * math.pi * f * i / SR) for i in range(n)]


def meter_run(img, words, raw, sched, L, R, inject=None, check=True):
    """CMP3 alone: per block the band gain reductions (dB), the meter state, the reply half-words of both pages; the
    mirror and the state rule checked every block"""
    run = TailRun(V.candidate_vm(img[0], cache=img[2]), words, raw, sched)
    vm = run.vm
    st = [(0.0, 0), (0.0, 0), (0.0, 0)]
    prev = meter_state(vm)
    rows, bad, maxdq = [], [], 0
    for b in range(len(L) // 32):
        run.tblock(L[32 * b:32 * b + 32], R[32 * b:32 * b + 32], b, inject=inject, tx_full=True)
        pages = run.tx_pages.pop()
        run.out[0].clear()
        run.out[1].clear()
        gr = run.gr[-1]
        cur = meter_state(vm)
        h0, h1 = hw(pages[:0x20]), hw(pages[0x1000:0x1020])
        qs = []
        for k in range(3):
            if inject is not None and b == getattr(inject, "at", -1):
                st[k] = prev[k]                      # the mirror starts from the state as corrupted
            st[k], qm = mirror(st[k], gr[k])
            qs.append(h0[k])
            maxdq = max(maxdq, abs(qm - h0[k]))
            if check:
                # the state rule on the VM's own state (prev -> cur), the gain reduction g = -gr
                g = -gr[k] if VC.finite(gr[k]) else 0.0
                g = min(max(g, 0.0), MAXDB)
                pm, ph = prev[k]
                pm = pm if VC.finite(pm) else 0.0
                pm = min(max(pm, 0.0), MAXDB)
                ph = ph if 0 <= ph <= HOLD else 0
                m, h = cur[k]
                if g >= pm:
                    okr = m == g and h == HOLD
                elif ph > 0:
                    okr = m == pm and h == ph - 1
                else:
                    okr = h == 0 and (m == g if VC.f32(pm - FALL) <= g else abs(m - (pm - FALL)) <= 2e-6 * max(1.0, pm))
                okq = abs(h0[k] - int(VC.f32(VC.f32(m * QDB) + 0.5))) <= 0 and h0[k] == h1[k]
                if not (okr and okq) and len(bad) < 10:
                    bad.append({"block": b, "band": k, "g": g, "prev": prev[k], "cur": cur[k], "q": [h0[k], h1[k]]})
        rows.append({"gr": [-x for x in gr], "m": [c[0] for c in cur], "hold": [c[1] for c in cur], "q": qs, "hw11": [h0[3], h1[3]]})
        prev = cur
    return rows, bad, maxdq, run


def bar_stats(rows, k):
    px = [r["q"][k] // 655 for r in rows]
    s = sorted(px)
    n = len(s)
    return {"max_px": s[-1], "median_px": s[n // 2], "p90_px": s[int(0.9 * (n - 1))],
            "blocks_with_bar_pct": round(100.0 * sum(1 for p in px if p > 0) / n, 1),
            "deepest_gr_db": round(max(r["gr"][k] for r in rows), 2),
            "median_gr_db": round(sorted(r["gr"][k] for r in rows)[n // 2], 2)}


def proof_b(imgs, q):
    t_all = time.time()
    img = imgs["CMP3"]
    res, oks = {}, []

    # (b1) ballistics: a tone burst in each band, 300 blocks on then silence; fast release (REL 0) and slow (REL 127)
    nb = 3000 if not q else 2400
    b1 = {}
    for band, f in ((0, 80.0), (1, 1000.0), (2, 8000.0)):
        for rel in (0, 127):
            bands = [(64, 0, 0, 32, 0)] * 3
            bands = [list(x) for x in bands]
            bands[band] = [64, 4, 0, rel, 0]           # THR 64 (-29.8 dB), RAT 4.00, ATK 0
            setting = ([tuple(x) for x in bands], (68, 44))
            x = tone(f, 300 * 32, 0.5) + [0.0] * ((nb - 300) * 32)
            rows, bad, maxdq, run = meter_run(img, dict(VC.BASE, **VC.NOFX, **VC.comp(32, 24, 32, 64, 3, 127)), VC.ROUTE_IN,
                                              sched3(setting, (127, 127, 127)), x, list(x))
            ms = [r["m"][band] for r in rows]
            grs = [r["gr"][band] for r in rows]
            last_take = max(i for i in range(len(rows)) if i < 600 and rows[i]["hold"][band] == HOLD)
            fall_from = last_take + HOLD + 1
            hold_ok = all(ms[i] == ms[last_take] for i in range(last_take, min(fall_from, len(ms))))
            # the fall: where the meter is above the gain reduction, it drops FALL per block
            falling = [i for i in range(fall_from, len(ms) - 1) if ms[i + 1] > grs[i + 1] + 1e-6 and ms[i] > 0.05]
            slope = ((ms[falling[0]] - ms[falling[-1]]) / (falling[-1] - falling[0]) * 1500.0) if len(falling) > 10 else None
            follows = [i for i in range(fall_from, len(ms)) if abs(ms[i] - grs[i]) < 1e-6 and grs[i] > 0.05]
            zero_at = next((i for i in range(fall_from, len(ms)) if rows[i]["q"][band] == 0), None)
            others_zero = all(r["q"][k] == 0 for r in rows for k in range(3) if k != band)
            e = {"tone_hz": f, "REL": rel, "peak_gr_db": round(max(grs), 3), "meter_at_peak_db": round(max(ms), 3),
                 "attack_same_block": all(ms[i] >= grs[i] - 1e-6 for i in range(len(ms))),
                 "last_new_peak_block": last_take, "hold_blocks": HOLD, "hold_exact": hold_ok,
                 "fall_db_per_s": round(slope, 3) if slope is not None else None,
                 "blocks_where_meter_follows_its_release": len(follows),
                 "bar_gone_at_block": zero_at, "seconds_after_last_peak": round((zero_at - last_take) / 1500.0, 3) if zero_at else None,
                 "other_bands_no_bar": others_zero, "mirror_max_q_diff": maxdq, "state_rule_violations": bad,
                 "px_at_peak": max(r["q"][band] for r in rows) // 655}
            # REL 0: the gain reduction is gone in ~50 ms, so the bar shows the hold, then 20 dB/s down to nothing; REL 127
            # (2 s): the bar falls at 20 dB/s onto the band's own, slower release and follows it
            if rel == 0:
                e["ok"] = (not bad and maxdq <= 1 and hold_ok and e["attack_same_block"] and others_zero and zero_at is not None
                           and slope is not None and abs(slope - 20.0) < 0.05)
            else:
                e["ok"] = (not bad and maxdq <= 1 and hold_ok and e["attack_same_block"] and others_zero
                           and e["blocks_where_meter_follows_its_release"] > 100)
            b1["%s band, %g Hz burst, REL %d" % (("LOW", "MID", "HIGH")[band], f, rel)] = e
            oks.append(e["ok"])
            print("(b1)", band, rel, {k: v for k, v in e.items() if k != "state_rule_violations"}, len(bad), flush=True)
            memguard.check()
    res["b1_ballistics"] = b1

    # (b2) the guide's starting point on drums at three levels; RAT 1.00: no bar
    secs = 6.0 if not q else 2.0
    b2 = {}
    for gain_db in (0.0, -12.0, -24.0):
        L, R = VC.load_input("drums", secs, gain=10 ** (gain_db / 20))
        for name, setting in (("starting point (THR 64, RAT 4.00, ATK 24, REL 32, MUP 0, MIX 127)", START),
                              ("RAT 1.00 on every band (else the starting point)", ([(64, 0, 24, 32, 0)] * 3, (68, 44)))):
            rows, bad, maxdq, run = meter_run(img, dict(VC.BASE, **VC.NOFX, **VC.comp(32, 24, 32, 64, 3, 127)), VC.ROUTE_IN,
                                              sched3(setting, (127, 127, 127)), L, R)
            e = {"drums_gain_db": gain_db, "input_peak_dbfs": round(20 * math.log10(max(abs(x) for x in L + R)), 1),
                 "bands": {("LOW", "MID", "HIGH")[k]: bar_stats(rows, k) for k in range(3)},
                 "mirror_max_q_diff": maxdq, "state_rule_violations": bad,
                 "hw11_deepest_now_max_px_old_law": max(r["hw11"][0] for r in rows) // 655}
            if "RAT 1.00" in name:
                e["no_bar_ever"] = all(r["q"] == [0, 0, 0] for r in rows)
                e["ok"] = e["no_bar_ever"] and not bad
            else:
                e["ok"] = not bad and maxdq <= 1
                if gain_db == 0.0:
                    e["ok"] = e["ok"] and all(e["bands"][k]["max_px"] >= 10 for k in ("LOW", "MID", "HIGH"))
            b2["drums %+g dB: %s" % (gain_db, name)] = e
            oks.append(e["ok"])
            print("(b2)", gain_db, name[:40], {k: (v["max_px"], v["median_px"], v["p90_px"], v["blocks_with_bar_pct"],
                                                   v["deepest_gr_db"]) for k, v in e["bands"].items()},
                  e.get("no_bar_ever"), len(bad), maxdq, flush=True)
            if gain_db == 0.0 and "starting" in name:
                res["b2_timeline_drums_0dB"] = [[i, r["q"], [round(x, 2) for x in r["gr"]]] for i, r in enumerate(rows) if i % 25 == 0]
            memguard.check()
    res["b2_starting_point"] = b2

    # (b3) a corrupted meter state: in range at the next block, the audio unchanged
    b3 = {}
    L, R = VC.load_input("drums", 200 * 32 / SR)
    base = None
    for label, m_bits, h in (("none", None, None), ("m NaN", 0x7fc00000, None), ("m +inf", 0x7f800000, None),
                             ("m -5 dB", struct.unpack("<I", struct.pack("<f", -5.0))[0], None),
                             ("m 1e9 dB", struct.unpack("<I", struct.pack("<f", 1e9))[0], None),
                             ("hold -1", None, -1), ("hold 2^31-1", None, 0x7fffffff)):
        def inj(b, vm_, ptrs, m_bits=m_bits, h=h):
            if b != 100:
                return
            for k in range(3):
                if m_bits is not None:
                    vm_.wr32(METER + 8 * k, m_bits)
                if h is not None:
                    vm_.wr32(METER + 8 * k + 4, h & 0xffffffff)
        inj.at = 100
        run = TailRun(V.candidate_vm(img[0], cache=img[2]), dict(VC.BASE, **VC.NOFX, **VC.comp(32, 24, 32, 64, 3, 127)),
                      VC.ROUTE_IN, sched3(START, (127, 127, 127)))
        qs = []
        for b in range(len(L) // 32):
            run.tblock(L[32 * b:32 * b + 32], R[32 * b:32 * b + 32], b, inject=inj if label != "none" else None, tx_full=True)
            pg = run.tx_pages.pop()
            qs.append(hw(pg[:0x20])[:3])
        out = fbits(run.out[0] + run.out[1])
        if base is None:
            base = (out, qs)
        st = meter_state(run.vm)
        e = {"q_block_100": list(qs[100]), "q_block_101": list(qs[101]),
             "in_range_after": all(0 <= x <= 32750 for qq in qs[100:] for x in qq),
             "audio_bit_identical_to_no_corruption": out == base[0],
             "state_at_end": [[round(m, 3), h_] for m, h_ in st],
             "q_equal_to_no_corruption_from_block": next((b for b in range(100, len(qs)) if all(qs[c] == base[1][c]
                                                                                              for c in range(b, len(qs)))), None)}
        e["ok"] = e["in_range_after"] and e["audio_bit_identical_to_no_corruption"]
        b3[label] = e
        oks.append(e["ok"])
        print("(b3)", label, e, flush=True)
    res["b3_corrupted_meter_state"] = b3
    res["ok"] = all(oks)
    res["seconds"] = round(time.time() - t_all)
    res["law"] = ("q = round(1310 x dB) (comp-ui 0.2: h = q // 655 px, so 2 px per dB, 1 px per 0.5 dB, the CMP box's "
                  "24 px = 12 dB, 50 px = 25 dB); instant attack, hold 750 blocks (0.5 s), then 20 dB/s, never below "
                  "the gain reduction; at most 25 dB")
    print("(b)", res["ok"], "%.0fs" % res["seconds"], flush=True)
    return res["ok"], res


# ---------------------------------------------------------------- (c) stores, (d) stack and cost

def proof_c(imgs, info):
    img = imgs["CMP3"]
    sbytes = info["layout_check"]["slot_sizes_from_image"]["cmp_slot_bytes"]
    tbytes = info["layout_check"]["slot_sizes_from_image"]["cmp_tail_bytes"]
    mbytes = info["layout_check"]["slot_sizes_from_image"]["cmp_meter_bytes"]
    allowed = [(V.STACK_LO, V.STACK_TOP, "stack"), (SLOT, SLOT + sbytes, "CmpSlot"), (TST, TST + tbytes, "CmpTail"),
               (METER, METER + mbytes, "CmpMeter"),
               (0x2e6800, 0x2e6800 + VC.B.stub.SAVE_BYTES, "stub save area"), (0x25ba68, 0x25ba68 + 0x80, "out L"),
               (0x25bae8, 0x25bae8 + 0x80, "out R"), (VC.GR_Y2, VC.GR_Y2 + 4, "0x257c1c meter"),
               (VC.TX[0] + 0x10, VC.TX[0] + 0x18, "reply page 0"), (VC.TX[1] + 0x10, VC.TX[1] + 0x18, "reply page 1")]
    L, R = VC.load_input("mix", 130 * 32 / SR)
    moving17 = lambda b: [20 + 4 * (b % 2), 3 + b % 2, 10 + 4 * (b % 2), 40 + 4 * (b % 2), 50 + 4 * (b % 2), 60 + 4 * (b % 2),   # noqa: E731
                          60 - 4 * (b % 2), 6 - b % 2, 40 + 4 * (b % 2), 60 + 4 * (b % 2), 30 + 4 * (b % 2), 50 + 4 * (b % 2),
                          90 - 4 * (b % 2), 8 - b % 2, 80 + 4 * (b % 2), 100 + 4 * (b % 2), 90 - 4 * (b % 2)]
    mixes_apart = lambda b: [0 if ((b + 13 * k) // 40) % 2 else (40, 64, 100)[k] for k in range(3)]   # noqa: E731
    census = {}
    for label, sched, inj in (
            ("zero tails (the fallback)", None, None),
            ("MIX 20 / 64 / 127, distinct bands", sched3(DISTINCT, (20, 64, 127)), None),
            ("every knob and each MIX moving (through 0 apart)", list3([(moving17(b), mixes_apart(b)) for b in range(130)]), None),
            ("pairs apart, a guard trip (NaN input, block 60)", sched3(DISTINCT, (0, 64, 127)),
             lambda b, vm_, ptrs: vm_.wr32(ptrs[0] + 20, 0x7fc00000) if b == 60 else None),
            ("corrupted magic (re-init, block 60)", sched3(DISTINCT, (0, 64, 127)),
             lambda b, vm_, ptrs: vm_.wr32(SLOT + OFF["magic"], 1) if b == 60 else None)):
        run = TailRun(V.candidate_vm(img[0], cache=img[2]), dict(VC.BASE, **VC.FXW, **VC.comp(32, 24, 32, 64, 3, 64)),
                      VC.ROUTE_INFX, sched)
        log = []
        for b in range(125):
            wl = [] if b in (0, 1, 2, 3, 40, 41, 60, 61, 80, 81, 120, 121) else None
            run.tblock(L[32 * b:32 * b + 32], R[32 * b:32 * b + 32], b, watch=wl, inject=inj)
            if wl is not None:
                log += wl
        stray = [(pc, ad) for pc, ad, va in log if not any(lo <= ad < hi for lo, hi, _ in allowed)]
        by = {}
        for pc, ad, va in log:
            nm = next((n_ for lo, hi, n_ in allowed if lo <= ad < hi), "OTHER")
            by[nm] = by.get(nm, 0) + 1
        forb = [ad for pc, ad, va in log if 0x315f8c <= ad < 0x318000]
        census[label] = {"stores": len(log), "by_range": by, "outside": ["%#x@%#x" % (ad, pc) for pc, ad in stray[:10]],
                         "outside_count": len(stray), "into_0x315f8c_0x318000": len(forb)}
        print("(c)", label, census[label]["stores"], census[label]["outside_count"], by, flush=True)
    ok = all(v["outside_count"] == 0 and v["into_0x315f8c_0x318000"] == 0 for v in census.values())
    return ok, {"store_census": census, "ok": ok}


def proof_d(imgs, q):
    nb = 1500 if not q else 300
    mixl = VC.load_input("mix", nb * 32 / SR)
    drums = VC.load_input("drums", nb * 32 / SR)
    out = {}
    for name, words, inp, sched in (
            ("typical: band defaults, the three MIX 39, FX on", dict(VC.BASE, **VC.FXW, **VC.TYPICAL), mixl, sched3(DEFAULTS, (39, 39, 39))),
            ("the starting point, MIX 127, drums", dict(VC.BASE, **VC.NOFX, **VC.comp(32, 24, 32, 64, 3, 127)), drums,
             sched3(START, (127, 127, 127)))):
        e = {}
        for tag in ("CMP3A", "CMP3"):
            run = TailRun(V.candidate_vm(imgs[tag][0], cache=imgs[tag][2]), words, VC.ROUTE_INFX, sched)
            run.tprocess(inp[0], inp[1], paint=True)
            pb = run.r.per_block[1:]
            e[tag] = {"mean": round(sum(pb) / len(pb)), "max": max(pb), "stack_bytes": run.stack,
                      "est_load_mean_pct": round(100 * VC.LOAD(sum(pb) / len(pb)), 2)}
        e["delta_mean_insns"] = e["CMP3"]["mean"] - e["CMP3A"]["mean"]
        out[name] = e
        print("(d)", name, e, flush=True)
    out["ok"] = all(v["CMP3"]["stack_bytes"] <= v["CMP3A"]["stack_bytes"] + 64 and abs(v["delta_mean_insns"]) < 1500
                    for v in out.values() if isinstance(v, dict))
    out["calibration"] = "load = (1.30 x instructions + 11k) / 666,667 (notes/T6.4 4.4) [inferred]; the unit measured ~50-60 % (CP2D)"
    return out["ok"], out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--only", help="comma-separated proofs (a,b,c,d)")
    a = ap.parse_args()
    only = set(a.only.split(",")) if a.only else set("abcd")
    t_all = time.time()
    imgs = {"CMP3": VC.image("CMP3"), "CMP3A": VC.image("CMP3A")}
    info = imgs["CMP3"][1]
    for nm in ("CMP3", "CMP3A"):
        stale = VC.stale_sources(nm, imgs[nm][1])
        if stale:
            raise SystemExit(f"{nm} was built from other sources ({stale}): run build.py {nm}")
    OFF.update(VC.offsets(OUT / "build", "CMP3"))
    offa = VC.offsets(ROOT / "out/T6.3/superseded/CMP3A-dsp/build", "CMP3A")
    if any(offa[k] != OFF[k] for k in offa):
        raise SystemExit("CmpSlot's layout differs between CMP3A and CMP3")
    res = {"name": "CMP3", "section7_sha256": info["section7"]["sha256"], "cmp3a_section7_sha256": imgs["CMP3A"][1]["section7"]["sha256"],
           "proofs_run": "".join(sorted(only)), "cmp_slot_layout_same_as_CMP3A": True}
    oks = {}
    if "c" in only:
        oks["c"], res["c_stores"] = proof_c(imgs, info)
    if "d" in only:
        oks["d"], res["d_stack_cost"] = proof_d(imgs, a.quick)
    if "b" in only:
        oks["b"], res["b_meters"] = proof_b(imgs, a.quick)
    if "a" in only:
        oks["a"], res["a_vs_CMP3A"] = proof_a(imgs, a.quick)
    res["oks"] = oks
    res["VERDICT"] = "PASS" if all(oks.values()) else "FAIL"
    res["seconds"] = round(time.time() - t_all)
    res["peak_rss_mb"] = round(memguard.peak_rss_mb())
    OUT.mkdir(parents=True, exist_ok=True)
    fn = ("verify3m-quick" if a.quick else "verify3m") + ("" if only == set("abcd") else "-" + "".join(sorted(only))) + ".json"
    (OUT / fn).write_text(json.dumps(res, indent=1, default=str))
    print("VERDICT", res["VERDICT"], oks, "%.0fs" % res["seconds"], "peak RSS %d MB" % res["peak_rss_mb"], OUT / fn, flush=True)


if __name__ == "__main__":
    main()
