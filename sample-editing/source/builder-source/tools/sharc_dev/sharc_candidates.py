#!/usr/bin/env python3
"""Rank exact Type19a address-adjust hypotheses from a sharcpcode SQLite dump.

This reads decoded metadata only; it never opens the firmware image or exposes
any decoder raw value.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

MEMORY_FORMS = {"15b", "4a", "4b", "3a", "3b", "3c", "6a_mem", "16a", "16b"}


def _field(fields, name):
    """Get an exact field, accepting an unsuffixed spelling where appropriate."""
    if name in fields:
        return fields[name]
    if "[" not in name:
        for key, value in fields.items():
            if key == name or key.startswith(name + "["):
                return value
    raise ValueError("missing field " + name)


def _i_index(form, fields):
    if form == "3c":
        return _field(fields, "dmi[2:0]")
    return _field(fields, "i") + (8 if _field(fields, "g") else 0)


def _writer(form, fields):
    """Return an I register definitely written by the implemented clear forms."""
    if form == "19a":
        return _field(fields, "idis") + (8 if _field(fields, "g") else 0)
    if form in {"17a", "17b"}:
        code = _field(fields, "ureg")
    elif form in {"5a_move", "5b_move"}:
        code = _field(fields, "dstureg")
    elif form == "15b" and _field(fields, "d") == 0:
        code = _field(fields, "ureg")
    else:
        return None
    return code - 16 if 16 <= code <= 31 else None


def _event(kind, row):
    return {
        "kind": kind,
        "pc": row["sw"],
        "pc_hex": hex(row["sw"]),
        "form": row["form"],
    }


def _parse_row(row):
    try:
        fields = json.loads(row["fields"])
    except (TypeError, json.JSONDecodeError) as error:
        raise ValueError(
            "bad decoder fields JSON at 0x%x: %s" % (row["sw"], error)
        ) from error
    if not isinstance(fields, dict):
        raise ValueError("bad decoder fields JSON at 0x%x: expected object" % row["sw"])
    return fields


def _slice(rows, pos, source, destination, window):
    before = rows[max(0, pos - window) : pos]
    writer = {"kind": "no-writer"}
    for row in reversed(before):
        fields = _parse_row(row)
        if row["kind"] != "confident":
            writer = _event("uncertain-barrier", row)
            break
        if row["flow"] != "FALL_THROUGH":
            writer = _event("control-barrier", row)
            break
        if _writer(row["form"], fields) == source:
            writer = _event("writer", row)
            break

    fate = {"kind": "no-use-in-window"}
    i7_seen = False
    for row in rows[pos + 1 : pos + 1 + window]:
        fields = _parse_row(row)
        if row["kind"] != "confident":
            fate = _event("uncertain-barrier", row)
            break
        if row["flow"] != "FALL_THROUGH":
            fate = _event("control-barrier", row)
            break
        if row["form"] in MEMORY_FORMS and _i_index(row["form"], fields) == destination:
            fate = _event("consumed", row)
            i7_seen = destination == 7
            break
        if _writer(row["form"], fields) == destination:
            fate = _event("overwritten", row)
            break
    return writer, fate, i7_seen


def _rows(db):
    # Explicit columns deliberately exclude decoder.raw and insn.raw.
    query = """
        SELECT d.sw, d.form, d.kind, d.fields, i.flow, i.function_sw,
               f.sw AS function_address, f.name AS function_name,
               COALESCE(f.in_main, i.in_main, 0) AS in_main
        FROM decoder d
        LEFT JOIN insn i ON i.sw = d.sw
        LEFT JOIN functions f ON f.sw = i.function_sw
        WHERE d.aligned = 1
        ORDER BY d.sw
    """
    try:
        return db.execute(query, ()).fetchall()
    except sqlite3.Error as error:
        raise ValueError(
            "missing or incompatible decoder/insn/functions schema: " + str(error)
        ) from error


def candidates(path, offset=0x94, word_bytes=2, window=64):
    if offset <= 0 or word_bytes <= 0 or window <= 0:
        raise ValueError("offset, word-bytes, and window must be positive")
    try:
        db = sqlite3.connect(path)
    except sqlite3.Error as error:
        raise ValueError("cannot open database: " + str(error)) from error
    db.row_factory = sqlite3.Row
    try:
        rows = _rows(db)
        result = []
        for _pos, row in enumerate(rows):
            if row["form"] != "19a" or row["kind"] != "confident":
                continue
            fields = _parse_row(row)
            try:
                raw = (_field(fields, "data[31:16]") << 16) | _field(
                    fields, "data[15:0]"
                )
                displacement = raw - (1 << 32) if raw & (1 << 31) else raw
                source = _field(fields, "is") + (8 if _field(fields, "g") else 0)
                destination = _field(fields, "idis") + (8 if _field(fields, "g") else 0)
            except (TypeError, ValueError) as error:
                raise ValueError(
                    "invalid Type19a fields at 0x%x: %s" % (row["sw"], error)
                ) from error
            unit = "byte" if displacement == offset else None
            if (
                unit is None
                and offset % word_bytes == 0
                and displacement == offset // word_bytes
            ):
                unit = "word"
            if unit is None or displacement <= 0:
                continue
            function_sw = row["function_sw"]
            local = [r for r in rows if r["function_sw"] == function_sw]
            local_pos = next(i for i, r in enumerate(local) if r["sw"] == row["sw"])
            writer, fate, i7_seen = _slice(
                local, local_pos, source, destination, window
            )
            stack_risk = source in {6, 7} or destination in {6, 7} or i7_seen
            fate_rank = {
                "consumed": 0,
                "uncertain-barrier": 1,
                "control-barrier": 1,
                "no-use-in-window": 1,
                "overwritten": 2,
            }[fate["kind"]]
            result.append(
                {
                    "address": row["sw"],
                    "address_hex": hex(row["sw"]),
                    "displacement": displacement,
                    "unit": unit,
                    "source_i": source,
                    "destination_i": destination,
                    "function_address": row["function_address"],
                    "function_address_hex": None
                    if row["function_address"] is None
                    else hex(row["function_address"]),
                    "function_name": row["function_name"],
                    "in_main": bool(row["in_main"]),
                    "source_writer": writer,
                    "forward_fate": fate,
                    "stack_risk": stack_risk,
                    "suggested_tracer": "hypothesis: --start 0x%x --set I%d=@receive_%s"
                    % (row["sw"], source, "words" if unit == "word" else "bytes"),
                    "_rank": (1 if stack_risk else 0, fate_rank, row["sw"]),
                }
            )
        result.sort(key=lambda item: item["_rank"])
        for item in result:
            del item["_rank"]
        return result
    finally:
        db.close()


def _table(items):
    print(
        "address     unit src->dst function             fate                 writer       stack"
    )
    for c in items:
        function = c["function_name"] or (c["function_address_hex"] or "-")
        writer = c["source_writer"]["kind"]
        print(
            "%-11s %-4s I%d->I%d %-20s %-20s %-12s %s"
            % (
                c["address_hex"],
                c["unit"],
                c["source_i"],
                c["destination_i"],
                function,
                c["forward_fate"]["kind"],
                writer,
                "yes" if c["stack_risk"] else "no",
            )
        )


def _positive(value):
    try:
        parsed = int(value, 0)
    except ValueError as error:
        raise argparse.ArgumentTypeError("expected integer: " + value) from error
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return parsed


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("db", type=Path)
    parser.add_argument("--offset", type=_positive, default=0x94)
    parser.add_argument("--word-bytes", type=_positive, default=2)
    parser.add_argument("--window", type=_positive, default=64)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        found = candidates(args.db, args.offset, args.word_bytes, args.window)
    except ValueError as error:
        parser.error(str(error))
    if args.json:
        print(json.dumps(found, sort_keys=True))
    else:
        _table(found)
    return 0


if __name__ == "__main__":
    sys.exit(main())
