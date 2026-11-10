#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""MR03 (2026-11-10; notes/MR03-eeem.md): the DSP proofs of EEEM = Elektron's DSP with MRB (fx/mrb.c, mrb_glue.c), the
morphing reverb, in the reverb slot, and the fault tones (faulttone.s). verify_sp02.py's checks for MRB, against EEEE
(Elektron's DSP: the stock section 7) and EEES. Whole images in sharcvm (vmrun.py: the boot stream over the stock
baseline, the effect init 0x1c1445, the block handler 0x1c9d6b with a ColdFire frame and codec input).

    python3 tools/dspsplice/verify_eeem.py [--only static,moving,lock,exec,stores,fault,silence,kill] [--workers 3]

(static) MRB = the Mac build of fx/mrb.c (clang -O2 -ffp-contract=off): EEEM's whole image on static reverb settings,
         the external input sent to the reverb with the cross-sends on; the reverb's input captured at its call every
         block and rendered by the Mac build through MRB's own API (mrb_init, mrb_set of the six knob integers, mrb_snap,
         mrb_process), not through the glue: its output = the reverb's output in the image, every sample bit for bit,
         and the Mrb state (MrbSlot + 32) byte for byte at the end.
(moving) the glue and MRB = their Mac build (-DMRB_NATIVE: mrb_glue.c and fx/mrb.c) with the knobs moving (every knob
         ramping every block; CHAR swept 0 -> 127 -> 0 and jumping; random jumps of all six every 7 blocks; the soak's
         hands): the parameter struct and the input captured at every call fed to the Mac build's mrb_glue_process:
         every output sample bit for bit and the whole MrbSlot byte for byte at the end.
(lock)   EEEE and EEEM in lockstep on the same frames and input (soak_cmp4's Control R1: the reverb's knobs moving):
         (b1) the reverb's VOL 0: the whole mix (handler output), the chorus's and the delay's outputs, both reply pages
         (word 0, the cycle count, and the reverb's stream 11 aside) every block bit for bit, at the end L1 word for word
         outside our code, the stack, the hook and park sites, and DDR outside the reverb's region; (b2) VOL up: the
         chorus's and the delay's outputs bit for bit. Instructions per block (EEEE, EEES, EEEM) on static and ramping
         cost cases: the worst block of each.
(exec)   the stock reverb's own instructions never run in EEEM (EEEE's reverb call and init profiled for them); the
         reverb call and init run only our code; the fault-tone code never runs in normal blocks.
(stores) the reverb call in EEEM stores only into MrbSlot, the stack, the stub save area and its outputs; the effect
         init's stores into the reverb's region all come from our code.
(fault)  each fault path driven from the stock handler's entry (EMUI 0xb8b546, ILOPI 0xb8b560, PARI 0xb8b57c, the
         default handler 0xb8b9f2, the abort path 0xb8b538 and 0x1c1430, and 0x1c07c6 with an unknown code) on EEEM's
         image after the init and some blocks: the core ends in our park (IDLE at ft's loop), the record as the handler
         wrote it, both codec output buffers (0x2c0478, 0x2c0578, 128 words) the expected square wave, nothing else in
         memory changed but the record, the buffers and the handler's own stores.
(silence) 15 s of the soak's input with every knob walking (the reverb's six too), then 180 s of silence with every
         knob held: no fault, no runaway, every output finite, MrbSlot's magic, no non-finite word in its state.
(kill)   15 s as above, then the FX-kill state for 60 s (input 0, REV_DEC 0, DEL_FDBK and DEL_VOL 0, REV_VOL x127/128 a
         frame, as the OS UPGRADE menu and a double STOP; stock-flow.md 2): as (silence).
Results: out/EEEM/verify/<check>.json, out/EEEM/verify/summary.json.
"""
import argparse
import ctypes as C
import gc
import json
import math
import random
import struct
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "tools/sharcvm"))
sys.path.insert(0, str(ROOT / "tools/sharcvm/tests"))
import vmrun as VR  # noqa: E402
import fullblock as FB  # noqa: E402
import memguard  # noqa: E402

OUT = ROOT / "out/EEEM/verify"
DIRS = {"EEEM": ROOT / "out/EEEM/EEEM-dsp", "EEES": ROOT / "out/EEES/EEES-dsp", "EEEE": None}
STOCK7 = ROOT / "out/T3.8/stock/section_7_blob.bin"
REV_CALL, REV_RET = 0x1c1d11, 0x1c1d18
REV_IN, REV_OUT = (0x25b268, 0x25b2e8), (0x25b368, 0x25b3e8)
REV_P = 0x257918
FXINIT_REV_CALL, FXINIT_REV_RET = 0x1c1643, 0x1c164a
SLOT = 0x80000018
MB_OFF = 32                                   # MrbSlot: magic, fresh, last[6], then Mrb
MAGIC = 0x4d524231
REV_REGION = (0x80000018, 0x8005912c)
CONST = (0x80058000, 0x80059000)
SAVE = (0x2e6800, 0x2e686c)
K48 = 0xa28
ST_ROW0, ST_STRIDE, ST_REV = 0x1c, 0x54, 66
NAMES = ("PRE", "DEC", "CHAR", "SIZE", "HPF", "TONE")
WORDS_REV = ("REV_PRE", "REV_DEC", "REV_FREQ", "REV_GAIN", "REV_HPF", "REV_LPF")
INIT = dict(zip(NAMES, (0, 64, 64, 64, 0, 70)))       # morph-names' new kit
# the fault tones (faulttone.s)
BUF0, BUF_WORDS = 0x2c0478, 128
REC = 0x26ef88
H = 0x6666666
PARKS_SW = [(0x1c07c7, "ft_fault"), (0x1c1431, "ft_abort")]
G = {}


def load(name):
    if name == "EEEE":
        stream = STOCK7.read_bytes()
        import build as BD
        if BD.sha(stream) != BD.STOCK7_SHA:
            raise SystemExit("stock section 7 hash")
        return stream, None, "eeemv-EEEE-" + BD.STOCK7_SHA[:12]
    d = DIRS[name]
    info = json.loads((d / "build.json").read_text())
    stream = (d / "section7.bin").read_bytes()
    import build as BD
    if BD.sha(stream) != info["section7"]["sha256"]:
        raise SystemExit(f"{d}: section7.bin is not the image build.json describes")
    return stream, info, f"eeemv-{name}-" + info["section7"]["sha256"][:12]


def setup():
    if G:
        return
    import soak_cmp4 as SC
    SC.setup(SC.cmp_offsets(), DIRS["EEEM"])
    G.update(SC=SC, S=SC.G["S"], VC=SC.G["VC"], V2=SC.G["V2"])


def fbits(xs):
    return struct.pack("<%df" % len(xs), *xs)


def base_words(rev, rev_vol=110):
    """the external input into all three effects, the cross-sends on; the reverb's knobs from rev"""
    setup()
    VC = G["VC"]
    w = dict(VC.BASE, IN_CHR_L=100, IN_CHR_R=100, IN_DEL_L=100, IN_DEL_R=100, IN_REV_L=100, IN_REV_R=100, CHR_DEL=40,
             CHR_REV=80, CHR_VOL=90, DEL_REV=90, DEL_VOL=100, REV_VOL=rev_vol, DEL_TIME=23, DEL_FDBK=60,
             **VC.comp(32, 24, 32, 64, 3, 64))
    for k, n in zip(WORDS_REV, NAMES):
        w[k] = rev[n]
    return w


class Run:
    """one image in sharcvm with the reverb's call probed (parameters, input, output, instructions), the chorus and
    delay outputs, the handler output and instructions, every block"""

    def __init__(self, name, words, tempo=120.0, k48=1):
        stream, info, cache = load(name)
        self.name, self.info = name, info
        self.vm = VR.candidate_vm(stream, cache=cache)
        self.r = VR.Runner(self.vm, words, tempo=tempo)
        self.r.d.set_raw(**G["VC"].ROUTE_INFX)
        struct.pack_into("<H", self.r.d.frame, K48, k48 << 8)
        self.tail = [int(256 * v) & 0xffff for v in G["S"].knobs17(G["S"].START)] + [64 << 8] * 3 + [0] * 7
        self.profile = None

    def prime_image(self):
        self.vm.write(0x25c48c, bytes(self.r.d.frame[:0xa80]))

    def block(self, L, R, b, watch=None):
        vm, d = self.vm, self.r.d
        struct.pack_into("<30H", d.frame, G["V2"].TAIL, *G["V2"].tail_words(1 + b, self.tail))
        spi = vm.rd32(FB.SPI_HALF)
        vm.write(FB.RX_BASE + 0x1000 * spi, bytes(d.frame))
        h = vm.rd32(FB.DMA_HALF)
        vm.write(FB.CODEC_IN + 0x100 * h, b"".join(FB.q31(v).to_bytes(4, "little") for pair in zip(L, R) for v in pair))
        c = {}

        def chr_ret():
            c["chr"] = vm.read(VR.CHR_OUT[0], 0x80) + vm.read(VR.CHR_OUT[1], 0x80)

        def del_ret():
            c["del"] = vm.read(VR.DEL_OUT[0], 0x80) + vm.read(VR.DEL_OUT[1], 0x80)

        def rev_call():
            c["p"] = vm.read(REV_P, 24)
            c["in"] = vm.read(REV_IN[0], 0x80) + vm.read(REV_IN[1], 0x80)
            c["n0"] = vm.icount
            if self.profile:
                vm.lib.vm_set_profile(vm.h, 1 if self.profile == "rev" else 0)
            if watch is not None:
                from vm import watch as W
                W(vm, 0, 0xffffffff)

        def rev_ret():
            c["rev_n"] = vm.icount - c["n0"]
            c["out"] = vm.read(REV_OUT[0], 0x80) + vm.read(REV_OUT[1], 0x80)
            if self.profile:
                vm.lib.vm_set_profile(vm.h, 0 if self.profile == "rev" else 1)
            if watch is not None:
                from vm import watch as W, watch_log
                watch.extend(watch_log(vm))
                W(vm, 0, 0)
        n0 = vm.icount
        self.r._call_probed([(VR.CHR_RET, chr_ret), (VR.DEL_RET, del_ret), (REV_CALL, rev_call), (REV_RET, rev_ret)])
        c["n"] = vm.icount - n0
        c["mix"] = vm.read(FB.OUT_FLOATS, 0x100)
        c["tx"] = b"".join(vm.read(t, 0x1000) for t in G["VC"].TX)
        return c


def source(kind, nb, seed):
    """the external input: drums and a mix (out/T3.11/dry), with noise bursts"""
    setup()
    VC = G["VC"]
    r = random.Random(seed)
    dL, dR = VC.load_input("drums", 3.0)
    mL, mR = VC.load_input("mix", 3.0)
    L, R = dL + mL, dR + mR
    out, noise, nxt = [], False, 0
    for b in range(nb):
        i = (32 * b) % len(L)
        if kind == "mixed":
            if b >= nxt:
                nxt = b + int(1500 * r.uniform(1, 4))
                noise = r.random() < 0.3
            if noise:
                out.append(([r.uniform(-0.7, 0.7) for _ in range(32)], [r.uniform(-0.7, 0.7) for _ in range(32)]))
                continue
        out.append((L[i:i + 32], R[i:i + 32]))
    return out


# ---------------------------------------------------------------- the Mac build

class FxIo(C.Structure):
    _fields_ = [("n", C.c_int32), ("inp", C.POINTER(C.POINTER(C.c_float))), ("r08", C.c_int32),
                ("out", C.POINTER(C.POINTER(C.c_float))), ("r10", C.c_int32), ("bpm", C.c_float)]


def native_lib():
    OUT.mkdir(parents=True, exist_ok=True)
    lib = OUT / "libmrbnative.dylib"
    subprocess.check_call(["clang", "-O2", "-ffp-contract=off", "-fno-strict-aliasing", "-shared", "-fPIC",
                           "-DMRB_NATIVE", f"-I{ROOT / 'fx'}", f"-I{ROOT / 'tools/dspsplice/src'}", "-o", str(lib),
                           str(ROOT / "tools/dspsplice/src/mrb_glue.c"), str(ROOT / "fx/mrb.c")])
    return lib


def open_lib():
    lib = C.CDLL(str(OUT / "libmrbnative.dylib"))
    lib.mrb_native_slot_ptr.restype = C.c_void_p
    lib.mrb_slot_size.restype = C.c_uint32
    lib.mrb_set.argtypes = [C.c_void_p, C.c_int, C.c_float]
    for f in ("mrb_init", "mrb_snap"):
        getattr(lib, f).argtypes = [C.c_void_p]
    lib.mrb_process.argtypes = [C.c_void_p] + [C.POINTER(C.c_float)] * 4 + [C.c_int]
    return lib


def bufs():
    bl = [(C.c_float * 32)() for _ in range(4)]
    inp = (C.POINTER(C.c_float) * 2)(C.cast(bl[0], C.POINTER(C.c_float)), C.cast(bl[1], C.POINTER(C.c_float)))
    out = (C.POINTER(C.c_float) * 2)(C.cast(bl[2], C.POINTER(C.c_float)), C.cast(bl[3], C.POINTER(C.c_float)))
    io = FxIo(32, C.cast(inp, C.POINTER(C.POINTER(C.c_float))), 0, C.cast(out, C.POINTER(C.POINTER(C.c_float))), 0,
              120.0)
    return bl, io


def same_count(a, b):
    return len(a) // 4 if a == b else sum(1 for i in range(0, len(a), 4) if a[i:i + 4] == b[i:i + 4])


# ---------------------------------------------------------------- (static)

STATIC = {
    "the new kit (PRE 0 DEC 64 CHAR 64 SIZE 64 HPF 0 TONE 70)": dict(INIT),
    "an old kit at the stock defaults (16 31 64 50 32 96)": dict(zip(NAMES, (16, 31, 64, 50, 32, 96))),
    "CHAR 0 (room)": dict(INIT, CHAR=0),
    "CHAR 32": dict(INIT, CHAR=32),
    "CHAR 96": dict(INIT, CHAR=96),
    "CHAR 127 (spring)": dict(INIT, CHAR=127),
    "every knob 0": dict(zip(NAMES, (0,) * 6)),
    "every knob 127": dict(zip(NAMES, (127,) * 6)),
    "heavy: DEC 127 SIZE 127 CHAR 127 PRE 40": dict(INIT, DEC=127, SIZE=127, CHAR=127, PRE=40),
    "dark and thin: TONE 0 HPF 127 SIZE 0": dict(INIT, TONE=0, HPF=127, SIZE=0),
}


def job_static(args):
    case, knobs, nb = args
    setup()
    lib = open_lib()
    run = Run("EEEM", base_words(knobs))
    run.prime_image()
    src = source("mixed", nb, 11)
    nmb = lib.mrb_slot_size() - MB_OFF
    mb = (C.c_uint8 * (nmb + 64))()
    mbp = C.c_void_p((C.addressof(mb) + 15) // 16 * 16)
    lib.mrb_init(mbp)
    for i, n in enumerate(NAMES):
        lib.mrb_set(mbp, i, float(knobs[n]))
    lib.mrb_snap(mbp)
    bl, io = bufs()
    same = samples = 0
    first, pk, rn = None, 0.0, []
    for b in range(nb):
        c = run.block(*src[b], b)
        C.memmove(bl[0], c["in"][:0x80], 0x80)
        C.memmove(bl[1], c["in"][0x80:], 0x80)
        lib.mrb_process(mbp, bl[0], bl[1], bl[2], bl[3], 32)
        mine = bytes(bl[2]) + bytes(bl[3])
        samples += 64
        k = same_count(mine, c["out"])
        same += k
        if k != 64 and first is None:
            first = b
        pk = max(pk, max(abs(v) for v in struct.unpack("<64f", c["out"])))
        rn.append(c["rev_n"])
    st_dsp = run.vm.read(SLOT + MB_OFF, nmb)
    st_mac = C.string_at(mbp, nmb)
    dw = [i for i in range(0, nmb, 4) if st_dsp[i:i + 4] != st_mac[i:i + 4]]
    applied = list(run.vm.rdf(SLOT + 8, 6))
    return case, {"knobs": knobs, "blocks": nb, "samples": samples, "identical": same, "first_block_differing": first,
                  "wet_peak": round(pk, 5), "mrb_state_bytes": nmb, "mrb_state_words_differing": len(dw),
                  "first_differing_offsets": dw[:8], "glue_knobs_applied": applied,
                  "magic": "%#x" % run.vm.rd32(SLOT), "rev_call_insns_mean": round(sum(rn[1:]) / (len(rn) - 1)),
                  "rev_call_insns_max": max(rn[1:])}


# ---------------------------------------------------------------- (moving)

def moves(kind, b, r, st):
    """the reverb's six knob words this block (v << 8, through the ColdFire's glide as soak_cmp4)"""
    t = st["t"]
    if kind == "ramp":
        for j in range(6):
            t[j] = (b * (3 + 2 * j) + 17 * j) % 255
            t[j] = t[j] if t[j] <= 127 else 254 - t[j]
    elif kind == "char":
        ph = (b % 6000) / 3000.0
        t[2] = 127 * (ph if ph <= 1 else 2 - ph)
        if b % 2250 == 2000:
            st["j"] = 127 - st.get("j", 0)
        if b % 2250 >= 2000:
            t[2] = st.get("j", 0)
    elif kind == "jumps":
        if b % 7 == 0:
            t[:] = [r.randrange(128) for _ in range(6)]
    elif kind == "hands":
        for j, h in enumerate(st["hands"]):
            t[j] = h.step(b)
    s = st["s"]
    for j in range(6):
        n = s[j] + 0.0298 * (256 * t[j] - s[j])
        if abs(n - 256 * t[j]) < 0.5:
            n = 256 * t[j]
        s[j] = n
    return [int(round(x)) & 0xffff for x in s]


def job_moving(args):
    kind, nb = args
    setup()
    lib = open_lib()
    run = Run("EEEM", base_words(INIT))
    run.prime_image()
    r = random.Random({"ramp": 1, "char": 2, "jumps": 3, "hands": 4}[kind])
    st = {"t": [INIT[n] for n in NAMES], "s": [256.0 * INIT[n] for n in NAMES]}
    if kind == "hands":
        import soak_sc01 as SK
        st["hands"] = [SK.Hand(r, INIT[n], 0, 127, "walk", period=(0.1, 1.5), jump=0.5) for n in NAMES]
    src = source("mixed", nb, 23)
    lib.mrb_glue_init()
    bl, io = bufs()
    pbuf = (C.c_float * 6)()
    same = samples = 0
    first, rn = None, []
    for b in range(nb):
        ws = moves(kind, b, r, st)
        for k, w in zip(WORDS_REV, ws):
            struct.pack_into("<H", run.r.d.frame, FB.FRAME_WORDS[k], w)
        c = run.block(*src[b], b)
        C.memmove(pbuf, c["p"], 24)
        C.memmove(bl[0], c["in"][:0x80], 0x80)
        C.memmove(bl[1], c["in"][0x80:], 0x80)
        lib.mrb_glue_process(pbuf, C.byref(io))
        mine = bytes(bl[2]) + bytes(bl[3])
        samples += 64
        k = same_count(mine, c["out"])
        same += k
        if k != 64 and first is None:
            first = b
        rn.append(c["rev_n"])
    n = lib.mrb_slot_size()
    slot_dsp = run.vm.read(SLOT, n)
    slot_mac = C.string_at(lib.mrb_native_slot_ptr(), n)
    dw = [i for i in range(0, n, 4) if slot_dsp[i:i + 4] != slot_mac[i:i + 4]]
    return kind, {"blocks": nb, "samples": samples, "identical": same, "first_block_differing": first,
                  "slot_bytes": n, "slot_words_differing": len(dw), "slot_first_differing_offsets": dw[:8],
                  "rev_call_insns_mean": round(sum(rn[1:]) / (len(rn) - 1)), "rev_call_insns_max": max(rn[1:]),
                  "knobs_applied_at_end": list(run.vm.rdf(SLOT + 8, 6))}


# ---------------------------------------------------------------- (lock) EEEE against EEEM, and the cost

def lock_cases():
    return {
        "b1: the reverb's VOL 0, everything moving (R1, the reverb's knobs too)": dict(rec="R1", vol=0, nb=4500),
        "b1: the reverb's VOL 0, the delay's FDBK high (K2)": dict(rec="K2", vol=0, nb=3000),
        "b2: the reverb's VOL up, everything moving": dict(rec="R1", vol=110, nb=4500),
        "cost: static, the new kit": dict(static=dict(INIT), vol=110, nb=1500),
        "cost: static, CHAR 0": dict(static=dict(INIT, CHAR=0), vol=110, nb=1500),
        "cost: static, CHAR 127": dict(static=dict(INIT, CHAR=127), vol=110, nb=1500),
        "cost: static, every knob 127": dict(static=dict(zip(NAMES, (127,) * 6)), vol=110, nb=1500),
        "cost: every reverb knob ramping every block": dict(ramp=True, vol=110, nb=1500),
    }


def job_lock(args):
    case, cfg = args
    setup()
    SC = G["SC"]
    nb = cfg["nb"]
    if "rec" in cfg:
        rc = dict(SC.recipes(1.0)["R1" if cfg["rec"] == "R1" else "K2"])
        if cfg["rec"] == "K2":
            rc["rev"] = dict(period=(0.5, 3), jump=0.3)
        ctl = SC.Control(rc)
        words = dict(ctl.words, REV_VOL=cfg["vol"])
    else:
        ctl = None
        words = base_words(cfg.get("static", dict(INIT)), cfg["vol"])
    names = ("EEEE", "EEEM") if "b1" in case or "b2" in case else ("EEEE", "EEES", "EEEM")
    runs = {n: Run(n, words) for n in names}
    for rr in runs.values():
        rr.prime_image()
    src = source("mixed", nb, 31)
    ramp = {"t": [INIT[n] for n in NAMES], "s": [256.0 * INIT[n] for n in NAMES]}
    res = {"blocks": nb, "mix_same": 0, "chr_same": 0, "del_same": 0, "pages_same": 0, "rev_out_peak": 0.0,
           "insns": {n: [] for n in runs}, "rev_insns": {n: [] for n in runs}, "first_mix_diff": None}
    fr = runs["EEEE"].r.d.frame
    for b in range(nb):
        if ctl is not None:
            ctl.step(b, fr)
            if cfg["vol"] == 0:
                struct.pack_into("<H", fr, FB.FRAME_WORDS["REV_VOL"], 0)
        elif cfg.get("ramp"):
            ws = moves("ramp", b, None, ramp)
            for k, w in zip(WORDS_REV, ws):
                struct.pack_into("<H", fr, FB.FRAME_WORDS[k], w)
        for n in names[1:]:
            runs[n].r.d.frame[:] = fr
        cs = {n: runs[n].block(*src[b], b) for n in names}
        ca, cb = cs["EEEE"], cs["EEEM"]
        res["mix_same"] += ca["mix"] == cb["mix"]
        if ca["mix"] != cb["mix"] and res["first_mix_diff"] is None:
            res["first_mix_diff"] = b
        res["chr_same"] += ca["chr"] == cb["chr"]
        res["del_same"] += ca["del"] == cb["del"]
        pa, pb = bytearray(ca["tx"]), bytearray(cb["tx"])
        for k in (0, 0x1000):
            pa[k:k + 4] = pb[k:k + 4] = bytes(4)
            for j in range(32):
                o = k + ST_ROW0 + ST_STRIDE * j + ST_REV
                pa[o:o + 6] = pb[o:o + 6] = bytes(6)
        res["pages_same"] += pa == pb
        for n, c in cs.items():
            res["insns"][n].append(c["n"])
            res["rev_insns"][n].append(c["rev_n"])
        res["rev_out_peak"] = max(res["rev_out_peak"], max(abs(x) for x in struct.unpack("<64f", cb["out"])))
        if b % 1500 == 1499:
            memguard.check(3072)
    out = {k: v for k, v in res.items() if k not in ("insns", "rev_insns")}
    for n in runs:
        xs, rs = res["insns"][n][1:], res["rev_insns"][n][1:]
        out[n] = {"insns_mean": round(sum(xs) / len(xs)), "insns_max": max(xs), "rev_call_mean": round(sum(rs) / len(rs)),
                  "rev_call_max": max(rs)}
    if "b1" in case:
        va, vb = runs["EEEE"].vm, runs["EEEM"].vm
        la, lb = bytes(va.mem["l1"][2]), bytes(vb.mem["l1"][2])
        base = 0x240000
        ign = [(0x2dd600, 0x2e7000), (0x300000, 0x318000), (VR.STACK_LO, VR.STACK_TOP), (0x382c84, 0x382c90),
               (0x383a20, 0x383a2c), (REV_OUT[0], REV_OUT[0] + 0x80), (REV_OUT[1], REV_OUT[1] + 0x80)] + [
            (2 * s & ~3, (2 * s + 8 + 3) & ~3) for s, _ in PARKS_SW] + [(t, t + 4) for t in G["VC"].TX]   # whole words
        dw = [base + i for i in range(0, len(la), 4) if la[i:i + 4] != lb[i:i + 4]
              and not any(lo <= base + i < hi for lo, hi in ign)]
        stream = [(t + ST_ROW0 + ST_STRIDE * j + ST_REV, t + ST_ROW0 + ST_STRIDE * j + ST_REV + 6)
                  for t in G["VC"].TX for j in range(32)]
        ns = len(dw)
        dw = [x for x in dw if not any(lo < x + 4 and x < hi for lo, hi in stream)]
        out["l1_words_differing_in_the_reply_pages_reverb_stream"] = ns - len(dw)
        zeros = {b"\0\0\0\0", b"\0\0\0\x80"}
        sz = [x for x in dw if la[x - base:x - base + 4] in zeros and lb[x - base:x - base + 4] in zeros]
        out["l1_signed_zeros_differing"] = len(sz)
        dw = [x for x in dw if x not in set(sz)]
        da, db_ = bytes(va.mem["ddr"][2]), bytes(vb.mem["ddr"][2])
        ddr = [0x80000000 + i for i in range(0, len(da), 4) if da[i:i + 4] != db_[i:i + 4]
               and not REV_REGION[0] <= 0x80000000 + i < REV_REGION[1]]
        out["l1_words_differing_at_end"] = len(dw)
        out["l1_first_differing"] = ["%#x" % x for x in dw[:12]]
        out["ddr_words_differing_outside_the_reverb_region"] = len(ddr)
        out["ddr_first_differing"] = ["%#x" % x for x in ddr[:12]]
    out["rev_out_peak"] = round(out["rev_out_peak"], 5)
    del runs
    gc.collect()
    return case, out


# ---------------------------------------------------------------- (exec)

def hit_pcs(vm):
    return {pc for pc in vm.decoded if vm.pages[pc >> 12][pc & 0xfff].hits}


def fxinit_init_pcs(name):
    stream, info, cache = load(name)
    from vm import VM, STOP_PC, ST_STOP
    vm = VM()
    vm.load_dn2()
    VR.load_stream(vm, stream)
    i6, i7 = vm.reg("I6"), vm.reg("I7")
    vm.set_reg("R2", i6)
    vm.set_reg("I6", i7)
    vm.wr32(i7, i6)
    vm.wr32(i7 - 4, STOP_PC - 1)
    vm.set_reg("I7", i7 - 8)
    vm.pc = VR.FXINIT
    assert vm.run(10**9, stop=FXINIT_REV_CALL) == ST_STOP
    vm.lib.vm_set_profile(vm.h, 1)
    assert vm.run(10**9, stop=FXINIT_REV_RET) == ST_STOP
    vm.lib.vm_set_profile(vm.h, 0)
    assert vm.run(10**9, stop=STOP_PC) == ST_STOP
    return hit_pcs(vm)


def ours_sw(info):
    out = []
    for p in info["placed"]:
        if p["kind"] == "sw_code":
            lo = int(p["sw"], 16)
            out.append((lo, lo + p["bytes"] // 2))
    return out


def job_exec(nb):
    setup()
    SC = G["SC"]
    rc = dict(SC.recipes(1.0)["R1"])
    runs = {}
    for name, mode in (("EEEE", "rev"), ("EEEE", "notrev"), ("EEEM", "rev"), ("EEEM", "all")):
        ctl = SC.Control(rc)
        run = Run(name, dict(ctl.words, REV_VOL=110))
        if mode == "all":
            run.vm.lib.vm_set_profile(run.vm.h, 1)
        else:
            run.profile = mode
            if mode == "notrev":
                run.vm.lib.vm_set_profile(run.vm.h, 1)
        src = source("mixed", nb, 41)
        for b in range(nb):
            ctl.step(b, run.r.d.frame)
            run.block(*src[b], b)
        runs[(name, mode)] = hit_pcs(run.vm)
        del run
        gc.collect()
    init = {n: fxinit_init_pcs(n) for n in ("EEEE", "EEEM")}
    info = load("EEEM")[1]
    ours = ours_sw(info)
    is_ours = lambda pc: any(lo <= pc < hi for lo, hi in ours)      # noqa: E731
    sym = {k: int(v, 16) for k, v in info["symbols"].items()}
    ft = [r for r in info["function_table"] if r["file"] == "faulttone"]
    ft_rng = (min(int(r["core"], 16) for r in ft) // 2, max(int(r["core"], 16) + r["bytes"] for r in ft) // 2)
    sites = {0x1c1d11, 0x1c1d14, 0x1c1d15, 0x1c1643, 0x1c1646, 0x1c1647}
    stock_rev = (runs[("EEEE", "rev")] | init["EEEE"]) - sites
    shared = stock_rev & runs[("EEEE", "notrev")]
    own = stock_rev - shared
    m_call = sorted(pc for pc in runs[("EEEM", "rev")] - sites if not is_ours(pc))
    m_init = sorted(pc for pc in init["EEEM"] - sites if not is_ours(pc))
    m_own = sorted(own & (runs[("EEEM", "all")] | init["EEEM"]))
    ft_hit = sorted(pc for pc in runs[("EEEM", "all")] | init["EEEM"] if ft_rng[0] <= pc < ft_rng[1])
    return "exec", {
        "blocks": nb,
        "stock reverb (EEEE): instructions run in its call and its init": len(stock_rev),
        "... shared with other stock code": len(shared),
        "... its own": len(own),
        "... the entry 0xb836bc and the init 0xb83dc1 among its own": [0xb836bc in own, 0xb83dc1 in own],
        "EEEM: instructions run in the reverb's call that are not ours (the call site aside)": len(m_call),
        "EEEM: ... listed": ["%#x" % x for x in m_call[:20]],
        "EEEM: instructions run in the reverb init's call that are not ours (the call site aside)": len(m_init),
        "EEEM: ... listed (init)": ["%#x" % x for x in m_init[:20]],
        "EEEM: our instructions run in the reverb's call": len(runs[("EEEM", "rev")] - sites),
        "EEEM: the stock reverb's own instructions run anywhere (init and blocks)": len(m_own),
        "EEEM: ... listed (own)": ["%#x" % x for x in m_own[:20]],
        "EEEM: fault-tone instructions run in normal blocks and the init (ft %#x-%#x)" % ft_rng: len(ft_hit),
        "ft symbols": {k: "%#x" % sym[k] for k in ("ft_fault", "ft_abort")},
    }


# ---------------------------------------------------------------- (stores)

def job_stores(nb):
    setup()
    SC = G["SC"]
    ctl = SC.Control(dict(SC.recipes(1.0)["R1"]))
    run = Run("EEEM", dict(ctl.words, REV_VOL=110))
    src = source("mixed", nb, 51)
    lib = open_lib()
    nslot = lib.mrb_slot_size()
    log, outside, total = [], [], 0
    allowed = [(SLOT, SLOT + nslot), (VR.STACK_LO, VR.STACK_TOP), SAVE, (REV_OUT[0], REV_OUT[0] + 0x80),
               (REV_OUT[1], REV_OUT[1] + 0x80)]
    for b in range(nb):
        ctl.step(b, run.r.d.frame)
        log.clear()
        run.block(*src[b], b, watch=log)
        total += len(log)
        for pc, ad, va in log:
            if not any(lo <= ad < hi for lo, hi in allowed):
                outside.append(("%#x" % ad, "%#x" % pc))
    stream, info, cache = load("EEEM")
    from vm import VM, watch as W, watch_log
    vm = VM()
    vm.load_dn2()
    VR.load_stream(vm, stream)
    W(vm, REV_REGION[0], REV_REGION[1])
    vm.call(VR.FXINIT, max_insns=10**9)
    wl = watch_log(vm)
    W(vm, 0, 0)
    ours = ours_sw(info)
    init_not_ours = [("%#x" % ad, "%#x" % pc) for pc, ad, va in wl if not any(lo <= pc < hi for lo, hi in ours)]
    return "stores", {"blocks": nb, "stores": total, "outside": len(outside), "first_outside": outside[:12],
                      "allowed": ["%#x-%#x" % r for r in allowed], "slot_bytes": nslot,
                      "effect_init_stores_into_the_reverb_region": len(wl),
                      "... not from our code": len(init_not_ours), "... first": init_not_ours[:8],
                      "... the highest address stored": "%#x" % max((ad for pc, ad, va in wl), default=0)}


# ---------------------------------------------------------------- (fault)

def expected_tone(code=None, abort=False):
    """the 128 words faulttone.s writes: frame n (0..63) L and R = h s(n, p1) + h s(n, p2), s = +1 in a period's first
    half; periods (frames): ILOPI 32, PARI 16, others 8; the abort two-tone 32 and 8"""
    if abort:
        p1, p2 = 32, 8
    else:
        p1 = {0x501: 32, 0x502: 16}.get(code, 8)
        p2 = p1
    words = []
    for n in range(64):
        v = (H if n % p1 < p1 // 2 else -H) + (H if n % p2 < p2 // 2 else -H)
        words += [v & 0xffffffff] * 2
    return words


def tone_facts(words):
    """the square's pitch(es) read back from the words: the sign changes of L over the 64-frame loop"""
    L = [struct.unpack("<i", struct.pack("<I", w))[0] for w in words[0::2]]
    lv = sorted(set(L))
    return {"levels": lv, "peak_dbfs": round(20 * math.log10(max(abs(x) for x in L) / 2 ** 31), 2) if any(L) else None,
            "L_equals_R": words[0::2] == words[1::2]}


FAULT_CASES = [
    ("EMUI handler 0xb8b546", 0xb8b546, None, False, 0x507),
    ("ILOPI handler 0xb8b560", 0xb8b560, None, False, 0x501),
    ("PARI handler 0xb8b57c", 0xb8b57c, None, False, 0x502),
    ("default handler 0xb8b9f2", 0xb8b9f2, None, False, 0xb),
    ("abort path 0xb8b538 (raise(2), then 0x1c1430)", 0xb8b538, None, True, None),
    ("main's end 0x1c1430", 0x1c1430, None, True, None),
    ("0x1c07c6 reached with an unknown record code (0x123)", 0x1c07c6, 0x123, False, 0x123),
]


def job_fault(case):
    name, entry, poke, abort, code = case
    setup()
    SC = G["SC"]
    from vm import ST_IDLE, VMError
    ctl = SC.Control(dict(SC.recipes(1.0)["R1"]))
    run = Run("EEEM", dict(ctl.words, REV_VOL=110))
    src = source("mixed", 300, 61)
    for b in range(300):                       # some blocks first: the buffers hold sound, MRB has state
        ctl.step(b, run.r.d.frame)
        run.block(*src[b], b)
    vm = run.vm
    info = run.info
    sym = {k: int(v, 16) for k, v in info["symbols"].items()}
    before_buf = vm.read(BUF0, 4 * BUF_WORDS)
    snap = {n: bytes(vm.mem[n][2]) for n in ("l1", "l2", "ddr")}
    if poke is not None:
        vm.wr32(REC + 4, poke)
    from vm import watch as W, watch_log
    W(vm, 0, 0xffffffff)
    if entry >= 0xb80000 and entry != 0xb8b538:
        # a core interrupt's entry: the interrupted PC on the PC stack (the PARI and default handlers read PCSTK)
        vm.lib.vm_pc_push(vm.h, REV_CALL)
    vm.pc = entry
    n0 = vm.icount
    err = None
    try:
        st = vm.run(10**6)
    except VMError as e:
        st, err = None, str(e)
    stores = watch_log(vm)
    W(vm, 0, 0)
    pc = vm.pc
    after = vm.read(BUF0, 4 * BUF_WORDS)
    words = list(struct.unpack("<%dI" % BUF_WORDS, after))
    want = expected_tone(code, abort)
    rec = list(struct.unpack("<4I", vm.read(REC, 16)))
    # a second run from the park: it stays there
    st2 = None
    pc2 = None
    if err is None:
        try:
            st2 = vm.run(1000)
            pc2 = vm.pc
        except VMError as e:
            st2 = "trap " + str(e)
    # memory changed outside the buffers, the record and the stack
    changed = {}
    for n in ("l1", "l2", "ddr"):
        base = vm.mem[n][0]
        a, b_ = snap[n], bytes(vm.mem[n][2])
        if a == b_:
            continue
        ws = [base + i for i in range(0, len(a), 4) if a[i:i + 4] != b_[i:i + 4]]
        ws = [x for x in ws if not (BUF0 <= x < BUF0 + 4 * BUF_WORDS or REC <= x < REC + 16
                                    or VR.STACK_LO <= x < VR.STACK_TOP)]
        if ws:
            changed[n] = ["%#x" % x for x in ws[:16]] + (["... %d words" % len(ws)] if len(ws) > 16 else [])
    ft_lo = sym["ft_fault"]
    ft_stores = [(ad, pc_) for pc_, ad, va in stores if pc_ >= ft_lo and pc_ < ft_lo + 0x80]
    return name, {
        "entry": "%#x" % entry, "status": st, "error": err, "instructions": vm.icount - n0, "pc": "%#x" % pc,
        "parked_in_ft_idle": err is None and st == ST_IDLE and ft_lo <= pc < ft_lo + 0x80,
        "second_run_status": st2, "second_run_pc": "%#x" % pc2 if pc2 is not None else None,
        "record": ["%#x" % x for x in rec], "record_code_expected": "%#x" % code if code is not None else None,
        "buffers_as_expected": words == want, "buffers_changed": before_buf != after,
        "words_differing": sum(1 for x, y in zip(words, want) if x != y),
        "first_words": ["%#010x" % w for w in words[:8]], "tone": tone_facts(words),
        "ft_stores": len(ft_stores), "ft_stores_outside_buffers": sum(1 for ad, _ in ft_stores
                                                                      if not BUF0 <= ad < BUF0 + 4 * BUF_WORDS),
        "memory_changed_outside_buffers_record_stack": changed,
        "stores_total": len(stores),
    }


# ---------------------------------------------------------------- (silence, kill)

def job_quiet(args):
    mode, sound, total = args
    import verify_packs as VP
    VP.PACKS = VP.PACKS + ("EEEM",)
    import verify_sc01 as S1
    st, info, cache = load("EEEM")
    off = S1.VC.offsets(VP.OUT / "offsets", "CMP3")
    VP.setup(off)
    import soak_te01 as ST
    if not ST.G:
        ST.setup(VP.G["S1"].OFF.copy())
    V, VC, MG = VP.G["V"], VP.G["VC"], VP.G["memguard"]
    from vm import VMError
    BPS = ST.BPS
    N0, NB = int(sound * BPS), int(total * BPS)
    vm = VR.candidate_vm(st, cache=cache)
    cfg = dict(ST.recipes(1.0)["R4a"], seconds=total, seed=977 if mode == "silence" else 978)
    ctl = ST.Control(cfg)
    r = random.Random(cfg["seed"] + 3)
    rev_h = [ST.Hand(r, INIT[n], 0, 127, "walk", period=(0.5, 6), jump=0.15) for n in NAMES]
    rev_s = [256.0 * h.v for h in rev_h]
    held = [None] * 6
    d = FB.FullDSP(vm=vm)
    d.set(**ctl.words)
    d.set_raw(**VC.ROUTE_INFX)
    fr = d.frame
    src = ST.Source("mixed", random.Random(cfg["seed"] + 7))
    pr = {}

    def at_call():
        pr["n0"] = vm.icount

    def at_ret():
        pr["n"] = vm.icount - pr["n0"]
        pr["rev"] = vm.rdf(REV_OUT[0], 32) + vm.rdf(REV_OUT[1], 32)
    probes = [(REV_CALL, at_call), (REV_RET, at_ret)]
    res = dict(mode=mode, sound_s=sound, total_s=total, N0=N0, faults=[], windows=[], kill_words=[])
    win, kill_vol, t0, b = None, None, time.time(), 0
    try:
        for b in range(NB):
            if b >= N0:
                ctl.mode_next = 1 << 60
                ctl.mangle = None
            ctl.step(b, fr)
            for j, k in enumerate(WORDS_REV):
                if b < N0:
                    t = 256.0 * rev_h[j].step(b)
                    held[j] = t
                else:
                    t = held[j]
                s = rev_s[j] + ST.GLIDE * (t - rev_s[j])
                if abs(s - t) < 0.5:
                    s = t
                rev_s[j] = s
                struct.pack_into("<H", fr, FB.FRAME_WORDS[k], int(round(s)) & 0xffff)
            if mode == "kill" and b >= N0:
                kill_vol = struct.unpack_from("<H", fr, FB.FRAME_WORDS["REV_VOL"])[0] if kill_vol is None \
                    else kill_vol * 127 // 128
                for k in ("REV_DEC", "DEL_FDBK", "DEL_VOL"):
                    struct.pack_into("<H", fr, FB.FRAME_WORDS[k], 0)
                struct.pack_into("<H", fr, FB.FRAME_WORDS["REV_VOL"], kill_vol)
                if b - N0 in (0, 1, 100, 1000, 2000):
                    res["kill_words"].append(dict(block=b, words={k: struct.unpack_from("<H", fr, FB.FRAME_WORDS[k])[0]
                                                                  for k in ("REV_DEC", "REV_VOL", "DEL_FDBK", "DEL_VOL")}))
            L, R = src.block(b) if b < N0 else ([0.0] * 32, [0.0] * 32)
            vm.wr32(FB.SPI_HALF, b & 1)
            vm.write(FB.RX_BASE + 0x1000 * (b & 1), bytes(fr))
            h_ = vm.rd32(FB.DMA_HALF)
            vm.write(FB.CODEC_IN + 0x100 * h_, struct.pack("<64I", *[FB.q31(v) for pair in zip(L, R) for v in pair]))
            ST.G["V"] = V
            pr.clear()
            try:
                n = ST.probed(vm, probes, ST.CAP)
            except VMError as e:
                raise ST.Fault(json.dumps({"kind": "trap", "message": str(e), "pc": "%#x" % vm.pc}))
            if b % (10 * BPS) == 0 or b == N0:
                if win:
                    res["windows"].append(win)
                win = dict(block=b, sec=round(b / BPS, 2), rev_n_min=1 << 40, rev_n_max=0, rev_peak=0.0, rev_bad=0,
                           out_peak=0.0, out_bad=0, rev_last_nonzero=None, block_insns_max=0)
            sn = pr.get("n", 0)
            win["rev_n_min"] = min(win["rev_n_min"], sn)
            win["rev_n_max"] = max(win["rev_n_max"], sn)
            win["block_insns_max"] = max(win["block_insns_max"], n)
            p, bad = VP.peak(pr.get("rev", ()))
            win["rev_peak"] = max(win["rev_peak"], p)
            win["rev_bad"] += bad
            if p > 0:
                win["rev_last_nonzero"] = b
            p, bad = VP.peak(vm.rdf(FB.OUT_FLOATS, 64))
            win["out_peak"] = max(win["out_peak"], p)
            win["out_bad"] += bad
            if b % BPS == BPS - 1:
                MG.check(3072)
            if b % (30 * BPS) == 0:
                print("%s block %d (%.0f s), %.0f s wall" % (mode, b, b / BPS, time.time() - t0), flush=True)
            res["blocks_run"] = b + 1
    except ST.Fault as f:
        res["faults"].append(dict(json.loads(str(f)), block=b))
    except Exception as e:
        res["faults"].append({"kind": "error", "message": repr(e)[:400], "block": b, "pc": "%#x" % vm.pc})
    if win:
        res["windows"].append(win)
    lib = open_lib()
    n = lib.mrb_slot_size()
    slot = vm.read(SLOT, n)
    fl = struct.unpack("<%df" % (n // 4), slot[:n // 4 * 4])
    res["slot_magic"] = "%#x" % struct.unpack_from("<I", slot, 0)[0]
    res["slot_fresh"] = struct.unpack_from("<I", slot, 4)[0]
    res["slot_nonfinite_words"] = sum(1 for x in fl if x != x or x in (float("inf"), float("-inf")))
    res["out_bad_total"] = sum(w["out_bad"] for w in res["windows"])
    res["rev_bad_total"] = sum(w["rev_bad"] for w in res["windows"])
    res["rev_last_nonzero_block"] = max((w["rev_last_nonzero"] for w in res["windows"] if w["rev_last_nonzero"]),
                                        default=None)
    res["insns_max"] = max(w["block_insns_max"] for w in res["windows"])
    res["wall_s"] = round(time.time() - t0)
    return mode, res


# ---------------------------------------------------------------- main

def run_job(fn, arg):
    return fn(arg)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", default="static,moving,lock,exec,stores,fault,silence,kill")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--quick", action="store_true", help="short runs (a smoke test)")
    a = ap.parse_args()
    only = a.only.split(",")
    OUT.mkdir(parents=True, exist_ok=True)
    native_lib()
    q = a.quick
    jobs = []
    if "static" in only:
        jobs += [(job_static, (c, k, 150 if q else 1500), "static", c) for c, k in STATIC.items()]
    if "moving" in only:
        jobs += [(job_moving, (k, 300 if q else 3000), "moving", k) for k in ("ramp", "char", "jumps", "hands")]
    if "lock" in only:
        jobs += [(job_lock, (c, dict(cfg, nb=300) if q else cfg), "lock", c) for c, cfg in lock_cases().items()]
    if "exec" in only:
        jobs.append((job_exec, 150 if q else 600, "exec", "exec"))
    if "stores" in only:
        jobs.append((job_stores, 100 if q else 300, "stores", "stores"))
    if "fault" in only:
        jobs += [(job_fault, c, "fault", c[0]) for c in FAULT_CASES]
    if "silence" in only:
        jobs.append((job_quiet, ("silence", 2.0, 4.0) if q else ("silence", 15.0, 195.0), "silence", "silence"))
    if "kill" in only:
        jobs.append((job_quiet, ("kill", 2.0, 4.0) if q else ("kill", 15.0, 75.0), "kill", "kill"))
    setup()
    for n in DIRS:
        stream, info, cache = load(n)
        VR.candidate_vm(stream, cache=cache)
    res = {"images": {n: (load(n)[1] or {"section7": {"sha256": "stock"}})["section7"]["sha256"] for n in DIRS}}
    t0 = time.time()
    # the long ones first
    jobs.sort(key=lambda j: 0 if j[2] in ("silence", "kill") else 1)
    with ProcessPoolExecutor(a.workers, max_tasks_per_child=1) as ex:
        futs = {ex.submit(fn, arg): (kind, label) for fn, arg, kind, label in jobs}
        for f in as_completed(futs):
            kind, label = futs[f]
            try:
                name, r = f.result()
            except Exception as e:
                name, r = label, {"error": repr(e)[:800]}
            res.setdefault(kind, {})[name] = r
            print(time.strftime("%H:%M:%S"), kind, name, json.dumps(r)[:500], flush=True)
            part = OUT / ("%s%s.json" % (a.only.replace(",", "-") if len(only) < 8 else "all", "-quick" if q else ""))
            res["wall_s"] = round(time.time() - t0)
            part.write_text(json.dumps(res, indent=1))
    print("wrote", part)


if __name__ == "__main__":
    main()
