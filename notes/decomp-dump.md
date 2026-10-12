# Decompilation dump of the 1.12 main OS and bootstrap

2026-10-05. A searchable static-analysis base for later tasks (T1.4 flash-writer audit, T1.5
power-on path, T2.1 voice limit, T2.2 arp on MIDI tracks). Output is in `out/decomp/`
(git-ignored, Elektron-derived). The tool is `scripts/decompile_dump.py`, and the file
formats are described in `out/decomp/README.md`.

Marks: **[verified]** = checked against the bytes or produced by a run; **[inferred]** =
reasoned from structure.

## Quick use

```sh
cd out/decomp/main_os
grep -P '\tN/A FOR MIDI TRACKS\t' strings.tsv        # string -> functions
awk -F'\t' '$3=="ArpSetupMenuView"' vtables.tsv      # class -> methods by slot
awk -F'\t' '$3=="0x4018ac5a"' calls.tsv | cut -f1,2   # callers of a function
grep -P '\t0xec07' mmio.tsv                          # who touches UART8
grep -l 'vtable_SMVoiceStates' decomp/*.c            # constructors/destructors of a class
less decomp/400ba2a8_FUN_400ba2a8.c                  # file name = entry address
sqlite3 xrefs.sqlite '.tables'                       # same data for joins
```

Each `decomp/*.c` starts with a comment header: address, size, ranges, signature, callers,
callees, vtable slots, strings and MMIO addresses used. `disasm/*.s` has the same functions as
instructions with their bytes.

## Rerun

```sh
venv/bin/python scripts/decompile_dump.py all        # about 8 minutes on the M2 Pro
venv/bin/python scripts/decompile_dump.py dump       # only the dump (6 min), from the existing copy
venv/bin/python scripts/decompile_dump.py check      # sanity checks, also written to out/decomp/check.txt
```

`setup` copies `ghidra/dn2` to `out/ghidra-copy/dn2` only when the copy is missing. To start
over from the persistent project, delete `out/ghidra-copy` first. The final dump comes from one
clean `all` run (log: `out/decomp/all.log`).

**Opening the copy with another tool:** its programs now use the language
`68000:BE:32:ColdfireEMAC`, which is installed only in the private settings directory
`out/ghidra-settings`. Start the JVM with `-Dapplication.settingsdir=<El-Fw>/out/ghidra-settings`.
The simplest way is `import decompile_dump as dd; dd.start_ghidra('6g'); proj = dd.open_copy()`
with `scripts/` on `sys.path`. Plain `scripts/gh.py` cannot open the copy. Only one JVM can hold
a project at a time.

## Sizes (final run)

| | main OS | bootstrap |
|---|---|---|
| functions (decompiled / failed) | 14,742 (14,735 / 7) | 145 (145 / 0) |
| call edges (resolved to a function) | 57,365 (57,353) | 468 (468) |
| data references from instructions | 49,267 | 1,397 |
| strings (with any reference) | 13,385 (11,793) | 122 (122) |
| MMIO rows | 1,186 | 394 |
| vtables / slots | 1,913 / 12,244 | none (no RTTI) |
| disk | 168 MB (decomp 60 MB, disasm 72 MB, sqlite 19 MB) | 1.9 MB |

The project copy is 63 MB. [verified, `manifest.json` and `du`]

## Environment

- venv recreated as written in HANDOFF: jpype1 1.5.2, pyghidra 3.1.0, Ghidra 12.1.4
  (`/opt/homebrew/opt/ghidra/libexec`), Homebrew openjdk@21. [verified]
- `ghidra/dn2` was not locked and was never modified. All work ran on `out/ghidra-copy/dn2`, so
  other agents keep the persistent project, and no lock is held for long. [verified: it was opened
  read-only only for the base checks and the instruction diffs]

## Image bases

- `main_os`: block `main_os` = `0x40000400..0x4030c97f`, and its bytes equal
  `02_type3.unpacked.bin` (sha256 `a098f620…`). [verified]
- `upgrader` (the bootstrap, section type 2; the name is historical): imported from a 4-byte-stripped
  payload. Block `upg_payload` = `0x80000400..0x80007a59`, and its bytes equal
  `01_type2.unpacked.bin[4:]` (executable sha256 `32710b94…` = sha256 of the file from offset 4). So
  file offset 0 = `0x800003FC`, as required. **The base is correct, so no `bootstrap_3fc` was
  imported.** [verified] Only the 4 header bytes `0x800003FC..0x800003FF` (`0x00007656`) are not in
  the program.
  At `0x80000400` is `0x80010000`, at `0x80000404` is `0x80000de8`, and at `0x80000408` is `0x0201`
  (the bootstrap version). [verified] Reading the first two as the initial SP and PC (vector-table
  layout) is [inferred]: `FUN_80000de8` exists and has no callers.
- Absolute JSR spot check on the original analysis. Main OS: 25,493 decoded `jsr (xxx).l`. All 25,453
  whose target is in memory land on a function entry; the other 40 target `0x0000c098..0x0000eb7c`
  (see the embedded blob below). Bootstrap: 98 of 98 land on function entries. [verified]

## What was applied to the copy (`prepare`; reports in `out/ghidra-copy/prepare_<program>.json`)

From `vendor/digikit/docs/refs/ghidra-coldfire-augmentation.md`, the cheap part was digikit's
ColdfireEMAC language. The MCF5441x feature-mask language that the doc proposes is a project of its
own, and was not attempted. Steps 5 to 8 repair the original analysis, which turned out to be the
larger gain.

1. **Language → `68000:BE:32:ColdfireEMAC`**, copied from `vendor/digikit/tools/ghidra/ColdfireEMAC`
   and compiled into `out/ghidra-ext/`. Ghidra's default translator switched the programs in place.
   No instruction was lost by the switch itself (562,768 before and after). [verified]
2. **Undecodable sites:** 25 "Unable to resolve constructor" sites in the main OS, 24 of them
   `movclr.l ACCx,Rn`: `0x40012b1a`, `0x400db2dc`, `0x400db446`, …, `0x40137d92`. All 24 now decode,
   and the containing bodies were repaired. Their C shows `emacSaturate(...)`, a pcodeop whose
   saturation is not modelled. The 25th, `0x4020f396`, is rodata inside the `__class_type_info` vtable,
   not code. [verified] In the bootstrap, `0x80000eb8` (`fbef 0003 fff8`, objdump: `wdebug.l (-8,sp)`)
   is in neither language. So `FUN_80000de8` (the reset entry) still decompiles with a "bad instruction"
   warning: read its `.s` file or objdump past that point. [verified]
3. **MMIO blocks:** volatile uninitialised blocks `MMIO_EC0` (`0xEC000000`, 1 MiB) and `MMIO_FC0`
   (`0xFC000000`, 1 MiB) in the main OS, and `MMIO_EC0` in the bootstrap, which already had `periph_fc`.
   A survey of every 32-bit operand ≥ `0xEC000000` found real registers only in those two windows. The
   rest are masks and constants: `0xEDB88320` (CRC-32), `0xFFFFxxxx` negatives. [verified]
4. **Register labels:** digikit's `McfLabels.java` placed 87 labels in each program (UART8/9, INTC0-2,
   PIT0-3, DTIM0-3, GPIO A, eSDHC, eDMA TCD35). [verified]
5. **RTTI / vtables (main OS):** 915 typeinfos (858 from `analysis/rtti_classes.json` plus 57 root
   classes found only as bases), and 1,913 vtables with 12,244 slots pointing at 5,972 distinct
   targets. These got the labels `typeinfo_<Class>` and `vtable_<Class>[_atK]`, and the slots were
   defined as pointers. Then:
   - 3,751 functions were created at slot targets that had none: virtual methods and thunks reached
     only through vtables. [verified]
   - At 29 slot targets the original analysis had made pointer data of a real prologue. The first is
     `0x40018818`, `lea (-0x80,sp),sp`, which is `ArpSetupMenuView` slot 4: Ghidra had made it the
     pointer `0x4fefff80`. The data was cleared and the code disassembled. [verified]
   - The original analysis had disassembled parts of 3 vtables: 19 instructions around `0x401d9ff4`
     and `0x4020f36c..0x4020f394`, with a function at `0x4020f36c`. These were removed. [verified]
   - 5,874 functions were renamed `Owner::vslotNN` (or `Class::vslotNN_atK` for secondary-vtable
     thunks). These are **slot positions, not method names**. [inferred from vtable layout] 98 targets
     that vtables disagree on kept their `FUN_` name.
6. **Orphan code:** in the main OS, 446 runs (14,334 instructions) of disassembled code lay in no
   function. A function was created at each run start that follows a `rts`/`bra`/`jmp` (589). That left
   21 runs (1,275 instructions), all after a fall-through. Bootstrap: 6 runs (396 instructions), 9
   functions created, 1 run (20 instructions) left. [verified] Example: the direct references to
   "ARPEGGIO" at `0x4019a7ca` and `0x401915ee` had no function before this.
7. **Re-analysis** with **"Non-Returning Functions - Discovered" turned off.** On a first try with it
   on, the re-run marked ordinary functions as non-returning (`0x4002c318`, `0x40115948`, `0x401cfbaa`;
   `0x4002c318` has a normal prologue and is called from `0x4001ebea`, with code after the call). It then
   deleted the code after every call to them: 6,274 instructions, for example
   `0x4001ebec..0x4001f2f0`. [verified by diffing instruction starts against `ghidra/dn2`] With the
   analyzer off, the no-return set is the original's 5 functions. [verified]
8. **Code that had been typed as data:** the analysis flowed into defined data at 289 sites, mostly
   switch cases. Examples: `0x400700ae` is `move.l #0x88,d0`; `0x4002612c` is
   `clr.l (-0x78,a6)`, which had been the pointer `0x42aeff88`. Only sites that flow reaches from inside
   a function were touched: the data was cleared and disassembled, repeatedly, because each fix
   uncovers the next 4-byte pointer. 285 of the 289 decoded; the 4 that did not are `0x400700a6`,
   `0x400700aa`, `0x401103d6` and `0x4019c850`. [verified] The cause is [inferred]: the original
   import's block `sram_tail` (`0x4030c980..0x7fffffff`) makes nearly any `0x4xxxxxxx` word a valid
   address, so Ghidra's pointer analysis accepted code words as pointers.

Net main OS: 10,390 → 14,742 functions, and 562,768 → 571,107 instructions (+8,358). The only 19
instructions removed are the ones inside the vtables. Error bookmarks went from 81 to 1
(`0x4020f396`). Bootstrap: 136 → 145 functions. [verified, instruction diff against `ghidra/dn2`]

## Sanity check (`check`, all PASS, `out/decomp/check.txt`)

- `"N/A FOR MIDI TRACKS"` is one string, at `0x4021a466`. It resolves to six functions through six
  direct `pea (0x4021a466).l` sites: `0x400ba314` in `FUN_400ba2a8`, `0x400ba594` in `FUN_400ba52c`,
  `0x400ba7f8` in `FUN_400ba790`, `0x400baa5c` in `FUN_400ba9f4`, `0x400bacc0` in `FUN_400bac58` and
  `0x400baf4c` in `FUN_400baee4`. These match a raw byte search for `48 79 40 21 a4 66`, which finds the
  same six sites and no others. [verified]
  Lead for T2.2: none of the six is called directly or held in a vtable. `FUN_400b8b00` (which also uses
  "VELOCITY TO VOL") loads each as an immediate (for example `move.l #0x400ba2a8,D0` at `0x400b927e`)
  and stores it next to a PC-relative address, probably building std::function-style callbacks for a
  page of parameters. [inferred]
- `ArpSetupMenuView` has 7 vtables and 36 slots: a 19-slot primary at `0x401d5080`, plus secondaries
  at -4, -8, -12, -16, -76 and -104. `SMVoiceStates` has 3 vtables and 6 slots; its primary at
  `0x401ddcf4` holds `0x4019930c` and `0x40199348`. [verified]
- The bootstrap's `STARTUP MENU` string (`0x8000651d`) resolves to `FUN_80002dc4`. [verified]

## Findings made on the way

- **Embedded blob in the main OS, linked at `0x0000C000`.** At file address `0x402F7D40` there is a
  header of `0x0000E778` and `0x00000015`, and code starts at `0x402F7D48`. Of the 42 distinct low
  absolute `jsr` targets in `0x402f7xxx..0x402faxxx`, 37 land right after a `rts`/`jmp`/`bra` with the
  constant delta `0x402EBD40` (run = file − `0x402EBD40`), so `0x402F7D40` runs at `0x0000C000`.
  [verified, objdump scan] The blob reaches peripherals at `0xFFFF8000+` (for example
  `lea 0xffff8006,a0` at `0x402f7d48`) and RAM at `0x0080xxxx`. That map is not the MCF5441x's: it
  looks like a ColdFire V1-style microcontroller (flash at 0, RAM at `0x00800000`, registers at
  `0xFFFF8000`), so this is probably firmware for a second MCU that the main OS carries. [inferred]
  The only reference from the OS is `FUN_400dbbd8`, which reads the header word at `0x402F7D40`
  (`0x400dbd66`) and subtracts `0x402F7D40` from a pointer (`0x400dbcd0`). [verified] Code continues
  past what Ghidra disassembled: there are instructions near `0x403064a0` that use run address
  `0x17c08`. [verified bytes] In calls.tsv, 79 `*_ALIAS` edges map blob calls to file addresses; 11
  low targets still do not hit a function start. [verified]
- **Bootstrap SRAM routine.** `0x80000e5a..0x80000e6e` copies `0x80007900..0x80007a55` to
  `0x8000F000`. The bootstrap then calls `0x8000F010` from six sites (`0x800005dc`, `0x80000d1e`,
  `0x80001d4c`, `0x80004724`, `0x8000540c`, `0x80005496`) and `0x8000F100` from `0x80000eee`. These are
  `FUN_80007910` and `FUN_80007a00` in the image. [verified] `FUN_80007a00` (run address `0x8000F100`)
  passes `0x8000F000` to `FUN_80005462` and calls `FUN_80007910`. It then writes CACR
  (`movec d0,cacr` at `0x80007a3c`), loads a pointer from `0x8000F00C`, reads the word it points to,
  and calls that (`jsr (a0)` at `0x80007a4c`). [verified, disassembly] This is very likely the jump into
  the main OS, which matters for T1.5. [inferred] The dump has 7 `*_ALIAS` edges for these calls.
- USB OTG (`0xFC0B0000..0xFC0B3FFF`): 150 MMIO rows in 39 main-OS functions, none in the bootstrap.
  The bootstrap's 45 `0xFC0Bxxxx` rows (for example `0xfc0b8180`) are above that window. [verified,
  mmio.tsv] This agrees with HANDOFF's "no USB in the bootstrap". HANDOFF's main-OS count of 156 used a
  different method.

## Known gaps

- **Virtual calls are not edges.** Ghidra resolved 23,517 register calls (`COMPUTED_CALL`), but calls
  through a vtable load are missing. To find callers of a virtual method, take its slot from
  vtables.tsv and search the disassembly for the slot offset `(4*slot, An)` after a vtable load. An
  empty caller list proves nothing: 1,066 main-OS functions have no caller, no pointer reference and
  no vtable slot.
- **`vslotNN` names are positions.** Nothing names the methods themselves (no symbols), apart from the
  47 `Class::method` strings in rodata (for example `Kit::updateMirror`), which are in strings.tsv.
- **Decompile failures (7):** `0x400c2c4e` (the decompiler's response buffer was exceeded; it was
  retried at 300 s and failed again), and `0x401bd414`, `0x401bec0e`, `0x401becb2`, `0x401bed56`,
  `0x401bedfa`, `0x401bee9e` ("Cannot properly adjust input varnodes"). Use their `.s` files.
- **EMAC semantics:** `movclr` is a pcodeop (`emacSaturate`). Accumulator saturation, rounding and
  MACSR flags are not modelled, so check DSP-like routines against the instructions.
- **The blob is analysed at the wrong addresses.** Its functions carry a `note`, and its absolute
  addresses are in its own map. It needs its own program at `0x0000C000` (a separate task).
- **Strings:** `via_ptr` is a heuristic. It walks down from a data pointer to the first address with a
  symbol or reference, at most 0x1000 bytes, and calls it a table base if code references it. Two
  samples checked against the bytes are correct: the word table at `0x4029c1e4` (+0x3e0 → "FOGGY",
  pushed by `pea` at `0x4011a352`), and `0x401f3930` (+0x70 → "PAGE MOD", UI test view). [verified]
  Treat other via_ptr rows as leads. 8,264 references are `data_only`. Raw strings shorter than 6
  characters are kept only when something references them.
- **Reference sites:** for constants that Ghidra propagated, the site in datarefs.tsv is the
  instruction that uses the value (`0x400b928c`), not the one that loads it (`0x400b927e`).
- **MMIO through a base register** shows only the base's `lea`, plus `decomp` rows taken from the C.
  The `decomp` rows can include constants that only look like addresses (`0xec000000` in
  `MainScreenView::vslot02`).
- **Leftovers:** 21 orphan code runs remain (1,275 instructions), all after a fall-through. The 4
  conflicting-data sites listed in step 8 also remain, and 12 call edges have no callee function: 11 in
  the blob, plus `0x40003610` → `0x400035ee`.
- **The improvements exist only in the copy.** `ghidra/dn2` still has the stock language and 10,390
  functions. Promoting the copy, or redoing the steps there, is a decision for the project, because
  other agents use it.
