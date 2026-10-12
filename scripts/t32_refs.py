#!/usr/bin/env python3
"""T3.2: who refers to which memory, from code (digikit sharcdb) and from data words (t32_load images).

    python3 scripts/t32_refs.py ranges            # references into every final-image segment and gap
    python3 scripts/t32_refs.py range LO HI       # list the references into [LO, HI)
    python3 scripts/t32_refs.py mmr               # system and core MMR references, grouped by 4 KiB page
    python3 scripts/t32_refs.py datawords LO HI   # 32-bit words in loaded data whose value is in [LO, HI)

Stdlib only. Needs out/T3.2/mem/ (run scripts/t32_load.py first) and the sharcdb.

Code references are the union of:
  literals    every immediate the decoder saw (16a/17a/... forms), any destination register
  mem_access  absolute DM/PM operands (direct forms)
  ptr         addresses digikit resolved from a literal base register plus an offset
Values are core byte addresses (see notes/T3.2-sharc-memmap.md section 0).
"""

import json
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "vendor/digikit/out/sharcdb/dn2-1.12.sqlite"
MEM = ROOT / "out/T3.2/mem"
IMG = "dn2-1.12"


def db():
    return sqlite3.connect(str(DB))


def func_of(c, sw):
    r = c.execute("SELECT function_sw FROM insn WHERE image=? AND sw=?", (IMG, sw)).fetchone()
    return r[0] if r and r[0] is not None else None


def code_refs(c, lo, hi):
    """(sw, kind, value) for every code reference with lo <= value < hi."""
    out = []
    for sw, v, form, reg in c.execute(
            "SELECT sw, value, form, dest_reg FROM literals WHERE image=? AND value>=? AND value<?", (IMG, lo, hi)):
        out.append((sw, "literal %s %s" % (form, reg or ""), v))
    for sw, v, d, w, sp in c.execute(
            "SELECT sw, abs_address, direction, width, space FROM mem_access WHERE image=? AND abs_address>=? "
            "AND abs_address<?", (IMG, lo, hi)):
        out.append((sw, "%s %s %s" % (sp, d, w), v))
    for sw, v, d, w, b in c.execute(
            "SELECT sw, address, direction, width, base_reg FROM ptr WHERE image=? AND address>=? AND address<?",
            (IMG, lo, hi)):
        out.append((sw, "ptr %s %s via %s" % (d, w, b), v))
    return out


def load_mem():
    m = json.loads((MEM / "memmap.json").read_text())
    regions = []
    for r in m["regions"]:
        regions.append((int(r["core_start"], 16), (MEM / r["file"]).read_bytes(), r))
    return m, regions


def data_words(regions, lo, hi, data_only=True):
    """(address, value, kind) of aligned 32-bit words in written data segments with lo <= value < hi."""
    out = []
    for base, buf, r in regions:
        for s in r["segments"]:
            k = s["kind"]
            if data_only and not (k.startswith("initialized data") or k == "init-program data"):
                continue
            a, e = int(s["start"], 16), int(s["end"], 16)
            for x in range(a + (-a % 4), e - 3, 4):
                v = int.from_bytes(buf[x - base:x - base + 4], "little")
                if lo <= v < hi:
                    out.append((x, v, k))
    return out


def cmd_ranges():
    c = db()
    m, regions = load_mem()
    print("%-23s %-34s %8s %6s %6s %6s" % ("range", "kind", "bytes", "code", "funcs", "dataw"))
    for base, buf, r in regions:
        segs = r["segments"]
        # merge adjacent segments of the same kind
        merged = []
        for s in segs:
            a, e = int(s["start"], 16), int(s["end"], 16)
            k = s["kind"]
            if merged and merged[-1][2] == k and merged[-1][1] == a and k == "unwritten":
                merged[-1][1] = e
            else:
                merged.append([a, e, k, s["block"]])
        for a, e, k, b in merged:
            refs = code_refs(c, a, e)
            funcs = {func_of(c, sw) for sw, _, _ in refs}
            dw = data_words(regions, a, e) if e - a < 0x200000 else []
            print("0x%08x-0x%08x %-34s %8d %6d %6d %6d" % (
                a, e, ("%s (blk %s)" % (k, b))[:34], e - a, len(refs), len(funcs), len(dw)))


def cmd_range(lo, hi):
    c = db()
    m, regions = load_mem()
    for sw, kind, v in sorted(code_refs(c, lo, hi), key=lambda t: (t[2], t[0])):
        f = func_of(c, sw)
        print("code  0x%08x  sw 0x%06x  fn %s  %s" % (v, sw, "0x%06x" % f if f else "-", kind))
    for a, v, k in data_words(regions, lo, hi):
        print("data  0x%08x  at 0x%08x (%s)" % (v, a, k))


MMR_NAMES = [
    # (start, end, name, source) -- see notes/T3.2-sharc-memmap.md section 3 for the confidence of each
    (0x31002000, 0x31002800, "SPORT0-7 A/B (0x80 each)", "digikit: SPORT4A 0x31002400"),
    (0x31003000, 0x31003400, "UART0", "selache xtest/uart.c"),
    (0x31004000, 0x31004800, "PORT A/B/C (0x80 each), PADS", "selache xtest/uart.c: PORTA_FER/MUX"),
    (0x31005000, 0x31005400, "PINT0-2 (pin interrupts)", "inferred"),
    (0x31018000, 0x31019000, "TIMER0", "selache xtest/xtest_timer.c"),
    (0x3102d000, 0x3102e000, "DMA channels of SPI0-2 (TX/RX pairs)", "inferred from the SPI table 0x268cd8"),
    (0x3102e000, 0x31031000, "SPI0 0x3102e000, SPI1 0x3102f000, SPI2 0x31030000", "inferred from the SPI table"),
    (0x31022000, 0x31024000, "DMA channels", "digikit: DMA10 0x31023000"),
    (0x31070000, 0x31072000, "DMC0 (DDR controller) and its PHY", "inferred"),
    (0x31089000, 0x3108a000, "SEC0 (GCTL/GSTAT/.. 0x000, core interface 0x400, sources 0x800+8n)", "inferred"),
    (0x3108b000, 0x3108c000, "SPU0", "inferred"),
    (0x3108c000, 0x3108d000, "RCU0", "inferred"),
    (0x3108f000, 0x31090000, "CDU0 (clock distribution)", "inferred"),
    (0x310c9000, 0x310c9300, "DAI0 / SRU0 routing (CLK/DAT/FS/PIN/PBEN)", "Linux sc5xx-dai.h offsets"),
    (0x310c9300, 0x310c9400, "PCG A/B (inferred by symmetry with PCG-C/D)", "inferred"),
    (0x310ca000, 0x310ca300, "DAI1 / SRU1 routing (same offsets as DAI0)", "Linux sc5xx-dai.h offsets"),
    (0x310ca300, 0x310ca400, "PCG C/D (CTLC0, CTLC1, CTLD0, CTLD1, PW2, SYNC2)", "digikit: PCG-C 0x310ca300"),
    (0x00030000, 0x00040000, "core MMRs (SYSCTL 0x30024, SHDBG_SECI_ID 0x300eb, BTB/cache 0x31000..)", "digikit findings 06"),
]


def mmr_name(a):
    for s, e, n, src in MMR_NAMES:
        if s <= a < e:
            return n
    return "?"


def cmd_mmr():
    c = db()
    m, regions = load_mem()
    rows = defaultdict(list)
    # system MMRs: every referenced one lies in 0x31000000-0x310fffff; literals above that
    # (0x3f800000 = 1.0f etc.) are float constants
    for lo, hi in ((0x30000, 0x40000), (0x31000000, 0x31100000)):
        for sw, kind, v in code_refs(c, lo, hi):
            rows[v].append(("code", sw, kind))
        if lo == 0x31000000:
            # driver tables live in L1 data; L2/DDR words in this range are sample or table data
            for a, v, k in [t for t in data_words(regions, 0x31000000, 0x31100000) if t[0] < 0x400000]:
                rows[v].append(("data", a, k))
    pages = defaultdict(list)
    for v in rows:
        pages[v & ~0xFFF if v >= 0x1000000 else v & ~0xFF].append(v)
    # core MMRs: keep a value only if some instruction accesses it directly (DM/PM absolute)
    # or it is loaded as a literal into a register and then used as an MMR address
    direct = {v for v, rs in rows.items() for k, w, how in rs if not how.startswith("literal")}
    for v in list(rows):
        if v < 0x40000 and v not in direct and v not in (0x30024, 0x3e000, 0x31000, 0x3c000, 0x3d100,
                                                         0x3d300, 0x3d500, 0x3e400):
            del rows[v]
    pages = defaultdict(list)
    for v in rows:
        pages[v & ~0xFFF if v >= 0x1000000 else v & ~0xFF].append(v)
    for p in sorted(pages):
        print("== page 0x%08x  %s" % (p, mmr_name(p)))
        for v in sorted(pages[p]):
            sites, funcs, dws = set(), set(), []
            for kind, where, how in rows[v]:
                if kind == "code":
                    sites.add(where)
                    f = func_of(c, where)
                    funcs.add("%06x" % f if f else "sw%06x" % where)
                else:
                    dws.append("0x%08x" % where)
            fl = sorted(funcs)
            print("  0x%08x  %3d insns in %2d fns: %s%s%s" % (
                v, len(sites), len(fl), " ".join(fl[:10]), " ..." if len(fl) > 10 else "",
                ("  data words at " + " ".join(dws[:6])) if dws else ""))


def main():
    a = sys.argv[1:]
    if not a or a[0] == "ranges":
        cmd_ranges()
    elif a[0] == "range":
        cmd_range(int(a[1], 16), int(a[2], 16))
    elif a[0] == "mmr":
        cmd_mmr()
    elif a[0] == "datawords":
        m, regions = load_mem()
        for x, v, k in data_words(regions, int(a[1], 16), int(a[2], 16)):
            print("0x%08x: 0x%08x  (%s)" % (x, v, k))


if __name__ == "__main__":
    main()
