# OB01: why Overbridge refuses our builds, and the fix

Date: 2026-10-22. Marks: **[verified]** = run here, read in the bytes, or in the owner's logs; **[inferred]** = reasoned,
not run. Nothing was flashed.

## Answer (short)

- **The cause is the OS version field.** Every build since EF01 writes its name into the container's 4-byte version
  field (header `+0x14`, `1.12` on stock), and the main OS sends that field to the computer. Overbridge 2.19.4 on the
  owner's Mac, 18 Oct (CMP1 on the unit): the slave process logs `Device OS Version: CMP1 : 62`, the master
  `Received OS version CMP1`, and the Digitone II plugin `Device is not running compatible OS Version for this version
  of Overbridge.` then `Failed to open device` (`~/Library/Logs/Elektron Overbridge/`). The engine itself reaches
  `Idle` with the unit; the refusal is the plugin's. The build id (`62`) is stock in every build. [verified: logs; the
  strings `Device OS Version: {} : {}` in Overbridge Engine and `has an incompatible OS version!` in the Digitone II
  component]
- **The fix, os-version-dn2 0.1** (fork `examples/os-version-dn2/`): the two main-OS sites that send the version out
  now get a copy of the header with `1.12` in the version field. The flash, the main OS's header cache and its five
  other readers are untouched, so the bootstrap still draws the build's name on the power-on screen and in TEST MODE.
- **OB01** = CMP4 + that mod: `out/OB01/OB01.syx` `bfb886237377555aea6778ba49d0b48b48e7ce46a04bd9be77365f36cb0b62dd`.
  DSP section stored byte for byte as CMP4's; main OS = CMP4's six `.elemod` files plus this one.

**On the unit, 2026-10-22: Overbridge works with OB01** (owner: "overbridge works fine"). Transfer: every later build with the fix (FL01, FL02, DT01, DJ01, 2026-10-23/24) was sent to the unit with Transfer, so it connects.

## 1. Where the version goes [verified]

T1.8 (`notes/T1.8-ef01-candidate.md` 2) mapped it: the main OS reads the container header only through its cache
getter `0x40134850` (reads flash `0x80000` once into `0x4463fc2c`, returns it, or 0 without `ELE3` / product 52). A raw
scan of the stock 1.12 main OS finds `0x40134850` at exactly seven sites (re-checked here):

| site (jsr) | function | reads | goes to |
|---|---|---|---|
| `0x400bb516` | `FUN_400bb4f8` | +8 (build id) | a settings record |
| `0x400bb73a` | `FUN_400bb696` | +8 | compared with the stored id |
| `0x400ce69e` | `FUN_400cd5f2` | +8 `%.16s` | factory console (maintenance mode) |
| **`0x400cf1c8`** | `FUN_400cedfc` (a task entry, passed by `pea` at `0x400ced26`) | +8..+0x17 | `FUN_40005906` copies 16 bytes to `0x40287ad5`: **the reply of USB vendor request 0xC0/0x02** |
| `0x400e953c` | `FUN_400e9366` | +8 | backup-file header |
| `0x4011ed5a` | `FUN_4011ed4a` | +8 `%.04s` | exception screen |
| **`0x40126270`** | `FUN_40125f9e` | +8 (4 bytes), +0x0C (12 bytes) | **the reply to `MidiRpcSoftwareVersionRequest`** (Transfer's protocol) |

Only the two in bold carry the version field; the other five read the build id at +8, which our builds keep stock.
The bootstrap reads the header from flash itself (power-on screen and TEST MODE draw `+0x0C..+0x17`). Overbridge's
`version : build` pair matches the USB reply's layout (`0062` then the 12 bytes ending in the version) [inferred: which
of the two channels Overbridge uses was not traced; both are fixed].

Before the fix, CMP4's emulator container (C4EM) reports, from its 400M cold-boot snapshot
(`scripts/ob01_emucheck.py`, `out/OB01/emu-C4EM-version.json`): USB reply `0062        C4EM`; the RPC handler's code
run from the getter's call site: build `0062`, version `        C4EM`.

## 2. The fix [verified]

`osv_hdr` (52 bytes of `.run`, 32 of `.bss`): calls the stock getter; on 0 returns 0, else copies the 0x20 header bytes
to `osv_copy`, writes `1.12` at +0x14 and returns `osv_copy` in d0 (an integer return: the callers read d0; it touches
d0, a0, a1 only, the caller-saved registers, and neither caller reads a0/a1/d1 before setting them). Sites: `jsr` at
`0x400cf1c8` and `0x40126270` (stock `4eb940134850`). The version string is the build's target, 1.12; nothing in the
mod depends on what the build is named.

Alternatives not taken: keep `1.12` in the field (the name would show nowhere: the main OS never draws the version);
put the name in the header's spaces `+0x0C..+0x13` (the splash would show it, but both replies carry those bytes, and
how Overbridge parses them is unknown; brickcheck check 2 also refuses it); a second line from section 5 on the splash
(new bootstrap data; not needed).

## 3. OB01 checks [verified]

`scripts/ob01_build.py` (`out/OB01/build-OB01.json`), all pass:
- pins: stock 1.12, `CMP4.syx` `c1df74aa...`, CMP4's main OS `a87af6f6...`, its section 7 `94dea646...`;
- header as stock but the version field `OB01`; sections 2, 4, 5, 8 stored as stock; section 7 stored byte for byte as
  in `CMP4.syx` and depacking to CMP4's `section7.bin`; section 3 as elekloader packed it;
- mods: CMP4's six `.elemod` files (sha256 as in CMP4's manifest) + os-version 0.1;
- **both main OS images rebuilt independently** from their `.elemod` files, manifest layout and symbol map (sites,
  core `.boot`, every `.run`, the tables; link.py's relocation rules), byte for byte: CMP4's and OB01's;
- against CMP4, the stock part of the image differs in 12 bytes, all inside 5 mod sites: os-version's two (CMP4 `jsr
  0x40134850`, OB01 `jsr osv_hdr 0x47e00a4c`) and param-grow's three, whose targets moved 52 bytes (os-version's
  `.run` sorts before param-grow's); the blob grows by 52 bytes.

Emulator, brick checker: section 4.

## 4. Emulator and brick checker [verified]

- **Cold boot** of `OB01.syx` in digikit to 400M instructions (`out/OB01/emu/`, emu.checkpoint): the same eight tasks
  as CMP4's boot. From that snapshot (`scripts/ob01_emucheck.py`, `out/OB01/emu-OB01-version.json`): header cache
  `0062        OB01` (the flash, unchanged by the mod); **USB reply `0062        1.12`**; **the RPC handler's code from
  `0x40126270` replies build `0062`, version `        1.12`**; `osv_hdr` called alone returns `osv_copy` (`0x47e017a4`)
  holding the cache's 0x20 bytes but `1.12` at +0x14. C4EM (CMP4) gave `C4EM` in both replies (section 1).
- **The bootstrap screens** (`scripts/t1.8_bootstrap_screens.py`, `out/OB01/screens/`): the power-on screen shows
  `OB01` top right, TEST MODE `OS: OB01`.
- **Brick checker: ACCEPT, 12 PASS, 0 FAIL, 0 SKIP** with `--intended-main out/OB01/OB01.main.bin --intended-dsp
  out/CMP4/CMP4-dsp/section7.bin --dsp-blocks 1500` (`out/brickcheck/OB01-2026-10-22/report.md`, 2,732 s): cold boot
  to the main screen with no flash write, section 7 the intended image.
- **Not checked here:** Overbridge itself (no unit), and which of the two replies Overbridge reads [inferred: the USB
  one]. If Overbridge also caches the old answer on the Mac, a restart may be needed (dn2_firmware_explore saw
  Overbridge refuse a modded 1.11 build until the host was rebooted, then work with stock and mods alike).

## 5. Files

`vendor/elekloader/examples/os-version-dn2/` (mod.json, osver.c; fork, uncommitted); `scripts/ob01_build.py`,
`scripts/ob01_emucheck.py`; `out/OB01/` (OB01.syx + .sha256, OB01.main.bin, build-OB01.json, FLASH-OB01.md, mods/,
build/, emu/, logs/); `out/brickcheck/OB01-2026-10-22/`; this note. Vault: `code/Digitone2/OB01/FLASH-OB01.md`.
