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

## 6. The stop on the unit, narrowed (2026-10-31)

SX01 (CFEE's main OS + SP02's DSP) stops, SX02 (CFES's main OS + DC02's DSP) runs: the spring's DSP code. SX03 (the
engine's additions gated by a stage on G): the stop at **exactly G 16**, stage 1, v2's engine with every addition off
(linear reads, own returns, no wobble, DIRT off); stage 0 (all the glue and the knob code, the engine not called)
runs. v1 (SP01) ran on 26 Oct; it was built on DT01, without DC02's peephole pass; v2 (SP02, SX03) on DC02, the pass
over spring.c too (258 sites in SX03; the glue's 63 run in stage 0 without a stop). The code changes between v1 and
stage 1 look harmless (sp_input reads all three springs' returns into rr[][] before mixing; DIRT's ramps moved to
sp_interp); the state ends at 0x8000a944, far from the constants at 0x8000b000; the load is +2.1 %. **SX04**: SX03
with the pass not run over spring.c (recipe key `peephole_skip`), code2 up to 0x3150ac (proven to 0x315f8c).

**SX04 on the unit (2026-10-31): the reverb plays past G 16** (owner: "it works past 16! suddenly width goes up": the
width jump is the diagnostic itself, stage 0 is silent and the spring comes in at 16 with WDTH fixed at 96). So the
peephole pass's rewrites of spring.c are the cause; v2's engine itself runs. Not yet reported: G up to 127 (stage 5,
the whole of v2 with DIRT 64).

What is special about the spring's 258 sites, offline [verified: `out/SX03/SX03-dsp/build/peephole.json`, the
final `.s` files]: nothing by form. With registers and numbers normalised, every rewrite form in the stage-1
functions (spring_process, sp_control, sp_input, sp_interp, sp_ap2, sp_ap2x2, sp_targets, sp_coefs) also occurs at
least three times in the compressor's, flanger's or glue's code, which runs on the unit; no compute-plus-access
offset outside -32..31; a move into I4 followed directly by a 48-bit user of I4 occurs in mbcomp too (109) and
cmp_glue (13). Nor is DDR: the tape echo's state is in DDR too (SeSlot at 0x8005912c) and its 499 rewritten sites
run on the unit (CFTE). sharcvm runs SP02 bit for bit, so the rewritten code computes the right values there; on
the chip something in one or more of the spring's rewrites does not hold [cause open]. Only the unit can split it
further: a build with the pass over some of the stage-1 functions only, about three flashes to the function, five
more to the site.

**For 0.3.0 [proposed]:** the spring builds with `peephole_skip=["spring"]` (exactly SX04's way of building the
spring), the pass kept for the compressor, flanger and glue as in every build that runs. SX04's code2 ends at
0x3150ac, inside the proven 0x310000-0x315f8c.

## 7. digikit's silicon rules, checked on every build (2026-11-06) [verified]

digikit (m-dwyer/digikit main, PRs #220, #222, #224; findings 49 section 10 and 91) found code that the assembler and
the emulators accept and the SHARC+ chip does not run: a VISA (16- or 32-bit) instruction in the last 11 of a hardware
loop body (PRM 4-42; it stopped their freqshift on a Digitakt II, from selache's one loop-end slot in nested loops), a
DM store of a DAG1 register (PM: DAG2) through an index register, a post-modify load of a register of the index
register's DAG, and selache mis-encodings (immediate shifts, sub-word direct access, 32-bit `MODIFY(Ii, Mm)`).
`tools/dspsplice/legal.py` (the first three, after patchkit's `sharc_legal.rs`) over CETE, EFTE, CEEE, EFEE, EETE,
TE01, DC02, the six spring builds, SX03, SX04 and SP02: **no hit** (every loop's end found: EFES 13, CFES 26, TE01
25 loops judged, none skipped; no VISA instruction in any loop body). The encodings: no Type 7 `MODIFY(Ii, Mm)`, no
Type 14d sub-word access, no uncertain decode, no immediate shift in our code. So none of these explains 0.2's
spring stop or EFES's locks; the cause stays open (the test plan: EFES on USB MIDI, then EFES-2).

## 8. EFES on the unit, 6 Nov [owner]

- **Correction (owner): USB MULTI/MIDI was on the whole session**, not USB MIDI as I first read. EFES played 30
  minutes without a lock, then locked about 20 minutes later (after switching to USB AUDIO/MIDI and back while
  playing, then left idle: no sound on PLAY, the synths silent). So EFES has locked in both sessions on 0.4 (twice
  in a short one on 4 Nov, once after about 50 minutes on 6 Nov), all on USB MULTI/MIDI; it has never been run on
  another USB mode. Nothing says the USB mode matters.
- **CFTE on USB MULTI/MIDI:** 30 minutes, playing and idle, no lock (after its hour on 4 Nov). Our USB code does not
  lock builds in general.
- Checked offline (6 Nov): the fx_sin_q/fx_cos_q frame defect on EFES's final `.s`: no caller reads R14 or R15 after a
  call that reaches them, so an interrupt landing in the defect changes nothing; digikit's silicon rules (section 7):
  no hit; EFES's main-OS mods (flanger-names, flanger-sync, spring-names) change labels and pages only, no frame
  layout; the stock reverb's DDR region is addressed only by its init and call setup (T3.4); EFES's load in sharcvm
  67.6 / 68.6 % (CETE's is higher, and runs).
- **Next: EEES** (the spring alone, whose spring runs only its own code, none of the borrowed helpers that went
  through the pass), an hour or more. Locks -> the cause is in the spring; holds -> the spring with another effect:
  EFES-2 (the spring with its own helpers, the frame defect fixed).
- **EEES locks too [owner, 6 Nov]:** 20 minutes of playing, then the pattern stopped, and PLAY gave no sound. The
  spring alone, running only its own code (no borrowed helpers): the cause is in the spring. Two locks now came at a
  stop or while idle (EEES; EFES's third), one at a pattern change (EFES's first). Read so far: DIRT's make-up takes
  log2 of the meters + 1e-12 (no 0/0), fx_log2 returns -126 for x <= 0, fx_exp2 clamps; no division in the tank.
  Asked the owner to look at the USB inputs while frozen: the tracks (1-2) against the main mix (33-34) and the reverb
  return (37-38), to tell a DSP that runs with a poisoned mix from a stopped DSP.

- **Offline, the spring through silence [verified, 6 Nov]:** fx/spring.c on the host with denormals flushed (as the
  chip), 40 trials of random knobs (every fourth with DIRT and DEC at 127): 20 s of noise bursts, then 5 minutes of
  silence, a knob changed every 2 s: no non-finite output and no non-finite word in the Spring state (harness in the
  session scratchpad, `silence.c`). So the C code itself does not poison its output when the sound stops.
- **Reproducible on the unit [owner, 7 Nov]:** EEES, a pattern stopped for one or two minutes: PLAY gives no sound
  (the tracks animate). Also with USB CONFIG on USB MIDI from a cold start (so not usb-multi). Entering the OS
  UPGRADE menu also leaves it silent (whether CFTE does the same is asked: it may be the menu's own behaviour).
- **sharcvm [verified, 7 Nov]:** EEES's whole image, 15 s of the soak's input with the hands walking, then 180 s of
  silence with every knob held (`out/packs-verify/EEES-silence.json`, scratchpad `silence_soak.py`): 292,500
  blocks, no fault, no anomaly, insns max 344,291, spring output finite, SpgSlot intact. So the compiled code runs
  through silence correctly in the simulator: the stop is the chip's.
- **FP underflow on the host [verified, 7 Nov]:** denormals flushed as on the chip, `fetestexcept(FE_UNDERFLOW)` per
  block over 190 s of silence after 10 s of noise: the spring 148 blocks (the first as the silence starts), SX05's
  spring 0; the tape echo 0; but the flanger (dt01, the unit's) about every block with FDBK off 64, and the flanger
  builds have never stopped. So underflow alone does not explain it; SX05 tests the whole "after the sound stops"
  state (no state of the spring decays to zero or to the underflow range).
- **SX05 [built, 7 Nov]:** EEES's main OS (`049018f8`, byte for byte) + SX05's DSP (`2c5d43ba`; recipe SX05,
  `tools/dspsplice/src/sx05/spring.c` = fx/spring.c with a -240 dB floor: a DC of 1e-12 into the input, +-1e-12 at
  12 kHz into the pre-delay ring); code2 to 0x314cae (proven to 0x315f8c); legal.py ok. `out/SX05/SX05.syx`
  `a1310a74`.
- **SX05 on the unit [owner, 7 Nov]: no sound at all from power-on.** It differs from EEES (which plays) by three
  immediates in sp_input, its frame +2 words, and t38_code2 +116 bytes (everything after sp_input shifted). So a
  tiny change to the spring again flips the chip between "runs" and "silent at once", as the peephole pass did
  (SX03/SX04). **SX06** built, not sent: SX05's code with the floor's immediates set to 0 (recipe key
  `asm_subst`, build.py), section 7 `1cd9ce91`; it would split layout from values in one flash.
- **No more blind device tests (owner, 7 Nov).** A full investigation started offline, in three parts, each
  writing to `out/spring-stop-investigation/` (brief: `BRIEF.md`): reproduction in an emulator with interrupts
  and timing (`emulator.md`), the chip's documented hazards checked against every build (`chip.md`), and what
  Elektron's DSP and main OS do around the reverb at a stop, when idle, in OS UPGRADE and at a pattern change,
  plus real time (`stock-flow.md`). The owner is open to rethinking the reverb.
- **The offline investigation, 7-8 Nov [verified unless marked]: no cause found, a long list ruled out.** Summary
  and reports in `out/spring-stop-investigation/SUMMARY.md`. Ruled out: an illegal instruction (107k instructions,
  ADI's tables, two decoders), an illegal/misaligned/unpopulated access (wrong-path loads included), parity (the boot
  ROM fills L1 with valid parity; same never-written reads in CFTE/CETE), FP exceptions (never unmasked), the stack
  (Audio Task 4,000 B: EEES 1,256, CFTE 1,764), the anomaly list rev F and loop/BTB rules, Elektron's code (no idle
  timer, no bypass, nothing else touches SpgSlot), a code path that runs only in silence (none), and interrupts and
  timing (digikit's coupled emulator, stock bit-exact: SX05 plays, EEES survives 90 s of silence, FX kill and 8.4 M
  forced interrupts in our code). **OS UPGRADE mutes stock too** (FX kill until the next kit load, sequencer halted)
  [inferred from the main OS]. Left: caches, the real pipeline, interrupts during hardware loops, anomaly revs H/J.
  Proposed: a diagnostic build with a tone per park point plus a heartbeat tick, or a rethink of the reverb.
- **Correction and a lead (owner, 9 Nov).** "the upgrade menu crash is not just the silence caused by entering the
  menu - going back out of the menu to SYN (for example) has no sound ever again either (until a reboot)". So the
  OS UPGRADE silence is most likely the spring's stop, not Elektron's FX-kill mute, which leaves the synths alone
  [inferred; the same menu on a 0.3.0 build settles it]. The timing lines up in sharcvm: the spring's output is
  exactly 0 from 1.4 s into FX kill (the menu forces DEC 0) and from 78 s after a stop
  (`out/spring-stop-investigation/coverage.md`) [verified in sharcvm]. Both match the unit's stops, so the stop
  probably comes when the spring goes fully silent [inferred]. SX05 doesn't fit: its floor kept the spring off
  exact zero, yet it was silent from power-on.
- **0.3.0 needs no change for this:** its eight builds have no spring (Elektron's reverb, 4th letter E); the six
  spring builds stayed out (R1.17 section 7). The spring is shelved; the owner proposed a 0.3.1 without it, which
  0.3.0 already is.
