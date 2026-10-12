"""Exact-PC decoding with digikit's table-driven decoder (vendor/digikit/tools).

decode_at(read, pc) -> Decoded. `read(byte_addr, n)` returns bytes or None.
Corrections applied on top of digikit, each counted in OVERRIDES so they stay visible:
  * 2c: a first parcel with bits 15:13 = 110 is always the 16-bit short compute
    (selache visa.rs); digikit's exact-PC decoder sometimes returns a 32-bit 2b.
  * IDLE16: the 16-bit IDLE parcel 0x0081 (selache visa.rs, PGR Type 22) is read by
    digikit as the 48-bit provisional form 22p_undoc48. 0x00C1 (EMUIDLE) likewise.
"""
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

_TOOLS = Path(__file__).resolve().parents[2] / "vendor/digikit/tools"
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))
sys.dont_write_bytecode = True
import sharc_disasm  # noqa: E402

OVERRIDES = Counter()


@dataclass
class Decoded:
    form: str
    parcels: int
    fields: dict
    raw: int
    kind: str      # confident | uncertain | unknown
    note: str = ""


def sw_to_byte(sw):
    if 0xb80000 <= sw < 0xc00000:
        return 0x20000000 + 2 * (sw - 0xb80000)
    return 2 * sw


def decode_at(read, pc):
    b = sw_to_byte(pc)
    first = read(b, 2)
    if first is None:
        return Decoded("unknown", 0, {}, 0, "unknown", f"PC {pc:#x} not in memory")
    p0 = first[0] | (first[1] << 8)
    if p0 == 0x0081:
        OVERRIDES["IDLE16 (digikit: 22p_undoc48)"] += 1
        return Decoded("IDLE16", 1, {}, p0, "confident", "override")
    if p0 == 0x00C1:
        OVERRIDES["EMUIDLE16"] += 1
        return Decoded("EMUIDLE16", 1, {}, p0, "confident", "override")
    sizes = (6, 4, 2)
    if p0 >> 13 == 0b110:
        # Type 2c short compute: always 16-bit (selache visa.rs is_16bit). digikit's
        # exact-PC decoder reads 0xC000-0xC07F followed by more parcels as a 32-bit
        # "2b" and refuses a 2-byte window, e.g. selcc's `r4=r4+r1` (0xc041); sharcdb's
        # context sweep decodes these DN2 parcels as 2c. Field: compute[11:0] = p0 & 0xfff.
        w6 = read(b, 6) or first
        alt = next(sharc_disasm.disassemble(w6, count=1))
        if alt.type_name == "2c" and alt.length_bytes == 2:
            return Decoded("2c", 1, dict(alt.fields), p0, alt.kind, "")
        OVERRIDES[f"2c forced (digikit: {alt.type_name})"] += 1
        return Decoded("2c", 1, {"compute[11:0]": p0 & 0xfff}, p0, "confident", "override 2c")
    for size in sizes:
        w = read(b, size)
        if w is None:
            continue
        ins = next(sharc_disasm.disassemble(w, count=1))
        if ins.kind == "unknown":
            return Decoded("unknown", 0, {}, p0, "unknown", ins.note)
        return Decoded(ins.type_name, ins.length_bytes // 2, dict(ins.fields), ins.raw or 0, ins.kind, ins.note)
    return Decoded("unknown", 0, {}, p0, "unknown", "no window")
