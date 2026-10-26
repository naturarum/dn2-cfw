# DC01: the compiler fix (DJ01, the same sound, 5.9 KB less DSP code)

**DC02 on the unit 2026-10-26: "seems stable and sounds identical."**

**DC01 is superseded by DC02 before any flash; kept reproducible** (`build.py DC01` and `scripts/dc01_build.py` still
give `a0b63d39...`). DC02 (section 5) is the same pass in a cautious mode, after the independent review of DC01.

2026-10-24 (one builder; the owner: "yes do the compiler fix"). Marks: **[verified]** = built, run in sharcvm, file
named; **[inferred]** = reasoned or estimated. Nothing flashed, committed or pushed. Evidence: `out/DC01/`
(build-DC01.json, DC01-dsp/build.json and build/peephole.json, DC01-dsp/verify.json, dc01check.json, soak/,
brickcheck.log), the budget in `out/DC01/budget/` (budget.json, budget_dc01.py).

| | |
|---|---|
| **`out/DC01/DC01.syx`** | sha256 **`a0b63d399b04130e0e1e645ebc831c0f96e7c3d3e549336d6f3f23f814eb8175`**, version DC01; `scripts/dc01_build.py`, 31 of 31 checks |
| main OS | DJ01's byte for byte (`f5380787...`; DJ01's nine .elemod files through the patcher, `--version DC01`); sections 2, 3, 4, 5, 8 stored as in DJ01.syx |
| DSP | recipe `DC01` = DT01 + `peephole="AB"` (`tools/dspsplice/build.py`): section 7 `49bfa3dc...`; DT01's sources and selcc output, every code section inside the proven map and shorter |
| brick checker | **ACCEPT, 12 PASS, 0 FAIL, 0 SKIP** (`out/brickcheck/DC01-2026-10-24/report.md`) |

## 1. The pass [verified: `tools/dspsplice/peephole.py`, `tools/dspsplice/test_peephole.py`, 39 tests]

A peephole pass on selcc's `-S` output, after the diet, before selas; only a recipe with the key `peephole` runs it
(DC01). selcc, selas and seld are unchanged (sha256 `c994b100...`, `53b4ae0e...`, `ac36d0db...`).

| pattern | before (selcc) | after | sites in DC01 |
|---|---|---|---:|
| A: the field offset in the access | `R5 = 0x3C; R6 = R3 + R5; I4=R6; DM (I4,M5)=R1;` | `I4=R3; DM (0xF,I4)=R1;` | 603 |
| A, nested (struct in struct) | `R3 = 0x4; R5 = R1 + R3; R1 = 0x14; R3 = R5 + R1; I4=R3; R1=DM (I4,M5);` | `I4=R1; R1=DM (0x6,I4);` | (in A) |
| A, stack arguments | `I4=I6; I4=MODIFY (I4,0x2)(NW); R5=DM (I4,M5);` | `R5=DM (0x2,I6);` | 23 |
| B: the base straight into I4 | `R3=DM (-0x7,I6); ... I4=R3;` | `I4=DM (-0x7,I6); ...` | 428 |

**Safety: a change is kept only where its basic block provably does the same.** The pass parses selcc's 54
instruction forms (anything else makes the function untouchable), builds each function's control flow (delayed
branches, calls, returns, DO loops) and computes liveness over the whole program: calls through the callee's
live-in set, returns through what is live after each call site, the hook stubs' functions returning with everything
live but what the stub restores (R0-R15, I0-I5, M1-M4). Every candidate block is executed symbolically before and
after (registers and flags as linear expressions over unknowns, opaque operations as terms): the memory accesses in
the same order with the same addresses and stored values, the same branch decisions, and every register, ALU,
multiplier and shifter flag group live at the block's exit equal; and the block may not read a register it did not
read before (liveness never grows). After all changes the result is parsed again, liveness recomputed on it, and every
changed block checked again against selcc's own (303 blocks, none put back). So: a removed temp is dead on every path
(a live sum, offset register, flag or I4 refuses it), the base is not changed between the add and the access (else
the move to I4 goes where the add was, or the change is refused), I4 is not read later, a store stays a store. Refused
too: an offset not a multiple of 4, a constant that is an address (>= 0x20000), an offset outside -32..31 words where
a compute shares the instruction, the last 3 instructions of a DO loop body and any change that leaves a body under 6
instructions (selas emits loop bodies unpacked), a change that saves no bytes.

**The two assumptions** (both outside the block check): (1) `DM(k, I4)` for a normal word addresses `I4 + 4k`: true
when I4 holds a word-aligned byte-space address (sharcvm's rule, verified by the DN2 code; selcc's own frame accesses
`DM(-k, I6)` rest on it). The base of every folded access is the address selcc dereferenced minus a field offset, and
every struct of ours is word aligned in L1 or DDR, far from a region's edge; sharcvm's `imm_align` mode (immediate
offsets drop the low bits of I) gives the same results in every check below. (2) A removed address add never
overflows (only the sticky AOS bit could differ; nothing reads it: the stock code tests only STKYx's stack bits).

**The new forms are the stock code's own.** selas's bytes equal CCES's instructions in the stock 1.12 image:
`R2=DM (0x3,I4)` 98080103 (0xb8000b), `DM (0x3,I4)=R2` 99080103, `R15=DM (0x62,I4)` a80f00000062 (0x1c5063, 48-bit),
`F2 = F1 + F2 , R1=DM (0x1E,I4)` 683ef0881212 (0x1c216c), `I4=DM (-0x27,I6)` 9c080a59 (0x1c2d7a, where the stock code
uses I4 in the very next instruction, as B does). Registers written: R0-R15 and I4 only (fewer writes), inside
`CENSUS_ALLOWED`.

**C not done** (the pointer kept in I4 within a block): measured on the A + B output it would remove 150 frame
reloads and 67 moves in DC01's code, about 0.9 KB (2.0 KB over all four packs), and needs a memory-aware check; the
study's 11 KB assumed a free index register per struct, which the census (I4 only) rules out [verified: counts].

## 2. Bytes saved [verified: the builds' function tables; spring, echo and tape: `budget_dc01.py`, each file linked alone]

| effect | file | before | after | saved |
|---|---|---:|---:|---:|
| flanger | src/dt01/flanger.c (with the shared fxmath helpers) | 13,148 | 11,668 | **1,480** (11.3 %) |
| compressor | src/dt01/mbcomp.c | 26,308 | 23,360 | **2,948** (11.2 %) |
| glue | flg_glue3.c, flg_sync.c, cmp_glue.c, cmp_tail.c | 12,272 | 10,794 | **1,478** (12.0 %) |
| spring | fx/spring.c (after SP01's diet), spng_glue.c | 15,362 | 13,980 | **1,382** (9.0 %) |
| echo (TAPE) | fx/spaceecho.c (after the diet), se_glue.c | 34,018 | 30,750 | **3,268** (9.6 %) |
| tape delay (no pack) | fx/tapedelay.c, td_glue.c | 24,458 | 23,842 | 616 (2.5 %) |

DC01's DSP: 53,254 -> **47,348 B (-5,906, -11.1 %)**: block 1 15,538 -> 13,870, code3 27,070 -> 24,122, code4
9,442 -> 8,190, code2 1,204 -> 1,166 (the stubs unchanged). Largest: `mb_run` -1,048, `cmp_process` -822,
`fl_control` -504.

## 3. Checks [verified]

- **The whole image, DC01 against DT01 (DJ01's DSP)** in sharcvm (`tools/dspsplice/verify_dc01.py`,
  `out/DC01/DC01-dsp/verify.json` 1,428 s and `verify-fg-merged.json`): (a) verify_dt01's 53 settings: **8,870,400 of
  8,870,400 handler samples identical**, the flanger's output, each band's gain reduction and both reply pages
  identical in 138,600 of 138,600 blocks, L1 word for word at the end (0 words differ outside our code, data, stack
  and save area; the flanger's slot 0 words); (e) the stock delay ON, 4 cases: 384,000 of 384,000 samples, the delay's
  output every block; (f, new) the flanger synced, 13 cases (four divisions at 60, 120, 174 BPM; free -> synced ->
  free with tempo jumps): 499,200 of 499,200; (g, new) the compressor's rare paths, 4 cases (a tail sequence held for
  4 blocks, a guard trip with all three MIX 0, the re-attack count corrupted to 1000 and -5): 76,800 of 76,800, gain
  reduction every block; (s) stores: the chorus call 26,310 and the compressor call 62,959 stores, none outside their
  own state, the stack, the save area and their outputs, none into our code.
- **Each effect alone, selcc against selcc + the pass** (`tools/sharcvm/tests/dc01check.py`, `out/DC01/dc01check.json`,
  835 cases, 706 s): every output sample and, at the end, every data and bss word and the effect's state region
  identical, run once as it is and once with sharcvm's `imm_align` on: flanger (DT01's, FL02's glue; dt01check's free,
  synced and transition cases and the grid, 690 cases) 47,856,000 samples; spring (spngcheck's 117 cases with the FIFO
  case) 22,464,000; echo (14 cases: every MODE and PAN, AGE/INT 127, knobs jumping and ramping) 2,688,000; tape delay
  (7) 1,344,000; DT01's flanger and compressor through fxdrv (FL_SHP 5, calls of 1..32 samples) 1,344,000. No misaligned
  access in any build.
- **Coverage** (a label before every changed line, the build relinked with them byte for byte DC01.dxe, sharcvm's
  per-instruction counters; tools/sharcvm/coverage.py measures only the stock program's static translation): DC01 has
  1,239 changed lines (the access, the move or load to I4; three of them are unchanged `Rn = imm` lines that the line
  diff puts in a changed hunk: flanger `R3 = 0x2545F491`, cmp_glue `R4 = 0xF8`, cmp_tail `R1 = 0x1`, so 1,236 lines
  really changed). (a), (e) and the effect init ran 1,211; the 28 others, by
  pattern: the flanger synced (`flanger_set_bpm` 4, `flg_spd` 4; verify_sc01 keeps SPD free): run by (f); a repeated
  tail sequence (`cmp_tail_fetch`'s `same` count, 4), a guard trip with all MIX 0 (`mb_restart`'s `xw = 1`, 2) and a
  corrupted re-attack count (`mb_control`'s two clamps, 4): run by (g). **1,229 of 1,239 run in the image.** The last 10
  cannot run in DC01: the flanger's random-step LFO (`fl_lfo_block` 4: FL_SHP is pinned to 0 by flanger_unit.h and
  flanger_init) and a call shorter than the rest of a control block (`flanger_process` 2, `mbcomp_process` 4: the
  handler always passes 32 samples); each of their basic blocks is found instruction for instruction in dc01check's
  fxdrv build of the same files, and run there (8 to 11,226 times). Each harness build's own changed lines: flanger
  293 of 301 (the same 6 plus the never-called `flanger_delay_at`), spring 253 of 253, echo and tape 853 of 859 (the
  echo's partial-call path and one `se_control` wrap; not in DC01).
- **Instructions per block** (the whole handler): DC01 1,633 to 1,921 fewer than DT01 in every case (-0.5 %); worst
  block over (a), (e): DT01 364,917, **DC01 362,888 (-2,029)**; brick checker's DSP run 339,226 (DT01 340,966). Each
  effect's worst block: flanger 18,905 -> 18,205, spring 101,514 -> 94,600 (the FIFO case), echo 69,054 -> 67,985,
  tape 28,902 -> 28,621. Fewer instructions; the B form adds a load-to-use step on I4 that sharcvm does not time
  (CCES pairs a load into an I register directly only with a 16- or 32-bit user: section 5), so cycles on the unit
  are not measured.
- **Rebuilds:** all 18 DSP recipes (CG01 .. DT01, SP01) section 7 and DSP .syx byte for byte with today's build.py;
  CMP4 `c1df74aa`, OB01 `bfb88623`, FL01 `a2179c0c`, FL02 `82bfd32d`, DT01 `5999998c`, DJ01 `7edf7c59`, SP01
  `859589f2`, DC01 `a0b63d39` by their scripts. selcc, selas, seld sha256 unchanged.
- **Soak** (`soak_cmp4.py --only K1,K2,K3,F1,S1 --scale 0.25`, as DT01's, `out/DC01/soak/`): 427,500 blocks (285 s of
  audio), **0 faults, 0 anomalies**, FLG3 intact; largest blocks 360,976 / 361,948 / 362,265 (DT01's 363,266 /
  364,200 / 364,483), stack 1,932 B (DT01's).
- **Brick checker:** ACCEPT, 12 PASS, 0 FAIL, 0 SKIP (`out/brickcheck/DC01-2026-10-24/report.md`, 3,179 s).

## 4. Budget with the pass [verified for the code: `budget_dc01.py`; every function placed by first-fit over the four proven regions, 83,838 B]

| pack | before | with A + B | room 83,838: before | with A + B |
|---|---:|---:|---:|---:|
| CMP3 + FLNG (DC01) | 53,250 | 47,344 | +30,588 | **+36,494** |
| CMP3 + FLNG + TAPE | 88,038 | 78,864 | -4,200 | **+4,974, packs** |
| CMP3 + FLNG + SPNG | 69,380 | 62,092 | +14,458 | **+21,746** |
| FLNG + TAPE + SPNG | 67,868 | 61,498 | +15,970 | **+22,340** |
| CMP3 + FLNG + TAPE + SPNG | 104,168 | 93,612 | -20,330 | **-9,774** |

TAPE = fx/spaceecho.c (its five API functions dropped) + se_glue.c + the two delay stubs; SPNG = fx/spring.c as SP01
builds it (`a1e08448...`, seven knob-law functions dropped) + spng_glue.c + the reverb's stubs; fxmath.h's helpers in
one copy per pack. CMP3 + FLNG + TAPE now packs with 4,974 B spare (code3 4,944 B free, code2 full). All four are
still 9.8 KB short: C would give about 2 KB, the level-3 probe 4.4 KB (notes/DT01-code-diet.md section 5); the rest
needs hand-written loops or a new proven place.

## 5. DC02: the cautious mode (after the review of DC01) [verified]

The independent review of DC01 found 124 places where B's new load into I4 is followed directly by a 48-bit
instruction that uses I4 (a compute with the access, an offset outside -64..63 words, a post-modify access with a
compute, or anything in a DO loop body). The stock 1.12 code never has that pairing (a load into an I register is
followed directly by a 16- or 32-bit user 598 times, by a 48-bit one 0 of about 14 times: there is always work in
between). sharcvm does not model the pipeline, so DC02 keeps to the pairings the chip already runs.

**The rule** (mode `"ABs"`, recipe `DC02`, `build.py DC02`, `scripts/dc02_build.py`): B is refused wherever the
instruction right after the new `I4=DM (k,I6)` uses I4 and is 48 bits (`peephole.py` `i4_load_w48_pairs`, checked on
every candidate block and again over the whole result, where a hit stops the build); A, the stack arguments and B
before a 16- or 32-bit user, or with other work in between, are as in DC01. Mode `"AB"` is unchanged: recipe DC01 still
gives `a0b63d39...` (its report now counts the 124 pairings). Seven unit tests more (39): the refusal for a 48-bit
offset, a compute with the access and a loop body; B kept before a 32-bit user and with work in between; the scan.

| | DC01 (AB) | DC02 (ABs) |
|---|---:|---:|
| sites: A / stack arguments / B | 603 / 23 / 428 | 600 / 23 / 328 |
| loads into I4 followed directly by a 48-bit user | 124 | **0** |
| DSP code (DT01 53,254 B) | 47,348 (-5,906) | **47,932 (-5,322, -10.0 %)** |
| block 1 / code4 / code3 / code2 | 13,870 / 8,190 / 24,122 / 1,166 | 13,882 / 8,366 / 24,518 / 1,166 |

Bytes saved per effect in mode ABs: flanger 1,468 (11.2 %), compressor 2,552 (9.7 %), glue 1,302 (10.6 %), spring
1,306 (8.5 %), echo 2,838 (8.3 %), tape delay 612 (2.5 %).

| | |
|---|---|
| **`out/DC02/DC02.syx`** | sha256 **`330dfa9bb63fe32e4e4d8499be071b6f1556af3702a552a6596ba9c48a2f0971`**, version DC02; `scripts/dc02_build.py`, 31 of 31 checks (dc01_build.py's, for DC02) |
| main OS | DJ01's byte for byte (`f5380787...`); sections 2, 3, 4, 5, 8 stored as in DJ01.syx |
| DSP | section 7 `9fd315f9...`; DT01's sources and selcc output, every code section inside the proven map and shorter |
| brick checker | **ACCEPT, 12 PASS, 0 FAIL, 0 SKIP** (`out/brickcheck/DC02-2026-10-24/report.md`) |

**Checks** (as DC01's, section 3):

- **The whole image, DC02 against DT01** (`verify_dc01.py --name DC02`, `out/DC02/DC02-dsp/verify.json`, 1,558 s):
  (a) 53 settings 8,870,400 of 8,870,400 samples, the flanger's output, gain reductions and reply pages in 138,600 of
  138,600 blocks, L1 and the flanger's slot word for word; (e) 384,000 of 384,000; (f) 499,200 of 499,200; (g) 76,800 of
  76,800; (s) 26,310 + 62,959 stores, none outside their own state, none into our code.
- **Each effect** (`dc01check.py --patterns ABs --image DC02`, `out/DC02/dc02check.json`, 835 cases): every sample and
  the end state identical, also with `imm_align` on: flanger 47,856,000, spring 22,464,000, echo 2,688,000, tape
  1,344,000, DT01's flanger and compressor through fxdrv 1,344,000; no misaligned access.
- **Coverage:** 1,232 changed lines (five of them unchanged `Rn = imm` lines in a changed hunk), 1,222 run in the image;
  the 10 others are DC01's unreachable ones (the random-step LFO, calls shorter than a control block), their blocks
  found instruction for instruction in the fxdrv build and run there (59 of 59 such lines).
- **Instructions per block:** 1,572 to 1,836 fewer than DT01 in every case; worst block 364,917 -> **362,962
  (-1,955)**; each effect's worst: flanger 18,905 -> 18,209, spring 101,514 -> 94,693, echo 69,054 -> 68,039, tape
  28,902 -> 28,653. Fewer instructions, not measured cycles.
- **Unit tests** 39 of 39. **Rebuilds:** all 20 DSP recipes (CG01 .. DT01, DC01, DC02, SP01) section 7 and DSP .syx
  byte for byte; CMP4, OB01, FL01, FL02, DT01, DJ01, SP01 `859589f2`, DC01 `a0b63d39` and DC02 `330dfa9b` by their
  scripts; selcc, selas, seld sha256 unchanged.
- **Soak** (as DC01's, `out/DC02/soak/`): 427,500 blocks, **0 faults, 0 anomalies**, FLG3 intact; largest blocks
  361,091 / 362,039 / 362,350 (DT01's 363,266 / 364,200 / 364,483), stack 1,932 B.
- **Brick checker:** ACCEPT, 12 PASS, 0 FAIL, 0 SKIP (`out/brickcheck/DC02-2026-10-24/report.md`, 2,945 s; DSP run
  339,305 instructions per block, DT01 340,966).

**Budget with mode ABs** (`out/DC02/budget/`, as section 4):

| pack | before | with ABs | room 83,838: before | with ABs |
|---|---:|---:|---:|---:|
| CMP3 + FLNG (DC02) | 53,250 | 47,930 | +30,588 | **+35,908** |
| CMP3 + FLNG + TAPE | 88,038 | 79,880 | -4,200 | **+3,958, packs** |
| CMP3 + FLNG + SPNG | 69,380 | 62,754 | +14,458 | **+21,084** |
| FLNG + TAPE + SPNG | 67,868 | 62,016 | +15,970 | **+21,822** |
| CMP3 + FLNG + TAPE + SPNG | 104,168 | 94,704 | -20,330 | **-10,866** |

The cautious mode costs 584 B on DC02's code and about 1 KB over all four packs; CMP3 + FLNG + TAPE still packs.

## 6. Files

New: `tools/dspsplice/peephole.py` (the pass and its check), `tools/dspsplice/test_peephole.py`,
`tools/dspsplice/verify_dc01.py` (whole image against DT01, and the coverage; `--name DC02`),
`tools/sharcvm/tests/dc01check.py` (each effect, selcc against selcc + pass; `--patterns ABs --image DC02`),
`scripts/dc01_build.py`, `scripts/dc02_build.py`, `out/DC01/FLASH-DC01.md`, `out/DC02/FLASH-DC02.md`. Changed:
`tools/dspsplice/build.py` (recipes DC01 and DC02; the key `peephole` in `compile_link`'s diet path, which writes
build/peephole.json; a recipe that sets it outside the diet path is refused). No other recipe uses the key; every one
rebuilds byte for byte (section 3).
