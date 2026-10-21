// SPDX-License-Identifier: GPL-2.0-or-later
// cmp_clk.s - T6.3 CMP2D: two leaf functions for cmp_load.c (selcc has no way to read EMUCLK or I6).
// Called from selcc code like any function (CJUMP: I6 = I7, the caller's I6 and the return address pushed);
// each returns in R0 and leaves every other register as it found it (I12 is scratch in both conventions).
// The forms are the stock image's: `R0 = EMUCLK` is the handler's own (0x1c9e49), `R2 = I6` (with R0 here)
// and the return sequence are the hook stubs' (tools/dspsplice/stub.py).
.SECTION/SW seg_t38c2;

// uint32_t cmp_clk(void): the cycle counter EMUCLK
.GLOBAL cmp_clk.;
cmp_clk.:
    R0 = EMUCLK;
    I12 = DM (M7,I6);
    JUMP (M14,I12) (DB);
    RFRAME;
    NOP;

// uint32_t cmp_fp(void): its own frame pointer; the word there is the caller's frame pointer
.GLOBAL cmp_fp.;
cmp_fp.:
    R0 = I6;
    I12 = DM (M7,I6);
    JUMP (M14,I12) (DB);
    RFRAME;
    NOP;
.ENDSEG;
