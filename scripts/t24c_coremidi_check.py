#!/usr/bin/env python3
"""T2.4c: check the live virtual CoreMIDI output of emu.live (--midi-port) end to end.

Run from vendor/digikit:
  DT2_SECTIONS=out/sections/dn2-1.12 uv run --with python-rtmidi python ../../scripts/t24c_coremidi_check.py SNAP SYX SCRIPT OUTDIR
Starts the emulator with a virtual output port, opens ONLY that virtual port (matched by its exact
name) with an rtmidi input in this process, runs SCRIPT, and prints what arrived with wall-clock
times next to the emulator's own DIN log. Never opens a hardware MIDI device.
"""
import os, sys, time
sys.path.insert(0, os.getcwd())
import rtmidi
import emu.live as live
from emu.midi import describe

snap, syx, script, out = sys.argv[1:5]
NAME = 'Digitone II (emulator) T2.4c check'
os.makedirs(out, exist_ok=True)
emu = live.LiveEmulator(snap, syx=syx, speed=1.0, ssi0_hz=96000, ips=150e6, midi_port=NAME,
                        midi_log=os.path.join(out, 'midi.txt'))
emu.start(); emu.ready.wait()
while emu.raw is None:
    time.sleep(0.01)
inp = rtmidi.MidiIn()
inp.ignore_types(sysex=False, timing=False, active_sense=False)
names = inp.get_ports()
idx = [i for i, n in enumerate(names) if n == NAME or n.endswith(NAME)]
if len(idx) != 1:
    raise SystemExit('virtual port not found exactly once: %r' % names)
inp.open_port(idx[0])
got = []
t0 = time.monotonic()
inp.set_callback(lambda ev, data=None: got.append((time.monotonic() - t0, bytes(ev[0]))))
with open(script) as fh:
    live.run_script(emu, live.KeyMap(), fh.readlines(), out)
time.sleep(0.5)
emu.stop(); emu.join(30)
inp.close_port()
print('received on the virtual port: %d messages' % len(got))
notes = [(t, m) for t, m in got if m[0] & 0xF0 == 0x90]
for t, m in [g for g in got if g[1][0] in (0xFA, 0xFC)] + notes:
    print('  wall %.4f  %s  %s' % (t, m.hex(), describe(m)))
clocks = [t for t, m in got if m[0] == 0xF8]
if len(clocks) > 2:
    print('clock on the port: %d, %.2f/s wall' % (len(clocks), (len(clocks) - 1) / (clocks[-1] - clocks[0])))
print('emulator sent %d DIN bytes' % len(emu.midi.log))
