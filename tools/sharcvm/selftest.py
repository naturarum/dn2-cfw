#!/usr/bin/env python3
"""Regression checks for the SHARC+ interpreter (T3.6). Run after any change to the core.

    python3 tools/sharcvm/selftest.py [--quick]

Checks (each prints PASS/FAIL with the measured number):
  1. selache C cases (build_cases.py output): >= 2036 of 2039 pass (--quick: first 300)
  2. DN2 divide 0x1c06ba vs Python: >= 99% bit-exact on 500 random pairs
  3. DN2 log10 0x1c0b60 / powf 0x1c0c76: <= 2 / 3 ulp on 200 inputs
  4. stock compressor static curve: -6 dBFS sine, THR -30 dB, 4:1 -> -24.0 dBFS (+-0.1 dB)
  5. stock delay: echo spacing 48,749 samples (65/128 bar) at TIME 64/127, 120 BPM
  6. stock chorus: noise-burst lag inside the 259..368-sample table range on both channels
  7. full block handler: runs, and its EMUCLK count equals the interpreter's instruction count +-1%
  8. fast paths off vs on: identical full-block output (200 blocks)
"""
import argparse
import math
import random
import struct
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.dont_write_bytecode = True

results = []


def check(name, ok, detail):
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {name}: {detail}", flush=True)


def f32(x):
    return struct.unpack("<f", struct.pack("<f", x))[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    a = ap.parse_args()
    import vm as V
    V.build_lib()
    from vm import VM, call_rts, u2f, f2u

    # 1
    cmd = [sys.executable, str(HERE / "run_cases.py")] + (["--only", "cctest_a"] if a.quick else [])
    out = subprocess.run(cmd, capture_output=True, text=True).stdout
    line = next((l for l in out.splitlines() if "cases in" in l), "")
    npass = int(line.split("PASS ")[1].split(",")[0]) if "PASS " in line else 0
    total = int(line.split(" cases")[0]) if line else 0
    check("selache C cases", npass >= total - 3 and total > 0, line.strip())

    # 2, 3
    vm = VM()
    vm.load_dn2()
    rng = random.Random(1)
    exact = 0
    for _ in range(500):
        x = f32(rng.uniform(-1e3, 1e3) * 10 ** rng.randint(-6, 6))
        y = f32(rng.uniform(-1e3, 1e3) * 10 ** rng.randint(-6, 6))
        r = u2f(call_rts(vm, 0x1c06ba, F4=x, F8=y))
        exact += f2u(r) == f2u(f32(x / y))
    check("DN2 divide", exact >= 495, f"{exact}/500 bit-exact")
    worst_log = worst_pow = 0
    for _ in range(200):
        x = f32(10 ** rng.uniform(-8, 8))
        r = u2f(vm.call(0x1c0b60, F4=x))
        worst_log = max(worst_log, abs(f2u(r) - f2u(f32(math.log10(x)))) if (r >= 0) == (math.log10(x) >= 0) else 99)
        b, e = f32(rng.uniform(0.1, 10)), f32(rng.uniform(-30, 30))
        r = u2f(vm.call(0x1c0c76, R4=f2u(b), R8=f2u(e)))
        worst_pow = max(worst_pow, abs(f2u(r) - f2u(f32(b ** e))))
    check("DN2 log10/powf", worst_log <= 2 and worst_pow <= 3, f"worst {worst_log} / {worst_pow} ulp")

    # 4, 5, 6
    from fx import FxRunner
    fx = FxRunner("comp")
    fx.set_params(SCF=0.5, THR=0.5, RAT=3 / 7, ATK=0.3, REL=0.5, MUP=0.0, MIX=1.0)
    for b in range(400):
        L = [0.5 * math.sin(2 * math.pi * 440 * (b * 32 + i) / 48000) for i in range(32)]
        oL, _ = fx.block(L, L)
    db = 20 * math.log10(max(map(abs, oL)))
    check("compressor static curve", abs(db + 24.0) < 0.1, f"{db:.2f} dBFS (expect -24.00)")
    fx = FxRunner("delay", vm=fx.vm)
    fx.set_params(TIME=64 / 127, X=0.0, WID=0.0, FDBK=0.5, HPF=0.0, LPF=1.0)
    n = 48000 * 3
    L = [0.0] * n
    L[0] = 1.0
    oL, _ = fx.process(L, [0.0] * n)
    peaks = [i for i, x in enumerate(oL) if abs(x) > 0.2 and i > 0]
    sp = [peaks[k + 1] - peaks[k] for k in range(len(peaks) - 1)]
    check("delay echo spacing", sp[:1] == [48749], f"echoes at {peaks[:3]}")
    from render_fx import test_signal
    iL, iR = test_signal(1.6)
    fx = FxRunner("chorus", vm=fx.vm)
    fx.set_params(DPTH=0.5, SPD=0.3, HPF=0.0, WDTH=0.7)
    oL, oR = fx.process(iL, iR)
    s0 = int(1.25 * 48000)

    def lag(x, y):
        best = max(range(800), key=lambda k: sum(a_ * b_ for a_, b_ in zip(x[s0:s0 + 1200], y[s0 + k:s0 + k + 1200])))
        return best
    lL, lR = lag(iL, oL), lag(iR, oR)
    check("chorus lag", 259 <= lL <= 368 and 259 <= lR <= 368, f"L {lL}, R {lR} samples")

    # 7, 8
    from fullblock import FullDSP
    outs = []
    for nf in (0, 1):
        d = FullDSP()
        d.vm.lib.vm_set_no_fast(d.vm.h, nf)
        d.set(IN_L=40, IN_R=40, IN_L_PAN=64, IN_R_PAN=64, PATTERN_VOL=127, IN_REV_L=80, IN_REV_R=80, REV_DEC=90,
              REV_VOL=100, IN_DEL_L=50, IN_DEL_R=50, DEL_TIME=32, DEL_FDBK=40, DEL_VOL=90)
        o = []
        for b in range(50 if a.quick else 200):
            L = [0.2 * math.sin(2 * math.pi * 440 * (b * 32 + i) / 48000) for i in range(32)]
            x, y = d.block(L, L)
            o += x + y
        outs.append(o)
        if nf == 0:
            per = d.insns / len(d.cycles)
            cyc = sorted(d.cycles)[len(d.cycles) // 2]
            check("full block handler", abs(cyc - per) / per < 0.01, f"{per:.0f} insns/block, handler EMUCLK {cyc}")
    check("fast paths equivalent", outs[0] == outs[1], "identical" if outs[0] == outs[1] else "DIFFERENT")
    print(f"{sum(results)}/{len(results)} checks passed")
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
