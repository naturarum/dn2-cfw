#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""TS01 (2026-10-31, notes/T2.9-trig-slides.md): CFTE (the 0.2 build played most on the unit) with trig-slide-dn2 0.1,
parameter slides between trigs (vendor/elekloader/examples/trig-slide-dn2): SLIDE, a new knob on the TRIG page's second
page (RTRG, E); with it on, when a note's gate goes off the track's parameters glide in a straight line to the values
the next trig sets (its sound with its p-locks), arriving as it plays; no gap, no slide. Saved with the sound.

    venv/bin/python scripts/ts01_build.py

Runs the mod's host test (scripts/ts01/test_ts.c), builds it (elekloader.sdk.build into out/TS01/mods/), then links
CFTE's thirteen pinned mods and trig-slide, inserts TE01's DSP section and checks the container as
scripts/pack_build.py checks a pack. Writes out/TS01/TS01.syx (+ .sha256), TS01.main.bin, build-TS01.json, build/.
"""
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scripts'))
sys.dont_write_bytecode = True
import pack_build as PB  # noqa: E402

T, OB, sha = PB.T, PB.OB, PB.sha
NAME = 'TS01'
MODDIR = os.path.join(PB.EL, 'examples', 'trig-slide-dn2')
OUT = os.path.join(PB.OUT, NAME)
# the stock instruction each site replaces, and what it becomes
JSR = {0x400da184: ('4eb940138e3c', 'ts_queue_hook'), 0x4002717e: ('4eb9400db28e', 'ts_block'),
       0x400dd8b2: ('156b00de00f4', 'ts_ser'), 0x400dd3e6: ('712a001c7406', 'ts_load')}
KEEP2 = {0x40025f00: ('49f94002b06e', 'ts_gateoff'), 0x40026778: ('47f94002b06e', 'ts_gateoff'),
         0x40026c50: ('45f94002b06e', 'ts_gateoff'), 0x40026f78: ('49f94002b06e', 'ts_gateoff'),
         0x40026ff6: ('47f94002b06e', 'ts_gateoff'), 0x400d7f14: ('2e3c40138e3c', 'ts_start_hook'),
         0x400d8380: ('2e3c40138e3c', 'ts_start_hook')}


def main():
    env = dict(os.environ, ELEKLOADER_CROSS='m68k-elf-')
    build_dir = os.path.join(OUT, 'build')
    mods_dir = os.path.join(OUT, 'mods')
    os.makedirs(build_dir, exist_ok=True)
    os.makedirs(mods_dir, exist_ok=True)
    checks = {}
    ba = os.path.join(PB.OUT, 'BA05', 'BA05.syx')
    if sha(open(PB.STOCK, 'rb').read()) != PB.STOCK_SHA or sha(open(ba, 'rb').read()) != PB.BA05_SYX_SHA:
        raise SystemExit('stock or BA05.syx: sha256 is not the expected one')

    # the mod: its host test, then the SDK build
    test = os.path.join(OUT, 'test_ts')
    T.run(['cc', '-O2', '-Wall', '-Wextra', '-Werror', '-o', test, os.path.join(ROOT, 'scripts', 'ts01', 'test_ts.c')])
    r = subprocess.run([test], capture_output=True, text=True)
    checks['trig-slide on the Mac (scripts/ts01/test_ts.c, %d scenarios): %s' % (
        r.stdout.count('\n') - 1, r.stdout.strip().splitlines()[-1])] = r.returncode == 0
    T.run([PB.ELPY, '-m', 'elekloader.sdk.build', MODDIR, '--stock', PB.STOCK, '--out', mods_dir], cwd=PB.EL, env=env)
    ts_file = os.path.join(mods_dir, 'trig-slide-0.1.elemod')

    # CFTE's mods (pinned by BA05, TE01) and trig-slide
    files = PB.mod_files(checks)
    want = [m for m in json.load(open(os.path.join(PB.OUT, 'BA05', 'build', 'BA05-main.syx.json')))['mods']
            if m['id'] in PB.BASE + PB.FX_MODS['C'] + PB.FX_MODS['F']]
    ids = [m['id'] for m in want] + ['echo-names']
    paths = [files[i][1] for i in ids] + [ts_file]
    ids.append('trig-slide')
    T.run([PB.ELPY, '-m', 'elekloader.lint'] + paths + ['--stock', PB.STOCK], cwd=PB.EL, env=env)
    mainsyx = os.path.join(build_dir, NAME + '-main.syx')
    cmd = [PB.ELPY, '-m', 'elekloader.patch', '--stock', PB.STOCK]
    for f in paths:
        cmd += ['--mod', f]
    T.run(cmd + ['--version', NAME, '--out', mainsyx], cwd=PB.EL, env=env)
    man = json.load(open(mainsyx + '.json'))
    mp = json.load(open(mainsyx + '.map.json'))
    checks['mods: CFTE\'s thirteen then trig-slide (%s)' % ', '.join('%s %s' % (m['id'], m['version'])
                                                                    for m in man['mods'])] = (
        [m['id'] for m in man['mods']] == ids
        and all(m['version'] == files[m['id']][0] and m['sha256'] == files[m['id']][2] for m in man['mods'][:-1]))

    sub = os.path.join(PB.OUT, 'TE01', 'TE01-dsp')
    js = json.load(open(os.path.join(sub, 'build.json')))
    sec7 = os.path.join(sub, 'section7.bin')
    dsyx = os.path.join(ROOT, js['syx']['path'])
    checks['DSP TE01 (CFTE\'s): its record names section7.bin (%s...)' % js['section7']['sha256'][:8]] = (
        js['section7']['sha256'] == sha(open(sec7, 'rb').read()) == PB.REUSED['CFTE'][1])
    syx = os.path.join(OUT, NAME + '.syx')
    tmp = syx + '.tmp'
    T.run([PB.TOOL, '-i', mainsyx, '-c', '7', sec7, '-o', tmp])
    try:
        bas, _ = T.sections(ba)
        fn, _ = T.sections(tmp)
        mn, _ = T.sections(mainsyx)
        st, _ = T.sections(PB.STOCK)
        hdr, h2 = T.header(tmp), T.header(ba)
        checks['ELE3 header: version field %s, the rest as BA05\'s' % NAME] = (
            hdr[0x14:0x18] == NAME.encode() and hdr[:0x14] == h2[:0x14] and hdr[0x18:] == h2[0x18:])
        checks['section ids as BA05\'s'] = sorted(fn) == sorted(bas)
        for i in sorted(bas):
            if i not in (3, 7):
                checks['section %d stored byte for byte as in BA05.syx and stock' % i] = fn[i] == bas[i] == st[i]
        d7, _ = T.sections(dsyx)
        checks['section 7 stored byte for byte as in %s' % os.path.relpath(dsyx, ROOT)] = fn[7] == d7[7]
        checks['section 7 depacks to TE01\'s (%s...)' % js['section7']['sha256'][:8]] = (
            T.depack7(tmp) == open(sec7, 'rb').read())
        checks['section 3 stored as elekloader packed it'] = fn[3] == mn[3]
        img = T.main_image(tmp)
        checks['the main OS depacks to the patcher\'s image (%s...)' % man['main_sha256'][:8]] = (
            sha(img) == man['main_sha256'])
        stock_img = open(T.STOCK_MAIN, 'rb').read()
        el = {m['id']: OB.load_elemod(f) for m, f in zip(man['mods'], paths)}
        checks['TS01\'s main OS = its %d .elemod files relinked from its layout and map, byte for byte' % len(paths)] = (
            OB.reconstruct(stock_img, man, mp, el) == img)
        rng = T.diff_ranges(stock_img[:PB.STOCK_END - PB.LOAD], img[:PB.STOCK_END - PB.LOAD])
        spans = [(int(s_['addr'], 16), s_['len']) for d in el.values() for s_ in d['sites']]
        outside = [(x, n) for x, n in rng if not all(any(a <= y < a + k for a, k in spans) for y in range(x, x + n))]
        checks['stock part: %d ranges (%d bytes) differ from Elektron\'s, every one inside a site of the %d mods' % (
            len(rng), sum(n for _, n in rng), len(el))] = not outside
        cf = open(os.path.join(PB.OUT, 'CFTE', 'CFTE.main.bin'), 'rb').read()
        ts_sites = el['trig-slide']['sites']
        diff_cf = T.diff_ranges(cf[:PB.STOCK_END - PB.LOAD], img[:PB.STOCK_END - PB.LOAD])
        in_ts = [(x, n) for x, n in diff_cf if all(any(int(s_['addr'], 16) <= y < int(s_['addr'], 16) + s_['len']
                                                        for s_ in ts_sites) for y in range(x, x + n))]
        checks['against CFTE\'s main OS, the stock part differs only inside trig-slide\'s %d sites (%d ranges)' % (
            len(ts_sites), len(diff_cf))] = len(in_ts) == len(diff_cf) and len(ts_sites) == len(JSR) + len(KEEP2)
        at = lambda a, n: img[a - PB.LOAD:a - PB.LOAD + n].hex()   # noqa: E731
        st_at = lambda a, n: stock_img[a - PB.LOAD:a - PB.LOAD + n].hex()   # noqa: E731
        tgt = {k: int(v, 16) for k, v in mp.items()}
        checks['the four calls go to %s, in place of Elektron\'s jsr or move (the stock bytes as expected)' % ', '.join(
            '%s %#x' % (n, tgt.get(n, 0)) for _, n in JSR.values())] = all(
            st_at(a, 6) == s and at(a, 2) == '4eb9' and int(at(a + 2, 4), 16) == tgt.get(n, -1)
            for a, (s, n) in JSR.items())
        checks['the five loads of 0x4002b06e (the gate\'s end) load ts_gateoff %#x, play start\'s two of 0x40138e3c '
               'ts_start_hook %#x' % (tgt.get('ts_gateoff', 0), tgt.get('ts_start_hook', 0))] = all(
            st_at(a, 6) == s and at(a, 2) == s[:4] and int(at(a + 2, 4), 16) == tgt.get(n, -1)
            for a, (s, n) in KEEP2.items())
        tabs = man['link']['tables']['param_rows']
        checks['param_rows: %d entries, param-rows\' then ts_rows %#x' % (tabs['entries'], tgt.get('ts_rows', 0))] = (
            tabs['entries'] == 2 and 'ts_rows' in tgt)
        for k, v in checks.items():
            print('%-4s %s' % ('ok' if v else 'FAIL', k))
        if not all(checks.values()):
            raise SystemExit('TS01: a check failed, nothing written')
        os.replace(tmp, syx)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    s = sha(open(syx, 'rb').read())
    open(syx + '.sha256', 'w').write('%s  %s\n' % (s, os.path.basename(syx)))
    open(os.path.join(OUT, NAME + '.main.bin'), 'wb').write(img)
    json.dump({'name': NAME, 'what': __doc__.split('\n\n')[0], 'syx': {'path': os.path.relpath(syx, ROOT), 'sha256': s,
                                                                         'bytes': os.path.getsize(syx)},
               'main_sha256': sha(img), 'section7': {'path': os.path.relpath(sec7, ROOT),
                                                     'sha256': js['section7']['sha256']},
               'mods': man['mods'], 'checks': checks}, open(os.path.join(OUT, 'build-%s.json' % NAME), 'w'), indent=1)
    print('%s  %s' % (s, os.path.relpath(syx, ROOT)))


if __name__ == '__main__':
    main()
