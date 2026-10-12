#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""T5.0b: write vendor/elekloader/examples/fx-rename-dn2/mod.json (sites checked against the stock image).

    venv/bin/python scripts/t50b_make_mod.py [--list]

Every site only repoints a name: a descriptor row's long name (+0x28) or short label (+0x30), the
operand of a `pea` that pushes a page name, or one entry of the modulation destination lists'
group-name table. Before a site is written, the stock pointer or instruction is read from the image
and must point at the expected string. The stock strings themselves are never edited (several are
shared with rows this mod leaves alone). notes/T5.0b-rename.md has the reasons for each site.
"""
import json
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMAGE = os.path.join(ROOT, 'ele3_out', '02_type3.unpacked.bin')
OUT = os.path.join(ROOT, 'vendor', 'elekloader', 'examples', 'fx-rename-dn2', 'mod.json')
LOAD = 0x40000400
DESC, ROW, ROWS = 0x401f83a4, 0x3c, 0x141
LONG, SHORT = 0x28, 0x30
DEST_GROUPS = 0x401f7b04          # group-name table of the modulation destination lists, by group - 5

img = open(IMAGE, 'rb').read()


def rd(a, n):
    return img[a - LOAD:a - LOAD + n]


def u32(a):
    return struct.unpack('>I', rd(a, 4))[0]


def cstr(a):
    return rd(a, 40).split(b'\0')[0].decode()


sites = []


def site(addr, op, sym, note, expect, size):
    sites.append({'addr': '0x%08x' % addr, 'stock': rd(addr, size).hex(), 'op': op, 'target': sym,
                  'note': '%s: "%s"' % (note, expect)})


def row(r, long_sym, long_old, short_sym, short_old, what):
    """repoint row r's long name and short label; the CC, range, storage and formatter stay"""
    a = DESC + r * ROW
    for off, sym, old in ((LONG, long_sym, long_old), (SHORT, short_sym, short_old)):
        got = cstr(u32(a + off))
        if got != old:
            raise SystemExit('row %#x +%#x: "%s", expected "%s"' % (r, off, got, old))
        site(a + off, 'ptr', sym, 'row 0x%x +0x%x (%s)' % (r, off, what), old, 4)


def pea(a, sym, old, what):
    """keep the `pea (abs).l` opcode, change its operand"""
    b = rd(a, 6)
    if b[:2] != b'\x48\x79' or cstr(struct.unpack('>I', b[2:])[0]) != old:
        raise SystemExit('0x%08x: not pea "%s"' % (a, old))
    site(a, 'keep2', sym, 'pea at 0x%08x (%s)' % (a, what), old, 6)


# ---- the chorus slot: the flanger
pea(0x400ca018, 'fxr_flanger', 'Chorus', 'page 19 title, built at boot')
pea(0x400c9fba, 'fxr_flg', 'CHO', 'page 19 short name, built at boot')
pea(0x4006f4f8, 'fxr_flg', 'CHO', 'SEND FX page box, page 0x13')
row(0x43, 'fxr_flg_send', 'Chorus Send', 'fxr_flg', 'CHR', 'track FX page G')
row(0x6f, 'fxr_flg_mix', 'Chorus Mix Vol.', 'fxr_flg', 'CHR', 'MIXER FX MIX page G')
row(0x8b, 'fxr_flg_in', 'In Chorus Send', 'fxr_flg', 'CHR', 'external input send')
row(0x8c, 'fxr_flg_in_l', 'In Chorus Send L', 'fxr_flg_l', 'LCHR', 'external input send L, MIXER EXT MIX G')
row(0x8d, 'fxr_flg_in_r', 'In Chorus Send R', 'fxr_flg_r', 'RCHR', 'external input send R')

# ---- the delay slot: the tape delay
pea(0x400c9f34, 'fxr_tape', 'Delay', 'page 17 title, built at boot')
pea(0x400c9ee6, 'fxr_tap', 'DEL', 'page 17 short name, built at boot')
pea(0x4006f4f0, 'fxr_tap', 'DEL', 'SEND FX page box, page 0x11')
row(0x44, 'fxr_tape_send', 'Delay Send', 'fxr_tape_s', 'DEL', 'track FX page E')
row(0x6d, 'fxr_tape_send', 'Delay Send', 'fxr_tape_s', 'DEL', 'chorus/flanger page G')
row(0x78, 'fxr_tape_mix', 'Delay Mix Vol.', 'fxr_tape_s', 'DEL', 'MIXER FX MIX page E')
row(0x8e, 'fxr_tape_in', 'In Delay Send', 'fxr_tape_s', 'DEL', 'external input send')
row(0x8f, 'fxr_tape_in_l', 'In Delay Send L', 'fxr_tape_l', 'LDEL', 'external input send L, MIXER EXT MIX E')
row(0x90, 'fxr_tape_in_r', 'In Delay Send R', 'fxr_tape_r', 'RDEL', 'external input send R')
a = DEST_GROUPS + 4 * (0x12 - 5)
if cstr(u32(a)) != 'DEL':
    raise SystemExit('0x%08x: not the delay group\'s "DEL"' % a)
site(a, 'ptr', 'fxr_tape_s', 'destination lists, group 0x12 (delay) name', 'DEL', 4)

# Left stock on purpose (notes/T5.0b-rename.md): the group pointer +0x2c of every row (no code reads
# it), row 0x7a "Delay FX Routing" (no page shows it), rows 0x3d and 0x2c (the AMP and filter
# envelopes' "Delay Time"/"Env. Delay", DEL), the knob rows of both pages, and the second, unreferenced
# name table at 0x401d0f04.

addrs = [int(s['addr'], 16) for s in sites]
if len(set(addrs)) != len(addrs):
    raise SystemExit('a site is listed twice')

mod = {
    'id': 'fx-rename',
    'version': '0.1',
    'title': 'Flanger and tape names (FTN1)',
    'category': 'Examples',
    'license': 'GPL-2.0-or-later',
    'author': 'naturarum',
    'description': 'EXPERIMENT: emulator-tested only, never run on a unit. For dn2-cfw\'s FTN1 build, '
                   'whose DSP runs a flanger in the chorus slot and a tape delay in the delay slot: '
                   'renames the chorus page and every chorus send to Flanger/FLG, and the delay page and '
                   'every delay send to Tape/TAPE (the page box says TAP). Labels only: no new rows, '
                   'knobs or glyphs, and every knob keeps its MIDI CC, storage and DSP frame word. '
                   'On a stock DSP the names would be wrong.',
    'device': 'digitone-mk2',
    'os': '1.12',
    'sources': ['fx_rename.c'],
    'sites': [{k: v for k, v in s.items() if k != 'note'} for s in sites],
    'requires': ['core'],
}

if __name__ == '__main__':
    with open(OUT, 'w') as f:
        json.dump(mod, f, indent=1)
        f.write('\n')
    if '--list' in sys.argv:
        for s in sites:
            print('%s %-5s %-14s %s' % (s['addr'], s['op'], s['target'], s['note']))
    print('%d sites -> %s' % (len(sites), os.path.relpath(OUT, ROOT)))
