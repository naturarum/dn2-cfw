/* SPDX-License-Identifier: GPL-2.0-or-later */
/* flg_sync.c - FL02: the flanger's speed knob (the chorus page's B, frame word 0x9fc) free or in sync with the tempo
 * (notes/T5.6-flanger-retune.md section 7). Called by flg_glue3.c's flg_process; in L1 block 2's code region
 * (0x310000-0x315f8c, where the tape delay ran on the unit: tools/dspsplice/build.py recipe FL02).
 *
 * The main OS (vendor/elekloader/examples/flanger-sync-dn2) keeps a synced speed in the knob's own 16-bit value:
 *     S(i) = (a_i << 8) | i, i = 1..12 = fx/flanger.c's FL_SYNC (one LFO cycle per 16, 8, 4, 2, 1 bars, 1/2, 1/4.,
 *     1/4, 1/8., 1/8, 1/16, 1/32), a_i = 9, 23, 37, 52, 66, 80, 86, 94, 100, 108, 122, 126
 * (the free SPD closest to that speed at 120 BPM). The ColdFire's smoother settles the frame word exactly on the
 * stored value (T3.3: word = round(state / 65536), the state's error decays to under 34/65536), so the DSP sees S(i)
 * itself once the knob has arrived; on the way there the words between are free speeds, as on FL01.
 * The stock unpack makes the parameter float p = word x (1/32512); w = p x 32512 + 0.5, truncated, is the word again
 * (its error is below 0.004 for every word up to 0x7f00).
 *
 * A word glides there: a word on the way can be a synced value for a few frames (a glide to the free speed 9 from
 * above passes 0x0901 for up to 37 frames). So a new synced value plays only after its word has held FLS_HOLD
 * blocks; until then the knob plays what it played (a free knob as FL01, the same call), and a word that is not a
 * synced value plays at once. The slot (flg_slot3.h) is flg_glue3.c's: `sync` = the FL_SYNC playing (bits 0-7), the
 * synced value waiting and its count of blocks (bits 8-15, 16-23).
 * Synced: FL_SYNC = i and the tempo (io +0x14, clamped to 30..300 BPM as flanger_set_bpm does) whenever either
 * changes; FL_SPD keeps its last free value. While synced or waiting, last[1] = -1, so that flg_glue3.c calls again on
 * the next block. Free: FL_SPD = v, flg_glue2.c's knob, and FL_SYNC back to 0 if it was
 * synced. A knob that was never synced gets FL01's call (flanger_set(s, FL_SPD, v)) and nothing else; while a word
 * on its way waits, that call comes every block (the same value again: the flanger's state the same).
 *
 * Written for selcc's known bugs (notes/T3.11 11.3, 12.7): no table (the 12 values are compared one by one), one
 * declaration per variable, no comparison initialisers.
 */
#include <stdint.h>
#include "flg_slot3.h"

/* blocks a synced word must hold before it plays: a glide of the ColdFire's smoother stays on a word it passes for at
   most 37 frames (the word 1 above or below where it settles), so a word held 48 blocks (32 ms) is where it settled */
#define FLS_HOLD 48u

/* the frame word whole -> its synced value 1..12, or 0 (a free speed) */
static uint32_t flg_decode(float p)
{
    float x;
    int32_t w;
    uint32_t sy;
    x = p * 32512.0f + 0.5f;
    if (!(x >= 0.0f))
        x = 0.0f;
    if (x > 32767.0f)
        x = 32767.0f;
    w = (int32_t)x;
    sy = 0;
    if (w == 0x0901)
        sy = 1;
    else if (w == 0x1702)
        sy = 2;
    else if (w == 0x2503)
        sy = 3;
    else if (w == 0x3404)
        sy = 4;
    else if (w == 0x4205)
        sy = 5;
    else if (w == 0x5006)
        sy = 6;
    else if (w == 0x5607)
        sy = 7;
    else if (w == 0x5e08)
        sy = 8;
    else if (w == 0x6409)
        sy = 9;
    else if (w == 0x6c0a)
        sy = 10;
    else if (w == 0x7a0b)
        sy = 11;
    else if (w == 0x7e0c)
        sy = 12;
    return sy;
}

void flg_spd(float p, float v, float bpm)
{
    FlgSlot *t = SLOT;
    Flanger *s;
    uint32_t on;
    uint32_t sy;
    uint32_t cand;
    uint32_t n;
    uint32_t play;
    s = &t->fl;
    on = t->sync & 0xffu;
    cand = 0;
    sy = flg_decode(p);
    play = sy;
    if (sy != 0 && sy != on) {
        /* a synced value the DSP does not play yet: only once the word has held it FLS_HOLD blocks */
        n = 1;
        if (((t->sync >> 8) & 0xffu) == sy)
            n = (t->sync >> 16) + 1;
        if (n < FLS_HOLD) {
            cand = (sy << 8) | (n << 16);
            play = on;
        }
    }
    if (play == 0) {
        flanger_set(s, FL_SPD, v);              /* FL01's call */
        if (on != 0)
            flanger_set(s, FL_SYNC, 0.0f);
    } else {
        if (!(bpm >= 30.0f))
            bpm = 30.0f;
        if (bpm > 300.0f)
            bpm = 300.0f;
        if (bpm != s->bpm)
            flanger_set_bpm(s, bpm);
        if (play != on)
            flanger_set(s, FL_SYNC, (float)play);
    }
    t->sync = play | cand;
    if (play != 0 || cand != 0)
        t->last[1] = -1.0f;                     /* no knob is -1: flg_glue3.c calls again next block */
}
