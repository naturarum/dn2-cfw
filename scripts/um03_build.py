#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""UM03 (2026-10-31, notes/USB01-class-compliant-multichannel.md): UM02 with usb-multi-dn2 0.3, the 42 channels as a
fourth USB CONFIG choice, USB MULTI/MIDI, and USB AUDIO/MIDI Elektron's stereo mode again (owner: "it should not be
hard ... so we ship it as an option"): CFTE with usb-multi-dn2 0.3 (vendor/elekloader/examples/usb-multi-dn2). With USB
CONFIG on USB MULTI/MIDI the Digitone is a 42-input USB Audio 2.0 device, 24-bit, 48 kHz, tracks 1-16 as stereo pairs
on 1-32, the main mix on 33-34, the delay, reverb and chorus returns on 35-40, the inputs on 41-42.

    venv/bin/python scripts/um03_build.py

Builds the mod (elekloader.sdk.build into out/UM03/mods/), runs its host test (scripts/um01/test_um.c), then links
CFTE's thirteen pinned mods and usb-multi, inserts TE01's DSP section and checks the container as scripts/pack_build.py
checks a pack. Writes out/UM03/UM03.syx (+ .sha256), UM03.main.bin, build-UM03.json, build/.
"""
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scripts'))
sys.dont_write_bytecode = True
import pack_build as PB  # noqa: E402

T, OB, sha = PB.T, PB.OB, PB.sha
NAME = 'UM03'
MODDIR = os.path.join(PB.EL, 'examples', 'usb-multi-dn2')
OUT = os.path.join(PB.OUT, NAME)
# the descriptor fields um_desc writes at run time: their stock values in the image (left as they are)
DESC = {0x402fc407: '02', 0x402fc494: '02', 0x402fc49e: '0420', 0x402fc4a4: '3800'}


def main():
    env = dict(os.environ, ELEKLOADER_CROSS='m68k-elf-')
    build_dir = os.path.join(OUT, 'build')
    mods_dir = os.path.join(OUT, 'mods')
    os.makedirs(build_dir, exist_ok=True)
    os.makedirs(mods_dir, exist_ok=True)
    checks = {}
    ba = os.path.join(PB.OUT, 'BA05', 'BA05.syx')
    if sha(open(PB.STOCK, 'rb').read()) != PB.STOCK_SHA or sha(open(ba, 'rb').read()) != PB.BA05_SYX_SHA:
        raise SystemExit('stock or BA05.syx: sha256 is not the expected one')

    # the mod: its host test, then the SDK build
    test = os.path.join(OUT, 'test_um')
    T.run(['cc', '-O2', '-Wall', '-Wextra', '-Werror', '-o', test, os.path.join(ROOT, 'scripts', 'um01', 'test_um.c')])
    r = subprocess.run([test], capture_output=True, text=True)
    checks['usb-multi\'s sender on the Mac (scripts/um01/test_um.c): %s' % r.stdout.strip().splitlines()[-1]] = (
        r.returncode == 0)
    T.run([PB.ELPY, '-m', 'elekloader.sdk.build', MODDIR, '--stock', PB.STOCK, '--out', mods_dir], cwd=PB.EL, env=env)
    um_file = os.path.join(mods_dir, 'usb-multi-0.3.elemod')

    # CFTE's mods (pinned by BA05, TE01) and usb-multi
    # CFTE's mods: pack_build's pins without usb-multi (since 0.3.0 the packs pin it to this build's manifest)
    PB.MOD_SRC = [m for m in PB.MOD_SRC if m[0] != NAME]
    PB.BASE = [b for b in PB.BASE if b != 'usb-multi']
    files = PB.mod_files(checks)
    want = [m for m in json.load(open(os.path.join(PB.OUT, 'BA05', 'build', 'BA05-main.syx.json')))['mods']
            if m['id'] in PB.BASE + PB.FX_MODS['C'] + PB.FX_MODS['F']]
    ids = [m['id'] for m in want] + ['echo-names']
    paths = [files[i][1] for i in ids] + [um_file]
    ids.append('usb-multi')
    T.run([PB.ELPY, '-m', 'elekloader.lint'] + paths + ['--stock', PB.STOCK], cwd=PB.EL, env=env)
    mainsyx = os.path.join(build_dir, NAME + '-main.syx')
    cmd = [PB.ELPY, '-m', 'elekloader.patch', '--stock', PB.STOCK]
    for f in paths:
        cmd += ['--mod', f]
    T.run(cmd + ['--version', NAME, '--out', mainsyx], cwd=PB.EL, env=env)
    man = json.load(open(mainsyx + '.json'))
    mp = json.load(open(mainsyx + '.map.json'))
    checks['mods: CFTE\'s thirteen then usb-multi (%s)' % ', '.join('%s %s' % (m['id'], m['version'])
                                                                  for m in man['mods'])] = (
        [m['id'] for m in man['mods']] == ids
        and all(m['version'] == files[m['id']][0] and m['sha256'] == files[m['id']][2] for m in man['mods'][:-1]))

    sub = os.path.join(PB.OUT, 'TE01', 'TE01-dsp')
    js = json.load(open(os.path.join(sub, 'build.json')))
    sec7 = os.path.join(sub, 'section7.bin')
    dsyx = os.path.join(ROOT, js['syx']['path'])
    checks['DSP TE01 (CFTE\'s): its record names section7.bin (%s...)' % js['section7']['sha256'][:8]] = (
        js['section7']['sha256'] == sha(open(sec7, 'rb').read()) == PB.REUSED['CFTE'][1])
    syx = os.path.join(OUT, NAME + '.syx')
    tmp = syx + '.tmp'
    T.run([PB.TOOL, '-i', mainsyx, '-c', '7', sec7, '-o', tmp])
    try:
        bas, _ = T.sections(ba)
        fn, _ = T.sections(tmp)
        mn, _ = T.sections(mainsyx)
        st, _ = T.sections(PB.STOCK)
        hdr, h2 = T.header(tmp), T.header(ba)
        checks['ELE3 header: version field %s, the rest as BA05\'s' % NAME] = (
            hdr[0x14:0x18] == NAME.encode() and hdr[:0x14] == h2[:0x14] and hdr[0x18:] == h2[0x18:])
        checks['section ids as BA05\'s'] = sorted(fn) == sorted(bas)
        for i in sorted(bas):
            if i not in (3, 7):
                checks['section %d stored byte for byte as in BA05.syx and stock' % i] = fn[i] == bas[i] == st[i]
        d7, _ = T.sections(dsyx)
        checks['section 7 stored byte for byte as in %s' % os.path.relpath(dsyx, ROOT)] = fn[7] == d7[7]
        checks['section 7 depacks to TE01\'s (%s...)' % js['section7']['sha256'][:8]] = (
            T.depack7(tmp) == open(sec7, 'rb').read())
        checks['section 3 stored as elekloader packed it'] = fn[3] == mn[3]
        img = T.main_image(tmp)
        checks['the main OS depacks to the patcher\'s image (%s...)' % man['main_sha256'][:8]] = (
            sha(img) == man['main_sha256'])
        stock_img = open(T.STOCK_MAIN, 'rb').read()
        el = {m['id']: OB.load_elemod(f) for m, f in zip(man['mods'], paths)}
        checks[NAME + '\'s main OS = its %d .elemod files relinked from its layout and map, byte for byte' % len(paths)] = (
            OB.reconstruct(stock_img, man, mp, el) == img)
        rng = T.diff_ranges(stock_img[:PB.STOCK_END - PB.LOAD], img[:PB.STOCK_END - PB.LOAD])
        spans = [(int(s_['addr'], 16), s_['len']) for d in el.values() for s_ in d['sites']]
        outside = [(x, n) for x, n in rng if not all(any(a <= y < a + k for a, k in spans) for y in range(x, x + n))]
        checks['stock part: %d ranges (%d bytes) differ from Elektron\'s, every one inside a site of the %d mods' % (
            len(rng), sum(n for _, n in rng), len(el))] = not outside
        cf = open(os.path.join(PB.OUT, 'CFTE', 'CFTE.main.bin'), 'rb').read()
        um_sites = el['usb-multi']['sites']
        diff_cf = T.diff_ranges(cf[:PB.STOCK_END - PB.LOAD], img[:PB.STOCK_END - PB.LOAD])
        in_um = [(x, n) for x, n in diff_cf if all(any(int(s_['addr'], 16) <= y < int(s_['addr'], 16) + s_['len']
                                                        for s_ in um_sites) for y in range(x, x + n))]
        checks['against CFTE\'s main OS, the stock part differs only inside usb-multi\'s %d sites (%d ranges)' % (
            len(um_sites), len(diff_cf))] = len(in_um) == len(diff_cf)
        at = lambda a, n: img[a - PB.LOAD:a - PB.LOAD + n].hex()   # noqa: E731
        checks['the descriptors stay Elektron\'s in the stored image (IT 5 and AS 2 bNrChannels 2, subslot 4 / 32 '
               'bits, EP 0x83 56 bytes; um_desc writes them at run time)'] = (
            all(at(a, len(v) // 2) == v for a, v in DESC.items())
            and all(stock_img[a - PB.LOAD:a - PB.LOAD + len(v) // 2].hex() == v for a, v in DESC.items()))
        tgt = {k: int(v, 16) for k, v in mp.items()}
        checks['EP 0x83 IN\'s dQH word (0x400040e6): jsr um_dqh %#x' % tgt.get('um_dqh', 0)] = (
            at(0x400040e6, 2) == '4eb9' and int(at(0x400040e8, 4), 16) == tgt.get('um_dqh', -1))
        checks['the start-up\'s USB mode (0x400cf22c): bgt.s, USB CONFIG 2 and above start mode 5'] = (
            at(0x400cf228, 6) == '7202b2806e06')
        menu = {0x40105220: ('4eb9', 'um_menu_hook'), 0x40104d54: ('4eb9', 'um_cfg_get'),
                0x40104e02: ('4ef9', 'um_mode_set')}
        checks['the USB CFG menu: the fourth item (0x40105220 jsr um_menu_hook), USB AUDIO/MIDI\'s box (0x40104d54 jsr '
               'um_cfg_get) and choice (0x40104e02 jmp um_mode_set)'] = all(
            at(a, 2) == o and int(at(a + 2, 4), 16) == tgt.get(n, -1) for a, (o, n) in menu.items())
        checks['0x4000677a\'s tail (0x40006880) jumps to um_desc %#x' % tgt.get('um_desc', 0)] = (
            at(0x40006880, 2) == '4ef9' and int(at(0x40006882, 4), 16) == tgt.get('um_desc', -1)
            and at(0x40006886, 2) == '4e71')
        jsr = {0x40027772: 'um_send', 0x400279d4: 'um_mute', 0x4000655e: 'um_start'}
        checks['the three calls go to um_send, um_mute, um_start (%s)' % ', '.join(
            '%s %#x' % (n, tgt.get(n, 0)) for n in jsr.values())] = all(
            at(a, 2) == '4eb9' and int(at(a + 2, 4), 16) == tgt.get(n, -1) for a, n in jsr.items())
        for k, v in checks.items():
            print('%-4s %s' % ('ok' if v else 'FAIL', k))
        if not all(checks.values()):
            raise SystemExit(NAME + ': a check failed, nothing written')
        os.replace(tmp, syx)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    s = sha(open(syx, 'rb').read())
    open(syx + '.sha256', 'w').write('%s  %s\n' % (s, os.path.basename(syx)))
    open(os.path.join(OUT, NAME + '.main.bin'), 'wb').write(img)
    json.dump({'name': NAME, 'what': __doc__.split('\n\n')[0], 'syx': {'path': os.path.relpath(syx, ROOT), 'sha256': s,
                                                                         'bytes': os.path.getsize(syx)},
               'main_sha256': sha(img), 'section7': {'path': os.path.relpath(sec7, ROOT),
                                                     'sha256': js['section7']['sha256']},
               'mods': man['mods'], 'checks': checks}, open(os.path.join(OUT, 'build-%s.json' % NAME), 'w'), indent=1)
    print('%s  %s' % (s, os.path.relpath(syx, ROOT)))


if __name__ == '__main__':
    main()
