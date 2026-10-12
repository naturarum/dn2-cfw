"""Independent reading of an Elektron OS .syx (Digitone II OS 1.12).

Written for the brick checker from the transport facts in notes/T1.2 and
elekloader's codec/transport.py, without importing either: the decoder, the
per-message checksum, the framing count, the content checksum and the HMAC
seal are all recomputed here. The device's own code is run separately
(bc_device.py); this module is the "computed independently" half of check 4
and all of check 1.

Layout of a file (outermost first):
  - 16-byte framing message, then N 128-byte data messages, then a 16-byte
    framing message. Every message is F0 00 20 3C <dev> ... F7.
  - data message: F0, 00 20 3C, dev, 00, 7E, counter (3 x 7 bits, from 242),
    116 bytes of 8-in-7 payload (101 decoded bytes), checksum, F7.
  - framing message bytes 12..14: N as 3 x 7 bits; byte 8: the transfer
    constant k used by the per-message checksum.
  - decoded stream: [u32 container length][u32 content checksum] container,
    then zero padding to the end of the last 101-byte chunk.
  - container: 'ELE3' header (0x1C bytes, version field at 0x14..0x18), the
    section count at 0x1C, 16-byte entries {id, offset, length, dest} from
    0x20, the sections, and a 32-byte HMAC-SHA256 seal at the end.
"""
import hashlib
import hmac
import struct

MFR = b'\x00\x20\x3c'
FIRST_COUNTER = 242
CHUNK = 101
FRAMING_LEN = 16
DATA_LEN = 128
HEADER_LEN = 0x1C
COUNT_OFF = 0x1C
TABLE_OFF = 0x20
ENTRY = 16
SEAL = 32
VERSION_FIELD = (0x14, 0x18)


class ParseError(ValueError):
    pass


def sha(b):
    return hashlib.sha256(b).hexdigest()


def messages(raw):
    out, i = [], 0
    while i < len(raw):
        if raw[i] != 0xF0:
            raise ParseError('byte %d is 0x%02x, not F0 (start of a message)' % (i, raw[i]))
        j = raw.find(b'\xf7', i)
        if j < 0:
            raise ParseError('the message at byte %d has no F7' % i)
        out.append(raw[i:j + 1])
        i = j + 1
    return out


def decode_payload(payload):
    """116 bytes of 8-in-7 -> 101 bytes: one byte of high bits (MSB first)
    before each group of up to 7 data bytes."""
    out = bytearray()
    k = 0
    while k < len(payload):
        hi = payload[k]
        k += 1
        for n in range(7):
            if k >= len(payload):
                break
            out.append(payload[k] | (0x80 if (hi >> (6 - n)) & 1 else 0))
            k += 1
    return bytes(out)


def message_checksum(msg, k):
    """Byte 126 of a 128-byte data message: k + sum((byte ^ (i + k))) over the
    119 bytes from the counter to the end of the payload, mod 128."""
    body = msg[1:-1]
    total = k
    for i in range(119):
        total += body[6 + i] ^ (i + k)
    return total & 0x7F


def content_checksum(cont):
    """The preamble's checksum: sum((i + 1) ^ word_i) over the container's
    big-endian 32-bit words, mod 2^32 (the bootstrap's 0x80003ca6 loop)."""
    n = len(cont) // 4
    words = struct.unpack('>%dI' % n, cont[:4 * n])
    s = 0
    for i, w in enumerate(words):
        s += (i + 1) ^ w
    return s & 0xFFFFFFFF


def seal_key(boot):
    """The HMAC key the bootstrap derives (0x80005da0): the 32 bytes after the
    seed "Multiplier" and its NUL, XOR sha256(seed), XOR sha256(seed reversed).
    `boot` is the unpacked section 2."""
    seed = b'Multiplier'
    i = boot.find(seed + b'\0')
    if i < 0 or boot.find(seed + b'\0', i + 1) >= 0:
        raise ParseError('the seed "Multiplier" is not in the bootstrap exactly once')
    const = boot[i + len(seed) + 1:i + len(seed) + 1 + 32]
    a = hashlib.sha256(seed).digest()
    b = hashlib.sha256(seed[::-1]).digest()
    return bytes(x ^ y ^ z for x, y, z in zip(const, a, b))


def seal(key, cont):
    return hmac.new(key, cont[:-SEAL], hashlib.sha256).digest()


class Syx:
    """A parsed .syx. Raises ParseError on anything structurally wrong; the
    finer transport facts (checksums, counters, framing count) are collected
    in `problems` so a report can list every one of them."""

    def __init__(self, raw, path=None):
        self.path = path
        self.raw = bytes(raw)
        self.sha256 = sha(self.raw)
        self.problems = []
        msgs = messages(self.raw)
        if len(msgs) < 3:
            raise ParseError('%d messages; need two framing messages and data' % len(msgs))
        if len(msgs[0]) != FRAMING_LEN or len(msgs[-1]) != FRAMING_LEN:
            raise ParseError('first/last message is %d/%d bytes, not %d'
                             % (len(msgs[0]), len(msgs[-1]), FRAMING_LEN))
        self.framing = (msgs[0], msgs[-1])
        self.data = msgs[1:-1]
        sizes = sorted({len(m) for m in self.data})
        if sizes != [DATA_LEN]:
            raise ParseError('data message sizes %s, not all %d' % (sizes, DATA_LEN))
        bad_mfr = [i for i, m in enumerate(msgs) if m[1:4] != MFR]
        if bad_mfr:
            raise ParseError('%d messages without Elektron\'s id 00 20 3C (first: message %d)'
                             % (len(bad_mfr), bad_mfr[0]))
        self.device_id = msgs[0][4]
        self.k = msgs[0][8]
        devs = sorted({m[4] for m in msgs})
        if devs != [self.device_id]:
            self.problems.append('device ids %s differ between messages' % ['%#04x' % d for d in devs])
        heads = sorted({bytes(m[5:7]) for m in self.data})
        if heads != [b'\x00\x7e']:
            self.problems.append('data message bytes 5..6 are %s, not 00 7E' % [h.hex() for h in heads])
        self.bad_checksums = [i for i, m in enumerate(self.data)
                              if message_checksum(m, self.k) != m[126]]
        if self.bad_checksums:
            self.problems.append('%d data messages fail their checksum (first: data message %d)'
                                 % (len(self.bad_checksums), self.bad_checksums[0]))
        self.bad_counters = [i for i, m in enumerate(self.data)
                             if ((m[7] << 14) | (m[8] << 7) | m[9]) != FIRST_COUNTER + i]
        if self.bad_counters:
            self.problems.append('%d data messages carry the wrong counter (first: %d)'
                                 % (len(self.bad_counters), self.bad_counters[0]))
        self.framing_counts = [(f[12] << 14) | (f[13] << 7) | f[14] for f in self.framing]
        if any(c != len(self.data) for c in self.framing_counts):
            self.problems.append('framing counts %s, but %d data messages'
                                 % (self.framing_counts, len(self.data)))
        hi_bits = [i for i, m in enumerate(msgs) for b in m[1:-1] if b & 0x80]
        if hi_bits:
            self.problems.append('%d bytes with bit 7 set inside messages' % len(hi_bits))
        dec = b''.join(decode_payload(m[10:126]) for m in self.data)
        self.stream = dec
        if len(dec) < 8 + TABLE_OFF:
            raise ParseError('decoded stream is only %d bytes' % len(dec))
        self.total, self.checksum = struct.unpack_from('>II', dec, 0)
        if 8 + self.total > len(dec):
            raise ParseError('preamble length %d is past the decoded stream (%d bytes)'
                             % (self.total, len(dec) - 8))
        self.container = dec[8:8 + self.total]
        self.tail = dec[8 + self.total:]
        if self.container[:4] != b'ELE3':
            raise ParseError('no ELE3 magic at the start of the container')
        if len(self.tail) >= CHUNK or any(self.tail):
            self.problems.append('%d bytes after the container (expected < %d zero bytes of '
                                 'chunk padding), %d of them non-zero'
                                 % (len(self.tail), CHUNK, sum(1 for b in self.tail if b)))
        self.header = self.container[:HEADER_LEN]
        n = struct.unpack_from('>I', self.container, COUNT_OFF)[0]
        if not 0 < n <= 16 or TABLE_OFF + ENTRY * n > self.total:
            raise ParseError('section count %d does not fit the container' % n)
        self.table = [struct.unpack_from('>IIII', self.container, TABLE_OFF + ENTRY * i)
                      for i in range(n)]
        self.entries = {t[0]: self.container[TABLE_OFF + ENTRY * i:TABLE_OFF + ENTRY * (i + 1)]
                        for i, t in enumerate(self.table)}
        if len(self.entries) != n:
            raise ParseError('duplicate section ids in the table: %s' % [t[0] for t in self.table])
        self.table_end = TABLE_OFF + ENTRY * n
        body_end = self.total - SEAL
        spans = []
        self.stored = {}
        for sid, off, ln, _dest in self.table:
            if off < self.table_end or off + ln > body_end:
                raise ParseError('section %d (0x%x + %d) lies outside 0x%x..0x%x (table end to seal)'
                                 % (sid, off, ln, self.table_end, body_end))
            spans.append((off, off + ln, sid))
            self.stored[sid] = self.container[off:off + ln]
        spans.sort()
        for (a0, a1, s0), (b0, b1, s1) in zip(spans, spans[1:]):
            if b0 < a1:
                raise ParseError('sections %d and %d overlap' % (s0, s1))

    @classmethod
    def load(cls, path):
        with open(path, 'rb') as fh:
            return cls(fh.read(), path)

    @property
    def version(self):
        a, b = VERSION_FIELD
        return self.header[a:b].decode('ascii', 'replace')

    @property
    def received(self):
        """Bytes the OS's receiver writes: whole 101-byte chunks (T1.6)."""
        return len(self.data) * CHUNK

    def section_of(self, sid):
        return next(t for t in self.table if t[0] == sid)
