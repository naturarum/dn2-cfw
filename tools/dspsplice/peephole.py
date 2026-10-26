#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""DC01: a peephole pass over selcc's assembly, shorter struct field accesses with the same behaviour
(notes/DC01-compiler-fix.md). It works on selcc's -S output before selas; selcc, selas and seld are unchanged, and
only a recipe with the key `peephole` (tools/dspsplice/build.py) uses it.

selcc computes every field address with an immediate load, an add and a move to the scratch index register I4, then
accesses DM(I4,M5) (notes/DT01-code-diet.md section 4). The patterns:

  A  the constant offset folded into the access (the SHARC+ immediate form, pre-modify without update; for a normal
     word through a byte-space pointer the offset counts words):
         R5 = 0x3C;  R6 = R3 + R5;  I4=R6;  DM (I4,M5)=R1;     ->   I4=R3;  DM (0xF,I4)=R1;
     nested offsets (struct in struct, array in struct) add up; the stack arguments selcc reads through I4,
         I4=I6;  I4=MODIFY (I4,0x1)(NW);  R5=DM (I4,M5);        ->   R5=DM (0x1,I6);
  B  the base loaded straight into I4 when it comes from the frame:
         R3=DM (-0x7,I6);  ...  I4=R3;                          ->   I4=DM (-0x7,I6);  ...
  s  (a mode flag, DC02: patterns "ABs") stock-proven pairings only: B is refused where the instruction right after the
     new load into I4 is a 48-bit instruction that uses I4 (a compute with the access, an offset outside -64..63
     words, or anything in a DO loop body, which selas emits unpacked). In the stock 1.12 code a load into an I
     register is followed directly by a 16- or 32-bit user hundreds of times, by a 48-bit one never (there is always
     other work in between: the review of DC01).

Safety: a rewrite is kept only if the whole basic block it is in, symbolically executed before and after, gives the
same memory accesses in the same order (addresses, stored values), the same branch decisions, and the same value in
every register (and ALU, multiplier, shifter flags) that is live at the block's exit. Liveness is computed over the
whole program (every function of every file passed in, calls through the callee's own live-in set, returns through
the union of what is live after each call site; a function called from outside, e.g. from a hook stub, returns with
everything live but what the caller restores). After all rewrites the program is parsed again, liveness recomputed
on it, and every changed block checked again against selcc's own block; a block that fails is put back. Anything the
parser does not know (an instruction form, a jump out of the function) makes its function untouchable.

One assumption, the same one selcc's frame accesses rest on: DM(k, Ia) for a normal word addresses Ia + 4k when Ia
holds a byte-space address (sharcvm's rule, verified by the DN2 code; the stock code itself reads and writes struct
fields this way in the 32-bit form selas emits, e.g. 0xb81206 `r0=dm (0x15,i4)` 98080015 and 0xb81211
`dm (-0x7,i4)=r0` 99080079). The base of a folded access is the address selcc dereferenced minus a small constant (a
field offset, never an absolute address), and every struct of ours is word aligned in L1 or DDR.

    python3 tools/dspsplice/peephole.py a.s b.s ... [--rewrite a,b] [--patterns AB|ABs] [--out DIR]
"""
import argparse
import re
import sys
from pathlib import Path

M32 = 0xFFFFFFFF
RREGS = tuple(f"R{i}" for i in range(16))
IREGS = tuple(f"I{i}" for i in range(16))
MREGS = tuple(f"M{i}" for i in range(16))
LREGS = tuple(f"L{i}" for i in range(16))
BREGS = tuple(f"B{i}" for i in range(16))
FLAGS = ("AF", "MF", "SF", "CACC")          # ALU flags, multiplier flags, shifter flags, compare accumulator
ALL = frozenset(RREGS + IREGS + MREGS + LREGS + BREGS + FLAGS + ("SYS",))
NONE = frozenset()

# conditions and the flags they read (SHARC+ condition codes)
COND_FLAGS = {"EQ": {"AF"}, "NE": {"AF"}, "GT": {"AF"}, "GE": {"AF"}, "LT": {"AF"}, "LE": {"AF"},
              "AC": {"AF"}, "NOT AC": {"AF"}, "AV": {"AF"}, "NOT AV": {"AF"},
              "MV": {"MF"}, "NOT MV": {"MF"}, "MS": {"MF"}, "NOT MS": {"MF"},
              "SV": {"SF"}, "NOT SV": {"SF"}, "SZ": {"SF"}, "NOT SZ": {"SF"}}

NUM = r"-?(?:0x[0-9A-Fa-f]+|\d+)"
LBL = r"\.L_[A-Za-z0-9_]+"
SYMR = r"[A-Za-z_][A-Za-z0-9_]*\."
FUNC_LABEL = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\.:\s*$")
LOCAL_LABEL = re.compile(r"^\s*(\.L_[A-Za-z0-9_]+):\s*$")


def rn(letter, n):
    """a data register's name (F and R are the same register)"""
    return ("R" if letter in "RF" else letter) + str(int(n))


def hexs(v):
    """selcc's number style"""
    return "-0x%X" % -v if v < 0 else "0x%X" % v


# ---------------------------------------------------------------------------------------------------------------------
# instructions

class Insn:
    """one instruction: its text, its form, the registers it reads, writes for sure and maybe writes, its control
    effect. form: a tuple, see parse(); kind: 'seq', 'jump', 'cjmp' (conditional jump), 'call', 'ret', 'do',
    'unknown'"""
    __slots__ = ("text", "form", "kind", "reads", "writes", "cwrites", "target", "delayed", "li")

    def __init__(self, text, form, kind="seq", reads=NONE, writes=NONE, cwrites=NONE, target=None, delayed=False,
                 li=None):
        self.text, self.form, self.kind = text, form, kind
        self.reads, self.writes, self.cwrites = frozenset(reads), frozenset(writes), frozenset(cwrites)
        self.target, self.delayed, self.li = target, delayed, li

    def __repr__(self):
        return "Insn(%r)" % self.text


ALU_OPS = {"ADD", "SUB", "AND", "OR", "XOR", "PASS", "NOT", "FADD", "FSUB", "FNEG", "RECIPS", "FLOAT", "TRUNC",
           "COMP", "COMPU", "FCOMP"}
MUL_OPS = {"FMUL", "IMUL"}
SHIFT_OPS = {"LSHIFT", "ASHIFT"}

_COMP_PATTERNS = [
    (re.compile(r"^([RF])(\d+)\s*=\s*([RF])(\d+)\s*([+\-*])\s*([RF])(\d+)(\s*\(SSI\))?$"), "arith"),
    (re.compile(r"^R(\d+)\s*=\s*R(\d+)\s+(AND|OR|XOR)\s+R(\d+)$"), "logic"),
    (re.compile(r"^R(\d+)\s*=\s*(PASS|NOT)\s+R(\d+)$"), "unary"),
    (re.compile(r"^R(\d+)\s*=\s*(LSHIFT|ASHIFT)\s+R(\d+)\s+BY\s+R(\d+)$"), "shift"),
    (re.compile(r"^F(\d+)\s*=\s*-F(\d+)$"), "fneg"),
    (re.compile(r"^F(\d+)\s*=\s*RECIPS\s+F(\d+)$"), "recips"),
    (re.compile(r"^F(\d+)\s*=\s*FLOAT\s+R(\d+)$"), "float"),
    (re.compile(r"^R(\d+)\s*=\s*TRUNC\s+F(\d+)$"), "trunc"),
    (re.compile(r"^(COMPU?)\s*\(\s*([RF])(\d+)\s*,\s*([RF])(\d+)\s*\)$"), "comp"),
]


def parse_compute(s):
    """-> ('comp', op, dst or None, (srcs)) or None"""
    for rx, kind in _COMP_PATTERNS:
        m = rx.match(s)
        if not m:
            continue
        g = m.groups()
        if kind == "arith":
            L, d, Lx, x, op, Ly, y, ssi = g
            if not (L == Lx == Ly):
                return None
            if L == "R":
                opn = {"+": "ADD", "-": "SUB", "*": "IMUL" if ssi else None}[op]
                if opn is None or (op != "*" and ssi):
                    return None
            else:
                if ssi:
                    return None
                opn = {"+": "FADD", "-": "FSUB", "*": "FMUL"}[op]
            return ("comp", opn, rn("R", d), (rn("R", x), rn("R", y)))
        if kind == "logic":
            return ("comp", g[2], rn("R", g[0]), (rn("R", g[1]), rn("R", g[3])))
        if kind == "unary":
            return ("comp", g[1], rn("R", g[0]), (rn("R", g[2]),))
        if kind == "shift":
            return ("comp", g[1], rn("R", g[0]), (rn("R", g[2]), rn("R", g[3])))
        if kind == "fneg":
            return ("comp", "FNEG", rn("R", g[0]), (rn("R", g[1]),))
        if kind == "recips":
            return ("comp", "RECIPS", rn("R", g[0]), (rn("R", g[1]),))
        if kind == "float":
            return ("comp", "FLOAT", rn("R", g[0]), (rn("R", g[1]),))
        if kind == "trunc":
            return ("comp", "TRUNC", rn("R", g[0]), (rn("R", g[1]),))
        if kind == "comp":
            op, L1, a, L2, b = g
            if L1 != L2 or (op == "COMPU" and L1 != "R"):
                return None
            return ("comp", "FCOMP" if L1 == "F" else op, None, (rn("R", a), rn("R", b)))
    return None


def compute_effects(c):
    """(reads, writes) of a compute form"""
    _, op, d, srcs = c
    w = set()
    if d:
        w.add(d)
    if op in ALU_OPS:
        w.add("AF")
        if op in ("COMP", "COMPU", "FCOMP"):
            w.add("CACC")
    elif op in MUL_OPS:
        w.add("MF")
    elif op in SHIFT_OPS:
        w.add("SF")
    else:
        raise ValueError(op)
    return set(srcs), w


_MEM_PATTERNS = [
    (re.compile(r"^([RFI])(\d+)\s*=\s*DM\s*\(\s*(" + NUM + r")\s*,\s*I(\d+)\s*\)$"), "ldi"),
    (re.compile(r"^DM\s*\(\s*(" + NUM + r")\s*,\s*I(\d+)\s*\)\s*=\s*([RF])(\d+)$"), "sti"),
    (re.compile(r"^([RF])(\d+)\s*=\s*DM\s*\(\s*I(\d+)\s*,\s*M(\d+)\s*\)$"), "ldm"),
    (re.compile(r"^DM\s*\(\s*I(\d+)\s*,\s*M(\d+)\s*\)\s*=\s*([RF])(\d+)$"), "stm"),
    (re.compile(r"^DM\s*\(\s*I(\d+)\s*,\s*M(\d+)\s*\)\s*=\s*(" + LBL + r"-\d+)$"), "stmimm"),
    (re.compile(r"^I(\d+)\s*=\s*DM\s*\(\s*M(\d+)\s*,\s*I(\d+)\s*\)$"), "ldpm"),
]


def parse_mem(s):
    """-> ('ldi', dst, k, I) | ('sti', src, k, I) | ('ldm', dst, I, M) | ('stm', src, I, M) | ('stmimm', text, I, M) |
    ('ldpm', dst, M, I) or None (normal-word accesses only: no width suffix)"""
    for rx, kind in _MEM_PATTERNS:
        m = rx.match(s)
        if not m:
            continue
        g = m.groups()
        if kind == "ldi":
            return ("ldi", rn(g[0], g[1]), int(g[2], 0), "I" + g[3])
        if kind == "sti":
            return ("sti", rn(g[2], g[3]), int(g[0], 0), "I" + g[1])
        if kind == "ldm":
            return ("ldm", rn(g[0], g[1]), "I" + g[2], "M" + g[3])
        if kind == "stm":
            return ("stm", rn(g[2], g[3]), "I" + g[0], "M" + g[1])
        if kind == "stmimm":
            return ("stmimm", g[2], "I" + g[0], "M" + g[1])
        if kind == "ldpm":
            return ("ldpm", "I" + g[0], "M" + g[1], "I" + g[2])
    return None


def mem_effects(m):
    k = m[0]
    if k == "ldi":
        return {m[3]}, {m[1]}
    if k == "sti":
        return {m[3], m[1]}, set()
    if k == "ldm":
        i = m[2]
        return {i, m[3], "L" + i[1:], "B" + i[1:]}, {m[1], i}
    if k == "stm":
        i = m[2]
        return {i, m[3], m[1], "L" + i[1:], "B" + i[1:]}, {i}
    if k == "stmimm":
        i = m[2]
        return {i, m[3], "L" + i[1:], "B" + i[1:]}, {i}
    if k == "ldpm":
        return {m[2], m[3]}, {m[1]}
    raise ValueError(m)


def split_top(s):
    """split on ' , ' at parenthesis depth 0"""
    parts, depth, cur, i = [], 0, "", 0
    while i < len(s):
        ch = s[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(cur.strip())
            cur = ""
        else:
            cur += ch
        i += 1
    parts.append(cur.strip())
    return parts


def unknown(text, li=None):
    return Insn(text, ("unknown",), "unknown", ALL, NONE, ALL, li=li)


def parse(text, li=None):
    """one instruction line (stripped, with its ';') -> Insn"""
    s = text.strip()
    if not s.endswith(";"):
        return unknown(s, li)
    s = s[:-1].strip()
    t = text.strip()
    if s == "NOP":
        return Insn(t, ("nop",), li=li)
    if s == "RFRAME":
        return Insn(t, ("rframe",), reads={"I6"}, writes={"I6", "I7"}, li=li)
    m = re.match(r"^IF\s+(NOT\s+)?([A-Z]+)\s+(.*)$", s)
    if m:
        cc = ((m.group(1) or "") + m.group(2)).strip()
        cc = re.sub(r"\s+", " ", cc)
        flags = COND_FLAGS.get(cc)
        if flags is None:
            return unknown(t, li)
        rest = m.group(3).strip()
        mj = re.match(r"^JUMP\s+(" + LBL + r")$", rest)
        if mj:
            return Insn(t, ("cjmp", cc, mj.group(1)), "cjmp", reads=flags, target=mj.group(1), li=li)
        c = parse_compute(rest)
        if c:
            r, w = compute_effects(c)
            return Insn(t, ("cond", cc, c), reads=r | flags, cwrites=w, li=li)
        return unknown(t, li)
    m = re.match(r"^JUMP\s+(" + LBL + r")(\s*\(DB\))?$", s)
    if m:
        return Insn(t, ("jump", m.group(1)), "jump", target=m.group(1), delayed=bool(m.group(2)), li=li)
    m = re.match(r"^JUMP\s*\(\s*M(\d+)\s*,\s*I(\d+)\s*\)\s*\(DB\)$", s)
    if m:
        return Insn(t, ("ret", "M" + m.group(1), "I" + m.group(2)), "ret", reads={"M" + m.group(1), "I" + m.group(2)},
                    delayed=True, li=li)
    m = re.match(r"^CJUMP\s+(" + SYMR + r")\s*\(DB\)$", s)
    if m:
        f = m.group(1)[:-1]
        return Insn(t, ("call", f), "call", reads={"I6", "I7"}, writes={"I6"}, cwrites={"R2"}, target=f,
                    delayed=True, li=li)
    m = re.match(r"^LCNTR\s*=\s*(" + NUM + r")\s*,\s*DO\s*\(PC,\s*(" + LBL + r")\)\s*UNTIL\s+LCE$", s)
    if m:
        return Insn(t, ("do", int(m.group(1), 0), m.group(2)), "do", writes={"SYS"}, target=m.group(2), li=li)
    m = re.match(r"^LCNTR\s*=\s*R(\d+)\s*,\s*DO\s+(" + LBL + r")\s+UNTIL\s+LCE$", s)
    if m:
        r = "R" + m.group(1)
        return Insn(t, ("do", r, m.group(2)), "do", reads={r}, writes={"SYS"}, target=m.group(2), li=li)
    m = re.match(r"^R(\d+)\s*=\s*(" + NUM + r")$", s)
    if m:
        return Insn(t, ("imm", "R" + m.group(1), int(m.group(2), 0)), writes={"R" + m.group(1)}, li=li)
    m = re.match(r"^R(\d+)\s*=\s*(" + SYMR + r")$", s)
    if m:
        return Insn(t, ("sym", "R" + m.group(1), m.group(2)), writes={"R" + m.group(1)}, li=li)
    m = re.match(r"^([RI])(\d+)\s*=\s*([RI])(\d+)$", s)
    if m and "I" in (m.group(1), m.group(3)):
        d, src = m.group(1) + m.group(2), m.group(3) + m.group(4)
        return Insn(t, ("mov", d, src), reads={src}, writes={d}, li=li)
    m = re.match(r"^I(\d+)\s*=\s*MODIFY\s*\(\s*I(\d+)\s*,\s*(" + NUM + r")\s*\)\s*\(NW\)$", s)
    if m:
        d, src = "I" + m.group(1), "I" + m.group(2)
        return Insn(t, ("modnw", d, src, int(m.group(3), 0)), reads={src}, writes={d}, li=li)
    parts = split_top(s)
    if len(parts) == 2:
        c, mm = parse_compute(parts[0]), parse_mem(parts[1])
        if c and mm and mm[0] in ("ldi", "sti", "ldm", "stm") and mm[1].startswith("R"):
            rc, wc = compute_effects(c)
            rm, wm = mem_effects(mm)
            if wc & wm:
                return unknown(t, li)
            return Insn(t, ("par", c, mm), reads=rc | rm, writes=wc | wm, li=li)
        return unknown(t, li)
    if len(parts) != 1:
        return unknown(t, li)
    mm = parse_mem(s)
    if mm:
        r, w = mem_effects(mm)
        return Insn(t, mm, reads=r, writes=w, li=li)
    c = parse_compute(s)
    if c:
        r, w = compute_effects(c)
        return Insn(t, c, reads=r, writes=w, li=li)
    return unknown(t, li)


# text of rewritten forms, in selcc's style
def text_ldi(dst, k, ireg):
    return "%s=DM (%s,%s);" % (dst, hexs(k), ireg)


def text_sti(src, k, ireg):
    return "DM (%s,%s)=%s;" % (hexs(k), ireg, src)


def text_mov(dst, src):
    return "%s=%s;" % (dst, src)


def mem_text(mm):
    k = mm[0]
    if k == "ldi":
        return "%s=DM (%s,%s)" % (mm[1], hexs(mm[2]), mm[3])
    if k == "sti":
        return "DM (%s,%s)=%s" % (hexs(mm[2]), mm[3], mm[1])
    if k == "ldm":
        return "%s=DM (%s,%s)" % (mm[1], mm[2], mm[3])
    if k == "stm":
        return "DM (%s,%s)=%s" % (mm[2], mm[3], mm[1])
    raise ValueError(mm)


# ---------------------------------------------------------------------------------------------------------------------
# program: files, functions, control flow

class Func:
    def __init__(self, name, stem):
        self.name, self.stem = name, stem
        self.ins = []            # Insn (li = line index in the file)
        self.labels = {}         # local label -> index of the instruction it names
        self.label_at = {}       # index -> [labels]
        self.opaque = None       # why the function is untouchable (None: analysable)
        self.succ = []
        self.blocks = []         # (start, end) instruction index ranges
        self.block_of = {}
        self.loops = []          # (body start, end instruction index)
        self.reach = set()

    def __repr__(self):
        return "Func(%s:%s)" % (self.stem, self.name)


def parse_program(texts):
    """{stem: text} -> (funcs {key: Func}, order [keys], refs: names of labels referenced other than by CJUMP).
    A function's key is 'stem:name' (selcc gives every file its own static copy of fxmath.h's helpers); a call goes to
    the function of that name in the same file, else to the one .GLOBAL in another file, else it is external."""
    funcs, order, refs, glob = {}, [], set(), {}
    for stem, text in texts.items():
        lines = text.split("\n")
        in_code, cur = False, None
        pend = []
        glob[stem] = set(re.findall(r"^\s*\.GLOBAL\s+([A-Za-z_][A-Za-z0-9_]*)\.;", text, re.M))
        for li, raw in enumerate(lines):
            s = raw.strip()
            if s.startswith(".SECTION/"):
                in_code = s.startswith(".SECTION/SW") or s.startswith(".SECTION/PM")
                cur, pend = None, []
                continue
            if s == ".ENDSEG;":
                if cur is not None and pend:
                    cur.opaque = cur.opaque or "a label at the end of the section"
                in_code, cur, pend = False, None, []
                continue
            for sym in re.findall(r"(?<![\w.])([A-Za-z_][A-Za-z0-9_]*)\.(?=[\s;),\-+]|$)", s):
                if not re.match(r"^\s*CJUMP\s", s) and not FUNC_LABEL.match(raw) and not s.startswith(
                        (".GLOBAL", ".EXTERN")):
                    refs.add(sym)
            if not in_code or not s or s.startswith("//"):
                continue
            m = FUNC_LABEL.match(raw)
            if m:
                cur = Func(m.group(1), stem)
                cur.key = f"{stem}:{cur.name}"
                if cur.key in funcs:
                    raise SystemExit(f"peephole: function {cur.key} defined twice")
                funcs[cur.key] = cur
                order.append(cur.key)
                pend = []
                continue
            m = LOCAL_LABEL.match(raw)
            if m:
                if cur is None:
                    raise SystemExit(f"{stem}: label {m.group(1)} before any function")
                pend.append(m.group(1))
                continue
            if s.startswith("."):
                continue                  # .GLOBAL / .EXTERN and other directives
            if cur is None:
                raise SystemExit(f"{stem}: code before the first function: {s}")
            x = parse(s, li)
            idx = len(cur.ins)
            for lab in pend:
                cur.labels[lab] = idx
                cur.label_at.setdefault(idx, []).append(lab)
            pend = []
            cur.ins.append(x)
    gdef = {}
    for k, f in funcs.items():
        if f.name in glob[f.stem]:
            gdef.setdefault(f.name, []).append(k)

    def resolve(stem, name):
        if f"{stem}:{name}" in funcs:
            return f"{stem}:{name}"
        g = gdef.get(name, [])
        if len(g) > 1:
            raise SystemExit(f"peephole: {name} is global in more than one file")
        return g[0] if g else name             # not in the texts: external
    for f in funcs.values():
        f.li_index = {x.li: i for i, x in enumerate(f.ins)}
        build_cfg(f)
        f.calls = {resolve(f.stem, x.target) for x in f.ins if x.kind == "call"}
        if not f.opaque:
            f.succ = [[("call", resolve(f.stem, s[1]), s[2]) if not isinstance(s, int) and s[0] == "call" else s
                       for s in ss] for ss in f.succ]
    return funcs, order, refs


def build_cfg(f):
    """successors per instruction: an index, ('call', callee, return index), ('ret',) or ('exit',) (falls out of the
    function: everything live); blocks; DO loops; reachability. Anything odd -> f.opaque"""
    ins, n = f.ins, len(f.ins)
    succ = [None] * n
    why = None
    if n == 0:
        f.opaque = "empty"
        return
    for i, x in enumerate(ins):
        if x.kind == "unknown":
            why = why or "unknown instruction %r" % x.text
    if why:
        f.opaque = why
        return
    loop_end = {}
    i = 0
    while i < n:
        x = ins[i]
        if x.delayed:
            if i + 2 >= n or any(j in f.label_at for j in (i + 1, i + 2)) or any(
                    ins[j].kind != "seq" for j in (i + 1, i + 2)):
                f.opaque = "delay slots of %r" % x.text
                return
            succ[i], succ[i + 1] = [i + 1], [i + 2]
            if x.kind == "jump":
                if x.target not in f.labels:
                    f.opaque = "jump out of the function %r" % x.text
                    return
                succ[i + 2] = [f.labels[x.target]]
            elif x.kind == "call":
                if i + 3 >= n:
                    f.opaque = "call at the end"
                    return
                succ[i + 2] = [("call", x.target, i + 3)]
            elif x.kind == "ret":
                succ[i + 2] = [("ret",)]
            else:
                f.opaque = "delayed %r" % x.text
                return
            i += 3
            continue
        if x.kind == "jump":
            if x.target not in f.labels:
                f.opaque = "jump out of the function %r" % x.text
                return
            succ[i] = [f.labels[x.target]]
        elif x.kind == "cjmp":
            if x.target not in f.labels:
                f.opaque = "jump out of the function %r" % x.text
                return
            succ[i] = [i + 1 if i + 1 < n else ("exit",), f.labels[x.target]]
        elif x.kind == "do":
            if x.target not in f.labels or not (i < f.labels[x.target] < n):
                f.opaque = "DO loop end %r" % x.text
                return
            e = f.labels[x.target]
            if e in loop_end:
                f.opaque = "two loops end at one instruction"
                return
            loop_end[e] = i + 1
            f.loops.append((i + 1, e))
            succ[i] = [i + 1]
        elif x.kind in ("call", "ret"):
            f.opaque = "non-delayed %r" % x.text
            return
        else:
            succ[i] = [i + 1 if i + 1 < n else ("exit",)]
        i += 1
    for e, start in loop_end.items():
        if ins[e].kind != "seq" or ins[e].delayed or succ[e] != [e + 1 if e + 1 < n else ("exit",)]:
            f.opaque = "a loop ends on a control instruction"
            return
        succ[e] = [start, e + 1 if e + 1 < n else ("exit",)]
    for lo, e in f.loops:
        for j in range(lo, e + 1):
            if ins[j].kind != "seq":
                for s in succ[j]:
                    if isinstance(s, int) and not (lo <= s <= e) and not (j == e):
                        f.opaque = "a jump out of a DO loop"
                        return
    f.succ = succ
    leaders = {0} | set(f.labels.values())
    for i, ss in enumerate(succ):
        if ss != [i + 1]:
            leaders.add(i + 1)
            for s in ss:
                if isinstance(s, int):
                    leaders.add(s)
    leaders = sorted(x for x in leaders if x < n)
    f.blocks = [(a, b) for a, b in zip(leaders, leaders[1:] + [n])]
    f.block_of = {}
    for bi, (a, b) in enumerate(f.blocks):
        for j in range(a, b):
            f.block_of[j] = bi
    # reachability from the entry
    seen, todo = set(), [0]
    while todo:
        i = todo.pop()
        if i in seen:
            continue
        seen.add(i)
        for s in succ[i]:
            if isinstance(s, int):
                todo.append(s)
            elif s[0] == "call":
                todo.append(s[2])
    f.reach = seen


# ---------------------------------------------------------------------------------------------------------------------
# liveness over the whole program

class Liveness:
    """registers live at each block's exit, every function, calls through the callee's live-in set and returns through
    what is live after its call sites. entries: {function: set live at its return} for functions called from outside
    the texts (hook stubs); a function called from nowhere in the texts and not in entries returns with ALL live, as
    does one whose label is referenced other than by a CJUMP."""

    def __init__(self, funcs, refs, entries=None):
        self.funcs = funcs
        entries = entries or {}
        callers = {}
        for k, f in funcs.items():
            if f.opaque:
                continue
            for i, ss in enumerate(f.succ):
                for s in ss:
                    if not isinstance(s, int) and s[0] == "call":
                        callers.setdefault(s[1], []).append((k, s[2]))
        self.callers = callers
        opaque_callers = set()
        for f in funcs.values():
            if f.opaque:
                opaque_callers |= f.calls
        base = {}
        for k, f in funcs.items():
            if f.name in refs or k in opaque_callers:
                base[k] = ALL
            elif f.name in entries:
                base[k] = frozenset(entries[f.name])
            elif k not in callers:
                base[k] = ALL
            else:
                base[k] = NONE
        self.base = base
        self.live_in = {k: (ALL if f.opaque else NONE) for k, f in funcs.items()}
        self.live_ret = dict(base)
        self.bin, self.bout = {}, {}
        self._solve()

    def callee_in(self, g):
        if g not in self.funcs:
            return ALL                       # not in the texts: anything may be read
        return self.live_in[g]

    def _func(self, f):
        """block liveness of one function given live_in / live_ret of the others; -> live_in of its entry"""
        bin_ = {bi: NONE for bi in range(len(f.blocks))}
        bout = {}
        changed = True
        while changed:
            changed = False
            for bi in reversed(range(len(f.blocks))):
                a, b = f.blocks[bi]
                out = set()
                for s in f.succ[b - 1]:
                    if isinstance(s, int):
                        out |= bin_[f.block_of[s]]
                    elif s[0] == "call":
                        out |= self.callee_in(s[1]) | bin_[f.block_of[s[2]]]
                    elif s[0] == "ret":
                        out |= self.live_ret[f.key]
                    else:
                        out |= ALL
                live = set(out)
                for j in range(b - 1, a - 1, -1):
                    x = f.ins[j]
                    live -= x.writes
                    live |= x.reads
                live = frozenset(live)
                bout[bi] = frozenset(out)
                if live != bin_[bi]:
                    bin_[bi] = live
                    changed = True
        self.bin[f.key], self.bout[f.key] = bin_, bout
        return bin_[0]

    def _solve(self):
        changed = True
        rounds = 0
        while changed:
            rounds += 1
            if rounds > 200:
                raise SystemExit("peephole: liveness does not converge")
            changed = False
            contrib = {}
            for name, f in self.funcs.items():
                if f.opaque:
                    continue
                li = self._func(f)
                if li != self.live_in[name]:
                    self.live_in[name] = li
                    changed = True
                for g, lst in self.callers.items():
                    for caller, r in lst:
                        if caller == name:
                            contrib[g] = contrib.get(g, NONE) | self.bin[name][f.block_of[r]]
            for name in self.funcs:
                new = self.base[name] | contrib.get(name, NONE)
                if new != self.live_ret[name]:
                    self.live_ret[name] = new
                    changed = True
        self.rounds = rounds

    def block_out(self, fname, bi):
        return self.bout[fname][bi]


# ---------------------------------------------------------------------------------------------------------------------
# symbolic execution of a block

class Lin:
    """an integer value mod 2^32: constant + sum of coef * atom (atoms: hashable tuples)"""
    __slots__ = ("c", "t", "_h")

    def __init__(self, c=0, t=None):
        self.c = c & M32
        self.t = frozenset((a, k & M32) for a, k in (t or {}).items() if k & M32)
        self._h = None

    @staticmethod
    def atom(a):
        return Lin(0, {a: 1})

    def terms(self):
        return dict(self.t)

    def __add__(self, o):
        t = self.terms()
        for a, k in o.t:
            t[a] = (t.get(a, 0) + k) & M32
        return Lin(self.c + o.c, t)

    def scale(self, s):
        return Lin(self.c * s, {a: k * s for a, k in self.t})

    def __sub__(self, o):
        return self + o.scale(-1)

    def key(self):
        return (self.c, self.t)

    def __eq__(self, o):
        return isinstance(o, Lin) and self.c == o.c and self.t == o.t

    def __hash__(self):
        if self._h is None:
            self._h = hash((self.c, self.t))
        return self._h

    def __repr__(self):
        return "Lin(%#x%s)" % (self.c, "".join(" + %d*%r" % (k, a) for a, k in self.t))


RAW = ("op", "init")


class Unvalidatable(Exception):
    pass


def run_block(insns):
    """-> (register state {name: Lin}, events [..]) from an all-symbolic start; registers not written are absent"""
    st, ev = {}, []

    def get(r):
        v = st.get(r)
        if v is None:
            v = Lin.atom(("init", r))
        return v

    def iv(v):
        """the 32-bit view of a value used as an integer (an opaque 40-bit result or an incoming data register gets
        its own atom, so an integer add never equals a raw float)"""
        if v.c == 0 and len(v.t) == 1:
            (a, k), = v.t
            if k == 1 and a[0] in RAW and not (a[0] == "init" and a[1][0] != "R"):
                return Lin.atom(("v32", a))
        return v

    def op_atom(*parts):
        return Lin.atom(("op",) + parts)

    def comp(c, w):
        _, op, d, srcs = c
        xs = tuple(get(s).key() for s in srcs)
        if op in ("ADD", "SUB"):
            a, b = iv(get(srcs[0])), iv(get(srcs[1]))
            w[d] = a + b if op == "ADD" else a - b
            w["AF"] = op_atom(op, "AF", a.key(), b.key())
            return
        if d:
            w[d] = op_atom(op, "d", xs)
        if op in ALU_OPS:
            w["AF"] = op_atom(op, "AF", xs)
            if op in ("COMP", "COMPU", "FCOMP"):
                w["CACC"] = op_atom(op, "CACC", xs, get("CACC").key())
        elif op in MUL_OPS:
            w["MF"] = op_atom(op, "MF", xs)
        else:
            w["SF"] = op_atom(op, "SF", xs)

    def mem(mm, w):
        k = mm[0]
        if k in ("ldi", "sti"):
            addr = iv(get(mm[3])) + Lin(4 * mm[2])
        elif k in ("ldm", "stm", "stmimm"):
            addr = iv(get(mm[2]))
        elif k == "ldpm":
            addr = iv(get(mm[3])) + iv(get(mm[2])).scale(4)
        else:
            raise Unvalidatable(mm)
        if k in ("ldi", "ldm", "ldpm"):
            ev.append(("ld", addr.key()))
            w[mm[1]] = Lin.atom(("ld", len(ev) - 1))
        elif k in ("sti", "stm"):
            ev.append(("st", addr.key(), iv(get(mm[1])).key()))
        else:
            ev.append(("st", addr.key(), ("imm", mm[1])))
        if k in ("ldm", "stm", "stmimm"):
            w[mm[2]] = addr + iv(get(mm[3])).scale(4)

    for x in insns:
        f, w = x.form, {}
        k = f[0]
        if k == "nop":
            pass
        elif k == "imm":
            w[f[1]] = Lin(f[2])
        elif k == "sym":
            w[f[1]] = Lin.atom(("sym", f[2]))
        elif k == "mov":
            w[f[1]] = iv(get(f[2]))
        elif k == "modnw":
            w[f[1]] = iv(get(f[2])) + Lin(4 * f[3])
        elif k in ("ldi", "sti", "ldm", "stm", "stmimm", "ldpm"):
            mem(f, w)
        elif k == "comp":
            comp(f, w)
        elif k == "par":
            w1, w2 = {}, {}
            comp(f[1], w1)
            mem(f[2], w2)
            if set(w1) & set(w2):
                raise Unvalidatable(x.text)
            w.update(w1)
            w.update(w2)
        elif k == "cond":
            cc, c = f[1], f[2]
            flags = tuple(get(r).key() for r in sorted(COND_FLAGS[cc]))
            w1 = {}
            comp(c, w1)
            for r, v in w1.items():
                w[r] = Lin.atom(("cond", cc, flags, r, v.key(), get(r).key()))
        elif k == "call":
            ev.append(("call", f[1], iv(get("I6")).key(), iv(get("I7")).key()))
            w["I6"] = Lin.atom(("call", "I6", iv(get("I7")).key()))
            w["R2"] = Lin.atom(("call", "R2", iv(get("I6")).key(), get("R2").key()))
        elif k == "ret":
            ev.append(("ret", get(f[1]).key(), get(f[2]).key()))
        elif k == "jump":
            ev.append(("jump", f[1]))
        elif k == "cjmp":
            ev.append(("cjmp", f[1], f[2], tuple(get(r).key() for r in sorted(COND_FLAGS[f[1]]))))
        elif k == "rframe":
            ev.append(("ld", iv(get("I6")).key()))
            w["I7"] = Lin.atom(("rframe", "I7", iv(get("I6")).key()))
            w["I6"] = Lin.atom(("ld", len(ev) - 1))
        elif k == "do":
            cnt = f[1] if isinstance(f[1], int) else get(f[1]).key()
            ev.append(("do", f[2], cnt))
            w["SYS"] = Lin.atom(("do", f[2], cnt, get("SYS").key()))
        else:
            raise Unvalidatable(x.text)
        st.update(w)
    return st, ev


def equivalent(old, new, live_out):
    """-> None if the two instruction lists do the same (memory accesses in order, branches, and every register of
    live_out at the end), else the first difference"""
    try:
        so, eo = run_block(old)
        sn, en = run_block(new)
    except Unvalidatable as e:
        return "not checkable: %s" % e
    if len(eo) != len(en):
        return "%d memory/control events against %d" % (len(eo), len(en))
    for i, (a, b) in enumerate(zip(eo, en)):
        if a != b:
            return "event %d differs: %r / %r" % (i, a[:2], b[:2])
    for r in sorted(live_out):
        a, b = so.get(r), sn.get(r)
        a = a if a is not None else Lin.atom(("init", r))
        b = b if b is not None else Lin.atom(("init", r))
        if a != b:
            return "live register %s differs at the exit" % r
    return None


# ---------------------------------------------------------------------------------------------------------------------
# the rewrites

def is_i4_access(x):
    """a normal-word access through I4 with post-modify (selcc's DM(I4,M5)), standalone or with a compute ->
    (mem form, compute form or None)"""
    f = x.form
    if f[0] in ("ldm", "stm") and f[2] == "I4":
        return f, None
    if f[0] == "par" and f[2][0] in ("ldm", "stm") and f[2][2] == "I4":
        return f[2], f[1]
    return None


def uses_i4(x):
    """a memory access with I4 as its index register (either form)"""
    f = x.form
    mm = f[2] if f[0] == "par" else f
    return mm[0] in ("ldm", "stm") and mm[2] == "I4" or mm[0] in ("ldi", "sti") and mm[3] == "I4"


def access_with(x, k, ireg):
    """the access x through (k, ireg): -> new Insn (None when the form cannot carry the offset)"""
    mm, c = is_i4_access(x)
    if c is not None and not (-32 <= k <= 31):
        return None                      # Type 4 (with a compute) has a 6-bit offset
    if mm[0] == "ldm":
        nm = ("ldi", mm[1], k, ireg)
    else:
        nm = ("sti", mm[1], k, ireg)
    if c is None:
        t = mem_text(nm) + ";"
    else:
        comp_txt = split_top(x.text.rstrip(";").strip())[0]
        t = comp_txt + " , " + mem_text(nm) + ";"
    y = parse(t, x.li)
    if y.kind == "unknown":
        raise AssertionError("peephole made an unparsable line %r" % t)
    return y


def reaching_def(blk, pos, reg):
    """index of the last instruction before pos that writes reg (or maybe writes it); None if none in the block"""
    for j in range(pos - 1, -1, -1):
        if reg in blk[j].writes or reg in blk[j].cwrites:
            return j
    return None


def readers_after(blk, j, regs, skip):
    """True if a register written by blk[j] (in regs) is read by a kept instruction after j before being written again;
    second value: the registers still live (not overwritten) at the block end"""
    pending = set(regs)
    for t in range(j + 1, len(blk)):
        if t in skip:
            continue
        x = blk[t]
        if pending & x.reads:
            return True, pending
        pending -= x.writes
        if not pending:
            return False, pending
    return False, pending


def dce(blk, cand, live_out, forced=frozenset()):
    """remove the candidate instructions whose results nothing reads (forced: removed in any case); -> the set
    removed"""
    rm = set(cand) | set(forced)
    changed = True
    while changed:
        changed = False
        for j in sorted(rm - set(forced), reverse=True):
            read, left = readers_after(blk, j, blk[j].writes | blk[j].cwrites, rm)
            if read or (left & live_out):
                rm.discard(j)
                changed = True
    return rm


def gen_set(blk):
    """registers a block reads before writing them (its upward-exposed uses)"""
    live = set()
    for x in reversed(blk):
        live -= x.writes
        live |= x.reads
    return live


def i4_load_w48_pairs(blk, in_loop):
    """indices in blk of a load from memory into I4 whose next instruction uses I4 and is 48 bits"""
    out = []
    for i, x in enumerate(blk[:-1]):
        f = x.form
        if f[0] in ("ldi", "ldpm") and f[1] == "I4":
            nxt = blk[i + 1]
            if "I4" in nxt.reads and size_of(nxt, in_loop) == 6:
                out.append(i)
    return out


def w48_after_i4_loads(texts):
    """[(stem, function, line, load, user)] over a whole program: every load into I4 followed directly by a 48-bit user
    of I4 (the pairing the stock code never has)"""
    funcs, order, _ = parse_program(texts)
    out = []
    for k in order:
        f = funcs[k]
        if f.opaque:
            continue
        for lo, hi in f.blocks:
            blk = f.ins[lo:hi]
            inl = any(a <= lo <= e for a, e in f.loops)
            for i in i4_load_w48_pairs(blk, inl):
                out.append((f.stem, f.name, blk[i].li + 1, blk[i].text, blk[i + 1].text))
    return out


def size_of(x, in_loop=False):
    """bytes selas emits for an instruction (selinstr visa_encode; a DO loop body is never compressed)"""
    if in_loop:
        return 6
    f = x.form
    k = f[0]
    if k == "nop" or k == "rframe":
        return 2
    if k == "imm":
        v = f[2] - (1 << 32) if f[2] >= 1 << 31 else f[2]
        return 4 if -32768 <= v <= 32767 else 6
    if k == "sym":
        return 6
    if k == "mov":
        return 4
    if k in ("ldm", "stm"):
        return 2 if f[2] in IREGS[:8] and f[3] in MREGS[:8] and f[1].startswith("R") else 4
    if k in ("ldi", "sti"):
        return 4 if f[3] in IREGS[:8] and -64 <= f[2] <= 63 else 6
    if k == "comp":
        op, d, srcs = f[1], f[2], f[3]
        if op in ("PASS", "NOT", "COMP", "FCOMP"):
            return 2
        if op in ("ADD", "SUB", "AND", "OR", "XOR", "FADD", "FSUB", "FMUL") and d == srcs[0]:
            return 2
        return 4
    if k == "par":
        return 6
    return 6


class Rewriter:
    def __init__(self, patterns="AB", allow_loops=True, min_loop=6, loop_tail=3):
        self.patterns = patterns
        self.allow_loops = allow_loops
        self.min_loop, self.loop_tail = min_loop, loop_tail
        self.sites = []
        self.refused = []
        self.stock_pairs = "s" in patterns

    def loop_ok(self, f, changed, deleted, loop_left):
        """DO loop bodies (selas emits them unpacked, every instruction 48 bits): nothing changed in the last loop_tail
        instructions of a body (the end label stays on its instruction), every body keeps at least min_loop instructions.
        changed, deleted: indices in the function's instruction list"""
        for lo, e in f.loops:
            touched = [j for j in changed | deleted if lo <= j <= e]
            if not touched:
                continue
            if not self.allow_loops:
                return False
            if any(j > e - self.loop_tail for j in touched):
                return False
            gone = sum(1 for j in deleted if lo <= j <= e)
            if loop_left[(lo, e)] - gone < self.min_loop:
                return False
        return True

    def block(self, f, bi, live_out, loop_left):
        """rewrite one block; -> the new instruction list (Insn objects keep their line index)"""
        a, b = f.blocks[bi]
        blk = list(f.ins[a:b])
        for li in [y.li for y in blk if uses_i4(y)]:
            if "A" in self.patterns:
                x = self._find(blk, li)
                if is_i4_access(blk[x]):
                    new = self.try_a(f, blk, x, live_out, loop_left)
                    if new is not None:
                        blk = new
            if "B" in self.patterns:
                x = self._find(blk, li)
                if uses_i4(blk[x]):
                    new = self.try_b(f, blk, x, live_out, loop_left)
                    if new is not None:
                        blk = new
        return blk

    @staticmethod
    def _find(blk, li):
        for i, y in enumerate(blk):
            if y.li == li:
                return i
        raise AssertionError("peephole lost an access")

    def _accept(self, f, blk, new, live_out, loop_left, deleted, changed, site):
        if not self.loop_ok(f, changed, deleted, loop_left):
            self.refused.append(dict(site, refused="DO loop rule"))
            return None
        if not gen_set(new) <= gen_set(blk):
            self.refused.append(dict(site, refused="the block would read a register it did not read before"))
            return None
        if self.stock_pairs:
            blk_in_loop = any(lo <= f.li_index[blk[0].li] <= e for lo, e in f.loops)
            if i4_load_w48_pairs(new, blk_in_loop):
                self.refused.append(dict(site, refused="s: a load into I4 followed directly by a 48-bit user of I4"))
                return None
        in_loop = any(lo <= j <= e for lo, e in f.loops for j in changed | deleted)
        saved = sum(size_of(x, in_loop) for x in blk) - sum(size_of(x, in_loop) for x in new)
        if saved <= 0:
            self.refused.append(dict(site, refused="no bytes saved"))
            return None
        why = equivalent(blk, new, live_out)
        if why is not None:
            self.refused.append(dict(site, refused=why))
            return None
        for lo, e in f.loops:
            loop_left[(lo, e)] -= sum(1 for j in deleted if lo <= j <= e)
        site["bytes"] = saved
        site["in_loop"] = in_loop
        self.sites.append(site)
        return new

    @staticmethod
    def _idx(f, y):
        """index of an instruction in the function's own list (by its line)"""
        return f.li_index[y.li]

    def try_a(self, f, blk, x, live_out, loop_left):
        X = blk[x]
        # the I4 definition the access uses: the last write of I4 before it, with no read of I4 in between
        m = None
        for j in range(x - 1, -1, -1):
            if "I4" in blk[j].writes or "I4" in blk[j].cwrites:
                m = j
                break
            if "I4" in blk[j].reads:
                return None
        if m is None:
            return None
        M = blk[m]
        site = {"func": f.name, "stem": f.stem, "pattern": "A", "access": X.text}
        # stack arguments: I4=I6; I4=MODIFY (I4,k)(NW); access  ->  access through (k, I6)
        if M.form[0] == "modnw" and M.form[1] == "I4" and M.form[2] == "I4" and m >= 1:
            P = blk[m - 1]
            if P.form == ("mov", "I4", "I6") and "I4" not in M.cwrites:
                k = M.form[3]
                y = access_with(X, k, "I6")
                if y is None:
                    return None
                new = blk[:m - 1] + blk[m + 1:x] + [y] + blk[x + 1:]
                site.update(pattern="Af", before=[P.text, M.text, X.text], after=[y.text], k=k)
                deleted = {self._idx(f, P), self._idx(f, M)}
                changed = {self._idx(f, X)}
                return self._accept(f, blk, new, live_out, loop_left, deleted, changed, site)
            return None
        if M.form[0] != "mov" or M.form[1] != "I4" or not M.form[2].startswith("R"):
            return None
        # the chain: Rs = Rbase + Rk with Rk = imm (nested: Rbase itself base + imm)
        levels = []
        cur, p, K = M.form[2], m, 0
        while True:
            j = reaching_def(blk, p, cur)
            if j is None or cur not in blk[j].writes:
                break
            d = blk[j]
            if d.form[0] != "comp" or d.form[1] != "ADD" or d.form[2] != cur:
                break
            xs = d.form[3]
            if xs[0] == xs[1]:
                break
            cands = []
            for o1, o2 in ((xs[0], xs[1]), (xs[1], xs[0])):
                ja = reaching_def(blk, j, o1)
                if ja is not None and blk[ja].form[0] == "imm" and o1 in blk[ja].writes:
                    v = blk[ja].form[2]
                    v = v - (1 << 32) if v >= 1 << 31 else v
                    # a field offset, never an absolute address (those are >= 0x240000 here)
                    if -0x20000 < v < 0x20000:
                        cands.append((o2, v, ja))
            if len(cands) != 1:
                break
            base, v, ja = cands[0]
            K += v
            levels.append({"add": j, "imm": ja, "base": base, "K": K})
            cur, p = base, j
        if not levels:
            return None
        for L in range(len(levels) - 1, -1, -1):
            lv = levels[L]
            base, Kt = lv["base"], lv["K"]
            if Kt % 4:
                continue
            k = Kt // 4
            y = access_with(X, k, "I4") if k else X
            if y is None:
                continue
            chain = set()
            for q in levels[:L + 1]:
                chain |= {q["add"], q["imm"]}
            for place in ("move", "add"):
                # the move to I4 where selcc's was, or (when the base is overwritten in between) where the innermost
                # add was, selcc's move then gone
                work = list(blk)
                work[x] = y
                if place == "move":
                    mv = parse(text_mov("I4", base), M.li)
                    work[m] = mv
                    rm = dce(work, chain, live_out)
                else:
                    jadd = lv["add"]
                    mv = parse(text_mov("I4", base), blk[jadd].li)
                    work[jadd] = mv
                    rm = dce(work, chain - {jadd}, live_out, forced={m})
                adds = {q["add"] for q in levels[:L + 1]} - ({lv["add"]} if place == "add" else set())
                if not adds <= rm:
                    continue                 # a folded sum is still needed: nothing gained at this depth
                new = [w for i, w in enumerate(work) if i not in rm]
                deleted = {self._idx(f, blk[i]) for i in rm}
                changed = {self._idx(f, X), self._idx(f, mv)}
                s = dict(site, levels=L + 1, K=Kt, k=k, place=place,
                         before=[blk[i].text for i in sorted(chain | {m, x})],
                         after=[w.text for i, w in enumerate(work) if i in (chain | {m, x}) and i not in rm])
                got = self._accept(f, blk, new, live_out, loop_left, deleted, changed, s)
                if got is not None:
                    return got
        return None

    def try_b(self, f, blk, x, live_out, loop_left):
        X = blk[x]
        m = None
        for j in range(x - 1, -1, -1):
            if "I4" in blk[j].writes or "I4" in blk[j].cwrites:
                m = j
                break
            if "I4" in blk[j].reads:
                return None
        if m is None:
            return None
        M = blk[m]
        if M.form[0] != "mov" or M.form[1] != "I4" or not M.form[2].startswith("R"):
            return None
        rb = M.form[2]
        p = reaching_def(blk, m, rb)
        if p is None:
            return None
        P = blk[p]
        if P.form[0] != "ldi" or P.form[1] != rb or P.form[3] != "I6" or rb not in P.writes:
            return None
        ld = parse(text_ldi("I4", P.form[2], "I6"), P.li)
        new = blk[:p] + [ld] + blk[p + 1:m] + blk[m + 1:]
        site = {"func": f.name, "stem": f.stem, "pattern": "B", "access": X.text, "before": [P.text, M.text],
                "after": [ld.text]}
        deleted = {self._idx(f, M)}
        changed = {self._idx(f, P)}
        return self._accept(f, blk, new, live_out, loop_left, deleted, changed, site)


# ---------------------------------------------------------------------------------------------------------------------
# the driver

def apply_edits(texts, edits):
    """edits: {stem: {line index: new text or None}} -> new texts (each line keeps its indentation)"""
    out = {}
    for stem, text in texts.items():
        e = edits.get(stem)
        if not e:
            out[stem] = text
            continue
        lines = text.split("\n")
        res = []
        for li, ln in enumerate(lines):
            if li in e:
                if e[li] is None:
                    continue
                ind = ln[:len(ln) - len(ln.lstrip())]
                res.append(ind + e[li])
            else:
                res.append(ln)
        out[stem] = "\n".join(res)
    return out


def block_edits(f, bi, new):
    """line edits that turn block bi of f into the instruction list new"""
    a, b = f.blocks[bi]
    old = f.ins[a:b]
    keep = {y.li: y.text for y in new}
    e = {}
    for x in old:
        if x.li not in keep:
            e[x.li] = None
        elif keep[x.li] != x.text:
            e[x.li] = keep[x.li]
    return e


def check_program(otexts, ntexts, entries=None, only=None):
    """the independent check: parse both, liveness of the new program, every block that differs checked against the
    old one. -> (failures [(stem, func, block index, why)], blocks checked)"""
    of, oorder, orefs = parse_program(otexts)
    nf, norder, nrefs = parse_program(ntexts)
    if oorder != norder:
        raise SystemExit("peephole check: the functions differ")
    lv = Liveness(nf, nrefs | orefs, entries)
    fails, checked = [], 0
    for name in norder:
        fo, fn = of[name], nf[name]
        if [x.text for x in fo.ins] == [x.text for x in fn.ins]:
            continue
        if fo.opaque or fn.opaque or len(fo.blocks) != len(fn.blocks):
            fails.append((fn.stem, name, None, "structure: %s / %s, %d / %d blocks" % (
                fo.opaque, fn.opaque, len(fo.blocks), len(fn.blocks))))
            continue
        for bi in range(len(fo.blocks)):
            (a, b), (c, d) = fo.blocks[bi], fn.blocks[bi]
            bo, bn = fo.ins[a:b], fn.ins[c:d]
            if [x.text for x in bo] == [x.text for x in bn]:
                continue
            checked += 1
            ctl_o = [x.text for x in bo if x.kind != "seq"]
            ctl_n = [x.text for x in bn if x.kind != "seq"]
            shape = lambda f_, i_: [s[:2] if not isinstance(s, int) else "i" for s in f_.succ[i_]]  # noqa: E731
            slots_o = [x.text for x in bo[-2:]] if any(x.delayed for x in bo) else []
            slots_n = [x.text for x in bn[-2:]] if any(x.delayed for x in bn) else []
            if sorted(fo.label_at.get(a, [])) != sorted(fn.label_at.get(c, [])) or ctl_o != ctl_n \
                    or slots_o != slots_n or shape(fo, b - 1) != shape(fn, d - 1):
                fails.append((fn.stem, name, bi, "block structure changed"))
                continue
            why = equivalent(bo, bn, lv.block_out(name, bi))
            if why:
                fails.append((fn.stem, name, bi, why))
    return fails, checked


def program_bytes(texts):
    """code bytes as selas will emit them (size_of; DO loop bodies unpacked), every function of the texts"""
    funcs, _, _ = parse_program(texts)
    n = 0
    for f in funcs.values():
        inl = set()
        for lo, e in f.loops:
            inl |= set(range(lo, e + 1))
        n += sum(size_of(x, i in inl) for i, x in enumerate(f.ins))
    return n


def stub_entries(stubs, save_regs):
    """{selcc function: registers live at its return} for the functions the hook stubs call (tools/dspsplice/stub.py):
    after its one C call a stub restores every register it saved (R0-R15, I0-I5, M1-M4; MODE1 through R0, written
    first) before it reads any, so those are dead at the function's return; everything else (I6, I7, the M registers,
    the flags, ...) stays live. A stub with more than one C call, or a stock call: everything live."""
    restored = frozenset(r for r in save_regs if r != "MODE1")
    out = {}
    for _, steps in stubs:
        cs = [st for st in steps if st[0] == "c"]
        simple = len(cs) == 1 and all(st[0] in ("c", "tail") for st in steps)
        for st in cs:
            out[st[1]] = out.get(st[1], NONE) | (ALL - restored if simple else ALL)
    return out


def run(texts, rewrite=None, entries=None, patterns="AB", allow_loops=True, max_rounds=4):
    """texts: {stem: selcc .s text} (every file of the program: the effects, the glue, a test harness); rewrite: the
    stems to rewrite (default all); entries: {function: registers live at its return} for functions called from
    outside the texts. -> (new texts, report)"""
    rewrite = set(texts) if rewrite is None else set(rewrite)
    cur = dict(texts)
    sites = []
    rounds = []
    for rnd in range(max_rounds):
        funcs, order, refs = parse_program(cur)
        lv = Liveness(funcs, refs, entries)
        rw = Rewriter(patterns, allow_loops=allow_loops)
        edits = {}
        for name in order:
            f = funcs[name]
            if f.stem not in rewrite or f.opaque:
                continue
            loop_left = {(lo, e): e - lo + 1 for lo, e in f.loops}
            for bi, (a, b) in enumerate(f.blocks):
                if a not in f.reach:
                    continue
                new = rw.block(f, bi, lv.block_out(name, bi), loop_left)
                e = block_edits(f, bi, new)
                if e:
                    edits.setdefault(f.stem, {}).update(e)
        rounds.append({"round": rnd + 1, "sites": len(rw.sites), "liveness_rounds": lv.rounds})
        if not rw.sites:
            break
        sites += rw.sites
        cur = apply_edits(cur, edits)
    # the independent check of the result against selcc's own text; blocks that fail are put back
    reverted = []
    while True:
        fails, checked = check_program(texts, cur, entries)
        if not fails:
            break
        reverted += fails
        cur = revert(texts, cur, fails)
    w48 = w48_after_i4_loads(cur)
    if "s" in patterns and w48:
        raise SystemExit("peephole (s): a load into I4 followed directly by a 48-bit user of I4: %s" % w48[:3])
    import hashlib
    report = {"peephole_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), "patterns": patterns,
              "i4_loads_then_48bit_user": len(w48),
              "rounds": rounds, "sites": len(sites),
              "by_pattern": {p: sum(1 for s in sites if s["pattern"] == p) for p in ("A", "Af", "B")},
              "bytes_saved_estimate": program_bytes(texts) - program_bytes(cur),
              "by_file": {st: {"sites": sum(1 for s in sites if s["stem"] == st),
                               "bytes_saved_estimate": program_bytes({st: texts[st]}) - program_bytes({st: cur[st]})}
                          for st in sorted(texts)},
              "blocks_checked": checked, "reverted": [list(x) for x in reverted],
              "entries_dead_at_return": {k: sorted(ALL - v) for k, v in (entries or {}).items()},
              "site_list": sites}
    return cur, report


def revert(otexts, ntexts, fails):
    """put the failing blocks (or whole functions) back to selcc's text"""
    of, _, _ = parse_program(otexts)
    nf, _, _ = parse_program(ntexts)
    out = dict(ntexts)
    for stem, name, bi, why in fails:
        fo, fn = of[name], nf[name]
        lines_o = otexts[stem].split("\n")
        lines_n = out[stem].split("\n")
        if bi is None:
            lo_o = min(x.li for x in fo.ins)
            hi_o = max(x.li for x in fo.ins)
            lo_n = min(x.li for x in fn.ins)
            hi_n = max(x.li for x in fn.ins)
        else:
            (a, b), (c, d) = fo.blocks[bi], fn.blocks[bi]
            lo_o, hi_o = fo.ins[a].li, fo.ins[b - 1].li
            lo_n, hi_n = fn.ins[c].li, fn.ins[d - 1].li
        lines_n[lo_n:hi_n + 1] = lines_o[lo_o:hi_o + 1]
        out[stem] = "\n".join(lines_n)
        nf, _, _ = parse_program(out)
    return out


def site_lines(otexts, ntexts):
    """{stem: [line index in the new text of every instruction the peephole changed]} (the accesses and the I4 loads),
    for coverage labels"""
    out = {}
    for stem in ntexts:
        if otexts[stem] == ntexts[stem]:
            continue
        import difflib
        a, b = otexts[stem].split("\n"), ntexts[stem].split("\n")
        sm = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag in ("replace", "insert"):
                out.setdefault(stem, []).extend(range(j1, j2))
    return out


def add_site_labels(text, lines, prefix):
    """a global label before each listed line (labels take no space; the code is unchanged): -> (text, [labels])"""
    ls = text.split("\n")
    out, labels = [], []
    want = set(lines)
    for i, ln in enumerate(ls):
        if i in want:
            lab = "%s%d" % (prefix, len(labels))
            labels.append(lab)
            out += [".GLOBAL %s.;" % lab, "%s.:" % lab]
        out.append(ln)
    return "\n".join(out), labels


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="+")
    ap.add_argument("--rewrite", help="comma-separated stems to rewrite (default: all)")
    ap.add_argument("--patterns", default="AB")
    ap.add_argument("--out", help="write the rewritten .s files here")
    ap.add_argument("--no-loops", action="store_true")
    a = ap.parse_args()
    texts = {Path(p).stem: Path(p).read_text() for p in a.files}
    new, rep = run(texts, a.rewrite.split(",") if a.rewrite else None, patterns=a.patterns,
                   allow_loops=not a.no_loops)
    print("sites %d %s, blocks checked %d, reverted %d, rounds %s" % (
        rep["sites"], rep["by_pattern"], rep["blocks_checked"], len(rep["reverted"]), rep["rounds"]))
    if a.out:
        Path(a.out).mkdir(parents=True, exist_ok=True)
        for stem, t in new.items():
            (Path(a.out) / f"{stem}.s").write_text(t)


if __name__ == "__main__":
    sys.dont_write_bytecode = True
    main()
