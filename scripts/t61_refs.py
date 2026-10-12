#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""T6.1: inventory of everything in the DN2 OS 1.12 main OS that is tied to the parameter
descriptor table's size (0x141 rows) or indexed by row id.

Reads the main OS image and the decompile dump (out/decomp/main_os: disasm/*.s, xrefs.sqlite).
Nothing is written outside --out.

  1. absolute 32-bit constants in instructions that point into (or one past) the descriptor
     table 0x401f83a4 .. 0x401fcee0 (row, field), found by scanning every instruction's bytes,
     not only what Ghidra labelled;
  2. the same for the display table in RAM 0x4243425c .. 0x424397b0 (0x44-byte rows);
  3. the same for the slot maps and CC/NRPN reverse maps built from the table (0x42c657a8 ..);
  4. every compare against #0x140 / #0x141 (and the other row-count forms: #0x13f, #0x142,
     0x4b3c = 0x141 * 0x3c, 0x5544 = 0x141 * 0x44), with the function and whether that function
     also names the descriptor or display table;
  5. functions that loop over all rows (a compare against the count that closes a loop).

Usage:
  venv/bin/python scripts/t61_refs.py [--out out/T6.1]
"""
import argparse
import glob
import os
import re
import sqlite3
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, '..')
IMAGE = os.path.join(ROOT, 'ele3_out', '02_type3.unpacked.bin')
DECOMP = os.path.join(ROOT, 'out', 'decomp', 'main_os')
BASE = 0x40000400
ROWS = 0x141
DESC, DROW = 0x401f83a4, 0x3c
DISP, XROW = 0x4243425c, 0x44
DESC_END = DESC + ROWS * DROW          # 0x401fcee0
DISP_END = DISP + ROWS * XROW          # 0x424397b0
# tables built from the descriptor table by FUN_400dc634 (names from the code that fills them)
MAPS = [
    (0x42c657a8, 4, 'built flag'),
    (0x42c657ac, 0x194, 'MIDI slot->row (101)'),
    (0x42c65940, 0x68, 'trig slot->row (26)'),
    (0x42c659a8, 0x194, 'FX slot->row (101)'),
    (0x42c65b3c, 0x194, 'sound slot->row (101)'),
    (0x42c65cd0, 0x48, 'filter-type slot->row (6x3)'),
    (0x42c65d18, 0x320, 'machine slot->row (5x40)'),
    (0x42c66038, 0x63c, 'MIDI NRPN->row (399)'),
    (0x42c66674, 0x200, 'MIDI CC->row (128)'),
    (0x42c66874, 0x63c, 'sound NRPN->row (399)'),
    (0x42c66eb0, 0x200, 'sound CC->row (128)'),
    (0x42c670b0, 0x63c, 'FX NRPN->row (399)'),
    (0x42c676ec, 0x200, 'FX CC->row (128)'),
    (0x42c678ec, 6 * 0x18, 'filter NRPN->row std::map x6'),
    (0x42c6797c, 6 * 0x18, 'filter CC->row std::map x6'),
    (0x42c67a0c, 5 * 0x18, 'machine NRPN->row std::map x5'),
    (0x42c67a84, 5 * 0x18, 'machine CC->row std::map x5'),
]
FIELDS = {0x00: 'group', 0x04: 'k', 0x08: 'min', 0x0c: 'max', 0x10: 'default', 0x14: 'bipolar',
          0x18: 'CC', 0x1a: 'CC2', 0x1c: 'NRPN', 0x20: 'ordinal', 0x24: 'modmask',
          0x28: 'long name', 0x2c: 'group name', 0x30: 'short name', 0x34: 'formatter',
          0x38: 'suffix'}
COUNT_IMMS = {0x13f: 'count-2', 0x140: 'count-1', 0x141: 'count', 0x142: 'count+1',
              0x4b3c: 'count*0x3c', 0x5544: 'count*0x44', 0x504: 'count*4', 0x282: 'count*2'}


def load_disasm():
    """addr -> (bytes as int list of 16-bit words, text, func) for every instruction."""
    ins = {}
    for path in glob.glob(os.path.join(DECOMP, 'disasm', '*.s')):
        func = os.path.basename(path)[:-2]
        with open(path) as f:
            for line in f:
                m = re.match(r'^([0-9a-f]{8})  ((?:[0-9a-f]{4} )+)\s*(.*)$', line.rstrip('\n'))
                if not m:
                    continue
                a = int(m.group(1), 16)
                words = [int(w, 16) for w in m.group(2).split()]
                ins[a] = (words, m.group(3).strip(), func)
    return ins


def classify(v):
    if DESC <= v <= DESC_END:
        if v == DESC_END:
            return 'desc', 'end of table'
        off = v - DESC
        row, fld = divmod(off, DROW)
        return 'desc', 'row 0x%x +0x%02x %s' % (row, fld, FIELDS.get(fld, '?'))
    if DISP <= v <= DISP_END:
        if v == DISP_END:
            return 'disp', 'end of table'
        row, fld = divmod(v - DISP, XROW)
        return 'disp', 'row 0x%x +0x%02x' % (row, fld)
    for a, n, name in MAPS:
        if a <= v < a + n:
            return 'map', '%s +0x%x' % (name, v - a)
    return None, None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--out', default=os.path.join(ROOT, 'out', 'T6.1'))
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    img = open(IMAGE, 'rb').read()
    ins = load_disasm()
    db = sqlite3.connect(os.path.join(DECOMP, 'xrefs.sqlite'))
    fname = dict(db.execute('select entry, name from functions'))

    # 1-3: absolute constants inside instructions
    rows = []
    for addr, (words, text, func) in sorted(ins.items()):
        bs = b''.join(struct.pack('>H', w) for w in words)
        for o in range(2, len(bs) - 3, 2):          # skip the opcode word
            v = struct.unpack('>I', bs[o:o + 4])[0]
            kind, what = classify(v)
            if kind:
                rows.append((kind, addr, func, v, what, text))
    # constants in data (pointer tables), outside any instruction
    covered = set()
    for addr, (words, _, _) in ins.items():
        for i in range(len(words) * 2):
            covered.add(addr + i)
    data_hits = []
    for o in range(0, len(img) - 3, 2):
        addr = BASE + o
        if addr in covered:
            continue
        v = struct.unpack('>I', img[o:o + 4])[0]
        kind, what = classify(v)
        if kind and not (DESC <= addr < DESC_END):   # rows pointing at their own strings are not refs
            data_hits.append((kind, addr, v, what))

    # 4: count compares and other count-derived immediates
    cmp_rows = []
    for addr, (words, text, func) in sorted(ins.items()):
        m = re.search(r'#(0x[0-9a-f]+)\b', text)
        if not m:
            continue
        imm = int(m.group(1), 16)
        if imm not in COUNT_IMMS:
            continue
        if imm in (0x504, 0x282) and not text.startswith(('cmp', 'pea', 'move.l #', 'addi', 'subi')):
            continue
        cmp_rows.append((addr, func, imm, COUNT_IMMS[imm], text))
    funcs_desc = {r[2] for r in rows if r[0] == 'desc'}
    funcs_disp = {r[2] for r in rows if r[0] == 'disp'}

    with open(os.path.join(a.out, 'refs-code.tsv'), 'w') as f:
        f.write('table\tsite\tfunction\ttarget\twhat\tinstruction\n')
        for kind, addr, func, v, what, text in rows:
            f.write('%s\t0x%08x\t%s\t0x%08x\t%s\t%s\n' % (kind, addr, func, v, what, text))
    with open(os.path.join(a.out, 'refs-data.tsv'), 'w') as f:
        f.write('table\taddr\ttarget\twhat\n')
        for kind, addr, v, what in data_hits:
            f.write('%s\t0x%08x\t0x%08x\t%s\n' % (kind, addr, v, what))
    with open(os.path.join(a.out, 'count-imms.tsv'), 'w') as f:
        f.write('site\tfunction\timm\tmeaning\tnames_desc\tnames_disp\tinstruction\n')
        for addr, func, imm, meaning, text in cmp_rows:
            f.write('0x%08x\t%s\t0x%x\t%s\t%d\t%d\t%s\n' % (addr, func, imm, meaning,
                                                           func in funcs_desc, func in funcs_disp, text))

    # summary
    out = []
    for kind in ('desc', 'disp', 'map'):
        sel = [r for r in rows if r[0] == kind]
        fs = sorted({r[2] for r in sel})
        out.append('%s: %d instruction operands in %d functions' % (kind, len(sel), len(fs)))
        if kind == 'desc':
            base = [r for r in sel if r[3] == DESC]
            out.append('  of which the base itself (row 0 +0): %d sites; other rows/fields: %d' %
                       (len(base), len(sel) - len(base)))
            nz = sorted({(r[3], r[4]) for r in sel if r[3] != DESC})
            for v, what in nz:
                out.append('    0x%08x %s: %s' % (v, what, ', '.join('0x%08x' % r[1] for r in sel if r[3] == v)))
        if kind == 'disp':
            others = [r for r in sel if not r[2].startswith('400c2c4e')]
            out.append('  outside the boot builder FUN_400c2c4e: %d' % len(others))
            for r in others:
                out.append('    0x%08x %s 0x%08x %s | %s' % (r[1], r[2], r[3], r[4], r[5]))
    out.append('data words pointing into the tables (outside instructions): %d' % len(data_hits))
    for kind, addr, v, what in data_hits[:40]:
        out.append('    0x%08x -> 0x%08x %s %s' % (addr, v, kind, what))
    by_imm = {}
    for addr, func, imm, meaning, text in cmp_rows:
        by_imm.setdefault(meaning, []).append((addr, func, text))
    for meaning, lst in sorted(by_imm.items()):
        tied = [x for x in lst if x[1] in funcs_desc or x[1] in funcs_disp]
        out.append('%s immediates: %d sites in %d functions; %d in functions that name a table' %
                   (meaning, len(lst), len({x[1] for x in lst}), len(tied)))
    cmps = [c for c in cmp_rows if c[2] in (0x140, 0x141) and c[4].startswith(('cmp', 'cmpa'))]
    out.append('cmp #0x140/#0x141: %d sites in %d functions; functions that do not name either table:' %
               (len(cmps), len({c[1] for c in cmps})))
    for c in cmps:
        if c[1] not in funcs_desc and c[1] not in funcs_disp:
            out.append('    0x%08x %s %s' % (c[0], c[1], c[4]))
    txt = '\n'.join(out) + '\n'
    with open(os.path.join(a.out, 'refs-summary.txt'), 'w') as f:
        f.write(txt)
    sys.stdout.write(txt)


if __name__ == '__main__':
    main()
