#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""T1.7b: dynamic flash watch over the DN2 1.12 in-OS OS-upgrade WRITER.

Feasibility prototype for the brick checker's new dynamic flash watch
(closes red-team gap 3/4, notes/T1.7-redteam.md; invariant from
notes/T1.4-verify-2.md section 6.5).

What it does, without touching hardware and without modifying digikit:

  * resumes the stock warm 800M snapshot (out/T2.4a/warm/warm800M.snap) in
    digikit's emulator with the SAME topology emu.live uses, so the guest's
    cached NOR geometry (page 0x402a12f8, block 0x402a12f4) is already set;
  * installs an OBSERVE-ONLY watch: a code hook on each NOR erase/program/
    protect driver entry (reads the flash address off the stack) and a
    write hook on DSPI0 PUSHR 0xFC05C034 that splits frames on the CONT bit
    and records each command byte -- the same mechanism as
    tools/brickcheck/bc_emuboot.py, but here aimed at a running WRITER;
  * stages a candidate container as the in-OS upgrade receive blob
    ([len:4][checksum:4][ELE3 container ...]) in free DDR, and drives the
    real writer FUN_40139104 over it, so the actual erase/program loops run
    against the real DSPI0 driver;
  * checks every erase (0x20/0xD8) and program (0x02/0x12) WIRE address is
    in [0x80000, 0x380000), and that no protect/OTP command (0xE1/0xE3/0xE4/
    0x42/0x44) is issued -- the T1.4-verify-2 dynamic invariant.

The writer's own validator FUN_400dbdb0 (content checksum + HMAC) is forced
to pass here (a one-instruction shim at 0x4013911a), because the seal is
already proven by brick-checker check 4 and this harness is about the WIRE
ADDRESSES, not the seal. The candidate's container length still drives the
erase/program extent exactly as on the unit, so --mode overlen can feed a
deliberately oversized length to prove the watch flags an out-of-window
erase.

    cd vendor/digikit && uv run python ../../scripts/t17b_flashwatch.py \
        --snap ../../out/T2.4a/warm/warm800M.snap \
        --syx ../../elektron-firmware/Digitone_II_OS1_12.syx \
        --out ../../out/T1.7b/emu/stock --mode stock
"""
import argparse
import collections
import json
import os
import struct
import sys
import time

sys.path.insert(0, os.getcwd())                       # vendor/digikit
from emu import live, config                           # noqa: E402

MAIN_LOAD = 0x40000400
WRITER = 0x40139104
VALIDATOR_RET = 0x4013911A         # addq.l #4,SP just after the validator jsr
PROG_DONE = 0x401391C8             # "Success! Rebooting" -- all writes issued
ERASE_DONE = 0x40139162            # LAB after the erase loop
BLK_CACHE = 0x402A12F4             # cached erase-block size
PAGE_CACHE = 0x402A12F8            # cached page size

BLOB = 0x46900000                  # free DDR (T1.3: 0x46800000-0x47F00000)
STACK = 0x47000000                 # scratch supervisor stack
RET_SENTINEL = 0x47FF0000          # unmapped-ish; caught by a stop hook

# NOR driver write/erase/protect entries (T1.4 / T1.4-verify-2 / bc_emuboot),
# with the stack slot (in longwords past the return address) that carries the
# flash address, and how to read the op.  None = no address arg.
WATCH = {
    0x40134A28: ('WREN 0x06', None),
    0x40134A6A: ('erase 0x20/0xD8', 2),     # (mode, addr): addr at slot 2
    0x4013517E: ('OTP/security program 0x42', 1),
    0x401352B8: ('OTP/security erase 0x44', 1),
    0x401354D6: ('page program 0x02', 1),   # (dest, len, src)
    0x40135618: ('page program 4-byte 0x12', 1),
    0x4013576C: ('subsector erase 0x20', 1),
    0x401357CE: ('block erase 0xD8', 1),     # (addr)
    0x40135850: ('block erase 0xD8 (wdog)', 1),
    0x40135CA6: ('PPB program 0xE3', 1),
    0x40135DC8: ('PPB erase 0xE4', None),
    0x40135F80: ('DYB write 0xE1', 1),
    0x401360A6: ('program wrapper', 1),      # (dest, len, src)
    0x40136122: ('erase-region wrapper', 1),
    0x4013618C: ('erase-region wrapper (wdog)', 1),
    0x40139104: ('in-OS upgrade writer', None),
}
PUSHR = 0xFC05C034
WRITE_CMDS = {0x01: 'WRSR', 0x02: 'PP', 0x06: 'WREN', 0x12: '4PP', 0x20: 'SE4K',
              0x21: '4SE4K', 0x42: 'OTPP', 0x44: 'OTP erase', 0x52: 'BE32K',
              0x60: 'CE', 0xC7: 'CE', 0xD8: 'SE', 0xDC: '4SE', 0xE1: 'DYBWR',
              0xE3: 'PPBP', 0xE4: 'PPBE', 0x2F: 'ASPP', 0xA6: 'PLBWR',
              0x17: 'BRWR', 0xB7: 'BRAC'}
# commands that erase or program the main array (addresses must be in-window)
ARRAY_WRITE = {0x02, 0x12, 0x20, 0x21, 0x52, 0x60, 0xC7, 0xD8, 0xDC}
# commands that must NEVER appear in a main-OS candidate upgrade
FORBIDDEN = {0xE1, 0xE3, 0xE4, 0x42, 0x44, 0x60, 0xC7}
WINDOW = (0x80000, 0x380000)


def install_watch(m, rec):
    from unicorn import UC_HOOK_CODE, UC_HOOK_MEM_WRITE
    from unicorn.m68k_const import UC_M68K_REG_A7
    uc = m.uc
    st = {'start': True, 'cur': None}

    def entry(addr):
        name, slot = WATCH[addr]

        def h(u, pc, size, data):
            sp = u.reg_read(UC_M68K_REG_A7)
            try:
                words = struct.unpack('>8I', u.mem_read(sp, 32))
            except Exception:
                words = ()
            flash = words[slot] & 0xFFFFFF if (slot is not None and len(words) > slot) else None
            rec['calls'].append({'entry': '%#010x' % pc, 'what': name,
                                 'flash': flash,
                                 'args': ['%#010x' % w for w in words[:4]]})
        return h
    for addr in WATCH:
        uc.hook_add(UC_HOOK_CODE, entry(addr), begin=addr, end=addr)

    def pushr(u, access, addr, size, value, data):
        if size != 4 or addr != PUSHR:
            return
        v = value & 0xFFFFFFFF
        cmd = v & 0xFF
        if st['start']:
            st['cur'] = {'cmd': cmd, 'bytes': []}
            rec['commands'][cmd] = rec['commands'].get(cmd, 0) + 1
        elif st['cur'] is not None and len(st['cur']['bytes']) < 5:
            st['cur']['bytes'].append(cmd)
        last = not (v & 0x80000000)                 # CONT bit clear = last frame
        if last and st['cur'] is not None:
            c = st['cur']
            if c['cmd'] in ARRAY_WRITE or c['cmd'] in FORBIDDEN:
                wire = None
                if len(c['bytes']) >= 3:
                    wire = (c['bytes'][0] << 16) | (c['bytes'][1] << 8) | c['bytes'][2]
                if len(rec['txns']) < 4000:
                    rec['txns'].append({'cmd': '%#04x' % c['cmd'],
                                        'name': WRITE_CMDS.get(c['cmd'], '?'),
                                        'wire': wire})
            st['cur'] = None
        st['start'] = last
    uc.hook_add(UC_HOOK_MEM_WRITE, pushr, begin=PUSHR, end=PUSHR + 3)


def force_validator(m):
    from unicorn import UC_HOOK_CODE
    from unicorn.m68k_const import UC_M68K_REG_D0
    m.uc.hook_add(UC_HOOK_CODE,
                  lambda u, a, s, d: u.reg_write(UC_M68K_REG_D0, 1),
                  begin=VALIDATOR_RET, end=VALIDATOR_RET)


# Console print/format helpers the writer calls to put "Writing Flash" and
# "Success! Rebooting" on screen.  In a bare writer-only run (no scheduler)
# these block on a console lock / scheduler trap that nothing services, which
# stops the run before the program loop.  They are cosmetic text, not flash
# safety.  Stub each to an immediate return (emulate `rts`): the writer's own
# inline register setup (dest base 0x80000, page count, source pointer) still
# runs, so the real program loop and its wire addresses are unaffected.
PRINT_HELPERS = (0x401CEAB8, 0x401C0BA6, 0x401CD008)


def stub_prints(m):
    from unicorn import UC_HOOK_CODE
    from unicorn.m68k_const import UC_M68K_REG_A7, UC_M68K_REG_PC, UC_M68K_REG_D0
    uc = m.uc

    def ret(u, a, s, d):
        sp = u.reg_read(UC_M68K_REG_A7)
        r = struct.unpack('>I', u.mem_read(sp, 4))[0]
        u.reg_write(UC_M68K_REG_A7, sp + 4)
        u.reg_write(UC_M68K_REG_PC, r)
        u.reg_write(UC_M68K_REG_D0, 0)
    for a in PRINT_HELPERS:
        uc.hook_add(UC_HOOK_CODE, ret, begin=a, end=a)


def build_blob(m, container_bytes, declared_len):
    uc = m.uc
    for p in range(0, len(container_bytes) + 0x200000, 0x100000):
        m.ensure(BLOB + p)
    m.ensure(STACK - 0x100000)
    m.ensure(STACK)
    uc.mem_write(BLOB, struct.pack('>II', declared_len & 0xFFFFFFFF, 0))
    uc.mem_write(BLOB + 8, container_bytes)


def run(snap, syx, out_dir, mode, container_syx=None):
    os.makedirs(out_dir, exist_ok=True)
    # The warm snapshot carries the STOCK flash (its manifest pins the flash
    # sha256), so LiveEmulator must restore with the snapshot's own .syx (stock).
    # The CANDIDATE container is staged separately as the upgrade blob the writer
    # reads; the writer code is byte-pinned to stock by brick-checker check 6, so
    # running it over the candidate's container is faithful. container_syx is the
    # candidate; syx (for the restore) is stock.
    container_syx = container_syx or syx
    emu = live.LiveEmulator(snap, syx=syx, ssi0_hz=live.SSI0_HZ_DN2, ips=150e6,
                            midi=False, dsp_sync=True)
    m, ev, pc, timers, profile, stepper = emu._setup()
    uc = m.uc
    from unicorn import UC_HOOK_CODE
    from unicorn.m68k_const import UC_M68K_REG_A7, UC_M68K_REG_SR
    # _setup installed emu.live's fast-stepper, whose UC_HOOK_BLOCK stops
    # emu_start the instant its instruction budget hits 0.  We drive the
    # writer with a bare emu_start, so hand the stepper a large budget first;
    # our own code hooks (below) are what actually stop the run.
    stepper.blocks = 0
    stepper.left = 1 << 30
    # The emulator HLEs the SPI NOR *read* (FUN_401296fe) during boot, so the
    # real driver's JEDEC/SFDP geometry probe (FUN_40135a7c) never runs and
    # the warm snapshot's cache reads -1.  Seed the recognised Spansion
    # S25FL-class geometry (T1.4: erase block 0x40000, page 0x200) so the
    # writer and FUN_40135a7c take their cached path -- exactly the geometry
    # the unit has.
    geom_before = (struct.unpack('>I', uc.mem_read(BLK_CACHE, 4))[0],
                   struct.unpack('>I', uc.mem_read(PAGE_CACHE, 4))[0])
    uc.mem_write(BLK_CACHE, struct.pack('>I', 0x40000))
    uc.mem_write(PAGE_CACHE, struct.pack('>I', 0x200))
    blk = struct.unpack('>I', uc.mem_read(BLK_CACHE, 4))[0]
    page = struct.unpack('>I', uc.mem_read(PAGE_CACHE, 4))[0]

    from dt2.container import container
    cbytes = container(container_syx)
    clen = len(cbytes)
    if mode == 'stock':
        declared = clen
    elif mode == 'overlen':
        # a length that, if the writer honoured it, would erase up to and
        # through the settings store at 0x380000 and on toward the bootstrap.
        declared = 0xF90000
    else:
        raise SystemExit('mode must be stock|overlen')

    rec = {'mode': mode, 'calls': [], 'commands': {}, 'txns': [],
           'blk': blk, 'page': page, 'geom_before_seed': ['%#010x' % g for g in geom_before],
           'container_len': clen, 'declared_len': declared}
    install_watch(m, rec)
    force_validator(m)
    stub_prints(m)

    # stop cleanly once the writer has issued all its wire transactions
    stop_at = ERASE_DONE if mode == 'overlen' else PROG_DONE

    def stop(u, a, s, d):
        rec['stopped_at'] = '%#010x' % a
        u.emu_stop()
    uc.hook_add(UC_HOOK_CODE, stop, begin=stop_at, end=stop_at)
    # safety net: an rts to the sentinel return address also stops the run
    m.ensure(RET_SENTINEL & ~0xFFFFF)

    build_blob(m, cbytes if mode == 'stock' else cbytes, declared)

    # stack frame the writer expects: SP -> [ret][arg0 = blob ptr]
    uc.reg_write(UC_M68K_REG_A7, STACK)
    uc.mem_write(STACK, struct.pack('>II', RET_SENTINEL, BLOB))
    uc.reg_write(UC_M68K_REG_SR, 0x2700)             # supervisor, ints off

    t0 = time.time()
    stop_reason = 'ok'
    try:
        # stop when the writer rts's to the sentinel, or at our PROG/ERASE
        # done code hook, whichever first; timeout is a wall-clock safety net.
        uc.emu_start(WRITER, RET_SENTINEL, timeout=120 * 1_000_000)
    except Exception as e:                           # UcError on a fault
        stop_reason = str(e)
    rec['seconds'] = round(time.time() - t0, 2)
    rec['stop_reason'] = stop_reason

    verdict = evaluate(rec)
    rec['verdict'] = verdict
    rec['commands'] = {'%#04x' % k: v for k, v in sorted(rec['commands'].items())}
    with open(os.path.join(out_dir, 'watch.json'), 'w') as fh:
        json.dump(rec, fh, indent=1)
    report(rec, out_dir)
    return rec


def evaluate(rec):
    lo, hi = WINDOW
    erases, programs, out_of_window, forbidden = [], [], [], []
    for c in rec['calls']:
        what = c['what']
        fl = c['flash']
        if 'erase' in what and fl is not None:
            erases.append(fl)
            if not (lo <= fl < hi):
                out_of_window.append((what, fl))
        if ('program' in what or what == 'program wrapper') and fl is not None:
            programs.append(fl)
            if not (lo <= fl < hi):
                out_of_window.append((what, fl))
        if what in ('DYB write 0xE1', 'PPB program 0xE3', 'PPB erase 0xE4',
                    'OTP/security program 0x42', 'OTP/security erase 0x44'):
            forbidden.append(what)
    # cross-check from the raw DSPI0 PUSHR stream
    for t in rec['txns']:
        cmdb = int(t['cmd'], 16)
        if cmdb in FORBIDDEN:
            forbidden.append('PUSHR cmd %s %s' % (t['cmd'], t['name']))
        if cmdb in ARRAY_WRITE and t['wire'] is not None and not (lo <= t['wire'] < hi):
            out_of_window.append(('PUSHR %s' % t['name'], t['wire']))
    ok = not out_of_window and not forbidden
    oow_addrs = sorted({a for _, a in out_of_window})
    hits_store = [a for a in oow_addrs if 0x380000 <= a < 0x400000]
    hits_bootstrap = [a for a in oow_addrs if a < 0x80000]   # 0x0-0x80000: reset+bootstrap
    return {'accept': ok, 'erase_count': len(set(erases)), 'program_count': len(set(programs)),
            'erase_addrs': ['%#08x' % a for a in sorted(set(erases))],
            'program_min': ('%#08x' % min(programs)) if programs else None,
            'program_max': ('%#08x' % max(programs)) if programs else None,
            'out_of_window_count': len(set((w, a) for w, a in out_of_window)),
            'out_of_window_addrs': ['%#08x' % a for a in oow_addrs],
            'hits_settings_store': ['%#08x' % a for a in hits_store],
            'hits_bootstrap_region': ['%#08x' % a for a in hits_bootstrap],
            'forbidden': sorted(set(forbidden))}


def report(rec, out_dir):
    v = rec['verdict']
    lines = []
    lines.append('# T1.7b dynamic flash watch -- in-OS upgrade writer (%s)' % rec['mode'])
    lines.append('')
    lines.append('cached geometry: erase block %#x, page %#x' % (rec['blk'], rec['page']))
    lines.append('container %d bytes; declared length fed to writer %#x (%d)'
                 % (rec['container_len'], rec['declared_len'], rec['declared_len']))
    lines.append('ran %.2fs, stopped at %s (%s)'
                 % (rec['seconds'], rec.get('stopped_at', rec.get('stop_reason')),
                    rec['stop_reason']))
    lines.append('')
    lines.append('DSPI0 PUSHR command histogram: %s' % rec['commands'])
    lines.append('erase wire addresses (%d unique): %s' % (v['erase_count'], v['erase_addrs']))
    lines.append('program wire range: %s .. %s' % (v['program_min'], v['program_max']))
    lines.append('')
    lines.append('invariant: every erase/program wire address in [0x80000,0x380000),'
                 ' no 0xE1/0xE3/0xE4/0x42/0x44')
    oow = v['out_of_window_addrs']
    shown = oow[:6] + (['...(+%d more)' % (len(oow) - 6)] if len(oow) > 6 else [])
    lines.append('  out-of-window wire addresses: %s' % (shown or 'none'))
    lines.append('  reaches settings store 0x380000/0x3c0000: %s'
                 % (v['hits_settings_store'] or 'no'))
    lines.append('  reaches reset+bootstrap region (< 0x80000): %s'
                 % (v['hits_bootstrap_region'] or 'no'))
    lines.append('  forbidden protect/OTP ops: %s' % (v['forbidden'] or 'none'))
    lines.append('')
    lines.append('VERDICT: %s' % ('ACCEPT (all writes in window, no protect/OTP)'
                                   if v['accept'] else 'REFUSE'))
    txt = '\n'.join(lines) + '\n'
    with open(os.path.join(out_dir, 'report.md'), 'w') as fh:
        fh.write(txt)
    print(txt)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--snap', required=True)
    ap.add_argument('--syx', required=True, help='the .syx the snapshot was built from (stock); '
                    'used to restore the snapshot')
    ap.add_argument('--container-syx', help='the candidate .syx whose container is staged as the '
                    'upgrade blob (default: --syx). Lets a modded candidate be watched against the '
                    'stock warm snapshot, since check 6 pins the writer+driver to stock')
    ap.add_argument('--out', required=True)
    ap.add_argument('--mode', default='stock', choices=('stock', 'overlen'))
    a = ap.parse_args()
    run(a.snap, a.syx, a.out, a.mode, container_syx=a.container_syx)


if __name__ == '__main__':
    main()
