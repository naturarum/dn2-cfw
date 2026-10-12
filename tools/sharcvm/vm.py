"""SHARC+ interpreter driver: ctypes bindings to the C core (sharcvm.c).

    from vm import VM
    vm = VM()
    vm.load_dn2()                       # out/T3.2/mem images
    r = vm.call(0x1c06ba, R4=..., ...)  # run a C function, return R0

Decoding is lazy: the core stops with ST_DECODE at an undecoded PC; we decode with
digikit (decode.py), translate (translate.py), store the insn and continue.
"""
import ctypes as C
import os
import struct
import subprocess
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
BUILD = ROOT / "out/T3.6/build"
LIB = BUILD / "libsharcvm.dylib"
sys.path.insert(0, str(HERE))
import decode as D          # noqa: E402
import translate as T       # noqa: E402

ST_OK, ST_STOP, ST_IDLE, ST_DECODE, ST_TRAP = range(5)
STOP_PC = 0xfffff0          # sentinel return address (no code there)


def build_lib(force=False):
    src = HERE / "sharcvm.c"
    hdr = HERE / "sharcvm.h"
    if force or not LIB.exists() or LIB.stat().st_mtime < max(src.stat().st_mtime, hdr.stat().st_mtime):
        BUILD.mkdir(parents=True, exist_ok=True)
        cmd = ["cc", "-O2", "-g", "-Wall", "-Wno-unused-function", "-Wno-unused-parameter",
               "-ffp-contract=off", "-mno-outline", "-shared", "-fPIC", "-o", str(LIB), str(src)]
        subprocess.check_call(cmd)


class CSlot(C.Structure):
    _fields_ = [("op", C.c_uint16), ("d", C.c_uint8), ("x", C.c_uint8), ("y", C.c_uint8),
                ("mod", C.c_uint8), ("imm", C.c_int16)]


class CMem(C.Structure):
    _fields_ = [("kind", C.c_uint8), ("write", C.c_uint8), ("ireg", C.c_uint8), ("mreg", C.c_uint8),
                ("mode", C.c_uint8), ("width", C.c_uint8), ("ureg", C.c_uint8), ("flags", C.c_uint8),
                ("off", C.c_int32), ("data", C.c_uint32)]


class CInsn(C.Structure):
    _fields_ = [("kind", C.c_uint16), ("len", C.c_uint8), ("cond", C.c_uint8), ("flags", C.c_uint8),
                ("ncomp", C.c_uint8), ("ckind", C.c_uint8), ("a", C.c_uint8), ("b", C.c_uint8),
                ("c", C.c_uint8), ("pad", C.c_uint8 * 2), ("imm", C.c_int32), ("imm2", C.c_uint32),
                ("cs", CSlot * 3), ("mem", CMem * 2), ("pc", C.c_uint32), ("msg", C.c_uint32),
                ("hits", C.c_uint32)]


Page = CInsn * 4096

UREG = {}
for i in range(16):
    UREG[f"R{i}"] = i; UREG[f"F{i}"] = i; UREG[f"I{i}"] = 0x10 + i; UREG[f"M{i}"] = 0x20 + i
    UREG[f"L{i}"] = 0x30 + i; UREG[f"B{i}"] = 0x40 + i; UREG[f"S{i}"] = 0x50 + i
UREG.update(FADDR=0x60, DADDR=0x61, PC=0x63, PCSTK=0x64, PCSTKP=0x65, LADDR=0x66, CURLCNTR=0x67, LCNTR=0x68,
            EMUCLK=0x69, EMUCLK2=0x6a, PX1=0x6c, PX2=0x6d, TPERIOD=0x6e, TCOUNT=0x6f, USTAT1=0x70, USTAT2=0x71,
            MODE1=0x72, MMASK=0x73, MODE2=0x74, FLAGS=0x75, ASTATX=0x76, ASTATY=0x77, STKYX=0x78, STKYY=0x79,
            IRPTL=0x7a, IMASK=0x7b, IMASKP=0x7c, MODE1STK=0x7d, USTAT3=0x7e, USTAT4=0x7f)


class VMError(RuntimeError):
    pass


def f2u(x):
    return struct.unpack("<I", struct.pack("<f", x))[0]


def u2f(u):
    return struct.unpack("<f", struct.pack("<I", u & 0xffffffff))[0]


class VM:
    REGIONS = [("l1", 0x240000, 0x160000), ("l2", 0x20000000, 0x100000), ("ddr", 0x80000000, 0x2000000)]

    def __init__(self, strict_mmr=False):
        build_lib()
        lib = C.CDLL(str(LIB))
        self.lib = lib
        lib.vm_new.restype = C.c_void_p
        for name, res, args in [
            ("vm_sizeof_insn", C.c_size_t, []), ("vm_run", C.c_int, [C.c_void_p, C.c_uint64]),
            ("vm_map", None, [C.c_void_p, C.c_uint32, C.c_uint32, C.c_void_p]),
            ("vm_set_code_page", None, [C.c_void_p, C.c_uint32, C.c_void_p]),
            ("vm_trap_msg", C.c_char_p, [C.c_void_p]), ("vm_get_ureg", C.c_uint64, [C.c_void_p, C.c_int]),
            ("vm_set_ureg", None, [C.c_void_p, C.c_int, C.c_uint64]), ("vm_set_mode1", None, [C.c_void_p, C.c_uint32]),
            ("vm_pc_push", C.c_int, [C.c_void_p, C.c_uint32]), ("vm_clear_trap", None, [C.c_void_p]),
            ("vm_get_pc", C.c_uint32, [C.c_void_p]), ("vm_set_pc", None, [C.c_void_p, C.c_uint32]),
            ("vm_get_icount", C.c_uint64, [C.c_void_p]), ("vm_set_stop", None, [C.c_void_p, C.c_uint32]),
            ("vm_get_trap", C.c_int, [C.c_void_p, C.POINTER(C.c_uint32), C.POINTER(C.c_uint32)]),
            ("vm_get_reg40", C.c_uint64, [C.c_void_p, C.c_int, C.c_int]),
            ("vm_set_reg40", None, [C.c_void_p, C.c_int, C.c_int, C.c_uint64]),
            ("vm_get_dag", C.c_uint32, [C.c_void_p, C.c_int, C.c_int]),
            ("vm_set_dag", None, [C.c_void_p, C.c_int, C.c_int, C.c_uint32]),
            ("vm_get_mr", None, [C.c_void_p, C.c_int, C.c_int, C.POINTER(C.c_uint64), C.POINTER(C.c_uint64)]),
            ("vm_stats", None, [C.c_void_p, C.POINTER(C.c_uint64)]),
            ("vm_depths", C.c_int, [C.c_void_p, C.POINTER(C.c_int)]),
            ("vm_set_mmr_strict", None, [C.c_void_p, C.c_int]), ("vm_reset_stacks", None, [C.c_void_p]),
            ("vm_free", None, [C.c_void_p]),
        ]:
            fn = getattr(lib, name)
            fn.restype = res
            fn.argtypes = args
        assert lib.vm_sizeof_insn() == C.sizeof(CInsn), (lib.vm_sizeof_insn(), C.sizeof(CInsn))
        self.h = C.c_void_p(lib.vm_new())
        self.mem = {}
        for name, base, size in self.REGIONS:
            buf = (C.c_uint8 * size)()
            self.mem[name] = (base, size, buf)
            lib.vm_map(self.h, base, size, C.addressof(buf))
        # system (loader) alias of L1
        base, size, buf = self.mem["l1"]
        lib.vm_map(self.h, base + 0x28000000, size, C.addressof(buf))
        self.pages = {}
        self.msgs = [""]
        self.decoded = {}              # pc -> (Decoded, Insn or msg)
        self.form_count = Counter()    # executed-or-decoded forms
        self.unimpl = Counter()
        lib.vm_set_mmr_strict(self.h, 1 if strict_mmr else 0)
        lib.vm_set_align_strict(self.h, 0)

    # ---------------- memory ----------------
    def _region(self, addr, n):
        for name, (base, size, buf) in self.mem.items():
            if base <= addr and addr + n <= base + size:
                return buf, addr - base
            if base + 0x28000000 <= addr < base + 0x28000000 + size and name == "l1":
                return buf, addr - base - 0x28000000
        return None, None

    def read(self, addr, n):
        buf, off = self._region(addr, n)
        if buf is None:
            return None
        return bytes(buf[off:off + n])

    def write(self, addr, data):
        buf, off = self._region(addr, len(data))
        if buf is None:
            raise VMError(f"write outside mapped memory at {addr:#x}")
        C.memmove(C.addressof(buf) + off, data, len(data))

    def rd32(self, a):
        return struct.unpack("<I", self.read(a, 4))[0]

    def wr32(self, a, v):
        self.write(a, struct.pack("<I", v & 0xffffffff))

    def rdf(self, a, n=1):
        return list(struct.unpack(f"<{n}f", self.read(a, 4 * n)))

    def wrf(self, a, vals):
        self.write(a, struct.pack(f"<{len(vals)}f", *vals))

    # ---------------- registers ----------------
    def reg(self, name):
        if name.startswith("S") and name[1:].isdigit():
            return self.lib.vm_get_reg40(self.h, 1, int(name[1:])) >> 8
        return self.lib.vm_get_ureg(self.h, UREG[name]) >> 8

    def set_reg(self, name, v):
        if isinstance(v, float):
            v = f2u(v)
        self.lib.vm_set_ureg(self.h, UREG[name], (v & 0xffffffff) << 8)

    def freg(self, name):
        return u2f(self.reg(name))

    @property
    def pc(self):
        return self.lib.vm_get_pc(self.h)

    @pc.setter
    def pc(self, v):
        self.lib.vm_set_pc(self.h, v)

    @property
    def icount(self):
        return self.lib.vm_get_icount(self.h)

    def stats(self):
        out = (C.c_uint64 * 36)()
        self.lib.vm_stats(self.h, out)
        return list(out)

    def depths(self):
        out = (C.c_int * 4)()
        self.lib.vm_depths(self.h, out)
        return list(out)

    # ---------------- code ----------------
    def _page(self, pc):
        p = pc >> 12
        if p not in self.pages:
            pg = Page()
            self.pages[p] = pg
            self.lib.vm_set_code_page(self.h, p, C.addressof(pg))
        return self.pages[p]

    def ensure_page(self, pc):
        self._page(pc)

    def _msg(self, s):
        self.msgs.append(s)
        return len(self.msgs) - 1

    def decode_into(self, pc):
        d = D.decode_at(self.read, pc)
        slot = self._page(pc)[pc & 0xfff]
        C.memset(C.addressof(slot), 0, C.sizeof(CInsn))
        slot.pc = pc
        self.form_count[d.form] += 1
        if d.kind == "unknown" or d.parcels == 0:
            msg = f"{pc:#x}: undecodable ({d.note})"
            self.unimpl[f"undecodable: {d.note[:40]}"] += 1
            slot.kind = T.K["UNIMPL"]; slot.len = 1; slot.msg = self._msg(msg)
            self.decoded[pc] = (d, msg)
            return
        try:
            ins = T.translate(d.form, d.fields, pc, d.parcels, d.raw)
            if d.kind == "uncertain":
                self.form_count["(uncertain) " + d.form] += 1
        except T.Unimpl as e:
            msg = f"{pc:#x}: {d.form}: {e}  fields={d.fields}"
            self.unimpl[f"{d.form}: {e}"] += 1
            slot.kind = T.K["UNIMPL"]; slot.len = d.parcels; slot.msg = self._msg(msg)
            self.decoded[pc] = (d, msg)
            return
        self.decoded[pc] = (d, ins)
        slot.kind = ins.kind; slot.len = ins.len; slot.cond = ins.cond; slot.flags = ins.flags
        slot.ckind = ins.ckind; slot.a = ins.a; slot.b = ins.b; slot.c = ins.c
        slot.imm = C.c_int32(ins.imm & 0xffffffff if ins.imm >= 0 else ins.imm).value
        slot.imm2 = ins.imm2 & 0xffffffff
        slot.ncomp = len(ins.cs)
        for i, s in enumerate(ins.cs):
            cs = slot.cs[i]
            cs.op, cs.d, cs.x, cs.y, cs.mod, cs.imm = s.op, s.d, s.x, s.y, s.mod, s.imm
        for i, m in enumerate(ins.mem):
            cm = slot.mem[i]
            cm.kind, cm.write, cm.ireg, cm.mreg, cm.mode, cm.width = m.kind, m.write, m.ireg, m.mreg, m.mode, m.width
            cm.ureg, cm.flags = m.ureg, m.flags
            cm.off = C.c_int32(m.off & 0xffffffff).value
            cm.data = m.data & 0xffffffff

    # ---------------- running ----------------
    def trap_info(self):
        pc, idx = C.c_uint32(), C.c_uint32()
        code = self.lib.vm_get_trap(self.h, C.byref(pc), C.byref(idx))
        msg = self.lib.vm_trap_msg(self.h).decode()
        if idx.value:
            msg += ": " + self.msgs[idx.value]
        return code, pc.value, msg

    def run(self, max_insns=10**9, stop=None):
        if stop is not None:
            self.lib.vm_set_stop(self.h, stop)
        left = max_insns
        while True:
            start = self.icount
            st = self.lib.vm_run(self.h, left)
            left -= self.icount - start
            if st == ST_DECODE:
                self.decode_into(self.pc)
                continue
            if st == ST_TRAP:
                code, pc, msg = self.trap_info()
                raise VMError(f"trap at PC {pc:#x}: {msg}")
            return st

    def call(self, entry, args=(), max_insns=10**9, **regs):
        """Call a C function with the SHARC+ C convention (CJUMP + two pushes).
        Integer/pointer args go in R4, R8, R12 (args tuple) or by name in regs.
        Returns R0 (unsigned 32-bit)."""
        for name, v in zip(("R4", "R8", "R12"), args):
            self.set_reg(name, v)
        for name, v in regs.items():
            self.set_reg(name, v)
        i6, i7 = self.reg("I6"), self.reg("I7")
        # cjump: R2 = I6, I6 = I7; delay slots: dm(i7,m7)=r2; dm(i7,m7)=ret-1
        self.set_reg("R2", i6)
        self.set_reg("I6", i7)
        self.wr32(self._dm(i7), i6)
        self.wr32(self._dm(i7 - 4), STOP_PC - 1)
        self.set_reg("I7", i7 - 8)
        self.pc = entry
        st = self.run(max_insns, stop=STOP_PC)
        if st != ST_STOP:
            raise VMError(f"call {entry:#x} did not return (status {st}, PC {self.pc:#x})")
        if self.reg("I7") != i7 or self.reg("I6") != i6:
            raise VMError(f"call {entry:#x}: stack not restored (I6 {self.reg('I6'):#x}/{i6:#x}, I7 {self.reg('I7'):#x}/{i7:#x})")
        return self.reg("R0")

    @staticmethod
    def _dm(a):
        if 0x90000 <= a < 0xe8000:
            return a * 4
        return a

    # ---------------- loaders ----------------
    def load_dn2(self, memdir=ROOT / "out/T3.2/mem"):
        """Load the DN2 1.12 DSP image (scripts/t32_load.py output) and set the C runtime state
        that the reset code 0x1c0e70 leaves (T3.2 sections 2.1 and 3.5)."""
        memdir = Path(memdir)
        l1 = bytearray(0x160000)
        for blk, base in (("l1b0", 0x240000), ("l1b1", 0x2c0000), ("l1b2", 0x300000), ("l1b3", 0x380000)):
            data = (memdir / f"{blk}.bin").read_bytes()
            off = base - 0x240000
            l1[off:off + len(data)] = data
        self.write(0x240000, bytes(l1))
        self.write(0x20000000, (memdir / "l2.bin").read_bytes())
        ddr = (memdir / "ddr.bin").read_bytes()
        self.write(0x80000000, ddr)
        self.c_runtime_state(stack_base=0x26f000, stack_top=0x26f7f0, l7=0x1fd, mode1=0x1011800)
        self.lib.vm_set_align_strict(self.h, 4)   # DN2 code: halfword-aligned words OK, trap on odd addresses

    def map_zero_page(self, addr, size=0x1000):
        """Map a zero-filled page at an address the stock map does not cover (e.g. the C runtime's
        second constructor list at 0x10000000, read during boot). Documented in the T3.6 note."""
        buf = (C.c_uint8 * size)()
        self.mem[f"zero_{addr:#x}"] = (addr, size, buf)
        self.lib.vm_map(self.h, addr, size, C.addressof(buf))

    def c_runtime_state(self, stack_base, stack_top, l7, mode1):
        self.lib.vm_set_mode1(self.h, mode1)
        for name, v in (("M5", 0), ("M6", 1), ("M7", 0xffffffff), ("M13", 0), ("M14", 1), ("M15", 0xffffffff)):
            self.set_reg(name, v)
        for i in range(16):
            self.set_reg(f"L{i}", 0)
        self.set_reg("B7", stack_base)
        self.set_reg("B6", stack_base)
        self.set_reg("I7", stack_top)
        self.set_reg("I6", stack_top)
        self.set_reg("L7", l7)
        self.set_reg("L6", l7)

    def load_elf(self, path):
        import elf as E
        e = E.read_elf(path)
        for s in e.sections:
            if s.kind == "sw_code":
                self.write(E.sw_to_byte(s.addr), s.data)
            elif s.kind in ("data", "bss"):
                self.write(s.addr, s.data)
        return e


def trace_steps(vm, n, regs=("R0", "R1", "R2", "R3", "R4", "R5", "R6", "R7", "R8", "R9", "R10", "R11", "R12", "R13",
                             "R14", "R15", "I4", "I5", "I6", "I7", "ASTATX"), out=None, stop=None):
    """Single-step n instructions; yield (pc, form, changed-registers dict). Slow; for debugging."""
    prev = {r: vm.reg(r) for r in regs}
    for _ in range(n):
        pc = vm.pc
        if stop is not None and pc == stop:
            return
        st = vm.lib.vm_run(vm.h, 1)
        while st == ST_DECODE:
            vm.decode_into(vm.pc)
            st = vm.lib.vm_run(vm.h, 1)
        cur = {r: vm.reg(r) for r in regs}
        ch = {r: cur[r] for r in regs if cur[r] != prev[r]}
        prev = cur
        d = vm.decoded.get(pc)
        form = d[0].form if d else "?"
        yield pc, form, ch, st
        if st == ST_TRAP or st == ST_IDLE:
            return


def call_rts(vm, entry, max_insns=10**8, **regs):
    """Call an assembly helper that returns with RTS (CALL convention, PC stack)."""
    for name, v in regs.items():
        vm.set_reg(name, v)
    if not vm.lib.vm_pc_push(vm.h, STOP_PC):
        raise VMError("PC stack push failed")
    vm.pc = entry
    st = vm.run(max_insns, stop=STOP_PC)
    if st != ST_STOP:
        raise VMError(f"call {entry:#x} did not return (status {st}, PC {vm.pc:#x})")
    return vm.reg("R0")


SNAP = ROOT / "out/T3.6/state"


def save_snapshot(vm, name):
    SNAP.mkdir(parents=True, exist_ok=True)
    for region, (base, size, buf) in vm.mem.items():
        (SNAP / f"{name}.{region}.bin").write_bytes(bytes(buf))


def load_snapshot(vm, name):
    for region, (base, size, buf) in vm.mem.items():
        data = (SNAP / f"{name}.{region}.bin").read_bytes()
        C.memmove(C.addressof(buf), data, len(data))


def dn2_after_init(name="dn2-fxinit"):
    """A VM with the DN2 image loaded and the effect init 0x1c1445 run (cached as a snapshot)."""
    vm = VM()
    vm.load_dn2()
    if (SNAP / f"{name}.ddr.bin").exists():
        load_snapshot(vm, name)
    else:
        vm.call(0x1c1445)
        save_snapshot(vm, name)
    return vm


def watch(vm, lo, hi):
    vm.lib.vm_set_watch.argtypes = [C.c_void_p, C.c_uint32, C.c_uint32]
    vm.lib.vm_set_watch(vm.h, lo, hi)


def watch_log(vm):
    pc, ad, va = (C.c_uint32 * 65536)(), (C.c_uint32 * 65536)(), (C.c_uint32 * 65536)()
    vm.lib.vm_get_watch.argtypes = [C.c_void_p] + [C.POINTER(C.c_uint32)] * 3
    n = vm.lib.vm_get_watch(vm.h, pc, ad, va)
    return [(pc[i], ad[i], va[i]) for i in range(n)]


def profile_report(vm, top=40):
    """Dynamic instruction mix from the per-instruction hit counters (vm_set_profile(1))."""
    by_form, by_op, total = Counter(), Counter(), 0
    for pc, (d, ins) in vm.decoded.items():
        slot = vm.pages[pc >> 12][pc & 0xfff]
        h = slot.hits
        if not h:
            continue
        total += h
        by_form[d.form] += h
        if not isinstance(ins, str):
            ops = "+".join(T.OPS[s.op] for s in ins.cs) or "-"
            mem = ",".join(f"{'st' if m.write else 'ld'}{['NW','LW','SW','SWSE','BW','BWSE','PX'][m.width]}" for m in ins.mem)
            by_op[f"{T_KIND_NAMES[ins.kind]} {ops} {mem}".strip()] += h
    return total, by_form, by_op


T_KIND_NAMES = {v: k for k, v in T.K.items()}
