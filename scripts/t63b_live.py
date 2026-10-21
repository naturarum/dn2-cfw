#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""T6.3 CMP1: scripts/t65_live.py with the MIXER view's state and the drawn texts at every screen.

    cd vendor/digikit && DT2_SECTIONS=... uv run python ../../scripts/t63b_live.py SNAP --syx SYX \\
        --identify-as STOCK.syx --speed 1 --script S --out DIR [t65_live / t19_live / emu.live options] \\
        [--map BUILD.map.json] [--fake-gr Q8,Q9,Q10[,Q11]] [--list-reads] [--set-k K=V,...]

At every `png NAME` of the script, also records (into DIR/t63b.json, by screen name):
  - every text the last complete frame drew (drawText 0x401153f8: font, x, y, the string), in the
    order drawn: the title, the knob labels and values, the header's "Name=value", the box;
  - the MIXER view (MasterPageView, vtable 0x401e292c, found in the heap at the start): its page
    list (+0x7c..+0x80) and the current page's index (+0x90) and id;
  - the current kit's live FX words k 59 (DUAL), 60..69 (the stock compressor's), 70..86 (the band
    rows), value and raw;
  - the DSP reply half-words 8..11 as the ColdFire holds them (0x80001a30..0x80001a37);
  - with --map (a build's .map.json): comp-ui's compui_draws and compui_bar[3].
--fake-gr writes those half-words (Q15 of 1 - g) at the start of every drawAll (0x4011d60a): the
emulator runs no DSP, so the band pages' meters read what this puts there. --list-reads counts the
reads of the stock MIXER page list 0x401e0434 (5 longs) by PC. --set-k writes live kit FX words
(K=value, value << 8) once at the start. --stock-sites (CMP3) gives comp-link 0.2's three storage sites their stock
bytes back at the start. --force-ob8 stands in for an OUTBOX 8 on USB (which the
emulator does not model): FUN_400f2e54 (connected) and FUN_400f2f1e (outputs) answer 1, and the
MIXER view's page list gets the Outbox 8 page (26) at its end, as the view's constructor pushes it
when one is connected (FUN_40070bc6); the list is moved to a buffer with room (0x47B00000) so that
the stock inserts never reallocate it. Everything else is t65_live.py's (--stock-reads, --state,
...). Read-only on digikit.
"""
import collections
import json
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.getcwd())                      # vendor/digikit
import t65_live                                      # noqa: E402
from emu import live                                 # noqa: E402

KITPTR, FXOFF = 0x800052a0, 0x48e4
RX = 0x80001a20
MPV_VT = 0x401e292c
DRAWALL, DRAWTEXT = 0x4011d60a, 0x401153f8
STOCK_LIST = 0x401e0434
HEAP = (0x44000000, 0x46800000)


OB8_CONNECTED, OB8_OUTPUTS = 0x400f2e54, 0x400f2f1e
OB8_LIST = 0x47B00000


class S:
    force_ob8 = False
    stock_sites = False
    syms = {}
    fake = None
    set_k = {}
    list_reads = collections.Counter()
    mpv = None
    cur = []
    last = []
    frames = 0
    captured = {}


def rd32(uc, a):
    return struct.unpack('>I', bytes(uc.mem_read(a, 4)))[0]


def find_mpv(uc):
    """the MasterPageView: the heap object whose vtable is 0x401e292c and whose page list holds
    small page ids"""
    pat = struct.pack('>I', MPV_VT)
    hits = []
    step = 0x100000
    for base in range(HEAP[0], HEAP[1], step):
        try:
            blob = bytes(uc.mem_read(base, step))
        except Exception:                            # noqa: BLE001 (unmapped)
            continue
        i = blob.find(pat)
        while i >= 0:
            if i % 4 == 0:
                a = base + i
                try:
                    b, e = rd32(uc, a + 0x7c), rd32(uc, a + 0x80)
                    n = (e - b) // 4
                    ids = [rd32(uc, b + 4 * k) for k in range(n)] if 0 < n <= 16 else []
                    if ids and all(0 < x < 64 for x in ids):
                        hits.append(a)
                except Exception:                    # noqa: BLE001
                    pass
            i = blob.find(pat, i + 1)
    return hits


def mixer(uc):
    if not S.mpv:
        return None
    b, e = rd32(uc, S.mpv + 0x7c), rd32(uc, S.mpv + 0x80)
    pages = [rd32(uc, b + 4 * k) for k in range((e - b) // 4)] if 0 <= e - b <= 64 else []
    idx = rd32(uc, S.mpv + 0x90)
    return {'mpv': '0x%08x' % S.mpv, 'pages': pages, 'index': idx,
            'page': pages[idx] if 0 <= idx < len(pages) else None}


def grab(uc):
    kit = rd32(uc, KITPTR)
    fx = [struct.unpack('>H', bytes(uc.mem_read(kit + FXOFF + 2 * k, 2)))[0] for k in range(101)]
    out = {'mixer': mixer(uc), 'texts': [list(t) for t in S.last], 'frame': S.frames,
           'k59': fx[59] >> 8, 'k60_69': [v >> 8 for v in fx[60:70]], 'k70_86': [v >> 8 for v in fx[70:87]],
           'k70_86_raw': ['%04x' % v for v in fx[70:87]], 'k87_89': [v >> 8 for v in fx[87:90]],
           'rx_hw8_11': list(struct.unpack('>4h', bytes(uc.mem_read(RX + 16, 8))))}
    for nm, a in S.syms.items():
        if nm == 'compui_bar':
            out[nm] = list(struct.unpack('>3i', bytes(uc.mem_read(a, 12))))
        else:
            out[nm] = struct.unpack('>i', bytes(uc.mem_read(a, 4)))[0]
    return out


def install_more(m, args):
    from unicorn import UC_HOOK_CODE, UC_HOOK_MEM_READ
    from unicorn.m68k_const import UC_M68K_REG_A7, UC_M68K_REG_PC
    uc = m.uc
    hits = find_mpv(uc)
    S.mpv = hits[0] if len(hits) == 1 else None
    print('[t63b] MasterPageView: %s' % ', '.join('0x%08x' % h for h in hits), flush=True)
    if S.mpv:
        print('[t63b] MIXER view at start: %s' % mixer(uc), flush=True)
    if S.force_ob8 and S.mpv:
        from unicorn.m68k_const import UC_M68K_REG_D0

        def answer1(uc_, addr, size, user):
            sp = uc_.reg_read(UC_M68K_REG_A7)
            uc_.reg_write(UC_M68K_REG_D0, 1)
            uc_.reg_write(UC_M68K_REG_A7, sp + 4)
            uc_.reg_write(UC_M68K_REG_PC, rd32(uc_, sp))
        uc.hook_add(UC_HOOK_CODE, answer1, begin=OB8_CONNECTED, end=OB8_CONNECTED)
        uc.hook_add(UC_HOOK_CODE, answer1, begin=OB8_OUTPUTS, end=OB8_OUTPUTS)
        pages = mixer(uc)['pages'] + [26]
        m.ensure(OB8_LIST)
        uc.mem_write(OB8_LIST, b''.join(struct.pack('>I', x) for x in pages))
        uc.mem_write(S.mpv + 0x7c, struct.pack('>III', OB8_LIST, OB8_LIST + 4 * len(pages), OB8_LIST + 0x40))
        print('[t63b] --force-ob8: MIXER view now %s' % mixer(uc), flush=True)
    if S.stock_sites:
        # CMP3 (2026-10-18): comp-link 0.2's three storage sites given their stock bytes back (the base runs)
        for a, sb in ((0x400dcf34, '2f0b2f0a246f000c'), (0x400dcef0, '2f0b2f0a226f000c'), (0x401da4f0, '4002ffca')):
            uc.mem_write(a, bytes.fromhex(sb))
        print('[t63b] --stock-sites: the FX record serializer, loader and FxSetup slot 17 as stock', flush=True)
    kit = rd32(uc, KITPTR)
    for k, v in S.set_k.items():
        uc.mem_write(kit + FXOFF + 2 * k, struct.pack('>H', v << 8))
        print('[t63b] kit FX k %d = %d' % (k, v), flush=True)

    def on_drawall(uc_, addr, size, user):
        S.last = S.cur
        S.cur = []
        S.frames += 1
        if S.fake:
            uc_.mem_write(RX + 16, struct.pack('>%dh' % len(S.fake), *S.fake))
    uc.hook_add(UC_HOOK_CODE, on_drawall, begin=DRAWALL, end=DRAWALL)

    def on_text(uc_, addr, size, user):
        sp = uc_.reg_read(UC_M68K_REG_A7)
        bmp, font, x, y, mono, maxlen, p = struct.unpack('>IIiiIiI', bytes(uc_.mem_read(sp + 4, 28)))
        n = maxlen if 0 < maxlen <= 64 else 64
        try:
            t = bytes(uc_.mem_read(p, n)).split(b'\0')[0].decode('latin-1')
        except Exception:                            # noqa: BLE001
            t = '?'
        S.cur.append(('0x%08x' % font, x, y, t))
    uc.hook_add(UC_HOOK_CODE, on_text, begin=DRAWTEXT, end=DRAWTEXT)
    if 'list-reads' in args:
        def rhook(uc_, access, addr, size, value, user):
            S.list_reads[uc_.reg_read(UC_M68K_REG_PC)] += 1
        uc.hook_add(UC_HOOK_MEM_READ, rhook, begin=STOCK_LIST, end=STOCK_LIST + 5 * 4 - 1)


def main():
    argv = sys.argv[1:]
    out = argv[argv.index('--out') + 1] if '--out' in argv else '.'
    more = set()
    if '--map' in argv:
        i = argv.index('--map')
        mp = json.load(open(argv[i + 1]))
        del argv[i:i + 2]
        for nm in ('compui_draws', 'compui_bar'):
            if nm in mp:
                S.syms[nm] = int(mp[nm], 16)
    if '--fake-gr' in argv:
        i = argv.index('--fake-gr')
        S.fake = [int(x) for x in argv[i + 1].split(',')]
        del argv[i:i + 2]
    if '--set-k' in argv:
        i = argv.index('--set-k')
        S.set_k = {int(k): int(v) for k, v in (kv.split('=') for kv in argv[i + 1].split(','))}
        del argv[i:i + 2]
    if '--list-reads' in argv:
        argv.remove('--list-reads')
        more.add('list-reads')
    if '--force-ob8' in argv:
        argv.remove('--force-ob8')
        S.force_ob8 = True
    if '--stock-sites' in argv:
        argv.remove('--stock-sites')
        S.stock_sites = True
    orig_install = t65_live.install

    def install(m, args):
        orig_install(m, args)
        install_more(m, more)
    t65_live.install = install
    orig_png = live.save_png

    def save_png(emu, path):
        r = orig_png(emu, path)
        name = os.path.basename(path)[:-4]
        try:
            S.captured[name] = grab(emu._m.uc)
        except Exception as e:                       # noqa: BLE001
            S.captured[name] = {'error': str(e)}
        return r
    live.save_png = save_png
    orig_summary = live.summary

    def summary(emu):
        S.captured['__end__'] = grab(emu._m.uc)
        if 'list-reads' in more:
            S.captured['__list_reads__'] = {'total': sum(S.list_reads.values()),
                                           'by_pc': {'0x%08x' % k: v for k, v in S.list_reads.most_common()}}
            print('[t63b] reads of the stock MIXER page list 0x401e0434: %d' % sum(S.list_reads.values()),
                  flush=True)
        json.dump(S.captured, open(os.path.join(out, 't63b.json'), 'w'), indent=1)
        print('[t63b] state at %d screens -> %s' % (len(S.captured) - 1, os.path.join(out, 't63b.json')), flush=True)
        return orig_summary(emu)
    live.summary = summary
    sys.argv = [sys.argv[0]] + argv
    return t65_live.main()


if __name__ == '__main__':
    sys.exit(main())
