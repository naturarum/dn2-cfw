#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""BA04: base 0.2 with the Turing arp's second version (notes/BA04-turing-arp-v2.md). BA02 (DC02 + lfo-dejavu 0.2,
arp-modes 0.1, boot-mark 0.1; on the unit since 2026-10-28) with arp-modes 0.3 (TM, the Turing Machine loop of notes
from the lowest note held, in the track's scale, and its ARP menu: LOOP CHANGE on knob E, the loop in the step graph)
and boot-mark 0.2 (the mark the right way up, as in BA03: right on the unit). BA03 (arp-modes 0.2's TM) is
scripts/ba03_build.py. The DSP is DC02's section 7 byte for byte (nothing here runs on the DSP).

    venv/bin/python scripts/ba04_build.py [--name BA04] [--out out/BA04]

  lfo-dejavu 0.2: scripts/ba02_make_dv.py writes its mod.json (every site checked against the stock image), the SDK
  builds it into NAME/mods/; then elekloader.patch links DJ01's eight other .elemod files (sha256-checked against
  DJ01's manifest) in DJ01's order with lfo-dejavu 0.2 in 0.1's place, with --version NAME; then section 7 is
  replaced by DC02's.

Writes NAME.syx (+ .sha256), NAME.main.bin, build-NAME.json, build/, mods/. Checks (all must hold, or no .syx is
written):
  - the pins: stock 1.12, DC02.syx, DC02's DSP section and build record (its main OS DJ01's), DJ01's main OS and
    manifest;
  - the mods: DJ01's nine but lfo-dejavu (sha256) in DJ01's order, lfo-dejavu 0.2 in 0.1's place; elekloader's lint;
  - the container: the ELE3 header DC02's but the version field; sections 2, 4, 5, 8 stored byte for byte as in
    DC02.syx; section 7 depacks to DC02's section7.bin; section 3 as elekloader packed it; the main OS depacks to the
    patcher's image, and both main OS images (DJ01's and NAME's) rebuild from their .elemod files, layouts and maps;
  - against DJ01's main OS: every relocated field of the eight unchanged mods names the same place in both links; in
    the stock part every byte that differs is inside one of lfo-dejavu's sites or is a relocated operand of another
    mod's site whose target moved with the layout (each range listed); lfo-dejavu 0.2's 26 sites written as dv.s
    documents them; the descriptor table and the grown table (pg_boot simulated) DJ01's, field by field, but the WAVE
    rows' maximum (15 -> 16, KEY); the layout DJ01's sections (some moved, listed).
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

TOOL, STOCK, STOCK_SHA, EL, ELPY = T.TOOL, T.STOCK, T.STOCK_SHA, T.EL, T.ELPY
LOAD, DESC, ROW, ROWS0, ROWS, STOCK_END = T.LOAD, T.DESC, T.ROW, T.ROWS0, T.ROWS, T.STOCK_END
DJ01 = os.path.join(ROOT, 'out', 'DJ01')
DJ01_JSON = os.path.join(DJ01, 'build-DJ01.json')
DJ01_MAIN = os.path.join(DJ01, 'DJ01.main.bin')
DJ01_MAN = os.path.join(DJ01, 'build', 'DJ01-main.syx.json')
DJ01_MAP = os.path.join(DJ01, 'build', 'DJ01-main.syx.map.json')
DC02 = os.path.join(ROOT, 'out', 'DC02')
DC02_SYX, DC02_SYX_SHA = os.path.join(DC02, 'DC02.syx'), '330dfa9bb63fe32e4e4d8499be071b6f1556af3702a552a6596ba9c48a2f0971'
DC02_JSON = os.path.join(DC02, 'build-DC02.json')
DC02_SEC7, DC02_SEC7_SHA = (os.path.join(DC02, 'DC02-dsp', 'section7.bin'),
                            '9fd315f922f4ea71952d3e0074132ac3df50ae532ba058da39bf032e677b1026')
DV_DIR = os.path.join(EL, 'examples', 'lfo-dejavu-dn2')
DV_VERSION = '0.2'
NEW_MODS = [('arp-modes', '0.3', os.path.join(EL, 'examples', 'arp-modes-dn2'), 'ba04_make_arp.py'),
            ('boot-mark', '0.2', os.path.join(EL, 'examples', 'boot-mark-dn2'), 'ba03_make_boot.py')]
WAVE_ROWS = (0x4f, 0x59, 0x63)
WTAB = 0x4020b7f8
log = T.log


def sha(b):
    return hashlib.sha256(b).hexdigest()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--name', default='BA04')
    ap.add_argument('--out', default=os.path.join(ROOT, 'out', 'BA04'))
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
    dc = json.load(open(DC02_JSON))
    for p, want in ((STOCK, STOCK_SHA), (DC02_SYX, DC02_SYX_SHA), (DJ01_MAIN, dj['main_sha256']),
                    (DC02_SEC7, DC02_SEC7_SHA)):
        if sha(open(p, 'rb').read()) != want:
            raise SystemExit('%s: sha256 is not the expected one' % p)
    checks['DC02 pinned: DC02.syx %s..., its DSP section 7 %s..., its main OS DJ01\'s (%s...), every check of its '
           'build held; DJ01\'s manifest for that main OS' % (DC02_SYX_SHA[:8], DC02_SEC7_SHA[:8],
                                                              dj['main_sha256'][:8])] = (
        dc['syx']['sha256'] == DC02_SYX_SHA and dc['section7']['sha256'] == DC02_SEC7_SHA
        and dc['main_sha256'] == dj['main_sha256'] == dman['main_sha256'] and all(dc['checks'].values())
        and all(dj['checks'].values()))

    # ---- lfo-dejavu 0.2: mod.json from the stock image, the SDK build
    T.run([sys.executable, os.path.join(ROOT, 'scripts', 'ba02_make_dv.py')])
    F1.sdk_build(DV_DIR, mods_dir, env)
    dv_file = os.path.join(mods_dir, 'lfo-dejavu-%s.elemod' % DV_VERSION)
    where = [os.path.join(ROOT, 'out', d, 'mods') for d in ('DJ01', 'FL02', 'FL01', 'OB01')] + [
        os.path.join(ROOT, 'out', 'CMP4', 'main', 'mods')]
    files, ids, kept = [], [], []
    for m in dman['mods']:
        if m['id'] == 'lfo-dejavu':
            files.append(dv_file)
            ids.append(m['id'])
            continue
        hit = [os.path.join(d, m['file']) for d in where if os.path.exists(os.path.join(d, m['file']))
               and sha(open(os.path.join(d, m['file']), 'rb').read()) == m['sha256']]
        checks['DJ01\'s %s %s: its .elemod (sha256 %s...) %s' % (m['id'], m['version'], m['sha256'][:8],
                                                                os.path.relpath(hit[0], ROOT) if hit else 'NOT FOUND')] = bool(hit)
        if not hit:
            raise SystemExit('%s: no file with DJ01\'s sha256' % m['file'])
        files.append(hit[0])
        ids.append(m['id'])
        kept.append(m['id'])
    for mid, ver, mdir, maker in NEW_MODS:
        T.run([sys.executable, os.path.join(ROOT, 'scripts', maker)])
        F1.sdk_build(mdir, mods_dir, env)
        files.append(os.path.join(mods_dir, '%s-%s.elemod' % (mid, ver)))
        ids.append(mid)
    T.run([ELPY, '-m', 'elekloader.lint'] + files + ['--stock', STOCK], cwd=EL, env=env)
    mainsyx = os.path.join(build_dir, a.name + '-main.syx')
    cmd = [ELPY, '-m', 'elekloader.patch', '--stock', STOCK]
    for f in files:
        cmd += ['--mod', f]
    T.run(cmd + ['--version', a.name, '--out', mainsyx], cwd=EL, env=env)
    man = json.load(open(mainsyx + '.json'))
    mp = json.load(open(mainsyx + '.map.json'))

    # ---- the container: DC02's DSP section
    syx = os.path.join(out, a.name + '.syx')
    tmp = syx + '.tmp'
    T.run([TOOL, '-i', mainsyx, '-c', '7', DC02_SEC7, '-o', tmp])
    try:
        ds, _ = T.sections(DC02_SYX)
        fn, _ = T.sections(tmp)
        mn, _ = T.sections(mainsyx)
        st, _ = T.sections(STOCK)
        hdr, h2 = T.header(tmp), T.header(DC02_SYX)
        checks['ELE3 header: version field %s, the rest as DC02\'s' % a.name] = (
            hdr[0x14:0x18] == a.name.encode() and hdr[:0x14] == h2[:0x14] and hdr[0x18:] == h2[0x18:])
        checks['section ids as DC02\'s'] = sorted(fn) == sorted(ds)
        for i in sorted(ds):
            if i != 3:
                checks['section %d stored byte for byte as in DC02.syx%s' % (i, ' (and stock)' if i != 7 and
                                                                            fn[i] == st[i] else '')] = fn[i] == ds[i]
        checks['section 7 depacks to DC02\'s section7.bin (%s...): the DSP is DC02\'s byte for byte' % (
            DC02_SEC7_SHA[:8])] = T.depack7(tmp) == open(DC02_SEC7, 'rb').read()
        checks['section 3 stored as elekloader packed it'] = fn[3] == mn[3]
        img = T.main_image(tmp)
        checks['the main OS depacks to the patcher\'s image (%s...)' % man['main_sha256'][:8]] = (
            sha(img) == man['main_sha256'])
        mids = [(m['id'], m['version']) for m in man['mods']]
        checks['mods: DJ01\'s in DJ01\'s order, lfo-dejavu %s in 0.1\'s place, then %s (%s)' % (
            DV_VERSION, ', '.join('%s %s' % (m_, v_) for m_, v_, _, _ in NEW_MODS),
            ', '.join('%s %s' % x for x in mids))] = (
            [m['id'] for m in man['mods']] == ids and mids[len(dman['mods']):] == [(m_, v_) for m_, v_, _, _ in NEW_MODS]
            and [(m['id'], m['version'], m['sha256']) for m in man['mods'] if m['id'] in kept]
            == [(m['id'], m['version'], m['sha256']) for m in dman['mods'] if m['id'] in kept]
            and dict(mids)['lfo-dejavu'] == DV_VERSION
            and [m for m in man['mods'] if m['id'] == 'lfo-dejavu'][0]['sha256'] == sha(open(dv_file, 'rb').read()))

        dimg = open(DJ01_MAIN, 'rb').read()
        stock_img = open(T.STOCK_MAIN, 'rb').read()
        dfiles = [f if m['id'] != 'lfo-dejavu' else os.path.join(DJ01, 'mods', m['file'])
                  for m, f in zip(dman['mods'], files)]
        el_d = {m['id']: OB.load_elemod(f) for m, f in zip(dman['mods'], dfiles)}
        el_n = {m['id']: OB.load_elemod(f) for m, f in zip(man['mods'], files)}
        el_n_files = dict(zip([m['id'] for m in man['mods']], files))
        checks['DJ01\'s main OS = its nine .elemod files relinked from its manifest\'s layout and map, byte for '
               'byte'] = OB.reconstruct(stock_img, dman, dmp, el_d) == dimg
        checks['%s\'s main OS = its %d .elemod files relinked from its layout and map, byte for byte' % (
            a.name, len(files))] = OB.reconstruct(stock_img, man, mp, el_n) == img

        # every relocated field of the unchanged mods: the same place in both links
        lo_, ln_ = F1.Link(dman, dmp, el_d), F1.Link(man, mp, el_n)
        fo, fnw = lo_.fields(), ln_.fields()
        same = [k for k in fo if k[0] in kept]
        moved = [k for k in fo if k in fnw and fo[k][0] != fnw[k][0]]
        bad = [k for k in same if k not in fnw or fo[k][1] != fnw[k][1]]
        checks['every relocated field of DJ01\'s %d unchanged mods names the same place in both links (%d fields, %d '
               'moved with the layout)' % (len(kept), len(same), len([k for k in moved if k[0] in kept]))] = (
            not bad and set(same) == {k for k in fnw if k[0] in kept})

        # lfo-dejavu's sites
        dv = el_n['lfo-dejavu']
        lk = T.Linked(img, mp)
        lkd = T.Linked(dimg, dmp)
        sym = {k: int(v, 16) for k, v in mp.items()}
        sites = {int(s_['addr'], 16): s_ for s_ in dv['sites']}

        def at(addr, n):
            return img[addr - LOAD:addr - LOAD + n]

        def jsr(t, pad=0):
            return bytes.fromhex('4eb9') + struct.pack('>I', sym[t]) + bytes.fromhex('4e71') * pad

        def jmp(t):
            return bytes.fromhex('4ef9') + struct.pack('>I', sym[t])
        want = {}
        for r in WAVE_ROWS:
            want[DESC + r * ROW + 0x0c] = bytes.fromhex('00001000')
            want[DESC + r * ROW + 0x34] = struct.pack('>I', sym['dv_fmt'])
        want[0x400272d4] = jsr('dv_frame_hook')
        want[0x40137b60] = bytes.fromhex('7e10')
        want[0x40137b66] = bytes.fromhex('7610')
        want[0x40137b88] = jsr('dv_mode_a')
        want[0x40137cda] = bytes.fromhex('41f9') + struct.pack('>I', sym['dv_wtab_a'])
        want[0x40137d98] = jmp('dv_add_a')
        want[0x40137770] = jsr('dv_mode_b', 1)
        want[0x40137788] = bytes.fromhex('7c10')
        want[0x4013778e] = bytes.fromhex('7610')
        want[0x401378ce] = bytes.fromhex('43f9') + struct.pack('>I', sym['dv_wtab_b'])
        want[0x40137c40] = jmp('lfx_trig_a')
        want[0x40137ccc] = jsr('lfx_eval_a')
        want[0x40137dbc] = jsr('lfx_page_a')
        want[0x40137bcc] = bytes.fromhex('4e714e71')
        want[0x40137c00] = bytes.fromhex('4e714e71')
        want[0x40137836] = jmp('lfx_trig_b')
        want[0x401378d8] = jsr('lfx_eval_b', 2)
        want[0x401379d8] = jsr('lfx_page_b')
        want[0x401377c4] = bytes.fromhex('4e714e71')
        want[0x401377fe] = bytes.fromhex('4e71')
        got = {x: at(x, len(w)) for x, w in want.items()}
        checks['lfo-dejavu %s: its %d sites written as dv.s documents them (%s)' % (DV_VERSION, len(want), ', '.join(
            '0x%08x %s' % (x, got[x].hex()) for x in sorted(want)))] = (
            set(sites) == set(want) and all(got[x] == w for x, w in want.items())
            and all(sites[x]['len'] == len(w) for x, w in want.items()))
        am = el_n['arp-modes']
        am_want = {0x4002a13c: jmp('am_mode') + bytes.fromhex('4e71') * 3, 0x4004befa: bytes.fromhex('746b'),
                   0x4004bf00: bytes.fromhex('726b'), 0x4004bfb4: bytes.fromhex('706b'),
                   0x4004bfc6: bytes.fromhex('726b'), 0x400dd694: bytes.fromhex('746b'),
                   0x401d4f38: struct.pack('>I', sym['am_s_tm']), 0x401d5090: struct.pack('>I', sym['am_menu_draw']),
                   0x401d50ac: struct.pack('>I', sym['am_menu_tick']), 0x400188de: jsr('am_mode_name'),
                   0x40018eee: jsr('am_knob_mode'), 0x400190ae: jsr('am_knob_e')}
        am_sites = {int(s_['addr'], 16): s_ for s_ in am['sites']}
        am_got = {x: at(x, len(w)) for x, w in am_want.items()}
        checks['arp-modes 0.3: its %d sites written as am.s and am.c document them (%s)' % (len(am_want), ', '.join(
            '0x%08x %s' % (x, am_got[x].hex()) for x in sorted(am_want)))] = (
            set(am_sites) == set(am_want) and all(am_got[x] == w for x, w in am_want.items())
            and all(am_sites[x]['len'] == len(w) for x, w in am_want.items()))
        bm = el_n['boot-mark']
        bm_sites = {int(s_['addr'], 16): s_ for s_ in bm['sites']}
        checks['boot-mark 0.2: its site 0x400d3ac4 (the intro\'s logo blit) is jsr bm_draw (%s)' % at(0x400d3ac4, 6).hex()] = (
            set(bm_sites) == {0x400d3ac4} and at(0x400d3ac4, 6) == jsr('bm_draw'))
        tabs = {}
        for t in ('dv_wtab_a', 'dv_wtab_b'):
            tabs[t] = [lk.u32(sym[t] + 4 * i) for i in range(17)]
        stock_tab = [T.rd32(stock_img, WTAB + 4 * i) for i in range(6)]
        checks['lfo-dejavu: dv_wtab_a and dv_wtab_b = the stock table\'s six (%s), 0, then ten times their own '
               'waveform stub' % ' '.join('%08x' % x for x in stock_tab)] = (
            all(tabs[t][:6] == stock_tab and tabs[t][6] == 0 and len(set(tabs[t][7:])) == 1
                and lk.run_start <= tabs[t][7] < lk.run_end for t in tabs) and tabs['dv_wtab_a'][7] != tabs['dv_wtab_b'][7])

        # the stock part against DJ01's: every differing byte in one of lfo-dejavu's sites, or in a relocated field of
        # another mod's site whose target moved with the layout
        rng = T.diff_ranges(dimg[:STOCK_END - LOAD], img[:STOCK_END - LOAD])
        spans = ([(x, s_['len']) for x, s_ in sites.items()] + [(x, s_['len']) for x, s_ in am_sites.items()]
                 + [(x, s_['len']) for x, s_ in bm_sites.items()])
        relo = []
        for mid, d in el_d.items():
            if mid not in kept:
                continue
            for s_ in d['sites']:
                for off, typ, tgt, add in s_['relocs']:
                    k = (mid, 'site ' + s_['addr'], off)
                    if k in moved:
                        relo.append((int(s_['addr'], 16) + off, 4 if typ in ('abs32', 'pc32') else 2, mid))
        outside = [(x, n) for x, n in rng if not all(any(sa <= y < sa + sn for sa, sn in spans) or
                                                     any(ra <= y < ra + rn for ra, rn, _ in relo)
                                                     for y in range(x, x + n))]
        ours = [r for r in rng if any(sa <= r[0] < sa + sn for sa, sn in spans)]
        theirs = [r for r in rng if r not in ours]
        checks['stock part: %d ranges (%d bytes) differ from DJ01: %d inside lfo-dejavu\'s, arp-modes\' or boot-mark\'s sites (%s); %d are relocated '
               'operands of other mods\' sites whose target moved with the layout (%s)' % (
                   len(rng), sum(n for _, n in rng), len(ours), ', '.join('0x%08x+%d' % r for r in ours), len(theirs),
                   ', '.join('0x%08x+%d %s' % (r[0], r[1], [m_ for ra, rn, m_ in relo if ra <= r[0] < ra + rn][0])
                             for r in theirs if any(ra <= r[0] < ra + rn for ra, rn, _ in relo)))] = not outside

        # the descriptor table and the grown table: DJ01's
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
        want_diff = sorted((r, 'max') for r in WAVE_ROWS)
        checks['the descriptor table: DJ01\'s (names as strings, formatters by name) but the WAVE rows\' maximum, '
               'KEY (%s)' % '; '.join('0x%x %s %s -> %s' % d for d in tab_diff)] = (
            sorted((r, k_) for r, k_, _, _ in tab_diff) == want_diff
            and all(y == 15 << 8 and x == 16 << 8 for _, _, y, x in tab_diff))
        table, rows = T.simulate_boot(lk, stock_img)
        table_o, rows_o = T.simulate_boot(lkd, stock_img)
        grown_diff = sorted((r, k_) for r in range(ROWS) for k_, v in named(lk, table[r * ROW:(r + 1) * ROW]).items()
                            if v != named(lkd, table_o[r * ROW:(r + 1) * ROW])[k_])
        checks['the grown table pg_boot builds (simulated, %d rows): DJ01\'s, field by field, but the WAVE rows\' '
               'maximum; the added rows the same' % ROWS] = (
            grown_diff == want_diff and table[:ROWS0 * ROW] == img[lo:hi]
            and [(r['id'], named(lk, r['desc'])) for r in rows] == [(r['id'], named(lkd, r['desc'])) for r in rows_o])

        # the layout
        lay_o, lay_n = dman['link']['layout'], man['link']['layout']
        sec_moved = sorted('%s %+d' % (k, lay_n['sections'][k] - v) for k, v in lay_o['sections'].items()
                           if lay_n['sections'].get(k) != v)
        new_secs = sorted(set(lay_n['sections']) - set(lay_o['sections']))
        checks['layout: DJ01\'s sections%s; the blob %d -> %d B; moved: %s' % (
            ' and ' + ', '.join(new_secs) if new_secs else '', lay_o['blob_len'], lay_n['blob_len'],
            ', '.join(sec_moved) or 'none')] = (
            set(lay_o['sections']) <= set(lay_n['sections'])
            and all(any(k.startswith(m_ + ' ') for m_, _, _, _ in NEW_MODS) for k in new_secs)
            and lay_n['ddr'][1] <= lay_n['ddr'][0] + lay_n['ddr_size'])

        for k, v in checks.items():
            print('%-4s %s' % ('ok' if v else 'FAIL', k))
        if not all(checks.values()):
            for x in bad[:20]:
                print('  moved to another place: %s %s -> %s' % (x, fo[x], fnw.get(x)))
            print('  outside sites: %s' % outside[:20])
            print('  table diff: %s' % tab_diff[:20])
            print('  grown diff: %s' % grown_diff[:20])
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
            'main_sha256': sha(img), 'section7': {'path': os.path.relpath(DC02_SEC7, ROOT), 'sha256': DC02_SEC7_SHA},
            'base': 'DC02 ' + DC02_SYX_SHA, 'mods': man['mods'],
            'main_vs_dj01': {'stock_part_ranges': ['0x%08x +%d' % r for r in rng],
                             'stock_part_ours': ['0x%08x +%d' % r for r in ours],
                             'stock_part_moved_operands': ['0x%08x +%d' % r for r in theirs],
                             'blob_len': [lay_o['blob_len'], lay_n['blob_len']],
                             'sections_moved': sec_moved, 'new_sections': new_secs},
            'symbols': {k: mp[k] for k in sorted(mp) if k.startswith(('dv_', 'lfx_', 'am_', 'bm_'))},
            'checks': checks, 'log': log}
    json.dump(info, open(os.path.join(out, 'build-%s.json' % a.name), 'w'), indent=1)
    print('main OS vs DJ01, stock part: ' + ', '.join('0x%08x +%d' % r for r in rng))
    print('%s  %s' % (s, os.path.relpath(syx, ROOT)))


if __name__ == '__main__':
    main()
