#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""T6.5: the sound randomiser 0x4004b0b4, run on the stock code from a snapshot.

    cd vendor/digikit && uv run python ../../scripts/t65_randomiser.py SNAP [--json OUT]

FUN_4004b0b4(sound) walks every row of the descriptor table (to the build's row count), and for
each row whose display entry has a randomiser (a std::function at +0x24) writes a random value
into the sound's slot k (sound + 0x14 + 2k), then names the sound "RANDOM". No key that reaches it
was found, so it is host-called here on a scratch sound (0x200 bytes filled with 0xa5) in a fresh
Machine loaded from the snapshot (emu.snapshot.restore: no run-time hooks). A write watch over
the whole of RAM below 0x48000000 records every store it makes outside the stack. The values
are random; what must match between two builds is the set of slots written (and nothing
written outside the scratch sound).
"""
import json
import os
import struct
import sys

DIGIKIT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'vendor', 'digikit')
sys.path.insert(0, DIGIKIT)

RANDOMISER = 0x4004b0b4
SOUND = 0x10200000


def main():
    argv = sys.argv[1:]
    snap = argv[0]
    out = argv[argv.index('--json') + 1] if '--json' in argv else None
    from emu import snapshot, harness
    from unicorn import UC_HOOK_MEM_WRITE
    m, extra, regs = snapshot.restore(snap)
    uc = m.uc
    # the ColdFire instructions Unicorn lacks (FF1, MOVEC), as every digikit run installs them
    img = bytes(uc.mem_read(0x40000400, 0x30c580))
    m.install_isa_patches_scoped(img, 0x40000400)
    m.ensure(SOUND)
    uc.mem_write(SOUND, b'\xa5' * 0x200)
    stack_lo, stack_hi = 0x10000000, 0x10100000
    writes = {}

    def wr(uc_, acc, addr, size, value, user):
        if stack_lo <= addr < stack_hi or SOUND <= addr < SOUND + 0x200:
            return
        writes[addr] = writes.get(addr, 0) + 1
    uc.hook_add(UC_HOOK_MEM_WRITE, wr, begin=0x40000000, end=0x47ffffff)
    harness.call(m, RANDOMISER, [SOUND])
    snd = bytes(uc.mem_read(SOUND, 0x200))
    ks = [k for k in range(0, (0x200 - 0x14) // 2) if snd[0x14 + 2 * k:0x16 + 2 * k] != b'\xa5\xa5']
    name = snd[4:20].split(b'\0')[0].decode('latin-1', 'replace')
    others = [o for o in range(0, 0x14) if snd[o] != 0xa5 and not 4 <= o < 4 + len(name) + 1]
    res = {'snap': snap, 'k_written': ks, 'name': name, 'other_bytes_written': others,
           'writes_outside_sound_and_stack': {'0x%08x' % a: n for a, n in sorted(writes.items())[:40]},
           'n_writes_outside': len(writes)}
    print('slots written: %d: %s' % (len(ks), ' '.join(map(str, ks))))
    print('name "%s"; other header bytes written: %s; writes elsewhere (not the stack): %d %s' % (
        name, others, len(writes), list(res['writes_outside_sound_and_stack'].items())[:6]))
    if out:
        json.dump(res, open(out, 'w'), indent=1)
    return 0


if __name__ == '__main__':
    sys.exit(main())
