#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""T6.3 CMP2D: the diagnostic build's checks in sharcvm (notes/T6.3-cmp2d.md). CMP2D = CMP2's DSP with a load meter
(src/cmp2d/cmp_load.c) in place of the glue's band meters.

    python3 tools/dspsplice/verify_cmp2d.py [--quick] [--only a,b,c,d]

(a) audio: CMP2D against CMP2 (out/T6.3/CMP2-dsp, 1a888114) on the same input and frames, the SPI half alternating
    as on the unit: every handler output sample bit-identical, the bands' gain reductions after every block, the
    reply pages but for word 0 (the handler's cycle count) and +0x10..+0x17 (the readout), and at the end the
    compressor's slot (but CMP2's meter words word10/word14), the tail reader's state, the flanger/tape state, the
    stock compressor state (but 0x257c1c, the meter word) and the master overdrive state. Settings: the fallback
    (zero tails), FX and a key with the stock knobs moving, and tail mode from the owner's starting point with each
    band's MUP swept 0 -> 127 (the trigger on the unit), all three swept loud, every band knob moving, MIX 0.
(b) the readout: CMP2D with EMUCLK set before every block, so that the handler entries are a scheduled period apart
    (666,667 cycles, two short-period spikes, one long period; across the counter's 2^32 wrap): every block the frame
    walk passes; the measured busy, period and compressor cycles equal the handler's own count, the schedule and the
    glue's span; load and share; the 1 s peak-hold windows and the all-time maxima after the settle, mirrored here;
    the reply half-words 8..10 in both pages, half-word 11 direct (the page the handler did not use) and through
    the stock getter (the handler's page, 0x1c3117), within 1 of q; the timeline (peak-hold up after a spike for one
    to two windows, the first spike before the settle not in the maxima).
(c) stores of the compressor call (tail mode, every knob moving; the first blocks): only CmpSlot, CmpTail, CmpLoad,
    the stack, the stub save area, the two outputs, 0x257c1c and reply +0x10..+0x17 of both pages.
(d) stack depth and instructions per block, CMP2D next to CMP2.
Results: out/T6.3/CMP2D-dsp/verify.json. In sharcvm, EMUCLK counts instructions, so (b)'s loads are instruction
counts over a scheduled period, not the unit's cycles.
"""
import argparse
import gc
import json
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
import verify_cmp2 as V2  # noqa: E402    (configures VC for CMP2: SLOT, the tail helpers, TailRun)
import memguard  # noqa: E402

V = VC.V
FB = V.fullblock
OUT = ROOT / "out/T6.3/CMP2D-dsp"
CL = 0x2e2800
FIELDS = ["have", "prev_entry", "prev_tx", "prev_comp", "t0", "ok", "calls", "blocks", "bad", "win_n",
          "win_cur:f", "win_prev:f", "max_load:f", "max_share:f", "last_load:f", "last_share:f",
          "last_busy", "last_period", "last_comp", "max_busy", "w10", "w14", "gr_db:f", "pad"]
WIN, SETTLE, Q11_MAX = 1500, 1500, 32750
SLOT, TST = VC.SLOT, VC.TAILST[0]
SLOT_METER = (0x3c, 0x44)          # CmpSlot word10, word14: CMP2 writes them, CMP2D not (the layout is CMP2's)
START = ([(64, 4, 24, 32, 0)] * 3, (68, 44))      # FLASH-CMP2.md's starting point, MUP 0 on every band


def f32(x):
    return struct.unpack("<f", struct.pack("<f", x))[0]


def cl_read(vm):
    raw = vm.read(CL, 4 * len(FIELDS))
    out = {}
    for i, f in enumerate(FIELDS):
        w = raw[4 * i:4 * i + 4]
        if f.endswith(":f"):
            out[f[:-2]] = struct.unpack("<f", w)[0]
        else:
            out[f] = struct.unpack("<I", w)[0]
    return out


def q15(x):
    y = f32(f32(x * 32767.0) + 0.5)
    y = min(max(y, 0.0), 32767.0)
    return int(y)


def unswap(w):
    return ((w >> 16) | (w << 16)) & 0xffffffff


def sched_mup(band, nb, all_bands=False, rat=4):
    """tail mode from START: one band's MUP (or all three) glides 0 -> 127 over the first two thirds of the run"""
    def vals(b):
        bands = [list(x) for x in START[0]]
        m = min(127.0, 127.0 * b / max(1, (2 * nb) // 3))
        for j in range(3):
            bands[j][1] = rat
            if all_bands or j == band:
                bands[j][4] = m
        return V2.knobs17(([tuple(x) for x in bands], START[1]))
    return lambda b: V2.tail_words(1 + b, V2.words_of(vals(b)))


def sched_moving(seed):
    """every band knob and both crossovers at a new random value every block"""
    rnd = random.Random(seed)
    cache = {}

    def vals(b):
        if b not in cache:
            bands = [(rnd.randrange(128), rnd.randrange(9), rnd.randrange(128), rnd.randrange(128), rnd.randrange(128))
                     for _ in range(3)]
            cache[b] = V2.knobs17((bands, (rnd.randrange(128), rnd.randrange(128))))
        return cache[b]
    return lambda b: V2.tail_words(1 + b, V2.words_of(vals(b)))


def masked_page(p):
    b = bytearray(p)
    b[0:4] = bytes(4)
    b[0x10:0x18] = bytes(8)
    return bytes(b)


def run_cfg(vm, words, route, sched, L, R, each=None, keep_pages=True):
    run = V2.TailRun(vm, words, route, sched)
    pages, grs = [], []
    nb = len(L) // 32
    for b in range(nb):
        if each:
            each(b, run.r.d.frame)
        run.tblock(L[32 * b:32 * b + 32], R[32 * b:32 * b + 32], b, tx_full=keep_pages)
        if keep_pages:
            pages.append(tuple(masked_page(run.tx_pages[-1][k * 0x1000:(k + 1) * 0x1000][:0xabc]) for k in (0, 1)))
            run.tx_pages.clear()
    state = {
        "slot": vm.read(SLOT, 1840), "tail": vm.read(TST, 208), "flg": vm.read(0x300000, 0x8400),
        "stock": vm.read(0x257ab0, 0x257c28 - 0x257ab0), "od": b"".join(vm.read(a, 4 * n) for a, n in VC.OD_STATE),
        "trips": vm.rd32(SLOT + V2.OFF["mb.trips"]) if "mb.trips" in V2.OFF else None,
    }
    return run.out, run.gr, pages, state, run.r.per_block


def proof_a(cfgs, img2, imgd):
    res, ok_all = {}, True
    for label, (words, route, sched, inp, each) in cfgs.items():
        t0 = time.time()
        L, R = inp
        o2, g2, p2, s2, n2 = run_cfg(V.candidate_vm(img2[0], cache=img2[2]), words, route, sched, L, R, each)
        gc.collect()
        od, gd, pd, sd, nd = run_cfg(V.candidate_vm(imgd[0], cache=imgd[2]), words, route, sched, L, R, each)
        gc.collect()
        nsamp = len(o2[0]) + len(o2[1])
        same = sum(1 for a, b in zip(o2[0] + o2[1], od[0] + od[1]) if struct.pack("<f", a) == struct.pack("<f", b))
        slot2, slotd = bytearray(s2["slot"]), bytearray(sd["slot"])
        slot2[SLOT_METER[0]:SLOT_METER[1]] = bytes(8)
        slotd[SLOT_METER[0]:SLOT_METER[1]] = bytes(8)
        st2, std = bytearray(s2["stock"]), bytearray(sd["stock"])
        gi = VC.GR_Y2 - 0x257ab0
        st2[gi:gi + 4] = bytes(4)
        std[gi:gi + 4] = bytes(4)
        pages_same = sum(1 for a, b in zip(p2, pd) if a == b)
        r = {"blocks": len(n2), "samples": nsamp, "samples_identical": same,
             "gain_reduction_blocks_identical": sum(1 for a, b in zip(g2, gd) if a == b),
             "reply_pages_identical_but_word0_and_readout": pages_same,
             "slot_identical_but_cmp2_meter_words": bytes(slot2) == bytes(slotd),
             "tail_state_identical": s2["tail"] == sd["tail"], "flanger_tape_state_identical": s2["flg"] == sd["flg"],
             "stock_comp_state_identical_but_0x257c1c": bytes(st2) == bytes(std), "master_od_state_identical": s2["od"] == sd["od"],
             "peak": max([abs(x) for x in o2[0] + o2[1]] or [0.0]),
             "insns_per_block_cmp2_mean_max": [sum(n2) / len(n2), max(n2)],
             "insns_per_block_cmp2d_mean_max": [sum(nd) / len(nd), max(nd)],
             "seconds": round(time.time() - t0, 1), "peak_rss_mb": round(memguard.peak_rss_mb())}
        r["ok"] = (same == nsamp and r["gain_reduction_blocks_identical"] == len(g2) and pages_same == len(p2)
                   and r["slot_identical_but_cmp2_meter_words"] and r["tail_state_identical"]
                   and r["flanger_tape_state_identical"] and r["stock_comp_state_identical_but_0x257c1c"]
                   and r["master_od_state_identical"])
        ok_all = ok_all and r["ok"]
        res[label] = r
        print("(a) %-58s %s  %d/%d samples, %d blocks, +%.0f insns/block, %.0fs, rss %d MB" % (
            label, "ok" if r["ok"] else "DIFFERS", same, nsamp, len(n2), r["insns_per_block_cmp2d_mean_max"][0]
            - r["insns_per_block_cmp2_mean_max"][0], time.time() - t0, r["peak_rss_mb"]), flush=True)
        memguard.check()
    return ok_all, res


def proof_b(imgd, nb):
    """the readout with a scheduled period"""
    t0 = time.time()
    vm = V.candidate_vm(imgd[0], cache=imgd[2])
    P = 666667
    per = [P] * nb
    spikes = {"spike 1 (blocks 100..104, during the settle)": (100, 105, 420000),
              "spike 2 (blocks 2000..2004)": (2000, 2005, 450000)}
    for _, (b0, b1, p) in spikes.items():
        for b in range(b0, min(b1, nb)):
            per[b] = p
    if nb > 2500:
        per[2500] = 1500000                               # a long period (a block without the compressor call)
    E = [0xf0000000]                                      # the counter wraps after ~400 blocks
    for b in range(nb - 1):
        E.append((E[-1] + per[b]) & 0xffffffff)
    L, R = VC.looped(nb)                                  # drums then the mix, looped (no silent stretch)
    words = dict(VC.BASE, **VC.NOFX, **VC.comp(32, 24, 32, 64, 3, 127))

    def each(b, fr):
        vm.set_reg("EMUCLK", E[b])
    run = V2.TailRun(vm, words, VC.ROUTE_IN, sched_mup(0, nb))
    busy, comp = [], []
    model = {"win_cur": 0.0, "win_prev": 0.0, "win_n": 0, "blocks": 0, "max_load": 0.0, "max_share": 0.0}
    bad, getter_diffs, nbad = [], [], 0
    timeline = {}
    for b in range(nb):
        each(b, run.r.d.frame)
        run.tblock(L[32 * b:32 * b + 32], R[32 * b:32 * b + 32], b)
        c = cl_read(vm)
        page = 0x2c49d0 + 0x1000 * (b & 1)                # the handler's reply page (TailRun alternates the half)
        other = 0x2c49d0 + 0x1000 * (1 - (b & 1))
        busy.append(unswap(vm.rd32(page)))
        comp.append(c["prev_comp"])
        why = []
        if c["ok"] != 1 or c["bad"] != 0 or c["prev_tx"] != page:
            why.append("walk: ok %d bad %d tx %#x" % (c["ok"], c["bad"], c["prev_tx"]))
        if b >= 1:
            period = (E[b] - E[b - 1]) & 0xffffffff
            if (c["last_busy"], c["last_period"], c["last_comp"]) != (busy[b - 1], period, comp[b - 1]):
                why.append("busy/period/comp %s != %s" % ((c["last_busy"], c["last_period"], c["last_comp"]),
                                                          (busy[b - 1], period, comp[b - 1])))
            el, es = busy[b - 1] / period, comp[b - 1] / period
            if abs(c["last_load"] - el) > 1e-6 or abs(c["last_share"] - es) > 1e-6:
                why.append("load %.7f/%.7f share %.7f/%.7f" % (c["last_load"], el, c["last_share"], es))
            # mirror the windows and maxima with the DSP's own values
            ld, sh = c["last_load"], c["last_share"]
            model["blocks"] += 1
            model["win_cur"] = max(model["win_cur"], ld)
            model["win_n"] += 1
            if model["win_n"] >= WIN:
                model["win_prev"], model["win_cur"], model["win_n"] = model["win_cur"], 0.0, 0
            if model["blocks"] > SETTLE:
                model["max_load"] = max(model["max_load"], ld)
                model["max_share"] = max(model["max_share"], sh)
        for k in ("win_cur", "win_prev", "win_n", "blocks", "max_load", "max_share"):
            if c[k] != model[k]:
                why.append("%s %r != %r" % (k, c[k], model[k]))
        q8, q9, q10 = q15(max(model["win_cur"], model["win_prev"])), q15(model["max_load"]), q15(model["max_share"])
        q11 = min(q9, Q11_MAX)
        hw = [struct.unpack("<4H", vm.read(t + 0x10, 8)) for t in (page, other)]
        if hw[1] != (q8, q9, q10, q11) or hw[0][:3] != (q8, q9, q10):
            why.append("reply %s, want %s" % (hw, (q8, q9, q10, q11)))
        getter_diffs.append(hw[0][3] - q11)
        if abs(hw[0][3] - q11) > 1:
            why.append("getter half-word 11 %d, want %d" % (hw[0][3], q11))
        if why:
            nbad += 1
            if len(bad) < 20:
                bad.append("block %d: %s" % (b, "; ".join(why)))
        if b in (1, 99, 101, 106, 1499, 1501, 1502, 1999, 2001, 2006, 2501, 2999, 3001, 3002, 4499, 4501, 4502, nb - 1):
            timeline[b] = {"q8_peak_hold": q8, "q9_max_load": q9, "q10_max_share": q10, "q11_getter": hw[0][3],
                           "px": [q8 // 655, q9 // 655, q10 // 655, hw[0][3] // 655],
                           "last_load": round(c["last_load"], 5), "last_share": round(c["last_share"], 5),
                           "gr_db": round(c["gr_db"], 3)}
    ok = not bad
    r = {"blocks": nb, "period": P, "spikes": {k: list(v) for k, v in spikes.items()}, "long_period_block": 2500,
         "emuclk_start": "%#x" % E[0], "blocks_with_problems": nbad, "problems": bad, "ok": ok,
         "getter_minus_q11": {"min": min(getter_diffs), "max": max(getter_diffs)},
         "busy_insns_mean_max": [sum(busy) / nb, max(busy)], "comp_insns_mean_max": [sum(comp) / nb, max(comp)],
         "final": {k: v for k, v in cl_read(vm).items() if k not in ("pad",)}, "timeline": timeline,
         "seconds": round(time.time() - t0, 1)}
    print("(b) readout", "ok" if ok else "PROBLEMS", "%d blocks, getter-q11 %s, %.0fs" % (
        nb, r["getter_minus_q11"], time.time() - t0), flush=True)
    for b, t in timeline.items():
        print("     block %5d  px %s  q %s  load %.4f share %.4f  y %.3f dB" % (
            b, t["px"], [t["q8_peak_hold"], t["q9_max_load"], t["q10_max_share"], t["q11_getter"]],
            t["last_load"], t["last_share"], t["gr_db"]))
    for x in bad[:8]:
        print("    ", x)
    return ok, r


def proof_c(imgd, info):
    allowed = [(V.STACK_LO, V.STACK_TOP, "stack"), (SLOT, SLOT + 1840, "CmpSlot"), (TST, TST + 208, "CmpTail"),
               (CL, CL + 4 * len(FIELDS), "CmpLoad"), (0x2e6800, 0x2e6800 + VC.B.stub.SAVE_BYTES, "stub save area"),
               (0x25ba68, 0x25ba68 + 0x80, "out L"), (0x25bae8, 0x25bae8 + 0x80, "out R"),
               (VC.GR_Y2, VC.GR_Y2 + 4, "0x257c1c meter"),
               (VC.TX[0] + 0x10, VC.TX[0] + 0x18, "reply page 0 +0x10..+0x17"),
               (VC.TX[1] + 0x10, VC.TX[1] + 0x18, "reply page 1 +0x10..+0x17")]
    L, R = VC.load_input("mix", 70 * 32 / VC.SR)
    census = {}
    for label, sched in (("tail mode, every knob moving", sched_moving(7)),
                         ("first tail after the fallback (block 3)",
                          lambda b: V2.tail_words(b, V2.words_of(V2.knobs17(V2.DISTINCT))) if b >= 3 else None)):
        run = V2.TailRun(V.candidate_vm(imgd[0], cache=imgd[2]), dict(VC.BASE, **VC.FXW, **VC.comp(32, 24, 32, 64, 3, 64)),
                         VC.ROUTE_INFX, sched)
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
        gc.collect()
    ok = all(v["outside_count"] == 0 for v in census.values())
    print("(c) stores", "ok" if ok else "OUTSIDE", {k: (v["stores"], v["outside_count"]) for k, v in census.items()}, flush=True)
    return ok, census


def proof_d(img2, imgd, nb):
    L, R = VC.load_input("mix", nb * 32 / VC.SR)
    out = {}
    for name, img in (("CMP2", img2), ("CMP2D", imgd)):
        run = V2.TailRun(V.candidate_vm(img[0], cache=img[2]), dict(VC.BASE, **VC.FXW, **VC.comp(32, 24, 32, 64, 3, 64, scs=18)),
                         VC.ROUTE_INFX, sched_moving(11))
        for b in range(nb):
            run.tblock(L[32 * b:32 * b + 32], R[32 * b:32 * b + 32], b, paint=(b % 25 == 0))
        n = run.r.per_block
        out[name] = {"stack_bytes": run.stack, "insns_mean": sum(n) / len(n), "insns_max": max(n),
                     "est_load_mean_max_pct": [round(100 * VC.LOAD(sum(n) / len(n)), 2), round(100 * VC.LOAD(max(n)), 2)]}
        gc.collect()
    out["added_insns_mean"] = out["CMP2D"]["insns_mean"] - out["CMP2"]["insns_mean"]
    ok = out["CMP2D"]["stack_bytes"] < 2000
    print("(d) stack/cost", json.dumps(out), flush=True)
    return ok, out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--only", help="comma-separated proofs (a,b,c,d)")
    a = ap.parse_args()
    only = set(a.only.split(",")) if a.only else set("abcd")
    t_all = time.time()
    img2 = VC.image("CMP2")
    imgd = VC.image("CMP2D")
    V2.OFF.update(VC.offsets(OUT / "offsets", "CMP2", every=True))          # CmpSlot is CMP2's, field for field
    res = {"name": "CMP2D", "section7_sha256": imgd[1]["section7"]["sha256"], "cmp2_section7_sha256": img2[1]["section7"]["sha256"],
           "proofs_run": "".join(sorted(only))}
    oks = {}
    if "a" in only:
        nb = 200 if a.quick else 600
        drums4 = VC.load_input("drums", nb * 32 / VC.SR, gain=4.0)
        drums2 = VC.load_input("drums", nb * 32 / VC.SR, gain=2.0)
        mix1 = VC.load_input("mix", nb * 32 / VC.SR)
        loud = VC.load_input("drums", nb * 32 / VC.SR, gain=16.0, offset=0.9)
        cfgs = {
            "fallback (zero tails), self, MIX 64, drums x4": (dict(VC.BASE, **VC.NOFX, **VC.comp(30, 10, 30, 64, 5, 64)),
                                                              VC.ROUTE_IN, None, drums4, None),
            "fallback, FX on, key SCS 18, stock knobs moving (W5)": (dict(VC.BASE, **VC.FXW, **VC.comp(40, 10, 60, 90, 6, 100, scs=18)),
                                                                    VC.ROUTE_INFX, None, mix1, VC.w5_moves),
            "tails: starting point, LOW MUP 0 -> 127, MIX 127": (dict(VC.BASE, **VC.NOFX, **VC.comp(32, 24, 32, 64, 3, 127)),
                                                                VC.ROUTE_IN, sched_mup(0, nb), drums2, None),
            "tails: starting point, MID MUP 0 -> 127, MIX 127": (dict(VC.BASE, **VC.NOFX, **VC.comp(32, 24, 32, 64, 3, 127)),
                                                                VC.ROUTE_IN, sched_mup(1, nb), drums2, None),
            "tails: starting point, HIGH MUP 0 -> 127, MIX 127": (dict(VC.BASE, **VC.NOFX, **VC.comp(32, 24, 32, 64, 3, 127)),
                                                                 VC.ROUTE_IN, sched_mup(2, nb), drums2, None),
            "tails: all three MUP 0 -> 127, RAT 1.00 (off), drums x16, MIX 127": (
                dict(VC.BASE, **VC.NOFX, **VC.comp(32, 24, 32, 64, 3, 127)), VC.ROUTE_IN,
                sched_mup(0, nb, all_bands=True, rat=0), loud, None),
            "tails: all three MUP 0 -> 127, RAT 8, drums x16, FX on, MIX 64": (
                dict(VC.BASE, **VC.FXW, **VC.comp(32, 24, 32, 64, 3, 64)), VC.ROUTE_INFX,
                sched_mup(0, nb, all_bands=True, rat=8), loud, None),
            "tails: every band knob moving every block, key SCS 18, FX on": (
                dict(VC.BASE, **VC.FXW, **VC.comp(32, 24, 32, 64, 3, 64, scs=18)), VC.ROUTE_INFX, sched_moving(3), mix1, None),
            "tails: distinct bands, MIX 0": (dict(VC.BASE, **VC.NOFX, **VC.comp(32, 24, 32, 64, 3, 0)), VC.ROUTE_IN,
                                            V2.const_sched(V2.words_of(V2.knobs17(V2.DISTINCT))), drums4, None),
        }
        oks["a"], res["a_audio_vs_cmp2"] = proof_a(cfgs, img2, imgd)
    if "b" in only:
        oks["b"], res["b_readout"] = proof_b(imgd, 1200 if a.quick else 4600)
    if "c" in only:
        oks["c"], res["c_stores"] = proof_c(imgd, imgd[1])
    if "d" in only:
        oks["d"], res["d_stack_cost"] = proof_d(img2, imgd, 100 if a.quick else 400)
    res["ok"] = oks
    res["verdict"] = "PASS" if all(oks.values()) else "FAIL"
    res["seconds"] = round(time.time() - t_all, 1)
    res["peak_rss_mb"] = round(memguard.peak_rss_mb())
    OUT.mkdir(parents=True, exist_ok=True)
    name = "verify-quick.json" if a.quick else "verify.json"
    (OUT / name).write_text(json.dumps(res, indent=1, default=str))
    print("VERDICT", res["verdict"], oks, "%.0fs" % res["seconds"], "peak RSS %d MB" % res["peak_rss_mb"])


if __name__ == "__main__":
    main()
