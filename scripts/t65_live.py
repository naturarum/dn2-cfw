#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""T6.5: emu.live (through scripts/t19_live.py) with the watches the table-growth proof needs.

    cd vendor/digikit && DT2_SECTIONS=... uv run python ../../scripts/t65_live.py SNAP --syx SYX \\
        --identify-as STOCK.syx --speed 1 --script S --out DIR [t19_live / emu.live options] \\
        [--stock-reads] [--cache-watch] [--flush-trace] [--state]

  --stock-reads   count every read of the stock descriptor table in the image (0x401f83a4,
                  0x141 x 0x3c), by PC. In a param-grow build there must be none: every reader was
                  pointed at the copy.
  --cache-watch   the ParameterUpdateCache: every write made by its own functions (constructor,
                  set, flush; 0x4018e93c.., 0x400f5264.., 0x400f5352..) must land inside the object
                  [ptr, ptr + size), size taken from the operand of its constructor's operator new
                  (0xfff4 stock, 0x13228 grown); every set/get must have lane < 17 and row < the
                  row count. Out-of-range writes and calls are listed.
  --flush-trace   every batch the cache's flush hands its listener: (lane, [(row, value)...]), and
                  every set (lane, row, value) — written to DIR/flush.json, for comparing two
                  builds' MIDI handling as multisets.
  --ob-trace      every call of 0x400d5a54, which sends a parameter change (the row's Overbridge id,
                  descriptor +0x20) to the Overbridge stream: the rows and ids, in t65.json
  --emmc          at the end: how much the OS wrote to the emulated eMMC (the +Drive)
  --state         at the end: the live FX setup of the current kit (k 0..100), the raw parameter
                  mirror (0x80003af0, 17 rows x 101 words), the TX frame (0x80005e60, 0xa80 bytes)
                  and the working image's kit records (FX record of kit 0..127) -> DIR/state.json

Every other argument is t19_live.py's / emu.live's. Read-only on digikit.
"""
import collections
import json
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.getcwd())                      # vendor/digikit
import t19_live                                      # noqa: E402
from emu import live                                 # noqa: E402

DESC0, ROWS0, ROW = 0x401f83a4, 0x141, 0x3c
CACHE_PTR = 0x44648988
CACHE_NEW_SIZE_AT = 0x4018e950           # operand of move.l #size,-(sp) (operator new)
CACHE_CODE = [(0x4018e93c, 0x4018ea10), (0x400f5264, 0x400f531a), (0x400f5352, 0x400f5564),
              (0x400f51a4, 0x400f5264)]
SET, GET = 0x400f5264, 0x400f51a4
OB_SEND = 0x400d5a54                     # a parameter change to the Overbridge stream (row's +0x20)
# (address, base, index, scale, displacement, size, what): the stores into the object
STORES = [
    (0x400f52c8, 'A0', 'D1', 1, 0, 2, 'set: value'),
    (0x400f52fa, 'A0', 'D1', 4, 0, 4, 'set: dirty word'),
    (0x400f5300, 'A2', None, 1, 4, 1, 'set: dirty flag'),
    (0x400f53aa, 'A2', 'D2', 1, 0, 4, 'flush: bank ^= 1'),
    (0x400f543c, 'A2', 'A0', 1, 4, 4, 'flush: vector end += 8'),
    (0x400f545a, 'A3', 'D2', 1, 0, 4, 'flush: dirty word cleared'),
    (0x400f553e, 'A2', 'D0', 1, 0, 4, 'flush: vector end = begin'),
    (0x400f5556, 'A2', None, 1, 4, 1, 'flush: dirty flag cleared'),
    (0x4018e98c, 'A2', 'D0', 1, 0, 4, 'constructor: bank'),
    (0x4018e996, 'A2', 'D1', 1, 0, 4, 'constructor: vector'),
    (0x4018e99a, 'A2', 'D1', 1, 4, 4, 'constructor: vector'),
    (0x4018e99e, 'A2', 'D1', 1, 8, 4, 'constructor: vector'),
]
FLUSH_CALL = 0x400f5518                  # jsr (a0): listener(obj, &vector, lane, 0)
KITPTR, FXOFF = 0x800052a0, 0x48e4
RAW, RAWN = 0x80003af0, 0xd8c
FRAME, FRAMEN = 0x80005e60, 0xa80
WORK, KITS, KSTRIDE, FXREC = 0x405ce96c, 0xae0200, 0x2a00, 0x16ac


def opt(argv, name):
    if name in argv:
        argv.remove(name)
        return True
    return False


class State:
    stock_reads = collections.Counter()
    cache = {'ptr': None, 'size': None, 'writes_in': 0, 'writes_out': [], 'bad_calls': [],
             'sets': 0, 'gets': 0}
    flushes = []
    sets = []
    emmc_log0 = None
    ob = []
    rows = ROWS0


def rd32(uc, a):
    return struct.unpack('>I', bytes(uc.mem_read(a, 4)))[0]


def install(m, args):
    from unicorn import UC_HOOK_MEM_READ, UC_HOOK_MEM_WRITE, UC_HOOK_CODE
    from unicorn.m68k_const import UC_M68K_REG_PC, UC_M68K_REG_A7, UC_M68K_REG_D3, UC_M68K_REG_A0
    uc = m.uc
    # the build's row count: the bound at 0x400174d6 (cmpi.l #rows,d2)
    State.rows = rd32(uc, 0x400174d8)
    print('[t65] rows 0x%x, table at 0x%08x' % (State.rows, rd32(uc, 0x400174e0)), flush=True)
    if 'ob-trace' in args:
        def ob_hook(uc_, addr, size_, user):
            sp = uc_.reg_read(UC_M68K_REG_A7)
            p1, p2, p3, row, p5 = struct.unpack('>IIIII', bytes(uc_.mem_read(sp + 4, 20)))
            r = row if row < State.rows else 0
            uid = struct.unpack('>I', bytes(uc_.mem_read(rd32(uc_, 0x400174e0) + 0x3c * r + 0x20, 4)))[0]
            State.ob.append((row, uid, p2 & 0xff, p3 & 0xffff, p5 & 0xffff))
        uc.hook_add(UC_HOOK_CODE, ob_hook, begin=OB_SEND, end=OB_SEND)
    if 'emmc' in args:
        import gc
        for e in [o for o in gc.get_objects() if type(o).__name__ == 'Esdhc']:
            State.emmc_log0 = len(e.log)
    if 'stock-reads' in args:
        def rhook(uc_, access, addr, size, value, user):
            State.stock_reads[uc_.reg_read(UC_M68K_REG_PC)] += 1
        uc.hook_add(UC_HOOK_MEM_READ, rhook, begin=DESC0, end=DESC0 + ROWS0 * ROW - 1)
    if 'cache-watch' in args or 'flush-trace' in args:
        size = rd32(uc, CACHE_NEW_SIZE_AT)
        State.cache['size'] = size
        State.cache['ptr'] = rd32(uc, CACHE_PTR)
        print('[t65] cache at 0x%08x, size 0x%x' % (State.cache['ptr'], size), flush=True)

        # every store the cache's own functions make into the object, with its effective
        # address computed from the registers (a code hook per instruction: cheap, and exact)
        from unicorn import m68k_const as R
        reg = lambda uc_, n: uc_.reg_read(getattr(R, 'UC_M68K_REG_' + n))   # noqa: E731

        def store(name, base, idx, scale, disp, size_):
            def h(uc_, addr, sz, user):
                p = State.cache['ptr'] or rd32(uc_, CACHE_PTR)
                ea = (reg(uc_, base) + (reg(uc_, idx) * scale if idx else 0) + disp) & 0xffffffff
                if p and p <= ea and ea + size_ <= p + State.cache['size']:
                    State.cache['writes_in'] += 1
                else:
                    State.cache['writes_out'].append((name, '0x%08x' % addr, '0x%08x' % ea, size_,
                                                      '0x%08x' % (p or 0)))
            return h
        for a_, base, idx, scale, disp, size_, name in STORES:
            uc.hook_add(UC_HOOK_CODE, store(name, base, idx, scale, disp, size_), begin=a_, end=a_)

        def call_hook(uc_, addr, size_, user):
            sp = uc_.reg_read(UC_M68K_REG_A7)
            obj, lane, row, val = struct.unpack('>IIII', bytes(uc_.mem_read(sp + 4, 16)))
            kind = 'set' if addr == SET else 'get'
            State.cache[kind + 's'] += 1
            if lane >= 17 or row >= State.rows:
                State.cache['bad_calls'].append((kind, lane, row))
            if kind == 'set' and 'flush-trace' in args:
                State.sets.append((lane, row, val & 0xffff))
        uc.hook_add(UC_HOOK_CODE, call_hook, begin=SET, end=SET)
        uc.hook_add(UC_HOOK_CODE, call_hook, begin=GET, end=GET)
    if 'flush-trace' in args:
        def fl_hook(uc_, addr, size_, user):
            sp = uc_.reg_read(UC_M68K_REG_A7)
            obj, vec, lane, zero = struct.unpack('>IIII', bytes(uc_.mem_read(sp, 16)))
            b, e = rd32(uc_, vec), rd32(uc_, vec + 4)
            raw = bytes(uc_.mem_read(b, e - b)) if e > b else b''
            items = [struct.unpack('>II', raw[i:i + 8]) for i in range(0, len(raw), 8)]
            State.flushes.append((lane, items))
        uc.hook_add(UC_HOOK_CODE, fl_hook, begin=FLUSH_CALL, end=FLUSH_CALL)


def state_of(uc):
    kit = rd32(uc, KITPTR)
    rd = lambda a, n: bytes(uc.mem_read(a, n))          # noqa: E731
    out = {'kit': '0x%08x' % kit,
           'fx_live': [struct.unpack('>H', rd(kit + FXOFF + 2 * k, 2))[0] for k in range(101)],
           'fx_routing': rd(kit + FXOFF + 0xca, 4).hex(),
           'raw_mirror': rd(RAW, RAWN).hex(), 'frame': rd(FRAME, FRAMEN).hex(),
           'kit_fx_records': [rd(WORK + KITS + KSTRIDE * i + FXREC, 0xa0).hex() for i in range(129)]}
    return out


class Watched(t19_live.Patched):
    args = set()

    def _setup(self):
        r = super()._setup()
        install(r[0], self.args)
        return r


def main():
    argv = sys.argv[1:]
    args = {a for a in ('stock-reads', 'cache-watch', 'flush-trace', 'state', 'emmc', 'ob-trace')
            if opt(argv, '--' + a)}
    Watched.args = args
    out = argv[argv.index('--out') + 1] if '--out' in argv else '.'
    t19_live.Patched = Watched
    orig = live.summary

    def summary(emu):
        uc = emu._m.uc
        rep = {'rows': State.rows}
        if 'stock-reads' in args:
            rep['stock_table_reads'] = {'total': sum(State.stock_reads.values()),
                                        'by_pc': {'0x%08x' % k: v for k, v in State.stock_reads.most_common()}}
            print('[t65] reads of the stock table 0x401f83a4: %d (%d PCs)' % (
                rep['stock_table_reads']['total'], len(State.stock_reads)), flush=True)
        if 'cache-watch' in args or 'flush-trace' in args:
            c = dict(State.cache)
            c['ptr_end'] = '0x%08x' % rd32(uc, CACHE_PTR)
            c['writes_out_n'] = len(c['writes_out'])
            c['writes_out'] = c['writes_out'][:50]
            rep['cache'] = c
            print('[t65] cache 0x%s size 0x%x: %d writes inside, %d outside, %d sets, %d gets, %d bad calls' % (
                c['ptr_end'][2:], c['size'], c['writes_in'], c['writes_out_n'], c['sets'], c['gets'],
                len(c['bad_calls'])), flush=True)
        if 'flush-trace' in args:
            json.dump({'flushes': State.flushes, 'sets': State.sets}, open(os.path.join(out, 'flush.json'), 'w'))
            print('[t65] %d flush batches, %d sets' % (len(State.flushes), len(State.sets)), flush=True)
        if 'state' in args:
            json.dump(state_of(uc), open(os.path.join(out, 'state.json'), 'w'))
        if 'ob-trace' in args:
            rep['overbridge_sends'] = State.ob
            ids = collections.Counter(u for _, u, _, _, _ in State.ob)
            print('[t65] Overbridge parameter sends: %d; rows past the stock ones: %d; ids %s' % (
                len(State.ob), sum(1 for r, _, _, _, _ in State.ob if r >= ROWS0),
                dict(sorted(ids.items())[:40])), flush=True)
        if 'emmc' in args:
            import gc
            cards = [o for o in gc.get_objects() if type(o).__name__ == 'Card' and hasattr(o, 'overlay')]
            for e in [o for o in gc.get_objects() if type(o).__name__ == 'Esdhc']:
                new = e.log[State.emmc_log0:] if State.emmc_log0 is not None else e.log
                rep['emmc_cmds'] = [(i, a_) for i, a_ in new][:5000]
                summ = collections.Counter(i for i, _ in new)
                print('[t65] eMMC commands this run: %d %s' % (len(new), dict(summ)), flush=True)
                print('[t65] eMMC first commands: %s' % ' '.join('CMD%d:%x' % c for c in new[:60]), flush=True)
            for c in cards:
                ov = c.overlay
                blocks = sorted({a // 512 for a in ov})
                rep['emmc'] = {'bytes_written': len(ov), 'blocks': len(blocks),
                               'first_block': blocks[0] if blocks else None,
                               'last_block': blocks[-1] if blocks else None}
                print('[t65] eMMC overlay: %d bytes in %d blocks (%s..%s)' % (
                    len(ov), len(blocks), blocks[0] if blocks else '-', blocks[-1] if blocks else '-'), flush=True)
        json.dump(rep, open(os.path.join(out, 't65.json'), 'w'), indent=1)
        return orig(emu)

    live.summary = summary
    sys.argv = [sys.argv[0]] + argv
    return t19_live.main()


if __name__ == '__main__':
    sys.exit(main())
