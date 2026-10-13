#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""T5.3: summarise the emulator runs of SE01's main OS (SE1M) against GT01.

    venv/bin/python scripts/t53_report.py [--json out/T5.3/SE01-main/report.json]

Reads out/T5.3/SE01-main/emu/{SE1M,GT01}/t53-*/t53.json (scripts/t53_live.py: the delay page's
state at every screen) and the frames captured by scripts/t53_frame.py (out/T5.3/SE01-main/frames/),
and checks, printing each with yes/NO:
  echo     each knob of the Echo page changes its own k (A 32, B 33, C 34, D 35, E 36, F 37, G 40,
           H 38), and the raw mirror and kit 0's stored record follow; MODE runs 0..6 and stops
           there; GT01, the same keys: X stops at 1, G changes nothing;
  frames   the frame word of each MODE run: 0xa0a = MODE << 8, 0xa18 = AGE << 8 (smoothed: within
           0x20 of it after 300 handler passes, the T3.3 capture);
  kit      MODE and AGE set on A01, pattern A02 shows its own kit, A01 again shows them back;
  plock    (the lock records come from scripts/t61_locks.py; here) the values after the held turns;
  midi     CC 22 sets MODE unscaled and clamped; NRPN 257 = 5 sets 5;
  band     the picture's inputs (fxe_band_seen) are CNTR's and BW's values, and the drawn flat top
           starts and ends where the band's edges are.
"""
import argparse
import glob
import json
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = os.path.join(ROOT, 'out', 'T5.3', 'SE01-main')
sys.path.insert(0, os.path.join(ROOT, 'scripts'))
K = list(range(32, 41))
res, lines = {}, []


def load(name, script):
    p = os.path.join(BASE, 'emu', name, script, 't53.json')
    return json.load(open(p)) if os.path.exists(p) else None


def v(entry, field, k):
    return entry[field][K.index(k)]


def check(what, ok, detail=''):
    res[what] = {'ok': bool(ok), 'detail': detail}
    lines.append('%-3s %s%s' % ('yes' if ok else 'NO', what, ('  [' + detail + ']') if detail else ''))


def echo():
    for name in ('SE1M', 'GT01'):
        d = load(name, 't53-echo')
        if not d:
            check('%s t53-echo ran' % name, False)
            continue
        scr = [k for k in d if not k.startswith('__')]
        prev = d[scr[0]]
        changed = {}
        # a screen against the one before it: its own turn, and the previous knob's turn back
        # (taken after the previous screen) show
        own = {'A': 32, 'B': 33, 'C': 34, 'D': 35, 'E': 36, 'F': 37, 'G': 40, 'H': 38}
        last_k = None
        for s in scr[1:]:
            cur = d[s]
            ks = [k for k in K if v(cur, 'live_k32_40', k) != v(prev, 'live_k32_40', k)]
            letter = s.split('-')[1] if s.count('-') >= 2 else None
            ks = [k for k in ks if k != last_k or own.get(letter) == k]
            changed[s] = ks
            if letter in own and s.endswith('+') or (letter in own and '+1' in s):
                last_k = own[letter]
            elif letter == 'B':
                last_k = None
            follow = all(v(cur, 'mirror_k32_40', k) == v(cur, 'live_k32_40', k) and
                         v(cur, 'stored_kit0_id8_16', k) == v(cur, 'live_k32_40', k) for k in K)
            if not follow:
                check('%s %s: mirror and stored kit follow the live values' % (name, s), False)
            prev = cur
        mode = [v(d[s], 'live_k32_40', 33) >> 8 for s in scr]
        age = [v(d[s], 'live_k32_40', 40) >> 8 for s in scr]
        res['%s echo values' % name] = {s: [v(d[s], 'live_k32_40', k) for k in K] for s in scr}
        if name == 'SE1M':
            want = {'02-A-time+': [32], '18-C-wid+': [34], '19-D-fdbk+': [35], '20-E-cntr+': [36],
                    '21-F-bw+': [37], '22-G-age+1': [40], '24-H-rev+': [38]}
            check('SE1M: A, C, D, E, F, G, H each change only their own k (32, 34, 35, 36, 37, 40, 38)',
                  all(changed.get(s) == k for s, k in want.items()), str({s: changed.get(s) for s in want}))
            up = [v(d['%02d-B-mode-up%d' % (2 + i, i)], 'live_k32_40', 33) >> 8 for i in range(1, 8)]
            down = [v(d['%02d-B-mode-down%d' % (10 + i, i)], 'live_k32_40', 33) >> 8 for i in range(1, 8)]
            check('SE1M: MODE (k 33) up from the kit\'s 1: 2..6 then stays 6; down 5..0 then stays 0',
                  up == [2, 3, 4, 5, 6, 6, 6] and down == [5, 4, 3, 2, 1, 0, 0], 'up %s down %s' % (up, down))
            check('SE1M: B changes only k 33', all(changed.get(s) in ([33], []) for s in scr if '-B-' in s))
            check('SE1M: AGE (k 40) 1 -> 2 after one detent, 22 after 21', (age[scr.index('22-G-age+1')],
                  age[scr.index('23-G-age+21')]) == (2, 22), str(age))
            e = d['__end__']
            check('SE1M: fxe_state 1 after one tick; page 17 knob G = 0x7a; MODE glyph = ALGO\'s (0x400bbee4), '
                  'AGE\'s = the knob (0x400c0986), flags 8 and 0xc kept',
                  e.get('fxe_state') == 1 and e.get('fxe_ticks') == 1 and e['page17_rows'][6] == '0x7a' and
                  e['disp_0x72'][8] == '400bbee4' and e['disp_0x7a'][8] == '400c0986' and
                  e['disp_0x72'][0] == '00000008' and e['disp_0x7a'][0] == '0000000c',
                  'state %s ticks %s' % (e.get('fxe_state'), e.get('fxe_ticks')))
        else:
            check('GT01 (same keys): X stops at 1, G (empty) changes nothing',
                  max(mode) == 1 and set(age) == {1}, 'X %s' % sorted(set(mode)))


def frames():
    """The frame handler copies the smoothed mirror into the frame; from emu.live's snapshots (no
    audio clock, so the smoother had not run) 300 passes move every word the same small fraction of
    the way. So: the raw mirror holds MODE / AGE exactly, and 0xa0a and 0xa18 are that same fraction
    of them as TIME's 0xa08 is of TIME (the control)."""
    out = {}
    MIR = 0x800047b2 - 0x80003af0
    for m in range(7):
        d = load('SE1M', 't53-frame-mode%d' % m)
        f = os.path.join(BASE, 'frames', 'mode%d' % m, 'end.tx.bin')
        if not d or not os.path.exists(f):
            check('frame run MODE %d present' % m, False)
            continue
        tx = open(f, 'rb').read()
        raw = open(f.replace('.tx.', '.raw.'), 'rb').read()
        w = lambda b, o: struct.unpack('>H', b[o:o + 2])[0]                 # noqa: E731
        live = d['__end__']['live_k32_40']
        r32, r33, r40 = (w(raw, MIR + 2 * k) for k in (32, 33, 40))
        f32, f33, f40 = w(tx, 0xa08), w(tx, 0xa0a), w(tx, 0xa18)
        frac = f32 / r32
        ok = (live[1] == m << 8 and live[8] == (1 + 3 * m) << 8 and r33 == m << 8 and r40 == (1 + 3 * m) << 8
              and abs(f33 - frac * r33) <= 1.5 and abs(f40 - frac * r40) <= 1.5)
        out[m] = {'live_k33': live[1], 'live_k40': live[8], 'mirror_k32_33_40': [r32, r33, r40],
                  'frame_0xa08_0xa0a_0xa18': [f32, f33, f40], 'fraction': round(frac, 5)}
        check('MODE %d, AGE %d: mirror k33 0x%04x k40 0x%04x; frame 0xa0a 0x%04x 0xa18 0x%04x = %.4f of them, as '
              'TIME\'s 0xa08 0x%04x of 0x%04x' % (m, 1 + 3 * m, r33, r40, f33, f40, frac, f32, r32), ok)
    res['frames'] = out


def kit():
    for name in ('SE1M', 'GT01'):
        d = load(name, 't53-kit')
        if not d:
            check('%s t53-kit ran' % name, False)
            continue
        get = lambda s: (d[s]['live_k32_40'][1] >> 8, d[s]['live_k32_40'][8] >> 8)      # noqa: E731
        st = {s: get(s) for s in d if not s.startswith('__')}
        res['%s kit' % name] = st
        a01 = [s for s in st if s.endswith('a01-set')][0]
        a02 = [s for s in st if s.endswith('a02-echo')][0]
        back = [s for s in st if s.endswith('-a01-echo')][-1]
        if name == 'SE1M':
            check('SE1M kit: A01 set to MODE 6 / AGE 11; A02 shows its own kit (%s); A01 again shows 6 / 11'
                  % (st[a02],), st[a01] == (6, 11) and st[back] == (6, 11) and st[a02] != (6, 11),
                  str(st))
        else:
            check('GT01 kit (same keys): X 1 / routing 1 kept', st[a01] == (1, 1) and st[back] == (1, 1), str(st))


def plock():
    for name in ('SE1M', 'GT01'):
        d = load(name, 't53-plock')
        if not d:
            check('%s t53-plock ran' % name, False)
            continue
        st = {s: [x >> 8 if x % 256 == 0 else x for x in d[s]['live_k32_40']] for s in d if not s.startswith('__')}
        res['%s plock values' % name] = st
        held = [s for s in st if s.endswith('-held') and 'fltr' not in s]
        base = [s for s in st if s.endswith('-echo')][0]
        check('%s: TIME, MODE, AGE, REV turned with the trig held: no value changes' % name,
              all(st[s] == st[base] for s in held), str({s: st[s] for s in [base] + held}))


def midi():
    for name in ('SE1M', 'GT01'):
        d = load(name, 't53-midi-cc')
        if not d:
            check('%s t53-midi-cc ran' % name, False)
            continue
        got = {s: d[s]['live_k32_40'][1] >> 8 for s in d if not s.startswith('__')}
        res['%s midi k33' % name] = got
        seq = [got[s] for s in sorted(got) if 'cc22' in s or 'nrpn' in s]
        want = [3, 6, 6, 6, 0, 1, 5] if name == 'SE1M' else [1, 1, 1, 1, 0, 1, 1]
        check('%s: CC 22 = 3, 6, 7, 127, 0, 1 then NRPN 257 = 5 -> MODE/X %s' % (name, seq), seq == want,
              'expected %s' % want)


def band():
    d = load('SE1M', 't53-band')
    if not d:
        check('SE1M t53-band ran', False)
        return
    import t53_make_mod
    _, half = t53_make_mod.band_table()
    out = {}
    for s in sorted(k for k in d if not k.startswith('__')):
        live = d[s]['live_k32_40']
        c, b = live[4], live[5]
        seen = d[s].get('fxe_band_seen')
        if b >> 8 >= 127:
            lo, wid = 0, 127 << 8
        else:
            lo, hi = max(0, c - half[b >> 8]), min(127 << 8, c + half[b >> 8])
            wid = hi - lo
        xh = 20 + ((lo * 52) >> 15)
        xl = xh + ((wid * 52) >> 15)
        rows = [l.rstrip('\n') for l in open(os.path.join(BASE, 'emu', 'SE1M', 't53-band', s + '.txt'))][:64]
        best = 63 - 0x11                     # the flat top: the picture's line at y 0x11 from the bottom
        r = rows[best]
        xs = [x for x in range(21, 71) if r[x] == '#']
        drawn = (xs[0], xs[-1]) if xs else None
        ok_in = seen == [c, b]
        ok_draw = drawn is not None and abs(drawn[0] - xh) <= 1 and abs(drawn[1] - min(xl, 70)) <= 1
        out[s] = {'cntr': c >> 8, 'bw': b >> 8, 'seen': seen, 'edges_knob': [lo / 256, (lo + wid) / 256],
                  'x_expected': [xh, xl], 'top_row': best, 'top_drawn': drawn}
        check('band %s: CNTR %d BW %d -> edges %.1f..%.1f, top x %d..%d expected, drawn %s; picture given %s'
              % (s, c >> 8, b >> 8, lo / 256, (lo + wid) / 256, xh, min(xl, 70), drawn, seen), ok_in and ok_draw)
    res['band'] = out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--json', default=os.path.join(BASE, 'report.json'))
    a = ap.parse_args()
    for f in (echo, frames, kit, plock, midi, band):
        f()
    print('\n'.join(lines))
    json.dump({'checks': res}, open(a.json, 'w'), indent=1)
    bad = [l for l in lines if l.startswith('NO')]
    print('%d checks, %d failed -> %s' % (len(lines), len(bad), os.path.relpath(a.json, ROOT)))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
