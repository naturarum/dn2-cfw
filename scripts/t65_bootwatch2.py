#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""T6.5: the second half of the boot watch (the ladder's 400M -> 800M stage, tools/guirun.py's
configuration), with the same memory watches as scripts/t65_bootwatch.py.

    cd vendor/digikit && DT2_SECTIONS=../../out/T6.5/emu/GT1T/sections uv run python \\
        ../../scripts/t65_bootwatch2.py ../../out/T6.5/emu/GT1T/snapshots/boot400M.snap \\
        --syx ../../out/T6.5/GT1T.syx --limit 800000000 --json OUT.json

guirun's own arguments are passed through. Records reads of the stock descriptor table by PC,
writes to param-grow's region (0x47D00000..0x47D06b00) by PC, the first read of it, the display
accessor's calls (stock rows / new rows, by the row asked for) and the ParameterUpdateCache's
set/get calls whose row is past the stock rows. guirun is imported, not changed.
"""
import collections
import json
import os
import struct
import sys

DIGIKIT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'vendor', 'digikit')
sys.path.insert(0, DIGIKIT)
sys.path.insert(0, os.path.join(DIGIKIT, 'tools'))

DESC0, ROW, ROWS0 = 0x401f83a4, 0x3c, 0x141
PG, PG_END = 0x47D00000, 0x47D06b00
ACCESSOR, SET, GET = 0x400c257c, 0x400f5264, 0x400f51a4


def main():
    argv = sys.argv[1:]
    out = argv[argv.index('--json') + 1]
    del argv[argv.index('--json'):argv.index('--json') + 2]
    import guirun
    from unicorn import UC_HOOK_MEM_READ, UC_HOOK_MEM_WRITE, UC_HOOK_CODE
    from unicorn.m68k_const import UC_M68K_REG_PC, UC_M68K_REG_A7
    rec = {'stock_reads': collections.Counter(), 'pg_writes': collections.Counter(), 'pg_first_read': None,
           'accessor_rows': collections.Counter(), 'cache_new_rows': collections.Counter()}

    def install(m):
        uc = m.uc

        def rd_stock(uc_, acc, addr, size, value, user):
            rec['stock_reads'][uc_.reg_read(UC_M68K_REG_PC)] += 1

        def wr_pg(uc_, acc, addr, size, value, user):
            rec['pg_writes'][uc_.reg_read(UC_M68K_REG_PC)] += 1

        def rd_pg(uc_, acc, addr, size, value, user):
            if rec['pg_first_read'] is None:
                rec['pg_first_read'] = ('0x%08x' % uc_.reg_read(UC_M68K_REG_PC), '0x%08x' % addr)

        def acc_hook(uc_, addr, size, user):
            sp = uc_.reg_read(UC_M68K_REG_A7)
            row = struct.unpack('>I', bytes(uc_.mem_read(sp + 4, 4)))[0]
            rec['accessor_rows']['stock' if row < ROWS0 else '0x%x' % row] += 1

        def cache_hook(uc_, addr, size, user):
            sp = uc_.reg_read(UC_M68K_REG_A7)
            lane, row = struct.unpack('>II', bytes(uc_.mem_read(sp + 8, 8)))
            if row >= ROWS0:
                rec['cache_new_rows'][(lane, row)] += 1
        uc.hook_add(UC_HOOK_MEM_READ, rd_stock, begin=DESC0, end=DESC0 + ROWS0 * ROW - 1)
        uc.hook_add(UC_HOOK_MEM_WRITE, wr_pg, begin=PG, end=PG_END - 1)
        uc.hook_add(UC_HOOK_MEM_READ, rd_pg, begin=PG, end=PG_END - 1)
        uc.hook_add(UC_HOOK_CODE, acc_hook, begin=ACCESSOR, end=ACCESSOR)
        uc.hook_add(UC_HOOK_CODE, cache_hook, begin=SET, end=SET)
        uc.hook_add(UC_HOOK_CODE, cache_hook, begin=GET, end=GET)

    orig = guirun.build

    def build(*a, **k):
        r = orig(*a, **k)
        install(r[0])
        return r
    guirun.build = build
    sys.argv = [sys.argv[0]] + argv
    try:
        rc = guirun.main()
    finally:
        res = {'stock_table_reads': {'total': sum(rec['stock_reads'].values()),
                                     'by_pc': {'0x%08x' % k: v for k, v in rec['stock_reads'].most_common()}},
               'region_writes': {'0x%08x' % k: v for k, v in rec['pg_writes'].most_common()},
               'region_first_read': rec['pg_first_read'],
               'accessor_rows': dict(rec['accessor_rows']),
               'cache_calls_past_stock_rows': {'%d/0x%x' % k: v for k, v in rec['cache_new_rows'].items()}}
        os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
        json.dump(res, open(out, 'w'), indent=1)
        print('[t65] stock table reads %d; region writes %s; region first read %s; accessor %s; '
              'cache calls past the stock rows %s' % (
                  res['stock_table_reads']['total'], res['region_writes'], res['region_first_read'],
                  res['accessor_rows'], res['cache_calls_past_stock_rows']), flush=True)
    return rc


if __name__ == '__main__':
    sys.exit(main())
