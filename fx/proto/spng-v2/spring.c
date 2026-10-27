/* SPDX-License-Identifier: GPL-2.0-or-later */
/*
 * spring.c - SPNG, a spring reverb for the reverb slot (T5.5). See spring.h and
 * notes/T5.5-spring-reverb.md.
 *
 * Choices:
 * - The dispersion is the spring's sound. A wave on a helical spring travels the
 *   faster the higher its frequency (below the spring's transition frequency, a
 *   few kHz), so one click arrives as a chirp that falls from the top to the
 *   bottom, and each trip along the spring stretches it more. A chain of
 *   first-order allpasses (c + z^-1)/(1 + c z^-1) with negative c (poles near DC)
 *   delays the low frequencies more than the high ones; inside the loop it
 *   disperses every round trip again.
 *   Each allpass is "stretched", H(z^2): twice the delay for the same work, and
 *   the delay curve mirrored above 3 kHz (the tank's quieter rising "high
 *   chirps"). A chirp needs phase: a 1 kHz-wide sweep of 10 ms takes about 30
 *   radians, and each first-order stage gives at most pi (2 pi stretched), which
 *   is why the stage count, not the rest, sets the cost. 16 stretched stages a
 *   spring delay 150 Hz about 7 ms (DRIP 64) to 11 ms (DRIP 127) more than 2.4 kHz
 *   per round trip, so every echo is smeared further than the one before.
 * - The tank runs at 12 kHz on a mono input, as a real tank (one input
 *   transducer, about 100 Hz..5 kHz). The half-band filters are polyphase
 *   allpass pairs (two first-order allpass branches in z^-2, one sample apart):
 *   the same allpass kernels as the springs.
 * - Every loop gain is below 1 (from DEC and the spring's own round trip, so the
 *   three springs die away together), and a cubic soft clip in each loop holds
 *   anything a resonance could build: the output is bounded at every setting.
 * - Coefficients without division (one-poles as 1 - exp(-w)), so the selcc build
 *   can match the Mac build bit for bit.
 * - Knob moves glide (50 ms), and the delays (TENS, PRE) also have a slew limit
 *   (half and a quarter sample per sample), so a fast turn bends the pitch of the
 *   tail (in the loop at most an octave down or 0.6 octave up) instead of clicking
 *   or reading backwards.
 * - MODE (2026-10-24) is a layer over the knobs: the spring's character (DRIP,
 *   TENS and the springs' spread, spring_mode_map); DEC, TONE, DWEL, HPF, PRE and
 *   WDTH stay direct. Until MODE is set the springs run as before, bit for bit.
 * - selcc: per-sample work in a few loops over 8 to 32 samples with the state in
 *   locals; two allpass kernels (one and two stages per pass) for the springs and
 *   the resampler; the helpers called at control rate only. No shift by an
 *   immediate amount; no table read in an expression with a fresh index (notes/
 *   T3.11 12.7); no negative number in a static table (notes/T5.5 4).
 */
#include "spring.h"

#define SP_FS      12000.0f
#define SP_RMASK   (SP_RING - 1)
#define SP_PMASK   (SP_PRE - 1)
#define SP_SM      0.013245f    /* control-rate one-pole, tau 50 ms: 1 - exp(-1/(0.05*1500)) */
#define SP_HZG     0.017998f    /* loop high-pass: TPT one-pole at 70 Hz, g/(1+g), g = tan(pi 70/12000) */
#define SP_GIN     0.5f         /* into the springs */
#ifndef SP_OUT
#define SP_OUT     1.4f
#endif
#define SP_OUT_C         /* wet output: at the defaults about as loud (RMS) as the stock reverb at DEC 64 on the same input */
#define SP_DMIN    24.0f        /* round trip limits (12 kHz samples) */
#define SP_DMAX    ((float)(SP_RING - 4))
#define SP_DSLEW   4.0f         /* at most this much change of a round trip per block: 0.5 per sample */
#define SP_PSLEW   2.0f         /* ... of the pre-delay per block: 0.25 per sample */
#define SP_PREMAX  3000.0f      /* 250 ms at 12 kHz */
#define SP_WOM     7.5539e-4f   /* 2 pi / (12000 ln 2): Hz -> the exponent of a one-pole */
#define SP_G60     8.3048e-4f   /* 3 log2(10) / 12000: -60 dB over one second, per sample, in log2 */
#define SP_TINY    1e-15f       /* states and loop samples below this are set to 0 (-300 dB): a decayed
                                   tank is exactly silent. (FX_UNDENORM is not enough here: it rounds a
                                   tiny value to one step of 1e-25, and a loop with a gain above 0.5,
                                   or an allpass with |c| > 0.5, then holds that step for ever.) */
/* x = 0 if |x| < SP_TINY (a statement; x a variable) */
#define SP_FLUSH(x) do { if ((x) < SP_TINY && (x) > -SP_TINY) (x) = 0.0f; } while (0)

/* the springs: round trips 1 : 1.173 : 1.387 (no simple ratios), a little less
   dispersion on the longer ones, panned left, right, centre */
#ifndef SPV2_R2
#define SPV2_R2 1.173f
#define SPV2_R3 1.387f
#endif
static const float SP_RATIO[SP_NS] = { 1.0f, SPV2_R2, SPV2_R3 };
static const float SP_APS[SP_NS] = { 1.0f, 0.96f, 0.92f };
/* pan + 1 (0 left, 2 right, 1 centre): no negative number in a static table, selcc writes
   every negative float constant of an initialiser wrong (-1.0f as -4.0f; notes/T5.5 4) */
static const float SP_PAN1[SP_NS] = { 0.0f, 2.0f, 1.0f };
/* the dispersion coefficients at DRIP 127 (spread, so the chirp sweeps through the
   middle of the band and not only the lows) */
static const float SP_AP[32] = {
    0.80f, 0.62f, 0.76f, 0.58f, 0.72f, 0.66f, 0.78f, 0.60f,
    0.74f, 0.64f, 0.70f, 0.56f, 0.79f, 0.63f, 0.68f, 0.61f,
    0.77f, 0.59f, 0.73f, 0.65f, 0.81f, 0.57f, 0.75f, 0.62f,
    0.71f, 0.67f, 0.79f, 0.60f, 0.69f, 0.64f, 0.78f, 0.58f
};
/* v2 prototype knobs (compile time) */
#ifndef SPV2_DWEL
#define SPV2_DWEL 0       /* DWEL: 0 the v1 law (-24..+24 dB, half made up), 1 0..+23.6 dB with SPV2_DPA of it made up,
                             2 SPV2_DLO..SPV2_DHI dB, made up (1/drive, or measured: SPV2_DAGC),
                             3 DIRT: the springs' input stays clean (v1's DWEL 64), the drive goes on the wet output */
#endif
#ifndef SPV2_RHI
#define SPV2_RHI 36.0f    /* DIRT: the drive at 127, dB (0 dB at 0) */
#endif
#ifndef SPV2_RCRV
#define SPV2_RCRV 1.06f   /* DIRT: the drive in dB follows (knob/127)^SPV2_RCRV */
#endif
#ifndef SPV2_RLV
#define SPV2_RLV 5.5f     /* DIRT: the level rises with the drive (the target at 127; measured +5.6..+6.3 dB) */
#endif
#ifndef SPV2_RAMX
#define SPV2_RAMX 3.0f    /* DIRT: log2 of the most the make-up adds over 1/drive (3 = +18 dB) */
#endif
#ifndef SPV2_RATC
#define SPV2_RATC 0.0008f /* DIRT: the power meters' one-pole coefficient per block (32 samples at 48 kHz): about 0.8 s */
#endif
#ifndef SPV2_DLO
#define SPV2_DLO 0.0f
#endif
#ifndef SPV2_DHI
#define SPV2_DHI 23.6f
#endif
#ifndef SPV2_DAGC
#define SPV2_DAGC 0       /* SPV2_DWEL 2: 1 the make-up follows the measured loss (power in / out, SPV2_DATC), up to SPV2_DAMX */
#endif
#ifndef SPV2_DATC
#define SPV2_DATC 0.0001f /* the power meters' one-pole coefficient at 12 kHz: about 0.8 s (0.0004, 0.2 s, pumps on drums) */
#endif
#ifndef SPV2_DAMX
#define SPV2_DAMX 2.0f    /* log2 of the most the make-up adds over 1/drive (2 = +12 dB) */
#endif
#ifndef SPV2_DPA
#define SPV2_DPA 1.0f
#endif
#ifndef SPV2_COUPLE
#define SPV2_COUPLE 0     /* 0 none, 1 Householder (full mix), 2 a rotation (partial) */
#endif
#ifndef SPV2_MD
#define SPV2_MD 0.0f      /* wobble depth: round-trip samples (12 kHz) */
#endif
#ifndef SPV2_MK
#define SPV2_MK 0.006f    /* wobble smoother per block (1500 Hz): tau about 0.11 s */
#endif
#ifndef SPV2_INTERP
#define SPV2_INTERP 0     /* loop read: 0 linear, 1 cubic (Catmull-Rom) */
#endif
#if SPV2_COUPLE == 1
static const float SP_MIX[9] = { 0.33333333f, -0.66666667f, -0.66666667f,
                                 -0.66666667f, 0.33333333f, -0.66666667f,
                                 -0.66666667f, -0.66666667f, 0.33333333f };
#elif SPV2_COUPLE == 2
#include "spmix.h"
#endif
static const int32_t SP_MPER[SP_NS] = { 467, 541, 613 };   /* blocks per wobble target */
/* MODE's characters (2026-10-24, the owner: "i like all the modes, can they live on a continuous
   knob?", then "size and dark should be separate from the continuous knob"): the spring's own
   character only, per anchor DRIP and TENS (knob values) and the springs' spread; DEC (size), TONE
   (dark), DWEL and HPF stay the knobs' own. Linear in between (TENS is a log law: log in time).
   Tension falls, the drip and the spread rise, all the way. No negative numbers (selcc). */
static const float SP_MV[3 * SP_NMODE] = {
    30.0f, 110.0f, 0.85f,      /*   0: taut short springs, little drip: a fast, bright boing */
    64.0f, 64.0f, 1.0f,        /*  64: the classic tank (the init patch) */
    127.0f, 16.0f, 1.2f        /* 127: slack long springs, the deepest drip: surf */
};

/* polyphase allpass half-bands, H(z) = (A0(z^2) + z^-1 A1(z^2)) / 2, A0 and A1 cascades of
   (c + z^-1) / (1 + c z^-1) at the lower rate; coefficients from a minimax fit of our own
   (the stop band's largest gain, given the transition band):
   48 <-> 24 kHz: flat to 6 kHz, -62 dB from 18 kHz (images and aliases of the 0..6 kHz band
   only land there); 24 <-> 12 kHz: flat (to 1e-5 dB) to 5 kHz, -65 dB from 7 kHz */
static const float SP_HBW0[SP_HW] = { 0.138025073f };
static const float SP_HBW1[SP_HW] = { 0.584683380f };
static const float SP_HBS0[SP_HS] = { 0.089588496f, 0.577111126f };
static const float SP_HBS1[SP_HS] = { 0.310671415f, 0.850563043f };

/* ---- knob laws ---- */
float spring_pre_ms(float knob)  { float x = fx_knob01(knob); return 250.0f * x * x; }
float spring_rt60_s(float knob)  { return 0.3f * fx_exp2(5.7370f * fx_knob01(knob)); }
float spring_drip(float knob)    { float x = fx_knob01(knob); return x * (2.0f - x); }
float spring_dwel_db(float knob) { return (fx_clampf(knob, 0.0f, 127.0f) - 64.0f) * (FX_DB_PER_LOG2 / 16.0f); }
float spring_hpf_hz(float knob)  { return 40.0f * fx_exp2(4.6438562f * fx_knob01(knob)); }
float spring_tone_hz(float knob) { return 600.0f * fx_exp2(4.0f * fx_knob01(knob)); }
float spring_width(float knob)   { return fx_knob01(knob); }
float spring_tens_ms(float knob) { return 50.4f * fx_exp2(-1.8f * fx_knob01(knob)); }

/* MODE -> its character, v[0..2] (spring.h): two segments, no division, no table read with a fresh
   index (selcc bug 3). Exact at the anchors: MODE 64 is the init patch bit for bit (the lower segment
   reaches it with f = 1 exactly; the upper one is written from 127 down, so 127 is exact too). */
void spring_mode_map(float mode, float *v)
{
    const float *a;
    float f;
    int i;
    mode = fx_clampf(mode, 0.0f, 127.0f);
    if (mode > 64.0f) {
        a = SP_MV + SP_NMODE;                      /* from the top anchor down to the middle one */
        f = (127.0f - mode) * 0.015873017f;        /* 1/63 */
        for (i = 0; i < SP_NMODE; i++) {
            float x = a[i + SP_NMODE];
            float y = a[i];
            v[i] = x + (y - x) * f;
        }
    } else {
        a = SP_MV;
        f = mode * 0.015625f;
        for (i = 0; i < SP_NMODE; i++) {
            float x = a[i];
            float y = a[i + SP_NMODE];
            v[i] = x + (y - x) * f;
        }
    }
}

/* the targets in the units cur[] glides in (spring.h) */
static void sp_targets(Spring *s)
{
    float *t = s->tgt;
    float k[SP_NINT];
    float x;
    int i;
    for (i = 0; i < SP_NINT; i++) k[i] = s->knob[i];
    if (s->mode_on) {
        /* MODE's character: the drip, the tension and the spread; every other knob is its own */
        float v[SP_NMODE];
        spring_mode_map(s->knob[SP_MODE], v);
        k[SP_DRIP] = v[0];
        k[SP_TENS] = v[1];
        s->t_spr = v[2];
    }
    x = fx_knob01(k[SP_PREDLY]);
    x = SP_PREMAX * x * x;
    t[SP_PREDLY] = (float)(int32_t)(x + 0.5f);                        /* whole samples: no interpolation at rest */
    t[SP_DEC] = -1.7369656f + 5.7370f * fx_knob01(k[SP_DEC]);        /* log2 0.3 s .. log2 16 s */
    t[SP_DRIP] = spring_drip(k[SP_DRIP]);
#if SPV2_DWEL == 3
    x = k[SP_DWEL] * (1.0f / 127.0f);                                  /* log2 of DIRT's drive: RHI dB x (knob/127)^RCRV */
    t[SP_DWEL] = x > 0.0f ? SPV2_RHI * 0.16609640f * fx_exp2(SPV2_RCRV * fx_log2(x)) : 0.0f;
#elif SPV2_DWEL == 2
    t[SP_DWEL] = (SPV2_DLO + k[SP_DWEL] * ((SPV2_DHI - SPV2_DLO) / 127.0f)) * 0.16609640f;
#elif SPV2_DWEL
    t[SP_DWEL] = k[SP_DWEL] * 0.031003937f;                            /* log2 of the drive: 0 dB at 0 .. +23.6 dB at 127 */
#else
    t[SP_DWEL] = (k[SP_DWEL] - 64.0f) * 0.0625f;                       /* log2 of the drive */
#endif
    t[SP_HPF] = 5.3219281f + 4.6438562f * fx_knob01(k[SP_HPF]);       /* log2 40 Hz .. log2 1 kHz */
    t[SP_TONE] = 9.2288187f + 4.0f * fx_knob01(k[SP_TONE]);           /* log2 600 Hz .. log2 9.6 kHz */
    t[SP_WDTH] = fx_knob01(k[SP_WDTH]);
    t[SP_TENS] = 9.2403125f - 1.8f * fx_knob01(k[SP_TENS]);           /* log2 604.8 samples (50.4 ms) .. */
    s->dirty = 0;
}

void spring_set(Spring *s, int param, float value)
{
    if (param < 0 || param >= SP_NPARAMS) return;
    s->knob[param] = fx_clampf(value, 0.0f, 127.0f);
    if (param == SP_MODE) s->mode_on = 1;
    s->dirty = 1;
}

/* low-pass one-pole coefficient 1 - exp(-2 pi f / 12 kHz) for log2 f */
static float sp_onepole(float lf)
{
    return 1.0f - fx_exp2(-SP_WOM * fx_exp2(lf));
}

/* the TPT one-pole's g/(1+g), g = tan(pi f / 12 kHz), for log2 f (up to 3 kHz), without a
   division: sin/(sin + cos) of pi f / 12 kHz, the quotient through log2 and exp2 */
static float sp_tpt(float lf)
{
    float w = (FX_PI / SP_FS) * fx_exp2(lf), sn, cs;
    sn = fx_sin_q(w);
    cs = fx_cos_q(w);
    return fx_exp2(fx_log2(sn) - fx_log2(sn + cs));
}

/* everything that follows from cur[]: called when a smoothed value moved */
#if SPV2_DWEL == 2 && SPV2_DAGC
/* DWEL's make-up: 1/drive plus the loss the saturator made over the last 0.8 s (half log2 of power in / out) */
static void sp_makeup(Spring *s)
{
    float l = 0.5f * (fx_log2(s->ein + 1e-9f) - fx_log2(s->eout + 1e-9f));
    l = fx_clampf(l, 0.0f, SPV2_DAMX);
    s->post = fx_exp2(l - s->cur[SP_DWEL]);
}
#endif

#if SPV2_DWEL == 3
/* DIRT's make-up: 1/drive plus the loss the soft clip made over the last 0.8 s (half log2 of power in / out) */
static void sp_dirt_makeup(Spring *s)
{
    float l = 0.5f * (fx_log2(s->rein + 1e-12f) - fx_log2(s->reout + 1e-12f));
    l = fx_clampf(l, 0.0f, SPV2_RAMX);
    s->rpost = fx_exp2(l - (1.0f - SPV2_RLV / SPV2_RHI) * s->cur[SP_DWEL]);
}
#endif

static void sp_coefs(Spring *s)
{
    const float *c = s->cur;
    float half = fx_exp2(c[SP_TENS]);              /* half round trip of spring 1 */
    float decay = SP_G60 * fx_exp2(-c[SP_DEC]);    /* -log2 of the gain per sample */
    float q = c[SP_DRIP];
    float w = c[SP_WDTH];
    int k, j;
    s->hpc = sp_tpt(c[SP_HPF]);
    s->lpc = sp_onepole(c[SP_TONE]);
    s->drive = fx_exp2(c[SP_DWEL]);
#if SPV2_DWEL == 3
    s->drive = 1.0f;
    s->post = 1.0f;
    s->rdrive = fx_exp2(c[SP_DWEL]);
    sp_dirt_makeup(s);
#elif SPV2_DWEL == 2 && SPV2_DAGC
    sp_makeup(s);
#elif SPV2_DWEL == 2
    s->post = fx_exp2(-c[SP_DWEL]);
#elif SPV2_DWEL
    s->post = fx_exp2(-SPV2_DPA * c[SP_DWEL]);
#else
    s->post = fx_exp2(-0.5f * c[SP_DWEL]);
#endif
    for (k = 0; k < SP_NS; k++) {
        SpLine *L = &s->sp[k];
        float r, dt, sc, p, x;
        r = SP_RATIO[k];
        r = 1.0f + (r - 1.0f) * s->spr;           /* the spread: exactly the ratio at 1 */
        sc = SP_APS[k];
        p = SP_PAN1[k] - 1.0f;
        dt = r * half;                             /* half this spring's round trip */
        dt = fx_clampf(dt, 0.5f * SP_DMIN, 0.5f * SP_DMAX);
        dt = 2.0f * (float)(int32_t)(dt + 0.5f);   /* an even number of samples: at rest both taps read
                                                      whole samples (linear interpolation would damp the
                                                      loop and shorten the decay) */
        L->dt = dt;
        /* the loop's delay: the ring plus the allpasses' delay in the middle of the band (about
           2 samples each); -60 dB after RT60 */
        L->g = fx_exp2(-decay * (dt + 2.0f * (float)SP_M));
        for (j = 0; j < SP_M; j++) L->a[j] = -q * sc * SP_AP[j];   /* negative: the poles near DC delay the lows */
        /* equal power: p = -1 left, 0 both at 0.707 (exactly: WDTH 0 is mono), +1 right */
        p = p * w;
        if (p == 0.0f) {
            L->gl = SP_OUT * 0.70710678f;
            L->gr = L->gl;
        } else {
            x = (1.0f + p) * (0.25f * FX_PI);
            L->gl = SP_OUT * fx_cos_q(x);
            L->gr = SP_OUT * fx_sin_q(x);
        }
    }
}

/* one step of a control-rate one-pole, snapped to the target when close (as se_glide) */
static float sp_glide(float x, float target, float b)
{
    float e_, d_;
    e_ = x + b * (target - x);
    d_ = e_ - target;
    FX_ABS(d_);
    if (d_ < 1e-6f) e_ = target;
    return e_;
}

/* x moved towards t by at most m */
static float sp_slew(float x, float t, float m)
{
    float d = t - x;
    FX_CLAMP(d, -m, m);
    return x + d;
}

void spring_snap(Spring *s)
{
    int p, k;
    if (s->dirty) sp_targets(s);
    for (p = 0; p < SP_NINT; p++) s->cur[p] = s->tgt[p];
    s->spr = s->t_spr;
    sp_coefs(s);
    for (k = 0; k < SP_NS; k++) {
        s->sp[k].d = s->sp[k].dt;
        s->sp[k].dd = 0.0f;
    }
    s->pd = s->cur[SP_PREDLY];
    s->dr0 = s->drive;
    s->po0 = s->post;
#if SPV2_DWEL == 3
    s->rdr0 = s->rdrive;
    s->rpo0 = s->rpost;
#endif
}

void spring_init(Spring *s)
{
    float *f = (float *)s;
    unsigned long i, n = sizeof(Spring) / sizeof(float);
    for (i = 0; i < n; i++) f[i] = 0.0f;           /* every field is a float or a 32-bit int */
    s->knob[SP_PREDLY] = 0.0f;
    s->knob[SP_DEC] = 64.0f;      /* 2.2 s */
    s->knob[SP_DRIP] = 64.0f;
    s->knob[SP_DWEL] = 64.0f;     /* 0 dB */
    s->knob[SP_HPF] = 0.0f;       /* 40 Hz */
    s->knob[SP_TONE] = 127.0f;    /* the open tank */
    s->knob[SP_WDTH] = 96.0f;
    s->knob[SP_TENS] = 64.0f;     /* 27 ms */
    s->knob[SP_MODE] = 64.0f;     /* the classic tank, once MODE is set */
    s->t_spr = 1.0f;
    s->spr = 1.0f;
    s->dirty = 1;
    s->sp[0].rnd = 0x12345u; s->sp[1].rnd = 0x9e3779b9u; s->sp[2].rnd = 0x7f4a7c15u;
    spring_snap(s);
}

/* the states of n floats below SP_TINY to 0 */
static void sp_flush(float *f, int n)
{
    int i;
    for (i = 0; i < n; i++) {
        float x = f[i];
        if (x < SP_TINY && x > -SP_TINY) f[i] = 0.0f;
    }
}

/* One control update at the start of every block: glide, the delay ramps; and every fourth block a
   quarter of the allpass states flushed (a spring's, or the half-bands'), so each every 16 blocks. */
static void sp_control(Spring *s)
{
    float *c = s->cur;
    const float *t = s->tgt;
    int p, k, moved = 0;
    float e;
    if (s->dirty) sp_targets(s);
    s->dr0 = s->drive;
    s->po0 = s->post;
#if SPV2_DWEL == 3
    s->rdr0 = s->rdrive;
    s->rpo0 = s->rpost;
#endif
    s->pd = sp_slew(s->pd, c[SP_PREDLY], SP_PSLEW);   /* where the last block's ramp ended */
    for (p = 0; p < SP_NINT; p++) {
        e = sp_glide(c[p], t[p], SP_SM);
        if (e != c[p]) moved = 1;
        c[p] = e;
    }
    if (s->mode_on) {
        e = sp_glide(s->spr, s->t_spr, SP_SM);
        if (e != s->spr) moved = 1;
        s->spr = e;
    }
    if (moved) sp_coefs(s);
#if SPV2_DWEL == 2 && SPV2_DAGC
    else sp_makeup(s);
#elif SPV2_DWEL == 3
    else sp_dirt_makeup(s);
#endif
    k = s->tick;
    s->tick = (k + 1) & 15;
    if (k == 0) sp_flush(s->sp[0].ap, 4 * SP_M);
    else if (k == 4) sp_flush(s->sp[1].ap, 4 * SP_M);
    else if (k == 8) sp_flush(s->sp[2].ap, 4 * SP_M);
    else if (k == 12) sp_flush(s->dw, (int)(sizeof s->dw + sizeof s->ds + sizeof s->uw + sizeof s->us) / (int)sizeof(float));
    for (k = 0; k < SP_NS; k++) {
        SpLine *L = &s->sp[k];
        L->d = L->d + 8.0f * L->dd;                   /* where the last block's ramp ended */
        if (--L->mcnt <= 0) {
            L->rnd = L->rnd * 1664525u + 1013904223u;
            L->mv = (float)(L->rnd >> 9) * (1.0f / 4194304.0f) - 1.0f;
            L->mcnt = SP_MPER[k];
        }
        L->m1 += SPV2_MK * (L->mv - L->m1);
        L->m2 += SPV2_MK * (L->m1 - L->m2);
        e = sp_slew(L->d, L->dt + SPV2_MD * L->m2, SP_DSLEW);
        L->dd = (e - L->d) * 0.125f;
        SP_FLUSH(L->lp);
        SP_FLUSH(L->hz);
    }
    SP_FLUSH(s->hps);
}

/* m stages of first-order allpasses over n pairs of samples, in place: the even samples b[2i]
   through (ce[j] + z^-1) / (1 + ce[j] z^-1), the odd ones b[2i+1] through co[j]'s, two chains of
   their own; st: per stage x[n-1], y[n-1] of the even chain, then of the odd one. n a multiple of
   4 (the body runs 4 pairs, written out). A spring's stretched allpass H(z^2) is ce = co on its
   samples in order; a half-band's two branches are ce != co on (x[2i], x[2i-1]) pairs. The states
   are flushed by sp_flush. selcc keeps every variable in its stack slot between statements, so a
   sample costs its loads and stores more than its arithmetic: sp_ap2x2 runs two stages per pass. */
static void sp_ap2(float *b, int n, const float *ce, const float *co, float *st, int m)
{
    int j, i;
    for (j = 0; j < m; j++) {
        const float ae = ce[j];
        const float ao = co[j];
        float xe = st[0];
        float ye = st[1];
        float xo = st[2];
        float yo = st[3];
        float *p = b;
        for (i = 0; i < n; i += 4) {
            float v;
            v = p[0]; ye = xe + ae * (v - ye); xe = v; p[0] = ye;
            v = p[1]; yo = xo + ao * (v - yo); xo = v; p[1] = yo;
            v = p[2]; ye = xe + ae * (v - ye); xe = v; p[2] = ye;
            v = p[3]; yo = xo + ao * (v - yo); xo = v; p[3] = yo;
            v = p[4]; ye = xe + ae * (v - ye); xe = v; p[4] = ye;
            v = p[5]; yo = xo + ao * (v - yo); xo = v; p[5] = yo;
            v = p[6]; ye = xe + ae * (v - ye); xe = v; p[6] = ye;
            v = p[7]; yo = xo + ao * (v - yo); xo = v; p[7] = yo;
            p += 8;
        }
        st[0] = xe;
        st[1] = ye;
        st[2] = xo;
        st[3] = yo;
        st += 4;
    }
}

/* as sp_ap2, two stages (j, j+1) per pass; m even. The same operations in the same order on
   every sample, so the same results as sp_ap2 */
static void sp_ap2x2(float *b, int n, const float *ce, const float *co, float *st, int m)
{
    int j, i;
    for (j = 0; j < m; j += 2) {
        const float ae = ce[j];
        const float ao = co[j];
        const float be = ce[j + 1];
        const float bo = co[j + 1];
        float xe = st[0];
        float ye = st[1];
        float xo = st[2];
        float yo = st[3];
        float ue = st[4];
        float we = st[5];
        float uo = st[6];
        float wo = st[7];
        float *p = b;
        for (i = 0; i < n; i += 4) {
            float v;
            v = p[0]; ye = xe + ae * (v - ye); xe = v; we = ue + be * (ye - we); ue = ye; p[0] = we;
            v = p[1]; yo = xo + ao * (v - yo); xo = v; wo = uo + bo * (yo - wo); uo = yo; p[1] = wo;
            v = p[2]; ye = xe + ae * (v - ye); xe = v; we = ue + be * (ye - we); ue = ye; p[2] = we;
            v = p[3]; yo = xo + ao * (v - yo); xo = v; wo = uo + bo * (yo - wo); uo = yo; p[3] = wo;
            v = p[4]; ye = xe + ae * (v - ye); xe = v; we = ue + be * (ye - we); ue = ye; p[4] = we;
            v = p[5]; yo = xo + ao * (v - yo); xo = v; wo = uo + bo * (yo - wo); uo = yo; p[5] = wo;
            v = p[6]; ye = xe + ae * (v - ye); xe = v; we = ue + be * (ye - we); ue = ye; p[6] = we;
            v = p[7]; yo = xo + ao * (v - yo); xo = v; wo = uo + bo * (yo - wo); uo = yo; p[7] = wo;
            p += 8;
        }
        st[0] = xe;
        st[1] = ye;
        st[2] = xo;
        st[3] = yo;
        st[4] = ue;
        st[5] = we;
        st[6] = uo;
        st[7] = wo;
        st += 8;
    }
}

/* the tank's output of the last block (lo[cur_lo ^ 1]) -> 48 kHz, into outL and outR: each sample
   doubled into a pair, the pair through the half-band's two branches is the next rate's two samples */
void sp_interp(Spring *s, float *outL, float *outR);
void sp_interp(Spring *s, float *outL, float *outR)
{
    float *t = s->wa;
    int c, m;
    for (c = 0; c < 2; c++) {
        const float *u = s->lo[s->cur_lo ^ 1][c];
        float *out = c ? outR : outL;
        for (m = 0; m < SP_LB; m++) {
            t[2 * m] = u[m];
            t[2 * m + 1] = u[m];
        }
        sp_ap2x2(t, SP_LB, SP_HBS0, SP_HBS1, s->us[c], SP_HS);
        for (m = 0; m < 2 * SP_LB; m++) {
            out[2 * m] = t[m];
            out[2 * m + 1] = t[m];
        }
        sp_ap2(out, 2 * SP_LB, SP_HBW0, SP_HBW1, s->uw[c], SP_HW);
    }
#if SPV2_DWEL == 3
    if (s->cur[SP_DWEL] == 0.0f && s->rdr0 == s->rdrive) {
        s->rein = s->reout = 0.0f;   /* DIRT 0: out of the way (the output as it is), the meters reset */
    } else {   /* DIRT: the wet output into the soft clip, both channels with one gain; power in and out for the make-up */
        float dr = s->rdr0, dri = (s->rdrive - s->rdr0) * (1.0f / FX_BLOCK);
        float po = s->rpo0, poi = (s->rpost - s->rpo0) * (1.0f / FX_BLOCK);
        float pin = 0.0f, pout = 0.0f;
        for (m = 0; m < FX_BLOCK; m++) {
            float a, b;
            dr += dri;
            po += poi;
            a = outL[m] * dr;
            b = outR[m] * dr;
            pin += a * a + b * b;
            FX_CLAMP(a, -1.5f, 1.5f);
            FX_CLAMP(b, -1.5f, 1.5f);
            a = a - 0.14814815f * a * a * a;
            b = b - 0.14814815f * b * b * b;
            pout += a * a + b * b;
            outL[m] = a * po;
            outR[m] = b * po;
        }
        s->rein += SPV2_RATC * (pin - s->rein);
        s->reout += SPV2_RATC * (pout - s->reout);
    }
#endif
}

/* one block of input (32 samples at 48 kHz) -> the tank -> lo[cur_lo] */
void sp_input(Spring *s, const float *inL, const float *inR);
void sp_input(Spring *s, const float *inL, const float *inR)
{
    float *v = s->wa, *e = s->wb, *x = s->wc;
    float *lo0 = s->lo[s->cur_lo][0], *lo1 = s->lo[s->cur_lo][1];
    const int32_t w0 = s->wp;
    float od, hs, hpc, dr, dri, po, poi, pd, pdi;
    int m, k;

    /* mono, 48 -> 24 kHz: pairs (x[2m], x[2m-1]) through the two branches */
    od = s->odw;
    for (m = 0; m < 2 * SP_LB; m++) {
        v[2 * m] = 0.5f * (inL[2 * m] + inR[2 * m]);
        v[2 * m + 1] = od;
        od = 0.5f * (inL[2 * m + 1] + inR[2 * m + 1]);
    }
    s->odw = od;
    sp_ap2(v, 2 * SP_LB, SP_HBW0, SP_HBW1, s->dw, SP_HW);
    /* the 24 kHz samples (each the mean of a pair), paired again for 24 -> 12 kHz (in place: the
       writes stay behind the reads) */
    od = s->ods;
    for (m = 0; m < SP_LB; m++) {
        float y0, y1;
        y0 = 0.5f * (v[4 * m] + v[4 * m + 1]);
        y1 = 0.5f * (v[4 * m + 2] + v[4 * m + 3]);
        v[2 * m] = y0;
        v[2 * m + 1] = od;
        od = y1;
    }
    s->ods = od;
    sp_ap2x2(v, SP_LB, SP_HBS0, SP_HBS1, s->ds, SP_HS);

    /* HPF, DWEL, into the pre-delay ring */
    hs = s->hps;
    hpc = s->hpc;
    dr = s->dr0;
    dri = (s->drive - dr) * 0.125f;
    po = s->po0;
    poi = (s->post - po) * 0.125f;
    for (m = 0; m < SP_LB; m++) {
        float y = 0.5f * (v[2 * m] + v[2 * m + 1]);
        {   /* TPT one-pole high-pass: y minus the low-pass */
            float u = (y - hs) * hpc;
            float l = u + hs;
            hs = l + u;
            y -= l;
        }
        dr += dri;
        po += poi;
#if SPV2_DWEL == 2
        y = y * dr;
#if SPV2_DAGC
        s->ein += SPV2_DATC * (y * y - s->ein);
#endif
        FX_CLAMP(y, -1.5f, 1.5f);
        y = y - 0.14814815f * y * y * y;
#if SPV2_DAGC
        s->eout += SPV2_DATC * (y * y - s->eout);
#endif
        y *= po;
#else
        y *= dr;
        FX_CLAMP(y, -1.5f, 1.5f);
        y = (y - 0.14814815f * y * y * y) * po;
#endif
        SP_FLUSH(y);
        s->pring[(w0 + m) & SP_PMASK] = y;
    }
    s->hps = hs;
    /* read it at the pre-delay (linear, the ramp of this block) */
    pd = s->pd;
    pdi = (sp_slew(pd, s->cur[SP_PREDLY], SP_PSLEW) - pd) * 0.125f;
    for (m = 0; m < SP_LB; m++) {
        int32_t di;
        float f, x0, x1;
        pd += pdi;
        di = (int32_t)pd;
        f = pd - (float)di;
        x0 = s->pring[(w0 + m - di) & SP_PMASK];
        x1 = s->pring[(w0 + m - di - 1) & SP_PMASK];
        x[m] = SP_GIN * (x0 + f * (x1 - x0));
        lo0[m] = 0.0f;
        lo1[m] = 0.0f;
    }

    /* the springs: the reads (and the output taps) of all three first, then the mix into each loop */
    for (k = 0; k < SP_NS; k++) {
        SpLine *L = &s->sp[k];
        float *rb = s->ring[k];
        float *rr = s->rr[k];
        float d = L->d;
        const float dd = L->dd;
        const float g = L->g;
        const float lpc = s->lpc;
        const float gl = L->gl;
        const float gr = L->gr;
        float lp = L->lp;
        float hz = L->hz;
        for (m = 0; m < SP_LB; m++) {
            int32_t di;
            float f, x0, x1, r, dh;
            d += dd;
            dh = 0.5f * d;
            di = (int32_t)dh;
            f = dh - (float)di;
            x0 = rb[(w0 + m - di) & SP_RMASK];
            x1 = rb[(w0 + m - di - 1) & SP_RMASK];
            r = x0 + f * (x1 - x0);
            lo0[m] += gl * r;
            lo1[m] += gr * r;
            di = (int32_t)d;
            f = d - (float)di;
            x0 = rb[(w0 + m - di) & SP_RMASK];
            x1 = rb[(w0 + m - di - 1) & SP_RMASK];
#if SPV2_INTERP == 1
            {
                float xm = rb[(w0 + m - di + 1) & SP_RMASK];
                float x2 = rb[(w0 + m - di - 2) & SP_RMASK];
                float c1 = 0.5f * (x1 - xm);
                float c2 = xm - 2.5f * x0 + 2.0f * x1 - 0.5f * x2;
                float c3 = 0.5f * (x2 - xm) + 1.5f * (x0 - x1);
                r = ((c3 * f + c2) * f + c1) * f + x0;
            }
#else
            r = x0 + f * (x1 - x0);
#endif
            lp += lpc * (r - lp);
            {
                float u = (lp - hz) * SP_HZG;
                float l = u + hz;
                hz = l + u;
                rr[m] = g * (lp - l);
            }
        }
        L->lp = lp;
        L->hz = hz;
    }
    for (k = 0; k < SP_NS; k++) {
        SpLine *L = &s->sp[k];
        float *rb = s->ring[k];
        for (m = 0; m < SP_LB; m++) {
#if SPV2_COUPLE
            e[m] = x[m] + SP_MIX[3 * k] * s->rr[0][m] + SP_MIX[3 * k + 1] * s->rr[1][m] + SP_MIX[3 * k + 2] * s->rr[2][m];
#else
            e[m] = x[m] + s->rr[k][m];
#endif
        }
        sp_ap2x2(e, SP_LB / 2, L->a, L->a, L->ap, SP_M);
        for (m = 0; m < SP_LB; m++) {
            float y = e[m];
            FX_CLAMP(y, -1.5f, 1.5f);
            y = y - 0.14814815f * y * y * y;
            SP_FLUSH(y);
            rb[(w0 + m) & SP_RMASK] = y;
        }
    }
    s->wp = (w0 + SP_LB) & SP_PMASK;
}

void spring_process(Spring *s, const float *inL, const float *inR,
                    float *outL, float *outR, int n)
{
    int i = 0;
    while (i < n) {
        if (s->pos == 0 && n - i >= FX_BLOCK) {
            /* a whole block: straight through (the input first, so in == out is fine) */
            sp_control(s);
            sp_input(s, inL + i, inR + i);           /* -> lo[cur_lo] */
            sp_interp(s, outL + i, outR + i);        /* <- lo[cur_lo ^ 1], the block before */
            s->cur_lo ^= 1;
            i += FX_BLOCK;
        } else {
            int p = s->pos;
            if (p == 0) sp_interp(s, s->ob[0], s->ob[1]);
            s->ib[p] = 0.5f * (inL[i] + inR[i]);   /* 0.5 (m + m) below is m exactly */
            outL[i] = s->ob[0][p];
            outR[i] = s->ob[1][p];
            i++;
            p++;
            if (p == FX_BLOCK) {
                sp_control(s);
                sp_input(s, s->ib, s->ib);
                s->cur_lo ^= 1;
                p = 0;
            }
            s->pos = p;
        }
    }
}
