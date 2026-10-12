#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Disassemble SHARC+ VISA code from a boot stream with selache's seldump, and
measure whether the listing is coherent.

    python3 scripts/sharc_visa_disasm.py                  # default regions
    python3 scripts/sharc_visa_disasm.py --region main
    python3 scripts/sharc_visa_disasm.py --sw 0x1c12e2 0x1c1400 --name entry

seldump only reads ELF, so each region is wrapped in a minimal ELF32 object
(e_machine 0x85, one SHT_PROGBITS ALLOC|EXECINSTR section "seg_swco" with
sh_entsize 1, which makes seldump use its VISA decoder) whose sh_addr is the
region's short-word (VISA PC) address. selache reads VISA parcels big endian
while the boot stream stores each 16-bit parcel little endian, so every
parcel is byte-swapped first (selload does the reverse swap when it writes a
stream).

Coherence checks, per region:
  * decode coverage: instructions by width, "???" (unknown) rows
  * a second, independent width-only decoder driven by digikit's
    tools/sharcspec/decode_table.json (data only: masks, values, widths,
    longest-leading-fixed-bits rule), and where the two sweeps disagree
  * direct branch targets (call, cjump, jump 0x...): inside loaded memory,
    inside a disassembled region, on an instruction boundary
  * PC-relative jumps and DO-loop ends: on an instruction boundary
  * calling convention: "i12=dm (m7,i6) ... jump (m14,i12) (db) ... rframe"
    returns, and what precedes each call target (a function boundary)

Needs selache built (vendor/selache/target/release/seldump). Writes listings
and a JSON summary under out/T3.1/disasm/.
"""

from __future__ import annotations

import argparse
import bisect
import collections
import json
import re
import struct
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import sharc_ldr_parse as P  # noqa: E402

ROOT = HERE.parent
SELDUMP = ROOT / "vendor" / "selache" / "target" / "release" / "seldump"
DECODE_TABLE = ROOT / "vendor" / "digikit" / "tools" / "sharcspec" / "decode_table.json"
OUT = ROOT / "out" / "T3.1" / "disasm"

# Regions of Digitone II 1.12 section 7 (short-word addresses, end exclusive).
REGIONS = {
    "init": (0x1201F8, 0x12161C, "app0"),   # app 0 block 1: init code run by the INIT block
    "l1b3_low": (0x1C0000, 0x1C12E0, "final"),  # blocks 64-78 (startup at 0x1c02a4)
    "main": (0x1C12E2, 0x1CDFFE, "final"),  # blocks 79-83, entry 0x1c12e2
    "l2": (0xB80000, 0xB8F440, "final"),    # block 56, L2 0x20000000
    "l2ext": (0xB8F444, 0xB97866, "final"),  # block 57, L2 0x2001e888
}
DEFAULT_REGIONS = list(REGIONS)

ROW = re.compile(r"^\s+([0-9a-f]{8})\s+([0-9a-f]{4,12})\s+(.*)$")
ABS_BRANCH = re.compile(r"\b(cjump|call|jump)\s+0x([0-9a-f]+)\b")
REL_BRANCH = re.compile(r"\b(call|jump)\s+\(pc,\s*(-?)0x([0-9a-f]+)\)")
DO_REL = re.compile(r"\bdo \(pc,\s*(-?)0x([0-9a-f]+)\)\s*until")


def sw_to_loader(sw: int) -> int:
    if P.L2_SW_BASE <= sw < P.L2_SW_BASE + (P.L2_END - P.L2_START) // 2:
        return P.L2_START + 2 * (sw - P.L2_SW_BASE)
    return P.SYS_FABRIC + 2 * sw


def swap_parcels(b: bytes) -> bytes:
    out = bytearray(b)
    for i in range(0, len(out) - 1, 2):
        out[i], out[i + 1] = out[i + 1], out[i]
    return bytes(out)


def make_elf(code_be: bytes, sh_addr: int, name: bytes = b"seg_swco") -> bytes:
    shstr = b"\0" + name + b"\0.shstrtab\0"
    off_code = 52
    off_str = off_code + len(code_be)
    off_sh = (off_str + len(shstr) + 3) & ~3
    ident = b"\x7fELF" + bytes([1, 1, 1, 0]) + bytes(8)
    hdr = ident + struct.pack("<HHIIIIIHHHHHH", 1, 0x85, 1, 0, 0, off_sh, 0, 52, 0, 0, 40, 3, 2)
    sh = bytes(40)
    sh += struct.pack("<IIIIIIIIII", 1, 1, 6, sh_addr, off_code, len(code_be), 0, 0, 2, 1)
    sh += struct.pack("<IIIIIIIIII", 1 + len(name) + 1, 3, 0, 0, off_str, len(shstr), 0, 0, 1, 0)
    body = hdr + code_be + shstr
    return body + bytes(off_sh - len(body)) + sh


def seldump_rows(code_le: bytes, sw_base: int, work: Path, name: str):
    elf = work / (name + ".doj")
    elf.write_bytes(make_elf(swap_parcels(code_le), sw_base))
    res = subprocess.run([str(SELDUMP), "-ns", "seg_swco", str(elf)], capture_output=True, text=True)
    if res.returncode:
        raise SystemExit("seldump failed: %s" % res.stderr)
    (work / (name + ".txt")).write_text(res.stdout)
    rows = []
    for line in res.stdout.splitlines():
        m = ROW.match(line)
        if m:
            rows.append((int(m.group(1), 16), bytes.fromhex(m.group(2)), m.group(3).strip()))
    elf.unlink()
    return rows


class TableWidths:
    """Width-only VISA decoder over digikit's decode_table.json (data)."""

    def __init__(self, path: Path):
        t = json.loads(path.read_text())
        self.forms = []
        for f in t["forms"]:
            if not f.get("visa"):
                continue
            mask, value = int(f["mask"], 16), int(f["value"], 16)
            lead = 0
            for bit in range(47, -1, -1):
                if not mask >> bit & 1:
                    break
                lead += 1
            self.forms.append((lead, f["fixed_bits"], mask, value, f["width"], f["name"]))
        self.forms.sort(key=lambda x: (-x[0], -x[1]))

    def width(self, parcels: list[int]):
        frame = 0
        for i, v in enumerate(parcels + [0] * (3 - len(parcels))):
            frame |= v << (32 - 16 * i)
        for lead, fixed, mask, value, width, name in self.forms:
            if frame & mask == value and width // 16 <= len(parcels):
                return width // 16, name
        return None, None

    def sweep(self, code_le: bytes, sw_base: int):
        n = len(code_le) // 2
        words = struct.unpack("<%dH" % n, code_le[:2 * n])
        starts, unknown = [], 0
        i = 0
        while i < n:
            w, _ = self.width(list(words[i:i + 3]))
            starts.append(sw_base + i)
            if w is None:
                unknown += 1
                w = 1
            i += w
        return starts, unknown


def classify_prev(text: str) -> str:
    t = text.lower()
    if t.startswith("rframe"):
        return "rframe"
    if t == "nop":
        return "nop"
    if "rts" in t or "rti" in t:
        return "rts/rti"
    if re.search(r"\bjump\b", t) and not t.startswith("if "):
        return "jump"
    if t.startswith("???"):
        return "unknown"
    return "other"


def analyse(regions: dict, table: TableWidths | None, loaded) -> dict:
    """regions: name -> (sw_start, code_le, rows)."""
    starts_all = set()
    span = []
    for name, (sw, code, rows) in regions.items():
        starts_all.update(r[0] for r in rows)
        span.append((sw, sw + len(code) // 2))

    def in_regions(a):
        return any(s <= a < e for s, e in span)

    sweeps = {name: table.sweep(code, sw) for name, (sw, code, _) in regions.items()} if table else {}
    table_all = set()
    for st, _ in sweeps.values():
        table_all.update(st)

    report = {}
    for name, (sw, code, rows) in regions.items():
        r = {"sw_start": sw, "sw_end": sw + len(code) // 2, "bytes": len(code)}
        widths = collections.Counter(len(raw) * 8 for _, raw, _ in rows)
        unk = [(a, raw.hex(), t) for a, raw, t in rows if t.startswith("???")]
        r["rows"] = len(rows)
        r["by_width_bits"] = dict(sorted(widths.items()))
        r["unknown_rows"] = len(unk)
        r["unknown_bytes_pct"] = round(100.0 * sum(len(bytes.fromhex(h)) for _, h, _ in unk) / max(1, len(code)), 2)
        r["unknown_first_parcels"] = collections.Counter(h[:4] for _, h, _ in unk).most_common(8)
        texts = [t.lower() for _, _, t in rows]
        r["returns_jump_m14_i12"] = sum("jump (m14,i12)" in t for t in texts)
        r["rframe"] = sum(t.startswith("rframe") for t in texts)
        r["rts"] = sum(bool(re.search(r"\brts\b", t)) for t in texts)
        # Full ABI epilogue: i12=dm (m7,i6) within 6 rows before jump (m14,i12) (db),
        # and rframe within 2 rows after it.
        epi = 0
        for i, t in enumerate(texts):
            if "jump (m14,i12) (db)" in t:
                before = any("i12=dm (m7,i6)" in x for x in texts[max(0, i - 6):i])
                after = any(x.startswith("rframe") for x in texts[i + 1:i + 3])
                epi += before and after
        r["abi_epilogues"] = epi
        # Branch targets.
        abs_t, rel_t, do_t = [], [], []
        for a, raw, t in rows:
            tl = t.lower()
            for m in ABS_BRANCH.finditer(tl):
                if "(pc," in tl[m.start():m.end() + 4]:
                    continue
                abs_t.append((a, m.group(1), int(m.group(2), 16)))
            for m in REL_BRANCH.finditer(tl):
                off = int(m.group(3), 16) * (-1 if m.group(2) else 1)
                rel_t.append((a, m.group(1), a + off))
            for m in DO_REL.finditer(tl):
                off = int(m.group(2), 16)
                do_t.append((a, off & 0x7FFFFF, a + (off & 0x7FFFFF)))
        def stat(targets):
            n = len(targets)
            ld = sum(loaded(t) for *_, t in targets)
            inr = [t for *_, t in targets if in_regions(t)]
            onb = sum(t in starts_all for t in inr)
            res = {"count": n, "in_loaded_memory": ld, "in_disassembled_regions": len(inr),
                   "on_selache_boundary": onb}
            if table:
                res["on_table_boundary"] = sum(t in table_all for t in inr)
                res["on_selache_only"] = sum(t in starts_all and t not in table_all for t in inr)
                res["on_table_only"] = sum(t in table_all and t not in starts_all for t in inr)
                res["on_neither"] = sum(t not in table_all and t not in starts_all for t in inr)
            return res
        r["direct_branches"] = stat(abs_t)
        r["direct_branches_by_kind"] = dict(collections.Counter(k for _, k, _ in abs_t))
        r["pc_relative_branches"] = stat(rel_t)
        r["do_loop_ends_masked_7fffff"] = stat(do_t)
        r["do_loops"] = len(do_t)
        r["do_offset_bit23_set"] = sum(
            1 for _, _, t in rows if DO_REL.search(t.lower()) and int(DO_REL.search(t.lower()).group(2), 16) & 0x800000)
        # What precedes call targets that land inside this region?
        idx = {a: i for i, (a, _, _) in enumerate(rows)}
        calls = sorted({t for _, k, t in abs_t if k in ("call", "cjump")})
        prev = collections.Counter()
        first = collections.Counter()
        for t in calls:
            i = idx.get(t)
            if i is None:
                continue
            first[re.sub(r"0x[0-9a-f]+", "N", rows[i][2].lower())] += 1
            if i > 0:
                prev[classify_prev(rows[i - 1][2])] += 1
        r["call_targets_distinct"] = len(calls)
        r["call_targets_here_preceded_by"] = dict(prev.most_common())
        r["call_targets_here_first_insn"] = first.most_common(8)
        if table:
            tstarts, tunk = sweeps[name]
            s1 = [a for a, _, _ in rows]
            set1, set2 = set(s1), set(tstarts)
            r["table_decoder"] = {
                "instructions": len(tstarts),
                "unknown_parcels": tunk,
                "starts_shared": len(set1 & set2),
                "selache_only_starts": len(set1 - set2),
                "table_only_starts": len(set2 - set1),
            }
            # A disagreement starts at a shared start where the two decoders
            # pick different widths; it ends at the next shared start.
            shared = sorted(set1 & set2)
            by_addr = {a: (raw, t) for a, raw, t in rows}
            kinds, examples, episodes = collections.Counter(), {}, 0
            episode_at = {}
            for a in shared:
                raw, t = by_addr[a]
                off = 2 * (a - sw)
                w_tab, form = table.width(list(struct.unpack("<3H", code[off:off + 6].ljust(6, b"\0")))[
                    :max(1, min(3, (len(code) - off) // 2))])
                w_sel = len(raw) // 2
                if w_tab is not None and w_tab == w_sel:
                    continue
                if (a + w_sel) in set2 and w_tab is None:
                    continue
                episodes += 1
                key = "first parcel %s: selache %d parcels, table %s parcels (%s)" % (
                    raw.hex()[:4], w_sel, w_tab, form)
                episode_at[a] = key
                kinds[key] += 1
                examples.setdefault(key, "0x%06x %s  %s" % (a, raw.hex(), t))
            r["table_decoder"]["width_disagreements"] = episodes
            # Branch targets that land on a start only one decoder has vote
            # for that decoder in the episode (last shared start before it).
            votes = collections.defaultdict(lambda: [0, 0])
            ep_starts = sorted(episode_at)
            for *_, t in abs_t + rel_t + do_t:
                if not (sw <= t < sw + len(code) // 2):
                    continue
                in1, in2 = t in set1, t in set2
                if in1 == in2:
                    continue
                k = bisect.bisect_right(ep_starts, t) - 1
                if k < 0:
                    continue
                votes[episode_at[ep_starts[k]]][0 if in1 else 1] += 1
            r["table_decoder"]["disagreement_kinds"] = [
                {"kind": k, "count": c, "example": examples[k],
                 "branch_targets_favour_selache": votes[k][0], "branch_targets_favour_table": votes[k][1]}
                for k, c in kinds.most_common(15)]
        report[name] = r
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("stream", nargs="?", default=str(P.DEFAULT_STREAM))
    ap.add_argument("--region", action="append", choices=list(REGIONS))
    ap.add_argument("--sw", nargs=2, metavar=("START", "END"), help="short-word range (final image)")
    ap.add_argument("--name", default="custom")
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--no-table", action="store_true", help="skip the decode_table.json cross-check")
    args = ap.parse_args(argv)
    if not SELDUMP.is_file():
        raise SystemExit("build selache first: %s missing" % SELDUMP)

    data = Path(args.stream).read_bytes()
    blocks, errors, _ = P.parse(data)
    if errors:
        raise SystemExit("stream does not parse: %s" % errors)
    apps = P.applications(blocks, len(data))
    images = {"final": P.Image(blocks)}
    for b in blocks:
        images["final"].apply(data, b)
    if len(apps) > 1:
        a0 = blocks[:apps[0]["last_block"] + 1]
        images["app0"] = P.Image(a0)
        for b in a0:
            images["app0"].apply(data, b)

    if args.sw:
        sel = {args.name: (int(args.sw[0], 0), int(args.sw[1], 0), "final")}
    else:
        sel = {k: REGIONS[k] for k in (args.region or DEFAULT_REGIONS)}

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    regions = {}
    for name, (s, e, img) in sel.items():
        code = images[img].read(sw_to_loader(s), 2 * (e - s))
        if code is None:
            raise SystemExit("region %s 0x%x-0x%x is not fully loaded in image %s" % (name, s, e, img))
        regions[name] = (s, code, seldump_rows(code, s, out, name))

    final = images["final"]

    def loaded(sw):
        try:
            return final.read(sw_to_loader(sw), 2) is not None
        except KeyError:
            return False

    table = None if args.no_table or not DECODE_TABLE.is_file() else TableWidths(DECODE_TABLE)
    report = analyse(regions, table, loaded)
    summary = out / ("summary_%s.json" % "_".join(regions))
    summary.write_text(json.dumps(report, indent=1))
    for name, r in report.items():
        print("== %s: sw 0x%x-0x%x, %d bytes, %d rows %s, unknown %d rows (%.2f%% of bytes)" % (
            name, r["sw_start"], r["sw_end"], r["bytes"], r["rows"], r["by_width_bits"],
            r["unknown_rows"], r["unknown_bytes_pct"]))
        print("   returns jump (m14,i12): %d, rframe: %d, full ABI epilogues: %d, rts: %d" % (
            r["returns_jump_m14_i12"], r["rframe"], r["abi_epilogues"], r["rts"]))
        for k in ("direct_branches", "pc_relative_branches", "do_loop_ends_masked_7fffff"):
            print("   %s: %s" % (k, r[k]))
        print("   direct kinds: %s" % r["direct_branches_by_kind"])
        print("   call targets here preceded by: %s" % r["call_targets_here_preceded_by"])
        print("   call target first insn: %s" % r["call_targets_here_first_insn"][:4])
        if "table_decoder" in r:
            td = r["table_decoder"]
            print("   table decoder: %d insns, %d unknown parcels, shared starts %d, selache-only %d, table-only %d, %d width disagreements" % (
                td["instructions"], td["unknown_parcels"], td["starts_shared"], td["selache_only_starts"],
                td["table_only_starts"], td["width_disagreements"]))
            for k in td["disagreement_kinds"][:10]:
                print("     %4d  %s  [targets: selache %d, table %d]  e.g. %s" % (
                    k["count"], k["kind"], k["branch_targets_favour_selache"],
                    k["branch_targets_favour_table"], k["example"]))
    print("wrote %s and listings in %s" % (summary, out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
