#!/usr/bin/env python3
"""SHARC+ semantics worklist: how much of a region actually lifts to p-code.

    uv run python -m tools.sharc_dev.sharc_worklist [--image dt2-1.16] [--min-depth N]
        [--function LO:HI] --out-json OUT.json --out-md OUT.md

Reproduces the methodology behind the original out/sharc-semantics/
worklist.json/worklist.md (see that file's header and HANDOVER-2026-09-21-
sharc-dataflow.md): sweep the aligned instruction stream
(tools/sharc_dev/sharcflow.py aligned()), lift each instruction with the currently
INSTALLED/in-tree SHARC_VISA language (tools/sharc_dev/sharcpcode.py load_context/
lift_one), and report, per form and image-wide, the share of instructions
that get at least one real p-code op. A handful of forms are deliberately
left with an empty body forever (Type21a: architecturally fixed NOP;
Type9a_abs/Type9b_abs with merged field b==1: register-indirect CALL, no
static target -- matches Ghidra's own default call fallthrough) and count
as "has semantics" despite zero ops. `--function LO:HI` (short-word
addresses, inclusive..exclusive) additionally reports the same table
restricted to that span."""

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

from tools.sharc_dev import (  # noqa: E402
    sharcflow,  # noqa: E402
    sharcpcode,  # noqa: E402
)

REPO = os.path.dirname(os.path.dirname(HERE))

DELIBERATELY_EMPTY_NOTE = [
    "21a (hardware nop; genuinely zero effect)",
    "9a_abs/9b_abs with b=1 (register-indirect CALL; deliberately no p-code, "
    "matches Ghidra default fallthrough for a call)",
]


def is_deliberately_empty(insn):
    if insn.type_name == "21a":
        return True
    if insn.type_name in ("9a_abs", "9b_abs"):
        return insn.fields.get("b") == 1
    return False


def sweep(ctx, data, base_sw, min_depth):
    """-> [(sw, form, has_ops, deliberately_empty, undecoded)] for every
    aligned instruction in `data`."""
    rows = sharcflow.aligned(data, min_depth)
    out = []
    for off, insn in rows:
        sw = base_sw + off // 2
        length, ops = sharcpcode.lift_one(
            ctx, data[off : off + sharcpcode.MAX_INSN_BYTES], 2 * sw
        )
        if length is None:
            out.append((sw, insn.type_name, False, False, True))
            continue
        out.append((sw, insn.type_name, bool(ops), is_deliberately_empty(insn), False))
    return out


def build_table(rows):
    per_form = {}
    for _sw, form, has_ops, deliberately_empty, undecoded in rows:
        rec = per_form.setdefault(
            form,
            {"count": 0, "yes": 0, "no": 0, "undecoded": 0, "deliberately_empty": 0},
        )
        rec["count"] += 1
        if undecoded:
            rec["undecoded"] += 1
        elif has_ops or deliberately_empty:
            rec["yes"] += 1
            rec["deliberately_empty"] += deliberately_empty
        else:
            rec["no"] += 1
    total = sum(r["count"] for r in per_form.values())
    ordered = sorted(per_form.items(), key=lambda kv: -kv[1]["count"])
    table, cumulative = [], 0
    for form, rec in ordered:
        cumulative += rec["count"]
        decodable = rec["count"] - rec["undecoded"]
        if rec["yes"] == 0:
            has_semantics = False
        elif rec["yes"] == decodable:
            has_semantics = True
        else:
            has_semantics = "partial"
        table.append(
            {
                "form": form,
                "count": rec["count"],
                "has_semantics": has_semantics,
                "yes_fraction": round(rec["yes"] / rec["count"], 4)
                if rec["count"]
                else 0,
                "yes": rec["yes"],
                "no": rec["no"],
                "undecoded": rec["undecoded"],
                "deliberately_empty": rec["deliberately_empty"],
                "share": rec["count"] / total if total else 0,
                "cumulative_share": cumulative / total if total else 0,
            }
        )
    return table, total


def markdown_table(rows, limit=None):
    lines = [
        "| form | count | has semantics? | share | cumulative share |",
        "|---|---:|---|---:|---:|",
    ]
    for row in rows[:limit] if limit else rows:
        if row["undecoded"]:
            status = "undecoded"
        elif row["yes"] == 0:
            status = "no"
        elif row["no"] == 0:
            status = "yes"
        else:
            status = f"partial ({row['yes']}/{row['count']} = {row['yes'] / row['count']:.0%})"
        lines.append(
            f"| {row['form']} | {row['count']} | {status} | {row['share']:.2%} | "
            f"{row['cumulative_share']:.2%} |"
        )
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--image", default="dt2-1.16", choices=list(sharcpcode.IMAGES))
    ap.add_argument("--min-depth", type=int, default=8)
    ap.add_argument(
        "--function", help="LO:HI short-word addresses (inclusive..exclusive)"
    )
    ap.add_argument("--out-json", required=True)
    ap.add_argument("--out-md", required=True)
    args = ap.parse_args(argv)

    spec = sharcpcode.IMAGES[args.image]
    region = os.path.join(REPO, spec["region"])
    with open(region, "rb") as f:
        data = f.read()
    region_sha256 = sharcpcode.sha256_bytes(data)

    compiler = sharcpcode.find_sleigh(prefer_ghidra=True)
    lint, ldefs = sharcpcode.build_language(
        sharcpcode.LANG_DIR,
        os.path.join(os.path.dirname(args.out_json), "lang"),
        compiler,
    )
    if lint["compile"]["returncode"] != 0:
        raise SystemExit("the installed slaspec does not compile: %s" % lint)
    ctx = sharcpcode.load_context(ldefs)

    rows = sweep(ctx, data, spec["base_sw"], args.min_depth)
    table, total = build_table(rows)
    undecoded_total = sum(r["undecoded"] for r in table)

    record = {
        "image": args.image,
        "region": spec["region"],
        "region_sha256": region_sha256,
        "base_sw": hex(spec["base_sw"]),
        "min_depth": args.min_depth,
        "total_instructions": total,
        "distinct_forms": len(table),
        "undecoded_total": undecoded_total,
        "rule": {
            "has_semantics_yes": "pypcode ctx.translate() decodes the instruction and yields "
            ">=1 p-code op (other than IMARK), OR the instruction is one of the "
            "deliberately-empty forms below.",
            "has_semantics_no": "pypcode decodes the instruction but yields zero p-code ops, "
            "and it is not one of the deliberately-empty forms.",
            "undecoded": "pypcode raises BadDataError or UnimplError translating the "
            "instruction (language and tools/sharc_dev/sharc_disasm.py disagree about what is here); "
            "tracked separately, not counted as having semantics.",
            "deliberately_empty_forms": DELIBERATELY_EMPTY_NOTE,
        },
        "table_image_wide": table,
    }

    md = [
        f"# SHARC+ semantics worklist ({args.image})",
        "",
        f"Region: {spec['region']} (sha256 {region_sha256}), base_sw {hex(spec['base_sw'])}, "
        f"aligned min_depth {args.min_depth}",
        "",
        f"Total instructions swept: {total}, distinct forms: {len(table)}, "
        f"undecoded by language: {undecoded_total}",
        "",
        "## Image-wide, top 25 forms by count",
        "",
        markdown_table(table, limit=25),
    ]

    if args.function:
        lo_s, hi_s = args.function.split(":")
        lo, hi = int(lo_s, 0), int(hi_s, 0)
        fn_rows = [r for r in rows if lo <= r[0] < hi]
        fn_table, fn_total = build_table(fn_rows)
        record["function"] = {
            "lo_sw": hex(lo),
            "hi_sw": hex(hi),
            "total_instructions": fn_total,
            "distinct_forms": len(fn_table),
        }
        record["table_function"] = fn_table
        md += [
            "",
            f"## Function {hex(lo)}..{hex(hi)} ({fn_total} instructions), all forms",
            "",
            markdown_table(fn_table),
        ]

    os.makedirs(os.path.dirname(os.path.abspath(args.out_json)), exist_ok=True)
    with open(args.out_json, "w") as f:
        json.dump(record, f, indent=2)
    with open(args.out_md, "w") as f:
        f.write("\n".join(md) + "\n")
    print("wrote %s and %s" % (args.out_json, args.out_md), file=sys.stderr)
    print(
        "image-wide: %d/%d instructions have semantics"
        % (sum(r["yes"] for r in table), total),
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
