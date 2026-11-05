"""Compare our SHARC+ disassembler with the sharc-spec decoder over a code region.

    uv run python -m tools.sharc_dev.sharccompare REGION.bin [--start OFFSET] [--ours DIR]
        [--pairs N] [--json OUT]

REGION.bin is raw DSP code, 16-bit little-endian words. Both decoders sweep
it linearly from --start, one instruction after another, stepping one word
past a word they cannot decode:

- ours: tools/sharc_dev/sharc_disasm.py, disassemble(data, offset, count=1). --ours
  names another directory holding a sharc_disasm.py and its
  sharc_visa_tables.py, such as an older version taken with `git show`;
- spec: tools/sharc_dev/sharcspec/sharc_decode.py, linear() over decode_table.json.

It reports, per decoder, how far a walk gets before its first unknown word
and the share of unknown words in the sweep; and, at the offsets where both
sweeps decode an instruction, how often they agree on the length and on the
form, with the most common disagreeing form pairs. Form names are compared
without the "Type" prefix, and a name without a suffix, such as `8a` in an
older table, agrees with `8a_abs` and `8a_rel`.
"""

import argparse
import collections
import importlib
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def spec_name(name):
    """'Type5b (move)' -> '5b_move'."""
    return re.sub(r"\s*\((\w+)\)", r"_\1", re.sub(r"^Type", "", name))


def names_agree(ours, spec):
    if ours is None or spec is None:
        return False
    if ours == spec:
        return True
    return ("_" not in ours and spec.split("_")[0] == ours) or (
        "_" not in spec and ours.split("_")[0] == spec
    )


def load_ours(directory=None):
    """-> the sharc_disasm module from directory (default: the current tree)."""
    if not directory:
        from tools.sharc_dev import sharc_disasm

        return sharc_disasm
    directory = os.path.abspath(directory)
    for name in ("sharc_disasm", "sharc_visa_tables"):
        sys.modules.pop(name, None)
    sys.path.insert(0, directory)
    try:
        return importlib.import_module("sharc_disasm")
    finally:
        sys.path.remove(directory)


def sweep_ours(disasm, data, start):
    """-> {offset: (length in bytes or None, form name or None)}."""
    out, offset = {}, start
    while offset + 2 <= len(data):
        rec = next(iter(disasm.disassemble(data, offset, count=1)), None)
        if rec is None:
            break
        if rec.kind == "unknown" or not rec.length_bytes:
            out[offset] = (None, None)
            offset += 2
        else:
            out[offset] = (rec.length_bytes, rec.type_name)
            offset += rec.length_bytes
    return out


def sweep_spec(data, start):
    """-> {offset: (length in bytes or None, form name or None)}."""
    from tools.sharc_dev.sharcspec.sharc_decode import Decoder, linear

    out = {}
    for pos, n, form, _, _ in linear(data, start, len(data), Decoder(mode="visa")):
        out[pos] = (2 * n, spec_name(form["name"])) if form else (None, None)
    return out


def summary(sweep, data, start):
    decoded = sum(1 for n, _ in sweep.values() if n)
    unknown = sum(1 for n, _ in sweep.values() if not n)
    first = min((o for o, (n, _) in sweep.items() if not n), default=len(data))
    return {
        "instructions": decoded,
        "unknown_words": unknown,
        "unknown_share": round(unknown / (decoded + unknown), 5)
        if decoded + unknown
        else 0.0,
        "first_unknown": first,
        "walk_share": round((first - start) / (len(data) - start), 5)
        if len(data) > start
        else 0.0,
    }


def compare(ours, spec, pairs):
    both = [o for o in sorted(set(ours) & set(spec)) if ours[o][0] and spec[o][0]]
    same_length = sum(1 for o in both if ours[o][0] == spec[o][0])
    same_form = sum(1 for o in both if names_agree(ours[o][1], spec[o][1]))
    differ = collections.Counter(
        (ours[o][1], spec[o][1])
        for o in both
        if not names_agree(ours[o][1], spec[o][1])
    )
    return {
        "common_instructions": len(both),
        "spec_instructions_also_ours": round(
            len(both) / max(1, sum(1 for n, _ in spec.values() if n)), 5
        ),
        "same_length": same_length,
        "same_form": same_form,
        "unknown_only_ours": sum(
            1 for o in set(ours) & set(spec) if not ours[o][0] and spec[o][0]
        ),
        "unknown_only_spec": sum(
            1 for o in set(ours) & set(spec) if ours[o][0] and not spec[o][0]
        ),
        "form_pairs": [
            {"ours": a, "spec": b, "count": n}
            for (a, b), n in differ.most_common(pairs)
        ],
    }


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("region")
    p.add_argument("--start", type=lambda s: int(s, 0), default=0, help="byte offset")
    p.add_argument(
        "--ours",
        help="directory with flat sharc_disasm.py and sharc_visa_tables.py, "
        "i.e. a tools/ directory from before the package layout",
    )
    p.add_argument(
        "--pairs", type=int, default=15, help="disagreeing form pairs to list"
    )
    p.add_argument("--json")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    with open(args.region, "rb") as f:
        data = f.read()
    disasm = load_ours(args.ours)
    walk = disasm.walk_and_report(data, args.start)
    ours = sweep_ours(disasm, data, args.start)
    spec = sweep_spec(data, args.start)
    result = {
        "region": args.region,
        "bytes": len(data),
        "start": args.start,
        "ours_dir": os.path.abspath(args.ours or HERE),
        "ours": dict(
            summary(ours, data, args.start),
            walk_stop=walk.end_offset,
            walk_reason=walk.stopped_reason,
        ),
        "spec": summary(spec, data, args.start),
        "agreement": compare(ours, spec, args.pairs),
    }
    for side in ("ours", "spec"):
        s = result[side]
        print(
            "%-4s %6d instructions, %5d unknown words (%.2f%%), first unknown at 0x%x (%.1f%% in)"
            % (
                side,
                s["instructions"],
                s["unknown_words"],
                100 * s["unknown_share"],
                s["first_unknown"],
                100 * s["walk_share"],
            )
        )
    print("ours walk stops at 0x%x: %s" % (walk.end_offset, walk.stopped_reason))
    a = result["agreement"]
    print(
        "%d offsets decoded by both (%.2f%% of spec instructions): same length %d, same form %d"
        % (
            a["common_instructions"],
            100 * a["spec_instructions_also_ours"],
            a["same_length"],
            a["same_form"],
        )
    )
    print(
        "unknown only in ours %d, only in spec %d"
        % (a["unknown_only_ours"], a["unknown_only_spec"])
    )
    for pair in a["form_pairs"]:
        print("  %6d  ours %-12s spec %s" % (pair["count"], pair["ours"], pair["spec"]))
    if args.json:
        os.makedirs(os.path.dirname(os.path.abspath(args.json)), exist_ok=True)
        with open(args.json, "w") as f:
            json.dump(result, f, indent=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
