/* SPDX-License-Identifier: GPL-2.0-or-later */
/* spng_glue.c - SP01: SPNG, the spring reverb (fx/spring.c, notes/T5.5-spring-reverb.md), in the stock reverb slot
 * (notes/SP01-spring-pack.md). In L1 block 2's code region after flg_sync.c (0x310000-0x315f8c, where FTD1 to CMP3's
 * tape delay ran on the unit): tools/dspsplice/build.py recipe SP01.
 *
 * The hook stubs (tools/dspsplice/stub.py, in code2 after this file) call:
 *   spg_init()               from the effect init 0x1c1445 (its CJUMP at 0x1c1643), in place of the stock reverb's init
 *                            0xb83dc1, which is NOT run: it would set up its delay lines over our state. What it writes
 *                            is its own DDR state, its parameter struct (which the render's unpack rewrites every
 *                            block) and the stack (scripts/sp01_build.py checks it in sharcvm);
 *   spg_process(params, io)  from the FX bus 0x1c1738 (its CJUMP at 0x1c1d11), in place of the stock reverb 0xb836bc:
 *                            params = the reverb's parameter struct 0x257918, io = the bus's I/O descriptor (fxio.h).
 * The spring writes the wet signal only, like the stock reverb: the bus adds VOL (k 47) as before, and its input is the
 * bus's reverb input as before (the track sends, the external input's sends, chorus -> reverb k 30, delay -> reverb
 * k 38).
 *
 * The page (stock row -> SPNG; the parameter struct holds float(w) x 1/32512 = v/127 for the frame word w = v << 8,
 * smoothed by the ColdFire, so fractional on the way to a new value; notes/T3.4 section 1):
 *   A PRE  (+0x00, k 41) -> PRE        B DEC  (+0x04, k 42) -> DEC
 *   C FREQ (+0x08, k 43) -> MODE       D GAIN (+0x0c, k 44) -> DWEL
 *   E HPF  (+0x10, k 45) -> HPF        F LPF  (+0x14, k 46) -> TONE
 *   G k 48 "Reverb FX Routing" (frame word 0xa28) -> WDTH. The unpack keeps only (w != 0) of it, as a byte at
 *     0x2579d4 that nothing reads (scripts/sp01_k48check.py); the render copies the whole received frame to the image
 *     at DM 0x25c48c before the FX bus runs, so word 0xa28 is the low half of the 32-bit word at 0x25ceb4. Read once per
 *     block: WDTH = w / 256 (every existing kit stores 1 there: almost mono, as a real tank).
 *   H (none), LEVEL VOL: the bus's.
 * Each knob is applied only when it changed (spring_set marks the targets dirty; the spring glides 50 ms). On the first
 * block all seven are set (MODE among them, so the springs follow MODE from the start), then snapped: the first block
 * plays the kit's settings settled, as fx/render.c starts (init, set, snap).
 * Knob values come back on the frame's 1/256 grid (as se_glue.c: p x 127 alone misses some integers by one float ulp),
 * so a settled knob is the integer the kit stores, bit for bit what fx/render.c's -p NAME=v sets.
 *
 * State: one SpgSlot at the start of the stock reverb's DDR state (0x80000018, 0x59114 bytes, notes/T3.4 3.3), free
 * once the stock reverb and its init no longer run; the stock boot stream zero-fills it.
 * Written for selcc's known bugs (se_glue.c, flg_glue3.c): no comparison initialisers, one declaration per variable, no
 * table indexed by a fresh integer, no unions, and no array indexed by a variable after a call (each knob written out
 * with constant indices).
 */
#include <stdint.h>
#include "spring.h"
#include "fxio.h"

#define SPG_MAGIC 0x53504e31u           /* "SPN1" */
#define SPG_REGION_BYTES 0x59114         /* the stock reverb's DDR state, 0x80000018.. (notes/T3.4 3.3) */
#define SPG_K48_ADDR 0x25ceb4            /* frame image 0x25c48c + 0xa28: low half = k 48 */

typedef struct {
    uint32_t magic;                     /* SPG_MAGIC once spg_init has run */
    uint32_t fresh;                     /* 1 until the first block has set the knobs and snapped */
    float last[7];                      /* the knobs as applied: PRE, DEC, MODE, DWEL, HPF, TONE, WDTH (0..127) */
    Spring sp;
} SpgSlot;

/* the slot must fit the region (selcc accepts a negative array size, so build.py checks the size too) */
typedef char spg_slot_fits[(sizeof(SpgSlot) <= SPG_REGION_BYTES) ? 1 : -1];

#ifdef SPG_NATIVE
static SpgSlot spg_native_slot;         /* the Mac reference build (scripts/sp01_verify.py) */
int32_t spg_native_k48;                 /* the 32-bit image word at 0x25ceb4 as the DSP read it */
#define SLOT (&spg_native_slot)
#define K48_WORD (spg_native_k48)
uint32_t spg_slot_size(void) { return (uint32_t)sizeof(SpgSlot); }
void *spg_native_slot_ptr(void) { return &spg_native_slot; }
#else
#define SLOT ((SpgSlot *)0x80000018)
#define K48_WORD (*(const int32_t *)SPG_K48_ADDR)
#endif

/* sizeof(SpgSlot) as the SHARC build computes it, for build.py's slot check */
const uint32_t spg_slot_bytes = (uint32_t)sizeof(SpgSlot);

void spg_init(void)
{
    SpgSlot *t = SLOT;
    spring_init(&t->sp);
    t->last[0] = -1.0f;
    t->last[1] = -1.0f;
    t->last[2] = -1.0f;
    t->last[3] = -1.0f;
    t->last[4] = -1.0f;
    t->last[5] = -1.0f;
    t->last[6] = -1.0f;
    t->fresh = 1;
    t->magic = SPG_MAGIC;
}

/* a parameter the unpack scaled by 1/32512 back to its knob value 0..127, on the frame's 1/256 grid; NaN -> 0 */
static float spg_knob(float p)
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

void spg_process(const float *p, const FxIo *io)
{
    SpgSlot *t = SLOT;
    Spring *s;
    float v;
    int32_t w;
    float **in;
    float **out;
    if (t->magic != SPG_MAGIC)
        spg_init();
    s = &t->sp;
    v = spg_knob(p[0]);
    if (v != t->last[0]) {
        spring_set(s, SP_PREDLY, v);
        t->last[0] = v;
    }
    v = spg_knob(p[1]);
    if (v != t->last[1]) {
        spring_set(s, SP_DEC, v);
        t->last[1] = v;
    }
    v = spg_knob(p[2]);
    if (v != t->last[2]) {
        spring_set(s, SP_MODE, v);
        t->last[2] = v;
    }
    v = spg_knob(p[3]);
    if (v != t->last[3]) {
        spring_set(s, SP_DWEL, v);
        t->last[3] = v;
    }
    v = spg_knob(p[4]);
    if (v != t->last[4]) {
        spring_set(s, SP_HPF, v);
        t->last[4] = v;
    }
    v = spg_knob(p[5]);
    if (v != t->last[5]) {
        spring_set(s, SP_TONE, v);
        t->last[5] = v;
    }
    /* G: k 48's frame word whole (v << 8, smoothed), clamped to the knob's range */
    w = K48_WORD & 0xffff;
    if (w > 32512)
        w = 32512;
    v = (float)w * 0.00390625f;
    if (v != t->last[6]) {
        spring_set(s, SP_WDTH, v);
        t->last[6] = v;
    }
    if (t->fresh) {
        spring_snap(s);
        t->fresh = 0;
    }
    in = io->in;
    out = io->out;
    spring_process(s, in[0], in[1], out[0], out[1], io->n);
}
