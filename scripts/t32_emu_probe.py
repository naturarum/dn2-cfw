#!/usr/bin/env python3
"""T3.2 (for T3.6): how far does digikit's tools/sharcemu.py get on the Digitone II DSP program?

Run with digikit's environment (it needs pypcode):
    cd vendor/digikit
    uv run python ../../scripts/t32_emu_probe.py sleigh      # generate + compile the SLEIGH language into out/T3.2/sleigh
    uv run python ../../scripts/t32_emu_probe.py coverage    # which instructions have p-code semantics
    uv run python ../../scripts/t32_emu_probe.py run SW [--steps N] [--set REG=VAL ...]
    uv run python ../../scripts/t32_emu_probe.py speed       # lift + execute throughput

Nothing in vendor/ is written: the SLEIGH language that sharcemu.py expects under
tools/sharcspec/ghidra/SHARC_VISA/ (generated, git-ignored there) is generated here into
out/T3.2/sleigh/ by importing digikit's gen_sleigh.py with its OUT_DIR redirected, and compiled
with pypcode's bundled `sleigh`. The executor is sharcemu.PypcodeConcreteBackend, unchanged,
except that this probe gives it the whole loaded image (out/T3.2/mem, from t32_load.py) instead
of one code region: data reads see the core-byte image, code is fetched at 2*SW (the backend's
"displayed" address), which for L2 code is redirected to 0x20000000 + 2*(SW-0xb80000).
"""

import argparse
import collections
import importlib.util
import json
import os
import sqlite3
import struct
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DK = ROOT / "vendor/digikit"
sys.path.insert(0, str(DK / "tools"))
MEM = ROOT / "out/T3.2/mem"
SLEIGH = ROOT / "out/T3.2/sleigh/SHARC_VISA/data/languages"
LDEFS = SLEIGH / "sharc_visa.ldefs"
DB = DK / "out/sharcdb/dn2-1.12.sqlite"
L2_SW = 0xB80000


def cmd_sleigh():
    spec = importlib.util.spec_from_file_location("gen_sleigh", DK / "tools/sharcspec/ghidra/gen_sleigh.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    m.OUT_DIR = str(SLEIGH)
    m.main()
    import pypcode
    sleigh = Path(pypcode.__file__).parent / "bin" / "sleigh"
    subprocess.run([str(sleigh), "sharc_visa.slaspec", "sharc_visa.sla"], cwd=SLEIGH, check=True)
    print("compiled", SLEIGH / "sharc_visa.sla")


class Image:
    """Loaded memory at core byte addresses, plus code fetch at displayed = 2*SW."""

    def __init__(self):
        m = json.loads((MEM / "memmap.json").read_text())
        self.regions = [(int(r["core_start"], 16), bytearray((MEM / r["file"]).read_bytes())) for r in m["regions"]]

    def find(self, a):
        for base, buf in self.regions:
            if base <= a < base + len(buf):
                return base, buf
        return None, None

    @staticmethod
    def disp_to_core(disp):
        sw = disp // 2
        if L2_SW <= sw < L2_SW + 0x80000:
            return 0x20000000 + 2 * (sw - L2_SW) + (disp & 1)
        return disp

    def code(self, disp, n):
        a = self.disp_to_core(disp)
        base, buf = self.find(a)
        if base is None:
            return b""
        return bytes(buf[a - base:a - base + n])


def make_backend(img):
    import sharcemu
    import pypcode

    class Probe(sharcemu.PypcodeConcreteBackend):
        def __init__(self):  # same fields as the original, own language path and memory
            self._pypcode = pypcode
            arch = pypcode.Arch("SHARC_VISA", str(LDEFS))
            self.ctx = pypcode.Context(arch.languages[0])
            self.code, self.code_base = b"", 0
            self.mem, self.regs = {}, {}
            self.pc, self.trace, self.max_bytes = 0, [], 32
            self._offsets = sharcemu._pypcode_register_offsets()
            self.img = img
            self.unmapped = collections.Counter()

        def peek(self, address, width):
            out = 0
            for i in range(width):
                b = self.mem.get(address + i)
                if b is None:
                    base, buf = img.find(address + i)
                    if base is None:
                        self.unmapped[address & ~0xFFF] += 1
                        b = 0
                    else:
                        b = buf[address + i - base]
                out |= b << (8 * i)
            return out

        def lift(self, pc):
            chunk = img.code(pc, self.max_bytes)
            tr = self.ctx.translate(chunk, base_address=pc, max_instructions=1)
            length, ops = None, []
            for op in tr.ops:
                if op.opcode == pypcode.OpCode.IMARK:
                    if length is None:
                        length = op.inputs[0].size
                else:
                    ops.append(op)
            if length is None:
                raise sharcemu.PypcodeFault("pypcode did not decode anything at %#x" % pc)
            return length, ops

    return Probe()


def db_rows(where, args=()):
    c = sqlite3.connect(str(DB))
    return c.execute("SELECT sw, width, form, mnemonic FROM insn WHERE image='dn2-1.12' AND aligned=1 AND " + where,
                     args).fetchall()


def cmd_coverage(out_json):
    import pypcode
    img = Image()
    arch = pypcode.Arch("SHARC_VISA", str(LDEFS))
    ctx = pypcode.Context(arch.languages[0])
    # main program code: L1 block 3 (SW 0x1c0000-0x1cdffe) and L2 block 56 (SW 0xb80000-0xb8f440)
    rows = db_rows("((sw >= 0x1c0000 AND sw < 0x1cdffe) OR (sw >= 0xb80000 AND sw < 0xb8f440))")
    per_form = collections.defaultdict(lambda: [0, 0, 0, ""])  # total, empty, len-mismatch, example
    sem = {}
    for sw, width, form, mn in rows:
        chunk = img.code(2 * sw, 32)
        tr = ctx.translate(chunk, base_address=2 * sw, max_instructions=1)
        ln, ops = None, []
        for op in tr.ops:
            if op.opcode == pypcode.OpCode.IMARK:
                ln = ln or op.inputs[0].size
            else:
                ops.append(op.opcode.name)
        f = per_form[form]
        f[0] += 1
        empty = not ops
        compute_only = ops and all(o in ("CALLOTHER",) for o in ops)
        if empty:
            f[1] += 1
            f[3] = f[3] or "0x%x %s" % (sw, mn)
        if ln != width:
            f[2] += 1
        sem[sw] = "empty" if empty else ("compute-only" if compute_only else "ok")
    tot = len(rows)
    nempty = sum(1 for v in sem.values() if v == "empty")
    print("main-program aligned instructions: %d; empty p-code (no-semantics): %d (%.1f%%)" % (
        tot, nempty, 100.0 * nempty / tot))
    print("%-22s %7s %7s %6s  %s" % ("form", "count", "empty", "lenΔ", "example of an empty one"))
    for form, (n, e, lm, ex) in sorted(per_form.items(), key=lambda kv: -kv[1][1]):
        if e or lm:
            print("%-22s %7d %7d %6d  %s" % (form, n, e, lm, ex))
    # per routine tree (T3.4 walks) and per root reach set
    trees = {}
    for name in ("delay", "chorus", "reverb", "comp"):
        p = ROOT / "out/T3.4" / ("%s_tree.json" % name)
        if p.exists():
            t = json.loads(p.read_text())
            sws = set()
            for k, v in t.items():
                lo, hi = (int(x, 16) for x in v["span"])
                sws |= {s for s in sem if lo <= s <= hi}
            trees[name] = sws
    c = sqlite3.connect(str(DB))
    for root, label in ((0x1C9FE7, "Audio Task reach"),):
        funcs = [r[0] for r in c.execute("SELECT function_sw FROM reach WHERE image='dn2-1.12' AND root_sw=?", (root,))]
        sws = set()
        for f in funcs:
            sws |= {r[0] for r in c.execute(
                "SELECT sw FROM insn WHERE image='dn2-1.12' AND aligned=1 AND function_sw=?", (f,))}
        trees[label] = sws & set(sem)
    summary = {}
    for name, sws in trees.items():
        n = len(sws)
        e = sum(1 for s in sws if sem[s] == "empty")
        summary[name] = {"insns": n, "empty": e}
        print("%-18s %6d instructions, %5d (%.1f%%) without semantics" % (name, n, e, 100.0 * e / max(n, 1)))
    if out_json:
        Path(out_json).write_text(json.dumps({
            "total": tot, "empty": nempty, "trees": summary,
            "forms": {k: {"count": v[0], "empty": v[1], "len_mismatch": v[2], "example": v[3]}
                      for k, v in per_form.items()}}, indent=1))


def cmd_run(start_sw, steps, sets, pokes):
    import sharcemu
    img = Image()
    be = make_backend(img)
    for name, val in sets:
        be.set_ureg(name, val)
    for a, v in pokes:
        be.poke(a, v)
    be.pc = 2 * start_sw
    n, t0, fault = 0, time.perf_counter(), None
    path = []
    try:
        while n < steps:
            path.append(be.pc // 2)
            if not be.step():
                fault = "RETURN p-code op at 0x%x (the backend stops here)" % (be.pc // 2)
                break
            n += 1
    except sharcemu.PypcodeFault as e:
        fault = "PypcodeFault: %s" % e
    except Exception as e:  # anything else the draft interpreter raises
        fault = "%s: %s" % (type(e).__name__, e)
    dt = time.perf_counter() - t0
    print("start SW 0x%x: executed %d instructions in %.3f s (%.0f insn/s); stop: %s" % (
        start_sw, n, dt, n / dt if dt else 0, fault or "max steps"))
    print("last PCs:", " ".join("0x%x" % p for p in path[-8:]))
    rows = {r[0]: r for r in db_rows("sw IN (%s)" % ",".join(str(p) for p in path[-3:]))} if path else {}
    for p in path[-3:]:
        r = rows.get(p)
        if r:
            print("  0x%x  %s  %s" % (p, r[2], r[3]))
    if be.unmapped:
        print("reads outside the loaded image (4 KiB pages):", dict(be.unmapped))
    return n, fault


def cmd_speed():
    """Throughput of lift + execute on straight-line instructions that do have semantics."""
    import sharcemu
    import pypcode
    img = Image()
    rows = db_rows("sw >= 0xb80000 AND sw < 0xb8f440")
    be = make_backend(img)
    ok = []
    for sw, width, form, mn in rows:
        try:
            ln, ops = be.lift(2 * sw)
        except sharcemu.PypcodeFault:
            continue
        names = {o.opcode.name for o in ops}
        if ops and not names & {"BRANCH", "CBRANCH", "CALL", "RETURN", "BRANCHIND", "CALLIND"}:
            ok.append(sw)
        if len(ok) >= 3000:
            break
    t0 = time.perf_counter()
    lifted = 0
    for sw in ok:
        be.lift(2 * sw)
        lifted += 1
    t_lift = time.perf_counter() - t0
    t0 = time.perf_counter()
    done = errs = 0
    for sw in ok:
        be.pc = 2 * sw
        try:
            be.step()
            done += 1
        except Exception:
            errs += 1
    t_step = time.perf_counter() - t0
    print("lift only: %d instructions in %.3f s = %.0f insn/s" % (lifted, t_lift, lifted / t_lift))
    print("lift + execute: %d ok, %d raised, in %.3f s = %.0f insn/s" % (done, errs, t_step, (done + errs) / t_step))


def parse_assign(s):
    k, v = s.split("=", 1)
    return k, int(v, 0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["sleigh", "coverage", "run", "speed"])
    ap.add_argument("start", nargs="?")
    ap.add_argument("--steps", type=int, default=100000)
    ap.add_argument("--set", action="append", default=[])
    ap.add_argument("--poke", action="append", default=[])
    ap.add_argument("--json")
    a = ap.parse_args()
    if a.cmd == "sleigh":
        cmd_sleigh()
    elif a.cmd == "coverage":
        cmd_coverage(a.json)
    elif a.cmd == "run":
        cmd_run(int(a.start, 16), a.steps, [parse_assign(s) for s in a.set], [parse_assign(s) for s in a.poke])
    elif a.cmd == "speed":
        cmd_speed()


if __name__ == "__main__":
    main()
