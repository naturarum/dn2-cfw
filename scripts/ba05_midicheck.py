#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""BA05: read the DIN MIDI out of scripts/ba05-midi.script's run (emu.live --midi-log) by the script's phases, from
its run.log (the PNG and midi-in lines carry the emulated time), and check the arp on the MIDI track:

    python3 scripts/ba05_midicheck.py out/BA05/emu/ba05-midi [--json out/BA05/midicheck.json]

  live    C3 held from MIDI in: the arp's notes, UP over RANGE 2 (C3, C4, C3, ...), one every 1/32 at the tempo,
          each ended (9n kk 00 or 8n) before the next, none after the key's release and the last note's end;
  arp     the trig on step 1 through the arp: its note, then the octave above, 1/32 apart, within the trig's 1/16;
  stock   the arp off: one note, the stock trig's (as notes/T2.4c-midi-leds-speed.md's proof: 1/16 long).
"""
import argparse
import json
import os
import re


def messages(path):
    out, on = [], False
    for ln in open(path):
        if ln.startswith('# messages'):
            on = True
            continue
        if on and ln.strip() and not ln.startswith('#'):
            t, hexmsg, *_ = ln.split()
            out.append((float(t), bytes.fromhex(hexmsg)))
    return out


def marks(run_log):
    """the script's PNGs and midi-in lines, by emulated time"""
    m = {}
    for ln in open(run_log):
        a = re.search(r'\[script\] png .*/([^/]+)\.png \(emulated ([0-9.]+) s', ln)
        b = re.search(r'\[script\] midi-in ([0-9a-f]+) \(emulated ([0-9.]+) s', ln)
        if a:
            m[a.group(1)] = float(a.group(2))
        if b:
            m['midi ' + b.group(1)] = float(b.group(2))
    return m


def notes(msgs, t0, t1):
    """note-ons and offs in [t0, t1): (t, note, velocity) with velocity 0 for an off"""
    out = []
    for t, b in msgs:
        if t0 <= t < t1 and len(b) == 3 and b[0] & 0xe0 == 0x80:
            vel = b[2] if b[0] & 0xf0 == 0x90 else 0
            out.append((round(t, 4), b[1], vel, b[0] & 0x0f))
    return out


def ons(ev):
    return [(t, n) for t, n, v, _ in ev if v]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('dir')
    ap.add_argument('--bpm', type=float, default=81.0)
    ap.add_argument('--json')
    a = ap.parse_args()
    msgs = messages(os.path.join(a.dir, 'midi.txt'))
    mk = marks(os.path.join(a.dir, 'run.log'))
    sixteenth = 60.0 / a.bpm / 4
    rep = {'messages': len(msgs), 'marks': mk, 'sixteenth_s': round(sixteenth, 4)}
    errs = []

    live = notes(msgs, mk['midi 903064'], mk['4-live-done'])
    lo = ons(live)
    gaps = [round((b[0] - a_[0]) / (sixteenth / 2), 3) for a_, b in zip(lo, lo[1:])]
    rep['live'] = {'events': live, 'note_ons': len(lo), 'gaps_in_32nds': gaps}
    if len(lo) < 8:
        errs.append('live: %d notes' % len(lo))
    if [n for _, n in lo] != [(0x30, 0x3c)[i % 2] for i in range(len(lo))]:
        errs.append('live: not C3 C4 C3 C4 ...')
    if any(abs(g - 1) > 0.1 for g in gaps):
        errs.append('live: not one every 1/32')
    if any(ch != 0 for *_, ch in live):
        errs.append('live: not on channel 1')
    sounding = {}
    for t, n, v, _ in live:                     # every note ended before the same note starts again
        if v and sounding.get(n):
            errs.append('live: %02x again while sounding at %.4f' % (n, t))
        sounding[n] = bool(v)
    if any(sounding.values()):
        errs.append('live: a note still on at the end: %s' % [n for n, s in sounding.items() if s])
    lens, start, rel = [], {}, mk['midi 803000']    # N.LEN's default, 0.53 step, goes out as the trig length 0.5625
    for t, n, v, _ in live:
        if v:
            start[n] = t
        elif n in start:
            cut = abs(t - rel) < 0.005             # ended by the key's release (the same note number; the log's
                                                   # times are rounded to 1 ms)
            lens.append((round((t - start.pop(n)) / sixteenth, 3), cut))
    rep['live']['lengths_in_steps'] = lens
    if any(abs(x / 0.5625 - 1) > 0.1 for x, cut in lens if not cut):
        errs.append('live: a note not 0.5625 step long')

    arp = notes(msgs, mk['4-live-done'], mk['6-arp-played'])      # the PNGs' times are rounded to 10 ms
    ao = ons(arp)
    rep['arp'] = {'events': arp, 'note_ons': ao}
    if [n for _, n in ao[:2]] != [0x3c, 0x48] or len(ao) != 2 or abs((ao[1][0] - ao[0][0]) / (sixteenth / 2) - 1) > 0.1:
        errs.append('arp: not the trig\'s note and its octave, 1/32 apart')

    stock = notes(msgs, mk['6-arp-played'], mk['8-stock-played'])
    so = ons(stock)
    offs = [t for t, n, v, _ in stock if not v]
    rep['stock'] = {'events': stock}
    if len(so) != 1 or not offs or abs((offs[0] - so[0][0]) / sixteenth - 1) > 0.1:
        errs.append('stock: not one note 1/16 long')

    rep['errors'] = errs
    rep['ok'] = not errs
    for k in ('live', 'arp', 'stock'):
        print(k, json.dumps(rep[k])[:600])
    print('ALL OK' if rep['ok'] else 'FAILED: ' + '; '.join(errs))
    if a.json:
        json.dump(rep, open(a.json, 'w'), indent=1)


if __name__ == '__main__':
    main()
