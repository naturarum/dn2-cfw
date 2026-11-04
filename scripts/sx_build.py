#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""SX01 and SX02 (2026-10-30): two diagnostic builds for the spring builds' DSP stop on the unit (CFES and EFTS stop the
DSP within a fraction of a second, even on an empty pattern; HANDOFF 2026-10-30). Each swaps one half of CFES for
CFEE's, which runs on the unit (BA05):

    SX01  CFEE's main OS (base 0.2, C and F mods, no spring-names)  + CFES's DSP (SP02's section 7: SPNG v2)
    SX02  CFES's main OS (base 0.2, C, F and spring-names 0.2)       + CFEE's DSP (DC02's section 7: Elektron's reverb)

    SX03  CFES's main OS                                             + SX03's DSP (SP02 with v2's additions gated by
                                                                       a stage on G: tools/dspsplice/src/sx03/)

If SX01 stops, the spring's DSP code is the cause; if SX02 stops, the main OS side. SX03 (2026-10-31), for when SX01
stops: G (WDTH) picks how much of SPNG v2 runs, so the stage where the DSP stops names the part. Built as scripts/pack_build.py
builds a pack (its pinned mods, elekloader.patch --version NAME, section 7 inserted, its container checks), with the
mods and the DSP chosen here rather than from the name.

SX04 (2026-10-31), after SX03 stopped at G 16 (stage 1): SX03 with DC02's peephole pass not run over spring.c.

    venv/bin/python scripts/sx_build.py SX01 SX02 SX03 SX04
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scripts'))
sys.dont_write_bytecode = True
import pack_build as PB  # noqa: E402

T, OB, sha = PB.T, PB.OB, PB.sha
SX = {'SX01': (['C', 'F'], 'CFEE', 'SP02'),         # (the main OS's effects' mods, its reference, the DSP's build)
      'SX02': (['C', 'F', 'S'], 'CFES', 'DC02'),
      'SX03': (['C', 'F', 'S'], 'CFES', 'SX03'),
      'SX04': (['C', 'F', 'S'], 'CFES', 'SX04')}


def build(name, env):
    on, ref, dsp = SX[name]
    out = os.path.join(PB.OUT, name)
    build_dir = os.path.join(out, 'build')
    os.makedirs(build_dir, exist_ok=True)
    checks = {}
    ba = os.path.join(PB.OUT, 'BA05', 'BA05.syx')
    if sha(open(PB.STOCK, 'rb').read()) != PB.STOCK_SHA or sha(open(ba, 'rb').read()) != PB.BA05_SYX_SHA:
        raise SystemExit('stock or BA05.syx: sha256 is not the expected one')
    files = PB.mod_files(checks)
    sub = os.path.join(PB.OUT, dsp, dsp + '-dsp')
    js = json.load(open(os.path.join(sub, 'build.json')))
    sec7 = os.path.join(sub, 'section7.bin')
    dsyx = os.path.join(ROOT, js['syx']['path'])
    checks['DSP %s: its record names section7.bin (%s...) and %s' % (dsp, js['section7']['sha256'][:8],
                                                                    js['syx']['path'])] = (
        js['section7']['sha256'] == sha(open(sec7, 'rb').read()) and js['syx']['sha256'] == sha(open(dsyx, 'rb').read()))
    want = [m for m in json.load(open(os.path.join(PB.OUT, 'BA05', 'build', 'BA05-main.syx.json')))['mods']
            if m['id'] in PB.BASE + sum((PB.FX_MODS[k] for k in on), [])]
    ids = [m['id'] for m in want] + (['spring-names'] if 'S' in on else [])
    paths = [files[i][1] for i in ids]
    T.run([PB.ELPY, '-m', 'elekloader.lint'] + paths + ['--stock', PB.STOCK], cwd=PB.EL, env=env)
    mainsyx = os.path.join(build_dir, name + '-main.syx')
    cmd = [PB.ELPY, '-m', 'elekloader.patch', '--stock', PB.STOCK]
    for f in paths:
        cmd += ['--mod', f]
    T.run(cmd + ['--version', name, '--out', mainsyx], cwd=PB.EL, env=env)
    man = json.load(open(mainsyx + '.json'))
    mp = json.load(open(mainsyx + '.map.json'))
    checks['mods: %s (%s)' % (', '.join(ids), ', '.join('%s %s' % (m['id'], m['version']) for m in man['mods']))] = (
        [m['id'] for m in man['mods']] == ids
        and all(m['version'] == files[m['id']][0] and m['sha256'] == files[m['id']][2] for m in man['mods']))
    syx = os.path.join(out, name + '.syx')
    tmp = syx + '.tmp'
    T.run([PB.TOOL, '-i', mainsyx, '-c', '7', sec7, '-o', tmp])
    try:
        bas, _ = T.sections(ba)
        fn, _ = T.sections(tmp)
        mn, _ = T.sections(mainsyx)
        st, _ = T.sections(PB.STOCK)
        hdr, h2 = T.header(tmp), T.header(ba)
        checks['ELE3 header: version field %s, the rest as BA05\'s' % name] = (
            hdr[0x14:0x18] == name.encode() and hdr[:0x14] == h2[:0x14] and hdr[0x18:] == h2[0x18:])
        checks['section ids as BA05\'s'] = sorted(fn) == sorted(bas)
        for i in sorted(bas):
            if i not in (3, 7):
                checks['section %d stored byte for byte as in BA05.syx and stock' % i] = fn[i] == bas[i] == st[i]
        d7, _ = T.sections(dsyx)
        checks['section 7 stored byte for byte as in %s' % os.path.relpath(dsyx, ROOT)] = fn[7] == d7[7]
        checks['section 7 depacks to %s (%s...)' % (os.path.relpath(sec7, ROOT), js['section7']['sha256'][:8])] = (
            T.depack7(tmp) == open(sec7, 'rb').read())
        checks['section 3 stored as elekloader packed it'] = fn[3] == mn[3]
        img = T.main_image(tmp)
        checks['the main OS depacks to the patcher\'s image (%s...)' % man['main_sha256'][:8]] = (
            sha(img) == man['main_sha256'])
        stock_img = open(T.STOCK_MAIN, 'rb').read()
        el = {m['id']: OB.load_elemod(f) for m, f in zip(man['mods'], paths)}
        checks['%s\'s main OS = its %d .elemod files relinked from its layout and map, byte for byte' % (
            name, len(paths))] = OB.reconstruct(stock_img, man, mp, el) == img
        rng = T.diff_ranges(stock_img[:PB.STOCK_END - PB.LOAD], img[:PB.STOCK_END - PB.LOAD])
        spans = [(int(s_['addr'], 16), s_['len']) for d in el.values() for s_ in d['sites']]
        outside = [(x, n) for x, n in rng if not all(any(a <= y < a + k for a, k in spans) for y in range(x, x + n))]
        checks['stock part: %d ranges (%d bytes) differ from Elektron\'s, every one inside a site of the %d mods' % (
            len(rng), sum(n for _, n in rng), len(el))] = not outside
        rj = json.load(open(os.path.join(PB.OUT, ref, 'build-%s.json' % ref)))
        checks['the main OS %s\'s byte for byte (%s...; the name is in the header only)' % (ref, rj['main_sha256'][:8])] = (
            sha(img) == rj['main_sha256'] == sha(open(os.path.join(PB.OUT, ref, ref + '.main.bin'), 'rb').read()))
        for k, v in checks.items():
            print('%-4s %s' % ('ok' if v else 'FAIL', k))
        if not all(checks.values()):
            print('  outside sites: %s' % outside[:20])
            raise SystemExit('%s: a check failed, nothing written' % name)
        os.replace(tmp, syx)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    s = sha(open(syx, 'rb').read())
    open(syx + '.sha256', 'w').write('%s  %s\n' % (s, os.path.basename(syx)))
    open(os.path.join(out, name + '.main.bin'), 'wb').write(img)
    info = {'name': name, 'what': __doc__.split('\n\n')[1], 'main_like': ref, 'dsp_from': dsp,
            'syx': {'path': os.path.relpath(syx, ROOT), 'sha256': s, 'bytes': os.path.getsize(syx)},
            'main_sha256': sha(img), 'section7': {'path': os.path.relpath(sec7, ROOT), 'sha256': js['section7']['sha256']},
            'mods': man['mods'], 'checks': checks}
    json.dump(info, open(os.path.join(out, 'build-%s.json' % name), 'w'), indent=1)
    print('%s  %s' % (s, os.path.relpath(syx, ROOT)))


def main():
    sys.path.insert(1, os.path.join(ROOT, 'tools', 'dspsplice'))
    sys.path.insert(2, os.path.join(ROOT, 'tools', 'sharcvm'))
    env = dict(os.environ, ELEKLOADER_CROSS='m68k-elf-')
    for n in sys.argv[1:] or sorted(SX):
        if n not in SX:
            raise SystemExit('%s: not a diagnostic build (%s)' % (n, ', '.join(sorted(SX))))
        build(n, env)


if __name__ == '__main__':
    main()
