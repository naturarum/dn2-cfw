#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Base 0.2: write vendor/elekloader/examples/lfo-dejavu-dn2/mod.json, lfo-dejavu-dn2 0.2 (every site checked
against the stock image). 0.1 (DJ01's) is frozen in scripts/dj01/lfo-dejavu-dn2/ (scripts/dj01_make_mod.py).

    venv/bin/python scripts/ba02_make_dv.py [--list]

0.2 = 0.1's sites, the WAVE maximum 16 (KEY, the track's own scale and root), plus the trig modes ONE and HALF
from the start phase, then hold (dv.s, lfx_*;
notes/BA02-base-0.2.md): A's trig 0x40137c40, its waveform's phase 0x40137ccc and the page's copy 0x40137dbc, its two
stop-value stores; B's 0x40137836, 0x401378d8, 0x401379d8 and its two stores. Every replaced range is checked: the
stock bytes, the instructions around it that the caves rely on, and no branch from outside into its middle.

lfo-dejavu-dn2 0.1 (notes/T2.8-dejavu-lfo.md): nine LFO waveforms after the stock seven (DV-F, DV-T, PMAJ .. OCTA),
a stored random-value loop with the Digitakt II "Deja Vu" behaviour. Sites (dv.s has the registers):
  - the three WAVE rows 0x4f, 0x59, 0x63 (LFO1-3, k 5, 13, 21): +0x0c the maximum 6 << 8 -> 15 << 8 (`bytes`) and
    +0x34 the value text 0x400e3610 -> dv_fmt (`ptr`); param-grow copies the stock rows at boot, these with them;
  - evaluator A (FUN_40137a06): its call 0x400272d4 (-> dv_frame_hook), the WAVE clamp (2 x `bytes`), the MODE read
    0x40137b88 (-> jsr dv_mode_a), the waveform table 0x40137cda (`keep2` -> dv_wtab_a), the add 0x40137d98 (-> jmp
    dv_add_a; nothing branches into 0x40137d9a..0x40137db1 from outside it);
  - evaluator B (FUN_401376bc): the MODE and WAVE reads 0x40137770 (-> jsr dv_mode_b + nop), the WAVE clamp
    (2 x `bytes`), the waveform table 0x401378ce (`keep2` -> dv_wtab_b).
The stock waveform table's six entries are checked against dv.s's copies (dv_wtab_a, dv_wtab_b).
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
MODDIR = os.path.join(ROOT, 'vendor', 'elekloader', 'examples', 'lfo-dejavu-dn2')   # 0.2
OUT = os.path.join(MODDIR, 'mod.json')
LOAD = 0x40000400
DESC, ROW = 0x401f83a4, 0x3c
WAVE_ROWS = {0x4f: 5, 0x59: 13, 0x63: 21}
FMT_WAVE = 0x400e3610
WTAB = 0x4020b7f8
EVAL_A, EVAL_B = 0x40137a06, 0x401376bc
END_A, END_B = 0x40137e0a, 0x40137a06

img = open(IMAGE, 'rb').read()


def rd(a, n):
    return img[a - LOAD:a - LOAD + n]


def u32(a):
    return struct.unpack('>I', rd(a, 4))[0]


def cstr(a):
    return rd(a, 40).split(b'\0')[0].decode()


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
    return got


sites = []


def site(addr, stock, op, what, **kw):
    need(addr, stock, what)
    s = {'addr': '0x%08x' % addr, 'stock': stock, 'op': op}
    s.update(kw)
    s['note'] = what
    sites.append(s)


# ---- the WAVE rows
for r, k in WAVE_ROWS.items():
    a = DESC + r * ROW
    want = {0x00: 0x1a + (k - 5) // 8, 0x04: k, 0x08: 0, 0x0c: 6 << 8, 0x34: FMT_WAVE}
    for off, v in want.items():
        if u32(a + off) != v:
            raise SystemExit('row 0x%x +0x%x: %#x, expected %#x' % (r, off, u32(a + off), v))
    for off, old in ((0x28, 'Waveform'), (0x30, 'WAVE')):
        if cstr(u32(a + off)) != old:
            raise SystemExit('row 0x%x +0x%x: "%s", expected "%s"' % (r, off, cstr(u32(a + off)), old))
    lfo = (k - 5) // 8 + 1
    site(a + 0x0c, '00000600', 'bytes', 'row 0x%x +0x0c (LFO%d WAVE, k %d): the maximum 6 << 8 -> 16 << 8' % (r, lfo, k),
         new='00001000')
    site(a + 0x34, '%08x' % FMT_WAVE, 'ptr', 'row 0x%x +0x34 (LFO%d WAVE): the value text 0x%08x -> dv_fmt (DV-F .. '
         'OCTA, KEY for 7..16, the stock text for 0..6)' % (r, lfo, FMT_WAVE), target='dv_fmt')

# ---- the stock waveform table: six functions, then the slew table
wt = [u32(WTAB + 4 * i) for i in range(6)]
dv_s = open(os.path.join(MODDIR, 'dv.s')).read()
copies = re.findall(r'^dv_wtab_[ab]:\n\s+\.long\s+([^\n]+)', dv_s, re.M)
for c in copies:
    vals = [int(x, 16) for x in c.split(',')[:6]]
    if vals != wt:
        raise SystemExit('dv.s copies %s of the waveform table, the image has %s' % (vals, wt))
if len(copies) != 2 or u32(WTAB + 0x18) != 0:
    raise SystemExit('the waveform table copies in dv.s, or 0x4020b810 (the slew table, first entry 0): not as read')

# ---- evaluator A
need(0x400272d4, '4eb9%08x' % EVAL_A, 'the frame handler\'s call of evaluator A')
calls = [ln for ln in listing(0x40025e36, 0x40027b00) if '0x%x' % EVAL_A in ln]
if len(calls) != 1:
    raise SystemExit('evaluator A: %d calls in the frame handler, expected 1' % len(calls))
site(0x400272d4, '4eb9%08x' % EVAL_A, 'jsr', 'FUN_40025e36 (the frame handler): jsr 0x40137a06, evaluator A, its only '
     'call -> dv_frame_hook (dv_frame += 1, then the stock call)', target='dv_frame_hook')
need(0x40137b56, '772c004c6c0442836008', 'A: mvs.b 0x4c(a4),d3 (WAVE); bge; clr.l d3; bra')
site(0x40137b60, '7e06', 'bytes', 'A: moveq #6,d7 (the WAVE clamp) -> moveq #16,d7', new='7e10', kind='code')
need(0x40137b62, 'be836c02', 'A: cmp.l d3,d7; bge.s')
site(0x40137b66, '7606', 'bytes', 'A: moveq #6,d3 (WAVE above 6 -> 6) -> moveq #16,d3', new='7610', kind='code')
site(0x40137b88, '732c00502406', 'jsr', 'A: mvs.b 0x50(a4),d1 (MODE); move.l d6,d2 -> jsr dv_mode_a (d1 = 0, FREE, '
     'on WAVE 7..15; d2 = d6)', target='dv_mode_a')
need(0x40137b8e, '206a0050d488', 'A: movea.l 0x50(a2),a0; add.l a0,d2 (no use of the condition codes)')
site(0x40137cda, '41f9%08x' % WTAB, 'keep2', 'A: lea 0x4020b7f8,a0 (the waveform table, indexed by d3) -> lea '
     'dv_wtab_a', target='dv_wtab_a')
need(0x40137ce0, '20703c002f024e90', 'A: movea.l (a0,d3*4),a0; move.l d2,-(sp); jsr (a0)')
site(0x40137d98, '7356d0816a04', 'jmp', 'A: mvs.w (a6),d1; add.l d1,d0; bpl (the add into row[DEST]) -> jmp dv_add_a '
     '(the same add, clamp and store, the scale rounding on WAVE 9..16, back at 0x40137db2)', target='dv_add_a')
need(0x40137d9e, '4280600e0c8000007f006f06203c00007f003c80', 'A: the clamp to 0..0x7f00 and move.w d0,(a6)')
# nothing outside 0x40137d98..0x40137db1 branches into its replaced part
lst = listing(EVAL_A, END_A)
into = []
for ln in lst:
    m = re.match(r'^ *([0-9a-f]+):\t.*\t(b[a-z]+|jmp|jsr|db[a-z]+)[a-z.]* +0x([0-9a-f]+)$', ln)
    if m:
        frm, to = int(m.group(1), 16), int(m.group(3), 16)
        if 0x40137d9a <= to <= 0x40137db1 and not 0x40137d98 <= frm <= 0x40137db1:
            into.append(ln)
if into:
    raise SystemExit('branches into the replaced add: %s' % into)

# ---- evaluator B
site(0x40137770, '7f2c002e772c002a', 'jsr', 'B: mvs.b 0x2e(a4),d7 (MODE); mvs.b 0x2a(a4),d3 (WAVE) -> jsr dv_mode_b '
     '+ nop (d3 = WAVE; d7 = 0, FREE, on WAVE 7..15)', target='dv_mode_b')
need(0x40137778, '588f206a00507947', 'B: addq.l #4,sp; movea.l 0x50(a2),a0; mvs.w d7,d4')
need(0x40137780, '4a836c0442836008', 'B: tst.l d3; bge; clr.l d3; bra')
site(0x40137788, '7c06', 'bytes', 'B: moveq #6,d6 (the WAVE clamp) -> moveq #16,d6', new='7c10', kind='code')
need(0x4013778a, 'bc836c02', 'B: cmp.l d3,d6; bge.s')
site(0x4013778e, '7606', 'bytes', 'B: moveq #6,d3 (WAVE above 6 -> 6) -> moveq #16,d3', new='7610', kind='code')
site(0x401378ce, '43f9%08x' % WTAB, 'keep2', 'B: lea 0x4020b7f8,a1 (the waveform table, indexed by d3) -> lea '
     'dv_wtab_b', target='dv_wtab_b')
need(0x401378d4, '22713c00', 'B: movea.l (a1,d3*4),a1')
for a, n in ((0x40137770, 8),):
    lstb = listing(EVAL_B, END_B)
    for ln in lstb:
        m = re.match(r'^ *([0-9a-f]+):\t.*\t(b[a-z]+)[a-z.]* +0x([0-9a-f]+)$', ln)
        if m and a < int(m.group(3), 16) < a + n:
            raise SystemExit('B: a branch into the replaced reads: %s' % ln)

# ---- 0.2: ONE and HALF from the start phase, then hold (dv.s lfx_*)
def no_branch_into(lo, hi, a, n, what):
    """no branch from outside [a, a + n) lands strictly inside it"""
    for ln in listing(lo, hi):
        m = re.match(r'^ *([0-9a-f]+):\t.*\t(b[a-z]+|jmp|jsr|db[a-z]+)[a-z.]* +0x([0-9a-f]+)$', ln)
        if m:
            frm, to = int(m.group(1), 16), int(m.group(3), 16)
            if a < to < a + n and not a <= frm < a + n:
                raise SystemExit('%s: a branch into the replaced bytes: %s' % (what, ln))


need(0x40137c32, '670c', 'A: beq.s 0x40137c40 (TRIG goes to the start point)')
site(0x40137c40, '7006b0836716', 'jmp', 'A: moveq #6,d0; cmp.l d3,d0; beq.s (RAND) -> jmp lfx_trig_a (TRIG: the stock '
     'start point at 0x40137c46; ONE, HALF: SPH latched, the phase from 0; RAND: phase 0)', target='lfx_trig_a')
need(0x40137c46, '242f0044203c5265c00048424242a4000800a1c260024282', 'A: the stock start point: move.l 0x44(sp),d2; '
     'move.l #1382400000,d0; swap; clr.w; macl; movclr.l acc0,d2; bra.s 0x40137c5e; clr.l d2')
need(0x40137c5e, '7e01736c0048', 'A: moveq #1,d7; mvs.w 0x48(a4),d1 (FADE, after the start point)')
need(0x40137b78, '2f420044', 'A: move.l d2,0x44(sp) (SPH, mvs.w 0x4e(a4) just before)')
no_branch_into(EVAL_A, END_A, 0x40137c40, 6, 'A trig')
site(0x40137ccc, '203c636ba875', 'jsr', 'A: move.l #1667999861,d0 (the phase\'s scale for the waveform) -> jsr '
     'lfx_eval_a (ONE, HALF with a latch: d2 = the phase + the start point; a latch in another mode folded into the '
     'phase; then the same d0)', target='lfx_eval_a')
need(0x40137cd2, 'a4000800a1c2e58a', 'A: macl d0,d2,acc0; movclr.l acc0,d2; lsl.l #2,d2')
need(0x40137c9a, '25420050', 'A: move.l d2,0x50(a2) (the phase stored before the waveform)')
no_branch_into(EVAL_A, END_A, 0x40137ccc, 6, 'A eval')
site(0x40137dbc, '256a00500070', 'jsr', 'A: move.l 0x50(a2),0x70(a2) (the page\'s phase copy, not stopped) -> jsr '
     'lfx_page_a (the shifted phase while latched)', target='lfx_page_a')
need(0x40137db2, '4a13670642aa00706006', 'A: tst.b (a3); beq.s 0x40137dbc; clr.l 0x70(a2); bra.s 0x40137dc2')
no_branch_into(EVAL_A, END_A, 0x40137dbc, 6, 'A page')
need(0x40137bc2, '41f94020b7a441f03c00', 'A: HALF\'s stop table, indexed by WAVE')
site(0x40137bcc, '25500054', 'bytes', 'A: move.l (a0),0x54(a2) (HALF\'s stop value into the output) -> nop nop (hold)',
     new='4e714e71', kind='code')
need(0x40137bfc, '20713c00', 'A: movea.l (a1,d3*4),a0 (ONE\'s stop value)')
site(0x40137c00, '25480054', 'bytes', 'A: move.l a0,0x54(a2) (ONE\'s stop value into the output) -> nop nop (hold)',
     new='4e714e71', kind='code')

need(0x4013782c, '6708', 'B: beq.s 0x40137836 (TRIG goes to the start point)')
site(0x40137836, '7406b4836716', 'jmp', 'B: moveq #6,d2; cmp.l d3,d2; beq.s (RAND) -> jmp lfx_trig_b', target='lfx_trig_b')
need(0x4013783c, '716c002c243c014997004840424' + '0a0020800a1c260024282', 'B: the stock start point: mvs.w 0x2c(a4),d0; '
     'move.l #21600000,d2; swap; clr.w; macl; movclr.l acc0,d2; bra.s 0x40137854; clr.l d2')
need(0x40137854, '7d6c0026', 'B: mvs.w 0x26(a4),d6 (FADE, after the start point)')
no_branch_into(EVAL_B, END_B, 0x40137836, 6, 'B trig')
need(0x401378ca, '103c0032', 'B: move.b #50,d0 (d0 = 50: 6 just before)')
need(0x401378c4, '7006b0836730', 'B: moveq #6,d0; cmp.l d3,d0; beq.s (RAND)')
site(0x401378d8, '4c002800203c7f421f4f', 'jsr', 'B: muls.l d0,d2; move.l #2135039823,d0 -> jsr lfx_eval_b + nop nop '
     '(the latch as A\'s, then the same multiply and constant; a1, the waveform, and d1 kept)', target='lfx_eval_b')
need(0x401378e2, 'a4000800a1c2e58a', 'B: macl d0,d2,acc0; movclr.l acc0,d2; lsl.l #2,d2')
need(0x40137898, '25420050', 'B: move.l d2,0x50(a2) (the phase stored before the waveform)')
no_branch_into(EVAL_B, END_B, 0x401378d8, 10, 'B eval')
site(0x401379d8, '256a00500070', 'jsr', 'B: move.l 0x50(a2),0x70(a2) (the page\'s phase copy) -> jsr lfx_page_b',
     target='lfx_page_b')
need(0x401379ce, '4a13670642aa00706006', 'B: tst.b (a3); beq.s 0x401379d8; clr.l 0x70(a2); bra.s 0x401379de')
need(0x4013797e, '7741', 'B: mvs.w d1,d3 (d3 = DEST from here: lfx_page_b reads WAVE again)')
no_branch_into(EVAL_B, END_B, 0x401379d8, 6, 'B page')
need(0x401377be, '41f94020b7a4', 'B: lea 0x4020b7a4,a0 (HALF\'s stop table)')
site(0x401377c4, '2ab03c00', 'bytes', 'B: move.l (a0,d3*4),(a5) (HALF\'s stop value) -> nop nop (hold)',
     new='4e714e71', kind='code')
need(0x401377f4, '43f94020b7c022313c00', 'B: ONE\'s stop table (+ speed)')
site(0x401377fe, '2a81', 'bytes', 'B: move.l d1,(a5) (ONE\'s stop value) -> nop (hold)', new='4e71', kind='code')

mod = {
    'id': 'lfo-dejavu',
    'version': '0.2',
    'title': 'Deja vu LFO waves: stored random loops and scales; ONE and HALF from the start phase (base 0.2)',
    'category': 'Examples',
    'license': 'GPL-2.0-or-later',
    'author': 'naturarum',
    'description': 'EXPERIMENT: emulator-tested only. Ten LFO waveforms after the stock seven, on all three LFOs of '
                   'every track (audio and MIDI): DV-F, a free-running loop of stored random values; DV-T, the same '
                   'restarted at its first value by a trig; PMAJ, PMIN, HIRA, WTON, DORI, LYDI, OCTA, the DV-F loop '
                   'rounded to that scale when DEST is the machine\'s tuning (TUNE, TUN1, TUN2, MAST, PITCH), DV-F on '
                   'any other destination; KEY, the same rounding in the track\'s own KEYBOARD SETUP scale and root, the '
                   'voice\'s note included (0.2). On these waves MODE is the loop length (FREE 2, TRIG 4, HOLD 8, ONE 16, '
                   'HALF 32 values) and SPH the mutation (0 a new value at every step, up to 63 fewer, 64 the loop '
                   'kept, above 64 more and more swaps, 127 a swap at every step); SPD and MULT set the rate (16 steps '
                   'per LFO cycle, as RAND), FADE and DEP are stock. The behaviour and names follow Kari Gardarsson\'s '
                   'Digitakt II Musical WAVE / Deja Vu (whose release credits Bezronczek\'s Syntakt Firmware Workbench), '
                   'an idea from the deja vu of Emilie Gillet\'s Marbles; this is our own code. WAVE 7..16 save, '
                   'load and p-lock as any stock value; the loops themselves are not saved (made anew after power-on). '
                   '0.2: on the stock waves the trig modes ONE and HALF run one cycle, or half a cycle, from the '
                   'start phase (SPH) and then hold their last value (stock stops them at fixed points of the cycle '
                   'and jumps to a fixed value). For dn2-cfw\'s base 0.2.',
    'device': 'digitone-mk2',
    'os': '1.12',
    'sources': ['dv.s', 'dejavu.c'],
    'sites': [{k: v for k, v in s.items() if k != 'note'} for s in sites],
    'resources': {'names': ['lfo-eval-a', 'lfo-eval-b', 'lfo-wave-rows']},
    'requires': ['core'],
}

if __name__ == '__main__':
    with open(OUT, 'w') as f:
        json.dump(mod, f, indent=1)
        f.write('\n')
    if '--list' in sys.argv:
        for s in sites:
            print('%s %-5s %-14s %s' % (s['addr'], s['op'], s.get('target', s.get('new', '')), s['note']))
    print('%d sites -> %s (lfo-dejavu %s)' % (len(sites), os.path.relpath(OUT, ROOT), mod['version']))
