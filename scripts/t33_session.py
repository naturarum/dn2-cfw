#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""T3.3: drive one FX/mixer page in the emulator and save a snapshot after each encoder turn.

  venv/bin/python scripts/t33_session.py PAGE_NAME --nav KEYS [--encoders 1-9] [--delta 4]

Starts digikit's tools/guirun.py from out/T3.3/snaps/main720M.snap (main screen, 1.12) and
replays: the navigation KEYS (e.g. "17+5,14" = hold FUNC, tap FX, release FUNC, then tap DOWN),
a baseline snapshot, then for each encoder E: turn +DELTA, snapshot, turn -DELTA, snapshot
(DELTA defaults to 32 raw encoder units; each turn is a wake event plus the real event).
Encoder turns are raw panel bytes (tag 3, channel E-1, signed delta) via guirun --feed.
Buttons go through guirun --input with --panel-dwell 0, 2M instructions apart (with guirun's
default dwell of 16 chunks, button events were delivered about 9M instructions apart, so FUNC
was still held during the first encoder turns).
Snapshots and screen PNGs go to out/T3.3/snaps/PAGE_NAME/. Never touches hardware.
"""
import argparse
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DK = os.path.join(ROOT, 'vendor', 'digikit')
SYX = os.path.join(ROOT, 'elektron-firmware', 'Digitone_II_OS1_12.syx')
BASE = os.path.join(ROOT, 'out', 'T3.3', 'snaps', 'main720M.snap')
M = 1_000_000


def main():
    p = argparse.ArgumentParser()
    p.add_argument('page')
    p.add_argument('--nav', required=True,
                   help='comma list of steps; "a+b" = hold a, tap b, release a; "a" = tap a; '
                        '"eN:D" = turn encoder N by D')
    p.add_argument('--encoders', default='1-9')
    p.add_argument('--delta', type=int, default=32)
    p.add_argument('--snapshot', default=BASE)
    p.add_argument('--pairs', type=int, default=1,
                   help='up/down pairs per encoder (2 catches a first turn that only woke the encoder)')
    p.add_argument('--extra', action='append', default=[],
                   help='extra guirun argument, e.g. --extra=--watch=0x800047b2:0xca=fxrow')
    a = p.parse_args()
    lo, hi = (int(x) for x in a.encoders.split('-'))
    out = os.path.join(ROOT, 'out', 'T3.3', 'snaps', a.page)
    os.makedirs(out, exist_ok=True)
    args = []
    t = 2 * M
    for step in a.nav.split(','):
        if step.startswith('e'):        # "e2:+32": turn encoder 2 by +32 (raw panel bytes)
            enc, delta = step[1:].split(':')
            args += ['--feed', '%d:%02x%02x' % (t, 0x30 | (int(enc) - 1), int(delta) & 0xff)]
            t += 2 * M
            continue
        keys = [int(k) for k in step.split('+')]
        if len(keys) == 2:
            mod, key = keys
            args += ['--input', '%d:press:%d' % (t, mod), '--input', '%d:press:%d' % (t + 2 * M, key),
                     '--input', '%d:release:%d' % (t + 4 * M, key),
                     '--input', '%d:release:%d' % (t + 6 * M, mod)]
            t += 10 * M
        else:
            args += ['--input', '%d:press:%d' % (t, keys[0]),
                     '--input', '%d:release:%d' % (t + 2 * M, keys[0])]
            t += 6 * M
    t += 4 * M

    def save(name, when):
        return ['--save-at', '%d:%s/%s.snap' % (when, out, name),
                '--png-at', '%d:%s/%s.png' % (when, out, name)]

    args += save('s00-base', t)
    plan = []
    for e in range(lo, hi + 1):
        for sign, label in [(s_, '%s%s' % (l_, '' if n_ == 0 else n_ + 1))
                            for n_ in range(a.pairs) for s_, l_ in ((1, 'up'), (-1, 'down'))]:
            d = sign * a.delta
            t += 2 * M
            # Measured on the delay page: the first events of a turn after a pause only wake the
            # encoder and change nothing; later events in the same direction change the value
            # (+32 raw units = +10 on a 0..127 parameter) and FUN_4002585a writes the mirror. So
            # each step is a wake event and the real event, both DELTA, 1M instructions apart.
            args += ['--feed', '%d:%02x%02x' % (t, 0x30 | (e - 1), d & 0xff),
                     '--feed', '%d:%02x%02x' % (t + M, 0x30 | (e - 1), d & 0xff)]
            t += 6 * M
            name = 'e%d-%s' % (e, label)
            args += save(name, t)
            plan.append((name, e, d))
    limit = t + 2 * M
    cmd = ['uv', 'run', 'python', 'tools/guirun.py', a.snapshot, '--syx', SYX,
           '--limit', str(limit), '--panel-dwell', '0'] + args + a.extra
    with open(os.path.join(out, 'plan.txt'), 'w') as f:
        f.write(' '.join(cmd) + '\n')
        for name, e, d in plan:
            f.write('%s encoder %d delta %+d\n' % (name, e, d))
    env = dict(os.environ, DT2_SECTIONS='out/sections/dn2-1.12')
    with open(os.path.join(out, 'guirun.log'), 'w') as log:
        r = subprocess.run(cmd, cwd=DK, env=env, stdout=log, stderr=subprocess.STDOUT)
    return r.returncode


if __name__ == '__main__':
    sys.exit(main())
