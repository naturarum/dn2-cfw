#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""stock_renders.py - the "stock DN2 (emulated)" side of the A/B page (T3.11, 2026-10-06).

    python3 stock_renders.py OUTDIR [--force] [--jobs N]     (after renders.sh; make stock)

For every render in OUTDIR/manifest.tsv, the same dry file goes through the Digitone II's own
effect code (OS 1.12, run in the T3.6 SHARC interpreter, tools/sharcvm) at the stock knob values
closest to the new effect's settings. The mapping is in map_tapedelay(), map_spaceecho() (T5.2),
map_flanger() and map_mbcomp() and in notes/T3.11-fx-workbench.md section 10 and notes/T5.2-space-echo.md. Renders whose settings map to the same stock knobs share
one stock file. Also renders the stock chorus and reverb on two dry files, for reference.

This is an emulation, not a recording: T3.7 checks the emulator against the owner's recordings
(T3.5). Writes OUTDIR/wav/stock_*.wav (24-bit) and OUTDIR/manifest_stock.tsv, which make_ab.py
merges into the page. Renders are cached by their settings (OUTDIR/stock_cache.json).
"""
import argparse
import hashlib
import json
import math
import os
import shlex
import sys
import time
import wave
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools/sharcvm"))
sys.dont_write_bytecode = True
import stockmap as SM  # noqa: E402

SR, BLOCK = 48000, 32

# --- the new effects' defaults (tapedelay_init, mbcomp_init) -------------------------------------
# (2026-10-06, notes/T3.11 section 12: one-head tape delay with resonant filters, stock-design bands)
TD_DEFAULTS = dict(TIME=31, FDBK=50, HPF=22, LPF=102, RES=0, WID=64, WOW=20, FLUT=20, AGE=0, DRIV=32)
MB_DEFAULTS = {}
for _b in "LMH":
    MB_DEFAULTS.update({f"{_b}.THR": 85, f"{_b}.ATK": 40, f"{_b}.REL": 44, f"{_b}.MUP": 0, f"{_b}.RAT": 2})
MB_DEFAULTS.update(XLO=68, XHI=44, SCS=0, MIX=127)   # T6.3 CMP0: LSTN and SCF dropped; crossover laws of T6.4 2.2
FL_DEFAULTS = dict(DPTH=96, SPD=30, HPF=32, WDTH=48, FDBK=107, MAN=50, SHP=0, SLEW=0, DAMP=12, SYNC=0, OFS=0, POL=0)
# the Space Echo (T5.2, fx/spaceecho.c: spaceecho_init); fxrender's long names are aliases
SE_DEFAULTS = dict(TIME=31, MODE=3, INT=50, AGE=40, CTR=64, BW=127, WID=96, PAN=1)
SE_ALIAS = {"INTENSITY": "INT", "CENTER": "CTR", "WIDTH": "WID"}
SE_HEADS = [1, 2, 4, 3, 6, 5, 7]          # MODE index -> heads (bit 0 = head 1)


def parse_fxrender(argstr):
    """fxrender arguments -> dict (fx, input, key, knobs at t=0, timed events, tail, bpm, vol, mix)."""
    a = shlex.split(argstr)
    r = dict(fx=None, i=None, key=None, p={}, at=[], ramp=[], tail=0.0, bpm=120.0, vol=100.0, mix=None, gain=0.0)
    i = 0
    while i < len(a):
        o = a[i]
        if o == "-fx": r["fx"] = a[i + 1]; i += 2
        elif o == "-i": r["i"] = a[i + 1]; i += 2
        elif o == "-key": r["key"] = a[i + 1]; i += 2
        elif o == "-p":
            k, v = a[i + 1].split("="); r["p"][k] = float(v); i += 2
        elif o == "-at":
            k, v = a[i + 2].split("="); r["at"].append((float(a[i + 1]), k, float(v))); i += 3
        elif o == "-ramp":
            r["ramp"].append((float(a[i + 1]), float(a[i + 2]), a[i + 3], float(a[i + 4]), float(a[i + 5]))); i += 6
        elif o in ("-tail", "-bpm", "-vol", "-gain"): r[o[1:]] = float(a[i + 1]); i += 2
        elif o == "-mix": r["mix"] = a[i + 1]; i += 2
        else: i += 1
    if r["mix"] is None:
        r["mix"] = "insert" if r["fx"] == "mbcomp" else "send"
    return r


def td_macro(k, name):
    """The tape delay's 1-page macros, as tapedelay_set() applies them: TONE sets HPF and LPF, WF sets
    WOW and FLUT (knob values)."""
    if name == "TONE":
        x = (min(max(k["TONE"], 0.0), 127.0) - 64.0) / 64.0
        hpf, lpf = 50.0, 6500.0
        if x < 0: lpf = 6500.0 * 2 ** (x * 4.4374053)
        if x > 0: hpf, lpf = 50.0 * 2 ** (x * 5.6438562), 6500.0 * 2 ** x
        k["HPF"] = min(127.0, max(0.0, 127.0 * math.log2(hpf / 20.0) / 7.6438562))
        k["LPF"] = min(127.0, max(0.0, 127.0 * math.log2(lpf / 100.0) / 7.4918531))
    elif name in ("WF", "W&F"):
        v = min(max(k[name], 0.0), 127.0)
        k["WOW"], k["FLUT"] = 0.9 * v, max(0.0, v - 40.0)


def knob_timeline(r, defaults, nblocks, macro=None):
    """Knob dicts per block, replicating fxrender's event timing (render.c). Returns [(block, knobs)]
    for block 0 and every block where something changed. macro(k, name) applies a knob that sets
    other knobs (the tape delay's TONE and W&F)."""
    def put(k, name, v):
        k[name] = v
        if macro:
            macro(k, name)
    k = dict(defaults)
    for name, v in r["p"].items():
        put(k, name, v)
    for t0, t1, name, v0, v1 in r["ramp"]:
        if t0 <= 0.0:
            put(k, name, v0)
    out = [(0, dict(k))]
    bd = BLOCK / SR
    for b in range(1, nblocks):
        t = b * BLOCK / SR
        changed = False
        for t0, name, v in r["at"]:
            if t0 <= t < t0 + bd and k.get(name) != v:
                put(k, name, v); changed = True
        for t0, t1, name, v0, v1 in r["ramp"]:
            if t0 <= t <= t1 + bd:
                v = v1 if t >= t1 else v0 + (v1 - v0) * (t - t0) / (t1 - t0)
                if k.get(name) != v:
                    put(k, name, v); changed = True
        if changed:
            out.append((b, dict(k)))
    return out


# --- tape delay -> stock delay --------------------------------------------------------------------
def td_filter_hz(k):
    """HPF and LPF corners of the new delay's tape loop (tapedelay.c: tapedelay_hpf_hz, tapedelay_lpf_hz)."""
    hpf = 20.0 * 2 ** (min(max(k["HPF"], 0.0), 127.0) / 127.0 * 7.6438562)
    lpf = 100.0 * 2 ** (min(max(k["LPF"], 0.0), 127.0) / 127.0 * 7.4918531)
    lpf *= 2 ** (-0.75 * min(max(k["AGE"], 0.0), 127.0) / 127.0)
    return hpf, lpf


def map_tapedelay(k):
    """New tape delay knobs -> stock delay knobs, plus what the stock delay cannot do."""
    hpf, lpf = td_filter_hz(k)
    pp = k["WID"] >= 96
    s = dict(TIME=round(k["TIME"], 3),
             FDBK=round(min(127.0, k["FDBK"] * 1.1 * 64.0 / 127.0)),   # new 0..110 %, stock v/64
             HPF=SM.delay_hpf_knob(hpf), LPF=SM.delay_lpf_knob(lpf),
             X=1 if pp else 0, WID=0 if pp else 64)                     # ping-pong needs WID -64
    missing = []
    if k["WOW"] > 0: missing.append("wow")
    if k["FLUT"] > 0: missing.append("flutter")
    if k["AGE"] > 0: missing.append("AGE (hiss, crackle)")
    if k["RES"] > 0: missing.append("filter resonance")
    if k["WID"] < 48: missing.append("mono width")
    if k["DRIV"] != 32: missing.append("drive")
    return s, missing


def describe_delay(s):
    return (f"TIME {s['TIME']:g}, FDBK {s['FDBK']}, HPF {s['HPF']}, LPF {s['LPF']}, "
            f"X {'on' if s['X'] else 'off'}, WID {s['WID'] - 64:+d}")


# --- Space Echo -> stock delay ------------------------------------------------------------------------
def se_macro(k, name):
    if name in SE_ALIAS:
        k[SE_ALIAS[name]] = k[name]


def map_spaceecho(k):
    """Space Echo knobs -> stock delay knobs (one head: the shortest active one), plus what the stock
    delay cannot do. Laws as in fx/spaceecho.c."""
    c01 = lambda v: min(max(v, 0.0), 127.0) / 127.0
    hm = SE_HEADS[int(min(max(k["MODE"], 0), 6) + 0.5)]
    heads = [h for h in (1, 2, 3) if hm & (1 << (h - 1))]
    h = heads[0]
    g = min(max(k["INT"], 0.0), 127.0) / 64.0         # loop gain per head: the stock FDBK law (SC01; SE01: 1.1 x^1.5)
    a = c01(k["AGE"])
    bw = min(max(k["BW"], 0.0), 127.0)
    if bw >= 127:
        hp, lp = 5.0, 30000.0
    else:
        C = 50.0 * 2 ** (8.0 * c01(k["CTR"]))
        B = 0.5 * 40.0 ** (bw / 127.0)
        hp, lp = C / 2 ** (B / 2), C * 2 ** (B / 2)
    if a > 0:                                          # AGE's top-end loss, a one-pole in the loop
        c = 2 ** (-1.6 * a)
        lp = min(lp, -math.log(1 - c) * 48000.0 / (2 * math.pi))
    pan = int(min(max(k["PAN"], 0), 2) + 0.5)
    alt = pan == 1 and k["WID"] >= 64
    t = h * (k["TIME"] + 1) - 1
    s = dict(TIME=round(min(127.0, t), 3), FDBK=round(min(127.0, g * 64.0)),
             HPF=SM.delay_hpf_knob(hp), LPF=SM.delay_lpf_knob(lp), X=1 if alt else 0, WID=0 if alt else 64)
    missing = []
    if len(heads) > 1:
        missing.append(f"heads {'+'.join(str(x) for x in heads[1:])} (one head only, at head {h}'s time)")
    if t > 127:
        missing.append("the head time (longer than a whole note)")
    if a > 0:
        missing.append("wow, flutter, hiss, crackle (AGE)")
    if bw < 96:
        missing.append("band resonance")
    if pan == 2:
        missing.append("random pan")
    if pan == 0 and k["WID"] < 64:
        missing.append("mono width")
    return s, missing


# --- 3-band compressor -> stock master compressor ----------------------------------------------------
_TABLES = None


def comp_tables():
    global _TABLES
    if _TABLES is None:
        from vm import dn2_after_init
        _TABLES = SM.comp_time_tables(dn2_after_init())
    return _TABLES


def map_mbcomp(k):
    """New 3-band settings -> one stock band. Since 2026-10-06 each band uses the stock compressor's own
    laws (notes/T3.11 12.2-12.3), so THR, ATK, REL and the ratio map one to one; MUP is set at render
    time for equal loudness. Returns (stock knobs or None, notes, uses_key)."""
    bands = [b for b in "LMH" if int(k[f"{b}.RAT"] + 0.5) > 0]
    notes = []
    if not bands:
        return dict(THR=127, RAT=0, ATK=0, REL=0, MUP=0, SCF=64, MIX=127), \
            ["stock has no 1:1; 1.5:1 at 0 dB threshold"], False
    sets = {tuple(round(k[f"{b}.{p}"]) for p in ("THR", "RAT", "ATK", "REL")) for b in bands}
    b = bands[0]
    if len(sets) > 1:
        notes.append(f"bands differ; the {'low mid high'.split()['LMH'.index(b)]} band's settings used")
    key = int(k["SCS"] + 0.5) == 1
    s = dict(THR=round(k[f"{b}.THR"]), RAT=int(k[f"{b}.RAT"] + 0.5) - 1, ATK=round(k[f"{b}.ATK"]),
             REL=round(k[f"{b}.REL"]), MUP=0, SCF=64, MIX=round(k["MIX"]))
    if len(bands) == 1 and not key:
        # one band compresses: aim the stock side-chain filter at that band
        if b == "L":
            hz = 30.0 * (1000.0 / 30.0) ** (k["XLO"] / 127.0)
            s["SCF"] = SM.scf_for("LP", hz)
            notes.append(f"side-chain low-pass at ~{SM.SCF_CORNERS[s['SCF']][1]} Hz (the low band's edge), "
                         "but the stock gain still acts on the whole signal")
        elif b == "H":
            hz = 1000.0 * 16.0 ** (k["XHI"] / 127.0)
            s["SCF"] = SM.scf_for("HP", hz)
            notes.append(f"side-chain high-pass at ~{SM.SCF_CORNERS[s['SCF']][1]} Hz (the high band's edge), "
                         "but the stock gain still acts on the whole signal")
        else:
            notes.append("full-band: the stock compressor cannot act on the mid band alone")
    elif len(bands) < 3:
        notes.append("full-band: the stock compressor acts on everything")
    if key:
        notes.append("external key as the stock side chain")
    att, rel = comp_tables()
    s["_actual"] = (f"{SM.RATIOS[s['RAT']]:g}:1 at {-60 + 60 * s['THR'] / 127:.1f} dB, ATK {att[s['ATK']]:.2g} ms, "
                    f"REL {rel[s['REL']]:.3g} ms")
    return s, notes, key


def map_flanger(k, bpm):
    """New flanger knobs -> the stock chorus (the slot's stock effect) at the same DPTH, HPF, WDTH,
    and the SPD whose rate is nearest (stock rate 0.28 + 2.82 x SPD/127 Hz, measured 2026-10-06)."""
    sy = int(min(max(k["SYNC"], 0), 12) + 0.5)
    beats = [0, 64, 32, 16, 8, 4, 2, 1.5, 1, 0.75, 0.5, 0.25, 0.125][sy]
    hz = bpm / 60.0 / beats if sy else 0.02 * 500 ** (min(max(k["SPD"], 0), 127) / 127.0)
    spd = max(0, min(127, round((hz - 0.28) / 2.82 * 127)))
    s = dict(DPTH=round(k["DPTH"]), SPD=spd, HPF=round(k["HPF"]), WDTH=round(k["WDTH"]))
    missing = ["feedback, damping, manual delay (the stock chorus sweeps 5.4-7.7 ms)"]
    if hz < 0.28:
        missing.append(f"rates below 0.28 Hz (here {hz:.3g} Hz)")
    if int(k["SHP"] + 0.5) != 0:
        missing.append("LFO shapes")
    if k["OFS"] > 0 or int(k["POL"] + 0.5):
        missing.append("time offset, polarity")
    return s, missing, hz


def describe_comp(s):
    return (f"THR {s['THR']}, RAT {SM.RATIOS[s['RAT']]:g}, ATK {s['ATK']}, REL {s['REL']}, MUP {s['MUP']}, "
            f"SCF {s['SCF'] - 64:+d}, MIX {s['MIX']}")


# --- rendering (one process per job) ----------------------------------------------------------------
PREROLL = {"delay": 6.0, "comp": 0.5, "chorus": 2.0, "reverb": 2.0}


def rms_db(L, R):
    acc = sum(x * x for x in L) + sum(x * x for x in R)
    return 10 * math.log10(acc / (len(L) + len(R)) + 1e-30)


def render(job, sched, fx_mod):
    """One stock render; returns (L, R, instructions per block)."""
    L, R, sr = fx_mod.read_wav(job["dry"])
    assert sr == SR
    g_in = 10 ** (job.get("gain", 0.0) / 20.0)
    if g_in != 1.0:
        L = [x * g_in for x in L]
        R = [x * g_in for x in R]
    frames = len(L) + int(job["tail"] * SR + 0.5)
    nb = (frames + BLOCK - 1) // BLOCK
    L = L + [0.0] * (nb * BLOCK - len(L))
    R = R + [0.0] * (nb * BLOCK - len(R))
    kL = kR = None
    if job.get("key"):
        kL, kR, _ = fx_mod.read_wav(job["key"])
        kL = (kL + [0.0] * (nb * BLOCK))[:nb * BLOCK]
        kR = (kR + [0.0] * (nb * BLOCK))[:nb * BLOCK]
    fx = fx_mod.FxRunner(job["effect"], bpm=job.get("bpm", 120.0))
    params = {b: SM.params(job["effect"], kn) for b, kn in sched}
    fx.set_params(**params[0])
    # settle first: the stock delay glides from its init time to TIME (about 1 s)
    z = [0.0] * BLOCK
    for _ in range(int(PREROLL[job["effect"]] * SR) // BLOCK):
        fx.block(z, z)
    fx.insns = 0
    oL, oR = [], []
    for b in range(nb):
        if b in params and b:
            fx.set_params(**params[b])
        s = slice(b * BLOCK, (b + 1) * BLOCK)
        a, c = fx.block(L[s], R[s], kL[s] if kL else None, kR[s] if kR else None) \
            if job["effect"] == "comp" else fx.block(L[s], R[s])
        oL += a
        oR += c
    g = job["vol"] / 127.0
    if job["mix"] == "send":
        oL = [x + g * y for x, y in zip(L, oL)]
        oR = [x + g * y for x, y in zip(R, oR)]
    return oL[:frames], oR[:frames], fx.insns / nb


def run_job(job):
    import fx as fx_mod
    t0 = time.time()
    sched = [(b, dict(k)) for b, k in job["schedule"]]
    res = dict(id=job["id"])
    oL, oR, ipb = render(job, sched, fx_mod)
    if job.get("match"):
        # compressor: set MUP so the stock render is as loud (RMS) as the new one. Stock makeup is
        # 0..+24 dB in steps of 0.19 dB, full band, and the stock output clips at +-1.0 (T3.4: fclip),
        # so MUP is also capped to keep the peak at or below -0.5 dBFS; the page's loudness match
        # does the rest.
        tL, tR, _ = fx_mod.read_wav(job["match"])
        target = rms_db(tL, tR)
        p0 = max(max(map(abs, oL)), max(map(abs, oR)), 1e-9)
        cap = max(0, min(127, int(20 * math.log10(0.944 / p0) * 127.0 / 24.0)))
        mup, capped = 0, False
        pts = [(0, rms_db(oL, oR))]                    # (MUP, RMS dB): secant search
        slope = 24.0 / 127.0                           # dB per MUP step when MIX = 127
        for _ in range(5):
            m0, r0 = pts[-1]
            if len(pts) > 1 and pts[-1][0] != pts[-2][0]:
                slope = max(0.02, (pts[-1][1] - pts[-2][1]) / (pts[-1][0] - pts[-2][0]))
            want = max(0, min(127, round(m0 + (target - r0) / slope)))
            capped = want > cap
            new = min(want, cap)
            if new == mup:
                break
            mup = new
            for _, k in sched:
                k["MUP"] = mup
            oL, oR, ipb = render(job, sched, fx_mod)
            pts.append((mup, rms_db(oL, oR)))
            if abs(pts[-1][1] - target) < 0.3 or capped:
                break
        res.update(mup=mup, capped=capped, level_diff=round(rms_db(oL, oR) - target, 2))
    res.update(peak=fx_mod.write_wav(Path(job["out"]), oL, oR), secs=time.time() - t0, insns=ipb,
               seconds=len(oL) / SR)
    return res


RENDER_VERSION = 4       # bump when run_job changes what it writes


def job_hash(job):
    j = {k: v for k, v in job.items() if k not in ("out",)}
    j["_version"] = RENDER_VERSION
    return hashlib.sha1(json.dumps(j, sort_keys=True).encode()).hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out", nargs="?", default=str(ROOT / "out/T3.11"))
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--jobs", type=int, default=os.cpu_count())
    a = ap.parse_args()
    out = Path(a.out).resolve()
    rows = [l.rstrip("\n").split("\t") for l in open(out / "manifest.tsv")]
    jobs, stock_rows, by_key = [], [], {}
    groups = {}
    for f in rows:
        if f[0] == "group":
            groups[f[1]] = f
            continue
        if len(f) < 7:
            sys.exit("manifest.tsv has no argument column: rerun make renders first")
        gid, path, title, args = f[1], f[2], f[3], f[6]
        rid = Path(path).stem
        r = parse_fxrender(args)
        dry = out / r["i"]
        with wave.open(str(dry)) as w:
            nframes = w.getnframes() + int(r["tail"] * SR + 0.5)
        nblocks = (nframes + BLOCK - 1) // BLOCK
        if r["fx"] in ("tapedelay", "spaceecho"):
            if r["fx"] == "tapedelay":
                tl = knob_timeline(r, TD_DEFAULTS, nblocks, td_macro)
            else:
                tl = knob_timeline(r, SE_DEFAULTS, nblocks, se_macro)
            sched, missing = [], set()
            for b, kn in tl:
                s, miss = map_tapedelay(kn) if r["fx"] == "tapedelay" else map_spaceecho(kn)
                missing.update(miss)
                if not sched or sched[-1][1] != s:
                    sched.append((b, s))
            effect, uses_key = "delay", False
            first = sched[0][1]
            notes = (["no " + ", ".join(sorted(missing))] if missing else [])
            settings = describe_delay(first)
            if len(sched) > 1:
                changes = sorted({b for b, _ in sched[1:]})
                settings += f"; {len(sched) - 1} timed changes (as the new render), from {changes[0] * BLOCK / SR:.2f} s"
            stitle = f"Stock delay: TIME {first['TIME']:g}, FDBK {first['FDBK']}" + (", ping-pong" if first["X"] else "")
        elif r["fx"] == "flanger":
            tl = knob_timeline(r, FL_DEFAULTS, nblocks)
            sched, missing = [], set()
            for b, kn in tl:
                s, miss, hz = map_flanger(kn, r["bpm"])
                missing.update(miss)
                if not sched or sched[-1][1] != s:
                    sched.append((b, s))
            effect, uses_key = "chorus", False
            first = sched[0][1]
            notes = ["no " + "; ".join(sorted(missing))]
            settings = ", ".join(f"{a} {v}" for a, v in first.items()) + \
                f" (stock rate {0.28 + 2.82 * first['SPD'] / 127:.2f} Hz)"
            if len(sched) > 1:
                settings += f"; {len(sched) - 1} timed changes (as the new render)"
            stitle = f"Stock chorus: DPTH {first['DPTH']}, SPD {first['SPD']}"
        elif r["fx"] in ("flgunit1", "flgunit2"):
            print(f"  {rid}: the flanger on the unit's knobs (T5.6); no stock pair")
            continue
        else:
            tl = knob_timeline(r, MB_DEFAULTS, nblocks)
            if len(tl) > 1:
                print(f"  {rid}: knobs move during the render (crossover sweep); no stock pair")
                continue
            s, notes, uses_key = map_mbcomp(tl[0][1])
            if s is None:
                print(f"  {rid}: {notes[0]}; no stock pair")
                continue
            actual = s.pop("_actual", "1.5:1 at 0 dB")
            s["MUP"] = 0          # set at render time for equal loudness (run_job)
            sched = [(0, s)]
            effect = "comp"
            settings = describe_comp(s) + f" ({actual})"
            stitle = f"Stock comp: {SM.RATIOS[s['RAT']]:g}:1 at THR {s['THR']}" + (", keyed" if uses_key else "")
        key = (effect, r["i"], r["key"] if uses_key else None, r["tail"], r["vol"], r["mix"], r["gain"], json.dumps(sched))
        if key in by_key:
            by_key[key]["pairs"].append(rid)
            by_key[key]["ptitles"].append(title)
            by_key[key]["notes"].update(notes)
            continue
        sid = "stock_" + rid
        job = dict(id=sid, effect=effect, dry=str(dry), key=str(out / r["key"]) if uses_key and r["key"] else None,
                   tail=r["tail"], vol=r["vol"], mix=r["mix"], bpm=r["bpm"], schedule=sched,
                   out=str(out / "wav" / f"{sid}.wav"))
        if r["gain"]:
            job["gain"] = r["gain"]
        if effect == "comp":
            job["match"] = str(out / path)
            job["match_sha"] = hashlib.sha1(open(out / path, "rb").read()).hexdigest()
        ent = dict(job=job, gid=gid, title=stitle, settings=settings, pairs=[rid], ptitles=[title], notes=set(notes))
        by_key[key] = ent
        jobs.append(ent)

    # reference group: the stock reverb (no new version yet), settings of the T3.5 takes. The stock
    # chorus is now paired with the flanger renders (2026-10-06).
    ref = [
        ("ref_reverb", "clave.wav", "Stock reverb (emulated), for reference",
         "No new reverb yet (G1). PRE 0, FREQ 64, GAIN 64, HPF 0, LPF 127 as in the T3.5 takes reverb_01 and "
         "reverb_02; send mix with VOL 64 (dry + 0.5 x wet); 4 s of tail.",
         [("stock_reverb_d64", "Stock reverb: DEC 64", dict(PRE=0, DEC=64, FREQ=64, GAIN=64, HPF=0, LPF=127)),
          ("stock_reverb_d127", "Stock reverb: DEC 127", dict(PRE=0, DEC=127, FREQ=64, GAIN=64, HPF=0, LPF=127))],
         "reverb", 4.0),
    ]
    for gid, dry, title, desc, items, effect, tail in ref:
        stock_rows.append(["group", gid, f"dry/{dry}", title, "", desc])
        for sid, stitle, kn in items:
            job = dict(id=sid, effect=effect, dry=str(out / "dry" / dry), key=None, tail=tail, vol=64.0,
                       mix="send", bpm=120.0, schedule=[(0, kn)], out=str(out / "wav" / f"{sid}.wav"))
            settings = ", ".join(f"{k} {v}" for k, v in kn.items())
            jobs.append(dict(job=job, gid=gid, title=stitle, settings=settings, pairs=[], ptitles=[], notes=set()))

    cache_path = out / "stock_cache.json"
    try:
        cache = json.load(open(cache_path))
    except (OSError, ValueError):
        cache = {}
    def fresh(j):
        c = cache.get(j["id"])
        return isinstance(c, dict) and c.get("hash") == job_hash(j) and Path(j["out"]).exists()
    todo = [e["job"] for e in jobs if a.force or not fresh(e["job"])]
    # tidy: stock files and cache entries that no current render uses
    live = {e["job"]["id"] for e in jobs}
    for f in (out / "wav").glob("stock_*.wav"):
        if f.stem not in live:
            f.unlink()
    cache = {k: v for k, v in cache.items() if k in live}
    print(f"stock renders: {len(jobs)} files, {len(todo)} to render ({a.jobs} processes)")
    t0 = time.time()
    if todo:
        with ProcessPoolExecutor(max_workers=a.jobs) as ex:
            for j, res in zip(todo, ex.map(run_job, todo)):
                print(f"  {res['id']}: {res['seconds']:.1f} s, {res['insns']:.0f} insns/block, "
                      f"peak {20 * math.log10(res['peak'] + 1e-12):.1f} dBFS"
                      + (" (CLIPS)" if res["peak"] > 1.0 else "")
                      + (f", MUP {res['mup']} (level vs new {res['level_diff']:+.2f} dB)" if "mup" in res else "")
                      + f", {res['secs']:.1f} s")
                cache[j["id"]] = dict(hash=job_hash(j), result=res)
        json.dump(cache, open(cache_path, "w"), indent=1)
    print(f"  done in {time.time() - t0:.1f} s")

    with open(out / "manifest_stock.tsv", "w") as fh:
        for row in stock_rows:
            fh.write("\t".join(row) + "\n")
        for e in jobs:
            notes = sorted(e["notes"])
            res = cache[e["job"]["id"]]["result"]
            if "mup" in res:
                e["settings"] = e["settings"].replace("MUP 0,", f"MUP {res['mup']},") + \
                    f"; MUP +{24 * res['mup'] / 127:.1f} dB, set so the stock render is as loud (RMS) as the new one" + \
                    (f" (ends {res['level_diff']:+.1f} dB: " + ("capped to keep the peak below -0.5 dBFS; the stock output clips at 1.0"
                     if res.get("capped") else "stock makeup cannot go below 0 dB" if res["mup"] == 0 else "nearest step")
                     + ")" if abs(res["level_diff"]) > 0.3 else "")
            desc = "The DN2's own " + {"delay": "delay", "comp": "master compressor", "chorus": "chorus (the slot's stock effect)",
                                       "reverb": "reverb"}[e["job"]["effect"]] \
                + " code (OS 1.12) in the SHARC emulator, same dry file. " \
                + ("Closest stock knobs to the new render(s): " + "; ".join(e["ptitles"]) + ". " if e["pairs"] else "") \
                + ("Not available on the stock effect: " + "; ".join(notes) + "." if notes else "")
            if e["ptitles"]:
                more = len(e["ptitles"]) - 1
                e["title"] = "Stock for: " + e["ptitles"][0] + (f" (+{more})" if more else "")
            fh.write("\t".join(["stock", e["gid"], f"wav/{e['job']['id']}.wav", e["title"], e["settings"],
                                desc, ",".join(e["pairs"])]) + "\n")
    print(f"wrote {out / 'manifest_stock.tsv'}")


if __name__ == "__main__":
    main()
