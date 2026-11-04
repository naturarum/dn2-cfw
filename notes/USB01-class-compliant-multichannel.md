# USB01: class-compliant multichannel audio (idea, 2026-10-22)

The goal: the unit's USB AUDIO/MIDI mode sends every track as its own channel, so
an iPhone, an iPad or any computer sees a multitrack interface with no app or
driver. Not started; this note keeps what a first look at the stock 1.12 OS showed.

## What the 1.12 OS has (stock main image, out/brickcheck-redteam/stock-main.bin)

The USB descriptors are static tables in the main image, around file offset
0x2fbdbc-0x2fc11d: four device descriptors (Elektron's vendor ID, product IDs
0xffff, 0x0134, 0x0b34 and 0x1034), each followed by its configuration. The
configurations' total-length fields are 0 in the tables, so the OS fills them at
run time.

- **USB MIDI mode:** a MIDI-streaming function only, high-speed and full-speed
  versions (bulk endpoints of 512 and 64 bytes).
- **Overbridge mode (0x0134):** vendor-specific interfaces with interrupt
  endpoints at bInterval 1: IN EP 0x83 with alternate settings up to 1,012 bytes
  per microframe, OUT EP 0x03 up to 256 bytes, a vendor bulk/interrupt pair on
  EP 0x02/0x82, plus a USB MIDI function. So in Overbridge mode the unit already
  sends about 1 KB every 125 us (about 8 MB/s).
- **USB AUDIO/MIDI mode (0x0b34):** USB Audio Class 2.0 (protocol 0x20), the
  class iOS, iPadOS, macOS, Linux and Windows 10+ drive natively. One clock source
  (48 kHz); input and output terminals with **2 channels** each; streaming
  interfaces with 4-byte subslots, 32-bit resolution, PCM; OUT EP 0x03 isochronous
  async with a feedback endpoint EP 0x82, IN EP 0x83 isochronous async, 56 bytes
  per microframe (7 frames x 2 channels x 4 bytes). Plus a USB MIDI function.

## What it would take

- Raise the channel count in the class-compliant tables: the input terminal, the
  AS general descriptor, the channel cluster, and wMaxPacketSize. With 4-byte
  subslots, 34 channels need 7 x 34 x 4 = 952 bytes per microframe, which fits
  one high-speed isochronous transaction; 42 channels would need 3-byte subslots
  (882 bytes) or high-bandwidth isochronous.
- Find the code that fills the isochronous IN packets (2 channels per frame now)
  and make it copy N channels.
- **The main unknown:** whether the per-track streams reach the main OS in this
  mode, or only the pair the USB OUT setting picks. If only the pair, the DSP's
  Overbridge output has to be switched on in this mode too.
- Check host quirks (clock and sample-rate requests, caching of descriptors by
  product ID), and whether a new product ID or a separate USB CFG entry is wiser
  than changing the stock mode.
- Safety: the flashing route also uses USB, so the first build changes only the
  channel count, and USB must still enumerate in the emulator before any flash.

## A second look, for 0.3.0 (2026-10-30)

The owner: "can we look into adding the multitrack audio for ipad for 0.3.0?" Nothing built; facts gathered so far.

- **Nobody has it.** The Digitone II's class-compliant mode is stereo; its USB OUT setting picks MAIN or one track for
  the left or right channel (Elektronauts, "Selected track(s) to USB out on DNII"). Overbridge carries multitrack, but
  only to a computer with Elektron's driver. No Elektron OS adds class-compliant multichannel to the Digitone II or
  Digitakt II (web search, 2026-10-30; the Digitakt II's "multichannel" in OS 1.03 is Overbridge). Of the community
  clones in vendor/, none works on it: DNX opens the stereo USB audio for a scope, dn2_firmware_explore's usbprobe
  is a SysEx debug channel; octabam's 4 to 24 channel USB audio is the Octatrack's (a DSP56300, not reusable here).
- **One track already reaches the USB path in USB AUDIO mode** (the stock USB OUT choices) [inferred: through the
  DSP's routing or the ColdFire's; to find].
- **The Digitakt II's ColdFire-SHARC audio link** (1.16, vendor/digikit-sharc-emulator/docs/findings/04): SSI0 is
  TDM, 16 slots of 24 bits each way, all 16 active. Overbridge mode's about 1 KB per microframe is more than 16
  channels, so the per-track streams may reach the ColdFire by another path, or only in Overbridge mode [inferred];
  to check on the Digitone II's 1.12.
- **Hosts.** iPadOS, macOS, Windows and Linux drive USB Audio Class 2 natively, many channels included [inferred from
  how class-compliant interfaces behave; to try with the owner's iPad and AUM].

## A plan for 0.3.0 (proposed, not started)

1. **Study, no flashing:** in the 1.12 image, the isochronous packet code of USB AUDIO mode and of Overbridge mode,
   where each takes its audio, and how the per-track streams leave the DSP in each mode (the emulator for the
   ColdFire side). Decide which channels to send: main L/R and the 16 tracks as stereo pairs is 34 channels, 952
   bytes per microframe at 32 bits, one high-speed isochronous transaction.
2. **Safety:** USB is also how Transfer flashes. A main OS with broken USB is recoverable, but only through the
   bootloader's OS UPGRADE over DIN MIDI. So the stock USB modes stay byte for byte; the multitrack interface is its
   own USB CONFIG choice, and the first unit build only enumerates (silent channels) before any audio is copied.
3. **Builds on the unit:** enumeration on the iPad and the Mac; then the audio; then a long run for dropouts and the
   ColdFire's load.
4. **Release:** in the base (every build) if it costs no DSP room, else as an option in the patcher.

## The study, part 1 (2026-10-30): the audio path in 1.12, and what multitrack needs

Owner (2026-10-30): start the study; Overbridge sends 16 tracks plus the effects as their own tracks; the 16 synth
tracks alone would do ("the main purpose of sending audio to an iPad would be to have effects on the iPad"); watch
MIDI tracks, the host changing the rate, the rate and bit depth the Digitone sends; if room runs short, USB audio
before our effects. Elektron is working on an Overbridge app for the iPad; a class-compliant mode needs no app.
Sources: `out/decomp/main_os` (the 1.12 decompile dump), `out/brickcheck-redteam/stock-main.bin`, and the stock
DSP in sharcvm (`tools/sharcvm/fullblock.py`). Marks as above.

**Every track already reaches the ColdFire in USB AUDIO mode [verified].**
- The DSP sends 42 channels per sample frame: channels 0-13 in the SSI0 TDM slots 0-13 (16 slots of 24 bits,
  `0x400d56fe` sets SSI0 up: CCR `0x16f00`, masks `0xffff0000`; eDMA 48/50 ping-pong 2 KB halves, receive
  `0x4e6e7100`/`0x4e6e7900`; slot 0's first word of a block is a sync marker), channels 14-41 in the per-block SPI
  reply at `0x800053c0`: 24-bit big-endian, two channels in 6 bytes, 84 bytes a sample frame. One reader,
  `FUN_400253d0(channel, sample)`, serves both. 42 = Overbridge's count (its 1,012-byte packets = 42 x 6 x 4 + 4).
- `FUN_40025e36` (vector 191, the per-block handler, 1,500 a second, 32 samples) reads the USB mode
  (`FUN_40006c4a` = `DAT_4058f2a8`): **5 = USB AUDIO/MIDI**: it builds two channels from USB OUT's choice
  (`0x80005362` on, `0x80005363` sum a pair to mono, `0x80005364`/`65` the channel numbers; the menu's format is
  `L:T%d R:T%d`, so any track to either side) through `FUN_400253d0`, and sends them with `FUN_40003d00`;
  **6 = Overbridge**: `FUN_40138740` and `FUN_40003118` send all 42 and a header. So in mode 5 every channel can
  already be read; the stock picks two.
- The stock DSP in sharcvm with our ordinary frame (no USB flag), a tone on each input: channels 40/41 are input
  L/R (correlation 1.000); the input sent to the reverb lights 36/37, to the chorus 38; channel 0 carries the left
  input (the main mix) and the marker. So the DSP fills the reply's channels in normal running. Channels 2-33 are
  then the 16 tracks as L/R pairs and 34/35 the delay [inferred: the count, USB OUT's track choice; no voice was
  played in sharcvm].

**The stereo sender** `FUN_40003d00` [verified]: asynchronous packets of 5, 6 or 7 samples (6 on average; the rate
`0x40287a84` re-estimated every 1,024 packets by `FUN_400036fc`), 8 bytes a frame (two 32-bit samples, swapped to
little-endian), a ring of 16 buffers of 56 bytes at `0x80009000`; each packet a dTD from `FUN_400036c0`, primed on
the driver's endpoint 7 (EP3 IN) by `FUN_40005a86`.

**The descriptors of USB AUDIO/MIDI (PID 0x0b34)** [verified, image offsets]: UAC2 (protocol 0x20); clock source
1, internal fixed, frequency read-only; IT 3 (USB streaming, host to device, 2 ch) -> FU 2 -> OT 4; IT 5 (2 ch, the
Digitone's audio) -> OT 6 (USB streaming); AS interface 1 (OUT, EP 03 async, 56 B, feedback EP 82) and AS interface
2 (IN, EP 83 async with implicit feedback, 56 B), both format type I, 4-byte subslots, 32 bits; then USB MIDI.
Multitrack changes values only: IT 5's and AS interface 2's bNrChannels, its subslot and bit resolution, EP 83's
wMaxPacketSize. 42 channels at 32 bits need 7 x 42 x 4 = 1,176 bytes, over the 1,024 one transaction carries; at 24
bits in 3 bytes (the DSP's own resolution, nothing lost) 882 bytes; 44 (two more for USB OUT's choice as today)
924 bytes.

**The rate** [verified]: a host's SET_CUR on the clock is accepted only at 48,000 (`FUN_40005f84`); any other rate
is stalled. The Digitone runs at 48 kHz only; iPadOS and macOS convert for apps at other rates.

**What it costs:** no DSP code and no DSP room (nothing competes with our effects); a main-OS mod: the descriptor
values, and a multichannel sender in place of `FUN_40003d00`'s call in mode 5, with a bigger packet ring (16 x 924
B) in RAM the USB controller can reach. Packing 42 channels x 32 samples a block is about 1,300 3-byte copies, of
the order Overbridge mode already does.

**Recovery if a build's USB AUDIO mode fails to enumerate:** USB CFG on the unit back to OVERBRIDGE or USB MIDI
(those configurations untouched), and Transfer works again; no DIN cable needed for that case.

**Open:** the channel layout (keep channels 1-2 as USB OUT's choice for stereo apps?); USB AUDIO/MIDI changed in
place or a fourth USB CFG choice; what a MIDI track's channels carry (silence expected, on the unit); channel names
(string descriptors) for the iPad apps; iPadOS with 44 channels at 24 bits (AUM, Logic, Cubasis) and macOS.

## The study, part 2 (2026-10-31): every track is a stereo pair already

Owner (2026-10-31): "Mono is fine if it's technically easier but indeed we need stereo for panning, so do it if you
can." Stereo costs nothing more: the DSP already sends each track as an L/R pair.

**The DSP's output table** [verified: the stock image's data at `0x25d760`, read in sharcvm after the effect init;
the packing function `0x1c244d`]: 42 entries of three words, one per output channel, copied to the stack each block
(`0x1c25c0`, 63 SIMD pairs); the third word of every entry is then set to the stream gain `0x3fb6db6e` (1.4286). The
two pointers of each entry are the channel's two possible sources (one is picked for all channels by a test of
R12 at `0x1c25cd` that sets the strides [inferred]; the second is the post-pan track buffer or the effect's output):

| channels | second pointer (first) | what |
|---|---|---|
| 0, 1 | (filled at run time) | the main mix L, R (part 1) |
| 2 + 2(n - 1), 3 + 2(n - 1) | `0x259f68 + 0x100 (n - 1)`, `+ 0x80` (`0x258f68 + ...`) | **track n L, R** (n = 1..16) |
| 34, 35 | `0x25b068`, `0x25b0e8` | the delay's output L, R |
| 36, 37 | `0x25b368`, `0x25b3e8` | the reverb's output L, R |
| 38, 39 | `0x25b668`, `0x25b6e8` | the chorus's output L, R |
| 40, 41 | (filled at run time) | the inputs L, R (part 1) |

The track buffers `0x259f68 + 0x100 (n - 1)` are cleared every block (`0x1c24b5`, 16 pairs) and filled by the
per-track pan stage (`0x1c2515`: the track's level and pan from `0x2578b8`/`0x257898`, its voices' sum from the
pointer table `0x257ea8`). So a MIDI track's pair stays silent [inferred: cleared, nothing rendered into it]. The
SSI half (channels 0-13) packs one buffer per channel (`0x1c266c`, 14 x 32 samples, slot stride 16); the SPI half
(channels 14-41) one entry pair per 6-byte unit (`0x1c26b2`, 14 x 32, rows of 84 bytes) [verified: the code].

**Proposed layout** (tracks first, as the owner asked; the rest after them costs no extra packet room):

| USB channels | DSP channels | |
|---|---|---|
| 1-32 | 2-33 | track n on 2n-1 (L) and 2n (R) |
| 33-34 | 0-1 | the main mix |
| 35-40 | 34-39 | delay, reverb, chorus returns (L, R each) |
| 41-42 | 40-41 | the inputs |

42 channels in 3-byte subslots (24 bits, the DSP's own): 7 x 42 x 3 = 882 bytes, one high-speed transaction.

## UM01, the first test build (2026-10-31) [verified: built, host test, checks; not yet run on a unit]

Owner: "You can start the test build for usb." UM01 = CFTE + `usb-multi-dn2` 0.1
(`vendor/elekloader/examples/usb-multi-dn2/`, `scripts/um01_build.py`, guide `out/UM01/FLASH-UM01.md`).

- **What the stock does in USB AUDIO mode** [verified, decompile and disassembly]: the block handler `0x40025e36`
  builds a stereo buffer (`0x4664c074`) from USB OUT's pair and calls `0x40003d00` (`0x40027772`; the silent path
  `0x400279d4`), which packs 8-byte frames into 16 x 56-byte packets at `0x80009000`, a dTD each (`0x400036c0`, the TX
  pool `0x80008000-0x80008800`) primed on endpoint index 7 (`0x40005a86`); 5-7 frames a packet (`0x40287a8c`, the 16.16
  rate `0x40287a84` re-estimated every 1,024 packets by `0x400036fc`). The stream's start (`0x40003b32`, called from
  SET_INTERFACE `0x4000655e`) primes 11 silent packets of 6 frames. The receive side (audio from the computer):
  dTDs at `0x80008800`, 16 x 56-byte buffers at `0x80009380-0x80009700`. EP 0x83's dQH capabilities are set to
  `0x40380000` (one transaction, 56 bytes) by `0x400040a6` (`0x400040e6`). Overbridge's ring (`0x80008c00`, 16 x 1,012
  bytes, to `0x8000cb40`) is used only in mode 6.
- **The mod**: `um_send` / `um_mute` in place of `0x40003d00` at both calls, `um_start` in place of `0x40003b32`; the
  stock sender's state words kept; frames of 42 x 3 bytes (126) read straight from the SSI half (picked as the handler
  picks it, from `0xfc045640`) and the SPI reply, in the order of part 2's layout; a ring of 15 x 882 bytes at
  `0x80009700-0x8000cb36` (mode 5's free span); the dQH word `0x43720000`; the descriptors (IT 5 and AS interface 2:
  42 channels, 3-byte 24-bit subslots, EP 0x83 882 bytes) **written at run time**, not in the stored image: the
  first UM01 patched the four values in place and the brick checker refused it (check 6: they lie in
  `0x402f7d40-0x4030c980`, the tail after the panel MCU firmware that the checker keeps byte for byte; the panel
  firmware itself, `0x402f7d40-0x402fb494`, was unchanged). The descriptors live in the main OS's data in RAM, and
  the stock `0x4000677a` already writes into them at every USB start (each configuration's wTotalLength and
  string lengths), each byte followed by `cpushl dc` for the controller's DMA. So `0x40006880` (its last write,
  push and rts) jumps to `um_desc`, which does that write and then ours the same way (compiled to the stock's own
  `move.b #imm,(a0); cpushl dc,(a0)`, `f468`); the stored image keeps Elektron's bytes there. 5 sites.
- **Checks**: the sender's C on the Mac with stubs (`scripts/um01/test_um.c`, also under ASan/UBSan): the start's 11
  packets of 756 zero bytes, 1,606 packets of 5, 6 and 7 frames at three rates, all inside the ring, every one of
  9,592 frames with each of the 42 channels the right DSP channel's sample, 24-bit little-endian; the skip count, the
  silent path, the mute flag, nothing sent when the host isn't listening. The build: CFTE's thirteen pinned mods +
  usb-multi, TE01's DSP, the container checks of a pack; against CFTE the main OS differs only inside usb-multi's 8
  sites.
- **Only the unit can show**: enumeration with 42 channels on macOS and iPadOS, the stream itself, the CPU time of the
  packing inside the block handler, what a MIDI track's pair carries.

## UM01 on the unit, and UM02 (2026-10-31)

**UM01 on the owner's unit:** the Mac sees the 42 inputs and the tracks arrive, but with USB AUDIO/MIDI streaming
the sequencer lags so much (playback and live recording) that the unit is unusable.

**Why [corrected 2026-10-31 evening; verified in the code, the cost estimated]:** the SSI DMA buffers are uncached.
The main OS's start-up at `0x40000554` (1.11 and 1.12, the same bytes) writes CACR `0xa50ce100` (data cache on, by
default cache-inhibited precise, store buffer on) and ACR0 `0x4007e020` (`0x40000000-0x47ffffff` cacheable,
copyback), over the bootstrap's `0x000cc100`. So DDR below `0x48000000` (the main OS's data, our mods at
`0x47e00000`) is cached, and everything else, the SSI DMA buffers at `0x4e6e7100` among them, is not: every read
there goes to memory. The on-chip SRAM (`0x80000000-0x8000ffff`: the parameter mirror, the USB rings, the SPI reply)
is fast. usb-multi 0.1's frame packer read the 14 SSI channels byte by byte (42 uncached reads a frame, 1,344 an
audio block, inside the block handler at level 5, which takes the CPU from the sequencer at level 2 and the UI).
*Earlier today this paragraph said the whole OS ran with the data cache off: only the bootstrap's CACR had been
checked, and the main OS's `movec` at `0x4000055a` was missed. The table reads UM01 also did were cached.*

**UM02 (usb-multi 0.2):** the same stream, byte for byte (`scripts/um01/test_um.c` passes unchanged), packed with 14
uncached reads a frame: each SSI slot once as a 32-bit word, the channel order as code (no table), the SPI row and the
ring in SRAM. Byte stores only (GCC had merged two into a 16-bit store at an odd address). `out/UM02/UM02.syx`
`780be2d3...`; guide `out/UM02/FLASH-UM02.md`. If UM02 still lags, next: a build that measures the sender's time
(a timer read at its entry and exit), then cheaper ways to read the SSI slots.

**UM02 on the unit (2026-10-31): no lag.** The owner: "um02 seems to handle anything without lag! even busy sequences
with chords and more involved synthesis." So the frame packer's DDR reads were the cost, and 14 a frame fit.
The owner, later: "it all seems to work, 42 channels, 48khz, no lag, i got all 16 tracks playing here."

## UM03: the 42 channels as their own USB CONFIG choice (2026-10-31)

Owner: "let's look into adding a menu option for 0.3.0 it should not be hard ... so we ship it as an option". UM03 =
CFTE + `usb-multi-dn2` 0.3 (`scripts/um03_build.py`). USB AUDIO/MIDI is Elektron's stereo mode again; a fourth choice,
**USB MULTI/MIDI**, is UM02's 42-channel stream.

**The stock menu [verified, disassembly]:** SETTINGS > SYSTEM > USB CONFIG opens the USB CFG list, built by
`0x40104f42` (ctor `0x40105310`): three items, each `new(0x54)` + ctor `0x401173b8(item, &name, &yes, &box, &turn, -1,
8)` from four std::function (16 bytes here: the lambda's heap pointer, a word, manager, invoker), handed to the menu
by its vtable's third entry (+8), then destroyed (`0x40188366`). If USB is busy (`0x400f2e54`) it shows THE USB CONFIG
IS CURRENTLY BEING HANDLED... instead. The choices store USB CONFIG through `0x400bb7ba` (clamped 0-2) into the
saved-settings byte `0x405ce898` (0 USB MIDI, 1 OVERBRIDGE, 2 USB AUDIO/MIDI; `0x400bb65c` copies and re-checksums)
and switch the USB mode with `0x40006c16(mode)` (2, 6, 5), which returns at once when the mode is already that one;
otherwise stop `0x40006bac`, the mode `0x4058f2a8` and its table `0x40287af0 + 40 x mode` (mode 5's first entry is
`0x400040a6`, the endpoints with the dQH word), start `0x40006bc6` (after a 30-tick delay). Every reader of USB
CONFIG goes through `0x400bb798`, which clamps it to 0-2, **except the start-up** `0x400cf216`: 1 -> mode 6, 2 -> mode
5, anything else -> mode 2. The descriptors' run-time values are written **once**, at the USB start-up
(`0x4000677a` from `0x40006c52`), not at each start as UM01's note said.

**usb-multi 0.3:** USB MULTI/MIDI is stored as 3: every stock reader sees 2 (USB AUDIO/MIDI, mode 5), so the whole
stock mode-5 path runs as before; `um_on` (latched with the descriptors) picks our stream or the stock sender.
Sites (9): the three sender calls and `0x40006880` as before, each falling back to the stock with `um_on` off; the dQH
word a `jsr um_dqh` (56 or 882 bytes); the start-up's `bne` -> `bgt` (`0x400cf22c`: 2 and above start mode 5); the
menu's fourth item after the third (`0x40105220` jsr `um_menu_hook` -> `um_menu_add`; its invokers in `um_stubs.s`,
USB AUDIO/MIDI's own instruction for instruction with our name, choice and box; its managers reused);
USB AUDIO/MIDI's box reads `um_cfg_get` (0-3, so a 3 does not tick it, `0x40104d54`); USB AUDIO/MIDI's mode switch
goes to `um_mode_set` (`0x40104e02`), which, when only the descriptors change (mode 5 to mode 5), stops USB, writes
them and starts it again, so the computer sees the device leave and come back.

**On Elektron's OS** a saved 3 starts USB MIDI at power-on while the menu ticks USB AUDIO/MIDI; picking any USB
CONFIG choice sets it right. Transfer works in USB MIDI.

**Checks [verified]:** `scripts/um01/test_um.c` (also ASan/UBSan): UM02's stream checks with the choice on; with it
off, the stock sender and start with their arguments and nothing of ours; `um_desc` for 3 and for -1, 0-2, 4-7;
`um_mode_set` in 8 cases (stereo <-> ours in mode 5: stop, descriptors, start; from another mode: descriptors then the
stock switch; otherwise the stock call alone); `um_choose` (3 saved through the stock's setter, then the restart);
`um_cfg_get`; `um_menu_add` (the four std::function, the item, the menu's call, the four destroyed). The build
(`out/UM03/UM03.syx` `55fbd95f...`, brick checker ACCEPT 12/12, `out/brickcheck/UM03-2026-10-31`): against CFTE the main OS differs only inside usb-multi's 9 sites; the compiled
`um_menu_add` lays the four std::function out as the stock does (16 bytes from sp+24) and calls the item ctor, the
menu and the destructors in the stock's order.

**In the emulator [verified, `scripts/um03-emu.sh`, `out/UM03/emu/`]:** the USB CFG list shows the four choices, the
cursor reaches USB MULTI/MIDI (`um03-usb/02-usbcfg.png`, `03-usbcfg-row4.png`); with `0x405ce898` poked to 3 only
USB MULTI/MIDI is ticked, to 2 only USB AUDIO/MIDI (`um03-ticks/`, `um03-ticks2/`; `scripts/t19_live.py --poke`,
new); YES on USB MULTI/MIDI from USB MIDI: um_choose -> um_mode_set(5) -> 0x40006c16(5) -> 0x40006bac -> 0x40005dcc
(traced), USB CONFIG 3, `um_on` 1, the descriptors 42 / 42 / 3, 24 / 882 in RAM. The emulator has no USB controller:
the stock stop `0x40005dcc` does not return there, and YES on USB AUDIO/MIDI stops the same way on plain CFTE
(`out/CFTE/CFTE.syx`, the same script), so the switch and the stream are the unit's to show.

## On the unit, and UM04 (2026-11-01 to 11-03)

**usb-multi 0.3 on the unit [unit]:** the owner, on 0.3.0's first CFTE (UM03's main OS): "CFTE ok, 42 channels show
up!" (1 Nov); then "I have done the USB testing using AUM on my iPhone and the iPhone picks back up on the new USB
signals very quickly. It just detects that it's either USB LR or 1+2 and so on" (3 Nov). Then: "i had a crash when
going to upgrade fw in the digitone, after the usb audio tests i did. never had that. i had disconnected it from my
phone and into my computer. maybe that's related. not sure how to reproduce it. CFTE" (3 Nov). Asked: what the crash
looked like, at which point of the transfer, which USB CONFIG, whether the upgrade worked afterwards. Until then:
USB MIDI for transfers (only the stock's USB code runs there: um_desc writes the stock's values, nothing else of ours
is reached outside mode 5).

**Two bugs in 0.3's sender, found looking for it [verified: the decompile, and the host test under ASan]:**
- `IDX` (`0x4058eea0`) is the stock sender's: it counts 16 slots of 56 bytes (`0x40003d00`: `0xf < IDX -> 0`), 0.3's
  ring has 15 of 882. After the stock's stereo stream it can be 15, and 0.3's `um_next` then hands out slot 15,
  `0x8000caae`: um_start zeroes 756 bytes there, a packet writes up to 882 (to `0x8000ce20`). 0.3's test against
  0.3's code with `IDX` 15: ASan "global-buffer-overflow ... WRITE of size 756 ... 0 bytes after 'ring_mem'". What
  lies there: from `0x8000cb40`, 16 x 256-byte receive buffers of endpoint index 3 (`0x40002eac`, buffer
  `0x8000cb40 + 0x100 i`, dTDs at `0x80008800`), which follow Overbridge's ring (`0x80008c00` + 16 x 1,012 =
  `0x8000cb40`) and are, as far as the callers show (`0x400030b0` from the control-request handler, `0x40003376` from
  the block handler), Overbridge's audio from the computer, mode 6 only [inferred]; mode 5's own receive buffers are
  `0x80009380` (`0x40003896`). So in USB MULTI/MIDI it most likely lands in memory nothing uses then; USB MIDI's
  buffers come through pointers, not this range.
- `TARGET` (`0x40287a8c`, frames in the next packet) comes from the stock's rate (`0x400036fc` reads a 4-entry table
  that the SOF-timed `0x40003732` fills: SSI DMA bytes over USB frames, `(bf08 - ee6c) & 0x1fff`, low-pass 82/4096
  per measurement, the first one after a reset taken as it is). Nothing bounds it to 5-7: a wrong estimate (one way:
  a measurement interval longer than the 13-bit frame count's wrap) makes packets bigger than a slot. The stock's
  8-byte frames spill a little into its own receive buffers; 0.3's 126-byte frames would run past the ring, into the
  buffers above and, with a very wrong rate, into the block handler's state at `0x8000dd40`. [inferred: no such
  estimate observed]
- Neither is shown to be the owner's crash.

**Two more crashes, and a likely mechanism (3 Nov):** the owner: "CEES crashes :(", then "i cant get it to crash
again. weird !", then "EFES also crashed, after a while of not playing. suspecting the usb multi audio..." (both
from `out/device-tests-0.3/`, usb-multi 0.3). How the stock's rate gets wrong [verified: the decompile; inferred:
that it happens]: the USB interrupt `0x40005e10` latches USBSTS and, when the start-of-frame bit is among them, runs
the start-of-frame list `0x4058f34c`, which mode 5's setup `0x400040a6` gives `0x40003732` (`0x4000698a`, the rate
table reset to `0x60000`, 6.0, there too). `0x40003732` switches the start-of-frame interrupt (USBINTR bit 7) on, takes
a measurement at the next one and switches it off again; only it and the list's registration switch it on. So between
measurements it runs only when some other USB interrupt comes: a dTD with IOC (one with a callback: the receive side's,
the start's first packet; the IN packets have none), MIDI, a control request. With the Digitone only streaming to a
host that sends nothing, that can be seconds or minutes. Its microframe count is FRINDEX's low 13 bits
(`& 0x1fff`), the timer's (`DTIM2_DTCN`) is not: across a pause longer than 8,192 microframes (1.024 s) the
microframes count short and the rate comes out too high, up to hundreds of times (the first measurement after a
reset unfiltered, later ones moving the estimate by 82/4096 of the error). The stock's packets are 8 bytes a frame in
56-byte slots at `0x80009000`, so even a rate of hundreds of frames a packet spills into its receive buffers and
Overbridge's free ring; 0.3's 126-byte frames would run from its last slot over `0x8000cb40` (Overbridge's receive
buffers), `0x8000dd40` (the block handler's state) and on. That fits "after a while of not playing" and "when going
to upgrade fw" (Transfer's first MIDI after a quiet spell).

**usb-multi 0.4 [verified: built and host-tested]:** `um_next` starts at our slot 0 when `IDX` is not one of ours;
`um_rate` takes the stock's rate only between 5.5 and 6.5 frames a packet (real rates are 6 within a few hundred ppm)
and 6 otherwise, so the packets keep pace with the microframes; `um_target` keeps a packet at 5 to 7 frames (7 x 126 = 882, a slot and wMaxPacketSize; the stock's sane range, so it
changes nothing in normal running); a packet the stock began (`CUR` not one of our slots) or one past its size starts
anew. The host test adds: the start after the stock's stream (`IDX` 15), a packet the stock began in its own ring
(3 frames in), rates of 20 and 0 frames a packet (6-frame packets instead) and a stale target of 40: every packet 5
to 7 frames inside the ring, the stock's ring untouched; all pass, also under ASan/UBSan (0.3's code fails the first under ASan). The
compiled remainder is `remu.l` (the stock's rate code uses `divu.l`). **UM04** = CFTE + usb-multi 0.4
(`scripts/um04_build.py`, `out/UM04/UM04.syx` `c8554b1a...`; a first 0.4 without `um_rate`, `2bde4a50...`, was
built and never flashed): every check as UM03's; against CFTE the main OS differs
only inside usb-multi's 9 sites. Base 0.3 takes usb-multi 0.4 (`scripts/pack_build.py` pins UM04's .elemod): the
fourteen packs rebuilt (`out/packs-0.3.0-build.log`; the usb-multi 0.3 run kept as `out/packs-0.3.0-build-um03.log`),
their DSP sections byte for byte as before.
