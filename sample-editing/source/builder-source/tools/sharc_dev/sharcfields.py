#!/usr/bin/env python3
"""Per-form field-value distributions over the SHARC+ main programs.

`sharcspec/audit_bits.py` lists the bits a PRM figure prints that
decode_table.json does not fix. This is the mirror image: it lists the bits
decode_table.json declares as FIELDS and asks what values they actually take
in the firmware. A field that takes one value in twenty thousand instructions
is not a field -- it is an opcode bit the figure mis-shaded, and the form is
matching more encoding space than the instruction owns, which lets it swallow
its neighbours.

Where the classic PGR grid (`sharcspec/classic.json`) prints a concrete 0 or 1
for a bit the PRM declares as a field, that disagreement is shown next to the
observed values, so all three sources -- PRM figure, classic grid, firmware --
can be read on one line.

The classic column is matched by name ("Type19a" -> "Type 19a") and the key it
matched is printed, so a wrong match is visible rather than silent.

  uv run python -m tools.sharc_dev.sharcfields
  uv run python -m tools.sharc_dev.sharcfields --all --form Type19a
  uv run python -m tools.sharc_dev.sharcfields --json out/sharc/fields.json

Nothing here imports `sharcspec/build_table.py`: it is a top-level script with
no `if __name__` guard, so importing it rewrites decode_table.json.
"""

import argparse
import json
import os
import sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
SPEC = os.path.join(HERE, "sharcspec")

from tools.sharc_dev import sharcflow  # noqa: E402

# region path relative to the repo root, and its base short-word address.
IMAGES = {
    "dt2-1.16": ("out/sharc/dt2-1.16-main.bin", 0x1C1338),
    "dn2-1.11": ("out/sharc/dn2-1.11-main.bin", 0x1C12E2),
}


def load_forms():
    """decode_table.json as {form name: form dict}.

    Keyed by the table's own name ("Type19a") and also by the short name the
    disassembler reports in `Instruction.type_name` ("19a"), so either spelling
    resolves.
    """
    with open(os.path.join(SPEC, "decode_table.json")) as handle:
        table = json.load(handle)["forms"]
    out = {}
    for form in table:
        out[form["name"]] = form
        short = form["name"][4:] if form["name"].startswith("Type") else form["name"]
        out.setdefault(short, form)
    return out


def load_classic():
    """classic.json patterns as {name: 48-character string}, bit 47 first.

    '0' and '1' are fixed bits, 'x' a field, '.' a cell the grid leaves blank.
    Returns {} when the file is missing or in an unexpected shape: the classic
    column is a cross-check, not a requirement.
    """
    path = os.path.join(SPEC, "classic.json")
    if not os.path.exists(path):
        return {}
    try:
        with open(path) as handle:
            raw = json.load(handle)
    except (OSError, ValueError):
        return {}
    entries = raw
    if isinstance(raw, dict):
        for key in ("tables", "forms", "types"):
            if isinstance(raw.get(key), (list, dict)):
                entries = raw[key]
                break
    items = []
    if isinstance(entries, dict):
        items = list(entries.items())
    elif isinstance(entries, list):
        items = [(e.get("name"), e) for e in entries if isinstance(e, dict)]
    out = {}
    for name, entry in items:
        if not name or not isinstance(entry, dict):
            continue
        pattern = entry.get("pattern")
        if isinstance(pattern, str) and len(pattern) == 48:
            out[name] = pattern
    return out


def form_classic_keys(form):
    """The classic.json tables a form's fixed bits actually came from.

    `build_table.py` records them as `classic_keys`. Guessing from the name
    instead gets a split form's variant wrong -- `Type8a_rel` guesses
    `Type 8a`, where the merge really used `Type 8a #2` -- so the guess is kept
    only as a fallback for a table built before the field existed, and is
    reported as a guess.

    -> ([key], came_from_the_table)
    """
    keys = form.get("classic_keys")
    if keys is not None:
        return list(keys), True
    base, _, suffix = form["name"].partition("_")
    spaced = base.replace("Type", "Type ", 1)
    return [spaced] + ([spaced + " #2"] if suffix else []), False


def classic_verdict(patterns, hi, lo):
    """What the classic grid says about bits hi..lo, across every table the
    form merged: a string, or None.

    This mirrors `build_table.py`'s own rule -- a bit counts as fixed only when
    every variant prints the same digit -- so a bit that selects between the
    variants reads `mixed`, not `fixed`. Reading one variant alone would call
    Type11a's bit 40 a conflict when it is the Type 11a / Type 11a #2 selector.
    """
    if not patterns:
        return None
    chars = []
    for bit in range(hi, lo - 1, -1):
        seen = {p[47 - bit] for p in patterns}
        chars.append(seen.pop() if len(seen) == 1 else "*")
    if all(c in "01" for c in chars):
        return "fixed " + "".join(chars)
    if all(c == "." for c in chars):
        return "blank"
    if all(c == "x" for c in chars):
        return "field"
    return "mixed " + "".join(chars)


def plan_joint(forms, classic):
    """Fields the table declares over bits the classic grid fixes.

    These are the bits where the two sources disagree about whether an encoding
    belongs to the form at all, so the joint value they take in the firmware
    says whether the form is one instruction or several wearing one name.

    -> ({short form name: [label]}, {short form name: (table name, [(label, hi,
    lo, value the classic grid wants)])}).
    """
    labels, expect = {}, {}
    for name, form in forms.items():
        if name != form["name"]:
            continue
        keys, _ = form_classic_keys(form)
        patterns = [classic[k] for k in keys if k in classic]
        if not patterns:
            continue
        picked = []
        for decl in form.get("fields", []):
            verdict = classic_verdict(patterns, decl["hi"], decl["lo"])
            if verdict and verdict.startswith("fixed "):
                picked.append(
                    (decl["label"], decl["hi"], decl["lo"], int(verdict.split()[1], 2))
                )
        if not picked:
            continue
        short = name[4:] if name.startswith("Type") else name
        labels[short] = [p[0] for p in picked]
        expect[short] = (name, picked)
    return labels, expect


def bits_str(values, picked):
    """A joint value as binary groups, one per field, widest bit first."""
    out = []
    for value, (_, hi, lo, _) in zip(values, picked, strict=False):
        out.append("?" if value is None else format(value, "0%db" % (hi - lo + 1)))
    return " ".join(out)


def tally(data, min_depth, joint_labels):
    """-> (per-form counts, {(form, label): Counter(value)}, {(form, label):
    {value: first offset}}, {form: Counter(joint)}, {form: {joint: offset}})."""
    counts = Counter()
    values = defaultdict(Counter)
    first = defaultdict(dict)
    joint = defaultdict(Counter)
    joint_first = defaultdict(dict)
    for off, insn in sharcflow.aligned(data, min_depth):
        form = insn.type_name
        counts[form] += 1
        for label, value in insn.fields.items():
            values[(form, label)][value] += 1
            first[(form, label)].setdefault(value, off)
        labels = joint_labels.get(form)
        if labels:
            key = tuple(insn.fields.get(label) for label in labels)
            joint[form][key] += 1
            joint_first[form].setdefault(key, off)
    return counts, values, first, joint, joint_first


def collect(images, min_depth, joint_labels):
    """Walk every image once. -> {image name: tally result}."""
    out = {}
    for name, path, base_sw in images:
        with open(path, "rb") as handle:
            data = handle.read()
        counts, values, first, joint, joint_first = tally(data, min_depth, joint_labels)
        out[name] = {
            "path": path,
            "base_sw": base_sw,
            "bytes": len(data),
            "counts": counts,
            "values": values,
            "first": first,
            "joint": joint,
            "joint_first": joint_first,
        }
    return out


def show_values(counter, top):
    """The commonest values of a field, as `0x1f x969`."""
    parts = ["0x%x x%d" % (value, n) for value, n in counter.most_common(top)]
    if len(counter) > top:
        parts.append("+%d more" % (len(counter) - top))
    return ", ".join(parts)


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "region", nargs="*", help="main-program image(s); default: both known images"
    )
    ap.add_argument(
        "--base-sw",
        type=lambda s: int(s, 0),
        default=None,
        help="base short-word address for a region given positionally",
    )
    ap.add_argument(
        "--min-depth",
        type=int,
        default=8,
        help="sharcflow.aligned depth filter (default 8)",
    )
    ap.add_argument(
        "--form",
        action="append",
        default=None,
        help="restrict to this form name; repeatable",
    )
    ap.add_argument(
        "--all", action="store_true", help="every field, not only the constant ones"
    )
    ap.add_argument(
        "--top", type=int, default=4, help="values shown per field (default 4)"
    )
    ap.add_argument("--json", default=None, help="write the full tally here")
    args = ap.parse_args()

    if args.region:
        base_sw = args.base_sw if args.base_sw is not None else IMAGES["dt2-1.16"][1]
        images = [(os.path.basename(r), r, base_sw) for r in args.region]
    else:
        images = [(n, os.path.join(ROOT, p), sw) for n, (p, sw) in IMAGES.items()]

    missing = [p for _, p, _ in images if not os.path.exists(p)]
    if missing:
        sys.exit("no such image: " + ", ".join(missing))

    forms = load_forms()
    classic = load_classic()
    joint_labels, joint_expect = plan_joint(forms, classic)
    walked = collect(images, args.min_depth, joint_labels)
    names = [n for n, _, _ in images]

    for name in names:
        w = walked[name]
        print(
            "%s: %d bytes, %d aligned instructions, %d forms"
            % (name, w["bytes"], sum(w["counts"].values()), len(w["counts"]))
        )
    if not classic:
        print("classic.json unreadable or absent: no classic column")
    print()

    rows = []
    wanted = None
    if args.form:
        wanted = set()
        for name in args.form:
            wanted.add(name)
            wanted.add(name[4:] if name.startswith("Type") else "Type" + name)
    total = Counter()
    for name in names:
        total.update(walked[name]["counts"])

    for form_name, _ in total.most_common():
        if wanted is not None and form_name not in wanted:
            continue
        form = forms.get(form_name)
        if form is None:
            continue
        keys, exact = form_classic_keys(form)
        patterns = [classic[k] for k in keys if k in classic]
        matched_key = ", ".join(k for k in keys if k in classic) or None
        if matched_key and not exact:
            matched_key += " (guessed)"
        for decl in form.get("fields", []):
            label, hi, lo = decl["label"], decl["hi"], decl["lo"]
            merged = Counter()
            per_image = {}
            for name in names:
                counter = walked[name]["values"].get((form_name, label), Counter())
                per_image[name] = counter
                merged.update(counter)
            if not merged:
                continue
            verdict = classic_verdict(patterns, hi, lo)
            constant = len(merged) == 1
            disagree = 0
            if verdict and verdict.startswith("fixed "):
                want = int(verdict.split()[1], 2)
                disagree = sum(n for value, n in merged.items() if value != want)
            conflict = bool(
                verdict and verdict.startswith("fixed ") and (constant or disagree)
            )
            examples = {}
            for name in names:
                first = walked[name]["first"].get((form_name, label), {})
                base_sw = walked[name]["base_sw"]
                examples[name] = {
                    "0x%x" % value: "0x%x" % (base_sw + off // 2)
                    for value, off in sorted(first.items())[:8]
                }
            rows.append(
                {
                    "form": form["name"],
                    "field": label,
                    "hi": hi,
                    "lo": lo,
                    "n": total[form_name],
                    "distinct": len(merged),
                    "constant": constant,
                    "classic_key": matched_key,
                    "classic": verdict,
                    "conflict": conflict,
                    "disagree": disagree,
                    "values": {"0x%x" % v: n for v, n in merged.most_common()},
                    "per_image": {
                        name: {"0x%x" % v: n for v, n in counter.most_common()}
                        for name, counter in per_image.items()
                    },
                    "first_sw": examples,
                    "_merged": merged,
                }
            )

    shown = [r for r in rows if args.all or r["constant"] or r["conflict"]]
    header = "%-22s %-16s %-7s %8s %5s  %-34s %s" % (
        "form",
        "field",
        "bits",
        "n",
        "vals",
        "observed",
        "classic",
    )
    print(header)
    print("-" * len(header))
    for r in shown:
        bits = "%d:%d" % (r["hi"], r["lo"]) if r["hi"] != r["lo"] else str(r["hi"])
        note = r["classic"] or "-"
        if r["conflict"]:
            note += "  CONFLICT"
            if r["disagree"]:
                note += " (%d disagree)" % r["disagree"]
        print(
            "%-22s %-16s %-7s %8d %5d  %-34s %s"
            % (
                r["form"],
                r["field"],
                bits,
                r["n"],
                r["distinct"],
                show_values(r["_merged"], args.top),
                note,
            )
        )

    constants = [r for r in rows if r["constant"]]
    conflicts = [r for r in rows if r["conflict"]]
    print()
    print(
        "%d fields over %d forms; %d take one value; %d sit on bits the "
        "classic grid fixes"
        % (len(rows), len({r["form"] for r in rows}), len(constants), len(conflicts))
    )
    if not args.all and (constants or conflicts):
        print("(--all shows every field)")

    if joint_expect:
        print()
        print(
            "joint value of the bits the classic grid fixes and the table leaves free:"
        )
        for short in sorted(joint_expect):
            table_name, picked = joint_expect[short]
            merged = Counter()
            for name in names:
                merged.update(walked[name]["joint"].get(short, Counter()))
            if not merged:
                continue
            spec = ", ".join("%s %d:%d" % (lab, hi, lo) for lab, hi, lo, _ in picked)
            want = tuple(p[3] for p in picked)
            print()
            print(
                "  %s -- %s; the classic grid wants %s"
                % (table_name, spec, bits_str(want, picked))
            )
            for key, n in merged.most_common():
                where = []
                for name in names:
                    off = walked[name]["joint_first"].get(short, {}).get(key)
                    if off is not None:
                        where.append(
                            "%s 0x%x" % (name, walked[name]["base_sw"] + off // 2)
                        )
                print(
                    "    %-16s x%-6d %-11s first at %s"
                    % (
                        bits_str(key, picked),
                        n,
                        "matches" if key == want else "DISAGREES",
                        ", ".join(where),
                    )
                )

    if args.json:
        for r in rows:
            r.pop("_merged", None)
        out = {
            "images": {
                name: {
                    "path": walked[name]["path"],
                    "base_sw": "0x%x" % walked[name]["base_sw"],
                    "bytes": walked[name]["bytes"],
                    "aligned": sum(walked[name]["counts"].values()),
                    "forms": dict(walked[name]["counts"].most_common()),
                }
                for name in names
            },
            "min_depth": args.min_depth,
            "fields": rows,
        }
        os.makedirs(os.path.dirname(os.path.abspath(args.json)), exist_ok=True)
        with open(args.json, "w") as handle:
            json.dump(out, handle, indent=1, sort_keys=True)
        print("wrote %s" % args.json)


if __name__ == "__main__":
    main()
