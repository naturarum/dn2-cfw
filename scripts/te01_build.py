#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""TE01, the tape echo test build (notes/TE01-tape-echo.md): BA05 (base 0.2 on DC02, on the unit 2026-10-29) with the
tape echo in the delay slot. The DSP section from recipe TE01 (DC02's with fx/spaceecho.c and se_glue.c fitted, one
function at a time, into the room DC02 leaves in the four regions where our code ran on the unit; the delay's hooks on
our stubs); the main OS BA05's twelve mods plus echo-names-dn2 0.1 (fx-echo 0.2, SC01's delay page, without its
flanger sites); the version field TE01.

    venv/bin/python tools/dspsplice/build.py TE01          first: the DSP section, out/TE01/TE01-dsp/
    venv/bin/python scripts/te01_build.py [--name TE01] [--out out/TE01]

  echo-names: scripts/te01_make_echo.py writes its mod.json (every site checked against the stock image), the SDK
  builds it into NAME/mods/; then elekloader.patch links BA05's twelve .elemod files (sha256-checked against BA05's
  manifest) and echo-names with --version NAME; then section 7 is replaced by out/TE01/TE01-dsp/section7.bin.

Writes NAME.syx (+ .sha256), NAME.main.bin, build-NAME.json, build/, mods/. Checks (all must hold, or no .syx is
written):
  - the pins: stock 1.12, BA05.syx, its main OS image, manifest and build record (DC02's DSP);
  - the TE01 DSP build: its record (section7.bin and the DSP .syx), no layout problem, every code section where our
    code ran on the unit, no stock hook kept, every function reached, the diet DC02's plus the echo's five never-called
    functions and its helper copies, the fit (every echo function placed), the stubs, the sources DC02's plus the echo's;
  - the DSP against DC02's: the final images the same but in the four regions after DC02's code (the echo, the delay's
    stubs) and the delay's two CJUMPs; DC02's sections in their places and sizes;
  - the mods: BA05's twelve .elemod files (sha256) in BA05's order, then echo-names 0.1; elekloader's lint;
  - the container: the ELE3 header BA05's but the version field; sections 2, 4, 5, 8 stored byte for byte as in
    BA05.syx; section 7 stored as in TE01-dsp.syx and depacking to its section7.bin; section 3 as elekloader packed it;
    the main OS depacks to the patcher's image, and both main OS images (BA05's and NAME's) rebuild from their .elemod
    files, layouts and maps byte for byte;
  - against BA05's main OS: every relocated field of the twelve mods names the same place in both links; in the stock
    part every byte that differs is inside one of echo-names' sites or a relocated operand of another mod whose target
    moved with the layout; the descriptor table and the grown table pg_boot builds are BA05's but the fields
    echo-names' descriptor sites write; the layout BA05's plus echo-names';
  - echo-names' 32 sites as its mod.json says, with the symbols they name, and its strings.
"""
import argparse
import hashlib
import json
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scripts'))
sys.dont_write_bytecode = True
import t65_build as T  # noqa: E402
import ob01_build as OB  # noqa: E402
import fl01_build as F1  # noqa: E402
import sp02_build as S2  # noqa: E402
sys.path.insert(1, os.path.join(ROOT, 'tools', 'dspsplice'))
sys.path.insert(2, os.path.join(ROOT, 'tools', 'sharcvm'))

TOOL, STOCK, STOCK_SHA, EL, ELPY = T.TOOL, T.STOCK, T.STOCK_SHA, T.EL, T.ELPY
LOAD, DESC, ROW, ROWS0, ROWS, STOCK_END = T.LOAD, T.DESC, T.ROW, T.ROWS0, T.ROWS, T.STOCK_END
BA05 = os.path.join(ROOT, 'out', 'BA05')
BA05_SYX, BA05_SYX_SHA = os.path.join(BA05, 'BA05.syx'), '1a345a94270797bf3e6e43f4b52bdf1b2303294b3f62e0b60714cae8a968eaf8'
BA05_MAIN, BA05_JSON = os.path.join(BA05, 'BA05.main.bin'), os.path.join(BA05, 'build-BA05.json')
BA05_MAN = os.path.join(BA05, 'build', 'BA05-main.syx.json')
BA05_MAP = os.path.join(BA05, 'build', 'BA05-main.syx.map.json')
DC02_DSP = os.path.join(ROOT, 'out', 'DC02', 'DC02-dsp')
DC02_SEC7_SHA = '9fd315f922f4ea71952d3e0074132ac3df50ae532ba058da39bf032e677b1026'
TE01_DSP = os.path.join(ROOT, 'out', 'TE01', 'TE01-dsp')
MODDIR = os.path.join(EL, 'examples', 'echo-names-dn2')            # 0.1
NEW_SRC = ['fx/spaceecho.c', 'fx/spaceecho.h', 'tools/dspsplice/src/se_glue.c']   # fxmath.h, fxio.h: DC02's already
ECHO_DROP = ['spaceecho:spaceecho_heads', 'spaceecho:spaceecho_center_hz', 'spaceecho:spaceecho_bw_oct',
             'spaceecho:spaceecho_q', 'spaceecho:spaceecho_trim']
L1 = 0x28000000
# DC02's code ends in the four proven regions (out/DC02/DC02-dsp/build.json), and the proven ends (build.py PROVEN)
TAILS = [(0x2e0c3a, 0x2e1c36, 'block 1 after DC02\'s code'), (0x2e49ae, 0x2e4f46, 'code4 after DC02\'s code'),
         (0x30e3c6, 0x30ff76, 'code3 after DC02\'s code'), (0x31048e, 0x315f8c, 'code2 after flg_sync.c')]
# the descriptor row's fields (T.row_fields) by offset and size
FIELDS = [(0x00, 4, 'group'), (0x04, 4, 'k'), (0x08, 4, 'min'), (0x0c, 4, 'max'), (0x10, 4, 'default'),
          (0x14, 4, 'bipolar'), (0x18, 2, 'cc'), (0x1a, 2, 'cc2'), (0x1c, 4, 'nrpn'), (0x20, 4, 'uid'),
          (0x24, 4, 'modmask'), (0x28, 4, 'name'), (0x2c, 4, 'group_name'), (0x30, 4, 'short'), (0x34, 4, 'fmt'),
          (0x38, 4, 'suffix')]
log = T.log


def sha(b):
    return hashlib.sha256(b).hexdigest()


def dsp_identity(checks):
    """TE01's DSP final image against DC02's: every differing byte in the four regions after DC02's code or in the
    delay's two hook CJUMPs"""
    import build as BD  # noqa: E402
    import ldr  # noqa: E402
    d1 = F1.dsp_regions(open(os.path.join(DC02_DSP, 'section7.bin'), 'rb').read())
    s1 = F1.dsp_regions(open(os.path.join(TE01_DSP, 'section7.bin'), 'rb').read())

    def span(regs, lo, hi):
        o = [None] * (hi - lo)
        for (s0, e0), d in regs.items():
            x0, x1 = max(s0, lo), min(e0, hi)
            if x0 < x1:
                o[x0 - lo:x1 - lo] = list(d[x0 - s0:x1 - s0])
        return o
    merged = []
    for lo, hi in sorted(set(d1) | set(s1)):
        if merged and lo <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], hi)
        else:
            merged.append([lo, hi])
    diffs = []
    for lo, hi in merged:
        if (lo, hi) in d1 and (lo, hi) in s1 and d1[(lo, hi)] == s1[(lo, hi)]:
            continue
        x, y = span(d1, lo, hi), span(s1, lo, hi)
        for i in range(hi - lo):
            if x[i] != y[i]:
                if diffs and diffs[-1][1] == lo + i:
                    diffs[-1][1] += 1
                else:
                    diffs.append([lo + i, lo + i + 1])
    parts = [(L1 + lo, L1 + hi, w) for lo, hi, w in TAILS]
    hooks = [(ldr.loader_addr(2 * site), ldr.loader_addr(2 * site) + 6, 'hook CJUMP at SW %#x (%s)' % (site, label))
             for site, _, label in BD.RECIPES['TE01']['hooks'] if label.startswith('fxh_delay')]
    why, stray = {}, []
    for a, b in diffs:
        hit = [w for lo, hi, w in parts + hooks if lo <= a and b <= hi]
        if hit:
            why.setdefault(hit[0], [0, 0])
            why[hit[0]][0] += 1
            why[hit[0]][1] += b - a
        else:
            stray.append('%#x-%#x' % (a, b))
    checks['DSP: every byte of the final image that differs from DC02\'s (%d ranges, %d B) is in one of the four '
           'regions after DC02\'s code or a delay hook CJUMP (%s)' % (
               len(diffs), sum(b - a for a, b in diffs),
               '; '.join('%s: %d ranges %d B' % (k, v[0], v[1]) for k, v in why.items()))] = not stray and len(why) == 6
    jd = json.load(open(os.path.join(DC02_DSP, 'build.json')))
    js = json.load(open(os.path.join(TE01_DSP, 'build.json')))
    pd = {r['section']: (int(r['core'], 16), r['bytes']) for r in jd['placed']}
    ps = {r['section']: (int(r['core'], 16), r['bytes']) for r in js['placed']}
    ends = {'t38_code': 0x2e0c3a, 't38_code4': 0x2e49ae, 't38_code3': 0x30e3c6, 't38_code2': 0x31048e}
    checks['DSP: every code section starts where DC02\'s does and holds at least its bytes (%s); the L1 data DC02\'s '
           '(%#x +%d)' % (', '.join('%s %#x +%d -> +%d' % (k, ps[k][0], pd[k][1], ps[k][1]) for k in ends),
                          ps['t38_data'][0], ps['t38_data'][1])] = (
        set(ps) == set(pd) and ps['t38_data'] == pd['t38_data']
        and all(ps[k][0] == pd[k][0] and pd[k][0] + pd[k][1] == ends[k] and ps[k][1] >= pd[k][1] for k in ends))
    hk = {p['why'].split(':')[0]: (p['old'], p['new']) for p in jd['patches']}
    hs = {p['why'].split(':')[0]: (p['old'], p['new']) for p in js['patches']}
    dly = sorted(set(hs) - set(hk))
    checks['DSP: DC02\'s four hook CJUMPs patched the same, and the delay\'s two (%s)' % ', '.join(dly)] = (
        all(hs.get(k) == v for k, v in hk.items()) and len(dly) == 2)
    return {'diff_ranges': len(diffs), 'diff_bytes': sum(b - a for a, b in diffs), 'by_part': why, 'stray': stray,
            'ranges': ['%#x-%#x' % (a, b) for a, b in diffs],
            'placed_dc02': {k: ['%#x' % a, n] for k, (a, n) in pd.items()},
            'placed_te01': {k: ['%#x' % a, n] for k, (a, n) in ps.items()}}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--name', default='TE01')
    ap.add_argument('--out', default=os.path.join(ROOT, 'out', 'TE01'))
    a = ap.parse_args()
    if len(a.name) != 4:
        raise SystemExit('--name: 4 characters')
    out = os.path.abspath(a.out)
    mods_dir, build_dir = os.path.join(out, 'mods'), os.path.join(out, 'build')
    os.makedirs(mods_dir, exist_ok=True)
    os.makedirs(build_dir, exist_ok=True)
    env = dict(os.environ, ELEKLOADER_CROSS='m68k-elf-')
    checks = {}

    # ---- pins
    bj = json.load(open(BA05_JSON))
    bman = json.load(open(BA05_MAN))
    bmp = json.load(open(BA05_MAP))
    for p, want in ((STOCK, STOCK_SHA), (BA05_SYX, BA05_SYX_SHA), (BA05_MAIN, bj['main_sha256']),
                    (os.path.join(DC02_DSP, 'section7.bin'), DC02_SEC7_SHA)):
        if sha(open(p, 'rb').read()) != want:
            raise SystemExit('%s: sha256 is not the expected one' % p)
    checks['BA05 pinned: BA05.syx %s..., its main OS %s..., its manifest for that main OS, every check of its build '
           'held; its DSP is DC02\'s section 7 %s...' % (BA05_SYX_SHA[:8], bj['main_sha256'][:8], DC02_SEC7_SHA[:8])] = (
        bj['syx']['sha256'] == BA05_SYX_SHA and bman['main_sha256'] == bj['main_sha256']
        and bj['section7']['sha256'] == DC02_SEC7_SHA and all(bj['checks'].values()))

    # ---- the TE01 DSP build
    jd = json.load(open(os.path.join(DC02_DSP, 'build.json')))
    js = json.load(open(os.path.join(TE01_DSP, 'build.json')))
    sec7 = os.path.join(TE01_DSP, 'section7.bin')
    dsyx = os.path.join(ROOT, js['syx']['path'])
    sec7_sha = sha(open(sec7, 'rb').read())
    checks['TE01 DSP: build.json records section7.bin (%s...) and %s' % (sec7_sha[:8], js['syx']['path'])] = (
        js['name'] == 'TE01' and js['section7']['sha256'] == sec7_sha
        and js['syx']['sha256'] == sha(open(dsyx, 'rb').read()))
    checks['TE01 DSP: the layout check reports no problems, every code section where our code ran on the unit (%s), '
           'no stock hook kept' % ', '.join('%s %s' % (r['section'], r['core']) for r in js['proven_check'])] = (
        not js['layout_check']['problems'] and all(r['proven'] for r in js['proven_check'])
        and not js['stock_hooks'] and not js['l2_patched'])
    checks['TE01 DSP: every function linked is reached from the hooks (%d functions)' % len(js['reach'])] = (
        all(r['reached'] for r in js['reach']))
    ds, dd = js['diet'], jd['diet']
    checks['TE01 DSP: the diet DC02\'s plus the echo\'s (dropped %s; copies of %s removed)' % (
        ', '.join(x.split(':')[1] for x in ds['dropped'] if x not in dd['dropped']),
        ', '.join(x for x in ds['removed_copies'] if x not in dd['removed_copies']))] = (
        [x for x in ds['dropped'] if x not in ECHO_DROP] == dd['dropped']
        and sorted(x for x in ds['dropped'] if x in ECHO_DROP) == sorted(ECHO_DROP)
        and [x for x in ds['removed_copies'] if not x.startswith(('spaceecho:', 'se_glue:'))] == dd['removed_copies']
        and ds['shared_from'] == dd['shared_from'] and ds['shared'] == dd['shared'])
    fit = js['fit']
    fns = [r for r in js['functions'] if r['region'] != 'drop']
    sec_of = {'c1': 't38_code', 'c2': 't38_code2', 'c3': 't38_code3', 'c4': 't38_code4'}
    used = {g: sum(r['bytes'] for r in fns if r['region'] == g) for g in sec_of}
    # the linker aligns each function's section, so the bytes as linked can be a parcel or two more than measured; the
    # proven check above holds every section inside its region whatever the count
    checks['TE01 DSP: the fit placed all %d echo functions in the room DC02 leaves (%s), as linked %s B' % (
        fit['functions'], ', '.join('%s %d' % kv for kv in fit['room'].items()),
        ', '.join('%s %d' % kv for kv in used.items()))] = (
        len(fns) == fit['functions'] and all(r['section'] == sec_of[r['region']] for r in fns)
        and all(used[g] <= fit['room'][g] for g in sec_of))
    s2, sd = jd['sources'], js['sources']
    checks['TE01 DSP: the sources DC02\'s plus %s, each one recorded as it is on disk' % ', '.join(NEW_SRC)] = (
        {k: v for k, v in sd.items() if k not in NEW_SRC} == s2 and set(NEW_SRC) <= set(sd)
        and all(sd[f] == sha(open(os.path.join(ROOT, f), 'rb').read()) for f in sd))
    stubs = [r['function'] for r in js['function_table'] if r['file'] in ('stubs', 'stubs2', 'stubs3')]
    checks['TE01 DSP: the stubs are the chorus slot\'s two (block 1), the compressor\'s two (code3) and the delay '
           'slot\'s two (code2): %s' % ', '.join(stubs)] = sorted(stubs) == sorted(
               ['fxh_chorus', 'fxh_chorus_init', 'fxh_comp', 'fxh_comp_init', 'fxh_delay', 'fxh_delay_init'])
    dspid = dsp_identity(checks)

    # ---- echo-names: mod.json checked against the stock image, the SDK build
    T.run([sys.executable, os.path.join(ROOT, 'scripts', 'te01_make_echo.py')])
    F1.sdk_build(MODDIR, mods_dir, env)
    en_file = os.path.join(mods_dir, 'echo-names-0.1.elemod')
    where = [os.path.join(ROOT, 'out', d, 'mods') for d in ('BA05', 'DJ01', 'FL02', 'FL01', 'OB01')] + [
        os.path.join(ROOT, 'out', 'CMP4', 'main', 'mods')]
    files, ids = [], []
    for m in bman['mods']:
        hit = [os.path.join(d, m['file']) for d in where if os.path.exists(os.path.join(d, m['file']))
               and sha(open(os.path.join(d, m['file']), 'rb').read()) == m['sha256']]
        checks['BA05\'s %s %s: its .elemod (sha256 %s...) %s' % (m['id'], m['version'], m['sha256'][:8],
                                                                os.path.relpath(hit[0], ROOT) if hit else 'NOT FOUND')] = bool(hit)
        if not hit:
            raise SystemExit('%s: no file with BA05\'s sha256' % m['file'])
        files.append(hit[0])
        ids.append(m['id'])
    files.append(en_file)
    ids.append('echo-names')
    T.run([ELPY, '-m', 'elekloader.lint'] + files + ['--stock', STOCK], cwd=EL, env=env)
    mainsyx = os.path.join(build_dir, a.name + '-main.syx')
    cmd = [ELPY, '-m', 'elekloader.patch', '--stock', STOCK]
    for f in files:
        cmd += ['--mod', f]
    T.run(cmd + ['--version', a.name, '--out', mainsyx], cwd=EL, env=env)
    man = json.load(open(mainsyx + '.json'))
    mp = json.load(open(mainsyx + '.map.json'))

    # ---- the container
    syx = os.path.join(out, a.name + '.syx')
    tmp = syx + '.tmp'
    T.run([TOOL, '-i', mainsyx, '-c', '7', sec7, '-o', tmp])
    try:
        bas, _ = T.sections(BA05_SYX)
        fn, _ = T.sections(tmp)
        mn, _ = T.sections(mainsyx)
        st, _ = T.sections(STOCK)
        te7, _ = T.sections(dsyx)
        hdr, h2 = T.header(tmp), T.header(BA05_SYX)
        checks['ELE3 header: version field %s, the rest as BA05\'s' % a.name] = (
            hdr[0x14:0x18] == a.name.encode() and hdr[:0x14] == h2[:0x14] and hdr[0x18:] == h2[0x18:])
        checks['section ids as BA05\'s'] = sorted(fn) == sorted(bas)
        for i in sorted(bas):
            if i not in (3, 7):
                checks['section %d stored byte for byte as in BA05.syx%s' % (i, ' (and stock)' if fn[i] == st[i]
                                                                            else '')] = fn[i] == bas[i]
        checks['section 7 stored byte for byte as in %s' % js['syx']['path']] = fn[7] == te7[7]
        checks['section 7 depacks to %s (sha256 %s...)' % (os.path.relpath(sec7, ROOT), sec7_sha[:8])] = (
            T.depack7(tmp) == open(sec7, 'rb').read())
        checks['section 3 stored as elekloader packed it'] = fn[3] == mn[3]
        img = T.main_image(tmp)
        checks['the main OS depacks to the patcher\'s image (%s...)' % man['main_sha256'][:8]] = (
            sha(img) == man['main_sha256'])
        mids = [(m['id'], m['version']) for m in man['mods']]
        checks['mods: BA05\'s twelve in BA05\'s order, then echo-names 0.1 (%s)' % ', '.join('%s %s' % x for x in mids)] = (
            [m['id'] for m in man['mods']] == ids and mids[-1] == ('echo-names', '0.1')
            and [(m['id'], m['version'], m['sha256']) for m in man['mods'][:-1]]
            == [(m['id'], m['version'], m['sha256']) for m in bman['mods']]
            and man['mods'][-1]['sha256'] == sha(open(en_file, 'rb').read()))

        bimg = open(BA05_MAIN, 'rb').read()
        stock_img = open(T.STOCK_MAIN, 'rb').read()
        el_b = {m['id']: OB.load_elemod(f) for m, f in zip(bman['mods'], files)}
        el_n = {m['id']: OB.load_elemod(f) for m, f in zip(man['mods'], files)}
        checks['BA05\'s main OS = its twelve .elemod files relinked from its manifest\'s layout and map, byte for '
               'byte'] = OB.reconstruct(stock_img, bman, bmp, el_b) == bimg
        checks['%s\'s main OS = its thirteen .elemod files relinked from its layout and map, byte for byte' % a.name] = (
            OB.reconstruct(stock_img, man, mp, el_n) == img)

        # every relocated field of the twelve unchanged mods: the same place in both links
        lo_, ln_ = F1.Link(bman, bmp, el_b), F1.Link(man, mp, el_n)
        fo, fnw = S2.own_ends(lo_, el_b, lo_.fields()), S2.own_ends(ln_, el_n, ln_.fields())
        same = [k for k in fo]
        moved = [k for k in same if k in fnw and fo[k][0] != fnw[k][0]]
        bad = [k for k in same if k not in fnw or fo[k][1] != fnw[k][1]]
        checks['every relocated field of BA05\'s twelve mods names the same place in both links (%d fields, %d moved '
               'with the layout)' % (len(same), len(moved))] = (
            not bad and set(same) == {k for k in fnw if k[0] != 'echo-names'})

        # echo-names' sites: as mod.json says
        en = el_n['echo-names']
        sym = {k: int(v, 16) for k, v in mp.items()}
        mj = json.load(open(os.path.join(MODDIR, 'mod.json')))
        sites = {int(s_['addr'], 16): s_ for s_ in en['sites']}

        def at(addr, n):
            return img[addr - LOAD:addr - LOAD + n]
        want = {}
        for s_ in mj['sites']:
            x, stockb = int(s_['addr'], 16), bytes.fromhex(s_['stock'])
            if s_['op'] == 'keep2':
                want[x] = stockb[:2] + struct.pack('>I', sym[s_['target']])
            elif s_['op'] == 'ptr':
                want[x] = struct.pack('>I', sym[s_['target']])
            elif s_['op'] == 'jsr':
                want[x] = bytes.fromhex('4eb9') + struct.pack('>I', sym[s_['target']]) + stockb[6:]
            else:
                want[x] = bytes.fromhex(s_['new'])
        got = {x: at(x, len(w)) for x, w in want.items()}
        checks['echo-names: its %d sites written as its mod.json says (%s)' % (len(want), ', '.join(
            '0x%08x %s' % (x, got[x].hex()) for x in sorted(want)))] = (
            set(sites) == set(want) and len(want) == 32 and all(got[x] == w for x, w in want.items())
            and all(sites[x]['len'] == len(w) for x, w in want.items()))
        lk = T.Linked(img, mp)
        lkb = T.Linked(bimg, bmp)
        names = ('fxe_echo', 'fxe_ech', 'fxe_echo_s', 'fxe_echo_send', 'fxe_echo_mix', 'fxe_time', 'fxe_mode',
                 'fxe_mode_s', 'fxe_wid', 'fxe_int', 'fxe_cntr', 'fxe_cntr_s', 'fxe_bw', 'fxe_bw_s', 'fxe_age',
                 'fxe_age_s')
        strs = {k: lk.string(sym[k]) for k in names}
        checks['echo-names: its strings (%s)' % ', '.join('%s "%s"' % kv for kv in strs.items())] = (
            list(strs.values()) == ['Echo', 'ECH', 'ECHO', 'Echo Send', 'Echo Mix Vol.', 'Echo Time', 'Echo Mode',
                                    'MODE', 'Width/Pan', 'Intensity', 'Band Center', 'CNTR', 'Bandwidth', 'BW',
                                    'Echo Age', 'AGE'])

        # the stock part: every differing byte in one of echo-names' sites, or in a relocated field of another mod's
        # site whose target moved with the layout (the check above: it names the same place)
        rng = T.diff_ranges(bimg[:STOCK_END - LOAD], img[:STOCK_END - LOAD])
        spans = [(x, s_['len']) for x, s_ in sites.items()]
        relo = []
        for mid, d in el_b.items():
            for s_ in d['sites']:
                for off, typ, tgt, add in s_['relocs']:
                    k = (mid, 'site ' + s_['addr'], off)
                    if k in moved:
                        relo.append((int(s_['addr'], 16) + off, 4 if typ in ('abs32', 'pc32') else 2, mid))
        outside = [(x, n) for x, n in rng if not all(any(sa <= y < sa + sn_ for sa, sn_ in spans) or
                                                     any(ra <= y < ra + rn for ra, rn, _ in relo)
                                                     for y in range(x, x + n))]
        ours = [r for r in rng if any(sa <= r[0] < sa + sn_ for sa, sn_ in spans)]
        theirs = [r for r in rng if r not in ours]
        checks['stock part: %d ranges (%d bytes) differ from BA05: %d inside echo-names\' sites (%s); %d are '
               'relocated operands of other mods\' sites whose target moved with the layout (%s)' % (
                   len(rng), sum(n for _, n in rng), len(ours), ', '.join('0x%08x+%d' % r for r in ours), len(theirs),
                   ', '.join('0x%08x+%d %s' % (r[0], r[1], [m_ for ra, rn, m_ in relo if ra <= r[0] < ra + rn][0])
                             for r in theirs if any(ra <= r[0] < ra + rn for ra, rn, _ in relo)))] = not outside

        # the descriptor table and the grown table: BA05's but the fields echo-names' descriptor sites write
        def named(lk_, d):
            f = T.row_fields(d)
            for k_ in ('name', 'group_name', 'short', 'suffix'):
                f[k_] = lk_.string(f[k_])
            if lk_.run_start <= f['fmt'] < lk_.run_end:
                f['fmt'] = sorted(k for k, x in lk_.mp.items() if int(x, 16) == f['fmt'] and not k.startswith('__'))
            return f
        want_diff = set()
        for x, s_ in sites.items():
            if DESC <= x < DESC + ROWS0 * ROW:
                r, off = divmod(x - DESC, ROW)
                want_diff |= {(r, f_) for o, n, f_ in FIELDS if o < off + s_['len'] and off < o + n}
        want_diff = sorted(want_diff)
        lo, hi = DESC - LOAD, DESC - LOAD + ROWS0 * ROW
        tab_diff = []
        for r in range(ROWS0):
            x, y = named(lk, img[lo + r * ROW:lo + (r + 1) * ROW]), named(lkb, bimg[lo + r * ROW:lo + (r + 1) * ROW])
            for k_ in x:
                if x[k_] != y[k_]:
                    tab_diff.append((r, k_, y[k_], x[k_]))
        checks['the descriptor table: BA05\'s (names as strings, formatters by name) but the fields echo-names\' '
               'descriptor sites write (%s)' % '; '.join('0x%x %s %s -> %s' % d for d in tab_diff)] = (
            sorted((r, k_) for r, k_, _, _ in tab_diff) == want_diff and len(want_diff) > 0)
        table, rows = T.simulate_boot(lk, stock_img)
        table_o, rows_o = T.simulate_boot(lkb, stock_img)
        grown_diff = sorted((r, k_) for r in range(ROWS) for k_, v in named(lk, table[r * ROW:(r + 1) * ROW]).items()
                            if v != named(lkb, table_o[r * ROW:(r + 1) * ROW])[k_])
        checks['the grown table pg_boot builds (simulated, %d rows): BA05\'s, field by field, but the same fields; the '
               'added rows the same' % ROWS] = (
            grown_diff == want_diff and table[:ROWS0 * ROW] == img[lo:hi]
            and [(r['id'], named(lk, r['desc'])) for r in rows] == [(r['id'], named(lkb, r['desc'])) for r in rows_o])

        # the layout
        lay_o, lay_n = bman['link']['layout'], man['link']['layout']
        sec_moved = sorted('%s %+d' % (k, lay_n['sections'][k] - v) for k, v in lay_o['sections'].items()
                           if lay_n['sections'].get(k) != v)
        new_secs = sorted(set(lay_n['sections']) - set(lay_o['sections']))
        checks['layout: BA05\'s sections and echo-names\' %s; the blob %d -> %d B; moved: %s' % (
            new_secs, lay_o['blob_len'], lay_n['blob_len'], ', '.join(sec_moved) or 'none')] = (
            set(lay_o['sections']) <= set(lay_n['sections']) and all(k.startswith('echo-names ') for k in new_secs)
            and lay_n['ddr'][1] <= lay_n['ddr'][0] + lay_n['ddr_size'])

        for k, v in checks.items():
            print('%-4s %s' % ('ok' if v else 'FAIL', k))
        if not all(checks.values()):
            for x in bad[:20]:
                print('  moved to another place: %s %s -> %s' % (x, fo[x], fnw.get(x)))
            print('  outside sites: %s' % outside[:20])
            print('  table diff: %s' % tab_diff[:30])
            print('  want diff: %s' % want_diff[:30])
            print('  grown diff: %s' % grown_diff[:30])
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
            'base': 'BA05 ' + BA05_SYX_SHA, 'mods': man['mods'], 'dsp_vs_dc02': dspid, 'dsp_fit': js['fit'],
            'main_vs_ba05': {'stock_part_ranges': ['0x%08x +%d' % r for r in rng],
                             'stock_part_ours': ['0x%08x +%d' % r for r in ours],
                             'stock_part_moved_operands': ['0x%08x +%d' % r for r in theirs],
                             'blob_len': [lay_o['blob_len'], lay_n['blob_len']],
                             'moved_fields': len(moved), 'sections_moved': sec_moved, 'new_sections': new_secs,
                             'descriptor_table_diff': [['0x%x' % r, k_, str(y), str(x)] for r, k_, y, x in tab_diff]},
            'symbols': {k: mp[k] for k in sorted(mp) if k.startswith('fxe_')},
            'checks': checks, 'log': log}
    json.dump(info, open(os.path.join(out, 'build-%s.json' % a.name), 'w'), indent=1)
    print('DSP vs DC02: %d ranges, %d B: %s' % (dspid['diff_ranges'], dspid['diff_bytes'], dspid['by_part']))
    print('main OS vs BA05, stock part: ' + ', '.join('0x%08x +%d' % r for r in rng))
    print('%s  %s' % (s, os.path.relpath(syx, ROOT)))


if __name__ == '__main__':
    main()
