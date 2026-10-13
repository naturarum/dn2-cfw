#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""T6.3 CMP0: every indirect store in selcc's assembly, its address traced back through the straight-line code
before it. The sixth selcc miscompile (notes/T5.3 3.4) formed a store address from a caller-saved register
(R0-R7 in selcc's convention; R0 = the return value) kept across a call. This trace follows the registers that
form the store's I register backwards to a stack-slot load (DM (-k,I6)), an immediate, the return value right
after a call, or a callee-saved register (R8-R15); it flags a caller-saved register that is still needed when
the trace crosses a call, and reports a trace that reaches a branch target (a merge point) as unresolved.
    python3 tools/dspsplice/store_trace.py out/T6.3/CMP0-dsp/build/*.s

A function without calls that forms an address from its incoming arguments (R4, R8, R12, never rewritten in it)
counts as resolved. What is left UNRESOLVED is for a reader (CMP0: one, the first store of mbcomp_init, before
any call). Static and partial: it follows straight-line code only; tools/dspsplice/verify_cmp.py's store census
checks the run-time addresses."""
import re
import sys

CALLER = {f"R{i}" for i in range(8)}


def parse(path):
    out = []
    for raw in open(path).read().split("\n"):
        line = raw.split("//")[0].strip()
        if line:
            out.append(line)
    return out


def dests_srcs(ins):
    """(destination registers, source registers) of one (possibly parallel) instruction"""
    d, s = set(), set()
    body = ins.rstrip(";")
    for part in [p.strip() for p in re.split(r",(?![^()]*\))", body)]:
        if "=" not in part or part.startswith("IF"):
            s |= set(re.findall(r"\b[RFI]\d+\b", part))
            continue
        lhs, rhs = part.split("=", 1)
        lhs = lhs.strip()
        mreg = re.match(r"^[RFI]\d+$", lhs)
        if mreg:
            d.add(lhs.replace("F", "R"))
        else:
            s |= set(re.findall(r"\b[RFI]\d+\b", lhs))     # memory destination: its address registers
        s |= {x.replace("F", "R") for x in re.findall(r"\b[RFI]\d+\b", rhs)}
    return d, s


def trace(lines, i, regs):
    """walk back from line i with the registers still to explain"""
    need = set(regs)
    crossed_call = False
    j = i - 1
    while j >= 0 and need:
        ins = lines[j]
        if ins.endswith(":"):
            if ins.startswith(".L_ret_"):
                crossed_call = True
                bad = need & CALLER - {"R0"}
                if bad:
                    return "FLAG", f"{sorted(bad)} needed across the call at {ins}"
                j -= 1
                continue
            return "UNRESOLVED", f"reaches label {ins} needing {sorted(need)}"
        if ins.startswith("CJUMP"):
            j -= 1
            continue
        d, s = dests_srcs(ins)
        hit = need & d
        if hit:
            need -= hit
            if re.search(r"=\s*DM\s*\(\s*-?0x[0-9A-Fa-f]+\s*,\s*I6\s*\)", ins) or re.search(r"=\s*-?0x[0-9A-Fa-f]+\s*;", ins):
                pass                                    # a stack slot or an immediate: resolved
            else:
                srcs = {x for x in s if x not in ("I6", "I7")}
                if "R0" in srcs and crossed_call is False and any(lines[k].startswith(".L_ret_") for k in range(max(0, j - 2), j)):
                    srcs.discard("R0")
                need |= {x for x in srcs if not re.match(r"R(8|9|1[0-5])$", x)}
        j -= 1
    return ("OK" if not need else "UNRESOLVED"), ""


def functions(lines):
    """function name -> (first line, last line, has a call, registers written)"""
    out, cur, start = {}, None, 0
    for i, ins in enumerate(lines + ["END.:"]):
        if ins.endswith(".:") and not ins.startswith(".L"):
            if cur:
                body = lines[start:i]
                wr = set()
                for x in body:
                    if not x.endswith(":"):
                        wr |= dests_srcs(x)[0]
                out[cur] = (start, i, any(x.startswith("CJUMP") for x in body), wr)
            cur, start = ins[:-2], i
    return out


def main():
    total = {}
    for path in sys.argv[1:]:
        lines = parse(path)
        funcs = functions(lines)
        func = "?"
        for i, ins in enumerate(lines):
            if ins.endswith(".:") and not ins.startswith(".L"):
                func = ins[:-2]
            m = re.match(r"^(?:.*,\s*)?DM\s*\(\s*(I\d+)\s*,\s*M\d+\s*\)\s*(?:u=\d\s*)?=", ins) or \
                re.match(r"^DM\s*\(\s*-?0x[0-9A-Fa-f]+\s*,\s*(I\d+)\s*\)\s*=", ins)
            if not m or m.group(1) in ("I6", "I7"):
                continue
            ireg = m.group(1)
            # the I register's last assignment
            j = i - 1
            while j >= 0 and not re.match(rf"^{ireg}\s*=", lines[j]) and not lines[j].endswith(":"):
                j -= 1
            if j < 0 or lines[j].endswith(":"):
                verdict, why = "UNRESOLVED", f"{ireg} set before a label"
            else:
                _, src = dests_srcs(lines[j])
                verdict, why = trace(lines, j, {x for x in src if x not in ("I6", "I7")})
            if verdict == "UNRESOLVED" and func in funcs:
                _, _, has_call, wr = funcs[func]
                need = set(re.findall(r"R\d+", why))
                # a function without calls that never writes the argument registers it needs: the address is
                # formed from its incoming arguments (R4, R8, R12)
                if not has_call and need <= {"R4", "R8", "R12"} and not (need & wr):
                    verdict, why = "OK", "argument register of a function without calls"
            total[verdict] = total.get(verdict, 0) + 1
            if verdict != "OK":
                print(f"{path.split('/')[-1]}:{i + 1} {func}: {ins}  -> {verdict} {why}")
    print("indirect stores:", total)


if __name__ == "__main__":
    main()
