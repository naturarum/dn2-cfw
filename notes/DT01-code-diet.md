# DT01: the code diet (FL02, the same sound, 6.5 KB less DSP code)

**On the unit 2026-10-24: "DT01 flashed, sounds identical, all stable."**

2026-10-23 (one builder, no reviewer). Owner: "start the code diet". Marks: **[verified]** = built, run in sharcvm or on
the Mac, file named; **[inferred]** = reasoned or estimated. Nothing flashed, committed or pushed. Evidence:
`out/DT01/` (build-DT01.json, DT01-dsp/build.json and verify.json, dt01check.json, soak/), scratch measurements in the
session scratchpad (`dt01/`: steps.json, budget.json, fxc_*.json, renders_cmp.json, fs_dt01/fieldstudy.json).

| | |
|---|---|
| **`out/DT01/DT01.syx`** | sha256 **`5999998c6ca63f70b6c91372891df77472b8c4e32f59e6795a82d708ffce3749`**, version DT01; `scripts/dt01_build.py`, 29 of 29 checks |
| main OS | FL02's byte for byte (`5258dc61...`; FL02's eight .elemod files through the patcher, `--version DT01`); sections 2, 3, 4, 5, 8 stored as in FL02.syx |
| DSP | recipe `DT01` (`tools/dspsplice/build.py`): section 7 `fb4b5556...`; every code section inside the proven map, every function reached from the hooks |
| brick checker | **ACCEPT, 12 PASS, 0 FAIL, 0 SKIP** (`out/brickcheck/DT01-2026-10-23/report.md`) |

## 1. The savings in the code DT01 ships [verified: `steps.json`, each step built]

FL02 59,724 B -> DT01 **53,254 B (-6,470 B, -10.8 %)**: block 1 17,966 -> 15,538, code3 31,110 -> 27,070, code2
1,206 -> 1,204, code4 9,442 (unchanged).

| item (cumulative) | saves | where / how |
|---|---:|---|
| the two delay stubs, unreachable since CMP4 (the stock delay's CJUMPs are not patched), not built | 768 | recipe `stubs` |
| the flanger's four never-called API functions (`flanger_man_ms`, `_damp_hz`, `_rate_hz`, `_delay_at`; `flanger_set_bpm` stays, flg_sync.c calls it since FL02) | 572 | recipe `drop` (build.py `diet_asm`) |
| the compressor's two (`mbcomp_ratio`, `mbcomp_gr_db`) | 364 | `drop` |
| fxmath.h's helpers in one copy: the compressor's nine copies of the flanger's (`fx_f2i` .. `fx_knob01`) go, line for line the same text (checked at build time) | 1,464 | recipe `share` |
| the flanger's rings last in its struct (every other field a 32-bit offset load instead of 48-bit) | 374 | `src/dt01/flanger.h` |
| `FL_GLIDE` a function (9 copies in `fl_control`) | 664 | `src/dt01/flanger.c` |
| `FL_UNDENORM`: each flanger state read and written once | 52 | `src/dt01/flanger.c` |
| the compressor's 20 crossover states in one loop (`mb_clear` -282, `mb_control` with `MB_UNDENORM` -1,296, `mb_state_expo` -636) | 2,212 | `src/dt01/mbcomp.c` |

`reach` (new build check): all 68 linked functions are reached from the four hooks. Glue, `cmp_glue.c`, `cmp_tail.c` and
`flg_sync.c` hold no helper copies or dead functions; they are FL02's sources.

## 2. The parked echo, for TAPE [verified: the Mac renders, fxtest, fxcheck]

`fx/spaceecho.c` and `.h` (no recipe builds them): 37,002 -> **34,288 B (-2,714)**: tape and history last 734, `SE_GLIDE`
a function 854, `SE_UNDENORM` and one loop in `se_control` 416, `se_mix_targets` through a pointer per head 442,
`se_control`'s per-head loops on locals 268. A TAPE build would also drop its five API functions (696) and share 13
helper copies (1,992; `fx_svf_coef` from the compressor). `se_glue.c` unchanged (its fields are before the echo).
Checks: all 139 A/B renders (47 Space Echo) bit for bit before and after (`make renders`' commands, both builds);
fxtest 817 checks, 0 failed; fxcheck (sharcvm) on 5 settings incl. every PAN mode, AGE 127, FDBK 127 and knob moves:
the selcc output and the Mac output each bit for bit as before (selcc and the Mac differ where selcc's division does,
as before, notes/T5.3); instructions per block -12..+17 mean, at most +49 worst (of 66,812).

## 3. Checks of DT01 [verified]

- **sharcvm, the whole image, DT01 against FL02** (`tools/dspsplice/verify_dt01.py`, `out/DT01/DT01-dsp/verify.json`,
  1,335 s): (a) verify_sc01's 53 settings that CMP4 used (zero and invalid tails, MIX 0, equal MIX and the relock, one
  band wet, the guard and re-init injections, the cost cases with the flanger's knobs jumping, seeded old kits):
  **8,870,400 of 8,870,400 handler samples identical**, the flanger's output, each band's gain reduction and both reply
  pages (the meters included; word 0, the cycle count, aside) identical in 138,600 of 138,600 blocks, L1 word for word
  at the end (the flanger's slot rearranged: 0 words differ); (e) the stock delay ON, 4 cases (FDBK 64 / 100 / 127,
  every flanger and delay knob jumping), 384,000 of 384,000 samples, the delay's output every block, L1 at the end;
  (s) stores: the chorus and compressor calls only into their own state, the stack, the save area and their outputs
  (89,269 stores, 0 outside); over 60 whole blocks no store into our code nor `0x30ff76-0x318000`.
- **The flanger in the harness** (`tools/sharcvm/tests/dt01check.py`, `out/DT01/dt01check.json`): fl02check's free,
  synced and transition cases (69 cases, 18,048,000 samples): DT01 = FL02 bit for bit, **DT01 selcc = DT01 Mac bit for
  bit**, the slot the same at the end; grid 189 free + 432 synced settings: worst 17,048 / 18,811 (FL02 16,938 / 18,701).
- **The compressor in fxcheck** (3 settings, knob moves, MIX through 0): selcc and Mac outputs each bit for bit FL02's
  (selcc and the Mac differ where selcc's division does, as before the diet; notes/T6.3).
- **Worst block** (whole handler): FL02 364,725, **DT01 364,917 (+192, +0.05 %)**; every case's mean +164..+192:
  FL_GLIDE's nine calls (+110 a block in the flanger, worst 18,905 vs 18,767) and the compressor's loops (+54).
- **Rebuilds**: all 16 earlier DSP recipes (CG01 .. FL02, the frozen sources) section 7 and container byte for byte;
  CMP4 `c1df74aa`, OB01 `bfb88623`, FL01 `a2179c0c`, FL02 `82bfd32d` rebuilt byte for byte by their scripts.
- **Soak** (`soak_cmp4.py --only K1,K2,K3,F1,S1 --scale 0.25`, `out/DT01/soak/`): 427,500 blocks (285 s of audio),
  **0 faults, 0 anomalies**, FLG3 intact; largest blocks 363,266 / 364,200 / 364,483 (FL02's +164), stack 1,932 B.
- **Brick checker**: **ACCEPT, 12 PASS, 0 FAIL, 0 SKIP** (`out/brickcheck/DT01-2026-10-23/report.md`, 2,874 s; DSP run
  340,966 instructions per block (FL02 340,789), peak 0.693).

## 4. selcc study: a shorter field access [inferred from selcc's output, `fieldstudy.py`; the compiler unchanged]

Over DT01's code plus the diet echo and se_glue (89.6 KB): frame loads/stores are 28 % of the bytes, immediate loads
17 %, address adds 10 %, `I4 = Rx` moves 7 %. A field access is base from the frame (4 B), the offset (4 or 6 B), the
add (4), `I4 =` (4), the access (2): 18-20 B, 5 instructions. 1,069 of the 1,589 I4 accesses are base + constant
(130 nested: struct in struct or array in struct, two adds that could be folded):

| selcc change | saves | instructions |
|---|---:|---|
| A: offset folded into the access, `I4 = base; DM(off, I4)` | **5,106 B** (5.7 %) | -2,135 static |
| A + B: the base loaded straight into I4 when it comes from the frame | **6,176 B** (6.9 %) | -2,399 |
| C: the struct pointer kept in an index register within a block | **~11.0 KB** (12 %) | more |

## 5. Budget [verified for the code; SPNG estimated]

Proven room 83,838 B (block 1 17,974, code4 9,798, code3 31,606, code2 24,460); every function placed by first-fit
over the four regions (`budget.py`).

| pack | code after the diet | room | spare / missing |
|---|---:|---:|---:|
| CMP3 + FLNG (DT01) | 53,254 | 83,838 | **+30,584** |
| + TAPE (echo 31,600 after drop and share, se_glue 2,418, the two delay stubs 770) | 88,042 | 83,838 | **-4,204** |
| + TAPE + SPNG (a small spring, ~14.3 KB [inferred]: allpass chain and two lines ~9 KB, control 3 KB, glue 1.5 KB, stubs 0.8 KB) | ~102,300 | 83,838 | **~-18,500** |

What could close it (measured unless marked): a probe build for level 3 (`0x2e4f46-0x2e6000`), the gap and block 1's
tail: +4,430 B of room (closes TAPE alone: every function packs, 226 B spare); selcc A: about -5 KB on that code
(closes it alone), C: about -11 KB; SC02's head
loop -1,316 B (+3.1 % DSP load); crackle cut -2,374 B (changes AGE); merged range tests in `mb_implausible` -184 B
(not applied). SPNG needs more than these together: C + level 3 + head loop give ~16.7 KB; with hand-written loops for
`se_run` (12.5 KB) and `mb_run` (11.2 KB) [not measured] or new places from probe builds.

## 6. Source changes

`tools/dspsplice/build.py` (recipe DT01; `asm_chunks`, `diet_asm`, `reach_check`, `function_table`; the diet path in
`compile_link`, used only by a recipe with `drop`/`share`); new `tools/dspsplice/src/dt01/` (`flanger.c`, `flanger.h`,
`flanger_unit.h` = fx's but a note, `mbcomp.c`); `fx/spaceecho.c`, `fx/spaceecho.h`; new `scripts/dt01_build.py`,
`tools/dspsplice/verify_dt01.py`, `tools/sharcvm/tests/dt01check.py`. fx/flanger.c, fx/mbcomp.c and every glue file
are unchanged, so the earlier recipes build as before.
