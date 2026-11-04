# Digiwerk 0.3.0

Digiwerk is custom firmware for the Elektron Digitone II on OS 1.12. 0.3.0 adds multitrack USB audio to every build.
**Not made or supported by Elektron.**

Digiwerk is part of the [Modwerk](https://modwerk.app) effort, the community hub for Elektron firmware mods.

You make your Digiwerk build yourself, from Elektron's own OS 1.12 file, with Digiwerk Patcher. No Elektron firmware
is in this release.

## What's new

- **Multitrack USB audio**, in every build: a fourth USB CONFIG choice, **USB MULTI/MIDI** (SETTINGS > SYSTEM > USB
  CONFIG). With it the Digitone shows up as a class-compliant USB audio device (USB Audio Class 2.0) with 42 inputs
  at 48 kHz and 24 bits, no driver needed. We've used it with a Mac and with AUM on an iPhone. USB AUDIO/MIDI stays
  Elektron's stereo mode, and OVERBRIDGE and USB MIDI are unchanged.

  | inputs | what |
  |---|---|
  | 1-32 | the 16 tracks as stereo pairs: track 1 on 1-2, track 2 on 3-4, and so on to track 16 on 31-32 |
  | 33-34 | the main mix |
  | 35-36 | the delay slot's return |
  | 37-38 | the reverb slot's return |
  | 39-40 | the chorus slot's return |
  | 41-42 | the Digitone's audio inputs |

**The base, in every build**

- **Multitrack USB audio** (above).
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
- **DJVU**, the Déjà vu LFO.

**The effects, one per FX slot**

- **TAPE**: a tape echo in the delay slot, with three playback heads at one, two and three times head 1's time, a band
  filter in the feedback loop and tape wear (AGE).
- **CMP3**, the 3-band master compressor, and **FLNG**, the flanger.

**Digiwerk Patcher** has a slot for each effect: keep Elektron's or take ours, and the patcher makes the build for
that combination. Its MANUAL page explains every effect and base feature.

**The builds**

| build | compressor | chorus slot | delay slot | reverb slot | brick check | played on a Digitone II | sha256 of the build |
|---|---|---|---|---|---|---|---|
| **EEEE** | Elektron's | Elektron's | Elektron's | Elektron's | 12 of 12 | 4 Nov 2026 | `95378fa9ed2d51cb8adf0d4075f80c3bd708e58626ec71452909858a210ec4d1` |
| **CEEE** | **3-band compressor** | Elektron's | Elektron's | Elektron's | 12 of 12 | 4 Nov 2026 | `95550dc47df88d72b9d8a4e031d39323f0b74a0cb51940ae3e7b5df5dc8f5afe` |
| **EFEE** | Elektron's | **flanger** | Elektron's | Elektron's | 12 of 12 | 4 Nov 2026 | `4a8db3692b0ecdc30148e91c5daed1e5079ce10b3b6fa2e51f05dd201e017c66` |
| **EETE** | Elektron's | Elektron's | **tape echo** | Elektron's | 12 of 12 | 4 Nov 2026 | `3c8803758c633e15a6e7600f453738b370c97fe13299ffca7b616043fd69428b` |
| **CFEE** | **3-band compressor** | **flanger** | Elektron's | Elektron's | 12 of 12 | 4 Nov 2026 | `6c7d6dba3cdb5253b312c5116c0ee847f0f879dc4f8678373782493b9b13e19b` |
| **CETE** | **3-band compressor** | Elektron's | **tape echo** | Elektron's | 12 of 12 | 4 Nov 2026 | `79b22add2b3014bd25601d2ed810f9f76b44e45cb8c30dc96bdd32559b0e78a8` |
| **EFTE** | Elektron's | **flanger** | **tape echo** | Elektron's | 12 of 12 | 4 Nov 2026 | `334675bd05ec96a6656313143a4e2a0bab67d4fb7ecf985964d2819c158a3b26` |
| **CFTE** | **3-band compressor** | **flanger** | **tape echo** | Elektron's | 12 of 12 | 4 Nov 2026 | `ff646e5e4ae9d007747a484cde20a6eabce8f42b700c6d1ba11388902953376d` |

Each build is named after its four slots, compressor, chorus, delay, reverb: C, F or T where ours runs, E where
Elektron's does. The six builds with the spring reverb stay withdrawn, as in 0.2.1. The patcher checks both
hashes itself: Elektron's `Digitone_II_OS1.12.syx` (inside `Digitone_II_OS1.12_dist.zip`) must be
`7e5b8a733d4ab1fdf7e5d22757f7d8eddaa3cbf71d0858874a791844c02ceb35` before it starts, and the build must be the one above
before it saves it.

## Downloads

| file | what | sha256 |
|---|---|---|
| `Digiwerk-Patcher-0.3.0.zip` | Digiwerk Patcher, the app. macOS 13 or later, Apple silicon and Intel. Notarized | `79a28f4a7601ef51c8eed2045c37b3fc5cf4a06b30f39cd972fa2ac13428a864` |

To check a download, open Terminal, type `shasum -a 256 ` with a space at the end, drag the file onto the window and
press Return. It must print the sha256 above.

**In your browser:** [Digiwerk Patcher on the web](https://naturarum.github.io/dn2-cfw/) does the same; nothing is uploaded.

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

**Going back:** send Elektron's `Digitone_II_OS1.12.syx` with Transfer the same way. If USB CONFIG is on USB
MULTI/MIDI, pick one of Elektron's three choices first: Elektron's OS reads a saved USB MULTI/MIDI as USB MIDI. If the
Digitone doesn't start, hold FUNC while you switch it on, press TRIG 4 (OS UPGRADE) and send Elektron's file with
Transfer's LEGACY OS UPGRADE mode over a DIN MIDI cable (about 13 minutes).

## Known issues

- USB MULTI/MIDI: 48 kHz and 24 bits only; all 42 inputs are always sent, whatever USB OUT is set to; the inputs
  have numbers, not names.
- The Overbridge plugin knows only Elektron's names and ranges: it offers LFO WAVE only up to RAND, so don't automate
  WAVE from the plugin on a track that uses a new wave.
- The tape echo's MODE and AGE can't be p-locked, and AGE has no MIDI CC.
- While a MIDI track's arp is on, its trigs' MIDI p-locks aren't sent.
- The shuffle order, TM's loop and the Déjà vu loops aren't saved: each power-on makes new ones from the same settings.
- The compressor's band knobs can't be p-locked and have no MIDI CC.
- On Elektron's OS, a kit saved on Digiwerk plays Elektron's own effects with our values, Déjà vu waves as RAND
  (shown as "ERR"), and opens a track whose arp is SHUF, RAND or TM with its arp off.
- After a transfer the Digitone can stay on SUCCESS! REBOOTING (once in our tests). Switch it off and on: the new
  OS is already written by then.
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
