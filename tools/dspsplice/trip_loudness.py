#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""T6.3 CMP1 proof: how loud the return after a guard trip is, CMP0 (before the fix) against CMP1. The CMP0
review's burst.py (out/T6.3/review-safety3), made general.

    python3 tools/dspsplice/trip_loudness.py [--workers 4]

Both images in sharcvm (the whole block handler, input into the compressor, no FX), five settings (the review's
three heavy ones, MIX 64 heavy, the typical MIX 39), four materials at half scale (mix, pad, chords, drums), 400
blocks. At block 200 one NaN in the compressor's input L (the guard trips at the end of that block: a silent
block), and on CMP1 also a NaN in band 0's attack envelope (found by the control block: the restart before the
block) and (CMP1b, the CMP1 review's p1_reinit_burst.py) the slot's magic set to 0 or to 0x12345678 (the quiet
re-init: CMP1 took 0 for a cold start, up to +18 dB). Measured on the handler output against the same image's run
without the injection, over the 100 ms after the trip block (4,800 samples per channel) and (CMP1b) over the
injection block and the 100 ms after it (4,832): the peak and the RMS (dBFS), their difference from the run
without the injection, and the largest jump of a 10 ms window's RMS; the pass rule takes the second window.
Writes out/T6.3/CMP1-dsp/trip_loudness.json.
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
AT, NB, WIN = 200, 400, 480
SETTINGS = {"THR 0, 20:1, ATK 127, REL 127, MUP 127, MIX 127": VC.comp(0, 127, 127, 127, 7, 127),
            "THR 32, 20:1, ATK 64, REL 100, MUP 100, MIX 127": VC.comp(32, 64, 100, 100, 7, 127),
            "new-kit defaults, MIX 127": VC.comp(32, 24, 32, 64, 3, 127),
            "heavy: THR 10, 20:1, ATK 0, REL 0, MUP 64, MIX 64": VC.HEAVY,
            "typical: new-kit defaults, MIX 39": VC.TYPICAL}
MATERIALS = ("mix", "pad", "chords", "drums")
G = {}


def init_worker(offs):
    for n in ("CMP0", "CMP1"):
        s, info, cache = VC.image(n)
        G[n] = (s, cache, offs[n])


def run(job):
    img, setting, material, kind = job
    stream, cache, off = G[img]
    L, R = VC.load_input(material, NB * 32 / VC.SR, gain=0.5)

    def inj(b, vm, ptrs):
        if b != AT or kind is None:
            return
        if kind == "NaN in comp in L":
            vm.wr32(ptrs[0] + 4 * 5, 0x7fc00000)
        elif kind == "NaN in band 0's attack envelope":
            vm.wr32(VC.SLOT + off["mb.band[0].y2"], 0x7fc00000)
        elif kind == "magic = 0":
            vm.wr32(VC.SLOT + off["magic"], 0)
        elif kind == "magic = 0x12345678":
            vm.wr32(VC.SLOT + off["magic"], 0x12345678)
    r = VC.Run(V.candidate_vm(stream, cache=cache), dict(VC.BASE, **VC.NOFX, **SETTINGS[setting]), VC.ROUTE_IN, keep=False)
    r.process(L, R, inject=inj)
    reinits = r.vm.rd32(VC.SLOT + off["reinits"]) if "reinits" in off else None
    return job, r.out, (r.vm.rd32(VC.SLOT + 16), reinits)


def db(x):
    return 20 * math.log10(max(x, 1e-12))


def stats(o, i0, n):
    xs = o[0][i0:i0 + n] + o[1][i0:i0 + n]
    return max(abs(x) for x in xs), math.sqrt(sum(x * x for x in xs) / len(xs))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args()
    VC.configure("CMP1")
    jobs = []
    for s in SETTINGS:
        for m in MATERIALS:
            for img, kinds in (("CMP0", (None, "NaN in comp in L")),
                               ("CMP1", (None, "NaN in comp in L", "NaN in band 0's attack envelope", "magic = 0",
                                         "magic = 0x12345678"))):
                for k in kinds:
                    jobs.append((img, s, m, k))
    t0 = time.time()
    outs = {}
    offs = {}
    for n in ("CMP0", "CMP1"):
        s_, info, cache = VC.image(n)
        V.candidate_vm(s_, cache=cache)          # the snapshot once, before the workers
        offs[n] = VC.offsets(VC.ROOT / VC.B.RECIPES[n]["outdir"] / "build", n)
    with ProcessPoolExecutor(a.workers, initializer=init_worker, initargs=(offs,)) as ex:
        for job, out, trips in ex.map(run, jobs):
            outs[job] = (out, trips)
    i0 = (AT + 1) * 32
    j0 = AT * 32                        # CMP1b: the injection block and the 100 ms after it
    LABELS = (("CMP0", "NaN in comp in L", "CMP0, guard trip"),
              ("CMP1", "NaN in comp in L", "CMP1, guard trip"),
              ("CMP1", "NaN in band 0's attack envelope", "CMP1, restart before the block"),
              ("CMP1", "magic = 0", "CMP1, re-init (magic = 0)"),
              ("CMP1", "magic = 0x12345678", "CMP1, re-init (magic = 0x12345678)"))
    rows = []
    for s in SETTINGS:
        for m in MATERIALS:
            ref0, _ = outs[("CMP0", s, m, None)]
            ref1, _ = outs[("CMP1", s, m, None)]
            same = all(struct.pack("<f", x) == struct.pack("<f", y) for ch in (0, 1) for x, y in zip(ref0[ch], ref1[ch]))
            pk_ref, rms_ref = stats(ref1, i0, 4800)
            row = {"setting": s, "material": m, "no-trip runs CMP0 == CMP1 bit for bit": same,
                   "no trip: peak dBFS": round(db(pk_ref), 2), "no trip: RMS dBFS": round(db(rms_ref), 2)}
            for img, kind, label in LABELS:
                o, (trips, reinits) = outs[(img, s, m, kind)]
                ref = ref0 if img == "CMP0" else ref1
                pk, rms = stats(o, i0, 4800)
                pkr, rmsr = stats(ref, i0, 4800)
                jump = max(db(stats(o, i0 + w * WIN, WIN)[1]) - db(stats(ref, i0 + w * WIN, WIN)[1]) for w in range(10))
                first10 = stats(o, i0, WIN)
                pk2, rms2 = stats(o, j0, 4832)
                pk2r, rms2r = stats(ref, j0, 4832)
                jump2 = max(db(stats(o, j0 + w * WIN, WIN)[1]) - db(stats(ref, j0 + w * WIN, WIN)[1]) for w in range(10))
                row[label] = {"trips": trips, "reinits": reinits, "peak dBFS": round(db(pk), 2), "RMS dBFS": round(db(rms), 2),
                              "peak dB vs no trip": round(db(pk) - db(pkr), 2),
                              "RMS dB vs no trip": round(db(rms) - db(rmsr), 2),
                              "largest 10 ms window jump dB": round(jump, 2),
                              "first 10 ms: peak / RMS dBFS": [round(db(first10[0]), 2), round(db(first10[1]), 2)],
                              "with the injection block: peak dB vs no trip": round(db(pk2) - db(pk2r), 2),
                              "with the injection block: RMS dB vs no trip": round(db(rms2) - db(rms2r), 2),
                              "with the injection block: largest 10 ms window jump dB": round(jump2, 2)}
            rows.append(row)
            print(s, m, {k: (v["with the injection block: peak dB vs no trip"],
                             v["with the injection block: RMS dB vs no trip"], v["largest 10 ms window jump dB"])
                         for k, v in row.items() if isinstance(v, dict)}, flush=True)
    summ = {}
    for _, _, label in LABELS:
        summ[label] = {"max peak dB vs no trip": max(r[label]["peak dB vs no trip"] for r in rows),
                       "max RMS dB vs no trip": max(r[label]["RMS dB vs no trip"] for r in rows),
                       "max 10 ms window jump dB": max(r[label]["largest 10 ms window jump dB"] for r in rows),
                       "max peak dBFS": max(r[label]["peak dBFS"] for r in rows),
                       "with the injection block: max peak dB vs no trip":
                           max(r[label]["with the injection block: peak dB vs no trip"] for r in rows),
                       "with the injection block: max RMS dB vs no trip":
                           max(r[label]["with the injection block: RMS dB vs no trip"] for r in rows),
                       "with the injection block: max 10 ms window jump dB":
                           max(r[label]["with the injection block: largest 10 ms window jump dB"] for r in rows),
                       "trips": sorted({r[label]["trips"] for r in rows}),
                       "reinits": sorted({r[label]["reinits"] for r in rows}, key=str)}
    summ["no-trip runs CMP0 == CMP1 bit for bit in every case"] = all(r["no-trip runs CMP0 == CMP1 bit for bit"] for r in rows)
    summ["cases"] = len(rows)
    summ["window"] = ("the 100 ms (4,800 samples per channel) after the trip block; and (CMP1b, 'with the injection "
                      "block') the injection block and the 100 ms after it; 10 ms windows for the jump")
    summ["seconds"] = round(time.time() - t0)
    summ["ok"] = (all(summ[lb]["with the injection block: max peak dB vs no trip"] <= 1.0
                      and summ[lb]["with the injection block: max RMS dB vs no trip"] <= 1.0
                      and summ[lb]["max peak dB vs no trip"] <= 1.0 and summ[lb]["max RMS dB vs no trip"] <= 1.0
                      for _, _, lb in LABELS[1:])
                  and summ["CMP1, re-init (magic = 0)"]["reinits"] == [1]
                  and summ["CMP1, re-init (magic = 0x12345678)"]["reinits"] == [1]
                  and summ["no-trip runs CMP0 == CMP1 bit for bit in every case"])
    (VC.OUT / "trip_loudness.json").write_text(json.dumps({"summary": summ, "rows": rows}, indent=1))
    print(json.dumps(summ, indent=1))
    return 0 if summ["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
