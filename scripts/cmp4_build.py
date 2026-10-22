#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""CMP4: build CMP4's main OS = CMP3's with flanger-names-dn2 0.1 in place of fx-rename-dn2 0.1 and comp-link-dn2 0.3.

    venv/bin/python scripts/cmp4_build.py                     the main OS, and C4EM.syx for the emulator
    venv/bin/python scripts/cmp4_build.py --dsp out/CMP4/CMP4-dsp/section7.bin --name CMP4 --out out/CMP4
                                                              the same main OS with that DSP section (writes
                                                              CMP4.syx, CMP4.main.bin, build-CMP4.json)

  CMP4 main OS = core-dn2 0.2 + comp-ui-dn2 0.2 + flanger-names-dn2 0.1 + param-grow-dn2 0.1 + param-rows-dn2 0.3
                 + comp-link-dn2 0.3
  (CMP3 = the same with fx-rename-dn2 0.1 and comp-link-dn2 0.2, in this order). CMP4's DSP gives the delay slot back to
  the stock delay (tools/dspsplice/build.py recipe CMP4), so every delay name is stock again: flanger-names is
  fx-rename's 13 flanger sites with the same strings (scripts/cmp4_make_mod.py) and none of its 16 tape sites.
  comp-link 0.3 = 0.2 with the CMP3 storage review's two fixes in compl_update (SC01's, notes/SC01-review.md).

Default output, out/CMP4/main/: main.bin (the --intended-main image), build.json, C4MO-main.syx (the patcher's
main-only container, stock DSP, its own version field C4MO), and C4EM.syx: the same main OS with GT01's DSP section
(FTD1's) so that the emulator boots it as it booted GT01, C3EM and SCEM. C4EM is for the emulator only. With
--dsp/--name the final container NAME.syx is written instead; the main OS is the same bytes either way, and the final
mode pins it to out/CMP4/main/main.bin (the image the emulator runs used) and section 3 to C4EM.syx's.

Checks (all must hold, or nothing is written):
  - the container: ELE3 header as stock but the version; sections 2, 4, 5, 8 stored as stock; section 3 as elekloader
    packed it; section 7 as given, depacking to it; the final container's section 7 stored as in the DSP side's own
    container, whose build.json records it (and its layout and placement checks report no problem);
  - the mods: versions as above; core, comp-ui, param-grow and param-rows are CMP3's .elemod files and comp-link SC01's
    (sha256); flanger-names with fx-rename is refused; comp-link 0.3 with param-rows 0.2 (pinned) is refused;
  - against CMP3's main OS (on the unit), below the appended blob: every byte that differs is in an fx-rename site or a
    site of core, param-grow, comp-ui or comp-link whose jsr/jmp/pointer operand moved with the link layout and still
    names the same symbol or string;
  - fx-rename's 13 flanger sites name the strings CMP3's do (through flanger-names' symbols); its 16 tape sites hold
    the stock bytes again;
  - against the stock main OS, below the blob: every byte that differs is in a site of the six mods (or core's code
    sites); the delay's descriptor rows (group 0x12) and the four delay send rows, and the modulation destination
    lists' delay group name, are the stock bytes;
  - comp-ui's five sites and comp-link's four as in CMP3 (the same bytes or the same symbol); compl_vmax/compl_vdef
    equal CMP3's (the rows' maxima and new-kit defaults);
  - the descriptor table: CMP3's but the tape fields, which are stock's;
  - the grown table pg_boot will build (simulated): the image's 0x141 rows, then CMP3's rows field by field (the 20
    band and mix rows; names as strings);
  - the RAM image: comp-ui's page list, core's page-record table and the three band records as in CMP3; ev_tick as
    CMP3's (one entry).
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scripts'))
import t65_build as T  # noqa: E402
import elk_pin  # noqa: E402

EL, ELPY, TOOL, STOCK, STOCK_SHA = T.EL, T.ELPY, T.TOOL, T.STOCK, T.STOCK_SHA
STOCK_MAIN, LOAD, STOCK_END = T.STOCK_MAIN, T.LOAD, T.STOCK_END
DESC, ROW, ROWS0, ROWS = T.DESC, T.ROW, T.ROWS0, T.ROWS
CMP3_MAIN = os.path.join(ROOT, 'out', 'T6.3', 'CMP3.main.bin')
CMP3_MAIN_SHA = '13c562b304b4a3be62ac4463a74310651ad74c2fa676b3922baebcaa5509ef08'
CMP3_MAP = os.path.join(ROOT, 'out', 'T6.3', 'CMP3', 'C3MO-main.syx.map.json')
CMP3_JSON = os.path.join(ROOT, 'out', 'T6.3', 'build-CMP3.json')
SC01_JSON = os.path.join(ROOT, 'out', 'SC01', 'build-SC01.json')
SEC7_GT01, SEC7_GT01_SHA = T.SEC7, T.SEC7_SHA
GT01_SYX = os.path.join(ROOT, 'out', 'T6.5', 'GT01.syx')
MODDIRS = {'core': 'mods/core-dn2', 'comp-ui': 'examples/comp-ui-dn2', 'flanger-names': 'examples/flanger-names-dn2',
           'param-grow': 'examples/param-grow-dn2', 'param-rows': 'examples/param-rows-dn2',
           'comp-link': 'examples/comp-link-dn2', 'fx-rename': 'examples/fx-rename-dn2'}
SET = ['core', 'comp-ui', 'flanger-names', 'param-grow', 'param-rows', 'comp-link']
VERSIONS = {'core': '0.2', 'comp-ui': '0.2', 'flanger-names': '0.1', 'param-grow': '0.1', 'param-rows': '0.3',
            'comp-link': '0.3'}
MAIN_ONLY, EM_NAME = 'C4MO', 'C4EM'
DEFAULT_OUT = os.path.join(ROOT, 'out', 'CMP4', 'main')
LINK_SITE, SER_SITE, LOAD_SITE, SLOT17 = 0x40025e9e, 0x400dcf34, 0x400dcef0, 0x401da4f0
PAGEREC = 0x400c25d8
PAGE = 0x2c
DELAY_GROUP = 0x12
DELAY_SEND_ROWS = (0x44, 0x6d, 0x78, 0x8e, 0x8f, 0x90)     # track FX E, flanger page G, MIXER FX MIX E, the input sends
DEST_DELAY = 0x401f7b04 + 4 * (DELAY_GROUP - 5)           # the destination lists' group-name entry of the delay
TEXT = {'fln_flanger': 'Flanger', 'fln_flg': 'FLG', 'fln_flg_send': 'Flanger Send', 'fln_flg_mix': 'Flanger Mix Vol.',
        'fln_flg_in': 'In Flanger Send', 'fln_flg_in_l': 'In Flanger Send L', 'fln_flg_in_r': 'In Flanger Send R',
        'fln_flg_l': 'LFLG', 'fln_flg_r': 'RFLG'}
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


def sdk_build(moddir, out_dir, env):
    _, o = run([ELPY, '-m', 'elekloader.sdk.build', moddir, '--stock', STOCK, '--out', out_dir], cwd=EL, env=env)
    return os.path.normpath(os.path.join(EL, re.search(r'BUILT (\S+\.elemod)', o).group(1)))


def refused(files, env, build_dir, *words):
    tmp = os.path.join(build_dir, 'refusal-test.syx')
    cmd = [ELPY, '-m', 'elekloader.patch', '--stock', STOCK]
    for f in files:
        cmd += ['--mod', f]
    rc, o = run(cmd + ['--version', 'XXXX', '--out', tmp], cwd=EL, env=env, ok=(0, 1, 2))
    for p in (tmp, tmp + '.json', tmp + '.map.json'):
        if os.path.exists(p):
            os.remove(p)
    last = o.splitlines()[-1] if o else ''
    return rc != 0 and all(w in o for w in words), last


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--dsp', help='section7.bin for the final container (default: GT01\'s, for the emulator)')
    ap.add_argument('--name', help='the ELE3 version field (default C4EM)')
    ap.add_argument('--dsp-json', help='with --dsp: the DSP side\'s build.json (default: beside section7.bin)')
    ap.add_argument('--out')
    a = ap.parse_args()
    a.name = a.name or EM_NAME
    a.out = a.out or DEFAULT_OUT
    if len(a.name) != 4 or a.name == MAIN_ONLY:
        raise SystemExit('--name: 4 characters, not %s' % MAIN_ONLY)
    sec7 = os.path.abspath(a.dsp) if a.dsp else SEC7_GT01
    pins = [(STOCK, STOCK_SHA), (CMP3_MAIN, CMP3_MAIN_SHA)] + ([] if a.dsp else [(SEC7_GT01, SEC7_GT01_SHA)])
    for p, want in pins:
        if sha(open(p, 'rb').read()) != want:
            raise SystemExit('%s: sha256 is not the expected one' % p)
    out = os.path.abspath(a.out)
    mods_dir, build_dir = os.path.join(out, 'mods'), os.path.join(out, 'build')
    os.makedirs(mods_dir, exist_ok=True)
    os.makedirs(build_dir, exist_ok=True)
    syx = os.path.join(out, a.name + '.syx')
    tmp_syx = syx + '.tmp'
    link_dir = tempfile.mkdtemp(prefix='link-%s-' % a.name, dir=build_dir)
    try:
        build(a, sec7, out, mods_dir, build_dir, syx, tmp_syx, link_dir)
    finally:
        shutil.rmtree(link_dir, ignore_errors=True)
        if os.path.exists(tmp_syx):
            os.remove(tmp_syx)


def build(a, sec7, out, mods_dir, build_dir, syx, tmp_syx, link_dir):
    env = dict(os.environ, ELEKLOADER_CROSS='m68k-elf-')
    checks = {}
    # flanger-names' mod.json from the stock image (scripts/cmp4_make_mod.py checks every stock value and that the
    # sites are fx-rename 0.1's flanger sites)
    run([sys.executable, os.path.join(ROOT, 'scripts', 'cmp4_make_mod.py')])
    moddir = lambda m: os.path.join(EL, MODDIRS[m])                          # noqa: E731
    built = {m: sdk_build(moddir(m), mods_dir, env) for m in SET + ['fx-rename']}
    pin2 = elk_pin.tree(elk_pin.REV_CMP2)
    rows02 = sdk_build(os.path.join(pin2, 'examples', 'param-rows-dn2'), os.path.join(mods_dir, 'pinned'), env)
    files = [built[m] for m in SET]
    run([ELPY, '-m', 'elekloader.lint'] + files + ['--stock', STOCK], cwd=EL, env=env)
    mainsyx = os.path.join(build_dir, MAIN_ONLY + '-main.syx')
    cmd = [ELPY, '-m', 'elekloader.patch', '--stock', STOCK]
    for f in files:
        cmd += ['--mod', f]
    run(cmd + ['--version', MAIN_ONLY, '--out', mainsyx], cwd=EL, env=env)
    namesyx = os.path.join(link_dir, a.name + '-main.syx')
    run(cmd + ['--version', a.name, '--out', namesyx], cwd=EL, env=env)
    ok, why = refused(files + [built['fx-rename']], env, build_dir, 'fx-rename')
    checks['flanger-names with fx-rename is refused (conflict): %s' % why[:80]] = ok
    ok, why = refused([rows02 if f == built['param-rows'] else f for f in files], env, build_dir,
                      'comp-link 0.3 requires param-rows 0.3 or later')
    checks['comp-link 0.3 with param-rows 0.2 (pinned) is refused (it requires param-rows>=0.3): %s' % why[:80]] = ok
    man = json.load(open(mainsyx + '.json'))
    mp = json.load(open(mainsyx + '.map.json'))
    run([TOOL, '-i', namesyx, '-c', '7', sec7, '-o', tmp_syx])

    st, _ = T.sections(STOCK)
    mn, _ = T.sections(mainsyx)
    fn, rep = T.sections(tmp_syx)
    hdr, shdr = T.header(tmp_syx), T.header(STOCK)
    checks['ELE3 header: version field %s, the rest as stock' % a.name] = (
        hdr[0x14:0x18] == a.name.encode() and hdr[:0x14] == shdr[:0x14] and hdr[0x18:] == shdr[0x18:])
    checks['section ids as stock'] = sorted(fn) == sorted(st)
    for i in (2, 4, 5, 8):
        checks['section %d stored as stock' % i] = fn[i] == st[i]
    nn, _ = T.sections(namesyx)
    checks['section 3 stored as elekloader packed it'] = fn[3] == nn[3]
    checks['the %s link is the %s link: the same map, the same main OS' % (a.name, MAIN_ONLY)] = (
        json.load(open(namesyx + '.map.json')) == mp
        and json.load(open(namesyx + '.json'))['main_sha256'] == man['main_sha256'])
    checks['section 7 depacks to %s' % os.path.relpath(sec7, ROOT)] = T.depack7(tmp_syx) == open(sec7, 'rb').read()
    dsp_side = None
    if a.dsp:
        dj = os.path.abspath(a.dsp_json) if a.dsp_json else os.path.join(os.path.dirname(sec7), 'build.json')
        bj = json.load(open(dj))
        dsyx = os.path.join(ROOT, bj['syx']['path'])
        sec7_sha = sha(open(sec7, 'rb').read())
        checks['%s records this section 7 (sha256 %s...) and the container %s' % (
            os.path.relpath(dj, ROOT), sec7_sha[:8], bj['syx']['path'])] = (
            bj['section7']['sha256'] == sec7_sha and bj['syx']['sha256'] == sha(open(dsyx, 'rb').read()))
        checks['the DSP side\'s layout check reports no problems'] = not bj['layout_check']['problems']
        checks['the DSP side\'s code all where our code ran on the unit (proven check), the stock delay hooks kept'] = (
            bool(bj.get('proven_check')) and all(r['proven'] for r in bj['proven_check'])
            and len(bj.get('stock_hooks') or []) == 2 and all(h['stock_bytes_kept'] for h in bj['stock_hooks']))
        ds, _ = T.sections(dsyx)
        checks['section 7 stored byte for byte as in %s' % bj['syx']['path']] = fn[7] == ds[7]
        em = os.path.join(DEFAULT_OUT, EM_NAME + '.syx')
        checks['section 3 stored byte for byte as in %s (the container the emulator ran)' % os.path.relpath(em, ROOT)] = (
            fn[3] == T.sections(em)[0][3])
        dsp_side = {'build_json': os.path.relpath(dj, ROOT), 'syx': bj['syx']['path'], 'syx_sha256': bj['syx']['sha256'],
                    'stored_bytes': len(fn[7]), 'em_syx_sha256': sha(open(em, 'rb').read())}
    else:
        gt, _ = T.sections(GT01_SYX)
        checks['section 7 stored as in GT01.syx (FTD1\'s): the emulator container'] = fn[7] == gt[7]
    checks['the main-only container\'s section 7 is stock\'s'] = mn[7] == st[7]
    img = T.main_image(tmp_syx)
    checks['main OS depacks to the patcher\'s image'] = sha(img) == man['main_sha256']
    checks['the main-only container depacks to the same main OS'] = T.main_image(mainsyx) == img
    if a.dsp:
        tested = os.path.join(DEFAULT_OUT, 'main.bin')
        checks['main OS = %s byte for byte, the image the emulator ran' % os.path.relpath(tested, ROOT)] = (
            os.path.exists(tested) and open(tested, 'rb').read() == img)
    cm = {m['id']: m['sha256'] for m in json.load(open(CMP3_JSON))['mods']}
    sm = {m['id']: m['sha256'] for m in json.load(open(SC01_JSON))['mods']}
    for m in man['mods']:
        if m['id'] in ('core', 'comp-ui', 'param-grow', 'param-rows'):
            checks['%s .elemod is CMP3\'s (sha256 %s...)' % (m['id'], cm[m['id']][:8])] = m['sha256'] == cm[m['id']]
        if m['id'] == 'comp-link':
            checks['comp-link .elemod is SC01\'s 0.3 (sha256 %s...)' % sm['comp-link'][:8]] = m['sha256'] == sm['comp-link']
    checks['mod versions %s' % ', '.join('%s %s' % kv for kv in VERSIONS.items())] = (
        {m['id']: m['version'] for m in man['mods']} == VERSIONS)
    checks['mod order as CMP3\'s (flanger-names in fx-rename\'s place)'] = (
        [m['id'] for m in man['mods']] == [m['id'] if m['id'] != 'fx-rename' else 'flanger-names'
                                           for m in json.load(open(CMP3_JSON))['mods']])

    stock_img = open(STOCK_MAIN, 'rb').read()
    c3 = open(CMP3_MAIN, 'rb').read()
    mp_c3 = json.load(open(CMP3_MAP))
    lk = T.Linked(img, mp)
    lk_c3 = T.Linked(c3, mp_c3)
    rd = lambda m: json.load(open(os.path.join(moddir(m), 'mod.json')))        # noqa: E731
    docs = {m: rd(m) for m in SET + ['fx-rename']}
    span = lambda s: (int(s['addr'], 16), len(s['stock']) // 2)               # noqa: E731
    inside = lambda x, spans: any(lo <= x < lo + n for lo, n in spans)         # noqa: E731
    sym_at = lambda m_, v: sorted(k for k, x in m_.items() if int(x, 16) == v and not k.startswith('__'))  # noqa: E731
    rename_spans = [span(s) for s in docs['fx-rename']['sites']]

    # the sites of the mods CMP4 shares with CMP3 whose operand moved with the link layout: the same symbol or string
    moved, moved_bad, moved_spans = [], [], []
    for mid in ('core', 'param-grow', 'comp-ui', 'comp-link'):
        for s in docs[mid]['sites']:
            a_, n = span(s)
            x, y = img[a_ - LOAD:a_ - LOAD + n], c3[a_ - LOAD:a_ - LOAD + n]
            if x == y:
                continue
            if s['op'] in ('jsr', 'jmp') and x[:2] == y[:2] and x[6:] == y[6:]:
                sx = sym_at(mp, struct.unpack('>I', x[2:6])[0])
                sy = sym_at(mp_c3, struct.unpack('>I', y[2:6])[0])
                if s['target'] in sx and s['target'] in sy:
                    moved.append('0x%08x %s %s %s' % (a_, mid, s['op'], s['target']))
                    moved_spans.append((a_, n))
                    continue
            if s['op'] in ('ptr', 'keep2'):
                off = 2 if s['op'] == 'keep2' else 0
                if x[:off] == y[:off]:
                    px, py = struct.unpack('>I', x[off:off + 4])[0], struct.unpack('>I', y[off:off + 4])[0]
                    sx, sy = sym_at(mp, px), sym_at(mp_c3, py)
                    if (s.get('target') in sx and s.get('target') in sy) or (
                            lk.string(px) is not None and lk.string(px) == lk_c3.string(py)):
                        moved.append('0x%08x %s %s %s' % (a_, mid, s['op'], s.get('target')))
                        moved_spans.append((a_, n))
                        continue
            moved_bad.append('0x%08x %s' % (a_, mid))
    # code sites of core with a 6-byte jsr/jmp but no table entry in its mod.json (t63b's CORE_SITES)
    core_sites = (0x40000538, 0x4002e464, 0x4002e4ca, 0x4002f20c, 0x4002f256, 0x4009ff88, 0x400987a2,
                  0x400da0b2, 0x400d9230, 0x4012b490, PAGEREC)
    for c in core_sites:
        x, y = img[c - LOAD:c - LOAD + 6], c3[c - LOAD:c - LOAD + 6]
        if x != y:
            if x[:2] == y[:2] and sym_at(mp, struct.unpack('>I', x[2:6])[0]) == sym_at(mp_c3, struct.unpack('>I', y[2:6])[0]):
                moved.append('0x%08x core %s' % (c, sym_at(mp, struct.unpack('>I', x[2:6])[0])))
                moved_spans.append((c, 6))
            else:
                moved_bad.append('0x%08x core' % c)
    stray = []
    for lo_, n in T.diff_ranges(c3[:STOCK_END - LOAD], img[:STOCK_END - LOAD]):
        if not all(inside(x, rename_spans + moved_spans) for x in range(lo_, lo_ + n)):
            stray.append('0x%08x +%d' % (lo_, n))
    checks['below the blob, every byte that differs from CMP3 (on the unit) is in an fx-rename site, or a site of core, '
           'param-grow, comp-ui or comp-link whose operand moved to the same symbol/string (%d)'
           % len(moved)] = not stray and not moved_bad

    # fx-rename's sites in CMP4: the flanger's through flanger-names (CMP3's strings), the tape's stock again
    then_now, mism = [], []
    fln = {s['addr']: s for s in docs['flanger-names']['sites']}
    for s in docs['fx-rename']['sites']:
        a_, n = span(s)
        new = img[a_ - LOAD:a_ - LOAD + n]
        off = 2 if s['op'] == 'keep2' else 0
        y = lk_c3.string(struct.unpack('>I', c3[a_ - LOAD + off:a_ - LOAD + off + 4])[0])
        if s['addr'] in fln:
            f = fln[s['addr']]
            p = struct.unpack('>I', new[off:off + 4])[0]
            x = lk.string(p)
            if f['op'] != s['op'] or f['stock'] != s['stock'] or p != int(mp[f['target']], 16) or x != y \
                    or x != TEXT[f['target']] or (off and new[:2] != b'\x48\x79'):
                mism.append('%s: flanger site "%s", CMP3 "%s"' % (s['addr'], x, y))
            then_now.append((s['addr'], y, x))
        else:
            x = lk.string(struct.unpack('>I', new[off:off + 4])[0])
            if new.hex() != s['stock'] or new != stock_img[a_ - LOAD:a_ - LOAD + n]:
                mism.append('%s: tape site not stock (%s)' % (s['addr'], new.hex()))
            then_now.append((s['addr'], y, x))
    flanger = [f for f in then_now if f[0] in fln]
    tape = [f for f in then_now if f[0] not in fln]
    checks['fx-rename\'s 13 flanger sites name the strings CMP3\'s do, through flanger-names\' symbols'] = (
        len(flanger) == 13 and len(fln) == 13 and not [m_ for m_ in mism if 'flanger' in m_])
    checks['fx-rename\'s 16 tape sites hold the stock bytes (the delay\'s stock names: Delay, DEL, LDEL, RDEL ...)'] = (
        len(tape) == 16 and not [m_ for m_ in mism if 'tape' in m_])

    # against stock: every differing byte below the blob in one of our mods' sites; the delay's rows stock
    all_spans = [span(s) for m in SET for s in docs[m].get('sites', [])] + [(c, 6) for c in core_sites]
    stray_stock = []
    for lo_, n in T.diff_ranges(stock_img[:STOCK_END - LOAD], img[:STOCK_END - LOAD]):
        if not all(inside(x, all_spans) for x in range(lo_, lo_ + n)):
            stray_stock.append('0x%08x +%d' % (lo_, n))
    checks['below the blob, every byte that differs from stock is in a site of the six mods or a core code site'] = not stray_stock
    lo, hi = DESC - LOAD, DESC - LOAD + ROWS0 * ROW
    delay_rows = [r for r in range(ROWS0) if T.row_fields(stock_img[lo + r * ROW:lo + (r + 1) * ROW])['group'] == DELAY_GROUP]
    rows_bad = [r for r in delay_rows + list(DELAY_SEND_ROWS)
                if img[lo + r * ROW:lo + (r + 1) * ROW] != stock_img[lo + r * ROW:lo + (r + 1) * ROW]]
    checks['the delay\'s %d descriptor rows (group 0x12) and its %d send rows are the stock bytes' % (
        len(delay_rows), len(DELAY_SEND_ROWS))] = not rows_bad and len(delay_rows) > 0
    checks['the destination lists\' delay group name is stock ("%s")' % lk.string(T.rd32(img, DEST_DELAY))] = (
        img[DEST_DELAY - LOAD:DEST_DELAY - LOAD + 4] == stock_img[DEST_DELAY - LOAD:DEST_DELAY - LOAD + 4])

    # comp-ui's and comp-link's sites: CMP3's bytes, or the same symbol; comp-link's tables equal CMP3's
    def same_or_moved(mid):
        bad = []
        for s in docs[mid]['sites']:
            a_, n = span(s)
            x, y = img[a_ - LOAD:a_ - LOAD + n], c3[a_ - LOAD:a_ - LOAD + n]
            if x != y and not any(m_.startswith('0x%08x' % a_) for m_ in moved):
                bad.append('0x%08x' % a_)
        return bad
    ui_bad, link_bad = same_or_moved('comp-ui'), same_or_moved('comp-link')
    checks['comp-ui\'s five sites as in CMP3 (the same bytes or the same symbol)'] = not ui_bad and len(docs['comp-ui']['sites']) == 5
    want_link = {LINK_SITE: '4eb9%08x' % int(mp['compl_tail'], 16), SER_SITE: '4ef9%08x4e71' % int(mp['compl_ser'], 16),
                 LOAD_SITE: '4ef9%08x4e71' % int(mp['compl_load'], 16), SLOT17: '%08x' % int(mp['compl_update'], 16)}
    checks['comp-link 0.3\'s four sites: 0x40025e9e jsr compl_tail, 0x400dcf34 jmp compl_ser, 0x400dcef0 jmp compl_load '
           '(each + nop), 0x401da4f0 -> compl_update'] = not link_bad and all(
        img[x - LOAD:x - LOAD + len(v) // 2].hex() == v for x, v in want_link.items()) and sorted(
        int(s_['addr'], 16) for s_ in docs['comp-link']['sites']) == sorted(want_link)
    checks['comp-link 0.3: compl_vmax / compl_vdef equal CMP3\'s (the rows\' maxima and new-kit defaults)'] = (
        lk.ram(int(mp['compl_vmax'], 16), 27) == lk_c3.ram(int(mp_c3['compl_vmax'], 16), 27)
        and lk.ram(int(mp['compl_vdef'], 16), 27) == lk_c3.ram(int(mp_c3['compl_vdef'], 16), 27))

    # the descriptor table: CMP3's but the tape fields (stock's now); pointer fields compared as strings
    tape_fields = {x for s in docs['fx-rename']['sites'] if s['addr'] not in fln
                   for x in range(span(s)[0], span(s)[0] + span(s)[1])}
    tab_bad = []
    for o in range(0, ROWS0 * ROW, 4):
        x, y = img[lo + o:lo + o + 4], c3[lo + o:lo + o + 4]
        if x == y:
            continue
        if DESC + o in tape_fields:
            if x != stock_img[lo + o:lo + o + 4]:
                tab_bad.append('row 0x%x +0x%x (tape field not stock)' % (o // ROW, o % ROW))
            continue
        if o % ROW in (0x28, 0x2c, 0x30, 0x38) and lk.string(struct.unpack('>I', x)[0]) == \
                lk_c3.string(struct.unpack('>I', y)[0]):
            continue
        tab_bad.append('row 0x%x +0x%x' % (o // ROW, o % ROW))
    checks['the descriptor table in the image is CMP3\'s but the tape fields, which are stock\'s'] = not tab_bad
    table, rows = T.simulate_boot(lk, stock_img)
    table_c3, rows_c3 = T.simulate_boot(lk_c3, stock_img)
    checks['the grown table: the image\'s 0x141 rows first'] = table[:ROWS0 * ROW] == img[lo:hi]

    def named(lk_, d):
        # pointer fields read as what they name: strings, and a formatter in the RAM image as its symbol
        f = T.row_fields(d)
        for k_ in ('name', 'group_name', 'short', 'suffix'):
            f[k_] = lk_.string(f[k_])
        if lk_.run_start <= f['fmt'] < lk_.run_end:
            f['fmt'] = sym_at(lk_.mp, f['fmt']) or f['fmt']
        return f
    grown_same = all(named(lk, table[r * ROW:(r + 1) * ROW]) == named(lk_c3, table_c3[r * ROW:(r + 1) * ROW])
                     for r in range(ROWS0, ROWS))
    added_same = [(r['id'], named(lk, r['desc']), r['look'], r['flags_set'], r['opts'], r['page'], r['slot'])
                  for r in rows] == [(r['id'], named(lk_c3, r['desc']), r['look'], r['flags_set'], r['opts'],
                                      r['page'], r['slot']) for r in rows_c3]
    checks['the grown table after the stock rows is CMP3\'s, field by field (the 20 band and mix rows; names as '
           'strings)'] = grown_same and added_same and len(rows) == 20

    # the RAM image: comp-ui's page list, core's page records, the band records (as in CMP3)
    pages = [lk.s32(int(mp['compui_pages'], 16) + 4 * i) for i in range(7)]
    pages_c3 = [lk_c3.s32(int(mp_c3['compui_pages'], 16) + 4 * i) for i in range(7)]
    checks['compui_pages = CMP3\'s %s' % pages_c3] = pages == pages_c3 == [40, 41, 42, 21, 22, 23, 24]
    tab = int(man['link']['tables']['core_pagerecs']['at'], 16)
    rec0 = int(mp['compui_rec'], 16)
    entries = [(lk.s32(tab + 8 * i), lk.u32(tab + 8 * i + 4)) for i in range(4)]
    checks['core_pagerecs = {40, 41, 42} -> comp-ui\'s records, then the end'] = (
        entries == [(40, rec0), (41, rec0 + PAGE), (42, rec0 + 2 * PAGE), (0, 0)])
    rec_bad = []
    rec0_c3 = int(mp_c3['compui_rec'], 16)
    for i in range(3):
        r, r3 = rec0 + PAGE * i, rec0_c3 + PAGE * i
        if [lk.string(lk.u32(r)), lk.string(lk.u32(r + 4))] != [lk_c3.string(lk_c3.u32(r3)), lk_c3.string(lk_c3.u32(r3 + 4))]:
            rec_bad.append('record %d titles' % (40 + i))
        if [lk.u32(r + 8 + 4 * k) for k in range(9)] != [lk_c3.u32(r3 + 8 + 4 * k) for k in range(9)]:
            rec_bad.append('record %d rows' % (40 + i))
    checks['the three band records: titles, A..H, LEVEL as in CMP3'] = not rec_bad
    ev, ev_c3 = man['link']['tables']['ev_tick'], json.load(open(CMP3_JSON))['tables']['ev_tick']
    ev_names = [sym_at(mp, lk.u32(int(ev['at'], 16) + 4 * i)) for i in range(ev['entries'])]
    ev_names_c3 = [sym_at(mp_c3, lk_c3.u32(int(ev_c3['at'], 16) + 4 * i)) for i in range(ev_c3['entries'])]
    checks['ev_tick as CMP3\'s: %s' % ev_names] = ev_names == ev_names_c3 and ev['entries'] == ev_c3['entries']
    for f in ('fln_flanger', 'fln_flg', 'core_pagerec', 'compui_draw', 'compui_bar', 'param_rows_fmt_ratio', 'compl_tail',
              'compl_seq', 'compl_ser', 'compl_load', 'compl_update', 'compl_encode', 'compl_decode', 'compl_vmax',
              'compl_vdef'):
        checks['symbol %s in the RAM image' % f] = f in mp and lk.run_start <= int(mp[f], 16) < lk.run_end + 0x100
    checks['no fx-rename tape string and no fx-echo symbol in the link'] = not any(
        k.startswith(('fxr_', 'fxe_')) for k in mp)

    final = bool(a.dsp)
    main_name = a.name + '.main.bin' if final else 'main.bin'
    sy = open(tmp_syx, 'rb').read()
    print('== %s' % os.path.relpath(syx, ROOT))
    for k, v in checks.items():
        print('  %-110s %s' % (k, 'yes' if v else 'NO'))
    if not all(checks.values()):
        for l in stray + moved_bad + mism + stray_stock + ['row 0x%x' % r for r in rows_bad] + tab_bad + rec_bad \
                + ui_bad + link_bad:
            print('  PROBLEM: ' + l)
        os.remove(tmp_syx)
        raise SystemExit('a check failed; nothing written')
    os.replace(tmp_syx, syx)
    with open(syx + '.sha256', 'w') as fh:
        fh.write('%s  %s\n' % (sha(sy), os.path.basename(syx)))
    with open(os.path.join(out, main_name), 'wb') as fh:
        fh.write(img)
    if not final:
        for ext in ('', '.json', '.map.json'):
            with open(mainsyx + ext, 'rb') as src, open(os.path.join(out, MAIN_ONLY + '-main.syx' + ext), 'wb') as dst:
                dst.write(src.read())
    info = {
        'name': a.name,
        'what': ('CMP4\'s main OS = CMP3\'s with flanger-names-dn2 0.1 in place of fx-rename-dn2 0.1 and comp-link-dn2 '
                 '0.3: core-dn2 0.2 + comp-ui-dn2 0.2 + flanger-names-dn2 0.1 + param-grow-dn2 0.1 + param-rows-dn2 0.3 '
                 '+ comp-link-dn2 0.3' + (' with %s' % os.path.relpath(sec7, ROOT) if final else
                                          '; %s.syx carries GT01\'s DSP section (FTD1\'s) for the emulator: never flash it'
                                          % a.name)),
        'main_bin': {'path': os.path.relpath(os.path.join(out, main_name), ROOT), 'bytes': len(img), 'sha256': sha(img)},
        'main_only_syx': {'sha256': sha(open(mainsyx, 'rb').read()), 'version': MAIN_ONLY, 'section7': 'stock'},
        'syx': {'path': os.path.relpath(syx, ROOT), 'bytes': len(sy), 'sha256': sha(sy)},
        'section7': {'path': os.path.relpath(sec7, ROOT), 'sha256': sha(open(sec7, 'rb').read())},
        'dsp_side': dsp_side, 'header': T.header(syx).hex(),
        'section_sizes': {str(i): len(fn[i]) for i in sorted(fn)},
        'mods': [{k: m[k] for k in ('id', 'version', 'file', 'sha256', 'sites')} for m in man['mods']],
        'inplace_gaps': man['output']['main']['inplace_gaps'], 'inplace_gaps_cmp3': {'0x40400000': 2149251, '0x40200000': 52099},
        'link': {k: man['link']['layout'][k] for k in ('ddr', 'bss', 'blob_len', 'ddr_spare')},
        'tables': man['link']['tables'],
        'symbols': {k: mp[k] for k in sorted(mp) if k.startswith(('fln_', 'compl_', 'compui_', 'core_', 'param_rows', 'pg_'))},
        'checks': checks, 'stray_vs_cmp3': stray, 'moved_vs_cmp3': moved, 'moved_bad': moved_bad,
        'stray_vs_stock': stray_stock, 'fx_rename_sites_cmp3_then_cmp4': then_now,
        'delay_rows_stock': ['0x%x' % r for r in delay_rows + list(DELAY_SEND_ROWS)],
        'diff_vs_cmp3': ['0x%08x +%d' % r for r in T.diff_ranges(c3, img)],
        'n_diff_vs_stock': len(T.diff_ranges(stock_img, img)), 'tool_report': rep.splitlines(), 'log': log,
    }
    with open(os.path.join(out, 'build-%s.json' % a.name if final else 'build.json'), 'w') as fh:
        json.dump(info, fh, indent=1)
    print('== %s' % info['what'])
    print('  in-place margins: %s (CMP3: +2,149,251 / +52,099)' % info['inplace_gaps'])
    for m in man['mods']:
        print('  %-13s %-4s %s' % (m['id'], m['version'], m['sha256']))
    print('  %s  %s (%d bytes)' % (sha(sy), os.path.relpath(syx, ROOT), len(sy)))
    print('  %s  %s (the main OS)' % (sha(img), info['main_bin']['path']))


if __name__ == '__main__':
    main()
