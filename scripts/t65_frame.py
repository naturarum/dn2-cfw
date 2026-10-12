#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""T6.5: the ColdFire-to-DSP frame of a T6.5 build's snapshot: scripts/t33_frame.py (T3.3) with the
image check set to the build's own main OS.

    cd vendor/digikit && DT2_SECTIONS=../../out/T6.5/emu/GT1T/sections uv run python \\
        ../../scripts/t65_frame.py GT1T SNAP [SNAP ...] --passes 300 --out OUTDIR

t33_frame.py refuses any main OS but stock 1.12's (its addresses: the vector-191 handler
0x40025e36, the DSPI2 driver 0x400cf922, the TX buffer 0x80005e60, the mirrors). Those routines and
buffers are the same in FTN1 and the GT builds (none of their sites is in them), so the check is
pointed at the build's main OS sha256 (out/T6.5/NAME.main.bin, or T5.0b's FTN1.main.bin) and
nothing else changes. The handler runs PASSES times from the snapshot; the TX frame it builds
carries k 25..69 of the FX setup (smoothed), so a changed k 48 shows at frame word 0xa28 and a
changed k 87 does not show at all.
"""
import hashlib
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)


def main():
    name, argv = sys.argv[1], sys.argv[2:]
    path = os.path.join(ROOT, 'out', 'T5.0b', 'FTN1.main.bin') if name == 'FTN1' else \
        os.path.join(ROOT, 'out', 'T6.5', name + '.main.bin')
    import t33_frame
    t33_frame.MAIN_SHA = hashlib.sha256(open(path, 'rb').read()).hexdigest()[:8]
    sys.argv = [sys.argv[0]] + argv
    return t33_frame.main(argv)


if __name__ == '__main__':
    sys.exit(main())
