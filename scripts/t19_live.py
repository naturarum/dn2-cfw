#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""T1.9: run digikit's emu.live headless with a modded main OS, and read mod symbols at the end.

Two ways to get the modded code into the emulator:

* reboot path (the real test): a snapshot made by cold-booting the candidate .syx
  (scripts/t19-emu.sh ladder), then this script with --syx CANDIDATE.syx and DT2_SECTIONS
  pointing at the candidate's sections. Nothing is patched here.
* --hotpatch BUILD.syx (a quick check while developing): load a snapshot of STOCK, then do
  what the candidate's boot would have done: write the bytes where its main OS differs from
  stock (the core's sites), copy its run image to __run_start and zero its .bss (from the
  build's .map.json). This is what core's .boot does at 0x40000538; the OS's own RAM state
  is the snapshot's. It is not a proof of boot.

    cd vendor/digikit && DT2_SECTIONS=out/sections/dn2-1.12 uv run python \\
       ../../scripts/t19_live.py SNAP --syx STOCK.syx [--hotpatch BUILD.syx] \\
       --ssi0-hz --ips 150e6 --script S --out DIR --midi-log DIR/midi.txt \\
       [--read-syms BUILD.syx.map.json:hooktest_steps,hooktest_notes]

Every other argument is emu.live's. Read-only on digikit.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.getcwd())                      # vendor/digikit
from emu import live                                 # noqa: E402

MAIN_LOAD = 0x40000400
STOCK_MAIN = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          'ele3_out', '02_type3.unpacked.bin')


def pop_opt(argv, name):
    if name in argv:
        i = argv.index(name)
        v = argv[i + 1]
        del argv[i:i + 2]
        return v
    return None


def main_of(syx_path):
    sys.path.insert(0, os.path.join(os.path.dirname(STOCK_MAIN), '..', 'vendor', 'elekloader'))
    from elekloader import devices, formats
    dev = [d for d in devices.devices() if d.key == 'digitone-mk2'][0]
    p = formats.parse(open(syx_path, 'rb').read(), dev)
    return formats.main_image(p, dev)


def hotpatch(m, build_syx):
    uc = m.uc
    img = main_of(build_syx)
    st = open(STOCK_MAIN, 'rb').read()
    mp = {k: int(v, 16) for k, v in json.load(open(build_syx + '.map.json')).items()}
    n = 0
    i = 0
    while i < len(st):
        if img[i] != st[i]:
            j = i
            while j < len(st) and img[j] != st[j]:
                j += 1
            uc.mem_write(MAIN_LOAD + i, bytes(img[i:j]))
            print('[t19] patched 0x%08x: %s -> %s' % (MAIN_LOAD + i, st[i:j].hex(), img[i:j].hex()))
            n += j - i
            i = j
        else:
            i += 1
    lo = mp['__run_load'] - MAIN_LOAD
    run = bytes(img[lo:lo + 4 * mp['__run_words']])
    for a in range(mp['__run_start'] & ~0xfffff, mp['__bss_end'] + 1, 0x100000):
        m.ensure(a)                    # digikit maps a page on first touch
    uc.mem_write(mp['__run_start'], run)
    uc.mem_write(mp['__bss_start'], b'\0' * (4 * mp['__bss_words']))
    try:
        uc.ctl_remove_cache(MAIN_LOAD, MAIN_LOAD + len(st))
    except Exception as e:                                  # older binding
        print('[t19] ctl_remove_cache: %s' % e)
    print('[t19] hot-patched %d site bytes; run image %d bytes at 0x%08x; .bss %d bytes zeroed'
          % (n, len(run), mp['__run_start'], 4 * mp['__bss_words']), flush=True)


def add_traces(m, spec):
    """spec: ADDR[,ADDR...]; print d0-d7, a0-a7 and 7 longs of the stack at each."""
    from unicorn import UC_HOOK_CODE
    from unicorn.m68k_const import (UC_M68K_REG_D0, UC_M68K_REG_A0)
    for a in [int(x, 16) for x in spec.split(',')]:
        def cb(uc, addr, size, data):
            d = [uc.reg_read(UC_M68K_REG_D0 + i) for i in range(8)]
            ar = [uc.reg_read(UC_M68K_REG_A0 + i) for i in range(8)]
            stk = bytes(uc.mem_read(ar[7], 48))
            w = [int.from_bytes(stk[i:i + 4], 'big') for i in range(0, 48, 4)]
            if os.environ.get('T19_TRACE_STEP'):
                # an ev_midi_step handler's entry: (sp) ret, vev, track, step, pattern, kit,
                # then core_midistep's saved event and a2, then the return into the site + 6
                rd = lambda a: int.from_bytes(bytes(uc.mem_read(a, 4)), 'big')
                mev = rd(w[1] + 0x18)
                print('[step] site 0x%08x track %d step %d vev %08x mev %08x flags %08x '
                      'head %d' % (w[8] - 6, w[2], w[3], w[1], mev, rd(mev + 0x10),
                                   rd(mev + 0x18) - (1 << 32 if rd(mev + 0x18) >> 31 else 0)),
                      flush=True)
                return
            print('[trace] 0x%08x d=%s a=%s sp: %s' % (
                addr, ' '.join('%x' % x for x in d), ' '.join('%x' % x for x in ar),
                ' '.join(stk[i:i + 4].hex() for i in range(0, 32, 4))), flush=True)
        m.uc.hook_add(UC_HOOK_CODE, cb, begin=a, end=a)


class Patched(live.LiveEmulator):
    build_syx = None
    traces = None

    def _setup(self):
        r = super()._setup()
        m = r[0]
        if self.build_syx:
            hotpatch(m, self.build_syx)
        if self.traces:
            add_traces(m, self.traces)
        return r


def main():
    argv = sys.argv[1:]
    build_syx = pop_opt(argv, '--hotpatch')
    read = pop_opt(argv, '--read-syms')
    Patched.build_syx = build_syx
    Patched.traces = pop_opt(argv, '--trace')
    live.LiveEmulator = Patched
    # digikit identifies the panel by the .syx file's sha256, so a modded file is "unknown" and
    # emu.live then silently drops every key (LiveEmulator.device_error). The panel is the same
    # hardware: identify a modded file as the stock release it was built from (--identify-as).
    ident = pop_opt(argv, '--identify-as')
    if ident:
        orig_identify = live.devices.identify

        def identify(path, dirpath=None):
            try:
                return orig_identify(path, dirpath)
            except BaseException:
                print('[t19] %s is not a known release; panel identified as %s'
                      % (os.path.basename(path), os.path.basename(ident)), flush=True)
                return orig_identify(ident, dirpath)
        live.devices.identify = identify
    captured = {}
    orig_summary = live.summary

    def summary(emu):
        if read:
            path, names = read.rsplit(':', 1)
            mp = {k: int(v, 16) for k, v in json.load(open(path)).items()}
            uc = emu._m.uc
            for nm in names.split(','):
                nm, _, nb = nm.partition('*')
                if nb:
                    raw = bytes(uc.mem_read(mp[nm], int(nb)))
                    print('[t19] %s @0x%08x = %s' % (nm, mp[nm], ' '.join(
                        raw[i:i + 4].hex() for i in range(0, len(raw), 4))), flush=True)
                    continue
                v = int.from_bytes(bytes(uc.mem_read(mp[nm], 4)), 'big')
                captured[nm] = v
                print('[t19] %s @0x%08x = %d' % (nm, mp[nm], v), flush=True)
        return orig_summary(emu)

    live.summary = summary
    sys.argv = [sys.argv[0]] + argv
    rc = live.main()
    return rc


if __name__ == '__main__':
    t0 = time.monotonic()
    rc = main()
    print('[t19] wall %.1f s' % (time.monotonic() - t0))
    sys.exit(rc)
