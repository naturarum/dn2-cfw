/* SPDX-License-Identifier: GPL-2.0-or-later */
/* fxio.h - the stock FX bus's I/O descriptor (T3.4 section 2), as the effects receive it in R12.
 * DM 0x257e6c: +0 block length (32), +4 pointer to the input pointer pair, +0xc pointer to the
 * output pointer pair, +0x14 tempo (BPM, float; read by the stock delay). [verified: 0x1c1bc6-0x1c1bdb
 * and 0x1c14a8 in the stock image; tools/sharcvm/fx.py] */
#ifndef FXIO_H
#define FXIO_H
#include <stdint.h>

typedef struct {
    int32_t n;          /* +0x00 samples per block (32) */
    float **in;         /* +0x04 -> {in L, in R} */
    int32_t r08;        /* +0x08 not used here */
    float **out;        /* +0x0c -> {out L, out R} */
    int32_t r10;        /* +0x10 not used here */
    float bpm;          /* +0x14 tempo, BPM */
} FxIo;

#endif
