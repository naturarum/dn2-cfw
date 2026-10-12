# brickcheck

One command that takes a candidate Digitone II OS 1.12 `.syx` and refuses it unless every
safety check passes. It is the gate before anything is sent to the unit (task T1.7; the
evidence behind each check is in `notes/T1.2`, `T1.4*`, `T1.5`, `T1.6` and
`notes/T1.7-brickcheck.md`).

```sh
tools/brickcheck/brickcheck <candidate.syx> [--stock elektron-firmware/Digitone_II_OS1_12.syx] \
                            [--intended-main <main_os.bin>] [--intended-dsp <dsp.bin>] [--out DIR] \
                            [--no-emu-boot] [--no-device] [--no-dsp-sim] [--no-upgrade-watch] \
                            [--warm-snap S] [--dsp-blocks N] [--dsp-loud X] [--decomp DIR]
```

- `--stock` defaults to `elektron-firmware/Digitone_II_OS1_12.syx`. It must have sha256
  `7e5b8a733d4ab1fdf7e5d22757f7d8eddaa3cbf71d0858874a791844c02ceb35`, or check 1 fails and
  nothing else runs.
- `--intended-main` is the unpacked main OS (based at `0x40000400`) the candidate must depack
  to. Without it, the intended image is the stock main OS (sha256 `a098f620...`).
- `--intended-dsp` is the depacked DSP image (section 7) a **changed** section 7 must depack to
  (check 11). Without it, a changed section 7 makes the verdict "incomplete" (the DSP is not
  vouched for); an unchanged section 7 needs no flag.
- `--no-emu-boot` skips checks 8 and 12 (the emulator; about 12 min for 8). `--no-device` skips
  the Unicorn runs of the unit's code (parts of checks 4 and 5, and all of check 9). `--no-dsp-sim`
  skips check 11's dynamic SHARC run (`tools/sharcvm`). `--no-upgrade-watch` skips check 12.
  `--warm-snap` sets check 12's warm snapshot (default `out/T2.4a/warm/warm800M.snap`). Each skip
  turns into a SKIP, so the verdict can then be "incomplete" at best.
- `--dsp-blocks` (default 256) and `--dsp-loud` (default 4.0, full scale is 1.0) tune check 11's
  dynamic run.

It prints each check as PASS, FAIL or SKIP with its evidence, then the verdict, and writes
`report.md` and `report.json` to `out/brickcheck/<candidate name>/` (git-ignored). The screen
PNG and the emulator logs go to `emu/` beside them.

| verdict | when | exit |
|---|---|---|
| accept | all twelve checks PASS | 0 |
| refuse | any check FAILs | 1 |
| incomplete | none FAILs, but at least one is SKIP. A skip is never a pass | 3 |
| (usage error) | a file is missing | 2 |

A check that crashes is reported as FAIL. Only "accept" means the file may be sent.

## Requirements

- digikit in `vendor/digikit` (branch `dn2-1.12`) with its uv environment and patched Unicorn
  (`scripts/dn2-emu-112.sh setup`). The wrapper runs `vendor/digikit/.venv/bin/python`.
- digikit's `tools/acceptoracle.py` (T1.2) and `tools/recovery_harness.py` (T1.5), imported,
  not changed.
- `out/decomp/main_os/functions.tsv` (and `xrefs.sqlite`) for function names in the diff. Without
  them check 10 is SKIP.
- Optional: `vendor/elekloader` with the `digitone_mk2` profile; check 6 then confirms that every
  range in the profile's `protected` list is checked here.

Nothing talks to hardware. The unit's code runs under Unicorn and stops before any flash code.

## The checks

| # | what | how |
|---|---|---|
| 1 | The stock file is pinned; the candidate parses | sha256 of the stock file. Candidate: F0/F7 framing, 16-byte framing messages and 128-byte data messages, Elektron id, device id and transfer constant as stock, every per-message checksum, counters from 242 in order, the framing count on both framing messages, ELE3 magic, a sane section table, zero padding after the container. Its own decoder, cross-checked against digikit's |
| 2 | Sections 2, 4, 5 and 8 byte-identical to stock; section 2's table entry unchanged; same section list | stored bytes compared; section 2's entry must be `00000002 000000a0 00003f30 02010000` (version `0x0201`); section ids, order and destinations as stock; the ELE3 header as stock apart from its version field. Sections 3 and 7 may differ (ROADMAP); a changed section 7 must still depack |
| 3 | Container at most `0x200000` bytes | the container, and the stream as the OS receives it (data messages x 101 bytes, T1.6's `receive_max`); flash end below `0x380000` |
| 4 | Checksum and seal | independently: the content checksum, and the HMAC-SHA256 trailer with the key derived from the stock bootstrap (checked first on stock's own seal). On the device's code: the bootstrap's verify-and-commit `0x80003c9c` must reach UPGRADING (`0x80003ce8`), and the main OS validator `0x400dbdb0` must return 1 |
| 5 | The device depacker gives exactly the intended main OS at both stages | the bootstrap's boot path from `0x80000ee4` (section lookup, flash read to `0x40400000`, depack to `0x40000400`, jump into the main OS) and section 4's loader `0x8000357c` (stage `0x40200000`, in place), both run on the stock code with the candidate as flash. Output must equal the intended image and the run must enter the main OS. Both in-place margins are reported and must be at least 1 byte. The Python depack (dt2/elz) must agree |
| 6 | Protected ranges unchanged in the unpacked main OS | the panel MCU blob from `0x402F7D40` to the end of the stock image, its version word `0x402f7d44`, the updater `FUN_400dbbd8`, and the four ranges in T1.6's profile (panel updater and helpers, panel firmware, NOR driver `0x40134A28-0x4013621C`, upgrade writer `FUN_40139104`) |
| 7 | Static diff **tripwire** (not the gate): nothing new that can touch the flash | in bytes that differ from stock: no absolute or pc-relative reference into the NOR driver or to a flash writer (T1.4 clusters A to D), no DSPI PUSHR command word for the flash chip select (`0x8402_00XX`, `0x0802_00XX`, `0x8002_00XX` and the other CONT/EOQ/CTCNT forms) as a long-immediate operand, nor as a raw word in the forms that cannot be a ColdFire instruction (top byte `0x80/0x84/0x88/0x8C/0x04`, non-zero command byte), no word in the DSPI0 register block `0xFC05C000-0xFC05C0FF`, no `0xB0B0DADA`, no computed `movea.l #base; adda.l #disp; jsr (An)` into the driver. A reference built another way is **not** seen: the gate is check 6 (driver + writer byte-identical) and check 12 |
| 8 | Emulated cold boot reaches the main screen | digikit: `emu.extract`, a cold boot to 400M instructions, then `tools/emucheck.py --device dn2` for 600M more (T1.1's flow). emucheck must PASS and the main screen must latch. The screen is saved as `emu/main-screen.png`. A watch on every NOR write/erase/protect entry and on DSPI0 PUSHR must see no write, erase or protect command in either phase |
| 9 | The STARTUP menu is still reachable | T1.5's harness on the candidate's container: FUNC held must stop in the menu key loop with the same screen as stock; no key must boot normally to the loader (the self-upgrade does not force the menu); a silent panel must take the section-4 path as stock does; OS UPGRADE must reach READY TO RECEIVE with no flash read; no run may reach the bootstrap's flash erase or program |
| 10 | Diff report | every changed byte range of the unpacked main OS, with address, length and the functions it touches (`out/decomp/main_os/functions.tsv`), plus which sections differ |
| 11 | Section 7 (SHARC/DSP) | stock, **or** equals `--intended-dsp` exactly (a changed DSP with no intended image is "incomplete"). When intended: stored `<= 1 MiB` and boot stream `<= 1 MiB - 1` (the ColdFire loader limits, `0x400cf710`/`0x400cf748`); the boot stream parses with a FINAL block; no load block into an L1/L2 cache range; no mis-encoded negative-immediate shift (the selas form) in changed code; and a `tools/sharcvm` dynamic run (effect init + the whole block handler `0x1c9d6b`) that refuses on trap, unimplemented stop, NaN/Inf or a loudness ceiling, and reports instructions/block vs stock. `bc_dsp.py` |
| 12 | Dynamic in-OS upgrade flash watch | drives the real writer `FUN_40139104` in digikit's emulator over the candidate's container and refuses any erase/program whose wire address is outside `[0x80000, 0x380000)`, or any protect/OTP command. Runs the stock writer, faithful because check 6 pins the writer+driver; skipped if check 6 did not pass. `bc_upgrade.py` + `scripts/t17b_flashwatch.py` |

## What a PASS does not prove

- The code that checks and unpacks runs from the **stock** file: that is what a unit on 1.12
  has. If the unit already runs a modified OS, its validator is that OS's, not stock's.
- Check 7 is a byte scan of changed bytes, not a disassembly. A reference built at run time
  (an address computed from pieces, a jump table filled by code) is not seen. Check 6 keeps
  the driver itself stock, and check 8's watch catches a flash command if the boot runs one.
- A mod that moves code shifts every byte after it: check 7 then sees all of it as changed
  and refuses anything that resembles a flash reference there. That is on purpose.
- Check 8 boots the image depacked by digikit's Python codec; check 5 is what shows that the
  unit's depacker gives the same bytes. The emulator stubs hardware (DSP audio, USB, panel);
  code paths that only run on real hardware are not exercised.
- Check 9 runs the stock bootstrap. Because check 2 keeps section 2 and its entry stock, the
  candidate only changes what the bootstrap reads from flash.

## Self-test

```sh
vendor/digikit/.venv/bin/python tools/brickcheck/selftest.py --jobs 6   # full: about 30 minutes on 10 cores
vendor/digikit/.venv/bin/python tools/brickcheck/selftest.py --quick    # no emulated boot
```

It builds its test files under `out/brickcheck/selftest/` and checks:

| case | file | expected |
|---|---|---|
| a-stock | the stock file | accept |
| b-repack | `elektron-firmware-tool -r` | accept |
| c-l0 | `-c 3 <stock main OS> -l 0` | accept |
| d-l3 | `-c 3 <stock main OS>` (default level 3) | refuse: check 5, margin at `0x40200000` |
| n1-msgsum | one data message's payload byte changed | refuse: check 1 |
| n2-sec2ver | section 2 entry version `0x0202`, resealed | refuse: checks 2 and 9 |
| n3-trailer | one trailer bit flipped | refuse: check 4 |
| n4-stream | one main OS stream bit flipped, resealed | refuse: check 5 |
| n5-panelword | panel firmware version word changed, `--intended-main` | refuse: check 6 (5 passes) |
| n6a..n6f | one new instruction: `jsr` abs to page program, `jsr (d16,pc)` to block erase, a PUSHR word, a DSPI0 address, a `0xB0B0DADA` store, a one-frame chip erase `move.l #0x080200c7,d0` | refuse: check 7 |
| p1-btst | `btst #6,d2; cmpi.b #0x20,d2` added (they look like PUSHR words as raw bytes), no device runs | incomplete: check 7 passes |
| m1-string | the unreachable "Unsupported downgrade" message changed to "Unsupported DOWNGRADE", full run | accept |
| n7-big | 300,000 random bytes appended to the main OS | refuse: check 3 |
| n8-intended | stock, with `--intended-main` of n5's image | refuse: check 5 |
| dsp-stock-noint | a changed section 7, no `--intended-dsp` | incomplete: check 11 SKIP |
| dsp-oversize | a changed DSP whose boot stream exceeds 1 MiB - 1 | refuse: check 11 |
| dsp-cache | a changed DSP with a load block into the L1 I-cache range | refuse: check 11 |
| dsp-badshift | a changed DSP with the mis-encoded (selas) negative-immediate shift | refuse: check 11 |
| dsp-trap | a changed DSP with a corrupted reachable handler instruction | refuse: check 11 (dynamic trap) |
| dsp-nan | a changed DSP whose reverb coefficients are NaN | refuse: check 11 (dynamic NaN) |

## Files

- `brickcheck`: the wrapper. `brickcheck.py`: the checks, verdict and reports.
- `bc_syx.py`: the independent transport/container reader, content checksum and seal.
- `bc_device.py`: the unit's code under Unicorn, through acceptoracle and the T1.5 harness.
- `bc_static.py`: protected ranges, changed ranges, reference scans, function names.
- `bc_emuboot.py`: the emulated cold boot with its flash-command watch.
- `bc_dsp.py`: section 7 (DSP) checks (check 11): sizes, boot-stream parse, cache ranges, the
  bad-shift scan, and the `tools/sharcvm` dynamic run.
- `bc_upgrade.py`: check 12, the dynamic in-OS upgrade flash watch (runs `scripts/t17b_flashwatch.py`
  in digikit's venv).
- `selftest.py`: the self-test.
