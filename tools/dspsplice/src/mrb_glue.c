/* SPDX-License-Identifier: GPL-2.0-or-later */
/* mrb_glue.c - MR03: MRB, the morphing reverb on a budget (fx/mrb.c, notes/MR02-budget-reverb.md), in the stock reverb
 * slot (notes/MR03-eeem.md). spng_glue.c's glue (SP01/SP02) with MRB in place of the spring; tools/dspsplice/build.py
 * pack recipes with a 4th letter M (EEEM).
 *
 * The hook stubs (tools/dspsplice/stub.py) call:
 *   mrb_glue_init()              from the effect init 0x1c1445 (its CJUMP at 0x1c1643), in place of the stock reverb's
 *                                init 0xb83dc1, which is NOT run: it would set up its delay lines over our state;
 *   mrb_glue_process(params, io) from the FX bus 0x1c1738 (its CJUMP at 0x1c1d11), in place of the stock reverb
 *                                0xb836bc: params = the reverb's parameter struct 0x257918, io = the bus's I/O
 *                                descriptor (fxio.h).
 * MRB writes the wet signal only, like the stock reverb: the bus adds VOL (k 47) as before, and its input is the bus's
 * reverb input as before.
 *
 * The page (stock row -> MRB; the parameter struct holds float(w) x 1/32512 = v/127 for the frame word w = v << 8,
 * smoothed by the ColdFire; notes/T3.4 section 1):
 *   A PRE  (+0x00, k 41) -> MB_PRE     B DEC  (+0x04, k 42) -> MB_DEC
 *   C FREQ (+0x08, k 43) -> MB_CHAR    D GAIN (+0x0c, k 44) -> MB_SIZE
 *   E HPF  (+0x10, k 45) -> MB_HPF     F LPF  (+0x14, k 46) -> MB_TONE
 *   G k 48: unused (left as stock). LEVEL VOL: the bus's.
 * Each knob is applied only when it changed; on the first block all six are set, then snapped: the first block plays
 * the kit's settings settled. Knob values come back on the frame's 1/256 grid (spg_knob's decoding), so a settled knob
 * is the integer the kit stores.
 *
 * State: one MrbSlot at the start of the stock reverb's DDR state (0x80000018, 0x59114 bytes, notes/T3.4 3.3), free
 * once the stock reverb and its init no longer run; the stock boot stream zero-fills it. The constant data of mrb.c
 * and this file go after it in the same region (recipe key dmda_ddr).
 * Written for selcc's known bugs: one declaration per variable, no comparison initialisers, no table indexed by a
 * fresh integer, no unions, no array indexed by a variable after a call (each knob written out with constant indices),
 * no void function ending in a switch.
 */
#include <stdint.h>
#include "mrb.h"
#include "fxio.h"

#define MRB_GLUE_MAGIC 0x4d524231u      /* "MRB1" */
#define MRB_REGION_BYTES 0x59114         /* the stock reverb's DDR state, 0x80000018.. (notes/T3.4 3.3) */

typedef struct {
    uint32_t magic;                     /* MRB_GLUE_MAGIC once mrb_glue_init has run */
    uint32_t fresh;                     /* 1 until the first block has set the knobs and snapped */
    float last[6];                      /* the knobs as applied: PRE, DEC, CHAR, SIZE, HPF, TONE (0..127) */
    Mrb mb;
} MrbSlot;

/* the slot must fit the region (selcc accepts a negative array size, so build.py checks the size too) */
typedef char mrb_slot_fits[(sizeof(MrbSlot) <= MRB_REGION_BYTES) ? 1 : -1];

#ifdef MRB_NATIVE
static MrbSlot mrb_native_slot;         /* the Mac reference build (tools/dspsplice/verify_eeem.py) */
#define SLOT (&mrb_native_slot)
uint32_t mrb_slot_size(void) { return (uint32_t)sizeof(MrbSlot); }
void *mrb_native_slot_ptr(void) { return &mrb_native_slot; }
#else
#define SLOT ((MrbSlot *)0x80000018)
#endif

/* sizeof(MrbSlot) as the SHARC build computes it, for build.py's slot check */
const uint32_t mrb_slot_bytes = (uint32_t)sizeof(MrbSlot);

void mrb_glue_init(void)
{
    MrbSlot *t = SLOT;
    mrb_init(&t->mb);
    t->last[0] = -1.0f;
    t->last[1] = -1.0f;
    t->last[2] = -1.0f;
    t->last[3] = -1.0f;
    t->last[4] = -1.0f;
    t->last[5] = -1.0f;
    t->fresh = 1;
    t->magic = MRB_GLUE_MAGIC;
}

/* a parameter the unpack scaled by 1/32512 back to its knob value 0..127, on the frame's 1/256 grid; NaN -> 0 */
static float mrb_knob(float p)
{
    float v;
    int32_t w;
    v = p * 32512.0f + 0.5f;
    if (!(v >= 0.0f))
        v = 0.0f;
    if (v > 32512.5f)
        v = 32512.5f;
    w = (int32_t)v;
    return (float)w * 0.00390625f;
}

void mrb_glue_process(const float *p, const FxIo *io)
{
    MrbSlot *t = SLOT;
    Mrb *s;
    float v;
    float **in;
    float **out;
    if (t->magic != MRB_GLUE_MAGIC)
        mrb_glue_init();
    s = &t->mb;
    v = mrb_knob(p[0]);
    if (v != t->last[0]) {
        mrb_set(s, MB_PRE, v);
        t->last[0] = v;
    }
    v = mrb_knob(p[1]);
    if (v != t->last[1]) {
        mrb_set(s, MB_DEC, v);
        t->last[1] = v;
    }
    v = mrb_knob(p[2]);
    if (v != t->last[2]) {
        mrb_set(s, MB_CHAR, v);
        t->last[2] = v;
    }
    v = mrb_knob(p[3]);
    if (v != t->last[3]) {
        mrb_set(s, MB_SIZE, v);
        t->last[3] = v;
    }
    v = mrb_knob(p[4]);
    if (v != t->last[4]) {
        mrb_set(s, MB_HPF, v);
        t->last[4] = v;
    }
    v = mrb_knob(p[5]);
    if (v != t->last[5]) {
        mrb_set(s, MB_TONE, v);
        t->last[5] = v;
    }
    if (t->fresh) {
        mrb_snap(s);
        t->fresh = 0;
    }
    in = io->in;
    out = io->out;
    mrb_process(s, in[0], in[1], out[0], out[1], io->n);
}
