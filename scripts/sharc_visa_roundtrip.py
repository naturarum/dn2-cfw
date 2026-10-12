#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Re-assemble selache's own disassembly with selas and compare the bytes.

    python3 scripts/sharc_visa_disasm.py          # first: writes the listings
    python3 scripts/sharc_visa_roundtrip.py [--regions main l2 init]

For every unique (bytes, text) row in out/T3.1/disasm/<region>.txt (rows that
are not "???"), assembles the text alone with selas -proc ADSP-21569 and
compares the section bytes with the original bytes (both big-endian parcels,
selache's ELF order). Outcomes:
    identical bytes
    different length          selas picked another width for the same text
    same length, different bytes   a field decodes and encodes differently
    selas rejects text        the disassembler printed text selas cannot parse
Takes about three minutes for the default regions. Writes
out/T3.1/disasm/roundtrip.json.
"""

from __future__ import annotations

import argparse
import collections
import json
import re
import struct
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SELAS = ROOT / "vendor" / "selache" / "target" / "release" / "selas"
DISASM = ROOT / "out" / "T3.1" / "disasm"
ROW = re.compile(r"^\s+([0-9a-f]{8})\s+([0-9a-f]{4,12})\s+(.*)$")


def elf_section(path: Path, name: str):
    d = path.read_bytes()
    shoff = struct.unpack_from("<I", d, 0x20)[0]
    shentsize, shnum, shstrndx = struct.unpack_from("<HHH", d, 0x2E)
    sh = [struct.unpack_from("<IIIIIIIIII", d, shoff + i * shentsize) for i in range(shnum)]
    so = sh[shstrndx][4]
    for s in sh:
        if d[so + s[0]:d.index(b"\0", so + s[0])].decode() == name:
            return d[s[4]:s[4] + s[5]]
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--regions", nargs="+", default=["main", "l2", "init"])
    args = ap.parse_args()
    uniq = {}
    for region in args.regions:
        for line in (DISASM / (region + ".txt")).read_text().splitlines():
            m = ROW.match(line)
            if m and not m.group(3).startswith("???"):
                uniq.setdefault((m.group(2), m.group(3).strip()), (region, int(m.group(1), 16)))
    results = collections.Counter()
    xor_bits = collections.Counter()
    shorter = longer = 0
    examples = collections.defaultdict(list)
    with tempfile.TemporaryDirectory() as tmp:
        src, obj = Path(tmp) / "x.s", Path(tmp) / "x.doj"
        for (raw, text), (region, addr) in uniq.items():
            src.write_text(".SECTION/CODE/DOUBLEANY seg_swco;\n %s;\n" % text)
            r = subprocess.run([str(SELAS), "-proc", "ADSP-21569", "-o", str(obj), str(src)], capture_output=True)
            got = elf_section(obj, "seg_swco") if r.returncode == 0 else None
            if got is None:
                kind = "selas rejects text"
            elif got.hex() == raw:
                kind = "identical bytes"
            elif len(got) != len(raw) // 2:
                kind = "different length"
                if len(got) < len(raw) // 2:
                    shorter += 1
                else:
                    longer += 1
            else:
                kind = "same length, different bytes"
                xor_bits["%d-bit word, xor 0x%x" % (len(raw) * 4, int(raw, 16) ^ int(got.hex(), 16))] += 1
            results[kind] += 1
            if kind != "identical bytes" and len(examples[kind]) < 10:
                examples[kind].append("%s 0x%06x %s  %s  -> %s" % (region, addr, raw, text, got.hex() if got else "-"))
    total = sum(results.values())
    report = {
        "regions": args.regions,
        "unique_rows": total,
        "results": {k: {"count": v, "pct": round(100.0 * v / total, 2)} for k, v in results.most_common()},
        "different_length_selas_shorter": shorter,
        "different_length_selas_longer": longer,
        "same_length_xor_patterns": xor_bits.most_common(15),
        "examples": examples,
    }
    (DISASM / "roundtrip.json").write_text(json.dumps(report, indent=1))
    print(json.dumps({k: report[k] for k in ("unique_rows", "results", "different_length_selas_shorter",
                                             "different_length_selas_longer")}, indent=1))
    for k, v in examples.items():
        print("== " + k)
        print("\n".join(v[:5]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
