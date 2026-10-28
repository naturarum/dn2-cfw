#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Base 0.2: write scripts/ba02/arp-modes-dn2/mod.json, arp-modes-dn2 0.1 (every site checked against
the stock image): the arpeggiator's SHUF and RAND (am.s, am.c; notes/BA02-base-0.2.md).

    venv/bin/python scripts/ba02_make_arp.py [--list]

Sites: the step's dispatch after UP (0x4002a13c, 12 bytes: DOWN, else CYCL) -> jmp am_mode; the five clamps of MODE
to 4 (setMode 0x4004befa, 0x4004bf00; FUNC + ARP's restore 0x4004bfb4, 0x4004bfc6; the project loader 0x400dd694)
-> 6. Checked: the stock bytes of each site, the instructions around them that am.s relies on, that nothing branches
into the middle of the replaced dispatch, and that MODE is read at sound +0x15f by every instruction listed as doing
so (no other reader can see 5 or 6 and do something new with it).
"""
import json
import os
import re
import struct
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMAGE = os.path.join(ROOT, 'ele3_out', '02_type3.unpacked.bin')
MODDIR = os.path.join(ROOT, 'scripts', 'ba02', 'arp-modes-dn2')   # 0.1, frozen for BA02 (the fork's folder is 0.2 since BA03)
OUT = os.path.join(MODDIR, 'mod.json')
LOAD = 0x40000400
STEP, STEP_END = 0x4002a0bc, 0x4002a3a6

img = open(IMAGE, 'rb').read()


def rd(a, n):
    return img[a - LOAD:a - LOAD + n]


def listing(lo, hi):
    with tempfile.NamedTemporaryFile(suffix='.bin') as tf:
        tf.write(img[lo - LOAD:hi - LOAD])
        tf.flush()
        out = subprocess.run(['m68k-elf-objdump', '-D', '-b', 'binary', '-m', 'm68k:cfv4e',
                              '--adjust-vma=0x%x' % lo, tf.name], capture_output=True, text=True, check=True).stdout
    return [ln for ln in out.splitlines() if re.match(r'^ *[0-9a-f]+:\t', ln)]


def need(addr, hexbytes, what):
    got = rd(addr, len(hexbytes) // 2).hex()
    if got != hexbytes:
        raise SystemExit('0x%08x: %s, expected %s (%s)' % (addr, got, hexbytes, what))


sites = []


def site(addr, stock, op, what, **kw):
    need(addr, stock, what)
    s = {'addr': '0x%08x' % addr, 'stock': stock, 'op': op}
    s.update(kw)
    s['note'] = what
    sites.append(s)


# ---- the step's dispatch
need(0x4002a114, '1029015f7a017900ba8467286d0a4a00670002746000' + '0160', 'the step: move.b 0x15f(a1),d0 (MODE); '
     'moveq #1,d5; mvs.b d0,d4; cmp.l d4,d5; beq.s TRUE; blt.s; tst.b d0; beq.w (OFF: a rest); bra.w CYCL')
need(0x4002a12c, '24017e02e58a45f940599728be846754', 'the step, MODE > 1: move.l d1,d2; moveq #2,d7; lsl.l #2,d2; '
     'lea 0x40599728,a2; cmp.l d4,d7; beq.s UP')
site(0x4002a13c, '7003b084670000c660000144', 'jmp', 'the step: moveq #3,d0; cmp.l d4,d0; beq.w 0x4002a208 (DOWN); '
     'bra.w 0x4002a28a (CYCL) -> jmp am_mode + 3 nops (DOWN as stock; 5 SHUF, 6 RAND ours; anything else CYCL)',
     target='am_mode')
need(0x4002a208, '700390a80004', 'DOWN: moveq #3,d0; sub.l 4(a0),d0')
need(0x4002a28a, '240170ff', 'CYCL: move.l d1,d2; moveq #-1,d0')
need(0x4002a376, '4a806d222228001c2401e58a2140000ce989d3c3928275290166d081d082', 'the common tail: tst.l d0; blt (a rest); '
     'the octave +0x1c x 12, the last note +0x0c, the step\'s offset at sound + 0x166 + d3, added to d0')
for ln in listing(STEP, STEP_END):
    m = re.match(r'^ *([0-9a-f]+):\t.*\t(b[a-z]+|jmp|jsr)[a-z.]* +0x([0-9a-f]+)$', ln)
    if m:
        frm, to = int(m.group(1), 16), int(m.group(3), 16)
        if 0x4002a13c < to < 0x4002a148 and not 0x4002a13c <= frm < 0x4002a148:
            raise SystemExit('a branch into the replaced dispatch: %s' % ln)

# ---- the clamps
need(0x4004bef4, '6c04', 'setMode: bge.s (MODE >= 0)')
site(0x4004befa, '7404', 'bytes', 'setMode: moveq #4,d2 (the maximum) -> moveq #6,d2', new='7406', kind='code')
need(0x4004befc, 'b4816c02', 'setMode: cmp.l d1,d2; bge.s')
site(0x4004bf00, '7204', 'bytes', 'setMode: moveq #4,d1 (above it: 4) -> moveq #6,d1', new='7206', kind='code')
need(0x4004bf0c, '1141015f', 'setMode: move.b d1,0x15f(a0)')
site(0x4004bfb4, '7004', 'bytes', 'FUNC + ARP, the restore: moveq #4,d0 (the maximum) -> moveq #6,d0', new='7006',
     kind='code')
need(0x4004bfb6, '73290176b0816d08', 'FUNC + ARP: mvs.b 0x176(a1),d1 (the parked MODE); cmp.l d1,d0; blt.s')
site(0x4004bfc6, '7204', 'bytes', 'FUNC + ARP: moveq #4,d1 (above it: 4) -> moveq #6,d1', new='7206', kind='code')
need(0x4004bfc8, '1741015f', 'FUNC + ARP: move.b d1,0x15f(a3)')
need(0x400dd696, '102b014b7300b4816d0e', 'the loader: move.b 0x14b(a3),d0 (the stored MODE); mvs.b d0,d1; '
     'cmp.l d1,d2; blt.s (above the maximum: 0, OFF)')
site(0x400dd694, '7404', 'bytes', 'the project loader: moveq #4,d2 (the maximum MODE kept) -> moveq #6,d2', new='7406',
     kind='code')
need(0x400dd6b0, '1540015f', 'the loader: move.b d0,0x15f(a2)')

# ---- every reader of MODE (sound + 0x15f) by its encodings, so no other one meets 5 or 6 unseen
readers = {'0x40029cf4': 'the note set: zero or not', '0x4002a114': 'the step (above)',
           '0x4004be98': 'getMode (menu text, LEDs: zero or not)'}

mod = {
    'id': 'arp-modes',
    'version': '0.1',
    'title': 'Arp SHUF and RAND (base 0.2)',
    'category': 'Examples',
    'license': 'GPL-2.0-or-later',
    'author': 'naturarum',
    'description': 'EXPERIMENT: emulator-tested only. The arpeggiator\'s SHUF and RAND, which the Digitone II names but '
                   'plays as CYCL: SHUF plays every held note in every octave of RANGE once per cycle, in a new random '
                   'order each cycle; RAND a random held note in a random octave of RANGE at each step. The steps\' '
                   'on/off and offsets, LEN, SPEED and N.LEN work as in the stock modes. A kit saved with SHUF or RAND '
                   'opens on a stock OS with its arp OFF. Inspired by research findings of dn2_firmware_explore '
                   '(angellinares) on OS 1.11; this is our own code. For dn2-cfw\'s base 0.2.',
    'device': 'digitone-mk2',
    'os': '1.12',
    'sources': ['am.s', 'am.c'],
    'sites': [{k: v for k, v in s.items() if k != 'note'} for s in sites],
    'resources': {'names': ['arp-step-dispatch', 'arp-mode-bounds']},
    'requires': ['core'],
}

if __name__ == '__main__':
    with open(OUT, 'w') as f:
        json.dump(mod, f, indent=1)
        f.write('\n')
    if '--list' in sys.argv:
        for s in sites:
            print('%s %-5s %-10s %s' % (s['addr'], s['op'], s.get('target', s.get('new', '')), s['note']))
    print('%d sites -> %s (arp-modes %s)' % (len(sites), os.path.relpath(OUT, ROOT), mod['version']))
