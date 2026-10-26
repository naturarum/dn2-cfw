#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""spngcheck.py - SPNG, the spring reverb (T5.5, fx/spring.c): the selcc build against the Mac build, its cost in
instructions per 32-sample block, its code size and stack, in the T3.6 SHARC interpreter.

    python3 tools/sharcvm/tests/spngcheck.py [--seconds 2] [--workers 4] [--out out/T5.5/spngcheck.json]

As fxcheck.py: harness/spngdrv.c and fx/spring.c built with selache's selcc -O1 for the ADSP-21569 (run here) and
with clang -O2 -ffp-contract=off -fno-strict-aliasing as a native library; both get the same calls (init, knob
settings, snap, then the test signal in 32-sample blocks, knob moves between blocks) and their outputs are compared
sample by sample, and at the end the whole Spring state byte for byte (its layout is the same on both: every field
32 bits). Cases:
  - default: the init patch;
  - extremes: each knob at 0 and at 127 alone (16);
  - grid: DEC, DRIP, TENS, PRE at 0, 64, 127 each (81), with DWEL 127, TONE 0 and WDTH 127 on every third;
  - moving: every knob ramping every block (worst: the coefficients worked out every block), and each knob jumping
    0 <-> 127 every 10 blocks in turn, and random jumps of every knob every 7 blocks;
  - fifo: the same signal in calls of 1..100 samples (the FIFO path) against 32-sample calls, in the selcc build.
Code size: spring.s alone, every function and the constant data (sc01_funcmap, as DT01 measured). Stack: the
harness stack filled with a pattern, the deepest word written. Results in --out (JSON).
"""
import argparse
import ctypes as C
import json
import math
import random
import struct
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(ROOT / "tools/dspsplice"))
sys.dont_write_bytecode = True
from vm import VM  # noqa: E402
from render_fx import test_signal  # noqa: E402
import memguard  # noqa: E402

OUT = ROOT / "out/T5.5/spngcheck"
SEL = ROOT / "vendor/selache/target/release"
STATE = 0x80100000      # DDR (the stock reverb's state is in DDR too, at 0x80000018)
CTX = 0x200a0000        # L2 scratch
BUF = 0x200a1000
NPAR = 8                # the internal knobs; MODE (2026-10-24) is index 8
NAMES = ["PRE", "DEC", "DRIP", "DWEL", "HPF", "TONE", "WDTH", "TENS", "MODE"]
MODE = 8
PATTERN = 0xA5A5A5A5


class NativeCtx(C.Structure):
    _fields_ = [("cmd", C.c_int32), ("state", C.c_void_p), ("inL", C.c_void_p), ("inR", C.c_void_p),
                ("outL", C.c_void_p), ("outR", C.c_void_p), ("n", C.c_int32), ("param", C.c_int32),
                ("value", C.c_float)]


def build():
    OUT.mkdir(parents=True, exist_ok=True)
    inc = [f"-I{ROOT / 'fx'}", f"-I{ROOT / 'vendor/selache/libsel/include'}"]
    flags = ["-proc", "ADSP-21569", "-char-size-8", "-O1", *inc]
    objs = []
    for name, path, extra in (("spngdrv", HERE.parent / "harness/spngdrv.c", ["-D__SHARC_HARNESS__"]),
                              ("spring", ROOT / "fx/spring.c", [])):
        subprocess.check_call([str(SEL / "selcc"), *flags, *extra, "-S", "-o", str(OUT / f"{name}.s"), str(path)])
        subprocess.check_call([str(SEL / "selas"), "-proc", "ADSP-21569", "-o", str(OUT / f"{name}.doj"),
                               str(OUT / f"{name}.s")])
        objs.append(str(OUT / f"{name}.doj"))
    subprocess.check_call([str(SEL / "seld"), "-proc", "ADSP-21569", "-T", str(ROOT / "vendor/selache/libsel/link.ldf"),
                           "-o", str(OUT / "spngdrv.dxe"), *objs, str(ROOT / "out/T3.6/libsel/libsel.dlb")])
    subprocess.check_call(["clang", "-O2", "-ffp-contract=off", "-fno-strict-aliasing", "-shared", "-fPIC",
                           f"-I{ROOT / 'fx'}", "-o", str(OUT / "libspngnative.dylib"),
                           str(HERE.parent / "harness/spngdrv.c"), str(ROOT / "fx/spring.c")])


def code_size():
    """bytes of every function in spring.s (linked alone, as sc01_funcmap does for a recipe section) and of its
    constant data; the immediate-count shifts in it (selas mis-encodes negative ones: must be 0)"""
    import sc01_funcmap as FM
    import elf as E
    rows, sec = FM.funcmap(OUT / "funcmap", 0x2c0000, [OUT / "spring.s"])
    funcs = sorted(({"function": n, "bytes": 2 * (b - a)} for n, f, a, b in rows), key=lambda r: -r["bytes"])
    e = E.read_elf(OUT / "funcmap" / "f.dxe")
    data = sum(len(s.data) for s in e.sections if s.kind == "data")
    text = (OUT / "spring.s").read_text()
    import re
    shifts = len(re.findall(r"shift [^;]* by -?(0x)?[0-9]", text, re.I))
    return {"code_bytes": len(sec.data), "functions": funcs,
            "const_data_bytes": data, "immediate_shifts": shifts}


class Native:
    def __init__(self):
        self.lib = C.CDLL(str(OUT / "libspngnative.dylib"))
        self.state = (C.c_uint8 * (1 << 20))()
        self.inL, self.inR = (C.c_float * 128)(), (C.c_float * 128)()
        self.outL, self.outR = (C.c_float * 128)(), (C.c_float * 128)()

    def cmd(self, cmd, n=0, param=0, value=0.0):
        c = NativeCtx(cmd, C.addressof(self.state), C.addressof(self.inL), C.addressof(self.inR),
                      C.addressof(self.outL), C.addressof(self.outR), n, param, value)
        self.lib.spngdrv(C.byref(c))

    def block(self, L, R):
        n = len(L)
        self.inL[:n] = L
        self.inR[:n] = R
        self.cmd(3, n=n)
        return list(self.outL[:n]), list(self.outR[:n])

    def state_bytes(self, size):
        return bytes(self.state[:size])


class Sharc:
    def __init__(self):
        self.vm = VM()
        e = self.vm.load_elf(OUT / "spngdrv.dxe")
        self.entry = e.symbols["spngdrv."]
        self.stack_base = e.symbols["ldf_stack_space"]
        self.stack_top = e.symbols["ldf_stack_space"] + e.symbols["ldf_stack_length"]
        self.vm.c_runtime_state(stack_base=self.stack_base, stack_top=self.stack_top,
                                l7=e.symbols["ldf_stack_length"], mode1=0x01010800)
        # the stack below the entry filled with a pattern: the deepest word changed is the stack used
        self.fill = 8192
        self.vm.write(self.stack_top - self.fill, struct.pack("<I", PATTERN) * (self.fill // 4))
        self.per_block = []

    def cmd(self, cmd, n=0, param=0, value=0.0):
        vm = self.vm
        vm.write(CTX, struct.pack("<iIIIIIiif", cmd, STATE, BUF, BUF + 0x200, BUF + 0x400, BUF + 0x600, n, param, value))
        n0 = vm.icount
        vm.call(self.entry, R4=CTX)
        if cmd == 3:
            self.per_block.append(vm.icount - n0)

    def block(self, L, R):
        n = len(L)
        self.vm.wrf(BUF, L)
        self.vm.wrf(BUF + 0x200, R)
        self.cmd(3, n=n)
        return self.vm.rdf(BUF + 0x400, n), self.vm.rdf(BUF + 0x600, n)

    def stack_used(self):
        b = self.vm.read(self.stack_top - self.fill, self.fill)
        words = struct.unpack("<%dI" % (self.fill // 4), b)
        for i, w in enumerate(words):
            if w != PATTERN:
                return self.fill - 4 * i
        return 0

    def state_bytes(self, size):
        return self.vm.read(STATE, size)


def sizeof_spring():
    """sizeof(Spring) from the Mac build (the same layout on the SHARC: every field 32 bits), and the byte range of
    the FIFO (ib, ob, pos: what the FIFO path leaves and the whole-block path does not touch)"""
    src = OUT / "sz.c"
    src.write_text('#include <stdio.h>\n#include <stddef.h>\n#include "spring.h"\nint main(void){printf("%lu %lu %lu\\n",'
                   '(unsigned long)sizeof(Spring),(unsigned long)offsetof(Spring,ib),(unsigned long)offsetof(Spring,pos)+4);return 0;}\n')
    subprocess.check_call(["clang", "-O0", f"-I{ROOT / 'fx'}", "-o", str(OUT / "sz"), str(src)])
    return [int(x) for x in subprocess.check_output([str(OUT / "sz")]).decode().split()]


def run_case(case):
    """case: name, knobs {i: v}, events {block: [(i, v)]}, seconds, cut ('32' or 'rand') -> results"""
    L, R = test_signal(case["seconds"])
    n = len(L) // 32 * 32
    size, f0, f1 = case["size"]
    outs = []
    sh = None
    impls = [Native(), Sharc()] if case.get("cut", "32") == "32" else [Sharc(), Sharc()]
    for k, impl in enumerate(impls):
        impl.cmd(0)
        for p, v in case["knobs"].items():
            impl.cmd(1, param=int(p), value=float(v))
        impl.cmd(2)
        oL, oR = [], []
        rnd = random.Random(7)
        ev = {int(b): e for b, e in case.get("events", {}).items()}
        fifo = case.get("cut") == "rand" and k == 1
        evpos = sorted(32 * b for b in ev)
        i = 0
        while i < n:
            if i % 32 == 0:
                for p, v in ev.get(i // 32, []):
                    impl.cmd(1, param=p, value=v)
            m = 32
            if fifo:
                # random calls, but one ends where a knob changes (the change lands on the same block in both)
                nxt = next((q for q in evpos if q > i), n)
                m = min(1 + int(rnd.random() * 100), nxt - i, n - i)
            x, y = impl.block(L[i:i + m], R[i:i + m])
            oL += x
            oR += y
            i += m
        st = impl.state_bytes(size)
        if case.get("cut") == "rand":
            st = st[:f0] + bytes(f1 - f0) + st[f1:]        # the FIFO: only the cut-up run fills it
        outs.append((oL, oR, st))
        if isinstance(impl, Sharc):
            sh = impl
    (aL, aR, aS), (bL, bR, bS) = outs
    f2u = lambda x: struct.unpack("<I", struct.pack("<f", x))[0]   # noqa: E731
    same = sum(1 for p, q in zip(aL + aR, bL + bR) if f2u(p) == f2u(q))
    diff = [abs(p - q) for p, q in zip(aL + aR, bL + bR)]
    peak = max(max(map(abs, aL)), max(map(abs, aR)), 1e-30)
    md = max(diff)
    bad = sum(1 for v in bL + bR if not math.isfinite(v))
    pb = sh.per_block if case.get("cut", "32") == "32" else []
    memguard.check()
    return {"name": case["name"], "knobs": case["knobs"], "samples": 2 * n, "bit_identical": same,
            "max_diff_db_re_peak": (20 * math.log10(md / peak) if md else None), "peak": peak,
            "state_identical": aS == bS, "nonfinite": bad,
            "insns_mean": (sum(pb) / len(pb)) if pb else None, "insns_max": max(pb) if pb else None,
            "insns_min": min(pb) if pb else None, "stack_bytes": sh.stack_used()}


def cases(seconds):
    out = [{"name": "default", "knobs": {}}]
    for p in range(NPAR):
        for v in (0, 127):
            out.append({"name": f"{NAMES[p]} {v}", "knobs": {p: v}})
    k = 0
    for dec in (0, 64, 127):
        for drip in (0, 64, 127):
            for tens in (0, 64, 127):
                for pre in (0, 64, 127):
                    kn = {1: dec, 2: drip, 7: tens, 0: pre}
                    if k % 3 == 0:
                        kn.update({3: 127, 5: 0, 6: 127})
                    out.append({"name": f"grid DEC {dec} DRIP {drip} TENS {tens} PRE {pre}" + (" DWEL 127 TONE 0 WDTH 127" if k % 3 == 0 else ""),
                                "knobs": kn})
                    k += 1
    nb = int(seconds * 48000) // 32
    # every knob ramping every block (each a different speed, through the whole range)
    ev = {}
    for b in range(1, nb):
        ev[b] = [(p, (b * (3 + 2 * p)) % 128 * 1.0) for p in range(NPAR)]
    out.append({"name": "moving: every knob ramping every block", "knobs": {}, "events": ev})
    ev = {}
    for b in range(1, nb):
        if b % 10 == 0:
            p = (b // 10) % NPAR
            ev[b] = [(p, 127.0 if (b // 80) % 2 == 0 else 0.0)]
    out.append({"name": "moving: one knob jumping 0 <-> 127 every 10 blocks, in turn", "knobs": {1: 100}, "events": ev})
    rnd = random.Random(3)
    ev = {}
    for b in range(1, nb):
        if b % 7 == 0:
            ev[b] = [(p, rnd.choice([0.0, 127.0, rnd.random() * 127])) for p in range(NPAR)]
    out.append({"name": "moving: every knob jumping at random every 7 blocks", "knobs": {}, "events": ev})
    # MODE: stills, stills with the other knobs at their ends, MODE ramping every block, MODE and the others jumping
    for m in (0, 24, 48, 64, 80, 104, 127):
        out.append({"name": f"mode {m}", "knobs": {MODE: m}})
    for m in (0, 85, 127):
        out.append({"name": f"mode {m}, DEC DWEL HPF TONE at the ends", "knobs": {MODE: m, 1: 127, 3: 127, 4: 127, 5: 0}})
        out.append({"name": f"mode {m}, DEC DWEL HPF TONE at the other ends", "knobs": {MODE: m, 1: 0, 3: 0, 4: 0, 5: 127}})
    ev = {b: [(MODE, (b * 3) % 254 / 2.0)] for b in range(1, nb)}
    out.append({"name": "moving: MODE ramping every block", "knobs": {MODE: 0}, "events": ev})
    ev = {}
    for b in range(1, nb):
        if b % 7 == 0:
            ev[b] = [(p, rnd.choice([0.0, 127.0, rnd.random() * 127])) for p in (MODE, 0, 1, 3, 4, 5, 6)]
    out.append({"name": "moving: MODE and the other knobs jumping at random every 7 blocks", "knobs": {MODE: 64}, "events": ev})
    out.append({"name": "fifo: calls of 1..100 samples (selcc) = 32-sample calls (selcc)", "knobs": {1: 90, 2: 100},
                "cut": "rand", "events": {200: [(7, 120.0)], 400: [(0, 40.0)]}})
    for c in out:
        c["seconds"] = seconds
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=2.0)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--nobuild", action="store_true")
    ap.add_argument("--only", default=None, help="run only the cases whose name contains this")
    ap.add_argument("--out", default=str(ROOT / "out/T5.5/spngcheck.json"))
    a = ap.parse_args()
    t0 = time.time()
    if not a.nobuild:
        build()
    size = sizeof_spring()
    cs = cases(a.seconds)
    if a.only:
        cs = [c for c in cs if a.only in c["name"]]
    for c in cs:
        c["size"] = size
    res = []
    with ProcessPoolExecutor(min(4, a.workers), max_tasks_per_child=memguard.TASKS_PER_CHILD) as ex:
        for r in memguard.pmap(ex, run_case, cs, chunksize=2):
            res.append(r)
            print(f"  {r['name']}: bit-identical {r['bit_identical']}/{r['samples']}"
                  + (f" (max diff {r['max_diff_db_re_peak']:.1f} dB re peak)" if r["max_diff_db_re_peak"] is not None else "")
                  + f", state {'same' if r['state_identical'] else 'DIFFERS'}"
                  + (f", {r['insns_mean']:.0f} insns/block (max {r['insns_max']})" if r["insns_mean"] else "")
                  + f", stack {r['stack_bytes']} B, peak {r['peak']:.3f}" + (f", {r['nonfinite']} non-finite" if r["nonfinite"] else ""),
                  flush=True)
    cz = code_size()
    stat = [r for r in res if r["insns_mean"] and not r["name"].startswith("moving")]
    mstat = [r for r in stat if r["name"].startswith("mode")]
    mov = [r for r in res if r["insns_mean"] and r["name"].startswith("moving")]
    summ = {
        "sizeof_spring": size[0],
        "code": cz,
        "cases": len(res),
        "all_bit_identical": all(r["bit_identical"] == r["samples"] for r in res),
        "all_state_identical": all(r["state_identical"] for r in res),
        "default_mean": next(r["insns_mean"] for r in res if r["name"] == "default"),
        "static_mean_range": [min(r["insns_mean"] for r in stat), max(r["insns_mean"] for r in stat)],
        "static_worst_block": max(r["insns_max"] for r in stat),
        "moving_mean": {r["name"]: r["insns_mean"] for r in mov},
        "moving_worst_block": max(r["insns_max"] for r in mov) if mov else None,
        "mode_static_mean_range": [min(r["insns_mean"] for r in mstat), max(r["insns_mean"] for r in mstat)] if mstat else None,
        "mode_static_worst_block": max(r["insns_max"] for r in mstat) if mstat else None,
        "stack_bytes_max": max(r["stack_bytes"] for r in res),
        "seconds": round(time.time() - t0, 1),
    }
    json.dump({"summary": summ, "results": res}, open(a.out, "w"), indent=1)
    print(json.dumps(summ, indent=1))


if __name__ == "__main__":
    main()
