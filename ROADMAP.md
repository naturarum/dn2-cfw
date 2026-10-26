# Elektron-CFW roadmap

Custom firmware for the Digitone II (OS 1.12), built on the community's tools (elekloader, digikit) and contributed back. Findings go in `HANDOFF.md`; this file holds goals and tasks.

**For agents:** claim a task by putting your name in its Owner cell before you start. Update Status when you stop, and record what you found in `HANDOFF.md`. Ask the user before any public action: forks, issues, PRs.

## Now
- **2026-10-22: CMP4 is the stable base, on the unit: "cmp4 looking very stable!"** CMP4 (`out/CMP4/CMP4.syx` `c1df74aa...`, ACCEPT 12/12; notes/T6.3-cmp4.md) = CMP3 with Elektron's stock delay back in the delay slot (our tape delay out; delay pages, names and DSP code all stock), the breathing band meters, comp-link 0.3, and flanger-names-dn2 0.1 in place of fx-rename. Every byte of our DSP code sits where CMP3's ran on the unit; build.py now refuses anything else (memory dsp-code-placement-rule). Next, in the owner's order (2026-10-22): **(1)** the Overbridge "incompatible OS version" fix (**done: OB01 = CMP4 + os-version-dn2 0.1, `bfb88623...`, ACCEPT 12/12; on the unit 2026-10-22, "overbridge works fine"**; notes/OB01-overbridge.md); **(2)** the flanger retune, better within the same memory and DSP load, no new code (chorus at the long end, phaser-like at the short end); **(3)** a terminal macOS patcher, "simple as heck", that walks the user through getting Elektron's 1.12 file; then a release candidate with the same unit tests and a GitHub release.
- **SC01 on the unit, 2026-10-21: the DSP stops** (UI fine): on echo knob turns (most with CNTR/BW), on FDBK, and at random while idle. Lead: SC01's echo knob code and math helpers ran from L2 (the dead stock FX code spots), where none of our code had run; crackles and RND head jumps run it while idle. Soak (notes/SC01-soak.md): 74.4 min of audio on the crash recipes in sharcvm, 0 faults, so not a logic bug the simulator can see. SC02 under the placement rule (notes/SC02-plan.md) is 13 KB short with all three effects (5.7 KB of same-sound savings measured). **The owner parked the tape echo (2026-10-22)** and kept the flanger; the echo can come back after a probe build proves more memory. FDBK normalised by the number of heads is done in `fx/` (FDBK 64 endless in every MODE; SC01's sources frozen in `tools/dspsplice/src/sc01/`).
- **2026-10-21: the 3-band compressor is done.** CMP3 runs on the unit (saved per kit, MIX per band, GR meters); CMP2's LOW MUP DSP stop was found with CP2D/CP2E/CP2F and fixed by moving code out of `0x315f8c-0x318000` (forbidden from now on).
- **On the unit (2026-10-12):** EF01 (T1.8), **FLG1, our flanger in the chorus slot (T3.9, M3): "it works perfectly"**, the first new code on the Digitone II's DSP, and **FTD1 (+ the tape delay in the delay slot): works**; the user: the delay sounds "not amazing, but that's not an issue", and the TIME repitch is "very interesting". The tape echo (T5.2) is the delay's next design.
- **On the unit (2026-10-13):** DH01 (boots and works; its MIDI-out check is still to be watched) and **FTN1: "tape and flanger + test all looking good. no bugs. all text display good"**: the core's boot copy and data mods work on the unit.
- **GT00 on the unit (2026-10-13): "all good. saved and loaded fine"**: the parameter table, moved at boot with room for 0x180 rows, works on the unit, +Drive save/load included.
- **GT01 on the unit (2026-10-13): "all good"**: the table is grown to 338 rows (17 new, hidden, for the compressor) and running. Next: the 3-band compressor (T6.4 map and plan running).
- **Order agreed with the user (2026-10-12), to avoid fanning out:** FTD1 and DH01 on the unit -> rename the pages (Flanger, Tape; T5.0's mod, paused 2026-10-06, resumes since dnfw is not collaborating yet) -> grow the parameter table (E/F knobs, second pages) with new glyphs for the new rows -> the 3-band compressor.
- **(Superseded 2026-10-22: see CMP4 and SC01 above.) Next after CMP3 (owner 2026-10-18):** CMP3 ends the compressor work. Then **one build line** (owner: "After this can we merge testing to one branch"): CMP3 + the tape echo (SE01's, never flashed yet) in the delay slot with FDBK on the stock law (T5.4), the flanger as now; every later feature stacks on it. Plan figures (T6.4 4.2-4.3): comp code moves to `0x311c40-0x318000` beside the tape echo (tight: check room), est. ~76 % typical, ~84 % worst [estimate]; reserve: hand-written crossover/gain loops (-5 points).
- **Then:** the flanger's fixed knobs (feedback, manual) and FLG2 (the friend's in-loop ideas), after listening on the unit; multi-page FX UI (T5.0/T6.0, parameter-table growth) on the ColdFire side.
- **Public (2026-10-12):** this project is public as github.com/naturarum/dn2-cfw (GPL-2.0-or-later, fresh history). Our digikit and elekloader `dn2-1.12` branches are public forks. Pull requests and issues upstream (C0-C8) still need the user's go for each.

## Release: Digiwerk (owner, 2026-10-22/23)

Public name **Digiwerk** (the repo stays dn2-cfw), meant as part of the Modwerk effort. Effects get pixel identities
(docs/identity/, `make_icons.py`): **CMP3** (3-band compressor), **FLNG** (flanger with TIME), **TAPE** (tape echo;
never the tape echo's trademarked name in public: it is Roland's), **SPNG** (spring reverb). Releases ship as **a base plus packs** (owner 2026-10-24): the base = light main-OS features in every build
(the Déjà vu LFO, the Overbridge fix); packs = the memory-heavy DSP effect sets, complete
builds, each brick-checked and played on the unit, chosen in the patcher (owner: "we could do manual tests for each
combination, for now the combinatorics are not insane").

| id | task | owner | size | pri | needs | status |
|---|---|---|---|---|---|---|
| R1.1 | Digiwerk Patcher: macOS app + Terminal tool that turns the user's own Elektron 1.12 file into a tested build; packs; no Elektron bytes shipped | agent | M | P1 | OB01 | done 2026-10-23 (notes/R1.1-patcher.md, tools/patcher/): 37/37 tests; CMP4, OB01, FL01 byte for byte; owner tried it ("seems to be working well"). Owner to fill in the team ID and notarize |
| R1.2 | The release candidate: FL01 (flanger TIME, Overbridge fix) as the first pack | owner | S | P1 | T5.6 | FL01 built 2026-10-22 (`a2179c0c...`, 12/12); **on the unit 2026-10-23: "seems to be working very well"**. **FL02** = FL01 + the stock compressor glyphs + flanger tempo sync (FUNC + turn on SPD): `82bfd32d...`, ACCEPT 12/12, soak 855,000 blocks clean (notes/FL02.md, notes/T5.6 7); **on the unit 2026-10-24: "stable, flanger sounds killer with sync, comp looks amazing too!"** |
| R1.3 | Release docs and the GitHub release: README header with the identity, pack notes, the patcher downloads, "not made or supported by Elektron" | agent | S | P1 | R1.1, R1.2 | todo |
| R1.4 | **Full check in a bigger patcher** (owner 2026-10-23: "the bigger patcher doesn't bother me ... it might be reassuring for everyone"): an optional "Run the full brick check" in the app, running all 12 checks on the user's Mac. It bundles digikit's emulator (Python, patched Unicorn, GPL-2.0+), brickcheck and sharcvm. About 100+ MB, and about 50 min of one core per run. Probably a separate download next to the small app. Notarization needs every bundled binary signed, plus the JIT entitlement for Unicorn | agent | M? | P2 | R1.1 | todo |
| R1.5 | **Windows version** (owner 2026-10-23: "lower priority but would be nice"): `digiwerk-patch.exe` from the same C core (libc only, own SHA-256). Unzip with a small bundled inflate (no shell), and dropping the .zip on the .exe passes it as an argument. Cross-built from the Mac (mingw-w64 or zig cc), tested under Wine or on a Windows machine. Unsigned .exe files get a SmartScreen warning; an Authenticode certificate is a cost for the owner to decide | agent | S-M | P3 | R1.1 | todo |
| R1.6 | Later: packs chosen from parts (fixed homes per effect, a DSP memory and load adder, the static brick checks in C in the patcher, the full checks on CI for every valid combination) | agent | L | P3 | R1.1, PB01 | idea (notes/R1.1-patcher.md, check-stage hook in place) |
| R1.8 | **The code diet** (owner 2026-10-23: "start the code diet"): SC02's measured same-sound savings (one shared fxmath copy, the echo's field order, SE_GLIDE as a function, dead flanger API) applied to the shipping code and the parked echo, plus new measured savings and a selcc size study; build **DT01** = FL02 with the diet (bit-identical audio); a budget table for CMP3 + FLNG + TAPE (+ SPNG) | agent | M | P1 | FL02 | **built 2026-10-23**: DT01 `5999998c...`, ACCEPT 12/12; 59,724 -> 53,254 B (-6,470), the parked echo 37,002 -> 34,288 B; bit for bit FL02 (8.87M compressor samples, 18M flanger samples), worst block +192 instructions; budget: CMP3 + FLNG + TAPE is 4,204 B short; a selcc field-access fix would save about 5 KB (study). **On the unit 2026-10-24: "sounds identical, all stable"** (notes/DT01-code-diet.md) |
| R1.9 | **The Spring pack, SP01** (owner 2026-10-24: "i want spng ready for release") = DJ01 + SPNG in the reverb slot, so the first release offers two packs: Compressor + Flanger (DJ01, stock reverb) and Spring (CMP3 + FLNG + SPNG). Budget: CMP3 + FLNG 53,254 B + SPNG about 12.6-15.2 KB, inside the proven 83,838 B (code2 0x310000-0x315f8c has room); SPNG state (43 KB) in the stock reverb's DDR region; about +1.1 % DSP. Main OS: a names mod for the reverb page (PRE DEC MODE DWEL HPF TONE, WDTH on row 0x83) | agent + owner | M | P1 | T5.5 MODE knob, DJ01 | **built 2026-10-24**: SP01 `859589f2...`, ACCEPT 12/12 (notes/SP01-spring-pack.md); spring-names-dn2 0.1 (WDTH on G via path c); SPNG bit for bit the Mac reference; DSP +1.2 % mean, +2.2 % soak worst; soak 517,500 blocks clean; the patcher makes it byte for byte. **On the unit 2026-10-26: stable; the sound "not there yet, but promising"** (the tail cycles, about 380 ms pile-ups; 1.5-5.5 dB under the stock reverb): SPNG v2 first (notes/T5.5 section 9, a Mac prototype), on DC02 (SP02); the Spring pack moves to a later release (owner: release without waiting) |
| R1.10 | **The compiler fix** (owner 2026-10-24: "yes do the compiler fix"): a verified peephole pass on selcc's output (tools/dspsplice/peephole.py, opt-in per recipe; vendor/selache untouched): A = the field offset folded into the access (~5.1 KB), B = the base straight into I4 (~6.2 KB with A), C later (~11 KB). Build **DC01** = DJ01 with it (bit-identical sound), then a reviewer pass; it closes CMP3 + FLNG + TAPE inside the proven memory | agent + 1 reviewer | M | P1 | DJ01 | DC01 built 2026-10-24 (`a0b63d39...`, 12/12): -5,906 B (-11.1 %), bit for bit DT01 on every suite, 1,229/1,239 changed lines covered; CMP3 + FLNG + TAPE now fits (+4,974 B). Review: OK with one condition, so **DC02** (B refused before 48-bit consumers, the pairing CCES never uses; about -514 B) built 2026-10-24: `330dfa9b...`, ACCEPT 12/12, -5,322 B (-10.0 %), bit for bit DT01 on every suite, soak clean; CMP3 + FLNG + TAPE fits with +3,958 B. DC01 superseded before any flash. **DC02 on the unit 2026-10-26: "seems stable and sounds identical"** (notes/DC01-compiler-fix.md section 5) |
| R1.7 | More DSP room: probe build PB01 = FL01 + watermarks and execution probes over L1 block 1's tail, block 3's tail and L2 (incl. SC01's L2 ranges) | agent + owner | M | P1 | FL01 | **not built**: the agent's output was halted by a safety classifier during exploration (2026-10-23), no files changed; its findings are in notes/PB01-findings.md (fast L1 adds about 12 KB, not 45; L2 holds about 260 KB of candidates). **Retried 2026-10-23 at the owner's request: halted again, in the reading phase, nothing written.** A third attempt, in the main session, was halted the same way (reading phase, no files written). **Paused** |

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

## Resource budget and stability (owner, 2026-10-13)
- **DSP load:** stay a few points above Elektron's own load, never near 90 %. Stock measures 63-65 % on the unit
  (dnfw's idle-time method). Working targets until the owner adjusts them: typical (everything on, default
  settings) about 70 %, worst case under about 75 %, measured on the unit, not estimated.
- **Today's estimates** (instruction counts, T6.4 4.4): GT01 68 %; + 3-band ~73 % typical, ~77 % worst; with the
  TAPE ~76 % typical, ~84 % worst. Over target: hand-assembled inner loops (selcc's code is several times
  larger than hand-written SHARC code) and per-slot hot swap (only the active effect runs) are the main levers.
- **The load meter (T4.1/T4.2) comes before anything that pushes the budget,** so numbers come from the unit.
- **Stability:** Elektron aims for live-performance stability. The owner accepts rare edge-case crashes from our
  firmware, to be fixed later, but not a heavy-load design. Brick safety (the brick checker, the untouched
  bootstrap) is never part of that trade.

## Feature scratchpad: requests from other users (not planned yet)
Collected by the owner; each one is an idea until the owner promotes it to a goal. Newest last.
- **2026-10-12: an MPC60-style bitcrusher** (bit depth, downsampling and drive with the MPC60's character), as a
  per-track FX option next to stock BR, SRR and OVER. The per-track FX run in the voice path of the DSP, which we
  have not touched yet. The MPC60's sound (12-bit, 40 kHz, its converters and filters) needs research first.
- **2026-10-12: p-locks on the send FX.** T6.1 found that lockability is a display flag, but p-locks are stored per
  track in the sound's slots: an FX lock needs its own storage and a rule for which track's lock applies to a
  shared effect [inferred]. Note: the owner's own FX knobs were planned without p-locks (2026-10-06).
- **2026-10-16: parameter slides**: like portamento, but for p-locks: a locked value glides from the previous one
  instead of jumping. Size not assessed yet (owner).
- **2026-10-16: sequencer ping-pong** (play direction reverses at the pattern's end). Heard from a friend; the owner
  is not sure they want it. Size not assessed yet.
- **2026-10-17: Mutable Instruments Beads, granular mode only, 8 controls, in the chorus slot** (replacing the flanger;
  owner). Code MIT (pichenettes/eurorack, `vendor/mi-eurorack`); the derivative may not use the name "Mutable
  Instruments". Next: a Mac-only cost and listening study before any port (C++ -> C for selcc).
- **2026-10-18: a macOS builder** (owner): bring your own stock 1.12 `.syx`, SHA-256 check, build locally, compare with
  our published hash. Inspiration: karigardarsson's Windows builders (`vendor/digitakt-ii-research/windows-builder/`,
  Python + PyInstaller, MIT; Synthack-OS's Syntakt MAX). digikit upstream is now AGPL-3.0 (ElekLabs): keep the builder
  free of newer digikit code, or accept AGPL for it.

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
| T1.12 | **New OS releases: a port checker** (owner 2026-10-18: "a plain checker that lets us quickly know if it will brick the device or run at all"). Stay on 1.12 for now. `portcheck <new stock .syx>` in minutes, no emulator: (1) container, seal and depacker the same scheme (a change = stop); (2) bootloader/recovery sections byte-identical to 1.12 (then a bad OS stays recoverable over DIN); (3) every mod site (each `mod.json` lists addr + stock bytes) at the same address, moved (found by relocation-tolerant function matching, digikit) or missing; (4) the data we rely on (parameter table, page records, frame layout and tail bytes, FX record free words, DSP hook CJUMPs, free DSP ranges, tables). Output: a scorecard, then per-version site maps (a frozen manifest per OS, as digitakt-ii-research's builder does) and brickcheck parametrised by version. Test bed we already have: DN2 1.00A, 1.10A-E, 1.11 (1.12's DSP = 1.11's; dnfw ran on 1.11 hardware) | agent | M | P3 | T1.7 | idea, after the compressor |

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
| T2.8 | **Déjà vu LFO** (owner 2026-10-23, after Kári Garðarsson's Digitakt II All-in-One v0.3.0, behaviour only; AGPL, facts only): new LFO waves DV-F, DV-T and scale-quantized PMAJ…OCTA; on them MODE = loop length 2-32 and SPH = mutation; build DJ01 = DT01 + the mod | agent | M | P2 | DT01 | **built 2026-10-24**: DJ01 `7edf7c59...`, ACCEPT 12/12; main OS only (lfo-dejavu-dn2 0.1, 16 sites), DSP = DT01; stock waves identical to DT01, kits keep 7-15 (a stock OS plays them as RAND); +77 instructions per DV LFO per frame. **On the unit 2026-10-24: "déjà vu works! and it sounds so cool".** Still open: FM Tone PITCH / WaveTone MAST in semitones (notes/T2.8-dejavu-lfo.md) |

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
| T5.0b | Rename the FX pages to match the new effects: Flanger/FLG, Tape/TAP(E), on every screen that shows them; no new knobs or glyphs | agent | S | P1 | T5.0, T1.9, T3.8 | done 2026-10-12, emulator: **FTN1** = FTD1's DSP + core-dn2 + fx-rename (29 data-only name repoints, elekloader fork `examples/fx-rename-dn2`), `out/T5.0b/FTN1.syx` sha256 `60d1cd1f...58e8`, brickcheck ACCEPT 12/12; 28 screens: only renamed words differ, parameter mirror and DSP frame byte-identical to stock (notes/T5.0b-rename.md). **On the unit 2026-10-13: works, all names display correctly (user)**; guide `out/T5.0b/FLASH-FTN1.md` |
| T5.1 | Chorus slot: a basic, swelly flanger (user 2026-10-06: "for now maybe let's just build a basic flanger"), in C, simulated, then on the unit | | L | P2 | T3.7, T3.9 | **on the unit 2026-10-12 as FLG1** (DPTH/SPD/HPF/WDTH on the stock chorus knobs; FDBK +68 % and MAN 0.9 ms fixed; the user: it works perfectly). C version 2026-10-06 (`fx/flanger.c`: 0.05-77 ms, damped feedback/Karplus-Strong, 6 LFO shapes + slew, sync, stereo phase and offset, polarity). Next: more knobs need the second page (T5.0/T6.0); FLG2 with in-loop saturation |
| T5.2 | Delay: a Roland RE-201 style echo (user 2026-10-12, replaces the one-head design): 3 heads chosen by MODE; TIME, MODE, INTENSITY, AGE, CENTER, BW, WIDTH, PAN | agent | L | P2 | T5.1 | C version done 2026-10-12 (`fx/spaceecho.c`, notes/T5.2-tape-echo.md): 282/282 tests, A/B renders `se_*` beside stock and FTD1, SHARC = Mac within -108 dB, 18-38k instructions/block (1-3 heads), state fits the stock delay's DDR. Next: the owner listens and answers the open questions; then the page layout (6 stock rows map, MODE and AGE need 2 more) and the splice |
| T5.3 | The tape echo on the unit, one-page layout (owner 2026-10-13): A TIME, B MODE (X row 0..6), C WID (width + PAN fold), D FDBK, E CNTR, F BW, G AGE (row 0x7a, k 40, frame word 0xa18), H REV; named Echo | workflow | M | P1 | T5.2, T6.5 | built 2026-10-13 (notes/T5.3-tape-echo-on-the-unit.md): **SE01** = GT01 + fx-echo-dn2 + the tape echo in the delay slot, `out/T5.3/SE01.syx` sha256 `760ff3f8...c538`, brickcheck ACCEPT 12/12 (`out/brickcheck/SE01b-2026-10-13`); a reviewer's loudness defect fixed (wet ceiling 0.70 vs stock's runaway 0.53; it was 4.24); 355k instructions/block mean (est. ~71 %), worst ~391k (~78 %), over the owner's budget in the worst case; a sixth selcc miscompile found and avoided. Minor, open: AGE's label doesn't show its value while turning; WID's centre step changes an existing tail by 6 dB; TIME capped below 68 BPM; slot-size guards are no-ops under selcc. Guide `out/T5.3/FLASH-SE01.md`. **SC01 (2026-10-21) put it on the unit merged with CMP3: the DSP stopped** (lead: its knob code in L2); parked by the owner 2026-10-22 (notes/SC01-soak.md, notes/SC02-plan.md) |
| T5.4 | FDBK on the stock law for our delays, the tape delay (FTD) and the tape echo: loop gain = FDBK/64 as on stock, so the display's 100 % (knob 64) is endless repeats and above it the repeats grow until the soft clip holds them (today: tape delay 1.1 x knob/127, tape echo 1.1 x^1.5, so repeats only build at the very top; owner 2026-10-13). Owner 2026-10-18: "Yes give our delay the stock law. We can build that later" | | S | P1 | T5.3, T6.3d | done in `fx/` for the tape echo (SC01, 2026-10-21; per head FDBK/64, and from 2026-10-22 divided by the number of heads). The delay slot is stock again in CMP4 (2026-10-22), which has the stock law by itself; the tape echo is parked |
| T5.5 | Reverb (G1: room to cathedral, more control over reflections). Owner 2026-10-21: **a spring reverb** to pair with the tape echo (the RE-201 has one), but as a choice, since its character is specific: a separate mod picked at build time (macOS builder), the stock reverb staying the default. First a C prototype with A/B renders | agent | L | P2 | T5.1 | **C prototype done 2026-10-23** (notes/T5.5-spring-reverb.md): fx/spring.c, three dispersive spring loops at 12 kHz, page A PRE B DEC C DRIP D DWEL E HPF F TONE G WDTH (row 0x83) H TENS (new row); 22.1k instructions/block (stock reverb 14.6k; moving 25.9k, worst 27.0k), 14,512 B selcc code (12.6 KB in a pack), 43 KB state; selcc = Mac bit for bit (102 cases); FLNG + TAPE + SPNG fits the proven room with 16.6 KB spare; 5 A/B groups incl. TAPE into SPNG, guide out/T3.11/SPNG-listen.md. 2026-10-24 owner: "sounds amazing", the characters on one knob, then "size and dark should be separate": **MODE** on C is the springs' character only (0 taut, little drip .. 64 classic = init .. 127 slack, deepest drip: DRIP, TENS, spread); DEC, TONE, DWEL, HPF direct; H free, no new row; 22.2k still / 26.3k moving, 15,550 B code, selcc = Mac (117 cases); groups sp_mode* |
| T5.6 | The flanger retune (owner 2026-10-22: option (a), "a continuous effect that uses the same controls", "without increasing mem/cpu load"): on the unit the flanger gets only the chorus page's DPTH, SPD, C (HPF), WDTH, with MAN 0.9 ms and FDBK +68 % fixed, so neither the chorus nor the short end is reachable. C becomes **TIME**, 0.05 ms (phaser-like) through 0.9 ms (TIME 32 = CMP4's flanger, bit-identical) to 26 ms (chorus), with feedback and depth range fading past TIME 64; HPF fixed at 64 Hz; the WDTH 127 overflow fixed | agent | S | P1 | T6.3 CMP4 | **built 2026-10-22 as FL01** = OB01 + the retuned glue + flanger-names-dn2 0.2 (C named TIME, default 32): `out/FL01/FL01.syx` `a2179c0c...`, ACCEPT 12/12; soak 675,000 blocks with 0 faults; waits for the owner on the unit. Planned (notes/T5.6-flanger-retune.md): `fx/flanger_unit.h`, `tools/dspsplice/src/flg_glue2.c` (fx/flanger.c unchanged); glue 1,604 B vs 1,636 B, the worst block over 189 knob settings 16,938 vs 16,943 instructions, -243 while a knob moves (`tools/sharcvm/tests/flgcheck.py`); fxtest 817/817; A/B groups flunit, flunitd, flunitk. Waits for the owner's listening; goes into the release candidate with the Overbridge fix (main OS: C's label TIME; maybe C's default 32) |

Each new effect is its own mod, so you choose one at build time and only one is loaded at once. Port open designs where the licence allows; octabam's `THIRD_PARTY.md` lists its sources and their licences.

## Phase 6: 3-band master compressor (G9)

| id | task | owner | size | pri | needs | status |
|---|---|---|---|---|---|---|
| T6.0 | FX parameter room: frame words and kit storage for extra pages | dn2-fw session | M | P2 | T3.3 | paused 2026-10-06 (overlap with dn2_firmware_explore; owner contacting its author). Found: 32 free frame words (30-word tail + 2), proven end to end in the emulators; 28 reserved words per kit FX record for storage (notes/T6.0-fx-params-room.md) |
| T6.1 | Can the parameter table grow? (study; user 2026-10-12: "I took it as off limits") | agent | M | P1 | T5.0, T6.0 | done 2026-10-12, emulator and static only (notes/T6.1-table-growth-study.md): **yes, with conditions**. Row ids are never saved (projects store slot k); 114 same-length operand edits (56 base refs, 58 count compares); relocated + 8 rows in the emulator: 25 screens byte-identical to stock, new knobs draw, turn and save. Conditions: resize `ParameterUpdateCache` before new rows get CCs; set the no-lock display flag on FX rows (else a p-lock lands on sound slot k); per-row rules (section 3.4). Not yet: cold boot of a grown build, +Drive save/load, Overbridge. Cheapest delay-page knob: stock row 0x7a "Delay FX Routing" (k 40) on the empty G slot (reverb: 0x83, k 48), no growth |
| T6.4 | Map the stock master compressor (DSP, UI, storage, frame) and plan the 3-band build | workflow | M | P1 | T3.4, T6.5 | done 2026-10-13 (notes/T6.4-compressor-map-and-plan.md): stock compressor mapped (k 60-67, laws, tables, sidechain sources, UI rows, never lockable); plan: MIXER pages {LOW, MID, HIGH (ids 40-42), INT MIX 1/2, FX MIX, EXT MIX}, band pages THR ATK REL MUP RAT XOVR SCS MIX + VOL, SCF to FX MIX knob D, per-band gain-reduction meters; stages **CMP0** (DSP only, stock knobs, fixed crossovers) -> **CMP1** (pages + meters) -> **CMP2** (frame tail, knobs live) -> **CMP3** (saved per kit in FX record +0x60..+0x97); CPU est. stock 64 %, GT01 68 %, +3-band ~73-77 % (FTD1) / ~84 % worst (TAPE). Owner answers 2026-10-13: SCF dropped (bands do its job; FX Mixer stays stock), crossover ranges OK, ratio 1.00 (band off) added, old projects open full-band. Next: CMP0, after SE01 (shared DSP memory block) |
| T6.3a | CMP0: the 3-band compressor on the DSP, on the stock Compressor knobs, crossovers fixed (196 Hz / 2.61 kHz), on GT01/FTD1 (owner 2026-10-13: "Build cmp0") | workflow | M | P1 | T6.4 | built 2026-10-13 (notes/T6.3-cmp0.md): `out/T6.3/CMP0.syx` sha256 `03598e3f...`, main OS = GT01's, brickcheck ACCEPT 12/12 (`out/brickcheck/CMP0-2026-10-13`); verify_cmp PASS: one band vs stock within 0.0011 dB, MIX 0 bit-identical to FTD1 (2,000 blocks), 18 NaN/inf injections recovered, SHARC vs Mac -115..-124 dB. Est. load 71.6 % (MIX 0) to 73.3 % (MIX 39) typical, worst 75.8 % (self-detection, everything moving): over the owner's ~70 % typical / <75 % worst targets; hand-written loops are the reserve. At the same knobs up to +5 dB RMS / +8 dB peak louder than stock on sustained sounds (three bands each with full makeup). Open, minor: the guard doesn't reset derived control state; a guard trip at heavy settings gives a loud 50-80 ms burst; the dry-crossfade branch has no SHARC-vs-Mac check. Listening page `out/T6.3/CMP0/ab/`; guide `out/T6.3/FLASH-CMP0.md` **On the unit 2026-10-13: "working quite well and sounding good"**; louder than stock accepted (owner) |
| T6.3b | CMP1: the Compressor Low/Mid/High pages with their own GR meters (band knobs on screen, inert), core-dn2 0.2 page records, comp-ui-dn2 0.1, param-rows-dn2 0.2; the CMP0 review's guard fixes (owner 2026-10-13: "can you build cmp1 now?") | workflow | M | P1 | T6.3a | done: `out/T6.3/CMP1.syx` `cbe4eac8...`, ACCEPT 12/12; **on the unit 2026-10-14, works** (notes/T6.3-cmp1.md). The flashed compressor source is frozen in `tools/dspsplice/src/cmp1/` (section 7 `ee82aa7c`) |
| T6.3c | CMP2: band knobs and both crossovers live through the SPI frame tail (comp-link-dn2 0.1 at `0x40025e9e`; DSP tail reader), fallback = CMP1 until a valid tail; values not saved (owner 2026-10-17: "Shall we get to work on cmp2?") | agent + 1 reviewer | M | P1 | T6.3b | built 2026-10-17 (notes/T6.3-cmp2.md): `out/T6.3/CMP2.syx` `d8551470...`, ACCEPT 12/12; fallback bit-identical to the unit's CMP1; injection 0/14,762 failed (the unit's CMP1: 40); est. load ~74 % typical, 76.6 % worst. After power-on every band is off (all values 0) until CMP3. **On the unit 2026-10-18: band knobs act, but MUP past ~noon on any band stops the DSP** (beep or silence until power-off; UI and sequencer keep running); CMP0 at high MUP is fine; the sim does not reproduce it. Band GR meters never moved on CMP1/CMP2 (CMP0's stock bar works). Overbridge: "incompatible OS version" on our builds. Diagnosing with CMP2D (load meter, T4.1/T4.2): `out/T6.3/CP2D.syx` `6db9b17b...`, ACCEPT 12/12, audio bit-identical to CMP2; notes/T6.3-cmp2d.md, guide out/T6.3/FLASH-CMP2D.md. **Found 2026-10-18 with CP2D/CP2E on the unit:** load ~50-60 % (not CPU); the DSP stops inside cmp_tail_apply (CP2E crumb 12 px = step 4) only when the LOW MUP body runs (probe: no stop when skipped); that code is identical to MID/HIGH MUP except its address (~0x3166a6, L1 block 2, a range no earlier unit build used for code). Hypothesis: something on the chip writes there at run time. **Next (paused until 2026-10-20): CMP2F** = cmp_tail moved to code4 + watermarks over the free L1 ranges and per-page code checks on the bars. **CP2F (`030cd2dc...`, ACCEPT 12/12) on the unit 2026-10-21: no stop however hard LOW MUP is turned ("i tried mangling it a whole bunch and no crash!")**: fixed by moving cmp_tail to code4. Rule: no code in `0x315f8c-0x318000` until the watermark result says that range is clean (notes/T6.3-cmp2f.md) |
| T6.3d | CMP3: band values saved per kit (comp-link-dn2 0.2: the three storage sites, seeding old kits as full-band) **plus a MIX per band** (owner 2026-10-17: "let's plan individual D/W for CMP3"): three rows from the spare k 87..96, on each band page's H, old kits seeded from their single MIX. Light process: one builder + one storage-only reviewer; DSP proofs only where the mix code changes | agent + 1 reviewer | M | P1 | T6.3c | 2026-10-18 build superseded (its code reached into `0x315f8c-0x318000`). Rebuilding 2026-10-21 on CP2F's proven layout **plus real GR meters** (dB scale, hold and fall; owner chose "Proper meters + CMP3"); CP2F's scanner was empty on the unit, so nothing writes that range: code there just fails. **Built 2026-10-21:** `out/T6.3/CMP3.syx` `28f10cba...`, ACCEPT 12/12; audio bit-identical to the 10-18 build (8.87M samples); meters 2 px/dB, 0.5 s hold, 20 dB/s fall. Storage review (notes/T6.3-cmp3-review.md): OK to flash with unit checks; later fixes: read `live` before the stock call in `compl_update` (rare kit-switch race), restore id 0 unless k 0..24 named, optional +0x96 clear after the old-format converter. **On the unit 2026-10-21: "cmp3 seems to be working quite well. low mup is good"**. Followed by CMP4 (2026-10-22, stock delay, breathing meters, comp-link 0.3): "cmp4 looking very stable!" |
| T6.5 | Grow the parameter table (user 2026-10-12: "let's commit this big thing"), for the 3-band compressor and later knobs | agent | L | P1 | T6.1 | done 2026-10-12, emulator (notes/T6.5-table-growth.md): **GT00** = FTN1 + `param-grow` (table copied at boot to `0x47D00000`, room for 0x180 rows, 149 sites: 56 operands, 57 bounds + the display accessor, 33 MIDI-cache constants), sha256 `4cfe8056...d13d`; **GT01** = GT00 + 17 inert rows for the compressor, `4c64c5dc...e41e`; both brickcheck ACCEPT 12/12. FTN1-identical: boot maps, p-locks, LFO list, randomisers, copy/paste, MIDI out, a 7,100-message CC/NRPN flood, the 12.9 MB working project image. Storage: a kit save keeps k 25-69 only; 28 unused FX-record words (+0x60..+0x97) for the compressor need hooks. **GT00 on the unit 2026-10-13: all good, +Drive save and load fine (user).** GT00 cold start fine; **GT01 on the unit 2026-10-13: all good (user)**. Still unit-only: Overbridge. Guides `out/T6.5/FLASH-GT00.md`, `FLASH-GT01.md` |
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
