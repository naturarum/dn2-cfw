# SPDX-License-Identifier: GPL-2.0-or-later
# The dump stage is adapted from digikit's tools/ghidradump.py (Em D, GPL-2.0-or-later,
# https://github.com/m-dwyer/digikit); modified for the Digitone II 1.12 by naturarum, 2026.
"""Searchable static-analysis dump of the Digitone II OS 1.12 main OS and bootstrap.

    venv/bin/python scripts/decompile_dump.py setup     # language + project copy
    venv/bin/python scripts/decompile_dump.py prepare   # augment the copy (slow: re-analysis)
    venv/bin/python scripts/decompile_dump.py dump      # write out/decomp/<program>/
    venv/bin/python scripts/decompile_dump.py check     # sanity checks on the dump
    venv/bin/python scripts/decompile_dump.py all       # the four in order

    options: --programs main_os bootstrap   --threads 6   --timeout 60   --heap 6g
             --limit N (dump only the first N functions, to time a run)

Nothing here touches the persistent project ghidra/dn2. `setup` copies it to
out/ghidra-copy/dn2 (only if the copy does not exist yet) and builds the
ColdfireEMAC language (digikit's MOVCLR/EMAC fix of Ghidra's ColdFire language)
into out/ghidra-ext/, linked into a private Ghidra settings directory
out/ghidra-settings/. Every JVM this script starts uses that settings directory,
so the language is visible here and nowhere else. Any other tool that opens the
copy must start Ghidra with -Dapplication.settingsdir=<El-Fw>/out/ghidra-settings,
or the programs' language will not be found.

`prepare` (per program, idempotent; steps already done are skipped):
  1. switch the language to 68000:BE:32:ColdfireEMAC (Ghidra's default translator),
  2. re-disassemble the sites that stock Ghidra could not decode (movclr),
  3. add volatile uninitialised blocks over the MCF5441x peripheral windows
     0xEC000000 and 0xFC000000 (1 MiB each) where none exists,
  4. run digikit's McfLabels.java (MCF5441x register labels),
  5. main OS only: label every typeinfo and vtable from analysis/rtti_classes.json,
     define the vtable slots as pointers, create functions at slot targets that
     had none, and name unnamed slot targets <Class>::vslotNN (inferred names),
  6. create functions at runs of disassembled code that no function contains,
  7. re-run Ghidra's auto-analysis, with the no-return discovery analyzer off
     (it wrongly marks functions such as 0x4002c318 and deletes the code after
     their call sites),
  8. where that analysis flowed into defined data (switch cases), clear the data,
     disassemble and analyse again (at most twice), then save.
The before/after counts go to out/ghidra-copy/prepare_<program>.json.

`dump` writes, per program, out/decomp/<program>/ (see out/decomp/README.md).
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import sqlite3
import struct
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GHIDRA = os.environ.get('GHIDRA_INSTALL_DIR', '/opt/homebrew/opt/ghidra/libexec')
JAVA_HOME = os.environ.get('JAVA_HOME', '/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home')
SRC_PROJECT_DIR = os.path.join(ROOT, 'ghidra')
PROJECT_NAME = 'dn2'
COPY_DIR = os.path.join(ROOT, 'out', 'ghidra-copy')
SETTINGS_DIR = os.path.join(ROOT, 'out', 'ghidra-settings')
EXT_DIR = os.path.join(ROOT, 'out', 'ghidra-ext')
LANG_SRC = os.path.join(ROOT, 'vendor', 'digikit', 'tools', 'ghidra', 'ColdfireEMAC')
MCF_LABELS = os.path.join(ROOT, 'vendor', 'digikit', 'tools', 'ghidra', 'McfLabels.java')
RTTI_JSON = os.path.join(ROOT, 'analysis', 'rtti_classes.json')
OUT_DECOMP = os.path.join(ROOT, 'out', 'decomp')
EMAC_LANG = '68000:BE:32:ColdfireEMAC'
TOOL_VERSION = 1
# analysis options forced for prepare's re-analysis
NOT_ANALYZERS = {'Non-Returning Functions - Discovered': False}
MAX_STEM = 120

# MCF5441x peripheral windows. A survey of every 32-bit operand >= 0xEC000000 in
# both images found real register addresses only inside these two windows; the
# other values are masks and constants (0xEDB88320 is the CRC-32 polynomial).
MMIO_WINDOWS = ((0xEC000000, 0xEC100000), (0xFC000000, 0xFC100000))

# program key -> where it lives and what the dump needs to know about it.
#  image / image_offset: the file and the file offset of the program's first initialised byte
#  aliases: (run_lo, run_hi, file_lo): code copied at run time; call targets in
#           [run_lo, run_hi) are also mapped to file_lo + (target - run_lo)
PROGRAMS = {
    'main_os': {
        'program': '/main_os',
        'image': os.path.join(ROOT, 'ele3_out', '02_type3.unpacked.bin'),
        'file_base': 0x40000400, 'image_offset': 0, 'rtti': True,
        # An embedded blob linked at 0x0000C000 sits at file address 0x402F7D40
        # (37 of its 42 low absolute jsr targets land on function starts with
        # this delta). It is not MCF5441x code; see notes/decomp-dump.md.
        'aliases': [(0x0000C000, 0x0000C000 + (0x4030C980 - 0x402F7D40), 0x402F7D40)],
        'regions': [(0x402F7D40, 0x4030C980, 'embedded blob linked at 0x0000C000 (file - 0x402EBD40); '
                     'not MCF5441x main-OS code: its absolute addresses are in the blob\'s own map')],
    },
    'bootstrap': {
        'program': '/upgrader',
        'image': os.path.join(ROOT, 'ele3_out', '01_type2.unpacked.bin'),
        # file offset 0 = 0x800003FC; the program starts at file offset 4 = 0x80000400
        'file_base': 0x800003FC, 'image_offset': 4, 'rtti': False,
        # 0x80000e5a..0x80000e6e copies [0x80007900, 0x80007a56) to 0x8000F000
        'aliases': [(0x8000F000, 0x8000F156, 0x80007900)],
        'regions': [(0x80007900, 0x80007A56, 'copied to 0x8000F000 at boot (0x80000e5a) and run from there'),
                    (0x8000F000, 0x8000F156, 'run-time copy of 0x80007900..0x80007a55; no bytes here')],
    },
}


def log(msg):
    print('[%s] %s' % (time.strftime('%H:%M:%S'), msg), file=sys.stderr, flush=True)


def hx(v):
    return '0x%08x' % v if v is not None else ''


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def tsv_escape(s):
    if s is None:
        return ''
    return (str(s).replace('\\', '\\\\').replace('\t', '\\t').replace('\n', '\\n')
            .replace('\r', '\\r'))


def write_tsv(path, header, rows):
    tmp = path + '.tmp'
    with open(tmp, 'w') as f:
        f.write('\t'.join(header) + '\n')
        for r in rows:
            f.write('\t'.join(tsv_escape(c) for c in r) + '\n')
    os.replace(tmp, path)


def safe_name(name):
    return re.sub(r'[^A-Za-z0-9_.-]+', '_', name.replace('::', '__')) or 'unnamed'


def file_stem(entry, name):
    return ('%08x_%s' % (entry, safe_name(name)))[:MAX_STEM]


# ---------------------------------------------------------------- Ghidra start

_started = False


def start_ghidra(heap):
    global _started
    if _started:
        return
    os.environ.setdefault('GHIDRA_INSTALL_DIR', GHIDRA)
    os.environ.setdefault('JAVA_HOME', JAVA_HOME)
    from pyghidra.launcher import HeadlessPyGhidraLauncher
    launcher = HeadlessPyGhidraLauncher()
    launcher.add_vmargs('-Dapplication.settingsdir=' + SETTINGS_DIR, '-Xmx' + heap)
    launcher.start()
    _started = True


def open_copy():
    from ghidra.base.project import GhidraProject
    if not os.path.exists(os.path.join(COPY_DIR, PROJECT_NAME + '.gpr')):
        raise SystemExit('no project copy in %s: run setup first' % COPY_DIR)
    if os.path.exists(os.path.join(COPY_DIR, PROJECT_NAME + '.lock')):
        raise SystemExit('%s/%s is locked by another process' % (COPY_DIR, PROJECT_NAME))
    return GhidraProject.openProject(COPY_DIR, PROJECT_NAME, False)


def open_program(proj, key, read_only):
    path = PROGRAMS[key]['program']
    return proj.openProgram('/', path.lstrip('/'), read_only)


def monitor():
    from ghidra.util.task import ConsoleTaskMonitor
    return ConsoleTaskMonitor()


# ---------------------------------------------------------------------- setup

def cmd_setup(args):
    # 1. the ColdfireEMAC language, compiled into out/ghidra-ext/
    mod = os.path.join(EXT_DIR, 'ColdfireEMAC')
    sla = os.path.join(mod, 'data', 'languages', 'coldfire_emac.sla')
    if not os.path.exists(sla):
        if not os.path.isdir(LANG_SRC):
            raise SystemExit('missing %s (vendor/digikit clone)' % LANG_SRC)
        os.makedirs(EXT_DIR, exist_ok=True)
        if os.path.exists(mod):
            shutil.rmtree(mod)
        shutil.copytree(LANG_SRC, mod)
        env = dict(os.environ, JAVA_HOME=JAVA_HOME,
                   PATH=os.path.join(JAVA_HOME, 'bin') + os.pathsep + os.environ.get('PATH', ''))
        subprocess.run([os.path.join(GHIDRA, 'support', 'sleigh'),
                        os.path.join(mod, 'data', 'languages', 'coldfire_emac.slaspec')],
                       check=True, env=env, stdout=subprocess.DEVNULL)
        log('compiled %s' % sla)
    props = {}
    with open(os.path.join(GHIDRA, 'Ghidra', 'application.properties')) as f:
        for line in f:
            if '=' in line:
                k, v = line.strip().split('=', 1)
                props[k] = v
    ver, rel = props['application.version'], props['application.release.name']
    with open(os.path.join(mod, 'extension.properties'), 'w') as f:
        f.write('name=ColdfireEMAC\ndescription=ColdFire language with MOVCLR and corrected '
                'EMAC instructions\nauthor=digitakt2\ncreatedOn=\nversion=%s\n' % ver)
    ext = os.path.join(SETTINGS_DIR, 'ghidra', 'ghidra_%s_%s' % (ver, rel), 'Extensions')
    os.makedirs(ext, exist_ok=True)
    link = os.path.join(ext, 'ColdfireEMAC')
    if os.path.islink(link) or os.path.exists(link):
        os.remove(link)
    os.symlink(mod, link)
    log('linked %s -> %s' % (link, mod))
    # 2. the project copy
    if os.path.exists(os.path.join(COPY_DIR, PROJECT_NAME + '.gpr')):
        log('project copy exists: %s (left as is)' % COPY_DIR)
    else:
        if os.path.exists(os.path.join(SRC_PROJECT_DIR, PROJECT_NAME + '.lock')):
            raise SystemExit('ghidra/%s is open in another process; close it first' % PROJECT_NAME)
        os.makedirs(COPY_DIR, exist_ok=True)
        shutil.copy2(os.path.join(SRC_PROJECT_DIR, PROJECT_NAME + '.gpr'), COPY_DIR)
        shutil.copytree(os.path.join(SRC_PROJECT_DIR, PROJECT_NAME + '.rep'),
                        os.path.join(COPY_DIR, PROJECT_NAME + '.rep'))
        log('copied ghidra/%s to %s' % (PROJECT_NAME, COPY_DIR))
    # 3. the language is visible to a JVM started with the private settings dir
    start_ghidra(args.heap)
    from ghidra.program.util import DefaultLanguageService
    ids = [str(d.getLanguageID()) for d in
           DefaultLanguageService.getLanguageService().getLanguageDescriptions(False)]
    if EMAC_LANG not in ids:
        raise SystemExit('%s not visible to Ghidra after setup' % EMAC_LANG)
    log('%s available' % EMAC_LANG)


# -------------------------------------------------------------------- prepare

def bad_constructor_sites(p):
    """-> [(address, flow_from)] of 'Unable to resolve constructor' error bookmarks."""
    out = []
    it = p.getBookmarkManager().getBookmarksIterator('Error')
    while it.hasNext():
        b = it.next()
        if 'Unable to resolve constructor' in str(b.getComment()):
            m = re.search(r'flow from ([0-9a-fA-F]+)', str(b.getComment()))
            out.append((hx(int(b.getAddress().getOffset())), ('0x' + m.group(1)) if m else None))
    return out


def program_stats(p):
    listing = p.getListing()
    return {
        'bad_constructor_sites': bad_constructor_sites(p),
        'language': str(p.getLanguageID()),
        'functions': int(p.getFunctionManager().getFunctionCount()),
        'instructions': int(listing.getNumInstructions()),
        'defined_data': int(listing.getNumDefinedData()),
        'error_bookmarks': int(p.getBookmarkManager().getBookmarkCount('Error')),
        'blocks': [[str(b.getName()), hx(int(b.getStart().getOffset())),
                    hx(int(b.getEnd().getOffset())), bool(b.isInitialized()), bool(b.isVolatile())]
                   for b in p.getMemory().getBlocks()],
    }


def step_language(p):
    from ghidra.program.util import DefaultLanguageService
    from ghidra.program.model.lang import LanguageID, CompilerSpecID
    if str(p.getLanguageID()) == EMAC_LANG:
        return 'already %s' % EMAC_LANG
    lang = DefaultLanguageService.getLanguageService().getLanguage(LanguageID(EMAC_LANG))
    old = str(p.getLanguageID())
    p.setLanguage(lang, CompilerSpecID('default'), False, monitor())
    return '%s -> %s' % (old, EMAC_LANG)


def step_bad_sites(p, sites):
    """Re-disassemble the sites stock Ghidra could not decode (recorded before the
    language switch, which drops the bookmarks); fix the containing bodies."""
    from ghidra.app.cmd.disassemble import DisassembleCommand
    from ghidra.app.cmd.function import CreateFunctionCmd
    fm = p.getFunctionManager()
    listing = p.getListing()
    bm = p.getBookmarkManager()
    space = p.getAddressFactory().getDefaultAddressSpace()
    fixed, still_bad = [], []
    for site, frm in sites:
        addr = space.getAddress(int(site, 16))
        for b in bm.getBookmarks(addr, 'Error'):
            bm.removeBookmark(b)
        if listing.getInstructionAt(addr) is None:
            DisassembleCommand(addr, None, True).applyTo(p, monitor())
        ins = listing.getInstructionAt(addr)
        if ins is not None:
            fixed.append('%s %s' % (site, ins))
            fn = fm.getFunctionContaining(space.getAddress(int(frm, 16))) if frm else None
            fn = fn or fm.getFunctionContaining(addr)
            if fn is not None:
                CreateFunctionCmd.fixupFunctionBody(p, fn, monitor())
        else:
            still_bad.append(site)
    return {'sites': len(sites), 'decoded_now': fixed, 'still_bad': still_bad}


def step_mmio_blocks(p):
    mem = p.getMemory()
    space = p.getAddressFactory().getDefaultAddressSpace()
    made = []
    for lo, hi in MMIO_WINDOWS:
        start, end = space.getAddress(lo), space.getAddress(hi - 1)
        if mem.getBlock(start) is not None or mem.getBlock(end) is not None:
            continue
        clash = [b for b in mem.getBlocks()
                 if b.getStart().getOffset() < hi and b.getEnd().getOffset() >= lo]
        if clash:
            continue
        b = mem.createUninitializedBlock('MMIO_%X' % (lo >> 20), start, hi - lo, False)
        b.setRead(True)
        b.setWrite(True)
        b.setExecute(False)
        b.setVolatile(True)
        made.append('MMIO_%X' % (lo >> 20))
    return made


def step_mcf_labels(proj, p):
    import pyghidra
    if not os.path.exists(MCF_LABELS):
        return 'missing %s' % MCF_LABELS
    from ghidra.app.script import GhidraScriptUtil
    from generic.jar import ResourceFile
    from java.io import File
    ns = p.getSymbolTable().getNamespace('MCF5441X_MMIO', p.getGlobalNamespace())
    if ns is not None:
        return 'already applied'
    # A Java GhidraScript must sit in a registered script bundle directory.
    GhidraScriptUtil.acquireBundleHostReference()
    try:
        host = GhidraScriptUtil.getBundleHost()
        host.add(ResourceFile(File(os.path.dirname(MCF_LABELS))), True, False)
        out, err = pyghidra.ghidra_script(MCF_LABELS, proj.getProject(), p, [], False, False)
    finally:
        GhidraScriptUtil.releaseBundleHostReference()
    lines = [l for l in out.splitlines() if l.startswith('done:')]
    return lines[-1] if lines else (out.strip()[-300:] + err.strip()[-300:])


def load_rtti():
    with open(RTTI_JSON) as f:
        raw = json.load(f)
    tis = {}
    for k, v in raw.items():
        bases = [int(b['addr'], 16) for b in v.get('bases_resolved', [])]
        tis[int(k, 16)] = {'name': v.get('demangled') or v['name'], 'kind': v['kind'], 'bases': bases}
        for b in v.get('bases_resolved', []):
            a = int(b['addr'], 16)
            if a not in tis and a not in [int(x, 16) for x in raw]:
                tis[a] = {'name': b.get('demangled') or b['name'], 'kind': 'class', 'bases': []}
    return tis


def image_words(cfg):
    with open(cfg['image'], 'rb') as f:
        data = f.read()[cfg['image_offset']:]
    base = cfg['file_base'] + cfg['image_offset']
    return data, base


def code_end_of(p, below):
    """End of the code area: the highest function body end below `below`."""
    hi = 0
    for fn in p.getFunctionManager().getFunctions(True):
        e = int(fn.getEntryPoint().getOffset())
        if e < below:
            hi = max(hi, int(fn.getBody().getMaxAddress().getOffset()) + 1)
    return hi


def find_vtables(p, cfg, tis):
    """Itanium ABI vtables: [offset_to_top][typeinfo][slot 0][slot 1]..., 4-byte aligned."""
    data, base = image_words(cfg)
    listing = p.getListing()
    space = p.getAddressFactory().getDefaultAddressSpace()
    lo_code = base
    hi_code = code_end_of(p, min(tis))
    n = len(data) // 4
    words = struct.unpack_from('>%dI' % n, data, 0)

    def word(a):
        i = (a - base) // 4
        return words[i] if 0 <= i < n and (a - base) % 4 == 0 else None

    code_cache = {}

    def in_code(v):
        """Even, inside the code area, and not inside another instruction. Defined data
        is accepted: Ghidra turned some real prologues into pointer data."""
        if v is None or v & 1 or not lo_code <= v < hi_code:
            return False
        if v in code_cache:
            return code_cache[v]
        a = space.getAddress(v)
        ins = listing.getInstructionContaining(a)
        ok = ins is None or ins.getAddress().equals(a)
        code_cache[v] = ok
        return ok

    vtables = []
    for i, w in enumerate(words):
        if w not in tis:
            continue
        at = base + 4 * i
        start = at - 4
        off = word(start)
        if off is None:
            continue
        off = off - (1 << 32) if off & 0x80000000 else off
        if not -0x10000 <= off <= 0:
            continue
        slots = []
        s = word(at + 4)
        while in_code(s):
            slots.append(s)
            s = word(at + 4 + 4 * len(slots))
        if slots:
            vtables.append({'start': start, 'point': start + 8, 'typeinfo': w,
                            'class': tis[w]['name'], 'offset_to_top': off, 'slots': slots})
    primary = {}
    for vt in vtables:
        if vt['offset_to_top'] == 0:
            primary.setdefault(vt['typeinfo'], vt)

    def all_bases(t):
        out, todo = [], list(tis.get(t, {}).get('bases', []))
        while todo:
            b = todo.pop(0)
            if b not in out:
                out.append(b)
                todo.extend(tis.get(b, {}).get('bases', []))
        return out

    def owner_of(vt, i):
        """The most basic class whose primary vtable has the same function in slot i.
        For a secondary vtable (offset_to_top < 0), any base whose primary vtable
        has it in slot i; the class itself when none has (an override or a thunk)."""
        fn = vt['slots'][i]
        owner = vt['typeinfo']
        if vt['offset_to_top'] != 0:
            cands = [b for b in all_bases(owner) if b in primary
                     and i < len(primary[b]['slots']) and primary[b]['slots'][i] == fn]
            basic = [c for c in cands if not any(c2 in all_bases(c) for c2 in cands)]
            return basic[0] if basic else owner
        seen = {owner}
        while tis.get(owner, {}).get('bases'):
            b = tis[owner]['bases'][0]
            bvt = primary.get(b)
            if b in seen or bvt is None or i >= len(bvt['slots']) or bvt['slots'][i] != fn:
                break
            seen.add(b)
            owner = b
        return owner

    for vt in vtables:
        vt['owners'] = [tis[owner_of(vt, i)]['name'] for i in range(len(vt['slots']))]
    return vtables, (lo_code, hi_code)


def label_name(prefix, cls, off=0):
    n = prefix + '_' + safe_name(cls).replace('.', '_')
    return n + ('_at%d' % -off if off else '')


def step_rtti(p, cfg):
    from ghidra.program.model.symbol import SourceType
    from ghidra.program.model.data import PointerDataType
    from ghidra.program.model.data import DataUtilities
    from ghidra.app.cmd.disassemble import DisassembleCommand
    from ghidra.app.cmd.function import CreateFunctionCmd
    from ghidra.program.model.symbol import SymbolUtilities
    tis = load_rtti()
    vtables, code = find_vtables(p, cfg, tis)
    space = p.getAddressFactory().getDefaultAddressSpace()
    st = p.getSymbolTable()
    fm = p.getFunctionManager()
    listing = p.getListing()
    clear = DataUtilities.ClearDataMode.CLEAR_ALL_UNDEFINED_CONFLICT_DATA
    ptr = PointerDataType()
    stats = {'typeinfos': len(tis), 'vtables': len(vtables), 'code_range': [hx(c) for c in code],
             'typeinfo_labels': 0, 'vtable_labels': 0, 'slot_pointers': 0,
             'functions_created': 0, 'functions_named': 0, 'slot_disassembly_failed': []}

    def add_label(addr, name):
        a = space.getAddress(addr)
        for s in st.getSymbols(a):
            if s.getName() == name:
                return False
        s = st.createLabel(a, name, SourceType.ANALYSIS)
        s.setPrimary()
        return True

    # Vtables are data. The original analysis had disassembled two of them (functions
    # at 0x401d9ff4 and 0x4020f36c): remove such functions and instructions first.
    from ghidra.program.model.address import AddressSet
    stats['functions_removed_in_vtables'] = []
    stats['vtables_with_instructions_cleared'] = 0
    for vt in vtables:
        lo = space.getAddress(vt['start'])
        hi = space.getAddress(vt['point'] + 4 * len(vt['slots']) - 1)
        rng = AddressSet(lo, hi)
        for fn in list(fm.getFunctionsOverlapping(rng)):
            if rng.contains(fn.getEntryPoint()):
                stats['functions_removed_in_vtables'].append('%s %s' % (fn.getEntryPoint(), fn.getName()))
                fm.removeFunction(fn.getEntryPoint())
        if listing.getInstructions(rng, True).hasNext():
            first = listing.getInstructions(rng, True).next()
            listing.clearCodeUnits(first.getAddress(), hi, False)
            stats['vtables_with_instructions_cleared'] += 1
    for t, ti in tis.items():
        if p.getMemory().contains(space.getAddress(t)) and add_label(t, label_name('typeinfo', ti['name'])):
            stats['typeinfo_labels'] += 1
    for vt in vtables:
        if add_label(vt['point'], label_name('vtable', vt['class'], vt['offset_to_top'])):
            stats['vtable_labels'] += 1
        for i in range(len(vt['slots'])):
            a = space.getAddress(vt['point'] + 4 * i)
            d = listing.getDefinedDataAt(a)
            if d is None or not d.isPointer():
                try:
                    DataUtilities.createData(p, a, ptr, -1, clear)
                    stats['slot_pointers'] += 1
                except Exception:
                    pass
    # functions at slot targets
    targets = {}
    for vt in vtables:
        for i, (s, owner) in enumerate(zip(vt['slots'], vt['owners'])):
            targets.setdefault(s, set()).add((owner, i, vt['offset_to_top'], vt['class']))
    stats['data_cleared_at_slot_targets'] = []
    for s in sorted(targets):
        a = space.getAddress(s)
        if fm.getFunctionAt(a) is not None:
            continue
        if listing.getInstructionAt(a) is None:
            d = listing.getDefinedDataContaining(a)
            if d is not None:  # Ghidra made data of a real prologue (e.g. 0x40018818)
                stats['data_cleared_at_slot_targets'].append('%s %s' % (hx(s), d))
                listing.clearCodeUnits(d.getAddress(), d.getMaxAddress(), False)
            DisassembleCommand(a, None, True).applyTo(p, monitor())
        if listing.getInstructionAt(a) is None:
            stats['slot_disassembly_failed'].append(hx(s))
            continue
        cmd = CreateFunctionCmd(a)
        if cmd.applyTo(p, monitor()):
            stats['functions_created'] += 1
    # Inferred names for unnamed slot targets: <owner>::vslotNN when the owner is a
    # base class (inherited) or the class's primary vtable; <class>::vslotNN_atK for a
    # function only found in the class's secondary vtable at offset -K (thunks).
    # Applied only when every vtable that holds the function gives the same name.
    for s, uses in sorted(targets.items()):
        fn = fm.getFunctionAt(space.getAddress(s))
        if fn is None or fn.getSymbol().getSource() not in (SourceType.DEFAULT,):
            continue
        cand = set()
        for owner, slot, off, cls in uses:
            short = 'vslot%02d' % slot + ('_at%d' % -off if off and owner == cls else '')
            cand.add((owner, short))
        if len(cand) != 1:
            stats['names_ambiguous'] = stats.get('names_ambiguous', 0) + 1
            continue
        owner, short = next(iter(cand))
        name = '%s::%s' % (owner, short)
        flat = safe_name(owner).replace('.', '_') + '__' + short
        try:
            if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*(::[A-Za-z_][A-Za-z0-9_]*)*', owner):
                from ghidra.app.util import NamespaceUtils
                ns = NamespaceUtils.createNamespaceHierarchy(owner, None, p, SourceType.ANALYSIS)
                fn.getSymbol().setNameAndNamespace(short, ns, SourceType.ANALYSIS)
            else:
                fn.setName(flat, SourceType.ANALYSIS)
            stats['functions_named'] += 1
        except Exception as e:  # name clash or invalid name: leave the default name
            stats.setdefault('name_errors', []).append('%s %s: %s' % (hx(s), name, e))
    return stats


def orphan_runs(p):
    """-> [(start, n_instructions, previous_instruction)] for runs of instructions in no function."""
    listing = p.getListing()
    fm = p.getFunctionManager()
    runs, cur, prev_end = [], None, None
    it = listing.getInstructions(True)
    while it.hasNext():
        ins = it.next()
        a = int(ins.getAddress().getOffset())
        if fm.getFunctionContaining(ins.getAddress()) is not None:
            cur = None
            continue
        if cur is not None and a == prev_end:
            cur[1] += 1
        else:
            cur = [a, 1]
            runs.append(cur)
        prev_end = a + ins.getLength()
    return runs


def step_orphans(p):
    """Create a function at the start of every run of disassembled code that no function
    contains. Most follow a rts/bra/jmp and are reached only through pointers."""
    from ghidra.app.cmd.function import CreateFunctionCmd
    listing = p.getListing()
    fm = p.getFunctionManager()
    space = p.getAddressFactory().getDefaultAddressSpace()
    before = orphan_runs(p)
    created = {'after_terminal': [], 'after_fallthrough': [], 'failed': []}
    for _ in range(3):
        runs = orphan_runs(p)
        if not runs:
            break
        for start, n in runs:
            a = space.getAddress(start)
            if fm.getFunctionContaining(a) is not None:
                continue
            prev = listing.getInstructionBefore(a)
            terminal = (prev is None or prev.getAddress().add(prev.getLength()).compareTo(a) != 0
                        or not prev.getFlowType().hasFallthrough())
            if not terminal:
                # first try to grow the function that falls through into the run
                fn = fm.getFunctionContaining(prev.getAddress())
                if fn is not None:
                    CreateFunctionCmd.fixupFunctionBody(p, fn, monitor())
                    if fm.getFunctionContaining(a) is not None:
                        continue
            if CreateFunctionCmd(a).applyTo(p, monitor()):
                created['after_terminal' if terminal else 'after_fallthrough'].append(hx(start))
            else:
                created['failed'].append(hx(start))
    after = orphan_runs(p)
    return {'runs_before': len(before), 'instructions_before': sum(n for _, n in before),
            'runs_after': len(after), 'instructions_after': sum(n for _, n in after),
            'created_after_terminal': len(created['after_terminal']),
            'created_after_fallthrough': created['after_fallthrough'],
            'failed': created['failed']}


def fix_conflicts(p):
    """Flow into defined data ('Failed to disassemble at X due to conflicting data'): the
    flow comes from code (switch cases such as 0x400700ae, 0x401103c8), so clear the data,
    disassemble, and repair the body of the function the flow comes from."""
    from ghidra.app.cmd.disassemble import DisassembleCommand
    from ghidra.app.cmd.function import CreateFunctionCmd
    bm = p.getBookmarkManager()
    listing = p.getListing()
    fm = p.getFunctionManager()
    space = p.getAddressFactory().getDefaultAddressSpace()
    fixed, failed, sites = [], [], 0
    # Disassembly stops again at the next bogus data item and bookmarks it at once,
    # so repeat until no new site appears.
    for _ in range(64):
        todo = []
        it = bm.getBookmarksIterator('Error')
        while it.hasNext():
            b = it.next()
            c = str(b.getComment())
            if 'due to conflicting data' in c:
                m = re.search(r'flow from ([0-9a-fA-F]+)', c)
                todo.append((b, b.getAddress(), m.group(1) if m else None))
        todo = [t for t in todo if hx(int(t[1].getOffset())) not in failed]
        if not todo:
            break
        sites += len(todo)
        for b, addr, frm in todo:
            bm.removeBookmark(b)
            d = listing.getDefinedDataContaining(addr)
            # only when the flow comes from inside a function
            src = fm.getFunctionContaining(space.getAddress(int(frm, 16))) if frm else None
            if d is None or src is None:
                failed.append(hx(int(addr.getOffset())))
                continue
            listing.clearCodeUnits(d.getAddress(), d.getMaxAddress(), False)
            DisassembleCommand(addr, None, True).applyTo(p, monitor())
            if listing.getInstructionAt(addr) is None:
                failed.append(hx(int(addr.getOffset())))
                continue
            fixed.append(hx(int(addr.getOffset())))
            fn = fm.getFunctionContaining(space.getAddress(int(frm, 16))) if frm else None
            prev = listing.getInstructionBefore(addr)
            fn = fn or (fm.getFunctionContaining(prev.getAddress()) if prev is not None else None)
            if fn is not None:
                CreateFunctionCmd.fixupFunctionBody(p, fn, monitor())
    return {'sites': sites, 'fixed': fixed, 'failed': failed}


def cmd_prepare(args):
    import pyghidra
    start_ghidra(args.heap)
    proj = open_copy()
    try:
        for key in args.programs:
            cfg = PROGRAMS[key]
            report_path = os.path.join(COPY_DIR, 'prepare_%s.json' % key)
            report = {}
            if os.path.exists(report_path):
                with open(report_path) as f:
                    report = json.load(f)
            p = open_program(proj, key, False)
            try:
                report.setdefault('before', program_stats(p))
                steps = report.setdefault('steps', {})
                changed = False
                for name, fn in (('language', lambda: step_language(p)),
                                 ('bad_sites', lambda: step_bad_sites(p, report['before']['bad_constructor_sites'])),
                                 ('mmio_blocks', lambda: step_mmio_blocks(p)),
                                 ('mcf_labels', lambda: step_mcf_labels(proj, p)),
                                 ('rtti', lambda: step_rtti(p, cfg) if cfg['rtti'] else 'n/a'),
                                 ('orphans', lambda: step_orphans(p))):
                    if name in steps and not args.force:
                        log('%s: %s already done' % (key, name))
                        continue
                    t = time.time()
                    tx = p.startTransaction('prepare ' + name)
                    ok = False
                    try:
                        res = fn()
                        ok = True
                    finally:
                        p.endTransaction(tx, ok)
                    steps[name] = {'result': res, 'seconds': round(time.time() - t, 1)}
                    changed = True
                    log('%s: %s -> %s' % (key, name, json.dumps(res)[:400]))
                if changed or 'analysis' not in steps or args.force:
                    t = time.time()
                    log('%s: auto-analysis (this takes a while)' % key)
                    # The no-return discovery analyzer marked ordinary functions such as
                    # 0x4002c318 as non-returning on a re-run and then cleared the code
                    # after every call to them (6274 instructions). Keep it off.
                    tx = p.startTransaction('analysis options')
                    try:
                        opts = pyghidra.analysis_properties(p)
                        for name, value in NOT_ANALYZERS.items():
                            opts.setBoolean(name, value)
                    finally:
                        p.endTransaction(tx, True)
                    pyghidra.analyze(p)
                    steps['analysis'] = {'seconds': round(time.time() - t, 1)}
                    log('%s: analysis done in %.0f s' % (key, time.time() - t))
                    steps.pop('post_conflicts', None)
                if 'post_conflicts' not in steps or args.force:
                    t = time.time()
                    rounds = []
                    for _ in range(2):
                        tx = p.startTransaction('conflicting data')
                        try:
                            res = fix_conflicts(p)
                        finally:
                            p.endTransaction(tx, True)
                        rounds.append(res)
                        log('%s: conflicting-data sites -> %s' % (key, json.dumps(res)[:300]))
                        if not res['fixed']:
                            break
                        pyghidra.analyze(p)
                    steps['post_conflicts'] = {'rounds': rounds, 'seconds': round(time.time() - t, 1)}
                report['after'] = program_stats(p)
                proj.save(p)
            finally:
                proj.close(p)
            with open(report_path, 'w') as f:
                json.dump(report, f, indent=1)
            log('%s: before %s' % (key, {k: v for k, v in report['before'].items() if k != 'blocks'}))
            log('%s: after  %s' % (key, {k: v for k, v in report['after'].items() if k != 'blocks'}))
    finally:
        proj.close()


# ----------------------------------------------------------------------- dump

def collect(p, cfg, out):
    """Walk every function once. Writes disasm/<stem>.s on the way."""
    from ghidra.program.model.listing import CodeUnitFormat
    from ghidra.program.model.scalar import Scalar
    from ghidra.program.model.address import Address
    from ghidra.program.model.lang import OperandType
    fmt = CodeUnitFormat.DEFAULT
    listing = p.getListing()
    mem = p.getMemory()
    st = p.getSymbolTable()
    fm = p.getFunctionManager()
    space = p.getAddressFactory().getDefaultAddressSpace()
    disasm_dir = os.path.join(out, 'disasm')
    funcs = {}

    def in_mmio(v):
        return any(lo <= v < hi for lo, hi in MMIO_WINDOWS)

    it = fm.getFunctions(True)
    while it.hasNext():
        fn = it.next()
        entry = int(fn.getEntryPoint().getOffset())
        name = str(fn.getName(True))
        body = fn.getBody()
        ranges = [(int(r.getMinAddress().getOffset()), int(r.getMaxAddress().getOffset()))
                  for r in body.getAddressRanges()]
        f = {'entry': entry, 'name': name, 'size': int(body.getNumAddresses()),
             'ranges': ranges, 'signature': str(fn.getSignature()),
             'thunk': bool(fn.isThunk()), 'stem': file_stem(entry, name),
             'calls': [], 'jumps': [], 'drefs': [], 'mmio': [], 'insns': 0}
        lines = ['; FUNCTION %s  %s' % (hx(entry), name),
                 '; size %d bytes, ranges %s' % (f['size'], ' '.join('%s-%s' % (hx(a), hx(b)) for a, b in ranges)),
                 '; %s' % f['signature']]
        insns = listing.getInstructions(body, True)
        while insns.hasNext():
            ins = insns.next()
            f['insns'] += 1
            site = int(ins.getAddress().getOffset())
            lab = st.getPrimarySymbol(ins.getAddress())
            if lab is not None and site != entry and not str(lab.getName()).startswith('LAB_'):
                lines.append('%s:' % lab.getName(True))
            raw = bytes(b & 0xFF for b in ins.getBytes())
            words = ' '.join(raw[i:i + 2].hex() for i in range(0, len(raw), 2))
            text = str(fmt.getRepresentationString(ins))
            lines.append('%08x  %-24s  %s' % (site, words, text))
            for ref in ins.getReferencesFrom():
                to = ref.getToAddress()
                if not to.isMemoryAddress():
                    continue
                rt = ref.getReferenceType()
                t = int(to.getOffset())
                if rt.isCall():
                    f['calls'].append((site, t, str(rt)))
                elif rt.isFlow():
                    if rt.isJump() and not body.contains(to):
                        f['jumps'].append((site, t, str(rt)))
                else:
                    f['drefs'].append((site, t, str(rt)))
            # unresolved absolute call targets (outside memory): Ghidra keeps no reference
            mnem = str(ins.getMnemonicString()).lower()
            if mnem in ('jsr', 'bsr', 'jmp') and not any(r.getReferenceType().isFlow() for r in ins.getReferencesFrom()):
                for o in ins.getOpObjects(0):
                    if isinstance(o, (Scalar, Address)):
                        v = int(o.getUnsignedValue() if isinstance(o, Scalar) else o.getOffset()) & 0xffffffff
                        f['calls' if mnem != 'jmp' else 'jumps'].append((site, v, 'UNRESOLVED_' + mnem.upper()))
            # MMIO operands
            for op in range(ins.getNumOperands()):
                optype = ins.getOperandType(op)
                for o in ins.getOpObjects(op):
                    if isinstance(o, Scalar):
                        v = int(o.getUnsignedValue()) & 0xffffffff
                    elif isinstance(o, Address):
                        v = int(o.getOffset())
                    else:
                        continue
                    if not in_mmio(v):
                        continue
                    if mnem in ('lea', 'pea'):
                        acc = 'addr'
                    elif OperandType.isScalar(optype) and not OperandType.isAddress(optype):
                        acc = 'imm'
                    elif op == 0:
                        acc = 'R'
                    elif mnem.startswith(('move', 'clr', 'mov3q', 'mvs', 'mvz')):
                        acc = 'W'
                    else:
                        acc = 'RW'
                    f['mmio'].append((site, v, acc, mnem, text))
        with open(os.path.join(disasm_dir, f['stem'] + '.s'), 'w') as fh:
            fh.write('\n'.join(lines) + '\n')
        funcs[entry] = f
    return funcs


def string_table(p, cfg):
    """Ghidra's defined strings plus raw NUL-terminated ASCII runs it did not define."""
    listing = p.getListing()
    space = p.getAddressFactory().getDefaultAddressSpace()
    strings = {}
    it = listing.getDefinedData(True)
    while it.hasNext():
        d = it.next()
        if d.hasStringValue():
            v = d.getValue()
            if v is not None:
                strings[int(d.getAddress().getOffset())] = (str(v), 'ghidra')
    data, base = image_words(cfg)
    covered = set()
    for a, (t, _) in strings.items():
        covered.update(range(a, a + len(t.encode('latin-1', 'replace')) + 1))
    for m in re.finditer(rb'[\x20-\x7e\t\n\r]{3,}\x00', data):
        a = base + m.start()
        if a in covered:
            continue
        txt = m.group()[:-1].decode('ascii')
        if not re.search(r'[A-Za-z0-9]', txt) or txt.strip() == '':
            continue
        if listing.getInstructionContaining(space.getAddress(a)) is not None:
            continue
        strings[a] = (txt, 'raw')
    return strings


def string_refs(p, strings, func_of):
    """-> {string_addr: [(kind, site, func_entry, via)]}.

    direct: an instruction references the string.
    via_ptr: a pointer in data references the string, and an instruction
    references that pointer, or the table/struct base below it: the first
    address with a symbol or a reference, at most 0x1000 bytes down. `via` is
    base+offset. data_only: no code reference found that way.
    """
    rm = p.getReferenceManager()
    st = p.getSymbolTable()
    listing = p.getListing()
    space = p.getAddressFactory().getDefaultAddressSpace()

    cache = {}

    def code_refs_to(a):
        key = int(a.getOffset())
        if key in cache:
            return cache[key]
        out = []
        for r in rm.getReferencesTo(a):
            frm = r.getFromAddress()
            if listing.getInstructionAt(frm) is not None:
                out.append(int(frm.getOffset()))
        cache[key] = out
        return out

    res = {}
    for s in sorted(strings):
        rows = []
        for r in rm.getReferencesTo(space.getAddress(s)):
            frm = r.getFromAddress()
            if not frm.isMemoryAddress():
                continue
            fo = int(frm.getOffset())
            if listing.getInstructionAt(frm) is not None:
                rows.append(('direct', fo, func_of(fo), ''))
                continue
            # A pointer in data. Walk down from it to the first address that has a
            # symbol or any reference (the table or struct base). If code references
            # that base, the string is used via_ptr base+offset; otherwise data_only.
            found = False
            for back in range(0, 0x1004, 2):
                base = fo - back
                ba = space.getAddress(base)
                hits = code_refs_to(ba)
                if hits:
                    for h in hits:
                        rows.append(('via_ptr', h, func_of(h), '%s+0x%x' % (hx(base), back)))
                    found = True
                    break
                if back and (rm.hasReferencesTo(ba) or st.getPrimarySymbol(ba) is not None):
                    break
            if not found:
                rows.append(('data_only', fo, None, hx(fo)))
        res[s] = rows
    return res


def decompile(p, funcs, entries, out, threads, timeout, headers):
    from ghidra.app.decompiler import DecompInterface, DecompileOptions
    fm = p.getFunctionManager()
    space = p.getAddressFactory().getDefaultAddressSpace()
    local, opened, lock = threading.local(), [], threading.Lock()
    ddir = os.path.join(out, 'decomp')

    def interface():
        ifc = getattr(local, 'ifc', None)
        if ifc is None:
            ifc = DecompInterface()
            opts = DecompileOptions()
            opts.grabFromProgram(p)
            ifc.setOptions(opts)
            with lock:
                opened.append(ifc)
            if not ifc.openProgram(p):
                raise RuntimeError('decompiler did not start: %s' % ifc.getLastMessage())
            local.ifc = ifc
        return ifc

    def one(entry):
        try:
            fn = fm.getFunctionAt(space.getAddress(entry))
            res = interface().decompileFunction(fn, timeout, None)
            if not res.decompileCompleted():
                err = str(res.getErrorMessage() or 'did not complete').strip()
                body = '/* decompile failed: %s */\n' % err.replace('*/', '* /')
            else:
                err = None
                body = str(res.getDecompiledFunction().getC())
            with open(os.path.join(ddir, funcs[entry]['stem'] + '.c'), 'w') as fh:
                fh.write(headers[entry] + body)
            return entry, err
        except Exception as e:
            return entry, '%s: %s' % (type(e).__name__, e)

    errors, done = {}, 0
    t0 = last = time.time()
    try:
        with ThreadPoolExecutor(max_workers=threads) as pool:
            for fut in as_completed([pool.submit(one, e) for e in entries]):
                entry, err = fut.result()
                done += 1
                if err is not None:
                    errors[entry] = err
                now = time.time()
                if now - last >= 30 or done == len(entries):
                    rate = done / max(now - t0, 1e-9)
                    log('decompiled %d/%d, %.1f/s, %d failed, ~%.0f s left'
                        % (done, len(entries), rate, len(errors), (len(entries) - done) / rate))
                    last = now
    finally:
        for ifc in opened:
            ifc.dispose()
    return errors


SQL_TABLES = """
CREATE TABLE functions(entry INTEGER PRIMARY KEY, name TEXT, size INTEGER, callers INTEGER,
    call_sites_in INTEGER, callees INTEGER, ptr_refs_in INTEGER, vtable_slots INTEGER,
    signature TEXT, decomp_status TEXT, file TEXT, note TEXT);
CREATE TABLE function_ranges(func INTEGER, lo INTEGER, hi INTEGER);
CREATE TABLE calls(caller INTEGER, callee INTEGER, target INTEGER, site INTEGER, kind TEXT);
CREATE TABLE data_refs(site INTEGER, func INTEGER, target INTEGER, kind TEXT, label TEXT, block TEXT);
CREATE TABLE strings(addr INTEGER PRIMARY KEY, text TEXT, source TEXT);
CREATE TABLE string_refs(string INTEGER, kind TEXT, site INTEGER, func INTEGER, via TEXT);
CREATE TABLE mmio(func INTEGER, site INTEGER, addr INTEGER, label TEXT, access TEXT, insn TEXT);
CREATE TABLE vtables(point INTEGER, start INTEGER, class TEXT, typeinfo INTEGER,
    offset_to_top INTEGER, slot INTEGER, target INTEGER, target_name TEXT, owner TEXT);
CREATE TABLE symbols(addr INTEGER, name TEXT, type TEXT, source TEXT, is_primary INTEGER);
CREATE INDEX calls_caller ON calls(caller);
CREATE INDEX calls_callee ON calls(callee);
CREATE INDEX data_refs_target ON data_refs(target);
CREATE INDEX data_refs_func ON data_refs(func);
CREATE INDEX string_refs_s ON string_refs(string);
CREATE INDEX string_refs_f ON string_refs(func);
CREATE INDEX mmio_addr ON mmio(addr);
CREATE INDEX vtables_target ON vtables(target);
CREATE INDEX vtables_class ON vtables(class);
CREATE INDEX symbols_addr ON symbols(addr);
CREATE INDEX symbols_name ON symbols(name);
"""


def cmd_dump(args):
    start_ghidra(args.heap)
    proj = open_copy()
    try:
        for key in args.programs:
            dump_one(proj, key, args)
    finally:
        proj.close()


def dump_one(proj, key, args):
    cfg = PROGRAMS[key]
    out = os.path.join(OUT_DECOMP, key)
    for sub in ('decomp', 'disasm'):
        shutil.rmtree(os.path.join(out, sub), ignore_errors=True)
        os.makedirs(os.path.join(out, sub), exist_ok=True)
    manifest_path = os.path.join(out, 'manifest.json')
    p = open_program(proj, key, True)
    try:
        t_all = time.time()
        image_sha = sha256_file(cfg['image'])
        with open(cfg['image'], 'rb') as f:
            prog_bytes_sha = hashlib.sha256(f.read()[cfg['image_offset']:]).hexdigest()
        manifest = {
            'tool': 'scripts/decompile_dump.py', 'tool_version': TOOL_VERSION,
            'generated': datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds'),
            'project': os.path.join(COPY_DIR, PROJECT_NAME), 'program': cfg['program'],
            'language': str(p.getLanguageID()), 'compiler': str(p.getCompilerSpec().getCompilerSpecID()),
            'image': os.path.relpath(cfg['image'], ROOT), 'image_sha256': image_sha,
            'image_offset': cfg['image_offset'], 'file_base': hx(cfg['file_base']),
            'program_executable_sha256': str(p.getExecutableSHA256()),
            'program_bytes_match_image': str(p.getExecutableSHA256()) == prog_bytes_sha,
            'mmio_windows': [[hx(a), hx(b)] for a, b in MMIO_WINDOWS],
            'aliases': [[hx(a), hx(b), hx(c)] for a, b, c in cfg['aliases']],
            'complete': False,
        }
        with open(manifest_path, 'w') as f:
            json.dump(manifest, f, indent=1)
        fm = p.getFunctionManager()
        space = p.getAddressFactory().getDefaultAddressSpace()
        listing = p.getListing()
        mem = p.getMemory()
        st = p.getSymbolTable()
        rm = p.getReferenceManager()

        t = time.time()
        funcs = collect(p, cfg, out)
        log('%s: collected %d functions in %.0f s' % (key, len(funcs), time.time() - t))

        def func_of(a):
            fn = fm.getFunctionContaining(space.getAddress(a))
            return int(fn.getEntryPoint().getOffset()) if fn is not None else None

        def region_note(a):
            return '; '.join(n for lo, hi, n in cfg.get('regions', []) if lo <= a < hi)

        def alias(tgt):
            for lo, hi, flo in cfg['aliases']:
                if lo <= tgt < hi:
                    return flo + (tgt - lo)
            return None

        # call edges. A target inside an alias range (code copied at run time) also
        # gets an edge to the function at the copy's file address, kind *_ALIAS.
        def initialized(a):
            b = mem.getBlock(space.getAddress(a))
            return b is not None and b.isInitialized()

        calls = []
        for e in sorted(funcs):
            f = funcs[e]
            for site, tgt, kind in f['calls'] + [(s, t_, 'TAILJUMP_' + k) for s, t_, k in f['jumps']]:
                tail = kind.startswith('TAILJUMP_')
                if tail and tgt == e:
                    continue
                callee = tgt if tgt in funcs else None
                m = alias(tgt)
                if m is not None and m in funcs and (callee is None or not initialized(tgt)):
                    if callee is not None:
                        calls.append((e, callee, tgt, site, kind))
                    calls.append((e, m, tgt, site, kind + '_ALIAS'))
                elif callee is not None or not tail or kind.startswith('TAILJUMP_UNRESOLVED'):
                    calls.append((e, callee, tgt, site, kind))
        callers, callees, sites_in = {}, {}, {}
        for caller, callee, _, _, _ in calls:
            if callee is not None:
                callers.setdefault(callee, set()).add(caller)
                callees.setdefault(caller, set()).add(callee)
                sites_in[callee] = sites_in.get(callee, 0) + 1

        # data refs, with target labels/blocks
        target_info = {}

        def tinfo(a):
            if a in target_info:
                return target_info[a]
            ad = space.getAddress(a)
            sym = st.getPrimarySymbol(ad)
            blk = mem.getBlock(ad)
            target_info[a] = (str(sym.getName(True)) if sym is not None else '',
                              str(blk.getName()) if blk is not None else '')
            return target_info[a]

        drefs = []
        for e in sorted(funcs):
            for site, tgt, kind in funcs[e]['drefs']:
                lab, blk = tinfo(tgt)
                drefs.append((site, e, tgt, kind, lab, blk))

        # pointer refs to function entries (vtables, function tables)
        ptr_in = {}
        for e in funcs:
            n = 0
            for r in rm.getReferencesTo(space.getAddress(e)):
                if not r.getReferenceType().isFlow():
                    n += 1
            ptr_in[e] = n

        # vtables
        vt_rows = []
        vt_slots = {}
        if cfg['rtti']:
            tis = load_rtti()
            vtables, _ = find_vtables(p, cfg, tis)
            for vt in vtables:
                for i, (s, owner) in enumerate(zip(vt['slots'], vt['owners'])):
                    fn = fm.getFunctionAt(space.getAddress(s))
                    vt_rows.append((vt['point'], vt['start'], vt['class'], vt['typeinfo'],
                                    vt['offset_to_top'], i, s,
                                    str(fn.getName(True)) if fn is not None else '', owner))
                    vt_slots.setdefault(s, []).append('%s[%d]' % (vt['class'], i))
            log('%s: %d vtables, %d slots' % (key, len(vtables), len(vt_rows)))

        # strings
        t = time.time()
        strings = string_table(p, cfg)
        srefs = string_refs(p, strings, func_of)
        # Raw runs Ghidra did not define are kept when something references them
        # ("ARP"), or when they read like text: 6+ characters, 75% letters/digits/spaces.
        def texty(txt):
            good = sum(1 for ch in txt if ch.isalnum() or ch == ' ')
            return len(txt) >= 6 and good >= 0.75 * len(txt)

        for s in [s for s, (txt, src) in strings.items()
                  if src == 'raw' and not srefs.get(s) and not texty(txt)]:
            del strings[s]
            srefs.pop(s, None)
        func_strings = {}
        for s, rows in srefs.items():
            for kind, site, fe, via in rows:
                if fe is not None:
                    d = func_strings.setdefault(fe, {})
                    if d.get(s, ('', 'via_ptr'))[1] != 'direct':
                        d[s] = (strings[s][0], kind)
        log('%s: %d strings, %d with references, in %.0f s' % (
            key, len(strings), sum(1 for r in srefs.values() if r), time.time() - t))

        # MMIO labels (from McfLabels.java or anything else named in the windows)
        mmio_labels = {}
        for lo, hi in MMIO_WINDOWS:
            si = st.getSymbolIterator(space.getAddress(lo), True)
            while si.hasNext():
                s = si.next()
                a = int(s.getAddress().getOffset())
                if not s.getAddress().isMemoryAddress() or a >= hi:
                    break
                mmio_labels.setdefault(a, str(s.getName()))

        # headers for the decompiled files
        def names(es, cap=40):
            es = sorted(es)
            txt = ', '.join('%s %s' % (hx(x), funcs[x]['name']) for x in es[:cap])
            return txt + (', ... (%d more)' % (len(es) - cap) if len(es) > cap else '')

        headers = {}
        for e, f in funcs.items():
            h = ['// FUNCTION %s  %s' % (hx(e), f['name']),
                 '// size %d bytes, %d instructions, ranges %s' % (
                     f['size'], f['insns'], ' '.join('%s-%s' % (hx(a), hx(b)) for a, b in f['ranges'])),
                 '// signature: %s' % f['signature'],
                 ] + (['// note: %s' % region_note(e)] if region_note(e) else []) + [
                 '// callers (%d): %s' % (len(callers.get(e, ())), names(callers.get(e, ()))),
                 '// callees (%d): %s' % (len(callees.get(e, ())), names(callees.get(e, ())))]
            if vt_slots.get(e):
                h.append('// vtable slots: %s' % ', '.join(sorted(set(vt_slots[e]))[:40]))
            if ptr_in.get(e):
                h.append('// pointer refs to entry: %d' % ptr_in[e])
            if func_strings.get(e):
                ss = sorted(func_strings[e].items(), key=lambda x: (x[1][1] != 'direct', x[0]))
                h.append('// strings (%d; * = through a pointer table): %s' % (len(ss), ' | '.join(
                    '%s%s "%s"' % ('' if k == 'direct' else '*', hx(a), tsv_escape(t)[:80])
                    for a, (t, k) in ss[:40])) + (' | ...' if len(ss) > 40 else ''))
            if f['mmio']:
                h.append('// mmio: %s' % ', '.join(sorted({hx(a) for _, a, _, _, _ in f['mmio']})[:30]))
            headers[e] = '\n'.join(h) + '\n\n'

        # decompile
        entries = sorted(funcs)
        if args.limit:
            entries = entries[:args.limit]
        t = time.time()
        errors = decompile(p, funcs, entries, out, args.threads, args.timeout, headers)
        retry = sorted(e for e, m in errors.items() if 'timeout' in m.lower() or 'did not complete' in m.lower())
        if retry and args.retry_timeout > args.timeout:
            log('%s: retrying %d functions with timeout %d s' % (key, len(retry), args.retry_timeout))
            again = decompile(p, funcs, retry, out, min(args.threads, 4), args.retry_timeout, headers)
            for e in retry:
                if e in again:
                    errors[e] = again[e]
                else:
                    errors.pop(e, None)
        decomp_seconds = time.time() - t

        # MMIO rows: instruction operands, then constants/labels in the decompiled C
        mmio_rows = []
        pat = re.compile(r'(?:0x|DAT_|_)((?:ec|fc)0[0-9a-f]{5})\b', re.I)
        lab_pat = re.compile(r'\b(' + '|'.join(re.escape(v) for v in sorted(set(mmio_labels.values()), key=len, reverse=True)) + r')\b') if mmio_labels else None
        lab_addr = {}
        for a, n in mmio_labels.items():
            lab_addr.setdefault(n, a)
        for e in sorted(funcs):
            f = funcs[e]
            seen = set()
            for site, a, acc, mnem, text in f['mmio']:
                mmio_rows.append((e, f['name'], site, a, mmio_labels.get(a, ''), acc, text))
                seen.add(a)
            if e in entries:
                path = os.path.join(out, 'decomp', f['stem'] + '.c')
                try:
                    with open(path) as fh:
                        c = fh.read().split('\n\n', 1)[-1]
                except OSError:
                    continue
                found = {int(m.group(1), 16) for m in pat.finditer(c)}
                if lab_pat is not None:
                    found |= {lab_addr[m.group(1)] for m in lab_pat.finditer(c)}
                for a in sorted(found - seen):
                    if any(lo <= a < hi for lo, hi in MMIO_WINDOWS):
                        mmio_rows.append((e, f['name'], None, a, mmio_labels.get(a, ''), 'decomp', ''))

        # ---- write files
        def fname(x):
            return funcs[x]['name'] if x in funcs else ''

        status = {e: ('ok' if e not in errors else 'failed') if e in entries else 'not_run' for e in funcs}
        frows = []
        for e in sorted(funcs):
            f = funcs[e]
            frows.append((hx(e), f['name'], f['size'], len(callers.get(e, ())), sites_in.get(e, 0),
                          len(callees.get(e, ())), ptr_in.get(e, 0), len(vt_slots.get(e, ())),
                          f['signature'], status[e], 'decomp/%s.c' % f['stem'], region_note(e)))
        write_tsv(os.path.join(out, 'functions.tsv'),
                  ['entry', 'name', 'size', 'callers', 'call_sites_in', 'callees', 'ptr_refs_in',
                   'vtable_slots', 'signature', 'decomp', 'file', 'note'], frows)
        write_tsv(os.path.join(out, 'calls.tsv'),
                  ['caller', 'caller_name', 'callee', 'callee_name', 'target', 'site', 'kind'],
                  [(hx(a), fname(a), hx(b), fname(b) if b is not None else '', hx(t_), hx(s), k)
                   for a, b, t_, s, k in calls])
        write_tsv(os.path.join(out, 'datarefs.tsv'),
                  ['site', 'func', 'func_name', 'target', 'kind', 'target_label', 'block'],
                  [(hx(s), hx(fe), fname(fe), hx(t_), k, lab, blk) for s, fe, t_, k, lab, blk in drefs])
        srows = []
        for s in sorted(strings):
            txt, src = strings[s]
            rows = srefs.get(s, [])
            fl = sorted({fe for _, _, fe, _ in rows if fe is not None})
            srows.append((hx(s), txt, src, len(rows),
                          ' '.join('%s:%s' % (hx(fe), fname(fe)) for fe in fl),
                          ' '.join('%s:%s%s' % (k, hx(site), ('@' + via) if via else '')
                                   for k, site, _, via in rows)))
        write_tsv(os.path.join(out, 'strings.tsv'),
                  ['addr', 'text', 'source', 'nrefs', 'functions', 'refs'], srows)
        write_tsv(os.path.join(out, 'mmio.tsv'),
                  ['func', 'func_name', 'site', 'addr', 'label', 'access', 'insn'],
                  [(hx(fe), n, hx(s) if s is not None else '', hx(a), lab, acc, text)
                   for fe, n, s, a, lab, acc, text in mmio_rows])
        if cfg['rtti']:
            write_tsv(os.path.join(out, 'vtables.tsv'),
                      ['vtable', 'start', 'class', 'typeinfo', 'offset_to_top', 'slot', 'target',
                       'target_name', 'owner'],
                      [(hx(a), hx(b), c, hx(d), e_, i, hx(s), n, o)
                       for a, b, c, d, e_, i, s, n, o in vt_rows])
        # symbols
        sym_rows = []
        si = st.getAllSymbols(True)
        while si.hasNext():
            s = si.next()
            a = s.getAddress()
            if a.isMemoryAddress() and not str(s.getName()).startswith(('LAB_', 'DAT_', 'switchD')):
                sym_rows.append((int(a.getOffset()), str(s.getName(True)), str(s.getSymbolType()),
                                 str(s.getSource()), int(bool(s.isPrimary()))))
        write_tsv(os.path.join(out, 'symbols.tsv'), ['addr', 'name', 'type', 'source', 'primary'],
                  [(hx(a), n, ty, so, pr) for a, n, ty, so, pr in sym_rows])

        # sqlite
        dbp = os.path.join(out, 'xrefs.sqlite')
        if os.path.exists(dbp + '.tmp'):
            os.remove(dbp + '.tmp')
        db = sqlite3.connect(dbp + '.tmp')
        db.executescript(SQL_TABLES)
        db.executemany('INSERT INTO functions VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                       [(int(r[0], 16),) + tuple(r[1:]) for r in frows])
        db.executemany('INSERT INTO function_ranges VALUES (?,?,?)',
                       [(e, lo, hi) for e, f in funcs.items() for lo, hi in f['ranges']])
        db.executemany('INSERT INTO calls VALUES (?,?,?,?,?)', calls)
        db.executemany('INSERT INTO data_refs VALUES (?,?,?,?,?,?)', drefs)
        db.executemany('INSERT INTO strings VALUES (?,?,?)', [(a, t_, s_) for a, (t_, s_) in strings.items()])
        db.executemany('INSERT INTO string_refs VALUES (?,?,?,?,?)',
                       [(s, k, site, fe, via) for s, rows in srefs.items() for k, site, fe, via in rows])
        db.executemany('INSERT INTO mmio VALUES (?,?,?,?,?,?)',
                       [(fe, s, a, lab, acc, text) for fe, _, s, a, lab, acc, text in mmio_rows])
        db.executemany('INSERT INTO vtables VALUES (?,?,?,?,?,?,?,?,?)', vt_rows)
        db.executemany('INSERT INTO symbols VALUES (?,?,?,?,?)', sym_rows)
        db.commit()
        db.close()
        os.replace(dbp + '.tmp', dbp)

        manifest.update({
            'function_count': len(funcs), 'decompiled': len(entries) - len(errors),
            'decompile_failed': len(errors),
            'decompile_failures': [{'entry': hx(e), 'name': funcs[e]['name'], 'error': errors[e][:300]}
                                   for e in sorted(errors)],
            'call_edges': len(calls), 'call_edges_resolved': sum(1 for c in calls if c[1] is not None),
            'data_refs': len(drefs), 'strings': len(strings),
            'strings_with_refs': sum(1 for r in srefs.values() if r), 'mmio_rows': len(mmio_rows),
            'vtables': len({r[0] for r in vt_rows}), 'vtable_slots': len(vt_rows),
            'symbols': len(sym_rows), 'threads': args.threads, 'timeout': args.timeout,
            'decompile_seconds': round(decomp_seconds, 1), 'total_seconds': round(time.time() - t_all, 1),
            'limit': args.limit, 'complete': not args.limit,
        })
        with open(manifest_path, 'w') as f:
            json.dump(manifest, f, indent=1)
        log('%s: done: %s' % (key, {k: manifest[k] for k in (
            'function_count', 'decompiled', 'decompile_failed', 'call_edges', 'strings', 'mmio_rows',
            'vtables', 'total_seconds')}))
    finally:
        proj.close(p)


# ---------------------------------------------------------------------- check

def read_tsv(path):
    with open(path) as f:
        head = f.readline().rstrip('\n').split('\t')
        for line in f:
            yield dict(zip(head, line.rstrip('\n').split('\t')))


def cmd_check(args):
    ok = True
    lines = []

    def say(line):
        # stderr: with the JVM running (the 'all' command), Python's stdout is lost
        print(line, file=sys.stderr, flush=True)
        lines.append(line)

    def report(cond, msg):
        nonlocal ok
        ok &= bool(cond)
        say('%s  %s' % ('PASS' if cond else 'FAIL', msg))

    for key in args.programs:
        out = os.path.join(OUT_DECOMP, key)
        with open(os.path.join(out, 'manifest.json')) as f:
            man = json.load(f)
        report(man.get('complete'), '%s: manifest complete' % key)
        report(man.get('program_bytes_match_image'), '%s: program bytes match %s' % (key, man['image']))
        funcs = list(read_tsv(os.path.join(out, 'functions.tsv')))
        n_files = len(os.listdir(os.path.join(out, 'decomp')))
        report(n_files == len(funcs), '%s: %d decomp files for %d functions' % (key, n_files, len(funcs)))
    if 'main_os' in args.programs:
        out = os.path.join(OUT_DECOMP, 'main_os')
        rows = [r for r in read_tsv(os.path.join(out, 'strings.tsv')) if r['text'] == 'N/A FOR MIDI TRACKS']
        report(len(rows) == 1, 'main_os: "N/A FOR MIDI TRACKS" is one string (%s)' %
               ', '.join(r['addr'] for r in rows))
        for r in rows:
            report(r['functions'], 'main_os: it resolves to %s via %s' % (r['functions'], r['refs']))
            for fe in r['functions'].split():
                stem = [x for x in os.listdir(os.path.join(out, 'decomp')) if x.startswith(fe.split(':')[0][2:])]
                if stem:
                    with open(os.path.join(out, 'decomp', stem[0])) as f:
                        body = f.read()
                    head, _, code = body.partition('\n\n')
                    report(r['addr'][2:] in head and r['addr'][2:] in code,
                           'main_os: %s names the string in its header and its C' % stem[0])
        vt = [r for r in read_tsv(os.path.join(out, 'vtables.tsv')) if r['class'] in ('ArpSetupMenuView', 'SMVoiceStates')]
        for cls in ('ArpSetupMenuView', 'SMVoiceStates'):
            n = sum(1 for r in vt if r['class'] == cls)
            report(n > 0, 'main_os: vtable slots for %s: %d' % (cls, n))
        jsr_bad = [r for r in read_tsv(os.path.join(out, 'calls.tsv')) if r['callee'] == '']
        say('info  main_os: %d call edges without a callee function (computed or unresolved)' % len(jsr_bad))
    with open(os.path.join(OUT_DECOMP, 'check.txt'), 'w') as f:
        f.write('\n'.join(lines + ['RESULT ' + ('PASS' if ok else 'FAIL')]) + '\n')
    sys.exit(0 if ok else 1)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('cmd', choices=('setup', 'prepare', 'dump', 'check', 'all'))
    ap.add_argument('--programs', nargs='+', default=list(PROGRAMS), choices=list(PROGRAMS))
    ap.add_argument('--threads', type=int, default=6)
    ap.add_argument('--timeout', type=int, default=60, help='decompiler seconds per function')
    ap.add_argument('--retry-timeout', type=int, default=300, help='second pass for timeouts')
    ap.add_argument('--heap', default='6g')
    ap.add_argument('--limit', type=int, help='decompile only the first N functions')
    ap.add_argument('--force', action='store_true', help='prepare: redo steps already done')
    args = ap.parse_args(argv)
    if args.cmd in ('setup', 'all'):
        cmd_setup(args)
    if args.cmd in ('prepare', 'all'):
        cmd_prepare(args)
    if args.cmd in ('dump', 'all'):
        cmd_dump(args)
    if args.cmd in ('check', 'all'):
        cmd_check(args)


if __name__ == '__main__':
    main()
