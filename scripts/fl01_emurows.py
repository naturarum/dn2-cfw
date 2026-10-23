#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
# FL01: row 0x6b (the chorus page's C) as the running main OS holds it: the image's table and the grown table pg_boot
# built in RAM (0x47d00000), from the emulator's warm snapshot. Read-only.
#   cd vendor/digikit && uv run python ../../scripts/fl01_emurows.py ../../out/FL01/emu/warm800M.snap ../../out/FL01/emu-FL01-row6b.json
import json, os, struct, sys
sys.path.insert(0, os.getcwd())
from emu.snapshot import restore
snap, out = sys.argv[1], sys.argv[2]
m, _x, _r = restore(snap)
rd = lambda a, n: bytes(m.uc.mem_read(a, n))
def cstr(a):
    return rd(a, 24).split(b'\0')[0].decode('latin-1')
res = {}
for name, base in (('image table 0x401f83a4', 0x401f83a4), ('grown table 0x47d00000', 0x47d00000)):
    for r in (0x69, 0x6a, 0x6b, 0x6c):
        d = rd(base + r * 0x3c, 0x3c)
        g, k, mn, mx, df = struct.unpack('>5i', d[:20])
        nm, gn, sh = struct.unpack('>3I', d[0x28:0x34])
        res.setdefault(name, {})['0x%x' % r] = {'group': g, 'k': k, 'max': mx, 'default': df, 'default_knob': df / 256,
                                                'name': cstr(nm), 'short': cstr(sh)}
json.dump(res, open(out, 'w'), indent=1)
print(json.dumps(res, indent=1))
