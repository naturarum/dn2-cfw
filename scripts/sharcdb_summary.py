#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Summarise one digikit SHARC database (vendor/digikit/tools/sharcdb.py).

Reads out/sharcdb/<image>.sqlite through digikit's tools/sharc.py and prints:
code blocks, functions, edges, roots (loader entry, RTOS tasks, interrupt
vectors, pointer tables), the call graph, reach from several root sets, and
optional probes of jump tables. Nothing is written into vendor/digikit except
the database that sharc.load() builds when it is missing or stale.

Run it with digikit's environment, from anywhere:

    uv run --project vendor/digikit python scripts/sharcdb_summary.py dn2-1.12 \
        --digikit vendor/digikit --json out/T3.1b/summary.json \
        --table 0x268a68:4

The RTOS task finder here is wider than digikit's own `rtos_task` detector:
it accepts any call whose R8 is a literal pointing at a NUL-terminated ASCII
string and whose R4 is a literal inside a decoded function, whatever R12
holds. digikit's detector also requires a literal R12, which misses tasks
created with a stack size loaded from memory (FreeRTOS's IDLE and timer
tasks on DN2 1.12).
"""

from __future__ import annotations

import argparse
import bisect
import collections
import json
import os
import sys


def _import_digikit(path):
    path = os.path.abspath(path)
    tools = os.path.join(path, "tools")
    if tools not in sys.path:
        sys.path.insert(0, tools)
    os.chdir(path)  # sharc.load() uses paths relative to the digikit root
    import networkx  # noqa: F401  (fails early outside digikit's env)
    import sharc
    import sharcdb
    import sharcldr
    return sharc, sharcdb, sharcldr


def region_of(sw):
    if 0x120000 <= sw < 0x130000:
        return "L1b0 init program (app 0)"
    if 0x1C0000 <= sw < 0x1D0000:
        return "L1b3 main program"
    if 0xB80000 <= sw < 0xC00000:
        return "L2 main program"
    return "other"


def read_cstring(mem, byte_addr, limit=48):
    raw = mem.read(byte_addr, limit)
    if raw is None:
        return None
    end = raw.find(b"\0")
    if end <= 0:
        return None
    s = raw[:end]
    if all(0x20 <= c < 0x7F for c in s):
        return s.decode()
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("image", help="digikit image name, e.g. dn2-1.12")
    ap.add_argument("--digikit", default=os.path.join(os.path.dirname(__file__), "..", "vendor", "digikit"))
    ap.add_argument("--json", help="also write the summary as JSON here")
    ap.add_argument("--table", action="append", default=[],
                    help="BARE_DM_ADDR:N -- read N 32-bit words there and map them to functions")
    ap.add_argument("--window", type=int, default=20, help="literal-to-call window in short words")
    args = ap.parse_args()
    json_out = os.path.abspath(args.json) if args.json else None

    sharc, sharcdb, sharcldr = _import_digikit(args.digikit)
    img = sharc.load(args.image)
    name = img.name
    q = img.sql
    out = collections.OrderedDict()

    out["meta"] = {k: img.meta.get(k) for k in ("image_sha256", "db_version", "tool_commit", "blocks")}

    funcs = q("SELECT entry_sw, end_sw, n_insns, block, has_static_caller, is_leaf, entry_kind "
              "FROM functions WHERE image=? ORDER BY entry_sw", name)
    fn_insns = {f[0]: f[2] for f in funcs}
    starts = [f[0] for f in funcs]
    ends = [f[1] for f in funcs]

    def owner(sw):
        i = bisect.bisect_right(starts, sw) - 1
        if i >= 0 and sw < ends[i]:
            return starts[i]
        return None

    # --- blocks ----------------------------------------------------------------
    blocks = []
    for idx, tgt, n, base in q("SELECT idx, target_address, byte_count, base_sw FROM blocks "
                               "WHERE image=? AND kind='code' ORDER BY idx", name):
        aligned = q("SELECT count(*) FROM insn WHERE image=? AND block=? AND aligned=1", name, idx)[0][0]
        nf, ni = q("SELECT count(*), coalesce(sum(n_insns),0) FROM functions WHERE image=? AND block=?", name, idx)[0]
        blocks.append({"idx": idx, "load": hex(tgt), "bytes": n, "base_sw": hex(base) if base is not None else None,
                       "region": region_of(base) if base is not None else "?",
                       "aligned_insns": aligned, "functions": nf, "insns_in_functions": ni})
    out["code_blocks"] = blocks

    # --- functions ---------------------------------------------------------------
    by_region = collections.Counter()
    insns_by_region = collections.Counter()
    for entry, _end, n, _blk, _hsc, _leaf, _kind in funcs:
        by_region[region_of(entry)] += 1
        insns_by_region[region_of(entry)] += n
    out["functions"] = {
        "total": len(funcs),
        "insns_in_functions": sum(fn_insns.values()),
        "by_region": dict(by_region),
        "insns_by_region": dict(insns_by_region),
        "leaf": sum(1 for f in funcs if f[5]),
        "with_static_caller": sum(1 for f in funcs if f[4]),
        "entry_kind": dict(collections.Counter(f[6] for f in funcs)),
    }
    out["edges"] = dict(q("SELECT kind, count(*) FROM edges WHERE image=? GROUP BY kind", name))

    # --- roots -------------------------------------------------------------------
    out["roots_by_kind"] = dict(q("SELECT kind, count(*) FROM roots WHERE image=? GROUP BY kind", name))
    detail = []
    for sw, kind, note in q("SELECT sw, kind, note FROM roots WHERE image=? AND kind IN "
                            "('loader_entry','rtos_task','interrupt_vector') ORDER BY kind, sw", name):
        o = owner(sw)
        detail.append({"sw": hex(sw), "kind": kind, "note": note,
                       "owner": hex(o) if o is not None else None})
    out["roots_detail"] = detail
    tables = collections.defaultdict(list)
    for sw, note in q("SELECT sw, note FROM roots WHERE image=? AND kind='code_pointer_array'", name):
        t, i = note.split()
        tables[t.split("=")[1]].append((int(i.split("=")[1]), hex(sw)))
    out["code_pointer_tables"] = {t: [v for _i, v in sorted(e)] for t, e in sorted(tables.items())}

    # --- RTOS tasks (wider finder, see module docstring) ---------------------------
    mem = img._mem() if hasattr(img, "_mem") else None
    if mem is None:
        blob = open(img.blob_path, "rb").read()
        mem = sharcldr.LoadedMemory.from_stream(blob, sharcldr.parse_blocks(blob))
    lits = collections.defaultdict(list)
    for sw, value, reg in q("SELECT sw, value, dest_reg FROM literals WHERE image=? AND dest_reg IN "
                            "('R4','R8','R12') ORDER BY sw", name):
        lits[reg].append((sw, value & 0xFFFFFFFF))
    lit_sw = {r: [s for s, _v in v] for r, v in lits.items()}

    def last_lit(reg, sw):
        i = bisect.bisect_left(lit_sw.get(reg, []), sw)
        if i == 0:
            return None
        s, v = lits[reg][i - 1]
        return v if sw - s <= args.window else None

    tasks = []
    for site, helper in q("SELECT from_sw, to_function FROM edges WHERE image=? AND kind='call' "
                          "AND to_function IS NOT NULL ORDER BY from_sw", name):
        r8, r4 = last_lit("R8", site), last_lit("R4", site)
        if r8 is None or r4 is None or owner(r4) is None:
            continue
        s = read_cstring(mem, r8 + sharcldr.SW_ALIAS_BASE)
        if not s or len(s) < 3:
            continue
        r12 = last_lit("R12", site)
        tasks.append({"name": s, "entry": hex(r4), "entry_owner": hex(owner(r4)), "helper": hex(helper),
                      "call_site": hex(site), "name_ptr": hex(r8),
                      "r12_literal": r12, "digikit_rtos_root": bool(q(
                          "SELECT 1 FROM roots WHERE image=? AND kind='rtos_task' AND sw=?", name, r4))})
    out["tasks"] = tasks

    # --- call graph ----------------------------------------------------------------
    cg = q("SELECT count(*), max(max_depth), sum(is_recursive), sum(n_callers=0) FROM callgraph WHERE image=?", name)[0]
    call_pairs = q("SELECT count(*) FROM (SELECT DISTINCT from_function, to_function FROM edges WHERE image=? "
                   "AND kind='call' AND from_function IS NOT NULL AND to_function IS NOT NULL)", name)[0][0]
    cj_pairs = q("SELECT count(*) FROM (SELECT DISTINCT from_function, to_function FROM edges WHERE image=? "
                 "AND kind IN ('call','jump','cond_jump') AND from_function IS NOT NULL AND to_function IS NOT NULL "
                 "AND from_function != to_function)", name)[0][0]
    top_callers = q("SELECT printf('0x%x',function_sw), n_callers FROM callgraph WHERE image=? "
                    "ORDER BY n_callers DESC LIMIT 10", name)
    top_trans = q("SELECT printf('0x%x',function_sw), n_transitive_callees, max_depth FROM callgraph WHERE image=? "
                  "ORDER BY n_transitive_callees DESC LIMIT 10", name)
    out["callgraph"] = {"nodes": cg[0], "distinct_call_pairs": call_pairs,
                        "distinct_call_or_jump_pairs": cj_pairs, "max_call_depth": cg[1],
                        "recursive": cg[2], "no_call_caller": cg[3],
                        "top_by_callers": top_callers, "top_by_transitive_callees": top_trans}
    out["unentered"] = {"functions": q("SELECT count(*) FROM unentered WHERE image=?", name)[0][0],
                        "clusters": q("SELECT count(DISTINCT cluster) FROM unentered WHERE image=?", name)[0][0]}
    out["loops"] = q("SELECT count(*) FROM loops WHERE image=?", name)[0][0]

    # --- reach -----------------------------------------------------------------------
    import networkx as nx
    G = sharcdb._function_call_jump_graph(img.db, name, starts)

    def seed(sw):
        o = owner(sw)
        if o is not None:
            return o
        i = bisect.bisect_right(starts, sw)  # digikit's loader_entry fallback
        return starts[i] if i < len(starts) else None

    def reach(sws):
        seen = set()
        for sw in sws:
            s = seed(sw)
            if s is not None:
                seen |= set(nx.descendants(G, s)) | {s}
        return seen

    roots = collections.defaultdict(set)
    for sw, kind in q("SELECT sw, kind FROM roots WHERE image=?", name):
        roots[kind].add(sw)
    task_entries = {int(t["entry"], 16) for t in tasks}
    main_fns = {f for f in starts if region_of(f) != "L1b0 init program (app 0)"}
    main_insns = sum(fn_insns[f] for f in main_fns)
    total_insns = sum(fn_insns.values())
    sets = collections.OrderedDict()
    sets["loader_entry"] = roots["loader_entry"]
    sets["interrupt_vectors"] = roots["interrupt_vector"]
    sets["tasks (wider finder)"] = task_entries
    sets["structural: loader + IVT + tasks"] = roots["loader_entry"] | roots["interrupt_vector"] | task_entries
    sets["structural + code_pointer_array"] = sets["structural: loader + IVT + tasks"] | roots["code_pointer_array"]
    sets["all roots except no_static_entry"] = set().union(*(v for k, v in roots.items() if k != "no_static_entry")) | task_entries
    sets["all roots"] = set().union(*roots.values()) | task_entries
    for t in tasks:
        sets["task %s" % t["name"]] = {int(t["entry"], 16)}
    reach_rows = []
    for label, sws in sets.items():
        r = reach(sws)
        n_i = sum(fn_insns[f] for f in r)
        reach_rows.append({"roots": label, "n_roots": len(sws), "functions": len(r),
                           "insns": n_i, "pct_functions_all": round(100 * len(r) / len(starts), 1),
                           "pct_insns_all": round(100 * n_i / total_insns, 1),
                           "main_functions": len(r & main_fns),
                           "main_insns": sum(fn_insns[f] for f in r & main_fns),
                           "pct_main_insns": round(100 * sum(fn_insns[f] for f in r & main_fns) / main_insns, 1)})
    out["reach"] = reach_rows
    for t in tasks:
        e = int(t["entry"], 16)
        t["reach_functions_digikit_table"] = q(
            "SELECT count(*) FROM reach WHERE image=? AND root_sw=?", name, e)[0][0] or None

    # --- probes -------------------------------------------------------------------------
    probes = []
    for spec in args.table:
        addr_s, n_s = spec.split(":")
        addr, n = int(addr_s, 0), int(n_s, 0)
        words = []
        for i in range(n):
            raw = mem.read(addr + sharcldr.SW_ALIAS_BASE + 4 * i, 4)
            w = int.from_bytes(raw, "little") if raw else None
            words.append({"word": hex(w) if w is not None else None,
                          "owner": hex(owner(w)) if w is not None and owner(w) is not None else None})
        probes.append({"table": hex(addr), "n": n, "entries": words})
    out["table_probes"] = probes

    text = json.dumps(out, indent=1, default=str)
    print(text)
    if json_out:
        os.makedirs(os.path.dirname(json_out), exist_ok=True)
        with open(json_out, "w") as f:
            f.write(text + "\n")


if __name__ == "__main__":
    main()
