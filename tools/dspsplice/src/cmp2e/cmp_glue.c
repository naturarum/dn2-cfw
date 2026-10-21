/* SPDX-License-Identifier: GPL-2.0-or-later */
/* cmp_glue.c - T6.3 CMP2E: CMP2D's glue (src/cmp2d/) with breadcrumbs (cmp_crumb.h: CRUMB(1)..(4), (15), (16) here;
 * (5)..(14) in mbcomp.c) and cmp_load_end given mbcomp's trip count and the re-init count (notes/T6.3-cmp2e.md).
 * Nothing that feeds the audio changed. The rest of this comment is CMP2D's.
 *
 * cmp_glue.c - T6.3 CMP2D: CMP2's glue (frozen in src/cmp2/cmp_glue.c, the image on the owner's unit since
 * 2026-10-18) with the band meters replaced by a DSP load meter (cmp_load.c; notes/T6.3-cmp2d.md): CMP2 stops
 * the DSP on the unit when a band's MUP goes past about noon, and the band meters never moved. Two changes, and
 * nothing that feeds the audio: cmp_process calls cmp_load_begin() first (EMUCLK, the frame walk, the previous
 * block's load) and cmp_load_end() last (this block's compressor cycles; reply half-words 8..11 and the stock
 * meter word 0x257c1c now carry the load readout); the band meters' Q15 (cmp_q15) and their reply stores are
 * gone. The bands' gain reductions are still kept in the slot (gr0..gr2, grd: the restart seed reads them).
 * The audio is CMP2's sample for sample (notes/T6.3-cmp2d.md). The rest of this comment is CMP2's.
 *
 * CMP2: CMP1's glue (frozen in src/cmp1/cmp_glue.c) with the band knobs live: once a valid SPI frame
 * tail of comp-link-dn2 has arrived (cmp_tail.c), each band's THR, RAT, ATK, REL, MUP and the two crossovers come
 * from k 70..86 (notes/T6.4-compressor-map-and-plan.md 3.4, 5 "CMP2"; notes/T6.3-cmp2.md). Until then (no tail has
 * ever been valid: a main OS without comp-link, or the first block before the first tail) the bands run exactly as
 * on CMP1, all three from the stock k 60..64 with the crossovers at 196 Hz / 2.61 kHz: the fallback, bit for bit
 * CMP1's output (tools/dspsplice/verify_cmp.py CMP2, (h)). MIX (k 67), SCS (k 65) and VOL stay the stock rows. The
 * slot's magic is "CMP2"; its layout is CMP1's. The rest of this comment is CMP1's.
 *
 * CMP1 (the DSP of CMP0 with the review's fixes; CMP1b: and the CMP1 review's): the 3-band
 * compressor (fx/mbcomp.c) in place of the stock master compressor 0xb8541d, on the stock knobs
 * (notes/T6.4-compressor-map-and-plan.md 4.1, 4.3, 5; the proof is tools/dspsplice/verify_cmp.py CMP1). CMP0's
 * glue as built: src/cmp0/cmp_glue.c.
 *
 * The hook stubs (tools/dspsplice/stub.py, recipe CMP0 in build.py) call:
 *   cmp_init()               from the effect init 0x1c1445 at 0x1c16bb, where the stock compressor init
 *                            0xb85501 was called; the stub then jumps on to 0xb85501, so the stock state,
 *                            its defaults and the shared init config 0x257e48 are set up as before;
 *   cmp_process(p, io)       from the master 0x1c1f1e at 0x1c215c, in place of 0xb8541d: p = the stock
 *                            compressor's parameter struct 0x257964, io = the I/O descriptor 0x257e6c
 *                            (fxio.h CmpIo: +4 -> {comp L, comp R, sc L, sc R}, +0xc -> {out L, out R},
 *                            read every call: the master rebuilds the arrays on its stack every block).
 * The output is the fully mixed compressor output (wet and dry); the master then adds the bypass bus.
 *
 * The parameter struct (the render's unpack of k 60..67 from the previous block's frame image, T6.4 1.3):
 *   +0x00 SCF p   (ignored: the owner dropped SCF; the detectors run at the neutral 64)
 *   +0x04 THR p   +0x08 RAT idx/7   +0x0c ATK p   +0x10 REL p   +0x14 MUP p   +0x18 MIX p
 *   (p = word / 32512 = knob / 127; +0x1c SCS and +0x20/+0x24 routing are read by the master only)
 * All three bands take THR, ATK, REL, MUP and RAT; the knob value is formed as the stock code forms its
 * table index (fmax/fmin then trunc of p x 127.0, of p x 7.0 for RAT), so ATK, REL and MIX land on the stock
 * table entry the stock compressor would use, and RAT stock index i becomes band step i + 1 (1.00, step 0,
 * is not reachable in CMP0). Crossovers fixed at XLO 68 (196 Hz) and XHI 44 (2.61 kHz).
 * SCS: the master already put the chosen bus in sc L/R; sc == comp in -> each band detects itself, else every
 * band detects the key.
 *
 * Meters (T6.4 4.3 #9): the deepest band's y2 (dB, <= 0) into 0x257c1c, where the stock GR getter 0xb8552b
 * (called at 0x1c3108) reads it for reply half-word 11 (the stock Compressor page's bar, Overbridge). Q15
 * (1 - g) of each band into reply half-words 8, 9, 10 (bytes +0x10/+0x12/+0x14; the ColdFire sees half-word
 * k at byte 2k), written as two 32-bit stores at +0x10 and +0x14 of both reply pages 0x2c49d0/0x2c59d0; the
 * high half of the second is half-word 11, set to the deepest band's value as the getter computes it, which
 * the stock 16-bit store at 0x1c3117 then rewrites in the page of this block.
 *
 * State: one CmpSlot at 0x2e1c40 (L1 block 1, after the flanger's code), zero-filled by a FILL block at
 * boot; the effect init (cmp_init, from the init hook) sets the magic before the first block (sharcvm: the slot
 * holds it right after the effect init; on the unit the init hook at 0x1c16bb runs before the block handler
 * [inferred]). cmp_process re-initialises quietly on any other magic, a zero one included (CMP1b), so a zero
 * state is still valid. build.py checks the slot's size from cmp_slot_bytes in the linked image against the
 * FILL and the other blocks.
 *
 * CMP1, the glue's part of the complete restart (fx/mbcomp.c #12-#14): every call compares each stock knob with
 * the value the compressor holds (all three bands; SCS; the fixed crossovers) and sets it again when they differ,
 * so a corrupted knob word lasts one call (CMP0 compared with its own copy, last_*, and never saw it); the table
 * pointer is set every call. After a restart (mbcomp's trip count moved in the call) each band's envelopes take the
 * gain reduction of the block before it, kept in this slot for the meters, when that is deeper than what the
 * restart left: the return goes on compressing as before instead of rebuilding the gain reduction from none
 * (out/T6.3/CMP1-dsp/trip_loudness.json).
 * CMP1b (the CMP1 review, out/T6.3/review-dsp-cmp1/p1_reinit_burst.json): a magic other than CMP1's, 0 included, is
 * a corrupted slot. CMP1 took a zero magic for a cold start and re-initialised with the normal attack and no seed
 * (up to +18 dB over the run without it, the burst the quiet return removes), and its re-init of any other magic
 * set the instant attack but no seed (+3.2 dB). Now every re-init is the quiet return: cmp_init, instant attack for
 * MB_REC_BLOCKS on every band, both envelopes of each band seeded from the last block's gain reduction (cmp_seed;
 * cmp_init leaves gr0..gr2 as they are), the MIX gains from 0 (fresh), and the count in reinits (cmp_init does not
 * clear it; it clears both trip counters). A real cold start loses nothing: the MIX gains start from 0 either way,
 * and the seed of a zeroed slot is 0. In normal operation the compressor holds exactly the
 * values the glue set, so at every block it runs on CMP0's knob values (the first block may skip a set that CMP0
 * made with the value init had already set) and its output is CMP0's (verify_cmp.py CMP1, (h)).
 *
 * selcc (notes/T3.11 11.3, 12.7; T5.2 6.2; T5.3 3.4): flags set with if statements, one declaration per
 * variable, no table indexed by a variable, no array store indexed by a variable after a call (the sixth
 * miscompile: t->last[k] = v after a call, k a parameter); every last-applied value is its own field, and
 * every store address in the compiled glue and in mbcomp.c was traced in selcc's output
 * (tools/dspsplice/store_trace.py: none formed from a caller-saved register across a call) and the stores of
 * the whole call are checked at run time (verify_cmp.py (g)).
 */
#include <stdint.h>
#include "mbcomp.h"
#include "fxio.h"
#include "cmp_tail.h"
#include "cmp_load.h"
#include "cmp_crumb.h"

#define CMP_MAGIC 0x434d5032u       /* "CMP2" */
#define CMP_XLO 68.0f               /* 196 Hz */
#define CMP_XHI 44.0f               /* 2.61 kHz */

typedef struct {
    uint32_t magic;                 /* CMP_MAGIC once cmp_init has run */
    uint32_t fresh;                 /* 1 until the first block has applied the knobs and snapped */
    uint32_t blocks;                /* blocks processed */
    uint32_t sc_ext;                /* 1: the bands detected the key (sc != comp in) in the last block */
    uint32_t trips;                 /* mbcomp's guard trips and restarts, as of the last block */
    float last_thr;                 /* the knob values last applied (knob scale; RAT as the band step; a record:
                                       CMP1 compares with the compressor's own knob words) */
    float last_atk;
    float last_rel;
    float last_mup;
    float last_rat;
    float last_mix;
    float gr0;                      /* the last block's y2 per band and the deepest, dB */
    float gr1;
    float gr2;
    float grd;
    uint32_t word10;                /* CMP2: the reply words written at +0x10 and +0x14; CMP2D: not written (the
                                       readout is CmpLoad's, cmp_load.c), the layout kept */
    uint32_t word14;
    uint32_t reinits;               /* CMP1b: quiet re-inits of a corrupted slot (bad magic); cmp_init keeps it.
                                       CMP1's pad word: an even number of words before mb, the same offsets on
                                       the Mac */
    MBComp mb;
} CmpSlot;

/* the slot's size in the SHARC build, read from the linked image by tools/dspsplice/build.py (selcc accepts
   a negative array size, so a compile-time guard would be a no-op there) */
const uint32_t cmp_slot_bytes = (uint32_t)sizeof(CmpSlot);

#ifdef CMP_NATIVE
/* the Mac reference build (tools/dspsplice/verify_cmp.py): the image's tables passed in, the meter word and
   the reply pages as plain arrays */
static CmpSlot cmp_native_slot;
static const float *cmp_native_tab;
float cmp_native_gr_y2;
uint32_t cmp_native_tx[2][8];
void cmp_native_set_tables(const float *tab) { cmp_native_tab = tab; }
void *cmp_native_slot_ptr(void) { return &cmp_native_slot; }
uint32_t cmp_native_mb_offset(void) { return (uint32_t)((char *)&cmp_native_slot.mb - (char *)&cmp_native_slot); }
#define SLOT (&cmp_native_slot)
#define TABLES (cmp_native_tab)
#define GR_Y2 (&cmp_native_gr_y2)
#define TX0 (&cmp_native_tx[0][0])
#define TX1 (&cmp_native_tx[1][0])
#else
#define SLOT ((CmpSlot *)0x2e1c40)
#define TABLES ((const float *)0x2cfde0)        /* the stock table block, read in place (MB_TAB_*) */
/* CMP2D: GR_Y2, TX0 and TX1 are no longer used here (cmp_load.c writes them) */
#define GR_Y2 ((float *)0x257c1c)               /* stock compressor state +0x16c: y2 */
#define TX0 ((uint32_t *)0x2c49d0)              /* the reply pages */
#define TX1 ((uint32_t *)0x2c59d0)
#endif

void cmp_init(void)
{
    CmpSlot *t = SLOT;
    MBComp *s = &t->mb;
    mbcomp_init(s, TABLES);
    mbcomp_set(s, MB_XLO, CMP_XLO);
    mbcomp_set(s, MB_XHI, CMP_XHI);
    t->last_thr = -1.0f;
    t->last_atk = -1.0f;
    t->last_rel = -1.0f;
    t->last_mup = -1.0f;
    t->last_rat = -1.0f;
    t->last_mix = -1.0f;
    t->sc_ext = 0;
    t->trips = 0;
    t->blocks = 0;
    t->fresh = 1;
    t->magic = CMP_MAGIC;
}

/* a parameter float (knob/127) -> the knob as the stock code forms it: fmax(p x 127, 0), fmin(.., 127) */
static float cmp_knob(float p)
{
    float v;
    v = p * 127.0f;
    if (!(v >= 0.0f))
        v = 0.0f;
    if (v > 127.0f)
        v = 127.0f;
    return v;
}

/* after a restart or a re-init: a band's envelope value y (what the restart left, log2 units) and the gain reduction
   it had in the block before, gdb (dB, this slot's meter copy) -> the deeper of the two, gdb kept inside
   CMP_SEED_MIN..0 log2 units (a NaN or inf meter copy gives no seed; the exponent test answers the same on the SHARC
   as on the Mac). CMP1b: CMP_SEED_MIN -16 (-96 dB; CMP1 -60): real material reaches it only above +41 dBFS (threshold
   -60 dB, 20:1), and it must stay above mbcomp's range test (-32), or a seed would restart the state again. */
#define CMP_SEED_MIN -16.0f
static float cmp_seed(float gdb, float y)
{
    float g;
    uint32_t u;
    g = gdb * 0.16609640f;
    u = *(uint32_t *)&g;
    u = u & 0x7f800000u;
    if (u == 0x7f800000u)
        g = 0.0f;
    if (g < CMP_SEED_MIN)
        g = CMP_SEED_MIN;
    if (g > 0.0f)
        g = 0.0f;
    if (g < y)
        y = g;
    return y;
}

void cmp_process(const float *p, const CmpIo *io)
{
    CmpSlot *t = SLOT;
    MBComp *s;
    float v;
    float y0;
    float y1;
    float y2;
    float yd;
    float **in;
    float **out;
    const float *kl;
    const float *kr;
    uint32_t ext;
    int32_t ri;
    int32_t d;
    uint32_t have;
    /* CMP2E: breadcrumb 1, glue entry; CMP2D: the load meter (cmp_load.c), first thing in the call */
    CRUMB(1);
    cmp_load_begin();
    t = SLOT;
    CRUMB(2);
    if (t->magic != CMP_MAGIC) {
        /* CMP1b: a corrupted slot (any magic but CMP1's: the effect init set it before the first block): the
           quiet re-init. Every band attacks instantly for MB_REC_BLOCKS (all three marked dirty, so the snap below
           or the first control block derives them with ca = 0 while rec_left > 0; cmp_init derived them before
           rec_left was set), each band's envelopes start from the last block's gain reduction, the MIX gains from
           0 (cmp_init sets fresh) */
        cmp_init();
        t = SLOT;
        s = &t->mb;
        s->rec_left = MB_REC_BLOCKS;
        s->dirty = s->dirty | 7;
        v = cmp_seed(t->gr0, 0.0f);
        s->band[0].y1 = v;
        s->band[0].y2 = v;
        v = cmp_seed(t->gr1, 0.0f);
        s->band[1].y1 = v;
        s->band[1].y2 = v;
        v = cmp_seed(t->gr2, 0.0f);
        s->band[2].y1 = v;
        s->band[2].y2 = v;
        t->reinits = t->reinits + 1;
    }
    s = &t->mb;
    s->tab = TABLES;

    /* CMP2: the band knobs from the frame tail, once one has been valid (cmp_tail.c) */
    CRUMB(3);
    have = cmp_tail_fetch();
    CRUMB(4);
    if (have != 0) {
        cmp_tail_apply(&SLOT->mb);
    } else {
        t = SLOT;
        s = &SLOT->mb;
        /* the fallback, CMP1's code: the stock knobs, all three bands; each set when it differs from what the
           compressor holds, with constant indices */
        v = cmp_knob(p[1]);
        d = 0;
        if (v != s->knob[MB_P(0, MB_THR)])
            d = 1;
        if (v != s->knob[MB_P(1, MB_THR)])
            d = 1;
        if (v != s->knob[MB_P(2, MB_THR)])
            d = 1;
        if (d) {
            mbcomp_set(s, MB_P(0, MB_THR), v);
            mbcomp_set(s, MB_P(1, MB_THR), v);
            mbcomp_set(s, MB_P(2, MB_THR), v);
            t->last_thr = v;
        }
        v = cmp_knob(p[3]);
        d = 0;
        if (v != s->knob[MB_P(0, MB_ATK)])
            d = 1;
        if (v != s->knob[MB_P(1, MB_ATK)])
            d = 1;
        if (v != s->knob[MB_P(2, MB_ATK)])
            d = 1;
        if (d) {
            mbcomp_set(s, MB_P(0, MB_ATK), v);
            mbcomp_set(s, MB_P(1, MB_ATK), v);
            mbcomp_set(s, MB_P(2, MB_ATK), v);
            t->last_atk = v;
        }
        v = cmp_knob(p[4]);
        d = 0;
        if (v != s->knob[MB_P(0, MB_REL)])
            d = 1;
        if (v != s->knob[MB_P(1, MB_REL)])
            d = 1;
        if (v != s->knob[MB_P(2, MB_REL)])
            d = 1;
        if (d) {
            mbcomp_set(s, MB_P(0, MB_REL), v);
            mbcomp_set(s, MB_P(1, MB_REL), v);
            mbcomp_set(s, MB_P(2, MB_REL), v);
            t->last_rel = v;
        }
        v = cmp_knob(p[5]);
        d = 0;
        if (v != s->knob[MB_P(0, MB_MUP)])
            d = 1;
        if (v != s->knob[MB_P(1, MB_MUP)])
            d = 1;
        if (v != s->knob[MB_P(2, MB_MUP)])
            d = 1;
        if (d) {
            mbcomp_set(s, MB_P(0, MB_MUP), v);
            mbcomp_set(s, MB_P(1, MB_MUP), v);
            mbcomp_set(s, MB_P(2, MB_MUP), v);
            t->last_mup = v;
        }
        /* RAT: stock index trunc(fmin(fmax(p x 7, 0), 7)) -> band step index + 1 */
        v = p[2] * 7.0f;
        if (!(v >= 0.0f))
            v = 0.0f;
        if (v > 7.0f)
            v = 7.0f;
        ri = (int32_t)v;
        v = (float)(ri + 1);
        d = 0;
        if (v != s->knob[MB_P(0, MB_RAT)])
            d = 1;
        if (v != s->knob[MB_P(1, MB_RAT)])
            d = 1;
        if (v != s->knob[MB_P(2, MB_RAT)])
            d = 1;
        if (d) {
            mbcomp_set(s, MB_P(0, MB_RAT), v);
            mbcomp_set(s, MB_P(1, MB_RAT), v);
            mbcomp_set(s, MB_P(2, MB_RAT), v);
            t->last_rat = v;
        }
        /* the fixed crossovers (set by cmp_init; again only if their words were corrupted) */
        if (s->knob[MB_XLO] != CMP_XLO)
            mbcomp_set(s, MB_XLO, CMP_XLO);
        if (s->knob[MB_XHI] != CMP_XHI)
            mbcomp_set(s, MB_XHI, CMP_XHI);
    }
    t = SLOT;
    s = &SLOT->mb;
    /* MIX: the stock row (k 67) in both cases */
    v = cmp_knob(p[6]);
    if (v != s->knob[MB_MIX]) {
        mbcomp_set(s, MB_MIX, v);
        t->last_mix = v;
    }

    /* SCS: the key is another bus than the compressor's own input */
    in = io->in;
    out = io->out;
    ext = 0;
    if (in[2] != in[0])
        ext = 1;
    if (in[3] != in[1])
        ext = 1;
    kl = 0;
    kr = 0;
    if (ext) {
        kl = in[2];
        kr = in[3];
    }
    v = 0.0f;
    if (ext)
        v = 1.0f;
    if (v != s->knob[MB_SCS])
        mbcomp_set(s, MB_SCS, v);
    t->sc_ext = ext;
    if (t->fresh) {
        mbcomp_snap(s);
        /* the stock init leaves both MIX one-poles at the default MIX 0.0: start from there, as stock */
        s->mw = 0.0f;
        s->md = 0.0f;
        t->fresh = 0;
    }

    mbcomp_process(s, in[0], in[1], kl, kr, out[0], out[1], io->n);

    /* CMP2E: breadcrumb 15, the glue's meters (the seed below, gr0..gr2, cmp_load_end) */
    CRUMB(15);
    /* CMP1 #13: a restart in this call: seed the envelopes from the last block's gain reduction (gr0..gr2, not
       yet rewritten below), constant indices */
    s = &SLOT->mb;
    t = SLOT;
    if (s->trips != t->trips) {
        v = cmp_seed(t->gr0, s->band[0].y1);
        s->band[0].y1 = v;
        s->band[0].y2 = v;
        v = cmp_seed(t->gr1, s->band[1].y1);
        s->band[1].y1 = v;
        s->band[1].y2 = v;
        v = cmp_seed(t->gr2, s->band[2].y1);
        s->band[2].y1 = v;
        s->band[2].y2 = v;
    }

    /* meters */
    s = &SLOT->mb;
    y0 = s->band[0].y2;
    y1 = s->band[1].y2;
    y2 = s->band[2].y2;
    yd = y0;
    if (y1 < yd)
        yd = y1;
    if (y2 < yd)
        yd = y2;
    /* CMP2D: the bands' gain reductions stay in the slot (the restart seed above reads gr0..gr2); 0x257c1c and
       reply half-words 8..11 now carry the load readout (cmp_load_end) */
    t = SLOT;
    t->gr0 = y0 * FX_DB_PER_LOG2;
    t->gr1 = y1 * FX_DB_PER_LOG2;
    t->gr2 = y2 * FX_DB_PER_LOG2;
    t->grd = yd * FX_DB_PER_LOG2;
    t->trips = s->trips;
    t->blocks = t->blocks + 1;
    /* CMP2D: this block's compressor cycles and the readout, last thing in the call (CMP2E: with the trip and
       re-init counts) */
    cmp_load_end(s->trips, t->reinits);
    /* CMP2E: breadcrumb 16, glue exit: our code has finished its block */
    CRUMB(16);
}
