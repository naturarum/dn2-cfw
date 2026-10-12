#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""T3.4 verify: is there a path from a function entry to a return that avoids a given instruction?
Own CFG from digikit sharcdb (aligned insns + edges), delayed branches take effect after 2 slots.
usage: t34v_avoid.py ENTRY AVOID [AVOID...]   (hex SW)"""
import sqlite3, sys
import os
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # the project folder
DB = os.path.join(ROOT, "vendor/digikit/out/sharcdb/dn2-1.12.sqlite")
db = sqlite3.connect(DB)
I = 'dn2-1.12'
rows = db.execute("select sw from insn where image=? and aligned=1 order by sw", (I,)).fetchall()
order = [r[0] for r in rows]; idx = {s: k for k, s in enumerate(order)}
E = {}
for f, t, kind, dl, c in db.execute("select from_sw,to_sw,kind,delayed,cond from edges where image=?", (I,)):
    E.setdefault(f, []).append((t, kind, dl, c))
def nxt(s, n=1):
    k = idx[s] + n
    return order[k] if k < len(order) else None
def succs(s):
    """returns (list of successors, is_return)"""
    out = []; term = False; ret = False
    for t, kind, dl, c in E.get(s, []):
        if kind in ('jump', 'cond_jump', 'return', 'indirect'):
            if dl:  # handled from the 2nd delay slot
                continue
            if kind == 'return': ret = True; term = True
            elif kind == 'indirect': term = True; out.append(('IND', s))
            else:
                if t is not None: out.append(t)
                if kind == 'jump': term = True
    # delayed branches 2 insns back
    p = order[idx[s] - 2] if idx[s] >= 2 else None
    for t, kind, dl, c in E.get(p, []) if p is not None else []:
        if dl and kind in ('jump', 'cond_jump', 'return', 'indirect'):
            if kind == 'return': ret = True; term = True
            elif kind == 'indirect': term = True; out.append(('IND', p))
            else:
                if t is not None: out.append(t)
                if kind == 'jump': term = True
    if not term: out.append(nxt(s))
    return out, ret
def walk(entry, avoid, limit=200000):
    seen = set(); st = [entry]; rets = []; inds = set()
    while st:
        s = st.pop()
        if s in seen or s in avoid or s is None: continue
        seen.add(s)
        if len(seen) > limit: break
        ss, r = succs(s)
        if r: rets.append(s)
        for x in ss:
            if isinstance(x, tuple): inds.add(x[1]); continue
            # calls: do not descend, fall through (already appended via nxt)
            st.append(x)
    return seen, rets, inds
if __name__ == '__main__':
    entry = int(sys.argv[1], 16)
    base, rets0, ind0 = walk(entry, set())
    print("entry %x: %d insns reachable, returns at %s, indirect at %s" % (entry, len(base), [hex(r) for r in sorted(rets0)], [hex(r) for r in sorted(ind0)]))
    for a in sys.argv[2:]:
        av = int(a, 16)
        seen, rets, inds = walk(entry, {av})
        print("  avoid %x: returns still reachable: %s ; indirect: %s" % (av, [hex(r) for r in sorted(rets)], [hex(r) for r in sorted(inds)]))
