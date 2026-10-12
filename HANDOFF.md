# Digitone II OS 1.12 firmware notes

> **Start with `ROADMAP.md`** (goals, tasks, who is doing what), then `FIRMWARE.md` (the firmware archive) and
> `VENDOR.md` (the upstream clones). This file is a chronological log: **later sections supersede earlier ones**.
> The "Next actions" under START HERE are out of date; the newest entries are at the bottom. Reports for each task
> are in `notes/`, bulky data in `out/` (ignored by git), tools in `tools/` and `scripts/`.
> **Moved 2026-10-05:** the project folder was renamed from `El-Fw` to `Elektron-CFW` (older notes
> say El-Fw). pi and opencode no longer work on it.

Working from `elektron-firmware/Digitone_II_OS1_12.syx` (moved there from the project root on 2026-10-05; older notes still say `Digitone_II_OS1_12.syx`), unpacked with `ele3_unpack.py` into `ele3_out/`.
Goal: find bugs/issues that hinder live performance, for personal use on this self-owned
device. Each finding below is marked **verified** (reproduced/computed and checked against
the real file) or **inferred** (reasoned from code shape, not independently confirmed).

## Ultimate project goal

To create a platform to build new instruments, effects and machines for the user’s Digitone II. The platform could take any form, but it should likely involve AI-assisted coding. Some long term feature ideas, any can be picked as a starting point to verify that the project is viable : 

**SCOPE REVISED 2026-10-08 (see "Scope revision" section below).** Original long-term
feature ideas, any can be picked as a starting point to verify that the project is viable :
- A CPU monitor
- New reverb machines (swappable - only 1 loaded at a time)
- New delay machines (swappable, same)
- New chorus/3rd fx slot machines  (swappable, same) (think flanger, spectral fx, etc) 
## Project state / handoff (as of 2026-10-05; scope revised 2026-10-08)

A resuming session should read this section first.

**Tooling already set up** (don't redo from scratch):
- `ele3_unpack.py` → `ele3_out/` (image.bin, per-section `.bin`/`.unpacked.bin`, manifest.json).
  Fully reproducible; every per-section byte-sum and NRV2B decompression verifies.
- Homebrew: `ghidra` 12.1.4 (`/opt/homebrew/opt/ghidra/libexec`), `m68k-elf-binutils`
  (ColdFire via `m68k-elf-objdump -m m68k:cfv4e`), `openjdk@21`.
- PyGhidra driver: `scripts/gh.py` (preserved) — a generic helper: `open_project()`,
  `import_raw(base,…)`, `seed_functions()`, `analyze()`, `decompile_all()`. It hardcodes the
  persistent project dir and the Homebrew Ghidra/JDK paths, so it runs as-is once a venv
  exists. The venv itself was session-temp and is gone — recreate: `python3 -m venv venv &&
  venv/bin/pip install jpype1 pyghidra`, then `venv/bin/python scripts/gh.py`-style scripts.
- The Ghidra *project* persists: `ghidra/` → project `dn2`,
  programs `upgrader` (@0x80000400) and `main_os` (@0x40000400), both imported + analyzed.
- `analysis/` holds the Task 2 outputs (`class_hierarchy.md`, `rtti_classes.json`).

**Done:** Task 1 (framing + both checksums verified against the real file, gating control
flow mapped), Task 2 (full RTTI hierarchy rebuilt + demangled, 858 classes, 100% demangle),
Task 3 *identification* (SHARC section = ADSP-2156x / SHARC+ running FreeRTOS; chip-role
question answered). Details in the per-task sections below.

**Open — legitimate next steps:**
- Task 3 disassembly proper: still need an **ADSP-2156x (SHARC+)** SLEIGH/disassembler — the
  only public Ghidra SHARC module is ADSP-214xx (wrong core). Not yet searched for one.
- Section **type 8** (i.MX RT-style, 159,948 B unpacked) was never examined past the
  unpacker's heuristic ID. Untouched.
- Type-5 build-timestamp encoding is still just string inference.

## Format (verified)

Confirmed by running `ele3_unpack.py` end to end — all per-section byte-sums and NRV2B
decompressions check out (`bytesum_ok: true`, `consumed_all: true` for every packed section).

- SysEx: Elektron ID (`00 20 3C`), device `0x15`. 18,674 data blocks (`7E`), block numbers
  242–18915 contiguous, framed by `7F 01`/`7F 02` begin/end markers.
- Image `image.bin`: `ELE3` magic, id `40062`, version `1.12`, built `260930 10:59:50`
  (type-5 section, plain text, not yet confirmed as a real timestamp encoding vs. just
  `YYMMDD HH:MM:SS` text — **inferred** format, string value itself is verified).
- Sections:
  | type | load addr | packed len | unpacked len | content |
  |---|---|---|---|---|
  | 5 | — | 15 | — | build timestamp string |
  | 2 | 0x02010000 | 16,176 | 30,302 | bootstrap upgrader (68k/ColdFire) |
  | 3 | 0x40000400 | 1,131,348 | 3,196,288 | main OS (68k/ColdFire, RTTI intact) |
  | 4 | 0x80000400 | 32,776 (raw) | — | uncompressed loader, holds NRV2B decompressor |
  | 7 | — | 602,076 | 836,956 | Analog Devices SHARC boot stream (**inferred** ID) |
  | 8 | — | 103,416 | 159,948 | i.MX RT-style image, IVT at offset 0x1000 (**inferred** ID) |
  | tail | — | 40 bytes after last section | — | `00×8 + 32-byte blob` (see below) |

## Task 1 — CRC CHECK / VERSION CHECK / block checksum (verified formulas; see note on scope below)

All reverse-engineered from `ele3_out/01_type2.unpacked.bin`, the bootstrap upgrader, loaded
at `0x80000400` (not `0x02010000` as the section header load-address field says — the string
table and absolute JSR targets only line up self-consistently at `0x80000400`; **verified**
by cross-matching 10 known UI strings against pointer references, all resolving to one
consistent base). Disassembled with `m68k-elf-objdump -m m68k:cfv4e`, decompiled with Ghidra
12.1.4 headless (PyGhidra) using `68000:BE:32:Coldfire`.

### Per-SysEx-block extra byte — verified
```
K = 0x10                      # from the "7F 01 10 ..." BEGIN marker, byte 3
extra = (K + sum((block[i] ^ (K+i)) for i in range(0x77))) & 0x7F
```
Matches the trailing byte of all 18,674 data blocks exactly. Found by decompiling
`FUN_800036f6` in the upgrader (the SysEx receive handler) — not purely inferred.

### Whole-image checksum (header field, `0x07a3d265` for this build) — verified
```
checksum = sum((i+1) ^ word[i] for i, word in enumerate(image_as_u32be)) & 0xFFFFFFFF
```
Computed directly over `image.bin` (471,504 big-endian 32-bit words, image length divides
evenly by 4) and matches the stored header value exactly. Source: `FUN_80003c9c` in the
upgrader, which recomputes this over the received image and compares to the stored value
before proceeding.

### Control flow / gating — verified from decompilation
`FUN_80003c9c`: recomputes the rolling checksum above; if it doesn't match the stored value,
falls into a loop that calls a 32-byte-tail check (`FUN_80005e3a`) and, on failure, calls
`FUN_80003bfc` (prints `UPGRADE ABORTED` / `PLEASE REBOOT`, then an unconditional infinite
loop — no auto-recovery, needs a power cycle / reflash attempt). Only when the tail check
succeeds does it fall through to `UPGRADING... / DO NOT TURN OFF` and commit the image to
flash. So **both** the rolling checksum and the 32-byte tail must validate before a received
image is ever written.

### 32-byte tail — inferred (algorithm family only, intentionally not fully extracted)
The tail-check routine (`FUN_80005e3a` → `FUN_80005c5c` → `FUN_80005bfc`/`FUN_8000595e`) has
the structural shape of an HMAC-style construction over a SHA-256-shaped compression function
(inner/outer `0x36`/`0x5c` pad bytes, standard SHA-2 round constants and working-variable
rotations), with key material sourced from constants embedded in this same upgrader binary
rather than anything device-unique or server-held.


## Task 2 — main OS RTTI / class hierarchy (verified)

`02_type3.unpacked.bin`, loads at `0x40000400`, ColdFire, 3,196,288 bytes. Imported and
auto-analyzed in headless Ghidra 12.1.4 (10,390 functions found) via PyGhidra — used mainly
to confirm there's no live code calling `dynamic_cast`/`typeid` anywhere (every reference to
the RTTI support structures is a data reference, i.e. this firmware has RTTI metadata from
compiling with it enabled, but doesn't exercise it at runtime itself).

The class hierarchy itself was reconstructed by direct byte-level Itanium-ABI RTTI parsing
(script in `analysis/`, not checked in — results are in `analysis/rtti_classes.json` and
`analysis/class_hierarchy.md`). Method:
1. Found the 3 special support-class mangled names (`N10__cxxabiv117__class_type_infoE` etc.)
   exactly once each — gives the 3 reference typeinfo struct addresses.
2. **Note on the vtable-identity approach (the textbook technique) — didn't work here and I
   stopped relying on it**: normally you classify a typeinfo struct's shape (0/1/N bases) by
   which of the 3 support vtables its first word points to. Here, `class_type_info`'s and
   `si_class_type_info`'s own typeinfo structs resolve to the *same* vtable address — they
   appear to be identically folded by the linker (plausible on a `-fno-exceptions` embedded
   build, where their virtual methods are likely trivial/unreachable stubs and thus byte-
   identical). That removes the one signal the standard technique depends on, and there's no
   live `dynamic_cast` code to use as a tiebreaker (see above). I didn't try to force a
   classification out of this.
3. Used structural self-consistency instead, independent of vtable-constant identity: a
   candidate struct is accepted as having a base only if the pointer at its own `+8` offset
   itself resolves to *another* well-formed candidate struct (valid name pointer at its `+4`).
   vmi-shaped structs (flags/base-count/base-list at a fixed layout) were validated the same
   way, keyed off `vmi_class_type_info`'s own vtable value, which *is* distinct.
4. Every resolved name was run through `c++filt`: **100% (858/858) demangled as valid Itanium
   C++ names** — mangled-name grammar is specific enough that this is a strong correctness
   check on the whole method, not just on individual entries. All 433 app-level base pointers
   resolved with no dangling references.

Results: 858 classes with RTTI (730 single-base, 128 multi/virtual-base), 433 of them
application-level (the rest are libstdc++/`__gnu_cxx` internals, mostly `shared_ptr`
machinery). Clear, coherent architecture, e.g.:
- `View` / `Observer` / `LedHandler` / `Timer` / `GlobalInstances` as the common UI mixins;
  dozens of `*MenuView`/`*Window` classes building on them.
- `Value<T>` / `ValueWithMirror<T,Storage>` / `ArrayValueWithMirror<T,Storage,N>` — a
  parameter data-binding layer over versioned storage structs (`Digitone::patternStorage_v1_t`
  … `v11`, `Digisharc::kitStorage_v1_t`…`v4`, `BOB::bobConfigStorage_v0_t`, etc. — i.e. the OS's
  on-disk pattern/kit formats have been revised at least 11 times).
- `ClipboardData` base under ~15 `*Copy` classes — copy/paste for patterns, kits, FX setups,
  chord slots, etc.
- `StaticSingleton<T>` / `DynamicSingleton<T>` CRTP singletons (`Brain`, `LedManager`,
  `MidiAsyncWorker`, `Clipboard`, `MainScreenView`, …).
- `StreamReader`/`StreamWriter` with `Lz4StreamCompressor`/`Lz4StreamDecompressor` — so LZ4 is
  used for some on-device data path, separate from the NRV2B used for OS update compression.
- An `elektron::MidiRpc*Request`/`*Response` namespace (126 classes) — a full file-system RPC
  protocol over MIDI SysEx (enumerate/open/read/write/rename/move/swap/delete files and dirs,
  device-UID query, tag setting, partial reads/writes). This is almost certainly the protocol
  a companion desktop app uses to browse/manage patterns, kits and samples over USB-MIDI.

Full list with resolved base classes: `analysis/class_hierarchy.md`. Raw structured data
(addresses, mangled + demangled names, resolved bases): `analysis/rtti_classes.json`.

## Task 3 — SHARC boot stream

### Chip identification — now verified (was inferred)
`04_type7.unpacked.bin` (836,956 bytes) contains literal embedded path strings, not just the
earlier byte-level heuristic:
- `..\..\..\..\lib\freertos-sharc\portable\CCES\SHARC_215xx\port.c` (+ `event_groups.c`,
  `queue.c`, `stream_buffer.c`, `tasks.c`, `timers.c`, `heap_4.c` — the rest of a FreeRTOS port)
- `C:\temp\win32\kit\SHARC\lib\src\services\Source\{gpio\adi_gpio.c, pcg\adi_pcg_v1.c,
  spu\adi_spu_v2.c}` — Analog Devices' own CCES services library asserts.

This confirms it's a genuine ADI SHARC image, running FreeRTOS (a scheduled, task-based RTOS,
not just a flat DSP algorithm blob) — **and** pins down the exact sub-family: **ADSP-2156x
("SHARC+"), not the older ADSP-214xx family**. That resolves why the one public Ghidra SHARC
extension I found/tried (see below) couldn't parse this boot stream: it targets ADSP-214xx
specifically, a different, incompatible core generation, not a bug in that tool.

Also now verified, answering the original "which chip runs the synth engines" open question:
the main OS (ColdFire) contains `../../../firmware/digisharc/cf/intro/intro_dither.cpp` — the
"Digisharc" codebase (the same namespace seen throughout the RTTI class hierarchy: the
`Digisharc::` storage/parameter types from task 2) has a ColdFire (`cf`) build target, but only
for what looks like a small "intro" dither utility, alongside a separate `lib/shared/sm/fade.c`
shared DSP-utility path. Given the SHARC+ chip is a dedicated FreeRTOS coprocessor while this
is the only piece of "Digisharc" found compiled for ColdFire, the working read is: **SHARC+
is the dedicated audio/synth-engine DSP core; ColdFire is UI/sequencer/control, with a few
small shared DSP utility routines (dither, fade) also built for it** — not independently
confirmed which exact routines run where beyond that, so treat the division of labor as
inferred, the chip identity and codebase name as verified.

### Disassembler search
Tried `roblg/ghidra-sharc` (github.com/roblg/ghidra-sharc, v0.0.6) — a Ghidra processor
extension with SLEIGH for ADSP-214xx SHARC (ISA+VISA) plus an `.ldr` boot-stream loader.
Installed cleanly into Ghidra 12.1.4. Result:
- Its boot-stream loader correctly rejects our file (`findSupportedLoadSpecs` returns no
  match) — confirmed by reimplementing its exact tag/count/address block-walk logic in Python
  and brute-forcing every offset/endianness: nothing reaches end-of-buffer, the handful of
  partial "matches" are just zero-padding runs coincidentally satisfying the loosest tags. So
  this isn't the ADSP-214xx `.ldr` block format, consistent with the 2156x finding above.
  MULTI_PROC streams aren't supported, and it's not a stream the loader confirmed as this at
  all. (The field layout is sane and worth keeping in mind in general: 3×little-endian-u32
  header `[tag][count][address]`, tag ≤10 or `0x2000`=zlib-compressed — see
  `SharcLdrLoader.java` in the extension's source if revisiting this format question for a
  214xx-family target.)
- Raw-binary import forcing its `SHARC:BE:32:214xx` language hits what looks like an
  extension/environment bug unrelated to our data (fails identically, same internal
  `EOFException`, for every file ≳16KB regardless of content — even disjoint prefixes of our
  real file and synthetic padding fail the same way at the same spot) — not pursued further
  since it's the wrong sub-family for this chip anyway.

**Next step, if/when resumed**: need a disassembler/SLEIGH spec for **ADSP-2156x (SHARC+)**
specifically, not 214xx — haven't yet looked for one. Mapping the boot stream into memory also
needs the SHARC+ boot-stream format (likely similar tag/count/address shape per the VisualDSP++
manual family, but not assumed without checking — 2156x is a newer core and CCES-era tooling,
so the on-wire format may differ from the 214xx one above).

---

# Session log — 2026-10-08: viability assessment, tooling breakthrough, scope revision

## Viability verdict

Original goal ("platform to build new instruments/effects/machines") assessed as a
multi-year moonshot. **Revised goal is viable** — see Scope revision.

## Breakthrough: SHARC+ toolchain now exists (NEW — closes Task 3's stated blocker)

`js216/selache` — https://github.com/js216/selache — open-source SHARC+ toolchain, targets
**ADSP-21569** (= the Digitone II's ADSP-2156x family). GPLv3 tools, MIT runtime lib.
- `selinstr` — SHARC+ instruction **encoder/disassembler + VISA decompression**
- `selload` — generates 2156x **boot stream (LDR)** from ELF (=> the LDR format we need is
  implemented in open source; read its source to write a parser)
- `selcc` / `selas` / `seld` — working **C99 compiler, assembler, linker** for the target
- `seldump`, `selmem`, `selpatch`, `selsyms` — inspection/patching utilities

This retires the handoff's #1 open blocker ("need an ADSP-2156x disassembler; none found").
Note: VISA compression is very likely why the roblg/ghidra-sharc ADSP-214xx SLEIGH failed
to parse our stream. There is still **no Ghidra SLEIGH/decompiler for SHARC+** — selinstr
gives disassembly only, so SHARC RE is assembly-level.

## Scope revision (user-directed)

Goal narrowed from "invent new instruments" to:
1. **Swap delay / reverb / chorus** implementations (SHARC+ DSP side)
2. **MIDI machine (track slot) mods** (ColdFire side):
   - send the DN2 arpeggiator out over MIDI
   - >4 note polyphony

### Why this reframing is structurally better (verified by string analysis of `02_type3.unpacked.bin`)

The features split across TWO independent domains, and only one is hard:

| Feature | Domain | Difficulty |
|---|---|---|
| MIDI-track polyphony >4 | **ColdFire** (`SMVoiceStates`, `patternNotePool_t`) | Easy — pool/voice-sizing constant |
| Arp → MIDI out | **ColdFire** (`ArpSetupMenuView`, `ArpPatternCopy`, "Arpeggiator ON/OFF") | Moderate — routing/gate change |
| Delay/reverb/chorus swap | **SHARC+ DSP** | Hard — separate RE domain |

**Key finding: the arpeggiator is ColdFire code, not SHARC.** Both MIDI-machine asks are
therefore pure ColdFire work, in a binary we already decompile cleanly. The SHARC domain is
needed ONLY for the three FX swaps, and is fully independent of the MIDI work.
=> A large part of the revised goal needs no SHARC RE at all.

### Verified strings/classes supporting this (from `02_type3.unpacked.bin`)
- `13SMVoiceStates`, `StaticSingletonI13SMVoiceStatesE`, `8NotePool`, `Digisharc::patternNotePool_t`
  / `patternNotePoolStorage_v0_t` — the voice/note pool; the >4 limit is very likely a pool-size
  or voice-array dimension here. **NOT YET located — next step.**
- `16ArpSetupMenuView`, `14ArpPatternCopy`, `ARP`, `Arpeggiator ON/OFF`, `CLEAR/COPY/PASTE ARP`
  — arp logic lives on ColdFire.
- `MidiEvent` / `ViewController::handleMidiEvent`, `MidiAsyncWorker`, `MidiRpcDispatcher` — MIDI I/O.
- `N/A FOR MIDI TRACKS`, `NO MIDI PRESETS IN POOL` — MIDI tracks are already a special-cased path;
  the arp may be **gated off for MIDI tracks by an explicit check** (to be confirmed in Ghidra).

## Revised milestone order (each ends in a working, flashed device)

1. **Resign/flash pipeline** (prereq) + **verify recovery path** from upgrader commit logic.
2. **MIDI polyphony bump** — one-constant ColdFire patch; proves the whole edit→flash loop
   with minimal blast radius. *Start here.*
3. **Arp → MIDI out** — ColdFire only; trace the MIDI-track gate; likely small-to-medium patch.
4. **FX swap: chorus** (simplest DSP) — first SHARC excursion: compile with `selcc`, splice,
   verify audio.
5. **FX swap: delay, reverb** — repeat once the splice pattern is proven.

Steps 1–3 are entirely ColdFire (the well-mapped half). Steps 4–5 are the real SHARC project,
segregated so failure can't endanger the MIDI work.

## SHARC image recon (this session, `04_type7.unpacked.bin`)

- Assert strings: **only 3**, all from ADI's service library (`adi_gpio.c:226`,
  `adi_pcg.c:178`, `adi_spu.c:147`). Format: `ASSERT [expr] fails at "file":line`.
  => **No function-name symbol map recoverable from asserts.** (Checked; it's absent.)
- FreeRTOS task-name string: **`"Audio Task"`** present => the audio thread is findable by
  xref to this string (name passed to `xTaskCreate`). This is the primary entry point into the
  audio path.
- No `digisharc`/`engine`/`voice` strings in the SHARC image (unlike the ColdFire side) =>
  the DSP code is compiled without its own assert/name footprint; expect stripped, asm-only RE.

## Concrete plan to RE the SHARC+ audio path (answer to "what's the plan for the 800KB")

Goal is NOT full comprehension — it's locating 3 effect entry points + their parameter
structs, then splicing replacements. Stages:

1. **Tooling + container** — build selache; read `selload` source for the 2156x LDR grammar;
   write a Python parser to turn `04_type7.unpacked.bin` into a memory image + segment map.
   (The 214xx loader's `[tag][count][addr]` logic does NOT match — already brute-forced.)
2. **Map to memory, find entry** — VISA-decompress (selinstr), disassemble at load addresses;
   locate reset/boot entry (boot kernel or first task).
3. **Thread map via FreeRTOS** — xref the `"Audio Task"` string -> `xTaskCreate` call ->
   **audio task function pointer** = entry into the audio path. Neighbors give the rest.
4. **Peripheral + callback map** — ADI asserts name SPU (DMA) / PCG (clock) / GPIO. Find the
   SPU service/ISR callback that owns buffer transfer => the render callback that drives the
   graph.
5. **Find the 3 effects** — follow the render callback into the mix/FX chain; identify
   delay/reverb/chorus by structure (FIR/comb tables, delay-line state in .rodata/.bss) and
   by param-struct layout. **Leverage:** the ColdFire side documents the FX parameter IDs
   (readable in Ghidra today) => use it as the spec for the SHARC-side struct layout.
6. **Splice** — write replacement in C, `selcc`+`seld` => .dxe, inspect w/ `seldump`; place by
   overwrite (must fit) or a jump stub; patch call site(s) + init fn + param scaling.
7. **Validate incrementally** — first a no-op/passthrough swap (audio still works), then real.

## Notes / caveats
- Custom FX DSP via selcc is allowed (GPLv3 tools, MIT runtime => distributable binaries OK).
- Verify the flash recovery path before first flash (SEPARATE from any legal question — this is
  pure engineering risk management).

---

# Session log — 2026-10-08 (2nd entry): **octabam found — viability is settled**

## `sambanks/octabam` — community firmware remixer for the Elektron Octatrack

https://github.com/sambanks/octabam — MIT. This is the precedent that answers "is this
viable": **yes, proven, on the same vendor's hardware.** ~50 modules, most verified on
hardware by multiple authors. Read its docs; they are a complete RE of a sibling Elektron
device and the methodology transfers almost wholesale.

### Why it transfers (same vendor, same CPU family)

| | Octatrack (octabam) | Digitone II (this project) |
|---|---|---|
| Main CPU | ColdFire **MCF5445AVR266**, 68k big-endian, 266 MHz | ColdFire, 68k big-endian, OS loaded at **`0x40000400`** — same map convention |
| DSP | 2x Freescale **DSP5636x** (DSP56721) | 1x ADI **SHARC+ ADSP-21569** (FreeRTOS) |
| Storage/UI | ColdFire + microkernel, producer/consumer buffers | same Elektron patterns |
| OS signature | **none** ("No cryptographic signature in any layer") | **32-byte HMAC-style tail** — the one gap octabam didn't have |

DN2 CPU model string not found in the binary (no `MCF*` strings) — **unverified whether
DN2 is also MCF5445x. High value to confirm**: if it is, octabam's ColdFire addresses,
hook points and even its m68k patch source apply nearly directly.

### What octabam already built (i.e. the effects we wanted, done)

- **DELAY SERVER** — CLEAN / pitched granular GRAIN / REVERSE + tape wow
- **REVERB SERVER** — 8-line FDN, ROOM/PLATE/BIG, shimmer, gate, mid/side width
- CHARACTER, SPECTRUM, MODULATION, TAPE ECHO, EUCLID, SIDECHAIN_COMPRESSOR,
  **WAVE (4-voice wavetable synth)**, TRANSIENT, TESTGEN
- **CF METER / WAVE LOAD** — a *frame-interrupt / CPU-load meter*: frame-interrupt
  duration, idle time, per-frame voice-engine cost in us. => our "CPU monitor" idea,
  already solved in shape.
- MIDI: SCENES, CC MAP, CC FEEDBACK, USB MIDI, USB AUDIO IN/OUT (4-24ch).

Their DSP is written in **DSP56300 asm**, placed as "modules" into payload slots by a
hand-written assembler/linker. For DN2 we get a **better** toolchain: `selcc`/`seld`
(C99 -> SHARC+). The *algorithms* (FDN reverb, granular delay) port; the *asm* does not
(DSP5636x != SHARC+).

### Their DSP RE — the template for our SHARC work

`docs/firmware/DSP.md` fully reverse-engineers the Octatrack DSP payload: 24-bit LE words,
records of `{space P/X/Y, load address, count}` (98 modules in payload A, 91 in B),
giving a **load map with per-module address + size**. Code lands in unused payload
slots; a ledger refuses two modules claiming one address/hook/id/buffer.

=> This is exactly our SHARC Stage 4-6, already done for a sibling DSP. Our SHARC boot
stream (836KB, FreeRTOS, ADSP-2156x) is harder (boot-stream container + RTOS, not a flat
payload), but the *method* is proven and `selload` gives us the container grammar.

### Verification without hardware — adopt this

- **Unicorn** emulation of the ColdFire to run test gates (no unit needed)
- offline **DSP render host** comparing output against a float reference, bit-identical
  gates; "predict the arithmetic, assert to the LSB"
- a "ColdFire port" that runs the OS image against emulated cores and lands every byte
- every module carries a README with Knobs / Measured / On the unit / Open / Gates

### Module shape (directly matches our ColdFire MIDI work)

```
modules/<name>/manifest.py   declaration (knobs, ids, ModeViews)
            <engine>.asm    DSP56300 source, if it has any
            <patch>.s       m68k source, if it patches the ColdFire
```
A **ColdFire module** "changes what the firmware does (parts, kits, menus, MIDI, bug
fixes) and touches no audio" — that is precisely the shape of our arp->MIDI and
MIDI-polyphony work. They wire hooks "by symbol" and refuse collisions by name.

### Revised conclusion

ColdFire half: **routine** (octabam is the working template; MIDI features are m68k
patches). SHARC half: **real but now templated work** — no longer open-ended RE.
The only genuine gap vs octabam is the DN2 signature (resign pipeline). See milestone
list above; ordering is unchanged, confidence is much higher.

---

# START HERE — cold-start orientation (written for the next session/agent)

Read top-to-bottom: `## Project state / handoff` (line 18) -> Task 1/2/3 sections ->
the two 2026-10-08 session logs (lines 223, 332). This block is the map of all of it.

## The goal, concretely

Build flashable custom firmware for **your own** Digitone II. Current scope (2026-10-08):
1. **MIDI machine (track slot) mods** — send the DN2 arpeggiator out over MIDI; raise
   >4 note polyphony. **ColdFire side.**
2. **Swap delay / reverb / chorus implementations** with your own DSP. **SHARC+ side.**
Feasibility is settled: see the octabam log. ColdFire half is routine; SHARC half is real
but templated work.

## Files / layout

- `HANDOFF.md` — this file (note: macOS case-insensitive FS; `handoff.md` == `HANDOFF.md`).
- `ele3_unpack.py` -> `ele3_out/` — the unpacked firmware image. **Do not redo; verified.**
  - `00_type5` build-ts · `01_type2` upgrader · `02_type3` MAIN OS (ColdFire) ·
    `03_type4` loader/NRV2B · `04_type7` SHARC+ · `05_type8` i.MX RT · `image.bin`
- `analysis/` — `class_hierarchy.md`, `rtti_classes.json` (Task 2 output). **Do not redo.**
- `ghidra/` — persistent Ghidra project `dn2`: programs `upgrader` (@0x80000400) and
  `main_os` (@0x40000400). **Already imported + analyzed; just reopen.**
- `scripts/gh.py` — PyGhidra helper (open_project/import_raw/seed_functions/analyze/decompile_all).
- `elektron-firmware/Digitone_II_OS1_12.syx` — the source firmware (read-only; it lives in the archive folder).

## Environment recreate (venv is gone; it was session-temp)

```
python3 -m venv venv && venv/bin/pip install jpype1 pyghidra
brew install ghidra m68k-elf-binutils openjdk@21   # already present
# ghidra: /opt/homebrew/opt/ghidra/libexec   ·  ColdFire language: 68000:BE:32:Coldfire
# disasm a raw section:  m68k-elf-objdump -D -b binary -m m68k:cfv4e -EB <file>
#   (file_offset = vaddr - 0x40000400  for the main OS)
```

## Do NOT redo

- SysEx unpacking / NRV2B decompress / per-section checksums — done & verified.
- Whole-image rolling checksum formula — done & verified (see Task 1).
- RTTI class hierarchy, 858 classes — done & verified (`analysis/`).
- SHARC chip ID (ADSP-2156x / SHARC+), FreeRTOS — done & verified (Task 3).
- Retrying `roblg/ghidra-sharc` (ADSP-214xx SLEIGH): **it cannot work on this file** —
  wrong core generation, and VISA-compressed. Already proven by brute force. Use selache.

## Next actions, in priority order

1. **Confirm the DN2 ColdFire part** (is it MCF5445x like the Octatrack?). No `MCF*` string
   in the binary. Resolve via ColdFire CPUID/MMU register read in `main_os`, or the DSP
   wire/handshake code. **If it matches, octabam's ColdFire addresses/hooks/patch source
   transfer nearly directly.**
2. **Ghidra on `main_os` (no flashing needed, pure analysis) — de-risks milestones 2+3:**
   - `SMVoiceStates` / `patternNotePool_t` -> find where the voice/polyphony cap is set (the >4 limit).
   - arp code (`ArpSetupMenuView`, `ARP`, "Arpeggiator ON/OFF") -> find the **gate that disables
     it for MIDI tracks** (strings "N/A FOR MIDI TRACKS" suggest MIDI tracks are special-cased).
     Report the concrete patch size for each.
3. **Type-8 (i.MX RT) analysis** — cheap win, ARM disassembles in Ghidra out of the box;
   completes the 3-core map (likely Overbridge/USB-audio path). Never examined.
4. **Build selache** (`cargo build --release`; target `-proc ADSP-21569`), **read `selload`
   source** for the 2156x LDR grammar -> write a Python parser for `04_type7.unpacked.bin`
   into a memory image. Then VISA-decompress + disassemble with `selinstr`.
5. **Verify the flash recovery path** from the upgrader's commit logic — BEFORE any first
   flash (pure engineering risk, not policy).
6. **Build the resign/repack tool** -> then start milestone 2 (MIDI polyphony, one-constant
   ColdFire patch) as the pipeline proof.

## Cheatsheet of verified facts

- Main OS is ColdFire, big-endian, at `0x40000400`; RTTI intact; **Ghidra decompiles it cleanly.**
- The **arpeggiator is ColdFire**, not SHARC (`ArpSetupMenuView`, `ArpPatternCopy`, `ARP`).
- Voice/note pool symbols: `13SMVoiceStates` (singleton), `8NotePool`,
  `Digisharc::patternNotePool_t` / `patternNotePoolStorage_v0_t`.
- SHARC image `04_type7.unpacked.bin` (836,956 B): FreeRTOS + ADI services; only **3 asserts**
  (no function-name symbol map recoverable). FreeRTOS task string **`"Audio Task"`** present
  -> xref it to `xTaskCreate` to get the audio task entry point (main SHARC foothold).
- selache (https://github.com/js216/selache): SHARC+ assembler/compiler/linker + `selinstr`
  disassembler (incl. VISA) + `selload` boot-stream generator, targets **ADSP-21569**. Our DSP.
- octabam (https://github.com/sambanks/octabam): Elektron Octatrack firmware remixer, MIT.
  Read `docs/firmware/DSP.md` (DSP payload RE + load map) and `docs/contributing/MODULES.md`
  (module/patch shape). **Its DSP is Freescale DSP5636x, NOT SHARC+** — port the algorithms,
  not the code; compile with selcc. Its Octatrack OS has **no signature**; the DN2 tail is the
  one gate octabam didn't have to face.

## Traps

- Case-insensitive FS: `HANDOFF.md` is the real filename.
- DN2 SHARC is a *FreeRTOS boot stream*, harder to map than the Octatrack's flat DSP payload;
  don't assume octabam's payload-record format applies — it won't. Use `selload`'s grammar.
- Don't trust string hits as code references; confirm via Ghidra xrefs/analysis.
- Custom DSP written for selcc is distributable (GPLv3 tools, MIT runtime), but per octabam's
  own norm: build images only from your own OS, never share a built image.

---

# Session log — 2026-10-05 (dn2-fw session): prior art, safety facts verified on 1.12

**Read this before "Next actions" above; it supersedes several of them.** Dates: today is
2026-10-05. The two logs above dated 2026-10-08 were written on or before 2026-10-05 (file
timestamps); their date is wrong.

**The project folder is now a git repo.** `.gitignore` keeps out firmware, `ele3_out/`, `ghidra/` and
`vendor/`. Commit your work, and check `git status` shows no firmware before you do.

## Superseded next actions
- **#1 (which ColdFire part):** an MCF5441x, not the Octatrack's MCF5445x. elekloader and
  digikit say so for the Digitakt II, and the 1.12 bootstrap's register use fits the
  MCF5441x map (UART8/9 at `0xEC070000`/`0xEC074000`, DSPI0/1, PIT1, crossbar). **inferred**
  for DN2 from the register map. octabam's addresses would not transfer anyway: different OS.
- **#5 (recovery path):** verified statically, below.
- **#6 (resign/repack tool):** already exists. Do not build it.

## Prior art (cloned in `vendor/`, commits pinned in `VENDOR.md`)
- **elektron-firmware-tool:** on 1.12, `-r` rebuilds byte for byte (sha256 `7e5b8a73…`) and
  the HMAC verifies. `-c 3` with an unchanged main OS passes every check, `ele3_unpack.py`
  decodes all sections identical to stock, and sections 2/4/7/8 are copied verbatim. **verified**
- **digikit:** ColdFire emulator that boots DN2 1.10E/1.11 (not 1.12 yet), SHARC+ database,
  and a partial SHARC p-code emulator (`tools/sharcemu.py`).
- **elekloader:** mod loader. Digitakt II 1.17 profile plus a core with a hook bus; a build
  ran on a real unit 2026-10-04. No DN2 profile yet.
- **modwerk.app:** DN2 page is "research" (format started, rebuild open, boot started, core
  open, mods open).

## Verified on DN2 1.12 this session
- **HMAC key:** seed string `"Multiplier"` in section 2 (DT2's is `"Master Overdrive"`).
  key = C ^ sha256(seed) ^ sha256(reversed seed), C = the 32 bytes after the seed's NUL.
  Device code: key derivation `0x80005da4`, trailer compare `0x80005e3a`.
- **Section 2 addresses:** file offset 0 maps to `0x800003FC` (a 4-byte header precedes the
  code). With objdump use `--adjust-vma=0x800003fc`; `0x80000400` is 4 bytes off.
  `0x80000408` holds the bootstrap version, `0x0201`.
- **Bootstrap self-upgrade gate:** `0x80001c72` (`bcc`, runs only when incoming > running).
  The unit is on 1.12 (user, 2026-10-05), so no 1.12-based build can trigger it.
- **No USB in the bootstrap:** zero references to the USB OTG block (`0xFC0B0000`–`0xFC0B3FFF`)
  in any encoding (the main OS has 156); it drives UART8/UART9. So STARTUP-menu recovery
  needs **DIN MIDI**. The user has no DIN interface yet (2026-10-05).
- Bootstrap menu strings: STARTUP MENU, TEST MODE, EMPTY RESET, FACTORY RESET, OS UPGRADE,
  READY TO RECEIVE, UPGRADE ABORTED.

## Not yet verified on DN2 (verified on DT2 by elekloader/digikit)
Flash map (container at `0x80000`, settings store `0x380000`–`0x400000`), every flash writer
in the main OS, free memory, the staging address, the device's own depacker accepting
rebuilt streams. Unproven on any device: the STARTUP menu still comes up when the main OS is broken.

## Plan (user agreed 2026-10-05)
1. digikit's emulator on 1.12. 2. elekloader DN2 1.12 profile and tests (per
`vendor/elekloader/AGENTS.md` and `docs/DEVICES.md`). 3. Brick checks: flash-writer audit,
device depacker and validator in the emulator, emulated boot, STARTUP menu reachable.
4. Port elekloader's core to DN2. Then hardware (needs a DIN MIDI interface), then the MIDI
machine. Contributions go upstream through forks of the `vendor/` projects.

**Tasks, priorities and owners now live in `ROADMAP.md`** (task ids T0.1 to T9.1, C0 to C7). Claim a task there before starting it.

**2026-10-05, user:** STARTUP-menu recovery tested on the unit (stock 1.12 over DIN via a MOTU M2): it works, and the unit is unchanged on 1.12. Commits are authored as naturarum, with no AI attribution. Nothing is pushed or posted publicly until there are actual results.

---

# Session log — 2026-10-05, run 1 results (T1.1, T1.2, T1.4, T2.1, T2.2, T3.1)

Details are in `notes/<task>.md`, plus `-verify-N.md` from independent checkers. Bulky data is in `out/` (ignored).

## Emulator: 1.12 boots (T1.1) [verified]
digikit's emulator boots 1.12 to the real main screen (`out/T1.1/main-screen.png`, first frame at instruction
741,578,400). The only digikit change needed was 1.12's hash in `devices/digitone-ii.toml`. The patched Unicorn
builds on this Mac with `PKG_CONFIG=/usr/bin/false`. Reproduce with `scripts/dn2-emu-112.sh all`. The digikit
changes are on local branch `dn2-1.12` in `vendor/digikit`.

## Device code accepts rebuilt files (T1.2) [verified]
- **Boot path:** the bootstrap loader `0x80000596` reads section 3 from flash `0x80000`+offset to stage
  `0x40400000`, and the depacker `0x80000432` unpacks it to `0x40000400`. Stock, `-r` and recompressed rebuilds
  all produce the stock main OS (sha256 `a098f620…`). A cold boot of a recompressed build in the emulator is
  identical to stock.
- **Validators:** the bootstrap's `0x80003c9c` (checksum, then HMAC `0x80005e3a`) and the main OS's
  `0x400dbdb0` pass on all three. **No build floor on DN2 1.12:** "Unsupported downgrade" is unreachable.
- **SAFETY 1:** section 4 is a factory board-test program, not an "updater". It has a **second main-OS loader**,
  `0x8000357c`, that stages at `0x40200000` and **unpacks in place**. The bootstrap takes that path when the
  power-on panel key read times out (boot flag bit 6). In-place margin there: stock +31,182 bytes;
  elektron-firmware-tool `-l 0` +4,791; default `-l 3` **−39,318, which corrupts the image and the depacker spins**.
  Never flash a default-level recompressed build. Every build must keep a non-negative margin at both stages.
  elekloader's Digitakt II builds may have the same issue (unchecked; T1.11).
- **SAFETY 2:** nothing on the device checks the *unpacked* image. A resealed but corrupt stream boots into
  garbage. The brick checker must compare the unpacked output itself.

## Flash writers (T1.4), corrected by two verifiers
- **Flash layout:** bootstrap at `0x40000`–`0x80000`. Container from `0x80000`. Test-flag record at `0x380000`,
  serial-number record at `0x3c0000`. Persistence at `0x400000`+. One DSPI0 NOR driver, with no chip-erase opcode.
- **Normal upgrade writer `FUN_40139104`:** erases and programs upward from `0x80000`. **But** there is no length
  clamp, and addresses are sent as 24 bits, so they wrap. Above `0x300000` it reaches the `0x380000` records;
  above `0xF80000` it wraps onto the bootstrap. It ignores flash errors and always prints "Success!". The
  **2 MiB receive buffer** (`0x4038bee0`) is the practical limit. Stock container is `0x1CC740`, leaving 211 KB
  of headroom.
- **Bootstrap self-upgrade `FUN_80001c48` runs on every boot.** It reads section 2's table entry from the stored
  container and re-flashes `0x40000` if that version exceeds `0x0201`. Section 2 and its table entry must stay stock.
- **Factory console** (`#UPGRADE`, `#FULL_UPGRADE`, which can write the bootstrap; no HMAC): only in maintenance
  mode (boot flag bit 5). That is set by holding 5 specific keys at power-on, by a console `#REBOOT` magic
  (`0xB0B0DADA` at `0x48000000`), or by a panel key-read timeout.
- **Panel MCU firmware updater `FUN_400dbbd8` runs on every normal boot.** It reprograms the front-panel MCU from
  the blob at `0x402F7D40` if the panel reports a version below `0x15`. The STARTUP menu needs the panel, so a mod
  must never touch that blob, its version word `0x402f7d44`, or the updater.
- **Section 8 is firmware for an external accessory** (breakout box; "OUTBOX 8 UPDATE REQUIRED"). The OS sends it
  over USB when the accessory reports an older release. Keep it stock.
- **Hardware backstop:** the bootstrap DYB-locks sectors `0x0` and `0x40000` on every boot, but only on one
  Spansion part. Don't count on it.

## Brick-checker requirements (T1.7), from T1.2 and T1.4
Refuse unless all of these hold:
1. The input is pinned stock 1.12.
2. Sections 2, 4 and 8 are byte-identical to stock, and section 2's table entry (`0x02010000`) is unchanged.
3. The container is at most `0x200000` bytes.
4. Seal and checksum pass the device's own code.
5. The device depacker gives exactly the intended image at **both** stages (`0x40400000`, and in place at `0x40200000`).
6. The panel blob, version word and updater are unchanged.
7. The static diff adds no references to the flash primitives, no new DSPI PUSHR command immediates, and no `0xB0B0DADA` stores.
8. The build boots to the main screen in the emulator.
9. The STARTUP menu is still reachable (T1.5).
10. It writes a byte diff report.

## MIDI machine voices (T2.1) [verified, with verifier corrections]
- **Not an engine limit:** the 4 is not in the MIDI engine or the pattern format. Notes are a flat pool of 8192
  six-byte entries (track, step, note, …). The MIDI task sends every note of a step's chain, and stock already
  allows 16 per step via NOTE EDIT and recording.
- **Where the 4 comes from:** the MIDI TRIG page's NOTE/NOT2–NOT4 rows (editing NOT2–4 forces the chain to exactly
  4, `FUN_40055606`), the 4-byte track default chord, and the 4-note keyboard SHAPE chords. So N>4 is code-only,
  and the project format stays stock-compatible.
- **Traps:** the lock-clear path writes −1 to notes 1–4. The 8-bit MIDI output counter `0x44600ca0` can wrap on big
  note-off bursts, causing stuck notes. Chains must stay sorted by micro-timing.
- **Test on the unit, no flash needed:** does stock NOTE EDIT already send more than 4 notes from a MIDI trig?

## Arp on MIDI tracks (T2.2) — partial
Both UI checks and engine structure keep the arp off MIDI tracks. The arp is self-clocked in the voice interrupt
(type-9 events) and drives internal voices only. MIDI-track trigs go to `FUN_400d8cbe`, and several functions read
the MIDI-track mask directly. The write-up was cut off; it is being finished.

## DSP (T3.1) [verified]
- **1.12's SHARC program is byte-identical to 1.11's** (sha256 `336e340a…`), so all of digikit's DN2 1.11 DSP
  knowledge applies.
- **Database:** `vendor/digikit/out/sharcdb/dn2-1.12.sqlite` has 1,157 functions. Tasks: "Audio Task"
  `0x1c9fe7`, IDLE, Tmr Svc. The Audio Task's block handler `0x1c9d6b` reads the cycle counter (EMUCLK) on
  entry, which is the lead for the CPU monitor.
- **Effects:** nobody has located chorus, delay, reverb or the master compressor yet, on either device.
- **selache:** builds at `2b26d3b`. The boot stream parses fully (95 blocks, 2 applications; `scripts/sharc_ldr_parse.py`).
  selload reproduces stock blocks byte for byte, and splicing new blocks parses clean. Its disassembler has
  width bugs in a few classes, so use digikit's decoder alongside it.
- **Free DSP memory candidates:** L1 block 2 (never written) and the L2 tail (unverified at run time).

## Static base for all agents
`out/decomp/` (rebuild: `scripts/decompile_dump.py`, ~8 min) has 14,742 main-OS functions as decompiled C and
disassembly, plus calls, strings, MMIO refs, 1,913 vtables, and `xrefs.sqlite`. Start there; see
`out/decomp/README.md`. Ghidra improvements are in `out/ghidra-copy/` (open it with `decompile_dump.open_copy`).

## Run 2, part 1 (T1.11), 2026-10-05 [verified, notes/T1.11-dt2-inplace.md]
- **Same loader on the Digitakt II:** Digitakt II 1.17's section 4 is byte-identical to DN2 1.12's apart from the
  product-word compare at `0x80003d30`. Same in-place loader `0x8000357c`, stage `0x40200000`.
- **Elektron's own stock DT2 1.17 file fails that path:** margin −31,912. The unit's section-4 loader corrupts
  it and never reaches the main OS. So the panel-timeout boot path doesn't work with stock firmware on a Digitakt
  II, which suggests Elektron doesn't rely on it.
- **elekloader's DT2 builds pass it:** its looser packer leaves +205,910 to +206,092. But elekloader's
  `syx.verify` checks only the `0x40400000` stage, so a synthetic mod can pass verify and still fail at
  `0x40200000`. That's a gap in the check, not a live problem.
- **Correction to T1.2:** the DT2 stream sizes were swapped. 1,147,584 is Elektron's stream and 1,385,586 is
  elekloader's.
- **For us:** DN2 stock passes that path (+31,182). Keep the rule that every build needs a margin of at least 0
  at both stages; it's cheap.
- Run 2's other agents (T1.5, T1.3/T1.6, T2.2, T1.7, T1.8) hit the session limit before reporting. Their partial
  work: `vendor/digikit/tools/recovery_harness.py`, `scripts/t1.3_*`, `out/T1.3/` (write-watch sessions),
  `vendor/elekloader/elekloader/codec/dense.py`, `scripts/t22_v2_checks.py`, `out/T2.2-v2/`. They are being rerun.

## Firmware archive (2026-10-05)
**`elektron-firmware/` is the user's archive: precious and read-only.** Never modify, move or delete anything in
it; extract copies to `out/firmware/`. It holds 45 files, including **Digitone II 1.00A, 1.10A, 1.10B, 1.10C,
1.10E, 1.11 and 1.12**, plus Digitakt, Digitone mk1, Octatrack, Syntakt, Rytm, A4, Heat, Model:Cycles/Samples and
Tonverk. All 39 `.syx` pass their checksums. Catalogue: `FIRMWARE.md`.

---

# Session log — 2026-10-05, run 2b results (T1.3, T1.5, T1.6, T1.7, T1.8, T2.2)

Details are in `notes/<task>.md`. Stock 1.12 now lives at `elektron-firmware/Digitone_II_OS1_12.syx` (read-only).

## STARTUP-menu recovery (T1.5) [verified; independent check: holds with corrections]
The menu, its FUNC test and the OS UPGRADE receiver all live in the bootstrap and read nothing from the stored
container. 15 emulated boots from the reset entry (`vendor/digikit/tools/recovery_harness.py`): with FUNC held the
menu appeared for a stock, a corrupted-OS, an all-0xFF and a truncated (power cut) container. A silent panel takes the
section-4 path, as expected. Corrections from the checker: the claim about how the menu's font is built was wrong in
detail, and "reset selector = 0 on a cold boot" is assumed [inferred], because the earliest startup code lies below
flash `0x40000`, outside the image. The conclusions stand. The must-never-change list is in `notes/T1.5-recovery-path.md`.

## Digitone II in elekloader (T1.3, T1.6) [verified]
Local branch `vendor/elekloader` `dn2-1.12`: profile `digitone_mk2.py`, tests `tests/test_digitone2.py`, and three new
profile fields: `more_stages` (check the in-place margin at both loader stages), `receive_max` (the OS's 2 MiB receive
buffer) and `packer`. **elekloader's normal packer makes a Digitone II container too big for the receive buffer**
(2,146,960 B), so the device uses a tighter packer, `codec/dense.py`. It keeps a margin of at least 1 byte at
`0x40200000` and was checked against the unit's own depacker. Free memory: DDR `0x46800000`–`0x47F00000` (23 MB) and
SRAM `0x8000F980`–`0x80010000` (1,664 B). A build can grow by about 164 KB of data that does not pack. A Digitone II
core (T1.9) does not exist yet. The Digitakt II profile does not yet use `more_stages`.

## Arp on MIDI tracks (T2.2) [design only; checker found corrections]
`notes/T2.2-arp-midi-v2.md` has four options, smallest first, none built. `notes/T2.2b-verify-1.md` lists the
corrections to make before building one (first-note timing with the sequencer stopped, the load on the MIDI queue,
related-track triggering). Free memory for the new code is now known (see above).

## Brick checker (T1.7) and the first flash candidate (T1.8)
- **Checker:** `tools/brickcheck/brickcheck <candidate.syx>`, ten checks, about 14 min for a full run (about 2.5 min
  without the emulated boot). Self-test: stock, `-r` and `-l 0` builds are accepted, the default `-l 3` build is refused
  on check 5, and 15 other cases behave as expected.
- **Red team:** `notes/T1.7-redteam.md`. It found gaps in the checker (details there). None of them can affect a file
  that changes no code. **Until they are closed (T1.7b), treat the checker as a reliable gate for version-name and repack
  changes only, not yet for code changes.** The agent assigned to close them was halted by an automated safety classifier
  before changing anything; T1.7b waits for the user.
- **EF01 (`out/T1.8/EF01.syx`):** stock 1.12 with the 4-character version name changed to EF01. An independent
  container diff shows exactly 36 bytes differ from stock: the 4 name bytes and the 32-byte seal. The checker accepts it
  (10 of 10). Guide for the user: `out/T1.8/FLASH-EF01.md`. Not flashed. The name shows on the power-on screen and in
  TEST MODE; the OS itself has no version page.
- elektron-firmware-tool's `-V` writes a file even when the name is longer than 4 characters (it keeps 1.12). Always
  rebuild from stock and check the "set to" line.

## Safety classifier halts, 2026-10-05
Output was halted three times: the first T2.2 write-up (run 1), the T1.7b hardening agent, and the main session's
summary of run 2b. No cause was given. If you work on T1.7b, write reports in small pieces and expect possible halts.

## Release history (2026-10-05, `notes/dn2-release-history.md`) [verified]
Compared all seven Digitone II releases from the archive. **1.11 to 1.12 changes only the main OS (+4,096 B):** the
bootstrap, board-test program, DSP program and section 8 are byte-identical, so DSP knowledge carries over between the
two. **Section 8 is the Outbox 8 accessory firmware** (added in 1.11). The bootstrap version was 0x0107, 0x0108,
0x0200, then 0x0201 from 1.11. Elektron's 1.12 notes give the STARTUP-menu steps: Transfer, CONNECTION page, "LEGACY OS
UPGRADE mode". How many main-OS addresses move between 1.11 and 1.12 is not measured yet.

---

# Session log — 2026-10-05, live control and the effects (T2.4a, T3.3, T3.4)

## Live keyboard control (T2.4a) [verified; independent tester fixed real-window bugs]
`scripts/dn2-live.sh` opens the emulator window in about 1 s, playable from the Mac keyboard. Key map: `vendor/digikit/docs/LIVE.md`
(US key positions); the user's layout is Swiss French, and their key map is in the vault (`Emulator keys.md`). Code: `vendor/digikit/emu/live.py`.
- **Speed:** skipping the RTOS idle loop (`bra.s *` at `0x400cf246`) took the main screen from 0.22x to 1.95x real time.
  The window paces to 1.00x. This covers most of T2.4b.
- **Encoders:** the firmware filters encoder counts (`FUN_4011364e`), so one key press now sends one detent's worth of counts.
- **DTIM0:** nothing in the firmware starts it. It now counts with emulated time (rate 132 MHz guessed; only tap tempo uses it).
- **Panel map** re-measured on 1.12; it matches digikit's.
- **Limits:** PLAY doesn't run the sequencer, probably because the SSI0/eDMA audio clock isn't modelled. That blocks
  testing the arp in the emulator; it's the next emulator task. No sound, no LEDs, no MIDI.

## Effects found on the DSP (T3.3, T3.4) [verified; independent check: holds with corrections]
- **Frame:** all send-FX, compressor and master parameters sit at TX frame offsets `0x9fa`–`0xa53`, one word each
  (offset = `0x9fa + 2*(k-25)`, k = mirror index; values v<<8). Compressor routing is at `0xd4`/`0xd6`, tempo at `0xd8`.
- **DSP side:** the render `0x1c2712` unpacks the frame into float structs: reverb `0x257918`, delay `0x257934`, chorus
  `0x257950`, compressor `0x257964`. Then the FX bus `0x1c1738` calls the **chorus `0xb84d5c`, delay `0xb8482c` and
  reverb `0xb836bc`**, and the master stage `0x1c1f1e` calls the **compressor `0xb8541d`**. All of them run every block.
- **Shapes:**
  - chorus: 2 x 512-sample modulated rings, a one-pole HPF and 6 biquads
  - delay: 2 x 2^19-sample rings in DDR, tempo-synced, 8 s maximum
  - reverb: a 16,384-sample pre-delay, 8 x 1,024 and 8 x 8,192 lines, 8 damping filters
  - compressor: a ratio table of 1.5, 2, 3, 4, 6, 8, 16 and 20, plus a gain-reduction readout at `0xb8552b`
- **Traps:** digikit's reach table misses the delay and reverb (their functions are mis-split). selache has a
  load/store direction bug at a few sites.
- **Unicorn's EMAC fractional mode is wrong,** so the emulator's smoothed frame values are off. Worth reporting to digikit later.

## Goal correction (user, 2026-10-05)
G10 means **more voices (for example 32 instead of 16), not more tracks.** More tracks isn't wanted.

---

# Session log — 2026-10-05, DSP memory map (T3.2)

## The DSP boot stream in memory (T3.2) [verified static; run time inferred], `notes/T3.2-sharc-memmap.md`
- **Loader:** `scripts/t32_load.py` writes flat images of the four L1 blocks, L2 and DDR plus `memmap.json` to
  `out/T3.2/mem/`. 36 self-checks pass, including all 65,372 sharcdb instructions byte for byte.
- **Layout:** IVT, C data/BSS, system stack `0x26f000-0x26f7f0` and a 2 KB C heap in L1 block 0; driver memory,
  every DMA buffer, constant tables and the 50 KB FreeRTOS heap (`0x2d08b8-0x2dd0b8`) in L1 block 1; code in L1
  block 3 and L2 `0x20000000-0x2001e880`; reverb, delay and chorus state, then 330 KB of 16-bit sample data, in DDR.
- **Free (no loader write, no static reference):** all of L1 block 2 (128 KB, SW `0x180000`), L1 block 1 tail
  `0x2dd52c-0x2effff`, L1 block 3 tail `0x39bffc-0x39ffff`, L2 `0x2008823c-0x200f9fff` (466 KB). Not yet confirmed
  at run time. `0x200fa000-0x200fffff` is reserved (cache code names it). DDR above the image is used at
  `0x8052fbe0`.
- **Audio pacing:** SPORT4 A/B is the codec (one DMA buffer = 32 frames x 2 slots); its callback `0x1ca136`
  notifies the Audio Task, so one SPORT4 DMA completion = one block (1,500/s). SPORT0 A/B is a 16-slot TDM link
  (to the ColdFire). The ColdFire frame arrives on **SPI2** (slave, DMA ping-pong at `0x2c29d0`/`0x2c39d0`); the
  block handler takes the last completed half. Output = negated Q31 of the floats at `0x268438`.
- **For G6:** the block handler already measures each block with EMUCLK and puts the cycle count in the first word
  of the frame it sends back to the ColdFire (`0x1c9e62`).
- **Offline rendering needs no peripherals:** fill the frame and inputs, call `0x1c9d6b`, read `0x268438`.
- **sharcemu.py is not usable as is for T3.6:** 48.7% of instructions have no p-code semantics (moves, basic loads,
  stack frames, DO loops), so every routine faults at its first instruction; ~2.7k insn/s (an eager big-integer
  shift; ~49k after a small fix). A dedicated interpreter on digikit's decoder is the better base.

---

# Session log — 2026-10-05, audio clock (T2.4b)

## PLAY runs the sequencer in the emulator [verified], `notes/T2.4b-audio-clock.md`
- `scripts/dn2-live.sh --ssi0-hz --ips 150e6`: digikit's SSI0/eDMA model at 96,000 requests/s (= 1,500 blocks/s),
  the SHARC's sync word `0x007fffff` planted in the RX banks, software-forced interrupts modelled (new
  `emu/intfrc.py`), and BITREV/BYTEREV added to the harness. digikit `dn2-1.12` commit `c9ca85b`.
- Checked against the screen: project at 81.0 BPM, sequencer advances 5.4 steps per emulated second = 81 BPM.
- Chain: vector 170 `0x400d55d4` (sync, handover after 64 blocks) -> channel-50 ISR `0x40025386` -> vector 191
  `0x40025e36` (frame) -> vector 108 `0x400d79c8` (clock) -> vector 121 `0x400d99de` (step).
- Cost 0.1-0.2x real time with the clock on; not profiled. Next: MIDI out of the emulator (T2.4 part two).

## FX plans and A/B (user, 2026-10-05)
The user's effect designs and the decision to listen through a Mac-native C workbench are in ROADMAP.md, "FX designs"
and T3.11. The user will send notes on Max/MSP's yflanger before the flanger is designed.

---

# Session log — 2026-10-06, FX workbench (T3.11)

## Tape delay and 3-band compressor on the Mac [verified by tests; sound not yet judged], `notes/T3.11-fx-workbench.md`
- `fx/`: portable C99 (float only, no libc, no malloc, state in one struct) for a **tape delay** (wow, flutter,
  crackle, up to 2 extra heads, tone and soft clip in the loop, tape-like TIME glide) and a **3-band master
  compressor** (LR4 crossovers that sum flat, per-band threshold/ratio/attack/release/gain/mode, soft knee, global
  sidechain with filter, dry/wet in phase). The flanger has a pass-through slot only (waits for yflanger notes).
- `make test`: 76 checks pass. Band sum flat to 0.0003 dB; static curve to 0.0001 dB; attack/release to 1 sample;
  delay time exact to 0.0001 sample; 32-sample blocks bit-identical to any other cut; 420 s of extreme settings,
  no NaN/Inf/denormal.
- Proposed pages: delay TIME, FDBK, TONE, WOW, FLUT, CRKL, TAPS, REV (fits the stock delay's 9 frame words, `k` 40
  is unread); compressor 3 band pages (THR, RAT, ATK, REL, GAIN, MODE, band edges) + a global page (THR offset,
  KNEE, TIME, OUT, LSTN, SCS, SCF, MIX), 28 values vs 8 stock frame words (open for T6.2).
- **Cost:** hand count ~5.5-7k instructions per block (delay, stock ~6.3k) and ~11-13.5k (compressor, stock ~18.7k).
  **selcc's output is 59k and 137k** (same static method as T3.4): it inlines nothing (`-O` flags change nothing) and
  keeps every value in memory. A 32-sample loop with locals halves it (`fx/sharc_svf_block.c`). Restructure or hand
  assembly needed before the unit.
- Listen: `cd fx && make renders && make listen`, then http://127.0.0.1:8711/ab.html (41 files, 8 groups).
- For the owner: `notes/T3.5-recording-plan.md` (27 takes through the M2, file names, settings as on screen); the
  open questions in section 8 of the T3.11 note.

---

# Session log — 2026-10-06, emulator MIDI, LEDs, speed (T2.4c)

Full note: `notes/T2.4c-midi-leds-speed.md`. digikit changes on `dn2-1.12`, not committed; suite 1003 passed, 99 skipped.
- **DIN MIDI = UART9** (31,250 baud): out by eDMA 37 (ring 0x4e6c9000, vector 157) and PIO for realtime bytes
  (vector 181), in by eDMA 36 (ring 0x4e6c0000, vector 156). New `emu/midi.py`; `emu.live --midi-log/--midi-file/
  --midi-port/--midi-in` (ports are virtual CoreMIDI via `uv run --with python-rtmidi`, or `DN2_RTMIDI=1
  scripts/dn2-live.sh`). [verified]
- **Proof:** CLOCK/TRANSPORT SEND on + track 1 = MIDI machine (CHAN enabled with FUNC + encoder A push) + trigs
  1 5 9 13: `FA`, clock at 80.9 BPM (project 81.0), `90 3C 64` every 4.000 sixteenths, `FC` on STOP
  (`out/T2.4c/proof/`). MIDI in: `FA` + clock run the sequencer on external clock. `out/T2.4c/saved/midi-ready.snap`
  starts with all that set up. [verified]
- **LEDs:** `B9 index palette-entry` / `B5 entry r g b` on UART8; index -> key from the factory UI test tables
  (0x401f37b0/0x401f3628). New `emu/leds.py`; drawn on the window's keys, saved next to headless PNGs. [verified]
- **Speed:** C block counter (`emu/nativehook.py`) + batched SSI minors: playing with the clock 0.10x -> 0.82x real
  time, main screen without the clock 1.95x -> 8.0x. Real time with the clock is not reached (guest work). [verified]
- **Bug fixed:** with `--ssi0-hz --ips 150e6` nearly every PIT/DTIM tick was dropped (SSI events fell just before
  the timer deadlines), so the UI loop barely ran; the SSI clock now starts half a period out of phase. [verified]
- Open: someone should play the window and a synth from the virtual port; USB MIDI not modelled; the warm
  project's pattern is 128 steps (trigs on page 1 repeat every 23.7 s).

---

# Session log — 2026-10-06, SHARC+ interpreter (T3.6)

Full note: `notes/T3.6-sharc-interpreter.md`. Code in `tools/sharcvm/` (C core `sharcvm.c`, Python driver `vm.py`);
outputs in `out/T3.6/`. Nothing in `vendor/` or `elektron-firmware/` was changed; no hardware used.
- **Design:** C core with lazily predecoded instructions; decoding stays in digikit (`sharc_disasm.py`), two counted
  overrides (16-bit IDLE, short compute 0xC0xx read as "2b"); anything without semantics traps when executed.
- **Oracle 1:** selache's 2,039 C cases built with selcc, run from `start`: **2,036 pass**; the 3 failures are csmith
  programs whose selcc assembly overwrites a pointer's stack slot (visible in the `.s`). [verified]
- **Oracle 2:** the DN2's own divide (1,992/2,000 bit-exact, rest 1 ulp), log10 (<= 2 ulp) and powf (<= 3 ulp) vs the
  Mac's libm; compute semantics agree with digikit's tracer on all 70,112 comparisons it models, apart from
  denormal/NaN inputs and FLOAT's 40-bit rounding (both per the PGR). [verified]
- **Renders** (`render_fx.py`, WAVs in `out/T3.6/wav/`): delay 5.3x real time, chorus 2.5x, reverb 2.3x, compressor
  2.2x. Checks: compressor -6 dBFS in -> -24.0 dBFS out at THR -30 dB 4:1 (exact static curve); delay time = 1/128
  bar steps; chorus delay 292-314 samples on noise. [verified]
- **Stretch done:** the whole block handler 0x1c9d6b runs (`fullblock.py`, `render_full.py`): codec input through
  the FX bus and master, 320k instructions/block (the handler's own EMUCLK agrees), 0.11x real time. No voices.
- **Rules found from DN2 evidence, not documents** [inferred]: Type 7 MODIFY bit 39 = (NW), bit 23 = short-word
  scaling; Type 4b width table differs from 3b; SIMD stores of I/M registers write both words; SIMD sub-word
  implicit = next element; misaligned word accesses byte-exact. Details in note section 2.2.
- Open: voices (need T3.3's per-track frame and main's other inits); RECIPS/RSQRTS ROM seeds unknown; odd-address
  short stores in the voice code unexplained; validation against the unit waits for T3.5 recordings (T3.7).
- **Later the same night (T3.6):** speed now 77-93M instructions/s (delay 8.1x, chorus 3.5x, reverb 3.3x, compressor
  2.9x real time; whole block 0.19x), fast paths bit-identical to the generic path. `tools/sharcvm/selftest.py`
  runs 8 regression checks in ~12 s (all pass). **For T3.11:** selcc miscompiles `fx_exp2`/`fx_log2` in
  `fx/fxmath.h` at every -O level (drops `u.f = p`, returns p without the exponent): `fx_exp2(7.6f)` = 1.516.
  Bit casts through pointers work; with that, the selcc tape delay and 3-band compressor match the native builds
  (max differences -166 dB and -123 dB). Boot from reset reaches main's driver set-up (561.7M instructions) and
  stops in the SPORT driver (needs a peripheral model; not needed for rendering). Owner-facing page in the vault:
  `code/Digitone2/T3.6 stock effects offline.md`.

# Session log — 2026-10-06, T3.11 stock vs new, SHARC cost (background agent)

Full write-up: `notes/T3.11-fx-workbench.md` sections 10-11 (T3.6 note updated where used). Nothing in `vendor/` or
`elektron-firmware/` changed; no hardware; nothing committed.
- **A/B page with the stock effects, emulated:** `make stock` in `fx/` (then `make listen`). Every group now has Dry,
  **Stock DN2 (emulated)** and New rows; keys d/s/n jump dry/stock pair/new. "Stock" = the DN2's own delay,
  compressor, chorus and reverb code in the T3.6 interpreter at the closest stock knobs (`fx/stock_renders.py`,
  mapping in section 10.2), not a recording; T3.7 checks it. Compressor groups are level-matched (stock MUP set for
  equal RMS, capped below clipping; page "match loudness" on by default). [verified: renders, page loads, keys]
- **Stock behaviour measured in the emulator** (10.1): delay time (TIME+1) x 750 + 24 samples; FDBK loop gain v/64
  (127 = 198 %); HPF/LPF first-order and in the loop; the stock delay also glides on TIME changes (2x speed at first);
  with X on, WID 0 on screen gives centred echoes (relevant to T3.5 take delay_04); compressor ATK 0.1-100 ms and REL
  10-2000 ms like the new one, MUP 0..+24 dB, SCF 64 neutral; MIX 32/96 2.3 dB dip (check on the unit).
- **T3.7 script:** `scripts/t37_compare.py` (lag, drift, gain vs path takes, residual, third-octave spectrum per take,
  `takes.txt` overrides), `--selftest` passes (self, delayed -3 dB copy, low-passed copy). Waits for recordings.
- **SHARC cost** (exact, interpreter): delay 44.5k -> **18.6k**, compressor 102.3k -> **47.7k** instructions/block
  (stock 5.4k / 16.6k); all 33 Mac renders bit-identical old vs new; SHARC-vs-Mac figures unchanged (-166.5 / -122.7
  dB). Mac build now `-ffp-contract=off` (matches the SHARC; changes renders at rounding level, -33 dB only inside
  the TIME glide, a sub-sample drift). Below stock needs hand assembly of 5 loops (11.4).
- **New selcc bug:** a local initialised with a comparison is re-evaluated at each use (11.3); worth reporting
  upstream with the union bug.

# Session log — 2026-10-06, Digitone II core (T1.9, background agent)

Full note: `notes/T1.9-dn2-core.md`. elekloader changes on its local branch `dn2-1.12`, uncommitted. Emulator only:
nothing flashed, no hardware, nothing committed in the project.
- **core-dn2 0.1** (`vendor/elekloader/mods/core-dn2`): core-dt2's design and shared sources with 1.12 addresses.
  Boot site `0x40000538` (copies the run image to DDR `0x47E00000-0x47F00000`, the top MB of T1.3's free DDR;
  `.fast` to SRAM `0x8000F980`), `ev_tick` `0x4002e464`, `ev_draw` `0x4002e4ca`, `ev_key` `0x4002f20c`, `ev_enc`
  `0x4002f256`, `ev_settings` `0x4009ff88`, `ev_personalize` `0x400987a2`: each found from Digitakt II 1.17 by masked
  code match (unique), every routine core calls identical to the DT2's whole (`scripts/t19_sites.py --verify`). [verified]
- **Two new MIDI events** for the tier-1 mods: `ev_midi_step(vev, track, step, pattern, kit)` where the sequencer
  builds a MIDI-machine trig (`0x400da0b2` step handler, `0x400d9230` pending-step pass; interrupt level) and
  `ev_midi_note(n)` in the MIDI task just before each note-on (`0x4012b490`; the handler's note/vel/len go out, and
  the booked off uses the same note). No branch or switch case lands inside any site. [verified]
- **Test mod** `examples/midi-hooktest-dn2`: sequencer notes +12, velocity = step + 1. Cold-booted in digikit, MIDI
  proof script: stock `90 3C 64` x4 became `90 48 01/05/09/0D`, offs `90 48 00`, same tempo (80.87 vs 80.88 BPM),
  every message within one audio block of stock; core alone gives stock's MIDI byte for byte; all 24 screens of both
  scripts pixel-identical to stock. [verified]
- **Brick checker: ACCEPT 10/10** for both builds (`out/T1.9/build/core-DC01.syx`, `hooktest-DH01.syx`); margins
  +54,790 / +54,751 at `0x40200000`. Still: the checker is fully trusted only for name/repack changes until T1.7b.
- **digikit trap:** `emu.live` identifies the panel by the .syx sha256, so it silently drops all keys for a modded
  file; `scripts/t19_live.py --identify-as STOCK.syx` works around it (digikit unchanged).
- **Before a flash** (note's last section): T1.8 first, then T1.7b, then core-DC01 alone, then the test mod.

# Session log — 2026-10-06, prior-art survey (background agent)

Full note: `notes/prior-art-2026-10.md` (urgent items at the top, ranked next steps in section 8). Read-only: 25
shallow clones added to `vendor/` (listed in `VENDOR.md`), nothing from them run, nobody contacted, no hardware,
nothing committed.
- **Another DN2 project, DSP included:** `angellinares/dn2_firmware_explore` (AGPL-3.0-or-later, OS 1.11) has
  hardware-confirmed mods (midiarp = our T2.6 design option 2, layermidi, lfo4, fxmod, transients, ...) and new SHARC
  code in section 7 that runs on a unit. Its splice code checks for our exact section-7 sha256 (`336e340a...`).
  Nobody has a 1.12 port. Owner's call whether to contact them. [verified]
- **For T3.8, from them** [verified in their code/docs]: append blocks before FINAL; section 7 must stay < 1 MiB
  (ColdFire loader refuses more and the DSP never boots); code in L1 block 1 `0x2dd600-0x2e7000`; **selas mis-encodes
  negative shift immediates** (silicon raises ILOPI and parks the core; every emulator accepted it); **the start-up
  enables all three L1 caches**, so `0x39c000-0x39ffff`, `0x2ec000-0x2effff` and `0x31c000-0x31ffff` are cache, not
  free (T3.2 needs correcting); SHARC clock 1 GHz; a crashed DSP = silence or a stuck ~750 Hz tone, no sequencer.
- **For G6:** reply word 0's EMUCLK count is one handler's span, not the load; idle-task time is the meter; stock
  DN2 is 63-65 % loaded whatever plays (theirs, on a unit).
- **For T5.0** [1.12 addresses verified here]: chorus page = RAM descriptor 19 (`0x42433F44`), knobs A-D, G, H, col 8 =
  records 105-110, 112 (E and F empty); short-name pointers at record `+0x30` (`0x401f83a4 + 60*e`); relabel by pointer,
  never edit shared strings. DN2 graphics routines by masked match with DT2 1.17: frameRect `0x40114906`, vline
  `0x401141bc`, pixel `0x40113e70`, textf `0x401156f8` (and TEXT `0x4011573c`), blit `0x40115adc`, icon picker
  `0x40115f88`, View::invalidate `0x4011ca9a`.
- **Our table vs theirs:** the DSP's own transient index at `0x266d08` has 32 variable-length entries (2,400/4,800/7,200
  samples); dnfw assumes 34 x 4,800. [verified: our image]
- **Also:** digikit's `work/sharc-emulator` branch couples a SHARC emulator to the ColdFire and renders DN2 1.11 voices
  (~1.22x slower than real time; T9.1); capicola has no Elektron link (no DT2 time-stretcher exists in public);
  elekloader is GPL-2.0-or-later and now needs TypeScript twins for engine changes (C2); no public DSP-section
  format (C4); the DT2 knowledge base's recovery keys (Trig 3 then 4) would pick FACTORY RESET on a DN2: ignore them.
- `VENDOR.md`: elekloader and digikit licences corrected to GPL-2.0-or-later; new clones table. ROADMAP: facts added
  under "Upstream contributions" only.

# Session log — 2026-10-06, FX parameter room (T6.0, background agent; paused)

Full note: `notes/T6.0-fx-params-room.md` (answer at the top, overlap with dn2_firmware_explore in section 6).
Emulator and interpreter only: nothing flashed, no hardware, nothing committed; `tools/sharcvm` and `vendor/` not
changed (the read watch is a private build under `out/T6.0/`). Paused at the owner's request.
- **Frame room [verified]:** the DSPI2 driver `FUN_400cf922` always sends `0xabc` bytes but fills only the first
  `0xa80`; the data halves of the last 30 PUSHR entries (`0x80003a12 + 4j`) are never written, and the DSP never
  reads frame bytes `0xa80..0xabb` (read census, 2,247 blocks, all machines and filters). Also unused on both
  sides: `0x2a` and `0xa18` (k 40). 32 words per frame; hook site: the 6-byte `jsr` at `0x40025e9e`.
- **Proof [verified]:** 68k test code in the emulator copies FX k 70..99 (set through the stock setter and glided
  by the stock smoother, exactly like k 27) into the tail; the wire frame, fed to the T3.6 interpreter, leaves
  the stock block handler's output bit-identical, and selcc-built DSP code reads the 30 values exactly.
- **Storage:** each kit's FX record (`fxSetupStorage_v0_t`, stored kit `+0x16ac`, `0xa0` B) has 28 words
  (`+0x60..+0x97`) that the stock serializer `FUN_400dcf34` never writes and the loader `FUN_400dcef0` never reads
  [verified static; zero in emulator RAM and in DNX's 2,176 kits]. Stock in-place saves keep them [inferred];
  exports into fresh buffers drop them. A modded OS needs hooks on `FUN_400dce5e`, `FUN_400dcf34`, `FUN_400dcef0`
  [design]. Kit padding `+0x28af..+0x29ff` (337 B) is a further candidate [inferred].
- **Answer:** 8 extra values each for chorus, delay and reverb plus 4 for the compressor fit in the 28 stored
  words; the 3-band compressor's other 16 need more storage, not more frame room.

# Session log: 2026-10-06, flanger page UI (T5.0, background agent): paused

The full note is `notes/T5.0-flanger-page-ui.md`. Work stopped at the coordinator's request
(overlap with dn2_firmware_explore). Emulator only: nothing flashed, nothing committed, and no
process is left running.

- **Drawing** [verified]:
  - Pages draw into a column-major 1 bpp Bitmap, with y = 0 at the bottom.
  - Primitives: blit `0x40115adc` (mask), text `0x401153f8`, printf text `0x40115948`/`0x4011573c`,
    fillRect `0x40114b22`.
  - **17 fonts**, each a 20-byte record (`0x401ba106` registers them), with glyph columns MSB at the
    bottom. Knob labels use `0x40291ca0` (4x5); the title and value bubble use `0x40290dd0`.
  - Every glyph is a frame of a `std::vector<Bitmap>` **strip**. The strips are built at boot by
    `0x40151b94`, and `0x40115e3e` picks the frame from the value.
- **Tables:**
  - the page table in RAM, `0x42433c00 + 0x2c*id`, gives the rows on the knobs and the title;
  - the descriptor table, `0x401f83a4 + 0x3c*row` (`+0x28` long name, `+0x2c` group, `+0x30`
    label, `+0x34` formatter);
  - the display table in RAM, `0x4243425c + 0x44*row`, gives the glyph `std::function` and so the
    strip.
  - **Chorus send glyph:** strip `0x44646bdc`, shared with the external-input sends.
- **Extracted:** 17 fonts, 142 strips and 513 icons, in `out/T5.0/atlas/`. Contact sheet:
  `out/T5.0/index.html`.
- **Mods** (uncommitted, `vendor/elekloader/examples/`):
  - `flanger-ui-dn2`: `ptr`/`keep2` sites for the names and title, plus run-time repointing of 3
    strip vectors to new art. The art is the send, FDBK and TIME (`scripts/t50_flanger_art.py`).
  - `fx-page2-dn2`: a 4th SEND FX page.
  - Candidate `out/T5.0/build/flanger-FP01.syx` (core + both).
  - Hot-patched into stock's snapshot, it shows FLG, "Flanger Send", FDBK and TIME with the new
    glyphs (`out/T5.0/before-after.png`).
  - Brick checker: 9 of 10 PASS. Check 8 (the cold boot) was stopped, so the title change and the
    second page have not been seen yet.
- **Owner's question, 2 pages per send effect: yes on the UI side.**
  - The view's pages are a list `{17,18,19}` (count at `0x40061616`, array at `0x40061624`). A new
    id needs the record accessor `0x400c25d8` patched, and 3 page-id tests in FxPageView remapped.
  - dnfw ran the same mechanism on the unit on 1.11 (LFO4).
  - The real cost is 8 parameter rows per page. There are no spare rows: the proof borrows "Error"
    rows that carry MIDI CCs, so a real build needs grown tables.
  - FX-setup words k 0-24 and 70-100 are unused and zero in the kit, and the frame carries only
    k 25-69.

# Session log — 2026-10-06 (later), T3.11/T5.1 FX revisions after the owner's listening (background agent)

Full write-up: `notes/T3.11-fx-workbench.md` section 12 (12.9: questions for the owner). Nothing in `vendor/` or
`elektron-firmware/` changed; no hardware; nothing committed.
- **Tape delay** (`fx/tapedelay.c`): one head (TAPS gone); CRKL -> **AGE** (hiss, sparse soft ticks and top-end
  loss that follow the echoes; AGE 127: hiss 50 dB, loudest tick 35 dB under the echoes; 0 = clean, bit-identical);
  **resonant loop filters** HPF/LPF/RES (Q to 5, passband trim, sings from ~44 % FDBK, held by the saturator);
  **WID** mono/stereo/ping-pong; DRIV on a knob; TONE and W&F macros for a 1-page layout. [verified by tests]
- **Compressor** (`fx/mbcomp.c`): the stock master compressor characterised in the emulator
  (`tools/sharcvm/tests/comp_model.py`: peak detector, hard knee, "smooth decoupled" smoothing, ATK 0.1+99.9x^2 ms,
  REL 10+1990x^2.5 ms, MUP +24.08 dB, a dB-shaped MIX table; model within 0.02-0.16 dB of the stock code) and each
  band rebuilt on it; MUP replaces GAIN; RAT = 1:1 + the stock 8 ratios; one sidechain for all bands; KNEE/OUT/TIME/
  GTHR gone. [verified]
- **Flanger** (`fx/flanger.c`, was a stub): 0.05-77 ms (MAN = 1 semitone per step, 66 = A4), damped feedback that
  rings as a Karplus-Strong string (pitch within 1.6 cents), 6 LFO shapes + slew, tempo sync, stereo phase + time
  offset, polarity, HPF; 33 KB (needs a home: more than the stock chorus's 4.9 KB). [verified by tests]
- **155 tests pass**; SHARC (selcc) = Mac in 10 configurations (flanger bit-identical, others -117..-128 dB). Cost per
  block: delay 19-24k, compressor 35-52k, flanger 16-18k (stock 5.4k / 16.6k / 14.5k): ~7 % of a block in all, inside
  the ~35 % the unit leaves free. **Two more selcc bugs** (table index clobbered; `float a = X, b = a;` drops `a`),
  worked around. selcc emits no immediate-count shifts (0 in all files; `make sharc` counts them).
- **A/B page** redone: `make stock` then `make listen` (17 groups, 105 files; per-band levels vs dry under each
  compressor render; old renders removed; earlier dry files byte-identical).
- 1-page and 2-page layouts proposed for all three effects (12.3, 12.4, 12.5).

# Session log — 2026-10-07, T1.7b: hardening the brick checker (background agent)

Full write-up: `notes/T1.7b-hardening.md`. Nothing in `vendor/` or `elektron-firmware/` was changed; no
hardware; nothing committed. The four red-team gaps are closed:
- **Gap 1 (validator):** check 6 now pins the in-OS upgrade validator `FUN_400dbdb0` and its crypto callee
  closure (content checksum, HMAC, SHA-256, the "Multiplier" seed, the key mask, the round constants). The
  red-team `danger-validator` is now **refused** (check 6).
- **Gap 2 (DSP):** new **check 11** (`tools/brickcheck/bc_dsp.py`). A changed section 7 must equal
  `--intended-dsp` exactly (no intended image -> "incomplete", never accept), then: stored <= 1 MiB and boot
  stream <= 1 MiB - 1 (ColdFire loader `0x400cf710`/`0x400cf748`), the boot stream parses with a FINAL block,
  no load block into an L1/L2 cache range, no mis-encoded negative-immediate shift (selas's silicon-parking
  form) in changed code, and a `tools/sharcvm` dynamic run (effect init + the whole block handler `0x1c9d6b`
  for a few hundred blocks) that refuses on trap / unimplemented stop / NaN or Inf / a loudness ceiling and
  reports instructions/block vs stock. `danger-dsp` is now **incomplete** (changed DSP, no `--intended-dsp`).
- **Gap 3 (tripwire + gate):** check 7 is labelled a tripwire and now also catches the `danger-computed`
  computed-address idiom (`movea.l #base; adda.l #disp; jsr (a0)` into the driver) -> **refused**; check 6
  states the NOR driver + `FUN_40139104` byte-identity as the real flash gate; new **check 12** drives the
  real in-OS upgrade writer in the emulator over the candidate container and watches every erase/program wire
  address against `[0x80000,0x380000)` (`scripts/t17b_flashwatch.py`, `bc_upgrade.py`; ~3 s from the stock warm
  snapshot, faithful because check 6 pins the writer+driver). An oversized-length demo wraps to the bootstrap
  and is refused.
- **Gap 4 (window):** check 12 adds the upgrade-writer session to the cold-boot watch; scripted menu/key
  sessions are not driven (stated as a limit, with the reason).
- **Verified:** 6 new DSP self-test cases (over-size, cache-range block, bad shift, trap, NaN, changed DSP
  with no intended image) each refused/incomplete by check 11 with the right reason; the 3 red-team files that
  used to pass are now refuse/incomplete/refuse; checks 6/7/11/12 pass stock, EF01 and the T1.9 cores
  (nothing that should pass now fails). Check 12 accepts stock/EF01/DC01/DH01 (all erase/program addresses in
  window). Self-test files and the dynamic watch outputs are under `out/brickcheck/` and `out/T1.7b/emu/`.

---

# 2026-10-12: EF01 on the unit (T1.8)

The user flashed `out/T1.8/EF01.syx` (sha256 `babb1291...41a5`, brickcheck ACCEPT 10/10 in
`out/brickcheck/EF01-2026-10-06/`) over USB with Transfer and reports success. That is the first hardware
result: our rebuild, seal and flash path works on a real unit. Next: prepare the public repo
(GPL-2.0-or-later, fresh history, README with an AI-use section, no personal paths).

# 2026-10-12: T3.8, our code in the DSP section (background agent)

Full note: `notes/T3.8-dsp-splice.md` (10-line summary at its end). Emulators and sharcvm only: nothing
flashed, no hardware, nothing committed; `vendor/`, `elektron-firmware/`, `fx/`, `tools/brickcheck/` and
`tools/sharcvm/` unchanged.
- **Pipeline** `tools/dspsplice/` (`build.py NAME`, `ldr.py`, `stub.py`, `audit.py`, `vmrun.py`, `verify_*.py`):
  selcc -> selas -> seld at fixed addresses -> new blocks before FINAL, the FX bus's CJUMP targets rewritten in
  place (block 83), FIRST argument recomputed -> `elektron-firmware-tool -c 7 -V NAME`. The unit's own depacker
  (acceptoracle, run read-only) gives exactly the intended stream for all three candidates. [verified]
- **selcc and ADI's compiler disagree on preserved registers** (selcc keeps R8-R15, stock code R3, R5-R7,
  R9-R11, R13-R15; measured with random registers). Every hook goes through a generated stub that saves R0-R15,
  I0-I5, M1-M4 and MODE1 with stock-exact absolute stores. Encoding audit: no instruction that neither the stock
  image nor selcc's hardware-validated output uses. Worth reporting to selache (owner's call). [verified]
- **Memory:** code and save area in L1 block 1 `0x2dd600-0x2e7000` (dnfw's proven range); flanger state 33 KB at
  `0x300000` (L1 block 2, FILL-zeroed); FTD1's tape code at `0x310000`, its state in the stock delay's DDR.
- **CG01** (stock chorus, wet x0.5): chorus output exactly half; wet in the output -6.021 dB; rest bit-identical;
  +1,195 instructions/block. **FLG1** (flanger in the chorus slot): SHARC = Mac bit for bit (768,000 samples);
  sends at 0 -> output bit-identical to stock; +2,434 instructions/block (+0.4 % of a 1 GHz block), stack
  +404 B. Knobs: DPTH, SPD, HPF, WDTH drive the flanger's own; FDBK +68 % and MAN 0.9 ms fixed; swelly default
  DPTH 96 SPD 30 HPF 32 WDTH 48; louder than the stock chorus (2.5-6.7 dB). **FTD1** (+ tape delay): SHARC vs
  Mac -103.8 dB; +18,707 instructions/block (+2.8 %).
- **Brick checker** (full runs, `--intended-dsp`): CG01, FLG1, FTD1 all **ACCEPT 12/12**.
- **For the owner:** `out/T3.8/FLASH-FLG1.md` (vault copy `code/Digitone2/T3.8/`); CG01 is an optional smaller
  first step, FTD1 comes after FLG1 works. T3.9 row updated.
- **Suggestion (not done, it changes the gate):** check 11's dynamic run feeds only the reverb; adding chorus
  and delay sends would make a replaced effect run on signal inside the gate too.

# 2026-10-12: FLG1 on the unit (T3.9, M3)

The user flashed `out/T3.8/FLG1/FLG1.syx` (sha256 `72c2690c...efed6`; brickcheck ACCEPT 12/12 with the
strengthened DSP gate, `out/brickcheck/FLG1-2026-10-12/`) over USB and reports: "IT WORKS PERFECTLY". The first
new code running on the Digitone II's SHARC: the flanger from `fx/flanger.c` in the chorus slot, spliced by
`tools/dspsplice/`. Milestone M3 reached. The splice method (selcc, fixed-address link, blocks before FINAL,
register-saving stubs) is now proven on silicon, so FTD1 and later effects reuse it unchanged.
Next: FTD1 (flanger + tape delay) re-checked with the strengthened gate (its earlier ACCEPT ran the delay slot on
silence); then the user's listening notes decide the flanger's fixed knobs and FLG2.

# 2026-10-12: FTD1 cleared; dnfw's reply

- **FTD1** (`out/T3.8/FTD1/FTD1.syx`, sha256 `14123a67...8f88`) rerun with the strengthened DSP gate:
  **ACCEPT 12/12** (`out/brickcheck/FTD1-2026-10-12/`; dynamic run 1,500 blocks with all three sends at full
  volume, peak 0.683, 341,040 instructions/block vs stock 320,281). Its earlier ACCEPT had run the delay slot on
  silence. Guide `out/T3.8/FLASH-FTD1.md` (vault copy `code/Digitone2/T3.8/`).
- **dnfw replied** on angellinares/dn2_firmware_explore#49: busy shipping their wavetable machine beta, wants to
  watch our progress for a few weeks before collaborating, and asked us to "check the licensing in your files".
  Read-only audit: `notes/license-audit-2026-10-12.md` (no dnfw code or prose copied; missing SPDX/NOTICE, two
  uncredited GPL-2.0+ adaptations from digikit and elekloader, one README sentence close to theirs, two notes
  worded like licence avoidance). Fixes wait for the user's go.

# 2026-10-12: licence notices and credits

After dnfw's "check the licensing in your files", a read-only audit (kept out of the repo, `out/license-audit-2026-10-12.md`,
vault copy) found no dnfw code or prose copied, but no licence lines, no named copyright holder, two uncredited
GPL-2.0+ adaptations and some bad wording. Fixed with the user's go:
- `NOTICE`: copyright (naturarum and contributors), the GPL-2.0-or-later grant, code adapted from other projects
  (digikit, elekloader, selache MIT with its licence text, UCL, octabam) and the findings we build on, per task.
- `SPDX-License-Identifier: GPL-2.0-or-later` in all 150 code files; origin lines in the adapted files
  (`scripts/decompile_dump.py`, `tools/brickcheck/bc_syx.py`, `t33_frame.py`, `t60_cf_frame.py`, `t32_emu_probe.py`,
  `t37_compare.py`, `ele3_unpack.py`, `tools/dspsplice/audit.py`, `hmain.c`, `t60_probe.ldf`).
- Our own wording for the README's firmware sentence (it was close to dnfw's); the README's credits now say what we
  build on; `notes/prior-art-2026-10.md` no longer reads like licence avoidance; T5.0's wrong "dnfw relocated the
  table" fixed inline; FIRMWARE.md says the 1.12 file is Elektron's own download.
- Kept, by the user's call: `analysis/rtti_classes.json` (class names and addresses, for interoperability).
- Checked: every .py compiles, every shell script parses, `fx` `make test` 155/155.

# 2026-10-12: T5.2, the Space Echo in C (background agent)

Full note: `notes/T5.2-space-echo.md`. The owner changed the delay's direction: an RE-201 style echo with 3 heads
picked by MODE, 8 controls (TIME, MODE, INTENSITY, AGE, CENTER, BW, WIDTH, PAN). `fx/spaceecho.c/.h`, reusing the
tape engine; `fx/tapedelay.c` (FTD1) unchanged and its renders bit-identical.
- Heads 1:2:3 (Roland GX-1/RE-202 manuals), MODE order 1, 2, 3, 1+2, 2+3, 1+3, 1+2+3 (RE-202; RE-201 [inferred]).
  INTENSITY 1.1 x^1.5 on the sum of active heads; AGE = wow, flutter, hiss, crackle, top loss, drive; BW 127 is an
  exact bypass, BW 0 a half-octave band at +6 dB; PAN MONO/ALT/RND; motor inertia on TIME glides.
- `make test` 282/282 (127 new: head positions per mode, transparency, BW bypass, no clicks on any knob, 120 s fuzz
  + 300 s silence). SHARC vs Mac: bit-identical to -108 dB (worst case, selcc's division in a runaway loop).
  Cost 18.4k (one head) to 38.0k (three) instructions/block; worst 48.6k mean, 67.9k peak; state 4,163,232 B (fits
  the stock delay's DDR with 31.6 KB spare), code ~38 KB.
- A fifth selcc bug: the preprocessor substitutes macro parameters inside number literals (`1.0f` -> `1.0x`);
  worked around, repro in the note 6.2.
- Page: 6 stock rows map (TIME, X->PAN, WID->WIDTH, FDBK->INTENSITY, HPF->CENTER, LPF->BW); MODE and AGE need 2 more.
  Open questions for the owner in the note's last section (vault: `Space Echo questions.md`).

# 2026-10-12: T6.1, can the parameter table grow? (background agent)

Full note: `notes/T6.1-table-growth-study.md` (task id T6.1 reused; the old T6.1 "map the stock compressor" is now
T6.4). Emulator (hot-patched RAM) and static analysis only.
- **Answer: yes, with conditions.** High confidence for the table itself, medium for "behaves like stock
  everywhere" until a cold boot of a grown build and a session on the unit.
- **Row ids are never saved:** kits, sounds, p-locks, LFO and mod destinations are stored by slot k through fixed
  k -> stored-id tables [verified]. A new row's effect on projects depends only on its k and display flags.
- **114 sites**, all same-length operand edits: 56 base references, 58 row-count compares (55 functions; T5.0's
  "about 84" was an over-count). Relocated to our DDR with all sites patched and the boot-time maps rebuilt: 25
  screens byte-identical to stock, slot and CC/NRPN maps identical; with 8 rows added, the new knobs draw, turn,
  show value text and write the kit's FX setup through the stock setter [verified, emulator].
- **Risks found and reproduced:** (1) lockability comes from display flags (bits 0 and 3), not the group: a new FX
  row copied from a lockable template took a p-lock that was saved as a lock on sound slot 70 (filter envelope
  delay). **T5.0's `fx-page2-dn2` has this bug** (SHPE copies display row 0x63); set flag bit 3. (2)
  `ParameterUpdateCache` (MIDI CC/NRPN input, 17 lanes x 0x141) overflows for a new row: a CC corrupted the other
  bank and the flush sent bogus changes for track 1 rows 0x18/0x19. Resize it or keep new rows out before any new
  row gets a CC. (3) Per-row rules and id tests (MIDI CC out sends only rows 0x69..0x83 on the FX channel; section
  3.4's list: free surviving k, 0 = classic, no randomiser, no CC in 32..63, Overbridge id > 387).
- **Where growth matters:** new synth parameters: every machine has free k slots that survive save/load (FM Tone
  2, WaveTone 15, FM Drum 10, Swarmer 32, the unused 5th machine 40). Shared filter/amp parameters are limited by
  storage, not rows (k 65 and 100 are lost on save).
- **Cheapest extra knob per send page, no growth:** stock row 0x7a "Delay FX Routing" (k 40) on the empty delay G
  slot (one page-slot write at run time `0x42433f0c` <- 0x7a, plus name/range/formatter data); proved as "Delay
  Mode", 0..6, saved per kit (stored id 16). Reverb G: row 0x83 (k 48). The stock DSP never reads frame word
  `0xa18`, so new DSP code can. Catch: every existing kit stores 1 there, so 1 must mean the classic behaviour.
- Not established: Overbridge with unknown ids, rounded-size arrays, MIDI CC out for new rows, the randomiser,
  +Drive save/load across builds, a cold boot of a grown build, the 1.12 per-machine SYN page table.

# 2026-10-12: T5.0b, FTN1 renames the FX pages (background agent)

Full note: `notes/T5.0b-rename.md`. **FTN1** (`out/T5.0b/FTN1.syx`, sha256 `60d1cd1f...58e8`) = FTD1's section 7
byte for byte + core-dn2 0.1 (same .elemod as DC01/DH01) + `fx-rename` 0.1 (elekloader fork, `examples/fx-rename-dn2`,
pushed on `naturarum/elekloader` `dn2-1.12`). Brickcheck **ACCEPT 12/12** with both intended images
(`out/brickcheck/FTN1-2026-10-12/`); cold boot main screen identical to stock.
- 29 sites, 195 bytes of strings, all data: 23 descriptor name pointers, 6 `pea` operands (two page titles, two page
  short names, two page boxes). The titles need no boot code: the core copies the strings before the page records
  are built, which closes T5.0's open cold-boot item.
- Names: SEND FX 3 "Flanger (3/3)" box FLG; SEND FX 1 "Tape (1/3)" box TAP (TAPE is 15 px, the box holds 13);
  track FX, the flanger page's G, FX MIX and EXT MIX (LTAP/RTAP, LFLG/RFLG with DUAL), LFO DEST lists, DEST box
  "FX TAPE". Knob labels unchanged; CC numbers unchanged.
- Evidence: 28 screens, same keys on stock and FTN1: 7 identical, the other 21 differ only inside renamed words;
  LEDs identical; after 14 knob turns, an LFO destination and two p-locks, the parameter mirror and the DSP frame
  are byte-identical to stock.
- Found: the rows' group-name pointers are never read (T5.0 renamed them for nothing); an unreferenced second table
  of old names at `0x401d0f04`; the FX MIX knobs are the effect pages' VOL (k 31, 39); elektron-firmware-tool `-V`
  cannot overwrite a version field elekloader set; T3.3's frame capture refuses a modded main OS (scratch copy used).
- Note for later sessions: in `vendor/elekloader`, `origin` is irpina's upstream; our fork's remote is `naturarum`.

# 2026-10-12: FTD1 on the unit

The user flashed FTD1 (flanger + one-head tape delay) over USB: it works. Their verdict on the delay: "sounds not
amazing, but that's not an issue"; the repitch on TIME changes is "very interesting" (the delay-time glide, tau
0.12 s, playback speed held within 0.5x..1.5x). The Space Echo (T5.2) is the delay's next design. Next on the unit:
DH01, then FTN1.
