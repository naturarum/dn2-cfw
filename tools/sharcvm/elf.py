"""Minimal ELF32 reader for selache .dxe files (SHARC+), stdlib only.

Returns loadable sections converted to the SHARC+ memory image conventions used by
the interpreter (and by scripts/t32_load.py for the DN2 image):
  * SW (VISA) code sections (sh_entsize == 1, SW address 0x120000-0x1cffff or
    0xb80000-0xbfffff): selache stores 16-bit parcels big-endian; memory holds them
    little-endian at byte address 2*SW (L1) or 0x20000000 + 2*(SW-0xb80000) (L2).
  * 48-bit NW code (the IVT at PM 0x90000): kept separately as (nw_addr, bytes);
    the interpreter does not execute it (it starts at the ELF entry).
  * byte-addressed data: copied as is.
"""
import struct
from dataclasses import dataclass, field


@dataclass
class Section:
    name: str
    addr: int
    data: bytes
    kind: str          # 'sw_code' | 'nw_code' | 'data' | 'bss'


@dataclass
class Elf:
    entry: int
    sections: list = field(default_factory=list)
    symbols: dict = field(default_factory=dict)   # name -> value


def sw_to_byte(sw):
    if 0xb80000 <= sw < 0xc00000:
        return 0x20000000 + 2 * (sw - 0xb80000)
    return 2 * sw


def read_elf(path):
    b = open(path, "rb").read()
    if b[:4] != b"\x7fELF" or b[4] != 1 or b[5] != 1:
        raise ValueError(f"{path}: not an ELF32 LE file")
    (e_type, e_machine, e_version, e_entry, e_phoff, e_shoff, e_flags, e_ehsize,
     e_phentsize, e_phnum, e_shentsize, e_shnum, e_shstrndx) = struct.unpack_from("<HHIIIIIHHHHHH", b, 16)
    shdrs = [struct.unpack_from("<IIIIIIIIII", b, e_shoff + i * e_shentsize) for i in range(e_shnum)]
    shstr = shdrs[e_shstrndx]
    def name_at(tab, off):
        s = tab[4] + off
        return b[s:b.index(b"\0", s)].decode()
    elf = Elf(entry=e_entry)
    for sh in shdrs:
        name_off, sh_type, flags, addr, off, size, link, info, align, entsize = sh
        name = name_at(shstr, name_off)
        if sh_type == 1 and (flags & 2):        # PROGBITS, ALLOC
            data = b[off:off + size]
            if entsize == 1 and (flags & 4):      # VISA code
                if size % 2:
                    raise ValueError(f"{name}: odd SW code size")
                data = b"".join(data[i:i + 2][::-1] for i in range(0, size, 2))
                elf.sections.append(Section(name, addr, data, "sw_code"))
            elif (flags & 4) and 0x80000 <= addr < 0x100000:
                elf.sections.append(Section(name, addr, data, "nw_code"))
            else:
                elf.sections.append(Section(name, addr, data, "data"))
        elif sh_type == 8 and (flags & 2):      # NOBITS
            elf.sections.append(Section(name, addr, bytes(size), "bss"))
        elif sh_type == 2:                       # SYMTAB
            strtab = shdrs[link]
            for i in range(size // 16):
                st_name, st_value, st_size, st_info, st_other, st_shndx = struct.unpack_from("<IIIBBH", b, off + 16 * i)
                if st_name:
                    elf.symbols[name_at(strtab, st_name)] = st_value
    return elf
