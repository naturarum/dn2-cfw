#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""DT01: FL02 on a code diet. The same sound with less DSP code (notes/DT01-code-diet.md): FL02's main OS byte for
byte, the DSP section from recipe DT01, the version field DT01.

    venv/bin/python tools/dspsplice/build.py DT01          first: the DSP section, out/DT01/DT01-dsp/
    venv/bin/python scripts/dt01_build.py [--name DT01] [--out out/DT01]

  DT01 main OS = FL02's eight .elemod files (sha256-checked against FL02's manifest) through elekloader.patch with
  --version NAME (the patcher sets the ELE3 version field, as for FL02); then section 7 replaced by
  out/DT01/DT01-dsp/section7.bin (elektron-firmware-tool -c 7).

Writes NAME.syx (+ .sha256), NAME.main.bin (= FL02.main.bin), build-NAME.json, build/. Checks (all must hold, or nothing is
written):
  - the pins: stock 1.12, FL02.syx and its main OS image, FL02's DSP section (0423d7ae...), the DT01 DSP build (its
    record, the layout and placement checks, every function reached, the diet's report);
  - the sources: FL02's but the diet's (tools/dspsplice/src/dt01/: flanger.c, flanger.h, flanger_unit.h, mbcomp.c in
    place of fx/flanger.c, fx/flanger.h, fx/flanger_unit.h, fx/mbcomp.c), the glue, flg_sync.c and the other headers
    FL02's byte for byte;
  - the main OS: FL02's eight .elemod files (sha256), the patcher's manifest (mods, link layout, map, main sha256) and
    map FL02's;
  - the container: the ELE3 header FL02's but the version field; sections 2, 3, 4, 5, 8 stored byte for byte as in
    FL02.syx (section 3, the main OS, as FL02 stores it); section 7 stored as in DT01-dsp.syx and depacking to its
    section7.bin; the main OS depacks to FL02.main.bin;
  - the DSP against FL02's: the final images the same but inside our code sections (block 1, code3, code4, code2 as
    DT01 and FL02 link them) and the two hook CJUMPs whose stub moved (the compressor's); every code section of DT01
    no longer than FL02's.
"""
import argparse
import hashlib
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scripts'))
sys.dont_write_bytecode = True
import t65_build as T  # noqa: E402
import fl01_build as F1  # noqa: E402
sys.path.insert(1, os.path.join(ROOT, 'tools', 'dspsplice'))
sys.path.insert(2, os.path.join(ROOT, 'tools', 'sharcvm'))

TOOL, STOCK, STOCK_SHA = T.TOOL, T.STOCK, T.STOCK_SHA
FL02 = os.path.join(ROOT, 'out', 'FL02')
FL02_SYX, FL02_SYX_SHA = os.path.join(FL02, 'FL02.syx'), '82bfd32db4294413bcd0b62e7c8faf77f650dc9f3262ea678e2f94c3245c92f1'
FL02_MAIN, FL02_JSON = os.path.join(FL02, 'FL02.main.bin'), os.path.join(FL02, 'build-FL02.json')
FL02_DSP = os.path.join(FL02, 'FL02-dsp')
FL02_SEC7_SHA = '0423d7aea8e1feb0cfb0721783a7661b5322c0d5c6118de6a30b6c435f5c790e'
DT01_DSP = os.path.join(ROOT, 'out', 'DT01', 'DT01-dsp')
DIET = {'fx/flanger.c': 'tools/dspsplice/src/dt01/flanger.c', 'fx/flanger.h': 'tools/dspsplice/src/dt01/flanger.h',
        'fx/flanger_unit.h': 'tools/dspsplice/src/dt01/flanger_unit.h', 'fx/mbcomp.c': 'tools/dspsplice/src/dt01/mbcomp.c'}
L1 = 0x28000000
# our code sections, the ranges where our code ran on the unit (tools/dspsplice/build.py PROVEN)
CODE = [(0x2dd600, 0x2e1c36, 'block 1: the stubs, the flanger and its glue'), (0x2e2900, 0x2e4f46, 'code4: the glue'),
        (0x308400, 0x30ff76, 'code3: the compressor'), (0x310000, 0x315f8c, 'code2: flg_sync.c')]
log = T.log


def sha(b):
    return hashlib.sha256(b).hexdigest()


def dsp_identity(checks):
    """DT01's DSP final image against FL02's: every differing byte inside our code or a hook CJUMP site"""
    import build as BD  # noqa: E402
    import ldr  # noqa: E402
    f2 = F1.dsp_regions(open(os.path.join(FL02_DSP, 'section7.bin'), 'rb').read())
    d1 = F1.dsp_regions(open(os.path.join(DT01_DSP, 'section7.bin'), 'rb').read())

    def span(regs, lo, hi):
        """the image's bytes at lo..hi, None where it loads nothing"""
        o = [None] * (hi - lo)
        for (s0, e0), d in regs.items():
            x0, x1 = max(s0, lo), min(e0, hi)
            if x0 < x1:
                o[x0 - lo:x1 - lo] = list(d[x0 - s0:x1 - s0])
        return o
    merged = []
    for lo, hi in sorted(set(f2) | set(d1)):
        if merged and lo <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], hi)
        else:
            merged.append([lo, hi])
    diffs = []
    for lo, hi in merged:
        if (lo, hi) in f2 and (lo, hi) in d1 and f2[(lo, hi)] == d1[(lo, hi)]:
            continue
        x, y = span(f2, lo, hi), span(d1, lo, hi)
        for i in range(hi - lo):
            if x[i] != y[i]:
                if diffs and diffs[-1][1] == lo + i:
                    diffs[-1][1] += 1
                else:
                    diffs.append([lo + i, lo + i + 1])
    hooks = [(ldr.loader_addr(2 * site), ldr.loader_addr(2 * site) + 6, '%#x' % site)
             for site, _, _ in BD.RECIPES['DT01']['hooks']]
    why, stray = {}, []
    for a, b in diffs:
        hit = [w for lo, hi, w in CODE if L1 + lo <= a and b <= L1 + hi + 2]
        hk = [w for lo, hi, w in hooks if lo <= a and b <= hi]
        if hit:
            why.setdefault(hit[0], [0, 0])
            why[hit[0]][0] += 1
            why[hit[0]][1] += b - a
        elif hk:
            why.setdefault('hook CJUMP at SW ' + hk[0], [0, 0])
            why['hook CJUMP at SW ' + hk[0]][0] += 1
            why['hook CJUMP at SW ' + hk[0]][1] += b - a
        else:
            stray.append('%#x-%#x' % (a, b))
    checks['DSP: every byte of the final image that differs from FL02\'s (%d ranges, %d B) is inside our code or a hook '
           'CJUMP (%s)' % (len(diffs), sum(b - a for a, b in diffs),
                           '; '.join('%s: %d ranges %d B' % (k, v[0], v[1]) for k, v in why.items()))] = not stray
    jf = json.load(open(os.path.join(FL02_DSP, 'build.json')))
    jd = json.load(open(os.path.join(DT01_DSP, 'build.json')))
    pf = {r['section']: r['bytes'] for r in jf['placed'] if r['kind'] == 'sw_code'}
    pd = {r['section']: r['bytes'] for r in jd['placed'] if r['kind'] == 'sw_code'}
    checks['DSP: every code section no longer than FL02\'s (%s; %d -> %d B in all)' % (
        ', '.join('%s %d -> %d' % (k, pf[k], pd.get(k, 0)) for k in sorted(pf)), sum(pf.values()), sum(pd.values()))] = (
        set(pd) <= set(pf) and all(pd[k] <= pf[k] for k in pd))
    hk = {p['why'].split(':')[0]: (p['old'], p['new']) for p in jf['patches']}
    hd = {p['why'].split(':')[0]: (p['old'], p['new']) for p in jd['patches']}
    checks['DSP: the same four hook CJUMPs patched, from the same stock bytes (%s)' % ', '.join(sorted(hd))] = (
        set(hk) == set(hd) and all(hk[k][0] == hd[k][0] for k in hk))
    return {'diff_ranges': len(diffs), 'diff_bytes': sum(b - a for a, b in diffs), 'by_part': why, 'stray': stray,
            'code_fl02': pf, 'code_dt01': pd}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--name', default='DT01')
    ap.add_argument('--out', default=os.path.join(ROOT, 'out', 'DT01'))
    a = ap.parse_args()
    if len(a.name) != 4:
        raise SystemExit('--name: 4 characters')
    out = os.path.abspath(a.out)
    os.makedirs(out, exist_ok=True)
    checks = {}

    # ---- pins
    f2j = json.load(open(FL02_JSON))
    for p, want in ((STOCK, STOCK_SHA), (FL02_SYX, FL02_SYX_SHA), (FL02_MAIN, f2j['main_sha256']),
                    (os.path.join(FL02_DSP, 'section7.bin'), FL02_SEC7_SHA)):
        if sha(open(p, 'rb').read()) != want:
            raise SystemExit('%s: sha256 is not the expected one' % p)
    checks['FL02 pinned: FL02.syx %s..., its main OS %s..., its DSP section 7 %s...' % (
        FL02_SYX_SHA[:8], f2j['main_sha256'][:8], FL02_SEC7_SHA[:8])] = f2j['syx']['sha256'] == FL02_SYX_SHA
    f2d = json.load(open(os.path.join(FL02_DSP, 'build.json')))
    dd = json.load(open(os.path.join(DT01_DSP, 'build.json')))
    sec7 = os.path.join(DT01_DSP, 'section7.bin')
    dsyx = os.path.join(ROOT, dd['syx']['path'])
    sec7_sha = sha(open(sec7, 'rb').read())
    checks['DT01 DSP: build.json records section7.bin (%s...) and %s' % (sec7_sha[:8], dd['syx']['path'])] = (
        dd['name'] == 'DT01' and dd['section7']['sha256'] == sec7_sha and dd['syx']['sha256'] == sha(open(dsyx, 'rb').read()))
    checks['DT01 DSP: the layout check reports no problems, every code section where our code ran on the unit (%s), '
           'the stock delay hooks kept' % ', '.join('%s %s' % (r['section'], r['core']) for r in dd['proven_check'])] = (
        not dd['layout_check']['problems'] and all(r['proven'] for r in dd['proven_check'])
        and len(dd['stock_hooks']) == 2 and all(h['stock_bytes_kept'] for h in dd['stock_hooks']))
    checks['DT01 DSP: every function linked is reached from the hooks (%d functions)' % len(dd['reach'])] = (
        all(r['reached'] for r in dd['reach']))
    dt = dd['diet']
    checks['DT01 DSP: the diet as planned: dropped %s; fxmath.h helpers from %s, copies removed %s' % (
        ', '.join(dt['dropped']), dt['shared_from'], ', '.join(dt['removed_copies']))] = (
        sorted(dt['dropped']) == sorted(['flanger:flanger_man_ms', 'flanger:flanger_damp_hz', 'flanger:flanger_rate_hz',
                                         'flanger:flanger_delay_at', 'mbcomp:mbcomp_ratio', 'mbcomp:mbcomp_gr_db'])
        and len(dt['removed_copies']) == 9 and all(x.startswith('mbcomp:') for x in dt['removed_copies']))
    s2, sd = f2d['sources'], dd['sources']
    same = {k: v for k, v in s2.items() if k not in DIET}
    checks['DT01 DSP: the sources FL02\'s but the diet\'s (%s), each one recorded as it is on disk' % ', '.join(
        '%s -> %s' % kv for kv in DIET.items())] = (
        {k: v for k, v in sd.items() if k not in DIET.values()} == same
        and set(DIET.values()) <= set(sd) and set(DIET) <= set(s2)
        and all(sd[f] == sha(open(os.path.join(ROOT, f), 'rb').read()) for f in sd))
    stubs = [r['function'] for r in dd['function_table'] if r['file'] == 'stubs']
    checks['DT01 DSP: block 1\'s stubs are the chorus slot\'s two (%s): the delay stubs CMP4 to FL02 kept, unreachable, '
           'are not built' % ', '.join(stubs)] = stubs == ['fxh_chorus', 'fxh_chorus_init']

    # ---- the main OS: FL02's eight .elemod files (sha256 from FL02's manifest) through the patcher, version NAME
    # (elektron-firmware-tool's -V finds no version field to change in a container named FL02, so the field is set
    # where FL02's was set: by the patcher)
    f2man = json.load(open(os.path.join(FL02, 'build', 'FL02-main.syx.json')))
    where = [os.path.join(ROOT, 'out', d, 'mods') for d in ('FL02', 'FL01', 'OB01')] + [
        os.path.join(ROOT, 'out', 'CMP4', 'main', 'mods')]
    files = []
    for m in f2man['mods']:
        hit = [os.path.join(d, m['file']) for d in where if os.path.exists(os.path.join(d, m['file']))
               and sha(open(os.path.join(d, m['file']), 'rb').read()) == m['sha256']]
        checks['FL02\'s %s %s: its .elemod (sha256 %s...) %s' % (m['id'], m['version'], m['sha256'][:8],
                                                                os.path.relpath(hit[0], ROOT) if hit else 'NOT FOUND')] = bool(hit)
        if not hit:
            raise SystemExit('%s: no file with FL02\'s sha256' % m['file'])
        files.append(hit[0])
    build_dir = os.path.join(out, 'build')
    os.makedirs(build_dir, exist_ok=True)
    mainsyx = os.path.join(build_dir, a.name + '-main.syx')
    env = dict(os.environ, ELEKLOADER_CROSS='m68k-elf-')
    cmd = [T.ELPY, '-m', 'elekloader.patch', '--stock', STOCK]
    for f in files:
        cmd += ['--mod', f]
    T.run(cmd + ['--version', a.name, '--out', mainsyx], cwd=T.EL, env=env)
    man = json.load(open(mainsyx + '.json'))
    checks['the patcher: FL02\'s eight mods in FL02\'s order, the same link layout and map, main OS %s...' % (
        man['main_sha256'][:8])] = (
        [(m['id'], m['version'], m['sha256']) for m in man['mods']]
        == [(m['id'], m['version'], m['sha256']) for m in f2man['mods']]
        and man['link'] == f2man['link'] and man['main_sha256'] == f2man['main_sha256']
        and json.load(open(mainsyx + '.map.json')) == json.load(open(os.path.join(FL02, 'build', 'FL02-main.syx.map.json'))))

    # ---- the container
    syx = os.path.join(out, a.name + '.syx')
    tmp = syx + '.tmp'
    T.run([TOOL, '-i', mainsyx, '-c', '7', sec7, '-o', tmp])
    try:
        f2s, _ = T.sections(FL02_SYX)
        fn, _ = T.sections(tmp)
        ds, _ = T.sections(dsyx)
        hdr, h2 = T.header(tmp), T.header(FL02_SYX)
        checks['ELE3 header: version field %s, the rest as FL02\'s' % a.name] = (
            hdr[0x14:0x18] == a.name.encode() and hdr[:0x14] == h2[:0x14] and hdr[0x18:] == h2[0x18:])
        checks['section ids as FL02\'s'] = sorted(fn) == sorted(f2s)
        for i in sorted(f2s):
            if i != 7:
                checks['section %d stored byte for byte as in FL02.syx' % i] = fn[i] == f2s[i]
        checks['section 7 stored byte for byte as in %s' % dd['syx']['path']] = fn[7] == ds[7]
        checks['section 7 depacks to %s (sha256 %s...)' % (os.path.relpath(sec7, ROOT), sec7_sha[:8])] = (
            T.depack7(tmp) == open(sec7, 'rb').read())
        img = T.main_image(tmp)
        checks['the main OS depacks to FL02.main.bin byte for byte (%s...)' % f2j['main_sha256'][:8]] = (
            img == open(FL02_MAIN, 'rb').read())
        dspid = dsp_identity(checks)
        for k, v in checks.items():
            print('%-4s %s' % ('ok' if v else 'FAIL', k))
        if not all(checks.values()):
            print('  DSP stray: %s' % dspid['stray'][:20])
            print('\n'.join(log[-30:]))
            raise SystemExit('a check failed: nothing written')
        os.replace(tmp, syx)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    s = sha(open(syx, 'rb').read())
    open(syx + '.sha256', 'w').write('%s  %s\n' % (s, os.path.basename(syx)))
    open(os.path.join(out, a.name + '.main.bin'), 'wb').write(img)
    info = {'name': a.name, 'syx': {'path': os.path.relpath(syx, ROOT), 'sha256': s, 'bytes': os.path.getsize(syx)},
            'main_sha256': sha(img), 'section7': {'path': os.path.relpath(sec7, ROOT), 'sha256': sec7_sha},
            'base': 'FL02 ' + FL02_SYX_SHA, 'mods': f2j['mods'], 'dsp_vs_fl02': dspid, 'diet': dt,
            'checks': checks, 'log': log}
    json.dump(info, open(os.path.join(out, 'build-%s.json' % a.name), 'w'), indent=1)
    print('DSP vs FL02: %d ranges, %d B: %s' % (dspid['diff_ranges'], dspid['diff_bytes'], dspid['by_part']))
    print('%s  %s' % (s, os.path.relpath(syx, ROOT)))


if __name__ == '__main__':
    main()
