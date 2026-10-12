#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""T3.4 helper: print digikit sharcdb listing for SW ranges of dn2-1.12.
usage: t34_lst.py START [END|+N] ...   (hex SW addresses)"""
import sqlite3, sys
import os
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # the project folder
DB = os.path.join(ROOT, "vendor/digikit/out/sharcdb/dn2-1.12.sqlite")
db = sqlite3.connect(DB)
args = sys.argv[1:]
i = 0
while i < len(args):
    s = int(args[i], 16); e = None
    if i + 1 < len(args) and (args[i+1].startswith('+') or args[i+1].startswith('0x') and int(args[i+1],16) > s):
        a = args[i+1]
        e = s + int(a[1:]) * 3 if a.startswith('+') else int(a, 16)
        i += 2
    else:
        e = s + 60; i += 1
    rows = db.execute("SELECT sw, raw, mnemonic, function_sw, aligned FROM insn WHERE image='dn2-1.12' AND sw>=? AND sw<? AND aligned=1 ORDER BY sw", (s, e)).fetchall()
    for sw, raw, m, f, al in rows:
        print("%06x %-14s %s" % (sw, raw, m))
    print("----")
