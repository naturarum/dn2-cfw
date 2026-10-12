#!/usr/bin/env python3
"""T5.0: trace what the Digitone II 1.12 runs and writes while it composes a frame.

Runs digikit's emu.live headless (same arguments, --script etc.) and, while the frame
composition DRAWALL (0x4011d60a, called at 0x4002e4ca) is active and the trace is armed,
records:
  * function entries (a block at a function start from out/decomp/main_os/functions.tsv),
    with the distinct return addresses seen at entry;
  * memory writes grouped by writing PC: count, lowest and highest address.
Armed from the first frame after the script command `png arm` would be awkward, so arming is
by emulated time: T50_ARM=S arms after S emulated seconds (default 0), T50_DISARM=S stops.

    cd vendor/digikit && DT2_SECTIONS=out/sections/dn2-1.12 uv run python \\
       ../../scripts/t50_trace.py SNAP --syx STOCK.syx --speed 0 --script S --out DIR \\
       --trace-out DIR/trace.json

Read-only on digikit; never talks to hardware.
"""
import bisect
import collections
import json
import os
import struct
import sys

sys.path.insert(0, os.getcwd())                      # vendor/digikit
from emu import live                                 # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DRAWALL = 0x4011d60a
AFTER = 0x4002e4d0                                   # return address of the call at 0x4002e4ca
CODE_LO, CODE_HI = 0x40000400, 0x401d0800


def load_funcs():
    ents, names = [], {}
    with open(os.path.join(ROOT, 'out/decomp/main_os/functions.tsv')) as f:
        next(f)
        for line in f:
            c = line.split('\t')
            a = int(c[0], 16)
            ents.append(a)
            names[a] = c[1]
    ents.sort()
    return ents, names


def pop_opt(argv, name):
    if name in argv:
        i = argv.index(name)
        v = argv[i + 1]
        del argv[i:i + 2]
        return v
    return None


class Tracer(live.LiveEmulator):
    trace_out = None
    write_lo = 0
    write_hi = 0xffffffff

    def _setup(self):
        r = super()._setup()
        m = r[0]
        uc = m.uc
        from unicorn import UC_HOOK_CODE, UC_HOOK_BLOCK, UC_HOOK_MEM_WRITE
        from unicorn.m68k_const import UC_M68K_REG_A7, UC_M68K_REG_PC
        ents, names = load_funcs()
        entset = set(ents)
        arm = float(os.environ.get('T50_ARM', '0'))
        disarm = float(os.environ.get('T50_DISARM', '1e9'))
        st = {'active': 0, 'frames': 0, 'armed_frames': 0}
        hits = collections.Counter()
        callers = collections.defaultdict(collections.Counter)
        writes = {}
        self._t50 = (st, hits, callers, writes, names)
        emu = self

        def armed():
            t = emu.stats.get('emu_s', 0.0) if hasattr(emu, 'stats') else 0.0
            return arm <= t < disarm

        def on_draw(uc_, a, s, d):
            st['frames'] += 1
            if armed():
                st['active'] = 1
                st['armed_frames'] += 1

        def on_after(uc_, a, s, d):
            st['active'] = 0

        def on_block(uc_, a, s, d):
            if st['active'] and a in entset:
                hits[a] += 1
                sp = uc_.reg_read(UC_M68K_REG_A7)
                ret = struct.unpack('>I', bytes(uc_.mem_read(sp, 4)))[0]
                callers[a][ret] += 1

        def on_write(uc_, typ, addr, size, val, d):
            if st['active'] and emu_write_lo <= addr < emu_write_hi:
                pc = uc_.reg_read(UC_M68K_REG_PC)
                w = writes.get(pc)
                if w is None:
                    writes[pc] = [1, addr, addr + size]
                else:
                    w[0] += 1
                    if addr < w[1]:
                        w[1] = addr
                    if addr + size > w[2]:
                        w[2] = addr + size

        emu_write_lo, emu_write_hi = self.write_lo, self.write_hi
        calls = []
        self._t50_calls = calls

        def rd(a, n):
            try:
                return bytes(uc.mem_read(a, n))
            except Exception:
                return b''

        def cstr(a):
            b = rd(a, 64)
            return b.split(b'\0')[0].decode('latin-1') if b else None

        def mk(addr, nargs, kind):
            def cb(uc_, a, s, d):
                if not st['active']:
                    return
                sp = uc_.reg_read(UC_M68K_REG_A7)
                w = struct.unpack('>%dI' % (nargs + 1), rd(sp, 4 * (nargs + 1)))
                ent = {'fn': '0x%08x' % addr, 'kind': kind, 'ret': '0x%08x' % w[0],
                       'args': ['0x%x' % x for x in w[1:]], 'frame': st['armed_frames']}
                if kind == 'blit':                       # (dst, icon, x, y, centre)
                    ent['icon'] = ['0x%x' % x for x in struct.unpack('>7I', rd(w[2], 28))]
                elif kind == 'text':                     # (dst, font, x, y, mono, max, str)
                    ent['str'] = cstr(w[7])
                elif kind == 'textf':                    # (dst, fontwrap, x, y, flags, fmt, ...)
                    ent['str'] = cstr(w[6])
                elif kind == 'strip':                    # (vector*, value, range, mode)
                    b, e = struct.unpack('>2I', rd(w[1], 8))
                    ent['strip'] = ['0x%x' % b, (e - b) // 28]
                calls.append(ent)
            uc.hook_add(UC_HOOK_CODE, cb, begin=addr, end=addr)

        for spec in (os.environ.get('T50_CALLS') or '').split(','):
            if spec:
                a, n, k = spec.split(':')
                mk(int(a, 16), int(n), k)
        uc.hook_add(UC_HOOK_CODE, on_draw, begin=DRAWALL, end=DRAWALL)
        uc.hook_add(UC_HOOK_CODE, on_after, begin=AFTER, end=AFTER)
        uc.hook_add(UC_HOOK_BLOCK, on_block, begin=CODE_LO, end=CODE_HI)
        if os.environ.get('T50_WRITES', '1') == '1':
            uc.hook_add(UC_HOOK_MEM_WRITE, on_write)
        return r


def main():
    argv = sys.argv[1:]
    out = pop_opt(argv, '--trace-out')
    wr = pop_opt(argv, '--writes')
    if wr:
        lo, hi = wr.split('-')
        Tracer.write_lo, Tracer.write_hi = int(lo, 16), int(hi, 16)
    live.LiveEmulator = Tracer
    orig_summary = live.summary

    def summary(emu):
        st, hits, callers, writes, names = emu._t50
        ents = sorted(names)

        def fn(a):
            i = bisect.bisect_right(ents, a) - 1
            return ents[i] if i >= 0 else 0
        res = {
            'frames': st['frames'], 'armed_frames': st['armed_frames'],
            'functions': [
                {'entry': '0x%08x' % a, 'name': names[a], 'hits': n,
                 'callers': [{'ret': '0x%08x' % r, 'in': '0x%08x' % fn(r),
                              'in_name': names.get(fn(r), '?'), 'n': c}
                             for r, c in callers[a].most_common(12)]}
                for a, n in hits.most_common()],
            'writes': [
                {'pc': '0x%08x' % pc, 'in': '0x%08x' % fn(pc), 'in_name': names.get(fn(pc), '?'),
                 'n': w[0], 'lo': '0x%08x' % w[1], 'hi': '0x%08x' % w[2]}
                for pc, w in sorted(writes.items(), key=lambda kv: -kv[1][0])],
        }
        res['calls'] = emu._t50_calls
        if out:
            with open(out, 'w') as f:
                json.dump(res, f, indent=1)
            print('[t50] trace: %d frames (%d armed), %d functions, %d writing PCs -> %s'
                  % (st['frames'], st['armed_frames'], len(hits), len(writes), out), flush=True)
        return orig_summary(emu)

    live.summary = summary
    sys.argv = [sys.argv[0]] + argv
    return live.main()


if __name__ == '__main__':
    sys.exit(main())
