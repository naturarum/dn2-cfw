#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Run selache's C test cases (built by build_cases.py) in the interpreter and compare
test_main()'s result with the case's /* @expect */ value.

    python3 tools/sharcvm/run_cases.py [--only SUBSTR] [--jobs N] [--limit INSNS] [-v]

Writes out/T3.6/cases/results.tsv (name, status, expect, got, instructions, detail)
and prints a summary: pass/fail/trap counts and the most common trap reasons.
"""
import argparse
import os
import re
import sys
import time
import concurrent.futures as cf
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.dont_write_bytecode = True
CASES = ROOT / "out/T3.6/cases"
SRC = ROOT / "vendor/selache/xtest/cases"


def expect_of(name):
    m = re.search(r"@expect\s+(0x[0-9a-fA-F]+|\d+)", (SRC / f"{name}.c").read_text(errors="replace"))
    return int(m.group(1), 0) if m else None


def run_one(args):
    import memguard
    r = _run_one(args)
    memguard.check()
    return r


def _run_one(args):
    name, limit = args
    from vm import VM, VMError, ST_IDLE
    exp = expect_of(name)
    t0 = time.time()
    try:
        vm = VM()
        vm.lib.vm_set_align_strict(vm.h, int(os.environ.get("SHARCVM_ALIGN", "0")))
        e = vm.load_elf(CASES / f"{name}.dxe")
        vm.pc = e.entry
        st = vm.run(limit)
        got = vm.rd32(e.symbols["xt_result"])
        done = vm.rd32(e.symbols["xt_done"])
        n = vm.icount
        if st != ST_IDLE or not done:
            return name, "TIMEOUT" if st == 0 else "NOHALT", exp, got, n, f"status {st} pc {vm.pc:#x}", time.time() - t0
        ok = exp is not None and (got & 0xffffffff) == (exp & 0xffffffff)
        return name, "PASS" if ok else "FAIL", exp, got, n, "", time.time() - t0
    except VMError as ex:
        return name, "TRAP", exp, None, 0, str(ex), time.time() - t0
    except Exception as ex:   # noqa
        return name, "ERROR", exp, None, 0, f"{type(ex).__name__}: {ex}", time.time() - t0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    ap.add_argument("--jobs", type=int, default=os.cpu_count())
    ap.add_argument("--limit", type=int, default=50_000_000)
    ap.add_argument("-v", action="store_true")
    ap.add_argument("--out", default=str(CASES / "results.tsv"))
    a = ap.parse_args()
    import vm as _vm
    _vm.build_lib()
    names = sorted(p.stem for p in CASES.glob("cctest_*.dxe") if a.only in p.stem)
    res = []
    t0 = time.time()
    import memguard
    with cf.ProcessPoolExecutor(a.jobs, max_tasks_per_child=memguard.TASKS_PER_CHILD) as ex:
        for r in memguard.pmap(ex, run_one, [(n, a.limit) for n in names], chunksize=4):
            res.append(r)
            if a.v and r[1] != "PASS":
                print(r[0], r[1], hex(r[2]) if r[2] is not None else None,
                      hex(r[3]) if isinstance(r[3], int) else r[3], r[5][:200])
    st = Counter(r[1] for r in res)
    reasons = Counter()
    for r in res:
        if r[1] in ("TRAP", "ERROR", "NOHALT", "TIMEOUT"):
            msg = re.sub(r"0x[0-9a-f]+", "#", r[5])
            msg = re.sub(r"fields=\{.*", "", msg)
            reasons[msg[:150]] += 1
    insns = sum(r[4] for r in res)
    secs = sum(r[6] for r in res)
    with open(a.out, "w") as f:
        f.write("name\tstatus\texpect\tgot\tinsns\tseconds\tdetail\n")
        for r in res:
            f.write(f"{r[0]}\t{r[1]}\t{r[2]}\t{r[3]}\t{r[4]}\t{r[6]:.3f}\t{r[5]}\n")
    print(f"{len(res)} cases in {time.time() - t0:.1f} s wall: " + ", ".join(f"{k} {v}" for k, v in st.most_common()))
    print(f"instructions executed: {insns:,} ({insns / max(secs, 1e-9):,.0f} insn/s summed over workers, incl. decode)")
    for msg, n in reasons.most_common(40):
        print(f"  {n:5d}  {msg}")


if __name__ == "__main__":
    main()
