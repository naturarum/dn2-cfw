#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""DC02: DJ01 with the compiler fix in its cautious mode (notes/DC01-compiler-fix.md section 5): scripts/dc01_build.py's
build and checks for recipe DC02 (the peephole in mode "ABs": a struct pointer loaded straight into I4 only where a 16-
or 32-bit instruction uses it next). DJ01's main OS byte for byte, the DSP section from recipe DC02, the version DC02.

    venv/bin/python tools/dspsplice/build.py DC02          first: the DSP section, out/DC02/DC02-dsp/
    venv/bin/python scripts/dc02_build.py [--name DC02] [--out out/DC02]
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scripts'))
sys.dont_write_bytecode = True
import dc01_build as D  # noqa: E402

D.RECIPE, D.PATTERNS = 'DC02', 'ABs'
D.DC01_DSP = os.path.join(ROOT, 'out', 'DC02', 'DC02-dsp')

if __name__ == '__main__':
    D.main()
