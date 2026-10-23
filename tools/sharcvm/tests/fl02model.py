#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""fl02model.py - FL02 (notes/T5.6-flanger-retune.md 7.1, 7.2): the arithmetic the sync relies on, outside the DSP.

    python3 tools/sharcvm/tests/fl02model.py [--out out/FL02/fl02model.json]

  - the frame word back from the parameter float: p = float32(w) x float32(1/32512) (the unit's unpack), then
    float32(float32(p x 32512) + 0.5) truncated, for every word 0..0x7fff: the word again;
  - the free knob v = min(float32(p x 127), 126.999) distinct for every word 0..0x7f00 (so flg_glue3.c sees each move);
  - the ColdFire's smoother (the unit's EMAC arithmetic, scripts/t63c_e2e.py) settles exactly on each of the 12 synced
    words from 8 starts (whole numbers, neighbours, the far ends), and how long it takes;
  - glides from every whole number and every synced word to every synced word: the frames that sit on another synced
    word on the way, and the longest such run (the DSP's hold, 48 blocks, must be longer);
  - glides to every whole number from 3 steps above and below: the longest run on a synced word (a free knob's).
"""
import argparse
import json
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
A = [9, 23, 37, 52, 66, 80, 86, 94, 100, 108, 122, 126]
S = [(a << 8) | i for i, a in enumerate(A, 1)]


def f32(x):
    return struct.unpack('<f', struct.pack('<f', x))[0]


def s32(x):
    x &= 0xffffffff
    return x - (1 << 32) if x & 0x80000000 else x


def step(raw, s):
    raw = raw - 0x10000 if raw & 0x8000 else raw
    return s32(((0x03d7 * raw) << 1) + ((0x7c290000 * s) >> 31))


def glide(a, b, n=1500):
    s, out = a << 16, []
    for _ in range(n):
        s = step(b, s)
        out.append(((s + 0x8000) >> 16) & 0xffff)
    return out


def runs_on(words, targets, exclude):
    longest = run = hits = 0
    for w in words:
        if w in targets and w != exclude:
            run += 1
            hits += 1
            longest = max(longest, run)
        else:
            run = 0
    return hits, longest


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default=str(ROOT / 'out/FL02/fl02model.json'))
    a = ap.parse_args()
    F15 = f32(1 / 32512)
    mism, maxerr, vs = 0, 0.0, set()
    for w in range(0x8000):
        p = f32(f32(float(w)) * F15)
        if int(f32(f32(p * 32512.0) + 0.5)) != w:
            mism += 1
        maxerr = max(maxerr, abs(p * 32512.0 - w))
        if w <= 0x7f00:
            vs.add(min(f32(p * 127.0), f32(126.999)))
    res = {'decode': {'words': 0x8000, 'mismatches': mism, 'max_abs_error': maxerr},
           'free_knob_distinct': {'words': 0x7f01, 'distinct': len(vs)}}
    worst, bad = 0, 0
    for t in S:
        for st in (0, 0x7f00, 0x2000, t - 1, t + 1, t - 0x100, t + 0x100, t & 0xff00):
            ws = glide(st, t)
            if ws[-1] != t:
                bad += 1
            last_off = max((i for i, x in enumerate(ws) if x != t), default=-1)
            worst = max(worst, last_off + 1)
    res['smoother_settles'] = {'targets': 12, 'starts_each': 8, 'not_exact': bad, 'worst_frames': worst,
                               'worst_ms': round(worst / 1.5, 1)}
    hits = longest = tot = 0
    for a_ in S + [x << 8 for x in range(128)]:
        for b in S:
            if a_ == b:
                continue
            h, l_ = runs_on(glide(a_, b), set(S), b)
            hits, longest, tot = hits + h, max(longest, l_), tot + 1
    res['glides_to_synced'] = {'glides': tot, 'frames_on_another_synced_word': hits, 'longest_run': longest}
    hits = longest = tot = 0
    for n in range(128):
        for st in (min(127, n + 3), max(0, n - 3)):
            if st == n:
                continue
            h, l_ = runs_on(glide(st << 8, n << 8), set(S), None)
            hits, longest, tot = hits + h, max(longest, l_), tot + 1
    res['glides_to_whole_numbers'] = {'glides': tot, 'frames_on_a_synced_word': hits, 'longest_run': longest,
                                      'dsp_hold_blocks': 48}
    res['ok'] = (mism == 0 and len(vs) == 0x7f01 and bad == 0 and res['glides_to_synced']['longest_run'] < 48
                 and longest < 48)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(res, indent=1))
    print(json.dumps(res, indent=1))
    return 0 if res['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
