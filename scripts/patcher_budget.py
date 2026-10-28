#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""patcher_budget.py: the DSP code of every combination of our four effects, for the patcher's slots
(tools/patcher/dsp-budget.json; notes/DC01-compiler-fix.md sections 4 and 5 measured five of them the same way).

    venv/bin/python scripts/patcher_budget.py [--work out/patcher/budget]

DC02's budget (out/DC02/budget/budget_dc02.py, mode ABs) over all fifteen combinations of CMP3, FLNG, TAPE and
SPNG, in that order: each combination's C files compiled with selcc as build.py does (DT01's include path), the diet
over all of them (the never-called functions dropped, fxmath.h's helpers in one copy: the first file's, the others'
identical copies removed), then tools/dspsplice/peephole.py over the whole combination (the hook stubs' functions as
entries), every function measured (sc01_funcmap, each file linked alone), the stubs as built (390/374 ...), and every
function placed by first-fit decreasing over the four proven regions (83,838 B). A combination fits when every
function is placed. The sources are the ones in the tree now (fx/spring.c is SPNG v2, SP02's).
"""
import sys, json, re, subprocess, itertools
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools/dspsplice")); sys.path.insert(0, str(ROOT / "tools/sharcvm")); sys.dont_write_bytecode = True
import build as BD, peephole as PH, sc01_funcmap as FM, stub
SEL = ROOT / "vendor/selache/target/release"
W = Path(sys.argv[sys.argv.index("--work") + 1]) if "--work" in sys.argv else ROOT / "out/patcher/budget"
W.mkdir(parents=True, exist_ok=True)
OUT = ROOT / "tools/patcher/dsp-budget.json"
PAT = "ABs"                                                # the pass's mode, DC02's
SRC = "tools/dspsplice/src"
INC = [ROOT / SRC / "dt01", ROOT / "fx", ROOT / SRC]
PACKS = {
    "FLNG": dict(files=[f"{SRC}/dt01/flanger.c", f"{SRC}/flg_glue3.c", f"{SRC}/flg_sync.c"],
                 drop=["flanger_man_ms", "flanger_damp_hz", "flanger_rate_hz", "flanger_delay_at"],
                 stubs={"fxh_chorus": ("flg_process", 390), "fxh_chorus_init": ("flg_init", 374)}),
    "CMP3": dict(files=[f"{SRC}/dt01/mbcomp.c", f"{SRC}/cmp_glue.c", f"{SRC}/cmp_tail.c"],
                 drop=["mbcomp_ratio", "mbcomp_gr_db"],
                 stubs={"fxh_comp": ("cmp_process", 390), "fxh_comp_init": ("cmp_init", 372)}),
    "TAPE": dict(files=["fx/spaceecho.c", f"{SRC}/se_glue.c"],
                 drop=["spaceecho_heads", "spaceecho_center_hz", "spaceecho_bw_oct", "spaceecho_q", "spaceecho_trim"],
                 stubs={"fxh_delay": ("se_process", 390), "fxh_delay_init": ("se_init", 380)}),
    "SPNG": dict(files=["fx/spring.c", f"{SRC}/spng_glue.c"],
                 drop=["spring_pre_ms", "spring_rt60_s", "spring_dwel_db", "spring_hpf_hz", "spring_tone_hz",
                       "spring_width", "spring_tens_ms"],
                 stubs={"fxh_reverb": ("spg_process", 390), "fxh_reverb_init": ("spg_init", 378)}),
}
REG = {"block 1 (0x2dd600-0x2e1c36)": 0x2e1c36 - 0x2dd600, "code4 (0x2e2900-0x2e4f46)": 0x2e4f46 - 0x2e2900,
       "code3 (0x308400-0x30ff76)": 0x30ff76 - 0x308400, "code2 (0x310000-0x315f8c)": 0x315f8c - 0x310000}
ORDER = ("CMP3", "FLNG", "TAPE", "SPNG")
COMBOS = [c for n in range(1, 5) for c in itertools.combinations(ORDER, n)]
SELTXT = {}

def selcc(path):
    stem = Path(path).stem
    if path not in SELTXT:
        s = W / "sel" / f"{stem}.s"; s.parent.mkdir(exist_ok=True)
        subprocess.run([SEL / "selcc", "-proc", "ADSP-21569", "-char-size-8", "-O1", *[f"-I{i}" for i in INC], "-S",
                        "-o", s, ROOT / path], check=True)
        SELTXT[path] = s.read_text()
    return SELTXT[path]

def diet(texts, drop):
    """drop the never-called functions; fxmath.h's helpers (fx_*) in one copy, the first file's (in pack order)"""
    parsed = {st: BD.asm_chunks(t, st) for st, t in texts.items()}
    prov = {}
    for st, (h, chunks, tl) in parsed.items():
        for n, body in chunks:
            if n.startswith("fx_") and not body[0].startswith(".GLOBAL"):
                prov.setdefault(n, (st, body))
    out, dropped, removed = {}, [], []
    for st, (h, chunks, tl) in parsed.items():
        keep, ext = [], []
        for n, body in chunks:
            if n in drop:
                dropped.append(f"{st}:{n}"); continue
            if n in prov and prov[n][0] == st:
                body = [f".GLOBAL {n}.;"] + body
            elif n in prov:
                if body != prov[n][1]:
                    raise SystemExit(f"{st}:{n} is not {prov[n][0]}'s text")
                ext.append(f".EXTERN {n}.;"); removed.append(f"{st}:{n}"); continue
            keep.append(body)
        out[st] = "\n".join(ext + h + [ln for b in keep for ln in b] + tl)
    return out, dropped, removed

def sizes(texts, tag):
    d = W / tag; d.mkdir(exist_ok=True)
    res = {}
    for st, t in texts.items():
        (d / f"{st}.s").write_text(t)
        rows, sec = FM.funcmap(d / f"fm_{st}", 0x200000, [d / f"{st}.s"])
        for n, f, a, b in rows:
            res[f"{st}:{n}"] = 2 * (b - a)
    return res

def pack(funcs, room):
    free = dict(room)
    for n, b in sorted(funcs, key=lambda x: -x[1]):
        cand = [r for r in free if free[r] >= b]
        if not cand:
            return None, n
        r = min(cand, key=lambda r: free[r] - b)
        free[r] -= b
    return free, None

rows = []
per_pack = {}
for combo in COMBOS:
    files = [f for p in combo for f in PACKS[p]["files"]]
    drop = [d for p in combo for d in PACKS[p]["drop"]]
    texts = {Path(f).stem: selcc(f) for f in files}
    dtexts, dropped, removed = diet(texts, set(drop))
    stubs = [(s, b) for p in combo for s, (fn, b) in PACKS[p]["stubs"].items()]
    entries = PH.stub_entries([(s, [("c", PACKS[p]["stubs"][s][0], [])]) for p in combo for s in PACKS[p]["stubs"]],
                              stub.SAVE_REGS)
    ntexts, rep = PH.run(dtexts, patterns=PAT, entries=entries)
    if rep["reverted"]:
        raise SystemExit(f"{combo}: blocks put back {rep['reverted'][:3]}")
    tag = "_".join(combo)
    s0, s1 = sizes(dtexts, tag + "_sel"), sizes(ntexts, tag + "_dc")
    f0 = list(s0.items()) + [(f"stubs:{s}", b) for s, b in stubs]
    f1 = list(s1.items()) + [(f"stubs:{s}", b) for s, b in stubs]
    room = sum(REG.values())
    r = {"pack": " + ".join(combo), "room": room}
    for k, fl in (("before", f0), ("peephole", f1)):
        need = sum(b for _, b in fl)
        free, miss = pack(fl, REG)
        r[k] = {"code": need, "spare_or_short": room - need, "packs": free is not None, "first_misfit": miss,
                "free_after": free}
    r["saved"] = r["before"]["code"] - r["peephole"]["code"]
    r["by_file"] = {st: [sum(v for k, v in s0.items() if k.startswith(st + ":")), sum(v for k, v in s1.items() if k.startswith(st + ":"))]
                    for st in dtexts}
    r["sites"] = rep["by_pattern"]; r["dropped"] = dropped; r["shared_copies_removed"] = removed
    rows.append(r)
    print("%-28s before %6d (%+7d %s)   peephole %6d (%+7d %s)   saved %5d   %s" % (
        r["pack"], r["before"]["code"], r["before"]["spare_or_short"], "packs" if r["before"]["packs"] else "no fit",
        r["peephole"]["code"], r["peephole"]["spare_or_short"], "packs" if r["peephole"]["packs"] else "no fit",
        r["saved"], {k: v for k, v in r["by_file"].items()}), flush=True)
json.dump({"patterns": PAT, "regions": REG, "rows": rows,
           "sources": {f: __import__("hashlib").sha256((ROOT / f).read_bytes()).hexdigest()
                       for p in PACKS.values() for f in p["files"]}}, open(W / "budget.json", "w"), indent=1)
room = sum(REG.values())
json.dump({"about": "Our DSP code for each combination of the effects (bytes), placed by first-fit over the four "
                    "proven regions (scripts/patcher_budget.py, DC02's compiler mode)",
           "room": room,
           "combos": [{"effects": [p.lower() for p in r["pack"].split(" + ")], "code": r["peephole"]["code"],
                       "fits": r["peephole"]["packs"]} for r in rows]},
          open(OUT, "w"), indent=1)
print("-> %s" % OUT.relative_to(ROOT))
