#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""flgcheck.py - T5.6: the chorus-slot glue, FLG1's knob map (flg_glue.c) vs the retune (flg_glue2.c).

    python3 tools/sharcvm/tests/flgcheck.py [--seconds 4]

Each glue is built with fx/flanger.c and harness/flgdrv.c twice, with selache's selcc -O1 (run in this
interpreter) and with clang -O2 -ffp-contract=off (native), both with -DFLG_NATIVE (the slot a static).
Both get the same calls: init, then the test signal in 32-sample blocks with the four chorus parameter
floats (knob/127) of each case. Reported, per glue and case: instructions per block (mean, max), selcc
vs native (bit-identical samples), and the glue's code size in the DSP build (selcc without FLG_NATIVE,
linked the same way; bytes = 2 x short words between function labels). Also FLG1 vs FLG2 on the same
input with C = 32 (HPF 32 = TIME 32, version 2's flanger point): they must be bit-identical.
--wide (FL01): WDTH 127 against 126 on each glue, with the same (mono) input on both sides: the correlation of the
left and right delay at each block's end (read from the slot; +1 = in phase, -1 = 180 degrees apart) and the largest
left-right output difference. Results in out/T5.6/flgcheck.json (--out: elsewhere).
"""
import argparse
import ctypes as C
import json
import random
import re
import struct
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
sys.dont_write_bytecode = True
from vm import VM  # noqa: E402
from render_fx import test_signal  # noqa: E402
from fxcheck import globalize  # noqa: E402
import memguard  # noqa: E402

OUT = ROOT / "out/T5.6/flgcheck"
SEL = ROOT / "vendor/selache/target/release"
GLUES = {"FLG1": ROOT / "tools/dspsplice/src/flg_glue.c", "FLG2": ROOT / "tools/dspsplice/src/flg_glue2.c"}
CTX = 0x200a0000        # L2 scratch: the context, the descriptor, the buffers
IO = CTX + 0x40
PRM = CTX + 0x80
PTRS = CTX + 0xc0
BUF = 0x200a1000

# unit knobs DPTH, SPD, C (HPF on FLG1, TIME on FLG2), WDTH
CASES = {
    "swell (C 32)": (96, 30, 32, 48),
    "new kit (stock defaults: C 0)": (127, 32, 0, 127),
    "phaser-like (C 12)": (90, 50, 12, 64),
    "flanger (C 40)": (80, 45, 40, 32),
    "chorus (C 115)": (40, 70, 115, 127),
}


def flags():
    return ["-proc", "ADSP-21569", "-char-size-8", "-O1", f"-I{ROOT / 'fx'}", f"-I{ROOT / 'tools/dspsplice/src'}",
            f"-I{ROOT / 'vendor/selache/libsel/include'}"]


def build(name, native_slot):
    tag = f"{name}{'n' if native_slot else 's'}"
    d = OUT / tag
    d.mkdir(parents=True, exist_ok=True)
    extra = ["-DFLG_NATIVE"] if native_slot else []
    objs, glue_labels = [], []
    for stem, path, ex in (("flgdrv", HERE.parent / "harness/flgdrv.c", ["-D__SHARC_HARNESS__"]),
                           ("glue", GLUES[name], extra), ("flanger", ROOT / "fx/flanger.c", [])):
        s = d / f"{stem}.s"
        subprocess.check_call([str(SEL / "selcc"), *flags(), *ex, "-S", "-o", str(s), str(path)])
        if stem != "flgdrv":
            globalize(s, ("G" if stem == "glue" else "F") + "x_")
        if stem == "glue":
            glue_labels = re.findall(r"^([A-Za-z_]\w*)\.:", s.read_text(), re.M)
        subprocess.check_call([str(SEL / "selas"), "-proc", "ADSP-21569", "-o", str(d / f"{stem}.doj"), str(s)])
        objs.append(str(d / f"{stem}.doj"))
    subprocess.check_call([str(SEL / "seld"), "-proc", "ADSP-21569", "-T", str(ROOT / "vendor/selache/libsel/link.ldf"),
                           "-o", str(d / "flg.dxe"), *objs, str(ROOT / "out/T3.6/libsel/libsel.dlb")])
    if native_slot:
        subprocess.check_call(["clang", "-O2", "-ffp-contract=off", "-fno-strict-aliasing", "-shared", "-fPIC",
                               "-DFLG_NATIVE", f"-I{ROOT / 'fx'}", f"-I{ROOT / 'tools/dspsplice/src'}", "-o",
                               str(d / "libflg.dylib"), str(HERE.parent / "harness/flgdrv.c"), str(GLUES[name]),
                               str(ROOT / "fx/flanger.c")])
    return d, glue_labels


def code_sizes(d, glue_labels):
    """bytes per function of the glue file: 2 x short words to the next code label in the image"""
    import elf as E
    e = E.read_elf(d / "flg.dxe")
    code = sorted(v for k, v in e.symbols.items() if k.endswith(".") and not k.startswith(".") and v)
    sizes = {}
    for k, v in e.symbols.items():
        if not k.endswith("."):
            continue
        n = k[:-1]
        base = n[3:] if n.startswith("Gx_") else n
        if base in glue_labels or n in glue_labels:
            nxt = next((c for c in code if c > v), None)
            if nxt is not None:
                sizes[base] = 2 * (nxt - v)
    return sizes


class FxIoN(C.Structure):
    _fields_ = [("n", C.c_int32), ("inp", C.c_void_p), ("r08", C.c_int32), ("outp", C.c_void_p),
                ("r10", C.c_int32), ("bpm", C.c_float)]


class CtxN(C.Structure):
    _fields_ = [("cmd", C.c_int32), ("p", C.c_void_p), ("io", C.c_void_p)]


class Native:
    def __init__(self, d):
        self.lib = C.CDLL(str(d / "libflg.dylib"))
        self.inL, self.inR = (C.c_float * 32)(), (C.c_float * 32)()
        self.outL, self.outR = (C.c_float * 32)(), (C.c_float * 32)()
        self.ins = (C.c_void_p * 2)(C.addressof(self.inL), C.addressof(self.inR))
        self.outs = (C.c_void_p * 2)(C.addressof(self.outL), C.addressof(self.outR))
        self.io = FxIoN(32, C.addressof(self.ins), 0, C.addressof(self.outs), 0, 120.0)
        self.p = (C.c_float * 4)()

    def init(self):
        self.lib.flgdrv(C.byref(CtxN(0, None, None)))

    def block(self, L, R, knobs):
        self.inL[:] = L
        self.inR[:] = R
        self.p[:] = [k / 127.0 for k in knobs]
        self.lib.flgdrv(C.byref(CtxN(1, C.addressof(self.p), C.addressof(self.io))))
        return list(self.outL), list(self.outR)


class Sharc:
    def __init__(self, d):
        self.vm = VM()
        e = self.vm.load_elf(d / "flg.dxe")
        self.entry = e.symbols["flgdrv."]
        self.vm.c_runtime_state(stack_base=e.symbols["ldf_stack_space"],
                                stack_top=e.symbols["ldf_stack_space"] + e.symbols["ldf_stack_length"],
                                l7=e.symbols["ldf_stack_length"], mode1=0x01010800)
        self.vm.write(IO, struct.pack("<iIiIif", 32, PTRS, 0, PTRS + 8, 0, 120.0))
        self.vm.write(PTRS, struct.pack("<IIII", BUF, BUF + 0x80, BUF + 0x100, BUF + 0x180))
        self.per_block = []

    def init(self):
        self.vm.write(CTX, struct.pack("<iII", 0, 0, 0))
        self.vm.call(self.entry, R4=CTX)

    def block(self, L, R, knobs):
        vm = self.vm
        vm.wrf(BUF, L)
        vm.wrf(BUF + 0x80, R)
        vm.wrf(PRM, [k / 127.0 for k in knobs])
        vm.write(CTX, struct.pack("<iII", 1, PRM, IO))
        n0 = vm.icount
        vm.call(self.entry, R4=CTX)
        self.per_block.append(vm.icount - n0)
        return vm.rdf(BUF + 0x100, 32), vm.rdf(BUF + 0x180, 32)


def run(impl, L, R, script):
    impl.init()
    oL, oR = [], []
    for b, i in enumerate(range(0, len(L) // 32 * 32, 32)):
        x, y = impl.block(L[i:i + 32], R[i:i + 32], script(b))
        oL += x
        oR += y
    return oL, oR


def walk_script(seed, blocks):
    rnd = random.Random(seed)
    k = [64.0, 64.0, 64.0, 64.0]
    seq = []
    for _ in range(blocks):
        j = rnd.randrange(4)
        k[j] = min(127.0, max(0.0, k[j] + rnd.choice((-3, -1, 1, 3))))
        seq.append(tuple(k))
    return lambda b: seq[b]


DEND = 33024        # offsetof(FlgSlot, fl.dend) in both glues (clang offsetof; the same 4-byte fields on SHARC)


def find_slot(sh, d, magic):
    """the static slot's address after init: where the magic and fresh = 1 sit in the ELF's data/bss"""
    import elf as E
    for sec in E.read_elf(d / "flg.dxe").sections:
        if sec.kind not in ("data", "bss") or len(sec.data) < DEND:
            continue
        raw = sh.vm.read(sec.addr, len(sec.data))
        for o in range(0, len(raw) - 8, 4):
            if struct.unpack_from("<II", raw, o) == (magic, 1):
                return sec.addr + o
    raise SystemExit("slot not found")


def wide(built, L, blocks):
    """WDTH 126 and 127 at DPTH 64, SPD 80 (about 1 Hz), C 32, mono input: the L/R delay trajectories' correlation and the largest L-R output difference"""
    out = {}
    mono = L[:blocks * 32]
    for name, d in built.items():
        magic = 0x464c4731 if name == "FLG1" else 0x464c4732
        for wd in (126, 127):
            sh = Sharc(d)
            sh.init()
            slot = find_slot(sh, d, magic)
            dl, dr, lr = [], [], 0.0
            for i in range(0, len(mono), 32):
                oL, oR = sh.block(mono[i:i + 32], mono[i:i + 32], (64, 80, 32, wd))
                a, b = sh.vm.rdf(slot + DEND, 2)
                dl.append(a)
                dr.append(b)
                lr = max(lr, max(abs(x - y) for x, y in zip(oL, oR)))
            n = len(dl) // 4                       # after the first quarter (the smoothers settled)
            ml, mr = sum(dl[n:]) / len(dl[n:]), sum(dr[n:]) / len(dr[n:])
            sxy = sum((x - ml) * (y - mr) for x, y in zip(dl[n:], dr[n:]))
            sxx = sum((x - ml) ** 2 for x in dl[n:])
            syy = sum((y - mr) ** 2 for y in dr[n:])
            out.setdefault(name, {})["WDTH %d" % wd] = {
                "lr_delay_correlation": round(sxy / max((sxx * syy) ** 0.5, 1e-30), 4),
                "delay_samples_left": [round(min(dl[n:]), 2), round(max(dl[n:]), 2)],
                "delay_samples_right": [round(min(dr[n:]), 2), round(max(dr[n:]), 2)],
                "max_lr_output_diff": round(lr, 4), "slot": "%#x" % slot}
            memguard.check()
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=4.0)
    ap.add_argument("--wide", action="store_true", help="WDTH 127 against 126 on each glue (mono input, 6 s)")
    ap.add_argument("--out", help="the results file (default out/T5.6/flgcheck.json)")
    ap.add_argument("--grid", action="store_true", help="also every combination of DPTH/SPD/WDTH 0, 64, 127 and "
                    "C 0, 12, 32, 64, 82, 100, 127 (0.25 s each, selcc only): the highest block per glue")
    a = ap.parse_args()
    global OUT
    if a.out:                       # the builds next to the results: <out without .json>/FLG1s ...
        OUT = Path(a.out).resolve().with_suffix("")
    f2u = lambda x: struct.unpack("<I", struct.pack("<f", x))[0]
    res = {"sizes": {}, "cases": {}}
    built = {}
    for name in GLUES:
        ds, labels = build(name, False)
        res["sizes"][name] = code_sizes(ds, labels)
        res["sizes"][name]["total"] = sum(v for k, v in res["sizes"][name].items())
        built[name] = build(name, True)[0]
    L, R = test_signal(a.seconds)
    blocks = len(L) // 32
    cases = dict((n, (lambda k: (lambda b: k))(k)) for n, k in CASES.items())
    cases["every knob moving"] = walk_script(5, blocks)
    outs = {}
    for name, d in built.items():
        for cname, script in cases.items():
            nat, sh = Native(d), Sharc(d)
            nL, nR = run(nat, L, R, script)
            sL, sR = run(sh, L, R, script)
            same = sum(1 for x, y in zip(nL + nR, sL + sR) if f2u(x) == f2u(y))
            pb = sh.per_block
            res["cases"].setdefault(cname, {})[name] = {
                "insn_mean": round(sum(pb[1:]) / (len(pb) - 1), 1), "insn_max": max(pb[1:]),
                "selcc_vs_native_identical": f"{same}/{2 * len(nL)}",
                "selcc_vs_native_max_diff_db": round(20 * __import__("math").log10(max(
                    max(abs(x - y) for x, y in zip(nL + nR, sL + sR)), 1e-30)), 1),
                "peak": round(max(max(map(abs, sL)), max(map(abs, sR))), 4)}
            outs[(name, cname)] = sL + sR
            memguard.check()
    if a.grid:
        gL, gR = L[:12000 + 12000], R[:12000 + 12000]
        for name, d in built.items():
            worst, wk, means = 0, None, []
            for dp in (0, 64, 127):
                for sp in (0, 64, 127):
                    for c in (0, 12, 32, 64, 82, 100, 127):
                        for wd in (0, 64, 127):
                            sh = Sharc(d)
                            run(sh, gL, gR, (lambda k: (lambda b: k))((dp, sp, c, wd)))
                            pb = sh.per_block[1:]
                            means.append(sum(pb) / len(pb))
                            if max(pb) > worst:
                                worst, wk = max(pb), (dp, sp, c, wd)
                            memguard.check()
            res.setdefault("grid", {})[name] = {"cases": len(means), "worst_block": worst, "worst_at": wk,
                                                 "highest_mean": round(max(means), 1)}
    s1, s2 = outs[("FLG1", "swell (C 32)")], outs[("FLG2", "swell (C 32)")]
    res["flg1_vs_flg2_at_c32_identical"] = f"{sum(1 for x, y in zip(s1, s2) if f2u(x) == f2u(y))}/{len(s1)}"
    if a.wide:
        res["wide"] = wide(built, test_signal(6.0)[0], int(6.0 * 1500))
    OUT.mkdir(parents=True, exist_ok=True)
    (Path(a.out) if a.out else OUT.parent / "flgcheck.json").write_text(json.dumps(res, indent=1))
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
