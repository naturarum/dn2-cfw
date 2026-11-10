/* SPDX-License-Identifier: GPL-2.0-or-later */
/* mrphdrv.c - one entry point for MRPH, the morphing reverb (fx/morph.c), built twice: with selache's selcc
 * for the SHARC interpreter and with clang as a native library (tools/sharcvm/tests/mrphcheck.py). As
 * spngdrv.c. Every field is 32 bits on the SHARC. */
#include <stdint.h>
#ifdef MRB
/* MRB, the budget version (fx/mrb.c): the same calls under its own names */
#include "mrb.h"
#define Morph Mrb
#define morph_init mrb_init
#define morph_set mrb_set
#define morph_snap mrb_snap
#define morph_process mrb_process
#else
#include "morph.h"
#endif

typedef struct {
    int32_t cmd;        /* 0 init, 1 set, 2 snap, 3 process */
    void *state;
    const float *inL, *inR;
    float *outL, *outR;
    int32_t n;
    int32_t param;
    float value;
} MrCtx;

void mrphdrv(MrCtx *c)
{
    Morph *s = (Morph *)c->state;
    /* no switch at the end: selcc gives such a function no return (it ran on into main) */
    if (c->cmd == 0) morph_init(s);
    if (c->cmd == 1) morph_set(s, c->param, c->value);
    if (c->cmd == 2) morph_snap(s);
    if (c->cmd == 3) morph_process(s, c->inL, c->inR, c->outL, c->outR, c->n);
}

#ifdef __SHARC_HARNESS__
int main(void) { return 0; }
#endif
