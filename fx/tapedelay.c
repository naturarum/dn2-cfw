/* SPDX-License-Identifier: GPL-2.0-or-later */
/*
 * tapedelay.c - tape delay for the Digitone II delay slot (T3.11, G2).
 * See tapedelay.h for the signal flow and notes/T3.11-fx-workbench.md for the
 * knob layout and the reasoning behind the constants marked [guess].
 */
#include "tapedelay.h"

#define CTL_RATE   (FX_SR / (float)FX_BLOCK)  /* 1,500 control updates per second */

/* modulation: rates are [guess], tuned by ear later */
#define WOW_HZ     0.55f      /* capstan-like slow drift */
#define FLUT_HZ    6.5f       /* flutter, main component */
#define FLUT2_HZ   11.3f      /* flutter, second component */
#define WOW_MAX    0.020f     /* max pitch deviation of the sine part, 2 % */
#define FLUT_MAX   0.008f     /* 0.8 % */
#define WOWN_HZ    1.5f       /* smoothed-noise bandwidth for wow */
#define FLUTN_HZ   20.0f      /* and for flutter */
#define WOWN_K     31.0f      /* noise std 0.022 -> 0.7 (measured offline) */
#define FLUTN_K    8.4f       /* noise std 0.084 -> 0.7 */

#define GLIDE_TAU  0.12f      /* delay-time glide, seconds */
#define GLIDE_SLEW 0.5f       /* max delay change per sample: playback speed stays in 0.5..1.5 */
#define PAR_TAU    0.02f      /* parameter smoothing, seconds */
#define MIN_DELAY  4.0f       /* samples, for the 4-point read */

/* loop filters (2026-10-06, owner: "the filters should have resonance"), [guess]:
   RES r = 0..1 sets the low-pass Q to 0.707 * 2^(RES_LP_OCT r) (0.71..5) and the
   high-pass Q to 0.707 * 2^(RES_HP_OCT r) (0.71..1.41); the loop's passband is
   trimmed by (Q_lp/0.707)^-RES_TRIM, so the resonant peak rises by +17 dB
   against the passband at full RES but only +7.2 dB in absolute gain: the
   loop sings at the resonance from about 44 % FDBK at full RES, 79 % at RES 64,
   and the saturator holds it (12.4). */
#define RES_LP_OCT  2.8219281f
#define RES_HP_OCT  1.0f
#define RES_TRIM    0.4f
#define FILT_TAU_B  0.03278f  /* corner glide per control block: tau 20 ms */

/* AGE (2026-10-06), a = AGE/127; all [guess], set for "subtle" (notes/T3.11 12.4):
   the loop low-pass drops by AGE_LPF_OCT * a octaves; hiss at HISS_MAX * a^2 of
   the playback envelope; crackle CK_RATE_MAX * a^2 events per second, each a
   soft band-passed tick of at most CK_AMP * a of the playback envelope. */
#define AGE_LPF_OCT 0.75f
#define HISS_MAX    0.0062f   /* hiss -50 dB under the echoes at AGE 127, -62 dB at 64 (measured, make test) */
#define CK_RATE_MAX 6.0f
#define CK_AMP      0.10f
#define HISS_LP     0.5441f   /* one-pole low-pass on the hiss, 1 - exp(-2 pi 6000/48000) */
#define ENV_ATT     0.0645f   /* playback envelope at the control rate: attack 10 ms */
#define ENV_REL     0.000444f /* release 1.5 s */

float tapedelay_time_samples(float knob, float bpm)
{
    /* (v+1)/128 of a whole note: (v+1) * 4 beats/128 * 60/bpm s * 48000.
       BPM clamp 30..300 as in the stock code (T3.4). [inferred] */
    float d;
    bpm = fx_clampf(bpm, 30.0f, 300.0f);
    d = (fx_clampf(knob, 0.0f, 127.0f) + 1.0f) * (90000.0f / bpm);
    return fx_clampf(d, MIN_DELAY, (float)(TD_RING - 512));
}

float tapedelay_hpf_hz(float knob) { return 20.0f * fx_exp2(fx_knob01(knob) * 7.6438562f); }   /* x200 */
float tapedelay_lpf_hz(float knob) { return 100.0f * fx_exp2(fx_knob01(knob) * 7.4918531f); }  /* x180 */
float tapedelay_lpf_q(float knob)  { return 0.70710678f * fx_exp2(fx_knob01(knob) * RES_LP_OCT); }

/* Hz -> knob, the inverse of the two maps above (for the TONE macro) */
static float td_hpf_knob(float hz) { return fx_clampf(127.0f * fx_log2(hz * (1.0f / 20.0f)) * (1.0f / 7.6438562f), 0.0f, 127.0f); }
static float td_lpf_knob(float hz) { return fx_clampf(127.0f * fx_log2(hz * (1.0f / 100.0f)) * (1.0f / 7.4918531f), 0.0f, 127.0f); }

static void td_targets(TapeDelay *s)
{
    float x;
    s->t_delay = tapedelay_time_samples(s->knob[TD_TIME], s->bpm);
    s->t_fb = 1.1f * fx_knob01(s->knob[TD_FDBK]);
    s->t_lhp = fx_log2(tapedelay_hpf_hz(s->knob[TD_HPF]));
    s->t_llp = fx_log2(tapedelay_lpf_hz(s->knob[TD_LPF]));
    s->t_res = fx_knob01(s->knob[TD_RES]);
    x = s->knob[TD_WID] - 64.0f;                                    /* -1 mono .. 0 stereo .. 1 ping-pong */
    s->t_wid = x < 0.0f ? x * (1.0f / 64.0f) : x * (1.0f / 63.0f);
    x = fx_knob01(s->knob[TD_WOW]);
    s->t_wow = WOW_MAX * x * x * FX_SR / (2.0f * FX_PI * WOW_HZ);
    x = fx_knob01(s->knob[TD_FLUT]);
    s->t_flut = FLUT_MAX * x * x * FX_SR / (2.0f * FX_PI * FLUT_HZ);
    s->t_age = fx_knob01(s->knob[TD_AGE]);
    s->t_drive = fx_exp2(fx_knob01(s->knob[TD_DRIV]) * 2.5849625f);  /* 1..6 */
    s->dirty = 0;
}

void tapedelay_set(TapeDelay *s, int param, float value)
{
    float v;
    if (param < 0 || param >= TD_NPARAMS) return;
    v = fx_clampf(value, 0.0f, 127.0f);
    s->knob[param] = v;
    if (param == TD_TONE) {
        /* the old bipolar TONE, now through the resonant filters: 64 = tape band
           50 Hz..6.5 kHz; down to 0 the LPF falls to 300 Hz (darker repeats);
           up to 127 the HPF rises to 2.5 kHz and the LPF opens to 13 kHz */
        float t = fx_knobbi(v), hpf = 50.0f, lpf = 6500.0f;
        if (t < 0.0f) lpf = 6500.0f * fx_exp2(t * 4.4374053f);
        if (t > 0.0f) { hpf = 50.0f * fx_exp2(t * 5.6438562f); lpf = 6500.0f * fx_exp2(t); }
        s->knob[TD_HPF] = td_hpf_knob(hpf);
        s->knob[TD_LPF] = td_lpf_knob(lpf);
    } else if (param == TD_WF) {
        /* wow first; flutter joins past 40 [guess] */
        s->knob[TD_WOW] = 0.9f * v;
        s->knob[TD_FLUT] = v > 40.0f ? (v - 40.0f) : 0.0f;
    }
    s->dirty = 1;
}

void tapedelay_set_bpm(TapeDelay *s, float bpm)
{
    s->bpm = fx_clampf(bpm, 30.0f, 300.0f);
    s->dirty = 1;
}

/* loop filters from the smoothed corners, resonance and age (control rate) */
static void td_filter_coefs(TapeDelay *s)
{
    float qlp = 0.70710678f * fx_exp2(s->res * RES_LP_OCT);
    float qhp = 0.70710678f * fx_exp2(s->res * RES_HP_OCT);
    fx_svf_coef(&s->hpc, fx_svf_g(fx_exp2(s->lhp)), 1.0f / qhp);
    fx_svf_coef(&s->lpc, fx_svf_g(fx_exp2(s->llp - AGE_LPF_OCT * s->age)), 1.0f / qlp);
    s->trim = fx_exp2(-RES_TRIM * RES_LP_OCT * s->res);
}

/* the width matrix for a width x (-1 mono .. 0 stereo .. 1 ping-pong):
   c[0..3] = input LL, LR, RL, RR; c[4], c[5] = feedback same side, cross */
static void td_width(float x, float *c)
{
    if (x <= 0.0f) {
        float a = 1.0f + x;                  /* 0 mono .. 1 stereo */
        c[0] = 0.5f + 0.5f * a; c[1] = 0.5f - 0.5f * a;
        c[2] = c[1];            c[3] = c[0];
        c[4] = 1.0f;            c[5] = 0.0f;
    } else {
        float p = x > 1.0f ? 1.0f : x;       /* ping-pong: the input (to mono) enters on the left, */
        c[0] = 1.0f - 0.5f * p; c[1] = 0.5f * p;   /* the repeats cross sides on every pass */
        c[2] = 0.0f;            c[3] = 1.0f - p;
        c[4] = 1.0f - p;        c[5] = p;
    }
}

static float td_mod_value(TapeDelay *s)
{
    float w = 0.8f * fx_sin2pi(s->ph_w) + 0.3f * WOWN_K * s->nw2;
    float f = 0.7f * fx_sin2pi(s->ph_f) + 0.3f * fx_sin2pi(s->ph_f2) + 0.4f * FLUTN_K * s->nf2;
    return s->wowA * w + s->flutA * f;
}

/* Jump every smoother to its target (use after init + set, before audio). */
void tapedelay_snap(TapeDelay *s)
{
    int k;
    if (s->dirty) td_targets(s);
    s->d = s->t_delay;
    s->fb = s->t_fb;   s->fb_i = 0.0f;
    s->wowA = s->t_wow;
    s->flutA = s->t_flut;
    s->lhp = s->t_lhp;
    s->llp = s->t_llp;
    s->res = s->t_res;
    s->age = s->t_age;
    td_filter_coefs(s);
    s->drv = s->t_drive;
    s->drive = s->t_drive * s->trim; s->drive_i = 0.0f;
    s->inv_drive = 1.0f / s->t_drive; s->inv_drive_i = 0.0f;
    s->wid = s->t_wid;
    td_width(s->wid, s->wc);
    for (k = 0; k < 6; k++) s->wc_i[k] = 0.0f;
    s->mod = td_mod_value(s);
    s->mod_i = 0.0f;
}

void tapedelay_init(TapeDelay *s, uint32_t seed)
{
    int32_t i;
    for (i = 0; i < 2 * TD_RING; i++) s->ring[i] = 0.0f;
    s->w = 0;
    s->knob[TD_TIME] = 31.0f;   /* a quarter note */
    s->knob[TD_FDBK] = 50.0f;
    s->knob[TD_HPF] = 22.0f;    /* 50 Hz */
    s->knob[TD_LPF] = 102.0f;   /* 6.5 kHz */
    s->knob[TD_RES] = 0.0f;
    s->knob[TD_WID] = 64.0f;    /* stereo as it comes */
    s->knob[TD_WOW] = 20.0f;
    s->knob[TD_FLUT] = 20.0f;
    s->knob[TD_AGE] = 0.0f;
    s->knob[TD_DRIV] = 32.0f;
    s->knob[TD_TONE] = 64.0f;   /* macros: only applied when set */
    s->knob[TD_WF] = 22.0f;
    s->bpm = 120.0f;
    s->dirty = 1;
    s->a_d = fx_onepole_a(GLIDE_TAU, FX_SR);
    s->ph_w = 0.0f; s->ph_f = 0.0f; s->ph_f2 = 0.25f;
    s->nw1 = s->nw2 = s->nf1 = s->nf2 = 0.0f;
    s->rng = seed ? seed : 0x1234567u;
    s->hrng = 0x9e3779b9u ^ s->rng;
    s->crng = 0x7f4a7c15u ^ s->rng;
    for (i = 0; i < 2; i++) {
        s->hp[i].ic1 = s->hp[i].ic2 = 0.0f;
        s->lp[i].ic1 = s->lp[i].ic2 = 0.0f;
        s->ck[i].ic1 = s->ck[i].ic2 = 0.0f;
        s->hz[i] = 0.0f;
    }
    s->env = 0.0f;
    s->blk_peak = 0.0f;
    s->hg = s->hg_i = 0.0f;
    s->ck_count = 4800;
    s->ck_impL = s->ck_impR = 0.0f;
    fx_svf_coef(&s->ckc, fx_svf_g(2000.0f), 1.4f);
    s->ctl_left = 0;
    tapedelay_snap(s);
}

/* one step of a control-rate one-pole, snapped to the target when close
   (so a value gliding to 0 lands on 0, never on a denormal); a statement
   macro, so the SHARC build makes no call */
#define TD_GLIDE(out, x, target, b) do { \
        float e_, d_;                    /* not "e_ = ..., d_ = e_ - ...": selcc drops e_ (12.7) */ \
        e_ = (x) + (b) * ((target) - (x)); \
        d_ = e_ - (target); \
        FX_ABS(d_); \
        if (d_ < 1e-6f) e_ = (target); \
        (out) = e_; \
    } while (0)

/* One control update: runs at the start of every 32-sample control block. */
static void td_control(TapeDelay *s)
{
    const float a = 0.96722f;          /* exp(-1/(PAR_TAU*1500)) */
    const float b = 1.0f - a;
    const float inv = 1.0f / (float)FX_BLOCK;
    const float cw = 0.0062637f;       /* 1-exp(-2pi*1.5/1500) */
    const float cf = 0.0803287f;       /* 1-exp(-2pi*20/1500) */
    float e, pk, c[6];
    int k, recoef = 0;

    if (s->dirty) td_targets(s);

    /* loop filters and age: smoothed at the control rate, coefficients recomputed on change */
    TD_GLIDE(e, s->lhp, s->t_lhp, FILT_TAU_B); if (e != s->lhp) recoef = 1; s->lhp = e;
    TD_GLIDE(e, s->llp, s->t_llp, FILT_TAU_B); if (e != s->llp) recoef = 1; s->llp = e;
    TD_GLIDE(e, s->res, s->t_res, b);          if (e != s->res) recoef = 1; s->res = e;
    TD_GLIDE(e, s->age, s->t_age, b);          if (e != s->age) recoef = 1; s->age = e;
    if (recoef) td_filter_coefs(s);

    /* gains: ramp linearly to a one-pole-smoothed end value; the drive ramp
       carries the resonance trim, the inverse drive does not */
    TD_GLIDE(e, s->fb, s->t_fb, b);             s->fb_i = (e - s->fb) * inv;
    TD_GLIDE(s->drv, s->drv, s->t_drive, b);
    e = s->drv * s->trim;                       s->drive_i = (e - s->drive) * inv;
    e = 1.0f / s->drv;                          s->inv_drive_i = (e - s->inv_drive) * inv;

    /* width: matrix of the smoothed width, ramped per sample */
    TD_GLIDE(s->wid, s->wid, s->t_wid, b);
    td_width(s->wid, c);
    for (k = 0; k < 6; k++) s->wc_i[k] = (c[k] - s->wc[k]) * inv;

    /* modulation: advance the sources to the end of this block */
    /* depth changes glide slowly (tau 0.3 s): a fast depth change is itself a pitch bend */
    TD_GLIDE(s->wowA, s->wowA, s->t_wow, 0.0022197f);
    TD_GLIDE(s->flutA, s->flutA, s->t_flut, 0.0022197f);
    s->ph_w += WOW_HZ * (float)FX_BLOCK / FX_SR;     if (s->ph_w >= 1.0f) s->ph_w -= 1.0f;
    s->ph_f += FLUT_HZ * (float)FX_BLOCK / FX_SR;    if (s->ph_f >= 1.0f) s->ph_f -= 1.0f;
    s->ph_f2 += FLUT2_HZ * (float)FX_BLOCK / FX_SR;  if (s->ph_f2 >= 1.0f) s->ph_f2 -= 1.0f;
    s->nw1 += cw * (fx_rands(&s->rng) - s->nw1);
    s->nw2 += cw * (s->nw1 - s->nw2);
    s->nf1 += cf * (fx_rands(&s->rng) - s->nf1);
    s->nf2 += cf * (s->nf1 - s->nf2);
    e = td_mod_value(s);
    s->mod_i = (e - s->mod) * inv;

    /* AGE: the playback envelope (peak of the last block, attack 10 ms,
       release 1.5 s) sets the hiss and crackle levels, so they follow the
       echoes and die with them; at AGE 0 the envelope is held at 0 */
    pk = s->blk_peak;
    s->blk_peak = 0.0f;
    if (s->age > 0.0f) {
        if (pk > s->env) s->env += ENV_ATT * (pk - s->env);
        else s->env += ENV_REL * (pk - s->env);
        FX_UNDENORM(s->env);
    } else {
        s->env = 0.0f;
    }
    e = HISS_MAX * s->age * s->age * s->env;
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
    }
}

/* crackle event: one soft tick (an impulse through a broad band-pass at
   1.2-3.5 kHz), random level (mostly tiny) and pan, scaled by AGE and by the
   playback envelope, then an exponential gap to the next one */
static void td_crackle_event(TapeDelay *s)
{
    float u, a, p, fc, rate = CK_RATE_MAX * s->age * s->age;
    if (rate <= 0.0f) {
        s->ck_count = 4800;
        return;
    }
    u = fx_randu(&s->crng);
    a = CK_AMP * s->age * s->env * u * u * u;
    if (fx_rand(&s->crng) & 0x40000000u) a = -a;
    fc = 1200.0f + 2300.0f * fx_randu(&s->crng);
    fx_svf_coef(&s->ckc, fx_svf_g(fc), 1.4f);
    p = fx_randu(&s->crng);
    s->ck_impL = a * (1.0f - 0.6f * p);
    s->ck_impR = a * (0.4f + 0.6f * p);
    /* exponential gap: -ln(u)/rate seconds */
    u = fx_randu(&s->crng);
    if (u < 1e-7f) u = 1e-7f;
    p = -fx_log2(u) * FX_LN2 * FX_SR / rate;
    s->ck_count = (int32_t)fx_clampf(p, 1.0f, 1e8f);
}

/* The crackle events of the next n samples, before td_run runs them: the
   counter reaches 0 at sample pos + ck_count - 1. Leaves the counter value
   after the n samples in s->ev_count. */
static void td_crackle_plan(TapeDelay *s, int n)
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
        td_crackle_event(s);
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

/* The per-sample loop, one call per control block (at most 32 samples).
   Written for selcc (section 11): the state the loop touches is copied into
   locals first and written back at the end, helper calls are replaced by the
   macros of fxmath.h, and per-block decisions are taken once:
   - the crackle events of the block are worked out before the loop;
   - the crackle filters run only while they ring or an event falls in this
     block (otherwise their state is exactly 0 and they add exactly 0);
   - the hiss and the envelope run only while AGE is on or the hiss fades;
   - the width matrix is skipped while it is the identity (WID 64);
   - the delay-time glide is skipped once the time has reached its target. */
static void td_run(TapeDelay *s, const float *inL, const float *inR, float *outL, float *outR, int n)
{
    float *ring = s->ring;
    const float ad = 1.0f - s->a_d;
    const float t_delay = s->t_delay;
    float d = s->d, fb = s->fb, drive = s->drive, inv_drive = s->inv_drive, mod = s->mod;
    const float fb_i = s->fb_i, drive_i = s->drive_i, inv_drive_i = s->inv_drive_i;
    const float mod_i = s->mod_i;
    int glide = 0, crackle = 0, aging = 0, plain = 0;   /* set below; see the note on selcc */
    int32_t w = s->w;
    /* crackle: band-pass state and coefficients */
    float c0a = s->ck[0].ic1, c0b = s->ck[0].ic2, c1a = s->ck[1].ic1, c1b = s->ck[1].ic2;
    float ca1 = s->ev_c0.a1, ca2 = s->ev_c0.a2, ca3 = s->ev_c0.a3, ck = s->ev_c0.k;
    float impL = 0.0f, impR = 0.0f;      /* the impulse is consumed in the sample of its event */
    int32_t nev = 0, next_ev;
    /* hiss and the playback peak */
    float hg = s->hg, hz0 = s->hz[0], hz1 = s->hz[1], pk = s->blk_peak;
    const float hg_i = s->hg_i;
    uint32_t hr = s->hrng;
    /* width matrix */
    float w0 = s->wc[0], w1 = s->wc[1], w2 = s->wc[2], w3 = s->wc[3], w4 = s->wc[4], w5 = s->wc[5];
    const float w0i = s->wc_i[0], w1i = s->wc_i[1], w2i = s->wc_i[2], w3i = s->wc_i[3];
    const float w4i = s->wc_i[4], w5i = s->wc_i[5];
    /* loop filters: resonant 2-pole high-pass, then 2-pole low-pass, per channel */
    float h0a = s->hp[0].ic1, h0b = s->hp[0].ic2, h1a = s->hp[1].ic1, h1b = s->hp[1].ic2;
    float l0a = s->lp[0].ic1, l0b = s->lp[0].ic2, l1a = s->lp[1].ic1, l1b = s->lp[1].ic2;
    const float ha1 = s->hpc.a1, ha2 = s->hpc.a2, ha3 = s->hpc.a3, hk = s->hpc.k;
    const float la1 = s->lpc.a1, la2 = s->lpc.a2, la3 = s->lpc.a3;
    int i;

    /* Per-block decisions. They are set with if statements on purpose: selcc
       re-evaluates a local initialised with a comparison at every use, with
       the operands' current values (notes/T3.11 section 11), which inside
       the loop would be wrong. */
    if (d != t_delay) glide = 1;
    if (s->ev_count0 <= n || c0a != 0.0f || c0b != 0.0f || c1a != 0.0f || c1b != 0.0f) crackle = 1;
    if (s->age > 0.0f || hg != 0.0f || hg_i != 0.0f) aging = 1;
    if (w0 == 1.0f && w1 == 0.0f && w2 == 0.0f && w3 == 1.0f && w4 == 1.0f && w5 == 0.0f
        && w0i == 0.0f && w1i == 0.0f && w2i == 0.0f && w3i == 0.0f && w4i == 0.0f && w5i == 0.0f) plain = 1;
    next_ev = s->ev_pos[0];

    for (i = 0; i < n; i++) {
        float fr, f, hl, hrr, xl, xr, lpo, bpo, x;
        int32_t di, ni;

        /* smoothed values */
        if (glide) {
            x = ad * (t_delay - d);
            FX_CLAMP(x, -GLIDE_SLEW, GLIDE_SLEW);
            d += x;
        }
        fb += fb_i;
        drive += drive_i;
        inv_drive += inv_drive_i;
        mod += mod_i;

        /* the head: 4-point Hermite read of both channels */
        di = (int32_t)d;
        fr = (d - (float)di) + mod;
        if ((float)di + fr < MIN_DELAY) { di = (int32_t)MIN_DELAY; fr = 0.0f; }
        ni = (int32_t)fr;
        if ((float)ni > fr) ni -= 1;
        f = fr - (float)ni;
        {
            int32_t i0 = w - (di + ni), im, i1, i2;
            const float *pm, *p0, *p1, *p2;
            float xm, x0, x1, x2, k1, k2, k3;
            if (i0 < 0) i0 += TD_RING;
            if (i0 >= 2 && i0 < TD_RING - 1) {
                /* no wrap among the 4 frames (all but 3 positions of the ring) */
                p0 = ring + 2 * i0;
                pm = p0 + 2;
                p1 = p0 - 2;
                p2 = p0 - 4;
            } else {
                im = i0 + 1; if (im >= TD_RING) im -= TD_RING;
                i1 = i0 - 1; if (i1 < 0) i1 += TD_RING;
                i2 = i1 - 1; if (i2 < 0) i2 += TD_RING;
                pm = ring + 2 * im;
                p0 = ring + 2 * i0;
                p1 = ring + 2 * i1;
                p2 = ring + 2 * i2;
            }
            xm = pm[0]; x0 = p0[0]; x1 = p1[0]; x2 = p2[0];
            k1 = 0.5f * (x1 - xm);
            k2 = xm - 2.5f * x0 + 2.0f * x1 - 0.5f * x2;
            k3 = 0.5f * (x2 - xm) + 1.5f * (x0 - x1);
            hl = ((k3 * f + k2) * f + k1) * f + x0;
            xm = pm[1]; x0 = p0[1]; x1 = p1[1]; x2 = p2[1];
            k1 = 0.5f * (x1 - xm);
            k2 = xm - 2.5f * x0 + 2.0f * x1 - 0.5f * x2;
            k3 = 0.5f * (x2 - xm) + 1.5f * (x0 - x1);
            hrr = ((k3 * f + k2) * f + k1) * f + x0;
        }

        /* AGE: playback peak (for the envelope), then hiss */
        if (aging) {
            float al = hl, ar = hrr, nl, nr;
            FX_ABS(al);
            FX_ABS(ar);
            if (al > pk) pk = al;
            if (ar > pk) pk = ar;
            hr = hr * 1664525u + 1013904223u;                      /* fx_rand, written out */
            nl = (float)(int32_t)hr * 4.6566129e-10f;              /* 2^-31: [-1, 1] */
            nr = (float)(int32_t)(hr * 0x9e3779b1u) * 4.6566129e-10f;   /* a scrambled copy for R */
            hz0 += HISS_LP * (nl - hz0);
            hz1 += HISS_LP * (nr - hz1);
            hg += hg_i;
            hl += hg * hz0;
            hrr += hg * hz1;
        }

        /* crackle on the playback signal */
        if (crackle) {
            if (i == next_ev) {
                impL = s->ev_imp[nev][0]; impR = s->ev_imp[nev][1];
                ca1 = s->ev_c[nev].a1; ca2 = s->ev_c[nev].a2; ca3 = s->ev_c[nev].a3; ck = s->ev_c[nev].k;
                nev++;
                next_ev = s->ev_pos[nev];
            }
            FX_SVF(c0a, c0b, ca1, ca2, ca3, impL, lpo, bpo); hl += ck * bpo;
            FX_SVF(c1a, c1b, ca1, ca2, ca3, impR, lpo, bpo); hrr += ck * bpo;
            impL = 0.0f;
            impR = 0.0f;
        }

        FX_UNDENORM(hl);
        FX_UNDENORM(hrr);
        outL[i] = hl;
        outR[i] = hrr;

        /* record: the width matrix of the input and the feedback */
        if (plain) {
            xl = inL[i] + fb * hl;
            xr = inR[i] + fb * hrr;
        } else {
            w0 += w0i; w1 += w1i; w2 += w2i; w3 += w3i; w4 += w4i; w5 += w5i;
            xl = w0 * inL[i] + w1 * inR[i] + fb * (w4 * hl + w5 * hrr);
            xr = w2 * inL[i] + w3 * inR[i] + fb * (w4 * hrr + w5 * hl);
        }

        /* loop filters: resonant high-pass, then resonant low-pass */
        FX_SVF(h0a, h0b, ha1, ha2, ha3, xl, lpo, bpo); xl = xl - hk * bpo - lpo;
        FX_SVF(h1a, h1b, ha1, ha2, ha3, xr, lpo, bpo); xr = xr - hk * bpo - lpo;
        FX_SVF(l0a, l0b, la1, la2, la3, xl, xl, bpo);
        FX_SVF(l1a, l1b, la1, la2, la3, xr, xr, bpo);

        /* saturation (fx_sat), unity gain for small signals apart from the
           resonance trim carried by drive */
        xl = xl * drive;
        FX_CLAMP(xl, -1.5f, 1.5f);
        xl = (xl - 0.14814815f * xl * xl * xl) * inv_drive;
        xr = xr * drive;
        FX_CLAMP(xr, -1.5f, 1.5f);
        xr = (xr - 0.14814815f * xr * xr * xr) * inv_drive;

        FX_UNDENORM(xl);
        FX_UNDENORM(xr);
        ring[2 * w] = xl;
        ring[2 * w + 1] = xr;
        if (++w >= TD_RING) w = 0;
    }

    s->d = d; s->fb = fb; s->drive = drive; s->inv_drive = inv_drive; s->mod = mod;
    s->w = w; s->ck_count = s->ev_count;
    s->ck[0].ic1 = c0a; s->ck[0].ic2 = c0b; s->ck[1].ic1 = c1a; s->ck[1].ic2 = c1b;
    s->ck_impL = impL; s->ck_impR = impR;
    s->hg = hg; s->hz[0] = hz0; s->hz[1] = hz1; s->blk_peak = pk; s->hrng = hr;
    s->wc[0] = w0; s->wc[1] = w1; s->wc[2] = w2; s->wc[3] = w3; s->wc[4] = w4; s->wc[5] = w5;
    s->hp[0].ic1 = h0a; s->hp[0].ic2 = h0b; s->hp[1].ic1 = h1a; s->hp[1].ic2 = h1b;
    s->lp[0].ic1 = l0a; s->lp[0].ic2 = l0b; s->lp[1].ic1 = l1a; s->lp[1].ic2 = l1b;
}

void tapedelay_process(TapeDelay *s, const float *inL, const float *inR,
                       float *outL, float *outR, int n)
{
    int i = 0;
    while (i < n) {
        int m;
        if (s->ctl_left <= 0) {
            td_control(s);
            s->ctl_left = FX_BLOCK;
        }
        m = n - i;
        if (m > s->ctl_left) m = s->ctl_left;
        td_crackle_plan(s, m);
        td_run(s, inL + i, inR + i, outL + i, outR + i, m);
        i += m;
        s->ctl_left -= m;
    }
}
