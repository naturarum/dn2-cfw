# SPDX-License-Identifier: GPL-2.0-or-later
"""Emulated cold boot of a candidate in digikit's emulator (brick checker check 8),
with a watch on every flash write, erase and protection command.

The flow is notes/T1.1's (scripts/dn2-emu-112.sh): emu.extract the
candidate's sections, emu.checkpoint make a cold boot to 400M instructions,
then tools/emucheck.py --device dn2 from that snapshot for 600M instructions,
which decides PASS/FAIL (main screen reached, task count, +Drive formatted,
no stray faked pend) and writes the screen PNG.

What this file adds, without editing digikit: in each of the two emulator
processes it wraps emu.harness.Machine.__init__ so every machine gets
  - a code hook on each NOR driver write/erase/protect entry and on the in-OS
    upgrade writer (stock addresses; check 6 keeps those bytes stock), and
  - a write hook on DSPI0 PUSHR (0xFC05C034) that splits the frames into
    transactions (CONT bit clear = last frame) and records each command byte.
Hooks only observe; they do not change what runs.

    python bc_emuboot.py ladder|check JSON_OUT [args...]     (internal)
"""
import json
import os
import struct
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
DIGIKIT = os.path.join(ROOT, 'vendor', 'digikit')

# write-class entries (notes/T1.4-flash-writers.md section 2, T1.4-verify-2 3.4)
WATCH = {
    0x40134A28: 'WREN 0x06',
    0x40134A6A: 'erase 0x20/0xD8',
    0x4013517E: 'security register program 0x42',
    0x401352B8: 'security register erase 0x44',
    0x401354D6: 'page program 0x02',
    0x40135618: 'page program 4-byte 0x12',
    0x4013576C: 'subsector erase 0x20',
    0x401357CE: 'block erase 0xD8',
    0x40135850: 'block erase 0xD8 (watchdog)',
    0x40135CA6: 'PPB program 0xE3',
    0x40135DC8: 'PPB erase 0xE4',
    0x40135F80: 'DYB write 0xE1',
    0x401360A6: 'program wrapper',
    0x40136122: 'erase-region wrapper',
    0x4013618C: 'erase-region wrapper (watchdog)',
    0x40139104: 'in-OS upgrade writer FUN_40139104',
}
PUSHR = 0xFC05C034
# NOR commands that change the array, its protection or its configuration
WRITE_CMDS = {0x01: 'WRSR', 0x02: 'PP', 0x06: 'WREN', 0x12: '4PP', 0x20: 'SE4K', 0x21: '4SE4K',
              0x42: 'OTPP', 0x44: 'OTP erase', 0x52: 'BE32K', 0x60: 'CE', 0xC7: 'CE', 0xD8: 'SE',
              0xDC: '4SE', 0xE1: 'DYBWR', 0xE3: 'PPBP', 0xE4: 'PPBE', 0x2F: 'ASPP',
              0xA6: 'PLBWR', 0x17: 'BRWR', 0xB7: 'BRAC'}


def install_watch(record):
    """Wrap Machine.__init__ so each new machine carries the watch."""
    from unicorn import UC_HOOK_CODE, UC_HOOK_MEM_WRITE
    from unicorn.m68k_const import UC_M68K_REG_A7
    from emu import harness

    orig = harness.Machine.__init__

    def init(self, *a, **k):
        orig(self, *a, **k)
        uc = self.uc
        state = {'start': True, 'cur': None}

        def entry(addr):
            def h(u, pc, size, data):
                sp = u.reg_read(UC_M68K_REG_A7)
                try:
                    args = struct.unpack('>IIII', u.mem_read(sp, 16))
                except Exception:
                    args = ()
                if len(record['calls']) < 1000:     # a looping candidate must not grow this for 1G instructions
                    record['calls'].append({'entry': '%#010x' % pc, 'what': WATCH[addr],
                                            'stack': ['%#010x' % x for x in args]})
            return h
        for addr in WATCH:
            uc.hook_add(UC_HOOK_CODE, entry(addr), begin=addr, end=addr)

        def pushr(u, access, addr, size, value, data):
            v = value & 0xFFFFFFFF
            if size == 4 and addr == PUSHR:
                if state['start']:
                    state['cur'] = {'cmd': v & 0xFF, 'bytes': []}
                    record['commands'][v & 0xFF] = record['commands'].get(v & 0xFF, 0) + 1
                else:
                    if state['cur'] is not None and len(state['cur']['bytes']) < 5:
                        state['cur']['bytes'].append(v & 0xFF)
                last = not (v & 0x80000000)
                if last and state['cur'] is not None:
                    c = state['cur']
                    if c['cmd'] in WRITE_CMDS and len(record['writes']) < 200:
                        record['writes'].append({'cmd': '%#04x' % c['cmd'],
                                                 'name': WRITE_CMDS[c['cmd']],
                                                 'next_bytes': [('%02x' % b) for b in c['bytes']]})
                    state['cur'] = None
                state['start'] = last
            else:
                record['other_writes'] += 1
        uc.hook_add(UC_HOOK_MEM_WRITE, pushr, begin=PUSHR, end=PUSHR + 3)

    harness.Machine.__init__ = init


def child(argv):
    """Runs inside digikit's venv with cwd = vendor/digikit."""
    phase, out = argv[0], argv[1]
    sys.path.insert(0, DIGIKIT)
    sys.path.insert(0, os.path.join(DIGIKIT, 'tools'))
    record = {'calls': [], 'commands': {}, 'writes': [], 'other_writes': 0}
    install_watch(record)
    t = time.time()
    rc = 0
    try:
        if phase == 'ladder':
            prefix, syx = argv[2], argv[3]
            from emu import checkpoint
            checkpoint.make([400_000_000], prefix, syx)
        elif phase == 'check':
            import emucheck
            sys.argv = ['emucheck.py'] + argv[2:]
            rc = emucheck.main()
        else:
            raise SystemExit('unknown phase %r' % phase)
    finally:
        record['commands'] = {'%#04x' % k: v for k, v in sorted(record['commands'].items())}
        record['seconds'] = round(time.time() - t, 1)
        with open(out, 'w') as fh:
            json.dump(record, fh, indent=1)
    return rc or 0


def run(cand_path, out_dir, python=None, log=print):
    """Orchestrate the three steps for one candidate. -> result dict."""
    py = python or sys.executable
    sec = os.path.join(out_dir, 'sections')
    snap = os.path.join(out_dir, 'snapshots')
    os.makedirs(snap, exist_ok=True)
    env = dict(os.environ, DT2_SECTIONS=sec, DT2_SNAPSHOTS=snap, PYTHONUNBUFFERED='1')
    res = {'sections': sec, 'snapshot': os.path.join(snap, 'boot400M.snap'), 'steps': {}}

    def step(name, cmd):
        lp = os.path.join(out_dir, name + '.log')
        log('    [check 8] %s ... (log %s)' % (name, lp))
        t = time.time()
        with open(lp, 'w') as fh:
            p = subprocess.run(cmd, cwd=DIGIKIT, env=env, stdout=fh, stderr=subprocess.STDOUT)
        res['steps'][name] = {'returncode': p.returncode, 'seconds': round(time.time() - t, 1),
                              'log': lp}
        return p.returncode, open(lp, errors='replace').read()

    rc, _ = step('extract', [py, '-m', 'emu.extract', cand_path, '-o', sec])
    if rc:
        res['error'] = 'emu.extract failed (exit %d)' % rc
        return res
    me = os.path.abspath(__file__)
    wl = os.path.join(out_dir, 'watch-ladder.json')
    rc, _ = step('ladder', [py, me, 'ladder', wl, os.path.join(snap, 'boot'), cand_path])
    res['watch_ladder'] = json.load(open(wl)) if os.path.exists(wl) else None
    if rc or not os.path.exists(res['snapshot']):
        res['error'] = 'cold boot to 400M failed (exit %d)' % rc
        return res
    wc = os.path.join(out_dir, 'watch-check.json')
    png = os.path.join(out_dir, 'main-screen.png')
    rc, text = step('emucheck', [py, me, 'check', wc, '--device', 'dn2', '--syx', cand_path,
                                 '--sections', sec, '--snapshot', res['snapshot'],
                                 '--instrs', '600000000', '--png', png])
    res['watch_check'] = json.load(open(wc)) if os.path.exists(wc) else None
    lines = [ln for ln in text.splitlines() if ln.startswith(('PASS dn2', 'FAIL dn2'))]
    res['verdict_line'] = lines[-1] if lines else None
    res['emucheck_pass'] = bool(lines) and lines[-1].startswith('PASS') and rc == 0
    res['reasons'] = [ln.strip() for ln in text.splitlines()
                      if ln.strip().startswith(('PASS:', 'FAIL:'))]
    first = [ln.strip() for ln in text.splitlines() if 'main screen first latched' in ln]
    res['first_main_screen'] = first[-1] if first else None
    res['png'] = png if os.path.exists(png) else None
    stem = os.path.splitext(png)[0] + '-first.png'
    res['png_first'] = stem if os.path.exists(stem) else None
    return res


if __name__ == '__main__':
    sys.exit(child(sys.argv[1:]))
