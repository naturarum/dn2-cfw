#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""DJ01: DT01 + the Deja vu LFO waves (notes/T2.8-dejavu-lfo.md). DT01's DSP section byte for byte (the LFOs run on
the ColdFire); the main OS is DT01's (= FL02's) eight mods plus lfo-dejavu-dn2 0.1; the version field DJ01.

    venv/bin/python scripts/dj01_build.py [--name DJ01] [--out out/DJ01]

  lfo-dejavu: scripts/dj01_make_mod.py writes its mod.json (every site checked against the stock image), the SDK
  builds it into NAME/mods/; then elekloader.patch links DT01's eight .elemod files (sha256-checked against FL02's
  manifest, which DT01's build checked) and lfo-dejavu with --version NAME; then section 7 is replaced by DT01's
  (out/DT01/DT01-dsp/section7.bin, elektron-firmware-tool -c 7).

Writes NAME.syx (+ .sha256), NAME.main.bin, build-NAME.json, build/, mods/. Checks (all must hold, or no .syx is
written):
  - the pins: stock 1.12, DT01.syx and its main OS image, DT01's DSP section, DT01's build record;
  - the mods: DT01's eight .elemod files (sha256) in DT01's order, then lfo-dejavu 0.1; elekloader's lint;
  - the container: the ELE3 header DT01's but the version field; sections 2, 4, 5, 7, 8 stored byte for byte as in
    DT01.syx; section 7 depacks to DT01's section7.bin; section 3 as elekloader packed it; the main OS depacks to
    the patcher's image, and both main OS images (DT01's and NAME's) rebuild from their .elemod files, layouts and
    maps byte for byte;
  - against DT01's main OS: every relocated field of the eight unchanged mods names the same place in both links;
    in the stock part every byte that differs is inside one of lfo-dejavu's 16 sites (each range listed); the
    descriptor table and the grown table pg_boot builds are DT01's but the three WAVE rows' maximum (6 -> 15) and
    value text (dv_fmt); the layout DT01's sections plus lfo-dejavu's (the others moved, listed);
  - lfo-dejavu's sites as dv.s documents them, with the symbols they name; its waveform tables' first six entries
    the stock table's.
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
DT01 = os.path.join(ROOT, 'out', 'DT01')
DT01_SYX, DT01_SYX_SHA = os.path.join(DT01, 'DT01.syx'), '5999998c6ca63f70b6c91372891df77472b8c4e32f59e6795a82d708ffce3749'
DT01_MAIN, DT01_JSON = os.path.join(DT01, 'DT01.main.bin'), os.path.join(DT01, 'build-DT01.json')
DT01_MAN = os.path.join(DT01, 'build', 'DT01-main.syx.json')
DT01_MAP = os.path.join(DT01, 'build', 'DT01-main.syx.map.json')
DT01_SEC7 = os.path.join(DT01, 'DT01-dsp', 'section7.bin')
MODDIR = os.path.join(EL, 'examples', 'lfo-dejavu-dn2')
WAVE_ROWS = (0x4f, 0x59, 0x63)
FMT_WAVE = 0x400e3610
WTAB = 0x4020b7f8
log = T.log


def sha(b):
    return hashlib.sha256(b).hexdigest()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--name', default='DJ01')
    ap.add_argument('--out', default=os.path.join(ROOT, 'out', 'DJ01'))
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
    dj = json.load(open(DT01_JSON))
    dman = json.load(open(DT01_MAN))
    dmp = json.load(open(DT01_MAP))
    for p, want in ((STOCK, STOCK_SHA), (DT01_SYX, DT01_SYX_SHA), (DT01_MAIN, dj['main_sha256']),
                    (DT01_SEC7, dj['section7']['sha256'])):
        if sha(open(p, 'rb').read()) != want:
            raise SystemExit('%s: sha256 is not the expected one' % p)
    checks['DT01 pinned: DT01.syx %s..., its main OS %s..., its DSP section 7 %s..., its manifest for that main '
           'OS' % (DT01_SYX_SHA[:8], dj['main_sha256'][:8], dj['section7']['sha256'][:8])] = (
        dj['syx']['sha256'] == DT01_SYX_SHA and dman['main_sha256'] == dj['main_sha256']
        and all(dj['checks'].values()))

    # ---- lfo-dejavu: mod.json from the stock image, the SDK build
    T.run([sys.executable, os.path.join(ROOT, 'scripts', 'dj01_make_mod.py')])
    F1.sdk_build(MODDIR, mods_dir, env)
    dv_file = os.path.join(mods_dir, 'lfo-dejavu-0.1.elemod')
    where = [os.path.join(ROOT, 'out', d, 'mods') for d in ('FL02', 'FL01', 'OB01')] + [
        os.path.join(ROOT, 'out', 'CMP4', 'main', 'mods')]
    files, ids = [], []
    for m in dman['mods']:
        hit = [os.path.join(d, m['file']) for d in where if os.path.exists(os.path.join(d, m['file']))
               and sha(open(os.path.join(d, m['file']), 'rb').read()) == m['sha256']]
        checks['DT01\'s %s %s: its .elemod (sha256 %s...) %s' % (m['id'], m['version'], m['sha256'][:8],
                                                                os.path.relpath(hit[0], ROOT) if hit else 'NOT FOUND')] = bool(hit)
        if not hit:
            raise SystemExit('%s: no file with DT01\'s sha256' % m['file'])
        files.append(hit[0])
        ids.append(m['id'])
    files.append(dv_file)
    ids.append('lfo-dejavu')
    T.run([ELPY, '-m', 'elekloader.lint'] + files + ['--stock', STOCK], cwd=EL, env=env)
    mainsyx = os.path.join(build_dir, a.name + '-main.syx')
    cmd = [ELPY, '-m', 'elekloader.patch', '--stock', STOCK]
    for f in files:
        cmd += ['--mod', f]
    T.run(cmd + ['--version', a.name, '--out', mainsyx], cwd=EL, env=env)
    man = json.load(open(mainsyx + '.json'))
    mp = json.load(open(mainsyx + '.map.json'))

    # ---- the container: DT01's DSP section
    syx = os.path.join(out, a.name + '.syx')
    tmp = syx + '.tmp'
    T.run([TOOL, '-i', mainsyx, '-c', '7', DT01_SEC7, '-o', tmp])
    try:
        ds, _ = T.sections(DT01_SYX)
        fn, _ = T.sections(tmp)
        mn, _ = T.sections(mainsyx)
        st, _ = T.sections(STOCK)
        hdr, h2 = T.header(tmp), T.header(DT01_SYX)
        checks['ELE3 header: version field %s, the rest as DT01\'s' % a.name] = (
            hdr[0x14:0x18] == a.name.encode() and hdr[:0x14] == h2[:0x14] and hdr[0x18:] == h2[0x18:])
        checks['section ids as DT01\'s'] = sorted(fn) == sorted(ds)
        for i in sorted(ds):
            if i != 3:
                checks['section %d stored byte for byte as in DT01.syx%s' % (i, ' (and stock)' if i != 7 and
                                                                            fn[i] == st[i] else '')] = fn[i] == ds[i]
        checks['section 7 depacks to DT01\'s section7.bin (%s...): the DSP is DT01\'s byte for byte' % (
            dj['section7']['sha256'][:8])] = T.depack7(tmp) == open(DT01_SEC7, 'rb').read()
        checks['section 3 stored as elekloader packed it'] = fn[3] == mn[3]
        img = T.main_image(tmp)
        checks['the main OS depacks to the patcher\'s image (%s...)' % man['main_sha256'][:8]] = (
            sha(img) == man['main_sha256'])
        mids = [(m['id'], m['version']) for m in man['mods']]
        checks['mods: DT01\'s eight in DT01\'s order, then lfo-dejavu 0.1 (%s)' % ', '.join('%s %s' % x for x in mids)] = (
            [m['id'] for m in man['mods']] == ids and mids[-1] == ('lfo-dejavu', '0.1')
            and [(m['id'], m['version'], m['sha256']) for m in man['mods'][:-1]]
            == [(m['id'], m['version'], m['sha256']) for m in dman['mods']]
            and man['mods'][-1]['sha256'] == sha(open(dv_file, 'rb').read()))

        dimg = open(DT01_MAIN, 'rb').read()
        stock_img = open(T.STOCK_MAIN, 'rb').read()
        el_d = {m['id']: OB.load_elemod(f) for m, f in zip(dman['mods'], files)}
        el_n = {m['id']: OB.load_elemod(f) for m, f in zip(man['mods'], files)}
        checks['DT01\'s main OS = its eight .elemod files relinked from its manifest\'s layout and map, byte for '
               'byte'] = OB.reconstruct(stock_img, dman, dmp, el_d) == dimg
        checks['%s\'s main OS = its nine .elemod files relinked from its layout and map, byte for byte' % a.name] = (
            OB.reconstruct(stock_img, man, mp, el_n) == img)

        # every relocated field of the eight unchanged mods: the same place in both links
        lo_, ln_ = F1.Link(dman, dmp, el_d), F1.Link(man, mp, el_n)
        fo, fnw = lo_.fields(), ln_.fields()
        same = [k for k in fo]
        moved = [k for k in same if k in fnw and fo[k][0] != fnw[k][0]]
        bad = [k for k in same if k not in fnw or fo[k][1] != fnw[k][1]]
        checks['every relocated field of DT01\'s eight mods names the same place in both links (%d fields, %d moved '
               'with the layout)' % (len(same), len(moved))] = (
            not bad and set(same) == {k for k in fnw if k[0] != 'lfo-dejavu'})

        # lfo-dejavu's sites
        dv = el_n['lfo-dejavu']
        lk = T.Linked(img, mp)
        lkd = T.Linked(dimg, dmp)
        sym = {k: int(v, 16) for k, v in mp.items()}
        sites = {int(s_['addr'], 16): s_ for s_ in dv['sites']}

        def at(addr, n):
            return img[addr - LOAD:addr - LOAD + n]
        want = {}
        for r in WAVE_ROWS:
            want[DESC + r * ROW + 0x0c] = bytes.fromhex('00000f00')
            want[DESC + r * ROW + 0x34] = struct.pack('>I', sym['dv_fmt'])
        want[0x400272d4] = bytes.fromhex('4eb9') + struct.pack('>I', sym['dv_frame_hook'])
        want[0x40137b60] = bytes.fromhex('7e0f')
        want[0x40137b66] = bytes.fromhex('760f')
        want[0x40137b88] = bytes.fromhex('4eb9') + struct.pack('>I', sym['dv_mode_a'])
        want[0x40137cda] = bytes.fromhex('41f9') + struct.pack('>I', sym['dv_wtab_a'])
        want[0x40137d98] = bytes.fromhex('4ef9') + struct.pack('>I', sym['dv_add_a'])
        want[0x40137770] = bytes.fromhex('4eb9') + struct.pack('>I', sym['dv_mode_b']) + bytes.fromhex('4e71')
        want[0x40137788] = bytes.fromhex('7c0f')
        want[0x4013778e] = bytes.fromhex('760f')
        want[0x401378ce] = bytes.fromhex('43f9') + struct.pack('>I', sym['dv_wtab_b'])
        got = {x: at(x, len(w)) for x, w in want.items()}
        checks['lfo-dejavu: its 16 sites written as dv.s documents them (%s)' % ', '.join(
            '0x%08x %s' % (x, got[x].hex()) for x in sorted(want))] = (
            set(sites) == set(want) and all(got[x] == w for x, w in want.items())
            and all(sites[x]['len'] == len(w) for x, w in want.items()))
        tabs = {}
        for t in ('dv_wtab_a', 'dv_wtab_b'):
            tabs[t] = [lk.u32(sym[t] + 4 * i) for i in range(16)]
        stock_tab = [T.rd32(stock_img, WTAB + 4 * i) for i in range(6)]
        checks['lfo-dejavu: dv_wtab_a and dv_wtab_b = the stock table\'s six (%s), 0, then nine times their own '
               'waveform stub' % ' '.join('%08x' % x for x in stock_tab)] = (
            all(tabs[t][:6] == stock_tab and tabs[t][6] == 0 and len(set(tabs[t][7:])) == 1
                and lk.run_start <= tabs[t][7] < lk.run_end for t in tabs) and tabs['dv_wtab_a'][7] != tabs['dv_wtab_b'][7])

        # the stock part: every differing byte in one of lfo-dejavu's sites, or in a relocated field of another mod's
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
        outside = [(x, n) for x, n in rng if not all(any(sa <= y < sa + sn for sa, sn in spans) or
                                                     any(ra <= y < ra + rn for ra, rn, _ in relo)
                                                     for y in range(x, x + n))]
        ours = [r for r in rng if any(sa <= r[0] < sa + sn for sa, sn in spans)]
        theirs = [r for r in rng if r not in ours]
        checks['stock part: %d ranges (%d bytes) differ from DT01: %d inside lfo-dejavu\'s sites (%s); %d are relocated '
               'operands of other mods\' sites whose target moved with the layout (%s)' % (
                   len(rng), sum(n for _, n in rng), len(ours), ', '.join('0x%08x+%d' % r for r in ours), len(theirs),
                   ', '.join('0x%08x+%d %s' % (r[0], r[1], [m_ for ra, rn, m_ in relo if ra <= r[0] < ra + rn][0])
                             for r in theirs if any(ra <= r[0] < ra + rn for ra, rn, _ in relo)))] = not outside

        # the descriptor table and the grown table: DT01's but the WAVE rows' maximum and value text
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
        want_diff = sorted((r, k_) for r in WAVE_ROWS for k_ in ('max', 'fmt'))
        checks['the descriptor table: DT01\'s (names as strings, formatters by name) but the WAVE rows\' maximum '
               'and value text (%s)' % '; '.join('0x%x %s %s -> %s' % d for d in tab_diff)] = (
            sorted((r, k_) for r, k_, _, _ in tab_diff) == want_diff
            and all((k_ == 'max' and y == 6 << 8 and x == 15 << 8) or (k_ == 'fmt' and y == FMT_WAVE
                                                                       and 'dv_fmt' in x)
                    for _, k_, y, x in tab_diff))
        table, rows = T.simulate_boot(lk, stock_img)
        table_o, rows_o = T.simulate_boot(lkd, stock_img)
        grown_diff = sorted((r, k_) for r in range(ROWS) for k_, v in named(lk, table[r * ROW:(r + 1) * ROW]).items()
                            if v != named(lkd, table_o[r * ROW:(r + 1) * ROW])[k_])
        checks['the grown table pg_boot builds (simulated, %d rows): DT01\'s, field by field, but the WAVE rows\' '
               'maximum and value text; the added rows the same' % ROWS] = (
            grown_diff == want_diff and table[:ROWS0 * ROW] == img[lo:hi]
            and [(r['id'], named(lk, r['desc'])) for r in rows] == [(r['id'], named(lkd, r['desc'])) for r in rows_o])

        # the layout
        lay_o, lay_n = dman['link']['layout'], man['link']['layout']
        sec_moved = sorted('%s %+d' % (k, lay_n['sections'][k] - v) for k, v in lay_o['sections'].items()
                           if lay_n['sections'].get(k) != v)
        new_secs = sorted(set(lay_n['sections']) - set(lay_o['sections']))
        checks['layout: DT01\'s sections and lfo-dejavu\'s %s; the blob %d -> %d B; moved: %s' % (
            new_secs, lay_o['blob_len'], lay_n['blob_len'], ', '.join(sec_moved) or 'none')] = (
            set(lay_o['sections']) <= set(lay_n['sections']) and all(k.startswith('lfo-dejavu ') for k in new_secs)
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
    blob_rng = T.diff_ranges(dimg[STOCK_END - LOAD:], img[STOCK_END - LOAD:])
    info = {'name': a.name, 'syx': {'path': os.path.relpath(syx, ROOT), 'sha256': s, 'bytes': os.path.getsize(syx)},
            'main_sha256': sha(img), 'section7': {'path': os.path.relpath(DT01_SEC7, ROOT),
                                                  'sha256': dj['section7']['sha256']},
            'base': 'DT01 ' + DT01_SYX_SHA, 'mods': man['mods'],
            'main_vs_dt01': {'stock_part_ranges': ['0x%08x +%d' % r for r in rng],
                             'stock_part_ours': ['0x%08x +%d' % r for r in ours],
                             'stock_part_moved_operands': ['0x%08x +%d' % r for r in theirs],
                             'blob': {'len': [lay_o['blob_len'], lay_n['blob_len']],
                                      'ranges_differing': len(blob_rng),
                                      'note': 'the appended blob: lfo-dejavu\'s .run added after param-rows\' (ids '
                                              'sort the link), the tables and .bss after it moved; every field of '
                                              'the eight mods names the same place (checked)'},
                             'moved_fields': len(moved), 'sections_moved': sec_moved, 'new_sections': new_secs,
                             'descriptor_table_diff': [['0x%x' % r, k_, str(y), str(x)] for r, k_, y, x in tab_diff]},
            'symbols': {k: mp[k] for k in sorted(mp) if k.startswith('dv_')},
            'checks': checks, 'log': log}
    json.dump(info, open(os.path.join(out, 'build-%s.json' % a.name), 'w'), indent=1)
    print('main OS vs DT01, stock part: ' + ', '.join('0x%08x +%d' % r for r in rng))
    print('%s  %s' % (s, os.path.relpath(syx, ROOT)))


if __name__ == '__main__':
    main()
