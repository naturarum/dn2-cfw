#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""T6.3 CMP1 proof (i): a corrupted value in any state word, or a bad sample at any input, and the master
recovers. The CMP0 review's injection run (out/T6.3/review-safety3/inject.py, stuck.py) made general; CMP1b (the CMP1
review, out/T6.3/review-dsp-cmp1): two more values, a REL 127 setting, the quiet re-init counted, and a peak criterion
whose window includes the injection block.

    python3 tools/dspsplice/inject_cmp.py [CMP1|CMP0] [--workers 9] [--fields a,b,...] [--quick]

On the image in sharcvm (the whole block handler, as verify_cmp.py runs it), at the compressor call of block 60:
  - every 32-bit word of CmpSlot (the glue's words, every scalar of MBComp with the arrays expanded, the table
    pointer, and nine words sampled from the work buffers) set to each of 21 values: NaN, +-inf, +-1e30, +-1e9,
    +-denormal, -0, 0, the integers 0x7fffffff, 0xffffffff (NaN as a float), 0x40000000 (2.0) and 7 (a denormal),
    100, -100, 0.5, 5, and (CMP1b) -48 and 1e5 (inside CMP1's envelope bound -64 and its 2^20 guard);
  - each input of the compressor (comp in L/R, key L/R; seven samples of the block) set to NaN, +-inf, +-1e30,
    +-1e9, a denormal, +-100, 1e5;
on four settings (the review's: THR 30, ATK 10, REL 30, MUP 64, RAT index 5, at MIX 64, 0 and 127 with
self-detection, and MIX 64 with the key SCS 18), drums, 400 blocks (700 for the inputs: a finite sample below the
guard, +-100, is a +40 dBFS peak, and the gain comes back from it at the release speed, 64 ms time constant here);
and (CMP1b) a fifth, REL 127 (2 s) at MIX 127, 1,000 blocks, on the words that hold a level, an envelope, a gain or
a restart's inputs (REL127_FIELDS) and on the inputs.
Per case:
  nonfinite   handler output samples that are NaN or inf from the injection on;
  od_finite   the master's overdrive state words (the ones NaN latches, T6.4 6.1) finite at the end;
  silent      handler output blocks of exact silence from the injection on;
  trips       guard trips and restarts after the injection (mb.trips; a re-init counts from 0), plus the glue's quiet
              re-inits (reinits, CMP1b);
  peak_db     the largest output sample from the injection block through the 100 ms after it, against the same
              window of the run without the injection (CMP1b; CMP1's peak_100ms_db_vs_ref started after the
              injection block and was not judged);
  rms_db      the last 50 blocks' RMS against the same run without the injection;
  derived     every knob-derived word (thresholds, ratios, times, makeup targets, the crossover, detector and MIX
              coefficients and targets, the flags, the knobs, the table pointer) bit-equal to that run's at the end,
              the crossover glide within 0.05 octave of it (a 40 ms glide: the largest distance a plausible value
              can start from, 13 octaves, is 0.046 octave after the 340 blocks), the restart count-down 0, the magic
              set.
Pass: nonfinite 0, od_finite, at most 1 silent block more than the run without the injection (the drums file
ends inside the 700-block runs: at MIX 0 the output is then exact silence in both), trips <= 1, derived equal,
where two differences are not counted: the crossover coefficients while the glide that feeds them is still within
0.05 octave of the undisturbed run's (a smoother on its way), and a knob word holding a denormal (the SHARC
computes and compares with a denormal as 0, so the knob is the true 0 for the DSP); and, unless the case is
"plausible", |rms_db| <= 0.5 and peak_db <= +1.0. Plausible (CMP1b): a smoother or a signal state given a value
inside the range it reaches in normal operation (an envelope in [-32, 0], the makeup in [-1, 5], a MIX gain or the
dry crossfade in [0, 1], the crossover glide in [2, 15] log2 Hz, a filter or detector state or an input sample
finite and under the 2^12 guard): the compressor cannot tell it from a real state and it acts as one (an envelope
at 0 lets the makeup through until the attack catches up; a crossover state at 100 is a +40 dBFS transient, and
the gain comes back at the release speed). And (added after the CMP1b run, applied by --rejudge; a property of the
code, not of the results) a knob-derived word given a value the range test before the block accepts (a threshold in
[-10, 0], 1/ratio in [0.04, 1], a makeup target in [0, 4], a MIX target or crossover coefficient in [0, 1], a
crossover target in its range), or one it does not test (thr_lin, on, mix0, sc_ext): it acts until the scrub's
group derives it again, at most 8 blocks. Both classes' peaks and RMS are reported (summary "residual_classes",
"plausible_*"), not judged.
A case that fails on the RMS alone is run again over 1,500 blocks (--long): a finite value below the guard in a
filter or detector state, or a release coefficient wrong for the scrub's 8 blocks, acts as a real peak of that
size, and the gain comes back at the release speed. Writes out/T6.3/<NAME>-dsp/inject.json (summary, failures,
every case), inject.log, and with --long inject-long.json; --rejudge applies the pass rule above to a saved
inject.json (and inject-long.json) without running anything.
"""
import argparse
import json
import math
import struct
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.dont_write_bytecode = True
sys.path.insert(0, str(HERE))
import verify_cmp as VC  # noqa: E402

V = VC.V
NB, AT, NB_IN = 400, 60, 700
NB_LONG = 1500
VALS = {"NaN": 0x7fc00000, "+inf": 0x7f800000, "-inf": 0xff800000, "1e30": 0x7149f2ca, "-1e30": 0xf149f2ca,
        "1e9": 0x4e6e6b28, "-1e9": 0xce6e6b28, "denormal": 0x00000001, "-denormal": 0x80000001, "-0": 0x80000000,
        "0": 0x00000000, "int 0x7fffffff": 0x7fffffff, "int -1 (NaN)": 0xffffffff, "int 2^30 (2.0)": 0x40000000,
        "int 7": 0x00000007, "100": 0x42c80000, "-100": 0xc2c80000, "0.5": 0x3f000000, "5": 0x40a00000,
        "-48": 0xc2400000, "1e5": 0x47c35000}
IN_VALS = ["NaN", "+inf", "-inf", "1e30", "-1e30", "1e9", "-1e9", "denormal", "100", "-100", "1e5"]
SETS = {"MIX 64": VC.comp(30, 10, 30, 64, 5, 64), "MIX 0": VC.comp(30, 10, 30, 64, 5, 0),
        "MIX 127": VC.comp(30, 10, 30, 64, 5, 127), "MIX 64 key SCS 18": VC.comp(30, 10, 30, 64, 5, 64, scs=18),
        "REL 127 MIX 127": VC.comp(30, 10, 127, 64, 5, 127)}
REL_SET, NB_REL = "REL 127 MIX 127", 1000
# the REL 127 setting's words: everything that holds a level, an envelope or a gain, the bands' derived values, and
# the glue's words a restart or a re-init reads
REL127_FIELDS = (["magic", "fresh", "trips", "gr0", "gr1", "gr2", "grd", "reinits",
                  "mb.lf_lo", "mb.lf_hi", "mb.t_lf_lo", "mb.t_lf_hi", "mb.mw", "mb.md", "mb.t_mw", "mb.t_md", "mb.xw",
                  "mb.xs", "mb.ctl_left", "mb.trips", "mb.rec_left", "mb.kx[0]", "mb.kx[1]", "mb.ky[0]", "mb.ky[1]"]
                 + [f"mb.{f}[{c}].{k}" for f in ("s1", "s1lp", "apl", "s2", "s2lp") for c in (0, 1) for k in ("ic1", "ic2")]
                 + [f"mb.band[{b}].{k}" for b in range(3) for k in ("y1", "y2", "ca", "ca1", "cr", "cr1", "thr", "thr_lin",
                                                                    "rinv", "on", "mk", "mk_step", "t_mk", "hx[0]",
                                                                    "hx[1]", "hy[0]", "hy[1]")])
# plausible values (the module docstring): the words that hold a smoother or a signal state, and their ranges
GUARD = 4096.0                          # fx/mbcomp.c GUARD_SIG, 2^12 (since CMP2; the flashed CMP1b: 2^16, src/cmp1/)
ENV_WORDS = {f"mb.band[{b}].{k}" for b in range(3) for k in ("y1", "y2")}
MK_WORDS = {f"mb.band[{b}].mk" for b in range(3)}
UNIT_WORDS = {"mb.mw", "mb.md", "mb.xw"}
LF_WORDS = {"mb.lf_lo", "mb.lf_hi"}
SIG_WORDS = ({f"mb.{f}[{c}].{k}" for f in ("s1", "s1lp", "apl", "s2", "s2lp") for c in (0, 1) for k in ("ic1", "ic2")}
             | {"mb.kx[0]", "mb.kx[1]", "mb.ky[0]", "mb.ky[1]"}
             | {f"mb.band[{b}].{k}" for b in range(3) for k in ("hx[0]", "hx[1]", "hy[0]", "hy[1]")})
# the words that are functions of the knobs (and the tables), compared bit for bit at the end
DERIVED = (["mb.c_lo.a1", "mb.c_lo.a2", "mb.c_lo.a3", "mb.c_lo.k", "mb.c_hi.a1", "mb.c_hi.a2", "mb.c_hi.a3", "mb.c_hi.k",
            "mb.sb0", "mb.sp", "mb.t_lf_lo", "mb.t_lf_hi", "mb.t_mw", "mb.t_md", "mb.mix0", "mb.sc_ext", "mb.dirty"]
           + [f"mb.band[{b}].{k}" for b in range(3) for k in ("thr", "thr_lin", "rinv", "on", "ca", "ca1", "cr", "cr1",
                                                               "t_mk")])
G = {}


def setup(name, outdir=None):
    VC.configure(name)
    stream, info, cache = VC.image(name)
    off = VC.offsets((outdir or VC.OUT) / "build", name, every=True)
    # the SHARC's table pointer: 4 bytes right after the work buffers (the Mac's is 8-aligned)
    off["mb.tab"] = off["mb.buf_g[0][0]"] + 3 * 32 * 4
    return stream, cache, off


def init_worker(name, off, long_=False):
    """the offsets come from the main process: one compile of the offsets program, no race between workers"""
    G["name"] = name
    G["long"] = long_
    VC.configure(name)
    G["stream"], _, G["cache"] = VC.image(name)
    G["off"] = off
    G["L"], G["R"] = VC.load_input("drums", (NB_LONG if long_ else max(NB_IN, NB_REL)) * 32 / VC.SR)


def u32(vm, a):
    return vm.rd32(a)


def inrange_derived(target, valname):
    """a knob-derived word given a value the range test before the block (fx/mbcomp.c mb_implausible, CMP1b #15)
    accepts, or a derived word it does not test: it acts until the scrub's group derives it again (at most 8
    blocks). Single-word pairs (ca/ca1, cr/cr1) are always caught: one corrupted word breaks the pair."""
    if target is None or target.startswith("in:"):
        return False
    x = struct.unpack("<f", struct.pack("<I", VALS.get(valname, 0)))[0]
    if abs(x) < 1.1754944e-38:
        x = 0.0
    untested = {f"mb.band[{b}].{k}" for b in range(3) for k in ("thr_lin", "on")} | {"mb.mix0", "mb.sc_ext"}
    if target in untested:
        return True
    if not VC.finite(x):
        return False
    rng = {"thr": (-10.0, 0.001), "rinv": (0.04, 1.0), "t_mk": (0.0, 4.0001)}
    for b in range(3):
        for k, (lo, hi) in rng.items():
            if target == f"mb.band[{b}].{k}":
                return lo <= x <= hi
    if target in ("mb.t_mw", "mb.t_md") or (target.startswith("mb.c_") and target[-2:] in ("a1", "a2", "a3")):
        return 0.0 <= x <= 1.0
    if target == "mb.t_lf_lo":
        return 4.8 <= x <= 10.0
    if target == "mb.t_lf_hi":
        return 9.9 <= x <= 14.0
    return False


def residual_class(target, valname):
    """CMP1b: the two classes of values the compressor cannot tell from real ones (the module docstring)"""
    if plausible(target, valname):
        return "in-range smoother or state"
    if inrange_derived(target, valname):
        return "in-range knob-derived value (until its scrub group, at most 8 blocks)"
    return None


def plausible(target, valname):
    """does the injected value lie inside the range this word reaches in normal operation (the module docstring)?"""
    if target is None:
        return False
    x = struct.unpack("<f", struct.pack("<I", VALS.get(valname, 0)))[0]
    if not VC.finite(x):
        return False
    if abs(x) < 1.1754944e-38:
        x = 0.0                         # a denormal: the SHARC computes and compares with it as 0
    if target.startswith("in:") or target in SIG_WORDS:
        return abs(x) < GUARD
    if target in ENV_WORDS:
        return -32.0 <= x <= 0.0
    if target in MK_WORDS:
        return -1.0 <= x <= 5.0
    if target in UNIT_WORDS:
        return 0.0 <= x <= 1.0
    if target in LF_WORDS:
        return 2.0 <= x <= 15.0
    return False


def one(job):
    setname, target, valname = job
    nb = NB_IN if (target is not None and target.startswith("in:")) or valname == "none (inputs)" else NB
    if setname == REL_SET:
        nb = NB_REL
    if G.get("long"):
        nb = NB_LONG
    off = G["off"]
    vm = V.candidate_vm(G["stream"], cache=G["cache"])
    words = dict(VC.BASE, **VC.NOFX, **SETS[setname])
    rc = VC.Run(vm, words, VC.ROUTE_IN, keep=False)
    u = VALS.get(valname, 0)
    st = {}

    def inj(b, vm_, ptrs):
        if b != AT or target is None:
            return
        if target.startswith("in:"):
            k = int(target[3])
            for i in range(0, 32, 5):
                vm_.wr32(ptrs[k] + 4 * i, u)
        else:
            vm_.wr32(VC.SLOT + off[target], u)
        st["trips0"] = vm_.rd32(VC.SLOT + off["mb.trips"])
        st["magic0"] = vm_.rd32(VC.SLOT + off["magic"])
        st["reinits0"] = vm_.rd32(VC.SLOT + off["reinits"])
    t0 = time.time()
    rc.process(G["L"][:32 * nb], G["R"][:32 * nb], inject=inj)
    oL, oR = rc.out
    nonfin = sum(1 for i in range(AT * 32, len(oL)) for x in (oL[i], oR[i]) if not VC.finite(x))
    silent = sum(1 for blk in range(AT, nb) if all(oL[i] == 0.0 and oR[i] == 0.0 for i in range(32 * blk, 32 * blk + 32)))
    tail = [x for i in range(len(oL) - 50 * 32, len(oL)) for x in (oL[i], oR[i])]
    fin = [x for x in tail if VC.finite(x)]
    rms = math.sqrt(sum(x * x for x in fin) / max(len(fin), 1))
    post = [abs(x) for i in range((AT + 1) * 32, (AT + 1) * 32 + 4800) for x in (oL[i], oR[i]) if VC.finite(x)]
    # CMP1b: the injection block itself and the 100 ms after it
    win = [abs(x) for i in range(AT * 32, AT * 32 + 32 + 4800) for x in (oL[i], oR[i]) if VC.finite(x)]
    trips_end = vm.rd32(VC.SLOT + off["mb.trips"])
    reinits = (vm.rd32(VC.SLOT + off["reinits"]) - st.get("reinits0", 0)) & 0xffffffff if target is not None else 0
    if target is None:
        trips = trips_end
    elif st.get("magic0") != VC.MAGIC or (target == "mb.trips"):
        # a re-init (corrupted magic) counts from 0; the trip counter itself counts from the injected value
        trips = trips_end if st.get("magic0") != VC.MAGIC else (trips_end - st["trips0"]) & 0xffffffff
    else:
        trips = (trips_end - st["trips0"]) & 0xffffffff
    end = {k: vm.rd32(VC.SLOT + off[k]) for k in DERIVED + ["mb.tab", "magic", "fresh", "mb.rec_left", "mb.lf_lo",
                                                           "mb.lf_hi"] + [f"mb.knob[{i}]" for i in range(19)]
           if k in off}
    return {"set": setname, "target": target, "value": valname, "blocks": nb, "trips": trips, "reinits": reinits,
            "nonfinite": nonfin,
            "od_finite": all(VC.finite(x) for lo, n in VC.OD_STATE for x in vm.rdf(lo, n)), "silent": silent,
            "rms": rms, "peak_100ms_after": max(post) if post else None,
            "peak_block_and_100ms": max(win) if win else None, "plausible": residual_class(target, valname) is not None,
            "residual_class": residual_class(target, valname), "end": end,
            "secs": round(time.time() - t0, 1)}


def f32(u):
    return struct.unpack("<f", struct.pack("<I", u))[0]


def judge(r, ref):
    r["rms_db"] = round(20 * math.log10(max(r["rms"], 1e-30) / max(ref["rms"], 1e-30)), 3)
    r["peak_100ms_db_vs_ref"] = (round(20 * math.log10(max(r["peak_100ms_after"], 1e-30) / max(ref["peak_100ms_after"], 1e-30)), 2)
                                 if r["peak_100ms_after"] is not None else None)
    r["peak_db"] = (round(20 * math.log10(max(r["peak_block_and_100ms"], 1e-30) / max(ref["peak_block_and_100ms"], 1e-30)), 2)
                    if r.get("peak_block_and_100ms") is not None else None)
    mism = []
    e, f = r["end"], ref["end"]
    for k in DERIVED + ["mb.tab"]:
        if k in e and e[k] != f[k]:
            mism.append(k)
    for i in range(19):
        k = f"mb.knob[{i}]"
        if f32(e[k]) != f32(f[k]):             # -0 == +0
            mism.append(k)
    r["lf_end_minus_ref_octaves"] = []
    for k in ("mb.lf_lo", "mb.lf_hi"):
        a, b = f32(e[k]), f32(f[k])
        r["lf_end_minus_ref_octaves"].append(a - b if VC.finite(a) else None)
        if not (VC.finite(a) and abs(a - b) <= 0.05):
            mism.append(k)
    if e.get("mb.rec_left", 0) != 0:
        mism.append("mb.rec_left")
    if e["magic"] != VC.MAGIC or e["fresh"] != 0:
        mism.append("magic/fresh")
    r["derived_mismatch"] = mism
    r["ref_silent"] = ref["silent"]
    return verdict(r)


DENORMAL_VALUES = {"denormal", "-denormal", "-0", "0", "int 7"}


def verdict(r):
    """the pass rule (the module docstring) from a case's saved fields"""
    lf = r.get("lf_end_minus_ref_octaves") or [0.0, 0.0]
    mism = []
    for k in r["derived_mismatch"]:
        if k.startswith("mb.c_lo.") and lf[0] not in (None, 0.0) and abs(lf[0]) <= 0.05:
            continue                                  # the low glide still on its way
        if k.startswith("mb.c_hi.") and lf[1] not in (None, 0.0) and abs(lf[1]) <= 0.05:
            continue
        if k.startswith("mb.knob[") and r["target"] == k and r["value"] in DENORMAL_VALUES:
            continue                                  # a denormal knob: 0 to the SHARC
        mism.append(k)
    r["derived_mismatch_counted"] = mism
    r["extra_silent"] = r["silent"] - r.get("ref_silent", 0)
    r["restarts"] = r["trips"] + r.get("reinits", 0)
    r["ok_except_rms"] = (r["nonfinite"] == 0 and r["od_finite"] and r["extra_silent"] <= 1 and r["restarts"] <= 1
                          and not mism)
    pk = r.get("peak_db")
    r["peak_ok"] = pk is None or pk <= 1.0
    r["ok"] = r["ok_except_rms"] and (r.get("plausible") or (abs(r["rms_db"]) <= 0.5 and r["peak_ok"]))
    return r


def summarize(name, stream_sha, res, fields_n, sets, t0, extra=None):
    fails = [r for r in res if not r["ok"]]
    by_field = {}
    for r in fails:
        by_field.setdefault(r["target"], []).append(f"{r['set']}: {r['value']}")
    trips_hist = {}
    for r in res:
        trips_hist[str(r["trips"])] = trips_hist.get(str(r["trips"]), 0) + 1
    s = {"name": name, "section7_sha256": stream_sha, "cases": len(res), "failed": len(fails),
         "state_words": fields_n, "values": list(VALS), "input_values": IN_VALS, "settings": sets,
         "blocks": NB, "blocks_input_cases": NB_IN, "injected_at_block": AT,
         "max_abs_lf_end_minus_ref_octaves": max((abs(x) for r in res for x in (r.get("lf_end_minus_ref_octaves") or [])
                                                  if x is not None), default=0.0),
         "max_extra_silent_blocks": max(r["extra_silent"] for r in res),
         "max_nonfinite": max(r["nonfinite"] for r in res),
         "all_od_finite": all(r["od_finite"] for r in res), "trips_histogram": trips_hist,
         "max_abs_rms_db_last_50_blocks": max(abs(r["rms_db"]) for r in res),
         "max_peak_100ms_db_vs_ref": max(r["peak_100ms_db_vs_ref"] for r in res if r["peak_100ms_db_vs_ref"] is not None),
         "judged_cases": sum(1 for r in res if not r.get("plausible")),
         "judged_max_peak_db_injection_block_and_100ms": max((r["peak_db"] for r in res
                                                               if not r.get("plausible") and r.get("peak_db") is not None),
                                                              default=None),
         "judged_max_abs_rms_db": max((abs(r["rms_db"]) for r in res if not r.get("plausible")), default=None),
         "judged_cases_peak_over_1db": sum(1 for r in res if not r.get("plausible") and not r.get("peak_ok", True)),
         "plausible_cases": sum(1 for r in res if r.get("plausible")),
         "plausible_max_peak_db": max((r["peak_db"] for r in res if r.get("plausible") and r.get("peak_db") is not None),
                                      default=None),
         "plausible_cases_peak_over_1db": sum(1 for r in res if r.get("plausible") and not r.get("peak_ok", True)),
         "plausible_cases_rms_over_0_5db": sum(1 for r in res if r.get("plausible") and abs(r["rms_db"]) > 0.5),
         "plausible_by_target_peak_over_1db": sorted({r["target"] for r in res if r.get("plausible")
                                                      and not r.get("peak_ok", True)}),
         "residual_classes": {c: {"cases": sum(1 for r in res if r.get("residual_class") == c),
                                  "max_peak_db": max((r["peak_db"] for r in res if r.get("residual_class") == c
                                                      and r.get("peak_db") is not None), default=None),
                                  "cases_peak_over_1db": sum(1 for r in res if r.get("residual_class") == c
                                                             and not r.get("peak_ok", True)),
                                  "max_abs_rms_db": max((abs(r["rms_db"]) for r in res if r.get("residual_class") == c),
                                                        default=None),
                                  "targets_peak_over_1db": sorted({r["target"] + " = " + r["value"] for r in res
                                                                   if r.get("residual_class") == c
                                                                   and not r.get("peak_ok", True)})[:60]}
                              for c in sorted({r.get("residual_class") for r in res if r.get("residual_class")})},
         "restarts_histogram": {str(k): sum(1 for r in res if r.get("restarts", r["trips"]) == k)
                                for k in sorted({r.get("restarts", r["trips"]) for r in res})},
         "reinit_cases": sum(1 for r in res if r.get("reinits", 0)),
         "cases_with_counted_derived_mismatch": sum(1 for r in res if r["derived_mismatch_counted"]),
         "failed_by_target": by_field, "seconds": round(time.time() - t0) if t0 else None}
    if extra:
        s.update(extra)
    return s


def rejudge(a):
    p = VC.OUT / "inject.json"
    d = json.loads(p.read_text())
    res = d["all"]
    ref = {(r["set"], r["blocks"]): r for r in res if r["target"] is None}
    for r in res:
        r["ref_silent"] = ref[(r["set"], r["blocks"])]["silent"]
        r["residual_class"] = residual_class(r["target"], r["value"])
        r["plausible"] = r["residual_class"] is not None
        verdict(r)
    longd = {}
    lp = VC.OUT / "inject-long.json"
    if lp.exists():
        for r in json.loads(lp.read_text())["all"]:
            if r["target"] is not None:
                longd[(r["set"], r["target"], r["value"])] = r
    n_long = 0
    for r in res:
        if not r["ok"] and r["ok_except_rms"]:
            lr = longd.get((r["set"], r["target"], r["value"]))
            if lr is not None:
                n_long += 1
                r["long_run"] = {k: lr[k] for k in ("blocks", "rms_db", "trips", "nonfinite", "od_finite", "extra_silent",
                                                    "derived_mismatch_counted", "ok")}
                r["ok"] = lr["ok"]
    first = d.get("summary_first_pass") or d["summary"]
    fields_n = first["state_words"]
    summ = summarize(first["name"], first["section7_sha256"], res, fields_n, first["settings"], None,
                     {"rejudged": "pass rule of the module docstring; the first pass counted silence absolutely, every "
                                  "crossover-coefficient and knob-word difference, and had no long run",
                      "first_pass_failed": first["failed"], "cases_settled_by_the_1500_block_run": n_long,
                      "seconds_first_pass": first.get("seconds")})
    fails = [r for r in res if not r["ok"]]
    p.write_text(json.dumps({"summary": summ, "summary_first_pass": first, "failures": fails, "all": res},
                            indent=1, default=str))
    print(json.dumps({k: v for k, v in summ.items() if k not in ("values", "input_values")}, indent=1))
    return 0 if not fails else 1


def run_long(a):
    stream, cache, off = setup(a.name)
    d = json.loads((VC.OUT / "inject.json").read_text())
    res0 = d["all"]
    ref0 = {(r["set"], r["blocks"]): r for r in res0 if r["target"] is None}
    for r in res0:
        r["ref_silent"] = ref0[(r["set"], r["blocks"])]["silent"]
        r["residual_class"] = residual_class(r["target"], r["value"])
        r["plausible"] = r["residual_class"] is not None
        verdict(r)
    redo = [(r["set"], r["target"], r["value"]) for r in res0 if not r["ok"] and r["ok_except_rms"]]
    sets = sorted({j[0] for j in redo})
    jobs = [(s_, None, "none") for s_ in sets] + redo
    t0 = time.time()
    res = []
    with ProcessPoolExecutor(a.workers, initializer=init_worker, initargs=(a.name, off, True)) as ex:
        for r in ex.map(one, jobs, chunksize=2):
            res.append(r)
    ref = {r["set"]: r for r in res if r["target"] is None}
    for r in res:
        judge(r, ref[r["set"]])
    for r in res:
        r.pop("end", None)
    fails = [r for r in res if not r["ok"]]
    summ = {"cases": len(redo), "blocks": NB_LONG, "failed": len(fails), "seconds": round(time.time() - t0),
            "max_abs_rms_db_last_50_blocks": max(abs(r["rms_db"]) for r in res)}
    (VC.OUT / "inject-long.json").write_text(json.dumps({"summary": summ, "failures": fails, "all": res}, indent=1,
                                                         default=str))
    print(json.dumps(summ, indent=1))
    return 0 if not fails else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("name", nargs="?", default="CMP1", choices=["CMP0", "CMP1", "CMP2"])
    ap.add_argument("--workers", type=int, default=9)
    ap.add_argument("--fields", help="only these state words (comma-separated names)")
    ap.add_argument("--sets", help="only these settings (comma-separated)")
    ap.add_argument("--out", help="output file name (default inject.json)")
    ap.add_argument("--outdir", help="write there instead of the image's own directory (e.g. a CMP0 comparison run)")
    ap.add_argument("--long", action="store_true", help="run again, over 1,500 blocks, the cases of inject.json that "
                    "fail on the RMS alone -> inject-long.json")
    ap.add_argument("--rejudge", action="store_true", help="apply the pass rule to the saved inject.json "
                    "(and inject-long.json): no run")
    a = ap.parse_args()
    if a.rejudge or a.long:
        VC.configure(a.name)
        return rejudge(a) if a.rejudge else run_long(a)
    outdir = Path(a.outdir).resolve() if a.outdir else None
    if outdir:
        outdir.mkdir(parents=True, exist_ok=True)
    stream, cache, off = setup(a.name, outdir)
    odir = outdir or VC.OUT
    V.candidate_vm(stream, cache=cache)          # make the snapshot once, before the workers
    sets = a.sets.split(",") if a.sets else list(SETS)
    skip = {"mb", "mb.knob", "band_bytes", "slot_bytes_mac"}
    fields = [k for k in off if k not in skip]
    if a.fields:
        fields = [k for k in fields if k in a.fields.split(",")]
    jobs = []
    missing = [f for f in REL127_FIELDS if f not in off]
    if missing:
        raise SystemExit(f"REL127_FIELDS not in the offsets: {missing}")
    for s in sets:
        jobs.append((s, None, "none"))
        if not a.fields:
            jobs.append((s, None, "none (inputs)"))
            for k in ("in:0", "in:1", "in:2", "in:3"):
                for v in IN_VALS:
                    jobs.append((s, k, v))
        for f in fields:
            if s == REL_SET and f not in REL127_FIELDS:
                continue
            for v in VALS:
                jobs.append((s, f, v))
    t0 = time.time()
    res = []
    log = open(odir / ((a.out or "inject.json").replace(".json", ".log")), "w")
    print(f"{a.name}: {len(jobs)} cases, {len(fields)} state words, {len(sets)} settings", file=log, flush=True)
    with ProcessPoolExecutor(a.workers, initializer=init_worker, initargs=(a.name, off)) as ex:
        for r in ex.map(one, jobs, chunksize=4):
            res.append(r)
            if len(res) % 200 == 0:
                print(len(res), "/", len(jobs), "%.0fs" % (time.time() - t0), file=log, flush=True)
    ref = {(r["set"], r["blocks"]): r for r in res if r["target"] is None}
    for r in res:
        judge(r, ref[(r["set"], r["blocks"])])
    fails = [r for r in res if not r["ok"]]
    summary = summarize(a.name, VC.B.sha(stream), res, len(fields), sets, t0)
    for r in res:
        r.pop("end", None)
    out = odir / (a.out or "inject.json")
    out.write_text(json.dumps({"summary": summary, "failures": fails, "all": res}, indent=1, default=str))
    print(json.dumps({k: v for k, v in summary.items() if k != "failed_by_target"}, indent=1), file=log, flush=True)
    print(json.dumps(summary, indent=1)[:4000])
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
