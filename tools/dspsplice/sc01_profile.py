#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""SC01 (notes/SC01.md 1.2): how often each function of the Space Echo (SE01's image) and of the 3-band compressor (CMP3's image) runs:
instructions executed per block, by scenario (SC01 layout study: what must stay in L1, what may run from L2).

    python3 tools/dspsplice/sc01_profile.py out/SC01/SC01-dsp/profile.json

Scenarios, each after a warm-up:
  SE01: steady (MODE 6, FDBK 64, AGE 64, band 64/64, a held input, no knob moving), every delay knob moving each block
        (TIME, X, WID, FDBK, HPF, LPF, AGE), and the effect init (from a fresh load).
  CMP3: steady tail mode (distinct bands compressing, MIX 64), every band knob and crossover moving every block,
        and the effect init.
Also reports the instructions executed in the stock FX ranges A-D (0xb83eaf-0xb84926, 0xb84943-0xb84c1f,
0xb84ccb-0xb84dee, 0xb84fd3-0xb85466) in CMP3's image, where they still hold the stock code: 0 = dead at run time.
"""
import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SCR = ROOT / "out/SC01/profile-work"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "tools/dspsplice"))
sys.path.insert(0, str(ROOT / "tools/sharcvm"))
sys.dont_write_bytecode = True
import memguard  # noqa: E402
import sc01_funcmap as FM  # noqa: E402
import verify_cmp3 as V3  # noqa: E402

VC, V2, V = V3.VC, V3.V2, V3.V
DEAD = [(0xb83eaf, 0xb84926, "A"), (0xb84943, 0xb84c1f, "B"), (0xb84ccb, 0xb84dee, "C"), (0xb84fd3, 0xb85466, "D")]


def hits_by_pc(vm):
    out = {}
    for pc in vm.decoded:
        h = vm.pages[pc >> 12][pc & 0xfff].hits
        if h:
            out[pc] = h
    return out


def by_func(h, fmap):
    res = {}
    for n, f, a, b in fmap:
        s = sum(v for pc, v in h.items() if a <= pc < b)
        res[n] = s
    return res


def dead_hits(h):
    return {tag: sum(v for pc, v in h.items() if lo <= pc < hi) for lo, hi, tag in DEAD}


def diff(h1, h0):
    return {pc: v - h0.get(pc, 0) for pc, v in h1.items() if v - h0.get(pc, 0)}


def main():
    out = Path(sys.argv[1])
    res = {}
    # ---------------- SE01
    seb = ROOT / "out/T5.3/SE01-dsp/build"
    se_map, _ = FM.funcmap(SCR / "fm_se", 0x308400, [seb / "spaceecho.s", seb / "se_glue.s"])
    stream = (ROOT / "out/T5.3/SE01-dsp/section7.bin").read_bytes()
    vm = V.candidate_vm(stream)            # effect init run inside, before the profile is on
    vm.lib.vm_set_profile(vm.h, 1)
    words = dict(VC.BASE, **VC.NOFX)
    words.update(IN_DEL_L=100, IN_DEL_R=100, DEL_VOL=100, DEL_TIME=40, DEL_X=6, DEL_WID=30,
                 DEL_FDBK=64, DEL_HPF=64, DEL_LPF=64)
    r = V.Runner(vm, words)
    import struct
    struct.pack_into("<H", r.d.frame, 0xa18, 64 << 8)          # k 40 = AGE 64
    sig = [0.3 * math.sin(2 * math.pi * 220 * i / 48000) for i in range(32 * 900)]
    for b in range(200):
        r.block(sig[32 * b:32 * b + 32], sig[32 * b:32 * b + 32], capture=False)
    h0 = hits_by_pc(vm)
    for b in range(200, 500):
        r.block(sig[32 * b:32 * b + 32], sig[32 * b:32 * b + 32], capture=False)
    h1 = hits_by_pc(vm)
    steady = by_func(diff(h1, h0), se_map)
    names = ["DEL_TIME", "DEL_X", "DEL_WID", "DEL_FDBK", "DEL_HPF", "DEL_LPF"]
    for b in range(500, 800):
        for k, n in enumerate(names):
            v = (b * (3 + k)) % 128 if n != "DEL_X" else (b // 7) % 7
            r.d.set(**{n: v})
        struct.pack_into("<H", r.d.frame, 0xa18, ((b * 5) % 128) << 8)
        r.block(sig[32 * b:32 * b + 32], sig[32 * b:32 * b + 32], capture=False)
    h2 = hits_by_pc(vm)
    moving = by_func(diff(h2, h1), se_map)
    # init: a fresh VM, profile on before the effect init
    vm2 = V.VM()
    vm2.load_dn2()
    V.load_stream(vm2, stream)
    vm2.lib.vm_set_profile(vm2.h, 1)
    vm2.call(V.FXINIT, max_insns=10**9)
    init = by_func(hits_by_pc(vm2), se_map)
    res["SE01"] = {n: {"bytes": 2 * (b - a), "steady_per_block": round(steady[n] / 300, 1),
                       "moving_per_block": round(moving[n] / 300, 1), "init": init[n]} for n, f, a, b in se_map}
    del vm, vm2, r
    memguard.check()
    # ---------------- CMP3
    cb = ROOT / "out/T6.3/CMP3-dsp/build"
    c3_map, _ = FM.funcmap(SCR / "fm_c3", 0x308400, [cb / "mbcomp.s", cb / "stubs3.s"])
    c4_map, _ = FM.funcmap(SCR / "fm_c4", 0x2e2900, [cb / "cmp_glue.s", cb / "cmp_tail.s"])
    cmap = c3_map + c4_map
    VC.configure("CMP3")
    V3.OFF.update(VC.offsets(SCR / "offsets_c3", name="CMP3"))
    stream = (ROOT / "out/T6.3/CMP3-dsp/section7.bin").read_bytes()
    vm = V.candidate_vm(stream)
    vm.lib.vm_set_profile(vm.h, 1)
    prog = VC.looped(900)
    words = dict(VC.BASE, **VC.FXW, **VC.comp(32, 24, 32, 64, 3, 64))
    t = V3.TailRun(vm, words, VC.ROUTE_INFX, V3.sched3(V3.DISTINCT, (64, 64, 64)), keep=False)
    for b in range(200):
        t.tblock(prog[0][32 * b:32 * b + 32], prog[1][32 * b:32 * b + 32], b)
    g0 = hits_by_pc(vm)
    for b in range(200, 500):
        t.tblock(prog[0][32 * b:32 * b + 32], prog[1][32 * b:32 * b + 32], b)
    g1 = hits_by_pc(vm)
    steady = by_func(diff(g1, g0), cmap)
    moving17 = lambda b: [20 + 4 * (b % 2), 3 + b % 2, 10 + 4 * (b % 2), 40 + 4 * (b % 2), 50 + 4 * (b % 2),  # noqa: E731
                          60 + 4 * (b % 2), 60 - 4 * (b % 2), 6 - b % 2, 40 + 4 * (b % 2), 60 + 4 * (b % 2),
                          30 + 4 * (b % 2), 50 + 4 * (b % 2), 90 - 4 * (b % 2), 8 - b % 2, 80 + 4 * (b % 2),
                          100 + 4 * (b % 2), 90 - 4 * (b % 2)]
    t.sched = V3.list3([(moving17(b), (64 - 8 * (b % 2), 64, 64 + 8 * (b % 2))) for b in range(900)])
    for b in range(500, 800):
        t.tblock(prog[0][32 * b:32 * b + 32], prog[1][32 * b:32 * b + 32], b)
    g2 = hits_by_pc(vm)
    moving = by_func(diff(g2, g1), cmap)
    dead_all = dead_hits(g2)
    vm2 = V.VM()
    vm2.load_dn2()
    V.load_stream(vm2, stream)
    vm2.lib.vm_set_profile(vm2.h, 1)
    vm2.call(V.FXINIT, max_insns=10**9)
    hi = hits_by_pc(vm2)
    init = by_func(hi, cmap)
    res["CMP3"] = {n: {"bytes": 2 * (b - a), "steady_per_block": round(steady[n] / 300, 1),
                       "moving_per_block": round(moving[n] / 300, 1), "init": init[n], "region": "code3" if (n, f, a, b) in c3_map else "code4"}
                   for n, f, a, b in cmap}
    res["CMP3_stock_dead_ranges_executed"] = {"run_800_blocks": dead_all, "init": dead_hits(hi)}
    out.write_text(json.dumps(res, indent=1))
    for img in ("SE01", "CMP3"):
        print(img)
        for n, d in res[img].items():
            print("  %-26s %6d B  steady %8.1f  moving %8.1f  init %7d" % (n, d["bytes"], d["steady_per_block"],
                                                                         d["moving_per_block"], d["init"]))
    print("dead ranges executed (CMP3):", res["CMP3_stock_dead_ranges_executed"])


if __name__ == "__main__":
    main()
