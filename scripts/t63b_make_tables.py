#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""T6.3 CMP1: write param-rows-dn2's crossover text tables (xover_table.h) from the knob laws.

    venv/bin/python scripts/t63b_make_tables.py [--check]

The two crossover knobs of the 3-band compressor (k 75 Low/Mid, k 81 Mid/High; notes/T6.4 2.2 and
the owner's answer 2) follow the laws the DSP uses (fx/mbcomp.c mbcomp_xlo_hz, mbcomp_xhi_hz):
    Low/Mid   f = 30 Hz x (1000/30)^(v/127)     30 Hz .. 1 kHz
    Mid/High  f = 1 kHz x 16^(v/127)            1 kHz .. 16 kHz
The value text shows f rounded half up: Low/Mid in whole Hz below 1 kHz ('196Hz'), and 1 kHz as
'1.0k'; Mid/High in tenths of a kHz ('2.6k', '16.0k'). The ColdFire has no FPU and the mod no C
library, so the formatters read these tables. --check only compares the file with what it would
write (exit 1 if it differs).
"""
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'vendor', 'elekloader', 'examples', 'param-rows-dn2', 'xover_table.h')


def xlo_hz(v):
    return 30.0 * (1000.0 / 30.0) ** (v / 127.0)


def xhi_hz(v):
    return 1000.0 * 16.0 ** (v / 127.0)


def half_up(x):
    return int(math.floor(x + 0.5))


def tables():
    lo = [half_up(xlo_hz(v)) for v in range(128)]           # whole Hz, 30..1000
    hi = [half_up(xhi_hz(v) / 100.0) for v in range(128)]   # tenths of a kHz, 10..160
    return lo, hi


def text_lo(n):
    return '%dHz' % n if n < 1000 else '%d.%dk' % (n // 1000, n % 1000 // 100)


def text_hi(n):
    return '%d.%dk' % (n // 10, n % 10)


def render():
    lo, hi = tables()
    assert lo[0] == 30 and lo[127] == 1000 and all(a <= b for a, b in zip(lo, lo[1:]))
    assert hi[0] == 10 and hi[127] == 160 and all(a <= b for a, b in zip(hi, hi[1:]))
    assert lo[68] == 196 and hi[44] == 26                   # the defaults: 196 Hz, 2.6 kHz

    def rows(vals, per):
        return '\n'.join('    ' + ', '.join('%d' % x for x in vals[i:i + per]) + ','
                         for i in range(0, len(vals), per))
    return ('/* SPDX-License-Identifier: GPL-2.0-or-later */\n'
            '/* param-rows: the crossover knobs\' value texts, written by dn2-cfw\'s\n'
            ' * scripts/t63b_make_tables.py from the DSP\'s knob laws (fx/mbcomp.c): do not edit.\n'
            ' *   xover_lo_hz[v]  = 30 Hz x (1000/30)^(v/127), rounded half up, in Hz (30..1000)\n'
            ' *   xover_hi_dk[v]  = 1 kHz x 16^(v/127), rounded half up, in tenths of a kHz (10..160)\n'
            ' * Defaults: v 68 = 196 Hz, v 44 = 2.6 kHz. */\n'
            'static const unsigned short xover_lo_hz[128] = {\n%s\n};\n\n'
            'static const unsigned char xover_hi_dk[128] = {\n%s\n};\n') % (rows(lo, 16), rows(hi, 16))


def main():
    text = render()
    if '--check' in sys.argv[1:]:
        same = os.path.exists(OUT) and open(OUT).read() == text
        print('%s: %s' % (os.path.relpath(OUT, ROOT), 'as generated' if same else 'DIFFERS'))
        return 0 if same else 1
    with open(OUT, 'w') as fh:
        fh.write(text)
    lo, hi = tables()
    print('wrote %s: Low/Mid %s .. %s (68: %s), Mid/High %s .. %s (44: %s)' % (
        os.path.relpath(OUT, ROOT), text_lo(lo[0]), text_lo(lo[127]), text_lo(lo[68]),
        text_hi(hi[0]), text_hi(hi[127]), text_hi(hi[44])))
    return 0


if __name__ == '__main__':
    sys.exit(main())
