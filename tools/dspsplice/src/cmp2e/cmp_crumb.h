/* SPDX-License-Identifier: GPL-2.0-or-later */
/* cmp_crumb.h - T6.3 CMP2E: breadcrumbs (notes/T6.3-cmp2e.md). CRUMB(id) writes the stage id into reply
 * half-word 9 of both reply pages (the MID page's left bar, id x 3 px: q = id x 1965, h = q / 655 = 3 id), with
 * half-word 8 (the LOW page's left bar) = the heartbeat half CmpLoad.hbq (the first word of CmpLoad, set once a
 * block by cmp_load_end): one 32-bit store at +0x10 of each page. The stores are volatile, to constant addresses,
 * with no call: selcc keeps them, in program order, where they are written (verify_cmp2e.py checks the order of
 * the stores against the stages' own). The ids rise through a block:
 *    1 glue entry (cmp_load_begin)          9 detector, band 2
 *    2 the magic test / quiet re-init      10 gain computer, band 0
 *    3 tail fetch                          11 gain computer, band 1
 *    4 tail apply, or the fallback knobs   12 gain computer, band 2
 *    5 mbcomp_process: the control block   13 output stage
 *      (mb_control, mb_targets)            14 guard / restart
 *    6 crossover split                     15 the glue's meters (seed, gr0..gr2, cmp_load_end)
 *    7 detector, band 0 (or the key)       16 glue exit
 *    8 detector, band 1
 * A core that stops inside our code leaves the stage it was in; 16 means our code had finished its block. */
#ifndef CMP_CRUMB_H
#define CMP_CRUMB_H

#include <stdint.h>

#define CRUMB_HBQ 0x2e2800u                       /* CmpLoad.hbq (cmp_load.h: the first field) */
#define CRUMB_TX0 0x2c49e0u                       /* reply page 0 + 0x10: half-words 8 (low) and 9 (high) */
#define CRUMB_TX1 0x2c59e0u                       /* reply page 1 + 0x10 */
#define CRUMB_Q 1965u                             /* 3 px of bar per id */

#ifdef CMP_NATIVE
#define CRUMB(id) do { } while (0)
#else
#define CRUMB(id) do { \
        uint32_t crumb_; \
        crumb_ = *(volatile uint32_t *)CRUMB_HBQ + (uint32_t)(id) * (CRUMB_Q * 65536u); \
        *(volatile uint32_t *)CRUMB_TX0 = crumb_; \
        *(volatile uint32_t *)CRUMB_TX1 = crumb_; \
    } while (0)
#endif

#endif
