#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""BA05: write vendor/elekloader/examples/arp-midi-dn2/mod.json, arp-midi-dn2 0.1 (every site checked against the
stock image): the arpeggiator on MIDI-machine tracks, notes/T2.2-arp-midi-v2.md's option 2 (ami.s;
notes/BA05-arp-midi.md).

    venv/bin/python scripts/ba05_make_midi.py [--list]

Sites (11): the ARP key, the ARP menu's auto-close and the ARP LED (bytes); the voice allocator's two calls in the
voice interrupt and FUN_40027c18's two calls at the live note-on and note-off (jsr); the sequencer's and the pending
step's MIDI splits, each as a jsr in place of the kit's load and a bytes site for the rest (the stock clr.l and bne.s
kept). Checked: the stock bytes of each site; the code the caves rely on (the interrupt's frame slots, its type-3
list append, the allocator's arguments, the MIDI event allocator and the MIDI task's gates, as
scripts/t22_v2_checks.py reads them); that nothing branches into the middle of a split site; and that ami.s's two
tables are the ones the stock arp-length, trig-length and speed tables give.
"""
import json
import os
import re
import struct
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMAGE = os.path.join(ROOT, 'ele3_out', '02_type3.unpacked.bin')
MODDIR = os.path.join(ROOT, 'vendor', 'elekloader', 'examples', 'arp-midi-dn2')
OUT = os.path.join(MODDIR, 'mod.json')
LOAD = 0x40000400

img = open(IMAGE, 'rb').read()


def rd(a, n):
    return img[a - LOAD:a - LOAD + n]


def u32(a):
    return struct.unpack('>I', rd(a, 4))[0]


def s32(a):
    return struct.unpack('>i', rd(a, 4))[0]


def listing(lo, hi):
    with tempfile.NamedTemporaryFile(suffix='.bin') as tf:
        tf.write(img[lo - LOAD:hi - LOAD])
        tf.flush()
        out = subprocess.run(['m68k-elf-objdump', '-D', '-b', 'binary', '-m', 'm68k:cfv4e',
                              '--adjust-vma=0x%x' % lo, tf.name], capture_output=True, text=True, check=True).stdout
    return [ln for ln in out.splitlines() if re.match(r'^ *[0-9a-f]+:\t', ln)]


def need(addr, hexbytes, what):
    hexbytes = hexbytes.replace(' ', '')
    got = rd(addr, len(hexbytes) // 2).hex()
    if got != hexbytes:
        raise SystemExit('0x%08x: %s, expected %s (%s)' % (addr, got, hexbytes, what))


sites = []


def site(addr, stock, op, what, **kw):
    need(addr, stock, what)
    s = {'addr': '0x%08x' % addr, 'stock': stock, 'op': op}
    s.update(kw)
    s['note'] = what
    sites.append(s)


def no_branch_into(lo, hi, func_lo, func_hi, what):
    """no branch from outside [lo, hi) lands strictly inside it (lo itself, the hook, is fine)"""
    for ln in listing(func_lo, func_hi):
        m = re.match(r'^ *([0-9a-f]+):\t.*\t(b[a-z]+|jmp|jsr)[a-z.]* +0x([0-9a-f]+)$', ln)
        if m:
            frm, to = int(m.group(1), 16), int(m.group(3), 16)
            if lo < to < hi and not lo <= frm < hi:
                raise SystemExit('a branch into %s: %s' % (what, ln))


# ---- the UI
need(0x4005f9b6, '4eb940031274 4fef000c 4a00', 'the ARP key (key code 0x33): jsr isMidiTrack; lea 12(sp); tst.b d0')
site(0x4005f9c2, '6600f58e', 'bytes', 'the ARP key: bne.w 0x4005ef52 (a MIDI track returns) -> nop nop',
     new='4e714e71', kind='code')
need(0x400187ea, '4eb940031274 4fef0014 4a00', 'ArpSetupMenuView\'s lambda FUN_400187b4: jsr isMidiTrack; tst.b d0')
need(0x400192c2, '203c400187b4', 'ArpSetupMenuView\'s constructor: move.l #FUN_400187b4 (the lambda, its only use)')
site(0x400187f6, '670e', 'bytes', 'the ARP menu\'s lambda: beq.s (not MIDI: stays open) -> bra.s (always)',
     new='600e', kind='code')
need(0x40060dde, '4eb940031274 4fef000c 4a00', 'MainScreenView::vslot18, the ARP LED: jsr isMidiTrack; tst.b d0')
site(0x40060dea, '6620', 'bytes', 'the ARP LED: bne.s (a MIDI track: off) -> nop', new='4e71', kind='code')

# ---- the voice interrupt (FUN_40025e36)
need(0x4002557c, '23c0400002fc 7005 13c0fc04c07f', 'vector 191 = the voice interrupt')
need(0x40025f06, '2d48ffd0', 'the interrupt: fp-48 = the pass\'s end')
need(0x4002611c, '9bcd 2d40ffd4', 'the interrupt: a5 (the MIDI list\'s tail) = 0; fp-44 = the time bucket')
need(0x40026172, '2d680008ffdc', 'the interrupt: fp-36 = the event')
need(0x4002629e, '246effd4 236a00040030', 'type 3: the MIDI event\'s [0xc] = the bucket\'s time')
need(0x400262ca, '4aaeff4c 6606 2d40ff4c 6054 2b40005c', 'type 3: the append, head fp-180, link +0x5c')
need(0x4002632a, '2a40', 'type 3: movea.l d0,a5 (the new tail)')
need(0x40026f5a, '4879445ff870 4eb940001896', 'the pass\'s end: the MIDI list posted to the MIDI task')
need(0x400269b4, '2f2eff64', 'the allocator\'s argument 9, the sound (fp-156)')
site(0x400269e6, '4eb94002a6a2', 'jsr', 'the note-on loop: jsr 0x4002a6a2 (the voice allocator) -> jsr ami_sink '
     '(a MIDI track: a MIDI note event, no voice)', target='ami_sink')
site(0x40026d86, '4eb94002a6a2', 'jsr', 'the note-off loop: jsr 0x4002a6a2 -> jsr ami_off (a MIDI track owns no voice)',
     target='ami_off')
need(0x40026d90, '203980005348 4a81', 'after the note-off call: d0 reloaded, d1 tested')
need(0x40027c18, '700f 222f0004 b081 6510 71798000537c e2a0 7201 c081 b380 4e75 4280 4e75',
     'FUN_40027c18: track < 16 and its bit of 0x8000537c clear -> 1 ("not MIDI")')

# ---- the MIDI task's side
need(0x4012a6e8, '40c0 46fc2700 20794460f4b8 43e8005c 23d14460f4b8', 'FUN_4012a6e8, the MIDI event allocator '
     '(no empty check)')
need(0x4012b2cc, '202a0010 028000000400 6612 202a0010 028000000080 66000130', 'the MIDI task: [4] & 0x400 LFO trig, '
     '[4] & 0x80 the note gate')
need(0x4012af2c, '222a0034 b681 666a', 'the MIDI task: [0xd] == 1 -> the mute checks')
need(0x4012afc4, 'b0aa000c 6600065a', 'the MIDI task: [3] == 1 note on')

# ---- the live keys
need(0x40121ba2, '2c0e 0686ffffffc0', 'FUN_40121b0a: d6 = a6 - 0x40 (the event to FUN_4013801c)')
need(0x40121bda, '2f06 4eb94013801c', 'FUN_40121b0a: not MIDI -> FUN_4013801c(d6)')
site(0x40121f2a, '4eb940027c18', 'jsr', 'the live note-on (FUN_40121db2): jsr FUN_40027c18 -> jsr ami_liveon (an arp-on '
     'MIDI track goes to the voice interrupt)', target='ami_liveon')
site(0x40121bcc, '4eb940027c18', 'jsr', 'the live note-off (FUN_40121b0a): jsr FUN_40027c18 -> jsr ami_liveoff (a MIDI '
     'track\'s release to both engines)', target='ami_liveoff')

# ---- the sequencer (FUN_400d99de) and the pending step (FUN_400d907a)
need(0x400da046, '6656', 'the step: bne.s 0x400da09e (the MIDI builder), kept')
site(0x400da036, '207942c5b9ac', 'jsr', 'the step: movea.l 0x42c5b9ac,a0 -> jsr ami_seq (d0.b: the MIDI builder or the '
     'synth path; the stock clr.l 96(sp) after it)', target='ami_seq')
need(0x400da03c, '42af0060', 'the step: clr.l 96(sp), kept')
site(0x400da040, '71685cda0500', 'bytes', 'the step: mvs.w (0x5cda,a0),d0; btst d2,d0 -> tst.b d0; nop; nop',
     new='4a004e714e71', kind='code')
need(0x400da048, '4eb940138cb6', 'the synth path: the arp\'s step sync event')
need(0x400d91c8, '6626', 'the pending step: bne.s 0x400d91f0 (the MIDI builder), kept')
site(0x400d91bc, '207942c5b9ac', 'jsr', 'the pending step: movea.l 0x42c5b9ac,a0 -> jsr ami_pre (Z: the synth path)',
     target='ami_pre')
site(0x400d91c2, '73685cda0501', 'bytes', 'the pending step: mvs.w (0x5cda,a0),d1; btst d2,d1 -> movea.l 0x42c5b9ac,a0 '
     '(a0 live after the branch; movea leaves the flags)', new='207942c5b9ac', kind='code')
no_branch_into(0x400da036, 0x400da046, 0x400d99de, 0x400da300, 'the step\'s split')
no_branch_into(0x400d91bc, 0x400d91c8, 0x400d907a, 0x400d9300, 'the pending step\'s split')

# ---- ami.s's tables against the stock ones
arp = [s32(0x40288048 + 4 * i) for i in range(128)]
trig = [s32(0x401d8ce8 + 4 * i) for i in range(128)]
lut = [0x7f if v < 0 else next(k for k in range(127) if trig[k] >= v) for v in arp]
tab = [u32(0x401d5fe4 + 4 * i) for i in range(0xb40)]
periods = []
for b in range(23):
    idx = [k for k in range(1, 0xb40) if (tab[k] >> b) & 1]
    periods.append(idx[1] - idx[0])
period_len = [next(k for k in range(127) if trig[k] >= p * 45000) for p in periods]
src = open(os.path.join(MODDIR, 'ami.s')).read()


def table(label):
    body = src.split(label + ':', 1)[1]
    rows = []
    for ln in body.splitlines()[1:]:
        ln = ln.split('|')[0].strip()
        if not ln.startswith('.byte'):
            break
        rows += [int(x, 16) for x in ln[5:].split(',')]
    return rows


if table('ami_len')[:127] != lut[:127] or len(table('ami_len')) != 128:
    raise SystemExit('ami_len is not the stock tables\' lookup')
if table('ami_period') != period_len:
    raise SystemExit('ami_period is not the stock tables\' (%s)' % period_len)

mod = {
    'id': 'arp-midi',
    'version': '0.1',
    'title': 'The arpeggiator on MIDI tracks',
    'category': 'Examples',
    'license': 'GPL-2.0-or-later',
    'author': 'naturarum',
    'description': 'EXPERIMENT: emulator-tested only. The ARP key, menu and LED work on MIDI-machine tracks, and a MIDI '
                   'track whose arp is on plays through the stock arpeggiator: its trigs and live keys feed the arp, '
                   'and the arp\'s notes leave on the track\'s MIDI channel by the path and timing of a stock MIDI '
                   'trig (N.LEN as the nearest trig length not shorter, INF as one arp period). The trig\'s MIDI '
                   'p-locks are not sent while the arp is on. Arp off, the track is stock. With arp-modes, SHUF, RAND '
                   'and TM play on MIDI tracks too. Our own code, from a study of OS 1.12 (dn2-cfw '
                   'notes/T2.2-arp-midi-v2.md).',
    'device': 'digitone-mk2',
    'os': '1.12',
    'sources': ['ami.s'],
    'sites': [{k: v for k, v in s.items() if k != 'note'} for s in sites],
    'resources': {'names': ['arp-midi-ui', 'arp-midi-voice', 'arp-midi-live', 'arp-midi-seq']},
    'requires': ['core'],
}

if __name__ == '__main__':
    with open(OUT, 'w') as f:
        json.dump(mod, f, indent=1)
        f.write('\n')
    if '--list' in sys.argv:
        for s in sites:
            print('%s %-5s %-12s %s' % (s['addr'], s['op'], s.get('target', s.get('new', '')), s['note']))
    print('%d sites -> %s (arp-midi %s); tables as the stock ones' % (len(sites), os.path.relpath(OUT, ROOT),
                                                                    mod['version']))
