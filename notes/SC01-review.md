# SC01 review: layout, dead-code reuse, comp-link 0.3, FDBK and meters

Date: 2026-10-21. Reviewer: one, read-only (no rebuild, nothing flashed, committed, pushed or posted). Subject:
`out/SC01/SC01.syx` sha256 `28779356...` (checked), main OS `out/SC01/SC01.main.bin` `0fac0ac1...`, DSP
`out/SC01/SC01-dsp/section7.bin` `788bbef3...`, the builder's note `notes/SC01.md`. Marks: **[verified]** = run (sharcvm,
a script over the image bytes, objdump) or read in the bytes or the decompile; **[inferred]** = reasoned, not run.

## Verdict

**OK to flash, with unit checks** (section 6). I found no blocker. The L2 patching is correct, and stock ranges A-D
are dead by every static test I could add. Level 3 is the main risk left. It cannot be retired off the unit, but
the way it would fail is benign and can be undone. comp-link 0.3 closes the review's finding 4 exactly. It closes
only part of finding 3, which stays a minor risk. Two claims in the note and the guide need correcting (findings 2
and 3).

## Findings

| # | sev. | finding | evidence | fix |
|---|---|---|---|---|
| 1 | **major (accepted risk)** | **Level 3** (`0x2e4f46-0x2e5df4`, 3,758 B) has the same kind of evidence as `0x315f8c-0x318000` had before CMP2: data intact (CP2F page 23), no code of ours ever. No off-unit test can retire it. Its exposure is narrow. **864 of its 870 instructions run in the effect init at every power-on.** The other 6 are one branch of `mbcomp_snap`, which runs at the first block or never. Mid-session, level 3 runs only through `mb_restart` → `mb_clear` (a guard trip) and the bad-magic re-inits, and those run only instructions the boot has already run. A fault should therefore show at power-on. The likely result: the DSP stops inside `0x1c1445`, before SPI2 is set up. The UI stays alive, with no sound and no sequencer clock, as in dnfw's silent builds. You recover by flashing CMP3 over USB, or through the STARTUP menu or DIN. | [verified] sharcvm coverage after `FXINIT` and 8 blocks (scratch `cov3.py`): `spaceecho_init` 452/452, `mb_clear` 135/135, `mbcomp_init` 153/153, `mbcomp_snap` 124/130; the call sites in `fx/mbcomp.c` (385, 415, 436), `cmp_glue.c` (170, 451), `se_glue.c` (92, 139); DSP main calls the effect init at `0x1ca804` and the SPI2 set-up `0x1ca46a` after it at `0x1ca814` (listing). [inferred] the UI and USB stay usable (dnfw's silent DN2 builds: UI alive; the brick checker's emulated ColdFire boot needs no SHARC); STARTUP and DIN recovery were proven on the unit (T1.5, 2026-09-08). | No cheap way to keep all code in code-proven ranges (section 1.3). Accept it, with unit check 1. Optional reduction: share the fxmath helpers and move `mbcomp_init` + `mb_clear` into L2 A. Level 3 then falls to about 2.25 KB. |
| 2 | **minor** | **`0x30ff76-0x310000` (138 B) is counted as code-proven, but it never held our code on the unit.** It was CP2F's watermark page 22. On every flashed build, code3 ended at or below `0x30ff76` (CP2F), and code2 starts at `0x310000`. In SC01, **`mb_targets` (`0x30fb70-0x310252`) puts 37 instructions there.** It runs at boot and about every 6th block, in block 2, the block where CMP2 failed. All 37 run in the effect init, so a deterministic fault shows at power-on, as for level 3. The note's "56,204 B where code of ours ran" and the guide's "every part that runs every block sits where CMP3's compressor and the tape delay run on your unit now" are therefore not exact. | [verified] `placed` in the build.json of FLG1, FTD1, CMP0-CMP3, CMP2D/E/F (highest code3 end `0x30ff76`); SC01 `functions`; sharcvm: 37/37 executed after `FXINIT`, and still after 60 still and 200 moving blocks (scratch `cov_gap.py`). | Cheap: split `code2` into `0x308400-0x30ff76` + `0x310000-0x315f8c`. 56,066 B is room for the 55,870 B of code, with 196 B of slack, so nothing moves to level 3. Or keep the layout and correct the two texts. Worth doing only if you rebuild for another reason. |
| 3 | **minor** | **comp-link 0.3 closes only part of the review's finding 3.** It catches a retarget during `UPDATE`. But the kit change `FUN_40031d94` sets the FxSetup's +0x10 early (`jsr 0x4002fccc` at `0x40031e78`, flags cleared at its end) and +0x20 only at its end (`move.l a0,a2@(0x3dcc)` at `0x40031f90`), with the children loops and the kit notifications in between. A slot-17 call from another task inside that gap sees the same (new live, old mirror) pair before and after `UPDATE`. So it still writes the new kit's 27 words and check word into the old kit's record. Whether another task can run slot 17 there depends on task priorities [inferred]. This is the same rarity class as before, and stock has the same race for one word. The guide's "fixes ... a rare mix-up" overstates it. | [verified] disassembly of `0x40031d94` and `0x4002fccc` (m68k-elf-objdump), decompiles of `vslot09`/`vslot04`/`vslot15`; `compl_update.lst` matches the C (below) | Optional: in `compl_update`, also require both pointers to name the same kit (the kit index of `stored` from the working-image base / 0x2a00 = the index of `live` in the live-kit array), or skip the encode while the KitActive is switching. Guide: "made rarer". |
| 4 | note | **Finding 4 is fixed exactly, and nothing else in storage changed.** `restore_id0` puts id 0 back unless a k in 0..24 is named. The stock map gives k 0..24 → id 0, k 25..69 → ids 1..45, k ≥ 70 → 0, so this is the exact test. Against CMP3's main OS, `compl_ser`, `compl_load`, `compl_encode`, `compl_vdef`, `compl_vmax` and `compl_seq` are byte for byte the same. `compl_tail` and `compl_decode` differ only in the moved addresses of `compl_seq` (+0x458) and `compl_vdef`/`compl_vmax` (+0x14). The three hook sites (`0x400dcf34`, `0x400dcef0`, `0x401da4f0`) are unchanged. Only `compl_update` changed. The getter (slot 10) is `return self+0x10`, so the extra call before `UPDATE` (also when `stored` is 0) has no side effect. The only FxSetup caller of slot 17 is `vslot04`, with `stored = +0x20`, so the new `MIRROR != stored` exit never fires outside a race. | [verified] the table at `0x401fd160`; byte compare of the two images via both map files (scratch); the 54 stock-part diffs are all relocations or fx-echo sites, none in storage; decompile `40190794`, `40190d74`, `4002ffca` (1 pointer ref) | none |
| 5 | note | **L2 patching: correct.** Each of the four sections is linked at START = 2 × SW (`0x1707d60`, `0x1709288`, `0x1709998`, `0x1709fa8`; END = 2 × hi − 1). Each lands at L2 byte `0x20000000 + 2 (SW − 0xb80000)`, inside the one stock L2 code block (block 56, `0x20000000`, 125,056 B). That block has the same header, size and argument as stock. All four `stock_sha256` values match the stock stream. The payload differs from stock only inside the four ranges. Our code ends before each live neighbour: A `0xb848a3` < `0xb84926`, B `0xb84c18` < `0xb84c1f`, C `0xb84dc7` < `0xb84dee`, D `0xb85461` < `0xb85466`. No other SC01 block writes `0x20000000-0x2001e880`. The six hooks call our stubs. | [verified] `l2check.py` (scratch) on `out/T3.8/stock/section_7_blob.bin` vs `section7.bin`; `build/t38.ldf` | none |
| 6 | note | **`build.json` "functions"** gives the last split function of each section the bytes that follow it. `mbcomp_process` shows 1,420 B, but 762 B of that are `fxh_comp`/`fxh_comp_init` (real size 658 B). Totals are right, sizes per function are not. | [verified] symbols `fxh_comp` `0x18ada2`, `fxh_comp_init` `0x18ae65` | Report only: end each function at the next symbol, not the next marker. |

## 1. The layout

### 1.1 Level 3: how risky
- **The CMP2 lesson applies fully** [verified, notes/T6.3-cmp2e/2f]. In CMP2, the code at `0x315f8c-0x3166ec` ran every
  block without trouble (`cmp_tail_fetch` and the head of `cmp_tail_apply`). Only executing `0x3166ec-0x31672f` stopped
  the DSP. Intact watermarks around level 3 therefore say nothing about code there, and neither does code running
  next to it.
- **What bounds it is when the code runs** (finding 1): every power-on runs 99.3 % of the level-3 instructions,
  including `spaceecho_init`'s 17.6 M-instruction tape clear. If executing those instructions fails, the first boot
  shows it, and so does every later boot. A fault that only shows up sometimes would most likely also show at some
  power-on, which is the benign case.
- **Mid-session exposure**: a guard trip (`mb_restart` → `mb_clear`, which needs a NaN/implausible state) and the
  bad-magic re-inits. Both are rare and run code the boot already ran.
- [inferred, weak] One geometric point, for what it is worth. The failing CMP2 bytes sat in the 8 KB just below where a
  32 KB PM cache would start (`0x318000`). In block 1, the same 8 KB (`0x2e6000-0x2e8000`) holds only data in SC01, and
  level 3 ends 524 B below it. This proves nothing, because code from `0x315f8c` to `0x3166ec` ran fine.
- Context [inferred, from notes/prior-art-2026-10.md 6.x]: dnfw read the PRM as advising no code in blocks that host a
  data cache. All our L1 code (blocks 1 and 2) is in such blocks. L2 code fetched through the I-cache is how the stock
  image runs. So moving code from L1 into L2 is a move toward the conventional place, not away from it.

### 1.2 Failure and recovery
The DSP stops inside the effect init. SPI2 is not yet configured, so the ColdFire sees no DSP. Expect: the screen
works, no sound, PLAY does nothing (dnfw's silent builds, prior-art 6.1 item 5). Recovery: flash CMP3 over USB
Transfer [inferred: the OS update path does not need the DSP]. If the OS does not take it, use the STARTUP menu or
the DIN route [verified on the unit, T1.5]. The guide's "no sound after power-on ... flash CMP3 back" fits. Add that the
sequencer will not run and the screen will.

### 1.3 Cheap alternatives that keep all code in code-proven ranges
None closes the gap cheaply [verified, sizes from build.json]:
- **Free space in proven ranges:** the block-2 tail has 334 B and the L2 tails have 262 / 14 / 78 / 10 B, so 698 B in
  all. Code4 below `0x2e4f46` is already used.
- **Needed:** 3,758 B above `0x2e4f46` (4,112 B for the four functions). Short by 3,060 B.
- **Sharing the ten fxmath helpers:** the echo would call the compressor's L1 copies of `fx_f2i`, `fx_i2f`,
  `fx_clampf`, `fx_log2`, `fx_exp2`, `fx_sin_q`, `fx_cos_q`, `fx_svf_g`, `fx_svf_coef` and `fx_knob01`. That frees
  1,672 B at the start of L2 A, which then has 1,934 B contiguous. Still 1,388 B short.
- **`spaceecho_init` (2,016 B) fits no hole**, even after sharing (the largest is 1,934 B).
- **Other sources, none cheap:**
  - The two comp stubs could share one save/restore (about −370 B in block 2). Doing the same for the block-1 stubs
    moves the flanger off its byte-identical proof.
  - Unreferenced stock functions total 6.5 KB, in pieces of 616 B or less, and some are probably live through roots.
  - Dropping the stock chorus/compressor inits changes CMP3's proven state.
- **The best partial step:**
  1. Share the helpers.
  2. Put `mbcomp_init` and `mb_clear` (1,508 B) into L2 A.
  3. Level 3 drops to `spaceecho_init`'s upper 1,662 B plus `mbcomp_snap` (588 B).

  Cost: a build change, a re-run of `verify_sc01.py` (about 1 h) and the brick checker. Not needed for this flash.
- **Possible but unproven:** a shorter `spaceecho_init` (state cleared with one loop instead of field by field) could
  make it fit L2 A. The proof would be (e1), the whole SeSlot = SE01's.

### 1.4 L2 patching in build.py
Correct (finding 5) [verified]:
- `ldr.Stream.read` requires exactly one covering payload block.
- `layout_check`, `placed` and the `allowed` list use the same SW → L2-byte map as the stock image and sharcvm.
- `image_diff_ranges` shows L2 changes only in the four ranges, plus the six hook words in L1 block 3.

## 2. Dead-code reuse (A-D)

| claim | mark | evidence |
|---|---|---|
| No call, jump, conditional, fall-through or indirect edge enters A-D from outside, except the four hooked CJUMPs | [verified] | `deadcode.json` (digikit sharcdb); my own pass over all 69,147 rows of `out/T3.1/disasm/*` (absolute and PC-relative targets): only `0x1c166c`, `0x1c1be1`, `0x1c1c58`, `0x1c215c` (scratch `deadscan.py`) |
| No literal or operand anywhere names an address in A-D (every address, not only the entries), in SW, L2-byte or 32-bit-word form | [verified] | `deadscan.py`; `nearscan.py`: the only operands within ±2 KB of the ranges are `r4=0xb859e7`/`0xb859f4` (outside) |
| No stock data word holds such an address | [verified] | Raw scan of every loaded byte of the stock image (each even offset, LE word and instruction-parcel order, every address of A-D). 26 hits, all coincidences: 24 are parcel fragments inside the code blocks 56/78/83 with no operand behind them, and 2 are the `"ASSERT ["` strings in blocks 47/55 after pointers `0x00b8c2c7`/`0x00b8d835` |
| Nothing reads the L2 code block as data | [verified] | The only operands in `0x20000000-0x2001e880` (or their word form) are the IMASK bit constants (`l2data.py`) |
| No fall-through into a range | [verified] | `rframe` right before each start (`deadcode.json` `before_lo`) |
| The live neighbours stay outside | [verified] | Chorus init `0xb84f51` → `0xb84926`, `0xb84c1f`, `0xb84f20`→`0xb84dee`; compressor init `0xb85501` → `0xb85466` → `0xb84faa`; meter getter `0xb8552b` → `0x1c0c76`: none has an edge, an operand or a PC-relative branch into A-D |
| Commands 0/1/2, kit change, project load, tempo | [verified] static | The command table `0x268a68` points into L1 block 3. The effect init `0x1c1445` has one caller (`main`, `0x1ca804`). Kit, tempo and project changes reach the DSP only as frame words, handled in the command-3 render through the hooked sites. Static deadness covers paths sharcvm never ran |
| `0xb84589` (A's "no static entry" function) is not a live root | [verified static] / [inferred] that nothing builds its address by arithmetic at run time | No pointer to it in any form |
| Dead at run time | [verified, sim] | CMP3's profile: 0 instructions in A-D over the init and 800 blocks. SC01's (w): every L2 instruction executed lies in our functions |

The residual assumption [inferred]: no stock code computes a code address in A-D from non-literal data, and the
ColdFire never sends one. Nothing I found suggests either.

## 3. comp-link 0.3
I read `compl_update.lst` against `store.c`, and they agree:
- `live0` is fetched before `jsr 0x4002ffca`, and `w0` (from `stored`+4) is saved.
- Vector: a k ≤ 24 (unsigned) → no restore; an empty vector → restore, harmless.
- Single: p[1] > 24 → restore.
- Then the `+0x20 == stored` compare, a fresh getter call compared with `live0`, and the inlined encode of 27 words
  plus `0xC3B1`.
- The `stored == 0` path tail-calls the stock routine.

On the review's findings:
- **Finding 4: fixed.**
- **Finding 3:** see finding 3 above for the window that stays open.
- **The rest of the storage paths:** unchanged (finding 4).
- **Storage tests** [verified, results files]: `store-SCEM.json` PASS 11/11. The 0.3 rules fail on C3EM and the 0.2
  rules pass. R1 models only the half that is fixed.

## 4. FDBK and meters
Nothing wrong:
- **`spaceecho_fb`** = clamp(v, ·, 127) / 64; NaN or ≤ 0 gives 0.
- **The runaway is bounded:** the tape write goes through ±1.5 and then x − 0.1481x³, which is monotonic and ends at
  1.0, times `inv_drive` = 1/drv ≤ 1. The resonant band filter and the DC blockers in the loop are stable and fed
  bounded values. The output stops at `SE_LIM_C` 0.7.
- **`cmp_mtr`** is a leaf with no state. NaN or inf gives 0, the value is clamped to 0..25 dB, and q ≤ 32,750.
- [verified, source]

## 5. Corrections to notes/SC01.md and FLASH-SC01.md
- 1.1 and the guide: block 2 proven = `0x308400-0x30ff76` + `0x310000-0x315f8c` (56,066 B), not 56,204 (finding 2).
- 1.4 and the guide: level 3 also runs on a guard trip and on re-inits, using instructions the boot already ran.
  "Shows at power-on" holds for 864/870 instructions.
- Guide, "worst realistic case": screen alive, no sound, PLAY does nothing; flash CMP3 back, or use STARTUP/DIN.
- Guide: the kit-switch mix-up is "made rarer", not fixed (finding 3).

## 6. Unit checks
1. **Three cold power-ons** (off ≥ 10 s). After each, before anything else:
   - the echo repeats (TIME 23, MODE 1, FDBK 32);
   - a band meter moves on drums;
   - the flanger sounds;
   - PLAY runs the sequencer.
   Any silence means level 3 (or the 138 B gap): flash CMP3 back.
2. **One knob session of 10 minutes or more:** every echo knob, every band knob and MIX. A freeze mid-session would
   point at the L2 code (first use of our code there) or at `mb_targets`' 138 B. Report what was being turned.
3. **The CMP3 review's storage checks** (notes/T6.3-cmp3-review.md section 3). Check 3 (switch kits with the
   sequencer running while turning a band knob) still matters (finding 3).
4. **Load:** listen for crackles with three heads, a high AGE and all bands working.

## 7. Files
- New: this note.
- Scratch, not in the repo, all in
  `/private/tmp/claude-501/-Users-naturarum-Documents-Claude-Elektron-CFW/7055c68c-582a-4ec4-965f-855ee047d978/scratchpad/`:
  `img.py`, `l2check.py`, `deadscan.py`, `nearscan.py`, `l2data.py`, `cov3.py` (+ `cov3.json`), `cov_gap.py`.
  Peak RSS 205 MB.
