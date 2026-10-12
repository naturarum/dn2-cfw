# Elektron-CFW roadmap

Custom firmware for the Digitone II (OS 1.12), built on the community's tools (elekloader, digikit) and contributed back. Findings go in `HANDOFF.md`; this file holds goals and tasks.

**For agents:** claim a task by putting your name in its Owner cell before you start. Update Status when you stop, and record what you found in `HANDOFF.md`. Ask the user before any public action: forks, issues, PRs.

## Now
- **On the unit (2026-10-12):** EF01 (T1.8), **FLG1, our flanger in the chorus slot (T3.9, M3): "it works perfectly"**, the first new code on the Digitone II's DSP, and **FTD1 (+ the tape delay in the delay slot): works**; the user: the delay sounds "not amazing, but that's not an issue", and the TIME repitch is "very interesting". The Space Echo (T5.2) is the delay's next design.
- **Next candidates, cleared for the user:** FTD1 (FLG1 + the tape delay in the delay slot; ACCEPT 12/12 with the strengthened DSP gate, guide `out/T3.8/FLASH-FTD1.md`); DH01 (the core + a MIDI test mod, M1). DH01 comes before any screen change: labels and glyphs run through the core.
- **Order agreed with the user (2026-10-12), to avoid fanning out:** FTD1 and DH01 on the unit -> rename the pages (Flanger, Tape; T5.0's mod, paused 2026-10-06, resumes since dnfw is not collaborating yet) -> grow the parameter table (E/F knobs, second pages) with new glyphs for the new rows -> the 3-band compressor.
- **Then:** the flanger's fixed knobs (feedback, manual) and FLG2 (the friend's in-loop ideas), after listening on the unit; multi-page FX UI (T5.0/T6.0, parameter-table growth) on the ColdFire side.
- **Public (2026-10-12):** this project is public as github.com/naturarum/dn2-cfw (GPL-2.0-or-later, fresh history). Our digikit and elekloader `dn2-1.12` branches are public forks. Pull requests and issues upstream (C0-C8) still need the user's go for each.

## User priorities (2026-10-05)
- **Tier 1:** G5 arp on MIDI tracks, G4 MIDI polyphony, G10 more synth voices (32 voices, not more tracks; corrected by the user 2026-10-05).
- **Tier 2:** the effects and the compressor (G1-G3, G9), and new machines (G7, G8).
- **Tier 3:** G6 CPU monitor. The CPU limits will show up naturally while pushing polyphony.
This overrides the P-levels in the task tables where they disagree.

## FX designs (user, 2026-10-05)
The user wants to hear new effects A/B against stock; offline rendering is fine.
- **Reverb (G1):** more flexible, room to cathedral, more control over reflections, within the 8 knobs of the page.
- **Delay (G2):** a better tape delay: wow and flutter, crackle, maybe 1-2 extra taps.
- **Chorus slot (G3):** most important: a swelly flanger, in the spirit of Max/MSP's yflanger. The user will send notes on how it works; build a CPU-friendly version.
- **Master compressor (G9), the main piece:** 3 bands, 3 subpages (one per band): band edges, ratio, threshold, attack, release per band; one sidechain setting for all bands; then see what room is left.
- **Decisions after listening round 2 (user, 2026-10-06):**
  - AGE sounds right.
  - Resonance is right; the maximum may stay, and the musical range sits below it.
  - **Two pages per effect.** Better still, a general multi-page mechanism so the page count stops being a constraint and no two parameters share one knob.
  - The flanger keeps its 77 ms range. Push the DSP towards its limit and cut back only if it gets unfriendly.
  - **Compressor:** one page per band. The crossover knob sits on the LOW page (low/mid) and on the MID page (mid/high); the HIGH page shows an empty box in that slot, as Elektron boxes do for unused knobs.
  - **Placeholder glyph:** every new parameter uses the stock default value dial, the round knob of the MIDI CC pages (drawer `0x400c0986`, strip `0x4464680c`, 96 frames, 19x17). Custom glyphs come last, once the parameters are settled.
  - Send effects need no p-locks (no FX track on the DN2). Any new synth or filter parameter must be p-lockable.
- **How to listen (T3.11):** write each effect in portable C, render it on the Mac at full quality from audio files, A/B against recordings of the stock unit (T3.5). The SHARC emulator then checks that the SHARC build matches the Mac build and fits the cycle budget; it is not the listening tool.

## Goals

| id | goal | status |
|---|---|---|
| G1 | Swap the reverb | not started |
| G2 | Swap the delay | not started |
| G3 | Swap the chorus | not started |
| G4 | MIDI machine: more than 4 voices | not started |
| G5 | MIDI machine: arpeggiator | not started |
| G6 | CPU monitor | not started |
| G7 | New synth machines | not started |
| G8 | New filter machines | not started |
| G9 | 3-band master compressor | not started |
| G10 | More synth voices, for example 32 instead of 16 (not more tracks), up to where the DSP's CPU runs out | not started |
| E1 | Safety: no hard bricks, and every soft brick recoverable | in progress |
| E2 | Simulator: test without the unit | in progress (stage 1: digikit boots 1.12) |
| E3 | Contribute Digitone II support upstream | local branches ready (digikit and elekloader, dn2-1.12); nothing pushed |

## Sizes and priorities

- **Sizes:** S = one working session · M = a few sessions · L = a week or two · XL = several weeks. A **?** marks research: the size can move a lot once we know more.
- **Priorities:** P0 = safety, done before any modified flash · P1 = on the way to the first features · P2 = effects and compressor · P3 = new machines.

## Milestones

| id | milestone | done when | tasks | size | pri | status |
|---|---|---|---|---|---|---|
| M0 | Safe to flash | recovery tested on the unit; brick checker passes; an unchanged rebuild flashed and reverted | T0.1–T0.3, T1.1–T1.8 | L | P0 |
| M1 | First mod | a small mod runs on the unit through the Digitone II core | T1.9–T1.10 | L | P1 |
| M2 | MIDI machine | G4 and G5 work on the unit | T2.* | L | P1 |
| M3 | First DSP patch | new code audibly changes the stock chorus on the unit | T3.* | XL? | P1 | **reached 2026-10-12: FLG1, the flanger in the chorus slot, works on the unit** |
| M4 | CPU monitor | G6: DSP load on screen | T4.* | M | P1 |
| M5–M7 | Chorus, delay, reverb | G3, G2, G1 on the unit | T5.* | 3 × L | P2 |
| M8 | 3-band compressor | G9 on the unit | T6.* | L | P2 |
| M9 | New filter machine | G8 on the unit | T7.* | L–XL? | P3 |
| M10 | New synth machine | G7 on the unit | T8.* | XL? | P3 |

There are two tracks, and they can run in parallel:
- **Track A (ColdFire: loader, core, MIDI):** phases 0–2, then 4.
- **Track B (DSP: research, simulator):** phase 3. Its static work (T3.1–T3.4) needs nothing from Track A, so a second agent can start it any time.

## Done (2026-10-05)
- File format understood. Stock 1.12 rebuilds byte for byte, and a recompressed rebuild passes every offline check.
- HMAC key derivation confirmed against the bootstrap's own code.
- Bootstrap: separate from the OS. It only upgrades itself for a higher version (`0x80001c72`), and it has no USB code, so recovery is DIN MIDI only.
- Prior art surveyed and cloned (`VENDOR.md`). Git set up.
- Earlier sessions: RTTI class map, SHARC+ chip identified.
- **Recovery works on the unit (T0.3):** STARTUP menu OS UPGRADE with stock 1.12 over DIN (MOTU M2), done by the user 2026-10-05.
- Digitone II 1.12 boots in digikit's emulator; the unit's own loaders and validators accept rebuilt files (T1.1, T1.2).
- Flash-writer audit and a proof that the STARTUP menu survives any stored OS, both independently checked (T1.4, T1.5).
- Digitone II 1.12 support in elekloader, on a local branch (T1.3, T1.6).
- Brick checker built (T1.7) and a version-name-only candidate, EF01, built and accepted (T1.8). Not flashed yet.
- Stock 1.12 moved into `elektron-firmware/`.
- **First own build on the unit (T1.8, 2026-10-12):** EF01 (stock 1.12 renamed, resealed) flashed over USB and works. The first hardware result.
- **First new DSP code on the unit (T3.9, M3, 2026-10-12):** FLG1, the flanger in the chorus slot, flashed over USB; the user reports it works perfectly.

## Phase 0: hardware prep (user)

| id | task | owner | size | pri | needs | status |
|---|---|---|---|---|---|---|
| T0.1 | Get a 5-pin DIN MIDI cable: MOTU M2 MIDI OUT to Digitone II MIDI IN | user | S | P0 | | done |
| T0.2 | Back up the +Drive with Elektron Transfer | user | S | P0 | | in progress |
| T0.3 | Recovery dry run: hold FUNC while powering on, then TRIG 4 (OS UPGRADE). Send stock 1.12 from Transfer's SYSEX TRANSFER page ("OS upgrade via device startup menu") over the M2. About 13 min at DIN speed | user | S | P0 | T0.1, T0.2 | done 2026-10-05: worked, unit back on stock 1.12 |

## Phase 1: Digitone II on elekloader, the brick checker and the base (Track A)

| id | task | owner | size | pri | needs | status |
|---|---|---|---|---|---|---|
| T1.1 | Port digikit's emulator to 1.12: boot to the main screen | dn2-fw session | M | P0 |  | done: boots to the main screen (notes/T1.1) |
| T1.2 | Run the unit's own depacker and validator (checksum, build floor, seal) on a rebuilt 1.12 in the emulator | dn2-fw session | S | P0 | T1.1 | done: device accepts rebuilds; section-4 in-place hazard found (notes/T1.2) |
| T1.3 | Measure the 1.12 facts elekloader needs: staging address, flash layout, reset copy and clear ranges, free memory (write watches) | dn2-fw session | M | P0 | T1.1 | done: free DDR 0x46800000-0x47F00000, SRAM 0x8000F980-0x80010000 (notes/T1.3) |
| T1.4 | Flash-writer audit: find every caller of the OS's flash write and erase routines, and prove none can reach the bootstrap | dn2-fw session | M | P0 |  | done, with verifier corrections (notes/T1.4*) |
| T1.5 | Prove the STARTUP menu doesn't depend on the main OS: read the bootstrap's power-on path, then emulate it with a broken main OS | dn2-fw session | M? | P0 | T1.1 | done, independently checked (notes/T1.5*) |
| T1.6 | elekloader: Digitone II 1.12 profile, byte-exact writer, tests (`vendor/elekloader/docs/DEVICES.md`) | dn2-fw session | M | P0 | T1.2–T1.4 | done on local branch vendor/elekloader dn2-1.12; tests pass (notes/T1.6) |
| T1.7 | **Brick checker:** one command that runs every check below on a candidate `.syx` and refuses on any failure | dn2-fw session | M | P0 | T1.2–T1.6 | built, self-test passes (notes/T1.7-brickcheck.md); hardening open, see T1.7b |
| T1.7b | Close the gaps the red team found in the checker (notes/T1.7-redteam.md), then rerun the self-test and the red-team cases | dn2-fw session | M | P0 | T1.7 | done: gaps 1-4 closed (notes/T1.7b-hardening.md). Check 6 pins the in-OS validator closure; new check 11 validates a changed section 7 (DSP) against --intended-dsp (size, boot-stream parse, cache ranges, the selas bad-shift form, a sharcvm dynamic run); check 7 is a tripwire + catches the computed-address idiom; new check 12 is a dynamic in-OS upgrade flash watch. The 3 red-team files that passed are now refuse/incomplete/refuse; 6 new DSP self-test cases pass |
| T1.8 | First flash: an unchanged rebuild with its own version name, sent via Transfer over USB. Check the version on the unit, then flash stock back | user + agent | S | P0 | T0.3, T1.7 | **done 2026-10-12: EF01 flashed over USB and confirmed working on the unit (user)**. Our rebuild/seal/flash path is proven on hardware |
| T1.9 | Port elekloader's core (the hook bus several mods share) to Digitone II 1.12 | dn2-fw session | L | P1 | T1.6 | done in the emulator only: core-dn2 0.1 on elekloader dn2-1.12 (uncommitted), core-dt2's 7 sites + ev_midi_step/ev_midi_note; test mod transposes MIDI notes as intended, screens and timing as stock; brick checker accepts both builds; not flashed (notes/T1.9-dn2-core.md) |
| T1.10 | First mod through the core, in the emulator and then on the unit | user + agent | S | P1 | T1.8, T1.9 | emulator done (T1.9: hooktest-DH01 rewrites MIDI notes as intended). Candidate `out/T1.9/build/hooktest-DH01.syx` passes all 12 hardened brick checks (2026-10-12); the user flashes it, guide `out/T1.9/FLASH-DH01.md` |
| T1.11 | Check whether elekloader's Digitakt II 1.17 builds have the same section-4 in-place margin problem (stock DT2 1.17 from Elektron, device loader under Unicorn) | dn2-fw session | S | P0 | T1.2 | done: DT2 stock fails that path itself; elekloader builds pass (notes/T1.11) |

The brick checker's checks (T1.7):
- only sections 3 (main OS) and 7 (DSP) may differ from stock; sections 2, 4 and 8 and the bootstrap version must be unchanged
- the seal and checksums pass the unit's own code (T1.2)
- the unit's depacker yields exactly the intended main OS (T1.2)
- the image boots to the main screen in the emulator (T1.1)
- the STARTUP menu is still reachable (T1.5)
- nothing writes to the bootstrap's flash during emulated sessions (T1.4)
- a report of every changed byte

## Phase 2: MIDI machine (Track A)

| id | task | owner | size | pri | needs | status |
|---|---|---|---|---|---|---|
| T2.1 | Find the MIDI machine's 4-voice limit (voice states, note pool), and whether patterns can store more notes per trig | dn2-fw session | M? | P1 |  | done: the 4 is UI-only, N>4 is code-only (notes/T2.1*) |
| T2.0 | Unit test, no flash: on a MIDI track, add more than 4 notes to one trig with NOTE EDIT and watch MIDI out (MOTU M2 + a MIDI monitor). Does stock already send them all? | user | S | P1 | | todo |
| T2.2 | Find why the arp doesn't drive MIDI tracks | dn2-fw session | M? | P1 |  | done: design options, none built (notes/T2.2-arp-midi-v2.md, notes/T2.2b-verify-1.md) |
| T2.3 | Decide where extra data lives without changing the project file format, so projects still open on stock OS | | S | P1 | T2.1 | todo |
| T2.4 | Emulator: live keys and encoders from the computer, plus the emulated unit's MIDI as a macOS MIDI port | dn2-fw session | M | P1 | T1.1 | done: keyboard (T2.4a); DIN MIDI = UART9 modelled, out logged/.mid/virtual CoreMIDI port, MIDI in, LEDs drawn (T2.4c, notes/T2.4c-midi-leds-speed.md). Not yet played by a person |
| T2.4b | Speed up the emulator: profile where the time goes (Python hooks, timers, screen updates) and fix the hot spots, aiming at real time for live play. Starts when T2.4a (keyboard control) is done | dn2-fw session | M? | P1 | T2.4 | audio clock done 2026-10-05 (notes/T2.4b-audio-clock.md); speed 2026-10-06 (T2.4c): 0.82x real time with the clock on (was 0.10x), 8x without (was 1.95x), timer-tick bug fixed. Real time with the clock not reached: guest frame work under Unicorn |
| T2.5 | Mod: more than 4 voices | | M–L? | P1 | T1.9, T2.1, T2.3 | todo |
| T2.6 | Mod: arp on MIDI tracks | | M | P1 | T1.9, T2.2 | todo |
| T2.7 | Test both in the emulator and on the unit; publish them as elekloader mods | user + agent | S | P1 | T2.4–T2.6 | todo |

## Phase 3: DSP foundations (Track B)

| id | task | owner | size | pri | needs | status |
|---|---|---|---|---|---|---|
| T3.1 | Build selache; build digikit's SHARC database for 1.12 | dn2-fw session | S | P1 |  | done: selache built; 1.12 DSP = 1.11 (notes/T3.1*) |
| T3.2 | Map the SHARC boot stream into memory: code, data, free space | dn2-fw session | M | P1 | T3.1 | done: loader `scripts/t32_load.py`, memory map, free L1 block 2 + L2 tail (static), audio pacing = SPORT4 DMA, sharcemu not usable as is (notes/T3.2-sharc-memmap.md) |
| T3.3 | Decode the ColdFire-to-DSP parameter frame for Digitone II: which bytes are which knobs | dn2-fw session | L? | P1 | T1.1 | done for the FX and master parameters (notes/T3.3-fx-frame.md) |
| T3.4 | Find the chorus, delay, reverb and master compressor in the DSP code: entry points, state, parameters | dn2-fw session | L? | P1 | T3.2 | done, independently checked: chorus 0xb84d5c, delay 0xb8482c, reverb 0xb836bc, compressor 0xb8541d (notes/T3.4*) |
| T3.5 | Reference recordings: the stock effects with fixed settings, into the MOTU M2 | user + agent | S | P1 | T3.4 | todo |
| T3.6 | Simulator stage 2: run one DSP routine on an audio buffer on the laptop (build on digikit's `tools/sharcemu.py`) | dn2-fw session | XL | P1 | T3.2, T3.4 | done: own SHARC+ interpreter (`tools/sharcvm/`, C core, 77-93M insn/s); 2,036/2,039 selache C tests pass; stock delay, chorus, reverb, compressor (3-8x real time) and the whole block handler (0.19x, no voices) render to WAV; selcc builds of T3.11 effects match native after a selcc bug workaround (notes/T3.6-sharc-interpreter.md) |
| T3.7 | Validate the simulator against T3.5's recordings | | M | P1 | T3.5, T3.6 | comparison script ready and self-tested (`scripts/t37_compare.py`: lag, drift, level, residual, third-octave spectrum per take); waits for the T3.5 recordings |
| T3.8 | Splice method: compile C with selcc, place it in free DSP memory and hook it. First patch: a gain change on the stock chorus | dn2-fw session | L? | P1 | T3.2, T3.4 | done 2026-10-12, emulators only: `tools/dspsplice/` (selcc -> fixed-address link -> blocks before FINAL, CJUMP retargets, register-saving stubs, encoding audit). CG01 (stock chorus -6 dB), FLG1 (flanger in the chorus slot, SHARC = Mac bit for bit, +0.4 % of a block), FTD1 (+ tape delay, +2.8 %); all three brickcheck ACCEPT 12/12 with --intended-dsp (notes/T3.8-dsp-splice.md) |
| T3.9 | First DSP patch on the unit (M3) | user + agent | S | P1 | T1.7, T1.8, T3.8 | **done 2026-10-12: FLG1 (`out/T3.8/FLG1/FLG1.syx`, sha256 `72c2690c...efed6`, the flanger in the chorus slot) flashed over USB; the user: "it works perfectly"**. Guide `out/T3.8/FLASH-FLG1.md` |
| T3.11 | FX workbench on the Mac: the new effects in portable C (same source later built with selcc), a renderer for WAV files at 48 kHz in 32-sample blocks like the DSP, and A/B against stock recordings (T3.5) | dn2-fw session | M | P1 | | tape delay and 3-band compressor done: C in `fx/`, 76 tests pass, A/B page `out/T3.11/ab.html`; selcc builds are 7-9x the stock cost, need restructuring (notes/T3.11). Flanger waits for yflanger notes; stock A side waits for T3.5 (notes/T3.5-recording-plan.md). 2026-10-06: the page also has the stock effects emulated (T3.6) at the closest knobs; SHARC cost measured and cut ~2.2x, output bit-identical (delay 18.6k, compressor 47.7k insns/block; stock 5.4k, 16.6k); below stock needs hand assembly of 5 loops (notes/T3.11 sections 10-11). 2026-10-06 later, after the user's listening (section 12): one-head tape delay with AGE, resonant loop filters, WID; each compressor band = the stock compressor's design (measured in the emulator), MUP, one sidechain; flanger in the chorus slot; 155 tests pass; SHARC = Mac (flanger bit-identical); delay 19-24k, comp 35-52k, flanger 16-18k insns/block (~7 % of a block in all); 1-page/2-page layouts proposed; renders and page redone |
| T3.10 | Propose DSP-section mods to elekloader's maintainer. Its mod format can only change the main OS today | | S | P1 | | todo |

## Phase 4: CPU monitor (G6)

| id | task | owner | size | pri | needs | status |
|---|---|---|---|---|---|---|
| T4.1 | Measure DSP load: a cycle count around the audio task, and idle time | | M | P1 | T3.8 | todo |
| T4.2 | Send the number back to the ColdFire (spare room in the DSP-to-ColdFire frame) and draw it through the core | | M | P1 | T1.9, T3.3, T4.1 | todo |
| T4.3 | ColdFire load (idle-loop time) on the same display | | S | P2 | T1.9 | todo |

## Phase 5: effect swaps (G3, then G2, then G1)

| id | task | owner | size | pri | needs | status |
|---|---|---|---|---|---|---|
| T5.0 | Flanger page UI: how the DN2 draws the chorus page, its parameter labels and glyphs, and the chorus send icon; extract them; prove a renamed/redrawn page in the emulator | dn2-fw session | M? | P2 | T1.9 | paused 2026-10-06 (overlap with dn2_firmware_explore; owner contacting its author). Found: fonts, glyph strips, the tables behind each knob, the chorus send glyph; relabel + 3 new glyphs shown by hot-patch; 2 pages per send FX: yes on the UI side, built but not yet cold-booted (notes/T5.0-flanger-page-ui.md) |
| T5.0b | Rename the FX pages to match the new effects: Flanger/FLG, Tape/TAP(E), on every screen that shows them; no new knobs or glyphs | agent | S | P1 | T5.0, T1.9, T3.8 | done 2026-10-12, emulator: **FTN1** = FTD1's DSP + core-dn2 + fx-rename (29 data-only name repoints, elekloader fork `examples/fx-rename-dn2`), `out/T5.0b/FTN1.syx` sha256 `60d1cd1f...58e8`, brickcheck ACCEPT 12/12; 28 screens: only renamed words differ, parameter mirror and DSP frame byte-identical to stock (notes/T5.0b-rename.md). Cleared for the unit after FTD1 and DH01; guide `out/T5.0b/FLASH-FTN1.md` |
| T5.1 | Chorus slot: a basic, swelly flanger (user 2026-10-06: "for now maybe let's just build a basic flanger"), in C, simulated, then on the unit | | L | P2 | T3.7, T3.9 | **on the unit 2026-10-12 as FLG1** (DPTH/SPD/HPF/WDTH on the stock chorus knobs; FDBK +68 % and MAN 0.9 ms fixed; the user: it works perfectly). C version 2026-10-06 (`fx/flanger.c`: 0.05-77 ms, damped feedback/Karplus-Strong, 6 LFO shapes + slew, sync, stereo phase and offset, polarity). Next: more knobs need the second page (T5.0/T6.0); FLG2 with in-loop saturation |
| T5.2 | Delay: a Roland Space Echo (RE-201) style echo (user 2026-10-12, replaces the one-head design): 3 heads chosen by MODE; TIME, MODE, INTENSITY, AGE, CENTER, BW, WIDTH, PAN | agent | L | P2 | T5.1 | C version done 2026-10-12 (`fx/spaceecho.c`, notes/T5.2-space-echo.md): 282/282 tests, A/B renders `se_*` beside stock and FTD1, SHARC = Mac within -108 dB, 18-38k instructions/block (1-3 heads), state fits the stock delay's DDR. Next: the owner listens and answers the open questions; then the page layout (6 stock rows map, MODE and AGE need 2 more) and the splice |
| T5.3 | Reverb | | L | P2 | T5.1 | todo |

Each new effect is its own mod, so you choose one at build time and only one is loaded at once. Port open designs where the licence allows; octabam's `THIRD_PARTY.md` lists its sources and their licences.

## Phase 6: 3-band master compressor (G9)

| id | task | owner | size | pri | needs | status |
|---|---|---|---|---|---|---|
| T6.0 | FX parameter room: frame words and kit storage for extra pages | dn2-fw session | M | P2 | T3.3 | paused 2026-10-06 (overlap with dn2_firmware_explore; owner contacting its author). Found: 32 free frame words (30-word tail + 2), proven end to end in the emulators; 28 reserved words per kit FX record for storage (notes/T6.0-fx-params-room.md) |
| T6.1 | Can the parameter table grow? (study; user 2026-10-12: "I took it as off limits") | agent | M | P1 | T5.0, T6.0 | done 2026-10-12, emulator and static only (notes/T6.1-table-growth-study.md): **yes, with conditions**. Row ids are never saved (projects store slot k); 114 same-length operand edits (56 base refs, 58 count compares); relocated + 8 rows in the emulator: 25 screens byte-identical to stock, new knobs draw, turn and save. Conditions: resize `ParameterUpdateCache` before new rows get CCs; set the no-lock display flag on FX rows (else a p-lock lands on sound slot k); per-row rules (section 3.4). Not yet: cold boot of a grown build, +Drive save/load, Overbridge. Cheapest delay-page knob: stock row 0x7a "Delay FX Routing" (k 40) on the empty G slot (reverb: 0x83, k 48), no growth |
| T6.4 | Map the stock master compressor: DSP routine, parameters, UI page | | M | P2 | T3.4 | todo |
| T6.2 | Design: crossover plus three compressors. It needs more knobs than the stock page has, so a new page via the core | | M | P2 | T6.4, T6.1 | todo |
| T6.3 | Build, simulate, test on the unit | | L | P2 | T6.2, T5.1 | todo |

## Phase 7: new filter machines (G8)

| id | task | owner | size | pri | needs | status |
|---|---|---|---|---|---|---|
| T7.1 | Machine and filter slots on the ColdFire for Digitone II (digikit got a copy of an existing machine into the Digitakt II's list) | | M | P3 | T1.9 | todo |
| T7.2 | How the chosen filter reaches the DSP, and how the DSP dispatches it | | L? | P3 | T3.3, T3.4 | todo |
| T7.3 | First new filter: per-voice DSP code within the CPU budget | | L | P3 | T7.1, T7.2, T4.2 | todo |

## Phase 8: new synth machines (G7)

| id | task | owner | size | pri | needs | status |
|---|---|---|---|---|---|---|
| T8.1 | The DSP's voice architecture: per-voice state, voice allocation, how a machine renders | | XL? | P3 | T7.2 | todo |
| T8.2 | First new synth machine | | XL | P3 | T8.1, T7.1, T4.2 | todo |

## Simulator (E2)

- **Stage 1 (ColdFire, no sound):** T1.1, T1.5, T2.4.
- **Stage 2 (one DSP routine on a buffer):** T3.6, T3.7.
- **Stage 3 (the whole unit, with sound):** T9.1.

| id | task | owner | size | pri | needs | status |
|---|---|---|---|---|---|---|
| T9.1 | Stage 3: the DSP simulator boots the full DSP program, linked to the ColdFire emulator, with audio out | | XL | P2 | T3.3, T3.7 | todo |

## Upstream contributions (E3): ask the user before each public step

| id | contribution | where | after | status |
|---|---|---|---|---|
| C0 | Issues announcing the Digitone II 1.12 work | elekloader, digikit | first results | todo |
| C1 | 1.12 emulator support (ready on local branch dn2-1.12), plus small digikit bug fixes found along the way | digikit | T1.1 | ready, waiting for results gate |
| C2 | Digitone II profile, tests and brick checks | elekloader | T1.6, T1.7 | todo |
| C3 | Digitone II core | elekloader | T1.10 | todo |
| C4 | DSP-section mods (a format extension) | elekloader | T3.10 | todo |
| C5 | DSP simulator work | digikit | T3.6 | todo |
| C6 | Digitone II status update; later, its mods in the catalog | modwerk | T1.8; T2.7 | todo |
| C7 | Report that 1.12 works | elektron-firmware-tool | first results | todo |
| C8 | Tell elekloader's author: its in-place check covers only the boot stage 0x40400000, not section 4's 0x40200000 path (DT2 stock fails that path; elekloader builds pass) | elekloader | T1.11 | todo |

Facts for these steps, from the prior-art survey (2026-10-06, `notes/prior-art-2026-10.md`):
- **Another Digitone II project exists:** `angellinares/dn2_firmware_explore` (AGPL-3.0-or-later, DN2 OS 1.11): hardware-confirmed mods incl. an arp on MIDI tracks, and new SHARC code in section 7 (same DSP image as our 1.12). Not contacted. Before C0, decide with the user whether to align with it; its author also has 21 open digikit PRs, some DN2 (#19, #29), to check against C1.
- **C2/C3:** elekloader main (`d644993`) now has a TypeScript engine (`js/`, GPL-3.0-or-later) that must mirror every Python change byte for byte (`AGENTS.md`); our branch needs a rebase and TypeScript twins of the DN2 profile and packer changes. elekloader is GPL-2.0-or-later since 2026-09-27.
- **C4:** no public elekloader DSP-section format exists (RingTone's `elekloader.dsp` is unpublished); modwerk's octabam has a run-time DSP-upload alternative.
- **C5:** digikit's `work/sharc-emulator` branch already couples a SHARC emulator to the ColdFire for DN2 1.11 (no 1.12).
- **C6:** modwerk's Digitone II page is still "research" and lists neither dnfw nor elekloader work.
- **C8:** dnfw does not check the 0x40200000 margin either; worth telling its author too (user's call).
