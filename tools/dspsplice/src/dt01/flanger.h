/* SPDX-License-Identifier: GPL-2.0-or-later */
/*
 * flanger.h - a flanger / comb for the Digitone II chorus slot (T3.11/T5.1, G3).
 *
 * DT01 (notes/DT01-code-diet.md): fx/flanger.h with the rings last in Flanger (every other field a short offset
 * for selcc); otherwise the same text. FlgSlot (flg_slot3.h) is the same size.
 *
 * Portable C99, float only, no libc, no malloc. A classic design of our own,
 * shaped by the traits the owner likes in Max for Live's y-flanger (notes/T3.11
 * 12.5): a delay range from 0.05 ms (flanging) up to 77 ms (chorus, slapback,
 * pitched combs), damped feedback that rings like a plucked string at short
 * delays (Karplus-Strong), polarity, a stereo LFO phase offset and a stereo time
 * offset, six LFO shapes with slew, tempo sync, and a high-pass.
 *
 * State about 33 KB, mostly two 4,099-sample rings: more than the stock chorus's
 * 4,928 bytes of DDR (T3.4), so it needs a home of its own (12.5: L1 block 2,
 * free L2, or the room the new tape delay leaves in the stock delay's 4 MB).
 * FL_RING 512 gives a 10 ms build that fits the stock chorus's space.
 *
 * Signal flow (per channel):
 *   in -> 1-pole high-pass (HPF) -> + soft-limited feedback -> ring
 *   ring -> 4-point Hermite read at delay d(t) -> POL -> wet out
 *        -> DAMP low-pass -> FDBK -> soft clip -> back into the ring
 *   d(t) = sweep in log-delay around MAN (1 semitone per step, MAN 66 = A4 as a
 *          comb pitch), DPTH octaves each way, moved by the LFO (SHP, SLEW, SPD or
 *          SYNC); the right channel's LFO runs WDTH (0..180 degrees) ahead and its
 *          delay is OFS (0..5 ms) longer.
 * The output is the wet (delayed) signal only, like the stock chorus. The FX bus
 * adds the dry track signal, so with the send and VOL at unity the sum is the
 * classic flanger x(t) + x(t - d(t)) with full-depth notches. DEL and REV are the
 * bus's chorus sends (frame k29, k30), not part of this code.
 *
 * Parameters take the DN2 knob value 0..127 (enumerations take the index).
 */
#ifndef FLANGER_H
#define FLANGER_H

#include <stdint.h>
#include "fxmath.h"

#ifndef FL_RING
#define FL_RING 4096     /* per channel, a power of two: 85 ms; the longest delay is 77 ms + OFS */
#endif

/* every parameter of both proposed layouts (12.5) */
enum {
    FL_DPTH = 0,  /* sweep depth: 0..3.32 octaves of delay each way around MAN */
    FL_SPD,       /* free LFO rate 0.02..10 Hz, log (0 = 50 s per cycle, 64 = 0.46 Hz) */
    FL_HPF,       /* high-pass on the flanger input (the wet path) 20 Hz..2 kHz, log */
    FL_WDTH,      /* stereo phase: the right LFO 0..180 degrees ahead of the left */
    FL_FDBK,      /* bipolar feedback -99..+99 % (64 = 0), soft-limited */
    FL_MAN,       /* manual: the centre delay, 0.05 ms * 2^(v/12): 1 semitone per step as a comb
                     pitch, 66 = 2.27 ms = A4 (440 Hz), 50 = 0.9 ms, 127 = 77 ms */
    FL_SHP,       /* LFO shape: 0 sine, 1 triangle, 2 saw up, 3 saw down, 4 square, 5 random step */
    FL_SLEW,      /* LFO slew (a lag on its output) 1 ms..0.5 s, log: rounds square and step shapes */
    FL_DAMP,      /* low-pass in the feedback loop: 0 none, 12 about 10 kHz, 64 1.6 kHz, 127 300 Hz */
    FL_SYNC,      /* 0 free (SPD), 1..12 = one LFO cycle per 16, 8, 4, 2, 1 bars, 1/2, 1/4., 1/4,
                     1/8., 1/8, 1/16, 1/32 at the project tempo */
    FL_OFS,       /* stereo time offset: the right delay 0..5 ms longer (square law) */
    FL_POL,       /* wet polarity: 0 normal, 1 inverted (notches move: hollow "negative" flanging) */
    FL_NPARAMS
};

typedef struct {
    int32_t w;

    float knob[FL_NPARAMS];
    float bpm;
    int dirty;

    /* targets (from the knobs) and control-rate smoothed values */
    float t_lc, lc;            /* centre delay, log2 samples */
    float t_dep, dep;          /* half-range, octaves */
    float t_off, off;          /* right-channel phase offset, cycles (0..0.5) */
    float t_ofs, ofs;          /* right-channel time offset, samples */
    float t_lh, lh;            /* HPF corner, log2 Hz */
    float t_da, da;            /* damping one-pole coefficient (1 = none) */
    float t_fb, fb, fb_i;      /* feedback and its per-sample ramp */
    float t_pg, pg1, pg, pg_i; /* wet polarity gain (+1 / -1, crossfaded by two one-poles in a row) */
    float sl_h;                /* LFO slew: one-pole coefficient per half block */
    int shape;
    uint32_t rate, rate_h;     /* LFO phase increment per block and per half block, 2^-32 cycles */
    uint32_t ph;               /* LFO phase of the left channel, 2^-32 cycles */
    uint32_t rng;              /* random-step LFO */
    float rnd[2], rnd_next;    /* random step: current value per channel, the next one for the left */
    float ls[2];               /* slewed LFO value at the block end, per channel */

    /* per channel: delay trajectory of the current control block (quadratic,
       run by forward differences), the delay at the block end, filter states */
    float dly[2], dv[2], dacc[2], dend[2];
    float hpG;
    float hp_s[2], fbl_s[2];

    int ctl_left;

    /* rings: N samples plus 3 guard samples that repeat the first 3, so the 4
       samples of a read are always consecutive. DT01: last, so that every field
       above has a short offset (selcc loads an offset above 32767 in a 48-bit
       instruction, one below it in a 32-bit one) */
    float ring[2][FL_RING + 3];
} Flanger;

void flanger_init(Flanger *s);
void flanger_set(Flanger *s, int param, float value);
void flanger_set_bpm(Flanger *s, float bpm);
/* jump all smoothers to their targets (after init + set, before audio) */
void flanger_snap(Flanger *s);
/* wet output only; any n >= 0; the result does not depend on how the stream is
   cut into calls (control updates run every 32 samples of the stream) */
void flanger_process(Flanger *s, const float *inL, const float *inR,
                     float *outL, float *outR, int n);
/* knob -> physical, for tests and the write-up */
float flanger_rate_hz(const Flanger *s);      /* the LFO rate now set (free or synced) */
float flanger_man_ms(float knob);
float flanger_depth_oct(float knob);
float flanger_hpf_hz(float knob);
float flanger_damp_hz(float knob);            /* corner of the damping one-pole (0: none) */
float flanger_fb(float knob);
/* the raw LFO value (-1..1) at phase p (cycles) for a shape (not random step) */
float flanger_lfo(float p, int shape);
/* the delay in samples the sweep gives at LFO value l (before OFS and damping compensation) */
float flanger_delay_at(const Flanger *s, float l);

#endif
