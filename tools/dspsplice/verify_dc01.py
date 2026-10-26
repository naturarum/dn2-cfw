#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""DC01 (2026-10-24; notes/DC01-compiler-fix.md): the DSP proofs of DC01 = DT01 with the compiler fix (the peephole
pass, tools/dspsplice/peephole.py), the same sound.

    python3 tools/dspsplice/verify_dc01.py [--quick] [--only a,e,s,f,g] [--cover-from verify.json]

(a) DC01 against DT01 (out/DT01/DT01-dsp): verify_dt01.py's proof (a) (verify_sc01's 53 settings that CMP4 used: zero
    and invalid tails, MIX 0, equal MIX and the relock, one band wet, the guard and re-init injections, the cost cases
    with the flanger's knobs jumping, seeded old kits; the delay slot silent) with DT01 in CMP3's place and DC01 in
    SC01's: every handler output sample, the chorus slot's output (the flanger) every block, each band's gain reduction
    every block, both reply pages every block but word 0 (the handler's cycle count), and at the end L1 word for word
    outside our code, data, stack and the stub save area, and the flanger's state word for word (the same layout);
(e) the same lockstep with the delay slot ON (verify_dt01's four cases: the stock delay fed by the input and the
    chorus, FDBK 64 / 100 / 127, every FX knob jumping);
(s) stores on DC01 (verify_cmp4.py's proof (s));
(f) the flanger synced to the tempo (verify_sc01's cases keep SPD free): synced SPD words at 60, 120 and 174 BPM, and
    free -> synced -> another division -> free with tempo jumps, compared as in (a);
(g) the compressor's rare paths: the same tail sequence held for 4 blocks, a guard trip while all three MIX are 0, a
    corrupted re-attack count (1000, -5), compared as in (a);
(c) coverage of the changed code: a label before every line the peephole changed (DT01's and DC01's build/*.s, the
    same selcc output, compared), the build relinked with them (each section byte for byte DC01.dxe's), sharcvm's
    per-instruction counters on in DC01's VM over (a), (e), (f), (g) and one effect init: every changed line run at least
    once (the ones not run listed); --cover-from adds an earlier run's counts (the same image).
Instructions per block, mean and max, of both images in every case; the worst block over all cases.
Results: out/DC01/DC01-dsp/verify.json (or verify-<only>.json). --name DC02: the same proofs for DC02 (the pass in its
cautious mode, notes/DC01-compiler-fix.md section 5) against DT01, results in out/DC02/DC02-dsp/ (the keys *_DT01_DC01
then hold DT01's and DC02's).
"""
import argparse
import gc
import json
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "tools/sharcvm/tests"))
import verify_sc01 as S1  # noqa: E402
import verify_cmp4 as C4  # noqa: E402
import verify_dt01 as D1  # noqa: E402
import memguard  # noqa: E402
import build as BD  # noqa: E402
import peephole as PH  # noqa: E402

VC, V = S1.VC, S1.V
SLOT, OFF = S1.SLOT, S1.OFF
NAME = "DC01"                   # the image checked against DT01 (--name)
OUT = ROOT / "out/DC01/DC01-dsp"
DT01_DIR = ROOT / "out/DT01/DT01-dsp"
FLG, FLG_BYTES = 0x300000, 0x8400
WORST = {"DT01": (0, ""), "new": (0, "")}
SITES = {}          # pc -> {"stem", "function", "line", "text"}
HITS = {}           # pc -> executions in DC01's VM


def site_map():
    """every line the peephole changed in DC01's build: its PC in the image. The build's .s files with a label before each
    such line (labels take no space), assembled and linked with the build's own seld command; every section of the
    result byte for byte DC01.dxe's. -> {pc: site}"""
    import elf as E
    rec = BD.RECIPES[NAME]
    b_dc, b_dt = OUT / "build", DT01_DIR / "build"
    work = OUT / "sites"
    work.mkdir(parents=True, exist_ok=True)
    info = json.loads((OUT / "build.json").read_text())
    sites = {}
    objs = {}
    for src in BD.all_c(rec):
        stem = Path(src).stem
        if (b_dt / f"{stem}.sel.s").read_text() != (b_dc / f"{stem}.sel.s").read_text():
            raise SystemExit(f"{stem}: selcc's output differs between DT01's and {NAME}'s builds")
        old, new = (b_dt / f"{stem}.s").read_text(), (b_dc / f"{stem}.s").read_text()
        lines = PH.site_lines({stem: old}, {stem: new}).get(stem, [])
        txt, labels = PH.add_site_labels(new, lines, f"dcs_{stem}_")
        (work / f"{stem}.s").write_text(txt)
        subprocess.run([str(BD.SEL / "selas"), "-proc", "ADSP-21569", "-o", str(work / f"{stem}.doj"),
                        str(work / f"{stem}.s")], check=True)
        objs[str(b_dc / f"{stem}.doj")] = str(work / f"{stem}.doj")
        nl = new.split("\n")
        for lab, li in zip(labels, lines):
            fn = next((m.group(1) for k in range(li, -1, -1) for m in [PH.FUNC_LABEL.match(nl[k])] if m), "?")
            sites[lab] = {"stem": stem, "function": fn, "line": li + 1, "text": nl[li].strip()}
    cmd = next(c for c in info["log"] if c.split()[0].endswith("/seld"))
    args = [objs.get(a, a) for a in cmd.split()]
    o = args.index("-o")
    args[o + 1] = str(work / f"{NAME}-sites.dxe")
    subprocess.run(args, check=True, capture_output=True)
    ref, got = E.read_elf(b_dc / f"{NAME}.dxe"), E.read_elf(work / f"{NAME}-sites.dxe")
    rs = sorted((s.name, s.addr, bytes(s.data)) for s in ref.sections if s.kind in ("sw_code", "data"))
    gs = sorted((s.name, s.addr, bytes(s.data)) for s in got.sections if s.kind in ("sw_code", "data"))
    if rs != gs:
        raise SystemExit(f"the relink with the site labels is not {NAME}.dxe byte for byte")
    out = {}
    for lab, s in sites.items():
        out[got.symbols[lab + "."]] = dict(s, label=lab)
    return out


def collect(vm):
    for pc in SITES:
        pg = vm.pages.get(pc >> 12)
        if pg is not None:
            h = pg[pc & 0xfff].hits
            if h:
                HITS[pc] = HITS.get(pc, 0) + h


class PairDC(D1.PairDT):
    """DT01 (a) and DC01 (b) on the same blocks: verify_dt01's Pair, the flanger's slot compared as it is (the same
    layout), DC01's VM counting executions for (c)"""

    def __init__(self, imgs, words, raw, sched, silent=True):
        super().__init__(imgs, words, raw, sched, silent=silent)
        self.b.vm.lib.vm_set_profile(self.b.vm.h, 1)

    def finish(self):
        va, vb = self.a.vm, self.b.vm
        ignore = [(0x2dd600, 0x2e1c40), (0x2e2900, 0x2e6000), (0x2e6000, 0x2e7000), (0x308400, 0x318000),
                  (V.STACK_LO, V.STACK_TOP), (VC.TX[0], VC.TX[0] + 4), (VC.TX[1], VC.TX[1] + 4)]
        mem = []
        for lo, hi in ((0x240000, 0x2f0000), (0x300000, 0x320000)):
            x, y = va.read(lo, hi - lo), vb.read(lo, hi - lo)
            if x != y:
                for i in range(0, hi - lo, 4):
                    ad = lo + i
                    if x[i:i + 4] != y[i:i + 4] and not any(p <= ad < q for p, q in ignore):
                        mem.append(ad)
        fa, fb = va.read(FLG, FLG_BYTES), vb.read(FLG, FLG_BYTES)
        flg_diff = [FLG + i for i in range(0, FLG_BYTES, 4) if fa[i:i + 4] != fb[i:i + 4]]
        pa, pb = self.a.r.per_block, self.b.r.per_block
        case = getattr(self, "case", "?")
        for tag, p in (("DT01", pa), ("new", pb)):
            if p[1:] and max(p[1:]) > WORST[tag][0]:
                WORST[tag] = (max(p[1:]), case)
        collect(vb)
        r = {"blocks": self.nb, "samples": self.samples, "samples_identical": self.same,
             "flanger_output_blocks_identical": self.chr_same, "delay_slot_output_blocks_identical": self.del_same,
             "delay_slot_silent_blocks_both": self.del_zero,
             "gain_reduction_blocks_identical": self.gr_same, "reply_pages_identical_but_word0": self.pages_same,
             "reply_byte_offsets_that_differ": sorted("%#x" % o for o in self.page_offsets),
             "l1_words_that_differ_outside_ours": ["%#x" % a for a in mem[:20]], "l1_count": len(mem),
             "flanger_slot_words_that_differ": len(flg_diff),
             "trips_reinits_DT01_DC01": [[v.rd32(SLOT + OFF["trips"]), v.rd32(SLOT + OFF["reinits"])] for v in (va, vb)],
             "insns_mean_DT01_DC01": [round(sum(pa[1:]) / max(1, len(pa) - 1)), round(sum(pb[1:]) / max(1, len(pb) - 1))],
             "insns_max_DT01_DC01": [max(pa[1:] or [0]), max(pb[1:] or [0])], "peak": round(self.peak, 4)}
        # verify_sc01's report line reads the CMP3/SC01 keys
        r["insns_mean_CMP3_SC01"], r["insns_max_CMP3_SC01"] = r["insns_mean_DT01_DC01"], r["insns_max_DT01_DC01"]
        r["ok"] = (self.same == self.samples and self.gr_same == self.nb and self.pages_same == self.nb
                   and self.chr_same == self.nb and self.del_same == self.nb and not mem and not flg_diff)
        return r


def proof_a(imgs, q):
    keep = S1.Pair
    S1.Pair = PairDC
    try:
        ok, res = S1.proof_a(imgs, q)
    finally:
        S1.Pair = keep
    res["note"] = "the keys *_CMP3_SC01 are DT01's and DC01's (verify_sc01's proof (a) run with DT01 in CMP3's place " \
                  "and DC01 in SC01's)"
    return ok, res


def proof_e(imgs, q):
    keep = D1.PairDT
    D1.PairDT = PairDC
    try:
        return D1.proof_e(imgs, q)
    finally:
        D1.PairDT = keep


def proof_f(imgs, q):
    """the flanger synced to the tempo through the whole image (verify_sc01's cases keep SPD free): SPD on synced words
    (fl02check's (a << 8) | i) at several tempos, and free -> synced -> another division -> free with tempo jumps; the
    delay slot silent, a typical compressor"""
    import fl02check as F2
    res, ok_all = {}, True
    nb = 600 if not q else 150
    mix = VC.load_input("mix", nb * 32 / S1.SR)
    words = dict(dict(VC.BASE, **VC.FXW), **VC.TYPICAL)
    cases = {}
    for bpm in (60.0, 120.0, 174.0):
        for i in (1, 4, 8, 12):
            cases["SPD synced %s at %g BPM" % (F2.NAMES[i - 1], bpm)] = [(0, F2.SYNC[i - 1], bpm)]
    cases["free -> synced 1/8 -> 1/16 -> free, tempo 100 -> 160"] = [
        (0, 70 << 8, 100.0), (nb // 6, F2.SYNC[9], 100.0), (nb // 3, F2.SYNC[10], 160.0), (nb // 2, 30 << 8, 160.0),
        (2 * nb // 3, F2.SYNC[0], 90.0)]
    for name, plan in cases.items():
        t0 = time.time()

        def each(b, fr, plan=plan):
            k = max(j for j, (b0, _, _) in enumerate(plan) if b >= b0)
            VC.put(fr, "CHR_SPD", plan[k][1])
            VC.put(fr, "TEMPO", int(round(120 * plan[k][2])))
        pr = PairDC(imgs, words, VC.ROUTE_INFX, S1.sched3(S1.DEFAULTS, (39, 39, 39)))
        pr.case = name
        r = pr.run(mix[0][:nb * 32], mix[1][:nb * 32], each=each)
        r["seconds"] = round(time.time() - t0, 1)
        res[name] = r
        ok_all &= r["ok"]
        print("(f) %-70s %s %d/%d samples, flanger %d/%d, insns %s, %.0fs" % (
            name[:70], "ok" if r["ok"] else "DIFFERS", r["samples_identical"], r["samples"],
            r["flanger_output_blocks_identical"], r["blocks"], r["insns_mean_DT01_DC01"], r["seconds"]), flush=True)
        del pr
        gc.collect()
        memguard.check(4096)
    res["ok"] = ok_all
    return ok_all, res


def proof_g(imgs, q):
    """the compressor's rare paths, compared as in (a): the same tail sequence held for 4 blocks (cmp_tail_fetch's
    'same' count; on the unit the ColdFire repeats a tail while nothing changes), a guard trip while all three MIX are 0
    (mb_restart: the dry path at once), and a corrupted re-attack count (mb_control's two clamps)"""
    res, ok_all = {}, True
    nb = 300 if not q else 120
    Ld, Rd = VC.load_input("drums", nb * 32 / S1.SR)
    vals = S1.words3(S1.knobs17(S1.DISTINCT), (20, 64, 127))
    vals0 = S1.words3(S1.knobs17(S1.DISTINCT), (0, 0, 0))
    w64 = dict(VC.BASE, **VC.NOFX, **VC.comp(32, 24, 32, 64, 3, 64))
    w0 = dict(VC.BASE, **VC.NOFX, **VC.comp(32, 24, 32, 64, 3, 0))
    tw = S1.tail_words

    def inj_inf(b, vm_, ptrs):
        if b == 60:
            vm_.wr32(ptrs[1] + 4 * 9, 0x7f800000)

    def inj_rec(v):
        def f(b, vm_, ptrs):
            if b == 60:
                vm_.wr32(SLOT + OFF["mb.rec_left"], v & 0xffffffff)
        return f
    cases = [("the tail's sequence held for 4 blocks", w64, lambda b: tw(1 + b // 4, vals), None),
             ("all three MIX 0, inf in comp in R at block 60 (guard trip)", w0, lambda b: tw(1 + b, vals0), inj_inf),
             ("the re-attack count 1000 at block 60 (clamped)", w64, lambda b: tw(1 + b, vals), inj_rec(1000)),
             ("the re-attack count -5 at block 60 (clamped)", w64, lambda b: tw(1 + b, vals), inj_rec(-5))]
    for name, words, sched, inj in cases:
        t0 = time.time()
        pr = PairDC(imgs, words, VC.ROUTE_IN, sched)
        pr.case = name
        r = pr.run(Ld[:nb * 32], Rd[:nb * 32], inject=inj)
        r["seconds"] = round(time.time() - t0, 1)
        res[name] = r
        ok_all &= r["ok"]
        print("(g) %-70s %s %d/%d samples, gain reduction %d/%d, insns %s, %.0fs" % (
            name[:70], "ok" if r["ok"] else "DIFFERS", r["samples_identical"], r["samples"],
            r["gain_reduction_blocks_identical"], r["blocks"], r["insns_mean_DT01_DC01"], r["seconds"]), flush=True)
        del pr
        gc.collect()
        memguard.check(4096)
    res["ok"] = ok_all
    return ok_all, res


def init_coverage(stream):
    """the effect init (0x1c1445) once on DC01 with the counters on (the cached images skip it)"""
    from vm import VM
    vm = VM()
    vm.load_dn2()
    V.load_stream(vm, stream)
    vm.lib.vm_set_profile(vm.h, 1)
    vm.call(V.FXINIT, max_insns=10**9)
    collect(vm)
    del vm
    gc.collect()


def coverage_report():
    rows = []
    for pc, s in sorted(SITES.items()):
        rows.append(dict(s, pc="%#x" % pc, hits=HITS.get(pc, 0)))
    miss = [r for r in rows if not r["hits"]]
    by_fn = {}
    for r in miss:
        by_fn.setdefault("%s:%s" % (r["stem"], r["function"]), []).append(r["text"])
    return {"lines": len(rows), "run": len(rows) - len(miss), "not_run": len(miss),
            "not_run_by_function": {k: len(v) for k, v in sorted(by_fn.items())},
            "not_run_lines": [{k: r[k] for k in ("stem", "function", "line", "text", "pc")} for r in miss],
            "hits": {"%#x" % pc: h for pc, h in sorted(HITS.items())}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--only", help="comma-separated proofs (a,e,s,f,g)")
    ap.add_argument("--cover-from", action="append", default=[],
                    help="a verify JSON of an earlier run of this image: its executions count for (c) too")
    ap.add_argument("--name", default="DC01", choices=["DC01", "DC02"], help="the image checked against DT01")
    a = ap.parse_args()
    global NAME, OUT
    NAME, OUT = a.name, ROOT / f"out/{a.name}/{a.name}-dsp"
    only = set(a.only.split(",")) if a.only else set("aesfg")
    t_all = time.time()
    imgs = {"SC01": S1.load_image(OUT, NAME), "CMP3": S1.load_image(DT01_DIR, "DT01")}
    for k in ("SC01", "CMP3"):
        stale = [s for s, h in imgs[k][1]["sources"].items() if BD.sha((ROOT / s).read_bytes()) != h]
        if stale:
            raise SystemExit(f"{imgs[k][1]['name']} was built from other sources ({stale})")
    if imgs["SC01"][1]["sources"] != imgs["CMP3"][1]["sources"]:
        raise SystemExit(f"{NAME} and DT01 are not built from the same sources")
    OFF.update(S1.VC.offsets(OUT / "offsets", "CMP3"))
    if imgs["SC01"][1]["layout_check"]["slot_sizes_from_image"] != imgs["CMP3"][1]["layout_check"]["slot_sizes_from_image"]:
        raise SystemExit(f"CmpSlot's or CmpTail's size differs between DT01 and {NAME}")
    SITES.update(site_map())
    print("(c) %d changed lines located in %s's image" % (len(SITES), NAME), flush=True)
    for fj in a.cover_from:
        prev = json.loads(Path(fj).read_text())
        if prev["section7_sha256"] != imgs["SC01"][1]["section7"]["sha256"]:
            raise SystemExit(f"{fj} is another image's")
        if "hits" in prev["coverage"]:
            for pc, h in prev["coverage"]["hits"].items():
                HITS[int(pc, 16)] = HITS.get(int(pc, 16), 0) + h
        else:       # a report without the counts: every line it does not list as not run, once
            miss = {int(r["pc"], 16) for r in prev["coverage"]["not_run_lines"]}
            if prev["coverage"]["lines"] != len(SITES):
                raise SystemExit(f"{fj}: another site map")
            for pc in SITES:
                if pc not in miss:
                    HITS[pc] = HITS.get(pc, 0) + 1
    res = {"name": NAME, "section7_sha256": imgs["SC01"][1]["section7"]["sha256"],
           "dt01_section7_sha256": imgs["CMP3"][1]["section7"]["sha256"], "proofs_run": "".join(sorted(only))}
    oks = {}
    init_coverage(imgs["SC01"][0])
    res["coverage_merged_from"] = a.cover_from
    for k, fn in (("s", C4.proof_s), ("e", proof_e), ("f", proof_f), ("g", proof_g), ("a", proof_a)):
        if k in only:
            t0 = time.time()
            oks[k], res[k] = fn(imgs, a.quick)
            res[k + "_seconds"] = round(time.time() - t0)
            print("== (%s) %s, %.0fs" % (k, "PASS" if oks[k] else "FAIL", time.time() - t0), flush=True)
            res["worst_block"] = {(NAME if t == "new" else t): {"instructions": w, "case": c} for t, (w, c) in WORST.items()}
            res["coverage"] = coverage_report()
            part = OUT / ("verify-%s.json" % ("".join(sorted(only)) + ("-quick" if a.quick else "")))
            part.write_text(json.dumps(dict(res, ok=all(oks.values()), seconds=round(time.time() - t_all)), indent=1))
    res["ok"] = all(oks.values())
    res["seconds"] = round(time.time() - t_all)
    res["peak_rss_mb"] = round(memguard.peak_rss_mb())
    res["worst_block"] = {(NAME if t == "new" else t): {"instructions": w, "case": c} for t, (w, c) in WORST.items()}
    res["coverage"] = coverage_report()
    name = "verify.json" if not a.only and not a.quick else "verify-%s.json" % ("".join(sorted(only)) + ("-quick" if a.quick else ""))
    if a.only and not a.quick and a.cover_from:
        name = "verify-%s-merged.json" % "".join(sorted(only))
    (OUT / name).write_text(json.dumps(res, indent=1))
    c = res["coverage"]
    print("coverage: %d of %d changed lines run; not run: %s" % (c["run"], c["lines"], c["not_run_by_function"]),
          flush=True)
    print("worst block", res["worst_block"], flush=True)
    print("VERDICT", "PASS" if res["ok"] else "FAIL", res["seconds"], "s", flush=True)
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
