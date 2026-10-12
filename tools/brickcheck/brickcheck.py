#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""brickcheck: refuse a candidate Digitone II OS 1.12 .syx unless every safety check passes.

    brickcheck <candidate.syx> [--stock elektron-firmware/Digitone_II_OS1_12.syx] [--intended-main <main_os.bin>]
               [--out DIR] [--no-emu-boot] [--no-device]

Each check prints PASS, FAIL or SKIP with its evidence. The verdict is
"accept" only if all twelve checks PASS, "refuse" if any FAILs, "incomplete" if
none FAILs but any is SKIPped: a SKIP never counts as a pass. Exit status:
0 accept, 1 refuse, 3 incomplete, 2 usage error. Reports: report.md and
report.json in out/brickcheck/<candidate name>/ (or --out).

See tools/brickcheck/README.md and notes/T1.7-brickcheck.md.
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import struct
import sys
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)

import bc_static  # noqa: E402
import bc_syx  # noqa: E402
import bc_dsp  # noqa: E402
import bc_upgrade  # noqa: E402

STOCK_SHA = '7e5b8a733d4ab1fdf7e5d22757f7d8eddaa3cbf71d0858874a791844c02ceb35'
STOCK_MAIN_SHA = 'a098f620019b2ad38fd7e0397df39501fb1cf6bc26d0283915f34c49446fdf1a'
STOCK_BOOT_SHA = '038aa5d188af120f3ca2ce4299147952c505f6bc2e8d24c2d1f16db8c21a9d62'
SEC2_ENTRY = bytes.fromhex('00000002000000a000003f3002010000')   # id 2, 0xa0, 16176, 0x02010000
MUST_BE_STOCK = (2, 4, 5, 8)    # 2, 4, 8: HANDOFF/T1.4/T1.5; 5: ROADMAP (only 3 and 7 may differ)
MAY_DIFFER = (3, 7)
RECEIVE_MAX = 0x200000          # the OS's receive buffer 0x4038bee0-0x4058bee0 (T1.4, T1.6)
FLASH_AT = 0x80000
FLASH_LIMIT = 0x380000          # the settings records (T1.4)
MIN_GAP = 1                     # elekloader's rule: a gap <= 0 is refused (T1.6 ran gap 1 on the unit's code)

NAMES = {
    1: 'stock pinned; candidate parses (transport, per-message checksums, framing count)',
    2: 'sections 2, 4, 5, 8 stock; section 2 entry unchanged; same section list',
    3: 'container and received stream within 0x200000',
    4: 'content checksum and HMAC seal: independent and device code',
    5: 'device depacker at both stages gives the intended main OS; in-place margins',
    6: 'protected ranges in the unpacked main OS unchanged (incl. the NOR driver, the writer and the in-OS validator: the real flash gate)',
    7: 'static diff tripwire: no new flash-primitive refs, PUSHR immediates, DSPI0 refs, 0xB0B0DADA',
    8: 'emulated cold boot reaches the main screen; no flash write commands',
    9: 'STARTUP menu reachable (T1.5 harness)',
    10: 'diff report of every changed byte range, with function names',
    11: 'section 7 (SHARC/DSP): stock, or equals --intended-dsp and passes the DSP checks',
    12: 'dynamic in-OS upgrade flash watch: every erase/program address in the allowed window',
}


def sha(b):
    return hashlib.sha256(b).hexdigest()


class Check:
    def __init__(self, cid):
        self.id = cid
        self.name = NAMES[cid]
        self.parts = []          # (status, text)
        self.data = {}
        self.seconds = 0.0

    def ok(self, text):
        self.parts.append(('PASS', text))

    def fail(self, text):
        self.parts.append(('FAIL', text))

    def skip(self, text):
        self.parts.append(('SKIP', text))

    def info(self, text):
        self.parts.append(('info', text))

    def need(self, cond, text_ok, text_fail):
        (self.ok if cond else self.fail)(text_ok if cond else text_fail)
        return cond

    @property
    def status(self):
        st = [s for s, _ in self.parts]
        if 'FAIL' in st:
            return 'FAIL'
        if 'SKIP' in st or 'PASS' not in st:
            return 'SKIP'
        return 'PASS'


def hx(v):
    return '%#010x' % v


class Run:
    def __init__(self, a):
        self.a = a
        self.checks = {i: Check(i) for i in NAMES}
        self.t0 = time.time()
        self.log_lines = []

    def log(self, text):
        print(text, flush=True)
        self.log_lines.append(text)

    def timed(self, cid, fn):
        c = self.checks[cid]
        t = time.time()
        try:
            fn(c)
        except Exception as e:      # a crash in a check is a FAIL, never a pass
            c.fail('the check crashed: %s: %s' % (type(e).__name__, e))
            c.data['traceback'] = traceback.format_exc()
        c.seconds += time.time() - t
        self.log('  check %-2d %-4s %s (%.1f s)' % (cid, c.status, c.name, c.seconds))

    def skip_rest(self, why, ids):
        for i in ids:
            if not self.checks[i].parts:
                self.checks[i].skip(why)

    # ------------------------------------------------------------------
    def go(self):
        a = self.a
        self.log('brickcheck %s' % a.candidate)
        self.log('  stock %s' % a.stock)
        self.log('  out   %s' % a.out)
        # check 1 ---------------------------------------------------------
        self.timed(1, self.c1)
        if self.checks[1].status != 'PASS' or self.cand is None:
            self.skip_rest('check 1 did not pass: the stock file or the candidate cannot be trusted '
                           'or read', range(2, 11))
            return
        self.timed(2, self.c2)
        self.timed(3, self.c3)
        self.timed(4, self.c4_independent)
        self.timed(5, self.c5_static)
        if self.cand_main is not None:
            self.timed(6, self.c6)
            self.timed(7, self.c7)
            self.timed(10, self.c10)
        else:
            self.skip_rest('the candidate main OS did not depack, nothing to compare', (6, 7, 10))
        # section 7 (DSP): its own check, independent of the main OS
        self.timed(11, self.c11)
        # the unit's code under Unicorn
        if a.no_device:
            for i in (4, 5, 9):
                self.checks[i].skip('--no-device: the unit\'s own code was not run')
        else:
            dev = self.device()
            if dev is None:
                for i in (4, 5, 9):
                    self.checks[i].fail('the device harness could not start: %s' % self.dev_error)
            else:
                self.timed(4, self.c4_device)
                self.timed(5, self.c5_device)
                self.timed(9, self.c9)
        if a.no_emu_boot:
            self.checks[8].skip('--no-emu-boot: the emulated cold boot was not run')
            self.checks[12].skip('--no-emu-boot: the dynamic in-OS upgrade flash watch was not run')
        else:
            self.timed(8, self.c8)
            self.timed(12, self.c12)

    # ------------------------------------------------------------------
    def device(self):
        if getattr(self, '_dev', None) is None:
            self.dev_error = None
            try:
                import bc_device
                self._dev = bc_device.Device(self.a.stock, os.path.join(self.a.out, 'work'))
            except Exception as e:
                self._dev = None
                self.dev_error = '%s: %s' % (type(e).__name__, e)
        return self._dev

    # check 1 ------------------------------------------------------------
    def c1(self, c):
        self.cand = None
        raw_stock = open(self.a.stock, 'rb').read()
        s = sha(raw_stock)
        c.data['stock_sha256'] = s
        if not c.need(s == STOCK_SHA, 'stock is Digitone II OS 1.12: sha256 %s' % s,
                      'stock sha256 %s is not the pinned %s' % (s, STOCK_SHA)):
            return
        self.stock = bc_syx.Syx(raw_stock, self.a.stock)
        c.need(not self.stock.problems, 'stock parses cleanly (%d data messages)' % len(self.stock.data),
               'stock has transport problems: %s' % self.stock.problems)
        raw = open(self.a.candidate, 'rb').read()
        c.data['candidate_sha256'] = sha(raw)
        c.data['candidate_bytes'] = len(raw)
        c.info('candidate sha256 %s, %d bytes%s' % (sha(raw), len(raw),
               ' (byte-identical to stock)' if raw == raw_stock else ''))
        try:
            cand = bc_syx.Syx(raw, self.a.candidate)
        except bc_syx.ParseError as e:
            c.fail('candidate does not parse: %s' % e)
            return
        self.cand = cand
        c.data.update({'data_messages': len(cand.data), 'framing_counts': cand.framing_counts,
                       'bad_message_checksums': len(cand.bad_checksums),
                       'bad_counters': len(cand.bad_counters), 'container_len': cand.total,
                       'version_field': cand.version, 'device_id': cand.device_id})
        c.need(not cand.problems,
               'transport: %d data messages, counters %d..%d in order, every message checksum '
               'right (k=%#04x), framing count %s on both framing messages, padding %d zero bytes'
               % (len(cand.data), bc_syx.FIRST_COUNTER, bc_syx.FIRST_COUNTER + len(cand.data) - 1,
                  cand.k, cand.framing_counts[0], len(cand.tail)),
               'transport problems: %s' % '; '.join(cand.problems))
        c.need(cand.device_id == self.stock.device_id and cand.k == self.stock.k,
               'device id %#04x and transfer constant %#04x as stock' % (cand.device_id, cand.k),
               'device id %#04x / constant %#04x, stock %#04x / %#04x'
               % (cand.device_id, cand.k, self.stock.device_id, self.stock.k))
        same_framing = all(f[:12] + f[15:] == g[:12] + g[15:]
                           for f, g in zip(cand.framing, self.stock.framing))
        c.need(same_framing, 'framing messages equal stock\'s apart from the count',
               'a framing message differs from stock\'s beyond its count bytes')
        try:
            import bc_device
            dk = bc_device.decode_digikit(self.a.candidate)
            c.need(dk == cand.stream, 'decoded stream identical to digikit\'s decoder (%d bytes)'
                   % len(dk), 'this decoder and digikit\'s disagree on the decoded stream')
        except ImportError as e:
            c.skip('digikit decoder not importable for the cross-check: %s' % e)
        c.info('container %d bytes (%#x), ELE3 version field %r, sections %s'
               % (cand.total, cand.total, cand.version, [t[0] for t in cand.table]))

    # check 2 ------------------------------------------------------------
    def c2(self, c):
        s, k = self.stock, self.cand
        c.need([(t[0], t[3]) for t in k.table] == [(t[0], t[3]) for t in s.table],
               'section list and order as stock: %s' % ', '.join('%d (dest %s)' % (t[0], hx(t[3]))
                                                                for t in k.table),
               'section list differs: %s, stock %s' % ([(t[0], hx(t[3])) for t in k.table],
                                                       [(t[0], hx(t[3])) for t in s.table]))
        e2 = k.entries.get(2)
        c.need(e2 == s.entries[2] == SEC2_ENTRY,
               'section 2 table entry %s unchanged (offset 0xa0, 16,176 bytes, version 0x0201 in '
               '0x02010000)' % SEC2_ENTRY.hex(),
               'section 2 table entry %s, stock %s: a higher version there makes the bootstrap '
               're-flash itself (T1.4/T1.5)' % (e2.hex() if e2 else None, s.entries[2].hex()))
        for sid in MUST_BE_STOCK:
            a, b = k.stored.get(sid), s.stored[sid]
            c.need(a == b, 'section %d stored bytes identical to stock (%d bytes, sha256 %s)'
                   % (sid, len(b), sha(b)[:16]),
                   'section %d stored bytes differ from stock (%s bytes vs %d)'
                   % (sid, len(a) if a is not None else 'missing', len(b)))
        hd = [i for i in range(bc_syx.HEADER_LEN) if k.header[i] != s.header[i]]
        bad = [i for i in hd if not bc_syx.VERSION_FIELD[0] <= i < bc_syx.VERSION_FIELD[1]]
        c.need(not bad, 'ELE3 header as stock%s' % (' apart from the version field (%r, stock %r)'
                                                    % (k.version, s.version) if hd else ''),
               'ELE3 header differs outside the version field at offsets %s' % bad)
        first = min(t[1] for t in k.table)
        sfirst = min(t[1] for t in s.table)
        c.need(k.container[k.table_end:first] == s.container[s.table_end:sfirst],
               'bytes between the table and the first section as stock',
               'bytes between the table and the first section differ from stock')
        for sid in MAY_DIFFER:
            same = k.stored.get(sid) == s.stored[sid]
            c.info('section %d %s (%d stored bytes, stock %d)'
                   % (sid, 'identical to stock' if same else 'differs from stock (allowed)',
                      len(k.stored.get(sid, b'')), len(s.stored[sid])))
        if k.stored.get(7) is not None and k.stored[7] != s.stored[7]:
            # The DSP is no longer vouched for here: a changed section 7 is
            # validated by check 11 (it must equal --intended-dsp and pass the
            # DSP checks), and a changed DSP with no intended image is
            # "incomplete", never accept (T1.7b gap 2).
            c.info('section 7 (the SHARC/DSP program) differs from stock (%d stored bytes, stock %d): '
                   'validated by check 11, not here'
                   % (len(k.stored[7]), len(s.stored[7])))

    # check 3 ------------------------------------------------------------
    def c3(self, c):
        k = self.cand
        c.data.update({'container_len': k.total, 'received': k.received,
                       'flash_end': hx(FLASH_AT + k.total)})
        c.need(k.total <= RECEIVE_MAX, 'container %d bytes <= 0x200000 (%d to spare)'
               % (k.total, RECEIVE_MAX - k.total),
               'container %d bytes > 0x200000 by %d' % (k.total, k.total - RECEIVE_MAX))
        c.need(k.received <= RECEIVE_MAX,
               'stream as the OS receives it, %d messages x 101 = %d bytes <= 0x200000 (%d to spare)'
               % (len(k.data), k.received, RECEIVE_MAX - k.received),
               'stream as the OS receives it, %d bytes, overruns the 0x200000 receive buffer at '
               '0x4038bee0 by %d' % (k.received, k.received - RECEIVE_MAX))
        c.need(FLASH_AT + k.total <= FLASH_LIMIT,
               'flash end %s <= 0x380000 (settings records)' % hx(FLASH_AT + k.total),
               'flash end %s reaches the 0x380000 settings records' % hx(FLASH_AT + k.total))

    # check 4 ------------------------------------------------------------
    def c4_independent(self, c):
        k = self.cand
        got = bc_syx.content_checksum(k.container)
        c.data['content_checksum'] = {'computed': hx(got), 'preamble': hx(k.checksum)}
        c.need(got == k.checksum, 'independent: content checksum %s matches the preamble' % hx(got),
               'independent: content checksum %s, preamble says %s' % (hx(got), hx(k.checksum)))
        import bc_device
        boot = bc_device.depack(self.stock.stored[2])
        if not c.need(sha(boot) == STOCK_BOOT_SHA, 'stock bootstrap depacks to sha256 %s'
                      % STOCK_BOOT_SHA[:16], 'stock bootstrap depacks to %s' % sha(boot)):
            return
        key = bc_syx.seal_key(boot)
        if not c.need(bc_syx.seal(key, self.stock.container) == self.stock.container[-32:],
                      'key from the stock bootstrap ("Multiplier" seed) verifies stock\'s own seal',
                      'the derived key does not verify stock\'s own seal: derivation broken'):
            return
        mine = bc_syx.seal(key, k.container)
        c.data['seal'] = {'computed': mine.hex(), 'stored': k.container[-32:].hex()}
        c.need(mine == k.container[-32:],
               'independent: HMAC-SHA256 seal over %d bytes matches the trailer %s...'
               % (k.total - 32, mine.hex()[:16]),
               'independent: HMAC-SHA256 seal %s does not match the trailer %s'
               % (mine.hex(), k.container[-32:].hex()))

    def c4_device(self, c):
        dev = self.device()
        r = dev.bootstrap_commit(self.cand.stream)
        c.data['bootstrap_commit'] = r
        c.need(r['verdict'] == 'PASS' and r['checksum_passed'] and r['trailer_passed'],
               'device: bootstrap verify-and-commit %s reached UPGRADING at %s (checksum %s, '
               'trailer passed)' % (hx(dev.R['commit'][0]), r['stopped_at'], r['checksum_computed']),
               'device: bootstrap verify-and-commit %s: %s at %s (checksum computed %s stored %s, '
               'checksum passed %s, trailer passed %s)'
               % (hx(dev.R['commit'][0]), r['verdict'], r['stopped_at'], r['checksum_computed'],
                  r['checksum_stored'], r['checksum_passed'], r['trailer_passed']))
        v = dev.os_validator(self.cand.stream)
        c.data['os_validator'] = v
        c.need(v['returned'] == 1, 'device: main OS validator %s returned 1 (pass)'
               % hx(dev.M['validator'][0]),
               'device: main OS validator %s returned %d (%s)'
               % (hx(dev.M['validator'][0]), v['returned'], v['means']))

    # check 5 ------------------------------------------------------------
    def c5_static(self, c):
        self.cand_main = None
        import bc_device
        stock_main = bc_device.depack(self.stock.stored[3])
        if not c.need(sha(stock_main) == STOCK_MAIN_SHA, 'stock main OS depacks to %s...'
                      % STOCK_MAIN_SHA[:16], 'stock main OS depacks to %s' % sha(stock_main)):
            return
        self.stock_main = stock_main
        if self.a.intended_main:
            want = open(self.a.intended_main, 'rb').read()
            c.info('intended main OS: %s (%d bytes, sha256 %s)' % (self.a.intended_main, len(want),
                                                                  sha(want)[:16]))
        else:
            want = stock_main
            c.info('intended main OS: the stock main OS (%d bytes, sha256 %s)'
                   % (len(want), sha(want)[:16]))
        self.want = want
        s3 = self.cand.stored[3]
        try:
            img = bc_device.depack(s3)
        except Exception as e:
            c.fail('section 3 does not depack with dt2/elz: %s' % e)
            return
        self.cand_main = img
        c.data['python_depack'] = {'len': len(img), 'sha256': sha(img)}
        c.need(img == want, 'Python depack (dt2/elz) of section 3 = intended (%d bytes)' % len(img),
               'Python depack (dt2/elz) of section 3 (%d bytes, sha256 %s) is not the intended '
               'image' % (len(img), sha(img)[:16]))
        try:
            dev = self.device() if not self.a.no_device else None
            gaps = dev.gaps(s3) if dev else None
        except Exception:
            gaps = None
        if gaps is None:
            import bc_device
            gaps = {}
            for stage in (0x40400000, 0x40200000):
                g, n = bc_device.ao.inplace_gap(s3, stage, 0x40000400)
                gaps[hx(stage)] = {'gap': g, 'output_bytes': n}
        c.data['inplace_gaps'] = gaps
        sg = {}
        for stage, g in gaps.items():
            stock_g = bc_device.ao.inplace_gap(self.stock.stored[3], int(stage, 16), 0x40000400)[0]
            sg[stage] = stock_g
            label = 'bootstrap loader' if stage == '0x40400000' else 'section 4 in-place loader'
            c.need(g['gap'] >= MIN_GAP,
                   'in-place margin at %s (%s): %+d bytes (stock %+d)' % (stage, label, g['gap'], stock_g),
                   'in-place margin at %s (%s): %+d bytes, below %d: the depacker overwrites '
                   'unread input there (stock %+d)' % (stage, label, g['gap'], MIN_GAP, stock_g))
        c.data['stock_gaps'] = sg

    def c5_device(self, c):
        dev = self.device()
        want = self.want
        bp = dev.boot_path(self.cand.stream, want)
        c.data['boot_path'] = bp
        entry = hx(struct.unpack_from('>I', want, 0)[0])
        c.need(bp['output_matches'] and bp['entered_main_at'] == entry and not bp['error']
               and bp['untouched_below_output'] and bp.get('untouched_between_output_and_stage', True)
               and bp.get('stage_last_byte_read') == bp.get('stage_stream_end'),
               'device: bootstrap boot path %s -> stage %s -> depacked to 0x40000400, %d bytes = '
               'intended, entered the main OS at %s; read the stream to its last byte %s; nothing '
               'below or past the output touched (%.1f s)'
               % (hx(dev.R['boot_from'][0]), bp['stage'], bp['depack_returned'] or 0,
                  bp['entered_main_at'], bp.get('stage_last_byte_read'), bp['seconds']),
               'device: bootstrap boot path: entered %s (want %s), output matches %s (%d bytes '
               'differ, first at %s), untouched below/between %s/%s, last stage byte read %s of %s, '
               'error %s, stopped at %s'
               % (bp['entered_main_at'], entry, bp['output_matches'], bp['output_bytes_differing'],
                  bp['first_difference'], bp['untouched_below_output'],
                  bp.get('untouched_between_output_and_stage'), bp.get('stage_last_byte_read'),
                  bp.get('stage_stream_end'), bp['error'], bp['stopped_at']))
        up = dev.section4_path(self.cand.stream, want)
        c.data['section4_path'] = up
        c.need(up['output_matches'] and up['entered_main_at'] == entry and not up['error'],
               'device: section 4 loader %s -> stage %s in place -> 0x40000400, %d bytes = intended, '
               'entered the main OS at %s (%.1f s)'
               % (hx(dev.U['loader'][0]), up['stage'], up['depack_returned'] or 0,
                  up['entered_main_at'], up['seconds']),
               'device: section 4 loader %s (stage %s, in place): entered %s, output matches %s, '
               '%d bytes differ, first at %s, stopped at %s, error %s'
               % (hx(dev.U['loader'][0]), up['stage'], up['entered_main_at'], up['output_matches'],
                  up['output_bytes_differing'], up['first_difference'], up['stopped_at'], up['error']))

    # check 6 ------------------------------------------------------------
    def c6(self, c):
        rep = bc_static.protected_report(self.stock_main, self.cand_main)
        c.data['ranges'] = rep
        for r in rep:
            c.need(r['unchanged'], '%s-%s unchanged: %s' % (r['lo'], r['hi'], r['what']),
                   '%s-%s CHANGED (%d bytes, first at %s): %s'
                   % (r['lo'], r['hi'], r['bytes_differing'], r['first_difference'], r['what']))
        # The real flash gate (T1.7b gap 3): the NOR driver and the in-OS writer
        # kept byte-identical. Check 7 is only a tripwire; this is what a
        # computed-address gadget cannot get past (it must go through these bytes).
        gate = bc_static.protected_report(self.stock_main, self.cand_main, extra=bc_static.FLASH_GATE)
        for r in gate[len(bc_static.PROTECTED):]:
            c.need(r['unchanged'],
                   'FLASH GATE %s-%s byte-identical to stock: %s' % (r['lo'], r['hi'], r['what']),
                   'FLASH GATE %s-%s CHANGED (%d bytes): %s -- a candidate that alters the flash '
                   'path is refused' % (r['lo'], r['hi'], r['bytes_differing'], r['what']))
        try:
            import importlib
            sys.path.insert(0, os.path.join(ROOT, 'vendor', 'elekloader'))
            prof = importlib.import_module('elekloader.devices.digitone_mk2').DEVICE.protected
            mine = {(lo, hi) for lo, hi, _ in bc_static.PROTECTED}
            missing = [(hx(lo), hx(hi)) for lo, hi, _ in prof if (lo, hi) not in mine]
            c.need(not missing, 'every range in elekloader\'s digitone_mk2 profile is checked here',
                   'the elekloader profile has ranges this checker lacks: %s' % missing)
        except Exception as e:
            c.info('elekloader profile not importable for the cross-check (%s); the T1.6 ranges '
                   'are built in' % e)

    # check 7 ------------------------------------------------------------
    def c7(self, c):
        ranges = bc_static.changed_ranges(self.stock_main, self.cand_main)
        self.ranges = ranges
        nbytes = sum(hi - lo for lo, hi in ranges)
        c.data['changed_ranges'] = len(ranges)
        c.data['changed_bytes'] = nbytes
        c.info('%d changed ranges, %d bytes, against the stock main OS (%d -> %d bytes)'
               % (len(ranges), nbytes, len(self.stock_main), len(self.cand_main)))
        res = bc_static.scan_new(self.stock_main, self.cand_main, ranges)
        c.data['scan'] = res

        def lst(items, key):
            return ', '.join('%s %s' % (i['site'], i[key]) for i in items[:8]) + \
                (' ... (%d)' % len(items) if len(items) > 8 else '')
        c.need(not res['flash_refs'], 'no new reference to the NOR driver (0x40134a28-0x4013621c) '
               'or a flash writer (T1.4 clusters A-D)',
               'NEW references to flash code: %s' % '; '.join('%s %s -> %s' % (r['site'], r['form'], r['target'])
                                                             for r in res['flash_refs'][:8]))
        c.need(not res['pushr'], 'no new DSPI PUSHR command word for the flash chip select',
               'NEW PUSHR command words: %s' % lst(res['pushr'], 'word'))
        c.need(not res['dspi0'], 'no new reference to the DSPI0 registers 0xFC05C000-0xFC05C0FF',
               'NEW DSPI0 register references: %s' % lst(res['dspi0'], 'register'))
        c.need(not res['magic'], 'no new 0xB0B0DADA (console #REBOOT magic)',
               'NEW 0xB0B0DADA at %s' % ', '.join(i['site'] for i in res['magic']))
        c.need(not res['computed_flash'],
               'no computed flash reference (movea.l #base,An; adda.l #disp,An into the driver)',
               'NEW computed flash reference(s): %s'
               % '; '.join('%s %s -> %s' % (r['site'], r['form'], r['target'])
                           for r in res['computed_flash'][:6]))
        c.info('check 7 is a TRIPWIRE, not the flash gate: it scans the changed bytes for literal '
               'and this one computed flash reference, but a reference built another way at run time '
               'is not seen. The gate is check 6 (NOR driver + writer byte-identical) and check 12 '
               '(the dynamic upgrade watch).')
        if res['cell_0x48000000']:
            c.info('new 0x48000000 words (the magic cell / stack top), not refused on their own: %s'
                   % ', '.join(i['site'] for i in res['cell_0x48000000'][:8]))

    # check 8 ------------------------------------------------------------
    def c8(self, c):
        import bc_emuboot
        d = os.path.join(self.a.out, 'emu')
        r = bc_emuboot.run(os.path.abspath(self.a.candidate), d, log=self.log)
        c.data['emu'] = {k: v for k, v in r.items() if not k.startswith('watch')}
        c.data['watch'] = {'ladder': r.get('watch_ladder'), 'check': r.get('watch_check')}
        if r.get('error'):
            c.fail('emulated boot: %s (logs in %s)' % (r['error'], d))
            return
        c.need(r['emucheck_pass'], 'emucheck: %s' % r['verdict_line'],
               'emucheck: %s; %s' % (r['verdict_line'], '; '.join(x for x in r['reasons'] if x.startswith('FAIL'))))
        c.need(r['first_main_screen'] and 'never' not in r['first_main_screen'],
               r['first_main_screen'] or '', 'main screen never latched')
        if r.get('png'):
            ref = os.path.join(ROOT, 'out', 'T1.1', 'main-screen.png')
            same = os.path.exists(ref) and open(ref, 'rb').read() == open(r['png'], 'rb').read()
            c.info('screen PNG %s (sha256 %s)%s' % (r['png'], sha(open(r['png'], 'rb').read())[:16],
                   ', byte-identical to stock\'s out/T1.1/main-screen.png' if same else
                   (', differs from out/T1.1/main-screen.png' if os.path.exists(ref) else '')))
        else:
            c.fail('no screen PNG was written')
        for phase in ('ladder', 'check'):
            w = r.get('watch_' + phase)
            if w is None:
                c.fail('flash watch (%s): no record' % phase)
                continue
            calls = [x for x in w['calls']]
            writes = w['writes']
            c.need(not calls and not writes,
                   'flash watch (%s, %s s): no call to a NOR write/erase/protect entry or the '
                   'upgrade writer; PUSHR commands seen %s, none of them a write, erase or protect'
                   % ('cold boot to 400M' if phase == 'ladder' else 'emucheck 400M->1000M',
                      w['seconds'], w['commands'] or 'none'),
                   'flash watch (%s): %d write-class calls %s; %d write-class commands %s'
                   % (phase, len(calls), [(x['entry'], x['what']) for x in calls[:6]], len(writes),
                      writes[:6]))

    # check 9 ------------------------------------------------------------
    def c9(self, c):
        dev = self.device()
        ok, bad, summ = dev.recovery(self.cand.container)
        c.data['runs'] = summ
        f, n, s, o = summ['func'], summ['none'], summ['silent'], summ['osupgrade']
        if ok:
            c.ok('FUNC held: stopped in the STARTUP menu key loop %s, %s; screen identical to '
                 'stock\'s (%d lit pixels)' % (f['stop_pc'], ' > '.join(f['landmarks']), f['lit_pixels']))
            c.ok('no key: normal boot to the loader %s, self-upgrade did not force the menu, boot '
                 'flags %s' % (n['stop_pc'], n['boot_flags']))
            c.ok('silent panel: %s at %s (boot flags %s), as stock; that path is check 5\'s section-4 loader'
                 % (s['stop'], s['stop_pc'], s['boot_flags']))
            c.ok('OS UPGRADE: READY TO RECEIVE and the MIDI wait %s with no flash read after entering it'
                 % o['stop_pc'])
            c.ok('no run reached flash_erase 0x80002896, flash_program 0x8000282a, the abort screen '
                 'or the DRAM halt')
        else:
            for b in bad:
                c.fail(b)

    # check 10 -----------------------------------------------------------
    def c10(self, c):
        names = bc_static.Names(self.a.decomp)
        ranges = bc_static.changed_ranges(self.stock_main, self.cand_main)
        rows = []
        for lo, hi in ranges:
            rows.append({'lo': hx(lo), 'hi': hx(hi), 'len': hi - lo,
                         'functions': names.covering(lo, hi) if names.ok else None})
        self.diff_rows = rows
        c.data['main_os_ranges'] = rows
        sec = []
        for t in self.stock.table:
            sid = t[0]
            a, b = self.cand.stored.get(sid), self.stock.stored[sid]
            ct = self.cand.section_of(sid) if sid in self.cand.stored else None
            sec.append({'section': sid, 'same': a == b, 'stored': len(a) if a is not None else None,
                        'stock_stored': len(b), 'offset': hx(ct[1]) if ct else None,
                        'stock_offset': hx(t[1])})
        c.data['sections'] = sec
        c.info('%d changed ranges in the main OS, %d bytes; sections that differ: %s'
               % (len(rows), sum(r['len'] for r in rows), [x['section'] for x in sec if not x['same']] or 'none'))
        if names.ok:
            c.ok('names from %s' % names.source)
        else:
            c.skip('no %s/functions.tsv: ranges listed without function names' % self.a.decomp)
        c.ok('diff written to report.md and report.json (every range)')

    # check 11 (DSP) -----------------------------------------------------
    def c11(self, c):
        s, k = self.stock, self.cand
        s7, k7 = s.stored.get(7), k.stored.get(7)
        if k7 is None:
            c.fail('the candidate has no section 7'); return
        if k7 == s7:
            c.ok('section 7 byte-identical to stock: the DSP program is stock'); return
        try:
            cand_dsp = bc_dsp.depack(k7)
        except Exception as e:
            c.fail('section 7 differs from stock and does NOT depack: %s' % e); return
        c.info('section 7 differs and depacks to %d bytes, sha256 %s' % (len(cand_dsp), sha(cand_dsp)[:16]))
        if not self.a.intended_dsp:
            c.skip('section 7 (the SHARC/DSP program) differs from stock but no --intended-dsp was '
                   'given: the DSP content cannot be vouched for. Pass --intended-dsp <depacked '
                   'image> to validate a DSP change (T1.7b gap 2).')
            return
        want = open(self.a.intended_dsp, 'rb').read()
        c.info('intended DSP: %s (%d bytes, sha256 %s)' % (self.a.intended_dsp, len(want), sha(want)[:16]))
        if not c.need(cand_dsp == want,
                      'depacked section 7 equals the intended DSP image exactly (%d bytes)' % len(cand_dsp),
                      'depacked section 7 (%d bytes, sha256 %s) is not the intended DSP image '
                      '(%d bytes, sha256 %s)' % (len(cand_dsp), sha(cand_dsp)[:16], len(want), sha(want)[:16])):
            return
        # static DSP checks
        sb = bc_dsp.scan_sizes_and_blocks(k7, cand_dsp)
        c.data['dsp_static'] = sb
        ok = True
        ok &= c.need(sb['stored_ok'],
                     'stored section 7 %d bytes <= 1 MiB (ColdFire DSP loader %s)'
                     % (sb['stored_len'], bc_dsp.STORED_LIMIT_SITE),
                     'stored section 7 %d bytes > 1 MiB: the DSP loader refuses it (%s) and the DSP '
                     'never boots' % (sb['stored_len'], bc_dsp.STORED_LIMIT_SITE))
        ok &= c.need(sb['stream_ok'],
                     'depacked boot stream %d bytes <= 1 MiB - 1 (%s)'
                     % (sb['stream_len'], bc_dsp.STREAM_LIMIT_SITE),
                     'depacked boot stream %d bytes > 1 MiB - 1: the DSP loader refuses it (%s)'
                     % (sb['stream_len'], bc_dsp.STREAM_LIMIT_SITE))
        ok &= c.need(sb['parse_ok'] and sb['final'],
                     'boot stream parses completely and ends with a FINAL block (%d blocks; %s)'
                     % (sb['blocks'], 'no payload CRC' if not sb['has_crc'] else 'CALLBACK CRCs verified'),
                     'boot stream does not parse cleanly: %s'
                     % ([x['check'] for x in sb['parse_fail']][:4] or 'no FINAL block'))
        ok &= c.need(not sb['cache_hits'],
                     'no load block writes into an L1/L2 cache range the start-up enables',
                     'load block(s) write into a cache range (lost when the caches turn on): %s'
                     % sb['cache_hits'][:4])
        ok &= c.need(not sb['region_miss'],
                     'every load block lands in a known L1/L2/DDR region',
                     'load block(s) target memory outside the known regions: %s' % sb['region_miss'][:4])
        # bad-shift scan over changed/new DSP code
        cand_img, blocks, _ch, _er = bc_dsp.final_image(cand_dsp)
        stock_img = self._stock_dsp_image()
        bads = bc_dsp.scan_bad_shifts(cand_img, stock_img, blocks)
        c.data['dsp_bad_shifts'] = bads
        ok &= c.need(not bads,
                     'no mis-encoded negative-immediate shift in changed/new DSP code',
                     'mis-encoded shift form(s) selas emits and the silicon rejects (ILOPI, the core '
                     'parks): %s' % [b['sw'] for b in bads][:6])
        # dynamic run
        if self.a.no_dsp_sim:
            c.skip('--no-dsp-sim: the SHARC block handler was not run; the DSP is not dynamically validated')
            return
        if not ok:
            c.info('static DSP checks already failed; the dynamic run was not started')
            return
        if not bc_dsp.dynamic_available():
            c.skip('tools/sharcvm memory images (out/T3.2/mem) are missing; the dynamic DSP run did '
                   'not happen'); return
        dyn = bc_dsp.dynamic_run(k7, blocks_n=self.a.dsp_blocks, loud=self.a.dsp_loud)
        c.data['dsp_dynamic'] = dyn
        if dyn.get('fail'):
            c.fail('dynamic DSP run (sharcvm, init %ss): %s'
                   % (dyn.get('init_seconds'), dyn['fail']))
        else:
            over = dyn.get('over_budget')
            c.ok('dynamic DSP run: %d blocks, no trap/NaN/Inf, peak %.3f <= %.1f, %d instructions/'
                 'block (stock %d)%s (init %ss, run %ss)'
                 % (dyn['blocks_run'], dyn['peak'], dyn['loud_ceiling'], dyn['insn_per_block'],
                    bc_dsp.STOCK_INSN_PER_BLOCK, '; WARN over 1.5x the stock budget' if over else '',
                    dyn.get('init_seconds'), dyn.get('seconds')))
            if over:
                c.info('WARNING: %d instructions/block is over 1.5x the stock budget (%d); the DSP '
                       'may overload at full voice count' % (dyn['insn_per_block'], bc_dsp.STOCK_INSN_PER_BLOCK))

    def _stock_dsp_image(self):
        if getattr(self, '_stock_dsp', None) is None:
            img, _b, _c, _e = bc_dsp.final_image(bc_dsp.depack(self.stock.stored[7]))
            self._stock_dsp = img
        return self._stock_dsp

    # check 12 (dynamic in-OS upgrade flash watch) -----------------------
    def c12(self, c):
        if self.a.no_upgrade_watch:
            c.skip('--no-upgrade-watch: the dynamic in-OS upgrade flash watch was not run'); return
        if self.checks[6].status != 'PASS':
            c.skip('check 6 did not confirm the writer and driver are stock, so running the stock '
                   'writer would not reflect this candidate'); return
        snap = self.a.warm_snap
        if not bc_upgrade.available(snap):
            c.skip('the dynamic upgrade watch needs scripts/t17b_flashwatch.py, digikit\'s venv and '
                   'the warm snapshot %s; one is missing' % (snap or bc_upgrade.WARM_SNAP)); return
        rec = bc_upgrade.run(self.a.candidate, os.path.join(self.a.out, 'upgrade'), snap=snap)
        c.data['upgrade_watch'] = rec
        if rec.get('error'):
            c.skip('the dynamic upgrade watch could not run: %s' % rec['error']); return
        v = rec.get('verdict', {})
        window = '[0x80000,0x380000)'
        c.need(v.get('accept'),
               'in-OS upgrade writer: %d block-erases and programs %s..%s, every flash address in %s, '
               'no protect/OTP command (container length %#x, %.1fs wall)'
               % (v.get('erase_count', 0), v.get('program_min'), v.get('program_max'), window,
                  rec.get('declared_len', 0), rec.get('wall_seconds', 0)),
               'in-OS upgrade writer issues OUT-OF-WINDOW flash commands: addrs %s%s%s -- do not flash'
               % (v.get('out_of_window_addrs', [])[:6],
                  '; reaches the settings store %s' % v['hits_settings_store'] if v.get('hits_settings_store') else '',
                  '; reaches the reset+bootstrap region %s' % v['hits_bootstrap_region'] if v.get('hits_bootstrap_region') else ''))
        c.info('coverage: the in-OS upgrade WRITER driven directly over the candidate container and '
               'watched (the NOR driver and FUN_40139104 are byte-pinned by check 6, so the stock '
               'writer is faithful). It does NOT drive scripted menu/key sessions or an organic '
               'SysEx upgrade, and does not execute a gadget off the upgrade path (that is check 6/7).')

    # ------------------------------------------------------------------
    def verdict(self):
        st = [self.checks[i].status for i in sorted(self.checks)]
        if 'FAIL' in st:
            return 'refuse'
        if 'SKIP' in st:
            return 'incomplete'
        return 'accept'


# --------------------------------------------------------------------------
# reports
# --------------------------------------------------------------------------


def print_checks(run):
    print()
    for i in sorted(run.checks):
        c = run.checks[i]
        print('%-4s %2d  %s' % (c.status, i, c.name))
        for s, t in c.parts:
            print('        %-4s %s' % (s, t))
    v = run.verdict()
    print('\nverdict: %s  (%s)' % (v.upper(), ', '.join('%d %s' % (sum(1 for c in run.checks.values()
                                                                     if c.status == s), s)
                                                    for s in ('PASS', 'FAIL', 'SKIP'))))


def write_reports(run):
    a = run.a
    os.makedirs(a.out, exist_ok=True)
    v = run.verdict()
    js = {
        'tool': 'brickcheck (El-Fw tools/brickcheck)',
        'date': datetime.datetime.now().isoformat(timespec='seconds'),
        'candidate': os.path.abspath(a.candidate), 'stock': os.path.abspath(a.stock),
        'intended_main': os.path.abspath(a.intended_main) if a.intended_main else None,
        'intended_dsp': os.path.abspath(a.intended_dsp) if a.intended_dsp else None,
        'verdict': v, 'seconds': round(time.time() - run.t0, 1),
        'checks': {str(i): {'name': c.name, 'status': c.status, 'seconds': round(c.seconds, 1),
                            'evidence': [{'status': s, 'text': t} for s, t in c.parts], 'data': c.data}
                   for i, c in sorted(run.checks.items())},
    }
    dev = getattr(run, '_dev', None)
    if dev is not None:
        js['device_addresses'] = dev.addresses()
    with open(os.path.join(a.out, 'report.json'), 'w') as fh:
        json.dump(js, fh, indent=1, default=str)
    L = []
    L.append('# brickcheck: %s' % os.path.basename(a.candidate))
    L.append('')
    L.append('**Verdict: %s.** %s' % (v.upper(), {
        'accept': 'Every check passed.',
        'refuse': 'At least one check failed. Do not flash this file.',
        'incomplete': 'No check failed, but at least one was skipped. A skip is not a pass: do '
                      'not flash this file until every check has run.'}[v]))
    L.append('')
    L.append('- candidate: `%s`, sha256 `%s`' % (os.path.abspath(a.candidate),
                                                run.checks[1].data.get('candidate_sha256')))
    L.append('- stock: `%s`, sha256 `%s`' % (os.path.abspath(a.stock), run.checks[1].data.get('stock_sha256')))
    L.append('- intended main OS: %s' % ('`%s`' % os.path.abspath(a.intended_main) if a.intended_main
                                        else 'the stock main OS'))
    L.append('- run: %s, %.0f s' % (js['date'], js['seconds']))
    L.append('')
    L.append('| # | check | status | time |')
    L.append('|---|---|---|---|')
    for i, c in sorted(run.checks.items()):
        L.append('| %d | %s | **%s** | %.0f s |' % (i, c.name, c.status, c.seconds))
    L.append('')
    for i, c in sorted(run.checks.items()):
        L.append('## %d. %s: %s' % (i, c.name, c.status))
        L.append('')
        for s, t in c.parts:
            L.append('- %s: %s' % (s, t))
        L.append('')
        if i == 5 and 'inplace_gaps' in c.data:
            L.append('| stage | margin | stock |')
            L.append('|---|---|---|')
            for st, g in c.data['inplace_gaps'].items():
                L.append('| `%s` | %+d | %+d |' % (st, g['gap'], c.data.get('stock_gaps', {}).get(st, 0)))
            L.append('')
        if i == 8 and c.data.get('emu', {}).get('png'):
            L.append('![main screen](%s)' % os.path.relpath(c.data['emu']['png'], a.out))
            L.append('')
        if i == 10 and 'main_os_ranges' in c.data:
            rows = c.data['main_os_ranges']
            L.append('Sections:')
            L.append('')
            L.append('| section | stock? | stored | stock stored | offset | stock offset |')
            L.append('|---|---|---|---|---|---|')
            for x in c.data['sections']:
                L.append('| %d | %s | %s | %d | %s | %s |' % (x['section'], 'yes' if x['same'] else '**no**',
                                                              x['stored'], x['stock_stored'], x['offset'],
                                                              x['stock_offset']))
            L.append('')
            L.append('Changed byte ranges in the unpacked main OS (base 0x40000400), %d:' % len(rows))
            L.append('')
            if rows:
                L.append('| from | to (excl.) | bytes | functions |')
                L.append('|---|---|---|---|')
                for r in rows:
                    fn = r['functions']
                    L.append('| `%s` | `%s` | %d | %s |' % (r['lo'], r['hi'], r['len'],
                                                           ', '.join(fn) if fn else ('(none)' if fn == [] else '?')))
            else:
                L.append('None: the main OS is byte-identical to stock.')
            L.append('')
    if dev is not None:
        L.append('## Device addresses used (found by signature in the stock file)')
        L.append('')
        for k, x in js['device_addresses'].items():
            L.append('- %s: `%s` (%s)' % (k, x['address'], x['how found']))
        L.append('')
    with open(os.path.join(a.out, 'report.md'), 'w') as fh:
        fh.write('\n'.join(L) + '\n')
    return v


def main(argv=None):
    ap = argparse.ArgumentParser(prog='brickcheck', description=__doc__.split('\n\n')[0])
    ap.add_argument('candidate', help='the candidate .syx')
    ap.add_argument('--stock', default=os.path.join(ROOT, 'elektron-firmware', 'Digitone_II_OS1_12.syx'),
                    help='stock Digitone II OS 1.12 .syx (pinned by sha256)')
    ap.add_argument('--intended-main', help='the main OS image the candidate must depack to '
                    '(default: the stock main OS)')
    ap.add_argument('--intended-dsp', help='the depacked DSP image (section 7) the candidate must '
                    'depack to; without it a changed section 7 makes the verdict "incomplete"')
    ap.add_argument('--no-dsp-sim', action='store_true',
                    help='skip check 11\'s dynamic SHARC run (tools/sharcvm); check 11 is then at '
                    'best "incomplete" for a changed DSP')
    ap.add_argument('--dsp-blocks', type=int, default=1500,
                    help='blocks for check 11\'s dynamic SHARC run (default 1500, one second of audio)')
    ap.add_argument('--dsp-loud', type=float, default=4.0,
                    help='loudness ceiling for check 11\'s dynamic run (default 4.0, full scale is 1.0)')
    ap.add_argument('--no-upgrade-watch', action='store_true',
                    help='skip check 12 (the dynamic in-OS upgrade flash watch)')
    ap.add_argument('--warm-snap', help='warm 800M snapshot for check 12 '
                    '(default out/T2.4a/warm/warm800M.snap)')
    ap.add_argument('--out', help='report directory (default out/brickcheck/<candidate name>)')
    ap.add_argument('--decomp', default=os.path.join(ROOT, 'out', 'decomp', 'main_os'),
                    help='directory with functions.tsv (and xrefs.sqlite) for function names')
    ap.add_argument('--no-emu-boot', action='store_true',
                    help='skip check 8 (about 12 minutes); the verdict is then at best "incomplete"')
    ap.add_argument('--no-device', action='store_true',
                    help='skip the Unicorn runs of the unit\'s code (checks 4, 5, 9 at best SKIP)')
    a = ap.parse_args(argv)
    for p in (a.candidate, a.stock) + ((a.intended_main,) if a.intended_main else ()) \
            + ((a.intended_dsp,) if a.intended_dsp else ()):
        if not os.path.isfile(p):
            ap.error('no such file: %s' % p)
    name = re.sub(r'[^A-Za-z0-9._-]+', '_', os.path.splitext(os.path.basename(a.candidate))[0])
    a.out = os.path.abspath(a.out or os.path.join(ROOT, 'out', 'brickcheck', name))
    os.makedirs(a.out, exist_ok=True)
    run = Run(a)
    run.cand = None
    run.cand_main = None
    try:
        run.go()
    except Exception as e:
        for c in run.checks.values():
            if not c.parts:
                c.fail('not run: brickcheck crashed: %s' % e)
        traceback.print_exc()
    print_checks(run)
    v = write_reports(run)
    print('reports: %s, %s' % (os.path.join(a.out, 'report.md'), os.path.join(a.out, 'report.json')))
    return {'accept': 0, 'refuse': 1, 'incomplete': 3}[v]


if __name__ == '__main__':
    sys.exit(main())
