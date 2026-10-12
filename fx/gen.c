/* SPDX-License-Identifier: GPL-2.0-or-later */
/*
 * gen.c - dry test material for the T3.11 workbench (no recordings yet).
 * Everything is synthesised here, deterministic, 48 kHz stereo 24-bit.
 *
 *   fxgen OUTDIR
 *
 * Files (120 BPM unless noted):
 *   drums.wav      4-bar drum loop: kick, snare, hats
 *   stabs.wav      synth chord stabs (Am F C G), off-beat eighths, short gates
 *   pad.wav        the same chords held (sustained saw pad): hear wow/flutter
 *   pluck.wav      plucked-string melody with space between notes: hear echoes and taps
 *   clave.wav      one clave hit per bar: tap patterns, crackle against silence
 *   mix.wav        drums + bass + stabs: material for the master compressor
 *   bassline.wav   sustained bass + pad, no drums (sidechain ducking program)
 *   kick4.wav      four-on-the-floor kick only (sidechain key)
 *   duck_LR.wav    left: bass + pad (mono), right: the kick (for recording the stock sidechain, T3.5)
 *   noise.wav      pink-ish noise bursts of different lengths and levels
 *   chords.wav     bright held chords (Am F C G, two bars each, 16 s): for the flanger (2026-10-06)
 *   hits.wav       short noise bursts and drum hits, one per beat, 8 s: Karplus-Strong plucks (2026-10-06)
 *   sweep.wav      log sine sweep 20 Hz - 20 kHz, 10 s, -12 dBFS
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include "wav.h"

#define SR 48000
#define BPM 120.0
#define BEAT (60.0 / BPM)
#define PI 3.14159265358979

static unsigned rs = 12345u;
static double rnd(void) { rs = rs * 1664525u + 1013904223u; return (rs >> 8) / 8388608.0 - 1.0; }

static long secs(double s) { return (long)(s * SR + 0.5); }

static void add(Wav *w, long at, double l, double r)
{
    if (at >= 0 && at < w->frames) { w->l[at] += (float)l; w->r[at] += (float)r; }
}

static void kick(Wav *w, double t, double amp)
{
    long s0 = secs(t), n = secs(0.45), i;
    double ph = 0.0;
    for (i = 0; i < n; i++) {
        double tt = (double)i / SR;
        double f = 48.0 + 110.0 * exp(-tt * 28.0);
        double e = exp(-tt * 7.0);
        double v;
        ph += 2 * PI * f / SR;
        v = sin(ph) * e + (i < 96 ? 0.3 * rnd() * (1.0 - i / 96.0) : 0.0);
        v = tanh(1.6 * v) / tanh(1.6);
        add(w, s0 + i, amp * v, amp * v);
    }
}

static void snare(Wav *w, double t, double amp)
{
    long s0 = secs(t), n = secs(0.3), i;
    double hp = 0.0, prev = 0.0, ph = 0.0;
    for (i = 0; i < n; i++) {
        double tt = (double)i / SR, nz = rnd(), v;
        hp = 0.82 * (hp + nz - prev);   /* crude high-pass */
        prev = nz;
        ph += 2 * PI * 185.0 / SR;
        v = 0.55 * hp * exp(-tt * 16.0) + 0.6 * sin(ph) * exp(-tt * 30.0);
        add(w, s0 + i, amp * v * 0.95, amp * v);
    }
}

static void hat(Wav *w, double t, double amp, double decay)
{
    long s0 = secs(t), n = secs(decay * 6.0), i;
    double a = 0, b = 0, prev = 0;
    for (i = 0; i < n; i++) {
        double tt = (double)i / SR, nz = rnd(), v;
        a = 0.3 * (a + nz - prev);   /* high-pass, twice */
        b = 0.3 * (b + a) ;
        prev = nz;
        v = (a + 0.5 * b) * exp(-tt / decay);
        add(w, s0 + i, amp * v * 0.8, amp * v * 1.1);
    }
}

static double midi_hz(int m) { return 440.0 * pow(2.0, (m - 69) / 12.0); }

/* detuned saw voice through a one-pole low-pass, with an AD(S)R envelope */
static void saw_note(Wav *w, double t, double len, int m, double amp, double cutoff, double pan)
{
    long s0 = secs(t), n = secs(len + 0.25), i;
    double f = midi_hz(m), p1 = 0, p2 = 0.37, lp = 0, lp2 = 0;
    double a = 1.0 - exp(-2 * PI * cutoff / SR);
    for (i = 0; i < n; i++) {
        double tt = (double)i / SR, env, v;
        p1 += f * 1.003 / SR; p1 -= floor(p1);
        p2 += f * 0.997 / SR; p2 -= floor(p2);
        v = (2 * p1 - 1) + (2 * p2 - 1);
        lp += a * (v - lp);
        lp2 += a * (lp - lp2);
        env = tt < 0.005 ? tt / 0.005 : (tt < len ? 1.0 - 0.3 * (tt - 0.005) / (len + 1e-9) : 0.7 * exp(-(tt - len) * 18.0));
        add(w, s0 + i, amp * env * lp2 * (1.0 - pan), amp * env * lp2 * (1.0 + pan));
    }
}

static void pluck(Wav *w, double t, int m, double amp, double pan)
{
    long s0 = secs(t), n = secs(1.6), i;
    int len = (int)(SR / midi_hz(m) + 0.5), k;
    double *buf = calloc((size_t)len, sizeof(double)), last = 0;
    for (k = 0; k < len; k++) buf[k] = rnd();
    for (i = 0; i < n; i++) {
        int j = (int)(i % len);
        double v = buf[j];
        double nv = 0.996 * 0.5 * (v + last);
        last = v;
        buf[j] = nv;
        add(w, s0 + i, amp * v * (1.0 - pan), amp * v * (1.0 + pan));
    }
    free(buf);
}

static void clave(Wav *w, double t, double amp)
{
    long s0 = secs(t), n = secs(0.12), i;
    for (i = 0; i < n; i++) {
        double tt = (double)i / SR;
        double v = sin(2 * PI * 2500.0 * tt) * exp(-tt * 55.0) + 0.4 * sin(2 * PI * 1670 * tt) * exp(-tt * 40.0);
        add(w, s0 + i, amp * v, amp * v);
    }
}

static void bass(Wav *w, double t, double len, int m, double amp)
{
    long s0 = secs(t), n = secs(len + 0.05), i;
    double f = midi_hz(m), p = 0, lp = 0, a = 1.0 - exp(-2 * PI * 400.0 / SR);
    for (i = 0; i < n; i++) {
        double tt = (double)i / SR, v, env;
        p += f / SR; p -= floor(p);
        v = 0.6 * sin(2 * PI * p) + 0.4 * (2 * p - 1);
        lp += a * (v - lp);
        env = tt < 0.004 ? tt / 0.004 : (tt < len ? 1.0 : exp(-(tt - len) * 60.0));
        add(w, s0 + i, amp * env * lp, amp * env * lp);
    }
}

static const int CHORDS[4][3] = { { 57, 60, 64 }, { 53, 57, 60 }, { 48, 52, 55 }, { 55, 59, 62 } };
static const int ROOTS[4] = { 33, 29, 36, 31 };

static void drums_into(Wav *w, int bars, double amp)
{
    int b, s;
    for (b = 0; b < bars; b++) {
        double t0 = b * 4 * BEAT;
        for (s = 0; s < 16; s++) {
            double t = t0 + s * BEAT / 4;
            if (s == 0 || s == 8 || (s == 10 && (b & 1)) || (s == 14 && b == bars - 1)) kick(w, t, 0.9 * amp);
            if (s == 4 || s == 12) snare(w, t, 0.55 * amp);
            if (s % 2 == 0) hat(w, t, (s % 4 == 2 ? 0.35 : 0.22) * amp, s == 14 && (b & 1) ? 0.12 : 0.025);
        }
    }
}

static void stabs_into(Wav *w, int bars, double amp)
{
    int b, e, k;
    for (b = 0; b < bars; b++)
        for (e = 0; e < 8; e++)
            if (e % 2 == 1 || e == 0)
                for (k = 0; k < 3; k++)
                    saw_note(w, b * 4 * BEAT + e * BEAT / 2, 0.09, CHORDS[b % 4][k] + 12, amp * 0.18, 2500.0, (k - 1) * 0.3);
}

/* writes a 24-bit file; norm_db < 0 normalises the peak to that level */
static void write_norm(const char *dir, const char *name, Wav *w, double norm_db)
{
    char path[1024];
    long i;
    double peak = 0;
    for (i = 0; i < w->frames; i++) {
        if (fabs(w->l[i]) > peak) peak = fabs(w->l[i]);
        if (fabs(w->r[i]) > peak) peak = fabs(w->r[i]);
    }
    if (norm_db < 0.0 && peak > 0.0) {
        double g = pow(10.0, norm_db / 20.0) / peak;
        for (i = 0; i < w->frames; i++) { w->l[i] = (float)(w->l[i] * g); w->r[i] = (float)(w->r[i] * g); }
        peak *= g;
    }
    snprintf(path, sizeof path, "%s/%s", dir, name);
    wav_write(path, w->l, w->r, w->frames, SR, 24);
    printf("%s  %.1f s  peak %.1f dBFS\n", path, (double)w->frames / SR, 20 * log10(peak + 1e-12));
    wav_free(w);
}

static void write(const char *dir, const char *name, Wav *w) { write_norm(dir, name, w, -3.0); }

int main(int argc, char **argv)
{
    const char *dir;
    Wav w;
    int b, k, i;
    char cmd[1100];
    if (argc < 2) { fprintf(stderr, "usage: fxgen OUTDIR\n"); return 2; }
    dir = argv[1];
    snprintf(cmd, sizeof cmd, "mkdir -p '%s'", dir);
    if (system(cmd)) return 1;

    /* drums: 4 bars + 1 s */
    wav_alloc(&w, secs(16 * BEAT + 1.0), SR);
    drums_into(&w, 4, 1.0);
    write(dir, "drums.wav", &w);

    wav_alloc(&w, secs(16 * BEAT + 1.0), SR);
    stabs_into(&w, 4, 1.0);
    write(dir, "stabs.wav", &w);

    /* pad: chords held for most of each bar */
    wav_alloc(&w, secs(16 * BEAT + 1.0), SR);
    for (b = 0; b < 4; b++)
        for (k = 0; k < 3; k++)
            saw_note(&w, b * 4 * BEAT, 4 * BEAT - 0.1, CHORDS[b][k], 0.16, 1800.0, (k - 1) * 0.4);
    write(dir, "pad.wav", &w);

    /* pluck melody: eighth notes with rests, A minor pentatonic */
    {
        static const int mel[16] = { 69, -1, 72, -1, 76, 74, -1, -1, 72, -1, 69, 67, -1, -1, 64, -1 };
        wav_alloc(&w, secs(16 * BEAT + 1.0), SR);
        for (i = 0; i < 32; i++) {
            int m = mel[i % 16];
            if (m > 0) pluck(&w, i * BEAT / 2, m, 0.45, (i % 3 - 1) * 0.2);
        }
        write(dir, "pluck.wav", &w);
    }

    wav_alloc(&w, secs(16 * BEAT + 1.0), SR);
    for (b = 0; b < 4; b++) clave(&w, b * 4 * BEAT, 0.5);
    write(dir, "clave.wav", &w);

    /* mix: drums + bass (16ths on the root) + stabs, 8 bars */
    wav_alloc(&w, secs(32 * BEAT + 1.0), SR);
    drums_into(&w, 8, 0.8);
    stabs_into(&w, 8, 0.8);
    for (b = 0; b < 8; b++)
        for (i = 0; i < 16; i++)
            if (i % 4 != 3) bass(&w, b * 4 * BEAT + i * BEAT / 4, BEAT / 4 * 0.8, ROOTS[b % 4] + (i == 6 || i == 14 ? 12 : 0), 0.35);
    write(dir, "mix.wav", &w);

    /* sustained bass + pad (ducking program) */
    wav_alloc(&w, secs(16 * BEAT + 1.0), SR);
    for (b = 0; b < 4; b++) {
        bass(&w, b * 4 * BEAT, 4 * BEAT - 0.05, ROOTS[b] + 12, 0.4);
        for (k = 0; k < 3; k++) saw_note(&w, b * 4 * BEAT, 4 * BEAT - 0.1, CHORDS[b][k], 0.12, 1500.0, (k - 1) * 0.4);
    }
    write(dir, "bassline.wav", &w);

    wav_alloc(&w, secs(16 * BEAT + 1.0), SR);
    for (i = 0; i < 16; i++) kick(&w, i * BEAT, 0.9);
    write(dir, "kick4.wav", &w);

    /* for recording the stock sidechain (T3.5): left = the bass and pad (mono), right = the kick.
       In DUAL mode the unit can route IN L through the compressor and key it from IN R. */
    {
        Wav a, k;
        long j;
        wav_alloc(&a, secs(16 * BEAT + 1.0), SR);
        wav_alloc(&k, secs(16 * BEAT + 1.0), SR);
        for (b = 0; b < 4; b++) {
            bass(&a, b * 4 * BEAT, 4 * BEAT - 0.05, ROOTS[b] + 12, 0.4);
            for (i = 0; i < 3; i++) saw_note(&a, b * 4 * BEAT, 4 * BEAT - 0.1, CHORDS[b][i], 0.12, 1500.0, 0.0);
        }
        for (i = 0; i < 16; i++) kick(&k, i * BEAT, 0.9);
        wav_alloc(&w, a.frames, SR);
        for (j = 0; j < w.frames; j++) { w.l[j] = 0.5f * (a.l[j] + a.r[j]); w.r[j] = k.l[j]; }
        wav_free(&a);
        wav_free(&k);
        write(dir, "duck_LR.wav", &w);
    }

    /* noise bursts: 50 ms, 200 ms, 1 s at -6, -18, -30 dBFS, pink-ish (one-pole tilt) */
    {
        static const double len[3] = { 0.05, 0.2, 1.0 }, lev[3] = { -6.0, -18.0, -30.0 };
        double t = 0.25, p1 = 0, p2 = 0;
        int a, c;
        wav_alloc(&w, secs(10.0), SR);
        for (a = 0; a < 3; a++)
            for (c = 0; c < 3; c++) {
                long s0 = secs(t), n = secs(len[a]), j;
                double g = pow(10.0, lev[c] / 20.0) * 2.0;
                for (j = 0; j < n; j++) {
                    double nz = rnd();
                    p1 = 0.97 * p1 + 0.15 * nz;
                    p2 = 0.6 * p2 + 0.4 * nz;
                    add(&w, s0 + j, g * 0.5 * (p1 + p2), g * 0.5 * (p1 + p2));
                }
                t += len[a] + 0.6;
            }
        write(dir, "noise.wav", &w);
    }

    /* log sweep 20 Hz - 20 kHz in 10 s, -12 dBFS, 0.5 s silence each side */
    {
        long n = secs(10.0), j;
        double f0 = 20.0, f1 = 20000.0, T = 10.0, L = T / log(f1 / f0), amp = pow(10.0, -12.0 / 20.0);
        wav_alloc(&w, n + secs(1.0), SR);
        for (j = 0; j < n; j++) {
            double tt = (double)j / SR;
            double v = amp * sin(2 * PI * f0 * L * (exp(tt / L) - 1.0));
            double fade = j < 480 ? j / 480.0 : (n - j < 480 ? (n - j) / 480.0 : 1.0);
            add(&w, secs(0.5) + j, v * fade, v * fade);
        }
        write_norm(dir, "sweep.wav", &w, 0.0);
    }

    /* bright held chords for the flanger: four voices, saws open to 7 kHz, spread across the field */
    wav_alloc(&w, secs(32 * BEAT + 1.0), SR);
    for (b = 0; b < 4; b++) {
        for (k = 0; k < 3; k++)
            saw_note(&w, b * 8 * BEAT, 8 * BEAT - 0.1, CHORDS[b][k], 0.11, 7000.0, (k - 1) * 0.5);
        saw_note(&w, b * 8 * BEAT, 8 * BEAT - 0.1, CHORDS[b][0] - 12, 0.11, 7000.0, 0.0);
    }
    write(dir, "chords.wav", &w);

    /* hits: per beat a short noise burst (8 ms), a snare, a longer burst (30 ms), a kick; 4 bars */
    {
        long s0, n, j;
        wav_alloc(&w, secs(16 * BEAT + 1.0), SR);
        for (i = 0; i < 16; i++) {
            double t0 = i * BEAT, len = (i % 4 == 0) ? 0.008 : 0.03;
            if (i % 4 == 1) { snare(&w, t0, 0.6); continue; }
            if (i % 4 == 3) { kick(&w, t0, 0.8); continue; }
            s0 = secs(t0);
            n = secs(len * 4.0);
            for (j = 0; j < n; j++) {
                double e = exp(-(double)j / (len * SR)), v = 0.5 * e * rnd();
                add(&w, s0 + j, v, v);
            }
        }
        write(dir, "hits.wav", &w);
    }
    return 0;
}
