#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Run the T2.2 v2 caves (out/T2.2-v2/arp_midi_v2.s) under Unicorn against the stock DN2 1.12 image.

Verifier harness for notes/T2.2b-verify-1.md. It does not modify any firmware file.
It maps the main OS image (ele3_out/02_type3.unpacked.bin at 0x40000400), links the sketch at
0x50000000 (text) / 0x50000800 (tables) with m68k-elf-ld, fabricates the interrupt frame and
the pools the caves touch, and calls each cave. Image functions the caves call (FUN_4012a6e8,
FUN_40027c18, FUN_40138cb6, FUN_40138e3c, FUN_40138b50) run as stock code.

Run with digikit's environment (patched Unicorn, ColdFire V4e model):
  vendor/digikit/.venv/bin/python scripts/t22b_cave_harness.py
Exit status 1 if any expectation fails.
"""
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

from unicorn import Uc, UC_ARCH_M68K, UC_MODE_BIG_ENDIAN, UC_HOOK_CODE
from unicorn.m68k_const import (UC_CPU_M68K_CFV4E, UC_M68K_REG_PC, UC_M68K_REG_SR,
                                UC_M68K_REG_A7, UC_M68K_REG_A2, UC_M68K_REG_A5, UC_M68K_REG_A6,
                                UC_M68K_REG_D0, UC_M68K_REG_D1, UC_M68K_REG_D2, UC_M68K_REG_D3,
                                UC_M68K_REG_D4, UC_M68K_REG_D6, UC_M68K_REG_A3, UC_M68K_REG_A4)

ROOT = Path(__file__).resolve().parent.parent
IMG = ROOT / "ele3_out" / "02_type3.unpacked.bin"
SRC = ROOT / "out" / "T2.2-v2" / "arp_midi_v2.s"
BASE = 0x40000400
TEXT, RODATA = 0x50000000, 0x50000800
STOP = 0x5000f000            # return address used for every call
STACK_TOP = 0x47ff0000

FAIL = []


def check(name, got, want):
    ok = got == want
    print(f"{'OK  ' if ok else 'FAIL'} {name}: got {got:#x} want {want:#x}"
          if isinstance(got, int) and isinstance(want, int)
          else f"{'OK  ' if ok else 'FAIL'} {name}: got {got} want {want}")
    if not ok:
        FAIL.append(name)


def build():
    tmp = Path(tempfile.mkdtemp())
    o, elf = tmp / "c.o", tmp / "c.elf"
    subprocess.run(["m68k-elf-as", "-mcpu=54418", "-o", o, SRC], check=True)
    subprocess.run(["m68k-elf-ld", "-Ttext=0x50000000", "--section-start=.rodata=0x50000800",
                    "-e", "cave_sink", "-o", elf, o], check=True)
    syms = {}
    for line in subprocess.run(["m68k-elf-nm", elf], capture_output=True, text=True).stdout.split("\n"):
        p = line.split()
        if len(p) == 3:
            syms[p[2]] = int(p[0], 16)
    tb, rb = tmp / "t.bin", tmp / "r.bin"
    subprocess.run(["m68k-elf-objcopy", "-O", "binary", "-j", ".text", elf, tb], check=True)
    subprocess.run(["m68k-elf-objcopy", "-O", "binary", "-j", ".rodata", elf, rb], check=True)
    text, rod = tb.read_bytes(), rb.read_bytes()
    assert len(text) == 616 and len(rod) == 152, (len(text), len(rod))
    return syms, text, rod


class M:
    def __init__(self, text, rod):
        uc = Uc(UC_ARCH_M68K, UC_MODE_BIG_ENDIAN)
        uc.ctl_set_cpu_model(UC_CPU_M68K_CFV4E)
        uc.mem_map(0x00000000, 0x10000)            # low memory (catches the empty-pool writes)
        uc.mem_map(0x40000000, 0x08000000)         # image + RAM
        uc.mem_map(0x50000000, 0x10000)            # caves
        uc.mem_map(0x80000000, 0x10000)            # SRAM
        uc.mem_write(BASE, IMG.read_bytes())
        uc.mem_write(TEXT, text)
        uc.mem_write(RODATA, rod)
        uc.mem_write(STOP, b"\x4e\x71\x4e\x71")
        self.uc = uc
        self.trace = []
        uc.hook_add(UC_HOOK_CODE, self._hook)

    def _hook(self, uc, addr, size, user):
        self.trace.append(addr)
        if addr in self.watch:
            self.hits.append(addr)
            uc.emu_stop()

    def w32(self, a, v): self.uc.mem_write(a, struct.pack(">I", v & 0xffffffff))
    def w16(self, a, v): self.uc.mem_write(a, struct.pack(">H", v & 0xffff))
    def w8(self, a, v): self.uc.mem_write(a, struct.pack(">B", v & 0xff))
    def r32(self, a): return struct.unpack(">I", self.uc.mem_read(a, 4))[0]
    def r8(self, a): return self.uc.mem_read(a, 1)[0]
    def reg(self, r): return self.uc.reg_read(r) & 0xffffffff

    def call(self, entry, args, regs=None, watch=()):
        uc = self.uc
        sp = STACK_TOP
        for a in reversed(args):
            sp -= 4
            self.w32(sp, a)
        sp -= 4
        self.w32(sp, STOP)
        uc.reg_write(UC_M68K_REG_SR, 0x2700)       # SR first: writing SR switches the stack pointer
        uc.reg_write(UC_M68K_REG_A7, sp)
        for r, v in (regs or {}).items():
            uc.reg_write(r, v)
        self.watch = set(watch) | {STOP}
        self.hits = []
        self.trace = []
        self.sp0 = sp
        uc.emu_start(entry, STOP + 2, count=200000)
        return self.hits[-1] if self.hits else None


def main():
    syms, text, rod = build()
    print(f"caves: {', '.join(f'{k}={v:#x}' for k, v in sorted(syms.items(), key=lambda x: x[1]) if k.startswith('cave'))}")

    # ---------------- cave_sink ----------------
    T = 3
    FP = 0x47f00000
    EV = 0x47e00000          # current voice event (0x6c)
    TD = 0x47d00000          # track data ([0xb])
    SND = 0x47c00000         # arp sound record
    BKT = 0x47b00000         # time bucket
    MEV = 0x46000000         # fake MIDI events, 0x60 each

    def fresh(nfree=3, flags=0x80000, src=1, arp_len=20, speed=13, vel=0x64, note=60, notelen=0x0e,
              bucket_now=0, bucket_time=0x11110000, clock=0x22220000, mask=1 << T, td=TD):
        m = M(text, rod)
        m.w16(0x8000537c, mask)
        m.w32(0x80005398, clock)
        # MIDI event free list 0x4460f4b8, linked through +0x5c
        head = 0
        for i in reversed(range(nfree)):
            a = MEV + i * 0x60
            m.uc.mem_write(a, b"\xa5" * 0x60)
            m.w32(a + 0x5c, head)
            head = a
        m.w32(0x4460f4b8, head)
        # voice event
        m.uc.mem_write(EV, b"\x00" * 0x6c)
        m.w32(EV + 8, 0x0badbeef)          # ev[2]
        m.w32(EV + 16, T)                  # ev[4] track
        m.w32(EV + 20, src)                # ev[5] source
        m.w32(EV + 44, td)                 # ev[0xb]
        m.w32(EV + 56, flags)              # flags
        m.w8(EV + 0x26, note)
        m.w8(EV + 0x27, vel)
        m.w8(EV + 0x28, notelen)
        m.w8(TD + 0x480, 90)
        m.w8(TD + 0x481, 0x0e)
        m.w8(SND + 0x160, speed)
        m.w8(SND + 0x162, arp_len)
        m.w32(0x4058f8d8 + 4 * T, SND)
        m.w32(BKT, bucket_now)
        m.w32(BKT + 4, bucket_time)
        m.w32(FP - 36, EV)
        m.w32(FP - 44, BKT)
        m.w32(FP - 48, 0x11120000)         # pass end
        m.w32(FP - 180, 0)
        return m, head

    def sink_args():
        return [T, 60, 1, 0xf, FP - 72, 1, T, 0x80000, SND]

    regs = {UC_M68K_REG_A6: FP, UC_M68K_REG_A2: EV + 0x24, UC_M68K_REG_A5: 0,
            UC_M68K_REG_D2: 0x3c0000, UC_M68K_REG_D3: T, UC_M68K_REG_A3: 0x80003af0,
            UC_M68K_REG_A4: 0x12341234}

    print("\n== S1 cave_sink: sequenced arp note, timed bucket, empty list")
    m, head = fresh()
    stop = m.call(syms["cave_sink"], sink_args(), regs, watch=[0x4002a6a2])
    check("returned to caller", stop, STOP)
    ev = head
    check("free-list head advanced", m.r32(0x4460f4b8), MEV + 0x60)
    for off, want, name in ((0x00, 0, "[0]"), (0x04, 0, "[1] locks"), (0x08, T, "[2] track"),
                            (0x0c, 1, "[3] on"), (0x10, 0x80, "[4]"), (0x14, 0, "[5]"),
                            (0x18, 0xffffffff, "[6] inline"), (0x2c, TD, "[0xb]"),
                            (0x30, 0x11110000, "[0xc] bucket time"), (0x34, 1, "[0xd] source"),
                            (0x38, 0x0badbeef, "[0xe] = ev[2] (timed bucket)"), (0x3c, 0, "[0xf]"), (0x40, 0, "[0x10]"),
                            (0x5c, 0, "link")):
        check("MIDI ev " + name, m.r32(ev + off), want)
    check("+0x26 raw note", m.r8(ev + 0x26), 60)
    check("+0x27 velocity", m.r8(ev + 0x27), 0x64)
    check("+0x28 len = arp_len_lut[20]", m.r8(ev + 0x28), rod[20])
    check("+0x29", m.r8(ev + 0x29), 0)
    check("list head fp-180", m.r32(FP - 180), ev)
    check("a5 tail", m.reg(UC_M68K_REG_A5), ev)
    check("d0", m.reg(UC_M68K_REG_D0), 0)
    check("d1", m.reg(UC_M68K_REG_D1), 0)
    check("d2 kept", m.reg(UC_M68K_REG_D2), 0x3c0000)
    check("d3 kept", m.reg(UC_M68K_REG_D3), T)
    check("a3 kept", m.reg(UC_M68K_REG_A3), 0x80003af0)
    check("a4 kept", m.reg(UC_M68K_REG_A4), 0x12341234)
    check("sp balanced", m.reg(UC_M68K_REG_A7), m.sp0 + 4)

    print("\n== S2 cave_sink: second append (list not empty)")
    m.w32(EV + 20, 2)                       # live source this time
    stop = m.call(syms["cave_sink"], sink_args(), {UC_M68K_REG_A6: FP, UC_M68K_REG_A2: EV + 0x24,
                                                  UC_M68K_REG_A5: m.reg(UC_M68K_REG_A5)},
                  watch=[0x4002a6a2])
    ev2 = MEV + 0x60
    check("first event links to second", m.r32(ev + 0x5c), ev2)
    check("a5 = second", m.reg(UC_M68K_REG_A5), ev2)
    check("head unchanged", m.r32(FP - 180), ev)
    check("[0xd] live = 2", m.r32(ev2 + 0x34), 2)

    print("\n== S3 cave_sink: INF arp length (127) uses speed_len[speed]")
    m, head = fresh(arp_len=127, speed=13)
    m.call(syms["cave_sink"], sink_args(), regs, watch=[0x4002a6a2])
    check("+0x28 = speed_len[13]", m.r8(head + 0x28), rod[0x80 + 13])

    print("\n== S4 cave_sink: empty pool -> nothing allocated, no low-memory write")
    m, head = fresh(nfree=0)
    m.w32(0x5c, 0xdeadbeef)
    m.call(syms["cave_sink"], sink_args(), regs, watch=[0x4002a6a2, 0x4012a6e8])
    check("allocator not called", 0x4012a6e8 in m.trace, False)
    check("list still empty", m.r32(FP - 180), 0)
    check("low memory untouched", m.r32(0x5c), 0xdeadbeef)

    print("\n== S5 cave_sink: audio track tail-jumps to the stock allocator")
    m, head = fresh(mask=0)
    stop = m.call(syms["cave_sink"], sink_args(), regs, watch=[0x4002a6a2])
    check("reached 0x4002a6a2", stop, 0x4002a6a2)
    check("stack untouched at the jump", m.reg(UC_M68K_REG_A7), m.sp0)

    print("\n== S6 cave_sink: live note, negative velocity, no track data -> 100")
    m, head = fresh(src=2, vel=0x90, td=0)
    m.call(syms["cave_sink"], sink_args(), regs, watch=[0x4002a6a2])
    check("velocity fallback", m.r8(head + 0x27), 100)
    check("[0xd] = 2", m.r32(head + 0x34), 2)

    print("\n== S7 cave_sink: event in a 'now' bucket (first note of a live arp, sequencer stopped)")
    # FUN_40138f16 makes 'now' buckets ([0] = 1) and never writes +4; FUN_40138b50 does not clear it.
    m, head = fresh(src=2, bucket_now=1, bucket_time=0x00001000, clock=0x22220000)
    m.call(syms["cave_sink"], sink_args(), regs, watch=[0x4002a6a2])
    c = m.r32(head + 0x30)
    print(f"     [0xc] = {c:#x}; clock 0x80005398 = 0x22220000; stale bucket time = 0x1000")
    check("observed: [0xc] = stale bucket time, not the clock", c, 0x00001000)
    check("[0xe] = pass end for a 'now' bucket", m.r32(head + 0x38), 0x11120000)

    # ---------------- cave_liveon ----------------
    print("\n== L1 cave_liveon")
    for arp, want, label in ((1, 1, "MIDI track, arp on -> 1 (to the ISR)"),
                             (0, 0, "MIDI track, arp off -> stock FUN_40027c18 -> 0")):
        m = M(text, rod)
        m.w16(0x8000537c, 1 << T)
        kit = 0x47a00000
        m.w32(0x800052a0, kit)
        m.w8(kit + 0x34 + T * 0x48b + 0x15f, arp)
        m.call(syms["cave_liveon"], [T])
        check(label, m.reg(UC_M68K_REG_D0), want)
    m = M(text, rod)
    m.w16(0x8000537c, 0)
    m.w32(0x800052a0, 0x47a00000)
    m.call(syms["cave_liveon"], [T])
    check("synth track -> stock -> 1", m.reg(UC_M68K_REG_D0), 1)

    # ---------------- cave_pre / cave_seq ----------------
    print("\n== P1 cave_pre (Z clear = MIDI branch) and cave_seq (d0.b != 0 = MIDI branch)")
    for mask, arp, want_midi, label in ((0, 1, False, "synth track"),
                                        (1 << T, 0, True, "MIDI, arp off"),
                                        (1 << T, 1, False, "MIDI, arp on")):
        m = M(text, rod)
        kit = 0x47a00000
        m.w32(0x42c5b9ac, kit)
        m.w16(kit + 0x5cda, mask)
        m.w8(kit + 0x34 + T * 0x48b + 0x15f, arp)
        m.call(syms["cave_pre"], [], {UC_M68K_REG_D2: T, UC_M68K_REG_D0: 0x77})
        z = (m.reg(UC_M68K_REG_SR) >> 2) & 1
        check(f"cave_pre {label}: branch to MIDI", z == 0, want_midi)
        check(f"cave_pre {label}: d0 kept", m.reg(UC_M68K_REG_D0), 0x77)
        m.call(syms["cave_seq"], [], {UC_M68K_REG_D2: T, UC_M68K_REG_D6: kit})
        check(f"cave_seq {label}: MIDI branch", (m.reg(UC_M68K_REG_D0) & 0xff) != 0, want_midi)

    # ---------------- cave_sync (runs stock FUN_40138cb6 / FUN_40138e3c / FUN_40138b50) ----------------
    print("\n== Y1 cave_sync: arp-on MIDI track schedules a type-9 step sync")
    m = M(text, rod)
    kit = 0x47a00000
    m.w32(0x42c5b9ac, kit)
    m.w16(kit + 0x5cda, 1 << T)
    m.w8(kit + 0x34 + T * 0x48b + 0x15f, 1)
    evp, bk = 0x47900000, 0x47800000
    m.uc.mem_write(evp, b"\xcc" * 0x6c)
    m.w32(evp + 0x68, 0)
    m.w32(0x44645234, evp)                  # voice-event free list (FUN_40138cb6)
    m.uc.mem_write(bk, b"\xcc" * 0x14)
    m.w32(bk + 16, 0)
    m.w32(0x44645230, bk)                   # bucket free list (FUN_40138b50)
    m.w32(0x44645228, 0)                    # queue empty
    m.w32(0x42c4f900, 0x01000000)
    m.call(syms["cave_sync"], [], {UC_M68K_REG_D2: T, UC_M68K_REG_D4: 6, UC_M68K_REG_D6: kit})
    check("d0 = 1 (MIDI branch)", m.reg(UC_M68K_REG_D0) & 0xff, 1)
    check("event [0] = 9", m.r32(evp), 9)
    check("event [4] = track", m.r32(evp + 16), T)
    check("event [5] = 1 (step sync)", m.r32(evp + 20), 1)
    check("queue head = bucket", m.r32(0x44645228), bk)
    check("bucket time = seq time + (d4-1)*900000", m.r32(bk + 4), 0x01000000 + 5 * 900000)
    check("bucket holds the event", m.r32(bk + 8), evp)

    print(f"\n{len(FAIL)} failure(s)" + (": " + ", ".join(FAIL) if FAIL else ""))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
