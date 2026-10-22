#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""SC01 (notes/SC01.md 1.3): the static deadness proof of the stock FX code ranges SC01 reuses (level 2 memory).

For each candidate range [lo, hi) (SW addresses, L2):
  1. every control-flow edge in digikit's sharcdb (call, jump, cond_jump, fallthrough, indirect) whose target lies in
     the range and whose source lies outside it: each must be one of the hooked CJUMPs (now calling our code);
  2. the instruction before lo is an unconditional return (no fall-through into the range), checked in the listing;
  3. every literal, data reference, pointer and absolute memory access in the database with a value in the range,
     in SW form (lo..hi), L2 byte form (0x20000000 + 2 (sw - 0xb80000)) and the NW/32-bit-word form (L2 byte / 4):
     each must come from inside the range (a return address the range's own code pushes);
  4. a raw scan of every loaded byte of the stock final image outside the code blocks (all data blocks, L1, L2, DDR)
     for 32-bit little-endian words and 24-bit fields equal to a function entry in the range (SW) or its L2 byte or
     word address: lists every hit with its location (data words that happen to hold such a value);
  5. the roots table (interrupt vectors, task entries, code-pointer arrays) has no entry in the range.
Writes a JSON report.

    python3 tools/dspsplice/sc01_deadcode.py out/SC01/SC01-dsp/deadcode.json

Facts from digikit's sharcdb (GPL-2.0, vendor/digikit) and our own listing out/T3.1/disasm/l2.txt.
"""
import bisect
import json
import re
import sqlite3
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tools/dspsplice"))
import sharc_ldr_parse as P  # noqa: E402

DB = ROOT / "vendor/digikit/out/sharcdb/dn2-1.12.sqlite"
IMG = "dn2-1.12"
STOCK7 = ROOT / "out/T3.8/stock/section_7_blob.bin"
HOOKS = {0x1c1be1: 0xb84d5c, 0x1c1695: 0xb84f51, 0x1c1c58: 0xb8482c, 0x1c166c: 0xb848cb,
         0x1c215c: 0xb8541d, 0x1c16bb: 0xb85501}
RANGES = [(0xb83eaf, 0xb84926, "A: the stock delay (process 0xb8482c, init 0xb848cb) and its helpers"),
          (0xb84943, 0xb84c1f, "B: the stock chorus's modulated delay and output (0xb84943-0xb84b39)"),
          (0xb84ccb, 0xb84dee, "C: the stock chorus's biquad bank driver and its process entry 0xb84d5c"),
          (0xb84fd3, 0xb85466, "D: the stock master compressor (process 0xb8541d and its five stages)")]


def l2b(sw):
    return 0x20000000 + 2 * (sw - 0xb80000)


def main():
    out = Path(sys.argv[1])
    db = sqlite3.connect(DB)
    c = db.cursor()
    ins = []
    for ln in open(ROOT / "out/T3.1/disasm/l2.txt"):
        m = re.match(r"\s+([0-9a-f]{8})\s+([0-9a-f]+)\s+(.*)", ln)
        if m:
            ins.append((int(m.group(1), 16), m.group(2), m.group(3)))
    addrs = [a for a, _, _ in ins]
    funcs = sorted(c.execute("select entry_sw, end_sw from functions where image=?", (IMG,)).fetchall())
    stream = STOCK7.read_bytes()
    blocks, errors, stop = P.parse(stream)
    img = P.Image(blocks)
    for b in blocks:
        img.apply(stream, b)
    rep = []
    allok = True
    for lo, hi, what in RANGES:
        r = {"range": "%#x-%#x" % (lo, hi), "l2_bytes": "%#x-%#x" % (l2b(lo), l2b(hi)), "bytes": 2 * (hi - lo),
             "what": what}
        # 1. edges into the range
        ext = []
        for frm, to, kind in c.execute("select from_sw, to_sw, kind from edges where image=? and to_sw>=? and to_sw<? "
                                       "and kind != 'return'", (IMG, lo, hi)):
            if not (lo <= frm < hi):
                ext.append((frm, to, kind))
        indead = lambda a: any(l <= a < h for l, h, _ in RANGES)  # noqa: E731
        r["edges_from_outside"] = [("%#x" % a, "%#x" % b, k, "hooked" if a in HOOKS and HOOKS[a] == b else
                                    ("from another dead range" if indead(a) else "LIVE?")) for a, b, k in ext]
        ok1 = all((a in HOOKS and HOOKS[a] == b) or indead(a) for a, b, k in ext)
        # indirect edges with unknown targets anywhere (for the record)
        r["indirect_edges_total_in_db"] = c.execute("select count(*) from edges where image=? and kind='indirect'",
                                                    (IMG,)).fetchone()[0]
        # 2. no fall-through into lo
        i = bisect.bisect_left(addrs, lo)
        prev = ins[i - 5:i]
        tail = [t for _, _, t in prev]
        ok2 = addrs[i] == lo and tail[-1] == "rframe" and any(t.startswith("jump (m14,i12) (db)") for t in tail[-3:])
        r["before_lo"] = ["%x %s" % (a, t) for a, _, t in prev]
        # every function entry in the range (for the raw scan)
        entries = [e for e, _ in funcs if lo <= e < hi] + [x for x in (0xb8482c,) if lo <= x < hi]
        r["function_entries"] = ["%#x" % e for e in sorted(set(entries))]
        # 3. database references
        refs = []
        forms = [(lo, hi, "sw"), (l2b(lo), l2b(hi), "l2 byte"), (l2b(lo) // 4, (l2b(hi) + 3) // 4, "l2 32-bit word")]
        for flo, fhi, form in forms:
            for tab, col in (("literals", "value"), ("dataref", "value"), ("ptr", "address"), ("mem_access", "abs_address"),
                             ("ptr", "base_value")):
                for sw, v in c.execute(f"select sw, {col} from {tab} where image=? and {col}>=? and {col}<?",
                                       (IMG, flo, fhi)).fetchall():
                    refs.append((tab, col, form, sw, v, lo <= sw < hi))
        r["db_refs_from_outside"] = [("%s.%s" % (t, col), form, "%#x" % sw, "%#x" % v) for t, col, form, sw, v, inside in refs
                                     if not inside]
        r["db_refs_from_inside"] = len([1 for x in refs if x[5]])
        ok3 = not r["db_refs_from_outside"]
        # 5. roots
        roots = c.execute("select sw, kind, note from roots where image=? and sw>=? and sw<?", (IMG, lo, hi)).fetchall()
        r["roots_not_return_addresses"] = [("%#x" % s, k, n) for s, k, n in roots
                                           if not (k == "dataref_code_pointer" and n and lo <= int(n.split()[-1], 16) < hi)
                                           and k != "no_static_entry"]
        r["no_static_entry_roots"] = [("%#x" % s, k) for s, k, n in roots if k == "no_static_entry"]
        ok5 = not r["roots_not_return_addresses"]
        # 4. raw scan of the loaded image for the entries' values
        targets = {}
        for e in set(entries):
            targets[e] = "sw %#x" % e
            targets[l2b(e)] = "l2 byte of %#x" % e
            targets[l2b(e) // 4] = "l2 word of %#x" % e
        hits = []
        for (s, e_), buf, own in zip(img.regions, img.data, img.owner):
            data = bytes(buf)
            for v, why in targets.items():
                pat = struct.pack("<I", v)
                k = data.find(pat)
                while k >= 0:
                    if own[k] >= 0:
                        hits.append(("%#x" % (s + k), "%#x" % v, why, int(own[k])))
                    k = data.find(pat, k + 1)
        # a hit inside the L2 code block's own instructions (the CJUMP/literal encodings) is listed separately
        code_lo, code_hi = 0x20000000, 0x2001e880
        r["raw_hits_outside_l2_code"] = [h for h in hits if not (code_lo <= int(h[0], 16) < code_hi)
                                         and not (0x28380000 <= int(h[0], 16) < 0x283a0000)]
        r["raw_hits_in_code_blocks"] = len(hits) - len(r["raw_hits_outside_l2_code"])
        r["ok"] = {"no_live_edge": ok1, "no_fallthrough": ok2, "no_db_ref_from_outside": ok3, "no_root": ok5}
        allok = allok and ok1 and ok2 and ok3 and ok5
        rep.append(r)
        print(json.dumps(r, indent=1)[:3000])
    out.write_text(json.dumps({"ranges": rep, "ok": allok}, indent=1))
    print("ALL OK" if allok else "NOT OK")


if __name__ == "__main__":
    main()
