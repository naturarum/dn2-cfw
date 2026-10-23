/* SPDX-License-Identifier: GPL-2.0-or-later */
/* flgdrv.c - one entry point for the chorus-slot glue (tools/dspsplice/src/flg_glue.c or flg_glue2.c,
 * built with -DFLG_NATIVE so the slot is a static), built with selcc for the SHARC interpreter and with
 * clang as a native library (tools/sharcvm/tests/flgcheck.py, T5.6). */
#include <stdint.h>
#include "fxio.h"

void flg_init(void);
void flg_process(const float *p, const FxIo *io);

typedef struct {
    int32_t cmd;        /* 0 init, 1 process */
    const float *p;     /* the four chorus parameter floats (knob/127) */
    const FxIo *io;
} FlgCtx;

void flgdrv(FlgCtx *c)
{
    if (c->cmd == 0)
        flg_init();
    else
        flg_process(c->p, c->io);
}

#ifdef __SHARC_HARNESS__
int main(void) { return 0; }
#endif
