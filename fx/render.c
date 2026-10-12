/* SPDX-License-Identifier: GPL-2.0-or-later */
/*
 * render.c - offline renderer for the T3.11 effects (Mac side).
 *
 * Reads a 48 kHz WAV, runs one effect in 32-sample blocks exactly as the DSP
 * does (parameters are applied between blocks, like a frame update), writes a WAV.
 *
 *   fxrender -fx tapedelay -i dry.wav -o wet.wav -p TIME=47 -p FDBK=80 \
 *            -ramp 2 6 WOW 0 127 -at 8 AGE=64 -bpm 120 -tail 4
 *   fxrender -fx mbcomp -i loop.wav -o comp.wav -p L.THR=60 -p L.RAT=4 ...
 *   fxrender -fx flanger -i pad.wav -o fl.wav -p SPD=30 -p FDBK=110 -vol 127
 *   fxrender -bands -i any.wav [-p XLO=38 -p XHI=90]   (RMS of the three bands, dB)
 *   fxrender -fx tapedelay -list
 *
 * Options:
 *   -fx NAME          tapedelay | mbcomp | flanger
 *   -i FILE, -o FILE  input / output WAV (input must be 48 kHz)
 *   -key FILE         external sidechain input (mbcomp, SCS=1)
 *   -p NAME=V         knob value at t = 0 (0..127, fractional allowed)
 *   -at T NAME=V      knob change at T seconds
 *   -ramp T0 T1 NAME V0 V1   linear knob sweep, updated every block
 *   -bpm B            tempo for TIME (default 120)
 *   -tail S           seconds of silence appended to the input (default 0)
 *   -block N          block length (default 32, the DSP's)
 *   -mix send|wet|insert   send: out = dry + VOL*wet (default for tapedelay and flanger);
 *                     wet: effect output only; insert: effect output (default for mbcomp)
 *   -vol V            VOL (LEVEL) knob for -mix send, 0..127 -> gain V/127 (default 100) [guess]
 *   -gain DB          input gain in dB (default 0)
 *   -bits 16|24|32    output format (default 16, dithered)
 *   -meter            print gain-reduction statistics (mbcomp)
 *   -bands            no effect, no output: split the input with the compressor's crossover
 *                     (XLO/XHI from -p) and print the RMS of each band in dB (for the A/B page)
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include "wav.h"
#include "tapedelay.h"
#include "mbcomp.h"
#include "flanger.h"

typedef struct { const char *name; int id; } PName;

static const PName TD_NAMES[] = {
    { "TIME", TD_TIME }, { "FDBK", TD_FDBK }, { "HPF", TD_HPF }, { "LPF", TD_LPF }, { "RES", TD_RES },
    { "WID", TD_WID }, { "WOW", TD_WOW }, { "FLUT", TD_FLUT }, { "AGE", TD_AGE }, { "DRIV", TD_DRIV },
    { "TONE", TD_TONE }, { "W&F", TD_WF }, { "WF", TD_WF }, { 0, 0 }
};
static const PName MB_NAMES[] = {
    { "L.THR", MB_P(0, MB_THR) }, { "L.ATK", MB_P(0, MB_ATK) }, { "L.REL", MB_P(0, MB_REL) },
    { "L.MUP", MB_P(0, MB_MUP) }, { "L.RAT", MB_P(0, MB_RAT) },
    { "M.THR", MB_P(1, MB_THR) }, { "M.ATK", MB_P(1, MB_ATK) }, { "M.REL", MB_P(1, MB_REL) },
    { "M.MUP", MB_P(1, MB_MUP) }, { "M.RAT", MB_P(1, MB_RAT) },
    { "H.THR", MB_P(2, MB_THR) }, { "H.ATK", MB_P(2, MB_ATK) }, { "H.REL", MB_P(2, MB_REL) },
    { "H.MUP", MB_P(2, MB_MUP) }, { "H.RAT", MB_P(2, MB_RAT) },
    { "XLO", MB_XLO }, { "XHI", MB_XHI }, { "LSTN", MB_LSTN }, { "SCS", MB_SCS },
    { "SCF", MB_SCF }, { "MIX", MB_MIX }, { 0, 0 }
};
static const PName FL_NAMES[] = {
    { "DPTH", FL_DPTH }, { "SPD", FL_SPD }, { "HPF", FL_HPF }, { "WDTH", FL_WDTH },
    { "FDBK", FL_FDBK }, { "MAN", FL_MAN }, { "SHP", FL_SHP }, { "SLEW", FL_SLEW },
    { "DAMP", FL_DAMP }, { "SYNC", FL_SYNC }, { "OFS", FL_OFS }, { "POL", FL_POL }, { 0, 0 }
};

typedef struct { double t0, t1; int id; double v0, v1; int ramp; } Ev;

enum { FX_TD, FX_MB, FX_FL };

static void usage(void)
{
    fprintf(stderr, "usage: fxrender -fx tapedelay|mbcomp|flanger -i in.wav -o out.wav [-p NAME=V] [-at T NAME=V]\n"
                    "       [-ramp T0 T1 NAME V0 V1] [-key key.wav] [-bpm B] [-tail S] [-block N]\n"
                    "       [-mix send|wet|insert] [-vol V] [-gain DB] [-bits 16|24|32] [-meter] | -list\n"
                    "       fxrender -bands -i in.wav [-p XLO=V -p XHI=V]\n");
    exit(2);
}

static int lookup(const PName *t, const char *name)
{
    int i;
    for (i = 0; t[i].name; i++)
        if (!strcmp(t[i].name, name)) return t[i].id;
    fprintf(stderr, "unknown parameter %s; known:", name);
    for (i = 0; t[i].name; i++) fprintf(stderr, " %s", t[i].name);
    fprintf(stderr, "\n");
    exit(2);
}

static void parse_assign(const PName *t, const char *s, int *id, double *v)
{
    char name[32];
    const char *eq = strchr(s, '=');
    size_t n;
    if (!eq) { fprintf(stderr, "expected NAME=V, got %s\n", s); exit(2); }
    n = (size_t)(eq - s);
    if (n >= sizeof name) n = sizeof name - 1;
    memcpy(name, s, n);
    name[n] = 0;
    *id = lookup(t, name);
    *v = atof(eq + 1);
}

int main(int argc, char **argv)
{
    const char *fxname = NULL, *in = NULL, *out = NULL, *keyf = NULL, *mixs = NULL;
    double bpm = 120.0, tail = 0.0, vol = 100.0, gain_db = 0.0;
    int block = FX_BLOCK, bits = 16, meter = 0, list = 0, bands = 0, fx, mixmode;
    const PName *names;
    Ev *ev = calloc((size_t)argc + 1, sizeof(Ev));
    int nev = 0, i;
    Wav w, key, o;
    TapeDelay *td = NULL;
    MBComp *mb = NULL;
    Flanger *fl = NULL;
    long pos, frames;
    float *tl, *tr, *zl;
    double grsum[3] = { 0, 0, 0 }, grmin[3] = { 0, 0, 0 };
    long grn = 0;
    double peak = 0.0;

    for (i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "-fx") && i + 1 < argc) fxname = argv[++i];
        else if (!strcmp(argv[i], "-list")) list = 1;
        else if (!strcmp(argv[i], "-i") && i + 1 < argc) in = argv[++i];
        else if (!strcmp(argv[i], "-o") && i + 1 < argc) out = argv[++i];
        else if (!strcmp(argv[i], "-key") && i + 1 < argc) keyf = argv[++i];
        else if (!strcmp(argv[i], "-bpm") && i + 1 < argc) bpm = atof(argv[++i]);
        else if (!strcmp(argv[i], "-tail") && i + 1 < argc) tail = atof(argv[++i]);
        else if (!strcmp(argv[i], "-block") && i + 1 < argc) block = atoi(argv[++i]);
        else if (!strcmp(argv[i], "-mix") && i + 1 < argc) mixs = argv[++i];
        else if (!strcmp(argv[i], "-vol") && i + 1 < argc) vol = atof(argv[++i]);
        else if (!strcmp(argv[i], "-gain") && i + 1 < argc) gain_db = atof(argv[++i]);
        else if (!strcmp(argv[i], "-bits") && i + 1 < argc) bits = atoi(argv[++i]);
        else if (!strcmp(argv[i], "-meter")) meter = 1;
        else if (!strcmp(argv[i], "-bands")) { bands = 1; if (!fxname) fxname = "mbcomp"; }
        else if (!strcmp(argv[i], "-p") || !strcmp(argv[i], "-at") || !strcmp(argv[i], "-ramp")) {
            /* parsed in a second pass, once the effect is known */
            if (!strcmp(argv[i], "-p")) i += 1;
            else if (!strcmp(argv[i], "-at")) i += 2;
            else i += 5;
            if (i >= argc) usage();
        } else usage();
    }
    if (!fxname) usage();
    if (!strcmp(fxname, "tapedelay")) { fx = FX_TD; names = TD_NAMES; }
    else if (!strcmp(fxname, "mbcomp")) { fx = FX_MB; names = MB_NAMES; }
    else if (!strcmp(fxname, "flanger")) { fx = FX_FL; names = FL_NAMES; }
    else usage();
    if (list) {
        for (i = 0; names[i].name; i++) printf("%s\n", names[i].name);
        return 0;
    }
    if (bands && fx != FX_MB) usage();
    if (!in || (!out && !bands) || block < 1) usage();

    for (i = 1; i < argc; i++) {
        Ev *e = &ev[nev];
        if (!strcmp(argv[i], "-p")) {
            parse_assign(names, argv[i + 1], &e->id, &e->v0);
            e->t0 = e->t1 = 0.0; e->v1 = e->v0; e->ramp = 0; nev++; i += 1;
        } else if (!strcmp(argv[i], "-at")) {
            e->t0 = e->t1 = atof(argv[i + 1]);
            parse_assign(names, argv[i + 2], &e->id, &e->v0);
            e->v1 = e->v0; e->ramp = 0; nev++; i += 2;
        } else if (!strcmp(argv[i], "-ramp")) {
            e->t0 = atof(argv[i + 1]); e->t1 = atof(argv[i + 2]);
            e->id = lookup(names, argv[i + 3]);
            e->v0 = atof(argv[i + 4]); e->v1 = atof(argv[i + 5]);
            e->ramp = 1; nev++; i += 5;
        } else if (!strcmp(argv[i], "-fx") || !strcmp(argv[i], "-i") || !strcmp(argv[i], "-o")
                   || !strcmp(argv[i], "-key") || !strcmp(argv[i], "-bpm") || !strcmp(argv[i], "-tail")
                   || !strcmp(argv[i], "-block") || !strcmp(argv[i], "-mix") || !strcmp(argv[i], "-vol")
                   || !strcmp(argv[i], "-gain") || !strcmp(argv[i], "-bits")) i++;
    }

    mixmode = fx == FX_MB ? 2 : 0;
    if (mixs) {
        if (!strcmp(mixs, "send")) mixmode = 0;
        else if (!strcmp(mixs, "wet")) mixmode = 1;
        else if (!strcmp(mixs, "insert")) mixmode = 2;
        else usage();
    }

    if (wav_read(in, &w)) return 1;
    if (w.rate != 48000) { fprintf(stderr, "%s: %d Hz; the workbench runs at 48000 Hz only\n", in, w.rate); return 1; }
    frames = w.frames + (long)(tail * 48000.0 + 0.5);
    if (bands) {
        /* the compressor at 1:1 on every band is its crossover alone; sum each band's energy */
        double e[3] = { 0, 0, 0 };
        long p0;
        int b, c, j;
        mb = malloc(sizeof *mb);
        if (!mb) return 1;
        mbcomp_init(mb);
        for (b = 0; b < 3; b++) mbcomp_set(mb, MB_P(b, MB_RAT), 0.0f);
        for (i = 0; i < nev; i++) if (ev[i].t0 <= 0.0) mbcomp_set(mb, ev[i].id, (float)ev[i].v0);
        mbcomp_snap(mb);
        tl = calloc(FX_BLOCK * 4, sizeof(float));
        if (!tl) return 1;
        for (p0 = 0; p0 < w.frames; p0 += FX_BLOCK) {
            int n = (int)(w.frames - p0 < FX_BLOCK ? w.frames - p0 : FX_BLOCK);
            mbcomp_process(mb, w.l + p0, w.r + p0, NULL, NULL, tl, tl + FX_BLOCK, n);
            for (c = 0; c < 2; c++)
                for (j = 0; j < n; j++) {
                    e[0] += (double)mb->buf_lo[c][j] * mb->buf_lo[c][j];
                    e[1] += (double)mb->buf_md[c][j] * mb->buf_md[c][j];
                    e[2] += (double)mb->buf_hi[c][j] * mb->buf_hi[c][j];
                }
        }
        printf("%.2f %.2f %.2f\n", 10.0 * log10(e[0] / (2.0 * w.frames) + 1e-30),
               10.0 * log10(e[1] / (2.0 * w.frames) + 1e-30), 10.0 * log10(e[2] / (2.0 * w.frames) + 1e-30));
        return 0;
    }
    if (keyf) {
        if (wav_read(keyf, &key)) return 1;
        if (key.rate != 48000) { fprintf(stderr, "%s: not 48 kHz\n", keyf); return 1; }
    }
    if (wav_alloc(&o, frames, 48000)) return 1;
    tl = calloc((size_t)block, sizeof(float));
    tr = calloc((size_t)block, sizeof(float));
    zl = calloc((size_t)block * 4, sizeof(float));
    if (!tl || !tr || !zl) return 1;

    if (fx == FX_TD) {
        td = malloc(sizeof *td);
        if (!td) return 1;
        tapedelay_init(td, 1u);
        tapedelay_set_bpm(td, (float)bpm);
    } else if (fx == FX_MB) {
        mb = malloc(sizeof *mb);
        if (!mb) return 1;
        mbcomp_init(mb);
    } else {
        fl = malloc(sizeof *fl);
        if (!fl) return 1;
        flanger_init(fl);
        flanger_set_bpm(fl, (float)bpm);
    }
    /* t = 0 settings, then snap the smoothers so the render starts settled */
    for (i = 0; i < nev; i++) {
        if (ev[i].t0 <= 0.0) {
            float v = (float)ev[i].v0;
            if (fx == FX_TD) tapedelay_set(td, ev[i].id, v);
            else if (fx == FX_MB) mbcomp_set(mb, ev[i].id, v);
            else flanger_set(fl, ev[i].id, v);
        }
    }
    if (fx == FX_TD) tapedelay_snap(td);
    else if (fx == FX_MB) mbcomp_snap(mb);
    else flanger_snap(fl);

    {
        double g = pow(10.0, gain_db / 20.0), vg = vol / 127.0;
        float *bl = zl, *br = zl + block, *kl = zl + 2 * block, *kr = zl + 3 * block;
        for (pos = 0; pos < frames; pos += block) {
            int n = (int)(frames - pos < block ? frames - pos : block);
            double t = pos / 48000.0;
            int j;
            /* parameter updates between blocks, like a frame arriving */
            for (i = 0; i < nev; i++) {
                Ev *e = &ev[i];
                double v;
                if (t < e->t0 || (e->t0 <= 0.0 && !e->ramp)) continue;
                if (e->ramp) {
                    if (t > e->t1 + block / 48000.0) continue;
                    v = t >= e->t1 ? e->v1 : e->v0 + (e->v1 - e->v0) * (t - e->t0) / (e->t1 - e->t0);
                } else {
                    if (t - e->t0 >= block / 48000.0) continue;  /* fire once */
                    v = e->v0;
                }
                if (fx == FX_TD) tapedelay_set(td, e->id, (float)v);
                else if (fx == FX_MB) mbcomp_set(mb, e->id, (float)v);
                else flanger_set(fl, e->id, (float)v);
            }
            for (j = 0; j < n; j++) {
                long k = pos + j;
                bl[j] = k < w.frames ? (float)(w.l[k] * g) : 0.0f;
                br[j] = k < w.frames ? (float)(w.r[k] * g) : 0.0f;
                if (keyf) {
                    kl[j] = k < key.frames ? key.l[k] : 0.0f;
                    kr[j] = k < key.frames ? key.r[k] : 0.0f;
                }
            }
            if (fx == FX_TD) tapedelay_process(td, bl, br, tl, tr, n);
            else if (fx == FX_MB) mbcomp_process(mb, bl, br, keyf ? kl : NULL, keyf ? kr : NULL, tl, tr, n);
            else flanger_process(fl, bl, br, tl, tr, n);
            for (j = 0; j < n; j++) {
                double l, r;
                if (mixmode == 0) { l = bl[j] + vg * tl[j]; r = br[j] + vg * tr[j]; }
                else { l = tl[j]; r = tr[j]; }
                o.l[pos + j] = (float)l;
                o.r[pos + j] = (float)r;
                if (fabs(l) > peak) peak = fabs(l);
                if (fabs(r) > peak) peak = fabs(r);
            }
            if (fx == FX_MB && meter) {
                int b;
                for (b = 0; b < 3; b++) {
                    double gr = mbcomp_gr_db(mb, b);
                    grsum[b] += gr;
                    if (gr < grmin[b]) grmin[b] = gr;
                }
                grn++;
            }
        }
    }
    if (wav_write(out, o.l, o.r, frames, 48000, bits)) return 1;
    fprintf(stderr, "%s: %ld frames, peak %.2f dBFS%s\n", out, frames, 20.0 * log10(peak + 1e-12),
            peak > 1.0 ? " (CLIPS in 16/24-bit)" : "");
    if (fx == FX_MB && meter && grn)
        fprintf(stderr, "  gain reduction mean/max dB: low %.1f/%.1f  mid %.1f/%.1f  high %.1f/%.1f\n",
                grsum[0] / grn, grmin[0], grsum[1] / grn, grmin[1], grsum[2] / grn, grmin[2]);
    return 0;
}
