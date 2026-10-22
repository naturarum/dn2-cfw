/* SPDX-License-Identifier: GPL-2.0-or-later */
/*
 * spaceecho.c - Roland Space Echo (RE-201) style tape echo for the Digitone II
 * delay slot (T5.2). See spaceecho.h for the signal flow and
 * notes/T5.2-space-echo.md for the knob laws and the reasoning behind the
 * constants marked [guess].
 *
 * Reused from tapedelay.c (FTD1, on the unit): the tape ring and its 4-point
 * Hermite read (the same polynomial and index arithmetic; the cubic evaluated
 * as weights shared by the two tracks, the read position moved per sample and
 * set exactly every control block), the tape glide of the delay time (one-pole
 * with a slew limit), the wow and flutter sources (rates, sine and noise mix),
 * the AGE hiss and crackle (envelope, levels, event planning; same arithmetic),
 * the cubic saturator with the drive and inverse drive ramps (same arithmetic),
 * the TPT state-variable loop filters, the control-rate glide macro and the
 * selcc rules of notes/T3.11 section 11 (state in locals, no calls in the
 * sample loop, flags set with if statements, one declaration per initialised
 * variable). tapedelay.c itself is unchanged.
 *
 * What is new:
 * - three heads at 1x, 2x, 3x the head-1 time on one tape, each with its own
 *   glide (head k glides k times slower, so every head bends by the same pitch
 *   ratio, the farther heads for longer, as a real tape does when its motor
 *   changes speed), and the glide's rate changes smoothly (motor inertia), so
 *   the bend starts without a corner;
 * - wow and flutter as a tape speed deviation eps(t): head k is offset by
 *   M(t - tau_k) - M(t), M the integral of eps, so it plays at the pitch
 *   1 + eps(t) - eps(t - tau_k) (the speed now against the speed when that
 *   bit of tape was recorded); M is kept per control block in a history;
 * - a band filter (resonant high-pass and low-pass around CENTER, BW wide, the
 *   resonance rising as the band narrows), bypassed exactly at BW 127;
 * - a mixing matrix for the three PAN modes (input, feedback, each head's
 *   output), every coefficient glided, so MODE, PAN and WIDTH never click;
 * - a ceiling on the wet output (T5.3 review): untouched up to 0.5, at most
 *   0.7, so a runaway of three heads stays near the stock delay's level.
 */
#include "spaceecho.h"

/* ---- tape and heads ---- */
#define SE_GLIDE_TAU  0.12f      /* head-1 glide, s (tapedelay.c's GLIDE_TAU); head k: k x this */
#define SE_GLIDE_SLEW 0.5f       /* max delay change per sample on every head: playback 0.5x..1.5x */
#define SE_GLIDE_ACC  0.00052f   /* max change of that rate per sample: full slew after 20 ms (motor inertia;
                                    without it the speed jumps at once, a corner in the waveform) */
#define SE_TMIN       64.0f      /* shortest head delay, samples (the TIME law's shortest is 300) */
#define SE_DMAX       169984.0f  /* head-1 maximum, (SE_RING - 2048)/3: head 3 + wow + the read fit */
#define SE_TMAX       (3.0f * SE_DMAX + 300.0f)  /* a head never glides past this */
#define SE_MOD_MAX    1500.0f    /* wow/flutter offset limit, samples (AGE 127 reaches about 300); also
                                    at most half the head's delay, so a read stays well behind the write */
#define SE_MOD_STEP   8.0f       /* the offset moves at most this much per block (0.25 sample per sample) */
#define SE_PB         0.03278f   /* control-rate one-pole, tau 20 ms: 1 - exp(-1/(0.02*1500)) */
#define SE_SLOWB      0.0022197f /* tau 0.3 s: wow/flutter depth glides (a fast change is a bend) */

/* ---- AGE: one knob for the wear, a = AGE/127; all [guess], set for "subtle" ---- */
/* wow and flutter: tapedelay.c's rates and mix, depths now a tape speed deviation */
#define SE_WOW_HZ   0.55f
#define SE_FLUT_HZ  6.5f
#define SE_FLUT2_HZ 11.3f
#define SE_WOWN_K   31.0f        /* noise std 0.022 -> 0.7 (tapedelay.c) */
#define SE_FLUTN_K  8.4f
#define SE_WOW_MAX  0.006f       /* speed deviation of the wow sine at AGE 127, x a^2: 0.6 %, 0.15 % at 64 */
#define SE_FLUT_MAX 0.003f       /* flutter, x a^2: 0.3 %, 0.075 % at 64 */
#define SE_M_LEAK   0.000133f    /* leak of the speed integral per block (tau 5 s), keeps it bounded */
/* top-end loss: a one-pole low-pass in the loop, coefficient 2^(-SE_HF_OCT a): about 13.1 kHz
   at AGE 32, 8.5 kHz at 64, 4.6 kHz at 127; exactly no filter at AGE 0. 1.6 until 2026-10-12
   (6.5 kHz at 64); the owner wanted "a little more top range" */
#define SE_HF_OCT   1.15f
/* drive into the saturator 2^(SE_DRV_OCT a): +3.9 dB at AGE 64 (FTD1's DRIV 32), +7.8 dB at 127 */
#define SE_DRV_OCT  1.2954f
/* hiss and crackle: tapedelay.c's AGE laws, which the owner approved (2026-10-06) */
#define SE_HISS_MAX    0.0062f
#define SE_CK_RATE_MAX 6.0f
#define SE_CK_AMP      0.10f
#define SE_HISS_LP     0.5441f
#define SE_ENV_ATT     0.0645f
#define SE_ENV_REL     0.000444f

/* ---- band filter (CENTER, BW) ---- */
#define SE_LC0      5.6438562f   /* log2(50 Hz): CENTER 0 */
#define SE_CTR_OCT  8.0f         /* CENTER 127: 12.8 kHz */
#define SE_LB0      (-1.0f)      /* log2 of the narrowest width: 0.5 octave (BW 0) */
#define SE_LB_RANGE 5.3219281f   /* log2(40): 20 octaves at BW 127 */
#define SE_RES_LB   3.0f         /* resonance from widths below 2^3 = 8 octaves (BW 96) ... */
#define SE_RES_SPAN 4.0f         /* ... to full at 2^-1 = 0.5 octave (BW 0) */
#define SE_Q_OCT    2.3074f      /* Q of both edges 0.707 .. 3.5 = 0.707 x 2^2.3074 [guess] */
#define SE_TRIM_OCT 1.83f        /* passband trim 2^(-1.83 r^2): one pass peaks at about +6 dB at BW 0 [guess] */
#define SE_BYP_STEPS 8.0f        /* BW 119..127 fades the filter out; 127 = no filter */
#define SE_DCK      0.0019615f   /* feedback DC blocker, one-pole 15 Hz: 1 - exp(-2 pi 15/48000) */

/* ---- random pan ---- */
#define SE_PSLEW    0.0416667f   /* position change per control block: a full swing in 32 ms */

/* ---- the wet ceiling (T5.3 review, 2026-10-13) ----
   The heads are summed at unity and ALT/RND put a head at up to sqrt 2 on its side, so with the tape
   held at +-1 by the saturator the wet output reached 3 sqrt 2 = 4.24 when three heads ran away, 18 dB
   over the stock delay's own runaway (0.53 in sharcvm). A memoryless limiter on the wet output only
   (the loop, the tape and the feedback sums are untouched): exactly linear up to SE_LIM_T, then the
   quadratic knee y = T + u - u^2 / (4 (C - T)), u = |x| - T, which reaches SE_LIM_C with zero slope at
   |x| = 2 C - T = 0.9, and flat beyond. On the unit a full-scale input reaches the delay slot at about
   0.13, so every echo short of a runaway stays below the knee and is unchanged bit for bit. */
#define SE_LIM_T    0.5f         /* the knee: below this the output is untouched */
#define SE_LIM_C    0.7f         /* the ceiling, +2.5 dB over the stock delay's runaway */
#define SE_LIM_U    0.4f         /* 2 (C - T): the knee's width */
#define SE_LIM_Q    1.25f        /* 1 / (4 (C - T)) */

float spaceecho_time_samples(float knob, float bpm)
{
    /* the stock and FTD1 law: (v+1)/128 of a whole note, BPM clamp 30..300 */
    float d;
    bpm = fx_clampf(bpm, 30.0f, 300.0f);
    d = (fx_clampf(knob, 0.0f, 127.0f) + 1.0f) * (90000.0f / bpm);
    return fx_clampf(d, SE_TMIN, SE_DMAX);
}

/* MODE index -> heads (bit 0 = head 1). The order of the RE-202's modes 1-7 (singles,
   then 1+2, 2+3, 1+3, all; Roland's RE-202 manual), which is the order of the RE-201's
   echo modes 5-11 [inferred]. An if chain, not a table: selcc bug 3 (notes/T3.11 12.7). */
static int se_heads(int m)
{
    if (m <= 0) return 1;
    if (m == 1) return 2;
    if (m == 2) return 4;
    if (m == 3) return 3;
    if (m == 4) return 6;
    if (m == 5) return 5;
    return 7;
}
int spaceecho_heads(float mode_knob) { return se_heads((int)(fx_clampf(mode_knob, 0.0f, 6.0f) + 0.5f)); }

/* INTENSITY on the stock delay's FDBK law (T5.4, SC01; the owner 2026-10-18: "Yes give our delay the stock law"):
   the loop gain is FDBK / 64, so the display's 100 % (knob 64) is endless repeats and above it the repeats grow
   until the tape's saturator (and, on the way out, the wet ceiling) holds them, as on the stock delay (127: 198 %).
   SC02 (the owner 2026-10-21: MODE 4 running away at about FDBK 32 was "weird"): the playing heads share it, each
   feeding back 1/2 or 1/3 of it (se_mix_targets), so 64 is endless in every MODE; SC01 gave each head FDBK / 64, so
   two heads ran away from 32 and three from 21. SE01 had 1.1 x^1.5 here (x = FDBK / 127). A leaf with no call:
   1/64 is exact, so the gain is the knob scaled exactly; the knob is clamped to 0..127 as fx_knob01 clamps it. */
float spaceecho_fb(float knob)
{
    float x = knob;
    if (x > 127.0f) x = 127.0f;
    if (x > 0.0f) return x * 0.015625f;
    return 0.0f;
}

float spaceecho_center_hz(float knob) { return fx_exp2(SE_LC0 + SE_CTR_OCT * fx_knob01(knob)); }
float spaceecho_bw_oct(float knob)    { return fx_exp2(SE_LB0 + SE_LB_RANGE * fx_knob01(knob)); }

/* resonance amount 0..1 from the log2 width */
static float se_res(float lb)
{
    float r = (SE_RES_LB - lb) * (1.0f / SE_RES_SPAN);
    if (r < 0.0f) r = 0.0f;
    if (r > 1.0f) r = 1.0f;
    return r;
}
float spaceecho_q(float bw_knob)
{
    return 0.70710678f * fx_exp2(SE_Q_OCT * se_res(SE_LB0 + SE_LB_RANGE * fx_knob01(bw_knob)));
}
float spaceecho_trim(float bw_knob)
{
    float r = se_res(SE_LB0 + SE_LB_RANGE * fx_knob01(bw_knob));
    return fx_exp2(-SE_TRIM_OCT * r * r);
}

static void se_targets(SpaceEcho *s)
{
    float x;
    int m;
    s->t_delay = spaceecho_time_samples(s->knob[SE_TIME], s->bpm);
    s->t_fb = spaceecho_fb(s->knob[SE_INT]);
    x = fx_knob01(s->knob[SE_AGE]);
    s->t_age = x;
    s->t_wow = SE_WOW_MAX * x * x;
    s->t_flut = SE_FLUT_MAX * x * x;
    s->t_drv = fx_exp2(SE_DRV_OCT * x);
    s->t_lc = SE_LC0 + SE_CTR_OCT * fx_knob01(s->knob[SE_CTR]);
    s->t_lb = SE_LB0 + SE_LB_RANGE * fx_knob01(s->knob[SE_BW]);
    x = (127.0f - fx_clampf(s->knob[SE_BW], 0.0f, 127.0f)) * (1.0f / SE_BYP_STEPS);
    if (x > 1.0f) x = 1.0f;
    s->t_byp = x;
    x = fx_knob01(s->knob[SE_WID]);
    if (x != s->t_wid) { s->t_wid = x; s->mixdirty = 1; }
    m = (int)(fx_clampf(s->knob[SE_MODE], 0.0f, 6.0f) + 0.5f);
    if (m != s->mode) { s->mode = m; s->mixdirty = 1; }
    m = (int)(fx_clampf(s->knob[SE_PAN], 0.0f, 2.0f) + 0.5f);
    if (m != s->pan) { s->pan = m; s->mixdirty = 1; }
    s->dirty = 0;
}

void spaceecho_set(SpaceEcho *s, int param, float value)
{
    if (param < 0 || param >= SE_NPARAMS) return;
    s->knob[param] = fx_clampf(value, 0.0f, 127.0f);
    s->dirty = 1;
}

void spaceecho_set_bpm(SpaceEcho *s, float bpm)
{
    s->bpm = fx_clampf(bpm, 30.0f, 300.0f);
    s->dirty = 1;
}

/* band filter from the smoothed log2 centre and width (control rate) */
static void se_filter_coefs(SpaceEcho *s)
{
    float r = se_res(s->lb);
    float half = 0.5f * fx_exp2(s->lb);                    /* half the width, octaves */
    float k = 1.41421356f * fx_exp2(-SE_Q_OCT * r);        /* 1/Q */
    fx_svf_coef(&s->hpc, fx_svf_g(fx_exp2(s->lc - half)), k);
    fx_svf_coef(&s->lpc, fx_svf_g(fx_exp2(s->lc + half)), k);
    s->trim = fx_exp2(-SE_TRIM_OCT * r * r);
}

/* equal-power pan normalised to 1 in the middle: p = -1 left (sqrt 2, 0), 0 (1, 1), +1 right */
static void se_pan_gains(float p, float *gl, float *gr)
{
    float x;
    if (p == 0.0f) {                      /* exact at the ends and the middle */
        *gl = 1.0f;
        *gr = 1.0f;
        return;
    }
    if (p <= -1.0f) {
        *gl = 1.41421356f;
        *gr = 0.0f;
        return;
    }
    if (p >= 1.0f) {
        *gl = 0.0f;
        *gr = 1.41421356f;
        return;
    }
    x = (1.0f + p) * (0.25f * FX_PI);
    *gl = 1.41421356f * fx_cos_q(x);
    *gr = 1.41421356f * fx_sin_q(x);
}

/* The mixing coefficients the current MODE, PAN, WIDTH (and random positions) ask for.
   Tracks A and B. MONO: A = L, B = R, feedback on the same track, every head plays both
   tracks with the width w (0 = mono, 1 = as they are). ALT: the input, summed to mono,
   records on A; the feedback crosses tracks, so a signal's passes alternate between A and
   B; heads 1 and 3 play A at -w (left) and B at +w, head 2 the other way round. RND: the
   input to mono on both tracks, feedback on the same track, head k plays (A+B)/2 at its
   random position w x pos_k. Every head is scaled by its MODE gain (0 or 1); into the feedback
   also by its share of the loop gain (1, 1/2 or 1/3 of it with one, two or three heads playing).
   An if chain, not a table: selcc bug 3 (notes/T3.11 12.7). */
static void se_mix_targets(const SpaceEcho *s, float *t)
{
    float w = s->t_wid, g[3], al, ar, bl, br, x, sh;
    int k, hm = se_heads(s->mode);
    for (k = 0; k < SE_NMIX; k++) t[k] = 0.0f;
    g[0] = 0.0f;
    g[1] = 0.0f;
    g[2] = 0.0f;
    if (hm & 1) g[0] = 1.0f;
    if (hm & 2) g[1] = 1.0f;
    if (hm & 4) g[2] = 1.0f;
    sh = 1.0f;
    if (hm == 3 || hm == 5 || hm == 6) sh = 0.5f;
    if (hm == 7) sh = 0.33333334f;
    t[8] = g[0] * sh;
    t[9] = g[1] * sh;
    t[10] = g[2] * sh;
    if (s->pan == SE_PAN_ALT) {
        t[0] = 0.5f; t[1] = 0.5f;               /* A <- (L+R)/2, B <- nothing */
        t[5] = 1.0f; t[6] = 1.0f;               /* A <- sum B, B <- sum A */
        se_pan_gains(-w, &al, &ar);
        se_pan_gains(w, &bl, &br);
        for (k = 0; k < 3; k++) {
            if (k == 1) {                       /* head 2: A on the right, B on the left */
                t[11 + 4 * k] = g[k] * bl; t[12 + 4 * k] = g[k] * al;
                t[13 + 4 * k] = g[k] * br; t[14 + 4 * k] = g[k] * ar;
            } else {
                t[11 + 4 * k] = g[k] * al; t[12 + 4 * k] = g[k] * bl;
                t[13 + 4 * k] = g[k] * ar; t[14 + 4 * k] = g[k] * br;
            }
        }
    } else if (s->pan == SE_PAN_RND) {
        t[0] = 0.5f; t[1] = 0.5f; t[2] = 0.5f; t[3] = 0.5f;
        t[4] = 1.0f; t[7] = 1.0f;
        for (k = 0; k < 3; k++) {
            se_pan_gains(w * s->pos[k], &al, &ar);
            x = 0.5f * g[k];
            t[11 + 4 * k] = x * al; t[12 + 4 * k] = x * al;
            t[13 + 4 * k] = x * ar; t[14 + 4 * k] = x * ar;
        }
    } else {
        t[0] = 1.0f; t[3] = 1.0f;
        t[4] = 1.0f; t[7] = 1.0f;
        al = 0.5f + 0.5f * w;
        bl = 0.5f - 0.5f * w;
        for (k = 0; k < 3; k++) {
            t[11 + 4 * k] = g[k] * al; t[12 + 4 * k] = g[k] * bl;
            t[13 + 4 * k] = g[k] * bl; t[14 + 4 * k] = g[k] * al;
        }
    }
}

/* Jump every smoother to its target (use after init + set, before audio). */
void spaceecho_snap(SpaceEcho *s)
{
    float t[SE_NMIX];
    int k;
    if (s->dirty) se_targets(s);
    for (k = 0; k < 3; k++) {
        s->tau[k] = s->t_delay * (float)(k + 1);
        s->vel[k] = 0.0f;
        s->mod_i[k] = 0.0f;
        s->mod_e[k] = s->mod[k];
        s->pos[k] = s->ptgt[k];
    }
    s->fb = s->t_fb; s->fb_e = s->t_fb; s->fb_i = 0.0f;
    s->lc = s->t_lc;
    s->lb = s->t_lb;
    se_filter_coefs(s);
    s->age = s->t_age;
    s->ac = fx_exp2(-SE_HF_OCT * s->age);
    s->wowA = s->t_wow;
    s->flutA = s->t_flut;
    s->drv = s->t_drv;
    s->drive = s->drv * s->trim; s->drive_e = s->drive; s->drive_i = 0.0f;
    s->inv_drive = 1.0f / s->drv; s->inv_drive_e = s->inv_drive; s->inv_drive_i = 0.0f;
    s->byp = s->t_byp; s->byp_e = s->t_byp; s->byp_i = 0.0f;
    se_mix_targets(s, t);
    for (k = 0; k < SE_NMIX; k++) {
        s->mx[k] = t[k];
        s->mx_e[k] = t[k];
        s->mx_i[k] = 0.0f;
    }
    s->mixdirty = 0;
    s->mixsettled = 1;
    s->mxramp = 0;
}

void spaceecho_init(SpaceEcho *s, uint32_t seed)
{
    int32_t i;
    int k;
    for (i = 0; i < 2 * SE_RING; i++) s->ring[i] = 0.0f;
    for (i = 0; i < SE_HIST; i++) s->hist[i] = 0.0f;
    s->w = 0;
    s->hw = 0;
    s->M = 0.0f;
    s->knob[SE_TIME] = 31.0f;   /* head 1 a quarter note, heads 2 and 3 a half and a dotted half */
    s->knob[SE_MODE] = 3.0f;    /* heads 1+2 */
    s->knob[SE_INT] = 50.0f;    /* 78 %, 39 % per head (SC01: 78 % per head; SE01's law: 27 %) */
    s->knob[SE_AGE] = 40.0f;
    s->knob[SE_CTR] = 64.0f;
    s->knob[SE_BW] = 127.0f;    /* no filter */
    s->knob[SE_WID] = 96.0f;
    s->knob[SE_PAN] = 1.0f;     /* ALT */
    s->bpm = 120.0f;
    s->dirty = 1;
    s->mode = -1;
    s->pan = -1;
    s->t_wid = -1.0f;
    s->ph_w = 0.0f; s->ph_f = 0.0f; s->ph_f2 = 0.25f;
    s->nw1 = 0.0f; s->nw2 = 0.0f; s->nf1 = 0.0f; s->nf2 = 0.0f;
    s->rng = seed ? seed : 0x1234567u;
    s->hrng = 0x9e3779b9u ^ s->rng;
    s->crng = 0x7f4a7c15u ^ s->rng;
    s->prng = 0x2545f491u ^ s->rng;
    for (k = 0; k < 3; k++) {
        s->ad[k] = 1.0f - fx_onepole_a(SE_GLIDE_TAU * (float)(k + 1), FX_SR);
        s->mod[k] = 0.0f;
        s->ptgt[k] = fx_rands(&s->prng);
        s->pcnt[k] = 0.0f;
    }
    for (k = 0; k < 2; k++) {
        s->hp[k].ic1 = 0.0f; s->hp[k].ic2 = 0.0f;
        s->lp[k].ic1 = 0.0f; s->lp[k].ic2 = 0.0f;
        s->ck[k].ic1 = 0.0f; s->ck[k].ic2 = 0.0f;
        s->hz[k] = 0.0f;
        s->az[k] = 0.0f;
        s->dc[k] = 0.0f;
    }
    s->env = 0.0f;
    s->blk_peak = 0.0f;
    s->hg = 0.0f; s->hg_i = 0.0f;
    s->ck_count = 4800;
    s->ck_impL = 0.0f; s->ck_impR = 0.0f;
    fx_svf_coef(&s->ckc, fx_svf_g(2000.0f), 1.4f);
    s->ctl_left = 0;
    spaceecho_snap(s);
}

/* one step of a control-rate one-pole, snapped to the target when close (tapedelay.c's
   TD_GLIDE; not "e_ = ..., d_ = e_ - ...": selcc drops e_, notes/T3.11 12.7) */
#define SE_GLIDE(out, x, target, b) do { \
        float e_, d_; \
        e_ = (x) + (b) * ((target) - (x)); \
        d_ = e_ - (target); \
        FX_ABS(d_); \
        if (d_ < 1e-6f) e_ = (target); \
        (out) = e_; \
    } while (0)

/* One control update: runs at the start of every 32-sample control block. Every value
   ramped per sample starts the block exactly at the end value planned for the last block
   (so a glide lands exactly on its target, and the stream can be cut anywhere). */
static void se_control(SpaceEcho *s)
{
    const float b = SE_PB;
    const float inv = 1.0f / (float)FX_BLOCK;
    const float cw = 0.0062637f;       /* 1-exp(-2pi*1.5/1500) */
    const float cf = 0.0803287f;       /* 1-exp(-2pi*20/1500) */
    float e, pk, eps, back, fr, tgt, t[SE_NMIX];
    int32_t bi, i0, i1;
    int k, recoef = 0, settled, moved = 0, any;

    if (s->dirty) se_targets(s);

    s->fb = s->fb_e;
    s->drive = s->drive_e;
    s->inv_drive = s->inv_drive_e;
    s->byp = s->byp_e;
    for (k = 0; k < 3; k++) s->mod[k] = s->mod_e[k];

    /* heads: a glide within 0.001 sample of its target, nearly at rest, lands on it */
    for (k = 0; k < 3; k++) {
        tgt = s->t_delay * (float)(k + 1);
        e = s->tau[k] - tgt;
        FX_ABS(e);
        pk = s->vel[k];
        FX_ABS(pk);
        if (e < 1e-3f && pk < 1e-5f) {
            s->tau[k] = tgt;
            s->vel[k] = 0.0f;
        }
    }

    /* each head's read position, exactly from its delay (tapedelay.c's index arithmetic),
       one sample back: se_run advances it before every read */
    for (k = 0; k < 3; k++) {
        tgt = s->tau[k];
        bi = (int32_t)tgt;
        fr = (tgt - (float)bi) + s->mod[k];
        i0 = (int32_t)fr;
        if ((float)i0 > fr) i0 -= 1;
        s->rf[k] = fr - (float)i0;
        i1 = s->w - (bi + i0) - 1;
        if (i1 < 0) i1 += SE_RING;
        s->rq[k] = i1;
    }

    /* band filter: centre and width smoothed at the control rate, coefficients on change */
    SE_GLIDE(e, s->lc, s->t_lc, b); if (e != s->lc) recoef = 1; s->lc = e;
    SE_GLIDE(e, s->lb, s->t_lb, b); if (e != s->lb) recoef = 1; s->lb = e;
    if (recoef) se_filter_coefs(s);
    SE_GLIDE(e, s->byp, s->t_byp, b);   s->byp_i = (e - s->byp) * inv; s->byp_e = e;
    if (s->byp == 0.0f && e == 0.0f) {
        /* no filter: start from rest when the band closes again */
        for (k = 0; k < 2; k++) {
            s->hp[k].ic1 = 0.0f; s->hp[k].ic2 = 0.0f;
            s->lp[k].ic1 = 0.0f; s->lp[k].ic2 = 0.0f;
        }
    }

    /* AGE: top-end loss coefficient, drive, wow/flutter depths */
    SE_GLIDE(e, s->age, s->t_age, b);
    if (e != s->age) {
        s->age = e;
        s->ac = fx_exp2(-SE_HF_OCT * e);
    }
    if (s->ac >= 1.0f) { s->az[0] = 0.0f; s->az[1] = 0.0f; }
    SE_GLIDE(s->drv, s->drv, s->t_drv, b);
    e = s->drv * s->trim;                       s->drive_i = (e - s->drive) * inv; s->drive_e = e;
    e = 1.0f / s->drv;                          s->inv_drive_i = (e - s->inv_drive) * inv; s->inv_drive_e = e;
    SE_GLIDE(e, s->fb, s->t_fb, b);             s->fb_i = (e - s->fb) * inv; s->fb_e = e;
    if (s->fb == 0.0f && e == 0.0f) { s->dc[0] = 0.0f; s->dc[1] = 0.0f; }

    /* random pan: each head jumps to a new place once per its own period; the position
       moves there at most SE_PSLEW per block (runs in every PAN mode, used in RND) */
    for (k = 0; k < 3; k++) {
        s->pcnt[k] += (float)FX_BLOCK;
        if (s->pcnt[k] >= s->tau[k]) {
            s->pcnt[k] -= s->tau[k];
            if (s->pcnt[k] >= s->tau[k]) s->pcnt[k] = 0.0f;
            s->ptgt[k] = fx_rands(&s->prng);
        }
        e = s->ptgt[k] - s->pos[k];
        if (e != 0.0f) {
            moved = 1;
            if (e > SE_PSLEW) s->pos[k] += SE_PSLEW;
            else if (e < -SE_PSLEW) s->pos[k] -= SE_PSLEW;
            else s->pos[k] = s->ptgt[k];
        }
    }
    if (moved && s->pan == SE_PAN_RND) s->mixdirty = 1;

    /* mixing coefficients: glide each to its target, ramped per sample */
    if (s->mxramp) {
        for (k = 0; k < SE_NMIX; k++) s->mx[k] = s->mx_e[k];
    }
    if (s->mixdirty || !s->mixsettled) {
        se_mix_targets(s, t);
        s->mixdirty = 0;
        settled = 1;
        any = 0;
        for (k = 0; k < SE_NMIX; k++) {
            SE_GLIDE(e, s->mx[k], t[k], b);
            if (e != t[k]) settled = 0;
            if (e != s->mx[k]) any = 1;
            s->mx_i[k] = (e - s->mx[k]) * inv;
            s->mx_e[k] = e;
        }
        s->mixsettled = settled;
        s->mxramp = any;
    } else if (s->mxramp) {
        for (k = 0; k < SE_NMIX; k++) s->mx_i[k] = 0.0f;
        s->mxramp = 0;
    }

    /* wow and flutter: the tape speed deviation eps over this block, its integral M (in
       samples, leaky) into the history, and each head's offset at the end of the block,
       M(t - tau_k) - M(t), ramped per sample */
    SE_GLIDE(s->wowA, s->wowA, s->t_wow, SE_SLOWB);
    SE_GLIDE(s->flutA, s->flutA, s->t_flut, SE_SLOWB);
    s->ph_w += SE_WOW_HZ * (float)FX_BLOCK / FX_SR;     if (s->ph_w >= 1.0f) s->ph_w -= 1.0f;
    s->ph_f += SE_FLUT_HZ * (float)FX_BLOCK / FX_SR;    if (s->ph_f >= 1.0f) s->ph_f -= 1.0f;
    s->ph_f2 += SE_FLUT2_HZ * (float)FX_BLOCK / FX_SR;  if (s->ph_f2 >= 1.0f) s->ph_f2 -= 1.0f;
    s->nw1 += cw * (fx_rands(&s->rng) - s->nw1);
    s->nw2 += cw * (s->nw1 - s->nw2);
    s->nf1 += cf * (fx_rands(&s->rng) - s->nf1);
    s->nf2 += cf * (s->nf1 - s->nf2);
    eps = s->wowA * (0.8f * fx_sin2pi(s->ph_w) + 0.3f * SE_WOWN_K * s->nw2)
        + s->flutA * (0.7f * fx_sin2pi(s->ph_f) + 0.3f * fx_sin2pi(s->ph_f2) + 0.4f * SE_FLUTN_K * s->nf2);
    s->M = s->M + (float)FX_BLOCK * eps - SE_M_LEAK * s->M;
    FX_UNDENORM(s->M);
    if (++s->hw >= SE_HIST) s->hw = 0;
    s->hist[s->hw] = s->M;
    for (k = 0; k < 3; k++) {
        back = s->tau[k] * (1.0f / (float)FX_BLOCK);
        if (back < 0.0f) back = 0.0f;
        bi = (int32_t)back;
        fr = back - (float)bi;
        i0 = s->hw - bi;
        if (i0 < 0) i0 += SE_HIST;
        i1 = i0 - 1;
        if (i1 < 0) i1 += SE_HIST;
        e = s->hist[i0] + fr * (s->hist[i1] - s->hist[i0]) - s->M;
        FX_CLAMP(e, -SE_MOD_MAX, SE_MOD_MAX);
        tgt = -0.5f * s->tau[k];
        if (e < tgt) e = tgt;
        tgt = s->mod[k] - SE_MOD_STEP;
        if (e < tgt) e = tgt;
        tgt = s->mod[k] + SE_MOD_STEP;
        if (e > tgt) e = tgt;
        s->mod_i[k] = (e - s->mod[k]) * inv;
        s->mod_e[k] = e;
    }

    /* AGE noise: the playback envelope (peak of the last block, attack 10 ms, release
       1.5 s) sets the hiss and crackle levels (tapedelay.c) */
    pk = s->blk_peak;
    s->blk_peak = 0.0f;
    if (s->age > 0.0f) {
        if (pk > s->env) s->env += SE_ENV_ATT * (pk - s->env);
        else s->env += SE_ENV_REL * (pk - s->env);
        FX_UNDENORM(s->env);
    } else {
        s->env = 0.0f;
    }
    e = SE_HISS_MAX * s->age * s->age * s->env;
    pk = s->hg;
    FX_ABS(pk);
    if (e == 0.0f && pk < 1e-7f) s->hg = 0.0f;
    s->hg_i = (e - s->hg) * inv;

    /* denormal guard on recursive state, once per control block */
    for (k = 0; k < 2; k++) {
        FX_UNDENORM(s->hp[k].ic1); FX_UNDENORM(s->hp[k].ic2);
        FX_UNDENORM(s->lp[k].ic1); FX_UNDENORM(s->lp[k].ic2);
        FX_UNDENORM(s->ck[k].ic1); FX_UNDENORM(s->ck[k].ic2);
        FX_UNDENORM(s->hz[k]);
        FX_UNDENORM(s->az[k]);
        FX_UNDENORM(s->dc[k]);
    }
}

/* crackle event (tapedelay.c's td_crackle_event): one soft tick, an impulse through a broad
   band-pass at 1.2-3.5 kHz, random level (mostly tiny) and pan, scaled by AGE and the
   playback envelope, then an exponential gap to the next one */
static void se_crackle_event(SpaceEcho *s)
{
    float u, a, p, fc, rate = SE_CK_RATE_MAX * s->age * s->age;
    if (rate <= 0.0f) {
        s->ck_count = 4800;
        return;
    }
    u = fx_randu(&s->crng);
    a = SE_CK_AMP * s->age * s->env * u * u * u;
    if (fx_rand(&s->crng) & 0x40000000u) a = -a;
    fc = 1200.0f + 2300.0f * fx_randu(&s->crng);
    fx_svf_coef(&s->ckc, fx_svf_g(fc), 1.4f);
    p = fx_randu(&s->crng);
    s->ck_impL = a * (1.0f - 0.6f * p);
    s->ck_impR = a * (0.4f + 0.6f * p);
    u = fx_randu(&s->crng);
    if (u < 1e-7f) u = 1e-7f;
    p = -fx_log2(u) * FX_LN2 * FX_SR / rate;
    s->ck_count = (int32_t)fx_clampf(p, 1.0f, 1e8f);
}

/* the crackle events of the next n samples, before se_run runs them (td_crackle_plan) */
static void se_crackle_plan(SpaceEcho *s, int n)
{
    int32_t nev = 0, pos = 0, cnt = s->ck_count;
    s->ev_c0.a1 = s->ckc.a1;
    s->ev_c0.a2 = s->ckc.a2;
    s->ev_c0.a3 = s->ckc.a3;
    s->ev_c0.k = s->ckc.k;
    s->ev_count0 = cnt;
    while (pos + cnt - 1 < n) {
        pos += cnt - 1;
        s->ck_count = 0;
        se_crackle_event(s);
        cnt = s->ck_count;
        s->ev_pos[nev] = pos;
        s->ev_imp[nev][0] = s->ck_impL;
        s->ev_imp[nev][1] = s->ck_impR;
        s->ev_c[nev].a1 = s->ckc.a1;
        s->ev_c[nev].a2 = s->ckc.a2;
        s->ev_c[nev].a3 = s->ckc.a3;
        s->ev_c[nev].k = s->ckc.k;
        nev++;
        pos++;
    }
    s->ev_pos[nev] = n;
    s->ev_count = cnt - (n - pos);
}

/* The tape glide of one head (tapedelay.c's one-pole with a slew limit, plus an acceleration
   limit): the delay t moves at the rate v towards the target, v follows the one-pole's rate
   (at most SE_GLIDE_SLEW) changing by at most SE_GLIDE_ACC per sample. */
#define SE_TAPE_GLIDE(t, v, tg, ad, x) do { \
        (x) = (ad) * ((tg) - (t)); \
        FX_CLAMP(x, -SE_GLIDE_SLEW, SE_GLIDE_SLEW); \
        (x) = (x) - (v); \
        FX_CLAMP(x, -SE_GLIDE_ACC, SE_GLIDE_ACC); \
        (v) += (x); \
        (t) += (v); \
        FX_CLAMP(t, SE_TMIN, SE_TMAX); \
    } while (0)

/* One head: 4-point Hermite read of both tracks. The read position is the ring index i0 and
   the fraction f towards the older neighbour (delay = (w - i0) + f, as tapedelay.c's head),
   set exactly from the delay at the start of every control block (se_control) and moved
   here per sample: the tape advances one sample, the delay grows by dl (glide rate plus the
   wow/flutter ramp, |dl| < 1). The cubic is the same Hermite (Catmull-Rom) polynomial as
   tapedelay.c's, written as four weights computed once per head and applied to both tracks
   (at f = 0 they are exactly 0, 1, 0, 0). Writes ha, hb; reads ring. The fraction's
   parameter is not called f: selcc's preprocessor substitutes a parameter named f inside
   float literals (1.0f became 1.0f0), a fifth selcc bug (notes/T5.2). */
#define SE_READ(i0, fq, dl, ha, hb) do { \
        int32_t im_, i1_, i2_; \
        float f2_, f3_, wm_, w0_, w1_, w2_; \
        const float *pm_, *p0_, *p1_, *p2_; \
        (fq) += (dl); \
        (i0) += 1; \
        if ((fq) >= 1.0f) { (fq) -= 1.0f; (i0) -= 1; } \
        else if ((fq) < 0.0f) { (fq) += 1.0f; (i0) += 1; } \
        if ((i0) >= SE_RING) (i0) -= SE_RING; \
        if ((i0) < 0) (i0) += SE_RING; \
        if ((i0) >= 2 && (i0) < SE_RING - 1) { \
            p0_ = ring + 2 * (i0); \
            pm_ = p0_ + 2; \
            p1_ = p0_ - 2; \
            p2_ = p0_ - 4; \
        } else { \
            im_ = (i0) + 1; if (im_ >= SE_RING) im_ -= SE_RING; \
            i1_ = (i0) - 1; if (i1_ < 0) i1_ += SE_RING; \
            i2_ = i1_ - 1; if (i2_ < 0) i2_ += SE_RING; \
            pm_ = ring + 2 * im_; \
            p0_ = ring + 2 * (i0); \
            p1_ = ring + 2 * i1_; \
            p2_ = ring + 2 * i2_; \
        } \
        f2_ = (fq) * (fq); \
        f3_ = f2_ * (fq); \
        wm_ = 0.5f * ((f2_ + f2_) - (fq) - f3_);         /* -f/2 + f^2 - f^3/2 */ \
        w0_ = 1.0f - 2.5f * f2_ + 1.5f * f3_; \
        w2_ = 0.5f * (f3_ - f2_); \
        w1_ = 1.0f - wm_ - w0_ - w2_;                    /* f/2 + 2 f^2 - 3/2 f^3 */ \
        (ha) = wm_ * pm_[0] + w0_ * p0_[0] + w1_ * p1_[0] + w2_ * p2_[0]; \
        (hb) = wm_ * pm_[1] + w0_ * p0_[1] + w1_ * p1_[1] + w2_ * p2_[1]; \
    } while (0)

/* The per-sample loop, one call per control block (at most 32 samples). Written for selcc
   as tapedelay.c's td_run: the state the loop touches is copied into locals and written
   back at the end, no calls, and per-block decisions are taken once (with if statements:
   selcc re-evaluates a local initialised with a comparison at every use, T3.11 11.3):
   - a head is read only while it sounds or fades;
   - the glide of a head runs only until its delay reaches the target;
   - the mixing coefficients are ramped only while one moves; the input and feedback
     matrices are skipped while they are the identity (MONO, settled);
   - the band filter runs only when BW is below 127 or fading; at BW 119 and below it is
     the filter alone, without the mix;
   - the top-end loss runs only for AGE > 0; hiss and crackle as in tapedelay.c;
   - the feedback path is skipped at INTENSITY 0; the gain ramps and the wow/flutter
     offsets run only while they move.
   Not static on purpose (T5.3): selcc inlines a static void function that is called once and
   calls nothing, and inlined here its 209-word frame sits under se_control's call chain, which
   took the delay slot's deepest stack point from 1,660 to 2,092 bytes below the DSP block
   handler (the Audio Task's whole stack is 4,000 bytes; FTD1 uses 1,516). The arithmetic is
   the same either way. */
void se_run(SpaceEcho *s, const float *inL, const float *inR, float *outL, float *outR, int n);
void se_run(SpaceEcho *s, const float *inL, const float *inR, float *outL, float *outR, int n)
{
    float *ring = s->ring;
    float c[SE_NMIX], ci[SE_NMIX];
    float tg0, tg1, tg2, t0, t1, t2, v0, v1, v2, m0, m1, m2, ad0, ad1, ad2, m0i, m1i, m2i;
    float f0, f1, f2, dl;
    int32_t q0, q1, q2;
    float fb, drive, inv_drive, byp, fb_i, drive_i, inv_drive_i, byp_i;
    float dca, dcb, za, zb, ac;
    int glide0 = 0, glide1 = 0, glide2 = 0, ramp = 0, on0 = 0, on1 = 0, on2 = 0;
    int inmix = 0, fbmix = 0, fbon = 0, band = 0, agelp = 0, aging = 0, crackle = 0, gramp = 0, wow = 0;
    int32_t w = s->w;
    /* crackle: band-pass state and coefficients */
    float c0a = s->ck[0].ic1, c0b = s->ck[0].ic2, c1a = s->ck[1].ic1, c1b = s->ck[1].ic2;
    float ca1 = s->ev_c0.a1, ca2 = s->ev_c0.a2, ca3 = s->ev_c0.a3, ck = s->ev_c0.k;
    float impL = 0.0f, impR = 0.0f;
    int32_t nev = 0, next_ev;
    /* hiss and the playback peak */
    float hg = s->hg, hz0 = s->hz[0], hz1 = s->hz[1], pk = s->blk_peak;
    const float hg_i = s->hg_i;
    uint32_t hr = s->hrng;
    /* band filter: resonant 2-pole high-pass, then 2-pole low-pass, per track */
    float h0a = s->hp[0].ic1, h0b = s->hp[0].ic2, h1a = s->hp[1].ic1, h1b = s->hp[1].ic2;
    float l0a = s->lp[0].ic1, l0b = s->lp[0].ic2, l1a = s->lp[1].ic1, l1b = s->lp[1].ic2;
    const float ha1 = s->hpc.a1, ha2 = s->hpc.a2, ha3 = s->hpc.a3, hk = s->hpc.k;
    const float la1 = s->lpc.a1, la2 = s->lpc.a2, la3 = s->lpc.a3;
    int i, k;

    for (k = 0; k < SE_NMIX; k++) {
        c[k] = s->mx[k];
        ci[k] = s->mx_i[k];
    }
    tg0 = s->t_delay;
    tg1 = s->t_delay * 2.0f;
    tg2 = s->t_delay * 3.0f;
    t0 = s->tau[0]; t1 = s->tau[1]; t2 = s->tau[2];
    v0 = s->vel[0]; v1 = s->vel[1]; v2 = s->vel[2];
    ad0 = s->ad[0]; ad1 = s->ad[1]; ad2 = s->ad[2];
    m0 = s->mod[0]; m1 = s->mod[1]; m2 = s->mod[2];
    m0i = s->mod_i[0]; m1i = s->mod_i[1]; m2i = s->mod_i[2];
    q0 = s->rq[0]; q1 = s->rq[1]; q2 = s->rq[2];
    f0 = s->rf[0]; f1 = s->rf[1]; f2 = s->rf[2];
    fb = s->fb; drive = s->drive; inv_drive = s->inv_drive; byp = s->byp;
    fb_i = s->fb_i; drive_i = s->drive_i; inv_drive_i = s->inv_drive_i; byp_i = s->byp_i;
    dca = s->dc[0]; dcb = s->dc[1];
    za = s->az[0]; zb = s->az[1];
    ac = s->ac;

    /* per-block decisions, set with if statements on purpose (see above) */
    if (t0 != tg0 || v0 != 0.0f) glide0 = 1;
    if (t1 != tg1 || v1 != 0.0f) glide1 = 1;
    if (t2 != tg2 || v2 != 0.0f) glide2 = 1;
    if (s->mxramp) ramp = 1;
    if (c[8] != 0.0f || c[11] != 0.0f || c[12] != 0.0f || c[13] != 0.0f || c[14] != 0.0f) on0 = 1;
    if (c[9] != 0.0f || c[15] != 0.0f || c[16] != 0.0f || c[17] != 0.0f || c[18] != 0.0f) on1 = 1;
    if (c[10] != 0.0f || c[19] != 0.0f || c[20] != 0.0f || c[21] != 0.0f || c[22] != 0.0f) on2 = 1;
    if (c[0] != 1.0f || c[1] != 0.0f || c[2] != 0.0f || c[3] != 1.0f) inmix = 1;
    if (c[4] != 1.0f || c[5] != 0.0f || c[6] != 0.0f || c[7] != 1.0f) fbmix = 1;
    if (ramp) {
        /* a coefficient that moves away from 0 (or from the identity) in this block */
        if (ci[8] != 0.0f || ci[11] != 0.0f || ci[12] != 0.0f || ci[13] != 0.0f || ci[14] != 0.0f) on0 = 1;
        if (ci[9] != 0.0f || ci[15] != 0.0f || ci[16] != 0.0f || ci[17] != 0.0f || ci[18] != 0.0f) on1 = 1;
        if (ci[10] != 0.0f || ci[19] != 0.0f || ci[20] != 0.0f || ci[21] != 0.0f || ci[22] != 0.0f) on2 = 1;
        if (ci[0] != 0.0f || ci[1] != 0.0f || ci[2] != 0.0f || ci[3] != 0.0f) inmix = 1;
        if (ci[4] != 0.0f || ci[5] != 0.0f || ci[6] != 0.0f || ci[7] != 0.0f) fbmix = 1;
    }
    if (fb != 0.0f || fb_i != 0.0f) fbon = 1;
    if (fb_i != 0.0f || drive_i != 0.0f || inv_drive_i != 0.0f) gramp = 1;
    if (m0 != 0.0f || m1 != 0.0f || m2 != 0.0f || m0i != 0.0f || m1i != 0.0f || m2i != 0.0f) wow = 1;
    if (byp != 0.0f || byp_i != 0.0f) band = 2;
    if (byp == 1.0f && byp_i == 0.0f) band = 1;
    if (ac < 1.0f) agelp = 1;
    if (s->ev_count0 <= n || c0a != 0.0f || c0b != 0.0f || c1a != 0.0f || c1b != 0.0f) crackle = 1;
    if (s->age > 0.0f || hg != 0.0f || hg_i != 0.0f) aging = 1;
    next_ev = s->ev_pos[0];

    for (i = 0; i < n; i++) {
        float ol, orr, sa, sb, xa, xb, fa, fbv, x, ya, yb, lpo, bpo, ha, hb;

        /* tape glide per head, smoothed gains, wow/flutter offsets */
        if (glide0) SE_TAPE_GLIDE(t0, v0, tg0, ad0, x);
        if (glide1) SE_TAPE_GLIDE(t1, v1, tg1, ad1, x);
        if (glide2) SE_TAPE_GLIDE(t2, v2, tg2, ad2, x);
        if (gramp) {
            fb += fb_i;
            drive += drive_i;
            inv_drive += inv_drive_i;
        }
        if (wow) {
            m0 += m0i;
            m1 += m1i;
            m2 += m2i;
        }
        if (ramp) {
            for (k = 0; k < SE_NMIX; k++) c[k] += ci[k];
        }

        /* the heads: each reads tracks A and B, into the output (its stereo place) and the
           feedback sums */
        ol = 0.0f; orr = 0.0f; sa = 0.0f; sb = 0.0f;
        if (on0) {
            dl = m0i;
            if (glide0) dl += v0;
            SE_READ(q0, f0, dl, ha, hb);
            ol += c[11] * ha + c[12] * hb;
            orr += c[13] * ha + c[14] * hb;
            sa += c[8] * ha;
            sb += c[8] * hb;
        }
        if (on1) {
            dl = m1i;
            if (glide1) dl += v1;
            SE_READ(q1, f1, dl, ha, hb);
            ol += c[15] * ha + c[16] * hb;
            orr += c[17] * ha + c[18] * hb;
            sa += c[9] * ha;
            sb += c[9] * hb;
        }
        if (on2) {
            dl = m2i;
            if (glide2) dl += v2;
            SE_READ(q2, f2, dl, ha, hb);
            ol += c[19] * ha + c[20] * hb;
            orr += c[21] * ha + c[22] * hb;
            sa += c[10] * ha;
            sb += c[10] * hb;
        }

        /* AGE: playback peak (for the envelope), then hiss, on the output and the feedback */
        if (aging) {
            float al = ol, ar = orr, nl, nr;
            FX_ABS(al);
            FX_ABS(ar);
            if (al > pk) pk = al;
            if (ar > pk) pk = ar;
            hr = hr * 1664525u + 1013904223u;                      /* fx_rand, written out */
            nl = (float)(int32_t)hr * 4.6566129e-10f;              /* 2^-31: [-1, 1] */
            nr = (float)(int32_t)(hr * 0x9e3779b1u) * 4.6566129e-10f;   /* a scrambled copy for R */
            hz0 += SE_HISS_LP * (nl - hz0);
            hz1 += SE_HISS_LP * (nr - hz1);
            hg += hg_i;
            nl = hg * hz0;
            nr = hg * hz1;
            ol += nl; orr += nr; sa += nl; sb += nr;
        }
        /* crackle, the same way */
        if (crackle) {
            if (i == next_ev) {
                impL = s->ev_imp[nev][0]; impR = s->ev_imp[nev][1];
                ca1 = s->ev_c[nev].a1; ca2 = s->ev_c[nev].a2; ca3 = s->ev_c[nev].a3; ck = s->ev_c[nev].k;
                nev++;
                next_ev = s->ev_pos[nev];
            }
            FX_SVF(c0a, c0b, ca1, ca2, ca3, impL, lpo, bpo); x = ck * bpo; ol += x; sa += x;
            FX_SVF(c1a, c1b, ca1, ca2, ca3, impR, lpo, bpo); x = ck * bpo; orr += x; sb += x;
            impL = 0.0f;
            impR = 0.0f;
        }

        /* the wet ceiling (SE_LIM_*): untouched up to the knee, then bent onto the ceiling */
        x = ol;
        FX_ABS(x);
        if (x > SE_LIM_T) {
            x = x - SE_LIM_T;
            if (x > SE_LIM_U) x = SE_LIM_U;
            x = (SE_LIM_T + x) - SE_LIM_Q * x * x;
            if (ol < 0.0f) ol = -x;
            else ol = x;
        }
        x = orr;
        FX_ABS(x);
        if (x > SE_LIM_T) {
            x = x - SE_LIM_T;
            if (x > SE_LIM_U) x = SE_LIM_U;
            x = (SE_LIM_T + x) - SE_LIM_Q * x * x;
            if (orr < 0.0f) orr = -x;
            else orr = x;
        }

        FX_UNDENORM(ol);
        FX_UNDENORM(orr);
        outL[i] = ol;
        outR[i] = orr;

        /* record: the input matrix, plus INTENSITY x the DC-blocked feedback matrix */
        if (inmix) {
            xa = c[0] * inL[i] + c[1] * inR[i];
            xb = c[2] * inL[i] + c[3] * inR[i];
        } else {
            xa = inL[i];
            xb = inR[i];
        }
        if (fbon) {
            if (fbmix) {
                fa = c[4] * sa + c[5] * sb;
                fbv = c[6] * sa + c[7] * sb;
            } else {
                fa = sa;
                fbv = sb;
            }
            dca += SE_DCK * (fa - dca);
            dcb += SE_DCK * (fbv - dcb);
            xa = xa + fb * (fa - dca);
            xb = xb + fb * (fbv - dcb);
        }

        /* band filter: resonant high-pass, then resonant low-pass; faded in below BW 127 */
        if (band) {
            FX_SVF(h0a, h0b, ha1, ha2, ha3, xa, lpo, bpo); ya = xa - hk * bpo - lpo;
            FX_SVF(h1a, h1b, ha1, ha2, ha3, xb, lpo, bpo); yb = xb - hk * bpo - lpo;
            FX_SVF(l0a, l0b, la1, la2, la3, ya, ya, bpo);
            FX_SVF(l1a, l1b, la1, la2, la3, yb, yb, bpo);
            if (band == 2) {
                byp += byp_i;
                xa = xa + byp * (ya - xa);
                xb = xb + byp * (yb - xb);
            } else {
                xa = ya;
                xb = yb;
            }
        }

        /* AGE: top-end loss of the tape */
        if (agelp) {
            za += ac * (xa - za);
            zb += ac * (xb - zb);
            xa = za;
            xb = zb;
        }

        /* saturation (fx_sat), unity gain for small signals apart from the resonance trim
           carried by drive */
        xa = xa * drive;
        FX_CLAMP(xa, -1.5f, 1.5f);
        xa = (xa - 0.14814815f * xa * xa * xa) * inv_drive;
        xb = xb * drive;
        FX_CLAMP(xb, -1.5f, 1.5f);
        xb = (xb - 0.14814815f * xb * xb * xb) * inv_drive;

        FX_UNDENORM(xa);
        FX_UNDENORM(xb);
        ring[2 * w] = xa;
        ring[2 * w + 1] = xb;
        if (++w >= SE_RING) w = 0;
    }

    s->tau[0] = t0; s->tau[1] = t1; s->tau[2] = t2;
    s->vel[0] = v0; s->vel[1] = v1; s->vel[2] = v2;
    s->rq[0] = q0; s->rq[1] = q1; s->rq[2] = q2;
    s->rf[0] = f0; s->rf[1] = f1; s->rf[2] = f2;
    s->mod[0] = m0; s->mod[1] = m1; s->mod[2] = m2;
    s->fb = fb; s->drive = drive; s->inv_drive = inv_drive; s->byp = byp;
    s->dc[0] = dca; s->dc[1] = dcb;
    s->az[0] = za; s->az[1] = zb;
    if (ramp) {
        for (k = 0; k < SE_NMIX; k++) s->mx[k] = c[k];
    }
    s->w = w; s->ck_count = s->ev_count;
    s->ck[0].ic1 = c0a; s->ck[0].ic2 = c0b; s->ck[1].ic1 = c1a; s->ck[1].ic2 = c1b;
    s->ck_impL = impL; s->ck_impR = impR;
    s->hg = hg; s->hz[0] = hz0; s->hz[1] = hz1; s->blk_peak = pk; s->hrng = hr;
    s->hp[0].ic1 = h0a; s->hp[0].ic2 = h0b; s->hp[1].ic1 = h1a; s->hp[1].ic2 = h1b;
    s->lp[0].ic1 = l0a; s->lp[0].ic2 = l0b; s->lp[1].ic1 = l1a; s->lp[1].ic2 = l1b;
}

void spaceecho_process(SpaceEcho *s, const float *inL, const float *inR,
                       float *outL, float *outR, int n)
{
    int i = 0;
    while (i < n) {
        int m;
        if (s->ctl_left <= 0) {
            se_control(s);
            s->ctl_left = FX_BLOCK;
        }
        m = n - i;
        if (m > s->ctl_left) m = s->ctl_left;
        se_crackle_plan(s, m);
        se_run(s, inL + i, inR + i, outL + i, outR + i, m);
        i += m;
        s->ctl_left -= m;
    }
}
