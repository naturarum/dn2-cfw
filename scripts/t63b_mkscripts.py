#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""T6.3 CMP1: write the emulator key scripts for CMP1's MIXER pages (scripts/t63b-*.script).

    venv/bin/python scripts/t63b_mkscripts.py

  t63b-pages7.script   FUNC+MOD, then each of the 7 MIXER pages (CMP1): a screen, each of A..H and
                       LEVEL one detent up (a screen: the header's "Name=value") and back, a screen
                       3 s later; DOWN to the next page; after the last, DOWN wraps to the first,
                       UP wraps to the last; then FUNC+MOD 8 times (the page key steps and wraps)
  t63b-pages5.script   the same over GT01's 5 pages (the baseline)
  t63b-values.script   LOW: RAT down to 0 and up to 8 (every text), the crossover to both ends;
                       MID: its crossover to both ends; HIGH: F (the empty box) turned both ways,
                       D turned
  t63b-defaults.script (with --set-k k=default for k 70..86) each band knob up one and back: the
                       header shows the default (CLEAR PAGE's values, scripts/t63b_pagecopy.py)
  t63b-dual.script     EXT MIX 1: DUAL (B) on, then DOWN (EXT MIX 2 after EXT MIX 1, 8 pages) and
                       FUNC+MOD through all 8
  t63b-dual2.script    from t63b-dual's end: LOW's SCS (G) up to In R and back to 0; EXT MIX 1: DUAL
                       off (7 pages again)
  t63b-copy.script     FUNC+REC/PLAY/STOP on the band pages: on this OS they copy, clear and paste
                       the PATTERN (as on GT01); page copy wants MOD held, which leaves the MIXER
                       view (scripts/t63b_pagecopy.py calls the page functions instead)
  t63b-meters.script   each MIXER page once (run with --fake-gr)
  t63b-ob8.script      (with --force-ob8) the last page, DUAL on and off around the Outbox 8 page
  t63b-plock.script    grid recording, a trig on step 1 held while LOW's A..F and MID's F turn
                       (lock records: scripts/t61_locks.py on end.snap)
"""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HDR = '# SPDX-License-Identifier: GPL-2.0-or-later\n# T6.3 CMP1 (scripts/t63b_mkscripts.py): %s\n'


def mixer():
    return ['down space', 'wait-emu 0.1', 'tap semicolon      # FUNC+MOD: MIXER', 'wait-emu 0.1',
            'up space', 'wait-emu 0.6']


def turn(knob, n=1, wait=0.15, back=False):
    k = ('shift+%s' if back else '%s') % knob
    out = []
    for _ in range(n):
        out += ['tap %s' % k, 'wait-emu %s' % wait]
    return out


def func(key, what):
    return ['down space', 'wait-emu 0.1', 'tap %s          # FUNC+%s' % (key, what), 'wait-emu 0.1',
            'up space', 'wait-emu 0.8']


def page_tour(p):
    out = ['png p%d' % p]
    for i, knob in enumerate('123456789'):
        name = 'ABCDEFGHL'[i]
        out += ['tap %s' % knob, 'wait-emu 0.3', 'png p%d-%s' % (p, name), 'tap shift+%s' % knob, 'wait-emu 0.3']
    out += ['wait-emu 3.0', 'png p%d-back' % p]
    return out


def pages(n):
    out = ['wait-emu 0.5', 'png 00-main'] + mixer()
    for p in range(1, n + 1):
        out += page_tour(p)
        out += ['tap Down', 'wait-emu 0.6']
    out += ['png wrap-down']                     # DOWN past the last page: the first again
    out += ['tap Up', 'wait-emu 0.6', 'png wrap-up']   # UP from the first: the last
    out += ['tap Down', 'wait-emu 0.6', 'png wrap-up-down']
    for i in range(n + 1):                       # the page key steps through and wraps
        out += mixer() + ['png key-%d' % (i + 1)]
    return out


def values():
    out = ['wait-emu 0.5'] + mixer() + ['png v00-low']
    out += ['tap 5', 'wait-emu 0.3', 'png v01-rat-up1']
    for i in range(6):                           # RAT down to 0, past it
        out += ['tap shift+5', 'wait-emu 0.3', 'png v02-rat-down%d' % (i + 1)]
    for i in range(10):                          # and up to 8, past it
        out += ['tap 5', 'wait-emu 0.3', 'png v03-rat-up%d' % (i + 1)]
    out += ['wait-emu 2.0']
    out += turn('6', 80, back=True) + ['wait-emu 0.2', 'png v04-xlo-bottom']
    out += ['tap 6', 'wait-emu 0.3', 'png v04-xlo-bottom-up1', 'tap shift+6', 'wait-emu 0.3', 'png v04-xlo-bottom-again']
    out += turn('6', 140) + ['wait-emu 0.2', 'png v05-xlo-top']
    out += ['tap shift+6', 'wait-emu 0.3', 'png v05-xlo-top-down1', 'tap 6', 'wait-emu 0.3', 'png v05-xlo-top-again']
    out += ['wait-emu 2.0', 'tap Down', 'wait-emu 0.6', 'png v06-mid']
    out += turn('6', 60, back=True) + ['wait-emu 0.2', 'png v07-xhi-bottom']
    out += ['tap 6', 'wait-emu 0.3', 'png v07-xhi-bottom-up1', 'tap shift+6', 'wait-emu 0.3', 'png v07-xhi-bottom-again']
    out += turn('6', 140) + ['wait-emu 0.2', 'png v08-xhi-top']
    out += ['tap shift+6', 'wait-emu 0.3', 'png v08-xhi-top-down1', 'tap 6', 'wait-emu 0.3', 'png v08-xhi-top-again']
    out += ['wait-emu 2.0', 'tap Down', 'wait-emu 0.6']               # MID -> HIGH
    out += ['png v20-high']
    out += turn('6', 3, wait=0.3) + ['png v21-high-F-up3'] + turn('6', 3, wait=0.3, back=True) + ['png v22-high-F-down3']
    out += ['tap 4', 'wait-emu 0.3', 'png v23-high-D-up', 'tap shift+4', 'wait-emu 0.3', 'png v24-high-D-back']
    out += ['wait-emu 3.0', 'png v25-high-end']
    return out


def defaults():
    """run with --set-k K=default for k 70..86 (what CLEAR PAGE sets, scripts/t63b_pagecopy.py): each band
    knob one detent up and back on LOW, MID and HIGH; the header shows the default it comes back to"""
    out = ['wait-emu 0.5'] + mixer()
    for p, band in ((1, 'low'), (2, 'mid'), (3, 'high')):
        out += ['png f%d-%s' % (p, band)]
        for i, knob in enumerate('123456'):
            name = 'ABCDEF'[i]
            out += ['tap %s' % knob, 'wait-emu 0.3', 'png f%d-%s-%s-up' % (p, band, name),
                    'tap shift+%s' % knob, 'wait-emu 0.3', 'png f%d-%s-%s-default' % (p, band, name)]
        out += ['wait-emu 3.0', 'tap Down', 'wait-emu 0.6']
    return out


def dual():
    out = ['wait-emu 0.5'] + mixer() + ['png d00-low']
    for i in range(6):                                             # UP/DOWN do not wrap: DOWN x 6
        out += ['tap Down', 'wait-emu 0.5']
    out += ['png d01-extmix1']
    out += ['tap 2', 'wait-emu 0.4', 'png d02-dual-on']            # B: DUAL
    out += ['wait-emu 2.0', 'png d03-dual-on-later']
    out += ['tap Down', 'wait-emu 0.6', 'png d10-down']            # EXT MIX 2, after EXT MIX 1
    out += ['tap Down', 'wait-emu 0.6', 'png d11-down']            # the last page: stays
    out += mixer() + ['png d12-pagekey']                           # FUNC+MOD wraps to the first
    for i in range(2, 9):
        out += mixer() + ['png d1%d-pagekey' % (i + 1) if i < 7 else 'png d2%d-pagekey' % (i - 7)]
    return out


def dual2():
    """from the end of t63b-dual (DUAL on, the MIXER view on EXT MIX 2): LOW's SCS up to In R and
    back to 0, then DUAL off on EXT MIX 1"""
    out = ['wait-emu 0.5'] + mixer() + ['png e00-low']             # FUNC+MOD from the last page: LOW
    for i in range(21):
        out += ['tap 7', 'wait-emu 0.3', 'png e01-scs-up%02d' % (i + 1)]
    out += ['wait-emu 2.0']
    out += turn('7', 25, back=True) + ['wait-emu 0.3', 'png e02-scs-back0']
    out += ['wait-emu 2.0']
    for i in range(6):
        out += ['tap Down', 'wait-emu 0.5']
    out += ['png e04-extmix1']
    out += ['tap shift+2', 'wait-emu 0.4', 'png e05-dual-off']
    out += ['wait-emu 2.0', 'png e06-dual-off-later']
    out += ['tap Down', 'wait-emu 0.6', 'png e07-down']            # EXT MIX 1 is the last again
    out += mixer() + ['png e08-pagekey']                           # wraps to LOW (1/7)
    return out


def copy():
    out = ['wait-emu 0.5'] + mixer() + ['png c00-low']
    out += func('0', 'REC: copy page') + ['png c01-low-copied']
    for knob in '123456':
        out += turn(knob, 5)
    out += ['wait-emu 0.5', 'png c02-low-turned']
    out += func('minus', 'PLAY: clear page') + ['png c03-low-cleared']
    out += func('equal', 'STOP: paste page') + ['png c04-low-pasted']
    out += ['wait-emu 2.0', 'tap Down', 'wait-emu 0.6', 'png c10-mid']
    out += func('0', 'REC: copy page') + ['png c11-mid-copied']
    out += turn('6', 10) + ['wait-emu 0.5', 'png c12-mid-turned']
    out += func('equal', 'STOP: paste page') + ['png c13-mid-pasted']
    out += ['wait-emu 2.0', 'tap Down', 'wait-emu 0.6', 'png c20-high']
    out += func('0', 'REC: copy page') + ['png c21-high-copied']
    out += turn('1', 7) + ['wait-emu 0.5', 'png c22-high-turned']
    out += func('minus', 'PLAY: clear page') + ['png c23-high-cleared']
    out += func('equal', 'STOP: paste page') + ['png c24-high-pasted']
    # LOW's copy pasted onto MID: another page id
    out += ['wait-emu 2.0', 'tap Up', 'wait-emu 0.6', 'tap Up', 'wait-emu 0.6', 'png c30-low']
    out += func('0', 'REC: copy page') + ['tap Down', 'wait-emu 0.6']
    out += func('equal', 'STOP: paste page') + ['png c31-low-on-mid']
    out += ['wait-emu 3.0', 'png c32-end']
    return out


def ob8():
    """with --force-ob8 (an Outbox 8 page at the list's end): the last page, DUAL on and off"""
    out = ['wait-emu 0.5'] + mixer() + ['png o00-first']
    for i in range(10):
        out += ['tap Down', 'wait-emu 0.5']
    out += ['png o01-last']
    out += ['tap Up', 'wait-emu 0.6', 'png o02-extmix1']
    out += ['tap 2', 'wait-emu 0.4', 'png o03-dual-on', 'wait-emu 2.0']
    out += ['tap Down', 'wait-emu 0.6', 'png o04-next', 'tap Down', 'wait-emu 0.6', 'png o05-next']
    out += ['tap Up', 'wait-emu 0.6', 'tap Up', 'wait-emu 0.6', 'png o06-extmix1']
    out += ['tap shift+2', 'wait-emu 0.4', 'png o07-dual-off', 'wait-emu 2.0']
    out += ['tap Down', 'wait-emu 0.6', 'png o08-next', 'tap Down', 'wait-emu 0.6', 'png o09-next']
    return out


def meters(n):
    out = ['wait-emu 0.5'] + mixer()
    for p in range(1, n + 1):
        out += ['wait-emu 0.4', 'png m%d' % p, 'tap Down', 'wait-emu 0.6']
    return out


def plock():
    out = ['wait-emu 0.5', 'tap 0                # RECORD: grid recording on', 'wait-emu 0.3',
           'tap q                # a trig on step 1', 'wait-emu 0.3', 'png l00-grid'] + mixer() + ['png l01-low']
    out += ['down q', 'wait-emu 0.3']
    for knob in '123456':
        out += ['tap %s' % knob, 'wait-emu 0.3', 'png l02-low-%s' % 'ABCDEF'[int(knob) - 1]]
    out += ['up q', 'wait-emu 0.4', 'tap Down', 'wait-emu 0.6', 'down q', 'wait-emu 0.3']
    for knob in '56':
        out += ['tap %s' % knob, 'wait-emu 0.3', 'png l03-mid-%s' % 'ABCDEF'[int(knob) - 1]]
    out += ['up q', 'wait-emu 0.4', 'tap Down', 'wait-emu 0.6', 'down q', 'wait-emu 0.3',
            'tap 1', 'wait-emu 0.3', 'png l04-high-A', 'up q', 'wait-emu 0.4',
            'tap 0                # RECORD: grid recording off', 'wait-emu 0.5', 'png l05-end']
    return out


def main():
    scripts = {
        't63b-pages7.script': ('the 7 MIXER pages, every knob', pages(7)),
        't63b-pages5.script': ('the 5 MIXER pages of GT01, every knob (baseline)', pages(5)),
        't63b-values.script': ('RAT 0..8, the crossovers\' ends, defaults after CLEAR PAGE, HIGH F', values()),
        't63b-defaults.script': ('the band knobs at the new-kit defaults (run with --set-k)', defaults()),
        't63b-dual.script': ('DUAL on through the UI, then the pages from EXT MIX 1', dual()),
        't63b-dual2.script': ('with DUAL on: SCS to In R on LOW, then DUAL off', dual2()),
        't63b-copy.script': ('page copy, clear and paste on the band pages', copy()),
        't63b-meters7.script': ('each MIXER page once (with --fake-gr)', meters(7)),
        't63b-ob8.script': ('with --force-ob8: the Outbox 8 page last, DUAL on and off', ob8()),
        't63b-plock.script': ('band knobs turned with a trig held (no lock may result)', plock()),
    }
    for name, (what, lines) in scripts.items():
        with open(os.path.join(ROOT, 'scripts', name), 'w') as fh:
            fh.write(HDR % what + '\n'.join(lines) + '\n')
        print('wrote scripts/%s (%d lines)' % (name, len(lines)))


if __name__ == '__main__':
    main()
