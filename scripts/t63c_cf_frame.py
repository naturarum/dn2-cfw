#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
# The resume and raise_vector follow digikit's tools/sharcframe.py (Em D, GPL-2.0-or-later), as
# scripts/t60_cf_frame.py does.
"""T6.3 CMP2: comp-link-dn2 0.1's frame tail (vendor/elekloader/examples/comp-link-dn2/tail.s) in the vector-191
frame handler, 300 passes; out/T6.4/store-frame/scripts/cf_frame.py (the T6.4 prototype's harness) for CMP2.

    cd vendor/digikit && DT2_SECTIONS=../../out/T6.3/CMP1/emu/C1EM/sections uv run python \\
        ../../scripts/t63c_cf_frame.py C1EM ../../out/T6.3/CMP1/emu/C1EM/warm800M.snap --passes 300
    cd vendor/digikit && DT2_SECTIONS=../../out/T6.3/CMP2/emu/C2EM/sections uv run python \\
        ../../scripts/t63c_cf_frame.py C2EM ../../out/T6.3/CMP2/emu/C2EM/warm800M.snap --passes 300

C1EM: CMP1's warm snapshot; the hook run gets tail.s assembled at 0x47D20000 (.bss 0x47D20400: free DDR,
outside param-grow's and core's regions; out/T6.3/CMP2/cf/tail_reloc.*), whose bytes are CMP2's linked
compl_tail but for the two compl_seq operands (checked here against out/T6.3/CMP2/main.bin), and the jsr at
0x40025e9e pointed at it. C2EM: CMP2's own warm snapshot; the hook run is the image as built, the base run has
the stock jsr 0x400cf922 put back at 0x40025e9e (so the base is CMP1's frame handler on CMP2's RAM).

Two runs from the same snapshot, in one process, each on a freshly built Machine:
  base: test values go into the FX setup's k 70..96 through the stock setter FUN_4002585a (as a
        page turn on GT01's compressor rows would), k 27 gets k 70's value; then the vector-191
        frame handler FUN_40025e36 runs PASSES times with the real DSPI2 driver (SR forced to
        "done", as t60_cf_frame.py does);
  hook: the same, with cf_tail.s assembled at 0x47D20000 (free DDR, outside param-grow's and
        core's regions) and the jsr at 0x40025e9e pointed at it.
Compared, pass by pass: the wire frame (the 0xabc bytes the PUSHR entries carry), the TX payload
buffer, the staging buffer's command and data halves; at the end every mapped byte of RAM.
Checked on the hook run: the tail layout (magic, sequence, k 70..96 smoothed, check word).
Writes out/T6.4/store-frame/cf/NAME-frame.json and NAME-hook.wire.bin. Emulator RAM only.
"""
import argparse
import hashlib
import json
import os
import struct
import sys

DK = os.environ.get('DIGIKIT', os.getcwd())
sys.path.insert(0, DK)
from unicorn import UcError  # noqa: E402
from unicorn.m68k_const import UC_M68K_REG_PC, UC_M68K_REG_D0, UC_M68K_REG_A7  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
LENS = os.path.join(ROOT, 'out', 'T6.3', 'CMP2')
VECTOR = 191
HANDLER_CALL = 0x40025e9e
DRIVER = 0x400cf922
COUNTER = 0x40287c38
TX, TX_LEN = 0x80005e60, 0xa80
FRAME_LEN = 0xabc
STAGE, STAGE_END = 0x80002510, 0x80003af0
SMOOTH16 = 0x800068e4 + 0xcc2
RAW16 = 0x800047b2
KITPTR, FXOFF = 0x800052a0, 0x48e4
SR = 0xEC03802C
SETTER = 0x4002585a
CODE, DATA = 0x47D20000, 0x47D20400
K0, NV, MAGIC = 70, 27, 0xC3B1


def run(name, snap, passes, hook, limit):
    from emu import config
    from emu.longrun import build
    img = config.main_image()
    sha = hashlib.sha256(open(img, 'rb').read()).hexdigest()
    mb = os.path.join(ROOT, 'out', 'T6.3', {'C1EM': 'CMP1', 'C2EM': 'CMP2'}[name], 'main.bin')
    want = hashlib.sha256(open(mb, 'rb').read()).hexdigest()
    if sha != want:
        raise SystemExit('DT2_SECTIONS main OS is not %s' % name)
    m, ev, st, pc, inq, at = build(snap, softfloat=True, bitmap=True, dsp=True, unblock=True,
                                   deferred_components=('timers',))
    uc = m.uc
    rd = lambda a, n: bytes(uc.mem_read(a, n))                 # noqa: E731
    w16 = lambda a: struct.unpack('>H', rd(a, 2))[0]           # noqa: E731
    m.ensure(CODE)
    info = {'code_region_zero_before': rd(CODE, 0x800) == bytes(0x800)}

    def sr_read(uc_, typ, addr, size, val, data):
        uc_.mem_write(SR, struct.pack('>I', 0x90000000))
    from unicorn import UC_HOOK_MEM_READ, UC_HOOK_MEM_WRITE
    uc.hook_add(UC_HOOK_MEM_READ, sr_read, begin=SR, end=SR + 3)
    stage_writers = {}

    def on_stage(uc_, typ, addr, size, val, data):
        p = uc_.reg_read(UC_M68K_REG_PC)
        for o in range(addr - STAGE, addr - STAGE + size, 2):
            i, half = divmod(o, 4)
            if half == 2 and i >= 0x540:
                stage_writers.setdefault(p, 0)
                stage_writers[p] += 1
    uc.hook_add(UC_HOOK_MEM_WRITE, on_stage, begin=STAGE, end=STAGE_END - 1)
    resume = uc.reg_read(UC_M68K_REG_PC)

    def call(fn, *args):
        sp0 = uc.reg_read(UC_M68K_REG_A7)
        sp = sp0 - 4 * (len(args) + 1)
        uc.mem_write(sp, struct.pack('>I' + 'I' * len(args), resume, *[x & 0xffffffff for x in args]))
        uc.reg_write(UC_M68K_REG_A7, sp)
        uc.emu_start(fn, resume, count=200_000)
        uc.reg_write(UC_M68K_REG_A7, sp0)
        return uc.reg_read(UC_M68K_REG_D0)

    info['site_bytes_in_snapshot'] = rd(HANDLER_CALL, 6).hex()
    if name == 'C1EM' and hook:
        text = open(os.path.join(LENS, 'cf', 'tail_reloc.run.bin'), 'rb').read()
        uc.mem_write(CODE, text)
        uc.mem_write(DATA, bytes(4))
        assert rd(HANDLER_CALL, 6) == bytes.fromhex('4eb9400cf922')
        uc.mem_write(HANDLER_CALL, bytes.fromhex('4eb9') + struct.pack('>I', CODE))
    if name == 'C2EM':
        mp = json.load(open(os.path.join(LENS, 'C2MO-main.syx.map.json')))
        assert rd(HANDLER_CALL, 6) == bytes.fromhex('4eb9') + struct.pack('>I', int(mp['compl_tail'], 16))
        if not hook:
            uc.mem_write(HANDLER_CALL, bytes.fromhex('4eb9400cf922'))
    kit = struct.unpack('>I', rd(KITPTR, 4))[0]
    fx = kit + FXOFF
    test = {K0 + j: ((90 - 3 * j) & 0x7f) << 8 for j in range(NV)}
    for k, v in test.items():
        call(SETTER, v, 0x10, k)
        uc.mem_write(fx + 2 * k, struct.pack('>H', v))       # the live word, as FxParameterSet::vslot31 does
    call(SETTER, test[K0], 0x10, 27)
    info['kit'] = '%#010x' % kit
    info['test_values'] = {str(k): '%#06x' % v for k, v in test.items()}
    wires, txs, cmds, glide = [], [], [], []
    for n in range(passes):
        uc.mem_write(COUNTER, bytes(4))
        uc.reg_write(UC_M68K_REG_PC, resume)
        m.halt_vec = None
        if not m.raise_vector(VECTOR):
            raise SystemExit('vector %d holds no handler' % VECTOR)
        h = uc.reg_read(UC_M68K_REG_PC)
        try:
            uc.emu_start(h, resume, count=limit)
        except UcError as e:
            raise SystemExit('pass %d: %s' % (n, e))
        if uc.reg_read(UC_M68K_REG_PC) != resume:
            raise SystemExit('pass %d did not return' % n)
        stg = rd(STAGE, STAGE_END - STAGE)
        wires.append(b''.join(stg[4 * i + 2:4 * i + 4] for i in range(FRAME_LEN // 2)))
        cmds.append(hashlib.sha256(b''.join(stg[4 * i:4 * i + 2] for i in range(0x578))).hexdigest())
        txs.append(hashlib.sha256(rd(TX, TX_LEN)).hexdigest())
        glide.append((w16(SMOOTH16 + 2 * 27), w16(SMOOTH16 + 2 * K0)))
    regions = []
    for b, e, perms in uc.mem_regions():
        regions.append((b, rd(b, e - b + 1)))
    info['stage_tail_writer_pcs'] = {'%#010x' % p: c for p, c in stage_writers.items()}
    info['smooth16_k70_96_end'] = [w16(SMOOTH16 + 2 * k) for k in range(K0, K0 + NV)]
    seq_at = DATA if name == 'C1EM' else int(json.load(open(os.path.join(LENS, 'C2MO-main.syx.map.json')))['compl_seq'], 16)
    info['seq_end'] = struct.unpack('>I', rd(seq_at, 4))[0] if hook else None
    m.close()
    return info, wires, txs, cmds, glide, regions


def main():
    p = argparse.ArgumentParser()
    p.add_argument('name')
    p.add_argument('snapshot')
    p.add_argument('--passes', type=int, default=300)
    p.add_argument('--limit', type=lambda s: int(s, 0), default=5_000_000)
    a = p.parse_args()
    out = os.path.join(LENS, 'cf')
    # the relocated code is CMP2's linked compl_tail but for the compl_seq operands
    mp = json.load(open(os.path.join(LENS, 'C2MO-main.syx.map.json')))
    img = open(os.path.join(LENS, 'main.bin'), 'rb').read()
    t_at, s_at = int(mp['compl_tail'], 16), int(mp['compl_seq'], 16)
    run_load, run_start = int(mp['__run_load'], 16), int(mp['__run_start'], 16)
    linked = img[run_load - 0x40000400 + (t_at - run_start):][:90]
    rel = open(os.path.join(out, 'tail_reloc.run.bin'), 'rb').read()
    norm = lambda b, s: b.replace(struct.pack('>I', s), b'SEQ!')   # noqa: E731
    reloc_check = {'linked_compl_tail': '%#010x' % t_at, 'linked_compl_seq': '%#010x' % s_at,
                   'bytes': len(rel), 'seq_operands': linked.count(struct.pack('>I', s_at)),
                   'equal_but_the_seq_operands': norm(linked, s_at) == norm(rel, DATA)}
    bi, bw, bt, bc, bg, br = run(a.name, a.snapshot, a.passes, False, a.limit)
    hi, hw, ht, hc, hg, hr = run(a.name, a.snapshot, a.passes, True, a.limit)
    res = {'name': a.name, 'snapshot': a.snapshot, 'passes': a.passes, 'relocated_code_vs_CMP2': reloc_check,
           'base': bi, 'hook': hi}
    res['wire_payload_equal_every_pass'] = all(x[:TX_LEN] == y[:TX_LEN] for x, y in zip(bw, hw))
    res['tx_buffer_equal_every_pass'] = bt == ht
    res['stage_command_halves_equal_every_pass'] = bc == hc
    res['base_tail_words_ever_nonzero'] = sum(1 for x in bw if x[TX_LEN:] != bytes(FRAME_LEN - TX_LEN))
    res['glide_equal'] = bg == hg
    res['k27_equals_k70_every_pass'] = all(x == y for x, y in hg)
    # the tail layout on every pass of the hook run
    bad, seqs = [], []
    for n, x in enumerate(hw):
        t = struct.unpack('>30H', x[TX_LEN:])
        ok = t[0] == MAGIC and ((~sum(t[:29])) & 0xffff) == t[29]
        seqs.append(t[1])
        if not ok:
            bad.append(n)
    res['tail_valid_passes'] = a.passes - len(bad)
    res['tail_invalid_passes'] = bad[:20]
    res['seq_first_last'] = (seqs[0], seqs[-1])
    res['seq_step_1_every_pass'] = all((seqs[i + 1] - seqs[i]) & 0xffff == 1 for i in range(len(seqs) - 1))
    last = struct.unpack('>30H', hw[-1][TX_LEN:])
    res['tail_values_equal_smoothed_k70_96'] = list(last[2:29]) == hi['smooth16_k70_96_end']
    res['tail_last'] = ['%#06x' % x for x in last]
    # whole-RAM comparison
    diffs = []
    bmap = {b: d for b, d in br}
    for b, d in hr:
        if b not in bmap:
            diffs.append(('region only in hook run', '%#010x' % b, len(d)))
            continue
        e = bmap[b]
        if d == e:
            continue
        n = min(len(d), len(e))
        cur = None
        for off in range(0, n, 4096):
            if d[off:off + 4096] == e[off:off + 4096]:
                if cur:
                    diffs.append(('%#010x' % (b + cur[0]), cur[1] - cur[0]))
                    cur = None
                continue
            for i in range(off, min(off + 4096, n)):
                if d[i] != e[i]:
                    if cur and i - cur[1] <= 8:
                        cur[1] = i + 1
                    else:
                        if cur:
                            diffs.append(('%#010x' % (b + cur[0]), cur[1] - cur[0]))
                        cur = [i, i + 1]
        if cur:
            diffs.append(('%#010x' % (b + cur[0]), cur[1] - cur[0]))
    res['ram_diff_ranges'] = diffs[:200]
    res['ram_diff_count'] = len(diffs)
    with open(os.path.join(out, a.name + '-hook.wire.bin'), 'wb') as f:
        f.write(hw[-1])
    with open(os.path.join(out, a.name + '-base.wire.bin'), 'wb') as f:
        f.write(bw[-1])
    json.dump(res, open(os.path.join(out, a.name + '-frame.json'), 'w'), indent=1, default=str)
    print(json.dumps({k: v for k, v in res.items() if k not in ('base', 'hook')}, indent=1, default=str)[:5000])
    return 0


if __name__ == '__main__':
    sys.exit(main())
