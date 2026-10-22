#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""CMP4 (2026-10-22; notes/T6.3-cmp4.md): the DSP proofs of CMP4 = CMP3 with the stock delay back in the delay slot and
SC01's breathing band meters.

    python3 tools/dspsplice/verify_cmp4.py [--quick] [--only a,b,d,s]

(a) the compressor and the flanger against CMP3 (out/T6.3/CMP3-dsp, on the unit): verify_sc01.py's proof (a) with
    CMP4 in SC01's place, the two images in lockstep on CMP3's own verify settings with the delay slot silent in both (no
    send into it, its VOL 0: CMP3's tape delay and CMP4's stock delay then both return 0, so the compressor gets the
    same input): every handler output sample bit for bit, the chorus slot's output every block, each band's gain
    reduction every block, both reply pages every block but word 0 and half-words 8..10 (the meters), and at the end L1
    word for word outside our code, data, stack, CMP3's CmpMeter and the stock delay's own state (the L1 words the stock
    delay call stores, measured on CMP4 first: CMP3 never runs it);
(b) the meters: verify_sc01.py's proof (b) on CMP4's image: every block the reply half-words 8..10 = the float32 mirror
    of SC01's cmp_mtr fed the band's y2 (bursts per band at REL 0 / 64 / 127, drums at the starting point, RAT 1.00),
    and cmp_mtr called at its address in CMP4's code4 on the edge values;
(d) the delay slot = the stock firmware's delay, bit for bit: CMP4 and the stock image (vmrun.candidate_vm(None)) on
    the same blocks, the delay's input from the external input only (no chorus send into it), its output buffer every
    block, its DDR state (the stock delay's slot, which CMP3's tape delay used) at the end; FDBK 0 / 32 / 64 / 100 / 127,
    TIME steps, X and WID, HPF and LPF sweeps, tempo changes, every delay knob jumping;
(x) kits saved on SC01 (MODE 0..6 in the delay's X): X 0..6 at FDBK 64 and 100 on CMP4 and on the stock image, the
    delay's output identical and finite, its peak per X;
(s) stores: the chorus call (the flanger) and the compressor call store only into their own state, the stack, the
    stub save area and their outputs; over whole blocks with every knob jumping no store into our code (L1 block 1,
    code4, the constant data, block 2's compressor) nor into 0x30ff76-0x318000 (with 0x315f8c-0x318000).
Results: out/CMP4/CMP4-dsp/verify.json (or verify-<only>.json).
"""
import argparse
import gc
import json
import random
import struct
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(HERE))
import verify_sc01 as S1  # noqa: E402
import memguard  # noqa: E402
import build as BD  # noqa: E402

VC, V2, V, V3 = S1.VC, S1.V2, S1.V, S1.V3
SLOT, TST, OFF, SR = S1.SLOT, S1.TST, S1.OFF, S1.SR
OUT = ROOT / "out/CMP4/CMP4-dsp"
CMP3_DIR = S1.CMP3_DIR
DEL_SLOT, DEL_BYTES = S1.SE_SLOT, S1.SE_BYTES          # the stock delay's DDR state (SC01's SeSlot, FTD1's TdSlot)
STOCK_DELAY_L1 = set()                                 # (a): the L1 words the stock delay call stores, measured


class Pair4(S1.Pair):
    """verify_sc01's Pair (CMP3 = a, CMP4 = b), its end-of-run L1 comparison also blind to the stock delay's own state"""

    def finish(self):
        va, vb = self.a.vm, self.b.vm
        r = super().finish()
        if r["l1_count"] == 0:
            return r
        ignore = [(0x2dd600, 0x2e1c40), (0x2e2800, 0x2e7000), (0x308400, 0x318000), (V.STACK_LO, V.STACK_TOP),
                  (VC.TX[0], VC.TX[0] + 0x1000), (VC.TX[1], VC.TX[1] + 0x1000), (SLOT + OFF["word10"], SLOT + OFF["word10"] + 8)]
        mem, dl = [], []
        for lo, hi in ((0x240000, 0x2f0000), (0x300000, 0x320000)):
            x, y = va.read(lo, hi - lo), vb.read(lo, hi - lo)
            for i in range(0, hi - lo, 4):
                ad = lo + i
                if x[i:i + 4] != y[i:i + 4] and not any(p <= ad < q for p, q in ignore):
                    (dl if ad in STOCK_DELAY_L1 else mem).append(ad)
        r["l1_words_that_differ_outside_ours"] = ["%#x" % a for a in mem[:20]]
        r["l1_count"] = len(mem)
        r["l1_differing_words_of_the_stock_delay"] = len(dl)
        r["ok"] = (self.same == self.samples and self.gr_same == self.nb and self.pages_same == self.nb
                   and self.hw11_same == self.nb and self.chr_same == self.nb and not mem)
        return r


def stock_delay_state(img):
    """the L1 words the stock delay call stores on CMP4 (watched over 400 blocks, the knobs jumping, the delay silent
    and also fed), outside its output buffers and the stack"""
    words = dict(VC.BASE, **VC.FXW, **VC.comp(32, 24, 32, 64, 3, 39))
    L, R = VC.load_input("mix", 400 * 32 / SR)
    rn = random.Random(5)
    seen = set()
    for extra in (S1.SILENT_DELAY, {}):
        run = S1.FxTailRun(V.candidate_vm(img[0], cache=img[2]), dict(words, **extra), VC.ROUTE_INFX,
                           S1.sched3(S1.DEFAULTS, (39, 39, 39)))
        for b in range(400):
            if b % 7 == 0:
                for k in ("DEL_TIME", "DEL_WID", "DEL_FDBK", "DEL_HPF", "DEL_LPF"):
                    VC.put(run.r.d.frame, k, rn.randrange(128) << 8)
                VC.put(run.r.d.frame, "DEL_X", rn.randrange(2) << 8)
            fw = ("del", [])
            run.tblock(L[32 * b:32 * b + 32], R[32 * b:32 * b + 32], b, fxwatch=fw)
            for pc, ad, va in fw[1]:
                if 0x240000 <= ad < 0x320000 and not (V.STACK_LO <= ad < V.STACK_TOP) and not any(
                        o <= ad < o + 0x80 for o in V.DEL_OUT):
                    seen.add(ad & ~3)
            run.out[0].clear(); run.out[1].clear(); run.chr.clear(); run.dl.clear(); run.gr.clear()
        del run
        gc.collect()
    return seen


def proof_a(imgs, q):
    STOCK_DELAY_L1.clear()
    STOCK_DELAY_L1.update(stock_delay_state(imgs["SC01"]))
    print("(a) the stock delay's own L1 words (stored by its call on CMP4): %d, %s" % (
        len(STOCK_DELAY_L1), ["%#x" % a for a in sorted(STOCK_DELAY_L1)[:12]]), flush=True)
    keep = S1.Pair
    S1.Pair = Pair4
    try:
        ok, res = S1.proof_a(imgs, q)
    finally:
        S1.Pair = keep
    res["stock_delay_l1_words"] = sorted("%#x" % a for a in STOCK_DELAY_L1)
    res["note"] = "the keys *_CMP3_SC01 are CMP3's and CMP4's (verify_sc01's proof (a) run with CMP4 in SC01's place)"
    return ok, res


def proof_b(imgs, q):
    keep = S1.OUT
    S1.OUT = OUT                       # code4_function: CMP4's cmp_glue.s / cmp_tail.s at 0x2e2900
    try:
        return S1.proof_b(imgs, q)
    finally:
        S1.OUT = keep


# ---------------------------------------------------------------- (d) the stock delay, bit for bit

def proof_d(imgs, q):
    import vmrun
    cm = imgs["SC01"]
    res, oks = {}, []
    base = dict(VC.BASE, IN_CHR_L=0, IN_CHR_R=0, IN_DEL_L=100, IN_DEL_R=100, IN_REV_L=0, IN_REV_R=0, CHR_DEL=0, CHR_REV=0,
                CHR_VOL=90, DEL_REV=0, DEL_VOL=100, REV_VOL=0, **VC.FL.KNOBS, **VC.comp(32, 24, 32, 64, 3, 39))
    dk = dict(DEL_TIME=23, DEL_X=0, DEL_WID=64, DEL_FDBK=64, DEL_HPF=0, DEL_LPF=127)
    nb = 2400 if not q else 400
    drums = VC.load_input("drums", nb * 32 / SR)
    burst = ([x if i < 1500 * 32 else 0.0 for i, x in enumerate(drums[0])],
             [x if i < 1500 * 32 else 0.0 for i, x in enumerate(drums[1])])
    music = VC.load_input("mix", nb * 32 / SR)
    rn = random.Random(17)
    jumps = {}

    def steps(name, values, every):
        def f(b, ra, rb):
            if b % every == 0:
                v = values[(b // every) % len(values)]
                for r in (ra, rb):
                    r.set(**{name: v})
        return f

    def tempo(values, every):
        def f(b, ra, rb):
            if b % every == 0:
                t = values[(b // every) % len(values)]
                for r in (ra, rb):
                    r.d.set_raw(TEMPO=int(round(120 * t)))
        return f

    def sweep(b, ra, rb):
        if b % 4 == 0:
            ph = (b % 1200) / 1200.0
            x = int(127 * (2 * ph if ph < 0.5 else 2 - 2 * ph))
            for r in (ra, rb):
                r.set(DEL_HPF=x // 2, DEL_LPF=127 - x // 2, DEL_WID=x)

    def fuzz(b, ra, rb):
        if b % 9 == 0:
            if b not in jumps:
                jumps[b] = dict({k: rn.randrange(128) for k in ("DEL_TIME", "DEL_WID", "DEL_FDBK", "DEL_HPF", "DEL_LPF")},
                                DEL_X=rn.randrange(2))
                jumps[b]["_t"] = rn.choice((60, 90, 120, 133.5, 174, 240))
            for r in (ra, rb):
                r.set(**{k: v for k, v in jumps[b].items() if not k.startswith("_")})
                if b % 63 == 0:
                    r.d.set_raw(TEMPO=int(round(120 * jumps[b]["_t"])))
    cases = {}
    for fb in (0, 32, 64, 100, 127):
        cases["FDBK %d, TIME 10 (about 9 repeats), drums 1 s then silence" % fb] = (dict(dk, DEL_FDBK=fb, DEL_TIME=10), burst, None)
    cases["TIME steps 0 / 10 / 23 / 60 / 100 / 127 every 300 blocks, FDBK 64, the mix"] = (
        dk, music, steps("DEL_TIME", (0, 10, 23, 60, 100, 127), 300))
    cases["X on, WID / HPF / LPF swept, FDBK 80, the mix"] = (dict(dk, DEL_X=1, DEL_FDBK=80), music, sweep)
    cases["tempo 120 / 90 / 160 / 60 / 200 every 400 blocks, TIME 31, FDBK 64, the mix"] = (
        dict(dk, DEL_TIME=31), music, tempo((120, 90, 160, 60, 200), 400))
    cases["every delay knob jumping every 9 blocks, X toggled, tempo jumps, the mix"] = (dk, music, fuzz)
    for name, (knobs, (L, R), each) in cases.items():
        t0 = time.time()
        ra = vmrun.Runner(V.candidate_vm(None), dict(base, **knobs))
        rb = vmrun.Runner(V.candidate_vm(cm[0], cache=cm[2]), dict(base, **knobs))
        same = samples = in_same = 0
        pk = 0.0
        for b in range(len(L) // 32):
            if each:
                each(b, ra, rb)
            for r in (ra, rb):
                for c in r.cap.values():
                    c[0].clear()
                    c[1].clear()
                r.block(L[32 * b:32 * b + 32], R[32 * b:32 * b + 32], capture=True)
            xa = S1.fbits(ra.cap["del_out"][0] + ra.cap["del_out"][1])
            xb = S1.fbits(rb.cap["del_out"][0] + rb.cap["del_out"][1])
            samples += 64
            same += 64 if xa == xb else sum(1 for i in range(0, 256, 4) if xa[i:i + 4] == xb[i:i + 4])
            in_same += ra.cap["del_in"] == rb.cap["del_in"]
            pk = max(pk, max(abs(x) for x in rb.cap["del_out"][0] + rb.cap["del_out"][1]))
        nb_ = len(L) // 32
        slot_same = ra.vm.read(DEL_SLOT, DEL_BYTES) == rb.vm.read(DEL_SLOT, DEL_BYTES)
        e = {"blocks": nb_, "samples": samples, "delay_output_identical": same, "delay_input_identical_blocks": in_same,
             "ddr_state_identical_at_end": slot_same, "delay_output_peak": round(pk, 5),
             "insns_mean_stock_CMP4": [round(sum(ra.per_block[1:]) / max(1, nb_ - 1)), round(sum(rb.per_block[1:]) / max(1, nb_ - 1))],
             "seconds": round(time.time() - t0, 1)}
        e["ok"] = same == samples and in_same == nb_ and slot_same and pk > 0
        res[name] = e
        oks.append(e["ok"])
        print("(d) %-80s %s %d/%d, input %d/%d, DDR %s, peak %.3f, %.0fs, rss %d MB" % (
            name, "ok" if e["ok"] else "DIFFERS", same, samples, in_same, nb_, slot_same, pk, e["seconds"],
            memguard.peak_rss_mb()), flush=True)
        del ra, rb
        gc.collect()
        memguard.check(4096)
    res["ok"] = all(oks)
    return res["ok"], res


# ---------------------------------------------------------------- (x) kits saved on SC01: X up to 6

def proof_x(imgs, q):
    """SC01 stored the Space Echo's MODE (0..6) in the delay's X (k 33, stock range 0..1) and AGE in k 40 (Delay FX
    Routing, never read by the stock DSP: notes/T3.4). A kit saved on SC01 with MODE 2..7 reaches the stock delay with X
    1..6 if the ColdFire passes the stored value on unclamped [not known]. Here the worst case on the DSP: X 0..6 at
    FDBK 64 and 100, CMP4 and the stock image on the same blocks (the delay's output identical, so CMP4 = stock there
    too), every sample finite, and the delay output's peak per X (against X 1)."""
    import vmrun
    cm = imgs["SC01"]
    base = dict(VC.BASE, IN_CHR_L=0, IN_CHR_R=0, IN_DEL_L=100, IN_DEL_R=100, IN_REV_L=0, IN_REV_R=0, CHR_DEL=0, CHR_REV=0,
                CHR_VOL=90, DEL_REV=0, DEL_VOL=100, REV_VOL=0, **VC.FL.KNOBS, **VC.comp(32, 24, 32, 64, 3, 39),
                DEL_TIME=10, DEL_WID=64, DEL_HPF=0, DEL_LPF=127)
    nb = 2400 if not q else 400
    drums = VC.load_input("drums", nb * 32 / SR)
    L = [x if i < 1500 * 32 else 0.0 for i, x in enumerate(drums[0])]
    R = [x if i < 1500 * 32 else 0.0 for i, x in enumerate(drums[1])]
    res, oks = {}, []
    for fb in (64, 100):
        for x in range(7):
            ra = vmrun.Runner(V.candidate_vm(None), dict(base, DEL_FDBK=fb, DEL_X=x))
            rb = vmrun.Runner(V.candidate_vm(cm[0], cache=cm[2]), dict(base, DEL_FDBK=fb, DEL_X=x))
            same = samples = bad = 0
            pk = pk_last = 0.0
            for b in range(nb):
                for r in (ra, rb):
                    for c in r.cap.values():
                        c[0].clear()
                        c[1].clear()
                    r.block(L[32 * b:32 * b + 32], R[32 * b:32 * b + 32], capture=True)
                oa = ra.cap["del_out"][0] + ra.cap["del_out"][1]
                ob = rb.cap["del_out"][0] + rb.cap["del_out"][1]
                samples += 64
                same += sum(1 for u, v in zip(oa, ob) if struct.pack("<f", u) == struct.pack("<f", v))
                for v in ob:
                    if v != v or abs(v) == float("inf"):
                        bad += 1
                    else:
                        pk = max(pk, abs(v))
                        if b >= nb - 300:
                            pk_last = max(pk_last, abs(v))
            e = {"identical_to_stock": same, "samples": samples, "nan_inf": bad, "delay_out_peak": round(pk, 4),
                 "peak_last_0_2_s": round(pk_last, 4)}
            e["ok"] = same == samples and bad == 0
            res["FDBK %d, X %d" % (fb, x)] = e
            oks.append(e["ok"])
            print("(x) FDBK %3d X %d %s" % (fb, x, e), flush=True)
            del ra, rb
            gc.collect()
    res["ok"] = all(oks)
    return res["ok"], res


# ---------------------------------------------------------------- (s) stores

def proof_s(imgs, q):
    img = imgs["SC01"]
    info = img[1]
    sbytes = info["layout_check"]["slot_sizes_from_image"]["cmp_slot_bytes"]
    tbytes = info["layout_check"]["slot_sizes_from_image"]["cmp_tail_bytes"]
    save = (0x2e6800, 0x2e6800 + BD.stub.SAVE_BYTES, "stub save area")
    stack = (V.STACK_LO, V.STACK_TOP, "stack")
    allowed = {
        "chr": [stack, save, (0x300000, 0x308400, "FlgSlot"), (S1.CHR_OUT[0], S1.CHR_OUT[0] + 0x80, "chorus out L"),
                (S1.CHR_OUT[1], S1.CHR_OUT[1] + 0x80, "chorus out R")],
        "comp": [stack, save, (SLOT, SLOT + sbytes, "CmpSlot"), (TST, TST + tbytes, "CmpTail"),
                 (0x25ba68, 0x25ba68 + 0x80, "out L"), (0x25bae8, 0x25bae8 + 0x80, "out R"), (VC.GR_Y2, VC.GR_Y2 + 4, "0x257c1c"),
                 (VC.TX[0] + 0x10, VC.TX[0] + 0x18, "reply page 0"), (VC.TX[1] + 0x10, VC.TX[1] + 0x18, "reply page 1")]}
    ours = [(0x2dd600, 0x2e1c40, "block-1 code"), (0x2e2900, 0x2e4f46, "code4 (glue, cmp_tail)"),
            (0x2e6000, 0x2e6044, "constant data"), (0x308400, 0x30ff76, "block-2 code (the compressor)"),
            (0x30ff76, 0x318000, "0x30ff76-0x318000 (with 0x315f8c-0x318000)")]
    L, R = VC.load_input("mix", 130 * 32 / SR)
    mv = {}
    rn = random.Random(3)

    def jump(b, fr):
        if b % 5 == 0:
            if b not in mv:
                mv[b] = {k: rn.randrange(128) for k in ("DEL_TIME", "DEL_WID", "DEL_FDBK", "DEL_HPF", "DEL_LPF",
                                                        *VC.FL.KNOBS)}
                mv[b]["DEL_X"] = rn.randrange(2)
            for k, v in mv[b].items():
                VC.put(fr, k, v << 8)
    words = dict(dict(VC.BASE, **VC.FXW, **VC.comp(32, 24, 32, 64, 3, 64)), DEL_FDBK=100, CHR_DEL=40)
    census = {}
    from vm import watch as W, watch_log
    for which in ("chr", "comp"):
        vm = V.candidate_vm(img[0], cache=img[2])
        run = S1.FxTailRun(vm, words, VC.ROUTE_INFX, S1.sched3(S1.DISTINCT, (20, 64, 127)))
        log = []
        for b in range(125):
            jump(b, run.r.d.frame)
            sel = b in (0, 1, 2, 3, 40, 41, 60, 61, 80, 81, 120, 121)
            wl = [] if sel else None
            fw = (which, []) if (sel and which != "comp") else None
            run.tblock(L[32 * b:32 * b + 32], R[32 * b:32 * b + 32], b, watch=wl if which == "comp" else None,
                       **({"fxwatch": fw} if fw else {}))
            if wl is not None and which == "comp":
                log += wl
            if fw:
                log += fw[1]
            run.out[0].clear(); run.out[1].clear(); run.chr.clear(); run.dl.clear(); run.gr.clear()
        al = allowed[which]
        stray = [(pc, ad) for pc, ad, va in log if not any(lo <= ad < hi for lo, hi, _ in al)]
        by = {}
        for pc, ad, va in log:
            nm = next((n_ for lo, hi, n_ in al if lo <= ad < hi), "OTHER")
            by[nm] = by.get(nm, 0) + 1
        into_ours = [ad for pc, ad, va in log if any(lo <= ad < hi for lo, hi, _ in ours)]
        census[which] = {"stores": len(log), "by_range": by, "outside": ["%#x@%#x" % (ad, pc) for pc, ad in stray[:10]],
                         "outside_count": len(stray), "into_our_code_or_the_forbidden_range": len(into_ours)}
        print("(s)", which, census[which], flush=True)
        del vm, run
        gc.collect()
    whole = {}
    for lo, hi, nm in ours:
        vm = V.candidate_vm(img[0], cache=img[2])
        run = S1.FxTailRun(vm, words, VC.ROUTE_INFX, S1.sched3(S1.DISTINCT, (20, 64, 127)))
        W(vm, lo, hi)
        n = 0
        for b in range(60):
            jump(b, run.r.d.frame)
            run.tblock(L[32 * b:32 * b + 32], R[32 * b:32 * b + 32], b)
            n += len(watch_log(vm))
            W(vm, lo, hi)
            run.out[0].clear(); run.out[1].clear(); run.chr.clear(); run.dl.clear(); run.gr.clear()
        whole[nm] = n
        del vm, run
        gc.collect()
    print("(s) stores into our code over 60 whole blocks:", whole, flush=True)
    ok = (all(v["outside_count"] == 0 and v["into_our_code_or_the_forbidden_range"] == 0 for v in census.values())
          and all(v == 0 for v in whole.values()))
    return ok, {"calls": census, "whole_blocks_stores_into": whole, "ok": ok}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--only", help="comma-separated proofs (a,b,d,s,x)")
    a = ap.parse_args()
    only = set(a.only.split(",")) if a.only else set("abdsx")
    t_all = time.time()
    imgs = {"SC01": S1.load_image(OUT, "CMP4"), "CMP3": S1.load_image(CMP3_DIR, "CMP3")}
    stale = [s for s, h in imgs["SC01"][1]["sources"].items() if BD.sha((ROOT / s).read_bytes()) != h]
    if stale:
        raise SystemExit(f"CMP4 was built from other sources ({stale}): run build.py CMP4")
    OFF.update(VC.offsets(OUT / "offsets", "CMP3"))      # CmpSlot is CMP3's (mbcomp.c, the glue's CmpSlot unchanged)
    if imgs["SC01"][1]["layout_check"]["slot_sizes_from_image"]["cmp_slot_bytes"] != \
            imgs["CMP3"][1]["layout_check"]["slot_sizes_from_image"]["cmp_slot_bytes"]:
        raise SystemExit("CmpSlot's size differs between CMP3 and CMP4")
    res = {"name": "CMP4", "section7_sha256": imgs["SC01"][1]["section7"]["sha256"],
           "cmp3_section7_sha256": imgs["CMP3"][1]["section7"]["sha256"], "proofs_run": "".join(sorted(only))}
    oks = {}
    for k, fn in (("s", proof_s), ("d", proof_d), ("x", proof_x), ("b", proof_b), ("a", proof_a)):
        if k in only:
            t0 = time.time()
            oks[k], res[k] = fn(imgs, a.quick)
            res[k + "_seconds"] = round(time.time() - t0)
            print("== (%s) %s, %.0fs" % (k, "PASS" if oks[k] else "FAIL", time.time() - t0), flush=True)
            part = OUT / ("verify-%s.json" % ("".join(sorted(only)) + ("-quick" if a.quick else "")))
            part.write_text(json.dumps(dict(res, ok=all(oks.values()), seconds=round(time.time() - t_all)), indent=1))
    res["ok"] = all(oks.values())
    res["seconds"] = round(time.time() - t_all)
    res["peak_rss_mb"] = round(memguard.peak_rss_mb())
    name = "verify.json" if not a.only and not a.quick else "verify-%s.json" % ("".join(sorted(only)) + ("-quick" if a.quick else ""))
    (OUT / name).write_text(json.dumps(res, indent=1))
    print("VERDICT", "PASS" if res["ok"] else "FAIL", res["seconds"], "s", flush=True)
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
