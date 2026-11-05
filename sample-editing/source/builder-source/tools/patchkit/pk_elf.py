"""Minimal ELF32 relocatable-object reader for the patchkit linkers (ColdFire and SHARC+).

Reads sections, symbols and REL/RELA relocations from either byte order. No writing, no dependency on
any toolchain: a patchkit object is built from what this returns (tools/patchkit/pk_link.py, tools/patchkit/pk_sharc.py).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

SHT_SYMTAB, SHT_RELA, SHT_NOBITS, SHT_REL = 2, 4, 8, 9
SHF_ALLOC, SHF_EXECINSTR = 2, 4
STB = {0: "local", 1: "global", 2: "weak"}
STT = {0: "notype", 1: "object", 2: "func", 3: "section", 4: "file"}


@dataclass
class Section:
    index: int
    name: str
    type: int
    flags: int
    addr: int
    offset: int
    size: int
    link: int
    info: int
    align: int
    entsize: int
    data: bytes = b""


@dataclass
class Symbol:
    index: int
    name: str
    value: int
    size: int
    bind: str
    type: str
    shndx: int  # 0 undefined, 0xfff1 absolute, 0xfff2 common


@dataclass
class Reloc:
    section: str  # section the relocation applies to
    offset: int
    rtype: int
    symbol: Symbol
    addend: int
    implicit: bool = False  # SHT_REL: the addend lives in the section bytes


@dataclass
class Elf:
    machine: int
    little: bool
    sections: list[Section] = field(default_factory=list)
    symbols: list[Symbol] = field(default_factory=list)
    relocs: list[Reloc] = field(default_factory=list)

    def section(self, name: str) -> Section | None:
        return next((s for s in self.sections if s.name == name), None)


def parse(blob: bytes) -> Elf:
    if blob[:4] != b"\x7fELF" or blob[4] != 1:
        raise ValueError("not an ELF32 object")
    little = blob[5] == 1
    e = "<" if little else ">"
    machine = struct.unpack_from(e + "H", blob, 0x12)[0]
    shoff = struct.unpack_from(e + "I", blob, 0x20)[0]
    shentsize, shnum, shstrndx = struct.unpack_from(e + "HHH", blob, 0x2E)
    raw = []
    for i in range(shnum):
        raw.append(struct.unpack_from(e + "IIIIIIIIII", blob, shoff + i * shentsize))
    strtab_off = raw[shstrndx][4]

    def cstr(base: int, off: int) -> str:
        end = blob.index(b"\0", base + off)
        return blob[base + off : end].decode()

    elf = Elf(machine, little)
    for i, (nm, ty, fl, ad, of, sz, lk, inf, al, es) in enumerate(raw):
        data = b"" if ty == SHT_NOBITS else blob[of : of + sz]
        elf.sections.append(
            Section(i, cstr(strtab_off, nm), ty, fl, ad, of, sz, lk, inf, al, es, data)
        )
    symsec = next((s for s in elf.sections if s.type == SHT_SYMTAB), None)
    if symsec:
        stroff = elf.sections[symsec.link].offset
        for i in range(symsec.size // 16):
            nm, val, sz, info, _other, shndx = struct.unpack_from(
                e + "IIIBBH", blob, symsec.offset + i * 16
            )
            elf.symbols.append(
                Symbol(
                    i,
                    cstr(stroff, nm) if nm else "",
                    val,
                    sz,
                    STB.get(info >> 4, "?"),
                    STT.get(info & 15, "?"),
                    shndx,
                )
            )
    for s in elf.sections:
        if s.type in (SHT_RELA, SHT_REL):
            target = elf.sections[s.info].name
            n = 12 if s.type == SHT_RELA else 8
            for k in range(s.size // n):
                if s.type == SHT_RELA:
                    off, info, add = struct.unpack_from(e + "IIi", s.data, k * n)
                else:
                    off, info = struct.unpack_from(e + "II", s.data, k * n)
                    add = 0
                elf.relocs.append(
                    Reloc(
                        target,
                        off,
                        info & 0xFF,
                        elf.symbols[info >> 8],
                        add,
                        s.type == SHT_REL,
                    )
                )
    return elf
