#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""SP01, the Spring pack: DJ01 with SPNG, the spring reverb, in the reverb slot (notes/SP01-spring-pack.md). The DSP
section from recipe SP01 (DT01's with fx/spring.c and its glue in code2 after flg_sync.c, the reverb's hooks on our
stubs, its state in the stock reverb's DDR region); the main OS DJ01's nine mods plus spring-names-dn2 0.1; the
version field SP01.

    venv/bin/python tools/dspsplice/build.py SP01          first: the DSP section, out/SP01/SP01-dsp/
    venv/bin/python scripts/sp01_build.py [--name SP01] [--out out/SP01]

  spring-names: scripts/sp01_make_mod.py writes its mod.json (every site checked against the stock image), the SDK
  builds it into NAME/mods/; then elekloader.patch links DJ01's nine .elemod files (sha256-checked against DJ01's
  manifest) and spring-names with --version NAME; then section 7 is replaced by out/SP01/SP01-dsp/section7.bin
  (elektron-firmware-tool -c 7).

Writes NAME.syx (+ .sha256), NAME.main.bin, build-NAME.json, build/, mods/. Checks (all must hold, or no .syx is
written):
  - the pins: stock 1.12, DJ01.syx, its main OS image and build record, DT01's DSP section (DJ01's);
  - the SP01 DSP build: its record (section7.bin and the DSP .syx), the layout and placement checks (every code
    section where our code ran on the unit), the stock delay's hooks kept, every function reached, the diet as DT01's
    plus spring.c's seven knob-law functions and its helper copies, SpgSlot inside the stock reverb's DDR region, the
    six hooks (DT01's four and the reverb's two); the sources DT01's plus fx/spring.c, fx/spring.h, spng_glue.c;
  - the DSP against DT01's: the final images the same but in code2 after flg_sync.c (0x3104b4-0x315f8c), the constant
    data of spring.c and the glue in DDR (0x8000b000, after SpgSlot) and the reverb's two CJUMPs; flg_sync.c, block 1,
    code3, code4 and the L1 data DT01's
    bytes;
  - the mods: DJ01's nine .elemod files (sha256) in DJ01's order, then spring-names 0.1; elekloader's lint;
  - the container: the ELE3 header DJ01's but the version field; sections 2, 4, 5, 8 stored byte for byte as in DJ01.syx;
    section 7 stored as in SP01-dsp.syx and depacking to its section7.bin; section 3 as elekloader packed it; the main
    OS depacks to the patcher's image, and both main OS images (DJ01's and NAME's) rebuild from their .elemod files,
    layouts and maps byte for byte;
  - against DJ01's main OS: every relocated field of the nine unchanged mods names the same place in both links; in
    the stock part every byte that differs is inside one of spring-names' 21 sites or a relocated operand of another
    mod whose target moved with the layout (each range listed); the descriptor table and the grown table pg_boot builds
    are DJ01's but the reverb rows' fields spring-names changes (listed); the layout DJ01's plus spring-names';
  - spring-names' sites as its mod.json says, with the symbols they name.
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
sys.path.insert(1, os.path.join(ROOT, 'tools', 'dspsplice'))
sys.path.insert(2, os.path.join(ROOT, 'tools', 'sharcvm'))

TOOL, STOCK, STOCK_SHA, EL, ELPY = T.TOOL, T.STOCK, T.STOCK_SHA, T.EL, T.ELPY
LOAD, DESC, ROW, ROWS0, ROWS, STOCK_END = T.LOAD, T.DESC, T.ROW, T.ROWS0, T.ROWS, T.STOCK_END
DJ01 = os.path.join(ROOT, 'out', 'DJ01')
DJ01_SYX, DJ01_SYX_SHA = os.path.join(DJ01, 'DJ01.syx'), '7edf7c59e2aeabd61ebca132a9f031f776475794e04ea37f1635ec8e5a17aed8'
DJ01_MAIN, DJ01_JSON = os.path.join(DJ01, 'DJ01.main.bin'), os.path.join(DJ01, 'build-DJ01.json')
DJ01_MAN = os.path.join(DJ01, 'build', 'DJ01-main.syx.json')
DJ01_MAP = os.path.join(DJ01, 'build', 'DJ01-main.syx.map.json')
DT01_DSP = os.path.join(ROOT, 'out', 'DT01', 'DT01-dsp')
DT01_SEC7_SHA = 'fb4b5556cdc3edca39f7a8f6391e5c30eadafacb0555e683c402dee3a1647fdb'
SP01_DSP = os.path.join(ROOT, 'out', 'SP01', 'SP01-dsp')
MODDIR = os.path.join(EL, 'examples', 'spring-names-dn2')
NEW_SRC = ['fx/spring.c', 'fx/spring.h', 'tools/dspsplice/src/spng_glue.c']
SPRING_DROP = ['spring:spring_pre_ms', 'spring:spring_rt60_s', 'spring:spring_dwel_db', 'spring:spring_hpf_hz',
               'spring:spring_tone_hz', 'spring:spring_width', 'spring:spring_tens_ms']
L1 = 0x28000000
FLG_SYNC_END = 0x3104b4                 # DT01's code2 (flg_sync.c) ends here
DDR_DATA = (0x8000b000, 0x8000c000)     # recipe SP01's dmda_ddr: spring.c's and the glue's constant data
# the rows spring-names changes, and the fields (named as T.row_fields names them)
ROW_CHANGES = {0x7b: {'default'}, 0x7c: {'default', 'fmt'}, 0x7d: {'name', 'short'},
               0x7e: {'name', 'short', 'default'}, 0x7f: {'default'}, 0x80: {'name', 'short', 'default'},
               0x83: {'name', 'short', 'max', 'default', 'fmt'}}
log = T.log


def sha(b):
    return hashlib.sha256(b).hexdigest()


def dsp_identity(checks):
    """SP01's DSP final image against DT01's: every differing byte in code2 after flg_sync.c, the data after DT01's,
    or the reverb's two hook CJUMPs"""
    import build as BD  # noqa: E402
    import ldr  # noqa: E402
    d1 = F1.dsp_regions(open(os.path.join(DT01_DSP, 'section7.bin'), 'rb').read())
    s1 = F1.dsp_regions(open(os.path.join(SP01_DSP, 'section7.bin'), 'rb').read())

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
    parts = [(L1 + FLG_SYNC_END, L1 + 0x315f8c, 'code2 after flg_sync.c: spring.c, spng_glue.c, the reverb stubs'),
             (DDR_DATA[0], DDR_DATA[1], 'constant data of spring.c and the glue, in DDR after SpgSlot')]
    hooks = [(ldr.loader_addr(2 * site), ldr.loader_addr(2 * site) + 6, 'hook CJUMP at SW %#x (%s)' % (site, label))
             for site, _, label in BD.RECIPES['SP01']['hooks'] if label.startswith('fxh_reverb')]
    why, stray = {}, []
    for a, b in diffs:
        hit = [w for lo, hi, w in parts + hooks if lo <= a and b <= hi]
        if hit:
            why.setdefault(hit[0], [0, 0])
            why[hit[0]][0] += 1
            why[hit[0]][1] += b - a
        else:
            stray.append('%#x-%#x' % (a, b))
    checks['DSP: every byte of the final image that differs from DT01\'s (%d ranges, %d B) is in code2 after '
           'flg_sync.c, the constant data in DDR or a reverb hook CJUMP (%s)' % (
               len(diffs), sum(b - a for a, b in diffs),
               '; '.join('%s: %d ranges %d B' % (k, v[0], v[1]) for k, v in why.items()))] = not stray and len(why) == 4
    jd = json.load(open(os.path.join(DT01_DSP, 'build.json')))
    js = json.load(open(os.path.join(SP01_DSP, 'build.json')))
    pd = {r['section']: (r['core'], r['bytes']) for r in jd['placed']}
    ps = {r['section']: (r['core'], r['bytes']) for r in js['placed']}
    checks['DSP: block 1, code3, code4 and the L1 data DT01\'s places and sizes (%s); code2 %s +%d -> +%d B; constant '
           'data in DDR %s +%d B' % (
               ', '.join('%s %s +%d' % (k, ps[k][0], ps[k][1]) for k in ('t38_code', 't38_code3', 't38_code4',
                                                                       't38_data')),
               ps['t38_code2'][0], pd['t38_code2'][1], ps['t38_code2'][1], ps['t38_ddata'][0], ps['t38_ddata'][1])] = (
        all(ps[k] == pd[k] for k in ('t38_code', 't38_code3', 't38_code4', 't38_data'))
        and ps['t38_code2'][0] == pd['t38_code2'][0] and set(ps) == set(pd) | {'t38_ddata'}
        and int(ps['t38_ddata'][0], 16) == DDR_DATA[0])
    hk = {p['why'].split(':')[0]: (p['old'], p['new']) for p in jd['patches']}
    hs = {p['why'].split(':')[0]: (p['old'], p['new']) for p in js['patches']}
    rev = sorted(set(hs) - set(hk))
    checks['DSP: DT01\'s four hook CJUMPs patched the same, and the reverb\'s two (%s)' % ', '.join(rev)] = (
        all(hs.get(k) == v for k, v in hk.items()) and len(rev) == 2)
    return {'diff_ranges': len(diffs), 'diff_bytes': sum(b - a for a, b in diffs), 'by_part': why, 'stray': stray,
            'ranges': ['%#x-%#x' % (a, b) for a, b in diffs], 'placed_dt01': pd, 'placed_sp01': ps}


def own_ends(link, el, fields):
    """fields (F1.Link.fields) with a field that names the end of its own mod's section (a relocation `sec:S` + the
    section's length, e.g. os-version's loop end over its .bss) named as that end: F1.Link.place names an address by
    what lies there, and what lies right after a section depends on the layout (in DJ01 the link's __bss_end, in SP01
    spring-names' .bss)"""
    out = dict(fields)
    for mid, d in el.items():
        for sec, off, typ, tgt, add in d['relocs']:
            k = (mid, sec, off)
            if k in out and tgt.startswith('sec:') and add == link.size.get((mid, tgt[4:])):
                out[k] = (out[k][0], ('end', mid, tgt[4:]))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--name', default='SP01')
    ap.add_argument('--out', default=os.path.join(ROOT, 'out', 'SP01'))
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
    dj = json.load(open(DJ01_JSON))
    dman = json.load(open(DJ01_MAN))
    dmp = json.load(open(DJ01_MAP))
    for p, want in ((STOCK, STOCK_SHA), (DJ01_SYX, DJ01_SYX_SHA), (DJ01_MAIN, dj['main_sha256']),
                    (os.path.join(DT01_DSP, 'section7.bin'), DT01_SEC7_SHA)):
        if sha(open(p, 'rb').read()) != want:
            raise SystemExit('%s: sha256 is not the expected one' % p)
    checks['DJ01 pinned: DJ01.syx %s..., its main OS %s..., its manifest for that main OS; its DSP is DT01\'s '
           'section 7 %s...' % (DJ01_SYX_SHA[:8], dj['main_sha256'][:8], DT01_SEC7_SHA[:8])] = (
        dj['syx']['sha256'] == DJ01_SYX_SHA and dman['main_sha256'] == dj['main_sha256']
        and dj['section7']['sha256'] == DT01_SEC7_SHA and all(dj['checks'].values()))

    # ---- the SP01 DSP build
    jd = json.load(open(os.path.join(DT01_DSP, 'build.json')))
    js = json.load(open(os.path.join(SP01_DSP, 'build.json')))
    sec7 = os.path.join(SP01_DSP, 'section7.bin')
    dsyx = os.path.join(ROOT, js['syx']['path'])
    sec7_sha = sha(open(sec7, 'rb').read())
    checks['SP01 DSP: build.json records section7.bin (%s...) and %s' % (sec7_sha[:8], js['syx']['path'])] = (
        js['name'] == 'SP01' and js['section7']['sha256'] == sec7_sha
        and js['syx']['sha256'] == sha(open(dsyx, 'rb').read()))
    checks['SP01 DSP: the layout check reports no problems, every code section where our code ran on the unit (%s), '
           'the stock delay\'s hooks kept' % ', '.join('%s %s' % (r['section'], r['core']) for r in js['proven_check'])] = (
        not js['layout_check']['problems'] and all(r['proven'] for r in js['proven_check'])
        and len(js['stock_hooks']) == 2 and all(h['stock_bytes_kept'] for h in js['stock_hooks']))
    checks['SP01 DSP: every function linked is reached from the hooks (%d functions)' % len(js['reach'])] = (
        all(r['reached'] for r in js['reach']))
    ds, dd = js['diet'], jd['diet']
    checks['SP01 DSP: the diet DT01\'s plus spring.c\'s (dropped %s; spring.c\'s copies of %s removed)' % (
        ', '.join(x.split(':')[1] for x in ds['dropped'] if x.startswith('spring:')),
        ', '.join(x.split(':')[1] for x in ds['removed_copies'] if x.startswith('spring:')))] = (
        [x for x in ds['dropped'] if not x.startswith('spring:')] == dd['dropped']
        and sorted(x for x in ds['dropped'] if x.startswith('spring:')) == sorted(SPRING_DROP)
        and [x for x in ds['removed_copies'] if not x.startswith('spring:')] == dd['removed_copies']
        and ds['shared_from'] == dd['shared_from'] and ds['shared'] == dd['shared'])
    sl = js['ddr_slot']
    checks['SP01 DSP: SpgSlot %s, %d B, inside the stock reverb\'s DDR state (%d B, %d left)' % (
        sl['core'], sl['bytes'], sl['region_bytes'], sl['left'])] = (
        sl['core'].startswith('0x80000018-') and 0 < sl['bytes'] <= sl['region_bytes'] == 0x59114)
    s2, sd = jd['sources'], js['sources']
    checks['SP01 DSP: the sources DT01\'s plus %s, each one recorded as it is on disk' % ', '.join(NEW_SRC)] = (
        {k: v for k, v in sd.items() if k not in NEW_SRC} == s2 and set(NEW_SRC) <= set(sd)
        and all(sd[f] == sha(open(os.path.join(ROOT, f), 'rb').read()) for f in sd))
    stubs = [r['function'] for r in js['function_table'] if r['file'] in ('stubs', 'stubs2')]
    checks['SP01 DSP: the stubs are the chorus slot\'s two (block 1) and the reverb slot\'s two (code2, %s)' % (
        ', '.join(stubs))] = stubs == ['fxh_chorus', 'fxh_chorus_init', 'fxh_reverb', 'fxh_reverb_init']
    dspid = dsp_identity(checks)

    # ---- spring-names: mod.json from the stock image, the SDK build
    T.run([sys.executable, os.path.join(ROOT, 'scripts', 'sp01_make_mod.py')])
    F1.sdk_build(MODDIR, mods_dir, env)
    sn_file = os.path.join(mods_dir, 'spring-names-0.1.elemod')
    where = [os.path.join(ROOT, 'out', d, 'mods') for d in ('DJ01', 'FL02', 'FL01', 'OB01')] + [
        os.path.join(ROOT, 'out', 'CMP4', 'main', 'mods')]
    files, ids = [], []
    for m in dman['mods']:
        hit = [os.path.join(d, m['file']) for d in where if os.path.exists(os.path.join(d, m['file']))
               and sha(open(os.path.join(d, m['file']), 'rb').read()) == m['sha256']]
        checks['DJ01\'s %s %s: its .elemod (sha256 %s...) %s' % (m['id'], m['version'], m['sha256'][:8],
                                                                os.path.relpath(hit[0], ROOT) if hit else 'NOT FOUND')] = bool(hit)
        if not hit:
            raise SystemExit('%s: no file with DJ01\'s sha256' % m['file'])
        files.append(hit[0])
        ids.append(m['id'])
    files.append(sn_file)
    ids.append('spring-names')
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
        djs, _ = T.sections(DJ01_SYX)
        fn, _ = T.sections(tmp)
        mn, _ = T.sections(mainsyx)
        st, _ = T.sections(STOCK)
        sp7, _ = T.sections(dsyx)
        hdr, h2 = T.header(tmp), T.header(DJ01_SYX)
        checks['ELE3 header: version field %s, the rest as DJ01\'s' % a.name] = (
            hdr[0x14:0x18] == a.name.encode() and hdr[:0x14] == h2[:0x14] and hdr[0x18:] == h2[0x18:])
        checks['section ids as DJ01\'s'] = sorted(fn) == sorted(djs)
        for i in sorted(djs):
            if i not in (3, 7):
                checks['section %d stored byte for byte as in DJ01.syx%s' % (i, ' (and stock)' if fn[i] == st[i]
                                                                            else '')] = fn[i] == djs[i]
        checks['section 7 stored byte for byte as in %s' % js['syx']['path']] = fn[7] == sp7[7]
        checks['section 7 depacks to %s (sha256 %s...)' % (os.path.relpath(sec7, ROOT), sec7_sha[:8])] = (
            T.depack7(tmp) == open(sec7, 'rb').read())
        checks['section 3 stored as elekloader packed it'] = fn[3] == mn[3]
        img = T.main_image(tmp)
        checks['the main OS depacks to the patcher\'s image (%s...)' % man['main_sha256'][:8]] = (
            sha(img) == man['main_sha256'])
        mids = [(m['id'], m['version']) for m in man['mods']]
        checks['mods: DJ01\'s nine in DJ01\'s order, then spring-names 0.1 (%s)' % ', '.join('%s %s' % x for x in mids)] = (
            [m['id'] for m in man['mods']] == ids and mids[-1] == ('spring-names', '0.1')
            and [(m['id'], m['version'], m['sha256']) for m in man['mods'][:-1]]
            == [(m['id'], m['version'], m['sha256']) for m in dman['mods']]
            and man['mods'][-1]['sha256'] == sha(open(sn_file, 'rb').read()))

        dimg = open(DJ01_MAIN, 'rb').read()
        stock_img = open(T.STOCK_MAIN, 'rb').read()
        el_d = {m['id']: OB.load_elemod(f) for m, f in zip(dman['mods'], files)}
        el_n = {m['id']: OB.load_elemod(f) for m, f in zip(man['mods'], files)}
        checks['DJ01\'s main OS = its nine .elemod files relinked from its manifest\'s layout and map, byte for '
               'byte'] = OB.reconstruct(stock_img, dman, dmp, el_d) == dimg
        checks['%s\'s main OS = its ten .elemod files relinked from its layout and map, byte for byte' % a.name] = (
            OB.reconstruct(stock_img, man, mp, el_n) == img)

        # every relocated field of the nine unchanged mods: the same place in both links
        lo_, ln_ = F1.Link(dman, dmp, el_d), F1.Link(man, mp, el_n)
        fo, fnw = own_ends(lo_, el_d, lo_.fields()), own_ends(ln_, el_n, ln_.fields())
        same = [k for k in fo]
        moved = [k for k in same if k in fnw and fo[k][0] != fnw[k][0]]
        bad = [k for k in same if k not in fnw or fo[k][1] != fnw[k][1]]
        checks['every relocated field of DJ01\'s nine mods names the same place in both links (%d fields, %d moved '
               'with the layout)' % (len(same), len(moved))] = (
            not bad and set(same) == {k for k in fnw if k[0] != 'spring-names'})

        # spring-names' sites: as mod.json says
        sn = el_n['spring-names']
        sym = {k: int(v, 16) for k, v in mp.items()}
        mj = json.load(open(os.path.join(MODDIR, 'mod.json')))
        sites = {int(s_['addr'], 16): s_ for s_ in sn['sites']}

        def at(addr, n):
            return img[addr - LOAD:addr - LOAD + n]
        want = {}
        for s_ in mj['sites']:
            x, stockb = int(s_['addr'], 16), bytes.fromhex(s_['stock'])
            if s_['op'] == 'keep2':
                want[x] = stockb[:2] + struct.pack('>I', sym[s_['target']])
            elif s_['op'] == 'ptr':
                want[x] = struct.pack('>I', sym[s_['target']])
            else:
                want[x] = bytes.fromhex(s_['new'])
        got = {x: at(x, len(w)) for x, w in want.items()}
        checks['spring-names: its %d sites written as its mod.json says (%s)' % (len(want), ', '.join(
            '0x%08x %s' % (x, got[x].hex()) for x in sorted(want)))] = (
            set(sites) == set(want) and len(want) == 21 and all(got[x] == w for x, w in want.items())
            and all(sites[x]['len'] == len(w) for x, w in want.items()))
        lk = T.Linked(img, mp)
        lkd = T.Linked(dimg, dmp)
        strs = {k: lk.string(sym[k]) for k in ('snm_spring', 'snm_mode', 'snm_mode_s', 'snm_dwel', 'snm_dwel_s',
                                               'snm_tone', 'snm_tone_s', 'snm_wdth', 'snm_wdth_s')}
        checks['spring-names: its strings (%s)' % ', '.join('%s "%s"' % kv for kv in strs.items())] = (
            list(strs.values()) == ['Spring', 'Spring Mode', 'MODE', 'Dwell', 'DWEL', 'Tone', 'TONE', 'Width', 'WDTH'])

        # the stock part: every differing byte in one of spring-names' sites, or in a relocated field of another mod's
        # site whose target moved with the layout (the check above: it names the same place)
        rng = T.diff_ranges(dimg[:STOCK_END - LOAD], img[:STOCK_END - LOAD])
        spans = [(x, s_['len']) for x, s_ in sites.items()]
        relo = []
        for mid, d in el_d.items():
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
        checks['stock part: %d ranges (%d bytes) differ from DJ01: %d inside spring-names\' sites (%s); %d are '
               'relocated operands of other mods\' sites whose target moved with the layout (%s)' % (
                   len(rng), sum(n for _, n in rng), len(ours), ', '.join('0x%08x+%d' % r for r in ours), len(theirs),
                   ', '.join('0x%08x+%d %s' % (r[0], r[1], [m_ for ra, rn, m_ in relo if ra <= r[0] < ra + rn][0])
                             for r in theirs if any(ra <= r[0] < ra + rn for ra, rn, _ in relo)))] = not outside

        # the descriptor table and the grown table: DJ01's but the reverb rows' fields spring-names changes
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
            x, y = named(lk, img[lo + r * ROW:lo + (r + 1) * ROW]), named(lkd, dimg[lo + r * ROW:lo + (r + 1) * ROW])
            for k_ in x:
                if x[k_] != y[k_]:
                    tab_diff.append((r, k_, y[k_], x[k_]))
        want_diff = sorted((r, k_) for r, ks in ROW_CHANGES.items() for k_ in ks)
        checks['the descriptor table: DJ01\'s (names as strings, formatters by name) but the reverb rows\' fields '
               'spring-names changes (%s)' % '; '.join('0x%x %s %s -> %s' % d for d in tab_diff)] = (
            sorted((r, k_) for r, k_, _, _ in tab_diff) == want_diff)
        table, rows = T.simulate_boot(lk, stock_img)
        table_o, rows_o = T.simulate_boot(lkd, stock_img)
        grown_diff = sorted((r, k_) for r in range(ROWS) for k_, v in named(lk, table[r * ROW:(r + 1) * ROW]).items()
                            if v != named(lkd, table_o[r * ROW:(r + 1) * ROW])[k_])
        checks['the grown table pg_boot builds (simulated, %d rows): DJ01\'s, field by field, but the same reverb '
               'rows\' fields; the added rows the same' % ROWS] = (
            grown_diff == want_diff and table[:ROWS0 * ROW] == img[lo:hi]
            and [(r['id'], named(lk, r['desc'])) for r in rows] == [(r['id'], named(lkd, r['desc'])) for r in rows_o])

        # the layout
        lay_o, lay_n = dman['link']['layout'], man['link']['layout']
        sec_moved = sorted('%s %+d' % (k, lay_n['sections'][k] - v) for k, v in lay_o['sections'].items()
                           if lay_n['sections'].get(k) != v)
        new_secs = sorted(set(lay_n['sections']) - set(lay_o['sections']))
        checks['layout: DJ01\'s sections and spring-names\' %s; the blob %d -> %d B; moved: %s' % (
            new_secs, lay_o['blob_len'], lay_n['blob_len'], ', '.join(sec_moved) or 'none')] = (
            set(lay_o['sections']) <= set(lay_n['sections']) and all(k.startswith('spring-names ') for k in new_secs)
            and lay_n['ddr'][1] <= lay_n['ddr'][0] + lay_n['ddr_size'])

        for k, v in checks.items():
            print('%-4s %s' % ('ok' if v else 'FAIL', k))
        if not all(checks.values()):
            for x in bad[:20]:
                print('  moved to another place: %s %s -> %s' % (x, fo[x], fnw.get(x)))
            print('  outside sites: %s' % outside[:20])
            print('  table diff: %s' % tab_diff[:30])
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
    blob_rng = T.diff_ranges(dimg[STOCK_END - LOAD:], img[STOCK_END - LOAD:])
    info = {'name': a.name, 'syx': {'path': os.path.relpath(syx, ROOT), 'sha256': s, 'bytes': os.path.getsize(syx)},
            'main_sha256': sha(img), 'section7': {'path': os.path.relpath(sec7, ROOT), 'sha256': sec7_sha},
            'base': 'DJ01 ' + DJ01_SYX_SHA, 'mods': man['mods'],
            'dsp_vs_dt01': dspid, 'dsp_ddr_slot': sl,
            'main_vs_dj01': {'stock_part_ranges': ['0x%08x +%d' % r for r in rng],
                             'stock_part_ours': ['0x%08x +%d' % r for r in ours],
                             'stock_part_moved_operands': ['0x%08x +%d' % r for r in theirs],
                             'blob': {'len': [lay_o['blob_len'], lay_n['blob_len']],
                                      'ranges_differing': len(blob_rng),
                                      'note': 'the appended blob: spring-names\' .run and .bss added (ids sort the '
                                              'link), the sections after them moved; every field of the nine mods '
                                              'names the same place (checked)'},
                             'moved_fields': len(moved), 'sections_moved': sec_moved, 'new_sections': new_secs,
                             'descriptor_table_diff': [['0x%x' % r, k_, str(y), str(x)] for r, k_, y, x in tab_diff]},
            'symbols': {k: mp[k] for k in sorted(mp) if k.startswith('snm_')},
            'checks': checks, 'log': log}
    json.dump(info, open(os.path.join(out, 'build-%s.json' % a.name), 'w'), indent=1)
    print('DSP vs DT01: %d ranges, %d B: %s' % (dspid['diff_ranges'], dspid['diff_bytes'], dspid['by_part']))
    print('main OS vs DJ01, stock part: ' + ', '.join('0x%08x +%d' % r for r in rng))
    print('%s  %s' % (s, os.path.relpath(syx, ROOT)))


if __name__ == '__main__':
    main()
