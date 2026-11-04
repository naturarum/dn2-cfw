// SPDX-License-Identifier: GPL-2.0-or-later
// dn2patch.js - the web patcher's core: a line-for-line port of ../dn2patch.c (the core of Digiwerk Patcher and
// digiwerk-patch), so a browser turns the user's own copy of Elektron's Digitone II OS 1.12 file into one of our
// builds, on the user's computer, without our ever handing out Elektron's firmware. test.mjs checks that it makes
// every release build byte for byte, as the C core does.
//
// A pack (made by ../mkpatch.py, whose docstring has its format) holds our bytes, copy instructions into the stock
// file, codec control bits and its metadata. run() does mkpatch.py's apply():
//   1. refuse the input unless its sha256 is that of Elektron's 1.12 file;
//   2. decode it: SysEx messages, 8-in-7, the ELE3 container, every section depacked: the pool;
//   3. per replaced section: its decoded image and its skeleton, from their ops; the packed stream, by walking the
//      skeleton with the decoder and taking each literal from the image;
//   4. the container as stock but the version field and those sections: the table, the 16-byte padding, the
//      HMAC-SHA256 seal (its key from the stock bootstrap), the preamble's content checksum; then the SysEx messages
//      with their counters and checksums, and the framing messages' count;
//   5. hand the result over only if its sha256 is the build's.

export const OK = 0;                // out holds the build, byte for byte the one we tested
export const NOT_STOCK = 1;         // the input is not Elektron's 1.12 file (msg says what it is, when it can)
export const FAILED = 2;            // the build could not be made, or is not the one we tested: nothing to write
export const STOCK_NAME = 'Digitone_II_OS1.12.syx';

const CHUNK = 101;                  // decoded bytes per data message
const FIRST_COUNTER = 242;          // the first data message's counter
const BIAS = 767;                   // the codec's constants (codec/elz.py)
const REUSE = 2;
const FAR = 3328;
const SEAL_SEED = 'Multiplier';     // the seal key's seed, in the bootstrap (devices/digitone_mk2.py)
const SEAL_SEC = 2;

class Fail extends Error {}         // a malformed input or pack: the C core's -1

// ---- a growable byte buffer ----

class Buf {
    constructor(cap = 1 << 16) { this.p = new Uint8Array(cap); this.n = 0; }
    grow(more) {
        if (more <= this.p.length - this.n)
            return;
        let cap = this.p.length || 1 << 16;
        while (cap - this.n < more)
            cap *= 2;
        const p = new Uint8Array(cap);
        p.set(this.p.subarray(0, this.n));
        this.p = p;
    }
    put(src) {
        if (src.length) {
            this.grow(src.length);
            this.p.set(src, this.n);
            this.n += src.length;
        }
    }
    put1(v) {
        this.grow(1);
        this.p[this.n++] = v;
    }
    bytes() { return this.p.subarray(0, this.n); }
}

const be32 = (p, i) => (p[i] << 24 | p[i + 1] << 16 | p[i + 2] << 8 | p[i + 3]) >>> 0;

function set32(p, i, v) {
    p[i] = v >>> 24;
    p[i + 1] = v >>> 16;
    p[i + 2] = v >>> 8;
    p[i + 3] = v;
}

const eq = (a, ai, b, bi, n) => {
    for (let k = 0; k < n; k++)
        if (a[ai + k] !== b[bi + k])
            return false;
    return true;
};

// ---- SHA-256 (FIPS 180-4) and HMAC-SHA256 (RFC 2104) ----

const K256 = new Uint32Array([
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
    0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
    0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
    0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
    0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
]);

const ror = (x, n) => (x >>> n | x << (32 - n)) >>> 0;

class Sha {
    constructor() {
        this.h = new Uint32Array([0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
                                  0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19]);
        this.len = 0;
        this.blk = new Uint8Array(64);
        this.n = 0;
        this.w = new Uint32Array(64);
    }
    block(p, at) {
        const w = this.w, h = this.h;
        for (let i = 0; i < 16; i++)
            w[i] = be32(p, at + 4 * i);
        for (let i = 16; i < 64; i++) {
            const s0 = ror(w[i - 15], 7) ^ ror(w[i - 15], 18) ^ (w[i - 15] >>> 3);
            const s1 = ror(w[i - 2], 17) ^ ror(w[i - 2], 19) ^ (w[i - 2] >>> 10);
            w[i] = w[i - 16] + s0 + w[i - 7] + s1;
        }
        let a = h[0], b = h[1], c = h[2], d = h[3], e = h[4], f = h[5], g = h[6], hh = h[7];
        for (let i = 0; i < 64; i++) {
            const t1 = (hh + (ror(e, 6) ^ ror(e, 11) ^ ror(e, 25)) + ((e & f) ^ (~e & g)) + K256[i] + w[i]) >>> 0;
            const t2 = ((ror(a, 2) ^ ror(a, 13) ^ ror(a, 22)) + ((a & b) ^ (a & c) ^ (b & c))) >>> 0;
            hh = g; g = f; f = e; e = (d + t1) >>> 0;
            d = c; c = b; b = a; a = (t1 + t2) >>> 0;
        }
        h[0] += a; h[1] += b; h[2] += c; h[3] += d; h[4] += e; h[5] += f; h[6] += g; h[7] += hh;
    }
    add(p, at = 0, n = p.length - at) {
        this.len += n;
        if (this.n) {                               // fill the pending block first
            const k = Math.min(64 - this.n, n);
            this.blk.set(p.subarray(at, at + k), this.n);
            this.n += k;
            at += k;
            n -= k;
            if (this.n < 64)
                return;
            this.block(this.blk, 0);
            this.n = 0;
        }
        for (; n >= 64; at += 64, n -= 64)
            this.block(p, at);
        this.blk.set(p.subarray(at, at + n), 0);
        this.n = n;
    }
    end() {
        const bits = this.len * 8, pad = new Uint8Array(72);
        pad[0] = 0x80;
        const k = (this.n < 56 ? 56 : 120) - this.n;
        for (let i = 0; i < 8; i++)                 // a 64-bit big-endian bit count, exact up to 2^53
            pad[k + i] = Math.floor(bits / 2 ** (56 - 8 * i)) & 0xff;
        this.add(pad, 0, k + 8);
        const out = new Uint8Array(32);
        for (let i = 0; i < 8; i++)
            set32(out, 4 * i, this.h[i]);
        return out;
    }
}

export function sha256(p) {
    const s = new Sha();
    s.add(p);
    return s.end();
}

function hmacSha256(key, p) {
    let inner;
    for (let pass = 0; pass < 2; pass++) {
        const pad = new Uint8Array(64).fill(pass ? 0x5c : 0x36);
        for (let i = 0; i < 32; i++)
            pad[i] ^= key[i];
        const s = new Sha();
        s.add(pad);
        s.add(pass ? inner : p);
        if (pass)
            return s.end();
        inner = s.end();
    }
}

export const hex = h => Array.from(h, b => b.toString(16).padStart(2, '0')).join('');

// ---- the codec's decoder (codec/elz.py; the bootstrap's depacker at 0x80000432), in two modes ----
// depack:  in is a packed stream; every byte read comes from it, literals too; out gets the decoded bytes.
// rebuild: in is a skeleton (a packed stream without its literals); each literal comes from img at the output
//          position instead; out gets the packed stream (every byte the decoder reads, in order).

class Walk {
    constructor(src, at, end, img, out, rebuild) {
        Object.assign(this, { src, at, end, img, out, rebuild, tag: 0 });
    }
    rd() {                          // the next byte of in: a tag or offset byte (or, depacking, a literal)
        if (this.at === this.end)
            throw new Fail('the stream ended early');
        const v = this.src[this.at++];
        if (this.rebuild)
            this.out.put1(v);
        return v;
    }
    bit() {
        this.tag = (this.tag << 1) & 0x1ff;
        if ((this.tag & 0xff) === 0) {
            const b = this.rd();
            this.tag = b << 1 | 1;
            return b >> 7;
        }
        return this.tag >> 8;
    }
    gamma() {                       // interlaced Elias gamma: a data bit, then a stop bit
        let v = 1;
        for (;;) {
            v = (v << 1 | this.bit()) >>> 0;
            if (this.bit())
                return v;
            if (v > 0x02000000)
                throw new Fail('a gamma code too long');
        }
    }
    walk() {
        let pos = 0, last = 1, off, n;              // pos: the output position, in decoded bytes
        const out = this.out;
        for (;;) {
            if (this.bit()) {                       // a literal
                if (!this.rebuild)
                    out.put1(this.rd());
                else if (pos < this.img.length)
                    out.put1(this.img[pos]);
                else
                    throw new Fail('a literal past the image');
                pos++;
                continue;
            }
            const g = this.gamma();                 // a match: its offset ...
            if (g === REUSE)
                off = last;
            else {
                const raw = (g * 256 + this.rd()) >>> 0;   // 32-bit, as the device computes it
                if (raw === BIAS)
                    break;                          // the end marker
                off = last = (raw - BIAS) >>> 0;
            }
            n = 2 * this.bit();                     // ... and its length
            n += this.bit();
            if (!n)
                n = this.gamma() + 2;
            n += (off > FAR ? 1 : 0) + 1;
            if (off === 0 || off > pos)
                throw new Fail('a match before the start');
            if (!this.rebuild) {
                out.grow(n);
                for (let from = out.n - off, i = 0; i < n; i++)
                    out.p[out.n++] = out.p[from + i];
            }
            pos += n;
        }
        if (this.at !== this.end || (this.rebuild && pos !== this.img.length))
            throw new Fail('the stream does not end where it should');
    }
}

// ---- the stock file ----

// The .syx: a 16-byte framing message, data messages of 128 bytes (F0 00 20 3C dev 00 7E, a 3-byte counter, 116
// bytes of 8-in-7 payload, a checksum, F7), the closing framing message. -> the stream, the table, the pool.
function stockRead(r) {
    const n = r.length;
    if (n < 32 + 128 || (n - 32) % 128 || r[0] !== 0xF0 || r[15] !== 0xF7 || r[n - 16] !== 0xF0)
        throw new Fail('not a framed SysEx file');
    const msgs = (n - 32) / 128, stream = new Buf(msgs * 101 + 16);
    for (let i = 0; i < msgs; i++) {
        const m = 16 + 128 * i, p = m + 10;
        if (r[m] !== 0xF0 || r[m + 6] !== 0x7E || r[m + 127] !== 0xF7)
            throw new Fail('a data message is malformed');
        for (let g = 0; g < 116; g += 8)            // a marker byte with the high bits, then up to 7 bytes
            for (let k = 1; k < 8 && g + k < 116; k++)
                stream.put1(r[p + g + k] | ((r[p + g] >> (7 - k)) & 1) << 7);
    }
    const st = stream.bytes();
    if (st.length < 12)
        throw new Fail('too short');
    const s = { raw: r, stream: st, cont: 8, total: be32(st, 0), sec: [] };
    if (s.total > st.length - 8 || !eq(st, 8, [0x45, 0x4c, 0x45, 0x33], 0, 4))      // "ELE3"
        throw new Fail('no ELE3 container');
    s.nsec = be32(st, s.cont + 0x1C);
    if (s.nsec === 0 || s.nsec > 16 || 0x20 + 16 * s.nsec > s.total)
        throw new Fail('a bad section table');
    s.dataStart = s.total;
    for (let i = 0; i < s.nsec; i++) {
        const e = s.cont + 0x20 + 16 * i;
        const c = { sid: be32(st, e), off: be32(st, e + 4), len: be32(st, e + 8), dest: be32(st, e + 12) };
        if (c.off > s.total || c.len > s.total - c.off)
            throw new Fail('a section outside the container');
        s.dataStart = Math.min(s.dataStart, c.off);
        s.sec.push(c);
    }
    // the pool: each section decoded as syx.unstore does it (packed: u32 length, u32 byte sum, the stream;
    // a zero byte sum: an 8-byte header, then the bytes; else the bytes as stored)
    const pool = new Buf(8 << 20);
    for (const c of s.sec) {
        const d = s.cont + c.off, len = c.len, ln = len >= 8 ? be32(st, d) : 0;
        let sum = 0;
        c.at = pool.n;
        if (len >= 8 && ln <= len - 8)
            for (let k = 0; k < ln; k++)
                sum = (sum + st[d + 8 + k]) >>> 0;
        if (len >= 8 && ln <= len - 8 && sum === be32(st, d + 4))
            new Walk(st, d + 8, d + 8 + ln, null, pool, false).walk();
        else if (len >= 8 && be32(st, d + 4) === 0)
            pool.put(st.subarray(d + 8, d + len));
        else
            pool.put(st.subarray(d, d + len));
        c.n = pool.n - c.at;
    }
    pool.put(st.subarray(0, s.total + 8));
    s.pool = pool.bytes();
    return s;
}

const stockSec = (s, sid) => s.sec.findIndex(c => c.sid === sid);   // the table index of section sid, or -1

// the seal key, as the bootstrap derives it (syx.seal_key): after the seed and a NUL in section 2, a 32-byte
// constant C; the key is C ^ sha256(seed) ^ sha256(the seed reversed)
function sealKey(s) {
    const seed = new TextEncoder().encode(SEAL_SEED), sl = seed.length + 1;        // sl counts the NUL
    const i = stockSec(s, SEAL_SEC);
    if (i < 0)
        throw new Fail('no bootstrap section');
    const d = s.sec[i].at, n = s.sec[i].n, pool = s.pool;
    let c = -1, found = 0;
    for (let k = 0; k + sl + 32 <= n; k++)
        if (pool[d + k + sl - 1] === 0 && eq(pool, d + k, seed, 0, sl - 1)) {
            c = d + k + sl;
            found++;
        }
    if (found !== 1)
        throw new Fail('the seal seed is not where it should be');
    const a = sha256(seed), b = sha256(seed.slice().reverse()), key = new Uint8Array(32);
    for (let k = 0; k < 32; k++)
        key[k] = pool[c + k] ^ a[k] ^ b[k];
    return key;
}

// ---- the pack ----

const META = ['title', 'description', 'record', 'tested_on_unit', 'brickcheck', 'effects', 'base'];
const META_SIZE = { title: 64, description: 160, record: 160, tested_on_unit: 32, brickcheck: 160, effects: 64,
                    base: 64 };

// the metadata: "key=value" lines; a value longer than its field is cut (as the C core's fields)
function metaRead(m) {
    const f = { dsp_code_bytes: -1, dsp_state_bytes: -1, dsp_load_percent: -1 };
    for (const k of META)
        f[k] = '';
    const cut = (s, size) => {                      // at most size - 1 bytes of UTF-8, as snprintf cuts
        const b = new TextEncoder().encode(s);
        return b.length < size ? s : new TextDecoder().decode(b.subarray(0, size - 1)).replace(/�$/, '');
    };
    for (const line of new TextDecoder().decode(m).split('\n')) {
        const i = line.indexOf('=');
        if (i < 0)
            continue;
        const key = line.slice(0, i), v = line.slice(i + 1);
        if (key in META_SIZE)
            f[key] = cut(v, META_SIZE[key]);
        if (v && key === 'dsp_code_bytes')
            f.dsp_code_bytes = parseInt(v, 10);
        if (v && key === 'dsp_state_bytes')
            f.dsp_state_bytes = parseInt(v, 10);
        if (v && key === 'dsp_load_percent')
            f.dsp_load_percent = parseFloat(v);
    }
    return f;
}

function patchRead(b) {
    const n = b ? b.length : 0;
    if (!b || n < 104 || !eq(b, 0, [0x44, 0x4e, 0x32, 0x50], 0, 4) || be32(b, 4) !== 2)      // "DN2P", format 2
        throw new Fail('not a pack');
    const p = { name: new TextDecoder().decode(b.subarray(8, 24)).replace(/\0.*$/s, ''),
                version: b.subarray(24, 28), stockSha: b.subarray(28, 60), outSha: b.subarray(60, 92),
                outLen: be32(b, 92), nsec: be32(b, 96), sec: [] };
    const meta = be32(b, 100);
    if (p.nsec > 8 || 104 + 36 * p.nsec > n || meta > n - (104 + 36 * p.nsec))
        throw new Fail('a bad pack header');
    let at = 104 + 36 * p.nsec;
    p.info = { id: p.name, ...metaRead(b.subarray(at, at + meta)), build_sha256: hex(p.outSha),
               stock_sha256: hex(p.stockSha) };
    at += meta;
    for (let i = 0; i < p.nsec; i++) {
        const e = 104 + 36 * i;
        const c = { sid: be32(b, e), storedLen: be32(b, e + 4), streamLen: be32(b, e + 8), imageLen: be32(b, e + 12),
                    skelLen: be32(b, e + 16), part: [] };
        if (c.storedLen < 8 || c.streamLen > c.storedLen - 8)
            throw new Fail('a bad section entry');
        for (let k = 0; k < 4; k++) {               // iops, ilit, sops, slit
            const pn = be32(b, e + 20 + 4 * k);
            if (pn > n - at)
                throw new Fail('a section part past the end');
            c.part.push(b.subarray(at, at + pn));
            at += pn;
        }
        p.sec.push(c);
    }
    if (at !== n || !p.name)
        throw new Fail('trailing bytes in the pack');
    return p;
}

function uvar(p, st) {                              // a LEB128 varint at st.i (st.i moves); exact up to 2^53
    let v = 0;
    for (let s = 0; s < 64; s += 7) {
        if (st.i >= p.length)
            throw new Fail('a varint past the end');
        const b = p[st.i++];
        v += (b & 0x7F) * 2 ** s;
        if (!(b & 0x80))
            return v;
    }
    throw new Fail('a varint too long');
}

// ops -> out, n bytes: copies from the pool (at cur + a zigzag delta) and runs of lit (mkpatch.py's run_ops)
function runOps(ops, lit, pool, cur, n) {
    const out = new Uint8Array(n), st = { i: 0 };
    let li = 0, at = 0;
    while (st.i < ops.length) {
        const v = uvar(ops, st), c = Math.floor(v / 4);
        if (c > n - at)
            throw new Fail('ops past the output');
        if (v % 4 === 0) {                          // a copy
            const z = uvar(ops, st);
            cur += z % 2 ? -((z + 1) / 2) : z / 2;
            if (cur < 0 || cur > pool.length || c > pool.length - cur)
                throw new Fail('a copy outside the pool');
            out.set(pool.subarray(cur, cur + c), at);
        } else if (v % 4 === 1) {                   // our bytes
            if (c > lit.length - li)
                throw new Fail('literals past the end');
            out.set(lit.subarray(li, li + c), at);
            li += c;
        } else
            throw new Fail('an unknown op');
        at += c;
        cur += c;
    }
    if (at !== n || li !== lit.length)
        throw new Fail('ops do not fill the output');
    return out;
}

// one replaced section -> its stored bytes: u32 stream length, u32 byte sum, the packed stream, zero padding
function makeSection(s, c) {
    const i = stockSec(s, c.sid);
    if (i < 0)
        throw new Fail('a replaced section is not in the stock file');
    const img = runOps(c.part[0], c.part[1], s.pool, s.sec[i].at, c.imageLen);
    const skel = runOps(c.part[2], c.part[3], s.pool, 0, c.skelLen);
    const stream = new Buf(c.streamLen + 16);
    new Walk(skel, 0, skel.length, img, stream, true).walk();
    if (stream.n !== c.streamLen)
        throw new Fail('the rebuilt stream has the wrong length');
    const stored = new Uint8Array(c.storedLen);     // zero padding after the stream
    let sum = 0;
    for (let k = 0; k < stream.n; k++)
        sum = (sum + stream.p[k]) >>> 0;
    set32(stored, 0, stream.n);
    set32(stored, 4, sum);
    stored.set(stream.bytes(), 8);
    return stored;
}

function framing(o, m, msgs) {                      // a framing message with the data-message count
    const f = m.slice(0, 16);
    f[12] = (msgs >> 14) & 0x7F;
    f[13] = (msgs >> 7) & 0x7F;
    f[14] = msgs & 0x7F;
    o.put(f);
}

// the patch on the stock file -> the build's .syx (mkpatch.py's apply)
function build(s, p) {
    const stored = p.sec.map(c => makeSection(s, c));
    const key = sealKey(s), st = s.stream;
    // the container: stock's up to its first section, the version field, then each section in table order with
    // its table entry, padded to 16 bytes; then the seal over all of it
    const c = new Buf(st.length + 4096);
    c.put(st.subarray(s.cont, s.cont + s.dataStart));
    c.p.set(p.version, 0x14);
    for (let i = 0; i < s.nsec; i++) {
        let d = st.subarray(s.cont + s.sec[i].off, s.cont + s.sec[i].off + s.sec[i].len);
        for (let k = 0; k < p.nsec; k++)
            if (p.sec[k].sid === s.sec[i].sid)
                d = stored[k];
        set32(c.p, 0x20 + 16 * i + 4, c.n);
        set32(c.p, 0x20 + 16 * i + 8, d.length);
        c.put(d);
        while (c.n % 16)
            c.put1(0);
    }
    c.put(hmacSha256(key, c.bytes()));
    // the stream: the container's length and checksum (the sum of word index ^ word), then the container
    const cb = c.bytes();
    let sum = 0;
    for (let i = 0; i + 4 <= cb.length; i += 4)
        sum = (sum + ((i / 4 + 1) ^ be32(cb, i))) >>> 0;
    const msgs = Math.ceil((8 + cb.length) / CHUNK);
    const strm = new Uint8Array(msgs * CHUNK);      // the last message zero-padded
    set32(strm, 0, cb.length);
    set32(strm, 4, sum);
    strm.set(cb, 8);
    // the messages: 101 bytes each, 8-in-7, with a counter and a checksum
    const r = s.raw, k = r[8], out = new Buf(32 + 128 * msgs);     // k: the transfer constant, in the framing
    framing(out, r, msgs);
    for (let i = 0; i < msgs; i++) {
        const m = new Uint8Array(128), cn = FIRST_COUNTER + i, d = i * CHUNK;
        m.set([0xF0, 0x00, 0x20, 0x3C, r[4], 0x00, 0x7E]);
        m[7] = (cn >> 14) & 0x7F;
        m[8] = (cn >> 7) & 0x7F;
        m[9] = cn & 0x7F;
        for (let g = 0, q = 10; g < CHUNK; g += 7, q += 8)
            for (let b = 0; b < 7 && g + b < CHUNK; b++) {
                m[q] |= (strm[d + g + b] >> 7) << (6 - b);
                m[q + 1 + b] = strm[d + g + b] & 0x7F;
            }
        let t = k;
        for (let b = 0; b < 119; b++)
            t += m[7 + b] ^ (b + k);
        m[126] = t & 0x7F;
        m[127] = 0xF7;
        out.put(m);
    }
    framing(out, r.subarray(r.length - 16), msgs);
    return out.bytes();
}

// ---- the API (dn2patch.h's, in JavaScript) ----

// A wrong file: what it is, if it can tell (a Digitone II OS file's version field is in its first data message).
// msg: a plain-English sentence or two for anyone, a newline, then the precise reason (for a bug report).
function describe(r, p, sha) {
    const n = r.length, stock = p.info.stock_sha256;
    let v = '', ele3 = false;
    if (n >= 4 && eq(r, 0, [0x50, 0x4b, 3, 4], 0, 4))
        return `That's a .zip file, but not Elektron's Digitone II OS 1.12 download. Use ` +
            `Digitone_II_OS1.12_dist.zip from Elektron's Digitone II page.\nthe input is a zip archive (${n} bytes)`;
    if (n >= 16 + 128 && r[0] === 0xF0 && r[1] === 0 && r[2] === 0x20 && r[3] === 0x3C && r[4] === 0x15 &&
        r[16] === 0xF0) {
        const d = new Uint8Array(32), q = 16 + 10;
        for (let g = 0, j = 0; j < 32; g += 8)      // 8-in-7: 5 groups give the first 35 bytes
            for (let k = 1; k < 8 && j < 32; k++)
                d[j++] = r[q + g + k] | ((r[q + g] >> (7 - k)) & 1) << 7;
        v = Array.from(d.subarray(28, 32), c => c < 0x20 || c > 0x7E ? '?' : String.fromCharCode(c)).join('');
        ele3 = eq(d, 8, [0x45, 0x4c, 0x45, 0x33], 0, 4);
    }
    const elektron = /^[0-9]\.[0-9]/.test(v), mine = new TextDecoder().decode(p.version);
    if (ele3 && v === mine)
        return `That's ${p.name}.syx already, the file this makes. Use Elektron's ${STOCK_NAME} instead.\n` +
            `the input is the ${p.name} build (${n} bytes)`;
    if (ele3 && !elektron)
        return `That's ${v}, a custom OS, not Elektron's OS 1.12 file. Use Elektron's ${STOCK_NAME} instead.\n` +
            `version field ${v}, ${n} bytes, sha256 ${sha}`;
    if (ele3 && v === '1.12')
        return `That's Elektron's OS 1.12 file, but it is damaged or incomplete. Download it again.\n` +
            `version field 1.12, ${n} bytes, sha256 ${sha} (Elektron's file: ${stock})`;
    if (ele3)
        return `That's Digitone II OS ${v}. This needs OS 1.12: download it from Elektron's Digitone II page.\n` +
            `version field ${v}, ${n} bytes, sha256 ${sha}`;
    return `That's not Elektron's Digitone II OS 1.12 file. Use the ${STOCK_NAME} file from Elektron's download ` +
        `(Digitone_II_OS1.12_dist.zip).\n${n} bytes, sha256 ${sha} (Elektron's file: ${stock})`;
}

// the pack's fields (dn2patch_info), or null when it is not a pack this core can apply
export function info(pack) {
    try {
        return patchRead(pack).info;
    } catch (e) {
        if (e instanceof Fail)
            return null;
        throw e;
    }
}

// pack, input: Uint8Arrays -> { status, out (the build's .syx, on OK), msg } (dn2patch_run)
export function run(pack, input) {
    let p;
    try {
        p = patchRead(pack);
    } catch (e) {
        if (!(e instanceof Fail))
            throw e;
        return { status: FAILED, msg: "This copy of the patcher is damaged. Reload the page.\nthe pack does not parse" };
    }
    const sin = hex(sha256(input));
    if (sin !== p.info.stock_sha256)
        return { status: NOT_STOCK, msg: describe(input, p, sin) };
    let out;
    try {
        out = build(stockRead(input), p);
    } catch (e) {
        if (!(e instanceof Fail) && !(e instanceof RangeError))
            throw e;
        const oom = e instanceof RangeError;
        return { status: FAILED, msg: oom
            ? `There wasn't enough memory to make ${p.name}.syx, so nothing was saved. Close other tabs and try ` +
              `again.\nan allocation failed (${p.name})`
            : `Something went wrong while making ${p.name}.syx, so nothing was saved. Please report this.\n` +
              `decoding the input or rebuilding ${p.name} failed: ${e.message} (input sha256 ${sin})` };
    }
    const sout = hex(sha256(out));
    if (out.length !== p.outLen || sout !== p.info.build_sha256)
        return { status: FAILED, msg: `Something went wrong: the result is not exactly the ${p.name} we tested, so ` +
            `nothing was saved. Please report this.\nresult ${out.length} bytes, sha256 ${sout}; tested build ` +
            `${p.outLen} bytes, sha256 ${p.info.build_sha256}` };
    return { status: OK, out, msg: `Checked: this is Elektron's original OS 1.12 file. Made ${p.name}.syx, ` +
        `exactly the build we tested.\ninput sha256 ${sin}; ${p.name}.syx ${out.length} bytes, sha256 ${sout}` };
}

// these bytes are the pack's build already (dn2patch_is_build)
export function isBuild(pack, input) {
    const p = info(pack) && patchRead(pack);
    return !!p && input.length === p.outLen && hex(sha256(input)) === p.info.build_sha256;
}
