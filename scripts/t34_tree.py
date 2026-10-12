#!/usr/bin/env python3
"""T3.4: walk a SHARC routine from an entry address in digikit's dn2-1.12 sharcdb.

Follows fallthrough / jump / cond_jump edges from the entry (delay slots included),
stops at returns, and recurses into calls. For every routine it reports the
instruction count, DO loops (trip counts), calls, absolute DM addresses,
literal constants (floats decoded) and short summaries. Output is text.

usage: t34_tree.py ENTRY [ENTRY...] [--depth N] [--list] [--json FILE]
"""
import json, sqlite3, struct, sys, re

DB = os.path.join(ROOT, "vendor/digikit/out/sharcdb/dn2-1.12.sqlite")
IMG = "dn2-1.12"
db = sqlite3.connect(DB)

INSN = {}
for sw, width, raw, m, al in db.execute(
        "SELECT sw, width, raw, mnemonic, aligned FROM insn WHERE image=? AND aligned=1", (IMG,)):
    INSN[sw] = (width, raw, m)
SWS = sorted(INSN)
import bisect
import os
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # the project folder
def nxt(sw):
    i = bisect.bisect_right(SWS, sw)
    return SWS[i] if i < len(SWS) else None

EDGES = {}
for f, t, k, d, c, note in db.execute(
        "SELECT from_sw, to_sw, kind, delayed, cond, note FROM edges WHERE image=?", (IMG,)):
    EDGES.setdefault(f, []).append((t, k, d, c, note))

def walk(entry):
    seen = set(); work = [entry]; calls = []; indirect = []; rets = 0
    while work:
        sw = work.pop()
        if sw is None or sw in seen or sw not in INSN:
            continue
        seen.add(sw)
        es = EDGES.get(sw, [])
        m = INSN[sw][2]
        stop = False
        for t, k, d, c, note in es:
            if k == 'call':
                if t is not None:
                    calls.append((sw, t))
            elif k in ('jump', 'cond_jump'):
                if t is not None:
                    work.append(t)
                if k == 'jump':
                    stop = True
                    if d:
                        a = nxt(sw); b = nxt(a) if a else None
                        for x in (a, b):
                            if x is not None: seen.add(x)
            elif k == 'return':
                rets += 1
                stop = True
                if d:
                    a = nxt(sw); b = nxt(a) if a else None
                    for x in (a, b):
                        if x is not None: seen.add(x)
            elif k == 'indirect':
                indirect.append(sw)
                if 'JUMP' in m.upper() and 'IF' not in m.upper():
                    stop = True
        if not stop:
            work.append(nxt(sw))
    return seen, calls, indirect, rets

def fl(v):
    try:
        f = struct.unpack('<f', struct.pack('<I', v & 0xffffffff))[0]
    except Exception:
        return None
    return f

DO_RE = re.compile(r'DO (0x[0-9a-f]+) UNTIL LCE\s+\(trip count (\w+)(?:, (\w+))?\)')

def summarize(entry, seen):
    out = {}
    lst = sorted(seen)
    out['n'] = len(lst)
    out['span'] = (min(lst), max(lst)) if lst else None
    dos = []
    for sw in lst:
        m = INSN[sw][2]
        mo = DO_RE.search(m)
        if mo:
            end = int(mo.group(1), 16)
            body = [s for s in lst if sw < s <= end]
            dos.append((sw, end, mo.group(2), mo.group(3), len(body)))
        elif m.startswith('DO ') or ' DO ' in m or 'LCNTR' in m:
            dos.append((sw, None, m, None, None))
    out['dos'] = dos
    lits = db.execute(
        "SELECT sw, value, dest_reg FROM literals WHERE image=? AND sw BETWEEN ? AND ?",
        (IMG, lst[0], lst[-1])).fetchall() if lst else []
    out['lits'] = [(s, v, r) for s, v, r in lits if s in seen]
    mems = db.execute(
        "SELECT sw, direction, width, abs_address FROM mem_access WHERE image=? AND sw BETWEEN ? AND ? AND abs_address IS NOT NULL",
        (IMG, lst[0], lst[-1])).fetchall() if lst else []
    out['abs'] = [(s, d, w, a) for s, d, w, a in mems if s in seen]
    fops = sum(1 for s in lst if re.search(r'\bF\d+ = |fadd|fsub|fmul|\* F', INSN[s][2]))
    out['float_ops'] = fops
    return out

def show(entry, depth, maxdepth, done, listing, js):
    pad = '  ' * depth
    seen, calls, ind, rets = walk(entry)
    s = summarize(entry, seen)
    tag = '' if entry not in done else ' (seen above)'
    print('%s%06x: %d insns, span %s, returns %d, float-ish %d%s' % (
        pad, entry, s['n'], '%06x-%06x' % s['span'] if s['span'] else '-', rets, s['float_ops'], tag))
    if entry in done:
        return
    done.add(entry)
    js[hex(entry)] = {
        'n_insns': s['n'], 'span': [hex(x) for x in s['span']] if s['span'] else None,
        'calls': [[hex(a), hex(b)] for a, b in calls], 'indirect': [hex(x) for x in ind],
        'do_loops': [[hex(a), hex(b) if b else None, c, d, e] for a, b, c, d, e in s['dos']],
        'abs': [[hex(a), d, w, hex(x)] for a, d, w, x in s['abs']],
        'literals': [[hex(a), hex(v & 0xffffffff), r] for a, v, r in s['lits']],
    }
    for sw, end, tc, kind, nb in s['dos']:
        print('%s  DO @%06x end %s trip %s %s body %s' % (pad, sw, '%06x' % end if end else '?', tc, kind or '', nb))
    if ind:
        print('%s  indirect at %s' % (pad, ' '.join('%06x' % x for x in ind)))
    absd = {}
    for a, d, w, x in s['abs']:
        absd.setdefault(x, set()).add(d[0])
    if absd:
        print('%s  abs: %s' % (pad, ' '.join('%x%s' % (x, ''.join(sorted(v))) for x, v in sorted(absd.items()))))
    lv = []
    for a, v, r in s['lits']:
        v &= 0xffffffff
        f = fl(v)
        if 0x3000000 < v and f is not None and 1e-6 < abs(f) < 1e7 and (v >> 23) & 0xff not in (0,):
            lv.append('%s=%x(%.6g)' % (r or '?', v, f))
        else:
            lv.append('%s=%x' % (r or '?', v))
    if lv:
        print('%s  lits: %s' % (pad, ' '.join(lv)))
    if listing:
        for sw in sorted(seen):
            print('%s    %06x %s' % (pad, sw, INSN[sw][2]))
    if depth < maxdepth:
        for csw, t in calls:
            print('%s  call @%06x ->' % (pad, csw))
            show(t, depth + 2, maxdepth, done, listing, js)

if __name__ == '__main__':
    a = sys.argv[1:]
    depth = 6; listing = False; jsf = None
    if '--depth' in a:
        i = a.index('--depth'); depth = int(a[i+1]); del a[i:i+2]
    if '--list' in a:
        a.remove('--list'); listing = True
    if '--json' in a:
        i = a.index('--json'); jsf = a[i+1]; del a[i:i+2]
    js = {}
    done = set()
    for e in a:
        show(int(e, 16), 0, depth, done, listing, js)
        print()
    if jsf:
        json.dump(js, open(jsf, 'w'), indent=1)
