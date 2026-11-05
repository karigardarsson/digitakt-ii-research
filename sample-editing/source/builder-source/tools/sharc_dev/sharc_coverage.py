"""Semantic coverage of tools/sharc_dev/sharc_trace.py over a SHARC program database.

Answers "which real instructions would the tracer refuse to execute?" from
the database alone, without running a trace:

- form: the instruction's form has no handler in sharc_core.forms.FORMS, so
  _execute stops with "unsupported form".
- provisional: the form's decode confidence is 'uncertain', so _execute
  stops unless the form is passed as a provisional form.
- compute: the compute field raises in sharc_trace._compute or
  _shift_immediate (probed with every UREG = 1.0f).

Other stops are not listed (SIMD-sensitive registers, RTI, circular
modify, long-word access, unresolved predicates): they depend on state.

    uv run python -m tools.sharc_dev.sharc_coverage dt2-1.16 --root 0x1c2b24
    uv run python -m tools.sharc_dev.sharc_coverage dt2-1.16 --all-roots --json

Also compares the two decode paths this project keeps in sync (see
docs/findings/05-sharc-isa-and-decoding.md, "One decode path"):
tools/sharc_dev/sharcdb/'s whole-image successor-confidence walk (the `insn` table)
against tools/sharc_dev/sharc_core/sequencer.decode_at()'s single-PC decode, for every
aligned instruction:

    uv run python -m tools.sharc_dev.sharc_coverage dt2-1.16 --compare-decode-at
"""

from __future__ import annotations

import argparse
import collections
import json
import sys

import networkx as nx  # noqa: E402

from tools.sharc_dev import (
    sharc,  # noqa: E402
    sharc_trace,  # noqa: E402
)
from tools.sharc_dev.sharc_core.forms import FORMS  # noqa: E402
from tools.sharc_dev.sharc_core.sequencer import decode_at  # noqa: E402

ONE = sharc_trace.Const(0x3F800000)
SHIFT_IMMEDIATE_FORMS = ("6b_shiftimm", "6a_mem")
SHORT_COMPUTE_FORMS = ("2c",)


def handled_forms():
    """Form names with a handler in sharc_core.forms.FORMS."""
    return set(FORMS)


def function_set(img, root=None, all_roots=False):
    """Function entries in scope: reach from ROOT over call and jump edges,
    the database's reach table for ALL_ROOTS, or None for the whole image."""
    if all_roots:
        return {
            r[0]
            for r in img.sql(
                "SELECT DISTINCT function_sw FROM reach WHERE image=?", img.name
            )
        }
    if root is None:
        return None
    graph = nx.DiGraph()
    graph.add_edges_from(
        img.sql(
            "SELECT DISTINCT from_function, to_function FROM edges WHERE image=? "
            "AND from_function IS NOT NULL AND to_function IS NOT NULL "
            "AND kind IN ('call', 'jump', 'cond_jump')",
            img.name,
        )
    )
    graph.add_node(root)
    return nx.descendants(graph, root) | {root}


def probe_compute(form, fields):
    """None when the compute field decodes, else the tracer's error text."""
    values = {code: ONE for code in range(256)}
    try:
        if form in SHIFT_IMMEDIATE_FORMS:
            sharc_trace._shift_immediate(fields, values)
            return None
        if form in SHORT_COMPUTE_FORMS:
            sharc_trace._compute(fields, True, values)
            return None
        if "compute" in fields and "compute[22:16]" not in fields:
            fields = dict(fields)
            fields["compute[22:16]"] = fields["compute"] >> 16
            fields["compute[15:0]"] = fields["compute"] & 0xFFFF
        if "compute[22:16]" not in fields:
            return None
        sharc_trace._compute(fields, False, values, {}, approx_recips=True)
    except (
        Exception
    ) as error:  # the tracer signals unsupported encodings with ValueError
        return str(error)
    return None


def coverage(img, functions=None):
    """Gap rows {kind, form, detail, count, example_sw} for real (aligned)
    instructions in FUNCTIONS (None = whole image), most frequent first."""
    handled = handled_forms()
    gaps = collections.OrderedDict()
    total = 0
    rows = img.sql(
        "SELECT sw, form, fields, confidence, function_sw FROM insn WHERE image=? AND aligned=1",
        img.name,
    )
    for sw, form, fields, confidence, function_sw in rows:
        if functions is not None and function_sw not in functions:
            continue
        total += 1
        if form not in handled:
            key = ("form", form, "unsupported form")
        elif confidence != "confident":
            key = ("provisional", form, "decode confidence %s" % confidence)
        else:
            detail = probe_compute(form, json.loads(fields or "{}"))
            if detail is None:
                continue
            key = ("compute", form, detail)
        entry = gaps.setdefault(
            key,
            {
                "kind": key[0],
                "form": key[1],
                "detail": key[2],
                "count": 0,
                "example_sw": "0x%x" % sw,
            },
        )
        entry["count"] += 1
    ordered = sorted(
        gaps.values(), key=lambda g: (-g["count"], g["kind"], g["form"], g["detail"])
    )
    return {
        "image": img.name,
        "instructions": total,
        "gap_instructions": sum(g["count"] for g in ordered),
        "gaps": ordered,
    }


def _raw_le_bytes(raw, width):
    """The little-endian byte sequence a WIDTH-byte, MSB-aligned raw
    instruction value occupies in memory: 16-bit little-endian words, most
    significant short word first (tools/sharc_dev/sharc_disasm.py's module docstring;
    the same packing tools/sharc_dev/sharc_isa.py's frame_of()/DecodedInstruction.raw
    use)."""
    words = width // 2
    out = bytearray()
    for i in range(words):
        out += ((raw >> (16 * (words - 1 - i))) & 0xFFFF).to_bytes(2, "little")
    return bytes(out)


def compare_decode_at(img, example_cap=25):
    """For every aligned instruction in IMG, compare tools/sharc_dev/sharcdb/'s own
    decode (the `insn` table row: width, form, fields -- built by
    tools/sharc_dev/sharcimm.py's successor-confidence walk over one code block's
    range, read through LoadedMemory since DB_VERSION v13) against
    tools/sharc_dev/sharc_core/sequencer.decode_at() at the same short-word PC, run
    against this image's own LoadedMemory (the merged, last-write-wins
    final boot image; no database access at decode time).
    -> {image, total, stale_bytes, mismatches, by_kind, by_key, examples}:

    - stale_bytes: rows skipped because LoadedMemory's bytes at that PC
      already differ from the database's own recorded `raw` bytes for that
      row -- not a decode disagreement, since the two paths are not even
      decoding the same bytes. Before DB_VERSION v13 this was seen on every
      image (dt2-1.16/1.15C's loader block 1, sw 0x120203-0x12161b: a 'code'
      loader block whose whole byte range a later block in the same boot
      stream overwrites, mostly with a large FILL of zeros, before the
      image LoadedMemory models is reached, so the database's per-block
      scan decoded bytes that never persist into the image the emulator
      -- and decode_at -- actually runs from; DN2 1.11/1.10E additionally
      had this on block 57, from a too-small L2_BYTE_LIMIT, not a real
      overwrite). v13 fixed both: block 1 is no longer a scanned code block
      on any image (docs/findings/05-sharc-isa-and-decoding.md, "One decode
      path") and every code block's `insn` rows are built from LoadedMemory
      directly, so this should now report 0 stale_bytes on all four images
      -- kept here as a permanent check, not a one-off measurement, since a
      future code-block addition could reintroduce the same class of bug.
    - by_kind (bytes-agreeing rows only): counts of "width" (decode_at chose
      a different instruction length), "form" (same width, different form)
      and "fields" (same width and form, different decoded field values)
      mismatches -- a row is classified by the first of these that differs.
    - by_key: one row per (db_form, decode_at_form, db_width,
      decode_at_width) combination, most frequent first.
    """
    mem = img._mem()
    rows = img.sql(
        "SELECT sw, width, raw, form, fields FROM insn WHERE image=? AND aligned=1",
        img.name,
    )
    total = len(rows)
    by_key = collections.Counter()
    by_kind = collections.Counter()
    stale_bytes = 0
    examples = []
    for sw, db_width, db_raw_hex, db_form, db_fields_json in rows:
        if db_width and db_raw_hex is not None:
            want = _raw_le_bytes(int(db_raw_hex, 16), db_width)
            if mem.read_sw(sw, db_width) != want:
                stale_bytes += 1
                continue
        insn = decode_at(mem, None, sw)
        at_width, at_form = insn.length_bytes, insn.type_name
        db_fields = json.loads(db_fields_json or "{}")
        if at_width == db_width and at_form == db_form and insn.fields == db_fields:
            continue
        if at_width != db_width:
            kind = "width"
        elif at_form != db_form:
            kind = "form"
        else:
            kind = "fields"
        by_kind[kind] += 1
        by_key[(db_form, at_form, db_width, at_width)] += 1
        if len(examples) < example_cap:
            examples.append(
                {
                    "sw": "0x%x" % sw,
                    "kind": kind,
                    "db_form": db_form,
                    "decode_at_form": at_form,
                    "db_width": db_width,
                    "decode_at_width": at_width,
                }
            )
    return {
        "image": img.name,
        "total": total,
        "stale_bytes": stale_bytes,
        "mismatches": sum(by_kind.values()),
        "by_kind": dict(by_kind),
        "by_key": [
            {
                "db_form": k[0],
                "decode_at_form": k[1],
                "db_width": k[2],
                "decode_at_width": k[3],
                "count": v,
            }
            for k, v in sorted(by_key.items(), key=lambda kv: -kv[1])
        ],
        "examples": examples,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("image", help="database name, e.g. dt2-1.16")
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument(
        "--root", type=lambda s: int(s, 0), help="function reach from this entry sw"
    )
    scope.add_argument(
        "--all-roots", action="store_true", help="the database's reach table"
    )
    parser.add_argument(
        "--kind", choices=("form", "provisional", "compute"), help="show one kind only"
    )
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--fail", action="store_true", help="exit 1 when any gap remains"
    )
    parser.add_argument(
        "--compare-decode-at",
        action="store_true",
        help="compare sharc_core.sequencer.decode_at() against the database's own "
        "decode for every aligned instruction, instead of the tracer-coverage report",
    )
    args = parser.parse_args(argv)

    img = sharc.load(args.image)

    if args.compare_decode_at:
        result = compare_decode_at(img)
        if args.json:
            print(json.dumps(result, indent=1))
        else:
            print(
                "%s: %d aligned instructions, %d stale-bytes (skipped), "
                "%d decode_at mismatches"
                % (
                    result["image"],
                    result["total"],
                    result["stale_bytes"],
                    result["mismatches"],
                )
            )
            for row in result["by_key"]:
                print(
                    "%6d  db=%-14s decode_at=%-14s db_width=%-4s decode_at_width=%-4s"
                    % (
                        row["count"],
                        row["db_form"],
                        row["decode_at_form"],
                        row["db_width"],
                        row["decode_at_width"],
                    )
                )
        return 1 if args.fail and result["mismatches"] else 0

    result = coverage(img, function_set(img, args.root, args.all_roots))
    if args.kind:
        result["gaps"] = [g for g in result["gaps"] if g["kind"] == args.kind]
        result["gap_instructions"] = sum(g["count"] for g in result["gaps"])
    if args.json:
        print(json.dumps(result, indent=1))
    else:
        print(
            "%s: %d instructions, %d not executable, %d distinct gaps"
            % (
                result["image"],
                result["instructions"],
                result["gap_instructions"],
                len(result["gaps"]),
            )
        )
        for g in result["gaps"]:
            print(
                "%6d  %-11s %-12s %-10s %s"
                % (g["count"], g["kind"], g["form"], g["example_sw"], g["detail"])
            )
    return 1 if args.fail and result["gaps"] else 0


if __name__ == "__main__":
    sys.exit(main())
