#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""BA04: write vendor/elekloader/examples/arp-modes-dn2/mod.json, arp-modes-dn2 0.3 (every site checked against
the stock image): the arpeggiator's SHUF, RAND and TM, the Turing Machine loop of notes (am.s, am.c;
notes/BA04-turing-arp-v2.md). 0.2 (BA03) is frozen in scripts/ba03/ with scripts/ba03_make_arp.py.

    venv/bin/python scripts/ba04_make_arp.py [--list]

Sites (0.1's six, the clamps raised to 107; then the ARP menu's six, as in 0.2; 0.2's hook on the step's rest test is
gone, 0.3's loop needs nothing at a rest):
  - the step's dispatch after UP (0x4002a13c, 12 bytes: DOWN, else CYCL) -> jmp am_mode;
  - the five clamps of MODE to 4 (setMode 0x4004befa, 0x4004bf00; FUNC + ARP's restore 0x4004bfb4, 0x4004bfc6; the
    project loader 0x400dd694) -> 107 (TM is 7..107);
  - the ARP menu: MODE's name table entry 7 (0x401d4f38, CHRD) -> TM; its draw (vtable slot 4, 0x401d5090) and tick
    (slot 11, 0x401d50ac) -> ours, which call the stock ones; the draw's getMode for the name (0x400188de) and the
    knob handler's setMode for A (0x40018eee) and setOffset for E (0x400190ae) -> ours.
Checked: the stock bytes of each site, the instructions around them that am.s and am.c rely on, that nothing branches
into the middle of a replaced range, that each replaced pointer is in the image once, and that MODE is read at sound
+0x15f by the instructions listed (no other reader meets 7..107 and does something new with it).
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
MODDIR = os.path.join(ROOT, 'vendor', 'elekloader', 'examples', 'arp-modes-dn2')
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
     'bra.w 0x4002a28a (CYCL) -> jmp am_mode + 3 nops (DOWN as stock; 5 SHUF, 6 RAND, 7..107 TM ours; anything else CYCL)',
     target='am_mode')
need(0x4002a208, '700390a80004', 'DOWN: moveq #3,d0; sub.l 4(a0),d0')
need(0x4002a28a, '240170ff', 'CYCL: move.l d1,d2; moveq #-1,d0')
need(0x4002a376, '4a806d222228001c2401e58a2140000ce989d3c3928275290166d081d082', 'the common tail: tst.l d0; blt (a rest); '
     'the octave +0x1c x 12, the last note +0x0c, the step\'s offset at sound + 0x166 + d3, added to d0')
need(0x4002a0f8, '20105280208077290163b6806c024290', 'the step: move.l (a0),d0; addq.l #1,d0; '
     'move.l d0,(a0) (the step moves on); mvs.b 0x163(a1),d3 (LEN - 1); cmp.l d0,d3; bge.s; clr.l (a0) (past LEN: 0)')
need(0x4002a108, '71e901642610070067000288', 'the step\'s rest test: mvz.w 0x164(a1),d0 (the steps on); '
     'move.l (a0),d3 (the step); btst d3,d0; beq.w 0x4002a39a (a rest): TM\'s am_tm takes the step from (a0) too')
need(0x4002a39a, '70ff4cd71cfc4fef00244e75', 'the end: moveq #-1,d0 (a rest); movem.l (sp),d2-d7/a2-a4; '
     'lea 36(sp),sp; rts')
for ln in listing(STEP, STEP_END):
    m = re.match(r'^ *([0-9a-f]+):\t.*\t(b[a-z]+|jmp|jsr)[a-z.]* +0x([0-9a-f]+)$', ln)
    if m:
        frm, to = int(m.group(1), 16), int(m.group(3), 16)
        for lo, hi in ((0x4002a13c, 0x4002a148),):
            if lo < to < hi and not lo <= frm < hi:
                raise SystemExit('a branch into a replaced range: %s' % ln)

# ---- the clamps
need(0x4004bef4, '6c04', 'setMode: bge.s (MODE >= 0)')
site(0x4004befa, '7404', 'bytes', 'setMode: moveq #4,d2 (the maximum) -> moveq #107,d2', new='746b', kind='code')
need(0x4004befc, 'b4816c02', 'setMode: cmp.l d1,d2; bge.s')
site(0x4004bf00, '7204', 'bytes', 'setMode: moveq #4,d1 (above it: 4) -> moveq #107,d1', new='726b', kind='code')
need(0x4004bf0c, '1141015f', 'setMode: move.b d1,0x15f(a0)')
site(0x4004bfb4, '7004', 'bytes', 'FUNC + ARP, the restore: moveq #4,d0 (the maximum) -> moveq #107,d0',
     new='706b', kind='code')
need(0x4004bfb6, '73290176b0816d08', 'FUNC + ARP: mvs.b 0x176(a1),d1 (the parked MODE); cmp.l d1,d0; blt.s')
site(0x4004bfc6, '7204', 'bytes', 'FUNC + ARP: moveq #4,d1 (above it: 4) -> moveq #107,d1', new='726b', kind='code')
need(0x4004bfc8, '1741015f', 'FUNC + ARP: move.b d1,0x15f(a3)')
need(0x400dd696, '102b014b7300b4816d0e', 'the loader: move.b 0x14b(a3),d0 (the stored MODE); mvs.b d0,d1; '
     'cmp.l d1,d2; blt.s (above the maximum: 0, OFF)')
site(0x400dd694, '7404', 'bytes', 'the project loader: moveq #4,d2 (the maximum MODE kept) -> moveq #107,d2',
     new='746b', kind='code')
need(0x400dd6b0, '1540015f', 'the loader: move.b d0,0x15f(a2)')

# ---- the ARP menu (ArpSetupMenuView)
def once(word, what):
    n = img.count(struct.pack('>I', word))
    if n != 1:
        raise SystemExit('0x%08x is in the image %d times (%s)' % (word, n, what))


need(0x401d4f1c, '40213d4140212db14022dd7e40227c2e40212db640212dbb40220dcb', 'MODE\'s names: OFF TRUE UP DOWN CYCL '
     'SHUF RAND (the menu\'s constructor copies the 8 to view + 0x190)')
site(0x401d4f38, '40212dc0', 'ptr', 'MODE\'s name 7, CHRD (no stock menu reaches 7) -> TM', target='am_s_tm')
once(0x40018818, 'ArpSetupMenuView slot 4')
site(0x401d5090, '40018818', 'ptr', 'ArpSetupMenuView slot 4, the draw (0x40018818) -> am_menu_draw (calls it, then '
     'TM\'s knob E and step graph)', target='am_menu_draw')
once(0x40019a72, 'ArpSetupMenuView slot 11')
site(0x401d50ac, '40019a72', 'ptr', 'ArpSetupMenuView slot 11, the tick (0x40019a72) -> am_menu_tick (calls it, then '
     'redraws in TM when the loop changes or moves on)', target='am_menu_tick')
need(0x400188dc, '2f03', 'the draw: move.l d3,-(sp) (the sound)')
site(0x400188de, '4eb94004be74', 'jsr', 'the draw: jsr 0x4004be74 (getMode, for MODE\'s name at view + 0x190 + 4 x it) '
     '-> jsr am_mode_name (7 for TM)', target='am_mode_name')
need(0x400188e4, '71000680000000642f320c00', 'the draw: mvs.b d0,d0; addi.l #100,d0; move.l (a2,d0.l*4),-(sp)')
need(0x40018ee0, '4eb94004be74d0827100', 'knob A: jsr 0x4004be74 (getMode); add.l d2,d0 (the turn); mvs.b d0,d0')
need(0x40018eea, '2f002f03', 'knob A: move.l d0,-(sp) (MODE + the turn); move.l d3,-(sp) (the sound)')
site(0x40018eee, '4eb94004bea4', 'jsr', 'knob A: jsr 0x4004bea4 (setMode) -> jsr am_knob_mode (OFF .. RAND, TM, '
     'holding at TM; into TM with the track\'s last LOOP CHANGE)', target='am_knob_mode')
need(0x40019088, '2eaa022c24002f034eb94004bc3c', 'knob E: move.l 556(a2),(sp) (the selected step); move.l d0,d2 '
     '(the turn x 256); move.l d3,-(sp); jsr 0x4004bc3c (getOffset)')
need(0x400190a4, '48701800 2f2a022c2f03'.replace(' ', ''), 'knob E: pea (a0,d1.l) (the offset + the turn); '
     'move.l 556(a2),-(sp) (the step); move.l d3,-(sp) (the sound)')
site(0x400190ae, '4eb94004bc84', 'jsr', 'knob E: jsr 0x4004bc84 (setOffset) -> jsr am_knob_e (in TM, LOOP CHANGE '
     '0..100)', target='am_knob_e')

# ---- every reader of MODE (sound + 0x15f) by its encodings, so no other one meets 7..107 unseen
readers = {'0x40029cf4': 'the note set: zero or not', '0x4002a114': 'the step (above)',
           '0x4004be98': 'getMode (menu text, LEDs: zero or not)'}

mod = {
    'id': 'arp-modes',
    'version': '0.3',
    'title': 'Arp SHUF, RAND and TM, the Turing Machine loop (base 0.2)',
    'category': 'Examples',
    'license': 'GPL-2.0-or-later',
    'author': 'naturarum',
    'description': 'EXPERIMENT: emulator-tested only. The arpeggiator\'s SHUF and RAND, which the Digitone II names but '
                   'plays as CYCL: SHUF plays every held note in every octave of RANGE once per cycle, in a new random '
                   'order each cycle; RAND a random held note in a random octave of RANGE at each step. The steps\' '
                   'on/off and offsets, LEN, SPEED and N.LEN work as in the stock modes. TM (0.3), a Turing Machine loop '
                   'of notes after the eighth MODE: one value per step of ARP LENGTH, put on the notes from the lowest '
                   'one held up that are in the track\'s scale (KEYBOARD SETUP), over the octaves of RANGE; before a '
                   'step plays it takes another of those notes with the chance LOOP CHANGE / 100 (knob E in TM: 0 '
                   'locks the loop, 100 makes every step new every time round), a change touching that step only; the '
                   'step graph shows the loop\'s notes live; the steps\' offsets are not added in TM. TM is MODE '
                   '7..107 (LOOP CHANGE in the MODE byte), so kits keep their format. A kit saved with SHUF, RAND or TM opens on a stock OS with its arp '
                   'OFF. Inspired by research findings of dn2_firmware_explore (angellinares) on OS 1.11; TM after Tom '
                   'Whitwell\'s Turing Machine; this is our own code. For dn2-cfw\'s base 0.2.',
    'device': 'digitone-mk2',
    'os': '1.12',
    'sources': ['am.s', 'am.c'],
    'sites': [{k: v for k, v in s.items() if k != 'note'} for s in sites],
    'resources': {'names': ['arp-step-dispatch', 'arp-mode-bounds', 'arp-menu']},
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
