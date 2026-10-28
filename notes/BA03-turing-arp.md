# BA03: base 0.2 with the Turing arp (TM), the boot mark the right way up

2026-10-28 (one builder, no reviewer: no storage format or DSP change). The owner: "turing arp: GO!", with the design
in the vault's Turing arp.md and its four answers as proposed (LOOP CHANGE 0 locked, 50 random, 100 the loop flipped;
the name TM; the offsets unused in TM; ARP LENGTH the loop's length). BA03 = BA02 (on the unit since 2026-10-28) with
arp-modes 0.2 and boot-mark 0.2. Marks: **[verified]** = run (function harness, emulator, build check) or read in the
bytes; **[inferred]** = reasoned. Nothing flashed; the guide is `out/BA03/FLASH-BA03.md` (vault code/Digitone2/BA03/).

| | |
|---|---|
| build | `scripts/ba03_build.py` (BA02's, with arp-modes 0.2 and boot-mark 0.2 from the fork); every check passes (`out/BA03/build-BA03.json`); DC02's DSP section 7 byte for byte |
| sources | fork `examples/arp-modes-dn2/` (0.2), `examples/boot-mark-dn2/` (0.2); generators `scripts/ba03_make_arp.py` (13 sites), `ba03_make_boot.py`; BA02's arp-modes 0.1 and boot-mark 0.1 frozen in `scripts/ba02/` (BA02 rebuilds byte for byte, `a3fefcaf...`) |
| blob | 12,784 -> 16,456 B |
| **`out/BA03/BA03.syx`** | sha256 **`3f1232165cfdc5ff11b15db2590564ccd3b6d7a36995970044cd966dde2cce92`** |
| brick checker | **ACCEPT, 12 PASS, 0 FAIL, 0 SKIP** (`out/brickcheck/BA03-2026-10-28/report.md`, 2,894 s; DSP run 339,305 instructions per block as BA02, peak 0.693) |

## 1. TM, the engine (arp-modes 0.2) [verified]

MODE 7..107 is TM, LOOP CHANGE = MODE - 7, so nothing new is stored (MODE's clamps in setMode, FUNC + ARP's restore and
the project loader go from 6 to 107; a stock OS or BA02 opens a TM kit with the arp OFF). Per track, a 16-bit shift
register moves one place at every step of the arp: rest steps too, through a hook on the step's rest test
(`0x4002a108`, the stock returns there before the mode dispatch). The bit leaving the loop (bit LEN - 1) comes back
in, flipped with the chance LOOP CHANGE / 100. On an on step the note is the low 8 bits scaled over the held notes
x the octaves of RANGE, lowest first, returned through the routine's end without the step's offset. Seeded at first
use; not saved (a new loop after power-on). Harness `scripts/ba03_arpcheck.py` (`out/BA03/arpcheck.json`):
- TRUE, UP, DOWN, CYCL, SHUF, RAND, 108 and -1: BA03 = BA02 note for note, 80 random runs;
- 2,400 TM steps (60 runs, LEN 1..16, masks, offsets, RANGE): the register's rule at every move, flips 0 of 780 at
  0, 378 of 780 at 50, 780 of 780 at 100; every note as computed; the note and octave in the arp state; rests -1;
- LOOP CHANGE 0 repeats every LEN steps (from the 8th step, once the 8-bit window holds loop bits only); 100: the low
  LEN bits inverted after LEN steps, back after 2 LEN; offsets never added; no note held: -2, the loop kept.

## 2. TM, the ARP menu (arp-modes 0.2)

MODE's eighth name (CHRD, `0x401d4f38`, which no stock menu reaches) is TM; the menu's draw (`0x400188de`) asks for
the name of 7 in TM. Knob A (`0x40018eee`): OFF .. RAND, TM, holding at TM; back into TM with the track's last LOOP
CHANGE. Knob E (`0x400190ae`) in TM: LOOP CHANGE 0..100. The draw (vtable slot 4): the stock menu, then in TM "LOOP
CHANGE" and its value in knob E's column, and the step graph as bars, one per step of ARP LENGTH: the note's place among
those held (or the loop's own value with none held), a flat line for a step that is off, the playing step marked over
the row of squares, LEN's dotted line as stock. The menu's tick (slot 11) redraws when the loop has moved. The screen's
y runs up (section 3). Emulator [verified], `scripts/ba03-ui.script` on BA03's cold boot (`out/BA03/emu/`, sheet
`ba03-ui-sheet.png`): MODE OFF .. RAND, TM, held at TM; LOOP CHANGE 0 -> 3 -> 0, held at 0; ARP LENGTH 8: eight bars,
the dotted end moved; with the audio clock on (`--ssi0-hz --ips 150e6`) and notes held from MIDI in, the arp runs in
TM: the bars take the held notes' places and the playing step's mark moves screen to screen (the tick's redraw); A
down to RAND and up brings back LOOP CHANGE 4; FUNC + ARP off and on brings back TM with it. The first build drew the
lowest note's bar 1 px, like a rest: rebuilt with bars of 3 px at least and a rest as one dot. Knob turns made late in
a long run with the audio clock (past about 11 s emulated) are lost, on BA02's stock menu too (`out/BA03/emu/ctl/`:
RAND's offset and MODE unchanged), so an emulator limit; earlier, TM's knobs act with the arp running (LOOP CHANGE 4
read back from the snapshot: MODE 11).

## 3. The boot mark the right way up (boot-mark 0.2) [verified]

On the unit BA02's mark was upside down (the owner). The panel's rows run up: page 0 is the bottom eight rows, so
setPixel's y = 0 is the bottom row (digikit's `emu/panel.py`; the menus draw their titles at y 58 and their knob hints
at y 0). 0.2 writes the picture's row r at y = 63 - r. `ba02_bootmark.py` now models 0.2 and renders as the panel shows
(`out/BA03/bootmark/`; vault code/Digitone2/BA02/Boot mark B, BA03 (right way up)): DIGI over WERK upright until
frame 65, then the tunnel. Emulator [verified]: in BA03's cold boot the intro's source holds the picture with row r at
y = 63 - r, all 2,277 pixels.

## Open

- Unit only: how TM sounds and feels; the graph's motion at speed.

## On the unit (2026-10-28)

TM is not what the owner meant: a change alters several notes, and one held note can't make a melody. The next TM
(v2, a loop of notes from the held note in the track's scale, a change replacing one note) is in the vault's Turing
arp.md, with questions. BA03 not committed.
