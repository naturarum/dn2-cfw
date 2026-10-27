#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Build a DSP-section (section 7) candidate with our own code spliced in (T3.8).

    python3 tools/dspsplice/build.py CG01      # milestone 1: stock chorus, wet output -6.02 dB
    python3 tools/dspsplice/build.py FLG1      # milestone 2: the flanger in the chorus slot
    python3 tools/dspsplice/build.py CMP0      # T6.3: FTD1 + the 3-band master compressor (frozen sources, src/cmp0/)
    python3 tools/dspsplice/build.py CMP1      # T6.3 CMP1's DSP: CMP0 + the review's fixes (fx/mbcomp.c #12-#14)
    python3 tools/dspsplice/build.py CMP2      # T6.3 CMP2's DSP: CMP1 + the band knobs from the frame tail
    python3 tools/dspsplice/build.py CMP2D     # T6.3 CMP2D: CMP2's DSP with a load meter (diagnostic, src/cmp2d/)
    python3 tools/dspsplice/build.py CMP2E     # T6.3 CMP2E: CMP2D + heartbeat, breadcrumbs, LOW MUP probe (src/cmp2e/)
    python3 tools/dspsplice/build.py CMP2F     # T6.3 CMP2F: CMP2E moved out of 0x315f8c-0x318000 + a memory scanner
    python3 tools/dspsplice/build.py CMP3A     # T6.3 the first CMP3 (2026-10-18, superseded; frozen sources, src/cmp3a/)
    python3 tools/dspsplice/build.py CMP3      # T6.3 CMP3's DSP: CMP3A on CP2F's layout + band GR meters in dB
    python3 tools/dspsplice/build.py SC01      # SC01: CMP3 + the tape echo (stock FDBK law) + breathing meters
    python3 tools/dspsplice/build.py CMP4      # CMP4: CMP3 with the stock delay back and the breathing meters
    python3 tools/dspsplice/build.py FL01      # FL01: CMP4 with the retuned flanger glue (knob C is TIME, T5.6)
    python3 tools/dspsplice/build.py FL02      # FL02: FL01 with the flanger's speed in sync with the tempo
    python3 tools/dspsplice/build.py DT01      # DT01: FL02 on a code diet (the same sound, less code)
    python3 tools/dspsplice/build.py DC01      # DC01: DT01 with the compiler fix (peephole.py: shorter field accesses)
    python3 tools/dspsplice/build.py DC02      # DC02: DC01's fix in its cautious mode (stock-proven pairings only)
    python3 tools/dspsplice/build.py SP01      # SP01: DT01 with SPNG, the spring reverb, in the reverb slot

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
import re
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
REVERB_CALL, REVERB = 0x1c1d11, 0xb836bc           # FX bus 0x1c1738: CJUMP to the stock reverb (T3.4)
REVERB_INIT_CALL, REVERB_INIT = 0x1c1643, 0xb83dc1  # effect init 0x1c1445: CJUMP to its init (R4 = 0x80000018)
COMP_CALL, COMP = 0x1c215c, 0xb8541d             # master 0x1c1f1e: CJUMP to the stock compressor (T6.4 1.2)
COMP_INIT_CALL, COMP_INIT = 0x1c16bb, 0xb85501   # effect init 0x1c1445: CJUMP to its init
CMP3_TD_PROCESS, CMP3_TD_INIT = 0x18ad4a, 0x18ac0a   # CMP3's td_process / td_init (out/T6.3/CMP3-dsp/build.json)
# CMP4: where our own code ran on the unit (notes/SC02-plan.md 1, the proven map): block 1's stubs and flanger (FLG1 to
# CMP3), code4's compressor glue and tail reader (CMP2E to CMP3), block 2's compressor (CMP0 to CP2F) and tape delay
# (FTD1 to CMP3); core byte addresses
PROVEN = [(0x2dd600, 0x2e1c36, "L1 block 1: the stubs and the flanger (FLG1 to CMP3)"),
          (0x2e2900, 0x2e4f46, "code4: the compressor glue and the tail reader (CMP2E to CMP3)"),
          (0x308400, 0x30ff76, "L1 block 2: the compressor (CMP0 to CP2F)"),
          (0x310000, 0x315f8c, "L1 block 2: the tape delay (FTD1 to CMP3)")]

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
    # T5.3: FTD1 with the tape echo (fx/spaceecho.c) in place of the tape delay, one-page layout
    # (src/se_glue.c, verify_se.py). Its code (about 38 KB) does not fit FTD1's 0x310000-0x318000,
    # so it starts right after the flanger state: 0x308400-0x318000 is below the PM cache whether that
    # cache is 16 KB (0x31c000, what the start-up's size fields say) or 32 KB (0x318000).
    # Since SC01 (2026-10-21: the stock FDBK law) fx/spaceecho.c and .h are SC01's; SE01's as built (commit 8e3e9f3,
    # image 60894300, never flashed) are frozen in src/se01/, first on the include path, so this recipe still rebuilds.
    "SE01": dict(
        what="T5.3: the flanger in the chorus slot and the tape echo (fx/spaceecho.c) in the delay slot, "
             "one-page layout (AGE from frame word 0xa18, k 40)",
        c=["fx/flanger.c", "tools/dspsplice/src/flg_glue.c"],
        c2=["tools/dspsplice/src/se01/spaceecho.c", "tools/dspsplice/src/se_glue.c"],
        inc=["tools/dspsplice/src/se01", "fx", "tools/dspsplice/src"],
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
    # bit (tools/dspsplice/verify_cmp.py CMP1, (h)). The compressor as flashed on the owner's unit (CMP1.syx cbe4eac8,
    # section 7 ee82aa7c: guard 2^16, no pre-block crossover-state check) is frozen in src/cmp1/ (mbcomp.c, mbcomp.h,
    # cmp_glue.c), first on the include path; fx/mbcomp.c moved on after the flash (CMP2 review, 2026-10-17).
    "CMP1": dict(
        what="T6.3 CMP1: FTD1 (flanger, tape delay) with the 3-band master compressor (fx/mbcomp.c, CMP0 plus the "
             "review's fixes: complete restart, quiet return after a trip, scrub) in place of the stock compressor, "
             "on the stock knobs, crossovers fixed at 196 Hz / 2.61 kHz",
        c=["fx/flanger.c", "tools/dspsplice/src/flg_glue.c"],
        c2=["fx/tapedelay.c", "tools/dspsplice/src/td_glue.c"],
        c3=["tools/dspsplice/src/cmp1/mbcomp.c", "tools/dspsplice/src/cmp1/cmp_glue.c"],
        inc=["tools/dspsplice/src/cmp1", "fx", "tools/dspsplice/src"],
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
    # T6.3 CMP2's DSP (2026-10-17): CMP1 with the band knobs live from the SPI frame tail of comp-link-dn2 0.1
    # (src/cmp_tail.c; notes/T6.3-cmp2.md). CMP1's compressor and glue are frozen in src/cmp1/ (recipe CMP1
    # above rebuilds ee82aa7c, the unit's). cmp_tail.c goes after the tape delay in block 2 (FTD1's code2, 8 KB free there; code3 has 406 bytes
    # left), its state CmpTail in its own FILL after CmpSlot.
    # CMP2's sources as built (commit c48b179, the image on the owner's unit since 2026-10-18: section 7 1a888114) are
    # frozen in src/cmp2/ (mbcomp.c, mbcomp.h, cmp_glue.c, cmp_tail.c, cmp_tail.h, byte for byte as committed), first
    # on the include path, since CMP3 (2026-10-18) moved fx/mbcomp.c and the glue on.
    "CMP2": dict(
        what="T6.3 CMP2: FTD1 (flanger, tape delay) with the 3-band master compressor (fx/mbcomp.c), the band knobs "
             "and both crossovers from k 70..86 in the SPI frame tail (comp-link-dn2); until a valid tail has "
             "arrived, CMP1's bands from the stock knobs",
        c=["fx/flanger.c", "tools/dspsplice/src/flg_glue.c"],
        c2=["fx/tapedelay.c", "tools/dspsplice/src/td_glue.c", "tools/dspsplice/src/cmp2/cmp_tail.c"],
        c3=["tools/dspsplice/src/cmp2/mbcomp.c", "tools/dspsplice/src/cmp2/cmp_glue.c"],
        inc=["tools/dspsplice/src/cmp2", "fx", "tools/dspsplice/src"],
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
        fills_sym=[((0x2e1c40, 0x2e2400), "cmp_slot_bytes", "3-band compressor state (CmpSlot), zeroed at boot"),
                   ((0x2e2400, 0x2e2800), "cmp_tail_bytes", "frame-tail reader state (CmpTail), zeroed at boot")],
        state=(0x300000, 0x308400), code=(0x2dd600, 0x2e1c40), code3=(0x308400, 0x310000),
        outdir="out/T6.3/CMP2-dsp", syx="CMP2-dsp.syx", version="CM2D"),
    # T6.3 CMP2D's DSP (2026-10-18): a diagnostic build (notes/T6.3-cmp2d.md): CMP2's DSP (src/cmp2d/ = src/cmp2/'s
    # frozen sources) with a load meter in place of the glue's band meters: cmp_load.c and cmp_clk.s (two leaf functions,
    # EMUCLK and I6, assembled with selas: recipe key asm2) after cmp_tail.c in code2, the state CmpLoad in its own FILL
    # after CmpTail's slot. The audio is CMP2's sample for sample.
    "CMP2D": dict(
        what="T6.3 CMP2D: CMP2's DSP (FTD1's flanger and tape delay, the 3-band master compressor with the band knobs "
             "from the SPI frame tail) with a DSP load meter in reply half-words 8..11 and the stock meter word "
             "(diagnostic build)",
        c=["fx/flanger.c", "tools/dspsplice/src/flg_glue.c"],
        c2=["fx/tapedelay.c", "tools/dspsplice/src/td_glue.c", "tools/dspsplice/src/cmp2d/cmp_tail.c",
            "tools/dspsplice/src/cmp2d/cmp_load.c"],
        asm2=["tools/dspsplice/src/cmp2d/cmp_clk.s"],
        c3=["tools/dspsplice/src/cmp2d/mbcomp.c", "tools/dspsplice/src/cmp2d/cmp_glue.c"],
        inc=["tools/dspsplice/src/cmp2d", "fx", "tools/dspsplice/src"],
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
        fills_sym=[((0x2e1c40, 0x2e2400), "cmp_slot_bytes", "3-band compressor state (CmpSlot), zeroed at boot"),
                   ((0x2e2400, 0x2e2800), "cmp_tail_bytes", "frame-tail reader state (CmpTail), zeroed at boot"),
                   ((0x2e2800, 0x2e2900), "cmp_load_bytes", "load meter state (CmpLoad), zeroed at boot")],
        state=(0x300000, 0x308400), code=(0x2dd600, 0x2e1c40), code3=(0x308400, 0x310000),
        outdir="out/T6.3/CMP2D-dsp", syx="CMP2D-dsp.syx", version="D2DS"),
    # T6.3 CMP2E's DSP (2026-10-18): CMP2D with breadcrumbs (src/cmp2e/cmp_crumb.h, in the glue and mbcomp.c), a heartbeat,
    # a trip/re-init counter, the master-latched flag and a probe switch in cmp_tail.c (notes/T6.3-cmp2e.md). Layout as
    # CMP2D's, but the glue (with its breadcrumbs, code3 would overflow by 20 bytes) moves to a fourth code region after
    # CmpLoad's slot, 0x2e2900-0x2e6000 (L1 block 1's free tail, as CMP3 does; mbcomp.c and the stubs stay in code3).
    # The audio is CMP2's sample for sample but in the probe mode.
    "CMP2E": dict(
        what="T6.3 CMP2E: CMP2D's DSP with a heartbeat, breadcrumbs (a stage id per stage of our code), a trip/re-init "
             "counter and the master-latched flag in reply half-words 8..10, the highest load in half-word 11, and a "
             "probe switch (LOW XOVR word >= 126: LOW MUP not applied) (diagnostic build)",
        c=["fx/flanger.c", "tools/dspsplice/src/flg_glue.c"],
        c2=["fx/tapedelay.c", "tools/dspsplice/src/td_glue.c", "tools/dspsplice/src/cmp2e/cmp_tail.c",
            "tools/dspsplice/src/cmp2e/cmp_load.c"],
        asm2=["tools/dspsplice/src/cmp2e/cmp_clk.s"],
        c3=["tools/dspsplice/src/cmp2e/mbcomp.c"], c4=["tools/dspsplice/src/cmp2e/cmp_glue.c"],
        inc=["tools/dspsplice/src/cmp2e", "fx", "tools/dspsplice/src"],
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
        fills_sym=[((0x2e1c40, 0x2e2400), "cmp_slot_bytes", "3-band compressor state (CmpSlot), zeroed at boot"),
                   ((0x2e2400, 0x2e2800), "cmp_tail_bytes", "frame-tail reader state (CmpTail), zeroed at boot"),
                   ((0x2e2800, 0x2e2900), "cmp_load_bytes", "load meter and readout state (CmpLoad), zeroed at boot")],
        state=(0x300000, 0x308400), code=(0x2dd600, 0x2e1c40), code3=(0x308400, 0x310000),
        code4=(0x2e2900, 0x2e6000),
        outdir="out/T6.3/CMP2E-dsp", syx="CMP2E-dsp.syx", version="E2DS"),
    # T6.3 CMP2F's DSP (2026-10-18): CMP2E with every byte of ours out of 0x315f8c-0x318000 (CMP2E's LOW MUP code sat at
    # 0x3166xx; on the unit the DSP stopped there, step 4): cmp_tail.c after the glue in code4, cmp_load.c and cmp_clk.s
    # after mbcomp.c in code3, code2 the tape delay alone (FTD1's bytes). A memory scanner (cmp_load.c) checks a slice
    # per block of the page table this script writes (recipe key scan): watermarked free L1 ranges (payload blocks of
    # decode-safe parcels, the vacated range in small pages) and our own code and constant data (Fletcher-32 per page).
    # notes/T6.3-cmp2f.md.
    "CMP2F": dict(
        what="T6.3 CMP2F: CMP2E (heartbeat, breadcrumbs, LOW MUP probe) with its code out of 0x315f8c-0x318000 and a "
             "memory scanner (watermarked free L1 ranges, our code and constant data per page) in the HIGH and right "
             "bars (diagnostic build)",
        c=["fx/flanger.c", "tools/dspsplice/src/flg_glue.c"],
        c2=["fx/tapedelay.c", "tools/dspsplice/src/td_glue.c"],
        asm2=["tools/dspsplice/src/cmp2f/cmp_clk.s"],
        c3=["tools/dspsplice/src/cmp2f/mbcomp.c", "tools/dspsplice/src/cmp2f/cmp_load.c"],
        c4=["tools/dspsplice/src/cmp2f/cmp_glue.c", "tools/dspsplice/src/cmp2f/cmp_tail.c"],
        inc=["tools/dspsplice/src/cmp2f", "fx", "tools/dspsplice/src"],
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
        fills_sym=[((0x2e1c40, 0x2e2400), "cmp_slot_bytes", "3-band compressor state (CmpSlot), zeroed at boot"),
                   ((0x2e2400, 0x2e2800), "cmp_tail_bytes", "frame-tail reader state (CmpTail), zeroed at boot"),
                   ((0x2e2800, 0x2e2900), "cmp_load_bytes", "load meter, readout and scanner state (CmpLoad), zeroed at boot")],
        state=(0x300000, 0x308400), code=(0x2dd600, 0x2e1c40), code3=(0x308400, 0x310000),
        code4=(0x2e2900, 0x2e6000),
        scan=dict(table=0x2e6880, table_slot=(0x2e6880, 0x2e7000),
                  # (lo, hi, page bytes (0: one page), what); lo ("after", section) = its end, ("slot_end", size symbol,
                  # base) = base + the size, "table_end" = the page table's end
                  marks=[(0x315f8c, 0x316000, 0, "vacated: CMP2's cmp_tail.c (fetch)"),
                         (0x316000, 0x317000, 0x100, "vacated: CMP2's cmp_tail.c (apply; LOW MUP at 0x3166xx)"),
                         (0x317000, 0x318000, 0x400, "vacated: CMP2D/CMP2E's cmp_load.c"),
                         (("after", "t38_code3"), 0x310000, 0, "code3's free tail"),
                         (("after", "t38_code4"), 0x2e6000, 0, "code4's free tail"),
                         (("slot_end", "cmp_slot_bytes", 0x2e1c40), 0x2e2400, 0, "CmpSlot's slot tail"),
                         (("slot_end", "cmp_tail_bytes", 0x2e2400), 0x2e2800, 0, "CmpTail's slot tail"),
                         (("slot_end", "cmp_load_bytes", 0x2e2800), 0x2e2900, 0, "CmpLoad's slot tail"),
                         (("after", "t38_data"), 0x2e6800, 0, "the data slot's tail"),
                         ("table_end", 0x2e7000, 0, "after the page table"),
                         (0x2e7000, 0x2e8000, 0, "0x2e7000-0x2e8000, below the 32 KB DM cache")],
                  code=[("t38_code", 0x1800), ("t38_code4", 0x1200), ("t38_data", 0x100), ("t38_code3", 0x1800),
                        ("t38_code2", 0x1800)]),
        outdir="out/T6.3/CMP2F-dsp", syx="CMP2F-dsp.syx", version="F2DS"),
    # T6.3 CMP3A = the first CMP3's DSP (2026-10-18; superseded, never flashed): CMP2 with a MIX per band (fx/mbcomp.c
    # MB_BMIX; the glue's fallback sets all three from the stock MIX, so it is CMP2's fallback bit for bit; cmp_tail.c
    # takes tail words 19..21). code3 had 174 bytes left, so the glue (5.4 KB, once per block) moves to a fourth code
    # region in L1 block 1's free tail, 0x2e2800-0x2e6000 (between CmpTail's slot and the data block; inside dnfw's
    # silicon-proven 0x2dd600-0x2e7000, notes/T3.8 3), and code3 keeps mbcomp.c and the two stubs. Its cmp_tail.c ran
    # after the tape delay in code2, to 0x316dba, inside 0x315f8c-0x318000, where code stopped the DSP on the unit
    # (CMP2, CP2E; notes/T6.3-cmp2f.md), so it was put on hold. Its sources as built are frozen in src/cmp3a/
    # (mbcomp.c, mbcomp.h, cmp_glue.c, cmp_tail.c, cmp_tail.h, byte for byte), first on the include path, so this
    # recipe still rebuilds 3a54d9a9; its outputs and evidence moved to out/T6.3/superseded/CMP3A-dsp/.
    "CMP3A": dict(
        what="T6.3 CMP3A (the first CMP3, 2026-10-18, superseded): FTD1 (flanger, tape delay) with the 3-band master "
             "compressor (fx/mbcomp.c), a MIX per band, the band knobs, mixes and crossovers from k 70..89 in the SPI "
             "frame tail (comp-link-dn2); until a valid tail has arrived, CMP2's fallback (CMP1's bands and the stock "
             "MIX from the stock knobs)",
        c=["fx/flanger.c", "tools/dspsplice/src/flg_glue.c"],
        c2=["fx/tapedelay.c", "tools/dspsplice/src/td_glue.c", "tools/dspsplice/src/cmp3a/cmp_tail.c"],
        c3=["tools/dspsplice/src/cmp3a/mbcomp.c"], c4=["tools/dspsplice/src/cmp3a/cmp_glue.c"],
        inc=["tools/dspsplice/src/cmp3a", "fx", "tools/dspsplice/src"],
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
        fills_sym=[((0x2e1c40, 0x2e2400), "cmp_slot_bytes", "3-band compressor state (CmpSlot), zeroed at boot"),
                   ((0x2e2400, 0x2e2800), "cmp_tail_bytes", "frame-tail reader state (CmpTail), zeroed at boot")],
        state=(0x300000, 0x308400), code=(0x2dd600, 0x2e1c40), code3=(0x308400, 0x310000), code4=(0x2e2800, 0x2e6000),
        outdir="out/T6.3/superseded/CMP3A-dsp", syx="CMP3-dsp.syx", version="CM3D"),
    # T6.3 CMP3's DSP (2026-10-21, the final CMP3; notes/T6.3-cmp3.md): CMP3A's compressor, mixes and tail reader on
    # CP2F's layout, which ran on the unit with LOW MUP turned hard and no stop (notes/T6.3-cmp2f.md): code2 = the tape
    # delay alone (FTD1's bytes, as on the unit since FTD1), code3 = mbcomp.c and the two stubs, code4 = the glue then
    # cmp_tail.c from 0x2e2900 (CP2F's code4), and nothing of ours in 0x315f8c-0x318000 (key forbid: a build check).
    # The glue adds the band GR meters (src/cmp_glue.c cmp_mtr: dB, instant attack, 0.5 s hold, 20 dB/s fall) with
    # their state CmpMeter in its own FILL where CP2F's CmpLoad was (0x2e2800-0x2e2900). None of CP2D-CP2F's
    # diagnostics (heartbeat, breadcrumbs, probe switch, load meter, scanner, watermarks, page table).
    # Since SC01 (2026-10-21: meters with the stock motion) src/cmp_glue.c is SC01's; CMP3's as built (commit f48908f,
    # section 7 b2d4e057, on the owner's unit) is frozen in src/cmp3/cmp_glue.c.
    "CMP3": dict(
        what="T6.3 CMP3: FTD1 (flanger, tape delay) with the 3-band master compressor (fx/mbcomp.c), a MIX per band, "
             "the band knobs, mixes and crossovers from k 70..89 in the SPI frame tail (comp-link-dn2), band gain "
             "reduction meters in dB with hold and fall in reply half-words 8..10; until a valid tail has arrived, "
             "CMP2's fallback (CMP1's bands and the stock MIX from the stock knobs); on CP2F's layout",
        c=["fx/flanger.c", "tools/dspsplice/src/flg_glue.c"],
        c2=["fx/tapedelay.c", "tools/dspsplice/src/td_glue.c"],
        c3=["fx/mbcomp.c"], c4=["tools/dspsplice/src/cmp3/cmp_glue.c", "tools/dspsplice/src/cmp_tail.c"],
        inc=["fx", "tools/dspsplice/src"],
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
        fills_sym=[((0x2e1c40, 0x2e2400), "cmp_slot_bytes", "3-band compressor state (CmpSlot), zeroed at boot"),
                   ((0x2e2400, 0x2e2800), "cmp_tail_bytes", "frame-tail reader state (CmpTail), zeroed at boot"),
                   ((0x2e2800, 0x2e2900), "cmp_meter_bytes", "band GR meter state (CmpMeter), zeroed at boot")],
        state=(0x300000, 0x308400), code=(0x2dd600, 0x2e1c40), code3=(0x308400, 0x310000), code4=(0x2e2900, 0x2e6000),
        forbid=[(0x315f8c, 0x318000, "0x315f8c-0x318000: code there stopped the DSP on the unit (CMP2, CP2E); "
                                     "nothing of ours, code or data (notes/T6.3-cmp2f.md)")],
        outdir="out/T6.3/CMP3-dsp", syx="CMP3-dsp.syx", version="CM3D"),
    # SC01 (2026-10-21, notes/SC01.md): the merged line, CMP3 + the tape echo in the delay slot (the stock FDBK law) + the
    # flanger, with band meters that breathe like the stock one. The layout study (notes/SC01.md 1): the tape echo
    # (39.4 KB) and the compressor (31.1 KB) do not fit block 2's proven 0x308400-0x315f8c (56.2 KB), so their
    # functions are placed one by one (key place; split_asm): every per-sample loop and every function that runs each
    # block in L1 block 2 (checked: key l1); the code that runs only when a knob moves, or rarely, in L2 over the stock
    # delay, chorus and compressor routines our hooks bypass (key l2: dead in this build, notes/SC01.md 1.3), patched
    # into the stock L2 code block in place; the boot-time init after the compressor glue in code4, up to 0x2e6000
    # (key level3: the one range of ours where only data has run: CP2F's watermarks, found intact); seven functions the
    # DSP never calls left out (drop). code4's first 9.8 KB, 0x2e2900-0x2e4f46, ran CMP3's glue on the unit.
    # Since SC02's FDBK change (2026-10-21: the loop gain shared by the playing heads) fx/spaceecho.c and .h are SC02's;
    # SC01's as built (section 7 788bbef3, on the owner's unit) are frozen in src/sc01/, first on the include path.
    "SC01": dict(
        what="SC01: the flanger (chorus slot), the tape echo (delay slot, fx/spaceecho.c on the stock FDBK law) and the "
             "3-band master compressor (fx/mbcomp.c, a MIX per band, the band knobs from the SPI frame tail, CMP3's), "
             "band gain-reduction meters in dB with the stock meter's motion; functions placed per region (L1 block 2, "
             "code4, L2 over dead stock FX code)",
        c=["fx/flanger.c", "tools/dspsplice/src/flg_glue.c"],
        c4=["tools/dspsplice/src/cmp_glue.c", "tools/dspsplice/src/cmp_tail.c"],
        split=["tools/dspsplice/src/sc01/spaceecho.c", "tools/dspsplice/src/se_glue.c", "fx/mbcomp.c"], split_default="c2",
        place={
            # never called on the DSP (the Mac's API and tests use them)
            "spaceecho_heads": "drop", "spaceecho_center_hz": "drop", "spaceecho_bw_oct": "drop", "spaceecho_q": "drop",
            "spaceecho_trim": "drop", "mbcomp_ratio": "drop", "mbcomp_gr_db": "drop",
            # level 3: boot-time init, after the glue in code4
            "spaceecho_init": "c4", "mbcomp_init": "c4", "mb_clear": "c4", "mbcomp_snap": "c4",
            # level 2, A (the stock delay): the tape echo's knob-change code and its own copies of the maths helpers
            "se_mix_targets": "l2a", "se_pan_gains": "l2a", "se_filter_coefs": "l2a", "spaceecho_set_bpm": "l2a",
            "spaceecho:fx_f2i": "l2a", "spaceecho:fx_i2f": "l2a", "spaceecho:fx_clampf": "l2a", "spaceecho:fx_log2": "l2a",
            "spaceecho:fx_exp2": "l2a", "spaceecho:fx_sin_q": "l2a", "spaceecho:fx_cos_q": "l2a", "spaceecho:fx_svf_g": "l2a",
            "spaceecho:fx_svf_coef": "l2a", "spaceecho:fx_knob01": "l2a", "spaceecho:fx_randu": "l2a",
            # B (the stock chorus's delay line): the crackle events, the compressor's restart
            "se_crackle_event": "l2b", "mb_restart": "l2b",
            # C (the stock chorus's filter bank and entry): the delay slot's init and its one-pole helper
            "se_init": "l2c", "spaceecho:fx_onepole_a": "l2c",
            # D (the stock compressor): the tape echo's snap, the compressor's knob setter
            "spaceecho_snap": "l2d", "mbcomp_set": "l2d"},
        l1=["se_run", "spaceecho_process", "se_control", "se_crackle_plan", "se_process", "mb_run", "mb_state_expo",
            "mb_out_bands", "mbcomp_process", "mb_control", "mb_implausible"],
        level3=["spaceecho_init", "mbcomp_init", "mb_clear", "mbcomp_snap"], code4_proven=0x2e4f46,
        inc=["tools/dspsplice/src/sc01", "fx", "tools/dspsplice/src"],
        stubs=[("fxh_chorus", [("c", "flg_process", ["R8", "R12"])]),
               ("fxh_chorus_init", [("c", "flg_init", []), ("tail", CHORUS_INIT)]),
               ("fxh_delay", [("c", "se_process", ["R8", "R12"])]),
               ("fxh_delay_init", [("c", "se_init", [])])],
        stubs3=[("fxh_comp", [("c", "cmp_process", ["R8", "R12"])]),
                ("fxh_comp_init", [("c", "cmp_init", []), ("tail", COMP_INIT)])], stubs3_seg="seg_t38c2",
        hooks=[(CHORUS_CALL, CHORUS, "fxh_chorus"), (CHORUS_INIT_CALL, CHORUS_INIT, "fxh_chorus_init"),
               (DELAY_CALL, DELAY, "fxh_delay"), (DELAY_INIT_CALL, DELAY_INIT, "fxh_delay_init"),
               (COMP_CALL, COMP, "fxh_comp"), (COMP_INIT_CALL, COMP_INIT, "fxh_comp_init")],
        fills=[(0x300000, 0x8400, "flanger state (FlgSlot: Flanger + glue), zeroed at boot")],
        fills_sym=[((0x2e1c40, 0x2e2400), "cmp_slot_bytes", "3-band compressor state (CmpSlot), zeroed at boot"),
                   ((0x2e2400, 0x2e2800), "cmp_tail_bytes", "frame-tail reader state (CmpTail), zeroed at boot")],
        state=(0x300000, 0x308400), code=(0x2dd600, 0x2e1c40), code2=(0x308400, 0x315f8c), code4=(0x2e2900, 0x2e6000),
        # SW ranges of stock code dead in this build (notes/SC01.md 1.3), each starting on an even parcel
        l2={"l2a": (0xb83eb0, 0xb84926), "l2b": (0xb84944, 0xb84c1f), "l2c": (0xb84ccc, 0xb84dee),
            "l2d": (0xb84fd4, 0xb85466)},
        forbid=[(0x315f8c, 0x318000, "0x315f8c-0x318000: code there stopped the DSP on the unit (CMP2, CP2E); "
                                     "nothing of ours, code or data (notes/T6.3-cmp2f.md)")],
        outdir="out/SC01/SC01-dsp", syx="SC01-dsp.syx", version="SC1D"),
    # CMP4 (2026-10-22, notes/T6.3-cmp4.md): the owner dropped the tape echo for now (SC01 stopped the DSP on the unit;
    # its knob code in L2 is the lead suspect, notes/SC01-soak.md) and gave the delay slot back to the stock delay:
    # CMP3 without the tape delay, with SC01's breathing meters (src/cmp_glue.c) and comp-link 0.3 (main OS). The delay
    # hooks are not patched (the stock CJUMPs at 0x1c1c58 and 0x1c166c stay), so Elektron's delay runs from its own code
    # with its own state; the tape delay (code2) is neither built nor loaded (key code2_unused: 0x310000-0x318000 keeps
    # the stock bytes, a build check). L1 block 1 stays CMP3's bytes: the two delay stubs are kept, unreachable, their
    # calls aimed at where CMP3's td_process and td_init were (stub step cabs). The rule (notes/SC02-plan.md): every byte
    # of our code where our code ran on the unit (key proven: each code section inside one PROVEN range; no L2; code3
    # and code4 slots end at the proven ends).
    "CMP4": dict(
        what="CMP4: the flanger (chorus slot), the stock delay (delay slot, Elektron's own) and the 3-band master "
             "compressor (fx/mbcomp.c, a MIX per band, the band knobs from the SPI frame tail, CMP3's), band "
             "gain-reduction meters in dB with the stock meter's motion (SC01's); CMP3's layout without the tape delay",
        c=["fx/flanger.c", "tools/dspsplice/src/flg_glue.c"],
        c3=["fx/mbcomp.c"], c4=["tools/dspsplice/src/cmp_glue.c", "tools/dspsplice/src/cmp_tail.c"],
        inc=["fx", "tools/dspsplice/src"],
        stubs=[("fxh_chorus", [("c", "flg_process", ["R8", "R12"])]),
               ("fxh_chorus_init", [("c", "flg_init", []), ("tail", CHORUS_INIT)]),
               ("fxh_delay", [("cabs", CMP3_TD_PROCESS, ["R8", "R12"])]),
               ("fxh_delay_init", [("cabs", CMP3_TD_INIT, [])])],
        stubs3=[("fxh_comp", [("c", "cmp_process", ["R8", "R12"])]),
                ("fxh_comp_init", [("c", "cmp_init", []), ("tail", COMP_INIT)])],
        hooks=[(CHORUS_CALL, CHORUS, "fxh_chorus"), (CHORUS_INIT_CALL, CHORUS_INIT, "fxh_chorus_init"),
               (COMP_CALL, COMP, "fxh_comp"), (COMP_INIT_CALL, COMP_INIT, "fxh_comp_init")],
        stock_hooks=[(DELAY_CALL, DELAY, "the stock delay"), (DELAY_INIT_CALL, DELAY_INIT, "the stock delay's init")],
        fills=[(0x300000, 0x8400, "flanger state (FlgSlot: Flanger + glue), zeroed at boot")],
        fills_sym=[((0x2e1c40, 0x2e2400), "cmp_slot_bytes", "3-band compressor state (CmpSlot), zeroed at boot"),
                   ((0x2e2400, 0x2e2800), "cmp_tail_bytes", "frame-tail reader state (CmpTail), zeroed at boot")],
        state=(0x300000, 0x308400), code=(0x2dd600, 0x2e1c40), code3=(0x308400, 0x30ff76), code4=(0x2e2900, 0x2e4f46),
        code2_unused=True, proven=PROVEN,
        forbid=[(0x315f8c, 0x318000, "0x315f8c-0x318000: code there stopped the DSP on the unit (CMP2, CP2E); "
                                     "nothing of ours, code or data (notes/T6.3-cmp2f.md)")],
        outdir="out/CMP4/CMP4-dsp", syx="CMP4-dsp.syx", version="CM4D"),
}
# FL01 (2026-10-22, notes/T5.6-flanger-retune.md): CMP4 with the retuned flanger glue, tools/dspsplice/src/flg_glue2.c
# (the chorus page's knob C is TIME through fx/flanger_unit.h's version 2; fx/flanger.c unchanged) in place of
# flg_glue.c, in the same place: block 1 after fx/flanger.c, where CMP4's glue ran. The glue is shorter (1,604 B against
# 1,636 B), so block 1 ends below CMP4's end; the stubs, the flanger, code3, code4, the data, the fills and the hooks
# are CMP4's (the stubs' calls follow flg_init and flg_process). The same placement rule (key proven).
RECIPES["FL01"] = dict(
    RECIPES["CMP4"],
    what="FL01: CMP4 with the retuned flanger glue (flg_glue2.c: knob C is TIME, the centre delay from 0.05 ms through "
         "CMP4's 0.9 ms flanger at 32 to a 25.7 ms chorus; WDTH 127 wide), in the place of CMP4's glue",
    c=["fx/flanger.c", "tools/dspsplice/src/flg_glue2.c"], h=["fx/flanger_unit.h"],
    outdir="out/FL01/FL01-dsp", syx="FL01-dsp.syx", version="FL1D")
# FL02 (2026-10-23, notes/T5.6-flanger-retune.md section 7): FL01 with the flanger's speed in sync with the tempo
# (FUNC + turn on SPD, main OS: flanger-sync-dn2). tools/dspsplice/src/flg_glue3.c in place of flg_glue2.c (block 1,
# the same place; SPD goes through flg_spd) and tools/dspsplice/src/flg_sync.c (flg_spd: the frame word whole, the 12
# synced values, the tempo) in code2 from 0x310000, inside the proven 0x310000-0x315f8c where FTD1 to CMP3's tape delay
# ran (free on FL01: code2 not loaded there). fx/flanger.c unchanged (its FL_SYNC and flanger_set_bpm, never called
# on FL01). The same placement rule (key proven), 0x315f8c-0x318000 forbidden.
RECIPES["FL02"] = dict(
    {k: v for k, v in RECIPES["FL01"].items() if k != "code2_unused"},
    what="FL02: FL01 with the flanger's speed in sync with the tempo: flg_glue3.c (FL01's glue, SPD through flg_spd) "
         "in its place, flg_sync.c (the 12 synced values, FL_SYNC and the tempo) in code2 from 0x310000",
    c=["fx/flanger.c", "tools/dspsplice/src/flg_glue3.c"], c2=["tools/dspsplice/src/flg_sync.c"],
    h=["fx/flanger_unit.h", "tools/dspsplice/src/flg_slot3.h"], code2=(0x310000, 0x315f8c),
    outdir="out/FL02/FL02-dsp", syx="FL02-dsp.syx", version="FL2D")
# DT01 (2026-10-23, notes/DT01-code-diet.md): FL02 on a code diet, the same sound. The C sources FL02's but the flanger
# (tools/dspsplice/src/dt01/: fx/flanger.c with the Flanger struct's rings last, so every other field has a short
# offset), and the diet on selcc's output (keys drop, share: diet_asm): the flanger's four API functions the DSP never
# calls and the compressor's two (mbcomp_ratio, mbcomp_gr_db) left out; fxmath.h's helpers in one copy, the flanger's
# (the compressor's nine copies, the same text, go); the two delay stubs, unreachable since CMP4 (the stock delay's
# CJUMPs are not patched), not built. Key reach: every function linked is reached from the hooks. The same places as
# FL02 (key proven), each code section shorter.
RECIPES["DT01"] = dict(
    RECIPES["FL02"],
    what="DT01: FL02 on a code diet (the same sound): the flanger's rings last in its struct (dt01/flanger.c), the "
         "never-called functions and the two dead delay stubs left out, fxmath.h's helpers in one copy (the flanger's)",
    c=["tools/dspsplice/src/dt01/flanger.c", "tools/dspsplice/src/flg_glue3.c"],
    c3=["tools/dspsplice/src/dt01/mbcomp.c"],
    h=["tools/dspsplice/src/dt01/flanger_unit.h", "tools/dspsplice/src/flg_slot3.h", "fx/mbcomp.h"],
    inc=["tools/dspsplice/src/dt01", "fx", "tools/dspsplice/src"],
    stubs=[("fxh_chorus", [("c", "flg_process", ["R8", "R12"])]),
           ("fxh_chorus_init", [("c", "flg_init", []), ("tail", CHORUS_INIT)])],
    drop=["flanger_man_ms", "flanger_damp_hz", "flanger_rate_hz", "flanger_delay_at", "mbcomp_ratio", "mbcomp_gr_db"],
    share={"from": "flanger", "names": ["fx_f2i", "fx_i2f", "fx_clampf", "fx_log2", "fx_exp2", "fx_sin_q", "fx_cos_q",
                                        "fx_svf_g", "fx_knob01"]},
    reach=True,
    outdir="out/DT01/DT01-dsp", syx="DT01-dsp.syx", version="DT1D")
# DC01 (2026-10-24, notes/DC01-compiler-fix.md): DT01 with the compiler fix, the same sources, the same sound, less code:
# key peephole runs tools/dspsplice/peephole.py over selcc's output after the diet (A: a field's constant offset folded
# into the access, DM(k,I4); B: the struct pointer loaded from the frame straight into I4), each change kept only where
# the block it is in does the same before and after (memory accesses, branches, every live register). The same places
# as DT01 (key proven), each code section shorter.
RECIPES["DC01"] = dict(
    RECIPES["DT01"],
    what="DC01: DT01 with the compiler fix (the same sound): selcc's field accesses shortened by a checked peephole "
         "pass (field offsets in the access, struct pointers loaded straight into I4)",
    peephole="AB",
    outdir="out/DC01/DC01-dsp", syx="DC01-dsp.syx", version="DC1D")
# DC02 (2026-10-24, notes/DC01-compiler-fix.md section 5): DC01, the cautious version (the review of DC01): the pass in
# mode "ABs", stock-proven pairings only: B is not used where the instruction right after the new load into I4 is a
# 48-bit user of I4 (the stock code never has that pairing). Everything else as DC01.
RECIPES["DC02"] = dict(
    RECIPES["DC01"],
    what="DC02: DT01 with the compiler fix in its cautious mode (the same sound): selcc's field accesses shortened by "
         "a checked peephole pass, a struct pointer loaded straight into I4 only where a 16- or 32-bit instruction "
         "uses it next (the pairings the stock code has)",
    peephole="ABs",
    outdir="out/DC02/DC02-dsp", syx="DC02-dsp.syx", version="DC2D")

# SP01 (2026-10-24, notes/SP01-spring-pack.md): DT01 with SPNG, the spring reverb (fx/spring.c, notes/T5.5), in the
# stock reverb slot. fx/spring.c and its glue (tools/dspsplice/src/spng_glue.c) in code2 after flg_sync.c, then the
# reverb's two hook stubs (key stubs2), inside the proven 0x310000-0x315f8c where FTD1 to CMP3's tape delay ran on the
# unit (key c2_tail: linked after every other object, so flg_sync.c keeps its place and every other section, the
# constant data included, keeps DT01's bytes; spring.c's constants follow DT01's in the data region). The reverb's
# CJUMPs (process 0x1c1d11, init 0x1c1643) go to the stubs; the init stub does not run the stock init (it would set up
# its lines over our state). The spring's state, SpgSlot (43 KB), lives at the start of the stock reverb's DDR state
# (0x80000018, 0x59114 bytes; the boot stream zero-fills it): key ddr_slot checks the size the SHARC build computes.
# Since 2026-10-26 (SPNG v2 in fx/spring.c) its spring.c and spring.h are frozen in tools/dspsplice/src/sp01/.
# The diet as DT01's, plus spring.c's seven knob-law functions the DSP never calls (the tests and the write-up use
# them); fxmath.h's helpers in one copy (the flanger's) for spring.c too. Key reach: every function reached.
# Key dmda_ddr: the constant data of spring.c and the glue (168 B) in DDR, in the reverb's region after SpgSlot, as one
# load block (the stock stream loads seven data blocks into DDR): in L1's data region, brickcheck's shift scan, which
# decodes every changed load block in an executable range as code, read spring.c's SP_AP table as a mis-encoded shift
# (a false positive on data). L1's data region keeps DT01's bytes.
RECIPES["SP01"] = dict(
    RECIPES["DT01"],
    what="SP01: DT01 with SPNG, the spring reverb (fx/spring.c, spng_glue.c), in the stock reverb slot: its code in "
         "code2 after flg_sync.c, its state in the stock reverb's DDR region (0x80000018), the stock reverb and its "
         "init no longer run",
    c2_tail=["tools/dspsplice/src/sp01/spring.c", "tools/dspsplice/src/spng_glue.c"],
    inc=["tools/dspsplice/src/sp01"] + RECIPES["DT01"]["inc"],
    dmda_ddr=(0x8000b000, 0x8000c000),
    stubs2=[("fxh_reverb", [("c", "spg_process", ["R8", "R12"])]),
            ("fxh_reverb_init", [("c", "spg_init", [])])],
    hooks=RECIPES["DT01"]["hooks"] + [(REVERB_CALL, REVERB, "fxh_reverb"),
                                      (REVERB_INIT_CALL, REVERB_INIT, "fxh_reverb_init")],
    h=RECIPES["DT01"]["h"] + ["tools/dspsplice/src/sp01/spring.h"],
    drop=RECIPES["DT01"]["drop"] + ["spring_pre_ms", "spring_rt60_s", "spring_dwel_db", "spring_hpf_hz",
                                    "spring_tone_hz", "spring_width", "spring_tens_ms"],
    ddr_slot=("spg_slot_bytes", 0x80000018, 0x59114, "SpgSlot (the spring and its glue) in the stock reverb's DDR "
                                                      "state, 0x80000018-0x8005912c"),
    outdir="out/SP01/SP01-dsp", syx="SP01-dsp.syx", version="SP1D")

# SP02 (2026-10-26, notes/SP02-spring-pack.md): DC02 with SPNG v2 (fx/spring.c: the new ratios, the cross-feed, the
# wobble, Catmull-Rom reads, DIRT on the wet output; notes/T5.5 9), placed as SP01 placed v1 on DT01: its code and
# the glue in code2 after flg_sync.c, the reverb's two stubs, the constants in DDR after SpgSlot, SpgSlot at the start
# of the stock reverb's DDR state. DC02's peephole runs over the spring's code too. The diet as SP01's, with
# spring_dirt_db in place of spring_dwel_db.
RECIPES["SP02"] = dict(
    RECIPES["DC02"],
    what="SP02: DC02 with SPNG v2 (fx/spring.c, spng_glue.c) in the stock reverb slot, placed as SP01: its code in "
         "code2 after flg_sync.c, its state in the stock reverb's DDR region (0x80000018), the stock reverb and its "
         "init no longer run",
    c2_tail=["fx/spring.c", "tools/dspsplice/src/spng_glue.c"],
    dmda_ddr=(0x8000b000, 0x8000c000),
    stubs2=[("fxh_reverb", [("c", "spg_process", ["R8", "R12"])]),
            ("fxh_reverb_init", [("c", "spg_init", [])])],
    hooks=RECIPES["DC02"]["hooks"] + [(REVERB_CALL, REVERB, "fxh_reverb"),
                                      (REVERB_INIT_CALL, REVERB_INIT, "fxh_reverb_init")],
    h=RECIPES["DC02"]["h"] + ["fx/spring.h"],
    drop=RECIPES["DC02"]["drop"] + ["spring_pre_ms", "spring_rt60_s", "spring_dirt_db", "spring_hpf_hz",
                                    "spring_tone_hz", "spring_width", "spring_tens_ms"],
    ddr_slot=("spg_slot_bytes", 0x80000018, 0x59114, "SpgSlot (the spring and its glue) in the stock reverb's DDR "
                                                      "state, 0x80000018-0x8005912c"),
    outdir="out/SP02/SP02-dsp", syx="SP02-dsp.syx", version="SP2D")

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
# CMP3: a fourth code region (the compressor glue, in L1 block 1's free tail)
LDF_MEM4 = """   mem_t38_code4 {{ TYPE(BW RAM) START(0x{g0:08x}) END(0x{g1:08x}) WIDTH(8) }}
"""
LDF_SEC4 = """      t38_code4 SW
      {{
         INPUT_SECTION_ALIGN(2)
         FILL(0x1)
         INPUT_SECTIONS( $OBJECTS(seg_t38c4) )
      }} > mem_t38_code4
"""
# SC01: code regions in L2, over stock code that is dead in the build (recipe key l2: name -> (sw lo, sw hi)); the LDF
# takes byte addresses for SW sections (START = 2 x SW), and build() patches the stock L2 payload block in place
LDF_MEML2 = """   mem_t38_{n} {{ TYPE(BW RAM) START(0x{g0:08x}) END(0x{g1:08x}) WIDTH(8) }}
"""
LDF_SECL2 = """      t38_{n} SW
      {{
         INPUT_SECTION_ALIGN(2)
         FILL(0x1)
         INPUT_SECTIONS( $OBJECTS(seg_t38{n}) )
      }} > mem_t38_{n}
"""
# SP01: constant data in DDR (recipe key dmda_ddr: byte addresses)
LDF_MEMDD = """   mem_t38_ddr {{ TYPE(BW RAM) START(0x{g0:08x}) END(0x{g1:08x}) WIDTH(8) }}
"""
LDF_SECDD = """      t38_ddata BW
      {{
         INPUT_SECTION_ALIGN(4)
         INPUT_SECTIONS( $OBJECTS(seg_t38dd) )
      }} > mem_t38_ddr
"""
SEG = {"c2": "seg_t38c2", "c3": "seg_t38c3", "c4": "seg_t38c4"}


def all_c(rec):
    """every C source of a recipe, in link order (SC01: the split files last; SP01: c2_tail, code2 sources linked after
    every other object, so their code follows c2's and their constant data follows everyone else's)"""
    return (rec["c"] + rec.get("c2", []) + rec.get("c3", []) + rec.get("c4", []) + rec.get("split", [])
            + rec.get("c2_tail", []))


def dmda_ddr_of(rec, src, text):
    """SP01: a c2_tail source's constant data (selcc's seg_dmda) renamed to seg_t38dd, which the LDF puts in the
    recipe's dmda_ddr range (DDR): brickcheck's shift scan decodes every load block in an executable L1 range as code,
    and spring.c's tables decode as a mis-encoded shift there (a false positive on data); a DDR block is not decoded"""
    if rec.get("dmda_ddr") and src in rec.get("c2_tail", []):
        return text.replace(".SECTION/DOUBLE32 seg_dmda;", ".SECTION/DOUBLE32 seg_t38dd;")
    return text


def seg_of(rec, src):
    """the code section a recipe's C source is renamed into (None: L1 block 1's seg_swco as selcc names it)"""
    if src in rec.get("c2", []) or src in rec.get("c2_tail", []):
        return "seg_t38c2"
    if src in rec.get("c3", []):
        return "seg_t38c3"
    if src in rec.get("c4", []):
        return "seg_t38c4"
    return None


FUNC_LABEL = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\.:\s*$")


def split_asm(text, stem, place, default):
    """SC01: selcc's output for one C file with each function in the section of its region.
    text: '.EXTERN' lines, '.SECTION/SW seg_swco;', the functions (each from its label 'name.:', with the '.GLOBAL
    name.;' line before it when global), '.ENDSEG;', then any data sections. place: function -> region ('c2', 'c3',
    'c4', an l2 name, or 'drop': left out, which the link refuses if anything still calls it; a key 'stem:name' wins over
    'name', for the static helpers each file has its own copy of); default: the region of
    every other function. Each function also gets a marker label 'scfn_<stem>_<name>.' (global, no code), so the
    linked image records where every function went. -> (new text, {function: region})"""
    lines = text.split("\n")
    i0 = next(i for i, ln in enumerate(lines) if ln.startswith(".SECTION/"))
    if lines[i0] != ".SECTION/SW seg_swco;" or any(ln.strip() and not ln.startswith(".EXTERN") for ln in lines[:i0]):
        raise SystemExit(f"{stem}.s: unexpected start")
    i1 = lines.index(".ENDSEG;", i0)
    rest = lines[i1 + 1:]
    if any(ln.startswith(".SECTION/SW") for ln in rest):
        raise SystemExit(f"{stem}.s: a second code section")
    chunks, cur = [], None
    for ln in lines[i0 + 1:i1]:
        m = FUNC_LABEL.match(ln)
        if m:
            pend = []
            if cur is not None and cur[1] and cur[1][-1] == f".GLOBAL {m.group(1)}.;":
                pend = [cur[1].pop()]
            cur = (m.group(1), pend + [ln])
            chunks.append(cur)
        elif cur is None:
            if ln.strip() and not ln.startswith(".GLOBAL"):
                raise SystemExit(f"{stem}.s: code before the first function: {ln}")
            cur = ("", [ln])
            chunks.append(cur)
        else:
            cur[1].append(ln)
    out, where = lines[:i0], {}
    for name, body in chunks:
        if not name:
            if any(x.strip() for x in body):
                raise SystemExit(f"{stem}.s: lines before the first function label: {body}")
            continue
        reg = place.get(f"{stem}:{name}", place.get(name, default))
        where[name] = reg
        if reg == "drop":
            continue
        seg = SEG.get(reg, f"seg_t38{reg}")
        out += [f".SECTION/SW {seg};", f".GLOBAL scfn_{stem}_{name}.;", f"scfn_{stem}_{name}.:"] + body + [".ENDSEG;"]
    return "\n".join(out + rest), where


def asm_chunks(text, stem):
    """DT01: a selcc .s file (one code section) as (head, [[name, lines]], tail): head = the lines up to and with the
    '.SECTION/SW ...;' line, one chunk per function from its label (with the '.GLOBAL name.;' line before it when it
    is global) to the next function, tail = the code section's '.ENDSEG;' and everything after it"""
    lines = text.split("\n")
    i0 = next(i for i, ln in enumerate(lines) if ln.startswith(".SECTION/SW"))
    i1 = lines.index(".ENDSEG;", i0)
    if any(ln.startswith(".SECTION/SW") for ln in lines[i1 + 1:]):
        raise SystemExit(f"{stem}.s: a second code section")
    chunks, cur = [], None
    for ln in lines[i0 + 1:i1]:
        m = FUNC_LABEL.match(ln)
        if m:
            pend = []
            if cur is not None and cur[1] and cur[1][-1] == f".GLOBAL {m.group(1)}.;":
                pend = [cur[1].pop()]
            cur = [m.group(1), pend + [ln]]
            chunks.append(cur)
        elif cur is None:
            if ln.strip() and not ln.startswith(".GLOBAL"):
                raise SystemExit(f"{stem}.s: code before the first function: {ln}")
            if ln.strip():
                cur = ["", [ln]]
                chunks.append(cur)
        else:
            cur[1].append(ln)
    chunks = [c for c in chunks if c[0] or any(x.strip() for x in c[1])]
    if any(not n for n, _ in chunks):
        raise SystemExit(f"{stem}.s: a line before the first function label")
    return lines[:i0 + 1], chunks, lines[i1:]


def diet_asm(texts, rec):
    """DT01 (notes/DT01-code-diet.md): the code diet on selcc's output, before assembly; the C sources stay as they are.
    texts: {stem: .s text}. Recipe keys:
      drop:  functions the DSP never calls, left out ('name' or 'stem:name'); the link refuses one something still calls;
      share: {"from": stem, "names": [...]}: fxmath.h's static helpers in one copy (selcc inlines nothing, so every file
             that uses one has its own copy): the provider's copies made .GLOBAL; in every other file a copy is removed
             and declared .EXTERN, so its calls go to the provider's. Each removed copy must be the provider's text line
             for line (the same code: the same results and the same instruction count), or the build stops.
    -> (texts, report)"""
    sh = rec.get("share") or {}
    names, prov = sh.get("names", []), sh.get("from")
    drop = set(rec.get("drop", []))
    parsed = {stem: asm_chunks(t, stem) for stem, t in texts.items()}
    if names and prov not in parsed:
        raise SystemExit(f"share: the provider {prov} is not in the recipe")
    pbody = {}
    if names:
        for n, body in parsed[prov][1]:
            if n in names:
                if body[0].startswith(".GLOBAL"):
                    raise SystemExit(f"share: {prov}'s {n} is already global")
                pbody[n] = body
        missing = [n for n in names if n not in pbody]
        if missing:
            raise SystemExit(f"share: {prov} has no copy of {missing}")
    rep = {"dropped": [], "shared_from": prov, "shared": [], "removed_copies": []}
    out = {}
    for stem, (head, chunks, tail) in parsed.items():
        keep, ext = [], []
        for n, body in chunks:
            if n in drop or f"{stem}:{n}" in drop:
                rep["dropped"].append(f"{stem}:{n}")
                continue
            if n in pbody and stem == prov:
                body = [f".GLOBAL {n}.;"] + body
                rep["shared"].append(n)
            elif n in pbody:
                if body != pbody[n]:
                    raise SystemExit(f"share: {stem}'s {n} is not {prov}'s text line for line")
                rep["removed_copies"].append(f"{stem}:{n}")
                ext.append(f".EXTERN {n}.;")
                continue
            keep.append(body)
        out[stem] = "\n".join(ext + head + [ln for b in keep for ln in b] + tail)
    gone = sorted(set(drop) - {d.split(":")[-1] for d in rep["dropped"]} - {d for d in rep["dropped"]})
    if gone:
        raise SystemExit(f"drop: no function {gone} in the recipe's files")
    return out, rep


def reach_check(bdir, stems, entries):
    """DT01: the static call graph of our code (every call is a CJUMP to a function label, as selcc and stub.py emit
    them): every function reachable from the hook stubs' entries. -> (rows, problems)"""
    fns, calls = {}, {}
    for stem in stems:
        cur = None
        for ln in (bdir / f"{stem}.s").read_text().split("\n"):
            m = FUNC_LABEL.match(ln)
            if m:
                cur = m.group(1)
                fns.setdefault(cur, stem)
                calls.setdefault(cur, set())
                continue
            for t in re.findall(r"\b(?:CJUMP|CALL|JUMP)\s+([A-Za-z_][A-Za-z0-9_]*)\.", ln):
                if cur:
                    calls[cur].add(t)
    seen, todo = set(), [e for e in entries if e in fns]
    while todo:
        f = todo.pop()
        if f in seen:
            continue
        seen.add(f)
        todo += [t for t in calls.get(f, ()) if t in fns]
    rows = [{"function": f, "file": s, "reached": f in seen} for f, s in fns.items()]
    return rows, [f"{r['file']}:{r['function']} is never reached from the hooks" for r in rows if not r["reached"]]


def compile_link(name, rec, bdir, log):
    bdir.mkdir(parents=True, exist_ok=True)
    flags = ["-proc", "ADSP-21569", "-char-size-8", "-O1"] + [f"-I{ROOT / i}" for i in rec["inc"]]
    objs = []
    rec["_placed_funcs"] = {}
    if rec.get("peephole") and not (rec.get("drop") or rec.get("share")):
        raise SystemExit("the key peephole runs only in the diet path (a recipe with drop or share)")
    if rec.get("drop") or rec.get("share"):
        # DT01: every file compiled (and its section renamed) first, the diet applied over all of them, then assembled
        texts = {}
        for src in all_c(rec):
            stem = Path(src).stem
            if src in rec.get("split", []):
                raise SystemExit("a diet recipe has no split files")
            run([SEL / "selcc", *flags, "-S", "-o", bdir / f"{stem}.s", ROOT / src], log)
            seg = seg_of(rec, src)
            s = (bdir / f"{stem}.s").read_text()
            (bdir / f"{stem}.sel.s").write_text(s)          # selcc's own output, kept for review
            if s.count(".SECTION/SW seg_swco;") != 1 or ".SECTION/" in s.replace(".SECTION/SW seg_swco;", "").replace(
                    ".SECTION/DOUBLE32 seg_dmda;", ""):
                raise SystemExit(f"{stem}.s: unexpected sections")
            texts[stem] = dmda_ddr_of(rec, src, s.replace(".SECTION/SW seg_swco;", f".SECTION/SW {seg};") if seg else s)
        texts, rec["_diet"] = diet_asm(texts, rec)
        if rec.get("peephole"):
            # DC01: the peephole pass (tools/dspsplice/peephole.py) over every file after the diet; its report in
            # build/peephole.json
            import peephole
            texts, rec["_peephole"] = peephole.run(texts, patterns=rec["peephole"], entries=peephole.stub_entries(
                rec["stubs"] + rec.get("stubs2", []) + rec.get("stubs3", []), stub.SAVE_REGS))
            (bdir / "peephole.json").write_text(json.dumps(rec["_peephole"], indent=1))
        for src in all_c(rec):
            stem = Path(src).stem
            (bdir / f"{stem}.s").write_text(texts[stem])
            run([SEL / "selas", "-proc", "ADSP-21569", "-o", bdir / f"{stem}.doj", bdir / f"{stem}.s"], log)
            objs.append(bdir / f"{stem}.doj")
    for src in ([] if rec.get("drop") or rec.get("share") else all_c(rec)):
        stem = Path(src).stem
        run([SEL / "selcc", *flags, "-S", "-o", bdir / f"{stem}.s", ROOT / src], log)
        seg = seg_of(rec, src)
        if src in rec.get("split", []):
            # SC01: each function in the section of its region (recipe keys place, split_default)
            s, where = split_asm((bdir / f"{stem}.s").read_text(), stem, rec.get("place", {}),
                                 rec.get("split_default", "c2"))
            (bdir / f"{stem}.s").write_text(s)
            rec["_placed_funcs"][stem] = where
        elif seg:
            # the same code, only its section renamed so the LDF puts it in the second (third) code region
            s = (bdir / f"{stem}.s").read_text()
            if s.count(".SECTION/SW seg_swco;") < 1 or ".SECTION/" in s.replace(".SECTION/SW seg_swco;", "").replace(
                    ".SECTION/DOUBLE32 seg_dmda;", ""):
                raise SystemExit(f"{stem}.s: unexpected sections")
            (bdir / f"{stem}.s").write_text(dmda_ddr_of(rec, src, s.replace(".SECTION/SW seg_swco;", f".SECTION/SW {seg};")))
        run([SEL / "selas", "-proc", "ADSP-21569", "-o", bdir / f"{stem}.doj", bdir / f"{stem}.s"], log)
        objs.append(bdir / f"{stem}.doj")
    # CMP2D: hand-written assembly (each file names its own section), after the C objects
    for src in rec.get("asm2", []):
        stem = Path(src).stem
        run([SEL / "selas", "-proc", "ADSP-21569", "-o", bdir / f"{stem}.doj", ROOT / src], log)
        objs.append(bdir / f"{stem}.doj")
    (bdir / "stubs.s").write_text(stub.module(rec["stubs"], SAVE))
    run([SEL / "selas", "-proc", "ADSP-21569", "-o", bdir / "stubs.doj", bdir / "stubs.s"], log)
    objs.insert(0, bdir / "stubs.doj")
    if rec.get("stubs3"):
        (bdir / "stubs3.s").write_text(stub.module(rec["stubs3"], SAVE, section=rec.get("stubs3_seg", "seg_t38c3")))
        run([SEL / "selas", "-proc", "ADSP-21569", "-o", bdir / "stubs3.doj", bdir / "stubs3.s"], log)
        objs.append(bdir / "stubs3.doj")
    if rec.get("stubs2"):
        # SP01: hook stubs in code2, after every C object's code2 (so after c2_tail's)
        (bdir / "stubs2.s").write_text(stub.module(rec["stubs2"], SAVE, section="seg_t38c2"))
        run([SEL / "selas", "-proc", "ADSP-21569", "-o", bdir / "stubs2.doj", bdir / "stubs2.s"], log)
        objs.append(bdir / "stubs2.doj")
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
    if "code4" in rec:
        g0, g1 = rec["code4"]
        anchor = "   mem_t38_code2 {"
        i = ldf.index(anchor)
        i = ldf.index("\n", i) + 1
        ldf = ldf[:i] + LDF_MEM4.format(g0=g0, g1=g1 - 1) + ldf[i:]
        anchor = "      }} > mem_t38_code2\n".replace("}}", "}")
        i = ldf.index(anchor) + len(anchor)
        ldf = ldf[:i] + LDF_SEC4.format() + ldf[i:]
    if rec.get("dmda_ddr"):
        g0, g1 = rec["dmda_ddr"]
        anchor = "   mem_t38_code2 {"
        i = ldf.index(anchor)
        i = ldf.index("\n", i) + 1
        ldf = ldf[:i] + LDF_MEMDD.format(g0=g0, g1=g1 - 1) + ldf[i:]
        anchor = "      }} > mem_t38_code2\n".replace("}}", "}")
        i = ldf.index(anchor) + len(anchor)
        ldf = ldf[:i] + LDF_SECDD.format() + ldf[i:]
    for n, (lo, hi) in rec.get("l2", {}).items():
        anchor = "   mem_t38_code2 {"
        i = ldf.index(anchor)
        i = ldf.index("\n", i) + 1
        ldf = ldf[:i] + LDF_MEML2.format(n=n, g0=2 * lo, g1=2 * hi - 1) + ldf[i:]
        anchor = "      }} > mem_t38_code2\n".replace("}}", "}")
        i = ldf.index(anchor) + len(anchor)
        ldf = ldf[:i] + LDF_SECL2.format(n=n) + ldf[i:]
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
    for p in ([bdir / f"{Path(s).stem}.s" for s in all_c(rec)] + [bdir / "stubs.s"]
              + ([bdir / "stubs3.s"] if rec.get("stubs3") else []) + ([bdir / "stubs2.s"] if rec.get("stubs2") else [])
              + [ROOT / a for a in rec.get("asm2", [])]):
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
    slot_of = {"t38_code": code, "t38_code2": code2, "t38_code3": code3, "t38_code4": rec.get("code4"),
               "t38_ddata": rec.get("dmda_ddr")}
    for n, (lo, hi) in rec.get("l2", {}).items():
        slot_of["t38_" + n] = (E.sw_to_byte(lo), E.sw_to_byte(hi))
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
        for lo, hi, name in FORBIDDEN + rec.get("forbid", []):
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


# ---- CMP2F: the memory scanner's page table and watermarks (src/cmp2f/cmp_load.c reads them) ----
SCAN_MAX_PAGES = 50


def scan_field(v):
    """a 32-bit value as two words whose four 16-bit halves are 0xC000 | 13 bits (13 + 13 + 6 bits used): every half
    is a 16-bit short-compute parcel to a linear decoder (tools/brickcheck's shift scan decodes payload blocks)"""
    w0 = (0xC000 | (v & 0x1FFF)) | ((0xC000 | ((v >> 13) & 0x1FFF)) << 16)
    w1 = (0xC000 | ((v >> 26) & 0x3F)) | (0xC000 << 16)
    return struct.pack("<II", w0, w1)


def mark_word(a):
    """the watermark of the word at byte address a (cmp_load.h CL_MARK_K1/K2); both halves short-compute parcels"""
    x = a >> 2
    return 0xC000C000 | ((x ^ 0x0A5C) & 0x1FFF) | (((x ^ 0x15A3) & 0x1FFF) << 16)


def fletcher(data):
    a = b = 0
    for (w,) in struct.iter_unpack("<I", data):
        a = (a + w) & 0xFFFFFFFF
        b = (b + a) & 0xFFFFFFFF
    return a, b


def scan_layout(rec, e, slot_sizes):
    """-> (pages [(lo, hi, kind, fa, fb, what)], payload blocks [(lo, bytes, why, slot)]): the watermark ranges in
    pages (in the recipe's order: the page number is the index + 1), then the code pages with their Fletcher-32 over the
    linked bytes, and the page table at scan["table"]"""
    sc = rec["scan"]
    secs = {s.name: (addr, n, s) for addr, n, s in ranges_of(e)}

    def lo_of(lo):
        if isinstance(lo, tuple) and lo[0] == "after":
            addr, n, _ = secs[lo[1]]
            return addr + n
        if isinstance(lo, tuple) and lo[0] == "slot_end":
            return lo[2] + slot_sizes[lo[1]]
        return lo

    def split(lo, hi, pg, why):
        out, a = [], lo
        while a < hi:
            b = min(hi, (a // pg + 1) * pg) if pg else hi
            out.append((a, b, 1, 0, 0, why))
            a = b
        return out
    code_pages = []
    for name, pg in sc["code"]:
        addr, n, s = secs[name]
        data = bytes(s.data)
        lo, hi = (addr + 3) // 4 * 4, (addr + n) // 4 * 4
        a = lo
        while a < hi:
            b = min(hi, a + pg)
            fa, fb = fletcher(data[a - addr:b - addr])
            code_pages.append((a, b, 2, fa, fb, "%s %#x-%#x" % (name, a, b)))
            a = b
    # the watermark ranges; "table_end" (one page) once the page count is known
    ranges = []
    for lo, hi, pg, why in sc["marks"]:
        if lo != "table_end":
            lo = (lo_of(lo) + 3) // 4 * 4
        ranges.append([lo, hi // 4 * 4, pg, why])
    n_pages = len(code_pages) + sum(len(split(lo, hi, pg, why)) if lo != "table_end" else 1
                                    for lo, hi, pg, why in ranges if lo == "table_end" or hi - lo >= 16)
    pages, marks = [], []
    for lo, hi, pg, why in ranges:
        if lo == "table_end":
            lo = sc["table"] + 8 + 40 * n_pages
        if hi - lo < 16:
            continue
        marks.append((lo, hi, why))
        pages += split(lo, hi, pg, why)
    pages += code_pages
    if len(pages) != n_pages:
        raise SystemExit(f"scan: {len(pages)} pages, {n_pages} expected")
    if len(pages) > SCAN_MAX_PAGES:
        raise SystemExit(f"scan: {len(pages)} pages, at most {SCAN_MAX_PAGES}")
    table = scan_field(len(pages)) + b"".join(scan_field(lo) + scan_field(hi) + scan_field(k) + scan_field(fa) + scan_field(fb)
                                              for lo, hi, k, fa, fb, _ in pages)
    t0 = sc["table"]
    if t0 + len(table) > sc["table_slot"][1]:
        raise SystemExit("scan: the page table does not fit its slot")
    blocks = [(lo, b"".join(struct.pack("<I", mark_word(a)) for a in range(lo, hi, 4)), "watermark: " + why, (lo, hi))
              for lo, hi, why in marks]
    blocks.append((t0, table, "scanner page table (%d pages)" % len(pages), (t0, t0 + len(table))))
    return pages, blocks


def placement(rec, e):
    """SC01: every function of the split files as linked: region, SW address, bytes (from the marker labels)"""
    marks = sorted((v, k[5:-1]) for k, v in e.symbols.items() if k.startswith("scfn_") and k.endswith("."))
    ends = {}
    for s in e.sections:
        if s.kind == "sw_code":
            ends[s.name] = (s.addr, s.addr + len(s.data) // 2)
    out = []
    for i, (a, key) in enumerate(marks):
        sec = next(n for n, (lo, hi) in ends.items() if lo <= a < hi)
        nxt = [b for b, _ in marks[i + 1:] if ends[sec][0] <= b < ends[sec][1]]
        hi = nxt[0] if nxt else ends[sec][1]
        stem = next(st for st in rec["_placed_funcs"] if key.startswith(st + "_") and key[len(st) + 1:] in rec["_placed_funcs"][st])
        fn = key[len(stem) + 1:]
        out.append({"function": fn, "file": stem, "region": rec["_placed_funcs"][stem][fn], "section": sec,
                    "sw": "%#x" % a, "core": "%#x" % E.sw_to_byte(a), "bytes": 2 * (hi - a)})
    out += [{"function": fn, "file": st, "region": "drop", "section": None, "sw": None, "core": None, "bytes": 0}
            for st, w in rec["_placed_funcs"].items() for fn, r in w.items() if r == "drop"]
    return out


def placement_check(rec, e):
    """SC01: the per-sample loops (and every function the recipe names l1) in L1 block 2; in code4 nothing of the split
    files below the end of the code-proven part, and above it only the functions the recipe allows (boot-time init);
    the non-split code4 objects (the compressor glue, cmp_tail.c) inside the code-proven part"""
    rows, problems = placement(rec, e), []
    by = {r["function"] + "@" + r["file"]: r for r in rows}
    for f in rec.get("l1", []):
        hit = [r for r in rows if r["function"] == f]
        if not hit or any(r["region"] != rec.get("split_default", "c2") for r in hit):
            problems.append(f"{f} is not in L1 block 2 ({[r['region'] for r in hit]})")
    proven = rec.get("code4_proven")
    if proven:
        c4lo = E.sw_to_byte([x for x in e.sections if x.name == "t38_code4"][0].addr)
        c4 = [r for r in rows if r["region"] == "c4"]
        for r in c4:
            if r["function"] not in rec.get("level3", []):
                problems.append(f"{r['function']} in code4 but not in the recipe's level-3 list (boot-time init only)")
            r["above_code_proven"] = int(r["core"], 16) + r["bytes"] > proven
        if c4 and min(int(r["core"], 16) for r in c4) <= c4lo:
            problems.append("code4 starts with a split function, not the compressor glue")
    return rows, problems


def proven_check(rec, e):
    """CMP4: every code section of ours (bytes > 0) inside one range of the recipe's proven list (where our code ran on
    the unit), no L2 region, and with code2_unused no code2 bytes at all. -> (rows, problems)"""
    rows, problems = [], []
    if rec.get("l2"):
        problems.append("the recipe places code in L2 (not proven)")
    for addr, n, s in ranges_of(e):
        if s.kind != "sw_code" or not n:
            continue
        hit = [w for lo, hi, w in rec["proven"] if lo <= addr and addr + n <= hi]
        rows.append({"section": s.name, "core": "%#x-%#x" % (addr, addr + n), "bytes": n, "proven": hit[0] if hit else None})
        if not hit:
            problems.append(f"{s.name} {addr:#x}-{addr + n:#x} is not inside a range where our code ran on the unit")
        if rec.get("code2_unused") and s.name == "t38_code2":
            problems.append(f"t38_code2 has {n} bytes, but the recipe leaves code2 unused")
    return rows, problems


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
    for src in all_c(rec):
        census += register_census((bdir / f"{Path(src).stem}.s").read_text())
    extra = sorted(set(census) - CENSUS_ALLOWED)
    if extra:
        raise SystemExit(f"our C code writes registers the hook stubs do not preserve: {extra}")
    sym = {k[:-1]: v for k, v in e.symbols.items() if k.endswith(".") and not k.startswith("_")}

    # where everything landed
    placed = []
    for addr, n, s in ranges_of(e):
        lo, hi = {"t38_code": rec.get("code", CODE), "t38_code2": code2,
                  "t38_code3": rec.get("code3", (0, 0)), "t38_code4": rec.get("code4", (0, 0)),
                  "t38_ddata": rec.get("dmda_ddr", (0, 0)),
                  **{"t38_" + n: (E.sw_to_byte(a), E.sw_to_byte(b)) for n, (a, b) in rec.get("l2", {}).items()}
                  }.get(s.name, DATA)
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
    l2_patched = []
    for addr, n, s in ranges_of(e):
        if s.kind == "bss":
            st.add_fill(addr, (n + 3) // 4 * 4, why=f"{s.name} (zero-initialised)")
        elif s.name[4:] in rec.get("l2", {}):
            # SC01: over dead stock code in L2, in the stock L2 code block itself (no new block)
            old = st.read(addr, n)
            st.patch(addr, old, bytes(s.data), why=f"{s.name}: our code over dead stock code (level 2)")
            l2_patched.append({"section": s.name, "core": "%#x" % addr, "sw": "%#x" % s.addr, "bytes": n,
                               "stock_sha256": sha(old)})
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
    scan_pages, scan_blocks = None, []
    if rec.get("scan"):
        scan_pages, scan_blocks = scan_layout(rec, e, slot_sizes)
        for lo, data, why, slot in scan_blocks:
            st.add_payload(lo, data, why=why)
            fills.append((lo, len(data), why, slot))
    layout, problems = layout_check(rec, e, fills)
    fn_rows = None
    if rec.get("split"):
        fn_rows, pp = placement_check(rec, e)
        problems += pp
    proven_rows = None
    if rec.get("proven"):
        proven_rows, pp = proven_check(rec, e)
        problems += pp
    reach_rows = None
    if rec.get("reach"):
        # DT01: no function of ours that nothing calls (from the hook stubs) is linked
        reach_rows, pp = reach_check(bdir, [Path(s).stem for s in all_c(rec)] + ["stubs"]
                                     + (["stubs3"] if rec.get("stubs3") else [])
                                     + (["stubs2"] if rec.get("stubs2") else []),
                                     [n for n, _ in rec["stubs"] + rec.get("stubs3", []) + rec.get("stubs2", [])])
        problems += pp
    ddr_slot = None
    if rec.get("ddr_slot"):
        # SP01: the effect's state in DDR, its size as the SHARC build computed it (a data symbol), inside its region
        dname, dlo, dsize, dwhy = rec["ddr_slot"]
        dn = symbol_word(e, sym, dname)
        ddr_slot = {"what": dwhy, "core": "%#x-%#x" % (dlo, dlo + dn), "bytes": dn, "region_bytes": dsize,
                    "left": dsize - dn}
        if dn > dsize:
            problems.append(f"{dwhy}: {dn} bytes, more than the region's {dsize}")
        if rec.get("dmda_ddr"):
            ddr_slot["constant_data"] = "%#x-%#x" % tuple(rec["dmda_ddr"])
            if not (dlo + dn <= rec["dmda_ddr"][0] and rec["dmda_ddr"][1] <= dlo + dsize):
                problems.append(f"{dwhy}: the constant data {rec['dmda_ddr']} is not after the slot inside the region")
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
    if rec.get("dmda_ddr"):
        # SP01: brickcheck's scan for mis-encoded shifts over every changed load block in an executable range (it
        # decodes data blocks as code too); run here so a layout it would flag stops the build
        bads = bc_dsp.scan_bad_shifts(bc_dsp.final_image(stream)[0], bc_dsp.final_image(stock7)[0],
                                      bc_dsp.final_image(stream)[1])
        if bads:
            raise SystemExit(f"brickcheck's shift scan flags {bads}")
    diffs = diff_images(final_image(stock7), final_image(stream))
    allowed = [(ldr.loader_addr(2 * site), ldr.loader_addr(2 * site) + 6) for site, _, _ in rec["hooks"]]
    allowed += [(ldr.loader_addr(CODE[0]), ldr.loader_addr(L1B1_LIMIT)),
                (ldr.loader_addr(state[0]), ldr.loader_addr(state[1]))]
    if not rec.get("code2_unused"):
        allowed.append((ldr.loader_addr(code2[0]), ldr.loader_addr(code2[1])))
    if "code3" in rec:
        allowed.append((ldr.loader_addr(rec["code3"][0]), ldr.loader_addr(rec["code3"][1])))
    for n, (lo, hi) in rec.get("l2", {}).items():
        allowed.append((E.sw_to_byte(lo), E.sw_to_byte(hi)))
    if rec.get("dmda_ddr"):
        allowed.append(tuple(rec["dmda_ddr"]))
    for lo, data, why, slot in scan_blocks:
        allowed.append((ldr.loader_addr(lo), ldr.loader_addr(lo + len(data))))
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
    # CMP3: no byte of the final image that differs from stock (code, data, FILL) in a forbidden range of the recipe
    hit = [(a, b, w) for a, b in diffs for lo, hi, w in rec.get("forbid", [])
           if a < ldr.loader_addr(hi) and ldr.loader_addr(lo) < b]
    if hit:
        raise SystemExit("final image differs from stock inside a forbidden range: %s" % [(hex(a), hex(b), w) for a, b, w in hit])
    # CMP4: the hooks the recipe leaves to the stock routines are the stock bytes in the final image
    stock_hooks = []
    for site, target, what in rec.get("stock_hooks", []):
        lo = ldr.loader_addr(2 * site)
        touched = [(a, b) for a, b in diffs if a < lo + 6 and lo < b]
        if touched or any(p.get("why", "").startswith(f"CJUMP at SW {site:#x}") for p in st.patches):
            raise SystemExit(f"the stock CJUMP at SW {site:#x} ({what}) is not the stock bytes")
        stock_hooks.append({"site": "%#x" % site, "target": "%#x" % target, "what": what, "stock_bytes_kept": True})

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
                    all_c(rec) + [f"tools/dspsplice/src/{h}" for h in ("fxio.h",)]
                    + ["fx/fxmath.h"] + rec.get("asm2", []) + rec.get("h", []) + [str(Path(src).with_suffix(".h")) for src in all_c(rec)
                                         if (ROOT / Path(src).with_suffix(".h")).exists()]},
        "layout_check": {"rows": layout, "problems": problems, "forbidden": [("%#x" % a, "%#x" % b, w) for a, b, w in FORBIDDEN + rec.get("forbid", [])],
                         "slot_sizes_from_image": slot_sizes},
        "patches": st.patches, "added_blocks": st.added,
        "scan_pages": [{"page": i + 1, "lo": "%#x" % lo, "hi": "%#x" % hi, "bytes": hi - lo,
                        "kind": "watermark" if k == 1 else "code", "fletcher": ["%#010x" % fa, "%#010x" % fb] if k == 2 else None,
                        "what": what} for i, (lo, hi, k, fa, fb, what) in enumerate(scan_pages)] if scan_pages else None,
        "image_diff_ranges": [("%#x" % a, "%#x" % b) for a, b in diffs],
        "l2_patched": l2_patched or None,
        "functions": fn_rows,
        "proven_check": proven_rows, "stock_hooks": stock_hooks or None, "ddr_slot": ddr_slot,
        "shift_immediates_in_our_asm": selcc_shift_imm_count(bdir, rec),
        "register_census": dict(sorted(census.items())),
        "save_area": {"core": "%#x" % SAVE, "bytes": stub.SAVE_BYTES, "regs": stub.SAVE_REGS},
        "log": log,
    }
    if rec.get("drop") or rec.get("share"):
        # DT01: what the diet did, the reachability, and every function's size per code region
        info["diet"] = rec["_diet"]
        info["reach"] = reach_rows
        info["function_table"] = function_table(rec, bdir, e)
    (odir / "build.json").write_text(json.dumps(info, indent=1))
    return info


def function_table(rec, bdir, e):
    """DT01: every function of ours as linked, per code section: (section, function, file, core address, bytes), from
    sc01_funcmap (each region's .s files re-assembled with every label global and linked alone at the section's
    address, in the link's order, calls out of the region to stand-ins); each region's size checked against the build's"""
    import importlib.util
    spec = importlib.util.spec_from_file_location("sc01_funcmap", str(HERE / "sc01_funcmap.py"))
    fm = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fm)
    stem = lambda xs: [bdir / f"{Path(s).stem}.s" for s in xs]   # noqa: E731
    order = {"t38_code": [bdir / "stubs.s"] + stem(rec["c"]),
             "t38_code2": stem(rec.get("c2", [])) + stem(rec.get("c2_tail", []))
             + ([bdir / "stubs2.s"] if rec.get("stubs2") else []),
             "t38_code3": stem(rec.get("c3", [])) + ([bdir / "stubs3.s"] if rec.get("stubs3") else []),
             "t38_code4": stem(rec.get("c4", []))}
    rows = []
    for s in e.sections:
        if s.kind != "sw_code" or not s.data:
            continue
        got, sec = fm.funcmap(bdir / f"funcmap-{s.name}", E.sw_to_byte(s.addr), order[s.name])
        if sec.addr != s.addr or len(sec.data) != len(s.data):
            raise SystemExit(f"function table: {s.name} re-linked alone is not the build's size")
        rows += [{"section": s.name, "function": n, "file": f[:-2], "core": "%#x" % E.sw_to_byte(a), "bytes": 2 * (b - a)}
                 for n, f, a, b in got]
    return rows


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
