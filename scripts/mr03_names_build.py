#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""MR03 (notes/MR03-eeem.md): morph-names-dn2 0.1, the reverb page for MRB, built and pinned for the packs.

    venv/bin/python scripts/mr03_names_build.py

scripts/mr03_make_mod.py writes its mod.json (every site checked against the stock image), the SDK builds it into
out/MR03/mods/ (as scripts/sp02_build.py builds spring-names), elekloader's lint checks it with core, and
elekloader.patch links core (base 0.3's, sha256-pinned through pack_build's pins) and morph-names on stock 1.12 with
--version MR03 into out/MR03/build/MR03-main.syx: its manifest (MR03-main.syx.json) is the build record whose sha256
pins morph-names for scripts/pack_build.py (MOD_SRC), as SP02's pins spring-names. MR03-main.syx is a link record only,
never a firmware to flash.
"""
import hashlib
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scripts'))
sys.dont_write_bytecode = True
import t65_build as T  # noqa: E402
import fl01_build as F1  # noqa: E402
import pack_build as PB  # noqa: E402

OUT = os.path.join(ROOT, 'out', 'MR03')
MODDIR = os.path.join(T.EL, 'examples', 'morph-names-dn2')


def main():
    env = dict(os.environ, ELEKLOADER_CROSS='m68k-elf-')
    mods, build_dir = os.path.join(OUT, 'mods'), os.path.join(OUT, 'build')
    os.makedirs(mods, exist_ok=True)
    os.makedirs(build_dir, exist_ok=True)
    T.run([sys.executable, os.path.join(ROOT, 'scripts', 'mr03_make_mod.py')])
    F1.sdk_build(MODDIR, mods, env)
    mn = os.path.join(mods, 'morph-names-0.1.elemod')
    core = PB.mod_files({})['core'][1]          # base 0.3's core, found by the sha256 its build pins
    T.run([T.ELPY, '-m', 'elekloader.lint', core, mn, '--stock', T.STOCK], cwd=T.EL, env=env)
    out = os.path.join(build_dir, 'MR03-main.syx')
    T.run([T.ELPY, '-m', 'elekloader.patch', '--stock', T.STOCK, '--mod', core, '--mod', mn, '--version', 'MR03',
           '--out', out], cwd=T.EL, env=env)
    man = json.load(open(out + '.json'))
    got = [(m['id'], m['version'], m['sha256']) for m in man['mods']]
    want = hashlib.sha256(open(mn, 'rb').read()).hexdigest()
    ok = got[-1] == ('morph-names', '0.1', want) and [g[0] for g in got] == ['core', 'morph-names']
    print('%s  %s' % ('ok' if ok else 'FAIL', ', '.join('%s %s %s' % (i, v, s[:12]) for i, v, s in got)))
    print('\n'.join(T.log[-6:]))
    if not ok:
        raise SystemExit('MR03: the manifest does not list core and morph-names 0.1 with its sha256')


if __name__ == '__main__':
    main()
