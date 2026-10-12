#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Self-test of the brick checker (task T1.7).

    vendor/digikit/.venv/bin/python tools/brickcheck/selftest.py [--jobs N] [--only NAME,...]
                                                                 [--quick]

Builds its test files in out/brickcheck/selftest/ from the pinned stock file
and runs tools/brickcheck/brickcheck on each, then compares the verdict and
each check's status with what is expected. Exit 0 only if every case comes
out as expected.

Required cases (full run, emulated boot included, about 15 minutes each):
  a-stock   the stock file                                         -> accept
  b-repack  elektron-firmware-tool -r                              -> accept
  c-l0      elektron-firmware-tool -c 3 <stock main OS> -l 0       -> accept
  d-l3      the same at the default level 3                        -> refuse, check 5 (0x40200000)

Negative controls (no emulated boot; each must FAIL the named check):
  n1-msgsum     one data message's payload byte changed            -> 1
  n2-sec2ver    section 2 table version 0x0202, checksum and seal redone -> 2 and 9
  n3-trailer    one trailer bit flipped                            -> 4
  n4-stream     one bit of the main OS stream flipped, resealed    -> 5
  n5-panelword  main OS with the panel firmware version word changed, -l 0,
                checked with --intended-main of that image         -> 6 (5 passes)
  n6a..n6e      main OS with one new instruction, -l 0, --intended-main:
                jsr 0x401354d6 / jsr (d16,pc) to 0x401357ce / move.l #0x840200d8,d0 /
                lea 0xfc05c034,a0 / move.l #0xb0b0dada,0x48000000 /
                move.l #0x080200c7,d0 (a one-frame chip erase)  -> 7
  p1-btst       main OS with btst #6,d2; cmpi.b #0x20,d2 (look like EOQ frames
                as raw words), no device runs                 -> incomplete, 7 passes
  m1-string     main OS with the unreachable "Unsupported downgrade" message
                changed to "Unsupported DOWNGRADE", -l 0, --intended-main,
                full run                                       -> accept
  n7-big        main OS + 300,000 random bytes, -l 0               -> 3
  n8-intended   stock, but --intended-main of n5's image           -> 5

--quick runs every case with --no-emu-boot (cases a-d then expect
"incomplete" with check 8 SKIP). Nothing here talks to hardware.
"""
import argparse
import concurrent.futures as cf
import json
import os
import random
import struct
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
import bc_syx  # noqa: E402

STOCK = os.path.join(ROOT, 'elektron-firmware', 'Digitone_II_OS1_12.syx')
TOOL = os.path.join(ROOT, 'vendor', 'elektron-firmware-tool', 'elektron-firmware-tool')
BRICK = os.path.join(HERE, 'brickcheck')
OUT = os.path.join(ROOT, 'out', 'brickcheck', 'selftest')
MAIN_BASE = 0x40000400


def encode_payload(data):
    """101 bytes -> 116 bytes of 8-in-7."""
    out = bytearray()
    for s in range(0, len(data), 7):
        g = data[s:s + 7]
        hi = 0
        for n, b in enumerate(g):
            if b & 0x80:
                hi |= 1 << (6 - n)
        out.append(hi)
        out.extend(b & 0x7F for b in g)
    return bytes(out)


def encode(stock, stream):
    """A decoded stream -> .syx bytes, framing copied from stock with the
    count set, counters from 242, every message checksum computed."""
    n = (len(stream) + bc_syx.CHUNK - 1) // bc_syx.CHUNK
    stream = stream + bytes(n * bc_syx.CHUNK - len(stream))

    def framing(f):
        f = bytearray(f)
        f[12], f[13], f[14] = (n >> 14) & 0x7F, (n >> 7) & 0x7F, n & 0x7F
        return bytes(f)
    out = bytearray(framing(stock.framing[0]))
    tmpl = stock.data[0]
    for i in range(n):
        c = bc_syx.FIRST_COUNTER + i
        m = bytearray(tmpl[:7]) + bytes([(c >> 14) & 0x7F, (c >> 7) & 0x7F, c & 0x7F])
        m += encode_payload(stream[i * bc_syx.CHUNK:(i + 1) * bc_syx.CHUNK]) + b'\0\xf7'
        m[126] = bc_syx.message_checksum(bytes(m), stock.k)
        out += m
    out += framing(stock.framing[1])
    return bytes(out)


def restream(cont):
    return struct.pack('>II', len(cont), bc_syx.content_checksum(cont)) + cont


def reseal(cont, key):
    cont = bytearray(cont)
    cont[-32:] = bc_syx.seal(key, bytes(cont))
    return bytes(cont)


def tool(args, log):
    p = subprocess.run([TOOL] + args, capture_output=True, text=True)
    log.write('$ elektron-firmware-tool %s\n%s%s\n' % (' '.join(args), p.stdout, p.stderr))
    if p.returncode:
        raise SystemExit('elektron-firmware-tool failed: %s' % p.stderr)


def build(only):
    """-> {name: (syx path, extra args, expectation)}"""
    sys.path.insert(0, os.path.join(ROOT, 'vendor', 'digikit'))
    from dt2 import elz
    os.makedirs(OUT, exist_ok=True)
    log = open(os.path.join(OUT, 'build.log'), 'w')
    stock = bc_syx.Syx.load(STOCK)
    boot = elz.depack_section(stock.stored[2])
    key = bc_syx.seal_key(boot)
    main = elz.depack_section(stock.stored[3])
    main_path = os.path.join(OUT, 'stock-main.bin')
    open(main_path, 'wb').write(main)
    P = lambda n: os.path.join(OUT, n)          # noqa: E731
    want = lambda name: not only or name in only  # noqa: E731
    cases = {}
    accept = {'verdict': 'accept', 'pass': list(range(1, 13))}

    if want('a-stock'):
        cases['a-stock'] = (STOCK, [], accept)
    if want('b-repack'):
        tool(['-i', STOCK, '-r', '-o', P('b-repack.syx')], log)
        cases['b-repack'] = (P('b-repack.syx'), [], accept)
    if want('c-l0'):
        tool(['-i', STOCK, '-c', '3', main_path, '-l', '0', '-o', P('c-l0.syx')], log)
        cases['c-l0'] = (P('c-l0.syx'), [], accept)
    if want('d-l3'):
        tool(['-i', STOCK, '-c', '3', main_path, '-o', P('d-l3.syx')], log)
        cases['d-l3'] = (P('d-l3.syx'), [], {'verdict': 'refuse', 'fail': [5],
                                             'pass': [1, 2, 3, 4, 6, 7, 8, 9, 10],
                                             'evidence': {5: 'in-place margin at 0x40200000'}})
    quick = ['--no-emu-boot']
    fast = ['--no-emu-boot', '--no-device']

    if want('n1-msgsum'):
        raw = bytearray(stock.raw)
        off = 16 + 128 * 500 + 40               # data message 500, inside the payload
        raw[off] ^= 0x01
        open(P('n1-msgsum.syx'), 'wb').write(raw)
        cases['n1-msgsum'] = (P('n1-msgsum.syx'), fast, {'verdict': 'refuse', 'fail': [1]})
    if want('n2-sec2ver'):
        c = bytearray(stock.container)
        i = [t[0] for t in stock.table].index(2)
        struct.pack_into('>I', c, bc_syx.TABLE_OFF + 16 * i + 12, 0x02020000)
        c = reseal(bytes(c), key)
        open(P('n2-sec2ver.syx'), 'wb').write(encode(stock, restream(c)))
        cases['n2-sec2ver'] = (P('n2-sec2ver.syx'), quick, {'verdict': 'refuse', 'fail': [2, 9],
                                                           'pass': [1, 3, 4, 5]})
    if want('n3-trailer'):
        c = bytearray(stock.container)
        c[-1] ^= 1
        s = struct.pack('>II', len(c), stock.checksum) + bytes(c)
        open(P('n3-trailer.syx'), 'wb').write(encode(stock, s))
        cases['n3-trailer'] = (P('n3-trailer.syx'), quick, {'verdict': 'refuse', 'fail': [4]})
    if want('n4-stream'):
        c = bytearray(stock.container)
        off, ln = stock.section_of(3)[1:3]
        c[off + 8 + ln // 2] ^= 0x10            # acceptoracle's third control
        c = reseal(bytes(c), key)
        open(P('n4-stream.syx'), 'wb').write(encode(stock, restream(c)))
        cases['n4-stream'] = (P('n4-stream.syx'), quick, {'verdict': 'refuse', 'fail': [5],
                                                         'pass': [1, 2, 3, 4]})

    def patched(name, edits, extra=b''):
        img = bytearray(main)
        for addr, b in edits:
            img[addr - MAIN_BASE:addr - MAIN_BASE + len(b)] = b
        img += extra
        mp = P(name + '.main.bin')
        open(mp, 'wb').write(img)
        tool(['-i', STOCK, '-c', '3', mp, '-l', '0', '-o', P(name + '.syx')], log)
        return P(name + '.syx'), mp

    n5 = None
    if want('n5-panelword') or want('n8-intended'):
        word = main[0x402F7D44 - MAIN_BASE:0x402F7D48 - MAIN_BASE]
        nw = struct.pack('>I', struct.unpack('>I', word)[0] ^ 0x01)
        n5 = patched('n5-panelword', [(0x402F7D44, nw)])
        log.write('n5: version word %s -> %s\n' % (word.hex(), nw.hex()))
    if want('n5-panelword'):
        cases['n5-panelword'] = (n5[0], quick + ['--intended-main', n5[1]],
                                 {'verdict': 'refuse', 'fail': [6], 'pass': [1, 2, 3, 4, 5]})
    site = 0x40100100
    six = {
        'n6a-jsrabs': [(site, bytes.fromhex('4eb9401354d6'))],
        'n6b-jsrpc': [(0x40130000, b'\x4e\xba' + struct.pack('>h', 0x401357CE - 0x40130002))],
        'n6c-pushr': [(site, bytes.fromhex('203c840200d8'))],
        'n6d-dspi0': [(site, bytes.fromhex('41f9fc05c034'))],
        'n6e-magic': [(site, bytes.fromhex('23fcb0b0dada48000000'))],
        'n6f-ce': [(site, bytes.fromhex('203c080200c7'))],
    }
    for name, edits in six.items():
        if want(name):
            sp, mp = patched(name, edits)
            cases[name] = (sp, fast + ['--intended-main', mp], {'verdict': 'refuse', 'fail': [7],
                                                              'pass': [1, 2, 3, 6]})
    if want('p1-btst'):
        # btst #6,d2; cmpi.b #0x20,d2: ordinary code that looks like EOQ frames 0x0802_0006
        # and 0x0C02_0020; check 7 must not refuse it
        sp, mp = patched('p1-btst', [(site, bytes.fromhex('080200060c020020'))])
        cases['p1-btst'] = (sp, fast + ['--intended-main', mp],
                            {'verdict': 'incomplete', 'pass': [1, 2, 3, 6, 7, 10]})
    if want('m1-string'):
        # a harmless modification: the unreachable "Unsupported downgrade" message
        # (T1.2) becomes "Unsupported DOWNGRADE"; full run, must be accepted
        sp, mp = patched('m1-string', [(0x40232A1A, b'DOWNGRADE')])
        cases['m1-string'] = (sp, ['--intended-main', mp], accept)
    if want('n7-big'):
        rnd = random.Random(7).randbytes(300_000)
        sp, mp = patched('n7-big', [], rnd)
        cases['n7-big'] = (sp, fast + ['--intended-main', mp], {'verdict': 'refuse', 'fail': [3]})
    if want('n8-intended'):
        cases['n8-intended'] = (STOCK, fast + ['--intended-main', n5[1]],
                                {'verdict': 'refuse', 'fail': [5]})

    # ---- DSP (section 7) cases: check 11 (T1.7b gap 2) ----
    sys.path.insert(0, os.path.join(ROOT, 'scripts'))
    import sharc_ldr_parse as _P  # noqa: E402
    sec7 = elz.depack_section(stock.stored[7])

    def _xor0(hdr):
        hdr[2] = 0
        x = 0
        for by in hdr:
            x ^= by
        hdr[2] = x

    def _owner_off(blocks, core):
        tgt = core + 0x28000000 if core < 0x20000000 else core
        for b in blocks:
            if not (b['byte_count'] and not ({'FIRST', 'FINAL', 'INIT', 'IGNORE'} & set(b['flags']))) \
                    or b['fill']:
                continue
            if b['target_address'] <= tgt < b['target_address'] + b['byte_count']:
                return b['payload_offset'] + (tgt - b['target_address'])
        raise SystemExit('no block owns %#x' % core)

    def _dsp_case(name, edit, flags, expect, intended=True):
        stream = bytearray(sec7)
        blocks, _e, _s = _P.parse(bytes(stream))
        if edit:
            edit(stream, blocks)
        dsp_path = P(name + '.dsp.bin')
        open(dsp_path, 'wb').write(bytes(stream))
        tool(['-i', STOCK, '-c', '7', dsp_path, '-l', '0', '-o', P(name + '.syx')], log)
        extra = list(flags) + (['--intended-dsp', dsp_path] if intended else [])
        cases[name] = (P(name + '.syx'), extra, expect)

    no_emu = ['--no-emu-boot', '--no-device']
    if want('dsp-stock-noint'):            # changed DSP, no --intended-dsp -> incomplete (11 SKIP)
        _dsp_case('dsp-stock-noint', None, no_emu + ['--no-dsp-sim'],
                  {'verdict': 'incomplete', 'skip': [11], 'pass': [1, 2, 3]}, intended=False)
    if want('dsp-oversize'):               # depacked boot stream over 1 MiB - 1

        def _oversize(stream, blocks):
            bcode = [b for b in blocks if b['index'] == 83][0]['block_code'] & 0xF
            fin = [b for b in blocks if 'FINAL' in b['flags']][-1]
            first1 = [b for b in blocks if 'FIRST' in b['flags']][-1]
            n = 0x40000
            hdr = bytearray(struct.pack('<IIII', (0xAD << 24) | bcode, 0x80700000, n, 0))
            _xor0(hdr)
            ins = bytes(hdr) + b'\x00' * n
            stream[fin['offset']:fin['offset']] = ins
            fo = first1['offset']
            fhdr = bytearray(stream[fo:fo + 16])
            struct.pack_into('<I', fhdr, 12, struct.unpack_from('<I', fhdr, 12)[0] + len(ins))
            _xor0(fhdr)
            stream[fo:fo + 16] = fhdr
        _dsp_case('dsp-oversize', _oversize, no_emu + ['--no-dsp-sim'],
                  {'verdict': 'refuse', 'fail': [11]})
    if want('dsp-cache'):                  # a load block into the I-cache range 0x39c000

        def _cache(stream, blocks):
            off = [b for b in blocks if b['index'] == 76][0]['offset']
            hdr = bytearray(stream[off:off + 16])
            struct.pack_into('<I', hdr, 4, 0x2839c000)
            _xor0(hdr)
            stream[off:off + 16] = hdr
        _dsp_case('dsp-cache', _cache, no_emu + ['--no-dsp-sim'], {'verdict': 'refuse', 'fail': [11]})
    if want('dsp-badshift'):               # the mis-encoded negative-immediate shift (selas) at SW 0x1c0091

        def _badshift(stream, blocks):
            o = _owner_off(blocks, 0x380122)
            stream[o:o + 6] = bytes.fromhex('3e02000012fd')
        _dsp_case('dsp-badshift', _badshift, no_emu + ['--no-dsp-sim'],
                  {'verdict': 'refuse', 'fail': [11]})
    if want('dsp-trap'):                   # a reachable handler instruction corrupted -> trap

        def _trap(stream, blocks):
            o = _owner_off(blocks, 0x393f72)       # block entry mark SW 0x1c9fb9
            stream[o:o + 6] = b'\xff\xff\xff\xff\xff\xff'
        _dsp_case('dsp-trap', _trap, no_emu, {'verdict': 'refuse', 'fail': [11]})
    if want('dsp-nan'):                    # NaN reverb coefficients -> NaN output

        def _nan(stream, blocks):
            o = _owner_off(blocks, 0x2cddd8)       # reverb constant table
            for kk in range(64):
                struct.pack_into('<I', stream, o + 4 * kk, 0x7fc00000)
        _dsp_case('dsp-nan', _nan, no_emu, {'verdict': 'refuse', 'fail': [11]})

    log.close()
    return cases


def run_case(name, syx, extra, quick):
    out = os.path.join(OUT, 'runs', name)
    args = [BRICK, syx, '--stock', STOCK, '--out', out] + extra
    if quick and '--no-emu-boot' not in extra:
        args.append('--no-emu-boot')
    t = time.time()
    lp = os.path.join(OUT, 'runs', name + '.log')
    os.makedirs(os.path.dirname(lp), exist_ok=True)
    with open(lp, 'w') as fh:
        p = subprocess.run(args, stdout=fh, stderr=subprocess.STDOUT)
    rep = json.load(open(os.path.join(out, 'report.json')))
    return {'name': name, 'exit': p.returncode, 'seconds': round(time.time() - t, 1),
            'report': os.path.join(out, 'report.md'), 'log': lp, 'verdict': rep['verdict'],
            'status': {int(k): v['status'] for k, v in rep['checks'].items()},
            'evidence': {int(k): [e['text'] for e in v['evidence'] if e['status'] == 'FAIL']
                         for k, v in rep['checks'].items()}}


def judge(r, exp, quick):
    exp = dict(exp)
    problems = []
    # checks 8 and 12 are the emulator checks: in --quick (no emulated boot)
    # they SKIP, so an "accept" case becomes "incomplete" with 8 and 12 skipped.
    EMU = (8, 12)
    if quick and exp['verdict'] == 'accept':
        exp = {'verdict': 'incomplete', 'pass': [i for i in exp['pass'] if i not in EMU],
               'skip': list(EMU)}
    if quick:
        exp['pass'] = [i for i in exp.get('pass', []) if i not in EMU]
    if r['verdict'] != exp['verdict']:
        problems.append('verdict %s, expected %s' % (r['verdict'], exp['verdict']))
    want_exit = {'accept': 0, 'refuse': 1, 'incomplete': 3}[exp['verdict']]
    if r['exit'] != want_exit:
        problems.append('exit %d, expected %d' % (r['exit'], want_exit))
    for k in ('pass', 'fail', 'skip'):
        for i in exp.get(k, []):
            if r['status'].get(i) != k.upper():
                problems.append('check %d is %s, expected %s' % (i, r['status'].get(i), k.upper()))
    for i, text in exp.get('evidence', {}).items():
        if not any(text in e for e in r['evidence'].get(i, [])):
            problems.append('check %d has no FAIL line containing %r' % (i, text))
    return problems


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--jobs', type=int, default=4)
    ap.add_argument('--only', help='comma list of case names')
    ap.add_argument('--quick', action='store_true', help='no emulated boot in any case')
    a = ap.parse_args(argv)
    only = set(a.only.split(',')) if a.only else None
    cases = build(only)
    print('selftest: %d cases, %d at a time; files and runs in %s' % (len(cases), a.jobs, OUT), flush=True)
    results = {}
    with cf.ThreadPoolExecutor(max_workers=a.jobs) as ex:
        futs = {ex.submit(run_case, n, s, e, a.quick): n for n, (s, e, _x) in cases.items()}
        for f in cf.as_completed(futs):
            n = futs[f]
            try:
                r = f.result()
            except Exception as e:
                r = {'name': n, 'error': '%s: %s' % (type(e).__name__, e), 'status': {}, 'verdict': None,
                     'exit': None, 'evidence': {}}
            r['expected'] = cases[n][2]
            r['problems'] = [r['error']] if 'error' in r else judge(r, cases[n][2], a.quick)
            results[n] = r
            print('  %-14s %-10s exit %s  %s  %s s  %s' % (
                n, r['verdict'], r['exit'], ' '.join('%d:%s' % (i, r['status'][i][0])
                                                     for i in sorted(r['status'])),
                r.get('seconds'), 'as expected' if not r['problems'] else 'UNEXPECTED: ' +
                '; '.join(r['problems'])), flush=True)
    ok = all(not r['problems'] for r in results.values())
    # merge into selftest.json by case name, so a run with --only keeps the others
    path = os.path.join(OUT, 'selftest.json')
    try:
        allres = json.load(open(path)).get('results', {})
    except (OSError, ValueError):
        allres = {}
    stamp = time.strftime('%Y-%m-%d %H:%M:%S')
    for n, r in results.items():
        r['quick'] = a.quick
        r['run_at'] = stamp
        allres[n] = r
    with open(path, 'w') as fh:
        json.dump({'ok': all(not r['problems'] for r in allres.values()), 'results': allres},
                  fh, indent=1, default=str)
    print('selftest: %s for this run (%d cases); %s holds %d cases'
          % ('PASS' if ok else 'FAIL', len(results), path, len(allres)))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
