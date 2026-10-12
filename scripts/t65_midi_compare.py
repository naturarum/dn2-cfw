#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""T6.5: compare two runs' DIN MIDI out logs (emu.live --midi-log) message by message.

    venv/bin/python scripts/t65_midi_compare.py A/midi.txt B/midi.txt [--out FILE] [--ignore-clock]

Bytes are parsed into messages (running status kept); real-time bytes (F8 clock, FA, FC) are
counted apart (--ignore-clock drops them). Times are ignored: the same keys arrive at slightly
different instruction counts in two builds. Prints the messages in A and B in order and where
the sequences first differ; verdict SAME if the message sequences are identical.
"""
import argparse
import sys


def messages(path):
    data = []
    for line in open(path):
        if line.startswith('# messages'):
            break                                  # the decoded list after the bytes
        if line.startswith('#') or not line.strip():
            continue
        parts = line.split()
        data.append(int(parts[2], 16))
    msgs, rt, cur, status = [], 0, [], None
    need = {0x80: 3, 0x90: 3, 0xa0: 3, 0xb0: 3, 0xc0: 2, 0xd0: 2, 0xe0: 3}
    for b in data:
        if b >= 0xf8:
            rt += 1
            continue
        if b & 0x80:
            if cur:
                msgs.append(tuple(cur))
            status = b
            cur = [b]
            if b >= 0xf0:
                msgs.append((b,))
                cur, status = [], None
            continue
        if not cur:
            if status is None:
                msgs.append(('stray', b))
                continue
            cur = [status]
        cur.append(b)
        if status and len(cur) >= need.get(status & 0xf0, 3):
            msgs.append(tuple(cur))
            cur = []
    if cur:
        msgs.append(tuple(cur))
    return msgs, rt


def fmt(m):
    return ' '.join('%02x' % x if isinstance(x, int) else str(x) for x in m)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('a')
    ap.add_argument('b')
    ap.add_argument('--out')
    ap.add_argument('--ignore-clock', action='store_true')
    a = ap.parse_args()
    ma, ra = messages(a.a)
    mb, rb = messages(a.b)
    out = ['A %s: %d messages, %d real-time bytes' % (a.a, len(ma), ra),
           'B %s: %d messages, %d real-time bytes' % (a.b, len(mb), rb)]
    first = next((i for i, (x, y) in enumerate(zip(ma, mb)) if x != y), None)
    if first is None and len(ma) != len(mb):
        first = min(len(ma), len(mb))
    if first is None:
        out.append('the message sequences are identical')
    else:
        out.append('first difference at message %d: A %s | B %s' % (
            first, fmt(ma[first]) if first < len(ma) else '-', fmt(mb[first]) if first < len(mb) else '-'))
        sa, sb = set(ma), set(mb)
        out.append('only in A: %s' % ', '.join(fmt(m) for m in sorted(sa - sb, key=str)[:40]))
        out.append('only in B: %s' % ', '.join(fmt(m) for m in sorted(sb - sa, key=str)[:40]))
    out.append('A: ' + ' | '.join(fmt(m) for m in ma[:400]))
    out.append('verdict: %s' % ('SAME' if first is None else 'DIFFERENT'))
    s = '\n'.join(out) + '\n'
    sys.stdout.write(s if len(s) < 6000 else s[:3000] + '\n...\n' + out[-1] + '\n')
    if a.out:
        open(a.out, 'w').write(s)
    return 0 if first is None else 1


if __name__ == '__main__':
    sys.exit(main())
