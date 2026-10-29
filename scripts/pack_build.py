#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""The packs of Digiwerk 0.2.0 (notes/R1.12-packs-0.2.md): base 0.2 (BA05's main OS without its effects' mods) with each
combination of our four effects that fits. A pack is named by the four FX slots in order, compressor, chorus, delay,
reverb: C, F, T, S where ours runs, E where Elektron's does (EEEE: the base alone).

    venv/bin/python tools/dspsplice/build.py NAME         first, for the ten new DSP sections (build.py PACKS)
    venv/bin/python scripts/pack_build.py NAME [NAME ...] | --all

The DSP: EEEE Elektron's section 7; CFEE DC02's (BA05's, on the unit), CFTE TE01's, CFES SP02's, each as built; the
other ten from build.py's pack recipes. The main OS: base 0.2's mods (core, os-version, lfo-dejavu 0.2, arp-modes 0.3,
boot-mark 0.2, arp-midi 0.1) and each effect's own (C: param-grow, param-rows, comp-ui, comp-link; F: flanger-names,
flanger-sync; T: echo-names; S: spring-names), every .elemod file sha256-pinned to the build that made it (BA05, TE01,
SP02), in BA05's order, then echo-names, then spring-names; elekloader.patch --version NAME.

Writes out/NAME/NAME.syx (+ .sha256), NAME.main.bin, build-NAME.json, build/. Checks (all must hold, or no .syx is
written):
  - the pins: stock 1.12, BA05.syx and its build record, each mod file, the DSP section and its build record (for a
    new one: no layout problem, every code section where our code ran on the unit, every function reached, the
    hooks of the effects it has, every other slot's hooks stock);
  - the mods: exactly the base's and the pack's effects', in that order; elekloader's lint;
  - the container: the ELE3 header BA05's but the version field; sections 2, 4, 5, 8 stored byte for byte as in
    BA05.syx (stock); section 7 stored as in the DSP's .syx (EEEE: stock) and depacking to the intended section;
    section 3 as elekloader packed it; the main OS depacks to the patcher's image and rebuilds from its .elemod files,
    layout and map byte for byte;
  - the stock part of the main OS: every byte that differs from Elektron's lies in one of the linked mods' sites;
  - CFEE and CFTE: the main OS BA05's and TE01's byte for byte (they differ in the version field only).
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
import ob01_build as OB  # noqa: E402

TOOL, STOCK, STOCK_SHA, EL, ELPY = T.TOOL, T.STOCK, T.STOCK_SHA, T.EL, T.ELPY
LOAD, STOCK_END = T.LOAD, T.STOCK_END
OUT = os.path.join(ROOT, 'out')
BA05_SYX_SHA = '1a345a94270797bf3e6e43f4b52bdf1b2303294b3f62e0b60714cae8a968eaf8'
PACKS = ('EEEE', 'CEEE', 'EFEE', 'EETE', 'EEES', 'CFEE', 'CETE', 'CEES', 'EFTE', 'EFES', 'EETS', 'CFTE', 'CFES', 'EFTS')
BASE = ['core', 'os-version', 'lfo-dejavu', 'arp-modes', 'boot-mark', 'arp-midi']
FX_MODS = {'C': ['param-grow', 'param-rows', 'comp-ui', 'comp-link'], 'F': ['flanger-names', 'flanger-sync'],
           'T': ['echo-names'], 'S': ['spring-names']}
# the DSP sections built before the packs, as built (the build that made them and its record)
REUSED = {'CFEE': ('DC02', '9fd315f922f4ea71952d3e0074132ac3df50ae532ba058da39bf032e677b1026'),
          'CFTE': ('TE01', '48993b82e1b6eccfaba02ca1dc4929ab69ced0646e2299527fe31b62b80ba458'),
          'CFES': ('SP02', 'a00d7461066dddd2636ab4dde543f868f033561356e287987f19d525de58ee95')}
SAME_MAIN = {'CFEE': 'BA05', 'CFTE': 'TE01'}
# where each mod file comes from: the build whose manifest pins it, and the folders that hold the files
MOD_SRC = [('BA05', None), ('TE01', 'echo-names'), ('SP02', 'spring-names')]
WHERE = [os.path.join(OUT, d, 'mods') for d in ('BA05', 'TE01', 'SP02', 'DJ01', 'FL02', 'FL01', 'OB01')] + [
    os.path.join(OUT, 'CMP4', 'main', 'mods')]
log = T.log


def sha(b):
    return hashlib.sha256(b).hexdigest()


def mod_files(checks):
    """{id: (version, file, sha256)} for every mod a pack can have, each file found by its pinned sha256"""
    out = {}
    for build, only in MOD_SRC:
        man = json.load(open(os.path.join(OUT, build, 'build', build + '-main.syx.json')))
        for m in man['mods']:
            if (only and m['id'] != only) or m['id'] in out:
                continue
            hit = [os.path.join(d, m['file']) for d in WHERE if os.path.exists(os.path.join(d, m['file']))
                   and sha(open(os.path.join(d, m['file']), 'rb').read()) == m['sha256']]
            if not hit:
                raise SystemExit('%s %s: no file with %s\'s sha256' % (m['id'], m['version'], build))
            out[m['id']] = (m['version'], hit[0], m['sha256'])
    checks['mod files: %s, each with the sha256 its build\'s manifest pins' % ', '.join(
        '%s %s (%s)' % (k, v[0], os.path.relpath(v[1], ROOT)) for k, v in out.items())] = (
        set(out) == set(BASE) | {m for v in FX_MODS.values() for m in v})
    return out


def dsp_of(name, checks):
    """(section7 path or None for Elektron's, the DSP .syx, its build record)"""
    import build as BD  # noqa: E402   (tools/dspsplice)
    if name == 'EEEE':
        return None, None, None
    d = os.path.join(OUT, REUSED[name][0] if name in REUSED else name)
    sub = os.path.join(d, os.path.basename(d) + '-dsp')
    js = json.load(open(os.path.join(sub, 'build.json')))
    sec7 = os.path.join(sub, 'section7.bin')
    s7 = sha(open(sec7, 'rb').read())
    dsyx = os.path.join(ROOT, js['syx']['path'])
    checks['DSP %s: its record names section7.bin (%s...) and %s' % (
        os.path.relpath(sub, ROOT), s7[:8], js['syx']['path'])] = (
        js['section7']['sha256'] == s7 and js['syx']['sha256'] == sha(open(dsyx, 'rb').read())
        and (name not in REUSED or s7 == REUSED[name][1]))
    if name not in REUSED:
        on = {o for ch, o in zip(name, 'CFTS') if ch != 'E'}
        rec = BD.RECIPES[name]
        hooks = {p['why'].split(':')[0] for p in js['patches']}
        checks['DSP %s: no layout problem, every code section where our code ran on the unit (%s), every function '
               'reached (%d), %d hooks patched (the effects it has), %d stock hooks kept (every other slot)' % (
                   name, ', '.join('%s %s' % (r['section'], r['core']) for r in js['proven_check']), len(js['reach']),
                   len(hooks), len(js['stock_hooks'] or []))] = (
            not js['layout_check']['problems'] and all(r['proven'] for r in js['proven_check'])
            and all(r['reached'] for r in js['reach']) and len(hooks) == 2 * len(on) == len(rec['hooks'])
            and len(js['stock_hooks'] or []) == 2 * (4 - len(on))
            and all(h['stock_bytes_kept'] for h in js['stock_hooks'] or [])
            and not js['l2_patched'])
    return sec7, dsyx, js


def build(name, env):
    if name not in PACKS:
        raise SystemExit('%s: not a pack' % name)
    out = os.path.join(OUT, name)
    build_dir = os.path.join(out, 'build')
    os.makedirs(build_dir, exist_ok=True)
    checks = {}
    ba = os.path.join(OUT, 'BA05', 'BA05.syx')
    if sha(open(STOCK, 'rb').read()) != STOCK_SHA or sha(open(ba, 'rb').read()) != BA05_SYX_SHA:
        raise SystemExit('stock or BA05.syx: sha256 is not the expected one')
    files = mod_files(checks)
    sec7, dsyx, js = dsp_of(name, checks)
    on = [k for k, ch in zip('CFTS', name) if ch != 'E']
    want = [m for m in json.load(open(os.path.join(OUT, 'BA05', 'build', 'BA05-main.syx.json')))['mods']
            if m['id'] in BASE + sum((FX_MODS[k] for k in on), [])]
    ids = [m['id'] for m in want] + (['echo-names'] if 'T' in on else []) + (['spring-names'] if 'S' in on else [])
    paths = [files[i][1] for i in ids]
    T.run([ELPY, '-m', 'elekloader.lint'] + paths + ['--stock', STOCK], cwd=EL, env=env)
    mainsyx = os.path.join(build_dir, name + '-main.syx')
    cmd = [ELPY, '-m', 'elekloader.patch', '--stock', STOCK]
    for f in paths:
        cmd += ['--mod', f]
    T.run(cmd + ['--version', name, '--out', mainsyx], cwd=EL, env=env)
    man = json.load(open(mainsyx + '.json'))
    mp = json.load(open(mainsyx + '.map.json'))
    checks['mods: the base\'s and %s\'s, in BA05\'s order then echo-names, spring-names (%s)' % (
        name, ', '.join('%s %s' % (m['id'], m['version']) for m in man['mods']))] = (
        [m['id'] for m in man['mods']] == ids
        and all(m['version'] == files[m['id']][0] and m['sha256'] == files[m['id']][2] for m in man['mods']))

    syx = os.path.join(out, name + '.syx')
    tmp = syx + '.tmp'
    if sec7:
        T.run([TOOL, '-i', mainsyx, '-c', '7', sec7, '-o', tmp])
    else:
        with open(mainsyx, 'rb') as f, open(tmp, 'wb') as g:
            g.write(f.read())
    try:
        bas, _ = T.sections(ba)
        fn, _ = T.sections(tmp)
        mn, _ = T.sections(mainsyx)
        st, _ = T.sections(STOCK)
        hdr, h2 = T.header(tmp), T.header(ba)
        checks['ELE3 header: version field %s, the rest as BA05\'s' % name] = (
            hdr[0x14:0x18] == name.encode() and hdr[:0x14] == h2[:0x14] and hdr[0x18:] == h2[0x18:])
        checks['section ids as BA05\'s'] = sorted(fn) == sorted(bas)
        for i in sorted(bas):
            if i not in (3, 7):
                checks['section %d stored byte for byte as in BA05.syx and stock' % i] = fn[i] == bas[i] == st[i]
        if sec7:
            d7, _ = T.sections(dsyx)
            checks['section 7 stored byte for byte as in %s' % os.path.relpath(dsyx, ROOT)] = fn[7] == d7[7]
            checks['section 7 depacks to %s (%s...)' % (os.path.relpath(sec7, ROOT), js['section7']['sha256'][:8])] = (
                T.depack7(tmp) == open(sec7, 'rb').read())
        else:
            checks['section 7 stored byte for byte as stock (Elektron\'s DSP)'] = fn[7] == st[7]
        checks['section 3 stored as elekloader packed it'] = fn[3] == mn[3]
        img = T.main_image(tmp)
        checks['the main OS depacks to the patcher\'s image (%s...)' % man['main_sha256'][:8]] = (
            sha(img) == man['main_sha256'])
        stock_img = open(T.STOCK_MAIN, 'rb').read()
        el = {m['id']: OB.load_elemod(f) for m, f in zip(man['mods'], paths)}
        checks['%s\'s main OS = its %d .elemod files relinked from its layout and map, byte for byte' % (
            name, len(paths))] = OB.reconstruct(stock_img, man, mp, el) == img
        rng = T.diff_ranges(stock_img[:STOCK_END - LOAD], img[:STOCK_END - LOAD])
        spans = [(int(s_['addr'], 16), s_['len']) for d in el.values() for s_ in d['sites']]
        outside = [(x, n) for x, n in rng if not all(any(a <= y < a + k for a, k in spans) for y in range(x, x + n))]
        checks['stock part: %d ranges (%d bytes) differ from Elektron\'s, every one inside a site of the %d mods' % (
            len(rng), sum(n for _, n in rng), len(el))] = not outside
        if name in SAME_MAIN:
            ref = SAME_MAIN[name]
            rj = json.load(open(os.path.join(OUT, ref, 'build-%s.json' % ref)))
            checks['the main OS %s\'s byte for byte (%s...)' % (ref, rj['main_sha256'][:8])] = (
                sha(img) == rj['main_sha256'] == sha(open(os.path.join(OUT, ref, ref + '.main.bin'), 'rb').read()))
        for k, v in checks.items():
            print('%-4s %s' % ('ok' if v else 'FAIL', k))
        if not all(checks.values()):
            print('  outside sites: %s' % outside[:20])
            print('\n'.join(log[-20:]))
            raise SystemExit('%s: a check failed, nothing written' % name)
        os.replace(tmp, syx)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    s = sha(open(syx, 'rb').read())
    open(syx + '.sha256', 'w').write('%s  %s\n' % (s, os.path.basename(syx)))
    open(os.path.join(out, name + '.main.bin'), 'wb').write(img)
    info = {'name': name, 'effects': [{'C': 'cmp3', 'F': 'flng', 'T': 'tape', 'S': 'spng'}[k] for k in on],
            'syx': {'path': os.path.relpath(syx, ROOT), 'sha256': s, 'bytes': os.path.getsize(syx)},
            'main_sha256': sha(img),
            'section7': ({'path': os.path.relpath(sec7, ROOT), 'sha256': js['section7']['sha256']} if sec7
                         else {'path': 'stock', 'sha256': sha(T.depack7(syx))}),
            'mods': man['mods'], 'stock_part_ranges': len(rng), 'checks': checks}
    json.dump(info, open(os.path.join(out, 'build-%s.json' % name), 'w'), indent=1)
    print('%s  %s' % (s, os.path.relpath(syx, ROOT)))
    return info


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('names', nargs='*')
    ap.add_argument('--all', action='store_true')
    a = ap.parse_args()
    sys.path.insert(1, os.path.join(ROOT, 'tools', 'dspsplice'))
    sys.path.insert(2, os.path.join(ROOT, 'tools', 'sharcvm'))
    env = dict(os.environ, ELEKLOADER_CROSS='m68k-elf-')
    for n in (PACKS if a.all else a.names):
        build(n, env)


if __name__ == '__main__':
    main()
