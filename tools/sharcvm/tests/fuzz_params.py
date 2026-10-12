#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Run the stock effects and the whole block handler with random parameter settings on noise,
looking for traps (unimplemented paths) and non-finite output.

    python3 tools/sharcvm/tests/fuzz_params.py [--sets 20] [--blocks 150]
"""
import argparse, math, random, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent)); sys.dont_write_bytecode = True
from fx import FxRunner, EFFECTS          # noqa: E402
from fullblock import FullDSP, FRAME_WORDS  # noqa: E402
from vm import VMError, dn2_after_init    # noqa: E402

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sets", type=int, default=20)
    ap.add_argument("--blocks", type=int, default=150)
    a = ap.parse_args()
    rng = random.Random(7)
    problems = 0
    for name in ("delay", "chorus", "reverb", "comp"):
        worst = 0.0
        for k in range(a.sets):
            fx = FxRunner(name)
            p = {f: rng.choice([0.0, 1.0, rng.random()]) for f in EFFECTS[name]["fields"]}
            if name == "delay":
                p["FDBK"] = rng.random() * 0.99      # value * 2^-15 range 0..~1
                p["WID"] = rng.uniform(-1, 1)
            if name == "comp":
                p["RAT"] = rng.randint(0, 7) / 7
            fx.set_params(**p)
            try:
                for b in range(a.blocks):
                    L = [rng.uniform(-0.5, 0.5) for _ in range(32)]
                    R = [rng.uniform(-0.5, 0.5) for _ in range(32)]
                    oL, oR = fx.block(L, R)
                    m = max(max(map(abs, oL)), max(map(abs, oR)))
                    if not math.isfinite(m):
                        print(f"  {name} set {k}: non-finite output at block {b}, params {p}")
                        problems += 1
                        break
                    worst = max(worst, m)
            except VMError as e:
                print(f"  {name} set {k}: TRAP {e} params {p}")
                problems += 1
        print(f"{name}: {a.sets} random settings x {a.blocks} blocks, largest output {worst:.3g}")
    # whole block: random FX/mixer words
    vm = dn2_after_init()
    for k in range(max(2, a.sets // 4)):
        d = FullDSP(vm=dn2_after_init())
        words = {n: rng.randint(0, 127) for n in FRAME_WORDS if n not in ("CMP_TRACKS", "CMP_IN_FX", "TEMPO")}
        d.set(**words)
        d.set_raw(TEMPO=120 * rng.randint(30, 300), CMP_IN_FX=rng.randint(0, 7), CMP_TRACKS=rng.randint(0, 0xffff))
        try:
            worst = 0.0
            for b in range(a.blocks // 2):
                L = [rng.uniform(-0.5, 0.5) for _ in range(32)]
                oL, oR = d.block(L, L)
                m = max(max(map(abs, oL)), max(map(abs, oR)))
                if not math.isfinite(m):
                    print(f"  full block set {k}: non-finite output at block {b}")
                    problems += 1
                    break
                worst = max(worst, m)
            print(f"full block set {k}: ok, largest output {worst:.3g}")
        except VMError as e:
            print(f"  full block set {k}: TRAP {e}")
            problems += 1
    print(f"problems: {problems}")

if __name__ == "__main__":
    main()
