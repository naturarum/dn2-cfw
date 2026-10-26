/* SPDX-License-Identifier: GPL-2.0-or-later */
/*
 * test_spaceecho.c - tests for the tape echo (T5.2), part of fxtest: test.c includes
 * this file after its helpers (fbuf, rms, mag_db, click_ratio, sine_residual,
 * sine_delay, cut_loop, CHECK). Not built on its own.
 */

/* one clean head: no feedback, no wear, no filter, MONO at full width (each output side
   plays its own track) */
static void se_clean(SpaceEcho *s, float time, float bpm, float mode)
{
    spaceecho_init(s, 7u);
    spaceecho_set_bpm(s, bpm);
    spaceecho_set(s, SE_TIME, time);
    spaceecho_set(s, SE_MODE, mode);
    spaceecho_set(s, SE_INT, 0);
    spaceecho_set(s, SE_AGE, 0);
    spaceecho_set(s, SE_BW, 127);
    spaceecho_set(s, SE_WID, 127);
    spaceecho_set(s, SE_PAN, SE_PAN_MONO);
    spaceecho_snap(s);
}

/* the saturator at AGE 0 (drive 1, trim 1), the same float operations as se_run */
static float se_sat_ref(float x)
{
    float y = x * 1.0f;
    FX_CLAMP(y, -1.5f, 1.5f);
    return (y - 0.14814815f * y * y * y) * 1.0f;
}

/* the wet ceiling (SE_LIM_* in spaceecho.c), the same float operations as se_run */
static float se_lim_ref(float v)
{
    float x = v;
    FX_ABS(x);
    if (x > 0.5f) {
        x = x - 0.5f;
        if (x > 0.4f) x = 0.4f;
        x = (0.5f + x) - 1.25f * x * x;
        v = v < 0.0f ? -x : x;
    }
    return v;
}
#define SE_CEIL 0.7000001

/* ---------- TIME: head positions, fractional delay, the clamp to the tape ---------- */

static void test_se_time(void)
{
    static const struct { float time, bpm; } cases[] = {
        { 31, 120 }, { 15, 120 }, { 0, 300 }, { 47.5f, 120 }, { 63, 97 }, { 127, 30 }, { 127, 60 }
    };
    SpaceEcho *s = malloc(sizeof *s);
    const long N = 3 * 170000 + 4000;
    float *x = fbuf(N), *yl = fbuf(N), *yr = fbuf(N);
    size_t c;
    int h;
    long n;
    printf("tape echo TIME: heads at 1x, 2x, 3x the head-1 time (impulse peak; 500 Hz phase for fractional delays);\n"
           "  head 1 is clamped to %.0f samples (3.54 s) so head 3 fits on the tape\n", (double)SE_DMAX);
    for (c = 0; c < sizeof cases / sizeof cases[0]; c++) {
        double D = spaceecho_time_samples(cases[c].time, cases[c].bpm), want;
        want = (cases[c].time + 1.0) * 90000.0 / cases[c].bpm;
        if (want > SE_DMAX) want = SE_DMAX;
        CHECK(fabs(D - want) < 1e-3 * want, "TIME %.1f @ %.0f: %.3f samples, expected %.3f", cases[c].time, cases[c].bpm, D, want);
        printf("  TIME %5.1f @ %3.0f BPM: head 1 %10.3f samples (%.4f s)%s;", cases[c].time, cases[c].bpm, D, D / 48000.0,
               want < (cases[c].time + 1.0) * 90000.0 / cases[c].bpm ? " CLAMPED" : "");
        for (h = 0; h < 3; h++) {
            double Dh = D * (h + 1), pk = 0, est, err, w;
            long at = -1, M = (long)Dh + 12000;
            if (M > N) M = N;
            se_clean(s, cases[c].time, cases[c].bpm, (float)h);   /* modes 0, 1, 2: head h+1 alone */
            memset(x, 0, sizeof(float) * (size_t)N);
            x[0] = 1.0f;
            spaceecho_process(s, x, x, yl, yr, (int)M);
            for (n = 0; n < M; n++) if (fabs(yl[n]) > pk) { pk = fabs(yl[n]); at = n; }
            err = 0;
            if (fabs(Dh - floor(Dh + 0.5)) > 1e-3) {
                se_clean(s, cases[c].time, cases[c].bpm, (float)h);
                w = 2 * M_PI * 500.0 / 48000.0;
                for (n = 0; n < M; n++) x[n] = (float)(0.01 * sin(w * n));
                spaceecho_process(s, x, x, yl, yr, (int)M);
                est = sine_delay(yl, M - 9600, M, 500.0);
                err = fabs(est - fmod(Dh, 96.0));
                if (err > 48) err = 96 - err;
                CHECK(err < 0.02, "head %d fractional delay error %.4f samples", h + 1, err);
                CHECK(labs(at - (long)Dh) <= 1, "head %d impulse at %ld, expected %.3f", h + 1, at, Dh);
            } else {
                CHECK(at == (long)floor(Dh + 0.5), "head %d impulse at %ld, expected %.0f", h + 1, at, Dh);
            }
            printf(" head %d at %ld%s", h + 1, at, err > 0 ? "" : "");
            if (err > 0) printf(" (phase err %.4f)", err);
        }
        printf("\n");
    }
    free(s); free(x); free(yl); free(yr);
}

/* ---------- MODE: which heads sound ---------- */

static void test_se_modes(void)
{
    static const char *names[7] = { "1", "2", "3", "1+2", "2+3", "1+3", "1+2+3" };
    static const int expect[7] = { 1, 2, 4, 3, 6, 5, 7 };
    SpaceEcho *s = malloc(sizeof *s);
    const long D = 6000, N = 3 * 6000 + 2000;   /* TIME 7 at 120 BPM */
    float *x = fbuf(N), *yl = fbuf(N), *yr = fbuf(N);
    const float v = se_sat_ref(0.01f);
    int m, k;
    long n;
    printf("tape echo MODE: an impulse, INTENSITY 0: echoes exactly at the selected heads (D, 2D, 3D), nothing else\n");
    x[0] = 0.01f;
    for (m = 0; m < 7; m++) {
        int got = 0, stray = 0, ok;
        se_clean(s, 7, 120, (float)m);
        spaceecho_process(s, x, x, yl, yr, (int)N);
        for (n = 0; n < N; n++) {
            int head = (n == D) ? 0 : (n == 2 * D) ? 1 : (n == 3 * D) ? 2 : -1;
            if (head >= 0 && yl[n] == v && yr[n] == v) got |= 1 << head;
            else if (yl[n] != 0.0f || yr[n] != 0.0f) stray++;
        }
        ok = got == expect[m] && stray == 0 && spaceecho_heads((float)m) == expect[m];
        printf("  MODE %d (heads %-5s): echoes at", m, names[m]);
        for (k = 0; k < 3; k++) if (got & (1 << k)) printf(" %ldD", (long)k + 1);
        printf("; other non-zero samples %d: %s\n", stray, ok ? "right" : "WRONG");
        CHECK(ok, "MODE %d: heads %d (expected %d), %d stray samples", m, got, expect[m], stray);
    }
    free(s); free(x); free(yl); free(yr);
}

/* ---------- transparency: BW 127 is no filter; INTENSITY 0 records the input as it is ---------- */

static void test_se_transparent(void)
{
    SpaceEcho *s = malloc(sizeof *s);
    const long N = 48000 * 3, D = 6000;
    float *x = fbuf(N), *xr = fbuf(N), *yl = fbuf(N), *yr = fbuf(N), *zl = fbuf(N), *zr = fbuf(N);
    uint32_t r = 3u;
    long n, same = 0, bad = 0;
    double dmax = 0, sig = 0;
    printf("tape echo transparency: BW 127, AGE 0, INTENSITY 0: the echo is the input through the tape saturator,\n"
           "  bit for bit; at BW 127 CENTER changes nothing even with feedback (the filter is out of the path)\n");
    for (n = 0; n < N; n++) { x[n] = 0.5f * fx_rands(&r); xr[n] = 0.5f * fx_rands(&r); }
    se_clean(s, 7, 120, 0);
    spaceecho_process(s, x, xr, yl, yr, (int)N);
    for (n = D; n < N; n++) {
        if (yl[n] == se_sat_ref(x[n - D]) && yr[n] == se_sat_ref(xr[n - D])) same++;
        else bad++;
    }
    printf("  INTENSITY 0, white noise at -6 dBFS: %ld of %ld echo samples equal sat(input), %ld differ\n", same, N - D, bad);
    CHECK(bad == 0, "INTENSITY 0 echo differs from sat(input) in %ld samples", bad);
    /* at -40 dBFS the saturator is the only change, far below hearing */
    for (n = 0; n < N; n++) { x[n] *= 0.02f; xr[n] *= 0.02f; }
    se_clean(s, 7, 120, 0);
    spaceecho_process(s, x, xr, yl, yr, (int)N);
    for (n = D; n < N; n++) {
        double d = fabs(yl[n] - x[n - D]);
        if (d > dmax) dmax = d;
        sig += (double)x[n - D] * x[n - D];
    }
    sig = sqrt(sig / (N - D));
    printf("  the same at -40 dBFS RMS: largest difference from the plain delayed input %.1f dB under the input RMS\n",
           20 * log10(sig / (dmax + 1e-30)));
    CHECK(20 * log10(sig / (dmax + 1e-30)) > 90, "not transparent at -40 dBFS: %.1f dB", 20 * log10(sig / (dmax + 1e-30)));
    /* BW 127: CENTER 0 and 127 identical, with feedback, three heads, ALT */
    {
        int k, ident = 1;
        for (k = 0; k < 2; k++) {
            se_clean(s, 7, 120, 6);
            spaceecho_set(s, SE_INT, 60);
            spaceecho_set(s, SE_PAN, SE_PAN_ALT);
            spaceecho_set(s, SE_CTR, k ? 127.0f : 0.0f);
            spaceecho_snap(s);
            spaceecho_process(s, x, xr, k ? zl : yl, k ? zr : yr, (int)N);
        }
        ident = !memcmp(yl, zl, sizeof(float) * (size_t)N) && !memcmp(yr, zr, sizeof(float) * (size_t)N);
        printf("  BW 127, INTENSITY 60, heads 1+2+3, ALT: CENTER 0 vs 127 %s\n", ident ? "bit-identical" : "DIFFER");
        CHECK(ident, "BW 127 still filters (CENTER changes the output)");
        se_clean(s, 7, 120, 6);
        spaceecho_set(s, SE_INT, 60); spaceecho_set(s, SE_PAN, SE_PAN_ALT); spaceecho_set(s, SE_CTR, 0); spaceecho_set(s, SE_BW, 126);
        spaceecho_snap(s);
        spaceecho_process(s, x, xr, zl, zr, (int)N);
        ident = !memcmp(yl, zl, sizeof(float) * (size_t)N);
        printf("  BW 126 (the fade starts): %s\n", ident ? "the same (WRONG)" : "differs, as it should");
        CHECK(!ident, "BW 126 does not filter");
    }
    free(s); free(x); free(xr); free(yl); free(yr); free(zl); free(zr);
}

/* ---------- the band filter: one pass, CENTER and BW ---------- */

static void test_se_band(void)
{
    static const float bws[] = { 0, 16, 32, 64, 96, 112, 118 };
    static const float ctrs[] = { 64, 30, 100 };
    SpaceEcho *s = malloc(sizeof *s);
    const long D = 24000, L = 16384, N = 24000 + 16384;
    float *x = fbuf(N), *yl = fbuf(N), *yr = fbuf(N);
    size_t b, c;
    int k;
    printf("tape echo band filter, one pass (first echo of an impulse, INTENSITY 0): peak, its frequency, the -3 dB\n"
           "  width around the peak, the gain at CENTER, and the worst gain 2 octaves outside the band edges\n");
    x[0] = 0.01f;
    CHECK(fabs(spaceecho_bw_oct(0) - 0.5) < 1e-4 && fabs(spaceecho_bw_oct(127) - 20.0) < 1e-3, "BW law ends %.4f %.4f",
          spaceecho_bw_oct(0), spaceecho_bw_oct(127));
    CHECK(fabs(spaceecho_center_hz(0) - 50.0) < 0.01 && fabs(spaceecho_center_hz(127) - 12800.0) < 1.0, "CENTER law ends");
    CHECK(fabs(spaceecho_q(0) - 3.5) < 0.01 && spaceecho_q(96) < 0.7072 && spaceecho_q(127) < 0.7072, "Q law %.3f %.3f",
          spaceecho_q(0), spaceecho_q(96));
    for (c = 0; c < sizeof ctrs / sizeof ctrs[0]; c++) {
        double C = spaceecho_center_hz(ctrs[c]);
        for (b = 0; b < sizeof bws / sizeof bws[0]; b++) {
            double B = spaceecho_bw_oct(bws[b]), pk = -1e9, fpk = 0, f, g, lo = 0, hi = 0, gc, out = -1e9, fl, fh;
            double m[241];
            if (c > 0 && bws[b] != 0 && bws[b] != 32 && bws[b] != 96) continue;
            se_clean(s, 31, 120, 0);
            spaceecho_set(s, SE_CTR, ctrs[c]);
            spaceecho_set(s, SE_BW, bws[b]);
            spaceecho_snap(s);
            spaceecho_process(s, x, x, yl, yr, (int)N);
            for (k = 0; k <= 240; k++) {
                f = 20.0 * pow(2.0, k / 24.0);              /* 20 Hz .. 20.5 kHz, quarter-tone steps */
                if (f > 21000) { m[k] = -300; continue; }
                g = mag_db(yl + D, L, f) - 20 * log10(0.01);
                m[k] = g;
                if (g > pk) { pk = g; fpk = f; }
            }
            for (k = 0; k <= 240; k++) if (m[k] >= pk - 3.0) { lo = 20.0 * pow(2.0, k / 24.0); break; }
            for (k = 240; k >= 0; k--) if (m[k] >= pk - 3.0) { hi = 20.0 * pow(2.0, k / 24.0); break; }
            gc = mag_db(yl + D, L, C) - 20 * log10(0.01);
            fl = C * pow(2.0, -B / 2) / 4.0;
            fh = C * pow(2.0, B / 2) * 4.0;
            if (fl > 25) out = mag_db(yl + D, L, fl) - 20 * log10(0.01);
            if (fh < 18000) { g = mag_db(yl + D, L, fh) - 20 * log10(0.01); if (g > out) out = g; }
            printf("  CTR %3.0f (%5.0f Hz) BW %3.0f (%5.2f oct, Q %.2f): peak %+5.1f dB at %5.0f Hz, -3 dB %5.0f..%5.0f Hz (%5.2f oct),"
                   " at CTR %+5.1f dB", ctrs[c], C, bws[b], B, spaceecho_q(bws[b]), pk, fpk, lo, hi, log2(hi / lo), gc);
            if (out > -1e8) printf(", 2 oct outside %+6.1f dB", out);
            printf("\n");
            if (bws[b] == 0) {
                CHECK(log2(hi / lo) < 1.0 && log2(hi / lo) > 0.4, "BW 0 width %.2f oct", log2(hi / lo));
                CHECK(pk > 4.5 && pk < 7.5, "BW 0 peak %+.1f dB (want about +6)", pk);
                CHECK(fabs(log2(fpk / C)) < 0.35, "BW 0 peak at %.0f Hz, CENTER %.0f", fpk, C);
            }
            if (bws[b] <= 64) CHECK(pk < 7.5, "BW %.0f peak %+.1f dB", bws[b], pk);
            if (bws[b] <= 64 && out > -1e8) CHECK(out < -15, "BW %.0f: 2 octaves outside the band only %+.1f dB", bws[b], out);
            if (bws[b] >= 96) CHECK(pk < 0.3, "BW %.0f resonates: peak %+.1f dB", bws[b], pk);
            if (bws[b] == 118 && c == 0) {
                double worst = 0;
                for (k = 0; k <= 240; k++) {
                    f = 20.0 * pow(2.0, k / 24.0);
                    if (f >= 40 && f <= 16000 && fabs(m[k]) > worst) worst = fabs(m[k]);
                }
                printf("    BW 118 (just before the fade to no filter): within %.2f dB from 40 Hz to 16 kHz\n", worst);
                CHECK(worst < 0.5, "BW 118 not nearly open: %.2f dB", worst);
            }
        }
    }
    free(s); free(x); free(yl); free(yr);
}

/* ---------- INTENSITY: per-head loop gain; full feedback, narrow band, three heads: sings, bounded ---------- */

static void test_se_intensity(void)
{
    SpaceEcho *s = malloc(sizeof *s);
    const long N = 48000 * 8;
    float *x = fbuf(N), *yl = fbuf(N), *yr = fbuf(N);
    long n;
    int m;
    printf("tape echo INTENSITY: echo-to-echo ratio of one head = the law; full INTENSITY with a narrow band held\n");
    /* the stock FDBK law (SC01): FDBK / 64, exact; 64 = 100 %, 127 = 198 % (shared by the playing heads since SC02) */
    CHECK(spaceecho_fb(0) == 0.0f && spaceecho_fb(-3) == 0.0f && spaceecho_fb(64) == 1.0f
          && spaceecho_fb(127) == 127.0f / 64.0f && spaceecho_fb(300) == 127.0f / 64.0f && spaceecho_fb(32.5f) == 32.5f / 64.0f,
          "INT law %g %g %g %g", spaceecho_fb(0), spaceecho_fb(64), spaceecho_fb(127), spaceecho_fb(300));
    {
        static const float iv[] = { 32, 64, 100 };
        size_t k;
        for (k = 0; k < 3; k++) {
            double r2;
            se_clean(s, 7, 120, 0);
            spaceecho_set(s, SE_INT, iv[k]);
            spaceecho_snap(s);
            memset(x, 0, sizeof(float) * (size_t)N);
            for (n = 0; n < 480; n++) x[n] = (float)(0.01 * sin(2 * M_PI * 1000.0 * n / 48000.0));
            spaceecho_process(s, x, x, yl, yr, 30000);
            r2 = rms(yl, 12000, 12480) / rms(yl, 6000, 6480);
            printf("  INT %3.0f: second echo / first %.4f, law %.4f\n", iv[k], r2, spaceecho_fb(iv[k]));
            CHECK(fabs(r2 / spaceecho_fb(iv[k]) - 1) < 0.01, "INT %.0f: echo ratio %.4f vs %.4f", iv[k], r2, spaceecho_fb(iv[k]));
        }
    }
    for (m = 0; m < 2; m++) {
        double pk = 0, tail;
        se_clean(s, 7, 120, 6);
        spaceecho_set(s, SE_INT, 127);
        spaceecho_set(s, SE_BW, m ? 127.0f : 0.0f);
        spaceecho_set(s, SE_AGE, m ? 0.0f : 64.0f);
        spaceecho_snap(s);
        memset(x, 0, sizeof(float) * (size_t)N);
        for (n = 0; n < 4800; n++) x[n] = 0.5f * (float)sin(2 * M_PI * 300.0 * n / 48000.0);
        spaceecho_process(s, x, x, yl, yr, (int)N);
        for (n = 0; n < N; n++) { if (fabs(yl[n]) > pk) pk = fabs(yl[n]); if (fabs(yr[n]) > pk) pk = fabs(yr[n]); }
        tail = rms(yl, N - 48000, N);
        printf("  heads 1+2+3, INT 127 (198 %%, 66 %% per head), %s: peak %.3f, RMS of the last second %.3f (held by the saturator)\n",
               m ? "BW 127, AGE 0" : "BW 0 (narrow, resonant), AGE 64", pk, tail);
        CHECK(pk <= SE_CEIL && tail > 0.05, "runaway: peak %.3f, tail %.3f", pk, tail);
        CHECK(fabs(rms(yl, N - 48000, N) - rms(yl, N - 96000, N - 48000)) < 0.5 * tail, "runaway level unsteady");
    }
    free(s); free(x); free(yl); free(yr);
}

/* ---------- INTENSITY shared by the playing heads (SC02): 64 is endless in every MODE ---------- */

static void test_se_share(void)
{
    SpaceEcho *s = malloc(sizeof *s);
    const long D = 6000, N = 48000 * 8;
    float *x = fbuf(N), *yl = fbuf(N), *yr = fbuf(N);
    /* per MODE: the head count, the window of the first echo and of the first pass that the feedback adds (in D),
       and whether a head's own first echo lands there too (MODE 1+2, 1+2+3 at 2D: 1 + the pass) */
    static const struct { int heads, first, pass, plus1; } md[7] = {
        { 1, 1, 2, 0 }, { 1, 2, 4, 0 }, { 1, 3, 6, 0 }, { 2, 1, 2, 1 }, { 2, 2, 4, 0 }, { 2, 1, 2, 0 }, { 3, 1, 2, 1 } };
    static const float iv[] = { 32, 64, 100 };
    long n;
    int m;
    size_t k;
    printf("tape echo INTENSITY shared (SC02): the playing heads share FDBK/64 (each 1/n of it), so a pass is FDBK/64/n\n"
           "  per head and FDBK 64 holds in every MODE (a 1 kHz burst at 0.01; TIME 7 at 120 BPM: D = 6000 samples)\n");
    for (m = 0; m < 7; m++) {
        printf("  MODE %d:", m + 1);
        for (k = 0; k < 3; k++) {
            double r, want = (double)spaceecho_fb(iv[k]) / md[m].heads + md[m].plus1;
            se_clean(s, 7, 120, (float)m);
            spaceecho_set(s, SE_INT, iv[k]);
            spaceecho_snap(s);
            memset(x, 0, sizeof(float) * (size_t)N);
            for (n = 0; n < 480; n++) x[n] = (float)(0.01 * sin(2 * M_PI * 1000.0 * n / 48000.0));
            spaceecho_process(s, x, x, yl, yr, (int)(D * md[m].pass + 1000));
            r = rms(yl, D * md[m].pass, D * md[m].pass + 480) / rms(yl, D * md[m].first, D * md[m].first + 480);
            printf(" FDBK %3.0f %.4f (want %.4f)", iv[k], r, want);
            CHECK(fabs(r / want - 1) < 0.01, "MODE %d FDBK %.0f: pass ratio %.4f vs %.4f", m + 1, iv[k], r, want);
        }
        printf("\n");
    }
    /* the level 3..4 s after the burst against 6..7 s: 64 holds, 56 dies away, 72 grows, in every MODE */
    for (m = 0; m < 7; m++) {
        static const float hv[] = { 56, 64, 72 };
        printf("  MODE %d, level change from 3-4 s to 6-7 s:", m + 1);
        for (k = 0; k < 3; k++) {
            double a, b, db;
            se_clean(s, 7, 120, (float)m);
            spaceecho_set(s, SE_INT, hv[k]);
            spaceecho_snap(s);
            memset(x, 0, sizeof(float) * (size_t)N);
            for (n = 0; n < 480; n++) x[n] = (float)(0.01 * sin(2 * M_PI * 1000.0 * n / 48000.0));
            spaceecho_process(s, x, x, yl, yr, (int)N);
            a = rms(yl, 3 * 48000, 4 * 48000);
            b = rms(yl, 6 * 48000, 7 * 48000);
            db = 20 * log10(b / a);
            printf(" FDBK %2.0f %+.2f dB", hv[k], db);
            if (k == 0) CHECK(db < -2.0, "MODE %d FDBK 56: %+.2f dB, not dying away", m + 1, db);
            if (k == 1) CHECK(fabs(db) < 0.5, "MODE %d FDBK 64: %+.2f dB, not holding", m + 1, db);
            if (k == 2) CHECK(db > 2.0, "MODE %d FDBK 72: %+.2f dB, not growing", m + 1, db);
        }
        printf("\n");
    }
    free(s); free(x); free(yl); free(yr);
}

/* ---------- the wet ceiling (T5.3 review): the curve, and three heads held running away ---------- */

static void test_se_ceiling(void)
{
    SpaceEcho *s = malloc(sizeof *s);
    const long D = 6000, N = 48000 * 5, H = 48000;
    float *x = fbuf(N), *yl = fbuf(N), *yr = fbuf(N);
    long n, same = 0, under = 0, mono_bad = 0, ulp = 0;
    int p;
    float prev = 0.0f;
    printf("tape echo wet ceiling: one head, INTENSITY 0, a ramp 0..1.5 into the tape: the echo is lim(sat(input)),\n"
           "  bit for bit, rises monotonically, and is the plain sat(input) up to the knee 0.5; then MODE 1+2+3 at\n"
           "  INTENSITY 127 (198 %%, 66 %% per head), BW 127, AGE 0, a held tone and 3 s of silence, in MONO, ALT and RND at full width\n");
    for (n = 0; n < N; n++) x[n] = 1.5f * (float)n / (float)N;
    se_clean(s, 7, 120, 0);
    spaceecho_process(s, x, x, yl, yr, (int)N);
    for (n = D; n < N; n++) {
        float want = se_lim_ref(se_sat_ref(x[n - D]));
        if (yl[n] == want && yr[n] == want) same++;
        if (yl[n] < prev - 1e-6f) mono_bad++;            /* float rounding where the slope is ~0: one ulp */
        if (yl[n] < prev && yl[n] >= prev - 1e-6f) ulp++;
        prev = yl[n];
        if (se_sat_ref(x[n - D]) <= 0.5f && yl[n] != se_sat_ref(x[n - D])) under++;
    }
    printf("  ramp: %ld of %ld echo samples = lim(sat(input)); %ld steps down (and %ld of at most 1e-6, rounding where\n"
           "  the curve is flat); %ld changed below the knee; last %.6f\n",
           same, N - D, mono_bad, ulp, under, (double)yl[N - 1]);
    CHECK(same == N - D && mono_bad == 0 && under == 0, "ceiling curve: %ld equal, %ld down, %ld below the knee changed",
          same, mono_bad, under);
    CHECK(fabs(yl[N - 1] - 0.7) < 1e-6, "the ceiling is %.7f, not 0.7", (double)yl[N - 1]);
    for (p = 0; p < 3; p++) {
        double pk = 0, tail = 0;
        se_clean(s, 10, 120, 6);
        spaceecho_set(s, SE_INT, 127);
        spaceecho_set(s, SE_PAN, (float)p);
        spaceecho_snap(s);
        memset(x, 0, sizeof(float) * (size_t)N);
        for (n = 0; n < H; n++) x[n] = 0.13f * (float)sin(2 * M_PI * 220.0 * n / 48000.0);
        spaceecho_process(s, x, x, yl, yr, (int)N);
        for (n = 0; n < N; n++) { if (fabs(yl[n]) > pk) pk = fabs(yl[n]); if (fabs(yr[n]) > pk) pk = fabs(yr[n]); }
        for (n = N - 48000; n < N; n++) { if (fabs(yl[n]) > tail) tail = fabs(yl[n]); if (fabs(yr[n]) > tail) tail = fabs(yr[n]); }
        printf("  MODE 1+2+3, INT 127, %s: peak %.5f, peak of the last second %.5f (held; 3.00 / 4.24 / 3.98 before the ceiling)\n",
               p == 0 ? "MONO" : p == 1 ? "ALT " : "RND ", pk, tail);
        CHECK(pk <= SE_CEIL && tail > 0.6, "held runaway, PAN %d: peak %.5f, last second %.5f", p, pk, tail);
    }
    free(s); free(x); free(yl); free(yr);
}

/* ---------- PAN and WIDTH ---------- */

static void test_se_pan(void)
{
    SpaceEcho *s = malloc(sizeof *s);
    const long D = 6000, N = 48000 * 12;
    float *xl = fbuf(N), *xr = fbuf(N), *yl = fbuf(N), *yr = fbuf(N), *zl = fbuf(N), *zr = fbuf(N);
    const float v = se_sat_ref(0.01f), vh = se_sat_ref(0.005f);
    int ok;
    long n;
    printf("tape echo PAN and WIDTH: a left-only impulse (0.01)\n");
    xl[0] = 0.01f;
    /* MONO, WIDTH 127: stereo as it comes; WIDTH 0: mono */
    se_clean(s, 7, 120, 0);
    spaceecho_process(s, xl, xr, yl, yr, 3 * (int)D);
    ok = yl[D] == v && yr[D] == 0.0f;
    printf("  MONO, WIDTH 127: echo L %.5f R %.5f: %s\n", yl[D], yr[D], ok ? "left only (the input's own stereo)" : "WRONG");
    CHECK(ok, "MONO width 127");
    se_clean(s, 7, 120, 0); spaceecho_set(s, SE_WID, 0); spaceecho_snap(s);
    spaceecho_process(s, xl, xr, yl, yr, 3 * (int)D);
    ok = fabs(yl[D] - 0.5 * v) < 1e-9 && yl[D] == yr[D];
    printf("  MONO, WIDTH 0: echo L %.5f R %.5f: %s\n", yl[D], yr[D], ok ? "centred" : "WRONG");
    CHECK(ok, "MONO width 0");
    /* ALT, one head, INTENSITY 64: L, R, L */
    se_clean(s, 7, 120, 0); spaceecho_set(s, SE_PAN, SE_PAN_ALT); spaceecho_set(s, SE_INT, 64); spaceecho_snap(s);
    spaceecho_process(s, xl, xr, yl, yr, 4 * (int)D);
    /* the far side carries only the feedback DC blocker's tail (about 1e-11 here) */
    ok = fabs(yl[D] - 1.41421356 * vh) < 1e-7 && yr[D] == 0.0f && fabs(yl[2 * D]) < 1e-8
         && fabs(yr[2 * D] / (yl[D] * spaceecho_fb(64)) - 1) < 0.01
         && fabs(yl[3 * D] / (yr[2 * D] * spaceecho_fb(64)) - 1) < 0.01 && fabs(yr[3 * D]) < 1e-8;
    printf("  ALT, head 1, WIDTH 127, INT 64: L %.6f %.6f %.6f, R %.6f %.6f %.6f: %s\n", yl[D], yl[2 * D], yl[3 * D],
           yr[D], yr[2 * D], yr[3 * D], ok ? "L, R, L, each x the INTENSITY gain (input summed to mono, +3 dB hard-panned)" : "WRONG");
    CHECK(ok, "ALT ping-pong");
    /* ALT, three heads, INTENSITY 0: head 1 left, head 2 right, head 3 left */
    se_clean(s, 7, 120, 6); spaceecho_set(s, SE_PAN, SE_PAN_ALT); spaceecho_snap(s);
    spaceecho_process(s, xl, xr, yl, yr, 4 * (int)D);
    ok = yl[D] > 0 && yr[D] == 0.0f && yl[2 * D] == 0.0f && yr[2 * D] > 0 && yl[3 * D] > 0 && yr[3 * D] == 0.0f;
    printf("  ALT, heads 1+2+3, INT 0: D: L %.5f R %.5f, 2D: L %.5f R %.5f, 3D: L %.5f R %.5f: %s\n", yl[D], yr[D],
           yl[2 * D], yr[2 * D], yl[3 * D], yr[3 * D], ok ? "heads alternate L, R, L" : "WRONG");
    CHECK(ok, "ALT heads");
    /* WIDTH 0 is the same mono echo in all three PAN modes (mono input, three heads) */
    {
        int k, same = 1;
        for (n = 0; n < N; n++) xr[n] = xl[n];
        for (k = 0; k < 3; k++) {
            se_clean(s, 7, 120, 6); spaceecho_set(s, SE_WID, 0); spaceecho_set(s, SE_PAN, (float)k); spaceecho_snap(s);
            spaceecho_process(s, xl, xr, k ? zl : yl, k ? zr : yr, 4 * (int)D);
            if (k && (memcmp(yl, zl, sizeof(float) * 4 * D) || memcmp(yr, zr, sizeof(float) * 4 * D))) same = 0;
        }
        printf("  WIDTH 0, mono input, heads 1+2+3: MONO, ALT and RND %s\n", same ? "bit-identical" : "DIFFER");
        CHECK(same, "WIDTH 0 differs between the PAN modes");
        memset(xr, 0, sizeof(float) * (size_t)N);
    }
    /* RND: jumps once per head period; positions spread; no clicks */
    {
        long blk, jumps[3] = { 0, 0, 0 };
        float last[3];
        int k;
        double bal_sd, m1 = 0, m2 = 0, r;
        long nw = 0;
        se_clean(s, 7, 120, 0); spaceecho_set(s, SE_PAN, SE_PAN_RND); spaceecho_snap(s);
        for (k = 0; k < 3; k++) last[k] = s->ptgt[k];
        for (n = 0; n < N; n++) xl[n] = (float)(0.3 * sin(2 * M_PI * 220.0 * n / 48000.0));
        for (blk = 0; blk < N / 32; blk++) {
            spaceecho_process(s, xl + 32 * blk, xl + 32 * blk, yl + 32 * blk, yr + 32 * blk, 32);
            for (k = 0; k < 3; k++) if (s->ptgt[k] != last[k]) { jumps[k]++; last[k] = s->ptgt[k]; }
        }
        for (n = 2 * D; n + 4800 <= N; n += 4800) {
            double b = 20 * log10(rms(yl, n, n + 4800) / rms(yr, n, n + 4800));
            if (b > 40) b = 40;
            if (b < -40) b = -40;
            m1 += b; m2 += b * b; nw++;
        }
        bal_sd = sqrt(m2 / nw - (m1 / nw) * (m1 / nw));
        r = click_ratio(yl, 2 * D, N, 2 * D, 2 * D + 4800);
        { double r2 = click_ratio(yr, 2 * D, N, 2 * D, 2 * D + 4800); if (r2 > r) r = r2; }
        printf("  RND, 12 s: position jumps per head %ld, %ld, %ld (expected %.0f, %.0f, %.0f: once per head period);\n"
               "    L/R balance of head 1 on a 220 Hz tone, 0.1 s windows: spread %.1f dB (s.d.); click detector %.2f\n",
               jumps[0], jumps[1], jumps[2], N / (double)D, N / (2.0 * D), N / (3.0 * D), bal_sd, r);
        CHECK(labs(jumps[0] - N / D) <= 1 && labs(jumps[1] - N / (2 * D)) <= 1 && labs(jumps[2] - N / (3 * D)) <= 1,
              "RND jump counts %ld %ld %ld", jumps[0], jumps[1], jumps[2]);
        CHECK(bal_sd > 3.0, "RND positions do not spread: %.1f dB", bal_sd);
        CHECK(r < 2.0, "RND clicks: %.2f", r);
    }
    free(s); free(xl); free(xr); free(yl); free(yr); free(zl); free(zr);
}

/* ---------- AGE: noise under a steady echo; wow and flutter per head ---------- */

static void test_se_age(void)
{
    SpaceEcho *s = malloc(sizeof *s);
    const long N = 48000 * 4;
    float *x = fbuf(N), *yl = fbuf(N), *yr = fbuf(N), *zl = fbuf(N), *zr = fbuf(N);
    static const float ages[] = { 0, 32, 64, 127 };
    size_t k;
    long n;
    printf("tape echo AGE: noise (hiss + crackle) under a steady echo (AGE 0..127; wow, flutter and top loss off\n"
           "  for this), AGE 0 independent of every noise seed, then wow and flutter per head\n");
    for (n = 0; n < N; n++) x[n] = (float)(0.005 * sin(2 * M_PI * 1000.0 * n / 48000.0));
    for (k = 0; k < 4; k++) {
        double sig, res;
        se_clean(s, 7, 120, 0);
        /* INT 17.5: 27 % per head on the stock law, the loop gain SE01's INT 50 gave (1.1 x^1.5); at 78 % the
           echo's own build-up over the window (a step every repeat) would count as noise here */
        spaceecho_set(s, SE_INT, 17.5f); spaceecho_set(s, SE_AGE, ages[k]);
        spaceecho_snap(s);
        s->t_wow = 0.0f; s->t_flut = 0.0f; s->wowA = 0.0f; s->flutA = 0.0f;   /* noise only */
        spaceecho_process(s, x, x, yl, yr, (int)N);
        sig = rms(yl, N - 96000, N);
        res = sine_residual(yl, N - 96000, N, 1000.0);
        printf("  AGE %3.0f: echo %.1f dBFS, noise %.1f dB under it\n", ages[k], 20 * log10(sig), 20 * log10(sig / res));
        if (ages[k] == 0) CHECK(20 * log10(sig / res) > 95, "AGE 0 noise only %.1f dB down", 20 * log10(sig / res));
        if (ages[k] == 127) CHECK(20 * log10(sig / res) > 40 && 20 * log10(sig / res) < 60, "AGE 127 noise %.1f dB down", 20 * log10(sig / res));
    }
    /* AGE 0: other seeds for every noise source, same output */
    se_clean(s, 7, 120, 6); spaceecho_set(s, SE_INT, 60); spaceecho_snap(s);
    spaceecho_process(s, x, x, yl, yr, 48000);
    se_clean(s, 7, 120, 6); spaceecho_set(s, SE_INT, 60); spaceecho_snap(s);
    s->hrng = 99u; s->crng = 12345u; s->rng = 777u;
    spaceecho_process(s, x, x, zl, zr, 48000);
    CHECK(!memcmp(yl, zl, 48000 * sizeof(float)) && !memcmp(yr, zr, 48000 * sizeof(float)), "AGE 0 output depends on the noise seeds");
    printf("  AGE 0 with other noise seeds: %s\n", memcmp(yl, zl, 48000 * sizeof(float)) ? "DIFFERS" : "bit-identical");
    /* wow and flutter: each head's offset M(t - tau_k) - M(t) and the pitch it plays at */
    {
        static const struct { float age, time; } wc[] = { { 64, 7 }, { 127, 7 }, { 64, 31 }, { 127, 31 } };
        size_t c;
        for (c = 0; c < 4; c++) {
            double mx[3] = { 0, 0, 0 }, px[3] = { 0, 0, 0 }, prev[3] = { 0, 0, 0 };
            long blk;
            int h;
            se_clean(s, wc[c].time, 120, 6);
            spaceecho_set(s, SE_AGE, wc[c].age);
            spaceecho_snap(s);
            for (blk = 0; blk < 20L * 1500; blk++) {
                spaceecho_process(s, x, x, yl, yr, 32);
                for (h = 0; h < 3; h++) {
                    double m = s->mod[h];
                    if (blk > 3000) {   /* after the first head-3 transit */
                        if (fabs(m) > mx[h]) mx[h] = fabs(m);
                        if (fabs(m - prev[h]) / 32.0 > px[h]) px[h] = fabs(m - prev[h]) / 32.0;
                    }
                    prev[h] = m;
                }
            }
            printf("  AGE %3.0f, head 1 at %5.0f samples: largest offset head 1/2/3 %.1f / %.1f / %.1f samples, largest pitch deviation"
                   " %.2f / %.2f / %.2f %%\n", wc[c].age, spaceecho_time_samples(wc[c].time, 120), mx[0], mx[1], mx[2],
                   100 * px[0], 100 * px[1], 100 * px[2]);
            for (h = 0; h < 3; h++)
                CHECK(px[h] < 3.2 * (SE_WOW_MAX + SE_FLUT_MAX) * (wc[c].age / 127.0) * (wc[c].age / 127.0) && px[h] > 0,
                      "head %d pitch deviation %.3f %%", h + 1, 100 * px[h]);
        }
    }
    free(s); free(x); free(yl); free(yr); free(zl); free(zr);
}

/* ---------- no clicks when a knob jumps ---------- */

static void test_se_smoothing(void)
{
    static const struct { int p; float v0, v1; float mode, pan, bw; double lim; const char *what; } c[] = {
        { SE_TIME, 31, 7, 0, 0, 127, 3.0, "TIME 31 -> 7" },
        { SE_TIME, 7, 31, 6, 1, 127, 3.0, "TIME 7 -> 31 (3 heads)" },
        { SE_MODE, 0, 6, 0, 0, 127, 2.0, "MODE 1 -> 1+2+3" },
        { SE_MODE, 6, 2, 0, 0, 127, 2.0, "MODE 1+2+3 -> 3" },
        { SE_INT, 0, 127, 0, 0, 127, 2.0, "INT 0 -> 127" },
        { SE_AGE, 0, 127, 0, 0, 127, 2.0, "AGE 0 -> 127" },
        { SE_CTR, 0, 127, 0, 0, 40, 2.0, "CTR 0 -> 127 (BW 40)" },
        { SE_BW, 127, 0, 0, 0, 127, 2.0, "BW 127 -> 0" },
        { SE_BW, 0, 127, 0, 0, 127, 2.0, "BW 0 -> 127" },
        { SE_WID, 127, 0, 6, 1, 127, 2.0, "WID 127 -> 0 (ALT)" },
        { SE_PAN, 0, 1, 6, 0, 127, 2.0, "PAN MONO -> ALT" },
        { SE_PAN, 1, 2, 6, 0, 127, 2.0, "PAN ALT -> RND" },
        { SE_PAN, 2, 0, 6, 0, 127, 2.0, "PAN RND -> MONO" },
    };
    const long N = 96000;
    float *x = fbuf(N), *yl = fbuf(N), *yr = fbuf(N);
    SpaceEcho *s = malloc(sizeof *s);
    size_t k;
    long i;
    double r;
    printf("tape echo smoothing: click detector after a knob jump (as for the other effects; TIME bends by design)\n");
    for (i = 0; i < N; i++) {
        double fade = i < 4800 ? 0.5 - 0.5 * cos(M_PI * i / 4800.0) : 1.0;
        /* 0.1, about the loudest the unit's input reaches the delay slot (0.13): at 0.3 (until T5.3) three
           heads pass the wet ceiling's knee, whose corners raise the reference window and hide a click */
        x[i] = (float)(0.1 * fade * sin(2 * M_PI * 220.0 * i / 48000.0));
    }
    for (k = 0; k < sizeof c / sizeof c[0]; k++) {
        se_clean(s, 31, 120, c[k].mode);
        spaceecho_set(s, SE_INT, 40);
        spaceecho_set(s, SE_PAN, c[k].pan);
        spaceecho_set(s, SE_BW, c[k].bw);
        spaceecho_set(s, SE_CTR, 64);
        spaceecho_set(s, c[k].p, c[k].v0);
        spaceecho_snap(s);
        spaceecho_process(s, x, x, yl, yr, 60000);
        spaceecho_set(s, c[k].p, c[k].v1);
        spaceecho_process(s, x + 60000, x + 60000, yl + 60000, yr + 60000, 36000);
        r = click_ratio(yl, 60000, 71000, 50000, 60000);
        { double r2 = click_ratio(yl, 60000, 71000, 88000, 96000); if (r2 < r) r = r2; }
        { double r3 = click_ratio(yr, 60000, 71000, 50000, 60000), r4 = click_ratio(yr, 60000, 71000, 88000, 96000);
          if (r4 < r3) r3 = r4; if (r3 > r) r = r3; }
        printf("  spaceecho %-22s %.2f (limit %.2f)\n", c[k].what, r, c[k].lim);
        CHECK(r < c[k].lim, "click on %s: %.2f", c[k].what, r);
    }
    free(x); free(yl); free(yr); free(s);
}

/* ---------- determinism and block size ---------- */

static void se_proc(void *s, long pos, long m) { spaceecho_process(s, g_x + pos, g_x + pos, g_yl + pos, g_yr + pos, (int)m); }
static void se_setp(void *s, int p, float v) { spaceecho_set(s, p, v); }

static void test_se_blocks(void)
{
    const long N = 48000 * 6;
    float *x = fbuf(N), *a[4];
    SpaceEcho *s = malloc(sizeof *s);
    Change ch[] = { { 48000, SE_TIME, 15 }, { 96000, SE_PAN, 2 }, { 120000, SE_MODE, 6 }, { 150016, SE_BW, 30 },
                    { 200000, SE_PAN, 1 }, { 230000, SE_CTR, 100 } };
    uint32_t r = 77u;
    long i;
    int k, m;
    printf("tape echo determinism and block size (32-sample calls vs one call per segment vs random 1..100-sample calls)\n");
    for (i = 0; i < N; i++) x[i] = (i % 24000) < 4800 ? 0.5f * fx_rands(&r) : 0.0f;
    for (k = 0; k < 4; k++) a[k] = fbuf(2 * N);
    for (k = 0; k < 6; k++) ch[k].at = ch[k].at / 32 * 32;
    g_x = x;
    for (m = 0; m < 4; m++) {
        g_yl = a[m]; g_yr = a[m] + N;
        spaceecho_init(s, 11u);
        spaceecho_set(s, SE_AGE, 127); spaceecho_set(s, SE_INT, 90); spaceecho_set(s, SE_MODE, 4);
        spaceecho_set(s, SE_WID, 100); spaceecho_set(s, SE_BW, 80);
        spaceecho_snap(s);
        cut_loop(N, m == 3 ? 0 : m, ch, 6, se_proc, se_setp, s);
    }
    CHECK(!memcmp(a[0], a[3], sizeof(float) * 2 * N), "spaceecho: two identical runs differ");
    CHECK(!memcmp(a[0], a[1], sizeof(float) * 2 * N), "spaceecho: 32-sample blocks != long blocks");
    CHECK(!memcmp(a[0], a[2], sizeof(float) * 2 * N), "spaceecho: 32-sample blocks != random blocks");
    printf("  spaceecho: repeat %s, long blocks %s, random blocks %s\n",
           memcmp(a[0], a[3], sizeof(float) * 2 * N) ? "DIFFERS" : "bit-identical",
           memcmp(a[0], a[1], sizeof(float) * 2 * N) ? "DIFFER" : "bit-identical",
           memcmp(a[0], a[2], sizeof(float) * 2 * N) ? "DIFFER" : "bit-identical");
    for (k = 0; k < 4; k++) free(a[k]);
    free(x); free(s);
}

/* ---------- fuzz: every knob jumping, loud bursts, then silence ---------- */

static void test_se_extremes(void)
{
    SpaceEcho *s = malloc(sizeof *s);
    float xl[32], xr[32], yl[32], yr[32];
    uint32_t r = 99u;
    long blk, bad = 0, i, nbad = 0;
    double peak = 0;
    int k;
    printf("tape echo fuzz: 120 s, a random knob jump every 7 blocks (INTENSITY pushed to 127 half the time), BPM jumps,\n"
           "  loud bursts (+-4.0), then 300 s of silence; every output sample and every state float checked\n");
    spaceecho_init(s, 3u);
    for (blk = 0; blk < 120L * 1500; blk++) {
        int j, p;
        int loud = (blk / 3000) % 2 == 0;
        for (j = 0; j < 32; j++) {
            float u = fx_rands(&r);
            xl[j] = loud ? 4.0f * u : 0.0f;
            xr[j] = loud ? (j & 1 ? 4.0f : -4.0f) : 0.0f;
        }
        if (blk % 7 == 0) {
            p = (int)(fx_randu(&r) * SE_NPARAMS) % SE_NPARAMS;
            spaceecho_set(s, p, fx_randu(&r) < 0.5f ? (fx_randu(&r) < 0.5f ? 0.0f : 127.0f) : fx_randu(&r) * 127.0f);
            if (fx_randu(&r) < 0.5f) spaceecho_set(s, SE_INT, 127.0f);
            if (blk % 700 == 0) spaceecho_set_bpm(s, 30.0f + 270.0f * fx_randu(&r));
        }
        spaceecho_process(s, xl, xr, yl, yr, 32);
        for (j = 0; j < 32; j++) {
            if (is_bad(yl[j]) || is_bad(yr[j])) { bad++; if (bad < 5) printf("    block %ld: %g %g\n", blk, yl[j], yr[j]); }
            if (fabs(yl[j]) > peak) peak = fabs(yl[j]);
            if (fabs(yr[j]) > peak) peak = fabs(yr[j]);
        }
    }
    printf("  peak output %.4f (the wet ceiling 0.7; 3.54 here before it)\n", peak);
    CHECK(peak <= SE_CEIL, "output above the ceiling: %.4f", peak);
    CHECK(bad == 0, "%ld NaN/Inf/denormal output samples", bad);
    spaceecho_set(s, SE_INT, 40.0f);
    spaceecho_set(s, SE_AGE, 0.0f);
    memset(xl, 0, sizeof xl);
    memset(xr, 0, sizeof xr);
    for (blk = 0; blk < 300L * 1500; blk++) {
        int j;
        spaceecho_process(s, xl, xr, yl, yr, 32);
        for (j = 0; j < 32; j++) if (is_bad(yl[j]) || is_bad(yr[j])) bad++;
    }
#define CKS(x) do { if (is_bad(x)) { nbad++; printf("    bad state %s = %g\n", #x, (double)(x)); } } while (0)
    for (i = 0; i < 2 * SE_RING; i++) if (is_bad(s->ring[i])) nbad++;
    for (i = 0; i < SE_HIST; i++) if (is_bad(s->hist[i])) nbad++;
    for (k = 0; k < SE_NMIX; k++) { CKS(s->mx[k]); CKS(s->mx_i[k]); CKS(s->mx_e[k]); }
    for (k = 0; k < 3; k++) { CKS(s->tau[k]); CKS(s->mod[k]); CKS(s->mod_i[k]); CKS(s->mod_e[k]); CKS(s->pos[k]); CKS(s->pcnt[k]); }
    for (k = 0; k < 2; k++) {
        CKS(s->hp[k].ic1); CKS(s->hp[k].ic2); CKS(s->lp[k].ic1); CKS(s->lp[k].ic2);
        CKS(s->ck[k].ic1); CKS(s->ck[k].ic2); CKS(s->hz[k]); CKS(s->az[k]); CKS(s->dc[k]);
    }
    CKS(s->fb); CKS(s->fb_i); CKS(s->drive); CKS(s->drive_i); CKS(s->inv_drive); CKS(s->inv_drive_i);
    CKS(s->byp); CKS(s->byp_i); CKS(s->drv); CKS(s->trim); CKS(s->lc); CKS(s->lb); CKS(s->age); CKS(s->ac);
    CKS(s->wowA); CKS(s->flutA); CKS(s->M); CKS(s->nw1); CKS(s->nw2); CKS(s->nf1); CKS(s->nf2);
    CKS(s->env); CKS(s->hg); CKS(s->hg_i); CKS(s->ck_impL); CKS(s->ck_impR);
#undef CKS
    printf("  after 300 s of silence: %ld bad output samples, %ld bad state floats (tape and history included)\n", bad, nbad);
    CHECK(bad == 0 && nbad == 0, "denormal/NaN after decay: %ld out, %ld state", bad, nbad);
    free(s);
}

static void test_spaceecho(void)
{
    printf("\n---------- TAPE (T5.2) ----------\n");
    printf("sizeof(SpaceEcho) = %lu bytes; with the 44-byte glue header %lu of the stock delay's 4,194,908 bytes of DDR\n",
           (unsigned long)sizeof(SpaceEcho), (unsigned long)sizeof(SpaceEcho) + 44);
    CHECK(sizeof(SpaceEcho) + 44 <= 4194908, "SpaceEcho does not fit the stock delay's DDR: %lu", (unsigned long)sizeof(SpaceEcho));
    test_se_time();
    test_se_modes();
    test_se_transparent();
    test_se_band();
    test_se_intensity();
    test_se_share();
    test_se_ceiling();
    test_se_pan();
    test_se_age();
    test_se_smoothing();
    test_se_blocks();
    test_se_extremes();
}
