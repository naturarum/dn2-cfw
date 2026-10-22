#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""T5.3: write vendor/elekloader/examples/fx-echo-dn2/mod.json (sites checked against the stock image).

    venv/bin/python scripts/t53_make_mod.py [--list]

fx-echo = fx-rename's flanger names (the same 13 sites and strings) + the delay slot named Echo
everywhere fx-rename said Tape + the delay page laid out for the Space Echo on one page (the
owner's layout of 2026-10-13: TIME MODE WID FDBK CNTR BW AGE REV, LEVEL VOL). Before a site is
written, the stock bytes are read from the image and checked: each pointer must name the expected
string (or formatter), each value must be the expected one, each instruction the expected opcode.
The stock strings themselves are never edited. vendor/elekloader/examples/fx-echo-dn2/fx_echo.c
has the reasons for each change.
"""
import json
import os
import re
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMAGE = os.path.join(ROOT, 'ele3_out', '02_type3.unpacked.bin')
OUT = os.path.join(ROOT, 'vendor', 'elekloader', 'examples', 'fx-echo-dn2', 'mod.json')
LOAD = 0x40000400
DESC, ROW, ROWS = 0x401f83a4, 0x3c, 0x141
MAX, LONG, SHORT, FMT = 0x0c, 0x28, 0x30, 0x34
DEST_GROUPS = 0x401f7b04          # group-name table of the modulation destination lists, by group - 5
FMT_ONOFF, FMT_PREPOST, FMT_NUMBER = 0x400e3568, 0x400e3592, 0x400e3030

BAND_H = os.path.join(ROOT, 'vendor', 'elekloader', 'examples', 'fx-echo-dn2', 'band_table.h')
SPACEECHO = os.path.join(ROOT, 'fx', 'spaceecho.c')

img = open(IMAGE, 'rb').read()


def band_table():
    """the band's half width per BW step, in 1/256 CENTER steps, from the DSP's own constants"""
    src = open(SPACEECHO).read()
    k = {}
    for nm in ('SE_LB0', 'SE_LB_RANGE', 'SE_CTR_OCT'):
        m = re.search(r'#define\s+%s\s+\(?\s*(-?[0-9.]+)f' % nm, src)
        if not m:
            raise SystemExit('%s: no %s' % (SPACEECHO, nm))
        k[nm] = float(m.group(1))
    vals = [min(65535, int(round(2 ** (k['SE_LB0'] + k['SE_LB_RANGE'] * bw / 127.0) / 2 * 127.0 / k['SE_CTR_OCT']
                                 * 256))) for bw in range(128)]
    return k, vals


def write_band_table():
    k, vals = band_table()
    rows = ['    ' + ', '.join('%5d' % v for v in vals[i:i + 10]) + ',' for i in range(0, 128, 10)]
    text = ('/* SPDX-License-Identifier: GPL-2.0-or-later */\n'
            '/* band_table.h: written by dn2-cfw\'s scripts/t53_make_mod.py from fx/spaceecho.c (do not edit).\n'
            ' * Half the Space Echo band\'s width for each BW step, in 1/256 CENTER steps: width = 2^(SE_LB0 +\n'
            ' * SE_LB_RANGE x BW / 127) octaves (SE_LB0 %g, SE_LB_RANGE %g), CENTER = SE_CTR_OCT (%g) octaves\n'
            ' * over 0..127, so half = width / 2 x 127 / SE_CTR_OCT x 256. */\n'
            'static const unsigned short band_half[128] = {\n%s\n};\n'
            % (k['SE_LB0'], k['SE_LB_RANGE'], k['SE_CTR_OCT'], '\n'.join(rows)))
    with open(BAND_H, 'w') as f:
        f.write(text)
    return vals


def rd(a, n):
    return img[a - LOAD:a - LOAD + n]


def u32(a):
    return struct.unpack('>I', rd(a, 4))[0]


def s32(a):
    return struct.unpack('>i', rd(a, 4))[0]


def cstr(a):
    return rd(a, 40).split(b'\0')[0].decode()


sites = []


def site(addr, op, note, size, sym=None, new=None, kind=None):
    s = {'addr': '0x%08x' % addr, 'stock': rd(addr, size).hex(), 'op': op}
    if sym:
        s['target'] = sym
    if new is not None:
        s['new'] = new
    if kind:
        s['kind'] = kind
    s['note'] = note
    sites.append(s)


def name(r, off, sym, old, what):
    a = DESC + r * ROW + off
    got = cstr(u32(a))
    if got != old:
        raise SystemExit('row %#x +%#x: "%s", expected "%s"' % (r, off, got, old))
    site(a, 'ptr', 'row 0x%x +0x%x (%s): "%s"' % (r, off, what, old), 4, sym=sym)


def row(r, long_sym, long_old, short_sym, short_old, what):
    """repoint row r's long name and short label; the CC, range, storage and formatter stay"""
    name(r, LONG, long_sym, long_old, what)
    if short_sym:
        name(r, SHORT, short_sym, short_old, what)


def pea(a, sym, old, what):
    """keep the `pea (abs).l` opcode, change its operand"""
    b = rd(a, 6)
    if b[:2] != b'\x48\x79' or cstr(struct.unpack('>I', b[2:])[0]) != old:
        raise SystemExit('0x%08x: not pea "%s"' % (a, old))
    site(a, 'keep2', 'pea at 0x%08x (%s): "%s"' % (a, what, old), 6, sym=sym)


def value(r, off, old, new, what):
    """a 32-bit field of row r, as data bytes"""
    a = DESC + r * ROW + off
    if u32(a) != old:
        raise SystemExit('row %#x +%#x: 0x%x, expected 0x%x' % (r, off, u32(a), old))
    site(a, 'bytes', 'row 0x%x +0x%x (%s): 0x%x -> 0x%x' % (r, off, what, old, new), 4,
         new='%08x' % new, kind='data')


def formatter(r, old, sym, what):
    a = DESC + r * ROW + FMT
    if u32(a) != old:
        raise SystemExit('row %#x +0x34: formatter 0x%x, expected 0x%x' % (r, u32(a), old))
    site(a, 'ptr', 'row 0x%x +0x34 (%s): formatter 0x%x -> %s' % (r, what, old, sym), 4, sym=sym)


def expect_row(r, group, k, mn, mx, df, cc, nrpn):
    """the row is the one the layout means: group, k, range, default, CC and NRPN as stock 1.12"""
    a = DESC + r * ROW
    got = (s32(a), s32(a + 4), s32(a + 8) >> 8, s32(a + 12) >> 8, s32(a + 16) >> 8,
           struct.unpack('>h', rd(a + 0x18, 2))[0], s32(a + 0x1c))
    want = (group, k, mn, mx, df, cc, nrpn)
    if got != want:
        raise SystemExit('row %#x: (group, k, min, max, default, cc, nrpn) = %r, expected %r' % (r, got, want))


# ---- the chorus slot: the flanger (fx-rename's 13 sites, the same strings)
pea(0x400ca018, 'fxe_flanger', 'Chorus', 'page 19 title, built at boot')
pea(0x400c9fba, 'fxe_flg', 'CHO', 'page 19 short name, built at boot')
pea(0x4006f4f8, 'fxe_flg', 'CHO', 'SEND FX page box, page 0x13')
row(0x43, 'fxe_flg_send', 'Chorus Send', 'fxe_flg', 'CHR', 'track FX page G')
row(0x6f, 'fxe_flg_mix', 'Chorus Mix Vol.', 'fxe_flg', 'CHR', 'MIXER FX MIX page G')
row(0x8b, 'fxe_flg_in', 'In Chorus Send', 'fxe_flg', 'CHR', 'external input send')
row(0x8c, 'fxe_flg_in_l', 'In Chorus Send L', 'fxe_flg_l', 'LCHR', 'external input send L, MIXER EXT MIX G')
row(0x8d, 'fxe_flg_in_r', 'In Chorus Send R', 'fxe_flg_r', 'RCHR', 'external input send R')

# ---- the delay slot: Echo wherever fx-rename said Tape
pea(0x400c9f34, 'fxe_echo', 'Delay', 'page 17 title, built at boot')
pea(0x400c9ee6, 'fxe_ech', 'DEL', 'page 17 short name, built at boot')
pea(0x4006f4f0, 'fxe_ech', 'DEL', 'SEND FX page box, page 0x11')
row(0x44, 'fxe_echo_send', 'Delay Send', 'fxe_echo_s', 'DEL', 'track FX page E')
row(0x6d, 'fxe_echo_send', 'Delay Send', 'fxe_echo_s', 'DEL', 'chorus/flanger page G')
row(0x78, 'fxe_echo_mix', 'Delay Mix Vol.', 'fxe_echo_s', 'DEL', 'MIXER FX MIX page E')
row(0x8e, 'fxe_echo_in', 'In Delay Send', 'fxe_echo_s', 'DEL', 'external input send')
row(0x8f, 'fxe_echo_in_l', 'In Delay Send L', 'fxe_echo_l', 'LDEL', 'external input send L, MIXER EXT MIX E')
row(0x90, 'fxe_echo_in_r', 'In Delay Send R', 'fxe_echo_r', 'RDEL', 'external input send R')
a = DEST_GROUPS + 4 * (0x12 - 5)
if cstr(u32(a)) != 'DEL':
    raise SystemExit('0x%08x: not the delay group\'s "DEL"' % a)
site(a, 'ptr', 'destination lists, group 0x12 (delay) name: "DEL"', 4, sym='fxe_echo_s')

# ---- the delay page (page record 17: A..F = rows 0x71..0x76, G empty, H 0x77, LEVEL 0x79)
expect_row(0x71, 0x12, 32, 0, 127, 23, 21, 256)
expect_row(0x72, 0x12, 33, 0, 1, 0, 22, 257)
expect_row(0x73, 0x12, 34, 0, 127, 64, 23, 258)
expect_row(0x74, 0x12, 35, 0, 127, 32, 24, 259)
expect_row(0x75, 0x12, 36, 0, 127, 32, 25, 260)
expect_row(0x76, 0x12, 37, 0, 127, 96, 26, 261)
expect_row(0x7a, 0x12, 40, 0, 1, 1, -1, -1)
row(0x71, 'fxe_time', 'Delay Time', None, None, 'delay page A: TIME')
row(0x72, 'fxe_mode', 'Pingpong', 'fxe_mode_s', 'X', 'delay page B: MODE')
value(0x72, MAX, 1 << 8, 6 << 8, 'delay page B: MODE max 1 -> 6')
formatter(0x72, FMT_ONOFF, 'fxe_fmt_mode', 'delay page B: MODE value text')
row(0x73, 'fxe_wid', 'Stereo Width', None, None, 'delay page C: WID')
row(0x74, 'fxe_int', 'Feedback Gain', None, None, 'delay page D: FDBK')
row(0x75, 'fxe_cntr', 'Feedback HPF', 'fxe_cntr_s', 'HPF', 'delay page E: CNTR')
row(0x76, 'fxe_bw', 'Feedback LPF', 'fxe_bw_s', 'LPF', 'delay page F: BW')
row(0x7a, 'fxe_age', 'Delay FX Routing', 'fxe_age_s', 'DEL', 'delay page G: AGE')
value(0x7a, MAX, 1 << 8, 127 << 8, 'delay page G: AGE max 1 -> 127')
value(0x7a, FMT, FMT_PREPOST, FMT_NUMBER, 'delay page G: AGE value text Pre/Post -> a plain number')

# ---- the filter curve over E and F: FxPageView::vslot04 asks the page id at 0x4006f710
GRAPH = 0x4006f710
if rd(GRAPH - 6, 12).hex() != '20527a132f0a206800884e90' or rd(GRAPH + 6, 6).hex() != '588fba806700':
    raise SystemExit('0x%08x: not FxPageView\'s page-id call before the filter curve' % GRAPH)
site(GRAPH, 'jsr', 'FxPageView::vslot04: movea.l (0x88,a0),a0; jsr (a0) -> jsr fxe_graphid (for the delay '
     'page, the filter picture over E and F is given the band\'s lower edge and width)', 6, sym='fxe_graphid')

# Left stock on purpose: knob labels TIME WID FDBK REV VOL (the layout keeps them); every row's group
# pointer +0x2c (no code reads it, notes/T5.0b-rename.md); rows 0x3d and 0x2c (the AMP and filter
# envelopes' DEL); every CC, NRPN, k, default and display flag; the second, unreferenced name table at
# 0x401d0f04; the reverb page and its routing row 0x83.

addrs = [int(s['addr'], 16) for s in sites]
if len(set(addrs)) != len(addrs):
    raise SystemExit('a site is listed twice')

mod = {
    'id': 'fx-echo',
    'version': '0.2',
    'title': 'Echo page: the Space Echo on one page (SE01, SC01)',
    'category': 'Examples',
    'license': 'GPL-2.0-or-later',
    'author': 'naturarum',
    'description': 'EXPERIMENT: emulator-tested only, never run on a unit (0.1 in SE01, 0.2 in SC01). For dn2-cfw\'s '
                   'SE01 and SC01 builds, whose '
                   'DSP runs a Space Echo in the delay slot and a flanger in the chorus slot. Supersedes '
                   'fx-rename: the chorus page and its sends are named Flanger/FLG as there, the delay page '
                   'and its sends Echo/ECHO (the page box says ECH). The delay page becomes TIME MODE WID '
                   'FDBK CNTR BW AGE REV: X (Pingpong) widened to MODE 0..6 (heads 1, 2, 3, 12, 23, 13, '
                   '123), HPF/LPF named CNTR/BW with the filter picture drawing the echo\'s band, and the hidden Delay FX Routing '
                   'row made AGE (0..127) and placed on the empty knob G. Every knob keeps its MIDI CC, '
                   'storage and DSP frame word; MODE and AGE are never p-lockable; AGE has no CC. On a '
                   'stock DSP the names would be wrong. 0.2: AGE shows its value while it is turned (bit 2 of '
                   'its display flags, the stock routing row\'s "no value text", cleared; bit 3, never '
                   'p-lockable, kept).',
    'device': 'digitone-mk2',
    'os': '1.12',
    'sources': ['fx_echo.c', 'graph.s'],
    'subscribe': [{'event': 'ev_tick', 'fn': 'fxe_tick', 'order': 50}],
    'exports': ['fxe_state', 'fxe_ticks', 'fxe_band_seen'],
    'sites': [{k: v for k, v in s.items() if k != 'note'} for s in sites],
    'requires': ['core'],
    'conflicts': ['fx-rename'],
}

if __name__ == '__main__':
    write_band_table()
    with open(OUT, 'w') as f:
        json.dump(mod, f, indent=1)
        f.write('\n')
    if '--list' in sys.argv:
        for s in sites:
            print('%s %-5s %-14s %s' % (s['addr'], s['op'], s.get('target', s.get('new', '')), s['note']))
    print('%d sites -> %s; %s' % (len(sites), os.path.relpath(OUT, ROOT), os.path.relpath(BAND_H, ROOT)))
