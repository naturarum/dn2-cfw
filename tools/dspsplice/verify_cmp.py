#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""T6.3 CMP0 proof: FTD1 with the 3-band master compressor (fx/mbcomp.c + src/cmp_glue.c) in place of the
stock compressor 0xb8541d (notes/T6.4-compressor-map-and-plan.md section 5, proofs (a)-(g)).

    python3 tools/dspsplice/verify_cmp.py [--quick]

Runs the DSP image as the unit loads it in tools/sharcvm (the whole block handler 0x1c9d6b), FTD1's image next
to it on the same input and frames, the stock compressor alone (tools/sharcvm/fx.py) and Mac builds of the same
C (clang -O2, no FMA). Results in out/T6.3/CMP0-dsp/verify.json:
  (a) one band on the whole input vs the stock compressor (SCF 64): static curve, knee, attack and release
      times, program material (drums, mix, kick at 20:1, MUP, MIX 39); per-sample gain p99 <= 0.01 dB;
  (b) SHARC vs Mac (the same glue and mbcomp.c, the image's tables) on six configurations, every block's
      parameters and inputs as captured at the hook: output <= -100 dB re peak, meters, reply words; the stock
      table index law (ATK, REL, MIX, RAT) for every frame word value;
  (c) MIX 0: the whole handler output bit-identical to FTD1's over 2,000 blocks of program material (with the
      knobs moving, with a key), the reply pages different only at half-words 8..11, the rest of memory equal;
  (d) cost per block (mean, max) with the flanger and the tape delay active: C1, typical, W3, W4, W4 on noise
      with the FX knobs jumping, each worst case both with a key (SCS 18) and with self-detection (SCS 0, the
      dearer path: three detector filters), and with MIX toggling 0 <-> 64 (the dry-crossfade loop); FTD1 on
      the same; the worst block over all of them; stack depth; load = (1.30 x instructions + 11k) / 666,667;
  (e) NaN and inf injected at the input, into the key and into the state: the block silent, the next finite,
      the master not latched (FTD1 latches on the same input);
  (f) SCS 0..20, DUAL off and on: the pointers at the hook as out/T6.4/stockcomp/scs_map.txt, and the glue's
      self/key choice;
  (g) image diff vs FTD1 only where planned; the encoding audit (audit.json); cold start with a zero state;
      registers through both hooks; every store of the compressor call inside the planned ranges; the table
      block in the image; the unit's own depacker (device_depack.json).
"""
import argparse
import ctypes as C
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
import vmrun as V  # noqa: E402
import build as B  # noqa: E402
from fx import read_wav, FxRunner  # noqa: E402
import stockmap as SM  # noqa: E402
import verify_cg01 as CG  # noqa: E402
import verify_flg as FL  # noqa: E402
import verify_ftd as FT  # noqa: E402

OUT = ROOT / "out/T6.3/CMP0-dsp"
FTD1 = ROOT / "out/T3.8/FTD1"
DRY = ROOT / "out/T3.11/dry"
SLOT, MAGIC = 0x2e1c40, 0x434d5030
PARAMS, IO = 0x257964, 0x257e6c
COMP_CALL, COMP_RET = 0x1c215c, 0x1c2163
TABLES, NTAB = 0x2cfde0, 520
GR_Y2 = 0x257c1c
TX = (0x2c49d0, 0x2c59d0)
STOCK_STATE = (0x257ab0, 0x257c28)
OD_STATE = [(0x257d08, 16), (0x257d68, 8), (0x257d88, 16)]     # the master overdrive words NaN latches (T6.4 6.1)
SR = 48000
LOAD = lambda ins: (1.30 * ins + 11000) / 666667.0               # noqa: E731  the plan's calibration (T6.4 4.4)

# the FX bus as verify_ftd.py drives it (flanger and tape delay on), the input into the compressor
FXW = dict(IN_CHR_L=100, IN_CHR_R=100, IN_DEL_L=100, IN_DEL_R=100, IN_REV_L=0, IN_REV_R=0, CHR_DEL=0, CHR_REV=0,
           CHR_VOL=90, DEL_REV=0, DEL_VOL=100, REV_VOL=0, **FL.KNOBS, **FT.DKNOBS)
BASE = dict(IN_L=127, IN_R=127, IN_L_PAN=64, IN_R_PAN=64, PATTERN_VOL=100, MASTER_OD=0, DUAL=0)
NOFX = dict(IN_CHR_L=0, IN_CHR_R=0, IN_DEL_L=0, IN_DEL_R=0, IN_REV_L=0, IN_REV_R=0, CHR_VOL=0, DEL_VOL=0, REV_VOL=0)
ROUTE_IN = dict(CMP_TRACKS=0, CMP_IN_FX=0x1)        # the input into the compressor
ROUTE_INFX = dict(CMP_TRACKS=0, CMP_IN_FX=0x5)      # the input and the FX returns into the compressor


def comp(thr, atk, rel, mup, rat, mix, scs=0, scf=64):
    return dict(CMP_THR=thr, CMP_ATK=atk, CMP_REL=rel, CMP_MUP=mup, CMP_RAT=rat, CMP_MIX=mix, CMP_SCS=scs, CMP_SCF=scf)


# CMP0 knob sets (stock knobs: THR ATK REL MUP RAT index MIX)
C1 = comp(85, 40, 44, 0, 1, 127)                 # the plan's C1: -20 dB, 2:1, 10 ms, 151 ms, MIX 127
TYPICAL = comp(32, 24, 32, 64, 3, 39)            # stock new-kit defaults with MIX 39 (LIGHTHOUSE-like [inferred])
HEAVY = comp(10, 0, 0, 64, 7, 64)                # 20:1 fast, MIX 64
W3 = comp(0, 0, 0, 64, 7, 64, scs=18)            # -60 dB, 20:1, ATK 0, REL 0, MUP 64, MIX 64, key = IN bus
# the same with self-detection (SCS 0, the stock default): since plan change #1 the key path filters the key once,
# while self-detection runs the detector filter on all three bands, so SCS 0 is the dearer path (review, T6.3)
W3_SELF = dict(W3, CMP_SCS=0)


def w3_moves(b, fr):
    o = b % 2
    put(fr, "CMP_MUP", (60 + 8 * o) << 8)
    put(fr, "CMP_MIX", (60 + 4 * o) << 8)


def w4_moves(b, fr):
    o = b % 2
    w3_moves(b, fr)
    put(fr, "CMP_THR", (4 * o) << 8)
    put(fr, "CMP_ATK", (4 * o) << 8)
    put(fr, "CMP_REL", (4 * o) << 8)
    put(fr, "CMP_SCF", (30 + 4 * o) << 8)


def w5_moves(b, fr):
    """W4, RAT moving too (20:1 <-> 16:1), and MIX 0 <-> 64 every 40 blocks in place of W4's 60 <-> 64: the
    32 blocks after MIX reaches or leaves index 0 take the dry-crossfade output loop, the dearest one"""
    w4_moves(b, fr)
    put(fr, "CMP_RAT", (7 - b % 2) << 8)
    put(fr, "CMP_MIX", (0 if (b // 40) % 2 else 64) << 8)


def mix_toggle(b, fr):
    """W4 with MIX 0 <-> 64 every 40 blocks (the review's set-up, out/T6.3/review-dsp3/t4_cost_crossfade.py)"""
    w4_moves(b, fr)
    put(fr, "CMP_MIX", (0 if (b // 40) % 2 else 64) << 8)


def put(fr, name, raw):
    struct.pack_into("<H", fr, V.fullblock.FRAME_WORDS[name], int(raw) & 0xffff)


def f32(x):
    return struct.unpack("<f", struct.pack("<f", x))[0]


def u2f(u):
    return struct.unpack("<f", struct.pack("<I", u & 0xffffffff))[0]


def finite(x):
    return x == x and not math.isinf(x)


# ---------------------------------------------------------------- the whole handler, captured at the hook

class Run:
    """the block handler with stops at the compressor call (parameters, I/O pointers and buffers) and at its
    return (outputs, meter word, reply words); inject(b, vm, ptrs) may change the input buffers at the call"""

    def __init__(self, vm, words, raw=None, keep=True):
        self.r = V.Runner(vm, words)
        if raw:
            self.r.d.set_raw(**raw)
        self.vm = vm
        self.keep = keep
        self.cap = []
        self.out = ([], [])
        self.tx_pages = []
        self.stack = 0

    def frame(self):
        return self.r.d.frame

    def block(self, L, R, inject=None, b=0, paint=False, tx_full=False, watch=None):
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

        def at_call():
            ia, oa = vm.rd32(IO + 4), vm.rd32(IO + 0xc)
            c["n"] = vm.rd32(IO)
            c["ptrs"] = [vm.rd32(ia + 4 * k) for k in range(4)]
            c["optrs"] = [vm.rd32(oa), vm.rd32(oa + 4)]
            if inject:
                inject(b, vm, c["ptrs"])
            if self.keep:
                c["p"] = vm.rdf(PARAMS, 7)
                c["ins"] = [vm.rdf(p, 32) for p in c["ptrs"]]
            if watch is not None:
                from vm import watch as W
                W(vm, 0, 0xffffffff)

        def at_ret():
            if watch is not None:
                from vm import watch_log
                watch.extend(watch_log(vm))
                from vm import watch as W
                W(vm, 0, 0)
            if self.keep:
                c["out"] = [vm.rdf(c["optrs"][0], 32), vm.rdf(c["optrs"][1], 32)]
                c["meter"] = vm.rdf(GR_Y2, 1)[0]
                c["tx"] = [struct.unpack("<II", vm.read(t + 0x10, 8)) for t in TX]
        n0 = vm.icount
        r._call_probed([(COMP_CALL, at_call), (COMP_RET, at_ret)])
        r.per_block.append(vm.icount - n0)
        if paint:
            i7 = vm.reg("I7")
            raw = vm.read(V.STACK_LO, i7 - V.STACK_LO)
            word = V.PAINT.to_bytes(4, "little")
            low = next((V.STACK_LO + k for k in range(0, len(raw), 4) if raw[k:k + 4] != word), i7)
            self.stack = max(self.stack, i7 - low)
        if self.keep:
            self.cap.append(c)
        if tx_full:
            self.tx_pages.append(b"".join(vm.read(t, 0x1000) for t in TX))
        oL, oR = vm.rdf(V.fullblock.OUT_FLOATS, 32), vm.rdf(V.fullblock.OUT_FLOATS + 0x80, 32)
        self.out[0].extend(oL)
        self.out[1].extend(oR)
        return oL, oR

    def process(self, L, R, each=None, **kw):
        for b, i in enumerate(range(0, len(L) // 32 * 32, 32)):
            if each:
                each(b, self.r.d.frame)
            self.block(L[i:i + 32], R[i:i + 32], b=b, **kw)
        return self.out


# ---------------------------------------------------------------- the Mac build

class CmpIo(C.Structure):
    _fields_ = [("n", C.c_int32), ("inp", C.POINTER(C.POINTER(C.c_float))), ("r08", C.c_int32),
                ("out", C.POINTER(C.POINTER(C.c_float)))]


def native(bdir, fullband=False):
    lib = bdir / ("libcmpfull.dylib" if fullband else "libcmpnative.dylib")
    subprocess.check_call(["clang", "-O2", "-ffp-contract=off", "-fno-strict-aliasing", "-shared", "-fPIC",
                           "-DCMP_NATIVE"] + (["-DMB_FULLBAND"] if fullband else [])
                          + [f"-I{ROOT / 'fx'}", f"-I{ROOT / 'tools/dspsplice/src'}", "-o", str(lib),
                             str(ROOT / "fx/mbcomp.c"), str(ROOT / "tools/dspsplice/src/cmp_glue.c")])
    lib = C.CDLL(str(lib))
    lib.cmp_native_slot_ptr.restype = C.c_void_p
    lib.cmp_native_mb_offset.restype = C.c_uint32
    return lib


class Native:
    def __init__(self, lib, tab):
        self.lib = lib
        self.tab = (C.c_float * NTAB)(*tab)
        lib.cmp_native_set_tables(self.tab)
        self.p = (C.c_float * 8)()
        self.buf = [(C.c_float * 32)() for _ in range(4)]
        self.o = [(C.c_float * 32)() for _ in range(2)]
        self.ptr = [C.cast(x, C.POINTER(C.c_float)) for x in self.buf]
        self.outs = (C.POINTER(C.c_float) * 2)(*[C.cast(x, C.POINTER(C.c_float)) for x in self.o])

    def init(self):
        self.lib.cmp_init()

    def block(self, p, ins, self_key, n=32):
        self.p[:7] = p
        for k in range(4):
            self.buf[k][:] = ins[k]
        sc = (self.ptr[0], self.ptr[1]) if self_key else (self.ptr[2], self.ptr[3])
        arr = (C.POINTER(C.c_float) * 4)(self.ptr[0], self.ptr[1], *sc)
        io = CmpIo(n, arr, 0, self.outs)
        self.lib.cmp_process(self.p, C.byref(io))
        tx = (C.c_uint32 * 16).in_dll(self.lib, "cmp_native_tx")
        return ([list(self.o[0]), list(self.o[1])], C.c_float.in_dll(self.lib, "cmp_native_gr_y2").value,
                [(tx[4], tx[5]), (tx[12], tx[13])])

    def replay(self, cap):
        """the captured blocks, in order, from cmp_init (as the SHARC: the init hook ran at boot)"""
        self.init()
        out, meters, txs = ([], []), [], []
        for c in cap:
            o, m, t = self.block(c["p"], c["ins"], c["ptrs"][2] == c["ptrs"][0] and c["ptrs"][3] == c["ptrs"][1], c["n"])
            out[0].extend(o[0])
            out[1].extend(o[1])
            meters.append(m)
            txs.append(t)
        return out, meters, txs


def offsets(bdir):
    """field offsets of CmpSlot/MBComp (the Mac's; the same on the SHARC for every field before mb.tab)"""
    src = bdir / "offsets.c"
    src.write_text("""#include <stdio.h>
#include <stddef.h>
#define CMP_NATIVE 1
#include "cmp_glue.c"
#define O(f) printf("\\"%s\\": %lu,\\n", #f, (unsigned long)offsetof(CmpSlot, f))
int main(void) { printf("{\\n"); O(magic); O(blocks); O(sc_ext); O(trips); O(gr0); O(grd); O(word10); O(word14);
O(mb); O(mb.knob); O(mb.s1[0].ic1); O(mb.s2[1].ic1); O(mb.kx[0]); O(mb.ky[0]); O(mb.band[0].y1); O(mb.band[0].y2);
O(mb.band[0].ca); O(mb.band[0].cr); O(mb.band[0].rinv); O(mb.band[0].mk); O(mb.band[0].hy[0]);
O(mb.band[1].y2); O(mb.band[2].y2); O(mb.mw); O(mb.md); O(mb.t_mw); O(mb.t_md); O(mb.mix0); O(mb.xw);
O(mb.trips); O(mb.buf_g); O(mb.tab); printf("\\"band_bytes\\": %lu, \\"slot_bytes_mac\\": %lu}\\n", (unsigned long)sizeof(MbBand),
(unsigned long)sizeof(CmpSlot)); return 0; }
""")
    exe = bdir / "offsets"
    subprocess.check_call(["clang", "-O0", f"-I{ROOT / 'fx'}", f"-I{ROOT / 'tools/dspsplice/src'}", "-o", str(exe),
                           str(src), str(ROOT / "fx/mbcomp.c")], stderr=subprocess.DEVNULL)
    return json.loads(subprocess.check_output([str(exe)]).decode())


def image_diff_any(ia, ib):
    """loader-address byte ranges where two final images differ in value or in being written; unlike
    build.diff_images, the two may have regions of different shapes (FTD1's data block is shorter)"""
    def masks(img):
        return [bytes(1 if x >= 0 else 0 for x in o) for o in img.owner]
    ma, mb = masks(ia), masks(ib)
    spans = sorted(list(ia.regions) + list(ib.regions))
    merged = []
    for lo, hi in spans:
        if merged and lo <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], hi)
        else:
            merged.append([lo, hi])

    def sl(img, msk, lo, hi):
        d, w = bytearray(hi - lo), bytearray(hi - lo)
        for (s_, e_), dd, mm in zip(img.regions, img.data, msk):
            a_, b_ = max(s_, lo), min(e_, hi)
            if a_ < b_:
                d[a_ - lo:b_ - lo] = dd[a_ - s_:b_ - s_]
                w[a_ - lo:b_ - lo] = mm[a_ - s_:b_ - s_]
        for i in range(hi - lo):
            if not w[i]:
                d[i] = 0
        return d, w
    out = []
    for lo, hi in merged:
        da, wa = sl(ia, ma, lo, hi)
        db, wb = sl(ib, mb, lo, hi)
        cur = None
        for i in range(hi - lo):
            if da[i] != db[i] or wa[i] != wb[i]:
                if cur and cur[1] == lo + i:
                    cur[1] += 1
                else:
                    if cur:
                        out.append(tuple(cur))
                    cur = [lo + i, lo + i + 1]
        if cur:
            out.append(tuple(cur))
    return out


def compare(a, b):
    r = FL.compare(a, b)
    r["max_diff_db_re_peak"] = r["max_diff_db_re_peak"]
    return r


def ok_db(r, lim=-100.0):
    return r["max_diff_db_re_peak"] is None or r["max_diff_db_re_peak"] <= lim


def load_input(name, seconds, gain=1.0, offset=0.0):
    L, R, sr = read_wav(DRY / f"{name}.wav")
    assert sr == SR
    i0 = int(offset * SR)
    n = int(seconds * SR) // 32 * 32
    L = (L[i0:] + [0.0] * n)[:n]
    R = (R[i0:] + [0.0] * n)[:n]
    return [gain * x for x in L], [gain * x for x in R]


def concat(*parts):
    L, R = [], []
    for a, b in parts:
        L += a
        R += b
    return L, R


# ---------------------------------------------------------------- (a) one band vs the stock compressor

def proof_a(lib_full, tab, quick=False):
    nat = Native(lib_full, tab)
    lib_full.mbcomp_test_fullband(1)

    def params_of(k):
        p = SM.params("comp", k)
        return [p[n] for n in ("SCF", "THR", "RAT", "ATK", "REL", "MUP", "MIX")]

    def ours(L, R, k, settle=40):
        nat.init()
        p = params_of(k)
        z = [0.0] * 32
        for _ in range(settle):
            nat.block(p, [z, z, z, z], True)
        oL, oR = [], []
        for i in range(0, len(L) // 32 * 32, 32):
            o, _, _ = nat.block(p, [L[i:i + 32], R[i:i + 32], L[i:i + 32], R[i:i + 32]], True)
            oL += o[0]
            oR += o[1]
        return oL, oR

    def stock(L, R, k, settle=40):
        fx = FxRunner("comp")
        fx.set_params(**SM.params("comp", k))
        z = [0.0] * 32
        for _ in range(settle):
            fx.block(z, z)
        oL, oR = [], []
        for i in range(0, len(L) // 32 * 32, 32):
            a, b = fx.block(L[i:i + 32], R[i:i + 32])
            oL += a
            oR += b
        return oL, oR

    def db(x):
        return 20 * math.log10(max(abs(x), 1e-30))

    def seg_gain(x, y, i0, i1):
        ex = sum(v * v for v in x[i0:i1])
        ey = sum(v * v for v in y[i0:i1])
        return 10 * math.log10(ey / ex)

    res = {}
    # static curve: 1 kHz sine at -60..0 dBFS in 3 dB steps, 0.3 s each, the last 50 ms; THR 64 (-29.8 dB)
    secs = 0.3
    n = int(secs * SR) // 32 * 32
    levels = list(range(-60, 1, 3))
    s = []
    for j, lv in enumerate(levels):
        a = 10 ** (lv / 20)
        s += [a * math.sin(2 * math.pi * 1000 * (j * n + k) / SR) for k in range(n)]
    worst = 0.0
    rows = {}
    for rat in ((1, 3, 7) if not quick else (7,)):
        k = dict(SCF=64, THR=64, RAT=rat, ATK=20, REL=40, MUP=0, MIX=127)
        sL, _ = stock(s, s, k)
        mL, _ = ours(s, s, k)
        rr = []
        for j, lv in enumerate(levels):
            i0, i1 = j * n + n - 2400, j * n + n
            gs, gm = seg_gain(s, sL, i0, i1), seg_gain(s, mL, i0, i1)
            worst = max(worst, abs(gs - gm))
            rr.append((lv, round(gs, 4), round(gm, 4)))
        rows[f"{SM.RATIOS[rat]:g}:1"] = rr
    res["static_curve"] = {"max_abs_diff_db": round(worst, 5), "rows (level, stock dB, CMP0 band dB)": rows}
    # knee: 4:1, levels thr-3..thr+3 dB in 0.5 dB steps
    k = dict(SCF=64, THR=64, RAT=3, ATK=20, REL=40, MUP=0, MIX=127)
    thr = 60 * SM.params("comp", k)["THR"] - 60
    lv = [thr + d / 2 for d in range(-6, 7)]
    s = []
    for j, l in enumerate(lv):
        a = 10 ** (l / 20)
        s += [a * math.sin(2 * math.pi * 1000 * (j * n + q) / SR) for q in range(n)]
    sL, _ = stock(s, s, k)
    mL, _ = ours(s, s, k)
    kw = 0.0
    for j in range(len(lv)):
        i0, i1 = j * n + n - 2400, j * n + n
        kw = max(kw, abs(seg_gain(s, sL, i0, i1) - seg_gain(s, mL, i0, i1)))
    res["knee_4to1"] = {"thr_db": round(thr, 3), "max_abs_diff_db": round(kw, 5)}
    # attack and release: square steps -40 -> -10 -> -40 dBFS at 20:1, THR 42 (-40.2 dB); 63 % times
    f = 6000.0

    def sq(a, m, n0):
        return [a * (1.0 if math.sin(2 * math.pi * f * (n0 + q + 0.5) / SR) >= 0 else -1.0) for q in range(m)]
    trs = []
    pairs = ((0, 0), (20, 30), (40, 44), (64, 64), (100, 90), (127, 127)) if not quick else ((0, 0), (64, 64))
    tw = 0.0
    for atk, rel in pairs:
        k = dict(SCF=64, THR=42, RAT=7, ATK=atk, REL=rel, MUP=0, MIX=127)
        secs2 = 1.0 if rel < 60 else (3.0 if rel < 100 else 8.0)
        m = int(secs2 * SR) // 32 * 32
        x = sq(10 ** (-2), m, 0) + sq(10 ** (-0.5), m, m) + sq(10 ** (-2), m, 2 * m)
        sL, _ = stock(x, x, k)
        mL, _ = ours(x, x, k)

        def t63(y, start, end, final=None):
            gd = [db(y[i] / x[i]) for i in range(start, end)]
            g0, g1 = gd[0], (gd[-1] if final is None else final)
            tgt = g0 + 0.632 * (g1 - g0)
            for i, v in enumerate(gd):
                if (g1 < g0 and v <= tgt) or (g1 > g0 and v >= tgt):
                    return i / SR * 1000.0
            return None
        ta_s, ta_m = t63(sL, m - 1, 2 * m), t63(mL, m - 1, 2 * m)
        tr_s, tr_m = t63(sL, 2 * m - 1, 3 * m, 0.0), t63(mL, 2 * m - 1, 3 * m, 0.0)
        ea = abs(ta_m - ta_s) / max(ta_s, 1e-9)
        er = abs(tr_m - tr_s) / max(tr_s, 1e-9)
        if ta_s > 0.2:
            tw = max(tw, ea)
        tw = max(tw, er)
        trs.append(dict(ATK=atk, REL=rel, stock_attack_ms=round(ta_s, 3), cmp0_attack_ms=round(ta_m, 3),
                        stock_release_ms=round(tr_s, 2), cmp0_release_ms=round(tr_m, 2)))
    res["times"] = {"rows": trs, "max_rel_diff (attack above 0.2 ms, release)": round(tw, 5)}
    # program material: per-sample gain where |in| > 0.02
    prog = []
    p99w = 0.0
    sets = [("drums", dict(SCF=64, THR=85, RAT=3, ATK=20, REL=40, MUP=0, MIX=127)),
            ("drums", dict(SCF=64, THR=59, RAT=5, ATK=40, REL=64, MUP=24, MIX=127)),
            ("mix", dict(SCF=64, THR=85, RAT=1, ATK=40, REL=44, MUP=0, MIX=127)),
            ("kick4", dict(SCF=64, THR=64, RAT=7, ATK=0, REL=20, MUP=0, MIX=127)),
            ("mix", dict(SCF=64, THR=32, RAT=3, ATK=24, REL=32, MUP=64, MIX=39))]
    for name, k in (sets if not quick else sets[3:4]):
        L, R = load_input(name, 3.0 if not quick else 1.0)
        sL, sR = stock(L, R, k)
        mL, mR = ours(L, R, k)
        idx = [i for i in range(len(sL)) if abs(L[i]) > 0.02]
        d = sorted(abs(db(sL[i] / L[i]) - db(mL[i] / L[i])) for i in idx)
        e = sum((a - b) ** 2 for a, b in zip(sL + sR, mL + mR))
        ref = sum(a * a for a in sL + sR)
        p99 = d[int(0.99 * len(d))]
        p99w = max(p99w, p99)
        prog.append(dict(input=name, knobs={kk: vv for kk, vv in k.items()}, samples=len(d), p99_abs_gain_diff_db=round(p99, 5),
                         max_abs_gain_diff_db=round(d[-1], 5), residual_db=round(10 * math.log10(e / ref), 1)))
    res["program"] = prog
    res["ok"] = worst <= 0.01 and kw <= 0.01 and p99w <= 0.01 and tw <= 0.01
    lib_full.mbcomp_test_fullband(0)
    return res


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true", help="shorter runs (development)")
    a = ap.parse_args()
    t_start = time.time()
    q = a.quick
    info = json.loads((OUT / "build.json").read_text())
    stream = (OUT / "section7.bin").read_bytes()
    finfo = json.loads((FTD1 / "build.json").read_text())
    fstream = (FTD1 / "section7.bin").read_bytes()
    # the VM snapshots are named after build.json's hash: refuse an image build.json does not describe
    for nm, bi, by in (("CMP0", info, stream), ("FTD1", finfo, fstream)):
        if B.sha(by) != bi["section7"]["sha256"]:
            raise SystemExit(f"{nm}: section7.bin is not the image its build.json describes: run build.py {nm}")
    cache = "t63-CMP0-" + info["section7"]["sha256"][:12]
    fcache = "t38-FTD1-" + finfo["section7"]["sha256"][:12]
    sym = {k: int(v, 16) for k, v in info["symbols"].items()}
    stale = [src for src, h in info.get("sources", {}).items() if B.sha((ROOT / src).read_bytes()) != h]
    if stale or "sources" not in info:
        raise SystemExit(f"out/T6.3/CMP0-dsp was built from other sources ({stale or 'no source hashes'}): run build.py CMP0")
    bdir = OUT / "build"
    res = {"section7_sha256": info["section7"]["sha256"], "section7_bytes": info["section7"]["bytes"],
           "ftd1_section7_sha256": finfo["section7"]["sha256"]}

    def vm_cmp():
        return V.candidate_vm(stream, cache=cache)

    def vm_ftd():
        return V.candidate_vm(fstream, cache=fcache)

    vm0 = vm_cmp()
    tab = vm0.rdf(TABLES, NTAB)
    lib = native(bdir)
    lib_full = native(bdir, fullband=True)
    off = offsets(bdir)
    slot_bytes = info["layout_check"]["slot_sizes_from_image"]["cmp_slot_bytes"]
    # every field up to mb.buf_g has the same offset on both (4-byte fields); mb.tab is a 4-byte pointer on the SHARC
    res["layout"] = {"mac_offsets": off, "sharc_slot_bytes": slot_bytes,
                     "sharc_slot_bytes_expected_from_mac_offsets": off["mb.buf_g"] + 3 * 32 * 4 + 4,
                     "mb_offset_mac": lib.cmp_native_mb_offset()}
    res["layout"]["ok"] = (slot_bytes == off["mb.buf_g"] + 3 * 32 * 4 + 4 and off["mb"] == 72
                           and lib.cmp_native_mb_offset() == 72)
    print("layout", res["layout"]["ok"], flush=True)

    # ---- the table block in the image
    rinv = tab[0:8]
    res["tables"] = {"rinv": rinv, "rinv_is_1_over_stock_ratios": all(f32(1.0 / r) == v for r, v in zip(SM.RATIOS, rinv)),
                     "mix_0": tab[B_MIX(0)], "mix_127": tab[B_MIX(127)], "scf_64": tab[392 + 64],
                     "atk_increasing": all(tab[8 + i] < tab[9 + i] for i in range(127)),
                     "rel_increasing": all(tab[136 + i] < tab[137 + i] for i in range(127))}
    res["tables"]["ok"] = (res["tables"]["rinv_is_1_over_stock_ratios"] and res["tables"]["mix_0"] == 0.0
                           and res["tables"]["mix_127"] == 1.0 and -1.0 < res["tables"]["scf_64"] < -0.99
                           and res["tables"]["atk_increasing"] and res["tables"]["rel_increasing"])

    # ---- (a)
    t0 = time.time()
    res["a_one_band_vs_stock"] = proof_a(lib_full, tab, quick=q)
    print("(a)", res["a_one_band_vs_stock"]["ok"], "%.0fs" % (time.time() - t0), flush=True)

    # ---- (b) SHARC vs Mac
    t0 = time.time()
    nat = Native(lib, tab)
    secs = 2.0 if not q else 0.5
    mix_in = load_input("mix", secs)
    drums_in = load_input("drums", secs)
    configs = {
        "C1 (-20 dB 2:1, MIX 127), mix": (dict(BASE, **NOFX, **C1), ROUTE_IN, mix_in, None),
        "typical (defaults + MIX 39), mix, FX on": (dict(BASE, **FXW, **TYPICAL), ROUTE_INFX, mix_in, None),
        "heavy (-54 dB 20:1 fast, MUP 64, MIX 64), drums": (dict(BASE, **NOFX, **HEAVY), ROUTE_IN, drums_in, None),
        "key (SCS 18 = IN bus, FX returns compressed), drums, FX on": (dict(BASE, **FXW, **comp(40, 10, 30, 30, 5, 100, scs=18)),
                                                                     ROUTE_INFX, drums_in, None),
        "W4 (all knobs moving every block, key), mix, FX on": (dict(BASE, **FXW, **W3), ROUTE_INFX, mix_in, w4_moves),
        "MIX 0 (heavy settings), drums": (dict(BASE, **NOFX, **comp(0, 0, 0, 127, 7, 0)), ROUTE_IN, drums_in, None),
    }
    bres = {}
    for name, (words, raw, (L, R), each) in configs.items():
        run = Run(vm_cmp(), words, raw)
        run.process(L, R, each=each)
        cap = run.cap
        sh = ([], [])
        for c in cap:
            sh[0].extend(c["out"][0])
            sh[1].extend(c["out"][1])
        mac, meters, txs = nat.replay(cap)
        mdiff = max(abs(c["meter"] - m) for c, m in zip(cap, meters))
        txd = 0
        for c, t in zip(cap, txs):
            for page in (0, 1):
                for w in (0, 1):
                    for half in (0, 16):
                        txd = max(txd, abs(((c["tx"][page][w] >> half) & 0xffff) - ((t[page][w] >> half) & 0xffff)))
        cmpr = compare(mac, sh)
        vmx = run.vm
        bres[name] = {"output_vs_mac": cmpr, "meter_0x257c1c_max_diff_db": mdiff, "reply_halfwords_max_diff": txd,
                      "self_key_blocks": sum(1 for c in cap if c["ptrs"][2] == c["ptrs"][0]),
                      "meter_last_db": round(cap[-1]["meter"], 3),
                      "band_gr_last_db": [round(x, 3) for x in vmx.rdf(SLOT + off["gr0"], 4)],
                      "guard_trips": vmx.rd32(SLOT + off["trips"]),
                      "comp_output_peak": round(cmpr["peak"], 4)}
        bres[name]["ok"] = ok_db(cmpr) and mdiff <= 1e-3 and txd <= 1 and bres[name]["guard_trips"] == 0
        print("  (b)", name, cmpr["max_diff_db_re_peak"], "meter", mdiff, "tx", txd, flush=True)
    res["b_sharc_vs_mac"] = bres
    # the stock table index law: every ATK/REL/MIX frame word value (and fractional ones), RAT 0..7
    F15 = SM.F15
    run = Run(vm_cmp(), dict(BASE, **NOFX, **comp(64, 0, 0, 0, 0, 0)), ROUTE_IN, keep=False)
    z = [0.0] * 32
    bad = []
    words = [v << 8 for v in range(128)] + [(v << 8) | 0x80 for v in range(0, 127, 7)] + [(v << 8) | 0xff for v in range(0, 127, 11)]
    for w in words:
        for nm in ("CMP_ATK", "CMP_REL", "CMP_MIX"):
            put(run.frame(), nm, w)
        run.block(z, z)
        run.block(z, z)
        p = f32(f32(float(w)) * F15)
        idx = int(min(max(f32(p * 127.0), 0.0), 127.0))
        ca = run.vm.rdf(SLOT + off["mb.band[0].ca"], 1)[0]
        cr = run.vm.rdf(SLOT + off["mb.band[0].cr"], 1)[0]
        tw, td = run.vm.rdf(SLOT + off["mb.t_mw"], 2)
        if not (ca == tab[8 + idx] and cr == tab[136 + idx] and tw == tab[264 + idx] and td == tab[264 + 127 - idx]):
            bad.append(w)
    rbad = []
    for i in range(8):
        put(run.frame(), "CMP_RAT", i << 8)
        run.block(z, z)
        run.block(z, z)
        p = run.vm.rdf(PARAMS + 8, 1)[0]
        si = int(min(max(f32(p * 7.0), 0.0), 7.0))
        rv = [run.vm.rdf(SLOT + off["mb.band[0].rinv"] + off["band_bytes"] * bb, 1)[0] for bb in range(3)]
        if not (si == i and all(x == tab[si] for x in rv)):
            rbad.append(i)
    res["b_index_law"] = {"words_checked": len(words), "mismatches": bad, "rat_mismatches": rbad,
                          "ok": not bad and not rbad}
    print("(b) index law", res["b_index_law"]["ok"], "%.0fs" % (time.time() - t0), flush=True)

    # ---- (c) MIX 0 bit-identical to FTD1
    t0 = time.time()
    nb = 2000 if not q else 300
    prog = concat(load_input("drums", nb * 32 / SR / 2), load_input("mix", nb * 32 / SR / 2))
    cres = {}

    def moving_knobs(b, fr):
        put(fr, "CMP_THR", int(127 * (0.5 + 0.5 * math.sin(b / 40.0))) << 8)
        put(fr, "CMP_ATK", (b % 128) << 8)
        put(fr, "CMP_REL", ((3 * b) % 128) << 8)
        put(fr, "CMP_MUP", ((7 * b) % 128) << 8)
        put(fr, "CMP_RAT", (b // 50 % 8) << 8)
        put(fr, "CMP_SCS", (b // 300 % 3) << 8)
    for name, (words, raw, each) in {
            "MIX 0, stock new-kit defaults, FX on": (dict(BASE, **FXW, **comp(32, 24, 32, 64, 3, 0)), ROUTE_INFX, None),
            "MIX 0, heavy (-60 dB 20:1, MUP 127)": (dict(BASE, **NOFX, **comp(0, 0, 0, 127, 7, 0)), ROUTE_IN, None),
            "MIX 0, every other knob moving, SCS 0/1/2": (dict(BASE, **FXW, **comp(32, 24, 32, 64, 3, 0)), ROUTE_INFX, moving_knobs),
            "MIX 0, key SCS 18": (dict(BASE, **FXW, **comp(20, 5, 20, 64, 6, 0, scs=18)), ROUTE_INFX, None)}.items():
        rf = Run(vm_ftd(), words, raw, keep=False)
        rc = Run(vm_cmp(), words, raw, keep=True)
        L, R = prog
        m = len(L)                                  # 2,000 blocks of program material (drums, then the mix)
        if each is not None or "key" in name:
            m = min(len(L), (1000 if not q else 300) * 32)
        rf.process(L[:m], R[:m], each=each, tx_full=True)
        rc.process(L[:m], R[:m], each=each, tx_full=True)
        same = sum(1 for x, y in zip(rf.out[0] + rf.out[1], rc.out[0] + rc.out[1]) if x == y)
        diffoff = set()
        for pa, pb in zip(rf.tx_pages, rc.tx_pages):
            if pa != pb:
                for i in range(0, len(pa), 2):
                    if pa[i:i + 2] != pb[i:i + 2]:
                        diffoff.add(i % 0x1000)
        # the rest of memory: everything but the planned ranges
        mem = {}
        ignore = [(SLOT, SLOT + 0x800), (0x2e6034, 0x2e6040), (0x2e6800, 0x2e7000), (0x308400, 0x310000),
                  STOCK_STATE, (V.STACK_LO, V.STACK_TOP), (TX[0], TX[0] + 0x1000), (TX[1], TX[1] + 0x1000)]
        for lo, hi in ((0x240000, 0x2c0000), (0x2c0000, 0x2f0000), (0x300000, 0x320000)):
            a_ = rf.vm.read(lo, hi - lo)
            b_ = rc.vm.read(lo, hi - lo)
            if a_ != b_:
                for i in range(0, hi - lo, 4):
                    ad = lo + i
                    if a_[i:i + 4] != b_[i:i + 4] and not any(x <= ad < y for x, y in ignore):
                        mem[ad] = 1
        cres[name] = {"blocks": m // 32, "samples": 2 * m, "bit_identical": same,
                      "reply_byte_offsets_that_differ": sorted("%#x" % o for o in diffoff),
                      "l1_words_that_differ_outside_planned": ["%#x" % x for x in sorted(mem)[:20]], "l1_count": len(mem),
                      "stack_cmp0": rc.stack}
        # the reply's first 32-bit word (half-words 0, 1) is the handler's cycle count (written at 0x1c9e62; read
        # by fullblock.FullDSP as `cycles`): CMP0 takes more instructions, so it differs; shown below
        w0 = [(struct.unpack_from("<I", pa, 0)[0], struct.unpack_from("<I", pb, 0)[0])
              for pa, pb in zip(rf.tx_pages[-3:], rc.tx_pages[-3:])]
        cres[name]["reply_word0_as_cycles_FTD1_CMP0_last_blocks"] = [
            (((a_ >> 16) | (a_ << 16)) & 0xffffffff, ((b_ >> 16) | (b_ << 16)) & 0xffffffff) for a_, b_ in w0]
        cres[name]["insn_per_block_last_FTD1_CMP0"] = [(x, y) for x, y in zip(rf.r.per_block[-3:], rc.r.per_block[-3:])]
        cres[name]["ok"] = (same == 2 * m and diffoff <= {0x0, 0x2, 0x10, 0x12, 0x14, 0x16} and not mem)
        print("  (c)", name, same, 2 * m, sorted(diffoff), len(mem), flush=True)
    res["c_mix0_vs_ftd1"] = cres
    print("(c) %.0fs" % (time.time() - t0), flush=True)

    # ---- (d) cost
    t0 = time.time()
    nb = 1500 if not q else 300
    mixl = load_input("mix", nb * 32 / SR)
    rnd = random.Random(7)
    noise = ([rnd.uniform(-1, 1) for _ in range(nb * 32)], [rnd.uniform(-1, 1) for _ in range(nb * 32)])
    knames = list(FL.KNOBS) + ["DEL_TIME", "DEL_WID", "DEL_FDBK", "DEL_HPF", "DEL_LPF"]

    def fuzzed(moves):
        def f(b, fr):
            moves(b, fr)
            if b % 20 == 0:
                for k in knames:
                    put(fr, k, rnd.choice([0, 127, rnd.randrange(128)]) << 8)
                put(fr, "DEL_X", rnd.randrange(2) << 8)
        return f
    w4_fuzz = fuzzed(w4_moves)
    loudfx = dict(CHR_DEL=127, CHR_REV=127, DEL_REV=127, REV_VOL=100)
    dcfg = {
        "MIX 0 (defaults)": (dict(BASE, **FXW, **comp(32, 24, 32, 64, 3, 0)), ROUTE_INFX, mixl, None),
        "C1": (dict(BASE, **FXW, **C1), ROUTE_INFX, mixl, None),
        "typical (defaults + MIX 39)": (dict(BASE, **FXW, **TYPICAL), ROUTE_INFX, mixl, None),
        "W3 (key, MUP and MIX moving)": (dict(BASE, **FXW, **W3), ROUTE_INFX, mixl, w3_moves),
        "W4 (every knob moving)": (dict(BASE, **FXW, **W3), ROUTE_INFX, mixl, w4_moves),
        "W4 + full-scale noise, FX knobs jumping": (dict(dict(BASE, **FXW, **W3), **loudfx), ROUTE_INFX, noise, w4_fuzz),
        # self-detection (SCS 0) versions: three detector filters per block instead of one
        "W3, self-detection (SCS 0)": (dict(BASE, **FXW, **W3_SELF), ROUTE_INFX, mixl, w3_moves),
        "W4, self-detection (SCS 0)": (dict(BASE, **FXW, **W3_SELF), ROUTE_INFX, mixl, w4_moves),
        "W4 + noise + FX jumping, self-detection (SCS 0)": (dict(dict(BASE, **FXW, **W3_SELF), **loudfx), ROUTE_INFX,
                                                            noise, w4_fuzz),
        # MIX 0 <-> 64 every 40 blocks: the dry-crossfade output loop for 32 blocks after each change
        "W4 + noise + FX jumping, MIX 0<->64 every 40 blocks, key (SCS 18)": (
            dict(dict(BASE, **FXW, **W3), **loudfx), ROUTE_INFX, noise, fuzzed(mix_toggle)),
        "W4 + noise + FX jumping, MIX 0<->64 every 40 blocks, self (SCS 0)": (
            dict(dict(BASE, **FXW, **W3_SELF), **loudfx), ROUTE_INFX, noise, fuzzed(mix_toggle)),
        "W5 (W4 + RAT moving) + noise + FX jumping, MIX 0<->64 every 40 blocks, self (SCS 0)": (
            dict(dict(BASE, **FXW, **W3_SELF), **loudfx), ROUTE_INFX, noise, fuzzed(w5_moves)),
    }
    dres = {}
    stack = 0
    for name, (words, raw, (L, R), each) in dcfg.items():
        st = {}
        for which, mk in (("FTD1", vm_ftd), ("CMP0", vm_cmp)):
            rnd.seed(7)
            r = Run(mk(), words, raw, keep=False)
            r.process(L, R, each=each, paint=True)
            pb = r.r.per_block
            st[which] = {"mean": round(sum(pb) / len(pb)), "max": max(pb[1:]), "stack_bytes": r.stack}
            if which == "CMP0":
                stack = max(stack, r.stack)
        st["delta_mean"] = st["CMP0"]["mean"] - st["FTD1"]["mean"]
        st["delta_max"] = st["CMP0"]["max"] - st["FTD1"]["max"]
        st["est_load_mean_pct"] = round(100 * LOAD(st["CMP0"]["mean"]), 1)
        st["est_load_max_pct"] = round(100 * LOAD(st["CMP0"]["max"]), 1)
        st["est_load_FTD1_mean_pct"] = round(100 * LOAD(st["FTD1"]["mean"]), 1)
        dres[name] = st
        print("  (d)", name, st, flush=True)
    worst = max(dres, key=lambda k: dres[k]["CMP0"]["max"])
    typ = dres["typical (defaults + MIX 39)"]
    res["d_cost"] = {"configs": dres, "stack_max_bytes": stack, "audio_task_stack_bytes": 4000,
                     "load_formula": "(1.30 x instructions + 11k) / 666,667 (T6.4 4.4, inferred calibration)",
                     "worst": {"config": worst, "max": dres[worst]["CMP0"]["max"],
                               "est_load_max_pct": dres[worst]["est_load_max_pct"]},
                     "owner_budget (ROADMAP; estimates, not a pass/fail of this proof)": {
                         "typical ~70 %: est. mean at MIX 39": typ["est_load_mean_pct"],
                         "worst under ~75 %: est. worst block over all configs": dres[worst]["est_load_max_pct"],
                         "worst_under_75": dres[worst]["est_load_max_pct"] < 75.0,
                         "never near 90 %": dres[worst]["est_load_max_pct"] < 85.0}}
    print("  (d) worst:", res["d_cost"]["worst"], flush=True)
    print("(d) %.0fs" % (time.time() - t0), flush=True)

    # ---- (e) NaN / inf
    t0 = time.time()
    eres = {}
    nb = 260
    L, R = load_input("drums", nb * 32 / SR)

    def od_finite(vm):
        return all(finite(x) for lo, n in OD_STATE for x in vm.rdf(lo, n))

    def mk_inject(kind):
        def f(b, vm, ptrs):
            if b != 60:
                return
            if kind == "NaN in comp in L":
                vm.wr32(ptrs[0] + 4 * 7, 0x7fc00000)
            elif kind == "+inf in comp in R":
                vm.wr32(ptrs[1] + 4 * 31, 0x7f800000)
            elif kind == "-inf in the key L":
                vm.wr32(ptrs[2] + 4 * 3, 0xff800000)
            elif kind == "1e35 in comp in L":
                vm.wr32(ptrs[0] + 4 * 9, struct.unpack("<I", struct.pack("<f", 1e35))[0])
            elif kind == "NaN in a crossover state":
                vm.wr32(SLOT + off["mb.s2[1].ic1"], 0x7fc00000)
            elif kind == "NaN in band 0's attack envelope":
                vm.wr32(SLOT + off["mb.band[0].y2"], 0x7fc00000)
            elif kind == "+inf in band 0's release envelope":
                vm.wr32(SLOT + off["mb.band[0].y1"], 0x7f800000)
            elif kind == "NaN in band 2's detector state":
                vm.wr32(SLOT + off["mb.band[0].hy[0]"] + 2 * off["band_bytes"], 0x7fc00000)
            elif kind == "NaN in the dry gain":
                vm.wr32(SLOT + off["mb.md"], 0x7fc00000)
        return f
    for mix in (64, 0):
        for kind in ("NaN in comp in L", "+inf in comp in R", "-inf in the key L", "1e35 in comp in L",
                     "NaN in a crossover state", "NaN in band 0's attack envelope", "NaN in band 2's detector state",
                     "NaN in the dry gain", "+inf in band 0's release envelope"):
            words = dict(BASE, **NOFX, **comp(30, 10, 30, 64, 5, mix, scs=18 if "key" in kind else 0))
            rc = Run(vm_cmp(), words, ROUTE_IN, keep=True)
            rc.process(L, R, inject=mk_inject(kind))
            outs = rc.out[0] + rc.out[1]
            after = [x for i in range(60 * 32, len(rc.out[0])) for x in (rc.out[0][i], rc.out[1][i])]
            comp60 = rc.cap[60]["out"][0] + rc.cap[60]["out"][1]
            comp61 = rc.cap[61]["out"][0] + rc.cap[61]["out"][1]
            rms_end = math.sqrt(sum(x * x for x in outs[-2 * 50 * 32:]) / (2 * 50 * 32))
            e = {"trips": rc.vm.rd32(SLOT + off["trips"]), "handler_output_nan_inf_after": sum(1 for x in after if not finite(x)),
                 "comp_block_60_silent": all(x == 0.0 for x in comp60),
                 "comp_block_61_finite_nonzero": all(finite(x) for x in comp61) and any(x != 0.0 for x in comp61),
                 "master_od_state_finite": od_finite(rc.vm), "output_rms_last_50_blocks": round(rms_end, 4)}
            if kind.startswith("+inf in band 0's release"):
                # heals inside the block: the release stage takes min(gc, ...), so y1 is finite again at once
                e["note"] = "y1 = min(gc, (1-cr) gc + cr y1) replaces +inf by gc at the next sample: no trip needed"
                e["ok"] = (e["trips"] == 0 and e["handler_output_nan_inf_after"] == 0 and e["master_od_state_finite"]
                           and rms_end > 1e-3)
            else:
                e["ok"] = (e["trips"] == 1 and e["handler_output_nan_inf_after"] == 0 and e["comp_block_60_silent"]
                           and e["master_od_state_finite"] and rms_end > 1e-3 and e["comp_block_61_finite_nonzero"])
            eres[f"MIX {mix}: {kind}"] = e
            print("  (e)", mix, kind, e, flush=True)
    # FTD1 on the same NaN input: the stock compressor passes it and the master latches
    rf = Run(vm_ftd(), dict(BASE, **NOFX, **comp(30, 10, 30, 64, 5, 64)), ROUTE_IN, keep=False)
    rf.process(L, R, inject=mk_inject("NaN in comp in L"))
    eres["FTD1 (stock compressor), NaN in comp in L, for comparison"] = {
        "handler_output_nan_last_block": sum(1 for x in rf.out[0][-32:] + rf.out[1][-32:] if not finite(x)),
        "master_od_state_finite": od_finite(rf.vm)}
    res["e_nan_inf"] = eres
    print("(e) %.0fs" % (time.time() - t0), flush=True)

    # ---- (f) SCS pointers and the glue's choice
    t0 = time.time()
    want = {}
    for line in (ROOT / "out/T6.4/stockcomp/scs_map.txt").read_text().splitlines():
        p = line.split()
        dual, scs = int(p[1]), int(p[3].rstrip(":"))
        sc = p[7].split("/")
        want[(dual, scs)] = (int(sc[0], 16), int(sc[1], 16))
    fres = {"rows": [], "mismatches": []}
    run = Run(vm_cmp(), dict(BASE, **NOFX, **comp(64, 10, 30, 0, 3, 64)), ROUTE_IN, keep=True)
    z = [0.01 * math.sin(i) for i in range(32)]
    for dual in (0, 1):
        for scs in range(21):
            run.r.set(CMP_SCS=scs, DUAL=dual)
            run.block(z, z)
            run.block(z, z)
            c = run.cap[-1]
            got = (c["ptrs"][2], c["ptrs"][3])
            ext = run.vm.rd32(SLOT + off["sc_ext"])
            expect_ext = int(got != (c["ptrs"][0], c["ptrs"][1]))
            row = (dual, scs, "%#x/%#x" % got, ext)
            fres["rows"].append(row)
            if got != want[(dual, scs)] or ext != expect_ext or c["ptrs"][:2] != [0x25b968, 0x25b9e8]:
                fres["mismatches"].append(row)
    fres["ok"] = not fres["mismatches"]
    res["f_scs"] = fres
    print("(f)", fres["ok"], "%.0fs" % (time.time() - t0), flush=True)

    # ---- (g) image diff vs FTD1, cold start, registers, store census, audit, depacker
    t0 = time.time()
    g = {}
    diffs = image_diff_any(B.final_image(fstream), B.final_image(stream))
    planned = [(B.ldr.loader_addr(2 * s_), B.ldr.loader_addr(2 * s_) + 6) for s_, _, _ in B.RECIPES["CMP0"]["hooks"][4:]]
    planned += [(B.ldr.loader_addr(0x308400), B.ldr.loader_addr(0x310000)),
                (B.ldr.loader_addr(SLOT), B.ldr.loader_addr(0x2e6000)),
                (B.ldr.loader_addr(0x2e6034), B.ldr.loader_addr(0x2e6040))]
    g["image_diff_vs_FTD1"] = {"ranges": [("%#x" % x, "%#x" % y) for x, y in diffs],
                               "outside_planned": [("%#x" % x, "%#x" % y) for x, y in diffs
                                                   if not any(lo <= x and y <= hi for lo, hi in planned)]}
    g["image_diff_vs_stock"] = {"ranges": info["image_diff_ranges"], "checked_by_build_py": True}
    # cold start: the init hook ran (magic); a zeroed slot (as if it had not) gives the same output
    vmz = vm_cmp()
    g["cold_start"] = {"magic_after_effect_init": "%#x" % vmz.rd32(SLOT)}
    L, R = load_input("mix", 300 * 32 / SR)
    for mix in (0, 39):
        words = dict(BASE, **NOFX, **comp(32, 24, 32, 64, 3, mix))
        ra = Run(vm_cmp(), words, ROUTE_IN, keep=False)
        ra.process(L, R)
        vz = vm_cmp()
        vz.write(SLOT, bytes(slot_bytes))
        rz = Run(vz, words, ROUTE_IN, keep=False)
        rz.process(L, R)
        rfz = Run(vm_ftd(), words, ROUTE_IN, keep=False)
        rfz.process(L, R)
        g["cold_start"][f"MIX {mix}"] = {
            "zero_slot_vs_init_identical": ra.out == rz.out,
            "zero_slot_magic_after": "%#x" % vz.rd32(SLOT),
            "vs_FTD1_identical_samples": sum(1 for x, y in zip(rz.out[0] + rz.out[1], rfz.out[0] + rfz.out[1]) if x == y),
            "samples": 2 * len(L) // 32 * 32}
    g["cold_start"]["ok"] = (g["cold_start"]["magic_after_effect_init"] == "%#x" % MAGIC
                             and all(g["cold_start"][f"MIX {m}"]["zero_slot_vs_init_identical"] for m in (0, 39))
                             and g["cold_start"]["MIX 0"]["vs_FTD1_identical_samples"] == g["cold_start"]["MIX 0"]["samples"])
    # registers: each hook stub changes nothing the stock routine it replaces keeps
    vmr = vm_cmp()
    Run(vmr, dict(BASE, **NOFX, **C1), ROUTE_IN, keep=False).process(L[:320], R[:320])
    arr = 0x2e3000                                      # free: after CmpSlot, below the data (layout check)
    for k_, p_ in enumerate((0x25b968, 0x25b9e8, 0x25b968, 0x25b9e8, 0x25ba68, 0x25bae8)):
        vmr.wr32(arr + 4 * k_, p_)
    vmr.wr32(IO, 32)
    vmr.wr32(IO + 4, arr)
    vmr.wr32(IO + 0xc, arr + 16)
    regs = {}
    for label, entry, args in (("hook stub fxh_comp", sym["fxh_comp"], {"R4": 0x257ab0, "R8": PARAMS, "R12": IO}),
                               ("stock compressor 0xb8541d alone", 0xb8541d, {"R4": 0x257ab0, "R8": PARAMS, "R12": IO}),
                               ("hook stub fxh_comp_init", sym["fxh_comp_init"], {"R4": 0x257ab0, "R8": PARAMS, "R12": 0x257e48}),
                               ("stock init 0xb85501 alone", 0xb85501, {"R4": 0x257ab0, "R8": PARAMS, "R12": 0x257e48})):
        ch, m1 = set(), False
        for seed in range(4):
            c_, mm = CG.reg_test(vmr, entry, args, seed)
            ch |= set(c_)
            m1 |= mm
        regs[label] = {"changed": sorted(ch, key=CG.SAVED.index), "MODE1_changed": m1}
    g["registers"] = regs
    g["comp_stub_changes_beyond_stock"] = sorted(set(regs["hook stub fxh_comp"]["changed"])
                                                 - set(regs["stock compressor 0xb8541d alone"]["changed"]) - {"R2", "I12"})
    g["comp_init_stub_changes_beyond_stock"] = sorted(set(regs["hook stub fxh_comp_init"]["changed"])
                                                      - set(regs["stock init 0xb85501 alone"]["changed"]) - {"R2", "I12"})
    # every store made during the compressor call, by address: normal, key, guard trip, first call from zero
    allowed = [(V.STACK_LO, V.STACK_TOP, "stack"), (SLOT, SLOT + slot_bytes, "CmpSlot"),
               (0x2e6800, 0x2e6800 + B.stub.SAVE_BYTES, "stub save area"), (0x25ba68, 0x25ba68 + 0x80, "out L"),
               (0x25bae8, 0x25bae8 + 0x80, "out R"), (GR_Y2, GR_Y2 + 4, "0x257c1c meter"),
               (TX[0] + 0x10, TX[0] + 0x18, "reply page 0 +0x10..+0x17"), (TX[1] + 0x10, TX[1] + 0x18, "reply page 1 +0x10..+0x17")]
    census = {}
    for label, words, raw, inj, zero in (
            ("normal (C1)", dict(BASE, **NOFX, **C1), ROUTE_IN, None, False),
            ("key, MIX 64, knobs moving", dict(BASE, **FXW, **W3), ROUTE_INFX, None, False),
            ("guard trip (NaN input)", dict(BASE, **NOFX, **HEAVY), ROUTE_IN, mk_inject("NaN in comp in L"), False),
            ("first call from a zeroed slot", dict(BASE, **NOFX, **TYPICAL), ROUTE_IN, None, True)):
        vw = vm_cmp()
        if zero:
            vw.write(SLOT, bytes(slot_bytes))
        rw = Run(vw, words, raw, keep=False)
        log = []
        for b in range(62):
            wl = [] if b in (0, 1, 2, 60, 61) else None
            rw.block(L[32 * b:32 * b + 32], R[32 * b:32 * b + 32], inject=inj, b=b, watch=wl)
            if wl is not None:
                log += wl
        stray = [(pc, ad) for pc, ad, va in log if not any(lo <= ad < hi for lo, hi, _ in allowed)]
        by = {}
        for pc, ad, va in log:
            nm = next((n for lo, hi, n in allowed if lo <= ad < hi), "OTHER")
            by[nm] = by.get(nm, 0) + 1
        census[label] = {"stores": len(log), "by_range": by, "outside": ["%#x@%#x" % (ad, pc) for pc, ad in stray[:10]],
                         "outside_count": len(stray)}
        print("  (g) census", label, census[label]["stores"], census[label]["outside_count"], flush=True)
    g["store_census"] = census
    # the encoding audit and the device depacker (run by their own tools; read here)
    for fn, key in (("audit.json", "audit"), ("device_depack.json", "device_depack")):
        p = OUT / fn
        g[key] = json.loads(p.read_text()) if p.exists() else None
    if g["audit"]:
        au = g["audit"]["summary"]
        g["audit_summary"] = {"ours": au.get("ours"), "levels": au.get("levels"), "findings": au.get("findings"),
                              "unseen": au.get("levels", {}).get("UNSEEN", 0)}
    if g["device_depack"]:
        dd = g["device_depack"]["files"]
        g["device_depack_summary"] = {f: v["depack"].get("7 DSP, main OS depacker, output at buffer+1", {}).get("output_sha256")
                                      for f, v in dd.items()}
        g.pop("device_depack")
    g.pop("audit", None)
    dd_ok = bool(g.get("device_depack_summary")) and all(v == info["section7"]["sha256"]
                                                            for v in g["device_depack_summary"].values())
    au_ok = bool(g.get("audit_summary")) and g["audit_summary"]["unseen"] == 0 and not g["audit_summary"]["findings"]
    g["audit_ok"], g["device_depack_ok"] = au_ok, dd_ok
    g["ok"] = (not g["image_diff_vs_FTD1"]["outside_planned"] and g["cold_start"]["ok"] and au_ok and dd_ok
               and not g["comp_stub_changes_beyond_stock"] and not g["comp_init_stub_changes_beyond_stock"]
               and all(v["outside_count"] == 0 for v in census.values()))
    res["g"] = g
    print("(g)", g["ok"], "%.0fs" % (time.time() - t0), flush=True)

    res["seconds_taken"] = round(time.time() - t_start)
    verdict = (res["layout"]["ok"] and res["tables"]["ok"] and res["a_one_band_vs_stock"]["ok"]
               and all(v["ok"] for v in res["b_sharc_vs_mac"].values()) and res["b_index_law"]["ok"]
               and all(v["ok"] for v in res["c_mix0_vs_ftd1"].values())
               and all(v["ok"] for k, v in res["e_nan_inf"].items() if not k.startswith("FTD1"))
               and res["f_scs"]["ok"] and res["g"]["ok"] and res["d_cost"]["stack_max_bytes"] < 2032)
    res["VERDICT"] = "PASS" if verdict else "FAIL"
    (OUT / ("verify-quick.json" if q else "verify.json")).write_text(json.dumps(res, indent=1, default=str))
    print("VERDICT:", res["VERDICT"])
    return 0 if verdict else 1


def B_MIX(i):
    return 264 + i


if __name__ == "__main__":
    sys.exit(main())
