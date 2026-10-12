#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Build red-team candidates for the brick checker (task T1.7 red team).

    vendor/digikit/.venv/bin/python scripts/t1.7_redteam_build.py

One hazard per file, written to out/brickcheck-redteam/. Each entry below is
(name, how it is built, the extra brickcheck args it needs, and the verdict we
expect). Nothing here talks to hardware and nothing is flashed; these are
deliberately dangerous Digitone II OS 1.12 images used only to confirm the
checker refuses them (or, for the DANGER cases, to see whether it wrongly
accepts them).

Reuses the independent reader bc_syx and the encode/reseal helpers from the
checker's own selftest, so the candidates are built the same way a modder
would and are valid transport-wise unless the hazard is in the transport.
"""
import json
import os
import struct
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, 'tools', 'brickcheck'))
sys.path.insert(0, os.path.join(ROOT, 'vendor', 'digikit'))
import bc_syx                                   # noqa: E402
import selftest as st                           # noqa: E402 (encode/restream/reseal helpers)
from dt2 import elz                             # noqa: E402

STOCK = os.path.join(ROOT, 'elektron-firmware', 'Digitone_II_OS1_12.syx')
TOOL = os.path.join(ROOT, 'vendor', 'elektron-firmware-tool', 'elektron-firmware-tool')
OUT = os.path.join(ROOT, 'out', 'brickcheck-redteam')
MAIN_BASE = 0x40000400
os.makedirs(OUT, exist_ok=True)
P = lambda n: os.path.join(OUT, n)              # noqa: E731
LOG = open(os.path.join(OUT, 'build.log'), 'w')


def tool(args):
    p = subprocess.run([TOOL] + args, capture_output=True, text=True)
    LOG.write('$ elektron-firmware-tool %s\n%s%s\n' % (' '.join(args), p.stdout, p.stderr))
    if p.returncode:
        raise SystemExit('elektron-firmware-tool failed:\n%s' % p.stderr)


stock = bc_syx.Syx.load(STOCK)
boot = elz.depack_section(stock.stored[2])
key = bc_syx.seal_key(boot)
main = bytearray(elz.depack_section(stock.stored[3]))
sec7 = bytearray(elz.depack_section(stock.stored[7]))
open(P('stock-main.bin'), 'wb').write(main)
manifest = {}


def record(name, syx, extra, expect, note, intended=None):
    manifest[name] = {'syx': os.path.abspath(syx), 'extra': extra, 'expect': expect,
                      'note': note, 'intended_main': os.path.abspath(intended) if intended else None}
    LOG.write('[%s] %s  extra=%s  expect=%s\n' % (name, note, extra, expect))


def container_edit(name, edit_fn, expect, note):
    """Edit the stock container bytes, reseal, recompute the preamble checksum,
    and re-encode to a valid .syx. `edit_fn(cont_bytearray)` mutates in place."""
    c = bytearray(stock.container)
    edit_fn(c)
    c[-32:] = bc_syx.seal(key, bytes(c))                 # valid HMAC seal over the new container
    stream = struct.pack('>II', len(c), bc_syx.content_checksum(bytes(c))) + bytes(c)
    open(P(name + '.syx'), 'wb').write(st.encode(stock, stream))
    record(name, P(name + '.syx'), ['--no-device', '--no-emu-boot'], expect, note)


def main_patch(name, edits, extra_flags, expect, note, intended_is_patched=True, full=False):
    """Depack stock section 3, apply (addr, bytes) edits, repack at level 0,
    and point --intended-main at the patched image (so check 5 passes and the
    hazard is isolated to the check that should catch it)."""
    img = bytearray(main)
    for addr, b in edits:
        img[addr - MAIN_BASE:addr - MAIN_BASE + len(b)] = b
    mp = P(name + '.main.bin')
    open(mp, 'wb').write(img)
    tool(['-i', STOCK, '-c', '3', mp, '-l', '0', '-o', P(name + '.syx')])
    intended = mp if intended_is_patched else None
    extra = list(extra_flags) + (['--intended-main', mp] if intended_is_patched else [])
    record(name, P(name + '.syx'), extra, expect, note, intended=intended)


# ---------------------------------------------------------------------------
# 1. section 2 body byte flip (not the table entry) -> check 2
# ---------------------------------------------------------------------------
def flip_section(c, sid, rel):
    off, ln = stock.section_of(sid)[1:3]
    c[off + rel] ^= 0x01

container_edit('s2-bodyflip', lambda c: flip_section(c, 2, 0x200),
               {'verdict': 'refuse', 'fail': [2], 'pass': [1, 3, 4]},
               'one body byte of section 2 (the bootstrap) flipped, resealed; version unchanged')

# ---------------------------------------------------------------------------
# 2. section 4 body change -> check 2  (the in-place loader; panel-fail boots)
# ---------------------------------------------------------------------------
container_edit('s4-bodyflip', lambda c: flip_section(c, 4, 0x400),
               {'verdict': 'refuse', 'fail': [2], 'pass': [1, 3, 4]},
               'one body byte of section 4 (the factory/in-place loader) flipped, resealed')

# ---------------------------------------------------------------------------
# 3. section 8 change -> check 2  (external accessory firmware)
# ---------------------------------------------------------------------------
container_edit('s8-bodyflip', lambda c: flip_section(c, 8, 0x400),
               {'verdict': 'refuse', 'fail': [2], 'pass': [1, 3, 4]},
               'one body byte of section 8 (the OUTBOX accessory firmware) flipped, resealed')

# ---------------------------------------------------------------------------
# 4. broken content checksum, seal still valid -> check 4 (independent csum)
# ---------------------------------------------------------------------------
def break_checksum(name):
    c = bytearray(stock.container)
    c[-32:] = bc_syx.seal(key, bytes(c))                 # valid seal
    good = bc_syx.content_checksum(bytes(c))
    stream = struct.pack('>II', len(c), (good ^ 0x01) & 0xFFFFFFFF) + bytes(c)
    open(P(name + '.syx'), 'wb').write(st.encode(stock, stream))
    record(name, P(name + '.syx'), ['--no-emu-boot'],
           {'verdict': 'refuse', 'fail': [4], 'pass': [1, 2, 3]},
           'preamble content-checksum word wrong by 1; container and seal valid')

break_checksum('cksum-broken')

# ---------------------------------------------------------------------------
# 5. broken seal, content checksum still correct -> check 4 (independent HMAC)
# ---------------------------------------------------------------------------
def break_seal(name):
    c = bytearray(stock.container)
    c[-1] ^= 0x01                                        # corrupt one seal byte
    good = bc_syx.content_checksum(bytes(c))             # checksum over the corrupted container
    stream = struct.pack('>II', len(c), good) + bytes(c)  # preamble made to match -> csum PASS
    open(P(name + '.syx'), 'wb').write(st.encode(stock, stream))
    record(name, P(name + '.syx'), ['--no-emu-boot'],
           {'verdict': 'refuse', 'fail': [4], 'pass': [1, 2, 3]},
           'seal trailer byte flipped; preamble checksum recomputed so only the HMAC is wrong')

break_seal('seal-broken')

# ---------------------------------------------------------------------------
# 6. panel MCU blob BODY changed (not the version word) -> check 6
# ---------------------------------------------------------------------------
main_patch('panel-blobbody', [(0x402F8000, b'\xAA')], ['--no-device', '--no-emu-boot'],
           {'verdict': 'refuse', 'fail': [6], 'pass': [1, 2, 3, 7]},
           'one byte of the panel MCU firmware blob body changed (0x402f8000); a corrupt blob '
           'pushed on first boot can brick the panel and kill FUNC recovery (T1.5 M2)')

# ---------------------------------------------------------------------------
# 7. panel MCU updater FUN_400dbbd8 changed -> check 6
# ---------------------------------------------------------------------------
main_patch('updater-body', [(0x400DBC00, b'\x4E\x71')], ['--no-device', '--no-emu-boot'],
           {'verdict': 'refuse', 'fail': [6], 'pass': [1, 2, 3, 7]},
           'a nop planted inside the panel MCU updater FUN_400dbbd8 (0x400dbc00)')

# ---------------------------------------------------------------------------
# 8. main OS that crashes at boot: ILLEGAL at the reset entry -> check 8 only
#    Built twice: the fast variant shows the static+device checks do NOT catch
#    it (verdict incomplete); the full variant confirms check 8 does.
# ---------------------------------------------------------------------------
main_patch('reset-illegal-fast', [(0x400004E8, b'\x4A\xFC\x4A\xFC')],
           ['--no-device', '--no-emu-boot'],
           {'verdict': 'incomplete', 'pass': [1, 2, 3, 6, 7, 10]},
           'ILLEGAL (0x4afc) at the reset entry 0x400004e8; fast run cannot see the boot crash')
main_patch('reset-illegal', [(0x400004E8, b'\x4A\xFC\x4A\xFC')], [],
           {'verdict': 'refuse', 'fail': [8], 'pass': [1, 2, 3, 4, 5, 6, 7, 9, 10]},
           'ILLEGAL (0x4afc) at the reset entry 0x400004e8; full run, check 8 must catch it')

# ---------------------------------------------------------------------------
# DANGER A: neuter the in-OS upgrade validator FUN_400dbdb0 (always return 1).
#   Not in any protected range (open problem #1). Expected to PASS every check.
# ---------------------------------------------------------------------------
main_patch('danger-validator', [(0x400DBDB0, b'\x70\x01\x4E\x75')], [],
           {'verdict': 'accept', 'pass': list(range(1, 11))},
           'DANGER: in-OS upgrade validator 0x400dbdb0 patched to `moveq #1,d0; rts` so it '
           'accepts any future upgrade; full run, expected to pass all ten checks')

# ---------------------------------------------------------------------------
# DANGER B: corrupt-but-depackable DSP (section 7). Section 3 is stock, so
#   intended-main = stock. The checker only requires section 7 to depack and
#   never runs the SHARC, so this is expected to PASS.
# ---------------------------------------------------------------------------
def danger_dsp(name):
    d = bytearray(sec7)
    d[0x1000] ^= 0xFF                                    # flip a byte of the DSP program
    dp = P(name + '.dsp.bin')
    open(dp, 'wb').write(d)
    tool(['-i', STOCK, '-c', '7', dp, '-l', '0', '-o', P(name + '.syx')])
    record(name, P(name + '.syx'), [],
           {'verdict': 'accept', 'pass': list(range(1, 11))},
           'DANGER: section 7 (SHARC/DSP) altered by one byte; main OS stock. Checker only '
           'requires it to depack and never runs the DSP, so expected to pass', intended=None)

danger_dsp('danger-dsp')

LOG.close()
with open(os.path.join(OUT, 'manifest.json'), 'w') as fh:
    json.dump(manifest, fh, indent=1)
print('built %d candidates in %s' % (len(manifest), OUT))
for n, m in manifest.items():
    print('  %-20s %-11s %s' % (n, m['expect']['verdict'], m['note'][:70]))
