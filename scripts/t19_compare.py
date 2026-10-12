#!/usr/bin/env python3
"""T1.9: compare a candidate's emulator run with stock's.

    python3 scripts/t19_compare.py midi STOCK/midi.txt CAND/midi.txt [--expect hooktest|same]
    python3 scripts/t19_compare.py screens STOCK_DIR CAND_DIR

midi: reads the '# messages' part of both logs (emu.live --midi-log), times relative to each run's
START (FA). Checks the message sequence: with --expect same, every message equal; with --expect
hooktest, notes +12 and note-on velocity = step + 1 (step from the time since START at the clock's
tempo, 6 clocks per sixteenth), everything else equal. Prints the timing differences (ms) between
matching messages and the clock tempo of each run.
screens: compares every NAME.png present in both folders, pixel for pixel (the .txt dumps).
"""
import os
import sys


def messages(path):
    out, on = [], False
    for ln in open(path):
        if ln.startswith('# messages'):
            on = True
            continue
        if on and ln.strip() and not ln.startswith('#'):
            t, hx, *_ = ln.split()
            out.append((float(t), bytes.fromhex(hx)))
    return out


def rel(msgs):
    t0 = next(t for t, m in msgs if m[0] == 0xFA)
    end = next((t for t, m in msgs if m[0] == 0xFC), msgs[-1][0])
    # from START to STOP, plus the note-offs that come after STOP
    return [(t - t0, m) for t, m in msgs if t >= t0 and (t <= end or m[0] & 0xF0 in (0x80, 0x90))]


def tempo(msgs):
    clocks = [t for t, m in msgs if m[0] == 0xF8]
    if len(clocks) < 3:
        return None
    return (len(clocks) - 1) / (clocks[-1] - clocks[0]) * 60 / 24


def midi(a, b, expect):
    A, B = rel(messages(a)), rel(messages(b))
    print('stock: %d messages START..STOP, candidate: %d' % (len(A), len(B)))
    print('clock tempo while playing: stock %.2f BPM, candidate %.2f BPM'
          % (tempo(A), tempo(B)))
    na = [(t, m) for t, m in A if m[0] != 0xF8]
    nb = [(t, m) for t, m in B if m[0] != 0xF8]
    ca = [t for t, m in A if m[0] == 0xF8]
    cb = [t for t, m in B if m[0] == 0xF8]
    bad = 0
    if len(na) != len(nb):
        print('DIFFERENT number of non-clock messages: %d vs %d' % (len(na), len(nb)))
        bad += 1
    sixteenth = None
    if len(ca) > 2:
        sixteenth = 6 * (ca[-1] - ca[0]) / (len(ca) - 1)
    print('%9s %-8s %9s %-8s %8s  %s' % ('stock t', 'msg', 'cand t', 'msg', 'dt ms', 'check'))
    for (ta, ma), (tb, mb) in zip(na, nb):
        want = ma
        if expect == 'hooktest' and ma[0] & 0xF0 == 0x90:
            note = ma[1] + 12
            vel = ma[2]
            if vel and sixteenth:
                vel = round(ta / sixteenth) + 1
            want = bytes([ma[0], note, vel])
        ok = mb == want
        bad += not ok
        print('%9.4f %-8s %9.4f %-8s %+8.2f  %s' % (ta, ma.hex(), tb, mb.hex(), (tb - ta) * 1000,
                                                    'ok' if ok else 'WANT %s' % want.hex()))
    n = min(len(ca), len(cb))
    if n:
        d = [(y - x) * 1000 for x, y in zip(ca[:n], cb[:n])]
        print('clock ticks: stock %d, candidate %d; first %d differ by %.2f..%.2f ms'
              % (len(ca), len(cb), n, min(d), max(d)))
    print('RESULT: %s' % ('as expected' if not bad else '%d differences' % bad))
    return bad


def screens(a, b):
    bad = 0
    names = sorted(f[:-4] for f in os.listdir(a) if f.endswith('.txt') and not f.endswith('.leds.txt') and f != 'midi.txt')
    for nm in names:
        pa, pb = os.path.join(a, nm + '.txt'), os.path.join(b, nm + '.txt')
        if not os.path.exists(pb):
            print('%-14s missing in the candidate' % nm)
            bad += 1
            continue
        ra, rb = open(pa).read().splitlines(), open(pb).read().splitlines()
        diff = sum(x != y for la, lb in zip(ra, rb) for x, y in zip(la, lb))
        diff += abs(len(ra) - len(rb))
        print('%-14s %s' % (nm, 'identical' if not diff else '%d pixels differ' % diff))
        bad += bool(diff)
    print('RESULT: %d of %d screens differ' % (bad, len(names)))
    return bad


if __name__ == '__main__':
    if sys.argv[1] == 'midi':
        exp = sys.argv[sys.argv.index('--expect') + 1] if '--expect' in sys.argv else 'same'
        sys.exit(1 if midi(sys.argv[2], sys.argv[3], exp) else 0)
    sys.exit(1 if screens(sys.argv[2], sys.argv[3]) else 0)
