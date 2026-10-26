#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""DC01: DJ01 with the compiler fix. The same features and sound, less DSP code (notes/DC01-compiler-fix.md): DJ01's
main OS byte for byte, the DSP section from recipe DC01 (DT01 with tools/dspsplice/peephole.py), the version field DC01.

    venv/bin/python tools/dspsplice/build.py DC01          first: the DSP section, out/DC01/DC01-dsp/
    venv/bin/python scripts/dc01_build.py [--name DC01] [--out out/DC01]

  DC01 main OS = DJ01's nine .elemod files (sha256-checked against DJ01's manifest) through elekloader.patch with
  --version NAME (the patcher sets the ELE3 version field, as for DJ01); then section 7 replaced by
  out/DC01/DC01-dsp/section7.bin (elektron-firmware-tool -c 7).

Writes NAME.syx (+ .sha256), NAME.main.bin (= DJ01.main.bin), build-NAME.json, build/. Checks (all must hold, or
nothing is written):
  - the pins: stock 1.12, DJ01.syx and its main OS image, DJ01's DSP section (DT01's, fb4b5556...), DT01's DSP build;
  - the DC01 DSP build: its record, the layout and placement checks, the stock delay hooks kept, every function reached,
    the diet as DT01's, the peephole's report (patterns A and B, every changed block checked, none put back), the
    sources DT01's byte for byte, selcc's output (build/*.sel.s) DT01's for every file;
  - the main OS: DJ01's nine .elemod files (sha256), the patcher's manifest (mods, link layout, map, main sha256)
    DJ01's;
  - the container: the ELE3 header DJ01's but the version field; sections 2, 3, 4, 5, 8 stored byte for byte as in
    DJ01.syx; section 7 stored as in DC01-dsp.syx and depacking to its section7.bin; the main OS depacks to
    DJ01.main.bin;
  - the DSP against DJ01's (DT01's): the final images the same but inside our code sections (block 1, code3, code4,
    code2, as both link them) and the hook CJUMPs whose stub moved; the same hooks patched from the same stock bytes;
    every code section of DC01 no longer than DT01's.
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
DJ01 = os.path.join(ROOT, 'out', 'DJ01')
DJ01_SYX, DJ01_SYX_SHA = os.path.join(DJ01, 'DJ01.syx'), '7edf7c59e2aeabd61ebca132a9f031f776475794e04ea37f1635ec8e5a17aed8'
DJ01_MAIN, DJ01_JSON = os.path.join(DJ01, 'DJ01.main.bin'), os.path.join(DJ01, 'build-DJ01.json')
DJ01_MAN = os.path.join(DJ01, 'build', 'DJ01-main.syx.json')
DJ01_MAP = os.path.join(DJ01, 'build', 'DJ01-main.syx.map.json')
DT01_DSP = os.path.join(ROOT, 'out', 'DT01', 'DT01-dsp')
DT01_SEC7_SHA = 'fb4b5556cdc3edca39f7a8f6391e5c30eadafacb0555e683c402dee3a1647fdb'
# the recipe this script builds from (scripts/dc02_build.py sets DC02's) and the peephole mode its DSP must have
RECIPE, PATTERNS = 'DC01', 'AB'
DC01_DSP = os.path.join(ROOT, 'out', RECIPE, RECIPE + '-dsp')
L1 = 0x28000000
# our code sections, the ranges where our code ran on the unit (tools/dspsplice/build.py PROVEN)
CODE = [(0x2dd600, 0x2e1c36, 'block 1: the stubs, the flanger and its glue'), (0x2e2900, 0x2e4f46, 'code4: the glue'),
        (0x308400, 0x30ff76, 'code3: the compressor'), (0x310000, 0x315f8c, 'code2: flg_sync.c')]
log = T.log


def sha(b):
    return hashlib.sha256(b).hexdigest()


def rd(p):
    return open(p, 'rb').read()


def BD_RECIPE_PATTERNS():
    import build as BD  # noqa: E402
    return BD.RECIPES[RECIPE]['peephole']


def dsp_identity(checks):
    """DC01's DSP final image against DT01's (DJ01's): every differing byte inside our code or a hook CJUMP site"""
    import build as BD  # noqa: E402
    import ldr  # noqa: E402
    a = F1.dsp_regions(rd(os.path.join(DT01_DSP, 'section7.bin')))
    b = F1.dsp_regions(rd(os.path.join(DC01_DSP, 'section7.bin')))

    def span(regs, lo, hi):
        o = [None] * (hi - lo)
        for (s0, e0), d in regs.items():
            x0, x1 = max(s0, lo), min(e0, hi)
            if x0 < x1:
                o[x0 - lo:x1 - lo] = list(d[x0 - s0:x1 - s0])
        return o
    merged = []
    for lo, hi in sorted(set(a) | set(b)):
        if merged and lo <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], hi)
        else:
            merged.append([lo, hi])
    diffs = []
    for lo, hi in merged:
        if (lo, hi) in a and (lo, hi) in b and a[(lo, hi)] == b[(lo, hi)]:
            continue
        x, y = span(a, lo, hi), span(b, lo, hi)
        for i in range(hi - lo):
            if x[i] != y[i]:
                if diffs and diffs[-1][1] == lo + i:
                    diffs[-1][1] += 1
                else:
                    diffs.append([lo + i, lo + i + 1])
    hooks = [(ldr.loader_addr(2 * site), ldr.loader_addr(2 * site) + 6, '%#x' % site)
             for site, _, _ in BD.RECIPES[RECIPE]['hooks']]
    why, stray = {}, []
    for lo, hi in diffs:
        hit = [w for c0, c1, w in CODE if L1 + c0 <= lo and hi <= L1 + c1 + 2]
        hk = [w for h0, h1, w in hooks if h0 <= lo and hi <= h1]
        key = hit[0] if hit else ('hook CJUMP at SW ' + hk[0] if hk else None)
        if key is None:
            stray.append('%#x-%#x' % (lo, hi))
            continue
        why.setdefault(key, [0, 0])
        why[key][0] += 1
        why[key][1] += hi - lo
    checks['DSP: every byte of the final image that differs from DT01\'s (DJ01\'s) (%d ranges, %d B) is inside our code '
           'or a hook CJUMP (%s)' % (len(diffs), sum(h - l for l, h in diffs),
                                     '; '.join('%s: %d ranges %d B' % (k, v[0], v[1]) for k, v in why.items()))] = not stray
    jt = json.load(open(os.path.join(DT01_DSP, 'build.json')))
    jc = json.load(open(os.path.join(DC01_DSP, 'build.json')))
    pt = {r['section']: r['bytes'] for r in jt['placed'] if r['kind'] == 'sw_code'}
    pc = {r['section']: r['bytes'] for r in jc['placed'] if r['kind'] == 'sw_code'}
    checks['DSP: every code section no longer than DT01\'s (%s; %d -> %d B in all)' % (
        ', '.join('%s %d -> %d' % (k, pt[k], pc.get(k, 0)) for k in sorted(pt)), sum(pt.values()), sum(pc.values()))] = (
        set(pc) == set(pt) and all(pc[k] <= pt[k] for k in pc))
    ht = {p['why'].split(':')[0]: (p['old'], p['new']) for p in jt['patches']}
    hc = {p['why'].split(':')[0]: (p['old'], p['new']) for p in jc['patches']}
    checks['DSP: the same hook CJUMPs patched, from the same stock bytes (%s)' % ', '.join(sorted(hc))] = (
        set(ht) == set(hc) and all(ht[k][0] == hc[k][0] for k in ht))
    return {'diff_ranges': len(diffs), 'diff_bytes': sum(h - l for l, h in diffs), 'by_part': why, 'stray': stray,
            'code_dt01': pt, 'code_dc01': pc, 'saved': sum(pt.values()) - sum(pc.values())}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--name', default=RECIPE)
    ap.add_argument('--out', default=os.path.join(ROOT, 'out', RECIPE))
    a = ap.parse_args()
    if len(a.name) != 4:
        raise SystemExit('--name: 4 characters')
    out = os.path.abspath(a.out)
    os.makedirs(out, exist_ok=True)
    checks = {}

    # ---- pins
    djj = json.load(open(DJ01_JSON))
    for p, want in ((STOCK, STOCK_SHA), (DJ01_SYX, DJ01_SYX_SHA), (DJ01_MAIN, djj['main_sha256']),
                    (os.path.join(DT01_DSP, 'section7.bin'), DT01_SEC7_SHA)):
        if sha(rd(p)) != want:
            raise SystemExit('%s: sha256 is not the expected one' % p)
    checks['DJ01 pinned: DJ01.syx %s..., its main OS %s..., its DSP section 7 (DT01\'s) %s...' % (
        DJ01_SYX_SHA[:8], djj['main_sha256'][:8], DT01_SEC7_SHA[:8])] = (
        djj['syx']['sha256'] == DJ01_SYX_SHA and djj['section7']['sha256'] == DT01_SEC7_SHA
        and all(djj['checks'].values()))
    dt = json.load(open(os.path.join(DT01_DSP, 'build.json')))
    dc = json.load(open(os.path.join(DC01_DSP, 'build.json')))
    sec7 = os.path.join(DC01_DSP, 'section7.bin')
    dsyx = os.path.join(ROOT, dc['syx']['path'])
    sec7_sha = sha(rd(sec7))
    checks['%s DSP: build.json records section7.bin (%s...) and %s' % (RECIPE, sec7_sha[:8], dc['syx']['path'])] = (
        dc['name'] == RECIPE and dc['section7']['sha256'] == sec7_sha and dc['syx']['sha256'] == sha(rd(dsyx)))
    checks['%s DSP: the layout check reports no problems, every code section where our code ran on the unit (%s), '
           'the stock delay hooks kept' % (RECIPE, ', '.join('%s %s' % (r['section'], r['core'])
                                                              for r in dc['proven_check']))] = (
        not dc['layout_check']['problems'] and all(r['proven'] for r in dc['proven_check'])
        and len(dc['stock_hooks']) == 2 and all(h['stock_bytes_kept'] for h in dc['stock_hooks']))
    checks['%s DSP: every function linked is reached from the hooks (%d functions)' % (RECIPE, len(dc['reach']))] = (
        all(r['reached'] for r in dc['reach']) and len(dc['reach']) == len(dt['reach']))
    checks['%s DSP: the diet as DT01\'s (dropped %d, copies removed %d)' % (
        RECIPE, len(dc['diet']['dropped']), len(dc['diet']['removed_copies']))] = dc['diet'] == dt['diet']
    ph = json.load(open(os.path.join(DC01_DSP, 'build', 'peephole.json')))
    import peephole as PH  # noqa: E402
    checks['%s DSP: the peephole (peephole.py %s...): patterns %s, %d sites (%s), %d changed blocks checked against '
           'selcc\'s, none put back' % (RECIPE, ph['peephole_sha256'][:8], ph['patterns'], ph['sites'], ph['by_pattern'],
                                         ph['blocks_checked'])] = (
        ph['patterns'] == PATTERNS and BD_RECIPE_PATTERNS() == PATTERNS and not ph['reverted'] and ph['blocks_checked'] > 0
        and ph['peephole_sha256'] == sha(rd(PH.__file__)))
    checks['%s DSP: the sources DT01\'s byte for byte (%d files), each one recorded as it is on disk' % (
        RECIPE, len(dc['sources']))] = (dc['sources'] == dt['sources']
                           and all(dc['sources'][f] == sha(rd(os.path.join(ROOT, f))) for f in dc['sources']))
    stems = sorted({os.path.basename(p)[:-6] for p in os.listdir(os.path.join(DC01_DSP, 'build')) if p.endswith('.sel.s')})
    same_sel = [s for s in stems if rd(os.path.join(DC01_DSP, 'build', s + '.sel.s'))
                == rd(os.path.join(DT01_DSP, 'build', s + '.sel.s'))]
    checks['%s DSP: selcc\'s output the same as DT01\'s for every file (%s)' % (RECIPE, ', '.join(stems))] = (
        same_sel == stems and len(stems) == 6)

    # ---- the main OS: DJ01's nine .elemod files through the patcher, version NAME
    dman = json.load(open(DJ01_MAN))
    where = [os.path.join(ROOT, 'out', d, 'mods') for d in ('DJ01', 'FL02', 'FL01', 'OB01')] + [
        os.path.join(ROOT, 'out', 'CMP4', 'main', 'mods')]
    files = []
    for m in dman['mods']:
        hit = [os.path.join(d, m['file']) for d in where if os.path.exists(os.path.join(d, m['file']))
               and sha(rd(os.path.join(d, m['file']))) == m['sha256']]
        checks['DJ01\'s %s %s: its .elemod (sha256 %s...) %s' % (m['id'], m['version'], m['sha256'][:8],
                                                                os.path.relpath(hit[0], ROOT) if hit else 'NOT FOUND')] = bool(hit)
        if not hit:
            raise SystemExit('%s: no file with DJ01\'s sha256' % m['file'])
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
    checks['the patcher: DJ01\'s nine mods in DJ01\'s order, the same link layout and map, main OS %s...' % (
        man['main_sha256'][:8])] = (
        [(m['id'], m['version'], m['sha256']) for m in man['mods']]
        == [(m['id'], m['version'], m['sha256']) for m in dman['mods']]
        and man['link'] == dman['link'] and man['main_sha256'] == dman['main_sha256']
        and json.load(open(mainsyx + '.map.json')) == json.load(open(DJ01_MAP)))

    # ---- the container
    syx = os.path.join(out, a.name + '.syx')
    tmp = syx + '.tmp'
    T.run([TOOL, '-i', mainsyx, '-c', '7', sec7, '-o', tmp])
    try:
        js, _ = T.sections(DJ01_SYX)
        fn, _ = T.sections(tmp)
        ds, _ = T.sections(dsyx)
        hdr, h2 = T.header(tmp), T.header(DJ01_SYX)
        checks['ELE3 header: version field %s, the rest as DJ01\'s' % a.name] = (
            hdr[0x14:0x18] == a.name.encode() and hdr[:0x14] == h2[:0x14] and hdr[0x18:] == h2[0x18:])
        checks['section ids as DJ01\'s'] = sorted(fn) == sorted(js)
        for i in sorted(js):
            if i != 7:
                checks['section %d stored byte for byte as in DJ01.syx' % i] = fn[i] == js[i]
        checks['section 7 stored byte for byte as in %s' % dc['syx']['path']] = fn[7] == ds[7]
        checks['section 7 depacks to %s (sha256 %s...)' % (os.path.relpath(sec7, ROOT), sec7_sha[:8])] = (
            T.depack7(tmp) == rd(sec7))
        img = T.main_image(tmp)
        checks['the main OS depacks to DJ01.main.bin byte for byte (%s...)' % djj['main_sha256'][:8]] = (
            img == rd(DJ01_MAIN))
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
    s = sha(rd(syx))
    open(syx + '.sha256', 'w').write('%s  %s\n' % (s, os.path.basename(syx)))
    open(os.path.join(out, a.name + '.main.bin'), 'wb').write(img)
    info = {'name': a.name, 'syx': {'path': os.path.relpath(syx, ROOT), 'sha256': s, 'bytes': os.path.getsize(syx)},
            'main_sha256': sha(img), 'section7': {'path': os.path.relpath(sec7, ROOT), 'sha256': sec7_sha},
            'base': 'DJ01 ' + DJ01_SYX_SHA, 'mods': djj['mods'], 'dsp_vs_dj01': dspid,
            'peephole': {k: ph[k] for k in ('peephole_sha256', 'patterns', 'sites', 'by_pattern', 'bytes_saved_estimate',
                                            'by_file', 'blocks_checked', 'reverted')},
            'checks': checks, 'log': log}
    json.dump(info, open(os.path.join(out, 'build-%s.json' % a.name), 'w'), indent=1)
    print('DSP vs DJ01 (DT01): %d ranges, %d B: %s; code %d B shorter' % (
        dspid['diff_ranges'], dspid['diff_bytes'], dspid['by_part'], dspid['saved']))
    print('%s  %s' % (s, os.path.relpath(syx, ROOT)))


if __name__ == '__main__':
    main()
