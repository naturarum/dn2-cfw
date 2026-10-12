#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
# pyright: reportMissingImports=false
"""T1.8: render the two places where the Digitone II 1.12 bootstrap shows the
OS version, for stock and for a candidate .syx, by running the unit's own
bootstrap under Unicorn with digikit's T1.5 harness (tools/recovery_harness.py,
imported, not changed).

  splash    : a normal power-on with no key held. After the STARTUP-menu
              decision the bootstrap draws container bytes 0x0C..0x17 right-
              aligned at the top (0x80000c96..0x80000cd8) and section 5's
              build stamp at the bottom (0x80000ce2..0x80000d5c), flushes the
              frame (0x80000d6c) and goes on to the loader. The run stops at
              the loader 0x80000596.
  testmode-entry : as testmode below, but stopped at TEST MODE's first key
              poll, before any key is pressed in it (what the screen shows first).
  testmode  : FUNC held at power-on, then a TRIG 1 press in the STARTUP menu
              (key id 0x19, the menu's own dispatch at 0x80002f98 runs
              FUN_80002ae6), then an UP press in TEST MODE (key id 0x0b,
              FUN_80004ba6 redraws the info page FUN_80004aca with the lines
              FUN_80004942 composed, "OS: %s" among them). The run stops at the
              next key poll.

Only key events are injected: the bootstrap's key poll FUN_800013ac (which
blocks on the panel UART ring) is answered with one scripted event per call;
everything else is the harness's model. Nothing talks to hardware.

    vendor/digikit/.venv/bin/python scripts/t1.8_bootstrap_screens.py \
        out/T1.8/EF01.syx [--stock elektron-firmware/Digitone_II_OS1_12.syx] [--out out/T1.8/screens]
"""
import argparse
import json
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DIGIKIT = os.path.join(ROOT, 'vendor', 'digikit')
sys.path.insert(0, DIGIKIT)
sys.path.insert(0, os.path.join(DIGIKIT, 'tools'))

import recovery_harness as rh                                     # noqa: E402
from emu.screen import png                                        # noqa: E402
from unicorn import UC_HOOK_CODE                                  # noqa: E402
from unicorn.m68k_const import UC_M68K_REG_A7, UC_M68K_REG_PC    # noqa: E402

KEYPOLL = 0x800013ac
MENU = (0x80002dc4, 0x80002ff8)        # FUN_80002dc4, the STARTUP menu
TESTMODE = (0x80002ae6, 0x80002dc4)    # FUN_80002ae6, TEST MODE (never returns)
KEY_TRIG1, KEY_UP = 0x19, 0x0b


def frame_pixels(buf):
    """128x64 list of 0/1 from the bootstrap's 1024-byte panel buffer, same
    addressing as recovery_harness.render (FUN_80001ab4's flush)."""
    rows = rh.render(buf).split('\n')
    return [[1 if ch == '#' else 0 for ch in r] for r in rows]


def write_png(buf, path, scale=4, border=2):
    px = frame_pixels(buf)
    h, w = len(px), len(px[0])
    W, H = (w + 2 * border) * scale, (h + 2 * border) * scale
    out = bytearray(W * H)
    for y in range(H):
        sy = y // scale - border
        for x in range(W):
            sx = x // scale - border
            if 0 <= sy < h and 0 <= sx < w:
                out[y * W + x] = 235 if px[sy][sx] else 20
            else:
                out[y * W + x] = 60
    with open(path, 'wb') as fh:
        fh.write(png(out, W, H))


class Script:
    """Answers FUN_800013ac: TRIG 1 in the menu, then (press_up) UP in TEST MODE,
    then stop at the next poll."""

    def __init__(self, press_up=True):
        self.log = []
        self.done = False
        self.press_up = press_up

    def hook(self, uc, addr, size, data):
        sp = uc.reg_read(UC_M68K_REG_A7)
        ret, evp = struct.unpack('>II', uc.mem_read(sp, 8))
        if MENU[0] <= ret < MENU[1] and not any(k == 'menu' for k, *_ in self.log):
            key, where = KEY_TRIG1, 'menu'
        elif (self.press_up and TESTMODE[0] <= ret < TESTMODE[1]
              and not any(k == 'testmode' for k, *_ in self.log)):
            key, where = KEY_UP, 'testmode'
        else:
            # the next poll after the scripted presses: the screen is drawn and
            # flushed; stop here (the real poll would block on an empty ring)
            self.log.append(('stop', '%#x' % ret, None))
            self.done = True
            uc.emu_stop()
            return
        rec = bytearray(uc.mem_read(evp, 8))
        rec[0:4] = b'\x00\x00\x00\x00'      # type 0: key event
        rec[4] = key                         # key id (table 0x80006ce4)
        rec[6] = 1                           # pressed
        uc.mem_write(evp, bytes(rec))
        self.log.append((where, '%#x' % ret, '%#x' % key))
        uc.reg_write(UC_M68K_REG_A7, sp + 4)
        uc.reg_write(UC_M68K_REG_PC, ret)


def run_case(cont, kind):
    script = Script(press_up=(kind == 'testmode')) if kind.startswith('testmode') else None
    orig = rh.Machine

    class M(orig):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            if script is not None:
                self.uc.hook_add(UC_HOOK_CODE, script.hook, begin=KEYPOLL, end=KEYPOLL)

    rh.Machine = M
    try:
        if kind == 'splash':
            st = rh.run(cont, rh.Panel('none'), want=('loader',))
        else:
            st = rh.run(cont, rh.Panel('func'), want=(), limit=400_000_000)
    finally:
        rh.Machine = orig
    st.pop('_m', None)
    if script is not None:
        st['script'] = script.log
        if script.done and st['stop'] == 'ran-to-limit':
            st['stop'] = 'scripted-stop at next key poll'
    return st


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('candidate')
    ap.add_argument('--stock', default=os.path.join(ROOT, 'elektron-firmware', 'Digitone_II_OS1_12.syx'))
    ap.add_argument('--boot', default=os.path.join(ROOT, 'ele3_out', '01_type2.unpacked.bin'))
    ap.add_argument('--out', default=os.path.join(ROOT, 'out', 'T1.8', 'screens'))
    a = ap.parse_args(argv)
    rh.BOOT_IMAGE = a.boot
    os.makedirs(a.out, exist_ok=True)

    files = {'stock': a.stock,
             os.path.splitext(os.path.basename(a.candidate))[0]: a.candidate}
    summary = {}
    for name, path in files.items():
        cont, _secs = rh.parse(path)
        for kind in ('splash', 'testmode-entry', 'testmode'):
            st = run_case(cont, kind)
            frame = st.get('panel_sent') or st.get('panel_compose')
            base = os.path.join(a.out, '%s-%s' % (name, kind))
            with open(base + '.txt', 'w') as fh:
                fh.write(rh.render(frame) + '\n')
            write_png(frame, base + '.png')
            info = {k: st.get(k) for k in ('stop', 'stop_pc', 'landmarks', 'boot_flags',
                                           'flash_reads', 'script')}
            info['lit_sent'] = rh.lit_count(st.get('panel_sent'))
            info['lit_compose'] = rh.lit_count(st.get('panel_compose'))
            info['bad'] = [x for x in ('flash_erase', 'flash_program', 'abort', 'halt_dram')
                           if x in (st.get('landmarks') or [])]
            summary['%s/%s' % (name, kind)] = info
            print('%-18s stop=%s at %s lit=%d/%d landmarks=%s script=%s bad=%s' % (
                name + '/' + kind, info['stop'], info['stop_pc'], info['lit_sent'],
                info['lit_compose'], ' > '.join(info['landmarks'] or []),
                info.get('script'), info['bad']))
            print('   png: %s.png' % base)
    with open(os.path.join(a.out, 'summary.json'), 'w') as fh:
        json.dump(summary, fh, indent=1, default=str)
    return 0


if __name__ == '__main__':
    sys.exit(main())
