#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""T5.3: who reads the delay page's record and the two display entries fx-echo changes, from reset
until fx-echo's first ev_tick has changed them, in digikit's emulator.

    cd vendor/digikit && DT2_SECTIONS=../../out/T5.3/SE01-main/emu/SE1M/sections uv run python \\
        ../../scripts/t53_bootwatch.py reset --syx ../../out/T5.3/SE01-main/SE1M.syx --limit 400000000 \\
        --json ../../out/T5.3/SE01-main/bootwatch/SE1M-0-400M.json
    ... t53_bootwatch.py resume ../../out/T5.3/SE01-main/emu/SE1M/snapshots/boot400M.snap \\
        --syx ../../out/T5.3/SE01-main/SE1M.syx --limit 800000000 --json .../SE1M-400M-800M.json

`reset` boots from reset with emu.dspboot.run (the path `emu.checkpoint make` builds the ladder on,
so instruction counts n compare with the ladder's); `resume` runs tools/guirun.py's configuration
from a snapshot (the ladder's second stage; n is counted per guirun chunk, so it is exact to a
chunk, and the order of the events is exact). Recorded, in order:
  - every read and write of page record 17 (0x42433eec, 0x2c bytes; knob G at 0x42433f0c), with
    n, PC, address and the value read or written;
  - every read and write of the display entries of rows 0x72 (MODE) and 0x7a (AGE) (0x44 bytes each
    at 0x4243425c + 0x44 x row), the same way (reads counted by PC once there are many);
  - the static initializer 0x400c2c4e (entry), fxe_tick's first entry, and the write that sets
    fxe_state (1 = done, -1 = refused).
The question: does anything read knob G of page 17, or those two display entries, between the
static initializer that builds them and fxe_tick's write? Nothing in digikit is edited.
"""
import collections
import json
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIGIKIT = os.path.join(ROOT, 'vendor', 'digikit')
sys.path.insert(0, DIGIKIT)
sys.path.insert(0, os.path.join(DIGIKIT, 'tools'))

PAGE17, PAGE17_N, SLOT_G = 0x42433eec, 0x2c, 0x42433f0c
DISP, DROW = 0x4243425c, 0x44
ROWS = {0x72: 'MODE', 0x7a: 'AGE'}
SINIT = 0x400c2c4e
MAP = os.path.join(ROOT, 'out', 'T5.3', 'SE01-main', 'build', 'SE01-main.syx.map.json')


def opt(argv, name, default=None):
    if name in argv:
        i = argv.index(name)
        v = argv[i + 1]
        del argv[i:i + 2]
        return v
    return default


class Rec:
    def __init__(self, nfn):
        self.n = nfn
        self.events = []                 # (n, kind, pc, addr, size, value)
        self.by_pc = collections.Counter()
        self.marks = {}
        self.state_writes = []

    def mem(self, kind):
        def h(uc, access, addr, size, value, user):
            from unicorn.m68k_const import UC_M68K_REG_PC
            pc = uc.reg_read(UC_M68K_REG_PC)
            if kind == 'read':
                value = int.from_bytes(bytes(uc.mem_read(addr, size)), 'big')
            self.by_pc[(kind, '0x%08x' % pc, where(addr))] += 1
            if len(self.events) < 4000:
                self.events.append((self.n(), kind, '0x%08x' % pc, '0x%08x' % addr, size, '0x%x' % value))
        return h


def where(a):
    if PAGE17 <= a < PAGE17 + PAGE17_N:
        o = a - PAGE17
        return 'page17 ' + ('+0 short' if o < 4 else '+4 long' if o < 8 else
                            'knob %s' % 'ABCDEFGH'[(o - 8) // 4] if o < 0x28 else 'LEVEL')
    for r, nm in ROWS.items():
        if DISP + DROW * r <= a < DISP + DROW * (r + 1):
            return 'disp 0x%x (%s) +0x%02x' % (r, nm, (a - DISP - DROW * r) & ~3)
    return '0x%08x' % a


def install(uc, rec, mp):
    from unicorn import UC_HOOK_MEM_READ, UC_HOOK_MEM_WRITE, UC_HOOK_CODE
    uc.hook_add(UC_HOOK_MEM_READ, rec.mem('read'), begin=PAGE17, end=PAGE17 + PAGE17_N - 1)
    uc.hook_add(UC_HOOK_MEM_WRITE, rec.mem('write'), begin=PAGE17, end=PAGE17 + PAGE17_N - 1)
    for r in ROWS:
        uc.hook_add(UC_HOOK_MEM_READ, rec.mem('read'), begin=DISP + DROW * r, end=DISP + DROW * (r + 1) - 1)
        uc.hook_add(UC_HOOK_MEM_WRITE, rec.mem('write'), begin=DISP + DROW * r, end=DISP + DROW * (r + 1) - 1)
    marks = {SINIT: 'static initializer 0x400c2c4e entry', int(mp['fxe_tick'], 16): 'fxe_tick entry'}

    def mark(uc_, addr, size, user):
        if marks[addr] not in rec.marks:
            rec.marks[marks[addr]] = rec.n()
            rec.events.append((rec.n(), 'mark', '0x%08x' % addr, marks[addr], 0, ''))
            print('[t53] %12d  %s' % (rec.n(), marks[addr]), flush=True)
    for a in marks:
        uc.hook_add(UC_HOOK_CODE, mark, begin=a, end=a)
    st = int(mp['fxe_state'], 16)

    def wstate(uc_, access, addr, size, value, user):
        rec.state_writes.append((rec.n(), value))
        rec.events.append((rec.n(), 'fxe_state', '', '0x%08x' % addr, size, '%d' % value))
        print('[t53] %12d  fxe_state <- %d' % (rec.n(), value), flush=True)
    uc.hook_add(UC_HOOK_MEM_WRITE, wstate, begin=st, end=st + 3)


def report(rec, out, extra):
    done = [n for n, v in rec.state_writes if v == 1]
    t_done = done[0] if done else None
    sinit = rec.marks.get('static initializer 0x400c2c4e entry')
    reads_between = [e for e in rec.events if e[1] == 'read' and (sinit is None or e[0] >= sinit)
                     and (t_done is None or e[0] < t_done)]
    slot_g_reads_before = [e for e in reads_between if e[3] == '0x%08x' % SLOT_G]
    res = dict(extra)
    res.update({
        'marks': rec.marks, 'fxe_state_writes': rec.state_writes,
        'events_first': rec.events[:400], 'events_total_recorded': len(rec.events),
        'by_pc': [{'kind': k, 'pc': pc, 'what': w, 'count': c} for (k, pc, w), c in sorted(rec.by_pc.items())],
        'reads_after_static_init_before_fxe_done': reads_between[:200],
        'n_reads_after_static_init_before_fxe_done': len(reads_between),
        'slot_g_reads_before_fxe_done': slot_g_reads_before,
    })
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    json.dump(res, open(out, 'w'), indent=1)
    print('[t53] marks %s; fxe_state writes %s' % (rec.marks, rec.state_writes))
    print('[t53] reads of page 17 / disp 0x72 / disp 0x7a after the static initializer and before fxe_state=1: %d'
          % len(reads_between))
    for e in reads_between[:20]:
        print('        n %s %s pc %s %s (%s)' % (e[0], e[1], e[2], where(int(e[3], 16)), e[5]))
    print('[t53] accesses by PC:')
    for (k, pc, w), c in sorted(rec.by_pc.items(), key=lambda kv: kv[0][1]):
        print('        %-5s %s %-28s x%d' % (k, pc, w, c))


def main():
    argv = sys.argv[1:]
    mode = argv.pop(0)
    out = opt(argv, '--json')
    mp = json.load(open(opt(argv, '--map', MAP)))
    if mode == 'reset':
        syx_path = opt(argv, '--syx')
        limit = int(float(opt(argv, '--limit', '4e8')))
        from emu import config, dspboot
        box = {}
        rec = Rec(lambda: box['st']['n'])

        def pre_start(m):
            install(m.uc, rec, mp)
        syx = config.firmware(syx_path)
        img = open(config.main_image(), 'rb').read()
        m, st, stop = dspboot.run(syx, img, limit=limit, machine_out=box, pre_start=pre_start)
        report(rec, out, {'mode': 'reset', 'syx': syx_path, 'limit': limit, 'stop': stop, 'n': st['n']})
        return 0
    if mode == 'resume':
        import guirun
        snap = argv[0]
        lim = argv[argv.index('--limit') + 1] if '--limit' in argv else None
        base = {'n': 0}
        try:
            from emu import snapshot
            base['n'] = 0
        except Exception:
            pass
        done = {'n': 0}
        rec = Rec(lambda: base['n'] + done['n'])
        orig_spin = guirun.spin

        def spin(*a, **k):
            r = orig_spin(*a, **k)
            done['n'] += r[1]
            return r
        guirun.spin = spin
        orig_build = guirun.build

        def build(*a, **k):
            r = orig_build(*a, **k)
            install(r[0].uc, rec, mp)
            return r
        guirun.build = build
        sys.argv = [sys.argv[0]] + argv
        try:
            rc = guirun.main()
        finally:
            report(rec, out, {'mode': 'resume', 'snapshot': snap, 'limit': lim,
                              'n_note': 'n = instructions run in this stage, counted per guirun chunk'})
        return rc
    raise SystemExit('reset | resume')


if __name__ == '__main__':
    sys.exit(main())
