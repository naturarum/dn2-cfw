"""Hook stubs in SHARC+ assembly for selas (T3.8), generated so the addresses stay in one place.

Why a stub at all (notes/T3.8 section 2):
  * selcc and the stock code (ADI's compiler, CCES) disagree on which registers a call
    preserves. selcc (vendor/selache/selcc/src/target.rs) treats R0, R1, R3-R7 as
    caller-saved and R8-R15 as callee-saved; the stock code keeps values in R3, R5-R7 across
    calls and treats R8 and R12 as scratch (see the prologue of 0x1c1445, which saves
    M0-M3, I0-I3, I5, I8-I11, I14, I15, M10, M11). So selcc code may not be called directly
    from stock code, nor stock code from selcc code.
  * The stub is entered exactly like the stock routine it replaces (CJUMP: R2 = I6, I6 = I7,
    caller's frame pointer and return address pushed in the delay slots). It saves R0-R15,
    I0-I5, M1-M4 and MODE1 to a fixed save area (absolute stores, the stock 14a form
    `DM(addr) = ureg`; see SAVE_REGS for why that set), runs its steps, restores them and
    returns with the stock C epilogue, or tail-jumps to a stock routine with the caller's
    frame untouched.
  * Every instruction used here occurs in the stock image with the same bits apart from its
    address or immediate (tools/dspsplice/audit.py checks it): 14a absolute load/store, 5b MODE1 <-> R0 moves,
    18a BIT SET/CLR MODE1, 25a CJUMP, 3c/16a pushes, 3b `I12 = DM(M7,I6)`, 9b return, 25c
    RFRAME, 8a_abs JUMP. No shift-by-immediate (selas mis-encodes negative ones).
Steps:
  ("stock", sw)          call a stock routine with the caller's own R4, R8, R12 and MODE1
  ("c", symbol, [regs])  call a selcc function; its R4, R8, R12 are the caller's registers named
                         in regs (e.g. ["R8", "R12"] passes the caller's R8 as R4); MODE1 is set to
                         the C environment selcc assumes (SISD, RND32, round to nearest, no ALU
                         saturation, no broadcast loads) for the call
  ("tail", sw)           restore everything and JUMP to a stock routine (it returns to our caller)
A stub without a "tail" step ends by restoring everything and returning to the caller.
"""

# Saved and restored: every register whose absolute load AND store (Type 14a) the stock image itself
# uses with the same encoding (R0-R15, I0-I5, M1-M4), plus MODE1 through R0. Not saved: I8-I15, M0,
# M5-M15, B, L, which neither our selcc code (build.py's register census refuses a build that writes
# them) nor the stock routines a stub calls (they follow their own convention) change; I4/I12 are
# scratch in both conventions. verify_*.py checks the result with random values in all registers.
SAVE_REGS = ([f"R{i}" for i in range(16)] + [f"I{i}" for i in range(6)]
             + [f"M{i}" for i in range(1, 5)] + ["MODE1"])
MODE1_CLR = 0x00E0A000   # BDCST1 | BDCST9 | PEYEN | TRUNCATE | ALUSAT
MODE1_SET = 0x00010000   # RND32


def slot(save_base, reg):
    return save_base + 4 * SAVE_REGS.index(reg)


def gen(name, steps, save_base):
    L = [f".GLOBAL {name}.;", f"{name}.:"]
    for r in SAVE_REGS:
        if r == "MODE1":
            continue
        L.append(f"    DM (0x{slot(save_base, r):X}) = {r};")
    L.append("    R0 = MODE1;")
    L.append(f"    DM (0x{slot(save_base, 'MODE1'):X}) = R0;")
    k = 0
    tail = None
    for st in steps:
        if st[0] == "stock":
            L.append(f"    R0 = DM (0x{slot(save_base, 'MODE1'):X});")
            L.append("    MODE1 = R0;")
            L.append("    NOP;")
            for r in ("R4", "R8", "R12"):
                L.append(f"    {r} = DM (0x{slot(save_base, r):X});")
            target = f"0x{st[1]:X}"
        elif st[0] == "c":
            L.append(f"    BIT CLR MODE1 0x{MODE1_CLR:X};")
            L.append(f"    BIT SET MODE1 0x{MODE1_SET:X};")
            L.append("    NOP;")
            for dst, src in zip(("R4", "R8", "R12"), st[2]):
                L.append(f"    {dst} = DM (0x{slot(save_base, src):X});")
            target = f"{st[1]}."
        elif st[0] == "tail":
            tail = st[1]
            break
        else:
            raise ValueError(st)
        L.append("    R2 = I6;")
        L.append(f"    CJUMP {target} (DB);")
        L.append("    DM (I7,M7) = R2;")
        L.append(f"    DM (I7,M7) = .L_{name}_r{k}-1;")
        L.append(f".L_{name}_r{k}:")
        k += 1
    # restore: MODE1 first (through R0), then every saved register
    L.append(f"    R0 = DM (0x{slot(save_base, 'MODE1'):X});")
    L.append("    MODE1 = R0;")
    L.append("    NOP;")
    for r in SAVE_REGS:
        if r == "MODE1":
            continue
        L.append(f"    {r} = DM (0x{slot(save_base, r):X});")
    if tail is not None:
        L.append(f"    JUMP 0x{tail:X};")
    else:
        L.append("    I12 = DM (M7,I6);")
        L.append("    JUMP (M14,I12) (DB);")
        L.append("    RFRAME;")
        L.append("    NOP;")
    return "\n".join(L) + "\n"


def module(stubs, save_base):
    out = [".SECTION/SW seg_swco;", ""]
    for name, steps in stubs:
        out.append(gen(name, steps, save_base))
    out.append(".ENDSEG;")
    return "\n".join(out) + "\n"


SAVE_BYTES = 4 * len(SAVE_REGS)
