#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""SC01: the function map (SW ranges, every function incl. static ones) of a built recipe section (the layout study,
notes/SC01.md 1; verify_sc01.py uses it to find cmp_mtr in code4).

funcmap(outdir, base_byte, [s files]) -> [(name, file, sw_lo, sw_hi)]
The .s files are re-assembled with every function label made .GLOBAL and linked alone at base_byte in one
SW section, in the given order (the recipe's order), so the addresses equal the real build's.
"""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools/sharcvm"))
import elf as E  # noqa: E402

SEL = ROOT / "vendor/selache/target/release"
LDF = """ARCHITECTURE(ADSP-21569)
$OBJECTS = $COMMAND_LINE_OBJECTS;
MEMORY
{
   mem_c { TYPE(BW RAM) START(0x%08x) END(0x%08x) WIDTH(8) }
   mem_d { TYPE(BW RAM) START(0x002e6000) END(0x002e67ff) WIDTH(8) }
}
PROCESSOR p0
{
   OUTPUT($COMMAND_LINE_OUTPUT_FILE)
   ENTRY(%s.)
   SECTIONS
   {
      c SW
      {
         INPUT_SECTION_ALIGN(2)
         FILL(0x1)
         INPUT_SECTIONS( $OBJECTS(seg_swco seg_pmco seg_t38c2 seg_t38c3 seg_t38c4) )
      } > mem_c
      d BW
      {
         INPUT_SECTION_ALIGN(4)
         INPUT_SECTIONS( $OBJECTS(seg_dmda) )
      } > mem_d
      b NO_INIT BW
      {
         INPUT_SECTION_ALIGN(4)
         INPUT_SECTIONS( $OBJECTS(seg_l1_bss seg_bss) )
      } > mem_d
   }
}
"""
LABEL = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\.:\s*$")


def funcmap(out, base, files, allow_unresolved=True):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    names, objs, first = [], [], None
    for p in files:
        p = Path(p)
        new = []
        for ln in p.read_text().split("\n"):
            m = LABEL.match(ln)
            if m:
                names.append((m.group(1), p.name))
                first = first or m.group(1)
                new.append(f".GLOBAL {m.group(1)}.;")
            new.append(ln)
        q = out / p.name
        q.write_text("\n".join(new).replace(".GLOBAL %s.;\n.GLOBAL %s.;" % (first, first), ".GLOBAL %s.;" % first))
        doj = out / (p.stem + ".doj")
        subprocess.run([SEL / "selas", "-proc", "ADSP-21569", "-o", doj, q], check=True)
        objs.append(doj)
    # stand-ins for symbols defined elsewhere (calls out of the group): a stub object
    (out / "f.ldf").write_text(LDF % (base, base + 0x80000 - 1, first))
    r = subprocess.run([SEL / "seld", "-proc", "ADSP-21569", "-T", out / "f.ldf", "-o", out / "f.dxe", *objs],
                       capture_output=True, text=True)
    if r.returncode:
        missing = sorted(set(re.findall(r"unresolved symbol: `?([A-Za-z_0-9]+)\.", r.stdout + r.stderr)))
        if not missing or not allow_unresolved:
            raise SystemExit(r.stdout + r.stderr)
        ext = out / "ext.s"
        ext.write_text(".SECTION/DOUBLE32 seg_dmda;\n" + "".join(f".GLOBAL {m}.;\n{m}.:\n.VAR = 0;\n" for m in missing))
        subprocess.run([SEL / "selas", "-proc", "ADSP-21569", "-o", out / "ext.doj", ext], check=True)
        subprocess.run([SEL / "seld", "-proc", "ADSP-21569", "-T", out / "f.ldf", "-o", out / "f.dxe", *objs,
                        out / "ext.doj"], check=True)
    e = E.read_elf(out / "f.dxe")
    sec = [s for s in e.sections if s.kind == "sw_code"][0]
    end = sec.addr + len(sec.data) // 2
    addr = sorted((e.symbols[n + "."], n, f) for n, f in names if n + "." in e.symbols)
    rows = []
    for i, (a, n, f) in enumerate(addr):
        rows.append((n, f, a, addr[i + 1][0] if i + 1 < len(addr) else end))
    return rows, sec


if __name__ == "__main__":
    rows, sec = funcmap(sys.argv[1], int(sys.argv[2], 16), sys.argv[3:])
    for n, f, a, b in rows:
        print("%-26s %-12s %#x-%#x %6d" % (n, f, a, b, 2 * (b - a)))
