/* SPDX-License-Identifier: GPL-2.0-or-later */
/* spring-names (Digitone II OS 1.12): the reverb page as the spring tank of dn2-cfw's SP01 build
 * (github.com/naturarum/dn2-cfw, notes/SP01-spring-pack.md), whose DSP runs SPNG, a spring reverb, in the reverb slot
 * (notes/T5.5-spring-reverb.md). The stock reverb's page (SEND FX 2, page record 18), knob by knob; every knob keeps
 * its row, k, MIDI CC, NRPN, storage and DSP frame word:
 *
 *   A PRE   row 0x7b k 41  "Pre-delay"     stock names; new-kit default 16 -> 0
 *   B DEC   row 0x7c k 42  "Decay Time"    stock names; value text the plain number (the stock one shows value + 1
 *                                           and "INF" at 127, and the spring's 127 is 16 s, not endless); new-kit
 *                                           default 31 -> 64 (2.2 s)
 *   C MODE  row 0x7d k 43  "Spring Mode"   was FREQ "FB Shelving Freq"; the spring's character (default 64, as stock)
 *   D DWEL  row 0x7e k 44  "Dwell"         was GAIN "FB Shelving Gain"; drive into the tank; new-kit default 50 -> 64
 *   E HPF   row 0x7f k 45  "Input HPF"     stock names (the tank's input high-pass); new-kit default 32 -> 0
 *   F TONE  row 0x80 k 46  "Tone"          was LPF "Input LPF" (the spring's is in the loop); new-kit default 96 -> 127
 *   G WDTH  row 0x83 k 48  "Width"         the stock row "Reverb FX Routing" (0..1 Pre/Post, on no page, no CC,
 *                                           stored as kit id 24, sent in frame word 0xa28, which the stock DSP keeps
 *                                           only as a flag nobody reads): now 0..127, a plain number, the chorus
 *                                           WDTH's picture, and placed on the empty G slot; new-kit default 96. Every
 *                                           existing kit holds 1 there (its stock default).
 *   H (empty), LEVEL VOL: stock.
 * C and D are drawn as knobs: the stock page draws them as one shelving-filter picture, which MODE and DWEL are not.
 * E and F keep the stock filter picture (a high-pass corner, then the low-pass), which is what HPF and TONE do.
 * Names repoint to strings of this mod; no stock string is edited (HPF, LPF, FREQ, GAIN, REV are shared with other
 * rows).
 *
 * Data (mod.json's sites, written by dn2-cfw's scripts/sp01_make_mod.py, which checks every stock value first):
 * - `ptr` sites on descriptor rows (0x401f83a4 + 0x3c * row: +0x28 long name, +0x30 short label);
 * - `bytes` sites: new-kit defaults (+0x10, value << 8), row 0x83's maximum (+0x0c) and the two formatters (+0x34:
 *   the stock plain number 0x400e3030 for DEC and WDTH);
 * - `keep2` on the `pea` that names page 18 at boot ("Spring", the title: "Spring (2/3)");
 * - two `bytes` sites in FxPageView::vslot04 (0x4006f5e4), the SEND FX pages' draw: its switch on the knob's row id
 *   (0x4006fb12, a table of 16-bit offsets at 0x4006fb2c, rows 0x75..0x80) sends rows 0x7d and 0x7e to the plain
 *   knob's case (offset 0xa8, as rows 0x77..0x7c) instead of keeping their values for the shelf picture; and the page
 *   id that picture is drawn for (0x4006f8e8 `moveq #18,%d1`) becomes 127, a page that does not exist, so it is not
 *   drawn. The filter picture over E and F (drawn for every page but 19) is untouched.
 *
 * Code: snm_tick, on ev_tick until done. The page records and the display table are built in RAM by the static
 * initializer 0x400c2c4e at boot, so a static edit cannot place a row id (the stock instruction at 0x400c9fde is a
 * clr.l; dn2-cfw notes/T6.1 section 4.3, the path fx-echo-dn2 took for the delay page on SC01). On the first tick that
 * finds them built, after checking every value it will replace against stock 1.12:
 *   1. WDTH's encoder and picture: row 0x83's display entry +0x04..+0x23 (steps per detent, the acceleration curve,
 *      the picture's std::function) <- the chorus WDTH's (row 0x6c: the two circles); its flags (+0x00) 0xc -> 0x8
 *      (bit 2 kept the label while the stock two-state row turned and would hide WDTH's value; bit 3, never
 *      p-lockable, kept); the std::functions at +0x24 and +0x34 stay row 0x83's own;
 *   2. page 18's knob G (0x42433f38, 0 in stock) <- 0x83, last, so the page never shows the row before it is ready.
 * If anything is not as stock 1.12 builds it, nothing is written and snm_state is -1 (G then stays empty).
 */
typedef unsigned int u32;

#define DISP 0x4243425cu            /* display table, 0x44 bytes per row (accessor 0x400c257c) */
#define DROW 0x44
#define PAGES 0x42433c00u           /* page records, 0x2c bytes each (accessor 0x400c25d8) */
#define PAGE_REVERB 18

/* stock 1.12 values the tick checks before it writes */
#define INV_ROUTING 0x400bc72eu     /* row 0x83's picture: PRE FX / POST FX */
#define INV_WIDTH   0x400bc490u     /* row 0x6c's: the chorus WDTH's two circles */
#define CURVE_KNOB  0x44508e40u     /* row 0x6c's acceleration curve (the plain knobs') */

struct pagerec {                    /* 0x2c bytes */
    const char *sname, *lname;      /* COW std::string data pointers */
    int rows[8];                    /* knobs A..H, 0 = empty */
    int level;
};

/* Widths in the 4 x 5 label font: MODE 20 px, DWEL 19, TONE 19, WDTH 19 (stock), as FDBK; in the title font "Spring
 * (2/3)" is shorter than "Reverb (2/3)". */
const char snm_spring[] = "Spring";               /* SEND FX 2 title: "Spring (2/3)" */
const char snm_mode[] = "Spring Mode", snm_mode_s[] = "MODE";
const char snm_dwel[] = "Dwell", snm_dwel_s[] = "DWEL";
const char snm_tone[] = "Tone", snm_tone_s[] = "TONE";
const char snm_wdth[] = "Width", snm_wdth_s[] = "WDTH";

/* 0 = waiting for the tables, 1 = done, -1 = refused (not stock); for the emulator test */
int snm_state;
int snm_ticks;

static volatile u32 *disp(int row)
{
    return (volatile u32 *)(DISP + DROW * row);
}

#define COPY(to, from, w) ((to)[w] = (from)[w])

void snm_tick(void *ctrl)
{
    volatile struct pagerec *p = (volatile struct pagerec *)(PAGES + 0x2c * PAGE_REVERB);
    volatile u32 *wdth = disp(0x83), *chw = disp(0x6c);
    (void)ctrl;
    if (snm_state)
        return;
    snm_ticks++;
    if (!p->rows[0] || !wdth[8] || !chw[8])
        return;                                 /* not built yet: try again on the next tick */
    if (p->rows[0] != 0x7b || p->rows[1] != 0x7c || p->rows[2] != 0x7d || p->rows[3] != 0x7e
        || p->rows[4] != 0x7f || p->rows[5] != 0x80 || p->rows[6] != 0 || p->rows[7] != 0
        || p->level != 0x82
        || wdth[8] != INV_ROUTING || wdth[0] != 0xc || chw[8] != INV_WIDTH || chw[4] != CURVE_KNOB) {
        snm_state = -1;
        return;
    }
    /* word by word, unrolled (fx-echo-dn2: as a loop m68k-elf-gcc made `move.l (a0)+,(off,a0)`, which GCC and
       unicorn/QEMU read differently) */
    COPY(wdth, chw, 1); COPY(wdth, chw, 2);     /* WDTH: the knob's steps, curve and picture (+0x04..+0x23) */
    COPY(wdth, chw, 3); COPY(wdth, chw, 4);
    COPY(wdth, chw, 5); COPY(wdth, chw, 6);
    COPY(wdth, chw, 7); COPY(wdth, chw, 8);
    wdth[0] = wdth[0] & ~4u;                    /* its value while it turns (bit 3, never p-lockable, kept) */
    p->rows[6] = 0x83;                          /* WDTH on knob G */
    snm_state = 1;
}
