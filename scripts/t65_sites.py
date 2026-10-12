#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""T6.5: every site in the DN2 OS 1.12 main OS that a grown parameter table must change, found
again from the bytes (independently of T6.1's lists, then compared with them).

    venv/bin/python scripts/t65_sites.py [--rows 0x180] [--out out/T6.5/inventory]

Reads the stock main OS (ele3_out/02_type3.unpacked.bin) and the decompile dump's instruction
listing (out/decomp/main_os/disasm). Writes only under --out. As a module, `plan(rows)` gives
the site list that scripts/t65_make_core.py turns into core-dn2's mod.json.

What it finds, and how:
  1. descriptor operands: every instruction whose bytes hold, after the opcode word, a 32-bit
     value inside the descriptor table 0x401f83a4..0x401fcee0 (row, field). The value one past
     the end (0x401fcee0) is the next table and is listed, not changed. A raw scan of every
     2-aligned word outside the dump's instructions must find none.
  2. row-count compares: every instruction holding the 32-bit immediate 0x13f, 0x140, 0x141 or
     0x142 (any opcode), plus a raw byte scan for the cmpi.l / cmpa.l / cmp.l #imm forms over
     the whole image, each hit checked to be an instruction the dump knows. Each is classified
     by hand below (NOT_BOUNDS): the 58 bounds change, the rest stay.
  3. the ParameterUpdateCache: every instruction in its five functions (constructor, destructor,
     get, set, flush) that holds one of its row-count-derived constants, which must be exactly
     the CACHE list below (the plan fails otherwise).
  4. anything else sized by the row count: 32/16-bit immediates and displacements equal to
     n x 0x141 or n x 0x140 (n = 1..256) anywhere in the dump, listed for review.
"""
import argparse
import glob
import json
import os
import re
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
IMAGE = os.path.join(ROOT, 'ele3_out', '02_type3.unpacked.bin')
IMAGE_SHA = 'a098f620019b2ad38fd7e0397df39501fb1cf6bc26d0283915f34c49446fdf1a'
DISASM = os.path.join(ROOT, 'out', 'decomp', 'main_os', 'disasm')
LOAD = 0x40000400
CODE_END = 0x401d07fc               # end of code (T1.3); data after it

DESC, ROW, ROWS0 = 0x401f83a4, 0x3c, 0x141
DESC_END = DESC + ROWS0 * ROW       # 0x401fcee0: the next table (sound-format upgrade), not ours
DISP, XROW = 0x4243425c, 0x44
DISP_END = DISP + ROWS0 * XROW      # 0x424397a0: a static guard, not ours

ACCESSOR = 0x400c257c               # the display table's only reader; core-dn2 replaces its head
ACCESSOR_HEAD = '202f00040c8000000141'   # move.l 4(sp),d0; cmpi.l #0x141,d0
SINIT_CALL = 0x401ae0cc             # jsr 0x400c2c4e (the static initializer) in FUN_401ae0c8
SINIT_CALL_STOCK = '4eb9400c2c4e'

# compares against the count's neighbours that are not the row count (notes/T6.1 2.3, re-read here)
NOT_BOUNDS = {
    0x4006ef0a: 'row 0x13f identity in the filter page draw FUN_4006eabe (beq/bne to a row id)',
    0x4006ef14: 'row 0x140 identity in the filter page draw FUN_4006eabe',
    0x40031fee: 'KitActive::vslot09: a loop over 16 x 0x14, not rows',
    0x40032422: 'FUN_40032364: the same loop',
    0x400325e2: 'FUN_40032510: the same loop',
}

# The ParameterUpdateCache (vtable 0x401d9c60), made once by FUN_4018e93c, 0xfff4 bytes:
#   +0x18          u16 value[2 banks][17 lanes][N rows]       lane stride 2N, bank stride 34N
#   +0x18+68N      u32 dirty[2 banks][17 lanes][N words]       lane stride 4N, bank stride 68N
#                  (bit r of the bitmap; only N bits are used, the flush scans all N words)
#   +0x18+204N     u32 bank
#   +0x1c+204N     std::vector<(row, value)>: begin, end, end of storage
#   size 0x28+204N
# With N = 0x141 these are 0x282, 0x2aa2, 0x504, 0x5544, 0x555c, 0xffe4, 0xffe8/ec/f0, 0xfff4.
CACHE_FUNCS = {
    0x4018e93c: 'FUN_4018e93c (singleton: allocate and construct)',
    0x4018e7e6: 'ParameterUpdateCache::vslot00 (destructor)',
    0x400f51a4: 'FUN_400f51a4 (get: dirty? then the value)',
    0x400f5264: 'FUN_400f5264 (set: value + dirty bit)',
    0x400f5352: 'FUN_400f5352 (flush: every dirty bit of every lane -> listeners)',
}


def cache_consts(n):
    bank = 0x18 + 204 * n
    return {'V': 2 * n, 'B': 34 * n, 'D': 4 * n, 'DB': 68 * n, 'DIRTY': 0x18 + 68 * n,
            'BANK': bank, 'VEC': bank + 4, 'VEC4': bank + 8, 'SIZE': bank + 0x10}


# (addr, stock hex, constant name, how the constant sits in the instruction)
#   'l2': 32-bit at +2; 'w2': 16-bit at +2 (an immediate or a d16 displacement); 'b3': the low byte
#   of a move.b #imm,Dn that patches the low byte of a register holding VEC (VEC4's low byte)
CACHE = [
    (0x4018e94e, '2f3c0000fff4', 'SIZE', 'l2', 'move.l #size,-(sp): operator new'),
    (0x4018e95c, '2f3c0000fff4', 'SIZE', 'l2', 'move.l #size,-(sp): memset length'),
    (0x4018e986, '203c0000ffe4', 'BANK', 'l2', 'move.l #bank,d0: clr.l bank'),
    (0x4018e990, '223c0000ffe8', 'VEC', 'l2', 'move.l #vec,d1: clr.l the vector'),
    (0x4018e7e8, '203c0000ffe8', 'VEC', 'l2', 'destructor: the vector\'s storage'),
    (0x400f51ca, '283c00000504', 'D', 'l2', 'get: lane stride of dirty'),
    (0x400f51e6, '223c00005544', 'DB', 'l2', 'get: bank stride of dirty'),
    (0x400f51f0, '203c0000ffe4', 'BANK', 'l2', 'get: bank'),
    (0x400f5202, '41e8555c', 'DIRTY', 'w2', 'get: lea dirty(a0),a0'),
    (0x400f5222, '223c00002aa2', 'B', 'l2', 'get: bank stride of value'),
    (0x400f5230, '203c00000282', 'V', 'l2', 'get: lane stride of value'),
    (0x400f5296, '203c0000ffe4', 'BANK', 'l2', 'set: bank'),
    (0x400f52a4, '2a3c00002aa2', 'B', 'l2', 'set: bank stride of value'),
    (0x400f52ae, '3a3c0504', 'D', 'w2', 'set: move.w #lane stride of dirty,d5w (d5 upper word 0)'),
    (0x400f52b6, '223c00000282', 'V', 'l2', 'set: lane stride of value'),
    (0x400f52cc, '223c00005544', 'DB', 'l2', 'set: bank stride of dirty'),
    (0x400f52de, '41e8555c', 'DIRTY', 'w2', 'set: lea dirty(a0),a0'),
    (0x400f536c, '243c0000ffe4', 'BANK', 'l2', 'flush: bank'),
    (0x400f53c4, '203c00005544', 'DB', 'l2', 'flush: bank stride of dirty'),
    (0x400f53ce, '207c0000ffe8', 'VEC', 'l2', 'flush: the vector'),
    (0x400f53dc, '203c00002aa2', 'B', 'l2', 'flush: bank stride of value'),
    (0x400f53e6, '47eb555c', 'DIRTY', 'w2', 'flush: lea dirty(a3),a3'),
    (0x400f5400, '227c0000ffe8', 'VEC', 'l2', 'flush: the vector'),
    (0x400f5436, '207c0000ffe8', 'VEC', 'l2', 'flush: the vector'),
    (0x400f5460, '0c8200000504', 'D', 'l2', 'flush: cmpi.l #words per lane: end of the scan'),
    (0x400f5468, '203c0000ffec', 'VEC4', 'l2', 'flush: the vector\'s end'),
    (0x400f546e, '2e3c0000ffe8', 'VEC', 'l2', 'flush: the vector'),
    (0x400f54d0, '203c0000ffe8', 'VEC', 'l2', 'flush: the vector'),
    (0x400f54e4, '103cffec', 'VEC4', 'b3', 'flush: move.b #-0x14,d0b turns d0 = vec into vec+4'),
    (0x400f552e, '223c0000ffe8', 'VEC', 'l2', 'flush: the vector'),
    (0x400f5534, '203c0000ffec', 'VEC4', 'l2', 'flush: the vector\'s end'),
    (0x400f5546, '47eb0504', 'D', 'w2', 'flush: lea next lane (dirty),a3'),
    (0x400f554a, '49ec0282', 'V', 'w2', 'flush: lea next lane (value),a4'),
]


def u32(b, o):
    return struct.unpack('>I', b[o:o + 4])[0]


def u16(b, o):
    return struct.unpack('>H', b[o:o + 2])[0]


def load_image():
    img = open(IMAGE, 'rb').read()
    import hashlib
    if hashlib.sha256(img).hexdigest() != IMAGE_SHA:
        raise SystemExit('%s is not the stock 1.12 main OS' % IMAGE)
    return img


def instructions():
    """addr -> (bytes, text, function file stem) for every instruction of the decompile dump."""
    ins = {}
    for path in glob.glob(os.path.join(DISASM, '*.s')):
        func = os.path.basename(path)[:-2]
        for line in open(path):
            m = re.match(r'^([0-9a-f]{8})  ((?:[0-9a-f]{4} )+)\s*(.*)$', line.rstrip('\n'))
            if m:
                ins[int(m.group(1), 16)] = (bytes.fromhex(m.group(2).replace(' ', '')),
                                            m.group(3).strip(), func)
    return ins


def nxt(ins, a):
    bs, text, _ = ins[a]
    b = a + len(bs)
    return ins.get(b, (b'', '?', ''))[1]


def scan(img, ins):
    """-> dict of everything found (no decisions about N yet)."""
    out = {'desc': [], 'desc_end': [], 'desc_data': [], 'count': [], 'count_raw_unknown': [],
           'disp_code': [], 'disp_data': [], 'multiples': []}
    covered = set()
    for a, (bs, text, func) in ins.items():
        assert img[a - LOAD:a - LOAD + len(bs)] == bs, hex(a)
        covered.update(range(a, a + len(bs)))
    for a, (bs, text, func) in sorted(ins.items()):
        for o in range(2, len(bs) - 3, 2):
            v = u32(bs, o)
            if DESC <= v < DESC_END:
                out['desc'].append({'addr': a, 'off': o, 'len': len(bs), 'value': v,
                                    'row_field': divmod(v - DESC, ROW), 'func': func, 'text': text})
            elif v == DESC_END:
                out['desc_end'].append({'addr': a, 'func': func, 'text': text})
            elif DISP <= v <= DISP_END:
                out['disp_code'].append({'addr': a, 'value': v, 'func': func, 'text': text})
            if o == 2 and v in (0x13f, 0x140, 0x141, 0x142):
                out['count'].append({'addr': a, 'imm': v, 'len': len(bs), 'func': func, 'text': text,
                                     'next': nxt(ins, a)})
        # word immediates equal to the count's neighbours (cmpi.w, move.w ...)
        if len(bs) == 4 and u16(bs, 2) in (0x13f, 0x140, 0x141, 0x142):
            out['count'].append({'addr': a, 'imm': u16(bs, 2), 'len': 4, 'func': func, 'text': text,
                                 'next': nxt(ins, a), 'word': True})
        # multiples of the count (and of count-1) as 32-bit or 16-bit operands
        cands = set()
        for o in range(2, len(bs) - 1, 2):
            cands.add(('w', u16(bs, o)))
            if o + 4 <= len(bs):
                cands.add(('l', u32(bs, o)))
        imms = {int(x, 16) for x in re.findall(r'#0x([0-9a-f]+)', text)}
        for kind, v in cands:
            if v not in imms:                      # immediates only (displacements are offsets)
                continue
            for base in (0x141, 0x140):
                if v and v % base == 0 and 2 <= v // base <= 256:
                    out['multiples'].append({'addr': a, 'value': v, 'n': v // base, 'base': base,
                                             'size': kind, 'func': func, 'text': text})
    # raw scans outside the dump's instructions
    for o in range(0, len(img) - 3, 2):
        a = LOAD + o
        if a in covered or DESC <= a < DESC_END:
            continue
        v = u32(img, o)
        if DESC <= v < DESC_END:
            out['desc_data'].append({'addr': a, 'value': v})
        if DISP <= v <= DISP_END:
            out['disp_data'].append({'addr': a, 'value': v})
    # raw scan for the compare forms with a count immediate, anywhere in code
    for o in range(0, CODE_END - LOAD - 5, 2):
        op = u16(img, o)
        imm = u32(img, o + 2)
        if imm not in (0x13f, 0x140, 0x141, 0x142):
            continue
        is_cmpi = (op & 0xfff8) == 0x0c80                   # cmpi.l #imm,Dn
        is_cmpa = (op & 0xf1ff) == 0xb1fc                   # cmpa.l #imm,An
        is_cmp = (op & 0xf1ff) == 0xb0bc                    # cmp.l #imm,Dn
        if (is_cmpi or is_cmpa or is_cmp) and LOAD + o not in ins:
            out['count_raw_unknown'].append({'addr': LOAD + o, 'bytes': img[o:o + 6].hex()})
    return out


def plan(rows, img=None, ins=None):
    """-> {'desc': [...], 'bounds': [...], 'cache': [...], 'accessor': {...}, 'sinit': {...},
           'left': [...]}: every site core-dn2 changes for `rows` rows, each with its stock bytes."""
    img = img or load_image()
    ins = ins or instructions()
    s = scan(img, ins)
    if s['desc_data'] or s['count_raw_unknown']:
        raise SystemExit('unexpected references outside the dump: %r %r' % (s['desc_data'][:5],
                                                                          s['count_raw_unknown'][:5]))
    if not (0x141 < rows <= 0x1e0):
        raise SystemExit('rows 0x%x: 0x142..0x1e0 (the cache\'s lea d16 displacement limits it)' % rows)
    p = {'rows': rows, 'desc': [], 'bounds': [], 'cache': [], 'left': []}
    # 1. descriptor operands: keep2 (opcode word kept, operand -> core_desc + offset)
    for d in s['desc']:
        if d['off'] != 2 or d['len'] != 6:
            raise SystemExit('descriptor operand not at +2 of a 6-byte instruction: %r' % d)
        a = d['addr']
        p['desc'].append({'addr': a, 'stock': img[a - LOAD:a - LOAD + 6].hex(), 'addend': d['value'] - DESC,
                          'func': d['func'], 'text': d['text']})
    # 2. bounds
    for c in s['count']:
        a = c['addr']
        if not re.search(r'#0x%x\b' % c['imm'], c['text']):
            continue                                   # a displacement or branch offset, not an immediate
        if a in NOT_BOUNDS or c.get('word') or c['imm'] in (0x13f, 0x142):
            p['left'].append({'addr': a, 'text': c['text'], 'func': c['func'],
                              'why': NOT_BOUNDS.get(a, 'not a compare of a row id with the count')})
            continue
        if not re.match(r'(cmpi|cmpa)\.l #0x14[01],', c['text']):
            p['left'].append({'addr': a, 'text': c['text'], 'func': c['func'], 'why': 'not a compare'})
            continue
        if a == ACCESSOR + 4:
            continue                                   # inside the accessor's head: core_disp bounds it
        new = c['imm'] - 0x141 + rows
        stock = img[a - LOAD:a - LOAD + 6]
        p['bounds'].append({'addr': a, 'stock': stock.hex(), 'new': (stock[:2] + struct.pack('>I', new)).hex(),
                            'imm': c['imm'], 'func': c['func'], 'text': c['text'], 'next': c['next']})
    # 3. the cache
    k = cache_consts(rows)
    k0 = cache_consts(0x141)
    listed = set()
    for a, stock, name, form, why in CACHE:
        b = img[a - LOAD:a - LOAD + len(stock) // 2]
        if b.hex() != stock:
            raise SystemExit('cache site 0x%08x: %s, expected %s' % (a, b.hex(), stock))
        nb = bytearray(b)
        if form == 'l2':
            assert u32(b, 2) == k0[name], (hex(a), name)
            struct.pack_into('>I', nb, 2, k[name])
        elif form == 'w2':
            assert u16(b, 2) == k0[name], (hex(a), name)
            if k[name] >= 0x8000:
                raise SystemExit('0x%08x: %s = 0x%x does not fit a d16/word' % (a, name, k[name]))
            struct.pack_into('>H', nb, 2, k[name])
        elif form == 'b3':
            assert (k0['VEC'] & 0xff) + 4 == b[3], hex(a)
            if (k['VEC'] & ~0xff) != (k['VEC4'] & ~0xff):
                raise SystemExit('0x%08x: vec and vec+4 differ above the low byte' % a)
            nb[3] = k['VEC4'] & 0xff
        listed.add(a)
        p['cache'].append({'addr': a, 'stock': stock, 'new': nb.hex(), 'const': name, 'why': why})
    if k['B'] >= 0x10000:
        raise SystemExit('set: move.w #D,d5w needs the bank stride below 0x10000')
    # every cache-constant operand in the cache functions must be listed
    funcs = {f for f in {i[2] for i in ins.values()} if int(f[:8], 16) in CACHE_FUNCS}
    vals = set(k0.values()) | {0xfff0}
    for a, (bs, text, func) in ins.items():
        if func not in funcs:
            continue
        # the operands as the listing prints them (a link.w's frame size -0x1c is the word 0xffe4
        # in the bytes, so the bytes alone would give false hits)
        nums = {int(x, 16) for x in re.findall(r'(?<![-\w])0x([0-9a-f]+)', text)}
        hit = bool(nums & vals) or (bs[:2] == b'\x10\x3c' and bs[3] == (k0['VEC4'] & 0xff))
        if hit and a not in listed:
            raise SystemExit('cache function %s: 0x%08x %s holds a cache constant not in the plan'
                             % (func, a, text))
    p['accessor'] = {'addr': ACCESSOR, 'stock': ACCESSOR_HEAD}
    p['sinit'] = {'addr': SINIT_CALL, 'stock': SINIT_CALL_STOCK}
    for d in (p['accessor'], p['sinit']):
        b = img[d['addr'] - LOAD:d['addr'] - LOAD + len(d['stock']) // 2]
        if b.hex() != d['stock']:
            raise SystemExit('0x%08x: %s, expected %s' % (d['addr'], b.hex(), d['stock']))
    p['scan'] = s
    return p


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--rows', default='0x180')
    ap.add_argument('--out', default=os.path.join(ROOT, 'out', 'T6.5', 'inventory'))
    a = ap.parse_args()
    rows = int(a.rows, 0)
    img, ins = load_image(), instructions()
    p = plan(rows, img, ins)
    s = p.pop('scan')
    os.makedirs(a.out, exist_ok=True)
    L = []
    L.append('T6.5 inventory, rows 0x%x (%d; stock 0x141 = 321)' % (rows, rows))
    L.append('')
    L.append('descriptor operands (keep2 -> core_desc + addend): %d in %d functions'
             % (len(p['desc']), len({d['func'] for d in p['desc']})))
    for d in p['desc']:
        L.append('  0x%08x +0x%02x %-40s %s' % (d['addr'], d['addend'], d['func'], d['text']))
    L.append('one past the end (0x401fcee0, the next table; left): %s'
             % ', '.join('0x%08x' % e['addr'] for e in s['desc_end']))
    L.append('descriptor addresses in data outside instructions: %d' % len(s['desc_data']))
    L.append('')
    L.append('row-count bounds changed: %d (+ the accessor head 0x%08x, replaced by core_disp)'
             % (len(p['bounds']), ACCESSOR))
    for b in p['bounds']:
        L.append('  0x%08x #0x%x -> #0x%x  %-40s %-26s then %s' % (b['addr'], b['imm'], b['imm'] - 0x141 + rows,
                                                               b['func'], b['text'], b['next']))
    L.append('compares and immediates with the count\'s neighbours, left as stock: %d' % len(p['left']))
    for b in p['left']:
        L.append('  0x%08x %-40s %-26s %s' % (b['addr'], b['func'], b['text'], b['why']))
    L.append('raw compare forms outside the dump: %d' % len(s['count_raw_unknown']))
    L.append('')
    L.append('ParameterUpdateCache sites: %d' % len(p['cache']))
    k = cache_consts(rows)
    L.append('  ' + ', '.join('%s 0x%x' % (n, v) for n, v in k.items()))
    for c in p['cache']:
        L.append('  0x%08x %-14s -> %-14s %-6s %s' % (c['addr'], c['stock'], c['new'], c['const'], c['why']))
    L.append('')
    L.append('display table references in code: %d (all in the static initializer but the accessor)'
             % len(s['disp_code']))
    others = [d for d in s['disp_code'] if not d['func'].startswith('400c2c4e')]
    for d in others:
        L.append('  0x%08x %s %s' % (d['addr'], d['func'], d['text']))
    L.append('display table addresses in data: %d' % len(s['disp_data']))
    for d in s['disp_data']:
        L.append('  0x%08x -> 0x%08x' % (d['addr'], d['value']))
    L.append('')
    mult = [m for m in s['multiples'] if not any(m['addr'] == c['addr'] for c in p['cache'])]
    m141 = {(m['addr'], m['value']): m for m in mult if m['base'] == 0x141}
    m140 = {(m['addr'], m['value']): m for m in mult if m['base'] == 0x140 and (m['addr'], m['value']) not in m141}
    L.append('other immediates equal to n x 0x141 (n 2..256), outside the cache: %d' % len(m141))
    for m in sorted(m141.values(), key=lambda m: m['addr']):
        L.append('  0x%08x 0x%x = %d x 0x141  %-40s %s' % (m['addr'], m['value'], m['n'], m['func'], m['text']))
    vals = {}
    for m in m140.values():
        vals.setdefault(m['value'], []).append(m['addr'])
    L.append('immediates equal to n x 0x140 (n 2..256): %d sites, %d values (8.8 fixed-point numbers, byte '
             'masks and record strides; the list is in multiples-0x140.txt):' % (len(m140), len(vals)))
    L.append('  ' + ' '.join('0x%x(%d)' % (v, len(a_)) for v, a_ in sorted(vals.items())))
    with open(os.path.join(a.out, 'multiples-0x140.txt'), 'w') as f:
        for m in sorted(m140.values(), key=lambda m: m['addr']):
            f.write('0x%08x 0x%x = %d x 0x140  %-40s %s\n' % (m['addr'], m['value'], m['n'], m['func'], m['text']))
    txt = '\n'.join(L) + '\n'
    open(os.path.join(a.out, 'inventory.txt'), 'w').write(txt)
    json.dump(p, open(os.path.join(a.out, 'plan.json'), 'w'), indent=1)
    # compare with T6.1's lists
    t61_desc = set(int(x, 16) for x in '''400174de 400365e0 400366c2 400372e6 4003753a 400375dc
        40037690 40037746 40038c0a 40038cac 40038d58 40038e28 400397be 40039ac8 40039cea 40039ee8 4004483c
        4004b0ca 40062bf0 40065ac0 400672f2 400674e6 400679da 40068254 40068330 4006e892 4006eba8 4006f6ae
        400bd57c 400c25b6 400c27ce 400d5a92 400dbe34 400dbe58 400dbe8a 400dbefa 400dbf68 400dbfce 400dbffa
        400dc026 400dc066 400dc0be 400dc0f2 400dc12c 400dc174 400dc292 400dc29e 400dc38e 400dc3e8 400dc412
        400dc436 400dc45a 400dc47e 400dc6a2 40105e40 40107862'''.split())
    t61_bounds = set(int(x, 16) for x in '''400174d6 400365d0 400366aa 40036af4 40036bbc 400372de 40037540
        400375e2 40037696 40037736 400379ec 40038c10 40038cb2 40038d5e 40038e2e 4003950a 400397a6 40044828
        4004b17a 40062be8 40065ab8 400672de 400674d4 400676cc 400679c8 40068290 4006831e 4006e88a 4006eb9e
        4006f6a6 400bd582 400c2580 400c25a4 400c27c6 400d5a8a 400dbe2c 400dbe50 400dbe76 400dbee6 400dbf54
        400dbfc6 400dbff2 400dc01e 400dc052 400dc0b6 400dc0ea 400dc124 400dc15a 400dc282 400dc380 400dc3e0
        400dc40a 400dc42e 400dc452 400dc476 400dc954 40105e38 4010784e'''.split())
    mine_d = {d['addr'] for d in p['desc']}
    mine_b = {b['addr'] for b in p['bounds']} | {ACCESSOR + 4}
    print(txt.split('\n')[0])
    print('descriptor operands %d (T6.1 56): same set as T6.1: %s' % (len(mine_d), mine_d == t61_desc))
    print('bounds %d + accessor head (T6.1 58): same set as T6.1: %s' % (len(mine_b) - 1, mine_b == t61_bounds))
    print('cache sites %d; left as stock %d' % (len(p['cache']), len(p['left'])))
    print('wrote %s' % os.path.relpath(os.path.join(a.out, 'inventory.txt'), ROOT))


if __name__ == '__main__':
    main()
