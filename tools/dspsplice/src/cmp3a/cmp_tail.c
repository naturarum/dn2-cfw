/* SPDX-License-Identifier: GPL-2.0-or-later */
/* cmp_tail.c - T6.3 CMP2: the band knobs from the SPI frame tail (notes/T6.4-compressor-map-and-plan.md 3.4, 5;
 * notes/T6.3-cmp2.md). CMP3 (notes/T6.3-cmp3.md): cmp_tail_apply also takes the three band mixes, k 87..89 = words
 * 19..21 (param-rows-dn2 0.3), as table indices (rounded to the step, as ATK and REL); the fetch is CMP2's, unchanged
 * (frozen with the rest in src/cmp2/). The ColdFire mod comp-link-dn2 0.1 (vendor/elekloader/examples/comp-link-dn2/tail.s) writes
 * 30 words into the frame bytes 0xa80..0xabb, which the stock driver sends but never fills and no stock, FLG1 or FTD1
 * instruction reads (T6.0, T6.4 3.4):
 *   word 0      magic 0xC3B1 (low byte = layout 1)
 *   word 1      sequence, +1 per tail the ColdFire writes
 *   words 2..28 the ColdFire's smoothed row-16 words of k 70..96 (k = 68 + word; value << 8, glided as the
 *               stock frame words are)
 *   word 29     ~(sum of words 0..28), low 16 bits (an all-zero tail, what a main OS without comp-link sends,
 *               never passes)
 * They land in both SPI2 receive buffers 0x2c29d0 / 0x2c39d0 (+ 0x1000 x DM(0x2c0450) is the one the stock code
 * reads) at +0xa80, as 16-bit words stored little-endian, so each 32-bit long holds two words, the lower one in its
 * low half.
 *
 * cmp_tail_fetch (every block, from cmp_process): copies both tails with 32-bit loads (selcc builds a 16-bit load
 * out of byte extractions), checks each copy (magic, check word) after it is taken and never reads the live buffer
 * again, takes the newer of the valid ones by the signed 16-bit difference of their sequences, and accepts it when
 * its sequence differs from the held one (equality only: a pause of any length in the calls cannot lock new tails
 * out, as "newer than held" would after 32,768 frames); otherwise the held values stay. A torn copy (the DMA
 * writing the tail while it is read) fails the check and the other buffer's is used.
 * cmp_tail_apply: the held words -> the band knobs as knob = word / 256 (= 127 x word / 32512, exact: a settled
 * word v << 8 gives v); ATK and REL, which index the stock tables by trunc(knob), take the word rounded to the
 * nearest knob step, so a settled word one below v << 8 still gives v. Each knob is set only when it differs from
 * the compressor's own knob word (a corrupted knob word lasts one call). mbcomp_set clamps every value to its row's
 * range (THR, ATK, REL, MUP, XOVR 0..127, RAT 0..8).
 *
 * selcc (notes/T3.11, T5.2, T5.3, T6.3): no store indexed by a variable after a call (cmp_tail_fetch makes no call;
 * cmp_tail_apply stores nothing of its own), constant indices, one declaration per variable.
 */
#include <stdint.h>
#include "mbcomp.h"
#include "cmp_tail.h"

const uint32_t cmp_tail_bytes = (uint32_t)sizeof(CmpTail);

#ifdef CMP_NATIVE
static CmpTail cmp_native_tail;
static uint32_t cmp_native_rx[2][CT_NL];
void *cmp_native_tail_ptr(void) { return &cmp_native_tail; }
void *cmp_native_rx_tail(int half) { return cmp_native_rx[half & 1]; }
#define CT_STATE (&cmp_native_tail)
#define CT_RX0 ((const volatile uint32_t *)cmp_native_rx[0])
#define CT_RX1 ((const volatile uint32_t *)cmp_native_rx[1])
#else
#define CT_STATE ((CmpTail *)CT_STATE_AT)                     /* FILL-zeroed at boot (recipes CMP2, CMP3) */
#define CT_RX0 ((const volatile uint32_t *)(0x2c29d0 + 0xa80))
#define CT_RX1 ((const volatile uint32_t *)(0x2c39d0 + 0xa80))
#endif

uint32_t cmp_tail_fetch(void)
{
    CmpTail *t = CT_STATE;
    const volatile uint32_t *src;
    uint32_t *dst;
    const uint32_t *c;
    uint32_t x;
    uint32_t s;
    uint32_t chk;
    uint32_t va;
    uint32_t vb;
    uint32_t sq;
    int32_t i;

    t->calls = t->calls + 1;
    src = CT_RX0;
    dst = t->a;
    s = 0;
    for (i = 0; i < CT_NL; i++) {
        x = src[i];
        dst[i] = x;
        s = s + (x & 0xffffu) + (x >> 16);
    }
    chk = t->a[CT_NL - 1] >> 16;
    s = s - chk;
    va = 0;
    if ((t->a[0] & 0xffffu) == CT_MAGIC) {
        if (((~s) & 0xffffu) == chk)
            va = 1;
    }
    src = CT_RX1;
    dst = t->b;
    s = 0;
    for (i = 0; i < CT_NL; i++) {
        x = src[i];
        dst[i] = x;
        s = s + (x & 0xffffu) + (x >> 16);
    }
    chk = t->b[CT_NL - 1] >> 16;
    s = s - chk;
    vb = 0;
    if ((t->b[0] & 0xffffu) == CT_MAGIC) {
        if (((~s) & 0xffffu) == chk)
            vb = 1;
    }

    c = 0;
    if (va != 0)
        c = t->a;
    if (vb != 0) {
        if (va == 0) {
            c = t->b;
        } else {
            /* both valid: b is newer when seq(b) - seq(a) is 1..0x7fff as a 16-bit number */
            x = ((t->b[0] >> 16) - (t->a[0] >> 16)) & 0xffffu;
            if (x != 0) {
                if (x < 0x8000u)
                    c = t->b;
            }
        }
    }
    if (c == 0) {
        t->none = t->none + 1;
        x = 0;
        if (t->have != 0)
            x = 1;
        return x;
    }
    sq = c[0] >> 16;
    if (t->have != 0) {
        if (sq == t->seq) {
            t->same = t->same + 1;
            return 1;
        }
    }
    dst = t->w;
    for (i = 0; i < CT_NL; i++)
        dst[i] = c[i];
    t->seq = sq;
    t->have = 1;
    t->accepted = t->accepted + 1;
    return 1;
}

/* tail word j of the held tail */
#define CT_WORD(j) (((j) & 1) ? (CT_STATE->w[(j) >> 1] >> 16) : (CT_STATE->w[(j) >> 1] & 0xffffu))
/* a continuous knob (THR, MUP, RAT, the crossovers): word / 256 */
#define CT_KNOB(j) ((float)(int32_t)CT_WORD(j) * 0.00390625f)
/* a table-index knob (ATK, REL): the word rounded to the nearest knob step */
#define CT_STEP(j) ((float)(int32_t)((CT_WORD(j) + 0x80u) >> 8))

void cmp_tail_apply(MBComp *s)
{
    float v;
    /* LOW: k 70..75 = words 2..7 (THR RAT ATK REL MUP, then the low/mid crossover) */
    v = CT_KNOB(2);
    if (v != s->knob[MB_P(0, MB_THR)])
        mbcomp_set(s, MB_P(0, MB_THR), v);
    v = CT_KNOB(3);
    if (v != s->knob[MB_P(0, MB_RAT)])
        mbcomp_set(s, MB_P(0, MB_RAT), v);
    v = CT_STEP(4);
    if (v != s->knob[MB_P(0, MB_ATK)])
        mbcomp_set(s, MB_P(0, MB_ATK), v);
    v = CT_STEP(5);
    if (v != s->knob[MB_P(0, MB_REL)])
        mbcomp_set(s, MB_P(0, MB_REL), v);
    v = CT_KNOB(6);
    if (v != s->knob[MB_P(0, MB_MUP)])
        mbcomp_set(s, MB_P(0, MB_MUP), v);
    v = CT_KNOB(7);
    if (v != s->knob[MB_XLO])
        mbcomp_set(s, MB_XLO, v);
    /* MID: k 76..81 = words 8..13 (then the mid/high crossover) */
    v = CT_KNOB(8);
    if (v != s->knob[MB_P(1, MB_THR)])
        mbcomp_set(s, MB_P(1, MB_THR), v);
    v = CT_KNOB(9);
    if (v != s->knob[MB_P(1, MB_RAT)])
        mbcomp_set(s, MB_P(1, MB_RAT), v);
    v = CT_STEP(10);
    if (v != s->knob[MB_P(1, MB_ATK)])
        mbcomp_set(s, MB_P(1, MB_ATK), v);
    v = CT_STEP(11);
    if (v != s->knob[MB_P(1, MB_REL)])
        mbcomp_set(s, MB_P(1, MB_REL), v);
    v = CT_KNOB(12);
    if (v != s->knob[MB_P(1, MB_MUP)])
        mbcomp_set(s, MB_P(1, MB_MUP), v);
    v = CT_KNOB(13);
    if (v != s->knob[MB_XHI])
        mbcomp_set(s, MB_XHI, v);
    /* HIGH: k 82..86 = words 14..18 */
    v = CT_KNOB(14);
    if (v != s->knob[MB_P(2, MB_THR)])
        mbcomp_set(s, MB_P(2, MB_THR), v);
    v = CT_KNOB(15);
    if (v != s->knob[MB_P(2, MB_RAT)])
        mbcomp_set(s, MB_P(2, MB_RAT), v);
    v = CT_STEP(16);
    if (v != s->knob[MB_P(2, MB_ATK)])
        mbcomp_set(s, MB_P(2, MB_ATK), v);
    v = CT_STEP(17);
    if (v != s->knob[MB_P(2, MB_REL)])
        mbcomp_set(s, MB_P(2, MB_REL), v);
    v = CT_KNOB(18);
    if (v != s->knob[MB_P(2, MB_MUP)])
        mbcomp_set(s, MB_P(2, MB_MUP), v);
    /* CMP3: the band mixes, k 87..89 = words 19..21 (stock MIX table indices; mbcomp_set clamps them to 0..127) */
    v = CT_STEP(19);
    if (v != s->knob[MB_P(0, MB_BMIX)])
        mbcomp_set(s, MB_P(0, MB_BMIX), v);
    v = CT_STEP(20);
    if (v != s->knob[MB_P(1, MB_BMIX)])
        mbcomp_set(s, MB_P(1, MB_BMIX), v);
    v = CT_STEP(21);
    if (v != s->knob[MB_P(2, MB_BMIX)])
        mbcomp_set(s, MB_P(2, MB_BMIX), v);
}
