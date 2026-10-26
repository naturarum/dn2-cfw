#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""SP01 (2026-10-24; notes/SP01-spring-pack.md): the DSP proofs of SP01 = DT01 (DJ01's DSP) with SPNG, the spring reverb,
in the reverb slot. Whole images in sharcvm (tools/dspsplice/vmrun.py: the boot stream over the stock baseline, the
effect init 0x1c1445, the block handler 0x1c9d6b with a ColdFire frame and codec input).

    python3 tools/dspsplice/verify_sp01.py [--only mac,native,lock,exec,stores] [--workers 2] [--quick]

(mac)    SPNG = the Mac reference, through fx/render.c: SP01's whole image on static reverb settings (the init patch, an
         old kit at the stock defaults, MODE 0 / 32 / 96 / 127, every knob at 0 and at 127, heavy, dark, WDTH 0 and
         127), the external input sent to all three effects with the cross-sends on; the reverb's input captured at its
         call every block and written as a float WAV, fxrender -fx spring -mix wet -bits 32 -block 32 with the same
         seven knobs on it: its output = the reverb's output in the image, every sample bit for bit. The frame is put
         in the DSP's frame image before the first block, so the glue's first call sets and snaps the kit's knobs as
         fxrender starts (init, set, snap).
(native) the glue and SPNG = their Mac build (clang -O2 -ffp-contract=off, -DSPG_NATIVE: spng_glue.c and fx/spring.c)
         with the knobs moving: every reverb knob ramping every block, MODE swept 0 -> 127 -> 0 and jumping, random
         jumps of all seven every 7 blocks, and the soak's R1 hands; the parameter struct (6 floats), the k 48 image
         word and the input captured at every call and fed to the Mac build's spg_process: every output sample bit for
         bit, and the whole SpgSlot (43,072 B: the glue's knobs and the spring's state) byte for byte at the end.
(lock)   DT01 (DJ01's DSP, the stock reverb) and SP01 in lockstep on the same frames and input (soak_cmp4.py's Control:
         the band knobs through the tail, MUP mangles, the flanger's four knobs, the stock delay's knobs and X, the
         tempo; the reverb's seven knobs moving too): (b1) the reverb's VOL at 0 (its return silent in both): every
         handler output sample (the compressor's output: the whole mix), the chorus slot's (the flanger) and the
         delay's outputs, both reply pages (the meters; word 0, the cycle count, aside) every block bit for bit, and at
         the end L1 word for word outside our code, the stack and the reverb's hook sites, and DDR outside the reverb's
         region; (b2) the reverb's VOL up: the flanger's and the delay's outputs every block bit for bit (they come
         before the reverb on the bus). Instructions per block of both, every block, and of the reverb call; static
         cost cases too: the worst block of each image and the difference.
(exec)   the stock reverb no longer runs: sharcvm's per-instruction counters over the reverb's call and its init call in
         DT01 give the stock reverb's instructions; in SP01, over the same blocks, the reverb's call and the init's run
         only our code, and none of the stock reverb's instructions run anywhere but those it shares with other stock
         code (listed);
(stores) the reverb call in SP01 stores only into SpgSlot, the stack, the stub save area and the reverb's output
         buffers.
Results: out/SP01/SP01-dsp/verify.json (or verify-<only>.json), the WAVs in out/SP01/verify/.
"""
import argparse
import ctypes as C
import gc
import json
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
import vmrun as VR  # noqa: E402
import fullblock as FB  # noqa: E402
import memguard  # noqa: E402

OUT = ROOT / "out/SP01/SP01-dsp"
WORK = ROOT / "out/SP01/verify"
IMAGES = {"DT01": ROOT / "out/DT01/DT01-dsp", "SP01": ROOT / "out/SP01/SP01-dsp"}
REV_CALL, REV_RET = 0x1c1d11, 0x1c1d18
REV_IN, REV_OUT = (0x25b268, 0x25b2e8), (0x25b368, 0x25b3e8)
REV_P, IMG_K48 = 0x257918, 0x25ceb4
FXINIT_REV_CALL, FXINIT_REV_RET = 0x1c1643, 0x1c164a
SPG, SPG_BYTES = 0x80000018, 43072
REV_REGION = (0x80000018, 0x8005912c)
SAVE = (0x2e6800, 0x2e686c)
K48 = 0xa28
# the reply pages' audio streams (0x1c2641..: 14 streams x 32 samples, 6 bytes each, rows of 84 bytes from +0x1c): stream
# 11 (bytes 66..71 of a row) carries the reverb's wet output [verified: the code at 0x1c26b2 converts 14 float buffers;
# in SP01 that stream is nonzero only in blocks whose reverb output is (the lock check below counts both ways)], so it
# differs from DT01's by design
ST_ROW0, ST_STRIDE, ST_REV = 0x1c, 0x54, 66
NAMES = ("PRE", "DEC", "MODE", "DWEL", "HPF", "TONE", "WDTH")
WORDS_REV = ("REV_PRE", "REV_DEC", "REV_FREQ", "REV_GAIN", "REV_HPF", "REV_LPF")
SPRING_KNOBS = dict(zip(NAMES, (0, 64, 64, 64, 0, 127, 96)))      # the spring's init patch
G = {}


def load(name):
    d = IMAGES[name]
    info = json.loads((d / "build.json").read_text())
    stream = (d / "section7.bin").read_bytes()
    import build as BD
    if BD.sha(stream) != info["section7"]["sha256"]:
        raise SystemExit(f"{d}: section7.bin is not the image build.json describes")
    return stream, info, f"sp01v-{name}-" + info["section7"]["sha256"][:12]


def setup():
    """soak_cmp4's globals (verify_sc01's VC, S, V2 and the compressor offsets) for its Control"""
    if G:
        return
    import soak_cmp4 as SC
    SC.setup(SC.cmp_offsets(), IMAGES["SP01"])
    G.update(SC=SC, S=SC.G["S"], VC=SC.G["VC"], V2=SC.G["V2"])


def fbits(xs):
    return struct.pack("<%df" % len(xs), *xs)


def base_words(rev, rev_vol=110):
    """every effect on, the external input into all three, the cross-sends on; the reverb's knobs from rev"""
    setup()
    VC = G["VC"]
    import verify_flg as FL
    import verify_ftd as FT
    w = dict(VC.BASE, IN_CHR_L=100, IN_CHR_R=100, IN_DEL_L=100, IN_DEL_R=100, IN_REV_L=100, IN_REV_R=100, CHR_DEL=40,
             CHR_REV=80, CHR_VOL=90, DEL_REV=90, DEL_VOL=100, REV_VOL=rev_vol, **FL.KNOBS, **FT.DKNOBS,
             **VC.comp(32, 24, 32, 64, 3, 64))
    for k, n in zip(WORDS_REV, NAMES):
        w[k] = rev[n]
    return w


class Run:
    """one image in sharcvm with the reverb's call probed (its parameters, k 48's image word, input and output, and its
    instructions), the chorus and delay outputs, the handler output and instructions, every block"""

    def __init__(self, name, words, wdth, tempo=120.0):
        stream, info, cache = load(name)
        self.name = name
        self.vm = VR.candidate_vm(stream, cache=cache)
        self.r = VR.Runner(self.vm, words, tempo=tempo)
        self.r.d.set_raw(**G["VC"].ROUTE_INFX)
        struct.pack_into("<H", self.r.d.frame, K48, wdth << 8)
        self.tail = [int(256 * v) & 0xffff for v in G["S"].knobs17(G["S"].START)] + [64 << 8] * 3 + [0] * 7
        self.cap = []
        self.profile = None             # "rev": count only over the reverb's call; "notrev": everything but it

    def prime_image(self):
        """the frame in the DSP's frame image (as if it had arrived the block before): the first block's unpack sees
        it, so the reverb's first call has the kit's knobs"""
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
            c["k48"] = vm.rd32(IMG_K48)
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
    """the external input: drums and a mix (out/T3.11/dry), with noise bursts (soak_sc01's Source) or plucks"""
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


# ---------------------------------------------------------------- (mac) static settings against fxrender

MAC_CASES = {
    "init patch": dict(SPRING_KNOBS),
    "old kit at the stock defaults (PRE 16 DEC 31 MODE 64 DWEL 50 HPF 32 TONE 96 WDTH 1)":
        dict(zip(NAMES, (16, 31, 64, 50, 32, 96, 1))),
    "MODE 0": dict(SPRING_KNOBS, MODE=0),
    "MODE 32": dict(SPRING_KNOBS, MODE=32),
    "MODE 96": dict(SPRING_KNOBS, MODE=96),
    "MODE 127": dict(SPRING_KNOBS, MODE=127),
    "every knob 0": dict(zip(NAMES, (0,) * 7)),
    "every knob 127": dict(zip(NAMES, (127,) * 7)),
    "heavy: DEC 127 DWEL 127 MODE 127 PRE 40": dict(SPRING_KNOBS, DEC=127, DWEL=127, MODE=127, PRE=40),
    "dark and thin: TONE 0 HPF 127 WDTH 0": dict(SPRING_KNOBS, TONE=0, HPF=127, WDTH=0),
    "WDTH 127": dict(SPRING_KNOBS, WDTH=127),
}


def fxrender():
    """the Mac reference: fx/render.c with fx/spring.c as on disk (the Makefile's flags)"""
    WORK.mkdir(parents=True, exist_ok=True)
    exe = WORK / "fxrender"
    fx = ROOT / "fx"
    srcs = ["render.c", "wav.c", "tapedelay.c", "mbcomp.c", "flanger.c", "spaceecho.c", "spring.c"]
    subprocess.check_call(["clang", "-O2", "-fno-strict-aliasing", "-ffp-contract=off", "-std=c99",
                           "-o", str(exe)] + [str(fx / s) for s in srcs] + ["-lm"])
    return exe


def write_wav_f32(path, L, R):
    n = len(L)
    data = b"".join(struct.pack("<ff", a, b) for a, b in zip(L, R))
    hdr = b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 3, 2, 48000,
                                                                                    48000 * 8, 8, 32)
    Path(path).write_bytes(hdr + b"data" + struct.pack("<I", len(data)) + data)
    return n


def read_wav_f32(path):
    b = Path(path).read_bytes()
    i = b.index(b"data")
    n = struct.unpack_from("<I", b, i + 4)[0]
    v = struct.unpack_from("<%df" % (n // 4), b, i + 8)
    return list(v[0::2]), list(v[1::2])


def job_mac(args):
    case, knobs, nb = args
    setup()
    exe = WORK / "fxrender"
    run = Run("SP01", base_words(knobs), knobs["WDTH"])
    run.prime_image()
    src = source("mixed", nb, 11)
    inL, inR, oL, oR, pk = [], [], [], [], 0.0
    for b in range(nb):
        c = run.block(*src[b], b)
        x = struct.unpack("<64f", c["in"])
        y = struct.unpack("<64f", c["out"])
        inL += x[:32]
        inR += x[32:]
        oL += y[:32]
        oR += y[32:]
        pk = max(pk, max(abs(v) for v in y))
    tag = "".join(ch if ch.isalnum() else "_" for ch in case)[:40]
    wi, wo = WORK / f"mac-{tag}-in.wav", WORK / f"mac-{tag}-render.wav"
    write_wav_f32(wi, inL, inR)
    cmd = [str(exe), "-fx", "spring", "-i", str(wi), "-o", str(wo), "-mix", "wet", "-bits", "32", "-block", "32"]
    for n in NAMES:
        cmd += ["-p", f"{n}={knobs[n]}"]
    subprocess.run(cmd, check=True, capture_output=True)
    rL, rR = read_wav_f32(wo)
    n = len(oL)
    a_, b_ = fbits(oL) + fbits(oR), fbits(rL[:n]) + fbits(rR[:n])
    sa = 2 * n if a_ == b_ else sum(1 for i in range(0, 8 * n, 4) if a_[i:i + 4] == b_[i:i + 4])
    first = None if a_ == b_ else next(i for i in range(n) if fbits(oL[i:i + 1] + oR[i:i + 1])
                                       != fbits(rL[i:i + 1] + rR[i:i + 1]))
    applied = list(run.vm.rdf(SPG + 8, 7))
    return case, {"knobs": knobs, "blocks": nb, "samples": 2 * len(oL), "identical": sa, "first_difference": first,
                  "render_frames": len(rL), "wet_peak": round(pk, 5), "glue_knobs_applied": applied,
                  "command": " ".join(Path(x).name if "/" in x else x for x in cmd)}


# ---------------------------------------------------------------- (native) moving knobs against the Mac build

class FxIo(C.Structure):
    _fields_ = [("n", C.c_int32), ("inp", C.POINTER(C.POINTER(C.c_float))), ("r08", C.c_int32),
                ("out", C.POINTER(C.POINTER(C.c_float))), ("r10", C.c_int32), ("bpm", C.c_float)]


def native_lib():
    WORK.mkdir(parents=True, exist_ok=True)
    lib = WORK / "libspgnative.dylib"
    subprocess.check_call(["clang", "-O2", "-ffp-contract=off", "-fno-strict-aliasing", "-shared", "-fPIC",
                           "-DSPG_NATIVE", f"-I{ROOT / 'fx'}", f"-I{ROOT / 'tools/dspsplice/src'}", "-o", str(lib),
                           str(ROOT / "tools/dspsplice/src/spng_glue.c"), str(ROOT / "fx/spring.c")])
    return lib


def native_moves(kind, b, r, st):
    """the reverb's knob words this block (frame values v << 8, smoothed by the ColdFire's glide as soak_cmp4 does),
    and k 48"""
    t = st["t"]
    if kind == "ramp":                 # every knob ramping every block: a new target every block
        for j in range(7):
            t[j] = (b * (3 + 2 * j) + 17 * j) % 255
            t[j] = t[j] if t[j] <= 127 else 254 - t[j]
    elif kind == "mode":               # MODE swept 0 -> 127 -> 0 over 4 s, a jump every 1.5 s
        ph = (b % 6000) / 3000.0
        t[2] = 127 * (ph if ph <= 1 else 2 - ph)
        if b % 2250 == 2000:
            st["j"] = 127 - st.get("j", 0)
        if b % 2250 >= 2000:
            t[2] = st.get("j", 0)
    elif kind == "jumps":              # all seven jump anywhere every 7 blocks
        if b % 7 == 0:
            t[:] = [r.randrange(128) for _ in range(7)]
    elif kind == "hands":              # the soak's R1 hands
        for j, h in enumerate(st["hands"]):
            t[j] = h.step(b)
    s = st["s"]
    for j in range(7):
        n = s[j] + 0.0298 * (256 * t[j] - s[j])
        if abs(n - 256 * t[j]) < 0.5:
            n = 256 * t[j]
        s[j] = n
    return [int(round(x)) & 0xffff for x in s]


def job_native(args):
    kind, nb = args
    setup()
    SC = G["SC"]
    lib = C.CDLL(str(WORK / "libspgnative.dylib"))
    lib.spg_native_slot_ptr.restype = C.c_void_p
    lib.spg_slot_size.restype = C.c_uint32
    k48v = C.c_int32.in_dll(lib, "spg_native_k48")
    run = Run("SP01", base_words(SPRING_KNOBS), 96)
    run.prime_image()
    r = random.Random({"ramp": 1, "mode": 2, "jumps": 3, "hands": 4}[kind])
    st = {"t": [SPRING_KNOBS[n] for n in NAMES], "s": [256.0 * SPRING_KNOBS[n] for n in NAMES]}
    if kind == "hands":
        import soak_sc01 as SK
        st["hands"] = [SK.Hand(r, SPRING_KNOBS[n], 0, 127, "walk", period=(0.1, 1.5), jump=0.5) for n in NAMES]
    src = source("mixed", nb, 23)
    lib.spg_init()
    bl = [(C.c_float * 32)() for _ in range(4)]
    inp = (C.POINTER(C.c_float) * 2)(C.cast(bl[0], C.POINTER(C.c_float)), C.cast(bl[1], C.POINTER(C.c_float)))
    out = (C.POINTER(C.c_float) * 2)(C.cast(bl[2], C.POINTER(C.c_float)), C.cast(bl[3], C.POINTER(C.c_float)))
    io = FxIo(32, C.cast(inp, C.POINTER(C.POINTER(C.c_float))), 0, C.cast(out, C.POINTER(C.POINTER(C.c_float))), 0,
              120.0)
    pbuf = (C.c_float * 6)()
    same = samples = 0
    first = None
    rn = []
    for b in range(nb):
        ws = native_moves(kind, b, r, st)
        for k, w in zip(WORDS_REV, ws[:6]):
            struct.pack_into("<H", run.r.d.frame, FB.FRAME_WORDS[k], w)
        struct.pack_into("<H", run.r.d.frame, K48, ws[6])
        c = run.block(*src[b], b)
        C.memmove(pbuf, c["p"], 24)
        k48v.value = struct.unpack("<i", struct.pack("<I", c["k48"]))[0]
        C.memmove(bl[0], c["in"][:0x80], 0x80)
        C.memmove(bl[1], c["in"][0x80:], 0x80)
        lib.spg_process(pbuf, C.byref(io))
        mine = bytes(bl[2]) + bytes(bl[3])
        samples += 64
        if mine == c["out"]:
            same += 64
        else:
            same += sum(1 for i in range(0, 256, 4) if mine[i:i + 4] == c["out"][i:i + 4])
            if first is None:
                first = b
        rn.append(c["rev_n"])
    slot_dsp = run.vm.read(SPG, SPG_BYTES)
    slot_mac = C.string_at(lib.spg_native_slot_ptr(), lib.spg_slot_size())
    diff_words = [i for i in range(0, SPG_BYTES, 4) if slot_dsp[i:i + 4] != slot_mac[i:i + 4]]
    return kind, {"blocks": nb, "samples": samples, "identical": same, "first_block_differing": first,
                  "slot_bytes": [len(slot_dsp), lib.spg_slot_size()], "slot_words_differing": len(diff_words),
                  "slot_first_differing_offsets": diff_words[:8], "rev_call_insns_mean": round(sum(rn) / len(rn)),
                  "rev_call_insns_max": max(rn), "knobs_applied_at_end": list(run.vm.rdf(SPG + 8, 7))}


# ---------------------------------------------------------------- (lock) DT01 against SP01

def lock_cases(quick):
    s = 0.25 if quick else 1.0
    n = lambda x: max(300, int(x * s))           # noqa: E731
    return {
        "b1: the reverb's VOL 0, everything moving (K1 with the reverb's knobs)": dict(rec="R1", vol=0, nb=n(4500)),
        "b1: the reverb's VOL 0, the flanger spun hard (F1)": dict(rec="F1", vol=0, nb=n(3000)),
        "b1: the reverb's VOL 0, SPD synced (S1)": dict(rec="S1", vol=0, nb=n(3000)),
        "b2: the reverb's VOL up, everything moving": dict(rec="R1", vol=110, nb=n(4500)),
        "b2: the reverb's VOL up, the delay's FDBK high (K2)": dict(rec="K2", vol=110, nb=n(3000)),
        "cost: static, the spring's init patch": dict(static=dict(SPRING_KNOBS), vol=110, nb=n(1500)),
        "cost: static, MODE 0": dict(static=dict(SPRING_KNOBS, MODE=0), vol=110, nb=n(1500)),
        "cost: static, MODE 127": dict(static=dict(SPRING_KNOBS, MODE=127), vol=110, nb=n(1500)),
        "cost: static, every knob 127": dict(static=dict(zip(NAMES, (127,) * 7)), vol=110, nb=n(1500)),
        "cost: every reverb knob ramping every block": dict(ramp=True, vol=110, nb=n(1500)),
    }


def job_lock(args):
    case, cfg = args
    setup()
    SC = G["SC"]
    nb = cfg["nb"]
    if "rec" in cfg:
        rc = dict(SC.recipes(1.0)[cfg["rec"]])
        if cfg["rec"] == "K2":
            rc["rev"] = dict(period=(0.5, 3), jump=0.3)
        ctl = SC.Control(rc)
        words = dict(ctl.words, REV_VOL=cfg["vol"])
        wd = 96
    else:
        ctl = None
        kn = cfg.get("static", dict(SPRING_KNOBS))
        words = base_words(kn, cfg["vol"])
        wd = kn["WDTH"]
    runs = {n: Run(n, words, wd) for n in ("DT01", "SP01")}
    for rr in runs.values():
        rr.prime_image()
    src = source("mixed", nb, 31)
    ramp = {"t": [SPRING_KNOBS[n] for n in NAMES], "s": [256.0 * SPRING_KNOBS[n] for n in NAMES]}
    res = {"blocks": nb, "mix_same": 0, "chr_same": 0, "del_same": 0, "pages_same": 0, "rev_out_peak": 0.0,
           "rev_stream_nonzero_while_rev_out_zero": 0, "rev_stream_zero_while_rev_out_nonzero": 0,
           "insns": {n: [] for n in runs}, "rev_insns": {n: [] for n in runs}, "first_mix_diff": None}
    fr = runs["DT01"].r.d.frame
    for b in range(nb):
        if ctl is not None:
            ctl.step(b, fr)
        elif cfg.get("ramp"):
            ws = native_moves("ramp", b, None, ramp)
            for k, w in zip(WORDS_REV, ws[:6]):
                struct.pack_into("<H", fr, FB.FRAME_WORDS[k], w)
            struct.pack_into("<H", fr, K48, ws[6])
        runs["SP01"].r.d.frame[:] = fr
        ca = runs["DT01"].block(*src[b], b)
        cb = runs["SP01"].block(*src[b], b)
        res["mix_same"] += ca["mix"] == cb["mix"]
        if ca["mix"] != cb["mix"] and res["first_mix_diff"] is None:
            res["first_mix_diff"] = b
        res["chr_same"] += ca["chr"] == cb["chr"]
        res["del_same"] += ca["del"] == cb["del"]
        pa, pb = bytearray(ca["tx"]), bytearray(cb["tx"])
        st11 = b"".join(bytes(pb[o:o + 6]) for k in (0, 0x1000)
                        for o in [k + ST_ROW0 + ST_STRIDE * j + ST_REV for j in range(32)])
        ro = any(struct.unpack("<64f", cb["out"]))
        res["rev_stream_nonzero_while_rev_out_zero"] += any(st11) and not ro
        res["rev_stream_zero_while_rev_out_nonzero"] += ro and not any(st11)
        for k in (0, 0x1000):
            pa[k:k + 4] = pb[k:k + 4] = bytes(4)
            for j in range(32):
                o = k + ST_ROW0 + ST_STRIDE * j + ST_REV
                pa[o:o + 6] = pb[o:o + 6] = bytes(6)
        res["pages_same"] += pa == pb
        for n, c in (("DT01", ca), ("SP01", cb)):
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
        va, vb = runs["DT01"].vm, runs["SP01"].vm
        la, lb = bytes(va.mem["l1"][2]), bytes(vb.mem["l1"][2])
        base = 0x240000
        ign = [(0x2dd600, 0x2e7000), (0x300000, 0x318000), (VR.STACK_LO, VR.STACK_TOP), (0x382c84, 0x382c90),
               (0x383a20, 0x383a2c), (REV_OUT[0], REV_OUT[0] + 0x80), (REV_OUT[1], REV_OUT[1] + 0x80)] + [
            (t, t + 4) for t in G["VC"].TX]
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
        out["l1_signed_zeros_range"] = ["%#x" % min(sz), "%#x" % (max(sz) + 4)] if sz else None
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


# ---------------------------------------------------------------- (exec) the stock reverb no longer runs

def hit_pcs(vm):
    return {pc for pc in vm.decoded if vm.pages[pc >> 12][pc & 0xfff].hits}


def fxinit_init_pcs(name):
    """a fresh VM, the effect init run by hand with the profile on only over the reverb init's CJUMP"""
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


def job_exec(args):
    nb = args
    setup()
    SC = G["SC"]
    rc = dict(SC.recipes(1.0)["R1"])
    runs = {}
    for name, mode in (("DT01", "rev"), ("DT01", "notrev"), ("SP01", "rev"), ("SP01", "all")):
        ctl = SC.Control(rc)
        run = Run(name, dict(ctl.words, REV_VOL=110), 96)
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
    init = {n: fxinit_init_pcs(n) for n in ("DT01", "SP01")}
    info = load("SP01")[1]
    ours = []
    for p in info["placed"]:
        if p["kind"] == "sw_code":
            lo = int(p["sw"], 16)
            ours.append((lo, lo + p["bytes"] // 2))
    is_ours = lambda pc: any(lo <= pc < hi for lo, hi in ours)      # noqa: E731
    # the FX bus's and the effect init's own CJUMP (and its two delay-slot instructions) run when the counting starts
    sites = {0x1c1d11, 0x1c1d14, 0x1c1d15, 0x1c1643, 0x1c1646, 0x1c1647}
    stock_rev = (runs[("DT01", "rev")] | init["DT01"]) - sites
    shared = stock_rev & runs[("DT01", "notrev")]          # stock code the reverb shares with the rest of the DSP
    own = stock_rev - shared                               # the stock reverb's own instructions
    sp_call = sorted(pc for pc in runs[("SP01", "rev")] - sites if not is_ours(pc))
    sp_init = sorted(pc for pc in init["SP01"] - sites if not is_ours(pc))
    sp_own = sorted(own & (runs[("SP01", "all")] | init["SP01"]))
    return "exec", {
        "blocks": nb,
        "stock reverb (DT01): instructions run in its call and its init": len(stock_rev),
        "... shared with other stock code (run outside the reverb's calls too)": len(shared),
        "... its own": len(own),
        "... the entry 0xb836bc and the init 0xb83dc1 among its own": [0xb836bc in own, 0xb83dc1 in own],
        "SP01: instructions run in the reverb's call that are not ours (the call site aside)": len(sp_call),
        "SP01: ... in the call, listed": ["%#x" % x for x in sp_call[:20]],
        "SP01: instructions run in the reverb init's call that are not ours (the call site aside)": len(sp_init),
        "SP01: ... in the init, listed": ["%#x" % x for x in sp_init[:20]],
        "SP01: our instructions run in the reverb's call": len(runs[("SP01", "rev")] - sites),
        "SP01: the stock reverb's own instructions run anywhere (init and blocks)": len(sp_own),
        "SP01: ... the stock reverb's own, listed": ["%#x" % x for x in sp_own[:20]],
        "SP01: stock reverb instructions it shares with other stock code, run": len(shared & runs[("SP01", "all")]),
    }


# ---------------------------------------------------------------- (stores) the reverb call's stores

def job_stores(args):
    nb = args
    setup()
    SC = G["SC"]
    ctl = SC.Control(dict(SC.recipes(1.0)["R1"]))
    run = Run("SP01", dict(ctl.words, REV_VOL=110), 96)
    src = source("mixed", nb, 51)
    log = []
    outside, total = [], 0
    allowed = [(SPG, SPG + SPG_BYTES), (VR.STACK_LO, VR.STACK_TOP), SAVE, (REV_OUT[0], REV_OUT[0] + 0x80),
               (REV_OUT[1], REV_OUT[1] + 0x80)]
    for b in range(nb):
        ctl.step(b, run.r.d.frame)
        log.clear()
        run.block(*src[b], b, watch=log)
        total += len(log)
        for pc, ad, va in log:
            if not any(lo <= ad < hi for lo, hi in allowed):
                outside.append(("%#x" % ad, "%#x" % pc))
    # the effect init (fresh): every store into the stock reverb's DDR region comes from our code (spg_init)
    stream, info, cache = load("SP01")
    from vm import VM, watch as W, watch_log
    vm = VM()
    vm.load_dn2()
    VR.load_stream(vm, stream)
    W(vm, REV_REGION[0], REV_REGION[1])
    vm.call(VR.FXINIT, max_insns=10**9)
    wl = watch_log(vm)
    W(vm, 0, 0)
    ours = []
    for p in info["placed"]:
        if p["kind"] == "sw_code":
            lo = int(p["sw"], 16)
            ours.append((lo, lo + p["bytes"] // 2))
    init_not_ours = [("%#x" % ad, "%#x" % pc) for pc, ad, va in wl if not any(lo <= pc < hi for lo, hi in ours)]
    return "stores", {"blocks": nb, "stores": total, "outside": len(outside), "first_outside": outside[:12],
                      "allowed": ["%#x-%#x" % r for r in allowed],
                      "effect_init_stores_into_the_reverb_region": len(wl),
                      "... not from our code": len(init_not_ours), "... first": init_not_ours[:8],
                      "... the highest address stored": "%#x" % max((ad for pc, ad, va in wl), default=0)}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", default="mac,native,lock,exec,stores")
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--quick", action="store_true")
    a = ap.parse_args()
    only = a.only.split(",")
    WORK.mkdir(parents=True, exist_ok=True)
    jobs = []
    if "mac" in only:
        fxrender()
        nb = 300 if a.quick else 1500
        jobs += [(job_mac, (c, k, nb)) for c, k in MAC_CASES.items()]
    if "native" in only:
        native_lib()
        nb = 600 if a.quick else 3000
        jobs += [(job_native, (k, nb)) for k in ("ramp", "mode", "jumps", "hands")]
    if "lock" in only:
        jobs += [(job_lock, (c, cfg)) for c, cfg in lock_cases(a.quick).items()]
    if "exec" in only:
        jobs.append((job_exec, 150 if a.quick else 600))
    if "stores" in only:
        jobs.append((job_stores, 100 if a.quick else 300))
    # warm the effect-init snapshots once, before the pool (each worker would make them otherwise)
    setup()
    for n in IMAGES:
        stream, info, cache = load(n)
        VR.candidate_vm(stream, cache=cache)
    res = {"images": {n: load(n)[1]["section7"]["sha256"] for n in IMAGES}, "mac": {}, "native": {}, "lock": {}}
    t0 = time.time()
    with ProcessPoolExecutor(a.workers, max_tasks_per_child=1) as ex:
        futs = {ex.submit(fn, arg): fn.__name__ for fn, arg in jobs}
        for f in as_completed(futs):
            kind = futs[f][4:]
            try:
                name, r = f.result()
            except Exception as e:
                name, r = "error %s" % kind, {"error": repr(e)[:800]}
            if kind in ("mac", "native", "lock"):
                res[kind][name] = r
            else:
                res[name] = r
            print(time.strftime("%H:%M:%S"), kind, name, json.dumps(r)[:600], flush=True)
    res["wall_s"] = round(time.time() - t0)
    name = "verify.json" if a.only == "mac,native,lock,exec,stores" and not a.quick else \
        "verify-%s%s.json" % (a.only.replace(",", "-"), "-quick" if a.quick else "")
    (OUT / name).write_text(json.dumps(res, indent=1))
    print("wrote", OUT / name)


if __name__ == "__main__":
    main()
