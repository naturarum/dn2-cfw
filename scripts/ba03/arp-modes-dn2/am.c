/* SPDX-License-Identifier: GPL-2.0-or-later */
/* arp-modes (Digitone II OS 1.12): the arpeggiator's SHUF, RAND and TM (am.s has the step's sites and registers).
 *
 *   SHUF (MODE 5)  every held note in every octave of RANGE once per cycle, in a new random order each cycle; a cycle
 *                  does not start on the note the one before ended on. More than 64 notes x octaves: as RAND.
 *   RAND (MODE 6)  a random held note in a random octave of RANGE at each step.
 *   TM (MODE 7..107, 0.2)  a Turing Machine loop: a 16-bit shift register per track that moves one place at every
 *                  step of the arp, rests included. The bit leaving the loop (ARP LENGTH steps long) comes back in,
 *                  flipped with the chance LOOP CHANGE / 100, LOOP CHANGE being MODE - 7: 0 locks the loop, 50 makes
 *                  every step new, 100 flips every bit, so the loop plays twice its length, inverted the second time.
 *                  The note is the register's low 8 bits scaled over the held notes x the octaves of RANGE, lowest
 *                  first; the steps' offsets are not added.
 * All three take the notes held (the arp's 128-bit set), RANGE, the steps' on/off, LEN, SPEED and N.LEN as the stock
 * modes do; SHUF and RAND add the steps' offsets, and their off steps rest without using up a note. Inspired by research
 * findings of dn2_firmware_explore (angellinares) on OS 1.11; TM after Tom Whitwell's Turing Machine (Music Thing
 * Modular); this is our own code. For dn2-cfw (notes/BA02-base-0.2.md, notes/BA03-turing-arp.md).
 *
 * The ARP menu (ArpSetupMenuView, 0.2): MODE's eighth name, CHRD (which no stock menu reaches), reads TM; knob A steps
 * OFF .. RAND, TM and holds there, and comes back into TM with the track's last LOOP CHANGE; in TM, knob E is LOOP
 * CHANGE (0..100) where STEP OFFSET was, and the step graph shows the loop's notes, one bar per step (the place of the
 * note among the notes held, or the loop's own value with none held), with the playing step marked above the row of
 * squares; the menu's tick redraws it when the loop moves.
 *
 * The random source is xorshift32, seeded at its first use from a constant and the frame clock 0x80005398 (another
 * order after each power-on); a test can set am_rng beforehand for a fixed sequence. The engine runs in the voice
 * interrupt only (the arp's step), as the stock arp does; the menu only reads the loop (seeding one it has not made).
 */
typedef unsigned long u32;
typedef unsigned short u16;
typedef unsigned char u8;

#define HELD  ((volatile u32 *)0x40599728)              /* track t's held notes: 4 words at 4 t; bit b of word w = note 32 w + b */
#define CLOCK ((volatile u32 *)0x80005398)
#define HEADS ((volatile u32 *)0x4059d868)              /* track t's press-order list: zero while no note is held */
#define ARPST ((volatile u8 *)0x405994a8)               /* track t's arp state at 40 t: +0 the step playing */

/* the menu's stock helpers (the ARP menu finds its sound the same way) */
#define PROJECT    ((void *(*)(void))0x4018ac5a)
#define P_TRACKS   ((void *(*)(void *))0x4003e40e)
#define P_SOUNDS   ((void *(*)(void *))0x4003e41a)
#define TRACK_OF   ((int (*)(void *))0x40046bfe)
#define SOUND_OF   ((void *(*)(void *, int))0x40030e66)
#define GET_MODE   ((int (*)(void *))0x4004be74)
#define SET_MODE   ((void (*)(void *, int))0x4004bea4)
#define GET_LENM1  ((int (*)(void *))0x4004bba0)
#define GET_RANGE  ((int (*)(void *))0x4004c0aa)
#define STEP_ON    ((int (*)(void *, int))0x4004bd06)
#define GET_OFS    ((int (*)(void *, int))0x4004bc3c)
#define SET_OFS    ((void (*)(void *, int, int))0x4004bc84)
#define MENU_DRAW  ((void (*)(void *, void *))0x40018818)  /* ArpSetupMenuView slot 4 */
#define MENU_TICK  ((void (*)(void *))0x40019a72)          /* ArpSetupMenuView slot 11 */
#define INVALIDATE ((void (*)(void *))0x4011ca9a)
#define FILL       ((void (*)(void *, int, int, int, int, int))0x40114b22)   /* x0, y0, x1, y1 (inclusive), colour */
#define HLINE      ((void (*)(void *, int, int, int, int))0x40113fd4)        /* x0, y, x1, colour */
#define DOTS_V     ((void (*)(void *, int, int, int, int))0x40114346)        /* x, y0, y1, colour: dotted */
#define FONT       ((void *(*)(void *))0x40115fe4)
#define TEXT       ((void (*)(void *, void *, int, int, int, int, const char *, const char *, ...))0x40115948)
#define SMALL      ((void *)0x44508ed8)                 /* the font of the menu's knob labels */
#define T_XXX      ((const char *)0x40217572)           /* "XXX", the value's width */
#define T_D        ((const char *)0x4021517a)           /* "%d" */

/* The screen's y runs up from the bottom row (0) to the top (63). */

struct am_track {
    u32 sig;            /* the held set and RANGE the order was made for */
    u32 id;             /* the arp's id it was made in (a new arp starts a new order) */
    u8 m;               /* notes x octaves in the order */
    u8 pos;             /* the next place */
    u8 last;            /* the last slot played */
    u8 made;            /* 1: an order exists */
    u8 perm[64];
};

struct am_tm {
    u16 reg;            /* the loop, the newest bit lowest */
    u8 pos;             /* the step it moved at last */
    u8 made;            /* 1: seeded */
};

struct am_track am_tr[16] __attribute__((aligned(4)));
struct am_tm am_tml[16] __attribute__((aligned(4)));
u8 am_chg[16];          /* each track's LOOP CHANGE when it last left TM (knob A brings it back) */
u32 am_drawn;           /* what the ARP menu drew last in TM; its tick redraws when the loop has moved since */
u32 am_rng;

const char am_s_tm[] = "TM";
static const char am_s_loop[] = "LOOP";
static const char am_s_change[] = "CHANGE";

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

static int clamp_range(int range)
{
    return range < 0 ? 0 : range > 9 ? 9 : range;
}

/* -> the note (0..127) with the octave stored at st + 0x1c, or -1 (a rest) */
int am_pick(int t, u8 *st, const u8 *sound, int mode)
{
    const volatile u32 *w = HELD + 4 * (t & 15);
    int range = (signed char)sound[0x161], n = held_count(w), m, k;

    if (n <= 0)
        return -1;
    range = clamp_range(range);
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

/* ---- TM */

static int is_tm(int mode)
{
    return mode >= 7 && mode <= 107;
}

static int tm_len(int len_m1)
{
    int len = len_m1 + 1;
    return len < 1 ? 1 : len > 16 ? 16 : len;
}

static void tm_seed(struct am_tm *s)
{
    if (!s->made) {
        s->reg = (u16)rnd();
        s->made = 1;
    }
}

/* one step: the bit leaving the loop comes back in, flipped with the chance c / 100 */
static void tm_move(struct am_tm *s, int len, int c)
{
    int b;

    tm_seed(s);
    b = (s->reg >> (len - 1)) & 1;
    if (c >= 100 || (c > 0 && (int)(rnd() % 100u) < c))
        b ^= 1;
    s->reg = (u16)((s->reg << 1) | b);
}

/* an off step in TM (am_rest): the loop moves on */
void am_tm_rest(int t, u8 *st, const u8 *sound, int mode)
{
    struct am_tm *s = &am_tml[t & 15];

    tm_move(s, tm_len((signed char)sound[0x163]), mode - 7);
    s->pos = (u8)*(volatile u32 *)st;
}

/* an on step in TM -> the note + 12 x its octave (the note at st + 0x0c, the octave at + 0x1c), or -1 (none held) */
int am_tm(int t, u8 *st, const u8 *sound, int mode)
{
    const volatile u32 *w = HELD + 4 * (t & 15);
    struct am_tm *s = &am_tml[t & 15];
    int n = held_count(w), m, k, oct, note;

    tm_move(s, tm_len((signed char)sound[0x163]), mode - 7);
    s->pos = (u8)*(volatile u32 *)st;
    if (n <= 0)
        return -1;
    m = n * (clamp_range((signed char)sound[0x161]) + 1);
    k = (int)(((u32)(s->reg & 0xff) * (u32)m) >> 8);
    oct = k / n;
    note = held_note(w, k % n);
    *(volatile u32 *)(st + 0x1c) = (u32)oct;
    *(volatile u32 *)(st + 0x0c) = (u32)note;
    return note + 12 * oct;
}

/* ---- the ARP menu */

static int ui_track(void)
{
    return TRACK_OF(P_TRACKS(PROJECT())) & 15;
}

static void *ui_sound(int t)
{
    return SOUND_OF(P_SOUNDS(PROJECT()), t);
}

static int mode_of(void *snd)
{
    return (signed char)GET_MODE(snd);
}

/* the menu's MODE name (jsr from 0x400188de, for getMode): TM's for TM, CYCL's for what plays as CYCL */
int am_mode_name(void *snd)
{
    int m = mode_of(snd);

    if (is_tm(m))
        return 7;
    if (m < 0 || m > 7)
        return 4;
    return m;
}

/* knob A (jsr from 0x40018eee, for setMode(snd, MODE + the turn)): OFF .. RAND, TM, holding at TM */
void am_knob_mode(void *snd, int v)
{
    int cur = mode_of(snd), t = ui_track(), at, to;
    int d = (signed char)(v - cur);                     /* the turn (the stock adds it to MODE in 8 bits) */

    at = is_tm(cur) ? 7 : cur < 0 || cur > 7 ? 4 : cur;
    to = at + d;
    if (to < 0)
        to = 0;
    if (to > 7)
        to = 7;
    if (is_tm(cur)) {
        if (to == 7)
            return;
        am_chg[t] = (u8)(cur - 7);
    }
    if (to == 7) {
        if (am_chg[t] > 100)
            am_chg[t] = 0;
        tm_seed(&am_tml[t]);
        SET_MODE(snd, 7 + am_chg[t]);
    } else
        SET_MODE(snd, to);
}

/* knob E (jsr from 0x400190ae, for setOffset(snd, step, offset + the turn)): in TM, LOOP CHANGE 0..100 */
void am_knob_e(void *snd, int step, int v)
{
    int cur = mode_of(snd), c;

    if (!is_tm(cur)) {
        SET_OFS(snd, step, v);
        return;
    }
    c = cur - 7 + (v - GET_OFS(snd, step));
    if (c < 0)
        c = 0;
    if (c > 100)
        c = 100;
    am_chg[ui_track()] = (u8)c;
    if (c != cur - 7)
        SET_MODE(snd, 7 + c);
}

/* the loop's bars, 1..21 for each step: the loop as it will play from where it is (no change assumed) */
static void tm_bars(int t, void *snd, int len, u8 *h)
{
    struct am_tm *s = &am_tml[t];
    u32 r = s->reg;
    int n = held_count(HELD + 4 * t), m = n * (clamp_range((signed char)GET_RANGE(snd)) + 1), j;

    for (j = 1; j <= len; j++) {
        int p = (s->pos + j) % len;
        r = ((r << 1) | ((r >> (len - 1)) & 1)) & 0xffff;
        if (m > 1)
            h[p] = (u8)(1 + (int)(((r & 0xff) * (u32)m) >> 8) * 20 / (m - 1));
        else if (m == 1)
            h[p] = 1;
        else
            h[p] = (u8)(1 + (int)(r & 0xff) * 20 / 255);
    }
}

static u32 tm_sig(int t, void *snd)
{
    struct am_tm *s = &am_tml[t];

    return ((u32)s->reg << 16) ^ ((u32)s->pos << 8) ^ (u32)held_count(HELD + 4 * t) ^ ((u32)GET_LENM1(snd) << 4)
           ^ ((u32)(u8)GET_RANGE(snd) << 12) ^ ((u32)(u8)GET_MODE(snd) << 24) ^ (HEADS[t] ? 0x80000000u : 0) ^ 1u;
}

/* ArpSetupMenuView slot 4: the stock menu, then in TM LOOP CHANGE on knob E and the loop in the step graph */
void am_menu_draw(void *view, void *bm)
{
    int t, m, len, i;
    void *snd, *font;
    u8 h[16];

    MENU_DRAW(view, bm);
    t = ui_track();
    snd = ui_sound(t);
    m = mode_of(snd);
    if (!is_tm(m)) {
        am_drawn = 0;
        return;
    }
    len = tm_len(GET_LENM1(snd));
    FILL(bm, 0, 8, 0x1b, 0x21, 0);                      /* knob E's column, above its (E) */
    font = FONT(SMALL);
    TEXT(bm, font, 0xf, 0x19, 1, 0, am_s_loop, am_s_loop);
    TEXT(bm, font, 0xf, 0x13, 1, 0, am_s_change, am_s_change);
    TEXT(bm, font, 0xf, 9, 1, 0, T_XXX, T_D, m - 7);
    FILL(bm, 0x1e, 4, 0x63, 0x1f, 0);                   /* the step graph, above the row of squares */
    tm_bars(t, snd, len, h);
    for (i = 0; i < len; i++) {
        int x = 0x21 + 4 * i;
        if ((u8)STEP_ON(snd, i))
            FILL(bm, x, 7, x + 2, 8 + h[i], 1);         /* 3 px at least */
        else
            FILL(bm, x + 1, 7, x + 1, 7, 1);            /* a rest: one dot */
    }
    if (HEADS[t]) {                                     /* the arp is playing: its step */
        int p = (int)*(volatile u32 *)(ARPST + 40 * t);
        if (p >= 0 && p < len)
            HLINE(bm, 0x21 + 4 * p, 5, 0x23 + 4 * p, 1);
    }
    DOTS_V(bm, 0x21 + 4 * len, 7, 0x1f, 1);             /* the loop's end, as the stock marks LEN */
    am_drawn = tm_sig(t, snd);
}

/* ArpSetupMenuView slot 11, the menu's tick: the stock tick, then a redraw in TM when the loop has moved */
void am_menu_tick(void *view)
{
    int t;
    void *snd;

    MENU_TICK(view);
    t = ui_track();
    snd = ui_sound(t);
    if (is_tm(mode_of(snd)) && tm_sig(t, snd) != am_drawn)
        INVALIDATE(view);
}
