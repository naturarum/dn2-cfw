#!/usr/bin/env python3
"""T2.4c: smoke test of the live window's LED drawing, with the window withdrawn.

Run from vendor/digikit:
  DT2_SECTIONS=out/sections/dn2-1.12 uv run python ../../scripts/t24c_tk_smoke.py SNAP SYX OUTDIR
Holds FUNC (all trig LEDs go green on 1.12), then reads back what each key canvas drew and writes
the TRIG 1 key canvas as PostScript. Never touches hardware.
"""
import os, sys
sys.path.insert(0, os.getcwd())
import emu.live as live

snap, syx, out = sys.argv[1:4]
os.makedirs(out, exist_ok=True)
LiveApp = live._window_classes()
app = LiveApp(snap, syx, live.KeyMap(), live_extra=dict(midi_log=os.path.join(out, 'midi.txt'), midi_bpm=81.0))
app.withdraw()
report = []

def step2():
    c = app.controls
    leds = app.emu.leds
    for code in (17, 25, 26, 40, 21, 2):
        for w in c.led_keys.get(code, []):
            report.append('code %2d %-10s led %s' % (code, app.emu.button_names.get(code), w.led))
    report.append('round: %s' % [c.round_canvas.itemcget(d, 'fill') for d in c.round_dots])
    c.led_keys[25][0].postscript(file=os.path.join(out, 'trig1-key.ps'))
    app.keys.key_up('space')
    app.after(300, app.quit_all)

def step1():
    app.keys.key_down('space')
    app.after(1500, step2)

app.after(2500, step1)
app.mainloop()
print('\n'.join(report))
