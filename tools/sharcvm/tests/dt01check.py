#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""dt01check.py - DT01's flanger (tools/dspsplice/src/dt01/flanger.c: the rings last in the struct, FL_GLIDE a function,
the undenormal step read and written once) against FL02's (fx/flanger.c), both with FL02's glue (flg_glue3.c,
flg_sync.c). notes/DT01-code-diet.md.

    python3 tools/sharcvm/tests/dt01check.py [--seconds 4] [--grid] [--out out/DT01/dt01check.json]

fl02check.py's harness and cases (harness/flgdrv.c with the glue, built with selcc -O1 for this interpreter and with
clang -O2 -ffp-contract=off native, -DFLG_NATIVE; the parameter floats as the unit's unpack makes them; the SPD word
through the ColdFire's smoother model where it glides), run on both flangers:
  - free: flgcheck's five settings, every knob moving, SPD swept 0..127, SPD glided onto the anchors, the tempo walking;
  - synced: every division at 60, 120, 137.5 and 174 BPM, and 16/1, 1/4, 1/32 at 20, 30, 300 and 400;
  - transitions through the smoother model (fl02check's plan);
each case: DT01 = FL02 sample for sample (selcc), DT01 selcc = DT01 native, and at the end the whole slot the same
(FlgSlot's header, the Flanger fields and the rings: DT01 has the rings after the fields, so its fields are compared
with FL02's at their own offsets, clang's offsetof); instructions per block (mean, max) of both. With --grid
fl02check's grid on both (DPTH/SPD/WDTH 0, 64, 127, C 0..127, and every division at 120 BPM): worst block and
highest mean. Results: out/DT01/dt01check.json.
"""
import argparse
import json
import math
import random
import struct
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
sys.dont_write_bytecode = True
import fl02check as F2  # noqa: E402
from fxcheck import globalize  # noqa: E402
from render_fx import test_signal  # noqa: E402
import memguard  # noqa: E402

SEL = ROOT / "vendor/selache/target/release"
SRC = ROOT / "tools/dspsplice/src"
VARIANTS = {"FL02": (ROOT / "fx/flanger.c", [ROOT / "fx", SRC]),
            "DT01": (SRC / "dt01/flanger.c", [SRC / "dt01", ROOT / "fx", SRC])}
GLUE = [SRC / "flg_glue3.c", SRC / "flg_sync.c"]
MAGIC = 0x464c4733
OUT = ROOT / "out/DT01/dt01check"


def build(name, native):
    fl, inc = VARIANTS[name]
    d = OUT / f"{name}{'n' if native else 's'}"
    d.mkdir(parents=True, exist_ok=True)
    flags = ["-proc", "ADSP-21569", "-char-size-8", "-O1"] + [f"-I{i}" for i in inc] + [
        f"-I{ROOT / 'vendor/selache/libsel/include'}"]
    extra = ["-DFLG_NATIVE"] if native else []
    objs = []
    srcs = [("flgdrv", HERE.parent / "harness/flgdrv.c", ["-D__SHARC_HARNESS__"], None)]
    srcs += [(p.stem, p, extra, "G%d" % i) for i, p in enumerate(GLUE)] + [("flanger", fl, [], "F")]
    for stem, path, ex, pre in srcs:
        s = d / f"{stem}.s"
        subprocess.check_call([str(SEL / "selcc"), *flags, *ex, "-S", "-o", str(s), str(path)])
        if pre:
            globalize(s, pre + "x_")
        subprocess.check_call([str(SEL / "selas"), "-proc", "ADSP-21569", "-o", str(d / f"{stem}.doj"), str(s)])
        objs.append(str(d / f"{stem}.doj"))
    subprocess.check_call([str(SEL / "seld"), "-proc", "ADSP-21569", "-T", str(ROOT / "vendor/selache/libsel/link.ldf"),
                           "-o", str(d / "flg.dxe"), *objs, str(ROOT / "out/T3.6/libsel/libsel.dlb")])
    if native:
        subprocess.check_call(["clang", "-O2", "-ffp-contract=off", "-fno-strict-aliasing", "-shared", "-fPIC",
                               "-DFLG_NATIVE", *[f"-I{i}" for i in inc], "-o", str(d / "libflg.dylib"),
                               str(HERE.parent / "harness/flgdrv.c"), *map(str, GLUE), str(fl)])
    return d


def layout(name):
    """FlgSlot / Flanger offsets (clang, the same 4-byte fields as the SHARC build) of a variant"""
    fl, inc = VARIANTS[name]
    d = OUT / f"{name}-layout"
    d.mkdir(parents=True, exist_ok=True)
    c = d / "lay.c"
    c.write_text('#include <stdio.h>\n#include <stddef.h>\n#include "flg_slot3.h"\nint main(void){printf("%d %d %d %d %d %d %d %d\\n",'
                 '(int)offsetof(FlgSlot, fl), (int)offsetof(Flanger, ring), (int)sizeof(Flanger), (int)sizeof(FlgSlot),'
                 '(int)offsetof(Flanger, rate), (int)offsetof(Flanger, ph), (int)offsetof(Flanger, bpm),'
                 '(int)offsetof(Flanger, knob));return 0;}\n')
    subprocess.check_call(["clang", "-O0", *[f"-I{i}" for i in inc], "-o", str(d / "lay"), str(c)])
    v = list(map(int, subprocess.check_output([str(d / "lay")]).split()))
    return dict(fl=v[0], ring=v[1], flanger=v[2], slot=v[3], rate=v[0] + v[4], ph=v[0] + v[5], bpm=v[0] + v[6],
                knob=v[0] + v[7])


def slot_map(lay, slot_bytes):
    """the variant's slot bytes rearranged into FL02's order (rings first in the Flanger)"""
    fl, ring, n = lay["fl"], lay["ring"], lay["flanger"]
    if ring == 0:
        return slot_bytes
    head, f = slot_bytes[:fl], slot_bytes[fl:fl + n]
    rings = f[ring:]
    return head + rings + f[:ring] + slot_bytes[fl + n:]


def slot_read(sh, lay):
    return sh.vm.read(sh.slot, lay["slot"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=4.0)
    ap.add_argument("--grid", action="store_true")
    ap.add_argument("--out", default=str(ROOT / "out/DT01/dt01check.json"))
    a = ap.parse_args()
    global OUT
    OUT = Path(a.out).resolve().with_suffix("")
    lay = {n: layout(n) for n in VARIANTS}
    if lay["FL02"]["ring"] != 0 or lay["DT01"]["slot"] != lay["FL02"]["slot"]:
        raise SystemExit("unexpected layouts: %s" % lay)
    # the slot a static of the glue (-DFLG_NATIVE) in both builds, as fl02check runs them (the DSP's 0x300000 is the
    # harness's code)
    built = {n: (build(n, True),) * 2 for n in VARIANTS}
    res = {"layouts": lay, "free": {}, "synced": {}, "transitions": {}}
    L, R = test_signal(a.seconds)
    blocks = len(L) // 32
    all_ok = True

    def pair(cname, Lc, Rc, script, group):
        """both variants (selcc), DT01 native, on one script: outputs, the end state, instructions"""
        nonlocal all_ok
        outs, pb, st = {}, {}, {}
        for n in VARIANTS:
            sh = F2.Sharc(built[n][0], MAGIC)
            outs[n] = F2.run(sh, Lc, Rc, script)
            pb[n] = sh.per_block
            st[n] = slot_map(lay[n], slot_read(sh, lay[n]))
            del sh
            memguard.check()
        nat = F2.Native(built["DT01"][1])
        nL, nR = F2.run(nat, Lc, Rc, script)
        a_, b_ = outs["FL02"][0] + outs["FL02"][1], outs["DT01"][0] + outs["DT01"][1]
        e = {"dt01_vs_fl02_identical": "%d/%d" % (F2.same_count(a_, b_), len(a_)),
             "dt01_selcc_vs_native_identical": "%d/%d" % (F2.same_count(b_, nL + nR), len(b_)),
             "slot_identical_at_end": st["FL02"] == st["DT01"],
             "insn_mean": {n: round(sum(p[1:]) / (len(p) - 1), 1) for n, p in pb.items()},
             "insn_max": {n: max(p[1:]) for n, p in pb.items()}}
        e["ok"] = (F2.bits(a_) == F2.bits(b_) and F2.bits(b_) == F2.bits(nL + nR) and e["slot_identical_at_end"]
                   and all(math.isfinite(x) for x in b_))
        all_ok &= e["ok"]
        res[group][cname] = e
        print("%-12s %-60s %s  max %s  mean %s" % (group, cname[:60], "ok" if e["ok"] else "DIFFERS", e["insn_max"],
                                                     e["insn_mean"]), flush=True)
        return e

    # free
    scripts, _ = F2.free_scripts(blocks)
    for cname, sc in scripts.items():
        pair(cname, L, R, sc, "free")
    # synced
    nb = 3600
    zL, zR = L[:nb * 32], R[:nb * 32]
    for bpm in (60.0, 120.0, 137.5, 174.0, 30.0, 300.0, 20.0, 400.0):
        for i, sw in enumerate(F2.SYNC, 1):
            if bpm not in (60.0, 120.0, 137.5, 174.0) and i not in (1, 8, 12):
                continue
            pair("%s @ %g" % (F2.NAMES[i - 1], bpm), zL, zR,
                 (lambda w, t: (lambda b: ([80 << 8, w, 40 << 8, 64 << 8], t)))(sw, bpm), "synced")
    # transitions (fl02check's plan through the smoother model)
    plan = [(0, 32 << 8, 120.0), (1.0, F2.SYNC[7], 120.0), (2.5, F2.SYNC[9], 120.0), (4.0, F2.SYNC[9], 90.0),
            (5.0, F2.SYNC[9], 160.0), (6.0, (F2.ANCHOR[9] + 1) << 8 | 10, 160.0), (7.0, F2.SYNC[9], 160.0),
            (8.0, 70 << 8, 100.0), (9.0, F2.SYNC[0], 100.0), (10.0, F2.SYNC[11], 100.0), (11.0, 9 << 8, 100.0)]
    nbt = int(12.0 * F2.BPS)
    tL, tR = (L * (nbt * 32 // len(L) + 1))[:nbt * 32], (R * (nbt * 32 // len(R) + 1))[:nbt * 32]
    sm = F2.Smoother(32 << 8)
    words, tempos = [], []
    for b in range(nbt):
        k = max(j for j, (t0, _, _) in enumerate(plan) if b >= t0 * F2.BPS)
        words.append(sm.step(plan[k][1]))
        tempos.append(plan[k][2])
    pair("fl02check's plan (12 s)", tL, tR, lambda b: ([80 << 8, words[b], 40 << 8, 64 << 8], tempos[b]), "transitions")
    # the grid: worst block and highest mean per variant
    if a.grid:
        gL, gR = L[:24000], R[:24000]
        res["grid"] = {}
        for n in VARIANTS:
            for tag, settings in (("free", [((dp << 8, sp << 8, c << 8, wd << 8)) for dp in (0, 64, 127)
                                            for sp in (0, 64, 127) for c in (0, 12, 32, 64, 82, 100, 127)
                                            for wd in (0, 64, 127)]),
                                  ("synced", [(dp << 8, sw, c << 8, wd << 8) for dp in (0, 64, 127)
                                              for c in (0, 32, 82, 127) for wd in (0, 64, 127) for sw in F2.SYNC])):
                worst, wk, means = 0, None, []
                for k in settings:
                    sh = F2.Sharc(built[n][0], MAGIC)
                    F2.run(sh, gL, gR, (lambda k: (lambda b: (list(k), 120.0)))(k))
                    p = sh.per_block[1:] if tag == "free" else sh.per_block[F2.HOLD + 2:]
                    means.append(sum(p) / len(p))
                    if max(sh.per_block[1:]) > worst:
                        worst, wk = max(sh.per_block[1:]), ["%#x" % x for x in k]
                    del sh
                    memguard.check()
                res["grid"]["%s %s" % (n, tag)] = {"cases": len(means), "worst_block": worst, "worst_at": wk,
                                                   "highest_mean": round(max(means), 1)}
                print("grid", n, tag, res["grid"]["%s %s" % (n, tag)], flush=True)
    worst = {n: max(max(e["insn_max"][n] for e in res[g].values()) for g in ("free", "synced", "transitions"))
             for n in VARIANTS}
    res["worst_block_over_the_cases"] = worst
    res["ok"] = all_ok
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(res, indent=1))
    print("worst block", worst, "OK" if all_ok else "FAIL", flush=True)
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
