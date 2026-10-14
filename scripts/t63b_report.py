#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""T6.3 CMP1: read the emulator runs of CMP1's main OS (C1EM) and GT01 and check what each proves.

    venv/bin/python scripts/t63b_report.py [SECTION ...]  -> out/T6.3/CMP1/emu/report.json, report.txt

Sections (each reads runs under out/T6.3/CMP1/emu/{C1EM,GT01}/<script>/, written by
scripts/t63b-emu.sh with scripts/t63b_live.py; a missing run is reported as such, not guessed):
  boot     the cold boot: C1EM's task creations against GT01's (scripts/t65-emu.sh / t63b-emu.sh
           ladder logs), the main screen pixel for pixel
  pages    t63b-pages7 (C1EM) and t63b-pages5 (GT01): the MIXER view's list, every page's title,
           box and knob labels, every knob's header ("Name=value"), HIGH's F, UP/DOWN at the ends
           and FUNC+MOD stepping and wrapping; INT MIX 1/2, FX MIX and EXT MIX 1 pixel for pixel
           against GT01's (title digits and the page marker may differ: listed)
  values   t63b-values: RAT 0..8 texts, the crossovers' end and default texts, CLEAR PAGE defaults
  dual     t63b-dual, t63b-dual2: DUAL on/off through the UI, the list and titles, SCS up to In R
  copy     t63b-copy: page copy/clear/paste popups and the band words before/after
  meters   t63b-meters7 runs with faked reply half-words: each page's bar
  plock    t63b-plock: lock records (scripts/t61_locks.py against the warm snapshot)
  t65      GT01's t65-tour3, t65-plock, t65-midi-flood on C1EM against GT01's own runs (T6.5's)
"""
import glob
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scripts'))
from t50b_compare import screen, boxes          # noqa: E402

EMU = os.path.join(ROOT, 'out', 'T6.3', 'CMP1', 'emu')
GT01_EMU = os.path.join(ROOT, 'out', 'T6.5', 'emu', 'GT01')
PY = os.path.join(ROOT, 'venv', 'bin', 'python')
TITLE_FONT, LABEL_FONT, BOX_FONT = '0x40290dd0', '0x40291ca0', '0x402906b4'
PAGES7 = [(40, 'Compressor Low'), (41, 'Compressor Mid'), (42, 'Compressor High'), (21, 'Internal Mixer'),
          (22, 'Internal Mixer'), (23, 'FX Mixer'), (24, 'External Mixer')]
BAND_LABELS = {40: ['THR', 'ATK', 'REL', 'MUP', 'RAT', 'XOVR', 'SCS', 'MIX', 'VOL'],
               41: ['THR', 'ATK', 'REL', 'MUP', 'RAT', 'XOVR', 'SCS', 'MIX', 'VOL'],
               42: ['THR', 'ATK', 'REL', 'MUP', 'RAT', 'SCS', 'MIX', 'VOL']}
BAND_HEADERS = {40: ['Low Threshold', 'Low Attack', 'Low Release', 'Low Makeup', 'Low Ratio', 'Low/Mid X-Over',
                     'Sidechain Src', 'Dry/Wet Mix', 'Pattern Volume'],
                41: ['Mid Threshold', 'Mid Attack', 'Mid Release', 'Mid Makeup', 'Mid Ratio', 'Mid/High X-Over',
                     'Sidechain Src', 'Dry/Wet Mix', 'Pattern Volume'],
                42: ['High Threshold', 'High Attack', 'High Release', 'High Makeup', 'High Ratio', None,
                     'Sidechain Src', 'Dry/Wet Mix', 'Pattern Volume']}
BOX = {40: ['CMP', 'LOW'], 41: ['CMP', 'MID'], 42: ['CMP', 'HI']}
R = {}
L = []


def run_dir(name, script):
    return os.path.join(EMU, name, script)


def load(name, script):
    p = os.path.join(run_dir(name, script), 't63b.json')
    return json.load(open(p)) if os.path.exists(p) else None


def texts(s, font=None):
    return [t[3] for t in s.get('texts', []) if font is None or t[0] == font]


def title(s):
    t = texts(s, TITLE_FONT)
    return t[-1] if t else None


def box(s):
    """the page box's texts (x < 15, y 27..42 y-up; the track box above it says SYN at y 43)"""
    return [t[3] for t in s.get('texts', []) if t[0] == BOX_FONT and t[1] < 15 and 26 < t[2] < 43]


def labels(s):
    return [t[3] for t in s.get('texts', []) if t[0] == LABEL_FONT]


def say(*a):
    line = ' '.join(str(x) for x in a)
    L.append(line)
    print(line)


def check(key, ok, detail=''):
    R.setdefault('checks', {})[key] = bool(ok)
    say('  [%s] %s%s' % ('yes' if ok else 'NO', key, (': ' + detail) if detail else ''))
    return ok


def diffboxes(a_txt, b_txt):
    a, b = screen(a_txt), screen(b_txt)
    d = [[a[y][x] ^ b[y][x] for x in range(128)] for y in range(64)]
    return [bx[:4] + [bx[4]] for bx in boxes(d)]


# ---------------------------------------------------------------------------------------------
def sec_boot():
    say('== boot')
    def tasks(p):
        out = []
        for l in open(p):
            m = re.search(r'\[n=(\d+)\] TASK_CREATE site (\S+) -> entry=(\S+) prio=(\d+) tcb=(\S+) stack=(\S+) size=(\S+)', l)
            if m:
                out.append((int(m.group(1)),) + m.groups()[1:])
        return out
    a = tasks(os.path.join(GT01_EMU, 'checkpoint.log'))
    b_p = os.path.join(EMU, 'C1EM', 'checkpoint.log')
    if not os.path.exists(b_p):
        say('  C1EM ladder not run')
        return
    b = tasks(b_p)
    same = [x[1:] for x in a] == [x[1:] for x in b]
    deltas = sorted({y[0] - x[0] for x, y in zip(a, b)})
    check('cold boot to 400M: the same %d task creations as GT01 (site, entry, priority, TCB, stack, size)' % len(b),
          same and len(a) == len(b) == 8, 'each later by %s instructions' % deltas)
    R['boot'] = {'tasks_gt01': a, 'tasks_c1em': b, 'deltas': deltas}
    for nm, p in (('GT01', os.path.join(GT01_EMU, 'guirun-warm.log')), ('C1EM', os.path.join(EMU, 'C1EM', 'guirun-warm.log'))):
        t = open(p).read() if os.path.exists(p) else ''
        m = re.findall(r'\[guirun\] end: instrs=\S+ terminal=(\S+) tasks=(\d+) .*?mainloop=(\d+)', t)
        f = re.findall(r'\[guirun\] faults: (\d+)', t)
        R.setdefault('guirun', {})[nm] = {'end': m[-1] if m else None, 'faults': f[-1] if f else None}
    g, c = R['guirun']['GT01'], R['guirun']['C1EM']
    check('400M -> 800M (guirun): not terminal, the same tasks, UI main loop runs, no fault',
          c['end'] and c['end'][0] == 'False' and g['end'] and c['end'][1] == g['end'][1] and c['faults'] == '0',
          'GT01 %s, C1EM %s' % (g, c))
    a0, b0 = run_dir('GT01', 't63b-pages5'), run_dir('C1EM', 't63b-pages7')
    if os.path.exists(os.path.join(a0, '00-main.txt')) and os.path.exists(os.path.join(b0, '00-main.txt')):
        bx = diffboxes(os.path.join(a0, '00-main.txt'), os.path.join(b0, '00-main.txt'))
        check('the main screen (warm 800M snapshot, first screen of the tour) is GT01\'s pixel for pixel', not bx, str(bx))


def sec_pages():
    say('== pages')
    c = load('C1EM', 't63b-pages7')
    g = load('GT01', 't63b-pages5')
    if not c:
        say('  t63b-pages7 not run on C1EM')
        return
    lists = {k: tuple(s['mixer']['pages']) for k, s in c.items() if not k.startswith('__') and s.get('mixer')}
    other = {k: v for k, v in lists.items() if v != (40, 41, 42, 21, 22, 23, 24)}
    check('the MIXER view\'s page list is {40, 41, 42, 21, 22, 23, 24} on every screen but EXT MIX 1\'s B turn '
          '(DUAL on for 0.3 s: EXT MIX 2 inserted after EXT MIX 1, as GT01 does at its p5-B)',
          set(other) <= {'p7-B'} and set(other.values()) <= {(40, 41, 42, 21, 22, 23, 24, 25)}, str(other))
    rows = []
    for p in range(1, 8):
        pid, name = PAGES7[p - 1]
        s = c.get('p%d' % p)
        if not s:
            check('page %d captured' % p, False)
            continue
        t = title(s)
        rows.append({'page': p, 'id': s['mixer']['page'], 'title': t, 'box': box(s), 'labels': labels(s)})
        check('page %d is id %d, title "%s (%d/7)"' % (p, pid, name, p), s['mixer']['page'] == pid and t == '%s (%d/7)' % (name, p),
              '%s, "%s"' % (s['mixer']['page'], t))
        if pid in BOX:
            check('page %d box %s, labels %s' % (p, '/'.join(BOX[pid]), ' '.join(BAND_LABELS[pid])),
                  box(s) == BOX[pid] and sorted(labels(s)) == sorted(BAND_LABELS[pid]), '%s %s' % (box(s), labels(s)))
            heads = []
            for i, kn in enumerate('ABCDEFGHL'):
                h = title(c.get('p%d-%s' % (p, kn), {}))
                heads.append(h)
                want = BAND_HEADERS[pid][i]
                if want is None:
                    k0 = c.get('p%d-E' % p, {}).get('k70_86')
                    k1 = c.get('p%d-%s' % (p, kn), {}).get('k70_86')
                    check('page %d knob %s (empty box): a turn shows no header of its own and changes no band word'
                          % (p, kn), h is not None and 'X-Over' not in h and k0 is not None and k0 == k1,
                          'header still %s (the last one shown), k70..86 %s' % (h, 'unchanged' if k0 == k1 else (k0, k1)))
                else:
                    check('page %d knob %s header "%s=..."' % (p, kn, want), h is not None and h.startswith(want + '='), str(h))
            R.setdefault('headers', {})[pid] = heads
    say('  pages: ' + json.dumps(rows))
    # UP/DOWN at the ends and the page key, against GT01's behaviour
    for nm, run, n in (('C1EM', c, 7), ('GT01', g, 5)):
        if not run:
            continue
        seq = [run.get('key-%d' % i, {}).get('mixer', {}) for i in range(1, n + 2)]
        R.setdefault('pagekey', {})[nm] = [x.get('index') for x in seq]
        R.setdefault('ends', {})[nm] = {k: (run.get(k, {}).get('mixer') or {}).get('index') for k in ('wrap-down', 'wrap-up', 'wrap-up-down')}
    pk = R['pagekey']['C1EM']
    check('FUNC+MOD steps through the 7 pages and wraps (indices after each press)', pk == [0, 1, 2, 3, 4, 5, 6, 0], str(pk))
    if 'GT01' in R['pagekey']:
        check('GT01: FUNC+MOD steps through its 5 and wraps the same way', R['pagekey']['GT01'] == [0, 1, 2, 3, 4, 0],
              str(R['pagekey']['GT01']))
        e1, e0 = R['ends']['C1EM'], R['ends']['GT01']
        check('DOWN on the last page stays there, UP goes back one (as GT01: UP/DOWN do not wrap)',
              e1 == {'wrap-down': 6, 'wrap-up': 5, 'wrap-up-down': 6} and e0 == {'wrap-down': 4, 'wrap-up': 3, 'wrap-up-down': 4},
              'C1EM %s, GT01 %s' % (e1, e0))
    # the stock pages against GT01's, pixel for pixel
    if g:
        pairs = {21: (2, 4), 22: (3, 5), 23: (4, 6), 24: (5, 7)}
        allowed_all, detail = True, {}
        for pid, (pg, pc) in pairs.items():
            for suf in [''] + ['-%s' % k for k in 'ABCDEFGHL'] + ['-back']:
                a = os.path.join(run_dir('GT01', 't63b-pages5'), 'p%d%s.txt' % (pg, suf))
                b = os.path.join(run_dir('C1EM', 't63b-pages7'), 'p%d%s.txt' % (pc, suf))
                if not (os.path.exists(a) and os.path.exists(b)):
                    continue
                bx = diffboxes(a, b)
                ta, tb = title(g.get('p%d%s' % (pg, suf), {})), title(c.get('p%d%s' % (pc, suf), {}))
                detail['%d%s' % (pid, suf)] = {'boxes': bx, 'title': [ta, tb]}
        R['stock_pages_vs_gt01'] = detail
        say('  stock pages vs GT01 (page id + knob: differing boxes x0,y0,x1,y1,n; titles):')
        unexplained = []
        for k, v in detail.items():
            say('    %-8s %s %s' % (k, v['boxes'], v['title']))
            pid = int(k[:2])
            suf = k[2:]
            pg, pc = pairs[pid]
            la = [t[1:] for t in g.get('p%d%s' % (pg, suf), {}).get('texts', []) if t[0] == LABEL_FONT]
            lb = [t[1:] for t in c.get('p%d%s' % (pc, suf), {}).get('texts', []) if t[0] == LABEL_FONT]
            for bx in v['boxes']:
                if bx[3] <= 9:            # the header line: the title's digits, or the header mid-change
                    continue
                if bx[0] >= 125:          # the page marker at the right edge (5 vs 7 pages)
                    continue
                if la != lb:              # a knob's label in one run, its value in the other (input timing)
                    continue
                unexplained.append((k, bx))
            v['labels_differ'] = la != lb
        check('INT MIX 1, INT MIX 2, FX MIX and EXT MIX 1 (every knob screen) against GT01: every differing pixel is in '
              'the header line, the page marker (x 125..127), or a knob cell where the text trace shows one run '
              'drawing the label and the other the value (input timing)', not unexplained, str(unexplained))
        backs = {k: v['boxes'] for k, v in detail.items() if k.endswith(('-back',)) or k in ('21', '22', '23', '24')}
        check('their settled screens (each page on arrival, and 3 s after the turns) differ only in the title '
              'digits and the page marker', all(all(b[3] <= 9 or b[0] >= 125 for b in bx) for bx in backs.values()),
              str(backs))



def heads(run, prefix):
    return [(k, title(v)) for k, v in run.items() if k.startswith(prefix) and not k.startswith('__')]


def sec_values():
    say('== values')
    v = load('C1EM', 't63b-values')
    if not v:
        say('  t63b-values not run')
        return
    rat = [title(x) for k, x in v.items() if k.startswith(('v01-rat', 'v02-rat', 'v03-rat'))]
    seen = []
    for t in rat:
        t = (t or '').split('=', 1)[-1]
        if not seen or seen[-1] != t:
            seen.append(t)
    R['rat_sequence'] = rat
    want = ['1.00', '1.50', '2.00', '3.00', '4.00', '6.00', '8.00', '16.00', '20.00']
    down_to = [t for t in rat if t == 'Low Ratio=1.00']
    check('RAT (LOW E) down to 0 shows "Low Ratio=1.00" and stays there', len(down_to) >= 2, str(rat[:8]))
    up = [(title(x) or '').split('=', 1)[-1] for k, x in v.items() if k.startswith('v03-rat')]
    upseq = [t for i, t in enumerate(up) if i == 0 or up[i - 1] != t]
    check('RAT up from 0: 1.50 2.00 3.00 4.00 6.00 8.00 16.00 20.00, then stays at 20.00',
          upseq == want[1:] and up[-1] == '20.00', str(up))
    kv = v.get('v03-rat-up10', {}).get('k70_86', [None, None])
    check('k 71 (Low Ratio) holds 8 at the top', kv[1] == 8, str(kv))
    exp = {'v04-xlo-bottom': 'Low/Mid X-Over=30Hz', 'v04-xlo-bottom-up1': 'Low/Mid X-Over=31Hz',
           'v04-xlo-bottom-again': 'Low/Mid X-Over=30Hz', 'v05-xlo-top': 'Low/Mid X-Over=1.0k',
           'v05-xlo-top-down1': 'Low/Mid X-Over=973Hz', 'v05-xlo-top-again': 'Low/Mid X-Over=1.0k',
           'v07-xhi-bottom': 'Mid/High X-Over=1.0k', 'v07-xhi-bottom-up1': 'Mid/High X-Over=1.0k',
           'v07-xhi-bottom-again': 'Mid/High X-Over=1.0k', 'v08-xhi-top': 'Mid/High X-Over=16.0k',
           'v08-xhi-top-down1': 'Mid/High X-Over=15.7k', 'v08-xhi-top-again': 'Mid/High X-Over=16.0k'}
    got = {k: title(v.get(k, {})) for k in exp}
    R['xover_ends'] = got
    check('the crossovers\' texts at the ends (30Hz, 31Hz, 1.0k, 973Hz; 1.0k, 16.0k, 15.7k)', got == exp,
          str({k: g for k, g in got.items() if g != exp[k]}))
    kx = (v.get('v05-xlo-top', {}).get('k70_86') or [0] * 17)
    ky = (v.get('v08-xhi-top', {}).get('k70_86') or [0] * 17)
    check('k 75 = 127 and k 81 = 127 at the top ends', kx[5] == 127 and ky[11] == 127, '%s %s' % (kx[5], ky[11]))
    hf = [(k, title(v.get(k, {})), (v.get(k, {}).get('k70_86') or [None] * 17)) for k in
          ('v20-high', 'v21-high-F-up3', 'v22-high-F-down3')]
    check('HIGH F (the empty box): three turns each way change no header and no band word',
          all(t == 'Compressor High (3/7)' for _, t, _ in hf) and hf[0][2] == hf[1][2] == hf[2][2],
          str([(k, t) for k, t, _ in hf]))
    check('HIGH D turns: "High Makeup=..."', (title(v.get('v23-high-D-up', {})) or '').startswith('High Makeup='),
          str(title(v.get('v23-high-D-up', {}))))


def sec_dual():
    say('== dual')
    d = load('C1EM', 't63b-dual')
    e = load('C1EM', 't63b-dual2')
    if not d:
        say('  t63b-dual not run')
        return
    def pg(x):
        m = x.get('mixer') or {}
        return m.get('pages'), m.get('index'), m.get('page'), title(x)
    rows = {k: pg(x) for k, x in d.items() if not k.startswith('__')}
    R['dual'] = rows
    l7, l8 = [40, 41, 42, 21, 22, 23, 24], [40, 41, 42, 21, 22, 23, 24, 25]
    check('DOWN x 6 from LOW reaches EXT MIX 1 (7/7)', rows.get('d01-extmix1', (0,))[1:] == (6, 24, 'External Mixer (7/7)'),
          str(rows.get('d01-extmix1')))
    check('DUAL turned on through the UI (EXT MIX 1 knob B): "Dual Mono=ON", EXT MIX 2 (25) inserted after EXT MIX 1',
          rows.get('d02-dual-on', [None])[0] == l8 and rows['d02-dual-on'][3] == 'Dual Mono=ON', str(rows.get('d02-dual-on')))
    check('DOWN from EXT MIX 1 shows EXT MIX 2 as "External Mixer (8/8)"',
          rows.get('d10-down', (0,))[1:] == (7, 25, 'External Mixer (8/8)'), str(rows.get('d10-down')))
    keys = [rows.get(k) for k in sorted(rows) if 'pagekey' in k]
    want = [(0, 40, 'Compressor Low (1/8)'), (1, 41, 'Compressor Mid (2/8)'), (2, 42, 'Compressor High (3/8)'),
            (3, 21, 'Internal Mixer (4/8)'), (4, 22, 'Internal Mixer (5/8)'), (5, 23, 'FX Mixer (6/8)'),
            (6, 24, 'External Mixer (7/8)'), (7, 25, 'External Mixer (8/8)')]
    check('FUNC+MOD from EXT MIX 2 (the last) wraps to LOW, then steps through all 8 pages, titles (x/8)',
          [k[1:] if k else None for k in keys] == want, str([k[1:] if k else None for k in keys]))
    if not e:
        say('  t63b-dual2 not run')
        return
    rows2 = {k: pg(x) for k, x in e.items() if not k.startswith('__')}
    R['dual2'] = rows2
    scs = [rows2[k][3] for k in sorted(rows2) if k.startswith('e01')]
    R['scs_sequence'] = scs
    check('LOW G (SCS) in DUAL mode goes up to "Sidechain Src=In R" (row 0x9c) and stops there',
          scs[-1] == 'Sidechain Src=In R' and 'Sidechain Src=In L' in scs and 'Sidechain Src=In LR' not in scs, str(scs))
    kv = e.get('e01-scs-up21', {}).get('k60_69', [None] * 10)
    check('k 65 (SCS) = 19 at the top', kv[5] == 19, str(kv))
    check('SCS back to 0', (rows2.get('e02-scs-back0') or [0, 0, 0, ''])[3].startswith('Sidechain Src='), str(rows2.get('e02-scs-back0')))
    check('DUAL off on EXT MIX 1: EXT MIX 2 erased, 7 pages', rows2.get('e05-dual-off', [None])[0] == l7
          and rows2['e05-dual-off'][3] == 'Dual Mono=OFF', str(rows2.get('e05-dual-off')))
    check('then FUNC+MOD wraps to "Compressor Low (1/7)"', rows2.get('e08-pagekey', (0,))[1:] == (0, 40, 'Compressor Low (1/7)'),
          str(rows2.get('e08-pagekey')))


def sec_defaults():
    say('== defaults (t63b-defaults, run with --set-k: k 70..86 = the values CLEAR PAGE sets)')
    v = load('C1EM', 't63b-defaults')
    if not v:
        say('  t63b-defaults not run')
        return
    defaults = {'low': ['Low Threshold=32', 'Low Attack=24', 'Low Release=32', 'Low Makeup=12.1dB', 'Low Ratio=4.00',
                        'Low/Mid X-Over=196Hz'],
                'mid': ['Mid Threshold=32', 'Mid Attack=24', 'Mid Release=32', 'Mid Makeup=12.1dB', 'Mid Ratio=4.00',
                        'Mid/High X-Over=2.6k'],
                'high': ['High Threshold=32', 'High Attack=24', 'High Release=32', 'High Makeup=12.1dB',
                         'High Ratio=4.00', None]}
    ups = {'low': ['Low Threshold=33', 'Low Attack=25', 'Low Release=33', 'Low Makeup=12.3dB', 'Low Ratio=6.00',
                   'Low/Mid X-Over=202Hz'],
           'mid': ['Mid Threshold=33', 'Mid Attack=25', 'Mid Release=33', 'Mid Makeup=12.3dB', 'Mid Ratio=6.00',
                   'Mid/High X-Over=2.7k'],
           'high': ['High Threshold=33', 'High Attack=25', 'High Release=33', 'High Makeup=12.3dB',
                    'High Ratio=6.00', None]}
    for p, band in ((1, 'low'), (2, 'mid'), (3, 'high')):
        d = [title(v.get('f%d-%s-%s-default' % (p, band, kn), {})) for kn in 'ABCDEF']
        u = [title(v.get('f%d-%s-%s-up' % (p, band, kn), {})) for kn in 'ABCDEF']
        if band == 'high':
            d[5] = u[5] = None                      # F is the empty box
        R.setdefault('defaults', {})[band] = {'default': d, 'up': u}
        check('%s at the defaults: each band knob up one and back, the header shows the default' % band.upper(),
              d == defaults[band], str(d))
        check('  ... and one step above it', u == ups[band], str(u))
    k = v.get('f1-low', {}).get('k70_86')
    check('the live words k 70..86 were the documented defaults', k == [32, 4, 24, 32, 64, 68, 32, 4, 24, 32, 64, 44,
                                                                       32, 4, 24, 32, 64], str(k))


def sec_pagecopy():
    say('== pagecopy (page copy/clear/paste, host-called on the OS\'s own MasterPageView::vslot53/55/54)')
    for nm in ('GT01', 'C1EM'):
        p = os.path.join(EMU, nm, 'pagecopy-warm.json')
        if not os.path.exists(p):
            say('  %s: not run' % nm)
            continue
        j = json.load(open(p))
        R.setdefault('pagecopy', {})[nm] = j
        for pg in j['pages']:
            check('%s page %d (id %d): "%s" / "%s" / "%s"; clear -> the rows\' defaults, paste -> the copied values'
                  % (nm, pg['index'], pg['page'], pg['copy_popup'], pg['clear_popup'], pg['paste_popup']), pg['ok'],
                  'k %s: before %s, changed %s, cleared %s, pasted %s' % (pg['k'], pg['before'], pg['changed'],
                                                                         pg['cleared'], pg['pasted']))
    c = load('C1EM', 't63b-copy')
    if c:
        pops = {k: title(x) for k, x in c.items() if k in ('c01-low-copied', 'c03-low-cleared', 'c04-low-pasted')}
        say('  by keys, FUNC+REC/PLAY/STOP on a band page: %s (the pattern, as on GT01\'s pages: '
            'out/T6.5/emu/GT01/t65-copypaste)' % pops)
        R['copy_keys'] = pops


def screen_of(name, script, png):
    p = os.path.join(run_dir(name, script), png + '.txt')
    return p if os.path.exists(p) else None


def sec_meters():
    say('== meters')
    runs = {k: load('C1EM', k) for k in ('meters-a', 'meters-b', 'meters-c')}
    if not all(runs.values()):
        say('  meters runs missing: %s' % [k for k, v in runs.items() if not v])
        return
    fake = {'meters-a': (9000, 16000, 4000), 'meters-b': (32767, 655, 654), 'meters-c': (0, 0, 0)}
    for run in ('meters-a', 'meters-b'):
        for p in range(1, 8):
            a = screen_of('C1EM', run, 'm%d' % p)
            z = screen_of('C1EM', 'meters-c', 'm%d' % p)
            bx = diffboxes(z, a)
            s = runs[run]['m%d' % p]
            pid = s['mixer']['page']
            if pid in (40, 41, 42):
                q = fake[run][pid - 40]
                h = q // 655
                want = [[18, 13, 19, 12 + h, 2 * h]] if h > 0 else []
                check('%s page %d (id %d): half-word %d = %d -> bar %d px (stock law q/655), x 18-19 from row 13 down'
                      % (run, p, pid, 8 + pid - 40, q, h), bx == want and s.get('compui_bar', [None] * 3)[pid - 40] == h,
                      'diff boxes %s, compui_bar %s' % (bx, s.get('compui_bar')))
            else:
                check('%s page %d (id %d, stock): no bar, identical to the run with zeros' % (run, p, pid), not bx, str(bx))
    hw = runs['meters-c']['m1'].get('rx_hw8_11')
    say('  meters-c (half-words 8..10 = 0, 11 = 32767): band pages without a bar: %s' % hw)


def sec_plock():
    say('== plock')
    d = run_dir('C1EM', 't63b-plock')
    if not os.path.exists(os.path.join(d, 'end.snap')):
        say('  t63b-plock not run')
        return
    r = subprocess.run([PY, os.path.join(ROOT, 'scripts', 't61_locks.py'), os.path.join(d, 'end.snap'), '--base',
                        os.path.join(EMU, 'C1EM', 'warm800M.snap')], capture_output=True, text=True)
    recs = [l for l in r.stdout.splitlines() if l.startswith('  0x')]
    R['plock'] = r.stdout.splitlines()
    say('  ' + '\n  '.join(r.stdout.splitlines()[:12]))
    band = [l for l in recs if re.search(r'k (7\d|8[0-6])\b', l)]
    check('band knobs turned with a trig held in grid recording: no lock record for k 70..86', not band, str(band))
    pl = load('C1EM', 't63b-plock')
    if pl:
        a, b = pl.get('l01-low', {}).get('k70_86'), pl.get('l05-end', {}).get('k70_86')
        say('  band words before/after: %s / %s' % (a, b))


def sec_ob8():
    say('== ob8 (forced: an Outbox 8 on USB is not modelled; --force-ob8)')
    for nm, n in (('GT01', 5), ('C1EM', 7)):
        o = load(nm, 't63b-ob8')
        if not o:
            say('  %s not run' % nm)
            continue
        rows = [(k, x['mixer']['pages'], x['mixer']['page'], title(x)) for k, x in o.items() if not k.startswith('__')]
        R.setdefault('ob8', {})[nm] = rows
        for r_ in rows:
            say('    %s %s' % (nm, r_))
    o = R.get('ob8', {}).get('C1EM')
    if o:
        d = {k: (p, pid, t) for k, p, pid, t in o}
        base = [40, 41, 42, 21, 22, 23, 24]
        check('Outbox 8 is the last page, "Outbox 8 (8/8)"', d['o01-last'][1:] == (26, 'Outbox 8 (8/8)'), str(d['o01-last']))
        check('DUAL on with Outbox 8 present: EXT MIX 2 lands between EXT MIX 1 and Outbox 8',
              d['o03-dual-on'][0] == base + [25, 26] and d['o04-next'][1:] == (25, 'External Mixer (8/9)')
              and d['o05-next'][1:] == (26, 'Outbox 8 (9/9)'), '%s %s %s' % (d['o03-dual-on'], d['o04-next'], d['o05-next']))
        check('DUAL off: EXT MIX 2 erased, Outbox 8 stays last', d['o07-dual-off'][0] == base + [26]
              and d['o08-next'][1:] == (26, 'Outbox 8 (8/8)'), '%s %s' % (d['o07-dual-off'], d['o08-next']))



def sh(args):
    r = subprocess.run(args, capture_output=True, text=True)
    return r.returncode, (r.stdout + r.stderr).strip()


def sec_t65():
    say('== t65 (GT01\'s scripts on C1EM against GT01\'s own runs, out/T6.5/emu/GT01)')
    warm_c, warm_g = os.path.join(EMU, 'C1EM', 'warm800M.snap'), os.path.join(GT01_EMU, 'warm800M.snap')
    for sname in ('t65-tour3', 't65-plock', 't65-midi-flood', 't65-storage'):
        a, b = os.path.join(GT01_EMU, sname), run_dir('C1EM', sname)
        if not (os.path.exists(os.path.join(a, 'end.snap')) and os.path.exists(os.path.join(b, 'end.snap'))):
            say('  %s: not run on both' % sname)
            continue
        say('  %s:' % sname)
        out = os.path.join(EMU, 'compare-%s.txt' % sname)
        rc, t = sh([PY, os.path.join(ROOT, 'scripts', 't65_compare.py'), a, b, '--out', out,
                    '--allow', '*mixer*=0,0,127,63'])
        lines = t.splitlines()
        diff = [l for l in lines if l.startswith('DIFF')]
        R.setdefault('t65', {})[sname] = {'screens': [l for l in lines if l.startswith('screens')], 'diff': diff}
        say('    screens (the MIXER pages allowed to differ): %s' % ' / '.join(l for l in lines if l.startswith(('screens', 'verdict'))))
        for l in diff[:12]:
            say('      ' + l)
        rc, t = sh([PY, os.path.join(ROOT, 'scripts', 't65_after.py'), 'stored', os.path.join(a, 'end.snap'),
                    os.path.join(b, 'end.snap'), '--base', warm_g])
        R['t65'][sname]['stored'] = t.splitlines()[:10]
        say('    working project image (bytes either run wrote): %s' % t.splitlines()[0])
        for l in t.splitlines()[1:6]:
            say('      ' + l)
        rl = os.path.join(b, 'run.log')
        if os.path.exists(rl):
            m = [l for l in open(rl) if 'reads of the stock table' in l]
            if m:
                say('    ' + m[-1].strip().replace('[t65] ', ''))
                R['t65'][sname]['stock_reads'] = m[-1].strip()
        if sname == 't65-plock':
            outs = []
            for snap, base in ((os.path.join(a, 'end.snap'), warm_g), (os.path.join(b, 'end.snap'), warm_c)):
                rc, t = sh([PY, os.path.join(ROOT, 'scripts', 't61_locks.py'), snap, '--base', base])
                outs.append([l for l in t.splitlines()[1:] if l.startswith('  0x')])
            R['t65'][sname]['locks'] = outs
            check('t65-plock: the same lock records as GT01 (%d)' % len(outs[0]), outs[0] == outs[1],
                  'GT01 %d, C1EM %d' % (len(outs[0]), len(outs[1])))
        if sname == 't65-midi-flood':
            rc, t = sh([PY, os.path.join(ROOT, 'scripts', 't65_after.py'), 'flush', os.path.join(a, 'flush.json'),
                        os.path.join(b, 'flush.json')])
            R['t65'][sname]['flush'] = t.splitlines()
            for l in t.splitlines():
                say('    flush: ' + l)
            j = json.load(open(os.path.join(b, 't65.json')))
            cc = j.get('cache', {})
            say('    cache: size 0x%x, %d writes inside, %d outside, %d sets, %d bad calls' % (
                cc.get('size', 0), cc.get('writes_in', 0), cc.get('writes_out_n', 0), cc.get('sets', 0),
                len(cc.get('bad_calls', []))))
            R['t65'][sname]['cache'] = {k: cc.get(k) for k in ('size', 'writes_in', 'writes_out_n', 'sets')}
        if os.path.exists(os.path.join(a, 'state.json')) and os.path.exists(os.path.join(b, 'state.json')):
            rc, t = sh([PY, os.path.join(ROOT, 'scripts', 't65_after.py'), 'state', os.path.join(a, 'state.json'),
                        os.path.join(b, 'state.json')])
            R['t65'][sname]['state'] = t.splitlines()[:14]
            for l in t.splitlines()[:8]:
                say('    state: ' + l)



def sec_tours():
    say('== tours (t65-tour3 twice on C1EM, once more on GT01; t65-tour2, the slower tour, on both)')
    G5 = os.path.join(GT01_EMU, 't65-tour3')
    runs = [(G5, run_dir('GT01', 't65-tour3'), 'GT01 (T6.5) vs GT01 (now)'),
            (run_dir('C1EM', 't65-tour3'), run_dir('C1EM', 't65-tour3-run2'), 'C1EM run 1 vs run 2'),
            (run_dir('GT01', 't65-tour3'), run_dir('C1EM', 't65-tour3'), 'GT01 (now) vs C1EM run 1'),
            (run_dir('GT01', 't65-tour3'), run_dir('C1EM', 't65-tour3-run2'), 'GT01 (now) vs C1EM run 2'),
            (run_dir('GT01', 't65-tour2'), run_dir('C1EM', 't65-tour2'), 'tour2: GT01 vs C1EM')]
    for a, b, nm in runs:
        if not (os.path.exists(os.path.join(a, 'end.snap')) and os.path.exists(os.path.join(b, 'end.snap'))):
            say('  %s: not run' % nm)
            continue
        rc, t = sh([PY, os.path.join(ROOT, 'scripts', 't65_compare.py'), a, b, '--allow', '*mixer*=0,0,127,63'])
        L_ = t.splitlines()
        diff = [l.split()[1] for l in L_ if l.startswith('DIFF')]
        rc, t2 = sh([PY, os.path.join(ROOT, 'scripts', 't65_after.py'), 'stored', os.path.join(a, 'end.snap'),
                     os.path.join(b, 'end.snap'), '--base', os.path.join(GT01_EMU, 'warm800M.snap')])
        img = t2.splitlines()
        R.setdefault('tours', {})[nm] = {'screens': [l for l in L_ if l.startswith('screens')], 'differ_outside_mixer': diff,
                                         'image': img[:8]}
        say('  %s: %s; outside the MIXER pages: %s' % (nm, ' '.join(l for l in L_ if l.startswith('screens')), diff))
        say('    working image: %s %s' % (img[0] if img else '', ' | '.join(x.strip() for x in img[1:6])))


def main():
    secs = sys.argv[1:] or ['boot', 'pages']
    for s in secs:
        globals()['sec_' + s]()
    os.makedirs(EMU, exist_ok=True)
    json.dump(R, open(os.path.join(EMU, 'report-%s.json' % '-'.join(secs)), 'w'), indent=1)
    open(os.path.join(EMU, 'report-%s.txt' % '-'.join(secs)), 'w').write('\n'.join(L) + '\n')


if __name__ == '__main__':
    main()
