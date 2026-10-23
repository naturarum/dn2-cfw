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
