#!/usr/bin/env python3
"""SHARC+ patchkit modules: assemble with Selache, link ourselves, re-decode every word, run on the core.

Selache (public, pinned; tools/patchkit/pk_toolchains.py selache) assembles VISA source to a relocatable ELF whose
parcels are big-endian. This module

  1. assembles a source file (``assemble``) and reads sections, symbols and relocations (tools/patchkit/pk_elf.py),
  2. places the section at a chosen short-word (PC) address and resolves the relocations (``link``),
  3. swaps every 16-bit parcel to the boot-stream order and re-decodes every instruction with our own
     decoder (tools/sharc_dev/sharc_isa.py): Selache's instruction extents must equal ours, no instruction may be
     uncertain, and every relocated field must read back as the intended value (``verify``),
  4. optionally runs the linked code on the concrete SHARC+ core (``run_call``, tools/sharc_dev/sharc_run.py).

Relocations Selache emits (offsets and symbol values are in 16-bit parcels, the PC unit):
  type 11  absolute 24-bit target of a 48-bit jump/call/cjump (low 24 bits of the instruction)
  type 12  absolute 32-bit immediate/address of a 48-bit instruction (low 32 bits)
Both carry the symbol's short-word address; the addend is encoded in the symbol name ("sym + 4").

    uv run python -m tools.patchkit.pk_sharc module.s --base 0xB90000 [--extern name=0xADDR ...] [--json OUT]
"""

from __future__ import annotations

import argparse
import functools
import hashlib
import json
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent

from tools.patchkit import (  # noqa: E402
    pk_crosscheck,  # noqa: E402
    pk_cunion,  # noqa: E402
    pk_elf,  # noqa: E402
    pk_toolchains,  # noqa: E402
)
from tools.sharc_dev import sharc_selache as sel  # noqa: E402

SECTION = "seg_pmco"
PROCESSOR = "ADSP-21569"
R_ABS24, R_ABS32 = 11, 12
SW_ALIAS_BASE = (
    0x28000000  # loader byte address of short-word 0 (tools/sharc_dev/sharcldr.py)
)


class PkSharcError(RuntimeError):
    pass


@dataclass
class SharcReloc:
    parcel: int  # offset of the instruction, in parcels
    rtype: int
    symbol: str
    addend: int


@dataclass
class SharcObject:
    source_sha256: str
    section: bytes  # Selache parcel order (big-endian per 16 bits)
    symbols: dict[
        str, tuple[int, bool]
    ]  # name -> (parcel offset, global); defined symbols only
    relocs: list[SharcReloc]
    rows: list[tuple[int, str]]  # Selache instruction extents: (parcel offset, raw hex)
    toolchain: dict[str, str] = field(default_factory=dict)
    # Data sections (DM): name -> (bytes in memory order, alignment, is_bss); data symbols: name -> (section, byte offset, global)
    data: dict[str, tuple[bytes, int, bool]] = field(default_factory=dict)
    data_symbols: dict[str, tuple[str, int, bool]] = field(default_factory=dict)

    def to_json(self) -> dict[str, object]:
        return {
            "arch": "sharc+",
            "parcel_order": "selache-be",
            "source_sha256": self.source_sha256,
            "toolchain": self.toolchain,
            "section": self.section.hex(),
            "symbols": {
                k: {"parcel": v[0], "global": v[1]} for k, v in self.symbols.items()
            },
            "relocs": [r.__dict__ for r in self.relocs],
        }


@dataclass
class Linked:
    base_sw: int
    code_selache: bytes
    code_boot: bytes  # little-endian per parcel, the order of the boot stream and of tools/sharc_dev/sharc_isa.py
    symbols: dict[str, int]  # short-word addresses
    checked: int = 0  # instructions re-decoded


def _tool(name: str) -> Path:
    d = pk_toolchains.selache_dir()
    if d is None:
        raise PkSharcError(
            "selache is not built (uv run python -m tools.patchkit.pk_toolchains selache)"
        )
    return d / "target" / "release" / name


def split_symbol(name: str) -> tuple[str, int]:
    m = re.fullmatch(r"\s*([A-Za-z_.$][\w.$]*)\s*(?:([+-])\s*(\w+))?\s*", name)
    if not m:
        raise PkSharcError(f"cannot parse relocation symbol {name!r}")
    add = int(m.group(3), 0) * (-1 if m.group(2) == "-" else 1) if m.group(2) else 0
    return m.group(1), add


def _object(
    obj: Path,
    source_sha: str,
    section: str | None,
    preprocessed: str | None = None,
    compiled_asm: str | None = None,
) -> SharcObject:
    """The object Selache wrote, cross-checked (tools/patchkit/pk_crosscheck.py). `preprocessed` is an assembly
    source's preprocessor output (one statement per instruction is required); `compiled_asm` is selcc's assembly of a
    C unit (used only to verify relocation addends)."""
    elf = pk_elf.parse(obj.read_bytes())
    if section is None:
        section = next(
            (x.name for x in elf.sections if x.name in (SECTION, "seg_swco")), SECTION
        )
    listing = subprocess.run(
        [str(_tool("seldump")), "-ns", section, str(obj)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    sec = elf.section(section)
    if sec is None:
        raise PkSharcError(
            f"no section {section} (a .SECTION/PM {section}; directive is required)"
        )
    rows = sel.parse_listing(listing)
    if b"".join(r.raw for r in rows) != sec.data:
        raise PkSharcError("Selache listing bytes do not equal the section bytes")
    stmts = pk_crosscheck.statements(preprocessed) if preprocessed is not None else None
    try:
        mismatches = pk_crosscheck.check(obj.read_bytes(), section, sec.data, stmts)
    except pk_crosscheck.CrossCheckError as e:
        raise PkSharcError(f"cross-check (tools/patchkit/pk_crosscheck.py): {e}") from e
    if mismatches:
        raise PkSharcError(
            "Selache's listing and our decoder disagree on operands (tools/patchkit/pk_crosscheck.py):\n  "
            + "\n  ".join(m.format() for m in mismatches)
        )
    symbols = {
        s.name: (s.value, s.bind != "local")
        for s in elf.symbols
        if s.name and 0 < s.shndx < 0xFF00 and elf.sections[s.shndx].name == section
    }
    relocs = []
    for r in elf.relocs:
        if r.section != section:
            raise PkSharcError(f"relocation in unexpected section {r.section}")
        if r.symbol.name == "":
            raise PkSharcError("section-relative relocation not supported")
        name, add = split_symbol(r.symbol.name)
        relocs.append(SharcReloc(r.offset, r.rtype, name, add + r.addend))
    # Selache drops the constant of `symbol + constant` in some forms: every reference must match its relocation.
    names = {split_symbol(s.name)[0] for s in elf.symbols if s.name}
    rel3 = [(r.parcel, r.symbol, r.addend) for r in relocs]
    if stmts is not None:
        parcels = [
            i.external.parcel_address
            for i in sel.compare_external_bytes(sec.data).instructions
        ]
        bad = pk_crosscheck.check_addends(stmts, parcels, rel3, names)
    elif compiled_asm is not None:
        bad = pk_crosscheck.check_addends_unmapped(
            pk_crosscheck.statements(compiled_asm), rel3, names
        )
    else:
        bad = []
    if bad:
        raise PkSharcError(
            "relocation addends (docs/findings/20-patch-toolchains.md):\n  "
            + "\n  ".join(bad)
        )
    tc = {"selache_rev": sel.PINNED_REVISION}
    for n in ("selas", "seldump"):
        tc[n + "_sha256"] = hashlib.sha256(_tool(n).read_bytes()).hexdigest()
    data: dict[str, tuple[bytes, int, bool]] = {}
    for x in elf.sections:
        if (
            x.flags & pk_elf.SHF_ALLOC
            and not x.flags & pk_elf.SHF_EXECINSTR
            and x.name != section
            and x.size
        ):
            data[x.name] = (
                b"" if x.type == pk_elf.SHT_NOBITS else x.data,
                max(x.align, 4),
                x.type == pk_elf.SHT_NOBITS,
            )
    data_symbols = {
        s.name: (elf.sections[s.shndx].name, s.value, s.bind != "local")
        for s in elf.symbols
        if s.name and 0 < s.shndx < 0xFF00 and elf.sections[s.shndx].name in data
    }
    return SharcObject(
        source_sha,
        sec.data,
        symbols,
        relocs,
        _native_rows([(r.parcel_address, r.raw.hex()) for r in rows], relocs, sec.data),
        tc,
        data,
        data_symbols,
    )


def _native_rows(
    selache_rows: list[tuple[int, str]], relocs: list[SharcReloc], section: bytes
) -> list[tuple[int, str]]:
    """The instruction rows of the section by our decoder's extents, cross-checked against Selache's listing.

    seldump lists the unrelocated object and splits some 48-bit forms early (Type 3a `DM(I,M) = Ureg` is
    listed as 4 bytes plus a separate `0000` row), so Selache's rows cannot be the extents. Ours are; the
    check against Selache is that every row it lists either starts where ours does with the same length, or
    is a shorter listing of ours whose missing parcels are zero, and that each relocation Selache emitted
    sits on the start of one of our instructions."""
    cmp = sel.compare_external_bytes(section)
    native = [
        (i.external.parcel_address, i.external.raw.hex()) for i in cmp.instructions
    ]
    nat = dict(native)
    for parcel, raw in selache_rows:
        if parcel in nat:
            mine = nat[parcel]
            if mine != raw and not (
                mine.startswith(raw) and set(mine[len(raw) :]) <= {"0"}
            ):
                raise PkSharcError(
                    f"parcel {parcel:#x}: Selache lists {raw}, our decoder {mine}"
                )
    for r in relocs:
        if r.parcel not in nat:
            raise PkSharcError(
                f"relocation at parcel {r.parcel:#x} is not on one of our instruction starts"
            )
    return native


# Selache encode bugs found against the firmware image (docs/findings/05-sharc-isa-and-decoding.md): the
# re-decode below cannot see them when both decodes agree on a wrong immediate, so refuse the form.
# The negative-shift hazard is fixed by tools/patchkit/selache_patches; it stays armed for an unpatched build.
ENCODE_HAZARDS = [
    (
        re.compile(r"\blshift\b[^;]*\bby\s+-\s*\d", re.I),
        "LSHIFT ... BY -n clears the immediate's sign-extension nibble",
    ),
]
_NEG_SHIFT_PROBE = ".SECTION/PM seg_pmco;\nR2 = LSHIFT R15 BY -1;\n"
_NEG_SHIFT_WORD = "023e7800ff2f"


@functools.cache
def _negative_shift_patched(selas: str) -> bool:
    """True when this selas encodes a negative shift immediate with the stock data_hi bits."""
    try:
        rows = assemble(_NEG_SHIFT_PROBE, _check_hazards=False).rows
    except (PkSharcError, subprocess.CalledProcessError):
        return False
    return [raw for _, raw in rows] == [_NEG_SHIFT_WORD]


# Selache ignores the (SW)/(SWSE) suffix of a direct load (it emits the normal-word form), so
# `Rn = DM(symbol) (SW|SWSE);` is assembled as the normal-word load and rewritten here to the short-word Type 14d
# word (opcode 0x1a, bit 38 short word, bit 36 sign extension; the form tools/sharc_dev/sharc_asm.py `load_sw_abs` emits).
# The re-decode in `verify` then sees the short-word form and its relocated address.
_SW_LOAD = re.compile(
    r"^(\s*)R(\d{1,2})\s*=\s*DM\(\s*([A-Za-z_.$][\w.$]*)\s*\)\s*\((SWSE|SW)\)\s*;",
    re.I | re.M,
)


def _sw_prepare(source: str) -> tuple[str, list[tuple[str, bool]]]:
    marks: list[tuple[str, bool]] = []

    def sub(m: re.Match) -> str:
        label = f"_pk_sw{len(marks)}"
        marks.append((label, m.group(4).upper() == "SWSE"))
        # one line: the statements keep their source line numbers (pk_crosscheck reports them)
        return (
            f"{m.group(1)}.GLOBAL {label}; {label}: R{m.group(2)} = DM({m.group(3)});"
        )

    return _SW_LOAD.sub(sub, source), marks


def _sw_apply(obj: SharcObject, marks: list[tuple[str, bool]]) -> None:
    if not marks:
        return
    code = bytearray(obj.section)
    at_reloc = {r.parcel: r for r in obj.relocs}
    for label, signed in marks:
        parcel = obj.symbols.pop(label)[0]
        at = parcel * 2
        if parcel not in at_reloc or at_reloc[parcel].rtype != R_ABS32:
            raise PkSharcError(f"{label}: expected a 32-bit relocated load")
        if code[at] != 0x10 or code[at + 1] & 0xF0:
            raise PkSharcError(
                f"{label}: not a normal-word direct load ({code[at : at + 2].hex()})"
            )
        code[at] = 0x1A
        code[at + 1] |= 0x40 | (0x10 if signed else 0)
    obj.section = bytes(code)


# Selache drops `.VAR name[N];` and cannot emit a multi-value `.VAR`, so a zero-filled array is declared with
# `.PKDATA name, bytes;` (loaded zeros, part of the boot-stream block) or `.PKBSS name, bytes;` (reserved memory the
# stream does not load: the program must write it before it reads it) instead: the line is removed before
# assembly, `name` becomes an extern of the source and the object gets a data or bss section of that size with a
# global data symbol `name` (the linker allocates it).
_PKBSS = re.compile(
    r"^[ \t]*\.PK(BSS|DATA)[ \t]+([A-Za-z_.$][\w.$]*)[ \t]*,[ \t]*(0x[0-9a-fA-F]+|\d+)[ \t]*;[^\n]*$",
    re.M,
)


def _bss_prepare(source: str) -> tuple[str, list[tuple[str, int, bool]]]:
    found: list[tuple[str, int, bool]] = []

    def sub(m: re.Match) -> str:
        found.append((m.group(2), int(m.group(3), 0), m.group(1) == "BSS"))
        return f".EXTERN {m.group(2)};"

    return _PKBSS.sub(sub, source), found


_LABEL_DEF = re.compile(r"^\s*([A-Za-z_.][\w.]*)\s*:(?![:=])")
_COND_IF = re.compile(r"^\s*#\s*(if|ifdef|ifndef)\b")
_COND_ELSE = re.compile(r"^\s*#\s*(else|elif)\b")
_COND_END = re.compile(r"^\s*#\s*endif\b")


def duplicate_labels(source: str) -> list[tuple[str, int, int]]:
    """Labels defined twice in one assembly translation unit: (name, first line, second line), 1-based. selas accepts a
    redefined label silently and binds every reference to one definition, so a label shared by two concatenated
    sources (pk_build.dsp_object) sends one file's jumps into the other. Two definitions in different branches of the
    same #if/#else are exclusive and allowed. Comments are ignored."""
    text = re.sub(
        r"/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n"), source, flags=re.S
    )
    path: list[tuple[int, int]] = []  # (if id, branch index) of every open conditional
    n_if = 0
    seen: dict[str, list[tuple[int, tuple[tuple[int, int], ...]]]] = {}
    found: list[tuple[str, int, int]] = []
    for no, line in enumerate(text.split("\n"), 1):
        line = line.split("//", 1)[0]
        if _COND_IF.match(line):
            n_if += 1
            path.append((n_if, 0))
            continue
        if _COND_ELSE.match(line) and path:
            i, b = path[-1]
            path[-1] = (i, b + 1)
            continue
        if _COND_END.match(line) and path:
            path.pop()
            continue
        m = _LABEL_DEF.match(line)
        if not m:
            continue
        name, here = m.group(1), tuple(path)
        for first, there in seen.get(name, []):
            branches = dict(there)
            if not any(i in branches and branches[i] != b for i, b in here):
                found.append((name, first, no))
                break
        seen.setdefault(name, []).append((no, here))
    return found


def assemble(
    source: str, section: str | None = None, _check_hazards: bool = True
) -> SharcObject:
    dups = duplicate_labels(source)
    if dups:
        raise PkSharcError(
            "label defined twice (selas would bind every reference to one of them):\n"
            + "\n".join(f"  line {b}: {n} (first at line {a}:)" for n, a, b in dups)
        )
    if _check_hazards and _negative_shift_patched(str(_tool("selas"))):
        _check_hazards = False
    for pat, why in ENCODE_HAZARDS if _check_hazards else []:
        m = pat.search(source)
        if m:
            raise PkSharcError(f"known Selache encode bug ({why}): {m.group(0)!r}")
    original = source
    source, bss_decls = _bss_prepare(source)
    source, sw_marks = _sw_prepare(source)
    with tempfile.TemporaryDirectory(prefix="pk-sharc-") as d:
        src, obj = Path(d) / "m.s", Path(d) / "m.doj"
        src.write_text(source)
        r = subprocess.run(
            [str(_tool("selas")), "-proc", PROCESSOR, "-o", str(obj), str(src)],
            capture_output=True,
            text=True,
        )
        if r.returncode:
            raise PkSharcError(f"selas failed:\n{r.stdout}{r.stderr}")
        pre = Path(d) / "m.i"
        subprocess.run(
            [
                str(_tool("selas")),
                "-proc",
                PROCESSOR,
                "--preprocess-only",
                "-o",
                str(pre),
                str(src),
            ],
            capture_output=True,
            check=True,
        )
        result = _object(
            obj, hashlib.sha256(original.encode()).hexdigest(), section, pre.read_text()
        )
        _sw_apply(result, sw_marks)
        for name, size, is_bss in bss_decls:
            if size <= 0 or size % 4:
                raise PkSharcError(
                    f".PKBSS/.PKDATA {name}: size {size} is not a positive multiple of 4"
                )
            sec = f"pkbss_{name}" if is_bss else f"pkdata_{name}"
            result.data[sec] = (bytes(size), 4, is_bss)
            result.data_symbols[name] = (sec, 0, True)
        return result


def compile_c(
    source: str,
    section: str | None = None,
    include_dirs: tuple[Path, ...] | list[Path] = (),
    defines: tuple[str, ...] | list[str] = (),
    name: str = "C source",
) -> SharcObject:
    """Selache's C compiler (selcc); evaluated in docs/findings/20-patch-toolchains.md.

    `include_dirs` go on the include path (`-I`), `defines` are `NAME` or `NAME=VALUE` (`-D`). The object's
    source_sha256 is the hash of the preprocessed translation unit, so a changed header changes it. selcc's code is
    one section (`seg_swco`); a translation unit that it splits over several code sections is refused, and so is
    one that names a union member other than the first (selcc places union members like struct members;
    tools/patchkit/pk_cunion.py). `name` labels the source in errors."""
    flags = [f"-I{p}" for p in include_dirs] + [f"-D{x}" for x in defines]
    with tempfile.TemporaryDirectory(prefix="pk-sharc-") as d:
        src, obj, pre = Path(d) / "m.c", Path(d) / "m.doj", Path(d) / "m.i"
        src.write_text(source)
        r = subprocess.run(
            [
                str(_tool("selcc")),
                "-proc",
                PROCESSOR,
                *flags,
                "-E",
                "-o",
                str(pre),
                str(src),
            ],
            capture_output=True,
            text=True,
        )
        if r.returncode:
            raise PkSharcError(f"selcc failed:\n{r.stdout}{r.stderr}")
        unions = pk_cunion.check(pre.read_text())
        if unions:
            raise PkSharcError(pk_cunion.message(unions, name))
        r = subprocess.run(
            [
                str(_tool("selcc")),
                "-proc",
                PROCESSOR,
                *flags,
                "-c",
                "-o",
                str(obj),
                str(src),
            ],
            capture_output=True,
            text=True,
        )
        if r.returncode:
            raise PkSharcError(f"selcc failed:\n{r.stdout}{r.stderr}")
        asm = Path(d) / "m.s"
        r = subprocess.run(
            [
                str(_tool("selcc")),
                "-proc",
                PROCESSOR,
                *flags,
                "-S",
                "-o",
                str(asm),
                str(src),
            ],
            capture_output=True,
            text=True,
        )
        if r.returncode:
            raise PkSharcError(f"selcc -S failed:\n{r.stdout}{r.stderr}")
        elf = pk_elf.parse(obj.read_bytes())
        code = sorted(
            x.name
            for x in elf.sections
            if x.flags & pk_elf.SHF_EXECINSTR and x.flags & pk_elf.SHF_ALLOC and x.size
        )
        if len(code) > 1:
            raise PkSharcError(
                f"selcc split the code over sections {code}; patchkit objects have one code section"
            )
        result = _object(
            obj,
            hashlib.sha256(pre.read_bytes()).hexdigest(),
            code[0] if code else section,
            compiled_asm=asm.read_text(),
        )
        result.toolchain["selcc_sha256"] = hashlib.sha256(
            _tool("selcc").read_bytes()
        ).hexdigest()
        return result


def merge(objs: list[SharcObject]) -> SharcObject:
    """Several objects (assembled and compiled) as one: the code sections back to back in list order, data sections
    of the same name joined (each piece at its alignment), symbols and relocations moved with their section.

    A local symbol whose name is already taken by an earlier object is renamed `name~k` (k = the object's index) in
    that object, together with the relocations that use it; a second definition of a global symbol is an error.
    Relocations inside data sections are not supported (`_object` refuses them)."""
    if len(objs) == 1:
        return objs[0]
    code = bytearray()
    symbols: dict[str, tuple[int, bool]] = {}
    relocs: list[SharcReloc] = []
    rows: list[tuple[int, str]] = []
    data: dict[str, tuple[bytes, int, bool]] = {}
    data_symbols: dict[str, tuple[str, int, bool]] = {}
    toolchain: dict[str, str] = {}
    hashes = []
    for k, o in enumerate(objs):
        base = len(code) // 2
        taken = set(symbols) | set(data_symbols)
        defined = {
            **{n: g for n, (_, g) in o.symbols.items()},
            **{n: g for n, (_, _, g) in o.data_symbols.items()},
        }
        rename: dict[str, str] = {}
        for name, glob in defined.items():
            if name in taken:
                if glob:
                    raise PkSharcError(
                        f"symbol {name} is defined by two sources of one object"
                    )
                rename[name] = f"{name}~{k}"
        for name, (off, glob) in o.symbols.items():
            symbols[rename.get(name, name)] = (base + off, glob)
        for r in o.relocs:
            relocs.append(
                SharcReloc(
                    base + r.parcel, r.rtype, rename.get(r.symbol, r.symbol), r.addend
                )
            )
        rows += [(base + p, raw) for p, raw in o.rows]
        code += o.section
        for sec, (blob, align, bss) in o.data.items():
            have, have_align, have_bss = data.get(sec, (b"", align, bss))
            if have_bss != bss:
                raise PkSharcError(
                    f"data section {sec} is bss in one source and data in another"
                )
            start = -(-len(have) // align) * align
            data[sec] = (
                have + bytes(start - len(have)) + blob,
                max(align, have_align),
                bss,
            )
            for name, (s, off, glob) in o.data_symbols.items():
                if s == sec:
                    data_symbols[rename.get(name, name)] = (sec, start + off, glob)
        for key, value in o.toolchain.items():
            toolchain.setdefault(key, value)
        hashes.append(o.source_sha256)
    return SharcObject(
        hashlib.sha256(" ".join(hashes).encode()).hexdigest(),
        bytes(code),
        symbols,
        relocs,
        rows,
        toolchain,
        data,
        data_symbols,
    )


def self_check(code: bytearray, at: int, size: int, r: SharcReloc) -> None:
    """Selache leaves the field zero or holding the addend; anything else means we misread the relocation."""
    mask = (1 << (8 * size)) - 1
    have = int.from_bytes(code[at : at + size], "big")
    if have not in (0, r.addend & mask):
        raise PkSharcError(
            f"parcel {r.parcel:#x}: field {have:#x} is neither 0 nor the addend {r.addend} before relocation"
        )


def link(
    obj: SharcObject, base_sw: int, externs: dict[str, int] | None = None
) -> Linked:
    externs = externs or {}
    code = bytearray(obj.section)
    syms = {n: base_sw + off for n, (off, _g) in obj.symbols.items()}
    extents = dict(obj.rows)
    for r in obj.relocs:
        if r.symbol in syms:
            target = syms[r.symbol]
        elif r.symbol in externs:
            target = externs[r.symbol]
        else:
            raise PkSharcError(f"unresolved symbol {r.symbol}")
        target += r.addend
        row = bytes.fromhex(extents.get(r.parcel, ""))
        if len(row) != 6:
            raise PkSharcError(
                f"relocation at parcel {r.parcel:#x}: not a 48-bit instruction"
            )
        at = r.parcel * 2
        if r.rtype == R_ABS24:
            if not 0 <= target < 1 << 24:
                raise PkSharcError(
                    f"{r.symbol}: {target:#x} does not fit a 24-bit target"
                )
            self_check(code, at + 3, 3, r)
            code[at + 3 : at + 6] = target.to_bytes(3, "big")
        elif r.rtype == R_ABS32:
            self_check(code, at + 2, 4, r)
            code[at + 2 : at + 6] = (target & 0xFFFFFFFF).to_bytes(4, "big")
        else:
            raise PkSharcError(f"relocation type {r.rtype} not supported")
    out = Linked(base_sw, bytes(code), sel.swap_parcels(bytes(code)), syms)
    verify(obj, out, externs)
    return out


def _field(fields: dict[str, int], stem: str) -> int | None:
    """Join the pieces ``stem[hi:lo]`` of a decoded form into one number."""
    parts = []
    for k, v in fields.items():
        m = re.fullmatch(re.escape(stem) + r"\[(\d+):(\d+)\]", k)
        if m:
            parts.append((int(m.group(2)), v))
    if not parts:
        return fields.get(stem)
    return sum(v << lo for lo, v in parts)


def verify(obj: SharcObject, linked: Linked, externs: dict[str, int]) -> None:
    """Every instruction decodes natively with the extent Selache chose; relocated fields read back."""
    rows = []
    for parcel, raw_hex in obj.rows:
        at = parcel * 2
        raw = linked.code_selache[at : at + len(raw_hex) // 2]
        rows.append(sel.ExternalInstruction(parcel, raw, ""))
    cmp = sel.compare_rows(rows)
    problems = []
    by_parcel = {}
    for item in cmp.instructions:
        by_parcel[item.external.parcel_address] = item
        if item.native_form_id is None or not item.extent_agrees:
            problems.append(
                f"parcel {item.external.parcel_address:#x} {item.external.raw.hex()}: "
                f"native {item.native_form_id or item.native_candidates} extent_agrees={item.extent_agrees}"
            )
    for r in obj.relocs:
        item = by_parcel[r.parcel]
        fields = dict(item.native_fields)
        want = (linked.symbols.get(r.symbol, externs.get(r.symbol)) or 0) + r.addend
        got = None
        for stem in ("addr", "data", "reladdr"):
            got = _field(fields, stem)
            if got is not None:
                break
        mask = 0xFFFFFF if r.rtype == R_ABS24 else 0xFFFFFFFF
        if got != want & mask:
            problems.append(
                f"parcel {r.parcel:#x} ({item.native_form_id}): relocated field reads {got} want {want & mask:#x}"
            )
    if problems:
        raise PkSharcError(
            "Selache output disagrees with our decoder:\n  " + "\n  ".join(problems)
        )
    linked.checked = len(cmp.instructions)


def build(
    source: str, base_sw: int, externs: dict[str, int] | None = None
) -> tuple[SharcObject, Linked]:
    obj = assemble(source)
    return obj, link(obj, base_sw, externs)


# ---------------------------------------------------------------- running on the concrete core


def run_call(
    linked: Linked,
    entry: str,
    regs: dict[str, int] | None = None,
    dm_words: dict[int, int] | None = None,
    reads: dict[str, tuple[int, int]] | None = None,
    max_steps: int = 100000,
    return_sw: int = 0xB9F000,
    approx_recips: bool = False,
    others: tuple[Linked, ...] = (),
    read_regs: tuple[str, ...] = (),
    extra_breaks: tuple[int, ...] = (),
) -> dict[str, object]:
    """Run ``entry`` (a symbol) to its RTS on the concrete core and return registers and memory reads.

    ``dm_words`` maps normal-word DM addresses (L2: 0x08000000 up) to values to poke before the run;
    ``reads`` maps a result name to (normal-word address, word count). Floats are passed as raw bits.
    """
    from tools.sharc_dev import sharc_run
    from tools.sharc_dev import sharc_trace as st
    from tools.sharc_dev.sharcldr import LoadedMemory

    start = linked.symbols[entry]
    blocks, data = [], b""
    for i, obj in enumerate((linked, *others)):
        blocks.append(
            {
                "target_address": SW_ALIAS_BASE + 2 * obj.base_sw,
                "byte_count": len(obj.code_boot),
                "fill": False,
                "payload_offset": len(data),
                "payload_len": len(obj.code_boot),
                "index": i,
            }
        )
        data += obj.code_boot
    mem = LoadedMemory(data, blocks)
    runner = sharc_run.Runner(
        mem,
        start,
        regs=regs or {},
        breakpoints=[return_sw, *extra_breaks],
        approx_recips=approx_recips,
    )
    for addr, word in (dm_words or {}).items():
        if not st._dm_write(
            runner.state, addr, 4, st.Const(word & 0xFFFFFFFF), normal_word=True
        ):
            raise PkSharcError(f"cannot poke DM word {addr:#x}")
    runner.state.call_stack.append(return_sw)
    res = runner.run(max_steps)
    out: dict[str, object] = {
        "halt": res.halt.to_json(),
        "instructions": res.instructions,
        "final_pc_sw": res.final_pc_sw,
    }
    for name, (addr, count) in (reads or {}).items():
        vals = []
        for k in range(count):
            v = st._dm_read(runner.state, addr + k, 4, normal_word=True)
            vals.append(None if v is None else v.value & 0xFFFFFFFF)
        out[name] = vals
    out["regs"] = {
        n: getattr(runner.state.uregs.get(st.UREG_CODES[n]), "value", None)
        for n in (
            "R0",
            "R1",
            "R2",
            "R4",
            "R5",
            "R6",
            "R7",
            "R8",
            "R9",
            "R10",
            "R11",
            "R12",
            "R13",
            "I0",
            *read_regs,
        )
    }
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    ap.add_argument("source")
    ap.add_argument(
        "--base",
        type=lambda x: int(x, 0),
        required=True,
        help="short-word (PC) address of the section",
    )
    ap.add_argument("--extern", action="append", default=[], metavar="NAME=ADDR")
    ap.add_argument("--json", help="write the object prototype here")
    a = ap.parse_args()
    externs = {k: int(v, 0) for k, v in (e.split("=") for e in a.extern)}
    obj, linked = build(Path(a.source).read_text(), a.base, externs)
    print(
        f"linked {len(linked.code_boot)} bytes at sw {a.base:#x}; {linked.checked} instructions re-decoded OK"
    )
    for n, v in sorted(linked.symbols.items(), key=lambda kv: kv[1]):
        print(f"  {v:#08x} {n}")
    if a.json:
        Path(a.json).write_text(json.dumps(obj.to_json(), indent=1))


if __name__ == "__main__":
    main()
