#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""FL02: the running main OS's display entries and functions for the two FL02 changes, from an emulator snapshot of
FL02.syx (notes/FL02.md, notes/T5.6-flanger-retune.md section 7). Read-only on the snapshot.

    cd vendor/digikit && uv run python ../../scripts/fl02_emurows.py SNAP MAP.json OUT.json

  - the band rows 0x141..0x154: each display entry (param-grow's pg_xdisp) against the stock row it borrows (look):
    the same words but the flags (| 8) and the randomiser (cleared); the glyph function (+0x20) the borrowed row's,
    or comp-ui's compui_rat_draw on the three ratio rows once a band page has been drawn;
  - row 0x6a (the flanger's SPD): its display entry's +0x34 std::function (the FUNC + turn function): a heap functor,
    0, the stock manager 0x400bfbec, fls_func; the grown table's +0x34 formatter fls_fmt;
  - fls_fmt and fls_func run on the snapshot's machine (a scratch stack in free DDR): the text of every synced value and
    of a few free ones; FUNC + turn right and left from every synced value and from free values;
  - compui_rat_draw's frames: PICK as the stock Ratio function calls it, for values 1..8 (stock frames 0..7) and the
    1.00 frame's Bitmap object (vtable, 19 x 17, stride 1, the stock mask).
"""
import json
import os
import struct
import sys

sys.path.insert(0, os.getcwd())
from unicorn.m68k_const import UC_M68K_REG_A7, UC_M68K_REG_D0  # noqa: E402
from emu.snapshot import restore  # noqa: E402

DISP, XDISP, XROW, GROWN = 0x4243425c, 0x47d05a00, 0x44, 0x47d00000
SCRATCH = 0x47f80000
LOOK = {0x141: 0x96, 0x142: 0x9a, 0x143: 0x97, 0x144: 0x98, 0x145: 0x99, 0x146: 0x99}
for b in (6, 12):
    for j in range(6 if b == 6 else 5):
        LOOK[0x141 + b + j] = LOOK[0x141 + j]
LOOK.update({0x152: 0x9e, 0x153: 0x9e, 0x154: 0x9e})
RAT_ROWS = (0x142, 0x148, 0x14e)
ANCHOR = [9, 23, 37, 52, 66, 80, 86, 94, 100, 108, 122, 126]
SYNC = [(a << 8) | i for i, a in enumerate(ANCHOR, 1)]
MANAGER, RAT_STOCK, RAT_STRIP = 0x400bfbec, 0x400bc2ea, 0x446458bc


def main():
    snap, mpath, out = sys.argv[1:4]
    mp = json.load(open(mpath))
    sym = {k: int(v, 16) for k, v in mp.items()}
    m, _x, _r = restore(snap)
    uc = m.uc
    rd = lambda a, n: bytes(uc.mem_read(a, n))                                # noqa: E731
    u32 = lambda a: struct.unpack('>I', rd(a, 4))[0]                          # noqa: E731
    rep = {'snap': os.path.abspath(snap), 'rows': {}, 'ok': {}}
    ok_rows = True
    for r in range(0x141, 0x155):
        e = struct.unpack('>17I', rd(XDISP + XROW * (r - 0x141), XROW))
        s = struct.unpack('>17I', rd(DISP + XROW * LOOK[r], XROW))
        want = list(s)
        want[0] = s[0] | 8
        want[9:13] = [0, 0, 0, 0]
        got = list(e)
        rat = r in RAT_ROWS
        if rat:
            want_inv = (RAT_STOCK, sym['compui_rat_draw'])
            okr = got[:8] + got[9:] == want[:8] + want[9:] and got[8] in want_inv
        else:
            okr = got == want
        ok_rows &= okr
        rep['rows']['%#x' % r] = {'look': '%#x' % LOOK[r], 'flags': got[0], 'glyph_fn': '%#x' % got[8],
                                  'glyph_fn_is': ('compui_rat_draw' if got[8] == sym['compui_rat_draw'] else
                                                  'the stock row %#x\'s' % LOOK[r] if got[8] == s[8] else '?'),
                                  'func_turn_fn': '%#x' % got[16], 'as_borrowed': okr}
    rep['ok']['band rows: the borrowed stock entries (flags | 8, no randomiser)'] = ok_rows
    rep['ok']['ratio rows: compui_rat_draw in place'] = all(
        u32(XDISP + XROW * (r - 0x141) + 0x20) == sym['compui_rat_draw'] for r in RAT_ROWS)
    rep['compui_rat_set'] = u32(sym['compui_rat_set'])
    # row 0x6a
    e = struct.unpack('>4I', rd(DISP + XROW * 0x6a + 0x34, 16))
    rep['row_0x6a_func_turn'] = ['%#x' % x for x in e]
    rep['ok']['row 0x6a +0x34: heap functor, 0, manager 0x400bfbec, fls_func'] = (
        0x44000000 <= e[0] < 0x46800000 and e[1] == 0 and e[2] == MANAGER and e[3] == sym['fls_func'])
    rep['row_0x6a_formatter'] = '%#x' % u32(GROWN + 0x6a * 0x3c + 0x34)
    rep['ok']['row 0x6a formatter (grown table) = fls_fmt'] = u32(GROWN + 0x6a * 0x3c + 0x34) == sym['fls_fmt']
    for a in (0x69, 0x6b, 0x6c, 0x71):
        rep['ok']['row %#x +0x34 FUNC function: stock (%#x)' % (a, u32(DISP + XROW * a + 0x40))] = (
            u32(DISP + XROW * a + 0x40) in (0x400bbd20, 0x400cb992))

    # run fls_fmt / fls_func / PICK on the machine
    for base in range(SCRATCH, SCRATCH + 0x4000, 0x1000):
        m.ensure(base)
    uc.mem_write(SCRATCH, bytes(0x4000))
    stop, buf, stack = SCRATCH + 0x3800, SCRATCH + 0x3000, SCRATCH + 0x2000
    uc.mem_write(stop, bytes.fromhex('4e71'))

    def call(fn, *args):
        sp = stack - 4 * (len(args) + 1)
        uc.mem_write(sp, struct.pack('>%dI' % (len(args) + 1), stop, *[x & 0xffffffff for x in args]))
        uc.reg_write(UC_M68K_REG_A7, sp)
        uc.emu_start(fn, stop, count=200000)
        return uc.reg_read(UC_M68K_REG_D0)

    def fmt(v):
        uc.mem_write(buf, bytes(32))
        call(sym['fls_fmt'], v, buf)
        return rd(buf, 32).split(b'\0')[0].decode('latin-1')
    rep['fls_fmt'] = {'%#06x' % v: fmt(v) for v in SYNC + [0, 0x0900, 0x0902, 0x2000, 0x2605, 0x5e00, 0x5f08, 0x7f00]}
    names = ["16/1", "8/1", "4/1", "2/1", "1/1", "1/2", "1/4.", "1/4", "1/8.", "1/8", "1/16", "1/32"]
    rep['ok']['fls_fmt: the 12 names, plain numbers otherwise'] = (
        [rep['fls_fmt']['%#06x' % v] for v in SYNC] == names
        and [rep['fls_fmt'][k] for k in ('0x0000', '0x0900', '0x0902', '0x2000', '0x2605', '0x5e00', '0x5f08',
                                         '0x7f00')] == ['0', '9', '9', '32', '38', '94', '95', '127'])
    steps = {}
    for v in SYNC + [0, 0x0800, 0x0900, 0x0902, 0x2000, 0x5e00, 0x5f08, 0x7e00, 0x7f00]:
        steps['%#06x' % v] = ['%#06x' % (call(sym['fls_func'], 0, v, d, 0, 0x7f00, 0x2000) & 0xffff) for d in (1, -1)]
    rep['fls_func'] = steps
    want = {}
    for i, v in enumerate(SYNC):
        want['%#06x' % v] = ['%#06x' % SYNC[min(11, i + 1)], '%#06x' % SYNC[max(0, i - 1)]]
    for v in (0, 0x0800, 0x0900, 0x0902, 0x2000, 0x5e00, 0x5f08, 0x7e00, 0x7f00):
        up = next((s for s in SYNC if s > v), SYNC[-1])
        dn = next((s for s in reversed(SYNC) if s < v), SYNC[0])
        want['%#06x' % v] = ['%#06x' % up, '%#06x' % dn]
    rep['ok']['fls_func: one step faster/slower from a synced value (held at the ends); from a free value the next '
              'synced value above/below'] = steps == want
    frames = [call(0x40115f88, RAT_STRIP, i - 1, 0) for i in range(1, 9)]
    begin = u32(RAT_STRIP)
    rep['rat_frames'] = ['%#x' % f for f in frames]
    rat_one = sym.get('comp-ui:rat_one')
    rep['ok']['the stock ratio frames 0..7 for values 1..8 (PICK as compui_rat_draw calls it)'] = (
        frames == [begin + 0x1c * j for j in range(8)])
    if rat_one:
        b1 = struct.unpack('>7I', rd(rat_one, 28))
        rep['rat_one_bitmap'] = ['%#x' % x for x in b1]
        st0 = struct.unpack('>7I', rd(begin, 28))
        rep['ok']['the 1.00 frame: a Bitmap like the stock frames (vtable, 19 x 17, stride 1, the same mask)'] = (
            b1[0] == st0[0] and b1[1:4] == st0[1:4] and b1[5] == st0[5] and b1[6] == 0)
    rep['all_ok'] = all(rep['ok'].values())
    json.dump(rep, open(out, 'w'), indent=1)
    print(json.dumps(rep['ok'], indent=1))
    print('all ok' if rep['all_ok'] else 'NOT OK')


if __name__ == '__main__':
    main()
