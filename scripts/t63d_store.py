#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""T6.3 CMP3: comp-link-dn2 0.2's kit storage (vendor/elekloader/examples/comp-link-dn2/store.s, store.c), host-called
on the OS's own kit serializer, loader and FxSetup mirror update, from a warm snapshot. The prototype's method
(out/T6.4/store-frame/scripts/store.py), on the code as built.

    cd vendor/digikit && DT2_SYX=../../out/T6.3/CMP3/C3EM.syx DT2_SECTIONS=../../out/T6.3/CMP3/emu/C3EM/sections \\
        uv run python ../../scripts/t63d_store.py built ../../out/T6.3/CMP3/emu/C3EM/warm800M.snap OUT.json \\
        --map ../../out/T6.3/CMP3/C3MO-main.syx.map.json
    cd vendor/digikit && DT2_SYX=../../out/T6.3/CMP2/C2EM.syx DT2_SECTIONS=../../out/T6.3/CMP2/emu/C2EM/sections \\
        uv run python ../../scripts/t63d_store.py inject ../../out/T6.3/CMP2/emu/C2EM/warm800M.snap OUT.json

built: C3EM's image (CMP3's main OS), the three storage sites as linked; "stock" = the three sites given their stock
bytes back in emulator RAM. inject: CMP2's image (C2EM), comp-link 0.2's storage code (the same sources, the
same compiler and flags as the SDK's) linked alone at 0x47D30000 and the three sites pointed at it, as the
prototype did; "stock" = C2EM as it is.

Stock (S): the stock serializer leaves the FX record's +0x60..+0x97 alone; the stock loader loads a record that holds
our words (the hooked serializer's own output) exactly as one without them and leaves k 70..96 alone; an in-place
stock re-save keeps our words; a stock single edit of k 75 writes the id-0 word.
Hooks (H): the serializer differs from stock only at FX record +0x60..+0x97 (raw live words, 0xC3B1); the loader
restores k 70..96 exactly (a round trip) and the rest of the kit as stock; an old record (zeros), a garbage one (0xa5)
and one without the check word: the seed from the kit's own k 60..64 and 67; per-word range checks; a record whose
version is not 0: the row defaults (FUN_400e7140) for k 70..89; the new-kit defaults (FUN_400e7140 on its own);
slot-17 single edits: a band knob (k 75) rewrites the 27 words and the check word and puts the id-0 word back; a
stock knob (k 60) and an LFO row (k 10, id 0) as stock plus our words; a change naming several k (the vector type):
all from 70 -> id 0 put back, mixed -> left as stock; the whole-setup path (info 0).
Emulator RAM only; nothing is saved. Writes OUT.json.
"""
import json
import os
import struct
import subprocess
import sys
import tempfile

DK = os.environ.get('DIGIKIT', os.getcwd())
sys.path.insert(0, DK)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LINK = os.path.join(ROOT, 'vendor', 'elekloader', 'examples', 'comp-link-dn2')

KITPTR, FXOFF, KITLEN, RECLEN, FXREC = 0x800052a0, 0x48e4, 0x5d71, 0x2a00, 0x16ac
KIT_SAVE, KIT_LOAD, NEW_KIT = 0x400ddfa8, 0x400dddb6, 0x400e7140
FX_SAVE, FX_LOAD = 0x400dcf34, 0x400dcef0
VSLOT04, GETTER = 0x40190d74, 0x40190794
FXSETUP_VT, PARAM_INFO_VT, SLOT17 = 0x401da4ac, 0x401dbb64, 0x401da4f0
TI_MANY, TI_ONE = 0x401da16c, 0x401da160
STOCK_BYTES = {FX_SAVE: '2f0b2f0a246f000c', FX_LOAD: '2f0b2f0a226f000c', SLOT17: '4002ffca'}
CODE = 0x47D30000
SCR = 0x10100000
K0, NV, MAGIC = 70, 27, 0xC3B1
VMAX = [127, 8, 127, 127, 127, 127, 127, 8, 127, 127, 127, 127, 127, 8, 127, 127, 127, 127, 127, 127] + [127] * 7
VDEF = [32, 4, 24, 32, 64, 68, 32, 4, 24, 32, 64, 44, 32, 4, 24, 32, 64, 0, 0, 0] + [0] * 7
T = [(((11 * i + 5) % 9) if i in (1, 7, 13) else ((11 * i + 5) & 0x7f)) << 8 for i in range(NV)]   # in range
M = [0x1234] * NV                                             # a marker the loader must replace

REC = [SCR + 0x3000 * i for i in range(10)]
LKBASE = SCR + 0x20000
LK = [SCR + 0x26000 + 0x6000 * i for i in range(8)]
INFO = SCR + 0x60000
VEC = SCR + 0x60100


def build_inject():
    """comp-link 0.2's storage code linked alone at CODE (the SDK's compiler and flags)"""
    d = tempfile.mkdtemp(prefix='t63d-')
    cf = ['-mcpu=54455', '-O2', '-ffreestanding', '-fno-builtin', '-nostdlib', '-fno-pic', '-fno-pie',
          '-fomit-frame-pointer', '-Wall']
    subprocess.check_call(['m68k-elf-gcc'] + cf + ['-c', os.path.join(LINK, 'store.c'), '-o', os.path.join(d, 'c.o')])
    subprocess.check_call(['m68k-elf-as', '-mcpu=54455', os.path.join(LINK, 'store.s'), '-o', os.path.join(d, 's.o')])
    open(os.path.join(d, 'l.ld'), 'w').write(
        'SECTIONS { . = 0x%x; .all : { *(.run) *(.text) *(.text.*) *(.rodata) *(.rodata.*) *(.data) *(.bss) *(COMMON) } '
        '/DISCARD/ : { *(.comment) *(.note*) } }\n' % CODE)
    elf = os.path.join(d, 'store.elf')
    subprocess.check_call(['m68k-elf-ld', '-T', os.path.join(d, 'l.ld'), '-o', elf, os.path.join(d, 's.o'),
                           os.path.join(d, 'c.o')])
    subprocess.check_call(['m68k-elf-objcopy', '-O', 'binary', elf, os.path.join(d, 'store.bin')])
    syms = {}
    for line in subprocess.check_output(['m68k-elf-nm', elf]).decode().splitlines():
        a, t, n = line.split()
        syms[n] = int(a, 16)
    return open(os.path.join(d, 'store.bin'), 'rb').read(), syms


def setup(snap):
    from emu.longrun import build
    m, ev, st, pc, inq, at = build(snap, softfloat=True, bitmap=True, dsp=True, unblock=True,
                                   deferred_components=('timers',))
    for a in range(SCR, SCR + 0x70000, 0x1000):
        m.ensure(a)
    return m


def tools(m):
    uc = m.uc
    rd = lambda a, n: bytes(uc.mem_read(a, n))                       # noqa: E731
    wr = lambda a, b: uc.mem_write(a, bytes(b))                      # noqa: E731
    r32 = lambda a: struct.unpack('>I', rd(a, 4))[0]                 # noqa: E731
    from unicorn.m68k_const import UC_M68K_REG_A7, UC_M68K_REG_SR, UC_M68K_REG_D0
    m.ensure(0x10000000)
    top = 0x10000000 + 0x1000 - 0x100

    def call(f, args):
        uc.mem_write(top, struct.pack('>I', 0xDEADBEE0) + b''.join(struct.pack('>I', x) for x in args))
        uc.reg_write(UC_M68K_REG_SR, 0x2700)
        uc.reg_write(UC_M68K_REG_A7, top)
        uc.emu_start(f, 0xDEADBEE0, count=50_000_000)
        return uc.reg_read(UC_M68K_REG_D0) & 0xffffffff
    return uc, rd, wr, r32, call


def words(b, off, n):
    return list(struct.unpack('>%dH' % n, b[off:off + 2 * n]))


def put_k(wr, live, vals, k0=K0):
    wr(live + 2 * k0, struct.pack('>%dH' % len(vals), *vals))


def ours(vals):
    return struct.pack('>%dH' % (NV + 1), *(list(vals) + [MAGIC]))


def inr(i, v):
    return v if v <= VMAX[i] << 8 else VDEF[i] << 8


def seed(live_k):
    """the seed comp-link 0.2 must give, from the live k 0..100 after the stock loader"""
    thr, atk, rel, mup, rat, mix = live_k[60], live_k[61], live_k[62], live_k[63], live_k[64] + 0x100, live_k[67]
    out = [0] * NV
    for b in range(3):
        i = 6 * b
        out[i], out[i + 1], out[i + 2], out[i + 3], out[i + 4] = (inr(i, thr), inr(i + 1, rat), inr(i + 2, atk),
                                                                  inr(i + 3, rel), inr(i + 4, mup))
        out[17 + b] = inr(17 + b, mix)
    out[5], out[11] = 0, 127 << 8
    return out


def diff_ranges(a, b, base=0):
    out, i, n = [], 0, min(len(a), len(b))
    while i < n:
        if a[i] != b[i]:
            j = i
            while j < n and a[j] != b[j]:
                j += 1
            out.append(('+%#x' % (base + i), j - i))
            i = j
        i += 1
    return out


def find_fxsetup(m, rd, call, live):
    uc = m.uc
    pat = struct.pack('>I', FXSETUP_VT)
    for b, e, perms in uc.mem_regions():
        data = rd(b, e - b + 1)
        i = data.find(pat)
        while i >= 0:
            h = b + i
            if h % 2 == 0 and h >= 0x40400000:
                try:
                    if call(GETTER, [h]) == live:
                        return h
                except Exception:      # noqa: BLE001
                    pass
            i = data.find(pat, i + 1)
    return None


def vtable_of(img_rd, ti):
    """the vtable whose type info is ti (offset-to-top 0, ti, then the slots), from the main OS image"""
    lo, n = 0x40000400, 0x00400000
    data = img_rd(lo, n)
    pat = b'\x00\x00\x00\x00' + struct.pack('>I', ti)
    i = data.find(pat)
    hits = []
    while i >= 0:
        hits.append(lo + i + 8)
        i = data.find(pat, i + 1)
    return hits


def install(m, mode, cmap):
    uc, rd, wr, r32, call = tools(m)
    for a, sb in STOCK_BYTES.items():
        assert mode != 'inject' or rd(a, len(sb) // 2).hex() == sb, hex(a)
    if mode == 'inject':
        code, syms = build_inject()
        m.ensure(CODE)
        assert rd(CODE, 0x800) == bytes(0x800)
        wr(CODE, code)
    else:
        syms = {k: int(v, 16) for k, v in json.load(open(cmap)).items() if k.startswith('compl_')}
    want = {FX_SAVE: bytes.fromhex('4ef9') + struct.pack('>I', syms['compl_ser']) + bytes.fromhex('4e71'),
            FX_LOAD: bytes.fromhex('4ef9') + struct.pack('>I', syms['compl_load']) + bytes.fromhex('4e71'),
            SLOT17: struct.pack('>I', syms['compl_update'])}
    if mode == 'inject':
        for a, b in want.items():
            wr(a, b)
    else:
        for a, b in want.items():
            assert rd(a, len(b)) == b, ('the image as built', hex(a), rd(a, len(b)).hex())
    return syms


def stock_sites(m):
    uc, rd, wr, r32, call = tools(m)
    for a, sb in STOCK_BYTES.items():
        wr(a, bytes.fromhex(sb))


def main():
    mode, snap, out = sys.argv[1:4]
    cmap = sys.argv[sys.argv.index('--map') + 1] if '--map' in sys.argv else None
    res = {'mode': mode, 'snapshot': snap}

    # ---- H: the hooks
    m = setup(snap)
    uc, rd, wr, r32, call = tools(m)
    syms = install(m, mode, cmap)
    res['symbols'] = {k: '%#010x' % v for k, v in syms.items() if k.startswith('compl_')}
    kit = r32(KITPTR)
    live = kit + FXOFF
    res['kit'] = '%#010x' % kit
    res['live_k70_96_at_snapshot'] = ['%04x' % x for x in words(rd(live, 202), 2 * K0, NV)]
    res['live_k60_67_at_snapshot'] = ['%04x' % x for x in words(rd(live, 202), 120, 8)]
    h = {}
    snap_live = words(rd(live, 202), 0, 101)
    h['snapshot_k70_96_is_the_seed_of_its_k60_67'] = words(rd(live, 202), 2 * K0, NV) == seed(snap_live)
    h['snapshot_k70_89_are_the_row_defaults'] = words(rd(live, 202), 2 * K0, 20) == [v << 8 for v in VDEF[:20]]
    put_k(wr, live, T)
    base_kit = rd(kit, KITLEN)
    wr(LKBASE, base_kit)
    put_k(wr, LKBASE + FXOFF, M)
    # H1 serializer: our words, nothing else differs from the stock serializer (run on a stock-site machine below)
    wr(REC[0], b'\xa5' * RECLEN)
    h['serializer_ret'] = call(KIT_SAVE, [REC[0], kit, 0]) & 0xff
    rec_h = rd(REC[0], RECLEN)
    h['record_fx_60_97'] = rec_h[FXREC + 0x60:FXREC + 0x98].hex()
    h['record_holds_the_raw_live_words_and_0xC3B1'] = rec_h[FXREC + 0x60:FXREC + 0x98] == ours(T)
    # H2/H3 loader round trip: live -> record -> live
    wr(LK[0], rd(LKBASE, KITLEN))
    h['loader_ret'] = call(KIT_LOAD, [LK[0], REC[0]]) & 0xff
    lk_h = rd(LK[0], KITLEN)
    h['round_trip_k70_96_exact'] = words(lk_h, FXOFF + 2 * K0, NV) == T
    # H4 old record (no check word, zeros at +0x60..+0x97: every kit saved before CMP3), H5 garbage, H5b check word
    # missing with in-range words: the seed from the kit's own k 60..64, 67
    cases = {}
    for name, fill in (('old record (zeros)', bytes(0x38)), ('garbage (0xa5)', b'\xa5' * 0x38),
                       ('the words without the check word', ours(T)[:-2] + b'\x00\x00'),
                       ('a later layout (0xC3B2)', ours(T)[:-2] + b'\xc3\xb2')):
        g = bytearray(rec_h)
        g[FXREC + 0x60:FXREC + 0x98] = fill
        wr(REC[1], g)
        wr(LK[1], rd(LKBASE, KITLEN))
        call(KIT_LOAD, [LK[1], REC[1]])
        lk = rd(LK[1], KITLEN)
        lv = words(lk, FXOFF, 101)
        cases[name] = {'k70_96': ['%04x' % x for x in lv[K0:K0 + NV]], 'k60_67': ['%04x' % x for x in lv[60:68]],
                       'is_the_seed': lv[K0:K0 + NV] == seed(lv),
                       'the_rest_of_the_kit_as_the_round_trip_load': all(
                           FXOFF + 2 * K0 <= int(x[0], 16) and int(x[0], 16) + x[1] <= FXOFF + 2 * (K0 + NV)
                           for x in diff_ranges(lk_h, lk))}
    h['seeded_loads'] = cases
    # the seed against several stock values (RAT 7 -> 8; out-of-range stock words -> the row default)
    sv = {}
    for nm, k60_67 in (('stock defaults 32 24 32 64 RAT 3 SCS 0 SCF 80 MIX 0', [32, 24, 32, 64, 3, 0, 80, 0]),
                       ('heavy 10 0 0 100 RAT 7 MIX 127', [10, 0, 0, 100, 7, 0, 64, 127]),
                       ('out of range: THR 200, RAT 9, MIX 128', [200, 24, 32, 64, 9, 0, 64, 128])):
        g = bytearray(rec_h)
        g[FXREC + 0x60:FXREC + 0x98] = bytes(0x38)
        # the stock words of k 60..67 live at ids 37..44 (map 0x401fd160)
        for j, v in enumerate(k60_67):
            struct.pack_into('>H', g, FXREC + 4 + 2 * (37 + j), (v << 8) & 0xffff)
        wr(REC[2], g)
        wr(LK[2], rd(LKBASE, KITLEN))
        call(KIT_LOAD, [LK[2], REC[2]])
        lv = words(rd(LK[2], KITLEN), FXOFF, 101)
        sv[nm] = {'k60_67': [x >> 8 for x in lv[60:68]], 'k70_89': [x >> 8 for x in lv[K0:K0 + 20]],
                  'is_the_seed': lv[K0:K0 + NV] == seed(lv)}
    h['seed_values'] = sv
    # H6 range checks: check word present, words out of range -> that word's default, the others kept
    g = bytearray(rec_h)
    bad = {1: 0x0900, 0: 0x8000, 17: 0x7f01, 23: 0xffff, 5: 0x7f80}
    for i, v in bad.items():
        struct.pack_into('>H', g, FXREC + 0x60 + 2 * i, v)
    wr(REC[3], g)
    wr(LK[3], rd(LKBASE, KITLEN))
    call(KIT_LOAD, [LK[3], REC[3]])
    got = words(rd(LK[3], KITLEN), FXOFF + 2 * K0, NV)
    exp = [VDEF[i] << 8 if i in bad else T[i] for i in range(NV)]
    edge = bytearray(rec_h)
    for i in range(NV):
        struct.pack_into('>H', edge, FXREC + 0x60 + 2 * i, VMAX[i] << 8)
    wr(REC[4], edge)
    wr(LK[4], rd(LKBASE, KITLEN))
    call(KIT_LOAD, [LK[4], REC[4]])
    got_edge = words(rd(LK[4], KITLEN), FXOFF + 2 * K0, NV)
    h['range_checks'] = {'out_of_range_words': {str(70 + i): '%04x' % v for i, v in bad.items()},
                         'each_takes_its_default_the_others_kept': got == exp,
                         'every_word_at_its_maximum_kept': got_edge == [v << 8 for v in VMAX]}
    # H7 version != 0 -> FUN_400e7140's row defaults; the new-kit defaults alone
    g = bytearray(rec_h)
    struct.pack_into('>I', g, FXREC, 1)
    wr(REC[5], g)
    wr(LK[5], rd(LKBASE, KITLEN))
    call(KIT_LOAD, [LK[5], REC[5]])
    lv5 = words(rd(LK[5], KITLEN), FXOFF, 101)
    wr(LK[6], rd(LKBASE, KITLEN))
    call(NEW_KIT, [LK[6] + FXOFF])
    lv6 = words(rd(LK[6], KITLEN), FXOFF, 101)
    h['version_1_record'] = {'k70_89': [x >> 8 for x in lv5[K0:K0 + 20]], 'k90_96': ['%04x' % x for x in lv5[90:97]],
                             'k70_89_are_the_row_defaults': lv5[K0:K0 + 20] == [v << 8 for v in VDEF[:20]],
                             'the_mixes_0': lv5[87:90] == [0, 0, 0]}
    h['new_kit_defaults (FUN_400e7140)'] = {'k70_89': [x >> 8 for x in lv6[K0:K0 + 20]],
                                            'are_the_rows_defaults': lv6[K0:K0 + 20] == [v << 8 for v in VDEF[:20]],
                                            'stock_k60_67': [x >> 8 for x in lv6[60:68]]}
    # H8 slot 17: single edits
    wr(kit, base_kit)
    fxo = find_fxsetup(m, rd, call, live)
    h['fxsetup_object'] = '%#010x' % fxo if fxo else None
    if fxo:
        mirror = r32(fxo + 0x20)
        h['mirror_record'] = '%#010x' % mirror
        se = {}
        cur = list(T)
        for nm, k, v in (('a band knob (k 75 Low/Mid X-Over)', 75, 0x4400), ('a band mix (k 88 Mid Mix)', 88, 0x2a00),
                         ('a stock knob (k 60 THR)', 60, 0x1500), ('an LFO row (k 10, id 0)', 10, 0x0700)):
            before = rd(mirror, 0xa0)
            wr(live + 2 * k, struct.pack('>H', v))
            if k >= K0:
                cur[k - K0] = v
            wr(INFO, struct.pack('>III', PARAM_INFO_VT, k, 1))
            call(VSLOT04, [fxo, INFO])
            after = rd(mirror, 0xa0)
            e = {'record_diff': diff_ranges(before, after), 'our_words_and_check_word': after[0x60:0x98] == ours(cur),
                 'id0_before_after': ['%04x' % struct.unpack('>H', x[4:6])[0] for x in (before, after)]}
            if k >= K0:
                e['ok'] = e['our_words_and_check_word'] and before[4:6] == after[4:6]
            elif k == 10:
                e['ok'] = e['our_words_and_check_word'] and after[4:6] == struct.pack('>H', v)       # stock: id 0
            else:
                e['ok'] = e['our_words_and_check_word'] and before[4:6] == after[4:6]
            se[nm] = e
        # the vector type (several k in one change): find its vtable in the image
        vts = vtable_of(rd, TI_MANY)
        se['vector_type_vtables'] = ['%#010x' % x for x in vts]
        if vts:
            for nm, ks, restore in (('vector [75, 80] (all from 70)', [75, 80], True), ('vector [60, 75] (mixed)', [60, 75], False)):
                before = rd(mirror, 0xa0)
                for k in ks:
                    wr(live + 2 * k, struct.pack('>H', 0x0300 + k))
                    if k >= K0:
                        cur[k - K0] = 0x0300 + k
                wr(VEC, struct.pack('>%dI' % len(ks), *ks))
                wr(INFO, struct.pack('>III', vts[0], VEC, VEC + 4 * len(ks)))
                call(VSLOT04, [fxo, INFO])
                after = rd(mirror, 0xa0)
                e = {'record_diff': diff_ranges(before, after), 'our_words_and_check_word': after[0x60:0x98] == ours(cur),
                     'id0_before_after': ['%04x' % struct.unpack('>H', x[4:6])[0] for x in (before, after)]}
                stock_id0 = struct.pack('>H', 0x0300 + 75)
                e['ok'] = e['our_words_and_check_word'] and (before[4:6] == after[4:6] if restore else after[4:6] == stock_id0)
                se[nm] = e
        # the whole-setup path (info 0: paste, clear, reload): stock posts a job; the hook writes the words at once
        wr(mirror + 0x60, bytes(0x38))
        wr(live + 2 * 80, struct.pack('>H', 0x2100))
        cur[80 - K0] = 0x2100
        call(VSLOT04, [fxo, 0])
        se['info 0 (whole setup)'] = {'ok': rd(mirror + 0x60, 0x38) == ours(cur)}
        h['slot17'] = se
    res['hooks'] = h

    # ---- S: the stock sites (a fresh machine)
    m2 = setup(snap)
    uc, rd, wr, r32, call = tools(m2)
    if mode == 'built':
        stock_sites(m2)
    for a, sb in STOCK_BYTES.items():
        assert rd(a, len(sb) // 2).hex() == sb
    kit = r32(KITPTR)
    live = kit + FXOFF
    put_k(wr, live, T)
    wr(LKBASE, rd(kit, KITLEN))
    put_k(wr, LKBASE + FXOFF, M)
    s = {}
    wr(REC[0], b'\xa5' * RECLEN)
    s['serializer_ret'] = call(KIT_SAVE, [REC[0], kit, 0]) & 0xff
    rec_s = rd(REC[0], RECLEN)
    s['stock_serializer_leaves_fx_60_97 (0xa5 kept)'] = rec_s[FXREC + 0x60:FXREC + 0x98] == b'\xa5' * 0x38
    s['hooked_serializer_differs_from_stock_only_at_fx_60_97'] = diff_ranges(rec_s, rec_h) == [('+%#x' % (FXREC + 0x60), 0x38)] or \
        all(FXREC + 0x60 <= int(x[0], 16) and int(x[0], 16) + x[1] <= FXREC + 0x98 for x in diff_ranges(rec_s, rec_h))
    s['diff_hooked_vs_stock_serializer'] = diff_ranges(rec_s, rec_h)
    # a stock 1.12 loading our kit: the hooked serializer's record, and the same record with our words zeroed
    rz = bytearray(rec_h)
    rz[FXREC + 0x60:FXREC + 0x98] = bytes(0x38)
    wr(REC[1], rec_h)
    wr(REC[2], rz)
    lks = []
    for i, r in ((1, REC[1]), (2, REC[2])):
        wr(LK[i], rd(LKBASE, KITLEN))
        call(KIT_LOAD, [LK[i], r])
        lks.append(rd(LK[i], KITLEN))
    s['stock_load_of_our_kit_equals_without_our_words'] = lks[0] == lks[1]
    s['stock_loader_leaves_k70_96'] = words(lks[0], FXOFF + 2 * K0, NV) == M
    s['loaded_kit_vs_hooked_load_differs_only_at_k70_96'] = all(
        FXOFF + 2 * K0 <= int(x[0], 16) and int(x[0], 16) + x[1] <= FXOFF + 2 * (K0 + NV) for x in diff_ranges(lks[0], lk_h))
    # an in-place stock re-save of our record keeps our words
    wr(REC[3], rec_h)
    call(KIT_SAVE, [REC[3], kit, 0])
    r3 = rd(REC[3], RECLEN)
    s['stock_resave_in_place_keeps_our_words'] = r3[FXREC + 0x60:FXREC + 0x98] == rec_h[FXREC + 0x60:FXREC + 0x98]
    s['stock_resave_in_place_diff_vs_our_record'] = diff_ranges(rec_h, r3)
    fxo = find_fxsetup(m2, rd, call, live)
    if fxo:
        mirror = r32(fxo + 0x20)
        before = rd(mirror, 0xa0)
        wr(live + 2 * 75, struct.pack('>H', 0x4400))
        wr(INFO, struct.pack('>III', PARAM_INFO_VT, 75, 1))
        call(VSLOT04, [fxo, INFO])
        after = rd(mirror, 0xa0)
        s['stock_single_change_k75_record_diff'] = diff_ranges(before, after)
        s['stock_single_change_k75_id0_word'] = '%04x' % struct.unpack('>H', after[4:6])[0]
    res['stock'] = s

    oks = {
        'H1 serializer: our words + 0xC3B1, nothing else differs from stock':
            h['record_holds_the_raw_live_words_and_0xC3B1'] and s['hooked_serializer_differs_from_stock_only_at_fx_60_97'],
        'H2 loader round trip exact': h['round_trip_k70_96_exact'],
        'H4/H5 old, garbage, no-check-word, later-layout records seeded': all(
            c['is_the_seed'] and c['the_rest_of_the_kit_as_the_round_trip_load'] for c in cases.values()),
        'H4 seed values (RAT +1, clamps)': all(c['is_the_seed'] for c in sv.values()),
        'H6 range checks': h['range_checks']['each_takes_its_default_the_others_kept'] and h['range_checks']['every_word_at_its_maximum_kept'],
        'H7 version 1 -> row defaults': h['version_1_record']['k70_89_are_the_row_defaults'],
        'new-kit defaults = the rows\'': h['new_kit_defaults (FUN_400e7140)']['are_the_rows_defaults'],
        'H8 slot 17': bool(h.get('slot17')) and all(v.get('ok', True) for v in h['slot17'].values() if isinstance(v, dict)),
        'S stock serializer leaves +0x60..+0x97': s['stock_serializer_leaves_fx_60_97 (0xa5 kept)'],
        'S stock load of our kit = without our words': s['stock_load_of_our_kit_equals_without_our_words'] and s['stock_loader_leaves_k70_96'],
        'S in-place stock re-save keeps our words': s['stock_resave_in_place_keeps_our_words'],
    }
    res['oks'] = oks
    res['VERDICT'] = 'PASS' if all(oks.values()) else 'FAIL'
    json.dump(res, open(out, 'w'), indent=1, default=str)
    print(json.dumps(oks, indent=1))
    print('VERDICT', res['VERDICT'])


if __name__ == '__main__':
    main()
