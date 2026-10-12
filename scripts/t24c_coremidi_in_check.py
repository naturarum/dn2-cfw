#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""T2.4c: check emu.live's virtual MIDI input (--midi-in) end to end.

Run from vendor/digikit:
  DT2_SECTIONS=out/sections/dn2-1.12 uv run --with python-rtmidi python ../../scripts/t24c_coremidi_in_check.py SNAP SYX OUTDIR
Starts the emulator with a virtual input port, opens ONLY that virtual port (exact name) with an
rtmidi output in this process, sends start + 48 clocks/s for 2.5 s + stop, and saves the LEDs at
three points. Never opens a hardware MIDI device.
"""
import os, sys, time
sys.path.insert(0, os.getcwd())
import rtmidi
import emu.live as live

snap, syx, out = sys.argv[1:4]
NAME = 'Digitone II (emulator) T2.4c in-check'
os.makedirs(out, exist_ok=True)
emu = live.LiveEmulator(snap, syx=syx, speed=1.0, ssi0_hz=96000, ips=150e6, midi_in_port=NAME)
emu.start(); emu.ready.wait()
while emu.raw is None:
    time.sleep(0.01)
time.sleep(0.5)
port = rtmidi.MidiOut()
names = port.get_ports()
idx = [i for i, n in enumerate(names) if n == NAME or n.endswith(NAME)]
if len(idx) != 1:
    raise SystemExit('virtual port not found exactly once: %r' % names)
port.open_port(idx[0])
live.save_png(emu, os.path.join(out, '0-before.png'))
port.send_message([0xFA])
t0 = time.monotonic()
n = 0
while time.monotonic() - t0 < 2.5:
    port.send_message([0xF8]); n += 1
    time.sleep(1 / 48)
live.save_png(emu, os.path.join(out, '1-running.png'))
port.send_message([0xFC])
time.sleep(1.0)
live.save_png(emu, os.path.join(out, '2-stopped.png'))
port.close_port()
emu.stop(); emu.join(30)
print('sent FA + %d F8 + FC; emulator received %d bytes on DIN in, vector 156 x%d'
      % (n, emu.midi.rx_bytes, emu.midi.taken[156]))
for name in ('0-before', '1-running', '2-stopped'):
    print('==', name)
    print(''.join(l for l in open(os.path.join(out, name + '.leds.txt')) if 'round' not in l), end='')
