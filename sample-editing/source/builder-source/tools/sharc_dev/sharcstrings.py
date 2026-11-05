#!/usr/bin/env python3
"""Map printable strings in a bounded DSP loader stream."""

import argparse
import json
import re
import sqlite3
import sys

from tools.sharc_dev import sharcldr

DEFAULT_MIN_LENGTH = 5
MAX_LENGTH = 512
MAX_LIMIT = 10000
TABLE_TEXT_LIMIT = 72


def _printable_runs(data, min_length, max_length):
    """Yield (file_offset, text) for complete printable runs in bounds."""
    start = None
    for offset, value in enumerate(data):
        if 0x20 <= value <= 0x7E:
            if start is None:
                start = offset
            continue
        if start is not None:
            length = offset - start
            if min_length <= length <= max_length:
                yield start, data[start:offset].decode("ascii")
            start = None
    if start is not None:
        length = len(data) - start
        if min_length <= length <= max_length:
            yield start, data[start:].decode("ascii")


def _containing_block(blocks, file_offset, length):
    """Return the one non-FILL payload block containing the whole range."""
    end = file_offset + length
    for block in blocks:
        if block["fill"]:
            continue
        begin = block["payload_offset"]
        if begin <= file_offset and end <= begin + block["payload_len"]:
            return block
    return None


def _short_word_range(byte_address, length):
    """Return inclusive short-word bounds for even byte positions in a range."""
    first = byte_address + (byte_address & 1)
    last = byte_address + length - 1
    if last & 1:
        last -= 1
    if first > last:
        return None, None
    return sharcldr.byte_to_sw(first), sharcldr.byte_to_sw(last)


def _is_shadowed(blocks, block_index, byte_address, length):
    end = byte_address + length
    for later in blocks[block_index + 1 :]:
        if later["fill"]:
            continue
        later_start = later["target_address"]
        later_end = later_start + later["payload_len"]
        if later_start < end and byte_address < later_end:
            return True
    return False


def map_strings(
    data,
    blocks,
    min_length=DEFAULT_MIN_LENGTH,
    max_length=MAX_LENGTH,
    patterns=(),
    include_shadowed=False,
):
    """Return mapped printable runs, excluding overwritten runs by default."""
    result = []
    for file_offset, text in _printable_runs(data, min_length, max_length):
        if patterns and not any(pattern.search(text) for pattern in patterns):
            continue
        block = _containing_block(blocks, file_offset, len(text))
        if block is None:
            continue
        byte_address = block["target_address"] + file_offset - block["payload_offset"]
        short_start, short_end = _short_word_range(byte_address, len(text))
        shadowed = _is_shadowed(blocks, blocks.index(block), byte_address, len(text))
        if shadowed and not include_shadowed:
            continue
        result.append(
            {
                "file_offset": file_offset,
                "length": len(text),
                "text": text,
                "block_index": block["index"],
                "core": block["core"],
                "loaded_byte_address": byte_address,
                "short_word_address": sharcldr.byte_to_sw(byte_address),
                "short_word_start": short_start,
                "short_word_end": short_end,
                "shadowed": shadowed,
            }
        )
    return result


def _required_columns(db, table, columns):
    statements = {
        "refs": "PRAGMA table_info('refs')",
        "insn": "PRAGMA table_info('insn')",
        "functions": "PRAGMA table_info('functions')",
    }
    found = {row[1] for row in db.execute(statements[table])}
    if not set(columns) <= found:
        raise ValueError(
            "SQLite database has missing or incompatible %s schema" % table
        )


def correlate_refs(rows, database):
    """Attach references and their containing function metadata to each row."""
    db = None
    try:
        db = sqlite3.connect(database)
        _required_columns(db, "refs", ("from_sw", "to_sw", "type"))
        _required_columns(db, "insn", ("sw", "function_sw"))
        _required_columns(db, "functions", ("sw", "name"))
        query = (
            "SELECT refs.from_sw, refs.type, insn.function_sw, functions.name "
            "FROM refs "
            "LEFT JOIN insn ON insn.sw = refs.from_sw "
            "LEFT JOIN functions ON functions.sw = insn.function_sw "
            "WHERE refs.to_sw >= ? AND refs.to_sw <= ? "
            "ORDER BY refs.to_sw, refs.from_sw, refs.type"
        )
        for row in rows:
            start, end = row["short_word_start"], row["short_word_end"]
            if start is None:
                row["refs"] = []
                continue
            row["refs"] = [
                {
                    "from_pc": from_sw,
                    "type": ref_type,
                    "function_address": function_sw,
                    "function_name": name,
                }
                for from_sw, ref_type, function_sw, name in db.execute(
                    query, (start, end)
                )
            ]
    except sqlite3.Error as exc:
        raise ValueError("cannot read SQLite database: %s" % exc) from exc
    finally:
        if db is not None:
            db.close()
    return rows


def _display_text(text):
    if len(text) <= TABLE_TEXT_LIMIT:
        return text
    return text[: TABLE_TEXT_LIMIT - 3] + "..."


def _table(rows, include_refs):
    for row in rows:
        sw = (
            "-"
            if row["short_word_address"] is None
            else "0x%x" % row["short_word_address"]
        )
        line = "off=0x%x block=%d core=%s byte=0x%x sw=%s len=%d%s %s" % (
            row["file_offset"],
            row["block_index"],
            row["core"],
            row["loaded_byte_address"],
            sw,
            row["length"],
            " shadowed" if row["shadowed"] else "",
            _display_text(row["text"]),
        )
        print(line)
        if include_refs:
            for ref in row["refs"]:
                function = (
                    "-"
                    if ref["function_address"] is None
                    else "0x%x" % ref["function_address"]
                )
                print(
                    "  ref pc=0x%x type=%s function=%s %s"
                    % (
                        ref["from_pc"],
                        ref["type"],
                        function,
                        ref["function_name"] or "-",
                    )
                )


def _positive(value):
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a positive integer") from exc
    if number <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return number


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("blob", metavar="BLOB")
    parser.add_argument("--grep", action="append", default=[], metavar="REGEX")
    parser.add_argument("--min-length", type=_positive, default=DEFAULT_MIN_LENGTH)
    parser.add_argument("--max-length", type=_positive, default=MAX_LENGTH)
    parser.add_argument("--limit", type=_positive, default=MAX_LIMIT)
    parser.add_argument("--sqlite", metavar="DB")
    parser.add_argument("--include-shadowed", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.max_length > MAX_LENGTH:
        parser.error("--max-length must not exceed %d" % MAX_LENGTH)
    if args.min_length > args.max_length:
        parser.error("--min-length must not exceed --max-length")
    if args.limit > MAX_LIMIT:
        parser.error("--limit must not exceed %d" % MAX_LIMIT)
    patterns = []
    for expression in args.grep:
        try:
            patterns.append(re.compile(expression, re.IGNORECASE))
        except re.error as exc:
            parser.error("invalid --grep regex %r: %s" % (expression, exc))
    try:
        with open(args.blob, "rb") as source:
            data = source.read()
    except OSError as exc:
        parser.error("cannot read blob: %s" % exc)
    blocks = sharcldr.parse_blocks(data)
    if not any(not block["fill"] and block["payload_len"] for block in blocks):
        parser.error("blob contains no valid non-FILL loader blocks")
    rows = map_strings(
        data, blocks, args.min_length, args.max_length, patterns, args.include_shadowed
    )[: args.limit]
    try:
        if args.sqlite:
            correlate_refs(rows, args.sqlite)
    except ValueError as exc:
        parser.error(str(exc))
    if args.json:
        print(json.dumps(rows, sort_keys=True, separators=(",", ":")))
    else:
        _table(rows, bool(args.sqlite))
    return 0


if __name__ == "__main__":
    sys.exit(main())
