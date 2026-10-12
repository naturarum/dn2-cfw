#!/usr/bin/env python3
"""T3.4: rough per-block cost of a SHARC routine tree (dn2-1.12 sharcdb).

cost(routine) = sum over its reachable instructions of (product of enclosing DO-loop
trip counts), plus, at each call site, cost(callee) times the same product, plus a fixed
call overhead. Literal trip counts are taken from the DO instruction; register trip
counts come from TRIPS below (site -> count) or fall back to DEFAULT_TRIP (the
32-sample block). Every instruction counts once per pass (both arms of a branch), so
the result is an upper-bound-ish instruction count, not a cycle-exact figure: no
stalls, no DDR latency, no cache misses.

usage: t34_cost.py ENTRY [ENTRY ...] [--trip SITE=N ...] [--verbose]
"""
import sys, re
sys.path.insert(0, os.path.join(ROOT, 'scripts'))
import t34_tree as T
import os
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # the project folder

DEFAULT_TRIP = 32
CALL_OVERHEAD = 6     # cjump + two pushes + return + frame restore, roughly
TRIPS = {}
# Loops built from a backward conditional jump instead of DO: jump site -> (head, trips).
BACK = {
    0xb84bf5: (0xb84b85, 32),   # chorus: per-sample LFO / delay-line loop (R14 = block length)
    0xb85054: (0xb8503b, 2),    # compressor: side-chain filter, once per channel (R14 = 2)
    0xb850f4: (0xb850ca, 32),   # compressor: per-sample level detector (R15 = block length)
    0xb85277: (0xb8524d, 32),   # compressor: per-sample gain computer (R14 = block length)
    0xb834db: (0xb834be, 4),    # reverb: reset helper loop (R15 = 4)
}
# Typical-path costs for library routines whose walked set includes rare error paths.
OVERRIDE = {
    0x1c0c76: 150,   # powf: 127-instruction main path plus a Newton reciprocal; error paths excluded
    0xb8b4dc: 30,
}

DO_RE = re.compile(r'DO (0x[0-9a-f]+) UNTIL LCE\s+\(trip count (\w+)(?:, (\w+))?\)')

memo = {}

def loops_of(seen):
    loops = []
    for sw in sorted(seen):
        m = T.INSN[sw][2]
        mo = DO_RE.search(m)
        if mo:
            end = int(mo.group(1), 16)
            tc = mo.group(2)
            if sw in TRIPS:
                n = TRIPS[sw]; src = 'set'
            elif tc.startswith('R') or tc.startswith('LCNTR') or not tc[0].isdigit():
                n = DEFAULT_TRIP; src = 'reg %s -> default' % tc
            else:
                n = int(tc, 0); src = 'literal'
            loops.append((sw, end, n, src))
        if sw in BACK:
            head, n = BACK[sw]
            ends = sorted(x for x in seen if x > sw)[:2]
            end = ends[-1] if ends else sw
            loops.append((head - 1, end, n, 'back-branch'))
    return loops

def cost(entry, verbose=False, depth=0, stack=()):
    if entry in memo:
        return memo[entry]
    if entry in stack:
        return 0
    if entry in OVERRIDE:
        memo[entry] = OVERRIDE[entry]
        if verbose:
            print('%s%06x: override %d' % ('  ' * depth, entry, OVERRIDE[entry]))
        return OVERRIDE[entry]
    seen, calls, ind, rets = T.walk(entry)
    loops = loops_of(seen)
    def mult(sw):
        k = 1
        for s, e, n, _ in loops:
            if s < sw <= e:
                k *= n
        return k
    own = sum(mult(sw) for sw in seen)
    sub = 0
    detail = []
    for csw, tgt in calls:
        c = cost(tgt, verbose, depth + 1, stack + (entry,))
        k = mult(csw)
        sub += k * (c + CALL_OVERHEAD)
        detail.append((csw, tgt, k, c))
    total = own + sub
    memo[entry] = total
    if verbose:
        pad = '  ' * depth
        print('%s%06x: own %d (static %d), calls %d, total %d' % (pad, entry, own, len(seen), sub, total))
        for s, e, n, src in loops:
            print('%s   loop %06x-%06x x%d (%s)' % (pad, s, e, n, src))
        for csw, tgt, k, c in detail:
            print('%s   call @%06x -> %06x x%d (each %d)' % (pad, csw, tgt, k, c))
    return total

if __name__ == '__main__':
    a = sys.argv[1:]
    verbose = '--verbose' in a
    if verbose: a.remove('--verbose')
    while '--trip' in a:
        i = a.index('--trip'); s, n = a[i+1].split('='); TRIPS[int(s, 16)] = int(n); del a[i:i+2]
    for e in a:
        memo.clear()
        print('%s total ~%d instructions per call' % (e, cost(int(e, 16), verbose)))
