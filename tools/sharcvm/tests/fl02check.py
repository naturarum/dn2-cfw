#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""fl02check.py - FL02: the chorus-slot glue with the speed in sync with the tempo (flg_glue3.c + flg_sync.c, FLG3)
against FL01's (flg_glue2.c, FLG2). notes/T5.6-flanger-retune.md section 7.

    python3 tools/sharcvm/tests/fl02check.py [--seconds 4] [--grid] [--out out/FL02/fl02check.json]

flgcheck.py's harness (harness/flgdrv.c, the glue built with fx/flanger.c twice: selcc -O1 run in this interpreter,
clang -O2 -ffp-contract=off native, both -DFLG_NATIVE), with the parameter floats made as the unit's unpack makes them:
p = float32(word) x float32(1/32512), the word = the knob << 8 for a free value, or one of the 12 synced values
(a << 8) | i. The SPD word reaches the DSP through the ColdFire's smoother (T3.3; scripts/t63c_e2e.py's model of the
unit's EMAC arithmetic), which is modelled here for SPD in the transition cases.
  - free: flgcheck's five settings, every knob moving, SPD swept over its whole range and SPD glided down onto each
    anchor a_i (the glide's last frames sit on a_i << 8 | i, a synced word, for up to 37 frames), each with the tempo
    walking: FLG3 = FLG2 bit for bit (selcc), and FLG3 selcc = native;
  - synced: every division at 60, 120, 137.5 and 174 BPM (and 16/1, 1/4, 1/32 at 30, 300, 20 and 400 for the
    clamps): after the hold, the slot's FL_SYNC and tempo, and the LFO period from the phase increment (2^32 / rate
    blocks of 32/48000 s; the phase advances by exactly the rate each block) against 60 / BPM x the division's beats;
    selcc = native;
  - transitions (through the smoother model): free -> synced (FUNC + turn) -> another synced value -> free (a turn
    without FUNC), tempo jumps while synced: when the sync plays (blocks after the word settles), and selcc = native;
  - instructions per block (selcc): mean and max of every case on both glues, and with --grid flgcheck's grid on both
    (DPTH/SPD/WDTH 0, 64, 127, C 0..127) plus every division at 120 BPM over DPTH/TIME/WDTH 0, 64, 127.
Code size per function (the DSP build's, as flgcheck.py measures it). Results: out/FL02/fl02check.json.
"""
import argparse
import ctypes as C
import json
import math
import random
import re
import struct
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
sys.dont_write_bytecode = True
from vm import VM  # noqa: E402
from render_fx import test_signal  # noqa: E402
from fxcheck import globalize  # noqa: E402
import memguard  # noqa: E402

OUT = ROOT / "out/FL02/fl02check"
SEL = ROOT / "vendor/selache/target/release"
SRC = ROOT / "tools/dspsplice/src"
GLUES = {"FLG2": [SRC / "flg_glue2.c"], "FLG3": [SRC / "flg_glue3.c", SRC / "flg_sync.c"]}
MAGIC = {"FLG2": 0x464c4732, "FLG3": 0x464c4733}
CTX = 0x200a0000
IO = CTX + 0x40
PRM = CTX + 0x80
PTRS = CTX + 0xc0
BUF = 0x200a1000
# FlgSlot (flg_slot3.h = flg_glue2.c's layout), clang's offsetof; the SHARC build has the same 4-byte fields
OFF = {"sync": 8, "last": 12, "rate": 32964, "ph": 32972, "bpm": 32872, "knob": 32824}
FL_SPD, FL_SYNC = 1, 9
F15 = struct.unpack("<f", struct.pack("<f", 1.0 / 32512.0))[0]
ANCHOR = [9, 23, 37, 52, 66, 80, 86, 94, 100, 108, 122, 126]
SYNC = [(a << 8) | i for i, a in enumerate(ANCHOR, 1)]
NAMES = ["16/1", "8/1", "4/1", "2/1", "1/1", "1/2", "1/4.", "1/4", "1/8.", "1/8", "1/16", "1/32"]
BEATS = [64.0, 32.0, 16.0, 8.0, 4.0, 2.0, 1.5, 1.0, 0.75, 0.5, 0.25, 0.125]     # fx/flanger.c FL_SYNC_BEATS[1..12]
HOLD = 48
BPS = 1500.0

CASES = {
    "swell (C 32)": (96, 30, 32, 48),
    "new kit (stock defaults: C 0)": (127, 32, 0, 127),
    "phaser-like (C 12)": (90, 50, 12, 64),
    "flanger (C 40)": (80, 45, 40, 32),
    "chorus (C 115)": (40, 70, 115, 127),
}


def f32(x):
    return struct.unpack("<f", struct.pack("<f", x))[0]


def pfloat(word):
    """the unit's unpack: float32(word) x F15"""
    return f32(f32(float(word)) * F15)


def s32(x):
    x &= 0xffffffff
    return x - (1 << 32) if x & 0x80000000 else x


class Smoother:
    """the ColdFire's one-pole for one word (unit arithmetic, scripts/t63c_e2e.py): state 32-bit, word = round(s/65536)"""

    def __init__(self, word):
        self.s = word << 16

    def step(self, raw):
        r = raw - 0x10000 if raw & 0x8000 else raw
        self.s = s32(((0x03d7 * r) << 1) + ((0x7c290000 * self.s) >> 31))
        return ((self.s + 0x8000) >> 16) & 0xffff


def flags():
    return ["-proc", "ADSP-21569", "-char-size-8", "-O1", f"-I{ROOT / 'fx'}", f"-I{SRC}",
            f"-I{ROOT / 'vendor/selache/libsel/include'}"]


def build(name, native_slot):
    tag = f"{name}{'n' if native_slot else 's'}"
    d = OUT / tag
    d.mkdir(parents=True, exist_ok=True)
    extra = ["-DFLG_NATIVE"] if native_slot else []
    objs, labels = [], []
    srcs = [("flgdrv", HERE.parent / "harness/flgdrv.c", ["-D__SHARC_HARNESS__"], None)]
    srcs += [(p.stem, p, extra, "G%d" % i) for i, p in enumerate(GLUES[name])]
    srcs += [("flanger", ROOT / "fx/flanger.c", [], "F")]
    for stem, path, ex, pre in srcs:
        s = d / f"{stem}.s"
        subprocess.check_call([str(SEL / "selcc"), *flags(), *ex, "-S", "-o", str(s), str(path)])
        if pre:
            globalize(s, pre + "x_")
        if pre and pre.startswith("G"):
            labels += re.findall(r"^([A-Za-z_]\w*)\.:", s.read_text(), re.M)
        subprocess.check_call([str(SEL / "selas"), "-proc", "ADSP-21569", "-o", str(d / f"{stem}.doj"), str(s)])
        objs.append(str(d / f"{stem}.doj"))
    subprocess.check_call([str(SEL / "seld"), "-proc", "ADSP-21569", "-T", str(ROOT / "vendor/selache/libsel/link.ldf"),
                           "-o", str(d / "flg.dxe"), *objs, str(ROOT / "out/T3.6/libsel/libsel.dlb")])
    if native_slot:
        subprocess.check_call(["clang", "-O2", "-ffp-contract=off", "-fno-strict-aliasing", "-shared", "-fPIC",
                               "-DFLG_NATIVE", f"-I{ROOT / 'fx'}", f"-I{SRC}", "-o", str(d / "libflg.dylib"),
                               str(HERE.parent / "harness/flgdrv.c"), *map(str, GLUES[name]), str(ROOT / "fx/flanger.c")])
    return d, labels


def code_sizes(d, labels):
    import elf as E
    e = E.read_elf(d / "flg.dxe")
    code = sorted(v for k, v in e.symbols.items() if k.endswith(".") and not k.startswith(".") and v)
    sizes = {}
    for k, v in e.symbols.items():
        if not k.endswith("."):
            continue
        n = k[:-1]
        base = n[3:] if re.match(r"^G\dx_", n) else n
        if base in labels or n in labels:
            nxt = next((c for c in code if c > v), None)
            if nxt is not None:
                sizes[base] = 2 * (nxt - v)
    return sizes


class FxIoN(C.Structure):
    _fields_ = [("n", C.c_int32), ("inp", C.c_void_p), ("r08", C.c_int32), ("outp", C.c_void_p),
                ("r10", C.c_int32), ("bpm", C.c_float)]


class CtxN(C.Structure):
    _fields_ = [("cmd", C.c_int32), ("p", C.c_void_p), ("io", C.c_void_p)]


class Native:
    def __init__(self, d):
        self.lib = C.CDLL(str(d / "libflg.dylib"))
        self.inL, self.inR = (C.c_float * 32)(), (C.c_float * 32)()
        self.outL, self.outR = (C.c_float * 32)(), (C.c_float * 32)()
        self.ins = (C.c_void_p * 2)(C.addressof(self.inL), C.addressof(self.inR))
        self.outs = (C.c_void_p * 2)(C.addressof(self.outL), C.addressof(self.outR))
        self.io = FxIoN(32, C.addressof(self.ins), 0, C.addressof(self.outs), 0, 120.0)
        self.p = (C.c_float * 4)()

    def init(self):
        self.lib.flgdrv(C.byref(CtxN(0, None, None)))

    def block(self, L, R, words, bpm):
        self.inL[:] = L
        self.inR[:] = R
        self.p[:] = [pfloat(w) for w in words]
        self.io.bpm = bpm
        self.lib.flgdrv(C.byref(CtxN(1, C.addressof(self.p), C.addressof(self.io))))
        return list(self.outL), list(self.outR)


class Sharc:
    def __init__(self, d, magic):
        self.vm = VM()
        self.d, self.magic = d, magic
        e = self.vm.load_elf(d / "flg.dxe")
        self.entry = e.symbols["flgdrv."]
        self.vm.c_runtime_state(stack_base=e.symbols["ldf_stack_space"],
                                stack_top=e.symbols["ldf_stack_space"] + e.symbols["ldf_stack_length"],
                                l7=e.symbols["ldf_stack_length"], mode1=0x01010800)
        self.vm.write(IO, struct.pack("<iIiIif", 32, PTRS, 0, PTRS + 8, 0, 120.0))
        self.vm.write(PTRS, struct.pack("<IIII", BUF, BUF + 0x80, BUF + 0x100, BUF + 0x180))
        self.per_block = []
        self.slot = None

    def init(self):
        self.vm.write(CTX, struct.pack("<iII", 0, 0, 0))
        self.vm.call(self.entry, R4=CTX)
        self.slot = find_slot(self.vm, self.d, self.magic)

    def block(self, L, R, words, bpm):
        vm = self.vm
        vm.wrf(BUF, L)
        vm.wrf(BUF + 0x80, R)
        vm.wrf(PRM, [pfloat(w) for w in words])
        vm.wrf(IO + 0x14, [bpm])
        vm.write(CTX, struct.pack("<iII", 1, PRM, IO))
        n0 = vm.icount
        vm.call(self.entry, R4=CTX)
        self.per_block.append(vm.icount - n0)
        return vm.rdf(BUF + 0x100, 32), vm.rdf(BUF + 0x180, 32)

    def u32(self, off):
        return self.vm.rd32(self.slot + off)

    def f(self, off):
        return self.vm.rdf(self.slot + off, 1)[0]


def find_slot(vm, d, magic):
    import elf as E
    for sec in E.read_elf(d / "flg.dxe").sections:
        if sec.kind not in ("data", "bss") or len(sec.data) < 33000:
            continue
        raw = vm.read(sec.addr, len(sec.data))
        for o in range(0, len(raw) - 8, 4):
            if struct.unpack_from("<II", raw, o) == (magic, 1):
                return sec.addr + o
    raise SystemExit("slot not found")


def run(impl, L, R, script, each=None):
    """script(b) -> (words[4], bpm); each(b, impl) after every block"""
    impl.init()
    oL, oR = [], []
    for b, i in enumerate(range(0, len(L) // 32 * 32, 32)):
        w, bpm = script(b)
        x, y = impl.block(L[i:i + 32], R[i:i + 32], w, bpm)
        oL += x
        oR += y
        if each:
            each(b, impl)
    return oL, oR


def bits(xs):
    return struct.pack("<%df" % len(xs), *xs)


def same_count(a, b):
    f2u = lambda x: struct.unpack("<I", struct.pack("<f", x))[0]      # noqa: E731
    return sum(1 for x, y in zip(a, b) if f2u(x) == f2u(y))


def tempo_walk(seed, blocks, lo=60.0, hi=180.0):
    rnd = random.Random(seed)
    t, seq = 120.0, []
    for b in range(blocks):
        if b % 400 == 0:
            t = min(hi, max(lo, t + rnd.choice((-17.0, -5.0, 3.0, 11.0, 29.0))))
        seq.append(t)
    return seq


def free_scripts(blocks):
    """FL01's knob space only (whole numbers through the smoother model for SPD where it glides)"""
    tw = tempo_walk(7, blocks)
    sc = {}
    for n, k in CASES.items():
        sc[n] = (lambda k: (lambda b: ([v << 8 for v in k], tw[b])))(k)
    rnd = random.Random(5)
    k = [64, 64, 64, 64]
    seq = []
    for _ in range(blocks):
        j = rnd.randrange(4)
        k[j] = min(127, max(0, k[j] + rnd.choice((-3, -1, 1, 3))))
        seq.append([v << 8 for v in k])
    sc["every knob moving (words jump)"] = lambda b: (seq[b], tw[b])
    sw = [((b * 128 // blocks) % 128) << 8 for b in range(blocks)]
    sc["SPD swept 0..127 (words jump)"] = lambda b: ([96 << 8, sw[b], 32 << 8, 48 << 8], tw[b])
    # SPD glided by the ColdFire's smoother onto every anchor from above (the words pass a_i << 8 | i on the way down,
    # for up to 37 frames: a free knob must play them as FL01) and from below, 0.5 s each
    targets = []
    for a in ANCHOR:
        targets += [(a + 2) << 8, a << 8]
    per = 250                                   # a glide of 2 steps settles in about 230 frames
    sm = Smoother(64 << 8)
    gl = []
    for b in range(blocks):
        gl.append(sm.step(targets[min(len(targets) - 1, b // per)]))
    sc["SPD glided down onto the anchors (smoother)"] = lambda b: ([90 << 8, gl[b], 20 << 8, 64 << 8], tw[b])
    run_, longest, hits = 0, 0, 0
    for w in gl:
        run_ = run_ + 1 if w in SYNC else 0
        hits += w in SYNC
        longest = max(longest, run_)
    return sc, (hits, longest, min(len(ANCHOR), blocks // (2 * per)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=4.0)
    ap.add_argument("--grid", action="store_true")
    ap.add_argument("--out", default=str(ROOT / "out/FL02/fl02check.json"))
    a = ap.parse_args()
    global OUT
    OUT = Path(a.out).resolve().with_suffix("")
    res = {"sizes": {}, "free": {}, "synced": {}, "transitions": {}, "notes": []}
    built = {}
    for name in GLUES:
        ds, labels = build(name, False)
        res["sizes"][name] = code_sizes(ds, labels)
        res["sizes"][name]["total"] = sum(res["sizes"][name].values())
        built[name] = build(name, True)[0]
    L, R = test_signal(a.seconds)
    blocks = len(L) // 32

    # ---- free: FLG3 = FLG2 (selcc), FLG3 selcc = native
    scripts, (hits, longest, nanch) = free_scripts(blocks)
    res["glide_onto_anchors"] = {"anchors": nanch, "blocks_on_a_synced_word": hits, "longest_run_blocks": longest,
                                 "hold_blocks": HOLD}
    ok_free = True
    for cname, sc in scripts.items():
        outs, pb = {}, {}
        for name, d in built.items():
            sh = Sharc(d, MAGIC[name])
            outs[name] = run(sh, L, R, sc)
            pb[name] = sh.per_block
            memguard.check()
        nat = Native(built["FLG3"])
        nL, nR = run(nat, L, R, sc)
        s2, s3 = outs["FLG2"][0] + outs["FLG2"][1], outs["FLG3"][0] + outs["FLG3"][1]
        e = {"flg3_vs_flg2_identical": "%d/%d" % (same_count(s2, s3), len(s2)),
             "flg3_selcc_vs_native_identical": "%d/%d" % (same_count(s3, nL + nR), len(s3)),
             "insn_mean": {n: round(sum(p[1:]) / (len(p) - 1), 1) for n, p in pb.items()},
             "insn_max": {n: max(p[1:]) for n, p in pb.items()}}
        ok_free &= bits(s2) == bits(s3) and bits(s3) == bits(nL + nR)
        res["free"][cname] = e
    res["free_ok"] = ok_free

    # ---- synced: period against the tempo
    nb = 3600
    zL, zR = L[:nb * 32], R[:nb * 32]
    ok_sync, worst_rel = True, 0.0
    for bpm in (60.0, 120.0, 137.5, 174.0, 30.0, 300.0, 20.0, 400.0):
        for i, sw in enumerate(SYNC, 1):
            if bpm not in (60.0, 120.0, 137.5, 174.0) and i not in (1, 8, 12):
                continue
            sh = Sharc(built["FLG3"], MAGIC["FLG3"])
            marks = {}

            def each(b, impl, marks=marks):
                if b in (HOLD - 2, HOLD + 2, 600, 3600 - 1):
                    marks[b] = (impl.u32(OFF["sync"]), impl.u32(OFF["rate"]), impl.u32(OFF["ph"]), impl.f(OFF["bpm"]),
                                impl.f(OFF["knob"] + 4 * FL_SYNC))
            sL, sR = run(sh, zL, zR, (lambda w, t: (lambda b: ([80 << 8, w, 40 << 8, 64 << 8], t)))(sw, bpm), each)
            nat = Native(built["FLG3"])
            nL, nR = run(nat, zL, zR, (lambda w, t: (lambda b: ([80 << 8, w, 40 << 8, 64 << 8], t)))(sw, bpm))
            bc = min(300.0, max(30.0, bpm))
            want_s = 60.0 / bc * BEATS[i - 1]
            sy, rate, ph0, tb, ks = marks[600]
            _, rate2, ph1, _, _ = marks[3600 - 1]
            per_rate = 4294967296.0 / rate * 32.0 / 48000.0
            # the phase: it advances by exactly `rate` a block (mod 2^32) from block 600 to the last
            adv_ok = (ph1 - ph0) % (1 << 32) == (rate * (3599 - 600)) % (1 << 32) and rate2 == rate
            rel = abs(per_rate / want_s - 1.0)
            worst_rel = max(worst_rel, rel)
            # the rate is a whole number of 2^-32 cycles a block (truncated, from float32 arithmetic): within 1.5 of
            # the exact value, or 1e-6 of it
            exact = 4294967296.0 * (bc / (60.0 * BEATS[i - 1])) * 32.0 / 48000.0
            rate_ok = abs(rate - exact) <= 1.5 or abs(rate / exact - 1.0) < 1e-6
            before = marks[HOLD - 2][0]
            e = {"division": NAMES[i - 1], "bpm": bpm, "bpm_used": round(tb, 3), "slot_sync": sy & 0xff,
                 "sync_before_hold": before & 0xff, "fl_sync_knob": ks, "period_s_expected": round(want_s, 6),
                 "period_s_from_rate": round(per_rate, 6), "phase_advances_by_rate_each_block": adv_ok,
                 "rel_error": "%.2e" % rel, "period_error_ms": round(1000.0 * (per_rate - want_s), 4),
                 "rate": rate, "rate_exact": round(exact, 2), "selcc_vs_native": "%d/%d" % (same_count(sL + sR, nL + nR), len(sL) * 2),
                 "insn_max": max(sh.per_block[1:]), "insn_mean": round(sum(sh.per_block[HOLD + 4:]) /
                                                                         len(sh.per_block[HOLD + 4:]), 1)}
            good = (sy & 0xff) == i and (before & 0xff) == 0 and ks == float(i) and abs(tb - bc) < 1e-3 and rate_ok \
                and adv_ok and bits(sL + sR) == bits(nL + nR)
            ok_sync &= good
            e["ok"] = good
            res["synced"]["%s @ %g" % (NAMES[i - 1], bpm)] = e
            memguard.check()
    res["synced_ok"] = ok_sync
    res["synced_worst_rel_error"] = "%.2e" % worst_rel

    # ---- transitions through the smoother model
    plan = [(0, 32 << 8, 120.0), (1.0, SYNC[7], 120.0), (2.5, SYNC[9], 120.0), (4.0, SYNC[9], 90.0),
            (5.0, SYNC[9], 160.0), (6.0, (ANCHOR[9] + 1) << 8 | 10, 160.0), (7.0, SYNC[9], 160.0),
            (8.0, 70 << 8, 100.0), (9.0, SYNC[0], 100.0), (10.0, SYNC[11], 100.0), (11.0, 9 << 8, 100.0)]
    nbt = int(12.0 * BPS)
    tL, tR = (L * (nbt * 32 // len(L) + 1))[:nbt * 32], (R * (nbt * 32 // len(R) + 1))[:nbt * 32]
    sm = Smoother(32 << 8)
    words, tempos, seg = [], [], []
    for b in range(nbt):
        k = max(j for j, (t0, _, _) in enumerate(plan) if b >= t0 * BPS)
        words.append(sm.step(plan[k][1]))
        tempos.append(plan[k][2])
        seg.append(k)
    sh = Sharc(built["FLG3"], MAGIC["FLG3"])
    trace, btrace = [], []

    def sh_each(b, impl):
        trace.append(impl.u32(OFF["sync"]) & 0xff)
        btrace.append(impl.f(OFF["bpm"]))
    sL, sR = run(sh, tL, tR, lambda b: ([80 << 8, words[b], 40 << 8, 64 << 8], tempos[b]), sh_each)
    nat = Native(built["FLG3"])
    nL, nR = run(nat, tL, tR, lambda b: ([80 << 8, words[b], 40 << 8, 64 << 8], tempos[b]))
    ok_tr = bits(sL + sR) == bits(nL + nR) and all(math.isfinite(x) for x in sL + sR)
    rows = []
    for k, (t0, tw_, bpm) in enumerate(plan):
        b0 = int(t0 * BPS)
        b1 = int(plan[k + 1][0] * BPS) if k + 1 < len(plan) else nbt
        want = SYNC.index(tw_) + 1 if tw_ in SYNC else 0
        settled = next((b for b in range(b0, b1) if words[b] == tw_ and all(w == tw_ for w in words[b:b1])), None)
        plays = next((b for b in range(b0, b1) if trace[b] == want and all(x == want for x in trace[b:b1])), None)
        early = [b for b in range(b0, settled if settled is not None else b1) if trace[b] not in (0, trace[b0 - 1] if b0
                                                                                            else 0)]
        r_ = {"at_s": t0, "word": "%#06x" % tw_, "sync_wanted": want, "bpm": bpm,
              "word_settled_block": settled - b0 if settled is not None else None,
              "plays_from_block": plays - b0 if plays is not None else None,
              "plays_after_settle_blocks": (plays - settled) if (plays is not None and settled is not None) else None,
              "other_sync_before_settle": len(early), "tempo_at_end": round(btrace[b1 - 1], 3)}
        if want:
            ok_tr &= plays is not None and settled is not None and plays - settled <= HOLD and not early
            ok_tr &= abs(btrace[b1 - 1] - bpm) < 1e-3
        else:
            ok_tr &= plays is not None and plays <= b0 + 2
        rows.append(r_)
    res["transitions"] = {"rows": rows, "selcc_vs_native": "%d/%d" % (same_count(sL + sR, nL + nR), len(sL) * 2),
                          "insn_max": max(sh.per_block[1:]), "ok": ok_tr}

    # ---- the grid: the highest block per glue
    if a.grid:
        gL, gR = L[:24000], R[:24000]
        for name, d in built.items():
            worst, wk, means = 0, None, []
            for dp in (0, 64, 127):
                for sp in (0, 64, 127):
                    for c in (0, 12, 32, 64, 82, 100, 127):
                        for wd in (0, 64, 127):
                            sh = Sharc(d, MAGIC[name])
                            run(sh, gL, gR, (lambda k: (lambda b: ([v << 8 for v in k], 120.0)))((dp, sp, c, wd)))
                            pb = sh.per_block[1:]
                            means.append(sum(pb) / len(pb))
                            if max(pb) > worst:
                                worst, wk = max(pb), (dp, sp, c, wd)
                            memguard.check()
            res.setdefault("grid", {})[name] = {"cases": len(means), "worst_block": worst, "worst_at": wk,
                                                 "highest_mean": round(max(means), 1)}
        worst, wk, means = 0, None, []
        for dp in (0, 64, 127):
            for c in (0, 32, 82, 127):
                for wd in (0, 64, 127):
                    for i, sw in enumerate(SYNC, 1):
                        sh = Sharc(built["FLG3"], MAGIC["FLG3"])
                        run(sh, gL, gR, (lambda k: (lambda b: (k, 120.0)))([dp << 8, sw, c << 8, wd << 8]))
                        pb = sh.per_block[1:]
                        means.append(sum(pb[HOLD + 2:]) / len(pb[HOLD + 2:]))
                        if max(pb) > worst:
                            worst, wk = max(pb), (dp, NAMES[i - 1], c, wd)
                        memguard.check()
        res["grid"]["FLG3 synced"] = {"cases": len(means), "worst_block": worst, "worst_at": wk,
                                      "highest_mean": round(max(means), 1)}
    res["ok"] = res["free_ok"] and res["synced_ok"] and res["transitions"]["ok"]
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(res, indent=1))
    print(json.dumps({k: v for k, v in res.items() if k not in ("synced",)}, indent=1))
    print("synced ok:", res["synced_ok"], "worst rel error", res["synced_worst_rel_error"])
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
