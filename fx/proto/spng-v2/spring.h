/* SPDX-License-Identifier: GPL-2.0-or-later */
/*
 * spring.h - SPNG, a spring reverb for the Digitone II reverb slot (T5.5).
 *
 * Portable C99, float only, no libc, no malloc. Our own design from the published
 * ideas on spring reverb modelling: a spring is a waveguide loop whose waves are
 * dispersed (the low frequencies travel slower than the high ones, so a click
 * comes out as a falling chirp, the "drip"), and the dispersion is a chain of
 * first-order allpass filters inside the loop (spectral delay filters, the
 * "stretched" kind, H(z^2)), so every round trip smears the echo further.
 * Meant to pair with the tape echo (TAPE) as a classic tape echo unit paired its
 * echo with a spring tank; a pack choice, the stock reverb stays the default.
 *
 * The tank runs at 12 kHz (a real tank passes about 100 Hz..5 kHz), which is what
 * makes it affordable: the input is summed to mono (a real tank has one input
 * transducer) and decimated 48 -> 24 -> 12 kHz by two polyphase allpass half-band
 * filters, the three springs run on 8 samples per 32-sample block, and the stereo
 * output is interpolated back the same way. The resampling costs one block (32
 * samples, 0.67 ms) of latency: the output of a block is the tank's answer to
 * the block before.
 *
 * Signal flow (one block):
 *   in L+R -> mono -> half-band 48 -> 24 -> half-band 24 -> 12 kHz
 *   -> HPF (one-pole) -> DWEL: drive into a cubic soft saturator, partly made up
 *   -> PRE (pre-delay, glided) -> three springs in parallel, each:
 *        v = input + g x loop filters(ring read at the round trip D)
 *        v -> 16 stretched allpasses (DRIP scales their coefficients)
 *          -> cubic soft clip (bounds the loop) -> ring
 *        output tap: the ring at D/2 (the far end of the spring: the first arrival
 *        comes after half a round trip, the echoes every round trip)
 *        loop filters: TONE low-pass (one-pole) and a fixed 70 Hz high-pass
 *        g: from DEC (RT60) and the spring's round trip, always below 1
 *   -> the three outputs panned by WDTH (spring 1 left, 2 right, 3 centre)
 *   -> half-band 12 -> 24 -> half-band 24 -> 48 kHz -> out L, R (wet only)
 * TENS sets the round trips (the three springs at 1 : 1.173 : 1.387).
 * MODE (the page's C since 2026-10-24) is a macro over these: the spring's own
 * character on one knob, from taut short springs with little drip (0) through the
 * classic tank (64, the init patch) to slack long springs with the deepest drip
 * (127). It sets DRIP, TENS and the springs' spread (spring_mode_map); size (DEC),
 * darkness (TONE), DWEL and HPF stay their own knobs. Until MODE is set the springs
 * follow DRIP and TENS, so the earlier renders (and the workbench's DRIP and TENS)
 * are as they were.
 * The output is the wet signal only, like the stock reverb: the FX bus adds VOL.
 *
 * State 43,020 bytes (three 2,048-sample spring rings and a 4,096-sample pre-delay
 * ring at 12 kHz), inside the stock reverb's DDR (0x59114 = 364,820 bytes).
 *
 * Parameters take the DN2 knob value 0..127 (fractional allowed). The proposed page
 * (notes/T5.5-spring-reverb.md): A PRE, B DEC, C MODE, D DWEL, E HPF, F TONE, G WDTH;
 * DRIP and TENS are the workbench's (and MODE's) internal knobs.
 */
#ifndef SPRING_H
#define SPRING_H

#include <stdint.h>
#include "fxmath.h"

#define SP_NS    3        /* springs */
#ifndef SP_M
#define SP_M     16       /* dispersion allpasses per spring (each in z^-2), even, 16 or 32 */
#endif
#define SP_RING  2048     /* per spring, 12 kHz samples (171 ms), a power of two */
#define SP_PRE   4096     /* pre-delay ring, 12 kHz samples (341 ms), a power of two */
#define SP_LB    8        /* 12 kHz samples per 32-sample block */
#define SP_HW    1        /* coefficients per branch, half-band 48 <-> 24 kHz */
#define SP_HS    2        /* coefficients per branch, half-band 24 <-> 12 kHz */

enum {
    SP_PREDLY = 0, /* A PRE: pre-delay 0..250 ms, square law (32 = 16 ms, 64 = 63 ms) */
    SP_DEC,        /* B DEC: decay time (RT60) 0.3..16 s, log (64 = 2.2 s) */
    SP_DRIP,       /* C DRIP: dispersion, the chirp: 0 = none (a metallic flutter), 64 = a classic
                      tank, 127 = a deep drip (the allpass coefficients scaled by sqrt(v/127)) */
    SP_DWEL,       /* D DWEL: drive into the tank, -24..+24 dB into a soft saturator, half of it made
                      up after (64 = 0 dB, clean at the unit's levels; 127 splashes) */
    SP_HPF,        /* E HPF: high-pass on the tank input, 40 Hz..1 kHz, log */
    SP_TONE,       /* F TONE: low-pass in the loops, 600 Hz..9.6 kHz, log (127 = the open tank; at 12 kHz
                      that corner leaves the band nearly as it is) */
    SP_WDTH,       /* G WDTH: 0 = mono (a real tank), 127 = spring 1 left, spring 2 right */
    SP_TENS,       /* H TENS: tension: half round trip of spring 1, 50 ms (slack, 0) .. 14.5 ms
                      (tight, 127), log; 64 = 27 ms */
    SP_NINT,       /* the 8 above are the internal parameters (the glides) */
    SP_MODE = SP_NINT, /* C MODE (page): the springs' character, 0 taut, little drip .. 64 classic .. 127
                      slack, deep surf drip; once set, it drives DRIP, TENS and the spread */
    SP_NPARAMS
};
#define SP_NMODE 3        /* what MODE sets: DRIP and TENS as knob values, and the spread */

typedef struct {
    float a[SP_M];            /* allpass coefficients now (DRIP x the spring's table) */
    float ap[4 * SP_M];       /* allpass states: x[n-1], y[n-1] of the even and of the odd phase */
    float d, dd;              /* round trip at the block start (12 kHz samples), its step per sample */
    float dt;                 /* the round trip TENS asks for */
    float g;                  /* loop gain */
    float lp, hz;             /* loop low-pass state, the loop high-pass's low-pass state */
    float gl, gr;             /* output gains */
    float mv, m1, m2;         /* v2: the wobble's target and its two smoothers */
    uint32_t rnd;             /* v2: the wobble's random state */
    int32_t mcnt;             /* v2: blocks to the next target */
} SpLine;

typedef struct {
    float knob[SP_NPARAMS];
    int dirty;
    int mode_on;              /* MODE has been set: it drives the springs */
    float t_spr, spr;         /* the springs' spread (1 = the ratios 1 : 1.173 : 1.387), target and glided */

    /* per parameter: the target (from the knob) and the control-rate smoothed value, in
       physical units: PRE samples, log2 RT60, DRIP scale, log2 drive, log2 HPF Hz, log2 TONE
       Hz, width 0..1, log2 half round trip (samples) */
    float tgt[SP_NINT];
    float cur[SP_NINT];

    /* from cur: */
    float hpc, lpc;           /* one-pole coefficients: input high-pass, loop low-pass */
    float drive, post;        /* DWEL gains at the block end; the block ramps to them */
    float dr0, po0;           /* ... at the block start */
    float pd;                 /* pre-delay at the block start (samples) */
    float hps;                /* input high-pass state */

    /* the resampler: decimation (mono) and interpolation (per channel) states;
       per branch and coefficient x[n-1], y[n-1]; one run of floats (sp_flush) */
    float dw[2 * 2 * SP_HW], ds[2 * 2 * SP_HS];
    float uw[2][2 * 2 * SP_HW], us[2][2 * 2 * SP_HS];
    int tick;                 /* which states the next block flushes */
    float odw, ods;           /* the last odd input sample of each decimation stage */

    float lo[2][2][SP_LB];    /* the tank's stereo output at 12 kHz, the last two blocks */
    int cur_lo;               /* which of lo[] the next block writes */
    float wa[32], wb[SP_LB], wc[SP_LB];   /* work buffers */

    /* the FIFO for calls that do not come in whole 32-sample blocks */
    float ib[FX_BLOCK];       /* mono input of the block being collected */
    float ob[2][FX_BLOCK];    /* output of the block being played */
    int pos;                  /* place in the block (0..31) */

    int32_t wp;               /* next write: pre-delay ring and spring rings (12 kHz samples) */

    float rr[SP_NS][SP_LB];   /* v2: each loop's filtered read, times its gain */
    SpLine sp[SP_NS];
    float pring[SP_PRE];
    float ring[SP_NS][SP_RING];
} Spring;

void spring_init(Spring *s);
void spring_set(Spring *s, int param, float value);
/* jump all smoothers to their targets (after init + set, before audio) */
void spring_snap(Spring *s);
/* wet output only; any n >= 0; the result does not depend on how the stream is cut
   into calls (the work runs in 32-sample blocks of the stream, one block late) */
void spring_process(Spring *s, const float *inL, const float *inR,
                    float *outL, float *outR, int n);

/* knob -> physical, for the tests and the write-up */
float spring_pre_ms(float knob);
float spring_rt60_s(float knob);
float spring_drip(float knob);          /* the scale on the allpass coefficients, 0..1 */
float spring_dwel_db(float knob);       /* drive into the saturator */
float spring_hpf_hz(float knob);
float spring_tone_hz(float knob);
float spring_width(float knob);
float spring_tens_ms(float knob);       /* half round trip of spring 1 (the first arrival) */
/* MODE -> v[SP_NMODE]: the DRIP and TENS knob values its character stands for, and the springs'
   spread (scales the ratios' distance from 1) */
void spring_mode_map(float mode, float *v);

#endif
