#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""T1.4 flash-writer audit helpers for Digitone II OS 1.12.

Two jobs:
  refs <hexaddr>...   list every direct reference to a code address in the main OS
                      (jsr/jmp abs.l, jsr/bsr/bra/pea/lea pc-relative, and raw word32),
                      resolving the containing function via out/decomp/main_os.
  opcodes             dump the SPI command bytes (PUSHR immediates) in the flash-driver
                      region of the main OS, and in the bootstrap, and flag chip-erase.

Static only; no hardware. Addresses: main OS file offset 0 = 0x40000400,
bootstrap file offset 0 = 0x800003FC (code at 0x80000400).
"""
import sys, os, sqlite3, bisect

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAIN = os.path.join(ROOT, 'ele3_out/02_type3.unpacked.bin')
BOOT = os.path.join(ROOT, 'ele3_out/01_type2.unpacked.bin')
DB   = os.path.join(ROOT, 'out/decomp/main_os/xrefs.sqlite')
MAIN_BASE, BOOT_BASE = 0x40000400, 0x800003FC

def load_fnmap():
    db = sqlite3.connect(DB)
    rng = sorted(db.execute('select func,lo,hi from function_ranges').fetchall(),
                 key=lambda r: r[1])
    los = [r[1] for r in rng]
    names = dict(db.execute('select entry,name from functions').fetchall())
    def fn(a):
        i = bisect.bisect_right(los, a) - 1
        for j in range(max(0, i-3), min(len(rng), i+3)):
            if rng[j][1] <= a <= rng[j][2]:
                return rng[j][0]
        return None
    return fn, names

def refs(target):
    img = open(MAIN, 'rb').read(); base = MAIN_BASE
    out = []
    for i in range(0, len(img)-3, 2):
        a = base + i
        op = int.from_bytes(img[i:i+2], 'big')
        w  = int.from_bytes(img[i+2:i+6], 'big') if i+6 <= len(img) else None
        d16 = int.from_bytes(img[i+2:i+4], 'big', signed=True)
        if op in (0x4eb9, 0x4ef9) and w == target:
            out.append((a, 'jsr.l' if op == 0x4eb9 else 'jmp.l'))
        elif op in (0x4eba, 0x4efa, 0x6100, 0x6000, 0x487a) or (op & 0xf1ff) == 0x41fa:
            if a + 2 + d16 == target:
                out.append((a, {0x4eba:'jsr.pc',0x4efa:'jmp.pc',0x6100:'bsr.w',
                                0x6000:'bra.w',0x487a:'pea.pc'}.get(op, 'lea.pc')))
        elif (op & 0xff00) == 0x6100 and (op & 0xff) not in (0, 0xff):
            if a + 2 + ((op & 0xff) ^ 0x80) - 0x80 == target: out.append((a, 'bsr.b'))
        elif (op & 0xff00) == 0x6000 and (op & 0xff) not in (0, 0xff):
            if a + 2 + ((op & 0xff) ^ 0x80) - 0x80 == target: out.append((a, 'bra.b'))
        if int.from_bytes(img[i:i+4], 'big') == target:
            out.append((a, 'word32'))
    return out

def cmd_opcodes(path, base, lo, hi):
    img = open(path, 'rb').read()
    found = {}
    i = max(0, lo - base)
    end = min(len(img), hi - base)
    while i < end - 5:
        if img[i:i+2] == b'\x20\x3c':            # move.l #imm,d0
            v = int.from_bytes(img[i+2:i+6], 'big')
            if (v >> 24) in (0x80, 0x84, 0x08, 0x90) and (v & 0xff00) == 0:
                found.setdefault(v & 0xff, []).append(base + i)
        i += 2
    return found

def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ('refs', 'opcodes'):
        print(__doc__); return
    if sys.argv[1] == 'refs':
        fn, names = load_fnmap()
        for t in sys.argv[2:]:
            t = int(t, 16)
            print('==', hex(t), names.get(t, ''))
            for a, k in refs(t):
                f = fn(a)
                print('   ', hex(a), k, hex(f) if f else '-', names.get(f, '') if f else '')
    else:
        for nm, path, base, lo, hi in [('main_os (flash driver)', MAIN, MAIN_BASE,
                                        0x40134a00, 0x40136100),
                                       ('bootstrap (flash core)', BOOT, BOOT_BASE,
                                        0x80002000, 0x800029f0)]:
            ops = cmd_opcodes(path, base, lo, hi)
            print('==', nm)
            for k in sorted(ops):
                print('   cmd %#04x x%d  %s' % (k, len(ops[k]),
                                                ' '.join(hex(a) for a in ops[k][:4])))
            ce = [hex(k) for k in ops if k in (0xC7, 0x60)]
            print('   chip-erase 0xC7/0x60:', ce or 'none')

if __name__ == '__main__':
    main()
