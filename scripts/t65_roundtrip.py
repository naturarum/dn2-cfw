#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""T6.5: what a kit save and reload keep, run on the stock code from a snapshot.

    cd vendor/digikit && uv run python ../../scripts/t65_roundtrip.py SNAP [--json OUT]

Loads the snapshot into a fresh digikit Machine (no run-time hooks: emu.snapshot.restore), then
host-calls the OS's own routines on the current kit's live FX setup (live kit + 0x48e4, 101 words
by k):
  1. FUN_400dcf34(record, live)  the FX part of the kit serializer (kitStorage's
     fxSetupStorage_v0_t, 0xa0 bytes): the record is pre-filled with 0xa5 so the bytes it never
     writes show;
  2. every live word k 0..100 is changed (+0x11 << 8);
  3. FUN_400dcef0(live, record)  the FX part of the kit loader;
  4. which k came back to their saved value, which kept the edited one.
Then the same for the kit as a whole: FUN_400ddfa8 (the kit serializer) into a scratch kit
record and FUN_400dddb6 (the kit loader) back, when their argument order is the one checked here
(kit serializer (stored, live kit), loader (live kit, stored)). Nothing is written to the
snapshot file.
"""
import json
import os
import struct
import sys

DIGIKIT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'vendor', 'digikit')
sys.path.insert(0, DIGIKIT)

KITPTR, FXOFF = 0x800052a0, 0x48e4
FX_SAVE, FX_LOAD = 0x400dcf34, 0x400dcef0
SCRATCH = 0x10100000


def main():
    argv = sys.argv[1:]
    snap = argv[0]
    out = argv[argv.index('--json') + 1] if '--json' in argv else None
    from emu import snapshot, harness
    m, extra, regs = snapshot.restore(snap)
    uc = m.uc
    m.ensure(SCRATCH)
    rd16 = lambda a: struct.unpack('>H', bytes(uc.mem_read(a, 2)))[0]   # noqa: E731
    rd32 = lambda a: struct.unpack('>I', bytes(uc.mem_read(a, 4)))[0]   # noqa: E731
    kit = rd32(KITPTR)
    live = kit + FXOFF
    before = [rd16(live + 2 * k) for k in range(101)]
    rec = SCRATCH
    uc.mem_write(rec, b'\xa5' * 0xa0)
    r1 = harness.call(m, FX_SAVE, [rec, live])
    stored = bytes(uc.mem_read(rec, 0xa0))
    for k in range(101):
        uc.mem_write(live + 2 * k, struct.pack('>H', (before[k] + 0x1100) & 0xffff))
    r2 = harness.call(m, FX_LOAD, [live, rec])
    after = [rd16(live + 2 * k) for k in range(101)]
    back = [k for k in range(101) if after[k] == before[k]]
    kept_edit = [k for k in range(101) if after[k] == (before[k] + 0x1100) & 0xffff]
    other = [k for k in range(101) if k not in back and k not in kept_edit]
    untouched = [o for o in range(0, 0xa0, 2) if stored[o:o + 2] == b'\xa5\xa5']
    res = {'snap': snap, 'kit': '0x%08x' % kit, 'serializer_returned': r1, 'loader_returned': r2,
           'k_restored': back, 'k_not_restored': kept_edit, 'k_other': {k: (before[k], after[k]) for k in other},
           'record': stored.hex(), 'record_words_never_written': ['+0x%02x' % o for o in untouched],
           'live_before': before}

    def ranges(ks):
        out, s = [], None
        for k in ks + [None]:
            if s is None:
                s = p = k
            elif k is not None and k == p + 1:
                p = k
            else:
                out.append('%d' % s if s == p else '%d..%d' % (s, p))
                s = p = k
        return ' '.join(x for x in out if x != 'None')
    print('kit 0x%08x: serializer -> %d, loader -> %d' % (kit, r1, r2))
    print('k restored by the loader (saved and reloaded): %s (%d)' % (ranges(back), len(back)))
    print('k that kept the edit (not saved, not reloaded): %s (%d)' % (ranges(kept_edit), len(kept_edit)))
    print('k with another value: %s' % res['k_other'])
    print('record words the serializer never wrote: %s' % ' '.join(res['record_words_never_written']))
    if out:
        json.dump(res, open(out, 'w'), indent=1)
    return 0


if __name__ == '__main__':
    sys.exit(main())
