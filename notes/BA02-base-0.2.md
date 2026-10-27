# BA02: base 0.2, the test build (DC02 + four light features)

2026-10-27 (one builder, no reviewer: no storage format or DSP change). The owner picked, in the vault's Base ideas.md:
the LFO ONE/HALF fix (1), Déjà vu in the track's key (3), the arp's SHUF and RAND (4, with a Turing mode to design),
the arp on MIDI tracks (6, later, without CC p-locks first), readable values (8, per pack, later), and a Digiwerk boot
mark. BA02 is DC02 (on the unit since 2026-10-26: "seems stable and sounds identical") with the first four of those
that need no more design: the LFO fix, KEY, SHUF and RAND, the boot mark. Marks: **[verified]** = run (function
harness, emulator, build check) or read in the bytes, evidence named; **[inferred]** = reasoned. Nothing flashed.

| | |
|---|---|
| build | `scripts/ba02_build.py`: DJ01's eight other mods (sha256-pinned) in DJ01's order, **lfo-dejavu 0.2** in 0.1's place, then **arp-modes 0.1** and **boot-mark 0.1**; DC02's DSP section 7 byte for byte; 31 of 31 checks (`out/BA02/build-BA02.json`) |
| sources | fork `examples/lfo-dejavu-dn2/` (0.2), `examples/arp-modes-dn2/`, `examples/boot-mark-dn2/`; generators `scripts/ba02_make_dv.py`, `ba02_make_arp.py`, `ba02_make_boot.py`; lfo-dejavu 0.1 frozen in `scripts/dj01/lfo-dejavu-dn2/` (DJ01 rebuilds byte for byte, `7edf7c59...`) |
| blob | 9,512 -> 12,784 B (DJ01's appended mods + the three) |
| **`out/BA02/BA02.syx`** | sha256 **`a3fefcaff4601ed1f7aa7ba03107ffd0d42e61d6ba9aff24587014afffe61ac3`** (mark B) |
| brick checker | **ACCEPT, 12 PASS, 0 FAIL, 0 SKIP** (`out/brickcheck/BA02-2026-10-27/report.md`, 2,732 s; DSP run 339,305 instructions per block as DC02, peak 0.693) |

## 1. LFO ONE/HALF from the start phase, then hold (lfo-dejavu 0.2) [verified]

Stock stops ONE at the cycle's end and HALF at its middle, fixed points, and at the stop jumps to a fixed value per
waveform: with SPH past 0 a run is short (or, for HALF, a whole cycle). (Research findings of dn2_firmware_explore on
1.11; 1.12's evaluators A `0x40137a06` and B `0x401376bc` are the same code, 0x2e0 later, read here.) Ours moves the
phase's origin: at a trig in ONE/HALF on a stock wave (0..5) the phase starts at 0 and SPH's cell is latched; the
waveform and the page's phase copy read phase + start point (the trig's own arithmetic: cell x cycle / 2^15, rounded
down), the cycle wrapped as unsigned (the overflow that broke dnfw's first build on their unit); the stock stop tests
then fall one or half a cycle after the trig, either direction. A latch left over in another mode or wave is folded
into the phase once. The four stores of the stop values are nops, so the LFO holds its last value.

- **The latch:** A, the record's +0x26 word (stock never writes it; Déjà vu keeps its read position there with bit
  15 clear), 0x8000 | the cell; a TRIG/RAND trig drops only a latch (bit 15), never Déjà vu's position. B (MIDI
  tracks, per track; its +0x26 is its hold flag): `lfx_lat_b` in our .bss. Zero after power-on (the DDR clear
  `0x402fd000-0x466b84d0`, T1.3; the .bss).
- **Sites (10):** A `0x40137c40` jmp, `0x40137ccc` jsr, `0x40137dbc` jsr, nops at `0x40137bcc`, `0x40137c00`; B
  `0x40137836` jmp, `0x401378d8` jsr + 2 nops, `0x401379d8` jsr, nops at `0x401377c4`, `0x401377fe`. Each checked
  against the stock bytes and for branches into its middle (`ba02_make_dv.py`).
- **The harness** (`scripts/ba02_lfocheck.py`): digikit's Unicorn halves the EMAC's fractional products, so every
  `macl`/`movclr.l` of the LFO code is done by a hook as the chip does it (Q31, 48-bit accumulator, truncation);
  checked first: 0.5 x 0.5 = 0x20000000, SPH 64 starts half a cycle in, SPH 32 a quarter (A and B).
  - DJ01 = BA02 in FREE, TRIG, HOLD and on every Déjà vu wave: every row word, the state block, the generator, every
    frame (A 1,500 frames x 48 LFOs, B 1,500 calls; both images' Déjà vu and generator state set alike first, since
    the other checks leave BA02's used);
  - ONE and HALF, TRI..RAMP, SPH 0..127 (11 values), five speeds up and down: until the stop, the waveform, the output
    and the page's phase the TRIG reference's, frame for frame; the stop where the stock tests put it from phase 0;
    then held: **1,980 of 1,980 cases on A, 1,980 of 1,980 on B** (FADE off, in and out; `out/BA02/lfocheck.json`);
  - retrigs mid-run and after the stop, switches to FREE/TRIG/HOLD mid-run (the latch folded, then TRIG's waveform);
    SPH 0 as DJ01 until the stop then held; RAND as DJ01 throughout (its slew writes the output in the stop's frame
    too, so stock's stop value never shows there); a random sweep of everything: latches and phases always valid;
  - run lengths, SPH 0/32/64/96/127, at 47.6 frames a cycle: DJ01 ONE 48/36/24/12/48, HALF 24/12/48/36/25; **BA02
    ONE 48 at every SPH, HALF 24** (B: 42/32/21/11/43 and 21/11/42/32/22 -> 42 and 21).

## 2. KEY, the Déjà vu wave in the track's own key (lfo-dejavu 0.2) [verified]

WAVE 16, "KEY": the DV-F loop; on a machine's tuning row the result is rounded so that the voice's note plus the
tuning is the nearest note of the track's KEYBOARD SETUP scale (Chromatic: semitones); elsewhere DV-F; on MIDI tracks
DV-F (as the other scale waves). Read on 1.12: the playing pattern's copy `*0x42c5b9a0` (the sequencer's; 1.11's
`0x42c5a9a0`), a track's part at + 0x6a5 t with SCALE +0x492, ROOT +0x494, the pattern's at + 0x1dd40 + 0x1d / + 0x1f,
bit 2 of + 0x1dd40 + 0x21 = per track (the stock getters `0x4003bdb2`, `0x4003bce2`, `0x4003ac0c`); a scale is -1
(Chromatic) or a row of the stock table `0x4020e358` (35 rows of 8 notes, -1 after the last); the voice's note is
`0x8000dda0 + 4 v` >> 16, transposes included (`0x40026a44`). WAVE's maximum 15 -> 16 (three rows, four clamps).
Harness check 7: the playing pattern written (the pattern's scale and root in seven settings, then per track, every
track another), the voices' notes differing: **23,040 results on every machine's tuning rows, each the scale's note
nearest to note + DV-F's unrounded result (20,128 moved by it); 38,400 on other DESTs DV-F's word for word**; four
scales of the table against ones written by hand.

## 3. The arp's SHUF and RAND (arp-modes 0.1) [verified]

Read on 1.12: the step `0x4002a0bc` dispatches TRUE, UP, DOWN and plays CYCL for anything else (`0x4002a114`); MODE is
clamped to 0..4 by setMode (`0x4004befa`, `0x4004bf00`), FUNC + ARP's restore (`0x4004bfb4`, `0x4004bfc6`) and the
project loader (`0x400dd694`: above 4 becomes 0, OFF). Ours: the dispatch after UP (`0x4002a13c`, 12 bytes) -> DOWN as
stock, 5 SHUF and 6 RAND ours, anything else CYCL; the five clamps -> 6. SHUF plays every held note in every octave of
RANGE once per cycle in a new random order, a cycle not starting on the note the one before ended on (over 64
notes x octaves: RAND); RAND a random note and octave each step; both return through the stock tail (`0x4002a376`:
+ 12 x octave + the step's offset). A stock OS loads SHUF/RAND kits with the arp OFF. Harness (`scripts/ba02_arpcheck.py`,
the step called as the interrupt calls it, digikit's ff1 emulation): TRUE, UP, DOWN, CYCL, 7, -1 = DJ01's over 60
random runs; SHUF every cycle a permutation, orders differing, no repeat at the seam, a new arp a new order; RAND over
4,000 steps chi-square 11.5 (7 dof), nothing outside the set; steps off rest without using a note, offsets added (SHUF,
RAND, CYCL); DJ01's SHUF and RAND are CYCL note for note.

## 4. DIGIWERK in the start-up animation (boot-mark 0.1) [verified]

The intro `FUN_400d39ea` (1.11's structure from dn2_firmware_explore, 0x1000 later here): object `0x42c46678` (+0
scroll, +4 the 128 x 64 source Bitmap, +0x20 a 16,384-entry offset table); frame 0 clears the source and blits
Elektron's logo (`0x44647008`) at (48, 20) (`0x400d3ac4`); frames 1..4 copy it; from 5 the panel samples the source
through the table. Ours: that jsr -> `bm_draw`, which sets the mark's pixels with the stock setPixel. The static screen
before (logo and version) is untouched. `scripts/ba02_bootmark.py` runs the stock intro offline on DJ01's image with
three candidate marks (our own pixel letters): 200 frames each, still until about frame 60, then the tunnel, blank
from 169 (`out/BA02/bootmark/`, vault code/Digitone2/BA02/). **The owner picked B** (DIGI over WERK, 2,277 pixels):
`ba02_make_boot.py`'s default. Against the build with mark A, the main OS images differ in 650 bytes, all inside
bm_mark's stored 1,024 (the link map identical), so every result of mark A's build holds for B's code.

## 5. The emulator [verified]

`scripts/ba02-emu.sh` (SP02's, for BA02): BA02.syx cold-booted in digikit to 400M (17.9 min; 8 tasks, as SP02's), then
to 800M with the screen captured every 2M from 402M (`out/BA02/emu/`): the main screen (project LIGHTHOUSE) from 402M
on; the intro ran inside the cold boot. **The intro's source picture in the 800M snapshot is mark A, pixel for pixel
(1,012 lit)**: bm_draw ran in the real boot and the boot went on. `scripts/ba02-ui.script` (t63b_live, the drawn
texts): the LFO page's WAVE steps SQR .. RAND, DV-F, DV-T, PMAJ .. OCTA, **KEY**, and holds at KEY ("Waveform=KEY");
the ARP menu's MODE steps OFF TRUE UP DOWN CYCL **SHUF RAND** and holds at RAND, one detent a step. **The mark-B
build** (`a3fefcaf...`) cold-booted the same way (`out/BA02/emu-B/`: 400M, 8 tasks; 800M, the main screen): its intro's
source picture is mark B, pixel for pixel (2,277 lit).

## Open

- The Turing mode (proposal in the vault's Turing arp.md, questions to the owner): a later build.
- The flashing guide: `out/BA02/FLASH-BA02.md` (vault code/Digitone2/BA02/). Not flashed yet.
- Unit only: everything sounds as described; KEY's units on FM Tone PITCH and WaveTone MAST (semitones [inferred],
  as DJ01's scale waves).
