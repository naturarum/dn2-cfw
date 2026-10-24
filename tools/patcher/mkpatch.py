#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""mkpatch.py: one of our Digitone II builds (.syx) -> a pack: the patch that Digiwerk Patcher and digiwerk-patch
apply to the user's own copy of Elektron's OS 1.12 file, so that we never hand out Elektron's firmware.

    python3 tools/patcher/mkpatch.py out/OB01/OB01.syx [--name OB01] [--out out/patcher/OB01] [--expect SHA256]
            [--pack-json FILE] [--brickcheck DIR] [--stock elektron-firmware/Digitone_II_OS1_12.syx] [--corrupt lit|skel]
    python3 tools/patcher/mkpatch.py --embed OUT.h A/patch.bin [B/patch.bin ...]     the CLI's packs, in this order

Writes OUT/patch.bin (the pack), OUT/patch.json (the summary) and OUT/pack.json (the pack's record: the hand-written
fields with the hashes and the brickcheck facts filled in). Nothing is written unless every check holds:
  - the stock file is 1.12 (sha256); the build is stock but for the parts below;
  - the pack's hand-written JSON exists: out/NAME/pack.json, else tools/patcher/packs/NAME.json (or --pack-json):
    {"title", "description", "tested_on_unit": "23 Oct 2026" or "", "effects": ["cmp3", "flng"] (the pack's
    memory-heavy DSP effects: EFFECTS, each with its pixel icon in docs/identity/ID.svg), "base": ["djvu",
    "overbridge"] (the light main-OS features of the release's base: BASE; those in BASE_ICONS have an icon too),
    "dsp": {"code_bytes", "state_bytes", "load_percent"} (optional; null when not known)};
  - a brickcheck report under out/brickcheck/NAME-*/ (or --brickcheck DIR) says ACCEPT, every check PASS, for exactly
    this .syx (its sha256); the record line says so: "Brick check 12 of 12 · tested on a Digitone II, 23 Oct 2026",
    or "... · not yet tested on a unit";
  - the pack rebuilds the build byte for byte here (apply(), the same steps as dn2patch.c), and stockcheck.py finds no
    run of 16 bytes of it in the stock file.
--corrupt flips one byte of the first section's lit or skel after the checks (for the test of the tool's refusal:
the output hash no longer matches, so no file must be written).

What a build changes (scripts/cmp4_build.py, scripts/ob01_build.py; vendor/elekloader/elekloader/syx.py): the stored
main OS (section 3, packed by elekloader's codec/dense.py), the stored DSP program (section 7, packed by
elektron-firmware-tool), the ELE3 version field, and what follows from them: the table's offsets and lengths, the
16-byte padding, the HMAC-SHA256 seal (its key comes from the stock bootstrap, section 2, as syx.seal_key derives it),
the preamble's length and content checksum, the framing messages' count, and every data message's counter and
checksum. mkpatch refuses a build that differs from stock in any other way (sections 2, 4, 5 and 8 stored as stock,
the rest of the header and the framing messages as stock).

The pack (big-endian u32 fields):
  0    "DN2P", u32 format (2)
  8    the build name, 16 bytes NUL-padded: the pack's id (the output is NAME.syx)
  24   the ELE3 version field (4 bytes)
  28   sha256 of the stock .syx; 60: sha256 of the output .syx; 92: u32 output length
  96   u32 n; 100: u32 meta_len
  104  n x {sid, stored_len, stream_len, image_len, skel_len, iops_len, ilit_len, sops_len, slit_len}
  then the metadata (meta_len bytes of UTF-8 "key=value" lines, META's keys in order), then, per section: iops, ilit,
  sops, slit
The pool is the user's stock file decoded: every section decoded, in table order, then the decoded transport stream
(the preamble and the ELE3 container, Elektron's packed sections as stored). Two byte strings are made from it by ops
(varint v, count = v >> 2; v & 3 == 0: copy count bytes of the pool at cur + zigzag varint delta; v & 3 == 1: the
next count bytes of lit; cur moves with each op):
  - the section's decoded image, from iops and ilit (cur starts at the section's own stock copy);
  - its skeleton, from sops and slit (cur starts at 0): the packed stream with every literal byte left out (the tag
    and offset bytes, in the order the decoder reads them). The stream is rebuilt by walking the skeleton with the
    decoder and taking each literal from the image at its output position.
Where two packers code the same data alike (section 7's stretches with no literal, runs of one byte) the skeleton
holds runs of Elektron's stored stream; sops copies those from the user's file too. So no K bytes in a row of the
blob appear in the stock file in any form (stockcheck.py proves it): ilit and slit hold only our bytes, iops and
sops copy instructions, the header structure and hashes.
"""
import argparse
import glob
import hashlib
import hmac
import json
import os
import re
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
STOCK = os.path.join(ROOT, 'elektron-firmware', 'Digitone_II_OS1_12.syx')
STOCK_SHA = '7e5b8a733d4ab1fdf7e5d22757f7d8eddaa3cbf71d0858874a791844c02ceb35'
MFR = b'\x00\x20\x3c'
CHUNK, FIRST_COUNTER = 101, 242          # decoded bytes per data message; the first message's counter
BIAS, REUSE, FAR = 767, 2, 3328          # the codec's constants (codec/elz.py)
SEAL_SEED, SEAL_SEC = b'Multiplier', 2   # the seal key's seed and the section holding it (devices/digitone_mk2.py)
VERSION_AT = 0x14
SWAPPABLE = (3, 7)                       # the sections a build may replace: the main OS and the DSP program
FORMAT = 2
K = 16                                   # the checker's run length; the shortest copy taken away from the cursor
R = 4                                    # the shortest copy taken at the cursor or at the section's own place
HDR = 104
META = (('title', 63), ('description', 159), ('record', 159), ('tested_on_unit', 31), ('brickcheck', 159),
        ('effects', 63), ('base', 63), ('dsp_code_bytes', 15), ('dsp_state_bytes', 15), ('dsp_load_percent', 15))
# key, most bytes (dn2patch.h's dn2p_info)
EFFECTS = {'cmp3': '3-band compressor', 'flng': 'flanger', 'tape': 'tape echo', 'spng': 'spring reverb'}
BASE = {'djvu': 'D\u00e9j\u00e0 vu LFO', 'overbridge': 'Overbridge works'}   # the base: light main-OS features
BASE_ICONS = ('djvu',)
IDENTITY = os.path.join(ROOT, 'docs', 'identity')         # the effects' pixel icons (make_icons.py)
BRICKCHECK = os.path.join(ROOT, 'out', 'brickcheck')
SEC = struct.Struct('>9I')               # sid, stored_len, stream_len, image_len, skel_len, iops, ilit, sops, slit
M32 = 0xFFFFFFFF


class PatchError(ValueError):
    pass


def sha(b):
    return hashlib.sha256(b).hexdigest()


# --- the .syx transport (vendor/elekloader/elekloader/codec/transport.py's rules)

def unpack7(p):
    out = bytearray()
    for g in range(0, len(p), 8):
        ms = p[g]
        out += bytes(b | ((ms >> (6 - n)) & 1) << 7 for n, b in enumerate(p[g + 1:g + 8]))
    return out


def pack7(chunk):
    out = bytearray()
    for g in range(0, len(chunk), 7):
        grp = chunk[g:g + 7]
        out.append(sum(1 << (6 - n) for n, b in enumerate(grp) if b & 0x80))
        out += bytes(b & 0x7F for b in grp)
    return out


def packet_checksum(m, k):
    """byte 126 of a data message, over its counter and payload (m[7:126])"""
    t = k
    for i in range(119):
        t += m[7 + i] ^ (i + k)
    return t & 0x7F


def content_checksum(c):
    words = struct.unpack('>%dI' % (len(c) // 4), c[:len(c) // 4 * 4])
    return sum((i + 1) ^ w for i, w in enumerate(words)) & M32


def with_count(msg, n):
    """a framing message with its data-message count (bytes 12..14, base 128) set to n"""
    return msg[:12] + bytes(((n >> 14) & 0x7F, (n >> 7) & 0x7F, n & 0x7F)) + msg[15:]


def encode(stream, first, last):
    """decoded stream -> .syx: the framing messages around one 128-byte message per 101 bytes (zero-padded)"""
    n = -(-len(stream) // CHUNK)
    out = bytearray(with_count(first, n))
    for i in range(n):
        chunk = stream[i * CHUNK:(i + 1) * CHUNK]
        c = FIRST_COUNTER + i
        m = bytearray(b'\xf0' + MFR + bytes((first[4], 0, 0x7E, (c >> 14) & 0x7F, (c >> 7) & 0x7F, c & 0x7F)))
        m += pack7(chunk + bytes(CHUNK - len(chunk)))
        m.append(packet_checksum(m, first[8]))
        m.append(0xF7)
        out += m
    return bytes(out + with_count(last, n))


# --- the codec (codec/elz.py), with the skeleton

def depack(s):
    """packed stream -> (decoded bytes, skeleton, bytes read): the skeleton is the stream's tag and offset bytes in
    the order the decoder reads them, every literal left out"""
    out, skel = bytearray(), bytearray()
    st = [0, 0]                          # stream position, tag register

    def byte():
        v = s[st[0]]
        st[0] += 1
        return v

    def bit():
        st[1] = (st[1] << 1) & 0x1FF
        if st[1] & 0xFF == 0:
            b = byte()
            skel.append(b)
            st[1] = (b << 1) | 1
            return b >> 7
        return st[1] >> 8

    def gamma():
        v = 1
        while True:
            v = (v << 1) | bit()
            if bit():
                return v
            if v > 0x02000000:
                raise PatchError('gamma overflow')

    last = 1
    try:
        while True:
            if bit():
                out.append(byte())
                continue
            g = gamma()
            if g == REUSE:
                off = last
            else:
                b = byte()
                skel.append(b)
                raw = ((g << 8) + b) & M32
                if raw == BIAS:
                    break
                off = last = (raw - BIAS) & M32
            short = 2 * bit()
            short += bit()
            n = (short if short else gamma() + 2) + (off > FAR) + 1
            if off == 0 or off > len(out):
                raise PatchError('a match reaches before the output')
            p = len(out) - off
            if off >= n:
                out += out[p:p + n]
            else:
                for i in range(n):
                    out.append(out[p + i])
    except IndexError:
        raise PatchError('the stream ends inside a token')
    return bytes(out), bytes(skel), st[0]


def rebuild(image, skel):
    """(decoded image, skeleton) -> the packed stream: the decoder's walk, each literal taken from the image"""
    out = bytearray()
    st = [0, 0, 0]                       # skeleton position, tag register, output position

    def sk():
        v = skel[st[0]]
        st[0] += 1
        out.append(v)
        return v

    def bit():
        st[1] = (st[1] << 1) & 0x1FF
        if st[1] & 0xFF == 0:
            b = sk()
            st[1] = (b << 1) | 1
            return b >> 7
        return st[1] >> 8

    def gamma():
        v = 1
        while True:
            v = (v << 1) | bit()
            if bit():
                return v
            if v > 0x02000000:
                raise PatchError('gamma overflow')

    last = 1
    try:
        while True:
            if bit():
                out.append(image[st[2]])
                st[2] += 1
                continue
            g = gamma()
            if g == REUSE:
                off = last
            else:
                raw = ((g << 8) + sk()) & M32
                if raw == BIAS:
                    break
                off = last = (raw - BIAS) & M32
            short = 2 * bit()
            short += bit()
            n = (short if short else gamma() + 2) + (off > FAR) + 1
            if off == 0 or off > st[2]:
                raise PatchError('a match reaches before the output')
            st[2] += n
    except IndexError:
        raise PatchError('the skeleton or the image ends early')
    if st[0] != len(skel) or st[2] != len(image):
        raise PatchError('the skeleton does not cover the image')
    return bytes(out)


def unstore(raw):
    """stored section -> decoded bytes (syx.classify's three kinds: packed, a zero-sum header, raw)"""
    if len(raw) >= 8:
        n, sm = struct.unpack_from('>II', raw)
        if n + 8 <= len(raw) and sum(raw[8:8 + n]) & M32 == sm:
            img, _skel, used = depack(raw[8:8 + n])
            if used != n:
                raise PatchError('the end marker is not at the stream length')
            return img
        if sm == 0:
            return raw[8:]
    return raw


# --- the container (syx.py's reader and writer)

class Syx:
    def __init__(self, raw):
        self.raw = bytes(raw)
        msgs, i = [], 0
        while i < len(raw):
            j = raw.find(b'\xf7', i)
            if raw[i] != 0xF0 or j < 0:
                raise PatchError('not SysEx at byte %d' % i)
            msgs.append(raw[i:j + 1])
            i = j + 1
        if len(msgs) < 3 or len(msgs[0]) != 16 or len(msgs[-1]) != 16:
            raise PatchError('no framing messages')
        self.first, self.last, data = msgs[0], msgs[-1], msgs[1:-1]
        for n, m in enumerate(data):
            c = FIRST_COUNTER + n
            if len(m) != 128 or m[1:4] != MFR or m[6] != 0x7E or m[7:10] != bytes(
                    ((c >> 14) & 0x7F, (c >> 7) & 0x7F, c & 0x7F)):
                raise PatchError('data message %d is not in order' % n)
        st = b''.join(unpack7(m[10:126]) for m in data)
        self.total, self.checksum = struct.unpack_from('>II', st)
        self.container = st[8:8 + self.total]
        if self.container[:4] != b'ELE3':
            raise PatchError('no ELE3 container')
        nsec = struct.unpack_from('>I', self.container, 0x1C)[0]
        self.table = [struct.unpack_from('>4I', self.container, 0x20 + 16 * i) for i in range(nsec)]
        self.data_start = min(t[1] for t in self.table)
        self.stored = {sid: self.container[off:off + ln] for sid, off, ln, _d in self.table}
        self.version = self.container[VERSION_AT:VERSION_AT + 4]
        self._decoded = None

    @property
    def decoded(self):
        if self._decoded is None:
            self._decoded = {sid: unstore(self.stored[sid]) for sid, _o, _l, _d in self.table}
        return self._decoded

    def pool(self):
        """-> (the pool: every section decoded, in table order, then the decoded transport stream; sid -> where its
        decoded bytes start in the pool)"""
        pool, home = bytearray(), {}
        for sid, _o, _l, _d in self.table:
            home[sid] = len(pool)
            pool += self.decoded[sid]
        return bytes(pool + struct.pack('>II', self.total, self.checksum) + self.container), home


def seal_key(sec2):
    i = sec2.find(SEAL_SEED + b'\0')
    if i < 0 or sec2.find(SEAL_SEED + b'\0', i + 1) >= 0:
        raise PatchError('the bootstrap does not hold the seal seed once')
    c = sec2[i + len(SEAL_SEED) + 1:i + len(SEAL_SEED) + 33]
    a, b = hashlib.sha256(SEAL_SEED).digest(), hashlib.sha256(SEAL_SEED[::-1]).digest()
    return bytes(x ^ y ^ z for x, y, z in zip(c, a, b))


def make_syx(stock, version, stored):
    """stock with the version field and the stored sections in `stored` (sid -> bytes); the table, padding, seal,
    preamble and transport follow"""
    c = bytearray(stock.container[:stock.data_start])
    c[VERSION_AT:VERSION_AT + 4] = version
    for i, (sid, _o, _l, dest) in enumerate(stock.table):
        d = stored.get(sid, stock.stored[sid])
        struct.pack_into('>4I', c, 0x20 + 16 * i, sid, len(c), len(d), dest)
        c += d
        c += bytes(-len(c) % 16)
    c += hmac.new(seal_key(stock.decoded[SEAL_SEC]), bytes(c), hashlib.sha256).digest()
    return encode(struct.pack('>II', len(c), content_checksum(c)) + c, stock.first, stock.last)


# --- the image as copy ops

def uvar(n):
    out = bytearray()
    while True:
        out.append((n & 0x7F) | (0x80 if n > 0x7F else 0))
        n >>= 7
        if not n:
            return out


def mlen(a, i, b, j):
    """-> how many bytes a[i:] and b[j:] have in common at their start"""
    if j < 0:
        return 0
    n, m, step = min(len(a) - i, len(b) - j), 0, 4096
    while step:
        while m + step <= n and a[i + m:i + m + step] == b[j + m:j + m + step]:
            m += step
        step >>= 2
    return m


def diff(img, pool, home=0, own_len=0):
    """-> (ops, lit, counts): img as copies from the pool and literal bytes. A copy continues at the cursor, or at
    img's own place in its stock copy (pool[home:home + own_len]), when R bytes or more match there; or it comes from
    anywhere in the pool when K or more do (a K-byte index of the pool, over the places of img that differ from its
    stock copy, with a margin). So no K bytes in a row of lit appear in the pool; stockcheck.py checks it."""
    n = len(img)
    # the places that differ from img's own stock copy (between runs of 32 or more equal bytes)
    loose, i = [], 0
    while i < n:
        same = mlen(img, i, pool, home + i) if i < own_len else 0
        if same >= 32:
            i += same
            continue
        j = i + 1
        while j < own_len and mlen(img, j, pool, home + j) < 32:
            j += 1
        j = j if j < own_len else n
        loose.append((max(0, i - 2 * K), min(n, j + 2 * K)))
        i = j
    want = {}
    for a, b in loose:
        want.update(dict.fromkeys(img[p:p + K] for p in range(a, min(b, n - K + 1))))
    for p in range(len(pool) - K + 1):
        g = pool[p:p + K]
        if g in want:
            hit = want[g]
            if hit is None:
                want[g] = [p]
            elif len(hit) < 16:
                hit.append(p)
    ops, lit, cnt = bytearray(), bytearray(), {'copy': 0, 'lit': 0}
    i, cur, lit0 = 0, home, None

    def flush(end):
        if lit0 is not None:
            ops.extend(uvar(((end - lit0) << 2) | 1))
            lit.extend(img[lit0:end])
            cnt['lit'] += 1
    while i < n:
        best, src = 0, None
        for p in (cur, home + i if i < own_len else -1):
            m = mlen(img, i, pool, p)
            if m >= R and m > best:
                best, src = m, p
        for p in (want.get(img[i:i + K]) or ()) if i + K <= n else ():
            m = mlen(img, i, pool, p)
            if m > best:
                best, src = m, p
        if src is None:
            if lit0 is None:
                lit0 = i
            i, cur = i + 1, cur + 1
            continue
        flush(i)
        lit0 = None
        d = src - cur
        ops += uvar(best << 2) + uvar(d * 2 if d >= 0 else -d * 2 - 1)
        cnt['copy'] += 1
        i, cur = i + best, src + best
    flush(n)
    return bytes(ops), bytes(lit), cnt


def run_ops(ops, lit, pool, cur, n):
    img, i, li = bytearray(), 0, 0

    def var():
        nonlocal i
        v, s = 0, 0
        while True:
            b = ops[i]
            i += 1
            v |= (b & 0x7F) << s
            s += 7
            if not b & 0x80:
                return v
    while i < len(ops):
        v = var()
        c = v >> 2
        if v & 3 == 0:
            z = var()
            cur += (z >> 1) if not z & 1 else -((z + 1) >> 1)
            if cur < 0 or cur + c > len(pool):
                raise PatchError('a copy outside the stock file')
            img += pool[cur:cur + c]
        elif v & 3 == 1:
            img += lit[li:li + c]
            li += c
        else:
            raise PatchError('an unknown op')
        cur += c
    if len(img) != n or li != len(lit):
        raise PatchError('the ops do not make the image')
    return bytes(img)


# --- the blob

PARTS = ('iops', 'ilit', 'sops', 'slit')


def sections(blob):
    """pack -> (header fields with the metadata, [one dict per replaced section: its fields, its four arrays, and
    where they are])"""
    if blob[:4] != b'DN2P' or struct.unpack_from('>I', blob, 4)[0] != FORMAT:
        raise PatchError('not a format-%d pack' % FORMAT)
    n, ml = struct.unpack_from('>II', blob, 96)
    at = HDR + n * SEC.size
    meta = dict(line.split('=', 1) for line in blob[at:at + ml].decode('utf-8').splitlines())
    head = {'name': blob[8:24].rstrip(b'\0').decode(), 'version': blob[24:28], 'stock_sha': blob[28:60].hex(),
            'out_sha': blob[60:92].hex(), 'out_len': struct.unpack_from('>I', blob, 92)[0], 'meta': meta,
            'meta_at': at, 'meta_len': ml}
    at, secs = at + ml, []
    for k in range(n):
        f = SEC.unpack_from(blob, HDR + k * SEC.size)
        sec = dict(zip(('sid', 'stored_len', 'stream_len', 'image_len', 'skel_len'), f[:5]), at={})
        for name, ln in zip(PARTS, f[5:]):
            sec[name], sec['at'][name] = blob[at:at + ln], at
            at += ln
        secs.append(sec)
    if at != len(blob):
        raise PatchError('the pack is %d bytes, its table says %d' % (len(blob), at))
    return head, secs


def meta_bytes(meta):
    """the metadata block: META's keys in order, each value one line within its size"""
    out = []
    for key, most in META:
        v = str(meta.get(key, ''))
        if '\n' in v or '=' in key or len(v.encode('utf-8')) > most:
            raise PatchError('pack field %s: one line of at most %d bytes' % (key, most))
        out.append('%s=%s\n' % (key, v))
    return ''.join(out).encode('utf-8')


def brickcheck(name, build_sha, root=BRICKCHECK):
    """-> the newest brickcheck report under root/NAME-*/ that says ACCEPT, every check PASS, for exactly this .syx"""
    best = None
    for d in sorted(glob.glob(os.path.join(root, name + '-*'))):
        try:
            j = json.load(open(os.path.join(d, 'report.json')))
            md = open(os.path.join(d, 'report.md'), 'rb').read()
        except (OSError, ValueError):
            continue
        m = re.search(rb'candidate: `[^`]*`, sha256 `([0-9a-f]{64})`', md)
        checks = j.get('checks') or {}
        passed = sum(1 for c in checks.values() if c.get('status') == 'PASS')
        if not m or m.group(1).decode() != build_sha or j.get('verdict') != 'accept' \
                or b'**Verdict: ACCEPT.**' not in md or not checks or passed != len(checks):
            continue
        fact = {'report': os.path.relpath(os.path.join(d, 'report.md'), ROOT), 'report_sha256': sha(md),
                'date': j.get('date', ''), 'passed': passed, 'total': len(checks), 'verdict': 'ACCEPT'}
        if best is None or fact['date'] > best['date']:
            best = fact
    if best is None:
        raise PatchError('no brickcheck ACCEPT report for %s (sha256 %s...) under %s: a pack needs one'
                         % (name, build_sha[:16], os.path.relpath(root, ROOT)))
    return best


def pack_record(name, build_sha, pack_json=None, bc_root=BRICKCHECK):
    """-> the pack's record: its hand-written JSON (title, description, tested_on_unit, dsp) with the record line and
    the brickcheck facts filled in"""
    places = [pack_json] if pack_json else [os.path.join(ROOT, 'out', name, 'pack.json'),
                                            os.path.join(os.path.dirname(os.path.abspath(__file__)), 'packs',
                                                         name + '.json')]
    src = next((p for p in places if os.path.exists(p)), None)
    if src is None:
        raise PatchError('no pack JSON for %s (%s)' % (name, ' or '.join(os.path.relpath(p, ROOT) for p in places)))
    j = json.load(open(src))
    if not isinstance(j.get('title'), str) or not j['title'] or not isinstance(j.get('description'), str) \
            or not isinstance(j.get('tested_on_unit', ''), str):
        raise PatchError('%s: needs "title" and "description" (and "tested_on_unit", may be "")' % src)
    fx = j.get('effects')
    if not isinstance(fx, list) or any(e not in EFFECTS for e in fx) or len(set(fx)) != len(fx):
        raise PatchError('%s: "effects" must list effect ids, each once, from %s' % (src, ', '.join(EFFECTS)))
    base = j.get('base')
    if not isinstance(base, list) or any(b not in BASE for b in base) or len(set(base)) != len(base):
        raise PatchError('%s: "base" must list base ids, each once, from %s' % (src, ', '.join(BASE)))
    missing = [e for e in fx + [b for b in base if b in BASE_ICONS]
               if not os.path.exists(os.path.join(IDENTITY, e + '.svg'))]
    if missing:
        raise PatchError('%s: no icon for %s in %s' % (src, ', '.join(missing), os.path.relpath(IDENTITY, ROOT)))
    bc = brickcheck(name, build_sha, bc_root)
    tested = j.get('tested_on_unit', '').strip()
    dsp = j.get('dsp') or {}
    rec = dict(id=name, title=j['title'], description=j['description'], tested_on_unit=tested,
               record='Brick check %d of %d \u00b7 %s' % (
                   bc['passed'], bc['total'], 'tested on a Digitone II, ' + tested if tested
                   else 'not yet tested on a unit'),
               effects=fx, base=base, brickcheck=bc, dsp={k: dsp.get(k) for k in ('code_bytes', 'state_bytes', 'load_percent')},
               pack_json=os.path.relpath(src, ROOT))
    rec['meta'] = {'title': rec['title'], 'description': rec['description'], 'record': rec['record'],
                   'tested_on_unit': tested,
                   'brickcheck': 'ACCEPT %d/%d %s' % (bc['passed'], bc['total'], bc['date']),
                   'effects': ' '.join(fx),
                   'base': ' '.join(base),
                   'dsp_code_bytes': '' if rec['dsp']['code_bytes'] is None else int(rec['dsp']['code_bytes']),
                   'dsp_state_bytes': '' if rec['dsp']['state_bytes'] is None else int(rec['dsp']['state_bytes']),
                   'dsp_load_percent': '' if rec['dsp']['load_percent'] is None else float(rec['dsp']['load_percent'])}
    return rec


def apply(blob, raw):
    """the patch on a stock file -> the build's .syx (dn2patch.c's steps)"""
    head, secs = sections(blob)
    if sha(raw) != head['stock_sha']:
        raise PatchError('not the stock file this patch is for')
    stock = Syx(raw)
    pool, home = stock.pool()
    stored = {}
    for c in secs:
        img = run_ops(c['iops'], c['ilit'], pool, home[c['sid']], c['image_len'])
        s = rebuild(img, run_ops(c['sops'], c['slit'], pool, 0, c['skel_len']))
        if len(s) != c['stream_len']:
            raise PatchError('section %d: the stream is %d bytes, not %d' % (c['sid'], len(s), c['stream_len']))
        stored[c['sid']] = struct.pack('>II', len(s), sum(s) & M32) + s + bytes(c['stored_len'] - 8 - len(s))
    out = make_syx(stock, head['version'], stored)
    if sha(out) != head['out_sha'] or len(out) != head['out_len']:
        raise PatchError('the output is not the build (sha256 %s)' % sha(out))
    return out


def shape(stock, target):
    """-> the sections the target replaces; refuses a target that is not stock with new stored sections 3 and 7 and a
    version field"""
    swap = [sid for sid, _o, _l, _d in stock.table if target.stored.get(sid) != stock.stored[sid]]
    if sorted(target.stored) != sorted(stock.stored):
        raise PatchError('the build has other sections than stock')
    if set(swap) - set(SWAPPABLE):
        raise PatchError('the build replaces section(s) %s; a build may replace only %s'
                         % (sorted(set(swap) - set(SWAPPABLE)), SWAPPABLE))
    if make_syx(stock, target.version, {s: target.stored[s] for s in swap}) != target.raw:
        raise PatchError('the build is not stock with new stored sections and a version field: refused')
    return swap


def make(stock, target, name, meta=None):
    """-> (pack, summary) for a target Syx made from the stock Syx; meta: the pack's metadata fields"""
    swap = shape(stock, target)
    pool, home = stock.pool()
    parts, table, summ = [], [], []
    for sid in swap:
        raw = target.stored[sid]
        n, sm = struct.unpack_from('>II', raw)
        if n + 8 > len(raw) or sum(raw[8:8 + n]) & M32 != sm or any(raw[8 + n:]):
            raise PatchError('section %d: not a packed stream with its byte sum and zero padding' % sid)
        img, skel, used = depack(raw[8:8 + n])
        if used != n:
            raise PatchError('section %d: the end marker is not at the stream length' % sid)
        iops, ilit, ic = diff(img, pool, home[sid], len(stock.decoded[sid]))
        sops, slit, sc = diff(skel, pool)
        table.append(SEC.pack(sid, len(raw), n, len(img), len(skel), len(iops), len(ilit), len(sops), len(slit)))
        parts += [iops, ilit, sops, slit]
        summ.append({'section': sid, 'stored': len(raw), 'stream': n, 'stream_literals': n - len(skel),
                     'image': len(img), 'image_sha256': sha(img), 'stock_image': len(stock.decoded[sid]),
                     'skel': len(skel), 'iops_bytes': len(iops), 'ilit_bytes': len(ilit), 'sops_bytes': len(sops),
                     'slit_bytes': len(slit), 'image_ops': ic, 'skel_ops': sc})
    nm, mb = name.encode('ascii'), meta_bytes(meta or {})
    blob = (b'DN2P' + struct.pack('>I', FORMAT) + nm + bytes(16 - len(nm)) + target.version
            + bytes.fromhex(STOCK_SHA) + hashlib.sha256(target.raw).digest()
            + struct.pack('>III', len(target.raw), len(swap), len(mb)) + b''.join(table) + mb + b''.join(parts))
    return blob, summ


def embed_text(paths):
    """the CLI's packs.h: every pack as a C array, in the order given (the order the CLI lists them)"""
    out, names = [], []
    for i, p in enumerate(paths):
        blob = open(p, 'rb').read()
        head, _secs = sections(blob)
        names.append(head['name'])
        rows = [','.join(str(b) for b in blob[k:k + 32]) for k in range(0, len(blob), 32)]
        out.append('/* %s: %s (output sha256 %s) */\nstatic const unsigned char pack_%d[%d] = {\n%s\n};\n'
                   % (head['name'], head['meta'].get('title', ''), head['out_sha'], i, len(blob), ',\n'.join(rows)))
    return ('/* Generated by tools/patcher/mkpatch.py --embed: %d pack(s), %s. Our bytes, copy instructions into the\n'
            ' * user\'s own stock file, and codec control bits only (tools/patcher/stockcheck.py). Do not edit. */\n'
            '#define NPACKS %d\n%s'
            'static const unsigned char *const packs[NPACKS] = { %s };\n'
            'static const size_t pack_len[NPACKS] = { %s };\n') % (
        len(paths), ', '.join(names), len(paths), ''.join(out), ', '.join('pack_%d' % i for i in range(len(paths))),
        ', '.join('sizeof pack_%d' % i for i in range(len(paths))))


def main():
    if len(sys.argv) > 2 and sys.argv[1] == '--embed':
        try:
            text = embed_text(sys.argv[3:])
        except (OSError, PatchError) as e:
            raise SystemExit('refused: %s' % e)
        os.makedirs(os.path.dirname(os.path.abspath(sys.argv[2])), exist_ok=True)
        open(sys.argv[2], 'w').write(text)
        print('%s: %d pack(s)' % (sys.argv[2], len(sys.argv) - 3))
        return
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('build', help='our build .syx (e.g. out/OB01/OB01.syx)')
    ap.add_argument('--stock', default=STOCK)
    ap.add_argument('--name', help='the build name; the output file is NAME.syx (default: the build file\'s name)')
    ap.add_argument('--out', help='output directory (default out/patcher/NAME)')
    ap.add_argument('--expect', help='refuse unless the build .syx has this sha256')
    ap.add_argument('--pack-json', help='the pack\'s hand-written JSON (default: out/NAME/pack.json, else '
                    'tools/patcher/packs/NAME.json)')
    ap.add_argument('--brickcheck', default=BRICKCHECK, help='where the brickcheck reports are (default out/brickcheck)')
    ap.add_argument('--corrupt', choices=('lit', 'skel'), help='flip one byte after the checks (refusal test only)')
    a = ap.parse_args()
    name = a.name or os.path.splitext(os.path.basename(a.build))[0]
    if not (0 < len(name) < 16 and all(c.isalnum() or c in '._-' for c in name)):
        raise SystemExit('the name must be 1..15 of A-Z a-z 0-9 . _ -')
    out = a.out or os.path.join(ROOT, 'out', 'patcher', name)
    sraw, traw = open(a.stock, 'rb').read(), open(a.build, 'rb').read()
    if sha(sraw) != STOCK_SHA:
        raise SystemExit('%s is not Elektron\'s Digitone II OS 1.12 file (sha256 %s)' % (a.stock, sha(sraw)))
    if a.expect and sha(traw) != a.expect:
        raise SystemExit('%s has sha256 %s, not %s' % (a.build, sha(traw), a.expect))
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import stockcheck
    try:
        stock, target = Syx(sraw), Syx(traw)
        shape(stock, target)
        rec = pack_record(name, sha(traw), a.pack_json, a.brickcheck)
        blob, summ = make(stock, target, name, rec['meta'])
        if apply(blob, sraw) != traw:
            raise PatchError('the patch does not rebuild the build')
        check = stockcheck.check(blob, stock)
    except PatchError as e:
        raise SystemExit('refused: %s' % e)
    if not check['ok']:
        raise SystemExit('refused: stockcheck found Elektron bytes in the patch: %s' % json.dumps(check['hits']))
    if a.corrupt:
        _h, secs = sections(blob)
        at = secs[0]['at']['ilit' if a.corrupt == 'lit' else 'slit'] + 7
        blob = blob[:at] + bytes([blob[at] ^ 0x5A]) + blob[at + 1:]
    os.makedirs(out, exist_ok=True)
    open(os.path.join(out, 'patch.bin'), 'wb').write(blob)
    rec.update(build_syx=os.path.relpath(os.path.abspath(a.build), ROOT), build_sha256=sha(traw),
               stock_sha256=STOCK_SHA, patch_sha256=sha(blob), patch_bytes=len(blob))
    json.dump({k: v for k, v in rec.items() if k != 'meta'}, open(os.path.join(out, 'pack.json'), 'w'), indent=1,
              ensure_ascii=False)
    summary = {'build': name, 'build_syx': os.path.relpath(os.path.abspath(a.build), ROOT), 'build_sha256': sha(traw),
               'build_bytes': len(traw), 'stock_sha256': STOCK_SHA, 'version_field': target.version.decode('ascii'),
               'patch_bytes': len(blob), 'patch_sha256': sha(blob), 'corrupted_for_test': a.corrupt,
               'pack': {k: v for k, v in rec.items() if k != 'meta'}, 'sections': summ, 'stockcheck': check,
               'python_apply': 'rebuilds the build byte for byte'}
    json.dump(summary, open(os.path.join(out, 'patch.json'), 'w'), indent=1)
    print('%s: pack %d bytes (%s), rebuilds %s; %s; stockcheck: %d runs of %d bytes, no stock hit%s' % (
        name, len(blob), ', '.join('section %d: iops %d, ilit %d, sops %d, slit %d' % (
            s['section'], s['iops_bytes'], s['ilit_bytes'], s['sops_bytes'], s['slit_bytes']) for s in summ),
        sha(traw)[:16], rec['record'],
        check['runs'], stockcheck.K, '; CORRUPTED (%s) for the refusal test' % a.corrupt if a.corrupt else ''))


if __name__ == '__main__':
    main()
