#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""T6.5: every comparison of a build's runs with FTN1's, in one summary.

    venv/bin/python scripts/t65_report.py [GT00 GT01 GT1T]  -> out/T6.5/compare/summary-NAME.txt

For each script run on both FTN1 and the build (out/T6.5/emu/{FTN1,NAME}/t65-*/): the screens
(scripts/t65_compare.py; for GT1T the flanger page's knob E/F boxes and the header may differ on the
SEND FX 3 screens), the working project image after the run (t65_after.py stored), and per script:
p-lock records (t61_locks.py against each build's own warm snapshot), MIDI out messages
(t65_midi_compare.py), the cache flush (t65_after.py flush; TEST/TST2 set aside for GT1T), the
live FX setup / mirror / frame / kit FX records (t65_after.py state), reads of the stock table
(t65_live.py --stock-reads). Missing runs are listed, not guessed.
"""
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EMU = os.path.join(ROOT, 'out', 'T6.5', 'emu')
OUT = os.path.join(ROOT, 'out', 'T6.5', 'compare')
PY = os.path.join(ROOT, 'venv', 'bin', 'python')
WARM = {'FTN1': os.path.join(ROOT, 'out', 'T5.0b', 'emu', 'FTN1', 'warm800M.snap')}
SCRIPTS = ['t65-tour', 't65-tour2', 't65-tour3', 't65-plock', 't65-lfo', 't65-midi-out', 't65-midi-out2', 't65-midi-flood',
           't65-storage', 't65-random', 't65-copypaste']
# GT1T: TEST and TST2 are drawn on the flanger page's knobs E and F (x 20..70, y 30..63, their values
# included) and their names and values show in the header (y 0..9) when they turn
FLANGER = '0,0,127,9;20,30,70,63'
GT1T_ALLOW = ['*send3*=' + FLANGER, '*flanger*=' + FLANGER]


def sh(args):
    r = subprocess.run(args, capture_output=True, text=True)
    return r.returncode, (r.stdout + r.stderr).strip()


def last(txt, pat):
    m = [l for l in txt.splitlines() if re.search(pat, l)]
    return m[-1] if m else ''


def report(name):
    L = ['%s against FTN1 (same keys, each from its own cold-booted warm snapshot)' % name]
    warm = os.path.join(EMU, name, 'warm800M.snap')
    for s in SCRIPTS:
        a, b = os.path.join(EMU, 'FTN1', s), os.path.join(EMU, name, s)
        if not (os.path.isdir(a) and os.path.isdir(b) and os.path.exists(os.path.join(b, 'end.snap'))
                and os.path.exists(os.path.join(a, 'end.snap'))):
            L.append('%-16s not run on both' % s)
            continue
        L.append('%s:' % s)
        args = [PY, os.path.join(ROOT, 'scripts', 't65_compare.py'), a, b,
                '--out', os.path.join(OUT, '%s-%s.screens.txt' % (s, name))]
        if name == 'GT1T':
            for al in GT1T_ALLOW:
                args += ['--allow', al]
        rc, t = sh(args)
        L.append('  screens: %s; %s' % (last(t, r'^screens:'), last(t, r'^verdict')))
        L += ['    ' + l for l in t.splitlines() if l.startswith('DIFF') or 'as allowed' in l][:12]
        rc, t = sh([PY, os.path.join(ROOT, 'scripts', 't65_after.py'), 'stored', os.path.join(a, 'end.snap'),
                    os.path.join(b, 'end.snap')])
        L.append('  working project image: %s' % t.splitlines()[0])
        L += ['    ' + l for l in t.splitlines()[1:8]]
        rl = os.path.join(b, 'run.log')
        if os.path.exists(rl):
            r = last(open(rl).read(), r'reads of the stock table')
            if r:
                L.append('  %s' % r.replace('[t65] ', ''))
        if s == 't65-plock':
            outs = []
            for nm, snap, base in (('FTN1', os.path.join(a, 'end.snap'), WARM['FTN1']),
                                   (name, os.path.join(b, 'end.snap'), warm)):
                rc, t = sh([PY, os.path.join(ROOT, 'scripts', 't61_locks.py'), snap, '--base', base])
                outs.append([l for l in t.splitlines()[1:] if l.startswith('  0x')])
            L.append('  lock records: FTN1 %d, %s %d, identical: %s' % (len(outs[0]), name, len(outs[1]),
                                                                       outs[0] == outs[1]))
        if s.startswith('t65-midi-out'):
            ma, mb = os.path.join(a, 'midi.txt'), os.path.join(b, 'midi.txt')
            if os.path.exists(ma) and os.path.exists(mb):
                rc, t = sh([PY, os.path.join(ROOT, 'scripts', 't65_midi_compare.py'), ma, mb,
                            '--out', os.path.join(OUT, '%s-%s.midi.txt' % (s, name))])
                L += ['  MIDI out: ' + l for l in t.splitlines()[:3]] + ['  MIDI out: ' + last(t, 'verdict')]
                L += ['  MIDI out: ' + l for l in t.splitlines() if l.startswith('only in')]
        if s == 't65-midi-flood':
            args = [PY, os.path.join(ROOT, 'scripts', 't65_after.py'), 'flush', os.path.join(a, 'flush.json'),
                    os.path.join(b, 'flush.json')]
            if name == 'GT1T':
                args += ['--ignore-rows', '0x17e,0x17f']
            rc, t = sh(args)
            L += ['  flush: ' + l for l in t.splitlines()]
            j = json.load(open(os.path.join(b, 't65.json')))
            c = j.get('cache', {})
            L.append('  cache: size 0x%x, %d writes inside, %d outside, %d sets, %d bad calls' % (
                c.get('size', 0), c.get('writes_in', 0), c.get('writes_out_n', 0), c.get('sets', 0),
                len(c.get('bad_calls', []))))
        if os.path.exists(os.path.join(a, 'state.json')) and os.path.exists(os.path.join(b, 'state.json')):
            rc, t = sh([PY, os.path.join(ROOT, 'scripts', 't65_after.py'), 'state', os.path.join(a, 'state.json'),
                        os.path.join(b, 'state.json')])
            L += ['  state: ' + l for l in t.splitlines()[:12]]
    txt = '\n'.join(L) + '\n'
    open(os.path.join(OUT, 'summary-%s.txt' % name), 'w').write(txt)
    return txt


def main():
    os.makedirs(OUT, exist_ok=True)
    for n in sys.argv[1:] or ['GT00', 'GT01', 'GT1T']:
        print(report(n))


if __name__ == '__main__':
    main()
