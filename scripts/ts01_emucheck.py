#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""TS01: read scripts/ts01_live.py's slide log (a block a line, from the emulated OS 1.12 playing TS01: after the
block handler's gate-offs, its events and ts_block) and check trig-slide on Elektron's own sequencer and handler
(notes/T2.9-trig-slides.md):

    python3 scripts/ts01_emucheck.py out/TS01/emu/ts01-play/slide.log [--json OUT]

  start    each slide (a run of blocks with ts_sl[t].on and the same t0, t1) starts in the block the track's last
           held voice ends (t0 = that block's clock), or goes on from the slide before it (a turn);
  line     every word that moves in a row the track owns lies, block by block, on the straight line from its value
           in the slide's first block to the value it ends on, within two units and a block's slope;
  arrival  the block after a slide ends, the next note starts (taken one block after the slide wrote its target),
           and the note's row holds, for every word that slid, the value the slide ended on: nothing left to change;
  quiet    outside slides and note starts, no word of a row the track owns changes.
"""
import argparse
import json


def parse(path):
    out = []
    for ln in open(path):
        p = ln.split()
        if len(p) < 8:
            continue
        ch = {}
        for tok in p[8:]:
            if '=' not in tok or tok.count(':') != 1 or tok.startswith(('ex=', 'nx=', 'v=', 'gate=', 'PROBE')):
                continue
            rk, v = tok.split('=')
            r, k = rk.split(':')
            if r.isdigit() and k.isdigit():
                ch[(int(r), int(k))] = int(v)
        step = int(p[5])
        out.append(dict(clock=int(p[0], 16), held=int(p[1], 16), on=int(p[2]), t0=int(p[3], 16), t1=int(p[4], 16),
                        step=step, n=int(p[6]), gen=int(p[7]), ch=ch))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('log')
    ap.add_argument('--json')
    a = ap.parse_args()
    recs = parse(a.log)
    per_block = (recs[1]['clock'] - recs[0]['clock']) & 0xffffffff if len(recs) > 1 else 0
    raw, problems, slides, notes = {}, [], [], []
    seg = None
    for i, b in enumerate(recs):
        before = dict(raw)
        raw.update(b['ch'])
        prev = recs[i - 1] if i else dict(held=0, on=0, t0=0, t1=0)
        new_voices = b['held'] & ~prev['held']
        changed = {rk: v for rk, v in b['ch'].items() if before.get(rk) != v and rk in before}
        if new_voices:
            notes.append(i)
            last = slides[-1] if slides else None
            if last and last['end'] is not None and last['end'] == i - 1:
                last['arrived_at'] = i
                for k, (seq, rows) in last['words'].items():
                    want = seq[-1][1]
                    for v in range(16):
                        if (new_voices >> v) & 1 and raw.get((v, k)) != want:
                            problems.append('block %d: the note in row %d starts with k %d = %s, the slide ended '
                                            'on %d' % (i, v, k, raw.get((v, k)), want))
            elif last and last['end'] is not None and last['end'] < i - 1 and last.get('arrived_at') is None \
                    and last['end'] >= i - 3:
                problems.append('block %d: a note started %d blocks after the slide ended (want 1)' % (
                    i, i - last['end']))
        if b['on']:
            if seg is None or (b['t0'], b['t1']) != (seg['t0'], seg['t1']):
                turned = seg is not None and seg['end'] is None
                if turned:
                    seg['end'] = i - 1
                    seg['turned'] = True
                if not turned and not (prev['held'] and not b['held']):
                    problems.append('block %d: a slide starts without the track\'s last voice ending' % i)
                if not turned and b['t0'] != b['clock']:
                    problems.append('block %d: a slide starts at t0 %08x, the block\'s clock is %08x' % (
                        i, b['t0'], b['clock']))
                seg = dict(start=i, end=None, t0=b['t0'], t1=b['t1'], step=b['step'], words={}, turned=False,
                           first={})
                slides.append(seg)
            for (r, k), v in b['ch'].items():
                seq, rows = seg['words'].setdefault(k, ([], set()))
                if r not in rows or not seq or seq[-1][0] != b['clock']:
                    seq.append((b['clock'], v))
                rows.add(r)
            for (r, k), v in changed.items():
                seg['first'].setdefault(k, before.get((r, k)))
        else:
            if seg is not None and seg['end'] is None:
                seg['end'] = i                     # the block it wrote its target in full
                for (r, k), v in b['ch'].items():
                    seq, rows = seg['words'].setdefault(k, ([], set()))
                    seq.append((b['clock'], v))
            elif changed and not new_voices:
                problems.append('block %d: words moved with no slide and no note starting: %s' % (
                    i, ' '.join('%d:%d=%d' % (r, k, v) for (r, k), v in sorted(changed.items())[:6])))
    # a slide refined when its target's exact event is queued (same step, t0 now) is one line from its first start
    merged = []
    for s in slides:
        m = merged[-1] if merged else None
        if m is not None and m['turned'] and m['end'] == s['start'] - 1 and m['step'] == s['step']:
            if m['t1'] != s['t1']:
                problems.append('block %d: refined toward step %d, the arrival moved from %08x to %08x (the '
                                'look-ahead\'s estimate was off by %d blocks)' % (
                                    s['start'], s['step'], m['t1'], s['t1'],
                                    ((s['t1'] - m['t1']) & 0xffffffff) // per_block if per_block else 0))
            for k, (seq, rows) in s['words'].items():
                mseq, mrows = m['words'].setdefault(k, ([], set()))
                mseq.extend(seq)
                mrows.update(rows)
                m['first'].setdefault(k, s['first'].get(k))
            m.update(end=s['end'], turned=s['turned'], t1=s['t1'], refined=True)
            if s.get('arrived_at'):
                m['arrived_at'] = s['arrived_at']
            continue
        merged.append(s)
    slides = merged
    report = []
    for s in slides:
        worst = 0.0
        span = (s['t1'] - s['t0']) & 0xffffffff
        for k, (seq, rows) in s['words'].items():
            if not seq:
                continue
            v0 = s['first'].get(k)
            v1 = seq[-1][1]
            if v0 is None or span == 0:
                continue
            slope = abs(v1 - v0) * per_block / span
            for c, v in seq[:-1]:
                want = v0 + (v1 - v0) * (((c - s['t0']) & 0xffffffff) / span)
                worst = max(worst, abs(v - want) - slope - 2)
        r = dict(start=s['start'], end=s['end'], blocks=(s['end'] - s['start'] + 1) if s['end'] is not None else None,
                 toward_step=s['step'], span_blocks=round(span / per_block, 1) if per_block else None,
                 words={str(k): [s['first'].get(k), seq[-1][1]] for k, (seq, rows) in s['words'].items()},
                 turned=s['turned'], refined=bool(s.get('refined')), arrived_at=s.get('arrived_at'),
                 worst_beyond_tolerance=round(worst, 2))
        report.append(r)
        if worst > 0:
            problems.append('slide at block %d: %.2f off its line beyond the tolerance' % (s['start'], worst))
    for r in report:
        print('slide blocks %s-%s toward step %d over %s blocks: %s%s; %s; off the line %.2f beyond tolerance' % (
            r['start'], r['end'], r['toward_step'], r['span_blocks'],
            ', '.join('k %s %s -> %s' % (k, v[0], v[1]) for k, v in sorted(r['words'].items(), key=lambda x: int(x[0]))),
            (' (then turned)' if r['turned'] else '') + (' (refined when its trig was queued)' if r['refined'] else ''),
            'the note started in the next block' if r['arrived_at'] else 'no note after it in the log',
            max(0, r['worst_beyond_tolerance'])))
    print('%d blocks, %d notes started, %d slides, %d problems' % (len(recs), len(notes), len(slides), len(problems)))
    for p in problems[:30]:
        print('  ' + p)
    if a.json:
        json.dump(dict(blocks=len(recs), notes=notes, slides=report, problems=problems), open(a.json, 'w'), indent=1)
    return 1 if problems or not slides else 0


if __name__ == '__main__':
    raise SystemExit(main())
