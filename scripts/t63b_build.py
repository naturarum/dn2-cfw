#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""T6.3 CMP1: build CMP1's main OS = GT01's + the 3-band compressor's pages.

    venv/bin/python scripts/t63b_build.py                    the main OS, and C1EM.syx for the emulator
    venv/bin/python scripts/t63b_build.py --dsp out/T6.3/CMP1-dsp/section7.bin --name CMP1 --out out/T6.3
                                                             the same main OS with that DSP section
                                                             (writes CMP1.syx, CMP1.main.bin,
                                                             build-CMP1.json), as t63_combine.py did
                                                             for CMP0

  CMP1 main OS = core-dn2 0.2 + fx-rename-dn2 0.1 + param-grow-dn2 0.1 + param-rows-dn2 0.2
                 + comp-ui-dn2 0.1
  (GT01 = core 0.1 + fx-rename 0.1 + param-grow 0.1 + param-rows 0.1; CMP0 = GT01's main OS with the
  3-band DSP). core 0.2 adds the page-record table core_pagerecs (it owns the accessor 0x400c25d8);
  param-rows 0.2 gives the 17 band rows their names, ranges, defaults and value texts (data only);
  comp-ui 0.1 puts the rows on three MIXER pages (ids 40..42) with a box and a GR bar per band.
  The band knobs do not reach the DSP yet (CMP2); the hidden stock k 60..64 still drive all bands.

Default output, out/T6.3/CMP1/: main.bin (the --intended-main image), build.json (every check,
every mod's sha256, the in-place margins), C1MO-main.syx (the patcher's main-only container, stock
DSP, under its own version field C1MO; with its .json manifest and .map.json link map), and
C1EM.syx: the same main OS with GT01's DSP section (FTD1's) so that the emulator boots it exactly as
it booted GT01. C1EM is for the emulator only. With --dsp/--name, the final container NAME.syx is
written instead (the DSP side supplies section7.bin); the main OS is the same bytes either way.
No container but NAME.syx carries NAME's version field (the CMP1 review): the main-only reference
is C1MO, and the link under NAME that elektron-firmware-tool needs (it cannot set the version
field) lives in a temporary directory that is removed when the run ends. The final mode also pins the DSP
side (its build.json must record section7.bin and its own container, with no layout problems;
section 7 is stored as in that container) and the main OS (the image the emulator proofs ran,
out/T6.3/CMP1/main.bin, and section 3 stored as in C1EM.syx).

CMP2 (2026-10-17, --stage CMP2): the same five mods + comp-link-dn2 0.1 (the frame-tail hook at 0x40025e9e: the
band knobs k 70..86 to the DSP); defaults --name C2EM --out out/T6.3/CMP2, main-only reference C2MO; the final
mode pins out/T6.3/CMP2/main.bin and C2EM.syx's section 3. Every CMP1 check holds for CMP2 too, with comp-link's
site added to the allowed differences against GT01 and its bytes checked (jsr compl_tail). Without --stage the
script is CMP1's, unchanged in what it builds and checks.

CMP3 (2026-10-18, --stage CMP3): core 0.2, fx-rename 0.1, param-grow 0.1 + param-rows-dn2 0.3 (three band mix rows
0x152..0x154, k 87..89, copied from the stock MIX row) + comp-ui-dn2 0.2 (each band page's H is its band's mix) +
comp-link-dn2 0.2 (the frame tail, and the band values saved per kit: the FX record serializer 0x400dcf34 and loader
0x400dcef0 hooked, FxSetup's vtable slot 17 0x401da4f0 -> compl_update); defaults --name C3EM --out out/T6.3/CMP3,
main-only reference C3MO. Built from the fork's working tree; since CMP3 the CMP1 and CMP2 stages build every mod from
the fork's commit e10bb05 (scripts/elk_pin.py REV_CMP2), where those mods are as CMP1 and CMP2 shipped them, so their
main OS still rebuild byte for byte. CMP3 adds to CMP2's checks: comp-link's four sites hold the planned bytes, the 20
rows (the mixes look 0x9e, the stock MIX's text and range, default 0), H on each page is its band's mix, comp-link's
range/default table equals the rows' maxima and new-kit defaults (the spare k 90..96: 127 / 0), and comp-ui 0.2 and
comp-link 0.2 are refused with param-rows 0.2 (pinned).

Steps (nothing here talks to hardware), as scripts/t53_build.py does for SE01:
  1. scripts/t63b_make_tables.py --check: param-rows' crossover tables are the DSP's laws;
  2. elekloader's SDK builds the five mods from vendor/elekloader (ELEKLOADER_CROSS=m68k-elf-),
     lint passes for the set, the patcher links them into stock 1.12's main OS; with core 0.1
     (pinned at elekloader 59cd41f) comp-ui must be refused (no table core_pagerecs), with
     param-rows 0.1 (pinned too) it must be refused (it requires param-rows>=0.2), and with
     examples/fx-page2-dn2 (which patches the accessor itself) core 0.2 must be refused (overlap);
  3. elektron-firmware-tool puts the DSP section in section 7, into NAME.syx.tmp;
  4. checks (all must hold, or nothing is written: NAME.syx.tmp is removed, and NAME.syx, its
     .sha256, the main OS and the build json are not written; as scripts/t63_combine.py):
     - the container: ELE3 header as stock but the version; sections 2, 4, 5, 8 stored as stock;
       section 3 as elekloader packed it; section 7 as given, depacking to it; checksums and seal;
     - fx-rename and param-grow are GT01's (same .elemod sha256);
     - against GT01's main OS, below the appended blob: every changed byte is in core 0.2's new
       site (0x400c25d8), a comp-ui site, or a site whose jsr/jmp/pointer moved with the link layout
       and still names the same symbol or string;
     - core's new site is `jmp core_pagerec`; each comp-ui site holds the planned bytes;
     - the RAM image: compui_pages = {40, 41, 42, 21, 22, 23, 24}; core_pagerecs = {40, 41, 42}
       -> comp-ui's three records, then id 0; each record's titles (COW strings, refcount -1),
       knobs A..H and LEVEL as notes/T6.4 2.1 says (HIGH's F empty; SCS, MIX, VOL on G, H, LEVEL);
     - the grown table pg_boot will build (simulated from the image): GT01's but for the 17 band
       rows, each as notes/T6.4 2.2 says (name, label, k, range, default, value text; group 0x14,
       no CC, NRPN or modulation, Overbridge id 0x400 + n, Makeup Gain's display entry with flag
       bit 3 = never p-lockable, no randomiser, on no page by param-grow);
     - the stock tables the formatters read: the 8 ratio texts at 0x401fe0e8, the makeup texts'
       formatter 0x400e3b2e; the crossover tables in the RAM image are the laws' (once each).
  5. only then: NAME.syx.tmp becomes NAME.syx, and the other outputs above are written.
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
import t63b_make_tables as XT  # noqa: E402
import elk_pin  # noqa: E402

EL, ELPY, TOOL, STOCK, STOCK_SHA = T.EL, T.ELPY, T.TOOL, T.STOCK, T.STOCK_SHA
STOCK_MAIN, LOAD, STOCK_END = T.STOCK_MAIN, T.LOAD, T.STOCK_END
DESC, ROW, ROWS0, ROWS = T.DESC, T.ROW, T.ROWS0, T.ROWS
GT01_MAIN = os.path.join(ROOT, 'out', 'T6.5', 'GT01.main.bin')
GT01_MAIN_SHA = 'b68f81fca886420358ef18d4bb5b75a7e85cc8b06138c4845195588c338534e2'
GT01_MAP = os.path.join(ROOT, 'out', 'T6.5', 'build', 'GT01-main.syx.map.json')
GT01_SYX = os.path.join(ROOT, 'out', 'T6.5', 'GT01.syx')
GT01_SYX_SHA = '4c64c5dcadacc7711129bc332d38ab01f18e723d0833c5344a707026f7f9e41e'
GT01_MODS = {'fx-rename': 'cf940b0239f2880d87ab35087d817d0dfce64cdd23b8eb2802aa95b30e07bf57',
             'param-grow': '5c0383754b8829ce354c46fdf1eef46ba11ac4de9afca731580f6f1d72c5170d'}
SEC7_GT01, SEC7_GT01_SHA = T.SEC7, T.SEC7_SHA
# the main OS the emulator proofs ran (out/T6.3/CMP1/main.bin); the final container must carry it
CMP1_MAIN_SHA = '3c77d8d513c0a3c3c37da654c25546e380c20ec6b3203146b02b19b9b471287e'
MODDIRS = {'core': 'mods/core-dn2', 'comp-ui': 'examples/comp-ui-dn2', 'fx-rename': 'examples/fx-rename-dn2',
           'param-grow': 'examples/param-grow-dn2', 'param-rows': 'examples/param-rows-dn2',
           'fx-page2': 'examples/fx-page2-dn2', 'comp-link': 'examples/comp-link-dn2'}
SET = ['core', 'comp-ui', 'fx-rename', 'param-grow', 'param-rows']
# the main-only container's own version field: never the deliverable's (CMP1 review)
MAIN_ONLY = 'C1MO'
VERSIONS = {'core': '0.2', 'comp-ui': '0.1', 'fx-rename': '0.1', 'param-grow': '0.1', 'param-rows': '0.2'}
# CMP2 (--stage CMP2): comp-link-dn2 0.1 on top; the main OS its emulator proofs ran (out/T6.3/CMP2/main.bin)
CMP2_MAIN_SHA = '7888a873c44e81032d4d57f0608335598056056f0a6d04316f0a970e1a05200e'
# CMP3 (--stage CMP3): the main OS its emulator proofs ran (out/T6.3/CMP3/main.bin)
CMP3_MAIN_SHA = '13c562b304b4a3be62ac4463a74310651ad74c2fa676b3922baebcaa5509ef08'
STAGES = {
    'CMP1': dict(set=SET, main_only='C1MO', name='C1EM', out=os.path.join(ROOT, 'out', 'T6.3', 'CMP1'),
                 main_sha=CMP1_MAIN_SHA, versions=VERSIONS, rev=elk_pin.REV_CMP2),
    'CMP2': dict(set=SET + ['comp-link'], main_only='C2MO', name='C2EM', out=os.path.join(ROOT, 'out', 'T6.3', 'CMP2'),
                 main_sha=CMP2_MAIN_SHA, versions=dict(VERSIONS, **{'comp-link': '0.1'}), rev=elk_pin.REV_CMP2),
    # CMP3: the fork's working tree (param-rows 0.3, comp-ui 0.2, comp-link 0.2)
    'CMP3': dict(set=SET + ['comp-link'], main_only='C3MO', name='C3EM', out=os.path.join(ROOT, 'out', 'T6.3', 'CMP3'),
                 main_sha=CMP3_MAIN_SHA, versions=dict(VERSIONS, **{'comp-ui': '0.2', 'param-rows': '0.3',
                                                                     'comp-link': '0.2'}), rev=None),
}
LINK_SITE, DRIVER = 0x40025e9e, 0x400cf922
# comp-link 0.2's storage sites: the FX record serializer and loader (entry jmp + nop), FxSetup's vtable slot 17
SER_SITE, LOAD_SITE, SLOT17 = 0x400dcf34, 0x400dcef0, 0x401da4f0
CORE_SITES = (0x40000538, 0x4002e464, 0x4002e4ca, 0x4002f20c, 0x4002f256, 0x4009ff88, 0x400987a2,
              0x400da0b2, 0x400d9230, 0x4012b490)
PAGEREC = 0x400c25d8
PAGES, PAGE = 0x42433c00, 0x2c
FMT_NUMBER, FMT_DB = 0x400e3030, 0x400e3b2e
RATIO_TEXT = 0x401fe0e8
STOCK_RATIOS = ['1.50', '2.00', '3.00', '4.00', '6.00', '8.00', '16.00', '20.00']

# the band pages (notes/T6.4 2.1): id -> (short, long title, A..H, LEVEL)
SCS, MIX, VOL = 0x9b, 0x9e, 0x9f
RECORDS = {
    40: ('COMP LOW', 'Compressor Low', [0x141, 0x143, 0x144, 0x145, 0x142, 0x146, SCS, MIX], VOL),
    41: ('COMP MID', 'Compressor Mid', [0x147, 0x149, 0x14a, 0x14b, 0x148, 0x14c, SCS, MIX], VOL),
    42: ('COMP HIGH', 'Compressor High', [0x14d, 0x14f, 0x150, 0x151, 0x14e, 0, SCS, MIX], VOL),
}
MIXER_PAGES = [40, 41, 42, 21, 22, 23, 24]
# CMP3: H on each band page is its band's mix (param-rows 0.3)
RECORDS3 = {pid: (sn, ln, knobs[:7] + [0x152 + pid - 40], lv) for pid, (sn, ln, knobs, lv) in RECORDS.items()}
# the band rows (notes/T6.4 2.2): row -> (long, short, k, max, default, formatter)
KINDS = [('Threshold', 'THR', 127, 32, FMT_NUMBER), ('Ratio', 'RAT', 8, 4, 'param_rows_fmt_ratio'),
         ('Attack', 'ATK', 127, 24, FMT_NUMBER), ('Release', 'REL', 127, 32, FMT_NUMBER),
         ('Makeup', 'MUP', 127, 64, FMT_DB)]
BAND_ROWS = {}
for b, band in enumerate(('Low', 'Mid', 'High')):
    for i, (nm, sn, mx, df, fmt) in enumerate(KINDS):
        BAND_ROWS[0x141 + 6 * b + i] = ('%s %s' % (band, nm), sn, 70 + 6 * b + i, mx, df, fmt)
BAND_ROWS[0x146] = ('Low/Mid X-Over', 'XOVR', 75, 127, 68, 'param_rows_fmt_xlo')
BAND_ROWS[0x14c] = ('Mid/High X-Over', 'XOVR', 81, 127, 44, 'param_rows_fmt_xhi')
# CMP3: the three band mixes, copied from the stock MIX row 0x9e (its text, range; look 0x9e; default 0)
MIX_ROWS = {0x152 + b: ('%s Mix' % band, 'MIX', 87 + b, 127, 0, FMT_NUMBER) for b, band in enumerate(('Low', 'Mid', 'High'))}
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
    """-> (refused?, the refusal's last line): the patcher must refuse this set"""
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
    ap.add_argument('--stage', default='CMP1', choices=sorted(STAGES),
                    help='CMP1 (default), CMP2 (+ comp-link 0.1) or CMP3 (param-rows 0.3, comp-ui 0.2, comp-link 0.2)')
    ap.add_argument('--name', help='the ELE3 version field (4 characters; default C1EM / C2EM)')
    ap.add_argument('--dsp-json', help='with --dsp: the DSP side\'s build.json (default: beside section7.bin); '
                                       'it must record this section 7 and its own container')
    ap.add_argument('--out')
    a = ap.parse_args()
    global SET, MAIN_ONLY, VERSIONS
    stg = STAGES[a.stage]
    SET, MAIN_ONLY, VERSIONS = stg['set'], stg['main_only'], stg['versions']
    a.name = a.name or stg['name']
    a.out = a.out or stg['out']
    a.stage_info = stg
    if len(a.name) != 4:
        raise SystemExit('--name: 4 characters')
    if a.name == MAIN_ONLY:
        raise SystemExit('--name: %s is the main-only container\'s version field' % MAIN_ONLY)
    sec7 = os.path.abspath(a.dsp) if a.dsp else SEC7_GT01
    pins = [(STOCK, STOCK_SHA), (GT01_MAIN, GT01_MAIN_SHA), (GT01_SYX, GT01_SYX_SHA)]
    if not a.dsp:
        pins.append((SEC7_GT01, SEC7_GT01_SHA))
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
        # the link under NAME (a main-only container with NAME's version field) never stays
        shutil.rmtree(link_dir, ignore_errors=True)
        if os.path.exists(tmp_syx):
            os.remove(tmp_syx)


def build(a, sec7, out, mods_dir, build_dir, syx, tmp_syx, link_dir):
    env = dict(os.environ, ELEKLOADER_CROSS='m68k-elf-')
    checks = {}
    rc, o = run([sys.executable, os.path.join(ROOT, 'scripts', 't63b_make_tables.py'), '--check'], ok=(0, 1))
    checks['param-rows\' xover_table.h is as scripts/t63b_make_tables.py writes it from the DSP\'s laws'] = rc == 0

    # the stage's sources: the fork's working tree (CMP3), or the commit CMP1 and CMP2 were built from (e10bb05)
    src = elk_pin.tree(a.stage_info['rev']) if a.stage_info['rev'] else EL
    moddir = lambda m: os.path.join(src, MODDIRS[m])                          # noqa: E731
    built = {m: sdk_build(moddir(m), mods_dir, env) for m in SET + ['fx-page2']}
    # core 0.1 and param-rows 0.1 as GT01 has them (elekloader 59cd41f), for the refusal tests
    pin = elk_pin.tree(elk_pin.REV_0_1)
    core01 = sdk_build(os.path.join(pin, 'mods', 'core-dn2'), os.path.join(mods_dir, 'pinned'), env)
    rows01 = sdk_build(os.path.join(pin, 'examples', 'param-rows-dn2'), os.path.join(mods_dir, 'pinned'), env)
    cmp3 = VERSIONS.get('comp-link') == '0.2'
    if cmp3:
        # param-rows 0.2 as CMP1 and CMP2 have it (e10bb05), for the refusal tests of comp-ui 0.2 and comp-link 0.2
        pin2 = elk_pin.tree(elk_pin.REV_CMP2)
        rows02 = sdk_build(os.path.join(pin2, 'examples', 'param-rows-dn2'), os.path.join(mods_dir, 'pinned'), env)
    files = [built[m] for m in SET]
    run([ELPY, '-m', 'elekloader.lint'] + files + ['--stock', STOCK], cwd=EL, env=env)
    # the main-only reference container, under its own version field (never NAME's)
    mainsyx = os.path.join(build_dir, MAIN_ONLY + '-main.syx')
    cmd = [ELPY, '-m', 'elekloader.patch', '--stock', STOCK]
    for f in files:
        cmd += ['--mod', f]
    run(cmd + ['--version', MAIN_ONLY, '--out', mainsyx], cwd=EL, env=env)
    # the container gets its own version field from the patcher (elektron-firmware-tool cannot
    # overwrite one elekloader set), so the set is linked again under NAME: the same main OS, in a
    # temporary directory (main() removes it)
    namesyx = os.path.join(link_dir, a.name + '-main.syx')
    run(cmd + ['--version', a.name, '--out', namesyx], cwd=EL, env=env)
    ok, why = refused([core01] + files[1:], env, build_dir, 'core_pagerecs')
    checks['comp-ui with core 0.1 is refused (no table core_pagerecs): %s' % why[:80]] = ok
    low = '0.3' if cmp3 else '0.2'
    ok, why = refused([rows01 if f == built['param-rows'] else f for f in files], env, build_dir,
                      'requires param-rows %s or later' % low)
    checks['comp-ui with param-rows 0.1 (pinned) is refused (it requires param-rows>=%s): %s' % (low, why[:80])] = ok
    if cmp3:
        ok, why = refused([rows02 if f == built['param-rows'] else f for f in files], env, build_dir,
                          'comp-ui 0.2 requires param-rows 0.3 or later', 'comp-link 0.2 requires param-rows 0.3 or later')
        checks['comp-ui 0.2 and comp-link 0.2 with param-rows 0.2 (pinned) are refused (both require param-rows>=0.3): '
               '%s' % why[:80]] = ok
    ok, why = refused(files + [built['fx-page2']], env, build_dir, 'overlap')
    checks['core 0.2 with fx-page2 0.1 (its own accessor patch) is refused: %s' % why[:80]] = ok
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
        json.load(open(namesyx + '.map.json')) == json.load(open(mainsyx + '.map.json'))
        and json.load(open(namesyx + '.json'))['main_sha256'] == json.load(open(mainsyx + '.json'))['main_sha256'])
    checks['section 7 depacks to %s' % os.path.relpath(sec7, ROOT)] = T.depack7(tmp_syx) == open(sec7, 'rb').read()
    dsp_side = None
    if a.dsp:
        # the final container, as scripts/t63_combine.py did for CMP0: the section 7 image must be the
        # one tools/dspsplice built and verify_cmp.py tested, stored as in the DSP side's own container
        dj = os.path.abspath(a.dsp_json) if a.dsp_json else os.path.join(os.path.dirname(sec7), 'build.json')
        bj = json.load(open(dj))
        dsyx = os.path.join(ROOT, bj['syx']['path'])
        sec7_sha = sha(open(sec7, 'rb').read())
        checks['%s records this section 7 (sha256 %s...) and the container %s' % (
            os.path.relpath(dj, ROOT), sec7_sha[:8], bj['syx']['path'])] = (
            bj['section7']['sha256'] == sec7_sha and bj['syx']['sha256'] == sha(open(dsyx, 'rb').read()))
        checks['the DSP side\'s layout check reports no problems'] = not bj['layout_check']['problems']
        ds, _ = T.sections(dsyx)
        checks['section 7 stored byte for byte as in %s' % bj['syx']['path']] = fn[7] == ds[7]
        emdir = a.stage_info['out']
        c1em = os.path.join(emdir, a.stage_info['name'] + '.syx')
        checks['section 3 stored byte for byte as in %s (the container the emulator ran)' % os.path.relpath(c1em, ROOT)] = (
            fn[3] == T.sections(c1em)[0][3])
        dsp_side = {'build_json': os.path.relpath(dj, ROOT), 'syx': bj['syx']['path'], 'syx_sha256': bj['syx']['sha256'],
                    'stored_bytes': len(fn[7]), 'c1em_syx_sha256': sha(open(c1em, 'rb').read())}
    if not a.dsp:
        gt, _ = T.sections(GT01_SYX)
        checks['section 7 stored as in GT01.syx (FTD1\'s)'] = fn[7] == gt[7]
    checks['the main-only container\'s section 7 is stock\'s'] = mn[7] == st[7]
    checks['the main-only container\'s version field is %s, not %s' % (MAIN_ONLY, a.name)] = (
        T.header(mainsyx)[0x14:0x18] == MAIN_ONLY.encode())
    img = T.main_image(tmp_syx)
    checks['main OS depacks to the patcher\'s image'] = sha(img) == man['main_sha256']
    checks['the main-only container depacks to the same main OS'] = T.main_image(mainsyx) == img
    if a.dsp:
        want_sha = a.stage_info['main_sha'] or ''
        tested = os.path.join(a.stage_info['out'], 'main.bin')
        checks['main OS = %s byte for byte, the image the emulator ran (sha256 %s...)'
               % (os.path.relpath(tested, ROOT), want_sha[:8])] = (sha(img) == want_sha and
                                                                 open(tested, 'rb').read() == img)
    for m in man['mods']:
        if m['id'] in GT01_MODS:
            checks['%s .elemod is GT01\'s (sha256 %s...)' % (m['id'], GT01_MODS[m['id']][:8])] = \
                m['sha256'] == GT01_MODS[m['id']]
    checks['mod versions %s' % ', '.join('%s %s' % kv for kv in VERSIONS.items())] = (
        {m['id']: m['version'] for m in man['mods']} == VERSIONS)

    stock_img = open(STOCK_MAIN, 'rb').read()
    gt01 = open(GT01_MAIN, 'rb').read()
    mp_gt = json.load(open(GT01_MAP))
    lk = T.Linked(img, mp)
    lk_gt = T.Linked(gt01, mp_gt)
    rd = lambda path: json.load(open(os.path.join(path, 'mod.json')))           # noqa: E731
    docs = {m: rd(moddir(m)) for m in SET}
    span = lambda s: (int(s['addr'], 16), len(s['stock']) // 2)               # noqa: E731
    ui_spans = [span(s) for s in docs['comp-ui']['sites']]
    link = 'comp-link' in SET
    if link:
        ui_spans += [span(s) for s in docs['comp-link']['sites']]
    new_core = [(PAGEREC, 6)]
    inside = lambda x, spans: any(lo <= x < lo + n for lo, n in spans)         # noqa: E731
    sym_at = lambda m_, v: sorted(k for k, x in m_.items() if int(x, 16) == v and not k.startswith('__'))  # noqa: E731

    # sites of GT01's mods whose operand moved with the link layout: the same symbol or string
    moved, moved_bad, moved_spans = [], [], []
    for mid in ('core', 'fx-rename', 'param-grow'):
        for s in docs[mid]['sites']:
            a_, n = span(s)
            if a_ == PAGEREC:
                continue
            x, y = img[a_ - LOAD:a_ - LOAD + n], gt01[a_ - LOAD:a_ - LOAD + n]
            if x == y:
                continue
            if s['op'] in ('jsr', 'jmp') and x[:2] == y[:2] and x[6:] == y[6:]:
                sx = sym_at(mp, struct.unpack('>I', x[2:6])[0])
                sy = sym_at(mp_gt, struct.unpack('>I', y[2:6])[0])
                if s['target'] in sx and s['target'] in sy:
                    moved.append('0x%08x %s %s %s' % (a_, mid, s['op'], s['target']))
                    moved_spans.append((a_, n))
                    continue
            if s['op'] in ('ptr', 'keep2'):
                off = 2 if s['op'] == 'keep2' else 0
                if x[:off] == y[:off]:
                    px, py = struct.unpack('>I', x[off:off + 4])[0], struct.unpack('>I', y[off:off + 4])[0]
                    if lk.string(px) is not None and lk.string(px) == lk_gt.string(py):
                        moved.append('0x%08x %s %s "%s"' % (a_, mid, s['op'], lk.string(px)))
                        moved_spans.append((a_, n))
                        continue
            moved_bad.append('0x%08x %s' % (a_, mid))
    stray = []
    for lo_, n in T.diff_ranges(gt01[:STOCK_END - LOAD], img[:STOCK_END - LOAD]):
        if not all(inside(x, ui_spans + new_core + moved_spans) for x in range(lo_, lo_ + n)):
            stray.append('0x%08x +%d' % (lo_, n))
    checks['below the blob, every byte that differs from GT01 is in core 0.2\'s new site, a comp-ui site, '
           'or a GT01 site whose operand moved with the link layout to the same symbol/string (%d)'
           % len(moved)] = not stray and not moved_bad
    checks['core\'s ten 0.1 sites hold jsr/jmp to the same core symbols as in GT01'] = all(
        img[c - LOAD:c - LOAD + 2] == gt01[c - LOAD:c - LOAD + 2] and
        sym_at(mp, struct.unpack('>I', img[c - LOAD + 2:c - LOAD + 6])[0]) ==
        sym_at(mp_gt, struct.unpack('>I', gt01[c - LOAD + 2:c - LOAD + 6])[0]) for c in CORE_SITES)
    pr = img[PAGEREC - LOAD:PAGEREC - LOAD + 6]
    checks['0x400c25d8 (the page-record accessor, stock 7224202f0004) is jmp core_pagerec'] = (
        stock_img[PAGEREC - LOAD:PAGEREC - LOAD + 6].hex() == '7224202f0004' and pr[:2] == b'\x4e\xf9'
        and struct.unpack('>I', pr[2:])[0] == int(mp['core_pagerec'], 16))

    # comp-ui's sites
    want = {
        0x4006169c: lambda b: b.hex() == '7207',
        0x400616aa: lambda b: b[:2].hex() == '203c' and struct.unpack('>I', b[2:])[0] == int(mp['compui_pages'], 16),
        0x40070b84: lambda b: b.hex() == '43e9001c',
        0x4007093e: lambda b: b[:2].hex() == '4ef9' and struct.unpack('>I', b[2:])[0] == int(mp['compui_draw'], 16),
        0x4006ffc8: lambda b: (b[:2].hex() == '4eb9' and b[6:].hex() == '4e71'
                               and struct.unpack('>I', b[2:6])[0] == int(mp['compui_baseid'], 16)),
    }
    ui_bad, ui_seen = [], []
    for s in docs['comp-ui']['sites']:
        a_, n = span(s)
        new = img[a_ - LOAD:a_ - LOAD + n]
        if stock_img[a_ - LOAD:a_ - LOAD + n].hex() != s['stock'] or gt01[a_ - LOAD:a_ - LOAD + n].hex() != s['stock']:
            ui_bad.append('0x%08x: stock/GT01 bytes differ from the site\'s' % a_)
        if a_ not in want or not want[a_](new):
            ui_bad.append('0x%08x: %s' % (a_, new.hex()))
        ui_seen.append('0x%08x %s -> %s' % (a_, s['stock'], new.hex()))
    checks['comp-ui\'s five sites hold the planned bytes (count 7, page list, DUAL index 7, draw, '
           'row remap)'] = not ui_bad and len(ui_seen) == 5
    if link:
        # CMP2: comp-link's one site and its code in the RAM image
        ls_ = img[LINK_SITE - LOAD:LINK_SITE - LOAD + 6]
        tail_at = int(mp['compl_tail'], 16)
        code = lk.ram(tail_at, 0x5a)
        checks['0x40025e9e (the frame handler\'s driver call, stock 4eb9400cf922) is jsr compl_tail'] = (
            stock_img[LINK_SITE - LOAD:LINK_SITE - LOAD + 6].hex() == '4eb9400cf922'
            and gt01[LINK_SITE - LOAD:LINK_SITE - LOAD + 6].hex() == '4eb9400cf922'
            and ls_[:2].hex() == '4eb9' and struct.unpack('>I', ls_[2:])[0] == tail_at
            and [s_['addr'] for s_ in docs['comp-link']['sites']][:1] == ['0x40025e9e']
            and len(docs['comp-link']['sites']) == (4 if cmp3 else 1))
        seq_at = int(mp['compl_seq'], 16)
        checks['compl_tail (90 bytes in .run) ends in jmp 0x400cf922 and keeps its sequence in .bss (compl_seq)'] = (
            lk.run_start <= tail_at < lk.run_end and code[-6:].hex() == '4ef9%08x' % DRIVER
            and struct.pack('>I', seq_at) in code and int(mp['__bss_start'], 16) <= seq_at < int(mp['__bss_end'], 16))
    if cmp3:
        # comp-link 0.2's storage sites (T6.4 3.2): jmp + nop at the serializer's and the loader's entry, the
        # vtable slot -> compl_update; the stock bytes as the planner read them
        got = {SER_SITE: img[SER_SITE - LOAD:SER_SITE - LOAD + 8], LOAD_SITE: img[LOAD_SITE - LOAD:LOAD_SITE - LOAD + 8],
               SLOT17: img[SLOT17 - LOAD:SLOT17 - LOAD + 4]}
        stock_b = {SER_SITE: '2f0b2f0a246f000c', LOAD_SITE: '2f0b2f0a226f000c', SLOT17: '4002ffca'}
        want_b = {SER_SITE: '4ef9%08x4e71' % int(mp['compl_ser'], 16), LOAD_SITE: '4ef9%08x4e71' % int(mp['compl_load'], 16),
                  SLOT17: '%08x' % int(mp['compl_update'], 16)}
        checks['comp-link 0.2: 0x400dcf34 jmp compl_ser, 0x400dcef0 jmp compl_load (each + nop), 0x401da4f0 -> '
               'compl_update; stock and GT01 bytes as planned'] = all(
            got[x].hex() == want_b[x] and stock_img[x - LOAD:x - LOAD + len(got[x])].hex() == stock_b[x]
            and gt01[x - LOAD:x - LOAD + len(got[x])].hex() == stock_b[x] for x in got) and sorted(
            int(s_['addr'], 16) for s_ in docs['comp-link']['sites']) == sorted([LINK_SITE] + list(got))
        # the stored words' range/default table: the rows' maxima and new-kit defaults, k 70..89; spare 127 / 0
        vmax = list(lk.ram(int(mp['compl_vmax'], 16), 27))
        vdef = list(lk.ram(int(mp['compl_vdef'], 16), 27))
        want_max, want_def = [], []
        for k in range(70, 97):
            r_ = [x for x in list(BAND_ROWS.values()) + list(MIX_ROWS.values()) if x[2] == k]
            want_max.append(r_[0][3] if r_ else 127)
            want_def.append(r_[0][4] if r_ else 0)
        checks['comp-link 0.2: compl_vmax/compl_vdef = the rows\' maxima and new-kit defaults for k 70..89 '
               '(spare k 90..96: 127 / 0)'] = vmax == want_max and vdef == want_def

    # the RAM image: page list, page-record table, records
    pages = [lk.s32(int(mp['compui_pages'], 16) + 4 * i) for i in range(7)]
    checks['compui_pages = %s' % MIXER_PAGES] = pages == MIXER_PAGES
    tab = int(man['link']['tables']['core_pagerecs']['at'], 16)
    entries = [(lk.s32(tab + 8 * i), lk.u32(tab + 8 * i + 4)) for i in range(4)]
    rec0 = int(mp['compui_rec'], 16)
    checks['core_pagerecs = {40, 41, 42} -> comp-ui\'s records, then the end (id 0)'] = (
        entries == [(40, rec0), (41, rec0 + PAGE), (42, rec0 + 2 * PAGE), (0, 0)]
        and man['link']['tables']['core_pagerecs']['entries'] == 3)
    recs, rec_bad = {}, []
    for i, (pid, (sname, lname, knobs, level)) in enumerate((RECORDS3 if cmp3 else RECORDS).items()):
        r = rec0 + PAGE * i
        ps, pl = lk.u32(r), lk.u32(r + 4)
        got = {'short': lk.string(ps), 'long': lk.string(pl),
               'rows': ['0x%x' % lk.u32(r + 8 + 4 * k) for k in range(8)], 'level': '0x%x' % lk.u32(r + 0x28)}
        recs[pid] = got
        for p_, text in ((ps, sname), (pl, lname)):
            ln, cap, ref = struct.unpack('>iii', lk.ram(p_ - 12, 12))
            if lk.string(p_) != text or ln != len(text) or cap != len(text) or ref != -1:
                rec_bad.append('%d: string "%s" (len %d cap %d ref %d)' % (pid, lk.string(p_), ln, cap, ref))
        if [lk.u32(r + 8 + 4 * k) for k in range(8)] != knobs or lk.u32(r + 0x28) != level:
            rec_bad.append('%d: rows %s' % (pid, got['rows']))
    checks['the three records: titles (COW strings, refcount -1), A..H, LEVEL as notes/T6.4 2.1; HIGH F empty'
           + ('; H = the band\'s mix 0x152/0x153/0x154' if cmp3 else '')] = not rec_bad

    # the grown table and the band rows
    table, rows = T.simulate_boot(lk, stock_img)
    table_gt, rows_gt = T.simulate_boot(lk_gt, stock_img)
    lo, hi = DESC - LOAD, DESC - LOAD + ROWS0 * ROW

    def named(lk_, d):
        f = T.row_fields(d)
        for k_ in ('name', 'group_name', 'short', 'suffix'):
            f[k_] = lk_.string(f[k_])
        return f
    stock_same = all(named(lk, table[r * ROW:(r + 1) * ROW]) == named(lk_gt, table_gt[r * ROW:(r + 1) * ROW])
                     for r in range(ROWS0))
    checks['the grown table\'s stock rows are GT01\'s, field by field (names as strings)'] = stock_same
    last = 0x155 if cmp3 else 0x152
    checks['the grown table\'s rows past 0x%x are GT01\'s (row-0 copies)' % (last - 1)] = all(
        table[r * ROW:(r + 1) * ROW] == table_gt[r * ROW:(r + 1) * ROW] for r in range(last, ROWS))
    row_bad, row_info = [], {}
    if [r['id'] for r in rows] != list(range(0x141, last)):
        row_bad.append('rows %s' % [hex(r['id']) for r in rows])
    all_rows = dict(BAND_ROWS)
    if cmp3:
        all_rows.update(MIX_ROWS)
    if cmp3:
        # the mixes copy the stock MIX row 0x9e: its range, default and text
        mixf = T.row_fields(stock_img[lo + 0x9e * ROW:lo + 0x9f * ROW])
        if (mixf['max'] >> 8, mixf['default'] >> 8, mixf['fmt'], mixf['k']) != (127, 0, FMT_NUMBER, 67):
            row_bad.append('stock MIX row 0x9e: %r' % mixf)
    for r in rows:
        f = named(lk, r['desc'])
        lname, sname, k, mx, df, fmt = all_rows.get(r['id'], (None,) * 6)
        want_fmt = int(mp[fmt], 16) if isinstance(fmt, str) else fmt
        got = (f['name'], f['short'], f['group'], f['k'], f['min'], f['max'] >> 8, f['default'] >> 8, f['bipolar'],
               f['cc'], f['cc2'], f['nrpn'], f['uid'], f['modmask'], f['fmt'], f['group_name'], f['suffix'],
               r['look'], r['flags_set'], r['opts'], r['page'], r['slot'])
        exp = (lname, sname, 0x14, k, 0, mx, df, 0, -1, -1, -1, 0x400 + r['id'] - 0x141, 0, want_fmt, 'Master', '',
               0x9e if r['id'] in MIX_ROWS else 0x99, 8, 0, -1, -1)
        if got != exp:
            row_bad.append('row 0x%x: %r, expected %r' % (r['id'], got, exp))
        # GT01's row with the same id: the same k, CC, NRPN, Overbridge id, group (the mixes are new in CMP3)
        g = [x for x in rows_gt if x['id'] == r['id']]
        gf = named(lk_gt, g[0]['desc']) if g else {}
        if r['id'] in MIX_ROWS and cmp3:
            pass
        elif not g or any(gf[x] != f[x] for x in ('group', 'k', 'cc', 'cc2', 'nrpn', 'uid', 'modmask')):
            row_bad.append('row 0x%x: identity differs from GT01\'s' % r['id'])
        row_info['0x%x' % r['id']] = {'long': f['name'], 'short': f['short'], 'k': f['k'],
                                      'range': [0, f['max'] >> 8], 'default': f['default'] >> 8,
                                      'fmt': '0x%08x' % f['fmt'], 'uid': '0x%x' % f['uid']}
    checks[('the 20 band rows as notes/T6.4 2.2 and notes/T6.3-cmp3.md say (names, k 70..89' if cmp3 else
            'the 17 band rows as notes/T6.4 2.2 says (names, k 70..86') +
           ', ranges, defaults, value texts; no CC/NRPN, uid 0x400+n, look 0x99'
           + (' (the mixes 0x9e, the stock MIX\'s text and range, default 0)' if cmp3 else '')
           + ', flags |= 8, no randomiser, on no page)'] = not row_bad
    # the stock data the formatters use
    ratios = [lk.string(struct.unpack('>I', stock_img[RATIO_TEXT - LOAD + 4 * i:RATIO_TEXT - LOAD + 4 * i + 4])[0])
              for i in range(8)]
    checks['the stock ratio texts at 0x401fe0e8: %s' % ' '.join(ratios)] = ratios == STOCK_RATIOS
    checks['0x400e3b2e is the stock makeup formatter (Makeup Gain row 0x99\'s)'] = (
        T.row_fields(stock_img[lo + 0x99 * ROW:lo + 0x9a * ROW])['fmt'] == FMT_DB)
    lo_t, hi_t = XT.tables()
    run_img = img[lk.run_load - LOAD:lk.run_load - LOAD + (lk.run_end - lk.run_start)]
    checks['the crossover tables in the RAM image are the laws\' (once each)'] = (
        run_img.count(struct.pack('>128H', *lo_t)) == 1 and run_img.count(bytes(hi_t)) == 1)
    for f in ('core_pagerec', 'compui_draw', 'compui_baseid', 'compui_pages', 'compui_rec', 'compui_draws',
              'compui_bar', 'param_rows_fmt_ratio', 'param_rows_fmt_xlo', 'param_rows_fmt_xhi') + (
                  ('compl_tail', 'compl_seq') if link else ()) + (
                  ('compl_ser', 'compl_load', 'compl_update', 'compl_encode', 'compl_decode', 'compl_vmax', 'compl_vdef')
                  if cmp3 else ()):
        checks['symbol %s in the RAM image' % f] = f in mp and lk.run_start <= int(mp[f], 16) < lk.run_end + 0x100

    # outputs: only when every check holds (the CMP1 review: the first CMP1 recipe wrote NAME.syx and its
    # .sha256 before it judged the checks, and left them when one failed)
    final = bool(a.dsp)
    main_name = a.name + '.main.bin' if final else 'main.bin'
    sy = open(tmp_syx, 'rb').read()
    print('== %s' % os.path.relpath(syx, ROOT))
    for k, v in checks.items():
        print('  %-110s %s' % (k, 'yes' if v else 'NO'))
    if not all(checks.values()):
        for l in stray + moved_bad + ui_bad + rec_bad + row_bad:
            print('  PROBLEM: ' + l)
        os.remove(tmp_syx)
        raise SystemExit('a check failed; nothing written')
    os.replace(tmp_syx, syx)
    with open(syx + '.sha256', 'w') as fh:
        fh.write('%s  %s\n' % (sha(sy), os.path.basename(syx)))
    with open(os.path.join(out, main_name), 'wb') as fh:
        fh.write(img)
    if not final:
        # the patcher's main-only container (C1MO, stock DSP), beside main.bin
        for ext in ('', '.json', '.map.json'):
            with open(mainsyx + ext, 'rb') as src, open(os.path.join(out, MAIN_ONLY + '-main.syx' + ext), 'wb') as dst:
                dst.write(src.read())
    gaps = man['output']['main']['inplace_gaps']
    info = {
        'name': a.name,
        'what': (('CMP3\'s main OS = CMP2\'s with param-rows-dn2 0.3 (the band mixes), comp-ui-dn2 0.2 (H = the band\'s '
                  'mix) and comp-link-dn2 0.2 (the band values saved per kit): ' if cmp3 else
                  'CMP2\'s main OS = CMP1\'s + comp-link-dn2 0.1 (the band knobs to the DSP in the SPI frame tail): '
                  if link else 'CMP1\'s main OS = ')
                 + 'core-dn2 0.2 + fx-rename-dn2 0.1 + param-grow-dn2 0.1 + param-rows-dn2 %s ' % VERSIONS['param-rows']
                 + '+ comp-ui-dn2 %s' % VERSIONS['comp-ui'] + (' + comp-link-dn2 %s' % VERSIONS['comp-link'] if link else '')
                 + ' (GT01\'s main OS + the 3-band compressor\'s MIXER pages)'
                 + (' with %s' % os.path.relpath(sec7, ROOT) if final else
                    '; %s.syx carries GT01\'s DSP section (FTD1\'s) for the emulator: never flash it' % a.name)),
        'main_bin': {'path': os.path.relpath(os.path.join(out, main_name), ROOT), 'bytes': len(img), 'sha256': sha(img)},
        'main_only_syx': {'path': os.path.relpath(mainsyx if final else os.path.join(out, MAIN_ONLY + '-main.syx'), ROOT),
                          'sha256': sha(open(mainsyx, 'rb').read()), 'version': MAIN_ONLY, 'section7': 'stock'},
        'syx': {'path': os.path.relpath(syx, ROOT), 'bytes': len(sy), 'sha256': sha(sy)},
        'section7': {'path': os.path.relpath(sec7, ROOT), 'sha256': sha(open(sec7, 'rb').read())},
        'dsp_side': dsp_side,
        'header': T.header(syx).hex(),
        'section_sizes': {str(i): len(fn[i]) for i in sorted(fn)},
        'mods': [{k: m[k] for k in ('id', 'version', 'file', 'sha256', 'sites')} for m in man['mods']],
        'inplace_gaps': gaps,
        'inplace_gaps_gt01': {'0x40400000': 2150305, '0x40200000': 53153},
        'link': {k: man['link']['layout'][k] for k in ('ddr', 'bss', 'blob_len', 'ddr_spare')},
        'tables': man['link']['tables'],
        'symbols': {k: mp[k] for k in ('core_pagerec', 'core_pagerecs', 'compui_draw', 'compui_baseid',
                                       'compui_pages', 'compui_rec', 'compui_draws', 'compui_bar',
                                       'param_rows_fmt_ratio', 'param_rows_fmt_xlo', 'param_rows_fmt_xhi',
                                       'param_rows_comp', 'pg_desc', '__run_start', '__run_load', '__bss_start',
                                       '__bss_end', 'compl_tail', 'compl_seq', 'compl_ser', 'compl_load',
                                       'compl_update', 'compl_encode', 'compl_decode', 'compl_vmax', 'compl_vdef')
                    if k in mp},
        'checks': checks,
        'stray_vs_gt01': stray, 'moved_vs_gt01': moved, 'moved_bad': moved_bad,
        'comp_ui_sites': ui_seen, 'comp_ui_problems': ui_bad,
        'records': recs, 'record_problems': rec_bad,
        'rows': row_info, 'row_problems': row_bad,
        'diff_vs_gt01': ['0x%08x +%d' % r for r in T.diff_ranges(gt01, img)],
        'n_diff_vs_stock': len(T.diff_ranges(stock_img, img)),
        'tool_report': rep.splitlines(), 'log': log,
    }
    with open(os.path.join(out, 'build-%s.json' % a.name if final else 'build.json'), 'w') as fh:
        json.dump(info, fh, indent=1)
    print('== %s' % info['what'])
    print('  in-place margins: %s (GT01: +2,150,305 / +53,153)' % gaps)
    for m in man['mods']:
        print('  %-11s %-4s %s' % (m['id'], m['version'], m['sha256']))
    print('  %s  %s (%d bytes)' % (sha(sy), os.path.relpath(syx, ROOT), len(sy)))
    print('  %s  %s (the main OS)' % (sha(img), info['main_bin']['path']))


if __name__ == '__main__':
    main()
