#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""T3.4: read 32-bit words from the loaded dn2-1.12 DSP image at bare DM byte addresses
(L1 bare + 0x28000000 = loader address; DDR 0x8xxxxxxx as is). Prints hex and float.
Run from vendor/digikit: uv run python ../../scripts/t34_rd.py ADDR N [ADDR N ...]"""
import sys, struct
sys.path.insert(0, "tools")
import sharc
img = sharc.load("dn2-1.12")
mem = img._mem()
a = sys.argv[1:]
for i in range(0, len(a), 2):
    base = int(a[i], 16); n = int(a[i+1])
    for k in range(n):
        bare = base + 4 * k
        la = bare + 0x28000000 if bare < 0x400000 else bare
        raw = mem.read(la, 4)
        if raw is None:
            print('%08x  (not loaded)' % bare); continue
        v = int.from_bytes(raw, 'little')
        f = struct.unpack('<f', raw)[0]
        print('%08x  %08x  %g' % (bare, v, f))
    print('--')
