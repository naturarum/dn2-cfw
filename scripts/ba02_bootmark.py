#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Base 0.2: the DIGIWERK boot mark. Draws the candidate marks (128 x 64, one bit a pixel, our own pixel letters) and
renders the Digitone II's start-up animation with each, by running the stock intro FUN_400d39ea frame by frame on
DJ01's main OS image (Unicorn, digikit's ColdFire model), its logo blit replaced by the mark.

    cd vendor/digikit && uv run python ../../scripts/ba02_bootmark.py --out ../../out/BA02/bootmark [--frames 200]

The intro (OS 1.12; dn2_firmware_explore's findings on 1.11, 0x1000 lower): an object at 0x42c46678 (+0 the scroll,
+4 the source Bitmap, +0x20 a 16,384-entry offset table). At frame 0 it clears the source, blits Elektron's logo
(the Bitmap at 0x44647008) into it at (48, 20) (0x400d3ac4, jsr 0x40115adc) and builds the table (0x400d376a);
frames 1..4 copy the source to the panel; from frame 5 each panel pixel samples the source at the table's offsets
plus the scroll, inside a window that grows with the frame. A Bitmap: +4 width, +8 height, +0x0c words per column,
+0x10 the pixels (column-major: word y >> 5 of column x, bit 31 - (y & 31)), +0x14 a second buffer. The panel's rows
run up: y = 0 is the bottom row (digikit's emu/panel.py; the menus draw their titles at y 58 and their knob hints at
y 0). So the mod writes the picture's row r at y = 63 - r (boot-mark 0.2; 0.1, in BA02, wrote it at y = r and the
unit showed it upside down, 2026-10-28), and the renders here put y = 63 at the top, as the panel shows it.

The player (0x400d3d0a, started by the chooser 0x400d3eea, which picks this intro unless the stock random generator's
first value is above 32,735) calls it as (frame, 175, the panel Bitmap), 175 frames. Frame 0 builds the table with
soft-float math, hundreds of millions of instructions: it runs here without the usual cap (until 2026-10-28 a 50M cap
cut it short, so the tunnel in the first previews was wrong, and they were drawn upside down too). The table takes two
values of the stock random generator (an LCG, 0x40150950, its state at 0x405ce95c); the state is set as after one
draw from a cleared state, as on the unit if nothing else draws before the chooser [inferred: nothing seeds it].

Writes NAME.frames (the mark, then each frame, 1 KB each) per candidate and marks.json (each mark's bits, for the
mod); then, with the system Python (Pillow), --render writes NAME.png (the mark, x4) and NAME.gif (the animation, x4).
"""
import argparse
import json
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scripts'))
sys.dont_write_bytecode = True
try:                                                    # not needed for --render (the system Python)
    import dj01_lfocheck as D  # noqa: E402
    from emu.harness import call  # noqa: E402
    from unicorn import UC_HOOK_CODE  # noqa: E402
    from unicorn import m68k_const as MC  # noqa: E402
    Base = D.Box
except ImportError:
    Base = object

W, H = 128, 64
INTRO, BLIT_SITE = 0x400d39ea, 0x400d3ac4
OBJ, SRC, TAB = 0x42c46678, 0x42c4667c, 0x42c46698
GUARDS = (0x42c4cae4, 0x42c4cadc)
SCRATCH = 0x10500000
NFRAMES = 175                                           # the player's table entry 0x4028c694: the intro, 0xaf frames
RNG, RNG_STATE = 0x405ce95c, 12345                      # the LCG's state after one draw from 0 (the chooser's)

# our pixel letters: 9 rows, variable width, 2-pixel strokes
GLYPHS = {
    'D': ['#####.', '##..##', '##..##', '##..##', '##..##', '##..##', '##..##', '##..##', '#####.'],
    'I': ['##'] * 9,
    'G': ['.####.', '##..##', '##....', '##....', '##.###', '##..##', '##..##', '##..##', '.#####'],
    'W': ['##....##', '##....##', '##....##', '##....##', '##.##.##', '##.##.##', '########', '###..###', '##....##'],
    'E': ['#####', '##...', '##...', '##...', '####.', '##...', '##...', '##...', '#####'],
    'R': ['#####.', '##..##', '##..##', '##..##', '#####.', '##.##.', '##..##', '##..##', '##..##'],
    'K': ['##..##', '##..##', '##.##.', '####..', '###...', '####..', '##.##.', '##..##', '##..##'],
}


def word_bits(word, scale, gap=2):
    rows = []
    for y in range(9):
        line = []
        for i, ch in enumerate(word):
            if i:
                line += [0] * gap
            line += [1 if c == '#' else 0 for c in GLYPHS[ch][y]]
        rows.append(line)
    out = []
    for r in rows:
        big = [v for v in r for _ in range(scale)]
        out += [big] * scale
    return out


def blank():
    return [[0] * W for _ in range(H)]


def place(canvas, bits, x0, y0):
    for y, row in enumerate(bits):
        for x, v in enumerate(row):
            if v and 0 <= x0 + x < W and 0 <= y0 + y < H:
                canvas[y0 + y][x0 + x] = 1


def centred(canvas, bits, y0):
    place(canvas, bits, (W - len(bits[0])) // 2, y0)


def marks():
    m = {}
    a = blank()                                          # A: one line, scale 2, centred
    b = word_bits('DIGIWERK', 2)
    centred(a, b, (H - len(b)) // 2)
    m['A-one-line'] = a
    c = blank()                                          # B: DIGI over WERK, scale 3
    t, u = word_bits('DIGI', 3), word_bits('WERK', 3)
    centred(c, t, 3)
    centred(c, u, 3 + len(t) + 4)
    m['B-two-lines'] = c
    d = blank()                                          # C: one line in a frame, a bar under it
    b = word_bits('DIGIWERK', 2)
    centred(d, b, 20)
    for x in range(1, 127):
        for y in (3, 4, 59, 60):
            d[y][x] = 1
    for y in range(3, 61):
        for x in (1, 2, 125, 126):
            d[y][x] = 1
    for x in range(30, 98):
        d[44][x] = d[45][x] = 1
    m['C-framed'] = d
    return m


class Intro(Base):
    def __init__(self, name):
        super().__init__(name)
        for a in (0x42c00000, SCRATCH, SCRATCH + 0x100000):
            self.m.ensure(a)
        for lo, hi in ((0x400d3000, 0x400d5000), (0x40113000, 0x40116000), (0x40170000, 0x401a0000)):
            self.m.install_isa_patches_scoped(self.img[lo - D.LOAD:hi - D.LOAD], lo)
        self.mark = None
        self.uc.hook_add(UC_HOOK_CODE, self._blit, begin=BLIT_SITE, end=BLIT_SITE)
        self.uc.ctl_flush_tb()

    def bitmap(self, obj, buf, buf2):
        self.wr(obj, struct.pack('>IIIIII', 0x402069c4, W, H, 2, buf, buf2) + bytes([1, 0, 0, 0]))
        self.wr(buf, bytes(W * 2 * 4))
        self.wr(buf2, bytes(W * 2 * 4))

    def _blit(self, uc, addr, size, data):
        """the logo blit at frame 0: the mark into the source's pixels instead (as boot-mark 0.2's setPixel calls do:
        the picture's row r at y = 63 - r)"""
        if self.mark is not None:
            buf = self.u32(SRC + 0x10)
            for x in range(W):
                for half in range(2):
                    w = 0
                    for b in range(32):
                        if self.mark[63 - (32 * half + b)][x]:
                            w |= 0x80000000 >> b
                    self.wr(buf + 4 * (2 * x + half), struct.pack('>I', w))
            uc.reg_write(MC.UC_M68K_REG_PC, addr + 6)

    def setup_intro(self, mark):
        self.mark = mark
        for g in GUARDS:
            self.wr(g, b'\x01')
        self.bitmap(SRC, SCRATCH + 0x1000, SCRATCH + 0x2000)
        self.bitmap(SCRATCH, SCRATCH + 0x3000, SCRATCH + 0x4000)       # the panel
        self.wr(TAB, struct.pack('>III', SCRATCH + 0x10000, SCRATCH + 0x20000, SCRATCH + 0x20000))
        self.wr(OBJ, struct.pack('>I', 0))
        self.m.ensure(RNG)
        self.wr(RNG, struct.pack('>I', RNG_STATE))

    def frame(self, n):
        call(self.m, INTRO, [n, NFRAMES, SCRATCH], stack_top=0x10000000 + 0xff000, limit=4_000_000_000)
        raw = self.rd(SCRATCH + 0x3000, W * 8)
        img = blank()
        for x in range(W):
            hi, lo = struct.unpack('>II', raw[8 * x:8 * x + 8])
            for y in range(32):                         # as the panel shows it: y = 63 at the top
                img[63 - y][x] = (hi >> (31 - y)) & 1
                img[31 - y][x] = (lo >> (31 - y)) & 1
        return img


def pack(bits):
    return bytes(int(''.join(str(v) for v in bits[y][8 * i:8 * i + 8]), 2) for y in range(H) for i in range(W // 8))


def unpack(raw):
    return [[(raw[y * (W // 8) + x // 8] >> (7 - x % 8)) & 1 for x in range(W)] for y in range(H)]


def render(out):
    """with the system Python (Pillow): NAME.frames -> NAME.png (the mark) and NAME.gif (the animation), x4"""
    from PIL import Image
    for fn in sorted(os.listdir(out)):
        if not fn.endswith('.frames'):
            continue
        raw = open(os.path.join(out, fn), 'rb').read()
        frames = []
        for i in range(0, len(raw), W * H // 8):
            bits = unpack(raw[i:i + W * H // 8])
            im = Image.new('L', (W, H))
            im.putdata([235 if bits[y][x] else 0 for y in range(H) for x in range(W)])
            frames.append(im.resize((W * 4, H * 4), Image.NEAREST))
        name = fn[:-7]
        frames[0].save(os.path.join(out, name + '.png'))
        frames[1].save(os.path.join(out, name + '.gif'), save_all=True, append_images=frames[2:], duration=33, loop=0)
        print('%s: %d frames' % (name, len(frames) - 1))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--out', required=True)
    ap.add_argument('--frames', type=int, default=NFRAMES)
    ap.add_argument('--only')
    ap.add_argument('--render', action='store_true', help='write the PNGs and GIFs (the system Python, with Pillow)')
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    if a.render:
        render(a.out)
        return
    ms = marks()
    json.dump({k: [''.join(str(v) for v in row) for row in bits] for k, bits in ms.items()},
              open(os.path.join(a.out, 'marks.json'), 'w'), indent=0)
    for name, bits in ms.items():
        if a.only and a.only != name:
            continue
        box = Intro('DJ01')
        box.setup_intro(bits)
        data, lit = [pack(bits)], []
        for n in range(a.frames):
            f = box.frame(n)
            data.append(pack(f))
            lit.append(sum(map(sum, f)))
        open(os.path.join(a.out, name + '.frames'), 'wb').write(b''.join(data))
        print('%s: %d frames, lit pixels %s ... %s' % (name, a.frames, lit[:6], lit[-3:]))


if __name__ == '__main__':
    main()
