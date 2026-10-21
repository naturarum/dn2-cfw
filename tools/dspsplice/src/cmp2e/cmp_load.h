/* SPDX-License-Identifier: GPL-2.0-or-later */
/* cmp_load.h - T6.3 CMP2E: CMP2D's load meter (notes/T6.3-cmp2d.md) with CMP2E's readout: a heartbeat, the
 * breadcrumbs' heartbeat half (cmp_crumb.h), a trip/re-init counter and the master-latched flag (notes/T6.3-cmp2e.md). */
#ifndef CMP_LOAD_H
#define CMP_LOAD_H

#include <stdint.h>

#define CL_WIN 1500u                /* the peak-hold window: 1,500 blocks = 1 s at 48 kHz / 32 */
#define CL_SETTLE 1500u             /* blocks measured after boot before the all-time maxima start */
#define CL_Q11_MAX 32750u           /* half-word 11 through the stock getter: kept under 0x8000 (a
                                       negative half-word on the ColdFire draws no bar) */

#define CL_HB_BLOCKS 750u           /* the heartbeat's half period: 750 blocks = 0.5 s */
#define CL_HB_LO 6550u              /* its two heights: 10 px and 20 px (q / 655) */
#define CL_HB_HI 13100u
#define CL_EV_Q 2620u               /* 4 px per guard trip or quiet re-init */
#define CL_EV_MAX 11u               /* at most 44 px, so a full bar (the master latched) stays distinct */

typedef struct {
    uint32_t hbq;                   /* CMP2E: FIRST FIELD (cmp_crumb.h reads it at 0x2e2800): the heartbeat's q,
                                       half-word 8 of every breadcrumb store */
    uint32_t hb_n;                  /* blocks into the heartbeat's half period */
    uint32_t hb_ph;                 /* 0: 10 px, 1: 20 px */
    uint32_t prev_trips;            /* mbcomp's trip count as last seen (cmp_init clears it on a re-init) */
    uint32_t prev_reinits;          /* the glue's re-init count as last seen */
    uint32_t events;                /* guard trips plus quiet re-inits since boot */
    uint32_t od_bad;                /* 1 once a master overdrive state word was NaN or inf (latched) */
    uint32_t w14_pad;               /* an even number of words before CMP2D's fields */
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
/* glue exit: this block's compressor cycles; the heartbeat; the counter and the flag; reply half-words 10 and 11
   of both pages and 0x257c1c for the stock getter (half-word 11). Half-words 8 and 9 are the breadcrumbs'. */
void cmp_load_end(uint32_t trips, uint32_t reinits);

/* cmp_clk.s */
uint32_t cmp_clk(void);             /* EMUCLK */
uint32_t cmp_fp(void);              /* its own frame pointer: the word there is its caller's frame pointer */

#endif
