#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""stockcheck.py: prove that a patch carries no Elektron bytes.

    python3 tools/patcher/stockcheck.py out/patcher/OB01/patch.bin [...] [--stock elektron-firmware/Digitone_II_OS1_12.syx]
                                        [--also OTHER.syx ...]

Every run of K = 16 bytes of the pack as it ships (its header and metadata, every section's ops, lit and skel), and of the literal data end to end (all lit arrays joined, so that a run across two of them counts too), is
looked up in: the stock .syx as stored, its decoded transport stream (the preamble and the ELE3 container, with
Elektron's packed sections as stored), and every stock section decoded. A single hit fails the patch; mkpatch.py runs
this check and writes nothing on a failure. Exit status 1 on a failure.

This proves the patch holds no bytes of the 1.12 file. That the rest is ours comes from what the builds are made of
(our mods and our DSP code: scripts/cmp4_build.py, tools/dspsplice). --also adds other Elektron OS files (ELE3 .syx:
the file, its decoded stream, its sections decoded where they decode) as sources, one at a time: for a build that
might carry code from another box.
"""
import argparse
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mkpatch  # noqa: E402

K = mkpatch.K


def sources_of(syx, tag):
    """-> [(name, bytes)]: a Syx as stored, its decoded transport stream, and every section decoded"""
    out = [(tag + '.syx', syx.raw), (tag + ' decoded stream', struct.pack('>II', syx.total, syx.checksum) + syx.container)]
    for sid, _o, _l, _d in syx.table:
        try:
            out.append((tag + ' section %d decoded' % sid, syx.decoded[sid]))
        except mkpatch.PatchError:
            pass                         # a section this codec does not decode: it is in the stream as stored
    return out


def check(blob, stock, also=()):
    """-> {'ok', 'runs', 'hits', 'sources'}: blob (bytes) against stock (a mkpatch.Syx), and the files in also"""
    head, secs = mkpatch.sections(blob)
    parts = [('header', 0, head['meta_at']), ('metadata', head['meta_at'], head['meta_len'])]
    parts += [('section %d %s' % (c['sid'], k), c['at'][k], len(c[k])) for c in secs for k in mkpatch.PARTS]
    lits = b''.join(c[k] for c in secs for k in ('ilit', 'slit'))
    want = {}                            # run -> its offset in the blob, or -1 - its offset in the literal data
    for i in range(len(lits) - K + 1):
        want[lits[i:i + K]] = -1 - i
    for i in range(len(blob) - K + 1):
        want[blob[i:i + K]] = i
    hits, nhit, seen, by = [], 0, {}, {}

    def scan(srcs):
        nonlocal nhit
        for name, src in srcs:
            seen[name] = len(src)
            for p in range(len(src) - K + 1):
                i = want.get(src[p:p + K])
                if i is None:
                    continue
                nhit += 1
                where = 'literal data end to end' if i < 0 else next(n for n, o, ln in parts if o <= i < o + ln)
                key = '%s in %s' % (where, name)
                by[key] = by.get(key, 0) + 1
                if len(hits) < 20:
                    hits.append({'source': name, 'at': p, 'patch_part': where,
                                 'patch_offset': i if i >= 0 else -1 - i})
    scan(sources_of(stock, 'stock'))
    for path in also:
        try:
            other = mkpatch.Syx(open(path, 'rb').read())
        except mkpatch.PatchError:
            seen[os.path.basename(path) + ' (not ELE3: skipped)'] = 0
            continue
        scan(sources_of(other, os.path.basename(path)))
    return {'ok': not nhit, 'runs': len(want), 'run_bytes': K, 'hits_total': nhit, 'hits_by_place': by, 'hits': hits,
            'sources': seen}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('patch', nargs='+', help='patch.bin files (mkpatch.py writes them)')
    ap.add_argument('--stock', default=mkpatch.STOCK)
    ap.add_argument('--also', nargs='*', default=(), help='other Elektron OS files (.syx) to check against')
    ap.add_argument('--brief', action='store_true', help='print the number of sources, not their names')
    a = ap.parse_args()
    raw = open(a.stock, 'rb').read()
    if mkpatch.sha(raw) != mkpatch.STOCK_SHA:
        raise SystemExit('%s is not the stock 1.12 file' % a.stock)
    stock, bad = mkpatch.Syx(raw), 0
    for p in a.patch:
        r = check(open(p, 'rb').read(), stock, a.also)
        print('%s: %d runs of %d bytes against %s: %s' % (
            p, r['runs'], K, '%d sources, %d bytes' % (len(r['sources']), sum(r['sources'].values())) if a.brief
            else ', '.join('%s (%d B)' % kv for kv in r['sources'].items()),
            'no hit' if r['ok'] else '%d HITS: %s' % (r['hits_total'], r['hits_by_place'])))
        bad += not r['ok']
    sys.exit(1 if bad else 0)


if __name__ == '__main__':
    main()
