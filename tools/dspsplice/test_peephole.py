#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Unit tests of tools/dspsplice/peephole.py (DC01, notes/DC01-compiler-fix.md).

    python3 tools/dspsplice/test_peephole.py [-v]

Each case is a small function in selcc's style, run through peephole.run(), and the result compared line for line
with the expected text: the patterns (A, nested A, the stack arguments, B, A + B), and the cases the pass must refuse
(a live temp, a branch between the chain and the access, I4 read after the access, I4 live at the return, an offset out
of range, an offset not a multiple of four, flags read after a removed add, an absolute address, the base used later,
the end of a DO loop). The last test assembles every rewritten form with selas and compares the bytes with the same
instructions in the stock DN2 1.12 DSP image (out/T3.1/disasm), where CCES emitted them.
"""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "tools/sharcvm"))
import peephole as P  # noqa: E402

SEL = ROOT / "vendor/selache/target/release"
# what the caller of the test function keeps after it returns: R0 (the result), the frame, the M registers; the
# scratch registers, I4 and the flags dead (as after a hook stub, which restores R0-R15 and I0-I5)
LIVE_RET = (P.ALL - set(P.RREGS) - {"I4", "AF", "MF", "SF", "CACC"}) | {"R0"}
EPILOGUE = ["I12=DM (M7,I6);", "JUMP (M14,I12) (DB);", "RFRAME;", "NOP;"]


def func(body, name="f"):
    lines = [".SECTION/SW seg_swco;", f".GLOBAL {name}.;", f"{name}.:"]
    for b in body + EPILOGUE:
        lines.append(b if b.endswith(":") else "    " + b)
    lines.append(".ENDSEG;")
    return "\n".join(lines) + "\n"


def body_of(text):
    out = []
    for ln in text.split("\n"):
        s = ln.strip()
        if not s or s.startswith(".") and not s.startswith(".L_") or s.endswith(".:"):
            continue
        out.append(s)
    return out[:-len(EPILOGUE)]


def rw(body, patterns="AB", live_ret=LIVE_RET, **kw):
    t, rep = P.run({"t": func(body)}, patterns=patterns, entries={"f": live_ret}, **kw)
    return body_of(t["t"]), rep


class TestPatterns(unittest.TestCase):
    def test_a_load(self):
        got, rep = rw(["R4=DM (-0x6,I6);",
                       "R1 = 0x34;", "R3 = R4 + R1;", "I4=R3;", "R1=DM (I4,M5);",
                       "R0 = PASS R1;"], patterns="A")
        self.assertEqual(got, ["R4=DM (-0x6,I6);", "I4=R4;", "R1=DM (0xD,I4);", "R0 = PASS R1;"])
        self.assertEqual(rep["by_pattern"]["A"], 1)

    def test_a_store(self):
        got, _ = rw(["R3=DM (-0x7,I6);", "R1 = PASS R4;",
                     "R5 = 0x3C;", "R6 = R3 + R5;", "I4=R6;", "DM (I4,M5)=R1;"], patterns="A")
        self.assertEqual(got, ["R3=DM (-0x7,I6);", "R1 = PASS R4;", "I4=R3;", "DM (0xF,I4)=R1;"])

    def test_a_nested(self):
        # struct in struct: base + 4 + 0x14, the base register overwritten by the second offset (selcc does this):
        # the move to I4 goes where the inner add was
        got, _ = rw(["R1=DM (-0x7,I6);", "R3 = 0x4;", "R5 = R1 + R3;", "R1 = 0x14;", "R3 = R5 + R1;", "I4=R3;",
                     "R1=DM (I4,M5);", "R0 = PASS R1;"], patterns="A")
        self.assertEqual(got, ["R1=DM (-0x7,I6);", "I4=R1;", "R1=DM (0x6,I4);", "R0 = PASS R1;"])

    def test_a_with_compute(self):
        got, _ = rw(["R3=DM (-0x7,I6);", "R5 = 0x8;", "R6 = R3 + R5;", "I4=R6;",
                     "R0 = PASS R4 , R1=DM (I4,M5);"], patterns="A")
        self.assertEqual(got, ["R3=DM (-0x7,I6);", "I4=R3;", "R0 = PASS R4 , R1=DM (0x2,I4);"])

    def test_a_negative_offset(self):
        got, _ = rw(["R3=DM (-0x7,I6);", "R5 = -0x8;", "R6 = R3 + R5;", "I4=R6;", "R0=DM (I4,M5);"], patterns="A")
        self.assertEqual(got, ["R3=DM (-0x7,I6);", "I4=R3;", "R0=DM (-0x2,I4);"])

    def test_a_large_offset_48bit(self):
        # 0x100 / 4 = 0x40 is outside the 32-bit form's -64..63: the 48-bit Type 15 form, still 4 bytes shorter
        got, rep = rw(["R3=DM (-0x7,I6);", "R5 = 0x100;", "R6 = R3 + R5;", "I4=R6;", "R0=DM (I4,M5);"], patterns="A")
        self.assertEqual(got, ["R3=DM (-0x7,I6);", "I4=R3;", "R0=DM (0x40,I4);"])
        self.assertEqual(rep["site_list"][0]["bytes"], 4)

    def test_a_stack_argument(self):
        got, rep = rw(["I4=I6;", "I4=MODIFY (I4,0x2)(NW);", "R5=DM (I4,M5);", "R0 = PASS R5;"])
        self.assertEqual(got, ["R5=DM (0x2,I6);", "R0 = PASS R5;"])
        self.assertEqual(rep["by_pattern"]["Af"], 1)

    def test_b(self):
        got, rep = rw(["R3=DM (-0x7,I6);", "I4=R3;", "R0=DM (I4,M5);"], patterns="B")
        self.assertEqual(got, ["I4=DM (-0x7,I6);", "R0=DM (I4,M5);"])
        self.assertEqual(rep["by_pattern"]["B"], 1)

    def test_a_then_b(self):
        got, rep = rw(["R1 = PASS R4;", "R3=DM (-0x7,I6);", "R5 = 0x3C;", "R6 = R3 + R5;", "I4=R6;",
                       "DM (I4,M5)=R1;", "R3=DM (-0x7,I6);", "R5 = 0x4;", "R6 = R3 + R5;", "I4=R6;",
                       "R0=DM (I4,M5);"])
        self.assertEqual(got, ["R1 = PASS R4;", "I4=DM (-0x7,I6);", "DM (0xF,I4)=R1;", "I4=DM (-0x7,I6);",
                               "R0=DM (0x1,I4);"])
        self.assertEqual((rep["by_pattern"]["A"], rep["by_pattern"]["B"]), (2, 2))


class TestRefusals(unittest.TestCase):
    def same(self, body, **kw):
        got, rep = rw(body, **kw)
        self.assertEqual(got, body, rep["site_list"])
        return rep

    def test_live_sum(self):
        # the field's address (R6) is read again later: the add stays, so nothing is gained
        self.same(["R3=DM (-0x7,I6);", "R5 = 0x3C;", "R6 = R3 + R5;", "I4=R6;", "R1=DM (I4,M5);", "R0 = R6 + R1;"],
                  patterns="A")

    def test_live_offset_register(self):
        # the offset register (R5) is read later: it stays, the add goes (2 bytes saved, not 6)
        got, rep = rw(["R3=DM (-0x7,I6);", "R5 = 0x3C;", "R6 = R3 + R5;", "I4=R6;", "R1=DM (I4,M5);",
                       "R0 = R5 + R1;"], patterns="A")
        self.assertEqual(got, ["R3=DM (-0x7,I6);", "R5 = 0x3C;", "I4=R3;", "R1=DM (0xF,I4);", "R0 = R5 + R1;"])
        self.assertEqual(rep["site_list"][0]["bytes"], 2)

    def test_temp_live_at_return(self):
        # R6 is live when the function returns (its caller reads it): no fold
        self.same(["R3=DM (-0x7,I6);", "R5 = 0x3C;", "R6 = R3 + R5;", "I4=R6;", "R1=DM (I4,M5);"],
                  patterns="A", live_ret=LIVE_RET | {"R6"})

    def test_branch_between(self):
        # the chain is in the block before a branch target: the pass works inside a block only
        self.same(["R3=DM (-0x7,I6);", "R5 = 0x3C;", "R6 = R3 + R5;", "COMP (R1,R3);", "IF EQ JUMP .L_x;",
                   "R1 = PASS R4;", ".L_x:", "I4=R6;", "R0=DM (I4,M5);"], patterns="A")

    def test_i4_read_after(self):
        # a second access through the same I4 value: I4 would hold the base, not the field's address
        self.same(["R3=DM (-0x7,I6);", "R5 = 0x3C;", "R6 = R3 + R5;", "I4=R6;", "R1=DM (I4,M5);",
                   "R0=DM (I4,M5);"], patterns="A")

    def test_i4_live_at_return(self):
        self.same(["R3=DM (-0x7,I6);", "R5 = 0x3C;", "R6 = R3 + R5;", "I4=R6;", "R0=DM (I4,M5);"],
                  patterns="A", live_ret=LIVE_RET | {"I4"})

    def test_offset_out_of_range_with_compute(self):
        # with a compute in parallel the offset has 6 bits (-32..31 words): 0x8C / 4 = 35 does not fit
        self.same(["R3=DM (-0x7,I6);", "R5 = 0x8C;", "R6 = R3 + R5;", "I4=R6;", "R0 = PASS R4 , R1=DM (I4,M5);"],
                  patterns="A")

    def test_offset_not_a_word(self):
        self.same(["R3=DM (-0x7,I6);", "R5 = 0x6;", "R6 = R3 + R5;", "I4=R6;", "R0=DM (I4,M5);"], patterns="A")

    def test_flags_read_after_the_add(self):
        # the add's ALU flags decide the branch: it stays
        self.same(["R3=DM (-0x7,I6);", "R5 = 0x3C;", "R6 = R3 + R5;", "I4=R6;", "R0=DM (I4,M5);",
                   "IF EQ JUMP .L_y;", "R0 = PASS R4;", ".L_y:", "R1 = PASS R0;"], patterns="A")

    def test_absolute_address_not_an_offset(self):
        # an address constant plus an index is not a pointer plus a field offset
        self.same(["R1 = 0x2C3450;", "R3=DM (-0xD,I6);", "R6 = R1 + R3;", "I4=R6;", "R0=DM (I4,M5);"],
                  patterns="A")

    def test_base_overwritten_moved_up(self):
        # the base register is reloaded between the add and the move: I4 = base goes where the add was
        got, _ = rw(["R3=DM (-0x7,I6);", "R5 = 0x3C;", "R6 = R3 + R5;", "R3=DM (-0x8,I6);", "I4=R6;",
                     "R0=DM (I4,M5);", "R1 = PASS R3;", "DM (-0x9,I6)=R1;"], patterns="A")
        self.assertEqual(got, ["R3=DM (-0x7,I6);", "I4=R3;", "R3=DM (-0x8,I6);", "R0=DM (0xF,I4);",
                               "R1 = PASS R3;", "DM (-0x9,I6)=R1;"])

    def test_base_overwritten_and_i4_busy(self):
        # the base is reloaded before the move and I4 is used in between: neither placement of I4 = base is the same
        self.same(["R3=DM (-0x7,I6);", "R5 = 0x3C;", "R6 = R3 + R5;", "I4=R7;", "R1=DM (I4,M5);",
                   "R3=DM (-0x8,I6);", "I4=R6;", "R0=DM (I4,M5);", "R1 = PASS R3;", "DM (-0x9,I6)=R1;"],
                  patterns="A")

    def test_b_base_used_later(self):
        self.same(["R3=DM (-0x7,I6);", "I4=R3;", "R0=DM (I4,M5);", "R1 = PASS R3;", "DM (-0x9,I6)=R1;"],
                  patterns="B")

    def test_b_i4_used_between(self):
        self.same(["R3=DM (-0x7,I6);", "I4=R7;", "R1=DM (I4,M5);", "I4=R3;", "R0=DM (I4,M5);"], patterns="B")

    def test_loop_end(self):
        # the last instructions of a DO loop body are never changed (the end label stays on its instruction)
        body = ["LCNTR = 0x4 , DO (PC,.L_doloop_end_f_1)UNTIL LCE;", "R1 = PASS R4;", "R1 = PASS R4;",
                "R1 = PASS R4;", "R1 = PASS R4;", "R1 = PASS R4;", "R1 = PASS R4;",
                "R3=DM (-0x7,I6);", "R5 = 0x3C;", "R6 = R3 + R5;", "I4=R6;", ".L_doloop_end_f_1:", "R0=DM (I4,M5);",
                "R1 = PASS R0;"]
        self.same(body, patterns="A")

    def test_short_loop(self):
        # a body of 7 would keep 5 instructions (fewer than 6): left alone
        body = ["R3=DM (-0x7,I6);", "LCNTR = 0x4 , DO (PC,.L_doloop_end_f_2)UNTIL LCE;", "R5 = 0x3C;",
                "R6 = R3 + R5;", "I4=R6;", "R0=DM (I4,M5);", "R1 = PASS R0;", "R1 = PASS R0;",
                ".L_doloop_end_f_2:", "R1 = PASS R0;", "R2 = PASS R1;"]
        self.same(body, patterns="A")

    def test_unknown_instruction(self):
        # a form the parser does not know makes the whole function untouchable
        self.same(["R3=DM (-0x7,I6);", "R5 = 0x3C;", "R6 = R3 + R5;", "I4=R6;", "R0=DM (I4,M5);",
                   "R1 = R1 + R2 (CI);"])


class TestStockPairs(unittest.TestCase):
    """mode s (DC02): B only where a 16- or 32-bit instruction uses I4 right after the load into it"""
    FAR = ["R3=DM (-0x7,I6);", "R5 = 0x100;", "R6 = R3 + R5;", "I4=R6;", "R0=DM (I4,M5);"]

    def test_ab_loads_before_a_48bit_access(self):
        got, rep = rw(self.FAR, patterns="AB")
        self.assertEqual(got, ["I4=DM (-0x7,I6);", "R0=DM (0x40,I4);"])
        self.assertEqual(rep["i4_loads_then_48bit_user"], 1)

    def test_abs_refuses_a_48bit_access_next(self):
        got, rep = rw(self.FAR, patterns="ABs")
        self.assertEqual(got, ["R3=DM (-0x7,I6);", "I4=R3;", "R0=DM (0x40,I4);"])
        self.assertEqual((rep["by_pattern"]["B"], rep["i4_loads_then_48bit_user"]), (0, 0))

    def test_abs_refuses_a_compute_with_the_access_next(self):
        got, _ = rw(["R3=DM (-0x7,I6);", "R5 = 0x8;", "R6 = R3 + R5;", "I4=R6;", "R0 = PASS R4 , R1=DM (I4,M5);"],
                    patterns="ABs")
        self.assertEqual(got, ["R3=DM (-0x7,I6);", "I4=R3;", "R0 = PASS R4 , R1=DM (0x2,I4);"])

    def test_abs_keeps_a_32bit_access_next(self):
        got, rep = rw(["R3=DM (-0x7,I6);", "R5 = 0x3C;", "R6 = R3 + R5;", "I4=R6;", "R0=DM (I4,M5);"],
                      patterns="ABs")
        self.assertEqual(got, ["I4=DM (-0x7,I6);", "R0=DM (0xF,I4);"])
        self.assertEqual(rep["by_pattern"]["B"], 1)

    def test_abs_keeps_a_48bit_access_after_other_work(self):
        got, _ = rw(["R3=DM (-0x7,I6);", "R1 = PASS R4;", "R5 = 0x100;", "R6 = R3 + R5;", "I4=R6;",
                     "DM (I4,M5)=R1;"], patterns="ABs")
        self.assertEqual(got, ["I4=DM (-0x7,I6);", "R1 = PASS R4;", "DM (0x40,I4)=R1;"])

    def test_abs_refuses_in_a_loop_body(self):
        # in a DO loop body every instruction is 48 bits: a load into I4 used right away is refused there
        body = ["LCNTR = 0x4 , DO (PC,.L_doloop_end_f_4)UNTIL LCE;",
                "R3=DM (-0x7,I6);", "R5 = 0x3C;", "R6 = R3 + R5;", "I4=R6;", "R0=DM (I4,M5);"] + [
                   "R1 = PASS R0;"] * 7 + [".L_doloop_end_f_4:", "R2 = PASS R1;", "R4 = PASS R2;"]
        got, rep = rw(body, patterns="ABs")
        self.assertEqual(got[:4], ["LCNTR = 0x4 , DO (PC,.L_doloop_end_f_4)UNTIL LCE;", "R3=DM (-0x7,I6);", "I4=R3;",
                                   "R0=DM (0xF,I4);"])
        self.assertEqual(rep["by_pattern"]["B"], 0)
        got, rep = rw(body, patterns="AB")
        self.assertEqual(got[:3], ["LCNTR = 0x4 , DO (PC,.L_doloop_end_f_4)UNTIL LCE;", "I4=DM (-0x7,I6);",
                                   "R0=DM (0xF,I4);"])

    def test_the_scan(self):
        t = func(["I4=DM (-0x7,I6);", "R0=DM (0x40,I4);", "I4=DM (-0x8,I6);", "R1=DM (0x3,I4);",
                  "I4=DM (-0x9,I6);", "R2 = PASS R1 , R3=DM (0x2,I4);"])
        got = P.w48_after_i4_loads({"t": t})
        self.assertEqual([(x[3], x[4]) for x in got], [("I4=DM (-0x7,I6);", "R0=DM (0x40,I4);"),
                                                      ("I4=DM (-0x9,I6);", "R2 = PASS R1 , R3=DM (0x2,I4);")])


class TestLoopAllowed(unittest.TestCase):
    def test_inside_a_long_loop(self):
        # a long loop body: a fold in its middle is allowed (selas emits the body unpacked: 6 bytes per instruction)
        body = ["LCNTR = 0x4 , DO (PC,.L_doloop_end_f_3)UNTIL LCE;",
                "R3=DM (-0x7,I6);", "R5 = 0x3C;", "R6 = R3 + R5;", "I4=R6;", "R0=DM (I4,M5);"] + [
                   "R1 = PASS R0;"] * 7 + [".L_doloop_end_f_3:", "R2 = PASS R1;", "R4 = PASS R2;"]
        got, rep = rw(body, patterns="A")
        self.assertEqual(got[:4], ["LCNTR = 0x4 , DO (PC,.L_doloop_end_f_3)UNTIL LCE;", "R3=DM (-0x7,I6);", "I4=R3;",
                                   "R0=DM (0xF,I4);"])
        self.assertEqual(rep["site_list"][0]["bytes"], 12)


class TestValidator(unittest.TestCase):
    def blk(self, lines):
        return [P.parse(x) for x in lines]

    def test_detects_a_wrong_offset(self):
        a = self.blk(["R5 = 0x3C;", "R6 = R3 + R5;", "I4=R6;", "DM (I4,M5)=R1;"])
        b = self.blk(["I4=R3;", "DM (0xE,I4)=R1;"])
        self.assertIsNotNone(P.equivalent(a, b, set()))
        c = self.blk(["I4=R3;", "DM (0xF,I4)=R1;"])
        self.assertIsNone(P.equivalent(a, c, set()))
        self.assertIsNotNone(P.equivalent(a, c, {"R6"}))       # R6 live: not the same
        self.assertIsNotNone(P.equivalent(a, c, {"I4"}))       # I4 live: not the same
        self.assertIsNotNone(P.equivalent(a, c, {"AF"}))       # the add's flags live: not the same

    def test_detects_a_load_store_swap(self):
        a = self.blk(["I4=R3;", "R1=DM (0x1,I4);"])
        b = self.blk(["I4=R3;", "DM (0x1,I4)=R1;"])
        self.assertIsNotNone(P.equivalent(a, b, set()))

    def test_integer_add_is_not_a_float_copy(self):
        # an integer add of zero to a float result is not the float result (40-bit register contents)
        a = self.blk(["F5 = F1 * F3;", "R0 = 0x0;", "R6 = R5 + R0;"])
        b = self.blk(["F5 = F1 * F3;", "R0 = 0x0;", "F6 = F1 * F3;"])
        self.assertIsNotNone(P.equivalent(a, b, {"R6"}))


class TestEncodings(unittest.TestCase):
    """selas's bytes for the rewritten forms against the same instructions in the stock image (CCES's code)"""
    STOCK = {   # stock DN2 1.12 DSP, out/T3.1/disasm: address, bytes, text
        "R2=DM (0x3,I4);": ("0xb8000b", "98080103"),
        "DM (0x3,I4)=R2;": ("0xb8006e", "99080103"),
        "DM (0x4,I4)=R1;": ("0xb802f1", "99080084"),
        "I4=DM (-0x27,I6);": ("0x1c2d7a", "9c080a59"),
        "I4=DM (-0x20,I6);": ("0x1c2875", "9c080a60"),
        # the 48-bit form (offset outside -64..63 words)
        "R15=DM (0x62,I4);": ("0x1c5063", "a80f00000062"),
        "DM (0x8E,I4)=R8;": ("0x1c4e38", "a9080000008e"),
        # a compute with the access (Type 4)
        "F2 = F1 + F2 , R1=DM (0x1E,I4);": ("0x1c216c", "683ef0881212"),
    }

    def assemble(self, lines):
        with tempfile.TemporaryDirectory() as d:
            s = Path(d) / "t.s"
            s.write_text(".SECTION/SW seg_swco;\n.GLOBAL t.;\nt.:\n" + "".join(
                "    " + x + "\n" for x in lines) + ".ENDSEG;\n")
            subprocess.run([str(SEL / "selas"), "-proc", "ADSP-21569", "-o", str(Path(d) / "t.doj"), str(s)],
                           check=True, capture_output=True)
            import elf as E
            e = E.read_elf(Path(d) / "t.doj")
            sec = [x for x in e.sections if x.name == "seg_swco"]
            raw = bytes(sec[0].data)
            # the object holds each 16-bit parcel little-endian; the listing prints parcels big-endian
            return b"".join(raw[i:i + 2][::-1] for i in range(0, len(raw), 2))

    def test_against_stock(self):
        if not (SEL / "selas").exists():
            self.skipTest("no selas")
        for text, (addr, want) in self.STOCK.items():
            got = self.assemble([text])
            # selas writes the parcels big-endian in the object; the stock listing prints them the same way
            self.assertEqual(got.hex(), want, "%s (stock %s)" % (text, addr))

    def test_sizes(self):
        if not (SEL / "selas").exists():
            self.skipTest("no selas")
        cases = ["R1=DM (0xD,I4);", "DM (-0x40,I4)=R1;", "R1=DM (0x40,I4);", "R0 = PASS R4 , R1=DM (0x1F,I4);",
                 "I4=DM (-0x7,I6);", "I4=R3;", "R1=DM (I4,M5);", "R5 = 0x3C;", "R5 = 0x12345;", "R6 = R3 + R5;",
                 "R3 = R3 + R5;"]
        for c in cases:
            x = P.parse(c)
            self.assertNotEqual(x.kind, "unknown", c)
            self.assertEqual(len(self.assemble([c])), P.size_of(x), c)


if __name__ == "__main__":
    unittest.main()
