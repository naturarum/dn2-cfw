#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""T3.4: put digikit (sharcdb) and selache (seldump listings from T3.1) side by side
for a list of SW addresses or ranges. usage: t34_xcheck.py OUT ADDR|START-END ..."""
import glob, re, sqlite3, sys
import os
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # the project folder
DB = os.path.join(ROOT, "vendor/digikit/out/sharcdb/dn2-1.12.sqlite")
SEL = {}
for f in glob.glob(os.path.join(ROOT, "out/T3.1/disasm/*.txt")):
    for line in open(f, errors="replace"):
        m = re.match(r"\s*([0-9a-f]{8})\s+([0-9a-f]+)\s+(.*)$", line)
        if m:
            SEL[int(m.group(1), 16)] = (m.group(2), m.group(3).strip())
db = sqlite3.connect(DB)
out = open(sys.argv[1], "w")
same = diff = missing = 0
for a in sys.argv[2:]:
    if "-" in a:
        s, e = (int(x, 16) for x in a.split("-"))
    else:
        s = e = int(a, 16)
    rows = db.execute("SELECT sw, raw, mnemonic FROM insn WHERE image='dn2-1.12' AND aligned=1 AND sw BETWEEN ? AND ? ORDER BY sw", (s, e)).fetchall()
    for sw, raw, m in rows:
        sel = SEL.get(sw)
        if sel is None:
            tag = "NO-SELACHE-START"; missing += 1
        elif sel[0].lstrip("0") == raw.lstrip("0"):
            tag = "same-bytes"; same += 1
        else:
            tag = "DIFFERENT-WIDTH"; diff += 1
        out.write("%06x  %-14s dk: %-55s | sel: %s  [%s]\n" % (sw, raw, m[:55], sel[1] if sel else "-", tag))
out.write("\nsame-bytes %d, different %d, no selache start %d\n" % (same, diff, missing))
print("same-bytes %d, different %d, no selache start %d" % (same, diff, missing))
