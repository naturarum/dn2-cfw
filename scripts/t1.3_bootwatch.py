#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""T1.3: digikit's tools/bootwatch.py, unchanged, with one fix around it.

bootwatch reads every watched range back after the run. A range that holds a
page the boot never touched is still unmapped then (digikit maps a page on its
first access), so that read raises UC_ERR_READ_UNMAPPED and the run ends with
no summary and no JSON. This wrapper lets the run finish, prints which 1 MB
pages of each watched range were mapped (touched) at the stop, then maps the
rest as zero pages so bootwatch's own read-back and JSON go through.

Run from vendor/digikit, in its environment, with bootwatch's arguments:

    cd vendor/digikit && DT2_SECTIONS=out/sections/dn2-1.12 uv run python \\
      ../../scripts/t1.3_bootwatch.py --limit 400000000 --syx ../../elektron-firmware/Digitone_II_OS1_12.syx \\
      --watch 0x46710000:0x18e0000=ddr ... --json OUT

Nothing in digikit is edited; bootwatch and emu.dspboot are imported.
"""
import os
import sys

DIGIKIT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       'vendor', 'digikit')
sys.path.insert(0, os.path.join(DIGIKIT, 'tools'))
sys.path.insert(0, DIGIKIT)

import bootwatch  # noqa: E402
from emu import dspboot  # noqa: E402

PAGE = 0x100000
_run = dspboot.run


def run(*a, **k):
    m, st, stop = _run(*a, **k)
    args = bootwatch.parse_args()
    mapped = set(m.mapped)              # before any page is mapped below
    for addr, length, name in args.watch:
        pages = range(addr - addr % PAGE, addr + length, PAGE)
        touched = [p for p in pages if p in mapped]
        print('pages %s 0x%08x+0x%x: %d of %d mapped at the stop%s'
              % (name, addr, length, len(touched), len(pages),
                 (' (' + ','.join('0x%08x' % p for p in touched) + ')') if touched else ''))
        for p in pages:
            m.ensure(p)
    return m, st, stop


dspboot.run = run

if __name__ == '__main__':
    raise SystemExit(bootwatch.main())
