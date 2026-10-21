#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""T6.3 CMP2D (a diagnostic build, notes/T6.3-cmp2d.md): CMP2's main OS with comp-ui-dn2 0.1d in place of 0.1.

    venv/bin/python scripts/cmp2d_build.py                    the main OS, and D2EM.syx for the emulator
                                                             (GT01's DSP section; never flash it)
    venv/bin/python scripts/cmp2d_build.py --dsp out/T6.3/CMP2D-dsp/section7.bin --name CP2D --out out/T6.3
                                                             the final container CP2D.syx (CMP2D's DSP)

CMP2D main OS = core-dn2 0.2 + fx-rename-dn2 0.1 + param-grow-dn2 0.1 + param-rows-dn2 0.2 + comp-link-dn2 0.1, all
from the fork's commit e10bb05 (scripts/elk_pin.py REV_CMP2, as CMP2's), + comp-ui-dn2 0.1d from the working tree
(vendor/elekloader/examples/comp-ui-dn2/0.1d/: 0.1 with a second bar for reply half-word 11). It runs
scripts/t63b_build.py's CMP2 stage with that one mod swapped: every CMP2 check (comp-ui's five sites, the records,
the rows, comp-link's site, the stray-byte check against GT01, the refusals) holds for CMP2D too. The version
field is 4 characters in ELE3, so "CMP2D" cannot be it: the final container's is CP2D, the emulator's D2EM, the
main-only reference's D2MO. t63b_build.py is not changed (its STAGES and MODDIRS are extended here, at run time).
CMP2E and CMP2F (notes/T6.3-cmp2e.md, -cmp2f.md) use the same main OS: --dsp out/T6.3/CMP2E-dsp/section7.bin --name CP2E,
--dsp out/T6.3/CMP2F-dsp/section7.bin --name CP2F (--out out/T6.3).
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scripts'))
import t63b_build as T  # noqa: E402
import elk_pin  # noqa: E402

UI_01D = os.path.join(ROOT, 'vendor', 'elekloader', 'examples', 'comp-ui-dn2', '0.1d')
# the main OS the emulator check ran (out/T6.3/CMP2D/main.bin); the final container must carry it
CMP2D_MAIN_SHA = '820db7908517e98bda32290bdd76601ee7071d1cd7e428dccdd4023c02d78899'

T.STAGES['CMP2D'] = dict(set=T.SET + ['comp-link'], main_only='D2MO', name='D2EM',
                         out=os.path.join(ROOT, 'out', 'T6.3', 'CMP2D'), main_sha=CMP2D_MAIN_SHA,
                         versions=dict(T.VERSIONS, **{'comp-ui': '0.1d', 'comp-link': '0.1'}), rev=elk_pin.REV_CMP2)
# comp-ui from the working tree (an absolute path: os.path.join(pinned tree, it) is it)
T.MODDIRS['comp-ui'] = UI_01D

if __name__ == '__main__':
    if '--stage' in sys.argv:
        raise SystemExit('cmp2d_build.py sets the stage itself (CMP2D)')
    sys.argv[1:1] = ['--stage', 'CMP2D']
    T.main()
