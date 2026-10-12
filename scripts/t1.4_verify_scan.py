#!/usr/bin/env python3
"""Cross-checks for the T1.4 flash-writer audit (Digitone II OS 1.12). Static only.

Subcommands:
  cmds            every DSPI PUSHR command word (0x8402_00XX / 0x0802_00XX / 0x8002_00XX)
                  loaded as an immediate into ANY data register (move.l #imm,Dn or
                  ori.l #imm,Dn) in the main OS, the bootstrap and the updater; flags
                  chip-erase (0xC7 / 0x60) in any frame form.
  dspi0           every 32-bit word 0xFC05C0xx (DSPI0 registers) in the main OS, grouped
                  by containing function (from out/decomp/main_os/xrefs.sqlite); words
                  outside any function are listed by address.
  urefs <hex>...  direct references (jsr/jmp abs.l, jsr/bsr/bra/lea/pea pc-relative,
                  raw word32) to code addresses in the updater (section 4, base 0x80000400).
  wrap <len>...   for an in-OS upgrade of a container of <len> bytes (FUN_40139104: erase
                  ceil(len/blk) blocks from 0x80000, 24-bit SPI address), print the first
                  and last erased address on the wire for 256 KB and 64 KB block parts.

Bases: main OS file offset 0 = 0x40000400, bootstrap 0x800003FC, updater 0x80000400.
"""
import bisect
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMAGES = [
    ('main_os', os.path.join(ROOT, 'ele3_out/02_type3.unpacked.bin'), 0x40000400),
    ('bootstrap', os.path.join(ROOT, 'ele3_out/01_type2.unpacked.bin'), 0x800003FC),
    ('updater', os.path.join(ROOT, 'ele3_out/03_type4.bin'), 0x80000400),
]
DB = os.path.join(ROOT, 'out/decomp/main_os/xrefs.sqlite')


def be32(b, i):
    return int.from_bytes(b[i:i + 4], 'big')


def be16(b, i):
    return int.from_bytes(b[i:i + 2], 'big')


def is_imm_load(op):
    # move.l #imm,Dn = 0x203c | n<<9 ; ori.l #imm,Dn = 0x0080 | n
    return (op & 0xf1ff) == 0x203c or (op & 0xfff8) == 0x0080


def cmd_cmds():
    for name, path, base in IMAGES:
        img = open(path, 'rb').read()
        found = {}
        for i in range(2, len(img) - 3, 2):
            v = be32(img, i)
            if (v >> 16) in (0x8402, 0x0802, 0x8002) and (v & 0xff00) == 0 \
                    and is_imm_load(be16(img, i - 2)):
                found.setdefault((v >> 16, v & 0xff), []).append(base + i - 2)
        print('==', name)
        for (hi, op) in sorted(found, key=lambda k: (k[1], k[0])):
            sites = found[(hi, op)]
            print('   %04x_00%02x x%-3d %s' % (hi, op, len(sites),
                                              ' '.join('%#x' % a for a in sites[:6])))
        ce = sorted({op for (_, op) in found if op in (0xC7, 0x60)})
        print('   chip-erase 0xC7/0x60 in any frame form:',
              ', '.join('%#x' % o for o in ce) if ce else 'none')


def load_fnmap():
    db = sqlite3.connect(DB)
    rng = sorted(db.execute('select func,lo,hi from function_ranges').fetchall(),
                 key=lambda r: r[1])
    los = [r[1] for r in rng]

    def fn(a):
        i = bisect.bisect_right(los, a) - 1
        for j in range(max(0, i - 5), min(len(rng), i + 5)):
            if rng[j][1] <= a <= rng[j][2]:
                return rng[j][0]
        return None
    return fn


def cmd_dspi0():
    name, path, base = IMAGES[0]
    img = open(path, 'rb').read()
    fn = load_fnmap()
    byfn, loose = {}, []
    for i in range(0, len(img) - 3, 2):
        v = be32(img, i)
        if 0xfc05c000 <= v <= 0xfc05c0ff:
            f = fn(base + i)
            if f is None:
                loose.append((base + i, v))
            else:
                byfn[f] = byfn.get(f, 0) + 1
    print('== DSPI0 words by function (main OS)')
    for f in sorted(byfn):
        print('   %#x  x%d' % (f, byfn[f]))
    print('== DSPI0 words outside any function: %d' % len(loose))
    for a, v in loose:
        print('   %#x  %#x' % (a, v))


def cmd_urefs(targets):
    name, path, base = IMAGES[2]
    img = open(path, 'rb').read()
    for t in targets:
        out = []
        for i in range(0, len(img) - 5, 2):
            a = base + i
            op = be16(img, i)
            d16 = int.from_bytes(img[i + 2:i + 4], 'big', signed=True)
            if op in (0x4eb9, 0x4ef9) and be32(img, i + 2) == t:
                out.append('%#x abs' % a)
            elif op in (0x4eba, 0x4efa, 0x6100, 0x6000, 0x487a) or (op & 0xf1ff) == 0x41fa:
                if a + 2 + d16 == t:
                    out.append('%#x pc16' % a)
            elif (op & 0xff00) in (0x6100, 0x6000) and (op & 0xff) not in (0, 0xff):
                if a + 2 + ((op & 0xff) ^ 0x80) - 0x80 == t:
                    out.append('%#x b8' % a)
            if be32(img, i) == t and op not in (0x4eb9, 0x4ef9):
                out.append('%#x word32' % a)
        print('%#x:' % t, ', '.join(out) if out else 'no direct reference')


def cmd_wrap(lengths):
    for ln in lengths:
        for blk in (0x40000, 0x10000):
            n = (ln - 1 + blk) // blk
            first, last = 0x80000, 0x80000 + (n - 1) * blk
            wire = sorted({(0x80000 + k * blk) & 0xffffff for k in range(n)})
            low = [a for a in wire if a < 0x80000]
            print('len %#x blk %#x: %d erases, %#x..%#x (wire last %#x)%s' % (
                ln, blk, n, first, last, last & 0xffffff,
                ('  WRAPS onto ' + ' '.join('%#x' % a for a in low)) if low else ''))


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    c = sys.argv[1]
    if c == 'cmds':
        cmd_cmds()
    elif c == 'dspi0':
        cmd_dspi0()
    elif c == 'urefs':
        cmd_urefs([int(x, 16) for x in sys.argv[2:]])
    elif c == 'wrap':
        cmd_wrap([int(x, 0) for x in sys.argv[2:]])
    else:
        print(__doc__)


if __name__ == '__main__':
    main()
