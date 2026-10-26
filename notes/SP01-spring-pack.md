# SP01: the Spring pack (DJ01 with SPNG in the reverb slot)

**On the unit 2026-10-26: stable; the sound "not there yet, but promising"** (the tail cycles, and it is quiet even at
full send). Measured on the Mac: the three round
trips (644/756/894 samples at MODE 64, close to 1 : 7/6 : 7/5) line up every ~380 ms, and the wet level is 1.5-5.5 dB
under the stock reverb. SPNG v2 (notes/T5.5 section 9) fixes both on the Mac; the Spring pack waits for it.

2026-10-24 (one builder, no reviewer). Owner: "i want spng ready for release" (ROADMAP R1.9). SP01 = DJ01 with SPNG,
the spring reverb of notes/T5.5 (MODE as the springs' character only, the version of 2026-10-24), in place of the stock
reverb. Marks: **[verified]** = built, run in sharcvm or the emulator, file named; **[inferred]** = reasoned. Nothing
flashed, committed or pushed.

| | |
|---|---|
| **`out/SP01/SP01.syx`** | 2,435,104 B, sha256 **`859589f267a3e646983b2189cde310453cee10b77c776a0ceacaba3683482b11`**, version SP01; `scripts/sp01_build.py`, 40 of 40 checks |
| main OS | DJ01's nine mods (sha256-pinned) + **spring-names-dn2 0.1** (fork, uncommitted) |
| DSP | recipe `SP01` (`tools/dspsplice/build.py`): section 7 906,740 B, 105 blocks, `38d7453d...` |
| brick checker | **ACCEPT, 12 PASS, 0 FAIL, 0 SKIP** (`out/brickcheck/SP01-2026-10-24/report.md`, 3,220 s; DSP run 348,915 instructions per block (DT01 340,966), peak 0.693) |

## 1. The DSP [verified: `out/SP01/SP01-dsp/build.json`]

DT01's recipe plus the reverb's two CJUMPs (process `0x1c1d11`, init `0x1c1643`) on two stubs, as CMP4 hooks the chorus
and the compressor. The init stub does not run the stock init (it would set up its lines over our state; what it
writes besides its DDR state is its parameter struct, which the unpack rewrites every block, and the stack).

| part | where | bytes |
|---|---|---:|
| `fx/spring.c` (the diet: its 7 knob-law functions dropped, fxmath's helpers shared with the flanger) | code2 after flg_sync.c | 13,648 |
| `tools/dspsplice/src/spng_glue.c` (the knobs, k 48, the slot) | code2 | 1,716 |
| the reverb's two stubs | code2 | 768 |
| code2 in all: `0x310000-0x3143b8` (flg_sync.c 1,204 first, as DT01) | inside the proven `0x310000-0x315f8c` | 17,336 (7,124 left) |
| block 1, code3, code4, L1 data | DT01's bytes and places | 52,050 + 68 |
| SpgSlot (the glue and the spring's state) | DDR `0x80000018-0x8000a858`, the stock reverb's region (364,820 B) | 43,072 |
| spring.c's and the glue's constants | DDR `0x8000b000`, one load block (key `dmda_ddr`) | 164 |

Our DSP code: 69,386 B (DT01 53,254 + 16,132) of the proven 83,838. The constants are in DDR because brickcheck's shift
scan decodes every changed load block in an executable L1 range as code, and SP_AP's 0.76f, 0.58f read as a mis-encoded
shift there (a false positive on data, whatever the padding: the decode resyncs onto it); the stock stream loads seven
data blocks into DDR. `build.py` now runs that scan itself for this recipe.

**Against DT01** (`scripts/sp01_build.py`): the final images differ in 16 ranges, 16,273 B: code2 `0x3104b4-0x3143b8`,
the two CJUMPs (3 + 3 B) and the DDR constants (135 B). flg_sync.c, block 1, code3, code4, the L1 data, the fills and
DT01's four hooks are DT01's byte for byte.

**The knobs.** A PRE, B DEC, E HPF from their stock words; C MODE from FREQ's (`+0x08`), D DWEL from GAIN's, F TONE from
LPF's, each back on the frame's 1/256 grid; G WDTH = frame word `0xa28` (k 48) / 256, read from the frame image
(`0x25ceb4`). The bus is as before: the sends (tracks, input, chorus -> reverb k 30, delay -> reverb k 38) are the
spring's input, its wet output returns through VOL.

**k 48.** The stock render reads `0xa28` every block (`0x1c2e4a`) and keeps only (value != 0) as a byte at `0x2579d4`
(`0x1c2e63`). Nothing reads that byte [verified, `scripts/sp01_k48check.py`, `out/SP01/k48check.json`]: a read watch
over it saw no read in the effect init or in 2,250 blocks per image (stock section 7, DT01, SP01; in SP01 the glue reads
the image word, `0x189f9a`); the static code has no other reference; with k 48 = 0, 0x7f00, 0x6000 and 0/1 alternating
against 1 (what kits store), real voice frames and the reverb busy (wet peaks 0.7-1.8), the handler output and the
reverb's, delay's and chorus's outputs were identical every block, and the whole memory (L1, L2, DDR) identical at
the end but that byte, on the stock image and on DT01. So a kit saved on SP01 with WDTH 0 or 127 plays on DJ01 and the
stock OS exactly as with the stock 1.

## 2. The main OS: spring-names-dn2 0.1 [verified: `out/SP01/build-SP01.json`]

21 sites, checked against stock by `scripts/sp01_make_mod.py`, plus a tick: the page title "Spring"; C "Spring Mode"/MODE,
D "Dwell"/DWEL, F "Tone"/TONE (repointed, no stock string edited); DEC's value text the plain number (stock: value + 1
and "INF" at 127); row 0x83 "Width"/WDTH 0..127, plain number; new-kit defaults PRE 0, DEC 64, DWEL 64, HPF 0, TONE 127,
WDTH 96 (MODE 64 is the stock FREQ default); C and D drawn as knobs instead of the stock shelf picture (two entries of
FxPageView's row switch, and the shelf picture's page id moved to a page that does not exist). The tick (fx-echo's
method, which ran on the unit in SC01) puts row 0x83 on page 18's G after checking the stock tables, with the chorus
WDTH's picture. Against DJ01: 21 ranges (50 B) of the stock part, all in these sites; the nine mods' 290 relocated
fields name the same places; kit storage unchanged.

## 3. Checks

- **sharcvm** (`tools/dspsplice/verify_sp01.py`, `out/SP01/SP01-dsp/verify.json`, `verify-stores.json`):
  - **= the Mac**: 11 static settings, the reverb's input captured in SP01's whole image and rendered by fxrender
    (`fx/render.c`, `-fx spring -mix wet`): 1,056,000 of 1,056,000 samples bit for bit; knobs moving (ramps every
    block, MODE sweeps and jumps, random jumps of all seven, the soak's hands; the Mac build of the glue and spring.c
    fed the same parameter words): 768,000 of 768,000 samples, SpgSlot byte for byte at the end;
  - **= DJ01**: DT01 and SP01 in lockstep, the reverb's VOL at 0 (10,500 blocks, the soak's K1/F1/S1 hands with the
    reverb's knobs moving): the whole mix (the compressor's output), the flanger, the delay and both reply pages (but
    the cycle count and the reverb's own audio stream) every block bit for bit; L1 at the end identical outside our
    code but signed zeros (the returns times VOL 0) and that stream; DDR outside the reverb's region identical. VOL up:
    the flanger and the delay identical every block (7,500);
  - **the stock reverb does not run**: of its 1,663 instructions (DT01: its call and its init), SP01 runs none of its
    own 1,549; the 114 it shares with other stock code run as before; the reverb's call and the init's run only ours;
  - **stores**: the reverb call stores only into SpgSlot, the stack, the save area and its outputs (1.2 M stores); the
    effect init stores into the region only from spg_init;
  - **load**: the reverb call 14,566 instructions (stock) -> 22,509-22,582 static, 26,838-27,263 mean with knobs moving,
    28,332 at most. Worst block DJ01 358,198 -> SP01 371,653 (+13,455, **+2.0 %** of 666,667); static settings
    +8,916 worst, **+1.2 %** mean.
- **Soak** (`tools/dspsplice/soak_cmp4.py --rev --only K1,K2,K3,F1,S1,R1 --scale 0.25`, `out/SP01/soak/`): DT01's five
  recipes and R1 (the reverb's seven knobs, MODE and WDTH among them, moved every 0.1-1.5 s, half of the moves jumps),
  517,500 blocks (345 s of audio): **0 faults, 0 anomalies**; outputs finite under the master ceiling, the spring's
  wet finite (peak 0.14), SpgSlot intact at the end; no store into our code, `0x30ff76-0x318000` or (every fifth block)
  the spring's DDR state from anything but its own code; stack 1,932 B (DT01's). Instructions per block: most blocks
  360-370k (DT01 350-360k), at most 378,873 (DT01 364,483: +14,390, +2.2 %); the reverb call at most 29,126.
- **Emulator** (`scripts/sp01-emu.sh`: SP01.syx cold-booted in digikit to 800M, 0 faults; `out/SP01/emu/`,
  `out/SP01/screens/`): the page (`sp01-spring.script`, 22 screens) is "Spring (2/3)", PRE DEC MODE DWEL / VOL HPF TONE
  WDTH, C and D as knobs, G the width picture, H empty; each knob changes only its own k (41..46, 48; H nothing),
  headers "Spring Mode=65", "Width=11"; the live kit, the mirror the DSP gets and stored kit 0 follow every turn;
  snm_state 1 after one tick. Kits (`sp01-kit.script`): MODE 70, DWEL 53, TONE 91, WDTH 10 set on A01; A02 shows its
  own kit (its FREQ 107 as MODE 107); A01 again shows 70/53/91/10, and the kit serializer and loader keep k 41..48
  (`scripts/sp01_emucheck.py`). A new kit (the stock routine that fills an FX setup with the rows' defaults,
  `0x400e7140`, host-called): PRE 0, DEC 64, MODE 64, DWEL 64, HPF 0, TONE 127, VOL 110, WDTH 96 (DJ01: 16, 31, 64, 50,
  32, 96, 110, 1). Power-on screen SP01, TEST MODE "OS: SP01"; Overbridge's USB and RPC version replies `0062 1.12`
  (`out/SP01/emu-SP01-version.json`).
- **Rebuilds**: the 18 earlier DSP recipes (CG01 .. DT01, and DC01) give their section 7 and DSP .syx byte for byte;
  CMP4, OB01, FL01, FL02, DT01 and DJ01 rebuild byte for byte by their scripts; no fork mod moved (no elk_pin change).

## 4. Limits

- Unit only: the sound, the real load (DDR stalls), a power cycle. Overbridge's plugin still names C, D and F FREQ,
  GAIN and LPF and has no WDTH; its reverb audio track is the reply pages' stream 11, which carries the spring
  [inferred].
- WDTH has no MIDI CC (the stock row has none); MODE keeps CC 31, DWEL 89, TONE 91.
- On a very hot input (the k 48 check's noise into every send) the spring's wet peaked at 2.4 where the stock reverb's
  reached 1.8; it is bounded (three clipped rings, about 4.2) and the master limits the mix as before.
- docs/identity has no `spng` icon yet, so the patcher's pack (tools/patcher/packs/SP01.json) cannot be made until it
  does.

## 5. Files

New: `scripts/sp01_build.py`, `scripts/sp01_make_mod.py`, `scripts/sp01_k48check.py`, `scripts/sp01_emucheck.py`,
`scripts/sp01_live.py`, `scripts/sp01-emu.sh`, `scripts/sp01-spring.script`, `scripts/sp01-kit.script`,
`tools/dspsplice/src/spng_glue.c`, `tools/dspsplice/verify_sp01.py`, `tools/patcher/packs/SP01.json`, this note;
fork: `examples/spring-names-dn2/` (and its line in `examples/DIGITONE2-STATUS.md`). Changed: `tools/dspsplice/build.py` (recipe SP01; keys `c2_tail`, `stubs2`,
`dmda_ddr`, `ddr_slot`), `tools/dspsplice/sc01_funcmap.py` (the DDR data section), `tools/dspsplice/soak_cmp4.py`
(recipe R1, `--rev`). Outputs: `out/SP01/`, `out/brickcheck/SP01-2026-10-24/`.
