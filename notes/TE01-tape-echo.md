# TE01: the tape echo back, every byte where our code ran on the unit

2026-10-29 (one builder; the DSP proofs by one helper agent, Opus, high: the whole mix's DSP code changes). The owner,
after BA05 on the unit: "tape goes into 0.2.0 for sure", then "run until we are ready to publicly release 0.2.0".
TE01 = BA05 (base 0.2, on the unit 2026-10-29) with the tape echo in the delay slot: the first echo build since SC01
stopped the DSP on the unit (echo knob turns, FDBK, at random when idle; its echo code partly in L2, where no code of
ours had run: notes/SC02-plan.md). TE01 is the 0.2.0 pack CFTE byte for byte but the name (scripts/pack_build.py).
Marks: **[verified]** = built, run in sharcvm or the emulator, file named; **[inferred]** = reasoned. The owner
flashed TE01 (section 4).

| | |
|---|---|
| **`out/TE01/TE01.syx`** | sha256 **`723ac5905601539ee74bf3e8e728fdef80e2eea776790c97c34adcdec72600ce`**, version TE01; `scripts/te01_build.py`, every check passes |
| main OS | BA05's twelve mods (sha256-pinned) + **echo-names-dn2 0.1** (fork): main OS `499e45e5...` |
| DSP | recipe `TE01` (`tools/dspsplice/build.py`): section 7 917,576 B, 104 blocks, `48993b82...` |
| brick checker | **ACCEPT, 12 PASS, 0 FAIL, 0 SKIP** (`out/brickcheck/TE01-2026-10-29/report.md`; DSP run 373,653 instructions per block: DC02 339,305, SC01 376,095, stock 320,281; peak 0.693) |

## 1. The DSP: DC02 and the echo, fitted [verified]

`fx/spaceecho.c` (SC02's FDBK, shared by the playing heads; DT01's struct order and diet) and `se_glue.c` (SE01's
one-page layout) in the delay slot. DC02's code keeps its places and bytes: the echo's two files are compiled with
DC02's (the diet: its five never-called functions dropped, its copies of the nine shared fxmath helpers removed; DC02's
peephole pass over it too), then split per function and linked after every other object. Recipe key `fit`: a first
link puts every echo function in a measuring region; each is then placed, largest first, in the region where it leaves
the least room (best fit), with each function's start aligned as the linker aligns it (4 bytes). The room is what DC02
leaves in the four proven regions (build.py `PROVEN`, where our code ran on the unit):

| region | DC02 ends | proven end | the echo's functions | bytes |
|---|---|---|---|---:|
| block 1 | 0x2e0c3a | 0x2e1c36 | spaceecho_init, se_crackle_plan, se_process | 4,076 |
| code4 | 0x2e49ae | 0x2e4f46 | spaceecho_snap | 1,388 |
| code3 | 0x30e3c6 | 0x30ff76 | the helpers, spaceecho_process, se_init, se_crackle_event and 13 more | 3,714 |
| code2 | 0x31048e (+ the delay's two stubs) | 0x315f8c | se_run, se_control, se_mix_targets, se_targets, se_filter_coefs, se_pan_gains | 22,518 |

The final image against DC02's: every differing byte is in those four tails or in the delay's two CJUMPs (12 ranges,
32,478 B; `te01_build.py`); DC02's four hooks patched the same. The stub of the delay's init does not run the stock
init (it would clear its rings over SeSlot, at the start of the stock delay's DDR state). Rebuilding DC02, SP02 and
SC01 with the extended build.py gives their images byte for byte.

## 2. The main OS: echo-names [verified]

fx-echo 0.2 (SC01, on the unit) also names the chorus slot Flanger, at the same 13 sites flanger-names (DJ01, BA05)
writes, so the two can't link together. echo-names 0.1 (`scripts/te01_make_echo.py`) is fx-echo 0.2 without those 13
sites and their strings: its 32 delay sites (each stock value checked again), its code and strings, and band_table.h
from `fx/spaceecho.c` (the same values). The delay page is named Echo as in SC01. The emulator
(`scripts/te01-emu.sh`, `te01-echo.script`, sheet `out/TE01/emu/te01-echo-sheet.png`): the Echo page with TIME, MODE
(1 2 3 12 23 13 123, the numbered box), WID, FDBK, CNTR and BW with the band picture, AGE on G showing its value while
it turns; the stock reverb page; the Flanger page with ECHO on its G.

## 3. The DSP in sharcvm [verified: `tools/dspsplice/verify_te01.py`, `soak_te01.py`; `out/TE01/verify/`, `out/TE01/soak/`]

- **(a) Against DC02, in lockstep, the delay slot silent** (no send, VOL 0, AGE 0): 53 cases, 138,600 blocks,
  8,870,400 output samples bit for bit; every block's flanger output, band gain reductions, reply pages (word 0
  aside) and delay slot output (0 in both); at the end no L1 word differs outside our code, the stack and reply word
  0. The silent echo costs about 18k instructions per block more than the stock delay (worst block DC02 362,962,
  TE01 381,979).
- **(e) The echo against SC01.** One head (X 0/1/2): 26 cases, 6,240,000 delay-slot samples bit for bit SC01's
  (verify_se's configurations, FDBK 32/64/100/127 per head, every frame word moving, the fuzz, held runaways). The law:
  `spaceecho_fb` = v/64 on 1,233 values; `se_mix_targets` live for all 21 MODE x PAN settings, the head gains SC01's x
  1, 0.5, 0.33333334 and every other coefficient SC01's. Two heads: with AGE 0, TE01 at FDBK word w is SC01 at w/2 bit
  for bit (11 cases, 2,688,000 samples; with AGE the hiss and crackle enter the loop without the share). Three heads,
  every MODE moving, against the Mac build (SE_NATIVE): 15 cases, 3,584,000 samples, AGE 0 bit-identical, AGE cases
  -111 to -128 dB; the chaotic held runaway (X 6, FDBK 127, ALT 127, BW 0, AGE 127) parts from the Mac by one ulp on
  a 9.3e-11 hiss sample at block 257 and the runaway amplifies it (-49.5 dB over the run; SC01's image against its own
  C does the same from the same block, -8.4 dB): rounding, not TE01; checked on its first 500 blocks (-110.3 dB).
  Runaways at FDBK 64..127: wet within 0.7, tape within 1.0, finite.
- **(w) The whole mix**, all three effects working, five cases of 3,000 blocks: finite, the master within 0.693, the
  echo's wet and tape bounded; every instruction run in the four regions inside a placed function. Instructions per
  block, mean / max:

  | case | TE01 | DC02 | SC01 |
  |---|---:|---:|---:|
  | typical | 369,025 / 371,909 | 351,701 / 354,621 | 371,127 / 374,016 |
  | busy, FDBK 64 | 382,623 / 386,005 | 350,492 / 353,118 | 385,126 / 388,445 |
  | runaway, FDBK 100 | 393,583 / 400,176 | 350,649 / 353,452 | 396,914 / 403,649 |
  | runaway, FDBK 127 | 383,984 / 388,985 | 351,738 / 353,494 | 387,154 / 392,564 |
  | worst (all knobs moving, noise) | 411,958 / 418,507 | 353,303 / 356,479 | 414,752 / 421,192 |

  The inferred load formula gives TE01's worst 82-83 % (it read CMP2 as ~74 % where the unit measured 50-60 %); the
  stack's high-water mark 1,932 bytes, as DC02's.
- **(s) Stores:** the delay call (12 blocks, 80,351 stores) only into the stack, the stub save area, SeSlot and its
  output buffers; the chorus and compressor calls only into their own; over whole blocks nothing into any code region
  of ours, the constant data or 0x315f8c-0x318000.
- **Soak:** the owner's SC01 crash recipes (12, scale 0.22): 1,837,500 blocks, about 20.4 minutes of audio, 0 faults,
  0 anomalies, no block over 600k instructions (worst 421,864; worst delay call 71,020); peaks master 0.693, wet 0.7,
  tape 1.0; no code run in a region's free tail.
- What no simulator can settle: SC01's soak was clean too and the unit stopped; the placement is the lead suspect.

## 4. On the unit (2026-10-29)

The owner flashed TE01: "te01 seems stable! been torturing it for 5min, turning every knob incl. the compressor
knobs, no crash!" The tape echo is in 0.2.0, and CFTE (TE01 byte for byte but the name) is marked played on a unit
(`tools/patcher/packs/CFTE.json`).

## Open

- None. Idle and in use, the owner: "stable for 15+min, made a whole patch around it actually using a lot of the
  devices stock and new custom functions. seems very very stable."
