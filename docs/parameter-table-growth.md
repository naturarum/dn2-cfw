# Growing the Digitone II parameter table (OS 1.12)

*2026-10-13. The addresses and emulator runs are in
[`notes/T6.1-table-growth-study.md`](../notes/T6.1-table-growth-study.md) (the study) and
[`notes/T6.5-table-growth.md`](../notes/T6.5-table-growth.md) (the build), which were written before
anything was flashed. The unit runs are logged in [`HANDOFF.md`](../HANDOFF.md) (2026-10-13) and
[`ROADMAP.md`](../ROADMAP.md) (T6.5).*

Every knob on the Digitone II is a row in one table inside the OS. On OS 1.12 it has 321 rows and no
room after them. We now move it at start-up into free memory with room for 384 rows. Two builds have
run on my unit:

- **GT00** moves the table and adds no rows. It saves and loads projects on the +Drive as before, and
  it passes a cold start.
- **GT01** adds 17 rows for a 3-band compressor, on no page yet. My report: "all good". Save, load and
  a power cycle were not reported for it separately.

Nothing changes on screen yet: the new rows are hidden. This is the groundwork for new knobs, starting
with the compressor's pages.

dn2_firmware_explore (dnfw) grew this table first, on OS 1.11, for their LFO4 mod (section 2). Their
notes on the table were our starting point. Ours does it on 1.12, as an elekloader mod that several
mods can add rows to, and resizes the MIDI input cache so new rows can take a CC.

## Before you try this

- No firmware is shared. You build from Elektron's own 1.12 file (README, "Getting started").
- It has run on one unit, mine, since 2026-10-13. param-grow has only run on top of FTN1 (our flanger
  and tape DSP, our core, fx-rename).
- Back up your +Drive with Transfer first. If a build won't start: hold FUNC at power-on, press TRIG 4
  (OS UPGRADE), and send the stock OS over DIN MIDI (README, "If you flash anything").
- Never flash `param-test-dn2` or `fx-page2-dn2`.
- Nothing in elekloader or param-grow checks the rules in section 6. Our build script checks FX rows
  only. A wrong row can write past the map builder's tables, replace a stock knob, or put stray
  p-locks into your patterns that stay after you go back to stock.

## 1. What the table is

One row per parameter, 60 bytes each, at `0x401f83a4` in the 1.12 image:

| offset | field |
|---|---|
| `+0x00` | group: the set and page family (chorus, delay, a machine...) |
| `+0x04` | **k**: the slot that holds the value in its set |
| `+0x08..+0x14` | min, max, default, bipolar |
| `+0x18`, `+0x1c` | MIDI CC, NRPN |
| `+0x20` | an id sent to Overbridge |
| `+0x28..+0x38` | names, value formatter, suffix |

A set is where values live: the sound (one per track), the kit's FX, the trig and the MIDI track each
have their own slots. Screens, encoders, MIDI, LFO destinations and p-locks all reach a parameter
through its row. A second table in RAM holds each row's display data: glyph, lock flags, randomiser.

dnfw's `docs/modulation-mask.md` uses the same offsets. Their LFO4 build plan counts each row from 8
bytes earlier (group at +8, slot at +12), so its offsets are ours plus 8.

## 2. What makes it hard

- **No room.** Another table starts right after it.
- **The size is baked into the code.** 56 address operands, in 53 functions, hold its address. 58
  compares, in 55 functions, test a row id against the row count. 57 of them are changed; the 58th is
  in the display table's reader, which becomes a jump.
- **Other things are sized by it**: the display table and the MIDI CC/NRPN input cache.

dnfw mapped this on 1.11: the row layout (`docs/modulation-mask.md`), the 56 address sites and the
bound counts (`docs/lfo4-feasibility.md`, `docs/parameter-table-consumer.md`), the slot tables
(`docs/parameter-set-tables.md`) and the dead "ERR" rows. Their LFO4 first planned to reuse the ERR
rows, then found there was no block of ten spare rows. The version they built moves the table into
their mod area, adds ten rows, and diverts the display table's reader for them. A read watch in their
emulator shows the old table is never read again ([`docs/lfo4-build-plan.md`](https://github.com/angellinares/dn2_firmware_explore/blob/main/docs/lfo4-build-plan.md),
2026-09-21). A build with the moved table ran on their unit that evening, and their README marks
lfo4 "confirmed on hardware". For Waverider's pages they chose a label hook instead (`docs/waverider-m7-pages.md`).

## 3. The fact that makes it safe

**Row ids are never saved.** Kits, sounds, patterns, p-locks, LFO destinations and modulation
destinations store a value by its slot k, through fixed k-to-stored-id tables. The slot maps and the
CC/NRPN maps are rebuilt from the table at every boot (`0x400dc634`). The page records are built at
boot too, from constants in the code (`0x400c2c4e`), not from the table.

So moving the table cannot by itself change how a project is stored. New rows are another matter:
what a new row can do to a project depends on its k and its display flags, and it is safe only within
the rules of section 6.

## 4. What changed

Every edit keeps the code the same length; all but one only change an operand. The exception is the
display table's reader, whose first 10 bytes become a `jmp` to our code and two `nop`s. The site list
is generated from the stock image (`scripts/t65_make_mod.py`), and each site carries the stock bytes it
expects: elekloader refuses an image that differs.

| what | sites |
|---|---|
| instructions holding the table's address | 56 |
| row-count compares (`#0x141` to `#0x180`, `#0x140` to `#0x17f`) | 57 |
| the display table's reader, `0x400c257c`: a jump to our code | 1 |
| MIDI input cache constants (5.2) | 33 |
| the boot hook and the static initializer's call | 2 |
| **total** | **149** |

Five compares look like the row count but are not. They stay, and the build checks that.

**Where and when.** The grown table lives at `0x47D00000`, in DDR the stock OS never touches.
`pg_boot` runs from the OS entry's call into its C start (`0x40000568`). That is after the OS's own
start-up code (SRAM copies, DDR clear) and our core's boot copy, and before any of the OS's C code. It
copies the 321 stock rows, fills the rest with copies of row 0 (what stock reads for any out-of-range
id), and writes the rows other mods add. In the emulator the table is complete about 83 million
instructions before its first reader.

**Why a copy at boot.** There is no free 23 KB inside the image, and memory past its end is cleared at
boot, so code must put a grown table in place either way. Copying from the image as loaded ships no
Elektron bytes and keeps other mods' edits to the stock rows (our `fx-rename`'s names). dnfw builds
theirs from the user's own image when the mod is applied, which ships no Elektron bytes either.

**The display table** stays where it is. Its reader now jumps to our code: a stock row reads its stock
entry, a new row its own (copied from a stock row it names), anything else row 0's, as on stock.
dnfw's LFO4 diverts the same reader for its ten rows.

**The map builder's bound** is raised too, so the OS files new rows in its slot, CC and NRPN maps.
dnfw found the trap here: the builder files each row by its k into tables of 101 entries, and LFO4's
slots (101-108) would have written past them. So they left that bound alone, and LFO4 keeps its
values in its own table. Our rows keep k inside their set's table (0..100 for FX), so we can raise
it. Our build script checks this for FX rows (section 9).

## 5. Two traps

Both were found and handled in the emulator. Neither has run on the unit: GT01's rows have no CC and
sit on no page.

### 5.1 P-locks come from the display flags

A parameter can be p-locked when bits 0 and 3 of its display flags are clear; its group does not
matter. A lock is stored as (track, k), as if it belonged to the track's sound. In the emulator, a new
FX row with a lockable display entry took a lock on k 70, and the project then held a lock on the
sound's slot 70, the filter envelope delay. Any OS, stock included, would play that back. Our own
earlier page experiment (`fx-page2-dn2`, emulator only) had this bug.

**Handled (emulator):** every send-FX row sets bit 3. Synth rows, which should be lockable, would keep
flags 0; none has been built yet.

### 5.2 The MIDI input cache overflows

MIDI CC and NRPN input goes through a `ParameterUpdateCache` sized for 321 rows. In the study's
emulator run, a CC sent to a new row wrote into the cache's other bank, and the next flush sent
changes for track 1 that nobody had sent.

**Handled (emulator):** 33 constants in the cache's five functions are recomputed for 384 rows. The
cache grows by 12,852 bytes. Over 7,100 MIDI messages, GT00 and GT01 make the same 991 parameter sets
as FTN1, the build before. Every store lands inside the cache (4,765 on GT00, 4,694 on GT01, 4,699 on
FTN1), and nothing is flushed that was not sent. On the test build GT1T (emulator only, never flashed),
a test row in the last slot took CC 3 and NRPN 307 correctly.

## 6. Rules for a new row

1. **A free k inside its set's table**: 0..100 for the sound, FX and MIDI sets, 0..25 for trig. One
   row per k: the map builder lets the last row win, so a taken k silently replaces a stock row. In
   the sound set, never k 65 or 100 (they are not saved).
2. **0 means the classic behaviour.** Existing projects carry 0 in slots stock has no row for.
3. **Send-FX rows are never lockable** (`flags_set = 8`) and borrow an FX row's display entry.
4. **Synth rows** use a free, saved k in their machine (FM Tone 34, 49; WaveTone 50-64; FM Drum
   55-64; Swarmer 33-64). By the study, stock code then gives them p-locks, LFO destinations, MIDI and
   save/load. This is from the study only: no synth row has been built or run yet, and our build
   script refuses one. The shared filter/amp block has no free saved slot.
5. **No randomiser.** The sound randomiser writes sound slot k for every row that has a randomiser,
   whatever its set. Give a new row none (param-grow clears it unless the row asks to keep it).
6. **MIDI**: no CC already in the set's map, none in 32-63, and none the CC parser takes itself (1, 2,
   6, 64, 66, 94, 98, 99, 110, 120, 121, 123; T6.1 section 2.6). NRPN 398 at most.
7. **An Overbridge id** outside stock's 0..387 (we use `0x400` + n). Untested with the plugin.
8. **FX k 70 and up are not saved** by stock code (section 10).

## 7. Evidence

The baseline is FTN1, the build just before, which also runs on my unit.

| where | check | result |
|---|---|---|
| build | elekloader's tests | 25 of 25 |
| build | brick checker, GT00 and GT01 | ACCEPT, 12 of 12 |
| emulator | cold boot of GT00, GT01 and GT1T | the same tasks and main screen as FTN1 |
| emulator | read/write watches from reset (GT1T) | no OS read of the table before the copy is done |
| emulator | maps built at boot | identical; new rows only where intended |
| emulator | 218 screens, the old table after boot | same as baseline (timing noise aside); old table never read |
| emulator | p-locks, LFO list, randomisers, copy/paste, MIDI out | as the baseline |
| emulator | 7,100 MIDI CC/NRPN messages | the same 991 parameter sets; every store inside the cache |
| emulator | the 12.9 MB working project image | the baseline's, byte for byte (but for turns the emulator dropped, each traced) |
| emulator | test build GT1T, two knobs on the flanger page | drawn, turned, CC/NRPN in, CC out |
| **unit** | GT00 | boots; +Drive save and load fine; cold start fine |
| **unit** | GT01 | "all good" |

The emulator cannot save to the +Drive, so the GT00 unit run was our first real save and load of a
grown build.

## 8. Limits and unknowns

- **New knobs on a page, MIDI into new rows, and the p-lock flags on new FX rows** have run only in
  the emulator.
- **Overbridge.** A row sends its id when its value changes. What the plugin does with an id it does
  not know (`0x400` and up) is untested. GT01's rows never change, so they never send.
- **MIDI CC out of a new FX row** should go out on the track's channel, not the FX channel. That is
  from reading the code (`ParameterSet::vslot25` sends only rows `0x69..0x83` on the FX channel), not
  from a test: in the emulator's MIDI setup the test row's CC went out on the same channel as the stock
  FX knobs, so the difference never showed. One site to extend later.
- **Arrays sized by a rounded row count**, for example one sized `0x150` or `0x200`, would not show in
  our scans, which look for multiples of `0x141`. A row in the top slot ran through screens, MIDI,
  locks and randomisers in the emulator without trouble, but such arrays are not ruled out.
- **A project saved on a grown build and loaded on stock 1.12** has not been tried on the unit. The
  study expects no change (T6.1 section 3.3).
- **Heap.** The cache takes 12,852 more bytes; free heap space was not measured.
- **The brick checker** must pass before anything is flashed, but for code changes a pass is not
  proof on its own (T6.5 section 6). That is why the emulator runs above were done.

## 9. How to use it

The mods are on our elekloader fork, branch
[`dn2-1.12`](https://github.com/naturarum/elekloader/tree/dn2-1.12):

| mod | what |
|---|---|
| `examples/param-grow-dn2` | moves the table, room for 384 rows, adds none (GT00) |
| `examples/param-rows-dn2` | the compressor's 17 hidden rows, `0x141-0x151`, FX k 70-86 (GT01) |
| `examples/param-test-dn2` | two test knobs. **Emulator only, never flash**: it breaks rule 1 on purpose |

To add rows, write a mod that requires `core` and `param-grow` and includes
`param-grow-dn2/param_rows.h`. Define an array of `struct param_row`, ended by id 0: the row id
(`0x141-0x17f`), the 60-byte descriptor, the stock row whose display entry it borrows, `flags_set`,
and optionally a page and an empty knob. Contribute it to the `param_rows` collection and claim each id
as `param-row:<id>`. `param-rows-dn2/rows.c` is a complete example.

**Nothing checks your rows for you.** `pg_boot` only skips a row whose id is outside `0x141-0x17f`;
it does not look at k, flags, CCs or Overbridge ids. Our `scripts/t65_build.py` (`check_rows`) checks
FX rows against section 6 and refuses any other kind. elekloader's test checks only `param-rows-dn2`'s
own rows. A row mod you build with elekloader is not checked at all, so check it against section 6 by
hand.

GT01 uses 17 of the 63 new ids. For more, regenerate param-grow with `scripts/t65_make_mod.py
--rows`. The ceiling with same-length edits is `0x1e0` (480 rows): one cache offset must fit a 16-bit
displacement.

## 10. What's next

- **The 3-band compressor's pages**, one per band. The OS has 37 fixed page records, so new pages need
  borrowed records or a page hook.
- **Saving per kit.** Stock saves FX k 25-69 only. A kit's FX record has 28 words (`+0x60..+0x97`)
  stock never writes or reads; three small hooks and a check word can keep k 70-97 there. A frame hook
  (proved in the emulator) carries them to the DSP.
- **Synth parameters** that can be p-locked.

## Credits

- **[dn2_firmware_explore](https://github.com/angellinares/dn2_firmware_explore)** by angellinares
  (AGPL-3.0-or-later, OS 1.11). They grew this table first: their lfo4 mod moves it, adds ten rows,
  diverts the display table's reader for them, checks in their emulator that the old table is never
  read again, and warns that the map builder's bound must not be raised while LFO4's slots pass 100
  ([`docs/lfo4-build-plan.md`](https://github.com/angellinares/dn2_firmware_explore/blob/main/docs/lfo4-build-plan.md),
  2026-09-21). Their 1.11 facts were our starting point: the row layout, the 56 address sites and the
  bound counts, the slot tables and last-write-wins, the lock id map and the dead ERR rows. Our notes
  did not draw on their LFO4 growth: we reached the reader divert, the boot watches and the k limit on
  our own, and they match theirs. We used their published findings, credited; none of their code.
  Their mods can be built in the browser: <https://angellinares.github.io/dn2_firmware_explore/>.
- An earlier version of our README said this was the first time anyone had grown the table. That was
  wrong; dnfw did it first.
- **[elekloader](https://github.com/irpina/elekloader)** by irpina: the mod format, the core and hook
  model our core-dn2 follows, and the collections and resources the rows use.
- **[digikit](https://github.com/m-dwyer/digikit)** by m-dwyer: the emulator every emulator check above
  ran in (on our fork).
- **[elektron-firmware-tool](https://github.com/mischa85/elektron-firmware-tool)** by mischa85: the
  `.syx` rebuild and seal that make GT00 and GT01.
- Made with Claude, Anthropic's AI model, in Claude Code: most of the analysis, code and these notes.
  I set the goals and ran everything on the unit.
