# MR03: EEEM, MRB in the reverb slot with fault tones (a test build)

2026-11-10, one builder. EEEM = base 0.3 (EEEE's main OS mods) + morph-names-dn2 0.1, and Elektron's DSP with **MRB**
(fx/mrb.c, notes/MR02) in the reverb slot plus **fault tones** (Elektron's park points play a pitch per fault).
Marks: **[verified]** = built or run here, file named; **[inferred]** = reasoned. Nothing flashed, committed or pushed.

| | |
|---|---|
| **`out/EEEM/EEEM.syx`** | 2,423,072 B, sha256 **`6133d6c5af083eda48262623e4453b90cbd81a9cf9852362f2d8fefa0a84408f`**, version EEEM; `scripts/pack_build.py EEEM`, every check ok (`out/EEEM/build-EEEM.json`) |
| DSP | `tools/dspsplice/build.py EEEM` (pack recipe, 4th letter M): section 7 862,112 B, 99 blocks, `61c0e921...` |
| main OS | base 0.3's seven mods + morph-names 0.1 (`b2a0a810...`) |
| brick checker | **ACCEPT, 12 PASS, 0 FAIL, 0 SKIP** (`out/brickcheck/EEEM-2026-11-10/report.md`; its DSP run 325,072 instructions a block, stock 320,281, peak 0.692) |

## 1. The DSP [verified: `out/EEEM/EEEM-dsp/build.json`]

- `pack_recipe` takes S, M or E as the 4th letter; every existing recipe rebuilt to the same section 7 (EEES `f7029ed6`,
  CFES, EFTS, EETS, CEEE, SP02, TE01 compared). For M: `mrb.c` and `tools/dspsplice/src/mrb_glue.c` (spng_glue.c's
  glue: `MrbSlot {magic, fresh, last[6], Mrb}` at 0x80000018, knobs A..F from the reverb's parameter struct on the
  1/256 grid, set on change, snapped on the first block, G unused) fitted one function at a time into the proven
  regions (TE01's split/fit), `peephole_skip` for mrb, constants in DDR at 0x80058000 (300 B, after MrbSlot's end
  0x8001983c, inside the reverb's region), the reverb's two stubs and CJUMPs as for S.
- Placement (all proven, PACK_CODE): block 1 `0x2dd600-0x2e10a6` 15,014 B (stubs 768, fault tones 192, MRB 13,780,
  glue 274; 2,960 B left of 0x2e1c36), code4 `0x2e2900-0x2e4f2e` 9,774 B (MRB 8,768, glue 1,006; 24 B left);
  code2 and code3 unused. Nothing in 0x315f8c-0x318000. MrbSlot 104,484 B (260,336 B left in the region).
- `legal.py EEEM`: 16 loops, 1,492 indexed accesses, no hit. No shift by an immediate. Every selcc function of mrb.s
  and mrb_glue.s ends in `JUMP (M14,I12) (DB)`.

## 2. Fault tones [verified: the stock code disassembled; `out/EEEM/verify/all.json` fault]

What the stock image holds (not quite the brief's "EMU; JUMP self"): the ILOPI (0xb8b560), PARI (0xb8b57c), EMUI
(0xb8b546) and default (0xb8b9f2) handlers clear IRPTEN, write `{8, code, address, address}` at DM 0x26ef88..0x26ef94
(code at 0x26ef8c: 0x501, 0x502, 0x507, 0xb) and `JUMP 0x1c07c6`: NOP; **IDLE (0x0081) at 0x1c07c7; JUMP 0x1c07c7**.
The abort park is the same shape at 0x1c1430 (IDLE at 0x1c1431), reached from FreeRTOS's stack-overflow hook
(0x1cd96a -> 0xb8b538, raise(2), then JUMP 0x1c1430) and from main's end; no record there.

build.py (key `parks`) replaces each IDLE + JUMP (8 bytes) with `JUMP ft_fault` / `JUMP ft_abort` + NOP.
`tools/dspsplice/src/faulttone.s` (selas; block 1): clears IRPTEN, SIMD and broadcast, writes all 128 words of the
two SPORT4A buffers 0x2c0478 and 0x2c0578 (contiguous, frame n's L and R at word 2n, 2n+1, as 0x1c9d3f interleaves
them), then IDLE; JUMP back. No stack, no hardware loop, no immediate shift, no DAG register stored. The words are
written as the DMA sends them (no negation; the stock tail's negation would only flip the square).

| fault | entry | record code | tone (P frames of the 64-frame loop) |
|---|---|---|---|
| illegal opcode (ILOPI) | 0x1c07c7 | 0x501 | **1500 Hz** (32) |
| parity (PARI) | 0x1c07c7 | 0x502 | **3000 Hz** (16) |
| EMUI, default handler, any other code | 0x1c07c7 | 0x507, 0xb, other | **6000 Hz** (8) |
| abort: stack overflow, main's end | 0x1c1431 | (none) | **1500 + 6000 Hz**, each at half amplitude |
| (a hang, not a park) | | | the stock replay: a ~750 Hz buzz or silence |

Peak 0x0ccccccc (-20.0 dBFS), L = R. In sharcvm, each path driven from the stock handler's entry after 300 blocks
(an interrupted PC pushed for the handlers): the record as the handler writes it, both buffers exactly the table's
square, the core in ft's IDLE and still there 1,000 instructions later, our routine's 128 stores all in the buffers,
nothing else in memory changed (the abort path: one word, 0x26efc8, raise's own store). In normal running no
fault-tone instruction runs (exec).

## 3. The main OS: morph-names-dn2 0.1

`scripts/mr03_make_mod.py` (sp02_make_mod.py's sites, every stock value checked) writes
`vendor/elekloader/examples/morph-names-dn2/mod.json`: 15 sites: C CHAR "Character", D SIZE "Size", F TONE "Tone";
DEC's value text the plain number; C and D plain knobs (switch table 0x4006fb3c/3e, shelf page 0x4006f8e8); new-kit
defaults PRE 0, DEC 64, CHAR 64 (stock), SIZE 64, HPF 0, TONE 70. Title "Reverb" and row 0x83 checked and left
alone. Its one source, `morph_names.c`, holds only the six strings (elekloader needs a source for them). Built by
`scripts/mr03_names_build.py` (SDK, lint with core, a link record `out/MR03/build/MR03-main.syx.json` that pins its
sha256 `e632041a...` for pack_build's MOD_SRC; MR03-main.syx is a record, not for flashing). pack_build: FX_MODS M =
morph-names, after spring-names' place; a new check for M: exactly the two park patches, stock bytes to the JUMPs.

## 4. Checks

| check | result | evidence |
|---|---|---|
| pack_build EEEM (all its checks; the hook count now counts CJUMPs, the parks have their own check) | ok | `out/EEEM/build-EEEM.json` |
| static: 10 settings x 1 s, image vs the Mac build of mrb.c through its own API | 96,000/96,000 samples each, Mrb state byte for byte | `out/EEEM/verify/all.json` static |
| moving: ramp, CHAR sweep and jumps, random jumps, the soak's hands, 2 s each, glue + MRB Mac build | 192,000/192,000 each, MrbSlot byte for byte | moving |
| VOL 0 against EEEE (R1, K2) | mix, chorus, delay, reply pages every block bit for bit; DDR outside the region 0; L1 at the end word for word outside our code, stack, hook and park sites (29 / 35 words differ only as +0 / -0, as SP02's) | `out/EEEM/verify/lock.json` (rerun with the park sites as whole words; all.json's first run flagged exactly those two words) |
| VOL up | chorus and delay bit for bit | lock |
| the stock reverb never runs | its 1,540 own instructions: 0 run in EEEM; the call and init run only ours | exec |
| stores | 986,752 stores in 300 blocks, 0 outside MrbSlot, stack, save area, outputs; init: all ours | stores |
| silence: 15 s soak input + 180 s silence | 292,500 blocks, no fault, 0 non-finite (outputs, MrbSlot), magic intact, at most 335,648 instructions a block; MRB's output still nonzero at the end (the held knobs' decay) | silence |
| FX kill: 15 s + 60 s (DEC 0, VOL faded, delay off) | no fault, 0 non-finite, magic intact; MRB's output exact 0 from 19 s (4 s into the kill) | kill |
| digikit's coupled ColdFire + SHARC+ emulator (upstream clone in out/spring-stop-investigation/emu, EEEM added to its device profile; ready state cold-booted from EEEM.syx's own section 7, the boot-ROM stream check passed), reverb sends poked to 127: (1) trig, PLAY, STOP at 5 s, 90 s stopped, PLAY at 95 s, trig at 96 s; (2) the same with the FX-kill words (REV_DEC, delay FDBK and VOL 0) from the first frame | both: no halt, no trap, 0 non-finite; the sound (MRB's tail) dies by 4.8 s / 3.5 s, nothing from 6 to 95 s, **sound back at 96 s** (rms 0.037 / 0.034); MrbSlot's magic intact, the fault record 0 | `out/EEEM/emu-coupled/` |
| ColdFire emulator (vendor/digikit, `scripts/mr03-emu.sh`): "Reverb (2/3)", PRE DEC CHAR SIZE / VOL HPF TONE, C and D plain knobs, "Character=65", "Tone=97"; `mr03_emucheck.py` 5/5: page 18 stock rows, the labels in RAM, **a new kit PRE 0 DEC 64 CHAR 64 SIZE 64 HPF 0 TONE 70** (VOL 110, routing 1 stock), a kit's k 41..48 through the serializer and loader; SETTINGS > SYSTEM > OS UPGRADE ("WAITING FOR SYSEX"), NO back to SYSTEM, SYN, PLAY (the pattern runs), STOP | ok | `out/EEEM/emu/mr03-*/`, `out/EEEM/emucheck.json` |
| brick checker | ACCEPT 12/12 | `out/brickcheck/EEEM-2026-11-10/report.md`, `out/EEEM/brickcheck.log` |
| patcher: `make PACKS="EEEM"`, `digiwerk-patch --pack EEEM` on stock 1.12 | EEEM.syx byte for byte (`6133d6c5...`); stockcheck no stock run of 16 B | `out/patcher/EEEM/`, `out/patcher/cli/EEEM/digiwerk-patch` |

**Load** (sharcvm instructions a block; the block is 666,667): worst block EEEE 320,734 (48.1 %), **EEEM 329,066
(49.4 %, +1.25 %)** with every reverb knob ramping (327,928 static), EEES 340,521 (51.1 %). The reverb call: stock
14,566; MRB 19,318 mean / 21,971 max static, 21,571 / 23,175 ramping; the spring (EEES) 28,696 / 33,463.

## 5. Limits

- Unit only: the sound, the real load, whether the stop seen with the spring comes back. sharcvm and the emulators
  have no faults, caches or real pipeline; the fault tones are proven in sharcvm only, by entering the handlers.
- Like the spring, MRB's output reaches exact 0 in silence (FX kill: after about 4 s). After a pattern stop it depends
  on the kit: in the coupled emulator (default kit) the whole output was exact 0 from about 15 s after STOP until
  PLAY; in sharcvm's silence run (the delay's feedback high) MRB was still nonzero after 180 s (2e-21, falling about
  11 dB per 10 s, fed by the delay's tail). The guide asks for moderate delay feedback in the stop step. [verified:
  `out/EEEM/emu-coupled/eeem-*.wav` read as float, `verify/all.json` silence]
- The patcher pack lists no effect icon (`effects` []): mkpatch knows no MRB effect id or icon yet (public UI work).
- Old kits bring C = FREQ, D = GAIN, F = LPF values (64, 50, 96 at the stock defaults).
- Files: `tools/dspsplice/build.py` (pack letter M, keys `dmda_ddr_src`, `parks`, `jump_bytes`, the function table
  with block-1 assembly), `tools/dspsplice/src/mrb_glue.c`, `tools/dspsplice/src/faulttone.s`,
  `tools/dspsplice/verify_eeem.py`, `scripts/pack_build.py`, `scripts/mr03_make_mod.py`,
  `scripts/mr03_names_build.py`, `scripts/mr03-emu.sh`, `scripts/mr03_emucheck.py`, `scripts/mr03-*.script`,
  `tools/patcher/packs/EEEM.json`; fork: `examples/morph-names-dn2/`.
