/* SPDX-License-Identifier: GPL-2.0-or-later */
/*
 * dn2patch.c - the core of Digiwerk Patcher and digiwerk-patch (dn2patch.h): turns the user's own copy of Elektron's
 * Digitone II OS 1.12 file into one of our builds, so that we never hand out Elektron's firmware.
 *
 * A pack (made by mkpatch.py, whose docstring has its format) holds our bytes, copy instructions into the stock file,
 * codec control bits and its metadata; stockcheck.py proves that no 16 bytes in a row of it appear in Elektron's
 * file. dn2patch_run() does mkpatch.py's apply():
 *   1. refuse the input unless its sha256 is that of Elektron's 1.12 file;
 *   2. decode it: SysEx messages, 8-in-7, the ELE3 container, every section depacked: the pool;
 *   3. per replaced section: its decoded image and its skeleton, from their ops; the packed stream, by walking the
 *      skeleton with the decoder and taking each literal from the image;
 *   4. the container as stock but the version field and those sections: the table, the 16-byte padding, the
 *      HMAC-SHA256 seal (its key from the stock bootstrap), the preamble's content checksum; then the SysEx messages
 *      with their counters and checksums, and the framing messages' count;
 *   5. hand the result over only if its sha256 is the build's, and a check stage, if the caller gives one, passes.
 * libc only (the SHA-256 is below), no global state, no exit(): the app calls it on a background thread.
 */
#include <stdarg.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "dn2patch.h"

#define CHUNK 101                   /* decoded bytes per data message */
#define FIRST_COUNTER 242           /* the first data message's counter */
#define BIAS 767                    /* the codec's constants (codec/elz.py) */
#define REUSE 2
#define FAR 3328
#define SEAL_SEED "Multiplier"      /* the seal key's seed, in the bootstrap (devices/digitone_mk2.py) */
#define SEAL_SEC 2

/* ---- a growable byte buffer: when an allocation fails, bad is set and later puts do nothing ---- */

typedef struct { uint8_t *p; size_t n, cap; int bad; } Buf;

static int grow(Buf *b, size_t more)
{
    if (b->bad)
        return -1;
    if (more <= b->cap - b->n)
        return 0;
    size_t cap = b->cap ? b->cap : 1 << 16;
    while (cap - b->n < more) {
        if (cap > SIZE_MAX / 2)
            break;
        cap *= 2;
    }
    uint8_t *p = cap - b->n >= more ? realloc(b->p, cap) : NULL;
    if (!p) {
        b->bad = 1;
        return -1;
    }
    b->p = p;
    b->cap = cap;
    return 0;
}

static void put(Buf *b, const void *src, size_t n)
{
    if (n && !grow(b, n)) {
        memcpy(b->p + b->n, src, n);
        b->n += n;
    }
}

static void put1(Buf *b, uint8_t v)
{
    if (!grow(b, 1))
        b->p[b->n++] = v;
}

static uint32_t be32(const uint8_t *p) { return (uint32_t)p[0] << 24 | p[1] << 16 | p[2] << 8 | p[3]; }

static void set32(uint8_t *p, uint32_t v)
{
    p[0] = v >> 24;
    p[1] = v >> 16;
    p[2] = v >> 8;
    p[3] = v;
}

/* ---- SHA-256 (FIPS 180-4) and HMAC-SHA256 (RFC 2104) ---- */

typedef struct { uint32_t h[8]; uint64_t len; uint8_t blk[64]; size_t n; } Sha;

static const uint32_t K256[64] = {
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
    0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
    0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
    0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
    0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
};

#define ROR(x, n) ((x) >> (n) | (x) << (32 - (n)))

static void sha_block(Sha *s, const uint8_t *p)
{
    uint32_t w[64], v[8];
    for (int i = 0; i < 16; i++)
        w[i] = be32(p + 4 * i);
    for (int i = 16; i < 64; i++) {
        uint32_t s0 = ROR(w[i - 15], 7) ^ ROR(w[i - 15], 18) ^ (w[i - 15] >> 3);
        uint32_t s1 = ROR(w[i - 2], 17) ^ ROR(w[i - 2], 19) ^ (w[i - 2] >> 10);
        w[i] = w[i - 16] + s0 + w[i - 7] + s1;
    }
    memcpy(v, s->h, sizeof v);
    for (int i = 0; i < 64; i++) {
        uint32_t e = v[4], a = v[0];
        uint32_t t1 = v[7] + (ROR(e, 6) ^ ROR(e, 11) ^ ROR(e, 25)) + ((e & v[5]) ^ (~e & v[6])) + K256[i] + w[i];
        uint32_t t2 = (ROR(a, 2) ^ ROR(a, 13) ^ ROR(a, 22)) + ((a & v[1]) ^ (a & v[2]) ^ (v[1] & v[2]));
        memmove(v + 1, v, 7 * sizeof *v);
        v[4] += t1;
        v[0] = t1 + t2;
    }
    for (int i = 0; i < 8; i++)
        s->h[i] += v[i];
}

static void sha_init(Sha *s)
{
    static const uint32_t h0[8] = { 0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
                                    0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19 };
    memcpy(s->h, h0, sizeof h0);
    s->len = 0;
    s->n = 0;
}

static void sha_add(Sha *s, const uint8_t *p, size_t n)
{
    s->len += n;
    while (n) {
        size_t k = 64 - s->n < n ? 64 - s->n : n;
        memcpy(s->blk + s->n, p, k);
        s->n += k;
        p += k;
        n -= k;
        if (s->n == 64) {
            sha_block(s, s->blk);
            s->n = 0;
        }
    }
}

static void sha_end(Sha *s, uint8_t out[32])
{
    uint64_t bits = s->len * 8;
    uint8_t pad[72] = { 0x80 };
    size_t k = (s->n < 56 ? 56 : 120) - s->n;
    for (int i = 0; i < 8; i++)
        pad[k + i] = bits >> (56 - 8 * i);
    sha_add(s, pad, k + 8);
    for (int i = 0; i < 8; i++)
        set32(out + 4 * i, s->h[i]);
}

static void sha256(const uint8_t *p, size_t n, uint8_t out[32])
{
    Sha s;
    sha_init(&s);
    sha_add(&s, p, n);
    sha_end(&s, out);
}

static void hmac_sha256(const uint8_t key[32], const uint8_t *p, size_t n, uint8_t out[32])
{
    uint8_t pad[64], inner[32];
    Sha s;
    for (int pass = 0; pass < 2; pass++) {
        memset(pad, pass ? 0x5c : 0x36, 64);
        for (int i = 0; i < 32; i++)
            pad[i] ^= key[i];
        sha_init(&s);
        sha_add(&s, pad, 64);
        if (pass)
            sha_add(&s, inner, 32);
        else
            sha_add(&s, p, n);
        sha_end(&s, pass ? out : inner);
    }
}

/* ---- the codec's decoder (codec/elz.py; the bootstrap's depacker at 0x80000432), in two modes ----
 * depack:  in is a packed stream; every byte read comes from it, literals too; out gets the decoded bytes.
 * rebuild: in is a skeleton (a packed stream without its literals); each literal comes from img at the output
 *          position instead; out gets the packed stream (every byte the decoder reads, in order). */

typedef struct {
    const uint8_t *in, *end;
    const uint8_t *img;             /* rebuild: the decoded image, img_n bytes */
    size_t img_n;
    Buf *out;
    unsigned tag;                   /* the tag register: 8 control bits and a marker */
    int rebuild, bad;
} Walk;

static unsigned rd(Walk *w)         /* the next byte of in: a tag or offset byte (or, depacking, a literal) */
{
    if (w->in == w->end) {
        w->bad = 1;
        return 0;
    }
    uint8_t v = *w->in++;
    if (w->rebuild)
        put1(w->out, v);
    return v;
}

static unsigned bit(Walk *w)
{
    w->tag = (w->tag << 1) & 0x1FF;
    if ((w->tag & 0xFF) == 0) {
        unsigned b = rd(w);
        w->tag = b << 1 | 1;
        return b >> 7;
    }
    return w->tag >> 8;
}

static uint32_t gamma(Walk *w)      /* interlaced Elias gamma: a data bit, then a stop bit */
{
    uint32_t v = 1;
    for (;;) {
        v = v << 1 | bit(w);
        if (bit(w))
            return v;
        if (v > 0x02000000) {
            w->bad = 1;
            return 0;
        }
    }
}

static int walk(Walk *w)
{
    size_t pos = 0;                 /* the output position, in decoded bytes */
    uint32_t last = 1, off, n;
    for (;;) {
        if (w->bad || w->out->bad)
            return -1;
        if (bit(w)) {               /* a literal */
            if (!w->rebuild)
                put1(w->out, rd(w));
            else if (pos < w->img_n)
                put1(w->out, w->img[pos]);
            else
                return -1;
            pos++;
            continue;
        }
        uint32_t g = gamma(w);      /* a match: its offset ... */
        if (g == REUSE)
            off = last;
        else {
            uint32_t raw = (g << 8) + rd(w);    /* 32-bit, as the device computes it */
            if (raw == BIAS)
                break;              /* the end marker */
            off = last = raw - BIAS;
        }
        n = 2 * bit(w);             /* ... and its length */
        n += bit(w);
        if (!n)
            n = gamma(w) + 2;
        n += (off > FAR) + 1;
        if (w->bad || off == 0 || off > pos)
            return -1;
        if (!w->rebuild)
            for (size_t from = w->out->n - off, i = 0; i < n && !w->out->bad; i++)
                put1(w->out, w->out->p[from + i]);
        pos += n;
    }
    if (w->in != w->end || (w->rebuild && pos != w->img_n))
        return -1;
    return 0;
}

/* ---- the stock file ---- */

typedef struct {
    const uint8_t *raw;
    size_t raw_n;
    Buf stream;                     /* the decoded transport stream: u32 length, u32 checksum, the container */
    const uint8_t *cont;            /* the ELE3 container, in stream */
    uint32_t total, nsec, data_start;
    struct { uint32_t sid, off, len, dest; size_t at, n; } sec[16];   /* at, n: its decoded bytes in pool */
    Buf pool;                       /* every section decoded, in table order, then the stream */
} Stock;

/* The .syx: a 16-byte framing message, data messages of 128 bytes (F0 00 20 3C dev 00 7E, a 3-byte counter, 116
 * bytes of 8-in-7 payload, a checksum, F7), the closing framing message. -> s->stream, the table, the pool. */
static int stock_read(Stock *s)
{
    const uint8_t *r = s->raw;
    size_t n = s->raw_n, msgs;
    if (n < 32 + 128 || (n - 32) % 128 || r[0] != 0xF0 || r[15] != 0xF7 || r[n - 16] != 0xF0)
        return -1;
    msgs = (n - 32) / 128;
    for (size_t i = 0; i < msgs; i++) {
        const uint8_t *m = r + 16 + 128 * i, *p = m + 10;
        if (m[0] != 0xF0 || m[6] != 0x7E || m[127] != 0xF7)
            return -1;
        for (int g = 0; g < 116; g += 8)        /* a marker byte with the high bits, then up to 7 bytes */
            for (int k = 1; k < 8 && g + k < 116; k++)
                put1(&s->stream, p[g + k] | ((p[g] >> (7 - k)) & 1) << 7);
    }
    if (s->stream.bad || s->stream.n < 12)
        return -1;
    s->cont = s->stream.p + 8;
    s->total = be32(s->stream.p);
    if (s->total > s->stream.n - 8 || memcmp(s->cont, "ELE3", 4))
        return -1;
    s->nsec = be32(s->cont + 0x1C);
    if (s->nsec == 0 || s->nsec > 16 || 0x20 + 16 * s->nsec > s->total)
        return -1;
    s->data_start = s->total;
    for (uint32_t i = 0; i < s->nsec; i++) {
        const uint8_t *e = s->cont + 0x20 + 16 * i;
        s->sec[i].sid = be32(e);
        s->sec[i].off = be32(e + 4);
        s->sec[i].len = be32(e + 8);
        s->sec[i].dest = be32(e + 12);
        if (s->sec[i].off > s->total || s->sec[i].len > s->total - s->sec[i].off)
            return -1;
        if (s->sec[i].off < s->data_start)
            s->data_start = s->sec[i].off;
    }
    /* the pool: each section decoded as syx.unstore does it (packed: u32 length, u32 byte sum, the stream;
     * a zero byte sum: an 8-byte header, then the bytes; else the bytes as stored) */
    for (uint32_t i = 0; i < s->nsec; i++) {
        const uint8_t *d = s->cont + s->sec[i].off;
        uint32_t len = s->sec[i].len, ln = len >= 8 ? be32(d) : 0, sum = 0;
        if (s->stream.bad)
            return -1;
        s->sec[i].at = s->pool.n;
        if (len >= 8 && ln <= len - 8) {
            for (uint32_t k = 0; k < ln; k++)
                sum += d[8 + k];
        }
        if (len >= 8 && ln <= len - 8 && sum == be32(d + 4)) {
            Walk w = { d + 8, d + 8 + ln, NULL, 0, &s->pool, 0, 0, 0 };
            if (walk(&w))
                return -1;
        } else if (len >= 8 && be32(d + 4) == 0)
            put(&s->pool, d + 8, len - 8);
        else
            put(&s->pool, d, len);
        s->sec[i].n = s->pool.n - s->sec[i].at;
    }
    put(&s->pool, s->stream.p, s->total + 8);
    return s->stream.bad || s->pool.bad ? -1 : 0;
}

static int stock_sec(const Stock *s, uint32_t sid)     /* -> the table index of section sid, or -1 */
{
    for (uint32_t i = 0; i < s->nsec; i++)
        if (s->sec[i].sid == sid)
            return (int)i;
    return -1;
}

/* the seal key, as the bootstrap derives it (syx.seal_key): after the seed and a NUL in section 2, a 32-byte
 * constant C; the key is C ^ sha256(seed) ^ sha256(the seed reversed) */
static int seal_key(const Stock *s, uint8_t key[32])
{
    static const char seed[] = SEAL_SEED;
    size_t sl = sizeof seed, found = 0;         /* sl counts the NUL */
    int i = stock_sec(s, SEAL_SEC);
    if (i < 0)
        return -1;
    const uint8_t *d = s->pool.p + s->sec[i].at, *c = NULL;
    for (size_t k = 0; k + sl + 32 <= s->sec[i].n; k++)
        if (!memcmp(d + k, seed, sl)) {
            c = d + k + sl;
            found++;
        }
    if (found != 1)
        return -1;
    uint8_t a[32], b[32], rev[sizeof seed - 1];
    for (size_t k = 0; k < sl - 1; k++)
        rev[k] = (uint8_t)seed[sl - 2 - k];
    sha256((const uint8_t *)seed, sl - 1, a);
    sha256(rev, sl - 1, b);
    for (int k = 0; k < 32; k++)
        key[k] = c[k] ^ a[k] ^ b[k];
    return 0;
}

/* ---- the pack ---- */

typedef struct {
    uint32_t sid, stored_len, stream_len, image_len, skel_len;
    const uint8_t *part[4];         /* iops, ilit, sops, slit */
    uint32_t part_n[4];
} PSec;

typedef struct {
    char name[17];
    const uint8_t *version, *stock_sha, *out_sha;
    uint32_t out_len, nsec;
    PSec sec[8];
    dn2p_info info;
} Patch;

static void hex(const uint8_t *h, int n, char *out)   /* n bytes of a hash as hex, NUL-terminated */
{
    for (int i = 0; i < n; i++)
        snprintf(out + 2 * i, 3, "%02x", h[i]);
}

/* the metadata: "key=value" lines; a value longer than its field is cut */
static void meta_read(const uint8_t *m, size_t n, dn2p_info *f)
{
    static const struct { const char *key; size_t at, size; } keys[] = {
        { "title", offsetof(dn2p_info, title), sizeof f->title },
        { "description", offsetof(dn2p_info, description), sizeof f->description },
        { "record", offsetof(dn2p_info, record), sizeof f->record },
        { "tested_on_unit", offsetof(dn2p_info, tested_on_unit), sizeof f->tested_on_unit },
        { "brickcheck", offsetof(dn2p_info, brickcheck), sizeof f->brickcheck },
        { "effects", offsetof(dn2p_info, effects), sizeof f->effects },
        { "base", offsetof(dn2p_info, base), sizeof f->base },
    };
    char line[256], *eq;
    f->dsp_code_bytes = f->dsp_state_bytes = -1;
    f->dsp_load_percent = -1;
    for (size_t i = 0; i < n;) {
        size_t j = i;
        while (j < n && m[j] != '\n')
            j++;
        snprintf(line, sizeof line, "%.*s", (int)(j - i), (const char *)m + i);
        i = j + 1;
        if (!(eq = strchr(line, '=')))
            continue;
        *eq++ = 0;
        for (size_t k = 0; k < sizeof keys / sizeof keys[0]; k++)
            if (!strcmp(line, keys[k].key))
                snprintf((char *)f + keys[k].at, keys[k].size, "%s", eq);
        if (*eq && !strcmp(line, "dsp_code_bytes"))
            f->dsp_code_bytes = strtol(eq, NULL, 10);
        if (*eq && !strcmp(line, "dsp_state_bytes"))
            f->dsp_state_bytes = strtol(eq, NULL, 10);
        if (*eq && !strcmp(line, "dsp_load_percent"))
            f->dsp_load_percent = strtod(eq, NULL);
    }
}

static int patch_read(const uint8_t *b, size_t n, Patch *p)
{
    memset(p, 0, sizeof *p);
    if (!b || n < 104 || memcmp(b, "DN2P", 4) || be32(b + 4) != 2)
        return -1;
    memcpy(p->name, b + 8, 16);
    p->version = b + 24;
    p->stock_sha = b + 28;
    p->out_sha = b + 60;
    p->out_len = be32(b + 92);
    p->nsec = be32(b + 96);
    uint32_t meta = be32(b + 100);
    if (p->nsec > 8 || 104 + 36 * p->nsec > n || meta > n - (104 + 36 * p->nsec))
        return -1;
    size_t at = 104 + 36 * p->nsec;
    memcpy(p->info.id, p->name, sizeof p->info.id);
    hex(p->out_sha, 32, p->info.build_sha256);
    hex(p->stock_sha, 32, p->info.stock_sha256);
    meta_read(b + at, meta, &p->info);
    at += meta;
    for (uint32_t i = 0; i < p->nsec; i++) {
        const uint8_t *e = b + 104 + 36 * i;
        PSec *c = &p->sec[i];
        c->sid = be32(e);
        c->stored_len = be32(e + 4);
        c->stream_len = be32(e + 8);
        c->image_len = be32(e + 12);
        c->skel_len = be32(e + 16);
        if (c->stream_len > c->stored_len - 8 || c->stored_len < 8)
            return -1;
        for (int k = 0; k < 4; k++) {
            c->part_n[k] = be32(e + 20 + 4 * k);
            if (c->part_n[k] > n - at)
                return -1;
            c->part[k] = b + at;
            at += c->part_n[k];
        }
    }
    return at == n && p->name[0] ? 0 : -1;
}

static int uvar(const uint8_t *p, size_t n, size_t *i, uint64_t *v)
{
    *v = 0;
    for (int s = 0; s < 64; s += 7) {
        if (*i >= n)
            return -1;
        uint8_t b = p[(*i)++];
        *v |= (uint64_t)(b & 0x7F) << s;
        if (!(b & 0x80))
            return 0;
    }
    return -1;
}

/* ops -> out, n bytes: copies from the pool (at cur + a zigzag delta) and runs of lit (mkpatch.py's run_ops) */
static int run_ops(const uint8_t *ops, size_t ops_n, const uint8_t *lit, size_t lit_n, const Buf *pool,
                   int64_t cur, uint8_t *out, size_t n)
{
    size_t i = 0, li = 0, at = 0;
    uint64_t v, z;
    while (i < ops_n) {
        if (uvar(ops, ops_n, &i, &v))
            return -1;
        uint64_t c = v >> 2;
        if (c > n - at)
            return -1;
        if ((v & 3) == 0) {         /* a copy */
            if (uvar(ops, ops_n, &i, &z))
                return -1;
            cur += z & 1 ? -(int64_t)((z + 1) >> 1) : (int64_t)(z >> 1);
            if (cur < 0 || (uint64_t)cur > pool->n || c > pool->n - (uint64_t)cur)
                return -1;
            memcpy(out + at, pool->p + cur, c);
        } else if ((v & 3) == 1) {  /* our bytes */
            if (c > lit_n - li)
                return -1;
            memcpy(out + at, lit + li, c);
            li += c;
        } else
            return -1;
        at += c;
        cur += c;
    }
    return at == n && li == lit_n ? 0 : -1;
}

/* one replaced section -> its stored bytes: u32 stream length, u32 byte sum, the packed stream, zero padding */
static int make_section(const Stock *s, const PSec *c, Buf *stored)
{
    int i = stock_sec(s, c->sid);
    uint8_t *img = malloc(c->image_len + 1), *skel = malloc(c->skel_len + 1);
    Buf stream = { 0 };
    int bad = i < 0 || !img || !skel
        || run_ops(c->part[0], c->part_n[0], c->part[1], c->part_n[1], &s->pool, i < 0 ? 0 : (int64_t)s->sec[i].at,
                   img, c->image_len)
        || run_ops(c->part[2], c->part_n[2], c->part[3], c->part_n[3], &s->pool, 0, skel, c->skel_len);
    if (!bad) {
        Walk w = { skel, skel + c->skel_len, img, c->image_len, &stream, 0, 1, 0 };
        bad = walk(&w) || stream.n != c->stream_len;
    }
    if (!bad) {
        uint8_t h[8];
        uint32_t sum = 0;
        for (size_t k = 0; k < stream.n; k++)
            sum += stream.p[k];
        set32(h, (uint32_t)stream.n);
        set32(h + 4, sum);
        put(stored, h, 8);
        put(stored, stream.p, stream.n);
        bad = grow(stored, c->stored_len - 8 - stream.n);
        if (!bad) {
            memset(stored->p + stored->n, 0, c->stored_len - 8 - stream.n);
            stored->n = c->stored_len;
        }
    }
    free(img);
    free(skel);
    free(stream.p);
    return bad ? -1 : 0;
}

static void framing(Buf *o, const uint8_t *m, size_t n)    /* a framing message with the data-message count */
{
    put(o, m, 16);
    if (o->bad)
        return;
    o->p[o->n - 4] = (n >> 14) & 0x7F;
    o->p[o->n - 3] = (n >> 7) & 0x7F;
    o->p[o->n - 2] = n & 0x7F;
}

/* the patch on the stock file -> out: the build's .syx (mkpatch.py's apply) */
static int build(const Stock *s, const Patch *p, Buf *out)
{
    Buf stored[8] = { { 0 } }, c = { 0 }, st = { 0 };
    int bad = 0;
    for (uint32_t i = 0; i < p->nsec && !bad; i++)
        bad = make_section(s, &p->sec[i], &stored[i]);
    uint8_t key[32];
    if (!bad && !seal_key(s, key)) {
        /* the container: stock's up to its first section, the version field, then each section in table order
         * with its table entry, padded to 16 bytes; then the seal over all of it */
        put(&c, s->cont, s->data_start);
        memcpy(c.p + 0x14, p->version, 4);
        for (uint32_t i = 0; i < s->nsec; i++) {
            const uint8_t *d = s->cont + s->sec[i].off;
            size_t n = s->sec[i].len;
            for (uint32_t k = 0; k < p->nsec; k++)
                if (p->sec[k].sid == s->sec[i].sid) {
                    d = stored[k].p;
                    n = stored[k].n;
                }
            uint8_t *e = c.p + 0x20 + 16 * i;
            set32(e + 4, (uint32_t)c.n);
            set32(e + 8, (uint32_t)n);
            put(&c, d, n);
            while (c.n % 16)
                put1(&c, 0);
        }
        uint8_t mac[32];
        hmac_sha256(key, c.p, c.n, mac);
        put(&c, mac, 32);
        /* the stream: the container's length and checksum (the sum of word index ^ word), then the container */
        uint8_t h[8];
        uint32_t sum = 0;
        for (size_t i = 0; i + 4 <= c.n; i += 4)
            sum += (uint32_t)(i / 4 + 1) ^ be32(c.p + i);
        set32(h, (uint32_t)c.n);
        set32(h + 4, sum);
        put(&st, h, 8);
        put(&st, c.p, c.n);
        /* the messages: 101 bytes each (the last one zero-padded), 8-in-7, with a counter and a checksum */
        size_t msgs = (st.n + CHUNK - 1) / CHUNK;
        unsigned k = s->raw[8];     /* the transfer constant, in the framing message */
        framing(out, s->raw, msgs);
        if (grow(&st, CHUNK))
            bad = 1;
        else
            memset(st.p + st.n, 0, CHUNK);
        for (size_t i = 0; i < msgs && !bad; i++) {
            uint8_t m[128] = { 0xF0, 0x00, 0x20, 0x3C, s->raw[4], 0x00, 0x7E }, *q = m + 10;
            uint32_t cn = FIRST_COUNTER + (uint32_t)i, t = k;
            const uint8_t *d = st.p + i * CHUNK;
            m[7] = (cn >> 14) & 0x7F;
            m[8] = (cn >> 7) & 0x7F;
            m[9] = cn & 0x7F;
            for (int g = 0; g < CHUNK; g += 7, q += 8)
                for (int b = 0; b < 7 && g + b < CHUNK; b++) {
                    q[0] |= (d[g + b] >> 7) << (6 - b);
                    q[1 + b] = d[g + b] & 0x7F;
                }
            for (int b = 0; b < 119; b++)
                t += m[7 + b] ^ (b + k);
            m[126] = t & 0x7F;
            m[127] = 0xF7;
            put(out, m, 128);
        }
        framing(out, s->raw + s->raw_n - 16, msgs);
    } else
        bad = 1;
    for (int i = 0; i < 8; i++) {
        bad |= stored[i].bad;
        free(stored[i].p);
    }
    bad |= c.bad | st.bad | out->bad;
    free(c.p);
    free(st.p);
    return bad ? -1 : 0;
}

/* ---- the API (dn2patch.h) ---- */

/* msg: a plain-English sentence or two for anyone, a newline, then the precise reason (for a bug report) */
static void say(char *msg, size_t msgn, const char *fmt, ...)
{
    va_list ap;
    if (!msgn)
        return;
    va_start(ap, fmt);
    vsnprintf(msg, msgn, fmt, ap);
    va_end(ap);
}

/* A wrong file: what it is, if it can tell (a Digitone II OS file's version field is in its first data message). */
static void describe(const uint8_t *r, size_t n, const Patch *p, const char *sha, char *msg, size_t msgn)
{
    char v[5] = { 0 };
    int ele3 = 0;
    if (n >= 4 && !memcmp(r, "PK\3\4", 4)) {
        say(msg, msgn, "That's the .zip file. Double-click it in Finder to unzip it, then use the "
            DN2P_STOCK_NAME " file inside.\nthe input is a zip archive (%zu bytes)", n);
        return;
    }
    if (n >= 16 + 128 && r[0] == 0xF0 && r[1] == 0 && r[2] == 0x20 && r[3] == 0x3C && r[4] == 0x15 && r[16] == 0xF0) {
        uint8_t d[32] = { 0 };
        const uint8_t *q = r + 16 + 10;
        for (int g = 0, j = 0; j < 32; g += 8)  /* 8-in-7: 5 groups give the first 35 bytes */
            for (int k = 1; k < 8 && j < 32; k++)
                d[j++] = q[g + k] | ((q[g] >> (7 - k)) & 1) << 7;
        memcpy(v, d + 28, 4);
        for (int i = 0; i < 4; i++)
            if (v[i] < 0x20 || v[i] > 0x7E)
                v[i] = '?';
        ele3 = !memcmp(d + 8, "ELE3", 4);
    }
    int elektron = v[0] >= '0' && v[0] <= '9' && v[1] == '.' && v[2] >= '0' && v[2] <= '9';
    if (ele3 && !memcmp(v, p->version, 4))
        say(msg, msgn, "That's %s.syx already, the file this makes. Use Elektron's " DN2P_STOCK_NAME " instead.\n"
            "the input is the %s build (%zu bytes)", p->name, p->name, n);
    else if (ele3 && !elektron)
        say(msg, msgn, "That's %s, a custom OS, not Elektron's OS 1.12 file. Use Elektron's " DN2P_STOCK_NAME
            " instead.\nversion field %s, %zu bytes, sha256 %s", v, v, n, sha);
    else if (ele3 && !memcmp(v, "1.12", 4))
        say(msg, msgn, "That's Elektron's OS 1.12 file, but it is damaged or incomplete. Download it again.\n"
            "version field 1.12, %zu bytes, sha256 %s (Elektron's file: %s)", n, sha, p->info.stock_sha256);
    else if (ele3)
        say(msg, msgn, "That's Digitone II OS %s. This needs OS 1.12: download it from Elektron's Digitone II page.\n"
            "version field %s, %zu bytes, sha256 %s", v, v, n, sha);
    else
        say(msg, msgn, "That's not Elektron's Digitone II OS 1.12 file. Use the " DN2P_STOCK_NAME " file from "
            "Elektron's download (Digitone_II_OS1.12_dist.zip).\n%zu bytes, sha256 %s (Elektron's file: %s)",
            n, sha, p->info.stock_sha256);
}

int dn2patch_info(const uint8_t *pack, size_t packn, dn2p_info *info)
{
    Patch p;
    if (patch_read(pack, packn, &p))
        return -1;
    *info = p.info;
    return 0;
}

int dn2patch_run(const uint8_t *pack, size_t packn, const uint8_t *in, size_t n, dn2p_check check, void *ctx,
                 uint8_t **out, size_t *outn, char *msg, size_t msgn)
{
    Patch p;
    uint8_t h[32];
    char sin[65], sout[65], why[256] = "";
    *out = NULL;
    *outn = 0;
    if (patch_read(pack, packn, &p)) {
        say(msg, msgn, "This copy of the patcher is damaged. Download it again.\nthe embedded patch does not parse");
        return DN2P_FAILED;
    }
    sha256(in, n, h);
    hex(h, 32, sin);
    if (memcmp(h, p.stock_sha, 32)) {
        describe(in, n, &p, sin, msg, msgn);
        return DN2P_NOT_STOCK;
    }
    Stock s;
    Buf o = { 0 };
    memset(&s, 0, sizeof s);
    s.raw = in;
    s.raw_n = n;
    int bad = stock_read(&s) || build(&s, &p, &o);
    int oom = s.stream.bad || s.pool.bad || o.bad;
    free(s.stream.p);
    free(s.pool.p);
    if (bad) {
        free(o.p);
        if (oom)
            say(msg, msgn, "There wasn't enough memory to make %s.syx, so nothing was saved. Close other apps and "
                "try again.\nan allocation failed (%s)", p.name, p.name);
        else
            say(msg, msgn, "Something went wrong while making %s.syx, so nothing was saved. Please report this.\n"
                "decoding the input or rebuilding %s failed (input sha256 %s)", p.name, p.name, sin);
        return oom ? DN2P_NOMEM : DN2P_FAILED;
    }
    sha256(o.p, o.n, h);
    hex(h, 32, sout);
    if (o.n != p.out_len || memcmp(h, p.out_sha, 32)) {
        free(o.p);
        say(msg, msgn, "Something went wrong: the result is not exactly the %s we tested, so nothing was saved. "
            "Please report this.\nresult %zu bytes, sha256 %s; tested build %u bytes, sha256 %s",
            p.name, o.n, sout, p.out_len, p.info.build_sha256);
        return DN2P_FAILED;
    }
    if (check && check(o.p, o.n, &p.info, why, sizeof why, ctx)) {
        free(o.p);
        say(msg, msgn, "A check on %s.syx failed, so nothing was saved: %s\ncheck stage refused %s (sha256 %s)",
            p.name, why, p.name, sout);
        return DN2P_CHECK_FAILED;
    }
    *out = o.p;
    *outn = o.n;
    say(msg, msgn, "Checked: this is Elektron's original OS 1.12 file. Made %s.syx, exactly the build we tested.\n"
        "input sha256 %s; %s.syx %zu bytes, sha256 %s", p.name, sin, p.name, o.n, sout);
    return DN2P_OK;
}

void dn2patch_free(uint8_t *out) { free(out); }

int dn2patch_is_build(const uint8_t *pack, size_t packn, const uint8_t *in, size_t n)
{
    Patch p;
    uint8_t h[32];
    if (patch_read(pack, packn, &p) || n != p.out_len)
        return 0;
    sha256(in, n, h);
    return !memcmp(h, p.out_sha, 32);
}
