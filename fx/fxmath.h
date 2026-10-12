/* SPDX-License-Identifier: GPL-2.0-or-later */
/*
 * fxmath.h - small helpers shared by the T3.11 effects.
 *
 * Portable C99, 32-bit float only, no libc. Meant to build both with clang on
 * the Mac and with selache's selcc for the SHARC+ (ADSP-2156x). Everything is
 * static inline so each effect file is self-contained.
 *
 * Accuracy (fitted in T3.11, see notes/T3.11-fx-workbench.md):
 *   fx_log2: max abs error 1.7e-5 (0.0001 dB when used for dB levels)
 *   fx_exp2: max rel error 1.2e-7 in the polynomial, plus float rounding
 */
#ifndef FX_MATH_H
#define FX_MATH_H

#include <stdint.h>

#define FX_SR        48000.0f      /* DSP sample rate (T3.4: 48000.0 stored at init) */
#define FX_BLOCK     32            /* DSP block length (T3.4: 32 samples, 1,500 blocks/s) */
#define FX_PI        3.14159265f
#define FX_LN2       0.69314718f
#define FX_DB_PER_LOG2 6.0205999f  /* 20*log10(2) */

typedef union { float f; uint32_t i; } fx_fu;
/* Bit casts through pointers, not the union: selcc miscompiles the union form
   in fx_exp2/fx_log2 (notes/T3.6-sharc-interpreter.md section 3.4). The Mac
   build uses -fno-strict-aliasing so these are well defined there too. */
static inline uint32_t fx_f2i(float f) { return *(uint32_t *)&f; }
static inline float fx_i2f(uint32_t i) { return *(float *)&i; }

/* Flush tiny values to zero: adding then subtracting 1e-18 rounds anything
   below about 1e-25 to exactly 0. Plain IEEE arithmetic; compilers may not
   fold it without fast-math. Used on every recursive state. */
static inline float fx_undenorm(float x)
{
    x += 1e-18f;
    return x - 1e-18f;
}

static inline float fx_absf(float x) { return x < 0.0f ? -x : x; }
static inline float fx_maxf(float a, float b) { return a > b ? a : b; }
static inline float fx_minf(float a, float b) { return a < b ? a : b; }
static inline float fx_clampf(float x, float lo, float hi)
{
    return x < lo ? lo : (x > hi ? hi : x);
}

/* log2(x) for x > 0 (x <= 0 returns a very negative number, about -126).
   The exponent is taken without a shift (the masked bits converted to float
   and scaled): selache's assembler mis-encodes negative shift immediates
   (notes/T3.11 section 12.7). */
static inline float fx_log2(float x)
{
    uint32_t u;
    float e, t, q;
    u = fx_f2i(x);
    if (!(x > 1.1754944e-38f)) return -126.0f;
    e = (float)(int32_t)(u & 0x7f800000u) * 1.1920929e-07f - 127.0f;
    u = (u & 0x007fffffu) | 0x3f800000u;   /* mantissa in [1,2) */
    t = fx_i2f(u) - 1.0f;
    q = 0.04526829f;
    q = q * t - 0.19351652f;
    q = q * t + 0.41524556f;
    q = q * t - 0.70886522f;
    q = q * t + 1.44187990f;
    return e + t * q;
}

/* 2^x, clamped to [-126, 126]. */
static inline float fx_exp2(float x)
{
    int32_t n;
    float f, p;
    x = fx_clampf(x, -126.0f, 126.0f);
    n = (int32_t)x;            /* truncates towards zero */
    if ((float)n > x) n -= 1;  /* floor */
    f = x - (float)n;          /* [0,1) */
    p = 0.0018854038f;
    p = p * f + 0.0089728993f;
    p = p * f + 0.055836598f;
    p = p * f + 0.24015244f;
    p = p * f + 0.69315254f;
    p = 1.0f + p * f;
    return fx_i2f(fx_f2i(p) + ((uint32_t)n << 23));
}

static inline float fx_db2lin(float db) { return fx_exp2(db * (1.0f / FX_DB_PER_LOG2)); }
static inline float fx_lin2db(float g)  { return fx_log2(g) * FX_DB_PER_LOG2; }

/* sin(2*pi*p) for any p: wraps p to [0,1). Max error about 0.001
   (parabola plus one correction step): fine for LFOs, not for oscillators. */
static inline float fx_sin2pi(float p)
{
    float x, y;
    int32_t n = (int32_t)p;
    if ((float)n > p) n -= 1;
    p -= (float)n;                 /* [0,1) */
    x = p < 0.5f ? p : p - 1.0f;   /* [-0.5,0.5) */
    y = 8.0f * x - 16.0f * x * fx_absf(x);  /* parabola, peak 1 at x=0.25 */
    return 0.225f * (y * fx_absf(y) - y) + y;
}

/* sin and cos of x for 0 <= x <= pi/2, Taylor to x^11 / x^10 (error < 1e-6). */
static inline float fx_sin_q(float x)
{
    float x2 = x * x;
    return x * (1.0f + x2 * (-1.0f/6.0f + x2 * (1.0f/120.0f + x2 * (-1.0f/5040.0f
           + x2 * (1.0f/362880.0f - x2 * (1.0f/39916800.0f))))));
}
static inline float fx_cos_q(float x)
{
    float x2 = x * x;
    return 1.0f + x2 * (-0.5f + x2 * (1.0f/24.0f + x2 * (-1.0f/720.0f
           + x2 * (1.0f/40320.0f - x2 * (1.0f/3628800.0f)))));
}

/* Prewarped SVF gain g = tan(pi*fc/fs), fc clamped to [10 Hz, 0.45*fs].
   One division; meant for the control rate, not per sample. */
static inline float fx_svf_g(float fc)
{
    float w;
    fc = fx_clampf(fc, 10.0f, 0.45f * FX_SR);
    w = FX_PI * fc / FX_SR;
    return fx_sin_q(w) / fx_cos_q(w);
}

/* One-pole smoothing coefficient for a time constant tau (seconds) at a given
   update rate: a = exp(-1/(tau*rate)). y += (1-a)*(x-y). */
static inline float fx_onepole_a(float tau, float rate)
{
    if (tau <= 0.0f) return 0.0f;
    return fx_exp2(-1.0f / (tau * rate * FX_LN2));
}

/* A 32-bit linear congruential generator (Numerical Recipes constants):
   deterministic and shift-free (selache's assembler mis-encodes negative shift
   immediates, notes/T3.11 12.7; the xorshift used before 2026-10-06 needed
   one). Its low bits are weak, so only the top bits are used, through a
   signed conversion to float. */
static inline uint32_t fx_rand(uint32_t *s)
{
    uint32_t x = *s * 1664525u + 1013904223u;
    *s = x;
    return x;
}
/* uniform in [-1,1] (the top 24 bits) */
static inline float fx_rands(uint32_t *s)
{
    return (float)(int32_t)fx_rand(s) * 4.6566129e-10f;
}
/* uniform in [0,1] */
static inline float fx_randu(uint32_t *s)
{
    return 0.5f + 0.5f * fx_rands(s);
}

/* Soft saturator: cubic, slope 1 at 0, reaches +-1 with slope 0 at |x| = 1.5,
   flat beyond. No division (cheap on the SHARC). */
static inline float fx_sat(float x)
{
    x = fx_clampf(x, -1.5f, 1.5f);
    return x - 0.14814815f * x * x * x;   /* 4/27 */
}

/* Trapezoidal (TPT) state-variable filter, after A. Simper.
   g = tan(pi fc/fs), k = 1/Q. One call = one sample; outputs lp, bp, hp. */
typedef struct { float ic1, ic2; } FxSvf;
typedef struct { float a1, a2, a3, k; } FxSvfCoef;

static inline void fx_svf_coef(FxSvfCoef *c, float g, float k)
{
    c->k = k;
    c->a1 = 1.0f / (1.0f + g * (g + k));
    c->a2 = g * c->a1;
    c->a3 = g * c->a2;
}

/* v0 in; writes lp, bp; hp = v0 - k*bp - lp. */
static inline void fx_svf_tick(FxSvf *s, const FxSvfCoef *c, float v0, float *lp, float *bp)
{
    float v3 = v0 - s->ic2;
    float v1 = c->a1 * s->ic1 + c->a2 * v3;
    float v2 = s->ic2 + c->a2 * s->ic1 + c->a3 * v3;
    s->ic1 = 2.0f * v1 - s->ic1;
    s->ic2 = 2.0f * v2 - s->ic2;
    *bp = v1;
    *lp = v2;
}

static inline void fx_svf_flush(FxSvf *s)
{
    s->ic1 = fx_undenorm(s->ic1);
    s->ic2 = fx_undenorm(s->ic2);
}

/* Map a 0..127 knob value (fractional allowed) to [0,1]. */
static inline float fx_knob01(float v) { return fx_clampf(v, 0.0f, 127.0f) * (1.0f / 127.0f); }
/* Bipolar knob: 64 = 0, 0 = -1, 127 = +63/64. */
static inline float fx_knobbi(float v) { return (fx_clampf(v, 0.0f, 127.0f) - 64.0f) * (1.0f / 64.0f); }
/* Log map 0..127 -> lo..hi */
static inline float fx_knoblog(float v, float lo, float hi)
{
    return lo * fx_exp2(fx_knob01(v) * fx_log2(hi / lo));
}

/* ---- Statement macros for the per-sample loops (T3.11, 2026-10-06) ----
   selcc (selache's compiler for the SHARC) inlines no function, so a call
   costs 15-80 instructions on the DSP. The hot loops use these macros
   instead. Each one does exactly the float operations of the function of
   the same name, in the same order, so the results are bit-identical
   (the Mac build uses -ffp-contract=off, like the SHARC, which has no fused
   multiply-add). Arguments are plain variables (evaluated more than once). */

#define FX_UNDENORM(x) do { (x) += 1e-18f; (x) -= 1e-18f; } while (0)
#define FX_ABS(x) do { if ((x) < 0.0f) (x) = -(x); } while (0)
#define FX_CLAMP(x, lo, hi) do { if ((x) < (lo)) (x) = (lo); else if ((x) > (hi)) (x) = (hi); } while (0)

/* fx_svf_tick on state (ic1, ic2) and coefficients (a1, a2, a3); writes lp, bp */
#define FX_SVF(ic1, ic2, a1, a2, a3, v0, lp, bp) do { \
        float v3_ = (v0) - (ic2); \
        float v1_ = (a1) * (ic1) + (a2) * v3_; \
        float v2_ = (ic2) + (a2) * (ic1) + (a3) * v3_; \
        (ic1) = (v1_ + v1_) - (ic1);    /* = 2*v1 - ic1 exactly; saves a constant load */ \
        (ic2) = (v2_ + v2_) - (ic2); \
        (bp) = v1_; \
        (lp) = v2_; \
    } while (0)

/* out = fx_log2(x); x must be a float variable */
#define FX_LOG2(x, out) do { \
        if (!((x) > 1.1754944e-38f)) { \
            (out) = -126.0f; \
        } else { \
            uint32_t u_ = *(uint32_t *)&(x); \
            float e_ = (float)(int32_t)(u_ & 0x7f800000u) * 1.1920929e-07f - 127.0f, t_; \
            u_ = (u_ & 0x007fffffu) | 0x3f800000u; \
            t_ = *(float *)&u_ - 1.0f; \
            (out) = e_ + t_ * ((((0.04526829f * t_ - 0.19351652f) * t_ + 0.41524556f) * t_ \
                                - 0.70886522f) * t_ + 1.44187990f); \
        } \
    } while (0)

/* out = fx_exp2(x); x is clamped in place */
#define FX_EXP2(x, out) do { \
        int32_t n_; \
        float f_, p_; \
        uint32_t u_; \
        FX_CLAMP(x, -126.0f, 126.0f); \
        n_ = (int32_t)(x); \
        if ((float)n_ > (x)) n_ -= 1; \
        f_ = (x) - (float)n_; \
        p_ = 1.0f + ((((0.0018854038f * f_ + 0.0089728993f) * f_ + 0.055836598f) * f_ \
                      + 0.24015244f) * f_ + 0.69315254f) * f_; \
        u_ = *(uint32_t *)&p_ + ((uint32_t)n_ << 23); \
        (out) = *(float *)&u_; \
    } while (0)

#endif
