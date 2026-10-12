#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Build selache's C test cases (vendor/selache/xtest/cases/*.c) with selcc/selas/seld
into ELF executables under out/T3.6/cases/, for the interpreter's oracle tests.

    python3 tools/sharcvm/build_cases.py [--jobs N] [--only SUBSTR] [--lib-only]

Nothing is written inside vendor/. libsel is compiled once into out/T3.6/libsel/.
The harness main (tools/sharcvm/harness/hmain.c) stores test_main()'s result in the
global xt_result and returns it; selache's startup.s then reaches its `.halt` IDLE.
"""
import argparse, os, re, subprocess, sys, concurrent.futures as cf
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SEL = ROOT / "vendor/selache"
BIN = SEL / "target/release"
OUT = ROOT / "out/T3.6"
LIB = OUT / "libsel"
CASES_OUT = OUT / "cases"
HARNESS = Path(__file__).resolve().parent / "harness"
CC_FLAGS = ["-proc", "ADSP-21569", "-char-size-8", f"-I{SEL/'libsel/include'}", f"-I{SEL/'xtest'}"]


def run(cmd, cwd=None):
    r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True, cwd=cwd)
    return r.returncode, r.stdout + r.stderr


def build_lib():
    LIB.mkdir(parents=True, exist_ok=True)
    objs = []
    srcs = sorted((SEL / "libsel/src").glob("*/*.c")) + sorted((SEL / "libsel/src").glob("*/*.s"))
    for f in srcs:
        o = LIB / f"lib_{f.parent.name}_{f.stem}.doj"
        objs.append(o)
        if o.exists() and o.stat().st_mtime > f.stat().st_mtime:
            continue
        if f.suffix == ".c":
            s = o.with_suffix(".s")
            rc, msg = run([BIN / "selcc", *CC_FLAGS, "-S", "-o", s, f])
            if rc == 0:
                rc, msg = run([BIN / "selas", "-proc", "ADSP-21569", "-o", o, s])
        else:
            rc, msg = run([BIN / "selas", "-proc", "ADSP-21569", "-o", o, f])
        if rc:
            sys.exit(f"libsel build failed on {f}:\n{msg}")
    # harness main
    hs = LIB / "hmain.s"
    ho = LIB / "hmain.doj"
    rc, msg = run([BIN / "selcc", *CC_FLAGS, "-S", "-o", hs, HARNESS / "hmain.c"])
    if rc == 0:
        rc, msg = run([BIN / "selas", "-proc", "ADSP-21569", "-o", ho, hs])
    if rc:
        sys.exit(f"harness build failed:\n{msg}")
    arch = LIB / "libsel.dlb"
    if arch.exists():
        arch.unlink()
    rc, msg = run([BIN / "selar", "-c", arch, *objs])
    if rc:
        sys.exit(f"selar failed:\n{msg}")
    return ho, arch


def build_case(src, ho, arch, outdir=CASES_OUT, extra=()):
    name = Path(src).stem
    s = outdir / f"{name}.s"
    o = outdir / f"{name}.doj"
    x = outdir / f"{name}.dxe"
    for cmd in ([BIN / "selcc", *CC_FLAGS, *extra, "-S", "-o", s, src],
                [BIN / "selas", "-proc", "ADSP-21569", "-o", o, s],
                [BIN / "seld", "-proc", "ADSP-21569", "-T", SEL / "libsel/link.ldf", "-o", x, o, ho, arch]):
        rc, msg = run(cmd)
        if rc:
            return name, False, msg.strip()[-400:]
    return name, True, ""


def expect_of(src):
    m = re.search(r"@expect\s+(0x[0-9a-fA-F]+|\d+)", Path(src).read_text(errors="replace"))
    return int(m.group(1), 0) if m else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs", type=int, default=os.cpu_count())
    ap.add_argument("--only", default="")
    ap.add_argument("--lib-only", action="store_true")
    a = ap.parse_args()
    ho, arch = build_lib()
    if a.lib_only:
        return
    CASES_OUT.mkdir(parents=True, exist_ok=True)
    srcs = [p for p in sorted((SEL / "xtest/cases").glob("cctest_*.c")) if a.only in p.name]
    ok = bad = 0
    with cf.ThreadPoolExecutor(a.jobs) as ex, open(CASES_OUT / "build.log", "w") as log:
        for name, good, msg in ex.map(lambda p: build_case(p, ho, arch), srcs):
            if good:
                ok += 1
            else:
                bad += 1
                log.write(f"FAIL {name}: {msg}\n")
    print(f"built {ok}, failed {bad} (see {CASES_OUT/'build.log'})")


if __name__ == "__main__":
    main()
