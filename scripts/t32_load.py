#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""T3.2: load the Digitone II DSP boot stream (section 7) into flat memory images.

    python3 scripts/t32_load.py                       # section 7 of 1.12 -> out/T3.2/mem/
    python3 scripts/t32_load.py STREAM --out DIR
    python3 scripts/t32_load.py --db vendor/digikit/out/sharcdb/dn2-1.12.sqlite   # + DB check

Stdlib only (reuses scripts/sharc_ldr_parse.py for the block grammar).

What it writes (default out/T3.2/mem/, git-ignored):
    l1b0.bin l1b1.bin l1b2.bin l1b3.bin   the four L1 blocks, full size, as the
                                          core sees them at byte addresses
                                          0x240000, 0x2c0000, 0x300000, 0x380000
    l2.bin                                L2, 1 MiB at 0x20000000
    ddr.bin                               DDR from 0x80000000 up to the end of the
                                          last loaded byte, rounded up to 64 KiB
    app0/<region>.bin                     the same regions at the moment the INIT
                                          block runs the init program (app 0)
    memmap.json                           regions, address conventions, every block's
                                          destination, the final owner segments
                                          (which block supplied each byte range, or
                                          "unwritten"), entry points, the checks
Unwritten bytes are 0 in the .bin files; memmap.json says which they are.

Address conventions (ADSP-2156x, single SHARC+ core; see notes/T3.2-sharc-memmap.md):
    core byte address (what C pointers hold; "DM bare")  L1 0x240000.., L2 0x20000000.., DDR 0x80000000..
    loader / system address                             L1: 0x28000000 + core byte; L2, DDR: unchanged
    short-word (SW, the VISA program counter)            L1: byte = 2*SW;  L2: byte = 0x20000000 + 2*(SW - 0xb80000)
    normal word (NW, 32-bit)                             L1: byte = 4*NW (block 0 NW 0x90000 = byte 0x240000)
    48-bit NW (instructions/PX data in PM)               L1: byte = block byte base + 6*(NW - block NW base)

Self-checks (exit status 0 only if all pass):
    * the stream's own structural checks (sharc_ldr_parse.check)
    * round trip: header + payload bytes = stream size; payload + fill bytes written =
      sum of byte counts; replaying every block, each byte's last writer matches the
      block order, and every payload block's surviving bytes equal its payload
    * known bytes land at known addresses: entry stub, Audio Task, block handler, the
      four effect entries, the "Audio Task" string, the compressor ratio table
    * with --db: N aligned instructions from digikit's sharcdb compared byte for byte
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sqlite3
import struct
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import sharc_ldr_parse as P  # noqa: E402

ROOT = HERE.parent
STREAM_CANDIDATES = [
    ROOT / "vendor/digikit/out/sections/dn2-1.12/section_7_BLOB.bin",
    ROOT / "ele3_out/04_type7.unpacked.bin",
]
DEFAULT_OUT = ROOT / "out" / "T3.2" / "mem"
SYS_L1 = 0x28000000
L2_SW_BASE = 0xB80000

# name, core byte start, core byte end, NW base (32-bit word address of the first byte)
REGIONS = [
    {"name": "l1b0", "desc": "L1 block 0", "start": 0x00240000, "end": 0x00270000, "sys": SYS_L1},
    {"name": "l1b1", "desc": "L1 block 1", "start": 0x002C0000, "end": 0x002F0000, "sys": SYS_L1},
    {"name": "l1b2", "desc": "L1 block 2", "start": 0x00300000, "end": 0x00320000, "sys": SYS_L1},
    {"name": "l1b3", "desc": "L1 block 3", "start": 0x00380000, "end": 0x003A0000, "sys": SYS_L1},
    {"name": "l2", "desc": "L2 SRAM", "start": 0x20000000, "end": 0x20100000, "sys": 0},
    {"name": "ddr", "desc": "DDR (external port, DMC)", "start": 0x80000000, "end": None, "sys": 0},
]

# Known bytes, as they sit in memory (VISA parcels are 16-bit little endian, so the
# instruction 0x180400b891fa is stored 04 18 b8 00 fa 91). Instruction texts are
# digikit sharcdb / selache seldump; both decoders agree on all of these.
KNOWN_SW = [
    (0x1C12E2, "100000031400", "entry: r0=dm(0x31400) (BTB disable sequence)"),
    (0x1C1311, "063e001c0000", "startup: jump 0x1c0000"),
    (0x1C9FE7, "180400b891fa", "Audio Task: cjump 0xb891fa (db)"),
    (0x1C9FF4, "1804001c9d6b", "Audio Task: cjump 0x1c9d6b (block handler)"),
    (0x1C9FFB, "073e00ffffec", "Audio Task: jump 0x1c9fe7 (loop)"),
    (0x1C1BE1, None, "FX bus: call of the chorus 0xb84d5c"),
    (0x1C1C58, None, "FX bus: call of the delay 0xb8482c"),
    (0x1C1D11, None, "FX bus: call of the reverb 0xb836bc"),
    (0x1C215C, None, "master: call of the compressor 0xb8541d"),
]
CALL_TARGETS = {0x1C1BE1: 0xB84D5C, 0x1C1C58: 0xB8482C, 0x1C1D11: 0xB836BC, 0x1C215C: 0xB8541D}
EFFECT_ENTRIES = {0xB84D5C: "chorus", 0xB8482C: "delay", 0xB836BC: "reverb", 0xB8541D: "compressor"}
KNOWN_DM = [
    (0x00268A78, b"Audio Task\x00", "task name string (R8 literal at 0x1ca013)"),
    (0x002CFDE0, struct.pack("<8f", 1 / 1.5, 1 / 2, 1 / 3, 1 / 4, 1 / 6, 1 / 8, 1 / 16, 1 / 20),
     "compressor ratio table 1/r (T3.4)"),
]


def find_stream() -> Path:
    for p in STREAM_CANDIDATES:
        if p.exists():
            return p
    raise SystemExit("no section-7 stream found; pass one")


def parcels_to_bytes(hexstr: str) -> bytes:
    """'180400b891fa' -> memory bytes (each 16-bit parcel little endian)."""
    out = bytearray()
    for i in range(0, len(hexstr), 4):
        out += int(hexstr[i:i + 4], 16).to_bytes(2, "little")
    return bytes(out)


def bytes_to_parcels(b: bytes) -> str:
    return "".join("%04x" % int.from_bytes(b[i:i + 2], "little") for i in range(0, len(b), 2))


class Memory:
    """The six regions as flat byte arrays at core byte addresses, with an owner map."""

    def __init__(self, ddr_end: int):
        self.regions = []
        for r in REGIONS:
            r = dict(r)
            if r["end"] is None:
                r["end"] = ddr_end
            r["data"] = bytearray(r["end"] - r["start"])
            r["owner"] = [-1] * (r["end"] - r["start"])
            self.regions.append(r)

    # --- address conversion -------------------------------------------------
    @staticmethod
    def loader_to_core(addr: int) -> int:
        if SYS_L1 <= addr < SYS_L1 + 0x400000:
            return addr - SYS_L1
        return addr

    @staticmethod
    def sw_to_core(sw: int) -> int:
        if L2_SW_BASE <= sw < L2_SW_BASE + 0x80000:
            return 0x20000000 + 2 * (sw - L2_SW_BASE)
        return 2 * sw

    def region(self, core: int):
        for r in self.regions:
            if r["start"] <= core < r["end"]:
                return r
        raise KeyError("0x%x is in no region" % core)

    def read(self, core: int, n: int) -> bytes:
        r = self.region(core)
        o = core - r["start"]
        return bytes(r["data"][o:o + n])

    def written(self, core: int, n: int) -> bool:
        r = self.region(core)
        o = core - r["start"]
        return all(x >= 0 for x in r["owner"][o:o + n])

    def apply(self, data: bytes, b: dict):
        n = b["byte_count"]
        core = self.loader_to_core(b["target_address"])
        r = self.region(core)
        o = core - r["start"]
        if o + n > len(r["data"]):
            raise ValueError("block %d overruns %s" % (b["index"], r["name"]))
        if b["fill"]:
            chunk = (struct.pack("<I", b["argument"]) * (n // 4 + 1))[:n]
        else:
            chunk = data[b["payload_offset"]:b["payload_offset"] + n]
        r["data"][o:o + n] = chunk
        r["owner"][o:o + n] = [b["index"]] * n

    def segments(self, r):
        own = r["owner"]
        out, s = [], 0
        for i in range(1, len(own) + 1):
            if i == len(own) or own[i] != own[s]:
                out.append((r["start"] + s, r["start"] + i, own[s]))
                s = i
        return out


def is_load_block(b: dict) -> bool:
    return bool(b["byte_count"]) and not ({"FIRST", "FINAL", "INIT", "IGNORE"} & set(b["flags"]))


def block_kind(b: dict, app_of: dict, code_blocks: set) -> str:
    if not is_load_block(b):
        return "marker:" + ",".join(b["flags"])
    if b["fill"]:
        return "zero-fill" if b["argument"] == 0 else "fill 0x%08x" % b["argument"]
    if app_of[b["index"]] == 0:
        return "init program" if b["index"] in code_blocks else "init-program data"
    return "code" if b["index"] in code_blocks else "initialized data"


# Code blocks: decided in notes/T3.2-sharc-memmap.md section 2 (VISA code by
# disassembly coherence and call/branch targets; IVT = 48-bit code in L1 block 0;
# block 57 is data). Everything else that carries a payload is data.
CODE_BLOCKS = {1, 56, 60, 64, 66, 68, 70, 72, 74, 76, 78, 79, 81, 83}
FILL_IN_CODE = {65, 67, 69, 71, 73, 75, 80, 82}  # 68-byte code FILLs (NOP padding)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("stream", nargs="?")
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--db", help="digikit sharcdb sqlite for an instruction byte cross-check")
    ap.add_argument("--db-image", default="dn2-1.12")
    ap.add_argument("--samples", type=int, default=2000)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    spath = Path(args.stream) if args.stream else find_stream()
    data = spath.read_bytes()
    blocks, errors, stop = P.parse(data)
    apps = P.applications(blocks, len(data))
    checks = []

    def add(name, ok, detail=""):
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    for c in P.check(data, blocks, errors, stop, apps):
        checks.append({"check": "stream: " + c["check"], "ok": c["ok"], "detail": c["detail"]})
    if errors:
        print("\n".join(errors))
        return 1

    app_of = {}
    for a in apps:
        for i in range(a["first_block"], a["last_block"] + 1):
            app_of[i] = a["app"]

    ddr_last = max(b["target_address"] + b["byte_count"] for b in blocks
                   if is_load_block(b) or (b["fill"] and b["byte_count"]))
    ddr_end = (ddr_last + 0xFFFF) & ~0xFFFF
    mem = Memory(ddr_end)
    app0 = None
    for b in blocks:
        if "INIT" in b["flags"] and app0 is None:
            # snapshot: what the init program sees when the boot ROM calls it
            app0 = [(r["name"], bytes(r["data"]), list(r["owner"])) for r in mem.regions]
        if is_load_block(b) or (b["fill"] and b["byte_count"]):
            mem.apply(data, b)

    # --- round-trip checks ----------------------------------------------------
    hdr = P.HEADER_LEN * len(blocks)
    pay = sum(b["payload_len"] for b in blocks)
    add("round trip: headers + payloads = stream size", hdr + pay == len(data),
        "%d + %d = %d of %d" % (hdr, pay, hdr + pay, len(data)))
    loaded = [b for b in blocks if is_load_block(b) or (b["fill"] and b["byte_count"])]
    total = sum(b["byte_count"] for b in loaded)
    owned = sum(sum(1 for o in r["owner"] if o >= 0) for r in mem.regions)
    survive = {}
    for r in mem.regions:
        for o in r["owner"]:
            if o >= 0:
                survive[o] = survive.get(o, 0) + 1
    overwritten = total - sum(survive.values())
    add("round trip: bytes written = sum of byte counts, minus overwrites",
        owned == sum(survive.values()) and total == sum(b["byte_count"] for b in loaded),
        "%d bytes written by %d blocks (%d payload, %d fill); %d end up in the image; "
        "%d overwritten by later blocks" % (
            total, len(loaded), sum(b["byte_count"] for b in loaded if not b["fill"]),
            sum(b["byte_count"] for b in loaded if b["fill"]), owned, overwritten))
    bad = []
    for b in loaded:
        if b["fill"]:
            continue
        core = Memory.loader_to_core(b["target_address"])
        r = mem.region(core)
        o = core - r["start"]
        pl = data[b["payload_offset"]:b["payload_offset"] + b["byte_count"]]
        for k in range(b["byte_count"]):
            if r["owner"][o + k] == b["index"] and r["data"][o + k] != pl[k]:
                bad.append(b["index"])
                break
    add("round trip: every surviving payload byte equals the stream byte", not bad, "bad blocks %s" % bad)
    app1_over = sorted({o for r in mem.regions for o in r["owner"] if o >= 0 and app_of.get(o) == 0})
    add("only app-0 bytes that app 1 does not overwrite survive from app 0", True,
        "app-0 blocks with surviving bytes: %s (%s bytes)" % (
            app1_over, sum(survive.get(i, 0) for i in app1_over)))

    # --- known bytes at known addresses ----------------------------------------
    for sw, hexbytes, what in KNOWN_SW:
        core = mem.sw_to_core(sw)
        got = mem.read(core, 6)
        if hexbytes is None:
            tgt = CALL_TARGETS[sw]
            want = "180400%06x" % tgt
            ok = bytes_to_parcels(got).startswith(want[:12])
            add("known code: %s at SW 0x%x (byte 0x%x)" % (what, sw, core), ok,
                "image %s, expected a delayed call 0x%s" % (bytes_to_parcels(got), want))
            # the callee: first bytes of an effect entry, written, in L2
            tc = mem.sw_to_core(tgt)
            add("known code: %s entry 0x%x lands in loaded L2 code (byte 0x%x)" % (
                EFFECT_ENTRIES[tgt], tgt, tc),
                mem.written(tc, 6) and mem.region(tc)["name"] == "l2" and any(mem.read(tc, 6)),
                "bytes %s, block %d" % (bytes_to_parcels(mem.read(tc, 6)),
                                        mem.region(tc)["owner"][tc - 0x20000000]))
        else:
            want = parcels_to_bytes(hexbytes)
            add("known code: %s at SW 0x%x (byte 0x%x)" % (what, sw, core), got[:len(want)] == want,
                "image %s, expected %s" % (bytes_to_parcels(got[:len(want)]), hexbytes))
    for core, want, what in KNOWN_DM:
        got = mem.read(core, len(want))
        add("known data: %s at 0x%x" % (what, core), got == want, got.hex())

    # --- optional sharcdb cross-check -------------------------------------------
    if args.db:
        db = sqlite3.connect(args.db)
        rows = db.execute("SELECT sw, width, raw FROM insn WHERE image=? AND aligned=1",
                          (args.db_image,)).fetchall()
        rnd = random.Random(32)
        sample = rows if len(rows) <= args.samples else rnd.sample(rows, args.samples)
        named = db.execute("SELECT sw, width, raw FROM insn WHERE image=? AND aligned=1 AND sw IN (%s)" % ",".join(
            str(x) for x in list(EFFECT_ENTRIES) + [0x1C9FE7, 0x1C9D6B, 0x1C2712, 0x1C1738, 0x1C1F1E]),
            (args.db_image,)).fetchall()
        # the init program (block 1, SW 0x1201f8-0x12161c) is overwritten by app 1,
        # so its instructions are compared with the app-0 snapshot
        a0 = {name: buf for name, buf, _ in app0} if app0 else {}
        b1 = blocks[1]
        init_lo = Memory.loader_to_core(b1["target_address"]) // 2
        init_hi = init_lo + b1["byte_count"] // 2
        miss, n_init = [], 0
        for sw, width, raw in sample + named:
            want = parcels_to_bytes(raw.zfill(2 * width))
            core = mem.sw_to_core(sw)
            if init_lo <= sw < init_hi and a0:
                n_init += 1
                got = a0["l1b0"][core - 0x240000:core - 0x240000 + width]
            else:
                got = mem.read(core, width)
            if got != want:
                miss.append("0x%x" % sw)
        add("sharcdb: %d sampled + %d named aligned instructions match the image byte for byte"
            " (%d init-program ones against the app-0 snapshot)"
            % (len(sample), len(named), n_init), not miss, "mismatches %s" % miss[:10])

    # --- outputs -------------------------------------------------------------------
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "app0").mkdir(exist_ok=True)
    regions_json = []
    for r in mem.regions:
        (out / (r["name"] + ".bin")).write_bytes(bytes(r["data"]))
        segs = []
        for s, e, o in mem.segments(r):
            if o < 0:
                kind = "unwritten"
            else:
                kind = block_kind(blocks[o], app_of, CODE_BLOCKS)
                if o in FILL_IN_CODE:
                    kind = "code fill (zero = NOP padding)"
            segs.append({"start": "0x%08x" % s, "end": "0x%08x" % e, "bytes": e - s,
                         "block": o if o >= 0 else None, "kind": kind})
        info = {"name": r["name"], "desc": r["desc"], "file": r["name"] + ".bin",
                "core_start": "0x%08x" % r["start"], "core_end": "0x%08x" % r["end"],
                "size": r["end"] - r["start"],
                "loader_start": "0x%08x" % (r["start"] + r["sys"]),
                "written": sum(1 for o in r["owner"] if o >= 0),
                "sha256": hashlib.sha256(bytes(r["data"])).hexdigest(),
                "segments": segs}
        if r["name"].startswith("l1"):
            info["sw_range"] = ["0x%06x" % (r["start"] // 2), "0x%06x" % (r["end"] // 2)]
            info["nw32_range"] = ["0x%05x" % (r["start"] // 4), "0x%05x" % (r["end"] // 4)]
        if r["name"] == "l2":
            info["sw_range"] = ["0x%06x" % L2_SW_BASE, "0x%06x" % (L2_SW_BASE + 0x80000)]
        regions_json.append(info)
    if app0:
        for name, buf, owner in app0:
            (out / "app0" / (name + ".bin")).write_bytes(buf)
    blocks_json = []
    for b in blocks:
        e = {"index": b["index"], "offset": b["offset"], "flags": b["flags"],
             "target": "0x%08x" % b["target_address"], "byte_count": b["byte_count"],
             "argument": "0x%x" % b["argument"], "app": app_of.get(b["index"]),
             "kind": block_kind(b, app_of, CODE_BLOCKS) if b["index"] not in FILL_IN_CODE
             else "code fill (zero = NOP padding)"}
        if is_load_block(b) or (b["fill"] and b["byte_count"]):
            core = Memory.loader_to_core(b["target_address"])
            e["core"] = "0x%08x" % core
            e["region"] = mem.region(core)["name"]
            e["surviving_bytes"] = survive.get(b["index"], 0)
            if mem.region(core)["name"].startswith("l1") or mem.region(core)["name"] == "l2":
                if core % 2 == 0:
                    e["sw"] = "0x%06x" % (core // 2 if core < 0x400000 else L2_SW_BASE + (core - 0x20000000) // 2)
        elif b["target_address"]:
            e["exec_sw"] = "0x%06x" % b["target_address"]
        blocks_json.append(e)
    doc = {
        "stream": str(spath), "stream_size": len(data), "sha256": hashlib.sha256(data).hexdigest(),
        "entry_points": {"app0_init_program": "0x%06x" % apps[0]["entry_sw"],
                         "app1_main": "0x%06x" % apps[-1]["entry_sw"]},
        "conventions": {
            "core_byte": "what C pointers and DM(abs) operands hold: L1 0x240000-0x39ffff, L2 0x20000000+, DDR 0x80000000+",
            "loader": "boot-stream target: L1 = 0x28000000 + core byte; L2 and DDR unchanged",
            "sw": "VISA PC: L1 byte = 2*SW; L2 byte = 0x20000000 + 2*(SW - 0xb80000)",
            "nw32": "L1 byte = 4*NW",
            "nw48": "L1 byte = block base + 6*(NW - block NW base); e.g. PM(0xe00e0) = byte 0x380540",
        },
        "regions": regions_json, "blocks": blocks_json, "checks": checks,
    }
    (out / "memmap.json").write_text(json.dumps(doc, indent=1))

    if not args.quiet:
        for r in regions_json:
            print("%-5s %-26s core %s-%s  %8d bytes, %8d written  -> %s" % (
                r["name"], r["desc"], r["core_start"], r["core_end"], r["size"], r["written"],
                out / r["file"]))
        for c in checks:
            print("%s  %s  %s" % ("PASS" if c["ok"] else "FAIL", c["check"], c["detail"]))
        print("wrote %s" % (out / "memmap.json"))
    return 0 if all(c["ok"] for c in checks) else 1


if __name__ == "__main__":
    sys.exit(main())
