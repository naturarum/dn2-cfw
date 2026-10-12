/* SPDX-License-Identifier: GPL-2.0-or-later */
/*
 * test.c - tests for the T3.11 effects (make test). The effect sources are
 * included directly so the tests can reach their static internals.
 *
 *   fxtest           run all tests, exit 1 on any failure
 *   fxtest -bench    Mac speed per effect (relative numbers only)
 *
 * Rewritten 2026-10-06 for the revised effects (notes/T3.11 section 12): the
 * compressor bands are the stock design, the tape delay has one head, AGE,
 * resonant loop filters and WID, and the chorus slot has the flanger.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <time.h>
#define FX_TEST 1
#include "tapedelay.c"
#include "mbcomp.c"
#include "flanger.c"
#include "spaceecho.c"

static int fails = 0, checks = 0;

#define CHECK(cond, ...) do { checks++; if (!(cond)) { fails++; printf("  FAIL: "); printf(__VA_ARGS__); printf("\n"); } } while (0)

static float *fbuf(long n) { float *p = calloc((size_t)n, sizeof(float)); if (!p) exit(1); return p; }

/* ---------- helpers ---------- */

static void mb_neutral(MBComp *s)
{
    int b;
    mbcomp_init(s);
    for (b = 0; b < 3; b++) {
        mbcomp_set(s, MB_P(b, MB_RAT), 0.0f);
        mbcomp_set(s, MB_P(b, MB_MUP), 0.0f);
    }
    mbcomp_set(s, MB_MIX, 127.0f);
}

/* |H(f)| in dB of a real impulse response */
static double mag_db(const float *h, long n, double f)
{
    double re = 0, im = 0, w = 2 * M_PI * f / 48000.0;
    long i;
    for (i = 0; i < n; i++) { re += h[i] * cos(w * i); im -= h[i] * sin(w * i); }
    return 10.0 * log10(re * re + im * im + 1e-300);
}

static int is_bad(float x) { return !isfinite(x) || (x != 0.0f && fpclassify(x) == FP_SUBNORMAL); }

static double rms(const float *x, long a, long b)
{
    double e = 0;
    long i;
    for (i = a; i < b; i++) e += (double)x[i] * x[i];
    return sqrt(e / (double)(b - a) + 1e-300);
}

/* phase of a sine of frequency f in y[a..b) -> delay in samples relative to sin(w n), modulo the period */
static double sine_delay(const float *y, long a, long b, double f)
{
    double re = 0, im = 0, w = 2 * M_PI * f / 48000.0, ph, per = 48000.0 / f, est;
    long n;
    for (n = a; n < b; n++) { re += y[n] * cos(w * n); im += y[n] * sin(w * n); }
    ph = atan2(-re, im);                  /* y = sin(w(n - D)): re ~ -sin(wD), im ~ cos(wD) */
    est = fmod(ph / w, per);
    if (est < 0) est += per;
    return est;
}

/* click detector: the largest second difference in [a,b) divided by the peak
   level there, relative to the same figure in a reference window [ra,rb).
   For a sine this is w^2 whatever its level, so level changes read 1.0 and a
   step or a click reads >> 1. A pitch change by a factor p reads p^2. */
static double click_ratio(const float *y, long a, long b, long ra, long rb)
{
    double m = 0, ref = 1e-12, pk = 1e-12, rpk = 1e-12;
    long i;
    for (i = a; i < b; i++) {
        double d = fabs(y[i] - 2 * y[i - 1] + y[i - 2]);
        if (d > m) m = d;
        if (fabs(y[i]) > pk) pk = fabs(y[i]);
    }
    for (i = ra; i < rb; i++) {
        double d = fabs(y[i] - 2 * y[i - 1] + y[i - 2]);
        if (d > ref) ref = d;
        if (fabs(y[i]) > rpk) rpk = fabs(y[i]);
    }
    return (m / pk) / (ref / rpk);
}

/* ============================== compressor ============================== */

/* ---------- crossover: bands sum flat, LR4 -6 dB points ---------- */

static void test_crossover(void)
{
    static const float pairs[][2] = { { 38, 90 }, { 0, 127 }, { 60, 64 }, { 90, 38 }, { 64, 64 }, { 20, 110 } };
    const long N = 65536;
    float *x = fbuf(N), *yl = fbuf(N), *yr = fbuf(N);
    MBComp *s = malloc(sizeof *s);
    size_t p;
    printf("compressor crossover: low+mid+high sums to flat magnitude; each LR4 band is -6.02 dB at its crossover\n");
    x[0] = 1.0f;
    for (p = 0; p < sizeof pairs / sizeof pairs[0]; p++) {
        double worst = 0, f;
        int k, b;
        mb_neutral(s);
        mbcomp_set(s, MB_XLO, pairs[p][0]);
        mbcomp_set(s, MB_XHI, pairs[p][1]);
        mbcomp_snap(s);
        mbcomp_process(s, x, x, NULL, NULL, yl, yr, (int)N);
        for (k = 0; k <= 120; k++) {
            f = 20.0 * pow(1000.0, k / 120.0);
            if (f > 23000) break;
            f = fabs(mag_db(yl, N, f));
            if (f > worst) worst = f;
        }
        printf("  XLO %3.0f (%6.0f Hz) XHI %3.0f (%6.0f Hz): max |sum| deviation %.4f dB\n", pairs[p][0],
               mbcomp_xover_hz(pairs[p][0]), pairs[p][1], mbcomp_xover_hz(pairs[p][1]), worst);
        CHECK(worst < 0.01, "sum not flat: %.4f dB", worst);
        if (fabs(pairs[p][0] - pairs[p][1]) >= 40) {
            float lo = pairs[p][0] < pairs[p][1] ? pairs[p][0] : pairs[p][1];
            float hi = pairs[p][0] < pairs[p][1] ? pairs[p][1] : pairs[p][0];
            double flo = mbcomp_xover_hz(lo), fhi = mbcomp_xover_hz(hi), m;
            for (b = 1; b <= 3; b++) {
                MBComp *t = malloc(sizeof *t);
                mb_neutral(t);
                mbcomp_set(t, MB_XLO, pairs[p][0]);
                mbcomp_set(t, MB_XHI, pairs[p][1]);
                mbcomp_set(t, MB_LSTN, (float)b);
                mbcomp_snap(t);
                mbcomp_process(t, x, x, NULL, NULL, yl, yr, (int)N);
                if (b == 1 || b == 2) {
                    m = mag_db(yl, N, b == 1 ? flo : fhi);
                    CHECK(fabs(m + 6.0206) < 0.05, "band %d at %.0f Hz: %.3f dB, expected -6.02", b, b == 1 ? flo : fhi, m);
                }
                if (b == 2 || b == 3) {
                    m = mag_db(yl, N, b == 2 ? flo : fhi);
                    CHECK(fabs(m + 6.0206) < 0.05, "band %d at %.0f Hz: %.3f dB, expected -6.02", b, b == 2 ? flo : fhi, m);
                }
                free(t);
            }
        }
    }
    free(s); free(x); free(yl); free(yr);
}

/* ---------- the stock laws (notes/T3.11 12.2) ---------- */

static void test_comp_laws(void)
{
    /* stock table values read from the DN2 image in the emulator (tools/sharcvm/tests/comp_model.py) */
    static const struct { int v; double atk_ms, rel_ms; } tt[] = {
        { 0, 0.1000, 10.000 }, { 1, 0.1062, 10.011 }, { 32, 6.4426, 73.419 }, { 64, 25.4708, 368.687 },
        { 96, 57.1763, 995.788 }, { 127, 99.9968, 2008.756 } };
    static const struct { int v; double db; } mt[] = {
        { 0, -400 }, { 1, -92.4 }, { 7, -45.75 }, { 8, -37.50 }, { 16, -33.00 }, { 32, -24.00 }, { 48, -15.00 },
        { 63, -6.56 }, { 64, -6.00 }, { 96, -3.00 }, { 126, -0.19 }, { 127, 0.0 } };
    static const float rats[9] = { 1, 1.5f, 2, 3, 4, 6, 8, 16, 20 };
    double worst_a = 0, worst_r = 0, worst_m = 0;
    size_t i;
    MBComp *s = malloc(sizeof *s);
    printf("compressor knob laws vs the stock tables (ATK, REL time constants; MIX gains; ratios; threshold; makeup)\n");
    for (i = 0; i < sizeof tt / sizeof tt[0]; i++) {
        double ea = fabs(mbcomp_atk_ms((float)tt[i].v) / tt[i].atk_ms - 1.0);
        double er = fabs(mbcomp_rel_ms((float)tt[i].v) / tt[i].rel_ms - 1.0);
        if (ea > worst_a) worst_a = ea;
        if (er > worst_r) worst_r = er;
    }
    for (i = 0; i < sizeof mt / sizeof mt[0]; i++) {
        double g = mt[i].v ? mbcomp_mix_db((float)mt[i].v) : 20 * log10(mb_mix_gain(0.0f) + 1e-20);
        double e = mt[i].v ? fabs(g - mt[i].db) : (g < -300 ? 0 : 99);
        if (e > worst_m) worst_m = e;
    }
    printf("  ATK law: max %.2f %% off the table; REL law: max %.2f %%; MIX law: max %.2f dB off\n",
           100 * worst_a, 100 * worst_r, worst_m);
    CHECK(worst_a < 0.005, "ATK law %.3f %% off", 100 * worst_a);
    CHECK(worst_r < 0.01, "REL law %.3f %% off", 100 * worst_r);
    CHECK(worst_m < 0.2, "MIX law %.2f dB off", worst_m);   /* the v = 1, 2 references are 6-decimal prints */
    for (i = 0; i < 9; i++) CHECK(mbcomp_ratio((float)i) == rats[i], "RAT %d -> %g", (int)i, mbcomp_ratio((float)i));
    mbcomp_init(s);
    mbcomp_set(s, MB_P(1, MB_THR), 0); mbcomp_set(s, MB_P(1, MB_MUP), 127); mbcomp_snap(s);
    CHECK(fabs(s->band[1].thr + 60.0) < 1e-3, "THR 0 -> %.3f dB", s->band[1].thr);
    CHECK(fabs(s->band[1].mk - 24.0824) < 1e-3, "MUP 127 -> %.3f dB", s->band[1].mk);
    mbcomp_set(s, MB_P(1, MB_THR), 127); mbcomp_set(s, MB_P(1, MB_MUP), 64); mbcomp_snap(s);
    CHECK(fabs(s->band[1].thr) < 1e-3, "THR 127 -> %.3f dB", s->band[1].thr);
    CHECK(fabs(s->band[1].mk - 24.0824 * 64 / 127) < 1e-3, "MUP 64 -> %.3f dB", s->band[1].mk);
    free(s);
}

/* ---------- static curve (hard knee, stock ratios) ---------- */

static void test_static_curve(void)
{
    static const float thr_k[] = { 0, 42.3f, 85, 127 };
    MBComp *s = malloc(sizeof *s);
    double worst = 0;
    size_t a;
    int r, i;
    printf("compressor static curve: one band's gain vs the hard-knee formula, 4 thresholds x 9 ratios x 93 levels\n");
    for (a = 0; a < 4; a++)
        for (r = 0; r <= 8; r++) {
            MbBand bd;
            mbcomp_init(s);
            mbcomp_set(s, MB_P(1, MB_THR), thr_k[a]);
            mbcomp_set(s, MB_P(1, MB_RAT), (float)r);
            mbcomp_snap(s);
            bd = s->band[1];
            bd.ca = 0.0f; bd.cr = 0.0f; bd.mk = 0.0f; bd.mk_i = 0.0f;
            for (i = -80; i <= 12; i++) {
                double lev = pow(10.0, i / 20.0), g, e, over = i - bd.thr;
                bd.y1 = bd.y2 = 0.0f;
                g = 20.0 * log10(mb_gain(&bd, (float)lev));
                e = over > 0 ? (1.0 / mbcomp_ratio((float)r) - 1.0) * over : 0.0;
                if (fabs(g - e) > worst) worst = fabs(g - e);
            }
        }
    printf("  max error %.4f dB\n", worst);
    CHECK(worst < 0.01, "static curve error %.4f dB", worst);

    /* the whole effect: 1 kHz in the mid band, peak in vs peak out */
    {
        const long N = 48000;
        float *xl = fbuf(N), *yl = fbuf(N), *yr = fbuf(N);
        double sworst = 0;
        static const double lev[] = { -40, -24, -12, -6, 0 };
        size_t l, b;
        printf("  whole effect, 1 kHz in the mid band (XLO 100 Hz, XHI 10 kHz), THR -30 dB, 4:1 and 20:1:\n");
        for (b = 0; b < 2; b++)
            for (l = 0; l < 5; l++) {
                double A = pow(10.0, lev[l] / 20.0), pk = 0, R, e, got;
                long n;
                mb_neutral(s);
                mbcomp_set(s, MB_P(1, MB_THR), 63.5f);
                mbcomp_set(s, MB_P(1, MB_RAT), b ? 8.0f : 4.0f);
                mbcomp_set(s, MB_P(1, MB_ATK), 0);
                mbcomp_set(s, MB_P(1, MB_REL), 100);
                mbcomp_set(s, MB_XLO, 21.3f);
                mbcomp_set(s, MB_XHI, 109.0f);
                mbcomp_snap(s);
                for (n = 0; n < N; n++) xl[n] = (float)(A * sin(2 * M_PI * 1000.0 * n / 48000.0));
                mbcomp_process(s, xl, xl, NULL, NULL, yl, yr, (int)N);
                for (n = N - 4800; n < N; n++) if (fabs(yl[n]) > pk) pk = fabs(yl[n]);
                R = mbcomp_ratio(b ? 8.0f : 4.0f);
                e = lev[l] > s->band[1].thr ? s->band[1].thr + (lev[l] - s->band[1].thr) / R : lev[l];
                got = 20 * log10(pk);
                printf("    in %5.1f dB, ratio %5.2f: out %7.2f dB, expected %7.2f\n", lev[l], R, got, e);
                if (fabs(got - e) > sworst) sworst = fabs(got - e);
            }
        CHECK(sworst < 0.3, "system static curve off by %.2f dB", sworst);
        free(xl); free(yl); free(yr);
    }
    free(s);
}

/* ---------- attack and release: the stock "smooth decoupled" smoothing ---------- */

static void test_times(void)
{
    static const float k[] = { 0, 32, 64, 96, 127 };
    MBComp *s = malloc(sizeof *s);
    size_t i;
    printf("compressor attack/release: samples to 63%% of a 28.5 dB gain-reduction step; release is the release\n"
           "  one-pole followed by the attack one-pole (stock), expected from a double-precision model of the laws\n");
    for (i = 0; i < 5; i++) {
        MbBand bd;
        double ta = (0.1 + 99.9 * pow(k[i] / 127.0, 2)) * 48.0, tr = (10 + 1990 * pow(k[i] / 127.0, 2.5)) * 48.0;
        double ca = exp(-1.0 / ta), cr = exp(-1.0 / tr), gc = (1.0 / 20.0 - 1.0) * 30.0, y1, y2;
        long n, m;
        mbcomp_init(s);
        mbcomp_set(s, MB_P(0, MB_THR), 63.5f);  /* -30 dB */
        mbcomp_set(s, MB_P(0, MB_RAT), 8);
        mbcomp_set(s, MB_P(0, MB_ATK), k[i]);
        mbcomp_set(s, MB_P(0, MB_REL), 127 - k[i]);
        mbcomp_snap(s);
        bd = s->band[0];
        bd.mk = 0; bd.mk_i = 0; bd.y1 = bd.y2 = 0;
        /* the model runs on the coefficients as the C holds them (float): near 1 a float
           coefficient is only good to about 0.6 %% of the time constant at 2 s, as in the
           stock tables (the REL 127 entry is 2009 ms); the laws are checked above */
        ca = bd.ca;
        cr = bd.cr;
        tr = (10 + 1990 * pow((127 - k[i]) / 127.0, 2.5)) * 48.0;
        for (n = 1; n < 2000000; n++) {
            mb_gain(&bd, 1.0f);
            if (bd.y2 <= (1.0 - exp(-1.0)) * gc) break;
        }
        printf("  ATK %3.0f: %8ld samples, expected %10.1f (%.2f ms)\n", k[i], n, ta, ta / 48.0);
        CHECK(fabs(n - ta) <= 1.0 + 0.003 * ta, "attack %ld vs %.1f", n, ta);
        for (n = 0; n < 4000000; n++) mb_gain(&bd, 1.0f);   /* settle */
        /* the model: y1 = cr y1 (no new reduction), y2 follows with ca */
        y1 = y2 = bd.y2;
        for (m = 1; m < 8000000; m++) {
            y1 = cr * y1;
            y2 = ca * y2 + (1 - ca) * y1;
            if (y2 >= exp(-1.0) * gc) break;
        }
        for (n = 1; n < 8000000; n++) {
            mb_gain(&bd, 1e-4f);
            if (bd.y2 >= exp(-1.0) * gc) break;
        }
        printf("  REL %3.0f (with ATK %3.0f): %8ld samples, expected %8ld (%.1f ms; the law's tau %.1f ms, the float coefficient's %.1f ms)\n",
               127 - k[i], k[i], n, m, m / 48.0, tr / 48.0, -1.0 / log((double)bd.cr) / 48.0);
        CHECK(labs(n - m) <= 1 + (long)(0.0005 * m), "release %ld vs %ld", n, m);
        CHECK(fabs(-1.0 / log((double)bd.cr) / tr - 1.0) < 0.008, "REL coefficient %.4f vs law", -1.0 / log((double)bd.cr) / tr);
    }
    free(s);
}

/* ---------- sidechain: one key for all bands ---------- */

static void test_sidechain(void)
{
    const long N = 48000 * 2;
    float *x = fbuf(N), *k = fbuf(N), *yl = fbuf(N), *yr = fbuf(N);
    MBComp *s = malloc(sizeof *s);
    double grk = 0, grq = 0, grm = 0;
    long i;
    printf("compressor sidechain (SCS 1): every band detects the key; low band at 20:1, mid and high at 1:1\n");
    for (i = 0; i < N; i++) {
        x[i] = (float)(0.1 * sin(2 * M_PI * 80.0 * i / 48000.0) + 0.1 * sin(2 * M_PI * 1000.0 * i / 48000.0));
        k[i] = (i >= 48000 && i < 72000) ? (float)(0.8 * sin(2 * M_PI * 60.0 * i / 48000.0)) : 0.0f;
    }
    mbcomp_init(s);
    mbcomp_set(s, MB_P(0, MB_THR), 85);   /* -20 dB */
    mbcomp_set(s, MB_P(0, MB_RAT), 8);
    mbcomp_set(s, MB_P(1, MB_RAT), 0);
    mbcomp_set(s, MB_P(2, MB_RAT), 0);
    mbcomp_set(s, MB_SCS, 1);
    mbcomp_snap(s);
    for (i = 0; i < N; i += 32) {
        mbcomp_process(s, x + i, x + i, k + i, k + i, yl + i, yr + i, 32);
        if (i == 47968) grq = mbcomp_gr_db(s, 0);
        if (i == 71968) { grk = mbcomp_gr_db(s, 0); grm = mbcomp_gr_db(s, 1); }
    }
    printf("  low-band gain reduction: %.1f dB before the key, %.1f dB during it; mid %.1f dB\n", grq, grk, grm);
    CHECK(grq > -0.5 && grk < -10.0 && grm == 0.0, "ducking: %.1f / %.1f dB, mid %.1f", grq, grk, grm);
    free(x); free(k); free(yl); free(yr); free(s);
}

/* ============================== tape delay ============================== */

static void td_transparent(TapeDelay *s)
{
    /* the record path without filters (g = 0 high-pass passes all; huge-g low-pass passes all) */
    fx_svf_coef(&s->hpc, 0.0f, 1.4142136f);
    fx_svf_coef(&s->lpc, 1e7f, 1.4142136f);
}

static void td_clean(TapeDelay *s, float time, float bpm)
{
    tapedelay_init(s, 7u);
    tapedelay_set_bpm(s, bpm);
    tapedelay_set(s, TD_TIME, time);
    tapedelay_set(s, TD_FDBK, 0);
    tapedelay_set(s, TD_WOW, 0);
    tapedelay_set(s, TD_FLUT, 0);
    tapedelay_set(s, TD_AGE, 0);
    tapedelay_set(s, TD_DRIV, 0);
    tapedelay_snap(s);
    td_transparent(s);
}

static void test_delay_time(void)
{
    static const struct { float time, bpm; } cases[] = {
        { 31, 120 }, { 15, 120 }, { 127, 30 }, { 0, 300 }, { 47.5f, 120 }, { 31, 133 }, { 63, 97 }
    };
    TapeDelay *s = malloc(sizeof *s);
    const long N = 400000;
    float *x = fbuf(N), *yl = fbuf(N), *yr = fbuf(N);
    size_t c;
    long n;
    printf("tape delay time: impulse peak (integer delays) and 500 Hz phase (fractional), transparent record path\n");
    for (c = 0; c < sizeof cases / sizeof cases[0]; c++) {
        double D = tapedelay_time_samples(cases[c].time, cases[c].bpm), pk = 0, w, est, err;
        long at = -1, M = (long)D + 20000;
        if (M > N) M = N;
        td_clean(s, cases[c].time, cases[c].bpm);
        memset(x, 0, sizeof(float) * (size_t)N);
        x[0] = 1.0f;
        tapedelay_process(s, x, x, yl, yr, (int)M);
        for (n = 0; n < M; n++) if (fabs(yl[n]) > pk) { pk = fabs(yl[n]); at = n; }
        td_clean(s, cases[c].time, cases[c].bpm);
        w = 2 * M_PI * 500.0 / 48000.0;
        for (n = 0; n < M; n++) x[n] = (float)(0.01 * sin(w * n));
        tapedelay_process(s, x, x, yl, yr, (int)M);
        est = sine_delay(yl, M - 9600, M, 500.0);
        err = fabs(est - fmod(D, 96.0));
        if (err > 48) err = 96 - err;
        printf("  TIME %5.1f @ %3.0f BPM: expected %10.3f samples (%.4f s); impulse peak at %ld; phase error %.4f samples\n",
               cases[c].time, cases[c].bpm, D, D / 48000.0, at, err);
        if (fabs(D - floor(D + 0.5)) < 1e-3) CHECK(at == (long)floor(D + 0.5), "impulse at %ld, expected %.0f", at, D);
        else CHECK(labs(at - (long)D) <= 1, "impulse at %ld, expected %.3f", at, D);
        CHECK(err < 0.02, "fractional delay error %.4f samples", err);
    }
    free(s); free(x); free(yl); free(yr);
}

/* ---------- WID: mono, stereo, ping-pong ---------- */

static void test_width(void)
{
    TapeDelay *s = malloc(sizeof *s);
    const long N = 80000;
    float *xl = fbuf(N), *xr = fbuf(N), *yl = fbuf(N), *yr = fbuf(N);
    const long D = 24000;   /* TIME 31 at 120 BPM */
    int ok;
    printf("tape delay WID: a left-only impulse, FDBK 50 %% (0.55 x 0.909), transparent record path\n");
    xl[0] = 0.01f;
    /* WID 0: mono, both sides 0.5 */
    td_clean(s, 31, 120); tapedelay_set(s, TD_WID, 0); tapedelay_set(s, TD_FDBK, 57.73f); tapedelay_snap(s); td_transparent(s);
    tapedelay_process(s, xl, xr, yl, yr, (int)N);
    ok = fabs(yl[D] - 0.005) < 1e-6 && fabs(yr[D] - 0.005) < 1e-6 && fabs(yl[2 * D] - 0.0025) < 1e-6 && fabs(yr[2 * D] - 0.0025) < 1e-6;
    printf("  WID 0 (mono): echo 1 L %.5f R %.5f, echo 2 L %.5f R %.5f: %s\n", yl[D], yr[D], yl[2 * D], yr[2 * D], ok ? "centred" : "WRONG");
    CHECK(ok, "mono width");
    /* WID 64: stereo as it comes */
    td_clean(s, 31, 120); tapedelay_set(s, TD_WID, 64); tapedelay_set(s, TD_FDBK, 57.73f); tapedelay_snap(s); td_transparent(s);
    tapedelay_process(s, xl, xr, yl, yr, (int)N);
    ok = fabs(yl[D] - 0.01) < 1e-6 && yr[D] == 0.0f && fabs(yl[2 * D] - 0.005) < 1e-6 && yr[2 * D] == 0.0f;
    printf("  WID 64 (stereo): echo 1 L %.5f R %.5f, echo 2 L %.5f R %.5f: %s\n", yl[D], yr[D], yl[2 * D], yr[2 * D], ok ? "left only" : "WRONG");
    CHECK(ok, "stereo width");
    /* WID 127: ping-pong, input to mono on the left, then alternating */
    td_clean(s, 31, 120); tapedelay_set(s, TD_WID, 127); tapedelay_set(s, TD_FDBK, 57.73f); tapedelay_snap(s); td_transparent(s);
    tapedelay_process(s, xl, xr, yl, yr, (int)N);
    ok = fabs(yl[D] - 0.005) < 1e-6 && fabs(yr[D]) < 1e-7 && fabs(yl[2 * D]) < 1e-7 && fabs(yr[2 * D] - 0.0025) < 1e-6
         && fabs(yl[3 * D] - 0.00125) < 1e-6 && fabs(yr[3 * D]) < 1e-7;
    printf("  WID 127 (ping-pong): L %.5f %.5f %.5f, R %.5f %.5f %.5f: %s\n", yl[D], yl[2 * D], yl[3 * D],
           yr[D], yr[2 * D], yr[3 * D], ok ? "L, R, L" : "WRONG");
    CHECK(ok, "ping-pong width");
    free(s); free(xl); free(xr); free(yl); free(yr);
}

/* ---------- resonant loop filters ---------- */

static void test_resonance(void)
{
    TapeDelay *s = malloc(sizeof *s);
    const long N = 48000 * 8;
    float *x = fbuf(N), *yl = fbuf(N), *yr = fbuf(N);
    long n;
    double pk, tail;
    int r;
    printf("tape delay resonance: Q law, one pass at the corner vs the passband, self-oscillation bounded\n");
    CHECK(fabs(tapedelay_lpf_q(0) - 0.7071) < 1e-3 && fabs(tapedelay_lpf_q(127) - 5.0) < 0.01,
          "Q law: %.3f .. %.3f", tapedelay_lpf_q(0), tapedelay_lpf_q(127));
    /* one pass (the first echo), FDBK 0: a sine at the LPF corner and one 3 octaves below */
    for (r = 0; r <= 127; r += 127) {
        double g_c, g_p, fc;
        int k;
        for (k = 0; k < 2; k++) {
            double f;
            tapedelay_init(s, 3u);
            tapedelay_set(s, TD_TIME, 3); tapedelay_set(s, TD_FDBK, 0); tapedelay_set(s, TD_WOW, 0);
            tapedelay_set(s, TD_FLUT, 0); tapedelay_set(s, TD_DRIV, 0); tapedelay_set(s, TD_RES, (float)r);
            tapedelay_set(s, TD_LPF, 76); tapedelay_set(s, TD_HPF, 0);
            tapedelay_snap(s);
            fc = tapedelay_lpf_hz(76);
            f = k ? fc : fc / 8;
            for (n = 0; n < 48000; n++) x[n] = (float)(0.01 * sin(2 * M_PI * f * n / 48000.0));
            tapedelay_process(s, x, x, yl, yr, 48000);
            if (k) g_c = 20 * log10(rms(yl, 24000, 48000) / rms(x, 24000, 48000));
            else g_p = 20 * log10(rms(yl, 24000, 48000) / rms(x, 24000, 48000));
        }
        printf("  RES %3d: gain at the corner (%.0f Hz) %+.1f dB, 3 octaves below %+.1f dB\n", r, fc, g_c, g_p);
        if (r == 0) CHECK(fabs(g_c + 3.0) < 0.3 && fabs(g_p) < 0.3, "RES 0 pass: %.2f / %.2f dB", g_c, g_p);
        else CHECK(fabs(g_c - 7.2) < 0.6 && fabs(g_p + 6.8) < 0.6, "RES 127 pass: %.2f / %.2f dB", g_c, g_p);
    }
    /* full RES, FDBK 110 %: a burst, then 6 s of silence: it sings, and stays bounded */
    tapedelay_init(s, 3u);
    tapedelay_set(s, TD_TIME, 7); tapedelay_set(s, TD_FDBK, 127); tapedelay_set(s, TD_RES, 127);
    tapedelay_set(s, TD_LPF, 70);
    tapedelay_snap(s);
    memset(x, 0, sizeof(float) * (size_t)N);
    for (n = 0; n < 4800; n++) x[n] = 0.5f * (float)sin(2 * M_PI * 300.0 * n / 48000.0);
    tapedelay_process(s, x, x, yl, yr, (int)N);
    pk = 0;
    for (n = 0; n < N; n++) { if (fabs(yl[n]) > pk) pk = fabs(yl[n]); if (fabs(yr[n]) > pk) pk = fabs(yr[n]); }
    tail = rms(yl, N - 48000, N);
    printf("  RES 127, FDBK 127: peak %.3f, RMS of the last second %.3f (self-oscillating, held by the saturator)\n", pk, tail);
    CHECK(pk < 1.6 && tail > 0.05, "self-oscillation: peak %.3f, tail %.3f", pk, tail);
    free(s); free(x); free(yl); free(yr);
}

/* ---------- AGE ---------- */

/* residual of y[a..b) after removing the best-fit sine at f (least squares) */
static double sine_residual(const float *y, long a, long b, double f)
{
    double sc = 0, cc = 0, ss = 0, ys = 0, yc = 0, A, B, det, e = 0, w = 2 * M_PI * f / 48000.0;
    long n;
    for (n = a; n < b; n++) {
        double s_ = sin(w * n), c_ = cos(w * n);
        ss += s_ * s_; cc += c_ * c_; sc += s_ * c_; ys += y[n] * s_; yc += y[n] * c_;
    }
    det = ss * cc - sc * sc;
    A = (ys * cc - yc * sc) / det;
    B = (yc * ss - ys * sc) / det;
    for (n = a; n < b; n++) { double r = y[n] - A * sin(w * n) - B * cos(w * n); e += r * r; }
    return sqrt(e / (double)(b - a));
}

static void test_age(void)
{
    TapeDelay *s = malloc(sizeof *s);
    const long N = 48000 * 4;
    float *x = fbuf(N), *yl = fbuf(N), *yr = fbuf(N), *zl = fbuf(N), *zr = fbuf(N);
    static const float ages[] = { 0, 32, 64, 127 };
    size_t k;
    long n, ev = 0, blk;
    printf("tape delay AGE: noise (hiss + crackle) under a steady echo, crackle rate, AGE 0 adds nothing\n");
    /* a small level, so the saturator's third harmonic (-113 dB here) stays out of the residual */
    for (n = 0; n < N; n++) x[n] = (float)(0.005 * sin(2 * M_PI * 1000.0 * n / 48000.0));
    for (k = 0; k < 4; k++) {
        double sig, res;
        tapedelay_init(s, 5u);
        tapedelay_set(s, TD_TIME, 7); tapedelay_set(s, TD_FDBK, 40); tapedelay_set(s, TD_WOW, 0);
        tapedelay_set(s, TD_FLUT, 0); tapedelay_set(s, TD_DRIV, 0); tapedelay_set(s, TD_AGE, ages[k]);
        tapedelay_snap(s);
        tapedelay_process(s, x, x, yl, yr, (int)N);
        sig = rms(yl, N - 96000, N);
        res = sine_residual(yl, N - 96000, N, 1000.0);
        printf("  AGE %3.0f: echo %.1f dBFS, noise %.1f dB under it\n", ages[k], 20 * log10(sig), 20 * log10(sig / res));
        if (ages[k] == 0) CHECK(20 * log10(sig / res) > 95, "AGE 0 noise only %.1f dB down", 20 * log10(sig / res));
        if (ages[k] == 127) CHECK(20 * log10(sig / res) > 40 && 20 * log10(sig / res) < 60,
                                  "AGE 127 noise %.1f dB down (want 40..60: subtle but there)", 20 * log10(sig / res));
    }
    /* AGE 0: the noise generators have no effect (other seeds, same output) */
    tapedelay_init(s, 5u);
    tapedelay_set(s, TD_TIME, 7); tapedelay_set(s, TD_FDBK, 60); tapedelay_snap(s);
    tapedelay_process(s, x, x, yl, yr, 48000);
    tapedelay_init(s, 5u);
    s->hrng = 99u; s->crng = 12345u;
    tapedelay_set(s, TD_TIME, 7); tapedelay_set(s, TD_FDBK, 60); tapedelay_snap(s);
    tapedelay_process(s, x, x, zl, zr, 48000);
    CHECK(!memcmp(yl, zl, 48000 * sizeof(float)) && !memcmp(yr, zr, 48000 * sizeof(float)), "AGE 0 output depends on the noise seeds");
    printf("  AGE 0 with other noise seeds: %s\n", memcmp(yl, zl, 48000 * sizeof(float)) ? "DIFFERS" : "bit-identical");
    /* crackle rate at AGE 127: CK_RATE_MAX = 6 events per second */
    tapedelay_init(s, 9u);
    tapedelay_set(s, TD_AGE, 127); tapedelay_snap(s);
    for (blk = 0; blk < 100L * 1500; blk++) {
        int j;
        tapedelay_process(s, x, x, yl, yr, 32);
        for (j = 0; s->ev_pos[j] != 32; j++) ev++;
    }
    printf("  crackle events in 100 s at AGE 127: %ld (expected 600 +- 74, 3 sigma)\n", ev);
    CHECK(labs(ev - 600) < 74, "crackle rate %ld per 100 s", ev);
    free(s); free(x); free(yl); free(yr); free(zl); free(zr);
}

/* ============================== flanger ============================== */

static void fl_static(Flanger *s, float man)
{
    flanger_init(s);
    flanger_set(s, FL_DPTH, 0); flanger_set(s, FL_FDBK, 64); flanger_set(s, FL_DAMP, 0);
    flanger_set(s, FL_MAN, man); flanger_set(s, FL_WDTH, 0); flanger_set(s, FL_HPF, 0);
    flanger_snap(s);
    s->hpG = 0.0f;           /* no input high-pass: the delay alone */
}

static void test_flanger_delay(void)
{
    static const float mans[] = { 0, 12, 50, 66, 90, 127 };
    Flanger *s = malloc(sizeof *s);
    const long N = 48000;
    float *x = fbuf(N), *yl = fbuf(N), *yr = fbuf(N);
    size_t c;
    long n;
    printf("flanger delay range: MAN 0..127 with DPTH 0, wet = the input delayed (500 Hz phase, impulse peak)\n");
    for (c = 0; c < sizeof mans / sizeof mans[0]; c++) {
        double D = flanger_man_ms(mans[c]) * 48.0, est, err, pk = 0;
        long at = 0;
        fl_static(s, mans[c]);
        memset(x, 0, sizeof(float) * (size_t)N);
        x[0] = 1.0f;
        flanger_process(s, x, x, yl, yr, (int)N);
        for (n = 0; n < N; n++) if (fabs(yl[n]) > pk) { pk = fabs(yl[n]); at = n; }
        fl_static(s, mans[c]);
        for (n = 0; n < N; n++) x[n] = (float)(0.1 * sin(2 * M_PI * 500.0 * n / 48000.0));
        flanger_process(s, x, x, yl, yr, (int)N);
        est = sine_delay(yl, N - 9600, N, 500.0);
        err = fabs(est - fmod(D, 96.0));
        if (err > 48) err = 96 - err;
        printf("  MAN %3.0f: %.4f ms = %8.3f samples; impulse peak at %ld; phase error %.4f samples\n",
               mans[c], D / 48.0, D, at, err);
        CHECK(err < 0.02, "MAN %.0f delay error %.4f samples", mans[c], err);
        CHECK(labs(at - (long)floor(D + 0.5)) <= 1, "MAN %.0f impulse at %ld", mans[c], at);
    }
    CHECK(fabs(flanger_man_ms(0) - 0.05022) < 1e-4 && fabs(flanger_man_ms(127) - 77.04) < 0.05,
          "range %.4f .. %.2f ms", flanger_man_ms(0), flanger_man_ms(127));
    free(s); free(x); free(yl); free(yr);
}

static void test_flanger_lfo(void)
{
    Flanger *s = malloc(sizeof *s);
    float z[32] = { 0 }, o[32], o2[32];
    static const struct { float spd, sync, bpm; } rc[] = { { 0, 0, 120 }, { 30, 0, 120 }, { 64, 0, 120 }, { 127, 0, 120 },
                                                         { 0, 5, 120 }, { 0, 1, 120 }, { 0, 12, 97 } };
    size_t c;
    printf("flanger LFO: rate (free and synced) from the swept delay's period; depth from its extremes\n");
    for (c = 0; c < sizeof rc / sizeof rc[0]; c++) {
        double f, per, t_first = -1, t_last = -1, prev = 0, mn = 1e9, mx = 0, lc;
        long blk, nb, crosses = 0;
        flanger_init(s);
        flanger_set_bpm(s, rc[c].bpm);
        flanger_set(s, FL_SPD, rc[c].spd); flanger_set(s, FL_SYNC, rc[c].sync);
        flanger_set(s, FL_DPTH, 64); flanger_set(s, FL_MAN, 60); flanger_set(s, FL_DAMP, 0);
        flanger_set(s, FL_SLEW, 0);
        flanger_snap(s);
        f = flanger_rate_hz(s);
        per = 1500.0 / f;                       /* blocks per cycle */
        nb = (long)(per * 3.0) + 4;
        if (nb < 3000) nb = (long)(per * 40) + 4;
        lc = log2(flanger_man_ms(60) * 48.0);
        for (blk = 0; blk < nb; blk++) {
            double v;
            flanger_process(s, z, z, o, o2, 32);
            v = log2(s->dend[0]) - lc;
            if (s->dend[0] < mn) mn = s->dend[0];
            if (s->dend[0] > mx) mx = s->dend[0];
            if (blk && prev < 0 && v >= 0) {        /* upward zero crossing, interpolated */
                double t = blk - 1 + (-prev) / (v - prev);
                if (t_first < 0) t_first = t; else { t_last = t; crosses++; }
            }
            prev = v;
        }
        {
            double got = crosses ? 1500.0 * crosses / (t_last - t_first) : 0;
            double dexp = 64.0 / 127.0 * 3.3219281;
            double dlo = log2(flanger_man_ms(60) * 48.0) - dexp, dhi = log2(flanger_man_ms(60) * 48.0) + dexp;
            printf("  SPD %3.0f SYNC %2.0f @ %3.0f BPM: %.5f Hz, measured %.5f Hz (%+.4f %%); delay %.2f..%.2f samples, expected %.2f..%.2f\n",
                   rc[c].spd, rc[c].sync, rc[c].bpm, f, got, 100 * (got / f - 1), mn, mx, pow(2, dlo), pow(2, dhi));
            CHECK(fabs(got / f - 1) < 1e-3, "LFO rate %.5f vs %.5f Hz", got, f);
            if (f < 5) CHECK(fabs(log2(mn) - dlo) < 0.01 && fabs(log2(mx) - dhi) < 0.01, "LFO depth %.2f..%.2f", mn, mx);
        }
    }
    /* shapes */
    {
        int ok = 1;
        ok &= fabs(flanger_lfo(0.25f, 0) - 1) < 1e-5 && fabs(flanger_lfo(0.75f, 0) + 1) < 1e-5 && fabs(flanger_lfo(0.1f, 0) - sin(0.2 * M_PI)) < 1e-5;
        ok &= fabs(flanger_lfo(0.25f, 1) - 1) < 1e-6 && fabs(flanger_lfo(0.125f, 1) - 0.5) < 1e-6 && fabs(flanger_lfo(0.75f, 1) + 1) < 1e-6;
        ok &= fabs(flanger_lfo(0.0f, 2) + 1) < 1e-6 && fabs(flanger_lfo(0.75f, 2) - 0.5) < 1e-6;
        ok &= fabs(flanger_lfo(0.0f, 3) - 1) < 1e-6 && fabs(flanger_lfo(0.75f, 3) + 0.5) < 1e-6;
        ok &= flanger_lfo(0.2f, 4) == 1.0f && flanger_lfo(0.7f, 4) == -1.0f;
        printf("  shapes (sine, triangle, saw up, saw down, square) at test phases: %s\n", ok ? "right" : "WRONG");
        CHECK(ok, "LFO shapes");
    }
    /* random step: new value once per cycle, the right channel the same sequence, ahead */
    {
        long blk, changesL = 0, changesR = 0, same = 0;
        float pl = 0, pr = 0;
        double f;
        flanger_init(s);
        flanger_set(s, FL_SHP, 5); flanger_set(s, FL_SPD, 100); flanger_set(s, FL_WDTH, 64); flanger_snap(s);
        for (blk = 0; blk < 15000; blk++) {
            flanger_process(s, z, z, o, o2, 32);
            if (s->rnd[0] != pl) { changesL++; pl = s->rnd[0]; if (pl == s->rnd_next) same++; }
            if (s->rnd[1] != pr) { changesR++; pr = s->rnd[1]; }
        }
        f = flanger_rate_hz(s) * 10.0;
        printf("  random step, %.2f Hz for 10 s: %ld new values left, %ld right (expected %.0f each)\n",
               flanger_rate_hz(s), changesL, changesR, f);
        CHECK(fabs(changesL - f) <= 1.5 && fabs(changesR - f) <= 1.5 && same == changesL, "random step %ld / %ld", changesL, changesR);
    }
    free(s);
}

/* Karplus-Strong: an impulse, full feedback, DPTH 0: the ringing pitch vs 1/MAN */
static double peak_freq(const float *y, long a, long b, double f0)
{
    /* Goertzel scan +-60 cents in 0.5-cent steps, parabolic peak */
    double best = -1, bc = 0, m[241];
    int k;
    for (k = 0; k <= 240; k++) {
        double f = f0 * pow(2.0, (k - 120) * 0.5 / 1200.0), w = 2 * M_PI * f / 48000.0, re = 0, im = 0;
        long n;
        for (n = a; n < b; n++) { re += y[n] * cos(w * (n - a)); im += y[n] * sin(w * (n - a)); }
        m[k] = re * re + im * im;
        if (m[k] > best) { best = m[k]; bc = k; }
    }
    k = (int)bc;
    if (k > 0 && k < 240) {
        double d = 0.5 * (m[k - 1] - m[k + 1]) / (m[k - 1] - 2 * m[k] + m[k + 1]);
        bc = k + d;
    }
    return (bc - 120) * 0.5;      /* cents off f0 */
}

static void test_flanger_ks(void)
{
    static const struct { float man, damp; const char *note; } kc[] = {
        { 90, 12, "A2 110 Hz" }, { 78, 12, "A3 220 Hz" }, { 66, 12, "A4 440 Hz" }, { 59, 12, "E5 659 Hz" },
        { 78, 48, "A3, DAMP 48" }, { 66, 64, "A4, DAMP 64" } };
    Flanger *s = malloc(sizeof *s);
    const long N = 48000 * 2;
    float *x = fbuf(N), *yl = fbuf(N), *yr = fbuf(N);
    size_t c;
    printf("flanger Karplus-Strong: impulse in, FDBK 127 (99 %%), DPTH 0: ringing pitch vs 1/MAN\n");
    for (c = 0; c < sizeof kc / sizeof kc[0]; c++) {
        double f0 = 1000.0 / flanger_man_ms(kc[c].man), cents, t60 = 0, e0;
        long n;
        flanger_init(s);
        flanger_set(s, FL_DPTH, 0); flanger_set(s, FL_FDBK, 127); flanger_set(s, FL_MAN, kc[c].man);
        flanger_set(s, FL_DAMP, kc[c].damp); flanger_set(s, FL_HPF, 0);
        flanger_snap(s);
        memset(x, 0, sizeof(float) * (size_t)N);
        x[0] = 0.5f;
        flanger_process(s, x, x, yl, yr, (int)N);
        cents = peak_freq(yl, 4800, 4800 + 24000, f0);
        e0 = rms(yl, 2400, 4800);
        for (n = 4800; n + 2400 < N; n += 2400) if (rms(yl, n, n + 2400) < e0 * 1e-3) { t60 = n / 48000.0; break; }
        printf("  MAN %3.0f (%s), DAMP %2.0f: f0 %.2f Hz, ringing at %+.2f cents; -60 dB after %s%.2f s\n",
               kc[c].man, kc[c].note, kc[c].damp, f0, cents, t60 > 0 ? "" : "more than ", t60 > 0 ? t60 : N / 48000.0);
        CHECK(fabs(cents) < 3.0, "KS pitch %+.2f cents off at MAN %.0f", cents, kc[c].man);
    }
    free(s); free(x); free(yl); free(yr);
}

static void test_flanger_feedback(void)
{
    Flanger *s = malloc(sizeof *s);
    float xl[32], xr[32], yl[32], yr[32];
    static const float fbs[] = { 0, 127 };
    uint32_t r = 5u;
    size_t k;
    printf("flanger feedback: 60 s of loud noise at FDBK 0 and 127 (-99 %% and +99 %%), all shapes, then silence\n");
    for (k = 0; k < 2; k++) {
        long blk, bad = 0;
        double pk = 0;
        int j;
        flanger_init(s);
        flanger_set(s, FL_FDBK, fbs[k]); flanger_set(s, FL_DAMP, 0); flanger_set(s, FL_SPD, 90);
        flanger_snap(s);
        for (blk = 0; blk < 60L * 1500; blk++) {
            if (blk % 1500 == 0) { flanger_set(s, FL_SHP, (float)((blk / 1500) % 6)); flanger_set(s, FL_MAN, (float)((blk / 1500) * 7 % 128)); }
            for (j = 0; j < 32; j++) { xl[j] = 2.0f * fx_rands(&r); xr[j] = 2.0f * fx_rands(&r); }
            flanger_process(s, xl, xr, yl, yr, 32);
            for (j = 0; j < 32; j++) {
                if (is_bad(yl[j]) || is_bad(yr[j])) bad++;
                if (fabs(yl[j]) > pk) pk = fabs(yl[j]);
                if (fabs(yr[j]) > pk) pk = fabs(yr[j]);
            }
        }
        memset(xl, 0, sizeof xl); memset(xr, 0, sizeof xr);
        for (blk = 0; blk < 60L * 1500; blk++) {
            flanger_process(s, xl, xr, yl, yr, 32);
            for (j = 0; j < 32; j++) if (is_bad(yl[j]) || is_bad(yr[j])) bad++;
        }
        printf("  FDBK %3.0f: peak %.2f with +-2.0 noise in, %ld bad samples, last output after 60 s of silence %g\n",
               fbs[k], pk, bad, (double)yl[31]);
        CHECK(pk < 4.0 && bad == 0 && fabs(yl[31]) < 1e-6, "feedback %0.f: peak %.2f, bad %ld", fbs[k], pk, bad);
    }
    free(s);
}

/* ============================== all three ============================== */

/* ---------- extremes: no NaN, Inf or denormals ---------- */

static void test_extremes(void)
{
    TapeDelay *td = malloc(sizeof *td);
    MBComp *mb = malloc(sizeof *mb);
    Flanger *fl = malloc(sizeof *fl);
    const int B = 32;
    float xl[32], xr[32], kl[32], kr[32], yl[32], yr[32];
    uint32_t r = 99u;
    long blk, bad = 0, i;
    double peak = 0, fpeak = 0;
    printf("extremes: 120 s, random knob jumps every 7 blocks, loud bursts (+-4.0), then 300 s of silence\n");
    tapedelay_init(td, 3u);
    mbcomp_init(mb);
    flanger_init(fl);
    for (blk = 0; blk < 120L * 1500; blk++) {
        int j, p;
        int loud = (blk / 3000) % 2 == 0;        /* 2 s on, 2 s off */
        for (j = 0; j < B; j++) {
            float u = fx_rands(&r);
            xl[j] = loud ? 4.0f * u : 0.0f;
            xr[j] = loud ? (j & 1 ? 4.0f : -4.0f) : 0.0f;
            kl[j] = kr[j] = loud ? u : 0.0f;
        }
        if (blk % 7 == 0) {
            p = (int)(fx_randu(&r) * TD_NPARAMS) % TD_NPARAMS;
            tapedelay_set(td, p, fx_randu(&r) < 0.5f ? 127.0f : fx_randu(&r) * 127.0f);
            tapedelay_set(td, TD_FDBK, 127.0f);
            if (blk % 700 == 0) { tapedelay_set_bpm(td, 30.0f + 270.0f * fx_randu(&r)); flanger_set_bpm(fl, 30.0f + 270.0f * fx_randu(&r)); }
            p = (int)(fx_randu(&r) * MB_NPARAMS) % MB_NPARAMS;
            mbcomp_set(mb, p, fx_randu(&r) < 0.5f ? (fx_randu(&r) < 0.5f ? 0.0f : 127.0f) : fx_randu(&r) * 127.0f);
            p = (int)(fx_randu(&r) * FL_NPARAMS) % FL_NPARAMS;
            flanger_set(fl, p, fx_randu(&r) < 0.5f ? (fx_randu(&r) < 0.5f ? 0.0f : 127.0f) : fx_randu(&r) * 127.0f);
        }
        tapedelay_process(td, xl, xr, yl, yr, B);
        for (j = 0; j < B; j++) {
            if (is_bad(yl[j]) || is_bad(yr[j])) { bad++; if (bad < 5) printf("    tapedelay block %ld: %g %g\n", blk, yl[j], yr[j]); }
            if (fabs(yl[j]) > peak) peak = fabs(yl[j]);
            if (fabs(yr[j]) > peak) peak = fabs(yr[j]);
        }
        mbcomp_process(mb, xl, xr, kl, kr, yl, yr, B);
        for (j = 0; j < B; j++) if (is_bad(yl[j]) || is_bad(yr[j])) { bad++; if (bad < 5) printf("    mbcomp block %ld: %g %g\n", blk, yl[j], yr[j]); }
        flanger_process(fl, xl, xr, yl, yr, B);
        for (j = 0; j < B; j++) {
            if (is_bad(yl[j]) || is_bad(yr[j])) { bad++; if (bad < 5) printf("    flanger block %ld: %g %g\n", blk, yl[j], yr[j]); }
            if (fabs(yl[j]) > fpeak) fpeak = fabs(yl[j]);
            if (fabs(yr[j]) > fpeak) fpeak = fabs(yr[j]);
        }
    }
    printf("  tapedelay (FDBK 127 held): peak output %.2f; flanger peak %.2f (input peaks 4.0, alternating at Nyquist on R:\n"
           "  the input itself plus at most 1.0 of feedback, and the Hermite read's overshoot)\n", peak, fpeak);
    CHECK(peak < 6.0 && fpeak < 8.0, "outputs not bounded: %.2f, %.2f", peak, fpeak);
    CHECK(bad == 0, "%ld NaN/Inf/denormal output samples", bad);
    tapedelay_set(td, TD_FDBK, 120.0f);
    tapedelay_set(td, TD_AGE, 0.0f);
    memset(xl, 0, sizeof xl);
    memset(xr, 0, sizeof xr);
    for (blk = 0; blk < 300L * 1500; blk++) {
        int j;
        tapedelay_process(td, xl, xr, yl, yr, B);
        for (j = 0; j < B; j++) if (is_bad(yl[j]) || is_bad(yr[j])) bad++;
        mbcomp_process(mb, xl, xr, NULL, NULL, yl, yr, B);
        for (j = 0; j < B; j++) if (is_bad(yl[j]) || is_bad(yr[j])) bad++;
        flanger_process(fl, xl, xr, yl, yr, B);
        for (j = 0; j < B; j++) if (is_bad(yl[j]) || is_bad(yr[j])) bad++;
        if (blk == 1000) { tapedelay_set(td, TD_FDBK, 40.0f); tapedelay_set(td, TD_RES, 0.0f); flanger_set(fl, FL_FDBK, 100.0f); }
    }
    {
        long nbad = 0;
        int b;
#define CKF(x) do { if (is_bad(x)) { nbad++; printf("    bad state %s = %g\n", #x, (double)(x)); } } while (0)
        for (i = 0; i < 2 * TD_RING; i++) if (is_bad(td->ring[i])) nbad++;
        for (i = 0; i < FL_RING + 3; i++) if (is_bad(fl->ring[0][i]) || is_bad(fl->ring[1][i])) nbad++;
        for (i = 0; i < TD_NPARAMS; i++) CKF(td->knob[i]);
        CKF(td->fb); CKF(td->fb_i); CKF(td->drive); CKF(td->drive_i); CKF(td->inv_drive); CKF(td->inv_drive_i);
        CKF(td->wowA); CKF(td->flutA); CKF(td->mod); CKF(td->mod_i); CKF(td->lhp); CKF(td->llp); CKF(td->res);
        CKF(td->age); CKF(td->trim); CKF(td->wid); CKF(td->env); CKF(td->hg); CKF(td->hg_i);
        CKF(td->d); CKF(td->nw1); CKF(td->nw2); CKF(td->nf1); CKF(td->nf2); CKF(td->ck_impL); CKF(td->ck_impR);
        for (b = 0; b < 6; b++) { CKF(td->wc[b]); CKF(td->wc_i[b]); }
        for (b = 0; b < 2; b++) {
            CKF(td->hp[b].ic1); CKF(td->hp[b].ic2); CKF(td->lp[b].ic1); CKF(td->lp[b].ic2);
            CKF(td->ck[b].ic1); CKF(td->ck[b].ic2); CKF(td->hz[b]);
            CKF(mb->s1[b].ic1); CKF(mb->s1[b].ic2); CKF(mb->s1lp[b].ic1); CKF(mb->s1lp[b].ic2);
            CKF(mb->s1hp[b].ic1); CKF(mb->s1hp[b].ic2); CKF(mb->s2[b].ic1); CKF(mb->s2[b].ic2);
            CKF(mb->s2lp[b].ic1); CKF(mb->s2lp[b].ic2); CKF(mb->s2hp[b].ic1); CKF(mb->s2hp[b].ic2);
            CKF(mb->apl[b].ic1); CKF(mb->apl[b].ic2); CKF(mb->apd1[b].ic1); CKF(mb->apd1[b].ic2);
            CKF(mb->apd2[b].ic1); CKF(mb->apd2[b].ic2); CKF(mb->sc[b].ic1); CKF(mb->sc[b].ic2);
            CKF(fl->dly[b]); CKF(fl->dv[b]); CKF(fl->dacc[b]); CKF(fl->dend[b]); CKF(fl->hp_s[b]); CKF(fl->fbl_s[b]);
            CKF(fl->ls[b]); CKF(fl->rnd[b]);
        }
        for (b = 0; b < 3; b++) {
            CKF(mb->band[b].y1); CKF(mb->band[b].y2); CKF(mb->band[b].mk); CKF(mb->band[b].mk_i);
            CKF(mb->band[b].w); CKF(mb->band[b].w_i);
        }
        CKF(mb->mw); CKF(mb->mw_i); CKF(mb->md); CKF(mb->md_i); CKF(mb->lf_lo); CKF(mb->lf_hi);
        CKF(fl->lc); CKF(fl->dep); CKF(fl->off); CKF(fl->ofs); CKF(fl->lh); CKF(fl->da); CKF(fl->fb); CKF(fl->fb_i);
        CKF(fl->pg); CKF(fl->pg_i); CKF(fl->hpG); CKF(fl->sl_h);
#undef CKF
        printf("  after 300 s of silence: %ld bad output samples, %ld bad state floats (rings included)\n", bad, nbad);
        CHECK(bad == 0 && nbad == 0, "denormal/NaN after decay: %ld out, %ld state", bad, nbad);
    }
    free(td); free(mb); free(fl);
}

/* ---------- determinism and block size ---------- */

typedef struct { long at; int p; float v; } Change;

static void cut_loop(long N, int mode, const Change *ch, int nch, void (*proc)(void *, long, long),
                     void (*set)(void *, int, float), void *st)
{
    long pos = 0;
    uint32_t r = 5u;
    int c = 0;
    while (pos < N) {
        long seg_end = c < nch ? ch[c].at : N;
        while (pos < seg_end) {
            long m = mode == 0 ? 32 : (mode == 1 ? seg_end - pos : 1 + (long)(fx_randu(&r) * 100));
            if (m > seg_end - pos) m = seg_end - pos;
            proc(st, pos, m);
            pos += m;
        }
        if (c < nch) { set(st, ch[c].p, ch[c].v); c++; }
    }
}

static const float *g_x;
static float *g_yl, *g_yr;
static void td_proc(void *s, long pos, long m) { tapedelay_process(s, g_x + pos, g_x + pos, g_yl + pos, g_yr + pos, (int)m); }
static void td_setp(void *s, int p, float v) { tapedelay_set(s, p, v); }
static void mb_proc(void *s, long pos, long m) { mbcomp_process(s, g_x + pos, g_x + pos, g_x + pos, g_x + pos, g_yl + pos, g_yr + pos, (int)m); }
static void mb_setp(void *s, int p, float v) { mbcomp_set(s, p, v); }
static void fl_proc(void *s, long pos, long m) { flanger_process(s, g_x + pos, g_x + pos, g_yl + pos, g_yr + pos, (int)m); }
static void fl_setp(void *s, int p, float v) { flanger_set(s, p, v); }

static void test_determinism_blocks(void)
{
    const long N = 48000 * 6;
    float *x = fbuf(N), *a[4];
    TapeDelay *td = malloc(sizeof *td);
    MBComp *mb = malloc(sizeof *mb);
    Flanger *fl = malloc(sizeof *fl);
    Change tch[] = { { 48000, TD_TIME, 15 }, { 96000, TD_WID, 120 }, { 150016, TD_LPF, 40 }, { 200000, TD_RES, 127 } };
    Change mch[] = { { 48000, MB_XLO, 80 }, { 96000, MB_P(1, MB_MUP), 127 }, { 150016, MB_LSTN, 2 }, { 200000, MB_MIX, 30 } };
    Change fch[] = { { 48000, FL_MAN, 90 }, { 96000, FL_SHP, 5 }, { 150016, FL_FDBK, 0 }, { 200000, FL_SYNC, 9 } };
    uint32_t r = 77u;
    const char *names[3] = { "tapedelay", "mbcomp", "flanger" };
    long i;
    int k, m, e;
    printf("determinism and block size (32-sample calls vs one call per segment vs random 1..100-sample calls)\n");
    for (i = 0; i < N; i++) x[i] = (i % 24000) < 4800 ? 0.5f * fx_rands(&r) : 0.0f;
    for (k = 0; k < 4; k++) a[k] = fbuf(2 * N);
    for (k = 0; k < 4; k++) { tch[k].at = tch[k].at / 32 * 32; mch[k].at = mch[k].at / 32 * 32; fch[k].at = fch[k].at / 32 * 32; }
    g_x = x;
    for (e = 0; e < 3; e++) {
        for (m = 0; m < 4; m++) {
            g_yl = a[m]; g_yr = a[m] + N;
            if (e == 0) {
                tapedelay_init(td, 11u);
                tapedelay_set(td, TD_WOW, 90); tapedelay_set(td, TD_FLUT, 70); tapedelay_set(td, TD_AGE, 127);
                tapedelay_set(td, TD_FDBK, 100); tapedelay_set(td, TD_WID, 20); tapedelay_set(td, TD_RES, 80);
                tapedelay_snap(td);
                cut_loop(N, m == 3 ? 0 : m, tch, 4, td_proc, td_setp, td);
            } else if (e == 1) {
                mbcomp_init(mb);
                mbcomp_set(mb, MB_P(0, MB_THR), 60); mbcomp_set(mb, MB_P(0, MB_RAT), 7);
                mbcomp_set(mb, MB_SCS, 1); mbcomp_set(mb, MB_SCF, 100); mbcomp_set(mb, MB_MIX, 90);
                mbcomp_snap(mb);
                cut_loop(N, m == 3 ? 0 : m, mch, 4, mb_proc, mb_setp, mb);
            } else {
                flanger_init(fl);
                flanger_set(fl, FL_SPD, 100); flanger_set(fl, FL_OFS, 60); flanger_set(fl, FL_SLEW, 30);
                flanger_set(fl, FL_POL, 1);
                flanger_snap(fl);
                cut_loop(N, m == 3 ? 0 : m, fch, 4, fl_proc, fl_setp, fl);
            }
        }
        CHECK(!memcmp(a[0], a[3], sizeof(float) * 2 * N), "%s: two identical runs differ", names[e]);
        CHECK(!memcmp(a[0], a[1], sizeof(float) * 2 * N), "%s: 32-sample blocks != long blocks", names[e]);
        CHECK(!memcmp(a[0], a[2], sizeof(float) * 2 * N), "%s: 32-sample blocks != random blocks", names[e]);
        printf("  %-9s: repeat %s, long blocks %s, random blocks %s\n", names[e],
               memcmp(a[0], a[3], sizeof(float) * 2 * N) ? "DIFFERS" : "bit-identical",
               memcmp(a[0], a[1], sizeof(float) * 2 * N) ? "DIFFER" : "bit-identical",
               memcmp(a[0], a[2], sizeof(float) * 2 * N) ? "DIFFER" : "bit-identical");
    }
    for (k = 0; k < 4; k++) free(a[k]);
    free(x); free(td); free(mb); free(fl);
}

/* ---------- no clicks on knob moves ---------- */

static void test_smoothing(void)
{
    const long N = 96000;
    float *x = fbuf(N), *yl = fbuf(N), *yr = fbuf(N);
    TapeDelay *td = malloc(sizeof *td);
    MBComp *mb = malloc(sizeof *mb);
    Flanger *fl = malloc(sizeof *fl);
    long i;
    double r;
    printf("smoothing: click detector after a knob jump (1.0 = clean; a step reads >> 2; pitch bends read their factor squared)\n");
    for (i = 0; i < N; i++) {
        double fade = i < 4800 ? 0.5 - 0.5 * cos(M_PI * i / 4800.0) : 1.0;
        x[i] = (float)(0.3 * fade * sin(2 * M_PI * 220.0 * i / 48000.0));
    }
    {
        static const struct { int p; float v0, v1; const char *what; } c[] = {
            { MB_P(1, MB_MUP), 0, 127, "M.MUP 0 -> 127" }, { MB_LSTN, 0, 3, "LSTN all -> high" },
            { MB_XLO, 10, 100, "XLO 10 -> 100" }, { MB_MIX, 127, 0, "MIX 127 -> 0" }, { MB_MIX, 0, 64, "MIX 0 -> 64" },
            { MB_P(1, MB_RAT), 0, 8, "M.RAT 1:1 -> 20:1" },
        };
        size_t k;
        for (k = 0; k < sizeof c / sizeof c[0]; k++) {
            mb_neutral(mb);
            mbcomp_set(mb, MB_XHI, 90);
            mbcomp_set(mb, MB_P(1, MB_THR), 100);
            mbcomp_set(mb, MB_P(1, MB_ATK), 20);
            mbcomp_set(mb, c[k].p, c[k].v0);
            mbcomp_snap(mb);
            mbcomp_process(mb, x, x, NULL, NULL, yl, yr, 48000);
            mbcomp_set(mb, c[k].p, c[k].v1);
            mbcomp_process(mb, x + 48000, x + 48000, NULL, NULL, yl + 48000, yr + 48000, 48000);
            r = click_ratio(yl, 48000, 52800, 40000, 48000);
            { double r2 = click_ratio(yl, 48000, 52800, 88000, 96000); if (r2 < r) r = r2; }
            printf("  mbcomp    %-20s %.2f\n", c[k].what, r);
            CHECK(r < 2.0, "click on %s: %.2f", c[k].what, r);
        }
    }
    {
        static const struct { int p; float v0, v1; double lim; const char *what; } c[] = {
            { TD_TIME, 31, 7, 3.0, "TIME 31 -> 7" }, { TD_LPF, 127, 40, 2.0, "LPF 127 -> 40" },
            { TD_HPF, 0, 100, 2.0, "HPF 0 -> 100" }, { TD_RES, 0, 127, 2.0, "RES 0 -> 127" },
            { TD_TONE, 0, 127, 2.0, "TONE 0 -> 127" }, { TD_WID, 0, 127, 2.0, "WID 0 -> 127" },
            { TD_FDBK, 0, 110, 2.0, "FDBK 0 -> 110" }, { TD_WOW, 0, 127, 2.0, "WOW 0 -> 127" },
            { TD_FLUT, 0, 127, 2.0, "FLUT 0 -> 127" }, { TD_AGE, 0, 127, 2.0, "AGE 0 -> 127" },
            { TD_DRIV, 0, 127, 2.0, "DRIV 0 -> 127" },
        };
        size_t k;
        for (k = 0; k < sizeof c / sizeof c[0]; k++) {
            td_clean(td, 31, 120);
            tapedelay_set(td, TD_FDBK, 60);
            tapedelay_set(td, TD_LPF, 102); tapedelay_set(td, TD_HPF, 22);
            tapedelay_set(td, c[k].p, c[k].v0);
            tapedelay_snap(td);
            tapedelay_process(td, x, x, yl, yr, 60000);
            tapedelay_set(td, c[k].p, c[k].v1);
            tapedelay_process(td, x + 60000, x + 60000, yl + 60000, yr + 60000, 36000);
            r = click_ratio(yl, 60000, 71000, 50000, 60000);
            { double r2 = click_ratio(yl, 60000, 71000, 88000, 96000); if (r2 < r) r = r2; }
            printf("  tapedelay %-20s %.2f (limit %.2f)\n", c[k].what, r, c[k].lim);
            CHECK(r < c[k].lim, "click on %s: %.2f", c[k].what, r);
        }
    }
    {
        /* the flanger's output is a delayed copy of the sine: a delay move is a short pitch bend */
        static const struct { int p; float v0, v1; double lim; const char *what; } c[] = {
            { FL_MAN, 50, 60, 2.0, "MAN 50 -> 60" }, { FL_DPTH, 0, 127, 2.0, "DPTH 0 -> 127" },
            { FL_SHP, 0, 4, 2.0, "SHP sine -> square" }, { FL_WDTH, 0, 127, 2.0, "WDTH 0 -> 127" },
            { FL_FDBK, 64, 120, 2.0, "FDBK 64 -> 120" }, { FL_HPF, 0, 127, 2.0, "HPF 0 -> 127" },
            { FL_POL, 0, 1, 2.0, "POL + -> -" }, { FL_OFS, 0, 127, 2.0, "OFS 0 -> 127" },
            { FL_DAMP, 0, 127, 2.0, "DAMP 0 -> 127" }, { FL_SYNC, 0, 12, 2.0, "SYNC off -> 1/32" },
        };
        size_t k;
        for (k = 0; k < sizeof c / sizeof c[0]; k++) {
            flanger_init(fl);
            flanger_set(fl, FL_DPTH, 20); flanger_set(fl, FL_SPD, 20); flanger_set(fl, FL_FDBK, 64);
            flanger_set(fl, FL_HPF, 0); flanger_set(fl, FL_DAMP, 0);
            flanger_set(fl, c[k].p, c[k].v0);
            flanger_snap(fl);
            flanger_process(fl, x, x, yl, yr, 48000);
            flanger_set(fl, c[k].p, c[k].v1);
            flanger_process(fl, x + 48000, x + 48000, yl + 48000, yr + 48000, 48000);
            r = click_ratio(yr, 48000, 52800, 40000, 48000);
            { double r2 = click_ratio(yr, 48000, 52800, 88000, 96000); if (r2 < r) r = r2; }
            printf("  flanger   %-20s %.2f (limit %.2f)\n", c[k].what, r, c[k].lim);
            CHECK(r < c[k].lim, "click on %s: %.2f", c[k].what, r);
        }
    }
    free(x); free(yl); free(yr); free(td); free(mb); free(fl);
}

/* the Space Echo's tests (T5.2) use the helpers above */
#include "test_spaceecho.c"

/* ---------- bench ---------- */

static void bench(void)
{
    const long N = 48000 * 20;
    float *x = fbuf(N), *yl = fbuf(N), *yr = fbuf(N);
    TapeDelay *td = malloc(sizeof *td);
    MBComp *mb = malloc(sizeof *mb);
    Flanger *fl = malloc(sizeof *fl);
    uint32_t r = 1u;
    long i;
    clock_t t0;
    double t;
    for (i = 0; i < N; i++) x[i] = 0.3f * fx_rands(&r);
    tapedelay_init(td, 1u);
    tapedelay_set(td, TD_AGE, 100); tapedelay_set(td, TD_WID, 20); tapedelay_set(td, TD_RES, 60);
    tapedelay_snap(td);
    t0 = clock();
    for (i = 0; i < N; i += 32) tapedelay_process(td, x + i, x + i, yl + i, yr + i, 32);
    t = (double)(clock() - t0) / CLOCKS_PER_SEC;
    printf("tapedelay (AGE, WID, RES): %.1f ns/sample, %.0fx real time\n", t / N * 1e9, 20.0 / t);
    mbcomp_init(mb);
    mbcomp_set(mb, MB_SCS, 1); mbcomp_set(mb, MB_SCF, 100); mbcomp_set(mb, MB_MIX, 100);
    mbcomp_snap(mb);
    t0 = clock();
    for (i = 0; i < N; i += 32) mbcomp_process(mb, x + i, x + i, x + i, x + i, yl + i, yr + i, 32);
    t = (double)(clock() - t0) / CLOCKS_PER_SEC;
    printf("mbcomp (key + dry path on): %.1f ns/sample, %.0fx real time\n", t / N * 1e9, 20.0 / t);
    flanger_init(fl);
    t0 = clock();
    for (i = 0; i < N; i += 32) flanger_process(fl, x + i, x + i, yl + i, yr + i, 32);
    t = (double)(clock() - t0) / CLOCKS_PER_SEC;
    printf("flanger (default patch): %.1f ns/sample, %.0fx real time\n", t / N * 1e9, 20.0 / t);
    {
        SpaceEcho *se = malloc(sizeof *se);
        spaceecho_init(se, 1u);
        spaceecho_set(se, SE_MODE, 6); spaceecho_set(se, SE_AGE, 100); spaceecho_set(se, SE_BW, 60);
        spaceecho_set(se, SE_PAN, SE_PAN_ALT); spaceecho_set(se, SE_INT, 50);
        spaceecho_snap(se);
        t0 = clock();
        for (i = 0; i < N; i += 32) spaceecho_process(se, x + i, x + i, yl + i, yr + i, 32);
        t = (double)(clock() - t0) / CLOCKS_PER_SEC;
        printf("spaceecho (3 heads, AGE, band, ALT): %.1f ns/sample, %.0fx real time\n", t / N * 1e9, 20.0 / t);
        free(se);
    }
}

int main(int argc, char **argv)
{
    if (argc > 1 && !strcmp(argv[1], "-bench")) { bench(); return 0; }
    printf("sizeof(TapeDelay) = %lu bytes (stock delay state: 4,194,908 bytes of DDR)\n", (unsigned long)sizeof(TapeDelay));
    printf("sizeof(MBComp)    = %lu bytes (stock compressor state: 376 bytes of L1)\n", (unsigned long)sizeof(MBComp));
    printf("sizeof(Flanger)   = %lu bytes (stock chorus state: 4,928 bytes of DDR)\n\n", (unsigned long)sizeof(Flanger));
    test_crossover();
    test_comp_laws();
    test_static_curve();
    test_times();
    test_sidechain();
    test_delay_time();
    test_width();
    test_resonance();
    test_age();
    test_flanger_delay();
    test_flanger_lfo();
    test_flanger_ks();
    test_flanger_feedback();
    test_smoothing();
    test_determinism_blocks();
    test_extremes();
    test_spaceecho();
    printf("\n%d checks, %d failed\n", checks, fails);
    return fails ? 1 : 0;
}
