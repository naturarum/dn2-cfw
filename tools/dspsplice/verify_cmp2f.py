#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""T6.3 CMP2F: the third diagnostic build's checks in sharcvm (notes/T6.3-cmp2f.md). CMP2F = CMP2E with all of our code
out of 0x315f8c-0x318000 (cmp_tail.c after the glue in code4, cmp_load.c and cmp_clk.s in code3) and a memory scanner
(watermarked free L1 ranges, our own code and constant data per page) in the HIGH and right bars.

    python3 tools/dspsplice/verify_cmp2f.py [--quick] [--only a,p,s,c,d]

(a) audio: CMP2F against CMP2 (1a888114) on verify_cmp2e.py's 11 settings (the probe switch off), every sample.
(p) the probe switch, as verify_cmp2e.py (p).
(s) the scanner and the readout: the page table as built.json lists it (45 pages); 3,000 blocks with every band knob
    moving, FX and a key: 0 corrupted pages over every sweep, the breadcrumbs 1..16 (and 1..7, 10..16 with the key)
    and the heartbeat as on CMP2E; then a byte poked into watermark page 8 (0x316680), into a code page (mbcomp_gr_db,
    never called) and into watermark page 2: each found within one sweep, the HIGH bar 2 px -> 5 px (odd: a code page)
    -> 7 px, the right bar the lowest corrupted page (8, 8, 2 px), direct and through the stock getter.
(c) stores of the compressor call: only CmpSlot, CmpTail, CmpLoad (184 B), stack, save area, outputs, 0x257c1c, reply
    +0x10..+0x17 (the watermarks and the page table are never written).
(d) stack depth and instructions per block next to CMP2.
Results: out/T6.3/CMP2F-dsp/verify.json.
"""
import argparse
import gc
import json
import struct
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(HERE))
import verify_cmp2e as E2  # noqa: E402
import memguard  # noqa: E402

D, VC, V2, V = E2.D, E2.VC, E2.V2, E2.V
OUT = ROOT / "out/T6.3/CMP2F-dsp"
CL = 0x2e2800
FIELDS = ["hbq", "hb_n", "hb_ph", "prev_trips", "prev_reinits", "events", "od_bad", "w14_pad", "pt_n", "sc_page",
          "sc_addr", "sc_lo", "sc_hi", "sc_kind", "sc_a", "sc_b", "bad_lo", "bad_hi", "n_bad", "first_bad", "code_hit",
          "sweeps"] + D.FIELDS
CL_BYTES = 184
SLOT, TST = VC.SLOT, VC.TAILST[0]


def cl_read(vm):
    raw = vm.read(CL, 4 * len(FIELDS))
    out = {}
    for i, f in enumerate(FIELDS):
        w = raw[4 * i:4 * i + 4]
        out[f[:-2] if f.endswith(":f") else f] = struct.unpack("<f" if f.endswith(":f") else "<I", w)[0]
    return out


def q_px(n, mid=True):
    return 0 if n == 0 else n * 655 + 300


def proof_s(imgf, info, nb):
    t0 = time.time()
    pages = info["scan_pages"]
    vm = V.candidate_vm(imgf[0], cache=imgf[2])
    L, R = VC.looped(nb)
    run = V2.TailRun(vm, dict(VC.BASE, **VC.FXW, **VC.comp(32, 24, 32, 64, 3, 64, scs=18)), VC.ROUTE_INFX, E2.sched_moving(9))
    key_from = 1                                       # the key from the first block's frame (stock knobs one block late)
    pokes = {nb - 1300: (0x316680, "watermark page 8"), nb - 850: (0x3095ea, "code page (mbcomp_gr_db)"),
             nb - 400: (0x316010, "watermark page 2")}
    seen, problems, hb, rows = {}, [], [], {}
    WATCH = {2, 3, nb // 3, nb - 1301}
    for b in range(nb):
        if b in pokes:
            a, what = pokes[b]
            vm.write(a, bytes([vm.read(a, 1)[0] ^ 0x5a]))
        wl = [] if b in WATCH else None
        run.tblock(L[32 * b:32 * b + 32], R[32 * b:32 * b + 32], b, watch=wl)
        c = cl_read(vm)
        page = 0x2c49d0 + 0x1000 * (b & 1)
        other = 0x2c49d0 + 0x1000 * (1 - (b & 1))
        hw = [struct.unpack("<4H", vm.read(t + 0x10, 8)) for t in (page, other)]
        hb.append(hw[1][0])
        if hw[1][1] != 16 * 1965:
            problems.append("block %d: last crumb %d" % (b, hw[1][1]))
        want10 = 0 if c["n_bad"] == 0 else (2 * min(c["n_bad"], 24) + c["code_hit"]) * 655 + 300
        want11 = min(q_px(c["first_bad"]), 32760)
        if hw[1][2] != want10 or hw[0][2] != want10 or hw[1][3] != want11 or abs(hw[0][3] - want11) > 1:
            problems.append("block %d: reply %s, want %d/%d" % (b, hw, want10, want11))
        if wl is not None:
            ids = [va >> 16 for pc, ad, va in wl if ad == E2.TXW[0]]
            want = [1, 2, 3, 4, 5, 6, 7, 10, 11, 12, 13, 14, 15, 16] if b > key_from else list(range(1, 17))
            if [i // 1965 for i in ids] != want:
                problems.append("block %d: crumbs %s" % (b, [i // 1965 for i in ids]))
        key = (c["n_bad"], c["first_bad"], c["code_hit"])
        if key not in seen:
            seen[key] = b
            rows[b] = {"n_bad": c["n_bad"], "first_bad": c["first_bad"], "code_hit": c["code_hit"], "sweeps": c["sweeps"],
                       "bad_lo": "%#x" % c["bad_lo"], "bad_hi": "%#x" % c["bad_hi"],
                       "HIGH px": hw[1][2] // 655, "right px direct/getter": [hw[1][3] // 655, hw[0][3] // 655]}
        if len(problems) > 20:
            break
    c = cl_read(vm)
    first_hit = min((b for b, r in rows.items() if r["n_bad"]), default=None)
    found = {}
    for pb, (a, what) in sorted(pokes.items()):
        after = sorted(b for b, r in rows.items() if b >= pb)
        found[what] = {"poked_at_block": pb, "address": "%#x" % a,
                       "reported_at_block": after[0] if after else None,
                       "state": rows[after[0]] if after else None}
    flips = [b for b in range(1, len(hb)) if hb[b] != hb[b - 1]]
    expect = [(1, 8, 0, 2, 8), (2, 8, 1, 5, 8), (3, 2, 1, 7, 2)]
    got = [(r["state"]["n_bad"], r["state"]["first_bad"], r["state"]["code_hit"], r["state"]["HIGH px"],
            r["state"]["right px direct/getter"][0]) if r["state"] else None for r in found.values()]
    ok = (not problems and first_hit is not None and first_hit >= nb - 1300 and got == expect
          and all(r["reported_at_block"] - r["poked_at_block"] <= 420 for r in found.values())
          and all((f2 - f1) == 750 for f1, f2 in zip(flips, flips[1:])) and bool(flips))
    r = {"blocks": nb, "pages": len(pages), "problems": problems[:20], "ok": ok,
         "sweeps_before_the_first_poke": None, "first_report_block": first_hit, "pokes": found,
         "expected (n_bad, first_bad, code_hit, HIGH px, right px)": expect, "got": got,
         "heartbeat_flips": flips[:6], "final": c, "seconds": round(time.time() - t0, 1)}
    # sweeps completed with 0 corrupted pages: the count at the block before the first poke
    r["sweeps_before_the_first_poke"] = rows[max(b for b in rows if b < nb - 1300)]["sweeps"] if any(b < nb - 1300 for b in rows) else None
    print("(s) scanner", "ok" if ok else "PROBLEMS", "first report at block %s (first poke %d); got %s; %.0fs" % (
        first_hit, nb - 1300, got, time.time() - t0), flush=True)
    for x in problems[:6]:
        print("    ", x)
    return ok, r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--only", help="comma-separated proofs (a,p,s,c,d)")
    a = ap.parse_args()
    only = set(a.only.split(",")) if a.only else set("apscd")
    t_all = time.time()
    img2 = VC.image("CMP2")
    imgf = VC.image("CMP2F")
    info = imgf[1]
    V2.OFF.update(VC.offsets(OUT / "offsets", "CMP2", every=True))          # CmpSlot is CMP2's, field for field
    res = {"name": "CMP2F", "section7_sha256": info["section7"]["sha256"], "cmp2_section7_sha256": img2[1]["section7"]["sha256"],
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
                dict(VC.BASE, **VC.FXW, **VC.comp(32, 24, 32, 64, 3, 127)), VC.ROUTE_INFX, E2.poweron_mup(nb), busy, None),
            "power-on state, LOW MUP 0 -> 127, self, MIX 64, drums x4": (
                dict(VC.BASE, **VC.NOFX, **VC.comp(32, 24, 32, 64, 3, 64)), VC.ROUTE_IN, E2.poweron_mup(nb), drums4, None),
            "power-on state, LOW MUP 40, LOW ATK and REL moving, MIX 127": (
                dict(VC.BASE, **VC.NOFX, **VC.comp(32, 24, 32, 64, 3, 127)), VC.ROUTE_IN, E2.poweron_atkrel(nb), busy, None),
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
                dict(VC.BASE, **VC.FXW, **VC.comp(32, 24, 32, 64, 3, 64, scs=18)), VC.ROUTE_INFX, E2.sched_moving(3), mix1, None),
            "tails: distinct bands, MIX 0": (dict(VC.BASE, **VC.NOFX, **VC.comp(32, 24, 32, 64, 3, 0)), VC.ROUTE_IN,
                                            V2.const_sched(V2.words_of(V2.knobs17(V2.DISTINCT))), drums4, None),
        }
        oks["a"], res["a_audio_vs_cmp2"] = D.proof_a(cfgs, img2, imgf)
    if "p" in only:
        oks["p"], res["p_probe_switch"] = E2.proof_p(img2, imgf, 300 if a.quick else 600)
    if "s" in only:
        oks["s"], res["s_scanner"] = proof_s(imgf, info, 1800 if a.quick else 3000)
    if "c" in only:
        E2.CL_BYTES = CL_BYTES
        oks["c"], res["c_stores"] = E2.proof_c(imgf)
    if "d" in only:
        oks["d"], dd = D.proof_d(img2, imgf, 100 if a.quick else 400)
        dd["CMP2F"] = dd.pop("CMP2D")
        res["d_stack_cost"] = dd
    res["page_table"] = info["scan_pages"]
    res["ok"] = oks
    res["verdict"] = "PASS" if all(oks.values()) else "FAIL"
    res["seconds"] = round(time.time() - t_all, 1)
    res["peak_rss_mb"] = round(memguard.peak_rss_mb())
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / ("verify-quick.json" if a.quick else "verify.json")).write_text(json.dumps(res, indent=1, default=str))
    print("VERDICT", res["verdict"], oks, "%.0fs" % res["seconds"], "peak RSS %d MB" % res["peak_rss_mb"])


if __name__ == "__main__":
    main()
