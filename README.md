# Elektron-CFW

Custom firmware research for the **Elektron Digitone II**, OS 1.12. This is my lab notebook, made public: the notes, the tools, and the new effects I'm building for my own unit.

The goals: swap the send effects for new ones (a tape delay, a flanger, a better reverb), a 3-band master compressor, the arpeggiator on MIDI tracks, more MIDI and synth voices, and a CPU meter. The plan and the state of each task are in [`ROADMAP.md`](ROADMAP.md).

**This repository contains no Elektron firmware, original or modified, and never will.** Bring your own OS file from Elektron. The tools check its sha256 before they touch it.

## Where things stand (2026-10-12)

**On the unit.** One build has run on real hardware: **EF01**, stock 1.12 with only its name changed and the seal redone, flashed over USB. That proves our rebuild, seal and flash path. Nothing else has run on a unit yet.

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

**Next:** putting the new effects into the DSP section (T3.8), then on the unit.

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
- [dn2_firmware_explore](https://github.com/angellinares/dn2_firmware_explore) by angellinares: the facts we cite from their Digitone II 1.11 research (their code is AGPL, and none of it is copied here).
- The community around [modwerk](https://modwerk.app).

Where a note uses someone else's finding, it says so.

## Licence

GPL-2.0-or-later. See [`LICENSE`](LICENSE).

Elektron and Digitone are trademarks of Elektron Music Machines. This project is not affiliated with or endorsed by Elektron.
