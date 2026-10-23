/* SPDX-License-Identifier: GPL-2.0-or-later */
/* flg_glue2.c - T5.6: the flanger (fx/flanger.c) in the stock chorus slot, with the retuned knob map.
 *
 * flg_glue.c (FLG1 to CMP4) with one change: the chorus page's knob C (HPF on the page) is TIME, the
 * whole range from phaser-like through flanger to chorus on one knob, and the four knobs go through
 * fx/flanger_unit.h's version 2 (TIME sets the centre delay; past the flanger zone it fades the feedback
 * to 0 and the depth range to a tenth; the input high-pass is fixed at 64 Hz). The hooks, the stubs, the
 * slot and its size are flg_glue.c's; the magic is "FLG2". The map runs only when TIME or DPTH moves.
 *
 * Written for selcc's known bugs (notes/T3.11 11.3, 12.7): no comparison initialisers, one declaration
 * per variable, no table indexed by a fresh integer, no unions.
 */
#include <stdint.h>
#define FLU_DSP
#include "flanger_unit.h"
#include "fxio.h"

#define FLG_MAGIC 0x464c4732u      /* "FLG2" */
#define FLG_SLOT_BYTES 0x8400       /* the FILL block in tools/dspsplice/build.py */

typedef struct {
    uint32_t magic;                 /* FLG_MAGIC once flg_init has run */
    uint32_t fresh;                 /* 1 until the first block has applied the knobs and snapped */
    uint32_t blocks;                /* kept for flg_glue.c's layout; FLG2 does not count (no tool reads it) */
    float last[4];                  /* the unit knobs last applied: DPTH, SPD, TIME, WDTH (0..127) */
    Flanger fl;
} FlgSlot;

/* the slot must fit the zero-filled range (a negative array size fails the build) */
typedef char flg_slot_fits[(sizeof(FlgSlot) <= FLG_SLOT_BYTES) ? 1 : -1];

#ifdef FLG_NATIVE
static FlgSlot flg_native_slot;     /* the Mac reference build and tools/sharcvm/tests/flgcheck.py */
#define SLOT (&flg_native_slot)
uint32_t flg_slot_size(void) { return (uint32_t)sizeof(FlgSlot); }
#else
#define SLOT ((FlgSlot *)0x300000)
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
    t->blocks = 0;
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
    v = flg_knob(p[1]);
    if (v != t->last[1]) {
        flanger_set(s, FL_SPD, v);
        t->last[1] = v;
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
