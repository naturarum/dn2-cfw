#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""T5.3: the ColdFire-to-DSP frame of an SE01-main snapshot: scripts/t33_frame.py (T3.3) with the image
check set to the build's own main OS, as scripts/t65_frame.py does for the T6.5 builds.

    cd vendor/digikit && DT2_SECTIONS=../../out/T5.3/SE01-main/emu/SE1M/sections uv run python \\
        ../../scripts/t53_frame.py SE1M SNAP [SNAP ...] --passes 300 --out OUTDIR

NAME is SE1M (out/T5.3/SE01-main/main.bin) or GT01 (out/T6.5/GT01.main.bin). The vector-191 handler,
the DSPI2 driver and the buffers t33_frame.py uses are untouched by every SE01 site (fx-echo's are
names, two descriptor fields, a page-id call in the FX page's draw and an ev_tick handler). The
frame carries k 25..69 of the FX setup, smoothed, at 0x9fa + 2 (k - 25): MODE (k 33) at 0xa0a,
AGE (k 40) at 0xa18.
"""
import hashlib
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)


def main():
    name, argv = sys.argv[1], sys.argv[2:]
    path = os.path.join(ROOT, 'out', 'T6.5', 'GT01.main.bin') if name == 'GT01' else \
        os.path.join(ROOT, 'out', 'T5.3', 'SE01-main', 'main.bin')
    import t33_frame
    t33_frame.MAIN_SHA = hashlib.sha256(open(path, 'rb').read()).hexdigest()[:8]
    return t33_frame.main(argv)


if __name__ == '__main__':
    sys.exit(main())
