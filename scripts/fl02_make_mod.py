#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""FL02: write vendor/elekloader/examples/flanger-sync-dn2/mod.json, flanger-sync-dn2 0.1 (sites checked against the
stock image).

    venv/bin/python scripts/fl02_make_mod.py [--list]

flanger-sync-dn2 0.1 (notes/T5.6-flanger-retune.md section 7): FUNC + turn on the flanger's SPD (the chorus page's knob
B, descriptor row 0x6a, group 16, k 26) steps through 12 tempo-synced speeds, stored in the knob's own value as
(a << 8) | i (sync.c). Two sites:
  - row 0x6a +0x34, the value text (stock 0x400e3030, the plain number) -> fls_fmt (`ptr`);
  - 0x400c5c96 `pea 0x42432ee8` in the display table's static initializer 0x400c2c4e (keep2) -> fls_tpl: the template
    row 0x6a's display entry +0x34 (the FUNC + turn function) is copied from. Checked here: the next instruction is
    `pea 0x42435eb8` (= the display table 0x4243425c + 0x44 x 0x6a + 0x34) and then `jsr (a4)`; a4 was loaded with
    the std::function copy constructor 0x401adfb2 (`lea 0x401adfb2,a4`) and no instruction between that load and the
    site writes a4 (objdump's listing of the range); the template's manager is 0x400bfbec. The emulator run (scripts/fl02_emurows.py) reads the built entry back.
"""
import json
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMAGE = os.path.join(ROOT, 'ele3_out', '02_type3.unpacked.bin')
OUT = os.path.join(ROOT, 'vendor', 'elekloader', 'examples', 'flanger-sync-dn2', 'mod.json')
LOAD = 0x40000400
DESC, ROW = 0x401f83a4, 0x3c
DISP, XROW = 0x4243425c, 0x44
ROW_SPD = 0x6a
FMT_NUMBER = 0x400e3030
SINIT = 0x400c2c4e
SITE_TPL = 0x400c5c96
TPL, COPY, MANAGER = 0x42432ee8, 0x401adfb2, 0x400bfbec

img = open(IMAGE, 'rb').read()


def rd(a, n):
    return img[a - LOAD:a - LOAD + n]


def u32(a):
    return struct.unpack('>I', rd(a, 4))[0]


def cstr(a):
    return rd(a, 40).split(b'\0')[0].decode()


a = DESC + ROW_SPD * ROW
want = {0x00: 0x10, 0x04: 26, 0x08: 0, 0x0c: 127 << 8, 0x10: 32 << 8, 0x34: FMT_NUMBER}
for off, v in want.items():
    if u32(a + off) != v:
        raise SystemExit('row 0x%x +0x%x: %#x, expected %#x' % (ROW_SPD, off, u32(a + off), v))
for off, old in ((0x28, 'Speed'), (0x30, 'SPD'), (0x2c, 'Chorus')):
    if cstr(u32(a + off)) != old:
        raise SystemExit('row 0x%x +0x%x: "%s", expected "%s"' % (ROW_SPD, off, cstr(u32(a + off)), old))
entry = DISP + XROW * ROW_SPD + 0x34
code = rd(SITE_TPL, 14)
if code != bytes.fromhex('4879%08x4879%08x4e94' % (TPL, entry)):
    raise SystemExit('0x%08x: %s, not pea 0x%08x; pea 0x%08x; jsr (a4)' % (SITE_TPL, code.hex(), TPL, entry))
# the last `lea abs.l,a4` (49f9) before the site in the static initializer, and no other write to a4 after it
lo, hi = SINIT - LOAD, SITE_TPL - LOAD
leas = [i for i in range(lo, hi, 2) if img[i:i + 2] == b'\x49\xf9']
last = leas[-1]
if struct.unpack('>I', img[last + 2:last + 6])[0] != COPY:
    raise SystemExit('a4 = %#x at %#x, not the copy constructor' % (struct.unpack('>I', img[last + 2:last + 6])[0],
                                                                       last + LOAD))
# no instruction between that load and the site writes a4 (objdump's disassembly of the range: a4 as the
# destination of a move, lea, arithmetic or a movem register list)
import re
import subprocess
import tempfile
with tempfile.NamedTemporaryFile(suffix='.bin') as tf:
    tf.write(img[last + 6:hi])
    tf.flush()
    lst = subprocess.run(['m68k-elf-objdump', '-D', '-b', 'binary', '-m', 'm68k:cfv4e',
                          '--adjust-vma=0x%x' % (last + 6 + LOAD), tf.name], capture_output=True, text=True,
                         check=True).stdout
ins = [ln for ln in lst.splitlines() if re.match(r'^[0-9a-f]+:\t', ln)]
bad = [ln for ln in ins if re.search(r',%a4$', ln) or (re.search(r'\tmoveml ', ln) and re.search(r',%[ad0-7/-]*a4', ln)
                                                          and not re.search(r'\tmoveml %[ad]', ln))]
if not ins or ins[-1].split(':')[0].strip() < '%x' % (SITE_TPL - 8) or bad:
    raise SystemExit('a4 may change between %#x and the site: %s' % (last + LOAD, bad[:5]))
if rd(COPY, 8) != bytes.fromhex('226f0004206f0008'):
    raise SystemExit('0x%08x is not the std::function copy constructor' % COPY)

sites = [
    {'addr': '0x%08x' % (a + 0x34), 'stock': rd(a + 0x34, 4).hex(), 'op': 'ptr', 'target': 'fls_fmt',
     'note': 'row 0x%x +0x34 (chorus page B, Speed): the value text 0x%08x -> fls_fmt (the 12 synced speeds by name)'
             % (ROW_SPD, FMT_NUMBER)},
    {'addr': '0x%08x' % SITE_TPL, 'stock': rd(SITE_TPL, 6).hex(), 'op': 'keep2', 'target': 'fls_tpl',
     'note': 'the display table\'s static initializer: pea 0x%08x (the shared FUNC + turn template) for row 0x%x\'s '
             '+0x34 -> fls_tpl (manager 0x%08x, invoker fls_func)' % (TPL, ROW_SPD, MANAGER)},
]

mod = {
    'id': 'flanger-sync',
    'version': '0.1',
    'title': 'Flanger speed in sync with the tempo (FL02)',
    'category': 'Examples',
    'license': 'GPL-2.0-or-later',
    'author': 'naturarum',
    'description': 'EXPERIMENT: emulator-tested only. For dn2-cfw\'s FL02 build, whose DSP runs a flanger in the '
                   'chorus slot: FUNC + turn on the chorus page\'s SPD (knob B) steps through 12 speeds in sync with '
                   'the project tempo, one LFO cycle per 16, 8, 4, 2 or 1 bars, 1/2, 1/4., 1/4, 1/8., 1/8, 1/16 or '
                   '1/32 note, named as the delay names its note values; a turn without FUNC goes back to the free '
                   'speeds. As the delay\'s FUNC + turn works: the row\'s display entry gets its own FUNC + turn '
                   'function, and its value text names the synced values. A synced speed is kept in the knob\'s own '
                   'value (a low byte, as the delay\'s fine values), so it is saved with the kit and nothing else is '
                   'stored; old kits keep their free speed. On a DSP without sync a synced value plays about that '
                   'speed at 120 BPM.',
    'device': 'digitone-mk2',
    'os': '1.12',
    'sources': ['sync.c'],
    'sites': [{k: v for k, v in s.items() if k != 'note'} for s in sites],
    'requires': ['core'],
}

if __name__ == '__main__':
    with open(OUT, 'w') as f:
        json.dump(mod, f, indent=1)
        f.write('\n')
    if '--list' in sys.argv:
        for s in sites:
            print('%s %-5s %-8s %s' % (s['addr'], s['op'], s['target'], s['note']))
    print('%d sites -> %s (flanger-sync %s)' % (len(sites), os.path.relpath(OUT, ROOT), mod['version']))
