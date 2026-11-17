# UF01: can Digiwerk share a build with upstream elekloader's DN2 machines?

2026-11-16. A paper check: manifests, docs and our build records compared. Nothing was built or run, so every
"fits" below is [inferred] until a combined build boots in the emulator.

Compared:
- upstream elekloader `origin/main` at 2026-11-14: `core` 0.4 (mods/core-dn2), `machines-dn2` 0.5, `dspbusy-dn2` 0.1,
  docs/MACHINES-DN2*.md, docs/SHARC-DN2.md;
- ours: the fork's branch `dn2-1.12` (`core` 0.2 and the 25 `examples/*-dn2` mods), `tools/dspsplice/build.py`'s
  memory plan, `out/EEEM/EEEM-dsp/build.json`.

The script: every `sites` entry of both sides as a byte range, intersected (157 upstream ranges, 476 of ours).

## 1. Result

Not a drop-in, but close. Three things clash; everything else is disjoint.

| # | Clash | Who is hit | What it takes |
|---|---|---|---|
| 1 | The core: both patch the same 7 base sites | every build | one core. Ours (0.2) and theirs (0.4) share the 7 sites; our 4 extra sites and their extras don't overlap |
| 2 | DSP memory: both start at L1 `0x2dd600` | every build with our DSP code | our code leaves `0x2dd600-0x2e0800`, or theirs moves |
| 3 | The parameter table: both move it and add rows from row 321 | only builds with our compressor (and trig-slide) | one table mover; our rows renumbered above theirs |

A build like EEEM (Elektron's compressor, chorus and delay, MRB, the base mods, usb-multi) is hit by 1 and 2 only.

## 2. Main OS (ColdFire)

Exact overlaps, by pair of mods:

| upstream | ours | sites | what |
|---|---|---|---|
| core 0.4 | core 0.2 | 7 | the base sites: boot `0x40000538`, tick `0x4002e464`, draw `0x4002e4ca`, key `0x4002f20c`, enc `0x4002f256`, personalize, settings |
| machines-dn2 0.5 | param-grow 0.1 | 112 | the parameter table's references and row bounds; also the boot call `0x40000568` (`dn2_start` / `pg_boot`) |
| core 0.4 | comp-link 0.3 | 1 | `0x40025e9e`, the DSP frame sender's call: their `core_dsp_frame`, our compressor's frame tail |

No overlap with: os-version, lfo-dejavu, arp-modes, arp-midi, boot-mark, morph-names, flanger-names, flanger-sync,
spring-names, usb-multi. Near misses (within 0x40 bytes, not overlapping): machines-dn2 with our core (5), fx-page2
(4), trig-slide (2).

The rows: upstream gives every synth page p, knob k the row `321 + 8p + k` (rows 321-360), the SDK knobs 361-375 and
a filter's G 376. Ours: param-rows 0.4 claims `0x141-0x151` (321-337, the compressor's 17 hidden rows), trig-slide
`0x155`, param-test `0x17e-0x17f`. Same numbers, so ours must move above 376, and only one mod may move the table.

Sound storage: machines-dn2 keeps SDK knob values in sound slots 25-39; trig-slide uses sound k 100. No clash seen.

## 3. DSP (SHARC)

Core byte addresses (upstream's loader address minus `0x28000000` for L1).

| range | upstream | ours |
|---|---|---|
| `0x2dd600-0x2dda00` | core's bridge and state | the hook stubs, the flanger, MRB (`t38_code`, proven `0x2dd600-0x2e1c36`) |
| `0x2dda00-0x2dde00` | the bridge's pitch table | same block |
| `0x2de000-0x2df000` | the synths' stack | same block |
| `0x2df000-0x2e0000` | the filters' stack | same block |
| `0x2e0000-0x2e0800` | core's DSP peek, busy record `0x2e0630` | same block |
| `0x2e2900-0x2e4f46` | free | code4: the compressor glue, tail reader; MRB's second part |
| `0x2e6000-0x2e7000` | free | our data and the register save area |
| `0x308400-0x30ff76` | free | the compressor |
| `0x310000-0x315f8c` | free | the tape delay's home |
| L2 `0x20090000-0x200b0000` | the machine pool: dispatcher and every kernel | nothing. `build.py` forbids `0x200918c0-0x200c2000` for data (dnfw: live pointers); SC01's stops had code run from L2 as their lead |

Upstream chose `0x2dd600` from our own memory map (their doc cites notes/T3.2), which is why both sit there.

Stock DSP sites patched, no overlap:
- upstream: `0x1c244d` (the bridge), `0x1c9459` (the voice pipeline), `0x1c9e5f` (the peek), `0xb88abb` (dspbusy);
- ours: the eight FX calls and inits (`0x1c1be1 0x1c1695 0x1c1c58 0x1c166c 0x1c1d11 0x1c1643 0x1c215c 0x1c16bb`) and
  the fault-tone parks (`0x1c07c7 0x1c1431`).

Section 7 has two writers: elekloader's SHARC linker and our `dspsplice`. One has to take the other's output as its
base; today `dspsplice` insists on the stock stream's sha256.

What moving costs under the placement rule (our code only where ours ran on the unit):
- **MRB alone fits without a new place**: its 15,014 B of `t38_code` fit in `0x310000-0x315f8c` (24,460 B), where
  the tape delay ran on the unit; its code4 part stays. So an EEEM-like build can clear upstream's ranges. [inferred]
- the flanger lives at `0x2dd600` and the tape delay in `0x310000`; a build with both and upstream's machines needs a
  new home for one of them, which means a probe build on the unit.

## 4. Open points

1. The L2 pool. Our notes distrust L2 for our own code (SC01) and dnfw flagged `0x200918c0-0x200c2000`. Upstream's
   doc says the stock boot's last L2 byte is `0x2008823c`; their busy meter ran on a unit, and whether a machine
   kernel did is not checked here. Read their evidence before any machine of ours goes there.
2. DSP load. Not looked at. Each machine kernel adds per-voice cost on top of our effects.
3. Our core's 4 extra sites (`0x400c25d8 0x400d9230 0x400da0b2 0x4012b490`): which of our mods need them, and whether
   they become a small mod on upstream's core or a pull request.
4. comp-link on upstream's core: the frame tail as a subscriber of their frame hook, not a second patch of
   `0x40025e9e`.

## 5. The smallest combined build

Upstream core 0.4 + machines-dn2 0.5 + our base mods (os-version, lfo-dejavu, arp-modes, arp-midi, boot-mark,
usb-multi) + MRB with morph-names, MRB's first code block in `0x310000`. No compressor, flanger or tape in it.
Emulator work only until it passes the brick check; then one unit test.

## 6. Tools on the new Mac (2026-11-16)

Homebrew `rust` 1.100.0 and `m68k-elf-gcc` 16.2.0 installed. selache built (`vendor/selache/target/release`).
`build.py EEEM --outdir` gives section 7 `61c0e921...` and `EEEM-dsp.syx` `f47a7dc9...`, bit for bit the build of
11-10. The stored ColdFire objects were built by GCC 16.2.0 too. sharcvm self-test 8/8, fx tests 861/861. Ghidra and
openjdk@21 were already here (the command is `ghidraRun`).
