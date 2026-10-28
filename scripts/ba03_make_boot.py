#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""BA03: write vendor/elekloader/examples/boot-mark-dn2/bm_mark.h and mod.json, boot-mark-dn2 0.2 (the site
checked against the stock image): the DIGIWERK mark in the start-up animation, the right way up (bm.c draws row j of
the picture at y = 63 - j: the panel's rows run up; 0.1, BA02, drew it upside down; notes/BA03-turing-arp.md).

    venv/bin/python scripts/ba03_make_boot.py [--mark B-two-lines (the default, the owner's pick)|A-one-line|C-framed] [--list]

The marks are scripts/ba02_bootmark.py's (our own pixel letters). Site: the intro's logo blit at its first frame,
0x400d3ac4 (jsr 0x40115adc, after the five argument pushes, the stack cleared by the lea after it) -> jsr bm_draw.
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scripts'))
sys.dont_write_bytecode = True
import ba02_bootmark as BM  # noqa: E402

IMAGE = os.path.join(ROOT, 'ele3_out', '02_type3.unpacked.bin')
MODDIR = os.path.join(ROOT, 'vendor', 'elekloader', 'examples', 'boot-mark-dn2')
LOAD = 0x40000400
img = open(IMAGE, 'rb').read()


def need(addr, hexbytes, what):
    got = img[addr - LOAD:addr - LOAD + len(hexbytes) // 2].hex()
    if got != hexbytes:
        raise SystemExit('0x%08x: %s, expected %s (%s)' % (addr, got, hexbytes, what))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--mark', default='B-two-lines')          # the owner's pick, 2026-10-27
    ap.add_argument('--list', action='store_true')
    a = ap.parse_args()
    marks = BM.marks()
    if a.mark not in marks:
        raise SystemExit('--mark: one of %s' % ', '.join(marks))
    bits = marks[a.mark]
    need(0x400d3aa0, '4878000142a7487942c4667c4e93', 'the intro, frame 0: pea 1; clr.l -(sp); pea 0x42c4667c; '
         'jsr (a3) (the source cleared)')
    need(0x400d3aae, '42a748780014487800304879446470084879' + '42c4667c', 'the logo blit\'s arguments: 0 (the mode), '
         '20 (y), 48 (x), 0x44647008 (the logo), 0x42c4667c (the source)')
    need(0x400d3aca, '4fef0020', 'lea 0x20(sp),sp after the blit (its five arguments and the clear\'s three)')
    site = {'addr': '0x400d3ac4', 'stock': '4eb940115adc', 'op': 'jsr', 'target': 'bm_draw'}
    need(0x400d3ac4, site['stock'], 'jsr 0x40115adc, the logo blit')
    rows = []
    for y in range(64):
        rows.append(bytes(int(''.join(str(v) for v in bits[y][8 * i:8 * i + 8]), 2) for i in range(16)))
    h = ['/* written by dn2-cfw\'s scripts/ba03_make_boot.py: the mark "%s", 64 rows x 128 pixels from the top, one bit each */'
         % a.mark, 'static const u8 bm_mark[1024] __attribute__((aligned(4))) = {']
    for r in rows:
        h.append('    ' + ', '.join('0x%02x' % b for b in r) + ',')
    h.append('};')
    open(os.path.join(MODDIR, 'bm_mark.h'), 'w').write('\n'.join(h) + '\n')
    mod = {
        'id': 'boot-mark',
        'version': '0.2',
        'title': 'DIGIWERK in the start-up animation (base 0.2)',
        'category': 'Examples',
        'license': 'GPL-2.0-or-later',
        'author': 'naturarum',
        'description': 'EXPERIMENT: emulator-tested only. The start-up animation warps the DIGIWERK mark (%s) where it '
                       'warped Elektron\'s logo, the right way up (0.1 drew it upside down: the panel\'s rows run up); '
                       'the static screen before it (the logo and the OS version) is untouched. Our own pixel '
                       'letters. For dn2-cfw\'s base 0.2.' % a.mark,
        'device': 'digitone-mk2',
        'os': '1.12',
        'sources': ['bm.c'],
        'sites': [site],
        'resources': {'names': ['intro-logo']},
        'requires': ['core'],
    }
    with open(os.path.join(MODDIR, 'mod.json'), 'w') as f:
        json.dump(mod, f, indent=1)
        f.write('\n')
    if a.list:
        print('%s %s %s' % (site['addr'], site['op'], site['target']))
    print('1 site, mark %s (%d pixels) -> %s (boot-mark %s)' % (a.mark, sum(map(sum, bits)),
                                                              os.path.relpath(MODDIR, ROOT), mod['version']))


if __name__ == '__main__':
    main()
