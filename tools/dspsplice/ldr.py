# SPDX-License-Identifier: GPL-2.0-or-later
"""Section 7 (the SHARC+ boot stream) as an editable list of blocks (T3.8).

The stream format is the ADSP-2156x boot-ROM block format (notes/T3.1-selache.md section 2):
a 16-byte little-endian header per block {block code, target, byte count, argument}, then the
payload (none for FILL and the markers). Block code = HDRSIGN 0xAD << 24 | HDRCHK << 16 |
flags (bits 4..15) | BCODE (bits 0..3); HDRCHK makes the XOR of the 16 header bytes zero.

What this module does, and nothing else:
  * parse a stream into blocks (scripts/sharc_ldr_parse.py does the checking),
  * patch bytes inside an existing payload block (a stock instruction, e.g. a CALL target),
    refusing FILL blocks and any byte not covered by exactly one payload block of the final
    image (so a later block cannot silently overwrite the patch),
  * insert new load blocks just before the FINAL block (dnfw's method, notes/prior-art 6.1),
  * set each application's FIRST argument to the byte count to the end of the application
    (the value selload writes and sharc_ldr_parse checks; block 7's stock 0xc9bd8),
  * serialise, with a round-trip guarantee: parse(stock) -> serialise = stock, byte for byte.
"""
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import sharc_ldr_parse as P  # noqa: E402

HDR = 16
SIGN = 0xAD
SYS = 0x28000000          # boot-DMA alias of L1 (core byte address + 0x28000000)
FLAG_BITS = {v: k for k, v in P.BFLAGS.items()}


def header(flags, target, count, arg, bcode=1):
    code = (SIGN << 24) | bcode
    for f in flags:
        code |= 1 << FLAG_BITS[f]
    b = bytearray(struct.pack("<IIII", code, target, count, arg))
    x = 0
    for i, v in enumerate(b):
        if i != 2:
            x ^= v
    b[2] = x
    return bytes(b)


def loader_addr(core):
    """core byte address -> boot-stream target (L1 through the 0x28000000 alias; L2, DDR as is)"""
    if 0x240000 <= core < 0x3a0000:
        return core + SYS
    return core


class Block:
    __slots__ = ("flags", "target", "count", "arg", "payload", "bcode", "origin")

    def __init__(self, flags, target, count, arg, payload=b"", bcode=1, origin="new"):
        self.flags, self.target, self.count, self.arg = list(flags), target, count, arg
        self.payload, self.bcode, self.origin = bytearray(payload), bcode, origin

    @property
    def fill(self):
        return "FILL" in self.flags

    @property
    def marker(self):
        return bool({"FIRST", "FINAL", "INIT", "IGNORE"} & set(self.flags))

    def wire(self):
        return header(self.flags, self.target, self.count, self.arg, self.bcode) + bytes(self.payload)

    def wire_size(self):
        return HDR + len(self.payload)


class Stream:
    def __init__(self, data):
        blocks, errors, stop = P.parse(data)
        if errors or stop != len(data):
            raise ValueError("boot stream does not parse: %s" % errors[:1])
        self.blocks = []
        for b in blocks:
            pay = data[b["payload_offset"]:b["payload_offset"] + b["payload_len"]]
            blk = Block(b["flags"], b["target_address"], b["byte_count"], b["argument"], pay,
                        bcode=b["bcode"], origin="stock %d" % b["index"])
            self.blocks.append(blk)
        if self.to_bytes(fix_first=False) != data:
            raise ValueError("round trip failed: the serialiser does not reproduce the input")
        self.patches = []
        self.added = []

    # -------- queries --------
    def final_index(self):
        idx = [i for i, b in enumerate(self.blocks) if "FINAL" in b.flags]
        if len(idx) != 1 or idx[0] != len(self.blocks) - 1:
            raise ValueError("expected exactly one FINAL block, at the end")
        return idx[0]

    def _covering(self, la, n):
        """payload blocks whose written range overlaps [la, la+n) (loader addresses)"""
        hits = []
        for i, b in enumerate(self.blocks):
            if b.marker or not b.count:
                continue
            if b.target < la + n and la < b.target + b.count:
                hits.append(i)
        return hits

    # -------- edits --------
    def patch(self, core, old, new, why=""):
        """Replace bytes `old` with `new` at core byte address `core`, inside one payload block.
        The bytes must be written by exactly one block of the whole stream (no later
        overwrite) and that block must carry a payload (not a FILL)."""
        assert len(old) == len(new)
        la = loader_addr(core)
        hits = self._covering(la, len(old))
        if len(hits) != 1:
            raise ValueError("%#x: written by %d blocks %s, need exactly one" % (core, len(hits), hits))
        b = self.blocks[hits[0]]
        if b.fill:
            raise ValueError("%#x lies in FILL block %d: cannot patch" % (core, hits[0]))
        off = la - b.target
        if off < 0 or off + len(old) > len(b.payload):
            raise ValueError("%#x: patch crosses the edge of block %d" % (core, hits[0]))
        cur = bytes(b.payload[off:off + len(old)])
        if cur != bytes(old):
            raise ValueError("%#x: expected %s, found %s" % (core, bytes(old).hex(), cur.hex()))
        b.payload[off:off + len(new)] = new
        self.patches.append({"core": "%#x" % core, "block": hits[0], "origin": b.origin,
                             "old": bytes(old).hex(), "new": bytes(new).hex(), "why": why})

    def add_payload(self, core, data, why=""):
        data = bytes(data)
        if core % 4:
            raise ValueError("%#x: load target must be 4-byte aligned" % core)
        if len(data) % 4:
            data += bytes(4 - len(data) % 4)
        blk = Block([], loader_addr(core), len(data), 0, data)
        self.blocks.insert(self.final_index(), blk)
        self.added.append({"core": "%#x" % core, "bytes": len(data), "kind": "payload", "why": why})

    def add_fill(self, core, n, why=""):
        if core % 4 or n % 4:
            raise ValueError("FILL target and length must be multiples of 4")
        blk = Block(["FILL"], loader_addr(core), n, 0, b"")
        self.blocks.insert(self.final_index(), blk)
        self.added.append({"core": "%#x" % core, "bytes": n, "kind": "fill", "why": why})

    # -------- output --------
    def fix_first_args(self):
        """Each FIRST block's argument = bytes after its header up to the next FIRST block or
        the end of the stream (the rule the stock stream follows, notes/T3.1 section 3)."""
        offs, o = [], 0
        for b in self.blocks:
            offs.append(o)
            o += b.wire_size()
        total = o
        firsts = [i for i, b in enumerate(self.blocks) if "FIRST" in b.flags]
        for n, i in enumerate(firsts):
            end = offs[firsts[n + 1]] if n + 1 < len(firsts) else total
            self.blocks[i].arg = end - (offs[i] + HDR)

    def to_bytes(self, fix_first=True):
        if fix_first:
            self.fix_first_args()
        return b"".join(b.wire() for b in self.blocks)


def check_stream(data):
    """sharc_ldr_parse's own checks on a stream -> (ok, failed checks, blocks)"""
    blocks, errors, stop = P.parse(data)
    apps = P.applications(blocks, len(data))
    checks = P.check(data, blocks, errors, stop, apps)
    bad = [c for c in checks if not c["ok"]]
    return not bad and not errors, bad, blocks
