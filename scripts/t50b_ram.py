#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""T5.0b: what the tour did to the parameters, stock against FTN1, from the emulator's snapshots.

    venv/bin/python scripts/t50b_ram.py [--a stock] [--b FTN1]

Reads the warm 800M snapshot each tour started from and the end.snap it saved
(out/T5.0b/emu/NAME/t50b-tour/end.snap; stock starts from out/T2.4a/warm/warm800M.snap).

1. The raw parameter mirror (0x80003af0, 0xd8c bytes; notes/T3.3-fx-frame.md): a 0x22-byte header
   (track levels) and 17 rows of 101 words, row r < 16 a voice row (0x80003b12 + 0xca*r + 2k), row 16
   the global FX block (0x800047b2 + 2k). Every word the tour changed, in each build, and whether
   the two builds end with the same mirror. The ColdFire-to-DSP frame is built from this mirror
   (per-voice slot +0x7a/+0x7c/+0x7e for k 86/87/88; global frame 0x9fa + 2*(k-25)).
2. The descriptor rows FTN1 renames, as the running OS has them: CC/NRPN words, k, range, and the
   names the pointers lead to (read from the snapshot's RAM, so from core's copy at 0x47E00000).
3. The page records 17 and 19 (0x42433c00 + 0x2c*id), whose names the boot built.
"""
import argparse
import json
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scripts'))
from t50_glyphs import Snap                       # noqa: E402

OUT = os.path.join(ROOT, 'out', 'T5.0b')
RAW, RAW_LEN = 0x80003af0, 0xd8c
DESC, ROW = 0x401f83a4, 0x3c
PAGES = 0x42433c00
ROWS = [0x43, 0x44, 0x6d, 0x6f, 0x78, 0x8b, 0x8c, 0x8d, 0x8e, 0x8f, 0x90]


def where(off):
    if off < 0x22:
        return 'header word %d (track %d level)' % (off // 2, off // 2 + 1)
    r, w = divmod(off - 0x22, 0xca)
    return ('row %d k %d' % (r, w // 2)) if r < 16 else ('global k %d' % (w // 2))


def mirror_changes(a, b):
    out = []
    for o in range(0, RAW_LEN, 2):
        x, y = a[o:o + 2], b[o:o + 2]
        if x != y:
            out.append((o, struct.unpack('>H', x)[0], struct.unpack('>H', y)[0]))
    return out


def cstr(m, a):
    return m.read(a, 40).split(b'\0')[0].decode('latin1')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--a', default='stock')
    ap.add_argument('--b', default='FTN1')
    a = ap.parse_args()
    snaps = {}
    for n in (a.a, a.b):
        warm = os.path.join(ROOT, 'out', 'T2.4a', 'warm', 'warm800M.snap') if n == 'stock' else \
            os.path.join(OUT, 'emu', n, 'warm800M.snap')
        snaps[n] = (Snap(warm), Snap(os.path.join(OUT, 'emu', n, 't50b-tour', 'end.snap')))
    rep = {}
    lines = []
    raws = {}
    for n, (w, e) in snaps.items():
        rw, re_ = w.read(RAW, RAW_LEN), e.read(RAW, RAW_LEN)
        raws[n] = (rw, re_)
        ch = mirror_changes(rw, re_)
        rep[n] = [{'offset': '0x%03x' % o, 'addr': '0x%08x' % (RAW + o), 'where': where(o),
                   'before': '0x%04x' % x, 'after': '0x%04x' % y} for o, x, y in ch]
        lines.append('%s: %d mirror words changed by the tour' % (n, len(ch)))
        for o, x, y in ch:
            lines.append('  0x%08x %-26s 0x%04x -> 0x%04x' % (RAW + o, where(o), x, y))
    same_start = raws[a.a][0] == raws[a.b][0]
    same_end = raws[a.a][1] == raws[a.b][1]
    lines.append('mirror at the start: %s; at the end: %s' % (
        'identical' if same_start else 'DIFFERENT', 'identical' if same_end else 'DIFFERENT'))

    lines.append('')
    lines.append('descriptor rows, as the running %s has them (end.snap), against %s:' % (a.b, a.a))
    rows = []
    sa, sb = snaps[a.a][1], snaps[a.b][1]
    for r in ROWS:
        ra, rb = sa.read(DESC + ROW * r, ROW), sb.read(DESC + ROW * r, ROW)
        wa, wb = struct.unpack('>15I', ra), struct.unpack('>15I', rb)
        same = [i for i in range(15) if wa[i] == wb[i]]
        diff = [i * 4 for i in range(15) if wa[i] != wb[i]]
        d = {'row': '0x%x' % r, 'cc_word_0x18': '0x%08x' % wb[6], 'nrpn_0x1c': '0x%08x' % wb[7],
             'id_0x04': wb[1], 'range': [wb[2] >> 8, wb[3] >> 8], 'formatter': '0x%08x' % wb[13],
             'words_differing': ['+0x%x' % x for x in diff],
             'long': [cstr(sa, wa[10]), cstr(sb, wb[10])], 'short': [cstr(sa, wa[12]), cstr(sb, wb[12])],
             'long_ptr': '0x%08x' % wb[10], 'short_ptr': '0x%08x' % wb[12]}
        rows.append(d)
        lines.append('  row 0x%02x CC %-10s NRPN %-10s id %3d: %-18s -> %-18s %-5s -> %-5s (differs only at %s)' % (
            r, d['cc_word_0x18'], d['nrpn_0x1c'], d['id_0x04'], d['long'][0], d['long'][1],
            d['short'][0], d['short'][1], ' '.join(d['words_differing'])))
    lines.append('')
    recs = {}
    for pid in (16, 17, 18, 19):
        out = []
        for n, (_, e) in snaps.items():
            base = PAGES + 0x2c * pid
            s, l = struct.unpack('>II', e.read(base, 8))
            rws = struct.unpack('>9I', e.read(base + 8, 36))
            out.append({'build': n, 'short': cstr(e, s), 'long': cstr(e, l), 'rows': ['0x%x' % x for x in rws]})
        recs[pid] = out
        lines.append('page record %d: %s' % (pid, '; '.join('%s "%s"/"%s" rows %s' % (
            o['build'], o['short'], o['long'], ' '.join(o['rows'])) for o in out)))
    with open(os.path.join(OUT, 'ram.json'), 'w') as f:
        json.dump({'mirror_changes': rep, 'mirror_identical_start': same_start,
                   'mirror_identical_end': same_end, 'rows': rows, 'page_records': recs}, f, indent=1)
    with open(os.path.join(OUT, 'ram.txt'), 'w') as f:
        f.write('\n'.join(lines) + '\n')
    print('\n'.join(lines))


if __name__ == '__main__':
    main()
