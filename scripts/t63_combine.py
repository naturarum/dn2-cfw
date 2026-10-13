#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""T6.3: combine CMP0 = GT01's main OS (byte-identical) + the CMP0 DSP section 7.

    venv/bin/python scripts/t63_combine.py [--dsp out/T6.3/CMP0-dsp/section7.bin] [--name CMP0]

  CMP0 = core-dn2 0.1 + fx-rename-dn2 0.1 + param-grow-dn2 0.1 + param-rows-dn2 0.1 (GT01's four
  .elemod files, out/T6.5/mods, sha256-pinned) + section 7 from tools/dspsplice's CMP0 recipe
  (FTD1's flanger and tape delay + the 3-band master compressor).

Why not GT01.syx with section 7 swapped: elektron-firmware-tool -V cannot overwrite a version field
elekloader set, so the patcher links GT01's four mods again with --version CMP0 (as
scripts/t65_build.py did with GT01). The main OS must come out byte-identical to GT01's.

Steps (nothing here talks to hardware):
  1. pins: stock, GT01.syx, GT01.main.bin, GT01's intermediate, the four .elemod files, and the
     section 7 image against tools/dspsplice's build.json;
  2. elekloader's patcher links the four mods into stock 1.12's main OS with --version NAME;
  3. elektron-firmware-tool puts section7.bin in section 7;
  4. checks (all must hold, or nothing final is written):
     - the ELE3 header: version field NAME, the rest as stock (and as GT01's);
     - section ids as stock; sections 2, 4, 5, 8 stored as stock; section 3 stored byte for byte
       as in GT01.syx (and as elekloader packed it);
     - section 7 stored as in the DSP side's CMP0-dsp.syx, depacking to section7.bin;
     - the main OS (elekloader's depack) is GT01.main.bin byte for byte, and the patcher's
       manifest says the same sha256;
     - the patcher's link map is GT01's, symbol for symbol;
     - against GT01.syx, the container differs only in the version field, section 7 and what
       follows from them (lengths, checksums, seal);
  5. writes out/T6.3/NAME.syx, NAME.syx.sha256, build-NAME.json.
"""
import argparse
import hashlib
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scripts'))
import t65_build as T  # noqa: E402

EL, ELPY, TOOL, STOCK, STOCK_SHA = T.EL, T.ELPY, T.TOOL, T.STOCK, T.STOCK_SHA
GT01_SYX = os.path.join(ROOT, 'out', 'T6.5', 'GT01.syx')
GT01_SYX_SHA = '4c64c5dcadacc7711129bc332d38ab01f18e723d0833c5344a707026f7f9e41e'
GT01_MAIN = os.path.join(ROOT, 'out', 'T6.5', 'GT01.main.bin')
GT01_MAIN_SHA = 'b68f81fca886420358ef18d4bb5b75a7e85cc8b06138c4845195588c338534e2'
GT01_INTER = os.path.join(ROOT, 'out', 'T6.5', 'build', 'GT01-main.syx')
GT01_INTER_SHA = 'c20d4535b437c631601029b53e4506b66c777413810005b93734302543c4e946'
GT01_MAP = GT01_INTER + '.map.json'
MODS_DIR = os.path.join(ROOT, 'out', 'T6.5', 'mods')
GT01_MODS = [('core', 'core-0.1.elemod', '63a2ea9074393fda8c97fbddb4fddb07adff7a58621133d2670364e1d7f378c9'),
             ('fx-rename', 'fx-rename-0.1.elemod', 'cf940b0239f2880d87ab35087d817d0dfce64cdd23b8eb2802aa95b30e07bf57'),
             ('param-grow', 'param-grow-0.1.elemod', '5c0383754b8829ce354c46fdf1eef46ba11ac4de9afca731580f6f1d72c5170d'),
             ('param-rows', 'param-rows-0.1.elemod', 'd93b9f885ab9716a5c56cd139c926572d7ffad676545ccfdde6311311b75c253')]
DSP_DIR = os.path.join(ROOT, 'out', 'T6.3', 'CMP0-dsp')
OUT = os.path.join(ROOT, 'out', 'T6.3')
log = T.log


def sha(b):
    return hashlib.sha256(b).hexdigest()


def fsha(p):
    return sha(open(p, 'rb').read())


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--dsp', default=os.path.join(DSP_DIR, 'section7.bin'))
    ap.add_argument('--dsp-syx', default=os.path.join(DSP_DIR, 'CMP0-dsp.syx'),
                    help='the DSP side\'s own container (stock main OS + this section 7)')
    ap.add_argument('--dsp-json', default=os.path.join(DSP_DIR, 'build.json'))
    ap.add_argument('--name', default='CMP0', help='the ELE3 version field (4 characters)')
    ap.add_argument('--out', default=OUT)
    a = ap.parse_args()
    if len(a.name) != 4:
        raise SystemExit('--name: 4 characters')
    sec7 = os.path.abspath(a.dsp)
    pins = [(STOCK, STOCK_SHA), (GT01_SYX, GT01_SYX_SHA), (GT01_MAIN, GT01_MAIN_SHA), (GT01_INTER, GT01_INTER_SHA)]
    pins += [(os.path.join(MODS_DIR, f), h) for _, f, h in GT01_MODS]
    for p, want in pins:
        if fsha(p) != want:
            raise SystemExit('%s: sha256 is not the expected one' % p)
    # the section 7 image must be the one tools/dspsplice built (and verify_cmp.py tested)
    bj = json.load(open(a.dsp_json))
    sec7_sha = fsha(sec7)
    if bj['section7']['sha256'] != sec7_sha or bj['syx']['sha256'] != fsha(a.dsp_syx):
        raise SystemExit('%s or %s: sha256 is not the one %s records' % (sec7, a.dsp_syx, a.dsp_json))
    if bj['layout_check']['problems']:
        raise SystemExit('%s: the layout check reports problems' % a.dsp_json)
    out = os.path.abspath(a.out)
    build_dir = os.path.join(out, 'build')
    os.makedirs(build_dir, exist_ok=True)
    env = dict(os.environ, ELEKLOADER_CROSS='m68k-elf-')
    files = [os.path.join(MODS_DIR, f) for _, f, _ in GT01_MODS]
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

    st, _ = T.sections(STOCK)
    gt, _ = T.sections(GT01_SYX)
    mn, _ = T.sections(mainsyx)
    ds, _ = T.sections(a.dsp_syx)
    fn, rep = T.sections(tmp)
    hdr, shdr, ghdr = T.header(tmp), T.header(STOCK), T.header(GT01_SYX)
    checks = {}
    checks['ELE3 header: version field %s, the rest as stock' % a.name] = (
        hdr[0x14:0x18] == a.name.encode() and hdr[:0x14] == shdr[:0x14] and hdr[0x18:] == shdr[0x18:])
    checks['ELE3 header: as GT01\'s but the version field'] = (hdr[:0x14] == ghdr[:0x14] and hdr[0x18:] == ghdr[0x18:])
    checks['section ids as stock'] = sorted(fn) == sorted(st)
    for i in (2, 4, 5, 8):
        checks['section %d stored as stock' % i] = fn[i] == st[i]
    checks['section 3 stored as elekloader packed it'] = fn[3] == mn[3]
    checks['section 3 stored byte for byte as in GT01.syx'] = fn[3] == gt[3]
    checks['section 7 stored as in %s' % os.path.relpath(a.dsp_syx, ROOT)] = fn[7] == ds[7]
    checks['section 7 depacks to %s (sha256 %s...)' % (os.path.relpath(sec7, ROOT), sec7_sha[:8])] = (
        T.depack7(tmp) == open(sec7, 'rb').read())
    checks['section 7 differs from GT01\'s (FTD1\'s)'] = fn[7] != gt[7]
    img = T.main_image(tmp)
    gt01 = open(GT01_MAIN, 'rb').read()
    checks['main OS depacks to GT01.main.bin byte for byte (sha256 %s...)' % GT01_MAIN_SHA[:8]] = img == gt01
    checks['the patcher\'s manifest: main sha256 = GT01.main.bin'] = man['main_sha256'] == GT01_MAIN_SHA
    checks['the four .elemod files are GT01\'s'] = (
        [(m['id'], m['sha256']) for m in man['mods']] == [(i, h) for i, _, h in GT01_MODS])
    mp_gt = json.load(open(GT01_MAP))
    checks['the link map is GT01\'s, symbol for symbol (%d symbols)' % len(mp)] = mp == mp_gt
    # the main-only intermediates: GT01's and this one differ only in the version field (and the
    # checksum/seal that cover it)
    inter_same = all(mn[i] == T.sections(GT01_INTER)[0][i] for i in mn)
    checks['the patcher\'s main-only container: every section stored as in GT01\'s'] = inter_same

    sy = open(tmp, 'rb').read()
    gsy = open(GT01_SYX, 'rb').read()
    if not all(checks.values()):
        for k, v in checks.items():
            print('  %-100s %s' % (k, 'yes' if v else 'NO'))
        os.remove(tmp)
        raise SystemExit('a check failed; nothing written')
    os.replace(tmp, syx)
    with open(syx + '.sha256', 'w') as fh:
        fh.write('%s  %s\n' % (sha(sy), os.path.basename(syx)))
    info = {
        'name': a.name,
        'what': 'GT01\'s main OS (core-dn2 0.1 + fx-rename-dn2 0.1 + param-grow-dn2 0.1 + param-rows-dn2 0.1, '
                'byte-identical) + the CMP0 DSP section 7 (FTD1\'s flanger and tape delay + the 3-band master '
                'compressor on the stock Compressor page\'s knobs)',
        'syx': {'path': os.path.relpath(syx, ROOT), 'bytes': len(sy), 'sha256': sha(sy)},
        'main_bin': {'path': os.path.relpath(GT01_MAIN, ROOT), 'bytes': len(img), 'sha256': sha(img),
                     'note': 'the --intended-main image: GT01\'s, unchanged'},
        'section7': {'path': os.path.relpath(sec7, ROOT), 'bytes': os.path.getsize(sec7), 'sha256': sec7_sha,
                     'stored_bytes': len(fn[7]), 'gt01_stored_bytes': len(gt[7])},
        'dsp_syx': {'path': os.path.relpath(a.dsp_syx, ROOT), 'sha256': fsha(a.dsp_syx)},
        'gt01_syx': {'path': os.path.relpath(GT01_SYX, ROOT), 'bytes': len(gsy), 'sha256': GT01_SYX_SHA},
        'intermediate': {'path': os.path.relpath(mainsyx, ROOT), 'sha256': fsha(mainsyx)},
        'mods': [{k: m[k] for k in ('id', 'version', 'file', 'sha256', 'sites')} for m in man['mods']],
        'header': hdr.hex(),
        'section_sizes': {str(i): len(fn[i]) for i in sorted(fn)},
        'checks': checks,
        'tool_report': rep.splitlines(), 'log': log,
    }
    with open(os.path.join(out, 'build-%s.json' % a.name), 'w') as fh:
        json.dump(info, fh, indent=1)
    print('== %s: %s' % (a.name, info['what']))
    for k, v in checks.items():
        print('  %-100s %s' % (k, 'yes' if v else 'NO'))
    print('  %s  %s (%d bytes)' % (sha(sy), os.path.relpath(syx, ROOT), len(sy)))
    print('  %s  %s (intended main)' % (sha(img), os.path.relpath(GT01_MAIN, ROOT)))
    print('  %s  %s (intended dsp)' % (sec7_sha, os.path.relpath(sec7, ROOT)))


if __name__ == '__main__':
    main()
