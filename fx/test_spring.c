/* SPDX-License-Identifier: GPL-2.0-or-later */
/*
 * test_spring.c - tests for SPNG, the spring reverb (T5.5), part of fxtest: test.c includes
 * this file after its helpers (fbuf, rms, click_ratio, cut_loop, CHECK). Not built on its own.
 */

/* a fresh spring at its defaults with some knobs set (pairs: param, value; -1 ends) */
static void sp_setup(Spring *s, const int *pv)
{
    spring_init(s);
    for (; pv && pv[0] >= 0; pv += 2) spring_set(s, pv[0], (float)pv[1]);
    spring_snap(s);
}

/* run x (mono, both inputs) through in 32-sample calls */
static void sp_run(Spring *s, const float *x, float *yl, float *yr, long n)
{
    long i;
    for (i = 0; i < n; i += 32) {
        int m = (int)(n - i < 32 ? n - i : 32);
        spring_process(s, x + i, x + i, yl + i, yr + i, m);
    }
}

/* RBJ band-pass (0 dB peak), in double, for the measurements */
static void sp_bandpass(const float *x, float *y, long n, double f, double q)
{
    double w = 2 * M_PI * f / 48000.0, al = sin(w) / (2 * q), a0 = 1 + al, a1 = -2 * cos(w), a2 = 1 - al;
    double x1 = 0, x2 = 0, y1 = 0, y2 = 0;
    long i;
    for (i = 0; i < n; i++) {
        double v = (al * x[i] - al * x2 - a1 * y1 - a2 * y2) / a0;
        x2 = x1; x1 = x[i]; y2 = y1; y1 = v;
        y[i] = (float)v;
    }
}

/* RT60 (s) of y from the Schroeder integral between -5 and -25 dB */
static double sp_rt60(const float *y, long n)
{
    double *sch = calloc((size_t)n, sizeof(double)), tot = 0;
    long i, t5 = -1, t25 = -1;
    for (i = n - 1; i >= 0; i--) { tot += (double)y[i] * y[i]; sch[i] = tot; }
    for (i = 0; i < n; i++) {
        double d = 10.0 * log10(sch[i] / tot + 1e-300);
        if (t5 < 0 && d < -5.0) t5 = i;
        if (t25 < 0 && d < -25.0) { t25 = i; break; }
    }
    free(sch);
    return t5 >= 0 && t25 > t5 ? 3.0 * (double)(t25 - t5) / 48000.0 : -1.0;
}

/* the energy centroid (ms) of band f of y over [0, w) */
static double sp_centroid(const float *y, long w, double f, float *tmp)
{
    double num = 0, den = 0;
    long i;
    sp_bandpass(y, tmp, w, f, 2.0);
    for (i = 0; i < w; i++) { num += (double)i * tmp[i] * tmp[i]; den += (double)tmp[i] * tmp[i]; }
    return den > 0 ? num / den / 48.0 : -1.0;
}

static void test_spring_laws(void)
{
    printf("spring: knob laws\n");
    CHECK(spring_pre_ms(0) == 0.0f && fabs(spring_pre_ms(127) - 250.0) < 1e-3 && fabs(spring_pre_ms(64) - 63.49) < 0.05,
          "PRE: %g %g %g ms", spring_pre_ms(0), spring_pre_ms(64), spring_pre_ms(127));
    CHECK(fabs(spring_rt60_s(0) - 0.3) < 1e-6 && fabs(spring_rt60_s(127) - 16.0) < 0.01 && fabs(spring_rt60_s(64) - 2.22) < 0.01,
          "DEC: %g %g %g s", spring_rt60_s(0), spring_rt60_s(64), spring_rt60_s(127));
    CHECK(spring_drip(0) == 0.0f && spring_drip(127) == 1.0f && fabs(spring_drip(64) - 0.754) < 0.001,
          "DRIP: %g %g %g", spring_drip(0), spring_drip(64), spring_drip(127));
    CHECK(spring_dwel_db(64) == 0.0f && fabs(spring_dwel_db(0) + 24.08) < 0.01 && fabs(spring_dwel_db(127) - 23.7) < 0.01,
          "DWEL: %g %g %g dB", spring_dwel_db(0), spring_dwel_db(64), spring_dwel_db(127));
    CHECK(fabs(spring_hpf_hz(0) - 40.0) < 1e-3 && fabs(spring_hpf_hz(127) - 1000.0) < 0.1,
          "HPF: %g %g Hz", spring_hpf_hz(0), spring_hpf_hz(127));
    CHECK(fabs(spring_tone_hz(0) - 600.0) < 1e-3 && fabs(spring_tone_hz(127) - 9600.0) < 0.5 && fabs(spring_tone_hz(64) - 2426.3) < 0.5,
          "TONE: %g %g %g Hz", spring_tone_hz(0), spring_tone_hz(64), spring_tone_hz(127));
    CHECK(spring_width(0) == 0.0f && spring_width(127) == 1.0f, "WDTH");
    CHECK(fabs(spring_tens_ms(0) - 50.4) < 1e-3 && fabs(spring_tens_ms(127) - 14.48) < 0.01 && fabs(spring_tens_ms(64) - 26.9) < 0.1,
          "TENS: %g %g %g ms", spring_tens_ms(0), spring_tens_ms(64), spring_tens_ms(127));
    printf("  PRE 0/64/127: %.1f/%.1f/%.1f ms; DEC %.2f/%.2f/%.1f s; DRIP %.3f/%.3f/%.3f; DWEL %+.1f/%+.1f/%+.1f dB;\n"
           "  HPF %.0f/%.0f/%.0f Hz; TONE %.0f/%.0f/%.0f Hz; TENS %.1f/%.1f/%.1f ms (springs x1, x1.173, x1.387)\n",
           spring_pre_ms(0), spring_pre_ms(64), spring_pre_ms(127), spring_rt60_s(0), spring_rt60_s(64), spring_rt60_s(127),
           spring_drip(0), spring_drip(64), spring_drip(127), spring_dwel_db(0), spring_dwel_db(64), spring_dwel_db(127),
           spring_hpf_hz(0), spring_hpf_hz(64), spring_hpf_hz(127), spring_tone_hz(0), spring_tone_hz(64), spring_tone_hz(127),
           spring_tens_ms(0), spring_tens_ms(64), spring_tens_ms(127));
}

/* silence in, exact silence out; mono at WDTH 0; the first arrival where TENS and PRE put it */
static void test_spring_basics(void)
{
    const long N = 48000 * 2;
    float *x = fbuf(N), *yl = fbuf(N), *yr = fbuf(N);
    Spring *s = malloc(sizeof *s);
    long i, nz = 0, diff = 0;
    int k;
    printf("spring: silence, mono, first arrival\n");
    {
        const int pv[] = { SP_DEC, 127, SP_DWEL, 127, SP_DRIP, 127, -1 };
        sp_setup(s, pv);
        sp_run(s, x, yl, yr, N);
        for (i = 0; i < N; i++) if (yl[i] != 0.0f || yr[i] != 0.0f) nz++;
        CHECK(nz == 0, "silence in: %ld nonzero output samples", nz);
    }
    for (i = 0; i < N; i++) x[i] = (i % 9600) < 200 ? 0.3f * sinf(0.05f * (float)i) : 0.0f;
    {
        const int pv[] = { SP_WDTH, 0, -1 };
        sp_setup(s, pv);
        sp_run(s, x, yl, yr, N);
        for (i = 0; i < N; i++) if (yl[i] != yr[i]) diff++;
        CHECK(diff == 0 && rms(yl, 0, N) > 1e-3, "WDTH 0: %ld samples differ between L and R (rms %g)", diff, rms(yl, 0, N));
    }
    {
        const int pv[] = { SP_WDTH, 127, -1 };
        double c = 0, el = 0, er = 0;
        sp_setup(s, pv);
        sp_run(s, x, yl, yr, N);
        for (i = 0; i < N; i++) { c += (double)yl[i] * yr[i]; el += (double)yl[i] * yl[i]; er += (double)yr[i] * yr[i]; }
        c /= sqrt(el * er) + 1e-300;
        printf("  WDTH 127: L/R correlation %.3f\n", c);
        CHECK(c < 0.5, "WDTH 127: L and R too alike (correlation %.3f)", c);
    }
    /* impulse, DRIP 0 (the allpasses are then a plain delay of 2 x 16 samples at 12 kHz): the first
       arrival is spring 1's half round trip + 2.67 ms + the block (0.67 ms) and the half-bands */
    for (k = 0; k < 4; k++) {
        static const int tens[4] = { 0, 64, 127, 64 };
        static const int pre[4] = { 0, 0, 0, 64 };
        int pv[7];
        long first = -1;
        double pk = 0, want;
        pv[0] = SP_DRIP; pv[1] = 0; pv[2] = SP_TENS; pv[3] = tens[k]; pv[4] = SP_PREDLY; pv[5] = pre[k]; pv[6] = -1;
        memset(x, 0, sizeof(float) * (size_t)N);
        x[0] = 1.0f;
        sp_setup(s, pv);
        sp_run(s, x, yl, yr, N);
        for (i = 0; i < N; i++) if (fabs(yl[i]) > pk) pk = fabs(yl[i]);
        for (i = 0; i < N; i++) if (fabs(yl[i]) > 0.1 * pk) { first = i; break; }
        want = 2.0 * (int)(spring_tens_ms((float)tens[k]) * 12.0 + 0.5) / 24.0 + 32.0 / 12.0 + 32.0 / 48.0
               + (int)(spring_pre_ms((float)pre[k]) * 12.0 + 0.5) / 12.0;
        printf("  TENS %3d PRE %2d: first arrival %.2f ms (spring 1: %.2f ms expected)\n", tens[k], pre[k], first / 48.0, want);
        CHECK(fabs(first / 48.0 - want) < 0.5, "TENS %d PRE %d: first arrival %.2f ms, expected %.2f", tens[k], pre[k], first / 48.0, want);
    }
    free(s); free(x); free(yl); free(yr);
}

/* DEC is the RT60 of the open tank (TONE 127) in the middle of the band; TONE shortens the top */
static void test_spring_decay(void)
{
    const long N = 48000 * 24;
    float *x = fbuf(N), *yl = fbuf(N), *yr = fbuf(N), *b = fbuf(N);
    Spring *s = malloc(sizeof *s);
    int k;
    printf("spring: decay (RT60 at 1 kHz from the impulse response, Schroeder -5..-25 dB)\n");
    x[0] = 1.0f;
    for (k = 0; k < 4; k++) {
        static const int dec[4] = { 0, 40, 64, 100 };
        int pv[3];
        double rt, want;
        pv[0] = SP_DEC; pv[1] = dec[k]; pv[2] = -1;
        sp_setup(s, pv);
        sp_run(s, x, yl, yr, N);
        sp_bandpass(yl, b, N, 1000.0, 1.0);
        rt = sp_rt60(b, N);
        want = spring_rt60_s((float)dec[k]);
        printf("  DEC %3d: %.2f s (nominal %.2f s)\n", dec[k], rt, want);
        CHECK(fabs(rt / want - 1.0) < 0.12, "DEC %d: RT60 %.2f s, nominal %.2f s", dec[k], rt, want);
    }
    {
        const int pv0[] = { SP_DEC, 64, SP_TONE, 0, -1 };
        const int pv1[] = { SP_DEC, 64, SP_TONE, 127, -1 };
        double r0, r1, h0, h1;
        sp_setup(s, pv0);
        sp_run(s, x, yl, yr, N);
        sp_bandpass(yl, b, N, 3000.0, 1.0);
        r0 = sp_rt60(b, N);
        h0 = rms(b, 4800, 48000);
        sp_setup(s, pv1);
        sp_run(s, x, yl, yr, N);
        sp_bandpass(yl, b, N, 3000.0, 1.0);
        r1 = sp_rt60(b, N);
        h1 = rms(b, 4800, 48000);
        printf("  TONE 0 vs 127 at 3 kHz: RT60 %.2f vs %.2f s, level in 0.1..1 s %.1f dB lower\n", r0, r1, 20 * log10(h1 / h0));
        CHECK(r0 < 0.5 * r1 && h1 / h0 > 5.0, "TONE: 3 kHz RT60 %.2f vs %.2f s, level ratio %.1f", r0, r1, h1 / h0);
    }
    free(s); free(x); free(yl); free(yr); free(b);
}

/* DRIP: the lows come later than the highs (a falling chirp), the more the higher DRIP */
static void test_spring_dispersion(void)
{
    const long N = 48000;
    float *x = fbuf(N), *yl = fbuf(N), *yr = fbuf(N), *b = fbuf(N);
    Spring *s = malloc(sizeof *s);
    double dl[3];
    int k;
    printf("spring: dispersion (energy centroid of 150 Hz minus 2.4 kHz in the first arrivals, TENS 64)\n");
    x[0] = 1.0f;
    for (k = 0; k < 3; k++) {
        static const int drip[3] = { 0, 64, 127 };
        int pv[3];
        long w = (long)(0.9 * 1.5 * 2.0 * spring_tens_ms(64) * 48.0);    /* before the second arrivals */
        pv[0] = SP_DRIP; pv[1] = drip[k]; pv[2] = -1;
        sp_setup(s, pv);
        sp_run(s, x, yl, yr, N);
        dl[k] = sp_centroid(yl, w, 150.0, b) - sp_centroid(yl, w, 2400.0, b);
        printf("  DRIP %3d: the lows %.1f ms after the highs\n", drip[k], dl[k]);
    }
    /* the band-pass itself delays 150 Hz by about 2 ms more than 2.4 kHz */
    CHECK(fabs(dl[0]) < 3.0, "DRIP 0: %.1f ms", dl[0]);
    CHECK(dl[1] > dl[0] + 4.0 && dl[2] > dl[1] + 1.5, "DRIP: %.1f, %.1f, %.1f ms", dl[0], dl[1], dl[2]);
    free(s); free(x); free(yl); free(yr); free(b);
}

/* every knob at random values and the extremes, jumping, loud bursts: finite, bounded, no denormals;
   then silence: it dies away to nothing */
static void test_spring_extremes(void)
{
    Spring *s = malloc(sizeof *s);
    float xl[32], xr[32], yl[32], yr[32];
    uint32_t r = 4242u;
    long blk, bad = 0, i;
    double peak = 0;
    int j;
    printf("spring: extremes, 120 s, a knob jump every 7 blocks, loud bursts (+-4, Nyquist on R), then 300 s of silence\n");
    spring_init(s);
    for (blk = 0; blk < 120L * 1500; blk++) {
        int loud = (blk / 3000) % 2 == 0;
        for (j = 0; j < 32; j++) {
            float u = fx_rands(&r);
            xl[j] = loud ? 4.0f * u : 0.0f;
            xr[j] = loud ? (j & 1 ? 4.0f : -4.0f) : 0.0f;
        }
        if (blk % 7 == 0) {
            int p = (int)(fx_randu(&r) * SP_NPARAMS) % SP_NPARAMS;
            spring_set(s, p, fx_randu(&r) < 0.5f ? (fx_randu(&r) < 0.5f ? 0.0f : 127.0f) : fx_randu(&r) * 127.0f);
            if (blk % 3000 == 0) spring_set(s, SP_DEC, 127.0f);
        }
        spring_process(s, xl, xr, yl, yr, 32);
        for (j = 0; j < 32; j++) {
            if (is_bad(yl[j]) || is_bad(yr[j])) { bad++; if (bad < 5) printf("    block %ld: %g %g\n", blk, yl[j], yr[j]); }
            if (fabs(yl[j]) > peak) peak = fabs(yl[j]);
            if (fabs(yr[j]) > peak) peak = fabs(yr[j]);
        }
    }
    printf("  peak output %.2f (a loop holds at most 1.0, three springs and the half-bands' overshoot)\n", peak);
    CHECK(peak < 6.0 * SP_OUT, "output not bounded: %.2f", peak);
    CHECK(bad == 0, "%ld NaN/Inf/denormal output samples", bad);
    spring_set(s, SP_DEC, 127.0f);
    memset(xl, 0, sizeof xl);
    memset(xr, 0, sizeof xr);
    for (blk = 0; blk < 300L * 1500; blk++) {
        spring_process(s, xl, xr, yl, yr, 32);
        for (j = 0; j < 32; j++) if (is_bad(yl[j]) || is_bad(yr[j])) bad++;
    }
    {
        long nbad = 0, nz = 0;
        int k;
        Spring *cp = malloc(sizeof *cp);
        const float *f = (const float *)cp;
        memcpy(cp, s, sizeof *cp);
        cp->dirty = 0; cp->cur_lo = 0; cp->pos = 0; cp->wp = 0; cp->tick = 0; cp->mode_on = 0;   /* the integers */
        for (i = 0; i < (long)(sizeof(Spring) / sizeof(float)); i++) {
            if (f[i] != f[i] || (f[i] != 0.0f && fpclassify(f[i]) == FP_SUBNORMAL)) nbad++;
        }
        for (k = 0; k < SP_NS; k++) for (i = 0; i < SP_RING; i++) if (s->ring[k][i] != 0.0f) nz++;
        for (j = 0; j < 32; j++) if (yl[j] != 0.0f || yr[j] != 0.0f) nz++;
        free(cp);
        printf("  after 300 s of silence (DEC 127): %ld bad output samples, %ld bad state floats, %ld nonzero ring/output values\n",
               bad, nbad, nz);
        CHECK(bad == 0 && nbad == 0, "denormal/NaN after decay: %ld out, %ld state", bad, nbad);
        CHECK(nz == 0, "not silent after 300 s: %ld nonzero values", nz);
    }
    free(s);
}

static const float *g_spx;
static float *g_spl, *g_spr;
static void sp_proc(void *s, long pos, long m) { spring_process(s, g_spx + pos, g_spx + pos, g_spl + pos, g_spr + pos, (int)m); }
static void sp_setp(void *s, int p, float v) { spring_set(s, p, v); }

static void test_spring_determinism(void)
{
    const long N = 48000 * 6;
    float *x = fbuf(N), *a[4];
    Spring *s = malloc(sizeof *s);
    Change ch[] = { { 48000, SP_TENS, 120 }, { 96000, SP_DRIP, 127 }, { 150016, SP_PREDLY, 40 }, { 200000, SP_DEC, 120 },
                    { 220000, SP_WDTH, 0 }, { 240000, SP_DWEL, 127 } };
    uint32_t r = 31u;
    long i;
    int k, m;
    printf("spring: determinism and block size (32-sample calls vs one call per segment vs random 1..100-sample calls)\n");
    for (i = 0; i < N; i++) x[i] = (i % 24000) < 4800 ? 0.5f * fx_rands(&r) : 0.0f;
    for (k = 0; k < 4; k++) a[k] = fbuf(2 * N);
    for (k = 0; k < 6; k++) ch[k].at = ch[k].at / 32 * 32;
    g_spx = x;
    for (m = 0; m < 4; m++) {
        g_spl = a[m]; g_spr = a[m] + N;
        spring_init(s);
        spring_set(s, SP_DEC, 90); spring_set(s, SP_HPF, 30); spring_set(s, SP_TONE, 80); spring_set(s, SP_DWEL, 90);
        spring_snap(s);
        cut_loop(N, m == 3 ? 0 : m, ch, 6, sp_proc, sp_setp, s);
    }
    CHECK(!memcmp(a[0], a[3], sizeof(float) * 2 * N), "spring: two identical runs differ");
    CHECK(!memcmp(a[0], a[1], sizeof(float) * 2 * N), "spring: 32-sample blocks != long blocks");
    CHECK(!memcmp(a[0], a[2], sizeof(float) * 2 * N), "spring: 32-sample blocks != random blocks");
    printf("  spring   : repeat %s, long blocks %s, random blocks %s\n",
           memcmp(a[0], a[3], sizeof(float) * 2 * N) ? "DIFFERS" : "bit-identical",
           memcmp(a[0], a[1], sizeof(float) * 2 * N) ? "DIFFER" : "bit-identical",
           memcmp(a[0], a[2], sizeof(float) * 2 * N) ? "DIFFER" : "bit-identical");
    for (k = 0; k < 4; k++) free(a[k]);
    free(x); free(s);
}

/* knob jumps: no clicks. A held 500 Hz sine into a long spring; each knob jumps from a middle
   value to an extreme and back; the click ratio (largest second difference against the level,
   relative to the steady part before) stays near 1 (a pitch bend by p reads p^2) */
static void test_spring_smooth(void)
{
    const long N = 48000 * 4;
    float *x = fbuf(N), *yl = fbuf(N), *yr = fbuf(N);
    Spring *s = malloc(sizeof *s);
    long i;
    int p;
    static const char *nm[SP_NINT] = { "PRE", "DEC", "DRIP", "DWEL", "HPF", "TONE", "WDTH", "TENS" };
    printf("spring: knob jumps (500 Hz sine in, DEC 100; mid value -> extreme at 2 s -> back at 3 s)\n  click ratio:");
    for (i = 0; i < N; i++) x[i] = 0.05f * sinf(2.0f * 3.14159265f * 500.0f * (float)i / 48000.0f);
    for (p = 0; p < SP_NINT; p++) {
        double c1, c2;
        long t;
        float to = p == SP_PREDLY || p == SP_HPF ? 127.0f : (p == SP_TONE || p == SP_TENS ? 0.0f : 127.0f);
        float from = p == SP_TONE ? 127.0f : 64.0f;
        spring_init(s);
        spring_set(s, SP_DEC, 100);
        spring_set(s, p, from);
        spring_snap(s);
        for (t = 0; t < N; t += 32) {
            if (t == 96000) spring_set(s, p, to);
            if (t == 144000) spring_set(s, p, from);
            spring_process(s, x + t, x + t, yl + t, yr + t, 32);
        }
        c1 = click_ratio(yl, 96000, 120000, 72000, 96000);
        c2 = click_ratio(yl, 144000, 168000, 72000, 96000);
        printf(" %s %.2f/%.2f", nm[p], c1, c2);
        CHECK(c1 < 4.0 && c2 < 4.0, "%s: click ratio %.2f / %.2f", nm[p], c1, c2);
    }
    printf("\n");
    free(s); free(x); free(yl); free(yr);
}

/* MODE (2026-10-24): the map, MODE 64 = the init patch, the trims, no clicks on a sweep, cut-independence */
static void test_spring_mode(void)
{
    const long N = 48000 * 6;
    float *x = fbuf(N), *yl = fbuf(N), *yr = fbuf(N), *a[3];
    Spring *s = malloc(sizeof *s), *s2 = malloc(sizeof *s2);
    float v[SP_NMODE], w[SP_NMODE];
    static const float anch[3] = { 0, 64, 127 };
    long i;
    int k, j, bad = 0, nonmono = 0;
    uint32_t r = 99u;
    printf("spring: MODE\n");
    for (k = 0; k < 3; k++) {
        spring_mode_map(anch[k], v);
        for (j = 0; j < SP_NMODE; j++) if (v[j] != SP_MV[k * SP_NMODE + j]) bad++;
    }
    CHECK(bad == 0, "MODE map: %d values off at the anchors", bad);
    /* the drip and the spread rise, the tension falls (the round trips lengthen), all the way */
    spring_mode_map(0, w);
    for (k = 1; k <= 254; k++) {
        spring_mode_map(0.5f * (float)k, v);
        if (v[0] < w[0] || v[1] > w[1] || v[2] < w[2]) nonmono++;
        memcpy(w, v, sizeof v);
    }
    CHECK(nonmono == 0, "MODE map: DRIP, TENS or the spread not monotonic at %d steps", nonmono);
    for (k = 0; k <= 127; k += 16) {
        spring_mode_map((float)k, v);
        printf("  MODE %3d: DRIP %5.1f TENS %5.1f (%4.1f ms) spread %.3f\n", k, v[0], v[1], spring_tens_ms(v[1]), v[2]);
    }
    /* MODE 64 is the init patch, bit for bit */
    for (i = 0; i < N; i++) x[i] = (i % 24000) < 4800 ? 0.3f * fx_rands(&r) : 0.0f;
    for (k = 0; k < 3; k++) a[k] = fbuf(2 * N);
    spring_init(s);
    spring_snap(s);
    sp_run(s, x, a[0], a[0] + N, N);
    spring_init(s);
    spring_set(s, SP_MODE, 64);
    spring_snap(s);
    sp_run(s, x, a[1], a[1] + N, N);
    CHECK(!memcmp(a[0], a[1], sizeof(float) * 2 * N), "MODE 64 differs from the init patch");
    printf("  MODE 64 against the init patch: %s\n", memcmp(a[0], a[1], sizeof(float) * 2 * N) ? "DIFFERS" : "bit-identical");
    /* character only: at every MODE, PRE, DEC, DWEL, HPF, TONE and WDTH give the targets they give without MODE,
       and MODE 127 is DRIP 127 and TENS 16 */
    {
        static const float kv[6][7] = { { 0, 0, 0, 0, 0, 0, 0 }, { 127, 127, 127, 127, 127, 127, 127 },
                                        { 40, 24, 100, 50, 24, 96, 0 }, { 100, 112, 20, 0, 110, 10, 0 },
                                        { 64, 64, 64, 0, 127, 96, 0 }, { 13.7f, 87.2f, 3.3f, 99.9f, 61.5f, 30, 0 } };
        static const int kp[6] = { SP_PREDLY, SP_DEC, SP_DWEL, SP_HPF, SP_TONE, SP_WDTH, -1 };
        int nd = 0, m;
        for (m = 0; m <= 127; m += 9) {
            for (k = 0; k < 6; k++) {
                spring_init(s); spring_init(s2);
                spring_set(s, SP_MODE, (float)m);
                for (j = 0; j < 6; j++) { spring_set(s, kp[j], kv[k][j]); spring_set(s2, kp[j], kv[k][j]); }
                spring_snap(s); spring_snap(s2);
                for (j = 0; j < 6; j++) if (s->tgt[kp[j]] != s2->tgt[kp[j]]) nd++;
            }
        }
        CHECK(nd == 0, "MODE moved %d targets of PRE, DEC, DWEL, HPF, TONE or WDTH", nd);
        spring_init(s); spring_set(s, SP_MODE, 127); spring_snap(s);
        spring_init(s2); spring_set(s2, SP_DRIP, 127); spring_set(s2, SP_TENS, 16); spring_snap(s2);
        CHECK(s->tgt[SP_DRIP] == s2->tgt[SP_DRIP] && s->tgt[SP_TENS] == s2->tgt[SP_TENS] && s->spr == 1.2f,
              "MODE 127: DRIP %g/%g TENS %g/%g spread %g", s->tgt[SP_DRIP], s2->tgt[SP_DRIP], s->tgt[SP_TENS], s2->tgt[SP_TENS], s->spr);
    }
    /* a MODE sweep on a held sine: no clicks (TENS bends the pitch on the way, as its own knob does) */
    for (i = 0; i < N; i++) x[i] = 0.05f * sinf(2.0f * 3.14159265f * 500.0f * (float)i / 48000.0f);
    {
        double c1, c2, c3;
        long t0;
        spring_init(s);
        spring_set(s, SP_DEC, 100);
        spring_set(s, SP_MODE, 0);
        spring_snap(s);
        for (t0 = 0; t0 < N; t0 += 32) {
            if (t0 >= 96000 && t0 < 192000) spring_set(s, SP_MODE, 127.0f * (float)(t0 - 96000) / 96000.0f);
            if (t0 == 240000) spring_set(s, SP_MODE, 0);
            spring_process(s, x + t0, x + t0, yl + t0, yr + t0, 32);
        }
        c1 = click_ratio(yl, 96000, 192000, 72000, 96000);
        c2 = click_ratio(yl, 240000, 264000, 72000, 96000);
        /* the jump back is TENS 16 -> 110 inside: the tail bends up (the slew limit), as the same TENS jump does */
        spring_init(s);
        spring_set(s, SP_DEC, 100);
        spring_set(s, SP_TENS, 16);
        spring_set(s, SP_DRIP, 127);
        spring_snap(s);
        for (t0 = 0; t0 < N; t0 += 32) {
            if (t0 == 240000) { spring_set(s, SP_TENS, 110); spring_set(s, SP_DRIP, 30); }
            spring_process(s, x + t0, x + t0, yl + t0, yr + t0, 32);
        }
        c3 = click_ratio(yl, 240000, 264000, 72000, 96000);
        printf("  MODE sweep 0 -> 127 over 2 s: click ratio %.2f; jump 127 -> 0: %.2f (the same jump on DRIP and TENS\n"
               "  without MODE: %.2f, the bend of the round trips shortening)\n", c1, c2, c3);
        /* MODE also moves the spread, so its springs travel further than with TENS alone: a longer bend */
        CHECK(c1 < 2.0 && c2 < 1.5 * c3, "MODE: click ratio %.2f / %.2f (DRIP and TENS: %.2f)", c1, c2, c3);
    }
    /* cut-independence with MODE moving */
    {
        Change ch[] = { { 48000, SP_MODE, 0 }, { 96000, SP_MODE, 127 }, { 144000, SP_MODE, 85 }, { 192000, SP_TONE, 30 } };
        int m;
        for (i = 0; i < N; i++) x[i] = (i % 24000) < 4800 ? 0.5f * fx_rands(&r) : 0.0f;
        for (k = 0; k < 4; k++) ch[k].at = ch[k].at / 32 * 32;
        g_spx = x;
        for (m = 0; m < 3; m++) {
            g_spl = a[m]; g_spr = a[m] + N;
            spring_init(s);
            spring_set(s, SP_MODE, 40);
            spring_snap(s);
            cut_loop(N, m, ch, 4, sp_proc, sp_setp, s);
        }
        CHECK(!memcmp(a[0], a[1], sizeof(float) * 2 * N) && !memcmp(a[0], a[2], sizeof(float) * 2 * N),
              "MODE: the output depends on how the stream is cut");
        printf("  MODE moving: 32-sample, long and random calls %s\n",
               !memcmp(a[0], a[1], sizeof(float) * 2 * N) && !memcmp(a[0], a[2], sizeof(float) * 2 * N) ? "bit-identical" : "DIFFER");
    }
    for (k = 0; k < 3; k++) free(a[k]);
    free(x); free(yl); free(yr); free(s); free(s2);
}

static void test_spring(void)
{
    test_spring_laws();
    test_spring_basics();
    test_spring_decay();
    test_spring_dispersion();
    test_spring_smooth();
    test_spring_determinism();
    test_spring_mode();
    test_spring_extremes();
}
