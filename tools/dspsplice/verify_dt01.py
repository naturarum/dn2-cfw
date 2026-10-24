#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""DT01 (2026-10-23; notes/DT01-code-diet.md): the DSP proofs of DT01 = FL02 on a code diet, the same sound.

    python3 tools/dspsplice/verify_dt01.py [--quick] [--only a,e,s]

(a) DT01 against FL02 (out/FL02/FL02-dsp): verify_sc01.py's proof (a) (the 53 settings CMP4 used: zero and invalid
    tails, MIX 0, equal MIX and the relock, one band wet, the guard and re-init injections, the cost cases with the
    flanger's knobs jumping, seeded old kits; the delay slot silent) with FL02 in CMP3's place and DT01 in SC01's: every
    handler output sample, the chorus slot's output (the flanger) every block, each band's gain reduction every block,
    both reply pages every block but word 0 (the handler's cycle count; the meters too: the glue is FL02's), and at the
    end L1 word for word outside our
    code, data, stack and the stub save area, with the flanger's state compared field for field (DT01 keeps the rings
    after the fields: its slot is rearranged into FL02's order first, clang's offsetof);
(e) the same lockstep with the delay slot ON (the stock delay fed by the chorus and the input, FDBK 64 / 100 / 127,
    every FX knob jumping, tempo changes): the whole handler output, the delay slot's output every block, L1 at the end;
(s) stores on DT01 (verify_cmp4.py's proof (s)): the chorus call and the compressor call store only into their own
    state, the stack, the save area and their outputs; over whole blocks no store into our code nor into
    0x30ff76-0x318000.
Instructions per block, mean and max, of both images in every case; the worst block over all cases.
Results: out/DT01/DT01-dsp/verify.json (or verify-<only>.json).
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
sys.path.insert(0, str(ROOT / "tools/sharcvm/tests"))
import verify_sc01 as S1  # noqa: E402
import verify_cmp4 as C4  # noqa: E402
import memguard  # noqa: E402
import build as BD  # noqa: E402

VC, V = S1.VC, S1.V
SLOT, OFF, SR = S1.SLOT, S1.OFF, S1.SR
OUT = ROOT / "out/DT01/DT01-dsp"
FL02_DIR = ROOT / "out/FL02/FL02-dsp"
FLG = 0x300000
WORST = {"FL02": (0, ""), "DT01": (0, "")}
LAY = {}


def flanger_layouts():
    """FlgSlot / Flanger offsets of both builds (tools/sharcvm/tests/dt01check.py, clang)"""
    import dt01check as DC
    DC.OUT = OUT / "layout"
    return {n: DC.layout(n) for n in ("FL02", "DT01")}


def flg_fl02_order(b):
    """DT01's FlgSlot bytes in FL02's order (the rings first in the Flanger)"""
    lay = LAY["DT01"]
    fl, ring, n = lay["fl"], lay["ring"], lay["flanger"]
    f = b[fl:fl + n]
    return b[:fl] + f[ring:] + f[:ring] + b[fl + n:]


class PairDT(S1.Pair):
    """FL02 (a) and DT01 (b) on the same blocks: verify_sc01's Pair with the reply pages compared whole and the L1 check
    at the end blind only to our code, data, stack and the save area, the flanger's slot rearranged"""

    def __init__(self, imgs, words, raw, sched, silent=True):
        if silent:
            words = dict(words, **S1.SILENT_DELAY)
        self.a = S1.FxTailRun(V.candidate_vm(imgs["CMP3"][0], cache=imgs["CMP3"][2]), words, raw, sched)
        self.b = S1.FxTailRun(V.candidate_vm(imgs["SC01"][0], cache=imgs["SC01"][2]), words, raw, sched)
        self.nb = self.samples = self.same = self.gr_same = self.pages_same = self.hw11_same = self.chr_same = 0
        self.del_zero = self.del_same = 0
        self.page_offsets = set()
        self.peak = 0.0

    def block(self, L, R, b, each=None, inject=None):
        if each:
            each(b, self.a.r.d.frame)
            each(b, self.b.r.d.frame)
        self.a.tblock(L, R, b, inject=inject, tx_full=True)
        self.b.tblock(L, R, b, inject=inject, tx_full=True)
        oa = self.a.out[0] + self.a.out[1]
        ob = self.b.out[0] + self.b.out[1]
        for r in (self.a, self.b):
            r.out[0].clear()
            r.out[1].clear()
        self.samples += len(oa)
        if S1.fbits(oa) == S1.fbits(ob):
            self.same += len(oa)
        else:
            self.same += sum(1 for x, y in zip(oa, ob) if struct.pack("<f", x) == struct.pack("<f", y))
        self.peak = max(self.peak, max((abs(x) for x in ob if x == x), default=0.0))
        self.gr_same += S1.fbits(self.a.gr.pop()) == S1.fbits(self.b.gr.pop())
        self.chr_same += self.a.chr.pop() == self.b.chr.pop()
        da, db_ = self.a.dl.pop(), self.b.dl.pop()
        self.del_zero += (da == bytes(0x100)) and (db_ == bytes(0x100))
        self.del_same += da == db_
        pa, pb = self.a.tx_pages.pop(), self.b.tx_pages.pop()
        pa, pb = bytearray(pa), bytearray(pb)
        for k in (0, 0x1000):
            pa[k:k + 4] = pb[k:k + 4] = bytes(4)           # word 0: the handler's cycle count (verify_cmp.py (h))
        if pa == pb:
            self.pages_same += 1
        else:
            for i in range(0, len(pa), 2):
                if pa[i:i + 2] != pb[i:i + 2]:
                    self.page_offsets.add(i)
        self.hw11_same += (pa[0x16:0x18] == pb[0x16:0x18] and pa[0x1016:0x1018] == pb[0x1016:0x1018])
        self.nb += 1

    def finish(self):
        va, vb = self.a.vm, self.b.vm
        ignore = [(0x2dd600, 0x2e1c40), (0x2e2900, 0x2e6000), (0x2e6000, 0x2e7000), (0x308400, 0x318000),
                  (V.STACK_LO, V.STACK_TOP), (FLG, FLG + 0x8400),
                  (VC.TX[0], VC.TX[0] + 4), (VC.TX[1], VC.TX[1] + 4)]      # reply word 0: the cycle count
        mem = []
        for lo, hi in ((0x240000, 0x2f0000), (0x300000, 0x320000)):
            x, y = va.read(lo, hi - lo), vb.read(lo, hi - lo)
            if x != y:
                for i in range(0, hi - lo, 4):
                    ad = lo + i
                    if x[i:i + 4] != y[i:i + 4] and not any(p <= ad < q for p, q in ignore):
                        mem.append(ad)
        fa, fb = va.read(FLG, 0x8400), flg_fl02_order(vb.read(FLG, 0x8400))
        flg_diff = [FLG + i for i in range(0, 0x8400, 4) if fa[i:i + 4] != fb[i:i + 4]]
        pa, pb = self.a.r.per_block, self.b.r.per_block
        for tag, p in (("FL02", pa), ("DT01", pb)):
            if p[1:] and max(p[1:]) > WORST[tag][0]:
                WORST[tag] = (max(p[1:]), self.case)
        r = {"blocks": self.nb, "samples": self.samples, "samples_identical": self.same,
             "flanger_output_blocks_identical": self.chr_same, "delay_slot_output_blocks_identical": self.del_same,
             "delay_slot_silent_blocks_both": self.del_zero,
             "gain_reduction_blocks_identical": self.gr_same, "reply_pages_identical_but_word0": self.pages_same,
             "reply_byte_offsets_that_differ": sorted("%#x" % o for o in self.page_offsets),
             "l1_words_that_differ_outside_ours": ["%#x" % a for a in mem[:20]], "l1_count": len(mem),
             "flanger_slot_words_that_differ": len(flg_diff),
             "trips_reinits_FL02_DT01": [[v.rd32(SLOT + OFF["trips"]), v.rd32(SLOT + OFF["reinits"])] for v in (va, vb)],
             "insns_mean_CMP3_SC01": [round(sum(pa[1:]) / max(1, len(pa) - 1)), round(sum(pb[1:]) / max(1, len(pb) - 1))],
             "insns_max_CMP3_SC01": [max(pa[1:] or [0]), max(pb[1:] or [0])], "peak": round(self.peak, 4)}
        r["ok"] = (self.same == self.samples and self.gr_same == self.nb and self.pages_same == self.nb
                   and self.chr_same == self.nb and self.del_same == self.nb and not mem and not flg_diff)
        return r

    def run(self, L, R, each=None, inject=None):
        import inspect
        self.case = inspect.stack()[1].frame.f_locals.get("name", "?")
        return super().run(L, R, each=each, inject=inject)


def proof_a(imgs, q):
    keep = S1.Pair
    S1.Pair = PairDT
    try:
        ok, res = S1.proof_a(imgs, q)
    finally:
        S1.Pair = keep
    res["note"] = "the keys *_CMP3_SC01 are FL02's and DT01's (verify_sc01's proof (a) run with FL02 in CMP3's place " \
                  "and DT01 in SC01's)"
    return ok, res


def proof_e(imgs, q):
    """the delay slot ON: the stock delay in both, fed by the input and the chorus"""
    res, ok_all = {}, True
    nb = 1500 if not q else 200
    mix = VC.load_input("mix", nb * 32 / SR)
    rn = random.Random(17)
    jumps = {}
    knames = list(VC.FL.KNOBS) + ["DEL_TIME", "DEL_WID", "DEL_FDBK", "DEL_HPF", "DEL_LPF"]

    def fx_jump(b, fr):
        if b % 25 == 0:
            if b not in jumps:
                jumps[b] = [rn.choice([0, 127, rn.randrange(128)]) for _ in knames] + [rn.randrange(2)]
            for k, v in zip(knames, jumps[b]):
                VC.put(fr, k, v << 8)
            VC.put(fr, "DEL_X", jumps[b][-1] << 8)
    base = dict(dict(VC.BASE, **VC.FXW), CHR_DEL=60, DEL_VOL=100, DEL_TIME=7)       # TIME 7: a 1/16 at 120 BPM
    cases = {}
    for fb in (64, 100, 127):
        cases["delay on, FDBK %d, chorus into it, typical compressor" % fb] = (
            dict(dict(base, **VC.TYPICAL), DEL_FDBK=fb), S1.sched3(S1.DEFAULTS, (39, 39, 39)), None)
    cases["delay on, every flanger and delay knob jumping, distinct bands"] = (
        dict(base, **VC.TYPICAL), S1.sched3(S1.DISTINCT, (20, 64, 127)), fx_jump)
    for name, (words, sched, each) in cases.items():
        t0 = time.time()
        jumps.clear()
        pr = PairDT(imgs, words, VC.ROUTE_INFX, sched, silent=False)
        pr.case = name
        r = pr.run(mix[0], mix[1], each=each)
        r["seconds"] = round(time.time() - t0, 1)
        res[name] = r
        ok_all &= r["ok"]
        print("(e) %-70s %s %d/%d samples, delay %d/%d, insns %s, %.0fs" % (
            name[:70], "ok" if r["ok"] else "DIFFERS", r["samples_identical"], r["samples"],
            r["delay_slot_output_blocks_identical"], r["blocks"], r["insns_mean_CMP3_SC01"], r["seconds"]), flush=True)
        del pr
        gc.collect()
        memguard.check(4096)
    res["ok"] = ok_all
    return ok_all, res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--only", help="comma-separated proofs (a,e,s)")
    a = ap.parse_args()
    only = set(a.only.split(",")) if a.only else set("aes")
    t_all = time.time()
    imgs = {"SC01": S1.load_image(OUT, "DT01"), "CMP3": S1.load_image(FL02_DIR, "FL02")}
    for k in ("SC01", "CMP3"):
        stale = [s for s, h in imgs[k][1]["sources"].items() if BD.sha((ROOT / s).read_bytes()) != h]
        if stale:
            raise SystemExit(f"{imgs[k][1]['name']} was built from other sources ({stale})")
    OFF.update(VC.offsets(OUT / "offsets", "CMP3"))      # CmpSlot: CMP3's = CMP4's = FL02's = DT01's (the glue, mbcomp.h)
    if imgs["SC01"][1]["layout_check"]["slot_sizes_from_image"] != imgs["CMP3"][1]["layout_check"]["slot_sizes_from_image"]:
        raise SystemExit("CmpSlot's or CmpTail's size differs between FL02 and DT01")
    LAY.update(flanger_layouts())
    if LAY["FL02"]["slot"] != LAY["DT01"]["slot"] or LAY["FL02"]["ring"] != 0:
        raise SystemExit("unexpected flanger layouts %s" % LAY)
    res = {"name": "DT01", "section7_sha256": imgs["SC01"][1]["section7"]["sha256"],
           "fl02_section7_sha256": imgs["CMP3"][1]["section7"]["sha256"], "proofs_run": "".join(sorted(only)),
           "flanger_layouts": LAY}
    oks = {}
    for k, fn in (("s", C4.proof_s), ("e", proof_e), ("a", proof_a)):
        if k in only:
            t0 = time.time()
            oks[k], res[k] = fn(imgs, a.quick)
            res[k + "_seconds"] = round(time.time() - t0)
            print("== (%s) %s, %.0fs" % (k, "PASS" if oks[k] else "FAIL", time.time() - t0), flush=True)
            res["worst_block"] = {t: {"instructions": w, "case": c} for t, (w, c) in WORST.items()}
            part = OUT / ("verify-%s.json" % ("".join(sorted(only)) + ("-quick" if a.quick else "")))
            part.write_text(json.dumps(dict(res, ok=all(oks.values()), seconds=round(time.time() - t_all)), indent=1))
    res["ok"] = all(oks.values())
    res["seconds"] = round(time.time() - t_all)
    res["peak_rss_mb"] = round(memguard.peak_rss_mb())
    res["worst_block"] = {t: {"instructions": w, "case": c} for t, (w, c) in WORST.items()}
    name = "verify.json" if not a.only and not a.quick else "verify-%s.json" % ("".join(sorted(only)) + ("-quick" if a.quick else ""))
    (OUT / name).write_text(json.dumps(res, indent=1))
    print("worst block", res["worst_block"], flush=True)
    print("VERDICT", "PASS" if res["ok"] else "FAIL", res["seconds"], "s", flush=True)
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
