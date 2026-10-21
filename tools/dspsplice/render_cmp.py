#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""T6.3 CMP0 listening renders: the stock master compressor (FTD1's image, on the owner's unit today) against
CMP0's 3-band compressor, both as the unit would play them: the whole DSP block handler 0x1c9d6b in
tools/sharcvm, the same input and the same frames, the master after the compressor included.

    python3 tools/dspsplice/render_cmp.py [--jobs 6]

Writes out/T6.3/CMP0/ab/: wav/*.wav (24-bit, 48 kHz), renders.json and index.html (a standalone A/B page in
the look of out/T3.11/ab.html; open it through `python3 -m http.server` in that folder for gapless switching,
or as a file). Nothing here touches fx/'s shared A/B page.

The input file goes in through the external input (IN L/R level 127, centred, routed to the compressor),
scaled so the compressor's input peaks at -12 dBFS; pattern volume 100, master overdrive 0, no send FX.
The dry reference is MIX 0 (the stock compressor's dry path: bit for bit the same in FTD1 and CMP0,
verify_cmp.py (c)). Gain-reduction statistics come from the meter word the stock bar reads (0x257c1c) and,
for CMP0, each band's y2.
"""
import argparse
import html
import json
import math
import struct
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "tools/sharcvm"))

OUT = ROOT / "out/T6.3/CMP0/ab"
DRY = ROOT / "out/T3.11/dry"
CMP0 = ROOT / "out/T6.3/CMP0-dsp"
FTD1 = ROOT / "out/T3.8/FTD1"
SLOT = 0x2e1c40
GR_Y2 = 0x257c1c
COMP_RET = 0x1c2163
TARGET_PEAK = 0.25          # the compressor's input peak (-12 dBFS)

# the compressor knobs as the stock page shows them (RAT as the index into 1.5 2 3 4 6 8 16 20)
SETTINGS = {
    "lh": dict(title="LIGHTHOUSE-like: new-kit defaults, MIX 39",
               knobs=dict(THR=32, ATK=24, REL=32, MUP=64, RAT=3, MIX=39),
               desc="THR 32 (-44.9 dB), ATK 24 (3.7 ms), REL 32 (73 ms), MUP 64 (+12.1 dB), RAT 4:1, MIX 39 (wet -20 dB, "
                    "dry -3.8 dB): heavy compression blended under the dry, as a parallel compressor. LIGHTHOUSE's MIX is 39 "
                    "(T6.4 plan); its other compressor values are taken to be the defaults [inferred]."),
    "heavy": dict(title="Heavy and fast: 20:1, ATK 0, MIX 127",
                  knobs=dict(THR=64, ATK=0, REL=16, MUP=40, RAT=7, MIX=127),
                  desc="THR 64 (-29.8 dB), ATK 0 (0.1 ms), REL 16 (21 ms), MUP 40 (+7.6 dB), RAT 20:1, MIX 127 (all wet): "
                       "a limiter-like setting, where the 3-band and the full-band designs differ most."),
    "heavy_par": dict(title="Heavy and fast, half wet (MIX 64)",
                      knobs=dict(THR=64, ATK=0, REL=16, MUP=40, RAT=7, MIX=64),
                      desc="The same at MIX 64 (wet and dry each -6 dB): parallel compression. CMP0's dry is the sum of its "
                           "bands (the input through the crossover's allpass), so dry and wet stay in phase."),
    "glue": dict(title="Gentle glue: 2:1, MIX 127",
                 knobs=dict(THR=85, ATK=40, REL=44, MUP=12, RAT=1, MIX=127),
                 desc="THR 85 (-19.8 dB), ATK 40 (10 ms), REL 44 (151 ms), MUP 12 (+2.3 dB), RAT 2:1, MIX 127."),
    "kit127": dict(title="New-kit defaults, all wet (MIX 127)",
                   knobs=dict(THR=32, ATK=24, REL=32, MUP=64, RAT=3, MIX=127),
                   desc="THR 32 (-44.9 dB), ATK 24 (3.7 ms), REL 32 (73 ms), MUP 64 (+12.1 dB), RAT 4:1, MIX 127: the "
                        "defaults with only the compressed signal. On sustained, tonal sounds every band sits over the "
                        "threshold, each is squeezed towards it and each gets the full makeup, so the three bands add "
                        "up louder than the one full-band compressor [inferred cause]."),
}
GROUPS = [("mix", "lh"), ("mix", "heavy"), ("mix", "glue"), ("drums", "lh"), ("drums", "heavy"), ("drums", "heavy_par"),
          ("chords", "lh"), ("chords", "kit127"), ("pad", "kit127")]
SECONDS = {"mix": 12.0, "drums": 9.0, "chords": 12.0, "pad": 9.0}
INPUT_NAME = {"mix": "a full mix (out/T3.11/dry/mix.wav)", "drums": "a drum loop (out/T3.11/dry/drums.wav)",
              "chords": "held chords (out/T3.11/dry/chords.wav)", "pad": "a chord pad (out/T3.11/dry/pad.wav)"}


def words_for(knobs):
    w = dict(IN_L=127, IN_R=127, IN_L_PAN=64, IN_R_PAN=64, PATTERN_VOL=100, MASTER_OD=0, DUAL=0,
             IN_CHR_L=0, IN_CHR_R=0, IN_DEL_L=0, IN_DEL_R=0, IN_REV_L=0, IN_REV_R=0, CHR_VOL=0, DEL_VOL=0, REV_VOL=0,
             CMP_SCS=0, CMP_SCF=80)
    for k, v in knobs.items():
        w["CMP_" + k] = v
    return w


def render_one(job):
    """(input name, setting id or None for dry, which image) -> the handler output and GR statistics"""
    import vmrun as V
    from fx import read_wav
    name, sid, which = job
    info = json.loads(((CMP0 if which == "CMP0" else FTD1) / "build.json").read_text())
    stream = ((CMP0 if which == "CMP0" else FTD1) / "section7.bin").read_bytes()
    import hashlib
    if hashlib.sha256(stream).hexdigest() != info["section7"]["sha256"]:   # the snapshot is named after it
        raise SystemExit(f"{which}: section7.bin is not the image its build.json describes: run build.py {which}")
    cache =("t63-CMP0-" if which == "CMP0" else "t38-FTD1-") + info["section7"]["sha256"][:12]
    L, R, sr = read_wav(DRY / f"{name}.wav")
    n = int(SECONDS[name] * sr) // 32 * 32
    L, R = L[:n], R[:n]
    g = TARGET_PEAK / (1.41254 * max(max(abs(x) for x in L), max(abs(x) for x in R)))
    L, R = [g * x for x in L], [g * x for x in R]
    knobs = SETTINGS[sid]["knobs"] if sid else dict(THR=32, ATK=24, REL=32, MUP=64, RAT=3, MIX=0)
    vm = V.candidate_vm(stream, cache=cache)
    r = V.Runner(vm, words_for(knobs))
    r.d.set_raw(CMP_TRACKS=0, CMP_IN_FX=0x1)
    meters, bands, cin_peak = [], [], 0.0
    oL, oR = [], []

    def at_ret():
        meters.append(vm.rdf(GR_Y2, 1)[0])
        if which == "CMP0":
            bands.append(vm.rdf(SLOT + 44, 3))
    for i in range(0, n, 32):
        spi = vm.rd32(V.fullblock.SPI_HALF)
        vm.write(V.fullblock.RX_BASE + 0x1000 * spi, bytes(r.d.frame))
        h = vm.rd32(V.fullblock.DMA_HALF)
        vm.write(V.fullblock.CODEC_IN + 0x100 * h,
                 b"".join(V.fullblock.q31(v).to_bytes(4, "little") for pair in zip(L[i:i + 32], R[i:i + 32]) for v in pair))
        r._call_probed([(COMP_RET, at_ret)])
        oL += vm.rdf(V.fullblock.OUT_FLOATS, 32)
        oR += vm.rdf(V.fullblock.OUT_FLOATS + 0x80, 32)
    cin_peak = TARGET_PEAK
    skip = 75                    # the first 50 ms: the stock one-poles start from 0 at boot
    m = meters[skip:]
    res = {"name": name, "setting": sid, "which": which, "input_gain_db": round(20 * math.log10(g), 2),
           "comp_input_peak_dbfs": round(20 * math.log10(cin_peak), 1),
           "gr_mean_db": round(sum(m) / len(m), 2), "gr_max_db": round(min(m), 2)}
    if bands:
        b = bands[skip:]
        res["band_gr_mean_db"] = [round(sum(x[k] for x in b) / len(b), 2) for k in range(3)]
        res["band_gr_max_db"] = [round(min(x[k] for x in b), 2) for k in range(3)]
    import memguard
    memguard.check()
    return res, oL, oR


def rms_db(L, R):
    e = sum(x * x for x in L) + sum(x * x for x in R)
    return 10 * math.log10(e / (len(L) + len(R)) + 1e-30)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs", type=int, default=6)
    ap.add_argument("--page-only", action="store_true", help="rewrite index.html from renders.json")
    a = ap.parse_args()
    if a.page_only:
        rj = json.loads((OUT / "renders.json").read_text())
        write_page(rj["groups"], rj.get("images"))
        return
    from fx import write_wav
    (OUT / "wav").mkdir(parents=True, exist_ok=True)
    jobs = sorted({(n, None, "FTD1") for n, _ in GROUPS} | {(n, s, w) for n, s in GROUPS for w in ("FTD1", "CMP0")},
                  key=lambda j: (j[0], j[1] or "", j[2]))
    t0 = time.time()
    results = {}
    import memguard
    with ProcessPoolExecutor(max_workers=a.jobs, max_tasks_per_child=memguard.TASKS_PER_CHILD) as ex:
        for job, (res, oL, oR) in zip(jobs, memguard.pmap(ex, render_one, jobs)):
            name, sid, which = job
            fid = f"{name}_dry" if sid is None else f"{name}_{sid}_{'stock' if which == 'FTD1' else 'cmp0'}"
            write_wav(OUT / "wav" / f"{fid}.wav", oL, oR)
            res.update(file=f"wav/{fid}.wav", rms_dbfs=round(rms_db(oL, oR), 2),
                       peak_dbfs=round(20 * math.log10(max(max(map(abs, oL)), max(map(abs, oR))) + 1e-30), 2))
            results[fid] = res
            print(f"  {fid}: rms {res['rms_dbfs']} dBFS, peak {res['peak_dbfs']}, GR mean {res['gr_mean_db']} max "
                  f"{res['gr_max_db']}{'  bands ' + str(res.get('band_gr_mean_db')) if 'band_gr_mean_db' in res else ''}",
                  flush=True)
    groups = []
    for name, sid in GROUPS:
        st = SETTINGS[sid]
        k = st["knobs"]
        ks = f"THR {k['THR']}, ATK {k['ATK']}, REL {k['REL']}, MUP {k['MUP']}, RAT {[1.5, 2, 3, 4, 6, 8, 16, 20][k['RAT']]}:1, MIX {k['MIX']}"
        d, s_, c = results[f"{name}_dry"], results[f"{name}_{sid}_stock"], results[f"{name}_{sid}_cmp0"]
        items = [
            {"id": "dry", "kind": "dry", "file": d["file"], "title": "Dry (MIX 0)", "settings": "MIX 0",
             "desc": "The compressor at MIX 0: the input through the master as it is (bit for bit the same on FTD1 and CMP0).",
             "rms": d["rms_dbfs"]},
            {"id": "stock", "kind": "stock", "file": s_["file"], "title": "Stock compressor (FTD1, GT01)", "settings": ks,
             "desc": f"The Digitone II's own master compressor (OS 1.12) as on your unit today. Gain reduction (the stock bar's "
                     f"meter) mean {s_['gr_mean_db']} dB, deepest {s_['gr_max_db']} dB.", "rms": s_["rms_dbfs"], "pair": 2},
            {"id": "cmp0", "kind": "new", "file": c["file"], "title": "CMP0: 3 bands", "settings": ks + "; crossovers 196 Hz / 2.61 kHz",
             "desc": f"CMP0: the same knobs on all three bands (196 Hz and 2.61 kHz crossovers), each band the stock design. "
                     f"Gain reduction mean low {c['band_gr_mean_db'][0]}, mid {c['band_gr_mean_db'][1]}, high "
                     f"{c['band_gr_mean_db'][2]} dB; deepest low {c['band_gr_max_db'][0]}, mid {c['band_gr_max_db'][1]}, high "
                     f"{c['band_gr_max_db'][2]} dB.", "rms": c["rms_dbfs"], "pair": 1},
        ]
        lv = (f" Unmatched, at these knobs CMP0's RMS is {c['rms_dbfs'] - s_['rms_dbfs']:+.1f} dB and its peak "
              f"{c['peak_dbfs'] - s_['peak_dbfs']:+.1f} dB against stock's.")
        groups.append({"id": f"{name}_{sid}", "title": f"{st['title']}, on {name}",
                       "desc": f"{st['desc']} Input: {INPUT_NAME[name]}.{lv}", "match": True, "items": items,
                       "d_rms_db": round(c["rms_dbfs"] - s_["rms_dbfs"], 2), "d_peak_db": round(c["peak_dbfs"] - s_["peak_dbfs"], 2)})
    # the images rendered (render_one refuses a section7.bin whose sha256 is not its build.json's)
    images = {w: json.loads((d / "build.json").read_text())["section7"]["sha256"] for w, d in (("CMP0", CMP0), ("FTD1", FTD1))}
    (OUT / "renders.json").write_text(json.dumps({"results": results, "groups": groups, "images": images,
                                                 "seconds": round(time.time() - t0)}, indent=1))
    write_page(groups, images)
    print(f"wrote {OUT / 'index.html'} ({round(time.time() - t0)} s)")


def write_page(groups, images=None):
    """the page: out/T3.11/ab.html's style and player, cut down to dry / stock / CMP0"""
    src = (ROOT / "out/T3.11/ab.html").read_text()
    css = src[src.index("<style>"):src.index("</style>") + len("</style>")]
    js0 = src.index("// Two engines.")
    js1 = src.index("</script>", js0)
    player = src[js0:js1]
    player = player.replace('stock: "Stock DN2 (emulated)"', 'stock: "Stock (FTD1)"').replace('new: "New"', 'new: "CMP0"')
    player = player.replace('stock: "STOCK, EMULATED"', 'stock: "STOCK"').replace('new: "NEW"', 'new: "CMP0"')
    rest = src[src.index("const root", js0):src.index("</script>", js0)]
    rest = rest.replace("For gapless switching run make listen in fx/ and open the address it prints.",
                        "For gapless switching run python3 -m http.server in this folder and open the address it prints.")
    player = player[:player.index("const root")] + rest
    page = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>CMP0 compressor A/B</title>
{css}
</head>
<body>
<main>
<h1>CMP0: the 3-band compressor against the stock one</h1>
<p class="lede">Rendered on 2026-10-13 from the DSP images themselves: <span class="badge stock">STOCK</span> is FTD1 (the DSP on your unit
today, with Elektron's master compressor), <span class="badge new">CMP0</span> is the same image with the 3-band compressor in its place.
Both run the whole DSP block handler in the SHARC interpreter (tools/sharcvm), so the master after the compressor is included; the
input comes in through the external input at -12 dBFS peak into the compressor, pattern volume 100. In CMP0 all three bands take the
stock knobs (THR, ATK, REL, MUP, RAT); crossovers fixed at 196 Hz and 2.61 kHz. SCF is at the stock default 80 (a 119 Hz
high-pass in the stock detector), as in a new kit; CMP0 ignores SCF and runs its detectors at the neutral 64, as you decided. Buttons (or keys 1-3) switch while it plays; keys:
<b>d</b> dry, <b>s</b> stock, <b>n</b> CMP0, space play/pause. "Match loudness" (on) plays every version at the quietest one's RMS.</p>
<p class="note"><b>Loudness.</b> At the same knobs, how loud CMP0 is next to stock depends on the sound. On drums and the full mix it
came out between 3 dB quieter and 2 dB louder (RMS). On sustained, tonal sounds (the chords and pad groups) it is louder: in a wider
check, up to about +5 dB RMS with peaks up to about +8 dB at heavy settings with high MUP, and about +4 dB at the new-kit defaults with
MIX 127. The master's ceiling is the same for both. Each group below gives its own unmatched difference; "Match loudness" hides it
while you compare the sound.</p>
<p class="note"><b>Emulated, not recorded.</b> These are the DSP's own instructions run in an interpreter, so they are what the unit
computes, but nobody has heard CMP0 on the unit yet. Over http (<code>python3 -m http.server</code> in this folder) switching is gapless.</p>
<p class="help" id="mode"></p>
<div id="groups"></div>
<p class="help">Files: <code>wav/</code> (24-bit, 48 kHz), <code>renders.json</code> (levels and gain reduction). Made by
<code>tools/dspsplice/render_cmp.py</code>.{(" DSP images (section 7 sha256): CMP0 <code>" + images["CMP0"][:16] + "...</code>, FTD1 <code>" + images["FTD1"][:16] + "...</code>.") if images else ""}</p>
</main>
<script>
const DATA = {json.dumps(groups, indent=1)};
{player}</script>
</body>
</html>
"""
    (OUT / "index.html").write_text(page)


if __name__ == "__main__":
    main()
