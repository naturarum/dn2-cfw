#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""T6.5: the boot order of a param-grow build, watched from reset in digikit's emulator.

    cd vendor/digikit && DT2_SECTIONS=../../out/T6.5/emu/GT1T/sections uv run python \\
        ../../scripts/t65_bootwatch.py --syx ../../out/T6.5/GT1T.syx --limit 400000000 \\
        --json ../../out/T6.5/bootwatch/GT1T-400M.json [--resume SNAP --guirun]

Boots the main OS from reset with emu.dspboot.run (the path `emu.checkpoint make` builds the
snapshot ladder on, so instruction counts compare with the ladder's) and records, with the
instruction count n and the PC:
  - every read of the stock descriptor table in the image (0x401f83a4, 0x141 x 0x3c), by PC,
    with the first and last n of each PC;
  - every write to param-grow's region (0x47D00000..0x47D06b00: the grown table, the new rows'
    display entries and their flags), by PC, and the first read of it (n, PC);
  - the first read of the stock display table (0x4243425c.., built in RAM by the static
    initializer) and the first and last write to it;
  - when these run: pg_boot (entry, and its jmp to the C start), pg_sinit (entry and return),
    the static initializer 0x400c2c4e, the boot-time builder of the slot and CC/NRPN maps
    0x400dc634, the display accessor 0x400c257c (first call), the cache constructor 0x4018e93c
    (and the size it asks operator new for), the first ParameterUpdateCache set/get.
The order these must show: pg_boot ends before anything else reads the table; the display
table's first read comes after pg_sinit; nothing but pg_boot and pg_sinit writes the region.
Nothing in digikit is edited; emu.dspboot is imported. With --resume SNAP --guirun the same
watches run from a snapshot under tools/guirun.py's configuration (the ladder's second stage).
"""
import argparse
import collections
import json
import os
import struct
import sys

DIGIKIT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'vendor', 'digikit')
sys.path.insert(0, DIGIKIT)
sys.path.insert(0, os.path.join(DIGIKIT, 'tools'))

DESC0, ROW, ROWS0 = 0x401f83a4, 0x3c, 0x141
DISP, DISP_END = 0x4243425c, 0x4243425c + 0x141 * 0x44
PG, PG_END = 0x47D00000, 0x47D06b00
MARKS = {0x400c2c4e: 'static initializer (display table, page records) entry',
         0x400dc634: 'builder of the slot and CC/NRPN maps entry',
         0x400c257c: 'display accessor',
         0x4018e93c: 'ParameterUpdateCache singleton',
         0x400f5264: 'cache set', 0x400f51a4: 'cache get', 0x400f5352: 'cache flush',
         0x40186fa2: 'the C start'}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--syx', required=True)
    ap.add_argument('--limit', type=lambda s: int(float(s)), default=400_000_000)
    ap.add_argument('--json', required=True)
    ap.add_argument('--map', help='the build\'s .map.json (pg_boot, pg_sinit); default: next to --syx '
                    'under out/T6.5/build/NAME-main.syx.map.json')
    a = ap.parse_args()
    from emu import config, dspboot
    from unicorn import UC_HOOK_MEM_READ, UC_HOOK_MEM_WRITE, UC_HOOK_CODE
    from unicorn.m68k_const import UC_M68K_REG_PC, UC_M68K_REG_A7

    name = os.path.basename(a.syx)[:-4]
    mpath = a.map or os.path.join(os.path.dirname(os.path.abspath(a.syx)), 'build', name + '-main.syx.map.json')
    marks = dict(MARKS)
    if os.path.exists(mpath):
        mp = {k: int(v, 16) for k, v in json.load(open(mpath)).items()}
        for k in ('pg_boot', 'pg_sinit', 'pg_disp_get'):
            if k in mp:
                marks[mp[k]] = k + ' entry'
        # pg_boot's last instruction: its jmp to the C start; pg_sinit's rts
        img_end = None
        marks_end = {}
    else:
        mp = {}
    box = {}
    first = {}
    calls = collections.Counter()
    stock_reads = {}
    pg_writes = {}
    pg_first_read = []
    disp = {'first_read': None, 'first_write': None, 'last_write': None, 'writes': 0}
    cache_size = []

    def n():
        return box['st']['n']

    def pre_start(m):
        uc = m.uc

        def rd_stock(uc_, acc, addr, size, value, user):
            pc = uc_.reg_read(UC_M68K_REG_PC)
            e = stock_reads.setdefault(pc, [0, n(), n()])
            e[0] += 1
            e[2] = n()
        uc.hook_add(UC_HOOK_MEM_READ, rd_stock, begin=DESC0, end=DESC0 + ROWS0 * ROW - 1)

        def wr_pg(uc_, acc, addr, size, value, user):
            pc = uc_.reg_read(UC_M68K_REG_PC)
            e = pg_writes.setdefault(pc, [0, n(), n(), addr, addr])
            e[0] += 1
            e[2] = n()
            e[3] = min(e[3], addr)
            e[4] = max(e[4], addr)

        def rd_pg(uc_, acc, addr, size, value, user):
            if not pg_first_read:
                pg_first_read.append((n(), '0x%08x' % uc_.reg_read(UC_M68K_REG_PC), '0x%08x' % addr))
        uc.hook_add(UC_HOOK_MEM_WRITE, wr_pg, begin=PG, end=PG_END - 1)
        uc.hook_add(UC_HOOK_MEM_READ, rd_pg, begin=PG, end=PG_END - 1)

        def rd_disp(uc_, acc, addr, size, value, user):
            if disp['first_read'] is None:
                disp['first_read'] = (n(), '0x%08x' % uc_.reg_read(UC_M68K_REG_PC), '0x%08x' % addr)

        def wr_disp(uc_, acc, addr, size, value, user):
            disp['writes'] += 1
            if disp['first_write'] is None:
                disp['first_write'] = (n(), '0x%08x' % uc_.reg_read(UC_M68K_REG_PC))
            disp['last_write'] = (n(), '0x%08x' % uc_.reg_read(UC_M68K_REG_PC))
        uc.hook_add(UC_HOOK_MEM_READ, rd_disp, begin=DISP, end=DISP_END - 1)
        uc.hook_add(UC_HOOK_MEM_WRITE, wr_disp, begin=DISP, end=DISP_END - 1)

        def mark(uc_, addr, size, user):
            calls[addr] += 1
            if addr not in first:
                first[addr] = n()
                print('[boot] %12d  0x%08x %s' % (n(), addr, marks[addr]), flush=True)
            if addr == 0x4018e93c and not cache_size:
                cache_size.append('0x%x' % struct.unpack('>I', bytes(uc_.mem_read(0x4018e950, 4)))[0])
        for addr in marks:
            uc.hook_add(UC_HOOK_CODE, mark, begin=addr, end=addr)

    syx = config.firmware(a.syx)
    img = open(config.main_image(), 'rb').read()
    m, st, stop = dspboot.run(syx, img, limit=a.limit, machine_out=box, pre_start=pre_start)
    out = {
        'syx': a.syx, 'limit': a.limit, 'stop': stop, 'n': st['n'],
        'marks': {('0x%08x' % k): {'what': marks[k], 'first_n': first.get(k), 'calls': calls.get(k, 0)}
                  for k in marks},
        'stock_table_reads': {('0x%08x' % pc): {'count': c, 'first_n': f, 'last_n': l}
                              for pc, (c, f, l) in sorted(stock_reads.items(), key=lambda kv: kv[1][1])},
        'region_writes': {('0x%08x' % pc): {'count': c, 'first_n': f, 'last_n': l, 'lo': '0x%08x' % lo,
                                             'hi': '0x%08x' % hi}
                          for pc, (c, f, l, lo, hi) in sorted(pg_writes.items(), key=lambda kv: kv[1][1])},
        'region_first_read': pg_first_read[0] if pg_first_read else None,
        'display_table': disp,
        'cache_new_size': cache_size[0] if cache_size else None,
        'map': {k: '0x%08x' % v for k, v in mp.items() if k.startswith('pg_')},
    }
    os.makedirs(os.path.dirname(os.path.abspath(a.json)), exist_ok=True)
    json.dump(out, open(a.json, 'w'), indent=1)
    print('stop: %s after %d instructions' % (stop, st['n']))
    print('stock table reads by PC (first n): %s' % ', '.join(
        '%s x%d @%d' % (pc, v['count'], v['first_n']) for pc, v in list(out['stock_table_reads'].items())[:12]))
    print('region writes by PC: %s' % ', '.join(
        '%s x%d @%d..%d' % (pc, v['count'], v['first_n'], v['last_n']) for pc, v in out['region_writes'].items()))
    print('region first read: %s; display table first read %s, writes %d (%s..%s)' % (
        out['region_first_read'], disp['first_read'], disp['writes'], disp['first_write'], disp['last_write']))
    return 0


if __name__ == '__main__':
    sys.exit(main())
