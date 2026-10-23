#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""OB01: CMP4 with os-version-dn2 0.1, so that Overbridge and Transfer see OS 1.12 (notes/OB01-overbridge.md).

    venv/bin/python scripts/ob01_build.py [--name OB01] [--out out/OB01]

  OB01 main OS = CMP4's six mods (the same .elemod files, sha256-checked against CMP4's build) + os-version-dn2 0.1;
  OB01 DSP section = CMP4's, stored byte for byte as in out/CMP4/CMP4.syx. The ELE3 version field is the build's name
  (OB01): the bootstrap still draws it on the power-on screen and in TEST MODE, while os-version makes the two main-OS
  sites that send the version to the computer report "1.12".

Writes NAME.syx (+ .sha256), NAME.main.bin, build-NAME.json, and mods/, build/. Checks (all must hold, or nothing is
written):
  - the pins: stock 1.12, CMP4.syx, CMP4's main OS image, CMP4's section 7;
  - the container: ELE3 header as stock but the version field; sections 2, 4, 5, 8 stored as stock; section 7 stored
    as in CMP4.syx and depacking to CMP4's section7.bin; section 3 as elekloader packed it;
  - the mods: CMP4's six .elemod files (sha256) plus os-version 0.1, in the linker's order;
  - against CMP4's main OS, in the stock part of the image: every byte that differs is inside a mod site; at the two
    os-version sites a jsr to osv_hdr; at every other site the operand names the same place (the same stock address,
    or the same offset in the same mod section or table) in both links;
  - the appended blob: every mod section of CMP4's is in OB01's with the same bytes but at relocation fields, whose
    values name the same place in both links; the tables have CMP4's entries, naming the same places; the blob grows
    by os-version's .run only.
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

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scripts'))
import t65_build as T  # noqa: E402

EL, ELPY, TOOL, STOCK, STOCK_SHA = T.EL, T.ELPY, T.TOOL, T.STOCK, T.STOCK_SHA
LOAD, STOCK_END = T.LOAD, T.STOCK_END
CMP4 = os.path.join(ROOT, 'out', 'CMP4')
CMP4_SYX, CMP4_SYX_SHA = os.path.join(CMP4, 'CMP4.syx'), 'c1df74aaa36f987c0dbdfb43486fe059952c413fccf95272966064cec42c3b88'
CMP4_MAIN = os.path.join(CMP4, 'CMP4.main.bin')
CMP4_MAN = os.path.join(CMP4, 'main', 'C4MO-main.syx.json')
CMP4_MAP = os.path.join(CMP4, 'main', 'C4MO-main.syx.map.json')
CMP4_SEC7 = os.path.join(CMP4, 'CMP4-dsp', 'section7.bin')
CMP4_DSP_JSON = os.path.join(CMP4, 'CMP4-dsp', 'build.json')
CMP4_MODS = os.path.join(CMP4, 'main', 'mods')
OSV_DIR = os.path.join(EL, 'examples', 'os-version-dn2')
OSV_SITES = (0x400cf1c8, 0x40126270)
HDR_GET = 0x40134850
log = T.log


def sha(b):
    return hashlib.sha256(b).hexdigest()


def run(cmd, cwd=None, env=None):
    return T.run(cmd, cwd=cwd, env=env)


def parts(ps, stock_img):
    """a section's bytes: hex parts, and stock parts copied from the stock image (elemod.py's format)"""
    out = bytearray()
    for p in ps:
        if p[0] == 'hex':
            out += bytes.fromhex(p[1])
        else:
            a_ = p[1] if isinstance(p[1], int) else int(p[1], 0)
            n = p[2] if isinstance(p[2], int) else int(p[2], 0)
            out += stock_img[a_ - LOAD:a_ - LOAD + n]
    return out


def reconstruct(stock_img, man, mp, elemods):
    """the main OS image rebuilt from the .elemod files, the manifest's layout and the symbol map (link.py's rules:
    the sites, the core's .boot, the RAM image of every .run and the tables), to compare with the patcher's image"""
    lay, glob = man['link']['layout'], {k: int(v, 16) for k, v in mp.items()}
    base = {tuple(k.split(' ')): v for k, v in lay['sections'].items()}
    ddr0, run_end = lay['ddr'][0], glob['__bss_start']

    def resolve(mid, tgt):
        if tgt == 'abs':
            return 0
        kind, nm = tgt.split(':', 1)
        if kind == 'sec':
            return base[(mid, nm)]
        sym = elemods[mid]['symbols'].get(nm)
        if sym is not None:
            return sym[1] if sym[0] == 'abs' else base[(mid, sym[0])] + sym[1]
        return glob[nm]

    def put(buf, off, typ, value, p):
        if typ == 'abs32':
            struct.pack_into('>I', buf, off, value & 0xFFFFFFFF)
        elif typ == 'pc32':
            struct.pack_into('>i', buf, off, value - p)
        else:
            struct.pack_into('>h', buf, off, value - p)

    content = {}
    for mid, d in elemods.items():
        for sec in ('.boot', '.run'):
            if (mid, sec) in base:
                content[(mid, sec)] = parts(d['sections'][sec]['parts'], stock_img)
        for sec, off, typ, tgt, add in d['relocs']:
            put(content[(mid, sec)], off, typ, resolve(mid, tgt) + add, base[(mid, sec)] + off)
    ddr = bytearray(run_end - ddr0)
    for (mid, sec), c in content.items():
        if sec == '.run':
            ddr[base[(mid, sec)] - ddr0:base[(mid, sec)] - ddr0 + len(c)] = c
    for t, info in man['link']['tables'].items():
        at, entry = int(info['at'], 16), info['entry']
        ents = sorted((e.get('order', 0), mid, i, e) for mid, d in elemods.items()
                      for i, e in enumerate(d.get('contribute', [])) if e['to'] == t)
        buf = bytearray()
        for _o, mid, _i, e in ents:
            dd = bytearray(bytes.fromhex(e['data']))
            for off, typ, tgt, add in e['relocs']:
                put(dd, off, typ, resolve(mid, tgt) + add, at + len(buf) + off)
            buf += dd
        buf += bytes(entry)
        ddr[at - ddr0:at - ddr0 + len(buf)] = buf
    blob = bytearray(content[('core', '.boot')])
    blob += bytes(lay['run_load'] - STOCK_END - len(blob))
    blob += ddr
    img = bytearray(stock_img)
    for mid, d in elemods.items():
        for st_ in d['sites']:
            a_ = int(st_['addr'], 16)
            nb = bytearray(bytes.fromhex(st_['new']))
            for off, typ, tgt, add in st_['relocs']:
                put(nb, off, typ, resolve(mid, tgt) + add, a_ + off)
            img[a_ - LOAD:a_ - LOAD + st_['len']] = nb
    return bytes(img + blob)


def load_elemod(path):
    return json.load(open(path))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--name', default='OB01')
    ap.add_argument('--out', default=os.path.join(ROOT, 'out', 'OB01'))
    a = ap.parse_args()
    if len(a.name) != 4:
        raise SystemExit('--name: 4 characters')
    out = os.path.abspath(a.out)
    mods_dir, build_dir = os.path.join(out, 'mods'), os.path.join(out, 'build')
    os.makedirs(mods_dir, exist_ok=True)
    os.makedirs(build_dir, exist_ok=True)
    env = dict(os.environ, ELEKLOADER_CROSS='m68k-elf-')
    checks = {}

    c4man = json.load(open(CMP4_MAN))
    sec7_sha = json.load(open(CMP4_DSP_JSON))['section7']['sha256']
    for p, want in ((STOCK, STOCK_SHA), (CMP4_SYX, CMP4_SYX_SHA), (CMP4_MAIN, c4man['main_sha256']),
                    (CMP4_SEC7, sec7_sha)):
        if sha(open(p, 'rb').read()) != want:
            raise SystemExit('%s: sha256 is not the expected one' % p)

    # CMP4's six mods, the very files CMP4 linked
    files = []
    for m in c4man['mods']:
        f = os.path.join(CMP4_MODS, m['file'])
        checks['%s %s: CMP4\'s .elemod (sha256 %s...)' % (m['id'], m['version'], m['sha256'][:8])] = (
            sha(open(f, 'rb').read()) == m['sha256'])
        files.append(f)
    T.run([ELPY, '-m', 'elekloader.sdk.build', OSV_DIR, '--stock', STOCK, '--out', mods_dir], cwd=EL, env=env)
    osv = os.path.join(mods_dir, 'os-version-0.1.elemod')
    files.append(osv)
    T.run([ELPY, '-m', 'elekloader.lint'] + files + ['--stock', STOCK], cwd=EL, env=env)
    mainsyx = os.path.join(build_dir, a.name + '-main.syx')
    cmd = [ELPY, '-m', 'elekloader.patch', '--stock', STOCK]
    for f in files:
        cmd += ['--mod', f]
    T.run(cmd + ['--version', a.name, '--out', mainsyx], cwd=EL, env=env)
    man = json.load(open(mainsyx + '.json'))
    syx = os.path.join(out, a.name + '.syx')
    tmp = syx + '.tmp'
    T.run([TOOL, '-i', mainsyx, '-c', '7', CMP4_SEC7, '-o', tmp])

    try:
        st, _ = T.sections(STOCK)
        fn, _ = T.sections(tmp)
        c4, _ = T.sections(CMP4_SYX)
        mn, _ = T.sections(mainsyx)
        hdr, shdr = T.header(tmp), T.header(STOCK)
        checks['ELE3 header: version field %s, the rest as stock' % a.name] = (
            hdr[0x14:0x18] == a.name.encode() and hdr[:0x14] == shdr[:0x14] and hdr[0x18:] == shdr[0x18:])
        checks['section ids as stock'] = sorted(fn) == sorted(st)
        for i in (2, 4, 5, 8):
            checks['section %d stored as stock' % i] = fn[i] == st[i]
        checks['section 7 stored byte for byte as in CMP4.syx'] = fn[7] == c4[7]
        checks['section 7 depacks to CMP4\'s section7.bin (sha256 %s...)' % sec7_sha[:8]] = (
            T.depack7(tmp) == open(CMP4_SEC7, 'rb').read())
        checks['section 3 stored as elekloader packed it'] = fn[3] == mn[3]
        img = T.main_image(tmp)
        checks['main OS depacks to the patcher\'s image'] = sha(img) == man['main_sha256']
        ids = [m['id'] for m in man['mods']]
        checks['mods: CMP4\'s six plus os-version 0.1, in the linker\'s order (%s)' % ', '.join(ids)] = (
            [m['sha256'] for m in man['mods'] if m['id'] != 'os-version'] == [m['sha256'] for m in c4man['mods']]
            and [(m['id'], m['version']) for m in man['mods'] if m['id'] == 'os-version'] == [('os-version', '0.1')])

        c4img = open(CMP4_MAIN, 'rb').read()
        stock_img = open(T.STOCK_MAIN, 'rb').read()
        elemods = {m['id']: load_elemod(os.path.join(mods_dir if m['id'] == 'os-version' else CMP4_MODS, m['file']))
                   for m in man['mods']}
        c4mods = {k: v for k, v in elemods.items() if k != 'os-version'}
        mp = json.load(open(mainsyx + '.map.json'))
        checks['CMP4\'s main OS = its six .elemod files relinked from its manifest\'s layout and map, byte for byte'] = (
            reconstruct(stock_img, c4man, json.load(open(CMP4_MAP)), c4mods) == c4img)
        checks['%s\'s main OS = the seven .elemod files relinked from its layout and map, byte for byte' % a.name] = (
            reconstruct(stock_img, man, mp, elemods) == img)
        sites = [(int(st_['addr'], 16), st_['len'], mid) for mid, d in elemods.items() for st_ in d['sites']]
        diffs = [i for i in range(STOCK_END - LOAD) if img[i] != c4img[i]]
        outside = [LOAD + i for i in diffs if not any(sa <= LOAD + i < sa + n for sa, n, _ in sites)]
        hit = sorted({(sa, mid) for i in diffs for sa, n, mid in sites if sa <= LOAD + i < sa + n})
        osv = int(mp['osv_hdr'], 16)
        jsr = lambda t: bytes.fromhex('4eb9') + struct.pack('>I', t)            # noqa: E731
        checks['stock part: %d bytes differ from CMP4, all inside %d mod sites (%s)' % (
            len(diffs), len(hit), ', '.join('%s 0x%08x' % (m_, sa) for sa, m_ in hit))] = not outside
        checks['os-version\'s sites 0x%08x and 0x%08x: CMP4 jsr 0x%08x (stock), %s jsr osv_hdr 0x%08x' % (
            OSV_SITES + (HDR_GET, a.name, osv))] = all(
            c4img[x - LOAD:x - LOAD + 6] == jsr(HDR_GET) and img[x - LOAD:x - LOAD + 6] == jsr(osv) for x in OSV_SITES)
        lay_c, lay_o = c4man['link']['layout'], man['link']['layout']
        new = sorted(set(lay_o['sections']) - set(lay_c['sections']))
        grow = lay_o['blob_len'] - lay_c['blob_len']
        osv_len = elemods['os-version']['sections']['.run']['len']
        checks['layout: CMP4\'s sections all there, new %s; the blob grows by %d bytes (os-version .run %d B, '
               '.bss %d B)' % (', '.join(new), grow, osv_len, elemods['os-version']['sections']['.bss']['size'])] = (
            set(lay_c['sections']) <= set(lay_o['sections']) and new == ['os-version .bss', 'os-version .run']
            and osv_len <= grow <= osv_len + 12)
        if outside:
            log.append('outside the sites: ' + ', '.join('0x%08x' % x for x in outside[:20]))

        for k, v in checks.items():
            print('%-4s %s' % ('ok' if v else 'FAIL', k))
        if not all(checks.values()):
            print('\n'.join(log[-30:]))
            raise SystemExit('a check failed: nothing written')
        os.replace(tmp, syx)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    s = sha(open(syx, 'rb').read())
    open(syx + '.sha256', 'w').write('%s  %s\n' % (s, os.path.basename(syx)))
    open(os.path.join(out, a.name + '.main.bin'), 'wb').write(img)
    json.dump({'name': a.name, 'syx': {'path': os.path.relpath(syx, ROOT), 'sha256': s},
               'main_sha256': sha(img), 'section7_sha256': sec7_sha, 'base': 'CMP4 ' + CMP4_SYX_SHA,
               'mods': man['mods'], 'osv_hdr': mp['osv_hdr'], 'osv_copy': mp['osv_copy'], 'checks': checks, 'log': log},
              open(os.path.join(out, 'build-%s.json' % a.name), 'w'), indent=1)
    print('%s  %s' % (s, os.path.relpath(syx, ROOT)))


if __name__ == '__main__':
    main()
