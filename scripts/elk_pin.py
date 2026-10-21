#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Mod sources from a pinned commit of our elekloader fork (vendor/elekloader, branch dn2-1.12).

    venv/bin/python scripts/elk_pin.py [REV]      extract it and print the tree's path

The builds that are on (or were made for) the unit must keep rebuilding byte for byte after
the working tree's mods move on: DC01 (core-dn2 0.1 alone) and DH01 (+ midi-hooktest-dn2; DH01 ran
on a unit), notes/T1.9; FP01 (core 0.1 + flanger-ui-dn2 + fx-page2-dn2), notes/T5.0; FTN1 (core
0.1, fx-rename-dn2 0.1), GT00 and GT01 (+ param-grow-dn2 0.1, param-rows-dn2 0.1) and SE01 (core 0.1,
fx-echo-dn2 0.1, param-grow 0.1, param-rows 0.1). From the working tree, DC01 and DH01 give other
containers and FP01 is refused (core 0.2 and fx-page2 both patch 0x400c25d8); from REV_0_1 all
three rebuild byte for byte (checked 2026-10-14: c437d58a, f13abeeb, b199823e). The T1.9 and T5.0
recipes have no script: link their core from tree(REV_0_1)/mods/core-dn2.
CMP1 moved core-dn2 to 0.2 (page records) and param-rows-dn2 to 0.2 (the band
rows' names, ranges and texts) in place, so those recipes build their core and param-rows from
REV_0_1, the fork's commit that holds the 0.1 sources (pushed: naturarum/elekloader dn2-1.12).
CMP3 moved param-rows-dn2 to 0.3, comp-ui-dn2 to 0.2 and comp-link-dn2 to 0.2 in place, so CMP1's and CMP2's
main OS (scripts/t63b_build.py without --stage, --stage CMP2) build every mod from REV_CMP2 (e10bb05, the
fork's commit of comp-link 0.1).
`git archive` of `mods/` and `examples/` at that commit goes to out/pinned/elekloader-REV/ (git
ignored), fresh on every call; the SDK and the patcher still run from vendor/elekloader (their
Python did not change). The .elemod files the SDK builds from there are the 0.1 files byte for
byte: the SDK records only source basenames and hashes, not paths.
"""
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EL = os.path.join(ROOT, 'vendor', 'elekloader')
REV_0_1 = '59cd41f9b5d03ea8ce83d2664087f52a269a9ef1'    # core-dn2 0.1, param-rows-dn2 0.1, fx-echo-dn2 0.1
# CMP3 (2026-10-18) moved param-rows-dn2 to 0.3, comp-ui-dn2 to 0.2 and comp-link-dn2 to 0.2 in place; CMP1's and
# CMP2's main OS build their mods from this commit (core 0.2, comp-ui 0.1, param-rows 0.2, comp-link 0.1)
REV_CMP2 = 'e10bb05f700954f55f8d6f9f41771e372df2a135'


def tree(rev=REV_0_1):
    """-> a fresh directory with the fork's mods/ and examples/ at `rev`"""
    full = subprocess.run(['git', '-C', EL, 'rev-parse', '--verify', rev + '^{commit}'],
                          capture_output=True, text=True, check=True).stdout.strip()
    dst = os.path.join(ROOT, 'out', 'pinned', 'elekloader-' + full[:12])
    if os.path.isdir(dst):
        shutil.rmtree(dst)
    os.makedirs(dst)
    arc = subprocess.run(['git', '-C', EL, 'archive', full, 'mods', 'examples'], capture_output=True, check=True)
    subprocess.run(['tar', '-x', '-C', dst], input=arc.stdout, check=True)
    with open(os.path.join(dst, 'PINNED'), 'w') as fh:
        fh.write('%s\n' % full)
    return dst


if __name__ == '__main__':
    print(tree(sys.argv[1] if len(sys.argv) > 1 else REV_0_1))
