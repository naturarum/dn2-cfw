#!/usr/bin/env python3
"""T6.0 proof of concept, DSP side, in the T3.6 SHARC interpreter (tools/sharcvm).

    python3 scripts/t60_dsp_poc.py [--wire out/T6.0/cf/main720M-poc.wire.bin] [--blocks 300]

Input: the 0xabc-byte wire frame scripts/t60_cf_frame.py --poc rebuilt from the ColdFire's PUSHR
entries (test values in the 30 tail words, put there by 68k test code in the emulator).

1. The frame goes into the DSP's SPI2 receive buffer exactly as the DMA would leave it (16-bit
   words at the same byte offsets) and the stock block handler 0x1c9d6b runs on it, block after
   block, with noise on the codec input. The same run is repeated with the tail zeroed: the
   outputs must be bit-identical (the stock DSP code ignores the tail; the read census in
   scripts/t60_dsp_reads.py shows it never reads it).
2. After each block, new DSP code (scripts/t60_probe.c, built with selcc, linked by
   scripts/t60_probe.ldf into free L1 at 0x2e2000) is called with the receive buffer the handler
   used and converts the 30 tail words to floats (x 1/32512, the stock scale). They must equal
   the test values exactly.
Writes out/T6.0/dsp/poc.json. No hardware.
"""
import argparse
import json
import random
import struct
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools/sharcvm"))
import fullblock as FB  # noqa: E402

OUT = ROOT / "out/T6.0/dsp"
SEL = ROOT / "vendor/selache/target/release"
PROBE_OUT = 0x2e6800        # float[30], in the probe's free data region


def build_probe():
    OUT.mkdir(parents=True, exist_ok=True)
    s, o, x = OUT / "t60_probe.s", OUT / "t60_probe.doj", OUT / "t60_probe.dxe"
    subprocess.check_call([SEL / "selcc", "-proc", "ADSP-21569", "-char-size-8", "-O1", "-S", "-o", s,
                           ROOT / "scripts/t60_probe.c"])
    subprocess.check_call([SEL / "selas", "-proc", "ADSP-21569", "-o", o, s])
    subprocess.check_call([SEL / "seld", "-proc", "ADSP-21569", "-T", ROOT / "scripts/t60_probe.ldf", "-o", x, o])
    return x


def wire_to_rx(wire):
    """Wire order is big-endian 16-bit words; the DSP stores each received word as a 16-bit
    little-endian value at the same byte offset (T3.4, tools/sharcvm/fullblock.py)."""
    return b"".join(wire[i:i + 2][::-1] for i in range(0, len(wire), 2))


def run(frame, blocks, seed, probe=None):
    d = FB.FullDSP()
    vm = d.vm
    entry = None
    if probe:
        # the probe's regions must be untouched by the DN2 image (zero) before loading it
        assert vm.read(0x2e2000, 0x5000) == bytes(0x5000), "0x2e2000.. not free in the DN2 image"
        e = vm.load_elf(probe)
        entry = e.symbols["t60_probe."]
    rnd = random.Random(seed)
    outs, probes = [], []
    for b in range(blocks):
        spi = vm.rd32(FB.SPI_HALF)
        rx = FB.RX_BASE + 0x1000 * spi
        vm.write(rx, frame)
        h = vm.rd32(FB.DMA_HALF)
        L = [rnd.uniform(-0.4, 0.4) for _ in range(32)]
        R = [rnd.uniform(-0.4, 0.4) for _ in range(32)]
        vm.write(FB.CODEC_IN + 0x100 * h, struct.pack("<64I", *[FB.q31(v) for pair in zip(L, R) for v in pair]))
        vm.call(FB.HANDLER)
        outs.append(vm.read(FB.OUT_FLOATS, 0x100))
        if entry is not None:
            n0 = vm.icount
            vm.call(entry, R4=rx, R8=PROBE_OUT)
            probes.append((vm.rdf(PROBE_OUT, 30), vm.icount - n0))
        vm.wr32(FB.SPI_HALF, spi ^ (b & 1))   # alternate the SPI half now and then, as the DMA would
    return outs, probes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wire", default=str(ROOT / "out/T6.0/cf/main720M-poc.wire.bin"))
    ap.add_argument("--blocks", type=int, default=300)
    a = ap.parse_args()
    wire = Path(a.wire).read_bytes()
    assert len(wire) == 0xabc, len(wire)
    tail_words = struct.unpack(">30H", wire[0xa80:])
    frame = wire_to_rx(wire)
    frame0 = frame[:0xa80] + bytes(0x3c)
    probe = build_probe()

    out_t, probes = run(frame, a.blocks, 6, probe)
    out_0, _ = run(frame0, a.blocks, 6, None)
    identical = out_t == out_0
    f32 = lambda x: struct.unpack("<f", struct.pack("<f", x))[0]   # noqa: E731
    scale = f32(1.0 / 32512.0)        # 0x38010204, the constant selcc and the stock unpacker use
    want = [f32(float(w) * scale) for w in tail_words]   # one IEEE single multiply
    got = probes[-1][0]
    exact = got == want
    res = {
        "wire": a.wire, "blocks": a.blocks,
        "tail_words": ["%#06x" % w for w in tail_words],
        "stock_output_identical_with_and_without_tail": identical,
        "probe_floats_last_block": got,
        "expected_floats": want,
        "probe_exact_every_block": all(p[0] == got for p in probes) and exact,
        "probe_instructions_per_call": probes[-1][1],
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "poc.json").write_text(json.dumps(res, indent=1))
    print(f"tail words from the ColdFire: {res['tail_words'][:6]} ...")
    print(f"stock block handler output identical with and without the tail ({a.blocks} blocks): {identical}")
    print(f"probe floats: {[round(g, 6) for g in got[:6]]} ... expected {[round(w, 6) for w in want[:6]]} ...")
    print(f"probe exact every block: {res['probe_exact_every_block']}; {res['probe_instructions_per_call']} "
          f"instructions per call")


if __name__ == "__main__":
    main()
