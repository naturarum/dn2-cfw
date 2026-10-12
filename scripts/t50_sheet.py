#!/usr/bin/env python3
"""T5.0: the contact sheet for the owner: before/after screens, the new glyphs, every font and icon.

    venv/bin/python scripts/t50_sheet.py

Reads out/T5.0/emu/stock/pages/*.txt and out/T5.0/hot/*.txt (the emulator's ASCII screens), out/T5.0/art/,
out/T5.0/atlas/index.json; writes out/T5.0/before-after.png and out/T5.0/index.html (open it in a
browser; it links the PNGs next to it).
"""
import html
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from t50_glyphs import png                       # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
T5 = os.path.join(ROOT, 'out', 'T5.0')
BEFORE = os.path.join(T5, 'emu', 'stock', 'pages')
AFTER = os.path.join(T5, 'hot')                 # FP01 hot-patched into stock's warm snapshot
SCREENS = [
    ('01-trackfx', 'Track FX page (FX key): the send on encoder G'),
    ('02-trackfx-chr', 'Track FX page, encoder G turned one step'),
    ('05-sendfx3-chorus', 'SEND FX 3 (FUNC+FX, DOWN, DOWN): the chorus page'),
    ('06-chorus-dpth', 'chorus page, encoder A turned'),
    ('07-chorus-spd', 'chorus page, encoder B turned'),
    ('04-sendfx2-reverb', 'SEND FX 2 (reverb), unchanged'),
]
FONT_USE = {
    0x40291ca0: 'knob labels and values',
    0x40290dd0: 'page title, value bubble ("Depth=127")',
    0x402906b4: 'header box "A01", tempo, the page box ("CHO", "SYN 1")',
    0x4028ffdc: 'large pattern name ("LIGHTHOUSE")',
}


def ascii_screen(path):
    rows = [l.rstrip('\n') for l in open(path)]
    return [[1 if c == '#' else 0 for c in r[:128]] for r in rows[:64]]


def composite():
    G = 3
    out = []
    for name, _ in SCREENS:
        a, b = os.path.join(BEFORE, name + '.txt'), os.path.join(AFTER, name + '.txt')
        if not (os.path.exists(a) and os.path.exists(b)):
            continue
        sa, sb = ascii_screen(a), ascii_screen(b)
        for y in range(64):
            out.append([G] * 4 + sa[y] + [G] * 8 + sb[y] + [G] * 4)
        out.append([G] * (128 * 2 + 16))
        out.append([G] * (128 * 2 + 16))
    if out:
        out = [[G] * len(out[0])] * 2 + out
        png(os.path.join(T5, 'before-after.png'), out, 3, (0, 255, 110, 60))
    return bool(out)


def page():
    idx = json.load(open(os.path.join(T5, 'atlas', 'index.json')))
    art = json.load(open(os.path.join(T5, 'art', 'art.json')))
    e = html.escape
    h = ['<!doctype html><html><head><meta charset="utf-8"><title>DN2 glyphs</title>',
         '<style>body{font:14px system-ui,sans-serif;margin:16px;background:#f4f4f4;color:#111}'
         'img{image-rendering:pixelated;border:1px solid #888;max-width:100%}'
         'figure{display:inline-block;margin:0 12px 16px 0;vertical-align:top}'
         'figcaption{font-size:12px;max-width:780px}code{font-size:12px}'
         '@media (prefers-color-scheme:dark){body{background:#1b1b1b;color:#ddd}}</style></head><body>',
         '<h1>Digitone II 1.12: the chorus page, its glyphs and fonts (T5.0)</h1>',
         '<p>Emulator only. Details: <code>notes/T5.0-flanger-page-ui.md</code>. Glyph atlases: black '
         '= pixel off, white = on, mid grey = masked out (the blit leaves the screen there).</p>',
         '<h2>1. Before and after (stock 1.12 left; right: build FP01 hot-patched into the same snapshot, so the page title still says Chorus: it is built at boot)</h2>']
    for name, cap in SCREENS:
        a = os.path.join('emu', 'stock', 'pages', name + '.png')
        b = os.path.join('hot', name + '.png')
        if os.path.exists(os.path.join(T5, a)) and os.path.exists(os.path.join(T5, b)):
            h.append('<figure><img src="%s" width="384"> <img src="%s" width="384"><figcaption>%s'
                     '</figcaption></figure>' % (a, b, e(cap)))
    h.append('<h2>2. The new glyphs (stock frames above, new below)</h2>')
    for r in art:
        h.append('<figure><img src="art/%s.png"><figcaption><b>%s</b>: %d frames, 19 x 17, replaces '
                 'the %s (strip <code>%s</code>)</figcaption></figure>'
                 % (r['name'], e(r['name']), r['frames'], e(r['replaces']), r['strip']))
    h.append('<h2>3. Fonts (17)</h2><p>Record: max width, (ascent&lt;&lt;16)|height, widths, offsets, '
             'columns (MSB = bottom row). Codes from 0x00 in rows of 32, empty glyphs left out.</p>')
    for f in idx['fonts']:
        use = FONT_USE.get(f['addr'], '')
        h.append('<figure><img src="atlas/%s"><figcaption>font <code>0x%08x</code>, max width %d, '
                 'height %d, ascent %d, %d glyphs%s</figcaption></figure>'
                 % (f['png'], f['addr'], f['maxw'], f['height'], f['ascent'], f['glyphs'],
                    (': <b>' + e(use) + '</b>') if use else ''))
    h.append('<h2>4. Strips (value-driven glyphs): the ones on the FX pages first</h2>')
    strips = sorted(idx['strips'], key=lambda s: (s['name'] == '', s['vector']))
    for s in strips:
        h.append('<figure><img src="atlas/%s"><figcaption><code>%s</code>: %d frames, %d x %d, data '
                 'from <code>%s</code>%s</figcaption></figure>'
                 % (s['png'], s['vector'], s['frames'], s['w'], s['h'], s['first_data'],
                    (': <b>' + e(s['name']) + '</b>') if s['name'] else ''))
    h.append('<h2>5. Single icons, by RAM region of their objects</h2>')
    for g in idx['singles']:
        named = [i for i in g['icons'] if i['name']]
        h.append('<figure><img src="atlas/%s"><figcaption>objects at <code>%s</code>: %d icons%s'
                 '</figcaption></figure>' % (g['png'], g['region'], g['count'],
                                             ''.join('; <code>%s</code> %s' % (i['addr'], e(i['name']))
                                                     for i in named)))
    h.append('</body></html>')
    with open(os.path.join(T5, 'index.html'), 'w') as f:
        f.write('\n'.join(h) + '\n')


if __name__ == '__main__':
    print('before-after.png:', composite())
    page()
    print('index.html written')
