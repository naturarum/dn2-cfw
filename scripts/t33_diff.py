#!/usr/bin/env python3
"""T3.3: diff consecutive frame captures of one page session (scripts/t33_frame.py output).

  python3 scripts/t33_diff.py PAGE [PAGE ...]

Reads out/T3.3/frames/PAGE/*.{raw,tx}.bin and out/T3.3/snaps/PAGE/plan.txt, orders the steps
as the session ran them (s00-base, e1-up, e1-down, e2-up, ...), and for each step prints the
words that changed against the previous step:
  raw  = the raw parameter mirror 0x80003af0 (row r, index k at 0x22 + r*0xca + 2k; row 16 = FX)
  tx   = the TX frame 0x80005e60 after 400 handler passes (emulated smoothing, see the notes)
Writes out/T3.3/diffs/PAGE.txt and PAGE.json.
"""
import json
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def words(b):
    return struct.unpack('>%dH' % (len(b) // 2), b)


def raw_name(off):
    if off < 0x22:
        return 'hdr+%#x' % off
    r, rem = divmod(off - 0x22, 0xca)
    return 'row%d.idx%d' % (r, rem // 2)


def tx_name(off):
    if 0x9fa <= off < 0xa54:
        return 'global.idx%d' % (25 + (off - 0x9fa) // 2)
    if 0xda <= off < 0xda + 16 * 0x92:
        t, rem = divmod(off - 0xda, 0x92)
        k = 25 + rem // 2 if rem < 0x88 else 95 + (rem - 0x88) // 2
        return 'track%d.idx%d' % (t, k)
    return 'other'


def main():
    for page in sys.argv[1:]:
        fdir = os.path.join(ROOT, 'out', 'T3.3', 'frames', page)
        plan = {}
        with open(os.path.join(ROOT, 'out', 'T3.3', 'snaps', page, 'plan.txt')) as f:
            next(f)
            for line in f:
                name, _, enc, _, delta = line.split()
                plan[name] = (int(enc), int(delta))
        steps = ['s00-base'] + list(plan)
        out, js = [], []
        prev = None
        for s in steps:
            raw = open(os.path.join(fdir, s + '.raw.bin'), 'rb').read()
            tx = open(os.path.join(fdir, s + '.tx.bin'), 'rb').read()
            if prev is not None:
                pr, pt = prev
                rd = [(2 * i, a, b) for i, (a, b) in enumerate(zip(words(pr), words(raw))) if a != b]
                td = [(2 * i, a, b) for i, (a, b) in enumerate(zip(words(pt), words(tx))) if a != b]
                enc, delta = plan[s]
                out.append('%s  encoder %d delta %+d' % (s, enc, delta))
                for off, a, b in rd:
                    out.append('  raw %#06x %-14s %04x -> %04x' % (off, raw_name(off), a, b))
                for off, a, b in td:
                    out.append('  tx  %#06x %-14s %04x -> %04x' % (off, tx_name(off), a, b))
                if not rd and not td:
                    out.append('  (no change)')
                js.append({'step': s, 'encoder': enc, 'delta': delta,
                           'raw': [['%#06x' % o, raw_name(o), a, b] for o, a, b in rd],
                           'tx': [['%#06x' % o, tx_name(o), a, b] for o, a, b in td]})
            prev = (raw, tx)
        ddir = os.path.join(ROOT, 'out', 'T3.3', 'diffs')
        os.makedirs(ddir, exist_ok=True)
        with open(os.path.join(ddir, page + '.txt'), 'w') as f:
            f.write('\n'.join(out) + '\n')
        with open(os.path.join(ddir, page + '.json'), 'w') as f:
            json.dump(js, f, indent=1)
        print('== %s' % page)
        print('\n'.join(out))


if __name__ == '__main__':
    main()
