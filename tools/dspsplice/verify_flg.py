#!/usr/bin/env python3
"""Milestone 2 proof (T3.8): FLG1 = stock, with the flanger in the chorus slot.

    python3 tools/dspsplice/verify_flg.py [--seconds 8] [--in out/T3.11/dry/chords.wav]

In tools/sharcvm, stock and FLG1 each run the effect init 0x1c1445 and then the whole block handler
0x1c9d6b on the same codec input (IN L/R, sent only to the chorus slot). Results in
out/T3.8/FLG1/verify.json, WAVs next to it. Checks:
  1. the init hook ran: the flanger slot carries its magic after 0x1c1445, and the stock chorus
     state (DDR 0x80459388, 4,928 B) is the same as stock's (the stub jumps on to the stock init);
  2. the chorus slot's input (the send bus) is bit-identical to stock's, block by block;
  3. the slot's output equals a Mac build of the same C (fx/flanger.c + flg_glue.c, clang -O2
     -ffp-contract=off) fed the same input and the same parameter floats: sample for sample;
  4. and is close to fx/'s own renderer (fxrender -fx flanger -mix wet) at the integer knobs;
  5. with the chorus sends at 0 the whole handler output is bit-identical to stock's;
  6. instructions per block (stock vs FLG1; also with a knob moving every block), stack depth;
  7. registers: the hook changes nothing the stock chorus leaves intact (random register values);
  8. a fuzz run: full-scale noise into the slot, VOL 127, random knob jumps: no NaN/Inf, peaks.
"""
import argparse
import ctypes as C
import json
import math
import random
import struct
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(HERE))
import vmrun as V  # noqa: E402
from render_fx import test_signal  # noqa: E402
from fx import write_wav, read_wav  # noqa: E402
import verify_cg01 as CG  # noqa: E402

OUT = ROOT / "out/T3.8/FLG1"
KNOBS = dict(CHR_DPTH=96, CHR_SPD=30, CHR_HPF=32, CHR_WDTH=48)     # the swelly default (T3.11 12.5)
WORDS = dict(IN_L=20, IN_R=20, IN_L_PAN=0, IN_R_PAN=127, IN_CHR_L=127, IN_CHR_R=127,
             IN_DEL_L=0, IN_DEL_R=0, IN_REV_L=0, IN_REV_R=0, CHR_DEL=0, CHR_REV=0, CHR_VOL=100,
             DEL_VOL=0, REV_VOL=0, CMP_THR=127, CMP_RAT=0, CMP_MIX=0, PATTERN_VOL=127, **KNOBS)
FIXED = dict(FDBK=107, MAN=50, SHP=0, SLEW=0, DAMP=12, SYNC=0, OFS=0, POL=0)
SLOT = 0x300000
MAGIC = 0x464c4731
PARAMS = 0x257950
CHORUS_STATE, CHORUS_STATE_BYTES = 0x80459388, 4928


class FxIo(C.Structure):
    _fields_ = [("n", C.c_int32), ("inp", C.POINTER(C.POINTER(C.c_float))), ("r08", C.c_int32),
                ("out", C.POINTER(C.POINTER(C.c_float))), ("r10", C.c_int32), ("bpm", C.c_float)]


def native_lib(bdir):
    lib = bdir / "libflgnative.dylib"
    subprocess.check_call(["clang", "-O2", "-ffp-contract=off", "-fno-strict-aliasing", "-shared", "-fPIC",
                           "-DFLG_NATIVE", f"-I{ROOT / 'fx'}", f"-I{ROOT / 'tools/dspsplice/src'}", "-o", str(lib),
                           str(ROOT / "fx/flanger.c"), str(ROOT / "tools/dspsplice/src/flg_glue.c")])
    return C.CDLL(str(lib))


def native_render(lib, params, chin):
    """the glue on the Mac: flg_init(), then flg_process per 32-sample block"""
    lib.flg_init()
    p = (C.c_float * 5)(*params, 1.0)
    inL, inR, oL, oR = ((C.c_float * 32)() for _ in range(4))
    ins = (C.POINTER(C.c_float) * 2)(C.cast(inL, C.POINTER(C.c_float)), C.cast(inR, C.POINTER(C.c_float)))
    outs = (C.POINTER(C.c_float) * 2)(C.cast(oL, C.POINTER(C.c_float)), C.cast(oR, C.POINTER(C.c_float)))
    io = FxIo(32, ins, 0, outs, 0, 120.0)
    L, R = [], []
    for i in range(0, len(chin[0]), 32):
        inL[:] = chin[0][i:i + 32]
        inR[:] = chin[1][i:i + 32]
        lib.flg_process(p, C.byref(io))
        L += list(oL)
        R += list(oR)
    return L, R


def write_f32(path, L, R):
    import wave
    # fx/wav.c reads WAVE_FORMAT_IEEE_FLOAT 32-bit; write it by hand (wave cannot)
    data = b"".join(struct.pack("<ff", a, b) for a, b in zip(L, R))
    hdr = (b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVE" + b"fmt " + struct.pack("<IHHIIHH", 16, 3, 2, 48000,
           48000 * 8, 8, 32) + b"data" + struct.pack("<I", len(data)))
    path.write_bytes(hdr + data)


def read_f32(path):
    b = path.read_bytes()
    i = b.index(b"data") + 8
    v = struct.unpack("<%df" % ((len(b) - i) // 4), b[i:])
    return list(v[0::2]), list(v[1::2])


def compare(a, b):
    n = min(len(a[0]), len(b[0]))
    same, diff, pk = 0, 0.0, 0.0
    for c in (0, 1):
        for x, y in zip(a[c][:n], b[c][:n]):
            same += x == y
            diff = max(diff, abs(x - y))
            pk = max(pk, abs(y))
    return {"samples": 2 * n, "bit_identical": same, "max_diff": diff, "peak": pk,
            "max_diff_db_re_peak": round(V.db(diff / pk), 1) if pk and diff else None}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=8.0)
    ap.add_argument("--in", dest="inp", default=str(ROOT / "out/T3.11/dry/chords.wav"))
    ap.add_argument("--fuzz-blocks", type=int, default=1500)
    a = ap.parse_args()
    info = json.loads((OUT / "build.json").read_text())
    stream = (OUT / "section7.bin").read_bytes()
    cache = "t38-FLG1-" + info["section7"]["sha256"][:12]
    n = int(a.seconds * 48000) // 32 * 32
    if Path(a.inp).exists():
        L, R, sr = read_wav(a.inp)
        assert sr == 48000
        L, R = (L + [0.0] * n)[:n], (R + [0.0] * n)[:n]
    else:
        L, R = test_signal(a.seconds)
    res = {"input": str(a.inp), "seconds": a.seconds, "knobs": KNOBS, "fixed": FIXED}

    # 1. init
    vm_st = V.candidate_vm(None)
    vm_fl = V.candidate_vm(stream, cache=cache)
    res["init_magic_ok"] = vm_fl.rd32(SLOT) == MAGIC
    res["init_fresh_flag"] = vm_fl.rd32(SLOT + 4)
    res["stock_chorus_state_same_after_init"] = (vm_st.read(CHORUS_STATE, CHORUS_STATE_BYTES)
                                                 == vm_fl.read(CHORUS_STATE, CHORUS_STATE_BYTES))

    # 2-3: renders with the chorus send
    rs = V.Runner(vm_st, WORDS)
    oS = rs.process(L, R, paint=True)
    rf = V.Runner(vm_fl, WORDS)
    oF = rf.process(L, R, paint=True)
    # stock (effect init + every block above) never touches the ranges we take
    res["stock_leaves_our_ranges_zero"] = {
        "L1b1 0x2dd600-0x2e7000": not any(vm_st.read(0x2dd600, 0x2e7000 - 0x2dd600)),
        "L1b2 0x300000-0x308400": not any(vm_st.read(0x300000, 0x8400))}
    params = vm_fl.rdf(PARAMS, 4)
    res["params_floats"] = params
    res["params_as_knobs"] = [p * 127.0 for p in params]
    res["chorus_input_identical"] = rs.cap["chr_in"] == rf.cap["chr_in"]
    lib = native_lib(OUT / "build")
    res["slot_bytes"] = lib.flg_slot_size()
    nat = native_render(lib, params, rf.cap["chr_in"])
    res["vs_mac_glue"] = compare(nat, rf.cap["chr_out"])
    # fx/'s own renderer at the integer knobs
    write_f32(OUT / "chorus_slot_input.wav", *rf.cap["chr_in"])
    fxr = OUT / "build/fxrender"
    subprocess.check_call(["clang", "-O2", "-fno-strict-aliasing", "-ffp-contract=off", "-o", str(fxr),
                           str(ROOT / "fx/render.c"), str(ROOT / "fx/wav.c"), str(ROOT / "fx/tapedelay.c"),
                           str(ROOT / "fx/mbcomp.c"), str(ROOT / "fx/flanger.c"), "-lm"])
    args = [str(fxr), "-fx", "flanger", "-i", str(OUT / "chorus_slot_input.wav"), "-o", str(OUT / "mac_fxrender_wet.wav"),
            "-mix", "wet", "-bits", "32"]
    for k, v in list(FIXED.items()) + [(k[4:], v) for k, v in KNOBS.items()]:
        args += ["-p", f"{k}={v}"]
    subprocess.check_call(args, stdout=subprocess.DEVNULL)
    res["vs_fx_fxrender"] = compare(read_f32(OUT / "mac_fxrender_wet.wav"), rf.cap["chr_out"])
    res["slot_output_peak"] = round(V.peak(rf.cap["chr_out"][0] + rf.cap["chr_out"][1]), 4)
    res["slot_input_peak"] = round(V.peak(rf.cap["chr_in"][0] + rf.cap["chr_in"][1]), 4)
    res["stock_chorus_output_peak"] = round(V.peak(rs.cap["chr_out"][0] + rs.cap["chr_out"][1]), 4)
    res["output_peak_stock"] = round(V.peak(oS[0] + oS[1]), 4)
    res["output_peak_FLG1"] = round(V.peak(oF[0] + oF[1]), 4)
    write_wav(OUT / "vm_input.wav", L, R)
    write_wav(OUT / "vm_stock_chorus.wav", *oS)
    write_wav(OUT / "vm_FLG1_flanger.wav", *oF)
    write_wav(OUT / "vm_FLG1_slot_wet.wav", *rf.cap["chr_out"])

    # 6. cost and stack
    nb = len(rs.per_block)
    res["insn_per_block_stock"] = round(sum(rs.per_block) / nb)
    res["insn_per_block_FLG1"] = round(sum(rf.per_block) / nb)
    res["insn_per_block_FLG1_max"] = max(rf.per_block[1:])
    res["insn_per_block_delta"] = res["insn_per_block_FLG1"] - res["insn_per_block_stock"]
    res["stack_depth_bytes_stock"] = rs.stack_depth
    res["stack_depth_bytes_FLG1"] = rf.stack_depth
    # a knob moving every block (the ColdFire glides a turned knob): fl_targets runs per block
    rm = V.Runner(vm_fl, WORDS)
    for b in range(300):
        rm.set(CHR_DPTH=(b * 7) % 128, CHR_SPD=(b * 3) % 128)
        rm.block(L[32 * b:32 * b + 32], R[32 * b:32 * b + 32], capture=False)
    res["insn_per_block_FLG1_knobs_moving_mean"] = round(sum(rm.per_block[1:]) / (len(rm.per_block) - 1))
    res["insn_per_block_FLG1_knobs_moving_max"] = max(rm.per_block[1:])
    res["insn_per_block_stock_budget_note"] = "stock whole block ~320k with no voices (T3.6); unit ~63-65 % loaded (dnfw)"

    # 5. sends at 0: the rest is untouched
    w0 = dict(WORDS, IN_CHR_L=0, IN_CHR_R=0)
    m = 48000 // 32 * 32
    o0s = V.Runner(V.candidate_vm(None), w0).process(L[:m], R[:m], capture=False)
    o0f = V.Runner(V.candidate_vm(stream, cache=cache), w0).process(L[:m], R[:m], capture=False)
    res["muted_send_output_identical"] = o0s == o0f

    # 7. registers
    vm = V.candidate_vm(stream, cache=cache)
    V.Runner(vm, WORDS).process(L[:320], R[:320], capture=False)
    vm.wr32(0x257e6c, 32)
    vm.wr32(0x257e70, 0x25d6c8)
    vm.wr32(0x257e78, 0x25d6d0)
    args = {"R4": CHORUS_STATE, "R8": PARAMS, "R12": 0x257e6c}
    regs = {}
    for label, entry in (("hook stub fxh_chorus", int(info["symbols"]["fxh_chorus"], 16)),
                         ("stock chorus 0xb84d5c alone", 0xb84d5c)):
        ch, m1 = set(), False
        for seed in range(4):
            c, mm = CG.reg_test(vm, entry, args, seed)
            ch |= set(c)
            m1 |= mm
        regs[label] = {"changed": sorted(ch, key=CG.SAVED.index), "MODE1_changed": m1}
    allowed = set(regs["stock chorus 0xb84d5c alone"]["changed"]) | {"R2", "I12"}
    res["registers"] = regs
    res["stub_changes_beyond_stock"] = sorted(set(regs["hook stub fxh_chorus"]["changed"]) - allowed)

    # 8. fuzz
    vm = V.candidate_vm(stream, cache=cache)
    rz = V.Runner(vm, dict(WORDS, IN_L=127, IN_R=127, CHR_VOL=127, CHR_DEL=127, CHR_REV=127, DEL_VOL=100, REV_VOL=100))
    rnd = random.Random(1)
    bad, pk_slot, pk_out, pk_in = 0, 0.0, 0.0, 0.0
    for b in range(a.fuzz_blocks):
        if b % 20 == 0:
            rz.set(**{k: rnd.choice([0, 127, rnd.randrange(128)]) for k in KNOBS})
        Lz = [rnd.uniform(-1, 1) for _ in range(32)]
        Rz = [rnd.uniform(-1, 1) for _ in range(32)]
        oa, ob = rz.block(Lz, Rz)
        cl, cr = rz.cap["chr_out"][0][-32:], rz.cap["chr_out"][1][-32:]
        pk_in = max(pk_in, V.peak(rz.cap["chr_in"][0][-32:] + rz.cap["chr_in"][1][-32:]))
        for v in oa + ob + cl + cr:
            if v != v or math.isinf(v):
                bad += 1
        pk_slot = max(pk_slot, V.peak(cl + cr))
        pk_out = max(pk_out, V.peak(oa + ob))
    res["fuzz"] = {"blocks": a.fuzz_blocks, "nan_inf": bad, "slot_input_peak": round(pk_in, 3),
                   "slot_peak": round(pk_slot, 3), "output_peak": round(pk_out, 3),
                   "insn_per_block_max": max(rz.per_block)}

    (OUT / "verify.json").write_text(json.dumps(res, indent=1))
    for k, v in res.items():
        print(f"{k}: {v}")
    ok = (res["init_magic_ok"] and res["stock_chorus_state_same_after_init"] and res["chorus_input_identical"]
          and res["vs_mac_glue"]["bit_identical"] == res["vs_mac_glue"]["samples"]
          and res["muted_send_output_identical"] and not res["stub_changes_beyond_stock"]
          and not res["fuzz"]["nan_inf"] and all(res["stock_leaves_our_ranges_zero"].values()))
    print("VERDICT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
