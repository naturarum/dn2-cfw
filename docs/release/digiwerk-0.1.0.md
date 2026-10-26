# Digiwerk 0.1.0

The first release of Digiwerk, custom firmware for the Elektron Digitone II on OS 1.12. **Not made or supported by
Elektron.**

Digiwerk is part of the [Modwerk](https://modwerk.app) effort, the community hub for Elektron firmware mods.

You make your Digiwerk build yourself, from Elektron's own OS 1.12 file, with Digiwerk Patcher. No Elektron firmware
is in this release.

## What's new

Everything: this is the first release. The build is the base plus one pack.

**The base, in every build**

- **DJVU, the Déjà vu LFO.** Nine new LFO waves after RAND: DV-F (a loop of random values), DV-T (the same loop,
  restarted by each trig), and seven that snap the loop to a scale when DEST is the machine's tuning (PMAJ PMIN HIRA
  WTON DORI LYDI OCTA). On these waves MODE sets the loop's length and SPH how much it changes.
  **This is Kári Garðarsson's Déjà vu LFO from the Digitakt II** ([digitakt-ii-research](https://github.com/karigardarsson/digitakt-ii-research)), written anew for the Digitone II. Kári's
  DV-F and DV-T follow the original, Bezronczek's on the Syntakt ([Syntakt Firmware Workbench](https://github.com/Bezronczek/syntakt-firmware-workbench)).

**The pack**

| pack | build | effects | brick check | played on a Digitone II |
|---|---|---|---|---|
| Compressor + Flanger | DJ01 | **CMP3**, the 3-band master compressor; **FLNG**, the flanger; Elektron's delay and reverb | 12 of 12 | 24 Oct 2026 |

- **CMP3** splits Elektron's master compressor into three bands, each following the stock compressor's design. Each
  band has its own page in the MIXER, with THR, ATK, REL, MUP, RAT (1.00 turns it off), a crossover, its own MIX and a
  gain-reduction meter. The settings are saved with the kit.
- **FLNG** sits in the chorus slot. Its TIME knob goes from a phaser-like sweep through the classic flanger to a
  chorus, and FUNC + turn on SPD sets the speed in time with the tempo, from 16/1 to 1/32.

What every knob does:
[README, "What's in it"](https://github.com/naturarum/dn2-cfw/blob/main/README.md#whats-in-it).

## Downloads

| file | what | sha256 |
|---|---|---|
| `Digiwerk-Patcher-0.1.0.zip` | Digiwerk Patcher, the app. macOS 13 or later, Apple silicon and Intel. Notarized | `bfd2c85113e2fb4314b5e3adf39fe9b81bc2773c1f8e5ec02de9bd143f6c8305` |
| `digiwerk-patch-0.1.0.zip` | `digiwerk-patch`, the same in Terminal. Notarized | `c128b5a7746b5aa4e8a586bac493c26ad45ef72fa10acae8bad52b3719d7f37c` |

To check a download, open Terminal, type `shasum -a 256 ` with a space at the end, drag the file onto the window and
press Return. It must print the sha256 above.

**What the patcher makes.** The patcher checks both of these itself; they're here if you want to check by hand.

| file | sha256 |
|---|---|
| Elektron's `Digitone_II_OS1.12.syx` (the input, inside `Digitone_II_OS1.12_dist.zip`) | `7e5b8a733d4ab1fdf7e5d22757f7d8eddaa3cbf71d0858874a791844c02ceb35` |
| `DJ01.syx` (Compressor + Flanger) | `7edf7c59e2aeabd61ebca132a9f031f776475794e04ea37f1635ec8e5a17aed8` |

## How to install

1. Back up your projects and the +Drive with Elektron Transfer.
2. Download Digiwerk Patcher (above), double-click the .zip and open the app.
3. On [Elektron's download page](https://elektron.se/support-downloads/digitone-ii), download **Digitone II OS 1.12**
   (`Digitone_II_OS1.12_dist.zip`). Keep it: Elektron's page lists only the current OS, and you need it to go back.
4. Drop the .zip on the patcher's window. It saves the build next to the .zip, for
   example `DJ01.syx`.
5. Connect the Digitone over USB, drag the build onto Transfer's **Drop files here**, answer **YES** on the Digitone
   and wait for **SUCCESS! REBOOTING**.

Keep the volume down the first time a new build plays. The full guide:
[tools/patcher/README.md](https://github.com/naturarum/dn2-cfw/blob/main/tools/patcher/README.md).

**Going back:** send Elektron's `Digitone_II_OS1.12.syx` with Transfer the same way. If the Digitone doesn't start,
hold FUNC while you switch it on, press TRIG 4 (OS UPGRADE) and send Elektron's file with Transfer's LEGACY OS UPGRADE
mode over a DIN MIDI cable (about 13 minutes).

## Known issues

- The Overbridge plugin knows only Elektron's names and ranges: it offers LFO WAVE only up to RAND, so don't automate
  WAVE from the plugin on a track that uses a new wave. It most likely calls the flanger's TIME "HPF".
- The flanger's sweep follows the tempo when synced, but doesn't restart at the bar.
- Déjà vu loops aren't saved: each power-on makes a new melody from the same settings.
- On FM TONE's PITCH and WAVETONE's MAST, the scale snapping assumes semitones; not checked on a unit yet.
- The compressor's band knobs can't be p-locked and have no MIDI CC.
- On Elektron's OS, a kit saved on Digiwerk plays Déjà vu waves as RAND (shown as "ERR"), plays the stock one-band
  compressor, and plays the flanger's TIME as the chorus's HPF. A Save Kit, +Drive library save or export there drops
  the compressor's band settings.
- macOS only for now.

The full list:
[README, "Compatibility" and "Known limitations"](https://github.com/naturarum/dn2-cfw/blob/main/README.md#compatibility).

## Credits

Digiwerk builds on elekloader (irpina), digikit (Em D, now ElekLabs), selache (Jakob Kastelic), elektron-firmware-tool
(Marcel Bierling), octabam (Sam Banks), and the findings of dn2_firmware_explore (angellinares). The Déjà vu LFO
follows Kári Garðarsson's design for the Digitakt II, after Bezronczek's Syntakt Firmware Workbench and the déjà vu of
Émilie Gillet's Marbles. Everyone, with licences:
[CREDITS.md](https://github.com/naturarum/dn2-cfw/blob/main/CREDITS.md).

Made by naturarum with Claude, Anthropic's AI model, in Claude Code. GPL-2.0-or-later.

Elektron and Digitone are trademarks of Elektron Music Machines. Digiwerk is not made or supported by Elektron.
