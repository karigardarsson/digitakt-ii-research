#!/usr/bin/env python3
"""Module sources -> `.dko.json` objects (patchkit's relocatable object format).

    uv run python -m tools.patchkit.pk_build build modules/fx/master-gain [--check]
    uv run python -m tools.patchkit.pk_build build-all [--check]

For every `[[object]]` of a module's `module.toml` that lists `sources`, the sources are compiled by the
pinned toolchain (SHARC+: Selache `selas` for `.s`, `selcc` for `.c`, merged into one object; ColdFire: m68k-elf
gcc/as, tools/patchkit/pk_link.py), turned into the
canonical object JSON (sorted keys, integers only; format in packages/patchkit/src/dko.rs) and written to the
object's `path`. Every instruction is re-decoded by our own decoders (SHARC: tools/patchkit/pk_sharc.py `verify`;
ColdFire: tools/patchkit/pk_link.py `check_isa`), so a toolchain encoding bug cannot reach an image.

`--check` writes nothing and fails when a committed object differs from a fresh build (objects are
committed; the check keeps them current with `src/`).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
import tomllib
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent

from tools.patchkit import (  # noqa: E402
    pk_link,  # noqa: E402
    pk_sharc,  # noqa: E402
)

FORMAT = 1
SHARC_TYPES = {pk_sharc.R_ABS24: "sharc.abs24", pk_sharc.R_ABS32: "sharc.abs32"}
CF_TYPES = {"32": "cf.32", "PC32": "cf.pc32", "PC16": "cf.pc16", "PC8": "cf.pc8"}
CF_PLT = {"PLT32": "PC32", "PLT16": "PC16", "PLT8": "PC8"}


class BuildError(RuntimeError):
    pass


def canonical(obj: dict) -> str:
    return json.dumps(obj, sort_keys=True, indent=1, ensure_ascii=True) + "\n"


def _tu_lines(sources: list[Path], texts: list[str], message: str) -> str:
    """Rewrite `line N:` (a line of the concatenated translation unit) as `file:line:`."""
    starts = []
    first = 1
    for p, t in zip(sources, texts, strict=True):
        starts.append((first, p))
        first += t.count("\n") + 1  # the "\n" joining the next file

    def sub(m: re.Match) -> str:
        n = int(m.group(1))
        start, path = max((s for s in starts if s[0] <= n), key=lambda s: s[0])
        return f"{path}:{n - start + 1}:"

    return re.sub(r"\bline (\d+):", sub, message)


def dsp_object(sources: list[Path]) -> pk_sharc.SharcObject:
    """A DSP object from `.s` and `.c` sources: the `.s` files, concatenated in order, are one translation unit
    (pk_sharc.assemble); each `.c` file is compiled by selcc with its own directory on the include path
    (pk_sharc.compile_c); the pieces are merged in that order, assembly first (pk_sharc.merge)."""
    asm = [p for p in sources if p.suffix == ".s"]
    objs = []
    if asm:
        texts = [p.read_text() for p in asm]
        try:
            objs.append(pk_sharc.assemble("\n".join(texts)))
        except pk_sharc.PkSharcError as e:
            raise pk_sharc.PkSharcError(_tu_lines(asm, texts, str(e))) from e
    for p in sources:
        if p.suffix == ".c":
            try:
                objs.append(pk_sharc.compile_c(p.read_text(), include_dirs=[p.parent]))
            except pk_sharc.PkSharcError as e:
                raise pk_sharc.PkSharcError(f"{p}: {e}") from e
        elif p.suffix != ".s":
            raise BuildError(f"{p}: a DSP object takes .s and .c sources")
    merged = pk_sharc.merge(objs)
    check_zero_runs(merged)
    return merged


ZERO_RUN_LIMIT = 4096


def check_zero_runs(obj: pk_sharc.SharcObject, limit: int = ZERO_RUN_LIMIT) -> None:
    """Fail closed: a loaded data section (not `.PKBSS`, not an explicit `.PKDATA`) with a run of more than `limit` zero bytes
    is zeros in the DSP boot stream (selcc emits a zero-initialised static as loaded data: a per-voice delay memory of 16 KB
    cost 206 KB of the stream's 1,048,575-byte cap). Reserve such memory with `.PKBSS name, bytes;` (the program writes it
    before it reads it) and reach it through an extern."""
    for name, (blob, _align, bss) in obj.data.items():
        if bss or name.startswith("pkdata_"):
            continue
        m = re.search(b"\\x00{%d,}" % (limit + 1), blob)
        if m:
            raise BuildError(
                f"data section {name}: a run of {m.end() - m.start()} zero bytes at offset {m.start()} is loaded into the boot "
                f"stream (limit {limit}); reserve big zero memory with `.PKBSS name, bytes;` in assembly instead of a C static"
            )


def sharc_object(sources: list[Path]) -> dict:
    """`.s` (one translation unit) and `.c` sources to the canonical object JSON."""
    obj = dsp_object(sources)
    # ISA check: link at a placeholder base with placeholder externs; verify() re-decodes every instruction
    # and reads every relocated field back.
    undefined = sorted({r.symbol for r in obj.relocs} - set(obj.symbols))
    externs = {n: 0xB90000 + 0x40 * i for i, n in enumerate(undefined)}
    for i, name in enumerate(obj.data_symbols):
        externs[name] = 0x20090000 + 0x10 * i
    linked = pk_sharc.link(obj, 0xB90000, externs)
    sections = []
    code_boot = pk_sharc.sel.swap_parcels(obj.section)
    sections.append(
        {
            "name": pk_sharc.SECTION,
            "kind": "text",
            "align": 8,
            "size": len(code_boot),
            "hex": code_boot.hex(),
        }
    )
    for name, (blob, align, bss) in obj.data.items():
        sections.append(
            {
                "name": name,
                "kind": "bss" if bss else "data",
                "align": align,
                "size": len(blob),
                "hex": blob.hex(),
            }
        )
    symbols = []
    for name, (off, glob) in sorted(obj.symbols.items()):
        symbols.append(
            {"name": name, "section": pk_sharc.SECTION, "value": off, "global": glob}
        )
    for name, (sec, off, glob) in sorted(obj.data_symbols.items()):
        symbols.append({"name": name, "section": sec, "value": off, "global": glob})
    for name in undefined:
        if name not in obj.data_symbols:
            symbols.append({"name": name, "section": None, "value": 0, "global": True})
    relocs = [
        {
            "section": pk_sharc.SECTION,
            "offset": r.parcel,
            "type": SHARC_TYPES[r.rtype],
            "symbol": r.symbol,
            "addend": r.addend,
        }
        for r in obj.relocs
    ]
    return {
        "format": FORMAT,
        "arch": "sharc",
        "source_sha256": obj.source_sha256,
        "toolchain": {**obj.toolchain, "instructions_redecoded": str(linked.checked)},
        "sections": sections,
        "symbols": symbols,
        "relocs": relocs,
    }


def cf_object(sources: list[Path]) -> dict:
    base = 0x47E8B000
    # Compile once to learn the undefined symbols, then place them near the base so PC16/PC8 reach.
    with tempfile.TemporaryDirectory(prefix="pk-build-") as d:
        objs = []
        for i, src in enumerate(sources):
            o = Path(d) / f"{i}_{src.stem}.o"
            pk_link.compile_source(src, o)
            objs.append(o)
        merged = objs[0]
        if len(objs) > 1:
            merged = Path(d) / "all.o"
            pk_link.merge_objects(objs, merged)
        proto = pk_link.object_from_elf(merged.read_bytes(), sources)
    undefined = [s["name"] for s in proto["symbols"] if s["section"] is None]
    symmap = {n: base + 0x100 + 8 * i for i, n in enumerate(undefined)}
    linked = pk_link.link(proto, base, symmap)
    hist = pk_link.check_isa(linked)
    kind_map = {"text": "text", "rodata": "data", "data": "data", "bss": "bss"}
    sections = []
    for s in proto["sections"]:
        sections.append(
            {
                "name": s["name"],
                "kind": kind_map[s["kind"]],
                "align": s["align"],
                "size": s["size"],
                "hex": s["hex"],
            }
        )
    symbols = [
        {
            "name": s["name"],
            "section": s["section"],
            "value": s["value"],
            "global": s["global"],
        }
        for s in proto["symbols"]
    ]
    seen = {s["name"] for s in symbols}
    relocs = []
    for r in proto["relocs"]:
        t = CF_PLT.get(r["type"], r["type"])
        if t not in CF_TYPES:
            raise BuildError(
                f"relocation type {r['type']} in {r['in']} is not supported (use 32-bit absolute or PC-relative calls)"
            )
        if "to_section" in r:
            name = ".sec." + r["to_section"]
            if name not in seen:
                symbols.append(
                    {
                        "name": name,
                        "section": r["to_section"],
                        "value": 0,
                        "global": False,
                    }
                )
                seen.add(name)
            sym = name
        else:
            sym = r["to_symbol"]
        relocs.append(
            {
                "section": r["in"],
                "offset": r["offset"],
                "type": CF_TYPES[t],
                "symbol": sym,
                "addend": r["addend"],
            }
        )
    return {
        "format": FORMAT,
        "arch": "cf",
        "source_sha256": proto["source_sha256"],
        "toolchain": {
            **proto["toolchain"],
            "instructions_redecoded": str(sum(hist.values())),
        },
        "sections": sections,
        "symbols": sorted(symbols, key=lambda s: s["name"]),
        "relocs": relocs,
    }


def build_module(moddir: Path, check: bool) -> list[str]:
    manifest = tomllib.loads((moddir / "module.toml").read_text())
    messages = []
    for o in manifest.get("object", []):
        srcs = o.get("sources")
        if not srcs:
            continue
        paths = [moddir / s for s in srcs]
        kinds = {p.suffix for p in paths}
        if kinds <= {".s", ".c"} and any("dsp" in Path(s).parts for s in srcs):
            obj = sharc_object(paths)
        else:
            obj = cf_object(paths)
        text = canonical(obj)
        out = moddir / o["path"]
        if check:
            if not out.exists() or out.read_text() != text:
                raise BuildError(
                    f"{out} is stale: uv run python -m tools.patchkit.pk_build build {moddir}"
                )
            messages.append(f"{out}: current")
        else:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(text)
            n = obj["toolchain"]["instructions_redecoded"]
            messages.append(f"{out}: {n} instructions re-decoded OK")
    return messages


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("module", type=Path)
    b.add_argument("--check", action="store_true")
    c = sub.add_parser("build-all")
    c.add_argument("--check", action="store_true")
    c.add_argument("--root", type=Path, default=ROOT / "modules")
    argv = sys.argv[1:]
    # `mise run patch:build [-- MODULE] [--check]`: no subcommand builds every module, a path builds that one
    if not argv or argv[0].startswith("-"):
        argv = ["build-all", *argv]
    elif argv[0] not in ("build", "build-all"):
        argv = ["build", *argv]
    a = ap.parse_args(argv)
    dirs = (
        [a.module]
        if a.cmd == "build"
        else sorted(p.parent for p in a.root.rglob("module.toml"))
    )
    try:
        for d in dirs:
            for m in build_module(d, a.check):
                print(m)
    except (BuildError, pk_sharc.PkSharcError, pk_link.PkLinkError) as e:
        print(f"pk_build: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
