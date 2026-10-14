#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""T5.0b: build FTN1 = FTD1's DSP (section 7) + core-dn2 + fx-rename-dn2 in the main OS (section 3).

    venv/bin/python scripts/t50b_build.py [--out DIR (default out/T5.0b)]

Steps (nothing here talks to hardware):
  1. scripts/t50b_make_mod.py writes fx-rename-dn2's mod.json from the stock image;
  2. elekloader's SDK builds core-dn2 0.1 (from the pinned elekloader commit, scripts/elk_pin.py)
     and fx-rename-dn2 (ELEKLOADER_CROSS=m68k-elf-), lints them
     together, and its patcher links them into stock 1.12's main OS with version FTN1
     (out/T5.0b/build/FTN1-main.syx: the main OS changed, section 7 still stock);
  3. elektron-firmware-tool replaces section 7 with FTD1's depacked DSP stream
     (out/T3.8/FTD1/section7.bin) and sets the version again: out/T5.0b/FTN1.syx;
  4. checks: sections 2, 4, 5 and 8 stored byte for byte as stock; section 3 stored byte for byte as
     elekloader packed it; section 7 stored byte for byte as in FTD1.syx and depacking to
     section7.bin; the main OS (elekloader's own depack) equal to the image the patcher built;
     the descriptor table differs from stock only in the renamed rows' +0x28/+0x30 words;
  5. writes out/T5.0b/FTN1.main.bin (the --intended-main image), FTN1.syx.sha256, build.json.
"""
import hashlib
import json
import os
import re
import struct
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EL = os.path.join(ROOT, 'vendor', 'elekloader')
ELPY = os.path.join(EL, '.venv', 'bin', 'python')
TOOL = os.path.join(ROOT, 'vendor', 'elektron-firmware-tool', 'elektron-firmware-tool')
STOCK = os.path.join(ROOT, 'elektron-firmware', 'Digitone_II_OS1_12.syx')
STOCK_SHA = '7e5b8a733d4ab1fdf7e5d22757f7d8eddaa3cbf71d0858874a791844c02ceb35'
STOCK_MAIN = os.path.join(ROOT, 'ele3_out', '02_type3.unpacked.bin')
FTD1 = os.path.join(ROOT, 'out', 'T3.8', 'FTD1', 'FTD1.syx')
FTD1_SHA = '14123a67bea5664f4e7922443b78a4c4cf7007264e2ec88429c9c9f355058f88'
SEC7 = os.path.join(ROOT, 'out', 'T3.8', 'FTD1', 'section7.bin')
SEC7_SHA = '9170d99eaf0c95cb35f59275dbef6db9b1bbb4e5c2a5ddcfdc830abdb1d6457d'
OUT = os.path.join(ROOT, 'out', 'T5.0b')
MODS = os.path.join(OUT, 'mods')
BUILD = os.path.join(OUT, 'build')
NAME = 'FTN1'
LOAD = 0x40000400
DESC, ROW, ROWS = 0x401f83a4, 0x3c, 0x141

log = []


def sha(b):
    return hashlib.sha256(b).hexdigest()


def run(cmd, cwd=None, env=None):
    log.append('$ ' + ' '.join(os.path.relpath(c, ROOT) if c.startswith(ROOT) else c for c in cmd))
    r = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True)
    out = (r.stdout + r.stderr).strip()
    if out:
        log.append(out)
    if r.returncode:
        print('\n'.join(log))
        raise SystemExit('failed (%d): %s' % (r.returncode, ' '.join(cmd)))
    return out


def sections(syx):
    """-> {id: stored bytes} from the container, using the tool's own section table"""
    with tempfile.TemporaryDirectory() as t:
        c = os.path.join(t, 'c.bin')
        rep = run([TOOL, '-i', syx, '-v', '--emit-container', c])
        data = open(c, 'rb').read()
    out = {}
    for m in re.finditer(r'id=(\d+)\s+[^\n]*?off=0x([0-9a-f]+)\s+clen=0x([0-9a-f]+)', rep):
        i, off, n = int(m.group(1)), int(m.group(2), 16), int(m.group(3), 16)
        out[i] = data[off:off + n]
    oks = [l for l in rep.splitlines() if re.search(r'per-packet checksum|content checksum|stream-sum|digest +:', l)]
    if len(oks) < 7 or not all(l.rstrip().endswith('[ok]') for l in oks):
        raise SystemExit('%s: the tool does not report every checksum ok' % syx)
    return out, rep


def depack7(syx):
    with tempfile.TemporaryDirectory() as t:
        run([TOOL, '-i', syx, '-d', '7', '-o', t])
        return open(os.path.join(t, 'section_7_blob.bin'), 'rb').read()


def main_image(syx):
    code = ('import sys; sys.path.insert(0, %r)\n'
            'from elekloader import devices, formats\n'
            'dev = [d for d in devices.devices() if d.key == "digitone-mk2"][0]\n'
            'p = formats.parse(open(%r, "rb").read(), dev)\n'
            'sys.stdout.buffer.write(bytes(formats.main_image(p, dev)))\n') % (EL, syx)
    r = subprocess.run([ELPY, '-c', code], capture_output=True)
    if r.returncode:
        raise SystemExit(r.stderr.decode())
    return r.stdout


def diff_ranges(a, b):
    out, i, n = [], 0, min(len(a), len(b))
    while i < n:
        if a[i] != b[i]:
            j = i
            while j < n and a[j] != b[j]:
                j += 1
            out.append((LOAD + i, j - i))
            i = j
        else:
            i += 1
    if len(b) > len(a):
        out.append((LOAD + len(a), len(b) - len(a)))
    return out


def main():
    global OUT, MODS, BUILD
    if '--out' in sys.argv[1:]:          # write somewhere else (a reproducibility check)
        OUT = os.path.abspath(sys.argv[sys.argv.index('--out') + 1])
        MODS, BUILD = os.path.join(OUT, 'mods'), os.path.join(OUT, 'build')
    for p, want in ((STOCK, STOCK_SHA), (FTD1, FTD1_SHA), (SEC7, SEC7_SHA)):
        if sha(open(p, 'rb').read()) != want:
            raise SystemExit('%s: sha256 is not the expected one' % p)
    os.makedirs(MODS, exist_ok=True)
    os.makedirs(BUILD, exist_ok=True)
    env = dict(os.environ, ELEKLOADER_CROSS='m68k-elf-')

    # 1-2: the mod, the main OS
    run([sys.executable, os.path.join(ROOT, 'scripts', 't50b_make_mod.py')])
    # core-dn2 0.1 from the pinned elekloader commit (scripts/elk_pin.py): the working tree moved it
    # on for CMP1 (0.2), and FTN1 must keep rebuilding byte for byte
    sys.path.insert(0, os.path.join(ROOT, 'scripts'))
    import elk_pin
    pin = elk_pin.tree(elk_pin.REV_0_1)
    for d in (os.path.join(pin, 'mods', 'core-dn2'), 'examples/fx-rename-dn2'):
        run([ELPY, '-m', 'elekloader.sdk.build', d, '--stock', STOCK, '--out', MODS], cwd=EL, env=env)
    core, ren = os.path.join(MODS, 'core-0.1.elemod'), os.path.join(MODS, 'fx-rename-0.1.elemod')
    run([ELPY, '-m', 'elekloader.lint', core, ren, '--stock', STOCK], cwd=EL, env=env)
    mainsyx = os.path.join(BUILD, NAME + '-main.syx')
    run([ELPY, '-m', 'elekloader.patch', '--stock', STOCK, '--mod', core, '--mod', ren,
         '--version', NAME, '--out', mainsyx], cwd=EL, env=env)
    man = json.load(open(mainsyx + '.json'))

    # 3: FTD1's DSP. elekloader has already written the version field ("FTN1" in the ELE3 header,
    # where stock has "1.12"); the tool's -V looks for the stock value, so it is not used here.
    syx = os.path.join(OUT, NAME + '.syx')
    run([TOOL, '-i', mainsyx, '-c', '7', SEC7, '-o', syx])

    # 4: checks
    st, _ = sections(STOCK)
    ftd, _ = sections(FTD1)
    mn, _ = sections(mainsyx)
    fn, rep = sections(syx)
    checks = {}
    with tempfile.TemporaryDirectory() as t:
        run([TOOL, '-i', syx, '--emit-container', os.path.join(t, 'c.bin')])
        hdr = open(os.path.join(t, 'c.bin'), 'rb').read()[:0x20]
    with tempfile.TemporaryDirectory() as t:
        run([TOOL, '-i', STOCK, '--emit-container', os.path.join(t, 'c.bin')])
        shdr = open(os.path.join(t, 'c.bin'), 'rb').read()[:0x20]
    checks['ELE3 header: version field FTN1, the rest as stock'] = (
        hdr[0x14:0x18] == NAME.encode() and hdr[:0x14] == shdr[:0x14] and hdr[0x18:] == shdr[0x18:])
    checks['section ids'] = sorted(fn) == sorted(st)
    for i in (2, 4, 5, 8):
        checks['section %d stored as stock' % i] = fn[i] == st[i]
    checks['section 3 stored as elekloader packed it'] = fn[3] == mn[3]
    checks['section 7 stored as in FTD1.syx'] = fn[7] == ftd[7]
    back7 = depack7(syx)
    checks['section 7 depacks to FTD1 section7.bin'] = back7 == open(SEC7, 'rb').read()
    img = main_image(syx)
    checks['main OS depacks to the patcher\'s image'] = sha(img) == man['main_sha256']
    stock_img = open(STOCK_MAIN, 'rb').read()
    # the descriptor table: only name pointers of the renamed rows
    lo, hi = DESC - LOAD, DESC - LOAD + ROW * ROWS
    changed = []
    for o in range(lo, hi, 4):
        if img[o:o + 4] != stock_img[o:o + 4]:
            r, f = divmod(o - lo, ROW)
            changed.append((r, f))
    checks['descriptor table: only +0x28/+0x30 words changed'] = all(f in (0x28, 0x30) for r, f in changed)
    renamed_rows = sorted({r for r, f in changed})
    ranges = diff_ranges(stock_img, img)
    with open(os.path.join(OUT, NAME + '.main.bin'), 'wb') as f:
        f.write(img)
    sy = open(syx, 'rb').read()
    with open(syx + '.sha256', 'w') as f:
        f.write('%s  %s\n' % (sha(sy), os.path.basename(syx)))
    info = {
        'name': NAME,
        'what': 'FTD1 (flanger in the chorus slot, tape delay in the delay slot) + core-dn2 0.1 + '
                'fx-rename-dn2 0.1 (labels only)',
        'syx': {'path': os.path.relpath(syx, ROOT), 'bytes': len(sy), 'sha256': sha(sy)},
        'main_bin': {'path': os.path.relpath(os.path.join(OUT, NAME + '.main.bin'), ROOT),
                     'bytes': len(img), 'sha256': sha(img)},
        'section7': {'path': os.path.relpath(SEC7, ROOT), 'sha256': SEC7_SHA},
        'intermediate': {'path': os.path.relpath(mainsyx, ROOT), 'sha256': sha(open(mainsyx, 'rb').read())},
        'mods': [{k: m[k] for k in ('id', 'version', 'file', 'sha256', 'sites')} for m in man['mods']],
        'link': {k: man['link']['layout'][k] for k in ('ddr', 'bss', 'blob_len', 'ddr_spare')},
        'checks': checks,
        'descriptor_rows_renamed': ['0x%x' % r for r in renamed_rows],
        'descriptor_words_changed': ['row 0x%x +0x%x' % rf for rf in changed],
        'main_diff_ranges': ['0x%08x +%d' % rr for rr in ranges],
        'tool_report': rep.splitlines(),
        'log': log,
    }
    with open(os.path.join(OUT, 'build.json'), 'w') as f:
        json.dump(info, f, indent=1)
    for k, v in checks.items():
        print('%-50s %s' % (k, 'yes' if v else 'NO'))
    print('renamed rows:', ' '.join(info['descriptor_rows_renamed']))
    print('main OS changed ranges: %d' % len(ranges))
    print('%s  %s (%d bytes)' % (sha(sy), os.path.relpath(syx, ROOT), len(sy)))
    print('%s  %s' % (sha(img), info['main_bin']['path']))
    if not all(checks.values()):
        raise SystemExit('a check failed')


if __name__ == '__main__':
    main()
