#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""FL01: OB01 with the retuned flanger (notes/T5.6-flanger-retune.md): the chorus page's knob C is TIME.

    venv/bin/python tools/dspsplice/build.py FL01          first: the DSP section, out/FL01/FL01-dsp/
    venv/bin/python scripts/fl01_build.py [--name FL01] [--out out/FL01]

  FL01 DSP section = recipe FL01 (tools/dspsplice/build.py): CMP4's with tools/dspsplice/src/flg_glue2.c in place of
  flg_glue.c, in the same place (block 1, after fx/flanger.c, where CMP4's glue ran).
  FL01 main OS = OB01's seven mods (CMP4's six .elemod files and OB01's os-version 0.1, sha256-checked against OB01's
  manifest) with flanger-names-dn2 0.2 in place of 0.1: knob C of the chorus page (descriptor row 0x6b) named
  Time/TIME instead of High-pass/HPF, and its new-kit default 32 (TIME 32 = CMP4's flanger). The ELE3 version field
  is the build's name (FL01): the power-on screen shows it, os-version makes the two replies to the computer say 1.12.

Writes NAME.syx (+ .sha256), NAME.main.bin, build-NAME.json, and mods/, build/. Checks (all must hold, or nothing is
written):
  - the pins: stock 1.12, OB01.syx and its main OS image, CMP4.syx and its section 7, the FL01 DSP build (its record,
    its placement checks, the sources CMP4's but the glue);
  - the container: ELE3 header as stock but the version field; sections 2, 4, 5, 8 stored as stock; section 7 stored
    as in FL01-dsp.syx and depacking to its section7.bin; section 3 as elekloader packed it;
  - the mods: OB01's .elemod files (sha256) but flanger-names 0.2, in OB01's order; both main OS images (OB01's and
    FL01's) rebuilt from their .elemod files, manifest layout and map, byte for byte;
  - against OB01's main OS: every relocated field of every mod but flanger-names names the same place (the same mod
    section and offset, the same stock address or the same symbol) in both links; flanger-names' 13 sites of 0.1 name
    the same strings, its three new sites are row 0x6b's default (8192), long name "Time" and label "TIME"; in the
    stock part every byte that differs is in a site; the descriptor table and the grown table pg_boot builds are
    OB01's but row 0x6b's three fields; the blob grows by flanger-names' new strings only;
  - the DSP against CMP4's (OB01's section 7 is CMP4's, stored byte for byte): the final images' regions the same but
    block 1's end; every differing byte inside CMP4's or FL01's glue, or in the two stubs' calls into it (flg_process,
    flg_init: the same instruction, the new address).
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
sys.path.insert(1, os.path.join(ROOT, 'tools', 'dspsplice'))
sys.path.insert(2, os.path.join(ROOT, 'tools', 'sharcvm'))

EL, ELPY, TOOL, STOCK, STOCK_SHA = T.EL, T.ELPY, T.TOOL, T.STOCK, T.STOCK_SHA
LOAD, STOCK_END, DESC, ROW, ROWS0, ROWS = T.LOAD, T.STOCK_END, T.DESC, T.ROW, T.ROWS0, T.ROWS
OB01 = os.path.join(ROOT, 'out', 'OB01')
OB01_SYX, OB01_SYX_SHA = os.path.join(OB01, 'OB01.syx'), 'bfb886237377555aea6778ba49d0b48b48e7ce46a04bd9be77365f36cb0b62dd'
OB01_MAIN, OB01_JSON = os.path.join(OB01, 'OB01.main.bin'), os.path.join(OB01, 'build-OB01.json')
OB01_MAN = os.path.join(OB01, 'build', 'OB01-main.syx.json')
OB01_MAP = os.path.join(OB01, 'build', 'OB01-main.syx.map.json')
OB01_OSV = os.path.join(OB01, 'mods', 'os-version-0.1.elemod')
CMP4 = os.path.join(ROOT, 'out', 'CMP4')
CMP4_SYX, CMP4_SYX_SHA = os.path.join(CMP4, 'CMP4.syx'), 'c1df74aaa36f987c0dbdfb43486fe059952c413fccf95272966064cec42c3b88'
CMP4_MODS = os.path.join(CMP4, 'main', 'mods')
CMP4_DSP = os.path.join(CMP4, 'CMP4-dsp')
FL01_DSP = os.path.join(ROOT, 'out', 'FL01', 'FL01-dsp')
FLN_DIR = os.path.join(EL, 'examples', 'flanger-names-dn2')
ROW_C = 0x6b
NEW_C = {0x10: 32 << 8, 0x28: 'Time', 0x30: 'TIME'}         # row 0x6b: default, long name, label
OLD_C = {0x10: 0, 0x28: 'High-pass', 0x30: 'HPF'}
STR_FIELDS = (0x28, 0x2c, 0x30, 0x38)
GLUE = 'tools/dspsplice/src/flg_glue2.c'
L1 = 0x28000000                       # the boot stream's alias of L1 (tools/dspsplice/ldr.py loader_addr)
log = T.log


def sha(b):
    return hashlib.sha256(b).hexdigest()


def sdk_build(moddir, out_dir, env):
    T.run([ELPY, '-m', 'elekloader.sdk.build', moddir, '--stock', STOCK, '--out', out_dir], cwd=EL, env=env)


class Link:
    """one link: its manifest, map and .elemod files; every relocated field and what it names"""

    def __init__(self, man, mp, elemods):
        self.man, self.mp, self.el = man, mp, elemods
        lay = man['link']['layout']
        self.base = {tuple(k.split(' ')): v for k, v in lay['sections'].items()}
        self.glob = {k: int(v, 16) for k, v in mp.items()}
        self.size = {}
        for (mid, sec) in self.base:
            s = elemods[mid]['sections'][sec]
            self.size[(mid, sec)] = s.get('len', s.get('size', 0))
        self.tables = {t: int(i['at'], 16) for t, i in man['link']['tables'].items()}

    def resolve(self, mid, tgt):
        if tgt == 'abs':
            return 0
        kind, nm = tgt.split(':', 1)
        if kind == 'sec':
            return self.base[(mid, nm)]
        sym = self.el[mid]['symbols'].get(nm)
        if sym is not None:
            return sym[1] if sym[0] == 'abs' else self.base[(mid, sym[0])] + sym[1]
        return self.glob[nm]

    def place(self, v):
        """what an address names: (mod, section, offset), a table and offset, a stock address, or symbols"""
        for (mid, sec), b in sorted(self.base.items(), key=lambda kv: kv[1]):
            if b <= v < b + self.size[(mid, sec)]:
                return ('sec', mid, sec, v - b)
        for t, at in self.tables.items():
            if v == at:
                return ('table', t)
        if LOAD <= v < STOCK_END or v < 0x40000000 or v >= 0x80000000:
            return ('addr', v)
        names = sorted(k for k, x in self.glob.items() if x == v)
        if names:
            return ('sym', tuple(names))
        for (mid, sec), b in self.base.items():
            if v == b + self.size[(mid, sec)]:
                return ('end', mid, sec)
        return ('?', v)

    def fields(self):
        """every relocated field: key -> (the address it names, the place)"""
        out = {}
        self.lengths = {}
        for mid, d in self.el.items():
            for sec, off, typ, tgt, add in d['relocs']:
                out[(mid, sec, off)] = self.resolve(mid, tgt) + add
                if tgt in ('sym:__run_words', 'sym:__bss_words'):
                    self.lengths[(mid, sec, off)] = tgt[4:]
            for st_ in d['sites']:
                for off, typ, tgt, add in st_['relocs']:
                    out[(mid, 'site ' + st_['addr'], off)] = self.resolve(mid, tgt) + add
            for i, e in enumerate(d.get('contribute', [])):
                for off, typ, tgt, add in e['relocs']:
                    out[(mid, 'contribute %d %s' % (i, e['to']), off)] = self.resolve(mid, tgt) + add
        # the core's boot copy takes the run image's and the .bss's lengths in words: a length, not a place
        return {k: (v, ('length', k) if k in self.lengths else self.place(v)) for k, v in out.items()}


def dsp_regions(stream):
    import build as BD  # noqa: E402  (tools/dspsplice)
    img = BD.final_image(stream)
    return {(s, e): bytes(d) for (s, e), d in zip(img.regions, img.data)}


def call_window(buf, lo, sw):
    """offsets in buf (core bytes from lo) of a 32-bit SW address as the stubs' calls hold it: two 16-bit words, the
    high one first, each little-endian (as tools/dspsplice/build.py's cjump_bytes)"""
    pat = bytes([(sw >> 16) & 0xff, (sw >> 24) & 0xff, sw & 0xff, (sw >> 8) & 0xff])
    out, i = [], buf.find(pat)
    while i >= 0:
        out.append(lo + i)
        i = buf.find(pat, i + 1)
    return out


def dsp_identity(checks):
    """FL01's DSP final image against CMP4's: the differing ranges and why each one differs"""
    import elf as E  # noqa: E402  (tools/sharcvm)
    c4 = dsp_regions(open(os.path.join(CMP4_DSP, 'section7.bin'), 'rb').read())
    fl = dsp_regions(open(os.path.join(FL01_DSP, 'section7.bin'), 'rb').read())
    b1 = [k for k in c4 if k[0] == L1 + 0x2dd600]
    b1f = [k for k in fl if k[0] == L1 + 0x2dd600]
    same = sorted(set(c4) & set(fl))
    checks['DSP: the final images have the same regions but block 1 (the same start, %s / %s)' % (
        '0x%x-0x%x' % (b1[0][0] - L1, b1[0][1] - L1), '0x%x-0x%x' % (b1f[0][0] - L1, b1f[0][1] - L1))] = (
        len(b1) == len(b1f) == 1 and set(c4) - set(b1) == set(fl) - set(b1f))
    checks['DSP: every other region of the final image byte for byte CMP4\'s (%d regions: block 2\'s compressor, code4, '
           'the data, the fills, the hooks, the stock code)' % len(same)] = all(c4[k] == fl[k] for k in same)
    syms4 = E.read_elf(os.path.join(CMP4_DSP, 'build', 'CMP4.dxe')).symbols
    symsf = E.read_elf(os.path.join(FL01_DSP, 'build', 'FL01.dxe')).symbols
    stubs = E.read_elf(os.path.join(FL01_DSP, 'build', 'stubs.doj')).sections[0]
    stub_end = 0x2dd600 + len(stubs.data)
    glue4 = len(E.read_elf(os.path.join(CMP4_DSP, 'build', 'flg_glue.doj')).sections[0].data)
    gluef = len(E.read_elf(os.path.join(FL01_DSP, 'build', 'flg_glue2.doj')).sections[0].data)
    A, B = c4[b1[0]], fl[b1f[0]]
    endf = 0x2dd600 + len(next(s for s in E.read_elf(os.path.join(FL01_DSP, 'build', 'FL01.dxe')).sections
                               if s.name == 't38_code').data)
    end4 = 0x2dd600 + len(next(s for s in E.read_elf(os.path.join(CMP4_DSP, 'build', 'CMP4.dxe')).sections
                               if s.name == 't38_code').data)
    g4, gf = end4 - glue4, endf - gluef
    checks['DSP: the glue starts where CMP4\'s did (0x%x): CMP4\'s 0x%x-0x%x (%d B), FL01\'s 0x%x-0x%x (%d B), inside '
           'the proven 0x2dd600-0x2e1c36' % (g4, g4, end4, glue4, gf, endf, gluef)] = (
        g4 == gf and endf <= end4 <= 0x2e1c36 and E.sw_to_byte(syms4['flg_init.']) == g4)
    # byte by byte over block 1 (a byte loaded in one image only counts as differing)
    n = max(len(A), len(B))
    diff = [i for i in range(n) if (A[i] if i < len(A) else None) != (B[i] if i < len(B) else None)]
    ranges = []
    for i in diff:
        if ranges and i == ranges[-1][1]:
            ranges[-1][1] = i + 1
        else:
            ranges.append([i, i + 1])
    ranges = [(0x2dd600 + a, 0x2dd600 + b) for a, b in ranges]
    calls = {}
    for fn in ('flg_process', 'flg_init'):
        w4 = call_window(A[:stub_end - 0x2dd600], 0x2dd600, syms4[fn + '.'])
        wf = call_window(B[:stub_end - 0x2dd600], 0x2dd600, symsf[fn + '.'])
        calls[fn] = (w4, wf, syms4[fn + '.'], symsf[fn + '.'])
    windows = [(w, w + 4) for fn, (w4, wf, _, _) in calls.items() if len(w4) == 1 and w4 == wf for w in w4]
    checks['DSP: the stubs call flg_process and flg_init once each, at the same place in both: %s' % ', '.join(
        '%s 0x%x (0x%x -> 0x%x)' % (fn, w4[0] if w4 else 0, a, b) for fn, (w4, wf, a, b) in calls.items())] = (
        len(windows) == 2)
    why, stray = [], []
    for lo, hi in ranges:
        if g4 <= lo and hi <= max(end4, endf) + 2:
            why.append(('0x%x-0x%x' % (lo, hi), hi - lo, 'the glue'))
        elif any(a <= lo and hi <= b for a, b in windows):
            fn = next(f for f, (w4, wf, _, _) in calls.items() if w4 and w4[0] <= lo < w4[0] + 4)
            why.append(('0x%x-0x%x' % (lo, hi), hi - lo, 'the stub\'s call to %s' % fn))
        else:
            stray.append('0x%x-0x%x' % (lo, hi))
    checks['DSP: block 1 differs from CMP4\'s only in the glue and the two stubs\' call addresses (%d ranges)' % len(
        ranges)] = not stray
    glue_ranges = [w for w in why if w[2] == 'the glue']
    summary = [w for w in why if w[2] != 'the glue']
    if glue_ranges:
        lo_, hi_ = glue_ranges[0][0].split('-')[0], glue_ranges[-1][0].split('-')[1]
        summary.append(('%s-%s' % (lo_, hi_), sum(w[1] for w in glue_ranges),
                        'the glue (%d ranges; CMP4 loads to 0x%x, FL01 to 0x%x)' % (len(glue_ranges), end4, endf)))
    return {'ranges': why, 'summary': summary, 'stray': stray, 'glue_cmp4': '0x%x-0x%x' % (g4, end4),
            'glue_fl01': '0x%x-0x%x' % (gf, endf),
            'calls': {fn: {'at': ['0x%x' % x for x in w4], 'cmp4': '0x%x' % a, 'fl01': '0x%x' % b}
                      for fn, (w4, wf, a, b) in calls.items()}}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--name', default='FL01')
    ap.add_argument('--out', default=os.path.join(ROOT, 'out', 'FL01'))
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
    obj = json.load(open(OB01_JSON))
    obman = json.load(open(OB01_MAN))
    c4dsp = json.load(open(os.path.join(CMP4_DSP, 'build.json')))
    for p, want in ((STOCK, STOCK_SHA), (OB01_SYX, OB01_SYX_SHA), (OB01_MAIN, obj['main_sha256']),
                    (CMP4_SYX, CMP4_SYX_SHA), (os.path.join(CMP4_DSP, 'section7.bin'), c4dsp['section7']['sha256'])):
        if sha(open(p, 'rb').read()) != want:
            raise SystemExit('%s: sha256 is not the expected one' % p)
    checks['OB01\'s manifest describes OB01.main.bin (%s...)' % obj['main_sha256'][:8]] = (
        obman['main_sha256'] == obj['main_sha256'] and obj['syx']['sha256'] == OB01_SYX_SHA)
    fdsp = json.load(open(os.path.join(FL01_DSP, 'build.json')))
    sec7 = os.path.join(FL01_DSP, 'section7.bin')
    dsyx = os.path.join(ROOT, fdsp['syx']['path'])
    sec7_sha = sha(open(sec7, 'rb').read())
    checks['FL01 DSP: build.json records section7.bin (%s...) and %s' % (sec7_sha[:8], fdsp['syx']['path'])] = (
        fdsp['name'] == 'FL01' and fdsp['section7']['sha256'] == sec7_sha
        and fdsp['syx']['sha256'] == sha(open(dsyx, 'rb').read()))
    checks['FL01 DSP: the layout check reports no problems, every code section where our code ran on the unit '
           '(%s), the stock delay hooks kept' % ', '.join('%s %s' % (r['section'], r['core']) for r in
                                                         fdsp['proven_check'])] = (
        not fdsp['layout_check']['problems'] and all(r['proven'] for r in fdsp['proven_check'])
        and len(fdsp['stock_hooks']) == 2 and all(h['stock_bytes_kept'] for h in fdsp['stock_hooks']))
    src4, srcf = c4dsp['sources'], fdsp['sources']
    checks['FL01 DSP: the sources CMP4\'s (fx/flanger.c %s... unchanged) but flg_glue.c -> %s (%s...) and '
           'fx/flanger_unit.h' % (src4['fx/flanger.c'][:8], GLUE, srcf.get(GLUE, '')[:8])] = (
        {k: v for k, v in src4.items() if k != 'tools/dspsplice/src/flg_glue.c'}
        == {k: v for k, v in srcf.items() if k not in (GLUE, 'fx/flanger_unit.h')}
        and srcf.get(GLUE) == sha(open(os.path.join(ROOT, GLUE), 'rb').read())
        and 'tools/dspsplice/src/flg_glue.c' not in srcf)
    ob_syx_s, _ = T.sections(OB01_SYX)
    c4_syx_s, _ = T.sections(CMP4_SYX)
    checks['OB01\'s section 7 is CMP4\'s, stored byte for byte'] = ob_syx_s[7] == c4_syx_s[7]

    # ---- the main OS: OB01's mods, flanger-names 0.2 in place of 0.1
    T.run([sys.executable, os.path.join(ROOT, 'scripts', 'fl01_make_mod.py')])
    sdk_build(FLN_DIR, mods_dir, env)
    fln = os.path.join(mods_dir, 'flanger-names-0.2.elemod')
    files = []
    for m in obman['mods']:
        if m['id'] == 'flanger-names':
            files.append(fln)
            continue
        f = OB01_OSV if m['id'] == 'os-version' else os.path.join(CMP4_MODS, m['file'])
        checks['%s %s: OB01\'s .elemod (sha256 %s...)' % (m['id'], m['version'], m['sha256'][:8])] = (
            sha(open(f, 'rb').read()) == m['sha256'])
        files.append(f)
    T.run([ELPY, '-m', 'elekloader.lint'] + files + ['--stock', STOCK], cwd=EL, env=env)
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
        ids = [m['id'] for m in man['mods']]
        checks['mods: OB01\'s, flanger-names 0.2 in place of 0.1, in OB01\'s order (%s)' % ', '.join(ids)] = (
            [(m['id'], m['sha256']) for m in man['mods'] if m['id'] != 'flanger-names']
            == [(m['id'], m['sha256']) for m in obman['mods'] if m['id'] != 'flanger-names']
            and ids == [m['id'] for m in obman['mods']]
            and [m['version'] for m in man['mods'] if m['id'] == 'flanger-names'] == ['0.2'])

        obimg = open(OB01_MAIN, 'rb').read()
        stock_img = open(T.STOCK_MAIN, 'rb').read()
        obmp = json.load(open(OB01_MAP))
        el_ob = {m['id']: OB.load_elemod(OB01_OSV if m['id'] == 'os-version' else os.path.join(CMP4_MODS, m['file']))
                 for m in obman['mods']}
        el_fl = {m['id']: OB.load_elemod(fln if m['id'] == 'flanger-names' else (
            OB01_OSV if m['id'] == 'os-version' else os.path.join(CMP4_MODS, m['file']))) for m in man['mods']}
        checks['OB01\'s main OS = its seven .elemod files relinked from its manifest\'s layout and map, byte for '
               'byte'] = OB.reconstruct(stock_img, obman, obmp, el_ob) == obimg
        checks['%s\'s main OS = its seven .elemod files relinked from its layout and map, byte for byte' % a.name] = (
            OB.reconstruct(stock_img, man, mp, el_fl) == img)

        # every relocated field: the same place in both links (flanger-names' own: below)
        lo_, lf_ = Link(obman, obmp, el_ob), Link(man, mp, el_fl)
        fo, ff = lo_.fields(), lf_.fields()
        others = [k for k in fo if k[0] != 'flanger-names']
        moved = [k for k in others if k in ff and fo[k][0] != ff[k][0]]
        bad = [k for k in others if k not in ff or fo[k][1] != ff[k][1]]
        checks['every relocated field of the six other mods names the same place in both links (%d fields, %d of '
               'them moved with the layout)' % (len(others), len(moved))] = (
            not bad and set(others) == {k for k in ff if k[0] != 'flanger-names'})
        lens = {lo_.lengths[k]: (fo[k][0], ff[k][0]) for k in lo_.lengths}
        grow = man['link']['layout']['blob_len'] - obman['link']['layout']['blob_len']
        checks['the core\'s boot copy: __run_words %d -> %d (the blob +%d B), __bss_words %d -> %d' % (
            lens['__run_words'] + (grow,) + lens['__bss_words'])] = (
            lens['__run_words'][1] - lens['__run_words'][0] == grow // 4 and lens['__bss_words'][0] == lens['__bss_words'][1]
            and lo_.lengths == lf_.lengths)
        lk, lko = T.Linked(img, mp), T.Linked(obimg, obmp)
        fl_old = [k for k in fo if k[0] == 'flanger-names']
        same_text = [k for k in fl_old if k in ff and lk.string(ff[k][0]) == lko.string(fo[k][0])]
        checks['flanger-names: the 13 sites of 0.1 name the same strings as in OB01 (%s)' % ', '.join(sorted(
            {'"%s"' % lk.string(ff[k][0]) for k in fl_old}))] = len(fl_old) == len(same_text) == 13
        new_sites = sorted({k[1] for k in ff if k[0] == 'flanger-names'} - {k[1] for k in fl_old})
        sites_fl = {s['addr']: s for s in el_fl['flanger-names']['sites']}
        row = DESC + ROW_C * ROW
        want_sites = {'site 0x%08x' % (row + 0x28), 'site 0x%08x' % (row + 0x30)}
        checks['flanger-names 0.2: two new pointer sites, row 0x6b +0x28 -> "%s", +0x30 -> "%s"' % (
            lk.string(lk.u32(row + 0x28)), lk.string(lk.u32(row + 0x30)))] = (
            set(new_sites) == want_sites and lk.string(lk.u32(row + 0x28)) == NEW_C[0x28]
            and lk.string(lk.u32(row + 0x30)) == NEW_C[0x30])
        dsite = sites_fl.get('0x%08x' % (row + 0x10))
        checks['flanger-names 0.2: one data site, row 0x6b +0x10 (the new-kit default) 0 -> %d (TIME 32)' % (
            lk.s32(row + 0x10))] = (
            dsite is not None and dsite['kind'] == 'data' and not dsite['relocs'] and lk.s32(row + 0x10) == NEW_C[0x10]
            and lko.s32(row + 0x10) == OLD_C[0x10] and len(sites_fl) == 16)

        # the stock part: every differing byte in a site
        sites = [(int(s['addr'], 16), s['len'], mid) for mid, d in el_fl.items() for s in d['sites']]
        rng = T.diff_ranges(obimg[:STOCK_END - LOAD], img[:STOCK_END - LOAD])
        outside = [(x, n) for x, n in rng if not all(any(sa <= y < sa + sn for sa, sn, _ in sites)
                                                     for y in range(x, x + n))]
        hit = sorted({(sa, mid) for x, n in rng for sa, sn, mid in sites if sa < x + n and x < sa + sn})
        checks['stock part: %d ranges (%d bytes) differ from OB01, all inside %d sites (%s)' % (
            len(rng), sum(n for _, n in rng), len(hit), ', '.join('%s 0x%08x' % (m_, sa) for sa, m_ in hit))] = (
            not outside)

        # the descriptor table and the grown table: OB01's but row 0x6b's three fields
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
            x, y = named(lk, img[lo + r * ROW:lo + (r + 1) * ROW]), named(lko, obimg[lo + r * ROW:lo + (r + 1) * ROW])
            for k_ in x:
                if x[k_] != y[k_]:
                    tab_diff.append((r, k_, y[k_], x[k_]))
        want_diff = [(ROW_C, 'default', 0, 8192), (ROW_C, 'name', 'High-pass', 'Time'), (ROW_C, 'short', 'HPF', 'TIME')]
        checks['the descriptor table: OB01\'s (names as strings) but row 0x6b\'s default 0 -> 8192, name High-pass -> '
               'Time, label HPF -> TIME'] = sorted(tab_diff) == sorted(want_diff)
        table, rows = T.simulate_boot(lk, stock_img)
        table_o, rows_o = T.simulate_boot(lko, stock_img)
        grown_diff = [(r, k_) for r in range(ROWS) for k_, v in named(lk, table[r * ROW:(r + 1) * ROW]).items()
                      if v != named(lko, table_o[r * ROW:(r + 1) * ROW])[k_]]
        checks['the grown table pg_boot builds (simulated, %d rows): OB01\'s, field by field, but row 0x6b\'s default, '
               'name and label; the added rows the same' % ROWS] = (
            sorted(grown_diff) == sorted((ROW_C, k_) for k_ in ('default', 'name', 'short'))
            and table[:ROWS0 * ROW] == img[lo:hi]
            and [(r['id'], named(lk, r['desc'])) for r in rows] == [(r['id'], named(lko, r['desc'])) for r in rows_o])

        # the layout: the blob grows by flanger-names' new strings
        lay_o, lay_f = obman['link']['layout'], man['link']['layout']
        grow = lay_f['blob_len'] - lay_o['blob_len']
        rl = (el_ob['flanger-names']['sections']['.run']['len'], el_fl['flanger-names']['sections']['.run']['len'])
        sec_moved = sorted('%s %+d' % (k, lay_f['sections'][k] - v) for k, v in lay_o['sections'].items()
                           if lay_f['sections'].get(k) != v)
        checks['layout: OB01\'s sections; flanger-names .run %d -> %d B ("TIME", "Time"); the blob grows by %d B; moved: '
               '%s' % (rl[0], rl[1], grow, ', '.join(sec_moved))] = (
            set(lay_o['sections']) == set(lay_f['sections']) and rl[1] - rl[0] == len('TIME\0Time\0')
            and 0 <= grow - (rl[1] - rl[0]) <= 4)

        # the DSP
        dspid = dsp_identity(checks)

        for k, v in checks.items():
            print('%-4s %s' % ('ok' if v else 'FAIL', k))
        if not all(checks.values()):
            for x in bad[:20]:
                print('  moved to another place: %s %s -> %s' % (x, fo[x], ff.get(x)))
            print('  outside sites: %s' % outside[:20])
            print('  table diff: %s' % tab_diff[:20])
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
            'base': 'OB01 ' + OB01_SYX_SHA, 'mods': man['mods'],
            'main_vs_ob01': {'stock_part_ranges': ['0x%08x +%d' % r for r in rng],
                             'sites_hit': ['%s 0x%08x' % (m_, sa) for sa, m_ in hit],
                             'moved_fields': len(moved), 'sections_moved': sec_moved,
                             'blob_len': [lay_o['blob_len'], lay_f['blob_len']],
                             'descriptor_table_diff': [['0x%x' % r, k_, y, x] for r, k_, y, x in tab_diff]},
            'dsp_vs_cmp4': dspid, 'checks': checks, 'log': log}
    json.dump(info, open(os.path.join(out, 'build-%s.json' % a.name), 'w'), indent=1)
    print('DSP vs CMP4 (= OB01): ' + '; '.join('%s %d B %s' % w for w in dspid['summary']))
    print('main OS vs OB01, stock part: ' + ', '.join('0x%08x +%d' % r for r in rng))
    print('%s  %s' % (s, os.path.relpath(syx, ROOT)))


if __name__ == '__main__':
    main()
