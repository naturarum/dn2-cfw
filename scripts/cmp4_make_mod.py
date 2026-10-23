#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""CMP4: write vendor/elekloader/examples/flanger-names-dn2/mod.json (sites checked against the stock image).

    venv/bin/python scripts/cmp4_make_mod.py [--list] [--out PATH]

FL01 moved flanger-names-dn2 to 0.2 in the fork's working tree (scripts/fl01_make_mod.py), so scripts/cmp4_build.py
writes this 0.1 mod.json into the pinned fork tree (elk_pin.REV_CMP4) with --out, and checks it is the committed file.
Without --out it writes the working tree's mod.json only while that is still 0.1.

flanger-names-dn2 0.1 = fx-rename-dn2 0.1's 13 flanger sites (scripts/t50b_make_mod.py: the chorus page's title and
short name, the SEND FX page box, five descriptor rows' long names and short labels) with the same strings, and none of
its 16 tape sites: CMP4 gives the delay slot back to the stock delay, so every delay name stays stock. The sites are
written here as t50b_make_mod.py writes them (the same checks of the stock pointer or instruction) and must equal
fx-rename 0.1's flanger sites but for the target symbols' prefix (fxr_ -> fln_).
"""
import json
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMAGE = os.path.join(ROOT, 'ele3_out', '02_type3.unpacked.bin')
OUT = os.path.join(ROOT, 'vendor', 'elekloader', 'examples', 'flanger-names-dn2', 'mod.json')
FXR = os.path.join(ROOT, 'vendor', 'elekloader', 'examples', 'fx-rename-dn2', 'mod.json')
LOAD = 0x40000400
DESC, ROW = 0x401f83a4, 0x3c
LONG, SHORT = 0x28, 0x30

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


# ---- the chorus slot: the flanger (as t50b_make_mod.py)
pea(0x400ca018, 'fln_flanger', 'Chorus', 'page 19 title, built at boot')
pea(0x400c9fba, 'fln_flg', 'CHO', 'page 19 short name, built at boot')
pea(0x4006f4f8, 'fln_flg', 'CHO', 'SEND FX page box, page 0x13')
row(0x43, 'fln_flg_send', 'Chorus Send', 'fln_flg', 'CHR', 'track FX page G')
row(0x6f, 'fln_flg_mix', 'Chorus Mix Vol.', 'fln_flg', 'CHR', 'MIXER FX MIX page G')
row(0x8b, 'fln_flg_in', 'In Chorus Send', 'fln_flg', 'CHR', 'external input send')
row(0x8c, 'fln_flg_in_l', 'In Chorus Send L', 'fln_flg_l', 'LCHR', 'external input send L, MIXER EXT MIX G')
row(0x8d, 'fln_flg_in_r', 'In Chorus Send R', 'fln_flg_r', 'RCHR', 'external input send R')

# the same sites as fx-rename 0.1's flanger sites (its first 13), but the prefix of the strings' symbols
fxr = json.load(open(FXR))
if fxr['version'] != '0.1':
    raise SystemExit('fx-rename is not 0.1')
want = [dict(s, target=s['target'].replace('fxr_', 'fln_')) for s in fxr['sites'][:13]]
if [{k: v for k, v in s.items() if k != 'note'} for s in sites] != want:
    raise SystemExit('the sites differ from fx-rename 0.1\'s 13 flanger sites')

mod = {
    'id': 'flanger-names',
    'version': '0.1',
    'title': 'Flanger names (CMP4)',
    'category': 'Examples',
    'license': 'GPL-2.0-or-later',
    'author': 'naturarum',
    'description': 'For dn2-cfw\'s CMP4 build, whose DSP runs a flanger in the chorus slot and the stock delay in the '
                   'delay slot: renames the chorus page and every chorus send to Flanger/FLG; every delay name stays '
                   'stock. fx-rename-dn2\'s 13 flanger sites with the same strings (they ran on a unit in FTN1 to CMP3), '
                   'without its tape sites. Labels only: no new rows, knobs or glyphs, and every knob keeps its MIDI CC, '
                   'storage and DSP frame word. On a stock DSP the chorus would be named Flanger.',
    'device': 'digitone-mk2',
    'os': '1.12',
    'sources': ['flanger_names.c'],
    'sites': [{k: v for k, v in s.items() if k != 'note'} for s in sites],
    'requires': ['core'],
}

if __name__ == '__main__':
    if '--out' in sys.argv:
        OUT = os.path.abspath(sys.argv[sys.argv.index('--out') + 1])
    elif os.path.exists(OUT) and json.load(open(OUT)).get('version') != mod['version']:
        raise SystemExit('%s is flanger-names %s now (FL01, scripts/fl01_make_mod.py); CMP4 builds 0.1 from the pinned '
                         'fork tree (scripts/elk_pin.py REV_CMP4): give --out' % (os.path.relpath(OUT, ROOT),
                                                                                   json.load(open(OUT)).get('version')))
    with open(OUT, 'w') as f:
        json.dump(mod, f, indent=1)
        f.write('\n')
    if '--list' in sys.argv:
        for s in sites:
            print('%s %-5s %-14s %s' % (s['addr'], s['op'], s['target'], s['note']))
    print('%d sites -> %s' % (len(sites), os.path.relpath(OUT, ROOT)))
