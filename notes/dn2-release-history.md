# Digitone II release history, from the firmware archive

Source: the seven Digitone II files in `elektron-firmware/` (every release from 1.00A to 1.12), each unpacked with
elektron-firmware-tool; the table compares the unpacked sections. Raw data: `out/versions/summary.json`.
Release notes are Elektron's, from the readme inside each zip. Date: 2026-10-05.

| release | bootstrap version | sec 2 bootstrap | sec 3 main OS | sec 4 board test | sec 7 DSP | sec 8 Outbox 8 |
|---|---|---|---|---|---|---|
| 1.00A | 0x0107 | 28,254 B | 3,061,120 B | 32,776 B | 832,064 B | none |
| 1.10A | 0x0108 | changed | changed | same | changed (833,060 B) | none |
| 1.10B | 0x0108 | same | changed | same | same | none |
| 1.10C | 0x0108 | same | changed | same | same | none |
| 1.10E | 0x0200 | changed (30,302 B) | changed | same | same | none |
| 1.11 | 0x0201 | changed | changed (3,192,192 B) | changed | changed (836,956 B) | added (159,948 B) |
| 1.12 | 0x0201 | **same** | changed (3,196,288 B) | **same** | **same** | **same** |

"Same" and "changed" compare the unpacked content with the release above it. Hash prefixes are in `out/versions/summary.json`.

## What it tells us
- **1.11 to 1.12 touches only the main OS (+4,096 bytes).** The bootstrap, the board-test program, the DSP program and
  the Outbox 8 firmware are byte-for-byte identical, measured directly. So everything learned about the DSP in 1.11 holds
  for 1.12, and a DSP mod built for 1.12 would fit 1.11 as well. The main OS differs, so ColdFire mods are per-release.
  How many addresses move between 1.11 and 1.12 is not measured yet (it would need a decompile of 1.11).
- **The bootstrap changed three times across the releases** (0x0107, 0x0108, 0x0200, 0x0201). Elektron's notes say: "After
  some upgrades the bootstrap is also upgraded. This upgrade is performed right after the first restart of the device."
  Our unit already has 0x0201, which every 1.12-based file carries, so it is never upgraded again.
- **Section 8 is the Outbox 8 firmware** (Elektron's breakout box). The 1.11 notes read "Added Outbox 8 configuration
  support", and the section first appears in 1.11.
- **The DSP program grew at 1.10A and at 1.11** and was otherwise stable.

## How far the main OS moved from 1.11 to 1.12 [measured; the explanation is inferred]
- The two main OS images share only their first 28 bytes. After that the code shifts in many small steps: of 66,503
  48-byte windows of 1.11, 48.7% occur verbatim in 1.12, at 535 different offset shifts (mostly +0 to +1,344 bytes).
  The rest differ, probably because absolute call and data addresses changed with the shifts.
- **So a patch pinned to fixed addresses will not carry over to another release.** Hook points must be found by byte
  pattern with the address operands masked, as digikit's own notes advise. For now every mod is pinned to 1.12 by hash,
  and a port to a new release re-derives its anchors on purpose.

## Elektron's notes for the last releases
- **1.12 (30 Sep 2026):** the Main outputs can be routed through Outbox 8; [FUNC] + [UP/DOWN] moves between channels in
  the Outbox 8 channel menus.
- **1.11 (9 Sep 2026):** Outbox 8 configuration support. Fixed MIDI messages sometimes not being sent at very low BPMs.
- **1.10E and 1.10D:** support for an updated production process.
- **1.10C:** a freeze when Note On/Off messages were sent to the FX control channel (also listed under 1.10B).

## Elektron's upgrade steps in the 1.12 notes
- **In the OS (USB):** Transfer, DROP page, drag the file in, press [YES] on the unit.
- **From the STARTUP menu (MIDI port, not USB):** hold [FUNC] while powering on, press [TRIG 4], then in Transfer click
  "LEGACY OS UPGRADE mode" on the CONNECTION page, pick the file and the MIDI interface, and press UPGRADE.
- "Downgrading the device OS is not supported, and is performed at your own risk. User content may be lost."
