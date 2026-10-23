/* SPDX-License-Identifier: GPL-2.0-or-later */
/* flg_slot3.h - FL02: the chorus slot's state (FlgSlot), shared by flg_glue3.c and flg_sync.c. flg_glue2.c's (FL01)
 * layout, its unused counter named `sync` (the FL_SYNC flg_sync.c applied, 0 = free); at 0x300000 on the DSP, a
 * static of flg_glue3.c in the native and harness builds (-DFLG_NATIVE). */
#ifndef FLG_SLOT3_H
#define FLG_SLOT3_H
#include <stdint.h>
#include "flanger.h"

#define FLG_MAGIC 0x464c4733u      /* "FLG3" */
#define FLG_SLOT_BYTES 0x8400       /* the FILL block in tools/dspsplice/build.py */

typedef struct {
    uint32_t magic;                 /* FLG_MAGIC once flg_init has run */
    uint32_t fresh;                 /* 1 until the first block has applied the knobs and snapped */
    uint32_t sync;                  /* the FL_SYNC flg_spd applied, 0 = free (FL01's unused counter, the same place) */
    float last[4];                  /* the unit knobs last applied: DPTH, SPD, TIME, WDTH (0..127) */
    Flanger fl;
} FlgSlot;

/* the slot must fit the zero-filled range (a negative array size fails the build) */
typedef char flg_slot_fits[(sizeof(FlgSlot) <= FLG_SLOT_BYTES) ? 1 : -1];

#ifdef FLG_NATIVE
extern FlgSlot flg_native_slot;
#define SLOT (&flg_native_slot)
#else
#define SLOT ((FlgSlot *)0x300000)
#endif

/* flg_sync.c: SPD's parameter float p, its knob v (flg_knob) and the tempo -> FL_SPD (free) or FL_SYNC and the tempo
   (synced) */
void flg_spd(float p, float v, float bpm);

#endif
