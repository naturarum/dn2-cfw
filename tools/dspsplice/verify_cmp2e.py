#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""T6.3 CMP2E: the second diagnostic build's checks in sharcvm (notes/T6.3-cmp2e.md). CMP2E = CMP2D with breadcrumbs, a
heartbeat, a trip/re-init counter, the master-latched flag and the LOW MUP probe switch (src/cmp2e/).

    python3 tools/dspsplice/verify_cmp2e.py [--quick] [--only a,p,b,c,d]

(a) audio: CMP2E against CMP2 (1a888114) as verify_cmp2d.py (a), with the LOW XOVR word kept under 126 << 8 (the
    probe switch off), plus the power-on state (every band word 0) with LOW MUP 0 -> 127 while playing, and LOW ATK
    and REL moving from the power-on state with LOW MUP up.
(p) the probe switch: LOW XOVR glides to 127 and back while LOW MUP turns: CMP2E against CMP2 fed the same tails but
    with word 6 (LOW MUP) held at the value CMP2E last applied while the XOVR word is >= 0x7e00: every sample
    bit-identical; band 0's MUP knob stays put while the switch is on and follows again after.
(b) the readout: every block the breadcrumb stores of the compressor call, in order (both pages, the low half the
    heartbeat): 1..16 with self-detection, 1..7, 10..16 with a key; between crumbs 7/8/9 the band's level stores, between
    10/11/12 its gain stores, between 13 and 14 the output stores (the crumbs are not batched); ids 7..9 and 10..12
    each from one PC (the band loops are shared, not unrolled); the heartbeat toggling every 750 blocks; a NaN input
    (a guard trip) -> half-word 10 = 4 px, a corrupted magic (a quiet re-init) -> 8 px, a NaN in the master overdrive
    state -> 50 px; half-word 11 direct and through the stock getter.
(c) stores of the compressor call: CmpSlot, CmpTail, CmpLoad (128 B), stack, save area, outputs, 0x257c1c, reply
    +0x10..+0x17 of both pages only.
(d) stack depth and instructions per block next to CMP2.
Results: out/T6.3/CMP2E-dsp/verify.json.
"""
import argparse
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
import verify_cmp2d as D  # noqa: E402     (verify_cmp, verify_cmp2: TailRun, the tail helpers)
import memguard  # noqa: E402

VC, V2, V = D.VC, D.V2, D.V
OUT = ROOT / "out/T6.3/CMP2E-dsp"
CL = 0x2e2800
FIELDS = ["hbq", "hb_n", "hb_ph", "prev_trips", "prev_reinits", "events", "od_bad", "w14_pad"] + D.FIELDS
CL_BYTES = 128
TXW = (0x2c49e0, 0x2c59e0)
SLOT, TST = VC.SLOT, VC.TAILST[0]
XLO_MAX_OFF = 125                                # keep the probe switch off in (a)


def cl_read(vm):
    raw = vm.read(CL, 4 * len(FIELDS))
    out = {}
    for i, f in enumerate(FIELDS):
        w = raw[4 * i:4 * i + 4]
        out[f[:-2] if f.endswith(":f") else f] = struct.unpack("<f" if f.endswith(":f") else "<I", w)[0]
    return out


def sched_vals(fn):
    return lambda b: V2.tail_words(1 + b, V2.words_of(fn(b)))


def sched_moving(seed):
    rnd = random.Random(seed)
    cache = {}

    def vals(b):
        if b not in cache:
            bands = [(rnd.randrange(128), rnd.randrange(9), rnd.randrange(128), rnd.randrange(128), rnd.randrange(128))
                     for _ in range(3)]
            cache[b] = V2.knobs17((bands, (rnd.randrange(XLO_MAX_OFF + 1), rnd.randrange(128))))
        return cache[b]
    return sched_vals(vals)


def poweron_mup(nb):
    """every band word 0 (the unit after power-on), LOW MUP 0 -> 127 over the first two thirds"""
    def vals(b):
        v = [0.0] * 17
        v[4] = min(127.0, 127.0 * b / max(1, (2 * nb) // 3))
        return v
    return sched_vals(vals)


def poweron_atkrel(nb, seed=5):
    """the power-on state with LOW MUP at 40, LOW ATK and REL jumping every 8 blocks, then gliding"""
    rnd = random.Random(seed)
    steps = [(rnd.randrange(128), rnd.randrange(128)) for _ in range(nb // 8 + 1)]

    def vals(b):
        v = [0.0] * 17
        v[4] = 40.0
        a, r = steps[b // 8]
        if b > nb // 2:
            a, r = 127.0 * ((b * 3) % 97) / 96.0, 127.0 * ((b * 5) % 89) / 88.0
        v[2], v[3] = a, r
        return v
    return sched_vals(vals)


def probe_words(nb):
    """(p): per block the 17 tail values for CMP2E (the switch in play) and for CMP2 (word 6 held as CMP2E holds it)"""
    xlo, mup = [], []
    for b in range(nb):
        if b < 100:
            x = 68.0
        elif b < 200:
            x = 68.0 + (127.0 - 68.0) * (b - 100) / 100.0
        elif b < 500:
            x = 127.0
        else:
            x = 68.0
        xlo.append(x)
        mup.append(min(127.0, max(0.0, 127.0 * (b - 150) / 300.0)))
    e, c = [], []
    held = 0.0
    for b in range(nb):
        v = list(V2.knobs17(([(64, 4, 24, 32, 0)] * 3, (68, 44))))
        v[5] = xlo[b]
        v[4] = mup[b]
        e.append(list(v))
        if int(round(xlo[b] * 256)) & 0xffff < 0x7e00:
            held = mup[b]
        v[4] = held
        c.append(v)
    return e, c


def crumbs_of(log):
    """the breadcrumb stores in a watch log: [(pc, page, id, low half)]"""
    out = []
    for pc, ad, va in log:
        if ad in TXW:
            out.append((pc, TXW.index(ad), va >> 16, va & 0xffff))
    return out


def proof_p(img2, imge, nb):
    t0 = time.time()
    ew, cw = probe_words(nb)
    L, R = VC.looped(nb)
    words = dict(VC.BASE, **VC.NOFX, **VC.comp(32, 24, 32, 64, 3, 127))
    se = lambda b: V2.tail_words(1 + b, V2.words_of(ew[b]))         # noqa: E731
    sc = lambda b: V2.tail_words(1 + b, V2.words_of(cw[b]))         # noqa: E731
    oc, gc_, _, _, _ = D.run_cfg(V.candidate_vm(img2[0], cache=img2[2]), words, VC.ROUTE_IN, sc, L, R, keep_pages=False)
    gc.collect()
    vm = V.candidate_vm(imge[0], cache=imge[2])
    run = V2.TailRun(vm, words, VC.ROUTE_IN, se)
    knob = []
    for b in range(nb):
        run.tblock(L[32 * b:32 * b + 32], R[32 * b:32 * b + 32], b)
        knob.append(vm.rdf(SLOT + V2.OFF["mb.knob[3]"], 1)[0])   # MB_P(0, MB_MUP) = 3
    oe = run.out
    n = len(oc[0]) + len(oc[1])
    same = sum(1 for a, b in zip(oc[0] + oc[1], oe[0] + oe[1]) if struct.pack("<f", a) == struct.pack("<f", b))
    on = [b for b in range(nb) if (int(round(ew[b][5] * 256)) & 0xffff) >= 0x7e00]
    r = {"blocks": nb, "switch_on_blocks": [on[0], on[-1]] if on else None, "samples": n, "samples_identical": same,
         "low_mup_knob": {"before (block 149)": knob[149], "first switch-on block": knob[on[0]] if on else None,
                          "last switch-on block": knob[on[-1]] if on else None, "tail word 6 then (knob)": ew[on[-1]][4] if on else None,
                          "after (block %d)" % (nb - 1): knob[nb - 1]},
         "gain_reduction_blocks_identical": sum(1 for a, b in zip(gc_, run.gr) if a == b),
         "seconds": round(time.time() - t0, 1)}
    # while the switch is on band 0's MUP knob stays put; after it (blocks 500.., full run only) it follows again
    held_ok = bool(on) and all(knob[b] == knob[on[0]] for b in on) and knob[on[0]] == round(cw[on[0]][4] * 256) / 256.0
    if nb > 500:
        held_ok = held_ok and abs(knob[nb - 1] - ew[nb - 1][4]) < 1e-3 and knob[nb - 1] != knob[on[0]]
    r["ok"] = same == n and held_ok
    print("(p) probe switch", "ok" if r["ok"] else "PROBLEM", "%d/%d samples, knob %s, %.0fs" % (
        same, n, r["low_mup_knob"], time.time() - t0), flush=True)
    return r["ok"], r


def proof_b(imge, nb):
    t0 = time.time()
    vm = V.candidate_vm(imge[0], cache=imge[2])
    P = 666667
    E = [0x10000000 + P * b for b in range(nb)]
    L, R = VC.looped(nb)
    trip_at, reinit_at, od_at, key_from = 300, 500, 900, 1200
    words = dict(VC.BASE, **VC.NOFX, **VC.comp(32, 24, 32, 64, 3, 127))
    run = V2.TailRun(vm, words, VC.ROUTE_IN, V2.const_sched(V2.words_of(V2.knobs17(V2.DISTINCT))))
    bad, nbad = [], 0
    seqs, pcs, interleave = {}, {}, {}
    hb, q10s, getter = [], [], []
    WATCH = {0, 1, 2, 3, 299, 300, 301, 500, 899, 900, 901, 1200, 1201, nb - 1}

    def nan_in(b, vm_, ptrs):
        if b == trip_at:
            vm_.wrf(ptrs[0] + 4 * 7, [float("nan")])
    for b in range(nb):
        vm.set_reg("EMUCLK", E[b])
        if b == reinit_at:
            vm.wr32(SLOT, 0)                                # a corrupted magic: the quiet re-init
        if b == od_at:
            vm.wrf(0x257d08, [float("nan")])                # the master overdrive state latched
        if b == key_from:
            V2.VC.put(run.r.d.frame, "CMP_SCS", 18 << 8)   # a key from here on (one detector loop)
            run.r.d.set_raw(CMP_TRACKS=0, CMP_IN_FX=0x5)
        wl = [] if b in WATCH else None
        run.tblock(L[32 * b:32 * b + 32], R[32 * b:32 * b + 32], b, inject=nan_in, watch=wl)
        c = cl_read(vm)
        page = 0x2c49d0 + 0x1000 * (b & 1)
        other = 0x2c49d0 + 0x1000 * (1 - (b & 1))
        hw = [struct.unpack("<4H", vm.read(t + 0x10, 8)) for t in (page, other)]
        hb.append(hw[1][0])
        q10s.append(hw[1][2])
        getter.append(hw[0][3] - hw[1][3])
        why = []
        if hw[0][:3] != hw[1][:3]:
            why.append("pages differ %s" % hw)
        if hw[1][1] != 16 * 1965:
            why.append("last crumb %d" % hw[1][1])
        if hw[1][0] != c["hbq"]:
            why.append("half-word 8 %d, hbq %d" % (hw[1][0], c["hbq"]))
        if abs(getter[-1]) > 1:
            why.append("getter %d vs %d" % (hw[0][3], hw[1][3]))
        if wl is not None:
            cr = crumbs_of(wl)
            ids = [x[2] // 1965 for x in cr if x[1] == 0]
            seqs[b] = ids
            # the stock knobs (SCS) reach the compressor through the block-old frame image: the key from key_from + 1
            want = list(range(1, 17)) if b <= key_from else [1, 2, 3, 4, 5, 6, 7, 10, 11, 12, 13, 14, 15, 16]
            if b == trip_at:
                want = list(range(1, 15)) + [14, 15, 16]      # the restart after the guard: crumb 14 again
            if ids != want:
                why.append("crumbs %s, want %s" % (ids, want))
            if [x[2] for x in cr if x[1] == 1] != [x[2] for x in cr if x[1] == 0] or any(x[2] % 1965 for x in cr):
                why.append("page 1's crumbs differ or not multiples of 1965")
            for x in cr:
                if x[1] == 0:                               # page 0's store of each crumb site
                    pcs.setdefault(x[2] // 1965, set()).add(x[0])
            # the stores between crumbs: the band's levels (7..9), its gains (10..12), the outputs (13..14)
            gbase = SLOT + V2.OFF["mb.buf_g[0][0]"]
            cur, cnt = None, {}
            for pc, ad, va in wl:
                if ad in TXW:
                    cur = va >> 16
                    continue
                if cur is None:
                    continue
                k = cur // 1965
                if gbase <= ad < gbase + 3 * 128:
                    cnt.setdefault(k, {}).setdefault("buf_g[%d]" % ((ad - gbase) // 128), 0)
                    cnt[k]["buf_g[%d]" % ((ad - gbase) // 128)] += 1
                elif 0x25ba68 <= ad < 0x25bb68:
                    cnt.setdefault(k, {}).setdefault("out", 0)
                    cnt[k]["out"] += 1
            interleave[b] = cnt
        if why:
            nbad += 1
            if len(bad) < 20:
                bad.append("block %d: %s" % (b, "; ".join(why)))
    # the heartbeat: 6550 / 13100, switching every 750 blocks
    flips = [b for b in range(1, nb) if hb[b] != hb[b - 1]]
    hb_ok = set(hb) == {6550, 13100} and all((f2 - f1) == 750 for f1, f2 in zip(flips, flips[1:])) and bool(flips)
    q10_at = {"before %d" % trip_at: q10s[trip_at - 1], "after the trip": q10s[trip_at + 1],
              "after the re-init": q10s[reinit_at + 1], "after the master NaN": q10s[od_at + 1], "end": q10s[-1]}
    q10_ok = (q10s[trip_at - 1] == 0 and q10s[trip_at + 1] == 2620 and q10s[reinit_at + 1] == 5240
              and q10s[od_at + 1] == 32767 and q10s[-1] == 32767)
    il = interleave.get(2, {})
    il_ok = (il.get(7, {}).get("buf_g[0]") == 32 and il.get(8, {}).get("buf_g[1]") == 32 and il.get(9, {}).get("buf_g[2]") == 32
             and il.get(10, {}).get("buf_g[0]") == 32 and il.get(11, {}).get("buf_g[1]") == 32
             and il.get(12, {}).get("buf_g[2]") == 32 and il.get(13, {}).get("out") == 64)
    shared = {"detector ids 7..9 PCs": sorted({"%#x" % p for i in (7, 8, 9) for p in pcs.get(i, ())}),
              "gain ids 10..12 PCs": sorted({"%#x" % p for i in (10, 11, 12) for p in pcs.get(i, ())})}
    # one site for the band loop's ids 7..9 (+ the key detector's 7), one for 10..12: the loops are shared
    shared_ok = len({p for i in (7, 8, 9) for p in pcs.get(i, ())}) == 2 and len({p for i in (10, 11, 12) for p in pcs.get(i, ())}) == 1
    final = cl_read(vm)
    r = {"blocks": nb, "problems": bad, "blocks_with_problems": nbad, "crumb_sequences": {str(k): v for k, v in seqs.items()},
         "stores_between_crumbs_block2": {str(k): v for k, v in il.items()}, "interleave_ok": il_ok,
         "crumb_pcs": {str(k): sorted("%#x" % p for p in v) for k, v in sorted(pcs.items())},
         "band_loops_shared": shared, "shared_ok": shared_ok,
         "heartbeat_flips": flips[:6], "heartbeat_ok": hb_ok, "half_word_10": q10_at, "q10_ok": q10_ok,
         "getter_minus_direct": [min(getter), max(getter)], "final": final, "seconds": round(time.time() - t0, 1)}
    r["ok"] = not bad and hb_ok and q10_ok and il_ok and shared_ok
    print("(b) readout", "ok" if r["ok"] else "PROBLEMS", "heartbeat flips %s, q10 %s, interleave %s, shared %s, %.0fs" % (
        flips[:4], q10_at, il_ok, shared, time.time() - t0), flush=True)
    for x in bad[:8]:
        print("    ", x)
    return r["ok"], r


def proof_c(imge):
    allowed = [(V.STACK_LO, V.STACK_TOP, "stack"), (SLOT, SLOT + 1840, "CmpSlot"), (TST, TST + 208, "CmpTail"),
               (CL, CL + CL_BYTES, "CmpLoad"), (0x2e6800, 0x2e6800 + VC.B.stub.SAVE_BYTES, "stub save area"),
               (0x25ba68, 0x25ba68 + 0x80, "out L"), (0x25bae8, 0x25bae8 + 0x80, "out R"),
               (VC.GR_Y2, VC.GR_Y2 + 4, "0x257c1c meter"),
               (VC.TX[0] + 0x10, VC.TX[0] + 0x18, "reply page 0 +0x10..+0x17"),
               (VC.TX[1] + 0x10, VC.TX[1] + 0x18, "reply page 1 +0x10..+0x17")]
    L, R = VC.load_input("mix", 70 * 32 / VC.SR)
    census = {}
    for label, sched in (("tail mode, every knob moving", sched_moving(7)), ("power-on state, LOW MUP turning", poweron_mup(60)),
                         ("first tail after the fallback (block 3)",
                          lambda b: V2.tail_words(b, V2.words_of(V2.knobs17(V2.DISTINCT))) if b >= 3 else None)):
        run = V2.TailRun(V.candidate_vm(imge[0], cache=imge[2]), dict(VC.BASE, **VC.FXW, **VC.comp(32, 24, 32, 64, 3, 64)),
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--only", help="comma-separated proofs (a,p,b,c,d)")
    a = ap.parse_args()
    only = set(a.only.split(",")) if a.only else set("apbcd")
    t_all = time.time()
    img2 = VC.image("CMP2")
    imge = VC.image("CMP2E")
    V2.OFF.update(VC.offsets(OUT / "offsets", "CMP2", every=True))          # CmpSlot is CMP2's, field for field
    res = {"name": "CMP2E", "section7_sha256": imge[1]["section7"]["sha256"], "cmp2_section7_sha256": img2[1]["section7"]["sha256"],
           "proofs_run": "".join(sorted(only))}
    oks = {}
    if "a" in only:
        nb = 200 if a.quick else 600
        drums4 = VC.load_input("drums", nb * 32 / VC.SR, gain=4.0)
        drums2 = VC.load_input("drums", nb * 32 / VC.SR, gain=2.0)
        mix1 = VC.load_input("mix", nb * 32 / VC.SR)
        loud = VC.load_input("drums", nb * 32 / VC.SR, gain=16.0, offset=0.9)
        busy = VC.looped(nb)
        cfgs = {
            "fallback (zero tails), self, MIX 64, drums x4": (dict(VC.BASE, **VC.NOFX, **VC.comp(30, 10, 30, 64, 5, 64)),
                                                              VC.ROUTE_IN, None, drums4, None),
            "fallback, FX on, key SCS 18, stock knobs moving (W5)": (dict(VC.BASE, **VC.FXW, **VC.comp(40, 10, 60, 90, 6, 100, scs=18)),
                                                                    VC.ROUTE_INFX, None, mix1, VC.w5_moves),
            "power-on state (every band word 0), LOW MUP 0 -> 127, FX on, MIX 127": (
                dict(VC.BASE, **VC.FXW, **VC.comp(32, 24, 32, 64, 3, 127)), VC.ROUTE_INFX, poweron_mup(nb), busy, None),
            "power-on state, LOW MUP 0 -> 127, self, MIX 64, drums x4": (
                dict(VC.BASE, **VC.NOFX, **VC.comp(32, 24, 32, 64, 3, 64)), VC.ROUTE_IN, poweron_mup(nb), drums4, None),
            "power-on state, LOW MUP 40, LOW ATK and REL moving, MIX 127": (
                dict(VC.BASE, **VC.NOFX, **VC.comp(32, 24, 32, 64, 3, 127)), VC.ROUTE_IN, poweron_atkrel(nb), busy, None),
            "tails: starting point, LOW MUP 0 -> 127, MIX 127": (dict(VC.BASE, **VC.NOFX, **VC.comp(32, 24, 32, 64, 3, 127)),
                                                                VC.ROUTE_IN, D.sched_mup(0, nb), drums2, None),
            "tails: starting point, MID MUP 0 -> 127, MIX 127": (dict(VC.BASE, **VC.NOFX, **VC.comp(32, 24, 32, 64, 3, 127)),
                                                                VC.ROUTE_IN, D.sched_mup(1, nb), drums2, None),
            "tails: starting point, HIGH MUP 0 -> 127, MIX 127": (dict(VC.BASE, **VC.NOFX, **VC.comp(32, 24, 32, 64, 3, 127)),
                                                                 VC.ROUTE_IN, D.sched_mup(2, nb), drums2, None),
            "tails: all three MUP 0 -> 127, RAT 8, drums x16, FX on, MIX 64": (
                dict(VC.BASE, **VC.FXW, **VC.comp(32, 24, 32, 64, 3, 64)), VC.ROUTE_INFX,
                D.sched_mup(0, nb, all_bands=True, rat=8), loud, None),
            "tails: every band knob moving every block (LOW XOVR <= 125), key SCS 18, FX on": (
                dict(VC.BASE, **VC.FXW, **VC.comp(32, 24, 32, 64, 3, 64, scs=18)), VC.ROUTE_INFX, sched_moving(3), mix1, None),
            "tails: distinct bands, MIX 0": (dict(VC.BASE, **VC.NOFX, **VC.comp(32, 24, 32, 64, 3, 0)), VC.ROUTE_IN,
                                            V2.const_sched(V2.words_of(V2.knobs17(V2.DISTINCT))), drums4, None),
        }
        oks["a"], res["a_audio_vs_cmp2"] = D.proof_a(cfgs, img2, imge)
    if "p" in only:
        oks["p"], res["p_probe_switch"] = proof_p(img2, imge, 300 if a.quick else 600)
    if "b" in only:
        oks["b"], res["b_readout"] = proof_b(imge, 1700 if a.quick else 3200)
    if "c" in only:
        oks["c"], res["c_stores"] = proof_c(imge)
    if "d" in only:
        oks["d"], dd = D.proof_d(img2, imge, 100 if a.quick else 400)
        dd["CMP2E"] = dd.pop("CMP2D")                       # verify_cmp2d's label for the second image
        res["d_stack_cost"] = dd
    res["ok"] = oks
    res["verdict"] = "PASS" if all(oks.values()) else "FAIL"
    res["seconds"] = round(time.time() - t_all, 1)
    res["peak_rss_mb"] = round(memguard.peak_rss_mb())
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / ("verify-quick.json" if a.quick else "verify.json")).write_text(json.dumps(res, indent=1, default=str))
    print("VERDICT", res["verdict"], oks, "%.0fs" % res["seconds"], "peak RSS %d MB" % res["peak_rss_mb"])


if __name__ == "__main__":
    main()
