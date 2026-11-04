/* SPDX-License-Identifier: GPL-2.0-or-later */
/* scripts/um01/test_um.c: usb-multi's sender (vendor/elekloader/examples/usb-multi-dn2/um.c) on the Mac, the stock
 * OS's state words, DSP buffers, packet ring and USB calls stood in by arrays and stubs (UM_HOST). Checks: the
 * stream's start (11 silent packets of 6 frames), then blocks of 32 frames from both SSI halves at frame rates of 6,
 * 6.5 and 5.5 frames a packet: every packet 5 to 7 frames, inside the ring, primed on endpoint index 7; every frame
 * in order, every one of the 42 channels the right DSP channel's 24-bit sample, little-endian; the skip count; the
 * silent path; the mute flag; um_desc's descriptor writes (address and value, each one pushed). Since 0.3 (USB
 * MULTI/MIDI, its own USB CONFIG choice): the stock sender and start with the choice off; um_desc for each choice;
 * um_mode_set's restart, mode switch or the stock call in each case; um_choose; um_cfg_get; the menu's fourth item
 * (its four std::function, the item, the menu's call, the four destroyed). Since 0.4: after the stock's stream (its
 * slot index at 15, a packet half filled in its own ring) and with a wrong rate (20 and 0 frames a packet: 6 instead;
 * a stale target of 40), every packet 5 to 7 frames inside our ring, the stock's ring untouched (run with
 * -fsanitize=address too: a write past the ring is a global-buffer-overflow).
 *   cc -O2 -Wall -Wextra -o out/UM04/test_um scripts/um01/test_um.c && out/UM04/test_um
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define UM_HOST
static unsigned char ssi_mem[0x1000];          /* 2 halves x 32 frames x 16 slots x 4 bytes */
static unsigned char spi_mem[32 * 84];
static unsigned char ring_mem[15 * 882];
static unsigned long g_on, g_restart, g_skip, g_unmute, g_fill, g_cur, g_idx, g_cnt, g_frac, g_target, g_est,
    g_rate, g_flag_a, g_flag_b, g_start_p, g_txpos;
static unsigned long dtd_pool[64][16];
static int dtd_next, n_est, n_start2;
struct primed { unsigned long ep, buf, page1, tok, bytes, cb; };
static struct primed log_[4096];
static int n_log;

static unsigned long dtd_get(void)
{
    unsigned long *t = dtd_pool[dtd_next++ & 63];
    memset(t, 0, sizeof dtd_pool[0]);
    t[0] = 0xdead0001;
    return (unsigned long)t;
}
static int ep_prime(unsigned long ep, unsigned long tp)
{
    unsigned long *t = (unsigned long *)tp;
    struct primed p = { ep, t[2], t[3], t[1], t[8], t[9] };
    log_[n_log++] = p;
    return 0;
}
static unsigned long rate_est(unsigned long est, unsigned long *r)
{
    (void)r;
    n_est++;
    return est;
}
static void zero(void *p, unsigned long n) { memset(p, 0, n); }
static unsigned long pokes[16][2];
static int n_poke;
static char ev[256];
static int n_ev;
static void evt(char c) { ev[n_ev++] = c; ev[n_ev] = 0; }
static void poke(unsigned long a, unsigned char v)
{
    pokes[n_poke][0] = a;
    pokes[n_poke++][1] = v;
    if (!n_ev || ev[n_ev - 1] != 'P')           /* a run of descriptor writes: one event */
        evt('P');
}
static void start2(void) { n_start2++; }

/* 0.3: the choice, the stock sender and start, USB's stop, start and mode switch, the menu's allocations */
static signed char g_cfg;
static unsigned long g_mode;
static int n_stock_send, n_stock_start;
static const void *stock_frames;
static int stock_n;
static unsigned long stock_p;
static void cfg_set(volatile void *dst, const void *src, unsigned long n)
{
    evt('C');
    if (dst == (volatile void *)&g_cfg && n == 1)
        g_cfg = *(const signed char *)src;
}
static void mode_set(unsigned long m) { evt('0' + (char)m); g_mode = m; }
static void usb_stop(void) { evt('S'); }
static void usb_start(void) { evt('T'); }
static void stock_send(const void *f, int n) { n_stock_send++; stock_frames = f; stock_n = n; }
static void stock_start(unsigned long p) { n_stock_start++; stock_p = p; }
static unsigned long news[8];
static int n_new;
static unsigned char heap[8][0x60];
static void *new_(unsigned long n) { news[n_new] = n; return heap[n_new++]; }
struct fnh { void *p; unsigned long x, mgr; void (*inv)(void); };
static struct fnh item_fn[4];
static void *item_at;
static int item_a, item_b, n_item;
static void item_ctor(void *it, void *a, void *b, void *c, void *d, int x, int y)
{
    item_at = it;
    item_fn[0] = *(struct fnh *)a;
    item_fn[1] = *(struct fnh *)b;
    item_fn[2] = *(struct fnh *)c;
    item_fn[3] = *(struct fnh *)d;
    item_a = x;
    item_b = y;
    n_item++;
}
static void *dels[8];
static int n_del;
static void fn_del(void *f) { dels[n_del++] = f; }

#define DTD_GET  dtd_get
#define EP_PRIME ep_prime
#define RATE_EST rate_est
#define ZERO     zero
#define POKE     poke
#define START_CB 0x400039feu
#define START_2  start2
#define ON      g_on
#define RESTART g_restart
#define SKIP    g_skip
#define UNMUTE  g_unmute
#define FILL    g_fill
#define CUR     g_cur
#define IDX     g_idx
#define CNT     g_cnt
#define FRAC    g_frac
#define TARGET  g_target
#define EST     g_est
#define RATE    g_rate
#define FLAG_A  g_flag_a
#define FLAG_B  g_flag_b
#define START_P g_start_p
#define TXPOS   g_txpos
#define TXHALF  100ul
#define SSI0    ((unsigned long)ssi_mem)
#define SPI0    ((unsigned long)spi_mem)
#define RING    ((unsigned long)ring_mem)
#define STOCK_SEND  stock_send
#define STOCK_START stock_start
#define CFG     g_cfg
#define CFG_SET cfg_set
#define MODE    g_mode
#define MODE_SET mode_set
#define USB_STOP  usb_stop
#define USB_START usb_start
#define NEW     new_
#define ITEM    item_ctor
#define FN_DEL  fn_del
#define MGR_NAME 0x40104b22u
#define MGR_YES  0x40104b74u
#define MGR_BOX  0x40104bd0u
#define MGR_TURN 0x40104c2cu
#include "../../vendor/elekloader/examples/usb-multi-dn2/um.c"

void um_inv_name(void) {}
void um_inv_yes(void) {}
void um_inv_box(void) {}
void um_inv_turn(void) {}
static void *add_menu, *add_item;
void add(void *m, void *it) { add_menu = m; add_item = it; }

static int failed;
#define CHECK(c, ...) do { if (!(c)) { failed++; printf("FAIL  "); printf(__VA_ARGS__); printf("\n"); } } while (0)

/* the sample the DSP puts on channel ch of frame s in a block whose SSI half is h (and whose SPI rows are block b's) */
static unsigned long sample(int b, int h, int s, int ch)
{
    return ch < 14 ? (unsigned long)(0x100000 * (h + 1) + 0x400 * s + ch) & 0xffffff
                   : (unsigned long)(0x800000 | (b & 7) << 16 | s << 8 | ch);
}

static void fill_block(int b)
{
    int h, s, ch;
    for (h = 0; h < 2; h++)
        for (s = 0; s < 32; s++)
            for (ch = 0; ch < 16; ch++) {
                unsigned char *w = ssi_mem + ((h * 32 + s) * 16 + ch) * 4;
                unsigned long v = sample(b, h, s, ch < 14 ? ch : 0);
                w[0] = 0xee;                         /* the word's top byte: not part of the sample */
                w[1] = v >> 16;
                w[2] = v >> 8;
                w[3] = v;
            }
    for (s = 0; s < 32; s++)
        for (ch = 14; ch < 42; ch++) {
            unsigned char *r = spi_mem + s * 84 + (ch - 14) / 2 * 6 + (ch - 14) % 2 * 3;
            unsigned long v = sample(b, 0, s, ch);
            r[0] = v >> 16;
            r[1] = v >> 8;
            r[2] = v;
        }
}

static const int dsp_of[42] = { 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24,
    25, 26, 27, 28, 29, 30, 31, 32, 33, 0, 1, 34, 35, 36, 37, 38, 39, 40, 41 };

int main(void)
{
    int b, i, frames_ok = 0, frames = 0, pk_ok = 1, start_ok = 1;
    static int want_b[16384], want_h[16384], want_s[16384];
    int nw = 0, sizes[8] = { 0 };
    const unsigned long rates[3] = { 6ul << 16, (6ul << 16) + 0x8000, (5ul << 16) + 0x8000 };

    /* the stream's start (USB MULTI/MIDI chosen) */
    um_on = 1;
    g_on = 1;
    g_unmute = 1;
    memset(ring_mem, 0x55, sizeof ring_mem);
    um_start(0x1234);
    CHECK(n_log == 11, "the start primes %d packets, not 11", n_log);
    for (i = 0; i < n_log; i++) {
        unsigned char *p = (unsigned char *)log_[i].buf;
        int k, nz = 0;
        for (k = 0; k < 6 * 126; k++)
            nz += p[k] != 0;
        start_ok &= log_[i].ep == 7 && log_[i].bytes == 756 && log_[i].tok == (756ul << 16 | 0x80) && nz == 0 &&
                    log_[i].cb == (i == 0 ? 0x400039feu : 0) && p >= ring_mem && p + 756 <= ring_mem + sizeof ring_mem;
    }
    CHECK(start_ok, "the start's packets: endpoint 7, 756 bytes of zeros each, the callback on the first, in the ring");
    CHECK(g_restart == 1 && g_flag_a == 1 && g_flag_b == 1 && g_start_p == 0x1234 && n_start2 == 1,
          "the start's flags, its parameter and the rest of the start");
    printf("%s  the stream's start: 11 silent packets of 6 frames (756 bytes), restart set\n", start_ok ? "PASS" : "FAIL");

    /* blocks: half by the DMA's place, three rates, a skip of 3 frames in block 5 */
    n_log = 0;
    for (b = 0; b < 300; b++) {
        int h = b & 1, s0 = 0;
        g_rate = rates[b / 100];
        g_txpos = h ? 50 : 150;                       /* < TXHALF: the handler's half is the second */
        fill_block(b);
        if (b == 5) {
            g_skip = 3;
            s0 = 3;
        }
        for (i = s0; i < 32; i++) {
            want_b[nw] = b;
            want_h[nw] = h;
            want_s[nw++] = i;
        }
        um_send(NULL, 32);
        /* check the packets this block finished (their frames are the oldest wanted ones not yet checked) */
        for (; frames_ok + frames < nw && n_log > 0;) {
            int k;
            struct primed p = log_[0];
            int nf = (int)(p.bytes / 126);
            unsigned char *d = (unsigned char *)p.buf;
            pk_ok &= p.ep == 7 && p.bytes % 126 == 0 && nf >= 5 && nf <= 7 && p.tok == (p.bytes << 16 | 0x80) &&
                     d >= ring_mem && d + p.bytes <= ring_mem + sizeof ring_mem &&
                     p.page1 == ((p.buf + 0x1000) & ~0xffful);
            sizes[nf]++;
            for (k = 0; k < nf; k++, d += 126) {
                int c, ok = 1, f = frames_ok + frames;
                for (c = 0; c < 42; c++) {
                    unsigned long v = d[3 * c] | d[3 * c + 1] << 8 | (unsigned long)d[3 * c + 2] << 16;
                    ok &= v == sample(want_b[f], want_h[f] == 1 ? 1 : 0, want_s[f], dsp_of[c]);
                }
                /* the SSI half: the handler's half is 1 when the DMA is in the first (txpos < TXHALF) */
                frames++;
                frames_ok += ok;
                frames -= ok;
            }
            memmove(log_, log_ + 1, (size_t)--n_log * sizeof log_[0]);
        }
    }
    printf("%s  %d packets of 5, %d of 6, %d of 7 frames, each on endpoint 7, inside the ring, its token and pages right\n",
           pk_ok && !sizes[0] ? "PASS" : "FAIL", sizes[5], sizes[6], sizes[7]);
    if (!pk_ok)
        failed++;
    CHECK(frames == 0, "%d frames with a wrong channel", frames);
    printf("%s  %d frames sent of %d (the last packet still filling), each channel the right DSP channel, 24-bit "
           "little-endian, tracks first\n", frames == 0 ? "PASS" : "FAIL", frames_ok, nw);

    /* the silent path and the mute flag: zeros, the same packet timing */
    n_log = 0;
    {
        int left = (int)g_fill * 126;                /* the packet being filled keeps the frames it already has */
        um_mute(NULL, 32);
        um_mute(NULL, 32);
        int ok = n_log > 0, k;
        for (i = 0; i < n_log; i++)
            for (k = i == 0 ? left : 0; k < (int)log_[i].bytes; k++)
                ok &= ((unsigned char *)log_[i].buf)[k] == 0;
        CHECK(ok, "the silent path sends zeros");
        printf("%s  the silent path: %d packets of zeros\n", ok ? "PASS" : "FAIL", n_log);
    }
    n_log = 0;
    g_unmute = 0;
    fill_block(1);
    {
        int left = (int)g_fill * 126;
        um_send(NULL, 32);
        um_send(NULL, 32);
        int ok = n_log > 0, k;
        for (i = 0; i < n_log; i++)
            for (k = i == 0 ? left : 0; k < (int)log_[i].bytes; k++)
                ok &= ((unsigned char *)log_[i].buf)[k] == 0;
        CHECK(ok, "muted: zeros");
        printf("%s  muted (the stock's flag off): %d packets of zeros\n", ok ? "PASS" : "FAIL", n_log);
    }
    n_log = 0;
    g_on = 0;
    um_send(NULL, 32);
    CHECK(n_log == 0, "the host not listening: nothing sent");
    printf("%s  the host not listening: nothing sent\n", n_log == 0 ? "PASS" : "FAIL");
    {
        /* 0.4: whatever the stock sender left, and whatever the rate says, every packet 5 to 7 frames in our ring */
        static unsigned char stock_ring[16 * 56];
        static const struct { unsigned long rate, target; const char *what; } bad[] = {
            { 20ul << 16, 6, "a rate of 20 frames a packet" }, { 0, 6, "a rate of 0" },
            { 6ul << 16, 40, "a stale target of 40" } };
        int k, ok = 1, n = 0, j;
        memset(stock_ring, 0xaa, sizeof stock_ring);
        um_on = 1;
        g_on = 1;
        g_unmute = 1;
        g_idx = 15;                                    /* the stock ring's 16th slot */
        n_log = 0;
        um_start(0x1234);
        for (i = 0; i < n_log; i++)
            ok &= log_[i].buf >= RING && log_[i].buf + log_[i].bytes <= RING + sizeof ring_mem;
        CHECK(ok && n_log == 11, "after the stock's stream: the start's packets in the ring");
        printf("%s  after the stock's stream (its slot index at 15): the start's 11 packets inside the ring\n",
               ok && n_log == 11 ? "PASS" : "FAIL");
        g_restart = 0;                                 /* a packet the stock began in its own ring, 3 frames in */
        g_cur = (unsigned long)stock_ring + 5 * 56;
        g_fill = 3;
        g_target = 6;
        g_idx = 15;
        g_rate = 6ul << 16;
        g_txpos = 150;
        fill_block(0);
        n_log = 0;
        for (b = 0; b < 4; b++)
            um_send(NULL, 32);
        ok = n_log > 0;
        for (i = 0; i < n_log; i++)
            ok &= log_[i].buf >= RING && log_[i].buf + log_[i].bytes <= RING + sizeof ring_mem &&
                  log_[i].bytes % 126 == 0 && log_[i].bytes / 126 >= 5 && log_[i].bytes / 126 <= 7;
        for (i = 0; i < (int)sizeof stock_ring; i++)
            ok &= stock_ring[i] == 0xaa;
        CHECK(ok, "a packet the stock began: ours start anew in the ring");
        printf("%s  a packet the stock began in its ring (3 frames in, slot index 15): %d packets, all in ours, the "
               "stock's ring untouched\n", ok ? "PASS" : "FAIL", n_log);
        for (k = 0; k < 3; k++) {
            int okk = 1;
            g_rate = bad[k].rate;
            g_target = bad[k].target;
            g_fill = 0;
            n_log = 0;
            for (b = 0; b < 40; b++)
                um_send(NULL, 32);
            for (i = 0; i < n_log; i++)
                okk &= log_[i].buf >= RING && log_[i].buf + log_[i].bytes <= RING + sizeof ring_mem &&
                       log_[i].bytes % 126 == 0 && log_[i].bytes / 126 >= 5 && log_[i].bytes / 126 <= 7 &&
                       log_[i].tok == (log_[i].bytes << 16 | 0x80);
            for (j = 0; j < (int)sizeof stock_ring; j++)
                okk &= stock_ring[j] == 0xaa;
            okk &= n_log > 0 && g_target >= 5 && g_target <= 7;
            if (bad[k].rate != 6ul << 16)                      /* a wrong rate: 6 frames a packet instead */
                for (i = 1; i < n_log; i++)
                    okk &= log_[i].bytes == 6 * 126;
            n += n_log;
            CHECK(okk, "%s: packets in the ring, 5 to 7 frames", bad[k].what);
            printf("%s  %s: %d packets, each %s inside the ring\n", okk ? "PASS" : "FAIL", bad[k].what, n_log,
                   bad[k].rate != 6ul << 16 ? "6 frames (the nominal rate instead)" : "5 to 7 frames");
        }
        (void)n;
        g_on = 0;
    }
    {
        static const unsigned long want42[7][2] = { { 0x402fc000, 0x22 }, { 0x402fc407, 42 }, { 0x402fc494, 42 },
            { 0x402fc49e, 3 }, { 0x402fc49f, 24 }, { 0x402fc4a4, 0x72 }, { 0x402fc4a5, 0x03 } };
        static const unsigned long want2[7][2] = { { 0x402fc000, 0x22 }, { 0x402fc407, 2 }, { 0x402fc494, 2 },
            { 0x402fc49e, 4 }, { 0x402fc49f, 32 }, { 0x402fc4a4, 56 }, { 0x402fc4a5, 0 } };
        int ok, ok2;
        g_cfg = 3;
        um_on = 0;
        n_poke = 0;
        um_desc();
        ok = n_poke == 7 && um_on == 1;
        for (i = 0; ok && i < 7; i++)
            ok = pokes[i][0] == want42[i][0] && pokes[i][1] == want42[i][1];
        CHECK(ok, "um_desc's writes, USB CONFIG 3");
        printf("%s  um_desc, USB MULTI/MIDI (3): the stock's last write, then IT 5 and AS 2 at 42 channels, 3-byte 24-bit "
               "subslots, EP 0x83 882 bytes (%d writes), um_on\n", ok ? "PASS" : "FAIL", n_poke);
        ok2 = 1;
        for (g_cfg = -1; g_cfg < 8; g_cfg++) {
            if (g_cfg == 3)
                continue;
            n_poke = 0;
            um_on = 1;
            um_desc();
            ok = n_poke == 7 && um_on == 0;
            for (i = 0; ok && i < 7; i++)
                ok = pokes[i][0] == want2[i][0] && pokes[i][1] == want2[i][1];
            ok2 &= ok;
        }
        CHECK(ok2, "um_desc's writes, USB CONFIG not 3");
        printf("%s  um_desc, any other USB CONFIG (-1, 0-2, 4-7): the stock's stereo values (2 channels, 4-byte 32-bit, "
               "56 bytes), um_on off\n", ok2 ? "PASS" : "FAIL");
    }
    {
        /* the choice off: the stock sender and start, with the arguments they were given; nothing of ours */
        int ok;
        um_on = 0;
        n_log = 0;
        g_on = 1;
        g_unmute = 1;
        um_send((const void *)0x1111, 32);
        ok = n_stock_send == 1 && stock_frames == (const void *)0x1111 && stock_n == 32;
        um_mute((const void *)0x2222, 32);
        ok &= n_stock_send == 2 && stock_frames == (const void *)0x2222 && stock_n == 32;
        um_start(0x4321);
        ok &= n_stock_start == 1 && stock_p == 0x4321 && n_log == 0;
        CHECK(ok, "the choice off: the stock calls");
        printf("%s  USB AUDIO/MIDI (um_on off): um_send, um_mute and um_start call the stock sender and start with "
               "their arguments, nothing primed of ours\n", ok ? "PASS" : "FAIL");
    }
    {
        /* um_mode_set: (mode asked, mode running, USB CONFIG, um_on) -> the events and um_on after */
        static const struct { unsigned long ask, run; signed char cfg; unsigned long on; const char *ev; unsigned long
            on_after; } t[] = {
            { 5, 5, 3, 0, "SPT", 1 },      /* stereo -> ours, mode 5 stays: stop, the descriptors, start */
            { 5, 5, 2, 1, "SPT", 0 },      /* ours -> stereo */
            { 5, 6, 3, 0, "P5", 1 },       /* from Overbridge: the descriptors, then the stock switch */
            { 5, 2, 2, 1, "P5", 0 },       /* from USB MIDI to stereo, ours last */
            { 5, 5, 2, 0, "5", 0 },        /* nothing changes: the stock call (which returns) */
            { 5, 5, 3, 1, "5", 1 },
            { 6, 5, 3, 1, "6", 1 },        /* another mode: the stock call only */
            { 2, 5, 2, 1, "2", 1 },
        };
        int k, ok = 1;
        for (k = 0; k < (int)(sizeof t / sizeof t[0]); k++) {
            char got[32];
            n_ev = 0;
            ev[0] = 0;
            n_poke = 0;
            g_mode = t[k].run;
            g_cfg = t[k].cfg;
            um_on = t[k].on;
            um_mode_set(t[k].ask);
            strcpy(got, ev);
            if (strcmp(got, t[k].ev) || um_on != t[k].on_after || (n_poke != 0 && n_poke != 6)) {
                ok = 0;
                printf("      case %d: events %s (want %s), um_on %lu, %d writes\n", k, got, t[k].ev,
                       (unsigned long)um_on, n_poke);
            }
        }
        CHECK(ok, "um_mode_set");
        printf("%s  um_mode_set: stereo <-> ours in mode 5 stops, writes the descriptors, starts; from another mode the "
               "descriptors then the stock switch; otherwise the stock call alone (8 cases)\n", ok ? "PASS" : "FAIL");
    }
    {
        int ok;
        g_cfg = 2;
        g_mode = 5;
        um_on = 0;
        n_ev = 0;
        ev[0] = 0;
        um_choose();
        ok = g_cfg == 3 && !strcmp(ev, "CSPT") && um_on == 1;
        CHECK(ok, "um_choose");
        printf("%s  um_choose: USB CONFIG saved as 3 through the stock's setter, then the restart (%s)\n",
               ok ? "PASS" : "FAIL", ev);
        ok = 1;
        for (i = -128; i < 128; i++) {
            g_cfg = (signed char)i;
            ok &= um_cfg_get() == (unsigned long)(i < 0 ? 0 : i > 3 ? 3 : i);
        }
        CHECK(ok, "um_cfg_get");
        printf("%s  um_cfg_get: the saved byte, 0 below, 3 above\n", ok ? "PASS" : "FAIL");
    }
    {
        /* the menu's fourth item */
        static unsigned long vt[4];
        static void *menu_called[2];
        struct { unsigned long *vt; } menu;
        int ok;
        void add(void *m, void *it);
        menu.vt = vt;
        vt[2] = (unsigned long)add;
        menu_called[0] = menu_called[1] = 0;
        n_new = n_item = n_del = 0;
        um_menu_add(&menu);
        ok = n_new == 5 && news[0] == 1 && news[1] == 4 && news[2] == 4 && news[3] == 4 && news[4] == 0x54;
        ok &= n_item == 1 && item_at == heap[4] && item_a == -1 && item_b == 8;
        ok &= item_fn[0].mgr == 0x40104b22u && item_fn[0].inv == um_inv_name && item_fn[0].p == heap[0];
        ok &= item_fn[1].mgr == 0x40104b74u && item_fn[1].inv == um_inv_yes && *(void **)heap[1] == &menu;
        ok &= item_fn[2].mgr == 0x40104bd0u && item_fn[2].inv == um_inv_box && *(void **)heap[2] == &menu;
        ok &= item_fn[3].mgr == 0x40104c2cu && item_fn[3].inv == um_inv_turn && *(void **)heap[3] == &menu;
        ok &= item_fn[0].x == 0 && item_fn[1].x == 0 && item_fn[2].x == 0 && item_fn[3].x == 0;
        ok &= n_del == 4 && dels[0] != dels[1] && dels[3] < dels[0] && dels[0] > dels[1] && dels[1] > dels[2];
        ok &= add_menu == &menu && add_item == heap[4];
        CHECK(ok, "um_menu_add");
        printf("%s  um_menu_add: name, YES, box and turn std::function (new 1, 4, 4, 4: the menu's pointer kept), the "
               "managers USB AUDIO/MIDI's, the item (0x54) built with -1, 8, given to the menu, the four destroyed last "
               "to first\n", ok ? "PASS" : "FAIL");
        (void)menu_called;
    }
    printf(failed ? "%d failed\n" : "usb-multi: all passed\n", failed);
    return failed ? 1 : 0;
}
