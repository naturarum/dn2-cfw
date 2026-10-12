#!/usr/bin/env python3
"""T1.3: what a digikit snapshot holds in address ranges.

For each snapshot: which 1 MB pages are mapped (digikit maps a page on first
touch, read or write, so an unmapped page was never touched), and inside each
range the nonzero bytes: how many, the lowest and highest, and a map of
nonzero bytes per granule.

    python3 scripts/t1.3_snapscan.py SNAP [SNAP ...] --range LO:HI[=NAME] ... [--granule 0x1000]

Reads the snapshot pickle directly (primitive types only, as
emu/snapshot.py's own loader requires), so it needs neither Unicorn nor the
digikit environment.
"""
import argparse
import pickle
import zlib

PAGE = 0x100000


class _Safe(pickle.Unpickler):
    def find_class(self, module, name):
        raise pickle.UnpicklingError('snapshot holds %s.%s' % (module, name))


def load(path):
    with open(path, 'rb') as fh:
        return _Safe(fh).load()


def read(blob, lo, hi):
    out = bytearray(hi - lo)
    mapped = set(blob['all_mapped'])
    for base in range(lo - lo % PAGE, hi, PAGE):
        if base in blob['pages']:
            page = zlib.decompress(blob['pages'][base])
            a, b = max(lo, base), min(hi, base + PAGE)
            out[a - lo:b - lo] = page[a - base:b - base]
    return bytes(out), sorted(b for b in mapped if b + PAGE > lo and b < hi)


def parse_range(s):
    spec, _, name = s.partition('=')
    lo, hi = (int(x, 0) for x in spec.split(':'))
    return lo, hi, name or '0x%08x-0x%08x' % (lo, hi)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('snaps', nargs='+')
    ap.add_argument('--range', action='append', type=parse_range, required=True)
    ap.add_argument('--granule', type=lambda s: int(s, 0), default=0)
    a = ap.parse_args(argv)
    for p in a.snaps:
        blob = load(p)
        print('%s  pc=0x%08x  mapped pages=%d' % (p, blob['regs']['pc'], len(blob['all_mapped'])))
        for lo, hi, name in a.range:
            data, pages = read(blob, lo, hi)
            nz = [i for i, b in enumerate(data) if b]
            print('  %-28s mapped %s' % (name, ','.join('0x%08x' % b for b in pages) or 'none'))
            if nz:
                print('  %-28s nonzero %d bytes, lowest 0x%08x, highest 0x%08x'
                      % ('', len(nz), lo + nz[0], lo + nz[-1]))
            else:
                print('  %-28s all zero' % '')
            if a.granule and nz:
                g = {}
                for i in nz:
                    k = (lo + i) - (lo + i) % a.granule
                    g[k] = g.get(k, 0) + 1
                for k in sorted(g):
                    print('      0x%08x  %d' % (k, g[k]))


if __name__ == '__main__':
    main()
