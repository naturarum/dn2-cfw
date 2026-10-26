#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""SP01: what the DSP does with frame word 0xa28 (k 48, "Reverb FX Routing"), before SP01 uses it for the spring's WDTH.

    venv/bin/python scripts/sp01_k48check.py [--blocks 150] [--images stock,DT01,SP01] [--out out/SP01/k48check.json]

notes/T6.0c-frame-census-dnfw.md: the stock render reads the word every block (0x1c2e4a) and stores (value != 0) as a
byte at 0x2579d4 (0x1c2e63); no code it ran read that byte. T3.4: no reader found by absolute address. This settles it
for SP01 on whole images in sharcvm, with T6.0c's private interpreter core (a read watch and read log;
out/T6.0c/flipcheck/fc.py):

  1. readers: every read that touches 0x2579d4..0x2579d7, from the effect init 0x1c1445 (run fresh, with the watch
     on) and from every block of every run below; and every read of the image word 0x25ceb4 (k 48's copy) and of the
     receive buffer's 0xa28 outside the two stock copy loops;
  2. flips: per image and scenario, the same blocks (the same codec noise and frames) from the same whole-DSP state with
     k 48 = 0x0100 (what every kit stores), 0, 0x7f00, 0x6000 and 0 / 0x0100 alternating every block: the handler's
     output, the reverb's output buffer (0x25b368/0x25b3e8) and the delay's and chorus's every block, and at the end the
     whole memory (L1, L2, DDR) but k 48's own words (rx, image) and the flag word 0x2579d4.
Scenarios: T3.3 captures (real voice frames) with a note-on on all 16 voices, the external input (noise) sent to all
three effects and the cross-sends on (chorus -> reverb, delay -> reverb), the reverb's VOL at 127, three reverb settings
(the stock defaults; DEC 127 FREQ 0 GAIN 127; PRE 127 HPF 127 LPF 0). Images: the stock section 7 (the stock OS), DT01's
(DJ01's DSP, which runs the stock reverb) and SP01's (the spring reads k 48 as WDTH, so its flips change the reverb's
output by design; only its readers are listed).
"""
import argparse
import ctypes as C
import json
import random
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "out/T6.0c/flipcheck"))
import fc  # noqa: E402  (the private core: V.LIB patched before any VM is made)
V = fc.V
FB = fc.FB
sys.path.insert(0, str(ROOT / "tools/dspsplice"))
import vmrun as VR  # noqa: E402

FLAG = (0x2579d4, 0x2579d8)
IMG_K48 = 0x25c48c + 0xa28
REV_OUT = (0x25b368, 0x25b3e8)
DEL_OUT = (0x25b068, 0x25b0e8)
CHR_OUT = (0x25b668, 0x25b6e8)
IMAGES = {"stock": None, "DT01": ROOT / "out/DT01/DT01-dsp/section7.bin", "SP01": ROOT / "out/SP01/SP01-dsp/section7.bin"}
WORDS = dict(IN_L=127, IN_R=127, IN_CHR_L=100, IN_CHR_R=100, IN_DEL_L=100, IN_DEL_R=100, IN_REV_L=127, IN_REV_R=127,
             CHR_DEL=64, CHR_REV=127, CHR_VOL=100, DEL_TIME=20, DEL_FDBK=90, DEL_REV=127, DEL_VOL=100, REV_VOL=127)
REVS = {"stock defaults": dict(REV_PRE=16, REV_DEC=31, REV_FREQ=64, REV_GAIN=50, REV_HPF=32, REV_LPF=96),
        "DEC 127 FREQ 0 GAIN 127": dict(REV_PRE=0, REV_DEC=127, REV_FREQ=0, REV_GAIN=127, REV_HPF=0, REV_LPF=127),
        "PRE 127 HPF 127 LPF 0": dict(REV_PRE=127, REV_DEC=64, REV_FREQ=64, REV_GAIN=64, REV_HPF=127, REV_LPF=0)}
FLIPS = {"0x0100 (every kit)": 0x0100, "0": 0, "0x7f00": 0x7f00, "0x6000": 0x6000, "0 / 0x0100 alternating": "alt"}


class Rig(fc.Rig):
    """fc.Rig on a VM of our choice (an image loaded over the stock baseline, the effect init run with the watch on)"""

    def __init__(self, stream):
        vm = V.VM()
        vm.load_dn2()
        if stream is not None:
            VR.load_stream(vm, stream)
        self.vm = vm
        lib = vm.lib
        lib.vm_sizeof.restype = C.c_size_t
        self.size = lib.vm_sizeof()
        lib.vm_rw_add.argtypes = [C.c_void_p, C.c_uint32, C.c_uint32]
        lib.vm_rw_off.argtypes = [C.c_void_p]
        lib.vm_rl_exclude.argtypes = [C.c_void_p, C.c_int, C.POINTER(C.c_uint32)]
        lib.vm_rl_tag.argtypes = [C.c_void_p, C.c_uint32]
        lib.vm_rl_clear.argtypes = [C.c_void_p]
        lib.vm_rl_get.restype = C.c_uint32
        lib.vm_rl_get.argtypes = [C.c_void_p] + [C.POINTER(C.c_uint32)] * 6
        assert lib.vm_rw_add(vm.h, *FLAG) == 0
        assert lib.vm_rw_add(vm.h, IMG_K48, IMG_K48 + 2) == 1
        assert lib.vm_rw_add(vm.h, fc.RX + 0xa28, fc.RX + 0xa2a) == 2
        assert lib.vm_rw_add(vm.h, fc.RX + 0x1000 + 0xa28, fc.RX + 0x1000 + 0xa2a) == 3
        lib.vm_rl_exclude(vm.h, 2, (C.c_uint32 * 2)(*fc.COPY_PCS))
        lib.vm_rl_clear(vm.h)
        vm.call(VR.FXINIT, max_insns=10**9)
        self.init_reads, _ = self.read_log()
        self.d = FB.FullDSP(vm=vm)


def frame_for(cap, rev, k48, b):
    f = bytearray(cap)
    for k, v in dict(WORDS, **rev).items():
        fc.put(f, FB.FRAME_WORDS[k], v << 8)
    for r in range(16):
        fc.put(f, 0x34 + 2 * r, 0x7f00)
    fc.header_events(f, 0xffff if b == 0 else 0, 0x00ff if b == 60 else 0)
    if k48 == "alt":
        k48 = 0 if b % 2 == 0 else 0x0100
    fc.put(f, 0xa28, k48)
    return bytes(f)


def summarize(reads):
    out = {}
    for e in reads:
        k = "pc %#x reads %d B at %#x (range %d)" % (e["pc"], e["n"], e["addr"], e["rng"])
        out[k] = out.get(k, 0) + 1
    return out


def run_image(name, stream, caps, nblk):
    rig = Rig(stream)
    vm = rig.vm
    res = {"init_reads_of_flag_or_k48": summarize(rig.init_reads), "scenarios": {}}
    reads_all = []
    for sname, rev in REVS.items():
        cap = caps[(len(res["scenarios"]) * 60) % len(caps)][1]
        noise = [fc.noise_block(random.Random(1000 * b + 7)) for b in range(nblk + 4)]
        for b in range(4):
            rig.block(frame_for(cap, rev, 0x0100, b + 1), noise[b])
        rig.read_log()
        snap = rig.save()
        base = None
        sres = {}
        for fname, k48 in FLIPS.items():
            rig.restore(snap)
            outs, revo, dlo, cho, flags = [], [], [], [], set()
            for b in range(nblk):
                rig.block(frame_for(cap, rev, k48, b), noise[4 + b])
                outs.append(vm.read(FB.OUT_FLOATS, 0x100))
                revo.append(vm.read(REV_OUT[0], 0x80) + vm.read(REV_OUT[1], 0x80))
                dlo.append(vm.read(DEL_OUT[0], 0x80) + vm.read(DEL_OUT[1], 0x80))
                cho.append(vm.read(CHR_OUT[0], 0x80) + vm.read(CHR_OUT[1], 0x80))
                flags.add(vm.read(FLAG[0], 1)[0])
            reads, lost = rig.read_log()
            reads_all += reads
            mem = rig.save()[1]
            row = {"flag_byte_values_seen": sorted(flags), "reads": summarize(reads), "reads_lost": lost,
                   "peak_out": max(abs(x) for o in outs for x in struct.unpack("<64f", o)),
                   "peak_reverb_out": max(abs(x) for o in revo for x in struct.unpack("<64f", o)),
                   "peak_delay_out": max(abs(x) for o in dlo for x in struct.unpack("<64f", o))}
            if base is None:
                base = (outs, revo, dlo, cho, mem)
            else:
                ex = [(fc.RX + h * 0x1000 + 0xa28, fc.RX + h * 0x1000 + 0xa2a) for h in (0, 1)] + [
                    (IMG_K48, IMG_K48 + 2), FLAG]
                w = fc.diff_mem(base[4], mem, exclude=ex)
                row.update({
                    "handler_output_blocks_identical": sum(a == b for a, b in zip(base[0], outs)),
                    "reverb_output_blocks_identical": sum(a == b for a, b in zip(base[1], revo)),
                    "delay_output_blocks_identical": sum(a == b for a, b in zip(base[2], dlo)),
                    "chorus_output_blocks_identical": sum(a == b for a, b in zip(base[3], cho)),
                    "blocks": nblk,
                    "memory_words_differing_at_end": len(w), "first_differing": ["%#x" % x for x in w[:8]]})
            sres[fname] = row
            print(name, sname, fname, {k: v for k, v in row.items() if k != "reads"}, flush=True)
        res["scenarios"][sname] = sres
    res["all_block_reads"] = summarize(reads_all)
    return res


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--blocks", type=int, default=150)
    ap.add_argument("--images", default="stock,DT01,SP01")
    ap.add_argument("--out", default=str(ROOT / "out/SP01/k48check.json"))
    a = ap.parse_args()
    caps = fc.captures()
    rep = {"blocks": a.blocks, "flips": list(FLIPS), "scenarios": REVS, "words": WORDS, "images": {}}
    for name in a.images.split(","):
        p = IMAGES[name]
        if p is not None and not p.exists():
            print("skip", name, "(no image)")
            continue
        rep["images"][name] = run_image(name, None if p is None else p.read_bytes(), caps, a.blocks)
        rep["images"][name]["section7"] = None if p is None else str(p.relative_to(ROOT))
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(rep, indent=1))
    for n, r in rep["images"].items():
        print(n, "init:", r["init_reads_of_flag_or_k48"])
        print(n, "blocks:", r["all_block_reads"])


if __name__ == "__main__":
    main()
