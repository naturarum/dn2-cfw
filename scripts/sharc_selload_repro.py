#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Check how closely selache's selload reproduces the stock section 7 blocks.

    python3 scripts/sharc_selload_repro.py

Takes the final memory image of the stock boot stream (via
sharc_ldr_parse.py), wraps chosen address ranges in a minimal SHARC ELF
executable, runs selload on it and compares the blocks selload writes with the
stock blocks for the same range: target, byte count, flags, BCODE, argument
and payload bytes.

  * L1 block 3 code (stock blocks 64-83). selload has no address mapping for
    L1 block 3 (its PM SW window ends at 0x190000, its byte window at
    0x320000), so the code is placed 0x80000 bytes lower, in L1 block 2, and
    target addresses are compared after that shift. The ranges are split
    into one ELF section per run of abutting stock blocks.
  * L1 block 1 data (stock blocks 38-55), once with selload's defaults and
    once with -MaxBlockSize 0x7FFFFFFC, which turns data FILL blocks back on.

Needs vendor/selache built. Writes the ELF and LDR files to out/T3.1/selload/.
"""

from __future__ import annotations

import struct
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import sharc_ldr_parse as P  # noqa: E402

ROOT = HERE.parent
SELLOAD = ROOT / "vendor" / "selache" / "target" / "release" / "selload"
OUT = ROOT / "out" / "T3.1" / "selload"


def swap_parcels(b: bytes) -> bytes:
    out = bytearray(b)
    for i in range(0, len(out) - 1, 2):
        out[i], out[i + 1] = out[i + 1], out[i]
    return bytes(out)


def make_exe(sections, entry_sw: int) -> bytes:
    """sections: (name, sh_addr, data, flags, align, entsize)."""
    shstr = b"\0" + b"".join(s[0].encode() + b"\0" for s in sections) + b".shstrtab\0"
    body, shdrs, name_off = b"", [bytes(40)], 1
    for name, addr, data, flags, align, entsize in sections:
        shdrs.append(struct.pack("<IIIIIIIIII", name_off, 1, flags, addr, 52 + len(body), len(data), 0, 0, align, entsize))
        name_off += len(name) + 1
        body += data
    stroff = 52 + len(body)
    body += shstr
    shoff = (52 + len(body) + 3) & ~3
    shdrs.append(struct.pack("<IIIIIIIIII", name_off, 3, 0, 0, stroff, len(shstr), 0, 0, 1, 0))
    hdr = b"\x7fELF" + bytes([1, 1, 1, 0]) + bytes(8) + struct.pack(
        "<HHIIIIIHHHHHH", 2, 0x85, 1, entry_sw, 0, shoff, 0, 52, 0, 0, 40, len(shdrs), len(shdrs) - 1)
    elf = hdr + body
    return elf + bytes(shoff - len(elf)) + b"".join(shdrs)


def run_selload(name: str, sections, entry_sw: int, extra=()):
    OUT.mkdir(parents=True, exist_ok=True)
    dxe, ldr = OUT / (name + ".dxe"), OUT / (name + ".ldr")
    dxe.write_bytes(make_exe(sections, entry_sw))
    r = subprocess.run([str(SELLOAD), "-proc", "ADSP-21569", "-bcode", "1", "-o", str(ldr), str(dxe), *extra],
                       capture_output=True, text=True)
    if r.returncode:
        raise SystemExit("selload failed: " + r.stderr)
    data = ldr.read_bytes()
    blocks, errors, _ = P.parse(data)
    if errors:
        raise SystemExit("selload output does not parse: %s" % errors)
    return data, [b for b in blocks if not ({"FIRST", "FINAL"} & set(b["flags"]))]


def compare(stock, sblocks, lo, hi, shift, new, nblocks, label):
    ours = [b for b in sblocks if lo <= b["index"] <= hi]
    same, diffs = 0, []
    for a, b in zip(ours, nblocks):
        pa = stock[a["payload_offset"]:a["payload_offset"] + a["payload_len"]]
        pb = new[b["payload_offset"]:b["payload_offset"] + b["payload_len"]]
        eq = (a["target_address"] - shift == b["target_address"] and a["byte_count"] == b["byte_count"]
              and a["fill"] == b["fill"] and pa == pb and a["argument"] == b["argument"]
              and (a["block_code"] & 0xFF00FFFF) == (b["block_code"] & 0xFF00FFFF))
        same += eq
        if not eq:
            diffs.append("stock blk%d 0x%08x +0x%x %s | selload 0x%08x +0x%x %s" % (
                a["index"], a["target_address"] - shift, a["byte_count"], ",".join(a["flags"]) or "-",
                b["target_address"], b["byte_count"], ",".join(b["flags"]) or "-"))
    print("%s: stock blocks %d-%d (%d), selload %d blocks, identical %d" % (
        label, lo, hi, len(ours), len(nblocks), same))
    for d in diffs[:8]:
        print("    " + d)
    return same == len(ours) == len(nblocks)


def main() -> int:
    if not SELLOAD.is_file():
        raise SystemExit("build selache first: %s missing" % SELLOAD)
    stock = P.DEFAULT_STREAM.read_bytes()
    sblocks, errors, _ = P.parse(stock)
    if errors:
        raise SystemExit(errors)
    img = P.Image(sblocks)
    for b in sblocks:
        img.apply(stock, b)

    # L1 block 3 code, moved to L1 block 2. Section split where stock blocks abut.
    shift = 0x00080000
    runs = [(0x28380000, 0x28380540), (0x28380540, 0x28380548), (0x28380548, 0x283825C0), (0x283825C4, 0x2839BFFC)]
    secs = [("code%d" % i, (s - shift - P.SYS_FABRIC) // 2, swap_parcels(img.read(s, e - s)), 7, 4, 1)
            for i, (s, e) in enumerate(runs)]
    entry = (0x283825C4 - shift - P.SYS_FABRIC) // 2
    new, nb = run_selload("l1b3_code_shifted", secs, entry)
    ok_code = compare(stock, sblocks, 64, 83, shift, new, nb, "L1 block 3 code (shifted to L1 block 2)")

    secs = [("data0", 0x282C0000 - P.SYS_FABRIC, img.read(0x282C0000, 0x1D52C), 3, 4, 0)]
    new, nb = run_selload("l1b1_data", secs, entry)
    compare(stock, sblocks, 38, 55, 0, new, nb, "L1 block 1 data, selload defaults")
    new, nb = run_selload("l1b1_data_mbs", secs, entry, ("-MaxBlockSize", "0x7FFFFFFC"))
    compare(stock, sblocks, 38, 55, 0, new, nb, "L1 block 1 data, -MaxBlockSize 0x7FFFFFFC")
    print("files in %s" % OUT)
    return 0 if ok_code else 1


if __name__ == "__main__":
    sys.exit(main())
