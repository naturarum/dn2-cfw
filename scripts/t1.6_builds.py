#!/usr/bin/env python3
"""T1.6: Digitone II 1.12 test builds made with elekloader (vendor/elekloader,
branch dn2-1.12), for the device oracle and the emulator. Writes to
out/T1.6/ (git-ignored), never anywhere else:

  nomod-DN01.syx          the stock image, packed as elekloader packs it for
                          this device (codec/dense.py with its in-place floor),
                          version DN01; formats.verify passes
  zeros120k-DN02.syx      the stock image + 120 KB of zeros, the same way;
                          formats.verify passes (the floor adds literals)
  zeros120k-nofloor.syx   the same image packed without the floor; NEGATIVE
                          CONTROL: verify refuses it (gap < 0 at 0x40200000),
                          and section 4's loader should corrupt it. Never flash.
  aplib-DN03.syx          the stock image packed with codec/aplib.py; NEGATIVE
                          CONTROL for size: verify refuses it (over 2 MiB).
                          Never flash.
  zeros120k.main.bin      the image the zeros builds should depack to
  builds.json             sizes, gaps and verify results

    python3 scripts/t1.6_builds.py [--syx Digitone_II_OS1_12.syx]
"""
import argparse
import hashlib
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'vendor', 'elekloader'))

from elekloader import formats, syx  # noqa: E402
from elekloader.codec import aplib, dense  # noqa: E402

OUT = os.path.join(ROOT, 'out', 'T1.6')


def sha(b):
    return hashlib.sha256(b).hexdigest()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--syx', default=os.path.join(ROOT, 'elektron-firmware', 'Digitone_II_OS1_12.syx'))
    a = ap.parse_args(argv)
    st, dev, rel = formats.load(a.syx)
    assert dev.key == 'digitone-mk2', dev.key
    img = formats.main_image(st, dev)
    zeros = img + bytes(120 * 1024)
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, 'zeros120k.main.bin'), 'wb') as fh:
        fh.write(zeros)
    builds = [
        ('nomod-DN01.syx', img, formats.pack_main(img, dev), 'DN01'),
        ('zeros120k-DN02.syx', zeros, formats.pack_main(zeros, dev), 'DN02'),
        ('zeros120k-nofloor.syx', zeros, dense.pack_section(zeros)[0], 'DN0X'),
        ('aplib-DN03.syx', img, aplib.pack_section(img), 'DN03'),
    ]
    report = {}
    for name, want, stored, version in builds:
        out = formats.write(st, stored, dev, version)['syx']
        gaps = {'0x%08x' % s: syx.inplace_depack(stored, dev, s)[1] for s in dev.stages()}
        try:
            facts = formats.verify({'syx': out}, st, want, dev, version)
            verdict = 'verified'
        except formats.FormatError as e:
            facts, verdict = None, 'refused: %s' % e
        with open(os.path.join(OUT, name), 'wb') as fh:
            fh.write(out)
        report[name] = {'sha256': sha(out), 'bytes': len(out), 'stored_main': len(stored) - 8,
                        'container': syx.Syx(out).total, 'main_sha256': sha(want),
                        'inplace_gaps': gaps, 'verify': verdict,
                        'receive_headroom': facts and facts.get('receive_headroom')}
        print('%-24s stream %9d  container %9d  gaps %s  %s'
              % (name, len(stored) - 8, report[name]['container'], gaps, verdict))
    with open(os.path.join(OUT, 'builds.json'), 'w') as fh:
        json.dump(report, fh, indent=1)


if __name__ == '__main__':
    main()
