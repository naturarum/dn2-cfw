#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""BA03: arp-modes 0.2's TM, the Turing Machine loop, with the arpeggiator's step FUN_4002a0bc run as a function on
BA02's and BA03's main OS images (Unicorn, digikit's ColdFire model), as scripts/ba02_arpcheck.py runs it.

    cd vendor/digikit && uv run python ../../scripts/ba03_arpcheck.py [--json ../../out/BA03/arpcheck.json]

Checks:
  1. TRUE, UP, DOWN, CYCL, SHUF, RAND, 108 and -1 (CYCL): BA03's notes BA02's, note for note, with the same random
     seed, over random held sets, RANGE, LEN, steps off and offsets;
  2. TM's loop, step by step (am_tml): the register moves one place at every step, off steps included; the bit
     leaving the loop (bit LEN - 1) comes back in, never flipped at LOOP CHANGE 0, always at 100, about half the
     time at 50 (a binomial bound); LEN 1..16;
  3. TM's notes: every on step plays held note k % n + 12 x (k / n), k = (the register's low 8 bits x n x (RANGE + 1))
     >> 8, lowest first; the note at +0x0c and the octave at +0x1c of the arp state; an off step rests; offsets
     not added;
  4. LOOP CHANGE 0 locks the loop: the notes repeat every LEN steps (once the 8-bit window holds loop bits only, from
     the 8th step for LEN below 8); 100: the low LEN bits come back every 2 LEN steps, inverted after LEN;
  5. no note held: the step stops before TM (-2), the loop stays.
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

STATE = A.STATE
TRUE, UP, DOWN, CYCL, SHUF, RAND = A.TRUE, A.UP, A.DOWN, A.CYCL, A.SHUF, A.RAND


class Tm(A.Arp):
    def reset_tm(self, seed):
        self.wr(self.mp['am_rng'], struct.pack('>I', seed))
        self.wr(self.mp['am_tml'], bytes(4 * 16))
        self.wr(self.mp['am_chg'], bytes(16))

    def loop(self, t):
        reg, pos, made = struct.unpack('>HBB', self.rd(self.mp['am_tml'] + 4 * t, 4))
        return reg, pos, made

    def st(self, t):
        return struct.unpack('>IIIiIIIII', self.rd(STATE + 40 * t, 36))


def check_stock(ba2, ba3, runs=80, seed=5):
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
        for b in (ba2, ba3):
            b.lay_out(t, notes, mode, rng, length, mask, offs, seed=1234 + run)
            res.append(b.steps(t, 48))
        if res[0] != res[1]:
            bad.append({'mode': mode, 'notes': notes, 'range': rng, 'len': length, 'ba02': res[0][:12],
                        'ba03': res[1][:12]})
    return {'runs': runs, 'differing': bad[:4], 'n_differing': len(bad), 'ok': not bad}


def expect_note(notes, rng, reg):
    s = sorted(notes)
    n = len(s)
    m = n * (max(0, min(9, rng)) + 1)
    k = ((reg & 0xff) * m) >> 8
    return s[k % n] + 12 * (k // n), s[k % n], k // n


def check_loop(b, runs=60, seed=8):
    """2 and 3: the register's rule and the notes, step by step"""
    rnd = random.Random(seed)
    errs, flips, moves, steps = [], {0: [0, 0], 50: [0, 0], 100: [0, 0]}, 0, 0
    for run in range(runs):
        t = rnd.randrange(16)
        notes = rnd.sample(range(24, 100), rnd.randint(1, 7))
        rng = rnd.randrange(4)
        length = (run % 16) + 1
        change = (0, 50, 100)[run % 3]
        mask = rnd.getrandbits(16) | 1 if run % 2 else 0xffff
        offs = [rnd.choice([0, 7, -5, 12]) for _ in range(16)]
        b.lay_out(t, notes, 7 + change, rng, length, mask, offs, seed=99)
        b.reset_tm(0x1000 + run)
        prev = None
        for i in range(40):
            got = b.steps(t, 1)[0]
            reg, pos, made = b.loop(t)
            st = b.st(t)
            step = st[0]
            steps += 1
            if not made:
                errs.append(('not seeded', run, i))
                break
            if pos != step:
                errs.append(('pos', run, i, pos, step))
            if prev is not None:
                moves += 1
                want = (prev << 1) & 0xffff
                out = (prev >> (length - 1)) & 1
                if reg & ~1 & 0xffff != want:
                    errs.append(('shift', run, i, hex(prev), hex(reg)))
                flipped = (reg & 1) != out
                flips[change][0] += flipped
                flips[change][1] += 1
                if change == 0 and flipped or change == 100 and not flipped:
                    errs.append(('flip', run, i, change, hex(prev), hex(reg)))
            prev = reg
            on = (mask >> step) & 1
            if not on:
                if got != -1:
                    errs.append(('rest', run, i, got))
                continue
            full, note, oct_ = expect_note(notes, rng, reg)
            if got != full:
                errs.append(('note', run, i, got, full, hex(reg)))
            if st[3] != note or st[7] != oct_:
                errs.append(('state', run, i, st[3], note, st[7], oct_))
    n50, of50 = flips[50]
    half = of50 / 2
    ok50 = abs(n50 - half) <= 3.5 * (of50 ** 0.5) / 2
    return {'runs': runs, 'steps': steps, 'moves': moves, 'flips': {str(k): v for k, v in flips.items()},
            'change 50 near half': ok50, 'errors': errs[:6], 'n_errors': len(errs), 'ok': not errs and ok50}


def check_periods(b):
    """4: LOOP CHANGE 0 repeats every LEN steps; 100 returns every 2 LEN, inverted after LEN"""
    res, errs = {}, []
    notes = [48, 52, 55, 59, 62]
    for length in (1, 3, 5, 8, 11, 16):
        b.lay_out(3, notes, 7, 1, length, seed=7)
        b.reset_tm(77 + length)
        seq = b.steps(3, 4 * length + 16)
        tail = seq[max(length, 8):]                     # the 8-bit window holds only loop bits from the 8th step
        if any(tail[i] != tail[i + length] for i in range(len(tail) - length)):
            errs.append(('change 0', length, seq[:2 * length]))
        b.lay_out(3, notes, 107, 1, length, seed=7)
        b.reset_tm(177 + length)
        lowmask = (1 << length) - 1
        regs = []
        for _ in range(4 * length + 4):
            b.steps(3, 1)
            regs.append(b.loop(3)[0] & lowmask)
        for i in range(len(regs) - 2 * length):
            if regs[i] != regs[i + 2 * length] or regs[i] ^ regs[i + length] != lowmask:
                errs.append(('change 100', length, i, regs[i], regs[i + length], regs[i + 2 * length]))
                break
        res[length] = 'ok' if not errs else 'see errors'
    return {'lengths': res, 'errors': errs[:4], 'ok': not errs}


def check_offsets_and_idle(b):
    """3 (offsets not added) and 5 (no note held)"""
    errs = []
    notes = [60, 63, 67]
    out = []
    for offs in ([0] * 16, [12, -7, 5, 3, -12, 7, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9]):
        b.lay_out(9, notes, 7 + 30, 2, 12, 0xffff, offs, seed=3)
        b.reset_tm(4242)
        out.append(b.steps(9, 36))
    if out[0] != out[1]:
        errs.append(('offsets added', out[0][:8], out[1][:8]))
    b.lay_out(9, [], 7 + 30, 2, 12, seed=3)
    b.reset_tm(4242)
    before = b.loop(9)
    got = b.steps(9, 5)
    if got != [-2] * 5 or b.loop(9) != before:
        errs.append(('idle', got, before, b.loop(9)))
    return {'errors': errs, 'ok': not errs}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--json')
    a = ap.parse_args()
    ba2, ba3 = A.Arp('BA02'), Tm('BA03')
    rep = {'stock_modes': check_stock(ba2, ba3)}
    print('1 TRUE UP DOWN CYCL SHUF RAND 108 -1, BA02 = BA03:', rep['stock_modes'])
    rep['loop'] = check_loop(ba3)
    print('2-3 the loop and its notes:', rep['loop'])
    rep['periods'] = check_periods(ba3)
    print('4 LOOP CHANGE 0 and 100:', rep['periods'])
    rep['offsets_idle'] = check_offsets_and_idle(ba3)
    print('3, 5 offsets, no note held:', rep['offsets_idle'])
    rep['ok'] = all(v['ok'] for v in rep.values())
    print('ALL OK' if rep['ok'] else 'FAILED')
    if a.json:
        json.dump(rep, open(a.json, 'w'), indent=1)


if __name__ == '__main__':
    main()
