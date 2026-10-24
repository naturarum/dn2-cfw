#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""DJ01: evaluator A on the memory of the booted OS, with the stock smoother's settled values.

    cd vendor/digikit && uv run python ../../scripts/dj01_booted.py out/DJ01/emu/dj01-live/end.snap [--json OUT]

The snapshot is DJ01 cold-booted in digikit (scripts/dj01-emu.sh), after scripts/dj01-live.script ran with the audio
clock: track 1's LFO1 WAVE turned to DV-F on the panel, and the frame handler called through dv_frame_hook 1,500
times a second (dv_frame counts it). There the LFOs see wrong values: the smoother runs on the EMAC in fractional
mode, which digikit's Unicorn computes at half the product, so every smoothed word settles near 1/34 of its value
(notes/T3.3) and WAVE 7 reads as 0. Here the smoothed rows (0x800068e4 + 0x22 + 0xca r) are given the raw mirror's
words (0x80003af0 + 0x22 + 0xca r: what the unit's smoother settles to) before each call, and A is called as the frame
handler calls it (args from 0x400272b4..0x400272d4: the rows, *0x402a132c, the trig mask, 0, two masks' arrays, 0),
through dv_frame_hook, MACSR = 0x20. Everything else is the booted OS's own memory: the voice rows' owners
(0x80005308), the machine records (0x80007670), the LFO state, the kit's parameters. Checks: the frame counter
moved during the live run; the generator seeded at first use; track 1's LFO1 loop made; voice rows owned by track 1
read their positions and step 16 times per cycle through that loop (the kit's MODE 0: two values, SPH 0: renewed at
each step); then (SPD 63, MULT x64, DEST TUN1, WAVE PMAJ, MODE HALF, SPH 64, DEP +12 semitones on every row's LFO1, the machine
records' type) every TUN1 result a PMAJ note.
"""
import argparse
import json
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'vendor', 'digikit'))
from emu import snapshot, harness  # noqa: E402

ROWS, RAW, ROWK = 0x800068e4, 0x80003af0, 0xca
OWNER, MACHINE = 0x80005308, 0x80007670
A_STATE = 0x44640c18
TRAMP, SCR = 0x10200000, 0x10300000
PMAJ = (0, 2, 4, 7, 9)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('snap')
    ap.add_argument('--json', default=os.path.join(ROOT, 'out', 'DJ01', 'booted.json'))
    a = ap.parse_args()
    mp = {k: int(v, 16) for k, v in json.load(open(os.path.join(ROOT, 'out', 'DJ01', 'build',
                                                                 'DJ01-main.syx.map.json'))).items()}
    m, _, _ = snapshot.restore(a.snap)
    m.install_isa_patches()
    uc = m.uc
    for x in (TRAMP, SCR, 0x10000000):
        m.ensure(x)
    uc.mem_write(TRAMP, bytes.fromhex('a93c00000020 4ef9%08x' % mp['dv_frame_hook']))
    rd = lambda x, n: bytes(uc.mem_read(x, n))                       # noqa: E731
    u32 = lambda x: struct.unpack('>I', rd(x, 4))[0]                 # noqa: E731
    u16 = lambda x: struct.unpack('>H', rd(x, 2))[0]                 # noqa: E731
    res = {'dv_frame_after_live_run': u32(mp['dv_frame']), 'rng_before': '0x%08x' % u32(mp['dv_rng_a']),
           'owners': [u32(OWNER + 4 * r) for r in range(16)], 'machines': [rd(MACHINE + 0x99 * r, 1)[0] for r in range(16)],
           'rate_0x402a132c': u32(0x402a132c)}
    raw = bytearray(rd(RAW + 0x22, 16 * ROWK))
    res['row0_lfo1_raw'] = {n: '0x%04x' % struct.unpack('>H', raw[2 * k:2 * k + 2])[0]
                            for n, k in (('SPD', 1), ('MULT', 2), ('FADE', 3), ('DEST', 4), ('WAVE', 5), ('SPH', 6),
                                         ('MODE', 7), ('DEP', 8))}

    def frame(rows):
        uc.mem_write(ROWS + 0x22, bytes(rows))
        harness.call(m, TRAMP, [ROWS, u32(0x402a132c), 0, 0, SCR, SCR + 0x10, 0], stack_top=0x10000000 + 0xff000)
        return rd(ROWS + 0x22, 16 * ROWK)

    own1 = [r for r in range(16) if (res['owners'][r] & 15) == 0]
    steps, last, f = [], {}, 0
    while len(steps) < 20 and f < 30000:
        frame(raw)
        for r in own1[:1]:
            rec = A_STATE + 0x78 * r
            ps = rd(rec + 0x26, 2)
            if last.get(r) != ps:
                loop = struct.unpack('>32h', rd(mp['dv_loop_a'], 64))    # the loop as it is at this step (SPH 0: renewed)
                steps.append((f, r, ps[0], ps[1] & 0x7f, struct.unpack('>i', rd(rec + 0x0c, 4))[0], loop[ps[0]]))
                last[r] = ps
        f += 1
    res['frames_run'] = f
    res['rng_after'] = '0x%08x' % u32(mp['dv_rng_a'])
    res['track1_lfo1_loop_made'] = rd(mp['dv_loop_a'] + 69, 1)[0] == 1
    res['steps_seen'] = [{'frame': s_[0], 'row': s_[1], 'pos': s_[2], 'step': s_[3], 'value_is_loop': s_[4] == s_[5] << 16}
                         for s_ in steps[:20]]
    seq = [s_[3] for s_ in steps[1:]]
    res['steps_ok'] = (len(steps) > 17 and all((b - a_) % 16 == 1 for a_, b in zip(seq, seq[1:]))
                       and all(s_[4] == s_[5] << 16 for s_ in steps[1:])
                       and [s_[2] for s_ in steps[1:5]] in ([1, 0, 1, 0], [0, 1, 0, 1]))     # MODE 0: 2 values

    # PMAJ on TUN1 (k 25), DEP +12 semitones, on every row: the machine record says which rows are WaveTone
    rows = bytearray(raw)
    for r in range(16):
        b = r * ROWK
        struct.pack_into('>H', rows, b + 2 * 1, 127 << 8)         # SPD 63, MULT x64: a new value every 9 frames
        struct.pack_into('>H', rows, b + 2 * 2, 6 << 8)
        struct.pack_into('>H', rows, b + 2 * 4, 25 << 8)          # DEST TUN1
        struct.pack_into('>H', rows, b + 2 * 5, 9 << 8)           # WAVE PMAJ
        struct.pack_into('>H', rows, b + 2 * 6, 64 << 8)          # SPH 64
        struct.pack_into('>H', rows, b + 2 * 7, 4 << 8)           # MODE HALF: 32 values
        struct.pack_into('>H', rows, b + 2 * 8, (64 + 6) << 8)    # DEP +12 (DEP shows 2 x (value - 64))
    bad, n, moved = [], 0, set()
    for f in range(600):
        out = frame(rows)
        for r in range(16):
            v = struct.unpack('>H', out[r * ROWK + 50:r * ROWK + 52])[0]
            mach = res['machines'][r]
            if mach in (1, 2, 3):
                n += 1
                q = (v - 0x4000) / 256.0
                moved.add(v)
                if q != int(q) or int(q) % 12 not in PMAJ:
                    bad.append((f, r, hex(v)))
    res['pmaj_tun1'] = {'values': n, 'distinct': sorted('%+d' % ((v - 0x4000) // 256) for v in moved), 'bad': bad[:10],
                        'ok': n > 0 and not bad and len(moved) > 3}
    res['ok'] = (res['dv_frame_after_live_run'] > 1000 and res['track1_lfo1_loop_made'] and res['steps_ok']
                 and res['pmaj_tun1']['ok'] and res['row0_lfo1_raw']['WAVE'] == '0x0700')
    json.dump(res, open(a.json, 'w'), indent=1)
    print(json.dumps(res, indent=1)[:3000])
    print('ALL OK' if res['ok'] else 'SOME CHECK FAILED')


if __name__ == '__main__':
    main()
