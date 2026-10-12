/* SPDX-License-Identifier: GPL-2.0-or-later */
/* flg_glue.c - T3.8 milestone 2: the flanger (fx/flanger.c) in the stock chorus slot.
 *
 * The hook stubs (tools/dspsplice/stub.py) call:
 *   flg_init()                  from the effect init 0x1c1445, where the stock chorus init was
 *                               called (the stub then jumps on to the stock chorus init, so the
 *                               stock chorus state stays initialised although nothing uses it);
 *   flg_process(params, io)     from the FX bus 0x1c1738, in place of the stock chorus 0xb84d5c:
 *                               params = the chorus parameter struct 0x257950 (frame words k25..k28
 *                               as knob/127 floats: +0 DPTH, +4 SPD, +8 HPF, +0xc WDTH), io = the
 *                               bus's I/O descriptor (fxio.h).
 * The flanger writes the wet signal only, like the stock chorus; the bus adds VOL and the DEL/REV
 * sends (k29..k31) itself, so those knobs keep their stock meaning.
 *
 * Knob map (notes/T3.8 section 5): DPTH -> DPTH, SPD -> SPD, HPF -> HPF, WDTH -> WDTH (the flanger's
 * own laws, fx/flanger.h). No stock knob exists for the rest; fixed defaults give the "slow, deep,
 * resonant, swelly" patch of notes/T3.11 12.5: FDBK 107 (+68 %), MAN 50 (0.9 ms), SHP sine, SLEW
 * minimum, DAMP 12 (about 10 kHz), SYNC off, OFS 0, POL normal.
 *
 * State: one FlgSlot at a fixed address in L1 block 2 (0x300000, 33 KB), zero-filled by a FILL block
 * at boot. Written for selcc's known bugs (notes/T3.11 11.3, 12.7): no comparison initialisers,
 * one declaration per variable, no table indexed by a fresh integer, no unions.
 */
#include <stdint.h>
#include "flanger.h"
#include "fxio.h"

#define FLG_MAGIC 0x464c4731u      /* "FLG1" */
#define FLG_SLOT_BYTES 0x8400       /* the FILL block in tools/dspsplice/build.py */

typedef struct {
    uint32_t magic;                 /* FLG_MAGIC once flg_init has run */
    uint32_t fresh;                 /* 1 until the first block has applied the knobs and snapped */
    uint32_t blocks;                /* blocks processed */
    float last[4];                  /* knob values last applied: DPTH, SPD, HPF, WDTH (0..127) */
    Flanger fl;
} FlgSlot;

/* the slot must fit the zero-filled range (a negative array size fails the build) */
typedef char flg_slot_fits[(sizeof(FlgSlot) <= FLG_SLOT_BYTES) ? 1 : -1];

#ifdef FLG_NATIVE
static FlgSlot flg_native_slot;     /* the Mac reference build (tools/dspsplice/verify_flg.py) */
#define SLOT (&flg_native_slot)
uint32_t flg_slot_size(void) { return (uint32_t)sizeof(FlgSlot); }
#else
#define SLOT ((FlgSlot *)0x300000)
#endif

void flg_init(void)
{
    FlgSlot *t = SLOT;
    Flanger *s = &t->fl;
    flanger_init(s);
    flanger_set(s, FL_FDBK, 107.0f);
    flanger_set(s, FL_MAN, 50.0f);
    flanger_set(s, FL_SHP, 0.0f);
    flanger_set(s, FL_SLEW, 0.0f);
    flanger_set(s, FL_DAMP, 12.0f);
    flanger_set(s, FL_SYNC, 0.0f);
    flanger_set(s, FL_OFS, 0.0f);
    flanger_set(s, FL_POL, 0.0f);
    flanger_snap(s);
    t->last[0] = -1.0f;
    t->last[1] = -1.0f;
    t->last[2] = -1.0f;
    t->last[3] = -1.0f;
    t->fresh = 1;
    t->blocks = 0;
    t->magic = FLG_MAGIC;
}

/* a parameter float (knob/127) -> knob 0..127; NaN or out of range -> clamped */
static float flg_knob(float x)
{
    float v;
    v = x * 127.0f;
    if (!(v >= 0.0f))
        v = 0.0f;
    if (v > 127.0f)
        v = 127.0f;
    return v;
}

void flg_process(const float *p, const FxIo *io)
{
    FlgSlot *t = SLOT;
    Flanger *s;
    float v;
    float **in;
    float **out;
    if (t->magic != FLG_MAGIC)
        flg_init();
    s = &t->fl;
    v = flg_knob(p[0]);
    if (v != t->last[0]) {
        flanger_set(s, FL_DPTH, v);
        t->last[0] = v;
    }
    v = flg_knob(p[1]);
    if (v != t->last[1]) {
        flanger_set(s, FL_SPD, v);
        t->last[1] = v;
    }
    v = flg_knob(p[2]);
    if (v != t->last[2]) {
        flanger_set(s, FL_HPF, v);
        t->last[2] = v;
    }
    v = flg_knob(p[3]);
    if (v != t->last[3]) {
        flanger_set(s, FL_WDTH, v);
        t->last[3] = v;
    }
    if (t->fresh) {
        flanger_snap(s);
        t->fresh = 0;
    }
    in = io->in;
    out = io->out;
    flanger_process(s, in[0], in[1], out[0], out[1], io->n);
    t->blocks = t->blocks + 1;
}
