#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""make_ab.py - build the T3.11 A/B listening page from manifest.tsv (written by renders.sh).

    python3 make_ab.py OUTDIR      -> OUTDIR/ab.html (relative links to dry/, wav/, stock/)

Stdlib only. Each group plays all its versions in sync, muted except the selected one, so switching
is instant. "Match loudness" turns the louder versions down to the quietest one's RMS.
Each group holds three kinds of version: the dry input, "stock DN2 (emulated)" renders (the unit's own
effect code run in the T3.6 SHARC interpreter, from manifest_stock.tsv, written by stock_renders.py)
and the new effect's renders; the TAPE groups (T5.2) add a fourth, the one-head tape delay as it
runs on the unit (FTD1), from manifest rows of kind "tape". Stock recordings (T3.5) dropped into OUTDIR/stock/ appear in their own group.
"""
import html
import json
import os
import re
import subprocess
import sys
import wave


def rms_db(path):
    """RMS in dBFS of a 16- or 24-bit PCM WAV (every 5th frame, both channels)."""
    try:
        w = wave.open(path)
    except (wave.Error, EOFError):
        return None
    sw, ch, n = w.getsampwidth(), w.getnchannels(), w.getnframes()
    data = w.readframes(n)
    w.close()
    step = sw * ch * 5
    acc, cnt = 0.0, 0
    full = float(1 << (8 * sw - 1))
    for off in range(0, len(data) - sw * ch + 1, step):
        for c in range(ch):
            b = data[off + c * sw: off + (c + 1) * sw]
            v = int.from_bytes(b, "little", signed=True) / full
            acc += v * v
            cnt += 1
    if not cnt or acc <= 0:
        return -120.0
    import math
    return 10 * math.log10(acc / cnt)


def band_levels(out, files, xlo=68, xhi=44):
    """RMS of the three compressor bands (dB) of each file, split by the compressor's own crossover
    (fxrender -bands); cached by path, size and mtime in OUTDIR/band_cache.json."""
    exe = os.path.join(out, "bin", "fxrender")
    cpath = os.path.join(out, "band_cache.json")
    try:
        cache = json.load(open(cpath))
    except (OSError, ValueError):
        cache = {}
    res = {}
    for f in files:
        full = os.path.join(out, f)
        st = os.stat(full)
        k = f"{f}|{st.st_size}|{int(st.st_mtime)}|{xlo}|{xhi}"
        if k not in cache:
            o = subprocess.check_output([exe, "-bands", "-i", full, "-p", f"XLO={xlo}", "-p", f"XHI={xhi}"])
            cache[k] = [float(x) for x in o.split()]
        res[f] = cache[k]
    json.dump(cache, open(cpath, "w"))
    return res


BAND_NOTE = ("Compressing one band changes that band's level against the other two, so a heavy single-band "
             "setting can sound like an EQ on that band; the band levels below show by how much, and MUP gives "
             "the band its level back.")


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "../out/T3.11"
    groups, order = {}, []
    lines = list(open(os.path.join(out, "manifest.tsv")))
    stock_tsv = os.path.join(out, "manifest_stock.tsv")
    if os.path.exists(stock_tsv):
        lines += list(open(stock_tsv))
    for line in lines:
        f = line.rstrip("\n").split("\t")
        if f[0] == "group":
            gid, dry, title, desc = f[1], f[2], f[3], f[5]
            groups[gid] = {"id": gid, "title": title, "desc": desc, "match": False,
                           "items": [{"id": "dry", "kind": "dry", "file": dry, "title": "Dry", "settings": "",
                                      "desc": "The input, unprocessed."}]}
            order.append(gid)
        elif f[0] in ("render", "stock", "tape", "se01", "sc01", "cmp4"):
            gid, path, title, settings, desc = f[1], f[2], f[3], f[4], f[5]
            if gid not in groups:
                continue                         # a stale stock row (its group was removed): make stock redoes them
            it = {"id": os.path.splitext(os.path.basename(path))[0],
                  "kind": {"render": "new", "stock": "stock", "tape": "tape", "se01": "se01", "sc01": "sc01", "cmp4": "cmp4"}[f[0]],
                  "file": path, "title": title, "settings": settings, "desc": desc}
            if f[0] == "stock":
                it["pairs"] = [x for x in (f[6] if len(f) > 6 else "").split(",") if x]
            if f[0] == "render" and (it["id"].startswith("mb_") or it["id"].startswith("fl_") or it["id"].startswith("flu_")):
                groups[gid]["match"] = True      # compressor and flanger groups start level-matched
            if f[0] == "render" and it["id"].startswith("mb_"):
                groups[gid]["comp"] = True
            if f[0] == "render" and re.search(r"-p LSTN=[1-3]", settings):
                it["solo"] = True                # band solos: matched with the dry gain, not as a target
            groups[gid]["items"].append(it)
    # display order: dry, stock, new; a new render points at its stock counterpart
    for g in groups.values():
        rank = {"dry": 0, "stock": 1, "tape": 2, "se01": 2.5, "sc01": 2.7, "cmp4": 2.8, "new": 3}
        g["items"].sort(key=lambda it: rank[it["kind"]])
        for i, it in enumerate(g["items"]):
            if it["kind"] == "stock":
                for j, jt in enumerate(g["items"]):
                    if jt["id"] in it["pairs"]:
                        jt["pair"] = i
                        it.setdefault("pairIdx", []).append(j)
    stock_dir = os.path.join(out, "stock")
    if os.path.isdir(stock_dir):
        files = sorted(x for x in os.listdir(stock_dir) if x.lower().endswith(".wav"))
        if files:
            groups["stock"] = {"id": "stock", "title": "Stock recordings (T3.5)", "match": False,
                               "desc": "Recordings of the unit's own effects, see notes/T3.5-recording-plan.md.",
                               "items": [{"id": x[:-4], "kind": "rec", "file": "stock/" + x, "title": x[:-4],
                                          "settings": "", "desc": "A recording of the unit."} for x in files]}
            order.append("stock")
    for gid in order:
        for it in groups[gid]["items"]:
            r = rms_db(os.path.join(out, it["file"]))
            it["rms"] = r if r is not None else -120.0
    # compressor groups: each version's band levels against the dry (same crossover for all)
    for gid in order:
        g = groups[gid]
        if not g.get("comp"):
            continue
        lv = band_levels(out, [it["file"] for it in g["items"]])
        dry = lv[g["items"][0]["file"]]
        for it in g["items"][1:]:
            if it.get("solo") or it["id"] == "mb_xsweep":
                continue
            d = [a - b for a, b in zip(lv[it["file"]], dry)]
            it["bands"] = "Band levels vs dry (RMS, split at 196 Hz and 2.6 kHz): low %+.1f dB, mid %+.1f dB, high %+.1f dB" % tuple(d)
    data = [groups[g] for g in order]
    page = TEMPLATE.replace("/*DATA*/", json.dumps(data, indent=1)).replace("/*BANDNOTE*/", html.escape(BAND_NOTE))
    with open(os.path.join(out, "ab.html"), "w") as fh:
        fh.write(page)
    print(f"{os.path.join(out, 'ab.html')}: {len(data)} groups, {sum(len(g['items']) for g in data)} files")


TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>FX workbench A/B</title>
<style>
:root { --bg:#fafaf8; --fg:#1d1d1b; --muted:#6b6b66; --card:#ffffff; --line:#e3e2dc; --accent:#b4501e; --accent-fg:#ffffff; --btn:#f0efea; --stock:#2f6690; --stock-fg:#ffffff; --tape:#5b7f3a; --tape-fg:#ffffff; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) { --bg:#161615; --fg:#ecebe6; --muted:#9a9993; --card:#1f1f1d; --line:#33332f; --accent:#e07a45; --accent-fg:#161615; --btn:#2a2a27; --stock:#6fa8d6; --stock-fg:#161615; --tape:#9cc27a; --tape-fg:#161615; } }
:root[data-theme="dark"] { --bg:#161615; --fg:#ecebe6; --muted:#9a9993; --card:#1f1f1d; --line:#33332f; --accent:#e07a45; --accent-fg:#161615; --btn:#2a2a27; --stock:#6fa8d6; --stock-fg:#161615; --tape:#9cc27a; --tape-fg:#161615; }
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--fg); font: 15px/1.45 -apple-system, BlinkMacSystemFont, "Helvetica Neue", Arial, sans-serif; }
main { max-width: 900px; margin: 0 auto; padding: 24px 16px 64px; }
h1 { font-size: 22px; margin: 0 0 4px; }
.lede { color: var(--muted); margin: 0 0 20px; }
.card { background: var(--card); border: 1px solid var(--line); border-radius: 10px; padding: 16px; margin: 0 0 16px; }
.card h2 { font-size: 17px; margin: 0 0 4px; }
.card .gdesc { color: var(--muted); margin: 0 0 12px; font-size: 14px; }
.versions { margin: 0 0 12px; }
.vrow { display: flex; gap: 8px; align-items: flex-start; margin: 0 0 6px; }
.vrow .lab { font-size: 12px; color: var(--muted); flex: 0 0 132px; padding-top: 7px; }
.vrow .btns { display: flex; flex-wrap: wrap; gap: 6px; flex: 1 1 auto; min-width: 0; }
@media (max-width: 560px) { .vrow { flex-direction: column; gap: 2px; } .vrow .lab { flex-basis: auto; padding-top: 0; } }
.versions button.stock { border-style: dashed; border-color: var(--stock); }
.versions button.stock.on { background: var(--stock); color: var(--stock-fg); border-style: solid; }
.badge { display: inline-block; font-size: 11px; font-weight: 600; letter-spacing: .02em; padding: 1px 6px; border-radius: 4px; margin-right: 6px; vertical-align: 1px; }
.badge.stock { background: var(--stock); color: var(--stock-fg); }
.badge.new { background: var(--accent); color: var(--accent-fg); }
.badge.dry, .badge.rec { background: var(--btn); color: var(--fg); border: 1px solid var(--line); }
.badge.tape { background: var(--tape); color: var(--tape-fg); }
.versions button.tape { border-style: dotted; border-color: var(--tape); }
.versions button.tape.on { background: var(--tape); color: var(--tape-fg); border-style: solid; }
.info .lvl { color: var(--muted); font-size: 12px; margin-top: 4px; font-variant-numeric: tabular-nums; }
.note { border-left: 3px solid var(--stock); padding: 6px 10px; margin: 0 0 16px; font-size: 14px; background: var(--card); }
.versions button { font: inherit; font-size: 13px; padding: 6px 10px; border-radius: 6px; border: 1px solid var(--line); background: var(--btn); color: var(--fg); cursor: pointer; }
.versions button.on { background: var(--accent); color: var(--accent-fg); border-color: var(--accent); }
.versions button .k { opacity: .65; margin-right: 4px; font-variant-numeric: tabular-nums; }
.transport { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.transport .play { font: inherit; font-weight: 600; width: 76px; padding: 6px 0; border-radius: 6px; border: 0; background: var(--fg); color: var(--bg); cursor: pointer; }
.transport input[type=range] { flex: 1 1 200px; min-width: 120px; accent-color: var(--accent); }
.transport .t { font-variant-numeric: tabular-nums; color: var(--muted); font-size: 13px; min-width: 88px; }
.transport label { font-size: 13px; color: var(--muted); white-space: nowrap; }
.info { margin-top: 10px; font-size: 14px; }
.info .settings { font-family: ui-monospace, Menlo, monospace; font-size: 12px; color: var(--muted); word-break: break-word; margin-top: 4px; }
.help { color: var(--muted); font-size: 13px; }
</style>
</head>
<body>
<main>
<h1>FX workbench: A/B</h1>
<p class="lede">Tape delay, 3-band master compressor and the chorus slot's new flanger (T3.11, revised 2026-10-06 after your listening:
one-head tape delay with AGE, resonant loop filters and WID; each compressor band now built like the stock compressor, with MUP;
a flanger/comb in the chorus slot), and since 2026-10-12 <b>TAPE</b>, the tape echo for the delay slot (T5.2: three heads, MODE, INTENSITY, AGE,
a resonant band filter on CENTER and BW, WIDTH and a PAN switch; the groups whose titles start with "TAPE"),
and since 2026-10-23 <b>SPNG</b>, a spring reverb for the reverb slot (T5.5: the groups whose titles start with "SPNG", and
"TAPE into SPNG" with the parked tape echo; the stock reverb next to each SPNG render at the same PRE, DEC, HPF and LPF knobs
after <code>make stock</code>; listening guide <code>SPNG-listen.md</code> next to this page),
rendered on the Mac in 32-sample blocks from synthesised material,
next to the Digitone II's own effects on the same material. In the TAPE groups a fourth row,
<span class="badge tape">FTD1</span>, is the one-head tape delay as it runs on the unit now, at the same head-1 time and feedback per head.
Each group holds the dry input, <span class="badge stock">STOCK</span> renders and <span class="badge new">NEW</span> renders; the buttons (or keys 1-9) switch while it plays.
Keys: <b>d</b> dry, <b>s</b> the stock version that matches the selected new one, <b>t</b> the FTD1 version (TAPE groups), <b>n</b> back to the new one, space play/pause.
"Match loudness" turns louder versions down to the quietest one (by RMS), so louder does not win by default; it starts on in the compressor and flanger groups.</p>
<p class="note"><b>Multiband compression and EQ.</b> /*BANDNOTE*/</p>
<p class="note"><b>"Stock" here is an emulation, not a recording.</b> It is the DN2's own DSP code (OS 1.12: delay, master compressor, chorus, reverb)
running in the SHARC interpreter from T3.6 (<code>tools/sharcvm</code>), at the stock knob values closest to each new render (the mapping is in
<code>notes/T3.11-fx-workbench.md</code>, section 10). T3.7 will check the emulator against recordings of the unit (T3.5); until then it can be wrong in
ways nobody has heard. Recordings dropped into <code>stock/</code> appear as their own group after <code>make renders</code>.</p>
<p class="help" id="mode"></p>
<div id="groups"></div>
<p class="help">Files: <code>dry/</code> (24-bit sources), <code>wav/</code> (new: 16-bit renders; <code>stock_*</code>: 24-bit emulator renders; <code>se_*</code> TAPE, <code>ftd_*</code> FTD1). Built by <code>fx/renders.sh</code>, <code>fx/stock_renders.py</code> (<code>make stock</code>) and <code>fx/make_ab.py</code>. Notes: <code>notes/T3.11-fx-workbench.md</code>, section 12; <code>notes/T5.2-space-echo.md</code>.</p>
</main>
<script>
const DATA = /*DATA*/;
// Two engines. Over http (make listen) every version of a group is decoded with Web Audio and
// played in sync; switching moves a 5 ms gain crossfade, so it is gapless and sample-aligned.
// Opened as a file, browsers block that, so the page falls back to one <audio> element at a time:
// switching seeks the new one to the same time (a short gap).
let ctx = null, webaudio = location.protocol.startsWith("http"), current = null;
function fmt(t) { if (!isFinite(t)) t = 0; const m = Math.floor(t / 60), s = t - 60 * m; return m + ":" + s.toFixed(1).padStart(4, "0"); }
class Group {
  constructor(g, root) {
    this.g = g; this.active = 0; this.playing = false; this.match = false; this.offset = 0;
    const card = document.createElement("section"); card.className = "card";
    card.innerHTML = `<h2></h2><p class="gdesc"></p><div class="versions"></div>
      <div class="transport"><button class="play">Play</button><input type="range" min="0" max="1000" value="0" step="1">
      <span class="t">0:00.0</span><label><input type="checkbox" class="loop" checked> loop</label>
      <label><input type="checkbox" class="match"> match loudness</label></div>
      <div class="info"><div class="title"></div><div class="settings"></div><div class="bands lvl"></div><div class="lvl rmsl"></div></div>`;
    card.querySelector("h2").textContent = g.title;
    card.querySelector(".gdesc").textContent = g.desc;
    this.card = card; root.appendChild(card);
    const vb = card.querySelector(".versions");
    const rows = {};
    const LABELS = { dry: "Dry", stock: "Stock DN2 (emulated)", tape: "FTD1 (one-head tape, on the unit)", se01: "SE01's FDBK law", sc01: "SC01 (FDBK/64 per head)", cmp4: "CMP4 on the unit (C = HPF), same knobs", new: "New", rec: "Recordings" };
    this.buttons = g.items.map((it, i) => {
      if (!rows[it.kind]) {
        const r = document.createElement("div"); r.className = "vrow";
        r.innerHTML = `<span class="lab"></span><div class="btns"></div>`; r.querySelector(".lab").textContent = LABELS[it.kind] || it.kind;
        vb.appendChild(r); rows[it.kind] = r.querySelector(".btns");
      }
      const b = document.createElement("button");
      b.className = it.kind;
      b.innerHTML = i < 9 ? `<span class="k">${i + 1}</span>` : ""; b.appendChild(document.createTextNode(it.title));
      b.onclick = () => { current = this; this.select(i); };
      rows[it.kind].appendChild(b); return b;
    });
    this.lastNew = g.items.findIndex(it => it.kind === "new");
    this.btn = card.querySelector(".play"); this.btn.onclick = () => { current = this; this.toggle(); };
    this.slider = card.querySelector("input[type=range]"); this.tdisp = card.querySelector(".t");
    this.loop = card.querySelector(".loop");
    this.match = !!g.match; card.querySelector(".match").checked = this.match;
    card.querySelector(".match").onchange = e => { this.match = e.target.checked; this.applyGains(0.005); this.applyVolumes(); this.showLevel(); };
    this.slider.oninput = () => { current = this; this.seek(this.slider.value / 1000 * this.duration()); };
    if (!webaudio) {
      this.audios = g.items.map(it => { const a = new Audio(it.file); a.preload = "auto"; return a; });
      this.audios.forEach(a => a.addEventListener("ended", () => { if (a === this.audios[this.active]) this.ended(); }));
      this.applyVolumes();
    }
    this.select(0);
    setInterval(() => this.tick(), 100);
  }
  vol(i) {
    // loudness match: every version down to the quietest one (band solos excluded: they get the dry's gain)
    if (!this.match) return 1;
    const its = this.g.items, r = its.filter(it => !it.solo).map(it => it.rms), lo = Math.min(...r);
    const ref = its[i].solo ? its.find(it => it.kind === "dry") || its[i] : its[i];
    return Math.pow(10, Math.min(0, lo - ref.rms) / 20);
  }
  applyVolumes() { if (this.audios) this.audios.forEach((a, i) => { a.volume = this.vol(i); }); }
  async load() {
    if (this.buffers) return;
    this.btn.textContent = "Loading";
    ctx = ctx || new AudioContext();
    try {
      this.buffers = await Promise.all(this.g.items.map(it => fetch(it.file).then(r => { if (!r.ok) throw new Error(it.file); return r.arrayBuffer(); }).then(b => ctx.decodeAudioData(b))));
    } catch (e) { this.btn.textContent = "Error"; throw e; }
  }
  duration() {
    if (this.buffers) return Math.max(...this.buffers.map(b => b.duration));
    if (this.audios) return Math.max(0.001, ...this.audios.map(a => isFinite(a.duration) ? a.duration : 0));
    return 1;
  }
  time() {
    if (webaudio) return this.playing ? ctx.currentTime - this.t0 : this.offset;
    return this.audios[this.active].currentTime;
  }
  applyGains(ramp) {
    if (!this.gains) return;
    const now = ctx.currentTime;
    this.gains.forEach((gn, i) => {
      gn.gain.cancelScheduledValues(now);
      gn.gain.setValueAtTime(gn.gain.value, now);
      gn.gain.linearRampToValueAtTime(i === this.active ? this.vol(i) : 0, now + ramp);
    });
  }
  select(i) {
    const prev = this.active;
    this.active = i;
    this.buttons.forEach((b, j) => b.classList.toggle("on", j === i));
    const it = this.g.items[i];
    if (it.kind === "new") this.lastNew = i;
    const t = this.card.querySelector(".info .title");
    t.innerHTML = `<span class="badge ${it.kind}">${{dry: "DRY", stock: "STOCK, EMULATED", tape: "FTD1", new: "NEW", rec: "RECORDING"}[it.kind] || ""}</span>`;
    t.appendChild(document.createTextNode(it.desc));
    this.card.querySelector(".info .settings").textContent = it.settings;
    this.card.querySelector(".info .bands").textContent = it.bands || "";
    this.showLevel();
    if (webaudio) { this.applyGains(0.005); return; }
    if (prev !== i && this.audios) {
      const a = this.audios[prev], b = this.audios[i];
      const t = a.currentTime;
      if (this.playing) { b.currentTime = Math.min(t, b.duration || t); b.play().catch(() => {}); a.pause(); }
      else b.currentTime = Math.min(t, b.duration || t);
    }
  }
  showLevel() {
    const it = this.g.items[this.active], g = this.vol(this.active);
    this.card.querySelector(".info .rmsl").textContent = `RMS ${it.rms.toFixed(1)} dBFS` +
      (this.match ? (g < 0.999 ? `, played ${(-20 * Math.log10(g)).toFixed(1)} dB quieter to match the quietest version` + (it.solo ? " (a band solo: same gain as the dry)" : "") : ", the quietest version: played as is") : "");
  }
  jump(kind) {
    const items = this.g.items, cur = items[this.active];
    let j = -1;
    if (kind === "dry") j = items.findIndex(it => it.kind === "dry");
    else if (kind === "tape") j = items.findIndex(it => it.kind === "tape");
    else if (kind === "stock") j = (cur.kind === "new" || cur.kind === "tape") && cur.pair !== undefined ? cur.pair
         : (this.lastNew >= 0 && items[this.lastNew].pair !== undefined ? items[this.lastNew].pair : items.findIndex(it => it.kind === "stock"));
    else if (kind === "new") j = cur.kind === "stock" && cur.pairIdx && !cur.pairIdx.includes(this.lastNew) ? cur.pairIdx[0] : this.lastNew;
    if (j >= 0) this.select(j);
  }
  toggle() { this.playing ? this.pause() : this.play(); }
  async play() {
    groups.forEach(o => { if (o !== this) o.pause(); });
    if (webaudio) {
      await this.load();
      if (ctx.state === "suspended") await ctx.resume();
      this.startAt(this.offset);
    } else {
      this.audios[this.active].play().catch(() => {});
    }
    this.playing = true; this.btn.textContent = "Pause";
  }
  startAt(off) {
    this.stopSources();
    const when = ctx.currentTime + 0.03;
    this.t0 = when - off;
    this.gains = this.buffers.map((b, i) => { const gn = ctx.createGain(); gn.gain.value = i === this.active ? this.vol(i) : 0; gn.connect(ctx.destination); return gn; });
    this.sources = this.buffers.map((b, i) => {
      const s = ctx.createBufferSource(); s.buffer = b; s.connect(this.gains[i]);
      if (off < b.duration) s.start(when, off);
      return s;
    });
  }
  stopSources() {
    if (this.sources) this.sources.forEach(s => { try { s.stop(); } catch (e) {} });
    if (this.gains) this.gains.forEach(gn => gn.disconnect());
    this.sources = null; this.gains = null;
  }
  pause() {
    if (!this.playing) return;
    if (webaudio) { this.offset = this.time(); this.stopSources(); }
    else this.audios.forEach(a => a.pause());
    this.playing = false; this.btn.textContent = "Play";
  }
  seek(t) {
    if (webaudio) { this.offset = t; if (this.playing && this.buffers) this.startAt(t); }
    else this.audios.forEach(a => { a.currentTime = Math.min(t, a.duration || t); });
  }
  ended() {
    if (this.loop.checked && this.playing) this.seek(0);
    else { this.pause(); this.seek(0); }
  }
  tick() {
    const d = this.duration();
    let t = this.time();
    if (webaudio && this.playing && t >= d) { this.ended(); t = this.time(); }
    this.slider.value = Math.round(1000 * Math.min(t, d) / d);
    const known = this.buffers || (this.audios && this.audios.some(a => isFinite(a.duration)));
    this.tdisp.textContent = known ? fmt(t) + " / " + fmt(d) : fmt(t);
  }
}
const root = document.getElementById("groups");
const groups = DATA.map(g => new Group(g, root));
current = groups[0];
document.getElementById("mode").textContent = webaudio
  ? "Gapless mode: all versions of a group are decoded and play in sync (the first Play of a group loads them)."
  : "Opened as a file: switching seeks between players and may leave a short gap. For gapless switching run make listen in fx/ and open the address it prints.";
document.addEventListener("keydown", e => {
  if (!current || (e.target.tagName === "INPUT" && e.target.type !== "range" && e.target.type !== "checkbox")) return;
  if (e.key === " ") { e.preventDefault(); current.toggle(); }
  const n = parseInt(e.key, 10);
  if (n >= 1 && n <= current.g.items.length) current.select(n - 1);
  if (e.key === "d") current.jump("dry");
  if (e.key === "s") current.jump("stock");
  if (e.key === "t") current.jump("tape");
  if (e.key === "n") current.jump("new");
});
</script>
</body>
</html>
"""

if __name__ == "__main__":
    main()
