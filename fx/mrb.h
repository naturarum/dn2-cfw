/* SPDX-License-Identifier: GPL-2.0-or-later */
/*
 * mrb.h - MRB, the morphing reverb on a budget: room through hall to spring on one knob, built to cost about
 * what the stock reverb costs (14.6k instructions a block). The same idea and the same page as MRPH
 * (morph.h, the full port of the owner's Beads reverb, about 87k-158k), redesigned for selcc's cost:
 *
 *   - the tank runs at 24 kHz (16 samples a block): half-band filters down and up, so the tail stops at
 *     about 11 kHz;
 *   - mono in (L+R), stereo out;
 *   - 4 tank lines instead of 8, two levels of rotations (the character's coupling);
 *   - the spring's dispersion on lines 0 and 1 only, MB_STAGES stretched allpasses each (24 x 4 in MRPH);
 *   - 3 early taps a side instead of 6, read from the one pre-delay line;
 *   - TONE's low-pass on the input instead of the output (the same for a linear tank, one filter not two);
 *   - every delay read once a block (linear, the fraction ramped over the block, the delay moving at most
 *     one sample a block: a knob turn bends the pitch at most 1/16, about a semitone, instead of clicking);
 *   - the coefficients worked out only when a knob moves, a quarter of them a block.
 *
 * Signal flow (one block, 32 samples at 48 kHz):
 *   L + R -> half-band 48 -> 24 kHz -> x send -> high-pass (one-pole) -> low-pass (SVF, the character's)
 *   -> low-pass (one-pole, TONE) -> pre-delay line -> early taps (3 a side, x their level)
 *   -> pre-delay read + 0.35 x the early taps -> 4 allpass diffusers -> tank: 4 lines, 0 and 1 through the
 *      dispersion, absorption (one-pole a line), output taps, rotations, the input added (+ - + -), written
 *   -> + early taps -> x wet -> half-band 24 -> 48 kHz -> out L, R (wet only)
 *
 * Page: as MRPH (A PRE, B DEC, C CHAR, D SIZE, E HPF, F TONE; knob values 0..127).
 *
 * Build options: MB_STAGES (even, default 6), MB_DOWN_HBS (the stronger half-band on the input too).
 */
#ifndef MRB_H
#define MRB_H

#include <stdint.h>
#include "fxmath.h"

#define MB_NL       4         /* tank lines */
#define MB_NSL      2         /* lines with the spring's dispersion (0 and 1) */
#ifndef MB_STAGES
#define MB_STAGES   6         /* stretched allpass stages a dispersive line (even) */
#endif
#define MB_ST       4         /* each stage in z^-4 at 24 kHz: dispersion peak at 3 kHz */
#define MB_ND       4         /* input diffusers */
#define MB_NE       3         /* early taps a side */
#define MB_BLK      32        /* the DSP's block, 48 kHz */
#define MB_HB       16        /* the same block at 24 kHz */
#define MB_LSEG     4096      /* a tank line's ring (24 kHz samples; a power of two) */
#define MB_PSEG     8192      /* the pre-delay's */
#define MB_DSEG     256       /* a diffuser's */
/* every ring is followed by a copy of its first block, so that a block's read never wraps */
#define MB_MEM      (MB_NL * (MB_LSEG + MB_HB) + (MB_PSEG + MB_HB) + MB_ND * (MB_DSEG + MB_HB))
#define MB_NFL      33        /* states flushed every block (Mrb.fl) */

enum {
    MB_PRE = 0,   /* A */
    MB_DEC,       /* B */
    MB_CHAR,      /* C */
    MB_SIZE,      /* D */
    MB_HPF,       /* E */
    MB_TONE,      /* F */
    MB_NPARAMS
};

/* the blended character (mrb.c has the three end points) */
enum {
    MBC_LINE = 0,                 /* 4 line lengths, ms */
    MBC_PRE = MBC_LINE + MB_NL,   /* pre-delay, ms */
    MBC_ELEV,                     /* early taps' level */
    MBC_ESPAN,                    /* their span, ms */
    MBC_DSCALE,                   /* decay scale */
    MBC_CPL0, MBC_CPL1,           /* rotation angle scales, (0,1)(2,3) then (0,2)(1,3) */
    MBC_DISP,                     /* stretched allpass coefficient */
    MBC_MODMS, MBC_MODHZ,         /* line modulation depth (ms) and rate (Hz) */
    MBC_HP, MBC_LP,               /* input band, Hz */
    MBC_DIFF,                     /* diffusers' share */
    MBC_OGAIN,                    /* output gain */
    MBC_N
};

typedef struct {
    float knob[MB_NPARAMS];       /* targets, 0..1 */
    float cur[MB_NPARAMS];        /* glided once a block */
    float c[MBC_N];               /* the character, from cur[MB_CHAR] */
    int32_t snap;                 /* 1: the next block jumps to the targets */
    int32_t dirty;                /* coefficient groups to work out again: 1 lines, 2 decay, 4 filters */
    int32_t phase;                /* the group this block may work out (0..3; 3: the rotations) */
    int32_t slewing;              /* >0: the pre-delay or an early tap is still moving */
    int32_t fq;                   /* the third of fl the next block flushes */
    uint32_t t;                   /* 24 kHz samples so far (a multiple of MB_HB) */

    uint32_t lseg[MB_NL];         /* where each ring starts in mem */
    uint32_t pseg;
    uint32_t dseg[MB_ND];
    int32_t ddel[MB_ND];          /* diffuser delays, samples */
    float lmax, pmax;             /* the longest delays the rings take */

    float lbase[MB_NL];           /* line delays without the modulation (24 kHz samples) */
    float loop[MB_NL];            /* loop lengths, for the absorption */
    float mod;                    /* modulation depth, samples */
    float lfi[MB_NL], lfo[MB_NL]; /* line LFOs: step a block, phase */
    float lda[MB_NL], ldb[MB_NL]; /* line delays at the block's start and end */
    float ag[MB_NL], ap[MB_NL];   /* absorption: input gain, pole */
    float rc0, rs0, rc1, rs1;     /* rotations */
    float al0, al1;               /* their modulation phases */
    float disp[MB_NSL];           /* allpass coefficients of the dispersive lines */
    float pret, pra, prb;         /* pre-delay: target, block start, block end */
    float edt[2 * MB_NE];         /* early taps (left, then right): targets */
    float eda[2 * MB_NE], edb[2 * MB_NE];   /* block start, end */
    float eg[2 * MB_NE];          /* their gains, with the level */
    float dgain;
    float send, sendt, wet, wett; /* gains at the block start, targets */
    float hpg, lpg, lpk, lph, tpg;   /* high-pass, SVF (g, r + g, 1/(1 + r g + g g)), TONE */

    /* states the block's flush keeps off denormals (the SHARC flushes them, the Mac does not): [0] high-pass,
       [1, 2] SVF, [3] TONE, [4..7] absorption; [8..15] half-band up L, [16..23] up R; [24..31] half-band
       down (4 used without MB_DOWN_HBS); [32] the odd input sample held over */
    float fl[MB_NFL];
    float chain[MB_NSL * MB_STAGES * MB_ST * 2];   /* dispersion states [line][stage pair][chain][4] */

    float x[MB_HB];               /* work: the tank's input */
    float y[MB_NSL][MB_HB];       /* work: the dispersive lines read */
    float el[MB_HB], er[MB_HB];   /* work: early taps */
    float ol[MB_HB], orr[MB_HB];  /* work: out at 24 kHz */
    float mem[MB_MEM];
} Mrb;

void mrb_init(Mrb *s);
void mrb_set(Mrb *s, int param, float value);      /* 0..127 */
void mrb_snap(Mrb *s);                             /* the next block starts settled */
/* wet output only; n a multiple of 32 (the DSP calls with 32); a remainder is left untouched */
void mrb_process(Mrb *s, const float *inL, const float *inR, float *outL, float *outR, int n);

#endif
