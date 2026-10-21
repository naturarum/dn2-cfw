#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""T6.3 CMP3 proof, the per-band MIX part (notes/T6.3-cmp3.md). verify_cmp.py CMP3 runs CMP1's proofs (a)-(h) on CMP3
with the tails zero (the fallback; (h) against CMP2); this runs CMP3 with tails in tools/sharcvm, the DSP image as the
unit loads it (out/T6.3/CMP3-dsp/section7.bin), next to CMP2's (out/T6.3/CMP2-dsp/section7.bin) and FTD1's. The
reader (cmp_tail_fetch) is CMP2's, unchanged; cmp_tail_apply takes three more words (19..21, k 87..89).

    python3 tools/dspsplice/verify_cmp3.py [--quick] [--only z,1,2,3,5,b,d,g,s]

  (z) invalid tails (verify_cmp2's set) over 1,000 blocks in 4 settings: the handler output and the reply pages bit for
      bit CMP2's: the fallback is unchanged (requirement 4; with zero tails: verify_cmp.py CMP3 (h))
  (1) all three band MIX at 0 in tail mode (distinct bands; every band knob moving; a key): the whole handler output
      bit for bit FTD1's (the stock MIX 0 path), and the instructions per block next to CMP2's at MIX 0 (requirement 1)
  (2) all three band MIX equal to m in tail mode against CMP2 in tail mode at MIX m (k 67) with the same band knobs:
      bit for bit (m 1, 39, 64, 127, and m moving 0 <-> 64 for all three); and three MIX coming to the same m from
      different values: when the pairs lock again, and the output against a run that had m all along (requirement 2)
  (3) one band at MIX 127, the others at 0: (3a) the other bands' settings cannot reach the output (their THR, RAT,
      ATK, REL, MUP changed: bit for bit the same output); (3b) against all three at 127 with the other bands at 1.00
      and 0 dB makeup (unity): the same output up to float rounding, and that band's gain reduction the same
      (only that band is compressed, the others pass dry and in phase) (requirement 3)
  (5) the guard and the quiet re-init with the MIX pairs apart: a NaN input, a corrupted magic, a band's wet gain
      corrupted to 2.0 and to NaN: finite output after, the master overdrive state finite, all three pairs restarted
      from 0 and back on their own targets, the knobs the tail's (requirement 5); the extreme tail (0xffff): MIX 127
  (b) SHARC vs Mac (-DCMP_NATIVE, the image's tables, the same tails): distinct MIX per band, MIX moving per band,
      one band at 127, the three coming to 0 from different values: <= -100 dB re peak, meters, reply words
  (d) cost per block with tails: CMP2's cases with equal mixes, unequal mixes, and the worst (every band knob, both
      crossovers and each band's MIX moving every block, the mixes through 0 apart, noise, FX jumping, self and key)
  (g) every store of the compressor call in tail mode with the pairs apart and moving inside the planned ranges
  (s) an old kit seeded (comp-link-dn2 0.2: each band the kit's k 60..64, RAT index + 1, crossovers 30 Hz / 16 kHz,
      each band's MIX the kit's MIX) against CMP2's fallback (CMP1's sound: the same k 60..64 on all three bands at
      196 Hz / 2.61 kHz) and against FTD1 (the stock compressor) on the A/B material: how far an old project moves,
      as levels (RMS, octave bands 31.5 Hz..16 kHz; the crossovers are allpasses, so the waveforms differ anyway)
Writes out/T6.3/CMP3-dsp/verify3.json and prints a VERDICT.
"""
import argparse
import json
import math
import random
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.dont_write_bytecode = True
sys.path.insert(0, str(HERE))
import verify_cmp2 as V2  # noqa: E402  (configures verify_cmp for CMP2; reconfigured below)

VC = V2.VC
VC.configure("CMP3")
V = VC.V
SLOT, TST, NW = V2.SLOT, V2.TST, V2.NW
OFF = V2.OFF
SR = VC.SR
tail_words, const_sched, glide, TailRun, NativeTail = V2.tail_words, V2.const_sched, V2.glide, V2.TailRun, V2.NativeTail
DEFAULTS, DISTINCT, LOW_ONLY, knobs17 = V2.DEFAULTS, V2.DISTINCT, V2.LOW_ONLY, V2.knobs17
# mbcomp's knob[] (CMP3: MB_BAND_NP 6, THR ATK REL MUP RAT BMIX; XLO 18, XHI 19, SCS 20, MIX 21) behind each of the 20
# tail values k 70..89
KIDX3 = [0, 4, 1, 2, 3, 18, 6, 10, 7, 8, 9, 19, 12, 16, 13, 14, 15, 5, 11, 17]
STEP3 = {2, 3, 8, 9, 14, 15, 17, 18, 19}          # ATK, REL, the mixes: rounded to the knob step
UNITY = (32, 0, 24, 32, 0)                        # a band at 1.00 without makeup: gain 1


def words3(vals17, mixes):
    """17 band values (fractions allowed) and 3 mixes -> the 27 tail values k 70..96"""
    return [int(round(v * 256)) & 0xffff for v in vals17] + [int(round(m * 256)) & 0xffff for m in mixes] + [0] * 7


def sched3(setting, mixes, seq0=1):
    w = words3(knobs17(setting), mixes)
    return lambda b: tail_words(seq0 + b, w)


def list3(per_block, seq0=1):
    """per_block: [(vals17, mixes3)] per block"""
    return lambda b: tail_words(seq0 + b, words3(*per_block[min(b, len(per_block) - 1)]))


def knobs_now3(vm):
    k = vm.rdf(SLOT + OFF["mb.knob"], 22)
    return [k[i] for i in KIDX3]


def expect3(vals27):
    out = []
    for j in range(20):
        w = vals27[j] & 0xffff
        v = float((w + 0x80) >> 8) if j in STEP3 else VC.f32(w * 0.00390625)
        hi = 8.0 if j in (1, 7, 13) else 127.0
        out.append(min(max(v, 0.0), hi))
    return out


def pairs(vm):
    """each band's (mw, md, t_mw, t_md)"""
    return [tuple(vm.rdf(SLOT + OFF[f"mb.band[{b}].mw"], 4)) for b in range(3)]


def locked(vm):
    p = pairs(vm)
    return p[0] == p[1] == p[2]


def db(x):
    return round(20 * math.log10(x), 1) if x > 0 else None


def diff_db(a, b):
    """max |a - b| re the peak of b, dB (None = identical)"""
    if not b[0]:
        return None, None
    pk = max(abs(x) for x in b[0] + b[1]) or 1.0
    d = max(abs(x - y) for x, y in zip(a[0] + a[1], b[0] + b[1]))
    return db(d / pk), d


OCT = [31.5, 63, 125, 250, 500, 1000, 2000, 4000, 8000, 16000]


def octaves(out):
    """the output's level in octave bands (RBJ band-pass, Q sqrt(2), 0 dB peak), dB re full scale, L and R together:
    the 3-band crossover is an allpass, so sample differences say nothing about how far two outputs sound apart"""
    res = []
    for fc in OCT:
        w0 = 2 * math.pi * fc / SR
        al = math.sin(w0) / (2 * 1.41421356)
        cw = math.cos(w0)
        a0 = 1 + al
        b0, b2, a1, a2 = al / a0, -al / a0, -2 * cw / a0, (1 - al) / a0
        e = 0.0
        n = 0
        for ch in out:
            x1 = x2 = y1 = y2 = 0.0
            for i, x in enumerate(ch):
                y = b0 * x + b2 * x2 - a1 * y1 - a2 * y2
                x2, x1, y2, y1 = x1, x, y1, y
                if i >= 4800:
                    e += y * y
                    n += 1
        res.append(10 * math.log10(e / max(n, 1) + 1e-30))
    return res


def same_samples(a, b):
    return sum(1 for x, y in zip(a[0] + a[1], b[0] + b[1]) if x == y), len(a[0]) + len(a[1])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--only", help="comma-separated proofs (z,1,2,3,5,b,d,g,s)")
    a = ap.parse_args()
    only = set(a.only.split(",")) if a.only else set("z1235bdgs")
    q = a.quick
    t_all = time.time()
    stream, info, cache = VC.image("CMP3")
    s2, i2, c2 = VC.image("CMP2")
    finfo = json.loads((VC.FTD1 / "build.json").read_text())
    fstream = (VC.FTD1 / "section7.bin").read_bytes()
    assert VC.B.sha(fstream) == finfo["section7"]["sha256"]
    fcache = "t38-FTD1-" + finfo["section7"]["sha256"][:12]
    stale = VC.stale_sources("CMP3", info)
    if stale:
        raise SystemExit(f"CMP3 was built from other sources ({stale}): run build.py CMP3")
    bdir = VC.OUT / "build"
    OFF.update(VC.offsets(bdir))
    tbytes = info["layout_check"]["slot_sizes_from_image"]["cmp_tail_bytes"]
    sbytes = info["layout_check"]["slot_sizes_from_image"]["cmp_slot_bytes"]
    res = {"name": "CMP3", "section7_sha256": info["section7"]["sha256"], "cmp2_section7_sha256": i2["section7"]["sha256"],
           "ftd1_section7_sha256": finfo["section7"]["sha256"], "cmp_slot_bytes": sbytes, "proofs_run": "".join(sorted(only)),
           "layout_ok": sbytes == OFF["mb.buf_g"] + 3 * 32 * 4 + 4 and OFF["mb"] == 72}
    vm3 = lambda: V.candidate_vm(stream, cache=cache)          # noqa: E731
    vm2 = lambda: V.candidate_vm(s2, cache=c2)                 # noqa: E731
    vmf = lambda: V.candidate_vm(fstream, cache=fcache)        # noqa: E731
    oks = {"layout": res["layout_ok"]}
    print("layout", res["layout_ok"], sbytes, flush=True)

    def moving17(b):
        """every band knob and both crossovers moving every block (verify_cmp2's (b)/(d) worst)"""
        o = b % 2
        return [20 + 4 * o, 3 + o, 10 + 4 * o, 40 + 4 * o, 50 + 4 * o, 60 + 4 * o,
                60 - 4 * o, 6 - o, 40 + 4 * o, 60 + 4 * o, 30 + 4 * o, 50 + 4 * o,
                90 - 4 * o, 8 - o, 80 + 4 * o, 100 + 4 * o, 90 - 4 * o]

    def mixes_apart(b):
        """each band's MIX through 0 at its own time (40-block periods, staggered): the pairs apart, the dry crossfade
        moving, and every output loop of mbcomp taken"""
        return [0 if ((b + 13 * k) // 40) % 2 else (40, 64, 100)[k] for k in range(3)]

    # ---- (z) invalid tails: CMP2's output bit for bit
    if "z" in only:
        t0 = time.time()
        nb = 1000 if not q else 200
        rnd = random.Random(11)

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
        zr, zok = {}, True
        for name, (words, raw, each) in zcfg.items():
            rnd.seed(hash(name) & 0xffff)
            tails = [bad_tail(b) for b in range(nb)]
            outs = []
            for vmk in (vm3, vm2):
                run = TailRun(vmk(), words, raw, lambda b: tails[b])
                run.tprocess(prog[0][:nb * 32], prog[1][:nb * 32], each=each, tx_full=True)
                outs.append(run)
            r3, r2 = outs
            txd = set()
            for pa, pb in zip(r3.tx_pages, r2.tx_pages):
                if pa != pb:
                    for i in range(0, len(pa), 2):
                        if pa[i:i + 2] != pb[i:i + 2]:
                            txd.add(i % 0x1000)
            e = {"blocks": nb, "output_bit_identical": r3.out == r2.out,
                 "reply_byte_offsets_that_differ": sorted("%#x" % x for x in txd),
                 "have_CMP3_CMP2": [r3.vm.rd32(TST), r2.vm.rd32(TST)],
                 "insns_mean_CMP3_CMP2": [round(sum(r.r.per_block[1:]) / (nb - 1)) for r in (r3, r2)]}
            e["ok"] = e["output_bit_identical"] and txd <= {0, 2} and e["have_CMP3_CMP2"] == [0, 0]
            zr[name] = e
            zok = zok and e["ok"]
            print("  (z)", name, e, flush=True)
        zr["ok"] = zok
        res["z_invalid_tails_vs_CMP2"] = zr
        oks["z"] = zok
        print("(z)", zok, "%.0fs" % (time.time() - t0), flush=True)

    # ---- (1) all three MIX at 0: FTD1's output bit for bit, CMP2's cost
    if "1" in only:
        t0 = time.time()
        nb = 2000 if not q else 300
        prog = VC.looped(nb)
        L, R = prog[0][:nb * 32], prog[1][:nb * 32]
        r1, ok1 = {}, True
        cases = (
            ("three distinct bands, the three MIX 0, self, FX on", sched3(DISTINCT, (0, 0, 0)),
             dict(VC.BASE, **VC.FXW, **VC.comp(32, 24, 32, 64, 3, 0))),
            ("every band knob and both crossovers moving every block, the three MIX 0, key SCS 18, FX on",
             list3([(moving17(b), (0, 0, 0)) for b in range(nb)]),
             dict(VC.BASE, **VC.FXW, **VC.comp(32, 24, 32, 64, 3, 0, scs=18))),
            ("the stock MIX (k 67) at 100, ignored in tail mode: the three band MIX 0, distinct bands",
             sched3(DISTINCT, (0, 0, 0)), dict(VC.BASE, **VC.FXW, **VC.comp(32, 24, 32, 64, 3, 100))))
        for name, sched, words in cases:
            ra = TailRun(vm3(), words, VC.ROUTE_INFX, sched)
            ra.tprocess(L, R)
            wf = dict(words, CMP_MIX=0)
            rf = TailRun(vmf(), wf, VC.ROUTE_INFX, sched)
            rf.tprocess(L, R)
            r2_ = TailRun(vm2(), wf, VC.ROUTE_INFX, sched)
            r2_.tprocess(L, R)
            same, tot = same_samples(ra.out, rf.out)
            m3 = sum(ra.r.per_block[1:]) / (nb - 1)
            m2 = sum(r2_.r.per_block[1:]) / (nb - 1)
            e = {"samples": tot, "identical_to_FTD1": same, "identical_CMP2_to_FTD1": same_samples(r2_.out, rf.out)[0],
                 "insns_mean_CMP3_CMP2": [round(m3), round(m2)], "insns_max_CMP3_CMP2": [max(ra.r.per_block[1:]), max(r2_.r.per_block[1:])],
                 "est_load_mean_CMP3_CMP2_pct": [round(100 * VC.LOAD(m3), 2), round(100 * VC.LOAD(m2), 2)],
                 "pairs_locked_at_end": locked(ra.vm), "tail_have": ra.vm.rd32(TST)}
            e["ok"] = same == tot and e["tail_have"] == 1 and e["pairs_locked_at_end"]
            r1[name] = e
            ok1 = ok1 and e["ok"]
            print("  (1)", name, e, flush=True)
        r1["ok"] = ok1
        res["1_all_three_mix0_vs_FTD1"] = r1
        oks["1"] = ok1
        print("(1)", ok1, "%.0fs" % (time.time() - t0), flush=True)

    # ---- (2) all three equal to m: CMP2 at MIX m, bit for bit
    if "2" in only:
        t0 = time.time()
        nb = 1500 if not q else 300
        prog = VC.looped(nb)
        L, R = prog[0][:nb * 32], prog[1][:nb * 32]
        r2r, ok2 = {}, True

        def mix_moves(b, fr):
            VC.put(fr, "CMP_MIX", (0 if (b // 40) % 2 else 64) << 8)
        cases = [
            ("m 39, band defaults, self, FX on", DEFAULTS, 39, dict(VC.BASE, **VC.FXW), VC.ROUTE_INFX, None),
            ("m 64, three distinct bands, self, FX on", DISTINCT, 64, dict(VC.BASE, **VC.FXW), VC.ROUTE_INFX, None),
            ("m 127, three distinct bands, key SCS 18, FX on", DISTINCT, 127, dict(VC.BASE, **VC.FXW), VC.ROUTE_INFX, None),
            ("m 1, LOW only", LOW_ONLY, 1, dict(VC.BASE, **VC.NOFX), VC.ROUTE_IN, None),
            ("m 127, heavy (20:1 fast, MUP 127 per band: the wet clip at +-1.0), drums", (
                [(0, 8, 0, 0, 127)] * 3, (68, 44)), 127, dict(VC.BASE, **VC.NOFX), VC.ROUTE_IN, None),
            ("m moving 0 <-> 64 every 40 blocks for all three (CMP2: k 67), distinct bands", DISTINCT, None,
             dict(VC.BASE, **VC.FXW), VC.ROUTE_INFX, mix_moves),
        ]
        for name, setting, m, base, raw, each in cases:
            key = "key" in name
            words = dict(base, **VC.comp(32, 24, 32, 64, 3, m if m is not None else 64, scs=18 if key else 0))
            if m is None:
                # CMP2 reads k 67 from the previous block's frame image (the stock unpack), the tail is live: the
                # tail carries block b - 1's MIX so that both change at the same block
                sch = list3([(knobs17(setting), [0 if (max(b - 1, 0) // 40) % 2 else 64] * 3) for b in range(nb)])
            else:
                sch = sched3(setting, (m, m, m))
            ra = TailRun(vm3(), words, raw, sch)
            ra.tprocess(L, R, each=each)
            rb = TailRun(vm2(), words, raw, sch)
            rb.tprocess(L, R, each=each)
            same, tot = same_samples(ra.out, rb.out)
            e = {"samples": tot, "identical_to_CMP2": same, "pairs_locked_at_end": locked(ra.vm),
                 "peak": round(max(abs(x) for x in ra.out[0] + ra.out[1]), 4)}
            e["ok"] = same == tot
            r2r[name] = e
            ok2 = ok2 and e["ok"]
            print("  (2)", name, e, flush=True)
        # coming to the same m from different values: (L 0, M 64, H 127) for 300 blocks, then all three 64
        nb2 = 1500 if not q else 600
        prog2 = VC.looped(nb2)
        L2, R2 = prog2[0][:nb2 * 32], prog2[1][:nb2 * 32]
        words = dict(VC.BASE, **VC.FXW, **VC.comp(32, 24, 32, 64, 3, 64))
        sch = list3([(knobs17(DISTINCT), (0, 64, 127) if b < 300 else (64, 64, 64)) for b in range(nb2)])
        ra = TailRun(vm3(), words, VC.ROUTE_INFX, sch)
        lock_at = None
        for b in range(nb2):
            ra.tblock(L2[32 * b:32 * b + 32], R2[32 * b:32 * b + 32], b)
            if b >= 300 and lock_at is None and locked(ra.vm):
                lock_at = b
        rb = TailRun(vm3(), words, VC.ROUTE_INFX, sched3(DISTINCT, (64, 64, 64)))
        rb.tprocess(L2, R2)
        rc = TailRun(vm2(), words, VC.ROUTE_INFX, sched3(DISTINCT, (64, 64, 64)))
        rc.tprocess(L2, R2)
        tail_from = 32 * (lock_at + 10) if lock_at else len(L2)
        after = ([x for x in ra.out[0][tail_from:]], [x for x in ra.out[1][tail_from:]])
        ref = ([x for x in rb.out[0][tail_from:]], [x for x in rb.out[1][tail_from:]])
        win = (ra.out[0][32 * 300:32 * 340], ra.out[1][32 * 300:32 * 340])
        winr = (rb.out[0][32 * 300:32 * 340], rb.out[1][32 * 300:32 * 340])
        e = {"locked_again_at_block": lock_at, "ms_after_the_change": round((lock_at - 300) * 32 / 48, 1) if lock_at else None,
             "after_lock_max_diff_db_re_peak_vs_m_all_along": diff_db(after, ref)[0] if after[0] else None,
             "first_40_blocks_after_the_change_max_diff_db_re_peak": diff_db(win, winr)[0],
             "m_all_along_CMP3_vs_CMP2_identical": same_samples(rb.out, rc.out)[0] == 2 * len(L2)}
        e["ok"] = lock_at is not None and e["m_all_along_CMP3_vs_CMP2_identical"]
        r2r["three MIX coming to 64 from 0 / 64 / 127 (block 300)"] = e
        ok2 = ok2 and e["ok"]
        print("  (2) converge", e, flush=True)
        r2r["ok"] = ok2
        res["2_equal_mix_vs_CMP2"] = r2r
        oks["2"] = ok2
        print("(2)", ok2, "%.0fs" % (time.time() - t0), flush=True)

    # ---- (3) one band at 127, the others at 0
    if "3" in only:
        t0 = time.time()
        nb = 1500 if not q else 300
        prog = VC.looped(nb)
        L, R = prog[0][:nb * 32], prog[1][:nb * 32]
        r3r, ok3 = {}, True
        words = dict(VC.BASE, **VC.FXW, **VC.comp(32, 24, 32, 64, 3, 0))
        heavy = (10, 8, 0, 20, 64)
        alt = (60, 6, 40, 60, 30)
        for band in range(3):
            mixes = [0, 0, 0]
            mixes[band] = 127
            bands_a = [heavy if b == band else alt for b in range(3)]
            bands_b = [heavy if b == band else (90, 8, 0, 0, 127) for b in range(3)]
            bands_u = [heavy if b == band else UNITY for b in range(3)]
            ra = TailRun(vm3(), words, VC.ROUTE_INFX, sched3((bands_a, (68, 44)), mixes))
            ra.tprocess(L, R)
            rb = TailRun(vm3(), words, VC.ROUTE_INFX, sched3((bands_b, (68, 44)), mixes))
            rb.tprocess(L, R)
            ru = TailRun(vm3(), words, VC.ROUTE_INFX, sched3((bands_u, (68, 44)), (127, 127, 127)))
            ru.tprocess(L, R)
            dd, dabs = diff_db(ra.out, ru.out)
            gra = [g[band] for g in ra.gr]
            gru = [g[band] for g in ru.gr]
            e = {"other_bands_settings_cannot_reach_the_output (bit for bit)": ra.out == rb.out,
                 "vs_all_127_with_the_others_at_unity_max_diff_db_re_peak": dd,
                 "its_gain_reduction_identical_to_the_all_127_run": gra == gru,
                 "its_deepest_gain_reduction_db": round(min(gra), 2),
                 "the_others_deepest_gain_reduction_db (metered, not applied)": [round(min(g[b] for g in ra.gr), 2) for b in range(3) if b != band],
                 "pairs_locked": locked(ra.vm)}
            e["ok"] = (e["other_bands_settings_cannot_reach_the_output (bit for bit)"] and dd is not None and dd <= -100
                       and e["its_gain_reduction_identical_to_the_all_127_run"] and not e["pairs_locked"])
            r3r[("LOW", "MID", "HIGH")[band] + " at 127, the others at 0"] = e
            ok3 = ok3 and e["ok"]
            print("  (3)", band, e, flush=True)
        r3r["ok"] = ok3
        res["3_one_band_wet"] = r3r
        oks["3"] = ok3
        print("(3)", ok3, "%.0fs" % (time.time() - t0), flush=True)

    # ---- (5) guard and quiet re-init with the pairs apart
    if "5" in only:
        t0 = time.time()
        nr, nok = {}, True
        L, R = VC.load_input("drums", 300 * 32 / SR)
        words = dict(VC.BASE, **VC.NOFX, **VC.comp(32, 24, 32, 64, 3, 64))
        vals = words3(knobs17(DISTINCT), (20, 64, 127))
        for label in ("NaN in comp in L at block 60", "inf in comp in R at block 60",
                      "corrupted magic at block 60 (quiet re-init)", "MID's wet gain 2.0 at block 60 (range test)",
                      "HIGH's dry gain NaN at block 60", "LOW's MIX target 7.0 at block 60 (range test)"):
            vm = vm3()

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
            run = TailRun(vm, words, VC.ROUTE_IN, lambda b: tail_words(1 + b, vals))
            pr = []
            for b in range(len(L) // 32):
                run.tblock(L[32 * b:32 * b + 32], R[32 * b:32 * b + 32], b, inject=inj)
                if b in (59, 61):
                    pr.append(pairs(vm))
            k_after = knobs_now3(vm)
            fin_after = all(VC.finite(x) for x in run.out[0][62 * 32:] + run.out[1][62 * 32:])
            od = all(VC.finite(x) for lo, n in VC.OD_STATE for x in vm.rdf(lo, n))
            p61 = pr[1]
            end = pairs(vm)
            e = {"output_finite_from_block_62": fin_after, "master_overdrive_state_finite": od,
                 "trips": vm.rd32(SLOT + OFF["trips"]), "reinits": vm.rd32(SLOT + OFF["reinits"]),
                 "wet_gains_block_59": [round(p[0], 4) for p in pr[0]],
                 "wet_gains_block_61 (fading in from 0)": [round(p[0], 4) for p in p61],
                 "pairs_at_end_on_their_targets": all(abs(p[0] - p[2]) < 1e-4 and abs(p[1] - p[3]) < 1e-4 for p in end),
                 "knobs_after_are_the_tail's": k_after == expect3(vals)}
            restarted = e["trips"] >= 1 or e["reinits"] >= 1
            fade = all(p[0] < q_[0] for p, q_ in zip(p61, pr[0]) if q_[0] > 0.01)
            e["restarted"] = restarted
            e["ok"] = fin_after and od and e["knobs_after_are_the_tail's"] and restarted and fade and e["pairs_at_end_on_their_targets"]
            nr[label] = e
            nok = nok and e["ok"]
            print("  (5)", label, e, flush=True)
        vm = vm3()
        run = TailRun(vm, dict(VC.BASE, **VC.NOFX, **VC.comp(32, 24, 32, 64, 3, 0)), VC.ROUTE_IN, const_sched([0xffff] * 27))
        run.tprocess(L[:200 * 32], R[:200 * 32])
        e = {"knobs": knobs_now3(vm), "expected_clamped": expect3([0xffff] * 27),
             "output_finite": all(VC.finite(x) for x in run.out[0] + run.out[1]),
             "peak": round(max(abs(x) for x in run.out[0] + run.out[1]), 4), "trips": vm.rd32(SLOT + OFF["trips"])}
        e["ok"] = e["knobs"] == e["expected_clamped"] and e["output_finite"] and e["trips"] == 0
        nr["every tail value 0xffff (valid tail): clamped, the mixes 127"] = e
        nok = nok and e["ok"]
        nr["ok"] = nok
        res["5_guard_reinit_pairs_apart"] = nr
        oks["5"] = nok
        print("(5)", nok, "%.0fs" % (time.time() - t0), flush=True)

    # ---- (b) SHARC vs Mac
    if "b" in only:
        t0 = time.time()
        lib = VC.native(bdir, name="CMP3")
        tab = vm3().rdf(VC.TABLES, VC.NTAB)
        nat = NativeTail(lib, tab, OFF["slot_bytes_mac"], tbytes)
        secs = 2.0 if not q else 0.5
        mix_in = VC.load_input("mix", secs)
        drums_in = VC.load_input("drums", secs)
        nbk = len(mix_in[0]) // 32
        hot = ([(0, 8, 0, 0, 127)] * 3, (68, 44))
        bcfg = {
            "three distinct bands, MIX 20 / 64 / 127, mix, FX on": (
                dict(VC.BASE, **VC.FXW, **VC.comp(32, 24, 32, 64, 3, 39)), VC.ROUTE_INFX, mix_in, sched3(DISTINCT, (20, 64, 127))),
            "LOW at 127, MID and HIGH at 0, drums": (
                dict(VC.BASE, **VC.NOFX, **VC.comp(32, 24, 32, 64, 3, 39)), VC.ROUTE_IN, drums_in, sched3(DISTINCT, (127, 0, 0))),
            "every band knob, both crossovers and each band's MIX moving (through 0 apart), key SCS 18, mix": (
                dict(VC.BASE, **VC.FXW, **VC.comp(32, 24, 32, 64, 3, 39, scs=18)), VC.ROUTE_INFX, mix_in,
                list3([(moving17(b), mixes_apart(b)) for b in range(nbk)])),
            "the three MIX coming to 0 from 127 / 40 / 0 (block 20), distinct bands, drums": (
                dict(VC.BASE, **VC.NOFX, **VC.comp(32, 24, 32, 64, 3, 39)), VC.ROUTE_IN, drums_in,
                list3([(knobs17(DISTINCT), (127, 40, 0) if b < 20 else (0, 0, 0)) for b in range(nbk)])),
            "hot: 20:1, +24 dB makeup per band, MIX 127 / 100 / 64 (the wet clip), drums": (
                dict(VC.BASE, **VC.NOFX, **VC.comp(32, 24, 32, 64, 3, 39)), VC.ROUTE_IN, drums_in, sched3(hot, (127, 100, 64))),
        }
        br, bok = {}, True
        for name, (words, raw, inp, sched) in bcfg.items():
            run = TailRun(vm3(), words, raw, sched, keep=True)
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
            e = {"blocks": len(cap), "max_diff_db_re_peak": cmpr["max_diff_db_re_peak"], "meter_max_diff_db": round(mdiff, 6),
                 "reply_q15_max_diff": txd, "deepest_gr_db_per_band": [round(min(g[i] for g in run.gr), 2) for i in range(3)],
                 "peak": round(max(abs(x) for x in sh[0] + sh[1]), 4), "trips": run.vm.rd32(SLOT + OFF["trips"])}
            e["ok"] = VC.ok_db(cmpr) and mdiff < 1e-3 and txd <= 2 and e["trips"] == 0
            br[name] = e
            bok = bok and e["ok"]
            print("  (b)", name, e, flush=True)
        br["ok"] = bok
        res["b_sharc_vs_mac_mixes"] = br
        oks["b"] = bok
        print("(b)", bok, "%.0fs" % (time.time() - t0), flush=True)

    # ---- (d) cost
    if "d" in only:
        t0 = time.time()
        nb = 1500 if not q else 300
        mixl = VC.load_input("mix", nb * 32 / SR)
        rnd = random.Random(7)
        noise = ([rnd.uniform(-1, 1) for _ in range(nb * 32)], [rnd.uniform(-1, 1) for _ in range(nb * 32)])
        knames = list(VC.FL.KNOBS) + ["DEL_TIME", "DEL_WID", "DEL_FDBK", "DEL_HPF", "DEL_LPF"]
        loudfx = dict(CHR_DEL=127, CHR_REV=127, DEL_REV=127, REV_VOL=100)

        def fx_jump(b, fr):
            if b % 20 == 0:
                for k in knames:
                    VC.put(fr, k, rnd.choice([0, 127, rnd.randrange(128)]) << 8)
                VC.put(fr, "DEL_X", rnd.randrange(2) << 8)
        worst_sched = list3([(moving17(b), mixes_apart(b)) for b in range(nb)])
        dcfg = {
            "typical: band defaults, the three MIX 39 (locked: CMP2's path)": (
                dict(VC.BASE, **VC.FXW, **VC.TYPICAL), VC.ROUTE_INFX, mixl, sched3(DEFAULTS, (39, 39, 39)), None),
            "band defaults, the three MIX 0": (dict(VC.BASE, **VC.FXW, **VC.TYPICAL), VC.ROUTE_INFX, mixl,
                                               sched3(DEFAULTS, (0, 0, 0)), None),
            "band defaults, MIX 20 / 39 / 64 (apart)": (dict(VC.BASE, **VC.FXW, **VC.TYPICAL), VC.ROUTE_INFX, mixl,
                                                        sched3(DEFAULTS, (20, 39, 64)), None),
            "three distinct bands, MIX 20 / 64 / 127": (dict(VC.BASE, **VC.FXW, **VC.TYPICAL), VC.ROUTE_INFX, mixl,
                                                        sched3(DISTINCT, (20, 64, 127)), None),
            "one band at 127, the others at 0, distinct bands": (dict(VC.BASE, **VC.FXW, **VC.TYPICAL), VC.ROUTE_INFX, mixl,
                                                                 sched3(DISTINCT, (0, 127, 0)), None),
            "worst: every band knob, both crossovers and each MIX moving every block (through 0 apart), noise, FX jumping, self": (
                dict(dict(VC.BASE, **VC.FXW, **VC.W3_SELF), **loudfx), VC.ROUTE_INFX, noise, worst_sched, fx_jump),
            "worst, key (SCS 18)": (dict(dict(VC.BASE, **VC.FXW, **VC.W3), **loudfx), VC.ROUTE_INFX, noise, worst_sched, fx_jump),
        }
        dr, worst = {}, 0
        for name, (words, raw, inp, sched, each) in dcfg.items():
            run = TailRun(vm3(), words, raw, sched)
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
        dr["ok"] = all(v["trips"] == 0 for v in dr.values() if isinstance(v, dict)) and dr["worst_block_est_load_pct"] < 80
        res["d_cost"] = dr
        oks["d"] = dr["ok"]
        print("(d)", dr["ok"], "%.0fs" % (time.time() - t0), flush=True)

    # ---- (g) store census with the pairs apart
    if "g" in only:
        t0 = time.time()
        allowed = [(V.STACK_LO, V.STACK_TOP, "stack"), (SLOT, SLOT + sbytes, "CmpSlot"), (TST, TST + tbytes, "CmpTail"),
                   (0x2e6800, 0x2e6800 + VC.B.stub.SAVE_BYTES, "stub save area"), (0x25ba68, 0x25ba68 + 0x80, "out L"),
                   (0x25bae8, 0x25bae8 + 0x80, "out R"), (VC.GR_Y2, VC.GR_Y2 + 4, "0x257c1c meter"),
                   (VC.TX[0] + 0x10, VC.TX[0] + 0x18, "reply page 0"), (VC.TX[1] + 0x10, VC.TX[1] + 0x18, "reply page 1")]
        L, R = VC.load_input("mix", 130 * 32 / SR)
        census = {}
        for label, sched, inj in (
                ("MIX 20 / 64 / 127, distinct bands", sched3(DISTINCT, (20, 64, 127)), None),
                ("every knob and each MIX moving (through 0 apart)", list3([(moving17(b), mixes_apart(b)) for b in range(130)]), None),
                ("pairs apart, a guard trip (NaN input, block 60)", sched3(DISTINCT, (0, 64, 127)),
                 lambda b, vm_, ptrs: vm_.wr32(ptrs[0] + 20, 0x7fc00000) if b == 60 else None)):
            run = TailRun(vm3(), dict(VC.BASE, **VC.FXW, **VC.comp(32, 24, 32, 64, 3, 64)), VC.ROUTE_INFX, sched)
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
            census[label] = {"stores": len(log), "by_range": by, "outside": ["%#x@%#x" % (ad, pc) for pc, ad in stray[:10]],
                             "outside_count": len(stray)}
            print("  (g)", label, census[label]["stores"], census[label]["outside_count"], flush=True)
        g = {"store_census": census, "ok": all(v["outside_count"] == 0 for v in census.values())}
        res["g_stores"] = g
        oks["g"] = g["ok"]
        print("(g)", g["ok"], "%.0fs" % (time.time() - t0), flush=True)

    # ---- (s) an old kit seeded, against CMP2's fallback and the stock compressor
    if "s" in only:
        t0 = time.time()
        secs = 6.0 if not q else 1.5
        mats = {"drums": VC.load_input("drums", secs), "mix": VC.load_input("mix", secs)}
        # stock knobs (THR, ATK, REL, MUP, RAT index, MIX): the stock new-kit defaults at MIX 39, the plan's C1 wet,
        # heavy at MIX 64, and a kit at MIX 0
        kits = {"defaults, MIX 39": (32, 24, 32, 64, 3, 39), "C1: -20 dB 2:1 10/151 ms, MIX 127": (85, 40, 44, 0, 1, 127),
                "heavy: 20:1 fast, MUP 64, MIX 64": (10, 0, 0, 64, 7, 64), "MIX 0": (32, 24, 32, 64, 3, 0)}
        sr_, sok = {}, True
        for mname, (L, R) in mats.items():
            for kname, (thr, atk, rel, mup, rat, mix) in kits.items():
                words = dict(VC.BASE, **VC.NOFX, **VC.comp(thr, atk, rel, mup, rat, mix))
                seed = ([(thr, rat + 1, atk, rel, mup)] * 3, (0, 127))
                ra = TailRun(vm3(), words, VC.ROUTE_IN, sched3(seed, (mix, mix, mix)))
                ra.tprocess(L, R)
                rb = TailRun(vm2(), words, VC.ROUTE_IN, None)            # CMP2's fallback: zero tails
                rb.tprocess(L, R)
                rf = TailRun(vmf(), words, VC.ROUTE_IN, None)            # FTD1: the stock compressor
                rf.tprocess(L, R)

                def rms_db(x):
                    return 10 * math.log10(sum(v * v for v in x[0] + x[1]) / len(x[0] + x[1]) + 1e-30)

                oa, ob, of = octaves(ra.out), octaves(rb.out), octaves(rf.out)
                e = {"rms_db_seeded_CMP2fallback_stock": [round(rms_db(r.out), 2) for r in (ra, rb, rf)],
                     "octave_levels_db_seeded": [round(x, 1) for x in oa],
                     "octave_seeded_minus_CMP2_fallback_db": [round(x - y, 2) for x, y in zip(oa, ob)],
                     "octave_seeded_minus_stock_db": [round(x - y, 2) for x, y in zip(oa, of)],
                     "octave_CMP2_fallback_minus_stock_db": [round(x - y, 2) for x, y in zip(ob, of)],
                     "max_abs_octave_diff_db_vs_CMP2_fallback_vs_stock": [
                         round(max(abs(x - y) for x, y in zip(oa, ob)), 2), round(max(abs(x - y) for x, y in zip(oa, of)), 2)],
                     "deepest_gr_db_seeded_per_band": [round(min(g[i] for g in ra.gr), 2) for i in range(3)],
                     "deepest_gr_db_CMP2_fallback_per_band": [round(min(g[i] for g in rb.gr), 2) for i in range(3)],
                     "identical_to_stock" if mix == 0 else "_": same_samples(ra.out, rf.out)[0] == 2 * len(L) if mix == 0 else None}
                sr_[f"{mname}, {kname}"] = e
                if mix == 0:
                    sok = sok and e["identical_to_stock"]
                print("  (s)", mname, kname, e, flush=True)
        sr_["ok"] = sok
        sr_["note"] = ("the seeded kit: every band the kit's THR/ATK/REL/MUP and RAT index + 1, Low/Mid 30 Hz, Mid/High 16 kHz, "
                       "each band's MIX the kit's MIX; CMP2's fallback = CMP1's sound (the same knobs on all three bands at "
                       "196 Hz / 2.61 kHz); the stock = FTD1's compressor 0xb8541d")
        res["s_seeded_old_kit"] = sr_
        oks["s"] = sok
        print("(s)", sok, "%.0fs" % (time.time() - t0), flush=True)

    res["oks"] = oks
    res["seconds"] = round(time.time() - t_all)
    res["VERDICT"] = "PASS" if all(oks.values()) else "FAIL"
    out = VC.OUT / ("verify3.json" if not a.only else "verify3-%s.json" % a.only.replace(",", ""))
    out.write_text(json.dumps(res, indent=1))
    print("VERDICT", res["VERDICT"], oks, out, flush=True)


if __name__ == "__main__":
    main()
