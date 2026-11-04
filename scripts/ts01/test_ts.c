/* SPDX-License-Identifier: GPL-2.0-or-later */
/* scripts/ts01/test_ts.c: trig-slide (vendor/elekloader/examples/trig-slide-dn2/ts.c) on the Mac. The stock OS's
 * memory is stood in by five regions at its own addresses (TS_HOST: every access of ts.c goes through host()), its
 * sequencer, event queue, block handler and voices by a model of what notes/T2.9-trig-slides.md reads in the code:
 * the scheduler builds each step's event a step ahead and queues it through ts_queue; each block the handler ends
 * gates (through ts_gateoff), takes the events due before clock + 2 rate (a mono track's held voice cut first, the set
 * copied into a row with its machine, the locks into the row and the smoothing state), then calls ts_block in place
 * of the smoothing. Checks, scenario by scenario: every slide, every block, in every row its track owns, on the
 * straight line from where it started to the trig it moves toward, the smoothing state with it, only parameters that
 * slide; the rows at the trig's values when the trig is taken (nothing left to change) and the trig at the time the
 * slide first aimed for (the look-ahead's estimate exact, swing and micro-timing included); slides only in gaps, only
 * within one machine, only while playing with SLIDE on; the generator and the PRE state as they were after every
 * ts_queue; ts_block returning the smoothing's value, once a block.
 *   cc -O2 -Wall -Wextra -o out/TS01/test_ts scripts/ts01/test_ts.c && out/TS01/test_ts
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* the stock OS's memory: five regions at its addresses */
static unsigned char rA[0x10000], rB[0xc0000], rC[0x10000], rD[0x10000], rE[0x80000];
static const struct { unsigned int base, size; unsigned char *p; } reg_[] = {
    { 0x80000000u, sizeof rA, rA }, { 0x401f0000u, sizeof rB, rB }, { 0x42c50000u, sizeof rC, rC },
    { 0x44630000u, sizeof rD, rD }, { 0x10000000u, sizeof rE, rE },
};
static void *host(unsigned int a, unsigned int n)
{
    unsigned int i;
    for (i = 0; i < sizeof reg_ / sizeof reg_[0]; i++)
        if (a >= reg_[i].base && a - reg_[i].base + n <= reg_[i].size)
            return reg_[i].p + (a - reg_[i].base);
    fprintf(stderr, "an access outside the model's memory: %#x\n", a);
    abort();
}

#define TS_HOST
#define M32(a)  (*(volatile unsigned int *)host((a), 4))
#define MS16(a) (*(volatile short *)host((a), 2))
#define M8(a)   (*(volatile unsigned char *)host((a), 1))
#define MS8(a)  (*(volatile signed char *)host((a), 1))
#define ADDR(p) ((u32)(unsigned long)(p))

static void q_insert(unsigned int ev, unsigned int time);
static unsigned int ev_build(int t, unsigned int pat, unsigned int kit, int step, unsigned int ev, int *micro,
                             int *swing);
static void gate_off(unsigned int v);
static unsigned int smooth(unsigned int mirror);
static unsigned int row_of(unsigned int k, int m, int f);
static int swing_amount(void);
static unsigned int conv(unsigned int t, unsigned int id);
#define Q_INSERT q_insert
#define EV_BUILD ev_build
#define GATE_OFF gate_off
#define SMOOTH   smooth
#define ROW_OF   row_of
#define SWING    swing_amount
#define CONV     conv
#define TS_EV    0x10078000u
#define TS_LK    0x10078100u

#include "../../vendor/elekloader/examples/trig-slide-dn2/ts.c"

#define KITA   0x10000000u         /* the kit */
#define POOL   0x10008000u         /* a sound lock's set: another machine */
#define PAT    0x10010000u         /* the pattern */
#define EVS    0x10030000u         /* the scheduler's events, 0x6c each */
#define LKS    0x10040000u         /* their lock sets */
#define MIRROR 0x80003af0u
#define SM_OUT 0x800068e4u          /* the smoothing's return */
#define RATE_V 14400u               /* 120 BPM, 6 ticks a step: 187.5 blocks a step */
#define STEP_BLOCKS 188u

struct trig { int on, micro, swing, pool, nl; u32 gate; s16 lk[NLOCK], lv[NLOCK]; };
static struct trig trig_[NT][64];
static int poly[NT], slide_ever[NT], len_, swing_v, armed[NT], armed_n[64], fails, n_lin, n_arr, n_blocks;
static u32 held[NT], gate_end[NROW], voice_on[NROW], tick_n, counter_[NT], ev_n, lk_n, smooth_calls, first_t1[NT];
static s16 trig_val[NROW][NK + 1];
static struct { u32 ev, time; } q_[512];
static int nq;

#define CHECK(c, ...) do { if (!(c)) { if (fails++ < 25) { printf("  FAIL line %d: ", __LINE__); \
                                         printf(__VA_ARGS__); printf("\n"); } } } while (0)

/* the stock OS, as the model stands it in */
static void q_insert(u32 ev, u32 time)
{
    int i = nq++;
    if (nq > 512)
        abort();
    while (i > 0 && (s32)(q_[i - 1].time - time) > 0) {
        q_[i] = q_[i - 1];
        i--;
    }
    q_[i].ev = ev;
    q_[i].time = time;
}

static u32 ev_build(s32 t, u32 pat, u32 kit, s32 step, u32 ev, s32 *micro, s32 *swing)
{
    const struct trig *g = &trig_[t][step];
    s32 tps = (s8)TICKS(MS8(pat + 0x1dd5a));
    u32 locks, i;
    RNG0 = RNG0 + RNG1;                     /* a probability draw (0x40136984) */
    RNG1 = RNG1 * 3 + 7;
    if (ev)
        PRE_PREVIEW(t) = 0x5a;              /* a supplied event's PRE result (0x401369cc) */
    if (!g->on || (ev && g->micro < 0))     /* no trig; a supplied event's build skips a note nudged early */
        return 0;
    if (!ev) {
        ev = EVS + 0x6c * (ev_n++ & 127);
        memset(host(ev, 0x6c), 0, 0x6c);
        locks = LKS + 0x340 * (lk_n++ & 127);
    } else
        locks = M32(ev + 0x54);
    M32(locks + 8) = (u32)g->nl;
    for (i = 0; i < (u32)g->nl; i++) {
        MS16(locks + 0x14 + 8 * i) = g->lk[i];
        MS16(locks + 0x16 + 8 * i) = g->lv[i];
    }
    M32(ev) = 1;
    M32(ev + 0xc) = 100 * 0x147af >> 7;     /* QUANTIZE 0 */
    M32(ev + 0x10) = (u32)t;
    M32(ev + 0x1c) = pat + 0x6a50;
    M32(ev + 0x38) = 1 | (g->swing ? 0x10 : 0);
    M32(ev + 0x3c) = g->pool ? POOL : SOUND(kit, t);
    M32(ev + 0x54) = locks;
    M32(ev + 0x60) = g->gate;               /* the model's own: the note's length, its step */
    M32(ev + 0x64) = (u32)step;
    *micro = g->micro * tps * 25;
    if (g->swing)
        *swing = SWING() * tps * 18000;
    return ev;
}

static void gate_off(u32 v)
{
    s32 t;
    if (v >= NROW || (t = VTRACK(v)) < 0 || VNOTE(v) < 0)
        return;
    VTRACK(v) = -1;
    VNOTE(v) = -1;
    held[t] &= ~(1u << v);
    voice_on[v] = 0;
}

static u32 smooth(u32 mirror)
{
    CHECK(mirror == MIRROR, "the smoothing's argument %#x", mirror);
    smooth_calls++;
    return SM_OUT;
}
static u32 row_of(u32 k, s32 m, s32 f) { (void)m; (void)f; return k < 100 ? k + 1 : 0; }
static s32 swing_amount(void) { return swing_v; }
static u32 conv(u32 t, u32 id) { return 1000 + 200 * t + id; }

/* the model's descriptor ranges: k 30 and 31 lists, k 50 bipolar, the rest 0..127 */
static int slidable(u32 k)
{
    return k >= 1 && k < 100 && k != 4 && k != 12 && k != 20 && k != 93 && k != 94 && k != 30 && k != 31;
}
static void desc(u32 k, s32 lo, s32 hi)
{
    M32(DESC + 0x3c * (k + 1) + 8) = (u32)(lo * 256);
    M32(DESC + 0x3c * (k + 1) + 0xc) = (u32)(hi * 256);
}

static u32 alloc_voice(s32 t)
{
    u32 r;
    if (!poly[t])
        for (r = 0; r < NROW; r++)
            if (OWNER(r) == (u32)t && VTRACK(r) < 0)
                return r;
    for (r = 0; r < NROW; r++)
        if (VTRACK(r) < 0 && OWNER(r) == 0xffffffffu)
            return r;
    for (r = 0; r < NROW; r++)
        if (VTRACK(r) < 0)
            return r;
    abort();
}

/* the handler takes a trig: a mono track's held voice cut, a voice, the set into its row, the locks */
static void play(u32 ev, u32 time)
{
    s32 t = (s32)M32(ev + 0x10), step = (s32)M32(ev + 0x64);
    u32 set = M32(ev + 0x3c), locks = M32(ev + 0x54), n = M32(locks + 8), v, r, k, i;
    s16 val[NK + 1];
    for (k = 0; k <= NK; k++)
        val[k] = SWORD(set, k);
    for (i = 0; i < n; i++)
        val[(u16)MS16(locks + 0x14 + 8 * i)] = MS16(locks + 0x16 + 8 * i);
    if (armed[t] == step) {                 /* a slide moved toward this trig: nothing left to change */
        CHECK(first_t1[t] == time, "track %d step %d: the slide first aimed for %#x, the trig is at %#x", t, step,
              first_t1[t], time);
        for (r = 0; r < NROW; r++)
            if (OWNER(r) == (u32)t) {
                for (k = 0; k < NK; k++)
                    if (slidable(k))
                        CHECK(RAW(r, k) == val[k], "track %d step %d row %u k %u: %d at the trig, it sets %d", t,
                              step, r, k, RAW(r, k), val[k]);
                n_arr++;
            }
        armed_n[step]++;
    }
    armed[t] = -1;
    if (!poly[t])
        for (v = 0; v < NROW; v++)
            if ((held[t] >> v) & 1)
                ts_gateoff(v);              /* the stock cuts it (0x4002b1b6, 0x4002b06e) */
    v = alloc_voice(t);
    OWNER(v) = (u32)t;
    VTRACK(v) = (s8)t;
    VNOTE(v) = 60;
    held[t] |= 1u << v;
    voice_on[v] = 1;
    gate_end[v] = time + M32(ev + 0x64 - 4);
    for (k = 0; k <= NK; k++)               /* 0x4002549c */
        RAW(v, k) = SWORD(set, k);
    ROW_MACH(v) = MS8(set + 0xde);
    ROW_FILT(v) = MS8(set + 0xdf);
    for (i = 0; i < n; i++) {               /* 0x400db1f6 */
        k = (u16)MS16(locks + 0x14 + 8 * i);
        RAW(v, k) = val[k];
        STATE(v, k) = (u32)(u16)val[k] << 16;
    }
    for (k = 0; k <= NK; k++)
        trig_val[v][k] = RAW(v, k);
}

/* every slide on: on its line, in every row its track owns; rows of tracks not sliding: their trig's values */
static void check_slides(u32 now)
{
    s32 t;
    u32 r, i;
    for (t = 0; t < NT; t++) {
        struct ts_slide *sl = &ts_sl[t];
        if (!sl->on)
            continue;
        if (armed[t] != sl->step)
            first_t1[t] = sl->t1;
        armed[t] = sl->step;
        CHECK(sl->n > 0 && (s32)(sl->t1 - sl->t0) > 0, "track %d: a slide of %u moves over %d", t, sl->n,
              (s32)(sl->t1 - sl->t0));
        for (i = 0; i < sl->n; i++) {
            CHECK(slidable(sl->m[i].k), "track %d: k %u slides", t, sl->m[i].k);
            if (!sl->m[i].locked)
                CHECK(sl->m[i].v1 == SWORD(sl->set, sl->m[i].k), "track %d k %u: aims for %d, the sound has %d",
                      t, sl->m[i].k, sl->m[i].v1, SWORD(sl->set, sl->m[i].k));
        }
        for (r = 0; r < NROW; r++) {
            if (OWNER(r) != (u32)t)
                continue;
            for (i = 0; i < sl->n; i++) {
                double a = sl->m[i].v0, b = sl->m[i].v1;
                double want = a + (b - a) * (double)(now - sl->t0) / (double)(sl->t1 - sl->t0);
                s16 got = RAW(r, sl->m[i].k);
                double tol = 2 + (b > a ? b - a : a - b) / 4096;
                CHECK(got >= want - tol && got <= want + tol, "track %d row %u k %u: %d, the line says %.1f", t, r,
                      sl->m[i].k, got, want);
                CHECK(STATE(r, sl->m[i].k) == (u32)(u16)got << 16, "track %d row %u k %u: state %#x for %d", t, r,
                      sl->m[i].k, STATE(r, sl->m[i].k), got);
                n_lin++;
            }
        }
    }
    for (r = 0; r < NROW; r++) {
        u32 o = OWNER(r), k;
        if (o < NT && !slide_ever[o])
            for (k = 0; k <= NK; k++)
                CHECK(RAW(r, k) == trig_val[r][k], "row %u (track %u, SLIDE off) k %u moved: %d, its trig set %d",
                      r, o, k, RAW(r, k), trig_val[r][k]);
    }
}

static void block(void)
{
    u32 now = CLOCK, due = now + 2 * RATE, v, ret, calls = smooth_calls;
    for (v = 0; v < NROW; v++)
        if (voice_on[v] && (s32)(gate_end[v] - now) <= 0)
            ts_gateoff(v);
    while (nq && (s32)(q_[0].time - due) < 0) {
        u32 ev = q_[0].ev, time = q_[0].time;
        memmove(q_, q_ + 1, (size_t)--nq * sizeof q_[0]);
        play(ev, time);
    }
    ret = ts_block(MIRROR);
    CHECK(ret == SM_OUT && smooth_calls == calls + 1, "ts_block returned %#x after %u smoothing calls", ret,
          smooth_calls - calls);
    check_slides(now);
    CLOCK = now + 2 * RATE;
    n_blocks++;
}

/* the sequencer task, once a tick: a step's event a step ahead, through ts_queue; the step counters */
static void sched_tick(void)
{
    u32 clk = (tick_n + 1) * TICK;
    s32 t;
    for (t = 0; t < NT; t++) {
        s32 tps = TICKS(SEQ_TSPEED(t));
        if (PLAYING(t) != 1)
            continue;
        if (counter_[t] == 0) {
            s32 micro = 0, swing = 0;
            u32 ev = ev_build(t, PAT, KITA, SEQ_STEP(t), 0, &micro, &swing);
            if (ev) {
                s32 m = (s32)(s16)((M32(ev + 0xc) * (u32)micro) >> 16) * 1500;
                u32 base = clk + (u32)swing + (u32)(tps - 1) * TICK, r0 = RNG0, r1 = RNG1;
                u8 pre = PRE_PREVIEW(t);
                M32(ev + 8) = base;
                if (tick_n == 0) {          /* PLAY: the first trig built before (0x400d907a), queued by play start */
                    START_STEP(t) = SEQ_STEP(t);
                    SEQ_STEP(t) = 99;       /* the scheduler's counter is not the first trig's step then */
                    ts_start(ev, base + (u32)m);
                    SEQ_STEP(t) = START_STEP(t);
                } else
                    ts_queue(ev, base + (u32)m);
                CHECK(RNG0 == r0 && RNG1 == r1 && PRE_PREVIEW(t) == pre, "track %d: ts_queue moved the generator or "
                      "the PRE state", t);
            }
        }
        if ((s32)++counter_[t] >= tps) {
            counter_[t] = 0;
            SEQ_STEP(t) = (u8)((SEQ_STEP(t) + 1) % len_);
        }
    }
    tick_n++;
}

static void run(u32 blocks)
{
    while (blocks--) {
        while ((s32)(CLOCK - tick_n * TICK) >= 0)
            sched_tick();
        block();
    }
}

static void reset(void)
{
    u32 t, r, k;
    memset(rA, 0, sizeof rA);
    memset(rB, 0, sizeof rB);
    memset(rC, 0, sizeof rC);
    memset(rD, 0, sizeof rD);
    memset(rE, 0, sizeof rE);
    memset(trig_, 0, sizeof trig_);
    memset(poly, 0, sizeof poly);
    memset(slide_ever, 0, sizeof slide_ever);
    memset(held, 0, sizeof held);
    memset(voice_on, 0, sizeof voice_on);
    memset(counter_, 0, sizeof counter_);
    memset(armed_n, 0, sizeof armed_n);
    memset(ts_exact, 0, sizeof ts_exact);
    memset(ts_next, 0, sizeof ts_next);
    memset(ts_sl, 0, sizeof ts_sl);
    memset(ts_mask, 0, sizeof ts_mask);
    ts_active = 0;
    memset(ts_map, 0, sizeof ts_map);
    nq = 0;
    tick_n = ev_n = lk_n = 0;
    len_ = 16;
    swing_v = 8;
    n_lin = n_arr = n_blocks = 0;
    for (k = 0; k < 100; k++)
        desc(k, 0, 127);
    desc(30, 0, 7);
    desc(31, 0, 7);
    desc(50, -64, 63);
    for (k = 0; k < 8; k++)
        M32(0x401f6f18 + 4 * k) = 48u >> (k < 4 ? k : 3);   /* speed 3: 6 ticks a step */
    RATE = RATE_V;
    CLOCK = 0;
    KIT = KITA;
    SEQ_PAT = PAT;
    SEQ_KIT = SEQ_KIT2 = KITA;
    MS8(PAT + 0x1dd59) = 0;
    MS16(PAT + 0x1dd54) = 16;
    MS8(PAT + 0x1dd5a) = 3;
    memset(host(PAT + 0x17e50 + 0x50a0, 0x650), 0xff, 0x650);   /* no lock lanes (as the stock's loader leaves them) */
    for (t = 0; t < NT; t++) {
        u32 set = SOUND(KITA, t);
        SEQ_TSPEED(t) = 3;
        SEQ_STEP(t) = 0;
        PLAYING(t) = 0;
        armed[t] = -1;
        MS8(set + 0xde) = 1;                    /* machine */
        MS8(set + 0xdf) = 2;                    /* filter */
        for (k = 0; k < 100; k++)
            SWORD(set, k) = (s16)((k * 0x51) & 0x3f00);
        SWORD(set, 50) = -0x2000;
    }
    MS8(POOL + 0xde) = 3;
    MS8(POOL + 0xdf) = 2;
    for (k = 0; k < 100; k++)
        SWORD(POOL, k) = 0x3000;
    for (r = 0; r < NROW; r++) {
        OWNER(r) = 0xffffffffu;
        VTRACK(r) = -1;
        VNOTE(r) = -1;
        ROW_MACH(r) = 0x7f;
    }
    RNG0 = 1;
    RNG1 = 2;
}

static void play_track(s32 t, int slide)
{
    PLAYING(t) = 1;
    SWORD(SOUND(KITA, t), K_SLIDE) = (s16)(slide ? 0x100 : 0);
    slide_ever[t] = slide;
}
static void trig(s32 t, s32 s, u32 gate_ticks)
{
    trig_[t][s].on = 1;
    trig_[t][s].gate = gate_ticks * TICK;
    MS16(PAT + 0x6a5 * (u32)t + 2 * (u32)s) |= 1;     /* the step's flags: a trig (what ts.c's scan reads) */
}
static void lock(s32 t, s32 s, u32 k, s32 v)
{
    struct trig *g = &trig_[t][s];
    g->lk[g->nl] = (s16)k;
    g->lv[g->nl++] = (s16)v;
    if (k == K_SLIDE) {
        LANE(PAT, t, K_SLIDE) = 0;              /* the track has a SLIDE lane */
        if (v >= 0x80)
            slide_ever[t] = 1;
    }
}

static int report(const char *name, int ok)
{
    printf("%-4s %s (%d blocks, %d line checks, %d arrivals)\n", ok && !fails ? "ok" : "FAIL", name, n_blocks, n_lin,
           n_arr);
    return ok && !fails;
}

int main(void)
{
    int all = 1, s, before;
    u32 k;

    /* S1: a trig on every step, half-step gates; locks on a few steps (a bipolar one, a list) */
    reset();
    play_track(0, 1);
    play_track(2, 0);                           /* a track playing beside it with SLIDE off */
    for (s = 0; s < 16; s++) {
        trig(0, s, 3);
        trig(2, s, 2);
    }
    lock(0, 1, 40, 0x6000);
    lock(0, 3, 40, 0x1000);
    lock(0, 3, 50, 0x3f00);
    lock(0, 6, 50, -0x4000);
    lock(0, 5, 30, 0x500);
    lock(0, 7, 4, 0x300);
    lock(2, 1, 40, 0x7000);
    run(32 * STEP_BLOCKS);
    /* a slide where a value changes: into 1, 2, 3, 4, 6, 7 (5 locks a list, 7 an LFO DEST: k 50 back), twice */
    all &= report("S1 a trig every step: a slide in every gap where a value changes, on the line, arriving",
                  n_arr == 12 && armed_n[1] == 2 && armed_n[5] == 0 && armed_n[7] == 2 && n_lin > 1000);

    /* S2: gaps, swing on odd steps, micro-timing: the look-ahead's estimate is the trig's time */
    fails = 0;
    reset();
    play_track(0, 1);
    trig(0, 0, 6);
    trig(0, 5, 6);
    trig(0, 8, 2);
    trig(0, 13, 6);
    trig_[0][5].swing = trig_[0][13].swing = 1;
    trig_[0][5].micro = 5;
    trig_[0][13].micro = 7;
    lock(0, 5, 40, 0x6000);
    lock(0, 8, 40, 0x1000);
    lock(0, 13, 40, 0x7f00);
    lock(0, 13, 50, 0x3000);
    run(32 * STEP_BLOCKS);
    all &= report("S2 gaps with swing and micro-timing: one line from the gate-off to the trig",
                  armed_n[5] >= 2 && armed_n[8] >= 2 && armed_n[13] >= 2 && armed_n[0] >= 1);

    /* S3: gates longer than a step: no gap, no slide */
    fails = 0;
    reset();
    play_track(0, 1);
    for (s = 0; s < 16; s++)
        trig(0, s, 7);
    lock(0, 1, 40, 0x6000);
    lock(0, 2, 40, 0x1000);
    run(32 * STEP_BLOCKS);
    all &= report("S3 gates past the next trig: no slide", n_arr == 0 && n_lin == 0);

    /* S4: a sound lock with another machine on step 2: no slide into it, none out of it */
    fails = 0;
    reset();
    play_track(0, 1);
    for (s = 0; s < 4; s++)
        trig(0, s, 3);
    trig_[0][2].pool = 1;
    lock(0, 1, 40, 0x6000);
    lock(0, 3, 40, 0x1000);
    len_ = 4;
    MS16(PAT + 0x1dd54) = 4;
    run(16 * STEP_BLOCKS);
    all &= report("S4 another machine: no slide into or out of it",
                  armed_n[2] == 0 && armed_n[3] == 0 && armed_n[1] >= 3 && armed_n[0] >= 3);

    /* S5: SLIDE off; S6: SLIDE on but a MIDI track: nothing published, no slide */
    fails = 0;
    reset();
    play_track(0, 0);
    for (s = 0; s < 16; s++)
        trig(0, s, 3);
    lock(0, 1, 40, 0x6000);
    run(8 * STEP_BLOCKS);
    all &= report("S5 SLIDE off: nothing", ts_exact[0].gen == 0 && n_lin == 0);
    fails = 0;
    reset();
    play_track(0, 1);
    MS16(KITA + 0x5cda) = 1;
    for (s = 0; s < 16; s++)
        trig(0, s, 3);
    lock(0, 1, 40, 0x6000);
    run(8 * STEP_BLOCKS);
    all &= report("S6 a MIDI track: nothing", ts_exact[0].gen == 0 && n_lin == 0);

    /* S7: a poly track, notes overlapping: no slide; not overlapping: slides, every row it owns */
    fails = 0;
    reset();
    play_track(0, 1);
    poly[0] = 1;
    for (s = 0; s < 16; s++)
        trig(0, s, 8);
    lock(0, 1, 40, 0x6000);
    run(16 * STEP_BLOCKS);
    all &= report("S7a poly, overlapping: no slide", n_lin == 0 && n_arr == 0);
    fails = 0;
    reset();
    play_track(0, 1);
    poly[0] = 1;
    for (s = 0; s < 16; s++)
        trig(0, s, 2);
    lock(0, 1, 40, 0x6000);
    lock(0, 9, 50, 0x3f00);
    run(16 * STEP_BLOCKS);
    all &= report("S7b poly, gaps: slides in every row it owns", n_arr > 16 && n_lin > 500);

    /* S8: a knob turned during a slide: an unlocked target follows the sound */
    fails = 0;
    reset();
    play_track(0, 1);
    for (s = 0; s < 16; s++)
        trig(0, s, 2);
    lock(0, 1, 40, 0x6000);                     /* step 1 -> step 2 slides k 40 back to the sound's value */
    run(500);                                   /* step 1 at block 375, its gate off at 438, step 2 at 563 */
    before = ts_sl[0].on && ts_sl[0].step == 2;
    SWORD(SOUND(KITA, 0), 40) = 0x3a00;
    run(4 * STEP_BLOCKS);
    all &= report("S8 a knob turned during a slide: it lands on the new value", before && armed_n[2] >= 1);

    /* S9: the sequencer stopped during a slide: it stops there, and no gate-off starts one */
    fails = 0;
    reset();
    play_track(0, 1);
    trig(0, 0, 2);
    trig(0, 4, 2);
    lock(0, 4, 40, 0x7f00);
    run(STEP_BLOCKS + 3 * STEP_BLOCKS / 6 + 60);
    before = ts_sl[0].on;
    PLAYING(0) = 0;
    run(1);
    {
        s16 v = RAW(0, 40);
        run(STEP_BLOCKS);
        all &= report("S9 stopped: the slide stops, nothing moves after",
                      before && !ts_sl[0].on && RAW(0, 40) == v);
    }

    /* S10: a trig nudged early (the look-ahead's build skips it): the slide turns to it when it is queued */
    fails = 0;
    reset();
    play_track(0, 1);
    trig(0, 0, 3);
    trig(0, 4, 3);
    trig(0, 8, 3);
    trig_[0][4].micro = -3;
    lock(0, 4, 40, 0x6000);
    lock(0, 8, 40, 0x1000);
    run(32 * STEP_BLOCKS);
    /* its first aim is step 8 (the look-ahead's), so the arrival's "first aimed" check does not apply to step 4:
       armed[] follows the step, so first_t1 is re-taken when the slide turns to step 4 */
    all &= report("S10 a trig nudged early: the slide turns to it", armed_n[4] >= 2 && armed_n[8] >= 2);

    /* S11: the trig after the target queued before the target plays (the target late): no turn */
    fails = 0;
    reset();
    play_track(0, 1);
    for (s = 0; s < 16; s++)
        trig(0, s, 3);
    trig_[0][1].micro = 20;                     /* 5 ticks late, a short gate: a gap before step 2 */
    trig_[0][1].gate = TICK / 2;
    lock(0, 1, 40, 0x6000);
    lock(0, 2, 40, 0x1000);
    run(32 * STEP_BLOCKS);
    all &= report("S11 the target late, the next one queued first: it still lands on the target",
                  armed_n[1] >= 2 && armed_n[2] >= 2);

    /* S12: a step locking every parameter, the next one none */
    fails = 0;
    reset();
    play_track(0, 1);
    for (s = 0; s < 16; s++)
        trig(0, s, 3);
    for (k = 1; k < 100; k++)
        lock(0, 1, k, (s32)((k * 0x137) & 0x7f00) - (k == 50 ? 0x4000 : 0));
    run(32 * STEP_BLOCKS);
    all &= report("S12 a step locking all 99: every one lands", armed_n[1] >= 2 && armed_n[2] >= 2);

    /* S13: SLIDE turned off during a slide: it stops */
    fails = 0;
    reset();
    play_track(0, 1);
    trig(0, 0, 2);
    trig(0, 4, 2);
    lock(0, 4, 40, 0x7f00);
    run(STEP_BLOCKS + 3 * STEP_BLOCKS / 6 + 60);
    before = ts_sl[0].on;
    SWORD(SOUND(KITA, 0), K_SLIDE) = 0;
    run(2);
    all &= report("S13 SLIDE off during a slide: it stops", before && !ts_sl[0].on);

    /* S15: SLIDE off, step 5 locks it on (and k 40): the slide into 5 only, none out of it (step 6 follows the sound) */
    fails = 0;
    reset();
    play_track(0, 0);
    for (s = 0; s < 16; s++)
        trig(0, s, 3);
    lock(0, 5, 40, 0x6000);
    lock(0, 5, K_SLIDE, 0x100);
    run(32 * STEP_BLOCKS);
    all &= report("S15 SLIDE off, locked on at step 5: the slide into 5 only",
                  armed_n[5] == 2 && armed_n[6] == 0 && n_arr == 2 && ts_exact[0].gen > 0);

    /* S16: SLIDE on, step 5 locks it off: no slide into 5, the slide out of it as the sound says */
    fails = 0;
    reset();
    play_track(0, 1);
    for (s = 0; s < 16; s++)
        trig(0, s, 3);
    lock(0, 5, 40, 0x6000);
    lock(0, 5, K_SLIDE, 0);
    run(32 * STEP_BLOCKS);
    all &= report("S16 SLIDE on, locked off at step 5: none into 5, the one out of it", armed_n[5] == 0 && armed_n[6] == 2);

    /* S17: a track that remaps its steps: the look-ahead builds every step (no flag scan); S2 again */
    fails = 0;
    reset();
    play_track(0, 1);
    M8(PAT + 0x5a4) = 16;
    trig(0, 0, 6);
    trig(0, 5, 6);
    trig(0, 8, 2);
    trig_[0][5].swing = 1;
    trig_[0][5].micro = 5;
    lock(0, 5, 40, 0x6000);
    lock(0, 8, 40, 0x1000);
    run(32 * STEP_BLOCKS);
    all &= report("S17 steps remapped: every step built, the same slides", armed_n[5] >= 2 && armed_n[8] >= 2);

    /* S18: the pattern's lock storage helpers */
    fails = 0;
    reset();
    for (k = 0; k < 100; k++)
        M32(MAP + 4 * k) = 0x40 + k;                   /* a stand-in map */
    M32(MAP + 400) = 0;                                /* the reverse map's first word, past the stock map's end */
    CHECK(ts_map_fill() == ADDR(ts_map), "ts_map_fill's address");
    for (k = 0; k < 100; k++)
        CHECK(ts_map[k] == 0x40 + k, "ts_map[%u] = %#x", k, ts_map[k]);
    CHECK(ts_map[100] == ID_SLIDE, "ts_map[100] = %u", ts_map[100]);
    M32(MAP + 4 * 7) = 0x99;                           /* filled once */
    ts_map_fill();
    CHECK(ts_map[7] == 0x47, "ts_map refilled");
    CHECK(ts_lconv(0, ID_SLIDE) == K_SLIDE && ts_lconv(15, ID_SLIDE) == K_SLIDE, "107 -> SLIDE on tracks 0..15");
    CHECK(ts_lconv(16, ID_SLIDE) == conv(16, ID_SLIDE) && ts_lconv(3, 5) == conv(3, 5), "the rest through 0x400dce24");
    all &= report("S18 lock storage: the map with k 100 -> 107, filled once; 107 -> SLIDE back", 1);

    /* S14: the progress */
    fails = 0;
    CHECK(ts_progress(0, 100) == 0 && ts_progress(50, 100) == FULL / 2 && ts_progress(100, 100) == FULL
          && ts_progress(5, 0) == FULL && ts_progress(0x7fffffffu, 0xffffffffu) >= FULL / 2 - 2
          && ts_progress(0x7fffffffu, 0xffffffffu) <= FULL / 2, "ts_progress");
    all &= report("S14 progress", 1);

    printf(all ? "ALL PASS\n" : "SOME FAILED\n");
    return all ? 0 : 1;
}
