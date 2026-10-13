/* SPDX-License-Identifier: GPL-2.0-or-later */
/* se_glue.c - T5.3: the Space Echo (fx/spaceecho.c, notes/T5.2) in the stock delay slot, with the
 * one-page delay layout the owner approved on 2026-10-13 (SE01). Modelled on td_glue.c (FTD1).
 *
 * The hook stubs (tools/dspsplice/stub.py) call:
 *   se_init()                 from the effect init 0x1c1445, in place of the stock delay init 0xb848cb,
 *                             which is NOT run (it would clear its rings over our state; what it
 *                             writes is its own DDR state, its parameter struct, which the render's
 *                             unpack rewrites every block, and stack: notes/T3.8 section 9);
 *   se_process(params, io)    from the FX bus 0x1c1738, in place of the stock delay 0xb8482c:
 *                             params = the delay parameter struct 0x257934, io = the bus's I/O
 *                             descriptor (fxio.h; +0x14 is the tempo in BPM).
 * The Space Echo writes the wet signal only, like the stock delay; the bus adds VOL and the REV send.
 * That wet signal is bounded by the echo's own ceiling (at most 0.7; spaceecho.c SE_LIM_*, added after the
 * T5.3 review: without it three heads running away held 4.24, 18 dB over the stock delay's runaway).
 *
 * What the parameter struct holds (the render's unpack 0x1c2dfc.., notes/T3.4 section 1; frame
 * word w = knob value v << 8, smoothed by the ColdFire, so fractional on the way to a new value):
 *   +0x00 TIME  float(w) x 1/32512 = v/127       +0x04 X    float(w) = 256 v, no scale
 *   +0x08 WID   float(w) x 2^-15   = v/128       +0x0c FDBK float(w) x 2^-15 = v/128
 *   +0x10 HPF   float(w) x 1/32512 = v/127       +0x14 LPF  float(w) x 1/32512 = v/127
 * [verified in sharcvm: X 0..6 arrives as 0, 256, .., 1536 at +4; tools/dspsplice/verify_se.py]
 * AGE has no slot in the struct: the unpack never reads frame word 0xa18 (k 40, the hidden
 * "Delay FX Routing" row, which the ColdFire side puts on the delay page's G slot). The render
 * copies the whole received frame to the image at DM 0x25c48c before the FX bus runs, so word
 * 0xa18 is the low half of the 32-bit word at 0x25cea4 (the high half is word 0xa1a, reverb PRE)
 * [verified in sharcvm]. Read once per block.
 *
 * Layout (encoder: stock row -> Space Echo):
 *   A TIME -> TIME (head 1; the stock and FTD1 tempo law)
 *   B X    -> MODE 0..6 = heads 1, 2, 3, 1+2, 2+3, 1+3, 1+2+3 (w / 256; spaceecho rounds to the index)
 *   C WID  -> WIDTH and PAN folded, d = v - 64 as shown (-64..+63):
 *               |d| <= 1:  MONO, WIDTH 0 (all mono; 64 is exactly mono)
 *               d < -1:    ALT,  WIDTH = (-d - 1) x 127/63   (d -64: 127)
 *               d > +1:    RND,  WIDTH = (d - 1) x 127/62    (d +63: 127)
 *             continuous at the edges (WIDTH 0 there, where the three PAN modes sound the same)
 *   D FDBK -> INTENSITY (v)
 *   E HPF  -> CENTER (v)
 *   F LPF  -> BW (v; 127 = no filter at all, the stock default "open")
 *   G k 40 -> AGE: v <= 1 clean tape exactly (every existing kit stores 1), 1..2 -> 0..2, then v
 *   H REV and LEVEL VOL: the bus's, unchanged.
 * TIME, HPF and LPF are snapped to the frame's 1/256 step: p x 127 alone misses 8 of the 128
 * integers by one float ulp (9, 13, 18, 26, 36, 52, 72, 104).
 *
 * State: one SeSlot at the start of the stock delay's DDR state (0x8005912c, 4,194,908 bytes), which
 * the stock boot stream already zero-fills. Same selcc rules as td_glue.c and spaceecho.c: no
 * comparison initialisers, one declaration per variable, flags set with if statements, no table
 * indexed by a fresh integer, no unions, no macro parameter named like a literal suffix; and no array
 * indexed by a variable after a call. That last one is a sixth selcc miscompile, found here (T5.3):
 *     typedef struct { int a; int b; int c; float last[9]; } S;  void f(float x);
 *     void g(S *t, int k, float v) { if (v != t->last[k]) { f(v); t->last[k] = v; } }
 * selcc -O1 keeps t in the caller-saved R3 across the call to f and forms the store's address from
 * it; in an earlier version of this file (a helper se_apply(t, k, param, v)) it formed the address
 * as t + 12 + 4 t, so every knob change wrote the knob value into the tape at 0x801bd5e8.
 */
#include <stdint.h>
#include "spaceecho.h"
#include "fxio.h"

#define SE_GLUE_MAGIC 0x53454531u   /* "SEE1" */
#define SE_REGION_BYTES 4194908     /* 0x8005912c-0x80459388 (T3.2 2.6, T3.4 3.2) */
#define SE_SEED 1u                  /* the seed fx/render.c uses: the same noise as the A/B renders */
#define SE_K40_ADDR 0x25cea4        /* frame image 0x25c48c + 0xa18: low half = k 40 */

typedef struct {
    uint32_t magic;
    uint32_t fresh;                 /* 1 until the first block with a valid tempo has snapped */
    uint32_t blocks;
    float last[9];                  /* as applied: TIME, MODE, WIDTH, PAN, INT, CTR, BW, AGE, tempo */
    SpaceEcho se;
} SeSlot;

typedef char se_slot_fits[(sizeof(SeSlot) <= SE_REGION_BYTES) ? 1 : -1];

#ifdef SE_NATIVE
static SeSlot se_native_slot;
int32_t se_native_k40;              /* the Mac reference build: verify_se.py sets the frame word */
#define SLOT (&se_native_slot)
#define K40_WORD (se_native_k40)
uint32_t se_slot_size(void) { return (uint32_t)sizeof(SeSlot); }
/* the knob values last applied (TIME, MODE, WIDTH, PAN, INT, CTR, BW, AGE, tempo), for the law checks */
float se_native_last(int k) { return se_native_slot.last[k]; }
void *se_native_slot_ptr(void) { return &se_native_slot; }
#else
#define SLOT ((SeSlot *)0x8005912c)
#define K40_WORD (*(const int32_t *)SE_K40_ADDR)
#endif

void se_init(void)
{
    SeSlot *t = SLOT;
    spaceecho_init(&t->se, SE_SEED);
    t->last[0] = -1.0f;
    t->last[1] = -1.0f;
    t->last[2] = -1.0f;
    t->last[3] = -1.0f;
    t->last[4] = -1.0f;
    t->last[5] = -1.0f;
    t->last[6] = -1.0f;
    t->last[7] = -1.0f;
    t->last[8] = -1.0f;
    t->fresh = 1;
    t->blocks = 0;
    t->magic = SE_GLUE_MAGIC;
}

static float se_clamp(float v, float hi)
{
    if (!(v >= 0.0f))
        v = 0.0f;
    if (v > hi)
        v = hi;
    return v;
}

/* a parameter the unpack scaled by 1/32512 back to its knob value, on the frame's 1/256 grid */
static float se_knob127(float p)
{
    float v;
    int32_t w;
    v = p * 32512.0f + 0.5f;
    v = se_clamp(v, 32512.5f);
    w = (int32_t)v;
    return (float)w * 0.00390625f;
}

void se_process(const float *p, const FxIo *io)
{
    SeSlot *t = SLOT;
    SpaceEcho *s;
    float v;
    float d;
    float pan;
    float bpm;
    int32_t raw;
    float **in;
    float **out;
    if (t->magic != SE_GLUE_MAGIC)
        se_init();
    s = &t->se;
    /* each knob written out with constant indices (the selcc miscompile above) */
    v = se_knob127(p[0]);
    if (v != t->last[0]) {
        spaceecho_set(s, SE_TIME, v);
        t->last[0] = v;
    }
    v = se_clamp(p[1] * 0.00390625f, 6.0f);
    if (v != t->last[1]) {
        spaceecho_set(s, SE_MODE, v);
        t->last[1] = v;
    }
    /* WID: width and pan mode folded around the centre */
    d = se_clamp(p[2] * 128.0f, 127.0f) - 64.0f;
    pan = 0.0f;
    v = 0.0f;
    if (d < -1.0f) {
        pan = 1.0f;
        v = (-1.0f - d) * 2.0158730f;
    }
    if (d > 1.0f) {
        pan = 2.0f;
        v = (d - 1.0f) * 2.0483871f;
    }
    v = se_clamp(v, 127.0f);
    if (v != t->last[2]) {
        spaceecho_set(s, SE_WID, v);
        t->last[2] = v;
    }
    if (pan != t->last[3]) {
        spaceecho_set(s, SE_PAN, pan);
        t->last[3] = pan;
    }
    v = se_clamp(p[3] * 128.0f, 127.0f);
    if (v != t->last[4]) {
        spaceecho_set(s, SE_INT, v);
        t->last[4] = v;
    }
    v = se_knob127(p[4]);
    if (v != t->last[5]) {
        spaceecho_set(s, SE_CTR, v);
        t->last[5] = v;
    }
    v = se_knob127(p[5]);
    if (v != t->last[6]) {
        spaceecho_set(s, SE_BW, v);
        t->last[6] = v;
    }
    /* AGE from frame word 0xa18 (k 40) */
    raw = K40_WORD & 0xffff;
    v = se_clamp((float)raw * 0.00390625f, 127.0f);
    if (v <= 1.0f)
        v = 0.0f;
    if (v > 0.0f && v < 2.0f)
        v = 2.0f * (v - 1.0f);
    if (v != t->last[7]) {
        spaceecho_set(s, SE_AGE, v);
        t->last[7] = v;
    }
    bpm = io->bpm;
    if (bpm >= 30.0f) {
        if (bpm != t->last[8]) {
            spaceecho_set_bpm(s, bpm);
            t->last[8] = bpm;
        }
        if (t->fresh) {
            spaceecho_snap(s);
            t->fresh = 0;
        }
    }
    in = io->in;
    out = io->out;
    spaceecho_process(s, in[0], in[1], out[0], out[1], io->n);
    t->blocks = t->blocks + 1;
}
