#!/usr/bin/env python3
"""Cross-check digikit's SHARC+ decoder (as stored in its tools/sharcdb.py
database) against selache's seldump listings, instruction by instruction.

Inputs (both git-ignored, both derived from the same section-7 image):
  - digikit database: vendor/digikit/out/sharcdb/<image>.sqlite
  - selache listings: out/T3.1/disasm/*.txt from scripts/sharc_visa_disasm.py
    (lines "  <sw hex>   <raw hex>   <text>", raw in big-endian parcels)

What it measures, per listing region:
  1. instruction starts: shared, digikit-only, selache-only;
  2. width and raw-byte agreement at shared starts;
  3. direct branch targets (digikit `edges` vs selache's absolute or
     PC-relative operand) at shared starts;
  4. immediate loads (digikit `literals` vs selache "reg=0x...") at shared starts;
  5. the 48-bit Type 9a (indirect jump/call) register field: for each site
     where the two decoders name a different I register, which one has a
     prior write in the same function (digikit `regdef`).
It also writes side-by-side listings of chosen windows for reading by hand.

Stdlib only:
    python3 scripts/sharc_decoder_crosscheck.py \
        --db vendor/digikit/out/sharcdb/dn2-1.12.sqlite \
        --listings out/T3.1/disasm --out out/T3.1b \
        --window 0x1c12e2:16 --window 0x1c9d6b:40 --window 0x1c9fe7:24
"""

from __future__ import annotations

import argparse
import collections
import glob
import json
import os
import re
import sqlite3

LINE = re.compile(r"^\s+([0-9a-f]{8})\s+([0-9a-f]+)\s+(.*)$")
# selache operand forms for direct branches
ABS_BR = re.compile(r"\b(?:jump|call|cjump)\s+0x([0-9a-f]+)\b")
REL_BR = re.compile(r"\b(?:jump|call)\s+\(pc,(-?)0x([0-9a-f]+)\)")
IMM = re.compile(r"^(?:if [a-z ]+ )?([a-z]+[0-9]*)=(-?)0x([0-9a-f]+)$")


def parse_listing(path):
    rows = {}
    with open(path) as f:
        for line in f:
            m = LINE.match(line)
            if m:
                sw = int(m.group(1), 16)
                rows[sw] = (len(m.group(2)) // 2, m.group(2), m.group(3).strip())
    return rows


def sel_branch_target(sw, text):
    """(kind, value): kind 'abs' (printed as an absolute address) or 'rel'
    (printed as (pc,+-off), value already added to sw, not wrapped)."""
    m = ABS_BR.search(text)
    if m:
        return "abs", int(m.group(1), 16)
    m = REL_BR.search(text)
    if m:
        off = int(m.group(2), 16)
        return "rel", (sw - off if m.group(1) else sw + off)
    return None


def classify_branch(sw, sel, dk_targets):
    kind, v = sel
    if v in dk_targets:
        return "equal"
    if (v & 0xFFFFFF) in dk_targets:
        return "equal after 24-bit PM wrap (selache prints the unwrapped sum)"
    if kind == "abs" and ((sw + v) & 0xFFFFFF) in dk_targets:
        return "equal: selache prints a PC-relative offset without (pc,...)"
    return "different target"


def sel_immediate(text):
    m = IMM.match(text)
    if not m:
        return None
    v = int(m.group(3), 16)
    return m.group(1).upper(), (-v if m.group(2) else v) & 0xFFFFFFFF


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", required=True)
    ap.add_argument("--listings", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--window", action="append", default=[], help="SW:N side-by-side window (N digikit rows)")
    args = ap.parse_args()

    db = sqlite3.connect(args.db)
    image = db.execute("SELECT value FROM meta WHERE key='image'").fetchone()[0]
    dk = {sw: (w, raw, form, mn, fn) for sw, w, raw, form, mn, fn in db.execute(
        "SELECT sw, width, raw, form, mnemonic, function_sw FROM insn WHERE image=? AND aligned=1", (image,))}
    edges = collections.defaultdict(set)
    for f, t, kind in db.execute("SELECT from_sw, to_sw, kind FROM edges WHERE image=? AND to_sw IS NOT NULL "
                                 "AND kind IN ('call','jump','cond_jump')", (image,)):
        edges[f].add(t)
    lits = {}
    for sw, v, reg in db.execute("SELECT sw, value, dest_reg FROM literals WHERE image=? AND dest_reg IS NOT NULL",
                                 (image,)):
        lits[sw] = (reg, v & 0xFFFFFFFF)

    report = collections.OrderedDict()
    all_sel = {}
    for path in sorted(glob.glob(os.path.join(args.listings, "*.txt"))):
        region = os.path.splitext(os.path.basename(path))[0]
        sel = parse_listing(path)
        if not sel:
            continue
        all_sel.update(sel)
        lo, hi = min(sel), max(sel)
        dks = {sw for sw in dk if lo <= sw <= hi}
        shared = dks & set(sel)
        width_eq = sum(1 for sw in shared if dk[sw][0] == sel[sw][0])
        raw_eq = sum(1 for sw in shared if dk[sw][1].zfill(2 * dk[sw][0]) == sel[sw][1])
        width_diff = collections.Counter()
        for sw in shared:
            if dk[sw][0] != sel[sw][0]:
                width_diff["%s dk%d/sel%d" % (dk[sw][2], 8 * dk[sw][0], 8 * sel[sw][0])] += 1
        br_both = br_eq = 0
        br_bad = []
        br_kinds = collections.Counter()
        for sw in sorted(shared):
            st = sel_branch_target(sw, sel[sw][2])
            if st is None or sw not in edges:
                continue
            br_both += 1
            k = classify_branch(sw, st, edges[sw])
            br_kinds[k] += 1
            if k.startswith("equal"):
                br_eq += 1
            elif len(br_bad) < 10:
                br_bad.append((hex(sw), dk[sw][2], sel[sw][2], sorted(hex(t) for t in edges[sw])))
        im_both = im_eq = 0
        im_bad = []
        for sw in sorted(shared):
            si = sel_immediate(sel[sw][2])
            if si is None or sw not in lits:
                continue
            im_both += 1
            if si == lits[sw]:
                im_eq += 1
            elif len(im_bad) < 10:
                im_bad.append((hex(sw), sel[sw][2], lits[sw][0], hex(lits[sw][1])))
        report[region] = {
            "range": [hex(lo), hex(hi)],
            "digikit_starts": len(dks), "selache_starts": len(sel), "shared_starts": len(shared),
            "pct_of_digikit_shared": round(100 * len(shared) / max(1, len(dks)), 1),
            "shared_width_equal": width_eq, "shared_raw_equal": raw_eq,
            "width_disagreements_top": width_diff.most_common(8),
            "direct_branches_compared": br_both, "direct_branch_targets_equal": br_eq,
            "branch_classes": dict(br_kinds),
            "branch_mismatch_examples": br_bad,
            "immediates_compared": im_both, "immediates_equal": im_eq,
            "immediate_mismatch_examples": im_bad,
        }

    # Type 9a register field: who has a prior writer of the named I register?
    t9 = collections.Counter()
    t9_rows = []
    for sw, (w, raw, form, _mn, fn) in sorted(dk.items()):
        if not form.startswith("9a") or w != 6:
            continue
        word = int(raw, 16)
        if (word >> 40) != 0x08:
            continue
        dk_i = 8 + ((((word >> 32) & 1) << 2) | ((word >> 30) & 3))
        se_i = 8 + ((word >> 24) & 7)
        dk_db, se_db = (word >> 26) & 1, (word >> 32) & 1
        if fn is None:
            t9["outside any function"] += 1
            continue

        def has_def(reg):
            return db.execute("SELECT max(sw) FROM regdef WHERE image=? AND reg=? AND sw>=? AND sw<?",
                              (image, "I%d" % reg, fn, sw)).fetchone()[0]
        if dk_i == se_i:
            t9["same I register"] += 1
        else:
            a, b = has_def(dk_i), has_def(se_i)
            k = "both written" if a and b else "only digikit's written" if a else \
                "only selache's written" if b else "neither written"
            t9[k] += 1
            t9_rows.append({"sw": hex(sw), "raw": raw.zfill(12), "digikit": "I%d db=%d" % (dk_i, dk_db),
                            "selache": "I%d db=%d" % (se_i, se_db), "verdict": k,
                            "digikit_writer": hex(a) if a else None, "selache_writer": hex(b) if b else None})
        if dk_db != se_db:
            t9["delay bit differs"] += 1
    report["type9a_register_field"] = {"counts": dict(t9), "differing_sites": t9_rows}

    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "crosscheck.json"), "w") as f:
        json.dump(report, f, indent=1)
    sbs = []
    for spec in args.window:
        s, n = spec.split(":")
        start, n = int(s, 0), int(n, 0)
        rows = [sw for sw in sorted(dk) if sw >= start][:n]
        sbs.append("== window %s, %d digikit rows ==" % (hex(start), n))
        sbs.append("%-8s | %-14s %-13s %-55s | %-14s %s" % ("sw", "digikit raw", "form", "digikit", "selache raw", "selache"))
        for sw in rows:
            w, raw, form, mn, _fn = dk[sw]
            sr = all_sel.get(sw)
            sbs.append("%-8x | %-14s %-13s %-55s | %-14s %s" % (
                sw, raw.zfill(2 * w), form, (mn or "")[:55], sr[1] if sr else "-", sr[2] if sr else "(no start here)"))
        sbs.append("")
    with open(os.path.join(args.out, "side_by_side.txt"), "w") as f:
        f.write("\n".join(sbs) + "\n")
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
