/* SPDX-License-Identifier: GPL-2.0-or-later */
/* cmp_load.h - T6.3 CMP2D: the DSP load meter of the diagnostic build (cmp_load.c; notes/T6.3-cmp2d.md). */
#ifndef CMP_LOAD_H
#define CMP_LOAD_H

#include <stdint.h>

#define CL_WIN 1500u                /* the peak-hold window: 1,500 blocks = 1 s at 48 kHz / 32 */
#define CL_SETTLE 1500u             /* blocks measured after boot before the all-time maxima start */
#define CL_Q11_MAX 32750u           /* half-word 11 through the stock getter: kept under 0x8000 (a
                                       negative half-word on the ColdFire draws no bar) */

typedef struct {
    uint32_t have;                  /* 1 when prev_entry and prev_tx hold the previous block's stamps */
    uint32_t prev_entry;            /* the previous block's handler-entry stamp (the stock R14: EMUCLK - 1) */
    uint32_t prev_tx;               /* the previous block's reply page (the handler's tx pointer) */
    uint32_t prev_comp;             /* the previous block's compressor cycles (glue entry to glue exit) */
    uint32_t t0;                    /* this block's EMUCLK at glue entry */
    uint32_t ok;                    /* 1 when this block's frame walk passed its checks */
    uint32_t calls;                 /* glue calls */
    uint32_t blocks;                /* blocks measured (a load computed) */
    uint32_t bad;                   /* calls whose frame walk failed its checks (nothing read, nothing measured) */
    uint32_t win_n;                 /* blocks in the current peak-hold window */
    float win_cur;                  /* the highest load in the current window */
    float win_prev;                 /* the highest load in the previous window */
    float max_load;                 /* the highest load since boot (after CL_SETTLE blocks) */
    float max_share;                /* the highest compressor share since boot (after CL_SETTLE blocks) */
    float last_load;                /* the last block measured: busy / period */
    float last_share;               /* its compressor cycles / period */
    uint32_t last_busy;             /* its busy cycles (the stock count in reply word 0) */
    uint32_t last_period;           /* its period (handler entry to the next handler entry) */
    uint32_t last_comp;             /* its compressor cycles */
    uint32_t max_busy;              /* the highest busy count since boot (after CL_SETTLE blocks) */
    uint32_t w10;                   /* the reply words written at +0x10 and +0x14 of both pages */
    uint32_t w14;
    float gr_db;                    /* the value written to the stock meter word 0x257c1c */
    uint32_t pad;                   /* an even number of words */
} CmpLoad;

extern const uint32_t cmp_load_bytes;

/* glue entry: EMUCLK, the frame walk (this block's handler-entry stamp and reply page), and the previous
   block's load and compressor share into the peak-hold and the maxima */
void cmp_load_begin(void);
/* glue exit: this block's compressor cycles; the readout into reply half-words 8..11 of both pages and
   into 0x257c1c for the stock getter (half-word 11) */
void cmp_load_end(void);

/* cmp_clk.s */
uint32_t cmp_clk(void);             /* EMUCLK */
uint32_t cmp_fp(void);              /* its own frame pointer: the word there is its caller's frame pointer */

#endif
