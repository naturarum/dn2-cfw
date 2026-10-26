#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""T5.3 proof: SE01's DSP = FTD1 with the tape echo (fx/spaceecho.c + src/se_glue.c) in the delay slot.

    python3 tools/dspsplice/verify_se.py [--seconds 8] [--in out/T3.11/dry/pluck.wav]

As verify_ftd.py, in tools/sharcvm, with the one-page layout's knobs (results in
out/T5.3/SE01-dsp/verify.json, WAVs next to it):
  1. init: both slots carry their magic after 0x1c1445; the stock chorus init still ran;
  2. where the AGE word lives: frame word 0xa18 (k 40) is the low half of DM 0x25cea4 at the delay
     call (the high half is word 0xa1a), and X 0..6 arrives at the parameter struct's +4 as 256 x;
  3. both slots' inputs (the send buses) are bit-identical to stock's;
  4. each slot's output against a Mac build of the same C (clang -O2, no FMA) fed the same input and,
     block by block, the same parameter floats, frame word 0xa18 and tempo the SHARC code saw; the
     tape echo also against fx/'s renderer at the integer knobs; four delay configurations
     (the main one; an existing kit's values, clean; the RND side; every frame word moving);
  5. the glue's laws on the Mac build: AGE 0 and 1 the same (clean), WID 63/64/65 mono, the ends;
  6. sends at 0: the whole handler output is bit-identical to stock's;
  7. instructions per block (stock, FTD1, SE01 on the same input and frame), stack depth;
     registers for the delay hook and the delay init hook; a fuzz run with every knob jumping;
  8. the tape itself: every sample within the saturator's bound (|x| <= 1), and equal to the Mac
     build's tape after the same blocks (a stray store into the 4 MB tape would show here even
     where no head has read it yet);
  9. a held runaway (T5.3 review): MODE 6 (1+2+3), INT 127, BW 127, AGE 0, at ALT 127 and RND 127,
     1 s of a 0.9 sine at IN, IN_DEL and DEL_VOL 127, then 3 s of silence: the wet output stays
     at or under the ceiling 0.7 (spaceecho.c SE_LIM_C) to the end while the loop still runs
     away, against the Mac build; the stock delay's own runaway on the same input for reference.
"""
import argparse
import ctypes as C
import json
import math
import random
import struct
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(HERE))
import vmrun as V  # noqa: E402
from fx import write_wav, read_wav  # noqa: E402
import verify_cg01 as CG  # noqa: E402
import verify_flg as FL  # noqa: E402

OUT = ROOT / "out/T5.3/SE01-dsp"
FTD1 = ROOT / "out/T3.8/FTD1"
SE_SLOT, SE_MAGIC = 0x8005912c, 0x53454531
TD_MAGIC = 0x54444531
DPARAMS = 0x257934
K40_FRAME, K40_DM = 0xa18, 0x25cea4          # frame word k 40 and where the render's copy puts it
TEMPO_DM = 0x257e80                          # io +0x14
BPM = 120.0
CEILING = 0.7000001                          # spaceecho.c SE_LIM_C, plus the float rounding of the knee's top

# the main configuration: three heads, ALT at about half width, a band, AGE 64 ("vintage")
DKNOBS = dict(DEL_TIME=23, DEL_X=6, DEL_WID=32, DEL_FDBK=50, DEL_HPF=64, DEL_LPF=64)
AGE_MAIN = 64
WORDS = dict(IN_L=20, IN_R=20, IN_L_PAN=0, IN_R_PAN=127, IN_CHR_L=100, IN_CHR_R=100, IN_DEL_L=100, IN_DEL_R=100,
             IN_REV_L=0, IN_REV_R=0, CHR_DEL=0, CHR_REV=0, CHR_VOL=90, DEL_REV=0, DEL_VOL=100, REV_VOL=0,
             CMP_THR=127, CMP_RAT=0, CMP_MIX=0, PATTERN_VOL=127, **FL.KNOBS, **DKNOBS)
# shorter extra configurations of the delay knobs (2 s each)
CONFIGS = {
    "existing_kit_clean": dict(knobs=dict(DEL_TIME=23, DEL_X=0, DEL_WID=64, DEL_FDBK=60, DEL_HPF=22, DEL_LPF=127),
                               age=1, what="what a stock kit holds: X 0 (head 1), WID 64 (mono), LPF 127 "
                                           "(no filter), k 40 = 1 (AGE clean)"),
    "rnd_side": dict(knobs=dict(DEL_TIME=15, DEL_X=3, DEL_WID=110, DEL_FDBK=70, DEL_HPF=90, DEL_LPF=20),
                     age=127, what="heads 1+2, RND at width 92, a narrow resonant band, AGE 127"),
}


def f32(x):
    return struct.unpack("<f", struct.pack("<f", x))[0]


def glue_knobs(dk, age_v):
    """the tape echo knobs se_glue.c applies for integer frame values (float32 as the C computes them)"""
    d = float(dk["DEL_WID"]) - 64.0
    pan, wid = 0, 0.0
    if d < -1.0:
        pan, wid = 1, f32(f32(-1.0 - d) * f32(2.0158730))
    if d > 1.0:
        pan, wid = 2, f32(f32(d - 1.0) * f32(2.0483871))
    wid = min(wid, 127.0)
    age = 0.0 if age_v <= 1 else float(age_v)
    return dict(TIME=dk["DEL_TIME"], MODE=dk["DEL_X"], INT=dk["DEL_FDBK"], AGE=age, CTR=dk["DEL_HPF"],
                BW=dk["DEL_LPF"], WID=wid, PAN=pan)


class Run:
    """the whole block handler, with per-block captures at the delay call: the slots' buffers, the
    delay parameter floats, the 32-bit word at 0x25cea4 and the tempo"""

    def __init__(self, vm, words, k40=0, tempo=BPM):
        self.r = V.Runner(vm, words, tempo=tempo)
        self.vm = vm
        self.set_k40(k40)
        self.params, self.k40w, self.bpm = [], [], []

    def set_k40(self, raw):
        struct.pack_into("<H", self.r.d.frame, K40_FRAME, raw & 0xffff)

    def block(self, L, R, paint=False):
        vm, r = self.vm, self.r
        cap = r.cap
        if paint:
            i7 = vm.reg("I7")
            vm.write(V.STACK_LO, V.PAINT.to_bytes(4, "little") * ((i7 - V.STACK_LO) // 4))
        spi = vm.rd32(V.fullblock.SPI_HALF)
        vm.write(V.fullblock.RX_BASE + 0x1000 * spi, bytes(r.d.frame))
        h = vm.rd32(V.fullblock.DMA_HALF)
        vm.write(V.fullblock.CODEC_IN + 0x100 * h,
                 b"".join(V.fullblock.q31(v).to_bytes(4, "little") for pair in zip(L, R) for v in pair))

        def grab(key, pair):
            def f():
                cap[key][0].extend(vm.rdf(pair[0], 32))
                cap[key][1].extend(vm.rdf(pair[1], 32))
            return f

        def at_delay():
            grab("del_in", V.DEL_IN)()
            self.params.append(vm.rdf(DPARAMS, 6))
            self.k40w.append(vm.rd32(K40_DM))
            self.bpm.append(vm.rdf(TEMPO_DM, 1)[0])
        probes = [(V.CHR_CALL, grab("chr_in", V.CHR_IN)), (V.CHR_RET, grab("chr_out", V.CHR_OUT)),
                  (V.DEL_CALL, at_delay), (V.DEL_RET, grab("del_out", V.DEL_OUT))]
        n0 = vm.icount
        r._call_probed(probes)
        r.per_block.append(vm.icount - n0)
        if paint:
            i7 = vm.reg("I7")
            raw = vm.read(V.STACK_LO, i7 - V.STACK_LO)
            word = V.PAINT.to_bytes(4, "little")
            low = next((V.STACK_LO + k for k in range(0, len(raw), 4) if raw[k:k + 4] != word), i7)
            r.stack_depth = max(r.stack_depth, i7 - low)
        return vm.rdf(V.fullblock.OUT_FLOATS, 32), vm.rdf(V.fullblock.OUT_FLOATS + 0x80, 32)

    def process(self, L, R, paint=False, each=None):
        oL, oR = [], []
        for b, i in enumerate(range(0, len(L) // 32 * 32, 32)):
            if each:
                each(b, self)
            a, c = self.block(L[i:i + 32], R[i:i + 32], paint=paint)
            oL += a
            oR += c
        return oL, oR


def native_se(bdir):
    lib = bdir / "libsenative.dylib"
    subprocess.check_call(["clang", "-O2", "-ffp-contract=off", "-fno-strict-aliasing", "-shared", "-fPIC",
                           "-DSE_NATIVE", f"-I{ROOT / 'fx'}", f"-I{ROOT / 'tools/dspsplice/src'}", "-o", str(lib),
                           str(ROOT / "fx/spaceecho.c"), str(ROOT / "tools/dspsplice/src/se_glue.c")])
    lib = C.CDLL(str(lib))
    lib.se_native_last.restype = C.c_float
    lib.se_native_last.argtypes = [C.c_int]
    return lib


def native_se_render(lib, params, k40w, bpms, din):
    """se_init(), then se_process per 32-sample block with that block's parameter floats, frame word
    and tempo as the SHARC code saw them"""
    lib.se_init()
    k40 = C.c_int32.in_dll(lib, "se_native_k40")
    p = (C.c_float * 7)()
    inL, inR, oL, oR = ((C.c_float * 32)() for _ in range(4))
    ins = (C.POINTER(C.c_float) * 2)(C.cast(inL, C.POINTER(C.c_float)), C.cast(inR, C.POINTER(C.c_float)))
    outs = (C.POINTER(C.c_float) * 2)(C.cast(oL, C.POINTER(C.c_float)), C.cast(oR, C.POINTER(C.c_float)))
    io = FL.FxIo(32, ins, 0, outs, 0, BPM)
    L, R = [], []
    for b, i in enumerate(range(0, len(din[0]), 32)):
        p[:] = list(params[b]) + [1.0]
        k40.value = struct.unpack("<i", struct.pack("<I", k40w[b]))[0]
        io.bpm = bpms[b]
        inL[:] = din[0][i:i + 32]
        inR[:] = din[1][i:i + 32]
        lib.se_process(p, C.byref(io))
        L += list(oL)
        R += list(oR)
    return L, R


def fxrender(bin_, inp, out, knobs):
    args = [str(bin_), "-fx", "spaceecho", "-i", str(inp), "-o", str(out), "-mix", "wet", "-bits", "32",
            "-bpm", "120"]
    for k, v in knobs.items():
        args += ["-p", f"{k}={v!r}"]
    subprocess.check_call(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return FL.read_f32(out)


RING_OFF, RING_FLOATS = 48, 2 * 512000          # SeSlot: 12-byte header + last[9], then SpaceEcho.ring


def vm_ring(vm):
    raw = vm.read(SE_SLOT + RING_OFF, 4 * RING_FLOATS)
    return struct.unpack("<%df" % RING_FLOATS, raw)


def native_ring(lib):
    lib.se_native_slot_ptr.restype = C.c_void_p
    raw = C.string_at(lib.se_native_slot_ptr() + RING_OFF, 4 * RING_FLOATS)
    return struct.unpack("<%df" % RING_FLOATS, raw)


def ring_check(vm, lib=None):
    """the tape: every sample comes out of the saturator, so |x| <= 1 (a stray store of a knob
    value would break that); with lib, also against the Mac build's tape after the same blocks"""
    r = vm_ring(vm)
    out = {"bad": sum(1 for x in r if not (abs(x) <= 1.0)), "peak": max(abs(x) for x in r if x == x)}
    if lib is not None:
        m = native_ring(lib)
        d = [abs(x - y) for x, y in zip(r, m)]
        out["vs_mac_identical"] = sum(1 for x, y in zip(r, m) if x == y)
        out["vs_mac_max_diff"] = max(d)
    return out


def nan_count(xs):
    return sum(1 for v in xs if v != v or math.isinf(v))


def law_checks(lib):
    """the glue's knob laws on the Mac build (parameter floats as the unpack makes them)"""
    F15 = struct.unpack("<f", struct.pack("<I", 0x38010204))[0]

    def p_of(time_v=23, x=0, wid=64, fdbk=50, hpf=64, lpf=127, frac=0):
        return [f32(f32((time_v << 8) + frac) * F15), float(x << 8), (wid << 8) * 2.0 ** -15,
                (fdbk << 8) * 2.0 ** -15, f32(f32(hpf << 8) * F15), f32(f32(lpf << 8) * F15)]

    def one(p, k40, n=96, seed=3):
        lib.se_init()
        C.c_int32.in_dll(lib, "se_native_k40").value = k40
        rnd = random.Random(seed)
        pp = (C.c_float * 7)(*p, 1.0)
        inL, inR, oL, oR = ((C.c_float * 32)() for _ in range(4))
        ins = (C.POINTER(C.c_float) * 2)(C.cast(inL, C.POINTER(C.c_float)), C.cast(inR, C.POINTER(C.c_float)))
        outs = (C.POINTER(C.c_float) * 2)(C.cast(oL, C.POINTER(C.c_float)), C.cast(oR, C.POINTER(C.c_float)))
        io = FL.FxIo(32, ins, 0, outs, 0, BPM)
        L, R = [], []
        for b in range(n * 50):
            inL[:] = [rnd.uniform(-0.3, 0.3) if b < 40 else 0.0 for _ in range(32)]
            inR[:] = [rnd.uniform(-0.3, 0.3) if b < 40 else 0.0 for _ in range(32)]
            lib.se_process(pp, C.byref(io))
            L += list(oL)
            R += list(oR)
        last = [lib.se_native_last(k) for k in range(9)]
        return (L, R), last

    res = {}
    # every integer knob through the /32512 path lands exactly on the integer (the snap)
    exact = []
    for v in range(128):
        _, last = one(p_of(time_v=v, hpf=v, lpf=v), 0, n=1)
        exact.append(last[0] == v and last[5] == v and last[6] == v)
    res["TIME_CTR_BW_integers_exact"] = sum(exact)
    _, last = one(p_of(time_v=40, frac=0x80), 0, n=1)
    res["TIME_fraction_kept"] = last[0]
    a0, l0 = one(p_of(), 0x0000)
    a1, l1 = one(p_of(), 0x0100)
    res["AGE_k40_0_vs_1_bit_identical"] = a0 == a1
    res["AGE_applied_for_k40_1"] = l1[7]
    _, l = one(p_of(), 0x0180, n=1)
    res["AGE_applied_for_k40_1.5"] = l[7]
    _, l = one(p_of(), 0x4000, n=1)
    res["AGE_applied_for_k40_64"] = l[7]
    _, l = one(p_of(), 0x7f00, n=1)
    res["AGE_applied_for_k40_127"] = l[7]
    w = {}
    for v in (0, 1, 32, 62, 63, 64, 65, 66, 96, 126, 127):
        out, last = one(p_of(wid=v), 0x100, n=40 if v in (63, 64, 65) else 1)
        w[v] = {"WIDTH": last[2], "PAN": ["MONO", "ALT", "RND"][int(last[3])]}
        if v in (63, 64, 65):
            w[v]["out"] = out
    res["WID_fold"] = {k: {kk: vv for kk, vv in d.items() if kk != "out"} for k, d in w.items()}
    res["WID_63_64_65_bit_identical"] = w[63]["out"] == w[64]["out"] == w[65]["out"]
    res["WID_64_output_mono_L_equals_R"] = w[64]["out"][0] == w[64]["out"][1]
    res["MODE_applied"] = {x: one(p_of(x=x), 0x100, n=1)[1][1] for x in range(7)}
    res["INT_applied_FDBK_0_64_127"] = [one(p_of(fdbk=v), 0x100, n=1)[1][4] for v in (0, 64, 127)]
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=8.0)
    ap.add_argument("--config-seconds", type=float, default=2.0)
    ap.add_argument("--in", dest="inp", default=str(ROOT / "out/T3.11/dry/pluck.wav"))
    ap.add_argument("--fuzz-blocks", type=int, default=1500)
    a = ap.parse_args()
    t_start = time.time()
    info = json.loads((OUT / "build.json").read_text())
    stream = (OUT / "section7.bin").read_bytes()
    ftd_info = json.loads((FTD1 / "build.json").read_text())
    ftd_stream = (FTD1 / "section7.bin").read_bytes()
    cache = "t53-SE01-" + info["section7"]["sha256"][:12]
    ftd_cache = "t38-FTD1-" + ftd_info["section7"]["sha256"][:12]
    n = int(a.seconds * 48000) // 32 * 32
    L, R, sr = read_wav(a.inp)
    assert sr == 48000
    L, R = (L + [0.0] * n)[:n], (R + [0.0] * n)[:n]
    res = {"input": a.inp, "seconds": a.seconds, "chorus_knobs": FL.KNOBS, "delay_knobs": DKNOBS,
           "k40_age": AGE_MAIN, "space_echo_knobs": glue_knobs(DKNOBS, AGE_MAIN), "flanger_fixed": FL.FIXED,
           "section7_sha256": info["section7"]["sha256"]}

    # 1. init
    vm_st = V.candidate_vm(None)
    vm_x = V.candidate_vm(stream, cache=cache)
    res["init_flanger_magic_ok"] = vm_x.rd32(FL.SLOT) == FL.MAGIC
    res["init_space_echo_magic_ok"] = vm_x.rd32(SE_SLOT) == SE_MAGIC
    res["stock_chorus_state_same_after_init"] = (vm_st.read(FL.CHORUS_STATE, FL.CHORUS_STATE_BYTES)
                                                 == vm_x.read(FL.CHORUS_STATE, FL.CHORUS_STATE_BYTES))

    # 2. where k 40 and X land (stock image, so the evidence is about the stock unpack and copies)
    vm = V.candidate_vm(None)
    rr = Run(vm, dict(WORDS, REV_PRE=55))
    z = [0.0] * 32
    xs, k40s = {}, []
    for x in range(7):
        rr.r.set(DEL_X=x)
        for raw in (0x0000, 0x0100, 0x4000 + 0x11 * x, 0x7f00):
            rr.set_k40(raw)
            rr.block(z, z)
            w = rr.k40w[-1]
            k40s.append({"frame_0xa18": raw, "low_half": w & 0xffff, "high_half": w >> 16,
                         "same_block": (w & 0xffff) == raw})
        rr.block(z, z)
        xs[x] = rr.params[-1][1]
    res["k40_at_0x25cea4"] = {"all_low_half_equal_frame_word_same_block": all(k["same_block"] for k in k40s),
                              "high_half_is_word_0xa1a_REV_PRE_55": all(k["high_half"] == 55 << 8 for k in k40s),
                              "samples": k40s[:6]}
    res["X_at_param_plus4"] = xs

    # 3-4. the main run: stock, FTD1, SE01 on the same input and frame
    runs = {}
    for name, vmx in (("stock", vm_st), ("FTD1", V.candidate_vm(ftd_stream, cache=ftd_cache)), ("SE01", vm_x)):
        t0 = time.time()
        rx = Run(vmx, WORDS, k40=AGE_MAIN << 8)
        o = rx.process(L, R, paint=True)
        runs[name] = (rx, o)
        print(f"  {name}: {len(rx.r.per_block)} blocks in {time.time() - t0:.0f} s", flush=True)
    rs, oS = runs["stock"]
    rf, oF = runs["FTD1"]
    rx, oX = runs["SE01"]
    res["chorus_input_identical"] = rs.r.cap["chr_in"] == rx.r.cap["chr_in"]
    res["delay_input_identical"] = rs.r.cap["del_in"] == rx.r.cap["del_in"]
    res["delay_params_floats_last_block"] = rx.params[-1]
    res["k40_word_seen"] = "%#010x" % rx.k40w[-1]
    res["tempo_float"] = rx.bpm[-1]
    bdir = OUT / "build"
    lib_f = FL.native_lib(bdir)
    cparams = vm_x.rdf(FL.PARAMS, 4)
    res["flanger_vs_mac_glue"] = FL.compare(FL.native_render(lib_f, cparams, rx.r.cap["chr_in"]), rx.r.cap["chr_out"])
    lib = native_se(bdir)
    res["se_slot_bytes"] = lib.se_slot_size()
    res["se_slot_fits_stock_delay_ddr"] = lib.se_slot_size() <= 4194908
    mac = native_se_render(lib, rx.params, rx.k40w, rx.bpm, rx.r.cap["del_in"])
    res["space_echo_vs_mac_glue"] = FL.compare(mac, rx.r.cap["del_out"])
    res["tape_after_main_run"] = ring_check(vm_x, lib)
    fxr = bdir / "fxrender"
    subprocess.check_call(["clang", "-O2", "-fno-strict-aliasing", "-ffp-contract=off", "-o", str(fxr),
                           str(ROOT / "fx/render.c"), str(ROOT / "fx/wav.c"), str(ROOT / "fx/tapedelay.c"),
                           str(ROOT / "fx/mbcomp.c"), str(ROOT / "fx/flanger.c"), str(ROOT / "fx/spaceecho.c"), "-lm"])
    FL.write_f32(OUT / "delay_slot_input.wav", *rx.r.cap["del_in"])
    ref = fxrender(fxr, OUT / "delay_slot_input.wav", OUT / "mac_fxrender_spaceecho.wav", glue_knobs(DKNOBS, AGE_MAIN))
    res["space_echo_vs_fx_fxrender"] = FL.compare(ref, rx.r.cap["del_out"])
    res["mac_glue_vs_fx_fxrender"] = FL.compare(ref, mac)
    res["delay_slot_output_peak"] = {"stock": round(V.peak(rs.r.cap["del_out"][0] + rs.r.cap["del_out"][1]), 4),
                                     "FTD1": round(V.peak(rf.r.cap["del_out"][0] + rf.r.cap["del_out"][1]), 4),
                                     "SE01": round(V.peak(rx.r.cap["del_out"][0] + rx.r.cap["del_out"][1]), 4)}
    res["output_peak"] = {k: round(V.peak(o[0] + o[1]), 4) for k, (_, o) in runs.items()}
    write_wav(OUT / "vm_input.wav", L, R)
    write_wav(OUT / "vm_stock.wav", *oS)
    write_wav(OUT / "vm_FTD1.wav", *oF)
    write_wav(OUT / "vm_SE01.wav", *oX)
    FL.write_f32(OUT / "vm_SE01_delay_slot_wet.wav", *rx.r.cap["del_out"])
    nb = len(rs.r.per_block)
    ins = {}
    for k, (r_, _) in runs.items():
        pb = r_.r.per_block
        ins[k] = {"mean": round(sum(pb) / nb), "max": max(pb[1:]), "stack_depth_bytes": r_.r.stack_depth}
    ins["SE01_minus_stock_mean"] = ins["SE01"]["mean"] - ins["stock"]["mean"]
    ins["SE01_minus_FTD1_mean"] = ins["SE01"]["mean"] - ins["FTD1"]["mean"]
    ins["SE01_minus_stock_max"] = ins["SE01"]["max"] - ins["stock"]["max"]
    res["insn_per_block"] = ins
    print("  main run done", flush=True)

    # 4b. the extra configurations, and one with every frame word moving
    m = int(a.config_seconds * 48000) // 32 * 32
    cfg_res = {}
    for name, c in CONFIGS.items():
        vmc = V.candidate_vm(stream, cache=cache)
        rc = Run(vmc, dict(WORDS, **c["knobs"]), k40=c["age"] << 8)
        rc.process(L[:m], R[:m])
        macc = native_se_render(lib, rc.params, rc.k40w, rc.bpm, rc.r.cap["del_in"])
        tape = ring_check(vmc, lib)
        FL.write_f32(OUT / f"cfg_{name}_input.wav", *rc.r.cap["del_in"])
        refc = fxrender(fxr, OUT / f"cfg_{name}_input.wav", OUT / f"cfg_{name}_fxrender.wav",
                        glue_knobs(c["knobs"], c["age"]))
        pb = rc.r.per_block
        cfg_res[name] = {"what": c["what"], "knobs": c["knobs"], "k40": c["age"],
                         "space_echo_knobs": glue_knobs(c["knobs"], c["age"]),
                         "vs_mac_glue": FL.compare(macc, rc.r.cap["del_out"]),
                         "vs_fx_fxrender": FL.compare(refc, rc.r.cap["del_out"]),
                         "tape": tape,
                         "delay_out_peak": round(V.peak(rc.r.cap["del_out"][0] + rc.r.cap["del_out"][1]), 4),
                         "insn_per_block_mean": round(sum(pb) / len(pb)), "insn_per_block_max": max(pb[1:])}
        (OUT / f"cfg_{name}_input.wav").unlink()
    # every word moving: frame words glide like the ColdFire's smoother (fractional low bytes), WID
    # sweeps across both sides, X steps through 0..6, k 40 sweeps up and down
    vmc = V.candidate_vm(stream, cache=cache)
    rc = Run(vmc, WORDS, k40=0)
    nbm = m // 32

    def moving(b, run):
        ph = b / nbm
        fr = run.r.d.frame

        def put(name, val):
            struct.pack_into("<H", fr, V.fullblock.FRAME_WORDS[name], int(val) & 0xffff)
        put("DEL_TIME", 0x0f00 + int(0x1000 * ph))
        put("DEL_X", min(6, int(7 * ph)) << 8)
        put("DEL_WID", int(0x7f00 * ph))
        put("DEL_FDBK", 0x3000 + int(0x3000 * math.sin(math.pi * ph)))
        put("DEL_HPF", 0x2000 + int(0x5000 * ph))
        put("DEL_LPF", 0x7f00 - int(0x6000 * math.sin(math.pi * ph)))
        run.set_k40(int(0x7f00 * math.sin(math.pi * ph)))
    rc.process(L[:m], R[:m], each=moving)
    macc = native_se_render(lib, rc.params, rc.k40w, rc.bpm, rc.r.cap["del_in"])
    tape = ring_check(vmc, lib)
    pb = rc.r.per_block
    cfg_res["all_words_moving"] = {"tape": tape, "what": "every delay word and k 40 moving every block with fractional low "
                                           "bytes; WID 0 -> 127 (ALT -> MONO -> RND); X 0 -> 6",
                                   "vs_mac_glue": FL.compare(macc, rc.r.cap["del_out"]),
                                   "delay_out_peak": round(V.peak(rc.r.cap["del_out"][0] + rc.r.cap["del_out"][1]), 4),
                                   "insn_per_block_mean": round(sum(pb) / len(pb)), "insn_per_block_max": max(pb[1:])}
    res["configs"] = cfg_res
    print("  configs done", flush=True)

    # 9. held runaways (T5.3 review): three heads at INT 127 (3 x 110 %), held by the tape's saturator at +-1,
    # used to give a wet of 3 sqrt 2 = 4.24 at ALT 127; now the wet ceiling holds it at 0.7
    hold = 48000 * 4 // 32 * 32
    tone = [0.9 * math.sin(2 * math.pi * 220 * i / 48000) for i in range(48000)] + [0.0] * (hold - 48000)
    loud = dict(WORDS, IN_L=127, IN_R=127, IN_DEL_L=127, IN_DEL_R=127, DEL_VOL=127, DEL_REV=0, REV_VOL=0,
                CHR_VOL=0, IN_CHR_L=0, IN_CHR_R=0, CMP_MIX=0)
    runaway = {}
    for name, (which, knobs) in {
            "SE01 MODE 6, INT 127, WID 0 (ALT 127)": ("se", dict(DEL_TIME=10, DEL_X=6, DEL_WID=0, DEL_FDBK=127,
                                                                 DEL_HPF=64, DEL_LPF=127)),
            "SE01 MODE 6, INT 127, WID 127 (RND 127)": ("se", dict(DEL_TIME=10, DEL_X=6, DEL_WID=127, DEL_FDBK=127,
                                                                   DEL_HPF=64, DEL_LPF=127)),
            "stock X 1, FDBK 127, WID 0": ("stock", dict(DEL_TIME=10, DEL_X=1, DEL_WID=0, DEL_FDBK=127, DEL_HPF=0,
                                                         DEL_LPF=127))}.items():
        vmh = V.candidate_vm(stream, cache=cache) if which == "se" else V.candidate_vm(None)
        rh = Run(vmh, dict(loud, **knobs), k40=0)
        rh.process(tone, tone)
        wet, din = rh.r.cap["del_out"], rh.r.cap["del_in"]
        e = {"slot_input_peak": round(V.peak(din[0] + din[1]), 4), "wet_peak": V.peak(wet[0] + wet[1]),
             "wet_peak_last_second": V.peak(wet[0][-48000:] + wet[1][-48000:]),
             "insn_per_block_max": max(rh.r.per_block[1:])}
        if which == "se":
            e["vs_mac_glue"] = FL.compare(native_se_render(lib, rh.params, rh.k40w, rh.bpm, din), wet)
            e["tape"] = ring_check(vmh, lib)
        runaway[name] = e
        print(f"  {name}: {e}", flush=True)
    res["held_runaway"] = runaway
    res["held_runaway_pre_review"] = {"SE01 ALT 127": 4.2426, "SE01 RND 127": 3.8261, "stock": 0.5263,
                                      "source": "out/T5.3/review-dsp/runaway.json"}

    # 5. the glue's laws (Mac build)
    res["laws"] = law_checks(lib)

    # 6. sends at 0: everything else identical (AGE 64 and the main knobs)
    w0 = dict(WORDS, IN_CHR_L=0, IN_CHR_R=0, IN_DEL_L=0, IN_DEL_R=0)
    mm = 48000 // 32 * 32
    o0s = Run(V.candidate_vm(None), w0, k40=AGE_MAIN << 8).process(L[:mm], R[:mm])
    o0x = Run(V.candidate_vm(stream, cache=cache), w0, k40=AGE_MAIN << 8).process(L[:mm], R[:mm])
    res["muted_send_output_identical"] = o0s == o0x

    # 7a. registers: the delay hooks vs the stock delay and its init called on their own
    vm = V.candidate_vm(stream, cache=cache)
    Run(vm, WORDS, k40=AGE_MAIN << 8).process(L[:320], R[:320])
    vm.wr32(0x257e6c, 32)
    vm.wr32(0x257e70, 0x25d6e8)
    vm.wr32(0x257e78, 0x25d6d8)
    args = {"R4": SE_SLOT, "R8": DPARAMS, "R12": 0x257e6c}
    regs = {}
    for label, entry in (("hook stub fxh_delay", int(info["symbols"]["fxh_delay"], 16)),
                         ("stock delay 0xb8482c alone", 0xb8482c),
                         ("hook stub fxh_delay_init", int(info["symbols"]["fxh_delay_init"], 16)),
                         ("stock delay init 0xb848cb alone", 0xb848cb)):
        ch, m1 = set(), False
        for seed in range(4):
            c, mmm = CG.reg_test(vm, entry, args, seed)
            ch |= set(c)
            m1 |= mmm
        regs[label] = {"changed": sorted(ch, key=CG.SAVED.index), "MODE1_changed": m1}
    res["registers"] = regs
    allowed = set(regs["stock delay 0xb8482c alone"]["changed"]) | {"R2", "I12"}
    res["delay_stub_changes_beyond_stock"] = sorted(set(regs["hook stub fxh_delay"]["changed"]) - allowed)
    allowed = set(regs["stock delay init 0xb848cb alone"]["changed"]) | {"R2", "I12"}
    res["delay_init_stub_changes_beyond_stock"] = sorted(set(regs["hook stub fxh_delay_init"]["changed"]) - allowed)

    # 7b. fuzz: full-scale noise into both slots, every knob jumping, X 0..6, WID both sides, k 40 jumping,
    # sometimes fractional frame words (the smoother's values on the way)
    vm = V.candidate_vm(stream, cache=cache)
    rz = Run(vm, dict(WORDS, IN_L=127, IN_R=127, CHR_VOL=127, CHR_DEL=127, CHR_REV=127, DEL_VOL=127,
                      DEL_REV=127, REV_VOL=100), k40=AGE_MAIN << 8)
    rnd = random.Random(5)
    bad, pk = 0, {"chr": 0.0, "del": 0.0, "out": 0.0}
    knames = list(FL.KNOBS) + ["DEL_TIME", "DEL_WID", "DEL_FDBK", "DEL_HPF", "DEL_LPF"]
    seen = {"X": set(), "WID_side": set(), "k40": set()}
    for b in range(a.fuzz_blocks):
        if b % 20 == 0:
            vals = {k: rnd.choice([0, 127, rnd.randrange(128)]) for k in knames}
            vals["DEL_X"] = rnd.randrange(7)
            rz.r.set(**vals)
            if rnd.random() < 0.3:                       # a fractional word somewhere
                k = rnd.choice(knames + ["DEL_X"])
                struct.pack_into("<H", rz.r.d.frame, V.fullblock.FRAME_WORDS[k], rnd.randrange(0x7f01))
            age = rnd.choice([0, 1, 127, rnd.randrange(128)])
            rz.set_k40((age << 8) | (rnd.randrange(256) if rnd.random() < 0.3 else 0))
            seen["X"].add(vals["DEL_X"])
            seen["WID_side"].add("ALT" if vals["DEL_WID"] < 63 else "RND" if vals["DEL_WID"] > 65 else "MONO")
            seen["k40"].add(age)
        Lz = [rnd.uniform(-1, 1) for _ in range(32)]
        Rz = [rnd.uniform(-1, 1) for _ in range(32)]
        oa, ob = rz.block(Lz, Rz, paint=True)
        vals_ = {"chr": rz.r.cap["chr_out"][0][-32:] + rz.r.cap["chr_out"][1][-32:],
                 "del": rz.r.cap["del_out"][0][-32:] + rz.r.cap["del_out"][1][-32:], "out": oa + ob}
        for k, vs in vals_.items():
            bad += nan_count(vs)
            pk[k] = max(pk[k], V.peak(vs))
    st = vm.read(SE_SLOT, 4 * 64)                        # the slot's header and the start of the tape
    fl_state = vm.rdf(SE_SLOT + 12, 9)
    res["fuzz"] = {"blocks": a.fuzz_blocks, "nan_inf": bad, "flanger_peak": round(pk["chr"], 3),
                   "space_echo_peak": round(pk["del"], 3), "space_echo_peak_exact": pk["del"],
                   "output_peak": round(pk["out"], 3),
                   "insn_per_block_max": max(rz.r.per_block), "insn_per_block_mean": round(
                       sum(rz.r.per_block) / len(rz.r.per_block)),
                   "X_values": sorted(seen["X"]), "WID_sides": sorted(seen["WID_side"]), "k40_count": len(seen["k40"]),
                   "glue_last_applied_after": fl_state, "slot_magic_after": "%#x" % struct.unpack("<I", st[:4])[0]}
    res["fuzz"]["nan_in_glue_state"] = nan_count(fl_state)
    res["fuzz"]["tape"] = ring_check(vm)
    res["fuzz"]["stack_depth_bytes"] = rz.r.stack_depth
    res["seconds_taken"] = round(time.time() - t_start)

    (OUT / "verify.json").write_text(json.dumps(res, indent=1))
    for k, v in res.items():
        print(f"{k}: {v}")
    law = res["laws"]
    ok = (res["init_flanger_magic_ok"] and res["init_space_echo_magic_ok"] and res["stock_chorus_state_same_after_init"]
          and res["k40_at_0x25cea4"]["all_low_half_equal_frame_word_same_block"]
          and all(res["X_at_param_plus4"][x] == 256.0 * x for x in range(7))
          and res["chorus_input_identical"] and res["delay_input_identical"]
          and res["flanger_vs_mac_glue"]["bit_identical"] == res["flanger_vs_mac_glue"]["samples"]
          and all((c["vs_mac_glue"]["max_diff_db_re_peak"] is None or c["vs_mac_glue"]["max_diff_db_re_peak"] < -100)
                  for c in list(res["configs"].values()) + [{"vs_mac_glue": res["space_echo_vs_mac_glue"]}])
          and law["TIME_CTR_BW_integers_exact"] == 128 and law["AGE_k40_0_vs_1_bit_identical"]
          and law["WID_63_64_65_bit_identical"] and law["WID_64_output_mono_L_equals_R"]
          and res["muted_send_output_identical"] and not res["delay_stub_changes_beyond_stock"]
          and not res["delay_init_stub_changes_beyond_stock"]
          and not res["fuzz"]["nan_inf"] and not res["fuzz"]["nan_in_glue_state"] and res["se_slot_fits_stock_delay_ddr"]
          and res["tape_after_main_run"]["bad"] == 0 and res["fuzz"]["tape"]["bad"] == 0
          and all(c["tape"]["bad"] == 0 and c["tape"]["vs_mac_max_diff"] < 1e-3 for c in res["configs"].values())
          and all(e["wet_peak"] <= CEILING and e["wet_peak_last_second"] > 0.6 and e["tape"]["bad"] == 0
                  and (e["vs_mac_glue"]["max_diff_db_re_peak"] is None or e["vs_mac_glue"]["max_diff_db_re_peak"] < -100)
                  for k, e in res["held_runaway"].items() if k.startswith("SE01"))
          and res["fuzz"]["space_echo_peak_exact"] <= CEILING)
    print("VERDICT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
