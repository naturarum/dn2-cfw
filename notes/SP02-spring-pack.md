# SP02: the Spring pack, v2 (DC02 with SPNG v2 and DIRT)

2026-10-26 (one builder, no reviewer). Owner, after the v2 A/B rows and the DIRT rows: "dirt sounds cool! could be
like 20% more marked by the end of the range ... could also reinstate a slight level increase, let's say max 6db", then
"let's build the spring fw syx"; on old kits (GAIN 50 at the stock defaults, or what the kit had), "Full knob, 0 =
clean". SP02 = DC02 (DJ01 with the compiler fix, on the unit "seems stable and sounds identical") with SPNG v2 in the
reverb slot, placed exactly as SP01 placed v1 on DT01. Marks: **[verified]** = built, run in sharcvm or the emulator,
file named; **[inferred]** = reasoned. Nothing flashed.

| | |
|---|---|
| **`out/SP02/SP02.syx`** | 2,436,000 B, sha256 **`4dc2d221d1c24d459ced83ca7f0a16ab3f42e7a5b5d31ec08602a680f302450c`**, version SP02; `scripts/sp02_build.py`, 41 of 41 checks |
| main OS | DJ01's nine mods (sha256-pinned; DC02's main OS is DJ01's) + **spring-names-dn2 0.2** (fork) |
| DSP | recipe `SP02` (`tools/dspsplice/build.py`): section 7 903,072 B, 105 blocks, `a00d7461...` |
| brick checker | **ACCEPT, 12 PASS, 0 FAIL, 0 SKIP** (`out/brickcheck/SP02-2026-10-26/report.md`, 3,104 s; DSP run 347,966 instructions per block (SP01 348,915), peak 0.693) |

## 1. The spring: v2 in `fx/spring.c` [verified]

The prototype of `fx/proto/spng-v2/` (notes/T5.5 section 9) with its chosen options, written for selcc: ratios
1 : 1.21 : 1.32; the cross-feed (a 30-degree rotation about (1,1,1)) as per-spring coefficients set in spring_init,
not `spmix.h`'s table (negative numbers in a static table: notes/T5.5 4; and a table read at a computed index); the
wobble's random value without a shift by an immediate amount (`(int32)((rnd & 0xfffffe00) ^ 0x80000000) * 2^-31`,
equal to the prototype's `(rnd >> 9) * 2^-22 - 1` on 50 M draws) and its periods per spring, not a table;
Catmull-Rom loop reads; the wet gain 2.4. The springs' input clip is fixed at v1's DWEL 64 (drive 1: clean at the
unit's levels), so its gain ramp is gone. **DIRT** on knob D: the cubic soft clip on the wet output at 48 kHz, drive
36 dB x (knob/127)^1.06 (0 is a bypass), made up by two one-pole power meters (in, before the clamp; out, after the
curve; 0.0008 per block, about 0.8 s) less 5.5 of the 36 dB, so the level rises about 6 dB to 127 (measured +5.6 /
+6.3 / +6.0 dB, pluck / drums / chords) and grit begins between 16 and 32. Constants that clang folds from an
expression are written as the folded literals, so both compilers use the same bits.

- **= the prototype**: 40 static settings over four files and two runs with DIRT, MODE and DEC moving, bit for bit
  against `fx/proto/spng-v2/build.sh -DSPV2_DWEL=3`.
- **Cut-independence** (the tests caught it): the prototype's DIRT used the gains `sp_control` had just set, and the
  FIFO path plays a block before its control step while the whole-block path plays it after, so 32-sample calls and
  random calls differed. Now each tank block carries the DIRT drive it was made with (`ldr`, `lcd`, saved in
  `sp_input`), and `sp_interp` computes the make-up when it plays the block. Static settings stay bit for bit the
  prototype's (the meters see the same blocks in the same order); a knob move lands one block (0.67 ms) later.
- **`make test`: 861 checks, 0 failed.** Changed: the DIRT law (0 / 17.41 / 36 dB at 0 / 64 / 127); DEC 0's RT60
  tolerance 25 % (v2 measures 0.24 s against 0.30, v1 was within 12 %); the DIRT jump's click ratio against the steady
  DIRT 127 (0.69; against DIRT 64 it was 11, the clip's own corners on a sine); v2's integer fields zeroed before the
  denormal scan (`mper` 467 read as a float is subnormal).
- State 43,248 B (v1 43,020); SpgSlot 43,296 B, `0x80000018-0x8000a938`, still below the constants at `0x8000b000`.
  DIRT's fields sit before the returns and the rings, so the rings stay last (the soak reads them there).

SP01 keeps v1: `tools/dspsplice/src/sp01/spring.c`, `spring.h` (recipe SP01's sources since today) and
`scripts/sp01/spring-names-dn2/` (0.1, for `sp01_build.py`). Rebuilt: SP01's section 7 and DSP .syx byte for byte,
and `SP01.syx` (`859589f2...`) byte for byte [verified].

## 2. The DSP [verified: `out/SP02/SP02-dsp/build.json`]

Recipe SP02 = DC02 plus SP01's keys: `fx/spring.c` and `spng_glue.c` in code2 after flg_sync.c (`c2_tail`), the
reverb's two stubs (`stubs2`), the constants in DDR (`dmda_ddr`, 164 B at `0x8000b000`), SpgSlot at `0x80000018`
(`ddr_slot`), the reverb's two CJUMPs (process `0x1c1d11`, init `0x1c1643`). DC02's peephole runs over the spring's
code too. code2 `0x310000-0x314a08`, 18,952 B (SP01 17,336) inside the proven `0x310000-0x315f8c` (5,508 B left).
Shift immediates in our assembly: 0. Against DC02: the final images differ only in code2 after flg_sync.c (DC02's
ends at `0x31048e`), the two CJUMPs and the DDR constants (`sp02_build.py`'s check).

## 3. Checks

- **sharcvm** (`tools/dspsplice/verify_sp02.py`, SP01's checks with DC02 for DT01; `out/SP02/SP02-dsp/verify.json`,
  `verify-mac.json`):
  - **= the Mac**: 11 static settings (the init patch, an old kit at the stock defaults with DIRT 50, MODE 0 / 32 / 96 /
    127, every knob at 0 and at 127, heavy with DIRT 127, dark and thin, WDTH 127), the reverb's input captured in
    SP02's whole image and rendered by fxrender: 96,000 of 96,000 samples each, bit for bit. (In the full run two of
    the 11 never started: two workers compiled the compressor's offset helper at once; `--only mac --workers 1`.)
    Knobs moving (ramps every block, MODE swept and jumping, random jumps of all seven, the soak's hands): 192,000 of
    192,000 samples each and SpgSlot byte for byte at the end;
  - **= DC02**: in lockstep, the reverb's VOL at 0 (F1, K1 with the reverb's knobs, S1; 10,500 blocks): the whole mix,
    the flanger, the delay and both reply pages every block bit for bit; VOL up (7,500 blocks): the flanger and the
    delay bit for bit;
  - **the stock reverb does not run**: none of its own 1,549 instructions, nothing but ours in the reverb's call and
    its init;
  - **stores**: the reverb call stores only into SpgSlot, the stack, the save area and its outputs (1.4 M stores, 0
    outside); the effect init's stores into the region all come from our code;
  - **load**: worst block DC02 354,425 -> SP02 368,354 static (+13,929, **+2.1 %** of 666,667), 372,458 with every
    reverb knob ramping every block (+18,070, **+2.7 %**); the reverb call 26,957 mean static (SP01 22,509-22,582:
    v2's wobble, cubic reads and cross-feed, and DIRT), 34,021 at most.
- **Emulator** (`scripts/sp02-emu.sh`: SP02.syx cold-booted in digikit to 800M; `out/SP02/emu/`): the page
  (`sp02-spring.script`) is "Spring (2/3)", PRE DEC MODE DIRT / VOL HPF TONE WDTH, a turn of D heads the screen
  "Dirt=51" (the emulator project's kit is an old one: GAIN 50); `scripts/sp02_emucheck.py` (`out/SP02/emucheck.json`),
  6 of 6: spring-names' tick done and page 18's rows; WDTH's display; C and D as plain knobs; **a new kit: PRE 0, DEC
  64, MODE 64, DIRT 0, HPF 0, TONE 127, VOL 110, WDTH 96**; DJ01's new kit the stock defaults (GAIN 50); after
  `sp02-kit.script` (MODE 70, DIRT 53, TONE 91, WDTH 10 on A01) the kit serializer and loader keep k 41..48.
  Overbridge's version replies `0062 1.12` (`out/SP02/emu-SP02-version.json`).
- **Soak** (`tools/dspsplice/soak_cmp4.py run --rev --only K1,K2,K3,F1,S1,R1 --scale 0.25 --dsp out/SP02/SP02-dsp`,
  `out/SP02/soak/`): 517,500 blocks (345 s of audio), **0 faults, 0 anomalies**; SpgSlot's magic and fresh flag
  intact and no non-finite word at the end of each recipe; stack 1,932 B (DT01's); the spring's wet peak 0.145; at most
  380,624 instructions in a block (SP01's soak: 378,873), the reverb call at most 33,998.

- **The patcher** (`make PACKS="SP02"`, `tools/patcher/packs/SP02.json`): `digiwerk-patch --pack SP02` on Elektron's
  stock 1.12 file writes SP02.syx byte for byte (`4dc2d221...`).

## 4. The main OS: spring-names-dn2 0.2

`scripts/sp02_make_mod.py` (0.1's with three sites changed): row 0x7e's long name "Dirt" and label DIRT (`snm_dirt`,
`snm_dirt_s`), its new-kit default 50 -> **0**. The 18 other sites are 0.1's. The fork's
`examples/spring-names-dn2/` is 0.2 now.

## 5. Limits

- Unit only: the sound, the real load, a power cycle.
- Old kits bring D = GAIN's value (50 at the stock defaults): DIRT 50, some grit and about +2 dB (the owner chose the
  full knob). The release notes and the flashing guide say so.
- Overbridge's plugin names C, D and F FREQ, GAIN and LPF; WDTH has no CC (as SP01).

## 6. Files

New: `scripts/sp02_build.py`, `scripts/sp02_make_mod.py`, `scripts/sp02_emucheck.py`, `scripts/sp02-emu.sh`,
`scripts/sp02-spring.script`, `scripts/sp02-kit.script`, `tools/dspsplice/verify_sp02.py`,
`tools/dspsplice/src/sp01/` (v1, frozen), `scripts/sp01/spring-names-dn2/` (0.1, frozen),
`tools/patcher/packs/SP02.json`, this note, `out/SP02/FLASH-SP02.md`. Changed: `fx/spring.c`, `fx/spring.h` (v2),
`fx/render.c` (DIRT as a name), `fx/test_spring.c`, `tools/dspsplice/build.py` (recipe SP02; SP01 on its frozen
sources), `tools/dspsplice/soak_cmp4.py` (SpgSlot's size from the DSP record), `scripts/sp01_build.py` and
`scripts/sp01_make_mod.py` (the frozen paths); fork: `examples/spring-names-dn2/` (0.2).
