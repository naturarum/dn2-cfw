# SPDX-License-Identifier: GPL-2.0-or-later
"""Run the DN2's stock effects (T3.4) block by block on audio, in the SHARC interpreter.

Each effect is called the way the FX bus 0x1c1738 calls it: R4 = state, R8 = parameter
struct, R12 = the I/O descriptor at 0x257e6c = {+0 block length 32, +4 -> input pointer
pair, +0xc -> output pointer pair, +0x14 tempo (BPM, read by the delay)}.
"""
import math
import struct
import sys
import time
import wave
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from vm import VM, dn2_after_init, f2u, u2f  # noqa: E402

DESC = 0x257e6c
BLOCK = 32
SR = 48000

# name: entry, state, params, n param fields, input pair table, output pair table, unity field offset
EFFECTS = {
    "delay":  dict(entry=0xb8482c, state=0x8005912c, params=0x257934, fields=["TIME", "X", "WID", "FDBK", "HPF", "LPF"],
                   inpair=0x25d6e8, outpair=0x25d6d8, unity=0x18),
    "chorus": dict(entry=0xb84d5c, state=0x80459388, params=0x257950, fields=["DPTH", "SPD", "HPF", "WDTH"],
                   inpair=0x25d6c8, outpair=0x25d6d0, unity=0x10),
    "reverb": dict(entry=0xb836bc, state=0x80000018, params=0x257918, fields=["PRE", "DEC", "FREQ", "GAIN", "HPF", "LPF"],
                   inpair=0x25d6f0, outpair=0x25d6f8, unity=0x18),
    # master compressor: input pointer array {in L, in R, side-chain L, side-chain R};
    # SCS (+0x1c) and the routing words (+0x20, +0x24) are integers read by the master
    # stage, not by the compressor; RAT is index/7 (ratio table 0x2cfde0, T3.4 3.4).
    "comp":   dict(entry=0xb8541d, state=0x257ab0, params=0x257964,
                   fields=["SCF", "THR", "RAT", "ATK", "REL", "MUP", "MIX"],
                   inpair=0x25d708, outpair=0x25d718, unity=None, sidechain=True),
}
SCRATCH = 0x300000      # L1 block 2: free in the stock image (T3.2 section 2.3)


class FxRunner:
    def __init__(self, name, vm=None, bpm=120.0):
        self.name = name
        self.e = EFFECTS[name]
        self.vm = vm or dn2_after_init()
        vm = self.vm
        self.inL, self.inR = vm.rd32(self.e["inpair"]), vm.rd32(self.e["inpair"] + 4)
        self.outL, self.outR = vm.rd32(self.e["outpair"]), vm.rd32(self.e["outpair"] + 4)
        vm.wr32(DESC, BLOCK)
        # input pointer array in scratch memory: {in L, in R[, side-chain L, side-chain R]}.
        # The side-chain buffers are separate (scratch L1) so a key signal can be fed;
        # block() copies the input there when no key is given.
        self.scL, self.scR = SCRATCH + 0x100, SCRATCH + 0x180
        ptrs = [self.inL, self.inR] + ([self.scL, self.scR] if self.e.get("sidechain") else [])
        for k, p in enumerate(ptrs):
            vm.wr32(SCRATCH + 4 * k, p)
        vm.wr32(DESC + 4, SCRATCH)
        vm.wr32(DESC + 0xc, self.e["outpair"])
        vm.wrf(DESC + 0x14, [bpm])
        self.insns = 0
        self.secs = 0.0

    def set_params(self, **p):
        e = self.e
        for k, v in p.items():
            i = e["fields"].index(k)
            self.vm.wrf(e["params"] + 4 * i, [float(v)])
        if e["unity"] is not None:
            self.vm.wrf(e["params"] + e["unity"], [1.0])

    def block(self, L, R, keyL=None, keyR=None):
        vm = self.vm
        vm.wrf(self.inL, L)
        vm.wrf(self.inR, R)
        if self.e.get("sidechain"):
            vm.wrf(self.scL, L if keyL is None else keyL)
            vm.wrf(self.scR, R if keyR is None else keyR)
        if self.e["unity"] is not None:
            vm.wrf(self.e["params"] + self.e["unity"], [1.0])
        n0, t0 = vm.icount, time.perf_counter()
        vm.call(self.e["entry"], R4=self.e["state"], R8=self.e["params"], R12=DESC)
        self.insns += vm.icount - n0
        self.secs += time.perf_counter() - t0
        return vm.rdf(self.outL, BLOCK), vm.rdf(self.outR, BLOCK)

    def process(self, L, R):
        outL, outR = [], []
        n = len(L) // BLOCK * BLOCK
        for i in range(0, n, BLOCK):
            a, b = self.block(L[i:i + BLOCK], R[i:i + BLOCK])
            outL += a
            outR += b
        return outL, outR


def write_wav(path, L, R, sr=SR):
    peak = max(1e-9, max(max(abs(x) for x in L), max(abs(x) for x in R)))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(3)
        w.setframerate(sr)
        frames = bytearray()
        for a, b in zip(L, R):
            for x in (a, b):
                v = max(-1.0, min(1.0, x))
                iv = int(round(v * 8388607))
                frames += struct.pack("<i", iv)[:3]
        w.writeframes(bytes(frames))
    return peak


def read_wav(path):
    with wave.open(str(path), "rb") as w:
        n, ch, sw, sr = w.getnframes(), w.getnchannels(), w.getsampwidth(), w.getframerate()
        raw = w.readframes(n)
    vals = []
    for i in range(0, len(raw), sw):
        b = raw[i:i + sw]
        if sw == 2:
            v = struct.unpack("<h", b)[0] / 32768.0
        elif sw == 3:
            v = int.from_bytes(b, "little", signed=True) / 8388608.0
        else:
            v = struct.unpack("<i", b)[0] / 2147483648.0
        vals.append(v)
    L = vals[0::ch]
    R = vals[1::ch] if ch > 1 else L[:]
    return L, R, sr
