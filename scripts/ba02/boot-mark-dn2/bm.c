/* SPDX-License-Identifier: GPL-2.0-or-later */
/* boot-mark (Digitone II OS 1.12): the DIGIWERK mark in the start-up animation, in place of Elektron's logo. The
 * static screen before it (Elektron's logo and the OS version) is untouched.
 *
 * The intro, FUN_400d39ea, draws into a 128 x 64 source Bitmap (0x42c4667c) at its first frame: it clears it and
 * blits the logo, the Bitmap at 0x44647008, at (48, 20) (0x400d3ac4, jsr 0x40115adc); the frames after warp the source
 * onto the panel through an offset table. The site's jsr comes here with the same five arguments (the source, the
 * logo, x, y, the mode), and the mark goes into the source with the stock setPixel (0x40113e70: bitmap, x, y, value;
 * it checks the bounds), so the stock animation animates it. (dn2_firmware_explore found the intro's structure on
 * OS 1.11, the same code here 0x1000 later; this is our own code.) For dn2-cfw (notes/BA02-base-0.2.md).
 *
 * bm_mark.h is written by dn2-cfw's scripts/ba02_make_boot.py: 64 rows of 128 pixels, one bit each, the left one
 * the high bit, our own pixel letters.
 */
typedef unsigned char u8;

#define SET_PIXEL ((void (*)(void *, int, int, int))0x40113e70)

#include "bm_mark.h"

void bm_draw(void *src, void *logo, int x, int y, int mode)
{
    int i, j;

    (void)logo;
    (void)x;
    (void)y;
    (void)mode;
    for (j = 0; j < 64; j++)
        for (i = 0; i < 128; i++)
            if ((bm_mark[16 * j + (i >> 3)] >> (7 - (i & 7))) & 1)
                SET_PIXEL(src, i, j, 1);
}
