#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""DJ01: the LFO evaluators run as functions on DT01's and DJ01's main OS images (Unicorn, digikit's ColdFire model).

    cd vendor/digikit && uv run python ../../scripts/dj01_lfocheck.py [--frames N] [--json out/DJ01/lfocheck.json]

Each image is loaded as the boot leaves it (the main OS at 0x40000400, the core's run image copied to 0x47E00000,
.bss zeroed); no OS code runs but the evaluators and what they call. A frame = the parameter rows written
(0x800068e4 + 0x22 + 0xca r + 2k: k of voice row r, as the frame handler's smoother leaves them), then evaluator A called as the
frame handler calls it (DT01: 0x40137a06; DJ01: through dv_frame_hook, the site 0x400272d4's new target), with
MACSR = 0x20 (the handler's fractional mode); the rows, the LFO state block and the generators read back.
digikit's Unicorn computes the EMAC's fractional products at half their value (notes/T3.3); both images run under
the same arithmetic, so A/B comparisons hold, and the DV checks read the waveform (state +0x0c, before FADE and
DEP) and the loops, which do not use the EMAC.

Checks:
  1. stock waves: the same random sweep (WAVE 0..6, every MODE, SPH, SPD, MULT, FADE, DEP, DEST; random trigs; the
     base rows random; no DEST on a WAVE word, k 5, 13, 21, which can now reach the added waves) on both images: every row word, the whole state block (but +0x26/+0x27: DJ01's) and the
     stock generator equal in every frame; and B (0x401376bc) on a MIDI track the same;
  2. DV-F / DV-T: the loop's length per MODE (the waveform's period in steps, SPH 64), 16 steps per LFO cycle, the
     values the loop's; DV-T's restart at a trig (phase 0, the first value, the values kept), DV-F's position
     going on through trigs; the same on B (a MIDI track);
  3. the mutation at SPH 0, 32, 64, 96, 127: replacement and swap rates against ((64 - SPH)/64)^2 and
     ((SPH - 64)/63)^2;
  4. the scales: on each machine's tuning rows every result a note of the scale (nearest to the unrounded DV-F
     result); on any other DEST and on the MIDI machine, PMAJ..OCTA = DV-F word for word;
  5. the cost: instructions per frame of A, stock waves (DT01, DJ01) and every LFO on DV waves;
  6. (--stock-os) DT01's evaluator with WAVE 7..15: each the same as RAND (WAVE 6), word for word.
"""
import argparse
import json
import os
import random
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIGIKIT = os.path.join(ROOT, 'vendor', 'digikit')
sys.path.insert(0, DIGIKIT)
from emu.harness import Machine, call  # noqa: E402
from unicorn import UC_HOOK_CODE  # noqa: E402

LOAD = 0x40000400
ROWS, ROWK, NK = 0x800068e4, 0xca, 101   # A: row r's k at ROWS + 0x22 + 0xca r + 2k (17 header words first)
OWNER, MACHINE, CLOCK = 0x80005308, 0x80007670, 0x80005398
A_STATE, B_STATE, SNAP = 0x44640c18, 0x44640498, 0x4463fd18
EVAL_A, EVAL_B = 0x40137a06, 0x401376bc
STOCK_RNG = 0x402a1338
B_ROWS = 0x44617448                   # B: LFO l's eight words at + 0xca t + 2 + 0x10 l (k 1..24 at + 2k, as A's)
TRAMP = 0x10200000
SCR = 0x10300000
CYCLE_A, CYCLE_B = 1382400000, 21600000
LFOK = {0: 1, 1: 9, 2: 17}
WAVE_K = (5, 13, 21)                  # the WAVE words: an LFO aimed at one can reach the added waves (by design)            # LFO l's first k: SPD, MULT, FADE, DEST, WAVE, SPH, MODE, DEP
SCALES = {9: (0, 2, 4, 7, 9), 10: (0, 3, 5, 7, 10), 11: (0, 2, 3, 7, 8), 12: (0, 2, 4, 6, 8, 10),
          13: (0, 2, 3, 5, 7, 9, 10), 14: (0, 2, 4, 6, 7, 9, 11), 15: (0, 1, 3, 4, 6, 7, 9, 10)}
TUNE_K = {0: (50,), 1: (25, 31, 48), 2: (25,), 3: (25,)}


class Box:
    def __init__(self, name):
        d = os.path.join(ROOT, 'out', name)
        self.name = name
        self.img = open(os.path.join(d, name + '.main.bin'), 'rb').read()
        self.mp = {k: int(v, 16) for k, v in json.load(open(os.path.join(d, 'build', name + '-main.syx.map.json'))).items()}
        m = self.m = Machine()
        uc = self.uc = m.uc
        for a in range(LOAD & ~0xfffff, LOAD + len(self.img) + 0x100000, 0x100000):
            m.ensure(a)
        uc.mem_write(LOAD, self.img)
        rs, rl, bs, be = self.mp['__run_start'], self.mp['__run_load'], self.mp['__bss_start'], self.mp['__bss_end']
        for a in range(rs & ~0xfffff, be + 0x100000, 0x100000):
            m.ensure(a)
        uc.mem_write(rs, self.img[rl - LOAD:rl - LOAD + bs - rs])
        uc.mem_write(bs, bytes(be - bs))
        for a in (0x80000000, 0x44600000, TRAMP, SCR, 0x10000000):
            m.ensure(a)
        self.entry = self.mp.get('dv_frame_hook', EVAL_A)
        uc.mem_write(TRAMP, bytes.fromhex('a93c00000020 4ef9%08x' % self.entry))
        uc.mem_write(TRAMP + 0x20, bytes.fromhex('a93c00000020 4ef9%08x' % EVAL_B))
        self.count = None
        self.hook = None

    def rd(self, a, n):
        return bytes(self.uc.mem_read(a, n))

    def wr(self, a, b):
        self.uc.mem_write(a, b)

    def u32(self, a):
        return struct.unpack('>I', self.rd(a, 4))[0]

    def s32(self, a):
        return struct.unpack('>i', self.rd(a, 4))[0]

    def setup(self, owners=None, machines=None, rate=14400, clock=0x12345678):
        for r in range(16):
            self.wr(OWNER + 4 * r, struct.pack('>I', r if owners is None else owners[r]))
            self.wr(MACHINE + 0x99 * r, bytes([1 if machines is None else machines[r]]))
        self.wr(CLOCK, struct.pack('>I', clock))
        self.rate = rate

    def counting(self, on):
        if on and self.hook is None:
            self.count = 0

            def cb(uc, addr, size, data):
                self.count += 1
            self.hook = self.uc.hook_add(UC_HOOK_CODE, cb)
            self.uc.ctl_flush_tb()                       # code translated before the hook would not count
        elif not on and self.hook is not None:
            self.uc.hook_del(self.hook)
            self.hook = None
            self.uc.ctl_flush_tb()

    def frame(self, rows, trig=0):
        """rows: 16 lists of 101 values (k 0..100, the smoothed words) -> the rows after A"""
        self.wr(ROWS + 0x22, b''.join(struct.pack('>101H', *[v & 0xffff for v in rows[r]]) for r in range(16)))
        call(self.m, TRAMP, [ROWS, self.rate, trig, 0, SCR, SCR + 0x10, 0], stack_top=0x10000000 + 0xff000)
        raw = self.rd(ROWS + 0x22, 16 * ROWK)
        return [list(struct.unpack('>101H', raw[r * ROWK:(r + 1) * ROWK])) for r in range(16)]

    def state(self, block=A_STATE):
        return self.rd(block, 0x780)

    def rec(self, r, l, block=A_STATE):
        return block + 0x78 * r + 0x28 * l

    def wave_out(self, r, l, block=A_STATE):
        """the waveform's value this frame, before FADE and DEP (state +0x0c)"""
        return self.s32(self.rec(r, l, block) + 0x0c)

    def loop(self, t, l, b=False):
        a = self.mp['dv_loop_b' if b else 'dv_loop_a'] + 72 * (3 * t + l)
        return list(struct.unpack('>32h', self.rd(a, 64))), self.rd(a + 64, 8)

    def b_frame(self, rows_b, t, trig):
        self.wr(B_ROWS, b''.join(struct.pack('>101H', *[v & 0xffff for v in rows_b[r]]) for r in range(16)))
        call(self.m, TRAMP + 0x20, [B_ROWS, t, (1 << t) if trig else 0], stack_top=0x10000000 + 0xff000)
        raw = self.rd(B_ROWS, 16 * ROWK)
        return [list(struct.unpack('>101H', raw[r * ROWK:(r + 1) * ROWK])) for r in range(16)]


def lfo_set(row, l, spd=64, mult=4, fade=64, dest=0, wave=0, sph=0, mode=0, dep=64):
    k = LFOK[l]
    for i, v in enumerate((spd, mult, fade, dest, wave, sph, mode, dep)):
        row[k + i] = v << 8


def base_rows(rnd):
    return [[rnd.randrange(128) << 8 for _ in range(NK)] for _ in range(16)]


def quiet_rows():
    """every row's words at 64 << 8 and its three LFOs on TRI with no depth (DEP 64), DEST none"""
    rows = [[64 << 8] * NK for _ in range(16)]
    for r in range(16):
        for l in range(3):
            lfo_set(rows[r], l)
    return rows


# ---------------------------------------------------------------- 1. stock waves: DT01 = DJ01
def check_stock(dt, dj, frames, seed=1):
    rnd = random.Random(seed)
    rows = base_rows(rnd)
    for b in (dt, dj):
        b.setup()
    worst, diffs, first = 0, 0, None
    for f in range(frames):
        if f % 97 == 0:
            for r in range(16):
                for l in range(3):
                    lfo_set(rows[r], l, spd=rnd.randrange(128), mult=rnd.randrange(24), fade=rnd.randrange(128),
                            dest=rnd.choice([0, 25, 30, 40, 67, 70, 80, 90, 100, 6, 7, 14, 22, 23, 1, 4,
                                             rnd.choice([k for k in range(101) if k not in WAVE_K])]),
                            wave=rnd.randrange(7), sph=rnd.randrange(128), mode=rnd.randrange(5),
                            dep=rnd.randrange(128))
        trig = rnd.getrandbits(16) if rnd.random() < 0.15 else 0
        o1, o2 = dt.frame(rows, trig), dj.frame(rows, trig)
        s1, s2 = bytearray(dt.state()), bytearray(dj.state())
        for i in range(48):                              # +0x26/+0x27: DJ01's own (untouched on stock waves)
            for o in (0x26, 0x27):
                s1[i * 0x28 + o] = s2[i * 0x28 + o] = 0
        same = o1 == o2 and s1 == s2 and dt.rd(STOCK_RNG, 8) == dj.rd(STOCK_RNG, 8)
        if not same:
            diffs += 1
            if first is None:
                first = f
    own = [dj.rd(dj.rec(r, l) + 0x26, 2) for r in range(16) for l in range(3)]
    return {'frames': frames, 'frames_differing': diffs, 'first_difference': first,
            'dj01_bytes_26_27_written_on_stock_waves': sum(1 for x in own if x != b'\0\0'),
            'ok': diffs == 0 and all(x == b'\0\0' for x in own)}


def check_stock_b(dt, dj, frames, seed=2):
    """B adds a MIDI LFO into its row's k 1..8 (DEST 1..8): DEST 5, LFO1's WAVE, is left out, as for A"""
    rnd = random.Random(seed)
    rows = base_rows(rnd)
    diffs = 0
    for f in range(frames):
        t = f % 16
        if f % 61 == 0:
            for r in range(16):
                for l in range(3):
                    lfo_set(rows[r], l, spd=rnd.randrange(128), mult=rnd.randrange(24), fade=rnd.randrange(128),
                            dest=rnd.choice((0, 1, 2, 3, 4, 6, 7, 8, 9, 50)), wave=rnd.randrange(7),
                            sph=rnd.randrange(128),
                            mode=rnd.randrange(5), dep=rnd.randrange(128))
        trig = rnd.random() < 0.1
        o1, o2 = dt.b_frame(rows, t, trig), dj.b_frame(rows, t, trig)
        if o1 != o2 or dt.state(B_STATE) != dj.state(B_STATE) or dt.rd(STOCK_RNG, 8) != dj.rd(STOCK_RNG, 8):
            diffs += 1
    return {'calls': frames, 'calls_differing': diffs, 'ok': diffs == 0}


# ---------------------------------------------------------------- 2. DV-F / DV-T
def steps_run(box, wave, mode, sph, nsteps, spd=96, mult=4, trig_at=(), r=0, l=0, dest=70, seed=7):
    """one LFO (row r, LFO l) on a DV wave: -> [(frame, step index, position, waveform value)], one per new step"""
    box.wr(box.mp['dv_rng_a'], struct.pack('>I', seed))
    rows = quiet_rows()
    lfo_set(rows[r], l, spd=spd, mult=mult, fade=64, dest=dest, wave=wave, sph=sph, mode=mode, dep=127)
    out, last, f = [], None, 0
    while len(out) < nsteps and f < 200000:
        box.frame(rows, (1 << r) if f in trig_at else 0)
        rec = box.rec(r, l)
        ph, ps = box.u32(rec), box.rd(rec + 0x26, 2)
        key = (ps[0], ps[1])
        if key != last or f in trig_at:
            out.append((f, ps[1] & 0x7f, ps[0], box.wave_out(r, l), ph))
            last = key
        f += 1
    return out


def period(vals):
    n = len(vals)
    for p in (1, 2, 4, 8, 16, 32, 64):
        if all(vals[i] == vals[i - p] for i in range(p, n)):
            return p
    return None


def check_dv(dj):
    dj.setup()
    res = {}
    lengths = {}
    for mode, want in enumerate((2, 4, 8, 16, 32)):
        for wave in (7, 8, 9):
            st = steps_run(dj, wave, mode, 64, 3 * want + 4, r=mode, l=wave - 7)
            vals = [x[3] for x in st[1:]]
            loopv, _ = dj.loop(mode, wave - 7)
            in_loop = all(v == (loopv[p] << 16) for (_, _, p, v, _) in st[1:])
            lengths['MODE %d WAVE %d' % (mode, wave)] = {'period': period(vals), 'want': want, 'values_from_loop': in_loop}
    res['lengths'] = lengths
    # 16 steps per cycle: the frames between steps against the cycle at this rate
    st = steps_run(dj, 7, 4, 64, 40, spd=96, mult=4, r=6)
    gaps = [b[0] - a[0] for a, b in zip(st[1:], st[2:])]
    res['steps'] = {'step_index_sequence': [x[1] for x in st[1:20]], 'frames_between_steps': sorted(set(gaps)),
                    'mean_frames_per_step': sum(gaps) / len(gaps)}
    # the measured cycle in frames (the phase wrap) / 16 = frames per step
    rows = quiet_rows()
    lfo_set(rows[7], 0, spd=96, mult=4, wave=0, dest=70)
    ph = []
    for f in range(1700):
        dj.frame(rows)
        ph.append(dj.u32(dj.rec(7, 0)))
    wraps = [i for i in range(1, len(ph)) if ph[i] < ph[i - 1]]
    cyc = (wraps[-1] - wraps[0]) / (len(wraps) - 1) if len(wraps) > 1 else None
    res['steps']['cycle_frames_tri'] = cyc
    res['steps']['ok'] = (cyc is not None and abs(res['steps']['mean_frames_per_step'] * 16 - cyc) < 0.05 * cyc
                          and res['steps']['step_index_sequence'][:16] == list(range(res['steps']['step_index_sequence'][0], 16)) +
                          list(range(0, res['steps']['step_index_sequence'][0])))
    # DV-T: a trig restarts at the first value, the values kept
    st = steps_run(dj, 8, 2, 64, 30, r=8, trig_at=(500, 1100, 1137))
    loopv, _ = dj.loop(8, 0)
    at_trig = [x for x in st if x[0] in (500, 1100, 1137)]
    after = [st[i + 1] for i, x in enumerate(st[:-1]) if x[0] in (500, 1100, 1137)]
    res['dvt'] = {'at_trig': [{'frame': x[0], 'pos': x[2], 'phase': x[4], 'value_is_loop0': x[3] == loopv[0] << 16}
                              for x in at_trig],
                  'next_pos_after_trig': [x[2] for x in after],
                  'loop_unchanged': loopv == dj.loop(8, 0)[0]}
    nxt = []
    for x in at_trig:                                   # the first step after each trig that is not itself a trig
        later = [y for y in st if y[0] > x[0] and y[0] not in (500, 1100, 1137)]
        if later and not [z for z in (500, 1100, 1137) if x[0] < z <= later[0][0]]:
            nxt.append(later[0][2])
    res['dvt']['next_pos_after_trig'] = nxt
    res['dvt']['ok'] = (len(at_trig) == 3 and all(x[2] == 0 and x[4] == 0 and x[3] == loopv[0] << 16 for x in at_trig)
                        and nxt and all(p == 1 for p in nxt) and res['dvt']['loop_unchanged'])
    # DV-F ignores trigs (the position goes on)
    st = steps_run(dj, 7, 2, 64, 30, r=9, trig_at=(500, 1100))
    res['dvf_trig'] = {'positions_around_trigs': [x[2] for x in st if 400 < x[0] < 1300]}
    seq = [p for i, p in enumerate(res['dvf_trig']['positions_around_trigs'])
           if i == 0 or p != res['dvf_trig']['positions_around_trigs'][i - 1]]
    res['dvf_trig']['ok'] = len(seq) > 10 and all((b - a) % 8 == 1 for a, b in zip(seq, seq[1:]))
    res['ok'] = (all(v['period'] == v['want'] and v['values_from_loop'] for v in lengths.values())
                 and res['steps']['ok'] and res['dvt']['ok'] and res['dvf_trig']['ok'])
    return res


def check_dv_b(dj):
    """B, a MIDI track's LFO3 on DV-F / DV-T, SPH 64: the loop's length per MODE, the values dv_loop_b's, DV-T's
    restart; B's rate words (param_1 + 4 t + 0xd20 / 0xd60, the MIDI task's) set to a constant"""
    out = {}
    for t in range(16):
        dj.wr(B_ROWS + 4 * t + 0xd20, struct.pack('>I', 0x00008000))
        dj.wr(B_ROWS + 4 * t + 0xd60, struct.pack('>I', 0x00008000))
    for mode, want in enumerate((2, 4, 8, 16, 32)):
        for wave in (7, 8):
            t = 2 * mode + (wave - 7)
            rows = quiet_rows()
            lfo_set(rows[t], 2, spd=127, mult=4, fade=64, dest=0, wave=wave, sph=64, mode=mode, dep=127)
            seq, last, n = [], None, 0
            trig_at = (300,) if wave == 8 else ()
            while len(seq) < 3 * want + 12 and n < 40000:
                dj.b_frame(rows, t, n in trig_at)
                ps = dj.rd(dj.mp['dv_pos_b'] + 2 * (3 * t + 2), 2)
                if ps != last:
                    seq.append((n, ps[0], dj.wave_out(t, 2, B_STATE), dj.u32(dj.rec(t, 2, B_STATE))))
                    last = ps
                n += 1
            loopv, _ = dj.loop(t, 2, b=True)
            vals = [x[2] for x in seq[1:] if not trig_at or x[0] >= trig_at[0]]   # DV-T: from its restart on
            rec = {'steps': len(seq), 'calls': n, 'period': period(vals), 'want': want,
                   'values_from_loop': all(v == loopv[p] << 16 for (_, p, v, _) in seq[1:])}
            if wave == 8:
                at = [x for x in seq if x[0] == 300]
                rec['trig'] = {'pos': at[0][1] if at else None, 'phase': at[0][3] if at else None}
            out['MODE %d WAVE %d' % (mode, wave)] = rec
    out['ok'] = all(v['period'] == v['want'] and v['values_from_loop'] and v['steps'] > 3 and
                    ('trig' not in v or (v['trig']['pos'] == 0 and v['trig']['phase'] == 0)) for v in out.values())
    return out


# ---------------------------------------------------------------- 3. mutation
def check_mutation(dj, nsteps=1500):
    dj.setup()
    out = {}
    for i, sph in enumerate((0, 32, 64, 96, 127)):
        r, l, mode = 10 + i % 5, 0, 3                    # 16 values
        length = 16
        dj.wr(dj.mp['dv_rng_a'], struct.pack('>I', 1000 + sph))
        rows = quiet_rows()
        lfo_set(rows[r], l, spd=127, mult=6, fade=64, dest=70, wave=7, sph=sph, mode=mode, dep=127)
        last, prev_loop, n, rep, swp, other, f = None, None, 0, 0, 0, 0, 0
        while n < nsteps:
            before = dj.loop(r, l)[0]
            dj.frame(rows)
            ps = dj.rd(dj.rec(r, l) + 0x26, 2)
            after = dj.loop(r, l)[0]
            if last is not None and ps != last:
                n += 1
                d = [j for j in range(32) if before[j] != after[j]]
                p = ps[0]
                if not d:
                    pass
                elif d == [p]:
                    rep += 1
                elif len(d) == 2 and p in d and before[d[0]] == after[d[1]] and before[d[1]] == after[d[0]]:
                    swp += 1
                else:
                    other += 1
            last = ps
            f += 1
        pr = ((64 - sph) / 64) ** 2 if sph < 64 else 0.0
        ps_ = ((sph - 64) / 63) ** 2 if sph > 64 else 0.0
        out['SPH %d' % sph] = {'steps': n, 'replaced': rep / n, 'want_replaced': pr, 'swapped_visibly': swp / n,
                               'want_swapped_visibly': ps_ * (length - 1) / length, 'other_changes': other}
    ok = True
    for k, v in out.items():
        tol_r = 4 * (v['want_replaced'] * (1 - v['want_replaced']) / v['steps']) ** 0.5 + 0.005
        tol_s = 4 * (v['want_swapped_visibly'] * (1 - v['want_swapped_visibly']) / v['steps']) ** 0.5 + 0.005
        v['ok'] = (abs(v['replaced'] - v['want_replaced']) <= tol_r and abs(v['swapped_visibly'] - v['want_swapped_visibly'])
                   <= tol_s and v['other_changes'] == 0)
        ok = ok and v['ok']
    out['ok'] = ok
    return out


# ---------------------------------------------------------------- 4. scales
def nearest(rel, classes):
    best = None
    for oc in range(-7, 8):
        for c in classes:
            q = c + 12 * oc
            y = 0x4000 + 256 * q
            if 0 <= y <= 0x7f00:
                d = abs(256 * q - rel)
                if best is None or d < best[0] or (d == best[0] and q < best[1]):
                    best = (d, q)
    return 0x4000 + 256 * best[1]


def check_scales(dj, frames=1200):
    out = {}
    # the classes in dv_scale against the scales here
    sc = struct.unpack('>7H', dj.rd(dj.mp['dv_scale'], 14))
    out['dv_scale'] = ['%03x' % x for x in sc]
    out['dv_scale_ok'] = all(sc[w - 9] == sum(1 << c for c in cl) for w, cl in SCALES.items())
    bad, n_on, n_off, n_q = [], 0, 0, 0
    for mach in (0, 1, 2, 3, 4):
        dests = list(TUNE_K.get(mach, ())) + [26, 70]
        for dest in dests:
            for wave in range(9, 16):
                seed = 77 + 13 * wave + dest
                res = {}
                for w in (7, wave):
                    dj.setup(machines=[mach] * 16)
                    dj.wr(dj.mp['dv_rng_a'], struct.pack('>I', seed))
                    for t in range(16):                   # fresh loops for each run
                        for l in range(3):
                            dj.wr(dj.mp['dv_loop_a'] + 72 * (3 * t + l), bytes(72))
                    for a in range(48):
                        dj.wr(A_STATE + 0x28 * a, bytes(0x28))
                    rows = quiet_rows()
                    for r in range(16):
                        rows[r][dest] = (4 + 8 * r) << 8     # the base (the knob) differs per row
                        lfo_set(rows[r], 1, spd=110, mult=5, fade=64, dest=dest, wave=w, sph=64, mode=4,
                                dep=20 + 7 * r)
                    seq = []
                    for f in range(frames // 10):
                        o = dj.frame(rows)
                        seq.append([o[r][dest] for r in range(16)])
                    res[w] = seq
                tune = dest in TUNE_K.get(mach, ())
                for fr7, frq in zip(res[7], res[wave]):
                    for x, y in zip(fr7, frq):
                        if tune:
                            n_on += 1
                            want = nearest(x - 0x4000, SCALES[wave])
                            if y != want:
                                bad.append((mach, dest, wave, x, y, want))
                            if y != x:
                                n_q += 1
                        else:
                            n_off += 1
                            if y != x:
                                bad.append((mach, dest, wave, x, y, 'unrounded'))
    out.update({'values_on_tuning_rows': n_on, 'of_them_moved_by_rounding': n_q, 'values_on_other_dest': n_off,
                'bad': bad[:20], 'n_bad': len(bad), 'ok': not bad and out['dv_scale_ok'] and n_q > 0})
    return out


# ---------------------------------------------------------------- 6. a stock-LFO OS (DT01) with WAVE 7..15
def check_stock_os(dt, frames=600):
    """DT01's evaluator clamps WAVE to 6: every added wave plays exactly as RAND (WAVE 6), the same SPH (its slew):
    every other row word and the whole state block equal, every frame (trigs, every MODE, SPH 0..120)"""
    out = {}
    for wave in range(7, 16):
        res = []
        for w in (6, wave):
            b = Box('DT01')
            b.setup()
            rows = quiet_rows()
            for r in range(16):
                lfo_set(rows[r], 0, spd=100, mult=5, dest=70, wave=w, sph=8 * r, mode=r % 5, dep=90)
            seq = []
            for f in range(frames):
                o = b.frame(rows, 1 if f % 50 == 0 else 0)
                for r in range(16):
                    o[r][5] = 0                          # the WAVE word itself (6 or the added wave)
                seq.append((o, b.state()))
            res.append(seq)
        out['WAVE %d = RAND' % wave] = res[0] == res[1]
    out['ok'] = all(out.values())
    return out


# ---------------------------------------------------------------- 5. the cost
def cost(box, waves, frames=160, seed=5, tune=False, sph=None):
    """instructions of one call of A (the trampoline's two included), 16 rows x 3 LFOs; every LFO at the same rate
    from phase 0, so all 48 step in the same frames (every 6th or 7th here): 'max' is that frame"""
    rnd = random.Random(seed)
    box.setup(machines=[1] * 16)
    rows = base_rows(rnd)
    for r in range(16):
        for l in range(3):
            lfo_set(rows[r], l, spd=127, mult=6, fade=64, dest=25 if tune else 70 + l, wave=waves[(r * 3 + l) % len(waves)],
                    sph=rnd.choice((0, 32, 64, 96, 127)) if sph is None else sph, mode=rnd.randrange(5), dep=64 + 10)
    box.frame(rows)
    box.counting(True)
    counts = []
    for f in range(frames):
        box.count = 0
        box.frame(rows, 0)
        counts.append(box.count)
    box.counting(False)
    return {'mean': round(sum(counts) / len(counts), 1), 'max': max(counts), 'min': min(counts)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--frames', type=int, default=3000)
    ap.add_argument('--json', default=os.path.join(ROOT, 'out', 'DJ01', 'lfocheck.json'))
    ap.add_argument('--stock-os', action='store_true', help='only check 6, into out/DJ01/stockos.json')
    a = ap.parse_args()
    if a.stock_os:
        r = check_stock_os(Box('DT01'))
        json.dump(r, open(os.path.join(ROOT, 'out', 'DJ01', 'stockos.json'), 'w'), indent=1)
        print('6. a stock-LFO OS: %s' % r)
        return
    dt, dj = Box('DT01'), Box('DJ01')
    res = {}
    res['stock_a'] = check_stock(dt, dj, a.frames)
    print('1. stock waves, A: %s' % res['stock_a'], flush=True)
    res['stock_b'] = check_stock_b(Box('DT01'), Box('DJ01'), a.frames)
    print('1. stock waves, B: %s' % res['stock_b'], flush=True)
    dj = Box('DJ01')
    res['dv'] = check_dv(dj)
    print('2. DV: %s' % json.dumps(res['dv'])[:1500], flush=True)
    res['dv_b'] = check_dv_b(Box('DJ01'))
    print('2. DV on B (MIDI tracks): %s' % json.dumps(res['dv_b']), flush=True)
    res['mutation'] = check_mutation(Box('DJ01'))
    print('3. mutation: %s' % json.dumps(res['mutation']), flush=True)
    res['scales'] = check_scales(Box('DJ01'))
    print('4. scales: %s' % json.dumps(res['scales'])[:1500], flush=True)
    c = {}
    c['DT01 stock waves'] = cost(Box('DT01'), [0, 1, 2, 3, 4, 5, 6])
    c['DJ01 stock waves'] = cost(Box('DJ01'), [0, 1, 2, 3, 4, 5, 6])
    c['DJ01 every LFO DV-F/DV-T'] = cost(Box('DJ01'), [7, 8])
    c['DJ01 every LFO a scale wave on TUN1'] = cost(Box('DJ01'), [9, 10, 11, 12, 13, 14, 15], tune=True)
    c['DJ01 every LFO a DV wave, mixed'] = cost(Box('DJ01'), list(range(7, 16)), tune=True)
    c['DJ01 every LFO DV-F, SPH 0 (a new value at every step)'] = cost(Box('DJ01'), [7], sph=0)
    c['DJ01 every LFO DV-F, SPH 127 (a swap at every step)'] = cost(Box('DJ01'), [7], sph=127)
    c['DJ01 every LFO PMAJ on TUN1, SPH 127'] = cost(Box('DJ01'), [9], tune=True, sph=127)
    res['cost'] = c
    print('5. cost (instructions per frame of A, 16 rows x 3 LFOs): %s' % json.dumps(c), flush=True)
    res['ok'] = all(res[k]['ok'] for k in ('stock_a', 'stock_b', 'dv', 'dv_b', 'mutation', 'scales'))
    json.dump(res, open(a.json, 'w'), indent=1)
    print('ALL OK' if res['ok'] else 'SOME CHECK FAILED')


if __name__ == '__main__':
    main()
