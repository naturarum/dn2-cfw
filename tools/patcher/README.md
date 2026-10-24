# Digiwerk Patcher

Digiwerk is custom firmware for the Elektron Digitone II. It is not made or supported by Elektron.

**Digiwerk Patcher** makes a Digiwerk OS file (like `OB01.syx`) from Elektron's own OS 1.12 file. We are not allowed
to share Elektron's firmware, so you download it from Elektron and the patcher turns it into ours. It saves one new
file next to Elektron's file. Elektron's file stays as it was, and nothing is sent to your Digitone.

**What's in a build: a base plus a pack.** Every Digiwerk build has both. The *base* is the light features in the
main OS that a release's builds share: today the Déjà vu LFO (nine new LFO waves) and the fix that lets Overbridge
connect. The *pack* is what differs from build to build: the memory-heavy effects in the sound engine, such as the
3-band compressor and the flanger. On each card, the big icons are the pack's effects, and the small BASE line
lists the base features.

A copy of the patcher carries one or more tested builds. The window lists them if there are several, and says which
one it makes if there is one. The examples below use **OB01**.

## Step by step

Download **Digiwerk Patcher** <!-- MAINTAINER: the release link -->, double-click the .zip, and open the app.
Apple has checked the app (it's notarized), so it opens normally. The first time, macOS asks whether you want to
open an app downloaded from the internet: click **Open**.

The window shows the builds as cards, and the three steps side by side as 01, 02 and 03. It ticks each step when
it's done.

1. **Get Elektron's OS 1.12.** Click **Open Elektron's download page**
   (<https://elektron.se/support-downloads/digitone-ii>) and download **Digitone II OS 1.12**. It arrives in your
   Downloads folder as `Digitone_II_OS1.12_dist.zip`. You don't need to unzip it.
2. **Drop it here.** If the patcher carries several builds, first click the card of the one you want. Each card
   shows the effects it carries (the pixel icons, named under them), what it does, and how it was tested (see
   below). Then drop `Digitone_II_OS1.12_dist.zip` onto the window, or onto the app's
   icon in the Dock. You can also click **Choose File...**. The unzipped folder, or the `Digitone_II_OS1.12.syx`
   inside it, works too. A second later the patcher says **Checked: this is Elektron's original OS 1.12 file** and
   saves `OB01.syx` next to the .zip.
3. **Send it to your Digitone.**
   1. Back up your projects with Elektron Transfer first.
   2. Connect the Digitone over USB and open **Elektron Transfer** (the **Open Transfer** button does it).
   3. Drag `OB01.syx` onto Transfer's **Drop files here** (**Show in Finder** shows you the file).
   4. Answer **YES** on the Digitone, then wait for **SUCCESS! REBOOTING**.

**What "Brick check 12 of 12" means.** Before a build goes into the patcher, it must pass all twelve checks of our
brick checker. That's an automatic test in an emulator of the Digitone II: among other things, the build starts up
to the main screen and writes nothing to the unit's memory that it shouldn't. "Tested on a Digitone II" with a date
means we also ran it on a real unit. "Not yet tested on a unit" means only the brick checker has run it so far.

## How do I go back to Elektron's OS?

Send Elektron's `Digitone_II_OS1.12.syx` to the Digitone the same way, with Transfer. It is inside
`Digitone_II_OS1.12_dist.zip`: double-click the .zip to get it. The patcher's **Back to Elektron's OS** link (top
right) says the same.

## If your Digitone doesn't start

Hold **FUNC** while you switch the Digitone on. The STARTUP menu appears. Press **TRIG 4** (OS UPGRADE), then send
Elektron's `Digitone_II_OS1.12.syx` with Transfer over a **DIN MIDI** cable. (USB doesn't work in the startup menu.)

## What the messages mean

Under each message there's a small grey line with the exact details. If something goes wrong, copy that line into
your report.

| The patcher says | What to do |
|---|---|
| Checked: this is Elektron's original OS 1.12 file. Saved OB01.syx next to ... | Go on with step 3. |
| Got Digitone_II_OS1.12_dist.zip. Now choose the build you want above. | Click one of the builds: the patcher then makes it. |
| ... OB01.syx is already next to it, exactly the build we tested. | Nothing to do: it's the same file. Go on with step 3. |
| That's Digitone II OS 1.11. This needs OS 1.12 ... (or another version) | Download OS 1.12 from Elektron's page (step 1). |
| That's Elektron's OS 1.12 file, but it is damaged or incomplete. | Download it again from Elektron. |
| That's not Elektron's Digitone II OS 1.12 file. | You used another file. Use `Digitone_II_OS1.12_dist.zip` from Elektron. |
| That .zip doesn't have Digitone_II_OS1.12.syx in it. | Use the .zip from Elektron's page. |
| That's OB01.syx already, the file this makes. | You used the patcher's result. Use Elektron's file. |
| That's FL01, a custom OS, not Elektron's OS 1.12 file. | You used one of our builds. Use Elektron's file. |
| There is already a file called OB01.syx in that folder. | Click **Replace** to replace it, or **Cancel** to keep it. |
| I couldn't save OB01.syx in that folder. | Copy `Digitone_II_OS1.12_dist.zip` to your Desktop and drop that copy. |
| Something went wrong ... nothing was saved. | Please report it, with the grey line. The patcher never saves a file it can't vouch for. |

## Is any of Elektron's firmware in the patcher?

No. The patcher holds our own changes and instructions like "copy these bytes from your file". Every Elektron byte
in the result comes from your copy of Elektron's file. Before it starts, the patcher checks that your file is exactly
Elektron's original. Before it saves anything, it checks that the result is exactly the build we tested on a
Digitone II. How it works: `notes/R1.1-patcher.md`.

To check Elektron's file yourself (optional), open Terminal, type `shasum -a 256 ` with a space at the end, drag
`Digitone_II_OS1.12.syx` onto the window and press Return. It must print
`7e5b8a733d4ab1fdf7e5d22757f7d8eddaa3cbf71d0858874a791844c02ceb35`.

## The Terminal tool

`digiwerk-patch` does the same in Terminal (and can be built for Linux and Windows later).

1. Double-click `digiwerk-patch.zip` <!-- MAINTAINER: the release link; ship the tool zipped so it stays executable -->.
   Like the app, it's notarized. The first run may take a moment while macOS checks it.
2. Open Terminal, drag `digiwerk-patch` onto the window, and press Return.
3. If it carries several builds, it lists them and asks for a number: type it and press Return.
4. It asks for the file: drag `Digitone_II_OS1.12_dist.zip` (or the `.syx` inside it) onto the window and press
   Return. You can also give the file straight away: `./digiwerk-patch Digitone_II_OS1.12_dist.zip`.

`./digiwerk-patch --list` lists the builds it carries. `--pack FL01` picks one without asking.

It saves `OB01.syx` next to Elektron's file and prints the same messages as the app. If `OB01.syx` is already
there, it asks before replacing it (type `y` and press Return).

## For the maintainer

- A pack is one tested build. To add one, write `packs/ID.json` (or `out/ID/pack.json`), with `title`,
  `description`, `tested_on_unit` (a date like "23 Oct 2026", or "" until it ran on a unit), `effects` (the pack:
  ids from `cmp3`, `flng`, `tape`, `spng`, each with its icon in `docs/identity/`) and `base` (ids from `djvu`,
  which needs `docs/identity/djvu.svg`, and `overbridge`; `[]` for none). mkpatch.py refuses an unknown id. The DSP fields
  (`dsp`: `code_bytes`, `state_bytes`, `load_percent`) are for a later check stage and may be null. Then run the
  brick checker on `out/ID/ID.syx`. mkpatch.py refuses a pack without an ACCEPT report for exactly that file
  under `out/brickcheck/ID-*/`. It fills in the hashes and the record line (`out/patcher/ID/pack.json`).
- Build: `make PACKS="CMP4 OB01 FL01"` (the packs and the Terminal tool, listing them in that order), `make app
  PACKS="..."` (the app, unsigned; its packs go into `Contents/Resources/packs`), `make test`, and
  `sh app/screens.sh` (PNGs of the window, rendered offscreen). Everything lands in `out/patcher/`.
- The pixel art (the mark, the wordmark, the effect icons) is `docs/identity/*.svg`, made by its `make_icons.py`.
  `app/identity.sh` copies it into the app's `Resources/identity` at build time. The app draws each one at whole
  device pixels, without smoothing.
- The app's Help link and the startup-menu link point to this README on GitHub (`Core.guide` in
  `app/Sources/Core.swift`, marked MAINTAINER).
- Signing: set `DEVELOPMENT_TEAM` in `app/project.yml` (bundle id `com.naturarum.digiwerk-patcher`). Archive,
  export as Developer ID, then notarize and staple: the commands are in `app/build.sh`'s comments. The Terminal tool
  is notarized by submitting its zip (a bare tool can't be stapled; macOS checks it online on first run).
