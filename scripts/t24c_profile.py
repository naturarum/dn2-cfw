#!/usr/bin/env python3
"""T2.4c: profile emu.live's worker thread (cProfile) on a headless --script run.

Run from vendor/digikit:
  DT2_SECTIONS=out/sections/dn2-1.12 uv run python ../../scripts/t24c_profile.py OUT.prof SNAP SYX SCRIPT OUTDIR [live args]
Prints the top functions by internal and cumulative time and the run summary. The
worker runs in a thread, so LiveEmulator.run is wrapped in a cProfile.Profile here.
Never touches hardware.
"""
import cProfile, os, pstats, sys, time
sys.path.insert(0, os.getcwd())
import emu.live as live

prof_path, snap, syx, script, out, *rest = sys.argv[1:]
prof = cProfile.Profile()
orig_run = live.LiveEmulator.run

def run(self):
    prof.enable()
    try:
        orig_run(self)
    finally:
        prof.disable()

live.LiveEmulator.run = run
t0 = time.monotonic()
rc = live.main([snap, '--syx', syx, '--script', script, '--out', out, '--speed', '0'] + rest)
print('[t24c] wall %.1f s' % (time.monotonic() - t0))
prof.dump_stats(prof_path)
st = pstats.Stats(prof_path)
st.sort_stats('tottime').print_stats(25)
st.sort_stats('cumulative').print_stats(25)
sys.exit(rc)
