#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Dump tables from the Digitone II OS 1.12 main OS image (ele3_out/02_type3.unpacked.bin).

  params  the parameter descriptor table at 0x401f83a4 (0x141 rows of 0x3c bytes)
  shapes  the keyboard chord SHAPE table at 0x4020e235 (4 signed semitone offsets per shape)
  pool    layout constants of the pattern note pool (printed, not read from the image)

Usage:
  venv/bin/python scripts/dn2_param_table.py params [--grep TEXT]
  venv/bin/python scripts/dn2_param_table.py shapes
  venv/bin/python scripts/dn2_param_table.py pool

Row layout (0x3c bytes, big endian), worked out in notes/T2.1-midi-voices.md:
  +0x00 group/type  +0x04 param id  +0x08 min<<8  +0x0c max<<8  +0x10 default<<8
  +0x14 ?  +0x18 (CC<<16)|0xffff or -1  +0x1c NRPN (msb<<7|lsb) or -1  +0x20 row index
  +0x24 ?  +0x28 long name ptr  +0x2c ?  +0x30 short name ptr  +0x34 formatter  +0x38 ?
The CC/NRPN reading of +0x18/+0x1c is an inference (it matches the published NOTE/VEL/LEN
numbers); everything else is checked against the code that reads the table.
"""
import argparse
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
IMAGE = os.path.join(HERE, '..', 'ele3_out', '02_type3.unpacked.bin')
BASE = 0x40000400            # address of file offset 0
TABLE = 0x401f83a4           # descriptor row 0
ROWS = 0x141                 # TrigParameterSet clamps the row index to <= 0x140
ROW = 0x3c
SHAPES = 0x4020e235          # FUN_40150204 reads (shape+1)*4 + 0x4020e235 - k, k = 4..1


def load(path):
    with open(path, 'rb') as f:
        return f.read()


class Img:
    def __init__(self, data):
        self.d = data

    def u32(self, a):
        return struct.unpack('>I', self.d[a - BASE:a - BASE + 4])[0]

    def s8(self, a):
        return struct.unpack('b', self.d[a - BASE:a - BASE + 1])[0]

    def cstr(self, a):
        if not BASE <= a < BASE + len(self.d):
            return None
        o = a - BASE
        e = self.d.find(b'\0', o)
        t = self.d[o:e]
        if len(t) < 64 and all(32 <= c < 127 or c == 10 for c in t):
            return t.decode()
        return None


def s32(v):
    return struct.unpack('>i', struct.pack('>I', v))[0]


def cmd_params(img, grep):
    print('row  addr        type id   min  max  def  CC   NRPN  fmt         long / short')
    for r in range(ROWS):
        a = TABLE + r * ROW
        w = [img.u32(a + 4 * i) for i in range(15)]
        long_, short = img.cstr(w[10]) or '?', img.cstr(w[12]) or '?'
        cc = (w[6] >> 16) if w[6] != 0xffffffff else -1
        nrpn = '%d:%d' % (w[7] >> 7, w[7] & 0x7f) if w[7] != 0xffffffff else '-'
        line = '%3x  0x%08x  %4s %4s %4d %4d %4d %4s %5s  0x%08x  %s / %s' % (
            r, a, '%x' % w[0] if w[0] != 0xffffffff else '-', '%x' % w[1] if w[1] != 0xffffffff else '-',
            s32(w[2]) >> 8, s32(w[3]) >> 8, s32(w[4]) >> 8,
            cc if cc >= 0 else '-', nrpn, w[13], long_, short)
        if grep and grep.lower() not in line.lower():
            continue
        print(line)


def cmd_shapes(img):
    for p5 in range(1, 24):
        offs = [img.s8(SHAPES + p5 * 4 - k) for k in (4, 3, 2, 1)]
        print('shape %2d (param %2d)  0x%08x  %s' % (p5 - 1, p5, SHAPES + p5 * 4 - 4, offs))


def cmd_pool():
    print("""pattern note pool, runtime (patternNotePool_t, at pattern_t + 0x6a50, 0x11400 bytes):
  +0x00000 entries[0x2000], 6 bytes: track, step, note, vel(-1=track default), len(-1=default), utm
  +0x0c000 head[16][128]  s16, first entry of (track, step), -1 = none
  +0x0d000 next[0x2000]   s16, next entry in the chain, -1 = end (chain sorted by utm)
  +0x11000 free bitmap    0x400 bytes, bit set = entry free
storage (patternNotePoolStorage_v0_t, at patternStorage_v4_t + 0x4a34): entries[0x2000] only,
  0xc000 bytes; track byte 0xff = unused. Load: FUN_400de4b0 rebuilds head/next/bitmap.
patternStorage_v4_t: +0 u32 version (4) | +4 16 x trackStorage (0x4a3) | +0x4a34 note pool |
  +0x10a34 param locks (0x50a0) | +0x15ad4 pattern settings""")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('cmd', choices=['params', 'shapes', 'pool'])
    ap.add_argument('--image', default=IMAGE)
    ap.add_argument('--grep')
    a = ap.parse_args()
    if a.cmd == 'pool':
        cmd_pool()
        return
    img = Img(load(a.image))
    if a.cmd == 'params':
        cmd_params(img, a.grep)
    else:
        cmd_shapes(img)


if __name__ == '__main__':
    sys.exit(main())
