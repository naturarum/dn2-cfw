/* SPDX-License-Identifier: GPL-2.0-or-later */
/* wav.c - minimal WAV I/O for the T3.11 tools (Mac side only, uses libc). */
#include "wav.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>

static uint32_t rd32(const unsigned char *p) { return p[0] | (p[1] << 8) | (p[2] << 16) | ((uint32_t)p[3] << 24); }
static uint16_t rd16(const unsigned char *p) { return (uint16_t)(p[0] | (p[1] << 8)); }

int wav_alloc(Wav *w, long frames, int rate)
{
    w->rate = rate;
    w->frames = frames;
    w->l = calloc((size_t)(frames > 0 ? frames : 1), sizeof(float));
    w->r = calloc((size_t)(frames > 0 ? frames : 1), sizeof(float));
    return (w->l && w->r) ? 0 : -1;
}

void wav_free(Wav *w)
{
    free(w->l);
    free(w->r);
    w->l = w->r = NULL;
}

int wav_read(const char *path, Wav *w)
{
    FILE *f = fopen(path, "rb");
    unsigned char hdr[12], ck[8], fmt[40];
    int fmt_ok = 0, ch = 0, bits = 0, tag = 0;
    long i;
    if (!f) { fprintf(stderr, "wav: cannot open %s\n", path); return -1; }
    if (fread(hdr, 1, 12, f) != 12 || memcmp(hdr, "RIFF", 4) || memcmp(hdr + 8, "WAVE", 4)) {
        fprintf(stderr, "wav: %s is not RIFF/WAVE\n", path); fclose(f); return -1;
    }
    while (fread(ck, 1, 8, f) == 8) {
        uint32_t len = rd32(ck + 4);
        if (!memcmp(ck, "fmt ", 4)) {
            size_t n = len < sizeof fmt ? len : sizeof fmt;
            if (fread(fmt, 1, n, f) != n) break;
            if (len > n) fseek(f, (long)(len - n), SEEK_CUR);
            tag = rd16(fmt); ch = rd16(fmt + 2); w->rate = (int)rd32(fmt + 4); bits = rd16(fmt + 14);
            if (tag == 0xfffe && n >= 26) tag = rd16(fmt + 24);
            fmt_ok = 1;
        } else if (!memcmp(ck, "data", 4) && fmt_ok) {
            int bps = bits / 8;
            long frames = (long)len / (bps * ch);
            unsigned char *buf = malloc(len ? len : 1);
            if (!buf || fread(buf, 1, len, f) != len) { free(buf); break; }
            if (wav_alloc(w, frames, w->rate)) { free(buf); break; }
            for (i = 0; i < frames; i++) {
                int c;
                for (c = 0; c < 2; c++) {
                    int cc = c < ch ? c : 0;
                    const unsigned char *p = buf + (size_t)(i * ch + cc) * bps;
                    float v = 0.0f;
                    if (tag == 3 && bits == 32) { memcpy(&v, p, 4); }
                    else if (tag == 1 && bits == 16) v = (int16_t)rd16(p) / 32768.0f;
                    else if (tag == 1 && bits == 24) { int32_t s = (int32_t)((p[0] << 8) | (p[1] << 16) | ((uint32_t)p[2] << 24)); v = (float)(s / 2147483648.0); }
                    else if (tag == 1 && bits == 32) v = (float)((int32_t)rd32(p) / 2147483648.0);
                    else { fprintf(stderr, "wav: unsupported format tag %d bits %d\n", tag, bits); free(buf); fclose(f); return -1; }
                    (c ? w->r : w->l)[i] = v;
                }
            }
            free(buf);
            fclose(f);
            return 0;
        } else {
            fseek(f, (long)(len + (len & 1)), SEEK_CUR);
        }
    }
    fprintf(stderr, "wav: no data in %s\n", path);
    fclose(f);
    return -1;
}

static void wr32(FILE *f, uint32_t v) { unsigned char b[4] = { v, v >> 8, v >> 16, v >> 24 }; fwrite(b, 1, 4, f); }
static void wr16(FILE *f, uint16_t v) { unsigned char b[2] = { v, v >> 8 }; fwrite(b, 1, 2, f); }

int wav_write(const char *path, const float *l, const float *r, long frames, int rate, int bits)
{
    FILE *f = fopen(path, "wb");
    int bps = bits / 8, tag = bits == 32 ? 3 : 1;
    uint32_t dlen = (uint32_t)(frames * 2 * bps);
    uint32_t seed = 22222u;
    long i;
    if (!f) { fprintf(stderr, "wav: cannot write %s\n", path); return -1; }
    fwrite("RIFF", 1, 4, f); wr32(f, 36 + dlen); fwrite("WAVE", 1, 4, f);
    fwrite("fmt ", 1, 4, f); wr32(f, 16); wr16(f, (uint16_t)tag); wr16(f, 2); wr32(f, (uint32_t)rate);
    wr32(f, (uint32_t)(rate * 2 * bps)); wr16(f, (uint16_t)(2 * bps)); wr16(f, (uint16_t)bits);
    fwrite("data", 1, 4, f); wr32(f, dlen);
    for (i = 0; i < frames; i++) {
        int c;
        for (c = 0; c < 2; c++) {
            float v = c ? r[i] : l[i];
            if (bits == 32) { fwrite(&v, 4, 1, f); continue; }
            if (bits == 16) {
                double d, t1, t2;
                seed = seed * 1664525u + 1013904223u; t1 = (seed >> 8) / 16777216.0;
                seed = seed * 1664525u + 1013904223u; t2 = (seed >> 8) / 16777216.0;
                d = v * 32767.0 + (t1 - t2);
                if (d > 32767.0) d = 32767.0;
                if (d < -32768.0) d = -32768.0;
                wr16(f, (uint16_t)(int16_t)(d < 0 ? d - 0.5 : d + 0.5));
            } else {
                double d = v * 8388607.0;
                int32_t s;
                unsigned char b[3];
                if (d > 8388607.0) d = 8388607.0;
                if (d < -8388608.0) d = -8388608.0;
                s = (int32_t)(d < 0 ? d - 0.5 : d + 0.5);
                b[0] = (unsigned char)s; b[1] = (unsigned char)(s >> 8); b[2] = (unsigned char)(s >> 16);
                fwrite(b, 1, 3, f);
            }
        }
    }
    fclose(f);
    return 0;
}
