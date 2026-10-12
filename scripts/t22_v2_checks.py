#!/usr/bin/env python3
"""Byte-level checks for notes/T2.2-arp-midi-v2.md (arp on MIDI-machine tracks, Digitone II OS 1.12).

Reads ele3_out/02_type3.unpacked.bin (main OS, file offset 0 = 0x40000400). Writes nothing.
Exit status 1 if any stock-byte check fails.

What it checks or derives:
  1. the image hash;
  2. stock bytes at every patch site and anchor the report quotes (the T2.2 v1 list plus the new
     sites: the second allocator call, the voice-builder call, the type-9 sync block);
  3. the time constants (90000 arp tick, 900000 sequencer tick, 0xb40 subtick cycle) in place;
  4. every type-9 event creator (jsr FUN_40138cb6 followed by moveq #9 within 18 bytes);
  5. every direct jsr/jmp to FUN_40031274 (isMidiTrack) and the vtable words that hold
     KitActive::vslot35 and its this-adjusting thunk;
  6. the arp speed table 0x401d5fe4: the period in ticks of each speed bit;
  7. the arp note-length table 0x40288048 against the MIDI trig-length table 0x401d8ce8, and a
     128-byte lookup table arp-length-index -> trig-length-index (first trig length not shorter);
  8. (v2) every 32-bit reference to FUN_40031274 by opcode (jsr.l, jmp.l, lea, move.l #) plus the
     jsr (d16,pc) calls, the KitActive typeinfo bases, the ARP key's switch entry, and the
     speed -> one-period trig-length table used for INF arp notes.

usage: python3 scripts/t22_v2_checks.py [path/to/02_type3.unpacked.bin] [--lut]
"""
import hashlib
import struct
import sys
from pathlib import Path

BASE = 0x40000400
DEFAULT = Path(__file__).resolve().parent.parent / "ele3_out" / "02_type3.unpacked.bin"
SHA = "a098f620019b2ad38fd7e0397df39501fb1cf6bc26d0283915f34c49446fdf1a"

# (address, stock bytes, what)
SITES = [
    # UI gates on the arp (v1 list, re-checked)
    (0x4005f9b6, "4eb940031274 4fef000c 4a00 6600f58e", "ARP key 0x33 handler: isMidiTrack -> bne.w 0x4005ef52 (return)"),
    (0x400187ea, "4eb940031274 4fef0014 4a00 670e", "ArpSetupMenuView observer lambda: isMidiTrack -> close view"),
    (0x40060dde, "4eb940031274 4fef000c 4a00 6620", "MainScreenView::vslot18 ARP LED 0x32: MIDI -> off"),
    # predicate, alias, thunk
    (0x40031274, "2f0a 246f0008 2052 2f0a 20680034 4e90 588f 4a80 671e", "FUN_40031274 isMidiTrack (head)"),
    (0x4003129c, "71685cda e2a0 7201 c081", "FUN_40031274: mvs.w (0x5cda,a0); asr; and #1"),
    (0x40191c70, "4ef940031274", "KitActive::vslot35 = jmp FUN_40031274"),
    (0x40191c76, "70a4 d1af0004 60f2", "thunk: this -= 0x5c; bra KitActive::vslot35"),
    # ISR-side predicates (live path)
    (0x40027c18, "700f 222f0004 b081 6510 71798000537c e2a0 7201 c081 b380 4e75 4280 4e75",
     "FUN_40027c18 'not MIDI' (reads ISR copy 0x8000537c)"),
    (0x40027c36, "700f 222f0004 b081 650e 71798000537c e2a0 7201 c081 4e75", "FUN_40027c36 'is MIDI'"),
    # sequencer
    (0x400da036, "207942c5b9ac 42af0060 71685cda 0500 6656", "FUN_400d99de: MIDI mask -> bne.s 0x400da09e (MIDI builder)"),
    (0x400da048, "4eb940138cb6 2040 4a80 672e 7009 2080 7001 21400014 223c000dbba0",
     "FUN_400d99de: type-9 sync event, [5]=1, step offset * 900000"),
    (0x400da098, "4ebae71e 601a", "FUN_400d99de: jsr FUN_400d87b8 (voice builder); bra.s 0x400da0b8"),
    (0x400da0b4, "4ebaec08 2a00", "FUN_400d99de: jsr FUN_400d8cbe (MIDI builder); move.l d0,d5"),
    (0x400d91bc, "207942c5b9ac 73685cda 0501 6626", "FUN_400d907a (trig preview): same MIDI split, no sync event"),
    (0x400d9a12, "203942c4f900 0680000dbba0", "FUN_400d99de: sequencer time += 900000 per call"),
    (0x400d9728, "23c8400001e4 13c0fc048079", "FUN_400d9610: vector 121 = FUN_400d99de, ICR level byte"),
    # voice ISR
    (0x4002557c, "23c0400002fc 7005 13c0fc04c07f", "vector 191 = FUN_40025e36, ICR1_63 = 5"),
    (0x40026762, "4eb940029cd4", "ISR: jsr FUN_40029cd4 (arp note in)"),
    (0x4002681e, "4eb940138d20", "ISR: copy event to arp template 0x4058f958 + t*0x6c"),
    (0x40026806, "283c00000b40", "ISR: subtick counter reset 0xb40"),
    (0x400268ba, "068000015f90", "ISR: first arp tick at now + 90000"),
    (0x400263f2, "d3fc00015f90", "ISR: next arp tick at bucket time + 90000"),
    (0x400269e6, "4eb94002a6a2", "ISR: voice allocator, note-on loop (cave_isr site)"),
    (0x40026d86, "4eb94002a6a2", "ISR: voice allocator, note-off loop (cave_off site)"),
    (0x400262ca, "4aaeff4c 6606 2d40ff4c 6054 2b40005c", "ISR type 3: append MIDI event, head fp@(-180), tail a5, link +0x5c"),
    (0x40026f5a, "4879445ff870 4eb940001896", "ISR: post MIDI list to queue 0x445ff870 (message 1)"),
    (0x40027b04, "2239402a132c 2001 d080", "ISR: clock += 2 * [0x402a132c] per block"),
    # MIDI task
    (0x4012a6e8, "40c0 46fc2700 20794460f4b8 43e8005c 23d14460f4b8", "FUN_4012a6e8 MIDI event alloc, no empty check"),
    (0x4012a6b2, "2f0a 7001 246f0008 b092 6726", "FUN_4012a6b2 free: refuses [0] == 1"),
    (0x4012aaa6, "73b944600ca0", "FUN_4012aaa6: mvz.b output count (8-bit)"),
    (0x4012aac2, "13c244600ca0", "FUN_4012aaa6: move.b output count"),
    (0x4012acb2, "13c044600ca0", "MIDI task: count cleared per message"),
    (0x4012af2c, "222a0034 b681 666a", "MIDI task: [0xd] == 1 -> mute / flush checks"),
    (0x4012afc4, "b0aa000c 6600065a", "MIDI task: [3] == 1 note-on, else note-off loop 0x4012b624"),
    # ---- v2 additions (notes/T2.2-arp-midi-v2.md) ----
    # interrupt chain: voice ISR -> vector 108 -> vector 121 (INTFRCH 0xfc048010)
    (0x40027af6, "203c00001000 81b9fc048010", "voice ISR: or.l #0x1000 (source 44) into INTFRCH"),
    (0x400d9710, "23c8400001b0", "FUN_400d9610: vector 108 = 0x400d79c8"),
    (0x400d9720, "13c0fc04806c", "FUN_400d9610: ICR44 level byte"),
    (0x400d79d4, "203cffffefff c1b9fc048010", "0x400d79c8: clears INTFRCH bit 12 on entry"),
    (0x400d7d60, "203c02000000 81b9fc048010", "0x400d79c8 code: or.l #0x02000000 (source 57) -> FUN_400d99de"),
    (0x400d99e6, "203cfdffffff c1b9fc048010", "FUN_400d99de: clears INTFRCH bit 25 on entry"),
    # arp state helpers
    (0x4002a3a6, "202f0004 41f94059d868 4ab00c00 56c0", "FUN_4002a3a6: held set non-empty"),
    (0x4002a3bc, "222f0004 41f94664c1bc 20301c00 6706 42b01c00 7001", "FUN_4002a3bc: resync pending test-and-clear"),
    (0x4002a3d4, "202f0004 7201 41f94664c1bc 21810c00", "FUN_4002a3d4: set resync pending"),
    (0x4002a3e6, "41ef0004 23d0405913a0", "FUN_4002a3e6: 0x405913a0 = arg (sequencer playing)"),
    (0x4002a3f2, "2039405913a0 4e75", "FUN_4002a3f2: read 0x405913a0"),
    (0x400e7e42, "700d 11400082 4200 11400083 700e 11400084", "FUN_400e7e38: default speed 0xd, length 0xe"),
    # UI
    (0x4005ed2e, "2800 7035 5384 b084 65000f18 303b4a08 48c0 4efb0802", "MainScreenView::vslot02 key switch (key-1, table 0x4005ed44)"),
    (0x400192c2, "203c400187b4", "ArpSetupMenuView ctor: move.l #FUN_400187b4 (auto-close lambda)"),
    (0x400b8e48, "203c400b5cda", "FUN_400b8b00: the '5cda' is the code address 0x400b5cda (false hit)"),
    # sequencer pending-step split (cave_pre site) and live routing sites
    (0x400d91bc, "207942c5b9ac 73685cda 0501 6626", "FUN_400d907a: cave_pre site (12 bytes) + bne.s"),
    (0x40121f2a, "4eb940027c18", "FUN_40121db2 live note-on: jsr FUN_40027c18 (cave_liveon site)"),
    (0x40121bcc, "4eb940027c18 4fef0010", "FUN_40121b0a live note-off: jsr FUN_40027c18 (cave_liveoff site)"),
    (0x40121ba2, "2c0e 0686ffffffc0", "FUN_40121b0a: d6 = a6-0x40 (event struct passed to FUN_4013801c)"),
    (0x40121bda, "2f06 4eb94013801c", "FUN_40121b0a: not MIDI -> FUN_4013801c(d6)"),
    (0x40121bf2, "2f06 4eb94012bb90", "FUN_40121b0a: MIDI -> FUN_4012bb90(d6)"),
    # interrupt: note-off poll, list reset, allocator args
    (0x400260a2, "4eb94012a7c6", "ISR: poll earliest MIDI note-off each pass"),
    (0x4002611c, "9bcd", "ISR: a5 (MIDI list tail) = 0 at the start of the event loop"),
    (0x400269b4, "2f2eff64", "ISR: allocator arg 9 = sound (fp-156)"),
    (0x40026d90, "203980005348 4a81", "ISR after cave_off site: d0 reloaded, d1 tested"),
    # MIDI task gates
    (0x4012b2cc, "202a0010 028000000400 6612 202a0010 028000000080 66000130", "MIDI task: [4]&0x400 LFO trig, [4]&0x80 note gate"),
    (0x4012b002, "4aaa0004 6630 2a02 5285 283c000000d0 4c024800 4ab65c00 674e", "MIDI task: lock list / restore test (option 3 hook)"),
    (0x4012b4d2, "486f004d 48780003", "MIDI task: FUN_4012aaa6(3, ptr) argument order"),
]

CONSTS = [(0x402a132c, 14400, "per-block clock step (initial)")]


def u32(d, a):
    return struct.unpack(">I", d[a - BASE:a - BASE + 4])[0]


def s32(d, a):
    return struct.unpack(">i", d[a - BASE:a - BASE + 4])[0]


def find_all(d, pat):
    out, i = [], 0
    while True:
        i = d.find(pat, i)
        if i < 0:
            return out
        out.append(i + BASE)
        i += 2


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    show_lut = "--lut" in sys.argv
    path = Path(args[0]) if args else DEFAULT
    d = path.read_bytes()
    sha = hashlib.sha256(d).hexdigest()
    bad = 0
    print(f"{path} size {len(d):#x} sha256 {sha} {'(pinned 1.12)' if sha == SHA else '(NOT the pinned image)'}")
    bad += sha != SHA

    print("\n== stock bytes")
    for addr, hexs, what in SITES:
        want = bytes.fromhex(hexs.replace(" ", ""))
        got = d[addr - BASE:addr - BASE + len(want)]
        ok = got == want
        bad += not ok
        print(f"{'OK ' if ok else 'BAD'} {addr:#010x}  {what}")
        if not ok:
            print(f"     want {want.hex()} got {got.hex()}")
    for addr, val, what in CONSTS:
        got = s32(d, addr)
        ok = got == val
        bad += not ok
        print(f"{'OK ' if ok else 'BAD'} {addr:#010x} = {got}  {what}")

    print("\n== type-9 event creators (jsr FUN_40138cb6 then moveq #9 within 18 bytes)")
    for a in find_all(d, bytes.fromhex("4eb940138cb6")):
        win = d[a - BASE + 6:a - BASE + 24]
        for k in range(0, len(win) - 1, 2):
            w = struct.unpack(">H", win[k:k + 2])[0]
            if (w & 0xF1FF) == 0x7009:
                print(f"  {a:#010x}")
                break

    print("\n== direct calls to FUN_40031274 (isMidiTrack)")
    jsr = find_all(d, bytes.fromhex("4eb940031274"))
    jmp = find_all(d, bytes.fromhex("4ef940031274"))
    print(f"  jsr.l sites: {len(jsr)}   jmp.l sites: {[hex(x) for x in jmp]}")
    for tgt, name in ((0x40191c70, "KitActive::vslot35"), (0x40191c76, "thunk this-0x5c")):
        refs = find_all(d, struct.pack(">I", tgt))
        print(f"  words equal to {tgt:#x} ({name}): {[hex(x) for x in refs]}")

    print("\n== arp speed table 0x401d5fe4 (0xb40 entries; counter counts down from 0xb40)")
    tab = [u32(d, 0x401d5fe4 + 4 * i) for i in range(0xB40)]
    print(f"  entry 0 = {tab[0]:#x}")
    for b in range(23):
        idx = [i for i in range(1, 0xB40) if (tab[i] >> b) & 1]
        gaps = sorted({idx[k + 1] - idx[k] for k in range(len(idx) - 1)}) if len(idx) > 1 else []
        per = gaps[0] if len(gaps) == 1 else gaps
        steps = per / 60 if isinstance(per, int) else None
        print(f"  speed {b:2d}: every {per} ticks = {steps} steps")

    print("\n== arp note length 0x40288048 vs MIDI trig length 0x401d8ce8 (half units; 1 step = 2,700,000)")
    arp = [s32(d, 0x40288048 + 4 * i) for i in range(128)]
    trig = [s32(d, 0x401d8ce8 + 4 * i) for i in range(128)]
    print(f"  trig[14] = {trig[14]} (LEN 1.000), trig[127] = {trig[127]} (0x7f = INF in the MIDI task)")
    print(f"  arp[0] = {arp[0]}, arp[126] = {arp[126]}, arp[127] = {arp[127]}")
    lut, exact, worst = [], 0, 0
    for v in arp:
        if v < 0:
            lut.append(0x7F)
            continue
        j = next(k for k in range(127) if trig[k] >= v)
        lut.append(j)
        exact += trig[j] == v
        worst = max(worst, trig[j] - v)
    print(f"  first trig length >= arp length: exact for {exact} of 127 timed values, "
          f"worst over-length {worst} half units = {worst / 2700000:.5f} step")
    if show_lut:
        print("  LUT (128 bytes):")
        for r in range(0, 128, 16):
            print("   ", " ".join(f"{x:02x}" for x in lut[r:r + 16]))

    print("\n== time base")
    print(f"  arp tick 90000 x 60 = {90000 * 60}; sequencer tick 900000 x 6 = {900000 * 6}; "
          f"2 x trig[14] = {2 * trig[14]}")
    print(f"  block at 120 BPM: 2 x {s32(d, 0x402a132c)} = {2 * s32(d, 0x402a132c)} units; "
          f"blocks per step = {5400000 / (2 * s32(d, 0x402a132c))}")

    print("\n== v2: every reference to FUN_40031274")
    pat = struct.pack(">I", 0x40031274)
    kinds = {}
    i = 0
    while True:
        i = d.find(pat, i)
        if i < 0:
            break
        op = d[i - 2:i].hex()
        name = {"4eb9": "jsr.l", "4ef9": "jmp.l", "2c3c": "move.l #,d6"}.get(op)
        if name is None and (int(op, 16) & 0xF1FF) == 0x41F9:
            name = "lea"
        kinds.setdefault(name or op, []).append(i - 2 + BASE)
        i += 2
    for off in range(0, len(d) - 4, 2):
        if d[off:off + 2] == b"\x4e\xba":
            disp = struct.unpack(">h", d[off + 2:off + 4])[0]
            if off + BASE + 2 + disp == 0x40031274:
                kinds.setdefault("jsr (d16,pc)", []).append(off + BASE)
    total = sum(len(v) for v in kinds.values())
    for k, v in sorted(kinds.items()):
        print(f"  {k:14s} {len(v):3d}  {' '.join(hex(x) for x in v) if len(v) < 8 else ''}")
    print(f"  total {total}")

    print("\n== v2: KitActive typeinfo 0x401da9f4 bases")
    ti = 0x401da9f4
    for k in range(u32(d, ti + 12)):
        bt = u32(d, ti + 16 + 8 * k)
        of = s32(d, ti + 20 + 8 * k)
        nm = d[u32(d, bt + 4) - BASE:u32(d, bt + 4) - BASE + 80].split(b"\0")[0].decode()
        print(f"  base {nm} at offset {of >> 8:#x}")

    print("\n== v2: ARP key in MainScreenView::vslot02 (table 0x4005ed44, index = key - 1)")
    for key in (0x32, 0x33, 0x35):
        off = struct.unpack(">h", d[0x4005ed44 + 2 * (key - 1) - BASE:0x4005ed44 + 2 * key - BASE])[0]
        print(f"  key {key:#x} -> {0x4005ed44 + off:#x}")

    print("\n== v2: speed -> trig length index >= one arp period (INF arp notes)")
    periods = []
    for b in range(23):
        idx = [k for k in range(1, 0xB40) if (tab[k] >> b) & 1]
        periods.append(idx[1] - idx[0])
    spd = [next(k for k in range(127) if trig[k] >= p * 45000) for p in periods]
    print("   ", " ".join(f"{x:02x}" for x in spd))
    print(f"  exact for {sum(trig[k] == p * 45000 for k, p in zip(spd, periods))} of 23 speeds")

    print("\nall stock-byte checks pass" if not bad else f"\n{bad} check(s) FAILED")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
