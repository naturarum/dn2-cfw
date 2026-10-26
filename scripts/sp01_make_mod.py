#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""SP01: write vendor/elekloader/examples/spring-names-dn2/mod.json, spring-names-dn2 0.1 (every site checked against
the stock image first).

    venv/bin/python scripts/sp01_make_mod.py [--list]

spring-names-dn2 0.1 names the reverb page for SPNG, the spring reverb SP01's DSP runs in the reverb slot
(tools/dspsplice/src/spng_glue.c): the page title, knobs C, D, F and G's labels and long names, DEC's and WDTH's value
text, the new-kit defaults of SPNG's init patch, row 0x83 ("Reverb FX Routing", k 48) as WDTH 0..127 on knob G (the
tick in spring_names.c places it), and C and D drawn as knobs instead of the stock shelf picture. Every stock value a
site replaces is checked here: the descriptor rows' names, groups, k, ranges, defaults and formatters, the page-title
`pea`, the draw's switch table and its page-id compare. The new-kit defaults are plain values in the rows: the stock
code that sets a new kit reads them there (notes/T6.1-table-growth-study.md 2.10; flanger-names-dn2 0.2 did the same
for the flanger's TIME), and param-grow's grown table copies the image's rows at boot. Saved kits keep their values.
"""
import json
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMAGE = os.path.join(ROOT, 'ele3_out', '02_type3.unpacked.bin')
OUT = os.path.join(ROOT, 'vendor', 'elekloader', 'examples', 'spring-names-dn2', 'mod.json')
LOAD = 0x40000400
DESC, ROW = 0x401f83a4, 0x3c
MAX, DEFAULT, LONG, GROUP, SHORT, FMT = 0x0c, 0x10, 0x28, 0x2c, 0x30, 0x34
FMT_NUMBER, FMT_DEC, FMT_PREPOST = 0x400e3030, 0x400e375e, 0x400e3592
TITLE_PEA = 0x400c9faa          # pea 0x40210b13 ("Reverb") -> page 18's long name (0x42433f1c), at boot
SWITCH_TABLE = 0x4006fb2c       # FxPageView::vslot04's switch on the row id - 0x75 (16-bit offsets)
SHELF_PAGE = 0x4006f8e8         # moveq #18,%d1: the page the shelf picture is drawn for

img = open(IMAGE, 'rb').read()


def rd(a, n):
    return img[a - LOAD:a - LOAD + n]


def u32(a):
    return struct.unpack('>I', rd(a, 4))[0]


def cstr(a):
    return rd(a, 40).split(b'\0')[0].decode()


sites = []

# ---- the page title: "Reverb" -> "Spring"
if rd(TITLE_PEA, 6) != bytes.fromhex('487940210b13') or cstr(0x40210b13) != 'Reverb':
    raise SystemExit('0x%08x: not the stock pea of "Reverb"' % TITLE_PEA)
if rd(TITLE_PEA + 6, 6) != bytes.fromhex('487942433f1c'):
    raise SystemExit('0x%08x: not followed by pea 0x42433f1c (page 18\'s long name)' % (TITLE_PEA + 6))
sites.append({'addr': '0x%08x' % TITLE_PEA, 'stock': rd(TITLE_PEA, 6).hex(), 'op': 'keep2', 'target': 'snm_spring',
              'note': 'page 18 (SEND FX 2) title at boot: "Reverb"'})

# ---- the rows: (row, k, stock long, stock short, stock max, stock default, stock formatter,
#                 new long sym, new short sym, new max, new default, new formatter)
ROWS = [
    (0x7b, 41, 'Pre-delay', 'PRE', 127, 16, FMT_NUMBER, None, None, None, 0, None),
    (0x7c, 42, 'Decay Time', 'DEC', 127, 31, FMT_DEC, None, None, None, 64, FMT_NUMBER),
    (0x7d, 43, 'FB Shelving Freq', 'FREQ', 127, 64, FMT_NUMBER, 'snm_mode', 'snm_mode_s', None, None, None),
    (0x7e, 44, 'FB Shelving Gain', 'GAIN', 127, 50, FMT_NUMBER, 'snm_dwel', 'snm_dwel_s', None, 64, None),
    (0x7f, 45, 'Input HPF', 'HPF', 127, 32, FMT_NUMBER, None, None, None, 0, None),
    (0x80, 46, 'Input LPF', 'LPF', 127, 96, FMT_NUMBER, 'snm_tone', 'snm_tone_s', None, 127, None),
    (0x83, 48, 'Reverb FX Routing', 'REV', 1, 1, FMT_PREPOST, 'snm_wdth', 'snm_wdth_s', 127, 96, FMT_NUMBER),
]
for r, k, lname, sname, mx, df, fmt, nl, ns, nmx, ndf, nfmt in ROWS:
    a = DESC + r * ROW
    grp, kk, mn, smx, sdf = struct.unpack('>iiiii', rd(a, 20))
    got = (grp, kk, mn, smx, sdf, cstr(u32(a + LONG)), cstr(u32(a + GROUP)), cstr(u32(a + SHORT)), u32(a + FMT))
    want = (17, k, 0, mx << 8, df << 8, lname, 'Reverb', sname, fmt)
    if got != want:
        raise SystemExit('row 0x%x: %r, expected %r' % (r, got, want))
    if nl:
        sites.append({'addr': '0x%08x' % (a + LONG), 'stock': rd(a + LONG, 4).hex(), 'op': 'ptr', 'target': nl,
                      'note': 'row 0x%x +0x28 long name: "%s"' % (r, lname)})
        sites.append({'addr': '0x%08x' % (a + SHORT), 'stock': rd(a + SHORT, 4).hex(), 'op': 'ptr', 'target': ns,
                      'note': 'row 0x%x +0x30 knob label: "%s"' % (r, sname)})
    if nmx is not None:
        sites.append({'addr': '0x%08x' % (a + MAX), 'stock': rd(a + MAX, 4).hex(), 'op': 'bytes',
                      'new': '%08x' % (nmx << 8), 'note': 'row 0x%x +0x0c maximum %d -> %d' % (r, mx, nmx)})
    if ndf is not None:
        sites.append({'addr': '0x%08x' % (a + DEFAULT), 'stock': rd(a + DEFAULT, 4).hex(), 'op': 'bytes',
                      'new': '%08x' % (ndf << 8), 'note': 'row 0x%x +0x10 new-kit default %d -> %d' % (r, df, ndf)})
    if nfmt is not None:
        sites.append({'addr': '0x%08x' % (a + FMT), 'stock': rd(a + FMT, 4).hex(), 'op': 'bytes',
                      'new': '%08x' % nfmt, 'note': 'row 0x%x +0x34 value text 0x%08x -> 0x%08x (plain number)'
                      % (r, fmt, nfmt)})

# ---- C and D as knobs: the draw's switch (row - 0x75 = 0..11; 16-bit offsets from the table) and the shelf picture
offs = struct.unpack('>12h', rd(SWITCH_TABLE, 24))
if offs != (0x1e, 0x24, 0xa8, 0xa8, 0xa8, 0xa8, 0xa8, 0xa8, 0x2c, 0x18, 0x1e, 0x24):
    raise SystemExit('the FX page draw\'s switch table is not stock: %r' % (offs,))
if rd(SWITCH_TABLE - 0x1a, 0x1a) != bytes.fromhex('20037a0b0680ffffff8bba806500 00b4303b0a0848c04efb0802'.replace(' ', '')):
    raise SystemExit('the code before the switch table is not the stock switch on the row id')
for r in (0x7d, 0x7e):
    a = SWITCH_TABLE + 2 * (r - 0x75)
    sites.append({'addr': '0x%08x' % a, 'stock': rd(a, 2).hex(), 'op': 'bytes', 'new': '00a8',
                  'note': 'FX page draw switch, row 0x%x: the shelf picture\'s case -> the plain knob\'s (0xa8)' % r})
if rd(SHELF_PAGE - 0xc, 0x10) != bytes.fromhex('20522f0a206800884e90588f7212b280'):
    raise SystemExit('0x%08x: not the stock page-id compare of the shelf picture' % SHELF_PAGE)
sites.append({'addr': '0x%08x' % SHELF_PAGE, 'stock': rd(SHELF_PAGE, 2).hex(), 'op': 'bytes', 'new': '727f',
              'kind': 'code', 'note': 'moveq #18,%d1 -> moveq #127,%d1: no page draws the shelf picture'})

strip = lambda ss: [{k: v for k, v in s.items() if k != 'note'} for s in ss]      # noqa: E731
mod = {
    'id': 'spring-names',
    'version': '0.1',
    'title': 'Spring names (SP01)',
    'category': 'Examples',
    'license': 'GPL-2.0-or-later',
    'author': 'naturarum',
    'description': 'EXPERIMENT: emulator-tested only. For dn2-cfw\'s SP01 build, whose DSP runs SPNG, a spring '
                   'reverb, in the reverb slot: the reverb page is titled Spring and reads PRE DEC MODE DWEL HPF TONE '
                   'WDTH. C (was FREQ) is MODE, the springs\' character; D (was GAIN) is DWEL, the drive into the '
                   'tank; F (was LPF) is TONE, the low-pass in the loop; the hidden "Reverb FX Routing" row (k 48, '
                   'stored with the kit, never shown, no CC) becomes WDTH 0..127 with the chorus WDTH\'s picture on '
                   'the empty knob G. C and D are drawn as knobs instead of the stock shelf picture; DEC shows a plain '
                   'number (no INF); a new kit starts at the spring\'s init patch (PRE 0, DEC 64, MODE 64, DWEL 64, '
                   'HPF 0, TONE 127, WDTH 96). Every knob keeps its MIDI CC, storage and DSP frame word; old kits '
                   'keep their values (WDTH 1: almost mono). On a stock DSP the names would be wrong.',
    'device': 'digitone-mk2',
    'os': '1.12',
    'sources': ['spring_names.c'],
    'sites': strip(sites),
    'subscribe': [{'event': 'ev_tick', 'fn': 'snm_tick', 'order': 50}],
    'exports': ['snm_state', 'snm_ticks'],
    'requires': ['core'],
}

if __name__ == '__main__':
    if '--list' in sys.argv:
        for s in sites:
            print(s['addr'], s['op'], s.get('target', s.get('new')), '-', s['note'])
        sys.exit(0)
    with open(OUT, 'w') as f:
        json.dump(mod, f, indent=1, ensure_ascii=False)
        f.write('\n')
    print('%s: %d sites' % (os.path.relpath(OUT, ROOT), len(sites)))
