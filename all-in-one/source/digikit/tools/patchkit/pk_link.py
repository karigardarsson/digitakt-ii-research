#!/usr/bin/env python3
"""ColdFire (MCF5441x, ISA_C + EMAC, no FPU) patchkit modules: compile, make an object, link, check, call.

    uv run python -m tools.patchkit.pk_link build src.c [more.s ...] --base 0x47e8b000 --syms-module tools.coldfire.cf_names_dt2_1_17 \
        [--sym name=0xADDR ...] [--object out.dko.json] [--bin out.bin]

Pipeline (the same one patchkit's module build and patch-time linker follow):

  1. ``compile_source``: m68k-elf-gcc / as (tools/patchkit/pk_toolchains.py m68k) -mcpu=54418 -> relocatable ELF.
  2. ``object_from_elf``: the ELF becomes a JSON *object prototype*: allocatable sections as hex, symbols,
     relocations, source hash, toolchain version. Nothing else of the ELF is needed afterwards, so patch
     time needs no ELF parser and no toolchain.
  3. ``link``: place the sections at a base address (text, rodata, data, then bss as zeros), resolve extern
     symbols from a symbol map (for example tools/coldfire/cf_names_dt2_1_17.py ADDRS), apply the relocations.
  4. ``check_isa``: decode every instruction of the executable sections with our own decoder (packages/emulator/coldfire
     ``cfdis``, generated from tools/coldfire/cfisa/coldfire.json); anything that is not ISA_A/B/C or EMAC (a 68020+
     form, an FPU form, a privileged form, an illegal word) fails.

Relocation types handled: R_68K_32/16/8 (absolute), R_68K_PC32/16/8 and R_68K_PLT32/16/8 (S + A - P).
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent

from tools.patchkit import (  # noqa: E402
    pk_elf,  # noqa: E402
    pk_toolchains,  # noqa: E402
)

CPU = "54418"
CFLAGS = [
    f"-mcpu={CPU}",
    "-Os",
    "-ffreestanding",
    "-fno-builtin",
    "-nostdlib",
    "-fno-pic",
    "-fno-pie",
    "-ffunction-sections",
    "-fdata-sections",
    "-fno-asynchronous-unwind-tables",
    "-fno-unwind-tables",
    "-fno-stack-protector",
    "-Wall",
    "-Wextra",
    "-Werror",
]
ASFLAGS = [f"-mcpu={CPU}"]
EM_68K = 4
R_68K = {
    1: ("32", 4, False),
    2: ("16", 2, False),
    3: ("8", 1, False),
    4: ("PC32", 4, True),
    5: ("PC16", 2, True),
    6: ("PC8", 1, True),
    18: ("PLT32", 4, True),
    19: ("PLT16", 2, True),
    20: ("PLT8", 1, True),
}
ALLOWED_UNITS = {"isa_a", "isa_b", "isa_c", "emac"}
KIND_ORDER = ["text", "rodata", "data", "bss"]


class PkLinkError(RuntimeError):
    pass


def _tool(name: str) -> Path:
    p = pk_toolchains.m68k_bin(name)
    if p is None:
        raise PkLinkError(
            "m68k-elf toolchain not installed (uv run python -m tools.patchkit.pk_toolchains m68k)"
        )
    return p


def toolchain_version() -> dict[str, str]:
    out = {}
    for n in ("gcc", "as", "ld"):
        out[n] = subprocess.run(
            [str(_tool(n)), "--version"], capture_output=True, text=True
        ).stdout.splitlines()[0]
    out["cflags"] = " ".join(CFLAGS)
    return out


def compile_source(path: Path, out: Path) -> None:
    """One C or assembly file to a relocatable ELF."""
    if path.suffix in (".s", ".S"):
        cmd = [str(_tool("as")), *ASFLAGS, "-o", str(out), str(path)]
    else:
        cmd = [str(_tool("gcc")), *CFLAGS, "-c", "-o", str(out), str(path)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        raise PkLinkError(
            f"{' '.join(cmd[:1])} failed on {path}:\n{r.stdout}{r.stderr}"
        )


def merge_objects(objs: list[Path], out: Path) -> None:
    """Combine several relocatable ELFs into one (ld -r), keeping relocations."""
    r = subprocess.run(
        [str(_tool("ld")), "-r", "-o", str(out), *map(str, objs)],
        capture_output=True,
        text=True,
    )
    if r.returncode:
        raise PkLinkError(f"ld -r failed:\n{r.stderr}")


def _kind(sec: pk_elf.Section) -> str:
    if sec.type == pk_elf.SHT_NOBITS:
        return "bss"
    if sec.flags & pk_elf.SHF_EXECINSTR:
        return "text"
    if sec.flags & 1:  # SHF_WRITE
        return "data"
    return "rodata"


def object_from_elf(blob: bytes, sources: list[Path]) -> dict[str, object]:
    elf = pk_elf.parse(blob)
    if elf.machine != EM_68K or elf.little:
        raise PkLinkError("not a big-endian m68k object")
    keep = {
        s.name: s
        for s in elf.sections
        if s.flags & pk_elf.SHF_ALLOC and s.type in (1, pk_elf.SHT_NOBITS) and s.size
    }
    sections = [
        {
            "name": s.name,
            "kind": _kind(s),
            "align": s.align or 1,
            "size": s.size,
            "hex": s.data.hex() if s.type != pk_elf.SHT_NOBITS else "",
        }
        for s in keep.values()
    ]
    symbols = []
    for sym in elf.symbols:
        if not sym.name or sym.type in ("file",):
            continue
        if 0 < sym.shndx < 0xFF00 and elf.sections[sym.shndx].name in keep:
            symbols.append(
                {
                    "name": sym.name,
                    "section": elf.sections[sym.shndx].name,
                    "value": sym.value,
                    "global": sym.bind != "local",
                    "func": sym.type == "func",
                }
            )
        elif sym.shndx == 0:
            symbols.append(
                {
                    "name": sym.name,
                    "section": None,
                    "value": 0,
                    "global": True,
                    "func": False,
                }
            )
        elif sym.shndx == 0xFFF2:
            raise PkLinkError(f"common symbol {sym.name}: compile with -fno-common")
    relocs = []
    for r in elf.relocs:
        if r.section not in keep:
            continue  # relocations of debug/unwind sections
        if r.rtype not in R_68K:
            raise PkLinkError(
                f"unsupported relocation type {r.rtype} in {r.section}+{r.offset:#x} -> {r.symbol.name}"
            )
        if r.symbol.type == "section":
            to = {"to_section": elf.sections[r.symbol.shndx].name}
        else:
            to = {"to_symbol": r.symbol.name}
        relocs.append(
            {
                "in": r.section,
                "offset": r.offset,
                "type": R_68K[r.rtype][0],
                **to,
                "addend": r.addend,
            }
        )
    src_hash = hashlib.sha256(b"".join(p.read_bytes() for p in sources)).hexdigest()
    return {
        "arch": "cf-mcf54418",
        "format": "dko-prototype-1",
        "source_sha256": src_hash,
        "sources": [p.name for p in sources],
        "toolchain": toolchain_version(),
        "sections": sections,
        "symbols": symbols,
        "relocs": relocs,
    }


@dataclass
class Linked:
    base: int
    image: bytes  # text + rodata + data at base; bss (zero) follows
    bss_size: int
    symbols: dict[str, int]
    text_ranges: list[tuple[int, int]]
    placement: dict[str, int]
    relocs_applied: dict[str, int]


def link(obj: dict, base: int, symmap: dict[str, int] | None = None) -> Linked:
    symmap = symmap or {}
    secs = sorted(obj["sections"], key=lambda s: KIND_ORDER.index(s["kind"]))
    placement: dict[str, int] = {}
    addr = base
    image = bytearray()
    text_ranges = []
    bss_start = None
    for s in secs:
        al = s["align"]
        addr = (addr + al - 1) // al * al
        placement[s["name"]] = addr
        if s["kind"] == "bss":
            bss_start = addr if bss_start is None else bss_start
        else:
            image += b"\0" * (addr - base - len(image))
            image += bytes.fromhex(s["hex"])
            if s["kind"] == "text":
                text_ranges.append((addr, addr + s["size"]))
        addr += s["size"]
    bss_size = 0 if bss_start is None else addr - bss_start
    syms: dict[str, int] = {}
    for sy in obj["symbols"]:
        if sy["section"] is not None:
            syms[sy["name"]] = placement[sy["section"]] + sy["value"]
    for sy in obj["symbols"]:
        if sy["section"] is None and sy["name"] not in syms:
            if sy["name"] not in symmap:
                raise PkLinkError(f"unresolved extern symbol {sy['name']}")
            syms[sy["name"]] = symmap[sy["name"]]
    applied: dict[str, int] = {}
    sizes = {name: size for name, size, _pc in R_68K.values()}
    pcrel = {name: pc for name, _size, pc in R_68K.values()}
    for r in obj["relocs"]:
        if "to_symbol" in r:
            if r["to_symbol"] not in syms:
                raise PkLinkError(f"relocation against unknown symbol {r['to_symbol']}")
            s_val = syms[r["to_symbol"]]
        else:
            s_val = placement[r["to_section"]]
        place = placement[r["in"]] + r["offset"]
        value = s_val + r["addend"]
        t = r["type"]
        size = sizes[t]
        if pcrel[t]:
            value -= place
            lo, hi = -(1 << (8 * size - 1)), 1 << (8 * size - 1)
        else:
            lo, hi = -(1 << (8 * size - 1)), 1 << (8 * size)
        if not lo <= value < hi:
            raise PkLinkError(
                f"relocation {t} at {place:#x} -> {r.get('to_symbol') or r.get('to_section')}: {value:#x} out of range"
            )
        off = place - base
        image[off : off + size] = (value & ((1 << (8 * size)) - 1)).to_bytes(
            size, "big"
        )
        applied[t] = applied.get(t, 0) + 1
    return Linked(base, bytes(image), bss_size, syms, text_ranges, placement, applied)


def check_isa(linked: Linked) -> dict[str, int]:
    """Decode every instruction of the executable sections with our decoder; return the form histogram."""
    if (
        not linked.text_ranges
    ):  # a data-only object (strings, tables): nothing to decode
        return {}
    cfdis = ROOT / "target" / "release" / "cfdis"
    if not cfdis.exists():
        r = subprocess.run(
            ["cargo", "build", "--release", "-p", "coldfire", "--bin", "cfdis"],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        if r.returncode:
            raise PkLinkError(f"cannot build cfdis:\n{r.stderr[-2000:]}")
    units = {}
    for line in subprocess.run(
        [str(cfdis), "--forms"], capture_output=True, text=True, check=True
    ).stdout.splitlines():
        form, unit, priv, _page = line.split("\t")
        units[form] = (unit, priv == "true")
    with tempfile.NamedTemporaryFile(suffix=".bin") as f:
        f.write(linked.image)
        f.flush()
        ranges = "".join(f"{lo:x} {hi:x}\n" for lo, hi in linked.text_ranges)
        out = subprocess.run(
            [str(cfdis), f.name, f"{linked.base:x}", "--ranges"],
            input=ranges,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    hist: dict[str, int] = {}
    bad = []
    for line in out.splitlines():
        if line.startswith("R "):
            continue
        addr, length, form, _text = line.split(" ", 3)
        if length == "0":
            bad.append(f"{addr}: illegal encoding")
            continue
        unit, priv = units[form]
        hist[form] = hist.get(form, 0) + 1
        if unit not in ALLOWED_UNITS or priv:
            bad.append(f"{addr}: {form} ({unit}{', privileged' if priv else ''})")
    if bad:
        raise PkLinkError("instructions outside ISA_A/B/C+EMAC:\n  " + "\n  ".join(bad))
    return hist


def load_symmap(module: str | None, pairs: list[str]) -> dict[str, int]:
    syms: dict[str, int] = {}
    if module:
        mod = importlib.import_module(module)
        syms.update({k: v[0] for k, v in mod.ADDRS.items()})
    for p in pairs:
        k, v = p.split("=")
        syms[k] = int(v, 0)
    return syms


def build(
    sources: list[Path], base: int, symmap: dict[str, int]
) -> tuple[dict, Linked, dict[str, int]]:
    with tempfile.TemporaryDirectory(prefix="pk-link-") as d:
        objs = []
        for i, src in enumerate(sources):
            o = Path(d) / f"{i}_{src.stem}.o"
            compile_source(src, o)
            objs.append(o)
        if len(objs) == 1:
            merged = objs[0]
        else:
            merged = Path(d) / "all.o"
            merge_objects(objs, merged)
        obj = object_from_elf(merged.read_bytes(), sources)
    linked = link(obj, base, symmap)
    return obj, linked, check_isa(linked)


def main() -> None:
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("sources", nargs="+", type=Path)
    b.add_argument("--base", type=lambda x: int(x, 0), default=0x47E8B000)
    b.add_argument("--syms-module")
    b.add_argument("--sym", action="append", default=[], metavar="NAME=ADDR")
    b.add_argument("--object", type=Path)
    b.add_argument("--bin", type=Path)
    a = ap.parse_args()
    obj, linked, hist = build(a.sources, a.base, load_symmap(a.syms_module, a.sym))
    print(
        f"{len(linked.image)} bytes at {a.base:#x} (+{linked.bss_size} bss); relocations {linked.relocs_applied}; "
        f"{sum(hist.values())} instructions decode as ISA_A/B/C+EMAC: {dict(sorted(hist.items()))}"
    )
    for n, v in sorted(linked.symbols.items(), key=lambda kv: kv[1]):
        if v >= a.base:
            print(f"  {v:#010x} {n}")
    if a.object:
        a.object.write_text(json.dumps(obj, indent=1))
    if a.bin:
        a.bin.write_bytes(linked.image)


if __name__ == "__main__":
    main()
