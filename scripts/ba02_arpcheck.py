#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""BA02: arp-modes 0.1, the arpeggiator's step FUN_4002a0bc run as a function on DJ01's and BA02's main OS images
(Unicorn, digikit's ColdFire model).

    cd vendor/digikit && uv run python ../../scripts/ba02_arpcheck.py [--json ../../out/BA02/arpcheck.json]

The step is called as the voice interrupt calls it, (track, the arp's id), with the track's arp laid out as the note
set's reset leaves it (notes/T2.2-arp-midi-v2.md 1.2): the held notes (128 bits at 0x40599728 + 16 t), the press-order
list (0x40599828 + (128 t + note) 8, its head at 0x4059d868 + 4 t), the state (0x405994a8 + 40 t: +0 step, +4 word,
+8 CYCL's direction, +0x0c last, +0x10 bits left, +0x14 TRUE's place, +0x18 id, +0x1c octave, +0x20 the sound) and the
sound's arp settings (+0x15f MODE, +0x161 RANGE, +0x163 LEN - 1, +0x164 the steps on, +0x166 the offsets).

Checks:
  1. TRUE, UP, DOWN, CYCL, and 7 and -1 (CYCL on both): BA02's notes DJ01's, note for note, over random held sets,
     RANGE, LEN, steps off and offsets;
  2. SHUF: each cycle of n notes x (RANGE + 1) octaves plays every one once; the cycles' orders differ; a cycle never
     starts on the note the one before ended on; a new arp (id) starts a new order; more than 64: random;
  3. RAND: every note in the held set and the octaves of RANGE, each about as often (a loose chi-square), nothing
     else; not the order of any other mode;
  4. steps that are off rest and use up no note; the offsets added (both modes, as the stock ones);
  5. DJ01 with 5 and 6: CYCL, note for note (stock: names only).
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
import dj01_lfocheck as D  # noqa: E402
from emu.harness import call  # noqa: E402
from unicorn import m68k_const as MC  # noqa: E402

STEP, STEP_END = 0x4002a0bc, 0x4002a3a6
HELD, NODES, HEAD, STATE = 0x40599728, 0x40599828, 0x4059d868, 0x405994a8
SOUND = 0x10380000
TRUE, UP, DOWN, CYCL, SHUF, RAND = 1, 2, 3, 4, 5, 6


class Arp(D.Box):
    def __init__(self, name):
        super().__init__(name)
        for a in (0x40500000, SOUND):
            self.m.ensure(a)
        # the step's ff1 (no Unicorn instruction): digikit's per-address emulation, on the step's range only
        self.m.install_isa_patches_scoped(self.img[STEP - D.LOAD:STEP_END - D.LOAD], STEP)

    def lay_out(self, t, notes, mode, rng, length, mask=0xffff, offsets=None, arp_id=7, seed=None):
        """the track's held notes (in press order), its state as the reset leaves it, the sound's settings"""
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
        snd = SOUND + 0x400 * t
        self.wr(snd, bytes(0x200))
        self.wr(snd + 0x15f, struct.pack('>b', mode))
        self.wr(snd + 0x161, struct.pack('>b', rng))
        self.wr(snd + 0x163, struct.pack('>b', length - 1))
        self.wr(snd + 0x164, struct.pack('>H', mask))
        self.wr(snd + 0x166, struct.pack('>16b', *(offsets or [0] * 16)))
        st = STATE + 40 * t
        # the reset (FUN_40029cd4 on a new arp): step -1 (the first call plays step 0), word 0, CYCL up, no last note,
        # every bit left, TRUE from the head, octave 0
        self.wr(st, struct.pack('>IIIiIIIII', 0xffffffff, 0, 1, -1, 0xffffffff, 0, arp_id, 0, snd))
        if seed is not None and 'am_rng' in self.mp:
            self.wr(self.mp['am_rng'], struct.pack('>I', seed))
            for tt in range(16):
                self.wr(self.mp['am_tr'] + 72 * tt, bytes(72))

    def steps(self, t, n, arp_id=7):
        out = []
        for _ in range(n):
            call(self.m, STEP, [t, arp_id], stack_top=0x10000000 + 0xff000)
            v = self.uc.reg_read(MC.UC_M68K_REG_D0) & 0xffffffff
            out.append(v - (1 << 32) if v & 0x80000000 else v)
        return out


def slots(notes, rng):
    s = sorted(notes)
    return [n + 12 * o for o in range(rng + 1) for n in s]


def check_stock(dj, ba, runs=60, seed=3):
    rnd = random.Random(seed)
    bad, n = [], 0
    for run in range(runs):
        t = rnd.randrange(16)
        notes = rnd.sample(range(30, 100), rnd.randint(1, 6))
        mode = (TRUE, UP, DOWN, CYCL, 7, -1)[run % 6]
        rng = rnd.randrange(4)
        length = rnd.randint(1, 16)
        mask = rnd.getrandbits(16) | 1
        offs = [rnd.choice([0, 0, 0, 7, -5, 12, 3]) for _ in range(16)]
        res = []
        for b in (dj, ba):
            b.lay_out(t, notes, mode, rng, length, mask, offs, seed=99)
            res.append(b.steps(t, 48))
        n += 1
        if res[0] != res[1]:
            bad.append({'mode': mode, 'notes': notes, 'range': rng, 'len': length, 'dj01': res[0][:12],
                        'ba02': res[1][:12]})
    return {'runs': n, 'differing': bad[:4], 'n_differing': len(bad), 'ok': not bad}


def check_shuf(ba):
    res, errs = {}, []
    for notes, rng in (([64, 60, 67, 71], 1), ([50, 53, 57], 0), ([40, 47, 52, 55, 59], 2), ([62], 0), ([62], 3)):
        ba.lay_out(1, notes, SHUF, rng, 16, seed=12345)
        seq = ba.steps(1, 8 * len(slots(notes, rng)))
        m = len(slots(notes, rng))
        cycles = [seq[i:i + m] for i in range(0, len(seq), m)]
        want = sorted(slots(notes, rng))
        perm_ok = all(sorted(c) == want for c in cycles)
        distinct = len({tuple(c) for c in cycles})
        starts = all(cycles[i][0] != cycles[i - 1][-1] for i in range(1, len(cycles))) if m > 1 else True
        res['%s RANGE %d' % (notes, rng)] = {'m': m, 'cycles': len(cycles), 'every_cycle_all_once': perm_ok,
                                             'distinct_orders': distinct, 'no_repeat_at_the_seam': starts,
                                             'first': cycles[0]}
        if not perm_ok or not starts or (m > 2 and distinct < 3):
            errs.append('SHUF %s RANGE %d' % (notes, rng))
    # a new arp: a new order from the start
    ba.lay_out(2, [60, 64, 67, 72], SHUF, 0, 16, arp_id=7, seed=777)
    a = ba.steps(2, 2, arp_id=7)
    ba.lay_out(2, [60, 64, 67, 72], SHUF, 0, 16, arp_id=8, seed=None)
    b = ba.steps(2, 4, arp_id=8)
    res['new arp id: a full cycle from the start'] = sorted(b) == [60, 64, 67, 72]
    if not res['new arp id: a full cycle from the start']:
        errs.append('new id')
    # more than 64 notes x octaves: random
    notes = list(range(36, 58))                          # 22 notes x 4 octaves = 88
    ba.lay_out(3, notes, SHUF, 3, 16, seed=31)
    seq = ba.steps(3, 88 * 3)
    big_ok = set(seq) <= set(slots(notes, 3)) and len(set(seq[:88])) < 88
    res['88 slots: random within the set'] = big_ok
    if not big_ok:
        errs.append('88 slots')
    res['ok'] = not errs
    res['errors'] = errs
    return res


def check_rand(ba):
    notes, rng = [64, 60, 67, 71], 1
    ba.lay_out(4, notes, RAND, rng, 16, seed=4242)
    seq = ba.steps(4, 4000)
    sl = slots(notes, rng)
    counts = {s: seq.count(s) for s in sl}
    exp = len(seq) / len(sl)
    chi2 = sum((c - exp) ** 2 / exp for c in counts.values())
    others = []
    for mode in (TRUE, UP, DOWN, CYCL, SHUF):
        ba.lay_out(4, notes, mode, rng, 16, seed=4242)
        others.append(ba.steps(4, 32))
    res = {'steps': len(seq), 'counts': counts, 'chi2_7dof': round(chi2, 2), 'outside': sorted(set(seq) - set(sl)),
           'repeats': sum(1 for i in range(1, len(seq)) if seq[i] == seq[i - 1]),
           'like_another_mode': any(seq[:32] == o for o in others)}
    res['ok'] = not res['outside'] and chi2 < 24.3 and not res['like_another_mode']   # chi2(7) 0.999: 24.3
    return res


def check_rests_offsets(ba):
    """steps 3 and 7 off, offsets +0 +7 -5 +12 ...: the notes are the unmuted run's, the offsets added"""
    res, errs = {}, []
    offs = [0, 7, -5, 12, 0, 3, 0, -12, 0, 0, 0, 0, 0, 0, 0, 0]
    mask = 0xffff & ~(1 << 3) & ~(1 << 7)
    for mode in (SHUF, RAND, CYCL):
        ba.lay_out(5, [60, 64, 67, 71], mode, 0, 8, mask, offs, seed=5150)
        got = ba.steps(5, 32)
        ba.lay_out(5, [60, 64, 67, 71], mode, 0, 8, 0xffff, [0] * 16, seed=5150)
        plain = ba.steps(5, 32)
        rests = [i for i, x in enumerate(got) if x == -1]
        it = iter(plain)
        rebuilt = [x if x == -1 else next(it) + offs[i % 8] for i, x in enumerate(got)]
        ok = rests == [i for i in range(32) if i % 8 in (3, 7)] and rebuilt == got
        res[{SHUF: 'SHUF', RAND: 'RAND', CYCL: 'CYCL'}[mode]] = {'rests': rests, 'ok': ok}
        if not ok:
            errs.append(mode)
    res['ok'] = not errs
    return res


def check_stock_names(dj):
    res = {}
    for mode in (SHUF, RAND):
        dj.lay_out(6, [60, 64, 67, 71], mode, 1, 16, seed=1)
        a = dj.steps(6, 24)
        dj.lay_out(6, [60, 64, 67, 71], CYCL, 1, 16, seed=1)
        b = dj.steps(6, 24)
        res[mode] = a == b
    return {'DJ01 SHUF = CYCL': res[SHUF], 'DJ01 RAND = CYCL': res[RAND], 'ok': res[SHUF] and res[RAND]}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--json')
    a = ap.parse_args()
    dj, ba = Arp('DJ01'), Arp('BA02')
    rep = {'stock_modes': check_stock(dj, ba)}
    print('1 TRUE UP DOWN CYCL 7 -1, DJ01 = BA02:', rep['stock_modes'])
    rep['shuf'] = check_shuf(ba)
    print('2 SHUF:', json.dumps(rep['shuf'])[:900])
    rep['rand'] = check_rand(ba)
    print('3 RAND:', rep['rand'])
    rep['rests_offsets'] = check_rests_offsets(ba)
    print('4 rests and offsets:', rep['rests_offsets'])
    rep['stock_names'] = check_stock_names(dj)
    print('5 DJ01:', rep['stock_names'])
    rep['ok'] = all(v['ok'] for v in rep.values())
    print('ALL OK' if rep['ok'] else 'FAILED')
    if a.json:
        json.dump(rep, open(a.json, 'w'), indent=1)


if __name__ == '__main__':
    main()
