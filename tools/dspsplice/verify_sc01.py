#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""SC01 (2026-10-21; notes/SC01.md): the DSP proofs of the merged line, CMP3 + the Space Echo (the stock FDBK law) + the
flanger, with breathing band meters and the functions placed per region (L1 block 2, code4, L2 over dead stock code).

    python3 tools/dspsplice/verify_sc01.py [--quick] [--only a,b,e,w,s]

(a) the compressor and the flanger against CMP3 (out/T6.3/CMP3-dsp, on the unit), the two images in lockstep on CMP3's
    own verify settings (verify_cmp3m.py (a): zero tails, invalid tails, the three MIX 0, equal MIX and the relock, one
    band wet, the guard and re-init injections, the cost cases, seeded old kits), with the delay slot silent in both
    (no send into it, its VOL 0: CMP3's tape delay and SC01's Space Echo then both return 0, so the compressor gets the
    same input): every handler output sample bit for bit, the chorus slot's output (the flanger) every block, each
    band's gain reduction every block, both reply pages every block but word 0 and half-words 8..10 (the meters;
    half-word 11 compared), and at the end L1 word for word (CmpSlot but its two meter words, CmpTail, the flanger's
    state, the stock state, the master overdrive) outside our code, data, stack and CMP3's CmpMeter;
(b) the meters: every block the reply half-words 8..10 of both pages = this script's float32 mirror of cmp_mtr fed the
    band's y2 (the slot's gain-reduction copy): q = round(1310 x clamp(dB, 0, 25)), a NaN or inf 0; on bursts per band at
    ATK 0 and REL 0 / 64 / 127 (how long the bar takes to go, against CMP3's 0.5 s hold + 20 dB/s), on the guide's
    starting point on drums, RAT 1.00 (no bar), and a NaN put into a band's envelope (the bar 0 in that block);
(e) the Space Echo against SE01 (out/T5.3/SE01-dsp, never flashed): (e1) at FDBK 0 SC01's delay slot output and the
    whole SeSlot (tape, state) bit for bit SE01's; (e2) SE01 with only spaceecho_fb replaced by SC01's (SC01's compiled
    function linked at SE01's own address and patched into SE01's stream: SE01') bit for bit SC01 on verify_se.py's
    configurations, the FDBK sweep 32 / 64 / 100 / 127 with one and three heads, every frame word moving, the fuzz and the
    held runaways: so the feedback law is the one change; (e3) the law in SC01's image: spaceecho_fb(v) = v / 64 for v in
    -8..300 step 0.25 (sharcvm call), SE01's for comparison; (e4) the runaways at FDBK 64..127: wet <= the ceiling 0.7,
    the tape within +-1, finite;
(w) the whole mix with all three effects on (drums and the mix, the echo at FDBK 64 / 100 / 127, three heads, the
    compressor's bands working, the flanger, the reverb): finite, the master ceiling, the echo's wet and tape bounded;
    instructions per block (typical and worst: every knob of all three moving, noise), the stack depth; and the
    placement at run time: with sharcvm's profile on, every instruction executed in the four L2 ranges lies inside our
    functions there, and nothing outside them (the stock bytes left in their tails) runs;
(s) stores: the delay call and the chorus call (only their state, the stack, the stub save area, their output buffers),
    the compressor call (CMP3's census without CmpMeter), and over whole blocks no store into our code (L1 block 1 and 2
    code, code4, the L2 ranges) nor into 0x315f8c-0x318000.
Results: out/SC01/SC01-dsp/verify.json (or verify-<only>.json).
"""
import argparse
import gc
import json
import math
import random
import struct
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(HERE))
import verify_cmp3 as V3  # noqa: E402   (configures verify_cmp for CMP3; verify_cmp2's tail helpers)
import memguard  # noqa: E402
import ldr  # noqa: E402
import build as BD  # noqa: E402

VC, V2, V = V3.VC, V3.V2, V3.V
SLOT, TST, OFF, SR = V3.SLOT, V3.TST, V3.OFF, V3.SR
TailRun, tail_words, knobs17, sched3, list3, words3 = V3.TailRun, V3.tail_words, V3.knobs17, V3.sched3, V3.list3, V3.words3
DEFAULTS, DISTINCT, LOW_ONLY = V3.DEFAULTS, V3.DISTINCT, V3.LOW_ONLY
OUT = ROOT / "out/SC01/SC01-dsp"
SE01_DIR = ROOT / "out/T5.3/SE01-dsp"
CMP3_DIR = ROOT / "out/T6.3/CMP3-dsp"
QDB, MAXDB = 1310.0, 25.0
START = ([(64, 4, 24, 32, 0)] * 3, (68, 44))          # FLASH-CMP2.md's starting point (RAT 4.00 = step 4), MUP 0
SE_SLOT, SE_BYTES = 0x8005912c, 4163304
RING_OFF, RING_FLOATS = 48, 2 * 512000
CEIL = 0.7000001
CHR_CALL, CHR_RET, DEL_CALL, DEL_RET = V.CHR_CALL, V.CHR_RET, V.DEL_CALL, V.DEL_RET
CHR_OUT, DEL_OUT, DEL_IN = V.CHR_OUT, V.DEL_OUT, V.DEL_IN
SILENT_DELAY = dict(IN_DEL_L=0, IN_DEL_R=0, CHR_DEL=0, DEL_VOL=0, DEL_REV=0)
SE_FB_SW = {"SE01": 0x1847e9}                        # spaceecho_fb in SE01's image (build.json symbols)


def fbits(xs):
    import array
    return array.array("f", xs).tobytes()


def f32(x):
    return struct.unpack("<f", struct.pack("<f", x))[0]


def load_image(d, name):
    info = json.loads((d / "build.json").read_text())
    stream = (d / "section7.bin").read_bytes()
    if BD.sha(stream) != info["section7"]["sha256"]:
        raise SystemExit(f"{d}: section7.bin is not the image build.json describes")
    return stream, info, f"sc01-{name}-" + info["section7"]["sha256"][:12]


def hw(page_bytes):
    return struct.unpack_from("<4H", page_bytes, 0x10)


def mirror_q(gr_db):
    """cmp_mtr in float32 from the slot's copy gr = y2 x 6.0206 (<= 0): g = -gr; NaN/inf 0; 0..25; round(1310 g)"""
    g = -gr_db
    if not VC.finite(g):
        g = 0.0
    g = min(max(g, 0.0), MAXDB)
    x = f32(f32(f32(g) * QDB) + 0.5)
    return min(max(int(x), 0), 32767)


def code4_function(info, name):
    """the SW address of a function of the compressor glue or cmp_tail.c (code4, not split: no marker labels): the two
    files re-assembled with every function label global and linked alone at code4's start, as the build links them
    first there (checked against the glue's exported symbols)"""
    sys.path.insert(0, str(ROOT / "tools/dspsplice"))
    import importlib.util
    spec = importlib.util.spec_from_file_location("sc01_funcmap", str(HERE / "sc01_funcmap.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    rows, _ = m.funcmap(OUT / "funcmap-code4", 0x2e2900, [OUT / "build" / "cmp_glue.s", OUT / "build" / "cmp_tail.s"])
    got = {n: a for n, f, a, b in rows}
    for sym in ("cmp_init", "cmp_process", "cmp_tail_fetch", "cmp_tail_apply"):
        if got[sym] != int(info["symbols"][sym], 16):
            raise SystemExit("code4 function map does not match the image (%s)" % sym)
    return got[name]


# ---------------------------------------------------------------- a Run with the FX slots captured too

class FxTailRun(TailRun):
    """TailRun with the chorus and delay calls probed: their outputs per block (and an optional store watch over one
    call), the compressor call as before"""

    def __init__(self, vm, words, raw, sched, keep=False):
        super().__init__(vm, words, raw, sched, keep=keep)
        self.chr = []
        self.dl = []

    def block(self, L, R, inject=None, b=0, paint=False, tx_full=False, watch=None, fxwatch=None):
        vm, r = self.vm, self.r
        if paint:
            i7 = vm.reg("I7")
            vm.write(V.STACK_LO, V.PAINT.to_bytes(4, "little") * ((i7 - V.STACK_LO) // 4))
        spi = vm.rd32(V.fullblock.SPI_HALF)
        vm.write(V.fullblock.RX_BASE + 0x1000 * spi, bytes(r.d.frame))
        h = vm.rd32(V.fullblock.DMA_HALF)
        vm.write(V.fullblock.CODEC_IN + 0x100 * h,
                 b"".join(V.fullblock.q31(v).to_bytes(4, "little") for pair in zip(L, R) for v in pair))
        c = {}
        from vm import watch as W, watch_log

        def wat_on(which):
            def f():
                if fxwatch is not None and fxwatch[0] == which:
                    W(vm, 0, 0xffffffff)
            return f

        def wat_off(which, grab):
            def f():
                if fxwatch is not None and fxwatch[0] == which:
                    fxwatch[1].extend(watch_log(vm))
                    W(vm, 0, 0)
                grab()
            return f

        def g_chr():
            self.chr.append(vm.read(CHR_OUT[0], 0x80) + vm.read(CHR_OUT[1], 0x80))

        def g_del():
            self.dl.append(vm.read(DEL_OUT[0], 0x80) + vm.read(DEL_OUT[1], 0x80))

        def at_call():
            ia, oa = vm.rd32(VC.IO + 4), vm.rd32(VC.IO + 0xc)
            c["ptrs"] = [vm.rd32(ia + 4 * k) for k in range(4)]
            if inject:
                inject(b, vm, c["ptrs"])
            if watch is not None:
                W(vm, 0, 0xffffffff)

        def at_ret():
            if watch is not None:
                watch.extend(watch_log(vm))
                W(vm, 0, 0)
        n0 = vm.icount
        r._call_probed([(CHR_CALL, wat_on("chr")), (CHR_RET, wat_off("chr", g_chr)), (DEL_CALL, wat_on("del")),
                        (DEL_RET, wat_off("del", g_del)), (VC.COMP_CALL, at_call), (VC.COMP_RET, at_ret)])
        r.per_block.append(vm.icount - n0)
        if paint:
            i7 = vm.reg("I7")
            raw = vm.read(V.STACK_LO, i7 - V.STACK_LO)
            word = V.PAINT.to_bytes(4, "little")
            low = next((V.STACK_LO + k for k in range(0, len(raw), 4) if raw[k:k + 4] != word), i7)
            self.stack = max(self.stack, i7 - low)
        if tx_full:
            self.tx_pages.append(b"".join(vm.read(t, 0x1000) for t in VC.TX))
        oL, oR = vm.rdf(V.fullblock.OUT_FLOATS, 32), vm.rdf(V.fullblock.OUT_FLOATS + 0x80, 32)
        self.out[0].extend(oL)
        self.out[1].extend(oR)
        return oL, oR


# ---------------------------------------------------------------- (a) lockstep CMP3 / SC01

class Pair:
    """CMP3 (a) and SC01 (b) on the same blocks, the delay slot silent in both; every block: outputs, the chorus slot's
    output, band gain reductions, reply pages (but word 0 and half-words 8..10)"""

    def __init__(self, imgs, words, raw, sched):
        words = dict(words, **SILENT_DELAY)
        self.a = FxTailRun(V.candidate_vm(imgs["CMP3"][0], cache=imgs["CMP3"][2]), words, raw, sched)
        self.b = FxTailRun(V.candidate_vm(imgs["SC01"][0], cache=imgs["SC01"][2]), words, raw, sched)
        self.nb = self.samples = self.same = self.gr_same = self.pages_same = self.hw11_same = self.chr_same = 0
        self.del_zero = 0
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
        ca, cb = self.a.chr.pop(), self.b.chr.pop()
        self.chr_same += ca == cb
        da, db_ = self.a.dl.pop(), self.b.dl.pop()
        self.del_zero += (da == bytes(0x100)) and (db_ == bytes(0x100))
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
        ignore = [(0x2dd600, 0x2e1c40, "our block-1 code"), (0x2e2800, 0x2e7000, "code4 / CMP3's CmpMeter / data / save area"),
                  (0x308400, 0x318000, "block-2 code"), (V.STACK_LO, V.STACK_TOP, "stack"),
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
             "flanger_output_blocks_identical": self.chr_same, "delay_slot_silent_blocks_both": self.del_zero,
             "gain_reduction_blocks_identical": self.gr_same, "reply_pages_identical_but_word0_and_hw8_10": self.pages_same,
             "reply_byte_offsets_that_differ": sorted("%#x" % o for o in self.page_offsets),
             "half_word_11_identical_blocks": self.hw11_same,
             "l1_words_that_differ_outside_ours": ["%#x" % a for a in mem[:20]], "l1_count": len(mem),
             "trips_reinits_CMP3_SC01": [[v.rd32(SLOT + OFF["trips"]), v.rd32(SLOT + OFF["reinits"])] for v in (va, vb)],
             "insns_mean_CMP3_SC01": [round(sum(pa[1:]) / max(1, len(pa) - 1)), round(sum(pb[1:]) / max(1, len(pb) - 1))],
             "insns_max_CMP3_SC01": [max(pa[1:] or [0]), max(pb[1:] or [0])], "peak": round(self.peak, 4)}
        r["ok"] = (self.same == self.samples and self.gr_same == self.nb and self.pages_same == self.nb
                   and self.hw11_same == self.nb and self.chr_same == self.nb and not mem)
        return r


def proof_a(imgs, q):
    res, ok_all = {}, True

    def done(group, name, r, t0):
        nonlocal ok_all
        r["seconds"] = round(time.time() - t0, 1)
        res.setdefault(group, {})[name] = r
        ok_all = ok_all and r["ok"]
        print("(a) %-4s %-92s %s %d/%d samples, flanger %d/%d, %d blocks, insns %s, %.0fs, rss %d MB" % (
            group, name[:92], "ok" if r["ok"] else "DIFFERS", r["samples_identical"], r["samples"],
            r["flanger_output_blocks_identical"], r["blocks"], r["blocks"], r["insns_mean_CMP3_SC01"], r["seconds"],
            memguard.peak_rss_mb()), flush=True)
        if not r["ok"]:
            print("      ", {k: v for k, v in r.items() if k not in ("seconds",)}, flush=True)
        memguard.check(4096)
        gc.collect()

    moving17 = lambda b: [20 + 4 * (b % 2), 3 + b % 2, 10 + 4 * (b % 2), 40 + 4 * (b % 2), 50 + 4 * (b % 2), 60 + 4 * (b % 2),   # noqa: E731
                          60 - 4 * (b % 2), 6 - b % 2, 40 + 4 * (b % 2), 60 + 4 * (b % 2), 30 + 4 * (b % 2), 50 + 4 * (b % 2),
                          90 - 4 * (b % 2), 8 - b % 2, 80 + 4 * (b % 2), 100 + 4 * (b % 2), 90 - 4 * (b % 2)]
    mixes_apart = lambda b: [0 if ((b + 13 * k) // 40) % 2 else (40, 64, 100)[k] for k in range(3)]   # noqa: E731

    # (h) zero tails, the fallback
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

    # (z) invalid tails
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

    # (3) one band at 127, the others at 0
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

    # (5) the guard and the quiet re-init
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

    # (d) the cost cases, noise and the flanger's knobs jumping (the delay slot silent)
    nb = 1500 if not q else 300
    mixl = VC.load_input("mix", nb * 32 / SR)
    rn = random.Random(7)
    noise = ([rn.uniform(-1, 1) for _ in range(nb * 32)], [rn.uniform(-1, 1) for _ in range(nb * 32)])
    knames = list(VC.FL.KNOBS)
    loudfx = dict(CHR_REV=127, REV_VOL=100)
    jumps = {}

    def fx_jump(b, fr):
        if b % 20 == 0:
            if b not in jumps:
                jumps[b] = [rn.choice([0, 127, rn.randrange(128)]) for _ in knames]
            for k, v in zip(knames, jumps[b]):
                VC.put(fr, k, v << 8)
    worst = list3([(moving17(b), mixes_apart(b)) for b in range(nb)])
    for name, (words, raw, inp, sched, each) in {
            "typical: band defaults, the three MIX 39": (dict(VC.BASE, **VC.FXW, **VC.TYPICAL), VC.ROUTE_INFX, mixl,
                                                         sched3(DEFAULTS, (39, 39, 39)), None),
            "band defaults, MIX 20 / 39 / 64 (apart)": (dict(VC.BASE, **VC.FXW, **VC.TYPICAL), VC.ROUTE_INFX, mixl,
                                                        sched3(DEFAULTS, (20, 39, 64)), None),
            "worst: every band knob, both crossovers and each MIX moving (through 0 apart), noise, flanger jumping, self": (
                dict(dict(VC.BASE, **VC.FXW, **VC.W3_SELF), **loudfx), VC.ROUTE_INFX, noise, worst, fx_jump),
            "worst, key (SCS 18)": (dict(dict(VC.BASE, **VC.FXW, **VC.W3), **loudfx), VC.ROUTE_INFX, noise, worst, fx_jump)}.items():
        t0 = time.time()
        jumps.clear()
        done("d", name, Pair(imgs, words, raw, sched).run(inp[0], inp[1], each=each), t0)

    # (s) old kits seeded (the fallback's seed, then tail mode)
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
    return ok_all, res


# ---------------------------------------------------------------- (b) the meters

def proof_b(imgs, q):
    img = imgs["SC01"]
    res, oks = {}, []

    def check_run(run, nb, inp, inject=None):
        bad, qs, grs = 0, [], []
        for b in range(nb):
            run.tblock(inp[0][32 * b:32 * b + 32], inp[1][32 * b:32 * b + 32], b, tx_full=True, inject=inject)
            pg = run.tx_pages.pop()
            gr = run.vm.rdf(SLOT + OFF["gr0"], 3)
            want = [mirror_q(g) for g in gr]
            got0, got1 = list(hw(pg[:0x20])[:3]), list(hw(pg[0x1000:0x1020])[:3])
            if got0 != want or got1 != want:
                bad += 1
            qs.append(got0)
            grs.append(gr)
            run.out[0].clear()
            run.out[1].clear()
        return bad, qs, grs

    # (b1) bursts per band, ATK 0, REL 0 / 64 / 127: the bar follows the band's envelope (no hold)
    nb = 4800 if not q else 900
    b1 = {}
    for band, f0 in ((0, 80.0), (1, 700.0), (2, 6000.0)):
        for rel in (0, 64, 127):
            L = [0.0] * (nb * 32)
            for i in range(300 * 32, 600 * 32):          # a 0.2 s burst at -6 dBFS-ish
                L[i] = 0.5 * math.sin(2 * math.pi * f0 * i / SR)
            bands = [(64, 4, 0, rel, 0) if b == band else (64, 0, 24, 32, 0) for b in range(3)]
            run = TailRun(V.candidate_vm(img[0], cache=img[2]), dict(VC.BASE, **VC.NOFX, **VC.comp(32, 24, 32, 64, 3, 127)),
                          VC.ROUTE_IN, sched3((bands, (68, 44)), (127, 127, 127)))
            bad, qs, grs = check_run(run, nb, (L, L))
            col = [x[band] for x in qs]
            pk = max(col)
            pb = col.index(pk)
            q_end = col[600]
            # after the burst: the bar follows the band's release (no hold); blocks to half its level, to under 1 px
            half = next((b for b in range(600, nb) if col[b] <= q_end / 2), None)
            px0 = next((b for b in range(600, nb) if col[b] < 655), None)
            e = {"mirror_mismatch_blocks": bad, "peak_q": pk, "peak_px": pk // 655, "peak_db": round(pk / QDB, 2),
                 "first_bar_block": next((b for b in range(nb) if col[b] >= 655), None), "burst_blocks": [300, 600],
                 "db_at_burst_end": round(q_end / QDB, 2),
                 "ms_from_burst_end_to_half": None if half is None else round((half - 600) / 1.5, 1),
                 "ms_from_burst_end_to_under_1px": None if px0 is None else round((px0 - 600) / 1.5, 1),
                 "cmp3_hold_then_fall": "0.5 s hold, then 20 dB/s: %.0f ms to under 1 px" % (500 + 1000 * max(q_end / QDB - 0.5, 0) / 20),
                 "other_bands_max_q": max(max(x[k] for x in qs) for k in range(3) if k != band)}
            e["ok"] = bad == 0 and pk > 0
            b1["%s REL %d" % (("LOW", "MID", "HIGH")[band], rel)] = e
            oks.append(e["ok"])
            print("(b1)", band, rel, e, flush=True)
            memguard.check(4096)
    res["b1_bursts"] = b1
    # (b2) the starting point on drums, three levels; RAT 1.00 on every band: no bar
    secs = 4.0 if not q else 1.0
    b2 = {}
    for tag, gain, bands in (("drums, the starting point", 1.0, START), ("drums -12 dB", 0.25, START),
                             ("drums, RAT 1.00 every band", 1.0, ([(64, 0, 24, 32, 0)] * 3, (68, 44)))):
        Ld, Rd = VC.load_input("drums", secs, gain=gain)
        run = TailRun(V.candidate_vm(img[0], cache=img[2]), dict(VC.BASE, **VC.NOFX, **VC.comp(32, 24, 32, 64, 3, 127)),
                      VC.ROUTE_IN, sched3(bands, (127, 127, 127)))
        bad, qs, grs = check_run(run, len(Ld) // 32, (Ld, Rd))
        px = [[x[k] // 655 for x in qs] for k in range(3)]
        e = {"mirror_mismatch_blocks": bad, "px_max": [max(p) for p in px],
             "px_mean": [round(sum(p) / len(p), 1) for p in px],
             "blocks_with_a_bar": [sum(1 for v in p if v > 0) for p in px], "blocks": len(qs)}
        e["ok"] = bad == 0 and (("RAT 1.00" not in tag) or max(e["px_max"]) == 0)
        b2[tag] = e
        oks.append(e["ok"])
        print("(b2)", tag, e, flush=True)
    res["b2_drums"] = b2
    # (b3) cmp_mtr itself, called in sharcvm at its address in the image (a leaf), on the edge values of y (the band's
    # y2, log2 units): NaN, +-inf, positive (no reduction), 0, the 25 dB cap and beyond; against the float32 mirror
    sys.path.insert(0, str(ROOT / "tools/dspsplice"))
    addr = code4_function(img[1], "cmp_mtr")
    vm = V.candidate_vm(img[0], cache=img[2])
    K = f32(6.0205999)
    rows, bad = [], 0
    for y in (float("nan"), float("inf"), -float("inf"), 1.0, 1e-30, 0.0, -0.0, -1e-6, -0.08304, -0.1, -1.0, -1.993,
              -2.0, -4.15, -4.1524, -4.2, -10.0, -1e30):
        r0 = vm.call(addr, args=(struct.unpack("<I", struct.pack("<f", y))[0],))
        want = mirror_q(f32(f32(y) * K)) if y == y else 0
        rows.append((repr(y), r0, want))
        bad += r0 != want
    e = {"cmp_mtr_sw": "%#x" % addr, "calls": rows, "mismatches": bad}
    e["ok"] = bad == 0
    res["b3_cmp_mtr_edge_values"] = e
    oks.append(e["ok"])
    print("(b3)", e, flush=True)
    res["law"] = ("every block: q = round(1310 x clamp(gain reduction dB, 0, 25)) of the band's y2 at the block's end (no "
                  "hold, no fall of its own); comp-ui 0.2 draws q // 655 px: 2 px per dB")
    res["ok"] = all(oks)
    return res["ok"], res


# ---------------------------------------------------------------- (e) the Space Echo against SE01

def se01_prime(se01_stream, sc01_bdir, work):
    """SE01's stream with spaceecho_fb replaced by SC01's (SC01's compiled function linked at SE01's own address);
    -> (stream, info)"""
    text = (sc01_bdir / "spaceecho.s").read_text().split("\n")
    i = text.index("spaceecho_fb.:")
    j = text.index(".ENDSEG;", i)
    body = text[i:j]
    if any("CJUMP" in ln or "CALL" in ln for ln in body):
        raise SystemExit("SC01's spaceecho_fb is not a leaf")
    work.mkdir(parents=True, exist_ok=True)
    (work / "fb.s").write_text(".SECTION/SW seg_fb;\n.GLOBAL spaceecho_fb.;\n" + "\n".join(body) + "\n.ENDSEG;\n")
    a = SE_FB_SW["SE01"]
    (work / "fb.ldf").write_text("""ARCHITECTURE(ADSP-21569)
$OBJECTS = $COMMAND_LINE_OBJECTS;
MEMORY
{
   mem_fb { TYPE(BW RAM) START(0x%08x) END(0x%08x) WIDTH(8) }
}
PROCESSOR p0
{
   OUTPUT($COMMAND_LINE_OUTPUT_FILE)
   ENTRY(spaceecho_fb.)
   SECTIONS
   {
      fb SW
      {
         INPUT_SECTION_ALIGN(2)
         FILL(0x1)
         INPUT_SECTIONS( $OBJECTS(seg_fb) )
      } > mem_fb
   }
}
""" % (2 * a, 2 * a + 196 - 1))
    subprocess.check_call([BD.SEL / "selas", "-proc", "ADSP-21569", "-o", work / "fb.doj", work / "fb.s"])
    subprocess.check_call([BD.SEL / "seld", "-proc", "ADSP-21569", "-T", work / "fb.ldf", "-o", work / "fb.dxe", work / "fb.doj"])
    e = BD.E.read_elf(work / "fb.dxe")
    sec = [s for s in e.sections if s.kind == "sw_code"][0]
    if sec.addr != a or len(sec.data) > 196:
        raise SystemExit("spaceecho_fb did not link at SE01's address or is too long")
    st = ldr.Stream(se01_stream)
    core = BD.E.sw_to_byte(a)
    old = st.read(core, len(sec.data))
    st.patch(core, old, bytes(sec.data), why="SC01's spaceecho_fb at SE01's spaceecho_fb")
    out = st.to_bytes()
    return out, {"sw": "%#x" % a, "core": "%#x" % core, "bytes": len(sec.data), "replaced_of": 196,
                 "stream_sha256": BD.sha(out)}


class SeRun:
    """the whole handler with the delay slot's output captured per block (vmrun.Runner), and the SeSlot at the end"""

    def __init__(self, stream, cache, words, age):
        self.vm = V.candidate_vm(stream, cache=cache)
        self.r = V.Runner(self.vm, words)
        self.age = age
        self.set_age(age)

    def set_age(self, v):
        struct.pack_into("<H", self.r.d.frame, 0xa18, (int(round(v * 256)) & 0xffff))

    def block(self, L, R):
        self.r.cap["del_out"][0].clear()
        self.r.cap["del_out"][1].clear()
        self.r.block(L, R, capture=True)
        return list(self.r.cap["del_out"][0]), list(self.r.cap["del_out"][1])

    def slot(self):
        return self.vm.read(SE_SLOT, SE_BYTES)

    def ring(self):
        return self.vm.rdf(SE_SLOT + RING_OFF, RING_FLOATS)


def se_pair(sa, sb, L, R, each=None, nb=None):
    """two SeRuns on the same blocks -> comparison of the delay slot's output every block, the SeSlot at the end"""
    nb = nb or len(L) // 32
    same = samples = 0
    pk = 0.0
    nan = 0
    for b in range(nb):
        if each:
            each(b, sa, sb)
        oa = sa.block(L[32 * b:32 * b + 32], R[32 * b:32 * b + 32])
        ob = sb.block(L[32 * b:32 * b + 32], R[32 * b:32 * b + 32])
        xa, xb = fbits(oa[0] + oa[1]), fbits(ob[0] + ob[1])
        samples += 64
        same += 64 if xa == xb else sum(1 for x, y in zip(oa[0] + oa[1], ob[0] + ob[1]) if struct.pack("<f", x) == struct.pack("<f", y))
        for x in ob[0] + ob[1]:
            if x != x or abs(x) == float("inf"):
                nan += 1
            elif abs(x) > pk:
                pk = abs(x)
    slot_same = sa.slot() == sb.slot()
    ring = sb.ring()
    rmax = max((abs(x) for x in ring if x == x), default=0.0)
    rbad = sum(1 for x in ring if x != x or abs(x) == float("inf"))
    return {"blocks": nb, "samples": samples, "delay_output_identical": same, "seslot_identical_at_end": slot_same,
            "wet_peak": round(pk, 6), "wet_nan_inf": nan, "tape_peak": round(rmax, 6), "tape_nan_inf": rbad,
            "ok": same == samples and slot_same}


def proof_e(imgs, q):
    res, oks = {}, []
    sc, se = imgs["SC01"], imgs["SE01"]
    sp_stream, sp_info = se01_prime(se[0], OUT / "build", OUT / "se01prime")
    res["se01_prime"] = sp_info
    sp = (sp_stream, None, "sc01-se01p-" + sp_info["stream_sha256"][:12])
    import verify_se as VS
    from fx import read_wav
    pl, pr, _ = read_wav(ROOT / "out/T3.11/dry/pluck.wav")
    secs = 6.0 if not q else 1.5
    n = int(secs * SR) // 32 * 32
    P = ((pl + [0.0] * n)[:n], (pr + [0.0] * n)[:n])
    base = dict(VS.WORDS)

    def run_case(tag, words, age, inp, ref="prime", each=None, nb=None):
        t0 = time.time()
        a = SeRun(sp[0] if ref == "prime" else se[0], sp[2] if ref == "prime" else se[2], words, age)
        b = SeRun(sc[0], sc[2], words, age)
        r = se_pair(a, b, inp[0], inp[1], each=each, nb=nb)
        r["against"] = "SE01'" if ref == "prime" else "SE01"
        r["seconds"] = round(time.time() - t0, 1)
        res.setdefault("cases", {})[tag] = r
        oks.append(r["ok"])
        print("(e) %-70s %s %d/%d, slot %s, wet %.4f, tape %.4f, %.0fs" % (tag[:70], "ok" if r["ok"] else "DIFFERS",
              r["delay_output_identical"], r["samples"], r["seslot_identical_at_end"], r["wet_peak"], r["tape_peak"],
              r["seconds"]), flush=True)
        del a, b
        gc.collect()
        memguard.check(4096)
        return r

    # (e1) FDBK 0: SC01 = SE01 itself
    run_case("e1 main configuration at FDBK 0 (three heads, AGE 64, band, ALT): SC01 vs SE01", dict(base, DEL_FDBK=0),
             VS.AGE_MAIN, P, ref="SE01")
    run_case("e1 a clean kit at FDBK 0 (X 0, WID 64, LPF 127, AGE 1): SC01 vs SE01",
             dict(base, **dict(VS.CONFIGS["existing_kit_clean"]["knobs"], DEL_FDBK=0)), 1, P, ref="SE01")
    # (e2) every FDBK against SE01'
    run_case("e2 verify_se main configuration (FDBK 50)", base, VS.AGE_MAIN, P)
    for k, c in VS.CONFIGS.items():
        run_case("e2 verify_se %s" % k, dict(base, **c["knobs"]), c["age"], P)
    for fb in (32, 64, 100, 127):
        for heads, x in (("one head", 0), ("three heads", 6)):
            run_case("e2 FDBK %d, %s, BW 127, AGE 0" % (fb, heads), dict(base, DEL_FDBK=fb, DEL_X=x, DEL_LPF=127, DEL_WID=64), 0, P)
    rnd = random.Random(5)
    nbm = int(4.0 * SR / 32) if not q else 300
    mv = {}

    def moving(b, a_, b_):
        if b % 3 == 0:
            if b not in mv:
                mv[b] = dict(DEL_TIME=rnd.randrange(24), DEL_X=rnd.randrange(7), DEL_WID=rnd.randrange(128),
                             DEL_FDBK=rnd.randrange(128), DEL_HPF=rnd.randrange(128), DEL_LPF=rnd.randrange(128),
                             age=rnd.randrange(128))
            for s_ in (a_, b_):
                s_.r.d.set(**{k: v for k, v in mv[b].items() if k != "age"})
                s_.set_age(mv[b]["age"])
    Pm = (P[0] * 3, P[1] * 3)
    run_case("e2 every frame word moving (each 3 blocks, random; TIME 0..23 so the echoes come back within the run)",
             base, 64, Pm, each=moving, nb=nbm)
    # fuzz: full-scale noise into the slot, every knob jumping each 20 blocks, FDBK 127 half the time
    nbf = 1500 if not q else 300
    noise = ([rnd.uniform(-1, 1) for _ in range(nbf * 32)], [rnd.uniform(-1, 1) for _ in range(nbf * 32)])
    fz = {}

    def fuzz(b, a_, b_):
        if b % 20 == 0:
            if b not in fz:
                fz[b] = dict(DEL_TIME=rnd.randrange(128) if rnd.random() < 0.3 else rnd.randrange(16),
                             DEL_X=rnd.randrange(7), DEL_WID=rnd.randrange(128),
                             DEL_FDBK=127 if rnd.random() < 0.5 else rnd.randrange(128), DEL_HPF=rnd.randrange(128),
                             DEL_LPF=rnd.randrange(128), age=rnd.randrange(128))
            for s_ in (a_, b_):
                s_.r.d.set(**{k: v for k, v in fz[b].items() if k != "age"})
                s_.set_age(fz[b]["age"])
    run_case("e2 fuzz: full-scale noise, every knob jumping each 20 blocks (TIME mostly short), FDBK 127 half the time",
             dict(base, IN_DEL_L=127, IN_DEL_R=127, DEL_VOL=127), 64, noise, each=fuzz)
    # held runaways (verify_se part 9) at FDBK 64, 100, 127: three heads, BW 127, AGE 0, ALT 127 and RND 127
    nbr = int(4.0 * SR / 32) if not q else 600
    sine = [0.9 * math.sin(2 * math.pi * 220 * i / SR) if i < SR else 0.0 for i in range(nbr * 32)]
    for fb in (64, 100, 127):
        for side, wid in (("ALT 127", 0), ("RND 127", 127)):
            r = run_case("e4 held runaway FDBK %d, MODE 123, %s, BW 127, AGE 0 (1 s of 0.9 sine, then silence)" % (fb, side),
                         dict(base, IN_L=127, IN_R=127, IN_DEL_L=127, IN_DEL_R=127, DEL_VOL=127, DEL_X=6, DEL_FDBK=fb,
                              DEL_LPF=127, DEL_WID=wid, DEL_TIME=10), 0, (sine, sine))
            ok = r["wet_peak"] <= CEIL and r["tape_peak"] <= 1.0 + 1e-6 and r["wet_nan_inf"] == 0 and r["tape_nan_inf"] == 0
            r["bounded"] = ok
            oks.append(ok)
    # (e3) the law in the image: spaceecho_fb called in sharcvm
    law = {}
    for tag, img, a in (("SC01", sc, int(sc[1]["symbols"]["spaceecho_fb"], 16)), ("SE01", se, SE_FB_SW["SE01"])):
        vm = V.candidate_vm(img[0], cache=img[2])
        vals, bad = [], 0
        for i in range(-32, 1201):
            v = i * 0.25
            r0 = vm.call(a, args=(struct.unpack("<I", struct.pack("<f", v))[0],))
            got = struct.unpack("<f", struct.pack("<I", r0 & 0xffffffff))[0]
            want = min(max(v, 0.0), 127.0) / 64.0 if v > 0 else 0.0
            if tag == "SC01" and got != f32(want):
                bad += 1
            vals.append((v, got))
        law[tag] = {"mismatches_vs_v_over_64": bad if tag == "SC01" else None,
                    "at_0_32_50_64_100_127": [round(g, 6) for v, g in vals if v in (0, 32, 50, 64, 100, 127)],
                    "max": round(max(g for v, g in vals), 6)}
        del vm
    law["ok"] = law["SC01"]["mismatches_vs_v_over_64"] == 0 and law["SC01"]["at_0_32_50_64_100_127"] == [0.0, 0.5, 0.78125, 1.0, 1.5625, 1.984375]
    res["e3_law"] = law
    oks.append(law["ok"])
    print("(e3)", law, flush=True)
    res["ok"] = all(oks)
    return res["ok"], res


# ---------------------------------------------------------------- (w) the whole mix, the cost, the placement at run time

def proof_w(imgs, q):
    img = imgs["SC01"]
    info = img[1]
    res, oks = {}, []
    l2 = [(int(r["sw"], 16), int(r["sw"], 16) + r["bytes"] // 2, r["function"]) for r in info["functions"]
          if r["region"].startswith("l2")]
    l2_ranges = [(lo, hi) for lo, hi in BD.RECIPES["SC01"]["l2"].values()]
    nb = 3000 if not q else 400
    prog = VC.looped(nb)
    moving17 = lambda b: [20 + 4 * (b % 2), 3 + b % 2, 10 + 4 * (b % 2), 40 + 4 * (b % 2), 50 + 4 * (b % 2), 60 + 4 * (b % 2),   # noqa: E731
                          60 - 4 * (b % 2), 6 - b % 2, 40 + 4 * (b % 2), 60 + 4 * (b % 2), 30 + 4 * (b % 2), 50 + 4 * (b % 2),
                          90 - 4 * (b % 2), 8 - b % 2, 80 + 4 * (b % 2), 100 + 4 * (b % 2), 90 - 4 * (b % 2)]
    mixes_apart = lambda b: [0 if ((b + 13 * k) // 40) % 2 else (40, 64, 100)[k] for k in range(3)]   # noqa: E731
    rn = random.Random(9)
    noise = ([rn.uniform(-1, 1) for _ in range(nb * 32)], [rn.uniform(-1, 1) for _ in range(nb * 32)])
    allfx = dict(IN_CHR_L=100, IN_CHR_R=100, IN_DEL_L=100, IN_DEL_R=100, IN_REV_L=60, IN_REV_R=60, CHR_DEL=40, CHR_REV=30,
                 CHR_VOL=90, DEL_REV=40, DEL_VOL=100, REV_VOL=90, REV_DEC=90, **VC.FL.KNOBS)
    jumps = {}

    def everything(b, fr):
        # every flanger and echo knob jumping each 20 blocks (the comp's band knobs move through the tail)
        if b % 20 == 0:
            if b not in jumps:
                jumps[b] = {**{k: rn.randrange(128) for k in VC.FL.KNOBS},
                            **{k: rn.randrange(128) for k in ("DEL_TIME", "DEL_WID", "DEL_FDBK", "DEL_HPF", "DEL_LPF")},
                            "DEL_X": rn.randrange(7), "age": rn.randrange(128)}
            for k, v in jumps[b].items():
                if k == "age":
                    struct.pack_into("<H", fr, 0xa18, v << 8)
                else:
                    VC.put(fr, k, v << 8)
    cases = {
        "typical: the mix, echo MODE 1 FDBK 50 AGE 32 BW 127, flanger, reverb, bands at defaults MIX 39":
            (dict(VC.BASE, **allfx, **VC.TYPICAL, DEL_TIME=23, DEL_X=0, DEL_WID=64, DEL_FDBK=50, DEL_HPF=64, DEL_LPF=127),
             32, prog, sched3(DEFAULTS, (39, 39, 39)), None),
        "busy: drums and the mix, echo MODE 123 FDBK 64 AGE 64 band 64/64 ALT, flanger, reverb, distinct bands MIX 64":
            (dict(VC.BASE, **allfx, **VC.comp(32, 24, 32, 64, 3, 64), DEL_TIME=23, DEL_X=6, DEL_WID=32, DEL_FDBK=64,
                  DEL_HPF=64, DEL_LPF=64), 64, prog, sched3(DISTINCT, (64, 64, 64)), None),
        "runaway: echo MODE 123 FDBK 100 BW 127 AGE 0 RND 127, flanger, reverb, the starting point MIX 127":
            (dict(VC.BASE, **allfx, **VC.comp(32, 24, 32, 64, 3, 127), DEL_TIME=10, DEL_X=6, DEL_WID=127, DEL_FDBK=100,
                  DEL_HPF=64, DEL_LPF=127), 0, prog, sched3(START, (127, 127, 127)), None),
        "runaway: echo MODE 123 FDBK 127 ALT 127 BW 0 AGE 127, flanger, reverb, heavy bands MIX 127":
            (dict(VC.BASE, **allfx, **VC.comp(32, 24, 32, 64, 3, 127), DEL_TIME=10, DEL_X=6, DEL_WID=0, DEL_FDBK=127,
                  DEL_HPF=64, DEL_LPF=0), 127, prog, sched3(([(10, 8, 0, 20, 64)] * 3, (68, 44)), (127, 127, 127)), None),
        "worst: noise, every band knob and each MIX moving, every echo and flanger knob jumping, key SCS 18":
            (dict(VC.BASE, **allfx, **VC.W3), 64, noise, list3([(moving17(b), mixes_apart(b)) for b in range(nb)]), everything),
    }
    for name, (words, age, inp, sched, each) in cases.items():
        t0 = time.time()
        vm = V.candidate_vm(img[0], cache=img[2])
        vm.lib.vm_set_profile(vm.h, 1)
        run = FxTailRun(vm, words, VC.ROUTE_INFX, sched)
        struct.pack_into("<H", run.r.d.frame, 0xa18, age << 8)
        jumps.clear()
        nan = 0
        pk = pkdel = 0.0
        for b in range(nb):
            if each:
                each(b, run.r.d.frame)
            run.tblock(inp[0][32 * b:32 * b + 32], inp[1][32 * b:32 * b + 32], b, paint=(b % 50 == 7))
            o = run.out[0] + run.out[1]
            run.out[0].clear()
            run.out[1].clear()
            for x in o:
                if x != x or abs(x) == float("inf"):
                    nan += 1
                elif abs(x) > pk:
                    pk = abs(x)
            d = struct.unpack("<64f", run.dl.pop())
            run.chr.clear()
            for x in d:
                if x != x or abs(x) == float("inf"):
                    nan += 1
                elif abs(x) > pkdel:
                    pkdel = abs(x)
            run.gr.clear()
            run.tx_pages.clear()
        ring = vm.rdf(SE_SLOT + RING_OFF, RING_FLOATS)
        rmax = max((abs(x) for x in ring if x == x), default=0.0)
        rbad = sum(1 for x in ring if x != x or abs(x) == float("inf"))
        # the placement at run time: what ran in the four L2 ranges
        ran_l2, ran_outside_ours = 0, []
        for pc in vm.decoded:
            if not any(lo <= pc < hi for lo, hi in l2_ranges):
                continue
            hcount = vm.pages[pc >> 12][pc & 0xfff].hits
            if not hcount:
                continue
            ran_l2 += hcount
            if not any(lo <= pc < hi for lo, hi, _ in l2):
                ran_outside_ours.append("%#x" % pc)
        pb = run.r.per_block[1:]
        e = {"blocks": nb, "output_peak": round(pk, 4), "echo_wet_peak": round(pkdel, 4), "tape_peak": round(rmax, 4),
             "nan_inf": nan + rbad, "insns_mean": round(sum(pb) / len(pb)), "insns_max": max(pb),
             "est_load_mean_pct": round(100 * VC.LOAD(sum(pb) / len(pb)), 1), "est_load_max_pct": round(100 * VC.LOAD(max(pb)), 1),
             "stack_bytes": run.stack, "l2_instructions_executed": ran_l2,
             "l2_executed_outside_our_functions": ran_outside_ours[:10],
             "trips": vm.rd32(SLOT + OFF["trips"]), "seconds": round(time.time() - t0)}
        e["ok"] = (nan + rbad == 0 and pk <= 0.6931 and pkdel <= CEIL and rmax <= 1.0 + 1e-6 and not ran_outside_ours
                   and run.stack < 3000)
        res[name] = e
        oks.append(e["ok"])
        print("(w)", name[:80], e, flush=True)
        del vm, run
        gc.collect()
        memguard.check(4096)
    res["calibration"] = ("load = (1.30 x instructions + 11k) / 666,667 (notes/T6.4 4.4) [inferred]; for CMP2 it said ~74 % "
                          "where the unit measured ~50-60 % (CP2D)")
    res["ok"] = all(oks)
    return res["ok"], res


# ---------------------------------------------------------------- (s) stores

def proof_s(imgs, q):
    img = imgs["SC01"]
    info = img[1]
    sbytes = info["layout_check"]["slot_sizes_from_image"]["cmp_slot_bytes"]
    tbytes = info["layout_check"]["slot_sizes_from_image"]["cmp_tail_bytes"]
    save = (0x2e6800, 0x2e6800 + BD.stub.SAVE_BYTES, "stub save area")
    stack = (V.STACK_LO, V.STACK_TOP, "stack")
    allowed = {
        "chr": [stack, save, (0x300000, 0x308400, "FlgSlot"), (CHR_OUT[0], CHR_OUT[0] + 0x80, "chorus out L"),
                (CHR_OUT[1], CHR_OUT[1] + 0x80, "chorus out R")],
        "del": [stack, save, (SE_SLOT, SE_SLOT + SE_BYTES, "SeSlot"), (DEL_OUT[0], DEL_OUT[0] + 0x80, "delay out L"),
                (DEL_OUT[1], DEL_OUT[1] + 0x80, "delay out R")],
        "comp": [stack, save, (SLOT, SLOT + sbytes, "CmpSlot"), (TST, TST + tbytes, "CmpTail"),
                 (0x25ba68, 0x25ba68 + 0x80, "out L"), (0x25bae8, 0x25bae8 + 0x80, "out R"), (VC.GR_Y2, VC.GR_Y2 + 4, "0x257c1c"),
                 (VC.TX[0] + 0x10, VC.TX[0] + 0x18, "reply page 0"), (VC.TX[1] + 0x10, VC.TX[1] + 0x18, "reply page 1")]}
    ours = [(0x2dd600, 0x2e1c40, "block-1 code"), (0x2e2900, 0x2e6000, "code4 (glue, cmp_tail, boot-time init)"),
            (0x2e6000, 0x2e6044, "constant data"), (0x308400, 0x315f8c, "block-2 code"),
            (0x315f8c, 0x318000, "0x315f8c-0x318000")] + [
        (BD.E.sw_to_byte(lo), BD.E.sw_to_byte(hi), "L2 " + n) for n, (lo, hi) in BD.RECIPES["SC01"]["l2"].items()]
    L, R = VC.load_input("mix", 130 * 32 / SR)
    mv = {}
    rn = random.Random(3)

    def jump(b, fr):
        if b % 5 == 0:
            if b not in mv:
                mv[b] = {k: rn.randrange(128) for k in ("DEL_TIME", "DEL_WID", "DEL_FDBK", "DEL_HPF", "DEL_LPF",
                                                        *VC.FL.KNOBS)}
                mv[b]["DEL_X"] = rn.randrange(7)
            for k, v in mv[b].items():
                VC.put(fr, k, v << 8)
            struct.pack_into("<H", fr, 0xa18, ((b * 7) % 128) << 8)
    words = dict(dict(VC.BASE, **VC.FXW, **VC.comp(32, 24, 32, 64, 3, 64)), DEL_X=6, DEL_FDBK=100, CHR_DEL=40)
    census = {}
    for which in ("chr", "del", "comp"):
        vm = V.candidate_vm(img[0], cache=img[2])
        run = FxTailRun(vm, words, VC.ROUTE_INFX, sched3(DISTINCT, (20, 64, 127)))
        log = []
        for b in range(125):
            jump(b, run.r.d.frame)
            sel = b in (0, 1, 2, 3, 40, 41, 60, 61, 80, 81, 120, 121)
            wl = [] if sel else None
            fw = (which, []) if (sel and which != "comp") else None
            run.tblock(L[32 * b:32 * b + 32], R[32 * b:32 * b + 32], b, watch=wl if which == "comp" else None,
                       **({"fxwatch": fw} if fw else {}))
            if wl is not None and which == "comp":
                log += wl
            if fw:
                log += fw[1]
            run.out[0].clear(); run.out[1].clear(); run.chr.clear(); run.dl.clear(); run.gr.clear()
        al = allowed[which]
        stray = [(pc, ad) for pc, ad, va in log if not any(lo <= ad < hi for lo, hi, _ in al)]
        by = {}
        for pc, ad, va in log:
            nm = next((n_ for lo, hi, n_ in al if lo <= ad < hi), "OTHER")
            by[nm] = by.get(nm, 0) + 1
        into_ours = [ad for pc, ad, va in log if any(lo <= ad < hi for lo, hi, _ in ours)]
        census[which] = {"stores": len(log), "by_range": by, "outside": ["%#x@%#x" % (ad, pc) for pc, ad in stray[:10]],
                         "outside_count": len(stray), "into_our_code_or_the_forbidden_range": len(into_ours)}
        print("(s)", which, census[which], flush=True)
        del vm, run
        gc.collect()
    # whole blocks: no store into our code ranges (one watch range at a time)
    whole = {}
    for lo, hi, nm in ours:
        vm = V.candidate_vm(img[0], cache=img[2])
        run = FxTailRun(vm, words, VC.ROUTE_INFX, sched3(DISTINCT, (20, 64, 127)))
        from vm import watch as W, watch_log
        W(vm, lo, hi)
        n = 0
        for b in range(60):
            jump(b, run.r.d.frame)
            run.tblock(L[32 * b:32 * b + 32], R[32 * b:32 * b + 32], b)
            n += len(watch_log(vm))
            W(vm, lo, hi)
            run.out[0].clear(); run.out[1].clear(); run.chr.clear(); run.dl.clear(); run.gr.clear()
        whole[nm] = n
        del vm, run
        gc.collect()
    print("(s) stores into our code over 60 whole blocks:", whole, flush=True)
    ok = (all(v["outside_count"] == 0 and v["into_our_code_or_the_forbidden_range"] == 0 for v in census.values())
          and all(v == 0 for v in whole.values()))
    return ok, {"calls": census, "whole_blocks_stores_into": whole, "ok": ok}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--only", help="comma-separated proofs (a,b,e,w,s)")
    a = ap.parse_args()
    only = set(a.only.split(",")) if a.only else set("abews")
    t_all = time.time()
    imgs = {"SC01": load_image(OUT, "SC01"), "CMP3": load_image(CMP3_DIR, "CMP3"), "SE01": load_image(SE01_DIR, "SE01")}
    # SC01 was built from the sources now in the tree
    stale = [s for s, h in imgs["SC01"][1]["sources"].items() if BD.sha((ROOT / s).read_bytes()) != h]
    if stale:
        raise SystemExit(f"SC01 was built from other sources ({stale}): run build.py SC01")
    OFF.update(VC.offsets(OUT / "offsets", "CMP3"))      # CmpSlot is CMP3's (mbcomp.c, the glue's CmpSlot unchanged)
    if imgs["SC01"][1]["layout_check"]["slot_sizes_from_image"]["cmp_slot_bytes"] != \
            imgs["CMP3"][1]["layout_check"]["slot_sizes_from_image"]["cmp_slot_bytes"]:
        raise SystemExit("CmpSlot's size differs between CMP3 and SC01")
    res = {"name": "SC01", "section7_sha256": imgs["SC01"][1]["section7"]["sha256"],
           "cmp3_section7_sha256": imgs["CMP3"][1]["section7"]["sha256"],
           "se01_section7_sha256": imgs["SE01"][1]["section7"]["sha256"], "proofs_run": "".join(sorted(only))}
    oks = {}
    for k, fn in (("s", proof_s), ("e", proof_e), ("b", proof_b), ("w", proof_w), ("a", proof_a)):
        if k in only:
            t0 = time.time()
            oks[k], res[k] = fn(imgs, a.quick)
            res[k + "_seconds"] = round(time.time() - t0)
            print("== (%s) %s, %.0fs" % (k, "PASS" if oks[k] else "FAIL", time.time() - t0), flush=True)
            part = OUT / ("verify-%s.json" % ("".join(sorted(only)) + ("-quick" if a.quick else "")))
            part.write_text(json.dumps(dict(res, ok=all(oks.values()), seconds=round(time.time() - t_all)), indent=1))
    res["ok"] = all(oks.values())
    res["seconds"] = round(time.time() - t_all)
    res["peak_rss_mb"] = round(memguard.peak_rss_mb())
    name = "verify.json" if not a.only and not a.quick else "verify-%s.json" % ("".join(sorted(only)) + ("-quick" if a.quick else ""))
    (OUT / name).write_text(json.dumps(res, indent=1))
    print("VERDICT", "PASS" if res["ok"] else "FAIL", res["seconds"], "s", flush=True)
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
