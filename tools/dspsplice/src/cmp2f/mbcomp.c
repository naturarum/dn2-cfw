/* SPDX-License-Identifier: GPL-2.0-or-later */
/*
 * T6.3 CMP2F: CMP2E's mbcomp.c, unchanged (notes/T6.3-cmp2f.md).
 * T6.3 CMP2E: CMP2's mbcomp.c (src/cmp2/, as on the unit) with breadcrumbs only (cmp_crumb.h: CRUMB(5)..CRUMB(14),
 * volatile stores of a stage id into the reply pages at the entry of each stage; notes/T6.3-cmp2e.md). Nothing
 * else changed: the audio is CMP2's sample for sample (tools/dspsplice/verify_cmp2e.py).
 *
 * mbcomp.c - 3-band master compressor (T3.11, G9; T6.4 plan section 4.3, T6.3 CMP0). See mbcomp.h.
 *
 * Crossover (T6.4 v2): each Linkwitz-Riley 4th-order split is a Butterworth SVF (Q 0.707) whose LP output
 * goes through a second SVF of the same frequency: LP4 = LP2 LP2, and HP4 = AP2 - LP4 (LP2^2 + HP2^2 =
 * AP2 for a Butterworth pair), with AP2 = x - 2k bp from the first SVF. The low band passes AP2 at the
 * upper crossover, so low + mid + high = AP2(lo) AP2(hi) x: flat whatever the two frequencies are.
 *
 * Each band is the stock master compressor 0xb8541d (T6.4 1.4), in the stock arithmetic where it is
 * cheap to follow (the curve, the two smoothing stages, the makeup ramp, the MIX one-poles), with our
 * log2/exp2 polynomials (degree 4 and 3: 0.0007 dB, 0.0009 dB) in place of the stock log10/pow, and levels
 * in log2 units (dB / 6.0206); both are T6.4 4.4 item 5. The changes from T3.11/v2, T6.4 4.3 (# = its table):
 *   #1  the detector filter is the stock first-order side-chain filter at its neutral setting (SCF 64,
 *       high-pass 20 Hz, the stock table's coefficient), on every detector input; the SCF knob is gone
 *       (owner, 2026-10-13: the bands do its job);
 *   #2  SCS: the caller passes the key or not (the glue compares the master's pointers);
 *   #3  ATK, REL, MIX and the ratios from the stock tables, index trunc(knob) as the stock code;
 *   #4  RAT steps 0..8: 0 = 1:1 (band off), 1..8 = the stock eight;
 *   #5  makeup: a 32-sample linear ramp to the target in each block (no 20 ms glide);
 *   #6  wet clip at +-1.0 on the summed wet;
 *   #7  MIX: the stock per-sample one-poles (0.995) on T[m] and T[127 - m]; dry = the raw input at MIX
 *       index 0, the band sum otherwise, crossfaded over 32 blocks (21 ms, smoothstep; the plan's one block
 *       clicks at the crossover frequencies, where the band sum is about -180 degrees from the raw input);
 *   #8  everything per sample;
 *   #10 the NaN/inf guard (integer exponent tests on the output and the state);
 *   #11 LSTN dropped.
 * CMP1 (2026-10-13, the CMP0 review's open points; out/T6.3/review-safety3):
 *   #12 the guard's reset is complete (mb_restart): CMP0 reset the filters and envelopes only, so a NaN or
 *       absurd value in a derived control value (crossover or detector coefficients, a band's times, ratio,
 *       threshold or makeup target, the MIX targets, the dry crossfade) survived the trip and tripped the
 *       guard again every block: the master silent until power-off (review: inject.py, stuck.py). Now every
 *       derived value is derived again from the knobs, read through NaN-safe clamps (mb_clampn);
 *   #13 a quiet return after a trip: both MIX gains from 0 (the stock one-poles fade the output in) and
 *       instant attack for MB_REC_BLOCKS (CMP0: the envelopes restarted at no gain reduction with the full
 *       makeup, a loud 50-80 ms burst at heavy settings: review burst.py); the DSP glue then seeds each band's
 *       envelopes with the gain reduction it had in the block before (tools/dspsplice/src/cmp_glue.c);
 *   #14 the scrub: one group of derived values derived again per control block (all every 8 blocks), and a
 *       smoother outside its possible range (or NaN) restarts the state as a trip does. In normal operation
 *       the scrub writes the values already there and the range test never fires: CMP0's output bit for bit.
 * CMP1b (2026-10-14, the CMP1 review; out/T6.3/review-dsp-cmp1):
 *   #15 the range test before the block also tests the knob-derived values the scrub reaches only every 8
 *       blocks: each band's time coefficients in [0, 1] with each complement exactly 1 - it (mb_targets stores c
 *       and 1.0f - c, so one corrupted word breaks the pair), 1/ratio, makeup target and threshold in range; the
 *       MIX targets; the crossover targets and coefficients. A value outside is a restart (the quiet return)
 *       before the block. CMP1 let such a value act for up to 8 blocks, at the clip ceiling for a MIX or makeup
 *       target or an attack coefficient (review p4_detect_latency.json). The detector coefficients are derived
 *       every block (two products). Deriving everything every block instead was measured at +3,500 instructions
 *       per block (out/T6.3/CMP1-dsp/probes/cmp1b-variants); these tests cost about +450;
 *   #16 an envelope below -32 log2 units (-193 dB) restarts the state (CMP1: -64; real material cannot get below
 *       -21 while every signal state is under the guard), and the signal guard is 2^12 (+72 dBFS; CMP1 2^20).
 *       Review p2_plausible_corruption.json: an envelope at -48 or a crossover state at 1e5 held the gain down
 *       for seconds at REL 127; now the first restarts before the block, the second trips the guard at the end
 *       of its block (a guard of 2^16 was tried first: in most crossover states and in the inputs 1e5 had decayed
 *       under it by the end of the block, out/T6.3/CMP1-dsp/probes/cmp1b-variants). A wrong value inside a range (a filter state at 100, an envelope at -20, a makeup target
 *       of 4 where the knob says 0) cannot be told from a real one: it acts as one, a knob-derived one until the
 *       scrub's group comes round (at most 8 blocks, 5 ms).
 * Cheap paths (T6.4 4.4): while the wet gain is exactly 0 and its target 0 (MIX 0), no exp2, no wet sum
 * (0 x wet = 0 for a finite wet, so the output is bit-identical); the detector and the curve still run
 * for the meter. A band at 1:1 with its envelopes at 0 and its makeup settled skips detection.
 *
 * NaN in comparisons (CMP1, measured in selcc's output): selcc compiles `if (a > b) X` as COMP(a, b) then
 * `IF LE` skip X, and the SHARC's COMP clears both AN and AZ when an operand is NaN, so on the DSP a NaN
 * compares as greater than everything (a > b and a >= b true, a < b and a <= b false), unlike C. The
 * control-path tests here are written to give the same answer on both: an exponent-field test for NaN/inf
 * (mb_clampn), or a range test of two ifs, (x > hi) and !(x >= lo), which a NaN fails on either machine.
 *
 * Written for selcc (notes/T3.11 11.3, 12.7; T5.2 6.2; T5.3 3.4): no calls in the sample loops (fxmath.h
 * statement macros), state in locals, flags set with if statements, one declaration per variable, every
 * table read in a statement of its own, no macro parameter named like a literal suffix, bit casts through
 * pointers.
 */
#include "mbcomp.h"
#include "cmp_crumb.h"

#define K_BW 1.4142136f      /* 1/Q for Butterworth */
#define XO_A  0.98347f       /* crossover glide per control block, tau 40 ms at 1,500 Hz */
#define MIX_C 0.995f         /* the stock MIX one-pole coefficient (state word 0x3f7eb852) */
#define LOG2_PER_DB 0.16609640f   /* 1 / 6.0205999 */
#define MUP_LOG2 4.0f        /* the stock makeup range, 20 log10(16) dB = 4 in log2 units */
#define XLO_OCT 5.0588937f   /* log2(1000/30) */
#define XHI_OCT 4.0f         /* log2(16) */
#define OFF_LIN 3.0e38f      /* thr_lin of a band at 1:1: no finite level is above it */
/* guard (#10): the master recovers from any finite block up to 1e30 but latches on 3e38, inf and NaN
   (out/T6.3/CMP0-dsp/probes/master_bound.json). CMP0 tripped at 2^30, CMP1 at 2^20, CMP1b as flashed at 2^16, CMP2 at 2^12 (+72 dBFS: the
   real bus level is not measured, but 16 tracks at full scale sum to +24 dBFS, the filter and detector states stay
   within about twice their input, and the Mac tests feed peaks of 64, +36 dBFS, without a trip): a finite absurd
   value in an input or a filter state then restarts the state rather than being taken for a peak of that size,
   which held the gain down for the release time (seconds at REL 127). A finite value below the guard still acts as
   a real peak of its size. */
#define GUARD_SIG 0x45800000u     /* exponent field of 2^12: outputs and filter states */
#define GUARD_ENV 0x42800000u     /* exponent field of 2^6: envelopes and makeup, log2 units */
/* #15: the deepest envelope the range test before the block accepts, log2 units. The deepest real one: threshold
   -60 dB (-9.97), 20:1 (1/r = 0.05), a level just under the 2^12 guard (12): (12 + 9.97) x (0.05 - 1) = -20.9 */
#define ENV_MIN -32.0f

/* x clamped to [lo, hi]; NaN and +-inf -> lo, by the exponent field, so that the answer is the same on the SHARC
   (where a NaN compares as greater than everything) and on the Mac: a corrupted knob never indexes a table
   outside it. For any other x the same value as fx_clampf. Control rate only. */
static float mb_clampn(float x, float lo, float hi)
{
    float v = x;
    uint32_t u;
    u = *(uint32_t *)&v;
    u = u & 0x7f800000u;
    if (u == 0x7f800000u)
        v = lo;
    if (v < lo)
        v = lo;
    if (v > hi)
        v = hi;
    return v;
}

float mbcomp_xlo_hz(float knob)
{
    return 30.0f * fx_exp2(fx_knob01(mb_clampn(knob, 0.0f, 127.0f)) * XLO_OCT);
}

float mbcomp_xhi_hz(float knob)
{
    return 1000.0f * fx_exp2(fx_knob01(mb_clampn(knob, 0.0f, 127.0f)) * XHI_OCT);
}

float mbcomp_ratio(const MBComp *s, float step)
{
    int32_t i;
    float r;
    i = (int32_t)(mb_clampn(step, 0.0f, 8.0f) + 0.5f);
    if (i < 1)
        return 1.0f;
    r = s->tab[MB_TAB_RINV + i - 1];
    return 1.0f / r;
}

/* ---------------- Mac only (or MB_LAW_TABLES): the stock tables from the stock laws ----------------
   The DSP image has the real tables (the glue passes them); tools/sharcvm/tests/fxcheck.py builds these for
   its bare SHARC harness with -DMB_LAW_TABLES. */
#if !defined(__ADSP21000__) || defined(MB_LAW_TABLES)
/* stock ATK law (table 0x2cfe00): tau = 0.1 + 99.9 (v/127)^2 ms (within 0.03 %) */
float mbcomp_atk_ms(float knob)
{
    float x = fx_knob01(knob);
    return 0.1f + 99.9f * x * x;
}

/* stock REL law (table 0x2d0000): tau = 10 + 1990 (v/127)^2.5 ms (within 0.6 %) */
float mbcomp_rel_ms(float knob)
{
    float x = fx_knob01(knob);
    if (x <= 0.0f) return 10.0f;
    return 10.0f + 1990.0f * fx_exp2(2.5f * fx_log2(x));
}

/* stock MIX table 0x2d0200 as a law, in dB: 0 at 127, -6 at 64 (6/64 dB per step above), -0.5625 dB
   per step from 64 down to 8 (-37.5 dB), then about -7.75 dB per step; 0 is silence */
float mbcomp_mix_db(float knob)
{
    float v = fx_clampf(knob, 0.0f, 127.0f);
    if (v >= 126.0f) return -0.1875f * (127.0f - v);
    if (v >= 64.0f) return -6.0f + 0.09375f * (v - 64.0f);
    if (v >= 8.0f) return -6.0f - 0.5625f * (64.0f - v);
    if (v >= 7.0f) return -37.5f - 8.25f * (8.0f - v);
    return -45.75f - 7.75f * (7.0f - v);
}

void mbcomp_law_tables(float *tab)
{
    static const float rat[8] = { 1.5f, 2.0f, 3.0f, 4.0f, 6.0f, 8.0f, 16.0f, 20.0f };
    int i;
    for (i = 0; i < 8; i++)
        tab[MB_TAB_RINV + i] = 1.0f / rat[i];
    for (i = 0; i < 128; i++) {
        float j = (float)(i & 63);
        /* first-order bilinear side-chain filter, no prewarp: analog corner 20 Hz x 1000^(j/63);
           LP (i < 64) T = w/(2fs + w), HP T = (w - 2fs)/(w + 2fs) (T6.4 1.4) */
        float w = 2.0f * FX_PI * 20.0f * fx_exp2(j * (9.9657843f / 63.0f));
        tab[MB_TAB_ATK + i] = fx_onepole_a(0.001f * mbcomp_atk_ms((float)i), FX_SR);
        tab[MB_TAB_REL + i] = fx_onepole_a(0.001f * mbcomp_rel_ms((float)i), FX_SR);
        tab[MB_TAB_MIX + i] = i ? fx_db2lin(mbcomp_mix_db((float)i)) : 0.0f;
        if (i < 64)
            tab[MB_TAB_SCF + i] = w / (2.0f * FX_SR + w);
        else
            tab[MB_TAB_SCF + i] = (w - 2.0f * FX_SR) / (w + 2.0f * FX_SR);
    }
}

static float mb_law_tab[MB_TAB_FLOATS];
static int mb_law_ready = 0;
#endif

/* ---------------- control ---------------- */

/* the values derived from the knobs, for the groups in dm. Bits: 1, 2, 4 = band 0, 1, 2; 8 crossovers; 32 SCS;
   64 MIX. Every knob is read through mb_clampn (CMP1), so a corrupted knob gives an in-range table index; for any
   knob value mbcomp_set can store, the results are CMP0's bit for bit. While rec_left > 0 (after a restart) a
   band's attack coefficient is 0: instant attack (#13). The caller clears s->dirty. */
static void mb_targets(MBComp *s, int32_t dm)
{
    int32_t b;
    int32_t i;
    const float *tab = s->tab;
    float v;
    float c;

    for (b = 0; b < 3; b++) {
        MbBand *bd = &s->band[b];
        const float *k = &s->knob[MB_P(b, 0)];
        if ((dm & (1 << b)) != 0) {
            v = -60.0f + 60.0f * fx_knob01(mb_clampn(k[MB_THR], 0.0f, 127.0f));   /* dB, the stock law */
            bd->thr = v * LOG2_PER_DB;
            i = (int32_t)(mb_clampn(k[MB_RAT], 0.0f, 8.0f) + 0.5f);
            bd->on = 0;
            bd->rinv = 1.0f;
            bd->thr_lin = OFF_LIN;
            if (i > 0) {
                c = tab[MB_TAB_RINV + i - 1];
                bd->rinv = c;
                bd->on = 1;
                bd->thr_lin = fx_exp2(bd->thr);
            }
            i = (int32_t)mb_clampn(k[MB_ATK], 0.0f, 127.0f);
            c = tab[MB_TAB_ATK + i];
            if (s->rec_left > 0)
                c = 0.0f;
            bd->ca = c;
            bd->ca1 = 1.0f - c;
            i = (int32_t)mb_clampn(k[MB_REL], 0.0f, 127.0f);
            c = tab[MB_TAB_REL + i];
            bd->cr = c;
            bd->cr1 = 1.0f - c;
            bd->t_mk = MUP_LOG2 * fx_knob01(mb_clampn(k[MB_MUP], 0.0f, 127.0f));
        }
    }
    if ((dm & 8) != 0) {
        s->t_lf_lo = fx_log2(mbcomp_xlo_hz(s->knob[MB_XLO]));
        s->t_lf_hi = fx_log2(mbcomp_xhi_hz(s->knob[MB_XHI]));
    }
    if ((dm & 32) != 0) {
        s->sc_ext = 0;
        if (mb_clampn(s->knob[MB_SCS], 0.0f, 1.0f) >= 0.5f)
            s->sc_ext = 1;
    }
    if ((dm & 64) != 0) {
        i = (int32_t)mb_clampn(s->knob[MB_MIX], 0.0f, 127.0f);
        c = tab[MB_TAB_MIX + i];
        s->t_mw = c;
        c = tab[MB_TAB_MIX + 127 - i];
        s->t_md = c;
        s->mix0 = 0;
        if (i == 0)
            s->mix0 = 1;
    }
}

/* the detector filter's coefficients: the stock HP form {b0, b1, a1} = {(1 - T)/2, -(1 - T)/2, T} at SCF 64 */
static void mb_detector_coefs(MBComp *s)
{
    float t;
    t = s->tab[MB_TAB_SCF + MB_SCF_NEUTRAL];
    s->sb0 = 0.5f - 0.5f * t;
    s->sp = -t;
}

/* no division or remainder: selcc divides by a constant through MRF, which the DSP hook stubs do not save
   (tools/dspsplice/build.py's register census) */
void mbcomp_set(MBComp *s, int param, float value)
{
    float hi = 127.0f;
    int32_t bit = 32;
    if (param < 0 || param >= MB_NPARAMS) return;
    if (param < MB_BAND_NP) {
        bit = 1;
        if (param == MB_RAT) hi = 8.0f;
    } else if (param < 2 * MB_BAND_NP) {
        bit = 2;
        if (param == MB_BAND_NP + MB_RAT) hi = 8.0f;
    } else if (param < 3 * MB_BAND_NP) {
        bit = 4;
        if (param == 2 * MB_BAND_NP + MB_RAT) hi = 8.0f;
    } else if (param == MB_XLO || param == MB_XHI) {
        bit = 8;
    } else if (param == MB_MIX) {
        bit = 64;
    } else if (param == MB_SCS) {
        hi = 1.0f;
    }
    s->knob[param] = mb_clampn(value, 0.0f, hi);
    s->dirty |= bit;
}

float mbcomp_gr_db(const MBComp *s, int band)
{
    if (band < 0 || band > 2) return 0.0f;
    return s->band[band].y2 * FX_DB_PER_LOG2;
}

static void mb_xover_coefs(MBComp *s)
{
    float lo = s->lf_lo;
    float hi = s->lf_hi;
    if (lo > hi) {
        float t = lo;
        lo = hi;
        hi = t;
    }
    fx_svf_coef(&s->c_lo, fx_svf_g(fx_exp2(lo)), K_BW);
    fx_svf_coef(&s->c_hi, fx_svf_g(fx_exp2(hi)), K_BW);
}

void mbcomp_snap(MBComp *s)
{
    int32_t b;
    if (s->dirty) {
        mb_targets(s, s->dirty);
        s->dirty = 0;
    }
    s->lf_lo = s->t_lf_lo;
    s->lf_hi = s->t_lf_hi;
    mb_xover_coefs(s);
    for (b = 0; b < 3; b++) {
        s->band[b].mk = s->band[b].t_mk;
        s->band[b].mk_step = 0.0f;
    }
    s->mw = s->t_mw;
    s->md = s->t_md;
    s->xw = 0.0f;
    if (s->mix0) s->xw = 1.0f;
    s->xs = 0.0f;
}

/* every filter and envelope back to rest (init, and the guard) */
static void mb_clear(MBComp *s)
{
    int32_t c;
    int32_t b;
    for (c = 0; c < 2; c++) {
        s->s1[c].ic1 = 0.0f; s->s1[c].ic2 = 0.0f;
        s->s1lp[c].ic1 = 0.0f; s->s1lp[c].ic2 = 0.0f;
        s->apl[c].ic1 = 0.0f; s->apl[c].ic2 = 0.0f;
        s->s2[c].ic1 = 0.0f; s->s2[c].ic2 = 0.0f;
        s->s2lp[c].ic1 = 0.0f; s->s2lp[c].ic2 = 0.0f;
        s->kx[c] = 0.0f;
        s->ky[c] = 0.0f;
    }
    for (b = 0; b < 3; b++) {
        MbBand *bd = &s->band[b];
        bd->y1 = 0.0f;
        bd->y2 = 0.0f;
        bd->hx[0] = 0.0f; bd->hx[1] = 0.0f;
        bd->hy[0] = 0.0f; bd->hy[1] = 0.0f;
    }
}

/* #12, #13: the whole state from the knobs again (the guard, and a smoother found outside its range): every
   filter and envelope at rest, every derived value derived again, the crossovers and the makeup on their
   targets, the dry crossfade at its end; both MIX gains at 0, so the stock one-poles fade the output in
   (cur = T + 0.995 (cur - T) per sample: -0.9 dB after 10 ms), and instant attack for MB_REC_BLOCKS control
   blocks, so the bands take the gain reduction the signal needs at once instead of starting from none with
   the full makeup. The knobs themselves are not changed (the glue sets the true values again at its next
   call; tools/dspsplice/src/cmp_glue.c). */
static void mb_restart(MBComp *s)
{
    int32_t b;
    s->rec_left = MB_REC_BLOCKS;
    mb_detector_coefs(s);
    mb_targets(s, 127);
    s->dirty = 0;
    mb_clear(s);
    s->lf_lo = s->t_lf_lo;
    s->lf_hi = s->t_lf_hi;
    mb_xover_coefs(s);
    for (b = 0; b < 3; b++) {
        s->band[b].mk = s->band[b].t_mk;
        s->band[b].mk_step = 0.0f;
    }
    s->mw = 0.0f;
    s->md = 0.0f;
    s->xw = 0.0f;
    if (s->mix0) s->xw = 1.0f;
    s->xs = 0.0f;
    s->scrub = 0;
}

void mbcomp_init(MBComp *s, const float *tab)
{
    int32_t b;
#if !defined(__ADSP21000__) || defined(MB_LAW_TABLES)
    if (!tab) {
        if (!mb_law_ready) {
            mbcomp_law_tables(mb_law_tab);
            mb_law_ready = 1;
        }
        tab = mb_law_tab;
    }
#endif
    s->tab = tab;
    mb_clear(s);
    /* the Mac tools' default (fx/stock_renders.py MB_DEFAULTS): every band -20 dB, 2:1, ATK 40 (10 ms),
       REL 44 (151 ms), no makeup; crossovers 196 Hz and 2.61 kHz; wet. The DSP glue sets every knob. */
    for (b = 0; b < 3; b++) {
        s->knob[MB_P(b, MB_THR)] = 85.0f;
        s->knob[MB_P(b, MB_ATK)] = 40.0f;
        s->knob[MB_P(b, MB_REL)] = 44.0f;
        s->knob[MB_P(b, MB_MUP)] = 0.0f;
        s->knob[MB_P(b, MB_RAT)] = 2.0f;
    }
    s->knob[MB_XLO] = 68.0f;
    s->knob[MB_XHI] = 44.0f;
    s->knob[MB_SCS] = 0.0f;
    s->knob[MB_MIX] = 127.0f;
    mb_detector_coefs(s);
    s->dirty = 127;
    s->ctl_left = 0;
    s->trips = 0;
    s->scrub = 0;
    s->rec_left = 0;
    mbcomp_snap(s);
}

/* #14, #15, #16: is a smoother outside the range it can reach in normal operation (or NaN)? The crossover glide in
   log2 Hz (the knobs give 4.9..13.97; fx_svf_g clamps 10 Hz..21.6 kHz), the MIX gains and the dry crossfade (convex
   combinations of values in [0, 1]), the envelopes (<= 0, >= ENV_MIN), the makeup (0..4, a linear ramp between
   targets); and (#15) a knob-derived value outside what mb_targets and mb_xover_coefs can store. Each range test
   is two ifs, (x > hi) and !(x >= lo): a NaN fails one of them on the SHARC (the first) and on the Mac (the
   second); a NaN also fails the complement test (x != y is true for a NaN on both). In normal operation none of
   these fires (verify_cmp.py CMP1 (h): the output is CMP0's bit for bit). */
static int32_t mb_implausible(const MBComp *s)
{
    int32_t bad = 0;
    int32_t b;
    int32_t i;
    float x;
    uint32_t u;
    const float *xp;
    x = s->lf_lo;
    if (x > 15.0f) bad = 1;
    if (!(x >= 2.0f)) bad = 1;
    x = s->lf_hi;
    if (x > 15.0f) bad = 1;
    if (!(x >= 2.0f)) bad = 1;
    x = s->mw;
    if (x > 1.0f) bad = 1;
    if (!(x >= 0.0f)) bad = 1;
    x = s->md;
    if (x > 1.0f) bad = 1;
    if (!(x >= 0.0f)) bad = 1;
    x = s->xw;
    if (x > 1.0f) bad = 1;
    if (!(x >= 0.0f)) bad = 1;
    for (b = 0; b < 3; b++) {
        const MbBand *bd = &s->band[b];
        x = bd->y1;
        if (x > 0.0f) bad = 1;
        if (!(x >= ENV_MIN)) bad = 1;
        x = bd->y2;
        if (x > 0.0f) bad = 1;
        if (!(x >= ENV_MIN)) bad = 1;
        x = bd->mk;
        if (x > 5.0f) bad = 1;
        if (!(x >= -1.0f)) bad = 1;
        /* CMP1b: the band's derived values, between two scrubs of its group: the time coefficients in [0, 1]
           and each one's complement exactly 1 - it (mb_targets stores c and 1.0f - c), 1/ratio, the makeup
           target, the threshold in their ranges */
        x = bd->ca;
        if (x > 1.0f) bad = 1;
        if (!(x >= 0.0f)) bad = 1;
        if (bd->ca1 != 1.0f - x) bad = 1;
        x = bd->cr;
        if (x > 1.0f) bad = 1;
        if (!(x >= 0.0f)) bad = 1;
        if (bd->cr1 != 1.0f - x) bad = 1;
        x = bd->rinv;
        if (x > 1.0f) bad = 1;
        if (!(x >= 0.04f)) bad = 1;
        x = bd->t_mk;
        if (x > 4.0001f) bad = 1;
        if (!(x >= 0.0f)) bad = 1;
        x = bd->thr;
        if (x > 0.001f) bad = 1;
        if (!(x >= -10.0f)) bad = 1;
    }
    /* CMP1b: the MIX targets (the stock table, [0, 1]), the crossover targets (30 Hz..1 kHz, 1..16 kHz in log2
       Hz) and coefficients ([0, 1] each) */
    x = s->t_mw;
    if (x > 1.0f) bad = 1;
    if (!(x >= 0.0f)) bad = 1;
    x = s->t_md;
    if (x > 1.0f) bad = 1;
    if (!(x >= 0.0f)) bad = 1;
    x = s->t_lf_lo;
    if (x > 10.0f) bad = 1;
    if (!(x >= 4.8f)) bad = 1;
    x = s->t_lf_hi;
    if (x > 14.0f) bad = 1;
    if (!(x >= 9.9f)) bad = 1;
    x = s->c_lo.a1;
    if (x > 1.0f) bad = 1;
    if (!(x >= 0.0f)) bad = 1;
    x = s->c_lo.a2;
    if (x > 1.0f) bad = 1;
    if (!(x >= 0.0f)) bad = 1;
    x = s->c_lo.a3;
    if (x > 1.0f) bad = 1;
    if (!(x >= 0.0f)) bad = 1;
    x = s->c_hi.a1;
    if (x > 1.0f) bad = 1;
    if (!(x >= 0.0f)) bad = 1;
    x = s->c_hi.a2;
    if (x > 1.0f) bad = 1;
    if (!(x >= 0.0f)) bad = 1;
    x = s->c_hi.a3;
    if (x > 1.0f) bad = 1;
    if (!(x >= 0.0f)) bad = 1;
    /* #16 (after the CMP1 flash, first shipped in CMP2): the 20 crossover states against the guard before the block (the exponent field; a NaN or inf is
       the largest). A wrong value in one rings out within a few samples at the upper crossover (2.6 kHz: about 4
       samples' time constant), under the guard by the end of the block, and leaves its gain reduction to the
       release (inject_cmp.py: 1e5 in apl/s2/s2lp, REL 127). The detector and key states decay at 20 Hz and are
       still over the guard at the end of the block. s1[2], s1lp[2], apl[2], s2[2], s2lp[2] are 20 consecutive
       floats (fx/test.c checks the offsets); a loop, because 20 unrolled tests do not fit the code slot */
    xp = &s->s1[0].ic1;
    for (i = 0; i < 20; i++) {
        x = xp[i];
        u = *(uint32_t *)&x;
        u = u & 0x7f800000u;
        if (u >= GUARD_SIG) bad = 1;
    }
    return bad;
}

static void mb_control(MBComp *s)
{
    const float inv = 1.0f / (float)FX_BLOCK;
    float e;
    float d;
    int32_t b;
    int32_t c;
    int32_t dm;
    int32_t ph;

    /* #13: the instant attack after a restart ends after MB_REC_BLOCKS: the bands' attack from the table again
       (a corrupted count: at most MB_REC_BLOCKS more) */
    dm = s->dirty;
    if (s->rec_left > MB_REC_BLOCKS) s->rec_left = MB_REC_BLOCKS;
    if (s->rec_left < 0) s->rec_left = 0;
    if (s->rec_left > 0) {
        s->rec_left = s->rec_left - 1;
        if (s->rec_left == 0) dm = dm | 7;
    }
    /* #14: the scrub. Group ph of 8: 0..2 a band, 3 the crossover targets, SCS and MIX, 4 the crossover
       coefficients, 5..7 none (the knob changes in dm as before); the detector coefficients every block (CMP1b;
       CMP1: group 4). The range test (#15) covers the groups between two scrubs */
    ph = s->scrub & 7;
    s->scrub = ph + 1;
    c = 0;
    if (ph == 0) dm = dm | 1;
    if (ph == 1) dm = dm | 2;
    if (ph == 2) dm = dm | 4;
    if (ph == 3) dm = dm | 104;
    if (ph == 4) c = 1;
    if (dm != 0) mb_targets(s, dm);
    s->dirty = 0;
    mb_detector_coefs(s);
    if (mb_implausible(s)) {
        mb_restart(s);
        s->trips = s->trips + 1;
    }

    /* crossover glide */
    e = s->lf_lo + (1.0f - XO_A) * (s->t_lf_lo - s->lf_lo);
    d = e - s->t_lf_lo;
    FX_ABS(d);
    if (d < 1e-5f) e = s->t_lf_lo;
    if (e != s->lf_lo) c = 1;
    s->lf_lo = e;
    e = s->lf_hi + (1.0f - XO_A) * (s->t_lf_hi - s->lf_hi);
    d = e - s->t_lf_hi;
    FX_ABS(d);
    if (d < 1e-5f) e = s->t_lf_hi;
    if (e != s->lf_hi) c = 1;
    s->lf_hi = e;
    if (c) mb_xover_coefs(s);

    /* the dry source: the crossfade position xw moves to 1 (MIX index 0) or to 0 in steps of 1/1024 per sample,
       over 32 control blocks (21 ms), and the raw input's share is smoothstep(xw) = xw^2 (3 - 2 xw): a faster
       or a linear crossfade between the raw input and the band sum (the allpassed input, about -180 degrees
       from it at each crossover) clicks at the crossover frequencies. Every xw is a multiple of 2^-15, so it
       lands on 0 or 1 exactly, where smoothstep is exactly 0 or 1. */
    e = 0.0f;
    if (s->mix0) e = 1.0f;
    d = e - s->xw;
    if (d > 0.03125f) d = 0.03125f;
    if (d < -0.03125f) d = -0.03125f;
    s->xs = d * inv;
    /* a MIX gain decaying to a target of 0 is 0 below 1e-9 (-180 dB): no denormal products on the Mac (the
       SHARC flushes them), and back on the exact paths below after 86 ms rather than the 365 ms the stock
       one-pole takes to flush */
    if (s->t_mw == 0.0f) {
        if (s->mw < 1e-9f) s->mw = 0.0f;
    }
    if (s->t_md == 0.0f) {
        if (s->md < 1e-9f) s->md = 0.0f;
    }

    /* makeup: a linear ramp over this block to the target (stock: (target - cur) / 32 per sample) */
    for (b = 0; b < 3; b++) {
        MbBand *bd = &s->band[b];
        bd->mk_step = (bd->t_mk - bd->mk) * inv;
        FX_UNDENORM(bd->y1);
        FX_UNDENORM(bd->y2);
        FX_UNDENORM(bd->hy[0]);
        FX_UNDENORM(bd->hy[1]);
    }
    for (c = 0; c < 2; c++) {
        FX_UNDENORM(s->s1[c].ic1); FX_UNDENORM(s->s1[c].ic2);
        FX_UNDENORM(s->s1lp[c].ic1); FX_UNDENORM(s->s1lp[c].ic2);
        FX_UNDENORM(s->s2[c].ic1); FX_UNDENORM(s->s2[c].ic2);
        FX_UNDENORM(s->s2lp[c].ic1); FX_UNDENORM(s->s2lp[c].ic2);
        FX_UNDENORM(s->apl[c].ic1); FX_UNDENORM(s->apl[c].ic2);
        FX_UNDENORM(s->ky[c]);
    }
}

/* T6.4 4.4 item 5: cheaper polynomials in the per-sample gain computer than fxmath.h's degree-5 pair
   (0.0001 dB, 1.2e-7). Minimax fits with the octave ends exact (log2(1+t): p(0) = 0, p(1) = 1; 2^f: p(0) = 1,
   p(1) = 2, so 2^0 = 1 exactly and a band at 1:1 without makeup passes untouched):
   MB_LOG2, degree 4: max abs error 1.13e-4 (0.0007 dB of level); MB_EXP2, degree 3: max rel error 1.03e-4
   (0.0009 dB). out/T6.3/CMP0-dsp/probes/fitpoly.py. Same forms as FX_LOG2 and FX_EXP2 otherwise. */
#define MB_LOG2(xv, ov) do { \
        if (!((xv) > 1.1754944e-38f)) { \
            (ov) = -126.0f; \
        } else { \
            uint32_t u_ = *(uint32_t *)&(xv); \
            float e_ = (float)(int32_t)(u_ & 0x7f800000u) * 1.1920929e-07f - 127.0f; \
            float t_; \
            u_ = (u_ & 0x007fffffu) | 0x3f800000u; \
            t_ = *(float *)&u_ - 1.0f; \
            (ov) = e_ + t_ * (((-0.082130730f * t_ + 0.32118902f) * t_ - 0.67778403f) * t_ + 1.4387257f); \
        } \
    } while (0)

#define MB_EXP2(xv, ov) do { \
        int32_t n_; \
        float f_; \
        float p_; \
        uint32_t u_; \
        FX_CLAMP(xv, -126.0f, 126.0f); \
        n_ = (int32_t)(xv); \
        if ((float)n_ > (xv)) n_ -= 1; \
        f_ = (xv) - (float)n_; \
        p_ = 1.0f + ((0.078267969f * f_ + 0.22630768f) * f_ + 0.69542432f) * f_; \
        u_ = *(uint32_t *)&p_ + ((uint32_t)n_ << 23); \
        (ov) = *(float *)&u_; \
    } while (0)

/* One sample of a band: level lev (linear; a variable) -> log2 -> hard-knee curve -> release stage y1
   (instant attack) -> attack stage y2 -> + makeup (used, then stepped) -> y (log2 units of the gain).
   The stock arithmetic: gc = ((lev - thr) / r + thr) - lev, y1 = min(gc, (1-cr) gc + cr y1),
   y2 = ca y2 + (1-ca) y1 (0xb85070, 0xb85111). */
#define MB_GC_STEP(lev, thl, thr, rinv, ca, ca1, cr, cr1, y1, y2, mk, mks, y) do { \
        float l2_; \
        float gc_; \
        gc_ = 0.0f; \
        if ((lev) > (thl)) { \
            MB_LOG2(lev, l2_); \
            if (l2_ > (thr)) gc_ = ((l2_ - (thr)) * (rinv) + (thr)) - l2_; \
        } \
        (y1) = (cr1) * gc_ + (cr) * (y1); \
        if (gc_ < (y1)) (y1) = gc_; \
        (y2) = (ca) * (y2) + (ca1) * (y1); \
        (y) = (y2) + (mk); \
        (mk) = (mk) + (mks); \
    } while (0)

/* the exponent field of a float variable, kept if larger than m (the guard) */
#define MB_EXPO(val, mxv) do { \
        uint32_t u_; \
        u_ = *(uint32_t *)&(val); \
        u_ = u_ & 0x7f800000u; \
        if (u_ > (mxv)) (mxv) = u_; \
    } while (0)

#ifdef FX_TEST
/* one sample of a band on a level; returns the linear gain (the tests use it) */
static float mb_gain(MbBand *bd, float level)
{
    float y1 = bd->y1;
    float y2 = bd->y2;
    float mk = bd->mk;
    float y;
    float g;
    MB_GC_STEP(level, bd->thr_lin, bd->thr, bd->rinv, bd->ca, bd->ca1, bd->cr, bd->cr1, y1, y2, mk,
               bd->mk_step, y);
    MB_EXP2(y, g);
    bd->y1 = y1;
    bd->y2 = y2;
    bd->mk = mk;
    return g;
}
#endif

#ifdef MB_FULLBAND
/* proof (a) of CMP0 (tools/dspsplice/verify_cmp.py): the mid band alone on the whole input, the way the
   stock compressor runs, with the low and high bands empty */
static int mb_fullband = 0;
void mbcomp_test_fullband(int on) { mb_fullband = on; }
#endif

/* the guard's check of the state; returns the largest exponent field per class in *sig, *env.
   Not static, on purpose (CMP1): selcc inlines a static void function called once (notes/T5.3 3.4), and inlined
   here it left mb_run without a call; selcc's code for that leaf mb_run was wrong: it kept the arguments in
   registers and, inside the crossover's hardware loop, stored R1 into the stack slot holding the copy of n, a
   value R1 held only in the first iteration, so the later loops ran the wrong count (out/T6.3/CMP1-dsp/probes/
   selcc-leaf-spill-miscompile/). With this call mb_run is compiled as in CMP0 (verify_cmp.py (b), (h)). */
void mb_state_expo(const MBComp *s, uint32_t *sig, uint32_t *env);
void mb_state_expo(const MBComp *s, uint32_t *sig, uint32_t *env)
{
    uint32_t m1 = *sig;
    uint32_t m2 = *env;
    float x;
    int32_t c;
    int32_t b;
    for (c = 0; c < 2; c++) {
        x = s->s1[c].ic1; MB_EXPO(x, m1);
        x = s->s1[c].ic2; MB_EXPO(x, m1);
        x = s->s1lp[c].ic1; MB_EXPO(x, m1);
        x = s->s1lp[c].ic2; MB_EXPO(x, m1);
        x = s->apl[c].ic1; MB_EXPO(x, m1);
        x = s->apl[c].ic2; MB_EXPO(x, m1);
        x = s->s2[c].ic1; MB_EXPO(x, m1);
        x = s->s2[c].ic2; MB_EXPO(x, m1);
        x = s->s2lp[c].ic1; MB_EXPO(x, m1);
        x = s->s2lp[c].ic2; MB_EXPO(x, m1);
        x = s->kx[c]; MB_EXPO(x, m1);
        x = s->ky[c]; MB_EXPO(x, m1);
    }
    for (b = 0; b < 3; b++) {
        const MbBand *bd = &s->band[b];
        x = bd->hx[0]; MB_EXPO(x, m1);
        x = bd->hx[1]; MB_EXPO(x, m1);
        x = bd->hy[0]; MB_EXPO(x, m1);
        x = bd->hy[1]; MB_EXPO(x, m1);
        x = bd->y1; MB_EXPO(x, m2);
        x = bd->y2; MB_EXPO(x, m2);
        x = bd->mk; MB_EXPO(x, m2);
    }
    x = s->mw; MB_EXPO(x, m1);
    x = s->md; MB_EXPO(x, m1);
    *sig = m1;
    *env = m2;
}

/* One control block (at most 32 samples): the crossover per channel, the detector levels, the gain
   computer per band, the output mix, the guard. Each stage is a loop with its state in locals. Returns 1 when
   the guard tripped: the output is then silent and the caller restarts the state (CMP1: outside this
   function's large frame, so the restart's calls do not add to the deepest stack; verify_cmp.py (d)). */
static int32_t mb_run(MBComp *s, const float *inL, const float *inR,
                      const float *keyL, const float *keyR,
                      float *outL, float *outR, int n)
{
    const float k2 = 2.0f * K_BW;
    const float sb0 = s->sb0;
    const float sp = s->sp;
    int32_t i;
    int32_t c;
    int32_t b;
    int32_t use_key;
    int32_t wet_on;
    int32_t raw_only;
    uint32_t esig;
    uint32_t eenv;
    int32_t tripped;

    /* 1. crossover: 5 SVF steps per channel */
    CRUMB(6);
#ifdef MB_FULLBAND
    if (mb_fullband) {
        for (c = 0; c < 2; c++) {
            const float *x = c ? inR : inL;
            for (i = 0; i < n; i++) {
                s->buf_lo[c][i] = 0.0f;
                s->buf_md[c][i] = x[i];
                s->buf_hi[c][i] = 0.0f;
            }
        }
    } else
#endif
    for (c = 0; c < 2; c++) {
        const float *x = c ? inR : inL;
        float *lo = s->buf_lo[c];
        float *md = s->buf_md[c];
        float *hi = s->buf_hi[c];
        const float la1 = s->c_lo.a1;
        const float la2 = s->c_lo.a2;
        const float la3 = s->c_lo.a3;
        const float ha1 = s->c_hi.a1;
        const float ha2 = s->c_hi.a2;
        const float ha3 = s->c_hi.a3;
        float p1 = s->s1[c].ic1;
        float p2 = s->s1[c].ic2;
        float q1 = s->s1lp[c].ic1;
        float q2 = s->s1lp[c].ic2;
        float u1 = s->apl[c].ic1;
        float u2 = s->apl[c].ic2;
        float v1 = s->s2[c].ic1;
        float v2 = s->s2[c].ic2;
        float w1 = s->s2lp[c].ic1;
        float w2 = s->s2lp[c].ic2;
        for (i = 0; i < n; i++) {
            float xv = x[i];
            float lp;
            float bp;
            float ap;
            float l;
            float m;
            float t;
            /* split 1 at the lower edge */
            FX_SVF(p1, p2, la1, la2, la3, xv, lp, bp);
            ap = xv - k2 * bp;                        /* AP2(lo) */
            FX_SVF(q1, q2, la1, la2, la3, lp, l, bp);  /* LP4(lo) */
            t = ap - l;                               /* HP4(lo) */
            /* low band: allpass at the upper edge */
            FX_SVF(u1, u2, ha1, ha2, ha3, l, lp, bp);
            lo[i] = l - k2 * bp;
            /* split 2 at the upper edge */
            FX_SVF(v1, v2, ha1, ha2, ha3, t, lp, bp);
            ap = t - k2 * bp;                         /* AP2(hi) */
            FX_SVF(w1, w2, ha1, ha2, ha3, lp, m, bp);  /* LP4(hi): mid */
            md[i] = m;
            hi[i] = ap - m;                           /* HP4(hi): high */
        }
        s->s1[c].ic1 = p1; s->s1[c].ic2 = p2; s->s1lp[c].ic1 = q1; s->s1lp[c].ic2 = q2;
        s->apl[c].ic1 = u1; s->apl[c].ic2 = u2;
        s->s2[c].ic1 = v1; s->s2[c].ic2 = v2; s->s2lp[c].ic1 = w1; s->s2lp[c].ic2 = w2;
    }

    /* the wet is needed unless its gain is exactly 0 and stays 0 (stock MIX 0) */
    wet_on = 0;
    if (s->mw != 0.0f) wet_on = 1;
    if (s->t_mw != 0.0f) wet_on = 1;
    use_key = 0;
    if (s->sc_ext) use_key = 1;
    if (!keyL) use_key = 0;
    if (!keyR) use_key = 0;

    /* 2. detector levels: the stock side-chain filter at SCF 64, then max(|L|, |R|) */
    if (use_key) {
        CRUMB(7);
        float *kb = s->buf_key;
        float x1l = s->kx[0];
        float y1l = s->ky[0];
        float x1r = s->kx[1];
        float y1r = s->ky[1];
        for (i = 0; i < n; i++) {
            float xl = keyL[i];
            float xr = keyR[i];
            float yl;
            float yr;
            yl = sb0 * (xl - x1l) + sp * y1l;
            yr = sb0 * (xr - x1r) + sp * y1r;
            x1l = xl;
            x1r = xr;
            y1l = yl;
            y1r = yr;
            FX_ABS(yl);
            FX_ABS(yr);
            if (yr > yl) yl = yr;
            kb[i] = yl;
        }
        s->kx[0] = x1l; s->ky[0] = y1l; s->kx[1] = x1r; s->ky[1] = y1r;
    } else {
        for (b = 0; b < 3; b++) {
            MbBand *bd = &s->band[b];
            const float *bl = s->buf_md[0];
            CRUMB(7 + b);
            const float *br = s->buf_md[1];
            float *g = s->buf_g[b];
            float x1l;
            float y1l;
            float x1r;
            float y1r;
            int32_t idle = 0;
            if (b == 0) {
                bl = s->buf_lo[0];
                br = s->buf_lo[1];
            }
            if (b == 2) {
                bl = s->buf_hi[0];
                br = s->buf_hi[1];
            }
            /* a band that takes the idle path below needs no level (the same test) */
            if (!bd->on && bd->y1 == 0.0f && bd->y2 == 0.0f && bd->mk_step == 0.0f) idle = 1;
            if (idle) {
                bd->hx[0] = 0.0f; bd->hx[1] = 0.0f; bd->hy[0] = 0.0f; bd->hy[1] = 0.0f;
                continue;
            }
            x1l = bd->hx[0];
            y1l = bd->hy[0];
            x1r = bd->hx[1];
            y1r = bd->hy[1];
            for (i = 0; i < n; i++) {
                float xl = bl[i];
                float xr = br[i];
                float yl;
                float yr;
                yl = sb0 * (xl - x1l) + sp * y1l;
                yr = sb0 * (xr - x1r) + sp * y1r;
                x1l = xl;
                x1r = xr;
                y1l = yl;
                y1r = yr;
                FX_ABS(yl);
                FX_ABS(yr);
                if (yr > yl) yl = yr;
                g[i] = yl;
            }
            bd->hx[0] = x1l; bd->hy[0] = y1l; bd->hx[1] = x1r; bd->hy[1] = y1r;
        }
    }

    /* 3. gain computer per band: level in buf_g (or the key) -> linear gain in buf_g */
    for (b = 0; b < 3; b++) {
        MbBand *bd = &s->band[b];
        const float *lv = s->buf_g[b];
        float *g = s->buf_g[b];
        const float thr = bd->thr;
        const float thl = bd->thr_lin;
        const float rinv = bd->rinv;
        const float ca = bd->ca;
        const float ca1 = bd->ca1;
        const float cr = bd->cr;
        const float cr1 = bd->cr1;
        const float mks = bd->mk_step;
        float y1 = bd->y1;
        float y2 = bd->y2;
        float mk = bd->mk;
        int32_t idle = 0;
        CRUMB(10 + b);
        if (use_key) lv = s->buf_key;
        if (!bd->on && y1 == 0.0f && y2 == 0.0f && mks == 0.0f) idle = 1;
        if (idle) {
            if (wet_on) {
                float y = mk;
                float gain;
                MB_EXP2(y, gain);
                for (i = 0; i < n; i++)
                    g[i] = gain;
            }
        } else if (wet_on) {
            for (i = 0; i < n; i++) {
                float lev = lv[i];
                float y;
                float gain;
                MB_GC_STEP(lev, thl, thr, rinv, ca, ca1, cr, cr1, y1, y2, mk, mks, y);
                MB_EXP2(y, gain);
                g[i] = gain;
            }
        } else {
            for (i = 0; i < n; i++) {
                float lev = lv[i];
                float y;
                MB_GC_STEP(lev, thl, thr, rinv, ca, ca1, cr, cr1, y1, y2, mk, mks, y);
            }
        }
        bd->y1 = y1;
        bd->y2 = y2;
        bd->mk = mk;
    }

    /* 4. output: out = Td x dry + Tw x clip(sum of the bands x gains), the stock one-poles per sample
          (each gain used, then updated); dry = xw x raw + (1 - xw) x band sum */
    CRUMB(13);
    esig = 0;
    eenv = 0;
    raw_only = 0;
    if (!wet_on && s->xw == 1.0f && s->xs == 0.0f) raw_only = 1;
    {
        const float *l0 = s->buf_lo[0];
        const float *m0 = s->buf_md[0];
        const float *h0 = s->buf_hi[0];
        const float *l1 = s->buf_lo[1];
        const float *m1 = s->buf_md[1];
        const float *h1 = s->buf_hi[1];
        const float *g0 = s->buf_g[0];
        const float *g1 = s->buf_g[1];
        const float *g2 = s->buf_g[2];
        const float tw = s->t_mw;
        const float td = s->t_md;
        float mw = s->mw;
        float md = s->md;
        if (raw_only) {
            /* stock MIX 0: out = Td(cur) x raw (+ 0 x wet), bit for bit */
            for (i = 0; i < n; i++) {
                float yl;
                float yr;
                yl = md * inL[i];
                yr = md * inR[i];
                md = td + MIX_C * (md - td);
                MB_EXPO(yl, esig);
                MB_EXPO(yr, esig);
                outL[i] = yl;
                outR[i] = yr;
            }
        } else if (md == 0.0f && td == 0.0f) {
            /* MIX 127, settled: the wet alone (+ 0 x dry) */
            for (i = 0; i < n; i++) {
                float w;
                float yl;
                float yr;
                w = (g0[i] * l0[i] + g1[i] * m0[i]) + g2[i] * h0[i];
                FX_CLAMP(w, -1.0f, 1.0f);
                yl = mw * w;
                w = (g0[i] * l1[i] + g1[i] * m1[i]) + g2[i] * h1[i];
                FX_CLAMP(w, -1.0f, 1.0f);
                yr = mw * w;
                mw = tw + MIX_C * (mw - tw);
                MB_EXPO(yl, esig);
                MB_EXPO(yr, esig);
                outL[i] = yl;
                outR[i] = yr;
            }
        } else if (s->xw == 0.0f && s->xs == 0.0f && wet_on) {
            /* MIX 1..126: dry = the band sum */
            for (i = 0; i < n; i++) {
                float a;
                float d;
                float h;
                float w;
                float yl;
                float yr;
                a = l0[i];
                d = m0[i];
                h = h0[i];
                w = (g0[i] * a + g1[i] * d) + g2[i] * h;
                FX_CLAMP(w, -1.0f, 1.0f);
                yl = md * ((a + d) + h) + mw * w;
                a = l1[i];
                d = m1[i];
                h = h1[i];
                w = (g0[i] * a + g1[i] * d) + g2[i] * h;
                FX_CLAMP(w, -1.0f, 1.0f);
                yr = md * ((a + d) + h) + mw * w;
                mw = tw + MIX_C * (mw - tw);
                md = td + MIX_C * (md - td);
                MB_EXPO(yl, esig);
                MB_EXPO(yr, esig);
                outL[i] = yl;
                outR[i] = yr;
            }
        } else {
            /* the dry source crossfading (or resting on the raw input while the wet still decays):
               dry = sw x raw + (1 - sw) x band sum, sw = smoothstep(xw) */
            float xw = s->xw;
            const float xs = s->xs;
            if (!wet_on) {
                for (i = 0; i < n; i++) {
                    s->buf_g[0][i] = 0.0f;
                    s->buf_g[1][i] = 0.0f;
                    s->buf_g[2][i] = 0.0f;
                }
            }
            for (i = 0; i < n; i++) {
                float a;
                float d;
                float h;
                float w;
                float sw;
                float dr;
                float yl;
                float yr;
                xw = xw + xs;
                sw = (xw * xw) * (3.0f - 2.0f * xw);
                a = l0[i];
                d = m0[i];
                h = h0[i];
                w = (g0[i] * a + g1[i] * d) + g2[i] * h;
                FX_CLAMP(w, -1.0f, 1.0f);
                dr = sw * inL[i] + (1.0f - sw) * ((a + d) + h);
                yl = md * dr + mw * w;
                a = l1[i];
                d = m1[i];
                h = h1[i];
                w = (g0[i] * a + g1[i] * d) + g2[i] * h;
                FX_CLAMP(w, -1.0f, 1.0f);
                dr = sw * inR[i] + (1.0f - sw) * ((a + d) + h);
                yr = md * dr + mw * w;
                mw = tw + MIX_C * (mw - tw);
                md = td + MIX_C * (md - td);
                MB_EXPO(yl, esig);
                MB_EXPO(yr, esig);
                outL[i] = yl;
                outR[i] = yr;
            }
            s->xw = xw;
        }
        s->mw = mw;
        s->md = md;
    }

    /* 5. guard (#10): NaN, inf or an absurd value in the output or the state -> a silent block and a
          restart of the whole state by the caller (#12, #13; the master latches on NaN/inf for good, T6.4 6.1) */
    CRUMB(14);
    mb_state_expo(s, &esig, &eenv);
    tripped = 0;
    if (esig >= GUARD_SIG || eenv >= GUARD_ENV) {
        for (i = 0; i < n; i++) {
            outL[i] = 0.0f;
            outR[i] = 0.0f;
        }
        tripped = 1;
    }
    return tripped;
}

void mbcomp_process(MBComp *s, const float *inL, const float *inR,
                    const float *keyL, const float *keyR,
                    float *outL, float *outR, int n)
{
    int i = 0;
    while (i < n) {
        int m;
        /* CMP1: a count outside 1..32 (only a corrupted one) runs the control block now */
        CRUMB(5);
        if (s->ctl_left <= 0 || s->ctl_left > FX_BLOCK) {
            mb_control(s);
            s->ctl_left = FX_BLOCK;
        }
        m = n - i;
        if (m > s->ctl_left) m = s->ctl_left;
        if (mb_run(s, inL + i, inR + i, keyL ? keyL + i : 0, keyR ? keyR + i : 0, outL + i, outR + i, m)) {
            CRUMB(14);
            mb_restart(s);
            s->trips = s->trips + 1;
        }
        i += m;
        s->ctl_left -= m;
    }
}
