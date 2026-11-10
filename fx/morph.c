/* SPDX-License-Identifier: GPL-2.0-or-later */
/*
 * morph.c - MRPH, the morphing reverb (morph.h).
 *
 * Written for selache's selcc as well as clang (fx/fxmath.h): no function is inlined by selcc, so the
 * per-sample work is in macros and plain loops, and calls happen once per block; no division (selcc's is
 * not always correctly rounded, so a quotient would split the SHARC build from the Mac one: reciprocals and
 * square roots are Newton steps, the same operations on both); and its known miscompiles are avoided (one
 * declaration per variable, no local initialised with a comparison, no negative number in a static float
 * table, no store indexed by a parameter after a call, no macro parameter named like a number suffix).
 *
 * The order of the float operations follows the original (morph.h) wherever the result could differ, so
 * that with MR_Q16 the Mac build can be held against it.
 *
 * Work per block, as the original, cut into loops that each do one thing (each reads only samples written
 * before it in the stream, so the order of the loops does not change the result): the input filters and the
 * pre-delay writes; the early reflection taps, one tap at a time over the block; per side, the pre-delay read
 * and the four diffusers, one diffuser at a time over the block (each reads its own line at least 87 samples
 * back); the tank lines read; the spring's allpass cascades; then per sample the absorption, the output, the
 * matrix and the writes.
 */
#include "morph.h"

#define MR_SPMS     48.0f                   /* samples per ms at 48 kHz */
#define MR_SR       48000.0f
#define MR_BT32     (32.0f / 48000.0f)      /* the time of a 32-sample block */
#define MR_ISR      (1.0f / 48000.0f)
#define MR_MIND     ((float)(MR_BLK + 2))   /* the shortest tank delay: longer than a block */
#define MR_SIZEMAX  1.6f
#define MR_EARLYMAX 100.0f                  /* ms of early reflections */
#define MR_PREMAX   120.0f                  /* ms of extra pre-delay (PRE) */
#define MR_SENDHR   0.7f                    /* headroom of the send and of the tank */
#define MR_TANKHR   0.7f
#define MR_PI_F     3.14159274f
#define MR_DIRTY_A  11.5839453f             /* 0.3736 pi^3: the one-pole's tan, "dirty" (below 8 kHz) */
#define MR_FAST_A   10.1080465f             /* 0.3260 pi^3, 0.1823 pi^5: tan, 16 Hz to 16 kHz at 48 kHz */
#define MR_FAST_B   55.7873878f

/* the three end points of CHAR, in the order of the MC_ fields (all non-negative: selcc mis-emits negative
   floats in static tables) */
static const float MR_ROOM[MC_N] = {
    8.0f, 11.5f, 15.5f, 22.0f, 9.5f, 13.5f, 18.5f, 25.5f,
    2.0f, 0.6f, 35.0f, 0.35f,
    1.0f, 1.0f, 1.0f, 1.0f, 0.0f,
    0.08f, 0.35f, 90.0f, 16000.0f, 0.9f, 0.8f
};
static const float MR_HALL[MC_N] = {
    31.0f, 47.0f, 61.0f, 83.0f, 37.0f, 53.0f, 73.0f, 97.0f,
    28.0f, 0.3f, 70.0f, 1.0f,
    1.0f, 1.0f, 1.0f, 1.0f, 0.0f,
    0.45f, 0.5f, 25.0f, 14000.0f, 1.0f, 1.0f
};
/* lines 4 to 7 keep their hall lengths: they are silent at this end */
static const float MR_SPRING[MC_N] = {
    55.0f, 63.0f, 71.0f, 79.0f, 37.0f, 53.0f, 73.0f, 97.0f,
    18.0f, 0.0f, 40.0f, 0.9f,
    0.7f, 0.7f, 0.0f, 0.0f, 0.7f,
    0.15f, 2.5f, 160.0f, 3200.0f, 0.25f, 1.45f
};

static const float MR_LFORATIO[MR_NL] = { 1.0f, 1.37f, 0.71f, 1.93f, 0.83f, 1.19f, 1.61f, 0.59f };
static const float MR_SPREAD[MR_NSL] = { 1.0f, 0.93f, 1.05f, 0.97f };
static const float MR_DIFMS[MR_ND] = { 4.77f, 3.59f, 2.73f, 1.83f };
/* early taps: time (share of the span) and level per side; the levels alternate in sign (+ - + - + -), and
   taps 2 and 5 of each side read the other side's pre-delay line */
static const float MR_ETL[MR_NE] = { 0.07f, 0.21f, 0.36f, 0.52f, 0.71f, 0.90f };
static const float MR_ETR[MR_NE] = { 0.12f, 0.27f, 0.43f, 0.60f, 0.77f, 0.95f };
static const float MR_EGL[MR_NE] = { 0.84f, 0.66f, 0.55f, 0.45f, 0.36f, 0.28f };
static const float MR_EGR[MR_NE] = { 0.80f, 0.62f, 0.58f, 0.42f, 0.33f, 0.26f };

/* ---- per-sample macros (arguments are plain variables; ring_ is the function's ring pointer) ---- */

#ifdef MR_Q16
/* the module's 16-bit lines: round to nearest, clip, back to float (native test builds only) */
static float mr_q16(float v)
{
    int32_t q;
    v *= 32768.0f;
    if (v >= 0.0f) v += 0.5f; else v -= 0.5f;
    q = (int32_t)v;
    if (q > 32767) q = 32767;
    if (q < -32768) q = -32768;
    return (float)q / 32768.0f;
}
#define MR_WR(base_, t_, val_) ring_[((t_) + (base_)) & MR_MASK] = mr_q16(val_)
#else
#define MR_WR(base_, t_, val_) ring_[((t_) + (base_)) & MR_MASK] = (val_)
#endif

/* the sample written dly_ (whole samples) before t_ */
#define MR_RD(out_, base_, t_, dly_) (out_) = ring_[((t_) + (base_) - (uint32_t)(dly_)) & MR_MASK]

/* linear interpolation at a fractional delay dl_ >= 1 */
#define MR_RDF(out_, base_, t_, dl_) do { \
        int32_t di_; \
        float fr_; \
        uint32_t ix_; \
        float ra_; \
        float rb_; \
        di_ = (int32_t)(dl_); \
        fr_ = (dl_) - (float)di_; \
        ix_ = (t_) + (base_) - (uint32_t)di_; \
        ra_ = ring_[ix_ & MR_MASK]; \
        rb_ = ring_[(ix_ - 1u) & MR_MASK]; \
        (out_) = ra_ + (rb_ - ra_) * fr_; \
    } while (0)

/* TPT one-pole: lp = (g x + st) gi, st = g (x - lp) + lp */
#define MR_OP(st_, g_, gi_, in_, lp_) do { \
        (lp_) = ((g_) * (in_) + (st_)) * (gi_); \
        (st_) = (g_) * ((in_) - (lp_)) + (lp_); \
    } while (0)

/* TPT state-variable low-pass: g, r = 1/Q, h = 1/(1 + r g + g g) */
#define MR_SVFLP(s1_, s2_, g_, r_, h_, in_, lp_) do { \
        float hp_; \
        float bp_; \
        hp_ = ((in_) - (r_) * (s1_) - (g_) * (s1_) - (s2_)) * (h_); \
        bp_ = (g_) * hp_ + (s1_); \
        (s1_) = (g_) * hp_ + bp_; \
        (lp_) = (g_) * bp_ + (s2_); \
        (s2_) = (g_) * bp_ + (lp_); \
    } while (0)

/* a 2x2 rotation of (a_, b_) by (cs_, sn_) */
#define MR_ROT(a_, b_, cs_, sn_) do { \
        float xa_; \
        float xb_; \
        xa_ = (a_); \
        xb_ = (b_); \
        (a_) = (cs_) * xa_ - (sn_) * xb_; \
        (b_) = (sn_) * xa_ + (cs_) * xb_; \
    } while (0)

/* ---- control-rate helpers (non-static: selcc inlines a static function called once, which has
   miscompiled a caller before, notes/T6.3-cmp1.md) ---- */

float mr_exp2(float x);
float mr_recip(float x);
float mr_rsqrt(float x);
float mr_lfo(float phase);
void mr_blend(Morph *s);
void mr_params(Morph *s, int n);
void mr_unrotate(Morph *s);
void mr_block(Morph *s, const float *inL, const float *inR, float *outL, float *outR, int n);
uint32_t mr_alloc(uint32_t *used, float length);

/* 2^x, about 1e-4 relative error (the original's) */
float mr_exp2(float x)
{
    int32_t i;
    float fr;
    float r;
    uint32_t u;
    if (x < -126.0f) return 0.0f;
    i = (int32_t)x;
    if (x < (float)i) i = i - 1;
    fr = x - (float)i;
    r = 1.0f + fr * (0.6960656f + fr * (0.2244943f + fr * 0.0794402f));
    u = fx_f2i(r) + ((uint32_t)i << 23);
    return fx_i2f(u);
}

/* 1/x for a positive normal x: a seed from the bits, four Newton steps (within 2 ulp) */
float mr_recip(float x)
{
    float r;
    r = fx_i2f(0x7ef311c7u - fx_f2i(x));
    r = r * (2.0f - x * r);
    r = r * (2.0f - x * r);
    r = r * (2.0f - x * r);
    r = r * (2.0f - x * r);
    return r;
}

/* 1/sqrt(x) for a positive normal x */
float mr_rsqrt(float x)
{
    float r;
    float hx;
    r = fx_i2f(0x5f3759dfu - (fx_f2i(x) >> 1));
    hx = 0.5f * x;
    r = r * (1.5f - hx * r * r);
    r = r * (1.5f - hx * r * r);
    r = r * (1.5f - hx * r * r);
    r = r * (1.5f - hx * r * r);
    return r;
}

/* smooth LFO shape for a phase in [0, 1), close to a cosine */
float mr_lfo(float phase)
{
    float x;
    if (phase < 0.5f) {
        x = 4.0f * phase - 1.0f;
    } else {
        x = 3.0f - 4.0f * phase;
    }
    return x * (1.5f - 0.5f * x * x);
}

/* CHAR -> the character, with flat spots at the three pure ones. Written as two loops on purpose: selcc
   mis-compiled the shorter form (pointers to the end points chosen in an if, then t worked out from CHAR):
   it kept CHAR in R0, loaded a table address into R0 inside the if, and then computed t from R0, so CHAR
   above 0.5 gave the hall (found by tools/sharcvm/tests/mrphcheck.py, 2026-11-09) */
void mr_blend(Morph *s)
{
    float ch;
    float t;
    float a;
    float b;
    int f;
    int upper;
    ch = s->cur[MR_CHAR];
    upper = 0;
    if (ch >= 0.5f) upper = 1;
    if (upper) {
        t = (ch - 0.5f - 0.06f) * (1.0f / 0.38f);
        FX_CLAMP(t, 0.0f, 1.0f);
        for (f = 0; f < MC_N; f++) {
            a = MR_HALL[f];
            b = MR_SPRING[f];
            s->c[f] = a + (b - a) * t;
        }
    } else {
        t = (ch - 0.06f) * (1.0f / 0.38f);
        FX_CLAMP(t, 0.0f, 1.0f);
        for (f = 0; f < MC_N; f++) {
            a = MR_ROOM[f];
            b = MR_HALL[f];
            s->c[f] = a + (b - a) * t;
        }
    }
}

/* the parameters of one block: glides, character, delays, coefficients, gains */
void mr_params(Morph *s, int n)
{
    float bt;
    float ninv;
    float slow;
    float fast;
    float l2s;
    float scale;
    float soft;
    float t60;
    float tr;
    float dps;
    float th;
    float th2;
    float sn;
    float cs;
    float nm;
    float mod;
    float loop;
    float dl;
    float a;
    float gdc;
    float gny;
    float pole;
    float pms;
    float pd;
    float span;
    float d;
    float hz;
    float x;
    float lz;
    float g;
    int i;
    int j;
    const float *c = s->c;

    if (n == MR_BLK) {
        bt = MR_BT32;
        ninv = 1.0f / (float)MR_BLK;
    } else {
        bt = (float)n * MR_ISR;
        ninv = mr_recip((float)n);
    }
    slow = 1.0f;
    fast = 1.0f;
    if (!s->snap) {
        slow = bt * 12.0f;
        if (slow > 1.0f) slow = 1.0f;
        fast = bt * 50.0f;
        if (fast > 1.0f) fast = 1.0f;
    }
    /* CHAR and SIZE glide slowly (about 80 ms): turning them bends the tail's pitch */
    s->cur[MR_CHAR] += slow * (s->knob[MR_CHAR] - s->cur[MR_CHAR]);
    s->cur[MR_SIZE] += slow * (s->knob[MR_SIZE] - s->cur[MR_SIZE]);
    s->cur[MR_DEC] += fast * (s->knob[MR_DEC] - s->cur[MR_DEC]);
    s->cur[MR_TONE] += fast * (s->knob[MR_TONE] - s->cur[MR_TONE]);
    s->cur[MR_PRE] += fast * (s->knob[MR_PRE] - s->cur[MR_PRE]);
    s->cur[MR_HPF] += fast * (s->knob[MR_HPF] - s->cur[MR_HPF]);
    mr_blend(s);

    /* size: all lengths 0.25x .. 1.6x; the pre-delay and early reflections follow more gently */
    l2s = -2.0f + 2.678f * s->cur[MR_SIZE];
    scale = mr_exp2(l2s);
    soft = mr_exp2(0.7f * l2s);

    /* decay 0.3 s .. 36 s x the character's scale; TONE: Nyquist over DC decay time, 0.06 .. 0.9 */
    t60 = 0.3f * mr_exp2(6.907f * s->cur[MR_DEC]) * c[MC_DSCALE];
    tr = 0.06f * mr_exp2(3.907f * s->cur[MR_TONE]);

    /* matrix angles at full diffusion (the knob fixed at the middle), slowly modulated */
    s->al0 += 0.071f * bt;
    if (s->al0 >= 1.0f) s->al0 -= 1.0f;
    s->al1 += 0.113f * bt;
    if (s->al1 >= 1.0f) s->al1 -= 1.0f;
    s->al2 += 0.053f * bt;
    if (s->al2 >= 1.0f) s->al2 -= 1.0f;
    for (j = 0; j < 3; j++) {
        float ph;
        float cp;
        ph = s->al0;
        cp = c[MC_CPL0];
        if (j == 1) {
            ph = s->al1;
            cp = c[MC_CPL1];
        }
        if (j == 2) {
            ph = s->al2;
            cp = c[MC_CPL2];
        }
        th = 0.7853982f * 1.0f * cp + 0.05f * mr_lfo(ph);
        th2 = th * th;
        sn = th * (1.0f - th2 * (0.16666667f - th2 * 0.00833333f));
        cs = 1.0f - th2 * (0.5f - th2 * (0.04166667f - th2 * 0.00138889f));
        /* keep the matrix exactly lossless despite the approximations */
        nm = mr_rsqrt(sn * sn + cs * cs);
        if (j == 0) {
            s->rs0 = sn * nm;
            s->rc0 = cs * nm;
        }
        if (j == 1) {
            s->rs1 = sn * nm;
            s->rc1 = cs * nm;
        }
        if (j == 2) {
            s->rs2 = sn * nm;
            s->rc2 = cs * nm;
        }
    }

    /* lines: modulated delay, absorption (one pole, from the decay times at DC and at Nyquist) */
    mod = c[MC_MODMS] * MR_SPMS;
    dps = -9.9658f * mr_recip(t60 * MR_SR);
    for (i = 0; i < MR_NL; i++) {
        float ph;
        ph = s->lfo[i] + c[MC_MODHZ] * MR_LFORATIO[i] * bt;
        if (ph >= 1.0f) ph -= 1.0f;
        s->lfo[i] = ph;
        loop = c[MC_LINE + i] * scale * MR_SPMS;
        dl = loop;
        if (i < MR_NSL) {
            /* the cascade adds its own delay: take its low-frequency group delay out of the line */
            a = c[MC_DISP] * MR_SPREAD[i];
            if (i == 0) s->a0 = a;
            if (i == 1) s->a1 = a;
            if (i == 2) s->a2 = a;
            if (i == 3) s->a3 = a;
            dl -= ((float)(MR_STAGES * MR_STRETCH) * (1.0f - a)) * mr_recip(1.0f + a);
        }
        dl += mod * mr_lfo(ph);
        if (dl < MR_MIND) dl = MR_MIND;
        if (dl > s->lmax[i]) dl = s->lmax[i];
        if (s->snap) s->ldel[i] = dl;
        s->linc[i] = (dl - s->ldel[i]) * ninv;
        gdc = mr_exp2(dps * loop);
        gny = mr_exp2(dps * loop * mr_recip(tr));
        pole = (gdc - gny) * mr_recip(gdc + gny);
        s->ap[i] = pole;
        s->ag[i] = gdc * (1.0f - pole);
    }

    /* injection and output weights; towards the spring end lines 4-7 fade out; even lines take the left
       input, odd lines the right, with the signs + - + - + + - - */
    g = c[MC_G2];
    s->g2 = g;
    s->ig[0] = MR_TANKHR;
    s->ig[1] = -MR_TANKHR;
    s->ig[2] = MR_TANKHR;
    s->ig[3] = -MR_TANKHR;
    s->ig[4] = g * MR_TANKHR;
    s->ig[5] = g * MR_TANKHR;
    s->ig[6] = -(g * MR_TANKHR);
    s->ig[7] = -(g * MR_TANKHR);
    s->onorm = mr_rsqrt(2.0f + 2.0f * g * g) * (1.0f / MR_TANKHR);

    /* pre-delay (the character's, scaled, plus PRE) and the early reflections behind it */
    x = s->cur[MR_PRE];
    pms = MR_PREMAX * x * x;
    pd = (c[MC_PRE] * soft + pms) * MR_SPMS;
    if (pd < 1.0f) pd = 1.0f;
    if (pd > s->pmax) pd = s->pmax;
    if (s->snap) s->pre = pd;
    s->pinc = (pd - s->pre) * ninv;
    span = c[MC_ESPAN] * soft;
    for (j = 0; j < MR_NE; j++) {
        d = (1.0f + MR_ETL[j] * span + pms) * MR_SPMS;
        if (d < 1.0f) d = 1.0f;
        if (d > s->pmax) d = s->pmax;
#ifdef MR_EARLY_FIXED
        d = (float)(int32_t)(d + 0.5f);
        s->edl[j] = d;
        s->eil[j] = 0.0f;
#else
        if (s->snap) s->edl[j] = d;
        s->eil[j] = (d - s->edl[j]) * ninv;
#endif
        d = (1.0f + MR_ETR[j] * span + pms) * MR_SPMS;
        if (d < 1.0f) d = 1.0f;
        if (d > s->pmax) d = s->pmax;
#ifdef MR_EARLY_FIXED
        d = (float)(int32_t)(d + 0.5f);
        s->edr[j] = d;
        s->eir[j] = 0.0f;
#else
        if (s->snap) s->edr[j] = d;
        s->eir[j] = (d - s->edr[j]) * ninv;
#endif
    }
    s->elev = c[MC_ELEV];
    s->efeed = 0.35f * c[MC_ELEV];
    s->dgain = 0.7f * 1.0f * c[MC_DIFF];
    s->early = 0;
    if (s->elev != 0.0f) s->early = 1;
    if (s->efeed != 0.0f) s->early = 1;

    /* input band (the spring is thin and dark), HPF above it, and the output tone */
    hz = c[MC_HP];
    lz = 20.0f * mr_exp2(5.6438562f * s->cur[MR_HPF]);
    if (lz > hz) hz = lz;
    x = hz * MR_ISR;
    g = x * (MR_PI_F + MR_DIRTY_A * x * x);
    s->hpg = g;
    s->hpgi = mr_recip(1.0f + g);
    x = c[MC_LP] * MR_ISR;
    if (x > 0.35f) x = 0.35f;
    lz = x * x;
    g = x * (MR_PI_F + lz * (MR_FAST_A + MR_FAST_B * lz));
    s->lpg = g;
    s->lpr = 1.0f / 0.7f;
    s->lph = mr_recip(1.0f + s->lpr * g + g * g);
    x = 2000.0f * mr_exp2(3.3219f * s->cur[MR_TONE]) * MR_ISR;
    if (x > 0.35f) x = 0.35f;
    lz = x * x;
    g = x * (MR_PI_F + lz * (MR_FAST_A + MR_FAST_B * lz));
    s->olg = g;
    s->olgi = mr_recip(1.0f + g);

    /* longer tails build up more energy: part of it is made up, so that VOL keeps its meaning at every
       decay; the cut for long tails goes on the send (it keeps the tank from clipping on the module), the
       boost for short ones on the output */
    x = mr_exp2(-0.35f * (6.907f * s->cur[MR_DEC] - 3.06f));
    if (x < 0.4f) x = 0.4f;
    if (x > 2.5f) x = 2.5f;
    a = x;
    if (a > 1.0f) a = 1.0f;
    s->sendt = 0.25f * a * MR_SENDHR;
    a = x;
    if (a < 1.0f) a = 1.0f;
    s->wett = 1.8f * c[MC_OGAIN] * a * (1.0f / MR_SENDHR);

    s->snap = 0;
}

/* back to stage k at row k, before the cascade runs again (once, when the character enters the spring
   half) */
void mr_unrotate(Morph *s)
{
    int slot;
    int st;
    int off;
    int row;
    int q;
    float *col = s->ucol;
    for (slot = 0; slot < MR_STRETCH; slot++) {
        off = s->coff[slot];
        if (off == 0) continue;
        for (st = 0; st < MR_STAGES; st++) {
            row = st + off;
            if (row >= MR_STAGES) row -= MR_STAGES;
            for (q = 0; q < MR_NSL; q++) col[st * MR_NSL + q] = s->chain[(row * MR_STRETCH + slot) * MR_NSL + q];
        }
        for (st = 0; st < MR_STAGES; st++) {
            for (q = 0; q < MR_NSL; q++) s->chain[(st * MR_STRETCH + slot) * MR_NSL + q] = col[st * MR_NSL + q];
        }
        s->coff[slot] = 0;
    }
    s->crot = 0;
}

/* a line of the ring: room for length + 4 samples, then a guard of a block. The guard matters because the
   block loops run one line over the whole block before the next line runs: a line's writes then reach up to
   a block ahead into the slots behind it, which the line allocated next may still have to read (the
   original runs its pre-delay and diffusers sample by sample and packs its lines two slots apart). */
uint32_t mr_alloc(uint32_t *used, float length)
{
    uint32_t l;
    uint32_t base;
    l = (uint32_t)(int32_t)length + 4u;
    base = *used + l + 1u;
    *used = *used + l + 2u + (uint32_t)MR_BLK;
    return base;
}

void morph_init(Morph *s)
{
    uint32_t used;
    float mm;
    float lg;
    float a;
    int i;
    float *f;

    used = 0;
    /* the longest each line can be: the longest character, 1.6x, plus the deepest modulation */
    mm = 0.45f * MR_SPMS;
    for (i = 0; i < MR_NL; i++) {
        lg = MR_ROOM[MC_LINE + i];
        a = MR_HALL[MC_LINE + i];
        if (a > lg) lg = a;
        a = MR_SPRING[MC_LINE + i];
        if (a > lg) lg = a;
        lg = lg * MR_SIZEMAX * MR_SPMS + mm;
        s->lbase[i] = mr_alloc(&used, lg);
        s->lmax[i] = (float)((int32_t)lg + 4 - 1);
    }
    lg = (MR_EARLYMAX + MR_PREMAX) * MR_SPMS;
    s->pmax = (float)((int32_t)lg + 4 - 1);
    s->pbl = mr_alloc(&used, lg);
    for (i = 0; i < MR_ND; i++) {
        s->ddel[i] = (int32_t)(MR_DIFMS[i] * MR_SPMS);
        s->dbl[i] = mr_alloc(&used, (float)s->ddel[i]);
    }
    s->pbr = mr_alloc(&used, lg);
    for (i = 0; i < MR_ND; i++) s->dbr[i] = mr_alloc(&used, (float)s->ddel[i]);
    /* used is 64,069 of MR_RING here (63,493 + 18 guards; checked by the test) */

    f = s->ring;
    for (i = 0; i < MR_RING; i++) f[i] = 0.0f;
    for (i = 0; i < MR_STAGES * MR_STRETCH * MR_NSL; i++) s->chain[i] = 0.0f;
    for (i = 0; i < MR_STRETCH; i++) s->coff[i] = 0;
    s->crot = 0;
    s->cidx = 0;
    for (i = 0; i < MR_NL; i++) {
        s->as[i] = 0.0f;
        s->lfo[i] = (float)i * 0.125f;
        s->ldel[i] = MR_MIND;
        s->linc[i] = 0.0f;
    }
    s->al0 = 0.0f;
    s->al1 = 0.33f;
    s->al2 = 2.0f * 0.33f;
    s->hpl = 0.0f;
    s->hpr = 0.0f;
    s->lp1l = 0.0f;
    s->lp2l = 0.0f;
    s->lp1r = 0.0f;
    s->lp2r = 0.0f;
    s->oll = 0.0f;
    s->olr = 0.0f;
    s->send = 0.0f;
    s->wet = 0.0f;
    s->wp = 0;
    s->pre = 1.0f;
    for (i = 0; i < MR_NE; i++) {
        s->edl[i] = 1.0f;
        s->edr[i] = 1.0f;
    }
    /* the original's defaults: hall, about 3.3 s */
    s->knob[MR_PRE] = 0.0f;
    s->knob[MR_DEC] = 0.5f;
    s->knob[MR_CHAR] = 0.5f;
    s->knob[MR_SIZE] = 0.5f;
    s->knob[MR_HPF] = 0.0f;
    s->knob[MR_TONE] = 0.55f;
    for (i = 0; i < MR_NPARAMS; i++) s->cur[i] = s->knob[i];
    /* the first block works out every parameter (mr_params) before it reads one, and snaps */
    s->snap = 1;
}

void morph_set(Morph *s, int param, float value)
{
    float x;
    x = value;
    FX_CLAMP(x, 0.0f, 127.0f);
    x = x * (1.0f / 127.0f);
    /* no switch: selcc gives a void function ending in a switch no return (it ran on into the next
       function: the moving-knob mismatch of MR01; found in MRB, 2026-11-09) */
    if (param == MR_PRE) s->knob[MR_PRE] = x;
    if (param == MR_DEC) s->knob[MR_DEC] = x;
    if (param == MR_CHAR) s->knob[MR_CHAR] = x;
    if (param == MR_SIZE) s->knob[MR_SIZE] = x;
    if (param == MR_HPF) s->knob[MR_HPF] = x;
    if (param == MR_TONE) s->knob[MR_TONE] = x;
}

#ifdef MR_TEST
void morph_set01(Morph *s, int param, float x)
{
    if (param >= 0 && param < MR_NPARAMS) s->knob[param] = x;
}
#endif

void morph_snap(Morph *s)
{
    s->snap = 1;
}

void mr_block(Morph *s, const float *inL, const float *inR, float *outL, float *outR, int n)
{
    float *ring_ = s->ring;
    uint32_t t0;
    uint32_t t;
    int k;
    int i;
    int j;
    float ninv;
    float v;
    float inc;
    float xl;
    float xr;
    float lp;
    float w;
    float dly;
    float gd;
    float g;
    float dl;
    float hpg;
    float hpgi;
    float hpl;
    float hpr;
    float lpg;
    float lpr;
    float lph;
    float l1;
    float l2;
    float r1;
    float r2;
    uint32_t b;
    uint32_t bo;
    int32_t di;
    float *yb;
    float *eb;

    mr_params(s, n);
    if (n == MR_BLK) {
        ninv = 1.0f / (float)MR_BLK;
    } else {
        ninv = mr_recip((float)n);
    }
    t0 = s->wp;

    /* 1. send, input filters, pre-delay writes; the tank input buffers hold the filtered input for now */
    v = s->send;
    inc = (s->sendt - v) * ninv;
    hpg = s->hpg;
    hpgi = s->hpgi;
    hpl = s->hpl;
    hpr = s->hpr;
    lpg = s->lpg;
    lpr = s->lpr;
    lph = s->lph;
    l1 = s->lp1l;
    l2 = s->lp2l;
    r1 = s->lp1r;
    r2 = s->lp2r;
    t = t0;
    for (k = 0; k < MR_BLK; k++) {
        float sd;
        t = t + 1u;
        v = v + inc;
        sd = 2.0f * v;
        xl = inL[k] * sd;
        xr = inR[k] * sd;
        MR_OP(hpl, hpg, hpgi, xl, lp);
        xl = xl - lp;
        MR_SVFLP(l1, l2, lpg, lpr, lph, xl, lp);
        xl = lp;
        MR_OP(hpr, hpg, hpgi, xr, lp);
        xr = xr - lp;
        MR_SVFLP(r1, r2, lpg, lpr, lph, xr, lp);
        xr = lp;
        b = s->pbl;
        MR_WR(b, t, xl);
        b = s->pbr;
        MR_WR(b, t, xr);
    }
    s->send = v;
    s->hpl = hpl;
    s->hpr = hpr;
    s->lp1l = l1;
    s->lp2l = l2;
    s->lp1r = r1;
    s->lp2r = r2;

    /* 2. early reflections, one tap at a time over the block (each reads its line at least 48 samples
       back... or written earlier in this block) */
    for (k = 0; k < MR_BLK; k++) {
        s->el[k] = 0.0f;
        s->er[k] = 0.0f;
    }
    if (s->early) {
        for (j = 0; j < MR_NE; j++) {
            /* left: taps 2 and 5 read the right side's line; odd taps are negative */
            b = s->pbl;
            bo = s->pbr;
            if (j == 2) b = bo;
            if (j == 5) b = bo;
            g = MR_EGL[j];
            if (j == 1) g = -g;
            if (j == 3) g = -g;
            if (j == 5) g = -g;
            dl = s->edl[j];
            inc = s->eil[j];
            di = (int32_t)dl;
            eb = s->el;
            t = t0;
            for (k = 0; k < MR_BLK; k++) {
                t = t + 1u;
#ifdef MR_EARLY_FIXED
                MR_RD(dly, b, t, di);
#else
                dl = dl + inc;
                MR_RDF(dly, b, t, dl);
#endif
                eb[k] = eb[k] + g * dly;
            }
            s->edl[j] = dl;
            /* right: taps 2 and 5 read the left side's line */
            b = s->pbr;
            bo = s->pbl;
            if (j == 2) b = bo;
            if (j == 5) b = bo;
            g = MR_EGR[j];
            if (j == 1) g = -g;
            if (j == 3) g = -g;
            if (j == 5) g = -g;
            dl = s->edr[j];
            inc = s->eir[j];
            di = (int32_t)dl;
            eb = s->er;
            t = t0;
            for (k = 0; k < MR_BLK; k++) {
                t = t + 1u;
#ifdef MR_EARLY_FIXED
                MR_RD(dly, b, t, di);
#else
                dl = dl + inc;
                MR_RDF(dly, b, t, dl);
#endif
                eb[k] = eb[k] + g * dly;
            }
            s->edr[j] = dl;
        }
    } else {
        /* at the spring end the early reflections are silent: only their delays move on */
        for (j = 0; j < MR_NE; j++) {
            s->edl[j] = s->edl[j] + s->eil[j] * (float)n;
            s->edr[j] = s->edr[j] + s->eir[j] * (float)n;
        }
    }

    /* 3. per side: pre-delay read plus a share of the early reflections, then the four diffusers, one at a
       time over the block (each reads its own line, at least 87 samples back) */
    dl = s->pre;
    inc = s->pinc;
    g = s->efeed;
    t = t0;
    for (k = 0; k < MR_BLK; k++) {
        float ef;
        t = t + 1u;
        dl = dl + inc;
        ef = g * (s->el[k] + s->er[k]);
        b = s->pbl;
        MR_RDF(xl, b, t, dl);
        s->tl[k] = xl + ef;
        b = s->pbr;
        MR_RDF(xr, b, t, dl);
        s->tr[k] = xr + ef;
    }
    s->pre = dl;
    gd = s->dgain;
    for (j = 0; j < MR_ND; j++) {
        di = s->ddel[j];
        b = s->dbl[j];
        bo = s->dbr[j];
        t = t0;
        for (k = 0; k < MR_BLK; k++) {
            t = t + 1u;
            v = s->tl[k];
            MR_RD(dly, b, t, di);
            w = v + gd * dly;
            MR_WR(b, t, w);
            s->tl[k] = dly - gd * w;
            v = s->tr[k];
            MR_RD(dly, bo, t, di);
            w = v + gd * dly;
            MR_WR(bo, t, w);
            s->tr[k] = dly - gd * w;
        }
    }

    /* 4. the tank lines, read for the whole block: every delay is longer than a block */
    for (i = 0; i < MR_NL; i++) {
        b = s->lbase[i];
        dl = s->ldel[i];
        inc = s->linc[i];
        yb = s->y[i];
        t = t0;
        for (k = 0; k < MR_BLK; k++) {
            t = t + 1u;
            dl = dl + inc;
            MR_RDF(v, b, t, dl);
            yb[k] = v;
        }
        s->ldel[i] = dl;
    }

    /* 5. the spring's dispersion: stretched allpass cascades on lines 0-3, run side by side */
    {
        int idx;
        int off;
        float *z;
        float *y0 = s->y[0];
        float *y1 = s->y[1];
        float *y2 = s->y[2];
        float *y3 = s->y[3];
        float a0 = s->a0;
        float a1 = s->a1;
        float a2 = s->a2;
        float a3 = s->a3;
        float *ch = s->chain;
        idx = s->cidx;
        if (a0 == 0.0f) {
            /* no dispersion: each stage is a plain delay, and one step of the cascade moves the column of
               the current slot down one stage; rotating the column does the same for a fraction of the
               work */
            for (k = 0; k < MR_BLK; k++) {
                float q;
                off = s->coff[idx] - 1;
                if (off < 0) off += MR_STAGES;
                s->coff[idx] = off;
                z = ch + (off * MR_STRETCH + idx) * MR_NSL;
                q = y0[k];
                y0[k] = z[0];
                z[0] = q;
                q = y1[k];
                y1[k] = z[1];
                z[1] = q;
                q = y2[k];
                y2[k] = z[2];
                z[2] = q;
                q = y3[k];
                y3[k] = z[3];
                z[3] = q;
                idx = idx + 1;
                if (idx >= MR_STRETCH) idx = 0;
            }
            s->crot = 1;
        } else {
            if (s->crot) mr_unrotate(s);
            for (k = 0; k < MR_BLK; k++) {
                float s0 = y0[k];
                float s1 = y1[k];
                float s2 = y2[k];
                float s3 = y3[k];
                int st;
                z = ch + idx * MR_NSL;
                for (st = 0; st < MR_STAGES; st++) {
                    float v0;
                    float v1;
                    float v2;
                    float v3;
                    v0 = s0 - a0 * z[0];
                    v1 = s1 - a1 * z[1];
                    v2 = s2 - a2 * z[2];
                    v3 = s3 - a3 * z[3];
                    s0 = a0 * v0 + z[0];
                    s1 = a1 * v1 + z[1];
                    s2 = a2 * v2 + z[2];
                    s3 = a3 * v3 + z[3];
                    z[0] = v0;
                    z[1] = v1;
                    z[2] = v2;
                    z[3] = v3;
                    z = z + MR_STRETCH * MR_NSL;
                }
                y0[k] = s0;
                y1[k] = s1;
                y2[k] = s2;
                y3[k] = s3;
                idx = idx + 1;
                if (idx >= MR_STRETCH) idx = 0;
            }
        }
        s->cidx = idx;
    }

    /* 6. per sample: absorption, output taps, the matrix, the input, the writes, the output */
    {
        float st0 = s->as[0];
        float st1 = s->as[1];
        float st2 = s->as[2];
        float st3 = s->as[3];
        float st4 = s->as[4];
        float st5 = s->as[5];
        float st6 = s->as[6];
        float st7 = s->as[7];
        float rc0 = s->rc0;
        float rs0 = s->rs0;
        float rc1 = s->rc1;
        float rs1 = s->rs1;
        float rc2 = s->rc2;
        float rs2 = s->rs2;
        float g2 = s->g2;
        float on = s->onorm;
        float el = s->elev;
        float og = s->olg;
        float ogi = s->olgi;
        float ol = s->oll;
        float orr = s->olr;
        float wv = s->wet;
        float wi = (s->wett - s->wet) * ninv;
        float ag0 = s->ag[0];
        float ap0 = s->ap[0];
        float ig0 = s->ig[0];
        uint32_t lb0 = s->lbase[0];
        const float *y0 = s->y[0];
        float ag1 = s->ag[1];
        float ap1 = s->ap[1];
        float ig1 = s->ig[1];
        uint32_t lb1 = s->lbase[1];
        const float *y1 = s->y[1];
        float ag2 = s->ag[2];
        float ap2 = s->ap[2];
        float ig2 = s->ig[2];
        uint32_t lb2 = s->lbase[2];
        const float *y2 = s->y[2];
        float ag3 = s->ag[3];
        float ap3 = s->ap[3];
        float ig3 = s->ig[3];
        uint32_t lb3 = s->lbase[3];
        const float *y3 = s->y[3];
        float ag4 = s->ag[4];
        float ap4 = s->ap[4];
        float ig4 = s->ig[4];
        uint32_t lb4 = s->lbase[4];
        const float *y4 = s->y[4];
        float ag5 = s->ag[5];
        float ap5 = s->ap[5];
        float ig5 = s->ig[5];
        uint32_t lb5 = s->lbase[5];
        const float *y5 = s->y[5];
        float ag6 = s->ag[6];
        float ap6 = s->ap[6];
        float ig6 = s->ig[6];
        uint32_t lb6 = s->lbase[6];
        const float *y6 = s->y[6];
        float ag7 = s->ag[7];
        float ap7 = s->ap[7];
        float ig7 = s->ig[7];
        uint32_t lb7 = s->lbase[7];
        const float *y7 = s->y[7];
        const float *tlp = s->tl;
        const float *trp = s->tr;
        const float *elp = s->el;
        const float *erp = s->er;
        t = t0;
        for (k = 0; k < MR_BLK; k++) {
            float q0;
            float q1;
            float q2;
            float q3;
            float q4;
            float q5;
            float q6;
            float q7;
            float lo;
            float ro;
            t = t + 1u;
            st0 = ag0 * y0[k] + ap0 * st0;
            st1 = ag1 * y1[k] + ap1 * st1;
            st2 = ag2 * y2[k] + ap2 * st2;
            st3 = ag3 * y3[k] + ap3 * st3;
            st4 = ag4 * y4[k] + ap4 * st4;
            st5 = ag5 * y5[k] + ap5 * st5;
            st6 = ag6 * y6[k] + ap6 * st6;
            st7 = ag7 * y7[k] + ap7 * st7;
            lo = (st0 + st2 + g2 * (st4 + st6)) * on;
            ro = (st1 + st3 + g2 * (st5 + st7)) * on;
            q0 = st0;
            q1 = st1;
            q2 = st2;
            q3 = st3;
            q4 = st4;
            q5 = st5;
            q6 = st6;
            q7 = st7;
            MR_ROT(q0, q1, rc0, rs0);
            MR_ROT(q2, q3, rc0, rs0);
            MR_ROT(q4, q5, rc0, rs0);
            MR_ROT(q6, q7, rc0, rs0);
            MR_ROT(q0, q2, rc1, rs1);
            MR_ROT(q1, q3, rc1, rs1);
            MR_ROT(q4, q6, rc1, rs1);
            MR_ROT(q5, q7, rc1, rs1);
            MR_ROT(q0, q4, rc2, rs2);
            MR_ROT(q1, q5, rc2, rs2);
            MR_ROT(q2, q6, rc2, rs2);
            MR_ROT(q3, q7, rc2, rs2);
            xl = tlp[k];
            xr = trp[k];
            MR_WR(lb0, t, q0 + ig0 * xl);
            MR_WR(lb1, t, q1 + ig1 * xr);
            MR_WR(lb2, t, q2 + ig2 * xl);
            MR_WR(lb3, t, q3 + ig3 * xr);
            MR_WR(lb4, t, q4 + ig4 * xl);
            MR_WR(lb5, t, q5 + ig5 * xr);
            MR_WR(lb6, t, q6 + ig6 * xl);
            MR_WR(lb7, t, q7 + ig7 * xr);
            lo = lo + elp[k] * el;
            ro = ro + erp[k] * el;
            MR_OP(ol, og, ogi, lo, lp);
            lo = lp;
            MR_OP(orr, og, ogi, ro, lp);
            ro = lp;
            wv = wv + wi;
            outL[k] = lo * wv;
            outR[k] = ro * wv;
        }
        s->as[0] = st0;
        s->as[1] = st1;
        s->as[2] = st2;
        s->as[3] = st3;
        s->as[4] = st4;
        s->as[5] = st5;
        s->as[6] = st6;
        s->as[7] = st7;
        s->oll = ol;
        s->olr = orr;
        s->wet = wv;
    }
    s->wp = t0 + (uint32_t)n;
}

void morph_process(Morph *s, const float *inL, const float *inR, float *outL, float *outR, int n)
{
    int m;
    while (n >= MR_BLK) {   /* whole 32-sample blocks (the DSP's); a remainder is not processed */
        m = MR_BLK;
        mr_block(s, inL, inR, outL, outR, m);
        inL += m;
        inR += m;
        outL += m;
        outR += m;
        n -= m;
    }
}
