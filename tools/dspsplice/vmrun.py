"""Run a spliced section 7 in tools/sharcvm (T3.8): load, effect init, the whole block handler.

The candidate's boot stream is replayed into its final image and written over the stock baseline
(the same way tools/brickcheck/bc_dsp.py's dynamic run does), then the effect init 0x1c1445 runs
fresh and FullDSP drives the block handler 0x1c9d6b with a ColdFire frame and codec input.
Per block it can also capture the chorus's input and output buffers inside the handler
(T3.4: chorus in 0x25b568/0x25b5e8, out 0x25b668/0x25b6e8) and the delay's.
"""
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.dont_write_bytecode = True
for p in (ROOT / "tools/sharcvm", ROOT / "scripts", ROOT / "vendor/digikit", HERE):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))
from vm import VM, dn2_after_init, save_snapshot, load_snapshot, SNAP, STOP_PC, ST_STOP, VMError  # noqa: E402
import fullblock  # noqa: E402
import sharc_ldr_parse as P  # noqa: E402

FXINIT = 0x1c1445
HANDLER = 0x1c9d6b
SYS = 0x28000000
CHR_IN, CHR_OUT = (0x25b568, 0x25b5e8), (0x25b668, 0x25b6e8)
DEL_IN, DEL_OUT = (0x25af68, 0x25afe8), (0x25b068, 0x25b0e8)
# the FX bus's effect calls (T3.4): the CJUMP, and the instruction its return lands on
CHR_CALL, CHR_RET = 0x1c1be1, 0x1c1be8
DEL_CALL, DEL_RET = 0x1c1c58, 0x1c1c5f
STACK_LO, STACK_TOP = 0x26f000, 0x26f7f0     # the system stack the VM uses (T3.2 2.1)
PAINT = 0x5A5AA5A5


def to_core(a):
    return a - SYS if SYS <= a < SYS + 0x400000 else a


def load_stream(vm, stream):
    blocks, errors, stop = P.parse(stream)
    if errors:
        raise ValueError(errors)
    img = P.Image(blocks)
    for b in blocks:
        img.apply(stream, b)
    for (s, e), buf, own in zip(img.regions, img.data, img.owner):
        core = to_core(s)
        run_s = None
        for i in range(len(own) + 1):
            w = i < len(own) and own[i] >= 0
            if w and run_s is None:
                run_s = i
            if (not w) and run_s is not None:
                vm.write(core + run_s, bytes(buf[run_s:i]))
                run_s = None


def candidate_vm(stream=None, cache=None):
    """a VM with the candidate (or stock, stream=None) loaded and the effect init run.
    cache: snapshot name to reuse (out/T3.6/state/<name>.*)."""
    if stream is None:
        return dn2_after_init()
    vm = VM()
    vm.load_dn2()
    load_stream(vm, stream)
    if cache and (SNAP / f"{cache}.ddr.bin").exists():
        load_snapshot(vm, cache)
        return vm
    vm.call(FXINIT, max_insns=10**9)
    if cache:
        save_snapshot(vm, cache)
    return vm


class Runner:
    """FullDSP plus per-block capture of the effect buffers and a stack-depth probe."""

    def __init__(self, vm, words, tempo=120.0):
        self.vm = vm
        self.d = fullblock.FullDSP(vm=vm)
        self.d.set(**words)
        self.d.set_raw(TEMPO=int(round(120 * tempo)))
        self.cap = {"chr_in": ([], []), "chr_out": ([], []), "del_in": ([], []), "del_out": ([], [])}
        self.per_block = []
        self.stack_depth = 0

    def set(self, **words):
        self.d.set(**words)

    def _call_probed(self, probes):
        """vm.call(HANDLER) with stops at the given PCs, in order: probes = [(pc, fn), ...]"""
        vm = self.vm
        i6, i7 = vm.reg("I6"), vm.reg("I7")
        vm.set_reg("R2", i6)
        vm.set_reg("I6", i7)
        vm.wr32(i7, i6)
        vm.wr32(i7 - 4, STOP_PC - 1)
        vm.set_reg("I7", i7 - 8)
        vm.pc = HANDLER
        for pc, fn in list(probes) + [(STOP_PC, None)]:
            st = vm.run(10**8, stop=pc)
            if st != ST_STOP:
                raise VMError(f"block handler: did not reach {pc:#x} (status {st}, PC {vm.pc:#x})")
            if fn:
                fn()
        if vm.reg("I7") != i7 or vm.reg("I6") != i6:
            raise VMError("block handler: stack not restored")

    def block(self, L, R, capture=True, paint=False):
        """one block through the handler; with capture, the chorus and delay buffers are read
        at their call (inputs) and at the instruction their return lands on (outputs)"""
        vm, d = self.vm, self.d
        if paint:
            i7 = vm.reg("I7")
            vm.write(STACK_LO, PAINT.to_bytes(4, "little") * ((i7 - STACK_LO) // 4))
        spi = vm.rd32(fullblock.SPI_HALF)
        vm.write(fullblock.RX_BASE + 0x1000 * spi, bytes(d.frame))
        h = vm.rd32(fullblock.DMA_HALF)
        vm.write(fullblock.CODEC_IN + 0x100 * h,
                 b"".join(fullblock.q31(v).to_bytes(4, "little") for pair in zip(L, R) for v in pair))
        cap = self.cap

        def grab(key, pair):
            def f():
                cap[key][0].extend(vm.rdf(pair[0], 32))
                cap[key][1].extend(vm.rdf(pair[1], 32))
            return f
        probes = []
        if capture:
            probes = [(CHR_CALL, grab("chr_in", CHR_IN)), (CHR_RET, grab("chr_out", CHR_OUT)),
                      (DEL_CALL, grab("del_in", DEL_IN)), (DEL_RET, grab("del_out", DEL_OUT))]
        n0 = vm.icount
        self._call_probed(probes)
        self.per_block.append(vm.icount - n0)
        if paint:
            i7 = vm.reg("I7")
            raw = vm.read(STACK_LO, i7 - STACK_LO)
            word = PAINT.to_bytes(4, "little")
            low = next((STACK_LO + k for k in range(0, len(raw), 4) if raw[k:k + 4] != word), i7)
            self.stack_depth = max(self.stack_depth, i7 - low)
        return vm.rdf(fullblock.OUT_FLOATS, 32), vm.rdf(fullblock.OUT_FLOATS + 0x80, 32)

    def process(self, L, R, **kw):
        oL, oR = [], []
        for i in range(0, len(L) // 32 * 32, 32):
            a, b = self.block(L[i:i + 32], R[i:i + 32], **kw)
            oL += a
            oR += b
        return oL, oR


def db(x):
    return 20 * math.log10(x) if x > 0 else -999.0


def peak(xs):
    return max((abs(v) for v in xs), default=0.0)
