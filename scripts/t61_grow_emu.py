#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""T6.1: grow the DN2 OS 1.12 parameter descriptor table in digikit's emulator (hot-patch, RAM only)
and drive the UI headless, to compare with stock.

Nothing here is flashable: the patch is applied to the emulator's memory after a warm stock snapshot
is loaded, the way scripts/t19_live.py --hotpatch applies a build. The stock image is only read.

Variants (--variant):
  stock   no change (the reference run)
  inert   relocate the descriptor table (0x401f83a4 -> NEW_DESC) and the display table
          (0x4243425c -> NEW_DISP) with room for 8 more rows, repoint every reference, raise every
          row-count bound from 0x141 to 0x149 and re-run the boot-time builder of the slot and CC
          maps (FUN_400dc634). The 8 new rows are copies of row 0 ("Error", group -1): nothing
          may change on screen.
  grown   inert + 8 real rows 0x141..0x148 (our own names; FX groups, k 70..76, and one Swarmer
          row at k 33), placed on the chorus page E/F and the reverb page H; the stock rows
          "Delay FX Routing" (0x7a) and "Reverb FX Routing" (0x83) placed on the empty G slots
          of the delay and reverb pages, and row 0x7a turned into a 0..6 mode knob by data.
  pathc   no growth: only the page-record and descriptor-data part of `grown` for rows 0x7a and
          0x83 (the cheapest path to one more knob on the delay and reverb pages).
  grown-cc  grown + MIDI CC 3 on the FX channel for row 0x141 (exercises the CC-in path and the
          ParameterUpdateCache, which is sized for 0x141 rows).

Usage (from vendor/digikit, like scripts/t50-emu.sh):
  DT2_SECTIONS=out/sections/dn2-1.12 uv run python ../../scripts/t61_grow_emu.py \\
      ../../out/T2.4a/warm/warm800M.snap --syx ../../elektron-firmware/Digitone_II_OS1_12.syx \\
      --variant grown --speed 1 --script ../../scripts/t61-pages.script --out ../../out/T6.1/emu/grown \\
      --report ../../out/T6.1/emu/grown/report.json --save ../../out/T6.1/emu/grown/end.snap \\
      --midi-log ../../out/T6.1/emu/grown/midi.txt
"""
import json
import os
import re
import struct
import sys
import time

sys.path.insert(0, os.getcwd())                      # vendor/digikit
from emu import live                                 # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, '..')
STOCK_MAIN = os.path.join(ROOT, 'ele3_out', '02_type3.unpacked.bin')
DISASM = os.path.join(ROOT, 'out', 'decomp', 'main_os', 'disasm')
LOAD = 0x40000400

DESC, ROWS, DROW = 0x401f83a4, 0x141, 0x3c
DESC_END = DESC + ROWS * DROW
DISP, XROW = 0x4243425c, 0x44
DISP_ACC_ADDI = 0x400c2598           # addi.l #0x4243425c,D0 in the display accessor 0x400c257c
NEW = 8
NROWS = ROWS + NEW
NEW_DESC = 0x47E40000                # our core's DDR (notes/T1.9): unused by stock
NEW_DISP = 0x47E46000
NEW_STR = 0x47E4C000
BUILDER = 0x400dc634                 # slot tables and CC/NRPN maps, built once at boot
PAGES, PAGE = 0x42433c00, 0x2c
# compares against 0x140/0x141 that are not the row count (notes/T6.1 section 2)
NOT_BOUNDS = {0x4006ef0a: 'row 0x13f identity (FilterPageView)',
              0x4006ef14: 'row 0x140 identity (FilterPageView)',
              0x40031fee: 'KitActive loop 16 x 0x14',
              0x40032422: 'KitActive loop 16 x 0x14',
              0x400325e2: 'KitActive loop 16 x 0x14'}
KITPTR, FXOFF, RAW16 = 0x800052a0, 0x48e4, 0x800047b2
CACHE_PTR = 0x44648988               # shared_ptr<ParameterUpdateCache>, built lazily by 0x4018e93c

FMT_NUM = 0x400e3030                 # plain number
FMT_WAVE = 0x400e3610                # LFO waveform names (TRI SINE ..)
EMPTY = 0x40218a2a                   # the empty unit-suffix string every row points at

# (row, group, k, max, default, long, short, fmt, mask, display template row, group-name row)
NEW_ROWS = [
    (0x141, 0x10, 70, 6, 0, 'LFO Shape', 'SHPE', FMT_WAVE, 0, 0x63, 0x69),
    (0x142, 0x10, 71, 127, 0, 'LFO Phase', 'PHAS', FMT_NUM, 0, 0x69, 0x69),
    (0x143, 0x11, 72, 127, 64, 'Tone', 'TONE', FMT_NUM, 0, 0x7d, 0x7b),
    (0x144, 0x12, 73, 127, 0, 'Drive', 'DRIV', FMT_NUM, 0, 0x75, 0x71),
    (0x145, 0x12, 74, 127, 0, 'Mod Depth', 'MDEP', FMT_NUM, 0, 0x75, 0x71),
    (0x146, 0x14, 75, 127, 64, 'Low Band', 'LOW', FMT_NUM, 0, 0x96, 0x96),
    (0x147, 0x14, 76, 127, 64, 'Mid Band', 'MID', FMT_NUM, 0, 0x96, 0x96),
    (0x148, 0x03, 33, 127, 0, 'Swarm Test', 'SWT', FMT_NUM, 0x1e00, 0x126, 0x126),
]
# page slots: (page id, slot 0..7 = A..H) -> row
GROWN_SLOTS = {(17, 6): 0x7a, (18, 6): 0x83, (18, 7): 0x143, (19, 4): 0x141, (19, 5): 0x142}
PATHC_SLOTS = {(17, 6): 0x7a, (18, 6): 0x83}


def u32(b, o):
    return struct.unpack('>I', b[o:o + 4])[0]


def instructions():
    """addr -> (bytes, text) for every instruction in the decompile dump's disassembly."""
    ins = {}
    for fn in os.listdir(DISASM):
        for line in open(os.path.join(DISASM, fn)):
            m = re.match(r'^([0-9a-f]{8})  ((?:[0-9a-f]{4} )+)\s*(.*)$', line.rstrip('\n'))
            if m:
                ins[int(m.group(1), 16)] = (bytes.fromhex(m.group(2).replace(' ', '')), m.group(3).strip())
    return ins


def plan_code_patches(img):
    """-> list of (addr, old bytes, new bytes, why): the operand repoints and bound raises."""
    ins = instructions()
    out = []
    for a, (bs, text) in sorted(ins.items()):
        if not (0x40000400 <= a < 0x402F7D40):
            continue
        assert img[a - LOAD:a - LOAD + len(bs)] == bs, hex(a)
        nb = bytearray(bs)
        why = []
        for o in range(2, len(bs) - 3, 2):
            v = u32(bs, o)
            if DESC <= v < DESC_END:                       # into the table (not the next table)
                nb[o:o + 4] = struct.pack('>I', NEW_DESC + v - DESC)
                why.append('desc 0x%08x -> 0x%08x' % (v, NEW_DESC + v - DESC))
            elif a == DISP_ACC_ADDI and v == DISP:
                nb[o:o + 4] = struct.pack('>I', NEW_DISP)
                why.append('disp base -> 0x%08x' % NEW_DISP)
        m = re.match(r'(cmpi|cmpa)\.l #0x(14[01]),', text)
        if m and a not in NOT_BOUNDS:
            imm = int(m.group(2), 16)
            assert u32(bs, 2) == imm, (hex(a), text)
            nb[2:6] = struct.pack('>I', imm + NEW)
            why.append('bound #0x%x -> #0x%x' % (imm, imm + NEW))
        if why:
            out.append((a, bs, bytes(nb), '; '.join(why) + ' | ' + text))
    return out


class Strings:
    def __init__(self, base):
        self.base, self.buf, self.at = base, bytearray(), {}

    def add(self, s):
        if s not in self.at:
            self.at[s] = self.base + len(self.buf)
            self.buf += s.encode() + b'\0'
            while len(self.buf) % 2:
                self.buf += b'\0'
        return self.at[s]


def build_tables(img, uc, variant):
    """-> (descriptor table bytes for NROWS rows, display table bytes, strings, log)."""
    desc = bytearray(img[DESC - LOAD:DESC_END - LOAD]) + bytearray(img[DESC - LOAD:DESC - LOAD + DROW]) * NEW
    disp = bytearray(bytes(uc.mem_read(DISP, ROWS * XROW))) + bytearray(bytes(uc.mem_read(DISP, XROW))) * NEW
    strs = Strings(NEW_STR)
    log = []
    if variant in ('grown', 'grown-cc'):
        for (row, grp, k, mx, df, ln, sn, fmt, mask, tmpl, gname) in NEW_ROWS:
            r = bytearray(DROW)
            struct.pack_into('>iiiii', r, 0, grp, k, 0, mx << 8, df << 8)
            struct.pack_into('>I', r, 0x14, 0)                       # not bipolar
            struct.pack_into('>hh', r, 0x18, -1, -1)                 # no CC, no CC2
            struct.pack_into('>i', r, 0x1c, -1)                      # no NRPN
            struct.pack_into('>i', r, 0x20, 0x400 + row - ROWS)      # UID outside stock's 0..387
            struct.pack_into('>I', r, 0x24, mask)
            struct.pack_into('>I', r, 0x28, strs.add(ln))
            struct.pack_into('>I', r, 0x2c, u32(img, gname * DROW + DESC - LOAD + 0x2c))
            struct.pack_into('>I', r, 0x30, strs.add(sn))
            struct.pack_into('>II', r, 0x34, fmt, EMPTY)
            desc[row * DROW:(row + 1) * DROW] = r
            disp[row * XROW:(row + 1) * XROW] = disp[tmpl * XROW:(tmpl + 1) * XROW]
            log.append('row 0x%x %s/%s group 0x%x k %d 0..%d glyph from row 0x%x' % (row, ln, sn, grp, k, mx, tmpl))
            # no randomiser: FUN_4004b0b4 (sound RANDOM) writes sound[k] for every row that has one
            disp[row * XROW + 0x24:row * XROW + 0x34] = bytes(0x10)
            if 0x10 <= grp <= 0x15 and not os.environ.get('T61_LOCKABLE_FX'):
                # p-lockable only when display flags bits 0 and 3 are clear (0x40064ccc); a send-FX
                # row must not be: its lock would land on the track's sound slot with the same k
                f = struct.unpack('>I', disp[row * XROW:row * XROW + 4])[0]
                struct.pack_into('>I', disp, row * XROW, f | 8)
        if variant == 'grown-cc':
            struct.pack_into(">h", desc, 0x141 * DROW + 0x18, 3)
            log.append('row 0x141 gets CC 3 (free on the FX channel; 33 is the mod-wheel LSB, taken by the CC parser)')
    if variant in ('grown', 'grown-cc', 'pathc'):
        # row 0x7a "Delay FX Routing" (k 40, 0..1, Pre/Post) becomes a 0..6 mode knob, by data alone
        o = 0x7a * DROW
        struct.pack_into('>ii', desc, o + 0x0c, 6 << 8, 1 << 8)
        struct.pack_into('>I', desc, o + 0x34, FMT_NUM)
        struct.pack_into('>I', desc, o + 0x28, strs.add('Delay Mode'))
        struct.pack_into('>I', desc, o + 0x30, strs.add('MODE'))
        log.append('row 0x7a: max 6, default 1, plain-number formatter, Delay Mode/MODE')
    return desc, disp, strs, log


def apply(m, variant):
    uc = m.uc
    img = open(STOCK_MAIN, 'rb').read()
    rep = {'variant': variant}
    if variant == 'stock':
        return rep
    desc, disp, strs, log = build_tables(img, uc, variant)
    rep['rows'] = log
    for a in range(NEW_DESC & ~0xfffff, NEW_STR + 0x1000, 0x1000):
        m.ensure(a)
    uc.mem_write(NEW_STR, bytes(strs.buf))
    if variant == 'pathc':
        # in place: the stock table, edited where the image lies (the emulator runs the image from RAM)
        uc.mem_write(DESC + 0x7a * DROW, bytes(desc[0x7a * DROW:0x7b * DROW]))
    else:
        uc.mem_write(NEW_DESC, bytes(desc))
        uc.mem_write(NEW_DISP, bytes(disp))
        patches = plan_code_patches(img)
        for a, old, new, why in patches:
            uc.mem_write(a, new)
        rep['code_patches'] = ['0x%08x %s -> %s  %s' % (a, o.hex(), n.hex(), w) for a, o, n, w in patches]
        rep['n_repoints'] = sum(w.count('desc 0x') + w.count('disp base') for _, _, _, w in patches)
        rep['n_bounds'] = sum(w.count('bound #') for _, _, _, w in patches)
        try:
            uc.ctl_remove_cache(LOAD, LOAD + len(img))
        except Exception as e:                                  # older binding
            print('[t61] ctl_remove_cache: %s' % e)
        rep['builder'] = rerun_builder(m)
    slots = GROWN_SLOTS if variant in ('grown', 'grown-cc') else PATHC_SLOTS if variant == 'pathc' else {}
    for (pid, slot), row in sorted(slots.items()):
        a = PAGES + PAGE * pid + 8 + 4 * slot
        old = struct.unpack('>I', bytes(uc.mem_read(a, 4)))[0]
        assert old == 0, (pid, slot, old)
        uc.mem_write(a, struct.pack('>I', row))
    rep['page_slots'] = {'%d%s' % (p, 'ABCDEFGH'[s]): '0x%x' % r for (p, s), r in sorted(slots.items())}
    print('[t61] %s: %s' % (variant, json.dumps({k: v for k, v in rep.items() if k != 'code_patches'})), flush=True)
    return rep


def rerun_builder(m):
    """Call FUN_400dc634 from the host (it zeroes and refills the slot tables and the CC/NRPN maps)
    and read back the FX/sound/machine slot tables. The CPU state is saved and restored around it."""
    from unicorn import m68k_const as R
    uc = m.uc
    regs = [getattr(R, 'UC_M68K_REG_%s%d' % (k, i)) for k in 'DA' for i in range(8)] + \
        [R.UC_M68K_REG_PC, R.UC_M68K_REG_SR]
    saved = [uc.reg_read(r) for r in regs]
    # poison what the builder must rewrite, so a builder that did not run shows
    uc.mem_write(0x42c657a8, bytes(4))
    uc.mem_write(0x42c659a8, b'\xff' * 0x194)
    t0 = time.monotonic()
    # harness.call without its timers check: this runs inside build(), before emu.live claims them
    stack_top = 0x10000000 + 0x1000 - 0x100
    m.ensure(0x10000000)
    uc.mem_write(stack_top, struct.pack('>I', 0xDEADBEE0))
    uc.reg_write(R.UC_M68K_REG_SR, 0x2700)
    uc.reg_write(R.UC_M68K_REG_A7, stack_top)
    uc.emu_start(BUILDER, 0xDEADBEE0, count=5_000_000)
    pc_end = uc.reg_read(R.UC_M68K_REG_PC)
    # SR first (it selects which stack pointer A7 names), then the rest. The snapshot's SR is
    # checked to be supervisor, so A7 is the SSP that call() moved, and nothing else is lost.
    assert saved[-1] & 0x2000, 'snapshot stopped in user mode: SSP not restorable here'
    uc.reg_write(R.UC_M68K_REG_SR, saved[-1])
    for r, v in zip(regs[:-1], saved[:-1]):
        uc.reg_write(r, v)
    rd = lambda a: struct.unpack('>i', bytes(uc.mem_read(a, 4)))[0]   # noqa: E731
    fx = {k: '0x%x' % rd(0x42c659a8 + 4 * k) for k in range(101) if rd(0x42c659a8 + 4 * k)}
    mach3 = {25 + i: '0x%x' % rd(0x42c65d18 + 4 * (3 * 40 + i)) for i in range(40) if rd(0x42c65d18 + 4 * (3 * 40 + i))}
    return {'built_flag': rd(0x42c657a8), 'returned': pc_end == 0xDEADBEE0, 'host_s': round(time.monotonic() - t0, 3),
            'fx_slots_70_100': {k: v for k, v in fx.items() if k >= 70},
            'n_fx_slots': len(fx), 'machine3': mach3}


def readout(uc):
    rd16 = lambda a: struct.unpack('>H', bytes(uc.mem_read(a, 2)))[0]   # noqa: E731
    kit = struct.unpack('>I', bytes(uc.mem_read(KITPTR, 4)))[0]
    ks = [25, 26, 40, 48] + list(range(70, 78))
    rd32 = lambda a: struct.unpack('>I', bytes(uc.mem_read(a, 4)))[0]   # noqa: E731
    out = {'kit': '0x%08x' % kit,
           'fx_live': {k: rd16(kit + FXOFF + 2 * k) for k in ks},
           'raw_mirror_row16': {k: rd16(RAW16 + 2 * k) for k in ks}}
    c = rd32(CACHE_PTR)
    if c:
        # ParameterUpdateCache (0x4018e93c): +0x18 u16 values [2 banks][17 lanes][0x141 rows],
        # +0x555c dirty bits [2][17][0x504 bytes], +0xffe4 current bank
        out['cache'] = {'bank': rd32(c + 0xffe4),
                        'lane16_row141_bank0': '0x%04x' % rd16(c + 0x18 + 16 * 0x282 + 0x141 * 2),
                        'lane16_row141_bank1': '0x%04x' % rd16(c + 0x18 + 0x2aa2 + 16 * 0x282 + 0x141 * 2),
                        'bank0_lane0_dirty_word0': '0x%08x' % rd32(c + 0x555c),
                        'bank1_lane0_dirty_word0': '0x%08x' % rd32(c + 0x555c + 0x5544),
                        'bank1_lane0_row0_value': '0x%04x' % rd16(c + 0x18 + 0x2aa2)}
    return out


class Grown:
    variant = 'stock'
    report = {}


def patched_build(orig):
    """emu.live's build(), then the patch: applied to the machine before emu.live installs its
    run-time hooks (with them in place a host call returns at once)."""
    def build(*a, **k):
        r = orig(*a, **k)
        Grown.report = apply(r[0], Grown.variant)
        Grown.report['before'] = readout(r[0].uc)
        if os.environ.get('T61_TRACE'):
            add_traces(r[0].uc, os.environ['T61_TRACE'])
        return r
    return build


def add_traces(uc, spec):
    """T61_TRACE=ADDR[,ADDR..]: at each address print d0-d7, a0 and the first 7 stack longs."""
    from unicorn import UC_HOOK_CODE
    from unicorn import m68k_const as R
    for a in [int(x, 16) for x in spec.split(',')]:
        def cb(uc_, addr, size, data):
            sp = uc_.reg_read(R.UC_M68K_REG_A7)
            st = bytes(uc_.mem_read(sp, 28))
            print('[trace] 0x%08x d0-d7 %s a0 %x  sp: %s' % (
                addr, ' '.join('%x' % uc_.reg_read(getattr(R, 'UC_M68K_REG_D%d' % i)) for i in range(8)),
                uc_.reg_read(R.UC_M68K_REG_A0), ' '.join(st[i:i + 4].hex() for i in range(0, 28, 4))), flush=True)
        uc.hook_add(UC_HOOK_CODE, cb, begin=a, end=a)


def main():
    argv = sys.argv[1:]
    v = 'stock'
    if '--variant' in argv:
        i = argv.index('--variant')
        v = argv[i + 1]
        del argv[i:i + 2]
    assert v in ('stock', 'inert', 'grown', 'pathc', 'grown-cc'), v
    Grown.variant = v
    live.build = patched_build(live.build)
    orig_summary = live.summary
    out = argv[argv.index('--out') + 1] if '--out' in argv else '.'

    def summary(emu):
        Grown.report['after'] = readout(emu._m.uc)
        with open(os.path.join(out, 't61.json'), 'w') as f:
            json.dump(Grown.report, f, indent=1)
        print('[t61] before %s' % json.dumps(Grown.report['before']))
        print('[t61] after  %s' % json.dumps(Grown.report['after']), flush=True)
        return orig_summary(emu)
    live.summary = summary
    sys.argv = [sys.argv[0]] + argv
    return live.main()


if __name__ == '__main__':
    t0 = time.monotonic()
    rc = main()
    print('[t61] wall %.1f s' % (time.monotonic() - t0))
    sys.exit(rc)
