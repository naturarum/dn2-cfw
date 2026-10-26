#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""CMP4 soak (2026-10-22; notes/T6.3-cmp4.md): CMP4's built DSP image, out/CMP4/CMP4-dsp/section7.bin, in sharcvm for
minutes of audio with a hand on every knob: the band knobs and MIX through the frame tail, MUP mangles, the flanger's
knobs, the stock delay's knobs (FDBK up to 127, X toggled) and tempo changes. soak_sc01.py's harness (its Hand, Source,
probed and Fault) without the tape echo's checks: the delay slot is Elektron's own delay again.

    python3 tools/dspsplice/soak_cmp4.py run [--scale 1.0] [--workers 3] [--only K1,K2]
    python3 tools/dspsplice/soak_cmp4.py run --dsp out/FL01/FL01-dsp --out out/FL01/soak --only K1,K2,K3,F1 --scale 0.5
                                                  FL01 (notes/T5.6): the same harness on FL01's DSP image (CMP4's
                                                  but the flanger glue), and F1: the flanger's knobs spun hard
    python3 tools/dspsplice/soak_cmp4.py run --dsp out/FL02/FL02-dsp --out out/FL02/soak --only K1,K2,K3,F1,S1 --scale 0.5
                                                  FL02 (notes/T5.6 7): FL01's recipes on FL02's image and S1: SPD
                                                  between synced and free values as FUNC + turn makes them
    python3 tools/dspsplice/soak_cmp4.py run --dsp out/SP01/SP01-dsp --out out/SP01/soak --rev --only K1,K2,K3,F1,S1,R1
                                                  SP01 (notes/SP01-spring-pack.md): DT01's recipes on SP01's image (the
                                                  spring in the reverb slot), R1: every reverb knob (MODE and WDTH, k 48,
                                                  among them) spun hard; --rev adds the reverb call's checks

Every block (1,500 blocks = 1 s): the whole handler under a cap of CAP instructions (a block over it = a runaway, with
where it loops); any sharcvm trap; the instructions of the block and of the delay call; the outputs finite and under
the master ceiling (0.6931); the compressor's guard counters (trips, re-inits) and its gain reductions finite; the
hardware stacks back to their depth; one of four ranges watched for stores, in turn (L1 block 1 code, code4, block 2's
compressor code, and 0x30ff76-0x318000: the rest of block 2's code area with 0x315f8c-0x318000, where nothing of ours
is). Every 100 blocks the C stack's high-water mark (painted). Results: out/CMP4/soak/<recipe>.json and .log.
With --rev (SP01): also the reverb call's instructions and its wet output (finite, its peak), a fifth watched range in
turn, the spring's DDR state (0x80000018-0x8005912c), whose stores must all come from our code2, and at the end the
spring glue's slot (magic "SPN1", fresh 0, the knobs last applied, the spring's state finite).
"""
import argparse
import gc
import json
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
import soak_sc01 as SK  # noqa: E402

OUTD = ROOT / "out/CMP4/soak"
DSP = ROOT / "out/CMP4/CMP4-dsp"
BPS, GLIDE, CAP, SPIKE = SK.BPS, SK.GLIDE, SK.CAP, SK.SPIKE
G = SK.G
DELAY_KEYS = ("DEL_TIME", "DEL_WID", "DEL_FDBK", "DEL_HPF", "DEL_LPF")
FLG_KEYS = ("CHR_DPTH", "CHR_SPD", "CHR_HPF", "CHR_WDTH")
WATCH = [(0x2dd600, 0x2e1c40, "L1 block 1 code (stubs, flanger)"),
         (0x2e2900, 0x2e4f46, "code4 (glue, cmp_tail)"),
         (0x308400, 0x30ff76, "L1 block 2: the compressor's code"),
         (0x30ff76, 0x318000, "L1 block 2: the rest of the code area, with 0x315f8c-0x318000")]
# SP01 (--rev): the reverb's call (T3.4) and the spring's DDR state (tools/dspsplice/src/spng_glue.c)
REV_CALL, REV_RET = 0x1c1d11, 0x1c1d18
REV_OUT = (0x25b368, 0x25b3e8)
SPG = (0x80000018, 0x8005912c, "the spring's DDR state (stores from our code2 only)")
SPG_MAGIC = 0x53504e31
REV_KEYS = ("REV_PRE", "REV_DEC", "REV_FREQ", "REV_GAIN", "REV_HPF", "REV_LPF")
K48 = 0xa28


def setup(cmp_off, dsp=DSP):
    if G:
        return
    import verify_sc01 as S
    import memguard
    import build as BD
    S.OFF.update(cmp_off)
    stream, info, cache = S.load_image(Path(dsp), json.loads((Path(dsp) / "build.json").read_text())["name"])
    G.update(S=S, V=S.V, VC=S.VC, V2=S.V2, FB=S.V.fullblock, memguard=memguard, BD=BD, stream=stream, info=info,
             cache=cache)


def recipes(scale):
    m = lambda x: max(30, int(x * scale))     # noqa: E731   seconds
    return {
        "K1": dict(what="every band knob and MIX walked, MUP mangles (about every 20 s), the flanger's four knobs, the "
                        "stock delay's knobs (FDBK 0..127, X toggled), tempo changes, drums/mix and noise, reverb on",
                   seconds=m(240), seed=601, src="mixed", fb=(0, 127)),
        "K2": dict(what="as K1, other seed, the delay's FDBK held high (64..127: the stock runaway), MUP mangles about "
                        "every 8 s", seconds=m(240), seed=602, src="mixed", fb=(64, 127), mangle_s=8),
        "K3": dict(what="idle: drum hits 2 s then silence, everything on (bands at the starting point, MIX 64, the "
                        "delay at FDBK 64, the flanger), nothing moves", seconds=m(180), seed=603, src="hits", idle=True),
        # FL01 (only with --only): the flanger's four knobs spun hard over their whole range (TIME 0.05 ms .. 25.7 ms
        # and back in one step), the rest as K1
        "F1": dict(what="as K1, other seed, the flanger's four knobs moved every 0.1-1.5 s, half of the moves a jump "
                        "to anywhere in 0..127 (TIME across its whole range in one step)", seconds=m(240), seed=604,
                   src="mixed", fb=(0, 127), flg=dict(period=(0.1, 1.5), jump=0.5), extra=True),
        # FL02 (only with --only): F1 with SPD as the FL02 UI makes it: FUNC + turn to one of the 12 synced words
        # ((a << 8) | i, notes/T5.6 7), a turn without FUNC (the synced word +- whole steps: free, low byte kept), whole
        # numbers, each through the ColdFire's smoother (the unit's EMAC arithmetic), every 0.2-3 s; the tempo walking
        "S1": dict(what="as F1, other seed, SPD between the 12 synced values (FUNC + turn), free whole numbers and the "
                        "free values a turn without FUNC leaves (through the unit's smoother), every 0.2-3 s; the tempo "
                        "walking 40..240", seconds=m(240), seed=605, src="mixed", fb=(0, 127),
                   flg=dict(period=(0.1, 1.5), jump=0.5), spd_sync=True, extra=True),
        # SP01 (only with --only): the reverb page spun hard: PRE, DEC, MODE (FREQ's word), DWEL (GAIN's), HPF, TONE
        # (LPF's) and WDTH (k 48, frame word 0xa28) moved every 0.1-1.5 s, half of the moves a jump to anywhere in
        # 0..127, each through the ColdFire's glide; the reverb's sends and VOL up; the rest as K1
        "R1": dict(what="as K1, other seed, the reverb's seven knobs (MODE and WDTH among them) moved every 0.1-1.5 s, "
                        "half of the moves a jump to anywhere in 0..127, the reverb's sends and VOL up", seconds=m(240),
                   seed=606, src="mixed", fb=(0, 127), rev=dict(period=(0.1, 1.5), jump=0.5), extra=True),
    }

SYNC_WORDS = [(a << 8) | i for i, a in enumerate((9, 23, 37, 52, 66, 80, 86, 94, 100, 108, 122, 126), 1)]


def unit_smooth(raw, st):
    """one frame of the ColdFire's one-pole for one word, the unit's EMAC arithmetic (scripts/t63c_e2e.py)"""
    r = raw - 0x10000 if raw & 0x8000 else raw
    x = (((0x03d7 * r) << 1) + ((0x7c290000 * st) >> 31)) & 0xffffffff
    st = x - (1 << 32) if x & 0x80000000 else x
    return st, ((st + 0x8000) >> 16) & 0xffff


class Control:
    """the knob targets of one recipe: the ColdFire's glide on the stock frame words, the frame tail for the bands"""

    def __init__(self, cfg):
        VC = G["VC"]
        import verify_flg as FL
        self.cfg = cfg
        r = self.r = random.Random(cfg["seed"])
        allfx = dict(IN_CHR_L=100, IN_CHR_R=100, IN_DEL_L=100, IN_DEL_R=100, IN_REV_L=60, IN_REV_R=60, CHR_DEL=40,
                     CHR_REV=30, CHR_VOL=90, DEL_REV=40, DEL_VOL=100, REV_VOL=90, REV_DEC=90, **FL.KNOBS)
        self.words = dict(VC.BASE, **allfx, **VC.comp(32, 24, 32, 64, 3, 64), DEL_TIME=23, DEL_X=0, DEL_WID=64,
                          DEL_FDBK=64, DEL_HPF=0, DEL_LPF=127)
        self.tail17 = list(G["S"].knobs17(G["S"].START))
        idle = cfg.get("idle")
        sty = "hold" if idle else "walk"
        lo, hi = cfg.get("fb", (0, 127))
        h = {"DEL_TIME": SK.Hand(r, 23, 0, 127, sty, period=(1, 8), jump=0.2),
             "DEL_WID": SK.Hand(r, 64, 0, 127, sty, period=(1, 8), jump=0.2),
             "DEL_FDBK": SK.Hand(r, 64 if idle else (lo + hi) / 2, lo, hi, sty, period=(2, 10), jump=0.25),
             "DEL_HPF": SK.Hand(r, 10, 0, 127, sty, period=(1, 8), jump=0.2),
             "DEL_LPF": SK.Hand(r, 110, 0, 127, sty, period=(1, 8), jump=0.2)}
        fh = cfg.get("flg", dict(period=(0.5, 6), jump=0.15))
        for k_ in FLG_KEYS:
            h[k_] = SK.Hand(r, FL.KNOBS[k_], 0, 127, sty, **fh)
        if cfg.get("rev"):
            # R1: the reverb page's knobs from the spring's init patch, the sends and VOL up
            self.words.update(IN_REV_L=100, IN_REV_R=100, CHR_REV=80, DEL_REV=90, REV_VOL=110)
            for k_, v in zip(REV_KEYS, (0, 64, 64, 64, 0, 127)):
                h[k_] = SK.Hand(r, v, 0, 127, sty, **cfg["rev"])
        self.k48 = SK.Hand(r, 96, 0, 127, sty, **cfg["rev"]) if cfg.get("rev") else None
        self.k48_s = 256.0 * 96
        self.h = h
        self.bpm = SK.Hand(r, 120, 40, 240, "hold" if idle else "walk", period=(6, 20), jump=0.4)
        self.x, self.x_next = 0, 0
        self.band = None if idle else [SK.Hand(r, v, 0, 8 if j in (1, 7, 13) else 127, "walk", period=(1, 10), jump=0.1)
                                       for j, v in enumerate(self.tail17)]
        self.mixh = None if idle else [SK.Hand(r, 64, 0, 127, "walk", period=(2, 12), jump=0.2) for _ in range(3)]
        self.s = {kk: 256.0 * hh.v for kk, hh in h.items()}
        self.spd_t = int(256 * FL.KNOBS["CHR_SPD"]) & 0xff00          # S1: the SPD word's target and smoother
        self.spd_st, self.spd_w, self.spd_next, self.spd_moves, self.spd_synced = self.spd_t << 16, self.spd_t, 0, 0, 0
        self.sb = [256.0 * v for v in self.tail17] + [256.0 * 64] * 3
        self.mangle = None
        self.moved = []

    def step(self, b, fr):
        FB, V2 = G["FB"], G["V2"]
        cfg, r = self.cfg, self.r
        moved = []
        if not cfg.get("idle") and b >= self.x_next:
            self.x_next = b + int(BPS * r.uniform(5, 20))
            if b:
                self.x ^= 1
                moved.append("DEL_X")
        struct.pack_into("<H", fr, FB.FRAME_WORDS["DEL_X"], self.x << 8)
        if cfg.get("spd_sync"):
            if b >= self.spd_next:
                self.spd_next = b + int(BPS * r.uniform(0.2, 3.0))
                x = r.random()
                if x < 0.4:
                    self.spd_t = r.choice(SYNC_WORDS)
                elif x < 0.6 and self.spd_t in SYNC_WORDS:
                    self.spd_t = min(max(self.spd_t + 0x100 * r.choice((-3, -1, 1, 2)), self.spd_t & 0xff), 0x7f00)
                else:
                    self.spd_t = r.randrange(128) << 8
                self.spd_moves += 1
                self.spd_synced += self.spd_t in SYNC_WORDS
            self.spd_st, w = unit_smooth(self.spd_t, self.spd_st)
            if w != self.spd_w:
                moved.append("CHR_SPD")
            self.spd_w = w
            struct.pack_into("<H", fr, FB.FRAME_WORDS["CHR_SPD"], w)
        for kk, hh in self.h.items():
            if kk == "CHR_SPD" and cfg.get("spd_sync"):
                continue
            t = 256.0 * hh.step(b)
            s = self.s[kk]
            ns = s + GLIDE * (t - s)
            if abs(ns - t) < 0.5:
                ns = t
            if int(round(ns)) != int(round(s)):
                moved.append(kk)
            self.s[kk] = ns
            struct.pack_into("<H", fr, FB.FRAME_WORDS[kk], int(round(ns)) & 0xffff)
        if self.k48 is not None:
            t = 256.0 * self.k48.step(b)
            ns = self.k48_s + GLIDE * (t - self.k48_s)
            if abs(ns - t) < 0.5:
                ns = t
            if int(round(ns)) != int(round(self.k48_s)):
                moved.append("K48")
            self.k48_s = ns
            struct.pack_into("<H", fr, K48, int(round(ns)) & 0xffff)
        struct.pack_into("<H", fr, FB.FRAME_WORDS["TEMPO"], int(round(120 * self.bpm.step(b))) & 0xffff)
        if self.band is not None:
            tg = [hh.step(b) for hh in self.band] + [hh.step(b) for hh in self.mixh]
            if self.mangle is None and r.random() < 1.0 / (BPS * cfg.get("mangle_s", 20)):
                self.mangle = (b, r.choice((4, 4, 10, 16)))             # a MUP fully up and down, LOW twice as often
            if self.mangle:
                b0, j = self.mangle
                ph = (b - b0) / (BPS * 1.5)
                if ph >= 4:
                    self.mangle = None
                else:
                    tg[j] = 127.0 if (ph % 1.0) < 0.5 else 0.0
                    moved.append("MUP%d" % (j // 6))
            ns = []
            for j, (s, t) in enumerate(zip(self.sb, tg)):
                n = s + GLIDE * (256 * t - s)
                if int(round(n)) != int(round(s)):
                    moved.append("band%d" % j)
                ns.append(n)
            self.sb = ns
        vals = [int(round(x)) & 0xffff for x in self.sb] + [0] * 7
        struct.pack_into("<30H", fr, V2.TAIL, *V2.tail_words(1 + b, vals))
        self.moved = moved
        return moved


def func_of(pc):
    syms = sorted((int(v, 16), k) for k, v in G["info"]["symbols"].items())
    best = None
    for a, k in syms:
        if a <= pc:
            best = k
    lo = [p for p in G["info"]["placed"] if p["sw"] and int(p["sw"], 16) <= pc < int(p["sw"], 16) + p["bytes"] // 2]
    return ("%s in %s" % (best, lo[0]["section"])) if lo else "stock code"


def run_recipe(args):
    name, cfg, cmp_off, dsp, outd = args
    setup(cmp_off, dsp)
    OUTD = Path(outd)
    S, V, VC, FB, memguard = G["S"], G["V"], G["VC"], G["FB"], G["memguard"]
    from vm import VMError, watch as W, watch_log
    log = open(OUTD / f"{name}.log", "w")

    def say(*a):
        print(time.strftime("%H:%M:%S"), name, *a, file=log, flush=True)
    nb = cfg["seconds"] * BPS
    t0 = time.time()
    vm = V.candidate_vm(G["stream"], cache=G["cache"])
    d = FB.FullDSP(vm=vm)
    ctl = Control(cfg)
    d.set(**ctl.words)
    d.set_raw(**VC.ROUTE_INFX)
    fr = d.frame
    src = SK.Source(cfg["src"], random.Random(cfg["seed"] + 7))
    base_depth = None
    res = {"name": name, "what": cfg["what"], "seconds_audio": cfg["seconds"], "blocks_planned": nb, "blocks_run": 0,
           "faults": [], "anomalies": {}, "insns": {}, "delay_call_insns": {}, "spikes": [], "stack_high_water": 0,
           "mangles": 0, "x_toggles": 0}
    anom = res["anomalies"]

    def note(kind, b, detail):
        e = anom.setdefault(kind, {"count": 0, "first": []})
        e["count"] += 1
        if len(e["first"]) < 12:
            e["first"].append({"block": b, "t_s": round(b / BPS, 3), "moved": ctl.moved[:8], **detail})
    ins_hist, del_hist = {}, {}
    ins_max = del_max = 0
    peak = {"out": 0.0, "delay_out": 0.0}
    trips0 = None
    dl = {}

    def at_del_call():
        dl["n0"] = vm.icount

    def at_del_ret():
        dl["n"] = vm.icount - dl["n0"]
        dl["wet"] = vm.rdf(V.DEL_OUT[0], 32) + vm.rdf(V.DEL_OUT[1], 32)
    probes = [(V.DEL_CALL, at_del_call), (V.DEL_RET, at_del_ret)]
    rev = cfg.get("rev_probe")
    rv = {}
    rev_max, rev_hist, code2 = 0, {}, None
    if rev:
        res.update(rev_call_insns={}, rev_peak=0.0)
        c2 = [p for p in G["info"]["placed"] if p["section"] == "t38_code2"][0]
        code2 = (int(c2["sw"], 16), int(c2["sw"], 16) + c2["bytes"] // 2)

        def at_rev_call():
            rv["n0"] = vm.icount

        def at_rev_ret():
            rv["n"] = vm.icount - rv["n0"]
            rv["wet"] = vm.rdf(REV_OUT[0], 32) + vm.rdf(REV_OUT[1], 32)
        probes += [(REV_CALL, at_rev_call), (REV_RET, at_rev_ret)]
    watch = WATCH + ([SPG] if rev else [])
    paint_word = V.PAINT.to_bytes(4, "little")
    b = 0
    was_mangle = False
    try:
        for b in range(nb):
            ctl.step(b, fr)
            if ctl.mangle and not was_mangle:
                res["mangles"] += 1
            was_mangle = bool(ctl.mangle)
            res["x_toggles"] += "DEL_X" in ctl.moved
            L, R = src.block(b)
            vm.wr32(FB.SPI_HALF, b & 1)
            vm.write(FB.RX_BASE + 0x1000 * (b & 1), bytes(fr))
            h = vm.rd32(FB.DMA_HALF)
            vm.write(FB.CODEC_IN + 0x100 * h, struct.pack("<64I", *[FB.q31(v) for pair in zip(L, R) for v in pair]))
            paint = (b % 100 == 37)
            if paint:
                i7 = vm.reg("I7")
                vm.write(V.STACK_LO, paint_word * ((i7 - V.STACK_LO) // 4))
            wlo, whi, wname = watch[b % len(watch)]
            W(vm, wlo, whi)
            try:
                n = SK.probed(vm, probes, CAP)
            except VMError as e:
                raise SK.Fault(json.dumps({"kind": "trap", "message": str(e), "pc": "%#x" % vm.pc,
                                           "function": func_of(vm.pc)}))
            wl = watch_log(vm)
            W(vm, 0, 0)
            if rev and wlo == SPG[0]:
                wl = [w_ for w_ in wl if not code2[0] <= w_[0] < code2[1]]     # the spring's own stores
            if wl:
                note("store_into_watched_range", b, {"range": wname, "count": len(wl),
                                                     "stores": ["%#x@pc %#x (%s)" % (ad, pc, func_of(pc))
                                                                for pc, ad, va in wl[:6]]})
            if paint:
                i7 = vm.reg("I7")
                raw = vm.read(V.STACK_LO, i7 - V.STACK_LO)
                low = next((V.STACK_LO + k for k in range(0, len(raw), 4) if raw[k:k + 4] != paint_word), i7)
                res["stack_high_water"] = max(res["stack_high_water"], i7 - low)
            kb = n // 10000
            ins_hist[kb] = ins_hist.get(kb, 0) + 1
            dn = dl.get("n", 0)
            del_hist[dn // 5000] = del_hist.get(dn // 5000, 0) + 1
            ins_max, del_max = max(ins_max, n), max(del_max, dn)
            if n > SPIKE and len(res["spikes"]) < 40:
                res["spikes"].append({"block": b, "insns": n, "delay_call": dn, "moved": ctl.moved[:10]})
            dep = vm.depths()
            if base_depth is None:
                base_depth = dep
            elif dep != base_depth:
                note("hw_stack_depth", b, {"depths": dep, "baseline": base_depth})
                base_depth = dep
            for x in vm.rdf(FB.OUT_FLOATS, 64):
                if not SK.finite(x):
                    note("output_nan_inf", b, {})
                    break
                peak["out"] = max(peak["out"], abs(x))
            if peak["out"] > 0.6931 and "output_over_ceiling" not in anom:
                note("output_over_ceiling", b, {"peak": peak["out"]})
            if rev:
                rn = rv.get("n", 0)
                rev_max = max(rev_max, rn)
                rev_hist[rn // 5000] = rev_hist.get(rn // 5000, 0) + 1
                for x in rv.get("wet", ()):
                    if not SK.finite(x):
                        note("reverb_out_nan_inf", b, {})
                        break
                    res["rev_peak"] = max(res["rev_peak"], abs(x))
            for x in dl.get("wet", ()):
                if not SK.finite(x):
                    note("delay_out_nan_inf", b, {})
                    break
                peak["delay_out"] = max(peak["delay_out"], abs(x))
            tr = vm.rd32(VC.SLOT + S.OFF["trips"])
            ri = vm.rd32(VC.SLOT + S.OFF["reinits"])
            if trips0 is None:
                trips0, reinits0, tr_last, ri_last = tr, ri, tr, ri
            if tr != tr_last or ri != ri_last:
                note("comp_guard", b, {"trips": tr - trips0, "reinits": ri - reinits0})
                tr_last, ri_last = tr, ri
            gr = vm.rdf(VC.SLOT + S.OFF["gr0"], 3)
            if not all(SK.finite(x) for x in gr):
                note("comp_gr_nan_inf", b, {"gr": gr})
            if b % BPS == BPS - 1:
                memguard.check(3072)
            if b % (10 * BPS) == 0:
                el = time.time() - t0
                say(f"block {b} ({b / BPS:.0f} s), {b / max(el, 1e-9):.1f} blocks/s, insns max {ins_max}, delay max "
                    f"{del_max}, anomalies {sum(v['count'] for v in anom.values())}, rss {memguard.peak_rss_mb():.0f} MB")
            res["blocks_run"] = b + 1
    except SK.Fault as f:
        res["faults"].append(dict(json.loads(str(f)), block=b, t_s=round(b / BPS, 3), moved=ctl.moved[:10],
                                  knobs={kk: round(v / 256, 2) for kk, v in ctl.s.items()}))
        say("FAULT", res["faults"][-1])
    except Exception as e:      # a harness or VM error is a fault too, with where it was
        res["faults"].append({"kind": "error", "message": repr(e)[:500], "block": b, "pc": "%#x" % vm.pc,
                              "function": func_of(vm.pc)})
        say("ERROR", res["faults"][-1])
    try:        # the flanger's slot at the end: flg_init's magic (FLG1 on CMP4, FLG2 on FL01, FLG3 on FL02), the knobs
        # last applied, and (FLG3) the sync field (bits 0-7 the FL_SYNC playing)
        mg = vm.rd32(0x300000)
        res["flg_slot"] = {"magic": "%#x" % mg, "fresh": vm.rd32(0x300004), "word8_sync": "%#x" % vm.rd32(0x300008),
                           "last": [round(x, 3) for x in vm.rdf(0x30000c, 4)]}
        if cfg.get("spd_sync"):
            res["spd_moves"], res["spd_moves_to_sync"] = ctl.spd_moves, ctl.spd_synced
        if mg not in (0x464c4731, 0x464c4732, 0x464c4733) or res["flg_slot"]["fresh"] != 0:
            note("flg_slot", b, res["flg_slot"])
    except Exception as e:
        note("flg_slot_unreadable", b, {"error": repr(e)[:200]})
    if rev:     # the spring's slot (spng_glue.c SpgSlot): magic, fresh, the knobs last applied, every float finite
        try:
            raw = vm.read(SPG[0], 43072)
            fl = struct.unpack("<%df" % (len(raw) // 4), raw)
            res["spg_slot"] = {"magic": "%#x" % vm.rd32(SPG[0]), "fresh": vm.rd32(SPG[0] + 4),
                               "last": [round(x, 3) for x in vm.rdf(SPG[0] + 8, 7)],
                               "nonfinite_words": sum(1 for x in fl[2:] if not SK.finite(x)),
                               "max_abs_ring": round(max(abs(x) for x in fl[-3 * 2048 - 4096:]), 5)}
            if vm.rd32(SPG[0]) != SPG_MAGIC or res["spg_slot"]["fresh"] != 0 or res["spg_slot"]["nonfinite_words"]:
                note("spg_slot", b, res["spg_slot"])
        except Exception as e:
            note("spg_slot_unreadable", b, {"error": repr(e)[:200]})
        res["rev_call_insns"] = {"max": rev_max, "hist_5k": {str(k * 5): c for k, c in sorted(rev_hist.items())}}
        res["rev_peak"] = round(res["rev_peak"], 5)
    blocks = max(1, res["blocks_run"])
    res["insns"] = {"max": ins_max, "mean_est": round(sum((k * 10000 + 5000) * c for k, c in ins_hist.items()) / blocks),
                    "est_load_max_pct": round(100 * VC.LOAD(ins_max), 1),
                    "hist_10k": {str(k * 10): c for k, c in sorted(ins_hist.items())}}
    res["delay_call_insns"] = {"max": del_max, "hist_5k": {str(k * 5): c for k, c in sorted(del_hist.items())}}
    res["peaks"] = {k: round(v, 5) for k, v in peak.items()}
    res["wall_s"] = round(time.time() - t0)
    res["blocks_per_s"] = round(blocks / max(res["wall_s"], 1), 1)
    res["peak_rss_mb"] = round(memguard.peak_rss_mb())
    res["ok"] = not res["faults"] and not anom
    (OUTD / f"{name}.json").write_text(json.dumps(res, indent=1))
    say("DONE", json.dumps({k: res[k] for k in ("blocks_run", "faults", "insns", "peaks", "stack_high_water", "mangles",
                                                    "ok")})[:3000])
    log.close()
    del vm, d
    gc.collect()
    return name, res["ok"], res["blocks_run"], res["faults"][:1], sorted(anom)


def cmp_offsets():
    import verify_sc01 as S
    return S.VC.offsets(OUTD / "cmp_offsets", "CMP3")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=("run",))
    ap.add_argument("--scale", type=float, default=1.0)
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--only")
    ap.add_argument("--dsp", default=str(DSP), help="the DSP build directory (section7.bin, build.json)")
    ap.add_argument("--out", default=str(OUTD), help="where the results go")
    ap.add_argument("--rev", action="store_true", help="SP01: the reverb call's checks (the spring in the reverb slot)")
    a = ap.parse_args()
    outd = Path(a.out).resolve()
    outd.mkdir(parents=True, exist_ok=True)
    off = cmp_offsets()
    rs = recipes(a.scale)
    if a.rev:
        for c_ in rs.values():
            c_["rev_probe"] = True
    names = a.only.split(",") if a.only else [n for n in rs if not rs[n].get("extra")]
    names.sort(key=lambda n: -rs[n]["seconds"])
    total = sum(rs[n]["seconds"] for n in names)
    print(f"{len(names)} recipes, {total} s of audio ({total * BPS} blocks), {a.workers} workers", flush=True)
    t0 = time.time()
    summary = {}
    with ProcessPoolExecutor(a.workers, max_tasks_per_child=1) as ex:
        futs = {ex.submit(run_recipe, (n, rs[n], off, str(Path(a.dsp).resolve()), str(outd))): n for n in names}
        for f in as_completed(futs):
            n = futs[f]
            try:
                name, ok, blocks, faults, anoms = f.result()
            except Exception as e:
                name, ok, blocks, faults, anoms = n, False, 0, [repr(e)[:300]], []
            summary[name] = {"ok": ok, "blocks": blocks, "faults": faults, "anomalies": anoms}
            print(time.strftime("%H:%M:%S"), name, "ok" if ok else "NOT OK", blocks, "blocks", faults, anoms, flush=True)
    summary["wall_s"] = round(time.time() - t0)
    summary["dsp"] = {"dir": str(Path(a.dsp).resolve().relative_to(ROOT)), "section7_sha256": json.loads(
        (Path(a.dsp) / "build.json").read_text())["section7"]["sha256"], "scale": a.scale}
    (outd / ("summary.json" if not a.only else "summary-%s.json" % a.only.replace(",", "-"))).write_text(
        json.dumps(summary, indent=1))
    print("all done", summary["wall_s"], "s", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
