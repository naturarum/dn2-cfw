/* SPDX-License-Identifier: GPL-2.0-or-later */
/*
 * dn2patch.h - the core of Digiwerk Patcher (the app) and digiwerk-patch (the Terminal tool): the user's own copy of
 * Elektron's Digitone II OS 1.12 file in, one of our tested builds out (dn2patch.c has the steps). A build comes as
 * a pack (mkpatch.py makes it: format in its docstring); the caller holds the packs (the app in its Resources, the
 * Terminal tool compiled in) and passes one in. No global state, so it can run on any thread.
 */
#ifndef DN2PATCH_H
#define DN2PATCH_H

#include <stddef.h>
#include <stdint.h>

#define DN2P_OK 0                   /* *out holds the build, byte for byte the one we tested */
#define DN2P_NOT_STOCK 1            /* the input is not Elektron's 1.12 file (msg says what it is, when it can) */
#define DN2P_FAILED 2               /* the build could not be made, or is not the one we tested: nothing to write */
#define DN2P_NOMEM 3
#define DN2P_CHECK_FAILED 4         /* a check stage (dn2p_check) said no: nothing to write */

#define DN2P_STOCK_NAME "Digitone_II_OS1.12.syx"
#define DN2P_MAX_IN (64u << 20)     /* no OS file is this big: a reader may refuse larger files before reading */
#define DN2P_MSG 640                /* enough for any msg */

/* A pack's fields, as dn2patch_info reads them (UTF-8, NUL-terminated). */
typedef struct {
    char id[17];                    /* the build's name, e.g. "OB01": the output file is OB01.syx */
    char title[64];                 /* e.g. "Compressor and flanger" */
    char description[160];          /* one line */
    char record[160];               /* e.g. "Brick check 12 of 12 · tested on a Digitone II, 22 Oct 2026" */
    char tested_on_unit[32];        /* the date it ran on a unit, or "" */
    char brickcheck[160];           /* e.g. "ACCEPT 12/12 2026-10-22T12:57:21" */
    char effects[64];               /* the effects it carries, by id, space-separated: e.g. "cmp3 flng" */
    char build_sha256[65];          /* the output's sha256, hex */
    char stock_sha256[65];          /* Elektron's 1.12 file's sha256, hex */
    long dsp_code_bytes;            /* for a later check stage (a DSP memory/load adder): -1 when not given */
    long dsp_state_bytes;
    double dsp_load_percent;
} dn2p_info;

/* 0: pack is one this core can apply; info gets its fields. */
int dn2patch_info(const uint8_t *pack, size_t packn, dn2p_info *info);

/* A check stage, for later (brick checks, a DSP memory/load adder over combined packs): it runs on the output after
 * the sha256 gate, before dn2patch_run hands the output over. Return 0 to pass; otherwise write why into text
 * (textn bytes) and dn2patch_run returns DN2P_CHECK_FAILED with no output. */
typedef int (*dn2p_check)(const uint8_t *out, size_t n, const dn2p_info *pack, char *text, size_t textn, void *ctx);

/* in: the input file's bytes. check: NULL, or a check stage (with ctx). On DN2P_OK, *out (free it with
 * dn2patch_free) and *outn hold the build's .syx. msg (msgn bytes, may be 0; DN2P_MSG is plenty) gets, whatever the
 * status, one or two plain-English sentences for the user, a newline, then the precise reason (sizes and full
 * sha256s, for a bug report). */
int dn2patch_run(const uint8_t *pack, size_t packn, const uint8_t *in, size_t n, dn2p_check check, void *ctx,
                 uint8_t **out, size_t *outn, char *msg, size_t msgn);
void dn2patch_free(uint8_t *out);

/* 1: these bytes are the pack's build already (an existing output file). */
int dn2patch_is_build(const uint8_t *pack, size_t packn, const uint8_t *in, size_t n);

#endif
