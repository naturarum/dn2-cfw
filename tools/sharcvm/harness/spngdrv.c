/* SPDX-License-Identifier: GPL-2.0-or-later */
/* spngdrv.c - one entry point for SPNG, the spring reverb (T5.5, fx/spring.c), built twice: with
 * selache's selcc for the SHARC interpreter and with clang as a native library, so the two builds can
 * be compared on the same input (tools/sharcvm/tests/spngcheck.py). As fxdrv.c, for this effect alone,
 * so the selcc code measured is the spring's own. Every field is 32 bits on the SHARC. */
#include <stdint.h>
#include "spring.h"

typedef struct {
    int32_t cmd;        /* 0 init, 1 set, 2 snap, 3 process */
    void *state;
    const float *inL, *inR;
    float *outL, *outR;
    int32_t n;
    int32_t param;
    float value;
} SpCtx;

void spngdrv(SpCtx *c)
{
    Spring *s = (Spring *)c->state;
    switch (c->cmd) {
    case 0: spring_init(s); break;
    case 1: spring_set(s, c->param, c->value); break;
    case 2: spring_snap(s); break;
    case 3: spring_process(s, c->inL, c->inR, c->outL, c->outR, c->n); break;
    }
}

#ifdef __SHARC_HARNESS__
int main(void) { return 0; }
#endif
