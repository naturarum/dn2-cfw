#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""T1.4 verification: find every SPI command frame the Digitone II 1.12 images can send.

Two scans over m68k-elf-objdump linear disassemblies (made on first run under
out/T1.4/verify-1/):
  immediates  every 32-bit immediate, in any instruction and any register, whose value
              has the shape of a DSPI PUSHR word (CONT/CTAS/EOQ bits, a PCS byte, an
              8-bit frame). Grouped by the top half word and the frame byte.
  txns        every DSPI0 transaction start (a write to SR 0xFC05C02C) followed by the
              first PUSHR write (0xFC05C034), with the register's last immediate load,
              so the command byte of each transaction is shown.
Also a raw byte scan of each image for 32-bit words inside the four DSPI blocks.

Images: main OS (file 0 = 0x40000400), bootstrap (0x800003FC), section 4 (0x80000400).
Static only. Usage: python3 scripts/t1.4v_pushr_scan.py [immediates|txns|raw|all]
"""
import os, re, sys, subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'out/T1.4/verify-1')
IMGS = [('main', 'ele3_out/02_type3.unpacked.bin', 0x40000400),
        ('boot', 'ele3_out/01_type2.unpacked.bin', 0x800003FC),
        ('upd', 'ele3_out/03_type4.bin', 0x80000400)]
BLOCKS = {'DSPI0': 0xfc05c000, 'DSPI1': 0xfc03c000, 'DSPI2': 0xec038000, 'DSPI3': 0xec03c000}
LINE = re.compile(r'^([0-9a-f]+):\s+((?:[0-9a-f]{4} )+)\s*(\S+)\s*(.*)$')


def dis(nm, path, base):
    out = os.path.join(OUT, nm + '.dis')
    if not os.path.exists(out):
        os.makedirs(OUT, exist_ok=True)
        with open(out, 'w') as f:
            subprocess.run(['m68k-elf-objdump', '-D', '-b', 'binary', '-m', 'm68k:cfv4e',
                            '-EB', '--adjust-vma=%#x' % base, os.path.join(ROOT, path)],
                           stdout=f, check=True)
    rows = []
    for line in open(out):
        m = LINE.match(line.rstrip())
        if m:
            rows.append((int(m.group(1), 16), m.group(3), m.group(4)))
    return rows


def immediates():
    for nm, path, base in IMGS:
        cnt = {}
        for a, mn, ops in dis(nm, path, base):
            for im in re.findall(r'#(-?\d+)', ops):
                v = int(im) & 0xffffffff
                pcs = (v >> 16) & 0xff
                if (v & 0x03000000) or not pcs or pcs >= 0x40 or (v & 0xff00):
                    continue
                if (v >> 24) not in (0x80, 0x84, 0x88, 0x8c, 0x08, 0x0c, 0x90, 0x94, 0x98,
                                     0x10, 0x18, 0x00, 0x04):
                    continue
                if (v >> 24) == 0 and (v >> 16) < 0x10:
                    continue
                cnt.setdefault((v >> 16, v & 0xff), []).append(a)
        print('== immediates', nm)
        for k in sorted(cnt):
            print('  hi=%04x frame=%02x n=%d %s' % (k[0], k[1], len(cnt[k]),
                                                    ' '.join('%x' % x for x in cnt[k][:6])))
        ce = sorted({k[1] for k in cnt if k[0] in (0x8402, 0x0802) and k[1] in (0xc7, 0x60)})
        print('  chip erase 0xC7/0x60 as a DSPI0 command frame:', ce or 'none')


def txns():
    P, S = '0xfc05c034', '0xfc05c02c'
    for nm, path, base in IMGS:
        L = dis(nm, path, base)
        print('== txns', nm)
        for i, (a, mn, ops) in enumerate(L):
            if not (mn.startswith('move') and ops.endswith(',' + S)):
                continue
            for j in range(i + 1, min(i + 25, len(L))):
                a2, mn2, ops2 = L[j]
                if ops2.endswith(',' + P):
                    reg = ops2.split(',')[0]
                    src, v = '?', None
                    for k in range(j - 1, i, -1):
                        ak, mk, ok = L[k]
                        if ok.endswith(',' + reg) and mk in ('movel', 'moveq', 'oril', 'orl', 'mvzb'):
                            src = '%s %s @%x' % (mk, ok, ak)
                            mm = re.match(r'#(-?\d+)', ok)
                            if mk == 'movel' and mm:
                                v = int(mm.group(1)) & 0xffffffff
                            break
                    print('  txn@%x cmd@%x %s <- %s%s' % (a, a2, reg, src,
                          '  [%08x op=%02x]' % (v, v & 0xff) if v is not None else ''))
                    break


def raw():
    for nm, path, base in IMGS:
        b = open(os.path.join(ROOT, path), 'rb').read()
        hits = {}
        for i in range(0, len(b) - 3, 2):
            v = int.from_bytes(b[i:i + 4], 'big')
            for bn, bb in BLOCKS.items():
                if bb <= v < bb + 0x100:
                    hits.setdefault(bn, []).append(base + i)
        print('== raw', nm, {k: len(v) for k, v in hits.items()})
        for bn, v in hits.items():
            print('  %s %s' % (bn, ' '.join('%x' % x for x in v)))


if __name__ == '__main__':
    what = sys.argv[1] if len(sys.argv) > 1 else 'all'
    if what in ('immediates', 'all'):
        immediates()
    if what in ('txns', 'all'):
        txns()
    if what in ('raw', 'all'):
        raw()
