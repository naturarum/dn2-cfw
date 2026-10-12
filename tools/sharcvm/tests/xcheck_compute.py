#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Differential test of compute semantics: this interpreter vs digikit's tracer.

For every distinct 23-bit compute field found in the DN2 1.12 code (sharcdb), and for
random register states, execute the field as a Type 2a instruction (`IF TRUE compute`)
in the interpreter and evaluate it with digikit's `sharc_trace._compute` (an independent
reading of the PRM; it models a subset and raises or returns Unknown for the rest).
Compares destination register values bit for bit; flags are not compared (digikit models
them partially).

    python3 tools/sharcvm/tests/xcheck_compute.py [--trials 20]
Writes out/T3.6/coverage/xcheck_compute.tsv.
"""
import argparse
import random
import sqlite3
import struct
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(ROOT / "vendor/digikit/tools"))
sys.dont_write_bytecode = True
import sharc_trace as ST  # noqa: E402
from vm import VM, ST_DECODE  # noqa: E402
import translate as T  # noqa: E402

PC = 0x1cf000          # free L1 block 3 tail (T3.2 2.4), SW address
DB = ROOT / "vendor/digikit/out/sharcdb/dn2-1.12.sqlite"


def f2u(x):
    return struct.unpack("<I", struct.pack("<f", x))[0]


def rand_val(rng, kind):
    r = rng.random()
    if kind == "float":
        if r < 0.1:
            return f2u(rng.choice([0.0, -0.0, 1.0, -1.0, 0.5, 2.0]))
        return f2u(rng.uniform(-100, 100) * 10 ** rng.randint(-3, 3))
    if r < 0.2:
        return rng.choice([0, 1, 0xffffffff, 0x7fffffff, 0x80000000, 2, 31, 16])
    if r < 0.5:
        return rng.randint(-64, 64) & 0xffffffff
    return rng.getrandbits(32)


def compute_fields():
    db = sqlite3.connect(DB)
    seen = set()
    for raw, fields in db.execute(
            "select raw, fields from insn where image='dn2-1.12' and aligned=1 and "
            "((sw >= 0x1c0000 and sw < 0x1d0000) or (sw >= 0xb80000 and sw < 0xb8f444)) and "
            "form in ('2a','2a_short','3a','4a','5a_move','7a','1a','9a_abs','9a_rel','11a')"):
        import json
        f = json.loads(fields)
        if "compute" in f:
            c = f["compute"]
        elif "compute[22:16]" in f:
            c = (f["compute[22:16]"] << 16) | f["compute[15:0]"]
        else:
            continue
        if c:
            seen.add(c)
    return sorted(seen)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=int, default=20)
    a = ap.parse_args()
    rng = random.Random(1)
    vm = VM()
    vm.load_dn2()
    fields = compute_fields()
    stats = Counter()
    per_op = defaultdict(Counter)
    lines = []
    frame0 = 0x013e00000000
    for c in fields:
        frame = frame0 | c
        parcels = [(frame >> 32) & 0xffff, (frame >> 16) & 0xffff, frame & 0xffff]
        vm.write(2 * PC, struct.pack("<3H", *parcels))
        vm.decode_into(PC)
        d, ins = vm.decoded[PC]
        if isinstance(ins, str):
            stats["ours: unimpl"] += 1
            lines.append(f"{c:#08x}\tours-unimpl\t{ins}")
            continue
        names = [T.OPS[s.op] for s in ins.cs]
        opname = "+".join(names)
        kind = "float" if any(n.startswith("F") or n in ("FLOAT", "TRUNC") for n in names) else "int"
        for trial in range(a.trials):
            regs = [rand_val(rng, kind if rng.random() < 0.8 else "int") for _ in range(16)]
            # digikit
            values = {i: ST.Const(regs[i]) for i in range(16)}
            fl = {"compute[22:16]": (c >> 16) & 0x7f, "compute[15:0]": c & 0xffff}
            try:
                res = ST._compute(fl, False, values, {"MRF": ST.Const(0)})
            except Exception as e:  # noqa
                stats["digikit: unsupported"] += 1
                per_op[opname]["digikit-unsupported"] += 1
                break
            if res is None:
                stats["digikit: none"] += 1
                break
            dest, val = res[0], res[1]
            if res[2] in ("compare", "float-compare", "bit-test") or "compare" in str(res[2]) or "test" in str(res[2]):
                stats["status-only op (skipped)"] += 1
                break
            special = any(((x >> 23) & 0xff) in (0, 0xff) and (x & 0x7fffffff) for x in regs)
            # ours
            for i in range(16):
                vm.lib.vm_set_reg40(vm.h, 0, i, regs[i] << 8)
            vm.set_reg("ASTATX", 0)
            vm.pc = PC
            st = vm.lib.vm_run(vm.h, 1)
            if st != 0:
                code, tpc, msg = vm.trap_info()
                vm.lib.vm_clear_trap(vm.h)
                stats["ours: trap"] += 1
                per_op[opname]["ours-trap"] += 1
                lines.append(f"{c:#08x}\t{opname}\tours trap: {msg}")
                break
            dests = dest if isinstance(dest, tuple) else (dest,)
            vals = val if isinstance(val, tuple) else (val,)
            for dreg, v in zip(dests, vals):
                if isinstance(dreg, str) or not isinstance(v, ST.Const):
                    stats["digikit: symbolic/MR"] += 1
                    continue
                mine = vm.lib.vm_get_reg40(vm.h, 0, dreg) >> 8
                if mine == v.value:
                    stats["agree"] += 1
                    per_op[opname]["agree"] += 1
                elif kind == "float" and special:
                    # denormal/NaN/inf inputs: SHARC flushes denormals to zero and returns all ones
                    # for NaN (PGR ch. 11); digikit uses IEEE behaviour
                    stats["differ on denormal/NaN/inf inputs (documented)"] += 1
                    per_op[opname]["special"] += 1
                elif "FLOAT" in opname and abs(mine - v.value) <= 1:
                    stats["FLOAT: 1 ulp, 40-bit rounding (PGR)"] += 1
                    per_op[opname]["float40"] += 1
                else:
                    stats["DISAGREE"] += 1
                    per_op[opname]["disagree"] += 1
                    if per_op[opname]["disagree"] <= 3:
                        lines.append(f"{c:#08x}\t{opname}\tR{dreg}: ours {mine:#010x} digikit {v.value:#010x} "
                                     f"inputs {[hex(x) for x in regs]}")
    out = ROOT / "out/T3.6/coverage/xcheck_compute.tsv"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n")
    print(f"{len(fields)} distinct compute fields in the DN2 code; trials per field {a.trials}")
    for k, v in stats.most_common():
        print(f"  {k}: {v}")
    print("per operation: agree / DISAGREE / special inputs / FLOAT 40-bit / digikit-unsupported / ours-trap")
    for op, c in sorted(per_op.items(), key=lambda kv: -sum(kv[1].values())):
        print(f"  {op:24s} {c['agree']:6d} {c['disagree']:6d} {c['special']:6d} {c['float40']:5d} "
              f"{c['digikit-unsupported']:4d} {c['ours-trap']:4d}")


if __name__ == "__main__":
    main()
