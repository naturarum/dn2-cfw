#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""MR03: the reverb page's state (morph-names 0.1) in a booted EEEM emulator snapshot, a new kit's reverb values, and a
kit's reverb values through the stock kit serializer and loader (notes/MR03-eeem.md); scripts/sp02_emucheck.py's checks
for MRB's page.

    cd vendor/digikit && uv run python ../../scripts/mr03_emucheck.py EEEM_SNAP --map EEEM_MAP.json \\
        [--compare DJ01_SNAP] [--kit-end KIT_END_SNAP] [--json OUT]

Each snapshot is loaded into a fresh digikit Machine (emu.snapshot.restore; no run-time hooks) and the OS's own
routines are host-called (emu.harness.call), as scripts/t65_roundtrip.py does:
  - page record 18 (SEND FX 2): its eight knobs and LEVEL (stock: G empty); the display entries of rows 0x7d, 0x7e
    (plain knobs) and 0x83 (stock); the descriptor rows' labels and long names as the RAM holds them;
  - a new kit's FX values: 0x400e7140(setup), the stock routine that fills an FX setup with each row's default (the
    new kit / sound / machine change path, notes/T6.1-table-growth-study.md 2.10), called on a scratch setup: k 41..48
    (PRE DEC CHAR SIZE HPF TONE VOL, routing); with --compare the same on another build;
  - with --kit-end (the end of scripts/sp02-kit.script): the live kit's k 41..48, then the FX part of the kit
    serializer FUN_400dcf34(record, live) into a scratch record, every live word changed, the FX loader
    FUN_400dcef0(live, record) back: k 41..48 restored (stored ids 17..24 in the record).
Read-only on the snapshot files.
"""
import argparse
import json
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'vendor', 'digikit'))
from emu import snapshot, harness  # noqa: E402

KITPTR, FXOFF = 0x800052a0, 0x48e4
FX_DEFAULTS, FX_SAVE, FX_LOAD = 0x400e7140, 0x400dcf34, 0x400dcef0
PAGES, DISP, DROW = 0x42433c00, 0x4243425c, 0x44
SCRATCH = 0x10100000
K = range(41, 49)
NAMES = ('PRE', 'DEC', 'CHAR', 'SIZE', 'HPF', 'TONE', 'VOL', 'ROUTING')
STR = 0x28, 0x30


class Snap:
    def __init__(self, path):
        self.m, _, _ = snapshot.restore(path)
        self.uc = self.m.uc
        self.m.ensure(SCRATCH)

    def rd(self, a, n):
        return bytes(self.uc.mem_read(a, n))

    def u16(self, a):
        return struct.unpack('>H', self.rd(a, 2))[0]

    def u32(self, a):
        return struct.unpack('>I', self.rd(a, 4))[0]

    def defaults(self):
        """a scratch FX setup (101 words by k + the routing long at +0xca) filled by the stock defaults routine"""
        self.uc.mem_write(SCRATCH, b'\xa5' * 0x100)
        harness.call(self.m, FX_DEFAULTS, [SCRATCH])
        return {n: self.u16(SCRATCH + 2 * k) for n, k in zip(NAMES, K)}


def knob(v):
    return v / 256 if v % 256 else v >> 8


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('snap')
    ap.add_argument('--map', required=True)
    ap.add_argument('--compare')
    ap.add_argument('--kit-end')
    ap.add_argument('--json')
    a = ap.parse_args()
    mp = {k: int(v, 16) for k, v in json.load(open(a.map)).items()}
    s = Snap(a.snap)
    res, ok = {'snap': a.snap}, {}
    page = [s.u32(PAGES + 0x2c * 18 + 8 + 4 * i) for i in range(9)]
    res['page18_rows'] = ['0x%x' % x for x in page]
    disp = {r: [s.u32(DISP + DROW * r + 4 * i) for i in range(17)] for r in (0x83, 0x6c, 0x7d, 0x7e)}
    res['display'] = {'0x%x' % r: ['%08x' % x for x in v] for r, v in disp.items()}
    ok['page 18 = 7b 7c 7d 7e 7f 80 0 0, LEVEL 82 (stock: G empty, row 0x83 not shown)'] = (
        page == [0x7b, 0x7c, 0x7d, 0x7e, 0x7f, 0x80, 0, 0, 0x82])
    ok['rows 0x7d and 0x7e (CHAR, SIZE): the plain knob\'s picture (0x400c0986), as stock; row 0x83 stock (flags 0xc)'] = (
        disp[0x7d][8] == 0x400c0986 and disp[0x7e][8] == 0x400c0986 and disp[0x83][0] == 0xc)

    def cstr(a):
        return s.rd(a, 24).split(b'\0')[0].decode()
    DESC, ROW = 0x401f83a4, 0x3c
    names = {r: (cstr(s.u32(DESC + ROW * r + 0x30)), cstr(s.u32(DESC + ROW * r + 0x28))) for r in range(0x7b, 0x84)}
    res['row_names'] = {'0x%x' % r: v for r, v in names.items()}
    ok['the rows\' labels and long names in RAM: PRE DEC CHAR SIZE HPF TONE (Character, Size, Tone), 0x83 REV'] = (
        [names[r][0] for r in range(0x7b, 0x81)] == ['PRE', 'DEC', 'CHAR', 'SIZE', 'HPF', 'TONE']
        and [names[r][1] for r in (0x7d, 0x7e, 0x80)] == ['Character', 'Size', 'Tone'] and names[0x83][0] == 'REV')
    nk = s.defaults()
    res['new_kit_reverb'] = {n: knob(v) for n, v in nk.items()}
    ok['a new kit\'s reverb: PRE 0, DEC 64, CHAR 64, SIZE 64, HPF 0, TONE 70, VOL 110 (stock), routing 1 (stock)'] = (
        [knob(nk[n]) for n in NAMES] == [0, 64, 64, 64, 0, 70, 110, 1])
    if a.compare:
        c = Snap(a.compare)
        res['compare_snap'] = a.compare
        res['compare_new_kit_reverb'] = {n: knob(v) for n, v in c.defaults().items()}
        ok['the compared build\'s new kit (the stock defaults): PRE 16, DEC 31, FREQ 64, GAIN 50, HPF 32, LPF 96, VOL 110, '
           'routing 1'] = list(res['compare_new_kit_reverb'].values()) == [16, 31, 64, 50, 32, 96, 110, 1]
    if a.kit_end:
        e = Snap(a.kit_end)
        uc = e.uc
        kit = e.u32(KITPTR)
        live = kit + FXOFF
        before = [e.u16(live + 2 * k) for k in range(101)]
        res['kit_end_live_reverb'] = {n: knob(before[k]) for n, k in zip(NAMES, K)}
        uc.mem_write(SCRATCH, b'\xa5' * 0xa0)
        harness.call(e.m, FX_SAVE, [SCRATCH, live])
        rec = e.rd(SCRATCH, 0xa0)
        for k in range(101):
            uc.mem_write(live + 2 * k, struct.pack('>H', (before[k] + 0x1100) & 0xffff))
        harness.call(e.m, FX_LOAD, [live, SCRATCH])
        after = [e.u16(live + 2 * k) for k in range(101)]
        res['kit_end_record_ids_17_24'] = [struct.unpack_from('>H', rec, 4 + 2 * i)[0] for i in range(17, 25)]
        res['kit_end_reverb_restored'] = [after[k] == before[k] for k in K]
        ok['the kit\'s reverb words k 41..48 (%s) saved by the kit serializer (stored ids 17..24) and restored by the '
           'loader' % ' '.join('%s %s' % (n, knob(before[k])) for n, k in zip(NAMES, K))] = (
            all(res['kit_end_reverb_restored'])
            and res['kit_end_record_ids_17_24'] == [before[k] for k in K])
    res['checks'] = ok
    for k, v in ok.items():
        print('%-4s %s' % ('ok' if v else 'FAIL', k))
    print(json.dumps({k: v for k, v in res.items() if k not in ('display', 'checks')}))
    if a.json:
        json.dump(res, open(a.json, 'w'), indent=1)
    return 0 if all(ok.values()) else 1


if __name__ == '__main__':
    sys.exit(main())
