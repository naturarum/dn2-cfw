#!/usr/bin/env python3
"""T6.0: the ColdFire side of the frame room, in digikit's emulator (DN2 OS 1.12).

Run from vendor/digikit (it imports digikit's emu package):

  cd vendor/digikit
  DT2_SECTIONS=out/sections/dn2-1.12 uv run python ../../scripts/t60_cf_frame.py \
      ../../out/T3.3/snaps/main720M.snap --passes 200 --out ../../out/T6.0/cf [--poc]

What it does, from a main-screen snapshot (sequencer stopped, the LIGHTHOUSE project):
  1. Reads the live FX setup (*(0x800052a0) + 0x48e4: 101 words + the routing long), the raw
     mirror row 16 (0x800047b2) and the smoothed row 16 (0x800068e4 + 0xcc2), and the DSPI2
     staging buffer 0x80002510 (one 32-bit PUSHR entry per 16-bit frame word).
  2. Watches every write to the TX payload 0x80005e60..0x800068e0 and to the staging buffer
     0x80002510..0x80003af0 while it enters the vector-191 frame handler FUN_40025e36 PASSES
     times by hand (as scripts/t33_frame.py does). Unlike t33_frame.py the DSPI2 driver
     FUN_400cf922 runs for real: DSPI2 SR (0xEC03802C) reads as 0x90000000 (TCF | EOQF, the
     previous transfer done), so the driver rebuilds its PUSHR entries every pass. No eDMA or
     SPI is modelled: the wire frame is rebuilt from the entries' low halves, which is what
     PUSHR transmits.
  3. --poc: a proof of concept, all inside the emulator's RAM (nothing is written to any file
     the unit would load). Test values go into the FX setup's unused words k 70..99 through
     the stock setter FUN_4002585a(value, 0x10, k) (as an FX page turn does), so the stock
     smoother FUN_400db28e glides them like k 25..69. A 30-instruction 68k routine at SRAM
     0x8000F980 (T1.3's free SRAM; core-dn2 would use it for .fast code) replaces the call at
     0x40025e9e: if the previous transfer is done (SR bit 28) it copies the smoothed row-16
     words k 70..99 into the data halves of PUSHR entries 0x540..0x55d (frame bytes
     0xa80..0xabb, the tail the stock driver sends as "tag-only" entries), then jumps to the
     stock driver with the same arguments. The wire frame of the last pass is saved for the
     DSP side (scripts/t60_dsp_poc.py).

Writes NAME-{census,poc}.json, wire frames (.bin, the 0xabc bytes the SHARC receives, big-endian
16-bit words as on the wire) and a text summary. Never touches hardware.
"""
import argparse
import hashlib
import json
import os
import struct
import sys

DK = os.environ.get('DIGIKIT', os.getcwd())
sys.path.insert(0, DK)

from unicorn import UcError, UC_HOOK_MEM_WRITE, UC_HOOK_MEM_READ  # noqa: E402
from unicorn.m68k_const import UC_M68K_REG_PC, UC_M68K_REG_D0  # noqa: E402

MAIN_SHA = 'a098f620'
VECTOR = 191
HANDLER_CALL = 0x40025e9e        # jsr FUN_400cf922.l (6 bytes)
DRIVER = 0x400cf922
COUNTER = 0x40287c38
TX, TX_LEN = 0x80005e60, 0xa80
FRAME_LEN = 0xabc
STAGE, STAGE_END = 0x80002510, 0x80003af0     # 0x578 entries x 4 (room for a 0xaf0-byte payload)
RAW = 0x80003af0
RAW16 = 0x800047b2               # raw mirror row 16, word k at +2k
SMOOTH16 = 0x800068e4 + 0xcc2    # smoothed row 16, word k at +2k
KITPTR = 0x800052a0
FXOFF = 0x48e4
SR = 0xEC03802C
SETTER = 0x4002585a
POC_CODE = 0x8000F980
K0, NK = 70, 30                  # FX setup words k 70..99 -> frame tail words 0..29
TAIL_ENTRY = 0xa80 // 2          # PUSHR entry 0x540


def poc_code():
    """68k (ColdFire ISA_C) routine, hand-assembled; see the module docstring.

        move.l  0xEC03802C,d0        2039 EC03 802C
        btst    #28,d0               0800 001C
        beq.s   go                   67 xx
        lea     SMOOTH16+2*K0,a0     41F9 hhhh llll
        lea     STAGE+4*0x540+2,a1   43F9 hhhh llll
        moveq   #NK-1,d0             70 1D
    1:  move.w  (a0)+,(a1)           3298
        addq.l  #4,a1                5889
        subq.l  #1,d0                5380
        bpl.s   1b                   6A xx
    go: jmp     DRIVER               4EF9 400C F922
    """
    src = SMOOTH16 + 2 * K0
    dst = STAGE + 4 * TAIL_ENTRY + 2
    body = b''
    body += bytes.fromhex('2039') + struct.pack('>I', SR)
    body += bytes.fromhex('0800001c')
    beq_at = len(body)
    body += b'\x67\x00'
    body += bytes.fromhex('41f9') + struct.pack('>I', src)
    body += bytes.fromhex('43f9') + struct.pack('>I', dst)
    body += bytes([0x70, NK - 1])
    loop = len(body)
    body += bytes.fromhex('3298') + bytes.fromhex('5889') + bytes.fromhex('5380')
    body += bytes([0x6a, (loop - (len(body) + 2)) & 0xff])
    go = len(body)
    body += bytes.fromhex('4ef9') + struct.pack('>I', DRIVER)
    body = bytearray(body)
    body[beq_at + 1] = (go - (beq_at + 2)) & 0xff
    return bytes(body)


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument('snapshot')
    p.add_argument('--passes', type=int, default=200)
    p.add_argument('--limit', type=lambda s: int(s, 0), default=5_000_000)
    p.add_argument('--out', required=True)
    p.add_argument('--poc', action='store_true')
    a = p.parse_args(argv)

    from emu import config
    from emu.longrun import build
    img = config.main_image()
    sha = hashlib.sha256(open(img, 'rb').read()).hexdigest()
    if not sha.startswith(MAIN_SHA):
        raise SystemExit('%s is not the 1.12 main OS (sha256 %s)' % (img, sha))
    os.makedirs(a.out, exist_ok=True)
    name = os.path.splitext(os.path.basename(a.snapshot))[0] + ('-poc' if a.poc else '-census')

    m, ev, st, pc, inq, at = build(a.snapshot, softfloat=True, bitmap=True, dsp=True, unblock=True,
                                   deferred_components=('timers',))
    uc = m.uc
    rd = lambda addr, n: bytes(uc.mem_read(addr, n))          # noqa: E731
    w16 = lambda addr: struct.unpack('>H', rd(addr, 2))[0]   # noqa: E731

    kit = struct.unpack('>I', rd(KITPTR, 4))[0]
    fx = kit + FXOFF
    info = {'snapshot': a.snapshot, 'main_sha256': sha, 'kit': '%#010x' % kit, 'fx_setup': '%#010x' % fx}
    info['fx_live_words'] = [w16(fx + 2 * k) for k in range(101)]
    info['fx_routing'] = '%#010x' % struct.unpack('>I', rd(fx + 0xca, 4))[0]
    info['raw16_words'] = [w16(RAW16 + 2 * k) for k in range(101)]
    info['smooth16_words_before'] = [w16(SMOOTH16 + 2 * k) for k in range(101)]
    st0 = rd(STAGE, STAGE_END - STAGE)
    ent = lambda b, i: struct.unpack('>HH', b[4 * i:4 * i + 4])   # noqa: E731
    info['stage_before'] = {'cmd_halves_distinct': sorted({'%#06x' % ent(st0, i)[0] for i in range(0x578)}),
                            'tail_data_halves': ['%#06x' % ent(st0, i)[1] for i in range(0x540, 0x55e)],
                            'beyond_frame_data_halves_nonzero': sum(1 for i in range(0x55e, 0x578) if ent(st0, i)[1])}

    # DSPI2 SR: previous transfer done (TCF | EOQF). Registered after build(), so it runs last.
    def sr_read(uc_, typ, addr, size, val, data):
        uc_.mem_write(SR, struct.pack('>I', 0x90000000))
    uc.hook_add(UC_HOOK_MEM_READ, sr_read, begin=SR, end=SR + 3)

    tx_writes = {}
    stage_writes = {}

    def on_tx(uc_, typ, addr, size, val, data):
        pc_ = uc_.reg_read(UC_M68K_REG_PC)
        for o in range(addr - TX, addr - TX + size):
            if 0 <= o < TX_LEN:
                tx_writes.setdefault(o & ~1, set()).add(pc_)

    def on_stage(uc_, typ, addr, size, val, data):
        pc_ = uc_.reg_read(UC_M68K_REG_PC)
        for o in range(addr - STAGE, addr - STAGE + size, 2):
            i, half = divmod(o, 4)
            stage_writes.setdefault((i, half // 2), set()).add(pc_)

    uc.hook_add(UC_HOOK_MEM_WRITE, on_tx, begin=TX, end=TX + TX_LEN - 1)
    uc.hook_add(UC_HOOK_MEM_WRITE, on_stage, begin=STAGE, end=STAGE_END - 1)

    resume = uc.reg_read(UC_M68K_REG_PC)

    def call(fn, *args):
        """Call a C function from the host with the stack args, return d0."""
        from unicorn.m68k_const import UC_M68K_REG_A7
        sp0 = uc.reg_read(UC_M68K_REG_A7)
        sp = sp0 - 4 * (len(args) + 1)
        uc.mem_write(sp, struct.pack('>I' + 'I' * len(args), resume, *[x & 0xffffffff for x in args]))
        uc.reg_write(UC_M68K_REG_A7, sp)
        uc.emu_start(fn, resume, count=200_000)
        uc.reg_write(UC_M68K_REG_A7, sp0)
        return uc.reg_read(UC_M68K_REG_D0)

    test_vals = {}
    if a.poc:
        code = poc_code()
        assert len(code) < 0x680
        uc.mem_write(POC_CODE, code)
        stock_call = rd(HANDLER_CALL, 6)
        assert stock_call == bytes.fromhex('4eb9400cf922'), stock_call.hex()
        uc.mem_write(HANDLER_CALL, bytes.fromhex('4eb9') + struct.pack('>I', POC_CODE))
        info['poc_code'] = code.hex()
        # test values, as an FX page turn would send them (value << 8): k 70..99 get distinct
        # values; k 27 (chorus HPF, 0 in this kit like k 70) gets the same value as k 70 so their glides can be compared
        for j in range(NK):
            test_vals[K0 + j] = ((100 - 3 * j) & 0x7f) << 8
        for k, v in test_vals.items():
            call(SETTER, v, 0x10, k)
        call(SETTER, test_vals[K0], 0x10, 27)
        info['test_values'] = {str(k): '%#06x' % v for k, v in test_vals.items()}
        info['raw16_after_setter'] = [w16(RAW16 + 2 * k) for k in range(101)]
        tx_writes.clear()
        stage_writes.clear()

    stops = {}
    glide = []
    driver_args = []

    def on_driver(uc_, addr, size, user):
        from unicorn.m68k_const import UC_M68K_REG_A7
        sp = uc_.reg_read(UC_M68K_REG_A7)
        driver_args.append(struct.unpack('>IIIII', rd(sp, 20)))
    at(DRIVER, on_driver)

    wire = None
    for n in range(a.passes):
        uc.mem_write(COUNTER, bytes(4))
        uc.reg_write(UC_M68K_REG_PC, resume)
        m.halt_vec = None
        if not m.raise_vector(VECTOR):
            raise SystemExit('vector %d holds no handler' % VECTOR)
        h = uc.reg_read(UC_M68K_REG_PC)
        stop = None
        try:
            uc.emu_start(h, resume, count=a.limit)
        except UcError as e:
            stop = 'UcError: %s' % e
        if stop is None:
            stop = 'returned' if uc.reg_read(UC_M68K_REG_PC) == resume else 'limit/other'
        stops[stop] = stops.get(stop, 0) + 1
        if stop != 'returned':
            print('pass %d: %s at %#x' % (n, stop, uc.reg_read(UC_M68K_REG_PC)))
            break
        if a.poc:
            glide.append((w16(SMOOTH16 + 2 * 27), w16(SMOOTH16 + 2 * K0), w16(STAGE + 4 * TAIL_ENTRY + 2)))
    # the wire frame the last driver call armed: CITER of the TX TCD = entries; low halves = data
    st1 = rd(STAGE, STAGE_END - STAGE)
    # eDMA TCD 29 (TX): +0 SADDR, +0x10 DADDR, +0x14 CITER, +0x1c BITER, +0x1e CSR
    citer = struct.unpack('>H', rd(0xfc0453b4, 2))[0]
    biter = struct.unpack('>H', rd(0xfc0453bc, 2))[0]
    saddr = struct.unpack('>I', rd(0xfc0453a0, 4))[0]
    daddr = struct.unpack('>I', rd(0xfc0453b0, 4))[0]
    wire = b''.join(st1[4 * i + 2:4 * i + 4] for i in range(FRAME_LEN // 2))
    tx_now = rd(TX, TX_LEN)
    last_eoq = [i for i in range(0x578) if ent(st1, i)[0] & 0x0800]
    info.update({
        'passes': a.passes, 'stops': stops,
        'driver_calls': len(driver_args),
        'driver_args_first': ['%#x' % x for x in driver_args[0]] if driver_args else None,
        'tcd29': {'saddr': '%#010x' % saddr, 'daddr': '%#010x' % daddr, 'citer': citer, 'biter': biter},
        'eoq_entries': last_eoq,
        'cmd_halves_after': sorted({'%#06x' % ent(st1, i)[0] for i in range(FRAME_LEN // 2)}),
        'tx_offsets_written': len(tx_writes),
        'tx_offsets_never_written': ['%#05x' % o for o in range(0, TX_LEN, 2) if o not in tx_writes],
        'stage_writer_pcs': sorted({'%#010x' % p_ for s in stage_writes.values() for p_ in s}),
        'stage_tail_data_written_by': {('%#x' % (2 * i)): sorted('%#010x' % p_ for p_ in s)
                                       for (i, half), s in stage_writes.items() if half == 1 and i >= 0x540},
        'stage_tail_data_halves_after': ['%#06x' % ent(st1, i)[1] for i in range(0x540, 0x55e)],
        'smooth16_words_after': [w16(SMOOTH16 + 2 * k) for k in range(101)],
    })
    # the wire frame's payload part must equal the TX buffer the driver was given
    sent_payload = wire[:TX_LEN]
    info['wire_payload_equals_tx_of_previous_pass'] = None
    if driver_args:
        # the driver copied the buffer as it was at the call (the previous pass's frame); the TX
        # buffer has since been rebuilt, so compare word by word and count differences
        diff = sum(1 for o in range(0, TX_LEN, 2) if sent_payload[o:o + 2] != tx_now[o:o + 2])
        info['wire_payload_words_differing_from_current_tx'] = diff
    if a.poc:
        info['glide_k27_k70_tail0'] = glide[:8] + glide[-4:]
        info['tail_equals_smoothed_k70_99'] = wire[TX_LEN:] == rd(SMOOTH16 + 2 * K0, 2 * NK)
        info['k27_equals_k70_every_pass'] = all(g[0] == g[1] for g in glide)
    with open(os.path.join(a.out, name + '.wire.bin'), 'wb') as f:
        f.write(wire)
    with open(os.path.join(a.out, name + '.tx.bin'), 'wb') as f:
        f.write(tx_now)
    with open(os.path.join(a.out, name + '.json'), 'w') as f:
        json.dump(info, f, indent=1, default=str)
    m.close()
    print(json.dumps({k: info[k] for k in ('stops', 'driver_calls', 'driver_args_first', 'tcd29', 'eoq_entries',
                                          'cmd_halves_after', 'tx_offsets_written', 'tx_offsets_never_written',
                                          'stage_writer_pcs', 'stage_tail_data_written_by')}, indent=1, default=str))
    print('stage tail data halves before:', info['stage_before']['tail_data_halves'][:6], '...')
    print('stage tail data halves after: ', info['stage_tail_data_halves_after'][:6], '...')
    print('fx live k70..100:', info['fx_live_words'][70:], 'routing', info['fx_routing'])
    if a.poc:
        print('glide (smoothed k27, smoothed k70, tail word 0):', info['glide_k27_k70_tail0'])
        print('tail == smoothed k70..99:', info['tail_equals_smoothed_k70_99'],
              ' k27 == k70 every pass:', info['k27_equals_k70_every_pass'])
    return 0


if __name__ == '__main__':
    sys.exit(main())
