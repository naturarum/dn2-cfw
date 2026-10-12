/* SPDX-License-Identifier: GPL-2.0-or-later */
/*
 * mbcomp.h - 3-band master compressor for the Digitone II (T3.11, G9).
 *
 * Portable C99, float only, no libc, no malloc. State is about 2 KB: filter and
 * smoothing state plus 1.5 KB of work buffers for one 32-sample block (the stock
 * compressor keeps 376 bytes in L1, T3.4; this one fits in the free L1 block 2, T3.2).
 *
 * Signal flow (stereo, linked):
 *   in -> Linkwitz-Riley 4th-order split at XLO -> low | rest
 *         rest -> LR4 split at XHI -> mid | high
 *         low -> 2nd-order allpass at XHI (so low+mid+high sums to a flat allpass)
 *   each band has its own compressor, the stock DN2 master compressor's design
 *   (measured in the emulator, notes/T3.11 section 12.2):
 *     detector  peak max(|L|,|R|) of the band (or of the key, see SCS), per sample, in dB
 *     curve     hard knee, threshold -60..0 dB, ratio from the stock list (plus 1:1 = band off)
 *     smoothing "smooth decoupled", in dB on the gain reduction: a release one-pole with an
 *               instant attack, then an attack one-pole (stock ATK/REL time laws)
 *     makeup    MUP 0..+24 dB
 *   out = Tw(MIX) * (sum of the bands, or one band with LSTN) + Td(MIX) * dry, with the stock
 *   MIX crossfade law; dry passes the same two allpasses, so dry and wet stay in phase.
 *   SCS = 1: every band detects the key input (through the SCF filter) instead of itself.
 *
 * Parameters take the DN2 knob value 0..127 (enumerations take the index).
 */
#ifndef MBCOMP_H
#define MBCOMP_H

#include <stdint.h>
#include "fxmath.h"

/* per-band parameters (the stock MIXER 1 order): index = band*MB_BAND_NP + p,
   band 0 = low, 1 = mid, 2 = high */
enum {
    MB_THR = 0,   /* threshold -60..0 dB, 60*v/127 - 60 (stock law) */
    MB_ATK,       /* attack time constant 0.1 + 99.9*(v/127)^2 ms (stock law: 0.1..100 ms) */
    MB_REL,       /* release time constant 10 + 1990*(v/127)^2.5 ms (stock law: 10..2000 ms) */
    MB_MUP,       /* makeup 0..+24.08 dB, 24.08*v/127 (stock law) */
    MB_RAT,       /* ratio index 0..8: 1:1 (band off), then the stock 1.5 2 3 4 6 8 16 20 */
    MB_BAND_NP
};
#define MB_P(band, p) ((band) * MB_BAND_NP + (p))

/* crossovers and global parameters */
enum {
    MB_XLO = 3 * MB_BAND_NP,  /* low/mid crossover, 30 Hz..16 kHz log (default 38 -> 196 Hz) */
    MB_XHI,       /* mid/high crossover, same map (default 90 -> 2.6 kHz); the two are sorted */
    MB_LSTN,      /* setup only: listen 0 all, 1 low, 2 mid, 3 high (crossfaded) */
    MB_SCS,       /* sidechain source: 0 each band detects itself, 1 every band detects the key input */
    MB_SCF,       /* key filter, stock-like law: <64 low-pass, 64 neutral (high-pass 20 Hz), >64 high-pass */
    MB_MIX,       /* dry/wet with the stock crossfade law (0 dry, 64 both at -6 dB, 127 wet) */
    MB_NPARAMS
};

typedef struct {
    float y1, y2;          /* release stage, attack stage: gain reduction in dB (<= 0) */
    float ca, cr;          /* per-sample one-pole coefficients, attack and release */
    float thr, rm1;        /* threshold dB, 1/ratio - 1 */
    int on;                /* ratio above 1:1 */
    float mk, mk_i, t_mk;  /* makeup dB, per-sample ramp, target */
    float w, w_i, t_w;     /* listen weight, ramp, target */
} MbBand;

typedef struct {
    float knob[MB_NPARAMS];
    int dirty;

    /* crossovers: log2 Hz, smoothed at the control rate */
    float lf_lo, lf_hi, t_lf_lo, t_lf_hi;
    FxSvfCoef c_lo, c_hi;

    /* filter states, [channel] */
    FxSvf s1[2], s1lp[2], s1hp[2];   /* split at the lower crossover */
    FxSvf s2[2], s2lp[2], s2hp[2];   /* split at the upper crossover */
    FxSvf apl[2];                    /* low band: allpass at the upper crossover */
    FxSvf apd1[2], apd2[2];          /* dry path: allpasses at both crossovers */
    FxSvf sc[2];                     /* key filter */
    FxSvfCoef scc;
    int sc_lp;                       /* 1 low-pass, 0 high-pass */
    int sc_ext;                      /* SCS: bands detect the key */

    MbBand band[3];
    float mw, mw_i, t_mw;            /* MIX: wet gain, ramp, target */
    float md, md_i, t_md;            /* MIX: dry gain */

    int ctl_left;

    /* work buffers of one control block (mb_run): band signals, dry path,
       key level, band gains */
    float buf_lo[2][FX_BLOCK], buf_md[2][FX_BLOCK], buf_hi[2][FX_BLOCK], buf_dry[2][FX_BLOCK];
    float buf_key[FX_BLOCK], buf_g[3][FX_BLOCK];
} MBComp;

void mbcomp_init(MBComp *s);
void mbcomp_set(MBComp *s, int param, float value);
/* jump all smoothers to their targets (after init + set, before audio) */
void mbcomp_snap(MBComp *s);
/* keyL/keyR may be NULL (silence). Any n >= 0; the result does not depend on
   how the stream is cut into calls. */
void mbcomp_process(MBComp *s, const float *inL, const float *inR,
                    const float *keyL, const float *keyR,
                    float *outL, float *outR, int n);
/* current gain reduction of a band in dB (<= 0), for a meter */
float mbcomp_gr_db(const MBComp *s, int band);
/* knob -> physical, for tests and the write-up */
float mbcomp_xover_hz(float knob);
float mbcomp_ratio(float knob);        /* RAT index -> ratio (1 = band off) */
float mbcomp_atk_ms(float knob);
float mbcomp_rel_ms(float knob);
float mbcomp_mix_db(float knob);       /* the stock MIX table law: gain in dB of the wet side (dry: 127 - v) */
float mbcomp_scf_hz(float knob);       /* key filter corner; the type is low-pass below 63.5 */

#endif
