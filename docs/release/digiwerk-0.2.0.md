# Digiwerk 0.2.0

Digiwerk is custom firmware for the Elektron Digitone II on OS 1.12. 0.2.0 brings a new base and two new effects, a
tape echo and a spring reverb, and you now pick the effects one FX slot at a time. **Not made or supported by
Elektron.**

Digiwerk is part of the [Modwerk](https://modwerk.app) effort, the community hub for Elektron firmware mods.

You make your Digiwerk build yourself, from Elektron's own OS 1.12 file, with Digiwerk Patcher. No Elektron firmware
is in this release.

## What's new

**The base, in every build**

- **KEY, the LFO in the track's key.** A Déjà vu wave snapped to the scale and root of the track's KEYBOARD SETUP:
  whatever note the trig plays, the melody stays in the key over the whole range of DEP. On an LFO's WAVE, past OCTA,
  with DEST on the machine's tuning.
- **LFO ONE and HALF** run one whole cycle, or half of one, from where SPH starts them, then hold. Elektron's OS 1.12
  cuts them short.
- **Arp SHUF, RAND and TM**, after CYCL. SHUF plays every held note in every octave of RANGE once per cycle, in a new
  order each time; RAND a random one at each step. TM is a Turing Machine loop: hold one note and the arp plays a
  melody from it in the track's scale, one note per step of ARP LENGTH, and LOOP CHANGE sets how often a step takes
  a new note.
- **The arp on MIDI tracks.** The ARP key and menu work on MIDI-machine tracks: trigs and the keys you play go through
  the arp, out on the track's MIDI channel, with SHUF, RAND and TM too.
- **The Digiwerk intro** at power-on.
- **DJVU**, the Déjà vu LFO, as in 0.1.0.

**The effects, one per FX slot**

- **TAPE**, new: a tape echo in the delay slot, with three playback heads at one, two and three times head 1's time,
  a band filter in the feedback loop and tape wear (AGE).
- **SPNG**, new: a spring reverb in the reverb slot, three springs that drip and boing; MODE sets their character and
  DIRT breaks up the tail.
- **CMP3**, the 3-band master compressor, and **FLNG**, the flanger, as in 0.1.0.

**Digiwerk Patcher** has a slot for each effect: keep Elektron's or take ours, and the patcher makes the build for
that combination. Its MANUAL page explains every effect and base feature.

**The builds**

| build | compressor | chorus slot | delay slot | reverb slot | brick check | played on a Digitone II | sha256 of the build |
|---|---|---|---|---|---|---|---|
| **EEEE** | Elektron's | Elektron's | Elektron's | Elektron's | 12 of 12 | not yet | `06e2e6a18c64cf9eefcf68509b618298550ed3ffd7bd65181e8eae4b406674da` |
| **CEEE** | **3-band compressor** | Elektron's | Elektron's | Elektron's | 12 of 12 | not yet | `925e82ae5d935cd0a1fdb6894dfd5a4b408ec47ea1609a0d47c1d7a54b6bd261` |
| **EFEE** | Elektron's | **flanger** | Elektron's | Elektron's | 12 of 12 | not yet | `355bee2cd58a61bfb564e406d1e8f72b50e0b973d0f693d32afb757ff1021e36` |
| **EETE** | Elektron's | Elektron's | **tape echo** | Elektron's | 12 of 12 | not yet | `5caa78166a014cb233e86756f400a0729fd4851dd4c8dcbec59153ada738abf2` |
| **EEES** | Elektron's | Elektron's | Elektron's | **spring reverb** | 12 of 12 | not yet | `1b0f5bc2bd8656a4a4e12266f1de912e55a77a632b44ec5dee2884558f548ef5` |
| **CFEE** | **3-band compressor** | **flanger** | Elektron's | Elektron's | 12 of 12 | 29 Oct 2026 | `cdd04164165bb8001412b29e7b320fde638e0ec5971b2a31235b7f21370e92cf` |
| **CETE** | **3-band compressor** | Elektron's | **tape echo** | Elektron's | 12 of 12 | not yet | `65eab76547211b8df18f094cf61c94348ca86f050cca1b4efc16d4dfc5a46615` |
| **CEES** | **3-band compressor** | Elektron's | Elektron's | **spring reverb** | 12 of 12 | not yet | `13d77c5d9706286da7e050ffd78746c5222ba7891b0004634ef678aed16593ba` |
| **EFTE** | Elektron's | **flanger** | **tape echo** | Elektron's | 12 of 12 | not yet | `db91033a37ceddbc852dcc789766a8afe55bcd8052bc195afccadcf84eac854c` |
| **EFES** | Elektron's | **flanger** | Elektron's | **spring reverb** | 12 of 12 | not yet | `5c8042e51b77c19a8f75d638c2817ac82e67efd31e083770bb5389f8f6279161` |
| **EETS** | Elektron's | Elektron's | **tape echo** | **spring reverb** | 12 of 12 | not yet | `6123d911c75e34136df1f1435096f00a3ec44558d9a0b1b249176e6e27e046e5` |
| **CFTE** | **3-band compressor** | **flanger** | **tape echo** | Elektron's | 12 of 12 | 29 Oct 2026 | `eafa99b3f73fdd7635c86dbd61f81db73b65c8181bd068e00125196950c84f63` |
| **CFES** | **3-band compressor** | **flanger** | Elektron's | **spring reverb** | 12 of 12 | not yet | `854e24b624f3cf58c5a45e4618b9a5f933a98d9041894ce7af8df5474a9543fe` |
| **EFTS** | Elektron's | **flanger** | **tape echo** | **spring reverb** | 12 of 12 | not yet | `b28c9958a34e88d22d6d98562104ce1d65efefc0c910f791ce02119e2278f35b` |

Each build is named after its four slots, compressor, chorus, delay, reverb: C, F, T or S where ours runs, E where
Elektron's does. CMP3 with TAPE and SPNG doesn't fit in the DSP's memory, with or without FLNG. The patcher checks both
hashes itself: Elektron's `Digitone_II_OS1.12.syx` (inside `Digitone_II_OS1.12_dist.zip`) must be
`7e5b8a733d4ab1fdf7e5d22757f7d8eddaa3cbf71d0858874a791844c02ceb35` before it starts, and the build must be the one above
before it saves it.

## Downloads

| file | what | sha256 |
|---|---|---|
| `Digiwerk-Patcher-0.2.0.zip` | Digiwerk Patcher, the app. macOS 13 or later, Apple silicon and Intel. Notarized | `31f555955bda2e5c8e588dbe5aa2759b1cb218500b34dfb4abfd052b8cd264be` |

To check a download, open Terminal, type `shasum -a 256 ` with a space at the end, drag the file onto the window and
press Return. It must print the sha256 above.

## How to install

1. Back up your projects and the +Drive with Elektron Transfer.
2. Download Digiwerk Patcher (above), double-click the .zip and open the app.
3. On [Elektron's download page](https://elektron.se/support-downloads/digitone-ii), download **Digitone II OS 1.12**
   (`Digitone_II_OS1.12_dist.zip`). Keep it: Elektron's page lists only the current OS, and you need it to go back.
4. Pick your effects in the patcher's four slots.
5. Drop the .zip on the patcher's window. It saves the build next to the .zip, named after its slots, for example
   `CFTE.syx`.
6. Connect the Digitone over USB, drag the build onto Transfer's **Drop files here**, answer **YES** on the Digitone
   and wait for **SUCCESS! REBOOTING**.

Keep the volume down the first time a new build plays. The full guide:
[tools/patcher/README.md](https://github.com/naturarum/dn2-cfw/blob/main/tools/patcher/README.md).

**Going back:** send Elektron's `Digitone_II_OS1.12.syx` with Transfer the same way. If the Digitone doesn't start,
hold FUNC while you switch it on, press TRIG 4 (OS UPGRADE) and send Elektron's file with Transfer's LEGACY OS UPGRADE
mode over a DIN MIDI cable (about 13 minutes).

## Known issues

- The Overbridge plugin knows only Elektron's names and ranges: it offers LFO WAVE only up to RAND, so don't automate
  WAVE from the plugin on a track that uses a new wave.
- The tape echo's MODE and AGE can't be p-locked, and AGE has no MIDI CC.
- While a MIDI track's arp is on, its trigs' MIDI p-locks aren't sent.
- The shuffle order, TM's loop and the Déjà vu loops aren't saved: each power-on makes new ones from the same settings.
- The compressor's band knobs can't be p-locked and have no MIDI CC.
- On Elektron's OS, a kit saved on Digiwerk plays Elektron's own effects with our values, Déjà vu waves as RAND
  (shown as "ERR"), and opens a track whose arp is SHUF, RAND or TM with its arp off.
- macOS only for now.

The full list:
[README, "Compatibility" and "Known limitations"](https://github.com/naturarum/dn2-cfw/blob/main/README.md#compatibility).

## Credits

Digiwerk builds on elekloader (irpina), digikit (Em D, now ElekLabs), selache (Jakob Kastelic), elektron-firmware-tool
(Marcel Bierling), octabam (Sam Banks), and the findings of dn2_firmware_explore (angellinares). The Déjà vu LFO
follows Kári Garðarsson's design for the Digitakt II, after Bezronczek's Syntakt Firmware Workbench and the déjà vu of
Émilie Gillet's Marbles. The arp's TM takes its idea from Tom Whitwell's Turing Machine. Everyone, with licences:
[CREDITS.md](https://github.com/naturarum/dn2-cfw/blob/main/CREDITS.md).

Made by naturarum with Claude, Anthropic's AI model, in Claude Code. GPL-2.0-or-later.

Elektron and Digitone are trademarks of Elektron Music Machines. Digiwerk is not made or supported by Elektron.
