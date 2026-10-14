/* SPDX-License-Identifier: GPL-2.0-or-later */
/* cmp_glue.c - T6.3 CMP0: the 3-band compressor (fx/mbcomp.c) in place of the stock master compressor
 * 0xb8541d, on the stock knobs (notes/T6.4-compressor-map-and-plan.md 4.1, 4.3, 5; the proof is
 * tools/dspsplice/verify_cmp.py).
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
 * boot; cmp_process runs cmp_init itself if it finds no magic, so a zero state is valid. build.py checks the
 * slot's size from cmp_slot_bytes in the linked image against the FILL and the other blocks.
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

#define CMP_MAGIC 0x434d5030u       /* "CMP0" */
#define CMP_XLO 68.0f               /* 196 Hz */
#define CMP_XHI 44.0f               /* 2.61 kHz */

typedef struct {
    uint32_t magic;                 /* CMP_MAGIC once cmp_init has run */
    uint32_t fresh;                 /* 1 until the first block has applied the knobs and snapped */
    uint32_t blocks;                /* blocks processed */
    uint32_t sc_ext;                /* 1: the bands detected the key (sc != comp in) in the last block */
    uint32_t trips;                 /* mbcomp's guard trips, as of the last block */
    float last_thr;                 /* the knob values last applied (knob scale; RAT as the band step) */
    float last_atk;
    float last_rel;
    float last_mup;
    float last_rat;
    float last_mix;
    float gr0;                      /* the last block's y2 per band and the deepest, dB */
    float gr1;
    float gr2;
    float grd;
    uint32_t word10;                /* the reply words written at +0x10 and +0x14 */
    uint32_t word14;
    uint32_t pad;                   /* an even number of words before mb: the same offsets on the Mac */
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

/* a band's gain reduction y2 (log2 units, <= 0) -> Q15 (1 - g), as the stock getter (1 - clamp(g, 0, 1),
   fix by 15) */
static uint32_t cmp_q15(float y)
{
    float g;
    float x;
    int32_t q;
    FX_EXP2(y, g);
    if (g > 1.0f)
        g = 1.0f;
    if (!(g >= 0.0f))
        g = 0.0f;
    x = (1.0f - g) * 32768.0f + 0.5f;
    q = (int32_t)x;
    if (q > 32767)
        q = 32767;
    if (q < 0)
        q = 0;
    return (uint32_t)q;
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
    uint32_t q0;
    uint32_t q1;
    uint32_t q2;
    uint32_t qd;
    uint32_t w10;
    uint32_t w14;
    int32_t ri;
    uint32_t *tx;
    if (t->magic != CMP_MAGIC)
        cmp_init();
    s = &t->mb;

    /* the stock knobs, all three bands; each set only when its value changes, with constant indices */
    v = cmp_knob(p[1]);
    if (v != t->last_thr) {
        mbcomp_set(s, MB_P(0, MB_THR), v);
        mbcomp_set(s, MB_P(1, MB_THR), v);
        mbcomp_set(s, MB_P(2, MB_THR), v);
        t->last_thr = v;
    }
    v = cmp_knob(p[3]);
    if (v != t->last_atk) {
        mbcomp_set(s, MB_P(0, MB_ATK), v);
        mbcomp_set(s, MB_P(1, MB_ATK), v);
        mbcomp_set(s, MB_P(2, MB_ATK), v);
        t->last_atk = v;
    }
    v = cmp_knob(p[4]);
    if (v != t->last_rel) {
        mbcomp_set(s, MB_P(0, MB_REL), v);
        mbcomp_set(s, MB_P(1, MB_REL), v);
        mbcomp_set(s, MB_P(2, MB_REL), v);
        t->last_rel = v;
    }
    v = cmp_knob(p[5]);
    if (v != t->last_mup) {
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
    if (v != t->last_rat) {
        mbcomp_set(s, MB_P(0, MB_RAT), v);
        mbcomp_set(s, MB_P(1, MB_RAT), v);
        mbcomp_set(s, MB_P(2, MB_RAT), v);
        t->last_rat = v;
    }
    v = cmp_knob(p[6]);
    if (v != t->last_mix) {
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
    if (ext != t->sc_ext) {
        mbcomp_set(s, MB_SCS, (float)ext);
        t->sc_ext = ext;
    }
    if (t->fresh) {
        mbcomp_snap(s);
        /* the stock init leaves both MIX one-poles at the default MIX 0.0: start from there, as stock */
        s->mw = 0.0f;
        s->md = 0.0f;
        t->fresh = 0;
    }

    mbcomp_process(s, in[0], in[1], kl, kr, out[0], out[1], io->n);

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
    *GR_Y2 = yd * FX_DB_PER_LOG2;
    q0 = cmp_q15(y0);
    q1 = cmp_q15(y1);
    q2 = cmp_q15(y2);
    qd = cmp_q15(yd);
    w10 = q0 + q1 * 65536u;
    w14 = q2 + qd * 65536u;
    tx = TX0;
    tx[4] = w10;
    tx[5] = w14;
    tx = TX1;
    tx[4] = w10;
    tx[5] = w14;
    t = SLOT;
    t->gr0 = y0 * FX_DB_PER_LOG2;
    t->gr1 = y1 * FX_DB_PER_LOG2;
    t->gr2 = y2 * FX_DB_PER_LOG2;
    t->grd = yd * FX_DB_PER_LOG2;
    t->word10 = w10;
    t->word14 = w14;
    t->trips = s->trips;
    t->blocks = t->blocks + 1;
}
