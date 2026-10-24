/* SPDX-License-Identifier: GPL-2.0-or-later */
/*
 * flanger_unit.h - the Digitone II's four chorus-page knobs -> the flanger's twelve (T5.6).
 *
 * DT01: a copy of fx/flanger_unit.h, the same text but this note, so that its #include "flanger.h" finds this
 * directory's flanger.h (selcc and clang look in the including file's directory first).
 *
 * On the unit the flanger sits in the chorus slot and gets only the stock chorus page's knobs:
 * A DPTH, B SPD, C HPF, D WDTH (frame words k25..k28). Everything else is fixed by the glue.
 *
 *  - version 1 (FLG1 to CMP4, tools/dspsplice/src/flg_glue.c): the four knobs go straight to
 *    DPTH, SPD, HPF and WDTH; FDBK 107 (+68 %), MAN 50 (0.9 ms), DAMP 12, sine, no SLEW/SYNC/OFS/POL.
 *    The centre delay never moves, so neither a chorus nor the short, phaser-like end is reachable.
 *  - version 2 (T5.6, tools/dspsplice/src/flg_glue2.c): knob C becomes TIME, one control for the
 *    whole range, short to long: phaser-like (0.05-0.3 ms) -> flanger (0.3-3 ms) -> chorus (10-26 ms).
 *    TIME sets the centre delay (MAN) and, past the flanger zone, fades the feedback to 0 and the
 *    depth range to a tenth, so every knob stays useful at the chorus end. The input high-pass is
 *    fixed at 64 Hz (HPF 32). TIME 32 is exactly version 1's flanger (MAN 50, FDBK 107, HPF 32), so a
 *    kit with C at 32 sounds as before. Only mappings change: the flanger's code, state and per-sample
 *    work are the same.
 *
 * The mapping runs only when a knob moves. Written for selcc (notes/T3.11 11.3, 12.7): one declaration
 * per variable, no comparison initialisers, no table indexed by a fresh integer.
 */
#ifndef FLANGER_UNIT_H
#define FLANGER_UNIT_H

#include "flanger.h"

enum { FLU_DPTH = 0, FLU_SPD, FLU_C, FLU_WDTH, FLU_N };

#define FLU_T_FLG   32.0f   /* TIME at which version 2 is version 1's flanger: MAN 50 (0.9 ms) */
#define FLU_M_FLG   50.0f
#define FLU_M_TOP   108.0f  /* TIME 127: MAN 108 = 25.7 ms */
#define FLU_T_C0    64.0f   /* the chorus fade: starts at TIME 64 (2.7 ms) ... */
#define FLU_T_C1    100.0f  /* ... and is complete at TIME 100 (10 ms) */
#define FLU_FB_FLG  107.0f  /* +68 %, version 1's fixed feedback */
#define FLU_FB_CHO  64.0f   /* 0 % */
#define FLU_DS_CHO  0.1f    /* depth range at the chorus end: DPTH 127 = +-0.33 octave */
#define FLU_HPF     32.0f   /* 64 Hz */
/* the top of every unit knob in version 2. WDTH 127 asks for an LFO offset of exactly half a cycle,
   2^31 in the flanger's 32-bit phase; selcc converts float to uint32 with a signed TRUNC, which
   overflows there, and with MODE1's ALUSAT off (as the DN2's DSP runs) the result is all ones, an
   offset of about 0: on FLG1 to CMP4, WDTH 127 (the stock default of a new kit) sweeps both sides
   together instead of 180 degrees apart [sharcvm; tools/sharcvm/tests/flgcheck.py]. 126.999 keeps
   every conversion in range (179.997 degrees) and moves the other knobs' tops by 0.0008 %. */
#define FLU_KMAX    126.999f

/* TIME and DPTH (0..127, clamped by the caller) -> the flanger's MAN, FDBK and DPTH (version 2):
   MAN rises 1.5625 semitones per step up to the flanger point (TIME 32 = MAN 50 = 0.9 ms), 0.61 above
   it (TIME 127 = MAN 108 = 25.7 ms); from TIME 64 to 100 a smoothstep c fades FDBK from +68 % to 0 and
   the depth range from DPTH's own to a tenth of it. One function, so the glue calls it only on a TIME
   or DPTH move. */
static void flanger_unit_td2(Flanger *s, float t, float dpth)
{
    float m, x, c;
    if (t < FLU_T_FLG)
        m = t * (FLU_M_FLG / FLU_T_FLG);
    else
        m = FLU_M_FLG + (t - FLU_T_FLG) * ((FLU_M_TOP - FLU_M_FLG) / (127.0f - FLU_T_FLG));
    x = (t - FLU_T_C0) * (1.0f / (FLU_T_C1 - FLU_T_C0));
    if (x < 0.0f)
        x = 0.0f;
    if (x > 1.0f)
        x = 1.0f;
    c = x * x * (3.0f - 2.0f * x);
    flanger_set(s, FL_MAN, m);
    flanger_set(s, FL_FDBK, FLU_FB_FLG + (FLU_FB_CHO - FLU_FB_FLG) * c);
    flanger_set(s, FL_DPTH, dpth * (1.0f + (FLU_DS_CHO - 1.0f) * c));
}

/* version 2's fixed settings are flanger_init's defaults (fx/flanger.c): HPF 32 (64 Hz), sine,
   SLEW 0, DAMP 12, SYNC off, OFS 0, POL normal; fxtest checks they stay so. SPD and WDTH go straight
   through. The renderer and the tests set all four knobs with this: */
#ifndef FLU_DSP
static void flanger_unit_set2(Flanger *s, const float *u)
{
    flanger_unit_td2(s, fx_clampf(u[FLU_C], 0.0f, FLU_KMAX), fx_clampf(u[FLU_DPTH], 0.0f, FLU_KMAX));
    flanger_set(s, FL_SPD, fx_clampf(u[FLU_SPD], 0.0f, FLU_KMAX));
    flanger_set(s, FL_WDTH, fx_clampf(u[FLU_WDTH], 0.0f, FLU_KMAX));
}
#endif

/* the Mac side only (left out of the DSP build, flg_glue2.c defines FLU_DSP: selcc keeps unused
   static functions): version 2's whole map, and version 1 (FLG1 to CMP4), where C is HPF */
#ifndef FLU_DSP
static void flanger_unit_init1(Flanger *s)
{
    flanger_set(s, FL_FDBK, 107.0f);
    flanger_set(s, FL_MAN, 50.0f);
    flanger_set(s, FL_SHP, 0.0f);
    flanger_set(s, FL_SLEW, 0.0f);
    flanger_set(s, FL_DAMP, 12.0f);
    flanger_set(s, FL_SYNC, 0.0f);
    flanger_set(s, FL_OFS, 0.0f);
    flanger_set(s, FL_POL, 0.0f);
}
static void flanger_unit_set1(Flanger *s, const float *u)
{
    flanger_set(s, FL_DPTH, u[FLU_DPTH]);
    flanger_set(s, FL_SPD, u[FLU_SPD]);
    flanger_set(s, FL_HPF, u[FLU_C]);
    /* as the unit plays it: WDTH 127's offset overflows to about 0 there (FLU_KMAX above) */
    flanger_set(s, FL_WDTH, u[FLU_WDTH] >= 127.0f ? 0.0f : u[FLU_WDTH]);
}
#endif

#endif
