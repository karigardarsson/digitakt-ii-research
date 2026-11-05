#!/usr/bin/env python3
"""Assemble a VISA snippet with Selache and cross-check it against our own decoder.

Selache (https://github.com/js216/selache) reads VISA 16-bit parcels
big-endian; the DT2 image and tools/sharc_dev/sharc_disasm.py read them little-endian
(the boot-stream convention -- see docs/findings/05-sharc-isa-and-decoding.md,
"selache encodes and decodes DT2 SHARC+ code, once parcel order is fixed").
So a snippet built with selas has to have every parcel byte-swapped before
our decoder can read it, and the resulting listing has to be compared to
Selache's own disassembly of the *unswapped* object, not to the source.

This assembles ONE snippet section with selas, reads its raw bytes straight
out of the ELF object (no dependency on any particular seldump text format),
and prints our decoder's listing next to Selache's, aligned by byte offset,
so a human can spot a disagreement immediately. It does not resolve
relocations (an unresolved branch target reads as zero); that is a
by-hand, per-snippet step, not something this script attempts.

Usage:
    uv run python selasm.py snippet.s
    uv run python selasm.py snippet.s --selache-dir out/selache-public \
        --section seg_pmco
"""

from __future__ import annotations

import argparse
from pathlib import Path

HERE = Path(__file__).resolve().parent

from tools.sharc_dev.sharc_selache import (  # noqa: E402
    DEFAULT_PROCESSOR,
    DEFAULT_SECTION,
    SelacheError,
    SelacheOracle,
)

DEFAULT_SELACHE_DIR = str(HERE.parents[1] / "out" / "selache-public")


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("source", help="VISA .s file to assemble")
    parser.add_argument(
        "--selache-dir",
        default=DEFAULT_SELACHE_DIR,
        help=f"Selache checkout with target/release built (default: {DEFAULT_SELACHE_DIR})",
    )
    parser.add_argument(
        "--proc",
        default=DEFAULT_PROCESSOR,
        help=f"selas -proc target (default: {DEFAULT_PROCESSOR})",
    )
    parser.add_argument(
        "--section",
        default=DEFAULT_SECTION,
        help=f"ELF section to compare (default: {DEFAULT_SECTION})",
    )
    args = parser.parse_args()

    try:
        oracle = SelacheOracle.open(args.selache_dir)
        source = Path(args.source).read_text()
        run = oracle.assemble_text(source, processor=args.proc, section=args.section)
    except (OSError, SelacheError) as exc:
        raise SystemExit(str(exc)) from exc

    print(
        f"== Selache's own disassembly ({args.section}, {len(run.external_bytes)} bytes) =="
    )
    print(run.listing)
    print("== our decoder, after the parcel-order swap ==")
    for item in run.comparison.instructions:
        status = "extent-ok" if item.extent_agrees else "EXTENT-DIFF"
        forms = item.native_form_id or "/".join(item.native_candidates) or "unknown"
        print(
            f"  {item.external.parcel_address:#010x}  {forms:<16s} {status:<11s} "
            f"{dict(item.native_fields)}"
        )


if __name__ == "__main__":
    main()
