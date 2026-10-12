/* SPDX-License-Identifier: GPL-2.0-or-later */
/*
 * flanger.c - a flanger / comb for the chorus slot (T3.11/T5.1, G3). See flanger.h.
 *
 * Choices (notes/T3.11 section 12.5):
 * - The sweep moves the delay in log2 (octaves), so the comb's notches move at
 *   an even musical speed (a triangle LFO sweeps at a constant rate in pitch; a
 *   sine slows down at both ends, the "swell"). MAN is the centre and DPTH the
 *   half-range; where the range would leave 0.05..77 ms it is cut to fit and the
 *   LFO still spans all of it (no flat stretches).
 * - The delay is updated per sample from a quadratic through three points of
 *   the sweep per 32-sample control block (start, middle, end), run by forward
 *   differences: two additions per sample, no exp per sample, and no corner in
 *   the pitch every 32 samples (straight lines between block ends would have one).
 * - The LFO phase is a 32-bit integer, so its rate is exact and never drifts.
 *   Its output goes through a one-pole lag (SLEW, at least 1 ms), so square,
 *   saw and random-step shapes jump in a short glide, not a click.
 * - Reads are 4-point Hermite (as the tape delay): smooth for slow sweeps, no
 *   zipper.
 * - Feedback is bipolar, damped (one-pole low-pass, DAMP) and soft-limited (the
 *   cubic clip of fxmath.h: linear for small signals, at most 1.0), so a
 *   resonant setting saturates instead of running away. At short delays and
 *   high feedback the loop rings like a plucked string (Karplus-Strong); the
 *   read delay is shortened by the damping filter's own delay at low
 *   frequencies, (1-a)/a samples, so the ringing pitch is 1/MAN.
 * - The wet polarity (POL) is crossfaded over about 10 ms when switched.
 * - Through-zero flanging is left out: in a send slot the bus adds the
 *   undelayed dry signal, which a through-zero design would have to delay too,
 *   so it cannot cancel cleanly there (12.5).
 * - No shift by an immediate amount in the per-block code: selache's assembler
 *   mis-encodes negative shift immediates (notes/T3.11 12.7).
 */
#include "flanger.h"

#define FL_D0     2.4105913f   /* MAN 0: 0.050221 ms = 48000/19912.13 samples (19912.13 Hz = 440 Hz * 2^(66/12)) */
#define FL_LMIN   1.2693871f   /* log2(FL_D0) */
#define FL_LMAX   11.852720f   /* log2(FL_D0) + 127/12: 77.05 ms */
#define FL_SM     0.013245f    /* control-rate one-pole, tau 50 ms: 1 - exp(-1/(0.05*1500)) */
#define FL_FBSM   0.03278f     /* feedback glide, tau 20 ms */
#define FL_PGSM   0.043471f    /* polarity glide, two one-poles of tau 15 ms in a row (no kink at the start) */
#define FL_FBMAX  0.99f
#define FL_DAMP_OCT 4.70f      /* DAMP 127: a = 2^-4.7 = 0.0385 (one-pole corner about 300 Hz) */
#define FL_OFS_MAX 240.0f      /* 5 ms */

/* SYNC: LFO cycle length in beats (4 beats = 1 bar) */
static const float FL_SYNC_BEATS[13] = { 0.0f, 64.0f, 32.0f, 16.0f, 8.0f, 4.0f, 2.0f, 1.5f, 1.0f,
                                         0.75f, 0.5f, 0.25f, 0.125f };

float flanger_man_ms(float knob)   { return 1000.0f / FX_SR * fx_exp2(FL_LMIN + fx_clampf(knob, 0.0f, 127.0f) * (1.0f / 12.0f)); }
float flanger_depth_oct(float knob) { return fx_knob01(knob) * 3.3219281f; }
float flanger_hpf_hz(float knob)    { return 20.0f * fx_exp2(fx_knob01(knob) * 6.6438562f); }  /* x100 */
float flanger_fb(float knob)
{
    float x = fx_clampf(knob, 0.0f, 127.0f) - 64.0f;
    return x > 0.0f ? FL_FBMAX * x * (1.0f / 63.0f) : FL_FBMAX * x * (1.0f / 64.0f);
}
static float fl_damp_a(float knob) { return fx_exp2(-FL_DAMP_OCT * fx_knob01(knob)); }
float flanger_damp_hz(float knob)
{
    float a = fl_damp_a(knob);
    if (a >= 1.0f) return 0.0f;
    return -fx_log2(1.0f - a) * FX_LN2 * FX_SR / (2.0f * FX_PI);
}

/* The table read is its own statement on purpose: written as
   `bpm * (1.0f / 60.0f) / FL_SYNC_BEATS[sy]`, selcc keeps sy in a register and
   loads the constant 1/60 into the same register before it scales the index,
   so the read goes to a wild address (notes/T3.11 12.7, a third selcc bug). */
static float fl_rate_hz(const float *knob, float bpm)
{
    int sy = (int)(fx_clampf(knob[FL_SYNC], 0.0f, 12.0f) + 0.5f);
    float beats;
    if (sy > 0) {
        beats = FL_SYNC_BEATS[sy];
        return bpm / (60.0f * beats);
    }
    return 0.02f * fx_exp2(fx_knob01(knob[FL_SPD]) * 8.9657843f);   /* x500 */
}
float flanger_rate_hz(const Flanger *s) { return fl_rate_hz(s->knob, s->bpm); }

/* sin(2 pi p), accurate (Taylor on a quarter wave, error < 1e-6); control rate only */
static float fl_sin2pi(float p)
{
    if (p < 0.25f) return fx_sin_q(2.0f * FX_PI * p);
    if (p < 0.5f) return fx_sin_q(2.0f * FX_PI * (0.5f - p));
    if (p < 0.75f) return -fx_sin_q(2.0f * FX_PI * (p - 0.5f));
    return -fx_sin_q(2.0f * FX_PI * (1.0f - p));
}

/* the deterministic shapes at phase p in [0, 1]: all start at 0 or -1 and are
   +1 at their top */
float flanger_lfo(float p, int shape)
{
    float t;
    if (p >= 1.0f) p -= 1.0f;
    if (p < 0.0f) p += 1.0f;
    switch (shape) {
    case 1:                                    /* triangle, 0 at 0, +1 at 0.25, -1 at 0.75 */
        t = p + 0.25f;
        if (t >= 1.0f) t -= 1.0f;
        t = t - 0.5f;
        if (t < 0.0f) t = -t;
        return 1.0f - 4.0f * t;
    case 2: return 2.0f * p - 1.0f;            /* saw up */
    case 3: return 1.0f - 2.0f * p;            /* saw down */
    case 4: return p < 0.5f ? 1.0f : -1.0f;    /* square */
    default: return fl_sin2pi(p);              /* sine (and the fallback) */
    }
}

/* the sweep: LFO value l (-1..1) -> delay in samples, for a centre lc and a depth dep */
static float fl_sweep(float l, float lc, float dep)
{
    float lo = lc - dep, hi = lc + dep;
    if (lo < FL_LMIN) lo = FL_LMIN;
    if (hi > FL_LMAX) hi = FL_LMAX;
    return fx_exp2(lo + (hi - lo) * 0.5f * (l + 1.0f));
}
float flanger_delay_at(const Flanger *s, float l) { return fl_sweep(l, s->lc, s->dep); }

static float fl_phase(uint32_t p)          /* 2^-32 cycles -> [0, 1], no shift */
{
    return (float)(int32_t)(p ^ 0x80000000u) * 2.3283064e-10f + 0.5f;
}

static void fl_targets(Flanger *s)
{
    float hz = fl_rate_hz(s->knob, s->bpm), x, tau;
    s->t_lc = FL_LMIN + fx_clampf(s->knob[FL_MAN], 0.0f, 127.0f) * (1.0f / 12.0f);
    s->t_dep = flanger_depth_oct(s->knob[FL_DPTH]);
    s->t_off = 0.5f * fx_knob01(s->knob[FL_WDTH]);
    x = fx_knob01(s->knob[FL_OFS]);
    s->t_ofs = FL_OFS_MAX * x * x;
    s->t_lh = fx_log2(flanger_hpf_hz(s->knob[FL_HPF]));
    s->t_da = fl_damp_a(s->knob[FL_DAMP]);
    s->t_fb = flanger_fb(s->knob[FL_FDBK]);
    s->t_pg = s->knob[FL_POL] >= 0.5f ? -1.0f : 1.0f;
    s->shape = (int)(fx_clampf(s->knob[FL_SHP], 0.0f, 5.0f) + 0.5f);
    tau = 0.001f * fx_exp2(fx_knob01(s->knob[FL_SLEW]) * 8.9657843f);       /* 1 ms .. 0.5 s */
    s->sl_h = 1.0f - fx_onepole_a(tau, 2.0f * FX_SR / (float)FX_BLOCK);    /* per half block */
    /* LFO phase increments in 2^-32 cycles */
    s->rate = (uint32_t)(hz * ((float)FX_BLOCK / FX_SR) * 4294967296.0f);
    s->rate_h = (uint32_t)(hz * (0.5f * (float)FX_BLOCK / FX_SR) * 4294967296.0f);
    s->dirty = 0;
}

void flanger_set(Flanger *s, int param, float value)
{
    if (param < 0 || param >= FL_NPARAMS) return;
    s->knob[param] = fx_clampf(value, 0.0f, 127.0f);
    s->dirty = 1;
}

void flanger_set_bpm(Flanger *s, float bpm)
{
    s->bpm = fx_clampf(bpm, 30.0f, 300.0f);
    s->dirty = 1;
}

static void fl_hpf_coef(Flanger *s)
{
    float g = fx_svf_g(fx_exp2(s->lh));
    s->hpG = g / (1.0f + g);
}

/* read delay of channel c for a (slewed) LFO value: the sweep, the right
   channel's time offset, minus the damping filter's low-frequency delay;
   the settings are passed in, so the middle of a block can use values
   halfway through their glide */
static float fl_read_delay(float l, int c, float lc, float dep, float ofs, float da)
{
    float d = fl_sweep(l, lc, dep), comp = (1.0f - da) / da;
    if (c) d += ofs;
    if (comp > 0.5f * d) comp = 0.5f * d;
    d -= comp;
    return fx_clampf(d, 2.0f, (float)(FL_RING - 8));
}

/* raw LFO values at the middle and the end of the coming block, for the
   right channel (c = 1, phase ahead by off) and then the left; the random
   step draws a new value when the right channel's cycle starts, and the left
   channel takes the same value when its own cycle starts */
static void fl_lfo_block(Flanger *s, float *mid, float *end)
{
    int c;
    for (c = 1; c >= 0; c--) {
        uint32_t b = s->ph, pm, pe;
        if (c) b += (uint32_t)(s->off * 4294967296.0f);
        pm = b + s->rate_h;
        pe = b + s->rate;
        if (s->shape == 5) {
            /* two declarations: selcc drops the store of old in "float old = ..., nv = old;" (12.7) */
            float old = s->rnd[c];
            float nv = old;
            if (pe < b) {                        /* a cycle starts in this block */
                if (c) {
                    nv = fx_rands(&s->rng);
                    s->rnd_next = nv;
                } else {
                    nv = s->rnd_next;
                }
                s->rnd[c] = nv;
            }
            mid[c] = pm < b ? nv : old;
            end[c] = nv;
        } else {
            mid[c] = flanger_lfo(fl_phase(pm), s->shape);
            end[c] = flanger_lfo(fl_phase(pe), s->shape);
        }
    }
}

void flanger_snap(Flanger *s)
{
    int c;
    if (s->dirty) fl_targets(s);
    s->lc = s->t_lc;
    s->dep = s->t_dep;
    s->off = s->t_off;
    s->ofs = s->t_ofs;
    s->lh = s->t_lh;
    s->da = s->t_da;
    fl_hpf_coef(s);
    s->fb = s->t_fb;
    s->fb_i = 0.0f;
    s->pg = s->t_pg;
    s->pg1 = s->t_pg;
    s->pg_i = 0.0f;
    for (c = 0; c < 2; c++) {
        uint32_t b = s->ph;
        float l;
        if (c) b += (uint32_t)(s->off * 4294967296.0f);
        l = s->shape == 5 ? s->rnd[c] : flanger_lfo(fl_phase(b), s->shape);
        s->ls[c] = l;
        s->dend[c] = fl_read_delay(l, c, s->lc, s->dep, s->ofs, s->da);
        s->dly[c] = s->dend[c];
        s->dv[c] = 0.0f;
        s->dacc[c] = 0.0f;
    }
}

void flanger_init(Flanger *s)
{
    int c, i;
    for (c = 0; c < 2; c++) {
        for (i = 0; i < FL_RING + 3; i++) s->ring[c][i] = 0.0f;
        s->hp_s[c] = 0.0f;
        s->fbl_s[c] = 0.0f;
        s->rnd[c] = 0.0f;
    }
    s->rnd_next = 0.0f;
    s->rng = 0x2545f491u;
    s->w = 0;
    s->ph = 0u;
    s->bpm = 120.0f;
    /* the default patch: a slow, deep, resonant swell (12.5) */
    s->knob[FL_DPTH] = 96.0f;    /* +-2.5 octaves around MAN: 0.16..5 ms */
    s->knob[FL_SPD] = 30.0f;     /* 0.087 Hz: 11.5 s per cycle */
    s->knob[FL_HPF] = 32.0f;     /* 64 Hz: the lows stay dry */
    s->knob[FL_WDTH] = 48.0f;    /* right LFO 68 degrees ahead */
    s->knob[FL_FDBK] = 107.0f;   /* +68 % */
    s->knob[FL_MAN] = 50.0f;     /* centre 0.90 ms */
    s->knob[FL_SHP] = 0.0f;      /* sine */
    s->knob[FL_SLEW] = 0.0f;     /* 1 ms */
    s->knob[FL_DAMP] = 12.0f;    /* about 10 kHz */
    s->knob[FL_SYNC] = 0.0f;     /* free */
    s->knob[FL_OFS] = 0.0f;
    s->knob[FL_POL] = 0.0f;
    s->dirty = 1;
    s->ctl_left = 0;
    flanger_snap(s);
}

#define FL_GLIDE(x, target, b, eps) do { \
        float d_; \
        (x) = (x) + (b) * ((target) - (x)); \
        d_ = (x) - (target); \
        FX_ABS(d_); \
        if (d_ < (eps)) (x) = (target); \
    } while (0)

/* One control update, at the start of every 32-sample control block: glide
   the settings, advance the LFO and set up each channel's delay trajectory
   for the block (a quadratic through the block's start, middle and end). */
static void fl_control(Flanger *s)
{
    const float inv = 1.0f / (float)FX_BLOCK;
    float lh_old, e, mid[2], end[2];
    const float lc0 = s->lc, dep0 = s->dep, ofs0 = s->ofs, da0 = s->da;   /* before this block's glide */
    float lcm, depm, ofsm, dam;
    int c;

    if (s->dirty) fl_targets(s);
    FL_GLIDE(s->lc, s->t_lc, FL_SM, 1e-5f);
    FL_GLIDE(s->dep, s->t_dep, FL_SM, 1e-5f);
    FL_GLIDE(s->off, s->t_off, FL_SM, 1e-6f);
    FL_GLIDE(s->ofs, s->t_ofs, FL_SM, 1e-4f);
    FL_GLIDE(s->da, s->t_da, FL_SM, 1e-6f);
    lh_old = s->lh;
    FL_GLIDE(s->lh, s->t_lh, FL_SM, 1e-5f);
    if (s->lh != lh_old) fl_hpf_coef(s);
    e = s->fb;
    FL_GLIDE(e, s->t_fb, FL_FBSM, 1e-6f);
    s->fb_i = (e - s->fb) * inv;
    FL_GLIDE(s->pg1, s->t_pg, FL_PGSM, 1e-5f);
    e = s->pg;
    FL_GLIDE(e, s->pg1, FL_PGSM, 1e-5f);
    s->pg_i = (e - s->pg) * inv;
    lcm = 0.5f * (lc0 + s->lc);
    depm = 0.5f * (dep0 + s->dep);
    ofsm = 0.5f * (ofs0 + s->ofs);
    dam = 0.5f * (da0 + s->da);

    fl_lfo_block(s, mid, end);
    for (c = 0; c < 2; c++) {
        float lm = s->ls[c] + s->sl_h * (mid[c] - s->ls[c]);
        float le = lm + s->sl_h * (end[c] - lm);
        float p0 = s->dend[c];
        float p1 = fl_read_delay(lm, c, lcm, depm, ofsm, dam);
        float p2 = fl_read_delay(le, c, s->lc, s->dep, s->ofs, s->da);
        float B = -3.0f * p0 + 4.0f * p1 - p2;   /* D(t) = p0 + B t + C t^2, t = 0..1 over the block */
        float C = 2.0f * p0 - 4.0f * p1 + 2.0f * p2;
        const float h = inv, h2 = inv * inv;
        s->ls[c] = le;
        s->dly[c] = p0 + B * h + C * h2;         /* sample i uses D((i+1)/32) */
        s->dv[c] = B * h + 3.0f * C * h2;
        s->dacc[c] = 2.0f * C * h2;
        s->dend[c] = p2;
    }
    s->ph += s->rate;

    FX_UNDENORM(s->hp_s[0]); FX_UNDENORM(s->hp_s[1]);
    FX_UNDENORM(s->fbl_s[0]); FX_UNDENORM(s->fbl_s[1]);
}

/* The per-sample loops, one call per control block (at most 32 samples), one
   loop per channel with its state in locals (selcc, section 11). */
static void fl_run(Flanger *s, const float *inL, const float *inR, float *outL, float *outR, int n)
{
    const int32_t mask = FL_RING - 1;
    const int32_t w0 = s->w;
    const float hpG = s->hpG, fb_i = s->fb_i, pg_i = s->pg_i, da = s->da;
    float fb_end = s->fb, pg_end = s->pg;
    int c, i;

    for (c = 0; c < 2; c++) {
        const float *x = c ? inR : inL;
        float *out = c ? outR : outL;
        float *rb = s->ring[c];
        float d = s->dly[c], dv = s->dv[c];
        const float dacc = s->dacc[c];
        float hs = s->hp_s[c], fl = s->fbl_s[c], fb = s->fb, pg = s->pg;
        int32_t w = w0;
        for (i = 0; i < n; i++) {
            float xv = x[i], v, lpo, dd, f, xm, x0, x1, x2, k1, k2, k3, y, t;
            const float *p;
            int32_t di;

            /* input high-pass (TPT one-pole) */
            v = (xv - hs) * hpG;
            lpo = v + hs;
            hs = lpo + v;
            xv = xv - lpo;

            /* read at the swept delay: 4-point Hermite */
            dd = d;
            FX_CLAMP(dd, 2.0f, (float)(FL_RING - 8));
            di = (int32_t)dd;
            f = dd - (float)di;
            p = rb + ((w - di - 2) & mask);     /* p[0..3]: delays di+2, di+1, di, di-1 */
            x2 = p[0]; x1 = p[1]; x0 = p[2]; xm = p[3];
            k1 = 0.5f * (x1 - xm);
            k2 = xm - 2.5f * x0 + 2.0f * x1 - 0.5f * x2;
            k3 = 0.5f * (x2 - xm) + 1.5f * (x0 - x1);
            y = ((k3 * f + k2) * f + k1) * f + x0;
            d += dv;
            dv += dacc;

            /* feedback: damped, soft-limited (fx_sat), then into the ring */
            fb += fb_i;
            fl += da * (y - fl);
            t = fb * fl;
            FX_CLAMP(t, -1.5f, 1.5f);
            t = t - 0.14814815f * t * t * t;
            v = xv + t;
            FX_UNDENORM(v);
            rb[w] = v;
            if (w < 3) rb[w + FL_RING] = v;      /* the guard copy */
            w = (w + 1) & mask;

            pg += pg_i;
            y = pg * y;
            FX_UNDENORM(y);
            out[i] = y;
        }
        s->dly[c] = d;
        s->dv[c] = dv;
        s->hp_s[c] = hs;
        s->fbl_s[c] = fl;
        fb_end = fb;
        pg_end = pg;
    }
    s->fb = fb_end;
    s->pg = pg_end;
    s->w = (w0 + n) & mask;
}

void flanger_process(Flanger *s, const float *inL, const float *inR,
                     float *outL, float *outR, int n)
{
    int i = 0;
    while (i < n) {
        int m;
        if (s->ctl_left <= 0) {
            fl_control(s);
            s->ctl_left = FX_BLOCK;
        }
        m = n - i;
        if (m > s->ctl_left) m = s->ctl_left;
        fl_run(s, inL + i, inR + i, outL + i, outR + i, m);
        i += m;
        s->ctl_left -= m;
    }
}
