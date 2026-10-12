#!/usr/bin/env python3
"""Render audio through the DN2's whole DSP block handler 0x1c9d6b (T3.6 stretch goal).

    python3 tools/sharcvm/render_full.py [--in input.wav] [--seconds 4] \
        --set IN_L=40 IN_R=40 IN_REV_L=80 IN_REV_R=80 REV_VOL=100 PATTERN_VOL=127 ...

The audio enters as the codec input (the unit's IN L/R jacks), goes through the external
input path, the FX bus (sends to chorus/delay/reverb per the frame words) and the master
stage (compressor routing, compressor, master overdrive, pattern volume), and leaves as
the floats the handler converts to the codec output. No voices are triggered: the
ColdFire frame carries only the global FX/mixer words named in fullblock.FRAME_WORDS
(knob values 0..127; TEMPO in BPM, sent as 120 x BPM). Pans are bipolar with 64 = centre.
"""
import argparse
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
from fullblock import FullDSP, FRAME_WORDS  # noqa: E402
from fx import write_wav, read_wav, SR  # noqa: E402
from render_fx import test_signal  # noqa: E402

DEFAULTS = dict(IN_L=40, IN_R=40, IN_L_PAN=64, IN_R_PAN=64, PATTERN_VOL=127,
                IN_REV_L=80, IN_REV_R=80, REV_PRE=10, REV_DEC=90, REV_FREQ=64, REV_GAIN=64,
                REV_HPF=0, REV_LPF=127, REV_VOL=100,
                IN_DEL_L=50, IN_DEL_R=50, DEL_TIME=32, DEL_FDBK=40, DEL_WID=64, DEL_LPF=127, DEL_VOL=90,
                DEL_REV=30, CHR_VOL=0, CMP_THR=127, CMP_RAT=0, CMP_MIX=0, TEMPO=120)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp")
    ap.add_argument("--seconds", type=float, default=4.0)
    ap.add_argument("--set", nargs="*", default=[])
    ap.add_argument("--out", default=str(ROOT / "out/T3.6/wav/fullchain_in_rev_del.wav"))
    a = ap.parse_args()
    words = dict(DEFAULTS)
    for kv in a.set:
        k, v = kv.split("=")
        if k not in FRAME_WORDS:
            sys.exit(f"unknown frame word {k}; known: {', '.join(FRAME_WORDS)}")
        words[k] = int(v)
    if a.inp:
        L, R, sr = read_wav(a.inp)
        assert sr == SR
        n = int(a.seconds * SR)
        L, R = (L + [0.0] * n)[:n], (R + [0.0] * n)[:n]
    else:
        L, R = test_signal(a.seconds)
    d = FullDSP()
    tempo = words.pop("TEMPO")           # BPM; the frame word 0xd8 carries 120 x BPM (T3.3)
    d.set(**words)
    d.set_raw(TEMPO=int(round(120 * tempo)))
    t0 = time.time()
    oL, oR = d.process(L, R)
    wall = time.time() - t0
    peak = write_wav(a.out, oL, oR)
    blocks = len(L) // 32
    cyc = sorted(d.cycles)
    print(f"full chain: {len(L) / SR:.1f} s, {blocks} blocks, {d.insns / blocks:.0f} instructions/block "
          f"(handler's own EMUCLK count: median {cyc[len(cyc) // 2]}), core {d.insns / d.secs / 1e6:.1f} M insn/s, "
          f"wall {wall:.1f} s ({len(L) / SR / wall:.3f}x real time), output peak {peak:.3f}")
    print(f"wrote {a.out}")


if __name__ == "__main__":
    main()
