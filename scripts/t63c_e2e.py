#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
# The resume and raise_vector follow digikit's tools/sharcframe.py (Em D, GPL-2.0-or-later), as
# scripts/t60_cf_frame.py and scripts/t63c_cf_frame.py do.
"""T6.3 CMP2 end to end, the ColdFire half: the wire frames CMP2's main OS sends after a knob turn.

    cd vendor/digikit && DT2_SYX=.../C2EM.syx DT2_SECTIONS=.../C2EM/sections uv run python \\
        ../../scripts/t63c_e2e.py BEFORE.snap AFTER.snap OUTDIR [--pre 300] [--post 900] [--emac-fix]

BEFORE.snap / AFTER.snap: the end snapshots of two key scripts on C2EM (scripts/t63c-setup.script, then
scripts/t63c-turn.script from its end; scripts/t63b-emu.sh with T63_STAGE=CMP2). The live emulator raises no
frame interrupt (its DSPI2 has no DSP behind it), so the frames are made here as scripts/t63c_cf_frame.py
makes them: the vector-191 handler run PRE times from BEFORE, then POST times from AFTER (the smoother picks
the turn up where BEFORE left it: no frame ran in between), DSPI2 SR read as "done", each pass's wire frame
(the 0xabc data halves the PUSHR entries carry) captured after the driver call. --emac-fix: digikit's Unicorn
leaves out the EMAC's fractional mode, so its smoother settles at about 1/34.3 of the value (T3.3); with this
the smoothed row-16 words of k 70..96 (the tail's) are recomputed after each FUN_400db28e with the unit's
arithmetic (0x03d7 x raw << 1 + 0x7c290000 x state >> 31, word = (state + 0x8000) >> 16; unity gain), from a
state settled on BEFORE's raw words. Writes OUTDIR/frames.bin (the frames, in order), frames.json (which k
the turn changed, the turn's frame index, the band to expect in tools/dspsplice/verify_cmp2.py --e2e).
Emulator RAM only; the snapshot files are not changed.
"""
import argparse
import json
import os
import struct
import sys

DK = os.environ.get('DIGIKIT', os.getcwd())
sys.path.insert(0, DK)
from unicorn import UcError, UC_HOOK_MEM_READ, UC_HOOK_CODE  # noqa: E402
from unicorn.m68k_const import UC_M68K_REG_PC, UC_M68K_REG_A3  # noqa: E402

VECTOR = 191
AFTER_DRIVER = 0x40025ea4                 # the instruction after the jsr at 0x40025e9e
SMOOTH_DONE = 0x400db2ee                  # FUN_400db28e: after its loop's last store
COUNTER = 0x40287c38
STAGE = 0x80002510
FRAME_LEN = 0xabc
SR = 0xEC03802C
KITPTR, FXOFF = 0x800052a0, 0x48e4
RAW16 = 0x800047b2                        # raw mirror row 16, word k at +2k
OUT0, STATE0 = 0x800068e4, 0x8000de60     # the smoother's output words and 32-bit states (word j)
J16 = (RAW16 - 0x80003af0) // 2           # word index of row 16, k 0, in the smoother's source block
KS = range(70, 97)


def s32(x):
    x &= 0xffffffff
    return x - (1 << 32) if x & 0x80000000 else x


def unit_step(raw, s):
    """one pass of the unit's smoother for one word (EMAC fractional mode)"""
    raw = raw - 0x10000 if raw & 0x8000 else raw
    return s32(((0x03d7 * raw) << 1) + ((0x7c290000 * s) >> 31))


def frames_from(snap, passes, fix, state, limit):
    from emu.longrun import build
    m, ev, st, pc, inq, at = build(snap, softfloat=True, bitmap=True, dsp=True, unblock=True,
                                   deferred_components=('timers',))
    uc = m.uc
    rd = lambda a, n: bytes(uc.mem_read(a, n))                 # noqa: E731

    def sr_read(uc_, typ, addr, size, val, data):
        uc_.mem_write(SR, struct.pack('>I', 0x90000000))
    uc.hook_add(UC_HOOK_MEM_READ, sr_read, begin=SR, end=SR + 3)
    wires = []

    def after_driver(uc_, addr, size, user):
        stg = bytes(uc_.mem_read(STAGE, 4 * (FRAME_LEN // 2)))
        wires.append(b''.join(stg[4 * i + 2:4 * i + 4] for i in range(FRAME_LEN // 2)))
    uc.hook_add(UC_HOOK_CODE, after_driver, begin=AFTER_DRIVER, end=AFTER_DRIVER)

    def smooth_done(uc_, addr, size, user):
        src = uc_.reg_read(UC_M68K_REG_A3)
        for k in KS:
            j = J16 + k
            raw = struct.unpack('>H', bytes(uc_.mem_read(src + 2 * j, 2)))[0]
            state[k] = unit_step(raw, state[k])
            uc_.mem_write(STATE0 + 4 * j, struct.pack('>I', state[k] & 0xffffffff))
            uc_.mem_write(OUT0 + 2 * j, struct.pack('>H', ((state[k] + 0x8000) >> 16) & 0xffff))
    if fix:
        uc.hook_add(UC_HOOK_CODE, smooth_done, begin=SMOOTH_DONE, end=SMOOTH_DONE)
        # the handler sends the frame (and compl_tail reads the smoothed words) before it calls the smoother, so
        # the first pass's tail would carry the snapshot's own words (the live runs raise no frame interrupt, so
        # the emulator's smoother never ran on the turned values): put the unit-model words in place first
        for k in KS:
            j = J16 + k
            uc.mem_write(STATE0 + 4 * j, struct.pack('>I', state[k] & 0xffffffff))
            uc.mem_write(OUT0 + 2 * j, struct.pack('>H', ((state[k] + 0x8000) >> 16) & 0xffff))
    resume = uc.reg_read(UC_M68K_REG_PC)
    raw16 = [struct.unpack('>H', rd(RAW16 + 2 * k, 2))[0] for k in range(101)]
    kit = struct.unpack('>I', rd(KITPTR, 4))[0]
    live = [struct.unpack('>H', rd(kit + FXOFF + 2 * k, 2))[0] for k in range(101)]
    # the first pass from a snapshot sends a type-1 frame (word 0 = 1, not the block frame's 3): one warm-up pass,
    # its frame dropped
    for n in range(passes + 1):
        uc.mem_write(COUNTER, bytes(4))
        uc.reg_write(UC_M68K_REG_PC, resume)
        m.halt_vec = None
        if not m.raise_vector(VECTOR):
            raise SystemExit('vector %d holds no handler' % VECTOR)
        try:
            uc.emu_start(uc.reg_read(UC_M68K_REG_PC), resume, count=limit)
        except UcError as e:
            raise SystemExit('pass %d: %s' % (n, e))
        if uc.reg_read(UC_M68K_REG_PC) != resume:
            raise SystemExit('pass %d did not return' % n)
    m.close()
    return wires[1:], raw16, live


def main():
    p = argparse.ArgumentParser()
    p.add_argument('before')
    p.add_argument('after')
    p.add_argument('outdir')
    p.add_argument('--pre', type=int, default=300)
    p.add_argument('--post', type=int, default=900)
    p.add_argument('--emac-fix', action='store_true')
    p.add_argument('--limit', type=lambda s: int(s, 0), default=5_000_000)
    a = p.parse_args()
    os.makedirs(a.outdir, exist_ok=True)
    state = {}
    # the unit's smoother settled on BEFORE's raw words (read from BEFORE first)
    w0, raw_b, live_b = frames_from(a.before, 1, False, {k: 0 for k in KS}, a.limit)
    for k in KS:
        r = raw_b[k] - 0x10000 if raw_b[k] & 0x8000 else raw_b[k]
        state[k] = r << 16
    pre, _, _ = frames_from(a.before, a.pre, a.emac_fix, state, a.limit)
    post, raw_a, live_a = frames_from(a.after, a.post, a.emac_fix, state, a.limit)
    frames = pre + post
    with open(os.path.join(a.outdir, 'frames.bin'), 'wb') as fh:
        fh.write(b''.join(frames))
    changed = [k for k in range(101) if raw_a[k] != raw_b[k]]
    band = None
    if changed and all(70 <= k <= 86 for k in changed):
        band = ['LOW', 'MID', 'HIGH'][min((changed[0] - 70) // 6, 2)]
    tails = [struct.unpack('>30H', f[0xa80:]) for f in frames]
    meta_word0 = sorted(set(struct.unpack('>H', f[:2])[0] for f in frames))
    valid = sum(1 for t in tails if t[0] == 0xC3B1 and ((~sum(t[:29])) & 0xffff) == t[29])
    meta = {'before': a.before, 'after': a.after, 'pre': a.pre, 'post': a.post, 'emac_fix': a.emac_fix,
            'frames': len(frames), 'turn_frame': a.pre, 'raw16_changed_k': changed,
            'turned': {str(k): [raw_b[k], raw_a[k]] for k in changed},
            'live_changed_k': [k for k in range(101) if live_a[k] != live_b[k]],
            'expect_band': band, 'tails_valid': valid, 'frame_word0_values': meta_word0,
            'tail_k70_86_first_last': [list(tails[0][2:19]), list(tails[-1][2:19])],
            'tail_seq_first_last': [tails[0][1], tails[-1][1]],
            'raw16_k70_86_before_after': [raw_b[70:87], raw_a[70:87]]}
    if changed:
        k = changed[0]
        meta['turned_k_tail_word_every_50_frames'] = [t[k - 68] for t in tails[::50]]
    json.dump(meta, open(os.path.join(a.outdir, 'frames.json'), 'w'), indent=1)
    print(json.dumps({x: meta[x] for x in ('frames', 'raw16_changed_k', 'turned', 'expect_band', 'tails_valid',
                                           'tail_seq_first_last')}, indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main())
