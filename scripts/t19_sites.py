#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""T1.9: find core-dt2's sites and routines (Digitakt II 1.17) in Digitone II 1.12.

For each DT2 address, take a window of code around it, mask every 32-bit word that
looks like an absolute address into the main OS (0x40000000-0x47ffffff) and every
16-bit pc-relative displacement after a bsr/bra/jsr (d16,pc), and search the DN2
image for the masked pattern. Prints each hit with how many bytes matched.

    python3 scripts/t19_sites.py            # the default list (core-dt2's mod.json)
Reads only: out/T1.11/Digitakt_II_OS1.17.syx and ele3_out/02_type3.unpacked.bin.
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'vendor', 'elekloader'))
from elekloader import formats  # noqa: E402

LOAD = 0x40000400


def dt2_image():
    p, dev, rel = formats.load(os.path.join(ROOT, 'out/T1.11/Digitakt_II_OS1.17.syx'))
    return formats.main_image(p, dev)


def dn2_image():
    return open(os.path.join(ROOT, 'ele3_out/02_type3.unpacked.bin'), 'rb').read()


def mask(img, a, before, after):
    """-> (bytes, mask) for img[a-before : a+after], with absolute addresses masked."""
    o0, o1 = a - LOAD - before, a - LOAD + after
    b = bytearray(img[o0:o1])
    m = bytearray(b'\xff' * len(b))
    for i in range(0, len(b) - 3, 2):
        w = int.from_bytes(b[i:i + 4], 'big')
        if 0x40000000 <= w < 0x48000000 or 0x80000000 <= w < 0x80010000:
            m[i:i + 4] = b'\0\0\0\0'
    # bsr.w / bra.w / jsr (d16,pc) displacements: 6100 xxxx, 6000 xxxx, 4eba xxxx
    for i in range(0, len(b) - 3, 2):
        op = b[i:i + 2]
        if op in (b'\x61\x00', b'\x60\x00', b'\x4e\xba', b'\x4e\xfa', b'\x41\xfa', b'\x43\xfa',
                  b'\x45\xfa', b'\x47\xfa', b'\x49\xfa'):
            m[i + 2:i + 4] = b'\0\0'
        if op[0] == 0x61 and op[1] not in (0, 0xff):   # bsr.b
            m[i + 1] = 0
    return bytes(b), bytes(m)


def find(img, pat, msk, limit=8):
    """Exact masked search (step 2). -> list of offsets."""
    hits = []
    # anchor on the longest unmasked run
    best, cur, start, bs = 0, 0, 0, 0
    for i, x in enumerate(msk):
        if x:
            if cur == 0:
                start = i
            cur += 1
            if cur > best:
                best, bs = cur, start
        else:
            cur = 0
    anchor = pat[bs:bs + best]
    for mo in re.finditer(re.escape(anchor), img):
        o = mo.start() - bs
        if o < 0 or o % 2:
            continue
        seg = img[o:o + len(pat)]
        if len(seg) < len(pat):
            continue
        if all((s & k) == (p & k) for s, p, k in zip(seg, pat, msk)):
            hits.append(o)
            if len(hits) >= limit:
                break
    return hits


def locate(dt2, dn2, a, windows=((32, 32), (24, 24), (16, 16), (12, 12), (8, 8))):
    for before, after in windows:
        pat, msk = mask(dt2, a, before, after)
        hits = find(dn2, pat, msk)
        if len(hits) == 1:
            return LOAD + hits[0] + before, before, after, 1
        if len(hits) > 1:
            return None, before, after, len(hits)
    return None, 0, 0, 0


DEFAULT = [
    ('boot site', 0x40000538),
    ('tick site', 0x40032ad4), ('tick target 0x4011bc60', 0x4011bc60),
    ('draw site', 0x40032b3a), ('DRAWALL', 0x4011bc96),
    ('key site', 0x40033d94), ('KEYDISP', 0x40030bd0),
    ('enc site', 0x40033dde), ('ENCDISP', 0x40030b64),
    ('settings site', 0x400a5eac), ('SETTINGS_RET', 0x400a5eb2),
    ('personalize site', 0x4009e2a2),
    ('OP_NEW', 0x4011ebd0), ('ITEM_CTOR', 0x40115a44), ('MENU_ADD', 0x4011527a),
    ('FN_MGR', 0x4019d8a8), ('VBR_FN', 0x400019b0),
]


def main():
    dt2, dn2 = dt2_image(), dn2_image()
    for name, a in DEFAULT:
        got, b, af, n = locate(dt2, dn2, a)
        if got:
            print('%-24s DT2 0x%08x -> DN2 0x%08x  (window -%d/+%d, unique)  DT2 %s  DN2 %s'
                  % (name, a, got, b, af, dt2[a - LOAD:a - LOAD + 6].hex(),
                     dn2[got - LOAD:got - LOAD + 6].hex()))
        else:
            print('%-24s DT2 0x%08x -> %s' % (name, a, '%d hits at window -%d/+%d' % (n, b, af)
                                                if n else 'no match'))


if __name__ == '__main__' and '--verify' not in sys.argv:
    main()


# ---- whole-function comparison and the branch-into-site scan (--verify) ----------------

def functions():
    """DN2 functions.tsv -> sorted [(entry, size)]."""
    out = []
    for ln in open(os.path.join(ROOT, 'out/decomp/main_os/functions.tsv')):
        f = ln.split('\t')
        if f[0].startswith('0x'):
            out.append((int(f[0], 16), int(f[2])))
    return sorted(out)


def containing(funcs, a):
    for e, n in funcs:
        if e <= a < e + n:
            return e, n
    return None, None


def masked_equal(dt2, da, dn2, na, n):
    """Compare n bytes at da (DT2) and na (DN2) with each side's addresses masked."""
    p1, m1 = mask(dt2, da, 0, n)
    p2, m2 = mask(dn2, na, 0, n)
    diff = [i for i in range(n) if (p1[i] & m1[i] & m2[i]) != (p2[i] & m1[i] & m2[i])]
    return diff


# (DT2 entry, DN2 entry, what) for the routines core calls and the functions holding its sites
PAIRS = [
    (0x4011bc60, 0x4011d5d4, 'the tick site\'s callee (dirty byte +0x20)'),
    (0x4011bc96, 0x4011d60a, 'DRAWALL'),
    (0x40030bd0, 0x4002c5c8, 'KEYDISP'),
    (0x40030b64, 0x4002c55c, 'ENCDISP'),
    (0x4011ebd0, 0x40120544, 'OP_NEW'),
    (0x40115a44, 0x401173b8, 'ITEM_CTOR'),
    (0x4011527a, 0x40116bee, 'MENU_ADD'),
    (0x4019d8a8, 0x4018de3e, 'FN_MGR'),
    (0x400019b0, 0x400019b0, 'VBR_FN'),
]

SITES = [  # DN2 address, length, what
    (0x40000538, 6, 'boot'), (0x4002e464, 6, 'ev_tick'), (0x4002e4ca, 6, 'ev_draw'),
    (0x4002f20c, 6, 'ev_key'), (0x4002f256, 6, 'ev_enc'), (0x4009ff88, 6, 'ev_settings'),
    (0x400987a2, 6, 'ev_personalize'), (0x4012b490, 6, 'ev_midi_note'),
    (0x400da0b2, 6, 'ev_midi_step (step handler)'), (0x400d9230, 6, 'ev_midi_step (pending step)'),
]


def branch_targets(dis_path):
    """Every explicit target in an objdump listing of the image: b<cc>/bra/bsr/jsr/jmp/pea/lea
    with an absolute or pc-relative address. -> {target: [sources]}."""
    tg = {}
    rx = re.compile(r'^([0-9a-f]{8}):\s.*?\t(b\w+|jsr|jmp|dbra|pea|lea)\s+(?:%pc@\()?(0x[0-9a-f]+)')
    for ln in open(dis_path):
        mo = rx.match(ln)
        if mo:
            tg.setdefault(int(mo.group(3), 16), []).append(int(mo.group(1), 16))
    return tg


def verify():
    dt2, dn2 = dt2_image(), dn2_image()
    funcs = functions()
    print('-- routines core calls: whole DN2 function vs DT2, addresses masked')
    for da, na, what in PAIRS:
        e, n = containing(funcs, na)
        if e != na:
            print('  %-44s DN2 0x%08x is not a function entry in functions.tsv' % (what, na))
            continue
        diff = masked_equal(dt2, da, dn2, na, n)
        print('  %-44s DT2 0x%08x DN2 0x%08x %4d bytes: %s'
              % (what, da, na, n, 'identical' if not diff else '%d bytes differ (first +%d)'
                 % (len(diff), diff[0])))
    print('-- the functions that hold the sites')
    for a, ln, what in SITES:
        e, n = containing(funcs, a)
        print('  %-28s site 0x%08x in FUN_%08x (%d bytes)' % (what, a, e or 0, n or 0))
    dis = os.path.join(ROOT, 'out/T1.9/main.dis')
    if os.path.exists(dis):
        tg = branch_targets(dis)
        print('-- explicit branch targets strictly inside a site (must be none)')
        bad = 0
        for a, ln, what in SITES:
            inside = [(t, tg[t]) for t in range(a + 1, a + ln) if t in tg]
            # objdump also decodes data as code; report only sources inside known functions
            real = [(t, [s for s in srcs if containing(funcs, s)[0]]) for t, srcs in inside]
            real = [(t, s) for t, s in real if s]
            if real:
                bad += 1
            print('  %-28s 0x%08x+%d: %s' % (what, a, ln, real or 'none'))
            starts = tg.get(a, [])
            if starts:
                print('  %-28s   (targets of the site itself, fine: %s)'
                      % ('', ' '.join('0x%08x' % s for s in starts[:6])))
        print('  %d sites with a branch into their middle' % bad)


if __name__ == '__main__' and '--verify' in sys.argv:
    verify()


def switch_targets(dis_path, img):
    """gcc's ColdFire switch: `jmp (T,pc,dn.l)` (4efb 0802) followed by a table of 16-bit
    offsets from T. Reads each table until it reaches the lowest target. -> set of targets."""
    tabs = []
    for ln in open(dis_path):
        mo = re.match(r'^([0-9a-f]{8}):\s+4efb [01]802\s+jmp %pc@\((0x[0-9a-f]+),%d\d:l\)', ln)
        if mo:
            tabs.append(int(mo.group(2), 16))
    targets = set()
    for T in tabs:
        i, lowest = 0, 1 << 40
        while T + 2 * i < lowest and i < 512:
            off = int.from_bytes(img[T - LOAD + 2 * i:T - LOAD + 2 * i + 2], 'big', signed=True)
            t = T + off
            if t <= T:
                break
            lowest = min(lowest, t)
            targets.add(t)
            i += 1
    return len(tabs), targets


if __name__ == '__main__' and '--verify' in sys.argv:
    n, tg = switch_targets(os.path.join(ROOT, 'out/T1.9/main.dis'), dn2_image())
    print('-- switch tables (jmp (T,pc,dn.l), 4efb 0802 and 1802): %d tables, %d targets' % (n, len(tg)))
    for a, ln, what in SITES:
        bad = sorted(t for t in tg if a < t < a + ln)
        print('  %-28s %s' % (what, 'a case starts inside: %s' % [hex(t) for t in bad] if bad
                                    else 'no case starts inside'))
