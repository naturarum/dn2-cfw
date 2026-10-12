#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
# pyright: reportMissingImports=false
"""T1.8: show that a Digitone II 1.12 rebuild differs from stock only in the
ELE3 version field (container 0x14..0x17), the HMAC trailer, the content
checksum in the stream preamble, and the SysEx messages that carry those bytes.

    vendor/digikit/.venv/bin/python scripts/t1.8_verify_version_only.py \
        out/T1.8/EF01.syx [--stock elektron-firmware/Digitone_II_OS1_12.syx] [--json OUT]

Read-only: it never writes the firmware files. Exit 0 when every check holds.
"""
import argparse
import hashlib
import json
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, 'vendor', 'digikit'))
from dt2.container import decode_syx  # noqa: E402

STOCK_SHA = '7e5b8a733d4ab1fdf7e5d22757f7d8eddaa3cbf71d0858874a791844c02ceb35'
VER_LO, VER_HI = 0x14, 0x18          # version field, end exclusive
DIGEST = 32                          # HMAC-SHA256 trailer


def sha(b):
    return hashlib.sha256(b).hexdigest()


def messages(syx):
    out, i = [], 0
    while True:
        a = syx.find(b'\xf0', i)
        if a < 0:
            break
        z = syx.find(b'\xf7', a)
        out.append(syx[a:z + 1])
        i = z + 1
    return out


def ranges(a, b):
    n = min(len(a), len(b))
    out, s = [], None
    for k in range(n):
        if a[k] != b[k]:
            if s is None:
                s = k
        elif s is not None:
            out.append((s, k))
            s = None
    if s is not None:
        out.append((s, n))
    return out


def container(path):
    stream = decode_syx(path)
    size, csum = struct.unpack_from('>II', stream, 0)
    return stream, size, csum, stream[8:8 + size]


def table(c):
    n = struct.unpack_from('>I', c, 0x1C)[0]
    return [struct.unpack_from('>IIII', c, 0x20 + 16 * k) for k in range(n)]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('candidate')
    ap.add_argument('--stock', default=os.path.join(ROOT, 'elektron-firmware', 'Digitone_II_OS1_12.syx'))
    ap.add_argument('--json')
    a = ap.parse_args(argv)

    s_syx = open(a.stock, 'rb').read()
    c_syx = open(a.candidate, 'rb').read()
    res = {'candidate': a.candidate, 'candidate_sha256': sha(c_syx),
           'stock_sha256': sha(s_syx), 'checks': []}

    def check(name, ok, detail):
        res['checks'].append({'check': name, 'ok': bool(ok), 'detail': detail})
        print('%-4s %s: %s' % ('OK' if ok else 'FAIL', name, detail))

    check('stock pinned', sha(s_syx) == STOCK_SHA, sha(s_syx))
    check('syx length', len(s_syx) == len(c_syx), '%d vs stock %d' % (len(c_syx), len(s_syx)))

    s_stream, s_size, s_csum, sc = container(a.stock)
    c_stream, c_size, c_csum, cc = container(a.candidate)
    check('container length', s_size == c_size, '%#x vs stock %#x' % (c_size, s_size))
    check('decoded stream length', len(s_stream) == len(c_stream),
          '%d vs stock %d' % (len(c_stream), len(s_stream)))

    st, ct = table(sc), table(cc)
    check('section table identical', st == ct and sc[0x1C:0x20 + 16 * len(st)] ==
          cc[0x1C:0x20 + 16 * len(ct)], '%d sections' % len(ct))
    for sid, off, ln, dest in ct:
        same = sc[off:off + ln] == cc[off:off + ln]
        check('section %d stored bytes identical' % sid, same,
              'offset %#x len %d sha256 %s' % (off, ln, sha(cc[off:off + ln])[:16]))

    diff = ranges(sc, cc)
    trailer_lo = c_size - DIGEST
    allowed = [(VER_LO, VER_HI), (trailer_lo, c_size)]
    outside = [(x, y) for x, y in diff
               if not any(lo <= x and y <= hi for lo, hi in allowed)]
    check('container differs only in version field and HMAC trailer', not outside,
          'changed ranges: %s' % ', '.join('%#x-%#x' % r for r in diff))
    res['container_diff'] = [['%#x' % x, '%#x' % y] for x, y in diff]

    hdr_s, hdr_c = sc[:0x20], cc[:0x20]
    check('header outside the version field identical',
          hdr_s[:VER_LO] == hdr_c[:VER_LO] and hdr_s[VER_HI:] == hdr_c[VER_HI:],
          'stock %r -> candidate %r' % (hdr_s[0x07:0x18], hdr_c[0x07:0x18]))
    ver = hdr_c[VER_LO:VER_HI]
    printable = all(0x20 <= b < 0x7f for b in ver)
    check('version field printable ASCII, 4 bytes', printable and len(ver) == 4, repr(ver))
    res['version_field'] = ver.decode('latin-1')
    res['bootstrap_shows'] = hdr_c[0x0C:0x18].decode('latin-1').lstrip(' ')

    # trailer region: is everything after the last section stock apart from the digest?
    last_end = max(off + ln for _, off, ln, _ in ct)
    check('bytes between last section and digest identical',
          sc[last_end:trailer_lo] == cc[last_end:trailer_lo],
          '%#x-%#x (%d bytes)' % (last_end, trailer_lo, trailer_lo - last_end))

    check('preamble: content checksum changed only', s_size == c_size,
          'checksum stock %#010x -> %#010x' % (s_csum, c_csum))
    check('stream after container identical', s_stream[8 + s_size:] == c_stream[8 + c_size:],
          '%d bytes of padding' % (len(c_stream) - 8 - c_size))

    sm, cm = messages(s_syx), messages(c_syx)
    check('same SysEx message count', len(sm) == len(cm), '%d' % len(cm))
    changed = [k for k in range(min(len(sm), len(cm))) if sm[k] != cm[k]]
    res['changed_messages'] = changed
    check('changed SysEx messages', len(changed) <= 6,
          '%d of %d: indices %s' % (len(changed), len(cm), changed))
    ok = all(c['ok'] for c in res['checks'])
    res['ok'] = ok
    print('RESULT: %s' % ('version-only rebuild' if ok else 'NOT version-only'))
    if a.json:
        with open(a.json, 'w') as fh:
            json.dump(res, fh, indent=1)
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
