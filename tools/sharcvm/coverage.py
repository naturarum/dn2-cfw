#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Static translation coverage of the DN2 1.12 DSP program.

Decodes (decode.py, digikit) and translates (translate.py) every aligned instruction that
digikit's sharcdb lists in the main program's code blocks (L1 block 3 and L2 code,
excluding block 57, which is data), and reports what the interpreter cannot express.
Optionally restricted to the functions reachable from a root (sharcdb `reach` table).

    python3 tools/sharcvm/coverage.py [--root 0x1c9fe7]
Writes out/T3.6/coverage/static.tsv and prints a summary.
"""
import argparse
import sqlite3
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
import decode as D      # noqa: E402
import translate as T   # noqa: E402
from vm import VM       # noqa: E402

DB = ROOT / "vendor/digikit/out/sharcdb/dn2-1.12.sqlite"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=lambda x: int(x, 0))
    a = ap.parse_args()
    db = sqlite3.connect(DB)
    q = "select sw, form, function_sw from insn where image='dn2-1.12' and aligned=1 and " \
        "((sw >= 0x1c0000 and sw < 0x1d0000) or (sw >= 0xb80000 and sw < 0xb8f444))"
    rows = db.execute(q).fetchall()
    funcs = None
    if a.root is not None:
        cols = [r[1] for r in db.execute("pragma table_info(reach)")]
        funcs = {r[0] for r in db.execute(f"select {cols[2]} from reach where {cols[1]}=?", (a.root,))}
        if not funcs:
            sys.exit(f"no reach rows for root {a.root:#x} (columns {cols})")
        rows = [r for r in rows if r[2] in funcs]
    vm = VM()
    vm.load_dn2()
    bad = Counter()
    forms = Counter()
    out = []
    mismatch = 0
    for sw, form, fn in rows:
        d = D.decode_at(vm.read, sw)
        if d.form != form:
            mismatch += 1
        forms[d.form] += 1
        try:
            T.translate(d.form, d.fields, sw, d.parcels, d.raw)
            status = "ok"
        except T.Unimpl as e:
            status = f"unimpl: {d.form}: {e}"
            bad[f"{d.form}: {e}"] += 1
        out.append(f"{sw:#x}\t{d.form}\t{status}")
    (ROOT / "out/T3.6/coverage").mkdir(parents=True, exist_ok=True)
    name = "static.tsv" if a.root is None else f"static_{a.root:#x}.tsv"
    (ROOT / "out/T3.6/coverage" / name).write_text("\n".join(out) + "\n")
    n = len(rows)
    nb = sum(bad.values())
    print(f"{n} instructions{' reachable from %#x' % a.root if a.root else ''}: {n - nb} translate "
          f"({100 * (n - nb) / n:.2f}%), {nb} would trap; decoder form differs from sharcdb on {mismatch}")
    for k, v in bad.most_common(30):
        print(f"  {v:5d}  {k}")
    print("overrides:", dict(D.OVERRIDES))


if __name__ == "__main__":
    main()
