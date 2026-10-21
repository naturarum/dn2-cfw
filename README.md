# Elektron-CFW

Custom firmware research for the **Elektron Digitone II**, OS 1.12. This is my lab notebook, made public: the notes, the tools, and the new effects I'm building for my own unit.

The goals: swap the send effects for new ones (a tape delay, a flanger, a better reverb), a 3-band master compressor, the arpeggiator on MIDI tracks, more MIDI and synth voices, and a CPU meter. The plan and the state of each task are in [`ROADMAP.md`](ROADMAP.md).

**No Elektron firmware is stored here.** You download the OS file from Elektron yourself, and the tools check its sha256 before using it. Every build happens on your own machine.

## Latest: the 3-band compressor is done (2026-10-21)

**CMP3** runs on my unit: three compressor bands with their own pages, knobs, meters and dry/wet, saved with each kit.
- **Every band knob acts on its band** (THR, RAT, ATK, REL, MUP, the two crossovers), sent from the main OS to the DSP in spare words at the end of the frame it sends every 0.67 ms.
- **A MIX per band** on knob H. Each band's dry part stays in phase with the others, so one band wet and two dry doesn't smear the sound.
- **Saved per kit**, in 56 bytes of the kit's effect record that the stock OS never reads or writes. A stock OS loads and re-saves these kits unchanged. Kits from before CMP3 open as one full-band compressor with their old settings.
- **Gain-reduction meters** per band, in dB.

**A crash only the chip could show.** CMP2 (band knobs live) stopped the DSP for good whenever LOW makeup changed, and the simulator never reproduced it. Three diagnostic builds found it:
- **CP2D**, a load meter from the DSP's own cycle counter: the real load is about 50-60 %, far below my instruction-count estimate, so it wasn't CPU.
- **CP2E**, a heartbeat, a "last step reached" marker and a probe switch: the DSP stopped inside the code that applies the LOW makeup value, and only when that code ran. That code is identical to the MID and HIGH versions except for its address.
- **CP2F** moved it, and filled the old spot and every other free region with a known pattern that the DSP keeps checking. No crash since, and nothing ever wrote to that memory. So code placed in one stretch of L1 block 2 (`0x315f8c-0x318000`) fails on the chip, for reasons I don't know yet. The build now refuses to put anything there.

Write-ups: [`notes/T6.3-cmp2d.md`](notes/T6.3-cmp2d.md), [`notes/T6.3-cmp2e.md`](notes/T6.3-cmp2e.md), [`notes/T6.3-cmp2f.md`](notes/T6.3-cmp2f.md), [`notes/T6.3-cmp3.md`](notes/T6.3-cmp3.md), and the storage review [`notes/T6.3-cmp3-review.md`](notes/T6.3-cmp3-review.md).

## Earlier: a 3-band master compressor on the Digitone II (2026-10-14)

The stock master compressor is now three compressors, each following Elektron's own design (same detector, curve, attack/release laws and makeup), split at two crossovers and summed back. It runs on the unit:
- **CMP0** puts the three bands on the DSP, driven by the stock Compressor knobs. One band matches Elektron's compressor within 0.001 dB; at MIX 0 the output is bit-identical to the build before it. A guard catches NaN/inf so the master can never latch silent.
- **CMP1** replaces the Compressor page with three pages, Compressor Low, Mid and High, each with its own gain-reduction meter, read from spare words the DSP sends back to the main OS. The band knobs are on screen; making them act (CMP2) and saving them per kit (CMP3) are next.

How the stock compressor works, the plan and the evidence: [`notes/T6.4-compressor-map-and-plan.md`](notes/T6.4-compressor-map-and-plan.md), [`notes/T6.3-cmp0.md`](notes/T6.3-cmp0.md), [`notes/T6.3-cmp1.md`](notes/T6.3-cmp1.md).

## Earlier: the parameter table grows on 1.12 (2026-10-13)

Every knob on the Digitone II is a row in one table inside the OS: 321 rows on 1.12, with no room after them. GT00 copies the table at start-up into free memory with room for 384 rows, and GT01 adds 17 hidden rows for the 3-band compressor. On my unit, GT00 starts from cold and saves and loads projects as before, and GT01 has run without problems. Projects store values by slot, never by row, so moving the table doesn't change how they are saved. New rows are safe for projects only if they follow a few rules, listed in the write-up. dn2_firmware_explore did this first, on 1.11, for their LFO4 mod; ours is for 1.12, as an elekloader mod other mods add rows to. How it works, what ran where, and the rules: [`docs/parameter-table-growth.md`](docs/parameter-table-growth.md).

## Where things stand (2026-10-21)

**On the unit.** Fourteen builds have run on real hardware:
- **EF01**, stock 1.12 with only its name changed and the seal redone. That proved our rebuild, seal and flash path.
- **FLG1**, our flanger in place of the stock chorus. Our first new code on the Digitone II's DSP: the C in [`fx/flanger.c`](fx/flanger.c), compiled with selcc and spliced into the DSP section by [`tools/dspsplice`](tools/dspsplice). It plays on the stock chorus page's knobs (depth, speed, high-pass, width).
- **FTD1**, FLG1 plus our one-head tape delay in place of the stock delay (resonant loop filters, wow and flutter, wear, and tape-style pitch bends when TIME changes).
- **DH01**, our core (the hook bus every screen and MIDI mod runs through) with a small MIDI test mod.
- **FTN1**, FTD1 with the screens renamed to match: Flanger and Tape on the effect pages, the sends and the mixer.
- **GT00**, FTN1 with Elektron's parameter table moved at start-up into free memory, with room for 63 more knobs. Saving and loading projects works as before.
- **GT01**, GT00 with the table grown by 17 rows (still hidden) for the compressor. dn2_firmware_explore grew it first, on 1.11, for their LFO4.
- **CMP0**, GT01 with the 3-band master compressor on the DSP, on the stock Compressor knobs.
- **CMP1**, CMP0 with the three band pages and their meters.
- **CMP2**, the band knobs live. It had the LOW makeup crash above.
- **CP2D, CP2E, CP2F**, the diagnostic builds that found and fixed it (CP2F is CMP2 fixed).
- **CMP3**, the finished compressor: band settings saved per kit, a MIX per band, meters in dB.

**In the ColdFire emulator** (digikit, with our changes on [a branch of our fork](https://github.com/naturarum/digikit/tree/dn2-1.12)):
- 1.12 boots to the main screen.
- PLAY runs the sequencer at the project's tempo. It needs the audio clock and the interrupts the firmware forces on itself.
- DIN MIDI in and out works, including virtual MIDI ports on a Mac.
- The trig and key LEDs are drawn.
- Live play from the computer keyboard runs at about 0.8x real time.

**On the DSP side:**
- [`tools/sharcvm`](tools/sharcvm) is an interpreter for the Digitone II's SHARC+ DSP code. It renders the stock chorus, delay, reverb and master compressor offline, and the whole per-block DSP handler. It is checked against 2,039 test programs compiled with selache.
- [`fx/`](fx) holds the new effects, in portable C: a one-head tape delay (resonant filters in the loop, wow and flutter, AGE, width), a 3-band compressor (each band follows the stock compressor's design), and a flanger/comb with damped feedback.
  - The SHARC build gives the same output as the Mac build.
  - An A/B page compares each effect with the stock one running in the emulator.

**Safety:**
- [`tools/brickcheck`](tools/brickcheck) runs 12 checks, and every candidate must pass before it may be flashed.
- The STARTUP-menu recovery over DIN MIDI was tested on the unit.
- An audit of the code that writes flash, and a proof that the STARTUP menu doesn't depend on the main OS (`notes/T1.4*`, `notes/T1.5*`).

**Next:** one build line with everything: CMP3 plus the Space Echo style delay ([`fx/spaceecho.c`](fx/spaceecho.c)) in place of the tape delay, with the stock feedback law. Memory is the hard part, now that one stretch is off limits. After that, maybe a spring reverb as an option, and a macOS builder so you can make the firmware from your own copy of the stock OS.

**Tooling lesson (2026-10-18):** the DSP simulator leaked about 9.6 MB per test run, and a long parallel test froze my Mac. It's fixed (`tools/sharcvm/vm.py`), test workers now stop themselves at 2 GB ([`tools/sharcvm/memguard.py`](tools/sharcvm/memguard.py)), and [`tools/memwatch.py`](tools/memwatch.py) guards unattended runs.

## What's where

| path | what |
|---|---|
| [`ROADMAP.md`](ROADMAP.md) | goals, milestones, tasks (T0.1 ...), who did what |
| [`HANDOFF.md`](HANDOFF.md) | the running log, oldest first. Later entries supersede earlier ones |
| [`notes/`](notes) | one report per task, with addresses. Claims are marked **[verified]** (measured, or checked against the bytes) or **[inferred]** |
| [`tools/brickcheck`](tools/brickcheck) | the safety gate for candidate firmware |
| [`tools/sharcvm`](tools/sharcvm) | the SHARC+ interpreter and offline effect renderer |
| [`fx/`](fx) | the new effects, a renderer for 48 kHz WAV files, tests, the A/B page builder |
| [`scripts/`](scripts) | the scripts behind each task's findings (named after the task: `t24b_*`, `t60_*` ...) |
| [`analysis/`](analysis) | the class hierarchy recovered from the OS's RTTI |
| [`VENDOR.md`](VENDOR.md) | the upstream projects this builds on, with the commits used |
| [`FIRMWARE.md`](FIRMWARE.md) | which Elektron firmware files were used, by sha256 |

## Getting started

Everything runs on macOS (Apple silicon). Linux should work for most of it.

1. Put Elektron's `Digitone_II_OS1_12.syx` into `elektron-firmware/`.
2. Clone the upstream projects into `vendor/`, as listed in [`VENDOR.md`](VENDOR.md). For digikit and elekloader use our forks on the branch `dn2-1.12`:
   - [naturarum/digikit](https://github.com/naturarum/digikit/tree/dn2-1.12) has 1.12 support, the audio clock, DIN MIDI, LEDs and live play.
   - [naturarum/elekloader](https://github.com/naturarum/elekloader/tree/dn2-1.12) has the Digitone II 1.12 profile and the core-dn2 hook bus.
3. Make a Python venv: `python3 -m venv venv`, then `venv/bin/pip install` what a tool's header asks for.
4. Then, for example:
   - `scripts/dn2-emu-112.sh all` boots 1.12 in the emulator.
   - `scripts/dn2-live.sh --ssi0-hz --ips 150e6` opens a window you play from the keyboard. Press `?` for the key map.
   - `cd fx && make test && make renders && make listen` runs the effects workbench.
   - `tools/brickcheck/brickcheck CANDIDATE.syx` checks a build.

## If you flash anything

Anything you flash is at your own risk. What we know about the risks:
- **A broken main OS or DSP program can be recovered.** Hold FUNC at power-on, press TRIG 4 (OS UPGRADE), and send the stock OS over DIN MIDI. Tested on my unit.
- **The bootstrap must never change.** It holds that recovery menu. Our builds keep it byte-identical, and the brick checker refuses any change to it.
- **Back up your +Drive with Transfer before you flash.** And keep the volume down the first time a new DSP build plays.

## How this is made

This project is a collaboration between me and Claude, Anthropic's AI model, working in Claude Code.
- **My part:** I set the goals, make the musical and design decisions, and do everything that touches the hardware: flashing, recording, listening.
- **Claude's part:** most of the reverse engineering, the tools, the code and the notes.

Claims in the notes are marked as verified or inferred, nothing is flashed without passing the brick checker, and nothing counts as working until it has run on my unit.

## Credits

This builds on other people's work:
- [digikit](https://github.com/m-dwyer/digikit) by m-dwyer: the ColdFire emulator and the SHARC+ analysis tools.
- [elekloader](https://github.com/irpina/elekloader) by irpina: the mod loader and its core and hook model.
- [selache](https://github.com/js216/selache) by js216: the SHARC+ C compiler and toolchain.
- [elektron-firmware-tool](https://github.com/mischa85/elektron-firmware-tool) by mischa85: the `.syx` format and rebuild.
- [octabam](https://github.com/sambanks/octabam) by sambanks: the template for the overall approach.
- [dn2_firmware_explore](https://github.com/angellinares/dn2_firmware_explore) by angellinares: their Digitone II 1.11 research, which much of our DSP and UI work builds on (the splice method, the DSP memory and cache map, the page records, and more; listed per task in [`NOTICE`](NOTICE)).
- The community around [modwerk](https://modwerk.app).

Where a note uses someone else's finding, it says so. Code adapted from other projects is listed in [`NOTICE`](NOTICE) and named in each file's header.

## Licence

GPL-2.0-or-later, Copyright (C) 2026 naturarum and contributors. See [`LICENSE`](LICENSE) and [`NOTICE`](NOTICE).

Elektron and Digitone are trademarks of Elektron Music Machines. This project is not affiliated with or endorsed by Elektron.
