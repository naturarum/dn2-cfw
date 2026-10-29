# BA05: base 0.2 with the arpeggiator on MIDI tracks

2026-10-28 (one builder, no reviewer: no storage format or DSP change; the interrupt code is the study's, checked
byte by byte). The owner: the arp on MIDI tracks, "without cc plocks, but ideally with shuf, rand and TM"; after BA04
on the unit: "commit and push, then go BA05". BA05 = BA04 (on the unit 2026-10-28) with arp-midi-dn2 0.1, the design
of notes/T2.2-arp-midi-v2.md, option 2 (sections 4.1, 4.7), built from its corrected sketch
(`out/T2.2-v2/arp_midi_v2.s`). Marks: **[verified]** = run (emulator, harness, build check) or read in the bytes;
**[inferred]** = reasoned. Flashed by the owner 2026-10-29 (section 4).

| | |
|---|---|
| build | `scripts/ba05_build.py` (BA04's, with arp-midi 0.1); every check passes, 32 of 32 (`out/BA05/build-BA05.json`); DC02's DSP section 7 byte for byte |
| sources | fork `examples/arp-midi-dn2/ami.s` (0.1); generator `scripts/ba05_make_midi.py` (11 sites, the anchors the caves rely on, no branch into a split site, the two tables recomputed from the stock ones) |
| **`out/BA05/BA05.syx`** | sha256 **`1a345a94270797bf3e6e43f4b52bdf1b2303294b3f62e0b60714cae8a968eaf8`** (the first build, `4a09b744...`, had the first live note's bug, section 3) |
| brick checker | **ACCEPT, 12 PASS, 0 FAIL, 0 SKIP** for `1a345a94...` (`out/brickcheck/BA05-2026-10-28/report.md`, 2,844 s; DSP run 339,305 instructions per block as BA04, peak 0.693) |

## 1. What it does

A MIDI-machine track gets the ARP key, the ARP menu (stock closes it at once on a MIDI track) and the ARP LED. With
its arp on, the track plays through Elektron's own arpeggiator, as a synth track does: its trigs (the sequencer's
step and the pending step take the synth path, so the trig's notes enter the arp's held set for the trig's length)
and its live keys (the unit's keys and MIDI in on its channel go to the voice interrupt). Where the interrupt would
start a voice for that track, `ami_sink` builds a MIDI note event instead and appends it to the list the interrupt
posts to the MIDI task at the end of each pass: the arp's notes leave by the path and timing of a stock MIDI trig, on
the track's channel, the MIDI task's transposes applied. N.LEN becomes the first trig length not shorter (exact for
96 of 127 values, at most 1/32 step longer); INF lasts one arp period. A key's release goes to both engines, so
toggling the arp while a key is held leaves nothing hanging. The arp off, the track is stock. SHUF, RAND and TM
(arp-modes 0.3) play on MIDI tracks as on synth tracks [inferred: the same arp step].

Not sent while the arp is on: the trig's MIDI p-locks (CC, pitch bend, aftertouch, program) and its LFO trig, since
the MIDI builder isn't called (the owner's "without cc plocks").

## 2. Checks

- Static [verified]: `ba05_make_midi.py` (every site's stock bytes; the voice interrupt's frame slots fp-36, fp-44,
  fp-48 and fp-180, its type-3 append and list tail, the allocator's arguments; FUN_40027c18; the MIDI event
  allocator and the MIDI task's gates; no branch into the two split sites; `ami_len` and `ami_period` equal to the
  ones the stock arp-length, trig-length and speed tables give); the build's 32 checks; the elekloader lint. The core
  mod's MIDI hooks (0x400da0b2, 0x400d9230, 0x4012b490) don't overlap ours, and no BA04 mod listens to them.
- The arp's step on BA05's image: `ba04_arpcheck.py --name BA05`, all six checks pass (the engine unchanged).
- Emulator: section 3.

## 3. The emulator, and the one fix it found [verified]

`scripts/ba05-midi.script` on BA05's cold boot, with the audio clock and the DIN MIDI log
(`scripts/ba05-emu.sh run t63b_live scripts/ba05-midi.script --ssi0-hz --ips 150e6 --midi-log .../midi.txt`), read by
`scripts/ba05_midicheck.py`: track 1 made the MIDI machine on channel 1 by keys (as T2.4c's proof); the ARP menu opens
on it; MODE UP, SPEED 1/32, RANGE 2. The first build (`4a09b744...`, `out/BA05/emu-first/`):
- live, C3 held from MIDI in for 1.2 s: C3 C4 C3 C4 ... on channel 1, one every 1/32 at the project's 81 BPM (gaps
  0.99 to 1.01 of a 1/32), 14 notes, each 0.561 to 0.569 step long (N.LEN's default 0.53 step goes out as the trig
  length 0.5625), the last one cut by the key's release (a release sends 9n kk 00 for a sounding note of its number,
  stock behaviour), nothing after;
- the trig on step 1 through the arp: C4, then C5 1/32 later, each 0.5625 step, within the trig's 1/16;
- the arp off (FUNC + ARP): one C4, 1/16 long, the stock MIDI trig.
- **But the first live note was ended 1 ms after it began** (0.005 step). A live key's event comes in a bucket the
  interrupt plays at once (bucket[0] set), whose time field isn't the time; the sink took it as the note's start, so
  the MIDI task booked the note-off in the past. The type-3 path dates such events by the pass's end ([0xe]); the sink
  now does that for [0xc] too (a timed bucket keeps its time). The check now tests every note's length.

The fixed build (`1a345a94...`, `out/BA05/emu/ba05-midi/`, sheet `out/BA05/emu/ba05-midi-sheet.png`): **every check
passes** (`out/BA05/midicheck.json`): the first live note 0.569 step long, the rest as above; the arp's trig C4 C5; the
stock trig with the arp off; the screens show the ARP menu on the MIDI track (MID 1, CHAN 1) and "Arpeggiator OFF"
after FUNC + ARP. No note is left sounding after the key's release, the arp's trig or STOP.

TM on the MIDI track (`scripts/ba05-tm.script`, `out/BA05/emu/ba05-tm/`): MODE TM, LOOP CHANGE 0, SPEED 1/32, ARP
LENGTH 8, C3 held 2 s: 22 notes on channel 1, the loop 51 48 53 55 48 58 55 55 twice and its start again, 1/32
apart, all in the octave above C3 and all in C minor pentatonic (C Eb F G Bb: the emulator's project has that scale
in KEYBOARD SETUP; eight chromatic notes in five classes by chance: about 1 in 1,100).

## 4. On the unit (2026-10-29)

The owner flashed BA05: "ba05 works flawlessly! omg". The arp on MIDI tracks (arp-midi-dn2 0.1) is base 0.2's, as
built; BA05 is the final base for 0.2.0.

## Open

- Not reported in detail from the unit, worth a look if a bug report comes: a synth track's notes while a MIDI
  track's arp plays (the study's remote risks for live keys on a MIDI track: notes/T2.2-arp-midi-v2.md, section 6,
  item 5) and sixteen MIDI tracks arpeggiating at top speed.
