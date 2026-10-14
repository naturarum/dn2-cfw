#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""T6.3 CMP1: page copy, clear and paste on the MIXER pages, host-called on the OS's own code.

    cd vendor/digikit && uv run python ../../scripts/t63b_pagecopy.py SNAP --pages 0,1,2 --json OUT

Why host-called: in the MIXER view, page copy/paste/clear want the view's page key (MOD, key id 6 at
MasterPageView + 0x88) held with REC/STOP/PLAY (ParameterPageView::vslot02), but pressing MOD there
opens the MOD page first; FUNC+REC/STOP/PLAY copy, paste and clear the pattern instead. That holds on
GT01's stock Compressor page as on CMP1's band pages (scripts/t63b-emu.sh runs copytest*), so the
keys cannot reach them in either build. This calls them directly, as the key handler would, on a
snapshot (emu.snapshot.restore, with digikit's ISA patches for FF1 & co., no other hook):
  for each page index given: the view's current index (+0x90) is set; the record comes from the
  page-record accessor 0x400c25d8 (core_pagerec on CMP1); then
  1. MasterPageView::vslot53 (0x40070eea): COPY PAGE; the popup text it formats is read back;
  2. every knob row's (A..H) live kit FX word (by the row's k) is changed (5 steps, within its range);
  3. MasterPageView::vslot55 (0x400710ec): CLEAR PAGE: the words go to the rows' defaults (A..H;
     LEVEL is not part of a page's copy/clear/paste, FUN_4003e69e collects the eight knobs);
  4. MasterPageView::vslot54 (0x40070fb8): PASTE PAGE: the words come back to the copied values.
The words, the popups and each call's return value go to --json. Nothing is written to the snapshot.
"""
import json
import os
import struct
import sys

DIGIKIT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'vendor', 'digikit')
sys.path.insert(0, DIGIKIT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

KITPTR, FXOFF = 0x800052a0, 0x48e4
PAGEREC = 0x400c25d8
COPY, PASTE, CLEAR = 0x40070eea, 0x40070fb8, 0x400710ec
POPUPS = {'copy': 0x405ce3b0, 'paste': 0x405ce390, 'clear': 0x405ce370}
DESC_AT = 0x400174e0                     # the operand of lea table,a0 at 0x400174de (param-grow's or stock)


def main():
    argv = sys.argv[1:]
    snap = argv[0]
    pages = [int(x) for x in argv[argv.index('--pages') + 1].split(',')] if '--pages' in argv else [0]
    out = argv[argv.index('--json') + 1] if '--json' in argv else None
    from emu import snapshot, harness
    import t63b_live
    m, extra, regs = snapshot.restore(snap)
    m.install_isa_patches()             # FF1, BITREV, BYTEREV, MOVEC (operator new uses FF1)
    uc = m.uc
    rd32 = lambda a: struct.unpack('>I', bytes(uc.mem_read(a, 4)))[0]   # noqa: E731
    rd16 = lambda a: struct.unpack('>H', bytes(uc.mem_read(a, 2)))[0]   # noqa: E731
    hits = t63b_live.find_mpv(uc)
    if len(hits) != 1:
        raise SystemExit('MasterPageView: %s' % hits)
    mpv = hits[0]
    t63b_live.S.mpv = mpv
    desc = rd32(DESC_AT)
    kit = rd32(KITPTR)
    res = {'snap': snap, 'mpv': '0x%08x' % mpv, 'table': '0x%08x' % desc, 'pages': []}

    def cstr(a):
        return bytes(uc.mem_read(a, 40)).split(b'\0')[0].decode('latin-1')

    for idx in pages:  # page indices of FX-set pages only (INT MIX pages hold track rows)
        uc.mem_write(mpv + 0x90, struct.pack('>I', idx))
        mix = t63b_live.mixer(uc)
        pid = mix['page']
        rec = harness.call(m, PAGEREC, [pid])
        rows = [rd32(rec + 8 + 4 * i) for i in range(8)]   # A..H: what copy/clear/paste act on (not LEVEL)
        info = []
        for r in rows:
            if not r:
                info.append(None)
                continue
            d = desc + 0x3c * r
            k, mn, mx, df = rd32(d + 4), rd32(d + 8), rd32(d + 0xc), rd32(d + 0x10)
            info.append({'row': '0x%x' % r, 'k': k, 'min': mn >> 8, 'max': mx >> 8, 'default': df >> 8,
                         'name': cstr(rd32(d + 0x28))})
        ks = [i['k'] for i in info if i]
        val = lambda: [rd16(kit + FXOFF + 2 * k) >> 8 for k in ks]       # noqa: E731
        p = {'index': idx, 'page': pid, 'record': '0x%08x' % rec, 'short': cstr(rd32(rec)), 'long': cstr(rd32(rec + 4)),
             'rows': info, 'k': ks}
        for a in POPUPS.values():
            uc.mem_write(a, b'\0' * 4)
        p['before'] = val()
        p['copy_returned'] = harness.call(m, COPY, [mpv])
        p['copy_popup'] = cstr(POPUPS['copy'])
        for i_ in info:
            if i_:
                v = (rd16(kit + FXOFF + 2 * i_['k']) >> 8)
                v = (v + 5 if v + 5 <= i_['max'] else v - 5 if v - 5 >= i_['min'] else
                     i_['max'] if v != i_['max'] else i_['min'])
                uc.mem_write(kit + FXOFF + 2 * i_['k'], struct.pack('>H', v << 8))
        p['changed'] = val()
        p['clear_returned'] = harness.call(m, CLEAR, [mpv])
        p['clear_popup'] = cstr(POPUPS['clear'])
        p['cleared'] = val()
        p['defaults'] = [i_['default'] for i_ in info if i_]
        p['paste_returned'] = harness.call(m, PASTE, [mpv])
        p['paste_popup'] = cstr(POPUPS['paste'])
        p['pasted'] = val()
        p['ok'] = (p['cleared'] == p['defaults'] and p['pasted'] == p['before'] and p['changed'] != p['before'])
        res['pages'].append(p)
        print('page %d (id %d, "%s"): popups "%s" / "%s" / "%s"' % (idx, pid, p['long'], p['copy_popup'],
                                                                   p['clear_popup'], p['paste_popup']))
        print('   k %s' % ks)
        print('   before %s\n   changed %s\n   cleared %s (defaults %s)\n   pasted %s  -> %s' % (
            p['before'], p['changed'], p['cleared'], p['defaults'], p['pasted'], 'OK' if p['ok'] else 'NOT AS EXPECTED'))
    if out:
        json.dump(res, open(out, 'w'), indent=1)
    return 0 if all(p['ok'] for p in res['pages']) else 1


if __name__ == '__main__':
    sys.exit(main())
