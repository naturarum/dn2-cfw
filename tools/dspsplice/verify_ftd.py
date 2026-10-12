#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Milestone 3 proof (T3.8): FTD1 = stock, with the flanger in the chorus slot and the tape delay in the
delay slot.

    python3 tools/dspsplice/verify_ftd.py [--seconds 8] [--in out/T3.11/dry/pluck.wav]

As verify_flg.py, for both slots at once (results in out/T3.8/FTD1/verify.json):
  1. init: both slots carry their magic after 0x1c1445; the stock chorus init still ran;
  2. both slots' inputs (the send buses) are bit-identical to stock's (chorus->delay send at 0, so the
     delay's input does not depend on the chorus);
  3. each slot's output against a Mac build of the same C (clang -O2, no FMA) fed the same input and
     the same parameter floats and tempo; and against fx/'s renderer at the integer knobs;
  4. sends at 0: the whole handler output is bit-identical to stock's;
  5. instructions per block, stack depth; registers for the delay hook; a fuzz run.
"""
import argparse
import ctypes as C
import json
import math
import random
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(HERE))
import vmrun as V  # noqa: E402
from fx import write_wav, read_wav  # noqa: E402
import verify_cg01 as CG  # noqa: E402
import verify_flg as FL  # noqa: E402

OUT = ROOT / "out/T3.8/FTD1"
DKNOBS = dict(DEL_TIME=23, DEL_X=0, DEL_WID=64, DEL_FDBK=60, DEL_HPF=22, DEL_LPF=102)
WORDS = dict(IN_L=20, IN_R=20, IN_L_PAN=0, IN_R_PAN=127, IN_CHR_L=100, IN_CHR_R=100, IN_DEL_L=100, IN_DEL_R=100,
             IN_REV_L=0, IN_REV_R=0, CHR_DEL=0, CHR_REV=0, CHR_VOL=90, DEL_REV=0, DEL_VOL=100, REV_VOL=0,
             CMP_THR=127, CMP_RAT=0, CMP_MIX=0, PATTERN_VOL=127, **FL.KNOBS, **DKNOBS)
TD_FIXED = dict(RES=32, WOW=20, FLUT=20, AGE=24, DRIV=32)
TD_SLOT, TD_MAGIC = 0x8005912c, 0x54444531
DPARAMS = 0x257934
BPM = 120.0


def native_td(bdir):
    lib = bdir / "libtdnative.dylib"
    subprocess.check_call(["clang", "-O2", "-ffp-contract=off", "-fno-strict-aliasing", "-shared", "-fPIC",
                           "-DTD_NATIVE", f"-I{ROOT / 'fx'}", f"-I{ROOT / 'tools/dspsplice/src'}", "-o", str(lib),
                           str(ROOT / "fx/tapedelay.c"), str(ROOT / "tools/dspsplice/src/td_glue.c")])
    return C.CDLL(str(lib))


def native_td_render(lib, params, bpm, din):
    lib.td_init()
    p = (C.c_float * 7)(*params, 1.0)
    inL, inR, oL, oR = ((C.c_float * 32)() for _ in range(4))
    ins = (C.POINTER(C.c_float) * 2)(C.cast(inL, C.POINTER(C.c_float)), C.cast(inR, C.POINTER(C.c_float)))
    outs = (C.POINTER(C.c_float) * 2)(C.cast(oL, C.POINTER(C.c_float)), C.cast(oR, C.POINTER(C.c_float)))
    io = FL.FxIo(32, ins, 0, outs, 0, bpm)
    L, R = [], []
    for i in range(0, len(din[0]), 32):
        inL[:] = din[0][i:i + 32]
        inR[:] = din[1][i:i + 32]
        lib.td_process(p, C.byref(io))
        L += list(oL)
        R += list(oR)
    return L, R


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=8.0)
    ap.add_argument("--in", dest="inp", default=str(ROOT / "out/T3.11/dry/pluck.wav"))
    ap.add_argument("--fuzz-blocks", type=int, default=1500)
    a = ap.parse_args()
    info = json.loads((OUT / "build.json").read_text())
    stream = (OUT / "section7.bin").read_bytes()
    cache = "t38-FTD1-" + info["section7"]["sha256"][:12]
    n = int(a.seconds * 48000) // 32 * 32
    L, R, sr = read_wav(a.inp)
    assert sr == 48000
    L, R = (L + [0.0] * n)[:n], (R + [0.0] * n)[:n]
    res = {"input": a.inp, "seconds": a.seconds, "chorus_knobs": FL.KNOBS, "delay_knobs": DKNOBS,
           "delay_fixed": TD_FIXED, "flanger_fixed": FL.FIXED}

    vm_st = V.candidate_vm(None)
    vm_x = V.candidate_vm(stream, cache=cache)
    res["init_flanger_magic_ok"] = vm_x.rd32(FL.SLOT) == FL.MAGIC
    res["init_tape_magic_ok"] = vm_x.rd32(TD_SLOT) == TD_MAGIC
    res["stock_chorus_state_same_after_init"] = (vm_st.read(FL.CHORUS_STATE, FL.CHORUS_STATE_BYTES)
                                                 == vm_x.read(FL.CHORUS_STATE, FL.CHORUS_STATE_BYTES))
    rs = V.Runner(vm_st, WORDS, tempo=BPM)
    oS = rs.process(L, R, paint=True)
    rx = V.Runner(vm_x, WORDS, tempo=BPM)
    oX = rx.process(L, R, paint=True)
    res["chorus_input_identical"] = rs.cap["chr_in"] == rx.cap["chr_in"]
    res["delay_input_identical"] = rs.cap["del_in"] == rx.cap["del_in"]
    cparams = vm_x.rdf(FL.PARAMS, 4)
    dparams = vm_x.rdf(DPARAMS, 6)
    bpm = vm_x.rdf(0x257e80, 1)[0]
    res["delay_params_floats"] = dparams
    res["tempo_float"] = bpm

    lib_f = FL.native_lib(OUT / "build")
    res["flanger_vs_mac_glue"] = FL.compare(FL.native_render(lib_f, cparams, rx.cap["chr_in"]), rx.cap["chr_out"])
    lib_t = native_td(OUT / "build")
    res["td_slot_bytes"] = lib_t.td_slot_size()
    res["tape_vs_mac_glue"] = FL.compare(native_td_render(lib_t, dparams, bpm, rx.cap["del_in"]), rx.cap["del_out"])
    # fx/'s renderer at the integer knobs
    FL.write_f32(OUT / "delay_slot_input.wav", *rx.cap["del_in"])
    fxr = OUT / "build/fxrender"
    subprocess.check_call(["clang", "-O2", "-fno-strict-aliasing", "-ffp-contract=off", "-o", str(fxr),
                           str(ROOT / "fx/render.c"), str(ROOT / "fx/wav.c"), str(ROOT / "fx/tapedelay.c"),
                           str(ROOT / "fx/mbcomp.c"), str(ROOT / "fx/flanger.c"), "-lm"])
    args = [str(fxr), "-fx", "tapedelay", "-i", str(OUT / "delay_slot_input.wav"), "-o", str(OUT / "mac_fxrender_tape.wav"),
            "-mix", "wet", "-bits", "32", "-bpm", "120"]
    knobs = dict(TD_FIXED, TIME=DKNOBS["DEL_TIME"], FDBK=DKNOBS["DEL_FDBK"], HPF=DKNOBS["DEL_HPF"],
                 LPF=DKNOBS["DEL_LPF"], WID=127 if DKNOBS["DEL_X"] else DKNOBS["DEL_WID"])
    for k, v in knobs.items():
        args += ["-p", f"{k}={v}"]
    subprocess.check_call(args, stdout=subprocess.DEVNULL)
    res["tape_vs_fx_fxrender"] = FL.compare(FL.read_f32(OUT / "mac_fxrender_tape.wav"), rx.cap["del_out"])
    res["delay_slot_output_peak"] = round(V.peak(rx.cap["del_out"][0] + rx.cap["del_out"][1]), 4)
    res["stock_delay_output_peak"] = round(V.peak(rs.cap["del_out"][0] + rs.cap["del_out"][1]), 4)
    res["output_peak_stock"] = round(V.peak(oS[0] + oS[1]), 4)
    res["output_peak_FTD1"] = round(V.peak(oX[0] + oX[1]), 4)
    write_wav(OUT / "vm_input.wav", L, R)
    write_wav(OUT / "vm_stock.wav", *oS)
    write_wav(OUT / "vm_FTD1.wav", *oX)
    nb = len(rs.per_block)
    res["insn_per_block_stock"] = round(sum(rs.per_block) / nb)
    res["insn_per_block_FTD1"] = round(sum(rx.per_block) / nb)
    res["insn_per_block_FTD1_max"] = max(rx.per_block[1:])
    res["insn_per_block_delta"] = res["insn_per_block_FTD1"] - res["insn_per_block_stock"]
    res["stack_depth_bytes_stock"] = rs.stack_depth
    res["stack_depth_bytes_FTD1"] = rx.stack_depth

    # sends at 0: everything else identical
    w0 = dict(WORDS, IN_CHR_L=0, IN_CHR_R=0, IN_DEL_L=0, IN_DEL_R=0)
    m = 48000 // 32 * 32
    o0s = V.Runner(V.candidate_vm(None), w0).process(L[:m], R[:m], capture=False)
    o0x = V.Runner(V.candidate_vm(stream, cache=cache), w0).process(L[:m], R[:m], capture=False)
    res["muted_send_output_identical"] = o0s == o0x

    # registers: the delay hook vs the stock delay called on its own
    vm = V.candidate_vm(stream, cache=cache)
    V.Runner(vm, WORDS).process(L[:320], R[:320], capture=False)
    vm.wr32(0x257e6c, 32)
    vm.wr32(0x257e70, 0x25d6e8)
    vm.wr32(0x257e78, 0x25d6d8)
    args = {"R4": TD_SLOT, "R8": DPARAMS, "R12": 0x257e6c}
    regs = {}
    for label, entry in (("hook stub fxh_delay", int(info["symbols"]["fxh_delay"], 16)), ("stock delay 0xb8482c alone", 0xb8482c)):
        ch, m1 = set(), False
        for seed in range(4):
            c, mm = CG.reg_test(vm, entry, args, seed)
            ch |= set(c)
            m1 |= mm
        regs[label] = {"changed": sorted(ch, key=CG.SAVED.index), "MODE1_changed": m1}
    res["registers"] = regs
    allowed = set(regs["stock delay 0xb8482c alone"]["changed"]) | {"R2", "I12"}
    res["delay_stub_changes_beyond_stock"] = sorted(set(regs["hook stub fxh_delay"]["changed"]) - allowed)

    # fuzz: full-scale noise into both slots, all knobs jumping
    vm = V.candidate_vm(stream, cache=cache)
    rz = V.Runner(vm, dict(WORDS, IN_L=127, IN_R=127, CHR_VOL=127, CHR_DEL=127, CHR_REV=127, DEL_VOL=127,
                           DEL_REV=127, REV_VOL=100))
    rnd = random.Random(2)
    bad, pk = 0, {"chr": 0.0, "del": 0.0, "out": 0.0}
    knames = list(FL.KNOBS) + ["DEL_TIME", "DEL_WID", "DEL_FDBK", "DEL_HPF", "DEL_LPF"]
    for b in range(a.fuzz_blocks):
        if b % 20 == 0:
            rz.set(**{k: rnd.choice([0, 127, rnd.randrange(128)]) for k in knames}, DEL_X=rnd.randrange(2))
        Lz = [rnd.uniform(-1, 1) for _ in range(32)]
        Rz = [rnd.uniform(-1, 1) for _ in range(32)]
        oa, ob = rz.block(Lz, Rz)
        vals = {"chr": rz.cap["chr_out"][0][-32:] + rz.cap["chr_out"][1][-32:],
                "del": rz.cap["del_out"][0][-32:] + rz.cap["del_out"][1][-32:], "out": oa + ob}
        for k, vs in vals.items():
            for v in vs:
                if v != v or math.isinf(v):
                    bad += 1
            pk[k] = max(pk[k], V.peak(vs))
    res["fuzz"] = {"blocks": a.fuzz_blocks, "nan_inf": bad, "flanger_peak": round(pk["chr"], 3),
                   "tape_peak": round(pk["del"], 3), "output_peak": round(pk["out"], 3),
                   "insn_per_block_max": max(rz.per_block)}

    (OUT / "verify.json").write_text(json.dumps(res, indent=1))
    for k, v in res.items():
        print(f"{k}: {v}")
    ok = (res["init_flanger_magic_ok"] and res["init_tape_magic_ok"] and res["stock_chorus_state_same_after_init"]
          and res["chorus_input_identical"] and res["delay_input_identical"]
          and res["flanger_vs_mac_glue"]["bit_identical"] == res["flanger_vs_mac_glue"]["samples"]
          and (res["tape_vs_mac_glue"]["max_diff_db_re_peak"] is None or res["tape_vs_mac_glue"]["max_diff_db_re_peak"] < -100)
          and res["muted_send_output_identical"] and not res["delay_stub_changes_beyond_stock"]
          and not res["fuzz"]["nan_inf"])
    print("VERDICT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
