#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""FL02: FL01 with the stock compressor's glyphs on the 3-band compressor's knobs (notes/FL02.md) and the flanger's
speed in sync with the tempo, FUNC + turn on SPD (notes/T5.6-flanger-retune.md section 7).

    venv/bin/python tools/dspsplice/build.py FL02          first: the DSP section, out/FL02/FL02-dsp/
    venv/bin/python scripts/fl02_build.py [--name FL02] [--out out/FL02]

  FL02 DSP section = recipe FL02 (tools/dspsplice/build.py): FL01's with tools/dspsplice/src/flg_glue3.c in place of
  flg_glue2.c (block 1, the same place) and tools/dspsplice/src/flg_sync.c in code2 from 0x310000 (proven: the tape
  delay ran there on FTD1 to CMP3).
  FL02 main OS = FL01's seven mods (the same .elemod files, sha256-checked against FL01's manifest) with
  param-rows-dn2 0.4 in place of 0.3 (each band knob borrows its stock compressor row's display entry: THR 0x96,
  ATK 0x97, REL 0x98, RAT 0x9a), comp-ui-dn2 0.3 in place of 0.2 (the ratio knobs' glyph function: the stock ratio
  frames for 1.50 .. 20.00, a ninth frame for 1.00) and flanger-sync-dn2 0.1 added last (row 0x6a, SPD: the value text
  names the synced speeds, and its FUNC + turn function; scripts/fl02_make_mod.py). The ELE3 version field is the
  build's name (FL02).

Writes NAME.syx (+ .sha256), NAME.main.bin, build-NAME.json, and mods/, build/. Checks (all must hold, or nothing is
written):
  - the pins: stock 1.12, FL01.syx and its main OS image, FL01's DSP section, the FL02 DSP build (its record, its
    layout and placement checks, the sources FL01's but the glue: flg_glue2.c -> flg_glue3.c, flg_sync.c, flg_slot3.h);
  - the container: ELE3 header as stock but the version field; sections 2, 4, 5, 8 stored as stock; section 7 stored
    as in FL02-dsp.syx and depacking to its section7.bin; section 3 as elekloader packed it;
  - the mods: FL01's .elemod files (sha256) for core, flanger-names 0.2, param-grow, comp-link and os-version;
    param-rows 0.4, comp-ui 0.3 and flanger-sync 0.1 built here; comp-ui 0.3 with param-rows 0.3 is refused; both
    main OS images (FL01's and FL02's) rebuilt from their .elemod files, manifest layout and map, byte for byte;
  - param-rows 0.4 against 0.3: the same .run bytes and relocations but the look field (+0x40) of the twelve THR, RAT,
    ATK and REL rows (0x99 -> 0x96, 0x9a, 0x97, 0x98); comp-ui 0.3 against 0.2: the same five sites (stock bytes,
    targets by name), the same three page records (rows, names) and page list;
  - against FL01's main OS: every relocated field of the five unchanged mods names the same place in both links (the
    same mod section and offset, the same stock address or symbol); in the stock part every byte that differs is in a
    site; the descriptor table and the grown table pg_boot builds are FL01's but row 0x6a's formatter (+0x34: fls_fmt);
  - flanger-sync's two sites: row 0x6a +0x34 -> fls_fmt, and 0x400c5c96 `pea fls_tpl` (then the stock
    `pea 0x42435eb8; jsr (a4)`), fls_tpl = {a pointer, 0, the stock manager 0x400bfbec, fls_func}; its 12 synced
    values = the 12 the DSP's flg_sync.c compares (read from the source) = (a << 8) | i;
  - the DSP against FL01's: the final images the same but block 1's glue and code2: every differing byte inside
    FL01's or FL02's glue (from 0x2e15d4) or in code2 from 0x310000 (FL02's flg_sync.c, stock bytes on FL01); the
    stubs' calls the same (flg_init and flg_process stay at FL01's addresses).
"""
import argparse
import hashlib
import json
import os
import re
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scripts'))
sys.dont_write_bytecode = True
import t65_build as T  # noqa: E402
import ob01_build as OB  # noqa: E402
import fl01_build as F1  # noqa: E402
sys.path.insert(1, os.path.join(ROOT, 'tools', 'dspsplice'))
sys.path.insert(2, os.path.join(ROOT, 'tools', 'sharcvm'))

EL, ELPY, TOOL, STOCK, STOCK_SHA = T.EL, T.ELPY, T.TOOL, T.STOCK, T.STOCK_SHA
LOAD, STOCK_END, DESC, ROW, ROWS0, ROWS = T.LOAD, T.STOCK_END, T.DESC, T.ROW, T.ROWS0, T.ROWS
FL01 = os.path.join(ROOT, 'out', 'FL01')
FL01_SYX, FL01_SYX_SHA = os.path.join(FL01, 'FL01.syx'), 'a2179c0cc5dc02d7d3816ee13aeb0cdf6a1908aef4e06aaf0322a973c8d75daf'
FL01_MAIN, FL01_JSON = os.path.join(FL01, 'FL01.main.bin'), os.path.join(FL01, 'build-FL01.json')
FL01_MAN = os.path.join(FL01, 'build', 'FL01-main.syx.json')
FL01_MAP = os.path.join(FL01, 'build', 'FL01-main.syx.map.json')
FL01_DSP = os.path.join(FL01, 'FL01-dsp')
FL02_DSP = os.path.join(ROOT, 'out', 'FL02', 'FL02-dsp')
CMP4_MODS = os.path.join(ROOT, 'out', 'CMP4', 'main', 'mods')
OB01_OSV = os.path.join(ROOT, 'out', 'OB01', 'mods', 'os-version-0.1.elemod')
FL01_FLN = os.path.join(FL01, 'mods', 'flanger-names-0.2.elemod')
MODDIR = {'param-rows': os.path.join(EL, 'examples', 'param-rows-dn2'),
          'comp-ui': os.path.join(EL, 'examples', 'comp-ui-dn2'),
          'flanger-sync': os.path.join(EL, 'examples', 'flanger-sync-dn2')}
NEWVER = {'param-rows': '0.4', 'comp-ui': '0.3', 'flanger-sync': '0.1'}
ROW_SPD = 0x6a
FMT_NUMBER = 0x400e3030
SITE_TPL, TPL_STOCK, ENTRY_SPD34, MANAGER = 0x400c5c96, 0x42432ee8, 0x4243425c + 0x44 * 0x6a + 0x34, 0x400bfbec
# param-rows: row n (0..19) of param_rows_comp at n x 0x54; look at +0x40; the twelve that change, and to what
LOOKS = {}
for b_, base in enumerate((0, 6, 12)):
    LOOKS.update({base + 0: 0x96, base + 1: 0x9a, base + 2: 0x97, base + 3: 0x98})
GLUE2, GLUE3 = 'tools/dspsplice/src/flg_glue2.c', 'tools/dspsplice/src/flg_glue3.c'
NEW_DSP_SRC = (GLUE3, 'tools/dspsplice/src/flg_sync.c', 'tools/dspsplice/src/flg_slot3.h')
L1 = 0x28000000
GLUE_START = 0x2e15d4                 # FL01's (and CMP4's) glue starts here, after fx/flanger.c
log = T.log


def sha(b):
    return hashlib.sha256(b).hexdigest()


def sdk_build(moddir, out_dir, env):
    T.run([ELPY, '-m', 'elekloader.sdk.build', moddir, '--stock', STOCK, '--out', out_dir], cwd=EL, env=env)


def refused(files, env, build_dir, want):
    """the patcher refuses this mod set, saying `want`"""
    import subprocess
    out = os.path.join(build_dir, 'refused-test.syx')
    cmd = [ELPY, '-m', 'elekloader.patch', '--stock', STOCK]
    for f in files:
        cmd += ['--mod', f]
    p = subprocess.run(cmd + ['--version', 'TEST', '--out', out], cwd=EL, env=env, capture_output=True, text=True)
    for x in (out, out + '.json', out + '.map.json'):
        if os.path.exists(x):
            os.remove(x)
    msg = (p.stdout + p.stderr).strip().splitlines()
    return p.returncode != 0 and any(want in m for m in msg), (msg[-1] if msg else '')


def dsp_identity(checks):
    """FL02's DSP final image against FL01's: every differing byte in the glue or in code2"""
    import elf as E  # noqa: E402  (tools/sharcvm)
    f1 = F1.dsp_regions(open(os.path.join(FL01_DSP, 'section7.bin'), 'rb').read())
    f2 = F1.dsp_regions(open(os.path.join(FL02_DSP, 'section7.bin'), 'rb').read())

    def span(regs, lo, hi):
        """the image's bytes at lo..hi, None where it loads nothing"""
        o = [None] * (hi - lo)
        for (s0, e0), d in regs.items():
            x0, x1 = max(s0, lo), min(e0, hi)
            if x0 < x1:
                o[x0 - lo:x1 - lo] = list(d[x0 - s0:x1 - s0])
        return o
    # every range either image loads, merged; compared byte by byte where the two images' regions differ
    spans = sorted(set(f1) | set(f2))
    merged = []
    for lo, hi in spans:
        if merged and lo <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], hi)
        else:
            merged.append([lo, hi])
    ranges = []
    for lo, hi in merged:
        if (lo, hi) in f1 and (lo, hi) in f2 and f1[(lo, hi)] == f2[(lo, hi)]:
            continue
        x, y = span(f1, lo, hi), span(f2, lo, hi)
        for i in range(hi - lo):
            if x[i] != y[i]:
                a_ = lo + i - L1
                if ranges and a_ == ranges[-1][1]:
                    ranges[-1][1] = a_ + 1
                else:
                    ranges.append([a_, a_ + 1])
    ranges = [tuple(r) for r in ranges]
    e1 = E.read_elf(os.path.join(FL01_DSP, 'build', 'FL01.dxe'))
    e2 = E.read_elf(os.path.join(FL02_DSP, 'build', 'FL02.dxe'))
    end1 = 0x2dd600 + len(next(s for s in e1.sections if s.name == 't38_code').data)
    end2 = 0x2dd600 + len(next(s for s in e2.sections if s.name == 't38_code').data)
    c2 = next(s for s in e2.sections if s.name == 't38_code2')
    c2lo, c2hi = 0x310000, 0x310000 + len(c2.data)
    glue1 = len(E.read_elf(os.path.join(FL01_DSP, 'build', 'flg_glue2.doj')).sections[0].data)
    glue2 = len(E.read_elf(os.path.join(FL02_DSP, 'build', 'flg_glue3.doj')).sections[0].data)
    s1, s2 = e1.symbols, e2.symbols
    checks['DSP: the glue starts where FL01\'s did (0x%x): FL01\'s to 0x%x (%d B), FL02\'s to 0x%x (%d B), inside the '
           'proven 0x2dd600-0x2e1c36; flg_init and flg_process at FL01\'s addresses (%s, %s)' % (
               GLUE_START, end1, glue1, end2, glue2, s2['flg_init.'] and '%#x' % s2['flg_init.'],
               '%#x' % s2['flg_process.'])] = (
        end1 - glue1 == end2 - glue2 == GLUE_START and end2 <= 0x2e1c36
        and s1['flg_init.'] == s2['flg_init.'] and s1['flg_process.'] == s2['flg_process.'])
    checks['DSP: code2 holds flg_sync.c only (%d B at 0x%x-0x%x, flg_spd at SW %#x), inside the proven '
           '0x310000-0x315f8c' % (len(c2.data), c2lo, c2hi, s2['flg_spd.'])] = (
        c2hi <= 0x315f8c and len(E.read_elf(os.path.join(FL02_DSP, 'build', 'flg_sync.doj')).sections[0].data)
        == len(c2.data) and not any(s.name == 't38_code2' and s.data for s in e1.sections))
    why, stray = [], []
    for lo, hi in ranges:
        if GLUE_START <= lo and hi <= max(end1, end2) + 2:
            why.append(('0x%x-0x%x' % (lo, hi), hi - lo, 'the glue'))
        elif c2lo <= lo and hi <= c2hi + 2:            # the loaded block ends on a 4-byte boundary
            why.append(('0x%x-0x%x' % (lo, hi), hi - lo, 'code2: flg_sync.c'))
        else:
            stray.append('0x%x-0x%x' % (lo, hi))
    same_regions = sorted(set(f1) & set(f2))
    checks['DSP: every differing byte of the final image (%d ranges, %d B) inside the glue or code2; %d regions the '
           'same bytes' % (len(ranges), sum(h - l_ for l_, h in ranges), sum(1 for k in same_regions if f1[k] == f2[k]))] = (
        not stray)
    summary = []
    for tag in ('the glue', 'code2: flg_sync.c'):
        rs = [w for w in why if w[2] == tag]
        if rs:
            summary.append(('%s-%s' % (rs[0][0].split('-')[0], rs[-1][0].split('-')[1]), sum(w[1] for w in rs),
                            '%s (%d ranges)' % (tag, len(rs))))
    return {'ranges': why, 'summary': summary, 'stray': stray, 'glue_fl01': '0x%x-0x%x' % (GLUE_START, end1),
            'glue_fl02': '0x%x-0x%x' % (GLUE_START, end2), 'code2': '0x%x-0x%x' % (c2lo, c2hi),
            'symbols': {k: '%#x' % s2[k + '.'] for k in ('flg_init', 'flg_process', 'flg_spd')}}


def sync_values_from_source():
    """the 12 words flg_sync.c compares, in FL_SYNC order"""
    src = open(os.path.join(ROOT, 'tools', 'dspsplice', 'src', 'flg_sync.c')).read()
    got = re.findall(r'if \(w == (0x[0-9a-f]+)\)\s*\n\s*sy = (\d+);', src)
    return [int(w, 16) for w, i in sorted(got, key=lambda x: int(x[1]))], [int(i) for w, i in got]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--name', default='FL02')
    ap.add_argument('--out', default=os.path.join(ROOT, 'out', 'FL02'))
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
    f1j = json.load(open(FL01_JSON))
    f1man = json.load(open(FL01_MAN))
    f1dsp = json.load(open(os.path.join(FL01_DSP, 'build.json')))
    for p, want in ((STOCK, STOCK_SHA), (FL01_SYX, FL01_SYX_SHA), (FL01_MAIN, f1j['main_sha256']),
                    (os.path.join(FL01_DSP, 'section7.bin'), f1dsp['section7']['sha256'])):
        if sha(open(p, 'rb').read()) != want:
            raise SystemExit('%s: sha256 is not the expected one' % p)
    checks['FL01\'s manifest describes FL01.main.bin (%s...), FL01.syx %s...' % (f1j['main_sha256'][:8],
                                                                                 FL01_SYX_SHA[:8])] = (
        f1man['main_sha256'] == f1j['main_sha256'] and f1j['syx']['sha256'] == FL01_SYX_SHA)
    fdsp = json.load(open(os.path.join(FL02_DSP, 'build.json')))
    sec7 = os.path.join(FL02_DSP, 'section7.bin')
    dsyx = os.path.join(ROOT, fdsp['syx']['path'])
    sec7_sha = sha(open(sec7, 'rb').read())
    checks['FL02 DSP: build.json records section7.bin (%s...) and %s' % (sec7_sha[:8], fdsp['syx']['path'])] = (
        fdsp['name'] == 'FL02' and fdsp['section7']['sha256'] == sec7_sha
        and fdsp['syx']['sha256'] == sha(open(dsyx, 'rb').read()))
    checks['FL02 DSP: the layout check reports no problems, every code section where our code ran on the unit '
           '(%s), the stock delay hooks kept' % ', '.join('%s %s' % (r['section'], r['core']) for r in
                                                         fdsp['proven_check'])] = (
        not fdsp['layout_check']['problems'] and all(r['proven'] for r in fdsp['proven_check'])
        and len(fdsp['stock_hooks']) == 2 and all(h['stock_bytes_kept'] for h in fdsp['stock_hooks']))
    s1src, s2src = f1dsp['sources'], fdsp['sources']
    checks['FL02 DSP: the sources FL01\'s (fx/flanger.c %s... unchanged) but %s -> %s' % (
        s1src['fx/flanger.c'][:8], GLUE2, ', '.join(NEW_DSP_SRC))] = (
        {k: v for k, v in s1src.items() if k != GLUE2} == {k: v for k, v in s2src.items() if k not in NEW_DSP_SRC}
        and all(s2src.get(f) == sha(open(os.path.join(ROOT, f), 'rb').read()) for f in NEW_DSP_SRC)
        and GLUE2 not in s2src)

    # ---- the main OS: FL01's mods, param-rows 0.4, comp-ui 0.3, flanger-sync 0.1
    T.run([sys.executable, os.path.join(ROOT, 'scripts', 'fl02_make_mod.py')])
    built = {}
    for m, d in MODDIR.items():
        sdk_build(d, mods_dir, env)
        built[m] = os.path.join(mods_dir, '%s-%s.elemod' % (m, NEWVER[m]))
    old_file = {}
    for m in f1man['mods']:
        if m['id'] == 'os-version':
            old_file[m['id']] = OB01_OSV
        elif m['id'] == 'flanger-names':
            old_file[m['id']] = FL01_FLN
        else:
            old_file[m['id']] = os.path.join(CMP4_MODS, m['file'])
        checks['FL01\'s %s %s: its .elemod (sha256 %s...)' % (m['id'], m['version'], m['sha256'][:8])] = (
            sha(open(old_file[m['id']], 'rb').read()) == m['sha256'])
    files, ids = [], []
    for m in f1man['mods']:
        files.append(built.get(m['id'], old_file[m['id']]))
        ids.append(m['id'])
    files.append(built['flanger-sync'])
    ids.append('flanger-sync')
    T.run([ELPY, '-m', 'elekloader.lint'] + files + ['--stock', STOCK], cwd=EL, env=env)
    ok, why = refused([old_file['param-rows'] if f == built['param-rows'] else f for f in files], env, build_dir,
                      'comp-ui 0.3 requires param-rows 0.4 or later')
    checks['comp-ui 0.3 with param-rows 0.3 is refused (it requires param-rows>=0.4): %s' % why[:90]] = ok
    mainsyx = os.path.join(build_dir, a.name + '-main.syx')
    cmd = [ELPY, '-m', 'elekloader.patch', '--stock', STOCK]
    for f in files:
        cmd += ['--mod', f]
    T.run(cmd + ['--version', a.name, '--out', mainsyx], cwd=EL, env=env)
    man = json.load(open(mainsyx + '.json'))
    mp = json.load(open(mainsyx + '.map.json'))
    syx = os.path.join(out, a.name + '.syx')
    tmp = syx + '.tmp'
    T.run([TOOL, '-i', mainsyx, '-c', '7', sec7, '-o', tmp])

    try:
        st, _ = T.sections(STOCK)
        fn, _ = T.sections(tmp)
        mn, _ = T.sections(mainsyx)
        ds, _ = T.sections(dsyx)
        hdr, shdr = T.header(tmp), T.header(STOCK)
        checks['ELE3 header: version field %s, the rest as stock' % a.name] = (
            hdr[0x14:0x18] == a.name.encode() and hdr[:0x14] == shdr[:0x14] and hdr[0x18:] == shdr[0x18:])
        checks['section ids as stock'] = sorted(fn) == sorted(st)
        for i in (2, 4, 5, 8):
            checks['section %d stored as stock' % i] = fn[i] == st[i]
        checks['section 7 stored byte for byte as in %s' % fdsp['syx']['path']] = fn[7] == ds[7]
        checks['section 7 depacks to %s (sha256 %s...)' % (os.path.relpath(sec7, ROOT), sec7_sha[:8])] = (
            T.depack7(tmp) == open(sec7, 'rb').read())
        checks['section 3 stored as elekloader packed it'] = fn[3] == mn[3]
        img = T.main_image(tmp)
        checks['main OS depacks to the patcher\'s image'] = sha(img) == man['main_sha256']
        mids = [(m['id'], m['version']) for m in man['mods']]
        checks['mods: FL01\'s seven in FL01\'s order with param-rows 0.4 and comp-ui 0.3, then flanger-sync 0.1 (%s)' % (
            ', '.join('%s %s' % x for x in mids))] = (
            [m['id'] for m in man['mods']] == ids
            and all(m['sha256'] == sha(open(old_file[m['id']], 'rb').read()) for m in man['mods']
                    if m['id'] not in built)
            and {m['id']: m['version'] for m in man['mods'] if m['id'] in built} == NEWVER)

        f1img = open(FL01_MAIN, 'rb').read()
        stock_img = open(T.STOCK_MAIN, 'rb').read()
        f1mp = json.load(open(FL01_MAP))
        el_1 = {m['id']: OB.load_elemod(old_file[m['id']]) for m in f1man['mods']}
        el_2 = {m['id']: OB.load_elemod(built.get(m['id'], old_file.get(m['id']))) for m in man['mods']}
        checks['FL01\'s main OS = its seven .elemod files relinked from its manifest\'s layout and map, byte for '
               'byte'] = OB.reconstruct(stock_img, f1man, f1mp, el_1) == f1img
        checks['%s\'s main OS = its eight .elemod files relinked from its layout and map, byte for byte' % a.name] = (
            OB.reconstruct(stock_img, man, mp, el_2) == img)

        # param-rows 0.4 against 0.3: the looks only
        r3, r4 = el_1['param-rows'], el_2['param-rows']
        b3 = OB.parts(r3['sections']['.run']['parts'], stock_img)
        b4 = OB.parts(r4['sections']['.run']['parts'], stock_img)
        sym3 = r3['symbols']['param_rows_comp']
        diffs = [i for i in range(max(len(b3), len(b4))) if (b3[i:i + 1] != b4[i:i + 1])]
        want_off = {sym3[1] + n * 0x54 + 0x40 + 3: (0x99, lk) for n, lk in LOOKS.items()}
        looks_ok = (len(b3) == len(b4) and set(diffs) == set(want_off) and r3['relocs'] == r4['relocs']
                    and r3['symbols'] == r4['symbols'] and r3['sites'] == r4['sites']
                    and all(b3[o] == v[0] and b4[o] == v[1] for o, v in want_off.items()))
        lk2 = T.Linked(img, mp)
        rows_base = int(mp['param_rows_comp'], 16)
        looks_now = {0x141 + n: lk2.u32(rows_base + n * 0x54 + 0x40) for n in range(20)}
        checks['param-rows 0.4 = 0.3 but the look of the twelve THR, RAT, ATK, REL rows (0x99 -> 0x96, 0x9a, 0x97, 0x98; '
               'MUP 0x99, XOVR 0x99, MIX 0x9e as before): %s' % ', '.join('%#x:%#x' % kv for kv in looks_now.items())] = (
            looks_ok and all(looks_now[0x141 + n] == LOOKS.get(n, 0x9e if n >= 17 else 0x99) for n in range(20)))

        # comp-ui 0.3 against 0.2: the same sites (stock bytes, targets by name), records and page list
        u2, u3 = el_1['comp-ui'], el_2['comp-ui']

        def site_names(d):
            return [(s_['addr'], s_['len'], s_['kind'] if 'kind' in s_ else None, [r_[2] for r_ in s_['relocs']],
                     s_['new'] if not s_['relocs'] else None) for s_ in d['sites']]
        lk1 = T.Linked(f1img, f1mp)
        recs = []
        for lk_, m_ in ((lk1, f1mp), (lk2, mp)):
            base = int(m_['compui_rec'], 16)
            rr = []
            for j in range(3):
                w = [lk_.u32(base + 44 * j + 4 * x) for x in range(11)]
                rr.append((lk_.string(w[0]), lk_.string(w[1]), w[2:]))
            pages = [lk_.u32(int(m_['compui_pages'], 16) + 4 * x) for x in range(7)]
            recs.append((rr, pages))
        checks['comp-ui 0.3 = 0.2\'s five sites (the same stock bytes, targets by name), three page records and page '
               'list; +compui_rat_draw, rat_one (the 1.00 frame) and compui_rat_set'] = (
            site_names(u2) == site_names(u3) and recs[0] == recs[1]
            and all(k in u3['symbols'] for k in ('compui_rat_draw', 'compui_rat_set')))

        # every relocated field of the five unchanged mods: the same place in both links
        lo_, lf_ = F1.Link(f1man, f1mp, el_1), F1.Link(man, mp, el_2)
        fo, ff = lo_.fields(), lf_.fields()
        same_mods = [m for m in ids if m not in built]
        others = [k for k in fo if k[0] in same_mods]
        moved = [k for k in others if k in ff and fo[k][0] != ff[k][0]]
        bad = [k for k in others if k not in ff or fo[k][1] != ff[k][1]]
        checks['every relocated field of the five unchanged mods (%s) names the same place in both links (%d fields, %d '
               'moved with the layout)' % (', '.join(same_mods), len(others), len(moved))] = (
            not bad and set(others) == {k for k in ff if k[0] in same_mods})
        pr = [k for k in fo if k[0] == 'param-rows']
        badr = [k for k in pr if k not in ff or fo[k][1] != ff[k][1]]
        checks['param-rows: its %d relocated fields name the same place in both links' % len(pr)] = (
            not badr and set(pr) == {k for k in ff if k[0] == 'param-rows'})

        # flanger-sync: its two sites, the template, the 12 values
        row = DESC + ROW_SPD * ROW
        fmt_at = lk2.u32(row + 0x34)
        checks['flanger-sync: row 0x6a +0x34 -> fls_fmt (%#x; FL01: the stock plain number %#x)' % (
            fmt_at, lk1.u32(row + 0x34))] = (fmt_at == int(mp['fls_fmt'], 16) and lk1.u32(row + 0x34) == FMT_NUMBER)
        tpl = int(mp['fls_tpl'], 16)
        code = img[SITE_TPL - LOAD:SITE_TPL - LOAD + 14]
        tw = [lk2.u32(tpl + 4 * x) for x in range(4)]
        checks['flanger-sync: 0x400c5c96 pea fls_tpl (%#x), then pea 0x42435eb8; jsr (a4) as stock; fls_tpl = {%#x, %d, '
               'manager %#x, invoker %#x = fls_func}' % (tpl, tw[0], tw[1], tw[2], tw[3])] = (
            code == bytes.fromhex('4879%08x4879%08x4e94' % (tpl, ENTRY_SPD34))
            and f1img[SITE_TPL - LOAD:SITE_TPL - LOAD + 14] == bytes.fromhex('4879%08x4879%08x4e94' % (TPL_STOCK,
                                                                                                         ENTRY_SPD34))
            and tw[1] == 0 and tw[2] == MANAGER and tw[3] == int(mp['fls_func'], 16)
            and int(mp['__run_start'], 16) <= tw[0] < int(mp['__run_start'], 16) + 4 * int(mp['__run_words'], 16))
        vals = [struct.unpack('>h', lk2.ram(int(mp['fls_val'], 16) + 2 * i, 2))[0] for i in range(13)]
        dsp_vals, order = sync_values_from_source()
        anchors = [9, 23, 37, 52, 66, 80, 86, 94, 100, 108, 122, 126]
        checks['flanger-sync\'s 12 synced values = flg_sync.c\'s = (a << 8) | i (%s)' % ' '.join(
            '%04x' % v for v in vals[1:])] = (
            vals[0] == 0 and vals[1:] == dsp_vals == [(x << 8) | i for i, x in enumerate(anchors, 1)]
            and order == list(range(1, 13)))

        # the stock part: every differing byte in a site
        sites = [(int(s_['addr'], 16), s_['len'], mid) for mid, d in el_2.items() for s_ in d['sites']]
        sites += [(int(s_['addr'], 16), s_['len'], mid) for mid, d in el_1.items() for s_ in d['sites']]
        rng = T.diff_ranges(f1img[:STOCK_END - LOAD], img[:STOCK_END - LOAD])
        outside = [(x, n) for x, n in rng if not all(any(sa <= y < sa + sn for sa, sn, _ in sites)
                                                     for y in range(x, x + n))]
        hit = sorted({(sa, mid) for x, n in rng for sa, sn, mid in sites if sa < x + n and x < sa + sn})
        checks['stock part: %d ranges (%d bytes) differ from FL01, all inside %d sites (%s)' % (
            len(rng), sum(n for _, n in rng), len(hit), ', '.join('%s 0x%08x' % (m_, sa) for sa, m_ in hit))] = (
            not outside)

        # the descriptor table and the grown table: FL01's but row 0x6a's formatter
        def named(lk_, d):
            f = T.row_fields(d)
            for k_ in ('name', 'group_name', 'short', 'suffix'):
                f[k_] = lk_.string(f[k_])
            if lk_.run_start <= f['fmt'] < lk_.run_end:
                f['fmt'] = sorted(k for k, x in lk_.mp.items() if int(x, 16) == f['fmt'] and not k.startswith('__'))
            return f
        lo, hi = DESC - LOAD, DESC - LOAD + ROWS0 * ROW
        tab_diff = []
        for r in range(ROWS0):
            x, y = named(lk2, img[lo + r * ROW:lo + (r + 1) * ROW]), named(lk1, f1img[lo + r * ROW:lo + (r + 1) * ROW])
            for k_ in x:
                if x[k_] != y[k_]:
                    tab_diff.append((r, k_, y[k_], x[k_]))
        checks['the descriptor table: FL01\'s (names as strings, formatters by name) but row 0x6a\'s formatter '
               '(%s)' % tab_diff] = ([(r, k_) for r, k_, _, _ in tab_diff] == [(ROW_SPD, 'fmt')]
                                     and 'fls_fmt' in tab_diff[0][3] and tab_diff[0][2] == FMT_NUMBER)
        table, rows = T.simulate_boot(lk2, stock_img)
        table_o, rows_o = T.simulate_boot(lk1, stock_img)
        grown_diff = [(r, k_) for r in range(ROWS) for k_, v in named(lk2, table[r * ROW:(r + 1) * ROW]).items()
                      if v != named(lk1, table_o[r * ROW:(r + 1) * ROW])[k_]]
        checks['the grown table pg_boot builds (simulated, %d rows): FL01\'s, field by field, but row 0x6a\'s formatter; '
               'the added rows the same' % ROWS] = (
            grown_diff == [(ROW_SPD, 'fmt')] and table[:ROWS0 * ROW] == img[lo:hi]
            and [(r['id'], named(lk2, r['desc'])) for r in rows] == [(r['id'], named(lk1, r['desc'])) for r in rows_o])

        # the layout
        lay_o, lay_f = f1man['link']['layout'], man['link']['layout']
        sec_moved = sorted('%s %+d' % (k, lay_f['sections'][k] - v) for k, v in lay_o['sections'].items()
                           if lay_f['sections'].get(k) != v)
        new_secs = sorted(set(lay_f['sections']) - set(lay_o['sections']))
        checks['layout: FL01\'s sections and flanger-sync\'s %s; the blob %d -> %d B; moved: %s' % (
            new_secs, lay_o['blob_len'], lay_f['blob_len'], ', '.join(sec_moved))] = (
            set(lay_o['sections']) <= set(lay_f['sections']) and all(k.startswith('flanger-sync ') for k in new_secs))

        # the DSP
        dspid = dsp_identity(checks)

        for k, v in checks.items():
            print('%-4s %s' % ('ok' if v else 'FAIL', k))
        if not all(checks.values()):
            for x in bad[:20]:
                print('  moved to another place: %s %s -> %s' % (x, fo[x], ff.get(x)))
            print('  outside sites: %s' % outside[:20])
            print('  table diff: %s' % tab_diff[:20])
            print('  grown diff: %s' % grown_diff[:20])
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
            'base': 'FL01 ' + FL01_SYX_SHA, 'mods': man['mods'],
            'main_vs_fl01': {'stock_part_ranges': ['0x%08x +%d' % r for r in rng],
                             'sites_hit': ['%s 0x%08x' % (m_, sa) for sa, m_ in hit],
                             'moved_fields': len(moved), 'sections_moved': sec_moved, 'new_sections': new_secs,
                             'blob_len': [lay_o['blob_len'], lay_f['blob_len']],
                             'descriptor_table_diff': [['0x%x' % r, k_, str(y), str(x)] for r, k_, y, x in tab_diff],
                             'param_rows_looks': {'%#x' % k: '%#x' % v for k, v in looks_now.items()}},
            'symbols': {k: mp[k] for k in ('compui_rat_draw', 'compui_rat_set', 'fls_fmt', 'fls_func', 'fls_tpl',
                                           'fls_val', 'compui_draws', 'compui_bar') if k in mp},
            'dsp_vs_fl01': dspid, 'checks': checks, 'log': log}
    json.dump(info, open(os.path.join(out, 'build-%s.json' % a.name), 'w'), indent=1)
    print('DSP vs FL01: ' + '; '.join('%s %d B %s' % w for w in dspid['summary']))
    print('main OS vs FL01, stock part: ' + ', '.join('0x%08x +%d' % r for r in rng))
    print('%s  %s' % (s, os.path.relpath(syx, ROOT)))


if __name__ == '__main__':
    main()
