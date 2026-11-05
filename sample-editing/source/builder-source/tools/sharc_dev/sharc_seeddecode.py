#!/usr/bin/env python3
"""Compare exact Ghidra instruction starts with loader-backed VISA decoding.

Seeds come only from the SQLite ``insn`` table produced by sharcpcode.py;
this tool never linearly sweeps memory.  ``--function SW`` selects Ghidra
instructions assigned to that function and still obeys ``--scope`` (unless
``--scope all`` is explicitly supplied).
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from typing import Any

HERE = os.path.dirname(os.path.abspath(__file__))

from tools.sharc_dev.sharc_disasm import decode_loaded_at  # noqa: E402
from tools.sharc_dev.sharcldr import LoadedMemory, sw_to_byte  # noqa: E402


def number(value: str) -> int:
    try:
        return int(value, 0)
    except ValueError as error:
        raise argparse.ArgumentTypeError(
            "expected an integer (for example 0x1c0000)"
        ) from error


def positive(value: str) -> int:
    result = number(value)
    if result <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return result


def _columns(db: sqlite3.Connection, table: str) -> set[str]:
    if table not in ("insn", "functions"):
        raise ValueError("unsupported SQLite table: " + table)
    try:
        return {
            row[1]
            for row in db.execute(
                "PRAGMA table_info(insn)"
                if table == "insn"
                else "PRAGMA table_info(functions)"
            )
        }
    except sqlite3.Error as error:
        raise ValueError("cannot inspect SQLite schema: " + str(error)) from error


def read_seeds(
    db: sqlite3.Connection, args: argparse.Namespace
) -> list[dict[str, Any]]:
    insn = _columns(db, "insn")
    required = {"sw", "length", "mnemonic", "flow", "function_sw", "in_main"}
    missing = sorted(required - insn)
    if missing:
        raise ValueError(
            "SQLite insn table lacks required columns: " + ", ".join(missing)
        )
    functions = _columns(db, "functions")
    have_functions = {"sw", "name", "in_main"} <= functions
    query = [
        "SELECT i.sw AS sw, i.length AS length, i.mnemonic AS mnemonic, i.flow AS flow,",
        "i.function_sw AS function_sw, i.in_main AS in_main,",
        (
            "f.name AS function_name, f.in_main AS function_in_main"
            if have_functions
            else "NULL AS function_name, NULL AS function_in_main"
        ),
        "FROM insn AS i",
    ]
    if have_functions:
        query.append("LEFT JOIN functions AS f ON f.sw = i.function_sw")
    where, values = [], []
    if args.scope == "main":
        where.append("i.in_main = 1")
    elif args.scope == "non-main":
        where.append("COALESCE(i.in_main, 0) = 0")
    if args.function is not None:
        where.append("i.function_sw = ?")
        values.append(args.function)
    if args.start is not None:
        where.append("i.sw >= ?")
        values.append(args.start)
    if args.end is not None:
        where.append("i.sw < ?")
        values.append(args.end)
    if where:
        query.append("WHERE " + " AND ".join(where))
    query.append("ORDER BY i.sw")
    if args.limit is not None:
        query.append("LIMIT ?")
        values.append(args.limit)
    try:
        return [dict(row) for row in db.execute(" ".join(query), values)]
    except sqlite3.Error as error:
        raise ValueError(
            "cannot read SQLite instruction seeds: " + str(error)
        ) from error


def decode_rows(
    memory: LoadedMemory, seeds: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    rows = []
    for seed in seeds:
        pc = seed["sw"]
        source = memory.source_block(sw_to_byte(pc))
        decoded = decode_loaded_at(memory, pc)
        if source is None:
            status = "unmapped"
        elif decoded.kind == "unknown":
            status = "unknown"
        elif decoded.length_bytes != seed["length"]:
            status = "length-mismatch"
        elif decoded.kind == "uncertain":
            status = "uncertain"
        else:
            status = "confirmed"
        rows.append(
            {
                "seed": {
                    "pc_sw": pc,
                    "ghidra_length": seed["length"],
                    "ghidra_mnemonic": seed["mnemonic"],
                    "ghidra_flow": seed["flow"],
                    "function_sw": seed["function_sw"],
                    "function_name": seed["function_name"],
                    "function_in_main": seed["function_in_main"],
                    "in_main": seed["in_main"],
                },
                "loader_source_block_index": source,
                "decoder": {
                    "form": decoded.type_name,
                    "kind": decoded.kind,
                    "length": decoded.length_bytes,
                    "fields": decoded.fields,
                    "note": decoded.note,
                },
                "status": status,
            }
        )
    return rows


def report(args: argparse.Namespace) -> dict[str, Any]:
    try:
        with open(args.loader_blob, "rb") as fh:
            memory = LoadedMemory.from_stream(fh.read())
    except (OSError, TypeError, ValueError) as error:
        raise ValueError("invalid loader blob: " + str(error)) from error
    if not memory.blocks or not memory.ranges():
        raise ValueError("invalid loader blob: no loaded ranges")
    if not memory.has_final_marker():
        raise ValueError("invalid loader blob: stream ended before a final marker")
    try:
        uri = "file:" + os.path.abspath(args.sqlite_db) + "?mode=ro"
        db = sqlite3.connect(uri, uri=True)
        db.row_factory = sqlite3.Row
    except sqlite3.Error as error:
        raise ValueError(
            "cannot open SQLite database read-only: " + str(error)
        ) from error
    try:
        seeds = read_seeds(db, args)
    finally:
        db.close()
    rows = decode_rows(memory, seeds)
    emitted = [
        row for row in rows if not args.only_problems or row["status"] != "confirmed"
    ]
    return {
        "metadata": {
            "seed_method": "exact Ghidra instruction-start seeds from SQLite insn table; no linear sweep",
            "filters": {
                "scope": args.scope,
                "function_sw": args.function,
                "start_sw": args.start,
                "end_sw": args.end,
                "limit": args.limit,
                "only_problems": args.only_problems,
            },
            "counts": {"selected_seeds": len(seeds), "emitted_rows": len(emitted)},
        },
        "rows": emitted,
    }


def render_human(result: dict[str, Any]) -> str:
    meta = result["metadata"]
    lines = [
        "exact Ghidra instruction-start seeds (no linear sweep): %d selected, %d emitted"
        % (meta["counts"]["selected_seeds"], meta["counts"]["emitted_rows"])
    ]
    for row in result["rows"]:
        seed, decoder = row["seed"], row["decoder"]
        lines.append(
            "%#x %s ghidra=%s/%s decoder=%s/%s %s"
            % (
                seed["pc_sw"],
                row["status"],
                seed["ghidra_length"],
                seed["ghidra_mnemonic"],
                decoder["length"],
                decoder["form"],
                decoder["kind"],
            )
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("loader_blob", metavar="LOADER_BLOB")
    parser.add_argument("sqlite_db", metavar="SQLITE_DB")
    parser.add_argument(
        "--scope",
        choices=("non-main", "main", "all"),
        default="non-main",
        help="Ghidra in_main scope (default: non-main)",
    )
    parser.add_argument(
        "--function",
        type=number,
        metavar="SW",
        help="function entry SW; still obeys --scope unless --scope all",
    )
    parser.add_argument("--start", type=number, metavar="SW")
    parser.add_argument("--end", type=number, metavar="SW", help="exclusive ending SW")
    parser.add_argument(
        "--limit", type=positive, metavar="N", help="maximum selected seeds"
    )
    parser.add_argument("--only-problems", action="store_true")
    parser.add_argument("--json", action="store_true", help="write JSON to stdout")
    args = parser.parse_args(argv)
    if args.start is not None and args.end is not None and args.end < args.start:
        parser.error("--end must not be less than --start")
    try:
        result = report(args)
    except ValueError as error:
        parser.error(str(error))
    print(
        json.dumps(result, indent=2, sort_keys=True)
        if args.json
        else render_human(result)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
