#!/usr/bin/env python3
"""Parse an ADSP-2156x (SHARC+) boot stream (LDR) such as Digitone II section 7.

    python3 scripts/sharc_ldr_parse.py                       # section 7 of 1.12
    python3 scripts/sharc_ldr_parse.py STREAM --out DIR
    python3 scripts/sharc_ldr_parse.py --digikit vendor/digikit   # cross-check
    python3 scripts/sharc_ldr_parse.py some.ldr --no-image   # e.g. selload output

Stdlib only. Writes under --out (default out/T3.1/):
    blocks.json            every block, the validation results, the regions
    image/final/<start>-<end>.bin   memory after the whole stream (last write
                           wins), one file per contiguous written range
    image/app<N>/...       memory at the end of application N (before the
                           next application's FIRST block), when the stream
                           holds more than one application
    segments_final.tsv     which block supplied each byte range of the final
                           image (after overwrites)
    payloads/blkNN_<addr>.bin   raw payloads (with --dump-payloads)

Block grammar (16-byte header, little endian, then the payload):
    +0  u32 block_code  bits 3:0 BCODE, 15:4 BFLAG, 23:16 HDRCHK, 31:24 HDRSIGN
    +4  u32 target_address
    +8  u32 byte_count
    +12 u32 argument
HDRCHK makes the XOR of all 16 header bytes zero. HDRSIGN 0xAD/0xAC/0xAB names
core 0/1/2. A FILL block has no payload: byte_count bytes of the 32-bit
argument (little endian, repeated) are written. Every other block is followed
by exactly byte_count payload bytes.

Exit status is 0 only when every check passes.
"""

from __future__ import annotations

import argparse
import array
import hashlib
import importlib.util
import json
import os
import random
import re
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_STREAM = ROOT / "ele3_out" / "04_type7.unpacked.bin"
DEFAULT_OUT = ROOT / "out" / "T3.1"

HEADER_LEN = 16
# Block flag bits of block_code (ADSP-2156x boot ROM). Bit 9 is reserved.
BFLAGS = {
    4: "SAVE",
    5: "AUX",
    8: "FILL",
    10: "CALLBACK",
    11: "INIT",
    12: "IGNORE",
    13: "INDIRECT",
    14: "FIRST",
    15: "FINAL",
}
FLAG_MASK = 0xFFF0
KNOWN_FLAG_MASK = sum(1 << b for b in BFLAGS)
HDRSIGN = {0xAD: 0, 0xAC: 1, 0xAB: 2}

# Memory map of the ADSP-21569 as the boot DMA sees it (byte addresses).
# L1 is reached through the system fabric alias 0x28000000 + byte address.
SYS_FABRIC = 0x28000000
L1_BLOCKS = [  # (name, first byte address, end byte address), core-local
    ("L1 block 0", 0x00240000, 0x00270000),
    ("L1 block 1", 0x002C0000, 0x002F0000),
    ("L1 block 2", 0x00300000, 0x00320000),
    ("L1 block 3", 0x00380000, 0x003A0000),
]
L2_START, L2_END, L2_SW_BASE = 0x20000000, 0x20100000, 0x00B80000
DDR_START, DDR_END = 0x80000000, 0xA0000000

# CRC32 used by selload's -CRC32 option (CALLBACK blocks): reflected table,
# init 0, no final xor, default polynomial 0xD8018001.
DEFAULT_CRC_POLY = 0xD8018001


def crc32_sel(data: bytes, poly: int = DEFAULT_CRC_POLY) -> int:
    table = []
    for i in range(256):
        c = i
        for _ in range(8):
            c = (c >> 1) ^ poly if c & 1 else c >> 1
        table.append(c)
    crc = 0
    for b in data:
        crc = (crc >> 8) ^ table[(crc ^ b) & 0xFF]
    return crc


def classify(addr: int) -> dict:
    """Memory space of a loader byte address, with execution aliases."""
    for name, lo, hi in L1_BLOCKS:
        if SYS_FABRIC + lo <= addr < SYS_FABRIC + hi:
            bw = addr - SYS_FABRIC
            info = {"space": name, "bw": bw}
            if bw % 2 == 0:
                info["sw"] = bw // 2
            if bw % 4 == 0:
                info["nw"] = bw // 4
            return info
    if L2_START <= addr < L2_END:
        info = {"space": "L2"}
        if addr % 2 == 0:
            info["sw"] = L2_SW_BASE + (addr - L2_START) // 2
        return info
    if DDR_START <= addr < DDR_END:
        return {"space": "DDR"}
    return {"space": "other"}


def classify_sw(sw: int) -> str:
    """Name the memory a short-word (VISA PC) address executes from."""
    for name, lo, hi in L1_BLOCKS:
        if lo // 2 <= sw < hi // 2:
            return name
    if L2_SW_BASE <= sw < L2_SW_BASE + (L2_END - L2_START) // 2:
        return "L2"
    return "unknown"


def flag_names(code: int) -> list[str]:
    names = [n for b, n in BFLAGS.items() if code >> b & 1]
    unknown = code & FLAG_MASK & ~KNOWN_FLAG_MASK
    names += ["bit%d" % b for b in range(4, 16) if unknown >> b & 1]
    return names


def parse(data: bytes) -> tuple[list[dict], list[str], int]:
    """Walk the block chain. Returns (blocks, errors, stop offset)."""
    blocks, errors = [], []
    off = 0
    while off < len(data):
        if off + HEADER_LEN > len(data):
            errors.append("%d trailing bytes at 0x%x, too short for a header" % (len(data) - off, off))
            break
        hdr = data[off:off + HEADER_LEN]
        code, target, count, arg = struct.unpack("<IIII", hdr)
        xor = 0
        for b in hdr:
            xor ^= b
        sign = code >> 24
        if sign not in HDRSIGN or xor:
            errors.append("bad header at 0x%x (HDRSIGN 0x%02x, XOR 0x%02x)" % (off, sign, xor))
            break
        names = flag_names(code)
        fill = "FILL" in names
        plen = 0 if fill else count
        if off + HEADER_LEN + plen > len(data):
            errors.append("payload of block at 0x%x overruns the stream" % off)
            break
        blk = {
            "index": len(blocks),
            "offset": off,
            "block_code": code,
            "bcode": code & 0xF,
            "hdrchk": code >> 16 & 0xFF,
            "core": HDRSIGN[sign],
            "flags": names,
            "target_address": target,
            "byte_count": count,
            "argument": arg,
            "fill": fill,
            "payload_offset": off + HEADER_LEN,
            "payload_len": plen,
            "wire_size": HEADER_LEN + plen,
        }
        if count and not ({"FIRST", "FINAL", "INIT", "IGNORE"} & set(names)):
            blk.update(classify(target))
        elif target and {"FIRST", "FINAL", "INIT"} & set(names):
            blk["space"] = "exec (sw %s)" % classify_sw(target)
            blk["sw"] = target
        if not fill and plen:
            blk["sha256"] = hashlib.sha256(data[off + HEADER_LEN:off + HEADER_LEN + plen]).hexdigest()
        blocks.append(blk)
        off += HEADER_LEN + plen
    return blocks, errors, off


def applications(blocks: list[dict], size: int) -> list[dict]:
    """Group blocks into applications, each starting at a FIRST block."""
    starts = [b["index"] for b in blocks if "FIRST" in b["flags"]]
    apps = []
    for n, s in enumerate(starts):
        e = starts[n + 1] if n + 1 < len(starts) else len(blocks)
        first = blocks[s]
        end_off = blocks[e]["offset"] if e < len(blocks) else size
        apps.append({
            "app": n,
            "first_block": s,
            "last_block": e - 1,
            "entry_sw": first["target_address"],
            "first_argument": first["argument"],
            "bytes_after_first_header": end_off - (first["offset"] + HEADER_LEN),
            "init_blocks": [b["index"] for b in blocks[s:e] if "INIT" in b["flags"]],
            "final_blocks": [b["index"] for b in blocks[s:e] if "FINAL" in b["flags"]],
        })
    return apps


def check(data: bytes, blocks: list[dict], errors: list[str], stop: int, apps: list[dict]) -> list[dict]:
    """Every structural check, as a list of {name, ok, detail}."""
    res = []

    def add(name, ok, detail=""):
        res.append({"check": name, "ok": bool(ok), "detail": detail})

    add("chain parses with no error", not errors, "; ".join(errors))
    add("chain ends exactly at end of stream", stop == len(data), "stop 0x%x, size 0x%x" % (stop, len(data)))
    # Byte accounting: every stream byte is in exactly one header or payload.
    owner = bytearray(len(data))
    for b in blocks:
        for lo, hi in ((b["offset"], b["offset"] + HEADER_LEN),
                       (b["payload_offset"], b["payload_offset"] + b["payload_len"])):
            for i in range(lo, hi):
                owner[i] += 1
    gaps = owner.count(0)
    doubles = sum(1 for v in owner if v > 1)
    hdr = HEADER_LEN * len(blocks)
    pay = sum(b["payload_len"] for b in blocks)
    add("every byte accounted for once", gaps == 0 and doubles == 0 and hdr + pay == len(data),
        "%d header + %d payload = %d of %d bytes; %d unowned, %d owned twice"
        % (hdr, pay, hdr + pay, len(data), gaps, doubles))
    add("every HDRCHK makes the header XOR zero", not errors, "%d headers" % len(blocks))
    add("every HDRSIGN is a core-0 signature", all(b["core"] == 0 for b in blocks),
        "cores %s" % sorted({b["core"] for b in blocks}))
    unknown = sorted({f for b in blocks for f in b["flags"] if f.startswith("bit")})
    add("no undocumented flag bits", not unknown, ",".join(unknown))
    bcodes = sorted({b["bcode"] for b in blocks})
    add("one BCODE for the whole stream", len(bcodes) == 1, "BCODE %s" % bcodes)
    odd = [b["index"] for b in blocks if b["byte_count"] % 4]
    add("byte counts are multiples of 4", not odd, "blocks %s" % odd if odd else "")
    unal = [b["index"] for b in blocks if b["byte_count"] and b["target_address"] % 4
            and not ({"FIRST", "FINAL", "INIT"} & set(b["flags"]))]
    add("load targets are 4-byte aligned", not unal, "blocks %s" % unal if unal else "")
    for a in apps:
        add("app %d FIRST argument = bytes to next application" % a["app"],
            a["first_argument"] == a["bytes_after_first_header"],
            "argument 0x%x, measured 0x%x" % (a["first_argument"], a["bytes_after_first_header"]))
    add("stream ends with a FINAL block", blocks and "FINAL" in blocks[-1]["flags"])
    if blocks and apps:
        add("FINAL target = last application's entry",
            blocks[-1]["target_address"] == apps[-1]["entry_sw"],
            "FINAL 0x%x, entry 0x%x" % (blocks[-1]["target_address"], apps[-1]["entry_sw"]))
    marker_payload = [b["index"] for b in blocks
                      if {"FIRST", "FINAL", "INIT"} & set(b["flags"]) and b["byte_count"]]
    add("FIRST/INIT/FINAL markers carry no payload", not marker_payload,
        "blocks %s" % marker_payload if marker_payload else "")
    fill_args = sorted({b["argument"] for b in blocks if b["fill"]})
    add("FILL blocks", True, "%d blocks, fill values %s" % (
        sum(b["fill"] for b in blocks), ["0x%08x" % v for v in fill_args]))
    # Per-block CRC: only CALLBACK blocks carry one (selload -CRC32 layout).
    cb = [b for b in blocks if "CALLBACK" in b["flags"]]
    if cb:
        poly = next((b["argument"] for b in blocks if "INIT" in b["flags"] and b["argument"]), DEFAULT_CRC_POLY)
        bad = [b["index"] for b in cb
               if crc32_sel(data[b["payload_offset"]:b["payload_offset"] + b["payload_len"]], poly) != b["argument"]]
        add("CALLBACK block CRC32 values match", not bad, "poly 0x%08x, %d blocks, bad %s" % (poly, len(cb), bad))
    else:
        add("per-block CRC32", True, "no CALLBACK blocks, so the stream carries no payload CRC")
    return res


class Image:
    """Sparse memory written by the stream, in loader byte addresses.

    Regions are the union of every nonzero-length block's target range,
    merged when they touch. Bytes are replayed in stream order, so later
    blocks overwrite earlier ones (last write wins).
    """

    def __init__(self, blocks: list[dict]):
        spans = sorted((b["target_address"], b["target_address"] + b["byte_count"])
                       for b in blocks if b["byte_count"] and not self._marker(b))
        merged: list[list[int]] = []
        for s, e in spans:
            if merged and s <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], e)
            else:
                merged.append([s, e])
        self.regions = [(s, e) for s, e in merged]
        self.data = [bytearray(e - s) for s, e in self.regions]
        self.owner = [array.array("h", [-1]) * (e - s) for s, e in self.regions]

    @staticmethod
    def _marker(b):
        return bool({"FIRST", "FINAL", "INIT", "IGNORE"} & set(b["flags"])) and not b["fill"]

    def _region(self, addr):
        for i, (s, e) in enumerate(self.regions):
            if s <= addr < e:
                return i
        raise KeyError(hex(addr))

    def apply(self, data: bytes, b: dict):
        if not b["byte_count"] or self._marker(b):
            return
        r = self._region(b["target_address"])
        s, _ = self.regions[r]
        lo = b["target_address"] - s
        n = b["byte_count"]
        if b["fill"]:
            word = struct.pack("<I", b["argument"])
            chunk = (word * (n // 4 + 1))[:n]
        else:
            chunk = data[b["payload_offset"]:b["payload_offset"] + n]
        self.data[r][lo:lo + n] = chunk
        self.owner[r][lo:lo + n] = array.array("h", [b["index"]]) * n

    def read(self, addr, n):
        r = self._region(addr)
        s, e = self.regions[r]
        if addr + n > e:
            return None
        if any(o < 0 for o in self.owner[r][addr - s:addr - s + n]):
            return None
        return bytes(self.data[r][addr - s:addr - s + n])

    def segments(self):
        """(start, end, block index) runs of the final owner map."""
        out = []
        for (s, _), own in zip(self.regions, self.owner):
            run_start, cur = 0, own[0] if len(own) else -1
            for i in range(1, len(own) + 1):
                v = own[i] if i < len(own) else None
                if v != cur:
                    out.append((s + run_start, s + i, cur))
                    run_start, cur = i, v
        return out

    def write(self, directory: Path) -> list[dict]:
        directory.mkdir(parents=True, exist_ok=True)
        for old in directory.glob("*.bin"):
            old.unlink()
        files = []
        for (s, e), buf, own in zip(self.regions, self.data, self.owner):
            written = sum(1 for o in own if o >= 0)
            name = "%08x-%08x.bin" % (s, e)
            (directory / name).write_bytes(bytes(buf))
            files.append({"file": name, "start": s, "end": e, "size": e - s,
                          "written": written, "sha256": hashlib.sha256(bytes(buf)).hexdigest(),
                          **{k: v for k, v in classify(s).items() if k == "space"}})
        return files


def overlaps(blocks: list[dict]) -> list[dict]:
    out = []
    loaded = [b for b in blocks if b["byte_count"] and not Image._marker(b)]
    for i, b in enumerate(loaded):
        bs, be = b["target_address"], b["target_address"] + b["byte_count"]
        for a in loaded[:i]:
            as_, ae = a["target_address"], a["target_address"] + a["byte_count"]
            lo, hi = max(bs, as_), min(be, ae)
            if lo < hi:
                out.append({"later": b["index"], "earlier": a["index"], "start": lo, "end": hi, "bytes": hi - lo})
    return out


def load_digikit_sharcldr(digikit: Path):
    """Import digikit's tools/sharcldr.py from its file (stdlib only), without
    writing bytecode into the digikit tree and without its environment."""
    sys.dont_write_bytecode = True
    path = digikit / "tools" / "sharcldr.py"
    spec = importlib.util.spec_from_file_location("digikit_sharcldr", path)
    mod = importlib.util.module_from_spec(spec)
    saved = list(sys.path)
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.path[:] = saved
    return mod


def cross_check(digikit: Path, data: bytes, blocks: list[dict], image: Image, apps: list[dict]) -> list[dict]:
    res = []

    def add(name, ok, detail=""):
        res.append({"check": "digikit: " + name, "ok": bool(ok), "detail": detail})

    L = load_digikit_sharcldr(digikit)
    theirs = L.parse_blocks(data)
    keys = ("offset", "block_code", "target_address", "byte_count", "argument", "fill",
            "payload_offset", "payload_len", "core")
    # digikit's flag list scans bits 0-15, so BCODE bits 3:0 show up as
    # "bit0".."bit3"; compare the BFLAG names only and report the rest.
    def bflags(names):
        return {n for n in names if n not in ("bit0", "bit1", "bit2", "bit3")}
    diff = [i for i, (a, b) in enumerate(zip(blocks, theirs))
            if any(a[k] != b[k] for k in keys) or set(a["flags"]) != bflags(b["flags"])]
    extra = sorted({n for b in theirs for n in b["flags"]} - {n for b in blocks for n in b["flags"]})
    add("parse_blocks gives the same blocks", len(theirs) == len(blocks) and not diff,
        "%d vs %d blocks, %d differ in offset/code/target/count/argument/fill/payload/core/BFLAGs;"
        " digikit also lists %s (BCODE bits)" % (len(blocks), len(theirs), len(diff), extra))
    eps = L.entry_points(theirs)
    add("entry_points", eps == [a["entry_sw"] for a in apps], " ".join("0x%x" % e for e in eps))
    base, code, used = L.main_program(data, theirs)
    mine = image.read(base, len(code)) if base is not None else None
    add("main_program() bytes equal our final image at the entry",
        base is not None and mine == code,
        "%d bytes at 0x%x from blocks %s" % (len(code), base or 0, used))
    mem = L.LoadedMemory.from_stream(data, theirs)
    their_ranges = [tuple(r) for r in mem.ranges()]
    mine_ranges = [r for r in image.regions]
    add("LoadedMemory.ranges() equals our regions", their_ranges == mine_ranges,
        "%d vs %d ranges" % (len(their_ranges), len(mine_ranges)))
    # Sample reads: every block's first/last 4 bytes, plus random addresses.
    rng = random.Random(7)
    probes = []
    for b in blocks:
        if b["byte_count"] and not Image._marker(b):
            probes += [b["target_address"], b["target_address"] + b["byte_count"] - 4]
    for s, e in image.regions:
        probes += [rng.randrange(s, e - 4) for _ in range(40)]
    bad = [p for p in probes if mem.read(p, 4) != image.read(p, 4)]
    add("LoadedMemory.read() agrees with our image", not bad,
        "%d probes, %d differ" % (len(probes), len(bad)))
    # digikit's per-image code-block selection, read from its source text.
    sha = hashlib.sha256(data).hexdigest()
    src = (digikit / "tools" / "sharcdb.py").read_text()
    m = re.search(r'"%s":\s*\(([^)]*)\)' % sha, src)
    if m:
        idx = [int(x) for x in m.group(1).split(",") if x.strip()]
        add("sharcdb KNOWN_CODE_BLOCKS has this image", True,
            "blocks %s at %s" % (idx, ", ".join("0x%08x" % blocks[i]["target_address"] for i in idx)))
    else:
        add("sharcdb KNOWN_CODE_BLOCKS has this image", False, "sha256 %s not listed" % sha)
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("stream", nargs="?", default=str(DEFAULT_STREAM))
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--no-image", action="store_true", help="skip the memory image")
    ap.add_argument("--dump-payloads", action="store_true")
    ap.add_argument("--digikit", help="digikit checkout for the cross-check")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    data = Path(args.stream).read_bytes()
    blocks, errors, stop = parse(data)
    apps = applications(blocks, len(data))
    checks = check(data, blocks, errors, stop, apps)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    result = {
        "stream": str(args.stream),
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "applications": apps,
        "blocks": blocks,
        "overlaps": overlaps(blocks),
    }

    if not args.no_image and not errors:
        app_images = []
        if len(apps) > 1:
            for a in apps[:-1]:  # the last application's state is the final image
                img = Image(blocks[:a["last_block"] + 1])
                for b in blocks[:a["last_block"] + 1]:
                    img.apply(data, b)
                app_images.append({"app": a["app"], "files": img.write(out / "image" / ("app%d" % a["app"]))})
        image = Image(blocks)
        for b in blocks:
            image.apply(data, b)
        result["image_final"] = image.write(out / "image" / "final")
        result["image_per_app"] = app_images
        segs = image.segments()
        with open(out / "segments_final.tsv", "w") as f:
            f.write("start\tend\tbytes\tblock\tkind\tspace\n")
            for s, e, i in segs:
                kind = "unwritten" if i < 0 else ("fill" if blocks[i]["fill"] else "payload")
                f.write("0x%08x\t0x%08x\t%d\t%s\t%s\t%s\n" % (s, e, e - s, i if i >= 0 else "-", kind, classify(s)["space"]))
        if args.digikit:
            checks += cross_check(Path(args.digikit), data, blocks, image, apps)
    if args.dump_payloads:
        pd = out / "payloads"
        pd.mkdir(parents=True, exist_ok=True)
        for b in blocks:
            if b["payload_len"]:
                (pd / ("blk%02d_%08x.bin" % (b["index"], b["target_address"]))).write_bytes(
                    data[b["payload_offset"]:b["payload_offset"] + b["payload_len"]])

    result["checks"] = checks
    (out / "blocks.json").write_text(json.dumps(result, indent=1))

    if not args.quiet:
        print("%s: %d bytes, sha256 %s" % (args.stream, len(data), result["sha256"]))
        print("%-4s %-8s %-6s %-10s %-9s %-10s %-24s %s" % (
            "blk", "offset", "bcode", "target", "count", "argument", "flags", "space / exec"))
        for b in blocks:
            sp = b.get("space", "")
            if "sw" in b and "space" in b and not sp.startswith("exec"):
                sp += " sw 0x%x" % b["sw"]
            print("%-4d %08x %-6d %08x   %-9x %08x   %-24s %s" % (
                b["index"], b["offset"], b["bcode"], b["target_address"], b["byte_count"],
                b["argument"], ",".join(b["flags"]) or "-", sp))
        for a in apps:
            print("app %d: blocks %d..%d, entry sw 0x%x (%s), INIT %s, FINAL %s" % (
                a["app"], a["first_block"], a["last_block"], a["entry_sw"],
                classify_sw(a["entry_sw"]), a["init_blocks"], a["final_blocks"]))
        if "image_final" in result:
            print("final image regions:")
            for r in result["image_final"]:
                print("  0x%08x-0x%08x %9d bytes (%d written) %s" % (
                    r["start"], r["end"], r["size"], r["written"], r["space"]))
        for c in checks:
            print("%s  %s  %s" % ("PASS" if c["ok"] else "FAIL", c["check"], c["detail"]))
        print("wrote %s" % (out / "blocks.json"))
    return 0 if all(c["ok"] for c in checks) else 1


if __name__ == "__main__":
    sys.exit(main())
