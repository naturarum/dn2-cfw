#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""T2.4c: which guest code runs while the sequencer plays (block histogram, weighted by block size).

Run from vendor/digikit:
  DT2_SECTIONS=out/sections/dn2-1.12 uv run python ../../scripts/t24c_hotblocks.py SNAP SYX SCRIPT OUTDIR [live args]
Counts every basic block the fast stepper sees once the script's first 'tap' has happened, and
prints the top blocks and functions (from out/decomp/main_os/functions.tsv). Never touches hardware.
"""
import bisect, collections, os, sys
sys.path.insert(0, os.getcwd())
import emu.longrun as lr
import emu.live as live

hist = collections.Counter()
on = {'v': False}
orig = lr._FastStepper._on_block

def on_block(self, uc, addr, size, data):
    if on['v'] and self.left > 0:
        hist[addr] += size
    return orig(self, uc, addr, size, data)

lr._FastStepper._on_block = on_block
lr._FastStepper.NATIVE = False   # the histogram needs the Python block hook
orig_script = live.run_script

def run_script(emu, keymap, lines, outdir, log=print):
    def log2(msg):
        log(msg)
        if 'tap' in msg:
            on['v'] = True
    return orig_script(emu, keymap, lines, outdir, log=log2)

live.run_script = run_script
snap, syx, script, out, *rest = sys.argv[1:]
rc = live.main([snap, '--syx', syx, '--script', script, '--out', out, '--speed', '0'] + rest)
funcs = []
fpath = os.path.join(os.path.dirname(__file__), '..', 'out', 'decomp', 'main_os', 'functions.tsv')
for line in open(fpath):
    p = line.split('\t')
    if p[0].startswith('0x'):
        funcs.append((int(p[0], 16), p[1]))
funcs.sort()
starts = [f[0] for f in funcs]
def fn(a):
    i = bisect.bisect_right(starts, a) - 1
    return funcs[i][1] if i >= 0 else '?'
total = sum(hist.values())
print('[hot] bytes of code executed (block size weighted): %d' % total)
byf = collections.Counter()
for a, n in hist.items():
    byf[fn(a)] += n
for name, n in byf.most_common(30):
    print('[hot] %5.1f%%  %s' % (100.0 * n / total, name))
for a, n in hist.most_common(20):
    print('[hot] block %08x %5.1f%%  %s' % (a, 100.0 * n / total, fn(a)))
sys.exit(rc)
