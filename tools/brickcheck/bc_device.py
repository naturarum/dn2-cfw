# SPDX-License-Identifier: GPL-2.0-or-later
"""The unit's own code under Unicorn (brick checker checks 4, 5 and 9).

Imports digikit's tools/acceptoracle.py (T1.2) and tools/recovery_harness.py
(T1.5) and calls their functions; nothing in digikit is changed. Every
routine that runs is the STOCK file's (bootstrap, section 4, main OS): the
unit checks and unpacks an incoming file with the code it already has, and
check 2 separately requires the candidate's sections 2 and 4 to be stock.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
DIGIKIT = os.path.join(ROOT, 'vendor', 'digikit')
for p in (DIGIKIT, os.path.join(DIGIKIT, 'tools')):
    if p not in sys.path:
        sys.path.insert(0, p)

import acceptoracle as ao          # noqa: E402
import recovery_harness as rh      # noqa: E402
from dt2 import elz                # noqa: E402
from dt2.container import decode_syx  # noqa: E402

# landmarks that must never be reached on any recovery run (T1.5)
RED_FLAGS = ('flash_erase', 'flash_program', 'abort', 'halt_dram')


def as_candidate(stream):
    """Decoded stream -> the dict acceptoracle's loaders take."""
    return ao.parse_stream(stream)


class Device:
    def __init__(self, stock_path, work_dir):
        self.stock = ao.parse(stock_path)
        self.boot = elz.depack_section(ao.stored(self.stock, 2))
        self.img = elz.depack_section(ao.stored(self.stock, 3))
        try:
            self.R = ao.resolve_boot(self.boot)
            dep = self.boot[self.R['depack'][0] - ao.BOOT_BASE:
                            self.R['depack'][0] - ao.BOOT_BASE + self.R['depack_len'][0]]
            self.M = ao.resolve_main(self.img, dep)
            self.upd = ao.stored(self.stock, 4)
            self.U = ao.resolve_upd(self.upd, dep, self.boot, self.R)
        except SystemExit as e:          # acceptoracle reports a failed signature this way
            raise RuntimeError('acceptoracle could not resolve the stock addresses: %s' % e)
        os.makedirs(work_dir, exist_ok=True)
        self.boot_path_file = os.path.join(work_dir, 'stock-bootstrap.bin')
        with open(self.boot_path_file, 'wb') as fh:
            fh.write(self.boot)
        rh.BOOT_IMAGE = self.boot_path_file
        self.load = self.stock['sections'][3][2]
        self._recovery_ref = None

    def addresses(self):
        pick = {
            'bootstrap verify-and-commit': self.R['commit'],
            'bootstrap trailer check': self.R['hmac'],
            'bootstrap boot path from': self.R['boot_from'],
            'bootstrap loader': self.R['loader'],
            'bootstrap depacker': self.R['depack'],
            'bootstrap stage': self.R['stage'],
            'section 4 loader': self.U['loader'],
            'section 4 depacker': self.U['depack'],
            'section 4 stage': self.U['stage'],
            'main OS validator': self.M['validator'],
            'main OS content checksum': self.M['os_checksum'],
            'main OS trailer check': self.M['os_hmac'],
        }
        return {k: {'address': '%#010x' % v, 'how found': how} for k, (v, how) in pick.items()}

    # -- check 4 ----------------------------------------------------------

    def bootstrap_commit(self, stream):
        """0x80003c9c from its entry; stops at the abort screen or at the
        first instruction of UPGRADING (before any flash code)."""
        return ao.boot_commit(self.boot, self.R, stream)

    def os_validator(self, stream):
        """The main OS's upgrade validator 0x400dbdb0 on [len][csum][container]."""
        m = ao.machine(self.img, ao.MAIN_BASE)
        for a in range(ao.SCRATCH, ao.SCRATCH + len(stream) + ao.PAGE, ao.PAGE):
            m.ensure(a)
        m.uc.mem_write(ao.SCRATCH, stream)
        v = ao.call(m, self.M['validator'][0], [ao.SCRATCH],
                    stack_top=ao.STACK + ao.PAGE - 0x100, limit=ao.LIMIT)
        names = {1: 'pass', 3: 'Checksum failed (content checksum)',
                 4: 'Checksum failed (trailer)', 5: 'Power adapter must be connected',
                 6: 'Unsupported downgrade'}
        return {'returned': v, 'means': names.get(v, 'unknown')}

    # -- check 5 ----------------------------------------------------------

    def boot_path(self, stream, want):
        return ao.boot_path(self.boot, self.R, as_candidate(stream), want)

    def section4_path(self, stream, want):
        return ao.updater_path(self.upd, self.U, as_candidate(stream), want)

    def gaps(self, stored3):
        out = {}
        for stage in (self.R['stage'][0], self.U['stage'][0]):
            g, n = ao.inplace_gap(stored3, stage, self.load)
            out['%#010x' % stage] = {'gap': g, 'output_bytes': n}
        return out

    # -- check 9 ----------------------------------------------------------

    def _recovery(self, container):
        want = ('menu_keypoll', 'os_upgrade', 'loader', 'run_sec4')
        out = {}
        for mode in ('func', 'none', 'silent'):
            st = rh.run(container, rh.Panel(mode), want=want)
            out[mode] = st
        out['osupgrade'] = rh.run(container, rh.Panel('func'), want=('recv_spin',), osupgrade=True)
        return out

    def recovery(self, container):
        """T1.5's harness on the candidate's container and on stock's.
        -> (ok, findings, summary)."""
        if self._recovery_ref is None:
            self._recovery_ref = self._recovery(self.stock['container'])
        ref = self._recovery_ref
        got = self._recovery(container)
        bad = []
        summ = {}
        for k, st in got.items():
            r = ref[k]
            summ[k] = {
                'stop': st['stop'], 'stop_pc': st['stop_pc'], 'landmarks': st['landmarks'],
                'boot_flags': st['boot_flags'],
                'lit_pixels': rh.lit_count(st.get('panel_compose')),
                'stock_stop': r['stop'], 'stock_lit_pixels': rh.lit_count(r.get('panel_compose')),
                'screen_same_as_stock': st.get('panel_compose') == r.get('panel_compose'),
                'flash_reads': st['flash_reads'][:8],
            }
            if k == 'osupgrade':
                summ[k]['flash_reads_after_os_upgrade'] = st['reads_after_osupgrade']
            red = [x for x in RED_FLAGS if x in st['landmarks']]
            if red:
                bad.append('%s: reached %s' % (k, ', '.join(red)))
        f, n, s, o = got['func'], got['none'], got['silent'], got['osupgrade']
        if f['stop'] != 'menu_keypoll' or 'menu' not in f['landmarks'] \
                or 'drawflush' not in f['landmarks']:
            bad.append('FUNC held: stopped at %s (%s), not in the STARTUP menu key loop 0x800013ac'
                       % (f['stop'], f['stop_pc']))
        if f.get('panel_compose') != ref['func'].get('panel_compose'):
            bad.append('FUNC held: the menu screen differs from stock\'s (%d lit pixels, stock %d)'
                       % (rh.lit_count(f.get('panel_compose')),
                          rh.lit_count(ref['func'].get('panel_compose'))))
        if n['stop'] != 'loader' or 'menu' in n['landmarks']:
            bad.append('no key: stopped at %s (%s), landmarks %s; stock goes to the loader '
                       '0x80000596 without the menu' % (n['stop'], n['stop_pc'], n['landmarks']))
        if s['stop'] != ref['silent']['stop']:
            bad.append('silent panel: stopped at %s, stock at %s' % (s['stop'], ref['silent']['stop']))
        if o['stop'] != 'recv_spin' or o['reads_after_osupgrade']:
            bad.append('OS UPGRADE: stopped at %s, flash reads after entering it %s'
                       % (o['stop'], o['reads_after_osupgrade']))
        return not bad, bad, summ


def decode_digikit(path):
    """digikit's decoder, to cross-check bc_syx's."""
    return decode_syx(path)


def depack(stored):
    return elz.depack_section(stored)


