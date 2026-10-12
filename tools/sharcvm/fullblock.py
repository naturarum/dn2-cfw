# SPDX-License-Identifier: GPL-2.0-or-later
"""Run the DN2's whole DSP block handler 0x1c9d6b offline (T3.2 section 3.5 (b)).

Per block: write the ColdFire frame (T3.3 layout, command word 3) into the SPI receive
buffer, write codec input (interleaved Q31, 32 frames x 2) into the SPORT4B buffer of the
current DMA half, call the block handler, and read the 32 L + 32 R floats it leaves at
0x268438 (before conversion to negated Q31). No peripherals, RTOS or interrupts.

The frame words used here (T3.3 / T3.4 numbering; knob values are sent as v << 8):
  0x9fa.. chorus, 0xa08.. delay, 0xa1a.. reverb, 0xa2a/0xa2c IN L/R level, 0xa2e/0xa30
  IN pans (bipolar), 0xa32.. IN -> chorus/delay/reverb sends, 0xa40.. compressor,
  0xa50 master overdrive, 0xa52 pattern volume, 0xd4/0xd6 compressor routing, 0xd8 tempo.
"""
import struct
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from vm import dn2_after_init  # noqa: E402

HANDLER = 0x1c9d6b
RX_BASE = 0x2c29d0          # + 0x1000 * DM(0x2c0450)
TX_BASE = 0x2c49d0
SPI_HALF = 0x2c0450
DMA_HALF = 0x268a38
CODEC_IN = 0x2c0678         # + 0x100 * half: 32 frames x (L, R) Q31
CODEC_OUT = 0x2c0478
OUT_FLOATS = 0x268438       # 32 L, then 32 R at 0x2684b8
FRAME_LEN = 0xabc

FRAME_WORDS = {
    # chorus
    "CHR_DPTH": 0x9fa, "CHR_SPD": 0x9fc, "CHR_HPF": 0x9fe, "CHR_WDTH": 0xa00, "CHR_DEL": 0xa02,
    "CHR_REV": 0xa04, "CHR_VOL": 0xa06,
    # delay
    "DEL_TIME": 0xa08, "DEL_X": 0xa0a, "DEL_WID": 0xa0c, "DEL_FDBK": 0xa0e, "DEL_HPF": 0xa10,
    "DEL_LPF": 0xa12, "DEL_REV": 0xa14, "DEL_VOL": 0xa16,
    # reverb
    "REV_PRE": 0xa1a, "REV_DEC": 0xa1c, "REV_FREQ": 0xa1e, "REV_GAIN": 0xa20, "REV_HPF": 0xa22,
    "REV_LPF": 0xa24, "REV_VOL": 0xa26,
    # external input
    "IN_L": 0xa2a, "IN_R": 0xa2c, "IN_L_PAN": 0xa2e, "IN_R_PAN": 0xa30, "IN_CHR_L": 0xa32,
    "IN_CHR_R": 0xa34, "IN_DEL_L": 0xa36, "IN_DEL_R": 0xa38, "IN_REV_L": 0xa3a, "IN_REV_R": 0xa3c,
    "DUAL": 0xa3e,
    # compressor and master
    "CMP_THR": 0xa40, "CMP_ATK": 0xa42, "CMP_REL": 0xa44, "CMP_MUP": 0xa46, "CMP_RAT": 0xa48,
    "CMP_SCS": 0xa4a, "CMP_SCF": 0xa4c, "CMP_MIX": 0xa4e, "MASTER_OD": 0xa50, "PATTERN_VOL": 0xa52,
    "CMP_TRACKS": 0xd4, "CMP_IN_FX": 0xd6, "TEMPO": 0xd8,
}


def q31(x):
    x = max(-1.0, min(x, 1.0 - 2 ** -31))
    return int(round(x * 2147483648.0)) & 0xffffffff


class FullDSP:
    def __init__(self, vm=None):
        self.vm = vm or dn2_after_init()
        self.vm.lib.vm_set_align_strict(self.vm.h, 0)   # the voice code makes odd short stores (note)
        self.frame = bytearray(FRAME_LEN)
        struct.pack_into("<H", self.frame, 0, 3)
        self.insns = 0
        self.secs = 0.0
        self.cycles = []

    def set(self, **words):
        """Frame words by name: knob values 0..127 (sent as v << 8), or raw=True values via set_raw."""
        for k, v in words.items():
            struct.pack_into("<H", self.frame, FRAME_WORDS[k], (int(v) << 8) & 0xffff)

    def set_raw(self, **words):
        for k, v in words.items():
            struct.pack_into("<H", self.frame, FRAME_WORDS[k], int(v) & 0xffff)

    def block(self, L, R):
        vm = self.vm
        spi = vm.rd32(SPI_HALF)
        vm.write(RX_BASE + 0x1000 * spi, bytes(self.frame))
        h = vm.rd32(DMA_HALF)
        vm.write(CODEC_IN + 0x100 * h, struct.pack("<64I", *[q31(v) for pair in zip(L, R) for v in pair]))
        n0, t0 = vm.icount, time.perf_counter()
        vm.call(HANDLER)
        self.insns += vm.icount - n0
        self.secs += time.perf_counter() - t0
        tx0 = vm.rd32(TX_BASE + 0x1000 * spi)
        self.cycles.append(((tx0 >> 16) | (tx0 << 16)) & 0xffffffff)
        return vm.rdf(OUT_FLOATS, 32), vm.rdf(OUT_FLOATS + 0x80, 32)

    def process(self, L, R):
        oL, oR = [], []
        for i in range(0, len(L) // 32 * 32, 32):
            a, b = self.block(L[i:i + 32], R[i:i + 32])
            oL += a
            oR += b
        return oL, oR
