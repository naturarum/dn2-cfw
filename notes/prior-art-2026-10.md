# Prior art survey, 2026-10 (new community work, late Sept to 6 Oct 2026)

Date: 2026-10-06 (background agent). Read-only research: shallow clones in `vendor/`, code and docs read,
nothing from those repos run (only listing and grep), nobody contacted, no issues opened.
Marks: **[verified]** = read in the repo's code or docs here (or in GitHub's API); **[inferred]** = reasoned,
not checked. "They say" = the repo's own claim, which we have not reproduced.

## Progress
- [x] metadata of every repo (licence, last push); clones listed in `VENDOR.md`
- [x] urgent items (top)
- [x] 1 capicola
- [x] 2 DigiFilter, RingTone/Tone+FX, the mk1 machines, digihealth, octabam's DSP dynload
- [x] 3 digikit's sharc-emulator branch, digiemu
- [x] 4 screen and graphics (for T5.0), DN2 routine addresses checked by masked match
- [x] 5 DT2 knowledge bases cross-checked
- [x] 6 dn2_firmware_explore: DSP, ColdFire mods, UI
- [x] 7 elekloader upstream, modwerk, syxgrid, DNX, others
- [x] 8 ranked next steps
- [x] VENDOR.md, ROADMAP upstream section, HANDOFF entry

Method: I read the repos myself and with seven read-only helper agents (one per area); claims they reported are
marked as theirs, and the ones that carry weight were re-checked here against the repos or our own images (noted
inline). Our own scripts and images were used read-only (masked matches, table dumps, hashes).

## Urgent

1. **Someone already mods the Digitone II, DSP included, and flashes it on a unit.**
   `github.com/angellinares/dn2_firmware_explore` ("dnfw"; AGPL-3.0-or-later; 1,272 commits by angellinares,
   193 PRs, last push 2026-10-06; site `angellinares.github.io/dn2_firmware_explore`) [verified]. It targets
   **DN2 OS 1.11**, not 1.12. Its README lists 11 mods "confirmed on hardware", among them **`midiarp` (the arp
   on MIDI tracks = our T2.6)**, `layermidi`, `arpmodes`, `arpplocks`, `lfo4`, `lfowaves`, `moddest`,
   `fxmod` (an LFO aimed at 24 chorus/delay/reverb parameters), `bootscreen`, `transients` (replaces the FM drum
   transient bank, which lives in the DSP's DDR) and `songguard` [verified: README, they say "confirmed on
   hardware"]. In development: **Waverider**, a wavetable machine whose SHARC code they write, assemble with
   selache and bake into the DSP image; recent PRs say "passed on the instrument" (#184, #191, #192) [verified:
   PR titles]. **Our 1.12 DSP section is byte-identical to 1.11's** (T3.1), so their SHARC-side work is
   directly about our DSP image: their SHARC splice checks for our exact section-7 hash (`dsp.py` line 58). Nobody
   has started a 1.12 port. Details in section 6. What to do: take their splice method for T3.8 (with their two
   hard-won safety facts: selas mis-encodes negative shift immediates, and part of our "free" L1 is cache),
   re-scope T2.6 around their midiarp design, and ask the owner whether to contact them (they also contribute
   to digikit: 31 of its PRs are angellinares's, 21 still open, among them #29 "Digitone II machine selection,
   the row -> type table" and #19 "three hardcoded Digitakt II values that stop Digitone II booting").
2. **Licences.** dnfw is **AGPL-3.0-or-later**. Its own licence audit (`docs/references.md`, 2026-09-27) says
   elekloader, digihealth and digislicer are GPL-2.0-only and so cannot be combined with it; **that is out of date**:
   elekloader was relicensed GPL-2.0-or-later the same day (upstream commit `7d47886`, #4; `NOTICE` now reads "version
   2 ... or (at your option) any later version"), and digihealth, digislicer, digineighbor, digitables and
   digitakt2-perform all declare `GPL-2.0-or-later` in their `mod.json`/headers [verified]. elekloader's new
   TypeScript engine (`js/`) is GPL-3.0-or-later. So: facts from dnfw (addresses, formats, measurements) are free to
   use; **dnfw or capicola code** copied into a mod would make that mod AGPL-3.0 (combinable with elekloader's core
   taken under GPL-3.0, but whoever gets the `.syx`/`.elemod` must be offered the source, and it could not go into
   elekloader's own repo, which is GPL-2.0-or-later). Keep anything we upstream to elekloader or digikit free of
   AGPL code. Same for syxgrid-digitone-ii and DNX (AGPL). [verified licence texts; legal reading inferred, not
   advice] Our `VENDOR.md` now says GPL-2.0-or-later for elekloader and digikit.
3. **digikit has a coupled ColdFire + SHARC emulator that renders DN2 voices** (branch `work/sharc-emulator`,
   158 commits ahead of main, 2026-10-04; Rust, generated from Python semantics; DN2 **1.11** only; ~1.22x slower than
   real time; no SHARC code injected, nothing flashed) [verified: its docs]. It covers most of our T9.1 and overlaps
   T3.6. Try it with our 1.12 before building T9.1 ourselves. Details in section 3.
4. **No Digitakt II time-stretcher exists in public.** capicola is Eurorack firmware for an STM32 module, with no
   Elektron code or port anywhere we could find (section 1). The owner's "timestretcher for DT2" is most likely a
   mix-up. [verified: repo, issues, release, forks, author's repos, GitHub search, modwerk, elekloader's shop]
5. **No public elekloader DSP-section tool.** RingTone's `DSP.md` section 6 documents `python -m elekloader.dsp`
   with `.dspmod.json` specs for section 7, but that module is in no public elekloader branch or fork (checked
   main, 3 other branches, 7 forks) [verified]. On the Digitone mk1 section 7 is ColdFire code for a second
   ColdFire, so even that tool would not fit the DN2's SHARC. T3.10 stays open.

## 1. capicola

| | |
|---|---|
| URL | https://github.com/heavylight-industries/capicola |
| licence | AGPL-3.0 (submodule `lib/alchemy-sdk` and its vendored libDaisy: MIT) |
| last push | 2026-10-02 (v1.0.0, first public release, 2026-10-01); clone `120b0b8` |
| what | Real-time time stretcher, pitch shifter, envelope follower and transient detector for the **Hermetic Modular Alchemy Lab**, a Eurorack module on an Electrosmith Daisy (STM32H750, Cortex-M7 with FPU). Built with arm-none-eabi-gcc, flashed with dfu-util. The DSP is the author's DAFx26 paper "Keyframe Time Stretching via Extrema Sampling" (repo `heavylight-industries/dafx26-paper`) |

**Elektron link: none.** [verified] No mention of Elektron, Digitakt, Digitone, `.syx` or SHARC anywhere in the
tree (grep). One issue (a README typo), one release, four forks (all plain copies, newest 2026-09-03). The author's
other repos are the paper, an audio-examples page and a résumé. GitHub search for Digitakt/Digitone/elekloader
repos updated since mid-August shows no time-stretch work; modwerk's only "timestretch" hit is octabam's
Octatrack REPITCH mode; elekloader's mod shop (62 items, `web/catalog.json` on main) has only Octatrack, Digitakt
mk1 and Digitone mk1 mods. So there is nothing to align with.

**Reusable DSP ideas** (all float C++ headers in `lib/`; ideas are free to reuse, the code is AGPL):

| idea | where | notes for us |
|---|---|---|
| Zavalishin TPT ("zero-delay feedback") one-pole and state-variable filter, LP/BP/HP/AP/notch outputs | `lib/filter.h` `OnePole`, `StateVariable` | Same family as our `fx/` SVF (`FX_SVF`, `fx_svf_coef`). Theirs divides by `den` every sample in `Tick`; we already precompute. Nothing new for us |
| "Deluxe" delay line: power-of-two ring, reads with linear, 4-point Hermite (with optional stride), cubic B-spline, and 1st/2nd derivatives at the read point | `lib/DeluxeLine.h` `ReadHermite`, `ReadHermiteD1`, `ReadBSplineFull` | Our tape delay already uses a 4-point Hermite read (`fx/tapedelay.c` around line 390). The **B-spline read** is a smoother (low-passing) alternative for a flanger's modulated tap, where Hermite's slight ripple matters less than zipper-free sweeps: worth an A/B in T5.1 [inferred] |
| Teager-Kaiser energy operator from the interpolated derivatives: `|d1² - x·d2|` | `DeluxeLine.h` `ReadHermiteFull`/`ReadBSplineFull` (`tkeo`) | A cheap per-sample "energy" for transient detection |
| Tabulated functions: 1,024-point tables with odd/even mirroring and wrap/clamp, linear interpolation; sine, a "quake" saturator and a sinc shaper | `lib/TabulatedFunction.h`, `lib/Tabulator.h`, `lib/Shapers.h` | On the SHARC a table read costs a DAG access plus one multiply-add; useful for the tape delay's soft clip and the flanger's LFO if the selcc cost of `fx_exp2`/polynomials stays high (T3.11 section 11) [inferred] |
| TKEO transient detector: TKEO on a B-spline read, SVF low-pass envelope, peak picking on the sign change of the derivative, sub-sample peak time, adaptive threshold = ratio over a 100 ms one-pole average, 150 ms hold-off | `lib/Detector.h` `Detector::Analyze` | Possible sidechain/transient logic for the 3-band compressor (G9) or a future "punch" control; not needed now |
| Keyframe time stretch: store sparse keyframes (local extrema, 12 bytes each) instead of raw audio, read them back on a stretched grid with two crossfading grains; on a kept transient the read head snaps to "now" with a short crossfade so rhythm stays tight | `lib/KeyframeRecorder.h`, `SparseLine.h`, `Granule.h`, `Analyzer.h`; the paper | Float-heavy but light on memory; the DN2 SHARC has the float throughput and DDR for it. A sampler-style stretch is not a roadmap goal; park as an idea [inferred] |

**AGPL, if we ever borrowed code:** AGPL-3.0 is GPL-3.0 plus a network clause. Code copied from capicola into our
firmware would make that firmware AGPL-3.0: anyone we give the `.syx` or `.elemod` to must also get the
corresponding source under AGPL. elekloader's core is GPL-2.0-or-later, so an AGPL effect mod could be combined
with it (the core taken under GPL-3.0), but the effect could never go upstream into elekloader's repo, and every
build that includes it carries the AGPL duties. Reimplementing the ideas from the paper or from first principles
carries no such duty. [verified licence texts; legal reading inferred, not legal advice]

**What it means for us:** nothing urgent. T5.1 (flanger): A/B a B-spline modulated tap against our Hermite.
T3.11/T6.*: tabulated saturators if selcc's maths stays expensive. Otherwise park.

## 2. New DSP code on Elektron mk1 devices: DigiFilter, RingTone / Tone+FX

Main finding: **on the mk1 devices there is no SHARC in the path these mods touch.** The Digitakt mk1 renders all
its audio on the ColdFire MCF5441x itself (render ISR on vector 191), and the Digitone mk1's "DSP" is a **second
ColdFire** (section 7, ColdFire code) that runs only the FM voices, while the main ColdFire runs the effects and the
mix. So these mods are ColdFire m68k code built with the same `m68k-elf` gcc as any elekloader mod, hooked through
elekloader's ordinary main-OS sites and events. [verified: `RingTone/DSP.md` section 1, `DigiFilter/RE_NOTES.md`
section 4; digiemu's README: "on the Digitone a second emulated CPU runs the firmware's own FM voice engine"]
The DN2 is different: its voices **and** its effects run on the SHARC+ (our T3.4), and its ColdFire vector 191 is
the frame ISR that builds the DSP frame (T3.3), not a render. What transfers is method, not code.

### 2.1 DigiFilter

| | |
|---|---|
| URL | https://github.com/DigiAlchemydsp/DigiFilter |
| licence | GPL-2.0-or-later (`LICENSE`, SPDX line) |
| last commit | `a415ed1`, 2026-10-05 (v1.0j) |
| what | Four extra per-track filter types on the **Digitakt mk1 OS 1.53**: BP, BP2, COMB, TRASH (TYPE 8-11 after stock OFF/LP/HP/EQ:1..5), with the FLTR page's response curve and TYPE-box glyph redrawn. A format-2 elekloader mod (`requires core`, core 2.1) |

How it works [verified, `RE_NOTES.md`, `mod.json`, `filter_glue.s`]:
- **Where the DSP runs:** on the ColdFire, inside the render ISR `FUN_40077420` (vector 191). The render's voice
  loop calls the stock per-voice filter `0x40072844(params, buffer, active, voice)` through `a3`; `buffer` =
  `0x80001a18 + v*0x80` (32 x int32 in place), `params` = `0x800027a4 + v*0x6a` (byte 0 = TYPE).
- **Hook:** one 6-byte `jsr` site at `0x400780ba` (stock `addil #0x80001a18,%d0`, the instruction just before the
  call) to `digifilter_dispatch`, which does the displaced add, remembers the original `a3` once, and per voice points
  `a3` at `digifilter_filt` for TYPE 8-11 or back at the original target otherwise. Chosen so it does not collide with
  digihealth, which rewrites the `lea` at `0x400780a4` for its profiling wrapper (RE_NOTES section 6). Stock TYPEs
  tail-call the stock filter and stay byte-identical to stock.
- **Parameters:** no new parameters. It widens the Filter Type descriptor's range word (`0x401aa45c` `00000700` ->
  `00000b00`, a `data` site), re-points the TYPE value formatter (`op: ptr` at `0x401539b2`, stock `0x40065336`) to
  `digifilter_typefmt`, which prints its own names for 8-11 and jumps to the stock formatter otherwise, and reuses
  the second FLTR page's stock knobs (Base, Width, Env Delay, SRR) as comb controls when TYPE is COMB/TRASH. The
  stock DSP clamps TYPE to 7 at `0x40072fd0` for its own use and stores the clamped value elsewhere, so the
  unclamped kit value reaches the hook; save/load keeps the raw slot (static check of the serializer tables).
- **DSP code:** integer only (no FPU, no libgcc): a trapezoidal SVF in hand-written EMAC assembly
  (`filter_dsp.s`, saves and restores MACSR/ACC0/ACCEXT01) and two feedback combs in C (`filter.c`, `comb_run`),
  coefficients computed at the two ends of each 32-sample block and ramped across it.
- **UI:** an `ev_draw` handler (`filter_ui.c`, `digifilter_draw`) walks the view controller's view list, finds the
  page of kind 6 (FLTR), clears the stock graph and TYPE box and paints its own curve. Lesson learned on a unit: the
  first version patched the FLTR view's drawView vtable slot (`0x401842a0`) directly; in a 9-mod build core's
  `ev_draw` dispatcher owned that slot and silently replaced it (RE_NOTES section 11). Subscribing to `ev_draw` fixed it.
- **Testing and cost:** 20 headless digiemu scripts (`tests/digiemu_filter_*.py`) with a float reference model
  (`tests/filter_model.py`); cost measured as instructions per voice per block with a counter around the dispatch:
  stock TYPE 1 2,397, BP/BP2 ~2,811, COMB/TRASH ~1,744 (RE_NOTES 10.3). The owner flashed it inside a 9-mod build
  ("ULTITAKT"), which is how the vtable-slot clash was found.

What it means for us:
- **T5.0 / T5.1:** the same three moves fit a DN2 flanger in the chorus slot: (1) rename by re-pointing the
  value formatter or name pointers (an `op: ptr` site) rather than overpainting, (2) overpaint only graphics, from
  `ev_draw`, never by patching a shared view vtable, (3) reuse the stock knobs and their frame words for the new
  effect's controls, so nothing new has to be stored or sent. On the DN2 the audio half is a SHARC patch (T3.8),
  not a ColdFire hook.
- **T3.8 design rule:** dispatch on a value and tail-call the stock routine for stock values, so stock settings stay
  bit-identical and the A/B against stock is free. A DN2 analogue: hook the chorus call in the FX bus and branch to
  new code only when a flag word in the frame says "flanger" [inferred].
- **T3.11 cost method:** their per-voice instruction counter in the emulator is what our T3.6 interpreter already
  gives; nothing new.

### 2.2 RingTone and Tone+FX (Digitone mk1 master effects)

| | RingTone | Tone+FX |
|---|---|---|
| URL | https://github.com/DigiAlchemydsp/RingTone | https://github.com/DigiAlchemydsp/Tone-FX |
| licence | GPL-2.0 file, sources say GPL-2.0-or-later | same |
| last commit | `2d61311`, 2026-10-05 | `7f329d8`, 2026-10-05 |
| what | source repo: ring/AM, tilt EQ, wavefolder, meter and two new master pages for the **Digitone mk1 OS 1.43** | release-only repo (no source) of the same suite as one merged `tonefx-2.3c.elemod`; stress-tested on a Digitone mk1 |

How it works [verified, `RingTone/DSP.md`, `mods/*/mod.json`]:
- **Where:** the main ColdFire's render ISR `0x4009D100` (vector 191). The suite subscribes to core-dn1's
  `ev_render_in` (site `0x4009D108`) and `ev_render_out` (site `0x4009E51C`) and processes the final output half
  (`0x80001A00 + off`, 32 stereo int32 frames, 24-bit left-justified) in place: ring (order 50), EQ (55), fold (60),
  then the meter. Integer Q14/Q15 maths, a 256-entry Q15 sine table.
- **DSP.md** is a good map of the Digitone mk1's two-CPU audio: a 128 KB dual-port RAM (`0x10000000` on the main
  CPU) carries a 928-byte parameter frame (stride `0x9E` per voice) one way and 8 voices x 32 int32 the other; a GPIO
  edge from the main CPU's SSI DMA interrupt clocks the voice CPU's render; section 6 boots section 7 over DSPI2.
- Its section 6 ("Writing a Core B DSP mod") describes an `elekloader.dsp` tool with absolute-address
  `.dspmod.json` sites for section 7. That tool is not in any public elekloader tree (see Urgent 5).
- `docs/FAST-AUDIO-DN.md`: digihealth's "FAST AUDIO" (copy the Digitakt's ~19 KB hot render block into unused
  on-chip SRAM `0x80003360-0x80007fff`, fix up self-references, stub the calls in; render 537 -> 480 us per block)
  does not port as is to the Digitone mk1, whose render is not self-contained. Not relevant to the DN2's SHARC.

What it means for us:
- **G6/T4.1:** "CPU and DSP shared, manage the load yourself" is the honest state on mk1 too; they publish no
  load figure for the Digitone. digihealth's measured render time and load (section 4) is the closest model for a
  load readout.
- **T6.2 / T5.0:** their two extra master pages, appended to the FUNC+LFO master page cycle with LEFT/RIGHT to
  rotate between them, and settings stored per pattern (`docs/PATTERN-STORAGE.md`) and driven by unused MIDI CCs
  (`docs/MIDI-CC.md`, hooked CC router `0x400ED94E`), are a working pattern for the DN2 3-band compressor's extra
  pages (T6.2) and for T2.3 (extra data without changing the format) [inferred: mk1 addresses, DN2 equivalents not
  looked up].

### 2.3 New machines on the Digitakt mk1: digistring, digisophie, digineighbor, digislicer

All four render on the Digitakt mk1's ColdFire inside the render ISR, as format-2 elekloader mods built with
`m68k-elf` gcc (digistring: `-mcpu=54455 -O2 -ffreestanding -nostdlib -fno-pic`) [verified, per-repo files below].

| repo | licence, last commit | what | how it hooks in |
|---|---|---|---|
| [Alez22/digistring](https://github.com/Alez22/digistring) | MIT, `b23f061` 2026-10-06 | STRING, a Karplus-Strong plucked string machine (OS 1.53/1.54) | `jmp` site at `0x40077fb2` (stock `addi.l #68,d7`), after the stock voice loop `0x400757fe` and before AMP/filter: writes 32 samples per track into `0x80001a18 + 128t`. A `core_machines` descriptor `{8, "STRING", "STRG", icon, params of machine 3 (SLICE), render 8}` (`glue.s`): it borrows SLICE's 8 parameter slots, so save, p-locks and CC/NRPN work, but SLICE's ranges cannot change. A `-DKS_FAST` build runs its loop from on-chip SRAM via digihealth's FAST AUDIO. They say five STRING tracks run at ~95 % load with FAST AUDIO on |
| [soejrd/digisophie](https://github.com/soejrd/digisophie) | MIT, `961c39c` 2026-10-02 | two-operator metallic percussion (machine 7) | `jsr` at `0x40077fba`. `DSP.md`: 16 synth samples at 24 kHz per 32-frame block, interpolated up; slow controls every 4 samples. `tests/timing_probe.py` uses digiemu's ColdFire cycle model (`emu.cftiming.deadline_report`, an I-cache miss penalty) to check the render deadline |
| [irpina/digineighbor](https://github.com/irpina/digineighbor) | GPL-2.0-or-later, `d09574a` 2026-10-01 | NEIGHBOR (machine 4), a pitch shifter with correlation-aligned splices | inject at `0x40077fba`, tap at `0x40078142`, one block of latency; cost counted in emulator instructions (650 / 5,000 / 11,400 per block vs ~82,000 for the whole render) |
| [irpina/digislicer](https://github.com/irpina/digislicer) | GPL-2.0-or-later, `0c1b680` 2026-10-05 | slicer (machine 5) | `ptr` op on a vtable slot (SamplePageView slot 2, `0x401848dc`); 2.2 moved 70 KB of tables to the firmware heap (`operator new` `0x400d4180`, allocated lazily in the UI task, pointer published last): mod RAM 92 KB -> 15 KB |

What it means for us: **T7.1** (machine slots on the ColdFire): elekloader's Digitakt mk1 `core_machines`
descriptor (a new machine that borrows a stock machine's parameter set) is the pattern to copy for the DN2 menu
side; the DN2 audio side is SHARC code, so the render hooks do not transfer. **T3.11/T3.6:** their habit of
checking every new render against a cycle/deadline model in the emulator is what our interpreter's instruction
counts give us on the SHARC. **Memory:** digislicer's move of big tables onto the firmware heap is a cheap fix if a
DN2 mod's `.bss` outgrows core-dn2's 1 MB DDR window (T1.9).

### 2.4 Measuring load: digihealth and digirta (for G6)

[irpina/digihealth](https://github.com/irpina/digihealth) (GPL-2.0-or-later, `6d2a956`, 2026-10-01; Digitakt mk1
1.53/1.54, Digitone mk1 1.43 as SYSTEM INFO only) [verified, `digihealth/sysinfo.s`, `build.py`, `fastaudio.s`]:
- **Render time:** read DMA timer 0's counter DTCN0 (`0xFC07000C`) in `ev_render_in` and `ev_render_out`; sum,
  count and keep the longest.
- **Idle time:** a `jsr` at the RTOS context switch (`0x40000438` on DT mk1) adds DTCN0 time while the outgoing TCB is
  the idle task's and its resume PC is the idle loop's `bra.b *`.
- **Once a second** (every 30 `ev_tick`s, interrupts masked): DSP = render ticks / window; peak = longest render /
  render period; CPU = 1 - (idle - renders inside idle). Drawn by `ev_draw` in the top bar with fillRect, frameRect
  and textf ("CPU 61%  DSP 60%/62%"), and readable over USB through a SysEx channel (device byte `0x7D`,
  HELLO/STATS/PEEK, `tools/digiusb.py`), hooked at the SysEx router's `lea`.
- **FAST AUDIO:** copies the ~19 KB hot render block to unused on-chip SRAM, rewrites self-references, stubs the 15
  call sites (`keep2`), and checks the copy's checksum every second. They say: render 537 -> 480 us per block, load
  80.5 % -> 72.0 % on a unit. Checked in digikit's emulator: audio and engine state bit-exact against stock over 4
  scenarios, screens pixel-identical.
- **Caveat for the DT2/DN2:** core-dt2 must not start DTIM0 (the OS reads it at ten sites; starting it broke a cold
  boot); our core-dn2 sets `NO_DTIM0` the same way (T1.9). A DN2 port must reuse a timer the OS already runs.

[Alez22/digirta](https://github.com/Alez22/digirta) (MIT, `5a0d596`, 2026-10-04; DT mk1): an octave-band analyser
with events only (latches the DMA half at `ev_render_in`, copies at `ev_render_out`, FFT in the UI task), timing
its own load with DTCN0.

What it means for us: **T4.3** (ColdFire load): digihealth's idle-task timing at the context switch ports to the
DN2 ColdFire as is (RTOS, same compiler), with the DN2's context-switch site, idle TCB and idle PC found again.
**T4.1/T4.2** (SHARC load): no mk1 repo measures a DSP; on the DN2 the block handler already writes its EMUCLK
cycle count into the first word of the frame it sends back (T3.2), so the DSP number may need no SHARC patch at
all; dnfw's `sharc-idle-load` work (section 6) is the other source. The `0x7D` SysEx read-out is a good way to log
load from the unit during T2.5/G10 stress tests.

### 2.5 Loading DSP code at run time (Octatrack, octabam in modwerk)

[repeat98/modwerk](https://github.com/repeat98/modwerk) (GPL-3.0, `1077590`, 2026-10-06) vendors octabam;
`sdk/octabam/platform/dsp-dynload/README.md` and `dsp-dynload-transport/README.md` describe uploading DSP56300 code
into the Octatrack's DSPs while the unit runs [verified, docs]: ColdFire code carried in the main OS writes a
64-halfword packet per DSP core into an idle bank of the frame the frame ISR's eDMA sends (at most 24 P-words per
transaction), the DSP side bounds-checks the write, reads it back and returns a 24-bit sum, and the new code is
bound into the stock init/process dispatch only after the acknowledgement. Pitfall they hit: the host's core
selector must be restored before the next frame or both DSPs stop.

What it means for us: **T3.8 / T3.10** have a second route that needs no section-7 change: keep the SHARC code in
the main OS (section 3, which elekloader already patches), push it over the SPI2 frame link from a ColdFire hook,
and switch it in on the SHARC only after a readback. The SHARC side still needs one small stub that reads the
packets and binds the code, so section 7 must change once; but then every new effect could ship as an ordinary
elekloader mod. dnfw already moves data this way: its DSP "command 4" copies a table chunk from the frame into DDR
(section 6) [inferred design; dnfw fact verified from its PR title].

## 3. digikit: the `work/sharc-emulator` branch, digiemu

**digikit `work/sharc-emulator`** ([m-dwyer/digikit](https://github.com/m-dwyer/digikit), branch head `277760a`,
2026-10-04; GPL-2.0-or-later; cloned as `vendor/digikit-sharc-emulator`) [verified unless marked]:
- **Scope:** DN2 **1.11** and DT2; it forked before main gained DN2 1.12 and removes the 1.12 row. Our 1.12 section 7
  equals 1.11's (sha256 `336e340a...`, also in its `docs/findings/11` line 8), so its DSP results apply to us.
- **What it is:** SHARC+ semantics written once in Python (`tools/sharc_core/`, ~12.8k lines), transpiled to Rust
  (`tools/sharc_transpile.py`, `native/sharc-gen`), plus ahead-of-time Rust "regions" generated from the
  firmware's basic blocks (`tools/sharc_rsgen.py`), an opt-in Cranelift tier for hot DO loops, and a Rust ColdFire
  emulator coupled per 2,748-byte SPI2 frame: per frame, the SPI2 exchange, 666,667 DSP instructions (1 GHz assumed),
  one SPORT4 block of 32 stereo samples (`native/boot/src/sharc_peer.rs`). It renders **voices**: a QA note gives a
  clean ~263 Hz tone and a GUI "Audition Trig 1" gives PCM (`docs/findings/07-emulator.md` ~5746-5785). Speed:
  202-217M DSP instructions/s; coupled ColdFire + DSP ~1.22x slower than real time with PGO and the fast tier.
  Validated against its own Python oracle, WASM parity, golden hashes and differential fuzzing; **not compared with a
  unit**. Boot starts at the flattened loader's entry (`0x1c12e2` on DN2), not from ROM reset; the DN2 reaches the RTOS
  idle loop after ~573.6M instructions.
- **No new SHARC code anywhere in the branch, nothing flashed, no LDR writer** (`tools/sharcldr.py` only parses;
  `tools/selasm.py` assembles snippets to cross-check decoding). "P7: DSP patching" is future work
  (`docs/plan-native-emulator.md` 327-330). XSLICE/CFADE (the flashed DT2 eighth machine) is a ColdFire-only patch;
  the SHARC reads the CFADE frame word and discards it (`HANDOVER-2026-09-22-machines-and-dsp-hooks.md` 28-35), and
  they suggest such ignored frame words as free parameter channels for future DSP code.
- **Facts for T3.8:** selache emits VISA 16-bit parcels big-endian while the image stores each parcel little-endian:
  byte-swap every parcel before placing selcc/selas output (`tools/selasm.py` 4-17). The IVT is fixed 48-bit words,
  stored little-endian, not VISA (loader block 70). Section 7 has no hash of its own beyond the ADI block-header
  checksums; the container checksum and HMAC cover it. Candidate hook points on DN2 1.11 (addresses verified by them,
  "hook" is our reading): the block handler's indirect command jump at `0x1c9dbf` through the RAM table `0x268a68`;
  the SEC dispatcher's tables `0x240910 + 2*SID` / `0x240aa0 + 8*sel`; command 3's call of the renderer `0x1c2712`.
- **Agrees with our T3.2:** L1 block 2 is never a loader target; the L2 free tail starts at `0x2008823c`; "space is
  not the constraint, the hook is" (`docs/findings/06` ~3173-3229). Neither project has checked block 2 at run time.
- **Agrees with / extends our T3.6:** `0x023e` begins a 48-bit Type 6a shift-immediate (digikit main's decoder,
  which sharcvm uses, already reads it so);
  plain MODIFY does not scale, only Type 7a (sw)/(nw) does (our T3.6 2.2 rule); SIMD DAG stores write both words
  (same as ours); RECIPS/RSQRTS seeds are unknown to them too (they truncate the true value to 8 bits, as we do).
  New to us: ShiftImm opcode `0x19` = OR FDEP (PRM table off by one row); Type 4b (l,x,w)=(1,1,1) is a plain
  normal-word access; (lw) offsets scale x4 in byte space; both RTI (DB) delay slots use the interrupt's banks
  (`docs/findings/15` 242-266, `findings/07` 4625-4637, 4850-4865) [verified, their docs; not yet checked in sharcvm].
- **DN2 load:** command 3 takes ~335k of the 666,667-instruction budget (~50 %) on their QA note workload
  (`findings/07` 4917-4918). Our T3.6 measured the whole block handler at 320k instructions with no voices; theirs
  includes voices, so the two do not yet line up [inferred: workloads differ].

What it means for us: **T9.1** (full DSP simulator linked to the ColdFire, with audio) is largely done upstream
for 1.11; before building it, try their coupled emulator with our 1.12 main OS (their DSPI2 and eDMA addresses on the
ColdFire side are 1.11's and must be found again). **T3.6:** keep sharcvm for offline renders (it is ours, small
and fast enough), and port the four ISA rules above after checking each on our test set. **T3.8:** take the parcel
byte-swap rule; the hook points are a shortlist.

**digiemu** ([irpina/digiemu](https://github.com/irpina/digiemu), GPL-2.0-or-later, `c1b5735`, 2026-10-03): a
digikit fork for the **mk1** devices with apps for Windows and Apple-silicon Macs, live 48 kHz audio, the Digitone
mk1's second ColdFire emulated (`emu/dsplink.py`), and a "firmware check" (`docs/FIRMWARE-CHECK.md`) that runs a
modded build under a stricter emulator (MCF5441x memory map and exceptions, cycle timing against the render
deadline, every screen and sample compared with stock). Its DN2/DT2 files are upstream digikit's, older than
our pin. For us: the firmware-check idea (stricter memory map, deadline check, stock comparison of every screen and
sample) is a good model for brick-checker hardening (T1.7b) [inferred]. **digislicer** is covered in 2.3.

## 4. Screen and graphics: how the community mods draw (for T5.0)

Repos read: `toonst/digitakt2-perform`, `Fadedlimes/digisplash-ii`, `octplane/digitakt-ii-splash-screens`,
`DigiAlchemydsp/DigiSplash`, DigiFilter's `filter_ui.c`, RingTone's `mods/digictl/digictl.c`, digistring,
digislicer, digitables, digihealth, and elekloader's cores (`mods/core`, `core-dn1`, `core-dt2`). Licences and
commits in VENDOR.md. dnfw's own display findings are in section 6.

**Bottom line for T5.0** [verified in code unless marked]:
- No DT/DN UI mod writes the screen's framebuffer directly. They all draw into the frame `Bitmap*` that core's
  `ev_draw` passes in, through firmware routines (`fillRect`, `frameRect`, `vline`, `pixel`, `textf` with the 5-px
  font object, `blit`). Only the mk1 boot-splash mods write raw panel bytes, and only while the intro runs.
- `core_draw` calls stock `drawAll` first, then each `ev_draw` handler `f(bmp, ctrl)` in ascending `order`
  (`elekloader/mods/core/core.s` lines 87-108). It only runs when the frame is recomposed: to animate, set the dirty
  byte `*(u8*)(ctrl+0x20) = 1` from `ev_tick` (`core.s` 67-85; `digitables/digitables.c` 707).
- Calling convention: C, arguments on the stack; colour 0 = clear, 1 = set, negative = invert; **y = 0 is the
  bottom row** of the 128x64 bitmap (`elekloader/examples/hello-marker/hello.c` 19-28, `digislicer/slice.c`
  1202-1215).
- **Bitmap object** (0x1c bytes): `{vtable, width, height, longs per column, pixels*, mask*, 0}`; pixels are
  column-major, one 32-bit word per 32 rows, MSB = bottom row; the mask has the same layout and selects which pixels
  a blit writes (`digistring/ui.c` 58-68, `digislicer/slice.c` 683-687). A 128x64 frame = 1,024 bytes, 8 per
  column. Icons are static Bitmap objects pointing at the stock Bitmap vtable (11x7 menu icons in
  `digislicer/glue.s` 22-46; 15x15 page icons in `digistring/ks_icons.inc`, generated from ASCII art by
  `digistring/scripts/gen_icons.py`), drawn with `blit(screen, icon, x, y, centre)`.

**The routines on the DN2 1.12.** The graphics library sits at the same relative offsets on Digitakt mk1 1.53 and
1.54, Digitone mk1 1.43 and Digitakt II 1.17 (fillRect is the anchor). I checked the resulting DN2 1.12 addresses by
masked match against Digitakt II 1.17 (48 bytes from each routine's start, absolute addresses masked, our
`scripts/t19_sites.py` helpers): every predicted DN2 address holds the same code as the DT2 routine, with the
same prologue [verified: match; the routine names rest on the mk1 mods' use and the constant offsets, inferred].

| routine | signature as the mods call it | DT2 1.17 | **DN2 1.12** | DN2 masked-match hits |
|---|---|---|---|---|
| fillRect | `(bmp, x0, y0, x1, y1, c)` | `0x401131ae` | **`0x40114b22`** (known, T1.9) | 2 (`0x40114f62` also) |
| frameRect (outline) | same | `0x40112f92` | **`0x40114906`** | 1 |
| vline | `(bmp, x, y0, y1, c)` | `0x40112848` | **`0x401141bc`** | 1 |
| pixel | `(bmp, x, y, c)` | `0x401124fc` | **`0x40113e70`** | 2 (`0x40113ed6` also) |
| textf (printf-style) | `(bmp, font, x, y, maxlen=-1, fmt, ...)` | `0x40113d84` | **`0x401156f8`** | 1 |
| TEXT with flags (dnfw's name) | `(bmp, font, x, y, flags, fmt, ...)`; flags 2 = x is the centre, 8 = invert | - | **`0x4011573c`** (dnfw, 1.11 `0x4011545c`) | - |
| blit | `(dst, src, x, y, centre)` | `0x40114168` | **`0x40115adc`** | 1 |
| stock icon picker | `(set, idx, 0) -> Bitmap*` | `0x40114614` | **`0x40115f88`** | 1 |
| View::invalidate | `(view)` | `0x4011b126` | **`0x4011ca9a`** | 1 |
| drawAll | `(ctrl, bmp)` | `0x4011bc96` | **`0x4011d60a`** (= T1.9's DRAWALL) | 1 |

The two text routines: `0x401156f8` (8 callers in our function table) formats into a 128-byte buffer with
`0x400005e0` (vsprintf) and draws it with `0x401156d4`, the mk1 mods' `textf` shape; `0x4011573c` (91 callers) is
the one the stock pages use, with the flags above (dnfw `docs/display-path.md`) [verified: our 1.12 listing and
`out/decomp/main_os/functions.tsv`]. dnfw also names `0x40115074` (1.11 `0x40114d94`) a write-only fill/clear.

Still to find on the DN2 (no repo has them for the DT2 either; dnfw has several, section 6.3): the 5-px font object `FONT5` (look for the `pea`
before callers' `jsr textf`), the Bitmap vtable (word 0 of any stock Bitmap), the view list offsets inside
`drawAll`, the page-kind vector offsets (mk1: `view+124`, `+144` = index), the parameter descriptor table with its
name and formatter fields, and the chorus page's view vtable. dnfw has several of these for 1.11 (section 6).

**Three ways to relabel a parameter, cheapest first:**
1. Re-point a pointer: a name pointer in the parameter descriptor, or the value formatter (DigiFilter: `op: ptr` at
   `0x401539b2` on DT mk1, its routine prints its own strings for the new values and `jmp`s to the stock formatter
   for the rest; descriptor table `0x401a9d9c`, 0x34 bytes per entry, long/group/short name pointers at words
   10/11/12). One `ptr` site per name composes under elekloader's one-owner-per-byte rule (`docs/ADAPTING.md`).
   No surveyed mod patches a descriptor's name pointer directly, but it is the simplest option [inferred].
2. Hook the label routines (digistring on DT mk1: short/long name getters `0x4000fe8a`/`0x4000feac` hooked at their
   `lea 0x401a9d9c,a0`; value text `0x4000f324`; knob drawing `0x4000f2bc`, where it blits an icon in place of the
   knob; `digistring/glue.s` 103-221, `ui.c` 115-213).
3. Overpaint in `ev_draw` (DigiFilter's FLTR curve, digictl's pages). For pictures only, not for text that the
   firmware also shows elsewhere (popups, LFO destination lists).

**Two ways to add a page:** (a) append a page kind to a view's kind vector (reallocated with `operator new`) and
take that view's drawView vtable slot (digictl on DN mk1: master view vtable `0x40199BAC`, slot 4 at `0x40199BBC`;
its replacement calls the stock draw, then paints; pages switched by writing `view+144` and calling invalidate;
keys and encoders claimed only while that page is drawn); (b) core-dn1's page and parameter tables (pages 27-30,
`core_pages`/`core_params`, DN mk1 only; nothing like it exists in core-dt2 or our core-dn2).

**Pitfall: vtable slots have one owner.** DigiFilter patched the DT mk1 FLTR view's drawView slot (`0x401842a0`)
and lost it in a 9-mod build; it blames core's `ev_draw`. Our reading of `core.s` is that core only patches the
`jsr` at the draw site, and the elekloader linker refuses overlapping sites, so another mod (digimatrix) owning
the same slot outside elekloader's checks is the likelier cause [inferred]. Either way: prefer an `ev_draw`
overpaint or a pointer patch nothing else needs, over a shared vtable slot.

**Boot splash (Digitakt II 1.17, digisplash-ii; no licence file):** frames are raw 1,024-byte buffers inside the
depacked main OS: 8 animation frames at file offset `0x30339C` (+`0x40000400` at run time), a static asset at
`0x307B18`, a lookup table at `0x307718`, the player at `0x307750`; the player blits a Bitmap at `0x44F36D24` after
writing the frame pointer to `+0x10` (`digi2_splash_tool.py` 17-27, 139-166) [verified in the tool; layout claims
not checked against an image]. Its research document contradicts its own code twice (Bitmap pixels at `+0x0C` vs
`+0x10`; `0x2E0800` called a free "ROM cave" in the README and the "global font sheet, do not overwrite" in the
research document): treat it with care. octplane's tool documents the DT2 1.16 intro selector (`0x400D18AE`, a
table of 5 routines at `0x402A1624`, an LCG at `0x401521C4` that is never seeded). The mk1 splashes are generated in
code, so DigiSplash copies its own frames into the front buffer at the intro's present calls.

`digitakt2-perform` has no drawing code (its two mods change PERFORM KIT key handling and level restore; an earlier
PERSONALIZE checkbox row is gone from its history).

What it means for us:
- **T5.0:** relabel the chorus page's knobs by pointer patches (descriptor names or formatter), draw the flanger
  graphics from `ev_draw` with the DN2 routines above, and keep icons as static Bitmaps in the mod
  (`gen_icons.py`'s ASCII-art format is the easiest tool to copy). Confirm the table above in the emulator by calling
  each routine from a test mod's `ev_draw` (T1.9's `hello-marker-dn2` already does it for fillRect).
- **T6.2 (compressor pages):** digictl's kind-vector append is the only proven way to add pages without a core
  page table; on the DN2 it needs the master/FX view's vtable and kind vector found first.

## 5. Digitakt II knowledge bases, cross-checked against our DN2 notes

| repo | licence | last commit | covers |
|---|---|---|---|
| [Fadedlimes/My-personal-Digitakt-II-reverse-engeneering-knowledge-base](https://github.com/Fadedlimes/My-personal-Digitakt-II-reverse-engeneering-knowledge-base) ("KB") | none stated (no rights granted) | `d383048`, 2026-10-05 | DT2 OS 1.17: five long documents (hardware contracts, post-mortems, SHARC audio pipeline, a granular machine, boot/splash); credits "Atlin & Community" |
| [Fadedlimes/digisplash-ii](https://github.com/Fadedlimes/digisplash-ii) | none stated | `5ae9c64`, 2026-10-05 | DT2 1.17 splash tool; its research document is the KB's boot document, byte for byte |
| [lalzart/digitakt-ii-firmware-research-public](https://github.com/lalzart/digitakt-ii-firmware-research-public) ("LZ") | CC0-1.0 | `b2f93c1`, 2026-09-15 | DT2 OS 1.15C architecture, static analysis and simulation only |

Neither author mentions the Digitone II. The KB mixes solid observations with wrong mechanisms; LZ is careful and
mostly agrees with us. [verified: agent read all three in full; the items below cite their files]

**New to us, most useful first:**
1. **Appending to section 7 can hang the boot.** KB (post-mortems section 2, lines 22-29): a 2 KB LDR block appended
   after the end of section 7 froze the DT2 at the boot screen. Our DN2 1.12 listing (`out/T1.3/main.dis`) shows why
   it can happen: the upload routine `0x400cf4b0` sends the depacked length + 1 bytes one at a time over DSPI2, rejects
   anything over 1 MiB (`0x400cf710`, `0x400cf748`), then spins on `0xec094018` bit 4 (`0x400cf76e`) and on TCF with no
   timeout; after FINAL the SHARC stops taking bytes, so a block after FINAL never drains [listing verified; cause
   inferred]. Matches dnfw's fact 3 (section 6.1) and confirms our T3.1 plan to insert before FINAL. The STARTUP menu
   still recovers (T1.5). Also: our T3.1 plan fixes the FIRST block's argument (bytes to the end of the application);
   dnfw leaves it stock and its builds boot on the unit, so the slave-boot ROM seems not to need it [inferred].
2. **A second ColdFire -> SHARC data path (Rapid GPIO -> link port 0 / DMA30).** LZ (`docs/arch/publication-and-transport.md`
   112-131, `sample-resource-lifecycle.md` sections 4-5): `RGPIOBAR = 0x8c000035`, one byte on RGPIO15..8, strobe on
   RGPIO7, ready on RGPIO0; on the SHARC, source `0x51` -> LP0_RX/DMA30 -> event 4 over `0x401`-dword blocks, records
   into a 1,025-entry resource table, data in 4 KiB pages (DT2 samples). The DN2 1.12 sets `RGPIOBAR` at `0x400cf612`
   inside the DSP upload routine and writes `0x8c000002` at `0x400cf2dc`/`0x400cf368` [verified in our listing]. On the
   DN2 it probably carries bulk data (wavetables, transients) [inferred]. Relevant to G10/T8 and to dnfw's +Drive table
   loading, which uses the frame instead.
3. **How Elektron added a machine** (LZ `docs/arch/coldfire-runtime-and-objects.md` 58-64,
   `sharc-runtime-and-dispatch.md` 13-43): 1.10A -> 1.15 added Slice as identity 6 -> SHARC selector role 5
   (`0x1c6992`) plus a nine-row descriptor schema; 201 descriptor domains were renumbered +1; the 13 menu entries are
   presentation only. The KB's granular machine was written in place over the STRETCH slot (SW `0x1C6782-0x1C686E`,
   472 bytes) and calls the stock setter `0x1C4D88`; patching the shared selector-2 call `0x1C6AF1` silenced every
   machine. For T7.1/T8.2.
4. **More selas bugs** (KB post-mortems section 4, lines 43-77): `i7 = modify(i7, 4)` assembles to `0f17 0000 0000`,
   which sets I7 = 0 and crashes at once; `dm(imm, Rn)`, `comp(r0, 0)` and in some modes `lshift ... by 13` are
   rejected. With dnfw's negative-shift bug: **disassemble and byte-audit every selas output before splicing**.
5. **A dead DSP stops the sequencer** (KB post-mortems section 3: UI alive, PLAY does nothing). Same on the DN2 in
   dnfw's silent builds. Our T2.4b explains it: the sequencer clock is forced from the ColdFire's SSI audio ISR, which
   stalls without the DSP [inferred].
6. **Interrupt levels** (LZ `coldfire-runtime-and-objects.md` 163-178): source 44 at level 5, source 57 at level 2,
   i.e. our vector 108 (sequencer clock) and vector 121 (step handler). So our core-dn2's `ev_midi_step` handlers run at
   IPL 2 and can be pre-empted (add to T1.9's "Contexts").
7. **Frame-link pins** (LZ `publication-and-transport.md` 31-49): DSPI2 on PF6/PF5/PF4/PF3 = SCK/PCS0/SIN/SOUT, fSCK =
   fBUS/3: enough to probe the frame on a board with a logic analyser if ever needed.
8. Small: per-block status bits (bit 0 housekeeping, bit 1 processing) may explain the DN2's 3 vs the DT2's 2 in frame
   word 0 [inferred]; the DT2 saved-sample format (64-byte header, payload, 16-byte trailer).

**Contradictions (ours looks right unless said):**
- **Recovery keys, a safety point.** The KB (hardware contracts lines 71-72) says: hold FUNC at power-on, press Trig 3
  ("EMPTY RESET"), then Trig 4. Our T1.5 (from the rendered menu) and the owner's own recovery on 5 Oct: TRIG 4 (OS
  UPGRADE) alone; on the DN2 menu TRIG 2 = EMPTY RESET, TRIG 3 = FACTORY RESET. **Following the KB on a DN2 would
  select FACTORY RESET.** digisplash-ii's README agrees with us.
- Section 7 "must be exactly 321,016 bytes" and is uploaded by DMA with a "DSP Ready" interrupt (KB): our listing shows
  a computed length, byte-at-a-time PIO and no such interrupt; the hazard is real, the mechanism is not.
- Section 3 "must be exactly 3,275,616 bytes or the boot halts on a CRC": grown images boot through both loaders in
  our emulation (T1.9, T1.11); the boot path never checks the depacked image (T1.2).
- SHARC memory: the KB puts L1 at `0x20000000` and L2 at `0x28000000`; `0x20000000` is L2 and `0x28xxxxxx` the system
  alias of L1 (T3.2 section 0; their own formula 2·SW + `0x28000000` is the L1 rule).
- A "free" per-voice scratchpad at voice record `+0x104..+0x17F` (KB SHARC section 3): `+0x104` holds the 2:1
  decimator's state and `+0x17c..+0x17e` are declick flags (T3.1-sharc-db 4.2; their own granular document uses `+0x17d`).
- Machine table: the KB says entry 4 = Slice, 5 = MIDI; LZ (backed by the version diff) says Slice = identity 6 -> role 5.
- Sequencer pacing by DSP-sent vector-191 interrupts (KB): vector 191 is forced by the ColdFire's own SSI eDMA ISR (T2.4b).
- Bootloader "in SRAM at `0x02010000`", main CPU "runs Linux" (KB): DN2's bootstrap runs at `0x80000400`, the OS is its
  own RTOS (T1.5, T1.3). `0x02010000` is only section 2's ELE3 `dest` field; dnfw's `docs/bootstrap.md` reaches our
  base independently (`0x800003fc` with the 8-byte header, from the string `BOOTSTRAP UPGRADE`), though its
  `STATUS.md` retraction table still says the opposite.
- SHARC clock 500 MHz (KB): our T3.6 counts ~320k instructions per DN2 block, which cannot fit 333k cycles; dnfw
  measures 1 GHz. 500 MHz is wrong for the DN2 at least.
- LZ calls section 4 the "ColdFire updater"; our T1.2 found a factory board test with its own main-OS loader, entered
  only when boot-flag bit 6 is set. LZ identifies the DT2's ColdFire as MCF54415 from board photos; our T2.4b says
  MCF54418 for the DN2 (not verified on the DN2's board).
- digisplash-ii contradicts itself (section 4 above).

**Confirmations** (independent agreement): ELE3 checksum + crypto trailer and a writer that re-validates before
erasing (T1.2, T1.4); STARTUP menu over DIN only, a main-OS-only mod cannot reach the bootstrap (T1.5, T1.4); the
0xabc-byte full-duplex DSPI2 frame, eDMA 28/29, SPI mode 1, PCS0 (T3.2 3.4); LZ's "logical slot" = our mirror index
(`0x19 -> +0xda`, machine identity at `0x94+2n`) (T3.3); track stride `0x6a5` (T2.1); the source 44 -> 57 forced chain
(T2.4b); FreeRTOS Audio Task, SPI2 ping-pong, negated output, DMA10 -> SPORT4A (T3.2); 32 DT2 voice records of `0x1d8`,
6-tap interpolator `0x1c4f81`, 2:1 decimator `0xb80000`, selector at `+0x4c` (T3.1-sharc-db); 1,500 blocks/s. LZ also
proves zero reusable SHARC bytes statically; T3.2 likewise leaves run time open. We are ahead on recovery (LZ leaves
the reset path "opaque"; T1.5 closed it) and on the section-4 in-place hazard (T1.11).

What it means for us: **T3.8**: insert before FINAL, stay < 1 MiB, emulate the upload, audit selas output. **T5.0**:
see section 4 (blit at fillRect + 0xFBA = `0x40115adc` on the DN2, 135 callers per our function table). **T7/T8**:
LZ's machine-addition diff is the best model of Elektron's own method. **E1**: never follow the KB's recovery keys.
**T1.9**: note IPL 2 for `ev_midi_step`.

## 6. dn2_firmware_explore ("dnfw"): Digitone II mods on OS 1.11, DSP included

| | |
|---|---|
| URL | https://github.com/angellinares/dn2_firmware_explore (site: angellinares.github.io/dn2_firmware_explore) |
| licence | AGPL-3.0-or-later (ports MIT code with attribution, `THIRD-PARTY.md`) |
| last commit | `b989647`, 2026-10-06 (PR #193); created 2026-09-07; 1,272 commits by angellinares, 8 by xrcstrecords |
| what | Python tool `dnfw` (inspect, extract, build, patch, mods, cave finder, SHARC boot-stream walker) and a set of DN2 **1.11** mods with a browser builder that writes the same bytes; sibling project DNX (section 7) for data formats; uses digikit's emulators as tools and selache's assembler |
| hardware | owner flashes builds on a DN2; recovery over DIN proven 2026-09-08 (same route as our T0.3) |

### 6.1 DSP: how they put new code into the SHARC (our T3.8) [verified in their code and docs unless marked]

**It applies to our 1.12 byte for byte.** `src/dnfw/waverider/dsp.py` line 58 refuses any section 7 whose sha256 is
not `336e340a...c83115e2`, which is our 1.12 section 7 (I checked the full hash). Their agent-checked stock bytes at
every patched site match our loaded image (`out/T3.2/mem/*.bin`). Their ColdFire half is 1.11-only.

- **Memory:** code and state in **L1 block 1's tail, `0x2dd600-0x2e7000`** (`dsp.py` 62-136); fixed code addresses in
  short-word space `0x16eb00-0x170400`; a save area at `0x2dde00`; tables and counters `0x2de100-0x2e2000`;
  `0x2e2000-0x2e7000` still unused. Wavetables in DDR `0x80600000`/`0x80604000`; a load area `0x80800000-0x80a00000`
  filled at run time.
- **Boot stream:** new ADI blocks appended just before the FINAL block (`src/dnfw/image/bootstream.py` 376-418,
  `insert_before_final`); each header gets the `0xAD` signature and HDRCHK so the XOR of its 16 bytes is 0 (no payload
  CRC exists). Stock instructions are patched in place inside existing payload blocks (`write_span`; fill blocks cannot
  be written). Then aPLib recompression of section 7 and a fresh HMAC. The FIRST block's argument stays stock.
- **Toolchain:** hand-written SHARC assembly (`csrc/waverider/sharc/*.asm`) assembled with selache's `selas -proc
  ADSP-21569`; no linker: absolute targets for a fixed load address (`src/dnfw/image/sharc_object.py`,
  `scripts/sharc_resolve_jumps.py`). They never use selcc.
- **Hooks (all short-word addresses, all on our 1.12 image too):**

| hook | site | stock | change |
|---|---|---|---|
| machine dispatch (per voice) | `0x1c9448` | `i5=dm(-0x18,i6); r10=dm(-0x22,i6)` | 48-bit `JUMP 0x16f600` + NOP; the stub saves R0-R15, I0-I5, I12, M0-M4 with absolute stores, does its work, restores, re-executes the two displaced instructions, `JUMP 0x1c944c` |
| block entry mark | `0x1c9fb9` | `r4=0x268438` | JUMP to a stub that reads EMUCLK, re-executes the load, jumps back to `0x1c9fbc` |
| idle loop | L2 `0xb88abb` | `jump (pc,-0x10)` | JUMP to an idle-time counter, back to `0xb88aab` (saves R8-R11 itself: it can be pre-empted) |
| command table | `0x1c9d9f`/`0x1c9daa` | `i4=0x268a68`, bound `lshift by -2` | `i4=0x2dfa00` (a copied, longer table), bound `-3`: adds command 4 |
| machine lookup | data `0x25d75c` (`0x25d748[5]`) | 0 | 5 (a sixth machine type) |
| stock routine via an adapter | `CALL 0x1c6d4a` at `0x1c9611` | WaveTone render | target -> `0x16ed00`, a C-ABI adapter that saves registers and tail-jumps to the stock routine; survived on silicon |

  State at their splice points (`waverider-dsp-compare.md` section 1): L registers 0 except L6 = L7 = `0x1fd`;
  M6 = 1, M7 = -1, M14 = 1; SISD; RND32 = 1; only the ILOPI and PARI interrupts enabled, and both handlers **park the
  core at `0x1c07c6`, after which the SPORT ring replays a stuck ~750 Hz tone**: that is what a DSP crash sounds like
  on the unit (`waverider-dsp-silence.md` ~953-990).
- **Verification:** digikit's SHARC runner with 11 workarounds (`scripts/sharc_dn2_fixups.py`), a bit-exact Python
  reference (`src/dnfw/waverider/render.py`), a three-way decode check, a strict memory map
  (`scripts/sharc_strict_memory.py`), an encoding audit against stock (`scripts/sharc_encoding_audit.py`), then the
  unit: Waverider M5e passed on 2026-09-29, M9 to M10b through 2026-10-06. On the unit they find a DSP stop with
  "stage markers" written into reply words and read back over USB.

**Three hard facts we did not have, each enough to brick audio:**
1. **selas mis-encodes shifts by a negative immediate.** `R0 = LSHIFT R11 BY -16` assembles to `023e 0000 f00b`;
   stock has `023e 7800 f00b` (all 368 negative shift immediates in stock carry `0x78`, all 281 positive ones `0x00`).
   On the ADSP-21569 the selas form raises ILOPI and the core parks; **digikit's decoder, selmap and the runner all
   accepted it**, so every emulator gate passed (`waverider-dsp-compare.md` section 11, lines 680-723). Their fix is a
   post-pass (`scripts/sharc_waverider_m3.py` 114-149, `fix_shift_imm`) plus a byte audit against stock encodings.
   They list more selas disagreements with the firmware's encodings (pre-modify `DM(M4,I0)` outside a loop emitted as
   post-modify, `MODIFY(I,M)`, DO-loop mode bit 23, DAG1 M0-M3 in Type 3c, `USTAT1=imm32` widths). We use the same
   selache commit (`2b26d3b`). Our selcc output for the T3.11 effects has no negative-immediate shifts and no
   pre-modify forms (agent grep of `out/T3.11/sharc/*.s`), but libsel, our planned hand assembly (T3.11 section
   11.4) and **sharcvm itself** are exposed: `tools/sharcvm/translate.py` builds the shift count from
   `dataex<<8 | data8`, so it may accept selas's form as a large positive shift instead of trapping [inferred,
   unchecked].
2. **Part of our "free" L1 is cache.** The start-up (`0xb8b93a`, called from `__start`) does `SHL1C_CFG |= 0x414141`,
   enabling all three L1 caches, carved from the top of their blocks: **I-cache `0x39c000-0x39ffff`** (our "free L1
   block 3 tail", T3.2 2.4), **DM cache `0x2ec000-0x2effff`** (top of our "free block 1 tail"), **PM cache
   `0x31c000-0x31ffff`** (top of our "free block 2") (`waverider-dsp-silence.md` 270-299, from the PRM). Their first
   block-2 builds went silent, but the cause turned out to be fact 1, and later block-2 builds ran; the PRM still
   advises no code in data-cache blocks [verified theirs; our T3.2 is wrong on these three ranges].
3. **Section 7 must stay under 1 MiB.** The ColdFire's DSP loader (`0x400cf4f8` on 1.11) reads it into a 1 MiB buffer
   and refuses a stored section over 1 MiB (`0x400cf5ac`) or a boot stream over 1 MiB - 1 (`0x400cf5e4`); the DSP then
   never boots (no audio, no sequencer clock): a 1,400,876-byte build was silent (`dsp.py` 343-359). Stock is
   836,956 bytes, so new code and tables in section 7 have ~210 KB. Big tables load from the +Drive at run time.

**Also new to us:**
- **The voice path** (`docs/sharc-voice-path.md`, measured in the runner): engine state `0x241298`; 16 voice records at
  `0x2554b8`, stride `0x234`, with `+0x1b4` machine type (0 FM Tone, 1 WaveTone, 2 FM Drum, 3 Swarmer, 4 MIDI),
  `+0x1dc` filter type, `+0x22c` level, `+0x20c..+0x228` ADSR; per-machine state blocks (FM Tone `+0x8` stride
  `0x240`, WaveTone `+0x2408` stride `0x30c`, FM Drum `+0x54c8` stride `0x2e8`, Swarmer `+0x8348` stride `0x55c`);
  machine dispatch inside `0x1c8ef1` with the guard `compu(type,5)` at `0x1c905d` and a 5-entry setup table at
  `0x8052db90`; renders FM Tone `0x1c54c7`+`0xb82728`, WaveTone `0x1c6d4a`, FM Drum `0x1c778b`, Swarmer `0x1c85ad`;
  voice buffers at `0x804acf90 + 0x80t` (32 floats); per-voice chain `0xb8251b` -> x1.585 -> `0x1cdb56` -> DC
  blocker `0xb80c39` -> `0xb8265b` -> `0xb81368` -> filter -> amp `0xb80345` -> scatter `0x1c9ae7`; filters through
  `0x8052dbc0[n]` (render) and `0x8052dba4[n]` (setup), ColdFire value 0-5 -> n = 1,3,2,4,5,6 (Multimode `0xb82342`,
  EQ `0xb81a16`, LP4 `0xb80f2e` with a tanh table at `0x26b3a8`, Comb `0xb806f5`, Legacy `0xb81526`). Their "tracks"
  are voices: the note-on/off masks carry one bit per voice (agrees with our T3.3 "row = voice"). Everything is
  sized 16 and the frame has `0x3c` spare bytes, so 32 voices (G10) needs a new frame layout [inferred].
- **The frame, DSP side** (agrees with our T3.3): `2+2t` note, `34`/`36` note-on/off masks, `84+2t`, `116+2t` level,
  `148+2t` machine, `180+2t` filter, per-voice slots at `218+146t`; FX block 16 is copied to frame `+0x9fa` (same as
  ours). Command 4 (theirs): `4|count<<16`, destination, sequence, checksum, then up to 668 words, written to DDR
  `0x80800000+dst`, answer in reply word 6. Reply page: `+0x16` compressor gain reduction, `+0x1c..+0xa9c` 32
  records of 84 bytes the ColdFire reads.
- **DSP load (G6):** word 0 of the reply (the EMUCLK count we found in T3.2) is **one handler's span, not the frame's
  load**: ~416k cycles silent, and it fell after a Waverider note while every effect still played. Their reliable meter
  is idle-task time: `idle_load.asm` sums EMUCLK deltas under 4,096 cycles into reply word 1 (DSP `0x2c49d4`/`0x2c59d4`,
  ColdFire `0x800053a8` on 1.11); load = 1 - delta_idle / (delta_frames x 666,667). **Core clock 1 GHz** (CGU
  20 MHz x 100 / 2), 666,667 cycles per frame at 1,500 frames/s. **Measured on the unit: stock is flat at 63-65 %
  whether or not anything plays (~36 % free)**; stock engines do nearly all their work with no note playing;
  Waverider costs 3.5 points less than WaveTone; DDR tables cost +0.7 points over L1 (`docs/sharc-load.md`,
  `waverider-m10-move.md` 220-260). Read over USB PEEK on a probe build; nothing drawn on the unit's screen yet.
- **FX:** they have not read the DSP's effect routines ("the master chain on the SHARC is unread",
  `ideas-backlog.md` section 32); our T3.4/T3.6 are ahead there.

**Contradictions with our notes:**
- **L1 cache ranges** (fact 2): our T3.2 lists `0x39bffc-0x39ffff`, the top of `0x2dd52c-0x2effff` and of
  `0x300000-0x31ffff` as free; they are cache when the caches are on, and stock turns them on.
- **L2 tail:** they say data words in stock point into `0x200918c0`, `0x200c0008`, `0x200c1f84` ("do not relocate
  there", `waverider-dsp-silence.md` 90-91); our T3.2 found no *code* reference in `0x2008823c-0x200f9fff`. I
  searched our loaded images for those three values as 32-bit little-endian words: `0x200918c0` once in DDR at
  `0x804aa2e0` (inside transient entry 31's samples), `0x200c0008` in L1 block 3 code bytes (`0x38d398`) and L2 code
  (`0x20000d28`), `0x200c1f84` twice at L2 `0x2002319c` (data block 57) [verified]. The first three look like
  coincidences in samples and instruction bytes; the block-57 pair is a real candidate pointer [inferred]. Until a
  run-time watch settles it, keep new DSP data out of `0x200918c0-0x200c2000`.
- **The transient bank: our data says their model is off.** They model 34 entries of 4,800 samples from `0x8045c380`
  and say the bank has no index table (`src/dnfw/mods/transients.py` 52-67, `docs/tran-mapping.md`, "TRAN = 4 x slot -
  8", "32 of 34 reachable"). The DSP's own table at L1 `0x266d08` (T3.2; read at `0x1c8117`) lists **32 entries
  {DDR pointer, length}**: entry 0 at `0x8045b0c8` (2,400 samples, below their bank), entries 0-12 of mixed length
  (2,400 / 7,200 / 4,800), entries 13-31 of 4,800 ending at `0x804abe88` [verified: dumped from
  `out/T3.2/mem/l1b0.bin`]. So TRAN most likely indexes this table (TRAN = 4 x entry) and their uniform grid is right
  only for entries 13-31; their "+12 low-half bias" matches where their slots fall in our variable-length entries
  [inferred]. Their hardware test still passed because markers in each slot overlap real entries. This is a fact we
  could give them (with the owner's permission).
- **Clock:** our notes leave the SHARC clock open (T3.2 section 5); they derive 1 GHz from the init program's CGU
  settings and their per-frame cycle counts.

What it means for us:
- **T3.8 is mostly solved upstream, for our exact DSP image.** Use their method (append blocks before FINAL, patch in
  place, keep section 7 < 1 MiB, place code in L1 block 1 `0x2dd600-0x2e7000` below the DM cache, hook by a JUMP with
  full register save and re-executed displaced instructions, or retarget a CALL through an adapter), rebuild it in our
  own code (their code is AGPL; the facts are free), and add their two gates before T3.9: an **encoding audit of
  every assembled byte against the stock encodings** and the **cache map** in T3.2. Our first patch (a gain on the
  stock chorus) fits their "adapter around a stock CALL" pattern: retarget the FX bus's `CALL` to the chorus
  (`0xb84d5c`, T3.4) to an adapter that calls the stock chorus and scales its output.
- **T3.2:** correct the free-space table (cache ranges; L2 references to recheck); record the 1 MiB limit and the
  1 GHz clock.
- **T3.6:** make sharcvm trap on a shift-immediate whose top bits disagree with its sign (so it cannot pass what the
  silicon rejects), and check the other selas forms above against our decoder.
- **G6/T4.1-T4.2:** use idle-task time, not reply word 0; the readout already exists on their side over USB; our
  T4.2 (draw it on the screen) is still new work.
- **G10:** 36 % SHARC headroom with stock engines that run whether or not notes play; doubling voices is not
  "free CPU", it needs cheaper engines first [inferred].
- **T7.1/T7.2/T8.1:** their voice records, machine dispatch and filter tables are the map; Waverider is a working
  example of a sixth machine type end to end.
- **E1:** a DSP crash = silence or a stuck ~750 Hz tone, recoverable by reflashing stock over DIN like any OS.

### 6.2 ColdFire mods: midiarp, layermidi, the platform, safety (our T2.x, T1.x)

All dnfw addresses are 1.11. **Sections 2 (bootstrap) and 4 (updater) are byte-identical in 1.11 and 1.12**
(sha256 `038aa5d1...` and `951ced17...` in `out/versions/`) [verified], so our T1.2/T1.5/T1.11 findings apply to
their builds and their recovery experience applies to ours.

**midiarp = our T2.2 option 2, built and passed on a unit (1.11).** History (`docs/ideas-backlog.md` section 10,
~lines 1920-2316; `scripts/build_arp_on_midi*.py`, `build_arp_midi_play.py`) [verified, their docs]: v1 (one byte,
`beq.s`->`bra.s` at `0x4005f9da`) failed: it was the FUNC branch, not the MIDI gate; v2 (`bne.w` -> NOPs at
`0x4005f9c2`) opened the menu but each knob turn closed it and nothing arpeggiated; play v3 sent no MIDI at all
because the record's `+44` (sound pointer) was null and the MIDI task reads default velocity/length through it;
play4 passed on 2026-09-17 with a 128-byte N.LEN -> trig-length lookup (the arp table has twice the trig table's
resolution): N.LEN 1/32-1/4 within ~1 ms, no stuck notes over 5,248 messages. The shipped mod
(`src/dnfw/mods/midiarp.py`, `midiarp_code.json`) makes 7 edits; agent-mapped 1.12 equivalents (stock bytes checked in
our image for the first two) [verified bytes; the rest by masked reads]:

| 1.11 site | change | 1.12 |
|---|---|---|
| `0x400187f6` `67`->`60` | the ARP menu no longer closes itself on a MIDI track | same address (`67`) |
| `0x4005f9c2` `6600f58e` -> NOPs | the ARP menu opens on MIDI tracks | same address (`6600f58e`) |
| `0x400d905e`, `0x400d9edc` -> `seq_gate` | if arp MODE (sound `+0x15f`) != 0, a MIDI trig takes the synth producer and schedules the arp clock | `0x400d91c2`, `0x400da040` |
| `0x40121d86` key down, `0x40121916` key up (`jsr 0x4012b8b0`) -> `live_send` | arp on: route the key to the voice starter `0x40137d3c`, return the key's MIDI lock list to the pool (`0x4012a3b4`) | `0x40122064`, `0x40121bf4` |
| `0x400268fa` `jsr 0x400db524` -> `voice_hook` | the sink: per arp note record, take a MIDI record (only while 4 are free), fill note/velocity/length (`+44` never null; INF sent as 126), append to the ISR's batch at `fp-180`/`%a5` | `0x400268f8` -> `jsr 0x400db688` |

Their facts we lacked: `+44` must be non-null or the MIDI task dies; record flags at `+56`: bit 1 is **not** a gate,
bit 17 = layered copy (TRACK WILL TRIGGER), bit 18 legato, bit 19 arp step, bit 20 already voiced; **the MIDI record
allocator (`0x4012a408`, 1.12 `0x4012a6e8`) pops its free list with no empty check, and an empty pop corrupts low
memory** (their layer-midi2 build froze on the unit this way), so keep a reserve; hooking at the voice trigger uses
the ISR's own timestamp (avoids our T2.2b defect 3.1, the stale "now" bucket). What our T2.2-v2/T2.2b has that
theirs lacks [inferred]: a key released after the arp is toggled can stick (their key-up routes by MODE at release
time; our `cave_liveoff` sends the release to both engines); with the arp on, a MIDI track's sequenced trigs lose
their CC/PB/AT/program p-locks and the synth builder reads those lock slots as synth parameters; the 8-bit MIDI output
count (T2.1-verify 3.2). Status caveat: the hardware pass was the cave version; the current platform build with the
layered-copy filter and pool guard (2026-10-03) is emulator-only (`docs/mods.md`, mod 5).

**layermidi** (TRACK WILL TRIGGER onto a MIDI track plays it over MIDI): two hooks at `0x40026980` (`73f98000537e`)
and `0x40026d32` (`792a00022f04`), both at the same address and bytes on 1.12 [verified]; passed on the unit
2026-10-02/03. midiarp + layermidi is refused (notes doubled).

**More than 4 MIDI notes / more voices:** nothing in dnfw. Tangents: ColdFire load on 1.11 ~60 % CPU, audio ISR
average 56 %, peaks 75-83 % (`docs/coldfire-tasks.md`); layermidi sends every note of a layered chord (fits our T2.1:
the MIDI engine itself has no 4-note limit).

**Their "platform" vs our core** (`src/dnfw/mods/platform.py`, `csrc/runtime/loader.S`): replaces the start-up calls
at `0x4000053e` (the SRAM copies, then the BSS clear) with a 132-byte loader in a 138-byte cave (`0x4028da6e`), which
copies an appended `DNFW` area from the stock end (`0x4030b980`) to fixed RAM: lfo4 state `0x46700000-0x46702a00`,
bootscreen `0x46708000`, data window `0x46710000-0x46740000`, lfowaves, arpmodes `0x467a0000`, arpplocks, midiarp
`0x467d0000`, layermidi `0x467d8000`, reloadconfirm `0x467e0000`, lfo4 C code `0x46800000`, a relocated parameter
table `0x46900000`, Waverider `0x46a00000` (~390 KB), usbprobe `0x46f00000`; their RAM check stops at `0x47000000`.
- **They never touch our core's DDR window `0x47E00000-0x47F00000` or the SRAM tail**, but they do use our "free"
  `0x46800000-0x47E00000`.
- **Site collisions:** reloadconfirm's `0x4009863e` is our `ev_personalize` site (1.12 `0x400987a2`, same bytes
  `4cef7c7c0018`); Waverider edits the `isDirty` poll `0x4002e464` = our `ev_tick`; both projects append at the main
  OS's stock end and `platform.split` refuses appended data it did not write. Boot hooks are adjacent and would chain
  (ours at `0x40000538` returns to `0x4000053e`, theirs). midiarp's guards read the `jsr`s inside our `ev_midi_step`
  sites, so a port with their guards would refuse a core image. Coexistence needs one merged appended area and their
  hooks as core subscribers [inferred, feasible].
- **A RAM hazard in their layout** [bytes verified by the agent on 1.11; aliasing inferred as in our T1.3]: a 64 KB
  MMC DMA bounce buffer at uncached `0x4e6f1300` (code `0x4012c642-0x4012c680`, bound `cmpil #65536`) is cached
  `0x466f1300-0x46701300` on 1.11; lfo4's state at `0x46700000`/`0x46701000` sits in its last 4,864 bytes. Only a
  transfer over 60,672 bytes reaches it. **On 1.12 the DMA/bounce range is `0x466c0000-0x46709340` (our T1.3), so
  all their `0x4670xxxx` placements must move for a 1.12 port**; our `0x46800000` floor is right, and their "25.3 MB
  free above BSS" missed these alias references.

**Porting to 1.12: nobody has started** [verified: grep]. "os-112-support" in their docs is a tag meaning "re-find
this by its shape" (`docs/data-api-routes.md` 107, `docs/sequencer-playhead.md` 51, `src/dnfw/waverider/drive.py`
25); `docs/layer-midi.md` says "OS 1.12: not built". Agent's 1.11 -> 1.12 shift map (masked reads of
`out/versions/1.11` vs our 1.12): `0x40000400-~0x4007ffff` +0 (the ISR, the arp code, the UI gates); sequencer and
Data API `0x400c2xxx-0x400dcxxx` +`0x164`; MIDI task and UI library `0x4010xxxx-0x4018xxxx` +`0x2e0`; rodata
`0x401f7xxx` +`0x410`; strings `0x4020-0x4021xxxx` +`0x4b8`; other constants +`0x540`; BSS `0x4243-0x4467xxxx`
+`0x1000` [inferred from samples; re-find each address before use]. Their platform cave `0x4028da6e` is not free on
1.12 (a 138-byte zero run sits at `0x4028dfae`).

**Safety and flashing** (`docs/flashing.md`, `docs/bootstrap.md`, `docs/version-gate.md`): same recovery as ours
(FUNC at power-on, TRIG 4, DIN only, >= 12 min); their ~80 % stall was a USB MIDI interface going idle and dropping
packets (stock stalled too), fixed by changing the link: worth knowing before any DIN recovery. Failures on the unit:
a bootscreen stamp overlapping lfo4 state (`V04 M0 P46700000`), layer-midi2 freezing (empty MIDI pool), `EXCEPTION
DS0059` (a stock song row-count bug), a silent play v3. No downgrade gate on 1.11; HMAC checked at `0x400d2a60` with
the "Multiplier" key; "Incompatible OS" = header byte 8 != `0x10` (agrees with our T1.2 for 1.12). **They do not
check the section-4 in-place margin at `0x40200000`** ("the in-place check is one we do not run", `docs/references.md`
~728), nor the 2 MiB receive-buffer cap, protected ranges or flash writers. Their builds appended 23-172 KB to the
main OS with a packer ~1.4 KB tighter than stock, so several flashed builds very likely had a negative margin, which
only bites on a panel-timeout boot and is recoverable (T1.2, T1.11) [inferred arithmetic]. Our brick checker covers
what they lack.

**Storage (T2.3)** [their measurements]: in a stored sound, ids 4, 8, ..., 32 (bytes `+28 + 2*id`) are never written
by the serializer, ignored by stock load and zeroed by a stock re-save; a MIDI track's 359-byte sound slot stores the
arp bytes 324-353 and a separate 268-byte MIDI record sits at kit `+5964`; pattern lock records are `(u8 id, u8 track,
u16[128])`, 80 per pattern, ids 107-127 free in the file but mapped to slot 0 on stock load and dropped on stock save;
out-of-range enum values (arpmodes' MODE 5, 6) load as OFF on stock. The +Drive has no filesystem, fixed slots,
projects of 16 MiB with ~90 KB used, and large ranges stock never references (`docs/drive-storage-research.md`
section 1): Waverider keeps a side store at sector `0x600000` through a new Data API route. For T2.3 this is the menu
of places; the "zeroed by a stock re-save" rule is the price of each.

What it means for us:
- **T2.6:** not redundant (no 1.12 build exists), but it becomes "re-implement midiarp's proven design on 1.12 from
  the facts above, plus our key-release and p-lock fixes", hooked through our core where possible. Their code is AGPL;
  re-implement or ask. **T2.5:** guard the MIDI pool (reserve >= 4) in any mod that adds notes.
- **T1.9:** two of our core sites are hooks they also use (`ev_personalize`, `ev_tick`); if the projects ever merge,
  their mods would subscribe to our events. Note the IPL 2 context of `ev_midi_step` (section 5).
- **T1.3/E1:** add the MMC bounce buffer to the T1.3 map explicitly; keep mods above `0x46800000`.
- **Contact (owner's call):** useful things we could offer them, all facts: the bounce-buffer overlap, the
  `0x40200000` margin, the elekloader licence change, the 1.12 address map, the key-release caveat, the transient
  table (6.1), the cache map is theirs already. Their rules: owner merges, no attribution lines in commits or PRs,
  evidence with controls.

### 6.3 UI: the chorus page and how they draw (for T5.0)

[Agent verified the 1.12 addresses below against our image (`ele3_out/02_type3.unpacked.bin`,
`out/versions/1.11`); I re-checked the parameter records myself.]

**The chorus page.** The UI builds a RAM table of 44-byte page descriptors at boot (initializer 1.12
`0x400c9fba-0x400ca080`); **the Chorus page is descriptor 19**, at 1.12 `0x42433C00 + 44*19 = 0x42433F44` (1.11 base
`0x42432C00`): `+0` std::string "CHO" (`0x4022153a`), `+4` "Chorus" (`0x40210a96`, the header text drawn by the header
renderer `0x40063f56`), `+8..+40` the knob entries A..H and a 9th column: **A 105 DPTH, B 106 SPD, C 107 HPF, D 108
WDTH, E 0, F 0, G 109 DEL, H 110 REV, col 8 112 VOL. E and F are empty** (an id of 0 draws a dotted empty box). Desc
16 is the track FX page (G = entry 67 "Chorus Send"), 17 Delay, 18 Reverb.

**Parameter records, 1.12** [verified here]: record for entry e at `0x401f83a4 + 60*e` (1.11 `0x401f7f94`), short-name
pointer at `+0x30`, long name `+0x28`, page label `+0x2c`:

| entry | short | long | record (1.12) | short-name pointer |
|---|---|---|---|---|
| 105 | DPTH | Depth | `0x401f9c40` | `0x40210a9d` |
| 106 | SPD | Speed | `0x401f9c7c` | `0x40210a46` |
| 107 | HPF | High-pass | `0x401f9cb8` | `0x40210af8` |
| 108 | WDTH | Width | `0x401f9cf4` | `0x40210a1b` |
| 109 | DEL | Delay Send | `0x401f9d30` | `0x40210dd1` |
| 110 | REV | Reverb Send | `0x401f9d6c` | `0x40210c8a` |
| 111 | CHR | Chorus Mix Vol. | `0x401f9da8` | `0x40210c34` |
| 112 | VOL | Mix Volume | `0x401f9de4` | `0x4021a3fa` |
| 67 (FX page send) | CHR | Chorus Send | `0x401f9358` | `0x40210c34` |

All chorus records have page `0x10`, value slots 25..31 and the formatter `0x400e3030` (agent). The DEST browser's
group-name table (1.12 `0x401f7b04`, indexed by group - 5) still holds the `ERR` fallback (`0x40211156`) in group 16's
slot `0x401f7b30` [verified here]; it only shows once something lists chorus parameters as LFO destinations (dnfw's
fxmod hit it and points the slot at "CHR" on 1.11).

**How to rename (dnfw's lessons):** repoint pointers, never edit strings in place: they are shared ("DEL" is also the
Delay group's name, "CHR" is also entry 67's short name, "Chorus" is also the page label of records 105-112, and
"Chorus\0" is followed directly by "DPTH", so "Flanger" does not fit). Knob labels come from `getShortName` `0x400372da`
(returns `*(base + 60*entry + 0x30)`; called from the per-parameter drawer `0x400169de` and from `0x40064622`), so set
`+0x30` of entries 105-112 (and 67 for the send); the "Depth=64" line while turning reads `+0x28` through
`0x400365c8`; the header title is the std::string at desc 19 `+4` (and `+0` "CHO"): replace the pointer with a static
old-ABI COW string (`{len, cap, refcount = -1}` before the characters; refcount -1 = never freed; their
`src/dnfw/waverider/pages.py` line 131). Never change a record's page id (`+0x00`), slot (`+0x04`) or the descriptor
ids: their LFO4 renumbering probes broke parameters exactly that way. All these code addresses are the same on 1.12.
Alternatives: hook `getShortName`'s 10-byte prologue (`202f0008 0c8000000141`, resume `0x400372e4`), or overpaint in
`ev_draw` (fragile: the label under a turning encoder becomes a value readout). Other "Chorus" references whose users
are unknown: a second 60-byte table at 1.12 `0x401d1ebc-0x401d2024`, "CHORUS" `0x40215a35` from `0x4028c580`, "CHO"
from code at `0x4006f4fa`.

**Display path** (`docs/display-path.md`): 128x64 1 bpp, double-buffered 1,024-byte panel buffers (1.12 pointers
`0x402a10c8`/`0x402a10cc`, our T1.1); column-major, 8 bytes per column; Bitmap y counted up from the bottom
(pixel (x, Y) = bit `31 - (Y&31)` of the big-endian long at `data + 4*(x*stride + (Y>>5))`). Bitmap vtable 1.12
`0x402069c4`. The UI task polls `isDirty(screen)` (byte at screen+32) at `0x4002e464` (our `ev_tick` site) and redraws
with DRAWALL `0x4011d60a`; `setDirty` 1.11 `0x4011d2fe`; a page redraws ~1/s at rest and ~26/s while a knob turns,
~1.4 ms per page draw. One firmware font pinned: 1.12 `0x44508ed8` ("the grid's label font", inferred from 1.11
`0x44507ed8`); its glyph format is undocumented. Text cannot be dimmed (grey = 50 % dither).

**Pages and widgets:** descriptor accessor 1.12 `0x400c25d8` (base + 44*id, ids above 36 fall back); the view's page
vector at `+124/+128`, current page `+144`; `getColumnParameter` `0x40063f24`; the stock grid `0x40017428`; value
getter `0x4006538e(view, id, &flag)`; FX classifier (vtable `+0x54`) 1.12 `0x400dc0b2` claims pages `0x10-0x15`.
**Widgets are assigned per entry id, not from the record** (a mask `1<<(id-79) & 0x501405` at `0x4010e148`,
index arms at `0x4010e1d6`, a 68-byte companion table bounded at 321, 1.12 `0x4243425c`): a cloned record gets a plain
dial. Waverider's own pages hook the page count/reader `0x400c24d2`/`0x400c24ee`, the label fetch `0x40064622`, the
grid call `0x40018214` and the `isDirty` poll, and draw in C (`csrc/waverider/page.c`). Things that broke for them:
moving a page id (parameters read zero, stray LFO locks), widgets keyed by id, a missing index-3 arm, page dots one
frame late, a misread `bne.s` that hung the UI.

**Glyphs:** LFO glyph sets are a `std::vector<Bitmap>` at 1.12 `0x44508b68 + 12*WAVE`, 28x15, four variants, filled
lazily (`scripts/lfo_wave_glyph.py`); WaveTone icons are frames of a RAM sprite sheet drawn by `0x40016cee`. **The
chorus send glyph has not been located**; their method for the others: hook blit (`0x40115adc`) while the FX page
draws and log the source Bitmap and return address, or watch the panel cell. The boot screen mod jumps from the intro
copy (1.12 `0x400d39ea`) to a stamp that overwrites the source Bitmap (1.12 `0x42c4667c`) each frame; `font3x5.json`
is their own 3x5 font, not the firmware's.

**Tools:** screens from the unit over their usbprobe mod (SysEx `F0 00 20 3C 7D 00`, PEEK of up to 1 KB; needs a
probe build flashed), or, with no mod, syxgrid's RPC 0x04 Screenshot (section 7). In digikit: `scripts/emulib/panel.py`
taps and screenshots; load a build before running (Unicorn keeps stale translated blocks).

What it means for **T5.0** (most of the page work is already mapped for 1.12):
- Relabel the knobs by pointing `+0x30` (and `+0x28`) of records 105-112 at new strings, and desc 19 `+4` at a new
  "Flanger" COW string; fix group 16's DEST-browser slot (`0x401f7b30`) to a "FLG" string; draw graphics only from
  `ev_draw`. These are pointer `ptr` sites in elekloader terms (records are in rodata; the descriptor is in RAM, so
  desc 19 needs a runtime write from a core event, e.g. `ev_tick` once after boot) [inferred].
- **E and F are free knob slots on the stock chorus page.** A flanger needs FEEDBACK (the chorus has none), and maybe
  MANUAL/OFFSET: those two slots are the natural home, but they need new parameter records, storage and frame words;
  that is T5.1/T2.3 work, not a relabel [inferred].
- Find the chorus send glyph with the blit-logging method above.

## 7. elekloader upstream, modwerk, syxgrid and the rest

**elekloader** ([irpina/elekloader](https://github.com/irpina/elekloader), main `d644993`, 2026-10-06; our pin
`793b2e4`) [verified: `gh api .../compare/793b2e4...main`]. Ten commits since our pin: a **TypeScript port of the
engine** (`js/`, GPL-3.0-or-later, "byte for byte as the Python", checked by `js/tools/parity.ts`), a website "kit"
(`docs/INTEGRATING.md`) that modwerk now uses, shop entries (digitables, digi1_mods, digistring, digislicer 2.2),
core-dn1 2.3 (the Mod Menu as icons), and octabam's hooks on core-ot's bus. **No DSP-section support and no DN2**:
the kit's device list is `digitakt-mk1`, `digitakt-mk2`, `digitone-mk1`, `octatrack`; `docs/ADAPTING.md` still
says DSP code is out of scope. The shop (`web/catalog.json`, 62 items) has only Octatrack, Digitakt mk1 and
Digitone mk1 files. `elekloader-qa` (`7922d13`) is a one-commit snapshot of irpina's release-workflow branch (0.4.0),
with a "submit a mod" issue form; nothing for the DN2.
- **For C2/C3 (our DN2 profile, packer and core upstream):** `AGENTS.md` now requires every change to what the
  Python accepts or writes to be mirrored in `js/` with the parity run passing. Our `dense` packer, `more_stages` and
  `receive_max` (local branch `dn2-1.12`) will need TypeScript twins before a PR, and the DN2 profile must be added to
  `js/src/devices.ts`. Rebase our branch on main first (10 commits, mostly additive).
- **For C8:** still relevant; nothing upstream touches the section-4 in-place path.

**modwerk** ([repeat98/modwerk](https://github.com/repeat98/modwerk), `1077590`, 2026-10-06, GPL-3.0): the
Digitone II page (`sdk/machines/digitone-ii/machine.json`) is still `status: research`, steps format "started",
rebuild "open", boot "started", core "open", mods "open", crediting only digikit; the Digitakt II page is the same;
the FAQ says support "does not include Digitakt II or Digitone II" (`src/components/FaqPage.tsx` line 44)
[verified]. It does not list dnfw's or elekloader's DN2/DT2 results. Its octabam SDK has the DSP dynload described
in 2.5. For C6: when we have results, the DN2 page is the place, and it is clearly out of date.

**syxgrid-digitone-ii** ([xrcstrecords/syxgrid-digitone-ii](https://github.com/xrcstrecords/syxgrid-digitone-ii),
`e806863`, 2026-10-04; AGPL-3.0, its SysEx map `sysexmap/SYSEXMAP.md` CC BY 4.0; author also commits to dnfw):
a browser pattern editor for DN2 OS 1.10E/1.11/1.12 with a 45-entry SysEx map, each entry with a confidence level
[verified: three facts spot-checked in `sysex-visualiser.html` (preset +232, VoiceMenuView rows at kit +10265, RPC 0x04 Screenshot); the rest as an agent read them]:
- preset record 359 bytes, magic `BEEFBACE`; SYN machine byte at preset +232 (0 FM TONE, 1 WAVETONE, 2 FM DRUM,
  3 SWARMER, 4 MIDI); filter machine at +0xF5 (0 Multi-Mode, 1 LP4, 2 EQ, 3 Comb-, 4 Legacy LP/HP, 5 Comb+);
- kit voice setup: voice steal at kit +10264, per-track 5-byte rows at +10265 (VOICES 0 = dynamic or 1-16 locked,
  unison, amount, spread, reuse; firmware class `VoiceMenuView`): 16 is the stock ceiling per track;
- +Drive/RPC opcodes under `F0 00 20 3C 10 00` (read: 0x01 device, 0x02 version, 0x53-0x56 list/open/read/close;
  write 0x57-0x59; 0x5a-0x5e move/copy/clear/swap/rename; **0x04 Screenshot** returns the 1,024-byte screen over
  USB; 0x50-0x52 OS upgrade); a malformed Open froze a DN1 three times;
- OS 1.11 routines: `Midi::handleSysexDump` `0x40123a36`, song load `0x400dea6a` with an unchecked row count (>99
  halts with EXCEPTION DS0059, the bug dnfw's `songguard` fixes).
What it means for us: **T5.0 / T1.10 hardware tests:** RPC 0x04 gives screenshots from the unit with no mod, so
emulator-vs-unit screen comparisons need no photos (only after the owner agrees to send it anything; it is a read).
**T7.1/T7.2:** the machine and filter enums say where a new machine id would be stored (preset +232/+0xF5).
**G10:** per-track VOICES caps at 16; more voices means both the SHARC and this menu.

**DNX** ([NoiseAndMatter/DNX](https://github.com/NoiseAndMatter/DNX), `79288c1`, 2026-10-06, AGPL-3.0; Ángel Linares
García, the dnfw author): browser tools (expander DN1 -> DN2, pattern manager, preset library, +Drive backup) and
the decoded formats in `docs/dn2-format.md`, `dn2-pattern-format.md`, `dn2-song-format.md`, `sysex-format.md`,
`device-storage.md`, `sound-mapping.md` [verified: file list, README]. For **T2.3** (extra data without changing the
project format) these are the format references to read first; dnfw's STATUS says the sound format has a reserved
fourth-LFO rank and an unused `4*slot + 0` p-lock band, both found by DNX.

**Others found, not useful now:** `drumkilla/elektron-model-tweaks` (MIT; Model:Cycles/Samples 1.13 ColdFire tweaks
in image caves, `{off, old, new}` JSON, re-packs only changed aPLib ops); `angellinares/Model-TG` (Model:Cycles,
created 2026-10-06, a fork); `JGuzak/digi_battery` (hardware battery mod); `zooloo303/digi-roll*`,
`Steven-Robinson/midi-router` (MIDI tools, no firmware). `AvroraPolnareff/plock2sound` and `irpina/digitables`
are Digitone mk1 mods.

## 8. What to do next, ranked

1. **Owner decision: talk to dnfw's author before duplicating work** (no contact made). They have DN2 DSP code running
   on a unit, a working MIDI arp, and the same SHARC image as our 1.12; we have the 1.12 port knowledge, the
   in-place-margin and bounce-buffer safety findings, the transient table, the stock FX routines (T3.4/T3.6) and the
   elekloader DN2 core. Possible outcomes: they port to 1.12 with our map, we port with their permission, or both
   projects keep separate code and share facts. Mind the licences (Urgent 2).
2. **T3.8: adopt their splice method on our image** (6.1): blocks before FINAL, section 7 < 1 MiB, code in L1 block 1
   `0x2dd600-0x2e7000`, JUMP hooks with full register save or a CALL retargeted through an adapter (the shape of our
   chorus-gain first patch), and add two gates before any flash: a byte audit of every assembled instruction against
   stock encodings (selas negative-shift and modify bugs, sections 5 and 6.1), and sharcvm trapping on the bad shift
   form. Correct T3.2's free-space table for the L1 caches.
3. **T5.0: use 6.3 directly**: chorus = descriptor 19, records 105-112, pointer relabels, E/F free; draw with the DN2
   routines in section 4; find the send glyph by logging blit.
4. **T2.6: re-scope** to "midiarp's design on 1.12 + our key-release and p-lock fixes", with the MIDI pool guard;
   the 1.12 sites are in 6.2.
5. **G6/T4: measure SHARC load from idle-task time** (dnfw: stock 63-65 %, ~36 % free; reply word 0 is not the load),
   and ColdFire load with digihealth's context-switch timing (2.4).
6. **T9.1/T3.6: try digikit's `work/sharc-emulator`** with 1.12 before building our own coupled emulator; port its
   four ISA rules into sharcvm after checking them (section 3).
7. **E1/T1.3:** add the MMC bounce buffer to the memory map; never follow the DT2 KB's recovery keys (section 5);
   remember a DSP crash sounds like silence or a stuck ~750 Hz tone and stops the sequencer.
8. **Upstream (C1/C2):** rebase our elekloader branch on main and plan TypeScript twins for our Python changes; our
   digikit 1.12 work should be checked against angellinares's open DN2 PRs (#19, #29) to avoid a clash.
9. **T7/T8/G10:** dnfw's voice records and machine dispatch (6.1), LZ's machine-addition diff (section 5) and
   syxgrid's enums (section 7) are the map; G10 has ~36 % SHARC headroom with engines that run whether or not notes
   play, and every DSP structure is sized 16.
10. **Park:** capicola (ideas only), mk1 DSP mods (methods only), octabam's run-time DSP upload (an option for T3.10 if
    patching section 7 per effect becomes a burden).
