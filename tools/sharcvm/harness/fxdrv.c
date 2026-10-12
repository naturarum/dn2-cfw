/* SPDX-License-Identifier: GPL-2.0-or-later */
/* fxdrv.c - one entry point for the T3.11 effects, built twice: with selache's selcc
 * for the SHARC interpreter and with clang as a native library, so the two builds can
 * be compared on the same input (T3.6, "selcc build behaves like the native build").
 * The context is passed by pointer; every field is 32 bits on the SHARC. */
#include <stdint.h>
#include "tapedelay.h"
#include "mbcomp.h"
#include "flanger.h"
#include "spaceecho.h"

typedef struct {
    int32_t cmd;        /* 0 init, 1 set, 2 snap, 3 process, 4 set bpm */
    int32_t fx;         /* 0 tapedelay, 1 mbcomp, 2 flanger, 3 spaceecho (T5.2) */
    void *state;
    const float *inL, *inR;
    float *outL, *outR;
    int32_t n;
    int32_t param;
    float value;
    float bpm;
    uint32_t seed;
} FxCtx;

void fxdrv(FxCtx *c)
{
    if (c->fx == 0) {
        TapeDelay *s = (TapeDelay *)c->state;
        switch (c->cmd) {
        case 0: tapedelay_init(s, c->seed); break;
        case 1: tapedelay_set(s, c->param, c->value); break;
        case 2: tapedelay_snap(s); break;
        case 3: tapedelay_process(s, c->inL, c->inR, c->outL, c->outR, c->n); break;
        case 4: tapedelay_set_bpm(s, c->bpm); break;
        }
    } else if (c->fx == 2) {
        Flanger *s = (Flanger *)c->state;
        switch (c->cmd) {
        case 0: flanger_init(s); break;
        case 1: flanger_set(s, c->param, c->value); break;
        case 2: flanger_snap(s); break;
        case 3: flanger_process(s, c->inL, c->inR, c->outL, c->outR, c->n); break;
        case 4: flanger_set_bpm(s, c->bpm); break;
        }
    } else if (c->fx == 3) {
        SpaceEcho *s = (SpaceEcho *)c->state;
        switch (c->cmd) {
        case 0: spaceecho_init(s, c->seed); break;
        case 1: spaceecho_set(s, c->param, c->value); break;
        case 2: spaceecho_snap(s); break;
        case 3: spaceecho_process(s, c->inL, c->inR, c->outL, c->outR, c->n); break;
        case 4: spaceecho_set_bpm(s, c->bpm); break;
        }
    } else {
        MBComp *s = (MBComp *)c->state;
        switch (c->cmd) {
        case 0: mbcomp_init(s); break;
        case 1: mbcomp_set(s, c->param, c->value); break;
        case 2: mbcomp_snap(s); break;
        case 3: mbcomp_process(s, c->inL, c->inR, 0, 0, c->outL, c->outR, c->n); break;
        }
    }
}

#ifdef __SHARC_HARNESS__
int main(void) { return 0; }
#endif
