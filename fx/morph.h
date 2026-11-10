/* SPDX-License-Identifier: GPL-2.0-or-later */
/*
 * morph.h - MRPH, a reverb whose character moves continuously from room through hall to spring, for the
 * Digitone II reverb slot.
 *
 * A port to portable C99 (float only, no libc, no malloc, no division) of the owner's morphing reverb for
 * their Beads firmware (naturarum/eurorack, branch beads-reverb, beads/dsp/fx/morph_reverb.h, version 2, MIT,
 * same author), with the owner's changes for the DN2 (2026-11-09): diffusion fixed at the middle of its knob
 * ("smooth", so the wash never runs), no ducking. Delay lines hold floats (the module's are 16-bit).
 *
 * The tank is an 8-line feedback delay network. Its mixing matrix is three levels of 2x2 rotations
 * (A. Coppola, "Fast parametric matrices for lossless feedback delay networks", DAFx 2026), each angle
 * pi/4 x the character's coupling, slowly modulated. Lines 0-3 run through cascades of stretched allpass
 * filters (V. Valimaki, J. Parker, J. Abel, "Parametric spring reverberation effect", JAES 2010): with a
 * coefficient of 0 a stretched allpass is a plain delay, so the spring fades in without a switch. Towards
 * the spring end lines 4-7 fade out and the top matrix level decouples the two groups.
 *
 * Signal flow (one block of up to 32 samples):
 *   in L, R x send -> input high-pass (one-pole) -> input low-pass (SVF) -> pre-delay line, per side
 *   -> early reflections: 6 taps per side on the pre-delay lines (2 from the other side), gliding
 *   -> per side: pre-delay read + a share of the early reflections -> 4 allpass diffusers -> tank input
 *   -> tank: 8 lines read (modulated delays), lines 0-3 through the allpass cascades, absorption
 *      (one-pole per line, from the decay times at DC and at Nyquist), output taps, the rotation matrix,
 *      the input added (even lines left, odd lines right), written back
 *   -> + early reflections -> output low-pass (TONE) -> x wet -> out L, R (wet only, like the stock
 *      reverb: the FX bus adds VOL)
 *
 * Page (DN2 knob values 0..127, fractional allowed; the proposed mapping onto the stock reverb page):
 *   A PRE   extra pre-delay 0..120 ms (square law), in front of the character's own and of the early
 *           reflections (not in the Beads original; 0 = the original)
 *   B DEC   decay: 0.3 s to 36 s x the character's own scale (64: about 3.3 s on the hall)
 *   C CHAR  character: room (0), hall (64), spring (127), blending in between, flat spots at the three
 *   D SIZE  delay lines 0.25x .. 1.6x of the character's own (glides slowly: the tail bends in pitch)
 *   E HPF   input high-pass 20 Hz .. 1 kHz (log), above the character's own (0 = the original)
 *   F TONE  dark to bright: how much faster the highs die, and the output low-pass
 *
 * State: sizeof(Morph), about 270 KB (one 65,536-float ring for every delay line), inside the stock
 * reverb's DDR state (0x59114 = 364,820 bytes).
 *
 * Build options (for the cost and A/B work): MR_STAGES (allpass stages per spring line, default 24),
 * MR_EARLY_FIXED (early reflection taps on whole samples, set once per block, instead of interpolated taps
 * gliding every sample), MR_Q16 (native test only: round every ring write to 16 bits as the module does,
 * to compare with the original).
 */
#ifndef MORPH_H
#define MORPH_H

#include <stdint.h>
#include "fxmath.h"

#define MR_NL       8         /* tank lines */
#define MR_NSL      4         /* lines with the spring's allpass cascade */
#ifndef MR_STAGES
#define MR_STAGES   24        /* stretched allpass stages per spring line */
#endif
#define MR_STRETCH  7         /* each stage in z^-7: (int)(48000 / 7000 + 0.5), dispersion peak about 3.4 kHz */
#define MR_NE       6         /* early reflection taps per side */
#define MR_ND       4         /* input diffusers per side */
#define MR_BLK      32        /* the DSP's block; the lines are read a block at a time */
#define MR_RING     65536     /* every delay line in one ring of floats, a power of two */
#define MR_MASK     (MR_RING - 1)

enum {
    MR_PRE = 0,   /* A */
    MR_DEC,       /* B */
    MR_CHAR,      /* C */
    MR_SIZE,      /* D */
    MR_HPF,       /* E */
    MR_TONE,      /* F */
    MR_NPARAMS
};

/* the blended character: fields of the three end points (morph.c) */
enum {
    MC_LINE = 0,                  /* 8 line lengths, ms */
    MC_PRE = MC_LINE + MR_NL,     /* pre-delay, ms */
    MC_ELEV,                      /* early reflections' level */
    MC_ESPAN,                     /* their span, ms */
    MC_DSCALE,                    /* decay scale */
    MC_CPL0, MC_CPL1, MC_CPL2,    /* matrix angle scales, finest level first */
    MC_G2,                        /* level of lines 4-7 */
    MC_DISP,                      /* stretched allpass coefficient, lines 0-3 */
    MC_MODMS, MC_MODHZ,           /* line modulation depth (ms) and rate (Hz) */
    MC_HP, MC_LP,                 /* input band, Hz */
    MC_DIFF,                      /* input diffusers' share */
    MC_OGAIN,                     /* output gain */
    MC_N
};

typedef struct {
    float knob[MR_NPARAMS];       /* targets, 0..1 */
    float cur[MR_NPARAMS];        /* glided once per block */
    float c[MC_N];                /* the character, blended from cur[MR_CHAR] */
    int32_t snap;                 /* 1: the next block jumps to the targets */
    uint32_t wp;                  /* write position; a line's sample written at t sits at (t + base) & MR_MASK */

    uint32_t lbase[MR_NL];        /* ring bases: tank lines */
    float lmax[MR_NL];            /* the longest delay each tank line takes */
    uint32_t pbl, pbr;            /* pre-delay lines, left and right */
    float pmax;
    uint32_t dbl[MR_ND], dbr[MR_ND];   /* diffusers */
    int32_t ddel[MR_ND];          /* their delays, samples */

    float ldel[MR_NL], linc[MR_NL];    /* tank line delays at the block start, step per sample */
    float lfo[MR_NL];             /* line modulation phases */
    float ag[MR_NL], ap[MR_NL], as[MR_NL];   /* absorption: input gain, pole, state */
    float ig[MR_NL];              /* input gains (sign, group level, headroom) */
    float g2, onorm;              /* level of lines 4-7, output normalisation */
    float rc0, rs0, rc1, rs1, rc2, rs2;     /* rotation cos, sin per matrix level */
    float al0, al1, al2;          /* angle modulation phases */
    float a0, a1, a2, a3;         /* allpass coefficients of lines 0-3 */
    int32_t cidx;                 /* which stretch slot the next sample uses */
    int32_t crot;                 /* 1: the cascade is stored rotated (dispersion 0 runs it as a shift) */
    int32_t coff[MR_STRETCH];     /* per slot, the rotation: stage k sits at row (k + coff) mod MR_STAGES */

    float pre, pinc;              /* pre-delay (samples) at the block start, step per sample */
    float edl[MR_NE], eil[MR_NE], edr[MR_NE], eir[MR_NE];   /* early taps' delays and steps, left, right */
    int32_t early;                /* 1: the early reflections are heard (or feed the tank) */
    float elev, efeed, dgain;

    float send, sendt, wet, wett; /* gains at the block start, targets */
    float hpg, hpgi, hpl, hpr;    /* input high-pass: g, 1/(1+g), states */
    float lpg, lpr, lph, lp1l, lp2l, lp1r, lp2r;   /* input low-pass (SVF): g, 1/Q, h, states */
    float olg, olgi, oll, olr;    /* output low-pass: g, 1/(1+g), states */

    float y[MR_NL][MR_BLK];       /* work: the tank lines read for this block */
    float tl[MR_BLK], tr[MR_BLK]; /* work: into the tank */
    float el[MR_BLK], er[MR_BLK]; /* work: early reflections */
    float ucol[MR_STAGES * MR_NSL];                   /* work: unrotating the cascade */
    float chain[MR_STAGES * MR_STRETCH * MR_NSL];     /* allpass states [stage][slot][line] */
    float ring[MR_RING];
} Morph;

void morph_init(Morph *s);
void morph_set(Morph *s, int param, float value);      /* 0..127 */
void morph_snap(Morph *s);                             /* the next block starts settled */
/* wet output only; any n >= 0, worked in blocks of up to 32 (the DSP calls with 32) */
void morph_process(Morph *s, const float *inL, const float *inR, float *outL, float *outR, int n);
#ifdef MR_TEST
void morph_set01(Morph *s, int param, float x);        /* the original's 0..1 controls, exactly */
#endif

#endif
