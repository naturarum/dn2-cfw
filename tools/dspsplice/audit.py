#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
# The idea of auditing our encodings against stock comes from dn2_firmware_explore's
# scripts/sharc_encoding_audit.py (angellinares); this is our own implementation.
"""Encoding audit of our spliced SHARC code against the stock image (T3.8).

    python3 tools/dspsplice/audit.py out/T3.8/CG01

Why: an emulator gate is not enough. dnfw found on silicon that selas encodes a shift by a
negative immediate in a form every decoder and emulator accepted but the ADSP-21569 rejects
(ILOPI, the core parks): "decodes the same" is not proof of an encoding (notes/prior-art-2026-10
6.1 fact 1). The rule taken from that (a fact, no code copied): compare our instructions with
the encodings the stock firmware itself uses.

For every instruction of our code (decoded at its exact address with digikit's table decoder,
the same one tools/sharcvm uses) this assigns one level:
  exact   the raw bits with only the address / immediate / branch-offset fields masked
          (digikit's own field layout, sharcdb.mask_relocatable's stems) equal those of at least
          one stock instruction of the same form: the same opcode, registers and option bits;
  shape   not exact, but the stock image has the same form and mnemonic with only the data
          registers (R/F/S n) different (e.g. `F7 = F5 * F6` vs stock `F2 = F1 * F4`);
  UNSEEN  neither: an opcode the stock firmware never uses. Must be looked at by hand.
Instructions that carry an immediate (Type 6 shifts, 14a/15a addresses, 16a/17a/17b/18a/19a data,
25a/8a targets) must be exact; a "shape" there is reported as a finding. Type 6 shift
immediates are also checked for the sign-extension rule (bc_dsp.bad_shift).
"""
import argparse
import collections
import json
import re
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DB = ROOT / "vendor/digikit/out/sharcdb/dn2-1.12.sqlite"
sys.dont_write_bytecode = True
for p in (ROOT / "vendor/digikit/tools", ROOT / "tools/sharcvm", ROOT / "tools/brickcheck"):
    sys.path.insert(0, str(p))
import sharc_disasm  # noqa: E402
import sharc_visa_tables as VT  # noqa: E402
import sharcfn  # noqa: E402
import elf as E  # noqa: E402

MASK_STEMS = ("addr", "reladdr", "data")


class _NoMem:
    """sharcfn.render_instruction annotates literals by reading memory; nothing to read here"""
    def read(self, addr, n):
        return None       # sharcdb.mask_relocatable's stems
IMM_FORMS = ("6", "14a", "15a", "16a", "17a", "17b", "18a", "19a", "25a", "8a")


def masked(type_name, raw):
    entry = VT.get_type(type_name)
    if entry is None or raw is None:
        return raw
    for label, (hi, lo) in entry["fields"].items():
        if label.split("[")[0] in MASK_STEMS:
            raw &= ~(((1 << (hi - lo + 1)) - 1) << lo)
    return raw


def shape(mnemonic):
    m = re.sub(r"0x[0-9a-fA-F]+|-?\b\d+\b(?![\]:])", "#", mnemonic or "")
    m = re.sub(r"\b([RFS])\d+\b", r"\1n", m)
    return re.sub(r"\s+", " ", m).strip()


def stock_sets():
    db = sqlite3.connect(DB)
    exact, shapes, count = set(), set(), 0
    for raw, form, mn in db.execute("SELECT raw, form, mnemonic FROM insn WHERE image='dn2-1.12' AND aligned=1"):
        if raw is None:
            continue
        count += 1
        exact.add((form, masked(form, int(raw, 16))))
        shapes.add((form, shape(mn)))
    return exact, shapes, count


CORPUS = ROOT / "out/T3.6/cases"            # selcc builds of selache's 2,039 xtest cases (T3.6)
CORPUS_CACHE = ROOT / "out/T3.8/selcc_corpus.json"


def corpus_sets():
    """(form, masked raw) and (form, shape) of every instruction in the selcc builds of selache's
    test corpus, which selache's author ran on an ADSP-21569 board with selcc before promoting
    each case (vendor/selache/xtest/grow_drafts.py). Only cases that also pass in our interpreter
    (out/T3.6/cases/results.tsv) are used. Cached."""
    if CORPUS_CACHE.exists():
        d = json.loads(CORPUS_CACHE.read_text())
        return {tuple(x) for x in d["exact"]}, {tuple(x) for x in d["shapes"]}, d["count"], d["cases"]
    ok = set()
    res = ROOT / "out/T3.8/selcc_cases_results.tsv"     # tools/sharcvm/run_cases.py --out ...
    for line in res.read_text().split("\n")[1:]:
        f = line.split("\t")
        if len(f) > 1 and f[1] == "PASS":
            ok.add(f[0])
    exact, shapes, count = set(), set(), 0
    for dxe in sorted(CORPUS.glob("*.dxe")):
        if dxe.stem not in ok:
            continue
        e = E.read_elf(dxe)
        for s in e.sections:
            if s.kind != "sw_code":
                continue
            for pc, ins in decode_section(s):
                if ins.kind == "unknown":
                    continue
                count += 1
                mn, _, _ = sharcfn.render_instruction(pc, ins, _NoMem(), set(), collections.Counter(), [])
                exact.add((ins.type_name, masked(ins.type_name, ins.raw)))
                shapes.add((ins.type_name, shape(mn)))
    CORPUS_CACHE.parent.mkdir(parents=True, exist_ok=True)
    CORPUS_CACHE.write_text(json.dumps({"count": count, "cases": len(ok), "exact": sorted(exact),
                                        "shapes": sorted(shapes)}))
    return exact, shapes, count, len(ok)


def our_code(odir):
    info = json.loads((odir / "build.json").read_text())
    e = E.read_elf(next((odir / "build").glob("*.dxe")))
    for s in e.sections:
        if s.kind == "sw_code":
            yield s, info


def decode_section(s):
    base = E.sw_to_byte(s.addr)

    def rd(a, n):
        o = a - base
        return s.data[o:o + n] if 0 <= o and o + n <= len(s.data) else None
    sys.path.insert(0, str(ROOT / "tools/sharcvm"))
    import decode as D
    pc = s.addr
    while pc < s.addr + len(s.data) // 2:
        d = D.decode_at(rd, pc)
        n = max(1, d.parcels)
        w = rd(E.sw_to_byte(pc), 2 * n)
        ins = next(sharc_disasm.disassemble(w, count=1))
        if d.form != ins.type_name:      # decode.py's documented overrides (2c, IDLE16)
            ins = type(ins)(offset=0, length_bytes=2 * n, type_name=d.form, fields=d.fields, raw=d.raw,
                            kind=d.kind, note=d.note)
        yield pc, ins
        pc += n


def audit(odir):
    import bc_dsp
    exact, shapes, nstock = stock_sets()
    cexact, cshapes, ncorpus, ncases = corpus_sets()
    rows, levels, findings = [], collections.Counter(), []
    labels = {}
    info = json.loads((odir / "build.json").read_text())
    for name, v in info["symbols"].items():
        labels[int(v, 16)] = name
    func = "?"
    for s, _ in our_code(odir):
        for pc, ins in decode_section(s):
            func = labels.get(pc, func)
            mn, _, _ = sharcfn.render_instruction(pc, ins, _NoMem(), set(), collections.Counter(), [])
            if ins.kind == "unknown":
                lvl = "UNSEEN"
            elif (ins.type_name, masked(ins.type_name, ins.raw)) in exact:
                lvl = "exact"
            elif (ins.type_name, masked(ins.type_name, ins.raw)) in cexact:
                lvl = "exact-selcc"
            elif (ins.type_name, shape(mn)) in shapes:
                lvl = "shape"
            elif (ins.type_name, shape(mn)) in cshapes:
                lvl = "shape-selcc"
            else:
                lvl = "UNSEEN"
            note = ""
            if ins.type_name.startswith("6") and "shiftimm" in "".join(ins.fields):
                pass
            f = ins.fields
            if "shiftimm[15:0]" in f or any(k.startswith("shiftimm") for k in f):
                si = next(v for k, v in f.items() if k.startswith("shiftimm"))
                de = next((v for k, v in f.items() if k.startswith("dataex")), 0)
                if bc_dsp.bad_shift(si, de):
                    note = "BAD SHIFT ENCODING"
                    findings.append((pc, mn, note))
            if not lvl.startswith("exact") and ins.type_name.startswith(IMM_FORMS):
                note = (note + "; " if note else "") + "immediate form not byte-exact with stock"
                findings.append((pc, mn, note))
            if lvl == "UNSEEN":
                findings.append((pc, mn, "opcode the stock image never uses"))
            levels[lvl] += 1
            rows.append({"sw": "%#x" % pc, "func": func, "form": ins.type_name, "raw": "%x" % (ins.raw or 0),
                         "mnemonic": mn, "level": lvl, "note": note})
    rep = {"stock_instructions": nstock, "selcc_corpus_instructions": ncorpus, "selcc_corpus_cases": ncases,
           "ours": len(rows), "levels": dict(levels),
           "findings": [{"sw": "%#x" % a, "mnemonic": m, "why": w} for a, m, w in findings]}
    (odir / "audit.json").write_text(json.dumps({"summary": rep, "rows": rows}, indent=1))
    return rep, rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("odir")
    a = ap.parse_args()
    rep, rows = audit(Path(a.odir))
    print(f"stock aligned instructions: {rep['stock_instructions']}; selcc corpus: {rep['selcc_corpus_instructions']} "
          f"in {rep['selcc_corpus_cases']} cases; ours: {rep['ours']}; levels: {rep['levels']}")
    forms = collections.Counter((r["form"], r["level"]) for r in rows)
    for (f, l), n in sorted(forms.items()):
        print(f"  {f:12s} {l:7s} {n}")
    if rep["findings"]:
        print("FINDINGS:")
        for x in rep["findings"]:
            print(f"  {x['sw']} {x['mnemonic']}  -- {x['why']}")
    else:
        print("no findings")
    return 1 if rep["findings"] else 0


if __name__ == "__main__":
    sys.exit(main())
