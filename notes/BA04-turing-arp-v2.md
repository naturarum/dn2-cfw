# BA04: base 0.2 with the Turing arp's second version (TM v2)

2026-10-28 (one builder, no reviewer: no storage format or DSP change). BA03's TM on the unit was not what the owner
meant (notes/BA03-turing-arp.md, "On the unit"). The owner's answers to the four questions (vault Turing arp.md):
several notes held, the others ignored (the lowest is the root); the track's KEYBOARD SETUP scale; LOOP CHANGE 100 fully
random, every note changed to another every time round; no offsets. The boot mark was right on the unit. BA04 = BA02
(on the unit since 2026-10-28) with arp-modes 0.3 (TM v2) and boot-mark 0.2 (BA03's). Marks: **[verified]** = run
(function harness, emulator, build check) or read in the bytes; **[inferred]** = reasoned. Nothing flashed; the guide
is `out/BA04/FLASH-BA04.md` (vault code/Digitone2/BA04/).

| | |
|---|---|
| build | `scripts/ba04_build.py` (BA03's, with arp-modes 0.3); every check passes, 31 of 31 (`out/BA04/build-BA04.json`); DC02's DSP section 7 byte for byte |
| sources | fork `examples/arp-modes-dn2/` (0.3), `examples/boot-mark-dn2/` (0.2, BA03's); generator `scripts/ba04_make_arp.py` (12 sites: BA03's 13 less the rest test's hook); BA03's arp-modes 0.2 frozen in `scripts/ba03/` (BA03 rebuilds byte for byte, `3f123216...`) |
| blob | 16,456 (BA03) -> 16,808 B |
| **`out/BA04/BA04.syx`** | sha256 **`75dcd637ec1c6c1b09aab774bce563b996bfda30102a3784f48241f4da4457dc`** |
| brick checker | **ACCEPT, 12 PASS, 0 FAIL, 0 SKIP** (`out/brickcheck/BA04-2026-10-28/report.md`, 2,829 s; DSP run 339,305 instructions per block as BA02 and BA03, peak 0.693) |

## 1. TM v2, the engine (arp-modes 0.3) [verified]

MODE 7..107 is TM with LOOP CHANGE = MODE - 7, as in BA03 (the clamps, FUNC + ARP's restore and the loader unchanged
from 0.2). Per track a loop of 16 values (0..255, random at first use, not saved). On an on step the step's value is
put on the pool: from the lowest note held up to below it + 12 x (RANGE + 1), and below 128, the notes of the track's
scale (KEYBOARD SETUP of the pattern playing, per track or per pattern as its CONFIG says, read as lfo-dejavu's KEY
reads it; Chromatic or no pattern: all twelve), lowest first: note k = (value x n) >> 8 of the n. Before it plays,
with the chance LOOP CHANGE / 100, the step takes another of the n notes (uniform over the other n - 1), its value
then the middle of that note's share; no other step changes. Rests (steps off) play nothing and change nothing; the
loop is indexed by the arp's own step, so 0.2's hook on the rest test is gone. The value scaled over the pool, not a
note number, so a new lowest note moves the melody in the scale and RANGE stretches it, as the module's SCALE knob does.
The note returns through the routine's end without the step's offset; the octave above the lowest note held goes to
the arp state's +0x1c, the note less those octaves to +0x0c.

Harness `scripts/ba04_arpcheck.py` (`out/BA04/arpcheck.json`), all six checks pass:
- TRUE, UP, DOWN, CYCL, SHUF, RAND, 108 and -1: BA04 = BA02 note for note, 80 random runs;
- 4,608 TM steps (96 runs, 3,559 on steps; LEN 1..16, masks, offsets, RANGE up to 9; no pattern, Chromatic, 35
  pattern scales and roots, per-track scales): every note pool[(value x n) >> 8] in the scale and range, only the step
  played changes, a change always to another note, the value the middle of its share; changes 0 of 576 at LOOP CHANGE
  0, 355 of 355 at 100, 574 of 1,152 at 50 (all rates within 4 standard deviations); the state's +0x0c and +0x1c;
- a new loop on first use: seeded, 4 of 4 loops distinct;
- LOOP CHANGE 0 repeats every LEN steps from the first step; 100 with LEN 1 (D Ionian from D): 3,001 notes, never
  the same twice in a row, spread evenly over the other six (chi-square 4.3, 6 degrees of freedom);
- other notes held don't matter; a new lowest note (50 -> 55) keeps the loop's values and moves the melody in the scale;
- no note held: -2 before TM (the stock's), the loop kept.

## 2. TM v2, the ARP menu

As 0.2 (MODE's name, knob A, knob E LOOP CHANGE, the step graph, the tick's redraw), with the graph's bars now each
step's note's place in the pool (or its value with no note held) and the redraw keyed to the loop's values, the
lowest note held, the scale, LEN, RANGE, MODE and the step playing. Emulator [verified], `scripts/ba04-ui.script` on
BA04's cold boot with the audio clock (`scripts/ba04-emu.sh run t63b_live scripts/ba04-ui.script --ssi0-hz --ips
150e6`; `out/BA04/emu/ba04-ui/`, sheet `out/BA04/emu/ba04-ui-sheet.png`; 11.8 s emulated, 17,613 audio blocks): MODE
up to TM, ARP LENGTH 8 (eight bars, the dotted end moved); one note held from MIDI in (C3): the arp runs in TM, the
playing step's mark moves screen to screen, the bars stay put at LOOP CHANGE 0; LOOP CHANGE up to 5 (every turn
registered): from then on single bars change between screens, one at a time; the note let go: the mark goes and the
bars show the loop's values.

## 3. The DSP budget for the patcher (scripts/patcher_budget.py) [verified]

All fifteen combinations of CMP3, FLNG, TAPE and SPNG measured as DC02's budget (selcc, the diet, the peephole in
mode ABs, first-fit over the four proven regions, 83,838 B), with SPNG v2 (SP02's): `tools/patcher/dsp-budget.json`.
Thirteen fit; CMP3 + TAPE + SPNG (85,714 B) and all four (99,330 B) don't. CMP3 + FLNG + SPNG is 66,972 B with v2
(62,754 with v1).

## On the unit (2026-10-28)

The owner: "new tm works exactly as expected!"
