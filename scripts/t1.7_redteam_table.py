#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Merge all out/brickcheck-redteam/results*.json and replace the
<!--RESULTS-TABLE--> marker in notes/T1.7-redteam.md with a verdict table."""
import glob
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'out', 'brickcheck-redteam')
NOTE = os.path.join(ROOT, 'notes', 'T1.7-redteam.md')
ORDER = ['s2-bodyflip', 's4-bodyflip', 's8-bodyflip', 'cksum-broken', 'seal-broken',
         'panel-blobbody', 'updater-body', 'reset-illegal-fast', 'reset-illegal',
         'danger-validator', 'danger-dsp', 'danger-computed']

merged = {}
for f in glob.glob(os.path.join(OUT, 'results*.json')):
    try:
        merged.update(json.load(open(f)).get('results', {}))
    except (OSError, ValueError):
        pass

rows = ['| candidate | hazard | expected | actual verdict | deciding check(s) | match |',
        '|---|---|---|---|---|---|']
for n in ORDER + [k for k in merged if k not in ORDER]:
    r = merged.get(n)
    if not r:
        continue
    st = {int(k): v for k, v in r['status'].items()}
    fails = [k for k in sorted(st) if st[k] == 'FAIL']
    note = r.get('note', '').split('.')[0].replace('DANGER: ', '').replace('DANGER/evasion: ', '')
    note = note.replace('|', '/')[:46]
    exp = r['expected']['verdict']
    deciding = ('check ' + ', '.join(str(k) for k in fails)) if fails else '(none; passes)'
    match = 'yes' if not r['problems'] else 'NO: ' + '; '.join(r['problems'])
    rows.append('| `%s` | %s | %s | **%s** | %s | %s |'
                % (n, note, exp, r['verdict'], deciding, match))
table = '\n'.join(rows)

text = open(NOTE).read()
if '<!--RESULTS-TABLE-->' in text:
    text = text.replace('<!--RESULTS-TABLE-->', table)
else:  # replace a previously injected table region if re-run
    import re
    text = re.sub(r'\| candidate \| hazard \|.*?(?=\n\n)', table, text, flags=re.S)
open(NOTE, 'w').write(text)
print('injected %d rows' % (len(rows) - 2))
for r in rows:
    print(r)
