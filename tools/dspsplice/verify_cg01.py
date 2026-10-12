#!/usr/bin/env python3
"""Milestone 1 proof (T3.8): CG01 = stock, except the chorus's wet output is exactly halved.

    python3 tools/dspsplice/verify_cg01.py [--seconds 2]

In tools/sharcvm, stock and CG01 each run the effect init and then the whole block handler
0x1c9d6b on the same codec input (the test signal through IN L/R, sent only to the chorus).
Checks (results in out/T3.8/CG01/verify.json, WAVs next to it):
  1. the chorus's input buffers are bit-identical, block by block;
  2. CG01's chorus output = 0.5 x stock's, bit for bit (x0.5 is exact in float);
  3. with the chorus VOL at 0 (the wet path muted) the whole handler output is bit-identical;
  4. with VOL up, CG01's output matches stock's dry part + half of stock's wet part
     (wet = stock(VOL) - stock(VOL 0)); the residual is the output stage's non-linearity;
  5. with random values in R0-R15, I0-I5, I8-I15, M0-M15: the hook changes no register (nor
     MODE1) that the stock chorus called on its own leaves intact (R2 and I12 change in any C
     call); the stock chorus and selcc's cg_post called on their own show which registers each
     convention treats as scratch;
  6. instructions per block and stack depth, stock vs CG01.
"""
import argparse
import json
import random
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(HERE))
import vmrun as V  # noqa: E402
from render_fx import test_signal  # noqa: E402
from fx import write_wav  # noqa: E402

OUT = ROOT / "out/T3.8/CG01"
WORDS = dict(IN_L=40, IN_R=40, IN_L_PAN=0, IN_R_PAN=127, IN_CHR_L=127, IN_CHR_R=127,
             IN_DEL_L=0, IN_DEL_R=0, IN_REV_L=0, IN_REV_R=0,
             CHR_DPTH=64, CHR_SPD=40, CHR_HPF=0, CHR_WDTH=80, CHR_DEL=0, CHR_REV=0, CHR_VOL=60,
             DEL_VOL=0, REV_VOL=0, CMP_THR=127, CMP_RAT=0, CMP_MIX=0, PATTERN_VOL=127)
SAVED = ([f"R{i}" for i in range(16)] + [f"I{i}" for i in range(16) if i not in (6, 7)]
         + [f"M{i}" for i in range(16)])
FIXED = {"M5": 0, "M6": 1, "M7": 0xffffffff, "M13": 0, "M14": 1, "M15": 0xffffffff}


def render(vm, L, R, vol, paint=True):
    w = dict(WORDS, CHR_VOL=vol)
    r = V.Runner(vm, w)
    oL, oR = r.process(L, R, paint=paint)
    return r, oL, oR


def reg_test(vm, entry, args, seed):
    """call `entry` with random values in every saved register; -> registers that changed"""
    rnd = random.Random(seed)
    before = {}
    for name in SAVED:
        v = FIXED.get(name, rnd.getrandbits(32) & 0xffffff00)
        if name in args:
            v = args[name]
        vm.set_reg(name, v)
        before[name] = v
    m1 = vm.reg("MODE1")
    vm.call(entry)
    changed = [n for n in SAVED if vm.reg(n) != before[n]]
    return changed, vm.reg("MODE1") != m1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=2.0)
    a = ap.parse_args()
    info = json.loads((OUT / "build.json").read_text())
    stream = (OUT / "section7.bin").read_bytes()
    L, R = test_signal(a.seconds)
    res = {}

    sA, oA = {}, {}
    for tag, st, cache in (("stock", None, None), ("CG01", stream, "t38-CG01-" + info["section7"]["sha256"][:12])):
        for vol in (60, 0):
            vm = V.candidate_vm(st, cache=cache)
            r, oL, oR = render(vm, L, R, vol)
            sA[(tag, vol)] = r
            oA[(tag, vol)] = (oL, oR)
    st100, cg100, st0, cg0 = sA[("stock", 60)], sA[("CG01", 60)], sA[("stock", 0)], sA[("CG01", 0)]

    # 1, 2: inside the handler
    res["chorus_input_identical"] = st100.cap["chr_in"] == cg100.cap["chr_in"]
    half = [[0.5 * v for v in ch] for ch in st100.cap["chr_out"]]
    res["chorus_output_exactly_half"] = [list(ch) for ch in cg100.cap["chr_out"]] == half
    res["chorus_output_peak_stock"] = round(V.peak(st100.cap["chr_out"][0] + st100.cap["chr_out"][1]), 6)
    res["chorus_output_peak_CG01"] = round(V.peak(cg100.cap["chr_out"][0] + cg100.cap["chr_out"][1]), 6)
    # 3: chorus muted: everything else identical
    res["muted_output_identical"] = oA[("stock", 0)] == oA[("CG01", 0)]
    # 4: VOL 100: CG01 = dry + wet/2 (wet measured on stock)
    resid, wetpk = 0.0, 0.0
    for c in (0, 1):
        for s100, s0, g in zip(oA[("stock", 60)][c], oA[("stock", 0)][c], oA[("CG01", 60)][c]):
            wet = s100 - s0
            wetpk = max(wetpk, abs(wet))
            resid = max(resid, abs(g - (s0 + 0.5 * wet)))
    res["vol60_wet_peak"] = round(wetpk, 6)
    res["vol60_residual_peak"] = resid
    res["vol60_residual_db_re_wet"] = round(V.db(resid / wetpk), 1) if wetpk else None
    import math
    def rms(xs):
        return math.sqrt(sum(x * x for x in xs) / max(1, len(xs)))
    wet_st = [x - y for c in (0, 1) for x, y in zip(oA[("stock", 60)][c], oA[("stock", 0)][c])]
    wet_cg = [x - y for c in (0, 1) for x, y in zip(oA[("CG01", 60)][c], oA[("CG01", 0)][c])]
    res["vol60_wet_in_output_change_db"] = round(V.db(rms(wet_cg) / rms(wet_st)), 3)
    res["output_peak_stock_vol60"] = round(V.peak(oA[("stock", 60)][0] + oA[("stock", 60)][1]), 4)
    diff = max(abs(x - y) for c in (0, 1) for x, y in zip(oA[("stock", 60)][c], oA[("CG01", 60)][c]))
    res["vol60_stock_vs_CG01_peak_difference"] = round(diff, 6)
    # 6: cost and stack
    n = len(st100.per_block)
    res["insn_per_block_stock"] = round(sum(st100.per_block) / n)
    res["insn_per_block_CG01"] = round(sum(cg100.per_block) / n)
    res["insn_per_block_delta"] = res["insn_per_block_CG01"] - res["insn_per_block_stock"]
    res["stack_depth_bytes_stock"] = st100.stack_depth
    res["stack_depth_bytes_CG01"] = cg100.stack_depth
    res["blocks"] = n

    # 5: registers
    vm = V.candidate_vm(stream, cache="t38-CG01-" + info["section7"]["sha256"][:12])
    V.Runner(vm, WORDS).process(L[:320], R[:320])      # ten blocks, so the chorus has a history
    # the descriptor as the FX bus sets it just before the chorus call (0x1c1bc6-0x1c1bdb)
    vm.wr32(0x257e6c, 32)
    vm.wr32(0x257e70, 0x25d6c8)
    vm.wr32(0x257e78, 0x25d6d0)
    args = {"R4": 0x80459388, "R8": 0x257950, "R12": 0x257e6c}
    stub = int(info["symbols"]["fxh_chorus"], 16)
    post = int(info["symbols"]["cg_post"], 16)
    regs = {}
    for label, entry, ar in (("hook stub fxh_chorus", stub, args), ("stock chorus 0xb84d5c alone", 0xb84d5c, args),
                             ("selcc cg_post alone", post, {"R4": 0x257e6c})):
        ch, m1 = set(), False
        for seed in range(4):
            c, m = reg_test(vm, entry, ar, seed)
            ch |= set(c)
            m1 |= m
        regs[label] = {"changed": sorted(ch, key=SAVED.index), "MODE1_changed": m1}
    res["registers"] = regs
    # the hook may change only what the stock routine it replaces changes itself (the caller
    # cannot rely on those across the call); R2 (the frame link CJUMP sets) and I12 (the return
    # address the C epilogue loads) change in every C call by the convention itself
    allowed = set(regs["stock chorus 0xb84d5c alone"]["changed"]) | {"R2", "I12"}
    res["stub_changes_beyond_stock"] = sorted(set(regs["hook stub fxh_chorus"]["changed"]) - allowed)
    res["stub_preserves_all"] = (not res["stub_changes_beyond_stock"]
                                 and not regs["hook stub fxh_chorus"]["MODE1_changed"])

    OUT.mkdir(parents=True, exist_ok=True)
    write_wav(OUT / "vm_stock_chorus_vol60.wav", *oA[("stock", 60)])
    write_wav(OUT / "vm_CG01_chorus_vol60.wav", *oA[("CG01", 60)])
    write_wav(OUT / "vm_input.wav", L, R)
    (OUT / "verify.json").write_text(json.dumps(res, indent=1))
    for k, v in res.items():
        print(f"{k}: {v}")
    ok = (res["chorus_input_identical"] and res["chorus_output_exactly_half"] and res["muted_output_identical"]
          and res["stub_preserves_all"])
    print("VERDICT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
