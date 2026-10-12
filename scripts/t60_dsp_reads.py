#!/usr/bin/env python3
"""T6.0: which ColdFire frame words does the DN2 DSP code read? (read census in the T3.6 interpreter)

    python3 scripts/t60_dsp_reads.py [--blocks 200] [--out out/T6.0/dsp_reads]

Runs the whole DSP block handler 0x1c9d6b (tools/sharcvm/fullblock.py) with a private build of
the interpreter core that adds a read watch (out/T6.0/vm/sharcvm.c: per-byte read counts with the
first and last reader PC; the fast DAG path is off while a watch is set). Two byte ranges are
watched:
  - the frame image DM 0x25c48c..0x25cf0c (0xa80 bytes, filled by the render's copy loops), and
  - the SPI2 receive buffers 0x2c29d0..0x2c49d0 (both ping-pong halves, 0xabc bytes used each).

Frames fed, block after block (each scenario runs --blocks blocks):
  1. every T3.3 capture (out/T3.3/frames/*/*.tx.bin, the LIGHTHOUSE kit, sequencer stopped);
  2. the same frames with the global FX block and header flags randomised;
  3. random per-voice slots and owner words, and random words in the tail 0xa80..0xabc.
Reads by the two 672-word copy loops (rx -> image) are reported apart from real reads.

Writes out/T6.0/dsp_reads/{census.json,words.tsv,summary.txt}. No hardware; the stock
interpreter in tools/sharcvm is not changed (only imported).
"""
import argparse
import ctypes as C
import glob
import json
import random
import struct
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools/sharcvm"))
import vm as V  # noqa: E402

V.LIB = ROOT / "out/T6.0/build/libsharcvm_rw.dylib"


def _build(force=False):
    src = ROOT / "out/T6.0/vm/sharcvm.c"
    if force or not V.LIB.exists() or V.LIB.stat().st_mtime < src.stat().st_mtime:
        subprocess.check_call(["cc", "-O2", "-g", "-Wall", "-Wno-unused-function", "-Wno-unused-parameter",
                               "-ffp-contract=off", "-mno-outline", "-shared", "-fPIC", "-o", str(V.LIB), str(src)])


V.build_lib = _build
import fullblock as FB  # noqa: E402

IMAGE, IMAGE_LEN = 0x25c48c, 0xa80
RX, RX_LEN = 0x2c29d0, 0x2000
FRAME_LEN = 0xabc


def frame_name(o):
    """Human name of frame byte offset o (T3.3 layout)."""
    if o < 0x02:
        return "command"
    if o < 0x22:
        return "hdr 0x02+2r r=%d" % ((o - 2) // 2)
    if o < 0x34:
        return "hdr 0x22-0x32"
    if o < 0x54:
        return "0x34+2r r=%d" % ((o - 0x34) // 2)
    if o < 0x74:
        return "0x54+2r (mach +2) r=%d" % ((o - 0x54) // 2)
    if o < 0x94:
        return "0x74+2r level r=%d" % ((o - 0x74) // 2)
    if o < 0xb4:
        return "0x94+2r machine r=%d" % ((o - 0x94) // 2)
    if o < 0xd4:
        return "0xb4+2r (mach +1) r=%d" % ((o - 0xb4) // 2)
    if o < 0xda:
        return {0xd4: "comp routing tracks", 0xd6: "comp routing IN/FX", 0xd8: "tempo"}[o & ~1]
    if o < 0x9fa:
        r, s = divmod(o - 0xda, 0x92)
        k = 25 + s // 2 if s < 0x88 else 95 + (s - 0x88) // 2
        return "voice r=%d slot+%#x k=%d" % (r, s & ~1, k)
    if o < 0xa54:
        return "global k=%d" % (25 + (o - 0x9fa) // 2)
    if o < 0xa5a:
        return {0xa54: "hdr local", 0xa56: "0x42432aad<<8", 0xa58: "0x42432c11"}[o & ~1]
    if o < 0xa7a:
        return "owner r=%d" % ((o - 0xa5a) // 2)
    if o < 0xa80:
        return {0xa7a: "0x8000536a", 0xa7c: "0x80005369", 0xa7e: "mode flag"}[o & ~1]
    return "tail (tag-only) word %d" % ((o - 0xa80) // 2)


def grp(o):
    n = frame_name(o)
    return n.split(" r=")[0].split(" k=")[0].split(" word")[0]


def be_to_dsp(fr):
    """ColdFire frame (big-endian 16-bit words) -> bytes as they land in the DSP rx buffer."""
    fr = fr[:0xa80].ljust(FRAME_LEN, b"\0")
    return b"".join(fr[i:i + 2][::-1] for i in range(0, FRAME_LEN, 2))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--blocks", type=int, default=120)
    ap.add_argument("--out", default=str(ROOT / "out/T6.0/dsp_reads"))
    ap.add_argument("--seed", type=int, default=60)
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    rnd = random.Random(a.seed)

    d = FB.FullDSP()
    vm = d.vm
    lib = vm.lib
    lib.vm_rw_add.argtypes = [C.c_void_p, C.c_uint32, C.c_uint32]
    lib.vm_rw_get.argtypes = [C.c_void_p, C.c_int] + [C.POINTER(C.c_uint32)] * 3
    assert lib.vm_rw_add(vm.h, IMAGE, IMAGE + IMAGE_LEN) == 0
    assert lib.vm_rw_add(vm.h, RX, RX + RX_LEN) == 1

    caps = sorted(glob.glob(str(ROOT / "out/T3.3/frames/*/*.tx.bin")))
    caps = [p for p in caps if "routing-play" not in p]
    base = [be_to_dsp(Path(p).read_bytes()) for p in caps]
    print(f"{len(base)} captured frames")

    zeros = [0.0] * 32
    noise = lambda: [rnd.uniform(-0.3, 0.3) for _ in range(32)]   # noqa: E731
    spi_half = FB.SPI_HALF

    def run(frame, n):
        for _ in range(n):
            for h in (0, 1):   # write both halves so whichever the handler takes holds the frame
                vm.write(RX + 0x1000 * h, frame)
            d.vm.wr32(spi_half, rnd.randrange(2))
            vm.write(FB.CODEC_IN, struct.pack("<128I", *[FB.q31(x) for x in noise() + noise() + noise() + noise()]))
            vm.call(FB.HANDLER)

    def rnd_global(fr):
        fr = bytearray(fr)
        for o in range(0x9fa, 0xa54, 2):
            v = rnd.choice([0, 0x100, 0x3f00, 0x4000, 0x7f00, rnd.randrange(0x8000), rnd.randrange(0x10000)])
            if o in (0xa48,):
                v = rnd.randrange(8) << 8
            if o in (0xa4a,):
                v = rnd.randrange(20) << 8
            struct.pack_into("<H", fr, o, v)
        for o in (0x22, 0x24, 0x26, 0x28, 0x2a, 0x2c, 0x2e, 0x30, 0x32, 0xa54, 0xa56, 0xa58, 0xa7a, 0xa7c, 0xa7e):
            struct.pack_into("<H", fr, o, rnd.randrange(0x10000))
        struct.pack_into("<H", fr, 0xd4, rnd.randrange(0x10000))
        struct.pack_into("<H", fr, 0xd6, rnd.randrange(8))
        struct.pack_into("<H", fr, 0xd8, rnd.choice([120 * 120, 120 * 81, 120 * 200, rnd.randrange(30 * 120, 300 * 120)]))
        return bytes(fr)

    def rnd_voices(fr):
        fr = bytearray(fr)
        for o in range(0xda, 0x9fa, 2):
            struct.pack_into("<H", fr, o, rnd.randrange(0x8000))
        for r in range(16):
            struct.pack_into("<H", fr, 0xa5a + 2 * r, rnd.randrange(16))
            struct.pack_into("<H", fr, 0x02 + 2 * r, rnd.randrange(0x10000))
            struct.pack_into("<H", fr, 0x34 + 2 * r, rnd.randrange(0x10000))
            struct.pack_into("<H", fr, 0x74 + 2 * r, rnd.randrange(0x8000))
        for o in range(0xa80, FRAME_LEN, 2):
            struct.pack_into("<H", fr, o, rnd.randrange(0x10000))
        return bytes(fr)

    def machine_frame(fr, mach, filt, trig):
        """All 16 voices on one machine type (0-4) and filter type (0-5), owners = voice, voice slots
        random, a note-on on every voice when trig (dnfw: 0x34/0x36 note-on/off masks, 0x94 machine,
        0xb4 filter; T3.3: 0x94 = machine record byte 0, 0xb4 = byte 1)."""
        fr = bytearray(rnd_voices(rnd_global(fr)))
        for r in range(16):
            struct.pack_into("<H", fr, 0x94 + 2 * r, mach)
            struct.pack_into("<H", fr, 0xb4 + 2 * r, filt)
            struct.pack_into("<H", fr, 0xa5a + 2 * r, r)
            struct.pack_into("<H", fr, 0x02 + 2 * r, 36 + 3 * r)
            struct.pack_into("<H", fr, 0x74 + 2 * r, 0x7f00)
        struct.pack_into("<H", fr, 0x34, 0xffff if trig else 0)
        struct.pack_into("<H", fr, 0x36, 0)
        return bytes(fr)

    scen = {}
    import time
    t0 = time.time()
    per = max(1, a.blocks // max(1, len(base)))
    for i, fr in enumerate(base):
        run(fr, per)
    scen["captured"] = len(base) * per
    for i in range(a.blocks):
        run(rnd_global(base[i % len(base)]), 1)
    scen["random global+header"] = a.blocks
    for i in range(a.blocks):
        try:
            run(rnd_voices(rnd_global(base[i % len(base)])), 1)
        except V.VMError as e:
            print("random voices: trap:", e)
            scen["random voices trap"] = str(e)
            break
    else:
        scen["random voices+tail"] = a.blocks
    n_m = 0
    for mach in range(5):
        for filt in range(6):
            try:
                for b in range(max(4, a.blocks // 10)):
                    run(machine_frame(base[(mach * 6 + filt) % len(base)], mach, filt, b == 0), 1)
                    n_m += 1
            except V.VMError as e:
                print(f"machine {mach} filter {filt}: trap: {e}")
                scen[f"machine {mach} filter {filt} trap"] = str(e)[:200]
    scen["per machine/filter, note-on"] = n_m
    print(f"ran {sum(v for v in scen.values() if isinstance(v, int))} blocks in {time.time() - t0:.0f} s")

    res = {}
    for r, (lo, n) in enumerate(((IMAGE, IMAGE_LEN), (RX, RX_LEN))):
        cnt, first, last = (C.c_uint32 * 0x2000)(), (C.c_uint32 * 0x2000)(), (C.c_uint32 * 0x2000)()
        lib.vm_rw_get(vm.h, r, cnt, first, last)
        res[r] = [(cnt[i], first[i], last[i]) for i in range(n)]

    # the loads of the render's two 672-word copy loops (rx -> image): 0x1c287a/0x1c287d and
    # 0x1c302d/0x1c3030 (out/T3.1/disasm/main.txt); every other rx reader is a real read
    copy_pcs = {0x1c287d, 0x1c3030}
    rx_pcs = set()
    for half in (0, 1):
        for o in range(0, FRAME_LEN):
            c, f, l = res[1][half * 0x1000 + o]
            if c:
                rx_pcs.update((f, l))
    print("rx readers:", sorted(hex(p) for p in rx_pcs))

    rows = []
    for o in range(0, FRAME_LEN, 2):
        img = res[0][o] if o < IMAGE_LEN else (0, 0, 0)
        img2 = res[0][o + 1] if o + 1 < IMAGE_LEN else (0, 0, 0)
        ic = img[0] + img2[0]
        ipc = {p for p in (img[1], img[2], img2[1], img2[2]) if p}
        rx = [res[1][h * 0x1000 + o] for h in (0, 1)] + [res[1][h * 0x1000 + o + 1] for h in (0, 1)]
        rx_other = {p for c, f, l in rx if c for p in (f, l)} - copy_pcs
        rc = sum(c for c, f, l in rx)
        rows.append(dict(off=o, name=frame_name(o), image_reads=ic, image_pcs=sorted(ipc),
                         rx_reads=rc, rx_noncopy_pcs=sorted(rx_other)))
    with open(out / "words.tsv", "w") as f:
        f.write("frame_off\tname\timage_reads\timage_reader_pcs\trx_reads\trx_noncopy_pcs\n")
        for r in rows:
            f.write("%#05x\t%s\t%d\t%s\t%d\t%s\n" % (r["off"], r["name"], r["image_reads"],
                    " ".join(hex(p) for p in r["image_pcs"]), r["rx_reads"],
                    " ".join(hex(p) for p in r["rx_noncopy_pcs"])))
    json.dump(dict(scenarios=scen, copy_pcs=sorted(copy_pcs), rows=rows), open(out / "census.json", "w"), indent=0)

    # summary: unread words grouped into runs
    lines = [f"scenarios: {scen}", f"copy-loop PCs (rx readers): {sorted(hex(p) for p in copy_pcs)}", ""]
    run_start = None
    unread = [r for r in rows if r["image_reads"] == 0 and not r["rx_noncopy_pcs"]]
    lines.append(f"payload words (0..0xa80) never read after the copy: "
                 f"{sum(1 for r in unread if r['off'] < 0xa80)} of {0xa80 // 2}")
    lines.append(f"tail words (0xa80..0xabc) read by anything but the copy loops: "
                 f"{sum(1 for r in rows if r['off'] >= 0xa80 and r['rx_noncopy_pcs'])} of {(FRAME_LEN - 0xa80) // 2}")
    lines.append("")
    lines.append("unread runs (frame offsets, inclusive):")
    prev = None
    for r in unread + [None]:
        o = r["off"] if r else None
        if run_start is None:
            run_start = prev = o
            continue
        if o is not None and o == prev + 2 and grp(o) == grp(run_start):
            prev = o
            continue
        n = (prev - run_start) // 2 + 1
        lines.append(f"  {run_start:#05x}-{prev:#05x} ({n} words): {frame_name(run_start)} .. {frame_name(prev)}")
        run_start = prev = o
    (out / "summary.txt").write_text("\n".join(lines) + "\n")
    print("\n".join(lines[:6]))
    print(f"wrote {out}/summary.txt, words.tsv, census.json")


if __name__ == "__main__":
    main()
