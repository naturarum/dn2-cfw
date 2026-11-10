/* SPDX-License-Identifier: GPL-2.0-or-later */
/*
 * mrb.c - MRB, the morphing reverb on a budget (mrb.h).
 *
 * Written for selache's selcc as well as clang, as morph.c and spring.c. selcc inlines nothing, keeps every
 * variable in its stack slot between statements and pays about five instructions for each array access, so
 * the work is a few fused loops over a block, each sample read and written once, a few long statements,
 * pointers moved once a pass. No division (reciprocals are Newton steps, the same operations on both
 * builds). Its known miscompiles avoided: one declaration a variable, no local initialised with a comparison,
 * no negative number in a static float table, no store indexed by a parameter after a call, no macro
 * parameter named like a number suffix, no pointer chosen in an if; and the three found here (notes/MR02):
 * at most three arguments a kernel (a float passed on the stack and used in a loop lost its register),
 * pointer + integer sums as one integer offset added to a local pointer (in p + a + (b & m) the last term
 * was not scaled to floats), no switch at the end of a void function (it had no return).
 *
 * Every ring (tank lines, pre-delay, diffusers) is a power of two long and followed by a copy of its first
 * block (16 samples), written whenever that block is: a block's read of 17 samples from any start then never
 * wraps. The tank lines and diffusers are at least a block long, so a block reads them before it writes.
 */
#include "mrb.h"

#define MB_SR       24000.0f
#define MB_ISR      (1.0f / 24000.0f)
#define MB_SPMS     24.0f                   /* samples a ms */
#define MB_BT       (32.0f / 48000.0f)      /* a block's time */
#define MB_MIND     ((float)(MB_HB + 3))    /* the shortest line read: longer than a block, with the slew */
#define MB_PREMAX   120.0f                  /* ms of extra pre-delay (PRE) */
#define MB_SENDHR   0.7f
#define MB_PI_F     3.14159274f
#define MB_DIRTY_A  11.5839453f             /* the one-pole's tan(pi x), fine below x = 0.1 */
#define MB_TINY     1e-18f                  /* added and taken away: values far below it become 0 */
#define MB_SLOW     (MB_BT * 12.0f)         /* CHAR and SIZE glide (about 80 ms) */
#define MB_FAST     (MB_BT * 50.0f)         /* the others (about 20 ms) */
#define MB_PMASK    (MB_PSEG - 1u)
#define MB_LMASK    (MB_LSEG - 1u)
#define MB_DMASK    (MB_DSEG - 1u)
/* half-bands (spring.c's): one first-order allpass a branch in z^-2 (W), or two (S) */
#define MB_HW0      0.138025073f
#define MB_HW1      0.584683380f
#define MB_HS00     0.089588496f
#define MB_HS01     0.577111126f
#define MB_HS10     0.310671415f
#define MB_HS11     0.850563043f

/* the three end points of CHAR, in the order of the MBC_ fields (no negative numbers: selcc mis-emits them
   in static tables). From MRPH's: lines 0-3 of its eight */
static const float MB_ROOM[MBC_N] = {
    8.0f, 11.5f, 15.5f, 22.0f,
    2.0f, 0.6f, 35.0f, 0.35f,
    1.0f, 1.0f, 0.0f,
    0.08f, 0.35f, 90.0f, 16000.0f, 0.9f, 0.8f
};
static const float MB_HALL[MBC_N] = {
    31.0f, 47.0f, 61.0f, 83.0f,
    28.0f, 0.3f, 70.0f, 1.0f,
    1.0f, 1.0f, 0.0f,
    0.45f, 0.5f, 25.0f, 14000.0f, 1.0f, 1.0f
};
static const float MB_SPRING[MBC_N] = {
    55.0f, 63.0f, 71.0f, 79.0f,
    18.0f, 0.0f, 40.0f, 0.9f,
    0.7f, 0.7f, 0.7f,
    0.15f, 2.5f, 160.0f, 3200.0f, 0.25f, 1.45f
};
static const float MB_LFORATIO[MB_NL] = { 1.0f, 1.37f, 0.71f, 1.93f };
static const float MB_SPREAD[MB_NSL] = { 1.0f, 0.93f };
static const float MB_DIFMS[MB_ND] = { 4.77f, 3.59f, 2.73f, 1.83f };
/* early taps: time (share of the span) and level (the middle tap of each side is negative); the levels
   make up for having three a side where MRPH has six */
static const float MB_ETL[MB_NE] = { 0.07f, 0.36f, 0.71f };
static const float MB_ETR[MB_NE] = { 0.12f, 0.43f, 0.77f };
static const float MB_EGL[MB_NE] = { 1.07f, 0.70f, 0.46f };
static const float MB_EGR[MB_NE] = { 1.02f, 0.74f, 0.42f };

/* a ramped read's set-up for a delay moving from d0_ to d1_ over the block (at most a sample): the whole
   part from the middle, the fraction at the start and its step a sample */
#define MB_RSET(d0_, d1_, i_, fr_, fi_) do { \
        float mid_; \
        mid_ = 0.5f * ((d0_) + (d1_)); \
        (i_) = (int32_t)mid_; \
        (fr_) = (d0_) - (float)(i_); \
        (fi_) = ((d1_) - (d0_)) * (1.0f / (float)MB_HB); \
    } while (0)

/* move x_ towards t_ by at most one sample */
#define MB_SLEW(x_, t_) do { \
        if ((t_) > (x_) + 1.0f) { \
            (x_) = (x_) + 1.0f; \
        } else if ((t_) < (x_) - 1.0f) { \
            (x_) = (x_) - 1.0f; \
        } else { \
            (x_) = (t_); \
        } \
    } while (0)

float mb_recip(float x);
float mb_exp2(float x);
float mb_tan(float w);
float mb_lfo(float phase);
void mb_blend(Mrb *s);
void mb_lines(Mrb *s);
void mb_decay(Mrb *s);
void mb_filters(Mrb *s);
void mb_rot(Mrb *s, float steps);
void mb_control(Mrb *s);
void mb_input(Mrb *s, const float *a, const float *b);
void mb_front(Mrb *s);
void mb_readr(Mrb *s, int line, float *dst);
void mb_disp(float *y, float a, float *st);
void mb_tank(Mrb *s);
void mb_up(const float *o, float *out, float *st);
void mb_mirror(float *ring, uint32_t len);
void mb_flush(float *f, int n);
void mb_block(Mrb *s, const float *inL, const float *inR, float *outL, float *outR);

/* 1/x for a positive normal x: a seed from the bits, four Newton steps */
float mb_recip(float x)
{
    float r;
    r = fx_i2f(0x7ef311c7u - fx_f2i(x));
    r = r * (2.0f - x * r);
    r = r * (2.0f - x * r);
    r = r * (2.0f - x * r);
    r = r * (2.0f - x * r);
    return r;
}

/* 2^x for -126 < x < 126 (fx_exp2's polynomial, without its clamp call) */
float mb_exp2(float x)
{
    int32_t n;
    float fr;
    float p;
    n = (int32_t)x;
    if ((float)n > x) n = n - 1;
    fr = x - (float)n;
    p = 0.0018854038f;
    p = p * fr + 0.0089728993f;
    p = p * fr + 0.055836598f;
    p = p * fr + 0.24015244f;
    p = p * fr + 0.69315254f;
    p = 1.0f + p * fr;
    return fx_i2f(fx_f2i(p) + ((uint32_t)n << 23));
}

/* tan(w) for 0 <= w < pi/2 */
float mb_tan(float w)
{
    float sn;
    float cs;
    sn = fx_sin_q(w);
    cs = fx_cos_q(w);
    return sn * mb_recip(cs);
}

/* a smooth LFO for a phase in [0, 1): a triangle through a cubic, close to a cosine */
float mb_lfo(float phase)
{
    float x;
    if (phase < 0.5f) {
        x = 4.0f * phase - 1.0f;
    } else {
        x = 3.0f - 4.0f * phase;
    }
    return x * (1.5f - 0.5f * x * x);
}

/* the character from CHAR: room (0) - hall (0.5) - spring (1), flat for 0.06 around each end point */
void mb_blend(Mrb *s)
{
    float ch;
    float t;
    float a;
    float b;
    int f;
    int upper;
    ch = s->cur[MB_CHAR];
    upper = 0;
    if (ch >= 0.5f) upper = 1;
    if (upper) {
        t = (ch - 0.5f - 0.06f) * (1.0f / 0.38f);
        FX_CLAMP(t, 0.0f, 1.0f);
        for (f = 0; f < MBC_N; f++) {
            a = MB_HALL[f];
            b = MB_SPRING[f];
            s->c[f] = a + (b - a) * t;
        }
    } else {
        t = (ch - 0.06f) * (1.0f / 0.38f);
        FX_CLAMP(t, 0.0f, 1.0f);
        for (f = 0; f < MBC_N; f++) {
            a = MB_ROOM[f];
            b = MB_HALL[f];
            s->c[f] = a + (b - a) * t;
        }
    }
}

/* group 1: the character, the line lengths and dispersion, the pre-delay and early taps */
void mb_lines(Mrb *s)
{
    float l2s;
    float scale;
    float soft;
    float loop;
    float dl;
    float a;
    float x;
    float pms;
    float pd;
    float span;
    float d;
    float lev;
    int i;
    int j;
    const float *c = s->c;

    mb_blend(s);
    /* size: lines 0.25x .. 1.6x; the pre-delay and early taps follow more gently */
    l2s = -2.0f + 2.678f * s->cur[MB_SIZE];
    scale = mb_exp2(l2s);
    soft = mb_exp2(0.7f * l2s);
    s->mod = c[MBC_MODMS] * MB_SPMS;
    for (i = 0; i < MB_NL; i++) {
        loop = c[MBC_LINE + i] * scale * MB_SPMS;
        s->loop[i] = loop;
        dl = loop;
        if (i < MB_NSL) {
            /* the dispersion adds its own delay: take its low-frequency group delay out of the line */
            a = c[MBC_DISP] * MB_SPREAD[i];
            s->disp[i] = a;
            dl -= ((float)(MB_STAGES * MB_ST) * (1.0f - a)) * mb_recip(1.0f + a);
        }
        s->lbase[i] = dl;
        s->lfi[i] = c[MBC_MODHZ] * MB_LFORATIO[i] * MB_BT;
    }
    /* pre-delay (the character's, scaled, plus PRE) and the early taps (in front of the character's) */
    x = s->cur[MB_PRE];
    pms = MB_PREMAX * x * x;
    pd = (c[MBC_PRE] * soft + pms) * MB_SPMS;
    if (pd < 1.0f) pd = 1.0f;
    if (pd > s->pmax) pd = s->pmax;
    s->pret = pd;
    span = c[MBC_ESPAN] * soft;
    lev = c[MBC_ELEV];
    for (j = 0; j < MB_NE; j++) {
        d = (1.0f + MB_ETL[j] * span + pms) * MB_SPMS;
        if (d > s->pmax) d = s->pmax;
        s->edt[j] = d;
        d = (1.0f + MB_ETR[j] * span + pms) * MB_SPMS;
        if (d > s->pmax) d = s->pmax;
        s->edt[MB_NE + j] = d;
        s->eg[j] = MB_EGL[j] * lev;
        s->eg[MB_NE + j] = MB_EGR[j] * lev;
    }
    s->eg[1] = -s->eg[1];
    s->eg[MB_NE + 1] = -s->eg[MB_NE + 1];
    s->slewing = 2;
}

/* group 2: the absorption, from the decay times low and high */
void mb_decay(Mrb *s)
{
    float t60;
    float tr;
    float dps;
    float itr;
    float gdc;
    float gny;
    float pole;
    float e;
    int i;
    /* decay 0.3 s .. 36 s x the character's scale; TONE: the high decay over the low, 0.06 .. 0.9 */
    t60 = 0.3f * mb_exp2(6.907f * s->cur[MB_DEC]) * s->c[MBC_DSCALE];
    tr = 0.06f * mb_exp2(3.907f * s->cur[MB_TONE]);
    dps = -9.9658f * mb_recip(t60 * MB_SR);
    itr = mb_recip(tr);
    for (i = 0; i < MB_NL; i++) {
        e = dps * s->loop[i];
        gdc = mb_exp2(e);
        gny = mb_exp2(e * itr);
        /* MRPH's pole at 48 kHz, squared: the same corner at 24 kHz (else the highs die faster) */
        pole = (gdc - gny) * mb_recip(gdc + gny);
        pole = pole * pole;
        s->ap[i] = pole;
        s->ag[i] = gdc * (1.0f - pole);
    }
}

/* group 3: the input filters, TONE, the diffusers, the gains */
void mb_filters(Mrb *s)
{
    float hz;
    float lz;
    float x;
    float g;
    float a;
    const float *c = s->c;
    /* input band (the spring is thin and dark), HPF above it */
    hz = c[MBC_HP];
    lz = 20.0f * mb_exp2(5.6438562f * s->cur[MB_HPF]);
    if (lz > hz) hz = lz;
    x = hz * MB_ISR;
    g = x * (MB_PI_F + MB_DIRTY_A * x * x);
    s->hpg = g * mb_recip(1.0f + g);
    x = c[MBC_LP] * MB_ISR;
    if (x > 0.4f) x = 0.4f;
    g = mb_tan(MB_PI_F * x);
    s->lpg = g;
    s->lpk = (1.0f / 0.7f) + g;
    s->lph = mb_recip(1.0f + (1.0f / 0.7f) * g + g * g);
    /* TONE: 2 kHz .. 20 kHz (here at most 9.6 kHz) */
    x = 2000.0f * mb_exp2(3.3219f * s->cur[MB_TONE]) * MB_ISR;
    if (x > 0.4f) x = 0.4f;
    g = mb_tan(MB_PI_F * x);
    s->tpg = g * mb_recip(1.0f + g);
    s->dgain = 0.7f * c[MBC_DIFF];
    /* longer tails build up more energy: part of it is made up, so VOL keeps its meaning at every decay
       (as MRPH: the cut for long tails on the send, the boost for short ones on the output). The send
       takes (L + R) through the half-band, four times the mean: x 0.5 here (MRPH's sides each take 2 x send) */
    x = mb_exp2(-0.35f * (6.907f * s->cur[MB_DEC] - 3.06f));
    if (x < 0.4f) x = 0.4f;
    if (x > 2.5f) x = 2.5f;
    a = x;
    if (a > 1.0f) a = 1.0f;
    s->sendt = 0.5f * 0.25f * a * MB_SENDHR;
    a = x;
    if (a < 1.0f) a = 1.0f;
    s->wett = 1.8f * c[MBC_OGAIN] * a * (1.0f / MB_SENDHR);
}

/* group 4 (every fourth block, or at a snap): the rotations, their slow modulation advanced steps blocks */
void mb_rot(Mrb *s, float steps)
{
    float th;
    float p;
    p = s->al0 + steps * (0.071f * MB_BT);
    if (p >= 1.0f) p -= 1.0f;
    s->al0 = p;
    th = 0.7853982f * s->c[MBC_CPL0] + 0.05f * mb_lfo(p);
    s->rc0 = fx_cos_q(th);
    s->rs0 = fx_sin_q(th);
    p = s->al1 + steps * (0.113f * MB_BT);
    if (p >= 1.0f) p -= 1.0f;
    s->al1 = p;
    th = 0.7853982f * s->c[MBC_CPL1] + 0.05f * mb_lfo(p);
    s->rc1 = fx_cos_q(th);
    s->rs1 = fx_sin_q(th);
}

/* once a block: glides, a quarter of the coefficients when a knob has moved, the line LFOs, the delays'
   slews */
void mb_control(Mrb *s)
{
    float d;
    float r;
    float t;
    float ph;
    float x;
    int i;
    int j;
    int moved;
    int ph4;

    moved = 0;
    for (i = 0; i < MB_NPARAMS; i++) {
        d = s->knob[i] - s->cur[i];
        if (d != 0.0f) {
            moved = 1;
            r = MB_FAST;
            if (i == MB_CHAR) r = MB_SLOW;
            if (i == MB_SIZE) r = MB_SLOW;
            if (s->snap) r = 1.0f;
            t = s->cur[i] + r * d;
            if (d < 1e-4f) {
                if (d > -1e-4f) t = s->knob[i];
            }
            s->cur[i] = t;
        }
    }
    if (moved) s->dirty = 7;
    if (s->snap) {
        mb_lines(s);
        mb_decay(s);
        mb_filters(s);
        mb_rot(s, 0.0f);
        s->dirty = 0;
    } else {
        ph4 = s->phase;
        if (ph4 == 0) {
            if (s->dirty & 1) {
                mb_lines(s);
                s->dirty = s->dirty & 6;
            }
        } else if (ph4 == 1) {
            if (s->dirty & 2) {
                mb_decay(s);
                s->dirty = s->dirty & 5;
            }
        } else if (ph4 == 2) {
            if (s->dirty & 4) {
                mb_filters(s);
                s->dirty = s->dirty & 3;
            }
        } else {
            mb_rot(s, 4.0f);
        }
        s->phase = (ph4 + 1) & 3;
    }

    /* the lines' modulated delays; every delay moves at most a sample a block */
    for (i = 0; i < MB_NL; i++) {
        ph = s->lfo[i] + s->lfi[i];
        if (ph >= 1.0f) ph -= 1.0f;
        s->lfo[i] = ph;
        t = s->lbase[i] + s->mod * mb_lfo(ph);
        if (t < MB_MIND) t = MB_MIND;
        if (t > s->lmax) t = s->lmax;
        x = s->ldb[i];
        if (s->snap) x = t;
        s->lda[i] = x;
        MB_SLEW(x, t);
        s->ldb[i] = x;
    }
    /* the pre-delay and early taps only while one of them moves (mb_lines sets slewing) */
    if (s->slewing) {
        moved = 0;
        x = s->prb;
        if (s->snap) x = s->pret;
        s->pra = x;
        MB_SLEW(x, s->pret);
        s->prb = x;
        if (x != s->pret) moved = 1;
        for (j = 0; j < 2 * MB_NE; j++) {
            x = s->edb[j];
            t = s->edt[j];
            if (s->snap) x = t;
            s->eda[j] = x;
            MB_SLEW(x, t);
            s->edb[j] = x;
            if (x != t) moved = 1;
        }
        /* one more block after they arrive, so that the start of the block equals the end */
        if (moved) {
            s->slewing = 2;
        } else {
            s->slewing = s->slewing - 1;
        }
    }
    if (s->snap) {
        s->send = s->sendt;
        s->wet = s->wett;
    }
    s->snap = 0;
}

/* 48 -> 24 kHz (L + R through the half-band), the send, high-pass, the character's low-pass, TONE, into the
   pre-delay ring */
void mb_input(Mrb *s, const float *a, const float *b)
{
    float *f = s->fl;
    float *w;
    uint32_t o;
    float x0;
    float y0;
    float x1;
    float y1;
#ifdef MB_DOWN_HBS
    float u0;
    float v0;
    float u1;
    float v1;
#endif
    float od;
    float e;
    float u;
    float sv;
    float si;
    float hg;
    float hs;
    float lg;
    float lk;
    float lh;
    float s1;
    float s2;
    float tg;
    float ts;
    float hp;
    float bp;
    float lp;
    int m;

    o = s->pseg + (s->t & MB_PMASK);
    w = s->mem;
    w = w + o;
    x0 = f[24];
    y0 = f[25];
    x1 = f[26];
    y1 = f[27];
#ifdef MB_DOWN_HBS
    u0 = f[28];
    v0 = f[29];
    u1 = f[30];
    v1 = f[31];
#endif
    od = f[32];
    hg = s->hpg;
    hs = f[0];
    lg = s->lpg;
    lk = s->lpk;
    lh = s->lph;
    s1 = f[1];
    s2 = f[2];
    tg = s->tpg;
    ts = f[3];
    sv = s->send;
    si = (s->sendt - sv) * (1.0f / (float)MB_HB);
    for (m = 0; m < MB_HB; m++) {
        /* the pair (x[2m], x[2m-1]) through the two branches */
        e = a[0] + b[0];
#ifdef MB_DOWN_HBS
        y0 = x0 + MB_HS00 * (e - y0);
        x0 = e;
        v0 = u0 + MB_HS01 * (y0 - v0);
        u0 = y0;
        y1 = x1 + MB_HS10 * (od - y1);
        x1 = od;
        v1 = u1 + MB_HS11 * (y1 - v1);
        u1 = y1;
        od = a[1] + b[1];
        sv = sv + si;
        u = (v0 + v1) * sv;
#else
        y0 = x0 + MB_HW0 * (e - y0);
        x0 = e;
        y1 = x1 + MB_HW1 * (od - y1);
        x1 = od;
        od = a[1] + b[1];
        sv = sv + si;
        u = (y0 + y1) * sv;
#endif
        /* TPT one-pole high-pass: e the step, lp its low-pass, the high-pass what is left */
        e = (u - hs) * hg;
        lp = e + hs;
        hs = lp + e;
        u = u - lp;
        /* TPT state-variable low-pass */
        hp = (u - lk * s1 - s2) * lh;
        bp = lg * hp + s1;
        s1 = lg * hp + bp;
        lp = lg * bp + s2;
        s2 = lg * bp + lp;
        /* TONE, one-pole */
        e = (lp - ts) * tg;
        lp = e + ts;
        ts = lp + e;
        w[0] = lp;
        a += 2;
        b += 2;
        w += 1;
    }
    f[24] = x0;
    f[25] = y0;
    f[26] = x1;
    f[27] = y1;
#ifdef MB_DOWN_HBS
    f[28] = u0;
    f[29] = v0;
    f[30] = u1;
    f[31] = v1;
#endif
    f[32] = od;
    f[0] = hs;
    f[1] = s1;
    f[2] = s2;
    f[3] = ts;
    s->send = sv;
}

/* the early taps, the pre-delay read and the four diffusers -> the tank's input (x), the early taps (el, er) */
void mb_front(Mrb *s)
{
    const float *pr;
    const float *qa;
    const float *qb;
    const float *qc;
    const float *qd;
    const float *qe;
    const float *qf;
    const float *qp;
    const float *r0;
    const float *r1;
    const float *r2;
    const float *r3;
    float *w0;
    float *w1;
    float *w2;
    float *w3;
    float *xo;
    float *lo;
    float *ro;
    float fa;
    float fb;
    float fc;
    float fd;
    float fe;
    float ff;
    float fp;
    float ia;
    float ib;
    float ic;
    float id;
    float ie;
    float iff;
    float ip;
    float ga;
    float gb;
    float gc;
    float gd;
    float ge;
    float gf;
    float g;
    float l;
    float r;
    float v;
    float d;
    float u;
    uint32_t pp;
    uint32_t pd;
    uint32_t o;
    int32_t i;
    int k;
    float *mem = s->mem;

    pp = s->t & MB_PMASK;
    pd = s->t & MB_DMASK;
    pr = mem + s->pseg;
    MB_RSET(s->eda[0], s->edb[0], i, fa, ia);
    qa = pr + ((pp - (uint32_t)i - 1u) & MB_PMASK);
    MB_RSET(s->eda[1], s->edb[1], i, fb, ib);
    qb = pr + ((pp - (uint32_t)i - 1u) & MB_PMASK);
    MB_RSET(s->eda[2], s->edb[2], i, fc, ic);
    qc = pr + ((pp - (uint32_t)i - 1u) & MB_PMASK);
    MB_RSET(s->eda[3], s->edb[3], i, fd, id);
    qd = pr + ((pp - (uint32_t)i - 1u) & MB_PMASK);
    MB_RSET(s->eda[4], s->edb[4], i, fe, ie);
    qe = pr + ((pp - (uint32_t)i - 1u) & MB_PMASK);
    MB_RSET(s->eda[5], s->edb[5], i, ff, iff);
    qf = pr + ((pp - (uint32_t)i - 1u) & MB_PMASK);
    MB_RSET(s->pra, s->prb, i, fp, ip);
    qp = pr + ((pp - (uint32_t)i - 1u) & MB_PMASK);
    ga = s->eg[0];
    gb = s->eg[1];
    gc = s->eg[2];
    gd = s->eg[3];
    ge = s->eg[4];
    gf = s->eg[5];
    o = s->dseg[0] + ((pd - (uint32_t)s->ddel[0]) & MB_DMASK);
    r0 = mem + o;
    o = s->dseg[1] + ((pd - (uint32_t)s->ddel[1]) & MB_DMASK);
    r1 = mem + o;
    o = s->dseg[2] + ((pd - (uint32_t)s->ddel[2]) & MB_DMASK);
    r2 = mem + o;
    o = s->dseg[3] + ((pd - (uint32_t)s->ddel[3]) & MB_DMASK);
    r3 = mem + o;
    o = s->dseg[0] + pd;
    w0 = mem + o;
    o = s->dseg[1] + pd;
    w1 = mem + o;
    o = s->dseg[2] + pd;
    w2 = mem + o;
    o = s->dseg[3] + pd;
    w3 = mem + o;
    g = s->dgain;
    xo = s->x;
    lo = s->el;
    ro = s->er;
    for (k = 0; k < MB_HB; k++) {
        l = ga * (qa[1] + fa * (qa[0] - qa[1])) + gb * (qb[1] + fb * (qb[0] - qb[1]))
            + gc * (qc[1] + fc * (qc[0] - qc[1]));
        r = gd * (qd[1] + fd * (qd[0] - qd[1])) + ge * (qe[1] + fe * (qe[0] - qe[1]))
            + gf * (qf[1] + ff * (qf[0] - qf[1]));
        lo[0] = l;
        ro[0] = r;
        v = qp[1] + fp * (qp[0] - qp[1]) + 0.35f * (l + r);
        d = r0[0];
        u = (v + g * d + MB_TINY) - MB_TINY;
        w0[0] = u;
        v = d - g * u;
        d = r1[0];
        u = (v + g * d + MB_TINY) - MB_TINY;
        w1[0] = u;
        v = d - g * u;
        d = r2[0];
        u = (v + g * d + MB_TINY) - MB_TINY;
        w2[0] = u;
        v = d - g * u;
        d = r3[0];
        u = (v + g * d + MB_TINY) - MB_TINY;
        w3[0] = u;
        v = d - g * u;
        xo[0] = 0.7f * v;
        fa = fa + ia;
        fb = fb + ib;
        fc = fc + ic;
        fd = fd + id;
        fe = fe + ie;
        ff = ff + iff;
        fp = fp + ip;
        qa += 1;
        qb += 1;
        qc += 1;
        qd += 1;
        qe += 1;
        qf += 1;
        qp += 1;
        r0 += 1;
        r1 += 1;
        r2 += 1;
        r3 += 1;
        w0 += 1;
        w1 += 1;
        w2 += 1;
        w3 += 1;
        xo += 1;
        lo += 1;
        ro += 1;
    }
    if (pd == 0u) {
        mb_mirror(mem + s->dseg[0], MB_DSEG);
        mb_mirror(mem + s->dseg[1], MB_DSEG);
        mb_mirror(mem + s->dseg[2], MB_DSEG);
        mb_mirror(mem + s->dseg[3], MB_DSEG);
    }
}

/* a dispersive line read for the block (the fraction ramped) */
void mb_readr(Mrb *s, int line, float *dst)
{
    const float *q;
    float fr;
    float fi;
    uint32_t o;
    int32_t i;
    int k;
    MB_RSET(s->lda[line], s->ldb[line], i, fr, fi);
    o = s->lseg[line] + (((s->t & MB_LMASK) - (uint32_t)i - 1u) & MB_LMASK);
    q = s->mem;
    q = q + o;
    for (k = 0; k < MB_HB; k += 4) {
        dst[0] = q[1] + fr * (q[0] - q[1]);
        fr = fr + fi;
        dst[1] = q[2] + fr * (q[1] - q[2]);
        fr = fr + fi;
        dst[2] = q[3] + fr * (q[2] - q[3]);
        fr = fr + fi;
        dst[3] = q[4] + fr * (q[3] - q[4]);
        fr = fr + fi;
        q += 4;
        dst += 4;
    }
}

/* the spring's dispersion on one line's block (16 samples, in place): MB_STAGES allpasses
   (a + z^-4) / (1 + a z^-4), each four first-order allpasses on the samples k = c, c+4, c+8, c+12 (c = 0..3),
   two stages a pass. st: per stage pair, per c: x1, y1, x2, y2 */
void mb_disp(float *y, float a, float *st)
{
    int j;
    int c;
    float x1;
    float y1;
    float x2;
    float y2;
    float v;
    float *p;
    float *q;
    q = st;
    for (j = 0; j < MB_STAGES; j += 2) {
        p = y;
        for (c = 0; c < MB_ST; c++) {
            x1 = q[0];
            y1 = q[1];
            x2 = q[2];
            y2 = q[3];
            v = p[0]; y1 = x1 + a * (v - y1); x1 = v; y2 = x2 + a * (y1 - y2); x2 = y1; p[0] = y2;
            v = p[4]; y1 = x1 + a * (v - y1); x1 = v; y2 = x2 + a * (y1 - y2); x2 = y1; p[4] = y2;
            v = p[8]; y1 = x1 + a * (v - y1); x1 = v; y2 = x2 + a * (y1 - y2); x2 = y1; p[8] = y2;
            v = p[12]; y1 = x1 + a * (v - y1); x1 = v; y2 = x2 + a * (y1 - y2); x2 = y1; p[12] = y2;
            q[0] = x1;
            q[1] = y1;
            q[2] = x2;
            q[3] = y2;
            q += 4;
            p += 1;
        }
    }
}

/* per sample: lines 2 and 3 read, absorption, rotations, the input, the writes, the output (+ early taps,
   x wet) */
void mb_tank(Mrb *s)
{
    float *f = s->fl;
    float *mem = s->mem;
    const float *q2;
    const float *q3;
    const float *y0;
    const float *y1;
    const float *ea;
    const float *eb;
    const float *xi;
    float *w0;
    float *w1;
    float *w2;
    float *w3;
    float *oa;
    float *ob;
    float st0;
    float st1;
    float st2;
    float st3;
    float ag0;
    float ag1;
    float ag2;
    float ag3;
    float ap0;
    float ap1;
    float ap2;
    float ap3;
    float c0;
    float s0;
    float c1;
    float s1;
    float f2;
    float f3;
    float i2;
    float i3;
    float wv;
    float wi;
    float q0;
    float qa;
    float qb;
    float qc;
    float x;
    uint32_t pl;
    uint32_t o;
    int32_t i;
    int k;

    pl = s->t & MB_LMASK;
    MB_RSET(s->lda[2], s->ldb[2], i, f2, i2);
    o = s->lseg[2] + ((pl - (uint32_t)i - 1u) & MB_LMASK);
    q2 = mem + o;
    MB_RSET(s->lda[3], s->ldb[3], i, f3, i3);
    o = s->lseg[3] + ((pl - (uint32_t)i - 1u) & MB_LMASK);
    q3 = mem + o;
    y0 = s->y[0];
    y1 = s->y[1];
    ea = s->el;
    eb = s->er;
    xi = s->x;
    o = s->lseg[0] + pl;
    w0 = mem + o;
    o = s->lseg[1] + pl;
    w1 = mem + o;
    o = s->lseg[2] + pl;
    w2 = mem + o;
    o = s->lseg[3] + pl;
    w3 = mem + o;
    oa = s->ol;
    ob = s->orr;
    st0 = f[4];
    st1 = f[5];
    st2 = f[6];
    st3 = f[7];
    ag0 = s->ag[0];
    ag1 = s->ag[1];
    ag2 = s->ag[2];
    ag3 = s->ag[3];
    ap0 = s->ap[0];
    ap1 = s->ap[1];
    ap2 = s->ap[2];
    ap3 = s->ap[3];
    c0 = s->rc0;
    s0 = s->rs0;
    c1 = s->rc1;
    s1 = s->rs1;
    wv = s->wet;
    wi = (s->wett - wv) * (1.0f / (float)MB_HB);
    for (k = 0; k < MB_HB; k++) {
        /* the dispersive lines' outputs can fall to denormals on the Mac (the allpasses ring on): the tiny
           add rounds them to 0 as the SHARC does */
        st0 = (ag0 * y0[0] + ap0 * st0 + MB_TINY) - MB_TINY;
        st1 = (ag1 * y1[0] + ap1 * st1 + MB_TINY) - MB_TINY;
        st2 = ag2 * (q2[1] + f2 * (q2[0] - q2[1])) + ap2 * st2;
        st3 = ag3 * (q3[1] + f3 * (q3[0] - q3[1])) + ap3 * st3;
        wv = wv + wi;
        /* out: lines 0 + 2 left, 1 + 3 right, 1/sqrt(2), the tank's headroom made up (1/0.7) */
        oa[0] = ((st0 + st2) * 1.01015254f + ea[0]) * wv;
        ob[0] = ((st1 + st3) * 1.01015254f + eb[0]) * wv;
        /* rotations (0,1) (2,3), then (0,2) (1,3); the input + - + - */
        q0 = c0 * st0 - s0 * st1;
        qa = s0 * st0 + c0 * st1;
        qb = c0 * st2 - s0 * st3;
        qc = s0 * st2 + c0 * st3;
        x = xi[0];
        w0[0] = c1 * q0 - s1 * qb + x;
        w2[0] = s1 * q0 + c1 * qb + x;
        w1[0] = c1 * qa - s1 * qc - x;
        w3[0] = s1 * qa + c1 * qc - x;
        f2 = f2 + i2;
        f3 = f3 + i3;
        q2 += 1;
        q3 += 1;
        y0 += 1;
        y1 += 1;
        ea += 1;
        eb += 1;
        xi += 1;
        w0 += 1;
        w1 += 1;
        w2 += 1;
        w3 += 1;
        oa += 1;
        ob += 1;
    }
    f[4] = st0;
    f[5] = st1;
    f[6] = st2;
    f[7] = st3;
    s->wet = wv;
}

/* 24 -> 48 kHz: each sample into both branches of the half-band (two allpasses each), the branches the
   output's even and odd samples */
void mb_up(const float *o, float *out, float *st)
{
    float xe;
    float ye;
    float ue;
    float we;
    float xo;
    float yo;
    float uo;
    float wo;
    float v;
    int m;
    xe = st[0];
    ye = st[1];
    ue = st[2];
    we = st[3];
    xo = st[4];
    yo = st[5];
    uo = st[6];
    wo = st[7];
    for (m = 0; m < MB_HB; m += 2) {
        v = o[0];
        ye = xe + MB_HS00 * (v - ye);
        xe = v;
        we = ue + MB_HS01 * (ye - we);
        ue = ye;
        out[0] = we;
        yo = xo + MB_HS10 * (v - yo);
        xo = v;
        wo = uo + MB_HS11 * (yo - wo);
        uo = yo;
        out[1] = wo;
        v = o[1];
        ye = xe + MB_HS00 * (v - ye);
        xe = v;
        we = ue + MB_HS01 * (ye - we);
        ue = ye;
        out[2] = we;
        yo = xo + MB_HS10 * (v - yo);
        xo = v;
        wo = uo + MB_HS11 * (yo - wo);
        uo = yo;
        out[3] = wo;
        o += 2;
        out += 4;
    }
    st[0] = xe;
    st[1] = ye;
    st[2] = ue;
    st[3] = we;
    st[4] = xo;
    st[5] = yo;
    st[6] = uo;
    st[7] = wo;
}

/* after the block at 0 is written: its copy behind the ring */
void mb_mirror(float *ring, uint32_t len)
{
    float *m;
    int k;
    m = ring + len;
    for (k = 0; k < MB_HB; k++) m[k] = ring[k];
}

/* states far below hearing set to 0 (the SHARC never makes a denormal; the Mac would): n states from f, by
   the tiny add (no branches); the block flushes a third of them in turn */
void mb_flush(float *f, int n)
{
    int i;
    for (i = 0; i < n; i++) {
        f[i] = (f[i] + MB_TINY) - MB_TINY;
    }
}

void mrb_init(Mrb *s)
{
    uint32_t used;
    int i;
    float *f;

    used = 0;
    for (i = 0; i < MB_NL; i++) {
        s->lseg[i] = used;
        used += MB_LSEG + MB_HB;
    }
    s->pseg = used;
    used += MB_PSEG + MB_HB;
    for (i = 0; i < MB_ND; i++) {
        s->dseg[i] = used;
        used += MB_DSEG + MB_HB;
        s->ddel[i] = (int32_t)(MB_DIFMS[i] * MB_SPMS);
    }
    s->lmax = (float)(MB_LSEG - MB_HB - 3);
    s->pmax = (float)(MB_PSEG - MB_HB - 3);

    f = s->mem;
    for (i = 0; i < MB_MEM; i++) f[i] = 0.0f;
    for (i = 0; i < MB_NSL * MB_STAGES * MB_ST * 2; i++) s->chain[i] = 0.0f;
    for (i = 0; i < MB_NFL; i++) s->fl[i] = 0.0f;
    for (i = 0; i < MB_NL; i++) {
        s->lfo[i] = (float)i * 0.25f;
        s->ldb[i] = MB_MIND;
    }
    s->prb = 1.0f;
    for (i = 0; i < 2 * MB_NE; i++) s->edb[i] = 1.0f;
    s->al0 = 0.0f;
    s->al1 = 0.5f;
    s->t = 0;
    s->phase = 0;
    s->dirty = 7;
    s->slewing = 2;
    s->fq = 0;
    s->send = 0.0f;
    s->wet = 0.0f;
    /* MRPH's defaults: hall, about 3.3 s */
    s->knob[MB_PRE] = 0.0f;
    s->knob[MB_DEC] = 0.5f;
    s->knob[MB_CHAR] = 0.5f;
    s->knob[MB_SIZE] = 0.5f;
    s->knob[MB_HPF] = 0.0f;
    s->knob[MB_TONE] = 0.55f;
    for (i = 0; i < MB_NPARAMS; i++) s->cur[i] = s->knob[i];
    s->snap = 1;
}

void mrb_set(Mrb *s, int param, float value)
{
    float x;
    x = value;
    FX_CLAMP(x, 0.0f, 127.0f);
    x = x * (1.0f / 127.0f);
    /* no switch: selcc gave a void function ending in a switch no return, so it ran on into the next
       function (here mrb_snap: every knob turn snapped; found 2026-11-09, notes/MR02) */
    if (param == MB_PRE) s->knob[MB_PRE] = x;
    if (param == MB_DEC) s->knob[MB_DEC] = x;
    if (param == MB_CHAR) s->knob[MB_CHAR] = x;
    if (param == MB_SIZE) s->knob[MB_SIZE] = x;
    if (param == MB_HPF) s->knob[MB_HPF] = x;
    if (param == MB_TONE) s->knob[MB_TONE] = x;
}

void mrb_snap(Mrb *s)
{
    s->snap = 1;
}

void mb_block(Mrb *s, const float *inL, const float *inR, float *outL, float *outR)
{
    float *mem = s->mem;
    uint32_t pl;
    uint32_t pp;

    mb_control(s);
    pl = s->t & MB_LMASK;
    pp = s->t & MB_PMASK;
    mb_input(s, inL, inR);
    if (pp == 0u) mb_mirror(mem + s->pseg, MB_PSEG);
    mb_front(s);
    mb_readr(s, 0, s->y[0]);
    mb_readr(s, 1, s->y[1]);
    mb_disp(s->y[0], s->disp[0], s->chain);
    mb_disp(s->y[1], s->disp[1], s->chain + MB_STAGES * MB_ST * 2);
    mb_tank(s);
    if (pl == 0u) {
        mb_mirror(mem + s->lseg[0], MB_LSEG);
        mb_mirror(mem + s->lseg[1], MB_LSEG);
        mb_mirror(mem + s->lseg[2], MB_LSEG);
        mb_mirror(mem + s->lseg[3], MB_LSEG);
    }
    mb_up(s->ol, outL, s->fl + 8);
    mb_up(s->orr, outR, s->fl + 16);
    mb_flush(s->fl + s->fq, MB_NFL / 3);
    s->fq = s->fq + MB_NFL / 3;
    if (s->fq >= MB_NFL) s->fq = 0;
    s->t = s->t + MB_HB;
}

void mrb_process(Mrb *s, const float *inL, const float *inR, float *outL, float *outR, int n)
{
    int o;
    for (o = 0; o + MB_BLK <= n; o += MB_BLK) {
        mb_block(s, inL + o, inR + o, outL + o, outR + o);
    }
}
