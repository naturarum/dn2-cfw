#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
# The FFT bit-reversal loop is the textbook one, as in octabam's tools/harness/send_probe.py
# (Sam Banks, MIT).
"""T3.7: compare the owner's recordings of the stock effects (T3.5) with the SHARC emulator (T3.6).

    python3 scripts/t37_compare.py                 # every take found in out/T3.11/stock/
    python3 scripts/t37_compare.py --take delay_01_pluck_t24f60
    python3 scripts/t37_compare.py --selftest      # no recordings needed (see below)
    python3 scripts/t37_compare.py --pair a.wav b.wav   # just compare two files (any 48 kHz WAVs)

For each take named as in notes/T3.5-recording-plan.md, the same dry file (out/T3.11/dry/) goes through
the same stock effect at the same settings in the emulator (tools/sharcvm/render_fx.py, render_file),
and the recording is compared with that render:
  alignment   lag of the recording behind the render (samples; minus the path_ takes' converter
              latency when those exist), the correlation at that lag, and the clock drift (ppm)
              from a second alignment late in the file
  level       least-squares gain from render to recording (dB), relative to the dry path's gain
              when the path_ takes exist; RMS of both
  residual    what is left after lag and gain are matched, relative to the recording (dB):
              -40 dB or lower means the two are nearly the same signal
  spectrum    third-octave band levels (31.5 Hz..16 kHz, Welch, 8192-point FFT) of the
              recording minus the render, after the level match: mean |dev| and the largest band
Settings: the T3.5 table, as the screen shows them (TIME shows v+1, bipolar knobs v-64, RAT the
ratio); OVERRIDES from out/T3.11/stock/takes.txt (lines "take_name KEY VALUE, KEY VALUE ..."), so
whatever was really set on the unit is what gets rendered. Unipolar knobs are assumed to show v.

Writes out/T3.7/compare.tsv (one line per take) and out/T3.7/bands.tsv (per-band differences), and
prints a table. Stdlib only (a small FFT below), so it is slow-ish: about 5-10 s per take, in
parallel. Emulated renders are cached in out/T3.7/emu/.

Limits [inferred]: the emulator renders the effect alone (FxRunner), not the whole unit: the input
stage, the FX bus, the master overdrive and the converters are left out (the path_ takes measure the
converters; tools/sharcvm/render_full.py could render the whole chain later). Time-varying effects
(chorus LFO, wow) start at an unknown phase on the unit, so their residual will stay high even when
the emulator is right; look at level and spectrum for those.

--selftest renders one take in the emulator and compares (a) the render with itself and (b) a
copy delayed by 0.3 s + 1234 samples, 3 dB quieter, with noise at -100 dBFS, and (c) the same copy
through a one-pole low-pass at 4 kHz, and checks that the script reports exactly that.
"""
import argparse
import cmath
import json
import math
import os
import random
import re
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools/sharcvm"))
sys.dont_write_bytecode = True
from fx import read_wav, write_wav  # noqa: E402
import memguard  # noqa: E402

SR = 48000
STOCK = ROOT / "out/T3.11/stock"
DRY = ROOT / "out/T3.11/dry"
OUT = ROOT / "out/T3.7"

# --- the takes of notes/T3.5-recording-plan.md (screen values) ---------------------------------------
D = dict(X="off", WID=0, HPF=0, LPF=127)
TAKES = [
    ("path_pluck", "pluck", None, {}),
    ("path_mix", "mix", None, {}),
    ("path_sweep", "sweep", None, {}),
    ("delay_01_pluck_t24f60", "pluck", "delay", dict(D, TIME=24, FDBK=60)),
    ("delay_02_pluck_t24f60_filt", "pluck", "delay", dict(D, TIME=24, FDBK=60, HPF=32, LPF=80)),
    ("delay_03_clave_t64f40", "clave", "delay", dict(D, TIME=64, FDBK=40)),
    ("delay_04_clave_t32f60_pp", "clave", "delay", dict(D, TIME=32, FDBK=60, X="on")),
    ("delay_05_stabs_t16_fmax", "stabs", "delay", dict(D, TIME=16, FDBK=127)),
    ("delay_06_pad_t32f60", "pad", "delay", dict(D, TIME=32, FDBK=60)),
    ("delay_07_sweep_t32f0", "sweep", "delay", dict(D, TIME=32, FDBK=0)),
    ("delay_08_pluck_timemove", "pluck", "delay", dict(D, TIME=24, FDBK=65, _until=2.9)),
    ("comp_01_mix_off", "mix", "comp", dict(THR=127, RAT=1.5, MUP=0)),
    ("comp_02_mix_glue", "mix", "comp", dict(THR=85, RAT=2, ATK=85, REL=65, MUP=0)),
    ("comp_03_drums_heavy", "drums", "comp", dict(THR=55, RAT=8, ATK=40, REL=50, MUP=0)),
    ("comp_04_noise_fast", "noise", "comp", dict(THR=64, RAT=20, ATK=0, REL=0)),
    ("comp_05_noise_slow", "noise", "comp", dict(THR=64, RAT=20, ATK=127, REL=127)),
    ("comp_06_noise_mid", "noise", "comp", dict(THR=64, RAT=20, ATK=64, REL=64)),
    ("comp_07_duck_LR", "duck_LR", "comp", dict(THR=70, RAT=20, ATK=10, REL=60, _duck=True)),
    ("comp_08_sweep", "sweep", "comp", dict(THR=127, RAT=1.5)),
    ("chorus_01_pad_d64s32", "pad", "chorus", dict(DPTH=64, SPD=32, HPF=0, WDTH=64)),
    ("chorus_02_pad_d127s10", "pad", "chorus", dict(DPTH=127, SPD=10, HPF=0, WDTH=127)),
    ("chorus_03_sweep", "sweep", "chorus", dict(DPTH=64, SPD=32, HPF=0, WDTH=64)),
    ("reverb_01_clave_d64", "clave", "reverb", dict(PRE=0, DEC=64, FREQ=64, GAIN=64, HPF=0, LPF=127)),
    ("reverb_02_clave_d127", "clave", "reverb", dict(PRE=0, DEC=127, FREQ=64, GAIN=64, HPF=0, LPF=127)),
    ("reverb_03_drums_d80", "drums", "reverb", dict(PRE=0, DEC=80, FREQ=64, GAIN=64, HPF=0, LPF=127)),
]
PREROLL = {"delay": 6.0, "comp": 0.5, "chorus": 2.0, "reverb": 2.0}
TAIL = 4.0
RATIOS = [1.5, 2.0, 3.0, 4.0, 6.0, 8.0, 16.0, 20.0]


def screen_to_raw(effect, k):
    """Screen values (T3.5) -> raw knob values 0..127 (stockmap). Keys starting with _ are options."""
    raw = {}
    for name, v in k.items():
        if name.startswith("_"):
            continue
        if isinstance(v, str):
            v = 1 if v.lower() == "on" else 0
        if effect == "delay" and name == "TIME":
            v = v - 1
        elif (effect == "delay" and name == "WID") or (effect == "comp" and name == "SCF"):
            v = v + 64
        elif effect == "comp" and name == "RAT":
            v = min(range(8), key=lambda i: abs(RATIOS[i] - v))
        raw[name] = max(0, min(127, v))
    return raw


def read_overrides():
    """takes.txt: 'take_name KEY VALUE, KEY VALUE ...' (screen values; on/off for X)."""
    ov = {}
    p = STOCK / "takes.txt"
    if not p.exists():
        return ov
    for line in open(p):
        m = re.match(r"\s*([A-Za-z0-9_]+?)(?:\.wav)?[\s:|]+(.*)", line)
        if not m:
            continue
        kv = {}
        for key, val in re.findall(r"\b([A-Z]{1,4})\s*[:=]?\s*(-?\d+(?:\.\d+)?|on|off)\b", m.group(2)):
            kv[key] = val if val in ("on", "off") else float(val)
        if kv:
            ov[m.group(1)] = kv
    return ov


# --- signal helpers -------------------------------------------------------------------------------------
def mono(L, R):
    return [(a + b) * 0.5 for a, b in zip(L, R)]


def env(x, hop):
    return [math.sqrt(sum(v * v for v in x[i:i + hop]) / hop) for i in range(0, len(x) - hop + 1, hop)]


def ncc(a, b, lag, n0, n1):
    """normalised correlation of a[i + lag] with b[i] for i in [n0, n1)"""
    s = sa = sb = 0.0
    for i in range(max(n0, -lag), min(n1, len(a) - lag, len(b))):
        x, y = a[i + lag], b[i]
        s += x * y
        sa += x * x
        sb += y * y
    return s / math.sqrt(sa * sb) if sa > 0 and sb > 0 else 0.0


def coarse_lag(rec, emu, max_lead=1.0, max_lag=8.0):
    """lag (samples) of rec behind emu from 10 ms then 1 ms envelopes"""
    best = (-2.0, 0)
    e10r, e10e = env(rec, 480), env(emu, 480)
    for l10 in range(-int(max_lead * 100), int(max_lag * 100)):
        c = ncc(e10r, e10e, l10, 0, len(e10e))
        if c > best[0]:
            best = (c, l10)
    e1r, e1e = env(rec, 48), env(emu, 48)
    l1best = (-2.0, best[1] * 10)
    for l1 in range(best[1] * 10 - 15, best[1] * 10 + 16):
        c = ncc(e1r, e1e, l1, 0, len(e1e))
        if c > l1best[0]:
            l1best = (c, l1)
    return l1best[1] * 48


def loudest_window(x, n, lo=0, hi=None):
    hi = len(x) - n if hi is None else min(hi, len(x) - n)
    best, at = -1.0, lo
    for i in range(lo, max(lo + 1, hi), n // 2):
        e = sum(v * v for v in x[i:i + n:4])
        if e > best:
            best, at = e, i
    return at


def fine_lag(rec, emu, guess, at, n=12000, span=96):
    """sample lag near guess from a window of emu at [at, at+n); parabolic sub-sample peak"""
    cs = {}
    for l in range(guess - span, guess + span + 1):
        cs[l] = ncc(rec, emu, l, at, at + n)
    l0 = max(cs, key=cs.get)
    y0, ym, yp = cs[l0], cs.get(l0 - 1, cs[l0]), cs.get(l0 + 1, cs[l0])
    den = ym - 2 * y0 + yp
    frac = 0.5 * (ym - yp) / den if den < 0 else 0.0
    return l0, l0 + frac, y0


def fft(a):
    """in-place iterative radix-2 FFT of a list of complex numbers (len a power of 2)"""
    n = len(a)
    j = 0
    for i in range(1, n):
        bit = n >> 1
        while j & bit:
            j ^= bit
            bit >>= 1
        j |= bit
        if i < j:
            a[i], a[j] = a[j], a[i]
    size = 2
    while size <= n:
        w = cmath.exp(-2j * math.pi / size)
        half = size // 2
        tw = [w ** k for k in range(half)]
        for s in range(0, n, size):
            for k in range(half):
                u, v = a[s + k], a[s + k + half] * tw[k]
                a[s + k], a[s + k + half] = u + v, u - v
        size *= 2
    return a


BANDS = [1000.0 * 2 ** (k / 3) for k in range(-15, 13)]       # 31.5 Hz .. 16 kHz


def band_levels(x, nfft=8192, max_frames=40):
    """third-octave band powers (dB) of x by Welch (Hann, hop nfft)"""
    win = [0.5 - 0.5 * math.cos(2 * math.pi * i / nfft) for i in range(nfft)]
    starts = list(range(0, len(x) - nfft + 1, nfft))
    if len(starts) > max_frames:
        step = len(starts) / max_frames
        starts = [starts[int(i * step)] for i in range(max_frames)]
    psd = [0.0] * (nfft // 2)
    for s in starts:
        sp = fft([complex(x[s + i] * win[i], 0.0) for i in range(nfft)])
        for k in range(nfft // 2):
            psd[k] += abs(sp[k]) ** 2
    out = []
    for fc in BANDS:
        lo, hi = fc / 2 ** (1 / 6), fc * 2 ** (1 / 6)
        k0, k1 = max(1, int(lo * nfft / SR)), max(int(lo * nfft / SR) + 1, int(hi * nfft / SR))
        p = sum(psd[k0:k1]) / max(1, len(starts))
        out.append(10 * math.log10(p + 1e-30))
    return out


def compare(rec_LR, emu_LR, until=None):
    """the comparison of one recording with one render; returns a dict"""
    rec, emu = mono(*rec_LR), mono(*emu_LR)
    if until:
        emu = emu[:int(until * SR)]
    guess = coarse_lag(rec, emu)
    n = min(12000, len(emu) // 4)
    at1 = loudest_window(emu, n, 0, len(emu) // 2)
    lag, lagf, c1 = fine_lag(rec, emu, guess, at1, n)
    at2 = loudest_window(emu, n, len(emu) // 2)
    lag2, lagf2, c2 = fine_lag(rec, emu, lag, at2, n, span=24)
    drift = (lagf2 - lagf) / ((at2 - at1) / SR) * 1e6 / SR if at2 > at1 + SR else 0.0
    # level and residual over the overlap, both channels
    sre = see = srr = 0.0
    m = range(max(0, -lag), min(len(emu), len(rec) - lag))
    for ch in (0, 1):
        r, e = rec_LR[ch], emu_LR[ch]
        for i in m:
            x, y = r[i + lag], e[i]
            sre += x * y
            see += y * y
            srr += x * x
    g = sre / see if see > 0 else 0.0
    resid = srr - 2 * g * sre + g * g * see
    rms_r = 10 * math.log10(srr / (2 * len(m)) + 1e-30)
    rms_e = 10 * math.log10(see / (2 * len(m)) + 1e-30)
    gain_db = 20 * math.log10(abs(g)) if g else -999.0
    resid_db = 10 * math.log10(max(resid, 1e-30) / srr) if srr > 0 else 0.0
    br = band_levels([rec[i + lag] for i in m])
    be = band_levels([emu[i] for i in m])
    top = max(max(br), max(be))
    dev = [(fc, (a - b) - gain_db) for fc, a, b in zip(BANDS, br, be) if max(a, b) > top - 60]
    mad = sum(abs(d) for _, d in dev) / len(dev) if dev else 0.0
    worst = max(dev, key=lambda t: abs(t[1])) if dev else (0.0, 0.0)
    return dict(lag=round(lagf, 2), corr=round(c1, 4), drift_ppm=round(drift, 1), gain_db=round(gain_db, 2),
                rms_rec=round(rms_r, 2), rms_emu=round(rms_e, 2), resid_db=round(resid_db, 1),
                spec_mad=round(mad, 2), spec_worst_hz=round(worst[0]), spec_worst_db=round(worst[1], 2),
                bands=[(round(fc), round(d, 2)) for fc, d in dev])


# --- the emulated side --------------------------------------------------------------------------------
def emulate(name, dry, effect, knobs):
    """render a take's settings in the emulator (cached); returns (L, R) of dry + wet or the insert"""
    import render_fx
    cache = OUT / "emu" / f"{name}.wav"
    meta = cache.with_suffix(".json")
    raw = screen_to_raw(effect, knobs) if effect else {}
    key = json.dumps(dict(dry=dry, effect=effect, raw=raw, duck=knobs.get("_duck", False)), sort_keys=True)
    if cache.exists() and meta.exists() and open(meta).read() == key:
        L, R, _ = read_wav(cache)
        return L, R
    inp = str(DRY / f"{dry}.wav")
    dL, dR, _ = read_wav(inp)
    secs = len(dL) / SR + (TAIL if effect in ("delay", "reverb", "chorus") else 0.0)
    if effect is None:
        L, R = dL, dR
    elif knobs.get("_duck"):
        # comp_07: DUAL on, IN L (bass) through the compressor, IN R (kick) as the side chain and dry
        # in the mix. Both inputs centred; the pan law is not known (unity assumed) [guess]
        import tempfile
        tmp = Path(tempfile.mkdtemp())
        write_wav(tmp / "bass.wav", dL, dL)
        write_wav(tmp / "kick.wav", dR, dR)
        L, R, _, _, _ = render_fx.render_file("comp", str(tmp / "bass.wav"), knobs=raw, seconds=secs,
                                              preroll=PREROLL["comp"], mix="wet", key=str(tmp / "kick.wav"))
        L = [a + b for a, b in zip(L, dR)]
        R = [a + b for a, b in zip(R, dR)]
    else:
        # delay, chorus, reverb: send 127 and VOL 127 -> dry + wet; compressor: insert, MIX as set
        mix = "wet" if effect == "comp" else "send"
        L, R, _, _, _ = render_fx.render_file(effect, inp, knobs=raw, seconds=secs, preroll=PREROLL[effect],
                                              mix=mix, vol=127.0)
    cache.parent.mkdir(parents=True, exist_ok=True)
    write_wav(cache, L, R)
    open(meta, "w").write(key)
    L, R, _ = read_wav(cache)      # as stored (24-bit), so cached and fresh runs agree
    return L, R


def run_take(args):
    (name, dry, effect, knobs), stock = args
    t0 = time.time()
    rec = read_wav(Path(stock) / f"{name}.wav")
    emu = emulate(name, dry, effect, knobs)
    r = compare(rec[:2], emu, until=knobs.get("_until"))
    r.update(take=name, effect=effect or "path", secs=round(time.time() - t0, 1))
    memguard.check()
    return r


def table(rows, path_lag=None, path_gain=None):
    hdr = (f"{'take':32s} {'lag':>9s} {'corr':>6s} {'drift':>7s} {'gain dB':>8s} {'rel path':>8s} "
           f"{'resid dB':>8s} {'spec |dev|':>10s}  worst band")
    print(hdr)
    for r in rows:
        rel_lag = f"{r['lag'] - path_lag:+.1f}" if path_lag is not None and r["effect"] != "path" else ""
        rel_g = f"{r['gain_db'] - path_gain:+.2f}" if path_gain is not None and r["effect"] != "path" else ""
        print(f"{r['take']:32s} {r['lag']:9.2f} {r['corr']:6.3f} {r['drift_ppm']:7.1f} {r['gain_db']:8.2f} "
              f"{rel_g:>8s} {r['resid_db']:8.1f} {r['spec_mad']:10.2f}  {r['spec_worst_hz']} Hz "
              f"{r['spec_worst_db']:+.1f} dB" + (f"  (lag vs path {rel_lag})" if rel_lag else ""))


def write_tsv(rows, path_lag, path_gain):
    OUT.mkdir(parents=True, exist_ok=True)
    cols = ["take", "effect", "lag", "corr", "drift_ppm", "gain_db", "rms_rec", "rms_emu", "resid_db",
            "spec_mad", "spec_worst_hz", "spec_worst_db"]
    with open(OUT / "compare.tsv", "w") as f:
        f.write("\t".join(cols + ["lag_vs_path", "gain_vs_path"]) + "\n")
        for r in rows:
            f.write("\t".join(str(r[c]) for c in cols))
            f.write("\t" + (f"{r['lag'] - path_lag:.2f}" if path_lag is not None else ""))
            f.write("\t" + (f"{r['gain_db'] - path_gain:.2f}" if path_gain is not None else "") + "\n")
    with open(OUT / "bands.tsv", "w") as f:
        f.write("take\t" + "\t".join(str(round(b)) for b in BANDS) + "\n")
        for r in rows:
            d = dict(r["bands"])
            f.write(r["take"] + "\t" + "\t".join(str(d.get(round(b), "")) for b in BANDS) + "\n")


# --- self-test -------------------------------------------------------------------------------------------
def selftest():
    """no recordings: compare an emulated take with itself and with altered copies of itself"""
    name, dry, effect, knobs = TAKES[3]                       # delay_01_pluck_t24f60
    L, R = emulate(name, dry, effect, knobs)
    ok = True

    def check(label, r, lag, gain, resid_max, mad_max, worst=None):
        nonlocal ok
        good = (abs(r["lag"] - lag) < 0.6 and abs(r["gain_db"] - gain) < 0.05 and r["resid_db"] <= resid_max
                and r["spec_mad"] <= mad_max and (worst is None or worst(r)))
        ok &= good
        print(f"{'PASS' if good else 'FAIL'}  {label}: lag {r['lag']} (expect {lag}), gain {r['gain_db']} dB "
              f"(expect {gain}), residual {r['resid_db']} dB, spectrum |dev| {r['spec_mad']} dB, worst "
              f"{r['spec_worst_hz']} Hz {r['spec_worst_db']:+.2f} dB, drift {r['drift_ppm']} ppm, "
              f"corr {r['corr']}")
    t0 = time.time()
    check("itself", compare((L, R), (L, R)), 0, 0.0, -100, 0.01)
    rnd = random.Random(5)
    d = int(0.3 * SR) + 1234
    g = 10 ** (-3 / 20)
    nz = 10 ** (-100 / 20)
    cL = [0.0] * d + [g * x + nz * rnd.gauss(0, 1) for x in L] + [0.0] * SR
    cR = [0.0] * d + [g * x + nz * rnd.gauss(0, 1) for x in R] + [0.0] * SR
    check("delayed 15634 samples, -3 dB, noise -100 dBFS", compare((cL, cR), (L, R)), d, -3.0, -60, 0.05)
    a = math.exp(-2 * math.pi * 4000 / SR)

    def lp(x):
        y, s = [], 0.0
        for v in x:
            s = (1 - a) * v + a * s
            y.append(s)
        return y
    r = compare((lp(cL), lp(cR)), (L, R))
    # a one-pole low-pass at 4 kHz: the spectrum must fall at the top (largest deviation above 8 kHz,
    # negative), with a small group delay (under one sample) and gain near -3 dB
    nonlocal_ok = r["spec_worst_hz"] >= 8000 and r["spec_worst_db"] < -3.0 and abs(r["lag"] - d) < 1.0
    ok &= nonlocal_ok
    print(f"{'PASS' if nonlocal_ok else 'FAIL'}  same copy through a 4 kHz one-pole low-pass: lag {r['lag']}, "
          f"gain {r['gain_db']} dB, spectrum |dev| {r['spec_mad']} dB, worst {r['spec_worst_hz']} Hz "
          f"{r['spec_worst_db']:+.2f} dB")
    print(f"selftest {'passed' if ok else 'FAILED'} in {time.time() - t0:.1f} s")
    return ok


def main():
    global STOCK
    ap = argparse.ArgumentParser()
    ap.add_argument("--take", action="append")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--pair", nargs=2)
    ap.add_argument("--jobs", type=int, default=os.cpu_count())
    ap.add_argument("--stock", help=f"recordings directory (default {STOCK})")
    a = ap.parse_args()
    if a.stock:
        STOCK = Path(a.stock).resolve()
    if a.selftest:
        sys.exit(0 if selftest() else 1)
    if a.pair:
        A, B = read_wav(a.pair[0]), read_wav(a.pair[1])
        r = compare(A[:2], B[:2])
        r["take"], r["effect"] = Path(a.pair[0]).stem, "pair"
        table([r])
        print("bands (Hz: dB, first minus second, after the level match):",
              " ".join(f"{f}:{d:+.1f}" for f, d in r["bands"]))
        return
    if not STOCK.is_dir():
        sys.exit(f"no recordings yet: {STOCK} does not exist (notes/T3.5-recording-plan.md)")
    ov = read_overrides()
    todo = []
    for name, dry, effect, knobs in TAKES:
        if a.take and name not in a.take:
            continue
        if not (STOCK / f"{name}.wav").exists():
            continue
        k = dict(knobs)
        k.update(ov.get(name, {}))
        todo.append((name, dry, effect, k))
    missing = [t[0] for t in TAKES if not (STOCK / f"{t[0]}.wav").exists()]
    if missing:
        print(f"not recorded (skipped): {', '.join(missing)}")
    if not todo:
        sys.exit("no takes to compare")
    with ProcessPoolExecutor(max_workers=a.jobs, max_tasks_per_child=memguard.TASKS_PER_CHILD) as ex:
        rows = list(memguard.pmap(ex, run_take, [(t, str(STOCK)) for t in todo]))
    paths = [r for r in rows if r["effect"] == "path"]
    path_lag = sum(r["lag"] for r in paths) / len(paths) if paths else None
    path_gain = sum(r["gain_db"] for r in paths) / len(paths) if paths else None
    table(rows, path_lag, path_gain)
    write_tsv(rows, path_lag, path_gain)
    print(f"wrote {OUT / 'compare.tsv'} and {OUT / 'bands.tsv'}")


if __name__ == "__main__":
    main()
