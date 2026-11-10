#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""mrphcheck.py - MRPH, the morphing reverb (fx/morph.c): the selcc build against the Mac build, its cost in
instructions per 32-sample block, its code size and stack, in the T3.6 SHARC interpreter. Adapted from
spngcheck.py (same harness, same method).

    python3 tools/sharcvm/tests/mrphcheck.py [--seconds 2] [--workers 4] [--define MR_EARLY_FIXED] [--define MR_STAGES=12]

Cases: the defaults (hall); CHAR at 0, 32, 64, 96, 127; each knob at 0 and 127; a grid of CHAR x SIZE x DEC;
moving knobs (every knob ramping every block; CHAR sweeping room -> spring -> room, which crosses the cascade's
switch; random jumps). Outputs compared sample by sample and the whole Morph state at the end.
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

OUT = ROOT / "out/morph/mrphcheck"
if __import__("os").environ.get("MRPH_SRC") == "mrb":     # the worker processes import this afresh
    OUT = ROOT / "out/morph/mrbcheck"
SEL = ROOT / "vendor/selache/target/release"
STATE = 0x80100000      # DDR (the stock reverb's state is in DDR too, at 0x80000018)
CTX = 0x200a0000        # L2 scratch
BUF = 0x200a1000
NPAR = 6
NAMES = ["PRE", "DEC", "CHAR", "SIZE", "HPF", "TONE"]
DEFINES = []
SRC = ROOT / "fx/morph.c"     # --src mrb: fx/mrb.c (MRB, the budget version), the same harness
HDR, TYPE = "morph.h", "Morph"
PATTERN = 0xA5A5A5A5


class NativeCtx(C.Structure):
    _fields_ = [("cmd", C.c_int32), ("state", C.c_void_p), ("inL", C.c_void_p), ("inR", C.c_void_p),
                ("outL", C.c_void_p), ("outR", C.c_void_p), ("n", C.c_int32), ("param", C.c_int32),
                ("value", C.c_float)]


def build():
    dflags = [f"-D{d}" for d in DEFINES]
    OUT.mkdir(parents=True, exist_ok=True)
    inc = [f"-I{ROOT / 'fx'}", f"-I{ROOT / 'vendor/selache/libsel/include'}"]
    flags = ["-proc", "ADSP-21569", "-char-size-8", "-O1", *inc]
    objs = []
    for name, path, extra in (("mrphdrv", HERE.parent / "harness/mrphdrv.c", ["-D__SHARC_HARNESS__"]),
                              (SRC.stem, SRC, [])):
        subprocess.check_call([str(SEL / "selcc"), *flags, *dflags, *extra, "-S", "-o", str(OUT / f"{name}.s"), str(path)])
        subprocess.check_call([str(SEL / "selas"), "-proc", "ADSP-21569", "-o", str(OUT / f"{name}.doj"),
                               str(OUT / f"{name}.s")])
        objs.append(str(OUT / f"{name}.doj"))
    subprocess.check_call([str(SEL / "seld"), "-proc", "ADSP-21569", "-T", str(ROOT / "vendor/selache/libsel/link.ldf"),
                           "-o", str(OUT / "mrphdrv.dxe"), *objs, str(ROOT / "out/T3.6/libsel/libsel.dlb")])
    subprocess.check_call(["clang", "-O2", "-ffp-contract=off", "-fno-strict-aliasing", "-shared", "-fPIC",
                           f"-I{ROOT / 'fx'}", *dflags, "-o", str(OUT / "libmrphnative.dylib"),
                           str(HERE.parent / "harness/mrphdrv.c"), str(SRC)])


def code_size():
    """bytes of every function in morph.s (linked alone, as sc01_funcmap does for a recipe section) and of its
    constant data; the immediate-count shifts in it (selas mis-encodes negative ones: must be 0)"""
    import sc01_funcmap as FM
    import elf as E
    rows, sec = FM.funcmap(OUT / "funcmap", 0x2c0000, [OUT / f"{SRC.stem}.s"])
    funcs = sorted(({"function": n, "bytes": 2 * (b - a)} for n, f, a, b in rows), key=lambda r: -r["bytes"])
    e = E.read_elf(OUT / "funcmap" / "f.dxe")
    data = sum(len(s.data) for s in e.sections if s.kind == "data")
    text = (OUT / f"{SRC.stem}.s").read_text()
    import re
    shifts = len(re.findall(r"shift [^;]* by -?(0x)?[0-9]", text, re.I))
    return {"code_bytes": len(sec.data), "functions": funcs,
            "const_data_bytes": data, "immediate_shifts": shifts}


class Native:
    def __init__(self):
        self.lib = C.CDLL(str(OUT / "libmrphnative.dylib"))
        self.state = (C.c_uint8 * (1 << 20))()   # sizeof(Morph) is about 270 KB
        self.inL, self.inR = (C.c_float * 128)(), (C.c_float * 128)()
        self.outL, self.outR = (C.c_float * 128)(), (C.c_float * 128)()

    def cmd(self, cmd, n=0, param=0, value=0.0):
        c = NativeCtx(cmd, C.addressof(self.state), C.addressof(self.inL), C.addressof(self.inR),
                      C.addressof(self.outL), C.addressof(self.outR), n, param, value)
        self.lib.mrphdrv(C.byref(c))

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
        e = self.vm.load_elf(OUT / "mrphdrv.dxe")
        self.entry = e.symbols["mrphdrv."]
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


def sizeof_morph():
    """sizeof(Morph) from the Mac build (the same layout on the SHARC: every field 32 bits); no FIFO (0, 0)"""
    src = OUT / "sz.c"
    src.write_text(f'#include <stdio.h>\n#include "{HDR}"\nint main(void){{printf("%lu\\n",(unsigned long)sizeof({TYPE}));return 0;}}\n')
    subprocess.check_call(["clang", "-O0", f"-I{ROOT / 'fx'}", *[f"-D{d}" for d in DEFINES], "-o", str(OUT / "sz"), str(src)])
    return [int(subprocess.check_output([str(OUT / "sz")]).decode().split()[0]), 0, 0]


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
    out = [{"name": "default (hall)", "knobs": {}}]
    for v in (0, 32, 64, 96, 127):
        out.append({"name": f"static CHAR {v}", "knobs": {2: v}})
    for p in range(NPAR):
        for v in (0, 127):
            out.append({"name": f"static {NAMES[p]} {v}", "knobs": {p: v}})
    for ch in (0, 64, 127):
        for sz in (0, 64, 127):
            for dc in (0, 127):
                out.append({"name": f"static grid CHAR {ch} SIZE {sz} DEC {dc}", "knobs": {2: ch, 3: sz, 1: dc, 0: 127 if sz == 127 else 0}})
    nb = int(seconds * 48000) // 32
    ev = {b: [(p, (b * (3 + 2 * p)) % 128 * 1.0) for p in range(NPAR)] for b in range(1, nb)}
    out.append({"name": "moving: every knob ramping every block", "knobs": {}, "events": ev})
    ev = {b: [(2, 127.0 * (1.0 - abs(1.0 - 2.0 * b / nb)))] for b in range(1, nb)}
    out.append({"name": "moving: CHAR room -> spring -> room", "knobs": {2: 0}, "events": ev})
    rnd = random.Random(3)
    ev = {}
    for b in range(1, nb):
        if b % 7 == 0:
            ev[b] = [(p, rnd.choice([0.0, 127.0, rnd.random() * 127])) for p in range(NPAR)]
    out.append({"name": "moving: every knob jumping at random every 7 blocks", "knobs": {}, "events": ev})
    for c in out:
        c["seconds"] = seconds
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=2.0)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--nobuild", action="store_true")
    ap.add_argument("--only", default=None, help="run only the cases whose name contains this")
    ap.add_argument("--out", default=str(ROOT / "out/morph/mrphcheck.json"))
    ap.add_argument("--define", action="append", default=[], help="a -D for both builds (MR_EARLY_FIXED, MR_STAGES=12)")
    ap.add_argument("--src", default="morph", choices=["morph", "mrb"], help="mrb: fx/mrb.c, the budget version")
    a = ap.parse_args()
    DEFINES.extend(a.define)
    if a.src == "mrb":
        global SRC, HDR, TYPE, OUT
        SRC, HDR, TYPE = ROOT / "fx/mrb.c", "mrb.h", "Mrb"
        OUT = ROOT / "out/morph/mrbcheck"
        DEFINES.append("MRB")
        __import__("os").environ["MRPH_SRC"] = "mrb"
    t0 = time.time()
    if not a.nobuild:
        build()
    size = sizeof_morph()
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
    mov = [r for r in res if r["insns_mean"] and r["name"].startswith("moving")]
    summ = {
        "defines": DEFINES,
        "sizeof_morph": size[0],
        "code": cz,
        "cases": len(res),
        "all_bit_identical": all(r["bit_identical"] == r["samples"] for r in res),
        "all_state_identical": all(r["state_identical"] for r in res),
        "default_mean": next((r["insns_mean"] for r in res if r["name"].startswith("default")), None),
        "char_mean": {r["name"]: r["insns_mean"] for r in res if r["name"].startswith("static CHAR")},
        "static_mean_range": [min(r["insns_mean"] for r in stat), max(r["insns_mean"] for r in stat)],
        "static_worst_block": max(r["insns_max"] for r in stat),
        "moving_mean": {r["name"]: r["insns_mean"] for r in mov},
        "moving_worst_block": max(r["insns_max"] for r in mov) if mov else None,
        "stack_bytes_max": max(r["stack_bytes"] for r in res),
        "seconds": round(time.time() - t0, 1),
    }
    json.dump({"summary": summ, "results": res}, open(a.out, "w"), indent=1)
    print(json.dumps(summ, indent=1))


if __name__ == "__main__":
    main()
