/*
 * tapedelay.h - tape delay for the Digitone II delay slot (T3.11, G2).
 *
 * Portable C99, float only, no libc, no malloc. All state lives in one
 * TapeDelay struct (3.15 MB, mostly the tape ring; the stock delay owns
 * 4,194,908 bytes of DDR, T3.4), so on the SHARC it can sit where the stock
 * delay state sits.
 *
 * Signal flow (per channel, stereo), one playback head (2026-10-06, after the
 * owner's listening: no extra heads, resonant loop filters, a width control):
 *   WID matrix (mono .. stereo .. ping-pong) of in and FDBK * playback
 *     ->  resonant HPF (2-pole) -> resonant LPF (2-pole; AGE lowers it), RES shared
 *     ->  drive into a soft saturator (holds a self-oscillating loop)  ->  tape ring
 *   tape ring  ->  head (4-point Hermite read, delay = TIME + wow + flutter)
 *              ->  + AGE noise (hiss and sparse soft crackle, both following the
 *                  playback level)  ->  wet out and feedback
 * The output is the wet signal only, as for the stock delay: the FX bus mixes
 * it with VOL (LEVEL) and sends some to the reverb (REV).
 *
 * Parameters take the DN2 knob value 0..127 (fractional allowed, as the frame
 * carries v<<8 with a fractional low byte for TIME and FDBK, T3.3).
 */
#ifndef TAPEDELAY_H
#define TAPEDELAY_H

#include <stdint.h>
#include "fxmath.h"

/* Ring length in frames: 8.19 s at 48 kHz. The stock delay's longest time is
   8 s (384,000 samples: a whole note at 30 BPM, T3.4); the rest is margin for
   wow/flutter and interpolation. */
#define TD_RING 393216

/* Every parameter of both proposed layouts (notes/T3.11 12.4): the 2-page layout
   uses HPF, LPF, RES, WID, WOW, FLUT, AGE, DRIV directly; the 1-page layout uses the
   two macro knobs TONE (sets HPF and LPF) and W&F (sets WOW and FLUT). REV and VOL
   belong to the FX bus. */
enum {
    TD_TIME = 0,  /* delay time in 128th notes, value v -> (v+1)/128 of a whole note, tempo-synced */
    TD_FDBK,      /* feedback 0..110 % (above 100 % the saturator holds it) */
    TD_HPF,       /* loop high-pass 20 Hz..4 kHz, log (default 22: 50 Hz), 12 dB/oct, mildly resonant */
    TD_LPF,       /* loop low-pass 100 Hz..18 kHz, log (default 102: 6.5 kHz), 12 dB/oct, resonant */
    TD_RES,       /* resonance of the loop filters: LPF Q 0.71..5, HPF Q 0.71..1.4 */
    TD_WID,       /* stereo: 0 mono, 64 stereo as it comes, 127 ping-pong (continuous) */
    TD_WOW,       /* wow depth, slow pitch drift, 0..2 % */
    TD_FLUT,      /* flutter depth, fast pitch jitter, 0..0.8 % */
    TD_AGE,       /* tape wear: hiss, sparse soft crackle, top-end loss; 0 = clean */
    TD_DRIV,      /* tape drive into the saturator, 0..+15.6 dB (default 32: +3.9 dB) */
    TD_TONE,      /* macro (1-page layout): bipolar tone, sets HPF and LPF: 64 = 50 Hz..6.5 kHz,
                     down to 0 the LPF falls to 300 Hz, up to 127 the HPF rises to 2.5 kHz */
    TD_WF,        /* macro (1-page layout): wow and flutter on one knob, wow first */
    TD_NPARAMS
};

typedef struct {
    /* the tape: interleaved L,R frames */
    float ring[2 * TD_RING];
    int32_t w;                 /* next write frame */

    /* knob values (0..127) and tempo */
    float knob[TD_NPARAMS];
    float bpm;
    int dirty;

    /* targets in physical units, set by the setters */
    float t_delay;             /* samples */
    float t_fb, t_drive, t_wow, t_flut, t_age;
    float t_lhp, t_llp, t_res, t_wid;   /* log2 Hz, log2 Hz, 0..1, 0..1 */

    /* control-rate smoothed values (start of the current control block) and
       per-sample increments */
    float fb, fb_i, drive, drive_i, inv_drive, inv_drive_i;   /* drive includes the resonance gain trim */
    float wowA, flutA;         /* smoothed depths, samples of delay modulation */
    float mod, mod_i;          /* delay modulation, samples */
    float lhp, llp, res, age;  /* smoothed filter corners (log2 Hz), resonance and age (0..1) */
    float trim;                /* passband trim for the resonance, folded into drive */
    float drv;                 /* smoothed drive without the trim */
    float wid;                 /* smoothed width 0..1 */
    float wc[6], wc_i[6];      /* width matrix: in LL, LR, RL, RR; feedback same side, cross */

    /* delay time: per-sample one-pole glide (tape-like) */
    float d, a_d;

    /* modulation sources */
    float ph_w, ph_f, ph_f2;   /* LFO phases 0..1 */
    float nw1, nw2, nf1, nf2;  /* two-pole smoothed noise, wow and flutter */
    uint32_t rng;

    /* loop filters (per channel) */
    FxSvf hp[2], lp[2];
    FxSvfCoef hpc, lpc;

    /* AGE: playback envelope, hiss, crackle */
    float env, blk_peak;       /* peak envelope of the playback (control rate), peak of the last block */
    float hg, hg_i;            /* hiss gain and per-sample ramp */
    float hz[2];               /* hiss colour filter state */
    uint32_t hrng, crng;       /* noise generators: hiss, crackle events */
    int32_t ck_count;          /* samples to the next crackle event */
    float ck_impL, ck_impR;    /* pending impulse */
    FxSvf ck[2];
    FxSvfCoef ckc;

    /* crackle events of the current control block, worked out before its
       sample loop (td_run): sample index, impulse, filter coefficients */
    int32_t ev_pos[FX_BLOCK + 1];
    float ev_imp[FX_BLOCK][2];
    FxSvfCoef ev_c[FX_BLOCK];
    FxSvfCoef ev_c0;           /* coefficients before the first event */
    int32_t ev_count0, ev_count;  /* counter before and after the block */

    int ctl_left;              /* samples left in the current control block */
} TapeDelay;

void tapedelay_init(TapeDelay *s, uint32_t seed);
/* value: knob 0..127 (fractional allowed) */
void tapedelay_set(TapeDelay *s, int param, float value);
void tapedelay_set_bpm(TapeDelay *s, float bpm);
/* jump all smoothers to their targets (after init + set, before audio) */
void tapedelay_snap(TapeDelay *s);
/* wet output only; any n >= 0; the result does not depend on how the
   stream is cut into calls (control updates run every 32 samples of the stream) */
void tapedelay_process(TapeDelay *s, const float *inL, const float *inR,
                       float *outL, float *outR, int n);
/* knob -> physical, for tests and the write-up */
float tapedelay_time_samples(float knob, float bpm);
float tapedelay_hpf_hz(float knob);
float tapedelay_lpf_hz(float knob);
float tapedelay_lpf_q(float knob);     /* RES -> Q of the low-pass */

#endif
