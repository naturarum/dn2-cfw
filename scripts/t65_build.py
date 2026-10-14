#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""T6.5: build GT00, GT01 and GT1T, the grown parameter table on top of FTN1.

    venv/bin/python scripts/t65_build.py [GT00 GT01 GT1T] [--out DIR (default out/T6.5)]

  GT00 = FTN1 (FTD1's DSP + core-dn2 0.1 + fx-rename-dn2 0.1) + param-grow-dn2 0.1:
         room for 0x180 rows, no new row
  GT01 = GT00 + param-rows-dn2 0.1: 17 inert rows (0x141..0x151), on no page
  GT1T = GT01 + param-test-dn2 0.1: the TEST row 0x17f on the flanger page's knob E.
         FOR THE EMULATOR ONLY: it shares k 48 with a stock row; never flash it.

Steps (nothing here talks to hardware), as scripts/t50b_build.py does for FTN1:
  1. scripts/t65_make_mod.py writes param-grow-dn2's mod.json from the stock image;
  2. elekloader's SDK builds every mod (ELEKLOADER_CROSS=m68k-elf-; core-dn2 0.1 and param-rows-dn2
     0.1 from the pinned elekloader commit, scripts/elk_pin.py), lint passes for the set,
     the patcher links them into stock 1.12's main OS with the build's version name;
  3. elektron-firmware-tool puts FTD1's DSP (out/T3.8/FTD1/section7.bin) in section 7;
  4. checks (all must hold, or nothing is written as final):
     - the container: ELE3 header as stock but the version; sections 2, 4, 5, 8 stored as
       stock; section 3 as elekloader packed it; section 7 as in FTD1.syx, depacking to
       section7.bin; the main OS depacks to the patcher's image;
     - core and fx-rename are FTN1's (same .elemod sha256);
     - against FTN1's main OS: below the appended blob, every changed byte is inside one of
       param-grow's sites, and each site holds what the plan says; the stock descriptor
       table in the image (with fx-rename's names) is FTN1's byte for byte; the five
       look-alike compares and the next table's pea are untouched;
     - GT01 and GT1T differ from GT00 only in the appended blob (rows are data);
     - the grown table the boot code will build (pg_boot simulated here from the image) has
       FTN1's 0x141 rows, row-0 copies after them, and each added row follows the rules of
       notes/T6.1 3.4 (checked below), its display template valid and its page slot empty;
  5. writes out/T6.5/NAME.syx, NAME.syx.sha256, NAME.main.bin (the --intended-main image),
     NAME.rows.bin (the 0x180 x 0x3c table pg_boot must build), build-NAME.json.
"""
import hashlib
import json
import os
import re
import struct
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scripts'))
import t65_sites  # noqa: E402

EL = os.path.join(ROOT, 'vendor', 'elekloader')
ELPY = os.path.join(EL, '.venv', 'bin', 'python')
TOOL = os.path.join(ROOT, 'vendor', 'elektron-firmware-tool', 'elektron-firmware-tool')
STOCK = os.path.join(ROOT, 'elektron-firmware', 'Digitone_II_OS1_12.syx')
STOCK_SHA = '7e5b8a733d4ab1fdf7e5d22757f7d8eddaa3cbf71d0858874a791844c02ceb35'
STOCK_MAIN = os.path.join(ROOT, 'ele3_out', '02_type3.unpacked.bin')
FTD1 = os.path.join(ROOT, 'out', 'T3.8', 'FTD1', 'FTD1.syx')
FTD1_SHA = '14123a67bea5664f4e7922443b78a4c4cf7007264e2ec88429c9c9f355058f88'
SEC7 = os.path.join(ROOT, 'out', 'T3.8', 'FTD1', 'section7.bin')
SEC7_SHA = '9170d99eaf0c95cb35f59275dbef6db9b1bbb4e5c2a5ddcfdc830abdb1d6457d'
FTN1_MAIN = os.path.join(ROOT, 'out', 'T5.0b', 'FTN1.main.bin')
FTN1_MAIN_SHA = '0bce486073798def4db719392dddb73f6c1971ff8ecbd63f27e0ee1f3fa0c35e'
FTN1_MAP = os.path.join(ROOT, 'out', 'T5.0b', 'build', 'FTN1-main.syx.map.json')
FTN1_MODS = {'core': '63a2ea9074393fda8c97fbddb4fddb07adff7a58621133d2670364e1d7f378c9',
             'fx-rename': 'cf940b0239f2880d87ab35087d817d0dfce64cdd23b8eb2802aa95b30e07bf57'}
OUT = os.path.join(ROOT, 'out', 'T6.5')
MODS = os.path.join(OUT, 'mods')
BUILD = os.path.join(OUT, 'build')
LOAD = 0x40000400
STOCK_END = 0x4030c980                 # the stock main OS ends here; the blob follows
DESC, ROW, ROWS0, ROWS = 0x401f83a4, 0x3c, 0x141, 0x180
DISP_ROWS_OK = range(0, ROWS0)

MODDIRS = {'core': 'mods/core-dn2', 'fx-rename': 'examples/fx-rename-dn2',
           'param-grow': 'examples/param-grow-dn2', 'param-rows': 'examples/param-rows-dn2',
           'param-test': 'examples/param-test-dn2'}
BUILDS = {
    'GT00': ['core', 'fx-rename', 'param-grow'],
    'GT01': ['core', 'fx-rename', 'param-grow', 'param-rows'],
    'GT1T': ['core', 'fx-rename', 'param-grow', 'param-rows', 'param-test'],
}
WHAT = {
    'GT00': 'FTN1 (FTD1 DSP + core-dn2 0.1 + fx-rename-dn2 0.1) + param-grow-dn2 0.1: the parameter '
            'table relocated with room for 0x180 rows, no new row',
    'GT01': 'GT00 + param-rows-dn2 0.1: 17 inert rows 0x141..0x151 (FX k 70..86), on no page',
    'GT1T': 'GT01 + param-test-dn2 0.1: TEST (row 0x17f, k 48, CC 3, NRPN 307) and TST2 (row 0x17e, '
            'k 87) on the flanger page knobs E and F. EMULATOR ONLY: never flash',
}

# FX set: groups 0x10..0x15 (chorus, reverb, delay, master, compressor, ext. inputs)
FX_GROUPS = range(0x10, 0x16)
log = []


def sha(b):
    return hashlib.sha256(b).hexdigest()


def run(cmd, cwd=None, env=None):
    log.append('$ ' + ' '.join(os.path.relpath(c, ROOT) if c.startswith(ROOT) else c for c in cmd))
    r = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True)
    out = (r.stdout + r.stderr).strip()
    if out:
        log.append(out)
    if r.returncode:
        print('\n'.join(log[-20:]))
        raise SystemExit('failed (%d): %s' % (r.returncode, ' '.join(cmd)))
    return out


def sections(syx):
    with tempfile.TemporaryDirectory() as t:
        c = os.path.join(t, 'c.bin')
        rep = run([TOOL, '-i', syx, '-v', '--emit-container', c])
        data = open(c, 'rb').read()
    out = {}
    for m in re.finditer(r'id=(\d+)\s+[^\n]*?off=0x([0-9a-f]+)\s+clen=0x([0-9a-f]+)', rep):
        i, off, n = int(m.group(1)), int(m.group(2), 16), int(m.group(3), 16)
        out[i] = data[off:off + n]
    oks = [l for l in rep.splitlines() if re.search(r'per-packet checksum|content checksum|stream-sum|digest +:', l)]
    if len(oks) < 7 or not all(l.rstrip().endswith('[ok]') for l in oks):
        raise SystemExit('%s: the tool does not report every checksum ok' % syx)
    return out, rep


def header(syx):
    with tempfile.TemporaryDirectory() as t:
        run([TOOL, '-i', syx, '--emit-container', os.path.join(t, 'c.bin')])
        return open(os.path.join(t, 'c.bin'), 'rb').read()[:0x20]


def depack7(syx):
    with tempfile.TemporaryDirectory() as t:
        run([TOOL, '-i', syx, '-d', '7', '-o', t])
        return open(os.path.join(t, 'section_7_blob.bin'), 'rb').read()


def main_image(syx):
    code = ('import sys; sys.path.insert(0, %r)\n'
            'from elekloader import devices, formats\n'
            'dev = [d for d in devices.devices() if d.key == "digitone-mk2"][0]\n'
            'p = formats.parse(open(%r, "rb").read(), dev)\n'
            'sys.stdout.buffer.write(bytes(formats.main_image(p, dev)))\n') % (EL, syx)
    r = subprocess.run([ELPY, '-c', code], capture_output=True)
    if r.returncode:
        raise SystemExit(r.stderr.decode())
    return r.stdout


def diff_ranges(a, b):
    out, i, n = [], 0, min(len(a), len(b))
    while i < n:
        if a[i] != b[i]:
            j = i
            while j < n and a[j] != b[j]:
                j += 1
            out.append((LOAD + i, j - i))
            i = j
        else:
            i += 1
    if len(b) > len(a):
        out.append((LOAD + len(a), len(b) - len(a)))
    return out


def rd32(img, a):
    return struct.unpack('>I', img[a - LOAD:a - LOAD + 4])[0]


def cstr(img, a):
    o = a - LOAD
    if not 0 <= o < len(img):
        return None
    return img[o:o + 80].split(b'\0')[0].decode('latin-1')


class Linked:
    """the main OS image the patcher built, read as the boot code will see it"""

    def __init__(self, img, mp):
        self.img, self.mp = img, mp
        self.run_start = int(mp['__run_start'], 16)
        self.run_load = int(mp['__run_load'], 16)
        self.run_end = self.run_start + 4 * int(mp['__run_words'], 16)

    def ram(self, a, n):
        """bytes at a run-time address: the image, or the run image where the core copies it"""
        if self.run_start <= a and a + n <= self.run_end:
            o = a - self.run_start + self.run_load - LOAD
            return self.img[o:o + n]
        if LOAD <= a and a + n <= LOAD + len(self.img):
            return self.img[a - LOAD:a - LOAD + n]
        raise KeyError('0x%08x' % a)

    def u32(self, a):
        return struct.unpack('>I', self.ram(a, 4))[0]

    def s32(self, a):
        return struct.unpack('>i', self.ram(a, 4))[0]

    def string(self, a):
        for n in (80, 40, 20, 8, 4, 1):
            try:
                b = self.ram(a, n)
            except KeyError:
                continue
            if b'\0' in b:
                return b.split(b'\0')[0].decode('latin-1')
        return None


def simulate_boot(lk, stock_img):
    """-> (the table pg_boot builds, [added rows as dicts]): param_rows read from the image"""
    table = bytearray(lk.img[DESC - LOAD:DESC - LOAD + ROWS0 * ROW])
    row0 = lk.img[DESC - LOAD:DESC - LOAD + ROW]
    table += row0 * (ROWS - ROWS0)
    rows = []
    t = int(lk.mp['param_rows'], 16)
    while True:
        arr = lk.u32(t)
        t += 4
        if not arr:
            break
        while True:
            rid = lk.s32(arr)
            if not rid:
                break
            d = lk.ram(arr + 4, ROW)
            look, fset, opts, page, slot = struct.unpack('>iIIii', lk.ram(arr + 0x40, 20))
            if ROWS0 <= rid < ROWS:
                table[rid * ROW:(rid + 1) * ROW] = d
            rows.append({'id': rid, 'desc': d, 'look': look, 'flags_set': fset, 'opts': opts,
                         'page': page, 'slot': slot, 'at': '0x%08x' % arr})
            arr += 0x54
    return bytes(table), rows


def row_fields(d):
    grp, k, mn, mx, df, bip = struct.unpack('>iiiiii', d[:0x18])
    cc, cc2 = struct.unpack('>hh', d[0x18:0x1c])
    nrpn, uid, mask, name, gname, short, fmt, suf = struct.unpack('>iiIIIIII', d[0x1c:0x3c])
    return dict(group=grp, k=k, min=mn, max=mx, default=df, bipolar=bip, cc=cc, cc2=cc2, nrpn=nrpn,
                uid=uid, modmask=mask, name=name, group_name=gname, short=short, fmt=fmt, suffix=suf)


def check_rows(lk, table, rows, stock_img, ftn1_table, allow_shared_k):
    """the rules of notes/T6.1 3.4 for each added row -> (ok, [lines])"""
    bad, notes = [], []
    stock = [row_fields(ftn1_table[r * ROW:(r + 1) * ROW]) for r in range(ROWS0)]
    # stock FX/sound sets: which k and CC each set's rows use
    fx_k = {s['k']: r for r, s in enumerate(stock) if s['group'] in FX_GROUPS}
    fx_cc = {s['cc'] for s in stock if s['group'] in FX_GROUPS and s['cc'] >= 0}
    fx_nrpn = {s['nrpn'] for s in stock if s['group'] in FX_GROUPS and s['nrpn'] >= 0}
    uids = {s['uid'] for s in stock}
    seen_ids, seen_k = set(), {}
    parser_ccs = {1, 2, 6, 33, 34, 38, 64, 66, 94, 98, 99, 110, 120, 121, 123}
    for r in rows:
        f = row_fields(r['desc'])
        tag = 'row 0x%x %s/%s' % (r['id'], lk.string(f['name']), lk.string(f['short']))
        if not ROWS0 <= r['id'] < ROWS:
            bad.append('%s: id outside 0x141..0x17f' % tag)
        if r['id'] in seen_ids:
            bad.append('%s: id given twice' % tag)
        seen_ids.add(r['id'])
        if f['group'] not in FX_GROUPS:
            bad.append('%s: group 0x%x is not a send-FX/master group (only FX rows are checked here)'
                       % (tag, f['group']))
        if not 0 <= f['k'] <= 100:
            bad.append('%s: k %d outside the FX set 0..100' % (tag, f['k']))
        if f['k'] in seen_k:
            bad.append('%s: k %d also used by new row 0x%x' % (tag, f['k'], seen_k[f['k']]))
        seen_k[f['k']] = r['id']
        if f['k'] in fx_k:
            if f['k'] in allow_shared_k:
                notes.append('%s: shares k %d with stock row 0x%x (allowed for this test build)'
                             % (tag, f['k'], fx_k[f['k']]))
                s = stock[fx_k[f['k']]]
                if f['default'] != s['default']:
                    bad.append('%s: default %d, the stock row\'s is %d (a new kit would change)'
                               % (tag, f['default'] >> 8, s['default'] >> 8))
            else:
                bad.append('%s: k %d is stock row 0x%x\'s' % (tag, f['k'], fx_k[f['k']]))
        elif f['default'] != 0:
            bad.append('%s: default %d: a k no stock row has must default to 0' % (tag, f['default'] >> 8))
        if f['k'] in (65, 100):
            bad.append('%s: k 65/100 do not round-trip' % tag)
        if not f['min'] <= f['default'] <= f['max']:
            bad.append('%s: default outside min..max' % tag)
        if f['cc2'] != -1:
            bad.append('%s: CC2 must be -1' % tag)
        if f['cc'] != -1:
            if r['id'] != 0x17f:
                bad.append('%s: a MIDI CC on an inert row' % tag)
            if f['cc'] in fx_cc or f['cc'] in parser_ccs or 32 <= f['cc'] <= 63 or not 0 <= f['cc'] < 128:
                bad.append('%s: CC %d is taken (FX map, the parser, or 32..63)' % (tag, f['cc']))
        if f['nrpn'] != -1:
            if r['id'] != 0x17f:
                bad.append('%s: an NRPN on an inert row' % tag)
            if f['nrpn'] in fx_nrpn or not 0 <= f['nrpn'] <= 398:
                bad.append('%s: NRPN %d is taken or above 398' % (tag, f['nrpn']))
        if f['uid'] in uids or f['uid'] < 0x400:
            bad.append('%s: Overbridge id 0x%x not outside the stock ids' % (tag, f['uid']))
        uids.add(f['uid'])
        if f['modmask']:
            bad.append('%s: modulation flags 0x%x on an FX row' % (tag, f['modmask']))
        for fld in ('name', 'short', 'group_name', 'suffix'):
            if lk.string(f[fld]) is None:
                bad.append('%s: %s 0x%08x is not a string' % (tag, fld, f[fld]))
        if f['fmt'] not in (0x400e3030,):
            bad.append('%s: formatter 0x%08x is not one checked here' % (tag, f['fmt']))
        if r['look'] not in DISP_ROWS_OK:
            bad.append('%s: display template %d is not a stock row' % (tag, r['look']))
        else:
            tf = stock[r['look']]
            if tf['group'] not in FX_GROUPS:
                bad.append('%s: display template 0x%x is not an FX row (rule 1)' % (tag, r['look']))
        if not r['flags_set'] & 8:
            bad.append('%s: flags_set 0x%x lacks bit 3: could be p-lockable' % (tag, r['flags_set']))
        if r['opts'] & 1:
            bad.append('%s: keeps a randomiser' % tag)
        if r['page'] != -1:
            if r['id'] not in (0x17e, 0x17f):
                bad.append('%s: on a page' % tag)
            if not (0 <= r['page'] < 37 and 0 <= r['slot'] < 8):
                bad.append('%s: page %d slot %d out of range' % (tag, r['page'], r['slot']))
        notes.append('%s: group 0x%x k %d %d..%d default %d CC %d NRPN %d uid 0x%x look 0x%x flags|=0x%x page %d/%d'
                     % (tag, f['group'], f['k'], f['min'] >> 8, f['max'] >> 8, f['default'] >> 8, f['cc'],
                        f['nrpn'], f['uid'], r['look'], r['flags_set'], r['page'], r['slot']))
    return not bad, bad, notes


def build(name, built):
    mods = BUILDS[name]
    env = dict(os.environ, ELEKLOADER_CROSS='m68k-elf-')
    files = [built[m] for m in mods]
    run([ELPY, '-m', 'elekloader.lint'] + files + ['--stock', STOCK], cwd=EL, env=env)
    mainsyx = os.path.join(BUILD, name + '-main.syx')
    cmd = [ELPY, '-m', 'elekloader.patch', '--stock', STOCK]
    for f in files:
        cmd += ['--mod', f]
    run(cmd + ['--version', name, '--out', mainsyx], cwd=EL, env=env)
    man = json.load(open(mainsyx + '.json'))
    mp = json.load(open(mainsyx + '.map.json'))
    syx = os.path.join(OUT, name + '.syx')
    run([TOOL, '-i', mainsyx, '-c', '7', SEC7, '-o', syx])

    st, _ = sections(STOCK)
    ftd, _ = sections(FTD1)
    mn, _ = sections(mainsyx)
    fn, rep = sections(syx)
    hdr, shdr = header(syx), header(STOCK)
    checks = {}
    checks['ELE3 header: version field %s, the rest as stock' % name] = (
        hdr[0x14:0x18] == name.encode() and hdr[:0x14] == shdr[:0x14] and hdr[0x18:] == shdr[0x18:])
    checks['section ids as stock'] = sorted(fn) == sorted(st)
    for i in (2, 4, 5, 8):
        checks['section %d stored as stock' % i] = fn[i] == st[i]
    checks['section 3 stored as elekloader packed it'] = fn[3] == mn[3]
    checks['section 7 stored as in FTD1.syx'] = fn[7] == ftd[7]
    checks['section 7 depacks to FTD1 section7.bin'] = depack7(syx) == open(SEC7, 'rb').read()
    img = main_image(syx)
    checks['main OS depacks to the patcher\'s image'] = sha(img) == man['main_sha256']
    for m in man['mods']:
        if m['id'] in FTN1_MODS:
            checks['%s .elemod is FTN1\'s (sha256 %s...)' % (m['id'], FTN1_MODS[m['id']][:8])] = \
                m['sha256'] == FTN1_MODS[m['id']]

    stock_img = open(STOCK_MAIN, 'rb').read()
    ftn1 = open(FTN1_MAIN, 'rb').read()
    # against FTN1, below the blob: only param-grow's sites, and fx-rename's pointers, which
    # move with the link layout (mods are laid out by id) but must still name the same strings
    grow = [m for m in man['mods'] if m['id'] == 'param-grow'][0]
    plan = t65_sites.plan(ROWS, stock_img)
    grow_sites = json.load(open(os.path.join(EL, MODDIRS['param-grow'], 'mod.json')))['sites']
    ren_sites = json.load(open(os.path.join(EL, MODDIRS['fx-rename'], 'mod.json')))['sites']
    lk = Linked(img, mp)
    lk_ftn1 = Linked(ftn1, json.load(open(FTN1_MAP)))
    spans = [(int(s['addr'], 16), len(s['stock']) // 2) for s in grow_sites]
    inside = lambda a: any(lo <= a < lo + n for lo, n in spans)        # noqa: E731
    below = [(a, n) for a, n in diff_ranges(ftn1[:STOCK_END - LOAD], img[:STOCK_END - LOAD])]
    renamed, stray = [], []
    for a, n in below:
        if all(inside(x) for x in range(a, a + n)):
            continue
        hit = None
        for s_ in ren_sites:
            sa = int(s_['addr'], 16)
            opnd = sa + (2 if s_['op'] == 'keep2' else 0)
            if opnd <= a and a + n <= opnd + 4:
                hit = opnd
        if hit is not None:
            mine, theirs = lk.string(lk.u32(hit)), lk_ftn1.string(lk_ftn1.u32(hit))
            if mine is not None and mine == theirs:
                renamed.append('0x%08x "%s"' % (hit, mine))
                continue
        stray.append((a, n))
    checks['below the blob, every byte that differs from FTN1 is in a param-grow site, or an '
           'fx-rename pointer to the same string (%d)' % len(renamed)] = not stray
    # each site's new bytes
    mism = []
    for s in grow_sites:
        a, n = int(s['addr'], 16), len(s['stock']) // 2
        new = img[a - LOAD:a - LOAD + n]
        if ftn1[a - LOAD:a - LOAD + n].hex() != s['stock']:
            mism.append('0x%08x: FTN1 has %s there' % (a, ftn1[a - LOAD:a - LOAD + n].hex()))
        if s['op'] == 'bytes' and new.hex() != s['new']:
            mism.append('0x%08x: %s, expected %s' % (a, new.hex(), s['new']))
        if s['op'] == 'keep2':
            got = struct.unpack('>I', new[2:6])[0]
            want = int(mp['pg_desc'], 16) + s.get('addend', 0)
            if new[:2] != bytes.fromhex(s['stock'][:4]) or got != want:
                mism.append('0x%08x: keep2 -> 0x%08x, expected 0x%08x' % (a, got, want))
        if s['op'] in ('jsr', 'jmp'):
            op = {'jsr': '4eb9', 'jmp': '4ef9'}[s['op']]
            got = struct.unpack('>I', new[2:6])[0]
            if new[:2].hex() != op or got != int(mp[s['target']], 16) or new[6:] != b'\x4e\x71' * ((n - 6) // 2):
                mism.append('0x%08x: %s, expected %s %s' % (a, new.hex(), s['op'], s['target']))
    checks['every param-grow site holds the planned bytes (%d sites)' % len(grow_sites)] = not mism
    lo, hi = DESC - LOAD, DESC - LOAD + ROWS0 * ROW

    def same_table(a_img, a_lk, b_img, b_lk):
        """FTN1's rows, field by field; a pointer field may differ if it names the same string"""
        for o in range(0, ROWS0 * ROW, 4):
            x, y = a_img[lo + o:lo + o + 4], b_img[lo + o:lo + o + 4]
            if x == y:
                continue
            if o % ROW not in (0x28, 0x2c, 0x30, 0x38):
                return False
            sx = a_lk.string(struct.unpack('>I', x)[0])
            if sx is None or sx != b_lk.string(struct.unpack('>I', y)[0]):
                return False
        return True
    checks['the descriptor table in the image is FTN1\'s (fx-rename\'s names in it)'] = same_table(
        img, lk, ftn1, lk_ftn1)
    untouched = list(t65_sites.NOT_BOUNDS) + [0x400e067c, 0x400cb258, 0x400cb262]
    checks['the five look-alike compares, the next table\'s pea and the static ids untouched'] = all(
        img[a - LOAD:a - LOAD + 6] == stock_img[a - LOAD:a - LOAD + 6] for a in untouched)
    checks['core\'s ten sites as in FTN1'] = all(
        img[a - LOAD:a - LOAD + 6] == ftn1[a - LOAD:a - LOAD + 6] for a in
        (0x40000538, 0x4002e464, 0x4002e4ca, 0x4002f20c, 0x4002f256, 0x4009ff88, 0x400987a2,
         0x400da0b2, 0x400d9230, 0x4012b490))

    table, rows = simulate_boot(lk, stock_img)
    ftn1_table = ftn1[lo:hi]
    checks['the grown table: the image\'s 0x141 rows first'] = table[:ROWS0 * ROW] == img[lo:hi]
    added = {r['id'] for r in rows}
    checks['the grown table: every other row is a copy of row 0'] = all(
        table[r * ROW:(r + 1) * ROW] == ftn1_table[:ROW] for r in range(ROWS0, ROWS) if r not in added)
    ok, bad, notes = check_rows(lk, table, rows, stock_img, ftn1_table,
                                allow_shared_k={48} if name == 'GT1T' else set())
    checks['added rows follow notes/T6.1 3.4 (%d rows)' % len(rows)] = ok

    ranges_ftn1 = diff_ranges(ftn1, img)
    ranges_stock = diff_ranges(stock_img, img)
    with open(os.path.join(OUT, name + '.main.bin'), 'wb') as f:
        f.write(img)
    with open(os.path.join(OUT, name + '.rows.bin'), 'wb') as f:
        f.write(table)
    sy = open(syx, 'rb').read()
    with open(syx + '.sha256', 'w') as f:
        f.write('%s  %s\n' % (sha(sy), os.path.basename(syx)))
    info = {
        'name': name, 'what': WHAT[name],
        'syx': {'path': os.path.relpath(syx, ROOT), 'bytes': len(sy), 'sha256': sha(sy)},
        'main_bin': {'path': 'out/T6.5/%s.main.bin' % name, 'bytes': len(img), 'sha256': sha(img)},
        'rows_bin': {'path': 'out/T6.5/%s.rows.bin' % name, 'sha256': sha(table)},
        'section7': {'path': os.path.relpath(SEC7, ROOT), 'sha256': SEC7_SHA},
        'intermediate': {'path': os.path.relpath(mainsyx, ROOT), 'sha256': sha(open(mainsyx, 'rb').read())},
        'mods': [{k: m[k] for k in ('id', 'version', 'file', 'sha256', 'sites')} for m in man['mods']],
        'link': {k: man['link']['layout'][k] for k in ('ddr', 'bss', 'blob_len', 'ddr_spare')},
        'symbols': {k: mp[k] for k in ('pg_boot', 'pg_sinit', 'pg_disp_get', 'pg_desc', 'pg_xdisp',
                                       'pg_xdisp_on', 'param_rows', 'param_rows_n', '__run_start',
                                       '__run_load', '__bss_start', '__bss_end')},
        'checks': checks,
        'stray_vs_ftn1': ['0x%08x +%d' % x for x in stray],
        'fx_rename_pointers_moved': renamed,
        'site_mismatches': mism,
        'rows': notes, 'row_problems': bad,
        'diff_vs_ftn1': ['0x%08x +%d' % r for r in ranges_ftn1],
        'n_diff_vs_ftn1': len(ranges_ftn1), 'n_diff_vs_stock': len(ranges_stock),
        'tool_report': rep.splitlines(),
    }
    with open(os.path.join(OUT, 'build-%s.json' % name), 'w') as f:
        json.dump(info, f, indent=1)
    print('== %s: %s' % (name, WHAT[name]))
    for k, v in checks.items():
        print('  %-88s %s' % (k, 'yes' if v else 'NO'))
    for l in bad + mism + ['stray 0x%08x +%d' % x for x in stray]:
        print('  PROBLEM: ' + l)
    print('  main OS: %d ranges differ from FTN1, %d from stock' % (len(ranges_ftn1), len(ranges_stock)))
    print('  %s  %s (%d bytes)' % (sha(sy), os.path.relpath(syx, ROOT), len(sy)))
    print('  %s  out/T6.5/%s.main.bin' % (sha(img), name))
    return info, img


# Built from the pinned elekloader commit that holds their 0.1 sources (scripts/elk_pin.py): the
# working tree moved them on for CMP1 (core-dn2 0.2, param-rows-dn2 0.2), and these builds must
# keep rebuilding byte for byte.
PINNED = ('core', 'param-rows')


def main():
    global OUT, MODS, BUILD
    argv = sys.argv[1:]
    if '--out' in argv:                 # write somewhere else (a reproducibility check)
        i = argv.index('--out')
        OUT = os.path.abspath(argv[i + 1])
        MODS, BUILD = os.path.join(OUT, 'mods'), os.path.join(OUT, 'build')
        del argv[i:i + 2]
    names = argv or ['GT00', 'GT01', 'GT1T']
    for p, want in ((STOCK, STOCK_SHA), (FTD1, FTD1_SHA), (SEC7, SEC7_SHA), (FTN1_MAIN, FTN1_MAIN_SHA)):
        if sha(open(p, 'rb').read()) != want:
            raise SystemExit('%s: sha256 is not the expected one' % p)
    os.makedirs(MODS, exist_ok=True)
    os.makedirs(BUILD, exist_ok=True)
    env = dict(os.environ, ELEKLOADER_CROSS='m68k-elf-')
    run([sys.executable, os.path.join(ROOT, 'scripts', 't65_make_mod.py')])
    import elk_pin
    pin = elk_pin.tree(elk_pin.REV_0_1)
    dirs = dict(MODDIRS, **{m: os.path.join(pin, MODDIRS[m]) for m in PINNED})
    need = sorted({m for n in names for m in BUILDS[n]}, key=list(MODDIRS).index)
    built = {}
    for m in need:
        out = run([ELPY, '-m', 'elekloader.sdk.build', dirs[m], '--stock', STOCK, '--out', MODS], cwd=EL, env=env)
        f = re.search(r'BUILT (\S+\.elemod)', out).group(1)
        built[m] = os.path.normpath(os.path.join(EL, f))
    infos, imgs = {}, {}
    for n in names:
        infos[n], imgs[n] = build(n, built)
    # GT01 and GT1T differ from GT00 only past the stock end (the rows are data)
    for n in ('GT01', 'GT1T'):
        if n in imgs and 'GT00' in imgs:
            same = imgs[n][:STOCK_END - LOAD] == imgs['GT00'][:STOCK_END - LOAD]
            infos[n]['checks']['identical to GT00 below the blob (rows are data only)'] = same
            print('%s identical to GT00 below the blob: %s' % (n, 'yes' if same else 'NO'))
            with open(os.path.join(OUT, 'build-%s.json' % n), 'w') as f:
                json.dump(infos[n], f, indent=1)
    with open(os.path.join(OUT, 'build.json'), 'w') as f:
        json.dump({n: {k: infos[n][k] for k in ('name', 'what', 'syx', 'main_bin', 'mods', 'checks')}
                   for n in infos} | {'log': log}, f, indent=1)
    if not all(all(i['checks'].values()) for i in infos.values()):
        raise SystemExit('a check failed')


if __name__ == '__main__':
    main()
