# SPDX-License-Identifier: GPL-2.0-or-later
"""Knob values (as the ColdFire sends them) -> the floats the stock effects read (T3.4, T3.6).

The ColdFire sends each FX knob as a 16-bit frame word `v << 8` (TIME and FDBK may carry a
fractional low byte, T3.3). The DSP's render 0x1c2712 converts the word to a float in one of
three ways (T3.4 section 1):

  scale   word * F15, F15 = 1/32512 as a float          -> v/127 (most knobs)
  2^-15   float(word) by -15                            -> v/128 (delay WID and FDBK)
  int     float(word >> 8) or an index                  -> delay X (0/1); comp RAT is index/7

`params(effect, knobs)` takes raw knob values 0..127 (not what the screen shows: TIME shows
v+1, bipolar knobs show v-64; the compressor's RAT takes the index 0..7) and returns the
parameter-struct floats for tools/sharcvm/fx.py.

Measured in the emulator on 2026-10-06 (notes/T3.11-fx-workbench.md, section 10) [verified in
the emulator, not on the unit]:
  delay   echo at (TIME+1) * 90000/BPM + 24 samples; loop gain FDBK/64 (64 = 100 %, 127 = 198 %,
          the soft clip holds it); first echo -0.4 dB; HPF and LPF are first-order and in the
          loop, corner about 50 Hz * 10^((HPF-32)/32) and 400 Hz * 10^((LPF-64)/32); WID is the
          bipolar width: 64 (screen 0) = plain stereo, with X on 0 (screen -64) = full ping-pong;
          a TIME change glides (tape-like pitch bend, up to about 2x speed).
  comp    hard knee, peak detector on max(|L|,|R|) after the side-chain filter; threshold
          -60 + 60*THR/127 dB; ratios 1.5 2 3 4 6 8 16 20; ATK and REL per-sample one-poles from
          tables 0x2cfe00 / 0x2d0000 (0.1..100 ms, 10..2000 ms); makeup 24*MUP/127 dB; SCF 64 is
          neutral, below 64 a low-pass, above a high-pass on the side chain (SCF_CORNERS).
"""
import math
import struct

F15 = struct.unpack("<f", struct.pack("<f", 1.0 / 32512.0))[0]


def f32(x):
    return struct.unpack("<f", struct.pack("<f", x))[0]


def scale(v):
    """word * F15, rounded to float32 as the DSP does."""
    w = int(round(max(0.0, min(127.99, v)) * 256))
    return f32(f32(float(w)) * F15)


def by15(v):
    w = int(round(max(0.0, min(127.99, v)) * 256))
    return w / 32768.0


FIELDS = {
    "delay": {"TIME": scale, "X": lambda v: 1.0 if v >= 0.5 else 0.0, "WID": by15, "FDBK": by15,
              "HPF": scale, "LPF": scale},
    "chorus": {"DPTH": scale, "SPD": scale, "HPF": scale, "WDTH": scale},
    "reverb": {"PRE": scale, "DEC": scale, "FREQ": scale, "GAIN": scale, "HPF": scale, "LPF": scale},
    "comp": {"SCF": scale, "THR": scale, "RAT": lambda i: f32(int(round(i)) / 7.0), "ATK": scale,
             "REL": scale, "MUP": scale, "MIX": scale},
}

# defaults as raw knob values: a new project's look (T3.5 plan), compressor at MIX 127
DEFAULT_KNOBS = {
    "delay": dict(TIME=31, X=0, WID=64, FDBK=40, HPF=0, LPF=127),
    "chorus": dict(DPTH=64, SPD=32, HPF=0, WDTH=64),
    "reverb": dict(PRE=0, DEC=64, FREQ=64, GAIN=64, HPF=0, LPF=127),
    "comp": dict(SCF=64, THR=127, RAT=0, ATK=0, REL=0, MUP=0, MIX=127),
}

RATIOS = [1.5, 2.0, 3.0, 4.0, 6.0, 8.0, 16.0, 20.0]      # table 0x2cfde0 holds 1/r

# side-chain filter -3 dB corners measured in the emulator (THR -30 dB, 20:1, sine at -10 dBFS):
# SCF value -> (type, Hz). 0..12: low-pass below 20 Hz.
SCF_CORNERS = {16: ("LP", 28), 20: ("LP", 80), 24: ("LP", 202), 28: ("LP", 359), 32: ("LP", 604),
               36: ("LP", 987), 40: ("LP", 1532), 44: ("LP", 2416), 48: ("LP", 3583), 52: ("LP", 5530),
               56: ("LP", 8127), 60: ("LP", 11060), 68: ("HP", 35), 72: ("HP", 50), 76: ("HP", 77),
               80: ("HP", 119), 84: ("HP", 184), 88: ("HP", 285), 92: ("HP", 435), 96: ("HP", 674),
               100: ("HP", 1042), 104: ("HP", 1655), 108: ("HP", 2507), 112: ("HP", 3971),
               116: ("HP", 5881), 120: ("HP", 8973), 124: ("HP", 11763)}


def params(effect, knobs):
    k = dict(DEFAULT_KNOBS[effect])
    k.update(knobs)
    return {name: fn(k[name]) for name, fn in FIELDS[effect].items()}


def nearest_ratio_index(r):
    return min(range(len(RATIOS)), key=lambda i: abs(math.log(RATIOS[i]) - math.log(max(r, 1.0001))))


def scf_for(kind, hz):
    """SCF knob whose measured corner of the given kind ('LP' or 'HP') is nearest hz (log)."""
    c = [(v, f) for v, (t, f) in SCF_CORNERS.items() if t == kind]
    return min(c, key=lambda vf: abs(math.log(vf[1] / hz)))[0]


def delay_hpf_knob(hz):
    return max(0, min(127, round(32 + 32 * math.log10(hz / 50.0))))


def delay_lpf_knob(hz):
    return max(0, min(127, round(64 + 32 * math.log10(hz / 400.0))))


def comp_time_tables(vm):
    """Per-sample one-pole tables of the stock compressor -> time constants in ms, per knob."""
    att = vm.rdf(0x2cfe00, 128)
    rel = vm.rdf(0x2d0000, 128)
    tau = lambda a: -1000.0 / (48000.0 * math.log(a))
    return [tau(a) for a in att], [tau(a) for a in rel]


def nearest_knob(table, ms):
    return min(range(len(table)), key=lambda i: abs(math.log(table[i] / ms)))
