#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""TE01 (2026-10-29): the DSP proofs of TE01 = DC02 (the DSP on the owner's unit: CMP3's 3-band compressor, the
flanger, the stock delay and reverb) with the tape echo (fx/spaceecho.c, src/se_glue.c) in the stock delay slot, its
functions placed one by one in the free tails of the four proven L1 code regions (build.py PROVEN, recipe key fit).

    python3 tools/dspsplice/verify_te01.py [--quick] [--only a,e,w,s] [--e-parts e1,e2,e3,e4]

(a) the compressor and the flanger against DC02 (out/DC02/DC02-dsp), the two images in lockstep on verify_sc01.py's
    proof (a) settings (zero and invalid tails, MIX 0, equal MIX and the relock, one band wet, the guard and re-init
    injections, the cost cases with the flanger's knobs jumping, seeded old kits), the delay slot silent in both (no
    send into it, its VOL 0, AGE word 0: DC02's stock delay and TE01's echo both return 0): every handler output
    sample, the chorus slot's output (the flanger) and the delay slot's output every block, each band's gain
    reduction every block, both reply pages every block but word 0 (the handler's cycle count), and at the end L1
    word for word outside our code regions, the stack and reply word 0, and the flanger's state word for word;
(e) the tape echo against SC01's (out/SC01/SC01-dsp, the echo the unit ran before SC02's law, the code diet and the
    peephole pass). The SpaceEcho layout moved (DT01: hist and ring last), so outputs are compared, not SeSlot bytes.
    (e1) single-head MODEs (X 0, 1, 2: heads 1, 2, 3 alone; the shared law gives each head all of FDBK / 64 as SC01
    did): the delay slot's output bit for bit SC01's on verify_se.py's configurations (the main one, the stock kit,
    the RND side), the FDBK sweep 32 / 64 / 100 / 127 per head, every frame word moving, the fuzz and the held
    runaways, with MODE kept to the single heads;
    (e2) the shared law in TE01's image, sharcvm calls: spaceecho_fb(v) = v / 64 (v -8..300 step 0.25), and
    se_mix_targets on the live state after the glue applied MODE 0..6 x MONO / ALT / RND, against SC01's
    se_mix_targets on the same knobs: every coefficient equal but the head gains into the feedback, t[8..10], which
    are SC01's x 1, 1/2 or 1/3 (0.33333334f) for one, two or three heads playing;
    (e3) the exact equivalence for two heads (X 3, 4, 5): with AGE 0 (no hiss, no crackle into the feedback sums),
    TE01 at FDBK frame word w = SC01 at w / 2 bit for bit (each head's share 0.5 x fb(w) = fb(w / 2), exact powers
    of two, the DC blocker's state scaled by exactly 1/2), static settings, runaways included;
    (e4) three heads (X 6, no exact equivalence: 1/3 is not a power of two) and every MODE moving: TE01's delay slot
    output against the Mac build of the same C (verify_se.py's SE_NATIVE route: clang -O2 -ffp-contract=off, fed the
    parameter floats, the AGE word, the tempo and the slot's input the SHARC code saw every block), tolerance
    -100 dB re the run's peak (verify_se.py's), and the tape against the Mac's at the end; the AGE 127 held runaway is
    chaotic (an ulp of difference where the noise first reaches the output, then amplified by the loop), so its bound
    is on the first 500 blocks, the whole run recorded, with SC01's image against SC01's Mac build as the control;
    every runaway (FDBK 64..127): the wet within the ceiling 0.7, the tape within +-1, finite.
(w) the whole mix with all three effects on (the echo at FDBK 50 / 64 / 100 / 127, three heads, the compressor's
    bands working, the flanger, the reverb) on TE01, DC02 and SC01, the same frames and input: finite, the master
    ceiling, the echo's wet and tape bounded (TE01, SC01); instructions per block (typical and worst: every knob of all
    three effects moving, noise) and the stack depth for the three; on TE01 the placement at run time: every
    instruction executed in the four code regions lies inside a placed function (build.json function_table), none in
    the free bytes left at a region's end;
(s) stores on TE01: the delay call stores only into its SeSlot (the stock delay's DDR region from 0x8005912c), the
    stack, the stub save area and its output buffers; the chorus and compressor calls as verify_sc01.py (s); over
    whole blocks no store into our code (the four regions, the constant data) nor into 0x315f8c-0x318000.
Results: out/TE01/verify/verify.json (or verify-<only>[-<e parts>].json).
"""
import argparse
import ctypes as C
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
sys.path.insert(0, str(ROOT / "tools/sharcvm/tests"))
import verify_sc01 as S1  # noqa: E402
import verify_dt01 as D1  # noqa: E402
import memguard  # noqa: E402
import build as BD  # noqa: E402

VC, V = S1.VC, S1.V
SLOT, OFF, SR = S1.SLOT, S1.OFF, S1.SR
OUT = ROOT / "out/TE01/verify"
TE01_DIR = ROOT / "out/TE01/TE01-dsp"
DC02_DIR = ROOT / "out/DC02/DC02-dsp"
SC01_DIR = ROOT / "out/SC01/SC01-dsp"
SEO_TE = ROOT / "out/TE01/soak/src"          # TE01's SpaceEcho offsets (soak_te01.py: clang on fx/spaceecho.h)
SEO_SC = ROOT / "out/SC01/soak/src"          # SC01's (its own header copy)
FLG, FLG_BYTES = 0x300000, 0x8400
SE_SLOT, SE_BYTES, SE_HDR = S1.SE_SLOT, S1.SE_BYTES, 48
RING_FLOATS = 2 * 512000
CEIL = S1.CEIL
AGE_W = 0xa18
WORST = {"DC02": (0, ""), "TE01": (0, "")}
SEO = {}
E_PARTS = {"e1", "e2", "e3", "e4"}


def fbits(xs):
    return S1.fbits(xs)


def seo(tag):
    if tag not in SEO:
        d = SEO_TE if tag == "TE01" else SEO_SC
        SEO[tag] = json.loads((d / "offsets.json").read_text())
    return SEO[tag]


def ring_addr(tag):
    return SE_SLOT + SE_HDR + seo(tag)["ring"]


def peaks(xs):
    pk, bad = 0.0, 0
    for x in xs:
        if x != x or abs(x) == float("inf"):
            bad += 1
        elif abs(x) > pk:
            pk = abs(x)
    return pk, bad


# ---------------------------------------------------------------- (a) lockstep DC02 / TE01

class PairTE(D1.PairDT):
    """DC02 (a) and TE01 (b) on the same blocks: verify_dt01's Pair (outputs, chorus and delay slot outputs, gain
    reductions, reply pages but word 0), the AGE word 0 in both, at the end L1 outside our code regions and the stack,
    and the flanger's slot word for word (the same layout)"""

    def __init__(self, imgs, words, raw, sched, silent=True):
        super().__init__(imgs, words, raw, sched, silent=silent)
        for r in (self.a, self.b):
            struct.pack_into("<H", r.r.d.frame, AGE_W, 0)

    def finish(self):
        va, vb = self.a.vm, self.b.vm
        ignore = [(0x2dd600, 0x2e1c40), (0x2e2900, 0x2e6000), (0x2e6000, 0x2e7000), (0x308400, 0x318000),
                  (V.STACK_LO, V.STACK_TOP), (VC.TX[0], VC.TX[0] + 4), (VC.TX[1], VC.TX[1] + 4)]
        mem = []
        for lo, hi in ((0x240000, 0x2f0000), (0x300000, 0x320000)):
            x, y = va.read(lo, hi - lo), vb.read(lo, hi - lo)
            if x != y:
                for i in range(0, hi - lo, 4):
                    ad = lo + i
                    if x[i:i + 4] != y[i:i + 4] and not any(p <= ad < q for p, q in ignore):
                        mem.append(ad)
        fa, fb = va.read(FLG, FLG_BYTES), vb.read(FLG, FLG_BYTES)
        flg_diff = [FLG + i for i in range(0, FLG_BYTES, 4) if fa[i:i + 4] != fb[i:i + 4]]
        pa, pb = self.a.r.per_block, self.b.r.per_block
        case = getattr(self, "case", "?")
        for tag, p in (("DC02", pa), ("TE01", pb)):
            if p[1:] and max(p[1:]) > WORST[tag][0]:
                WORST[tag] = (max(p[1:]), case)
        r = {"blocks": self.nb, "samples": self.samples, "samples_identical": self.same,
             "flanger_output_blocks_identical": self.chr_same, "delay_slot_output_blocks_identical": self.del_same,
             "delay_slot_silent_blocks_both": self.del_zero,
             "gain_reduction_blocks_identical": self.gr_same, "reply_pages_identical_but_word0": self.pages_same,
             "reply_byte_offsets_that_differ": sorted("%#x" % o for o in self.page_offsets),
             "l1_words_that_differ_outside_ours": ["%#x" % a for a in mem[:20]], "l1_count": len(mem),
             "flanger_slot_words_that_differ": len(flg_diff),
             "trips_reinits_DC02_TE01": [[v.rd32(SLOT + OFF["trips"]), v.rd32(SLOT + OFF["reinits"])] for v in (va, vb)],
             "insns_mean_DC02_TE01": [round(sum(pa[1:]) / max(1, len(pa) - 1)), round(sum(pb[1:]) / max(1, len(pb) - 1))],
             "insns_max_DC02_TE01": [max(pa[1:] or [0]), max(pb[1:] or [0])], "peak": round(self.peak, 4)}
        r["insns_mean_CMP3_SC01"], r["insns_max_CMP3_SC01"] = r["insns_mean_DC02_TE01"], r["insns_max_DC02_TE01"]
        r["ok"] = (self.same == self.samples and self.gr_same == self.nb and self.pages_same == self.nb
                   and self.chr_same == self.nb and self.del_same == self.nb and self.del_zero == self.nb
                   and not mem and not flg_diff)
        return r


def proof_a(imgs, q):
    keep = S1.Pair
    S1.Pair = PairTE
    try:
        ok, res = S1.proof_a({"CMP3": imgs["DC02"], "SC01": imgs["TE01"]}, q)
    finally:
        S1.Pair = keep
    res["note"] = ("verify_sc01's proof (a) run with DC02 in CMP3's place and TE01 in SC01's; the delay slot silent "
                   "(no send, VOL 0, AGE word 0): its output 0 in both every block is required too")
    return ok, res


# ---------------------------------------------------------------- (e) the tape echo against SC01's

class SeRun(S1.SeRun):
    """verify_sc01's SeRun (the whole handler, the delay slot's output per block) with the image's tag (ring offset)"""

    def __init__(self, img, words, age, tag, fdbk_raw=None):
        super().__init__(img[0], img[2], words, age)
        self.tag = tag
        if fdbk_raw is not None:
            self.fdbk_raw(fdbk_raw)

    def fdbk_raw(self, w):
        struct.pack_into("<H", self.r.d.frame, V.fullblock.FRAME_WORDS["DEL_FDBK"], w & 0xffff)

    def ring(self):
        return self.vm.rdf(ring_addr(self.tag), RING_FLOATS)


def se_cmp(sa, sb, L, R, each=None, nb=None):
    """SC01 (sa) and TE01 (sb) on the same blocks: the delay slot's output every block; TE01's wet and tape"""
    nb = nb or len(L) // 32
    same = samples = 0
    first_diff = None
    maxd = 0.0
    pk = pka = 0.0
    nan = 0
    for b in range(nb):
        if each:
            each(b, sa, sb)
        oa = sa.block(L[32 * b:32 * b + 32], R[32 * b:32 * b + 32])
        ob = sb.block(L[32 * b:32 * b + 32], R[32 * b:32 * b + 32])
        xa, xb = oa[0] + oa[1], ob[0] + ob[1]
        samples += 64
        if fbits(xa) == fbits(xb):
            same += 64
        else:
            n = sum(1 for x, y in zip(xa, xb) if struct.pack("<f", x) == struct.pack("<f", y))
            same += n
            if first_diff is None:
                first_diff = b
            maxd = max(maxd, max((abs(x - y) for x, y in zip(xa, xb) if x == x and y == y), default=0.0))
        p, bad = peaks(xb)
        pk, nan = max(pk, p), nan + bad
        pka = max(pka, peaks(xa)[0])
    rp, rbad = peaks(sb.ring())
    r = {"blocks": nb, "samples": samples, "delay_output_identical": same, "first_block_that_differs": first_diff,
         "max_abs_diff": maxd, "wet_peak_SC01": round(pka, 6), "wet_peak": round(pk, 6), "wet_nan_inf": nan,
         "tape_peak": round(rp, 6), "tape_nan_inf": rbad}
    r["ok"] = same == samples
    return r


def mode_gains(x):
    """se_heads(MODE) as bits, the heads playing"""
    hm = {0: 1, 1: 2, 2: 4, 3: 3, 4: 6, 5: 5}.get(x, 7)
    return [1.0 if hm & (1 << k) else 0.0 for k in range(3)]


def share(x):
    n = int(sum(mode_gains(x)))
    return {1: 1.0, 2: 0.5, 3: S1.f32(0.33333334)}[n]


def proof_e(imgs, q):
    res, oks = {}, []
    te, sc = imgs["TE01"], imgs["SC01"]
    import verify_se as VS
    from fx import read_wav
    pl, pr, _ = read_wav(ROOT / "out/T3.11/dry/pluck.wav")

    def pluck(secs):
        n = int(secs * SR) // 32 * 32
        return ((pl + [0.0] * n)[:n], (pr + [0.0] * n)[:n])
    P6, P3 = pluck(4.0 if not q else 1.0), pluck(2.0 if not q else 1.0)
    base = dict(VS.WORDS)
    rnd = random.Random(5)

    def run_pair(group, tag, words, age, inp, each=None, nb=None, sc_fdbk_raw=None):
        t0 = time.time()
        a = SeRun(sc, words, age, "SC01", fdbk_raw=sc_fdbk_raw)
        b = SeRun(te, words, age, "TE01")
        r = se_cmp(a, b, inp[0], inp[1], each=each, nb=nb)
        r["seconds"] = round(time.time() - t0, 1)
        res.setdefault(group, {})[tag] = r
        oks.append(r["ok"])
        print("(%s) %-78s %s %d/%d, wet %.4f (SC01 %.4f), tape %.4f, %.0fs" % (
            group, tag[:78], "ok" if r["ok"] else "DIFFERS", r["delay_output_identical"], r["samples"], r["wet_peak"],
            r["wet_peak_SC01"], r["tape_peak"], r["seconds"]), flush=True)
        del a, b
        gc.collect()
        memguard.check(4096)
        return r

    def bounded(r):
        ok = r["wet_peak"] <= CEIL and r["tape_peak"] <= 1.0 + 1e-6 and r["wet_nan_inf"] == 0 and r["tape_nan_inf"] == 0
        r["bounded"] = ok
        oks.append(ok)
        return ok

    nbm = int(4.0 * SR / 32) if not q else 300
    Pm = (P6[0] * 3, P6[1] * 3)
    nbf = 1500 if not q else 300
    noise = ([rnd.uniform(-1, 1) for _ in range(nbf * 32)], [rnd.uniform(-1, 1) for _ in range(nbf * 32)])
    nbr = int(3.0 * SR / 32) if not q else 600
    sine = [0.9 * math.sin(2 * math.pi * 220 * i / SR) if i < SR else 0.0 for i in range(nbr * 32)]
    held = dict(base, IN_L=127, IN_R=127, IN_DEL_L=127, IN_DEL_R=127, DEL_VOL=127, DEL_LPF=127, DEL_TIME=10)
    # (e1) single heads: bit for bit SC01
    if "e1" in E_PARTS:
        for x in (0, 1, 2):
            run_pair("e1", "verify_se main configuration (FDBK 50, AGE 64, band, ALT), X %d" % x, dict(base, DEL_X=x),
                     VS.AGE_MAIN, P6)
        c = VS.CONFIGS["existing_kit_clean"]
        run_pair("e1", "verify_se existing_kit_clean (X 0)", dict(base, **c["knobs"]), c["age"], P3)
        c = VS.CONFIGS["rnd_side"]
        for x in (0, 2):
            run_pair("e1", "verify_se rnd_side (RND 92, resonant band, AGE 127, FDBK 70), X %d" % x,
                     dict(base, **dict(c["knobs"], DEL_X=x)), c["age"], P3)
        for fb in (32, 64, 100, 127):
            for x in (0, 1, 2):
                r = run_pair("e1", "FDBK %d, X %d (head %d alone), BW 127, AGE 0" % (fb, x, x + 1),
                             dict(base, DEL_FDBK=fb, DEL_X=x, DEL_LPF=127, DEL_WID=64), 0, P3)
                if fb >= 64:
                    bounded(r)
        mv = {}

        def moving(b, a_, b_):
            if b % 3 == 0:
                if b not in mv:
                    mv[b] = dict(DEL_TIME=rnd.randrange(24), DEL_X=rnd.randrange(3), DEL_WID=rnd.randrange(128),
                                 DEL_FDBK=rnd.randrange(128), DEL_HPF=rnd.randrange(128), DEL_LPF=rnd.randrange(128),
                                 age=rnd.randrange(128))
                for s_ in (a_, b_):
                    s_.r.d.set(**{k: v for k, v in mv[b].items() if k != "age"})
                    s_.set_age(mv[b]["age"])
        run_pair("e1", "every frame word moving (each 3 blocks, random; MODE 0..2, TIME 0..23)", base, 64, Pm,
                 each=moving, nb=nbm)
        fz = {}

        def fuzz(b, a_, b_):
            if b % 20 == 0:
                if b not in fz:
                    fz[b] = dict(DEL_TIME=rnd.randrange(128) if rnd.random() < 0.3 else rnd.randrange(16),
                                 DEL_X=rnd.randrange(3), DEL_WID=rnd.randrange(128),
                                 DEL_FDBK=127 if rnd.random() < 0.5 else rnd.randrange(128), DEL_HPF=rnd.randrange(128),
                                 DEL_LPF=rnd.randrange(128), age=rnd.randrange(128))
                for s_ in (a_, b_):
                    s_.r.d.set(**{k: v for k, v in fz[b].items() if k != "age"})
                    s_.set_age(fz[b]["age"])
        r = run_pair("e1", "fuzz: full-scale noise, every knob jumping each 20 blocks (MODE 0..2), FDBK 127 half the "
                     "time", dict(base, IN_DEL_L=127, IN_DEL_R=127, DEL_VOL=127), 64, noise, each=fuzz)
        bounded(r)
        for fb in (64, 100, 127):
            for x, side, wid in ((0, "ALT 127", 0), (2, "RND 127", 127)):
                r = run_pair("e1", "held runaway FDBK %d, X %d, %s, BW 127, AGE 0 (1 s of 0.9 sine, then silence)" % (
                    fb, x, side), dict(held, DEL_X=x, DEL_FDBK=fb, DEL_WID=wid), 0, (sine, sine))
                bounded(r)

    # (e2) the shared law in TE01's image: spaceecho_fb and se_mix_targets (sharcvm calls)
    if "e2" in E_PARTS:
        law = {}
        vm = V.candidate_vm(te[0], cache=te[2])
        a_fb = int(te[1]["symbols"]["spaceecho_fb"], 16)
        bad, vals = 0, {}
        for i in range(-32, 1201):
            v = i * 0.25
            r0 = vm.call(a_fb, args=(struct.unpack("<I", struct.pack("<f", v))[0],))
            got = struct.unpack("<f", struct.pack("<I", r0 & 0xffffffff))[0]
            want = S1.f32(min(max(v, 0.0), 127.0) / 64.0) if v > 0 else 0.0
            bad += got != want
            if v in (0, 32, 50, 64, 100, 127, 200):
                vals[v] = got
        law["spaceecho_fb_mismatches_vs_v_over_64"] = bad
        law["spaceecho_fb_at"] = vals
        del vm
        gc.collect()
        mt = {"TE01": int(te[1]["symbols"]["scfn_spaceecho_se_mix_targets"], 16),
              "SC01": int(sc[1]["symbols"]["scfn_spaceecho_se_mix_targets"], 16)}
        rows, mt_bad = [], 0
        z = [0.0] * 32
        for x in range(7):
            for pan, wid in (("MONO", 64), ("ALT", 20), ("RND", 110)):
                ts = {}
                for tag, img in (("TE01", te), ("SC01", sc)):
                    run = SeRun(img, dict(base, DEL_X=x, DEL_WID=wid, DEL_FDBK=64), 0, tag)
                    for _ in range(3):
                        run.block(z, z)
                    s_ptr = SE_SLOT + SE_HDR
                    t_ptr = ring_addr(tag) + 4096          # scratch on the tape (a throwaway VM)
                    run.vm.call(mt[tag], args=(s_ptr, t_ptr))
                    ts[tag] = run.vm.rdf(t_ptr, 23)
                    ts[tag + "_mode_pan"] = struct.unpack("<2i", run.vm.read(s_ptr + seo(tag)["mode"], 8))
                    del run
                    gc.collect()
                g, sh = mode_gains(x), share(x)
                want8 = [S1.f32(gk * sh) for gk in g]
                ok_heads = ts["TE01"][8:11] == want8
                ok_vs_sc = (ts["TE01"][:8] == ts["SC01"][:8] and ts["TE01"][11:] == ts["SC01"][11:]
                            and ts["TE01"][8:11] == [S1.f32(v * sh) for v in ts["SC01"][8:11]])
                ok_state = ts["TE01_mode_pan"] == ts["SC01_mode_pan"] == (x, {"MONO": 0, "ALT": 1, "RND": 2}[pan])
                mt_bad += not (ok_heads and ok_vs_sc and ok_state)
                rows.append({"X": x, "pan": pan, "head_gains_TE01": ts["TE01"][8:11], "head_gains_SC01": ts["SC01"][8:11],
                             "want": want8, "others_equal_SC01": ts["TE01"][:8] == ts["SC01"][:8]
                             and ts["TE01"][11:] == ts["SC01"][11:], "mode_pan": ts["TE01_mode_pan"],
                             "ok": ok_heads and ok_vs_sc and ok_state})
        law["se_mix_targets"] = rows
        law["se_mix_targets_bad"] = mt_bad
        law["ok"] = bad == 0 and mt_bad == 0
        res["e2_law"] = law
        oks.append(law["ok"])
        print("(e2) spaceecho_fb mismatches %d, at %s; se_mix_targets %d of %d settings wrong" % (
            bad, vals, mt_bad, len(rows)), flush=True)

    # (e3) two heads: TE01 at FDBK w = SC01 at w / 2, AGE 0, static
    if "e3" in E_PARTS:
        for x, fb, wid, lpf, hpf, tm in ((3, 50, 20, 64, 64, 23), (4, 64, 110, 127, 64, 15), (5, 100, 64, 64, 90, 23),
                                         (3, 80, 127, 30, 40, 7), (5, 33, 0, 127, 64, 23)):
            run_pair("e3", "X %d, TE01 FDBK %d = SC01 FDBK %.1f, WID %d, CNTR %d, BW %d, TIME %d, AGE 0" % (
                x, fb, fb / 2, wid, hpf, lpf, tm), dict(base, DEL_X=x, DEL_FDBK=fb, DEL_WID=wid, DEL_LPF=lpf, DEL_HPF=hpf,
                                                        DEL_TIME=tm), 0, P3, sc_fdbk_raw=fb << 7)
        for fb in (64, 100, 127):
            for x, side, wid in ((3, "ALT 127", 0), (4, "RND 127", 127)):
                r = run_pair("e3", "held runaway X %d, TE01 FDBK %d = SC01 FDBK %.1f, %s, BW 127, AGE 0" % (
                    x, fb, fb / 2, side), dict(held, DEL_X=x, DEL_FDBK=fb, DEL_WID=wid), 0, (sine, sine),
                    sc_fdbk_raw=fb << 7)
                bounded(r)

    # (e4) three heads and every MODE moving: against the Mac build of the same C
    if "e4" in E_PARTS:
        bdir = OUT / "native"
        bdir.mkdir(parents=True, exist_ok=True)
        lib = VS.native_se(bdir)
        lib.se_native_slot_ptr.restype = C.c_void_p
        ring_off = SE_HDR + seo("TE01")["ring"]

        def sc01_control(words, age, inp, nblk):
            """SC01's image against the Mac build of SC01's own C (tools/dspsplice/src/sc01) on the same case: where its
            difference starts and how far it grows (the chaotic case's control)"""
            import subprocess
            so = bdir / "libsenative_sc01.dylib"
            src = ROOT / "tools/dspsplice/src/sc01"
            subprocess.check_call(["clang", "-O2", "-ffp-contract=off", "-fno-strict-aliasing", "-shared", "-fPIC",
                                   "-DSE_NATIVE", f"-I{src}", f"-I{ROOT / 'fx'}", f"-I{ROOT / 'tools/dspsplice/src'}",
                                   "-o", str(so), str(src / "spaceecho.c"), str(ROOT / "tools/dspsplice/src/se_glue.c")])
            lsc = C.CDLL(str(so))
            vm = V.candidate_vm(sc[0], cache=sc[2])
            rx = VS.Run(vm, words, k40=int(round(age * 256)))
            rx.process(inp[0], inp[1])
            wet = rx.r.cap["del_out"]
            mac = VS.native_se_render(lsc, rx.params, rx.k40w, rx.bpm, rx.r.cap["del_in"])
            out = {"first_difference": first_diff(mac, wet), "first_blocks": VS.FL.compare(
                [m[:32 * nblk] for m in mac], [w[:32 * nblk] for w in wet]), "whole_run": VS.FL.compare(mac, wet)}
            del vm, rx
            gc.collect()
            return out

        def first_diff(mac, wet):
            for i in range(len(wet[0])):
                for c in (0, 1):
                    if wet[c][i] != mac[c][i]:
                        return {"block": i // 32, "image": wet[c][i], "mac": mac[c][i]}
            return None

        def native_case(tag, words, age, inp, each=None, nb=None, runaway=False, chaotic=False):
            """chaotic: AGE's noise in a held runaway; the SHARC's and the Mac's float results differ by an ulp where
            the noise first reaches the output, and the runaway loop then amplifies that: the bound is applied to the
            first 500 blocks (before the loop has grown it), the whole run recorded, and SC01's image against SC01's
            Mac build on the same case for comparison"""
            t0 = time.time()
            vm = V.candidate_vm(te[0], cache=te[2])
            rx = VS.Run(vm, words, k40=int(round(age * 256)))
            n = (nb or len(inp[0]) // 32) * 32
            rx.process(inp[0][:n], inp[1][:n], each=each)
            wet = rx.r.cap["del_out"]
            mac = VS.native_se_render(lib, rx.params, rx.k40w, rx.bpm, rx.r.cap["del_in"])
            r = VS.FL.compare(mac, wet)
            if chaotic:
                r["whole_run_not_gated"] = dict(r)
                r["first_difference"] = first_diff(mac, wet)
                r.update(VS.FL.compare([m[:32 * 500] for m in mac], [w[:32 * 500] for w in wet]))
                r["gated_on"] = "the first 500 blocks"
            pk, bad_ = peaks(wet[0] + wet[1])
            tv = vm.rdf(SE_SLOT + ring_off, RING_FLOATS)
            tm_ = struct.unpack("<%df" % RING_FLOATS, C.string_at(lib.se_native_slot_ptr() + ring_off, 4 * RING_FLOATS))
            tp, tbad = peaks(tv)
            r.update(wet_peak=round(pk, 6), wet_nan_inf=bad_, tape_peak=round(tp, 6), tape_nan_inf=tbad,
                     tape_vs_mac_identical=sum(1 for u, v in zip(tv, tm_) if u == v),
                     tape_vs_mac_max_diff=max(abs(u - v) for u, v in zip(tv, tm_)))
            r["ok"] = r["max_diff_db_re_peak"] is None or r["max_diff_db_re_peak"] < -100
            r["seconds"] = round(time.time() - t0, 1)
            res.setdefault("e4", {})[tag] = r
            oks.append(r["ok"])
            if runaway:
                bounded(r)
            print("(e4) %-78s %s %d/%d bit-identical, max diff %.3g (%s dB re peak), wet %.4f, tape %.4f, %.0fs" % (
                tag[:78], "ok" if r["ok"] else "OVER", r["bit_identical"], r["samples"], r["max_diff"],
                r["max_diff_db_re_peak"], r["wet_peak"], r["tape_peak"], r["seconds"]), flush=True)
            del vm, rx, tv, tm_
            if chaotic:
                r["sc01_control"] = sc01_control(words, age, inp, 500)
                print("     whole run %s; first difference %s; SC01 vs its Mac build: %s" % (
                    {k: r["whole_run_not_gated"][k] for k in ("bit_identical", "max_diff", "max_diff_db_re_peak")},
                    r["first_difference"], {k: (v if k == "first_difference" else
                                                {kk: v[kk] for kk in ("bit_identical", "max_diff", "max_diff_db_re_peak")})
                                            for k, v in r["sc01_control"].items()}), flush=True)
            gc.collect()
            memguard.check(4096)

        native_case("verify_se main configuration, X 6 (three heads, FDBK 50, AGE 64, band, ALT)", base, VS.AGE_MAIN, P6)
        c = VS.CONFIGS["rnd_side"]
        native_case("verify_se rnd_side as it is (X 3: heads 1+2, AGE 127)", dict(base, **c["knobs"]), c["age"], P3)
        for fb in (32, 64, 100, 127):
            native_case("X 6, FDBK %d, BW 127, AGE 0" % fb, dict(base, DEL_FDBK=fb, DEL_X=6, DEL_LPF=127, DEL_WID=64), 0, P3,
                        runaway=fb >= 64)
        for fb in (64, 100, 127):
            for side, wid in (("ALT 127", 0), ("RND 127", 127)):
                native_case("held runaway X 6, FDBK %d, %s, BW 127, AGE 0" % (fb, side),
                            dict(held, DEL_X=6, DEL_FDBK=fb, DEL_WID=wid), 0, (sine, sine), runaway=True)
        native_case("held runaway X 6, FDBK 127, ALT 127, BW 0, AGE 127", dict(held, DEL_X=6, DEL_FDBK=127, DEL_WID=0,
                    DEL_LPF=0), 127, (sine, sine), runaway=True, chaotic=True)
        mv2 = {}

        def moving_all(b, run):
            if b % 3 == 0:
                if b not in mv2:
                    mv2[b] = dict(DEL_TIME=rnd.randrange(24), DEL_X=rnd.randrange(7), DEL_WID=rnd.randrange(128),
                                  DEL_FDBK=rnd.randrange(128), DEL_HPF=rnd.randrange(128), DEL_LPF=rnd.randrange(128),
                                  age=rnd.randrange(128))
                run.r.d.set(**{k: v for k, v in mv2[b].items() if k != "age"})
                run.set_k40(mv2[b]["age"] << 8)
        native_case("every frame word moving (each 3 blocks, random; MODE 0..6)", base, 64, Pm, each=moving_all, nb=nbm)
        fz2 = {}

        def fuzz_all(b, run):
            if b % 20 == 0:
                if b not in fz2:
                    fz2[b] = dict(DEL_TIME=rnd.randrange(128) if rnd.random() < 0.3 else rnd.randrange(16),
                                  DEL_X=rnd.randrange(7), DEL_WID=rnd.randrange(128),
                                  DEL_FDBK=127 if rnd.random() < 0.5 else rnd.randrange(128), DEL_HPF=rnd.randrange(128),
                                  DEL_LPF=rnd.randrange(128), age=rnd.randrange(128))
                run.r.d.set(**{k: v for k, v in fz2[b].items() if k != "age"})
                run.set_k40(fz2[b]["age"] << 8)
        native_case("fuzz: full-scale noise, every knob jumping each 20 blocks (MODE 0..6), FDBK 127 half the time",
                    dict(base, IN_DEL_L=127, IN_DEL_R=127, DEL_VOL=127), 64, noise, each=fuzz_all, runaway=True)
    res["e4_tolerance"] = ("max |TE01 - Mac| < -100 dB re the run's peak (verify_se.py's bound for SE01); the AGE 127 held "
                           "runaway (chaotic: the loop amplifies an ulp) gated on its first 500 blocks, see the case")
    res["ok"] = all(oks)
    return res["ok"], res


# ---------------------------------------------------------------- (w) the whole mix, three images

def proof_w(imgs, q):
    res, oks = {}, []
    info = imgs["TE01"][1]
    regions = [(lo // 2, hi // 2) for _, _, lo, hi in BD.RECIPES["TE01"]["fit"]]
    placed = [(int(f["core"], 16) // 2, (int(f["core"], 16) + f["bytes"]) // 2) for f in info["function_table"]]
    nb = 3000 if not q else 400
    prog = VC.looped(nb)
    moving17 = lambda b: [20 + 4 * (b % 2), 3 + b % 2, 10 + 4 * (b % 2), 40 + 4 * (b % 2), 50 + 4 * (b % 2), 60 + 4 * (b % 2),   # noqa: E731
                          60 - 4 * (b % 2), 6 - b % 2, 40 + 4 * (b % 2), 60 + 4 * (b % 2), 30 + 4 * (b % 2), 50 + 4 * (b % 2),
                          90 - 4 * (b % 2), 8 - b % 2, 80 + 4 * (b % 2), 100 + 4 * (b % 2), 90 - 4 * (b % 2)]
    mixes_apart = lambda b: [0 if ((b + 13 * k) // 40) % 2 else (40, 64, 100)[k] for k in range(3)]   # noqa: E731
    rn0 = random.Random(9)
    noise = ([rn0.uniform(-1, 1) for _ in range(nb * 32)], [rn0.uniform(-1, 1) for _ in range(nb * 32)])
    allfx = dict(IN_CHR_L=100, IN_CHR_R=100, IN_DEL_L=100, IN_DEL_R=100, IN_REV_L=60, IN_REV_R=60, CHR_DEL=40, CHR_REV=30,
                 CHR_VOL=90, DEL_REV=40, DEL_VOL=100, REV_VOL=90, REV_DEC=90, **VC.FL.KNOBS)
    jumps = {}
    rn = random.Random(19)

    def everything(b, fr):
        if b % 20 == 0:
            if b not in jumps:
                jumps[b] = {**{k: rn.randrange(128) for k in VC.FL.KNOBS},
                            **{k: rn.randrange(128) for k in ("DEL_TIME", "DEL_WID", "DEL_FDBK", "DEL_HPF", "DEL_LPF",
                                                              "REV_DEC")},
                            "DEL_X": rn.randrange(7), "age": rn.randrange(128)}
            for k, v in jumps[b].items():
                if k == "age":
                    struct.pack_into("<H", fr, AGE_W, v << 8)
                else:
                    VC.put(fr, k, v << 8)
    cases = {
        "typical: the mix, echo MODE 1 FDBK 50 AGE 32 BW 127, flanger, reverb, bands at defaults MIX 39":
            (dict(VC.BASE, **allfx, **VC.TYPICAL, DEL_TIME=23, DEL_X=0, DEL_WID=64, DEL_FDBK=50, DEL_HPF=64, DEL_LPF=127),
             32, prog, S1.sched3(S1.DEFAULTS, (39, 39, 39)), None),
        "busy: drums and the mix, echo MODE 123 FDBK 64 AGE 64 band 64/64 ALT, flanger, reverb, distinct bands MIX 64":
            (dict(VC.BASE, **allfx, **VC.comp(32, 24, 32, 64, 3, 64), DEL_TIME=23, DEL_X=6, DEL_WID=32, DEL_FDBK=64,
                  DEL_HPF=64, DEL_LPF=64), 64, prog, S1.sched3(S1.DISTINCT, (64, 64, 64)), None),
        "runaway: echo MODE 123 FDBK 100 BW 127 AGE 0 RND 127, flanger, reverb, the starting point MIX 127":
            (dict(VC.BASE, **allfx, **VC.comp(32, 24, 32, 64, 3, 127), DEL_TIME=10, DEL_X=6, DEL_WID=127, DEL_FDBK=100,
                  DEL_HPF=64, DEL_LPF=127), 0, prog, S1.sched3(S1.START, (127, 127, 127)), None),
        "runaway: echo MODE 123 FDBK 127 ALT 127 BW 0 AGE 127, flanger, reverb, heavy bands MIX 127":
            (dict(VC.BASE, **allfx, **VC.comp(32, 24, 32, 64, 3, 127), DEL_TIME=10, DEL_X=6, DEL_WID=0, DEL_FDBK=127,
                  DEL_HPF=64, DEL_LPF=0), 127, prog, S1.sched3(([(10, 8, 0, 20, 64)] * 3, (68, 44)), (127, 127, 127)), None),
        "worst: noise, every band knob and each MIX moving, every echo, flanger and reverb knob jumping, key SCS 18":
            (dict(VC.BASE, **allfx, **VC.W3), 64, noise, S1.list3([(moving17(b), mixes_apart(b)) for b in range(nb)]),
             everything),
    }
    for name, (words, age, inp, sched, each) in cases.items():
        res[name] = {}
        for tag in ("TE01", "DC02", "SC01"):
            img = imgs[tag]
            t0 = time.time()
            vm = V.candidate_vm(img[0], cache=img[2])
            if tag == "TE01":
                vm.lib.vm_set_profile(vm.h, 1)
            run = S1.FxTailRun(vm, words, VC.ROUTE_INFX, sched)
            struct.pack_into("<H", run.r.d.frame, AGE_W, age << 8)
            jumps.clear()
            rn.seed(19)
            nan = 0
            pk = pkdel = 0.0
            for b in range(nb):
                if each:
                    each(b, run.r.d.frame)
                run.tblock(inp[0][32 * b:32 * b + 32], inp[1][32 * b:32 * b + 32], b, paint=(b % 50 == 7))
                p, bad = peaks(run.out[0] + run.out[1])
                pk, nan = max(pk, p), nan + bad
                run.out[0].clear()
                run.out[1].clear()
                p, bad = peaks(struct.unpack("<64f", run.dl.pop()))
                pkdel, nan = max(pkdel, p), nan + bad
                run.chr.clear()
                run.gr.clear()
                run.tx_pages.clear()
            pb = run.r.per_block[1:]
            e = {"blocks": nb, "output_peak": round(pk, 4), "delay_slot_wet_peak": round(pkdel, 4), "nan_inf": nan,
                 "insns_mean": round(sum(pb) / len(pb)), "insns_max": max(pb),
                 "est_load_mean_pct": round(100 * VC.LOAD(sum(pb) / len(pb)), 1),
                 "est_load_max_pct": round(100 * VC.LOAD(max(pb)), 1), "stack_bytes": run.stack,
                 "trips": vm.rd32(SLOT + OFF["trips"])}
            ok = nan == 0 and pk <= 0.6931 and run.stack < 3000
            if tag in ("TE01", "SC01"):
                rp, rbad = peaks(vm.rdf(ring_addr(tag), RING_FLOATS))
                e.update(tape_peak=round(rp, 4), tape_nan_inf=rbad)
                ok = ok and pkdel <= CEIL and rp <= 1.0 + 1e-6 and rbad == 0
            if tag == "TE01":
                ran, outside = 0, []
                for pc in vm.decoded:
                    if not any(lo <= pc < hi for lo, hi in regions):
                        continue
                    h = vm.pages[pc >> 12][pc & 0xfff].hits
                    if not h:
                        continue
                    ran += h
                    if not any(lo <= pc < hi for lo, hi in placed):
                        outside.append("%#x" % pc)
                e["region_instructions_executed"] = ran
                e["executed_in_regions_outside_placed_functions"] = outside[:10]
                ok = ok and not outside
            e["ok"] = ok
            e["seconds"] = round(time.time() - t0)
            res[name][tag] = e
            oks.append(ok)
            print("(w) %-60s %s %s" % (name[:60], tag, e), flush=True)
            del vm, run
            gc.collect()
            memguard.check(4096)
    res["summary_insns_mean_max"] = {tag: {n[:40]: [res[n][tag]["insns_mean"], res[n][tag]["insns_max"]] for n in cases}
                                     for tag in ("TE01", "DC02", "SC01")}
    res["stack_bytes"] = {tag: max(res[n][tag]["stack_bytes"] for n in cases) for tag in ("TE01", "DC02", "SC01")}
    res["calibration"] = ("load = (1.30 x instructions + 11k) / 666,667 (notes/T6.4 4.4) [inferred]; for CMP2 it said ~74 % "
                          "where the unit measured ~50-60 % (CP2D)")
    res["ok"] = all(oks)
    return res["ok"], res


# ---------------------------------------------------------------- (s) stores

def proof_s(imgs, q):
    img = imgs["TE01"]
    info = img[1]
    sbytes = info["layout_check"]["slot_sizes_from_image"]["cmp_slot_bytes"]
    tbytes = info["layout_check"]["slot_sizes_from_image"]["cmp_tail_bytes"]
    save = (0x2e6800, 0x2e6800 + BD.stub.SAVE_BYTES, "stub save area")
    stack = (V.STACK_LO, V.STACK_TOP, "stack")
    allowed = {
        "chr": [stack, save, (FLG, FLG + FLG_BYTES, "FlgSlot"), (S1.CHR_OUT[0], S1.CHR_OUT[0] + 0x80, "chorus out L"),
                (S1.CHR_OUT[1], S1.CHR_OUT[1] + 0x80, "chorus out R")],
        "del": [stack, save, (SE_SLOT, SE_SLOT + SE_BYTES, "SeSlot"), (S1.DEL_OUT[0], S1.DEL_OUT[0] + 0x80, "delay out L"),
                (S1.DEL_OUT[1], S1.DEL_OUT[1] + 0x80, "delay out R")],
        "comp": [stack, save, (SLOT, SLOT + sbytes, "CmpSlot"), (S1.TST, S1.TST + tbytes, "CmpTail"),
                 (0x25ba68, 0x25ba68 + 0x80, "out L"), (0x25bae8, 0x25bae8 + 0x80, "out R"), (VC.GR_Y2, VC.GR_Y2 + 4, "0x257c1c"),
                 (VC.TX[0] + 0x10, VC.TX[0] + 0x18, "reply page 0"), (VC.TX[1] + 0x10, VC.TX[1] + 0x18, "reply page 1")]}
    ours = [(0x2dd600, 0x2e1c40, "c1: L1 block 1 code (stubs, flanger, echo)"),
            (0x2e2900, 0x2e6000, "c4: code4 (glue, cmp_tail, echo) and its gap"), (0x2e6000, 0x2e6044, "constant data"),
            (0x308400, 0x310000, "c3: L1 block 2 compressor region (and echo)"),
            (0x310000, 0x315f8c, "c2: L1 block 2 tape delay region (stubs2, flg_sync, echo)"),
            (0x315f8c, 0x318000, "0x315f8c-0x318000")]
    nbk = 125 if not q else 40
    L, R = VC.load_input("mix", (nbk + 5) * 32 / SR)
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
            struct.pack_into("<H", fr, AGE_W, ((b * 7) % 128) << 8)
    words = dict(dict(VC.BASE, **VC.FXW, **VC.comp(32, 24, 32, 64, 3, 64)), DEL_X=6, DEL_FDBK=100, CHR_DEL=40)
    census = {}
    for which in ("chr", "del", "comp"):
        vm = V.candidate_vm(img[0], cache=img[2])
        run = S1.FxTailRun(vm, words, VC.ROUTE_INFX, S1.sched3(S1.DISTINCT, (20, 64, 127)))
        log = []
        for b in range(nbk):
            jump(b, run.r.d.frame)
            sel = b in (0, 1, 2, 3, 40, 41, 60, 61, 80, 81, 120, 121) or (q and b in (10, 11, 30, 31))
            wl = [] if sel else None
            fw = (which, []) if (sel and which != "comp") else None
            run.tblock(L[32 * b:32 * b + 32], R[32 * b:32 * b + 32], b, watch=wl if which == "comp" else None,
                       **({"fxwatch": fw} if fw else {}))
            if wl is not None and which == "comp":
                log += wl
            if fw:
                log += fw[1]
            run.out[0].clear(); run.out[1].clear(); run.chr.clear(); run.dl.clear(); run.gr.clear()   # noqa: E702
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
    whole = {}
    from vm import watch as W, watch_log
    nbw = 60 if not q else 20
    for lo, hi, nm in ours:
        vm = V.candidate_vm(img[0], cache=img[2])
        run = S1.FxTailRun(vm, words, VC.ROUTE_INFX, S1.sched3(S1.DISTINCT, (20, 64, 127)))
        W(vm, lo, hi)
        n = 0
        for b in range(nbw):
            jump(b, run.r.d.frame)
            run.tblock(L[32 * b:32 * b + 32], R[32 * b:32 * b + 32], b)
            n += len(watch_log(vm))
            W(vm, lo, hi)
            run.out[0].clear(); run.out[1].clear(); run.chr.clear(); run.dl.clear(); run.gr.clear()   # noqa: E702
        whole[nm] = n
        del vm, run
        gc.collect()
    print("(s) stores into our code over %d whole blocks:" % nbw, whole, flush=True)
    ok = (all(v["outside_count"] == 0 and v["into_our_code_or_the_forbidden_range"] == 0 and v["stores"] > 0
              for v in census.values()) and all(v == 0 for v in whole.values()))
    return ok, {"calls": census, "whole_blocks": nbw, "whole_blocks_stores_into": whole, "ok": ok}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--only", help="comma-separated proofs (a,e,w,s)")
    ap.add_argument("--e-parts", help="comma-separated parts of (e) (e1,e2,e3,e4; default all)")
    a = ap.parse_args()
    only = set(a.only.split(",")) if a.only else set("aews")
    if a.e_parts:
        E_PARTS.clear()
        E_PARTS.update(a.e_parts.split(","))
    t_all = time.time()
    OUT.mkdir(parents=True, exist_ok=True)
    imgs = {"TE01": S1.load_image(TE01_DIR, "TE01"), "DC02": S1.load_image(DC02_DIR, "DC02"),
            "SC01": S1.load_image(SC01_DIR, "SC01")}
    te = imgs["TE01"][1]
    stale = [s for s, h in te["sources"].items() if BD.sha((ROOT / s).read_bytes()) != h]
    if stale:
        raise SystemExit(f"TE01 was built from other sources ({stale})")
    if BD.sha((SEO_TE / "spaceecho.h").read_bytes()) != te["sources"]["fx/spaceecho.h"]:
        raise SystemExit("out/TE01/soak/src/spaceecho.h (the SpaceEcho offsets) is not the header TE01 was built from")
    if BD.sha((SEO_SC / "spaceecho.h").read_bytes()) != imgs["SC01"][1]["sources"]["fx/spaceecho.h"]:
        raise SystemExit("out/SC01/soak/src/spaceecho.h is not the header SC01 was built from")
    dc_src = imgs["DC02"][1]["sources"]
    differ = [s for s, h in dc_src.items() if te["sources"].get(s) != h]
    if differ:
        raise SystemExit(f"TE01 and DC02 differ in shared sources {differ}")
    OFF.update(VC.offsets(OUT / "offsets", "CMP3"))      # CmpSlot: CMP3's = DT01's = DC02's = TE01's
    if te["layout_check"]["slot_sizes_from_image"] != imgs["DC02"][1]["layout_check"]["slot_sizes_from_image"]:
        raise SystemExit("CmpSlot's or CmpTail's size differs between DC02 and TE01")
    res = {"name": "TE01", "section7_sha256": te["section7"]["sha256"],
           "dc02_section7_sha256": imgs["DC02"][1]["section7"]["sha256"],
           "sc01_section7_sha256": imgs["SC01"][1]["section7"]["sha256"], "proofs_run": "".join(sorted(only)),
           "spaceecho_offsets_TE01": {k: seo("TE01")[k] for k in ("w", "hist", "ring", "mode", "size")},
           "spaceecho_offsets_SC01": {k: seo("SC01")[k] for k in ("w", "hist", "ring", "mode", "size")}}
    oks = {}
    tag = "".join(sorted(only)) + ("-" + "".join(sorted(E_PARTS)) if a.e_parts else "") + ("-quick" if a.quick else "")
    res["e_parts"] = sorted(E_PARTS)
    for k, fn in (("s", proof_s), ("e", proof_e), ("w", proof_w), ("a", proof_a)):
        if k in only:
            t0 = time.time()
            oks[k], res[k] = fn(imgs, a.quick)
            res[k + "_seconds"] = round(time.time() - t0)
            print("== (%s) %s, %.0fs" % (k, "PASS" if oks[k] else "FAIL", time.time() - t0), flush=True)
            res["worst_block_a"] = {t: {"instructions": w, "case": c} for t, (w, c) in WORST.items()}
            (OUT / ("verify-%s.json" % tag)).write_text(json.dumps(dict(res, ok=all(oks.values()),
                                                                       seconds=round(time.time() - t_all)), indent=1))
    res["ok"] = all(oks.values())
    res["seconds"] = round(time.time() - t_all)
    res["peak_rss_mb"] = round(memguard.peak_rss_mb())
    res["worst_block_a"] = {t: {"instructions": w, "case": c} for t, (w, c) in WORST.items()}
    name = "verify.json" if not a.only and not a.quick and not a.e_parts else "verify-%s.json" % tag
    (OUT / name).write_text(json.dumps(res, indent=1))
    print("VERDICT", "PASS" if res["ok"] else "FAIL", res["seconds"], "s", flush=True)
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
