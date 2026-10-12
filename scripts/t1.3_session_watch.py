#!/usr/bin/env python3
"""T1.3: digikit's tools/guirun.py with write watches over large ranges.

guirun's --watch takes at most 0x1000 bytes. This wrapper runs guirun
unchanged, with that one limit lifted (up to 0x10000000 bytes), so a write
watch can cover a whole candidate free area. Every other option is
guirun's: --input for key presses, --save-at for a snapshot at the end,
--png-at for screens. Each watch prints its first --watch-max hits (address,
size, value, PC, stack scan) and, at the end, its total hit count.

Run from vendor/digikit, in its environment:

    cd vendor/digikit && DT2_SECTIONS=out/sections/dn2-1.12 uv run python \\
      ../../scripts/t1.3_session_watch.py out/snapshots/dn2-1.12/boot400M.snap \\
      --syx ../../elektron-firmware/Digitone_II_OS1_12.syx --limit 800M \\
      --watch 0x46710000:0x18e0000=ddr --input 330M:press:25 ...

Nothing in digikit is edited; guirun is imported as a module.
"""
import argparse
import os
import sys

DIGIKIT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       'vendor', 'digikit')
sys.path.insert(0, os.path.join(DIGIKIT, 'tools'))
sys.path.insert(0, DIGIKIT)

import guirun  # noqa: E402

MAX = 0x10000000


def parse_watch(spec):
    rest, _, name = spec.partition('=')
    addr_str, len_str = rest.split(':', 1)
    addr, length = int(addr_str, 0), int(len_str, 0)
    if not 1 <= length <= MAX:
        raise argparse.ArgumentTypeError('--watch LEN must be 1..0x%x, got %r' % (MAX, len_str))
    return addr, length, name or '0x%08x' % addr


guirun.parse_watch = parse_watch

if __name__ == '__main__':
    guirun.main()
