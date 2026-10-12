# SPDX-License-Identifier: GPL-2.0-or-later
"""Section 7 (SHARC/DSP) checks for the brick checker (check 11, task T1.7b).

A changed section 7 used to only have to depack. This module makes a DSP
change provable instead: the depacked DSP must equal an intended image the
caller supplies (--intended-dsp, mirroring --intended-main); a changed DSP
with no intended image is "incomplete", never "accept". When a DSP change is
intended, these checks run, each as strong as we can make it offline:

  * size: stored section 7 <= 1 MiB and the depacked boot stream <= 1 MiB - 1,
    the two limits the ColdFire's DSP loader FUN_400cf4b0 enforces on 1.12
    (stored at 0x400cf710/0x400cf716, stream+1 at 0x400cf748/0x400cf74e;
    notes/T1.2, notes/prior-art-2026-10 6.1 fact 3). Over either, the DSP
    never boots.
  * boot stream parses completely (sharc_ldr_parse: header XOR/HDRSIGN, byte
    accounting, 4-byte counts, ends with a FINAL block; the stream carries no
    payload CRC, which is stated, not assumed).
  * no load block writes into an L1/L2 cache range the start-up enables, or
    outside the known L1/L2/DDR regions (notes/T3.2 + its 2026-10-06
    correction; cache ranges from prior-art 6.1 fact 2).
  * a scan of changed/new DSP code for the mis-encoded negative-immediate
    shift form selas emits, which parks the SHARC on silicon (prior-art 6.1
    fact 1): a pure shift (LSHIFT/ASHIFT/ROT, +/- the OR forms) whose 8-bit
    immediate is negative but whose sign-extension nibble is not 0xf (or
    positive with a non-zero nibble). Decoded with the repo's SHARC decoder
    (tools/sharcvm/decode.py -> digikit).
  * a dynamic run with tools/sharcvm: the effect init 0x1c1445 then the whole
    block handler 0x1c9d6b for a few hundred blocks with a stock-like frame
    and a test input, refusing on any trap / unimplemented-instruction stop /
    NaN or Inf output / output above a loudness ceiling, and reporting the
    instructions per block against the stock budget (warn above it).

All of this is offline (no hardware, no flashing). The decoder and the
interpreter are the repo's own (tools/sharcvm, T3.6); digikit is read, never
changed.
"""
import math
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
DIGIKIT = os.path.join(ROOT, 'vendor', 'digikit')
SCRIPTS = os.path.join(ROOT, 'scripts')
SHARCVM = os.path.join(ROOT, 'tools', 'sharcvm')
MEMDIR = os.path.join(ROOT, 'out', 'T3.2', 'mem')

# ColdFire DSP loader limits on 1.12 (FUN_400cf4b0) [verified in the decompile]
STORED_MAX = 0x100000            # stored section 7 <= 1 MiB  (0x400cf710 cmpi; 0x400cf716 bhi)
STREAM_MAX = 0x100000 - 1        # depacked boot stream <= 1 MiB - 1 (0x400cf748 cmpi of len+1; 0x400cf74e bgt)
STORED_LIMIT_SITE = '0x400cf710/0x400cf716'
STREAM_LIMIT_SITE = '0x400cf748/0x400cf74e'

# L1/L2/DDR regions, core byte addresses (notes/T3.2 section 0). L1 is reached
# in a boot-stream target through the system alias 0x28000000 + core byte.
SYS = 0x28000000
REGIONS = [(0x240000, 0x270000, 'L1 block 0'), (0x2c0000, 0x2f0000, 'L1 block 1'),
           (0x300000, 0x320000, 'L1 block 2'), (0x380000, 0x3a0000, 'L1 block 3'),
           (0x20000000, 0x20100000, 'L2'), (0x80000000, 0xa0000000, 'DDR')]

# Cache ranges the start-up (SHL1C_CFG |= 0x414141 at 0xb8b93a) carves from the
# top of the L1 blocks, plus the reserved top of L2 the cache code names. Code
# or state loaded here is lost/undefined once the caches are on (prior-art 6.1
# fact 2; our T3.2 2026-10-06 correction). Stock loads nothing into them.
CACHE = [(0x39c000, 0x3a0000, 'L1 block 3 I-cache'),
         (0x2ec000, 0x2f0000, 'L1 block 1 DM cache'),
         (0x31c000, 0x320000, 'L1 block 2 PM cache'),
         (0x200fa000, 0x20100000, 'L2 reserved / non-cacheable window and boot-ROM top')]

# Pure shift opcodes (Type 6 shiftimm): a BY <imm> whose sign must extend into
# the dataex nibble. FEXT/FDEP/BSET/... (>=0x10) use that nibble as a real
# field and are not shift-by-immediate, so they are left alone.
SHIFT_OPS = {0x00, 0x01, 0x02, 0x08, 0x09}   # LSHIFT ASHIFT ROT LSHIFT_OR ASHIFT_OR

# Executable regions to disassemble for the shift scan (core byte addresses).
CODE_REGIONS = [(0x380000, 0x3a0000), (0x20000000, 0x20100000), (0x2c0000, 0x2f0000),
                (0x300000, 0x320000)]

HANDLER = 0x1c9d6b
FXINIT = 0x1c1445
STOCK_INSN_PER_BLOCK = 320281    # measured here on stock (T3.6 full block, no voices)


def _imports():
    for p in (SHARCVM, SCRIPTS, DIGIKIT):
        if p not in sys.path:
            sys.path.insert(0, p)


def to_core(a):
    return a - SYS if SYS <= a < SYS + 0x400000 else a


def in_region(a, n):
    return any(lo <= a and a + n <= hi for lo, hi, _ in REGIONS)


def bad_shift(si, dataex):
    """A shift-by-immediate whose sign-extension nibble disagrees with the sign
    of its 8-bit immediate: selas's mis-encoding (prior-art 6.1 fact 1).
    -> (op6, data8, dataex) when bad, else None."""
    op6 = (si >> 16) & 0x3f
    if op6 not in SHIFT_OPS:
        return None
    data8 = (si >> 8) & 0xff
    neg = bool(data8 & 0x80)
    if (neg and dataex == 0xf) or (not neg and dataex == 0):
        return None
    return (op6, data8, dataex)


# --------------------------------------------------------------------------


class DspError(Exception):
    pass


def depack(stored):
    _imports()
    from dt2 import elz
    return elz.depack_section(stored)


def final_image(stream):
    """The candidate boot stream -> its loaded memory image (last write wins),
    as sharc_ldr_parse builds it. -> (Image, blocks, parse_checks)."""
    _imports()
    import sharc_ldr_parse as P
    blocks, errors, stop = P.parse(stream)
    apps = P.applications(blocks, len(stream))
    checks = P.check(stream, blocks, errors, stop, apps)
    img = None
    if not errors:
        img = P.Image(blocks)
        for b in blocks:
            img.apply(stream, b)
    return img, blocks, checks, errors


def _is_load(b):
    return bool(b['byte_count']) and not ({'FIRST', 'FINAL', 'INIT', 'IGNORE'} & set(b['flags']))


def scan_sizes_and_blocks(stored7, stream):
    """Static checks on the stored/depacked sizes and the boot-stream blocks.
    -> dict with the findings (no I/O)."""
    _imports()
    import sharc_ldr_parse as P
    out = {'stored_len': len(stored7), 'stored_ok': len(stored7) <= STORED_MAX,
           'stream_len': len(stream), 'stream_ok': len(stream) <= STREAM_MAX}
    blocks, errors, stop = P.parse(stream)
    apps = P.applications(blocks, len(stream))
    checks = P.check(stream, blocks, errors, stop, apps)
    out['parse_ok'] = all(c['ok'] for c in checks) and not errors
    out['parse_fail'] = [c for c in checks if not c['ok']]
    out['final'] = bool(blocks) and 'FINAL' in blocks[-1]['flags']
    out['has_crc'] = any('CALLBACK' in b['flags'] for b in blocks)
    cache_hits, region_miss = [], []
    for b in blocks:
        if not _is_load(b):
            continue
        core = to_core(b['target_address'])
        n = b['byte_count']
        if not in_region(core, n):
            region_miss.append({'block': b['index'], 'target': '%#010x' % b['target_address'], 'bytes': n})
        for lo, hi, name in CACHE:
            if core < hi and lo < core + n:
                cache_hits.append({'block': b['index'], 'core': '%#010x' % core, 'bytes': n, 'range': name})
    out['cache_hits'] = cache_hits
    out['region_miss'] = region_miss
    out['blocks'] = len(blocks)
    return out


def scan_bad_shifts(cand_img, stock_img, blocks):
    """Disassemble each of the candidate's executable payload blocks from its
    (instruction-aligned) start and flag the mis-encoded shift form, but only
    where the bytes differ from stock (so unchanged stock code is never
    reported). Block starts are instruction boundaries, which keeps the linear
    decode aligned through the code; data and FILL blocks are skipped.
    -> list of findings."""
    _imports()
    import decode as D
    import translate as T

    cand_read = _image_reader(cand_img)
    stock_read = _image_reader(stock_img) if stock_img is not None else (lambda a, n: None)
    found = []
    for b in blocks:
        if not _is_load(b) or b['fill']:
            continue
        core = to_core(b['target_address'])
        sw0 = _byte_to_sw(core)
        if sw0 is None:                       # not an executable region (DDR data, etc.)
            continue
        sw1 = sw0 + b['byte_count'] // 2
        pc = sw0
        while pc < sw1:
            d = D.decode_at(cand_read, pc)
            step = d.parcels or 1
            if d.parcels and d.form.startswith('6') and ('shift' in d.form or d.form.startswith('6a')):
                try:
                    si = T.f(d.fields, 'shiftimm')
                    de = T.f(d.fields, 'dataex')
                except KeyError:
                    si = de = None
                if si is not None:
                    bad = bad_shift(si, de)
                    if bad and _bytes_differ(cand_read, stock_read, _sw_to_byte(pc), step * 2):
                        found.append({'sw': '%#08x' % pc, 'byte': '%#010x' % _sw_to_byte(pc),
                                      'raw': '%#014x' % d.raw, 'op6': bad[0],
                                      'imm8': '%#04x' % bad[1], 'dataex': bad[2], 'block': b['index']})
            pc += step
    return found


def _image_reader(img):
    """A reader over a sharc_ldr_parse.Image keyed by CORE byte address. The
    Image stores L1 at its loader alias 0x28000000 + core, so map L1 reads to
    that alias; L2 and DDR targets are the same in both views."""
    def read(a, n):
        la = a + SYS if 0x240000 <= a < 0x3a0000 else a
        try:
            return img.read(la, n)
        except Exception:
            return None
    return read


def _bytes_differ(cand_read, stock_read, byte, n):
    c = cand_read(byte, n)
    s = stock_read(byte, n)
    if c is None:
        return False
    if s is None:
        return True              # new bytes (stock has nothing here) count as changed
    return c != s


def _byte_to_sw(byte):
    if 0x380000 <= byte < 0x3a0000:
        return (byte) // 2            # L1 block 3: SW 0x1c0000..
    if 0x2c0000 <= byte < 0x2f0000:
        return byte // 2
    if 0x300000 <= byte < 0x320000:
        return byte // 2
    if 0x20000000 <= byte < 0x20100000:
        return 0xb80000 + (byte - 0x20000000) // 2
    return None


def _sw_to_byte(sw):
    if 0xb80000 <= sw < 0xc00000:
        return 0x20000000 + 2 * (sw - 0xb80000)
    return 2 * sw


# --------------------------------------------------------------------------
# dynamic: run the block handler on the candidate DSP image offline
# --------------------------------------------------------------------------


def dynamic_available():
    return os.path.isdir(MEMDIR) and os.path.exists(os.path.join(MEMDIR, 'l1b3.bin'))


def dynamic_run(stored7, blocks_n=1500, loud=4.0, fxinit_cap=5 * 10**8, wall_budget=180.0):
    """Load the candidate's section 7 into tools/sharcvm, run the effect init
    then the whole block handler for `blocks_n` blocks with a stock-like frame
    and a test input. -> dict. Refuses (sets 'fail') on a trap, an
    unimplemented-instruction stop, a NaN/Inf output, or a peak above `loud`."""
    _imports()
    import time
    from vm import VM
    import fullblock
    import sharc_ldr_parse as P
    from dt2 import elz

    res = {'blocks_requested': blocks_n, 'loud_ceiling': loud}
    stream = elz.depack_section(stored7)
    blk, errors, stop = P.parse(stream)
    if errors:
        res['fail'] = 'boot stream does not parse: %s' % errors[:1]
        return res
    img = P.Image(blk)
    for b in blk:
        img.apply(stream, b)

    vm = VM()
    vm.load_dn2()                       # stock baseline sets the C-runtime state and maps memory
    # overwrite with the candidate's written bytes (run by run, core byte addresses)
    for (s, e), buf, own in zip(img.regions, img.data, img.owner):
        core = to_core(s)
        run_s = None
        for i in range(len(own) + 1):
            w = i < len(own) and own[i] >= 0
            if w and run_s is None:
                run_s = i
            if (not w) and run_s is not None:
                try:
                    vm.write(core + run_s, bytes(buf[run_s:i]))
                except Exception:
                    pass                # a block outside the VM's mapped regions is caught statically
                run_s = None

    t0 = time.time()
    try:
        vm.call(FXINIT, max_insns=fxinit_cap)
    except Exception as e:
        res['fail'] = 'effect init %#x did not complete: %s' % (FXINIT, e)
        res['init_seconds'] = round(time.time() - t0, 1)
        return res
    res['init_seconds'] = round(time.time() - t0, 1)

    d = fullblock.FullDSP(vm=vm)
    # Every send effect gets signal and is heard: the external input goes to
    # chorus, delay and reverb, each at full mix volume, with the pattern
    # volume up. (Until 2026-10-12 only the reverb send was set and every
    # VOL was 0, so a replaced chorus or delay processed silence and the
    # loudness ceiling could not see it: peak 0.017 for every candidate.)
    base = dict(IN_L=40, IN_R=40, IN_CHR_L=80, IN_CHR_R=80, IN_DEL_L=80, IN_DEL_R=80,
                IN_REV_L=80, IN_REV_R=80,
                CHR_DPTH=96, CHR_SPD=30, CHR_HPF=32, CHR_WDTH=48, CHR_VOL=127,
                DEL_TIME=4, DEL_FDBK=64, DEL_HPF=0, DEL_LPF=127, DEL_WID=64, DEL_VOL=127,
                REV_DEC=90, REV_VOL=127, PATTERN_VOL=127)
    d.set(**base)
    d.set_raw(TEMPO=120 * 120)
    # DEL_TIME 4 echoes after about 3,800 samples (TIME+1 eighths of 750 at 120 BPM),
    # well inside the run, so a replaced delay is heard too.
    # In the second half, the chorus and delay knobs jump to new values every
    # 16 blocks (fixed seed): a candidate must stay finite and under the
    # ceiling across its knob ranges, not only at one setting. Feedback stays
    # at or below the stock default range's top (DEL_FDBK <= 100), so a stock
    # delay is not driven into its own runaway.
    import random
    rng = random.Random(0x5EED)
    fuzz_keys = ('CHR_DPTH', 'CHR_SPD', 'CHR_HPF', 'CHR_WDTH', 'DEL_TIME', 'DEL_X',
                 'DEL_WID', 'DEL_HPF', 'DEL_LPF')
    res['settings'] = 'all three sends, VOL 127; knob fuzz in the second half (seed 0x5EED)'
    peak = 0.0
    nan_inf = 0
    done = 0
    t0 = time.time()
    try:
        for i in range(blocks_n):
            if i >= blocks_n // 2 and i % 16 == 0:
                knobs = {k: rng.randrange(128) for k in fuzz_keys}
                knobs['DEL_TIME'] = rng.randrange(16)   # echoes inside the run
                d.set(**knobs, DEL_FDBK=rng.randrange(101))
            L = [math.sin(i * 0.30 + j * 0.11) * 0.3 for j in range(32)]
            R = [math.sin(i * 0.31 + j * 0.13) * 0.3 for j in range(32)]
            a, b = d.block(L, R)
            for v in a + b:
                if v != v or math.isinf(v):
                    nan_inf += 1
                else:
                    peak = max(peak, abs(v))
            done = i + 1
            if nan_inf or peak > loud:
                break               # a bad block: stop early, it is already a refusal
            if time.time() - t0 > wall_budget:
                res['fail'] = ('the block handler did not finish %d blocks within %.0f s '
                               '(ran %d); cannot vouch' % (blocks_n, wall_budget, done))
                res['blocks_run'] = done
                return res
    except Exception as e:
        res['fail'] = 'the block handler %#x trapped at block %d: %s' % (HANDLER, done, e)
        res['blocks_run'] = done
        return res
    res['seconds'] = round(time.time() - t0, 1)
    res['blocks_run'] = done
    res['insn_per_block'] = round(d.insns / max(1, done))
    res['peak'] = round(peak, 4)
    res['nan_inf'] = nan_inf
    res['median_cycles'] = sorted(d.cycles)[len(d.cycles) // 2] if d.cycles else None
    if nan_inf:
        res['fail'] = '%d DSP output samples were NaN or Inf' % nan_inf
    elif peak > loud:
        res['fail'] = 'DSP output peak %.3f is above the loudness ceiling %.1f' % (peak, loud)
    res['over_budget'] = res.get('insn_per_block', 0) > STOCK_INSN_PER_BLOCK * 1.5
    return res
