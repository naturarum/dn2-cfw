#!/usr/bin/env python3
"""T3.3 check: is every emulated global-block frame word the steady state of FUN_400db28e's filter
as digikit's Unicorn computes it (macw without the fractional <<1, macl >>32)?
  python3 scripts/t33_emac_model.py      (reads out/T3.3/frames/*/; writes nothing)
With the EMAC fractional mode as the ColdFire defines it, the filter has unity DC gain
(0x03d7 + 0x7c29 = 0x8000), so on the unit the same words settle at the raw value."""
import glob
import os


def emu_ss(r):
    s = 0
    for _ in range(400):
        s = (983 * r + ((0x7c290000 * s) >> 32)) & 0xffffffff
        if s & 0x80000000:
            s -= 1 << 32
    return ((s + 0x8000) >> 16) & 0xffff


root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ok = bad = 0
for f in sorted(glob.glob(os.path.join(root, 'out/T3.3/frames/*/*.raw.bin'))):
    d = os.path.dirname(f)
    if 'routing-play' in d:      # the handler did not return there (sequencer running)
        continue
    n = os.path.basename(f)[:-8]
    raw = open(f, 'rb').read()
    tx = open(os.path.join(d, n + '.tx.bin'), 'rb').read()
    for k in range(25, 70):
        r = int.from_bytes(raw[0xcc2 + 2 * k:0xcc4 + 2 * k], 'big')
        t = int.from_bytes(tx[0x9fa + 2 * (k - 25):0x9fc + 2 * (k - 25)], 'big')
        if emu_ss(r) == t:
            ok += 1
        else:
            bad += 1
print('global words: %d match the emulated-EMAC steady state, %d differ' % (ok, bad))
