/* SPDX-License-Identifier: GPL-2.0-or-later */
/* cmp_tail.h - T6.3 CMP2: the DSP side of comp-link-dn2's SPI frame tail (cmp_tail.c). */
#ifndef CMP_TAIL_H
#define CMP_TAIL_H

#include <stdint.h>
#include "mbcomp.h"

#define CT_MAGIC 0xC3B1u            /* tail word 0: magic, low byte = layout 1 */
#define CT_NL 15                    /* the 30 tail words as 15 longs */

typedef struct {
    uint32_t have;                  /* 1 once a valid tail has been accepted; only the boot FILL clears it */
    uint32_t seq;                   /* the held tail's sequence (tail word 1) */
    uint32_t accepted;              /* calls that took a tail (its sequence differed from the held one) */
    uint32_t same;                  /* calls whose newest valid tail had the held sequence: values kept */
    uint32_t none;                  /* calls with no valid tail in either receive buffer: values kept */
    uint32_t calls;
    uint32_t a[CT_NL];              /* receive buffer 0's tail as copied (two words per long, the lower one in
                                       the low half: the SPI DMA stores 16-bit words little-endian) */
    uint32_t b[CT_NL];              /* receive buffer 1's */
    uint32_t w[CT_NL];              /* the held tail: word 2 + j = k 70 + j */
    uint32_t pad;                   /* an even number of words */
} CmpTail;

extern const uint32_t cmp_tail_bytes;

/* read both receive buffers' tails; take a new one if there is one. -> 1 once any valid tail has been
   accepted since boot (the held values are then the band knobs), else 0 (the fallback) */
uint32_t cmp_tail_fetch(void);
/* the held k 70..86 -> the band knobs and the crossovers, each set only when it differs from the
   compressor's own knob word */
void cmp_tail_apply(MBComp *s);

#ifdef CMP_NATIVE
void *cmp_native_tail_ptr(void);
void *cmp_native_rx_tail(int half);
#endif

#endif
