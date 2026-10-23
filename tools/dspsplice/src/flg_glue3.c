/* SPDX-License-Identifier: GPL-2.0-or-later */
/* flg_glue3.c - FL02: the flanger (fx/flanger.c) in the stock chorus slot, flg_glue2.c's knob map (T5.6) and the
 * speed in sync with the tempo (notes/T5.6-flanger-retune.md section 7).
 *
 * flg_glue2.c (FL01) with one change: the chorus page's knob B (SPD) goes through flg_spd (flg_sync.c, in L1 block 2's
 * code region 0x310000, where the tape delay ran on the unit), which reads its frame word whole. The main OS
 * (flanger-sync-dn2) keeps a synced speed in the knob's own value, (a << 8) | i with i = FL_SYNC 1..12; flg_spd plays
 * those 12 values at the tempo (FL_SYNC i, flanger_set_bpm) and every other value as FL01 does (FL_SPD = the knob).
 * A free knob takes the same calls as on FL01, so a kit that never had a synced speed sounds as on FL01, bit for bit.
 * While synced, flg_spd runs every block (it leaves last[1] at -1; the tempo can move). The slot (flg_slot3.h, shared with flg_sync.c) is FL01's
 * with its unused counter field named `sync`, the FL_SYNC applied (no tool read the counter). The magic is "FLG3".
 *
 * Written for selcc's known bugs (notes/T3.11 11.3, 12.7): no comparison initialisers, one declaration
 * per variable, no table indexed by a fresh integer, no unions.
 */
#include <stdint.h>
#define FLU_DSP
#include "flanger_unit.h"
#include "fxio.h"

#include "flg_slot3.h"

#ifdef FLG_NATIVE
FlgSlot flg_native_slot;            /* the Mac reference build and tools/sharcvm/tests/flgcheck.py (flg_sync.c too) */
uint32_t flg_slot_size(void) { return (uint32_t)sizeof(FlgSlot); }
#endif

void flg_init(void)
{
    FlgSlot *t = SLOT;
    Flanger *s = &t->fl;
    flanger_init(s);            /* its defaults are version 2's fixed settings (fx/flanger_unit.h) */
    flanger_snap(s);
    t->last[0] = -1.0f;
    t->last[1] = -1.0f;
    t->last[2] = -1.0f;
    t->last[3] = -1.0f;
    t->fresh = 1;
    t->sync = 0;
    t->magic = FLG_MAGIC;
}

/* a parameter float (knob/127) -> knob 0..FLU_KMAX (126.999: WDTH 127 would overflow the LFO offset,
   fx/flanger_unit.h); NaN or out of range -> clamped */
static float flg_knob(float x)
{
    float v;
    v = x * 127.0f;
    if (!(v >= 0.0f))
        v = 0.0f;
    if (v > FLU_KMAX)
        v = FLU_KMAX;
    return v;
}

void flg_process(const float *p, const FxIo *io)
{
    FlgSlot *t = SLOT;
    Flanger *s;
    float v;
    float w;
    float **in;
    float **out;
    if (t->magic != FLG_MAGIC)
        flg_init();
    s = &t->fl;
    /* as flg_glue.c: a knob is applied only when it changed. DPTH and TIME go through the map together,
       last: on the first block (last[] at -1) that is always, so the snap waits there for all four */
    /* FL02: SPD through flg_spd when it moves (as FL01). While synced flg_spd leaves last[1] at -1, which no knob
       equals, so it runs every block (the tempo can move) */
    v = flg_knob(p[1]);
    if (v != t->last[1]) {
        t->last[1] = v;
        flg_spd(p[1], v, io->bpm);
    }
    v = flg_knob(p[3]);
    if (v != t->last[3]) {
        flanger_set(s, FL_WDTH, v);
        t->last[3] = v;
    }
    w = flg_knob(p[0]);
    v = flg_knob(p[2]);
    if (v != t->last[2] || w != t->last[0]) {
        flanger_unit_td2(s, v, w);
        t->last[0] = w;
        t->last[2] = v;
        if (t->fresh) {
            flanger_snap(s);
            t->fresh = 0;
        }
    }
    in = io->in;
    out = io->out;
    flanger_process(s, in[0], in[1], out[0], out[1], io->n);
}
