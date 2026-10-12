---
tags: [digitone2, collaboration]
updated: 2026-10-06
---
# Overlap with dn2_firmware_explore

A comparison of this project with dn2_firmware_explore, made on 2026-10-06 so the two don't duplicate work.

Their repo: <https://github.com/angellinares/dn2_firmware_explore> ("dnfw"), AGPL-3.0-or-later, very active (last commit 2026-10-06), with a web builder at <https://angellinares.github.io/dn2_firmware_explore/>. Our local read-only clone is `Elektron-CFW/vendor/dn2_firmware_explore`. Full survey: `Elektron-CFW/notes/prior-art-2026-10.md`.

## The key fact for working together
**1.11 to 1.12 changed only the main OS (+4 KB).** The DSP program, bootstrap, board test and Outbox 8 firmware are byte-identical (we hashed them). So:
- **DSP work transfers both ways unchanged:** their DSP splice and Waverider, our stock-FX emulator and new effects.
- **ColdFire mods need re-addressing per OS version:** their mods are 1.11, our core is 1.12.

## What they have that we don't
- **11 mods confirmed on hardware (1.11):** midiarp, layermidi, arpmodes, arpplocks, lfo4, lfowaves, moddest, fxmod, bootscreen, transients, songguard. The **arp on MIDI tracks**, one of our tier-1 goals, is among them.
- **A web builder:** pick mods and options in the browser, and it produces the same bytes as their command line (tested).
- **A mod compatibility matrix:** every pair tested in both orders. Some exclude each other because each needs the start-up hook.
- **DSP code insertion (our T3.8):** a documented method for adding code to the DSP. Their own DSP code (Waverider) has partly passed on the instrument.
- **Hardware-tested safety facts:**
  - selache mis-encodes negative shift immediates.
  - Parts of L1 memory are cache.
  - The DSP section must stay under 1 MiB.
- **Measured on hardware:** DSP load is 63-65% stock; the DSP clock is 1 GHz; they have a USB probe for reading memory on the unit.
- **Large maps of the UI and parameters:** display path, UI map, LFO and modulation internals, FX parameter space, +Drive storage.
- **digikit contributions:** open pull requests, including a headless panel driver, DN2 boot fixes and an EMAC fix.

## What we have that they don't
- **OS 1.12 support:**
  - digikit boots 1.12;
  - elekloader has a Digitone II 1.12 profile with tests;
  - our core-dn2 hook bus for elekloader works in the emulator (UI events, plus MIDI step and note hooks).
- **The sequencer running in the emulator.** Their docs say it "does not play under the emulator" and that the arp's interrupt half "cannot run there". Ours runs at the project tempo, with:
  - DIN MIDI in and out (virtual MIDI ports on the Mac);
  - LEDs drawn;
  - 0.8x real time with the audio clock on, 8x without.

  On a local digikit branch, not yet offered upstream.
- **sharcvm:** our own SHARC interpreter. It renders the stock chorus, delay, reverb, compressor and the whole DSP block handler offline, and it is checked against 2,039 selache-compiled tests. It found two selache compiler bugs.
- **New effects in portable C:** tape delay, 3-band compressor, flanger in progress. The SHARC build matches the Mac build, and an A/B page compares them with the emulated stock effects.
- **Safety work:**
  - a brick checker;
  - a flash-writer audit;
  - proof that the STARTUP menu survives any stored OS;
  - a section-4 in-place hazard, found and reported as a note for elekloader.

## Where we duplicated (so far)
- The file format and seal; recovery; the ColdFire-to-DSP frame; the FX parameter mapping; the DSP memory map.
- The UI drawing (our T5.0) and FX parameter storage (our T6.0). **Both paused on 2026-10-06** because they overlap.
- Our arp design (T2.2) is close to their shipped midiarp.
