# SC01 soak: minutes of audio on the owner's crash recipes (2026-10-21)

The owner's SC01 report (2026-10-21): the DSP stops (no sound, the UI and the sequencer run on, as the CMP2 stop) when
an Echo knob is turned, notably CNTR/BW, once while touching FDBK, and at random while the unit is left alone. SC01's
proofs (notes/SC01.md 3) ran 2 s per case (3,000 blocks), too short for anything rare. This soak runs SC01's built DSP
image for minutes per recipe, to tell whether our code itself (the tape echo, the compressor, the glue) can stop,
trap, loop or blow up, independent of where it sits in memory.

## 1. Method

`tools/dspsplice/soak_sc01.py` (`bench`, `selftest`, `run`), on `out/SC01/SC01-dsp/section7.bin` (sha256 `788bbef3...`,
the image on the unit), started from the effect-init snapshot verify_sc01.py uses. The SpaceEcho offsets come from the
header SC01 was built from (copied to `out/SC01/soak/src/`, sha256 checked against build.json) [verified].

- **Knobs as on the unit**: the echo's frame words move as the ColdFire sends a turned encoder (3 % of the way per
  frame, verify_cmp2.glide); MODE steps; AGE through frame word 0xa18; the band knobs and MIX through the frame tail
  (magic, sequence, checksum); the tempo word for tempo changes. The "hand" holds, walks, sweeps or spins a knob.
- **Per block**: the whole handler under a cap of 3 M instructions (a block over it is a runaway: where it runs is
  sampled, then up to 10^8 more instructions to see whether it returns); every sharcvm trap (unmapped read or write,
  PC/loop/status stack overflow, an undecodable instruction); the instructions of the block and of the delay call; the
  output and the echo's wet finite and under 0.6931 / 0.7; SeSlot's magic and block counter; every float of the
  SpaceEcho state finite; the tape's write index, each head's read index, fraction and delay in range; MODE and PAN
  in range; the crackle event positions in 0..32; the 32 frames written to the tape finite and within +-1; the
  compressor's guard counters (trips, re-inits) and gain reductions; the hardware stacks back at their depth; one of
  four code ranges watched for stores in turn (L1 block 1 code, code4, L1 block 2 code with 0x315f8c-0x318000, L2
  A..D). Every 100 blocks the C stack's high-water mark; every second the entries of each L2 and code4 function (the
  profile's hit count of its first instruction) and the instructions run in L2; every minute a scan of the whole tape.
- **Self-test** (`selftest`, `out/SC01/soak/selftest.json`) [verified]: a NaN written into head 1's delay is caught
  (echo_head, echo_state_nan_inf); SeSlot's magic cleared makes se_process re-run se_init, whose spaceecho_init
  (code4, level 3) clears the 4 MB tape in one block: caught as a runaway of 17,944,751 instructions that returns. A
  head's read index written past the tape is not caught, because se_control recomputes every read index from the
  head's delay each block before the check reads it (the check reads the state the next block starts from).

## 2. Speed and size

sharcvm runs SC01's whole handler at 171-187 blocks/s per process (profile on or off: the same within 2 %), 158-174
blocks/s with every check (`out/SC01/soak/bench.json`, the logs) [verified]: about a tenth of real time. Three workers
(two more for R1e and R2c at the end), memguard at 3 GB per worker (peak RSS 219-226 MB), tools/memwatch.py running.
**74.4 minutes of audio, 6,696,000 blocks, in 3 h 52 min** (`out/SC01/soak/summary.json`, `summary-R1e-R2c.json`)
[verified].

## 3. Results [verified: out/SC01/soak/R*.json]

**No fault and no anomaly in any recipe**: no trap, no runaway block, no NaN or inf anywhere checked, no index out of
range, SeSlot's magic and block counter right in every block, no guard trip (trips and re-inits 0; mb_restart, se_init,
spaceecho_init, mbcomp_init and mb_clear never entered after the start), the hardware stacks at their depth after every
block, no store into any of our code ranges or 0x315f8c-0x318000 (each range watched in a quarter of the blocks, about
1.67 M blocks each), nothing executed in the L2 ranges outside our functions, the whole tape finite and within +-1 at
every minute.

| recipe | what | audio s | max instr. / block (est. load) | mean | delay call max | L2 instr. / s | L2 entries / s (main ones) |
|---|---|---:|---:|---:|---:|---:|---|
| R1a | idle, MODE 7, FDBK 32, AGE 64, RND 127 | 480 | 415,206 (82.6 %) | 401k | 64,561 | 1,176,971 | se_mix_targets 1,041, se_pan_gains 3,122, se_crackle_event 1.4 |
| R1b | idle, MODE 4, FDBK 64, AGE 127, ALT 127 | 480 | 395,841 (78.8 %) | 385k | 40,092 | 3,682 | se_crackle_event 5.8 |
| R1c | idle, MODE 7, FDBK 64, AGE 127, RND 127 | 384 | 415,195 (82.6 %) | 402k | 65,038 | 1,178,504 | se_mix_targets 1,040, se_pan_gains 3,119, se_crackle_event 5.8 |
| R1d | idle, MODE 4, FDBK 32, AGE 64, ALT 127 | 384 | 395,841 (78.8 %) | 384k | 39,559 | 926 | se_crackle_event 1.4 |
| R1e | idle at the guide's start (MODE 1, WID 0, FDBK 32, BW 127, AGE 1) | 240 | 381,645 (76.1 %) | 365k | 24,266 | 361 | se_crackle_event 10.0 |
| R2a | CNTR/BW by hand, MODE 4/7, FDBK 32-127, AGE 64 | 336 | 417,009 (83.0 %) | 391k | 67,081 | 2,853,435 | se_filter_coefs 1,352, se_mix_targets 840 |
| R2b | as R2a, BW 0-60 (resonant) | 288 | 416,250 (82.8 %) | 391k | 67,241 | 2,808,245 | se_filter_coefs 1,342 |
| R2c | CNTR/BW by hand from the guide's start | 240 | 386,374 (77.0 %) | 374k | 30,468 | 2,022,600 | se_filter_coefs 1,364, fx_clampf 23,788 |
| R3a | FDBK swept 0-127-0, MODE 4, AGE 0 then 64 | 288 | 385,512 (76.8 %) | 376k | 35,017 | 618,225 | fx_clampf 17,986, fx_knob01 5,994, fx_exp2 1,500 |
| R4a | every echo, band and MIX knob by hand, MUP mangles, MODE steps, tempo, noise | 576 | 423,227 (84.2 %) | 403k | 71,829 | 3,622,779 | mbcomp_set 4,278, se_filter_coefs 1,200, se_mix_targets 1,202 |
| R4b | as R4a, other seed | 480 | 423,989 (84.3 %) | 403k | 71,538 | 3,664,242 | mbcomp_set 4,267, se_filter_coefs 1,192 |
| R5a | TIME by hand with tempo changes | 288 | 395,100 (78.7 %) | 385k | 44,267 | 239,991 | spaceecho_set_bpm 165, fx_clampf 6,853 |

Peaks: the output at the master ceiling 0.693 in every recipe; the echo's wet at its 0.700 ceiling in every recipe with
two or three heads (the runaways the owner heard in MODE 4 hold there); the tape at most 1.000. C stack 1,764 B (1,932 B
with the band knobs moving, as notes/SC01.md 3 (w)). The est. load is notes/SC01.md 4's calibration, which read about
74 % where the unit measured 50-60 % on CP2D [inferred].

**How often the suspect L2 code runs** (the profile's entry counts; one block = 1/1,500 s):

- **Any Echo knob turned runs L2 A every block while its word glides**: spaceecho_set calls the echo's own fx_clampf,
  se_targets its fx_knob01 and fx_exp2 (all in L2 A). FDBK alone: 618k L2 instructions per second (R3a); CNTR/BW:
  se_filter_coefs about every block, 2.0-2.9 M L2 instructions per second (R2).
- **Idle with WID right of centre (RND)**: L2 A runs in about two of three blocks with nothing touched (se_mix_targets
  1,040/s, se_pan_gains 3,120/s, 1.18 M L2 instructions/s), because the random head positions glide.
- **Idle with WID at or left of centre**: only se_crackle_event (L2 B) with its helpers in L2 A: 1.4/s at AGE 64,
  5.8/s at AGE 127, and **10/s at AGE 0 or 1** (with no crackle it returns at once and asks again 4,800 samples
  later): 361-3,682 L2 instructions per second. So no setting keeps SC01 out of L2 entirely.
- **Band knobs**: mbcomp_set (L2 D) about 4,270/s while they move (R4).
- **Level 3** (code4 above 0x2e4f46): only mbcomp_snap, once, in the first block; nothing during play.

## 4. Verdict

Over 74 minutes of audio on the owner's situations, SC01's code does nothing wrong in the simulator: no stop, trap,
loop, overrun past 424k instructions, NaN, bad index, stray store or guard trip [verified]. A fault of the code's logic
(or a selcc miscompile that sharcvm executes as the chip would) is therefore unlikely to explain the stops; what the
simulator cannot see is how the chip runs that code where it sits (the I-cache path to L2, memory timing, the tape's DDR
reads) [inferred]. The entry counts fit the layout theory: every situation in which the owner saw a stop runs L2 code
often (a turned Echo knob: every block; idle with RND: two blocks in three; idle otherwise: a crackle check 1.4-10
times a second), and the rate follows how fast the stops came (fast when turning CNTR/BW, minutes when idle) [inferred].
For the owner's check A (AGE 0, WID 0, the Echo page untouched): L2 still runs, but only se_crackle_event's early
return, 10 times a second, about 5,000 times less L2 work than turning CNTR/BW.

## 5. Files

`tools/dspsplice/soak_sc01.py`; `out/SC01/soak/` (R1a..R5a .json and .log, summary.json, summary-R1e-R2c.json,
bench.json, selftest.json, src/ with the header copies and offsets.json, cmp_offsets/); this note.
