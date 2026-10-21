/* SPDX-License-Identifier: GPL-2.0-or-later */
/* cmp_load.h - T6.3 CMP2F: CMP2E's (heartbeat, breadcrumbs, counters; notes/T6.3-cmp2e.md) with a memory scanner in
 * place of the HIGH and right bars' readout: watermarked free L1 ranges and our own code and constant data, checked a
 * slice per block against the page table build.py writes at CL_PT (notes/T6.3-cmp2f.md). */
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
/* CMP2F: the scanner. The page table (build.py recipe CMP2F, a payload block at CL_PT): word pair 0 = the page count,
   then per page 5 fields (lo, hi, kind, a, b), each field two words in the decode-safe form of build.py's scan_field:
   13 bits per 16-bit half, each half 0xC000 | bits (a 16-bit short-compute parcel to any linear decoder) */
#define CL_PT 0x2e6880u
#define CL_PT_MAX 50u
#define CL_SCAN_WORDS 64u           /* words checked per block: 256 bytes */
#define CL_KIND_MARK 1u             /* every word = the watermark of its address (CL_MARK) */
#define CL_KIND_CODE 2u             /* Fletcher-32 over the page's words = (a, b) from the build */
#define CL_MARK_K1 0x0a5cu          /* watermark of the word at byte address x: low half 0xC000 | ((x >> 2) ^ K1) & 0x1fff, */
#define CL_MARK_K2 0x15a3u          /* high half 0xC000 | ((x >> 2) ^ K2) & 0x1fff */
#define CL_PG_MAX 24u               /* HIGH bar: 2 px per corrupted page, at most 48 px (+1 px if a code page) */

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
    uint32_t pt_n;                  /* CMP2F: the page count read from the table (0: no table, nothing scanned) */
    uint32_t sc_page;               /* the page being scanned (0-based) */
    uint32_t sc_addr;               /* the next word's address */
    uint32_t sc_lo;                 /* the page's range and kind (decoded at the page's start) */
    uint32_t sc_hi;
    uint32_t sc_kind;
    uint32_t sc_a;                  /* the Fletcher sums so far */
    uint32_t sc_b;
    uint32_t bad_lo;                /* bit p: page p + 1 corrupted (latched); pages 1..32 */
    uint32_t bad_hi;                /* pages 33..50 */
    uint32_t n_bad;                 /* corrupted pages */
    uint32_t first_bad;             /* the lowest corrupted page number (1-based), 0: none */
    uint32_t code_hit;              /* 1 once a code page was corrupted */
    uint32_t sweeps;                /* full passes over the table */
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
