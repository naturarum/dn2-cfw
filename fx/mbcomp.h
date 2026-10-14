/* SPDX-License-Identifier: GPL-2.0-or-later */
/*
 * mbcomp.h - 3-band master compressor for the Digitone II (T3.11, G9; T6.4/T6.3 CMP0, CMP1, CMP1b).
 *
 * Portable C99, float only, no libc, no malloc. State 1,768 bytes on the SHARC (1,776 on the Mac): filter
 * and smoothing state plus 1,280 bytes of work buffers for one 32-sample block. Built on the Mac (clang) and
 * for the SHARC+ with selache's selcc (tools/dspsplice recipe CMP1, glue tools/dspsplice/src/cmp_glue.c;
 * CMP0's sources as built are frozen in tools/dspsplice/src/cmp0/).
 *
 * Signal flow (stereo, linked), notes/T6.4-compressor-map-and-plan.md 4.3:
 *   in -> Linkwitz-Riley 4th-order split at XLO -> low | rest
 *         rest -> LR4 split at XHI -> mid | high
 *         low -> 2nd-order allpass at XHI, so low + mid + high = AP(XLO) AP(XHI) in (a flat allpass)
 *   each band is the stock DN2 master compressor (0xb8541d, T6.4 section 1.4):
 *     detector  the stock first-order side-chain filter at its neutral setting (SCF 64: high-pass 20 Hz,
 *               the stock table's coefficient), on each band's own signal or, with a key, on the key;
 *               then max(|L|,|R|) per sample (peak, linked)
 *     curve     hard knee: gc = thr + (lev - thr) / r - lev, threshold -60..0 dB, the stock ratios
 *               (1/r from the stock table) plus step 0 = 1:1 (band off)
 *     smoothing the stock "smooth decoupled" pair: y1 = min(gc, (1-cr) gc + cr y1) (instant attack,
 *               release one-pole), y2 = ca y2 + (1-ca) y1; ca, cr from the stock ATK/REL tables
 *     makeup    0..+24.08 dB, a 32-sample linear ramp per block to the target (stock)
 *     gain      per sample, 2^((y2 + makeup)/6.02)
 *   wet = clip(sum of the bands x their gains, +-1.0) (the stock wet clip)
 *   out = Tw * wet + Td * dry with the stock MIX table and the stock per-sample one-poles (0.995);
 *   dry = the raw input while the MIX index is 0 (stock MIX 0 is the raw input x 0.99999404, so a
 *   kit at MIX 0 is bit-identical to stock), else the sum of the bands (the allpassed input, in phase
 *   with the wet); a change of the dry source crossfades over 32 blocks (21 ms, smoothstep).
 *   Guard: a block whose output or state holds NaN, inf or an absurd value (integer exponent test: over
 *   2^12, +72 dBFS, since CMP1b) is output as silence and the whole state is restarted (CMP1): every
 *   filter and envelope at rest, every value derived from the knobs derived again (the knobs read
 *   through NaN-safe clamps), the smoothers on
 *   their targets, the dry crossfade at its end; then a quiet return: both MIX gains start from 0 (the stock
 *   one-poles fade the output in, 4.2 ms time constant) and for MB_REC_BLOCKS control blocks every band
 *   attacks instantly (no burst at no gain reduction with the full makeup); on the DSP the glue also seeds
 *   each band's envelopes with the gain reduction of the block before the restart (cmp_glue.c cmp_seed).
 *   Scrub (CMP1): each control block re-derives one group of the knob-derived values (a band's curve,
 *   times and makeup target; the crossover targets, SCS and MIX; the crossover coefficients), all of them
 *   every 8 blocks, and the detector coefficients every block (CMP1b). Range test before the samples (CMP1,
 *   CMP1b): a smoother outside the range it can reach (crossover glide, MIX gains, dry crossfade, envelopes
 *   outside [-32, 0] log2 units, makeup) or a knob-derived value outside what the code can store (time
 *   coefficients outside [0, 1] or not paired with their exact complement, 1/ratio, makeup target,
 *   threshold, MIX targets, crossover targets and coefficients) restarts the state. In normal operation the
 *   scrub writes the values already there and the test never fires, so the output is CMP0's bit for bit
 *   (tools/dspsplice/verify_cmp.py CMP1, (h)). A wrong value inside its range cannot be told from a real
 *   one and acts as one: a knob-derived one until its scrub group comes round (at most 8 blocks, 5 ms); an
 *   envelope at its release speed (seconds at REL 127); a filter state below the guard as a transient of
 *   that size, whose gain reduction comes back at the release speed.
 *
 * Tables: the stock layout of the DN2 1.12 DSP image from 0x2cfde0 (MB_TAB_*): 1/ratio[8], ATK[128],
 * REL[128], MIX[128], SCF[128]. On the SHARC the glue passes the image's own tables (read in place);
 * on the Mac, mbcomp_init(s, NULL) uses tables built from the stock laws (mbcomp_law_tables), or the
 * caller passes the image's values.
 *
 * Parameters take the DN2 knob value 0..127 (RAT the step 0..8, SCS 0/1). ATK, REL and MIX take the
 * table index as the stock code does: trunc(knob).
 */
#ifndef MBCOMP_H
#define MBCOMP_H

#include <stdint.h>
#include "fxmath.h"

/* the stock table block (floats from DSP 0x2cfde0, T6.4 1.5) */
#define MB_TAB_RINV 0       /* 0x2cfde0: 1/r for the stock ratios 1.5 2 3 4 6 8 16 20 */
#define MB_TAB_ATK 8        /* 0x2cfe00: attack one-pole coefficient per ATK index */
#define MB_TAB_REL 136      /* 0x2d0000: release one-pole coefficient per REL index */
#define MB_TAB_MIX 264      /* 0x2d0200: MIX gain T[m]; wet T[m], dry T[127 - m] */
#define MB_TAB_SCF 392      /* 0x2d0400: side-chain filter T per SCF index (64 = neutral, HP 20 Hz) */
#define MB_TAB_FLOATS 520
#define MB_SCF_NEUTRAL 64
/* CMP1 #13: control blocks of instant attack after a restart (48 = 32 ms; out/T6.3/CMP1-dsp/trip_loudness.json) */
#define MB_REC_BLOCKS 48

/* per-band parameters: index = band * MB_BAND_NP + p, band 0 = low, 1 = mid, 2 = high */
enum {
    MB_THR = 0,   /* threshold -60..0 dB, 60 v/127 - 60 (stock law) */
    MB_ATK,       /* attack: stock table index trunc(v) (0.1..100 ms) */
    MB_REL,       /* release: stock table index trunc(v) (10..2000 ms) */
    MB_MUP,       /* makeup 0..+24.08 dB, 24.0824 v/127 (stock law) */
    MB_RAT,       /* ratio step 0..8: 0 = 1:1 (band off), 1..8 = the stock 1.5 2 3 4 6 8 16 20 */
    MB_BAND_NP
};
#define MB_P(band, p) ((band) * MB_BAND_NP + (p))

/* crossovers and globals */
enum {
    MB_XLO = 3 * MB_BAND_NP,  /* low/mid crossover, 30 Hz x (1000/30)^(v/127): 30 Hz..1 kHz (68 = 196 Hz) */
    MB_XHI,       /* mid/high crossover, 1 kHz x 16^(v/127): 1..16 kHz (44 = 2.61 kHz); the two are sorted */
    MB_SCS,       /* sidechain: 0 each band detects itself, 1 every band detects the key (when one is given) */
    MB_MIX,       /* dry/wet, stock MIX table index trunc(v): 0 dry (raw input), 127 wet */
    MB_NPARAMS
};

typedef struct {
    float y1, y2;          /* release stage, attack stage: gain reduction in log2 units (<= 0) */
    float ca, ca1;         /* attack coefficient and 1 - ca */
    float cr, cr1;         /* release coefficient and 1 - cr */
    float thr, thr_lin;    /* threshold in log2 units, and as a linear level */
    float rinv;            /* 1/ratio (1 at step 0) */
    int32_t on;            /* ratio step > 0 */
    float mk, mk_step, t_mk;   /* makeup in log2 units: current, per-sample step this block, target */
    float hx[2], hy[2];    /* detector filter state per channel (own signal): last input, last output */
} MbBand;

typedef struct {
    float knob[MB_NPARAMS];
    int32_t dirty;

    /* crossovers: log2 Hz, smoothed at the control rate */
    float lf_lo, lf_hi, t_lf_lo, t_lf_hi;
    FxSvfCoef c_lo, c_hi;
    FxSvf s1[2], s1lp[2];            /* split at the lower crossover */
    FxSvf apl[2];                    /* low band: allpass at the upper crossover */
    FxSvf s2[2], s2lp[2];            /* split at the upper crossover */

    /* detector filter (stock side-chain filter at SCF 64): y = sb0 (x - x1) + sp y1 */
    float sb0, sp;
    float kx[2], ky[2];              /* its state on the key */
    int32_t sc_ext;                  /* SCS: the bands detect the key */

    MbBand band[3];

    /* MIX: the stock one-poles, per sample cur = target + 0.995 (cur - target), used before the update */
    float mw, md;                    /* current wet and dry gains */
    float t_mw, t_md;                /* targets T[m], T[127 - m] */
    int32_t mix0;                    /* MIX index 0: the dry is the raw input */
    float xw, xs;                    /* dry crossfade position (0 = band sum, 1 = raw; moving by xs per
                                        sample over 21 ms after the MIX index left or reached 0) */

    int32_t ctl_left;
    uint32_t trips;                  /* guard trips since init */
    int32_t scrub;                   /* CMP1: the scrub's next group (& 7) */
    int32_t rec_left;                /* CMP1: control blocks of instant attack left after a guard trip */

    /* work buffers of one control block */
    float buf_lo[2][FX_BLOCK], buf_md[2][FX_BLOCK], buf_hi[2][FX_BLOCK];
    float buf_key[FX_BLOCK], buf_g[3][FX_BLOCK];

    const float *tab;                /* the stock table block (MB_TAB_*); last, so that every field above
                                        has the same offset on the SHARC (4-byte pointers) and the Mac */
} MBComp;

/* tab: the stock table block (MB_TAB_FLOATS floats); NULL on the Mac = the law tables */
void mbcomp_init(MBComp *s, const float *tab);
void mbcomp_set(MBComp *s, int param, float value);
/* jump all smoothers (crossovers, makeup, MIX) to their targets (after init + set, before audio) */
void mbcomp_snap(MBComp *s);
/* keyL/keyR may be NULL (no key: self-detection whatever SCS says). Any n >= 0; the result does not
   depend on how the stream is cut into calls. in and out must not overlap. */
void mbcomp_process(MBComp *s, const float *inL, const float *inR,
                    const float *keyL, const float *keyR,
                    float *outL, float *outR, int n);
/* current gain reduction of a band in dB (<= 0, makeup not included), for a meter */
float mbcomp_gr_db(const MBComp *s, int band);
/* knob -> physical, for tests and the write-up */
float mbcomp_xlo_hz(float knob);
float mbcomp_xhi_hz(float knob);
float mbcomp_ratio(const MBComp *s, float step);   /* RAT step -> ratio (1 = band off) */

#if !defined(__ADSP21000__) || defined(MB_LAW_TABLES)
/* Mac only (and fxcheck's SHARC harness, -DMB_LAW_TABLES): the stock table block built from the stock
   laws (T6.4 1.3; within 0.03 % (ATK), 0.6 % (REL) of the image's tables, MIX within 0.2 dB, ratios and
   SCF 64 to float rounding) */
void mbcomp_law_tables(float *tab);
float mbcomp_atk_ms(float knob);
float mbcomp_rel_ms(float knob);
float mbcomp_mix_db(float knob);
#endif

#endif
