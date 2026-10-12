#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Run the brick checker on the red-team candidates and judge each verdict.

    vendor/digikit/.venv/bin/python scripts/t1.7_redteam_run.py [names...]

With no names, runs every candidate in out/brickcheck-redteam/manifest.json.
Writes out/brickcheck-redteam/runs/<name>/ (brickcheck's own report.md/json),
runs/<name>.log (console), and merges a verdict into results.json. A case is
"as expected" when the verdict, the exit code, and every named PASS/FAIL/SKIP
check match the expectation the build recorded.
"""
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(ROOT, 'out', 'brickcheck-redteam')
BRICK = os.path.join(ROOT, 'tools', 'brickcheck', 'brickcheck')
STOCK = os.path.join(ROOT, 'elektron-firmware', 'Digitone_II_OS1_12.syx')
EXIT = {'accept': 0, 'refuse': 1, 'incomplete': 3}


def judge(rep, exit_code, exp):
    probs = []
    if rep['verdict'] != exp['verdict']:
        probs.append('verdict %s, expected %s' % (rep['verdict'], exp['verdict']))
    if exit_code != EXIT[exp['verdict']]:
        probs.append('exit %s, expected %d' % (exit_code, EXIT[exp['verdict']]))
    status = {int(k): v['status'] for k, v in rep['checks'].items()}
    for key in ('pass', 'fail', 'skip'):
        for i in exp.get(key, []):
            if status.get(i) != key.upper():
                probs.append('check %d is %s, expected %s' % (i, status.get(i), key.upper()))
    return probs, status


def fail_lines(rep):
    out = {}
    for k, v in rep['checks'].items():
        f = [e['text'] for e in v['evidence'] if e['status'] == 'FAIL']
        if f:
            out[int(k)] = f
    return out


def run(name, m):
    outdir = os.path.join(OUT, 'runs', name)
    os.makedirs(os.path.dirname(outdir), exist_ok=True)
    args = [BRICK, m['syx'], '--stock', STOCK, '--out', outdir] + m['extra']
    t = time.time()
    with open(os.path.join(OUT, 'runs', name + '.log'), 'w') as fh:
        p = subprocess.run(args, stdout=fh, stderr=subprocess.STDOUT)
    rep = json.load(open(os.path.join(outdir, 'report.json')))
    probs, status = judge(rep, p.returncode, m['expect'])
    return {'name': name, 'seconds': round(time.time() - t, 1), 'exit': p.returncode,
            'verdict': rep['verdict'], 'expected': m['expect'], 'status': status,
            'fail_lines': fail_lines(rep), 'problems': probs, 'note': m['note']}


def main():
    manifest = json.load(open(os.path.join(OUT, 'manifest.json')))
    names = sys.argv[1:] or list(manifest)
    respath = os.path.join(OUT, os.environ.get('REDTEAM_RESULTS', 'results.json'))
    try:
        results = json.load(open(respath)).get('results', {})
    except (OSError, ValueError):
        results = {}
    for name in names:
        r = run(name, manifest[name])
        results[name] = r
        verdict_line = '%-20s %-11s exit %s  %s  %.0fs  %s' % (
            name, r['verdict'], r['exit'],
            ' '.join('%d:%s' % (i, r['status'][i][0]) for i in sorted(r['status'])),
            r['seconds'], 'as expected' if not r['problems'] else 'UNEXPECTED: ' + '; '.join(r['problems']))
        print(verdict_line, flush=True)
        for i, lines in sorted(r['fail_lines'].items()):
            for ln in lines:
                print('        FAIL %d: %s' % (i, ln), flush=True)
        with open(respath, "w") as fh:
            json.dump({'ok': all(not x['problems'] for x in results.values()),
                       'results': results}, fh, indent=1, default=str)
    bad = [n for n in names if results[n]['problems']]
    print('\n%d run, %d as expected, %d unexpected%s'
          % (len(names), len(names) - len(bad), len(bad),
             ': ' + ', '.join(bad) if bad else ''))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
