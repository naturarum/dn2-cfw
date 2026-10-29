#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""BA04: arp-modes 0.3's TM, the Turing Machine loop of notes, with the arpeggiator's step FUN_4002a0bc run as a
function on BA02's and BA04's main OS images (Unicorn, digikit's ColdFire model), as scripts/ba02_arpcheck.py runs it.

    cd vendor/digikit && uv run python ../../scripts/ba04_arpcheck.py [--json ../../out/BA04/arpcheck.json] [--name BA04]

The pool: the notes from the lowest note held up to below it + 12 x (RANGE + 1) (and below 128) that are in the
track's scale, KEYBOARD SETUP's in the pattern playing (scale and root per pattern, or per track when bit 2 of the
pattern's config is set; the scales read from the stock table 0x4020e358; Chromatic or no pattern: all twelve).
Checks:
  1. TRUE, UP, DOWN, CYCL, SHUF, RAND, 108 and -1 (CYCL): BA04's notes BA02's, note for note, with the same random
     seed, over random held sets, RANGE, LEN, steps off and offsets;
  2. TM, step by step (am_tml, the loop's 16 values): an on step plays its value put on the pool, pool[(v x n) >> 8];
     only the step played can change, and a change is another note of the pool (its value the middle of that note's
     share); never at LOOP CHANGE 0, always at 100 (pool above 1), about LOOP CHANGE % of the time between (binomial
     bounds); an off step rests (-1) and changes nothing; the octave above the lowest note held at +0x1c, the note
     less those octaves at +0x0c; offsets not added; over Chromatic, pattern and per-track scales and roots, no pattern;
  3. a new loop on first use (made 0): seeded, values spread;
  4. LOOP CHANGE 0 locks the loop: the notes repeat every LEN steps from the first; 100 with LEN 1: every note another,
     the new notes spread evenly over the rest of the pool (chi-square);
  5. the other notes held don't matter (the same notes with only the lowest held); a new lowest note keeps the loop's
     values, the melody moved with it in the scale;
  6. no note held: the step stops before TM (-2), the loop kept.
"""
import argparse
import json
import os
import random
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scripts'))
sys.dont_write_bytecode = True
import ba02_arpcheck as A  # noqa: E402

STATE, HELD, NODES, HEAD = A.STATE, A.HELD, A.NODES, A.HEAD
TRUE, UP, DOWN, CYCL, SHUF, RAND = A.TRUE, A.UP, A.DOWN, A.CYCL, A.SHUF, A.RAND
PAT, PLAYING, SCALE_TAB = 0x10400000, 0x42c5b9a0, 0x4020e358
LOAD = 0x40000400
TML = 20                                                # struct am_tm: v[16], made, 3 bytes of padding


class Tm(A.Arp):
    def __init__(self, name):
        super().__init__(name)
        for a in (PAT, PAT + 0x100000, 0x42c00000):
            self.m.ensure(a)

    def reset_tm(self, seed, values=None):
        """the random source, and every track's loop unmade, or made with the values given ({track: 16 values})"""
        self.wr(self.mp['am_rng'], struct.pack('>I', seed))
        self.wr(self.mp['am_tml'], bytes(TML * 16))
        self.wr(self.mp['am_chg'], bytes(16))
        for t, v in (values or {}).items():
            self.wr(self.mp['am_tml'] + TML * t, bytes(v) + b'\x01\0\0\0')

    def loop(self, t):
        b = self.rd(self.mp['am_tml'] + TML * t, TML)
        return list(b[:16]), b[16]

    def st(self, t):
        return struct.unpack('>IIIiIIIII', self.rd(STATE + 40 * t, 36))

    def hold(self, t, notes):
        """the held notes only (the 128 bits, the press-order list, its head), the arp's state left as it is"""
        held = [0, 0, 0, 0]
        for n in notes:
            held[n >> 5] |= 1 << (n & 31)
        self.wr(HELD + 16 * t, struct.pack('>4I', *held))
        base = NODES + (t << 10)
        self.wr(base, bytes(1024))
        for i, n in enumerate(notes):
            prv = base + 8 * notes[i - 1] if i else 0
            nxt = base + 8 * notes[i + 1] if i + 1 < len(notes) else 0
            self.wr(base + 8 * n, struct.pack('>II', prv, nxt))
        self.wr(HEAD + 4 * t, struct.pack('>I', base + 8 * notes[0] if notes else 0))

    def scale(self, where, sc=0, root=0, per_track=None):
        """KEYBOARD SETUP: where None (no pattern), 'pattern' (sc, root) or 'track' ({t: (sc, root)})"""
        self.wr(PLAYING, struct.pack('>I', PAT if where else 0))
        if not where:
            return
        self.wr(PAT, bytes(0x1dd40 + 0x40))
        if where == 'pattern':
            self.wr(PAT + 0x1dd40 + 0x1d, struct.pack('>b', sc))
            self.wr(PAT + 0x1dd40 + 0x1f, struct.pack('>b', root))
        else:
            self.wr(PAT + 0x1dd40 + 0x21, bytes([4]))
            self.wr(PAT + 0x1dd40 + 0x1d, struct.pack('>b', 0))       # the pattern's, not used: Ionian on C
            for t, (tsc, troot) in per_track.items():
                self.wr(PAT + 0x6a5 * t + 0x492, struct.pack('>b', tsc))
                self.wr(PAT + 0x6a5 * t + 0x494, struct.pack('>b', troot))


def scale_classes(img, sc):
    if sc < 0 or sc > 34:
        return set(range(12))
    cl = set()
    for i in range(8):
        a = SCALE_TAB + 4 * (8 * sc + i) - LOAD
        n = struct.unpack('>i', img[a:a + 4])[0]
        if n < 0:
            break
        cl.add(n % 12)
    return cl or set(range(12))


def pool(lo, rng, classes, root):
    hi = min(128, lo + 12 * (max(0, min(9, rng)) + 1))
    return [x for x in range(lo, hi) if (x - root) % 12 in classes]


def place(v, n):
    return (v * n) >> 8


def check_stock(ba2, ba4, runs=80, seed=5):
    rnd = random.Random(seed)
    bad = []
    for run in range(runs):
        t = rnd.randrange(16)
        notes = rnd.sample(range(30, 100), rnd.randint(1, 6))
        mode = (TRUE, UP, DOWN, CYCL, SHUF, RAND, 108, -1)[run % 8]
        rng = rnd.randrange(4)
        length = rnd.randint(1, 16)
        mask = rnd.getrandbits(16) | 1
        offs = [rnd.choice([0, 0, 0, 7, -5, 12, 3]) for _ in range(16)]
        res = []
        for b in (ba2, ba4):
            b.lay_out(t, notes, mode, rng, length, mask, offs, seed=1234 + run)
            res.append(b.steps(t, 48))
        if res[0] != res[1]:
            bad.append({'mode': mode, 'notes': notes, 'range': rng, 'len': length, 'ba02': res[0][:12],
                        'ba04': res[1][:12]})
    return {'runs': runs, 'differing': bad[:4], 'n_differing': len(bad), 'ok': not bad}


def scale_case(b, rnd, run):
    """a KEYBOARD SETUP for the run -> (classes, root) of track t as the code should read them, per track"""
    kind = run % 5
    if kind == 0:
        b.scale(None)
        return lambda t: (set(range(12)), 0), 'no pattern'
    if kind == 1:
        b.scale('pattern', -1, rnd.randrange(12))
        return lambda t: (set(range(12)), 0), 'pattern Chromatic'
    if kind in (2, 3):
        sc, root = rnd.randrange(35), rnd.randrange(24)
        b.scale('pattern', sc, root)
        cl = scale_classes(b.img, sc)
        return lambda t: (cl, root % 12), 'pattern scale %d root %d' % (sc, root)
    per = {t: (rnd.randrange(-1, 35), rnd.randrange(24)) for t in range(16)}
    b.scale('track', per_track=per)
    return lambda t: (scale_classes(b.img, per[t][0]), per[t][1] % 12), 'per track'


def check_steps(b, runs=96, seed=8):
    """2: the loop step by step"""
    rnd = random.Random(seed)
    errs, changes, kinds = {}, {}, set()
    steps = on_steps = 0

    def err(*a):
        errs.setdefault(a[0], []).append(a[1:])
    for run in range(runs):
        t = rnd.randrange(16)
        notes = rnd.sample(range(12, 110), rnd.randint(1, 5))
        lo = min(notes)
        rng = rnd.randrange(4) if run % 7 else rnd.randrange(10)
        length = (run % 16) + 1
        change = (0, 100, 50, 10, 1, 99, 50, 25)[run % 8]
        mask = rnd.getrandbits(16) | 1 if run % 2 else 0xffff
        offs = [rnd.choice([0, 7, -5, 12]) for _ in range(16)]
        b.lay_out(t, notes, 7 + change, rng, length, mask, offs, seed=99)
        cls_of, kind = scale_case(b, rnd, run)
        kinds.add(kind)
        cl, root = cls_of(t)
        p = pool(lo, rng, cl, root)
        n = len(p)
        b.reset_tm(0x1000 + run, {t: [rnd.randrange(256) for _ in range(16)]})
        ch = changes.setdefault(change, [0, 0])
        for i in range(48):
            before, _ = b.loop(t)
            got = b.steps(t, 1)[0]
            after, made = b.loop(t)
            st = b.st(t)
            step = st[0]
            steps += 1
            diff = [j for j in range(16) if before[j] != after[j]]
            if not made:
                err('unmade', run, i)
            if step >= length:
                err('step past LEN', run, i, step, length)
            if not (mask >> step) & 1:
                if got != -1 or diff:
                    err('rest', run, i, got, diff)
                continue
            on_steps += 1
            if any(j != step for j in diff):
                err('another step changed', run, i, step, diff)
            k0, k1 = place(before[step], n), place(after[step], n)
            changed = bool(diff)
            if changed:
                if k1 == k0:
                    err('a change to the same note', run, i, before[step], after[step], n)
                if after[step] != (256 * k1 + 128) // n:
                    err('changed value not the middle of its share', run, i, after[step], k1, n)
            if n > 1:
                ch[0] += changed
                ch[1] += 1
            if change == 0 and changed or change == 100 and n > 1 and not changed:
                err('LOOP CHANGE', run, i, change, changed)
            want = p[k1] if n else lo
            if got != want:
                err('note', run, i, got, want, kind, lo, rng)
            if not lo <= got < min(128, lo + 12 * (rng + 1)) or n and (got - root) % 12 not in cl:
                err('outside the pool', run, i, got, kind)
            o = (got - lo) // 12
            if st[7] != o or st[3] != got - 12 * o:
                err('state', run, i, st[3], st[7], got, lo)
    rates = {}
    ok_rates = True
    for c, (k, m) in sorted(changes.items()):
        rates[str(c)] = '%d of %d' % (k, m)
        if 0 < c < 100 and m:
            mean, sd = m * c / 100, (m * c / 100 * (1 - c / 100)) ** 0.5
            ok_rates &= abs(k - mean) <= 4 * sd + 1
    return {'runs': runs, 'steps': steps, 'on_steps': on_steps, 'scales': sorted(kinds), 'changes': rates,
            'rates within 4 sd': ok_rates, 'errors': {k: v[:4] for k, v in errs.items()},
            'n_errors': sum(len(v) for v in errs.values()), 'ok': not errs and ok_rates}


def check_seed(b):
    """3: made 0 -> seeded at the first step"""
    vals = []
    for s in (11, 12, 13, 14):
        b.lay_out(5, [60], 7, 1, 16, seed=3)
        b.scale(None)
        b.reset_tm(0x7000 + s)
        b.steps(5, 1)
        v, made = b.loop(5)
        vals.append((v, made))
    flat = [x for v, _ in vals for x in v]
    ok = all(m == 1 for _, m in vals) and len({tuple(v) for v, _ in vals}) == 4 and len(set(flat)) > 32
    return {'made': [m for _, m in vals], 'distinct loops': len({tuple(v) for v, _ in vals}),
            'distinct values': len(set(flat)), 'ok': ok}


def check_lock_and_spread(b):
    """4: LOOP CHANGE 0 repeats every LEN steps; 100 with LEN 1: always another note, spread evenly"""
    errs, res = [], {}
    b.scale('pattern', 0, 2)                            # Ionian on D
    for length in (1, 3, 5, 8, 11, 16):
        b.lay_out(3, [50], 7, 1, length, seed=7)
        b.reset_tm(77 + length)
        seq = b.steps(3, 4 * length + 16)
        if any(seq[i] != seq[i + length] for i in range(len(seq) - length)):
            errs.append(('change 0', length, seq[:2 * length]))
        res['LEN %d' % length] = seq[:length]
    b.lay_out(3, [50], 107, 0, 1, seed=7)
    b.reset_tm(4321)
    seq = b.steps(3, 3001)
    p = pool(50, 0, scale_classes(b.img, 0), 2)
    same = sum(1 for i in range(1, len(seq)) if seq[i] == seq[i - 1])
    counts = {x: seq.count(x) for x in p}
    exp = len(seq) / len(p)
    chi2 = sum((c - exp) ** 2 / exp for c in counts.values())
    res['100, LEN 1, D Ionian from D'] = {'pool': p, 'counts': counts, 'repeats': same, 'chi2_6dof': round(chi2, 2),
                                         'outside': sorted(set(seq) - set(p))}
    ok = not errs and same == 0 and chi2 < 22.5 and not res['100, LEN 1, D Ionian from D']['outside']   # 0.999: 22.46
    return {'res': res, 'errors': errs[:4], 'ok': ok}


def check_held(b):
    """5: other notes held don't matter; a new lowest note keeps the loop and moves the melody"""
    errs = []
    b.scale('pattern', 7, 9)                            # row 7 on A
    out = []
    for notes in ([50, 57, 62, 66], [50], [62, 50, 71]):
        b.lay_out(6, notes, 7 + 40, 2, 12, 0xffff, [0] * 16, seed=3)
        b.reset_tm(4242)
        out.append(b.steps(6, 36))
    if not out[0] == out[1] == out[2]:
        errs.append(('other notes', out[0][:8], out[1][:8], out[2][:8]))
    b.lay_out(6, [50], 7, 1, 8, 0xffff, [0] * 16, seed=3)
    b.reset_tm(99, {6: [16 * i + 5 for i in range(16)]})
    first = b.steps(6, 8)
    v0, _ = b.loop(6)
    b.hold(6, [55])
    moved = b.steps(6, 8)
    v1, _ = b.loop(6)
    cl = scale_classes(b.img, 7)
    want = [pool(55, 1, cl, 9)[place(v0[i], len(pool(55, 1, cl, 9)))] for i in range(8)]
    if v0 != v1 or moved != want:
        errs.append(('new lowest note', first, moved, want))
    return {'from 50': first, 'from 55': moved, 'errors': errs, 'ok': not errs}


def check_idle(b):
    """6: no note held"""
    b.lay_out(9, [], 7 + 30, 2, 12, seed=3)
    b.reset_tm(4242, {9: list(range(16))})
    before = b.loop(9)
    got = b.steps(9, 5)
    ok = got == [-2] * 5 and b.loop(9) == before
    return {'got': got, 'loop kept': b.loop(9) == before, 'ok': ok}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--json')
    ap.add_argument('--name', default='BA04', help='a later build with arp-modes 0.3 (BA05)')
    a = ap.parse_args()
    ba2, ba4 = A.Arp('BA02'), Tm(a.name)
    rep = {'stock_modes': check_stock(ba2, ba4)}
    print('1 TRUE UP DOWN CYCL SHUF RAND 108 -1, BA02 = %s:' % a.name, rep['stock_modes'])
    rep['steps'] = check_steps(ba4)
    print('2 TM step by step:', rep['steps'])
    rep['seed'] = check_seed(ba4)
    print('3 a new loop:', rep['seed'])
    rep['lock_spread'] = check_lock_and_spread(ba4)
    print('4 LOOP CHANGE 0 and 100:', rep['lock_spread'])
    rep['held'] = check_held(ba4)
    print('5 the lowest note held:', rep['held'])
    rep['idle'] = check_idle(ba4)
    print('6 no note held:', rep['idle'])
    rep['ok'] = all(v['ok'] for v in rep.values())
    print('ALL OK' if rep['ok'] else 'FAILED')
    if a.json:
        json.dump(rep, open(a.json, 'w'), indent=1)


if __name__ == '__main__':
    main()
