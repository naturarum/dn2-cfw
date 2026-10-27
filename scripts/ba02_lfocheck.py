#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""BA02: lfo-dejavu 0.2's ONE/HALF fix, the LFO evaluators run as functions on DJ01's and BA02's main OS images
(Unicorn, digikit's ColdFire model), with the EMAC's fractional products done as the chip does them.

    cd vendor/digikit && uv run python ../../scripts/ba02_lfocheck.py [--json ../../out/BA02/lfocheck.json]

digikit's Unicorn returns the EMAC's fractional products at half their value (notes/T3.3; dn2_firmware_explore's
docs/for-digikit-emac-frac.md): there SPH 64 starts an LFO a quarter of the way into its cycle instead of half, and
SINE's polynomial is off. A fix tested only there can pass and still be wrong on the unit (dnfw's first LFO fix
did exactly that). Here every macl and movclr.l of the LFO code (0x401346c0-0x401346ee, the exp helper; 0x40137200-0x40137e40) is
done by a hook instead: the signed 32 x 32 product shifted left one place (Q31), its bits 63..24 added into a 48-bit
accumulator, movclr.l the accumulator's bits 39..8 (MACSR 0x20: fractional, signed, truncating, no saturation), then
the PC past the instruction.

Checks:
  0. the EMAC model: 0.5 x 0.5 = 0.25 (0x20000000); a trig at SPH 64 in TRIG mode starts the phase at half a cycle,
     at SPH 32 a quarter (both evaluators);
  1. DJ01 = BA02 in FREE, TRIG and HOLD (A: every row word, the state block but +0x26/+0x27, the stock generator, in
     every frame of a random sweep: stock waves and the Deja vu waves, whose MODE reads FREE; B the same);
  2. ONE and HALF on TRI .. RAMP, SPH 0 .. 127, speed up and down, FADE on and off, a trig after a free run: until the
     stop the waveform (+0x0c), the output (+4) and the page's phase (+0x20) those of the same LFO in TRIG mode, frame
     for frame; the stop in the frame the stock tests give for a phase starting at 0 (one cycle, half a cycle); from
     it the output held (the frame before the stop's), the page's phase 0 (as stock); the DEST row constant;
  3. a retrig mid-run and after the stop (TRIG retriggered as the reference); a mode change mid-run (ONE or HALF to
     FREE, TRIG or HOLD): from it on, the TRIG reference's waveform, and the latch cleared;
  4. SPH 0 on TRI .. RAMP in ONE/HALF: DJ01's until the stop, then held where DJ01 jumps; RAND (whose slew writes
     the output in the stop's frame too, so the stop value never shows) DJ01's throughout;
  5. a random sweep with every MODE, wave and SPH switching, retrigs: the latches always empty or 0x8000 | a cell,
     every phase below the cycle (BA02);
  6. for the record, DJ01's ONE/HALF run lengths at SPH 0/32/64/96/127 against BA02's;
  7. KEY (WAVE 16): with the playing pattern's KEYBOARD SETUP written (the pattern's scale and root, then per track,
     every track another), the voices' notes differing, on every machine's tuning rows each result the scale's note
     nearest to the voice's note + DV-F's unrounded result (the same loop, the same seed), ties down; on other DESTs
     DV-F's word for word; the scale table read from the image against four scales written here.
"""
import argparse
import json
import os
import random
import re
import struct
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scripts'))
sys.dont_write_bytecode = True
import dj01_lfocheck as D  # noqa: E402
from unicorn import UC_HOOK_CODE  # noqa: E402
from unicorn import m68k_const as MC  # noqa: E402
from emu.harness import call  # noqa: E402

A_STATE, B_STATE = D.A_STATE, D.B_STATE
CYCLE_A, CYCLE_B = D.CYCLE_A, D.CYCLE_B
LOAD = D.LOAD
EMAC_RANGES = ((0x401346c0, 0x401346ee), (0x40137200, 0x40137e40))   # the exp helper; the LFO code
REG = {'%%d%d' % i: getattr(MC, 'UC_M68K_REG_D%d' % i) for i in range(8)}
REG.update({'%%a%d' % i: getattr(MC, 'UC_M68K_REG_A%d' % i) for i in range(8)})
REG['%fp'], REG['%sp'] = MC.UC_M68K_REG_A6, MC.UC_M68K_REG_A7
PC = MC.UC_M68K_REG_PC
ONE, HALF, TRIG, FREE, HOLD = 3, 4, 1, 0, 2
# (SPD, MULT): 47.6, 41.7, 23.8 frames a cycle up; 44.1, 23.4 down (rate 14400)
SPEEDS = ((127, 7), (100, 8), (127, 8), (30, 8), (0, 8))


def s32(x):
    x &= 0xffffffff
    return x - (1 << 32) if x & 0x80000000 else x


def emac_sites(img, lo, hi):
    with tempfile.NamedTemporaryFile(suffix='.bin') as tf:
        tf.write(img[lo - LOAD:hi - LOAD])
        tf.flush()
        out = subprocess.run(['m68k-elf-objdump', '-D', '-b', 'binary', '-m', 'm68k:cfv4e', '--adjust-vma=0x%x' % lo,
                              tf.name], capture_output=True, text=True, check=True).stdout
    sites = {}
    for ln in out.splitlines():
        m = re.match(r'^ *([0-9a-f]+):\t([0-9a-f ]+)\t(\S+)\s*(.*)$', ln)
        if not m:
            continue
        addr, raw, op, args = int(m.group(1), 16), m.group(2).split(), m.group(3), m.group(4)
        size = 2 * len(raw)
        if op == 'macl':
            x, y, acc = args.split(',')
            if acc != '%acc0' or '<<' in args or '>>' in args:
                raise SystemExit('0x%08x: %s %s: not modelled' % (addr, op, args))
            sites[addr] = ('mac', size, REG[x], REG[y])
        elif op == 'movclrl':
            acc, dst = args.split(',')
            if acc != '%acc0':
                raise SystemExit('0x%08x: %s %s: not modelled' % (addr, op, args))
            sites[addr] = ('clr', size, REG[dst])
        elif op.startswith(('mac', 'msac', 'movclr')) or '%acc' in args:
            raise SystemExit('0x%08x: %s %s: an EMAC instruction the model does not do' % (addr, op, args))
    return sites


class Box(D.Box):
    """D.Box with the EMAC's fractional macl / movclr.l done in Python"""

    def __init__(self, name):
        super().__init__(name)
        self.acc = 0
        self.esites = {}
        for lo, hi in EMAC_RANGES:
            self.esites.update(emac_sites(self.img, lo, hi))
        for addr in self.esites:
            self.uc.hook_add(UC_HOOK_CODE, self._emac, begin=addr, end=addr)
        self.uc.ctl_flush_tb()
        self.setup()

    def _emac(self, uc, addr, size, data):
        e = self.esites[addr]
        if e[0] == 'mac':
            p = s32(uc.reg_read(e[2])) * s32(uc.reg_read(e[3]))
            a = self.acc + ((p << 1) >> 24)
            self.acc = ((a + (1 << 47)) & ((1 << 48) - 1)) - (1 << 47)
        else:
            uc.reg_write(e[2], (self.acc >> 8) & 0xffffffff)
            self.acc = 0
        uc.reg_write(PC, addr + e[1])

    def setup(self, *a, **k):
        super().setup(*a, **k)
        for t in range(16):                              # B's rate words (the MIDI task's, param_1 + 4 t + 0xd20 / 0xd60):
            self.wr(D.B_ROWS + 4 * t + 0xd20, struct.pack('>I', 0x00200000))   # SPD 127, MULT 7: 41.9 calls a cycle
            self.wr(D.B_ROWS + 4 * t + 0xd60, struct.pack('>I', 0x00200000))

    def reset_lfos(self):
        self.wr(A_STATE, bytes(0x780))
        self.wr(B_STATE, bytes(0x780))
        if 'lfx_lat_b' in self.mp:
            self.wr(self.mp['lfx_lat_b'], bytes(480))

    def rec_vals(self, r, l, block=A_STATE):
        a = self.rec(r, l, block)
        return {'phase': self.u32(a), 'out': self.s32(a + 4), 'wave': self.s32(a + 0x0c), 'page': self.u32(a + 0x20),
                'stopped': self.rd(a + 0x24, 1)[0], 'lat': struct.unpack('>H', self.rd(a + 0x26, 2))[0]}

    def lat_b(self, t, l):
        if 'lfx_lat_b' not in self.mp:
            return 0
        return struct.unpack('>H', self.rd(self.mp['lfx_lat_b'] + ((0x78 * t + 0x28 * l) >> 2), 2))[0]


# ---------------------------------------------------------------- 0. the EMAC model
def check_emac(box):
    code = bytes.fromhex('203c40000000' '223c40000000' 'a2000800' 'a1c0' '4e75')   # d0 = d1 = 0.5; macl; movclr
    base = D.TRAMP + 0x100
    box.wr(base, code)
    sites = {base + 12: ('mac', 4, REG['%d1'], REG['%d0']), base + 16: ('clr', 2, REG['%d0'])}
    box.esites.update(sites)
    for adr in sites:
        box.uc.hook_add(UC_HOOK_CODE, box._emac, begin=adr, end=adr)
    box.uc.ctl_flush_tb()
    call(box.m, base, [], stack_top=0x10000000 + 0xff000)
    got = box.uc.reg_read(REG['%d0']) & 0xffffffff
    res = {'half_x_half': '0x%08x' % got}
    # a trig at SPH 64 / 32 in TRIG mode: the start point
    for name, blk in (('A', A_STATE), ('B', B_STATE)):
        for sph, want in ((64, 0.5), (32, 0.25)):
            box.reset_lfos()
            rows = D.quiet_rows()
            D.lfo_set(rows[0], 0, spd=64, mult=4, sph=sph, mode=TRIG, wave=0, dest=70, dep=64)
            if blk == A_STATE:
                box.frame(rows, 1)
                ph = box.u32(box.rec(0, 0))
                cyc = CYCLE_A
            else:
                box.b_frame(rows, 0, True)
                ph = box.u32(box.rec(0, 0, B_STATE))
                cyc = CYCLE_B
            res['%s_sph%d' % (name, sph)] = ph
            res['%s_sph%d_ok' % (name, sph)] = ph == int(cyc * want)
    res['ok'] = got == 0x20000000 and all(v for k, v in res.items() if k.endswith('_ok'))
    return res


# ---------------------------------------------------------------- 1. FREE / TRIG / HOLD and the Deja vu waves: DJ01 = BA02
def sync_state(dj, ba):
    """the same Deja vu and generator state in both images (other checks leave BA02's used): the loops, read
    positions and frame count cleared, the generators seeded alike, the stock generator alike"""
    for b in (dj, ba):
        for name, n in (('dv_loop_a', 48 * 72), ('dv_loop_b', 48 * 72), ('dv_pos_b', 96), ('dv_frame', 4)):
            b.wr(b.mp[name], bytes(n))
        b.wr(b.mp['dv_rng_a'], struct.pack('>I', 0x1234567))
        b.wr(b.mp['dv_rng_b'], struct.pack('>I', 0x7654321))
        b.wr(D.STOCK_RNG, struct.pack('>II', 0x9e3779b9, 0x7f4a7c15))
        b.setup()
        b.reset_lfos()


def check_same_a(dj, ba, frames, seed=11):
    rnd = random.Random(seed)
    rows = D.base_rows(rnd)
    sync_state(dj, ba)
    diffs, first = 0, None
    for f in range(frames):
        if f % 97 == 0:
            for r in range(16):
                for l in range(3):
                    wave = rnd.randrange(16)
                    D.lfo_set(rows[r], l, spd=rnd.randrange(128), mult=rnd.randrange(24), fade=rnd.randrange(128),
                              dest=rnd.choice([0, 25, 30, 40, 67, 70, 80, 90, 100, 6, 7, 14, 22, 23, 1, 4,
                                               rnd.choice([k for k in range(101) if k not in D.WAVE_K])]),
                              wave=wave, sph=rnd.randrange(128),
                              mode=rnd.randrange(5) if wave >= 7 else rnd.randrange(3), dep=rnd.randrange(128))
        trig = rnd.getrandbits(16) if rnd.random() < 0.15 else 0
        o1, o2 = dj.frame(rows, trig), ba.frame(rows, trig)
        s1, s2 = bytearray(dj.state()), bytearray(ba.state())
        same = o1 == o2 and s1 == s2 and dj.rd(D.STOCK_RNG, 8) == ba.rd(D.STOCK_RNG, 8)
        if not same:
            diffs += 1
            if first is None:
                first = f
    return {'frames': frames, 'frames_differing': diffs, 'first_difference': first, 'ok': diffs == 0}


def check_same_b(dj, ba, frames, seed=12):
    rnd = random.Random(seed)
    rows = D.base_rows(rnd)
    sync_state(dj, ba)
    diffs = 0
    for f in range(frames):
        t = f % 16
        if f % 61 == 0:
            for r in range(16):
                for l in range(3):
                    wave = rnd.randrange(16)
                    D.lfo_set(rows[r], l, spd=rnd.randrange(128), mult=rnd.randrange(24), fade=rnd.randrange(128),
                              dest=rnd.choice((0, 1, 2, 3, 4, 6, 7, 8, 9, 50)), wave=wave, sph=rnd.randrange(128),
                              mode=rnd.randrange(5) if wave >= 7 else rnd.randrange(3), dep=rnd.randrange(128))
        trig = rnd.random() < 0.1
        o1, o2 = dj.b_frame(rows, t, trig), ba.b_frame(rows, t, trig)
        if o1 != o2 or dj.state(B_STATE) != ba.state(B_STATE) or dj.rd(D.STOCK_RNG, 8) != ba.rd(D.STOCK_RNG, 8):
            diffs += 1
    return {'calls': frames, 'calls_differing': diffs, 'ok': diffs == 0}


# ---------------------------------------------------------------- 2-4. ONE / HALF
def run(box, b, mode, wave, sph, spd, mult, fade, frames, trigs, modes=None, pre=0, seed=5):
    """one LFO (A: voice row 0, LFO 0; B: track 0, LFO 0), DEST 70 (A) or 3 (B): -> per frame the record's values and
    the DEST row word; `pre` frames free-running first (FREE, no trig); `modes` = {frame: mode}"""
    box.reset_lfos()
    box.wr(D.STOCK_RNG, struct.pack('>II', 0x12345678, 0x9abcdef0 ^ seed))
    rows = D.quiet_rows()
    dest = 70 if not b else 3
    D.lfo_set(rows[0], 0, spd=spd, mult=mult, fade=fade, dest=dest, wave=wave, sph=sph, mode=FREE, dep=127)
    blk = B_STATE if b else A_STATE
    res = []
    for f in range(pre + frames):
        g = f - pre
        if g == 0:
            D.lfo_set(rows[0], 0, spd=spd, mult=mult, fade=fade, dest=dest, wave=wave, sph=sph, mode=mode, dep=127)
        if modes and g in modes:
            D.lfo_set(rows[0], 0, spd=spd, mult=mult, fade=fade, dest=dest, wave=wave, sph=sph, mode=modes[g], dep=127)
        tr = g in trigs
        if b:
            o = box.b_frame(rows, 0, tr)
        else:
            o = box.frame(rows, 1 if tr else 0)
        if g >= 0:
            v = box.rec_vals(0, 0, blk)
            v['row'] = o[0][dest]
            if b:
                v['lat'] = box.lat_b(0, 0)
            res.append(v)
    return res


def stock_stop(inc, mode, cycle, frames):
    """the frame (counted from the trig, 0) in which the stock tests stop a run whose phase is 0 after the trig"""
    half = cycle // 2
    ph = 0
    for j in range(1, frames):
        new = (ph + inc) & 0xffffffff
        if j >= 2:
            if mode == HALF and (s32(new - half) ^ s32(ph - half)) < 0:
                return j
            if mode == ONE and new > cycle - 1:
                return j
        if new > cycle - 1:
            new = (new - cycle) & 0xffffffff if inc >= 0 else (new + cycle) & 0xffffffff
        ph = new
    return None


def increment(ref, cycle):
    d = (ref[3]['phase'] - ref[2]['phase']) % cycle
    return d if d < cycle // 2 else d - cycle


def check_run(ba, b, mode, wave, sph, spd, mult, fade, frames=200, pre=7):
    cycle = CYCLE_B if b else CYCLE_A
    got = run(ba, b, mode, wave, sph, spd, mult, fade, frames, {0}, pre=pre)
    ref = run(ba, b, TRIG, wave, sph, spd, mult, fade, frames, {0}, pre=pre)
    inc = increment(ref, cycle)
    want_stop = stock_stop(inc, mode, cycle, frames)
    stop = next((i for i, v in enumerate(got) if v['stopped']), None)
    errs = []
    if stop != want_stop:
        errs.append('stop at %s, expected %s (inc %d)' % (stop, want_stop, inc))
    end = stop if stop is not None else frames
    for i in range(min(end, want_stop or frames)):
        for k in ('wave', 'out', 'page'):
            if got[i][k] != ref[i][k]:
                errs.append('frame %d %s %d != TRIG\'s %d' % (i, k, got[i][k], ref[i][k]))
                break
        if errs:
            break
    if stop is not None and stop > 0:
        held = got[stop - 1]['out']
        for i in range(stop, frames):
            if got[i]['out'] != held or got[i]['page'] != 0 or got[i]['row'] != got[stop]['row']:
                errs.append('frame %d after the stop: out %d (held %d), page %d, row %d' % (
                    i, got[i]['out'], held, got[i]['page'], got[i]['row']))
                break
    lat = got[1]['lat']
    if wave <= 5 and not (lat & 0x8000 and (lat & 0x7fff) == sph << 8):
        errs.append('latch 0x%04x after the trig' % lat)
    return {'stop': stop, 'inc': inc, 'errors': errs[:3]}


def check_onehalf(ba, b, quick=False):
    cases, bad = 0, []
    sphs = (0, 1, 32, 63, 64, 74, 85, 96, 100, 120, 127)
    for wave in range(6):
        for mode in (ONE, HALF):
            for sph in sphs:
                for spd, mult in SPEEDS:
                    for fade in ((64,) if quick else (64, 40, 90)):
                        r = check_run(ba, b, mode, wave, sph, spd, mult, fade)
                        cases += 1
                        if r['errors'] or r['stop'] is None:
                            bad.append({'wave': wave, 'mode': mode, 'sph': sph, 'spd': spd, 'mult': mult,
                                        'fade': fade, **r})
    return {'cases': cases, 'failing': bad[:10], 'n_failing': len(bad), 'ok': not bad}


def check_changes(ba, b):
    """retrig mid-run and after the stop; ONE/HALF -> FREE/TRIG/HOLD mid-run"""
    out, bad = [], []
    for wave in (0, 1, 2, 3):
        for mode in (ONE, HALF):
            for sph in (32, 85, 120):
                # retrigs at 20 and 150: the TRIG reference retriggered at the same frames
                got = run(ba, b, mode, wave, sph, 127, 7, 64, 260, {0, 20, 150}, pre=5)
                ref = run(ba, b, TRIG, wave, sph, 127, 7, 64, 260, {0, 20, 150}, pre=5)
                for seg in ((20, 150), (150, 260)):
                    i0, i1 = seg
                    stop = next((i for i in range(i0, i1) if got[i]['stopped']), i1)
                    if any(got[i]['wave'] != ref[i]['wave'] for i in range(i0, stop)):
                        bad.append(('retrig', wave, mode, sph, seg))
                    if stop < i1 and any(got[i]['out'] != got[stop - 1]['out'] for i in range(stop, i1)):
                        bad.append(('held after retrig', wave, mode, sph, seg))
                for to in (FREE, TRIG, HOLD):
                    got = run(ba, b, mode, wave, sph, 127, 7, 64, 120, {0}, modes={9: to}, pre=5)
                    ref = run(ba, b, TRIG, wave, sph, 127, 7, 64, 120, {0}, pre=5)
                    if any(got[i]['wave'] != ref[i]['wave'] for i in range(9, 120)):
                        k = next(i for i in range(9, 120) if got[i]['wave'] != ref[i]['wave'])
                        bad.append(('switch', wave, mode, sph, to, k, got[k]['wave'], ref[k]['wave']))
                    if got[20]['lat'] != 0:
                        bad.append(('latch kept after switch', wave, mode, sph, to, got[20]['lat']))
                out.append((wave, mode, sph))
    return {'cases': len(out), 'failing': bad[:10], 'n_failing': len(bad), 'ok': not bad}


def check_vs_stock(dj, ba, b):
    """RAND in ONE/HALF, and SPH 0 on the other waves: DJ01's until the stop, then held"""
    bad, n = [], 0
    for wave in range(7):
        for mode in (ONE, HALF):
            for spd, mult in ((127, 7), (30, 8)):
                g = run(ba, b, mode, wave, 0, spd, mult, 64, 160, {0}, pre=6)
                s = run(dj, b, mode, wave, 0, spd, mult, 64, 160, {0}, pre=6)
                n += 1
                stop_g = next((i for i, v in enumerate(g) if v['stopped']), None)
                stop_s = next((i for i, v in enumerate(s) if v['stopped']), None)
                if stop_g != stop_s or stop_g is None:
                    bad.append(('stop', wave, mode, spd, stop_g, stop_s))
                    continue
                if wave == 6:                            # RAND: no stop value of its own (its slew writes the output
                    if g != s:                           # in the stop's frame too), so DJ01's throughout
                        bad.append(('RAND not DJ01\'s', mode, spd))
                    continue
                if any(g[i]['wave'] != s[i]['wave'] or g[i]['out'] != s[i]['out'] for i in range(stop_g)):
                    bad.append(('before the stop', wave, mode, spd))
                if any(g[i]['out'] != g[stop_g - 1]['out'] for i in range(stop_g, 160)):
                    bad.append(('not held', wave, mode, spd))
    return {'cases': n, 'failing': bad[:10], 'n_failing': len(bad), 'ok': not bad}


def check_chaos(ba, b, frames, seed=21):
    rnd = random.Random(seed)
    rows = D.base_rows(rnd)
    ba.setup()
    ba.reset_lfos()
    bad = []
    cycle = CYCLE_B if b else CYCLE_A
    for f in range(frames):
        if f % 13 == 0:
            for r in range(16):
                for l in range(3):
                    D.lfo_set(rows[r], l, spd=rnd.randrange(128), mult=rnd.randrange(24), fade=rnd.randrange(128),
                              dest=rnd.choice([0, 25, 70, 3, 2]), wave=rnd.randrange(16), sph=rnd.randrange(128),
                              mode=rnd.randrange(5), dep=rnd.randrange(128))
        if b:
            t = f % 16
            ba.b_frame(rows, t, rnd.random() < 0.2)
            for l in range(3):
                v = ba.rec_vals(t, l, B_STATE)
                lat = ba.lat_b(t, l)
                if v['phase'] >= cycle or (lat and (not lat & 0x8000 or (lat & 0x7fff) > 0x7f00)):
                    bad.append((f, t, l, v['phase'], lat))
        else:
            ba.frame(rows, rnd.getrandbits(16) if rnd.random() < 0.2 else 0)
            for r in range(16):
                for l in range(3):
                    v = ba.rec_vals(r, l)
                    lat = v['lat']
                    wave = rows[r][D.LFOK[l] + 4] >> 8
                    if v['phase'] >= cycle or (lat & 0x8000 and (lat & 0x7fff) > 0x7f00):
                        bad.append((f, r, l, v['phase'], lat, wave))
    return {'frames': frames, 'failing': bad[:10], 'n_failing': len(bad), 'ok': not bad}


def lengths(dj, ba, b):
    res = {}
    for name, box in (('DJ01', dj), ('BA02', ba)):
        for mode in (ONE, HALF):
            for sph in (0, 32, 64, 96, 127):
                g = run(box, b, mode, 0, sph, 127, 7, 64, 200, {0}, pre=3)
                stop = next((i for i, v in enumerate(g) if v['stopped']), None)
                res['%s %s SPH %d' % (name, 'ONE' if mode == ONE else 'HALF', sph)] = stop
    return res


# ---------------------------------------------------------------- 7. KEY (WAVE 16): the track's own scale and root
PAT, PLAYING, VNOTE, SCALE_TAB = 0x10400000, 0x42c5b9a0, 0x8000dda0, 0x4020e358


def scale_classes(img, sc):
    if sc < 0 or sc > 34:
        return set(range(12))
    cl = set()
    for i in range(8):
        n = struct.unpack('>i', img[SCALE_TAB + 4 * (8 * sc + i) - LOAD:SCALE_TAB + 4 * (8 * sc + i) - LOAD + 4])[0]
        if n < 0:
            break
        cl.add(n % 12)
    return cl


def nearest_key(x, note, root, classes):
    """the nearest pitch in the scale (classes from the root) to note + (x - 0x4000) / 256, as a TUNE word; ties down"""
    rel = x - 0x4000
    best = None
    for q in range(-140, 140):                           # semitones from the note
        y = 0x4000 + 256 * q
        if 0 <= y <= 0x7f00 and (note + q - root) % 12 in classes:
            d = abs(256 * q - rel)
            if best is None or d < best[0] or (d == best[0] and q < best[1]):
                best = (d, q)
    return 0x4000 + 256 * best[1]


def check_key(ba, frames=300):
    m = ba.m
    for a_ in (PAT, PAT + 0x100000, 0x42c00000):
        m.ensure(a_)
    img = ba.img
    known = {0: {0, 2, 4, 5, 7, 9, 11}, 7: {0, 3, 5, 7, 10}, 17: {0, 2, 3, 7, 8}, 13: {0, 2, 3, 5, 7, 8, 10, 11}}
    table_ok = all(scale_classes(img, sc) == cl for sc, cl in known.items()) and scale_classes(img, -1) == set(range(12))
    cases = [('pattern', -1, 0), ('pattern', 0, 0), ('pattern', 0, 9), ('pattern', 7, 2), ('pattern', 17, 9),
             ('pattern', 13, 11), ('pattern', 34, 5), ('track', None, None)]
    bad, n_on, n_q, n_off = [], 0, 0, 0
    for where, sc, root in cases:
        ba.wr(PLAYING, struct.pack('>I', PAT))
        ba.wr(PAT, bytes(0x1dd40 + 0x40))
        per_track = {}
        if where == 'pattern':
            ba.wr(PAT + 0x1dd40 + 0x1d, struct.pack('>b', sc))
            ba.wr(PAT + 0x1dd40 + 0x1f, struct.pack('>b', root))
        else:
            ba.wr(PAT + 0x1dd40 + 0x21, bytes([4]))
            ba.wr(PAT + 0x1dd40 + 0x1d, struct.pack('>b', 0))       # the pattern's, not used: Ionian on C
            for t in range(16):
                tsc, troot = (t * 5) % 36 - 1, (t * 7) % 12 + (12 if t % 3 == 0 else 0)
                ba.wr(PAT + 0x6a5 * t + 0x492, struct.pack('>b', tsc))
                ba.wr(PAT + 0x6a5 * t + 0x494, struct.pack('>b', troot))
                per_track[t] = (tsc, troot % 12)
        for r in range(16):
            ba.wr(VNOTE + 4 * r, struct.pack('>I', (30 + 5 * r) << 16))
        for mach in (0, 1, 2, 3, 4):
            dests = list(D.TUNE_K.get(mach, ())) + [26, 70]
            for dest in dests:
                seed = 101 + dest + 7 * mach
                res = {}
                for w in (7, 16):
                    ba.setup(machines=[mach] * 16)
                    ba.wr(ba.mp['dv_rng_a'], struct.pack('>I', seed))
                    for t in range(16):
                        for l in range(3):
                            ba.wr(ba.mp['dv_loop_a'] + 72 * (3 * t + l), bytes(72))
                    ba.reset_lfos()
                    rows = D.quiet_rows()
                    for r in range(16):
                        rows[r][dest] = (4 + 8 * r) << 8
                        D.lfo_set(rows[r], 1, spd=110, mult=5, fade=64, dest=dest, wave=w, sph=64, mode=4,
                                  dep=20 + 7 * r)
                    seq = []
                    for f in range(frames // 10):
                        o = ba.frame(rows)
                        seq.append([o[r][dest] for r in range(16)])
                    res[w] = seq
                tune = dest in D.TUNE_K.get(mach, ())
                for frx, fry in zip(res[7], res[16]):
                    for r, (x, y) in enumerate(zip(frx, fry)):
                        if tune:
                            n_on += 1
                            if where == 'pattern':
                                cl, rt = scale_classes(img, sc), root % 12
                            else:
                                tsc, rt = per_track[r]
                                cl = scale_classes(img, tsc)
                            want = nearest_key(x, 30 + 5 * r, rt, cl)
                            if y != want:
                                bad.append((where, sc, root, mach, dest, r, x, y, want))
                            if y != x:
                                n_q += 1
                        else:
                            n_off += 1
                            if y != x:
                                bad.append((where, sc, root, mach, dest, r, x, y, 'unrounded'))
    ba.wr(PLAYING, struct.pack('>I', 0))
    return {'scale_table_as_read': table_ok, 'values_on_tuning_rows': n_on, 'of_them_moved': n_q,
            'values_on_other_dest': n_off, 'bad': bad[:12], 'n_bad': len(bad),
            'ok': table_ok and not bad and n_q > 0}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--frames', type=int, default=1500)
    ap.add_argument('--quick', action='store_true')
    ap.add_argument('--key-only', action='store_true')
    ap.add_argument('--part', choices=('all', 'A', 'B'), default='all')
    ap.add_argument('--only', choices=('same',))
    ap.add_argument('--json')
    a = ap.parse_args()
    dj, ba = Box('DJ01'), Box('BA02')
    rep = {}
    rep['emac'] = check_emac(ba)
    print('0 EMAC model:', rep['emac'])
    rep['key'] = check_key(ba)
    print('7 KEY:', rep['key'])
    if a.key_only:
        return
    if a.only == 'same':
        rep = {'same_a': check_same_a(dj, ba, a.frames), 'same_b': check_same_b(dj, ba, a.frames)}
        print('1 A:', rep['same_a'], '\n1 B:', rep['same_b'])
        rep['ok'] = rep['same_a']['ok'] and rep['same_b']['ok']
        print('ALL OK' if rep['ok'] else 'FAILED')
        if a.json:
            json.dump(rep, open(a.json, 'w'), indent=1)
        return
    if a.part in ('all', 'A'):
        rep['same_a'] = check_same_a(dj, ba, a.frames)
        print('1 A FREE/TRIG/HOLD + DV, DJ01 = BA02:', rep['same_a'])
    if a.part in ('all', 'B'):
        rep['same_b'] = check_same_b(dj, ba, a.frames)
        print('1 B:', rep['same_b'])
    for b in (False, True):
        n = 'B' if b else 'A'
        if a.part != 'all' and a.part != n:
            continue
        rep['onehalf_' + n] = check_onehalf(ba, b, a.quick)
        print('2 %s ONE/HALF vs TRIG:' % n, {k: v for k, v in rep['onehalf_' + n].items() if k != 'failing'},
              rep['onehalf_' + n]['failing'][:2])
        rep['changes_' + n] = check_changes(ba, b)
        print('3 %s retrigs, mode changes:' % n, rep['changes_' + n])
        rep['vs_stock_' + n] = check_vs_stock(dj, ba, b)
        print('4 %s RAND and SPH 0 vs DJ01:' % n, rep['vs_stock_' + n])
        rep['chaos_' + n] = check_chaos(ba, b, a.frames // 2)
        print('5 %s chaos:' % n, rep['chaos_' + n])
        rep['lengths_' + n] = lengths(dj, ba, b)
        print('6 %s run lengths:' % n, rep['lengths_' + n])
    rep['ok'] = all(v.get('ok', True) for v in rep.values() if isinstance(v, dict) and 'ok' in v)
    print('ALL OK' if rep['ok'] else 'FAILED')
    if a.json:
        json.dump(rep, open(a.json, 'w'), indent=1)


if __name__ == '__main__':
    main()
