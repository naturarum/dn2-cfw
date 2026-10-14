#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Build a DSP-section (section 7) candidate with our own code spliced in (T3.8).

    python3 tools/dspsplice/build.py CG01      # milestone 1: stock chorus, wet output -6.02 dB
    python3 tools/dspsplice/build.py FLG1      # milestone 2: the flanger in the chorus slot
    python3 tools/dspsplice/build.py CMP0      # T6.3: FTD1 + the 3-band master compressor (frozen sources, src/cmp0/)
    python3 tools/dspsplice/build.py CMP1      # T6.3 CMP1's DSP: CMP0 + the review's fixes (fx/mbcomp.c #12-#14)

Pipeline (notes/T3.8-dsp-splice.md section 1):
  1. compile the recipe's C with selcc -O1 (as tools/sharcvm/tests/fxcheck.py does), generate the
     hook stubs (stub.py) and assemble everything with selas;
  2. link with seld at fixed addresses (a generated LDF: code from L1 block 1 0x2dd600, data and
     the register save area below 0x2e8000), read the ELF with tools/sharcvm/elf.py;
  3. splice into the stock boot stream (ldr.py): retarget each hooked CJUMP inside the stock
     payload, add one load block per code/data section and FILL blocks for the save area and the
     effect state, all just before FINAL; set the FIRST argument;
  4. check: the stream parses (sharc_ldr_parse), its final image equals stock's except at the
     patched bytes and in our own ranges, no block touches a cache range, sizes under the
     ColdFire loader's limits;
  5. wrap it: elektron-firmware-tool -c 7 <stream> -V <NAME> on the stock .syx, then depack
     section 7 back out of the .syx and compare with the stream.
Outputs in out/T3.8/<NAME>/: section7.bin (the --intended-dsp image), <NAME>.syx, build.json,
build/ (selcc .s, objects, the LDF, the .dxe), stubs.s.
Nothing here talks to hardware.
"""
import argparse
import collections
import hashlib
import json
import struct
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "tools/sharcvm"))
sys.path.insert(0, str(ROOT / "vendor/digikit"))
import ldr  # noqa: E402
import stub  # noqa: E402
import elf as E  # noqa: E402

SEL = ROOT / "vendor/selache/target/release"
TOOL = ROOT / "vendor/elektron-firmware-tool/elektron-firmware-tool"
STOCK_SYX = ROOT / "elektron-firmware/Digitone_II_OS1_12.syx"
STOCK_SYX_SHA = "7e5b8a733d4ab1fdf7e5d22757f7d8eddaa3cbf71d0858874a791844c02ceb35"
STOCK7 = ROOT / "out/T3.8/stock/section_7_blob.bin"
STOCK7_SHA = "336e340aa0cdcd34e314cfa44849f709a3134f6bd4cd57dfc7e15702c83115e2"

# ---- memory plan (notes/T3.8 section 3) ----
CODE = (0x2dd600, 0x2e6000)     # L1 block 1 tail: statically free (T3.2 2.2), below the DM cache
DATA = (0x2e6000, 0x2e6800)     # selcc's seg_dmda / bss
SAVE = 0x2e6800                 # hook stubs' register save area (FILL'd)
L1B1_LIMIT = 0x2e7000           # all our block-1 bytes inside dnfw's silicon-proven 0x2dd600-0x2e7000,
                                # below a 32 KB DM cache (0x2e8000) as well as the 16 KB one (0x2ec000)
STATE = (0x300000, 0x310000)    # L1 block 2: effect state (below the PM cache 0x31c000/0x318000)
CODE2 = (0x310000, 0x318000)    # L1 block 2: more code when block 1's tail is full (FTD1's tape delay)
# T6.3: ranges no block of ours may touch (layout_check), the caches at their larger (32 KB) size and L2
FORBIDDEN = [(0x2e8000, 0x2f0000, "L1 block 1 DM cache (32 KB at its largest; brickcheck: 16 KB from 0x2ec000)"),
             (0x318000, 0x320000, "L1 block 2 PM cache (32 KB at its largest; brickcheck: 16 KB from 0x31c000)"),
             (0x398000, 0x3a0000, "L1 block 3 I-cache (32 KB at its largest)"),
             (0x200918c0, 0x200c2000, "L2 range dnfw found holding live pointers (keep data out)"),
             (0x200fa000, 0x20100000, "L2 reserved / non-cacheable window and boot-ROM top")]

CHORUS_CALL = 0x1c1be1          # FX bus 0x1c1738: CJUMP 0xb84d5c (stock chorus), T3.4
CHORUS = 0xb84d5c
CHORUS_INIT_CALL = 0x1c1695     # effect init 0x1c1445: CJUMP 0xb84f51 (stock chorus init)
CHORUS_INIT = 0xb84f51
DELAY_CALL, DELAY = 0x1c1c58, 0xb8482c
DELAY_INIT_CALL, DELAY_INIT = 0x1c166c, 0xb848cb
COMP_CALL, COMP = 0x1c215c, 0xb8541d             # master 0x1c1f1e: CJUMP to the stock compressor (T6.4 1.2)
COMP_INIT_CALL, COMP_INIT = 0x1c16bb, 0xb85501   # effect init 0x1c1445: CJUMP to its init

RECIPES = {
    "CG01": dict(
        what="milestone 1: the stock chorus, then its wet output x 0.5 (-6.02 dB)",
        c=["tools/dspsplice/src/cg_post.c"], inc=["tools/dspsplice/src"],
        stubs=[("fxh_chorus", [("stock", CHORUS), ("c", "cg_post", ["R12"])])],
        hooks=[(CHORUS_CALL, CHORUS, "fxh_chorus")],
        fills=[]),
    "FLG1": dict(
        what="milestone 2: the flanger (fx/flanger.c) in the chorus slot",
        c=["fx/flanger.c", "tools/dspsplice/src/flg_glue.c"], inc=["fx", "tools/dspsplice/src"],
        stubs=[("fxh_chorus", [("c", "flg_process", ["R8", "R12"])]),
               ("fxh_chorus_init", [("c", "flg_init", []), ("tail", CHORUS_INIT)])],
        hooks=[(CHORUS_CALL, CHORUS, "fxh_chorus"), (CHORUS_INIT_CALL, CHORUS_INIT, "fxh_chorus_init")],
        fills=[(0x300000, 0x8400, "flanger state (FlgSlot: Flanger + glue), zeroed at boot")]),
    "FTD1": dict(
        what="milestone 3: the flanger in the chorus slot and the tape delay (fx/tapedelay.c) in the delay slot",
        c=["fx/flanger.c", "tools/dspsplice/src/flg_glue.c"],
        c2=["fx/tapedelay.c", "tools/dspsplice/src/td_glue.c"], inc=["fx", "tools/dspsplice/src"],
        stubs=[("fxh_chorus", [("c", "flg_process", ["R8", "R12"])]),
               ("fxh_chorus_init", [("c", "flg_init", []), ("tail", CHORUS_INIT)]),
               ("fxh_delay", [("c", "td_process", ["R8", "R12"])]),
               ("fxh_delay_init", [("c", "td_init", [])])],
        hooks=[(CHORUS_CALL, CHORUS, "fxh_chorus"), (CHORUS_INIT_CALL, CHORUS_INIT, "fxh_chorus_init"),
               (DELAY_CALL, DELAY, "fxh_delay"), (DELAY_INIT_CALL, DELAY_INIT, "fxh_delay_init")],
        fills=[(0x300000, 0x8400, "flanger state (FlgSlot: Flanger + glue), zeroed at boot")]),
    # T5.3: FTD1 with the Space Echo (fx/spaceecho.c) in place of the tape delay, one-page layout
    # (src/se_glue.c, verify_se.py). Its code (about 38 KB) does not fit FTD1's 0x310000-0x318000,
    # so it starts right after the flanger state: 0x308400-0x318000 is below the PM cache whether that
    # cache is 16 KB (0x31c000, what the start-up's size fields say) or 32 KB (0x318000).
    "SE01": dict(
        what="T5.3: the flanger in the chorus slot and the Space Echo (fx/spaceecho.c) in the delay slot, "
             "one-page layout (AGE from frame word 0xa18, k 40)",
        c=["fx/flanger.c", "tools/dspsplice/src/flg_glue.c"],
        c2=["fx/spaceecho.c", "tools/dspsplice/src/se_glue.c"], inc=["fx", "tools/dspsplice/src"],
        stubs=[("fxh_chorus", [("c", "flg_process", ["R8", "R12"])]),
               ("fxh_chorus_init", [("c", "flg_init", []), ("tail", CHORUS_INIT)]),
               ("fxh_delay", [("c", "se_process", ["R8", "R12"])]),
               ("fxh_delay_init", [("c", "se_init", [])])],
        hooks=[(CHORUS_CALL, CHORUS, "fxh_chorus"), (CHORUS_INIT_CALL, CHORUS_INIT, "fxh_chorus_init"),
               (DELAY_CALL, DELAY, "fxh_delay"), (DELAY_INIT_CALL, DELAY_INIT, "fxh_delay_init")],
        fills=[(0x300000, 0x8400, "flanger state (FlgSlot: Flanger + glue), zeroed at boot")],
        state=(0x300000, 0x308400), code2=(0x308400, 0x318000),
        outdir="out/T5.3/SE01-dsp", syx="SE01-dsp.syx", version="SE1D"),
    # T6.3 CMP0 (notes/T6.4 4.1-4.3, 5): FTD1 (the build on the owner's unit) with the 3-band master compressor
    # (fx/mbcomp.c + src/cmp_glue.c) in place of the stock compressor 0xb8541d, on the stock knobs. FTD1's
    # objects are linked first and in FTD1's order, so blocks 1 and 2 keep FTD1's code byte for byte; the
    # compressor and its two stubs go to a third code region 0x308400-0x310000 (free on FTD1: between the
    # flanger state and the tape code), its state (CmpSlot, size from cmp_slot_bytes in the linked image) to
    # 0x2e1c40 in L1 block 1, so block 1's code ends below it (code=).
    # Since CMP1 (2026-10-13) fx/mbcomp.c and src/cmp_glue.c are CMP1's; CMP0's compressor sources as built (commit
    # beddb01, the image on the owner's unit) are kept frozen in src/cmp0/ (mbcomp.c, mbcomp.h, cmp_glue.c, byte for
    # byte as committed), first on the include path, so this recipe still rebuilds the image dd78e1d8.
    "CMP0": dict(
        what="T6.3: FTD1 (flanger, tape delay) with the 3-band master compressor (fx/mbcomp.c) in place of the "
             "stock compressor, on the stock knobs, crossovers fixed at 196 Hz / 2.61 kHz",
        c=["fx/flanger.c", "tools/dspsplice/src/flg_glue.c"],
        c2=["fx/tapedelay.c", "tools/dspsplice/src/td_glue.c"],
        c3=["tools/dspsplice/src/cmp0/mbcomp.c", "tools/dspsplice/src/cmp0/cmp_glue.c"],
        inc=["tools/dspsplice/src/cmp0", "fx", "tools/dspsplice/src"],
        stubs=[("fxh_chorus", [("c", "flg_process", ["R8", "R12"])]),
               ("fxh_chorus_init", [("c", "flg_init", []), ("tail", CHORUS_INIT)]),
               ("fxh_delay", [("c", "td_process", ["R8", "R12"])]),
               ("fxh_delay_init", [("c", "td_init", [])])],
        stubs3=[("fxh_comp", [("c", "cmp_process", ["R8", "R12"])]),
                ("fxh_comp_init", [("c", "cmp_init", []), ("tail", COMP_INIT)])],
        hooks=[(CHORUS_CALL, CHORUS, "fxh_chorus"), (CHORUS_INIT_CALL, CHORUS_INIT, "fxh_chorus_init"),
               (DELAY_CALL, DELAY, "fxh_delay"), (DELAY_INIT_CALL, DELAY_INIT, "fxh_delay_init"),
               (COMP_CALL, COMP, "fxh_comp"), (COMP_INIT_CALL, COMP_INIT, "fxh_comp_init")],
        fills=[(0x300000, 0x8400, "flanger state (FlgSlot: Flanger + glue), zeroed at boot")],
        fills_sym=[((0x2e1c40, 0x2e6000), "cmp_slot_bytes", "3-band compressor state (CmpSlot), zeroed at boot")],
        state=(0x300000, 0x308400), code=(0x2dd600, 0x2e1c40), code3=(0x308400, 0x310000),
        outdir="out/T6.3/CMP0-dsp", syx="CMP0-dsp.syx", version="CM0D"),
    # T6.3 CMP1's DSP (2026-10-13): CMP0's layout, hooks and stubs, with the compressor of the CMP0 review's fixes
    # (fx/mbcomp.c #12-#14: the complete restart, the quiet return after a trip, the scrub; src/cmp_glue.c: the
    # knobs compared with the compressor's own words). On normal material its output and reply are CMP0's bit for
    # bit (tools/dspsplice/verify_cmp.py CMP1, (h)).
    "CMP1": dict(
        what="T6.3 CMP1: FTD1 (flanger, tape delay) with the 3-band master compressor (fx/mbcomp.c, CMP0 plus the "
             "review's fixes: complete restart, quiet return after a trip, scrub) in place of the stock compressor, "
             "on the stock knobs, crossovers fixed at 196 Hz / 2.61 kHz",
        c=["fx/flanger.c", "tools/dspsplice/src/flg_glue.c"],
        c2=["fx/tapedelay.c", "tools/dspsplice/src/td_glue.c"],
        c3=["fx/mbcomp.c", "tools/dspsplice/src/cmp_glue.c"], inc=["fx", "tools/dspsplice/src"],
        stubs=[("fxh_chorus", [("c", "flg_process", ["R8", "R12"])]),
               ("fxh_chorus_init", [("c", "flg_init", []), ("tail", CHORUS_INIT)]),
               ("fxh_delay", [("c", "td_process", ["R8", "R12"])]),
               ("fxh_delay_init", [("c", "td_init", [])])],
        stubs3=[("fxh_comp", [("c", "cmp_process", ["R8", "R12"])]),
                ("fxh_comp_init", [("c", "cmp_init", []), ("tail", COMP_INIT)])],
        hooks=[(CHORUS_CALL, CHORUS, "fxh_chorus"), (CHORUS_INIT_CALL, CHORUS_INIT, "fxh_chorus_init"),
               (DELAY_CALL, DELAY, "fxh_delay"), (DELAY_INIT_CALL, DELAY_INIT, "fxh_delay_init"),
               (COMP_CALL, COMP, "fxh_comp"), (COMP_INIT_CALL, COMP_INIT, "fxh_comp_init")],
        fills=[(0x300000, 0x8400, "flanger state (FlgSlot: Flanger + glue), zeroed at boot")],
        fills_sym=[((0x2e1c40, 0x2e6000), "cmp_slot_bytes", "3-band compressor state (CmpSlot), zeroed at boot")],
        state=(0x300000, 0x308400), code=(0x2dd600, 0x2e1c40), code3=(0x308400, 0x310000),
        outdir="out/T6.3/CMP1-dsp", syx="CMP1-dsp.syx", version="CM1D"),
}

LDF = """ARCHITECTURE(ADSP-21569)
$OBJECTS = $COMMAND_LINE_OBJECTS;
MEMORY
{{
   mem_t38_code {{ TYPE(BW RAM) START(0x{c0:08x}) END(0x{c1:08x}) WIDTH(8) }}
   mem_t38_data {{ TYPE(BW RAM) START(0x{d0:08x}) END(0x{d1:08x}) WIDTH(8) }}
   mem_t38_code2 {{ TYPE(BW RAM) START(0x{e0:08x}) END(0x{e1:08x}) WIDTH(8) }}
}}
PROCESSOR p0
{{
   OUTPUT($COMMAND_LINE_OUTPUT_FILE)
   ENTRY({entry}.)
   SECTIONS
   {{
      t38_code SW
      {{
         INPUT_SECTION_ALIGN(2)
         FILL(0x1)
         INPUT_SECTIONS( $OBJECTS(seg_swco seg_pmco) )
      }} > mem_t38_code
      t38_code2 SW
      {{
         INPUT_SECTION_ALIGN(2)
         FILL(0x1)
         INPUT_SECTIONS( $OBJECTS(seg_t38c2) )
      }} > mem_t38_code2
      t38_data BW
      {{
         INPUT_SECTION_ALIGN(4)
         INPUT_SECTIONS( $OBJECTS(seg_dmda) )
      }} > mem_t38_data
      t38_bss NO_INIT BW
      {{
         INPUT_SECTION_ALIGN(4)
         INPUT_SECTIONS( $OBJECTS(seg_l1_bss seg_bss) )
      }} > mem_t38_data
   }}
}}
"""


def sha(b):
    return hashlib.sha256(b).hexdigest()


def rel(p):
    """a path relative to the repository when it is inside it (build.json), else as it is (--outdir)"""
    try:
        return str(Path(p).relative_to(ROOT))
    except ValueError:
        return str(p)


def run(cmd, log):
    log.append(" ".join(str(c) for c in cmd))
    r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True)
    if r.stdout.strip():
        log.append(r.stdout.strip())
    if r.stderr.strip():
        log.append(r.stderr.strip())
    if r.returncode:
        raise SystemExit("command failed (%d): %s\n%s" % (r.returncode, " ".join(map(str, cmd)), r.stderr))
    return r


def cjump_bytes(target_sw):
    """memory bytes of the 48-bit Type 25a `CJUMP target (DB)` (stock: 0x180400 << 24 | target)"""
    raw = (0x180400 << 24) | (target_sw & 0xffffff)
    parcels = [(raw >> 32) & 0xffff, (raw >> 16) & 0xffff, raw & 0xffff]
    return b"".join(struct.pack("<H", p) for p in parcels)


LDF_MEM3 = """   mem_t38_code3 {{ TYPE(BW RAM) START(0x{g0:08x}) END(0x{g1:08x}) WIDTH(8) }}
"""
LDF_SEC3 = """      t38_code3 SW
      {{
         INPUT_SECTION_ALIGN(2)
         FILL(0x1)
         INPUT_SECTIONS( $OBJECTS(seg_t38c3) )
      }} > mem_t38_code3
"""


def compile_link(name, rec, bdir, log):
    bdir.mkdir(parents=True, exist_ok=True)
    flags = ["-proc", "ADSP-21569", "-char-size-8", "-O1"] + [f"-I{ROOT / i}" for i in rec["inc"]]
    objs = []
    for src in rec["c"] + rec.get("c2", []) + rec.get("c3", []):
        stem = Path(src).stem
        run([SEL / "selcc", *flags, "-S", "-o", bdir / f"{stem}.s", ROOT / src], log)
        seg = "seg_t38c2" if src in rec.get("c2", []) else ("seg_t38c3" if src in rec.get("c3", []) else None)
        if seg:
            # the same code, only its section renamed so the LDF puts it in the second (third) code region
            s = (bdir / f"{stem}.s").read_text()
            if s.count(".SECTION/SW seg_swco;") < 1 or ".SECTION/" in s.replace(".SECTION/SW seg_swco;", "").replace(
                    ".SECTION/DOUBLE32 seg_dmda;", ""):
                raise SystemExit(f"{stem}.s: unexpected sections")
            (bdir / f"{stem}.s").write_text(s.replace(".SECTION/SW seg_swco;", f".SECTION/SW {seg};"))
        run([SEL / "selas", "-proc", "ADSP-21569", "-o", bdir / f"{stem}.doj", bdir / f"{stem}.s"], log)
        objs.append(bdir / f"{stem}.doj")
    (bdir / "stubs.s").write_text(stub.module(rec["stubs"], SAVE))
    run([SEL / "selas", "-proc", "ADSP-21569", "-o", bdir / "stubs.doj", bdir / "stubs.s"], log)
    objs.insert(0, bdir / "stubs.doj")
    if rec.get("stubs3"):
        (bdir / "stubs3.s").write_text(stub.module(rec["stubs3"], SAVE, section="seg_t38c3"))
        run([SEL / "selas", "-proc", "ADSP-21569", "-o", bdir / "stubs3.doj", bdir / "stubs3.s"], log)
        objs.append(bdir / "stubs3.doj")
    code = rec.get("code", CODE)
    ldf = LDF.format(c0=code[0], c1=code[1] - 1, d0=DATA[0], d1=DATA[1] - 1,
                     e0=rec.get("code2", CODE2)[0], e1=rec.get("code2", CODE2)[1] - 1, entry=rec["stubs"][0][0])
    if "code3" in rec:
        g0, g1 = rec["code3"]
        anchor = "   mem_t38_code2 {"
        i = ldf.index(anchor)
        i = ldf.index("\n", i) + 1
        ldf = ldf[:i] + LDF_MEM3.format(g0=g0, g1=g1 - 1) + ldf[i:]
        anchor = "      }} > mem_t38_code2\n".replace("}}", "}")
        i = ldf.index(anchor) + len(anchor)
        ldf = ldf[:i] + LDF_SEC3.format() + ldf[i:]
    (bdir / "t38.ldf").write_text(ldf)
    run([SEL / "seld", "-proc", "ADSP-21569", "-T", bdir / "t38.ldf", "-o", bdir / f"{name}.dxe", *objs], log)
    return E.read_elf(bdir / f"{name}.dxe")


CENSUS_ALLOWED = {f"R{i}" for i in range(16)} | {"I4", "I6", "I7", "I12", "LCNTR"}


def _split_top(s):
    parts, depth, cur = [], 0, ""
    for ch in s:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    parts.append(cur)
    return parts


def register_census(asm_text):
    """registers our selcc code writes (from its assembly text): lhs of every assignment, the
    index register of every post-modify access DM(Ia,Mb), and the frame registers of CJUMP and
    RFRAME. The hook stubs save only R0-R15, I0-I5, M1-M4 and MODE1, so any other written
    register (other than the scratch I4/I12 and the frame I6/I7) would break the caller."""
    import re
    reg = re.compile(r"^(R|F|I|M|L|B|S)(\d+)$|^(MODE1|MODE2|LCNTR|PX|PX1|PX2|USTAT\d|ASTAT\w*|STKY\w*|"
                     r"MR\w*|IMASK\w*|IRPTL|MMASK|SYSCTL)$")
    out = collections.Counter()
    for line in asm_text.split("\n"):
        line = line.split("//")[0].strip()
        if not line or line.startswith(".") or line.endswith(":"):
            continue
        body = line.rstrip(";").strip()
        if body.startswith("CJUMP"):
            out["R2"] += 1
            out["I6"] += 1
            continue
        if body.startswith("RFRAME"):
            out["I6"] += 1
            out["I7"] += 1
            continue
        for part in _split_top(body):
            part = part.strip()
            for m in re.finditer(r"(?:DM|PM)\s*\(\s*(I\d+)\s*,\s*(M\d+)\s*\)", part):
                out[m.group(1)] += 1                     # post-modify updates the I register
            if "=" in part and not part.startswith(("DM", "PM", "IF")):
                lhs = part.split("=")[0].strip()
                mm = reg.match(lhs)
                if mm:
                    out["R" + mm.group(2) if mm.group(1) == "F" else lhs] += 1
            elif part.startswith("IF") and "=" in part:
                lhs = part.split("=")[0].split()[-1]
                if reg.match(lhs):
                    out[lhs.replace("F", "R", 1) if lhs.startswith("F") else lhs] += 1
            if part.startswith("LCNTR") or " DO " in f" {part} ":
                out["LCNTR"] += 1
    return out


def selcc_shift_imm_count(bdir, rec):
    """immediate-count shifts in our assembly (selas mis-encodes negative ones; selcc emits none)"""
    import re
    n = 0
    for p in ([bdir / f"{Path(s).stem}.s" for s in rec["c"] + rec.get("c2", []) + rec.get("c3", [])] + [bdir / "stubs.s"]
              + ([bdir / "stubs3.s"] if rec.get("stubs3") else [])):
        n += len(re.findall(r"(LSHIFT|ASHIFT|ROT)\s+R\d+\s+BY\s+-?(0x)?[0-9A-Fa-f]+\s*;", p.read_text()))
    return n


def ranges_of(e):
    out = []
    for s in e.sections:
        if s.kind == "sw_code":
            out.append((E.sw_to_byte(s.addr), len(s.data), s))
        elif s.kind in ("data", "bss"):
            out.append((s.addr, len(s.data), s))
        elif s.kind == "nw_code":
            raise SystemExit(f"unexpected 48-bit NW code section {s.name}")
    return out


def depack7(syx_path, log):
    """section 7 as stored in a .syx -> depacked boot stream (the brick checker's own path)"""
    import tempfile
    with tempfile.TemporaryDirectory() as t:
        run([TOOL, "-i", syx_path, "-d", "7", "-o", t], log)
        return (Path(t) / "section_7_blob.bin").read_bytes()


def final_image(stream):
    import sharc_ldr_parse as P
    blocks, errors, stop = P.parse(stream)
    img = P.Image(blocks)
    for b in blocks:
        img.apply(stream, b)
    return img


def diff_images(stock, cand):
    """-> sorted (start, end) loader-address ranges where the two final images differ (data or
    written/unwritten). Regions present in both are compared chunk by chunk; a region only in
    the candidate counts as new in full; a stock region missing from the candidate is an error."""
    sreg = {s: (e, d, o) for (s, e), d, o in zip(stock.regions, stock.data, stock.owner)}
    out = []
    for (s, e), d, o in zip(cand.regions, cand.data, cand.owner):
        if s not in sreg:
            if any(a < e and s < b for a, (b, _, _) in sreg.items()):
                raise SystemExit(f"candidate region {s:#x}-{e:#x} overlaps a stock region of another shape")
            out.append((s, e))
            continue
        se, sd, so = sreg[s]
        if se != e:
            raise SystemExit(f"region {s:#x} changed size: {se:#x} -> {e:#x}")
        ob, sob = o.tobytes(), so.tobytes()
        cur = None
        step = 4096
        for c in range(0, e - s, step):
            if d[c:c + step] == sd[c:c + step] and ob[2 * c:2 * (c + step)] == sob[2 * c:2 * (c + step)]:
                continue
            for i in range(c, min(c + step, e - s)):
                if d[i] != sd[i] or (o[i] >= 0) != (so[i] >= 0):
                    if cur and cur[1] == s + i:
                        cur[1] += 1
                    else:
                        if cur:
                            out.append(tuple(cur))
                        cur = [s + i, s + i + 1]
        if cur:
            out.append(tuple(cur))
    missing = set(sreg) - {s for (s, _) in cand.regions}
    if missing:
        raise SystemExit(f"stock regions missing from the candidate: {sorted(hex(m) for m in missing)}")
    return sorted(out)


def symbol_word(e, sym, name):
    """the 32-bit little-endian value of a data symbol in the linked image"""
    a = sym[name]
    for s in e.sections:
        if s.kind == "data" and s.addr <= a and a + 4 <= s.addr + len(s.data):
            return struct.unpack_from("<I", s.data, a - s.addr)[0]
    raise SystemExit(f"data symbol {name} is not in a loaded data section")


def layout_check(rec, e, fills):
    """The layout as built (T6.3, after SE01's review: selcc accepts a negative-size typedef, so the C 'slot
    fits' guards are no-ops in the SHARC build). Every block the recipe adds - each code, data and bss section
    as linked, the stubs' save area, every FILL (state) - must lie inside its own slot, must not overlap any
    other (so neither FTD1's blocks nor ours), and must stay out of the cache and L2 ranges in FORBIDDEN.
    fills: [(addr, bytes, why, slot)]. Returns (rows, problems)."""
    code, code2, code3 = rec.get("code", CODE), rec.get("code2", CODE2), rec.get("code3")
    slot_of = {"t38_code": code, "t38_code2": code2, "t38_code3": code3}
    rows = []
    for addr, n, s in ranges_of(e):
        rows.append(dict(what=f"{s.name} ({s.kind})", lo=addr, hi=addr + n, slot=slot_of.get(s.name, DATA)))
    rows.append(dict(what="hook register save area", lo=SAVE, hi=SAVE + (stub.SAVE_BYTES + 3) // 4 * 4,
                     slot=(SAVE, L1B1_LIMIT)))
    for addr, n, why, slot in fills:
        rows.append(dict(what=f"FILL: {why}", lo=addr, hi=addr + n, slot=slot))
    rows = [r for r in rows if r["hi"] > r["lo"]]
    problems = []
    for r in rows:
        if r["slot"] is None or not (r["slot"][0] <= r["lo"] and r["hi"] <= r["slot"][1]):
            slot = "%#x-%#x" % tuple(r["slot"]) if r["slot"] else "(none)"
            problems.append(f"{r['what']} {r['lo']:#x}-{r['hi']:#x} is outside its slot {slot}")
        for lo, hi, name in FORBIDDEN:
            if r["lo"] < hi and lo < r["hi"]:
                problems.append(f"{r['what']} {r['lo']:#x}-{r['hi']:#x} overlaps {name}")
    for i, a in enumerate(rows):
        for b in rows[i + 1:]:
            if a["lo"] < b["hi"] and b["lo"] < a["hi"]:
                problems.append(f"{a['what']} {a['lo']:#x}-{a['hi']:#x} overlaps {b['what']} {b['lo']:#x}-{b['hi']:#x}")
    out = [dict(what=r["what"], core="%#x-%#x" % (r["lo"], r["hi"]), bytes=r["hi"] - r["lo"],
                slot="%#x-%#x" % tuple(r["slot"]) if r["slot"] else None,
                slot_left=(r["slot"][1] - r["hi"]) if r["slot"] else None) for r in sorted(rows, key=lambda r: r["lo"])]
    return out, problems


def build(name, out_root=ROOT / "out/T3.8", odir=None):
    rec = RECIPES[name]
    if odir is None:
        odir = ROOT / rec["outdir"] if "outdir" in rec else out_root / name
    state, code2 = rec.get("state", STATE), rec.get("code2", CODE2)
    if state[1] > code2[0] and code2[1] > state[0] and "state" in rec:
        raise SystemExit("the recipe's state and code2 ranges overlap")
    bdir = odir / "build"
    log = []
    if sha(STOCK_SYX.read_bytes()) != STOCK_SYX_SHA:
        raise SystemExit("stock .syx hash mismatch")
    if not STOCK7.exists():
        STOCK7.parent.mkdir(parents=True, exist_ok=True)
        run([TOOL, "-i", STOCK_SYX, "-d", "7", "-o", STOCK7.parent], log)
    stock7 = STOCK7.read_bytes()
    if sha(stock7) != STOCK7_SHA:
        raise SystemExit("stock section 7 hash mismatch")

    e = compile_link(name, rec, bdir, log)
    census = collections.Counter()
    for src in rec["c"] + rec.get("c2", []) + rec.get("c3", []):
        census += register_census((bdir / f"{Path(src).stem}.s").read_text())
    extra = sorted(set(census) - CENSUS_ALLOWED)
    if extra:
        raise SystemExit(f"our C code writes registers the hook stubs do not preserve: {extra}")
    sym = {k[:-1]: v for k, v in e.symbols.items() if k.endswith(".") and not k.startswith("_")}

    # where everything landed
    placed = []
    for addr, n, s in ranges_of(e):
        lo, hi = {"t38_code": rec.get("code", CODE), "t38_code2": code2,
                  "t38_code3": rec.get("code3", (0, 0))}.get(s.name, DATA)
        if not (lo <= addr and addr + n <= hi):
            raise SystemExit(f"section {s.name} at {addr:#x}+{n} is outside {lo:#x}-{hi:#x}")
        placed.append({"section": s.name, "kind": s.kind, "core": "%#x" % addr, "bytes": n,
                       "sw": "%#x" % s.addr if s.kind == "sw_code" else None})
    assert SAVE + stub.SAVE_BYTES <= L1B1_LIMIT

    st = ldr.Stream(stock7)
    for site, old, label in rec["hooks"]:
        new = sym[label]
        st.patch(2 * site, cjump_bytes(old), cjump_bytes(new),
                 why=f"CJUMP at SW {site:#x}: {old:#x} -> {label} {new:#x}")
    for addr, n, s in ranges_of(e):
        if s.kind == "bss":
            st.add_fill(addr, (n + 3) // 4 * 4, why=f"{s.name} (zero-initialised)")
        else:
            st.add_payload(addr, s.data, why=f"{s.name} ({s.kind})")
    st.add_fill(SAVE, (stub.SAVE_BYTES + 3) // 4 * 4, why="hook register save area")
    fills = []
    for addr, n, why in rec["fills"]:
        if not (state[0] <= addr and addr + n <= state[1]):
            raise SystemExit("state fill outside the planned L1 block 2 range")
        st.add_fill(addr, n, why=why)
        fills.append((addr, n, why, state))
    slot_sizes = {}
    for (lo, hi), symname, why in rec.get("fills_sym", []):
        n = symbol_word(e, sym, symname)          # sizeof as the SHARC build computed it
        slot_sizes[symname] = n
        n = (n + 3) // 4 * 4
        st.add_fill(lo, n, why=f"{why} ({symname} = {slot_sizes[symname]})")
        fills.append((lo, n, why, (lo, hi)))
    layout, problems = layout_check(rec, e, fills)
    if problems:
        raise SystemExit("layout check failed:\n  " + "\n  ".join(problems))
    stream = st.to_bytes()

    # ---- checks on the stream ----
    ok, bad, blocks = ldr.check_stream(stream)
    if not ok:
        raise SystemExit(f"spliced stream fails sharc_ldr_parse: {bad}")
    sys.path.insert(0, str(ROOT / "tools/brickcheck"))
    import bc_dsp
    scan = bc_dsp.scan_sizes_and_blocks(b"\0" * 1, stream)   # stored size checked on the .syx below
    if not (scan["stream_ok"] and scan["parse_ok"] and scan["final"]) or scan["cache_hits"] or scan["region_miss"]:
        raise SystemExit(f"stream check failed: {scan}")
    diffs = diff_images(final_image(stock7), final_image(stream))
    allowed = [(ldr.loader_addr(2 * site), ldr.loader_addr(2 * site) + 6) for site, _, _ in rec["hooks"]]
    allowed += [(ldr.loader_addr(CODE[0]), ldr.loader_addr(L1B1_LIMIT)),
                (ldr.loader_addr(state[0]), ldr.loader_addr(state[1])),
                (ldr.loader_addr(code2[0]), ldr.loader_addr(code2[1]))]
    if "code3" in rec:
        allowed.append((ldr.loader_addr(rec["code3"][0]), ldr.loader_addr(rec["code3"][1])))
    merged = []                          # touching ranges as one (SE01's code2 starts where its state ends)
    for lo, hi in sorted(allowed):
        if merged and lo <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], hi)
        else:
            merged.append([lo, hi])
    allowed = [tuple(m) for m in merged]
    stray =[(a, b) for a, b in diffs if not any(lo <= a and b <= hi for lo, hi in allowed)]
    if stray:
        raise SystemExit("final image differs from stock outside the planned ranges: %s"
                         % [(hex(a), hex(b)) for a, b in stray])

    odir.mkdir(parents=True, exist_ok=True)
    (odir / "section7.bin").write_bytes(stream)
    syx = odir / rec.get("syx", f"{name}.syx")
    version = rec.get("version", name)
    r = run([TOOL, "-i", STOCK_SYX, "-c", "7", odir / "section7.bin", "-V", version, "-o", syx], log)
    if not any(f'set to "{version}"' in line for line in log):
        raise SystemExit("the version field was not set (see the tool's output)")
    back = depack7(syx, log)
    if back != stream:
        raise SystemExit("section 7 depacked from the .syx differs from the intended stream")

    info = {
        "name": name, "what": rec["what"],
        "stock_section7_sha256": STOCK7_SHA,
        "section7": {"path": rel(odir / "section7.bin"), "bytes": len(stream),
                     "sha256": sha(stream), "stock_bytes": len(stock7), "added_bytes": len(stream) - len(stock7),
                     "blocks": len(blocks)},
        "syx": {"path": rel(syx), "bytes": syx.stat().st_size, "sha256": sha(syx.read_bytes())},
        "placed": placed, "symbols": {k: "%#x" % v for k, v in sorted(sym.items(), key=lambda kv: kv[1])},
        "sources": {src: sha((ROOT / src).read_bytes()) for src in
                    rec["c"] + rec.get("c2", []) + rec.get("c3", []) + [f"tools/dspsplice/src/{h}" for h in ("fxio.h",)]
                    + ["fx/fxmath.h"] + [str(Path(src).with_suffix(".h")) for src in rec["c"] + rec.get("c2", []) + rec.get("c3", [])
                                         if (ROOT / Path(src).with_suffix(".h")).exists()]},
        "layout_check": {"rows": layout, "problems": problems, "forbidden": [("%#x" % a, "%#x" % b, w) for a, b, w in FORBIDDEN],
                         "slot_sizes_from_image": slot_sizes},
        "patches": st.patches, "added_blocks": st.added,
        "image_diff_ranges": [("%#x" % a, "%#x" % b) for a, b in diffs],
        "shift_immediates_in_our_asm": selcc_shift_imm_count(bdir, rec),
        "register_census": dict(sorted(census.items())),
        "save_area": {"core": "%#x" % SAVE, "bytes": stub.SAVE_BYTES, "regs": stub.SAVE_REGS},
        "log": log,
    }
    (odir / "build.json").write_text(json.dumps(info, indent=1))
    return info


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("name", choices=sorted(RECIPES))
    ap.add_argument("--outdir", help="write here instead of the recipe's own directory (rebuild checks)")
    a = ap.parse_args()
    info = build(a.name, odir=Path(a.outdir).resolve() if a.outdir else None)
    print(f"{a.name}: {info['what']}")
    print(f"  section 7: {info['section7']['bytes']} bytes (+{info['section7']['added_bytes']}), "
          f"{info['section7']['blocks']} blocks, sha256 {info['section7']['sha256']}")
    print(f"  .syx: {info['syx']['path']} {info['syx']['bytes']} bytes, sha256 {info['syx']['sha256']}")
    for p in info["patches"]:
        print(f"  patch {p['why']}: {p['old']} -> {p['new']}")
    for b in info["added_blocks"]:
        print(f"  block {b['kind']:7s} {b['core']:>10s} {b['bytes']:6d} B  {b['why']}")
    print(f"  shift immediates in our assembly: {info['shift_immediates_in_our_asm']}")
    print(f"  layout check: {len(info['layout_check']['rows'])} blocks, problems: {info['layout_check']['problems'] or 'none'}")
    for r in info["layout_check"]["rows"]:
        print(f"    {r['core']:>19s} {r['bytes']:6d} B  slot {r['slot']}, {r['slot_left']} B left  {r['what']}")
    if info["layout_check"]["slot_sizes_from_image"]:
        print(f"  slot sizes read from the linked image: {info['layout_check']['slot_sizes_from_image']}")


if __name__ == "__main__":
    main()
