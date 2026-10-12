/* td_glue.c - T3.8 milestone 3: the tape delay (fx/tapedelay.c) in the stock delay slot.
 *
 * The hook stubs (tools/dspsplice/stub.py) call:
 *   td_init()                 from the effect init 0x1c1445, in place of the stock delay init 0xb848cb
 *                             (which is NOT run: it would clear our state, which lives in its memory;
 *                             what it writes is its own DDR state, its parameter struct, which the
 *                             render's unpack rewrites every block before the FX bus runs, and stack;
 *                             notes/T3.8 section 9);
 *   td_process(params, io)    from the FX bus 0x1c1738, in place of the stock delay 0xb8482c:
 *                             params = the delay parameter struct 0x257934 (+0 TIME knob/127, +4 X 0/1,
 *                             +8 WID knob/128, +0xc FDBK knob/128, +0x10 HPF knob/127, +0x14 LPF knob/127,
 *                             T3.4 section 1), io = the bus's I/O descriptor (its +0x14 is the tempo, BPM).
 * The tape delay writes the wet signal only, like the stock delay; the bus adds VOL and the REV send.
 *
 * Knob map (notes/T3.8 section 9): TIME -> TIME (the same tempo-synced law), FDBK -> FDBK (the tape's
 * own law: 0..110 %), HPF -> HPF, LPF -> LPF (the tape's resonant loop filters), WID -> WID (0 mono,
 * 64 stereo, 127 ping-pong), X on -> WID 127 (full ping-pong). Fixed: RES 32, WOW 20, FLUT 20, AGE 24,
 * DRIV 32 (+3.9 dB).
 *
 * State: one TdSlot at the start of the stock delay's DDR state (0x8005912c, 4,194,908 bytes), which the
 * stock boot stream already zero-fills. Same selcc rules as flg_glue.c.
 */
#include <stdint.h>
#include "tapedelay.h"
#include "fxio.h"

#define TD_MAGIC 0x54444531u        /* "TDE1" */
#define TD_REGION_BYTES 4194908     /* 0x8005912c-0x80459388 (T3.2 2.6, T3.4 3.2) */
#define TD_SEED 1u                  /* the seed fx/render.c uses: the same noise as the A/B renders */

typedef struct {
    uint32_t magic;
    uint32_t fresh;                 /* 1 until the first block with a valid tempo has snapped */
    uint32_t blocks;
    float last[7];                  /* TIME, X, WID, FDBK, HPF, LPF as applied, and the tempo */
    TapeDelay td;
} TdSlot;

typedef char td_slot_fits[(sizeof(TdSlot) <= TD_REGION_BYTES) ? 1 : -1];

#ifdef TD_NATIVE
static TdSlot td_native_slot;
#define SLOT (&td_native_slot)
uint32_t td_slot_size(void) { return (uint32_t)sizeof(TdSlot); }
#else
#define SLOT ((TdSlot *)0x8005912c)
#endif

void td_init(void)
{
    TdSlot *t = SLOT;
    TapeDelay *s = &t->td;
    tapedelay_init(s, TD_SEED);
    tapedelay_set(s, TD_RES, 32.0f);
    tapedelay_set(s, TD_WOW, 20.0f);
    tapedelay_set(s, TD_FLUT, 20.0f);
    tapedelay_set(s, TD_AGE, 24.0f);
    tapedelay_set(s, TD_DRIV, 32.0f);
    tapedelay_snap(s);
    t->last[0] = -1.0f;
    t->last[1] = -1.0f;
    t->last[2] = -1.0f;
    t->last[3] = -1.0f;
    t->last[4] = -1.0f;
    t->last[5] = -1.0f;
    t->last[6] = -1.0f;
    t->fresh = 1;
    t->blocks = 0;
    t->magic = TD_MAGIC;
}

static float td_clamp127(float v)
{
    if (!(v >= 0.0f))
        v = 0.0f;
    if (v > 127.0f)
        v = 127.0f;
    return v;
}

void td_process(const float *p, const FxIo *io)
{
    TdSlot *t = SLOT;
    TapeDelay *s;
    float v;
    float w;
    float bpm;
    float **in;
    float **out;
    if (t->magic != TD_MAGIC)
        td_init();
    s = &t->td;
    v = td_clamp127(p[0] * 127.0f);
    if (v != t->last[0]) {
        tapedelay_set(s, TD_TIME, v);
        t->last[0] = v;
    }
    w = td_clamp127(p[2] * 128.0f);
    if (p[1] >= 0.5f)
        w = 127.0f;
    if (w != t->last[2]) {
        tapedelay_set(s, TD_WID, w);
        t->last[2] = w;
    }
    v = td_clamp127(p[3] * 128.0f);
    if (v != t->last[3]) {
        tapedelay_set(s, TD_FDBK, v);
        t->last[3] = v;
    }
    v = td_clamp127(p[4] * 127.0f);
    if (v != t->last[4]) {
        tapedelay_set(s, TD_HPF, v);
        t->last[4] = v;
    }
    v = td_clamp127(p[5] * 127.0f);
    if (v != t->last[5]) {
        tapedelay_set(s, TD_LPF, v);
        t->last[5] = v;
    }
    bpm = io->bpm;
    if (bpm >= 30.0f) {
        if (bpm != t->last[6]) {
            tapedelay_set_bpm(s, bpm);
            t->last[6] = bpm;
        }
        if (t->fresh) {
            tapedelay_snap(s);
            t->fresh = 0;
        }
    }
    in = io->in;
    out = io->out;
    tapedelay_process(s, in[0], in[1], out[0], out[1], io->n);
    t->blocks = t->blocks + 1;
}
