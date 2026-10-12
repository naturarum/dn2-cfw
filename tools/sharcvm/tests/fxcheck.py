#!/usr/bin/env python3
"""selcc build vs native build of the T3.11 effects (T3.6 goal 2).

The same driver (tools/sharcvm/harness/fxdrv.c) and effect sources (fx/*.c) are built
with selache's selcc -O1 for the ADSP-21569 (run in this interpreter) and with clang
-O2 -ffp-contract=off as a native library. Both get identical calls: init, tempo,
knob settings, snap, then the test signal in 32-sample blocks. Outputs are compared
sample by sample.

    python3 tools/sharcvm/tests/fxcheck.py [--seconds 2] [--fx tapedelay|mbcomp]
--src fx (default) builds from the T3.11 sources; --src out/T3.6/fxcheck/src builds from
the copy with the selcc union workaround (section 3.4 of the note).

Added 2026-10-06 (T3.11 cost work): --set P=V sets knob P (the effect's parameter index) instead of
the defaults below, --profile prints instructions per block by function (exclusive), and the
instruction count is reported as mean and maximum per 32-sample block. The native library is built
with -fno-strict-aliasing like fx/Makefile (fxmath.h's bit casts).
"""
import argparse
import re
import ctypes as C
import math
import struct
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE.parent))
sys.dont_write_bytecode = True
from vm import VM  # noqa: E402
from render_fx import test_signal  # noqa: E402
from fx import write_wav  # noqa: E402

OUT = ROOT / "out/T3.6/fxcheck"
STATE = 0x80100000      # DDR (mapped by the interpreter; not used by the selcc program)
CTX = 0x200a0000        # L2 scratch
BUF = 0x200a1000

KNOBS = {
    # 2026-10-06 (T3.11 12): TIME FDBK HPF LPF RES WID WOW FLUT AGE DRIV (TONE and W&F are macros, not set)
    "tapedelay": {0: 40.0, 1: 70.0, 2: 22.0, 3: 102.0, 4: 40.0, 5: 64.0, 6: 50.0, 7: 40.0, 8: 30.0, 9: 30.0},
    "mbcomp": None,   # use the effect's defaults after init (none set)
    "flanger": None,  # the default patch (the slow resonant swell)
}
FXID = {"tapedelay": 0, "mbcomp": 1, "flanger": 2}


class NativeCtx(C.Structure):
    _fields_ = [("cmd", C.c_int32), ("fx", C.c_int32), ("state", C.c_void_p), ("inL", C.c_void_p),
                ("inR", C.c_void_p), ("outL", C.c_void_p), ("outR", C.c_void_p), ("n", C.c_int32),
                ("param", C.c_int32), ("value", C.c_float), ("bpm", C.c_float), ("seed", C.c_uint32)]


class Native:
    def __init__(self, fx):
        self.lib = C.CDLL(str(OUT / "libfxnative.dylib"))
        self.fx = FXID[fx]
        self.state = (C.c_uint8 * (4 << 20))()
        self.inL, self.inR = (C.c_float * 32)(), (C.c_float * 32)()
        self.outL, self.outR = (C.c_float * 32)(), (C.c_float * 32)()

    def cmd(self, cmd, n=0, param=0, value=0.0, bpm=0.0, seed=0):
        c = NativeCtx(cmd, self.fx, C.addressof(self.state), C.addressof(self.inL), C.addressof(self.inR),
                      C.addressof(self.outL), C.addressof(self.outR), n, param, value, bpm, seed)
        self.lib.fxdrv(C.byref(c))

    def block(self, L, R):
        self.inL[:] = L
        self.inR[:] = R
        self.cmd(3, n=32)
        return list(self.outL), list(self.outR)


class Sharc:
    def __init__(self, fx):
        self.vm = VM()
        e = self.vm.load_elf(OUT / "fxdrv.dxe")
        self.entry = e.symbols["fxdrv."]
        # the state selache's startup.s leaves (M5..M15, L = 0, stack B7/I7/L7, MODE1)
        self.vm.c_runtime_state(stack_base=e.symbols["ldf_stack_space"],
                                stack_top=e.symbols["ldf_stack_space"] + e.symbols["ldf_stack_length"],
                                l7=e.symbols["ldf_stack_length"], mode1=0x01010800)
        self.fx = FXID[fx]
        self.insns = 0
        self.secs = 0.0
        self.per_block = []
        self.elf = e

    def cmd(self, cmd, n=0, param=0, value=0.0, bpm=0.0, seed=0):
        vm = self.vm
        vm.write(CTX, struct.pack("<iiIIIIIiiffI", cmd, self.fx, STATE, BUF, BUF + 0x80, BUF + 0x100,
                                  BUF + 0x180, n, param, value, bpm, seed))
        n0, t0 = vm.icount, time.perf_counter()
        vm.call(self.entry, R4=CTX)
        if cmd == 3:
            self.insns += vm.icount - n0
            self.per_block.append(vm.icount - n0)
            self.secs += time.perf_counter() - t0

    def block(self, L, R):
        self.vm.wrf(BUF, L)
        self.vm.wrf(BUF + 0x80, R)
        self.cmd(3, n=32)
        return self.vm.rdf(BUF + 0x100, 32), self.vm.rdf(BUF + 0x180, 32)


def globalize(path, prefix):
    """profile builds: make every static function label global (prefixed, so the two effect files
    do not clash) so the linked ELF has a symbol for it; the code is unchanged."""
    import re
    t = open(path).read()
    glob = set(re.findall(r"^\.GLOBAL (\w+)\.;", t, re.M))
    names = [n for n in re.findall(r"^([A-Za-z_]\w*)\.:", t, re.M) if n not in glob]
    for n in names:
        t = re.sub(r"(?<![\w.])" + n + r"\.(?=[\s;:(),]|$)", prefix + n + ".", t, flags=re.M)
        t = t.replace(f"\n{prefix}{n}.:", f"\n.GLOBAL {prefix}{n}.;\n{prefix}{n}.:")
    open(path, "w").write(t)


def label_lines(path, prefix):
    """--annotate builds: a global label before every instruction line, so each PC maps back to
    its line of selcc's assembly (labels take no space; the code is unchanged)."""
    out, k = [], 0
    for line in open(path).read().split("\n"):
        st = line.strip()
        if line.startswith("    ") and st and not st.startswith((".", "//", "/*")):
            out.append(f".GLOBAL {prefix}{k}.;")
            out.append(f"{prefix}{k}.:")
            k += 1
        out.append(line)
    open(path, "w").write("\n".join(out))


def annotate(sh, blocks, names):
    """write <name>.annot.s: every instruction line with its executions per block"""
    import re
    hits = {}
    for pc in sh.vm.decoded:
        h = sh.vm.pages[pc >> 12][pc & 0xfff].hits
        if h:
            hits[pc] = h
    addr = {k: v for k, v in sh.elf.symbols.items()}
    for name, prefix in names:
        lines = open(OUT / f"{name}.s").read().split("\n")
        res, pend = [], None
        for line in lines:
            m = re.match(r"^(" + prefix + r"\d+)\.:$", line)
            if m:
                pend = addr.get(m.group(1) + ".")
                continue
            if line.startswith(".GLOBAL " + prefix):
                continue
            if pend is not None and line.startswith("    "):
                res.append(f"{hits.get(pend, 0) / blocks:9.2f} {line}")
                pend = None
            else:
                res.append(" " * 10 + line)
        open(OUT / f"{name}.annot.s", "w").write("\n".join(res))


def build(src, profile=False, annot=False):
    import subprocess
    S = ROOT / "vendor/selache/target/release"
    src = ROOT / src
    OUT.mkdir(parents=True, exist_ok=True)
    flags = ["-proc", "ADSP-21569", "-char-size-8", "-O1", f"-I{src}", f"-I{ROOT / 'vendor/selache/libsel/include'}"]
    objs = []
    for name, path, extra in (("fxdrv", HERE.parent / "harness/fxdrv.c", ["-D__SHARC_HARNESS__"]),
                              ("tapedelay", src / "tapedelay.c", []), ("mbcomp", src / "mbcomp.c", []),
                              ("flanger", src / "flanger.c", [])):
        subprocess.check_call([str(S / "selcc"), *flags, *extra, "-S", "-o", str(OUT / f"{name}.s"), str(path)])
        if profile and name != "fxdrv":
            globalize(OUT / f"{name}.s", {"tapedelay": "TDx_", "mbcomp": "MBx_", "flanger": "FLx_"}[name])
        if annot and name != "fxdrv":
            label_lines(OUT / f"{name}.s", {"tapedelay": "TDL", "mbcomp": "MBL", "flanger": "FLL"}[name])
        subprocess.check_call([str(S / "selas"), "-proc", "ADSP-21569", "-o", str(OUT / f"{name}.doj"), str(OUT / f"{name}.s")])
        objs.append(str(OUT / f"{name}.doj"))
    subprocess.check_call([str(S / "seld"), "-proc", "ADSP-21569", "-T", str(ROOT / "vendor/selache/libsel/link.ldf"),
                           "-o", str(OUT / "fxdrv.dxe"), *objs, str(ROOT / "out/T3.6/libsel/libsel.dlb")])
    subprocess.check_call(["clang", "-O2", "-ffp-contract=off", "-fno-strict-aliasing", "-shared", "-fPIC", f"-I{src}", "-o",
                           str(OUT / "libfxnative.dylib"), str(HERE.parent / "harness/fxdrv.c"),
                           str(src / "tapedelay.c"), str(src / "mbcomp.c"), str(src / "flanger.c")])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default="fx")
    ap.add_argument("--fx", default="tapedelay", choices=["tapedelay", "mbcomp", "flanger"])
    ap.add_argument("--seconds", type=float, default=2.0)
    ap.add_argument("--set", nargs="*", default=None, help="P=V: knob index P to value V (replaces the defaults)")
    ap.add_argument("--profile", action="store_true")
    ap.add_argument("--annotate", action="store_true", help="write out/T3.6/fxcheck/<file>.annot.s: hits per line per block")
    ap.add_argument("--nobuild", action="store_true")
    ap.add_argument("--at", nargs="*", default=[], help="B:P=V: set knob P to V before block B (knob moves)")
    a = ap.parse_args()
    if a.set is not None:
        KNOBS[a.fx] = {int(kv.split("=")[0]): float(kv.split("=")[1]) for kv in a.set}
    if not a.nobuild:
        if a.annotate:
            a.profile = True
        build(a.src, profile=a.profile, annot=a.annotate)
    L, R = test_signal(a.seconds)
    runs = []
    for impl in (Native(a.fx), Sharc(a.fx)):
        prof = a.profile and isinstance(impl, Sharc)
        impl.cmd(0, seed=1)
        if a.fx in ("tapedelay", "flanger"):
            impl.cmd(4, bpm=120.0)
        for p, v in (KNOBS[a.fx] or {}).items():
            impl.cmd(1, param=p, value=v)
        impl.cmd(2)
        if prof:
            impl.vm.lib.vm_set_profile(impl.vm.h, 1)
        oL, oR = [], []
        events = {}
        for ev in a.at:
            blk, pv = ev.split(":")
            p_, v_ = pv.split("=")
            events.setdefault(int(blk), []).append((int(p_), float(v_)))
        for i in range(0, len(L) // 32 * 32, 32):
            for p_, v_ in events.get(i // 32, []):
                impl.cmd(1, param=p_, value=v_)
            x, y = impl.block(L[i:i + 32], R[i:i + 32])
            oL += x
            oR += y
        runs.append((impl, oL, oR))
    (nat, nL, nR), (sh, sL, sR) = runs
    n = len(nL)
    f2u = lambda x: struct.unpack("<I", struct.pack("<f", x))[0]
    same = sum(1 for a_, b_ in zip(nL + nR, sL + sR) if f2u(a_) == f2u(b_))
    diff = [abs(a_ - b_) for a_, b_ in zip(nL + nR, sL + sR)]
    peak = max(max(map(abs, nL)), max(map(abs, nR)), 1e-30)
    md = max(diff)
    rms = math.sqrt(sum(d * d for d in diff) / len(diff))
    first = next((i for i, d in enumerate(diff) if d > 0), None)
    write_wav(OUT / f"{a.fx}_native.wav", nL, nR)
    write_wav(OUT / f"{a.fx}_sharc.wav", sL, sR)
    blocks = n // 32
    print(f"{a.fx}: {n} frames x 2; bit-identical samples {same}/{2 * n} ({100 * same / (2 * n):.2f}%); "
          f"max |diff| {md:.3g} ({20 * math.log10(md / peak) if md else float('-inf'):.1f} dB re peak {peak:.3f}), "
          f"rms diff {rms:.3g}; first difference at sample {first}")
    print(f"selcc build: {sh.insns / blocks:.0f} instructions per 32-sample block in the interpreter "
          f"(max {max(sh.per_block)}, min {min(sh.per_block)}; {sh.insns / sh.secs / 1e6:.1f} M insn/s)")
    if a.profile:
        funcs = sorted((v, k.rstrip(".")) for k, v in sh.elf.symbols.items()
                       if k.endswith(".") and not k.startswith(".") and v)
        from bisect import bisect_right
        starts = [v for v, _ in funcs]
        per = {}
        for pc in sh.vm.decoded:
            h = sh.vm.pages[pc >> 12][pc & 0xfff].hits
            if h:
                i = bisect_right(starts, pc) - 1
                name = funcs[i][1] if i >= 0 else "?"
                per[name] = per.get(name, 0) + h
        if a.annotate:
            annotate(sh, blocks, [("tapedelay", "TDL"), ("mbcomp", "MBL"), ("flanger", "FLL")])
            per = {k: v for k, v in per.items() if not re.match(r"^_?(TDL|MBL|FLL)\d+$", k)}
        tot = sum(per.values())
        print("by function (exclusive, per block):")
        for name, h in sorted(per.items(), key=lambda t: -t[1]):
            if h / blocks >= 1:
                print(f"  {name:28s} {h / blocks:9.0f}  {100 * h / tot:5.1f}%")


if __name__ == "__main__":
    main()
