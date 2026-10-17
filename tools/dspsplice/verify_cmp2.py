#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""T6.3 CMP2 proof, the frame-tail part (notes/T6.3-cmp2.md section 3). verify_cmp.py CMP2 runs CMP1's proofs (a)-(h)
on CMP2 with the tails zero (the fallback); this runs CMP2 with tails, in tools/sharcvm, the DSP image as the unit
loads it (out/T6.3/CMP2-dsp/section7.bin), next to CMP1's (out/T6.3/CMP1-dsp/section7.bin) where a comparison needs it.

    python3 tools/dspsplice/verify_cmp2.py [--quick] [--only r,w,z,c,b,s,d,n,g,e] [--e2e DIR]

  (r) the reader, called directly (cmp_tail_fetch): no tail, one buffer, both (either newer), torn, sequence wrap,
      the held sequence (values kept), an older one, a 40,000-frame pause, all ones, a wrong check word, 6,000 random
      tails; the values cmp_tail_apply then gives (word / 256; ATK and REL rounded to the knob step)
  (w) real wire frames (the emulator's, out/T6.3/CMP2/cf/*.wire.bin, and T6.4's prototype ones) -> exact knob values,
      through the reader alone and through the whole block handler
  (z) invalid tails (random, magic only, one bit flipped, all ones, zero) over 1,000 blocks per setting: the handler
      output and the reply pages bit for bit CMP1's (word 0, the cycle count, aside), the reader never takes one
  (c) read census (the read-watch build of sharcvm, out/T6.4/store-frame/build/): which instructions read the tail
      bytes rx + 0xa80..0xabb of either receive buffer, with valid tails, the FX on, random globals and voices
  (b) SHARC vs Mac (the same C, -DCMP_NATIVE, the image's tables, the same tails) on distinct band settings, a key,
      every band knob and both crossovers moving every block: output <= -100 dB re peak, meters, reply words
  (s) one band's knob turned: only that band's gain reduction moves (the others bit for bit); crossover and band-knob
      sweeps as the ColdFire sends them (the stock 3 % per frame glide of an encoder turn) and as jumps: the energy
      above 12 kHz of a two-sine input against the static run (clicks and zipper show there)
  (d) cost per block (mean, max) with tails: typical, distinct bands, every band knob and crossover moving every block
      with MIX 0 <-> 64, noise and the FX knobs jumping (self and key); the stack
  (n) NaN/inf in tail mode (a NaN input: the guard; a corrupted magic: the quiet re-init takes the tail's values in
      the same block), the extreme tail (every word 0xffff, valid): clamped knobs, finite output, no trip
  (m) MIX 0 in tail mode (distinct bands; every band knob and crossover moving): the whole handler output bit for
      bit FTD1's (the plan's MIX 0 rule: at MIX 0 the band settings cannot reach the output)
  (g) every store of the compressor call in tail mode inside the planned ranges (CmpSlot, CmpTail, stack, stub save
      area, outputs, meter word, reply +0x10..+0x17); CmpTail zero after the effect init (cold start: the fallback)
  (e) end to end, with --e2e DIR (scripts/t63c_e2e.py's capture): the emulator's wire frames, in order, through the
      handler: the band whose knob the script turned moves, the others do not
Writes out/T6.3/CMP2-dsp/verify2.json and prints a VERDICT.
"""
import argparse
import ctypes as C
import glob
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
import verify_cmp as VC  # noqa: E402

VC.configure("CMP2")
V = VC.V
FB = V.fullblock
RX = (FB.RX_BASE, FB.RX_BASE + 0x1000)
TAIL, NW, MAGIC = 0xa80, 30, 0xC3B1
TST = VC.TAILST[0]
SLOT = VC.SLOT
RWLIB = ROOT / "out/T6.4/store-frame/build/libsharcvm_rw.dylib"
SR = VC.SR
OFF = {}
# band settings: per band (THR, RAT, ATK, REL, MUP), and (XLO, XHI)
DEFAULTS = ([(32, 4, 24, 32, 64)] * 3, (68, 44))      # the rows' new-kit defaults (param-rows 0.2)
DISTINCT = ([(20, 3, 10, 40, 50), (60, 6, 40, 60, 30), (90, 8, 80, 100, 90)], (90, 70))
LOW_ONLY = ([(10, 8, 0, 20, 64), (32, 0, 24, 32, 0), (32, 0, 24, 32, 0)], (68, 44))
NAMES17 = ["L THR", "L RAT", "L ATK", "L REL", "L MUP", "XLO", "M THR", "M RAT", "M ATK", "M REL", "M MUP", "XHI",
           "H THR", "H RAT", "H ATK", "H REL", "H MUP"]
# the knob word (mbcomp's knob[] index) behind each of the 17 tail values (MB_P(b, p): THR 0 ATK 1 REL 2 MUP 3 RAT 4)
KIDX = [0, 4, 1, 2, 3, 15, 5, 9, 6, 7, 8, 16, 10, 14, 11, 12, 13]
STEP = {2, 3, 8, 9, 14, 15}                            # ATK, REL: rounded to the knob step


def knobs17(setting):
    (b0, b1, b2), (xlo, xhi) = setting
    return list(b0) + [xlo] + list(b1) + [xhi] + list(b2)


def words_of(vals17):
    """knob values (fractions allowed: a glide) -> the 27 tail values k 70..96 (word = value x 256)"""
    return [int(round(v * 256)) & 0xffff for v in vals17] + [0] * 10


def tail_words(seq, vals27):
    w = [MAGIC, seq & 0xffff] + [v & 0xffff for v in vals27]
    w.append((~sum(w)) & 0xffff)
    assert len(w) == NW
    return w


def tb(words):
    return struct.pack("<30H", *words)


def expect_knobs(vals27):
    """the knob values cmp_tail_apply must give for these tail values (clamped as mbcomp_set clamps)"""
    out = []
    for j in range(17):
        w = vals27[j] & 0xffff
        v = float((w + 0x80) >> 8) if j in STEP else VC.f32(w * 0.00390625)
        hi = 8.0 if j in (1, 7, 13) else 127.0
        out.append(min(max(v, 0.0), hi))
    return out


def knobs_now(vm):
    k = vm.rdf(SLOT + OFF["mb.knob"], 19)
    return [k[i] for i in KIDX]


def glide(targets, nb, start=None):
    """the ColdFire's stock smoother for an encoder turn: each frame the word moves 3 % of the way to the stored
    value (FUN_400db28e: 0x03d7 / 0x7c29 of 0x8000, unity gain on the unit, T3.3); targets(b) -> 17 knob values"""
    s = [float(x) * 256 for x in (start or targets(0))]
    out = []
    for b in range(nb):
        t = targets(b)
        s = [x + (0x03d7 / 0x8000) * (256 * y - x) for x, y in zip(s, t)]
        out.append([x / 256 for x in s])
    return out


class TailRun(VC.Run):
    """VC.Run with a frame tail per block (sched(b) -> 30 words, or None for zeros) in the frame the block reads,
    the SPI half alternating as the DMA does on the unit (so the other buffer holds the previous frame), both
    receive buffers' tails recorded at the compressor call, and each band's gain reduction (dB) after each block"""

    def __init__(self, vm, words, raw, sched, keep=False, alternate=True):
        super().__init__(vm, words, raw, keep=keep)
        self.sched, self.alt, self.tails, self.gr = sched, alternate, [], []

    def tblock(self, L, R, b, inject=None, **kw):
        vm = self.vm
        if self.alt:
            vm.wr32(FB.SPI_HALF, b & 1)
        w = self.sched(b) if self.sched else None
        struct.pack_into("<30H", self.r.d.frame, TAIL, *(w if w is not None else [0] * NW))

        def inj(bb, vm_, ptrs):
            self.tails.append((vm_.read(RX[0] + TAIL, 60), vm_.read(RX[1] + TAIL, 60)))
            if inject:
                inject(bb, vm_, ptrs)
        o = self.block(L, R, inject=inj, b=b, **kw)
        self.gr.append(tuple(vm.rdf(SLOT + OFF["gr0"], 3)))
        return o

    def tprocess(self, L, R, each=None, **kw):
        for b, i in enumerate(range(0, len(L) // 32 * 32, 32)):
            if each:
                each(b, self.r.d.frame)
            self.tblock(L[i:i + 32], R[i:i + 32], b, **kw)
        return self.out


class NativeTail(VC.Native):
    def __init__(self, lib, tab, slot_bytes, tail_bytes):
        super().__init__(lib, tab, slot_bytes=slot_bytes)
        lib.cmp_native_tail_ptr.restype = C.c_void_p
        lib.cmp_native_rx_tail.restype = C.c_void_p
        self.tbytes = tail_bytes

    def init(self):
        C.memset(self.lib.cmp_native_tail_ptr(), 0, self.tbytes)
        super().init()

    def replay_tails(self, cap, tails, pre=None):
        def pre2(b, slot):
            for h in (0, 1):
                C.memmove(self.lib.cmp_native_rx_tail(h), tails[b][h], 60)
            if pre:
                pre(b, slot)
        return self.replay(cap, pre=pre2)


def const_sched(vals27, seq0=1):
    return lambda b: tail_words(seq0 + b, vals27)


def list_sched(per_block, seq0=1):
    return lambda b: tail_words(seq0 + b, words_of(per_block[min(b, len(per_block) - 1)]))


def hp12k(x):
    """4th-order Butterworth high-pass at 12 kHz (two RBJ biquads, Q 0.5412 and 1.3066), direct form I"""
    y = x
    for q in (0.54119610, 1.30656296):
        w0 = 2 * math.pi * 12000 / SR
        al = math.sin(w0) / (2 * q)
        cw = math.cos(w0)
        b0, b1, b2 = (1 + cw) / 2, -(1 + cw), (1 + cw) / 2
        a0, a1, a2 = 1 + al, -2 * cw, 1 - al
        b0, b1, b2, a1, a2 = b0 / a0, b1 / a0, b2 / a0, a1 / a0, a2 / a0
        o, x1, x2, y1, y2 = [], 0.0, 0.0, 0.0, 0.0
        for s in y:
            v = b0 * s + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2
            x2, x1, y2, y1 = x1, s, y1, v
            o.append(v)
        y = o
    return y


def hf_profile(out, skip=4800, win=256):
    """per 256-sample window after the first 100 ms: the output's RMS above 12 kHz against its whole RMS in the same
    window (dB); -> the max and the median over the windows, and the max HF RMS in dBFS"""
    x = out[0][skip:]
    h = hp12k(out[0])[skip:]
    rel, hdb = [], []
    for i in range(0, len(h) - win, win):
        hr = math.sqrt(sum(v * v for v in h[i:i + win]) / win)
        sr_ = math.sqrt(sum(v * v for v in x[i:i + win]) / win)
        hdb.append(20 * math.log10(max(hr, 1e-15)))
        rel.append(hdb[-1] - 20 * math.log10(max(sr_, 1e-15)))
    s = sorted(rel)
    return {"max_re_window_db": round(s[-1], 1), "median_re_window_db": round(s[len(s) // 2], 1),
            "max_hf_dbfs": round(max(hdb), 1)}


def two_sines(n, f1=150.0, f2=2000.0, a=0.002):
    L = [a * math.sin(2 * math.pi * f1 * i / SR) + a * math.sin(2 * math.pi * f2 * i / SR) for i in range(n)]
    return L, list(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--only", help="comma-separated proofs (r,w,z,c,b,s,d,n,g,e)")
    ap.add_argument("--e2e", help="directory of scripts/t63c_e2e.py's capture (frames.bin, frames.json)")
    a = ap.parse_args()
    only = set(a.only.split(",")) if a.only else set("rwzcbsdnmg" + ("e" if a.e2e else ""))
    q = a.quick
    t_all = time.time()
    stream, info, cache = VC.image("CMP2")
    s1, i1, c1 = VC.image("CMP1")
    sym = {k: int(v, 16) for k, v in info["symbols"].items()}
    bdir = VC.OUT / "build"
    OFF.update(VC.offsets(bdir))
    tbytes = info["layout_check"]["slot_sizes_from_image"]["cmp_tail_bytes"]
    res = {"name": "CMP2", "section7_sha256": info["section7"]["sha256"], "cmp1_section7_sha256": i1["section7"]["sha256"],
           "cmp_tail_bytes": tbytes, "proofs_run": "".join(sorted(only))}
    vm_cmp = lambda: V.candidate_vm(stream, cache=cache)       # noqa: E731
    vm_cmp1 = lambda: V.candidate_vm(s1, cache=c1)             # noqa: E731
    fetch, apply_ = sym["cmp_tail_fetch"], sym["cmp_tail_apply"]
    mbp = SLOT + OFF["mb"]
    oks = {}

    # ---- (r) the reader
    if "r" in only:
        t0 = time.time()
        A = vm_cmp()
        vals = [((7 * i + 3) & 0x7f) << 8 for i in range(27)]
        vals2 = [v ^ 0x100 for v in vals]
        zero = [0] * NW

        def rcase(t0w, t1w, held=None, mark=False):
            A.write(RX[0] + TAIL, tb(t0w))
            A.write(RX[1] + TAIL, tb(t1w))
            A.write(TST, bytes(tbytes))
            if held is not None:
                A.wr32(TST, 1)
                A.wr32(TST + 4, held)
            if mark:
                A.write(TST + 24 + 120, b"\xa5" * 60)        # w[] = a marker: kept when nothing is taken
            r = A.call(fetch)
            st = [A.rd32(TST + 4 * i) for i in range(6)]
            w = A.read(TST + 24 + 120, 60)
            return {"ret": r, "have": st[0], "seq": st[1], "accepted": st[2], "same": st[3], "none": st[4], "_w": w}
        old, new = tail_words(100, vals), tail_words(101, vals2)
        torn = list(new)
        torn[10] ^= 0x0300
        badchk = list(new)
        badchk[29] ^= 1
        S = 1000
        pause = tail_words(S + 40000, vals2)
        cases = {
            "no tail (zero, both buffers)": (rcase(zero, zero), dict(ret=0, have=0, none=1)),
            "valid in buffer 0 only": (rcase(new, zero), dict(ret=1, seq=101, accepted=1)),
            "valid in buffer 1 only": (rcase(zero, new), dict(ret=1, seq=101, accepted=1)),
            "old in 0, new in 1": (rcase(old, new), dict(ret=1, seq=101)),
            "new in 0, old in 1": (rcase(new, old), dict(ret=1, seq=101)),
            "torn new in 0, old in 1": (rcase(torn, old), dict(ret=1, seq=100)),
            "torn new in 0, nothing in 1": (rcase(torn, zero), dict(ret=0, have=0, none=1)),
            "wrong check word in 0, nothing in 1": (rcase(badchk, zero), dict(ret=0, none=1)),
            "all ones in both": (rcase([0xffff] * NW, [0xffff] * NW), dict(ret=0, none=1)),
            "sequence wrap 0xffff in 0, 0x0000 in 1": (rcase(tail_words(0xffff, vals), tail_words(0, vals2)), dict(ret=1, seq=0)),
            "sequence wrap 0x0000 in 0, 0xffff in 1": (rcase(tail_words(0, vals2), tail_words(0xffff, vals)), dict(ret=1, seq=0)),
            "both buffers the same tail (a frame without a new tail)": (rcase(new, new), dict(ret=1, seq=101, accepted=1)),
            "the held sequence (values kept)": (rcase(new, zero, held=101, mark=True), dict(ret=1, seq=101, same=1, accepted=0)),
            "an older sequence than the held one (taken: equality rule)": (rcase(old, zero, held=101), dict(ret=1, seq=100, accepted=1)),
            "a 40,000-frame pause (held 1000, tail 41000)": (rcase(pause, zero, held=S), dict(ret=1, seq=(S + 40000) & 0xffff, accepted=1)),
            "no valid tail after one was held (values kept, still on)": (rcase(zero, zero, held=101, mark=True), dict(ret=1, none=1)),
        }
        rr, rok = {}, True
        for k, (c, want) in cases.items():
            okc = all(c[f] == v for f, v in want.items())
            if "values kept" in k:
                okc = okc and c["_w"] == b"\xa5" * 60
            elif want.get("seq") == 101 and want.get("ret") == 1:
                okc = okc and c["_w"] == tb(new)
            c.pop("_w")
            c["as_designed"] = okc
            rok = rok and okc
            rr[k] = c
        # the prototype's rule ("newer than held") on the pause: what it would have done
        proto_newer = ((S + 40000 - S) & 0xffff)
        rr["the prototype's rule on the pause"] = {"signed_difference": proto_newer - 0x10000 if proto_newer >= 0x8000 else proto_newer,
                                                   "would_take_it": 0 < proto_newer < 0x8000}
        # the values: fetch new, then apply to the slot's compressor
        A2 = vm_cmp()
        A2.write(RX[0] + TAIL, tb(new))
        A2.write(TST, bytes(tbytes))
        A2.call(fetch)
        A2.call(apply_, R4=mbp)
        got = knobs_now(A2)
        rr["apply: knob values for the new tail"] = {"got": got, "expected": expect_knobs(vals2),
                                                     "exact": got == expect_knobs(vals2)}
        rok = rok and got == expect_knobs(vals2)
        # every row value and the glide's fractions: words v << 8 for all v and a sweep of odd words
        exact_all = True
        for base in range(0, 0x8000, 0x100 * 7):
            vv = [((base + 0x100 * i) % 0x8000) for i in range(27)]
            vv[1], vv[7], vv[13] = (base >> 8) % 9 << 8, ((base >> 8) + 3) % 9 << 8, ((base >> 8) + 5) % 9 << 8
            A2.write(RX[0] + TAIL, tb(tail_words(base, vv)))
            A2.call(fetch)
            A2.call(apply_, R4=mbp)
            exact_all = exact_all and knobs_now(A2) == expect_knobs(vv)
        rnd = random.Random(5)
        for i in range(200):
            vv = [rnd.randrange(0x8000) for _ in range(27)]
            A2.write(RX[1] + TAIL, tb(tail_words(5000 + i, vv)))
            A2.write(RX[0] + TAIL, tb(zero))
            A2.call(fetch)
            A2.call(apply_, R4=mbp)
            exact_all = exact_all and knobs_now(A2) == expect_knobs(vv)
        rr["apply: every row value v << 8 and 200 random words (glide fractions)"] = exact_all
        rok = rok and exact_all
        # garbage
        acc = 0
        A3 = vm_cmp()
        for i in range(3000):
            A3.write(TST, bytes(tbytes))
            A3.write(RX[0] + TAIL, bytes(rnd.randrange(256) for _ in range(60)))
            A3.write(RX[1] + TAIL, struct.pack("<H", MAGIC) + bytes(rnd.randrange(256) for _ in range(58)))
            acc += A3.call(fetch)
        rr["random tails taken, of 6,000 (3,000 with the right magic)"] = acc
        rok = rok and acc == 0
        A.write(RX[0] + TAIL, tb(new))
        A.write(RX[1] + TAIL, tb(old))
        A.write(TST, bytes(tbytes))
        n0 = A.icount
        A.call(fetch)
        n1 = A.icount
        A.call(apply_, R4=mbp)
        n2 = A.icount
        A.call(fetch)
        n3 = A.icount
        rr["instructions: fetch taking a tail, apply (17 sets), fetch with the held sequence"] = [n1 - n0, n2 - n1, n3 - n2]
        rr["ok"] = rok
        res["r_reader"] = rr
        oks["r"] = rok
        print("(r)", rok, rr["instructions: fetch taking a tail, apply (17 sets), fetch with the held sequence"],
              "%.0fs" % (time.time() - t0), flush=True)

    # ---- (w) real wire frames
    if "w" in only:
        t0 = time.time()
        files = sorted(glob.glob(str(ROOT / "out/T6.3/CMP2/cf/*hook*.wire.bin"))) + \
            sorted(glob.glob(str(ROOT / "out/T6.4/store-frame/cf/*-hook.wire.bin")))
        wr, wok = {}, bool(files)
        for f in files:
            wire = Path(f).read_bytes()
            assert len(wire) == FB.FRAME_LEN
            be = struct.unpack(">%dH" % (FB.FRAME_LEN // 2), wire)
            t = list(be[TAIL // 2:])
            vals27 = t[2:29]
            dsp = b"".join(wire[i:i + 2][::-1] for i in range(0, FB.FRAME_LEN, 2))
            A = vm_cmp()
            A.write(RX[0], dsp)
            A.write(RX[1], bytes(FB.FRAME_LEN))
            A.write(TST, bytes(tbytes))
            r = A.call(fetch)
            A.call(apply_, R4=mbp)
            got = knobs_now(A)
            exp = expect_knobs(vals27)
            # the whole handler: the wire frame as the frame of every block (payload and tail)
            B = vm_cmp()
            run = VC.Run(B, dict(VC.BASE), None, keep=False)
            run.r.d.frame[:] = dsp
            L, R = VC.load_input("mix", 20 * 32 / SR)
            run.process(L, R)
            got_h = knobs_now(B)
            e = {"tail_valid": t[0] == MAGIC and ((~sum(t[:29])) & 0xffff) == t[29], "seq": t[1],
                 "k70_86_words": ["%04x" % v for v in vals27[:17]], "reader_ret": r, "knobs": got,
                 "exact": got == exp, "handler_knobs_exact": got_h == exp,
                 "handler_tail_state": [B.rd32(TST + 4 * i) for i in range(6)]}
            wr[str(Path(f).relative_to(ROOT))] = e
            wok = wok and e["tail_valid"] and r == 1 and e["exact"] and e["handler_knobs_exact"]
        wr["ok"] = wok
        res["w_wire_frames"] = wr
        oks["w"] = wok
        print("(w)", wok, len(files), "%.0fs" % (time.time() - t0), flush=True)

    # ---- (z) invalid tails: CMP1's output bit for bit
    if "z" in only:
        t0 = time.time()
        nb = 1000 if not q else 200
        rnd = random.Random(11)

        def bad_tail(b):
            k = b % 6
            if k == 0:
                return [rnd.randrange(0x10000) for _ in range(NW)]
            if k == 1:
                return [MAGIC] + [rnd.randrange(0x10000) for _ in range(NW - 1)]
            if k == 2:
                w = tail_words(rnd.randrange(0x10000), [rnd.randrange(0x7f01) for _ in range(27)])
                w[rnd.randrange(NW)] ^= 1 << rnd.randrange(16)
                return w
            if k == 3:
                return [0xffff] * NW
            if k == 4:
                w = tail_words(b, [0x4000] * 27)
                w[0] = 0xC3B2                     # a later layout: refused
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
        zr, zok = {}, True
        for name, (words, raw, each) in zcfg.items():
            seqs = []
            rnd.seed(hash(name) & 0xffff)
            tails = [bad_tail(b) for b in range(nb)]
            outs = []
            for vmf in (vm_cmp, vm_cmp1):
                run = TailRun(vmf(), words, raw, lambda b: tails[b])
                run.tprocess(prog[0][:nb * 32], prog[1][:nb * 32], each=each, tx_full=True)
                outs.append(run)
                seqs.append([run.vm.rd32(TST + 4 * i) for i in range(6)])
            a2, a1 = outs
            same = a2.out == a1.out
            txd = set()
            for pa, pb in zip(a2.tx_pages, a1.tx_pages):
                for i in range(0, len(pa), 4):
                    if pa[i:i + 4] != pb[i:i + 4]:
                        txd.add(i % 0x1000)
            e = {"blocks": nb, "output_bit_identical_to_CMP1": same, "reply_bytes_that_differ": sorted(txd),
                 "tail_state_CMP2": seqs[0][:6]}
            e["ok"] = same and txd <= {0x0} and seqs[0][0] == 0 and seqs[0][2] == 0
            zr[name] = e
            zok = zok and e["ok"]
            print("  (z)", name, e["ok"], sorted(txd), seqs[0][:6], flush=True)
        zr["ok"] = zok
        res["z_invalid_tails_vs_CMP1"] = zr
        oks["z"] = zok
        print("(z)", zok, "%.0fs" % (time.time() - t0), flush=True)

    # ---- (c) read census (read-watch build)
    if "c" in only:
        t0 = time.time()
        import vm as VMMOD
        saved = VMMOD.LIB, VMMOD.build_lib
        VMMOD.LIB, VMMOD.build_lib = RWLIB, (lambda force=False: None)
        try:
            A = V.VM()
            A.load_dn2()
            V.load_stream(A, stream)
            A.call(V.FXINIT, max_insns=10 ** 9)
        finally:
            VMMOD.LIB, VMMOD.build_lib = saved
        lib = A.lib
        lib.vm_rw_add.argtypes = [C.c_void_p, C.c_uint32, C.c_uint32]
        lib.vm_rw_get.argtypes = [C.c_void_p, C.c_int] + [C.POINTER(C.c_uint32)] * 3
        assert lib.vm_rw_add(A.h, RX[0], RX[0] + 0x2000) == 0
        rnd = random.Random(64)
        nb = 600 if not q else 150
        vals = [[rnd.randrange(0x7f01) for _ in range(27)] for _ in range(nb)]
        run = TailRun(A, dict(VC.BASE, **VC.FXW, **VC.TYPICAL), VC.ROUTE_INFX,
                      lambda b: tail_words(b, vals[b]) if b % 7 else [rnd.randrange(0x10000) for _ in range(NW)])

        def each(b, fr):
            for o in range(0x9fa, 0xa54, 2):
                if rnd.random() < 0.2 and o not in (0xa48, 0xa4a):
                    struct.pack_into("<H", fr, o, rnd.choice([0, 0x3f00, 0x7f00, rnd.randrange(0x8000)]))
            if b % 5 == 4:
                for o in range(0xda, 0x9fa, 2):
                    struct.pack_into("<H", fr, o, rnd.randrange(0x8000))
        L = [rnd.uniform(-0.3, 0.3) for _ in range(nb * 32)]
        run.tprocess(L, list(reversed(L)), each=each)
        cnt, first, last = (C.c_uint32 * 0x2000)(), (C.c_uint32 * 0x2000)(), (C.c_uint32 * 0x2000)()
        lib.vm_rw_get(A.h, 0, cnt, first, last)
        lo, hi = fetch, sym.get("cmp_tail_apply", fetch + 0x1000)
        readers = {}
        for h in (0, 1):
            for o in range(TAIL, FB.FRAME_LEN):
                i = h * 0x1000 + o
                if cnt[i]:
                    for pc in (first[i], last[i]):
                        readers.setdefault("%#x" % pc, 0)
                        readers["%#x" % pc] += 1
        outside = {p: n for p, n in readers.items() if not (lo <= int(p, 16) < hi)}
        tail_bytes_read = sum(1 for h in (0, 1) for o in range(TAIL, FB.FRAME_LEN) if cnt[h * 0x1000 + o])
        cr = {"blocks": nb, "tail_bytes_read_of_120": tail_bytes_read, "reader_pcs": readers,
              "cmp_tail_fetch": "%#x-%#x" % (lo, hi), "read_by_anything_else": outside,
              "tail_state": [A.rd32(TST + 4 * i) for i in range(6)]}
        cr["ok"] = tail_bytes_read == 120 and not outside
        res["c_read_census"] = cr
        oks["c"] = cr["ok"]
        print("(c)", cr["ok"], tail_bytes_read, len(readers), outside, "%.0fs" % (time.time() - t0), flush=True)

    lib = VC.native(bdir, name="CMP2")
    tab = vm_cmp().rdf(VC.TABLES, VC.NTAB)

    def moving_all(b):
        """every band knob and both crossovers moving every block (two settings, alternately)"""
        o = b % 2
        return [(5 + 6 * o, 8 - o, 4 * o, 4 * o, 60 + 8 * o)] * 3, (60 + 6 * o, 40 + 6 * o)

    def moving_vals(b):
        bands, xo = moving_all(b)
        return knobs17((bands, xo))

    # ---- (b) SHARC vs Mac, distinct bands
    if "b" in only:
        t0 = time.time()
        nat = NativeTail(lib, tab, OFF["slot_bytes_mac"], tbytes)
        secs = 2.0 if not q else 0.5
        mix_in = VC.load_input("mix", secs)
        drums_in = VC.load_input("drums", secs)
        nbk = len(mix_in[0]) // 32
        bcfg = {
            "LOW only (MID, HIGH at 1.00), MIX 127, drums": (dict(VC.BASE, **VC.NOFX, **VC.comp(32, 24, 32, 64, 3, 127)),
                                                             VC.ROUTE_IN, drums_in, const_sched(words_of(knobs17(LOW_ONLY)))),
            "three distinct bands, MIX 100, mix, FX on": (dict(VC.BASE, **VC.FXW, **VC.comp(32, 24, 32, 64, 3, 100)),
                                                          VC.ROUTE_INFX, mix_in, const_sched(words_of(knobs17(DISTINCT)))),
            "three distinct bands, key SCS 18, MIX 127, drums, FX on": (
                dict(VC.BASE, **VC.FXW, **VC.comp(32, 24, 32, 64, 3, 127, scs=18)), VC.ROUTE_INFX, drums_in,
                const_sched(words_of(knobs17(DISTINCT)))),
            "every band knob and both crossovers moving every block, MIX 64, mix": (
                dict(VC.BASE, **VC.NOFX, **VC.comp(32, 24, 32, 64, 3, 64)), VC.ROUTE_IN, mix_in,
                list_sched([moving_vals(b) for b in range(nbk)])),
            "an encoder sweep of both crossovers (the ColdFire glide), defaults, MIX 127, mix": (
                dict(VC.BASE, **VC.NOFX, **VC.comp(32, 24, 32, 64, 3, 127)), VC.ROUTE_IN, mix_in,
                list_sched(glide(lambda b: knobs17((DEFAULTS[0], (min(127, b // 4), max(0, 127 - b // 4)))), nbk))),
        }
        br, bok = {}, True
        for name, (words, raw, inp, sched) in bcfg.items():
            run = TailRun(vm_cmp(), words, raw, sched, keep=True)
            run.tprocess(inp[0], inp[1])
            cap = run.cap
            sh = ([], [])
            for c in cap:
                sh[0].extend(c["out"][0])
                sh[1].extend(c["out"][1])
            mac, meters, txs = nat.replay_tails(cap, run.tails)
            cmpr = VC.compare(sh, mac)
            mdiff = max(abs(c["meter"] - m) for c, m in zip(cap, meters))
            txd = 0
            for c, t in zip(cap, txs):
                for (x0, x1), (y0, y1) in zip(c["tx"], t):
                    txd = max(txd, abs((x0 & 0xffff) - (y0 & 0xffff)), abs((x0 >> 16) - (y0 >> 16)),
                              abs((x1 & 0xffff) - (y1 & 0xffff)), abs((x1 >> 16) - (y1 >> 16)))
            gr = [min(g[i] for g in run.gr) for i in range(3)]
            e = {"blocks": len(cap), "max_diff_db_re_peak": cmpr["max_diff_db_re_peak"], "meter_max_diff_db": round(mdiff, 6),
                 "reply_q15_max_diff": txd, "deepest_gr_db_per_band": [round(x, 2) for x in gr],
                 "tail_state": [run.vm.rd32(TST + 4 * i) for i in range(6)]}
            e["ok"] = VC.ok_db(cmpr) and mdiff < 1e-3 and txd <= 2
            br[name] = e
            bok = bok and e["ok"]
            print("  (b)", name, e["max_diff_db_re_peak"], round(mdiff, 6), txd, e["deepest_gr_db_per_band"], flush=True)
        br["ok"] = bok
        res["b_sharc_vs_mac_tails"] = br
        oks["b"] = bok
        print("(b)", bok, "%.0fs" % (time.time() - t0), flush=True)

    # ---- (s) band isolation and smoothness
    if "s" in only:
        t0 = time.time()
        sr_, sok = {}, True
        nb = 900 if not q else 300
        prog = VC.looped(nb)
        L, R = prog[0][:nb * 32], prog[1][:nb * 32]
        words = dict(VC.BASE, **VC.NOFX, **VC.comp(32, 24, 32, 64, 3, 127))
        base = [list(b) for b in DISTINCT[0]]
        ref = TailRun(vm_cmp(), words, VC.ROUTE_IN, const_sched(words_of(knobs17(DISTINCT))))
        ref.tprocess(L, R)
        iso = {}
        for band in range(3):
            for p_, nm in ((0, "THR"), (1, "RAT"), (2, "ATK")):
                bb = [list(x) for x in base]
                bb[band][p_] = {0: 5, 1: 1, 2: 120}[p_]
                per = []
                for b in range(nb):
                    per.append(knobs17((base if b < 300 else bb, DISTINCT[1])))
                run = TailRun(vm_cmp(), words, VC.ROUTE_IN, list_sched(per))
                run.tprocess(L, R)
                moved = [any(x[i] != y[i] for x, y in zip(run.gr[300:], ref.gr[300:])) for i in range(3)]
                before = all(x == y for x, y in zip(run.gr[:300], ref.gr[:300]))
                e = {"bands_whose_gr_differs_after_the_turn": [i for i in range(3) if moved[i]], "identical_before": before}
                e["ok"] = before and moved == [i == band for i in range(3)]
                iso["%s %s" % (("LOW", "MID", "HIGH")[band], nm)] = e
                sok = sok and e["ok"]
        sr_["one knob turned at block 300 (three distinct bands, MIX 127, program)"] = iso
        print("  (s) isolation", all(v["ok"] for v in iso.values()), flush=True)
        # smoothness: the energy above 12 kHz with a two-sine input, encoder turns at a hand's speed (the whole range in
        # about a second), 2 s per run
        nb = 3000 if not q else 600
        n = nb * 32
        L2, R2 = two_sines(n)
        tri = lambda b, per: 127 - abs(127 - (b * 254 // per) % 254)      # noqa: E731  0..127..0 over per blocks
        off_all = ([(32, 0, 24, 32, 0)] * 3, (68, 44))     # every band at 1.00: the compressor is an allpass
        # compressing slowly (ATK 127, REL 127: the gain does not follow the waveform, so compression adds no harmonics
        # of its own and what is left above 12 kHz is a step in a coefficient or a gain)
        slow = ([(0, 8, 127, 127, 0)] * 3, (68, 44))
        sweeps = {
            "A static: crossovers 68 / 44, bands off (an allpass)": ("A", [knobs17(off_all)] * nb),
            "both crossovers swept 0 -> 127 -> 0, 1 s each way, the ColdFire glide, bands off": ("A", glide(
                lambda b: knobs17((off_all[0], (tri(b, nb), tri(b, nb)))), nb)),
            "every band's MUP swept 0 -> 127 -> 0, 1 s each way, the ColdFire glide, bands off": ("A", glide(
                lambda b: knobs17(([(x[0], x[1], x[2], x[3], tri(b, nb)) for x in off_all[0]], off_all[1])), nb)),
            "both crossovers jumping 0 <-> 127 every 500 blocks (without the ColdFire smoother: only the DSP's own 40 ms glide), bands off": (
                "A", [knobs17((off_all[0], ((b // 500) % 2 * 127, (b // 500) % 2 * 127))) for b in range(nb)]),
            "B static: every band THR 0, RAT 20.00, ATK 127, REL 127 (compressing, slow)": ("B", [knobs17(slow)] * nb),
            "every band's THR swept 0 -> 127 -> 0, 1 s each way, the ColdFire glide, slow times": ("B", glide(
                lambda b: knobs17(([(tri(b, nb), x[1], x[2], x[3], x[4]) for x in slow[0]], slow[1])), nb)),
            "every band's RAT stepping 0..8, one step per 100 ms, the ColdFire glide, slow times": ("B", glide(
                lambda b: knobs17(([(x[0], (b // 150) % 9, x[2], x[3], x[4]) for x in slow[0]], slow[1])), nb)),
            "both crossovers swept, 1 s each way, the ColdFire glide, compressing slowly": ("B", glide(
                lambda b: knobs17((slow[0], (tri(b, nb), tri(b, nb)))), nb)),
        }
        sm = {}
        for name, (ref_, per) in sweeps.items():
            run = TailRun(vm_cmp(), dict(VC.BASE, **VC.NOFX, **VC.comp(32, 24, 32, 64, 3, 127)), VC.ROUTE_IN, list_sched(per))
            run.tprocess(L2, R2)
            sm[name] = hf_profile(run.out)
            sm[name]["finite"] = all(VC.finite(x) for x in run.out[0] + run.out[1])
            sm[name]["reference"] = ref_
            print("  (s)", name, sm[name], flush=True)
        floor = {r_: next(v["max_re_window_db"] for k, v in sm.items() if k.startswith(r_ + " static")) for r_ in "AB"}
        for name, v in sm.items():
            v["max_over_static_db"] = round(v["max_re_window_db"] - floor[v["reference"]], 1)
        sr_["energy above 12 kHz against the whole output, 256-sample windows (two sines 150 Hz + 2 kHz at -54 dBFS each, MIX 127)"] = sm
        glided = [k for k in sm if "ColdFire glide" in k]
        sr_["criterion"] = ("every glided sweep: in no window more energy above 12 kHz (against the window's level) than 3 dB over "
                            "its static reference (A: bands off; B: compressing slowly)")
        sr_["glided sweeps within 3 dB of their static reference"] = all(sm[k]["max_over_static_db"] <= 3.0 for k in glided)
        sok = sok and sr_["glided sweeps within 3 dB of their static reference"] and all(v["finite"] for v in sm.values())
        sr_["ok"] = sok
        res["s_isolation_smoothness"] = sr_
        oks["s"] = sok
        print("(s)", sok, "%.0fs" % (time.time() - t0), flush=True)

    # ---- (d) cost with tails
    if "d" in only:
        t0 = time.time()
        nb = 1500 if not q else 300
        mixl = VC.load_input("mix", nb * 32 / SR)
        rnd = random.Random(7)
        noise = ([rnd.uniform(-1, 1) for _ in range(nb * 32)], [rnd.uniform(-1, 1) for _ in range(nb * 32)])
        knames = list(VC.FL.KNOBS) + ["DEL_TIME", "DEL_WID", "DEL_FDBK", "DEL_HPF", "DEL_LPF"]
        loudfx = dict(CHR_DEL=127, CHR_REV=127, DEL_REV=127, REV_VOL=100)

        def fx_jump_mix(b, fr):
            VC.put(fr, "CMP_MIX", (0 if (b // 40) % 2 else 64) << 8)
            if b % 20 == 0:
                for k in knames:
                    VC.put(fr, k, rnd.choice([0, 127, rnd.randrange(128)]) << 8)
                VC.put(fr, "DEL_X", rnd.randrange(2) << 8)
        mv = list_sched([moving_vals(b) for b in range(nb)])
        dcfg = {
            "typical: band defaults + MIX 39": (dict(VC.BASE, **VC.FXW, **VC.TYPICAL), VC.ROUTE_INFX, mixl,
                                                 const_sched(words_of(knobs17(DEFAULTS))), None),
            "band defaults, MIX 0": (dict(VC.BASE, **VC.FXW, **VC.comp(32, 24, 32, 64, 3, 0)), VC.ROUTE_INFX, mixl,
                                     const_sched(words_of(knobs17(DEFAULTS))), None),
            "three distinct bands, MIX 100": (dict(VC.BASE, **VC.FXW, **VC.comp(32, 24, 32, 64, 3, 100)), VC.ROUTE_INFX,
                                              mixl, const_sched(words_of(knobs17(DISTINCT))), None),
            "an encoder sweep of both crossovers, defaults, MIX 39": (
                dict(VC.BASE, **VC.FXW, **VC.TYPICAL), VC.ROUTE_INFX, mixl,
                list_sched(glide(lambda b: knobs17((DEFAULTS[0], (min(127, b // 4), max(0, 127 - b // 4)))), nb)), None),
            "worst: every band knob + both crossovers moving every block, MIX 0<->64, noise, FX jumping, self": (
                dict(dict(VC.BASE, **VC.FXW, **VC.W3_SELF), **loudfx), VC.ROUTE_INFX, noise, mv, fx_jump_mix),
            "worst, key (SCS 18)": (dict(dict(VC.BASE, **VC.FXW, **VC.W3), **loudfx), VC.ROUTE_INFX, noise, mv, fx_jump_mix),
        }
        dr = {}
        worst = 0
        for name, (words, raw, inp, sched, each) in dcfg.items():
            run = TailRun(vm_cmp(), words, raw, sched)
            run.tprocess(inp[0], inp[1], each=each, paint=True)
            pb = run.r.per_block[1:]
            e = {"mean": round(sum(pb) / len(pb)), "max": max(pb), "stack_bytes": run.stack,
                 "trips": run.vm.rd32(SLOT + OFF["trips"])}
            e["est_load_mean_pct"] = round(100 * VC.LOAD(e["mean"]), 2)
            e["est_load_max_pct"] = round(100 * VC.LOAD(e["max"]), 2)
            dr[name] = e
            worst = max(worst, e["max"])
            print("  (d)", name, e, flush=True)
        dr["worst_block"] = worst
        dr["worst_block_est_load_pct"] = round(100 * VC.LOAD(worst), 2)
        dr["calibration"] = "load = (1.30 x instructions + 11k) / 666,667 (notes/T6.4 4.4) [inferred]"
        dr["ok"] = all(v["trips"] == 0 for k, v in dr.items() if isinstance(v, dict) and "trips" in v)
        res["d_cost_tails"] = dr
        oks["d"] = dr["ok"]
        print("(d)", dr["ok"], "%.0fs" % (time.time() - t0), flush=True)

    # ---- (n) NaN / inf and the extreme tail in tail mode
    if "n" in only:
        t0 = time.time()
        nr, nok = {}, True
        L, R = VC.load_input("drums", 200 * 32 / SR)
        words = dict(VC.BASE, **VC.NOFX, **VC.comp(32, 24, 32, 64, 3, 64))
        for label, inj_kind in (("NaN in comp in L at block 60", "NaN in comp in L"), ("inf in comp in R at block 60", None),
                                ("corrupted magic at block 60 (quiet re-init)", "corrupted magic")):
            vm = vm_cmp()

            def inj(b, vm_, ptrs, label=label):
                if b != 60:
                    return
                if "NaN" in label:
                    vm_.wr32(ptrs[0] + 4 * 5, 0x7fc00000)
                elif "inf" in label:
                    vm_.wr32(ptrs[1] + 4 * 9, 0x7f800000)
                else:
                    vm_.wr32(SLOT + OFF["magic"], 0x12345678)
            run = TailRun(vm, words, VC.ROUTE_IN, const_sched(words_of(knobs17(DISTINCT))))
            run.tprocess(L, R, inject=inj)
            k_after = knobs_now(vm)
            fin_after = all(VC.finite(x) for x in run.out[0][62 * 32:] + run.out[1][62 * 32:])
            od = all(VC.finite(x) for lo, n in VC.OD_STATE for x in vm.rdf(lo, n))
            e = {"output_finite_from_block_62": fin_after, "master_overdrive_state_finite": od,
                 "trips": vm.rd32(SLOT + OFF["trips"]), "reinits": vm.rd32(SLOT + OFF["reinits"]),
                 "knobs_after_are_the_tail's": k_after == expect_knobs(words_of(knobs17(DISTINCT)))}
            e["ok"] = fin_after and od and e["knobs_after_are_the_tail's"]
            nr[label] = e
            nok = nok and e["ok"]
        # the extreme valid tail: every value word 0xffff
        vm = vm_cmp()
        run = TailRun(vm, dict(VC.BASE, **VC.NOFX, **VC.comp(32, 24, 32, 64, 3, 127)), VC.ROUTE_IN,
                      const_sched([0xffff] * 27))
        run.tprocess(L, R)
        e = {"knobs": knobs_now(vm), "expected_clamped": expect_knobs([0xffff] * 27),
             "output_finite": all(VC.finite(x) for x in run.out[0] + run.out[1]),
             "peak": round(max(abs(x) for x in run.out[0] + run.out[1]), 4), "trips": vm.rd32(SLOT + OFF["trips"])}
        e["ok"] = e["knobs"] == e["expected_clamped"] and e["output_finite"] and e["trips"] == 0
        nr["every tail value 0xffff (valid tail): clamped"] = e
        nok = nok and e["ok"]
        nr["ok"] = nok
        res["n_nan_inf_tails"] = nr
        oks["n"] = nok
        print("(n)", nok, {k: v.get("ok") for k, v in nr.items() if isinstance(v, dict)}, "%.0fs" % (time.time() - t0), flush=True)

    # ---- (m) MIX 0 in tail mode against FTD1
    if "m" in only:
        t0 = time.time()
        finfo = json.loads((VC.FTD1 / "build.json").read_text())
        fstream = (VC.FTD1 / "section7.bin").read_bytes()
        assert VC.B.sha(fstream) == finfo["section7"]["sha256"]
        fcache = "t38-FTD1-" + finfo["section7"]["sha256"][:12]
        nb = 2000 if not q else 300
        prog = VC.looped(nb)
        L, R = prog[0][:nb * 32], prog[1][:nb * 32]
        mr, mok = {}, True
        for name, sched, words in (
                ("three distinct bands, MIX 0, self, FX on", const_sched(words_of(knobs17(DISTINCT))),
                 dict(VC.BASE, **VC.FXW, **VC.comp(32, 24, 32, 64, 3, 0))),
                ("every band knob and both crossovers moving every block, MIX 0, key SCS 18, FX on",
                 list_sched([moving_vals(b) for b in range(nb)]), dict(VC.BASE, **VC.FXW, **VC.comp(32, 24, 32, 64, 3, 0, scs=18)))):
            ra = TailRun(vm_cmp(), words, VC.ROUTE_INFX, sched)
            ra.tprocess(L, R)
            rf = TailRun(V.candidate_vm(fstream, cache=fcache), words, VC.ROUTE_INFX, sched)
            rf.tprocess(L, R)
            same = sum(1 for x, y in zip(ra.out[0] + ra.out[1], rf.out[0] + rf.out[1]) if x == y)
            e = {"samples": 2 * len(L), "identical_samples": same, "tail_state": [ra.vm.rd32(TST + 4 * i) for i in range(6)]}
            e["ok"] = same == 2 * len(L) and e["tail_state"][0] == 1
            mr[name] = e
            mok = mok and e["ok"]
            print("  (m)", name, same, 2 * len(L), flush=True)
        mr["ok"] = mok
        res["m_mix0_vs_ftd1_tails"] = mr
        oks["m"] = mok
        print("(m)", mok, "%.0fs" % (time.time() - t0), flush=True)

    # ---- (g) store census in tail mode, cold start
    if "g" in only:
        t0 = time.time()
        vmz = vm_cmp()
        g = {"cmp_tail_zero_after_effect_init": vmz.read(TST, tbytes) == bytes(tbytes),
             "fill_block_in_build": [b for b in info["added_blocks"] if "CmpTail" in b["why"]]}
        allowed = [(V.STACK_LO, V.STACK_TOP, "stack"), (SLOT, SLOT + info["layout_check"]["slot_sizes_from_image"]["cmp_slot_bytes"], "CmpSlot"),
                   (TST, TST + tbytes, "CmpTail"),
                   (0x2e6800, 0x2e6800 + VC.B.stub.SAVE_BYTES, "stub save area"), (0x25ba68, 0x25ba68 + 0x80, "out L"),
                   (0x25bae8, 0x25bae8 + 0x80, "out R"), (VC.GR_Y2, VC.GR_Y2 + 4, "0x257c1c meter"),
                   (VC.TX[0] + 0x10, VC.TX[0] + 0x18, "reply page 0"), (VC.TX[1] + 0x10, VC.TX[1] + 0x18, "reply page 1")]
        L, R = VC.load_input("mix", 70 * 32 / SR)
        census = {}
        for label, sched in (("tail mode, distinct bands", const_sched(words_of(knobs17(DISTINCT)))),
                             ("tail mode, every knob moving", list_sched([moving_vals(b) for b in range(70)])),
                             ("first tail after the fallback (block 3)", lambda b: tail_words(b, words_of(knobs17(DISTINCT))) if b >= 3 else None)):
            run = TailRun(vm_cmp(), dict(VC.BASE, **VC.FXW, **VC.comp(32, 24, 32, 64, 3, 64)), VC.ROUTE_INFX, sched)
            log = []
            for b in range(62):
                wl = [] if b in (0, 1, 2, 3, 4, 60, 61) else None
                run.tblock(L[32 * b:32 * b + 32], R[32 * b:32 * b + 32], b, watch=wl)
                if wl is not None:
                    log += wl
            stray = [(pc, ad) for pc, ad, va in log if not any(lo <= ad < hi for lo, hi, _ in allowed)]
            by = {}
            for pc, ad, va in log:
                nm = next((n_ for lo, hi, n_ in allowed if lo <= ad < hi), "OTHER")
                by[nm] = by.get(nm, 0) + 1
            census[label] = {"stores": len(log), "by_range": by, "outside": ["%#x@%#x" % (ad, pc) for pc, ad in stray[:10]],
                             "outside_count": len(stray)}
        g["store_census"] = census
        g["ok"] = g["cmp_tail_zero_after_effect_init"] and bool(g["fill_block_in_build"]) and all(
            v["outside_count"] == 0 for v in census.values())
        res["g_stores_cold_start"] = g
        oks["g"] = g["ok"]
        print("(g)", g["ok"], {k: (v["stores"], v["outside_count"]) for k, v in census.items()}, "%.0fs" % (time.time() - t0), flush=True)

    # ---- (e) end to end: the emulator's wire frames through the handler
    if "e" in only:
        t0 = time.time()
        d = Path(a.e2e)
        meta = json.loads((d / "frames.json").read_text())
        raw = (d / "frames.bin").read_bytes()
        fl = FB.FRAME_LEN
        frames = [raw[i:i + fl] for i in range(0, len(raw), fl)]
        er = {"capture": str(d.relative_to(ROOT)) if d.is_relative_to(ROOT) else str(d), "frames": len(frames),
              "turned": meta.get("turned")}
        prog = VC.looped(len(frames))
        L, R = prog[0][:len(frames) * 32], prog[1][:len(frames) * 32]

        def render(frs):
            vm = vm_cmp()
            run = VC.Run(vm, dict(VC.BASE), None, keep=False)
            gr, kn = [], []
            for b, fr in enumerate(frs):
                vm.wr32(FB.SPI_HALF, b & 1)
                dsp = bytearray(b"".join(fr[i:i + 2][::-1] for i in range(0, fl, 2)))
                # the compressor's input as the other proofs have it: the external input routed to it
                for k_, v_ in dict(IN_L=127, IN_R=127, IN_L_PAN=64, IN_R_PAN=64, PATTERN_VOL=100, MASTER_OD=0).items():
                    struct.pack_into("<H", dsp, FB.FRAME_WORDS[k_], v_ << 8)
                for k_, v_ in VC.ROUTE_IN.items():
                    struct.pack_into("<H", dsp, FB.FRAME_WORDS[k_], v_)
                run.r.d.frame[:] = dsp
                run.block(L[32 * b:32 * b + 32], R[32 * b:32 * b + 32], b=b)
                gr.append(tuple(vm.rdf(SLOT + OFF["gr0"], 3)))
                kn.append(knobs_now(vm))
            return gr, kn, run
        gr, kn, run = render(frames)
        t_at = meta["turn_frame"]
        held = [bytes(f[:TAIL]) + bytes(frames[t_at - 1][TAIL:]) if i >= t_at else f for i, f in enumerate(frames)]
        gr0, kn0, _ = render(held)
        moved = [any(x[i] != y[i] for x, y in zip(gr[t_at:], gr0[t_at:])) for i in range(3)]
        kmoved = [NAMES17[j] for j in range(17) if any(x[j] != y[j] for x, y in zip(kn[t_at:], kn0[t_at:]))]
        er["knobs_that_moved"] = kmoved
        er["bands_whose_gr_moved"] = [("LOW", "MID", "HIGH")[i] for i in range(3) if moved[i]]
        er["knobs_first_last"] = {NAMES17[j]: [round(kn[0][j], 3), round(kn[-1][j], 3)] for j in range(17)}
        er["deepest_gr_db"] = {"turned run": [round(min(g[i] for g in gr), 2) for i in range(3)],
                               "held tail": [round(min(g[i] for g in gr0), 2) for i in range(3)]}
        er["tail_state"] = [run.vm.rd32(TST + 4 * i) for i in range(6)]
        want = meta.get("expect_band")
        er["ok"] = (er["tail_state"][0] == 1 and er["bands_whose_gr_moved"] == [want]
                    and all(k.startswith(want[0]) for k in kmoved) and bool(kmoved))
        res["e_end_to_end"] = er
        oks["e"] = er["ok"]
        print("(e)", er["ok"], kmoved, er["bands_whose_gr_moved"], "%.0fs" % (time.time() - t0), flush=True)

    res["oks"] = oks
    res["seconds"] = round(time.time() - t_all)
    res["VERDICT"] = "PASS" if oks and all(oks.values()) else "FAIL"
    p = VC.OUT / ("verify2.json" if not a.only else "verify2-%s.json" % "".join(sorted(only)))
    p.write_text(json.dumps(res, indent=1, default=str))
    print("VERDICT:", res["VERDICT"], oks, "->", p.relative_to(ROOT))


if __name__ == "__main__":
    main()
