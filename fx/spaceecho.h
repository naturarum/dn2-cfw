/* SPDX-License-Identifier: GPL-2.0-or-later */
/*
 * spaceecho.h - a Roland RE-201 style tape echo for the Digitone II
 * delay slot (T5.2). Replaces the one-head tape delay design (tapedelay.c, on the
 * unit as FTD1), whose proven parts it reuses: the tape ring with 4-point Hermite
 * reads and the tape-motor glide of the delay time, wow and flutter, AGE hiss and
 * crackle, the cubic saturator, the resonant state-variable loop filters, the
 * stereo matrix. See notes/T5.2-tape-echo.md.
 *
 * Portable C99, float only, no libc, no malloc. All state lives in one SpaceEcho
 * struct (about 4.16 MB, mostly the tape) that fits the stock delay's DDR area
 * (4,194,908 bytes, notes/T3.8 section 9).
 *
 * Signal flow. The tape has two tracks, A and B (left and right in MONO mode):
 *   record:  input matrix (PAN mode) + INTENSITY x DC-blocked feedback matrix of the
 *            sum of the active heads, each at its share (1/n of n heads; PAN mode: same
 *            track, or crossed for ALT)
 *            -> band filter: resonant 2-pole high-pass and low-pass around CENTER,
 *               BW wide; bypassed exactly at BW 127
 *            -> AGE top-end loss (one-pole low-pass; bypassed exactly at AGE 0)
 *            -> drive into the cubic soft saturator (holds the loop)  -> tape
 *   play:    three heads at 1x, 2x, 3x the head-1 time (TIME); MODE says which sound.
 *            Each head reads both tracks (Hermite) at its delay, moved by the tape
 *            speed's wow and flutter as a real tape moves them (the speed now against
 *            the speed when that bit of tape was recorded), and is placed in the
 *            stereo field by WIDTH and PAN.  + AGE hiss and soft crackle that follow
 *            the playback level  ->  the feedback sum, and the wet out through a
 *            ceiling (exactly linear up to 0.5, a soft knee, at most 0.7; T5.3).
 * The output is the wet signal only, as for the stock delay: the FX bus mixes it
 * with VOL (LEVEL) and sends some to the reverb (REV).
 *
 * Parameters take the DN2 knob value 0..127 (fractional allowed; MODE and PAN take
 * their index).
 */
#ifndef SPACEECHO_H
#define SPACEECHO_H

#include <stdint.h>
#include "fxmath.h"

/* Tape length in frames (two tracks): 10.67 s at 48 kHz. Head 3 sits at 3x the head-1
   time, so head 1 reaches (SE_RING - margin)/3 = 3.54 s at most (TIME is clamped there;
   at 120 BPM every TIME value fits, at 60 BPM TIME up to 112). */
#define SE_RING 512000
/* the tape speed's integral per control block, for the wow/flutter of each head: longer
   than the tape (16,384 blocks = 10.9 s) */
#define SE_HIST 16384
#define SE_NMIX 23          /* mixing coefficients (input 4, feedback 4, head gains 3, head outputs 12) */

enum {
    SE_TIME = 0,  /* repeat rate: head 1 at (v+1)/128 of a whole note (tempo-synced, the stock and
                     FTD1 law), heads 2 and 3 at 2x and 3x; changing it glides the tape speed */
    SE_MODE,      /* heads: 0 = 1, 1 = 2, 2 = 3, 3 = 1+2, 4 = 2+3, 5 = 1+3, 6 = 1+2+3 (RE-202 order) */
    SE_INT,       /* intensity: feedback from the sum of the active heads, v/64 shared by them (the stock FDBK
                     law: 64 = 100 %, endless in every MODE, 127 = 198 %; SC01: v/64 per head; SE01: 0..110 %
                     per head, taper (v/127)^1.5) */
    SE_AGE,       /* wear: wow, flutter, hiss, crackle, top-end loss, drive; 0 = clean tape */
    SE_CTR,       /* band centre 50 Hz..12.8 kHz, log (64 = 819 Hz) */
    SE_BW,        /* band width 0.5..20 octaves, log (64 = 3.2 oct); resonance rises below 8 octaves
                     (v < 96); 119..127 fades to no filter, 127 = bypassed exactly */
    SE_WID,       /* 0 = all mono; up: MONO keeps the input's own stereo, ALT and RND spread the heads */
    SE_PAN,       /* 0 MONO (heads not panned), 1 ALT (heads alternate sides, repeats cross sides),
                     2 RND (each head jumps to a random place once per its own period, glided) */
    SE_NPARAMS
};

enum { SE_PAN_MONO = 0, SE_PAN_ALT = 1, SE_PAN_RND = 2 };

typedef struct {
    int32_t w;                  /* next write frame */
    int32_t hw;                 /* index of the newest entry of hist */

    /* knob values (0..127) and tempo */
    float knob[SE_NPARAMS];
    float bpm;
    int dirty;

    /* targets, set from the knobs */
    float t_delay;              /* head 1, samples */
    float t_fb, t_age, t_lc, t_lb, t_byp, t_wid;   /* t_lc: log2 centre Hz; t_lb: log2 width (octaves) */
    float t_wow, t_flut, t_drv;                     /* from AGE */
    int mode, pan;              /* MODE index 0..6, PAN index 0..2 */
    int mixdirty;               /* the mix targets need recomputing (MODE, PAN, WIDTH, random moves) */

    /* heads: delays (per-sample tape glide) and their wow/flutter offsets (ramped per sample) */
    float tau[3];
    float vel[3];               /* glide rate, samples per sample */
    int32_t rq[3];              /* read position per head: ring index (one sample back) ... */
    float rf[3];                /* ... and fraction; set from the delay every control block */
    float ad[3];                /* glide coefficients, head k: tau (k+1) x 0.12 s */
    float mod[3], mod_i[3], mod_e[3];

    /* mixing coefficients: current (block start), per-sample step, end of block, target.
       [0..3] input: A<-L, A<-R, B<-L, B<-R; [4..7] feedback: A<-sumA, A<-sumB, B<-sumA, B<-sumB;
       [8..10] head gains into the feedback; [11 + 4k ..] head k's output: L<-A, L<-B, R<-A, R<-B */
    float mx[SE_NMIX], mx_i[SE_NMIX], mx_e[SE_NMIX];
    int mixsettled;             /* every coefficient is at its target */
    int mxramp;                 /* some coefficient moves in this block */

    /* gains ramped per sample (value, step, end of block) */
    float fb, fb_i, fb_e;
    float drive, drive_i, drive_e;            /* drive x resonance trim */
    float inv_drive, inv_drive_i, inv_drive_e;
    float byp, byp_i, byp_e;                  /* band filter mix: 0 = no filter, 1 = filter */
    float drv, trim;                          /* smoothed drive (without trim), trim */

    /* band filter (per track) and the smoothed log2 centre and width */
    float lc, lb;
    FxSvf hp[2], lp[2];
    FxSvfCoef hpc, lpc;

    /* AGE: smoothed age, top-end loss one-pole (coefficient, state), wow/flutter */
    float age, ac, az[2];
    float wowA, flutA;          /* tape speed deviation depths (fractions) */
    float ph_w, ph_f, ph_f2;    /* LFO phases 0..1 */
    float nw1, nw2, nf1, nf2;   /* two-pole smoothed noise */
    uint32_t rng;
    float M;                    /* the tape speed deviation's integral, samples (leaky) */

    /* feedback DC blocker (per track) */
    float dc[2];

    /* random pan: position, target, samples since the last jump, per head */
    float pos[3], ptgt[3], pcnt[3];
    uint32_t prng;

    /* AGE noise (as tapedelay.c): playback envelope, hiss, crackle */
    float env, blk_peak;
    float hg, hg_i;
    float hz[2];
    uint32_t hrng, crng;
    int32_t ck_count;
    float ck_impL, ck_impR;
    FxSvf ck[2];
    FxSvfCoef ckc;
    int32_t ev_pos[FX_BLOCK + 1];
    float ev_imp[FX_BLOCK][2];
    FxSvfCoef ev_c[FX_BLOCK];
    FxSvfCoef ev_c0;
    int32_t ev_count0, ev_count;

    int ctl_left;               /* samples left in the current control block */

    /* DT01 (the code diet): the history and the tape last, so that every field above has a short offset (selcc
       loads an offset above 32767 in a 48-bit instruction, one below it in a 32-bit one); SC02 and before had them
       first (tools/dspsplice/src/sc01/spaceecho.h) */
    float hist[SE_HIST];        /* tape speed integral M (samples) at the end of each control block */
    /* the tape: interleaved A, B frames */
    float ring[2 * SE_RING];
} SpaceEcho;

void spaceecho_init(SpaceEcho *s, uint32_t seed);
/* value: knob 0..127 (fractional allowed; MODE and PAN are rounded to their index) */
void spaceecho_set(SpaceEcho *s, int param, float value);
void spaceecho_set_bpm(SpaceEcho *s, float bpm);
/* jump all smoothers to their targets (after init + set, before audio) */
void spaceecho_snap(SpaceEcho *s);
/* wet output only; any n >= 0; the result does not depend on how the stream is cut
   into calls (control updates run every 32 samples of the stream) */
void spaceecho_process(SpaceEcho *s, const float *inL, const float *inR,
                       float *outL, float *outR, int n);

/* knob -> physical, for tests and the write-up */
float spaceecho_time_samples(float knob, float bpm);   /* head 1 */
int spaceecho_heads(float mode_knob);                  /* bit k set = head k+1 sounds */
float spaceecho_fb(float knob);                        /* loop gain per head, 0..1.1 */
float spaceecho_center_hz(float knob);
float spaceecho_bw_oct(float knob);
float spaceecho_q(float bw_knob);                      /* Q of both band edges */
float spaceecho_trim(float bw_knob);                   /* passband trim for the resonance */

#endif
