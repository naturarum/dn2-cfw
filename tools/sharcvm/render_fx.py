#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Render the DN2's stock effects offline in the SHARC interpreter (T3.6).

    python3 tools/sharcvm/render_fx.py delay  --set TIME=0.5 FDBK=0.4 [--in input.wav] [--seconds 6]
    python3 tools/sharcvm/render_fx.py chorus --set DPTH=0.5 SPD=0.3 WDTH=0.7
    python3 tools/sharcvm/render_fx.py reverb --set PRE=0.1 DEC=0.6 ...

Parameters are the floats the DSP sees in the parameter struct (T3.4 section 1): most
are knob/127 (0..1); delay WID and FDBK are value*2^-15 (FDBK 0.5 ~ knob 64 ~ 99%).
Without --in, a synthetic test signal is used (plucked tones, a noise burst, an impulse).
Writes out/T3.6/wav/<effect>_<tag>.wav (24-bit, 48 kHz, not normalised, clipped at +-1) and
prints instructions per block and speed.

Added 2026-10-06 (T3.11/T3.7): --knob NAME=V takes raw knob values 0..127 (tools/sharcvm/stockmap.py
converts them as the DSP does; RAT takes the index 0..7), --preroll S runs S seconds of silence first
(the stock delay glides to a new TIME over about a second), --mix send --vol V writes
dry + V/127 x wet as the FX bus would (VOL curve [inferred] linear), --key FILE feeds the compressor's
side chain. render_file() does the same from Python (scripts/t37_compare.py uses it).
"""
import argparse
import math
import random
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
from fx import FxRunner, EFFECTS, write_wav, read_wav, SR, BLOCK  # noqa: E402

# WID 0.5 is the screen's 0 (the centre of the bipolar knob: plain stereo echoes) and SCF 64/127
# the neutral side-chain filter (T3.11, 2026-10-06 measurements). Before 2026-10-06 the defaults
# were WID 0.0 (screen -64) and SCF 0.0 (a low-pass side-chain filter below 20 Hz).
DEFAULTS = {
    "delay": dict(TIME=32 / 127, X=0.0, WID=0.5, FDBK=0.4, HPF=0.0, LPF=1.0),
    "chorus": dict(DPTH=0.5, SPD=0.3, HPF=0.0, WDTH=0.7),
    "reverb": dict(PRE=0.1, DEC=0.6, FREQ=0.5, GAIN=0.5, HPF=0.0, LPF=1.0),
    "comp": dict(SCF=64 / 127, THR=0.5, RAT=3 / 7, ATK=0.3, REL=0.5, MUP=0.0, MIX=1.0),
}


def test_signal(seconds, seed=1):
    """Plucked tones (L: 220 Hz then 330 Hz, R: 277 Hz), a noise burst, then an impulse. Peak ~0.5."""
    random.seed(seed)
    n = int(seconds * SR)
    L = [0.0] * n
    R = [0.0] * n

    def pluck(buf, t0, f, dur, amp):
        i0 = int(t0 * SR)
        for k in range(int(dur * SR)):
            if i0 + k >= n:
                break
            t = k / SR
            env = math.exp(-t * 4.0) * min(1.0, t * 400)
            buf[i0 + k] += amp * env * (math.sin(2 * math.pi * f * t) + 0.3 * math.sin(4 * math.pi * f * t))
    pluck(L, 0.0, 220.0, 1.0, 0.4)
    pluck(R, 0.25, 277.2, 1.0, 0.4)
    pluck(L, 0.5, 330.0, 1.0, 0.3)
    pluck(R, 0.5, 330.0, 1.0, 0.3)
    i0 = int(1.25 * SR)
    for k in range(int(0.05 * SR)):
        e = 0.3 * math.exp(-k / (0.01 * SR))
        if i0 + k < n:
            L[i0 + k] += e * random.uniform(-1, 1)
            R[i0 + k] += e * random.uniform(-1, 1)
    i1 = int(2.0 * SR)
    if i1 < n:
        L[i1] += 0.5
        R[i1] += 0.5
    return L, R


def render_file(effect, inp, knobs=None, params=None, seconds=None, preroll=0.0, mix="wet", vol=127.0,
                key=None, bpm=120.0):
    """Render a 48 kHz WAV through a stock effect. knobs: raw knob values (stockmap); params: floats
    (override). Returns (L, R, input L, input R, instructions per block)."""
    import stockmap
    p = dict(DEFAULTS[effect])
    if knobs is not None:
        p = stockmap.params(effect, knobs)
    p.update(params or {})
    L, R, sr = read_wav(inp)
    if sr != SR:
        raise ValueError(f"{inp}: input must be {SR} Hz")
    n = int(seconds * SR) if seconds else len(L)
    n = (n + BLOCK - 1) // BLOCK * BLOCK
    L = (L + [0.0] * n)[:n]
    R = (R + [0.0] * n)[:n]
    kL = kR = None
    if key:
        kL, kR, _ = read_wav(key)
        kL, kR = (kL + [0.0] * n)[:n], (kR + [0.0] * n)[:n]
    fx = FxRunner(effect, bpm=bpm)
    fx.set_params(**p)
    z = [0.0] * BLOCK
    for _ in range(int(preroll * SR) // BLOCK):
        fx.block(z, z)
    fx.insns = 0
    oL, oR = [], []
    for i in range(0, n, BLOCK):
        s = slice(i, i + BLOCK)
        a, b = fx.block(L[s], R[s], kL[s], kR[s]) if (key and effect == "comp") else fx.block(L[s], R[s])
        oL += a
        oR += b
    if mix == "send":
        g = vol / 127.0
        oL = [x + g * y for x, y in zip(L, oL)]
        oR = [x + g * y for x, y in zip(R, oR)]
    return oL, oR, L, R, fx.insns / (n // BLOCK)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("effect", choices=sorted(EFFECTS))
    ap.add_argument("--set", nargs="*", default=[], help="FIELD=value")
    ap.add_argument("--in", dest="inp")
    ap.add_argument("--seconds", type=float, default=6.0)
    ap.add_argument("--bpm", type=float, default=120.0)
    ap.add_argument("--tag", default="")
    ap.add_argument("--out")
    ap.add_argument("--knob", nargs="*", default=None, help="NAME=raw knob value 0..127 (RAT: index 0..7)")
    ap.add_argument("--preroll", type=float, default=0.0)
    ap.add_argument("--mix", choices=["wet", "send"], default="wet")
    ap.add_argument("--vol", type=float, default=127.0)
    ap.add_argument("--key")
    a = ap.parse_args()
    params = dict(DEFAULTS[a.effect])
    for kv in a.set:
        k, v = kv.split("=")
        params[k] = float(v)
    if a.knob is not None or a.preroll or a.mix != "wet" or a.key:
        if not a.inp:
            sys.exit("--knob/--preroll/--mix/--key need --in")
        knobs = {kv.split("=")[0]: float(kv.split("=")[1]) for kv in (a.knob or [])}
        t0 = time.time()
        oL, oR, L, R, ipb = render_file(a.effect, a.inp, knobs=knobs if a.knob is not None else None,
                                        params={k: float(v) for k, v in (kv.split("=") for kv in a.set)},
                                        seconds=a.seconds, preroll=a.preroll, mix=a.mix, vol=a.vol,
                                        key=a.key, bpm=a.bpm)
        out = Path(a.out) if a.out else ROOT / f"out/T3.6/wav/{a.effect}_{a.tag or 'knobs'}.wav"
        out.parent.mkdir(parents=True, exist_ok=True)
        peak = write_wav(out, oL, oR)
        print(f"{a.effect}: {len(oL) / SR:.1f} s, {ipb:.0f} instructions/block, wall {time.time() - t0:.1f} s, "
              f"peak {peak:.3f}; wrote {out}")
        return
    if a.inp:
        L, R, sr = read_wav(a.inp)
        if sr != SR:
            sys.exit(f"input must be {SR} Hz")
        L, R = L[:int(a.seconds * SR)], R[:int(a.seconds * SR)]
        pad = int(a.seconds * SR) - len(L)
        if pad > 0:
            L += [0.0] * pad
            R += [0.0] * pad
    else:
        L, R = test_signal(a.seconds)
    t0 = time.time()
    fx = FxRunner(a.effect, bpm=a.bpm)
    t_init = time.time() - t0
    fx.set_params(**params)
    t0 = time.time()
    oL, oR = fx.process(L, R)
    wall = time.time() - t0
    blocks = len(L) // BLOCK
    tag = a.tag or "_".join(f"{k}{params[k]:.3g}" for k in EFFECTS[a.effect]["fields"])
    out = Path(a.out) if a.out else ROOT / f"out/T3.6/wav/{a.effect}_{tag}.wav"
    out.parent.mkdir(parents=True, exist_ok=True)
    peak = write_wav(out, oL, oR)
    write_wav(out.with_name(out.stem + "_input.wav"), L, R)
    print(f"{a.effect}: {len(L) / SR:.1f} s, {blocks} blocks, {fx.insns / blocks:.0f} instructions/block, "
          f"core {fx.insns / fx.secs / 1e6:.1f} M insn/s, wall {wall:.1f} s ({len(L) / SR / wall:.2f}x real time), "
          f"setup {t_init:.1f} s, output peak {peak:.3f}")
    print(f"params {params}")
    print(f"wrote {out}")
    if fx.vm.unimpl:
        print("unimplemented (decoded, not necessarily executed):", dict(fx.vm.unimpl))


if __name__ == "__main__":
    main()
