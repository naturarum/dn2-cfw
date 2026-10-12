#!/usr/bin/env python3
"""Check the stock bytes at the T2.2 (arp on MIDI tracks) patch points in the Digitone II 1.12 main OS.

Reads ele3_out/02_type3.unpacked.bin (file offset 0 = 0x40000400) and compares each site with the
bytes notes/T2.2-arp-midi.md quotes. Writes nothing. Exit status 1 if any site differs.

usage: python3 scripts/t22_patch_points.py [path/to/02_type3.unpacked.bin]
"""
import hashlib
import sys
from pathlib import Path

BASE = 0x40000400
DEFAULT = Path(__file__).resolve().parent.parent / "ele3_out" / "02_type3.unpacked.bin"

# (address, stock bytes, what it is, proposed replacement or None)
SITES = [
    (0x4005f9b6, "4eb940031274 4fef000c 4a00 6600f58e",
     "ARP key (key 0x33) in MainScreenView::vslot02: isMidiTrack -> return before toggle/menu",
     "keep jsr; replace bne.w at 0x4005f9c2 with 4e71 4e71"),
    (0x400187ea, "4eb940031274 4fef0014 4a00 670e",
     "ArpSetupMenuView observer FUN_400187b4: isMidiTrack -> close the view (vslot10)",
     "replace beq.s at 0x400187f6 with bra.s: 600e"),
    (0x40060dde, "4eb940031274 4fef000c 4a00 6620",
     "MainScreenView::vslot18 ARP LED (0x32): MIDI track -> LED off",
     "replace bne.s at 0x40060dea with 4e71 (optional)"),
    (0x400da036, "207942c5b9ac 42af0060 71685cda 0500 6656",
     "sequencer FUN_400d99de: MIDI mask -> FUN_400d8cbe (MIDI event), else type-9 arp sync + FUN_400d87b8",
     "42af0060 4eb9<cave_seq> 4a00 4e71 4e71 6656"),
    (0x400d91bc, "207942c5b9ac 73685cda 0501 6626",
     "second trig path FUN_400d907a: same MIDI split, no arp sync event (not patched by the sketch)",
     None),
    (0x400269e6, "4eb94002a6a2",
     "voice ISR FUN_40025e36: voice allocator call in the note loop",
     "4eb9<cave_isr>"),
    (0x40027c18, "700f 222f0004 b081 6510 71798000537c e2a0 7201 c081 b380 4e75 4280 4e75",
     "FUN_40027c18 'track is not MIDI', used only by live note on/off",
     "4ef9<cave_live> (optional)"),
    (0x40031274, "2f0a 246f0008 2052 2f0a 20680034 4e90 588f 4a80 671e",
     "FUN_40031274 isMidiTrack(kitActive, track): bit track of kit+0x5cda (start of function)",
     None),
    (0x400ba306, "4eb940031274 4fef000c 4a00 6748",
     "KeyTrackSetupView opener: isMidiTrack -> 'N/A FOR MIDI TRACKS' (one of six)",
     None),
]


def main() -> int:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT
    data = path.read_bytes()
    print(f"{path}  size {len(data):#x}  sha256 {hashlib.sha256(data).hexdigest()}")
    bad = 0
    for addr, hexs, what, repl in SITES:
        want = bytes.fromhex(hexs.replace(" ", ""))
        off = addr - BASE
        got = data[off:off + len(want)]
        ok = got == want
        bad += not ok
        print(f"{'OK ' if ok else 'BAD'} {addr:#010x} {got.hex(' ', 2)}")
        print(f"    {what}")
        if repl:
            print(f"    patch: {repl}")
    print("all sites match" if not bad else f"{bad} site(s) differ")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
