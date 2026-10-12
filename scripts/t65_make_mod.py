#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""T6.5: write vendor/elekloader/examples/param-grow-dn2/mod.json, every site found in the stock
image and checked against it (scripts/t65_sites.py), and check the stock addresses the row mods
(param-rows-dn2, param-test-dn2) name.

    venv/bin/python scripts/t65_make_mod.py [--rows 0x180] [--check-only]

param-grow's sites:
  - 0x40000568 jsr -> pg_boot: the OS entry's call into the C start; the table is built there
  - 0x401ae0cc jsr -> pg_sinit: the init_array function's call of the display table's static
    initializer; new rows get their display entries and page places there
  - 0x400c257c jmp -> pg_disp_get: the display table's only reader (10 bytes: its first two
    instructions, the second of which is one of the 58 row-count bounds)
  - 56 keep2 -> pg_desc (+ the field offset): every operand that holds the table's address
  - 57 bytes: the other row-count bounds, 0x141 -> rows and 0x140 -> rows - 1
  - 33 bytes: the ParameterUpdateCache's constants, recomputed for rows
"""
import argparse
import json
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import t65_sites  # noqa: E402

EL = os.path.join(ROOT, 'vendor', 'elekloader', 'examples')
GROW = os.path.join(EL, 'param-grow-dn2', 'mod.json')
BOOT_CALL = (0x40000568, '4eb940186fa2')      # jsr 0x40186fa2: the OS's C start

DESCRIPTION = (
    "EXPERIMENT: emulator-tested only, never run on a unit. Gives the parameter table room for 63 "
    "more rows (0x141 -> 0x180) and adds none: the table is copied to RAM at boot, before any of "
    "the OS's own code, and every instruction that holds its address or its row count is pointed "
    "at the copy; the MIDI CC/NRPN input cache and the display table follow the new count. The "
    "rows after the stock ones are copies of row 0, which is what the stock OS reads for any row "
    "id it clamps, so nothing on the screen, in MIDI or in a saved project changes. Other mods "
    "add rows through the table param_rows (grow.s says how). For dn2-cfw's GT00 build.")


def u32(img, a):
    return struct.unpack('>I', img[a - t65_sites.LOAD:a - t65_sites.LOAD + 4])[0]


def cstr(img, a):
    o = a - t65_sites.LOAD
    return img[o:o + 64].split(b'\0')[0].decode('latin-1')


def grow_mod(rows, img):
    p = t65_sites.plan(rows, img)
    sites = []
    a, stock = BOOT_CALL
    if img[a - t65_sites.LOAD:a - t65_sites.LOAD + 6].hex() != stock:
        raise SystemExit('0x%08x is not jsr 0x40186fa2' % a)
    sites.append({'addr': '0x%08x' % a, 'stock': stock, 'op': 'jsr', 'target': 'pg_boot',
                  'note': 'the OS entry\'s call into the C start: the table is built here'})
    sites.append({'addr': '0x%08x' % p['sinit']['addr'], 'stock': p['sinit']['stock'], 'op': 'jsr',
                  'target': 'pg_sinit', 'note': 'FUN_401ae0c8 (init_array) calls the static initializer '
                  '0x400c2c4e that builds the display table and the page records'})
    sites.append({'addr': '0x%08x' % p['accessor']['addr'], 'stock': p['accessor']['stock'], 'op': 'jmp',
                  'target': 'pg_disp_get', 'note': 'the display table\'s accessor: move.l 4(sp),d0; '
                  'cmpi.l #0x141,d0 (one of the row-count bounds)'})
    for d in p['desc']:
        s = {'addr': '0x%08x' % d['addr'], 'stock': d['stock'], 'op': 'keep2', 'target': 'pg_desc',
             'note': '%s: %s' % (d['func'], d['text'])}
        if d['addend']:
            s['addend'] = d['addend']
        sites.append(s)
    for b in p['bounds']:
        sites.append({'addr': '0x%08x' % b['addr'], 'stock': b['stock'], 'op': 'bytes', 'new': b['new'],
                      'kind': 'code', 'note': '%s: %s -> #0x%x' % (b['func'], b['text'],
                                                                 b['imm'] - 0x141 + rows)})
    for c in p['cache']:
        sites.append({'addr': '0x%08x' % c['addr'], 'stock': c['stock'], 'op': 'bytes', 'new': c['new'],
                      'kind': 'code', 'note': 'ParameterUpdateCache %s: %s' % (c['const'], c['why'])})
    sites.sort(key=lambda s: int(s['addr'], 16))
    mod = {
        'id': 'param-grow', 'version': '0.1',
        'title': 'Room for more parameter rows (GT00)',
        'category': 'Examples', 'license': 'GPL-2.0-or-later', 'author': 'naturarum',
        'description': DESCRIPTION,
        'device': 'digitone-mk2', 'os': '1.12',
        'sources': ['grow.s'],
        'collections': {'param_rows': 4},
        'exports': ['pg_desc', 'pg_xdisp', 'pg_xdisp_on'],
        'resources': {'names': ['param-table'],
                      'regions': [{'name': 'param-table', 'lo': '0x47D00000', 'hi': '0x47D10000'}]},
        'requires': ['core'],
        'sites': sites,
    }
    return mod, p


# Stock addresses the row mods name in their C sources: each must point at what the comment says.
STOCK_REFS = {
    0x40218a2a: ('str', ''),                       # the empty suffix every row points at
    0x400e3030: ('fmt', None),                     # plain number formatter
}


def check_row_refs(img):
    """the stock pointers param-rows-dn2 and param-test-dn2 use, read from the rows they copy"""
    D, R = t65_sites.DESC, t65_sites.ROW
    out = {}
    for row, what in ((0x96, 'Compressor'), (0x69, 'Chorus')):
        g = u32(img, D + row * R + 0x2c)
        out['group name of row 0x%x' % row] = ('0x%08x' % g, cstr(img, g))
    out['suffix of row 0x96'] = ('0x%08x' % u32(img, D + 0x96 * R + 0x38), '')
    out['formatter of row 0x96'] = ('0x%08x' % u32(img, D + 0x96 * R + 0x34), '')
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--rows', default='0x180')
    ap.add_argument('--check-only', action='store_true')
    a = ap.parse_args()
    rows = int(a.rows, 0)
    img = t65_sites.load_image()
    mod, p = grow_mod(rows, img)
    kinds = {}
    for s in mod['sites']:
        kinds[s['op']] = kinds.get(s['op'], 0) + 1
    print('param-grow: %d sites (%s); rows 0x%x' % (len(mod['sites']), ', '.join(
        '%s %d' % kv for kv in sorted(kinds.items())), rows))
    for k, v in check_row_refs(img).items():
        print('  %s: %s %r' % (k, v[0], v[1]))
    if a.check_only:
        return
    os.makedirs(os.path.dirname(GROW), exist_ok=True)
    with open(GROW, 'w') as f:
        json.dump(mod, f, indent=1)
        f.write('\n')
    print('wrote %s' % os.path.relpath(GROW, ROOT))


if __name__ == '__main__':
    main()
