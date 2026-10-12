#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""The stock DN2 master compressor (OS 1.12, 0xb8541d) in the T3.6 interpreter vs a per-sample model
of its code (T3.11 section 12.2, 2026-10-06).

The model is what the disassembly of 0xb85070 / 0xb85111 / 0xb851e5 / 0xb852d2 says:
  detector  lev = max(|scL|, |scR|, 2^-23) of the side-chain signal, in dB (20 log10)
  curve     hard knee: gc = 0 if lev <= thr else thr + (lev - thr)/r - lev   (thr = 60*THR - 60 dB)
  release   y1 = min(gc, cr*y1 + (1-cr)*gc)        (instant attack, one-pole release, in dB)
  attack    y2 = ca*y2 + (1-ca)*y1                 (plain one-pole on top: "smooth decoupled")
  gain      g = 10^((y2 + 24.08*MUP)/20); wet = clip(x*g, +-1)
  mix       out = T[MIX]*wet + T[127-MIX]*dry      (T = table 0x2d0200)
ca, cr are the 128-entry tables 0x2cfe00 (ATK) and 0x2d0000 (REL), indexed by trunc(knob).
Each check renders a test signal through the stock code and through the model and reports the
largest difference of the applied gain in dB (samples where |input| > 0.3 x its peak). A second
model, "branching" (one one-pole whose coefficient depends on the direction, the T3.11 design
before 12.3), is fitted to the same renders for contrast.

    python3 tools/sharcvm/tests/comp_model.py          (about 1 minute)
"""
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.dont_write_bytecode = True
from fx import FxRunner, BLOCK, SR  # noqa: E402
import stockmap as SM  # noqa: E402

RATIOS = SM.RATIOS


def f32(x):
    return SM.f32(x)


def render_stock(fx, sig, knobs):
    """a fresh runner per render (the detector state of a slow release lasts seconds)"""
    fx = FxRunner("comp")
    fx.set_params(**SM.params("comp", knobs))
    out = []
    z = [0.0] * BLOCK
    for _ in range(40):                    # settle the parameter ramps (makeup, mix) on silence
        fx.block(z, z)
    for i in range(0, len(sig), BLOCK):
        a, _ = fx.block(sig[i:i + BLOCK], sig[i:i + BLOCK])
        out += a
    return out


def model(sig, knobs, tabs, kind="decoupled"):
    att, rel, mixt = tabs
    thr = f32(60.0 * SM.params("comp", knobs)["THR"]) - 60.0
    r = RATIOS[int(knobs["RAT"])]
    ca, cr = att[int(knobs["ATK"])], rel[int(knobs["REL"])]
    mk = 20 * math.log10(16.0) * knobs.get("MUP", 0) / 127.0
    y1 = y2 = 0.0
    g = []
    for x in sig:
        lev = max(abs(x), 2.0 ** -23)
        ldb = 20 * math.log10(lev)
        gc = 0.0 if ldb <= thr else (thr + (ldb - thr) / r) - ldb
        if kind == "decoupled":
            y1 = min(gc, cr * y1 + (1 - cr) * gc)
            y2 = ca * y2 + (1 - ca) * y1
        else:                                # branching: one one-pole, attack or release coefficient
            c = ca if gc < y2 else cr
            y2 = c * y2 + (1 - c) * gc
        g.append(10 ** ((y2 + mk) / 20))
    return g


def gain_err(sig, out, g):
    pk = max(abs(x) for x in sig)
    worst = 0.0
    for x, y, gg in zip(sig, out, g):
        if abs(x) > 0.3 * pk and abs(y) < 0.999:
            e = abs(20 * math.log10(abs(y / x)) - 20 * math.log10(gg))
            worst = max(worst, e)
    return worst


def sine(levels_db, seconds_each, f=1000.0):
    s = []
    n0 = 0
    for L in levels_db:
        a = 10 ** (L / 20)
        n = int(seconds_each * SR) // BLOCK * BLOCK
        s += [a * math.sin(2 * math.pi * f * (n0 + k) / SR) for k in range(n)]
        n0 += n
    return s


def main():
    fx = FxRunner("comp")
    att = fx.vm.rdf(0x2cfe00, 128)
    rel = fx.vm.rdf(0x2d0000, 128)
    mixt = fx.vm.rdf(0x2d0200, 128)
    tabs = (att, rel, mixt)
    base = dict(SCF=64, THR=64, RAT=3, ATK=40, REL=44, MUP=0, MIX=127)
    rows = []

    # 1. static curve: settled gain at five levels, three ratios, two thresholds
    for rat in (0, 3, 7):
        for thr in (42, 85):
            k = dict(base, RAT=rat, THR=thr, ATK=0, REL=20)
            sig = sine([-50, -30, -20, -10, -3], 0.25)
            out = render_stock(fx, sig, k)
            g = model(sig, k, tabs)
            rows.append((f"static curve, RAT {RATIOS[rat]:g}:1, THR {thr} ({-60 + 60 * thr / 127:.1f} dB)",
                         gain_err(sig, out, g), None))
    # 2. attack and release on level steps -40 -> -10 -> -40 dBFS, several ATK/REL
    for atk, relk in ((0, 0), (20, 30), (40, 44), (64, 64), (100, 90), (127, 127)):
        k = dict(base, RAT=7, THR=42, ATK=atk, REL=relk)
        sig = sine([-40, -10, -40], 1.0 if relk < 100 else 2.5)
        out = render_stock(fx, sig, k)
        g = model(sig, k, tabs)
        gb = model(sig, k, tabs, "branching")
        rows.append((f"steps, ATK {atk} ({-1000 / (48000 * math.log(att[atk])):.1f} ms), "
                     f"REL {relk} ({-1000 / (48000 * math.log(rel[relk])):.0f} ms)",
                     gain_err(sig, out, g), gain_err(sig, out, gb)))
    # 3. makeup
    for mup in (0, 64, 127):
        k = dict(base, THR=127, RAT=0, MUP=mup)
        sig = sine([-30], 0.3)
        out = render_stock(fx, sig, k)
        g = model(sig, k, tabs)
        rows.append((f"makeup, MUP {mup} (+{24.0824 * mup / 127:.2f} dB)", gain_err(sig, out, g), None))
    # 4. stereo link: the louder side sets the gain for both
    k = dict(base, RAT=7, THR=42, ATK=10, REL=40)
    sl = sine([-10], 0.5)
    fx = FxRunner("comp")
    fx.set_params(**SM.params("comp", k))
    z = [0.0] * BLOCK
    for _ in range(40):
        fx.block(z, z)
    outR = []
    for i in range(0, len(sl), BLOCK):
        a, b = fx.block(sl[i:i + BLOCK], [0.1 * x for x in sl[i:i + BLOCK]])
        outR += b
    g = model(sl, k, tabs)
    rows.append(("stereo link (L at -10 dBFS, R 20 dB lower): R's gain", gain_err([0.1 * x for x in sl], outR, g), None))

    print(f"{'check':66s} {'decoupled':>10s} {'branching':>10s}   (max gain error, dB)")
    for name, e, eb in rows:
        print(f"{name:66s} {e:10.4f} {'' if eb is None else f'{eb:10.3f}'}")

    # 5. MIX: wet and dry gains, signal below threshold, MUP 0 (wet = dry)
    print("\nMIX (sine below threshold, so wet = dry): output level vs input, and the table law")
    for mix in (0, 8, 16, 32, 48, 63, 64, 65, 96, 112, 127):
        k = dict(base, THR=127, RAT=0, MUP=0, MIX=mix)
        sig = sine([-20], 0.2)
        out = render_stock(fx, sig, k)
        n = len(sig) // 2
        lvl = 10 * math.log10(sum(y * y for y in out[n:]) / sum(x * x for x in sig[n:]))
        law = 20 * math.log10(mixt[mix] + mixt[127 - mix])
        print(f"  MIX {mix:3d}: wet T={mixt[mix]:.4f} ({20 * math.log10(mixt[mix] + 1e-12):6.1f} dB), "
              f"dry T={mixt[127 - mix]:.4f}; output {lvl:+6.2f} dB, T[m]+T[127-m] {law:+6.2f} dB")


if __name__ == "__main__":
    main()
