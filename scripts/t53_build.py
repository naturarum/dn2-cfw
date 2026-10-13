#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""T5.3: build SE01's main OS: GT01 with fx-echo in place of fx-rename (the Space Echo's page).

    venv/bin/python scripts/t53_build.py                       the main OS, and SE1M.syx for the emulator
    venv/bin/python scripts/t53_build.py --dsp SEC7.bin --name SE01 [--out DIR]
                                                               the same main OS with that DSP section
                                                               (writes NAME.syx, NAME.main.bin, build-NAME.json)

  SE01 main OS = core-dn2 0.1 + fx-echo-dn2 0.1 + param-grow-dn2 0.1 + param-rows-dn2 0.1
  (GT01 = core + fx-rename + param-grow + param-rows: fx-echo supersedes fx-rename, whose sites it
  repeats for the flanger and changes for the delay; the two conflict).

Default output, out/T5.3/SE01-main/: main.bin (the --intended-main image), build.json, the patcher's
main-only .syx (stock DSP) under build/, and SE1M.syx: the same main OS with GT01's DSP section
(FTD1's flanger and tape delay) so that the emulator boots it exactly as it booted GT01. SE1M is for
the emulator only: its DSP is not the Space Echo. With --dsp/--name, the final SE01 container is
written instead (the DSP side supplies section7.bin); the main OS is the same bytes either way.

Steps (nothing here talks to hardware), as scripts/t65_build.py does for GT01:
  1. scripts/t53_make_mod.py writes fx-echo-dn2's mod.json from the stock image (its own checks);
  2. elekloader's SDK builds the four mods (ELEKLOADER_CROSS=m68k-elf-), lint passes for the set,
     the patcher links them into stock 1.12's main OS; a second link with fx-rename added must be
     refused (conflict);
  3. elektron-firmware-tool puts the DSP section in section 7;
  4. checks (all must hold, or nothing is written as final):
     - the container: ELE3 header as stock but the version; sections 2, 4, 5, 8 stored as stock;
       section 3 as elekloader packed it; section 7 as given, depacking to it; checksums and seal;
     - core, param-grow and param-rows are GT01's (same .elemod sha256);
     - against stock, below the appended blob: every changed byte is inside a core, param-grow or
       fx-echo site; against GT01: inside an fx-echo site;
     - every fx-echo site holds what the plan says: a pointer to the expected string or function in
       the linked RAM image, the planned bytes, the `pea` opcode kept, the jsr to fxe_graphid;
     - the descriptor table in the image: GT01's except the fields fx-echo names (and fx-rename's
       tape names, now Echo); the rows of the delay page as the layout says (names, ranges,
       formatters; k, CC, NRPN, default, bipolar and Overbridge id as stock);
     - the grown table pg_boot will build (simulated from the image): the image's 0x141 rows,
       row-0 copies, GT01's 17 compressor rows, unchanged;
     - fxe_tick, fxe_fmt_mode and fxe_graphid are where the map says, in the RAM image.
"""
import argparse
import hashlib
import json
import os
import re
import struct
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scripts'))
import t65_build as T  # noqa: E402

EL, ELPY, TOOL, STOCK, STOCK_SHA = T.EL, T.ELPY, T.TOOL, T.STOCK, T.STOCK_SHA
STOCK_MAIN, LOAD, STOCK_END = T.STOCK_MAIN, T.LOAD, T.STOCK_END
DESC, ROW, ROWS0, ROWS = T.DESC, T.ROW, T.ROWS0, T.ROWS
GT01_MAIN = os.path.join(ROOT, 'out', 'T6.5', 'GT01.main.bin')
GT01_MAIN_SHA = 'b68f81fca886420358ef18d4bb5b75a7e85cc8b06138c4845195588c338534e2'
GT01_MAP = os.path.join(ROOT, 'out', 'T6.5', 'build', 'GT01-main.syx.map.json')
GT01_SYX = os.path.join(ROOT, 'out', 'T6.5', 'GT01.syx')
GT01_MODS = {'core': '63a2ea9074393fda8c97fbddb4fddb07adff7a58621133d2670364e1d7f378c9',
             'param-grow': '5c0383754b8829ce354c46fdf1eef46ba11ac4de9afca731580f6f1d72c5170d',
             'param-rows': 'd93b9f885ab9716a5c56cd139c926572d7ffad676545ccfdde6311311b75c253'}
SEC7_GT01, SEC7_GT01_SHA = T.SEC7, T.SEC7_SHA
MODDIRS = {'core': 'mods/core-dn2', 'fx-echo': 'examples/fx-echo-dn2', 'param-grow': 'examples/param-grow-dn2',
           'param-rows': 'examples/param-rows-dn2', 'fx-rename': 'examples/fx-rename-dn2'}
SET = ['core', 'fx-echo', 'param-grow', 'param-rows']
CORE_SITES = (0x40000538, 0x4002e464, 0x4002e4ca, 0x4002f20c, 0x4002f256, 0x4009ff88, 0x400987a2,
              0x400da0b2, 0x400d9230, 0x4012b490)

# what each fx-echo symbol a site points at must hold (the strings), or be (code)
TEXT = {
    'fxe_flanger': 'Flanger', 'fxe_flg': 'FLG', 'fxe_flg_send': 'Flanger Send', 'fxe_flg_mix': 'Flanger Mix Vol.',
    'fxe_flg_in': 'In Flanger Send', 'fxe_flg_in_l': 'In Flanger Send L', 'fxe_flg_in_r': 'In Flanger Send R',
    'fxe_flg_l': 'LFLG', 'fxe_flg_r': 'RFLG',
    'fxe_echo': 'Echo', 'fxe_ech': 'ECH', 'fxe_echo_s': 'ECHO', 'fxe_echo_send': 'Echo Send',
    'fxe_echo_mix': 'Echo Mix Vol.', 'fxe_echo_in': 'In Echo Send', 'fxe_echo_in_l': 'In Echo Send L',
    'fxe_echo_in_r': 'In Echo Send R', 'fxe_echo_l': 'LECH', 'fxe_echo_r': 'RECH',
    'fxe_time': 'Echo Time', 'fxe_mode': 'Echo Mode', 'fxe_mode_s': 'MODE', 'fxe_wid': 'Width/Pan',
    'fxe_int': 'Intensity', 'fxe_cntr': 'Band Center', 'fxe_cntr_s': 'CNTR', 'fxe_bw': 'Bandwidth',
    'fxe_bw_s': 'BW', 'fxe_age': 'Echo Age', 'fxe_age_s': 'AGE',
}
CODE = ('fxe_fmt_mode', 'fxe_graphid')
# the delay page as the layout says: row -> (long, short, k, min, max, default, bipolar, cc, nrpn, uid, formatter)
FMT_NUMBER, FMT_BIPOLAR64 = 0x400e3030, 0x400e3254
PAGE = {
    0x71: ('Echo Time', 'TIME', 32, 0, 127, 23, 1, 21, 256, 119, 0x400e3e7a),
    0x72: ('Echo Mode', 'MODE', 33, 0, 6, 0, 0, 22, 257, 120, 'fxe_fmt_mode'),
    0x73: ('Width/Pan', 'WID', 34, 0, 127, 64, 0, 23, 258, 121, FMT_BIPOLAR64),
    0x74: ('Intensity', 'FDBK', 35, 0, 127, 32, 1, 24, 259, 122, 0x400e37ae),
    0x75: ('Band Center', 'CNTR', 36, 0, 127, 32, 0, 25, 260, 123, FMT_NUMBER),
    0x76: ('Bandwidth', 'BW', 37, 0, 127, 96, 0, 26, 261, 124, FMT_NUMBER),
    0x77: ('Reverb Send', 'REV', 38, 0, 127, 0, 0, 27, 262, 125, FMT_NUMBER),
    0x79: ('Mix Volume', 'VOL', 39, 0, 127, 110, 0, 28, 263, 127, FMT_NUMBER),
    0x7a: ('Echo Age', 'AGE', 40, 0, 127, 1, 0, -1, -1, 128, FMT_NUMBER),
}
log = T.log


def sha(b):
    return hashlib.sha256(b).hexdigest()


def run(cmd, cwd=None, env=None, ok=(0,)):
    log.append('$ ' + ' '.join(os.path.relpath(c, ROOT) if c.startswith(ROOT) else c for c in cmd))
    r = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True)
    out = (r.stdout + r.stderr).strip()
    if out:
        log.append(out)
    if r.returncode not in ok:
        print('\n'.join(log[-20:]))
        raise SystemExit('failed (%d): %s' % (r.returncode, ' '.join(cmd)))
    return r.returncode, out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--dsp', help='section7.bin for the final container (default: GT01\'s, for the emulator)')
    ap.add_argument('--name', default='SE1M', help='the ELE3 version field (4 characters)')
    ap.add_argument('--out', default=os.path.join(ROOT, 'out', 'T5.3', 'SE01-main'))
    a = ap.parse_args()
    if len(a.name) != 4:
        raise SystemExit('--name: 4 characters')
    sec7 = os.path.abspath(a.dsp) if a.dsp else SEC7_GT01
    for p, want in ((STOCK, STOCK_SHA), (GT01_MAIN, GT01_MAIN_SHA)) + (((SEC7_GT01, SEC7_GT01_SHA),) if not a.dsp else ()):
        if sha(open(p, 'rb').read()) != want:
            raise SystemExit('%s: sha256 is not the expected one' % p)
    out = os.path.abspath(a.out)       # elekloader runs in its own directory
    mods_dir, build_dir = os.path.join(out, 'mods'), os.path.join(out, 'build')
    os.makedirs(mods_dir, exist_ok=True)
    os.makedirs(build_dir, exist_ok=True)
    env = dict(os.environ, ELEKLOADER_CROSS='m68k-elf-')
    run([sys.executable, os.path.join(ROOT, 'scripts', 't53_make_mod.py')])
    built = {}
    for m in SET + ['fx-rename']:
        _, o = run([ELPY, '-m', 'elekloader.sdk.build', MODDIRS[m], '--stock', STOCK, '--out', mods_dir], cwd=EL, env=env)
        built[m] = os.path.normpath(os.path.join(EL, re.search(r'BUILT (\S+\.elemod)', o).group(1)))
    files = [built[m] for m in SET]
    run([ELPY, '-m', 'elekloader.lint'] + files + ['--stock', STOCK], cwd=EL, env=env)
    mainsyx = os.path.join(build_dir, 'SE01-main.syx')
    cmd = [ELPY, '-m', 'elekloader.patch', '--stock', STOCK]
    for f in files:
        cmd += ['--mod', f]
    run(cmd + ['--version', a.name, '--out', mainsyx], cwd=EL, env=env)
    # fx-echo and fx-rename together must be refused
    tmp = os.path.join(build_dir, 'conflict-test.syx')
    rc, refusal = run(cmd + ['--mod', built['fx-rename'], '--version', 'XXXX', '--out', tmp], cwd=EL, env=env, ok=(0, 1, 2))
    if os.path.exists(tmp):
        os.remove(tmp)
    man = json.load(open(mainsyx + '.json'))
    mp = json.load(open(mainsyx + '.map.json'))
    syx = os.path.join(out, a.name + '.syx')
    run([TOOL, '-i', mainsyx, '-c', '7', sec7, '-o', syx])

    st, _ = T.sections(STOCK)
    mn, _ = T.sections(mainsyx)
    fn, rep = T.sections(syx)
    hdr, shdr = T.header(syx), T.header(STOCK)
    checks = {}
    checks['fx-echo with fx-rename is refused (conflict): %s' % refusal.splitlines()[-1][:90]] = (
        rc != 0 and 'fx-rename' in refusal)
    checks['ELE3 header: version field %s, the rest as stock' % a.name] = (
        hdr[0x14:0x18] == a.name.encode() and hdr[:0x14] == shdr[:0x14] and hdr[0x18:] == shdr[0x18:])
    checks['section ids as stock'] = sorted(fn) == sorted(st)
    for i in (2, 4, 5, 8):
        checks['section %d stored as stock' % i] = fn[i] == st[i]
    checks['section 3 stored as elekloader packed it'] = fn[3] == mn[3]
    checks['section 7 depacks to %s' % os.path.relpath(sec7, ROOT)] = T.depack7(syx) == open(sec7, 'rb').read()
    if not a.dsp:
        gt, _ = T.sections(GT01_SYX)
        checks['section 7 stored as in GT01.syx (FTD1\'s)'] = fn[7] == gt[7]
    img = T.main_image(syx)
    checks['main OS depacks to the patcher\'s image'] = sha(img) == man['main_sha256']
    for m in man['mods']:
        if m['id'] in GT01_MODS:
            checks['%s .elemod is GT01\'s (sha256 %s...)' % (m['id'], GT01_MODS[m['id']][:8])] = \
                m['sha256'] == GT01_MODS[m['id']]

    stock_img = open(STOCK_MAIN, 'rb').read()
    gt01 = open(GT01_MAIN, 'rb').read()
    lk = T.Linked(img, mp)
    lk_gt = T.Linked(gt01, json.load(open(GT01_MAP)))
    doc = json.load(open(os.path.join(EL, MODDIRS['fx-echo'], 'mod.json')))
    grow_sites = json.load(open(os.path.join(EL, MODDIRS['param-grow'], 'mod.json')))['sites']
    ren_sites = json.load(open(os.path.join(EL, MODDIRS['fx-rename'], 'mod.json')))['sites']
    span = lambda s: (int(s['addr'], 16), len(s['stock']) // 2)        # noqa: E731
    echo_spans = [span(s) for s in doc['sites']]
    other_spans = [span(s) for s in grow_sites] + [(c, 6) for c in CORE_SITES]
    inside = lambda x, spans: any(lo <= x < lo + n for lo, n in spans)  # noqa: E731
    stray_stock, stray_gt = [], []
    for lo_, n in T.diff_ranges(stock_img[:STOCK_END - LOAD], img[:STOCK_END - LOAD]):
        if not all(inside(x, echo_spans + other_spans) for x in range(lo_, lo_ + n)):
            stray_stock.append('0x%08x +%d' % (lo_, n))
    # param-grow's code lies in the RAM image, whose layout follows the mods' ids (fx-echo comes where
    # fx-rename came, with more bytes), so its three jsr/jmp operands move: they must name the same symbol
    mp_gt = json.load(open(GT01_MAP))
    sym_at = lambda m_, v: sorted(k for k, x in m_.items() if int(x, 16) == v and not k.startswith('__'))  # noqa: E731
    grow_bad, grow_moved = [], []
    for s in grow_sites:
        a_, n = span(s)
        x, y = img[a_ - LOAD:a_ - LOAD + n], gt01[a_ - LOAD:a_ - LOAD + n]
        if x == y:
            continue
        if s['op'] in ('jsr', 'jmp') and x[:2] == y[:2] and x[6:] == y[6:]:
            sx = sym_at(mp, struct.unpack('>I', x[2:6])[0])
            sy_ = sym_at(mp_gt, struct.unpack('>I', y[2:6])[0])
            if s['target'] in sx and s['target'] in sy_:
                grow_moved.append('0x%08x %s %s: 0x%s -> 0x%s' % (a_, s['op'], s['target'], y[2:6].hex(), x[2:6].hex()))
                continue
        grow_bad.append('0x%08x' % a_)
    moved_spans = [span(s) for s in grow_sites if any(m_.startswith(s['addr']) for m_ in grow_moved)]
    for lo_, n in T.diff_ranges(gt01[:STOCK_END - LOAD], img[:STOCK_END - LOAD]):
        if not all(inside(x, echo_spans + moved_spans) for x in range(lo_, lo_ + n)):
            stray_gt.append('0x%08x +%d' % (lo_, n))
    checks['below the blob, every byte that differs from stock is in a core, param-grow or fx-echo site'] = not stray_stock
    checks['below the blob, every byte that differs from GT01 is in an fx-echo site, or a param-grow '
           'jsr/jmp to the same symbol moved with the link layout (%d)' % len(grow_moved)] = not stray_gt
    checks['param-grow\'s sites hold GT01\'s bytes (jsr/jmp: the same targets)'] = not grow_bad
    checks['core\'s ten sites as in GT01'] = all(img[c - LOAD:c - LOAD + 6] == gt01[c - LOAD:c - LOAD + 6] for c in CORE_SITES)

    # every fx-echo site
    mism, sites_seen = [], []
    for s in doc['sites']:
        a_, n = span(s)
        new = img[a_ - LOAD:a_ - LOAD + n]
        if stock_img[a_ - LOAD:a_ - LOAD + n].hex() != s['stock']:
            mism.append('0x%08x: stock bytes differ' % a_)
        if s['op'] == 'bytes':
            if new.hex() != s['new']:
                mism.append('0x%08x: %s, expected %s' % (a_, new.hex(), s['new']))
            sites_seen.append('0x%08x bytes %s' % (a_, s['new']))
            continue
        if s['op'] == 'jsr':
            ok = new[:2] == b'\x4e\xb9' and struct.unpack('>I', new[2:6])[0] == int(mp[s['target']], 16)
            if not ok:
                mism.append('0x%08x: not jsr %s' % (a_, s['target']))
            sites_seen.append('0x%08x jsr %s' % (a_, s['target']))
            continue
        opnd = new[2:6] if s['op'] == 'keep2' else new
        if s['op'] == 'keep2' and new[:2] != b'\x48\x79':
            mism.append('0x%08x: the pea opcode is not kept' % a_)
        p = struct.unpack('>I', opnd)[0]
        if p != int(mp[s['target']], 16):
            mism.append('0x%08x: points at 0x%08x, not %s' % (a_, p, s['target']))
        if s['target'] in TEXT:
            got = lk.string(p)
            if got != TEXT[s['target']]:
                mism.append('0x%08x: "%s", expected "%s"' % (a_, got, TEXT[s['target']]))
            sites_seen.append('0x%08x %s -> "%s"' % (a_, s['op'], got))
        elif s['target'] in CODE:
            sites_seen.append('0x%08x %s -> %s 0x%08x' % (a_, s['op'], s['target'], p))
        else:
            mism.append('0x%08x: target %s not checked' % (a_, s['target']))
    checks['every fx-echo site holds the planned bytes (%d sites)' % len(doc['sites'])] = not mism
    # fx-rename's flanger sites: the same strings in GT01 and here
    flg_same = []
    for s in ren_sites:
        a_, n = span(s)
        off = 2 if s['op'] == 'keep2' else 0
        x = lk.string(struct.unpack('>I', img[a_ - LOAD + off:a_ - LOAD + off + 4])[0])
        y = lk_gt.string(struct.unpack('>I', gt01[a_ - LOAD + off:a_ - LOAD + off + 4])[0])
        flg_same.append((s['addr'], y, x))
    flanger = [f for f in flg_same if 'Fla' in f[1] or 'FLG' in f[1]]
    tape = [f for f in flg_same if f not in flanger]
    checks['fx-rename\'s 13 flanger sites name the same strings as in GT01'] = (
        len(flanger) == 13 and all(y == x for _, y, x in flanger))
    checks['fx-rename\'s 16 tape sites now say Echo (Tape->Echo, TAP->ECH, LTAP->LECH, RTAP->RECH)'] = (
        len(tape) == 16 and all(x == y.replace('Tape', 'Echo').replace('TAPE', 'ECHO').replace('TAP', 'ECH')
                                for _, y, x in tape))

    # the descriptor table: GT01's but the fields fx-echo names; the delay page's rows as planned
    lo, hi = DESC - LOAD, DESC - LOAD + ROWS0 * ROW
    echo_fields = {a_ for a_, n in echo_spans if DESC <= a_ < DESC + ROWS0 * ROW}
    tab_bad = []
    for o in range(0, ROWS0 * ROW, 4):
        x, y = img[lo + o:lo + o + 4], gt01[lo + o:lo + o + 4]
        if x == y or DESC + o in echo_fields:
            continue
        if o % ROW in (0x28, 0x2c, 0x30, 0x38) and lk.string(struct.unpack('>I', x)[0]) == \
                lk_gt.string(struct.unpack('>I', y)[0]):
            continue
        tab_bad.append('row 0x%x +0x%x' % (o // ROW, o % ROW))
    checks['the descriptor table in the image is GT01\'s but the fields fx-echo names'] = not tab_bad
    page_bad, page_rows = [], {}
    for r, (lname, sname, k, mn_, mx_, df, bip, cc, nrpn, uid, fmt) in PAGE.items():
        f = T.row_fields(img[lo + r * ROW:lo + (r + 1) * ROW])
        want_fmt = int(mp[fmt], 16) if isinstance(fmt, str) else fmt
        got = (lk.string(f['name']), lk.string(f['short']), f['k'], f['min'] >> 8, f['max'] >> 8,
               f['default'] >> 8, f['bipolar'], f['cc'], f['nrpn'], f['uid'], f['fmt'], f['group'], f['cc2'])
        want = (lname, sname, k, mn_, mx_, df, bip, cc, nrpn, uid, want_fmt, 0x12, -1)
        page_rows['0x%x' % r] = {'long': got[0], 'short': got[1], 'k': k, 'range': [got[3], got[4]],
                                 'default': got[5], 'cc': cc, 'nrpn': nrpn, 'uid': uid, 'fmt': '0x%08x' % f['fmt'],
                                 'modmask': '0x%x' % f['modmask']}
        if got != want:
            page_bad.append('row 0x%x: %r, expected %r' % (r, got, want))
        gf = T.row_fields(gt01[lo + r * ROW:lo + (r + 1) * ROW])
        if (f['modmask'], f['group_name'], f['suffix']) != (gf['modmask'], gf['group_name'], gf['suffix']):
            page_bad.append('row 0x%x: modmask/group/suffix differ from GT01' % r)
    checks['the delay page\'s rows as the layout says (names, ranges, formatters; k, CC, NRPN, default, '
           'Overbridge id as stock)'] = not page_bad
    table, rows = T.simulate_boot(lk, stock_img)
    table_gt, rows_gt = T.simulate_boot(lk_gt, stock_img)
    checks['the grown table: the image\'s 0x141 rows first'] = table[:ROWS0 * ROW] == img[lo:hi]
    def named(lk_, d):
        # a descriptor with its pointer fields read as the strings they name (they move with the layout)
        f = T.row_fields(d)
        for k_ in ('name', 'group_name', 'short', 'suffix'):
            f[k_] = lk_.string(f[k_])
        return f
    grown_same = all(named(lk, table[r * ROW:(r + 1) * ROW]) == named(lk_gt, table_gt[r * ROW:(r + 1) * ROW])
                     for r in range(ROWS0, ROWS))
    added_same = [(r['id'], named(lk, r['desc']), r['look'], r['flags_set'], r['opts'], r['page'], r['slot'])
                  for r in rows] == [(r['id'], named(lk_gt, r['desc']), r['look'], r['flags_set'], r['opts'],
                                      r['page'], r['slot']) for r in rows_gt]
    checks['the grown table after the stock rows is GT01\'s, field by field (row-0 copies, the 17 compressor '
           'rows; names compared as strings)'] = grown_same and added_same and len(rows) == 17
    checks['ev_tick has fxe_tick (after core_fastcopy)'] = (
        'fxe_tick' in mp and man['link']['tables']['ev_tick']['entries'] == 2)
    import t53_make_mod
    _, band = t53_make_mod.band_table()
    run_img = img[lk.run_load - LOAD:lk.run_load - LOAD + (lk.run_end - lk.run_start)]
    band_bytes = struct.pack('>128H', *band)
    checks['the band picture\'s table in the RAM image is the DSP law\'s (fx/spaceecho.c: SE_LB0, '
           'SE_LB_RANGE, SE_CTR_OCT), once'] = run_img.count(band_bytes) == 1
    for f in ('fxe_fmt_mode', 'fxe_graphid', 'fxe_band', 'fxe_tick', 'fxe_state', 'fxe_ticks', 'fxe_band_seen'):
        checks['symbol %s in the RAM image' % f] = f in mp and lk.run_start <= int(mp[f], 16) < lk.run_end + 0x100

    os.makedirs(out, exist_ok=True)
    # the final container (--dsp) names its files after it, as T6.5 does: NAME.main.bin, build-NAME.json
    main_name = a.name + '.main.bin' if a.dsp else 'main.bin'
    json_name = 'build-%s.json' % a.name if a.dsp else 'build.json'
    with open(os.path.join(out, main_name), 'wb') as fh:
        fh.write(img)
    sy = open(syx, 'rb').read()
    with open(syx + '.sha256', 'w') as fh:
        fh.write('%s  %s\n' % (sha(sy), os.path.basename(syx)))
    info = {
        'name': 'SE01 main OS (%s container)' % a.name,
        'what': 'GT01 with fx-echo-dn2 0.1 in place of fx-rename-dn2 0.1: core-dn2 0.1 + fx-echo-dn2 0.1 + '
                'param-grow-dn2 0.1 + param-rows-dn2 0.1. The Space Echo\'s delay page on one page.',
        'main_bin': {'path': os.path.relpath(os.path.join(out, main_name), ROOT), 'bytes': len(img), 'sha256': sha(img)},
        'syx': {'path': os.path.relpath(syx, ROOT), 'bytes': len(sy), 'sha256': sha(sy),
                'note': ('FOR THE EMULATOR ONLY: section 7 is GT01\'s (FTD1: flanger + tape delay), not the '
                         'Space Echo' if not a.dsp else 'section 7 from %s' % os.path.relpath(sec7, ROOT))},
        'section7': {'path': os.path.relpath(sec7, ROOT), 'sha256': sha(open(sec7, 'rb').read())},
        'intermediate': {'path': os.path.relpath(mainsyx, ROOT), 'sha256': sha(open(mainsyx, 'rb').read())},
        'mods': [{k: m[k] for k in ('id', 'version', 'file', 'sha256', 'sites')} for m in man['mods']],
        'link': {k: man['link']['layout'][k] for k in ('ddr', 'bss', 'blob_len', 'ddr_spare')},
        'inplace_gaps': man['output']['main'].get('inplace_gaps'),
        'symbols': {k: mp[k] for k in ('fxe_tick', 'fxe_fmt_mode', 'fxe_graphid', 'fxe_band', 'fxe_state',
                                       'fxe_ticks', 'fxe_band_seen',
                                       'pg_boot', 'pg_sinit', 'pg_disp_get', 'pg_desc', 'param_rows',
                                       '__run_start', '__run_load', '__bss_start', '__bss_end') if k in mp},
        'checks': checks,
        'stray_vs_stock': stray_stock, 'stray_vs_gt01': stray_gt, 'site_mismatches': mism,
        'param_grow_jumps_moved': grow_moved, 'param_grow_bad': grow_bad,
        'fx_echo_sites': sites_seen, 'fx_rename_sites_then_now': flg_same,
        'table_problems': tab_bad, 'page_problems': page_bad, 'delay_page_rows': page_rows,
        'diff_vs_gt01': ['0x%08x +%d' % r for r in T.diff_ranges(gt01, img)],
        'n_diff_vs_gt01': len(T.diff_ranges(gt01, img)), 'n_diff_vs_stock': len(T.diff_ranges(stock_img, img)),
        'tool_report': rep.splitlines(), 'log': log,
    }
    with open(os.path.join(out, json_name), 'w') as fh:
        json.dump(info, fh, indent=1)
    print('== SE01 main OS, %s container: %s' % (a.name, info['what']))
    for k, v in checks.items():
        print('  %-100s %s' % (k, 'yes' if v else 'NO'))
    for l in mism + page_bad + tab_bad + ['stray vs stock ' + x for x in stray_stock] + ['stray vs GT01 ' + x for x in stray_gt]:
        print('  PROBLEM: ' + l)
    print('  main OS: %d ranges differ from GT01, %d from stock; in-place gaps %s' % (
        info['n_diff_vs_gt01'], info['n_diff_vs_stock'], info['inplace_gaps']))
    print('  %s  %s (%d bytes)' % (sha(sy), os.path.relpath(syx, ROOT), len(sy)))
    print('  %s  %s' % (sha(img), info['main_bin']['path']))
    if not all(checks.values()):
        raise SystemExit('a check failed')


if __name__ == '__main__':
    main()
