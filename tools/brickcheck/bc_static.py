# SPDX-License-Identifier: GPL-2.0-or-later
"""Static checks on the unpacked main OS (brick checker checks 6, 7 and 10).

Everything compares the candidate's depacked main OS with the stock one, both
based at 0x40000400. No disassembler is needed: the scans look for the byte
forms a reference can take on ColdFire (an absolute 32-bit operand or word,
or a 16/32-bit pc-relative displacement), and only inside bytes that differ
from stock, so whatever stock already does is never reported as new.

Addresses come from the notes they cite; each list says where.
"""
import bisect
import csv
import os
import sqlite3
import struct

MAIN_BASE = 0x40000400
STOCK_END = MAIN_BASE + 3196288          # 0x4030C980, the stock image's end

# --------------------------------------------------------------------------
# protected ranges (check 6): [lo, hi) in the unpacked main OS
# --------------------------------------------------------------------------

# From the task and HANDOFF ("panel MCU blob from 0x402F7D40 to the end"),
# T1.4 (version word, updater) and the T1.6 profile
# (vendor/elekloader/elekloader/devices/digitone_mk2.py, `protected`).
PROTECTED = [
    (0x402F7D40, STOCK_END, 'panel MCU blob from 0x402F7D40 to the end of the stock image '
                            '(HANDOFF/T1.4; T1.6 measured the blob the updater sends as '
                            '0x402f7d40-0x402fb494, the rest is other data and the zero SRAM init)'),
    (0x402F7D44, 0x402F7D48, 'panel MCU firmware version word 0x402f7d44 (T1.4)'),
    (0x400DBBD8, 0x400DBDAE, 'panel MCU updater FUN_400dbbd8 (T1.4; function_ranges '
                             '0x400dbbd8-0x400dbdad)'),
    (0x400DBA28, 0x400DBDAE, 'T1.6 profile: the panel MCU updater FUN_400dbbd8 and its UART8 helpers'),
    (0x402F7D40, 0x402FB494, 'T1.6 profile: the panel MCU firmware the updater sends'),
    (0x40134A28, 0x4013621C, 'T1.6 profile: the SPI NOR driver (every flash command and its wrappers)'),
    (0x40139104, 0x4013924C, 'T1.6 profile: the in-OS upgrade writer FUN_40139104'),
    # T1.7b gap 1: the in-OS upgrade validator and its crypto callees. Pinning the
    # validator alone leaves the hole one call deeper (patch the checksum or the
    # trailer check instead), so its static callee closure (calls.tsv) and the
    # data they read are pinned. A brick-safe mod never needs to weaken this gate.
    (0x400DBDB0, 0x400DBDE4, 'in-OS upgrade validator FUN_400dbdb0 (T1.7b gap 1; red team danger-validator)'),
    (0x401226F8, 0x40122732, 'in-OS upgrade content checksum FUN_401226f8 (validator callee)'),
    (0x400D2BC4, 0x400D2CA2, 'in-OS upgrade HMAC trailer check FUN_400d2bc4 (validator callee)'),
    (0x4011E840, 0x4011E982, 'HMAC FUN_4011e840 (trailer-check callee)'),
    (0x4011E984, 0x4011ECB8, 'SHA-256 FUN_4011e984 (trailer-check callee)'),
    (0x40207DA0, 0x40207EA0, 'SHA-256 round constants (0x428a2f98..) read by FUN_4011e984'),
    (0x40210A4A, 0x40210A55, 'seal-key seed string "Multiplier" read by FUN_400d2bc4'),
    (0x4021B9EF, 0x4021BA0F, 'the 32-byte key mask FUN_400d2bc4 XORs into the derived key'),
]

# The real flash gate (T1.7b gap 3): check 6 must keep these byte-identical.
# A computed-address gadget cannot reach a flash write without going through
# the NOR driver or the writer, and neither can change without check 6 failing.
FLASH_GATE = [
    (0x40134A28, 0x4013621C, 'the SPI NOR driver (every flash erase/program/protect primitive)'),
    (0x40139104, 0x4013924C, 'the in-OS upgrade writer FUN_40139104'),
]

# --------------------------------------------------------------------------
# reference targets (check 7)
# --------------------------------------------------------------------------

# The DSPI0 NOR driver: notes/T1.4-flash-writers.md section 2 and
# notes/T1.4-verify-2.md sections 2, 3.4 and 6. Any new reference to an
# address inside the driver counts, not only to these entries; the names are
# for the report.
DRIVER = (0x40134A28, 0x4013621C)
PRIMITIVES = {
    0x40134A28: 'WREN 0x06',
    0x40134A6A: 'erase 0x20/0xD8 (subsector or block)',
    0x40134B04: 'RDSR 0x05 (read)',
    0x40134B58: 'CLSR 0x30',
    0x40134BCE: 'REMS 0x90 (read)',
    0x40134C96: 'OTP read 0x4B',
    0x40134EE4: 'OTP read 0x4B',
    0x40134DDE: 'SFDP read 0x5A',
    0x4013503E: 'security register read 0x48',
    0x4013517E: 'WREN + 0x42 security register program',
    0x401352B8: 'WREN + 0x44 security register erase',
    0x401353AE: 'READ 0x03 (flash_read)',
    0x401354D6: 'PAGE PROGRAM 0x02',
    0x40135618: 'PAGE PROGRAM 4-byte 0x12',
    0x4013576C: 'WREN + 0x20 subsector erase',
    0x401357CE: 'block erase 0xD8',
    0x40135850: 'block erase 0xD8, watchdog-serviced',
    0x401358F2: 'ASPRD 0x2B (read)',
    0x40135994: 'RDID 0x9F (read)',
    0x40135BA2: 'PPB read 0xE2',
    0x40135CA6: 'PPB program 0xE3',
    0x40135DC8: 'PPB erase 0xE4',
    0x40135E7C: 'DYB read 0xE0',
    0x40135F80: 'DYB write 0xE1',
    0x401360A2: 'read wrapper',
    0x401360A6: 'program wrapper',
    0x40136122: 'erase-region wrapper',
    0x4013618C: 'erase-region wrapper, watchdog-serviced',
}
# The code that calls the primitives to erase or write (T1.4 section 3,
# clusters A-D, and T1.4-verify-2 section 2). A new reference to one of these
# is a new path into a flash writer.
WRITERS = {
    0x40139104: ('in-OS upgrade writer FUN_40139104 (cluster A)', 0x4013924C),
    0x40109144: ('"ERASING FLASH..." job FUN_40109144 that calls the writer (cluster A)', None),
    0x401094E8: ('upgrade job FUN_401094e8 that installs FUN_40109144 (cluster A)', None),
    0x400CD5F2: ('factory console FUN_400cd5f2, #UPGRADE/#FULL_UPGRADE (clusters B, B\')', None),
    0x400CEBD2: ('factory console task FUN_400cebd2 (clusters B, B\')', None),
    0x400CCCC6: ('settings store writer FUN_400cccc6, 0x380000/0x3c0000 (cluster C)', None),
    0x400CCDA2: ('settings store writer FUN_400ccda2 (cluster C)', None),
    0x400CCE7E: ('settings store writer FUN_400cce7e (cluster C)', None),
    0x400CCF5A: ('settings store writer FUN_400ccf5a (cluster C)', None),
    0x400CD036: ('settings store writer FUN_400cd036 (cluster C)', None),
    0x400CD10A: ('settings store writer FUN_400cd10a (cluster C)', None),
    0x400F2022: ('COKi migration eraser FUN_400f2022 (cluster D)', None),
}
DSPI0_REGS = (0xFC05C000, 0xFC05C100)    # MCR..POPR of DSPI0, the flash bus (T1.4 section 2)
MAGIC = 0xB0B0DADA                       # the console #REBOOT magic stored at 0x48000000 (T1.4)


def pushr_frame(v):
    """A DSPI PUSHR command word for the flash chip select: PCS field 0x02
    (bits 23..16), CTAS 0, any of CONT/EOQ/CTCNT, data byte in bits 7..0.
    The driver's own frames are 0x8402_00XX, 0x0802_00XX and 0x8002_00XX
    (T1.4-verify-2 section 2). 0x0002_00XX is left out: it is the common
    constant 0x20000 plus a byte."""
    return ((v >> 16) & 0xFF) == 0x02 and (v & 0xFF00) == 0 and \
        (v >> 24) in (0x04, 0x08, 0x0C, 0x80, 0x84, 0x88, 0x8C)


def pushr_raw(v):
    """The PUSHR forms that are refused wherever they appear (data tables
    too): those whose top byte cannot start a ColdFire instruction word
    (0x80xx/0x84xx/0x88xx/0x8Cxx would be or.b and 0x04xx subi.b, which
    ColdFire does not have). The EOQ-only forms 0x0802_00XX and 0x0C02_00XX
    are also `btst #n,d2` and `cmpi.b #n,d2` followed by a word, so as raw
    words they would flag ordinary compiled code; they count only as an
    immediate operand (long_imm_op). A zero data byte is an address or
    dummy frame, not a command, and stock's data holds 414 raw 0x80020000
    words, so raw words must also carry a non-zero byte."""
    return pushr_frame(v) and (v >> 24) in (0x04, 0x80, 0x84, 0x88, 0x8C) and (v & 0xFF) != 0


def long_imm_op(op):
    """True if `op` is an instruction word whose next 32 bits are a long
    immediate: move.l #imm,<ea>; ori/andi/subi/addi/eori/cmpi.l #imm,Dn."""
    return (op & 0xF03F) == 0x203C or (op & 0xFFF8) in (0x0080, 0x0280, 0x0480, 0x0680,
                                                          0x0A80, 0x0C80)


# --------------------------------------------------------------------------
# names
# --------------------------------------------------------------------------


class Names:
    """Function names from out/decomp/main_os/functions.tsv (entry, size),
    refined by xrefs.sqlite's function_ranges where present."""

    def __init__(self, decomp_dir):
        self.ok = False
        self.source = None
        self.ranges = []                 # (lo, hi_exclusive, entry)
        self.names = {}
        tsv = os.path.join(decomp_dir, 'functions.tsv')
        if not os.path.exists(tsv):
            return
        with open(tsv, newline='') as fh:
            for row in csv.DictReader(fh, delimiter='\t'):
                e = int(row['entry'], 16)
                self.names[e] = row['name']
                self.ranges.append((e, e + max(int(row['size'] or 0), 1), e))
        self.source = tsv
        db = os.path.join(decomp_dir, 'xrefs.sqlite')
        if os.path.exists(db):
            con = sqlite3.connect(db)
            extra = con.execute('select func, lo, hi from function_ranges').fetchall()
            con.close()
            self.ranges += [(lo, hi + 1, f) for f, lo, hi in extra if f in self.names]
            self.source += ' + function_ranges in ' + db
        self.ranges = sorted(set(self.ranges))
        self.los = [r[0] for r in self.ranges]
        self.maxlen = max(b - a for a, b, _e in self.ranges)
        self.ok = True

    def covering(self, lo, hi):
        """-> sorted names of functions overlapping [lo, hi)."""
        if not self.ok:
            return []
        out = set()
        j = bisect.bisect_left(self.los, hi) - 1
        while j >= 0 and self.ranges[j][0] > lo - self.maxlen:
            a, b, e = self.ranges[j]
            if a < hi and lo < b:
                out.add(e)
            j -= 1
        return ['%s' % self.names[e] for e in sorted(out)]

    def name(self, addr):
        n = self.covering(addr, addr + 1)
        return n[0] if n else None


# --------------------------------------------------------------------------
# diff
# --------------------------------------------------------------------------


def changed_ranges(stock, cand, base=MAIN_BASE):
    """-> [(lo, hi)] absolute, exclusive: runs of differing bytes over the
    common length, plus the extra tail if the candidate is longer and the
    missing tail if it is shorter."""
    n = min(len(stock), len(cand))
    out = []
    # compare in blocks first: images are mostly identical
    B = 4096
    i = 0
    while i < n:
        j = min(n, i + B)
        if stock[i:j] != cand[i:j]:
            k = i
            while k < j:
                if stock[k] != cand[k]:
                    s = k
                    while k < n and stock[k] != cand[k]:
                        k += 1
                    out.append([base + s, base + k])
                    j = max(j, k)
                else:
                    k += 1
        i = j
    if len(cand) != len(stock):
        out.append([base + n, base + max(len(cand), len(stock))])
    merged = []
    for lo, hi in out:
        if merged and lo <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], hi)
        else:
            merged.append([lo, hi])
    return [tuple(r) for r in merged]


def hunks(ranges, gap=16):
    """Group changed runs closer than `gap` bytes, for a readable report."""
    out = []
    for lo, hi in ranges:
        if out and lo - out[-1][1] <= gap:
            out[-1][1] = hi
            out[-1][2] += 1
        else:
            out.append([lo, hi, 1])
    return out


class ChangedIndex:
    def __init__(self, ranges):
        self.r = ranges
        self.los = [r[0] for r in ranges]

    def hit(self, lo, hi):
        i = bisect.bisect_right(self.los, hi - 1) - 1
        return i >= 0 and self.r[i][0] < hi and lo < self.r[i][1]


def protected_report(stock, cand, base=MAIN_BASE, extra=()):
    out = []
    for lo, hi, why in list(PROTECTED) + list(extra):
        s = stock[lo - base:hi - base]
        c = cand[lo - base:hi - base]
        diff = [lo + k for k in range(min(len(s), len(c))) if s[k] != c[k]]
        short = len(c) < len(s)
        out.append({'lo': '%#010x' % lo, 'hi': '%#010x' % hi, 'what': why,
                    'unchanged': not diff and not short,
                    'bytes_differing': len(diff) + (len(s) - len(c) if short else 0),
                    'first_difference': '%#010x' % diff[0] if diff else
                                        ('%#010x' % (base + len(c)) if short else None)})
    return out


# --------------------------------------------------------------------------
# reference scans (check 7)
# --------------------------------------------------------------------------


def _targets_name(t):
    if t in PRIMITIVES:
        return 'NOR driver %s %#010x' % (PRIMITIVES[t], t)
    if DRIVER[0] <= t < DRIVER[1]:
        k = max(a for a in PRIMITIVES if a <= t) if any(a <= t for a in PRIMITIVES) else None
        return 'inside the NOR driver %#010x (after %s)' % (t, PRIMITIVES.get(k, '?'))
    for e, (why, end) in WRITERS.items():
        if t == e or (end and e <= t < end):
            return '%s %#010x' % (why, t)
    return '%#010x' % t


def _flashy(t):
    if DRIVER[0] <= t < DRIVER[1]:
        return True
    for e, (_why, end) in WRITERS.items():
        if t == e or (end and e <= t < end):
            return True
    return False


def scan_new(stock, cand, ranges, base=MAIN_BASE):
    """Find references and constants in the candidate's changed bytes.
    -> dict of lists: flash_refs, pushr, dspi0, magic, stack_magic_cell."""
    idx = ChangedIndex(ranges)
    res = {'flash_refs': [], 'pushr': [], 'dspi0': [], 'magic': [], 'cell_0x48000000': [],
           'computed_flash': []}
    n = len(cand)
    seen = set()
    for lo, hi in ranges:
        # every word32 that overlaps the changed run, at any byte offset
        a0 = max(0, lo - base - 3)
        a1 = min(n - 4, hi - base - 1)
        for o in range(a0, a1 + 1):
            if (o, 'w') in seen:
                continue
            seen.add((o, 'w'))
            v = struct.unpack_from('>I', cand, o)[0]
            site = base + o
            if _flashy(v):
                res['flash_refs'].append({'site': '%#010x' % site, 'form': 'absolute 32-bit word',
                                          'target': _targets_name(v)})
            if pushr_frame(v):
                imm = o >= 2 and o % 2 == 0 and long_imm_op(struct.unpack_from('>H', cand, o - 2)[0])
                if imm or pushr_raw(v):
                    res['pushr'].append({'site': '%#010x' % site, 'word': '%#010x' % v,
                                         'command': '%#04x' % (v & 0xFF),
                                         'form': 'immediate operand' if imm else 'raw word'})
            if DSPI0_REGS[0] <= v < DSPI0_REGS[1]:
                res['dspi0'].append({'site': '%#010x' % site, 'register': '%#010x' % v})
            if v == MAGIC:
                res['magic'].append({'site': '%#010x' % site})
            if v == 0x48000000:
                res['cell_0x48000000'].append({'site': '%#010x' % site})
        # pc-relative forms at even offsets whose instruction overlaps the run
        e0 = max(0, (lo - base - 6) & ~1)
        e1 = min(n - 2, hi - base)
        for o in range(e0, e1 + 1, 2):
            if (o, 'p') in seen:
                continue
            seen.add((o, 'p'))
            op = struct.unpack_from('>H', cand, o)[0]
            site = base + o
            forms = []
            if op in (0x4EBA, 0x4EFA, 0x487A) or (op & 0xF1FF) == 0x41FA or \
                    (op & 0xF000) == 0x6000 and (op & 0xFF) == 0x00:
                if o + 4 <= n:
                    d = struct.unpack_from('>h', cand, o + 2)[0]
                    forms.append((site + 2 + d, 4, {0x4EBA: 'jsr (d16,pc)', 0x4EFA: 'jmp (d16,pc)',
                                                    0x487A: 'pea (d16,pc)'}.get(
                        op, 'lea (d16,pc)' if (op & 0xF1FF) == 0x41FA else 'bra/bsr/bcc.w')))
            if (op & 0xF000) == 0x6000 and (op & 0xFF) == 0xFF and o + 6 <= n:
                d = struct.unpack_from('>i', cand, o + 2)[0]
                forms.append((site + 2 + d, 6, 'bra/bsr/bcc.l'))
            if (op & 0xF000) == 0x6000 and (op & 0xFF) not in (0x00, 0xFF):
                d = struct.unpack('>b', bytes([op & 0xFF]))[0]
                forms.append((site + 2 + d, 2, 'bra/bsr/bcc.b'))
            for t, ln, form in forms:
                if not idx.hit(site, site + ln):
                    continue
                if _flashy(t):
                    res['flash_refs'].append({'site': '%#010x' % site, 'form': form,
                                              'target': _targets_name(t)})
            # a computed flash reference: movea.l #base,An (0x2n7C) then
            # adda.l #disp,An (0xDnFC, same An) whose sum is flashy. This is the
            # red-team danger-computed idiom (base 0x40134000 + 0xa28 -> WREN).
            # Check 7 is a tripwire, not a gate (easily evaded by splitting the
            # computation further); this raises the bar on the one form seen.
            if (op & 0xF1FF) == 0x207C and o + 10 <= n:
                an = (op >> 9) & 7
                op2 = struct.unpack_from('>H', cand, o + 6)[0]
                if op2 == (0xD1FC | (an << 9)):
                    base_imm = struct.unpack_from('>I', cand, o + 2)[0]
                    disp = struct.unpack_from('>I', cand, o + 6 + 2)[0]
                    tgt = (base_imm + disp) & 0xFFFFFFFF
                    if idx.hit(site, site + 12) and _flashy(tgt):
                        res['computed_flash'].append(
                            {'site': '%#010x' % site, 'form': 'movea.l #%#010x,a%d; adda.l #%#x,a%d'
                             % (base_imm, an, disp, an), 'target': _targets_name(tgt)})
    return res


