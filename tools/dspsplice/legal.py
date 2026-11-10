#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Silicon rules the assembler and our simulators accept but the SHARC+ chip does not, checked on our code in a DSP
build (out/NAME/NAME-dsp: build.json and the .dxe). The rules are digikit's (m-dwyer/digikit, patchkit
build/sharc_legal.rs; findings 49 section 10 and 91), each from the SHARC+ Core Programming Reference:

  loop end   the last 11 instructions of a hardware loop body (all of a shorter body) are 48-bit (PRM 4-42)
  DAG store  no DM store of a DAG1 register (I0-I7, M0-M7, L0-L7, B0-B7) through an index register, no PM store of a
             DAG2 register (Types 3a, 3b, 3d, 15a, 15b)
  DAG load   no post-modify load (Types 3a, 3b, 3d, u = 1) of a register of the index register's DAG

    python3 tools/dspsplice/legal.py NAME [NAME ...]      exit 1 if any rule is broken
"""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools/dspsplice"))
import audit as A  # noqa: E402

LOOP_END = 11
IDX, MOD = ("3a", "3b", "3d", "15a", "15b"), ("3a", "3b", "3d")


def dag_register(u):
    if u is None or not 0x10 <= u <= 0x4F:
        return None
    return f"{'IMLB'[(u >> 4) - 1]}{u & 0xF}", 1 if (u & 0xF) < 8 else 2


def problems(name):
    odir = ROOT / f"out/{name}/{name}-dsp"
    info = json.loads((odir / "build.json").read_text())
    labels = sorted((int(v, 16), k) for k, v in info["symbols"].items())

    def where(pc):
        return f"{pc:#x} ({max((l for l in labels if l[0] <= pc), default=(0, '?'))[1]})"
    out, loops, accesses = [], 0, 0
    for s, _ in A.our_code(odir):
        ins = list(A.decode_section(s))
        at = {pc: i for i, (pc, _) in enumerate(ins)}
        for i, (pc, d) in enumerate(ins):
            t, f = d.type_name.split("_")[0], d.fields
            if t in ("12a", "13a"):
                loops += 1
                rel = (f.get("reladdr[22:16]", 0) << 16) | f.get("reladdr[15:0]", 0)
                end = pc + (rel - 0x800000 if rel & 0x400000 else rel)
                if end not in at:
                    continue                      # as digikit: an end that is not an instruction start is not judged
                short = [p for p, x in ins[i + 1:at[end] + 1][-LOOP_END:] if x.length_bytes != 6]
                if short:
                    out.append(f"{where(pc)}: loop ending {end:#x} has {len(short)} VISA instruction(s) in its last "
                               f"{LOOP_END}, first at {short[0]:#x}")
            if t in IDX:
                accesses += 1
                r = dag_register(f.get("ureg"))
                if r is None or f.get("g") is None or r[1] != (1 if f["g"] == 0 else 2):
                    continue
                if f.get("d") == 1:
                    out.append(f"{where(pc)}: {'DM' if r[1] == 1 else 'PM'} store of {r[0]} through its own DAG")
                elif t in MOD and f.get("u") == 1:
                    out.append(f"{where(pc)}: post-modify load of {r[0]} through its own DAG")
    return out, loops, accesses


def main():
    bad = 0
    for name in sys.argv[1:]:
        p, loops, acc = problems(name)
        print(f"{name}: {loops} loops, {acc} indexed accesses: {'ok' if not p else f'{len(p)} problem(s)'}")
        for x in p:
            print("   " + x)
        bad += len(p)
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
