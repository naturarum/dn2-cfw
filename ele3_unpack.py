#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
# nrv2b() follows the NRV2B decoder of UCL (Markus F.X.J. Oberhumer, GPL-2.0-or-later), as UPX uses it.
"""Unpack an Elektron "ELE3" OS update (.syx). Tested on Digitone II OS 1.12.

Usage: python3 ele3_unpack.py elektron-firmware/Digitone_II_OS1_12.syx [out_dir]

Format notes (reverse-engineered from DN2 OS 1.12, Oct 2026):
  SysEx    F0 00 20 3C 15 00 ... F7          Elektron ID, device 0x15
    7F 01|02 10 <first:3> <count:3>         begin / end marker (3 = three 7-bit bytes, big-endian)
    7E <block:3> <116 packed> <1 unknown>   data; 7-bit groups (MSB byte first, bit 6 = 1st byte)
                                            -> 101 bytes per message; last raw byte not identified yet
  Stream   u32 BE image size | u32 BE image checksum (algorithm not identified yet) | image
  Image    "ELE3" | ID text @0x04 | version @0x14 | u32 section count @0x1C
           | count x {u32 type, offset, length, load address} @0x20 | sections
           | 8 x 00 + 32-byte tail (not a plain SHA-256 of the image; open question)
  Packed sections: u32 BE n | u32 BE byte-sum of the n packed bytes | UCL NRV2B stream (8-bit bit buffer)
"""
import functools, json, operator, pathlib, struct, sys


def syx_messages(raw):
    i = 0
    while i < len(raw):
        j = raw.index(0xF7, i)
        yield raw[i:j + 1]
        i = j + 1


def unpack7(p):
    out = bytearray()
    for g in range(0, len(p), 8):
        msb = p[g]
        out += bytes(b | ((msb >> (6 - k)) & 1) << 7 for k, b in enumerate(p[g + 1:g + 8]))
    return out


def n21(b):
    return b[0] << 14 | b[1] << 7 | b[2]


def nrv2b(src):
    """UCL NRV2B decompressor with 8-bit bit buffer (the algorithm UPX uses)."""
    dst, pos, bb, last = bytearray(), 0, 0, 1

    def bit():
        nonlocal bb, pos
        if bb & 0x7F:
            bb = (bb << 1) & 0x1FF
        else:
            bb = src[pos] * 2 + 1
            pos += 1
        return bb >> 8 & 1

    while True:
        while bit():
            dst.append(src[pos])
            pos += 1
        off = 1
        while True:
            off = off * 2 + bit()
            if bit():
                break
        if off == 2:
            off = last
        else:
            off = (off - 3) * 256 + src[pos]
            pos += 1
            if off == 0xFFFFFFFF:
                return bytes(dst), pos
            off += 1
            last = off
        ln = bit()
        ln = ln * 2 + bit()
        if ln == 0:
            ln = 1
            while True:
                ln = ln * 2 + bit()
                if bit():
                    break
            ln += 2
        n = ln + 1 + (off > 0xD00)
        p = len(dst) - off
        if off >= n:
            dst += dst[p:p + n]
        else:
            for _ in range(n):
                dst.append(dst[p])
                p += 1


def guess(t, d, addr):
    if t == 5:
        return "build timestamp"
    if len(d) >= 16 and d[3] == 0xAD and functools.reduce(operator.xor, d[:16]) == 0:
        return "Analog Devices boot stream (SHARC DSP)"
    if d[0x1000:0x1004] == b"\xd1\x00\x20\x41":
        return "NXP i.MX RT-style image (IVT at 0x1000)"
    if b"BOOTSTRAP UPGRADE" in d:
        return "68k/ColdFire bootstrap upgrader"
    if addr and d.count(b"\x4e\x75") > 50:
        return f"68k/ColdFire code, loads at {addr:#010x}"
    return "unknown"


def main(path, out="ele3_out"):
    raw = pathlib.Path(path).read_bytes()
    msgs = list(syx_messages(raw))
    assert all(m[1:4] == b"\x00\x20\x3c" for m in msgs), "not Elektron SysEx"
    ctrl = [m for m in msgs if m[6] == 0x7F]
    data = [m for m in msgs if m[6] == 0x7E]
    first, count = n21(ctrl[0][9:12]), n21(ctrl[0][12:15])
    assert [n21(m[7:10]) for m in data] == list(range(first, first + count)), "missing or reordered blocks"
    stream = b"".join(unpack7(m[10:-2]) for m in data)
    size, img_chk = struct.unpack(">II", stream[:8])
    img = stream[8:8 + size]
    assert img[:4] == b"ELE3", "no ELE3 magic"
    outdir = pathlib.Path(out)
    outdir.mkdir(exist_ok=True)
    (outdir / "image.bin").write_bytes(img)
    text = lambda b: b.split(b"\0")[0].decode(errors="replace").strip()
    man = {"device": msgs[0][4], "blocks": count, "first_block": first, "image_size": size,
           "image_checksum": f"{img_chk:#010x}", "id": text(img[4:0x14].lstrip(b"\0")),
           "version": text(img[0x14:0x1C]), "sections": []}
    nsec = struct.unpack(">I", img[0x1C:0x20])[0]
    for k in range(nsec):
        t, off, ln, addr = struct.unpack(">IIII", img[0x20 + 16 * k:0x30 + 16 * k])
        sec = img[off:off + ln]
        name = f"{k:02d}_type{t}"
        (outdir / f"{name}.bin").write_bytes(sec)
        info = {"type": t, "offset": off, "length": ln, "load_address": f"{addr:#010x}"}
        n = struct.unpack(">I", sec[:4])[0] if ln >= 8 else -1
        if (8 + n + 3) // 4 * 4 == ln:
            packed = sec[8:8 + n]
            body, used = nrv2b(packed)
            (outdir / f"{name}.unpacked.bin").write_bytes(body)
            info.update(packed=n, unpacked=len(body), consumed_all=used == n,
                        bytesum_ok=sum(packed) & 0xFFFFFFFF == struct.unpack(">I", sec[4:8])[0])
        else:
            body = sec
        if t == 5:
            man["build"] = text(sec)
        info["content"] = guess(t, body, addr)
        man["sections"].append(info)
        print(f"type {t}: {ln:>9,} B -> {info.get('unpacked', ln):>9,} B  {info['content']}")
    end = max(s["offset"] + s["length"] for s in man["sections"])
    man["tail"] = img[end:].hex()
    (outdir / "manifest.json").write_text(json.dumps(man, indent=2))
    print(f"OS {man['version']} built {man.get('build')}, written to {outdir}/")


if __name__ == "__main__":
    main(*sys.argv[1:3])
