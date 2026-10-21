/* SPDX-License-Identifier: GPL-2.0-or-later */
/* cmp_load.c - T6.3 CMP2F: CMP2E's with a memory scanner (notes/T6.3-cmp2f.md). Each block cl_scan checks the next
 * CL_SCAN_WORDS words of the page table build.py writes at CL_PT: a watermark page word for word against the watermark
 * of its address (payload blocks the loader writes: free L1 ranges, among them 0x315f8c-0x318000, where CMP2's
 * cmp_tail.c was), a code page (our code and constant data) by its Fletcher-32 against the build's. A page that fails is
 * latched. Shown now:
 *   half-word 10 (HIGH page, left bar)  2 px per corrupted page (at most 24 pages), + 1 px once any of them is a code
 *                                       page: an odd height means code
 *   half-word 11 (right bar)            the lowest corrupted page number (1 px per page; 0: none), direct and through
 *                                       the stock getter (q = page x 655 + 300, mid-pixel, so the getter's +-1 keeps it)
 * Half-words 8 and 9 are the heartbeat and the breadcrumbs, as on CMP2E. The load meter (CMP2D) is still measured
 * (CmpLoad), no longer shown; the master-overdrive flag is gone. The rest of this comment is CMP2E's.
 *
 * cmp_load.c - T6.3 CMP2E: CMP2D's load meter (unchanged: the measurement below) with CMP2E's readout
 * (notes/T6.3-cmp2e.md). Shown now:
 *   half-word 8  (LOW page, left bar)   the heartbeat: 10 px and 20 px, alternating every 750 blocks (0.5 s),
 *                                       counted here; it is the low half of every breadcrumb store (cmp_crumb.h)
 *   half-word 9  (MID page, left bar)   the breadcrumb: the stage id x 3 px (cmp_crumb.h)
 *   half-word 10 (HIGH page, left bar)  guard trips plus quiet re-inits since boot, 4 px each, at most 11 (44 px);
 *                                       a full bar (50 px) once the master overdrive state (0x257d08 16 words,
 *                                       0x257d68 8, 0x257d88 16) has held a NaN or an inf (latched)
 *   half-word 11 (right bar)            the highest load since boot, as on CMP2D (direct and via the stock getter)
 * cmp_load_end writes +0x14 of both pages (half-words 10, 11); +0x10 (8, 9) is written only by the breadcrumbs.
 * The rest of this comment is CMP2D's (half-words 8..10 there: the peak-hold, the highest load, the share).
 *
 * cmp_load.c - T6.3 CMP2D: a DSP load meter for the diagnostic build (notes/T6.3-cmp2d.md).
 *
 * What is measured, per block, with the core's cycle counter EMUCLK (sharcvm: the instruction count):
 *   period  = the handler-entry stamp of this block - that of the previous block. The stamp is the stock
 *             block handler's own: 0x1c9d6b reads EMUCLK at entry (0x1c9d89), keeps EMUCLK - 1 in R14 for the
 *             whole block, and the render 0x1c2712 saves R14 in its frame (DM(I6 - 61), 0x1c274f);
 *   busy    = the stock handler's own count for the previous block: entry to 0x1c9e47, which it stores, halves
 *             swapped, in word 0 of that block's reply page (0x1c9e62); the page is the handler's DM(I6 - 2)
 *             (0x1ca020's tx pointer), read here at glue entry and kept for the next block;
 *   comp    = EMUCLK at glue exit - EMUCLK at glue entry (cmp_process, without the stub's register saves).
 * load = busy / period, share = comp / period, both for the previous block (its period ends at this block's
 * entry). No new hook: the frame walk reads what the stock code keeps anyway. The walk from cmp_fp's frame:
 * cmp_load_begin, cmp_process, the stub fxh_comp, the master 0x1c1f1e, the render 0x1c2712, the handler
 * 0x1c9d6b (each frame's first word is its caller's frame pointer). It is checked (frames rising, each step
 * under 4 KB, the reply page one of the two, the stamp under 2^21 cycles old); a block that fails reads
 * nothing through the walk and is counted in `bad`.
 *
 * Kept (CmpLoad, FILL-zeroed at boot): a peak-hold of the load over the last 1-2 s (the larger of the current
 * and the previous 1,500-block window), the highest load and the highest compressor share since boot (after
 * the first CL_SETTLE blocks measured).
 *
 * Shown (cmp_load_end), Q15 of the fraction of a block period, q = x * 32767 (h = q / 655 px on the band
 * pages: 1 px = 2 % of the period, a full 50 px bar = 100 %):
 *   half-word 8  (LOW page)   the load peak-hold
 *   half-word 9  (MID page)   the highest load since boot
 *   half-word 10 (HIGH page)  the highest compressor share since boot
 *   half-word 11              the highest load since boot again, capped at CL_Q11_MAX, written directly and
 *                             through the stock getter: 0x257c1c gets the dB value y with
 *                             32768 * (1 - 10^(y/20)) = q, which 0xb8552b (called at 0x1c3108) turns back into
 *                             q and stores at 0x1c3117 (the path the stock Compressor page's bar reads)
 * as two 32-bit stores at +0x10 and +0x14 of both reply pages (CMP2's meter stores). A frame walk that never
 * passed shows 1 px on every bar (q = 655).
 *
 * After the DSP stops, the reply pages are no longer written and the SPI DMA keeps sending them: the bars
 * hold what they showed last. Audio: nothing here reads or writes the compressor's state or the audio
 * buffers (0x257c1c is the stock compressor's y2, which only the getter reads).
 *
 * selcc (as the glue): one declaration per variable, flags by if statements, constant offsets only.
 */
#include <stdint.h>
#include "fxmath.h"
#include "cmp_load.h"

#ifdef CMP_NATIVE
static CmpLoad cmp_native_load;
#define LOAD (&cmp_native_load)
uint32_t cmp_clk(void) { return 0; }
uint32_t cmp_fp(void) { return 0; }
#define WALK 0
#else
#define LOAD ((CmpLoad *)0x2e2800)      /* L1 block 1, after CmpTail's slot (build.py recipe CMP2D: FILL) */
#define WALK 1
#endif
#define GR_Y2 ((volatile float *)0x257c1c)        /* stock compressor state +0x16c: y2, read by the getter */
#define TX0 0x2c49d0u                             /* the reply pages (T3.2 3.4) */
#define TX1 0x2c59d0u
#define FRAME_STEP 0x1000u                        /* a frame of the walk lies within 4 KB above the one before */
#define R14_SLOT 0xf4u                            /* the render's DM(I6 - 61): R14 = handler-entry EMUCLK - 1 */
#define TX_SLOT 8u                                /* the handler's DM(I6 - 2): the reply page */
#define STAMP_AGE 0x200000u                       /* glue entry is under 2^21 cycles after handler entry */

const uint32_t cmp_load_bytes = (uint32_t)sizeof(CmpLoad);

/* one frame up: the word at fp is the caller's frame pointer; 0 when the step fails the check */
static uint32_t cl_up(uint32_t fp)
{
    uint32_t up;
    up = *(volatile uint32_t *)fp;
    if (up <= fp)
        return 0;
    if (up - fp > FRAME_STEP)
        return 0;
    return up;
}

/* a fraction of the period -> Q15, 0..32767 */
static uint32_t cl_q15(float x)
{
    float y;
    int32_t q;
    y = x * 32767.0f + 0.5f;
    if (!(y >= 0.0f))
        y = 0.0f;
    if (y > 32767.0f)
        y = 32767.0f;
    q = (int32_t)y;
    return (uint32_t)q;
}

void cmp_load_begin(void)
{
    CmpLoad *t = LOAD;
    uint32_t t0;
    uint32_t f;
    uint32_t entry;
    uint32_t tx;
    uint32_t w0;
    uint32_t busy;
    uint32_t period;
    uint32_t comp;
    uint32_t ok;
    float load;
    float share;
    t0 = cmp_clk();
    t->t0 = t0;
    t->calls = t->calls + 1;
    ok = 0;
    entry = 0;
    tx = 0;
    if (WALK) {
        f = cmp_fp();               /* cmp_fp's own frame */
        f = cl_up(f);               /* cmp_load_begin */
        if (f)
            f = cl_up(f);           /* cmp_process */
        if (f)
            f = cl_up(f);           /* the stub fxh_comp */
        if (f)
            f = cl_up(f);           /* the master 0x1c1f1e */
        if (f)
            f = cl_up(f);           /* the render 0x1c2712 */
        if (f) {
            entry = *(volatile uint32_t *)(f - R14_SLOT);
            f = cl_up(f);           /* the block handler 0x1c9d6b */
        }
        if (f) {
            tx = *(volatile uint32_t *)(f - TX_SLOT);
            ok = 1;
            if (tx != TX0) {
                if (tx != TX1)
                    ok = 0;
            }
            if (t0 - entry > STAMP_AGE)
                ok = 0;
        }
    }
    t->ok = ok;
    if (!ok) {
        t->bad = t->bad + 1;
        t->have = 0;
        return;
    }
    if (t->have) {
        /* the previous block: its busy count (stock, reply word 0 of its page, halves swapped), its period
           (to this block's entry) and its compressor cycles */
        w0 = *(volatile uint32_t *)t->prev_tx;
        busy = (w0 >> 16) | (w0 << 16);
        period = entry - t->prev_entry;
        comp = t->prev_comp;
        if (period != 0) {
            load = (float)(int32_t)(busy & 0x7fffffffu) / (float)(int32_t)(period & 0x7fffffffu);
            share = (float)(int32_t)(comp & 0x7fffffffu) / (float)(int32_t)(period & 0x7fffffffu);
            if (load > 1.0f)
                load = 1.0f;
            if (share > 1.0f)
                share = 1.0f;
            t->last_load = load;
            t->last_share = share;
            t->last_busy = busy;
            t->last_period = period;
            t->last_comp = comp;
            t->blocks = t->blocks + 1;
            if (load > t->win_cur)
                t->win_cur = load;
            t->win_n = t->win_n + 1;
            if (t->win_n >= CL_WIN) {
                t->win_prev = t->win_cur;
                t->win_cur = 0.0f;
                t->win_n = 0;
            }
            if (t->blocks > CL_SETTLE) {
                if (load > t->max_load)
                    t->max_load = load;
                if (share > t->max_share)
                    t->max_share = share;
                if (busy > t->max_busy)
                    t->max_busy = busy;
            }
        }
    }
    t->prev_entry = entry;
    t->prev_tx = tx;
    t->prev_comp = 0;
    t->have = 1;
}

/* one field of the page table (two words, 13 + 13 + 6 bits in the halves' low bits) */
static uint32_t cl_field(uint32_t a)
{
    uint32_t w0;
    uint32_t w1;
    uint32_t v;
    w0 = *(volatile uint32_t *)a;
    w1 = *(volatile uint32_t *)(a + 4u);
    v = w0 & 0x1fffu;
    v = v | (((w0 >> 16) & 0x1fffu) << 13);
    v = v | ((w1 & 0x3fu) << 26);
    return v;
}

/* page p (0-based) into the scanner: its range and kind, the sums cleared */
static void cl_page(CmpLoad *t, uint32_t p)
{
    uint32_t e;
    e = CL_PT + 8u + 40u * p;
    t->sc_page = p;
    t->sc_lo = cl_field(e);
    t->sc_hi = cl_field(e + 8u);
    t->sc_kind = cl_field(e + 16u);
    t->sc_addr = t->sc_lo;
    t->sc_a = 0;
    t->sc_b = 0;
}

/* page p (0-based) failed: latch it */
static void cl_bad(CmpLoad *t, uint32_t p)
{
    uint32_t m;
    uint32_t isnew;
    isnew = 0;
    if (p < 32u) {
        m = 1u << p;
        if ((t->bad_lo & m) == 0) {
            t->bad_lo = t->bad_lo | m;
            isnew = 1;
        }
    } else {
        m = 1u << (p - 32u);
        if ((t->bad_hi & m) == 0) {
            t->bad_hi = t->bad_hi | m;
            isnew = 1;
        }
    }
    if (isnew) {
        t->n_bad = t->n_bad + 1;
        if (t->sc_kind == CL_KIND_CODE)
            t->code_hit = 1;
        if (t->first_bad == 0)
            t->first_bad = p + 1;
        if (p + 1 < t->first_bad)
            t->first_bad = p + 1;
    }
}

/* CL_SCAN_WORDS words of the table's pages, from where the last block stopped */
static void cl_scan(CmpLoad *t)
{
    uint32_t k;
    uint32_t a;
    uint32_t w;
    uint32_t x;
    uint32_t want;
    uint32_t e;
    if (t->pt_n == 0) {
        x = cl_field(CL_PT);
        if (x == 0)
            return;
        if (x > CL_PT_MAX)
            return;
        t->pt_n = x;
        cl_page(t, 0);
    }
    for (k = 0; k < CL_SCAN_WORDS; k++) {
        a = t->sc_addr;
        if (a >= t->sc_hi) {
            /* the page is done: a code page's sums against the build's, then the next page */
            if (t->sc_kind == CL_KIND_CODE) {
                e = CL_PT + 8u + 40u * t->sc_page;
                if (t->sc_a != cl_field(e + 24u))
                    cl_bad(t, t->sc_page);
                else if (t->sc_b != cl_field(e + 32u))
                    cl_bad(t, t->sc_page);
            }
            x = t->sc_page + 1u;
            if (x >= t->pt_n) {
                x = 0;
                t->sweeps = t->sweeps + 1;
            }
            cl_page(t, x);
            a = t->sc_addr;
        }
        w = *(volatile uint32_t *)a;
        if (t->sc_kind == CL_KIND_MARK) {
            x = a >> 2;
            want = 0xc000c000u | ((x ^ CL_MARK_K1) & 0x1fffu) | (((x ^ CL_MARK_K2) & 0x1fffu) << 16);
            if (w != want)
                cl_bad(t, t->sc_page);
        } else {
            t->sc_a = t->sc_a + w;
            t->sc_b = t->sc_b + t->sc_a;
        }
        t->sc_addr = a + 4u;
    }
}

void cmp_load_end(uint32_t trips, uint32_t reinits)
{
    CmpLoad *t = LOAD;
    uint32_t t1;
    uint32_t q10;
    uint32_t q11;
    uint32_t w14;
    uint32_t ev;
    float x;
    float l;
    t1 = cmp_clk();
    if (t->ok)
        t->prev_comp = t1 - t->t0;
    /* the heartbeat, counted in blocks */
    t->hb_n = t->hb_n + 1;
    if (t->hb_n >= CL_HB_BLOCKS) {
        t->hb_n = 0;
        t->hb_ph = t->hb_ph ^ 1u;
    }
    t->hbq = CL_HB_LO;
    if (t->hb_ph)
        t->hbq = CL_HB_HI;
    /* guard trips (mbcomp's count, cleared by a re-init) and quiet re-inits since boot */
    if (trips != t->prev_trips) {
        if (trips > t->prev_trips)
            t->events = t->events + (trips - t->prev_trips);
        else
            t->events = t->events + trips;
        t->prev_trips = trips;
    }
    if (reinits != t->prev_reinits) {
        t->events = t->events + (reinits - t->prev_reinits);
        t->prev_reinits = reinits;
    }
    /* CMP2F: the scanner, then its readout: HIGH = 2 px per corrupted page (+1 px: a code page), right = the lowest
       corrupted page number */
    if (WALK)
        cl_scan(t);
    ev = t->n_bad;
    if (ev > CL_PG_MAX)
        ev = CL_PG_MAX;
    q10 = (2u * ev + t->code_hit) * 655u + 300u;
    if (t->n_bad == 0)
        q10 = 0;
    q11 = t->first_bad * 655u + 300u;
    if (t->first_bad == 0)
        q11 = 0;
    if (q11 > 32760u)
        q11 = 32760u;
    w14 = q10 + q11 * 65536u;
    t->w10 = 0;
    t->w14 = w14;
    if (WALK) {
        *(volatile uint32_t *)(TX0 + 0x14u) = w14;
        *(volatile uint32_t *)(TX1 + 0x14u) = w14;
    }
    /* y = 20 log10(1 - q11 / 32768) dB: the stock getter returns 32768 * (1 - 10^(y/20)) = q11 (+-1) */
    x = 1.0f - (float)(int32_t)q11 * 3.0517578e-05f;
    FX_LOG2(x, l);
    l = l * FX_DB_PER_LOG2;
    t->gr_db = l;
    if (WALK)
        *GR_Y2 = l;
}
