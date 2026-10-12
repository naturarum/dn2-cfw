# SPDX-License-Identifier: GPL-2.0-or-later
"""Pure-Python model of the main OS depacker run in place (task T1.2): stream and
output share one memory, so a write past the next unread stream byte changes
what is read. Reports, per file and stage, where the writer first reaches
unread input and where the output first differs from the stock main OS.

    cd vendor/digikit && uv run python ../../scripts/inplace_model.py [FILE.syx ...]

See notes/T1.2-device-oracle.md. The 2^25 gamma guard is the model's own; the
unit's depacker has none.
"""
import os, sys, hashlib
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DK = os.path.join(ROOT, 'vendor', 'digikit')
sys.path.insert(0, DK); sys.path.insert(0, os.path.join(DK, 'tools'))
import acceptoracle as A
want = open(os.path.join(ROOT, 'ele3_out', '02_type3.unpacked.bin'), 'rb').read()
LO = 0x40000000

def run(stream, stage, load, cap=0x800000, maxsteps=50_000_000):
    mem = bytearray(b'\xa5' * 0x600000)
    s0 = stage - LO
    mem[s0:s0 + len(stream)] = stream
    p = s0 + 8                     # read pointer (absolute offset in mem)
    w = load - LO                  # write pointer
    w0 = w
    tag = 0
    first_clobber = None
    def byte():
        nonlocal p
        v = mem[p]; p += 1; return v
    def bit():
        nonlocal tag
        tag = (tag << 1) & 0x1FF
        if tag & 0xFF == 0:
            b = byte(); tag = (b << 1) | 1; return b >> 7
        return tag >> 8
    def gamma():
        v = 1
        while True:
            v = (v << 1) | bit()
            if bit(): return v
            if v > 0x02000000: raise ValueError('gamma overflow')
    last = 1
    steps = 0
    while True:
        steps += 1
        if steps > maxsteps or w - w0 > cap:
            return bytes(mem[w0:w0 + len(want)]), 'runaway', first_clobber, w - w0, p - s0
        if bit():
            if first_clobber is None and w >= p: first_clobber = (w, p)
            mem[w] = byte(); w += 1; continue
        try:
            g = gamma()
        except ValueError:
            return bytes(mem[w0:w0 + len(want)]), 'gamma overflow (the device code has no such guard)', first_clobber, w - w0, p - s0
        if g == 2:
            off = last
        else:
            raw = ((g << 8) + byte()) & 0xFFFFFFFF
            if raw == 767:
                return bytes(mem[w0:w0 + len(want)]), 'end', first_clobber, w - w0, p - s0
            off = (raw - 767) & 0xFFFFFFFF; last = off
        short = 2 * bit() + bit()
        try:
            n = short if short else gamma() + 2
        except ValueError:
            return bytes(mem[w0:w0 + len(want)]), 'gamma overflow (the device code has no such guard)', first_clobber, w - w0, p - s0
        if off > 3328: n += 1
        for _ in range(n + 1):
            if first_clobber is None and w >= p: first_clobber = (w, p)
            if w >= len(mem): return bytes(mem[w0:w0 + len(want)]), 'ran off memory', first_clobber, w - w0, p - s0
            mem[w] = mem[w - off]; w += 1

files = sys.argv[1:] or [os.path.join(ROOT, 'elektron-firmware', 'Digitone_II_OS1_12.syx'), os.path.join(ROOT, 'out', 'T1.2', 'recomp.syx')]
for path in files:
    c = A.parse(path); st = A.stored(c, 3)
    for stage in (0x40400000, 0x40200000):
        out, how, fc, nout, nin = run(st, stage, 0x40000400)
        diff = next((i for i, (x, y) in enumerate(zip(out, want)) if x != y), None)
        print(path.split('/')[-1], hex(stage), how, 'out', nout, 'in', nin,
              'first write over unread input', (hex(LO + fc[0]), hex(LO + fc[1])) if fc else None,
              'match', out == want, 'first diff', hex(0x40000400 + diff) if diff is not None else None,
              'sha', hashlib.sha256(out).hexdigest()[:16])
