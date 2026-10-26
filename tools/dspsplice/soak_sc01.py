#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""SC01 soak (2026-10-21; notes/SC01-soak.md): SC01's built DSP image, out/SC01/SC01-dsp/section7.bin, in sharcvm for
minutes of audio on the owner's crash recipes (filter turns, FDBK turns, idle with crackles and RND jumps), where the
proofs of notes/SC01.md ran 2 s per case. It looks for a fault of our code itself, independent of the memory layout.

    python3 tools/dspsplice/soak_sc01.py bench                       speed, blocks per second per process
    python3 tools/dspsplice/soak_sc01.py run [--scale 1.0] [--workers 3] [--only R1a,R2a]

Every block (1,500 blocks = 1 s): the whole handler under a cap of CAP instructions (a block over it = a runaway; then
up to 10^8 more to see whether it ever returns, and where it loops); any sharcvm trap (unmapped access, stack overflow,
undecodable instruction); the instructions of the block and of the delay call; the outputs and the echo's wet finite
and under their ceilings (0.6931, 0.7); the tape echo's state (SeSlot magic and block counter, every float field
finite, the write and read indices on the tape, tau within its clamp, the crackle event positions in 0..32) and the
32 frames the block wrote on the tape (finite, within +-1); the compressor's guard counters (trips, re-inits); the
hardware stacks back to their depth; and one of four code ranges watched for stores (L1 block 1 code, code4, L1 block
2 code with 0x315f8c-0x318000, the four L2 ranges), in turn. Every 100 blocks the C stack's high-water mark (painted);
every 1,500 blocks the entries of every L2 and code4 (level 3) function (the profile's hit count of its first
instruction) and the instructions executed in L2; every 60 s a scan of the whole tape.

The echo's knobs move as the ColdFire sends a turned encoder (each frame 3 % of the way to the knob: verify_cmp2.glide),
the band knobs through the frame tail as on the unit. Results: out/SC01/soak/<recipe>.json and .log.
The SpaceEcho field offsets come from the header SC01 was built from (copied to out/SC01/soak/src/, its sha256
checked against build.json), so later edits of fx/spaceecho.h do not move them.
"""
import argparse
import array
import gc
import json
import math
import os
import random
import struct
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(HERE))

OUTD = ROOT / "out/SC01/soak"
SRC = OUTD / "src"
SE_RING, SE_HIST = 512000, 16384
SE_TMIN, SE_TMAX = 64.0, 3.0 * 169984.0 + 300.0
SE_MAGIC = 0x53454531
BPS = 1500
GLIDE = 0x03d7 / 0x8000              # the ColdFire's encoder smoother per frame (verify_cmp2.glide)
CAP = int(os.environ.get("SOAK_CAP", 3_000_000))   # a block over this many instructions is a runaway (typical 370-420k)
SPIKE = 600_000                      # blocks over this are listed
INT_FIELDS = ("w", "hw", "dirty", "mode", "pan", "mixdirty", "rq", "mixsettled", "mxramp", "rng", "prng", "hrng",
              "crng", "ck_count", "ev_pos", "ev_count0", "ev_count", "ctl_left")
ECHO_KEYS = ("DEL_TIME", "DEL_WID", "DEL_FDBK", "DEL_HPF", "DEL_LPF", "AGE")   # glided; DEL_X (MODE) steps

G = {}                               # per process: the modules, the image, the offsets


def setup(cmp_off):
    if G:
        return
    import verify_sc01 as S
    import memguard
    import build as BD
    S.OFF.update(cmp_off)
    stream, info, cache = S.load_image(S.OUT, "SC01")
    seo = json.loads((SRC / "offsets.json").read_text())
    if BD.sha((SRC / "spaceecho.h").read_bytes()) != info["sources"]["fx/spaceecho.h"]:
        raise SystemExit("out/SC01/soak/src/spaceecho.h is not the header SC01 was built from")
    G.update(S=S, V=S.V, VC=S.VC, V2=S.V2, FB=S.V.fullblock, memguard=memguard, BD=BD, stream=stream, info=info,
             cache=cache, seo=seo)
    funcs = []
    for f in info["functions"]:
        if f["region"].startswith("l2") or f["region"] == "c4":
            funcs.append((f["region"], f["file"] + ":" + f["function"], int(f["sw"], 16)))
    G["entries"] = funcs
    G["l2_sw"] = list(BD.RECIPES["SC01"]["l2"].values())
    l2b = [(BD.E.sw_to_byte(lo), BD.E.sw_to_byte(hi)) for lo, hi in G["l2_sw"]]
    G["watch"] = [(0x2dd600, 0x2e1c40, "L1 block 1 code (stubs, flanger)"),
                  (0x2e2900, 0x2e5df4, "code4 (glue, cmp_tail, boot-time init)"),
                  (0x308400, 0x318000, "L1 block 2 code and 0x315f8c-0x318000"),
                  (min(a for a, _ in l2b), max(b for _, b in l2b), "L2 A..D")]
    # the float words of SpaceEcho from knob[] to its end, minus the integer fields
    lo, hi = seo["knob"], seo["size"]
    ints = set()
    sizes = {"rq": 3, "ev_pos": 33}
    for k in INT_FIELDS:
        for j in range(sizes.get(k, 1)):
            ints.add(seo[k] + 4 * j)
    G["se_lo"], G["se_hi"] = lo, hi
    G["se_float_idx"] = [(o - lo) // 4 for o in range(lo, hi, 4) if o not in ints]


# ---------------------------------------------------------------- a hand on the knobs

class Hand:
    """one knob's target: holds, walks, sweeps or jumps (knob units 0..127); step(b) -> target"""

    def __init__(self, rnd, v, lo=0.0, hi=127.0, style="walk", period=(0.3, 3.0), jump=0.15, sweep_s=20.0):
        self.r, self.v, self.lo, self.hi, self.style = rnd, float(v), lo, hi, style
        self.period, self.jump, self.sweep_s = period, jump, sweep_s
        self.t = self.v
        self.next = 0
        self.rate = 0.0

    def step(self, b):
        r = self.r
        if self.style == "hold":
            return self.v
        if self.style == "sweep":         # a triangle lo..hi..lo over sweep_s, the knob turned by hand
            ph = (b / (BPS * self.sweep_s)) % 1.0
            x = 2 * ph if ph < 0.5 else 2 - 2 * ph
            self.v = self.lo + (self.hi - self.lo) * x
            return self.v
        if b >= self.next:
            self.next = b + int(BPS * r.uniform(*self.period))
            if r.random() < self.jump:
                self.t = r.uniform(self.lo, self.hi)
                self.rate = 0.0
                self.v = self.t                       # a fast spin: the ColdFire's glide does the rest
            else:
                self.t = min(max(self.v + r.gauss(0, 25), self.lo), self.hi)
                self.rate = (self.t - self.v) / max(1, (self.next - b) * r.uniform(0.3, 1.0))
        if self.rate:
            self.v += self.rate
            if (self.rate > 0 and self.v >= self.t) or (self.rate < 0 and self.v <= self.t):
                self.v, self.rate = self.t, 0.0
        return self.v


# ---------------------------------------------------------------- recipes

def wid_word(kind, amount):
    """the WID knob for MONO / ALT / RND at a width 0..127 (se_glue: d = v - 64; ALT d < -1, RND d > +1)"""
    if kind == "MONO":
        return 64
    if kind == "ALT":
        return max(0, 63 - amount * 63 / 127.0)
    return min(127, 65 + amount * 62 / 127.0)


def recipes(scale):
    m = lambda x: max(30, int(x * scale))     # noqa: E731   seconds
    R = {}
    for name, mode, fb, age, wk, src, secs, seed in (
            ("R1a", 6, 32, 64, "RND", "hits", 600, 101), ("R1b", 3, 64, 127, "ALT", "hits", 600, 102),
            ("R1c", 6, 64, 127, "RND", "hits", 480, 103), ("R1d", 3, 32, 64, "ALT", "hits", 480, 104)):
        R[name] = dict(kind="idle", what=f"idle: MODE {mode + 1}, FDBK {fb}, AGE {age}, WID {wk} 127, drum hits 2 s "
                       "then silence, nothing moves", mode=mode, fb=fb, age=age, wid=(wk, 127), src=src,
                       seconds=m(secs), seed=seed)
    R["R1e"] = dict(kind="idle", what="idle at the guide's starting point: TIME 23, MODE 1, WID 0 (mono), FDBK 32, CNTR 64,"
                    " BW 127, AGE 1, drum hits 2 s then silence, nothing moves", mode=0, fb=32, age=1, wid=("MONO", 0),
                    bw=127, src="hits", seconds=m(300), seed=105)
    R["R2c"] = dict(kind="filter", what="CNTR and BW turned like a hand from the guide's starting point (MODE 1, WID 0, "
                    "FDBK 32, AGE 1), drums and the mix", seconds=m(300), seed=203, src="music", plain=True)
    R["R2a"] = dict(kind="filter", what="CNTR and BW turned like a hand (walks, spins, BW to 0), MODE 4/7, FDBK 32..127,"
                    " AGE 64, drums and the mix", seconds=m(420), seed=201, src="music")
    R["R2b"] = dict(kind="filter", what="as R2a, other seed, BW mostly resonant (0..60)", seconds=m(360), seed=202,
                    src="music", bw=(0, 60))
    R["R3a"] = dict(kind="fdbk", what="FDBK swept 0..127..0 (20 s), MODE 4, AGE 0 then 64, drums and the mix",
                    seconds=m(360), seed=301, src="music")
    R["R4a"] = dict(kind="human", what="every echo, band and MIX knob walked, MUP mangles, MODE steps, tempo changes, "
                    "drums/mix and noise, reverb and flanger on", seconds=m(720), seed=401, src="mixed")
    R["R4b"] = dict(kind="human", what="as R4a, other seed", seconds=m(600), seed=402, src="mixed")
    R["R5a"] = dict(kind="time", what="TIME swept (slow and spun) with tempo changes, MODE 7, FDBK 50, AGE 32, drums",
                    seconds=m(360), seed=501, src="music")
    return R


class Source:
    def __init__(self, kind, rnd):
        VC = G["VC"]
        dL, dR = VC.load_input("drums", 3.0)
        mL, mR = VC.load_input("mix", 3.0)
        self.L, self.R = dL + mL, dR + mR
        self.n = len(self.L)
        self.kind, self.r = kind, rnd
        self.noise_on = False
        self.next = 0

    def block(self, b):
        i = (32 * b) % self.n
        if self.kind == "hits":
            if b < 2 * BPS:
                return self.L[i:i + 32], self.R[i:i + 32]
            return [0.0] * 32, [0.0] * 32
        if self.kind == "mixed":
            if b >= self.next:
                self.next = b + int(BPS * self.r.uniform(5, 20))
                self.noise_on = self.r.random() < 0.3
            if self.noise_on:
                r = self.r
                return [r.uniform(-0.7, 0.7) for _ in range(32)], [r.uniform(-0.7, 0.7) for _ in range(32)]
        return self.L[i:i + 32], self.R[i:i + 32]


class Control:
    """the knob targets of one recipe, the ColdFire's glide on the echo's words, the frame tail for the bands"""

    def __init__(self, cfg):
        VC, V2 = G["VC"], G["V2"]
        import verify_flg as FL
        self.cfg = cfg
        r = self.r = random.Random(cfg["seed"])
        allfx = dict(IN_CHR_L=100, IN_CHR_R=100, IN_DEL_L=100, IN_DEL_R=100, IN_REV_L=60, IN_REV_R=60, CHR_DEL=40,
                     CHR_REV=30, CHR_VOL=90, DEL_REV=40, DEL_VOL=100, REV_VOL=90, REV_DEC=90, **FL.KNOBS)
        self.words = dict(VC.BASE, **allfx, **VC.comp(32, 24, 32, 64, 3, 64))
        k = cfg["kind"]
        self.bpm = Hand(r, 120, 60, 200, "hold")
        self.mode = cfg.get("mode", 6)
        self.mode_next = 0
        self.tail17 = list(G["S"].knobs17(G["S"].START))
        self.mix3 = [64.0, 64.0, 64.0]
        self.band = None
        if k == "idle":
            h = {"DEL_TIME": Hand(r, 23, style="hold"), "DEL_WID": Hand(r, wid_word(*cfg["wid"]), style="hold"),
                 "DEL_FDBK": Hand(r, cfg["fb"], style="hold"), "DEL_HPF": Hand(r, 64, style="hold"),
                 "DEL_LPF": Hand(r, cfg.get("bw", 64), style="hold"), "AGE": Hand(r, cfg["age"], style="hold")}
        elif k == "filter" and cfg.get("plain"):
            h = {"DEL_TIME": Hand(r, 23, style="hold"), "DEL_WID": Hand(r, 64, style="hold"),
                 "DEL_FDBK": Hand(r, 32, style="hold"),
                 "DEL_HPF": Hand(r, 64, 0, 127, "walk", period=(0.2, 1.5), jump=0.2),
                 "DEL_LPF": Hand(r, 64, 0, 127, "walk", period=(0.2, 1.5), jump=0.2),
                 "AGE": Hand(r, 1, style="hold")}
            self.mode = 0
        elif k == "filter":
            lo, hi = cfg.get("bw", (0, 127))
            h = {"DEL_TIME": Hand(r, 23, style="hold"), "DEL_WID": Hand(r, wid_word("ALT", 100), style="hold"),
                 "DEL_FDBK": Hand(r, 48, 32, 127, "walk", period=(8, 20), jump=0.3),
                 "DEL_HPF": Hand(r, 64, 0, 127, "walk", period=(0.2, 1.5), jump=0.2),
                 "DEL_LPF": Hand(r, (lo + hi) / 2, lo, hi, "walk", period=(0.2, 1.5), jump=0.2),
                 "AGE": Hand(r, 64, style="hold")}
        elif k == "fdbk":
            h = {"DEL_TIME": Hand(r, 23, style="hold"), "DEL_WID": Hand(r, 64, style="hold"),
                 "DEL_FDBK": Hand(r, 0, 0, 127, "sweep", sweep_s=20.0), "DEL_HPF": Hand(r, 64, style="hold"),
                 "DEL_LPF": Hand(r, 127, style="hold"), "AGE": Hand(r, 0, style="hold")}
            self.mode = 3
        elif k == "time":
            h = {"DEL_TIME": Hand(r, 23, 0, 127, "walk", period=(0.5, 4), jump=0.3), "DEL_WID": Hand(r, 64, style="hold"),
                 "DEL_FDBK": Hand(r, 50, style="hold"), "DEL_HPF": Hand(r, 64, style="hold"),
                 "DEL_LPF": Hand(r, 127, style="hold"), "AGE": Hand(r, 32, style="hold")}
            self.bpm = Hand(r, 120, 40, 240, "walk", period=(4, 12), jump=0.4)
        else:   # human
            h = {kk: Hand(r, r.uniform(0, 127), 0, 127, "walk", period=(0.5, 6), jump=0.15) for kk in ECHO_KEYS}
            self.bpm = Hand(r, 120, 40, 240, "walk", period=(6, 20), jump=0.4)
            self.band = [Hand(r, v, 0, 8 if j in (1, 7, 13) else 127, "walk", period=(1, 10), jump=0.1)
                         for j, v in enumerate(self.tail17)]
            self.mixh = [Hand(r, 64, 0, 127, "walk", period=(2, 12), jump=0.2) for _ in range(3)]
        self.h = h
        self.s = {kk: 256.0 * hh.v for kk, hh in h.items()}           # the glided words
        self.sb = [256.0 * v for v in self.tail17] + [256.0 * v for v in self.mix3]
        self.mangle = None
        self.moved = []

    def step(self, b, fr):
        VC, FB = G["VC"], G["FB"]
        cfg, r = self.cfg, self.r
        k = cfg["kind"]
        moved = []
        # MODE
        if k == "filter" and not cfg.get("plain") and b >= self.mode_next:
            self.mode_next = b + int(BPS * r.uniform(10, 30))
            nm = 3 if self.mode == 6 else 6
            if b:
                moved.append("MODE")
            self.mode = nm
        if k == "human" and b >= self.mode_next:
            self.mode_next = b + int(BPS * r.uniform(3, 15))
            if b:
                self.mode = r.randrange(7)
                moved.append("MODE")
        if k == "fdbk":
            self.h["AGE"].v = 0.0 if b < self.cfg["seconds"] * BPS // 2 else 64.0
        VC.put(fr, "DEL_X", self.mode << 8)
        for kk, hh in self.h.items():
            t = 256.0 * hh.step(b)
            s = self.s[kk]
            ns = s + GLIDE * (t - s)
            if abs(ns - t) < 0.5:
                ns = t
            if int(round(ns)) != int(round(s)):
                moved.append(kk)
            self.s[kk] = ns
            w = int(round(ns)) & 0xffff
            if kk == "AGE":
                struct.pack_into("<H", fr, 0xa18, w)
            else:
                struct.pack_into("<H", fr, FB.FRAME_WORDS[kk], w)
        bpm = self.bpm.step(b)
        struct.pack_into("<H", fr, FB.FRAME_WORDS["TEMPO"], int(round(120 * bpm)) & 0xffff)
        # bands
        if self.band is not None:
            tg = [hh.step(b) for hh in self.band] + [hh.step(b) for hh in self.mixh]
            if self.mangle is None and r.random() < 1.0 / (BPS * 20):
                self.mangle = (b, r.choice((4, 10, 16)))             # a MUP fully up and down several times
            if self.mangle:
                b0, j = self.mangle
                ph = (b - b0) / (BPS * 1.5)
                if ph >= 4:
                    self.mangle = None
                else:
                    tg[j] = 127.0 if (ph % 1.0) < 0.5 else 0.0
            ns = []
            for j, (s, t) in enumerate(zip(self.sb, tg)):
                n = s + GLIDE * (256 * t - s)
                if int(round(n)) != int(round(s)):
                    moved.append("band%d" % j)
                ns.append(n)
            self.sb = ns
        vals = [int(round(x)) & 0xffff for x in self.sb] + [0] * 7
        struct.pack_into("<30H", fr, G["V2"].TAIL, *G["V2"].tail_words(1 + b, vals))
        self.moved = moved
        return moved


# ---------------------------------------------------------------- one recipe

class Fault(Exception):
    pass


def finite(x):
    return x == x and x not in (float("inf"), float("-inf"))


def probed(vm, probes, cap):
    """vm.call(HANDLER) with stops at the PCs in order (as vmrun.Runner._call_probed), under a cap of instructions"""
    from vm import STOP_PC, ST_STOP, VMError
    V = G["V"]
    i6, i7 = vm.reg("I6"), vm.reg("I7")
    vm.set_reg("R2", i6)
    vm.set_reg("I6", i7)
    vm.wr32(i7, i6)
    vm.wr32(i7 - 4, STOP_PC - 1)
    vm.set_reg("I7", i7 - 8)
    vm.pc = V.HANDLER
    n0 = vm.icount
    for pc, fn in list(probes) + [(STOP_PC, None)]:
        left = cap - (vm.icount - n0)
        st = vm.run(max(left, 1), stop=pc)
        if st != ST_STOP:
            # over the cap: sample where it runs, then see whether it ever returns
            pcs = []
            for _ in range(64):
                vm.run(9973, stop=pc)
                pcs.append(vm.pc)
                if vm.pc == pc:
                    break
            st2 = vm.run(10**8, stop=pc)
            raise Fault(json.dumps({"kind": "runaway", "stop_wanted": "%#x" % pc, "instructions": vm.icount - n0,
                                    "returned_within_1e8_more": st2 == ST_STOP,
                                    "pc_samples": sorted({"%#x" % p for p in pcs})[:40]}))
        if fn:
            fn()
    if vm.reg("I7") != i7 or vm.reg("I6") != i6:
        raise VMError("block handler: stack not restored")
    return vm.icount - n0


def func_of(sw):
    for f in G["info"]["functions"]:
        lo = int(f["sw"], 16)
        if lo <= sw < lo + f["bytes"] // 2:
            return f["file"] + ":" + f["function"] + " (" + f["region"] + ")"
    return None


def run_recipe(args):
    name, cfg, cmp_off = args
    setup(cmp_off)
    S, V, VC, FB, memguard = G["S"], G["V"], G["VC"], G["FB"], G["memguard"]
    from vm import VMError, watch as W, watch_log
    log = open(OUTD / f"{name}.log", "w")

    def say(*a):
        print(time.strftime("%H:%M:%S"), name, *a, file=log, flush=True)
    seo = G["seo"]
    nb = cfg["seconds"] * BPS
    t0 = time.time()
    vm = V.candidate_vm(G["stream"], cache=G["cache"])
    vm.lib.vm_set_profile(vm.h, 1)
    d = FB.FullDSP(vm=vm)
    ctl = Control(cfg)
    d.set(**ctl.words)
    d.set_raw(**VC.ROUTE_INFX)
    fr = d.frame
    src = Source(cfg["src"], random.Random(cfg["seed"] + 7))
    SE = S.SE_SLOT + 48
    base_depth = None
    res = {"name": name, "what": cfg["what"], "seconds_audio": cfg["seconds"], "blocks_planned": nb, "blocks_run": 0,
           "faults": [], "anomalies": {}, "insns": {}, "delay_call_insns": {}, "spikes": [], "l2_entries": {},
           "c4_entries": {}, "l2_entries_per_s_max": {}, "l2_instructions": 0, "stack_high_water": 0}
    anom = res["anomalies"]

    def note(kind, b, detail):
        e = anom.setdefault(kind, {"count": 0, "first": []})
        e["count"] += 1
        if len(e["first"]) < 12:
            e["first"].append({"block": b, "t_s": round(b / BPS, 3), "moved": ctl.moved[:8], **detail})
    entries = G["entries"]
    last_hits = {nm: 0 for _, nm, _ in entries}
    l2_pcs, l2_seen = [], 0
    ins_hist = {}
    del_hist = {}
    ins_max = del_max = 0
    peak = {"out": 0.0, "wet": 0.0, "tape": 0.0}
    trips0 = reinits0 = None
    blocks_cnt = None
    dl = {}

    def at_del_call():
        dl["n0"] = vm.icount

    def at_del_ret():
        dl["n"] = vm.icount - dl["n0"]
        dl["wet"] = vm.rdf(V.DEL_OUT[0], 32) + vm.rdf(V.DEL_OUT[1], 32)
    probes = [(V.DEL_CALL, at_del_call), (V.DEL_RET, at_del_ret)]
    paint_word = V.PAINT.to_bytes(4, "little")
    b = 0
    try:
        for b in range(nb):
            ctl.step(b, fr)
            if cfg.get("inject") and b == cfg["inject"][0]:         # the detectors' self-test (selftest)
                what = cfg["inject"][1]
                if what == "nan_tau":
                    vm.wrf(SE + seo["tau"], [float("nan")])
                elif what == "magic":
                    vm.wr32(S.SE_SLOT, 0)
                elif what == "rq":
                    vm.wr32(SE + seo["rq"], SE_RING + 5)
            L, R = src.block(b)
            vm.wr32(FB.SPI_HALF, b & 1)
            vm.write(FB.RX_BASE + 0x1000 * (b & 1), bytes(fr))
            h = vm.rd32(FB.DMA_HALF)
            vm.write(FB.CODEC_IN + 0x100 * h, struct.pack("<64I", *[FB.q31(v) for pair in zip(L, R) for v in pair]))
            paint = (b % 100 == 37)
            if paint:
                i7 = vm.reg("I7")
                vm.write(V.STACK_LO, paint_word * ((i7 - V.STACK_LO) // 4))
            wlo, whi, wname = G["watch"][b % 4]
            W(vm, wlo, whi)
            try:
                n = probed(vm, probes, CAP)
            except VMError as e:
                code, pc, msg = vm.trap_info()
                raise Fault(json.dumps({"kind": "trap", "message": str(e), "pc": "%#x" % vm.pc,
                                        "function": func_of(vm.pc)}))
            wl = watch_log(vm)
            W(vm, 0, 0)
            if wl:
                note("store_into_code", b, {"range": wname, "stores": ["%#x@pc %#x" % (ad, pc) for pc, ad, va in wl[:6]],
                                            "count": len(wl)})
            if paint:
                i7 = vm.reg("I7")
                raw = vm.read(V.STACK_LO, i7 - V.STACK_LO)
                low = next((V.STACK_LO + k for k in range(0, len(raw), 4) if raw[k:k + 4] != paint_word), i7)
                res["stack_high_water"] = max(res["stack_high_water"], i7 - low)
            # instructions
            kb = n // 10000
            ins_hist[kb] = ins_hist.get(kb, 0) + 1
            dn = dl.get("n", 0)
            del_hist[dn // 5000] = del_hist.get(dn // 5000, 0) + 1
            if n > ins_max:
                ins_max = n
            if dn > del_max:
                del_max = dn
            if n > SPIKE and len(res["spikes"]) < 40:
                res["spikes"].append({"block": b, "insns": n, "delay_call": dn, "moved": ctl.moved[:10]})
            # hardware stacks
            dep = vm.depths()
            if base_depth is None:
                base_depth = dep
            elif dep != base_depth:
                note("hw_stack_depth", b, {"depths": dep, "baseline": base_depth})
                base_depth = dep
            # outputs
            o = vm.rdf(FB.OUT_FLOATS, 64)
            for x in o:
                if not finite(x):
                    note("output_nan_inf", b, {})
                    break
                if abs(x) > peak["out"]:
                    peak["out"] = abs(x)
            if peak["out"] > 0.6931 and "output_over_ceiling" not in anom:
                note("output_over_ceiling", b, {"peak": peak["out"]})
            for x in dl.get("wet", ()):
                if not finite(x):
                    note("echo_wet_nan_inf", b, {})
                    break
                if abs(x) > peak["wet"]:
                    peak["wet"] = abs(x)
            if peak["wet"] > 0.7000001 and "echo_wet_over_ceiling" not in anom:
                note("echo_wet_over_ceiling", b, {"peak": peak["wet"]})
            # the tape echo
            mg, _, blk = struct.unpack("<3I", vm.read(S.SE_SLOT, 12))
            if mg != SE_MAGIC:
                note("seslot_magic", b, {"magic": "%#x" % mg})
            if blocks_cnt is not None and blk != (blocks_cnt + 1) & 0xffffffff:
                note("seslot_block_counter", b, {"was": blocks_cnt, "now": blk})
            blocks_cnt = blk
            w = struct.unpack("<i", vm.read(SE + seo["w"], 4))[0]
            raw = vm.read(SE + G["se_lo"], G["se_hi"] - G["se_lo"])
            fl = array.array("f", raw)
            it = array.array("i", raw)
            for j in G["se_float_idx"]:
                if not finite(fl[j]):
                    note("echo_state_nan_inf", b, {"offset": G["se_lo"] + 4 * j})
                    break
            gi = lambda f, k=0: it[(seo[f] - G["se_lo"]) // 4 + k]      # noqa: E731
            gf = lambda f, k=0: fl[(seo[f] - G["se_lo"]) // 4 + k]      # noqa: E731
            hw_ = struct.unpack("<i", vm.read(SE + seo["hw"], 4))[0]
            if not (0 <= w < SE_RING) or not (0 <= hw_ < SE_HIST):
                note("echo_index", b, {"w": w, "hw": hw_})
            for kk in range(3):
                rq, rf, tau = gi("rq", kk), gf("rf", kk), gf("tau", kk)
                if not (0 <= rq < SE_RING) or not (0.0 <= rf <= 1.0) or not (SE_TMIN <= tau <= SE_TMAX):
                    note("echo_head", b, {"head": kk, "rq": rq, "rf": rf, "tau": tau})
            if not (0 <= gi("mode") <= 6) or not (0 <= gi("pan") <= 2):
                note("echo_mode_pan", b, {"mode": gi("mode"), "pan": gi("pan")})
            evp = [gi("ev_pos", kk) for kk in range(33)]
            if any(not (0 <= x <= 32) for x in evp) or gi("ck_count") < 0:
                note("echo_crackle_events", b, {"ev_pos": evp[:8], "ck_count": gi("ck_count")})
            # the 32 frames written this block
            a0 = (w - 32) % SE_RING
            if a0 + 32 <= SE_RING:
                tp = vm.rdf(SE + 8 * a0, 64)
            else:
                k1 = SE_RING - a0
                tp = vm.rdf(SE + 8 * a0, 2 * k1) + vm.rdf(SE, 2 * (32 - k1))
            for x in tp:
                if not finite(x) or abs(x) > 1.0 + 1e-6:
                    note("tape_write", b, {"value": x})
                    break
                if abs(x) > peak["tape"]:
                    peak["tape"] = abs(x)
            # the compressor's guard
            tr = vm.rd32(VC.SLOT + S.OFF["trips"])
            ri = vm.rd32(VC.SLOT + S.OFF["reinits"])
            if trips0 is None:
                trips0, reinits0, tr_last, ri_last = tr, ri, tr, ri
            if tr != tr_last or ri != ri_last:
                note("comp_guard", b, {"trips": tr - trips0, "reinits": ri - reinits0})
                tr_last, ri_last = tr, ri
            gr = vm.rdf(VC.SLOT + S.OFF["gr0"], 3)
            if not all(finite(x) for x in gr):
                note("comp_gr_nan_inf", b, {"gr": gr})
            # once a second: function entries, L2 instructions
            if b % BPS == BPS - 1:
                for reg, nm, sw in entries:
                    pg = vm.pages.get(sw >> 12)
                    hcount = pg[sw & 0xfff].hits if pg is not None else 0
                    inc = hcount - last_hits[nm]
                    last_hits[nm] = hcount
                    tgt = res["l2_entries"] if reg.startswith("l2") else res["c4_entries"]
                    tgt[nm] = tgt.get(nm, 0) + inc
                    if reg.startswith("l2"):
                        res["l2_entries_per_s_max"][nm] = max(res["l2_entries_per_s_max"].get(nm, 0), inc)
                if len(vm.decoded) != l2_seen:
                    l2_seen = len(vm.decoded)
                    l2_pcs = [pc for pc in vm.decoded if any(lo <= pc < hi for lo, hi in G["l2_sw"])]
                memguard.check(3072)
            if b % (60 * BPS) == 60 * BPS - 1:
                ring = vm.rdf(SE, 2 * SE_RING)
                bad = sum(1 for x in ring if not finite(x) or abs(x) > 1.0 + 1e-6)
                if bad:
                    note("tape_scan", b, {"bad": bad})
                del ring
            if b % (10 * BPS) == 0:
                el = time.time() - t0
                say(f"block {b} ({b / BPS:.0f} s), {b / max(el, 1e-9):.1f} blocks/s, insns max {ins_max}, delay max "
                    f"{del_max}, anomalies {sum(v['count'] for v in anom.values())}, rss {memguard.peak_rss_mb():.0f} MB")
            res["blocks_run"] = b + 1
    except Fault as f:
        res["faults"].append(dict(json.loads(str(f)), block=b, t_s=round(b / BPS, 3), moved=ctl.moved[:10],
                                  knobs={kk: round(v / 256, 2) for kk, v in ctl.s.items()}, mode=ctl.mode))
        say("FAULT", res["faults"][-1])
    except Exception as e:      # a harness or VM error is a fault too, with where it was
        res["faults"].append({"kind": "error", "message": repr(e)[:500], "block": b, "pc": "%#x" % vm.pc,
                              "function": func_of(vm.pc)})
        say("ERROR", res["faults"][-1])
    # the L2 instructions executed, and anything run in the L2 ranges outside our functions
    tot, outside = 0, []
    l2f = [(int(f["sw"], 16), int(f["sw"], 16) + f["bytes"] // 2) for f in G["info"]["functions"]
           if f["region"].startswith("l2")]
    for pc in [pc for pc in vm.decoded if any(lo <= pc < hi for lo, hi in G["l2_sw"])]:
        pg = vm.pages.get(pc >> 12)
        hcount = pg[pc & 0xfff].hits if pg is not None else 0
        tot += hcount
        if hcount and not any(lo <= pc < hi for lo, hi in l2f):
            outside.append("%#x" % pc)
    res["l2_instructions"] = tot
    res["l2_executed_outside_our_functions"] = outside[:10]
    blocks = max(1, res["blocks_run"])
    res["insns"] = {"max": ins_max, "mean_est": round(sum((k * 10000 + 5000) * c for k, c in ins_hist.items()) / blocks),
                    "est_load_max_pct": round(100 * VC.LOAD(ins_max), 1),
                    "hist_10k": {str(k * 10): c for k, c in sorted(ins_hist.items())}}
    res["delay_call_insns"] = {"max": del_max, "hist_5k": {str(k * 5): c for k, c in sorted(del_hist.items())}}
    res["peaks"] = {k: round(v, 5) for k, v in peak.items()}
    secs = blocks / BPS
    res["l2_entries_per_s"] = {k: round(v / secs, 3) for k, v in res["l2_entries"].items() if v}
    res["l2_instructions_per_s"] = round(tot / secs)
    res["wall_s"] = round(time.time() - t0)
    res["blocks_per_s"] = round(blocks / max(res["wall_s"], 1), 1)
    res["peak_rss_mb"] = round(memguard.peak_rss_mb())
    res["ok"] = not res["faults"] and not anom and not outside
    (OUTD / f"{name}.json").write_text(json.dumps(res, indent=1))
    say("DONE", json.dumps({k: res[k] for k in ("blocks_run", "faults", "insns", "peaks", "l2_entries_per_s",
                                                    "stack_high_water", "ok")})[:3000])
    log.close()
    del vm, d
    gc.collect()
    return name, res["ok"], res["blocks_run"], res["faults"][:1], sorted(anom)


def cmp_offsets():
    import verify_sc01 as S
    return S.VC.offsets(OUTD / "cmp_offsets", "CMP3")


def bench():
    off = cmp_offsets()
    out = {}
    for name, prof in (("R4a", 1), ("R4a", 0), ("R1a", 1)):
        setup(off)
        cfg = dict(recipes(1.0)[name], seconds=1)
        V, FB, VC = G["V"], G["FB"], G["VC"]
        vm = V.candidate_vm(G["stream"], cache=G["cache"])
        vm.lib.vm_set_profile(vm.h, prof)
        d = FB.FullDSP(vm=vm)
        ctl = Control(cfg)
        d.set(**ctl.words)
        d.set_raw(**VC.ROUTE_INFX)
        src = Source(cfg["src"], random.Random(1))
        t0 = time.time()
        n = 450
        for b in range(n):
            ctl.step(b, d.frame)
            L, R = src.block(b)
            vm.wr32(FB.SPI_HALF, b & 1)
            vm.write(FB.RX_BASE + 0x1000 * (b & 1), bytes(d.frame))
            h = vm.rd32(FB.DMA_HALF)
            vm.write(FB.CODEC_IN + 0x100 * h, struct.pack("<64I", *[FB.q31(v) for pair in zip(L, R) for v in pair]))
            probed(vm, [], CAP)
        el = time.time() - t0
        out[f"{name} profile {prof}"] = round(n / el, 1)
        print(name, "profile", prof, round(n / el, 1), "blocks/s (bare handler)", flush=True)
        del vm
        gc.collect()
    # the full checks
    cfg = dict(recipes(1.0)["R4a"], seconds=1)
    t0 = time.time()
    r = run_recipe(("bench-R4a", cfg, off))
    el = time.time() - t0
    print("full checks R4a 1 s:", round(BPS / el, 1), "blocks/s incl. setup", r, flush=True)
    out["full R4a"] = round(BPS / el, 1)
    (OUTD / "bench.json").write_text(json.dumps(out, indent=1))


def selftest():
    """each detector fires: a NaN in a head's delay, the SeSlot magic cleared (se_init's re-init: a runaway block),
    a read index past the tape"""
    off = cmp_offsets()
    out = {}
    for what in ("nan_tau", "magic", "rq"):
        cfg = dict(recipes(1.0)["R4a"], seconds=1, inject=(700, what))
        name, ok, blocks, faults, anoms = run_recipe((f"selftest-{what}", cfg, off))
        out[what] = {"ok_should_be_false": ok, "blocks": blocks, "faults": faults, "anomalies": anoms}
        print(what, out[what], flush=True)
    (OUTD / "selftest.json").write_text(json.dumps(out, indent=1))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=("bench", "run", "selftest"))
    ap.add_argument("--scale", type=float, default=1.0)
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--only")
    a = ap.parse_args()
    OUTD.mkdir(parents=True, exist_ok=True)
    if a.cmd == "bench":
        bench()
        return 0
    if a.cmd == "selftest":
        selftest()
        return 0
    off = cmp_offsets()
    rs = recipes(a.scale)
    names = a.only.split(",") if a.only else list(rs)
    # longest first, so three workers finish together
    names.sort(key=lambda n: -rs[n]["seconds"])
    total = sum(rs[n]["seconds"] for n in names)
    print(f"{len(names)} recipes, {total} s of audio ({total * BPS} blocks), {a.workers} workers", flush=True)
    t0 = time.time()
    summary = {}
    with ProcessPoolExecutor(a.workers, max_tasks_per_child=1) as ex:
        futs = {ex.submit(run_recipe, (n, rs[n], off)): n for n in names}
        for f in as_completed(futs):
            n = futs[f]
            try:
                name, ok, blocks, faults, anoms = f.result()
            except Exception as e:
                name, ok, blocks, faults, anoms = n, False, 0, [repr(e)[:300]], []
            summary[name] = {"ok": ok, "blocks": blocks, "faults": faults, "anomalies": anoms}
            print(time.strftime("%H:%M:%S"), name, "ok" if ok else "NOT OK", blocks, "blocks", faults, anoms,
                  flush=True)
    summary["wall_s"] = round(time.time() - t0)
    (OUTD / ("summary.json" if not a.only else "summary-%s.json" % a.only.replace(",", "-"))).write_text(
        json.dumps(summary, indent=1))
    print("all done", summary["wall_s"], "s", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
