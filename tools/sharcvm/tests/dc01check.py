#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""dc01check.py - DC01 (notes/DC01-compiler-fix.md): every effect built twice with selcc, as it is ('sel') and through
tools/dspsplice/peephole.py ('dc'), run on the same inputs in this interpreter: every output sample and the whole state
at the end must be the same, and every line the peephole changed must run.

    python3 tools/sharcvm/tests/dc01check.py [--fx flanger,spring,spaceecho,tapedelay] [--seconds S] [--grid]
                                             [--workers 2] [--quick] [--out out/DC01/dc01check.json]

  flanger    dt01check.py's harness (harness/flgdrv.c; FL02's glue flg_glue3.c + flg_sync.c with -DFLG_NATIVE; DT01's
             flanger src/dt01/flanger.c) and cases: fl02check's free, synced and transition cases; --grid adds the
             grid (DPTH/SPD/WDTH 0, 64, 127, C 0..127; every division at 120 BPM): the worst block of both builds;
  spring     spngcheck.py's harness (harness/spngdrv.c, fx/spring.c as it is when the build starts, copied) and cases
             (the FIFO case: both builds cut the same way);
  spaceecho, tapedelay   fxcheck.py's harness (harness/fxdrv.c with all four effects of fx/, the peephole over all
             four) and the cases below: fxcheck's settings, every MODE and PAN, the extremes, knobs jumping.
Each case runs three times: 'sel', 'dc', and 'dc' with sharcvm's imm_align on (an immediate-offset access then drops the
low bits of its I register: the same result shows every rewritten access had a word-aligned base). Compared, 'dc' and
'dc+align' against 'sel': every output sample (bits), and at the end every data and bss word of the image but the stack
(the harness's statics, the flanger's slot) and the effect's state region. Instructions per block, mean and max.
Coverage: a global label before every line the peephole changed in the 'dc' build (labels take no space; the code is
the same), sharcvm's counters on in the 'dc' run: every changed line must run in some case. Results: --out (JSON).
"""
import argparse
import ctypes as C
import hashlib
import json
import random
import re
import shutil
import struct
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "tools/dspsplice"))
sys.dont_write_bytecode = True
from vm import VM  # noqa: E402,F401
from render_fx import test_signal  # noqa: E402
import memguard  # noqa: E402
import peephole as PH  # noqa: E402

SEL = ROOT / "vendor/selache/target/release"
SRC = ROOT / "tools/dspsplice/src"
HARN = HERE.parent / "harness"
LIBSEL = ROOT / "out/T3.6/libsel/libsel.dlb"
LINK_LDF = ROOT / "vendor/selache/libsel/link.ldf"
OUT = ROOT / "out/DC01/dc01check"
STATE = 0x80100000              # fxdrv's and spngdrv's effect state (DDR)
STATE_BYTES = 8 << 20
FLG_MAGIC = 0x464c4733
VARIANTS = ("sel", "dc", "dca")
PATTERNS = "AB"                 # the pass's mode for the 'dc' builds (--patterns; DC02: ABs)
IMAGE = "DC01"                  # the image whose unreachable changed lines the fxdt build runs (--image)

BUILDS = {
    "flanger": dict(dxe="flg.dxe", inc=[SRC / "dt01", ROOT / "fx", SRC],
                    files=[("flgdrv", HARN / "flgdrv.c", ["-D__SHARC_HARNESS__"], False),
                           ("flg_glue3", SRC / "flg_glue3.c", ["-DFLG_NATIVE"], True),
                           ("flg_sync", SRC / "flg_sync.c", ["-DFLG_NATIVE"], True),
                           ("flanger", SRC / "dt01/flanger.c", [], True)]),
    "spring": dict(dxe="spngdrv.dxe", inc=[ROOT / "fx"],
                   files=[("spngdrv", HARN / "spngdrv.c", ["-D__SHARC_HARNESS__"], False),
                          ("spring", "SPRING_COPY", [], True)]),
    "fx": dict(dxe="fxdrv.dxe", inc=[ROOT / "fx"],
               files=[("fxdrv", HARN / "fxdrv.c", ["-D__SHARC_HARNESS__"], False),
                      ("tapedelay", ROOT / "fx/tapedelay.c", [], True),
                      ("mbcomp", ROOT / "fx/mbcomp.c", ["-DMB_LAW_TABLES"], True),
                      ("flanger", ROOT / "fx/flanger.c", [], True),
                      ("spaceecho", ROOT / "fx/spaceecho.c", [], True)]),
}
BUILDS["fxdt"] = dict(dxe="fxdrv.dxe", inc=[SRC / "dt01", ROOT / "fx", SRC],
                      files=[("fxdrv", HARN / "fxdrv.c", ["-D__SHARC_HARNESS__"], False),
                             ("tapedelay", ROOT / "fx/tapedelay.c", [], False),
                             ("mbcomp", SRC / "dt01/mbcomp.c", ["-DMB_LAW_TABLES"], True),
                             ("flanger", SRC / "dt01/flanger.c", [], True),
                             ("spaceecho", ROOT / "fx/spaceecho.c", [], False)])
BUILD_OF = {"flanger": "flanger", "spring": "spring", "spaceecho": "fx", "tapedelay": "fx",
            "flanger-dt": "fxdt", "mbcomp-dt": "fxdt"}
# DC01's own functions whose changed lines the whole image cannot run (verify_dc01's (c)): the flanger's random-step LFO
# (FL_SHP is pinned to 0 by flanger_unit.h) and the partial-block paths (the DSP always calls with 32 samples); run here
# through fxdrv with DT01's flanger.c and mbcomp.c, when their rewritten text is DC01's
IMAGE_FUNCS = {"flanger": ["fl_lfo_block", "flanger_process"], "mbcomp": ["mbcomp_process"]}
FX_PARAM = {"flanger-dt": "flanger", "mbcomp-dt": "mbcomp"}


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def build(bname):
    """both builds of one harness: -> {variant: dir}, plus the sites of the 'dc' build"""
    b = BUILDS[bname]
    base = OUT / bname
    base.mkdir(parents=True, exist_ok=True)
    files = []
    for stem, path, ex, rw in b["files"]:
        if path == "SPRING_COPY":
            # fx/spring.c may be edited by other work: this run uses one copy throughout
            cp = base / "src"
            cp.mkdir(exist_ok=True)
            for f in ("spring.c", "spring.h", "fxmath.h"):
                shutil.copy(ROOT / "fx" / f, cp / f)
            path = cp / "spring.c"
        files.append((stem, Path(path), ex, rw))
    inc = ([base / "src"] if bname == "spring" else []) + b["inc"]
    flags = ["-proc", "ADSP-21569", "-char-size-8", "-O1"] + [f"-I{i}" for i in inc] + [
        f"-I{ROOT / 'vendor/selache/libsel/include'}"]
    texts = {}
    sel_dir = base / "sel"
    sel_dir.mkdir(parents=True, exist_ok=True)
    for stem, path, ex, rw in files:
        subprocess.check_call([str(SEL / "selcc"), *flags, *ex, "-S", "-o", str(sel_dir / f"{stem}.s"), str(path)])
        texts[stem] = (sel_dir / f"{stem}.s").read_text()
    rewrite = [stem for stem, _, _, rw in files if rw]
    new, rep = PH.run(texts, rewrite=rewrite, patterns=PATTERNS)
    sites = {}
    labeled = dict(new)
    for stem in rewrite:
        lines = PH.site_lines({stem: texts[stem]}, {stem: new[stem]}).get(stem, [])
        txt, labs = PH.add_site_labels(new[stem], lines, f"dcs_{stem}_")
        labeled[stem] = txt
        nl = new[stem].split("\n")
        for lab, li in zip(labs, lines):
            fn = next((m.group(1) for k in range(li, -1, -1) for m in [PH.FUNC_LABEL.match(nl[k])] if m), "?")
            sites[lab] = {"stem": stem, "function": fn, "line": li + 1, "text": nl[li].strip()}
    dirs = {}
    for variant, tx in (("sel", texts), ("dc", labeled)):
        d = base / variant
        d.mkdir(parents=True, exist_ok=True)
        objs = []
        for stem, _, _, _ in files:
            (d / f"{stem}.s").write_text(tx[stem])
            subprocess.check_call([str(SEL / "selas"), "-proc", "ADSP-21569", "-o", str(d / f"{stem}.doj"),
                                   str(d / f"{stem}.s")])
            objs.append(str(d / f"{stem}.doj"))
        subprocess.check_call([str(SEL / "seld"), "-proc", "ADSP-21569", "-T", str(LINK_LDF), "-o", str(d / b["dxe"]),
                               *objs, str(LIBSEL)])
        dirs[variant] = d
    (base / "peephole.json").write_text(json.dumps(rep, indent=1))
    import elf as E
    es, ed = E.read_elf(dirs["sel"] / b["dxe"]), E.read_elf(dirs["dc"] / b["dxe"])
    ds = sorted((s.name, s.addr, len(s.data)) for s in es.sections if s.kind in ("data", "bss"))
    dd = sorted((s.name, s.addr, len(s.data)) for s in ed.sections if s.kind in ("data", "bss"))
    if ds != dd:
        raise SystemExit(f"{bname}: the two builds place their data differently: {ds} / {dd}")
    code = {v: sum(len(s.data) for s in E.read_elf(dirs[v] / b["dxe"]).sections if s.kind == "sw_code")
            for v in ("sel", "dc")}
    pcs = {lab: ed.symbols[lab + "."] for lab in sites}
    same_as_image = None
    image_sites = {}
    if bname == "fxdt":
        # DC01's changed lines in the functions its image cannot run there: each one's basic block, found here with the
        # same instructions, and the label of the same line here (coverage of that block here = of DC01's)
        same_as_image = {}
        lab_of_line = {}
        for stem in IMAGE_FUNCS:
            for lab, st in sites.items():
                if st["stem"] == stem:
                    lab_of_line[(stem, st["line"] - 1)] = lab
        for stem, fns in IMAGE_FUNCS.items():
            img_new = (ROOT / f"out/{IMAGE}/{IMAGE}-dsp/build" / f"{stem}.s").read_text()
            img_old = (ROOT / "out/DT01/DT01-dsp/build" / f"{stem}.s").read_text()
            img_lines = set(PH.site_lines({stem: img_old}, {stem: img_new}).get(stem, []))
            fi_all, _, _ = PH.parse_program({stem: img_new})
            fh_all, _, _ = PH.parse_program({stem: new[stem]})
            for fn in fns:
                same_as_image[f"{stem}:{fn}"] = func_text(new[stem], fn) == func_text(img_new, fn) != []
                fi, fh = fi_all[f"{stem}:{fn}"], fh_all[f"{stem}:{fn}"]
                hblocks = {}
                for (a, b) in fh.blocks:
                    hblocks.setdefault(tuple(x.text for x in fh.ins[a:b]), (a, b))
                for (a, b) in fi.blocks:
                    blk = fi.ins[a:b]
                    ch = [x for x in blk if x.li in img_lines]
                    if not ch:
                        continue
                    hb = hblocks.get(tuple(x.text for x in blk))
                    for x in ch:
                        key = "%s:%s:%d" % (stem, fn, x.li + 1)
                        if hb is None:
                            image_sites[key] = {"text": x.text, "same_block_here": False, "label": None}
                        else:
                            hx = fh.ins[hb[0] + blk.index(x)]
                            image_sites[key] = {"text": x.text, "same_block_here": True,
                                                "label": lab_of_line.get((stem, hx.li))}
    info = {"build": bname, "same_text_as_dc01_image": same_as_image, "dc01_image_sites": image_sites,
            "sources": {str(p.relative_to(ROOT)) if str(p).startswith(str(ROOT)) else str(p): sha(p)
                                        for _, p, _, _ in files},
            "peephole": {k: rep[k] for k in ("sites", "by_pattern", "bytes_saved_estimate", "blocks_checked",
                                             "reverted", "by_file")},
            "image_code_bytes": code, "changed_lines": len(sites)}
    (base / "build.json").write_text(json.dumps(info, indent=1))
    return {"dirs": {k: str(v) for k, v in dirs.items()}, "sites": sites, "pcs": pcs, "info": info}


def func_text(text, fn):
    """the instruction and label lines of one function (from its label to the next function's)"""
    out, on = [], False
    for ln in text.split("\n"):
        m = PH.FUNC_LABEL.match(ln)
        if m and not m.group(1).startswith("dcs_"):
            on = m.group(1) == fn
            continue
        if m:
            continue
        if on and ln.strip() and not ln.strip().startswith((".GLOBAL", ".EXTERN", ".SECTION", ".ENDSEG")):
            out.append(ln.strip())
    return out


def vm_setup(vm, variant):
    lib = vm.lib
    lib.vm_set_imm_align.argtypes = [C.c_void_p, C.c_int]
    lib.vm_misaligned.restype = C.c_uint64
    lib.vm_misaligned.argtypes = [C.c_void_p]
    if variant == "dca":
        lib.vm_set_imm_align(vm.h, 1)
    if variant == "dc":
        lib.vm_set_profile(vm.h, 1)


def image_state(vm, dxe):
    """every data and bss byte of the image, the stack left out"""
    import elf as E
    e = E.read_elf(dxe)
    lo, hi = e.symbols["ldf_stack_space"], e.symbols["ldf_stack_space"] + e.symbols["ldf_stack_length"]
    out = []
    for s in sorted(e.sections, key=lambda s: s.addr):
        if s.kind not in ("data", "bss") or not len(s.data):
            continue
        a, b = s.addr, s.addr + len(s.data)
        if a < hi and lo < b:
            if a < lo:
                out.append(vm.read(a, lo - a))
            if hi < b:
                out.append(vm.read(hi, b - hi))
        else:
            out.append(vm.read(a, b - a))
    return b"".join(out)


def hits_of(vm, pcs):
    out = {}
    for lab, pc in pcs.items():
        pg = vm.pages.get(pc >> 12)
        if pg is not None and pg[pc & 0xfff].hits:
            out[lab] = pg[pc & 0xfff].hits
    return out


# ---------------------------------------------------------------------------------------------------------- the cases

def flanger_cases(seconds, grid):
    import fl02check as F2
    L, R = test_signal(seconds)
    blocks = len(L) // 32
    out = []
    scripts, _ = F2.free_scripts(blocks)
    for name in scripts:
        out.append({"fx": "flanger", "name": "free: " + name, "kind": "free", "blocks": blocks})
    for bpm in (60.0, 120.0, 137.5, 174.0, 30.0, 300.0, 20.0, 400.0):
        for i in range(1, 13):
            if bpm not in (60.0, 120.0, 137.5, 174.0) and i not in (1, 8, 12):
                continue
            out.append({"fx": "flanger", "name": "synced: %s @ %g" % (F2.NAMES[i - 1], bpm), "kind": "synced",
                        "div": i, "bpm": bpm, "blocks": min(blocks, 3600)})
    out.append({"fx": "flanger", "name": "transitions: fl02check's plan (12 s)", "kind": "plan"})
    if grid:
        for dp in (0, 64, 127):
            for sp in (0, 64, 127):
                for c in (0, 12, 32, 64, 82, 100, 127):
                    for wd in (0, 64, 127):
                        out.append({"fx": "flanger", "name": "grid free %d %d %d %d" % (dp, sp, c, wd), "kind": "grid",
                                    "words": [dp << 8, sp << 8, c << 8, wd << 8], "grid": "free"})
        for dp in (0, 64, 127):
            for c in (0, 32, 82, 127):
                for wd in (0, 64, 127):
                    for i, sw in enumerate(F2.SYNC, 1):
                        out.append({"fx": "flanger", "name": "grid synced %d %d %d %d" % (dp, i, c, wd),
                                    "kind": "grid", "words": [dp << 8, sw, c << 8, wd << 8], "grid": "synced"})
    return out


def flanger_script(case, seconds):
    import fl02check as F2
    L, R = test_signal(seconds)
    k = case["kind"]
    if k == "free":
        scripts, _ = F2.free_scripts(len(L) // 32)
        return L, R, scripts[case["name"][len("free: "):]]
    if k == "synced":
        nb = case["blocks"]
        sw, bpm = F2.SYNC[case["div"] - 1], case["bpm"]
        return L[:nb * 32], R[:nb * 32], (lambda b: ([80 << 8, sw, 40 << 8, 64 << 8], bpm))
    if k == "plan":
        plan = [(0, 32 << 8, 120.0), (1.0, F2.SYNC[7], 120.0), (2.5, F2.SYNC[9], 120.0), (4.0, F2.SYNC[9], 90.0),
                (5.0, F2.SYNC[9], 160.0), (6.0, (F2.ANCHOR[9] + 1) << 8 | 10, 160.0), (7.0, F2.SYNC[9], 160.0),
                (8.0, 70 << 8, 100.0), (9.0, F2.SYNC[0], 100.0), (10.0, F2.SYNC[11], 100.0), (11.0, 9 << 8, 100.0)]
        nbt = int(12.0 * F2.BPS)
        tL, tR = (L * (nbt * 32 // len(L) + 1))[:nbt * 32], (R * (nbt * 32 // len(R) + 1))[:nbt * 32]
        sm = F2.Smoother(32 << 8)
        words, tempos = [], []
        for b in range(nbt):
            j = max(i for i, (t0, _, _) in enumerate(plan) if b >= t0 * F2.BPS)
            words.append(sm.step(plan[j][1]))
            tempos.append(plan[j][2])
        return tL, tR, (lambda b: ([80 << 8, words[b], 40 << 8, 64 << 8], tempos[b]))
    if k == "grid":
        w = case["words"]
        return L[:24000], R[:24000], (lambda b: (list(w), 120.0))
    raise ValueError(k)


def spring_cases(seconds):
    import spngcheck as SC
    out = []
    for c in SC.cases(seconds):
        out.append(dict(c, fx="spring", events={str(k): v for k, v in c.get("events", {}).items()}))
    return out


SE_DEFAULT = {0: 23.0, 1: 6.0, 2: 50.0, 3: 64.0, 4: 64.0, 5: 40.0, 6: 100.0, 7: 1.0}
TD_DEFAULT = {0: 40.0, 1: 70.0, 2: 22.0, 3: 102.0, 4: 40.0, 5: 64.0, 6: 50.0, 7: 40.0, 8: 30.0, 9: 30.0}


def jumps(nparams, every, seed, nb, ranges):
    rnd = random.Random(seed)
    ev = {}
    for b in range(1, nb):
        if b % every == 0:
            ev[str(b)] = [(p, float(rnd.choice([ranges[p][0], ranges[p][1], rnd.randint(*ranges[p])])))
                          for p in range(nparams)]
    return ev


def fx_cases(fx, seconds):
    nb = int(seconds * 48000) // 32
    out = []
    if fx == "spaceecho":
        rng = {p: (0, 127) for p in range(8)}
        rng[1], rng[7] = (0, 6), (0, 2)
        out.append({"name": "fxcheck's setting", "knobs": SE_DEFAULT})
        for mode in range(7):
            out.append({"name": "MODE %d, INT 64, PAN %d" % (mode, mode % 3),
                        "knobs": {**SE_DEFAULT, **{1: float(mode), 2: 64.0, 7: float(mode % 3)}}})
        out.append({"name": "AGE 127, INT 127, WID 127, PAN RND", "knobs": {**SE_DEFAULT, **{3: 127.0, 2: 127.0,
                                                                                        6: 127.0, 7: 2.0}}})
        out.append({"name": "BW 127 (no filter), CTR 0, AGE 0", "knobs": {**SE_DEFAULT, **{5: 127.0, 4: 0.0, 3: 0.0}}})
        out.append({"name": "BW 0, CTR 127, WID 0, TIME 127", "knobs": {**SE_DEFAULT, **{5: 0.0, 4: 127.0, 6: 0.0,
                                                                                         0: 127.0}}})
        out.append({"name": "every knob jumping every 7 blocks", "knobs": SE_DEFAULT,
                    "events": jumps(8, 7, 11, nb, rng)})
        out.append({"name": "every knob jumping every 50 blocks, tempo 90", "knobs": SE_DEFAULT, "bpm": 90.0,
                    "events": jumps(8, 50, 12, nb, rng)})
        ev = {str(b): [(0, float((b * 5) % 128)), (4, float((b * 3) % 128)), (5, float((b * 7) % 128))]
              for b in range(1, nb)}
        out.append({"name": "TIME, CTR, BW ramping every block", "knobs": SE_DEFAULT, "events": ev})
    else:
        rng = {p: (0, 127) for p in range(12)}
        out.append({"name": "fxcheck's setting", "knobs": TD_DEFAULT})
        out.append({"name": "FDBK 127, DRIV 127, AGE 127", "knobs": {**TD_DEFAULT, **{1: 127.0, 9: 127.0, 8: 127.0}}})
        out.append({"name": "FDBK 0, WID 127 (ping-pong), RES 127", "knobs": {**TD_DEFAULT, **{1: 0.0, 5: 127.0,
                                                                                            4: 127.0}}})
        out.append({"name": "HPF 127, LPF 0, WOW 127, FLUT 127", "knobs": {**TD_DEFAULT, **{2: 127.0, 3: 0.0, 6: 127.0,
                                                                                         7: 127.0}}})
        out.append({"name": "TONE and W&F macros moving", "knobs": TD_DEFAULT,
                    "events": {str(b): [(10, float((b * 3) % 128)), (11, float((b * 5) % 128))] for b in range(1, nb)}})
        out.append({"name": "every knob jumping every 7 blocks", "knobs": TD_DEFAULT, "events": jumps(12, 7, 21, nb, rng)})
        out.append({"name": "every knob jumping every 60 blocks, tempo 150", "knobs": TD_DEFAULT, "bpm": 150.0,
                    "events": jumps(12, 60, 22, nb, rng)})
    for c in out:
        c["fx"] = fx
        c["seconds"] = seconds
    return out


# ---------------------------------------------------------------------------------------------------------- the runs

def fxdt_cases(seconds):
    nb = int(seconds * 48000) // 32
    out = []
    base = {0: 96.0, 1: 30.0, 2: 32.0, 3: 48.0, 4: 80.0, 5: 50.0, 7: 40.0, 8: 20.0}
    for shp, cut in ((5, "32"), (5, "rand"), (0, "rand"), (2, "rand")):
        out.append({"fx": "flanger-dt", "name": "flanger (DT01's) FL_SHP %d, %s-sample calls" % (shp, cut),
                    "knobs": {**base, 6: float(shp)}, "cut": cut})
    rnd = random.Random(31)
    ev = {str(b): [(p, float(rnd.choice([0, 127, rnd.randint(0, 127)]))) for p in range(9)] + [(6, 5.0)]
          for b in range(1, nb) if b % 9 == 0}
    out.append({"fx": "flanger-dt", "name": "flanger (DT01's) random step, every knob jumping", "knobs": {**base, 6: 5.0},
                "events": ev, "cut": "rand"})
    out.append({"fx": "mbcomp-dt", "name": "compressor (DT01's) defaults, 1..32-sample calls", "knobs": {}, "cut": "rand"})
    out.append({"fx": "mbcomp-dt", "name": "compressor (DT01's) heavy, 1..32-sample calls",
                "knobs": {0: 20.0, 4: 8.0, 3: 64.0, 6: 10.0, 10: 20.0, 12: 0.0, 16: 6.0, 18: 70.0, 20: 1.0},
                "cut": "rand"})
    for c in out:
        c["seconds"] = seconds
    return out


def run_one(case, variant, built):
    """one case on one build -> (output bytes, state bytes, per-block instructions, hits, misaligned)"""
    fx = case["fx"]
    bl = built[BUILD_OF[fx]]
    d = Path(bl["dirs"]["sel" if variant == "sel" else "dc"])
    if fx == "flanger":
        import fl02check as F2
        L, R, script = flanger_script(case, case["seconds"])
        sh = F2.Sharc(d, FLG_MAGIC)
        vm_setup(sh.vm, variant)
        oL, oR = F2.run(sh, L, R, script)
        st = image_state(sh.vm, d / "flg.dxe")
        pb = sh.per_block
    elif fx == "spring":
        import spngcheck as SC
        SC.OUT = d
        sh = SC.Sharc()
        vm_setup(sh.vm, variant)
        L, R = test_signal(case["seconds"])
        n = len(L) // 32 * 32
        sh.cmd(0)
        for p, v in case["knobs"].items():
            sh.cmd(1, param=int(p), value=float(v))
        sh.cmd(2)
        ev = {int(b): e for b, e in case.get("events", {}).items()}
        evpos = sorted(32 * b for b in ev)
        rnd = random.Random(7)
        oL, oR = [], []
        i = 0
        while i < n:
            if i % 32 == 0:
                for p, v in ev.get(i // 32, []):
                    sh.cmd(1, param=p, value=v)
            m = 32
            if case.get("cut") == "rand":
                nxt = next((q for q in evpos if q > i), n)
                m = min(1 + int(rnd.random() * 100), nxt - i, n - i)
            x, y = sh.block(L[i:i + m], R[i:i + m])
            oL += x
            oR += y
            i += m
        st = image_state(sh.vm, d / "spngdrv.dxe") + sh.vm.read(STATE, STATE_BYTES)
        pb = sh.per_block
    elif fx in FX_PARAM:
        import fxcheck as FX
        FX.OUT = d
        sh = FX.Sharc(FX_PARAM[fx])
        vm_setup(sh.vm, variant)
        L, R = test_signal(case["seconds"])
        n = len(L) // 32 * 32
        sh.cmd(0, seed=1)
        if FX_PARAM[fx] == "flanger":
            sh.cmd(4, bpm=120.0)
        for p, v in case["knobs"].items():
            sh.cmd(1, param=int(p), value=float(v))
        sh.cmd(2)
        ev = {int(b): e for b, e in case.get("events", {}).items()}
        rnd = random.Random(5)
        evpos = sorted(32 * b for b in ev)
        oL, oR = [], []
        i = 0
        while i < n:
            for p, v in ev.get(i // 32, []) if i % 32 == 0 else []:
                sh.cmd(1, param=p, value=v)
            m = 32
            if case.get("cut") == "rand":
                # 1..32 samples, across the 32-sample grid; a call ends where a knob changes
                nxt = next((q for q in evpos if q > i), n)
                m = min(1 + int(rnd.random() * 32), nxt - i, n - i)
            sh.vm.wrf(FX.BUF, L[i:i + m])
            sh.vm.wrf(FX.BUF + 0x80, R[i:i + m])
            sh.cmd(3, n=m)
            oL += sh.vm.rdf(FX.BUF + 0x100, m)
            oR += sh.vm.rdf(FX.BUF + 0x180, m)
            i += m
        st = image_state(sh.vm, d / "fxdrv.dxe") + sh.vm.read(STATE, STATE_BYTES)
        pb = sh.per_block
    else:
        import fxcheck as FX
        FX.OUT = d
        sh = FX.Sharc(fx)
        vm_setup(sh.vm, variant)
        L, R = test_signal(case["seconds"])
        sh.cmd(0, seed=1)
        sh.cmd(4, bpm=case.get("bpm", 120.0))
        for p, v in case["knobs"].items():
            sh.cmd(1, param=int(p), value=float(v))
        sh.cmd(2)
        ev = {int(b): e for b, e in case.get("events", {}).items()}
        oL, oR = [], []
        for i in range(0, len(L) // 32 * 32, 32):
            for p, v in ev.get(i // 32, []):
                sh.cmd(1, param=p, value=v)
            x, y = sh.block(L[i:i + 32], R[i:i + 32])
            oL += x
            oR += y
        st = image_state(sh.vm, d / "fxdrv.dxe") + sh.vm.read(STATE, STATE_BYTES)
        pb = sh.per_block
    out = struct.pack("<%df" % len(oL), *oL) + struct.pack("<%df" % len(oR), *oR)
    hits = hits_of(sh.vm, bl["pcs"]) if variant == "dc" else {}
    mis = sh.vm.lib.vm_misaligned(sh.vm.h)
    del sh
    memguard.check()
    return out, st, pb, hits, mis


def run_case(arg):
    case, built = arg
    t0 = time.time()
    res = {}
    for v in VARIANTS:
        res[v] = run_one(case, v, built)
    o_sel, s_sel, pb_sel, _, mis_sel = res["sel"]
    r = {"fx": case["fx"], "name": case["name"]}
    for v in ("dc", "dca"):
        o, s, pb, h, mis = res[v]
        n = len(o_sel) // 4
        same = n if o == o_sel else sum(1 for i in range(0, len(o), 4) if o[i:i + 4] == o_sel[i:i + 4])
        r[v] = {"samples_identical": "%d/%d" % (same, n), "state_identical": s == s_sel, "misaligned": mis}
        r[v]["ok"] = same == n and s == s_sel and len(o) == len(o_sel)
    pb_dc = res["dc"][2]
    skip = 1 if case["fx"] == "flanger" else 0
    r["insn_mean"] = {"sel": round(sum(pb_sel[skip:]) / max(1, len(pb_sel) - skip), 1),
                      "dc": round(sum(pb_dc[skip:]) / max(1, len(pb_dc) - skip), 1)}
    r["insn_max"] = {"sel": max(pb_sel[skip:] or [0]), "dc": max(pb_dc[skip:] or [0])}
    r["misaligned_sel"] = mis_sel
    r["hits"] = res["dc"][3]
    r["ok"] = r["dc"]["ok"] and r["dca"]["ok"]
    r["seconds"] = round(time.time() - t0, 1)
    return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fx", default="flanger,spring,spaceecho,tapedelay,flanger-dt,mbcomp-dt")
    ap.add_argument("--seconds", type=float, default=None, help="audio per case (default: flanger 4, others 2)")
    ap.add_argument("--grid", action="store_true")
    ap.add_argument("--quick", action="store_true", help="a few cases per effect, 1 s each")
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--out", default=str(ROOT / "out/DC01/dc01check.json"))
    ap.add_argument("--patterns", default="AB", help="the pass's mode (DC02: ABs)")
    ap.add_argument("--image", default="DC01", help="the image whose unreachable changed lines fxdt runs (DC02)")
    a = ap.parse_args()
    global OUT, PATTERNS, IMAGE
    PATTERNS, IMAGE = a.patterns, a.image
    OUT = Path(a.out).resolve().with_suffix("")
    t0 = time.time()
    fxs = a.fx.split(",")
    built = {}
    for fx in fxs:
        bn = BUILD_OF[fx]
        if bn not in built:
            built[bn] = build(bn)
            print("built %s: %d changed lines, %s" % (bn, built[bn]["info"]["changed_lines"],
                                                       built[bn]["info"]["peephole"]["by_file"]), flush=True)
    cases = []
    for fx in fxs:
        sec = a.seconds or (4.0 if fx == "flanger" else 2.0)
        if a.quick:
            sec = 1.0
        if fx == "flanger":
            cs = flanger_cases(sec, a.grid)
            for c in cs:
                c["seconds"] = sec
        elif fx == "spring":
            cs = spring_cases(sec)
        elif fx in FX_PARAM:
            cs = [c for c in fxdt_cases(sec) if c["fx"] == fx]
        else:
            cs = fx_cases(fx, sec)
        if a.quick:
            cs = cs[:3] + cs[-2:]
        cases += cs
    print("%d cases" % len(cases), flush=True)
    results = []
    with ProcessPoolExecutor(min(2, a.workers), max_tasks_per_child=memguard.TASKS_PER_CHILD) as ex:
        for r in memguard.pmap(ex, run_case, [(c, built) for c in cases], chunksize=1):
            results.append(r)
            print("%-10s %-58s %s  dc %s %s  align %s %s  insns mean %s max %s  %.0fs" % (
                r["fx"], r["name"][:58], "ok" if r["ok"] else "DIFFERS", r["dc"]["samples_identical"],
                "state same" if r["dc"]["state_identical"] else "STATE DIFFERS", r["dca"]["samples_identical"],
                "state same" if r["dca"]["state_identical"] else "STATE DIFFERS", r["insn_mean"], r["insn_max"],
                r["seconds"]), flush=True)
    summary = {"ok": all(r["ok"] for r in results), "cases": len(results), "seconds": round(time.time() - t0),
               "patterns": PATTERNS, "image": IMAGE}
    cover = {}
    for bn, bl in built.items():
        hit = {}
        for r in results:
            if BUILD_OF[r["fx"]] == bn:
                for lab, h in r["hits"].items():
                    hit[lab] = hit.get(lab, 0) + h
        ran_fx = {fx for fx in fxs if BUILD_OF[fx] == bn}
        # in the fxdrv build only the effects run here count (the others' lines are listed apart)
        sites = {lab: s for lab, s in bl["sites"].items()}
        stems_run = {"fx": {"spaceecho": "spaceecho", "tapedelay": "tapedelay"},
                     "fxdt": {"flanger-dt": "flanger", "mbcomp-dt": "mbcomp"}}.get(bn)
        if stems_run:
            sites = {lab: s for lab, s in sites.items() if s["stem"] in {stems_run[f] for f in ran_fx}}
        miss = [dict(s, label=lab) for lab, s in sites.items() if lab not in hit]
        by = {}
        for s in miss:
            by["%s:%s" % (s["stem"], s["function"])] = by.get("%s:%s" % (s["stem"], s["function"]), 0) + 1
        cover[bn] = {"changed_lines": len(sites), "run": len(sites) - len(miss), "not_run": len(miss),
                     "not_run_by_function": dict(sorted(by.items())), "not_run_lines": miss}
    for fx in fxs:
        rs = [r for r in results if r["fx"] == fx]
        summary[fx] = {"cases": len(rs), "ok": all(r["ok"] for r in rs),
                       "worst_block": {"sel": max(r["insn_max"]["sel"] for r in rs),
                                       "dc": max(r["insn_max"]["dc"] for r in rs)},
                       "highest_mean": {"sel": max(r["insn_mean"]["sel"] for r in rs),
                                        "dc": max(r["insn_mean"]["dc"] for r in rs)},
                       "misaligned": {"sel": sum(r["misaligned_sel"] for r in rs),
                                      "dc": sum(r["dc"]["misaligned"] for r in rs),
                                      "dca": sum(r["dca"]["misaligned"] for r in rs)}}
    if "fxdt" in built:
        hit = {}
        for r in results:
            if BUILD_OF[r["fx"]] == "fxdt":
                for lab, h in r["hits"].items():
                    hit[lab] = hit.get(lab, 0) + h
        img = built["fxdt"]["info"]["dc01_image_sites"]
        cover["dc01_image_lines_run_here"] = {
            "lines": len(img), "same_block_here": sum(1 for v in img.values() if v["same_block_here"]),
            "run_here": sum(1 for v in img.values() if v["label"] and v["label"] in hit),
            "detail": {k: dict(v, hits=hit.get(v["label"], 0) if v["label"] else 0) for k, v in img.items()}}
    summary["coverage"] = {bn: {k: v for k, v in c.items() if k not in ("not_run_lines", "detail")}
                           for bn, c in cover.items()}
    summary["builds"] = {bn: bl["info"] for bn, bl in built.items()}
    for r in results:
        r.pop("hits")
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps({"summary": summary, "coverage": cover, "results": results}, indent=1))
    print(json.dumps(summary, indent=1), flush=True)
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
