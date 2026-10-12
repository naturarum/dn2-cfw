#!/usr/bin/env python3
"""Run a C snippet both natively and as selcc code in the interpreter; compare test_main().

    python3 tools/sharcvm/tests/snip.py 'return f2u(fx_log2(196.0f));' [--inc fx/fxmath.h] [-O1]
The snippet is the body of `unsigned test_main(void)`; helper f2u(float) -> bits is provided.
"""
import argparse, subprocess, sys, tempfile
from pathlib import Path
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE.parent)); sys.dont_write_bytecode = True
import build_cases as B   # noqa: E402
from vm import VM          # noqa: E402

def run(body, incs, opt, defs=""):
    d = ROOT / "out/T3.6/snip"
    d.mkdir(parents=True, exist_ok=True)
    src = d / "snip.c"
    src.write_text("#include <stdint.h>\n" + "".join(f'#include "{ROOT / i}"\n' for i in incs) + defs +
                   "\nstatic unsigned f2u(float f){ union {float f; unsigned u;} x; x.f = f; return x.u; }\n"
                   "unsigned test_main(void) {\n" + body + "\n}\n")
    # native
    nat = d / "snip_native"
    (d / "nmain.c").write_text('#include <stdio.h>\nunsigned test_main(void);\nint main(void){printf("%x\\n", test_main());return 0;}\n')
    subprocess.check_call(["clang", "-O2", "-ffp-contract=off", "-w", "-I", str(ROOT / "fx"), "-o", str(nat), str(src), str(d / "nmain.c"), "-lm"])
    n = int(subprocess.check_output([str(nat)]).decode().strip(), 16)
    # sharc
    ho, arch = B.LIB / "hmain.doj", B.LIB / "libsel.dlb"
    name, ok, msg = B.build_case(src, ho, arch, outdir=d, extra=[opt, f"-I{ROOT / 'fx'}"])
    if not ok:
        sys.exit("selcc build failed: " + msg)
    vm = VM()
    e = vm.load_elf(d / "snip.dxe")
    vm.pc = e.entry
    vm.run(10**8)
    s = vm.rd32(e.symbols["xt_result"])
    return n, s

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("body")
    ap.add_argument("--inc", action="append", default=[])
    ap.add_argument("-O", dest="opt", default="-O1")
    a = ap.parse_args()
    n, s = run(a.body, a.inc, a.opt if a.opt.startswith("-O") else "-O" + a.opt)
    import struct
    f = lambda u: struct.unpack("<f", struct.pack("<I", u))[0]
    print(f"native {n:#010x} ({f(n):.9g})  sharc {s:#010x} ({f(s):.9g})  {'SAME' if n == s else 'DIFFERENT'}")
