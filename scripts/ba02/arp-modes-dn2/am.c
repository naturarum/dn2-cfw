/* SPDX-License-Identifier: GPL-2.0-or-later */
/* arp-modes (Digitone II OS 1.12): the arpeggiator's SHUF and RAND (am.s has the sites and registers).
 *
 *   SHUF (MODE 5)  every held note in every octave of RANGE once per cycle, in a new random order each cycle; a cycle
 *                  does not start on the note the one before ended on. More than 64 notes x octaves: as RAND.
 *   RAND (MODE 6)  a random held note in a random octave of RANGE at each step.
 * Both take the notes held (the arp's 128-bit set, lowest first), RANGE, the steps' on/off and offsets, LEN, SPEED and
 * N.LEN as the stock modes do: a step that is off rests and uses up no note. Inspired by research findings of
 * dn2_firmware_explore (angellinares) on OS 1.11; this is our own code. For dn2-cfw (notes/BA02-base-0.2.md).
 *
 * The random source is xorshift32, seeded at its first use from a constant and the frame clock 0x80005398 (another
 * order after each power-on); a test can set am_rng beforehand for a fixed sequence. It runs in the voice interrupt
 * only (the arp's step), as the stock arp does.
 */
typedef unsigned long u32;
typedef unsigned char u8;

#define HELD  ((volatile u32 *)0x40599728)              /* track t's held notes: 4 words at 4 t; bit b of word w = note 32 w + b */
#define CLOCK ((volatile u32 *)0x80005398)

struct am_track {
    u32 sig;            /* the held set and RANGE the order was made for */
    u32 id;             /* the arp's id it was made in (a new arp starts a new order) */
    u8 m;               /* notes x octaves in the order */
    u8 pos;             /* the next place */
    u8 last;            /* the last slot played */
    u8 made;            /* 1: an order exists */
    u8 perm[64];
};

struct am_track am_tr[16] __attribute__((aligned(4)));
u32 am_rng;

static u32 rnd(void)
{
    u32 x = am_rng;
    if (!x) {
        x = 0x2545f491u ^ *CLOCK;
        if (!x)
            x = 0x2545f491u;
    }
    x ^= x << 13;
    x ^= x >> 17;
    x ^= x << 5;
    am_rng = x;
    return x;
}

static int held_count(const volatile u32 *w)
{
    int i, n = 0;
    for (i = 0; i < 4; i++) {
        u32 x = w[i];
        while (x) {
            x &= x - 1;
            n++;
        }
    }
    return n;
}

/* the k-th held note, lowest first (k below the count) */
static int held_note(const volatile u32 *w, int k)
{
    int note;
    for (note = 0; note < 128; note++)
        if ((w[note >> 5] >> (note & 31)) & 1)
            if (k-- == 0)
                return note;
    return -1;
}

/* -> the note (0..127) with the octave stored at st + 0x1c, or -1 (a rest) */
int am_pick(int t, u8 *st, const u8 *sound, int mode)
{
    const volatile u32 *w = HELD + 4 * (t & 15);
    int range = (signed char)sound[0x161], n = held_count(w), m, k;

    if (n <= 0)
        return -1;
    if (range < 0)
        range = 0;
    if (range > 9)
        range = 9;
    m = n * (range + 1);
    if (mode == 6 || m > 64) {
        k = (int)(rnd() % (u32)m);
    } else {
        struct am_track *a = &am_tr[t & 15];
        u32 sig = w[0] ^ (w[1] * 3u) ^ (w[2] * 5u) ^ (w[3] * 7u) ^ ((u32)range << 28);
        u32 id = *(volatile u32 *)(st + 0x18);
        if (!a->made || a->sig != sig || a->id != id || a->m != m || a->pos >= m) {
            int i;
            for (i = 0; i < m; i++)
                a->perm[i] = (u8)i;
            for (i = m - 1; i > 0; i--) {               /* Fisher-Yates */
                int j = (int)(rnd() % (u32)(i + 1));
                u8 x = a->perm[i];
                a->perm[i] = a->perm[j];
                a->perm[j] = x;
            }
            if (a->made && m > 1 && a->perm[0] == a->last) {   /* not the note the cycle before ended on */
                int j = 1 + (int)(rnd() % (u32)(m - 1));
                u8 x = a->perm[0];
                a->perm[0] = a->perm[j];
                a->perm[j] = x;
            }
            a->sig = sig;
            a->id = id;
            a->m = (u8)m;
            a->pos = 0;
            a->made = 1;
        }
        k = a->perm[a->pos++];
        a->last = (u8)k;
    }
    *(volatile u32 *)(st + 0x1c) = (u32)(k / n);
    return held_note(w, k % n);
}
