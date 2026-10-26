# SC02 plan: the merged line with every byte of our DSP code where our code has already run

Date: 2026-10-21. Firmware: Digitone II OS 1.12. A plan, not a build: nothing built for the unit, flashed or
committed. SC01 (notes/SC01.md) stops the DSP on the unit when echo knobs move and at random when idle; its new
places (four L2 ranges A-D over dead stock FX code, "level 3" above `0x2e4f46`, the 138 B gap `0x30ff76-0x310000`)
are the prime suspect, since the Echo's knob code (A), crackle (B) and RND jumps (A) run there. SC02's rule: **our
code only where our code has executed on the unit**: no L2, no level 3, not the gap, nothing in `0x315f8c-0x318000`.
Marks: **[verified]** = run or read in the bytes, file named; **[inferred]** = reasoned, not run. Scratch work:
the session scratchpad (`sc02/`: `sizes.py`, `pack.py`, `reach.py`, `diet_check.py`, the variant sources `x1..x7`, `d1`).

## 1. The proven map [verified: each build's `build.json` layout rows; static reachability from the stubs, `reach.py`]

Builds whose DSP ran on the unit: FLG1, FTD1 (also under FTN1, GT00, GT01, DH01), CMP0, CMP1, CMP2, CP2D, CP2E,
CP2F, CMP3 (README "Where things stand").

| range | bytes | our code that executed there | never reached in the last build there |
|---|---:|---|---|
| `0x2dd600-0x2e1c36` (L1 block 1) | 17,974 | the four FX stubs (1,532), the flanger (14,808), flg_glue (1,634): FLG1 onwards | five flanger API functions, 708 B (`flanger_man_ms`, `_damp_hz`, `_rate_hz`, `_delay_at`, `_set_bpm`) |
| `0x2e2900-0x2e4f46` (block 1, code4) | 9,798 | the compressor glue and tail reader: CMP2E (to `0x2e3cce`), CP2F (`0x2e49c2`), CMP3 (to the end) | none unreachable (`cmp_knob`, `cmp_seed`: fallback and old-kit paths) |
| `0x308400-0x30ff76` (block 2, code3) | 31,606 | the compressor, CMP0 onwards; CP2F to `0x30ff76` (its two stubs, then its clock read) | in CMP3 `mbcomp_ratio`, `mbcomp_gr_db`, 366 B (other builds had other code there) |
| `0x310000-0x315f8c` (block 2, code2) | 24,460 | the tape delay, FTD1 to CMP3 (the same bytes); `td_process` runs every block up to the end | `tapedelay_lpf_q`, 106 B |
| **total** | **83,838** | | about 1.2 KB of never-reached functions inside |

Not proven for code: the gap `0x30ff76-0x310000` (138 B, CP2F's watermark), `0x2e4f46-0x2e6000` (level 3, CP2F's
watermark page), every L2 range (only stock code ran there), every data slot (FlgSlot, CmpSlot, CmpTail, SeSlot),
`0x315f8c-0x318000` (CMP2/CP2D/CP2E code there stopped the DSP).

## 2. The demand [verified: `sizes.py`, SC01's recipe with SC02's `fx/spaceecho.c`]

SC02's functions (SC01 plus the FDBK share, section 5: `se_mix_targets` +118 B) are 69,474 B: the echo and se_glue
38,730, the compressor (CMP3's text) 30,744; plus the glue and tail reader 9,444 (code4) and the stubs (block 1).
Room for them under the rule: code4's 354 free + block 2's 56,066 = 56,420 B. **Short by 13,054 B.** With the hot
code (every per-sample and every-block function, 48,822 B) kept in block 2 as in SC01, the bins pack to the byte
(`pack.py`).

## 3. Making room, each idea measured

selcc has no size option: `-O0`, `-O1`, `-Os` and `-Oa` give the same assembly (`opts.optimize` is parsed and never
read) [verified]. Its code is large because every struct field access is four or five instructions (base from the
frame, a 32-bit offset, the add, `I4`, the access) and every local lives in the frame; a global struct compiles the
same way [verified, `g.c`]. Savings, cumulative, sizes from the build's function table:

| idea | saves | how measured | sound |
|---|---:|---|---|
| one copy of the fxmath helpers: the echo's 13 copies and the compressor's 9 call the flanger's (textually identical after label renaming; `fx_svf_coef` stays the compressor's) | 3,454 | function table; text hashes compared | identical (the same code) |
| the tape and the history at the end of `SpaceEcho` (small field offsets) | 736 | built | identical |
| `se_run`: the three head reads in one loop | 1,316 | built | identical; **costs 11,584 instructions per block (+3.1 %)**, so left out below |
| `SE_GLIDE` a function, not a macro (`se_control` 9,508 -> 8,538, + 116) | 854 | built | identical |
| the flanger's five never-called API functions dropped | 708 | function table, reachability | identical |
| **subtotal without the head loop** | **5,752** | | the loop would add 1,316 more |
| crackle removed (a feature: AGE loses its ticks) | 2,374 | se_run block built (748) + function table | changes AGE |

Tried and dropped: the head read as a called function (`se_run` -794, the function +1,012: net +218 B); the tape
glide inside the head loop (no gain). **The diet's three code changes give the same output**: on the Mac, all 47
TAPE renders bit for bit [verified]; in sharcvm against SC01's image, section 6.

After the subtotal 7,302 B are still missing (5,986 B with the head loop, 4,928 B with crackle gone instead). They would need a rewrite of the echo's
control code for selcc (values kept in locals: `se_control` 8.5 KB, `spaceecho_init` + `_snap` + `se_init` 3.8 KB,
`se_mix_targets` 2.4 KB, `se_process` 1.9 KB, `se_targets` 1.1 KB) or hand-written assembly for `se_run` (11.2 KB);
neither is measured, so **a fit with all three effects is not shown** [inferred: plausible, not certain].

## 4. Options

1. **SC02-F: SC01 without the flanger** (the stock chorus back in the chorus slot). Its stubs, flanger.c and
   flg_glue.c free 17,204 B of block 1, where the flanger has run since FLG1. SC02's 69,474 B then pack with every
   hot function in block 2 and **4,150 B to spare** [verified, `pack.py`]. The echo and the compressor stay SC01's
   text (only the FDBK share changes), so SC02-F is also the clean test of the layout: same code, proven places
   only. Main OS: SC01's minus fx-rename's flanger names (the chorus page and the sends read stock again); the
   storage and the band pages unchanged. Cost: no flanger in this line (it stays in CMP3).
2. **All three effects**: the diet (section 3, 5.8 KB measured at no cost) plus about 7.3 KB from a rewrite not
   yet measured, or the crackle cut and about 4.9 KB. More new code, each piece checked sample for sample against SC01.
3. **A probe build first**: CMP3 (on the unit, stable) with small compiled test functions in each of SC01's new
   places (L2 A-D, level 3, the gap), run every block with known results, heartbeat and "last place entered" on the
   bars. It says which places our code can use; if all pass for an hour, SC01's stops come from something else (the
   echo's own code, which has never run on the unit, or the load), and no layout change would fix them.

**Recommendation** [inferred]: option 1 as SC02, for the owner to choose against the flanger. If SC02-F plays
without stops where SC01 stopped, the layout was the cause; then option 2 or 3 brings the flanger back. If SC02-F
stops too, the cause is in the echo itself, and the next step is a diagnostic build, not a layout.

## 5. The sound change: FDBK shared by the playing heads (done in `fx/`)

The owner 2026-10-21: in MODE 4 (1+2) the echo ran away at about FDBK 32, "weird". `se_mix_targets` now scales each
head's gain into the feedback by 1, 1/2 or 1/3 (one, two, three heads), so the loop gain is FDBK/64 in every MODE:
64 is endless everywhere, 32 halves per pass. MODE 1, 2, 3 are unchanged bit for bit (gain x 1.0). Heads still add
up in the output (the wet level of a multi-head mode is unchanged).

- SC01's sources as built are frozen in `tools/dspsplice/src/sc01/` (spaceecho.c, .h; sha256 `7270c83a`,
  `e4ed0def`), first on SC01's include path; `build.py SC01 --outdir` rebuilds section 7 `788bbef3...` and the
  .syx `97416408...` byte for byte [verified].
- `fx/` tests: **373 checks, 0 failed** (`make test`; was 331). New `test_se_share`: in all seven MODEs the first
  pass after a head's echo is FDBK/64/n (within 0.2 % at FDBK 32, 64, 100), and from 3-4 s to 6-7 s after a burst
  FDBK 56 dies away (-9 to -28 dB), 64 holds (-0.08 to -0.23 dB), 72 grows (+8 to +15 dB) [verified].
- Renders (`make renders`, `out/T3.11/ab.html`, group **se_share**): MODE 1, 4 and 7 at FDBK 32 and 64, each with
  SC01's law next to it (`fxrender_sc01`, built from `src/sc01/`). The last 6 s of each: MODE 1 identical on both
  laws (byte for byte); MODE 4 at 32 dies away (SC01: held at -20 dB), MODE 7 at 32 dies away (SC01: runaway,
  -3.7 dB); at 64 both MODEs hold at about -20 dB (SC01: runaway at -3.5 dB). SC02's MODE 4 at 64 is byte for byte
  SC01's MODE 4 at 32 [verified]. The other multi-head renders had their INT multiplied by the head count, so they
  sound as before (`se_sing` tops out at 127, the stock maximum, instead of 3 x 56.7).

## 6. The diet in sharcvm [verified: scratchpad `diet_check.json`, `diet_cost.json`, `diet_cost_d2.json`]

`d1` = SC01's frozen echo with the three code changes (the tape at the end, the head loop, `SE_GLIDE` a function),
built on SC01's recipe (section 7 `2d72b7a2...`, scratch only), run block for block against SC01's own image
(`788bbef3`) on the whole handler: verify_se's main configuration and its two configurations, FDBK 32/64/127 with
one and three heads, every frame word moving (1,500 blocks), the fuzz (600 blocks, noise, FDBK 127 half the time):
**11 of 11 cases, 1,526,400 of 1,526,400 delay-slot samples identical, the tape identical at the end of every case**.
Instructions per block (three heads, FDBK 64, AGE 64, a band; 600 blocks): SC01 373,734; d1 385,369 (+11,635);
`d2` (the tape at the end and `SE_GLIDE` only, the same two edits as in d1) 373,785 (+51). The head loop is the
whole cost.
