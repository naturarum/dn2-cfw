# Credits

Digiwerk is made by naturarum with Claude, Anthropic's AI model, in Claude Code. It builds on the projects and people
below. For each one: who made it, its licence, and what we use from it.

- **Code:** some of their code is adapted into our files. Each such file names its origin in its header, and
  [NOTICE](NOTICE) lists them.
- **Tool:** we run their program. None of its code is in Digiwerk.
- **Facts:** their findings, designs or ideas, restated in our own words and written as our own code.

## Projects

### elekloader

- **By** irpina and contributors: <https://github.com/irpina/elekloader>
- **Licence:** GPL-2.0-or-later (since 2026-09-27; its `js/` engine is GPL-3.0-or-later).
- **Tool, code.** Every change Digiwerk makes to the main OS is an elekloader mod, built with our fork's Digitone II
  1.12 profile ([branch `dn2-1.12`](https://github.com/naturarum/elekloader/tree/dn2-1.12)). Its mod format, its core
  and hook model and its device profiles are what ours follow. The brick checker's message splitting and 8-in-7
  decoding come from its `syx.py`. Digiwerk Patcher decodes Elektron's packed sections with the codec constants of
  elekloader's `codec/elz.py`, and derives the Digitone's seal key the way elekloader's `seal_key` does.

### digikit

- **By** Em D (m-dwyer), now at ElekLabs: <https://github.com/m-dwyer/digikit>
- **Licence:** GPL-2.0-or-later at the commit we use (`21b38b9`) and in our fork. Upstream is AGPL-3.0 since
  2026-10-18.
- **Tool, code.** Its ColdFire emulator, which our fork
  ([branch `dn2-1.12`](https://github.com/naturarum/digikit/tree/dn2-1.12)) extends to OS 1.12, runs the brick
  checker's start-up and flash checks and every emulator test. We also use its SHARC+ table decoder, its ColdfireEMAC
  Ghidra language and `McfLabels.java`. Four of our scripts adapt parts of it (NOTICE).

### selache

- **By** Jakob Kastelic (js216): <https://github.com/js216/selache>
- **Licence:** GPL-3.0 (the tools), MIT (the runtime).
- **Tool, code.** Its compiler, assembler and linker (selcc, selas, seld) build all of our DSP code. Its 2,039 test
  programs check our SHARC interpreter. Two small files of ours follow its MIT sources (NOTICE).

### elektron-firmware-tool

- **By** Marcel Bierling (mischa85): <https://github.com/mischa85/elektron-firmware-tool>
- **Licence:** MIT.
- **Tool, facts.** The `.syx` and ELE3 formats, the rebuild and the seal. We run it to pack the DSP section of our
  builds. None of its code is in Digiwerk.

### octabam

- **By** Sam Banks: <https://github.com/sambanks/octabam>
- **Licence:** MIT.
- **Facts, code.** The template for the whole approach: build from the user's own OS file, with emulator checks as
  gates. One small loop in `scripts/t37_compare.py` follows its `tools/harness/send_probe.py` (NOTICE).

### dn2_firmware_explore

- **By** angellinares: <https://github.com/angellinares/dn2_firmware_explore>
- **Licence:** AGPL-3.0-or-later.
- **Inspired by their research.** Digiwerk builds on their published findings on the Digitone II's OS 1.11, credited
  in the notes where they're used; our code and text are our own. Among them:
  - the way we put new code into the DSP section, the memory our DSP code runs in, the 1 MiB limit on that section,
    the cache map, and the assembler's shift mis-encoding they found on the chip;
  - the FX page records, and renaming by repointing strings;
  - the parameter table: they grew it first, on 1.11;
  - the kit and sound storage, and the frame census method;
  - the stock DSP load, measured by idle time;
  - the LFO facts behind the Déjà vu LFO, and where the trig modes ONE and HALF stop;
  - the arpeggiator's step, which plays SHUF and RAND as CYCL;
  - the start-up animation's structure;
  - that Overbridge may need the computer restarted before it accepts a changed OS.

### DNX

- **By** NoiseAndMatter, the author of dn2_firmware_explore: <https://github.com/NoiseAndMatter/DNX>
- **Licence:** AGPL-3.0.
- **Inspired by its research.** Its kit captures: the FX offsets in the kit record, which agree with ours, and that
  the bytes our compressor keeps its band settings in are zero in 2,176 real kits.

### syxgrid-digitone-ii

- **By** xrcstrecords: <https://github.com/xrcstrecords/syxgrid-digitone-ii>
- **Licence:** AGPL-3.0 (its SysEx map: CC BY 4.0).
- **Facts only.** Where the compressor's settings sit in the kit record.

### digirta

- **By** Alez22: <https://github.com/Alez22/digirta>
- **Licence:** MIT.
- **Facts only.** Band meters drawn through elekloader's events, with ballistics: the prior art for our compressor's
  meters.

### capicola

- **By** heavylight-industries: <https://github.com/heavylight-industries/capicola>
- **Licence:** AGPL-3.0.
- **Facts only.** DSP ideas we studied for the effects. No code.

### UCL

- **By** Markus F.X.J. Oberhumer, <http://www.oberhumer.com/opensource/ucl/>
- **Licence:** GPL-2.0-or-later.
- **Code.** The `nrv2b()` decoder in our developer script `ele3_unpack.py` follows UCL's NRV2B decoder, as used by
  UPX.

### Unicorn

- **By** Nguyen Anh Quynh, Dang Hoang Vu and contributors, <https://www.unicorn-engine.org/>
- **Licence:** GPL-2.0.
- **Tool.** The CPU emulator under digikit's emulator (digikit uses a patched copy), and so under the brick checker.

### Ghidra

- **By** the National Security Agency (NSA), <https://github.com/NationalSecurityAgency/ghidra>
- **Licence:** Apache-2.0.
- **Tool.** We read the Digitone II's main OS with it (the `ghidra/` project, `scripts/decompile_dump.py`).

## Designs and ideas

### Kári Garðarsson

- **What:** the "Musical WAVE / Déjà Vu" LFO for the Digitakt II, <https://github.com/karigardarsson/digitakt-ii-research>
- **Digiwerk's Déjà vu LFO is Kári's design:** the nine waves, their names and how they behave. We wrote the code anew for
  the Digitone II's LFOs.
- **Licence:** the Digitakt II All-in-One v0.3.0-beta release, where the Déjà vu LFO lives, is AGPL-3.0-or-later (its
  release notes, 2026-10-23); the earlier research in the same repository is MIT.
- **Facts only.** Digiwerk's Déjà vu LFO takes its names and behaviour from that project's user guide. None of its
  source was read or copied: ours is our own code for the Digitone II's LFOs.
- **Idea only.** The bring-your-own-`.syx` Windows builder in the same repo and the one of
  [Synthack-OS](https://github.com/karigardarsson/Synthack-OS) (MIT, Angelo-politek and Syntakt+ contributors) were the
  model for Digiwerk Patcher.

### Bezronczek

- **What:** the [Syntakt Firmware Workbench](https://github.com/Bezronczek/syntakt-firmware-workbench) (MIT), home of
  the original Déjà vu LFO, DV-F and DV-T on the Syntakt. Kári Garðarsson's release credits it as its reference for
  the DV-F and DV-T behaviour. We didn't use it directly.

### Émilie Gillet

- **What:** the déjà vu random loop of Marbles, Émilie Gillet's design. The idea of the Déjà vu LFO comes from it.
- **Facts only.** The one detail we took from it is the squared curve of its déjà vu knob, for SPH. The Marbles code (MIT,
  <https://github.com/pichenettes/eurorack>) is not in Digiwerk.

### Elektron

- **What:** the Digitone II and its OS. The bands of CMP3 follow Elektron's master compressor, as we measured it in
  their code. The delay and the reverb in the Compressor + Flanger pack are Elektron's own code, taken from your own
  OS file.
- Elektron and Digitone are trademarks of Elektron Music Machines. Digiwerk is not made or supported by Elektron.

### The Modwerk community

- **What:** <https://modwerk.app>, the community hub for Elektron firmware mods. Digiwerk is part of the Modwerk effort.
- **Facts.** Shared knowledge about Elektron firmware.
