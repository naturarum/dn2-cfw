#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""T5.0: write vendor/elekloader/examples/flanger-ui-dn2/mod.json (sites read from the stock image).

    venv/bin/python scripts/t50_make_mod.py

The sites only carry the stock pointer or instruction they replace (4 or 6 bytes each), never
Elektron's icon data: the glyphs are replaced at run time by flanger_ui.c (notes/T5.0).
"""
import json
import os
import struct

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMAGE = os.path.join(ROOT, 'ele3_out', '02_type3.unpacked.bin')
OUT = os.path.join(ROOT, 'vendor', 'elekloader', 'examples', 'flanger-ui-dn2', 'mod.json')
LOAD = 0x40000400
DESC, ROW = 0x401f83a4, 0x3c
LONG, GROUP, SHORT = 0x28, 0x2c, 0x30

img = open(IMAGE, 'rb').read()


def rd(a, n):
    return img[a - LOAD:a - LOAD + n]


def cstr(a):
    return rd(a, 40).split(b'\0')[0].decode()


sites = []


def ptr(row, off, sym, expect):
    a = DESC + row * ROW + off
    old = struct.unpack('>I', rd(a, 4))[0]
    if cstr(old) != expect:
        raise SystemExit('row %#x +%#x: %r, expected %r' % (row, off, cstr(old), expect))
    sites.append({'addr': '0x%08x' % a, 'stock': rd(a, 4).hex(), 'op': 'ptr', 'target': sym,
                  'note': 'row 0x%x +0x%x: "%s"' % (row, off, expect)})


def keep2(a, sym, expect):
    b = rd(a, 6)
    if b[:2] != b'\x48\x79' or cstr(struct.unpack('>I', b[2:])[0]) != expect:
        raise SystemExit('0x%08x: not pea "%s"' % (a, expect))
    sites.append({'addr': '0x%08x' % a, 'stock': b.hex(), 'op': 'keep2', 'target': sym,
                  'note': 'pea "%s"' % expect})


# the chorus send (track FX page G) and the other chorus sends
ptr(0x43, LONG, 'flui_send', 'Chorus Send')
ptr(0x43, SHORT, 'flui_send_s', 'CHR')
ptr(0x6f, LONG, 'flui_mix', 'Chorus Mix Vol.')       # FX MIXER page
ptr(0x6f, SHORT, 'flui_send_s', 'CHR')
ptr(0x8b, LONG, 'flui_in', 'In Chorus Send')          # external input mixer
ptr(0x8b, SHORT, 'flui_send_s', 'CHR')
ptr(0x8c, LONG, 'flui_in_l', 'In Chorus Send L')
ptr(0x8c, SHORT, 'flui_in_l_s', 'LCHR')
ptr(0x8d, LONG, 'flui_in_r', 'In Chorus Send R')
ptr(0x8d, SHORT, 'flui_in_r_s', 'RCHR')
# the chorus page's knobs C and D
ptr(0x6b, LONG, 'flui_fdbk', 'High-pass')
ptr(0x6b, SHORT, 'flui_fdbk_s', 'HPF')
ptr(0x6c, LONG, 'flui_time', 'Width')
ptr(0x6c, SHORT, 'flui_time_s', 'WDTH')
# the group name of every chorus parameter
for r in range(0x69, 0x71):
    ptr(r, GROUP, 'flui_title', 'Chorus')
# page title (the page table's long name, built by the static initializer 0x400c2c4e),
# the page table's short name, and the box the FX page draws at its left (0x4006f4a8)
keep2(0x400ca018, 'flui_title', 'Chorus')
keep2(0x400c9fba, 'flui_box', 'CHO')
keep2(0x4006f4f8, 'flui_box', 'CHO')
# the LFO DEST browser's group-name table (0x401f7b04, indexed by group - 5): group 0x10 (chorus)
# holds the "ERR" fallback in stock; it shows only if chorus parameters are listed as destinations
a = 0x401f7b04 + 4 * (0x10 - 5)
if cstr(struct.unpack('>I', rd(a, 4))[0]) != 'ERR':
    raise SystemExit('0x%08x: not the ERR fallback' % a)
sites.append({'addr': '0x%08x' % a, 'stock': rd(a, 4).hex(), 'op': 'ptr', 'target': 'flui_send_s',
              'note': 'DEST browser group 0x10 name ("ERR" in stock)'})

mod = {
    'id': 'flanger-ui',
    'version': '0.1',
    'title': 'Flanger page (UI test)',
    'category': 'Examples',
    'license': 'GPL-2.0-or-later',
    'author': 'naturarum',
    'description': 'Renames the chorus page and every chorus send to a flanger and redraws three '
                   'glyphs (the send, and the page\'s knobs C and D as FDBK and TIME). UI only: '
                   'the DSP still runs the stock chorus. A test of how the Digitone II draws its '
                   'pages; emulator-tested only.',
    'device': 'digitone-mk2',
    'os': '1.12',
    'sources': ['flanger_ui.c'],
    'subscribe': [{'event': 'ev_tick', 'fn': 'flui_tick', 'order': 50}],
    'exports': ['flui_state', 'flui_ticks'],
    'sites': [{k: v for k, v in s.items() if k != 'note'} for s in sites],
    'requires': ['core'],
}
with open(OUT, 'w') as f:
    json.dump(mod, f, indent=1)
    f.write('\n')
for s in sites:
    print('%s %-5s %-12s %s' % (s['addr'], s['op'], s['target'], s['note']))
print('%d sites -> %s' % (len(sites), OUT))


# ------------------------------------------------------------------ fx-page2-dn2
# A second chorus page in the SEND FX view (notes/T5.0 section 0). The 8 knobs are descriptor
# rows 1-5 and 0xb-0xd ("Error" rows no page shows) made into FX parameters (group 0x10) with
# FX-setup words k 70-77; their display rows are copied at run time by fxpage2.c.
OUT2 = os.path.join(ROOT, 'vendor', 'elekloader', 'examples', 'fx-page2-dn2', 'mod.json')
ROWS2 = [0x01, 0x02, 0x03, 0x04, 0x05, 0x0b, 0x0c, 0x0d]
FMT_NUM, FMT_WAVE = 0x400e3030, 0x400e3610          # plain number; LFO waveform names
sites = []
for i, r in enumerate(ROWS2):
    a = DESC + r * ROW
    old = rd(a, 0x28)
    if struct.unpack('>I', old[:4])[0] != 0xffffffff or cstr(struct.unpack('>I', rd(a + LONG, 4))[0]) != 'Error':
        raise SystemExit('row %#x is not an "Error" row' % r)
    wave = i == 0
    new = struct.pack('>10I', 0x10, 70 + i, 0, (6 if wave else 127) << 8, (0 if wave else 64) << 8, 0,
                      0xffffffff, 0xffffffff, struct.unpack('>I', old[0x20:0x24])[0], 0)
    sites.append({'addr': '0x%08x' % a, 'stock': old.hex(), 'op': 'bytes', 'new': new.hex(),
                  'note': 'row 0x%x: group 0x10, k %d, range, no CC/NRPN' % (r, 70 + i)})
    for off, sym in ((LONG, 'fx2_l%d' % i), (GROUP, 'fx2_g'), (SHORT, 'fx2_s%d' % i)):
        p = a + off
        sites.append({'addr': '0x%08x' % p, 'stock': rd(p, 4).hex(), 'op': 'ptr', 'target': sym,
                      'note': 'row 0x%x +0x%x' % (r, off)})
    p = a + 0x34
    sites.append({'addr': '0x%08x' % p, 'stock': rd(p, 4).hex(), 'op': 'bytes',
                  'new': '%08x' % (FMT_WAVE if wave else FMT_NUM), 'note': 'row 0x%x formatter' % r})


def code(a, stock_hex, op, **kw):
    if rd(a, len(stock_hex) // 2).hex() != stock_hex:
        raise SystemExit('0x%08x: stock bytes %s, expected %s' % (a, rd(a, len(stock_hex) // 2).hex(), stock_hex))
    s = {'addr': '0x%08x' % a, 'stock': stock_hex, 'op': op}
    s.update(kw)
    sites.append(s)


# the SEND FX view's page-id list: count 3 -> 4 and the array {17, 18, 19} -> fx2_pages
code(0x40061616, '7203', 'bytes', new='7204', kind='code', note='moveq #3,%d1 -> #4')
code(0x40061624, '203c401e0448', 'keep2', target='fx2_pages', note='movel #0x401e0448,%d0')
# the page-record accessor: jmp to fx2_pagerec (id 37 -> fx2_rec)
code(0x400c25d8, '7224202f0004', 'jmp', target='fx2_pagerec', note='moveq #36,%d1; movel 4(sp),%d0')
# FxPageView's page-id tests (filter graphic, reverb graphic, box label): id 37 reads as 0x13
for a in (0x4006f710, 0x4006f8e0, 0x4006f4d0):
    code(a, rd(a, 6).hex(), 'jsr', target='fx2_baseid', note='movea.l (0x88,A0),A0; jsr (A0)')
    if rd(a, 6).hex() not in ('206800884e90',):
        raise SystemExit('0x%08x: not movea.l (0x88,A0),A0; jsr (A0)' % a)

mod2 = {
    'id': 'fx-page2',
    'version': '0.1',
    'title': 'Second chorus page (UI test)',
    'category': 'Examples',
    'license': 'GPL-2.0-or-later',
    'author': 'naturarum',
    'description': 'Adds a fourth page to the SEND FX view (FUNC+FX, then DOWN past the chorus) '
                   'with 8 new knobs whose values go to unused FX-setup words. UI only: the DSP never '
                   'sees them, and the 8 knobs borrow descriptor rows that carry MIDI CCs in stock. '
                   'A test of the multi-page mechanism; emulator-tested only.',
    'device': 'digitone-mk2',
    'os': '1.12',
    'sources': ['fxpage2.c', 'pagerec.s'],
    'subscribe': [{'event': 'ev_tick', 'fn': 'fx2_tick', 'order': 55}],
    'sites': [{k: v for k, v in s.items() if k != 'note'} for s in sites],
    'requires': ['core'],
}
with open(OUT2, 'w') as f:
    json.dump(mod2, f, indent=1)
    f.write('\n')
for s in sites:
    print('%s %-5s %-12s %s' % (s['addr'], s['op'], s.get('target', ''), s.get('note', '')))
print('%d sites -> %s' % (len(sites), OUT2))
