#!/usr/bin/env python3
"""Where the instructions of one DSP block go (whole block handler, no voices playing).

Runs the block handler 0x1c9d6b to successive stop points at the render's stage call sites
(T3.4 section 2) and reports instruction counts between them, averaged over N blocks.
    python3 tools/sharcvm/tests/block_profile.py [--blocks 50]
"""
import argparse, math, struct, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent)); sys.dont_write_bytecode = True
from fullblock import FullDSP, HANDLER, RX_BASE, SPI_HALF, DMA_HALF, CODEC_IN, q31  # noqa: E402
from vm import STOP_PC, ST_STOP  # noqa: E402

POINTS = [  # (pc, label of the stretch that ENDS here); return addresses = pushed constant + 1
    (0x1c9fbc, "handler before the render (frame/half bookkeeping, input de-interleave)"),
    (0x1c3044, "render: frame copy and unpack before the voices"),
    (0x1c304d, "voices 0x1c8ef1 (16 slots, none triggered), call inclusive"),
    (0x1c3054, "render between voices and FX bus"),
    (0x1c1be1, "FX bus: 16 rows x 3 sends, up to the chorus call"),
    (0x1c1be8, "chorus 0xb84d5c, call inclusive"),
    (0x1c1c58, "FX bus between chorus and delay (cross-sends)"),
    (0x1c1c5f, "delay 0xb8482c, call inclusive"),
    (0x1c1d11, "FX bus between delay and reverb"),
    (0x1c1d18, "reverb 0xb836bc, call inclusive"),
    (0x1c305b, "FX bus rest (returns, external input)"),
    (0x1c305d, "render between FX bus and master"),
    (0x1c215c, "master before the compressor (routing sums)"),
    (0x1c2163, "compressor 0xb8541d, call inclusive"),
    (0x1c3064, "master after the compressor (overdrive chain, pattern volume)"),
    (0x1c9fc3, "render tail (frame image copy)"),
    (STOP_PC, "handler after the render (Q31 conversion, negate, cycle count)"),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--blocks", type=int, default=50)
    a = ap.parse_args()
    d = FullDSP()
    d.set(IN_L=40, IN_R=40, IN_L_PAN=64, IN_R_PAN=64, PATTERN_VOL=127, IN_REV_L=80, IN_REV_R=80, REV_DEC=90,
          REV_VOL=100, IN_DEL_L=50, IN_DEL_R=50, DEL_TIME=32, DEL_FDBK=40, DEL_VOL=90, CHR_VOL=64, IN_CHR_L=40)
    d.set_raw(TEMPO=14400)
    vm = d.vm
    sums = [0] * len(POINTS)
    for b in range(a.blocks):
        L = [0.2 * math.sin(2 * math.pi * 440 * (b * 32 + i) / 48000) for i in range(32)]
        vm.write(RX_BASE + 0x1000 * vm.rd32(SPI_HALF), bytes(d.frame))
        vm.write(CODEC_IN + 0x100 * vm.rd32(DMA_HALF), struct.pack("<64I", *[q31(v) for v in L for _ in (0, 1)]))
        i6, i7 = vm.reg("I6"), vm.reg("I7")
        vm.set_reg("R2", i6); vm.set_reg("I6", i7); vm.wr32(i7, i6); vm.wr32(i7 - 4, STOP_PC - 1)
        vm.set_reg("I7", i7 - 8); vm.pc = HANDLER
        last = vm.icount
        for k, (pc, _) in enumerate(POINTS):
            st = vm.run(10**8, stop=pc)
            if st != ST_STOP:
                sys.exit(f"did not reach {pc:#x}")
            sums[k] += vm.icount - last
            last = vm.icount
    total = sum(sums) / a.blocks
    print(f"{a.blocks} blocks, {total:.0f} instructions per block")
    for (pc, label), s in zip(POINTS, sums):
        v = s / a.blocks
        print(f"  {v:9.0f}  {100 * v / total:5.1f}%  {label}")


if __name__ == "__main__":
    main()
