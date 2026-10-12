/* SPDX-License-Identifier: GPL-2.0-or-later */
/*
 * mbcomp.c - 3-band master compressor (T3.11, G9). See mbcomp.h.
 *
 * Crossover: each Linkwitz-Riley 4th-order split is a Butterworth SVF (Q 0.707)
 * whose LP and HP outputs each go through a second Butterworth SVF of the same
 * frequency: LP4 = LP2*LP2, HP4 = HP2*HP2, and LP4 + HP4 = AP2 (the
 * second-order allpass at that frequency). With the low band passed through
 * AP2 at the upper crossover, low + mid + high = AP2(lo) * AP2(hi): flat
 * magnitude whatever the two frequencies are.
 *
 * Each band's compressor is the stock DN2 master compressor's design, as
 * measured in the T3.6 emulator (notes/T3.11 section 12.2,
 * tools/sharcvm/tests/comp_model.py): peak detector, hard knee, the stock
 * ratios, "smooth decoupled" smoothing of the gain reduction in dB (release
 * stage with an instant attack, then an attack one-pole), the stock time laws,
 * makeup 0..+24 dB, and the stock MIX crossfade law. Deliberate differences
 * (12.3): a 1:1 ratio step that switches a band off, continuous ATK/REL (the
 * stock tables have 128 steps), no wet clip at +-1, and SCF acting on the key
 * only.
 */
#include "mbcomp.h"

#define K_BW 1.4142136f      /* 1/Q for Butterworth */
#define PAR_A 0.96722f       /* control-rate one-pole, tau 20 ms at 1,500 Hz */
#define XO_A  0.98347f       /* crossover glide, tau 40 ms at 1,500 Hz */
#define MUP_DB 24.082400f    /* 20*log10(16): the stock makeup range */

/* RAT index -> ratio: 1:1 (off), then the stock table 0x2cfde0 (T3.4) */
static const float MB_RATIOS[9] = { 1.0f, 1.5f, 2.0f, 3.0f, 4.0f, 6.0f, 8.0f, 16.0f, 20.0f };

float mbcomp_xover_hz(float knob)
{
    return 30.0f * fx_exp2(fx_knob01(knob) * 9.0588936f);   /* 30 Hz .. 16 kHz */
}

float mbcomp_ratio(float knob)
{
    int i = (int)(fx_clampf(knob, 0.0f, 8.0f) + 0.5f);
    return MB_RATIOS[i];
}

/* stock ATK law (table 0x2cfe00): tau = 0.1 + 99.9 (v/127)^2 ms */
float mbcomp_atk_ms(float knob)
{
    float x = fx_knob01(knob);
    return 0.1f + 99.9f * x * x;
}

/* stock REL law (table 0x2d0000): tau = 10 + 1990 (v/127)^2.5 ms */
float mbcomp_rel_ms(float knob)
{
    float x = fx_knob01(knob);
    if (x <= 0.0f) return 10.0f;
    return 10.0f + 1990.0f * fx_exp2(2.5f * fx_log2(x));
}

/* stock MIX table 0x2d0200 as a law, in dB: 0 at 127, -6 at 64 (6/64 dB per
   step above), -0.5625 dB per step from 64 down to 8 (-37.5 dB), then about
   -7.75 dB per step; 0 is silence (handled by mb_mix_gain) */
float mbcomp_mix_db(float knob)
{
    float v = fx_clampf(knob, 0.0f, 127.0f);
    if (v >= 126.0f) return -0.1875f * (127.0f - v);
    if (v >= 64.0f) return -6.0f + 0.09375f * (v - 64.0f);
    if (v >= 8.0f) return -6.0f - 0.5625f * (64.0f - v);
    if (v >= 7.0f) return -37.5f - 8.25f * (8.0f - v);
    return -45.75f - 7.75f * (7.0f - v);
}

static float mb_mix_gain(float knob)
{
    float v = fx_clampf(knob, 0.0f, 127.0f);
    if (v < 1.0f) return v * fx_db2lin(mbcomp_mix_db(1.0f));   /* to exactly 0 at 0 */
    return fx_db2lin(mbcomp_mix_db(v));
}

/* key filter corner (fitted to the stock SCF corners measured in the emulator,
   tools/sharcvm/stockmap.py SCF_CORNERS): low-pass below 63.5, high-pass above
   64.5, neutral (high-pass 20 Hz: no DC in the detector) in between */
float mbcomp_scf_hz(float knob)
{
    float v = fx_clampf(knob, 0.0f, 127.0f);
    if (v < 63.5f) {
        if (v >= 24.0f) return 359.0f * fx_exp2((v - 28.0f) * (1.0f / 6.5f));
        return 234.4f * fx_exp2((v - 24.0f) * (1.0f / 3.0f));
    }
    if (v > 64.5f) return 77.0f * fx_exp2((v - 76.0f) * (1.0f / 6.4f));
    return 20.0f;
}

static void mb_targets(MBComp *s)
{
    int b;
    int lstn = (int)(s->knob[MB_LSTN] + 0.5f);
    float scf = s->knob[MB_SCF];

    for (b = 0; b < 3; b++) {
        MbBand *bd = &s->band[b];
        const float *k = &s->knob[MB_P(b, 0)];
        float r = mbcomp_ratio(k[MB_RAT]);
        bd->thr = -60.0f + 60.0f * fx_knob01(k[MB_THR]);
        bd->rm1 = 1.0f / r - 1.0f;
        bd->on = 0;
        if (r > 1.0f) bd->on = 1;
        bd->ca = fx_onepole_a(0.001f * mbcomp_atk_ms(k[MB_ATK]), FX_SR);
        bd->cr = fx_onepole_a(0.001f * mbcomp_rel_ms(k[MB_REL]), FX_SR);
        bd->t_mk = MUP_DB * fx_knob01(k[MB_MUP]);
        bd->t_w = (lstn <= 0 || lstn > 3 || lstn == b + 1) ? 1.0f : 0.0f;
    }
    s->t_lf_lo = fx_log2(mbcomp_xover_hz(s->knob[MB_XLO]));
    s->t_lf_hi = fx_log2(mbcomp_xover_hz(s->knob[MB_XHI]));
    s->sc_ext = 0;
    if (s->knob[MB_SCS] >= 0.5f) s->sc_ext = 1;
    s->sc_lp = 0;
    if (scf < 63.5f) s->sc_lp = 1;
    fx_svf_coef(&s->scc, fx_svf_g(mbcomp_scf_hz(scf)), K_BW);
    s->t_mw = mb_mix_gain(s->knob[MB_MIX]);
    s->t_md = mb_mix_gain(127.0f - s->knob[MB_MIX]);
    s->dirty = 0;
}

void mbcomp_set(MBComp *s, int param, float value)
{
    if (param < 0 || param >= MB_NPARAMS) return;
    s->knob[param] = fx_clampf(value, 0.0f, 127.0f);
    s->dirty = 1;
}

float mbcomp_gr_db(const MBComp *s, int band)
{
    if (band < 0 || band > 2) return 0.0f;
    return s->band[band].y2;
}

static void mb_xover_coefs(MBComp *s)
{
    float lo = s->lf_lo, hi = s->lf_hi;
    if (lo > hi) { float t = lo; lo = hi; hi = t; }
    fx_svf_coef(&s->c_lo, fx_svf_g(fx_exp2(lo)), K_BW);
    fx_svf_coef(&s->c_hi, fx_svf_g(fx_exp2(hi)), K_BW);
}

void mbcomp_snap(MBComp *s)
{
    int b;
    if (s->dirty) mb_targets(s);
    s->lf_lo = s->t_lf_lo;
    s->lf_hi = s->t_lf_hi;
    mb_xover_coefs(s);
    for (b = 0; b < 3; b++) {
        s->band[b].mk = s->band[b].t_mk; s->band[b].mk_i = 0.0f;
        s->band[b].w = s->band[b].t_w;   s->band[b].w_i = 0.0f;
    }
    s->mw = s->t_mw; s->mw_i = 0.0f;
    s->md = s->t_md; s->md_i = 0.0f;
}

static void svf_zero(FxSvf *f) { f->ic1 = 0.0f; f->ic2 = 0.0f; }

void mbcomp_init(MBComp *s)
{
    int c, b;
    for (c = 0; c < 2; c++) {
        svf_zero(&s->s1[c]); svf_zero(&s->s1lp[c]); svf_zero(&s->s1hp[c]);
        svf_zero(&s->s2[c]); svf_zero(&s->s2lp[c]); svf_zero(&s->s2hp[c]);
        svf_zero(&s->apl[c]); svf_zero(&s->apd1[c]); svf_zero(&s->apd2[c]);
        svf_zero(&s->sc[c]);
    }
    /* a gentle default on every band: -20 dB, 2:1, ATK 40 (10 ms), REL 44 (151 ms), no makeup */
    for (b = 0; b < 3; b++) {
        s->knob[MB_P(b, MB_THR)] = 85.0f;
        s->knob[MB_P(b, MB_ATK)] = 40.0f;
        s->knob[MB_P(b, MB_REL)] = 44.0f;
        s->knob[MB_P(b, MB_MUP)] = 0.0f;
        s->knob[MB_P(b, MB_RAT)] = 2.0f;
        s->band[b].y1 = 0.0f;
        s->band[b].y2 = 0.0f;
    }
    s->knob[MB_XLO] = 38.0f;
    s->knob[MB_XHI] = 90.0f;
    s->knob[MB_LSTN] = 0.0f;
    s->knob[MB_SCS] = 0.0f;
    s->knob[MB_SCF] = 64.0f;
    s->knob[MB_MIX] = 127.0f;
    s->dirty = 1;
    s->ctl_left = 0;
    mbcomp_snap(s);
}

/* control-rate glide to a target, snapped when within eps (a statement macro:
   no call on the SHARC) */
#define MB_GLIDE(e, x, target, b, eps) do { \
        float d_; \
        (e) = (x) + (b) * ((target) - (x)); \
        d_ = (e) - (target); \
        FX_ABS(d_); \
        if (d_ < (eps)) (e) = (target); \
    } while (0)

static void mb_control(MBComp *s)
{
    const float inv = 1.0f / (float)FX_BLOCK;
    float e, d;
    int b, c;
    if (s->dirty) mb_targets(s);

    /* crossover glide */
    e = s->lf_lo + (1.0f - XO_A) * (s->t_lf_lo - s->lf_lo);
    d = e - s->t_lf_lo; FX_ABS(d);
    if (d < 1e-5f) e = s->t_lf_lo;
    c = 0;
    if (e != s->lf_lo) c = 1;
    s->lf_lo = e;
    e = s->lf_hi + (1.0f - XO_A) * (s->t_lf_hi - s->lf_hi);
    d = e - s->t_lf_hi; FX_ABS(d);
    if (d < 1e-5f) e = s->t_lf_hi;
    if (e != s->lf_hi) c = 1;
    s->lf_hi = e;
    if (c) mb_xover_coefs(s);

    for (b = 0; b < 3; b++) {
        MbBand *bd = &s->band[b];
        MB_GLIDE(e, bd->mk, bd->t_mk, 1.0f - PAR_A, 1e-4f);
        bd->mk_i = (e - bd->mk) * inv;
        MB_GLIDE(e, bd->w, bd->t_w, 2.0f * (1.0f - PAR_A), 1e-4f);
        bd->w_i = (e - bd->w) * inv;
        FX_UNDENORM(bd->y1);
        FX_UNDENORM(bd->y2);
    }
    MB_GLIDE(e, s->mw, s->t_mw, 1.0f - PAR_A, 1e-6f);
    s->mw_i = (e - s->mw) * inv;
    MB_GLIDE(e, s->md, s->t_md, 1.0f - PAR_A, 1e-6f);
    s->md_i = (e - s->md) * inv;

    for (c = 0; c < 2; c++) {
        FX_UNDENORM(s->s1[c].ic1); FX_UNDENORM(s->s1[c].ic2);
        FX_UNDENORM(s->s1lp[c].ic1); FX_UNDENORM(s->s1lp[c].ic2);
        FX_UNDENORM(s->s1hp[c].ic1); FX_UNDENORM(s->s1hp[c].ic2);
        FX_UNDENORM(s->s2[c].ic1); FX_UNDENORM(s->s2[c].ic2);
        FX_UNDENORM(s->s2lp[c].ic1); FX_UNDENORM(s->s2lp[c].ic2);
        FX_UNDENORM(s->s2hp[c].ic1); FX_UNDENORM(s->s2hp[c].ic2);
        FX_UNDENORM(s->apl[c].ic1); FX_UNDENORM(s->apl[c].ic2);
        FX_UNDENORM(s->apd1[c].ic1); FX_UNDENORM(s->apd1[c].ic2);
        FX_UNDENORM(s->apd2[c].ic1); FX_UNDENORM(s->apd2[c].ic2);
        FX_UNDENORM(s->sc[c].ic1); FX_UNDENORM(s->sc[c].ic2);
    }
}

/* One sample of a band's compressor, the stock design: peak level (linear,
   a variable; overwritten) -> dB -> hard-knee curve (thr, rm1 = 1/ratio - 1)
   -> release stage y1 (instant attack, coefficient cr) -> attack stage y2
   (coefficient ca) -> + makeup mk (ramped by mk_i) -> linear gain. Used by
   mb_gain() (the tests) and by the block loop in mb_run(), so both are the
   same arithmetic. */
#define MB_GAIN_STEP(lev, thr, rm1, ca, cr, y1, y2, mk, mk_i, gain) do { \
        float xg_, over_, gc_, y_; \
        FX_LOG2(lev, xg_); \
        xg_ = xg_ * FX_DB_PER_LOG2; \
        over_ = xg_ - (thr); \
        gc_ = 0.0f; \
        if (over_ > 0.0f) gc_ = (rm1) * over_; \
        (y1) = gc_ + (cr) * ((y1) - gc_); \
        if (gc_ < (y1)) (y1) = gc_; \
        (y2) = (y1) + (ca) * ((y2) - (y1)); \
        (mk) += (mk_i); \
        y_ = ((y2) + (mk)) * (1.0f / FX_DB_PER_LOG2); \
        FX_EXP2(y_, gain); \
    } while (0)

#ifdef FX_TEST
/* one sample of a band; returns the linear gain (the tests use it) */
static float mb_gain(MbBand *bd, float level)
{
    float y1 = bd->y1, y2 = bd->y2, mk = bd->mk, g;
    MB_GAIN_STEP(level, bd->thr, bd->rm1, bd->ca, bd->cr, y1, y2, mk, bd->mk_i, g);
    bd->y1 = y1;
    bd->y2 = y2;
    bd->mk = mk;
    return g;
}
#endif

/* One control block (at most 32 samples), written for selcc (section 11):
   each stage is a loop over the block with its state in locals and no calls:
   the crossover per channel, the dry allpasses, the key level, the gain
   computer per band, then the output mix. Per-block decisions are made with
   if statements, not comparison initialisers (selcc re-evaluates those at
   every use). */
static void mb_run(MBComp *s, const float *inL, const float *inR,
                   const float *keyL, const float *keyR,
                   float *outL, float *outR, int n)
{
    const float kk = K_BW;
    int i, c, b, do_dry = 0;

    if (s->md != 0.0f || s->md_i != 0.0f) do_dry = 1;

    /* 1. crossover: LR4 split at the low edge, allpass on the low band, LR4 split at the high edge */
    for (c = 0; c < 2; c++) {
        const float *x = c ? inR : inL;
        float *lo = s->buf_lo[c], *md = s->buf_md[c], *hi = s->buf_hi[c];
        const float la1 = s->c_lo.a1, la2 = s->c_lo.a2, la3 = s->c_lo.a3;
        const float ha1 = s->c_hi.a1, ha2 = s->c_hi.a2, ha3 = s->c_hi.a3;
        float p1 = s->s1[c].ic1, p2 = s->s1[c].ic2, q1 = s->s1lp[c].ic1, q2 = s->s1lp[c].ic2;
        float r1 = s->s1hp[c].ic1, r2 = s->s1hp[c].ic2, u1 = s->apl[c].ic1, u2 = s->apl[c].ic2;
        float v1 = s->s2[c].ic1, v2 = s->s2[c].ic2, w1 = s->s2lp[c].ic1, w2 = s->s2lp[c].ic2;
        float z1 = s->s2hp[c].ic1, z2 = s->s2hp[c].ic2;
        for (i = 0; i < n; i++) {
            float xv = x[i], lp, bp, hp, t, l, m, h;
            /* split 1 */
            FX_SVF(p1, p2, la1, la2, la3, xv, lp, bp);
            hp = xv - kk * bp - lp;
            FX_SVF(q1, q2, la1, la2, la3, lp, l, bp);
            FX_SVF(r1, r2, la1, la2, la3, hp, t, bp);
            t = hp - kk * bp - t;                    /* HP of the HP: high side */
            /* low band: allpass at the upper crossover */
            FX_SVF(u1, u2, ha1, ha2, ha3, l, lp, bp);
            l = l - 2.0f * kk * bp;
            /* split 2 on the high side */
            FX_SVF(v1, v2, ha1, ha2, ha3, t, lp, bp);
            hp = t - kk * bp - lp;
            FX_SVF(w1, w2, ha1, ha2, ha3, lp, m, bp);
            FX_SVF(z1, z2, ha1, ha2, ha3, hp, h, bp);
            h = hp - kk * bp - h;
            lo[i] = l;
            md[i] = m;
            hi[i] = h;
        }
        s->s1[c].ic1 = p1; s->s1[c].ic2 = p2; s->s1lp[c].ic1 = q1; s->s1lp[c].ic2 = q2;
        s->s1hp[c].ic1 = r1; s->s1hp[c].ic2 = r2; s->apl[c].ic1 = u1; s->apl[c].ic2 = u2;
        s->s2[c].ic1 = v1; s->s2[c].ic2 = v2; s->s2lp[c].ic1 = w1; s->s2lp[c].ic2 = w2;
        s->s2hp[c].ic1 = z1; s->s2hp[c].ic2 = z2;

        /* dry path: the same two allpasses, so dry and wet stay in phase */
        if (do_dry) {
            float *dr = s->buf_dry[c];
            float d1 = s->apd1[c].ic1, d2 = s->apd1[c].ic2, e1 = s->apd2[c].ic1, e2 = s->apd2[c].ic2;
            for (i = 0; i < n; i++) {
                float xv = x[i], lp, bp, t;
                FX_SVF(d1, d2, la1, la2, la3, xv, lp, bp);
                t = xv - 2.0f * kk * bp;
                FX_SVF(e1, e2, ha1, ha2, ha3, t, lp, bp);
                dr[i] = t - 2.0f * kk * bp;
            }
            s->apd1[c].ic1 = d1; s->apd1[c].ic2 = d2; s->apd2[c].ic1 = e1; s->apd2[c].ic2 = e2;
        }
    }

    /* 2. key level max(|L|,|R|) through the SCF filter, when the bands detect the key */
    if (s->sc_ext) {
        float *kb = s->buf_key;
        const float sa1 = s->scc.a1, sa2 = s->scc.a2, sa3 = s->scc.a3;
        int lp_type = 0;
        if (s->sc_lp) lp_type = 1;
        for (c = 0; c < 2; c++) {
            const float *k = c ? keyR : keyL;
            float f1 = s->sc[c].ic1, f2 = s->sc[c].ic2;
            for (i = 0; i < n; i++) {
                float v = 0.0f, lp, bp;
                if (k) v = k[i];
                FX_SVF(f1, f2, sa1, sa2, sa3, v, lp, bp);
                if (lp_type) v = lp; else v = v - kk * bp - lp;
                FX_ABS(v);
                if (c == 0) kb[i] = v;
                else if (v > kb[i]) kb[i] = v;     /* max(|L|, |R|) */
            }
            s->sc[c].ic1 = f1; s->sc[c].ic2 = f2;
        }
    }

    /* 3. gain computer per band, times the listen weight */
    for (b = 0; b < 3; b++) {
        MbBand *bd = &s->band[b];
        const float *bl = b == 0 ? s->buf_lo[0] : (b == 1 ? s->buf_md[0] : s->buf_hi[0]);
        const float *br = b == 0 ? s->buf_lo[1] : (b == 1 ? s->buf_md[1] : s->buf_hi[1]);
        const float *kb = s->buf_key;
        float *g = s->buf_g[b];
        const float thr = bd->thr, rm1 = bd->rm1, ca = bd->ca, cr = bd->cr;
        const float mk_i = bd->mk_i, w_i = bd->w_i;
        float y1 = bd->y1, y2 = bd->y2, mk = bd->mk, w = bd->w;
        int key = 0, idle = 0;
        if (s->sc_ext) key = 1;
        /* a band at 1:1 with nothing left to release and a still makeup has
           one constant gain: the loop below would give exactly that value */
        if (!bd->on && y1 == 0.0f && y2 == 0.0f && mk_i == 0.0f) idle = 1;
        if (idle) {
            float y = (y2 + mk) * (1.0f / FX_DB_PER_LOG2), gain;
            FX_EXP2(y, gain);
            for (i = 0; i < n; i++) {
                w += w_i;
                g[i] = gain * w;
            }
        } else {
            for (i = 0; i < n; i++) {
                float lev, gain;
                if (key) {
                    lev = kb[i];
                } else {
                    float l = bl[i], r = br[i];
                    FX_ABS(l);
                    FX_ABS(r);
                    lev = l > r ? l : r;            /* max(|L|, |R|) */
                }
                MB_GAIN_STEP(lev, thr, rm1, ca, cr, y1, y2, mk, mk_i, gain);
                w += w_i;
                g[i] = gain * w;
            }
        }
        bd->y1 = y1;
        bd->y2 = y2;
        bd->mk = mk;
        bd->w = w;
    }

    /* 4. output: Tw(MIX) * (sum of the bands) + Td(MIX) * dry */
    {
        const float *l0 = s->buf_lo[0], *m0 = s->buf_md[0], *h0 = s->buf_hi[0], *d0 = s->buf_dry[0];
        const float *l1 = s->buf_lo[1], *m1 = s->buf_md[1], *h1 = s->buf_hi[1], *d1 = s->buf_dry[1];
        const float *g0 = s->buf_g[0], *g1 = s->buf_g[1], *g2 = s->buf_g[2];
        float mw = s->mw, md = s->md;
        const float mw_i = s->mw_i, md_i = s->md_i;
        for (i = 0; i < n; i++) {
            float wet, y, dl = 0.0f, dr = 0.0f;
            if (do_dry) { dl = d0[i]; dr = d1[i]; }
            mw += mw_i;
            md += md_i;
            wet = g0[i] * l0[i] + g1[i] * m0[i] + g2[i] * h0[i];
            y = mw * wet + md * dl;
            FX_UNDENORM(y);
            outL[i] = y;
            wet = g0[i] * l1[i] + g1[i] * m1[i] + g2[i] * h1[i];
            y = mw * wet + md * dr;
            FX_UNDENORM(y);
            outR[i] = y;
        }
        s->mw = mw;
        s->md = md;
    }
}

void mbcomp_process(MBComp *s, const float *inL, const float *inR,
                    const float *keyL, const float *keyR,
                    float *outL, float *outR, int n)
{
    int i = 0;
    while (i < n) {
        int m;
        if (s->ctl_left <= 0) {
            mb_control(s);
            s->ctl_left = FX_BLOCK;
        }
        m = n - i;
        if (m > s->ctl_left) m = s->ctl_left;
        mb_run(s, inL + i, inR + i, keyL ? keyL + i : 0, keyR ? keyR + i : 0,
               outL + i, outR + i, m);
        i += m;
        s->ctl_left -= m;
    }
}
