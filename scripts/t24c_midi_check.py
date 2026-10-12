#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""T2.4c: check a DIN MIDI out log from emu.live --midi-log against the project tempo.

  python3 scripts/t24c_midi_check.py LOG [BPM]
Reads the '# messages' part (wire times), prints the transport bytes, the clock rate and its spread
in audio blocks (1/1500 s), the note-on spacing in sixteenths, and note lengths.
"""
import collections, statistics, sys

path = sys.argv[1]
bpm = float(sys.argv[2]) if len(sys.argv) > 2 else 81.0
msgs, on = [], False
for line in open(path):
    if line.startswith('# messages'):
        on = True
        continue
    if on and line.strip() and not line.startswith('#'):
        t, hexmsg, *desc = line.split()
        msgs.append((float(t), bytes.fromhex(hexmsg), ' '.join(desc)))
print('messages: %d' % len(msgs))
for t, m, d in msgs:
    if m[0] in (0xFA, 0xFB, 0xFC):
        print('  %.4f  %s' % (t, d))
start = next((t for t, m, _ in msgs if m[0] == 0xFA), None)
stop = next((t for t, m, _ in msgs if m[0] == 0xFC), None)
clocks = [t for t, m, _ in msgs if m[0] == 0xF8 and (start is None or start < t) and (stop is None or t < stop)]
if len(clocks) > 2:
    d = [b - a for a, b in zip(clocks, clocks[1:])]
    rate = (len(clocks) - 1) / (clocks[-1] - clocks[0])
    print('clock while playing: %d ticks, %.3f/s -> %.2f BPM (expected %.3f/s at %.1f BPM); '
          'intervals in audio blocks %s' % (len(clocks), rate, rate * 60 / 24, bpm * 24 / 60, bpm,
                                            dict(collections.Counter(round(x * 1500) for x in d))))
step = 60.0 / bpm / 4
ons = [(t, m) for t, m, _ in msgs if m[0] & 0xF0 == 0x90 and m[2]]
offs = [(t, m) for t, m, _ in msgs if (m[0] & 0xF0 == 0x90 and not m[2]) or m[0] & 0xF0 == 0x80]
if ons:
    print('note-ons: %d; first %.4f s (%s after start)' % (
        len(ons), ons[0][0], '%.1f ms' % ((ons[0][0] - start) * 1000) if start else '-'))
    gaps = [(b[0] - a[0]) / step for a, b in zip(ons, ons[1:])]
    print('  spacing in sixteenths: %s' % ' '.join('%.3f' % g for g in gaps))
    lens = []
    for t, m in ons:
        off = next((u for u, n in offs if u > t and n[1] == m[1] and n[0] & 0x0F == m[0] & 0x0F), None)
        if off:
            lens.append((off - t) / step)
    if lens:
        print('  lengths in sixteenths: %s' % ' '.join('%.3f' % x for x in lens))
    print('  notes: %s' % sorted(set((m[0] & 0x0F) + 1 for _, m in ons)), 'channels;',
          sorted(set(m[1] for _, m in ons)), 'notes;', sorted(set(m[2] for _, m in ons)), 'velocities')
