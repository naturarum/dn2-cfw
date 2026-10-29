#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""The ten DSP packs of Digiwerk 0.2.0 (2026-10-29; build.py pack_recipe): out/NAME/NAME-dsp/section7.bin for NAME in
CEEE EFEE EETE EEES CETE CEES EFTE EFES EETS EFTS, the FX slots in the order compressor, chorus, delay, reverb (C, F,
T, S ours: CMP3, the flanger, the tape echo, the spring reverb; E Elektron's). Whole images in sharcvm.

    python3 tools/dspsplice/verify_packs.py [--only lock,mix,stores,soak] [--packs CEEE,EFTS] [--workers 3]
                                            [--soak-seconds 60] [--quick]

(lock)   each of our effects in a pack against the image it was proven in, the two in lockstep on the same frames and
         input: CMP3 against DC02, the flanger against DC02, the tape echo against TE01, the spring against SP02.
         CMP3: every effect's send and VOL at 0 (the slots return 0 in both; checked), the input alone into the
         compressor: every handler output sample, each band's gain reduction and both reply pages' meter half-words
         (8..11) every block; the flanger: its slot output every block (its input is the tracks' sends only); the tape
         echo and the spring: their slot output every block, a cross-send into them (chorus -> delay, chorus -> reverb,
         delay -> reverb) on only where the sending slot is the same effect in both images, else 0. All bit for bit.
(mix)    the whole mix with every effect working (the input into all three FX, the cross-sends on, the compressor's bands
         through the tail), a typical case and a worst case (noise, every band knob and MIX moving, every chorus, delay
         and reverb knob and AGE jumping each 20 blocks, tempo changes): every output sample finite and within the
         master ceiling 0.6931; the echo's wet within 0.7 and its tape within +-1 and finite; the spring's output finite
         and within 2.0 (its soft clips; it has no hard ceiling) and SpgSlot's magic; on every instruction executed in
         the four code regions (sharcvm's profile), inside a placed function (build.json function_table), none in a
         region's free tail; instructions per block (mean, max) and the stack depth, with DC02 and the stock image
         (EEEE) on the same cases.
(stores) over whole blocks with every knob jumping, no store into any section of ours (each placed code section,
         the constant data) nor into 0x315f8c-0x318000 (the proven regions' unused room is listed apart); each call of
         our effects stores only into its own state, the stack, the stub save area and its output buffers (the
         compressor: CmpSlot, CmpTail, its outputs, the gain-reduction word and the reply pages' meter words).
(soak)   SOAK seconds of audio per pack (soak_te01.py's checks, generalised): every knob of ours walked by a hand
         (soak_sc01's "human" recipe: echo knobs, AGE, MODE steps, band knobs and MIX, MUP mangles, tempo changes;
         the spring's seven knobs walked too), drums, the mix and noise; a block over 3M instructions, a trap, a
         hardware stack not restored, an output not finite or over its ceiling, the echo's state and the 32 frames it
         wrote, SpgSlot's magic, a store into one of our sections (one range per block in turn), the C stack's
         high-water mark, and at the end every instruction run in the regions inside a placed function.
Results: out/packs-verify/<pack>-<check>.json, out/packs-verify/summary.json.
"""
import argparse
import gc
import json
import math
import random
import struct
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "tools/sharcvm/tests"))

PACKS = ("CEEE", "EFEE", "EETE", "EEES", "CETE", "CEES", "EFTE", "EFES", "EETS", "EFTS")
REFS = {"DC02": "CFEE", "TE01": "CFTE", "SP02": "CFES"}
OUT = ROOT / "out/packs-verify"
REV_CALL, REV_RET = 0x1c1d11, 0x1c1d18
REV_OUT = (0x25b368, 0x25b3e8)
SE_SLOT, SE_BYTES, SE_RING_ADDR = 0x8005912c, 4163304, 0x8005912c + 48 + 67256   # TE01's layout (soak_te01 offsets)
SE_RING = 512000
SE_MAGIC, SPG_MAGIC = 0x53454531, 0x53504e31
SPG, SPG_BYTES = 0x80000018, 43296
SPG_CONST = (0x8000b000, 0x8000c000)
SAVE = (0x2e6800, 0x2e686c)
K48, AGE_W, FRAME_IMG = 0xa28, 0xa18, 0x25c48c
REV_WORDS = ("REV_PRE", "REV_DEC", "REV_FREQ", "REV_GAIN", "REV_HPF", "REV_LPF")
SPRING_INIT = (0, 64, 64, 64, 0, 127, 96)        # PRE DEC MODE DIRT HPF TONE WDTH (verify_sp02.SPRING_KNOBS)
FORBID = (0x315f8c, 0x318000)
PROVEN = [(0x2dd600, 0x2e1c36, "c1"), (0x2e2900, 0x2e4f46, "c4"), (0x308400, 0x30ff76, "c3"), (0x310000, 0x315f8c, "c2")]
SPRING_MAX = 2.0
G = {}


def setup(off):
    if G:
        return
    import verify_sc01 as S1
    import memguard
    import build as BD
    S1.OFF.update(off)
    G.update(S1=S1, V=S1.V, VC=S1.VC, V2=S1.V2, FB=S1.V.fullblock, memguard=memguard, BD=BD)


def image(name):
    """(stream, info, cache) of a pack or a reference image; EEEE: the stock image"""
    S1 = G["S1"]
    if name == "EEEE":
        return None, None, None
    return S1.load_image(ROOT / f"out/{name}/{name}-dsp", name)


def new_vm(name):
    V = G["V"]
    st, info, cache = image(name)
    return V.candidate_vm(st, cache=cache)


def letters(name):
    return {"DC02": "CFEE", "TE01": "CFTE", "SP02": "CFES", "EEEE": "EEEE"}.get(name, name)


def tail_vals(knobs17, mixes):
    return [int(round(256 * v)) & 0xffff for v in knobs17] + [int(round(256 * m)) & 0xffff for m in mixes] + [0] * 7


def finite(x):
    return x == x and x not in (float("inf"), float("-inf"))


def peak(xs):
    p, bad = 0.0, 0
    for x in xs:
        if not finite(x):
            bad += 1
        elif abs(x) > p:
            p = abs(x)
    return p, bad


class Run:
    """one image, the whole handler per block: the frame's tail written for the compressor's bands, the chorus, delay
    and reverb outputs captured at their returns, optional store watches over one call or the whole block"""

    def __init__(self, name, words, raw, tempo=120.0, profile=False):
        V, VC = G["V"], G["VC"]
        self.name = name
        self.vm = new_vm(name)
        if profile:
            self.vm.lib.vm_set_profile(self.vm.h, 1)
        self.r = V.Runner(self.vm, words, tempo=tempo)
        self.r.d.set_raw(**raw)
        self.fr = self.r.d.frame
        self.per_block = []
        self.stack = 0

    def prime(self):
        """the frame in the DSP's frame image, as if it had come the block before (verify_sp02.Run.prime_image)"""
        self.vm.write(FRAME_IMG, bytes(self.fr[:0xa80]))

    def block(self, L, R, b, tail=None, watch_call=None, paint=False):
        V, VC, FB, V2 = G["V"], G["VC"], G["FB"], G["V2"]
        vm = self.vm
        from vm import watch as W, watch_log
        if tail is not None:
            struct.pack_into("<30H", self.fr, V2.TAIL, *V2.tail_words(1 + b, tail))
        if paint:
            i7 = vm.reg("I7")
            vm.write(V.STACK_LO, V.PAINT.to_bytes(4, "little") * ((i7 - V.STACK_LO) // 4))
        spi = vm.rd32(FB.SPI_HALF)
        vm.write(FB.RX_BASE + 0x1000 * spi, bytes(self.fr))
        h = vm.rd32(FB.DMA_HALF)
        vm.write(FB.CODEC_IN + 0x100 * h, struct.pack("<64I", *[FB.q31(v) for pair in zip(L, R) for v in pair]))
        c = {"log": []}

        def on(which):
            def f():
                if watch_call == which:
                    W(vm, 0, 0xffffffff)
            return f

        def off(which, key=None, buf=None):
            def f():
                if watch_call == which:
                    c["log"].extend(watch_log(vm))
                    W(vm, 0, 0)
                if key:
                    c[key] = vm.read(buf[0], 0x80) + vm.read(buf[1], 0x80)
            return f
        probes = [(V.CHR_CALL, on("chr")), (V.CHR_RET, off("chr", "chr", V.CHR_OUT)), (V.DEL_CALL, on("del")),
                  (V.DEL_RET, off("del", "del", V.DEL_OUT)), (REV_CALL, on("rev")), (REV_RET, off("rev", "rev", REV_OUT)),
                  (VC.COMP_CALL, on("comp")), (VC.COMP_RET, off("comp"))]
        n0 = vm.icount
        self.r._call_probed(probes)
        self.per_block.append(vm.icount - n0)
        if paint:
            i7 = vm.reg("I7")
            raw = vm.read(V.STACK_LO, i7 - V.STACK_LO)
            word = V.PAINT.to_bytes(4, "little")
            low = next((V.STACK_LO + k for k in range(0, len(raw), 4) if raw[k:k + 4] != word), i7)
            self.stack = max(self.stack, i7 - low)
        c["out"] = vm.read(FB.OUT_FLOATS, 0x100)
        return c


def inputs(kind, nb, seed):
    VC = G["VC"]
    if kind == "noise":
        r = random.Random(seed)
        return [r.uniform(-1, 1) for _ in range(nb * 32)], [r.uniform(-1, 1) for _ in range(nb * 32)]
    if kind == "pluck":
        from fx import read_wav
        pl, pr, _ = read_wav(ROOT / "out/T3.11/dry/pluck.wav")
        n = nb * 32
        return (pl + [0.0] * n)[:n], (pr + [0.0] * n)[:n]
    if kind == "sine":
        s = [0.9 * math.sin(2 * math.pi * 220 * i / 48000) if i < 48000 else 0.0 for i in range(nb * 32)]
        return s, list(s)
    return VC.load_input(kind, nb * 32 / 48000)


def moving17(b):
    return [20 + 4 * (b % 2), 3 + b % 2, 10 + 4 * (b % 2), 40 + 4 * (b % 2), 50 + 4 * (b % 2), 60 + 4 * (b % 2),
            60 - 4 * (b % 2), 6 - b % 2, 40 + 4 * (b % 2), 60 + 4 * (b % 2), 30 + 4 * (b % 2), 50 + 4 * (b % 2),
            90 - 4 * (b % 2), 8 - b % 2, 80 + 4 * (b % 2), 100 + 4 * (b % 2), 90 - 4 * (b % 2)]


def mixes_apart(b):
    return [0 if ((b + 13 * k) // 40) % 2 else (40, 64, 100)[k] for k in range(3)]


def jumper(seed, every=20, chr_=True, dly=True, rev=True, xmax=7):
    """every chorus, delay and reverb knob, AGE and k 48 jumping each EVERY blocks, the tempo too (the same sequence
    for every image built with the same seed)"""
    rn = random.Random(seed)
    memo = {}

    def f(b, fr):
        VC = G["VC"]
        if b % every:
            return
        if b not in memo:
            j = {}
            if chr_:
                j.update({k: rn.randrange(128) for k in VC.FL.KNOBS})
            if dly:
                j.update({k: rn.randrange(128) for k in ("DEL_TIME", "DEL_WID", "DEL_FDBK", "DEL_HPF", "DEL_LPF")})
                j["DEL_X"] = rn.randrange(xmax)
                j["age"] = rn.randrange(128)
            if rev:
                j.update({k: rn.randrange(128) for k in REV_WORDS})
                j["k48"] = rn.randrange(128)
            j["bpm"] = rn.choice((90.0, 120.0, 120.0, 174.0))
            memo[b] = j
        for k, v in memo[b].items():
            if k == "age":
                struct.pack_into("<H", fr, AGE_W, v << 8)
            elif k == "k48":
                struct.pack_into("<H", fr, K48, v << 8)
            elif k == "bpm":
                VC.put(fr, "TEMPO", int(round(120 * v)))
            else:
                VC.put(fr, k, v << 8)
    return f


# ---------------------------------------------------------------- (lock)

def lockstep(pack, ref, words, raw, inp, nb, key, tail=None, each=None, age=0, k48=None, prime=False, tempo=120.0):
    """pack and ref on the same blocks; key: what is compared ("mix": handler output, gain reductions, meter words;
    "chr" / "del" / "rev": that slot's output)"""
    S1, VC, OFF = G["S1"], G["VC"], G["S1"].OFF
    runs = [Run(n, words, raw, tempo=tempo) for n in (ref, pack)]
    for r in runs:
        struct.pack_into("<H", r.fr, AGE_W, age << 8)
        if k48 is not None:
            struct.pack_into("<H", r.fr, K48, k48 << 8)
        if prime:
            r.prime()
    res = {"blocks": nb, "samples": 0, "identical": 0, "first_block_that_differs": None, "max_abs_diff": 0.0,
           "peak": 0.0, "slots_zero_both": {"chr": 0, "del": 0, "rev": 0}}
    if key == "mix":
        res.update(gr_identical=0, meters_identical=0, reply_offsets_that_differ=set())
    for b in range(nb):
        tv = tail(b) if tail else None
        if each:
            for r in runs:
                each(b, r.fr)
        cs = [r.block(inp[0][32 * b:32 * b + 32], inp[1][32 * b:32 * b + 32], b, tail=tv) for r in runs]
        if key == "mix":
            xa, xb = cs[0]["out"], cs[1]["out"]
            ga = runs[0].vm.read(S1.SLOT + OFF["gr0"], 12)
            gb = runs[1].vm.read(S1.SLOT + OFF["gr0"], 12)
            res["gr_identical"] += ga == gb
            pa = b"".join(runs[0].vm.read(t, 0x1000) for t in VC.TX)
            pb = b"".join(runs[1].vm.read(t, 0x1000) for t in VC.TX)
            res["meters_identical"] += all(pa[k + 0x10:k + 0x18] == pb[k + 0x10:k + 0x18] for k in (0, 0x1000))
            if pa[4:] != pb[4:] or pa[0x1004:] != pb[0x1004:]:
                for i in range(4, len(pa), 2):
                    if i % 0x1000 >= 4 and pa[i:i + 2] != pb[i:i + 2] and len(res["reply_offsets_that_differ"]) < 40:
                        res["reply_offsets_that_differ"].add(i)
            for s in ("chr", "del", "rev"):
                res["slots_zero_both"][s] += cs[0][s] == bytes(0x100) and cs[1][s] == bytes(0x100)
        else:
            xa, xb = cs[0][key], cs[1][key]
        res["samples"] += 64
        if xa == xb:
            res["identical"] += 64
        else:
            fa, fb = struct.unpack("<64f", xa), struct.unpack("<64f", xb)
            res["identical"] += sum(1 for i in range(64) if xa[4 * i:4 * i + 4] == xb[4 * i:4 * i + 4])
            if res["first_block_that_differs"] is None:
                res["first_block_that_differs"] = b
            res["max_abs_diff"] = max(res["max_abs_diff"], max((abs(x - y) for x, y in zip(fa, fb)
                                                                 if finite(x) and finite(y)), default=0.0))
        res["peak"] = max(res["peak"], peak(struct.unpack("<64f", xb))[0])
    if key == "mix":
        res["reply_offsets_that_differ"] = sorted("%#x" % o for o in res["reply_offsets_that_differ"])
        res["ok"] = (res["identical"] == res["samples"] and res["gr_identical"] == nb and res["meters_identical"] == nb
                     and all(v == nb for v in res["slots_zero_both"].values()))
    else:
        res["ok"] = res["identical"] == res["samples"]
    res["insns_mean_ref_pack"] = [round(sum(r.per_block[1:]) / max(1, len(r.per_block) - 1)) for r in runs]
    del runs
    gc.collect()
    return res


def check_lock(pack, q):
    VC, S1 = G["VC"], G["S1"]
    import verify_flg as FL
    import verify_se as VS
    res = {}
    nb = 1200 if not q else 150
    has = set(ch for ch in pack if ch != "E")

    def done(name, r):
        res[name] = r
        print("[%s lock] %-70s %s %d/%d, %.0fs" % (pack, name[:70], "ok" if r["ok"] else "DIFFERS", r["identical"],
              r["samples"], r.get("seconds", 0)), flush=True)
        G["memguard"].check(3072)

    def run(name, *a, **k):
        t0 = time.time()
        r = lockstep(pack, *a, **k)
        r["seconds"] = round(time.time() - t0, 1)
        done(name, r)

    def cross(ref):
        """the cross-sends allowed: a slot's output into the next only where that slot is the same effect in both"""
        rl = REFS[ref]
        same_chr = pack[1] == rl[1]
        same_del = pack[2] == rl[2] and same_chr
        return dict(CHR_DEL=40 if same_chr else 0, CHR_REV=60 if same_chr else 0, DEL_REV=60 if same_del else 0)
    if "C" in has:
        nofx = dict(VC.BASE, **VC.NOFX)
        mix, drums, noise = inputs("mix", nb, 0), inputs("drums", nb, 0), inputs("noise", nb, 7)
        run("CMP3: band defaults, the three MIX 39, the mix", "DC02", dict(nofx, **VC.TYPICAL), VC.ROUTE_IN, mix, nb,
            "mix", tail=lambda b: tail_vals(S1.knobs17(S1.DEFAULTS), (39, 39, 39)))
        run("CMP3: three distinct bands, MIX 20 / 64 / 127, drums", "DC02", dict(nofx, **VC.comp(32, 24, 32, 64, 3, 64)),
            VC.ROUTE_IN, drums, nb, "mix", tail=lambda b: tail_vals(S1.knobs17(S1.DISTINCT), (20, 64, 127)))
        run("CMP3: every band knob and both crossovers moving every block, each MIX through 0, noise", "DC02",
            dict(nofx, **VC.comp(32, 24, 32, 64, 3, 64)), VC.ROUTE_IN, noise, nb, "mix",
            tail=lambda b: tail_vals(moving17(b), mixes_apart(b)))
        run("CMP3: heavy (20:1 fast, MUP 127 per band), key SCS 18", "DC02", dict(nofx, **VC.W3), VC.ROUTE_IN, drums, nb,
            "mix", tail=lambda b: tail_vals(S1.knobs17(([(0, 8, 0, 0, 127)] * 3, (68, 44))), (127, 127, 127)))
    if "F" in has:
        fx = dict(VC.BASE, **VC.FXW)
        run("flanger: the swelly default, the mix", "DC02", fx, VC.ROUTE_INFX, inputs("mix", nb, 0), nb, "chr")
        run("flanger: every knob jumping each 20 blocks, tempo changes, noise", "DC02", fx, VC.ROUTE_INFX,
            inputs("noise", nb, 11), nb, "chr", each=jumper(5, dly=False, rev=False))
        import fl02check as F2
        sync = F2.SYNC

        def synced(b, fr):
            VC.put(fr, "CHR_SPD", sync[9] if b < nb // 2 else sync[10])
            VC.put(fr, "TEMPO", int(round(120 * (120.0 if b < nb // 3 else 160.0))))
        run("flanger: SPD synced 1/8 then 1/16, tempo 120 -> 160", "DC02", fx, VC.ROUTE_INFX, inputs("mix", nb, 0), nb,
            "chr", each=synced)
    if "T" in has:
        x = cross("TE01")
        dl = dict(VC.BASE)
        dl.update(VS.WORDS)
        dl.update(IN_L=127, IN_R=127, IN_DEL_L=100, IN_DEL_R=100, DEL_VOL=100, CHR_DEL=x["CHR_DEL"])
        pl = inputs("pluck", nb, 0)
        run("tape echo: verify_se main configuration (X 6, FDBK 50, AGE 64, band, ALT), CHR_DEL %d" % x["CHR_DEL"],
            "TE01", dl, VC.ROUTE_INFX, pl, nb, "del", age=64)
        run("tape echo: held runaway X 6, FDBK 127, ALT 127, BW 127, AGE 0", "TE01",
            dict(dl, IN_DEL_L=127, IN_DEL_R=127, DEL_X=6, DEL_FDBK=127, DEL_WID=0, DEL_LPF=127, DEL_TIME=10), VC.ROUTE_INFX,
            inputs("sine", nb, 0), nb, "del")
        run("tape echo: every delay knob, AGE and the tempo jumping each 20 blocks (MODE 0..6), noise", "TE01", dl,
            VC.ROUTE_INFX, inputs("noise", nb, 13), nb, "del", each=jumper(6, chr_=False, rev=False), age=64)
    if "S" in has:
        x = cross("SP02")
        names = REV_WORDS
        rv = dict(VC.BASE, IN_REV_L=100, IN_REV_R=100, REV_VOL=110, IN_CHR_L=100, IN_CHR_R=100, IN_DEL_L=100,
                  IN_DEL_R=100, CHR_VOL=90, DEL_VOL=100, **x, **dict(zip(names, SPRING_INIT[:6])))
        if not x["DEL_REV"]:
            rv["CHR_DEL"] = 0
        run("spring: the init patch, the mix, cross-sends %s" % x, "SP02", rv, VC.ROUTE_INFX, inputs("mix", nb, 0), nb,
            "rev", k48=SPRING_INIT[6], prime=True)
        run("spring: every knob 127, drums", "SP02", dict(rv, **dict(zip(names, (127,) * 6))), VC.ROUTE_INFX,
            inputs("drums", nb, 0), nb, "rev", k48=127, prime=True)
        run("spring: every reverb knob and k 48 jumping each 7 blocks, tempo changes, noise", "SP02", rv, VC.ROUTE_INFX,
            inputs("noise", nb, 17), nb, "rev", each=jumper(7, every=7, chr_=False, dly=False), k48=96, prime=True)
    res["ok"] = all(v["ok"] for v in res.values() if isinstance(v, dict))
    res["comparable"] = ("CMP3: handler output, gain reductions, meter words with every FX slot silent (sends and VOLs "
                         "0; each slot's output checked 0 in both); the rest of the reply pages carries the FX slots' "
                         "audio streams, reported, not gated. FLNG: its slot output (input: the tracks' sends only). "
                         "T and S: their slot output, a cross-send into them on only from a slot that is the same "
                         "effect in both images. The handler output with our FX on is not comparable where another slot "
                         "differs, and is not compared.")
    return res


# ---------------------------------------------------------------- (mix)

ALLFX = dict(IN_CHR_L=100, IN_CHR_R=100, IN_DEL_L=100, IN_DEL_R=100, IN_REV_L=60, IN_REV_R=60, CHR_DEL=40, CHR_REV=30,
             CHR_VOL=90, DEL_REV=40, DEL_VOL=100, REV_VOL=90)


def mix_cases(q):
    S1, VC = G["S1"], G["VC"]
    import verify_flg as FL
    nb = 2000 if not q else 200
    base = dict(VC.BASE, **ALLFX, **FL.KNOBS, REV_DEC=90)
    return nb, {
        "typical: the mix, echo X 6 FDBK 64 AGE 32 band ALT, spring init patch, flanger default, bands MIX 39":
            (dict(base, **VC.TYPICAL, DEL_TIME=23, DEL_X=6, DEL_WID=32, DEL_FDBK=64, DEL_HPF=64, DEL_LPF=64,
                  **dict(zip(REV_WORDS, SPRING_INIT[:6]))), 32, ("mix", 0),
             lambda b: tail_vals(S1.knobs17(S1.DEFAULTS), (39, 39, 39)), None),
        "worst: noise, every band knob and MIX moving, every FX knob, AGE, k 48 and the tempo jumping each 20 blocks, "
        "key SCS 18":
            (dict(base, **VC.W3, DEL_FDBK=100, DEL_X=6), 64, ("noise", 9), lambda b: tail_vals(moving17(b), mixes_apart(b)),
             jumper(19)),
    }


def check_mix(name, q):
    VC = G["VC"]
    info = image(name)[1]
    has = set(ch for ch in letters(name) if ch != "E")
    nb, cases = mix_cases(q)
    res = {}
    for cname, (words, age, (kind, seed), tail, each) in cases.items():
        t0 = time.time()
        run = Run(name, words, VC.ROUTE_INFX, profile=name in PACKS)
        struct.pack_into("<H", run.fr, AGE_W, age << 8)
        struct.pack_into("<H", run.fr, K48, 96 << 8)
        run.prime()
        inp = inputs(kind, nb, seed)
        e = {"blocks": nb, "out_peak": 0.0, "out_nan_inf": 0, "del_peak": 0.0, "del_nan_inf": 0, "rev_peak": 0.0,
             "rev_nan_inf": 0, "spg_magic_bad_blocks": 0}
        for b in range(nb):
            if each:
                each(b, run.fr)
            c = run.block(inp[0][32 * b:32 * b + 32], inp[1][32 * b:32 * b + 32], b, tail=tail(b), paint=(b % 50 == 7))
            for k, buf in (("out", c["out"]), ("del", c["del"]), ("rev", c["rev"])):
                p, bad = peak(struct.unpack("<64f", buf))
                e[k + "_peak"] = max(e[k + "_peak"], p)
                e[k + "_nan_inf"] += bad
            if "S" in has and run.vm.rd32(SPG) != SPG_MAGIC:
                e["spg_magic_bad_blocks"] += 1
        pb = run.per_block[1:]
        e.update(insns_mean=round(sum(pb) / len(pb)), insns_max=max(pb), stack_bytes=run.stack,
                 est_load_max_pct=round(100 * VC.LOAD(max(pb)), 1))
        ok = e["out_nan_inf"] == 0 and e["out_peak"] <= 0.6931 and run.stack < 3000
        if "T" in has:
            tp, tbad = peak(run.vm.rdf(SE_RING_ADDR, 2 * SE_RING))
            e.update(tape_peak=tp, tape_nan_inf=tbad, se_magic_ok=run.vm.rd32(SE_SLOT) == SE_MAGIC)
            ok = ok and e["del_nan_inf"] == 0 and e["del_peak"] <= 0.7000001 and tp <= 1.0 + 1e-6 and tbad == 0 \
                and e["se_magic_ok"]
        if "S" in has:
            ok = ok and e["rev_nan_inf"] == 0 and e["rev_peak"] <= SPRING_MAX and e["spg_magic_bad_blocks"] == 0
        if name in PACKS:
            e.update(placement(run.vm, info))
            ok = ok and not e["executed_in_regions_outside_placed_functions"]
        e["ok"] = ok
        e["seconds"] = round(time.time() - t0)
        res[cname] = e
        print("[%s mix] %-50s %s mean %d max %d, out %.4f del %.4f rev %.4f, %ds" % (
            name, cname[:50], "ok" if ok else "FAIL", e["insns_mean"], e["insns_max"], e["out_peak"], e["del_peak"],
            e["rev_peak"], e["seconds"]), flush=True)
        del run
        gc.collect()
        G["memguard"].check(3072)
    res["ok"] = all(v["ok"] for v in res.values() if isinstance(v, dict))
    return res


def placement(vm, info):
    placed = [(int(f["core"], 16) // 2, (int(f["core"], 16) + f["bytes"]) // 2) for f in info["function_table"]]
    regions = [(lo // 2, hi // 2) for lo, hi, _ in PROVEN]
    ran, outside = 0, []
    for pc in vm.decoded:
        if not any(lo <= pc < hi for lo, hi in regions):
            continue
        pg = vm.pages.get(pc >> 12)
        h = pg[pc & 0xfff].hits if pg is not None else 0
        if not h:
            continue
        ran += h
        if not any(lo <= pc < hi for lo, hi in placed):
            outside.append("%#x" % pc)
    return {"region_instructions_executed": ran, "executed_in_regions_outside_placed_functions": outside[:10]}


# ---------------------------------------------------------------- (stores)

def ours(info):
    """the pack's own sections (code and constant data) and the forbidden range; the proven regions' unused room"""
    r = []
    for p in info["placed"]:
        lo = int(p["core"], 16)
        r.append((lo, lo + p["bytes"], p["section"]))
    r.append((FORBID[0], FORBID[1], "0x315f8c-0x318000"))
    spare = []
    for lo, hi, nm in PROVEN:
        used = [(a, b) for a, b, _ in r if lo <= a < hi]
        end = max([b for _, b in used], default=lo)
        if end < hi:
            spare.append((end, hi, "unused room of %s" % nm))
    return r, spare


def check_stores(pack, q):
    VC, S1, V = G["VC"], G["S1"], G["V"]
    info = image(pack)[1]
    has = set(ch for ch in pack if ch != "E")
    from vm import watch as W, watch_log
    stack = (V.STACK_LO, V.STACK_TOP, "stack")
    save = (SAVE[0], SAVE[1], "stub save area")
    sb = info["layout_check"]["slot_sizes_from_image"]
    allowed = {"chr": [stack, save, (0x300000, 0x308400, "FlgSlot"), (V.CHR_OUT[0], V.CHR_OUT[0] + 0x80, "out L"),
                       (V.CHR_OUT[1], V.CHR_OUT[1] + 0x80, "out R")],
               "del": [stack, save, (SE_SLOT, SE_SLOT + SE_BYTES, "SeSlot"), (V.DEL_OUT[0], V.DEL_OUT[0] + 0x80, "out L"),
                       (V.DEL_OUT[1], V.DEL_OUT[1] + 0x80, "out R")],
               "rev": [stack, save, (SPG, SPG + SPG_BYTES, "SpgSlot"), (REV_OUT[0], REV_OUT[0] + 0x80, "out L"),
                       (REV_OUT[1], REV_OUT[1] + 0x80, "out R")]}
    if "C" in has:
        allowed["comp"] = [stack, save, (S1.SLOT, S1.SLOT + sb["cmp_slot_bytes"], "CmpSlot"),
                           (S1.TST, S1.TST + sb["cmp_tail_bytes"], "CmpTail"), (0x25ba68, 0x25ba68 + 0x80, "out L"),
                           (0x25bae8, 0x25bae8 + 0x80, "out R"), (VC.GR_Y2, VC.GR_Y2 + 4, "0x257c1c"),
                           (VC.TX[0] + 0x10, VC.TX[0] + 0x18, "reply page 0 meters"),
                           (VC.TX[1] + 0x10, VC.TX[1] + 0x18, "reply page 1 meters")]
    calls = {"C": "comp", "F": "chr", "T": "del", "S": "rev"}
    words = dict(VC.BASE, **ALLFX, **VC.comp(32, 24, 32, 64, 3, 64), DEL_X=6, DEL_FDBK=100)
    nbk = 80 if not q else 20
    inp = inputs("mix", nbk, 0)
    tail = lambda b: tail_vals(moving17(b), mixes_apart(b))   # noqa: E731
    sec, spare = ours(info)
    res = {"calls": {}, "whole_blocks": nbk, "stores_into": {}, "stores_into_unused_room": {}}
    for ch in sorted(has):
        which = calls[ch]
        run = Run(pack, words, VC.ROUTE_INFX)
        run.prime()
        each = jumper(3, every=5)
        log = []
        for b in range(nbk):
            each(b, run.fr)
            c = run.block(inp[0][32 * b:32 * b + 32], inp[1][32 * b:32 * b + 32], b, tail=tail(b),
                          watch_call=which if b % 4 == 1 or b < 4 else None)
            log += c["log"]
        al = allowed[which]
        stray = [(pc, ad) for pc, ad, va in log if not any(lo <= ad < hi for lo, hi, _ in al)]
        by = {}
        for pc, ad, va in log:
            nm = next((n_ for lo, hi, n_ in al if lo <= ad < hi), "OTHER")
            by[nm] = by.get(nm, 0) + 1
        res["calls"][which] = {"stores": len(log), "by_range": by, "outside_count": len(stray),
                               "outside": ["%#x@%#x" % (ad, pc) for pc, ad in stray[:10]]}
        del run
        gc.collect()
    for group, rngs in (("stores_into", sec), ("stores_into_unused_room", spare)):
        for lo, hi, nm in rngs:
            run = Run(pack, words, VC.ROUTE_INFX)
            run.prime()
            each = jumper(3, every=5)
            W(run.vm, lo, hi)
            n = 0
            for b in range(nbk // 2):
                each(b, run.fr)
                run.block(inp[0][32 * b:32 * b + 32], inp[1][32 * b:32 * b + 32], b, tail=tail(b))
                n += len(watch_log(run.vm))
                W(run.vm, lo, hi)
            res[group]["%s %#x-%#x" % (nm, lo, hi)] = n
            del run
            gc.collect()
    res["ok"] = (all(v["outside_count"] == 0 and v["stores"] > 0 for v in res["calls"].values())
                 and all(v == 0 for v in res["stores_into"].values()))
    print("[%s stores] %s calls %s, into ours %s, unused room %s" % (
        pack, "ok" if res["ok"] else "FAIL", {k: (v["stores"], v["outside_count"]) for k, v in res["calls"].items()},
        res["stores_into"], res["stores_into_unused_room"]), flush=True)
    return res


# ---------------------------------------------------------------- (soak)

def check_soak(pack, seconds, q):
    """soak_te01's per-block checks on PACK with soak_sc01's "human" hands (and the spring's seven knobs walked)"""
    import soak_te01 as ST
    VC, V, FB = G["VC"], G["V"], G["FB"]
    from vm import VMError, watch as W, watch_log
    if not ST.G:
        ST.setup(G["S1"].OFF.copy())        # its modules and Control's globals (TE01's image is not run)
    info = image(pack)[1]
    has = set(ch for ch in pack if ch != "E")
    BPS = ST.BPS
    nb = int(seconds * BPS) if not q else 600
    cfg = dict(ST.recipes(1.0)["R4a"], seconds=seconds, seed=900 + PACKS.index(pack))
    ctl = ST.Control(cfg)
    r = random.Random(cfg["seed"] + 3)
    rev_h = [ST.Hand(r, v, 0, 127, "walk", period=(0.5, 6), jump=0.15) for v in SPRING_INIT]
    rev_s = [256.0 * h.v for h in rev_h]
    vm = new_vm(pack)
    vm.lib.vm_set_profile(vm.h, 1)
    d = FB.FullDSP(vm=vm)
    d.set(**ctl.words)
    d.set_raw(**VC.ROUTE_INFX)
    fr = d.frame
    src = ST.Source("mixed", random.Random(cfg["seed"] + 7))
    sec, spare = ours(info)
    watch = sec
    probes_d = {}

    def at_del():
        probes_d["del"] = vm.rdf(V.DEL_OUT[0], 32) + vm.rdf(V.DEL_OUT[1], 32)

    def at_rev():
        probes_d["rev"] = vm.rdf(REV_OUT[0], 32) + vm.rdf(REV_OUT[1], 32)
    probes = [(V.DEL_RET, at_del), (REV_RET, at_rev)]
    res = {"pack": pack, "seconds_audio": seconds, "blocks_planned": nb, "blocks_run": 0, "faults": [], "anomalies": {},
           "insns_max": 0, "insns_sum": 0, "stack_high_water": 0, "peaks": {"out": 0.0, "del": 0.0, "rev": 0.0, "tape": 0.0}}
    anom = res["anomalies"]

    def note(kind, b, detail):
        e = anom.setdefault(kind, {"count": 0, "first": []})
        e["count"] += 1
        if len(e["first"]) < 8:
            e["first"].append(dict(block=b, **detail))
    paint_word = V.PAINT.to_bytes(4, "little")
    base_depth = None
    blk_prev = None
    t0 = time.time()
    b = 0
    try:
        for b in range(nb):
            ctl.step(b, fr)
            for j, (h, k) in enumerate(zip(rev_h, REV_WORDS + ("K48",))):
                t = 256.0 * h.step(b)
                s = rev_s[j] + ST.GLIDE * (t - rev_s[j])
                if abs(s - t) < 0.5:
                    s = t
                rev_s[j] = s
                struct.pack_into("<H", fr, K48 if k == "K48" else FB.FRAME_WORDS[k], int(round(s)) & 0xffff)
            L, R = src.block(b)
            vm.wr32(FB.SPI_HALF, b & 1)
            vm.write(FB.RX_BASE + 0x1000 * (b & 1), bytes(fr))
            h_ = vm.rd32(FB.DMA_HALF)
            vm.write(FB.CODEC_IN + 0x100 * h_, struct.pack("<64I", *[FB.q31(v) for pair in zip(L, R) for v in pair]))
            paint = b % 100 == 37
            if paint:
                i7 = vm.reg("I7")
                vm.write(V.STACK_LO, paint_word * ((i7 - V.STACK_LO) // 4))
            wlo, whi, wname = watch[b % len(watch)]
            W(vm, wlo, whi)
            ST.G["V"] = V
            try:
                n = ST.probed(vm, probes, ST.CAP)
            except VMError as e:
                raise ST.Fault(json.dumps({"kind": "trap", "message": str(e), "pc": "%#x" % vm.pc}))
            wl = watch_log(vm)
            W(vm, 0, 0)
            if wl:
                note("store_into_ours", b, {"range": wname, "stores": ["%#x@pc %#x" % (ad, pc) for pc, ad, va in wl[:6]]})
            if paint:
                i7 = vm.reg("I7")
                raw = vm.read(V.STACK_LO, i7 - V.STACK_LO)
                low = next((V.STACK_LO + k for k in range(0, len(raw), 4) if raw[k:k + 4] != paint_word), i7)
                res["stack_high_water"] = max(res["stack_high_water"], i7 - low)
            res["insns_max"] = max(res["insns_max"], n)
            res["insns_sum"] += n
            dep = vm.depths()
            if base_depth is None:
                base_depth = dep
            elif dep != base_depth:
                note("hw_stack_depth", b, {"depths": dep})
                base_depth = dep
            p, bad = peak(vm.rdf(FB.OUT_FLOATS, 64))
            res["peaks"]["out"] = max(res["peaks"]["out"], p)
            if bad:
                note("output_nan_inf", b, {})
            if p > 0.6931:
                note("output_over_ceiling", b, {"peak": p})
            if "T" in has:
                p, bad = peak(probes_d.get("del", ()))
                res["peaks"]["del"] = max(res["peaks"]["del"], p)
                if bad or p > 0.7000001:
                    note("echo_wet", b, {"peak": p, "nan_inf": bad})
                mg, _, blk = struct.unpack("<3I", vm.read(SE_SLOT, 12))
                if mg != SE_MAGIC:
                    note("seslot_magic", b, {"magic": "%#x" % mg})
                if blk_prev is not None and blk != (blk_prev + 1) & 0xffffffff:
                    note("seslot_block_counter", b, {"was": blk_prev, "now": blk})
                blk_prev = blk
                w = struct.unpack("<i", vm.read(SE_SLOT + 48, 4))[0]
                if not 0 <= w < SE_RING:
                    note("echo_index", b, {"w": w})
                else:
                    a0 = (w - 32) % SE_RING
                    if a0 + 32 <= SE_RING:
                        tp = vm.rdf(SE_RING_ADDR + 8 * a0, 64)
                    else:
                        k1 = SE_RING - a0
                        tp = vm.rdf(SE_RING_ADDR + 8 * a0, 2 * k1) + vm.rdf(SE_RING_ADDR, 2 * (32 - k1))
                    p, bad = peak(tp)
                    res["peaks"]["tape"] = max(res["peaks"]["tape"], p)
                    if bad or p > 1.0 + 1e-6:
                        note("tape_write", b, {"peak": p, "nan_inf": bad})
            if "S" in has:
                p, bad = peak(probes_d.get("rev", ()))
                res["peaks"]["rev"] = max(res["peaks"]["rev"], p)
                if bad or p > SPRING_MAX:
                    note("spring_out", b, {"peak": p, "nan_inf": bad})
                if vm.rd32(SPG) != SPG_MAGIC:
                    note("spgslot_magic", b, {"magic": "%#x" % vm.rd32(SPG)})
            if b % BPS == BPS - 1:
                G["memguard"].check(3072)
            res["blocks_run"] = b + 1
    except ST.Fault as f:
        res["faults"].append(dict(json.loads(str(f)), block=b))
    except Exception as e:
        res["faults"].append({"kind": "error", "message": repr(e)[:400], "block": b, "pc": "%#x" % vm.pc})
    res.update(placement(vm, info))
    res["insns_mean"] = round(res.pop("insns_sum") / max(1, res["blocks_run"]))
    res["peaks"] = {k: round(v, 5) for k, v in res["peaks"].items()}
    res["wall_s"] = round(time.time() - t0)
    res["peak_rss_mb"] = round(G["memguard"].peak_rss_mb())
    res["ok"] = not res["faults"] and not anom and not res["executed_in_regions_outside_placed_functions"] \
        and res["blocks_run"] == nb
    print("[%s soak] %s %d blocks (%.0f s), insns mean %d max %d, peaks %s, stack %d, faults %s, anomalies %s, %ds, rss %d MB"
          % (pack, "ok" if res["ok"] else "FAIL", res["blocks_run"], res["blocks_run"] / BPS, res["insns_mean"],
             res["insns_max"], res["peaks"], res["stack_high_water"], res["faults"][:1], sorted(anom), res["wall_s"],
             res["peak_rss_mb"]), flush=True)
    del vm, d
    gc.collect()
    return res


# ---------------------------------------------------------------- the pool

def job(args):
    check, name, off, q, soak_s = args
    setup(off)
    t0 = time.time()
    if check == "lock":
        r = check_lock(name, q)
    elif check == "mix":
        r = check_mix(name, q)
    elif check == "stores":
        r = check_stores(name, q)
    else:
        r = check_soak(name, soak_s, q)
    r["seconds"] = round(time.time() - t0)
    r["peak_rss_mb"] = round(G["memguard"].peak_rss_mb())
    (OUT / ("%s-%s%s.json" % (name, check, "-quick" if q else ""))).write_text(json.dumps(r, indent=1))
    G["memguard"].check(3072)
    return check, name, r["ok"], r["seconds"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="lock,mix,stores,soak")
    ap.add_argument("--packs", default=",".join(PACKS))
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--soak-seconds", type=float, default=60.0)
    ap.add_argument("--quick", action="store_true")
    a = ap.parse_args()
    if a.workers > 3:
        raise SystemExit("at most 3 workers (the Mac froze once from sharcvm workers)")
    OUT.mkdir(parents=True, exist_ok=True)
    import verify_sc01 as S1
    import memguard
    import build as BD
    packs = a.packs.split(",")
    checks = a.only.split(",")
    names = packs + ["DC02", "TE01", "SP02"]
    srcs = {}
    for n in names:
        st, info, cache = S1.load_image(ROOT / f"out/{n}/{n}-dsp", n)
        stale = [s for s, h in info["sources"].items() if BD.sha((ROOT / s).read_bytes()) != h]
        if stale and n in packs:
            raise SystemExit(f"{n} was built from other sources ({stale})")
        srcs[n] = {s: h for s, h in info["sources"].items()}
        S1.V.candidate_vm(st, cache=cache)              # the snapshot, made once here, not by racing workers
        gc.collect()
    if BD.sha((ROOT / "out/TE01/soak/src/spaceecho.h").read_bytes()) != srcs["TE01"]["fx/spaceecho.h"] or any(
            srcs[p].get("fx/spaceecho.h", srcs["TE01"]["fx/spaceecho.h"]) != srcs["TE01"]["fx/spaceecho.h"] for p in packs):
        raise SystemExit("the packs' fx/spaceecho.h is not TE01's (the SpaceEcho offsets used here)")
    diff = {}
    for p in packs:
        for ref in ("DC02", "TE01", "SP02"):
            d = [s for s in srcs[p] if s in srcs[ref] and srcs[p][s] != srcs[ref][s]]
            if d:
                diff[f"{p}/{ref}"] = d
    off = S1.VC.offsets(OUT / "offsets", "CMP3")
    jobs = []
    if "soak" in checks:
        jobs += [("soak", p, off, a.quick, a.soak_seconds) for p in packs]
    for c in ("lock", "mix", "stores"):
        if c in checks:
            jobs += [(c, p, off, a.quick, a.soak_seconds) for p in packs]
    if "mix" in checks:
        jobs += [("mix", "DC02", off, a.quick, 0), ("mix", "EEEE", off, a.quick, 0)]
    print(f"{len(jobs)} jobs on {a.workers} workers; sources that differ from a reference image: {diff or 'none'}",
          flush=True)
    t0 = time.time()
    done = {}
    with ProcessPoolExecutor(a.workers, max_tasks_per_child=1) as ex:
        for check, name, ok, secs in memguard.pmap(ex, job, jobs):
            done.setdefault(name, {})[check] = ok
            print(time.strftime("%H:%M:%S"), "DONE", check, name, "ok" if ok else "FAIL", secs, "s", flush=True)
    summ = {"packs": done, "sources_that_differ_from_the_reference": diff, "wall_s": round(time.time() - t0),
            "ok": all(all(v.values()) for v in done.values())}
    (OUT / ("summary%s.json" % ("-quick" if a.quick else ""))).write_text(json.dumps(summ, indent=1))
    print("VERDICT", "PASS" if summ["ok"] else "FAIL", summ["wall_s"], "s", flush=True)
    return 0 if summ["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
