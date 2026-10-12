#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""sharc_cost.py - static instruction count for selcc's SHARC+ output (T3.11).

Same method as T3.4's scripts/t34_cost.py, so the numbers compare with the
stock effects: every instruction of a function counts once, times the trip
counts of the loops around it; a call adds the callee's count. Both arms of
every branch are counted, so this is an upper bound for straight-line code.
No stalls, no DDR latency, no multi-issue.

    python3 sharc_cost.py OUTDIR/tapedelay.s OUTDIR/mbcomp.s

Loops are found as backward jumps inside a function. Their trip counts are
given below per function, in the order the loop headers appear.
"""
import re
import sys

BLOCK = 32
# trip counts per function, loops in order of their header label
TRIPS = {
    "td_run": [BLOCK, 2],           # samples, extra heads
    "td_control": [2, 2],           # heads, denormal flush
    "mb_run": [BLOCK, 2, 2],        # samples, channels, sidechain channels
    "mb_control": [3, 2],           # bands, denormal flush
    "tapedelay_process": [1],       # one control block per 32-sample call
    "mbcomp_process": [1],
    "flanger_process": [BLOCK],
}

# called only on an event or a knob move, not every block: left out of the
# "steady" figure, kept in the "knob moving" figure
RARE = {"td_crackle_event", "td_tone_coefs", "td_targets", "mb_targets", "mb_xover_coefs"}

INSN = re.compile(r"^\s+(?!\.)(\S.*?);?\s*$")
LABEL = re.compile(r"^(\.?[A-Za-z_][\w.]*):\s*$")
CALL = re.compile(r"\bCJUMP\s+([\w]+)\.\s")
JUMP = re.compile(r"\bJUMP\s+(\.L[\w]+)")


def parse(path):
    funcs, cur = {}, None
    for line in open(path):
        line = line.rstrip("\n")
        if not line.strip() or line.startswith(".SECTION") or line.lstrip().startswith(("//", "/*", ".")) and not LABEL.match(line):
            if LABEL.match(line):
                pass
            else:
                continue
        m = LABEL.match(line)
        if m:
            name = m.group(1)
            if not name.startswith(".L"):
                cur = name.rstrip(".")
                funcs[cur] = []
            elif cur is not None:
                funcs[cur].append(("label", name))
            continue
        if cur is not None and INSN.match(line):
            funcs[cur].append(("insn", line.strip()))
    return funcs


DO = re.compile(r"\bDO\s+(\.L[\w]+)\s+UNTIL")


def loops(body):
    """backward jumps and hardware DO loops -> (start_index, end_index) spans"""
    pos = {}
    for i, (k, v) in enumerate(body):
        if k == "label":
            pos[v] = i
    spans = []
    for i, (k, v) in enumerate(body):
        if k == "insn":
            m = JUMP.search(v)
            if m and m.group(1) in pos and pos[m.group(1)] < i:
                spans.append((pos[m.group(1)], i))
            m = DO.search(v)
            if m and m.group(1) in pos:
                # the DO label marks the loop's last instruction, which follows it
                spans.append((i + 1, pos[m.group(1)] + 1))
    # one span per header (the outermost back edge to it)
    best = {}
    for s, e in spans:
        best[s] = max(best.get(s, -1), e)
    return sorted(best.items())


memo = {}
SKIP_RARE = False


def cost(funcs, name, stack=()):
    key = (name, SKIP_RARE)
    if key in memo:
        return memo[key]
    if SKIP_RARE and name in RARE:
        return 0
    if name not in funcs or name in stack:
        return 0
    body = funcs[name]
    sp = loops(body)
    trips = TRIPS.get(name, [])
    mult = [1] * len(body)
    for j, (s, e) in enumerate(sp):
        t = trips[j] if j < len(trips) else 1
        for i in range(s, e + 1):
            mult[i] *= t
    total = 0
    for i, (k, v) in enumerate(body):
        if k != "insn":
            continue
        total += mult[i]
        m = CALL.search(v + " ")
        if m:
            total += mult[i] * cost(funcs, m.group(1), stack + (name,))
    memo[key] = total
    return total


def main():
    funcs = {}
    for p in sys.argv[1:]:
        funcs.update(parse(p))
    rows = []
    for name in sorted(funcs):
        n = sum(1 for k, _ in funcs[name] if k == "insn")
        sp = loops(funcs[name])
        rows.append((name, n, len(sp), cost(funcs, name)))
    print(f"{'function':24s} {'static insns':>12s} {'loops':>5s} {'count with callees, loops':>26s}")
    for name, n, nl, c in rows:
        print(f"{name:24s} {n:12d} {nl:5d} {c:26d}")
    global SKIP_RARE
    for top, run, ctl in (("tapedelay", "td_run", "td_control"), ("mbcomp", "mb_run", "mb_control"),
                          ("flanger", "fl_run", "fl_control")):
        if run not in funcs or top == "flanger":
            if top == "flanger":
                # fl_run and fl_control are inlined into flanger_process (each called once), and the
                # static count multiplies nested loops it cannot bound: only the dynamic count is meaningful
                print(f"\n{top}: fl_run is inlined into {top}_process; static per-block count not computed")
                continue
            # since 2026-10-06 selcc inlines td_run/mb_run (called once) into *_process, and the
            # static loop-trip guess no longer applies: see the dynamic counts below
            print(f"\n{top}: {run} is inlined into {top}_process; static per-block count not computed")
            continue
        print(f"\n{top}:")
        for skip, what in ((True, "steady (knobs still, no crackle event)"), (False, "a knob moving every block, a crackle event every sample (upper bound)")):
            SKIP_RARE = skip
            r = cost(funcs, run)
            k = cost(funcs, ctl) if ctl else 0
            print(f"  {what}: {run} {r} + {ctl} {k} = {r + k} instructions per 32-sample block ({(r + k) / BLOCK:.0f} per sample)")
        SKIP_RARE = False
        for name in (run, ctl):
            if name:
                print(f"  loops in {name}: {loops(funcs[name])} trips {TRIPS.get(name)}")


if __name__ == "__main__":
    main()
