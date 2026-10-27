/* SPDX-License-Identifier: GPL-2.0-or-later */
/* lfo-dejavu (Digitone II OS 1.12): nine LFO waveforms after the stock seven, a stored random-value loop in the
 * spirit of the "deja vu" loops of Emilie Gillet's Marbles (her design; no code of it here), with
 * the behaviour and the names of Kari Gardarsson's Digitakt II "Musical WAVE / Deja Vu" (digitakt-ii-research
 * v0.3.0, whose release credits Bezronczek's Syntakt Firmware Workbench for the behaviour). This is our own
 * implementation for the Digitone II's LFO code, written from that published behaviour only. For dn2-cfw
 * (github.com/naturarum/dn2-cfw, notes/T2.8-dejavu-lfo.md). EXPERIMENT: emulator-tested only.
 *
 *   WAVE 7  DV-F  a free-running loop of stored random values
 *   WAVE 8  DV-T  the same; a trig (the trig's LFO.T) restarts it at its first value, the values are kept
 *   WAVE 9..15  PMAJ PMIN HIRA WTON DORI LYDI OCTA: the DV-F loop; when DEST is the machine's tuning (WaveTone TUN1,
 *               TUN2, MAST; FM Drum and Swarmer TUNE; FM Tone PITCH) the result is rounded to the nearest note of
 *               the scale, counted in semitones from TUNE 0; on any other DEST these are DV-F
 *   on these waves MODE is the loop's length: FREE 2, TRIG 4, HOLD 8, ONE 16, HALF 32 values (MODE's words stay
 *   the stock ones; its trig modes do not apply), and SPH is the mutation: at each step the visited value is
 *     SPH 0..63    replaced by a new random value with probability ((64 - SPH) / 64)^2 (0: always)
 *     SPH 64       kept: the same values in the same order (the loop still steps)
 *     SPH 65..127  swapped with a random value of the loop with probability ((SPH - 64) / 63)^2 (127: always; the
 *                  swap may pick the same place)
 *   SPD and MULT set the rate as on every wave: the loop steps 16 times per LFO cycle, as the stock RAND takes a new
 *   value 16 times per cycle (SPD x MULT = 128: one value per 16th note); FADE and DEP are the stock ones.
 *
 * Where it runs (notes/T2.8-dejavu-lfo.md section 1): the LFOs are computed on the ColdFire, not on the DSP.
 *   A  0x40137a06, called by the frame handler (vector 191) once per frame, 1,500 a second: 16 voice rows x 3
 *      LFOs, the parameters read from the smoothed rows 0x800068e4 + 0xca r (k at +0x22 + 2k), the state in
 *      0x44640c18 + 0x78 r + 0x28 l (40 bytes: +0 phase, a cycle of 1,382,400,000; +0x26 and +0x27 never written
 *      by stock code), the result added into row[DEST] (clamped 0..0x7f00); a new voice copies its row's LFO state
 *      from the track's other voice (the 40 bytes, so our +0x26/+0x27 go with it).
 *   B  0x401376bc, MIDI tracks, called per track by the MIDI task: the same logic, its own state at 0x44640498
 *      (+0x26 is B's hold flag), a cycle of 21,600,000.
 * Both clamp WAVE to 0..6 and take the waveform from the table 0x4020b7f8 (TRI SINE SQR SAW EXPO RAMP; RAND
 * has its own code): the clamp goes to 15, the table becomes ours (the stock six, then dv_wave_*_s for 7..15),
 * and MODE reads as FREE on the added waves (dv.s). The value text of rows 0x4f, 0x59, 0x63 (LFO1-3 WAVE) is
 * dv_fmt, their maximum 15. Everything else (the page's glyphs clamp to RAND's, SPH and SLEW: SLEW only at
 * WAVE 6) is stock.
 *
 * State: the loop's values belong to the track (16 tracks x 3 LFOs, so every voice of a track reads the same
 * loop), the read position to the voice row (the stock record's free bytes +0x26/+0x27: copied with the stock
 * state when a voice changes hands); B keeps both per track. Nothing is saved: the loops are made at the first use
 * after power-on. The generator is xorshift32, one state for A (interrupt level) and one for B (the MIDI task);
 * each is seeded at its first use from a constant and the frame clock 0x80005398 (so another loop after each
 * power-on), and a test can set dv_rng_a / dv_rng_b beforehand for a fixed sequence.
 */
typedef unsigned long u32;
typedef long s32;
typedef short s16;
typedef unsigned short u16;
typedef unsigned char u8;

#define SPRINTF   ((int (*)(char *, const char *, ...))0x40000e82)
#define FMT_WAVE  ((int (*)(int, char *))0x400e3610)    /* the stock WAVE text: TRI .. RAND, ERR past 6 */
#define OWNER     ((volatile u32 *)0x80005308)          /* the track that owns voice row r */
#define MACHINE   ((volatile u8 *)0x80007670)           /* the frame's copy of the machine records, 0x99 per row */
#define CLOCK     ((volatile u32 *)0x80005398)          /* the frame clock (+2 x 120 x BPM a frame) */
#define A_STEP    86400000u                             /* 1,382,400,000 / 16 */
#define B_STEP    1350000u                              /* 21,600,000 / 16 */

#define NV 32                                           /* values stored per loop */

struct loop {
    s16 v[NV];
    u32 stamp;          /* dv_frame of the last mutation (A: one mutation per step, however many voices) */
    u8 mpos;            /* the place it was made at */
    u8 init;            /* 1: filled */
    u8 pad[2];
};

struct loop dv_loop_a[48] __attribute__((aligned(4)));  /* A: track t, LFO l at 3 t + l */
struct loop dv_loop_b[48] __attribute__((aligned(4)));  /* B: the same */
u8 dv_pos_b[48][2] __attribute__((aligned(4)));         /* B: read position, step */
u32 dv_rng_a, dv_rng_b;                                 /* 0: not seeded yet */
u32 dv_frame;                                           /* frames: +1 per call of A (dv.s, dv_frame_hook) */

static u32 rnd(u32 *s)
{
    u32 x = *s;
    if (!x) {
        x = 0x6d2b79f5u ^ *CLOCK;
        if (!x)
            x = 0x6d2b79f5u;
    }
    x ^= x << 13;
    x ^= x >> 17;
    x ^= x << 5;
    *s = x;
    return x;
}

/* the visited value's mutation (SPH as above) */
static void mutate(struct loop *lp, int p, int len, int sph, u32 *rng)
{
    u32 d, prob;
    if (sph < 64) {
        d = 64 - sph;
        prob = d * d * 16;                              /* of 65536: 64^2 x 16 = always */
        if ((rnd(rng) >> 16) < prob)
            lp->v[p] = (s16)(rnd(rng) >> 16);
    } else if (sph > 64) {
        d = sph - 64;
        if (d > 63)
            d = 63;
        prob = d * d * 65536u / 3969u;                  /* 63^2: always */
        if ((rnd(rng) >> 16) < prob) {
            int j = (int)(rnd(rng) >> 16) & (len - 1);
            s16 t = lp->v[p];
            lp->v[p] = lp->v[j];
            lp->v[j] = t;
        }
    }
}

/* one LFO, one frame, on a new step, a DV-T trig or the first look: -> the waveform's value (full scale +-2^31) */
static s32 dv_step(u32 *phase, u8 *ps, struct loop *lp, u32 *rng, int wave, int sph, int len, int trig, int s,
                   int dedupe)
{
    int p, visit = 0;

    if (!lp->init) {
        int i;
        for (i = 0; i < NV; i++)
            lp->v[i] = (s16)(rnd(rng) >> 16);
        lp->init = 1;
        lp->mpos = 0xff;
    }
    p = ps[0] & (NV - 1);
    if (wave == 8 && trig) {                            /* DV-T: back to the start, values kept */
        *phase = 0;
        p = 0;
        s = 0;
        visit = 1;
    } else if ((ps[1] & 0x80) && (ps[1] & 0x7f) != s) {  /* a new sixteenth of the cycle: the next value */
        p++;
        visit = 1;
    }
    p &= len - 1;
    if (visit && sph != 64 && !(dedupe && lp->mpos == p && lp->stamp == dv_frame)) {
        mutate(lp, p, len, sph, rng);
        lp->mpos = p;
        lp->stamp = dv_frame;
    }
    ps[0] = p;
    ps[1] = 0x80 | s;
    return (s32)lp->v[p] << 16;
}

/* the loop's length from MODE (0..4: 2, 4, 8, 16, 32) */
static inline int dv_len(int mode)
{
    if ((u32)mode > 4)
        mode = mode < 0 ? 0 : 4;
    return 2 << mode;
}

/* A (dv.s, dv_wave_a_s): rec = the LFO's 40-byte state (phase at +0), row = the voice row, l = the LFO (0..2).
 * Most frames are not a new step: the stored value is read and nothing else is written. */
s32 dv_wave_a(u8 *rec, int row, int wave, int sph, int mode, int trig, int l)
{
    struct loop *lp;
    u8 *ps = rec + 0x26;
    u32 s = *(u32 *)rec / A_STEP;
    int len = dv_len(mode);

    if ((u32)l > 2)
        l = 2;
    lp = &dv_loop_a[3 * (OWNER[row & 15] & 15) + l];
    if (s > 15)
        s = 15;
    if (ps[1] == (0x80 | s) && lp->init && !(trig && wave == 8))
        return (s32)lp->v[ps[0] & (len - 1)] << 16;
    return dv_step((u32 *)rec, ps, lp, &dv_rng_a, wave, sph, len, trig, s, 1);
}

/* B (dv.s, dv_wave_b_s): rec = the LFO's state in B's block, t = the track, l = the LFO (0..2) */
s32 dv_wave_b(u8 *rec, int t, int wave, int sph, int mode, int trig, int l)
{
    u32 i = 3 * ((u32)t & 15) + ((u32)l > 2 ? 2 : l);
    u8 *ps = dv_pos_b[i];
    struct loop *lp = &dv_loop_b[i];
    u32 s = *(u32 *)rec / B_STEP;
    int len = dv_len(mode);

    if (s > 15)
        s = 15;
    if (ps[1] == (0x80 | s) && lp->init && !(trig && wave == 8))
        return (s32)lp->v[ps[0] & (len - 1)] << 16;
    return dv_step((u32 *)rec, ps, lp, &dv_rng_b, wave, sph, len, trig, s, 0);
}

/* The scales as semitone classes from TUNE 0 (bit c: class c), and for each class o the scale's last note at or
 * below it (lo) and its first note above it (hi, 12 = the next octave's 0); dn2-cfw's scripts/dj01_lfocheck.py
 * checks every output against the classes read from dv_scale. */
const u16 dv_scale[7] __attribute__((aligned(4))) = {
    0x295,      /* PMAJ 0 2 4 7 9 */
    0x4a9,      /* PMIN 0 3 5 7 10 */
    0x18d,      /* HIRA 0 2 3 7 8 */
    0x555,      /* WTON 0 2 4 6 8 10 */
    0x6ad,      /* DORI 0 2 3 5 7 9 10 */
    0xad5,      /* LYDI 0 2 4 6 7 9 11 */
    0x6db,      /* OCTA 0 1 3 4 6 7 9 10 */
};
static const u8 dv_lo[7][12] = {
    {0, 0, 2, 2, 4, 4, 4, 7, 7, 9, 9, 9}, {0, 0, 0, 3, 3, 5, 5, 7, 7, 7, 10, 10}, {0, 0, 2, 3, 3, 3, 3, 7, 8, 8, 8, 8},
    {0, 0, 2, 2, 4, 4, 6, 6, 8, 8, 10, 10}, {0, 0, 2, 3, 3, 5, 5, 7, 7, 9, 10, 10},
    {0, 0, 2, 2, 4, 4, 6, 7, 7, 9, 9, 11}, {0, 1, 1, 3, 4, 4, 6, 7, 7, 9, 10, 10},
};
static const u8 dv_hi[7][12] = {
    {2, 2, 4, 4, 7, 7, 7, 9, 9, 12, 12, 12}, {3, 3, 3, 5, 5, 7, 7, 10, 10, 10, 12, 12},
    {2, 2, 3, 7, 7, 7, 7, 8, 12, 12, 12, 12}, {2, 2, 4, 4, 6, 6, 8, 8, 10, 10, 12, 12},
    {2, 2, 3, 5, 5, 7, 7, 9, 9, 10, 12, 12}, {2, 2, 4, 4, 6, 6, 7, 9, 9, 11, 11, 12},
    {1, 3, 3, 4, 6, 6, 7, 9, 9, 10, 12, 12},
};

/* the k of each machine's tuning rows, 0 = none (machine 0 FM Tone, 1 WaveTone, 2 FM Drum, 3 Swarmer) */
static const u8 dv_tune_k[4][3] = { {50, 0, 0}, {25, 31, 48}, {25, 0, 0}, {25, 0, 0} };

/* A (dv.s, dv_add_a) on WAVE 9..15: x = row[DEST] after the stock add and clamp (0..0x7f00) -> the value stored */
s32 dv_quant_a(s32 x, int wave, int dest, int row)
{
    int m = MACHINE[0x99 * (row & 15)], sc = wave - 9, s, o, base, lo, hi, q;
    s32 rel, y;

    if (m > 3 || sc < 0 || sc > 6 || dest <= 0)
        return x;
    if (dest != dv_tune_k[m][0] && dest != dv_tune_k[m][1] && dest != dv_tune_k[m][2])
        return x;
    rel = x - 0x4000;                                   /* 1/256 semitone from TUNE 0 */
    s = rel >> 8;                                       /* floor */
    o = s % 12;
    if (o < 0)
        o += 12;
    base = s - o;
    lo = base + dv_lo[sc][o];
    hi = base + dv_hi[sc][o];
    q = (hi * 256 - rel < rel - lo * 256) ? hi : lo;
    y = 0x4000 + q * 256;
    if (y > 0x7f00)
        y = 0x4000 + lo * 256;
    if (y < 0)
        y = 0x4000 + hi * 256;
    return y;
}

/* rows 0x4f, 0x59, 0x63 +0x34, the WAVE value text, called as the stock ones: (value, out) -> what sprintf returns */
static const char n7[] = "DV-F", n8[] = "DV-T", n9[] = "PMAJ", n10[] = "PMIN", n11[] = "HIRA", n12[] = "WTON",
                  n13[] = "DORI", n14[] = "LYDI", n15[] = "OCTA";
static const char *const dv_names[9] __attribute__((aligned(4))) = { n7, n8, n9, n10, n11, n12, n13, n14, n15 };

int dv_fmt(int value, char *out)
{
    int v = value >> 8;
    if (v >= 7 && v <= 15)
        return SPRINTF(out, "%s", dv_names[v - 7]);
    return FMT_WAVE(value, out);
}
