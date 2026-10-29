#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Digiwerk 0.2.0: the builds tables of README.md and docs/release/digiwerk-0.2.0.md, from the packs' hand-written
JSON (tools/patcher/packs/NAME.json: played on a unit or not) and their build records (out/NAME/build-NAME.json: the
sha256 of the build the patcher makes) and brick checker reports (out/brickcheck/NAME-*/report.json).

    python3 scripts/release_tables.py [--check]

Rewrites the table under README's "### The builds" (the lines from the first "| build" to the blank line after
it) and the one under the release notes' "**The builds**"; --check only says whether they are up to date.
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKS = ('EEEE', 'CEEE', 'EFEE', 'EETE', 'EEES', 'CFEE', 'CETE', 'CEES', 'EFTE', 'EFES', 'EETS', 'CFTE', 'CFES', 'EFTS')
OURS = {'C': '**3-band compressor**', 'F': '**flanger**', 'T': '**tape echo**', 'S': '**spring reverb**'}


def accept(name, syx_sha):
    """the brick checker's verdict for exactly this build, as mkpatch.py finds it: '12 of 12' or None"""
    sys.path.insert(0, os.path.join(ROOT, 'tools', 'patcher'))
    import mkpatch  # noqa: E402
    try:
        f = mkpatch.brickcheck(name, syx_sha)
    except mkpatch.PatchError:
        return None
    return '%d of %d' % (f['passed'], f['total'])


def rows():
    out = []
    for n in PACKS:
        p = json.load(open(os.path.join(ROOT, 'tools', 'patcher', 'packs', n + '.json')))
        b = json.load(open(os.path.join(ROOT, 'out', n, 'build-%s.json' % n)))
        out.append(dict(name=n, slots=[OURS[o] if ch == o else 'Elektron\'s' for ch, o in zip(n, 'CFTS')],
                        played=p['tested_on_unit'] or 'not yet', sha=b['syx']['sha256'], bc=accept(n, b['syx']['sha256'])))
    return out


def readme_table(rs):
    t = ['| build | compressor | chorus slot | delay slot | reverb slot | played on a Digitone II |',
         '|---|---|---|---|---|---|']
    t += ['| **%s** | %s | %s |' % (r['name'], ' | '.join(r['slots']), r['played']) for r in rs]
    return '\n'.join(t)


def notes_table(rs):
    t = ['| build | compressor | chorus slot | delay slot | reverb slot | brick check | played on a Digitone II | sha256 of the build |',
         '|---|---|---|---|---|---|---|---|']
    t += ['| **%s** | %s | %s | %s | `%s` |' % (r['name'], ' | '.join(r['slots']), r['bc'] or 'NOT YET', r['played'], r['sha'])
          for r in rs]
    return '\n'.join(t)


def replace(path, anchor, table):
    s = open(path).read()
    i = s.index(anchor)
    i = s.index('\n| build', i) + 1
    j = s.index('\n\n', i)
    return s, s[:i] + table + s[j:]


def main():
    rs = rows()
    stale = []
    for path, anchor, table in ((os.path.join(ROOT, 'README.md'), '### The builds', readme_table(rs)),
                                (os.path.join(ROOT, 'docs', 'release', 'digiwerk-0.2.0.md'), '**The builds**',
                                 notes_table(rs))):
        if not os.path.exists(path):
            continue
        try:
            old, new = replace(path, anchor, table)
        except ValueError:
            raise SystemExit('%s: no table under %r' % (path, anchor))
        if old != new:
            stale.append(os.path.relpath(path, ROOT))
            if '--check' not in sys.argv:
                open(path, 'w').write(new)
    missing = [r['name'] for r in rs if not r['bc']]
    print('tables %s; brick checker not yet ACCEPT for: %s' % (
        ('stale: ' + ', '.join(stale)) if stale and '--check' in sys.argv else ('written: ' + ', '.join(stale)) if stale
        else 'up to date', ', '.join(missing) or 'none'))
    if '--check' in sys.argv and (stale or missing):
        sys.exit(1)


if __name__ == '__main__':
    main()
