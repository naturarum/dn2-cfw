# SPDX-License-Identifier: GPL-2.0-or-later
"""Translate digikit-decoded SHARC+ instructions into the core's insn_t records.

Input: (form name, fields, pc, length) from decode.py (digikit's decoder).
Output: a dict of insn_t fields, or an UNIMPL record with a message.

The compute-field tables follow digikit's transcription of the SHARC+ PRM
(vendor/digikit/tools/sharcspec/compute_table.json) and the classic PGR rev 2.4
(chapter 12). Width encodings for Types 3b/3d/4b/4d/14d come from digikit's
ACCESS_WIDTHS table where it fits the DN2 code, and from DN2 evidence where it
does not (see notes/T3.6-sharc-interpreter.md, section "Access widths").
"""
from dataclasses import dataclass, field

# --- constants mirrored from sharcvm.h -------------------------------------
K = dict(UNDECODED=0, UNIMPL=1, NOP=2, IDLE=3, COMPUTE=4, DAG=5, UREG_MOVE=6, SWAP=7,
         UREG_IMM=8, MODIFY=9, JUMP=10, RETURN=11, DO=12, SYSBIT=13, STACK=14, RFRAME=15,
         ACONV=16, SHIFTIMM=17)
IF_DB, IF_CALL, IF_LA, IF_CI, IF_ELSE, IF_CJUMP, IF_LR, IF_RTI = 1, 2, 4, 8, 16, 32, 64, 128
IF_IMM, IF_NW, IF_BITREV, IF_SW = 1, 2, 4, 8
JT_ABS, JT_IND = 0, 1
DO_IMM, DO_UREG, DO_COND = 0, 1, 2
CK_NONE, CK_SINGLE, CK_MULALU, CK_DUAL, CK_MULDUAL = range(5)
AM_PRE, AM_POST, AM_ABS = 0, 1, 2
W_NW, W_LW, W_SW, W_SWSE, W_BW, W_BWSE, W_PX48 = range(7)
MF_IMMDATA, MF_DREG, MF_PM = 1, 2, 4
MOD_RND, MOD_FRAC, MOD_XS, MOD_YS, MOD_IMM = 1, 2, 4, 8, 16
MT_RN, MT_MRF, MT_MRB = 0, 1, 2
MA_NONE, MA_ADD, MA_SUB = 0, 1, 2

OPS = """NONE ADD SUB ADDCI SUBCI AVG COMP COMPU PASS NEG INCCI DECCI INC DEC ABS AND OR XOR NOT
MIN MAX CLIP FADD FSUB FAVG FCOMP FABSADD FABSSUB FPASS FNEG FRND FMANT FABS FSCALB FLOGB
FRECIPS FRSQRTS FIX FLOAT TRUNC FIXBY FLOATBY TRUNCBY FCOPYSIGN FMIN FMAX FCLIP
MUL MRSAT MRRND MRCLR FMUL MR2R R2MR
LSHIFT ASHIFT ROT LSHIFT_OR ASHIFT_OR FEXT FDEP FEXT_SE FDEP_SE FDEP_OR FDEP_OR_SE
BSET BCLR BTGL BTST EXP EXPEX LEFTZ LEFTO FPACK FUNPACK COUNT""".split()
OP = {n: i for i, n in enumerate(OPS)}


class Unimpl(Exception):
    pass


@dataclass
class Slot:
    op: int
    d: int = 0
    x: int = 0
    y: int = 0
    mod: int = 0
    imm: int = 0


@dataclass
class Mem:
    kind: int = 1
    write: int = 0
    ireg: int = 0
    mreg: int = 0xFF
    mode: int = AM_POST
    width: int = W_NW
    ureg: int = 0
    flags: int = 0
    off: int = 0
    data: int = 0


@dataclass
class Insn:
    kind: int
    len: int
    cond: int = 31
    flags: int = 0
    ckind: int = CK_NONE
    a: int = 0
    b: int = 0
    c: int = 0
    imm: int = 0
    imm2: int = 0
    cs: list = field(default_factory=list)
    mem: list = field(default_factory=list)
    pc: int = 0
    msg: str = ""
    text: str = ""          # short description for traces


def sext(v, bits):
    v &= (1 << bits) - 1
    return v - (1 << bits) if v >> (bits - 1) else v


def f(fl, stem):
    """field value by stem: 'cond' matches 'cond' or 'cond[4:0]'; split fields are joined."""
    if stem in fl:
        return fl[stem]
    parts = [(k, v) for k, v in fl.items() if k.split('[')[0] == stem]
    if not parts:
        raise KeyError(stem)
    if len(parts) == 1:
        return parts[0][1]
    val = 0
    for k, v in parts:
        lo = int(k.split('[')[1].rstrip(']').split(':')[1])
        val |= v << lo
    return val


def is_complementary(code):
    return code < 0x10 or 0x50 <= code < 0x60 or code in (0x70, 0x71, 0x7e, 0x7f, 0x76, 0x77, 0x78, 0x79, 0x6c, 0x6d)


# --- compute field -------------------------------------------------------------
ALU_FIXED = {0x01: ('ADD', 'nxy'), 0x02: ('SUB', 'nxy'), 0x05: ('ADDCI', 'nxy'), 0x06: ('SUBCI', 'nxy'),
             0x09: ('AVG', 'nxy'), 0x0a: ('COMP', 'xy'), 0x0b: ('COMPU', 'xy'), 0x21: ('PASS', 'nx'),
             0x22: ('NEG', 'nx'), 0x25: ('INCCI', 'nx'), 0x26: ('DECCI', 'nx'), 0x29: ('INC', 'nx'),
             0x2a: ('DEC', 'nx'), 0x30: ('ABS', 'nx'), 0x40: ('AND', 'nxy'), 0x41: ('OR', 'nxy'),
             0x42: ('XOR', 'nxy'), 0x43: ('NOT', 'nx'), 0x61: ('MIN', 'nxy'), 0x62: ('MAX', 'nxy'),
             0x63: ('CLIP', 'nxy')}
ALU_FLOAT = {0x81: ('FADD', 'nxy'), 0x82: ('FSUB', 'nxy'), 0x89: ('FAVG', 'nxy'), 0x8a: ('FCOMP', 'xy'),
             0x91: ('FABSADD', 'nxy'), 0x92: ('FABSSUB', 'nxy'), 0xa1: ('FPASS', 'nx'), 0xa2: ('FNEG', 'nx'),
             0xa5: ('FRND', 'nx'), 0xad: ('FMANT', 'nx'), 0xb0: ('FABS', 'nx'), 0xbd: ('FSCALB', 'nxy'),
             0xc1: ('FLOGB', 'nx'), 0xc4: ('FRECIPS', 'nx'), 0xc5: ('FRSQRTS', 'nx'), 0xc9: ('FIX', 'nx'),
             0xca: ('FLOAT', 'nx'), 0xcd: ('TRUNC', 'nx'), 0xd9: ('FIXBY', 'nxy'), 0xda: ('FLOATBY', 'nxy'),
             0xdd: ('TRUNCBY', 'nxy'), 0xe0: ('FCOPYSIGN', 'nxy'), 0xe1: ('FMIN', 'nxy'), 0xe2: ('FMAX', 'nxy'),
             0xe3: ('FCLIP', 'nxy')}
SHIFT8 = {0x00: 'LSHIFT', 0x04: 'ASHIFT', 0x08: 'ROT', 0x20: 'LSHIFT_OR', 0x24: 'ASHIFT_OR',
          0x40: 'FEXT', 0x44: 'FDEP', 0x48: 'FEXT_SE', 0x4c: 'FDEP_SE', 0x64: 'FDEP_OR', 0x6c: 'FDEP_OR_SE',
          0xc0: 'BSET', 0xc4: 'BCLR', 0xc8: 'BTGL', 0xcc: 'BTST',
          0x80: 'EXP', 0x84: 'EXPEX', 0x88: 'LEFTZ', 0x8c: 'LEFTO'}
SHIFT_NO_Y = {'EXP', 'EXPEX', 'LEFTZ', 'LEFTO'}
SHIFT_NO_D = {'BTST'}
SHIFT6 = {0x00: 'LSHIFT', 0x01: 'ASHIFT', 0x02: 'ROT', 0x08: 'LSHIFT_OR', 0x09: 'ASHIFT_OR',
          0x10: 'FEXT', 0x11: 'FDEP', 0x12: 'FEXT_SE', 0x13: 'FDEP_SE', 0x1b: 'FDEP_OR', 0x1d: 'FDEP_OR_SE',
          0x30: 'BSET', 0x31: 'BCLR', 0x32: 'BTGL', 0x33: 'BTST'}


def alu_slot(opc, rn, rx, ry):
    tab = ALU_FLOAT if opc & 0x80 else ALU_FIXED
    if opc not in tab:
        raise Unimpl(f"ALU opcode {opc:#04x}")
    name, shape = tab[opc]
    return Slot(OP[name], d=rn, x=rx, y=ry)


def mul_mod(bits8):
    """MOD1 from a multiplier opcode: 01yx Fttr"""
    m = 0
    if bits8 & 0x20: m |= MOD_YS
    if bits8 & 0x10: m |= MOD_XS
    if bits8 & 0x08: m |= MOD_FRAC
    if bits8 & 0x01: m |= MOD_RND
    return m


def decode_compute(c):
    """23-bit compute field -> (ckind, [Slot]). Raises Unimpl."""
    if c == 0:
        return CK_NONE, []
    if not (c >> 22) & 1:
        cu = (c >> 20) & 3
        opc = (c >> 12) & 0xff
        rn, rx, ry = (c >> 8) & 15, (c >> 4) & 15, c & 15
        if cu == 0:
            if (opc >> 4) in (0x7, 0xf):
                rs = opc & 15
                fl = (opc >> 4) == 0xf
                return CK_DUAL, [Slot(OP['FADD' if fl else 'ADD'], d=rn, x=rx, y=ry),
                                 Slot(OP['FSUB' if fl else 'SUB'], d=rs, x=rx, y=ry)]
            return CK_SINGLE, [alu_slot(opc, rn, rx, ry)]
        if cu == 1:
            if opc == 0x30:
                return CK_SINGLE, [Slot(OP['FMUL'], d=rn, x=rx, y=ry)]
            if opc in (0x31, 0x32, 0x33):
                raise Unimpl(f"64-bit float multiply {opc:#04x}")
            if opc == 0x14:
                return CK_SINGLE, [Slot(OP['MRCLR'], imm=MT_MRF)]
            if opc == 0x16:
                return CK_SINGLE, [Slot(OP['MRCLR'], imm=MT_MRB)]
            if (opc >> 4) == 0:      # 0000 F t t s : SAT
                t = (opc >> 1) & 3
                src, tgt = [(MT_MRF, MT_RN), (MT_MRB, MT_RN), (MT_MRF, MT_MRF), (MT_MRB, MT_MRB)][t]
                mod = (MOD_FRAC if opc & 8 else 0) | ((MOD_XS | MOD_YS) if opc & 1 else 0)
                return CK_SINGLE, [Slot(OP['MRSAT'], d=rn, mod=mod, imm=src | (tgt << 2))]
            if (opc >> 3) == 0b00011:  # 0001 1 t t s : RND
                t = (opc >> 1) & 3
                src, tgt = [(MT_MRF, MT_RN), (MT_MRB, MT_RN), (MT_MRF, MT_MRF), (MT_MRB, MT_MRB)][t]
                mod = MOD_FRAC | ((MOD_XS | MOD_YS) if opc & 1 else 0)
                return CK_SINGLE, [Slot(OP['MRRND'], d=rn, mod=mod, imm=src | (tgt << 2))]
            grp = opc >> 6
            t = (opc >> 1) & 3
            mod = mul_mod(opc)
            if grp == 1:
                tgt = {0: MT_RN, 2: MT_MRF, 3: MT_MRB}.get(t)
                if tgt is None:
                    raise Unimpl(f"multiplier opcode {opc:#04x}")
                return CK_SINGLE, [Slot(OP['MUL'], d=rn, x=rx, y=ry, mod=mod, imm=tgt)]
            if grp in (2, 3):
                acc = MA_ADD if grp == 2 else MA_SUB
                tgt, src = [(MT_RN, MT_MRF), (MT_RN, MT_MRB), (MT_MRF, MT_MRF), (MT_MRB, MT_MRB)][t]
                return CK_SINGLE, [Slot(OP['MUL'], d=rn, x=rx, y=ry, mod=mod, imm=tgt | (acc << 2) | (src << 4))]
            raise Unimpl(f"multiplier opcode {opc:#04x}")
        if cu == 2:
            name = SHIFT8.get(opc)
            if name is None:
                raise Unimpl(f"shifter opcode {opc:#04x}")
            return CK_SINGLE, [Slot(OP[name], d=rn, x=rx, y=ry)]
        raise Unimpl("compute unit 3")
    # multifunction
    if (c >> 17) == 0b100000:
        d = (c >> 16) & 1
        part = (c >> 12) & 15
        rn = (c >> 8) & 15
        if (c & 0xff) != 0:
            raise Unimpl(f"MR data move with low bits {c & 0xff:#x}")
        mp = {0: (0, MT_MRF), 1: (1, MT_MRF), 2: (2, MT_MRF), 4: (0, MT_MRB), 5: (1, MT_MRB), 6: (2, MT_MRB)}
        if part not in mp:
            raise Unimpl(f"MR data move register {part}")
        p, mr = mp[part]
        if d:
            return CK_SINGLE, [Slot(OP['R2MR'], x=rn, imm=p | (mr << 2))]
        return CK_SINGLE, [Slot(OP['MR2R'], d=rn, imm=p | (mr << 2))]
    op6 = (c >> 16) & 0x3f
    rm, ra = (c >> 12) & 15, (c >> 8) & 15
    xm, ym, xa, ya = (c >> 6) & 3, 4 + ((c >> 4) & 3), 8 + ((c >> 2) & 3), 12 + (c & 3)
    if (op6 >> 4) in (0b10, 0b11):
        rs = (c >> 16) & 15
        fl = (op6 >> 4) == 0b11
        if fl:
            m = Slot(OP['FMUL'], d=rm, x=xm, y=ym)
        else:
            m = Slot(OP['MUL'], d=rm, x=xm, y=ym, mod=MOD_XS | MOD_YS | MOD_FRAC | MOD_RND, imm=MT_RN)
        return CK_MULDUAL, [m, Slot(OP['FADD' if fl else 'ADD'], d=ra, x=xa, y=ya),
                            Slot(OP['FSUB' if fl else 'SUB'], d=rs, x=xa, y=ya)]
    if (op6 & 0b110011) == 0b000011:
        raise Unimpl("64-bit float multifunction")
    if (op6 >> 3) == 0b011:
        alu = op6 & 7
        name = ['FADD', 'FSUB', 'FLOATBY', 'FIXBY', 'FAVG', 'FABS', 'FMAX', 'FMIN'][alu]
        return CK_MULALU, [Slot(OP['FMUL'], d=rm, x=xm, y=ym), Slot(OP[name], d=ra, x=xa, y=ya)]
    mv, alu = op6 >> 2, op6 & 3
    if alu == 3 or mv not in (1, 2, 3, 4, 5):
        raise Unimpl(f"multifunction opcode {op6:#04x}")
    ssf = MOD_XS | MOD_YS | MOD_FRAC
    if mv == 1:
        m = Slot(OP['MUL'], d=rm, x=xm, y=ym, mod=ssf | MOD_RND, imm=MT_RN)
    elif mv == 2:
        m = Slot(OP['MUL'], d=rm, x=xm, y=ym, mod=ssf, imm=MT_MRF | (MA_ADD << 2) | (MT_MRF << 4))
    elif mv == 3:
        m = Slot(OP['MUL'], d=rm, x=xm, y=ym, mod=ssf | MOD_RND, imm=MT_RN | (MA_ADD << 2) | (MT_MRF << 4))
    elif mv == 4:
        m = Slot(OP['MUL'], d=rm, x=xm, y=ym, mod=ssf, imm=MT_MRF | (MA_SUB << 2) | (MT_MRF << 4))
    else:
        m = Slot(OP['MUL'], d=rm, x=xm, y=ym, mod=ssf | MOD_RND, imm=MT_RN | (MA_SUB << 2) | (MT_MRF << 4))
    a = Slot(OP[['ADD', 'SUB', 'AVG'][alu]], d=ra, x=xa, y=ya)
    return CK_MULALU, [m, a]


SHORT = {0: ('ADD', 'nnx'), 1: ('SUB', 'nnx'), 2: ('PASS', 'nx'), 3: ('COMP', 'nx'), 4: ('NOT', 'nx'),
         5: ('INC', 'nx'), 6: ('DEC', 'nx'), 7: ('MULSSI', 'nnx'), 8: ('FADD', 'nnx'), 9: ('FSUB', 'nnx'),
         10: ('FLOAT', 'nx'), 11: ('FCOMP', 'nx'), 12: ('AND', 'nnx'), 13: ('OR', 'nnx'), 14: ('XOR', 'nnx'),
         15: ('FMUL', 'nnx')}


def decode_short_compute(c12):
    op, rn, rx = (c12 >> 8) & 15, (c12 >> 4) & 15, c12 & 15
    name, shape = SHORT[op]
    if name == 'MULSSI':
        return CK_SINGLE, [Slot(OP['MUL'], d=rn, x=rn, y=rx, mod=MOD_XS | MOD_YS, imm=MT_RN)]
    if shape == 'nnx':
        return CK_SINGLE, [Slot(OP[name], d=rn, x=rn, y=rx)]
    if name in ('COMP', 'FCOMP'):
        return CK_SINGLE, [Slot(OP[name], x=rn, y=rx)]
    return CK_SINGLE, [Slot(OP[name], d=rn, x=rx)]


def decode_shiftimm(si, dataex):
    op6 = (si >> 16) & 0x3f
    if (si >> 22) & 1:
        raise Unimpl("shift-immediate with bit 22 set")
    data8 = (si >> 8) & 0xff
    rn, rx = (si >> 4) & 15, si & 15
    name = SHIFT6.get(op6)
    if name is None:
        raise Unimpl(f"shift-immediate opcode {op6:#04x}")
    imm = ((dataex & 15) << 8) | data8
    return CK_SINGLE, [Slot(OP[name], d=rn, x=rx, mod=MOD_IMM, imm=imm)]


# --- access widths ------------------------------------------------------------
# Type 3b/3d: digikit ACCESS_WIDTHS keyed (l, x, w); (1,1,1) = long word (checked
# on DN2: 0xb82976 loads R0:R1 pairs from the reverb tables).
W3 = {(0, 1, 1): W_NW, (0, 0, 0): W_BW, (0, 1, 0): W_BWSE, (1, 0, 0): W_SW, (1, 1, 0): W_SWSE, (1, 1, 1): W_LW}
# Type 4b/4d: bits (18,17,16) = (l, w, x) in digikit's names. 111 is the plain normal
# word here (DN2 0xb81a54: 7-word struct stride, values reused as 32-bit words),
# 000 byte, 001 byte sign-extended, 100 short, 101 short sign-extended.
W4 = {(1, 1, 1): W_NW, (0, 0, 0): W_BW, (0, 0, 1): W_BWSE, (1, 0, 0): W_SW, (1, 0, 1): W_SWSE}
# Type 14d (absolute, sub-word): (l, w, x): l = short, x = sign-extend (DN2 render 0x1c2888 ...).
W14 = {(1, 0, 0): W_SW, (1, 0, 1): W_SWSE, (0, 0, 0): W_BW, (0, 0, 1): W_BWSE}


def width_3b(fl):
    key = (f(fl, 'l'), f(fl, 'x'), f(fl, 'w'))
    if key not in W3:
        raise Unimpl(f"Type 3b/3d width bits l,x,w={key}")
    return W3[key]


def width_4b(fl):
    key = (f(fl, 'l'), f(fl, 'w'), f(fl, 'x'))
    if key not in W4:
        raise Unimpl(f"Type 4b/4d width bits l,w,x={key}")
    return W4[key]


def width_14d(fl):
    key = (f(fl, 'l'), f(fl, 'w'), f(fl, 'x'))
    if f(fl, 'ex'):
        raise Unimpl("Type 14d ex bit")
    if key not in W14:
        raise Unimpl(f"Type 14d width bits l,w,x={key}")
    return W14[key]


def dreg_flags(code):
    return MF_DREG if is_complementary(code) else 0


# --- forms ----------------------------------------------------------------------
def with_compute(ins, c, short=False):
    ck, slots = decode_short_compute(c) if short else decode_compute(c)
    ins.ckind, ins.cs = ck, slots
    return ins


def translate(form, fl, pc, nparcels, raw=0):
    """-> Insn (raises Unimpl). raw = the instruction word in its own width."""
    L = nparcels
    t = form
    if t in ('21a', '21c'):
        return Insn(K['NOP'], L)
    if t == 'IDLE16':
        return Insn(K['IDLE'], L)
    if t in ('2a', '2a_short', '2b'):
        c = f(fl, 'compute')
        cond = f(fl, 'cond') if t == '2a' else 31
        return with_compute(Insn(K['COMPUTE'], L, cond=cond), c)
    if t == '2c':
        return with_compute(Insn(K['COMPUTE'], L), f(fl, 'compute'), short=True)
    if t in ('5a_move', '5b_move'):
        src = (f(fl, 'srcureghigh') << 2) | (fl['srcureglow[1:1]'] << 1) | fl['srcureglow[0:0]']
        ins = Insn(K['UREG_MOVE'], L, cond=f(fl, 'cond'), a=f(fl, 'dstureg'), b=src)
        if t == '5a_move':
            with_compute(ins, f(fl, 'compute'))
        return ins
    if t in ('5a (swap)', '5b (swap)', '5a_swap', '5b_swap'):
        ins = Insn(K['SWAP'], L, cond=f(fl, 'cond'), a=f(fl, 'dreg'), b=f(fl, 'cdreg'))
        if t.startswith('5a'):
            with_compute(ins, f(fl, 'compute'))
        return ins
    if t in ('17a', '17b'):
        v = f(fl, 'data') if t == '17a' else sext(f(fl, 'data'), 16)
        return Insn(K['UREG_IMM'], L, a=f(fl, 'ureg'), imm=v & 0xffffffff)
    if t in ('1a', '1b'):
        ins = Insn(K['DAG'], L)
        dreg, preg = f(fl, 'dmdreg'), f(fl, 'pmdreg')
        ins.mem = [Mem(write=f(fl, 'dmd'), ireg=f(fl, 'dmi'), mreg=f(fl, 'dmm'), mode=AM_POST, ureg=dreg, flags=MF_DREG),
                   Mem(write=f(fl, 'pmd'), ireg=8 + f(fl, 'pmi'), mreg=8 + f(fl, 'pmm'), mode=AM_POST, ureg=preg,
                       flags=MF_DREG | MF_PM)]
        if t == '1a':
            with_compute(ins, f(fl, 'compute'))
        return ins
    if t in ('3a', '3b', '3d'):
        g = f(fl, 'g')
        ureg = f(fl, 'ureg')
        if t == '3a':
            width = W_LW if f(fl, 'l') else W_NW
        else:
            if t == '3d' and f(fl, 'ex'):
                raise Unimpl("Type 3d ex bit")
            width = width_3b(fl)
        if ureg == 0x6b:
            if f(fl, 'l'):
                raise Unimpl("PX long-word transfer")
            width = W_PX48
        m = Mem(write=f(fl, 'd'), ireg=f(fl, 'i') + 8 * g, mreg=f(fl, 'm') + 8 * g,
                mode=AM_POST if f(fl, 'u') else AM_PRE, width=width, ureg=ureg,
                flags=dreg_flags(ureg) | (MF_PM if g else 0))
        ins = Insn(K['DAG'], L, cond=f(fl, 'cond'), mem=[m])
        if t == '3a':
            with_compute(ins, f(fl, 'compute'))
        return ins
    if t == '3c':
        m = Mem(write=f(fl, 'd'), ireg=f(fl, 'dmi'), mreg=f(fl, 'dmm'), mode=AM_POST, ureg=f(fl, 'dreg'), flags=MF_DREG)
        return Insn(K['DAG'], L, mem=[m])
    if t in ('4a', '4b', '4d'):
        g = f(fl, 'g')
        data = sext(f(fl, 'data'), 6)
        width = W_NW if t == '4a' else width_4b(fl)
        dreg = f(fl, 'dreg')
        m = Mem(write=f(fl, 'd'), ireg=f(fl, 'i') + 8 * g, mreg=0xFF, off=data,
                mode=AM_POST if f(fl, 'u') else AM_PRE, width=width, ureg=dreg,
                flags=MF_DREG | (MF_PM if g else 0))
        ins = Insn(K['DAG'], L, cond=f(fl, 'cond'), mem=[m])
        if t == '4a':
            with_compute(ins, f(fl, 'compute'))
        return ins
    if t == '14a':
        ureg = f(fl, 'ureg')
        width = W_LW if f(fl, 'l') else W_NW
        if ureg == 0x6b:
            if f(fl, 'l'):
                raise Unimpl("PX long-word absolute transfer")
            width = W_PX48
        m = Mem(write=f(fl, 'd'), mode=AM_ABS, off=f(fl, 'addr') & 0xffffffff,
                width=width, ureg=ureg, flags=dreg_flags(ureg))
        return Insn(K['DAG'], L, mem=[m])
    if t == '14d':
        dreg = f(fl, 'dreg')
        m = Mem(write=f(fl, 'd'), mode=AM_ABS, off=f(fl, 'addr') & 0xffffffff, width=width_14d(fl), ureg=dreg,
                flags=MF_DREG)
        return Insn(K['DAG'], L, mem=[m])
    if t in ('15a', '15b'):
        g = f(fl, 'g')
        ureg = f(fl, 'ureg')
        off = sext(f(fl, 'addr'), 32) if t == '15a' else sext(f(fl, 'data'), 7)
        if ureg == 0x6b:
            raise Unimpl("PX transfer with immediate offset")
        m = Mem(write=f(fl, 'd'), ireg=f(fl, 'i') + 8 * g, mreg=0xFF, off=off, mode=AM_PRE,
                width=W_LW if f(fl, 'l') else W_NW, ureg=ureg, flags=dreg_flags(ureg) | (MF_PM if g else 0))
        return Insn(K['DAG'], L, mem=[m])
    if t in ('16a', '16b'):
        g = f(fl, 'g')
        if t == '16a' and (f(fl, 'sl') or f(fl, 'by')):
            raise Unimpl(f"Type 16a sl/by bits {f(fl, 'sl')}/{f(fl, 'by')}")
        data = f(fl, 'data') if t == '16a' else sext(f(fl, 'data'), 16) & 0xffffffff
        m = Mem(write=1, ireg=f(fl, 'i') + 8 * g, mreg=f(fl, 'm') + 8 * g, mode=AM_POST, width=W_NW,
                flags=MF_IMMDATA, data=data)
        return Insn(K['DAG'], L, mem=[m])
    if t == '6a (mem)' or t == '6a_mem':
        g = f(fl, 'g')
        dreg = f(fl, 'dreg')
        m = Mem(write=f(fl, 'd'), ireg=f(fl, 'i') + 8 * g, mreg=f(fl, 'm') + 8 * g, mode=AM_POST, ureg=dreg,
                flags=MF_DREG)
        ins = Insn(K['DAG'], L, cond=f(fl, 'cond'), mem=[m])
        ins.ckind, ins.cs = decode_shiftimm(f(fl, 'shiftimm'), f(fl, 'dataex'))
        return ins
    if t in ('6a (nomem)', '6a_nomem', '6b_shiftimm'):
        ins = Insn(K['SHIFTIMM'], L, cond=f(fl, 'cond'))
        ins.ckind, ins.cs = decode_shiftimm(f(fl, 'shiftimm'), f(fl, 'dataex'))
        return ins
    if t in ('7a', '7b'):
        g = 8 if f(fl, 'g') else 0
        src = f(fl, 'is')
        dst = src ^ f(fl, 'idis')
        ins = Insn(K['MODIFY'], L, cond=f(fl, 'cond'), a=dst + g, b=src + g, c=f(fl, 'm') + g)
        # Scaling options, bits digikit's table leaves unlabelled: frame bit 39 = (NW)
        # (selache: `i7=modify (i7,m7)(nw)`; stack frames), frame bit 23 = short-word
        # scaling (DN2 render 0x1c2cc2/0x1c3a9f step 16-bit frame words; reverb tank
        # 0xb82e43 reaches its second descriptor set only with x2). [inferred] Both set: meaning
        # unknown; the core traps unless the M register is 0 (DSP boot 0x1c0807 has M9 = 0).
        frame = raw << 16 if L == 2 else raw
        b39, b23 = (frame >> 39) & 1, (frame >> 23) & 1
        if b39:
            ins.flags |= IF_NW
        if b23:
            ins.flags |= IF_SW
        if t == '7a':
            with_compute(ins, f(fl, 'compute'))
        return ins
    if t in ('19a', '19a_scaled', '19a_bitrev'):
        g = 8 if f(fl, 'g') else 0
        src = f(fl, 'is')
        dst = src ^ f(fl, 'idis')
        ins = Insn(K['MODIFY'], L, a=dst + g, b=src + g, imm=sext(f(fl, 'data'), 32), flags=IF_IMM)
        if t == '19a_scaled':
            if not f(fl, 'w'):
                raise Unimpl("Type 19a short-word scaled modify")
            ins.flags |= IF_NW
        if t == '19a_bitrev':
            ins.flags |= IF_BITREV
        return ins
    if t in ('8a_abs', '8a_rel'):
        if t == '8a_abs':
            tgt = f(fl, 'addr')
        else:
            tgt = (pc + sext(f(fl, 'reladdr'), 24)) & 0xffffff
        flags = (IF_DB if f(fl, 'j') else 0) | (IF_CALL if f(fl, 'b') else 0) | (IF_LA if f(fl, 'a') else 0) | \
                (IF_CI if f(fl, 'ci') else 0)
        if f(fl, 'ci'):
            raise Unimpl("JUMP (CI)")
        return Insn(K['JUMP'], L, cond=f(fl, 'cond'), flags=flags, a=JT_ABS, imm=tgt)
    if t in ('9a_abs', '9a_rel', '9b_abs', '9b_rel'):
        flags = (IF_DB if f(fl, 'j') else 0) | (IF_CALL if f(fl, 'b') else 0) | (IF_LA if f(fl, 'a') else 0)
        if f(fl, 'ci'):
            raise Unimpl("indirect JUMP (CI)")
        if t.endswith('abs'):
            ins = Insn(K['JUMP'], L, cond=f(fl, 'cond'), flags=flags, a=JT_IND,
                       b=8 + f(fl, 'pmi'), c=8 + f(fl, 'pmm'))
        else:
            # 6-bit PC-relative offset, sign-extended at bit 5 (digikit's flow code
            # does not sign-extend it; T3.1b section 2.6)
            ins = Insn(K['JUMP'], L, cond=f(fl, 'cond'), flags=flags, a=JT_ABS,
                       imm=(pc + sext(f(fl, 'reladdr'), 6)) & 0xffffff)
        if t.startswith('9a'):
            if f(fl, 'e'):
                ins.flags |= IF_ELSE
            with_compute(ins, f(fl, 'compute'))
        return ins
    if t in ('11a', '11c'):
        flags = (IF_DB if f(fl, 'j') else 0) | (IF_LR if f(fl, 'lr') else 0) | (IF_RTI if f(fl, 'x') else 0)
        ins = Insn(K['RETURN'], L, cond=f(fl, 'cond'), flags=flags)
        if t == '11a':
            if f(fl, 'e'):
                ins.flags |= IF_ELSE
            with_compute(ins, f(fl, 'compute'))
        return ins
    if t in ('12a_imm', '12a_ureg'):
        rel = sext(f(fl, 'reladdr'), 23)
        end = (pc + rel) & 0xffffff
        if t == '12a_imm':
            return Insn(K['DO'], L, a=DO_IMM, imm=end, imm2=f(fl, 'data'))
        return Insn(K['DO'], L, a=DO_UREG, b=f(fl, 'ureg'), imm=end)
    if t == '13a':
        rel = sext(f(fl, 'reladdr'), 23)
        term = f(fl, 'term')
        if term == 31:
            raise Unimpl("DO FOREVER")
        return Insn(K['DO'], L, a=DO_COND, c=term, imm=(pc + rel) & 0xffffff)
    if t == '18a':
        bop = f(fl, 'bop')
        if bop not in (0, 1, 2, 4, 5):
            raise Unimpl(f"Type 18a bop {bop}")
        return Insn(K['SYSBIT'], L, a=0x70 + f(fl, 'sreg'), b=bop, imm=f(fl, 'data') & 0xffffffff)
    if t == '20a':
        bits = 0
        for i, n in enumerate(('lpu', 'lpo', 'spu', 'spo', 'ppu', 'ppo', 'fc')):
            if fl.get(n):
                bits |= 1 << i
        for n in ('llii', 'lldwb', 'lldi', 'llpwb', 'llpi'):
            if fl.get(n):
                bits |= 0x80   # unsupported
        if bits & 0x80:
            raise Unimpl("Type 20a cache/lock options " + ",".join(n for n in ('llii', 'lldwb', 'lldi', 'llpwb', 'llpi') if fl.get(n)))
        return Insn(K['STACK'], L, imm=bits)
    if t in ('25a_direct', '25a_pcrel'):
        tgt = f(fl, 'addr') if t == '25a_direct' else (pc + sext(f(fl, 'reladdr'), 24)) & 0xffffff
        return Insn(K['JUMP'], L, flags=IF_DB | IF_CJUMP, a=JT_ABS, imm=tgt)
    if t in ('25a_rframe', '25c_rframe'):
        return Insn(K['RFRAME'], L)
    if t == '7d':
        # ACONV (PRM Table 6-4 as quoted by digikit's tracer): B2W "Id <- Is >> 2", W2B
        # "Id <- Is << 2"; breg selects the B registers; destination = is XOR idis.
        g = 8 if f(fl, 'g') else 0
        src = f(fl, 'is')
        return Insn(K['ACONV'], L, a=(src ^ f(fl, 'idis')) + g, b=src + g, c=f(fl, 'breg'), imm=f(fl, 'toby'))
    raise Unimpl(f"form {t}")
