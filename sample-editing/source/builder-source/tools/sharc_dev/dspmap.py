"""Map the instructions that reference the ColdFire-to-SHARC frame tables.

    uv run python -m tools.sharc_dev.dspmap --image out/sections/dt2-1.16/section_3_MAIN_OS.bin \\
        --dump out/ghidra/dt2-1.16-emac [--json out/maps/dt2-1.16-dspmap.json]

The image selects its tools/sharc_dev/framelink.py profile by SHA-256, and the dump must
be of the same image. The regions are the profile's frame tables, its
frame-gate variables and its pacing counter. Sites come from the dump's data
references (instructions inside functions) and from a raw sweep of the image
with tools/coldfire/refscan.py (absolute operands only, but also outside functions).
An indexed access through a base loaded earlier shows only at the load. A
site only Ghidra reports can be wrong: on 1.15C it attributes the driver's
`move.w a0,(a1,d0.l*4)` to 0x80003340, but a1 holds 0x80001bc0 there.

Each site gets its region, row and offset in the row, the instruction, its
function and that function's callers two levels up. A site whose function or
callers include a `*::updateMirror` method lists them. The JSON has the sites
and a per-function summary.
"""

import argparse
import json
import os
import sqlite3

from tools.coldfire.disasm import disasm  # noqa: E402
from tools.coldfire.refscan import scan  # noqa: E402
from tools.coldfire.vtcheck import containing  # noqa: E402
from tools.sharc_dev import framelink  # noqa: E402

BASE = 0x40000400


def hx(value):
    return "0x%08x" % value


def regions(prof):
    """-> sorted [(lo, hi, name, row size)] for the tables and variables."""
    out = [
        (base, base + size * rows, name, size)
        for base, size, rows, name in prof["tables"]
    ]
    out += [(prof[name], prof[name] + 4, name, 4) for name in framelink.VARIABLES]
    return sorted(out)


def locate(regs, addr):
    """-> (name, row, offset in the row) of the region holding addr, or None."""
    for lo, hi, name, size in regs:
        if lo <= addr < hi:
            return name, (addr - lo) // size, (addr - lo) % size
    return None


def caller_tree(func, names, callers, depth):
    """-> [{'entry', 'name', 'callers'}] for the callers of func, depth levels up."""
    if func is None or depth == 0:
        return []
    return [
        {
            "entry": hx(c),
            "name": names.get(c),
            "callers": caller_tree(c, names, callers, depth - 1),
        }
        for c in sorted(callers.get(func, ()))
    ]


def mirror_names(name, tree):
    """-> sorted updateMirror method names among name and its caller tree."""
    found = set()
    if name and "updateMirror" in name:
        found.add(name)
    for node in tree:
        found.update(mirror_names(node["name"], node["callers"]))
    return sorted(found)


def instruction(image, base, pc):
    """-> (hex bytes, text) of the instruction at pc."""
    for _, hexbytes, mnemonic, ops in disasm(image, base, pc, pc + 22):
        return hexbytes, ("%s %s" % (mnemonic, ops)).strip()
    return None, None


def load_dump(path):
    """-> (manifest, names, ranges, callers, data refs) from a tools/coldfire/ghidradump.py output."""
    with open(os.path.join(path, "manifest.json")) as f:
        manifest = json.load(f)
    db = sqlite3.connect(os.path.join(path, "xrefs.sqlite"))
    try:
        names = dict(db.execute("SELECT entry, name FROM functions"))
        ranges = sorted(db.execute("SELECT lo, hi, func FROM function_ranges"))
        callers = {}
        for frm, to in db.execute(
            "SELECT from_func, to_func FROM calls WHERE to_func IS NOT NULL"
        ):
            if frm != to:
                callers.setdefault(to, set()).add(frm)
        refs = list(db.execute("SELECT site, func, to_addr, kind FROM data_refs"))
    finally:
        db.close()
    return manifest, names, ranges, callers, refs


def collect(image, base, regs, names, ranges, callers, refs):
    """-> site records, sorted by site and target."""
    sites = {}

    def record(pc, target):
        if (pc, target) not in sites:
            region, row, offset = locate(regs, target)
            sites[(pc, target)] = {
                "region": region,
                "row": row,
                "offset": offset,
                "kinds": set(),
                "sources": set(),
            }
        return sites[(pc, target)]

    for site, _, to, kind in refs:
        if locate(regs, to):
            r = record(site, to)
            r["kinds"].add(kind)
            r["sources"].add("ghidra")
    lo, hi = regs[0][0], max(r[1] for r in regs)
    for pc, _, _, _, target in scan(image, base, base, base + len(image), lo, hi)[0]:
        if locate(regs, target):
            record(pc, target)["sources"].add("raw")
    out = []
    for pc, target in sorted(sites):
        r = sites[(pc, target)]
        func = containing(ranges, pc)
        tree = caller_tree(func, names, callers, 2)
        hexbytes, text = instruction(image, base, pc)
        out.append(
            {
                "site": hx(pc),
                "target": hx(target),
                "region": r["region"],
                "row": r["row"],
                "offset": r["offset"],
                "instruction": text,
                "bytes": hexbytes,
                "kinds": sorted(r["kinds"]),
                "sources": sorted(r["sources"]),
                "function": {"entry": hx(func), "name": names.get(func)}
                if func is not None
                else None,
                "callers": tree,
                "update_mirror": mirror_names(names.get(func), tree),
            }
        )
    return out


def summarize(sites):
    """-> one record per function (None for sites outside functions)."""
    funcs = {}
    for s in sites:
        entry = s["function"]["entry"] if s["function"] else None
        f = funcs.setdefault(
            entry,
            {
                "entry": entry,
                "name": s["function"]["name"] if s["function"] else None,
                "sites": 0,
                "regions": set(),
                "update_mirror": set(),
            },
        )
        f["sites"] += 1
        f["regions"].add(s["region"])
        f["update_mirror"].update(s["update_mirror"])
    return [
        dict(f, regions=sorted(f["regions"]), update_mirror=sorted(f["update_mirror"]))
        for _, f in sorted(funcs.items(), key=lambda kv: kv[0] or "")
    ]


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--image", required=True, help="MAIN OS image")
    p.add_argument(
        "--dump",
        required=True,
        help="tools/coldfire/ghidradump.py output of the same image",
    )
    p.add_argument("--base", type=lambda s: int(s, 0), default=BASE)
    p.add_argument("--json", help="write the sites and the per-function summary here")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    image_sha256, prof = framelink.profile_for(args.image)
    manifest, names, ranges, callers, refs = load_dump(args.dump)
    if manifest.get("image_sha256") != image_sha256:
        raise SystemExit(
            "%s is a dump of image %s, not of %s (%s)"
            % (args.dump, manifest.get("image_sha256"), args.image, image_sha256)
        )
    with open(args.image, "rb") as f:
        image = f.read()
    regs = regions(prof)
    sites = collect(image, args.base, regs, names, ranges, callers, refs)
    functions = summarize(sites)
    print("%s: %d sites in %d functions" % (prof["name"], len(sites), len(functions)))
    for lo, hi, name, _ in regs:
        inside = [s for s in sites if s["region"] == name]
        entries = {s["function"]["entry"] if s["function"] else None for s in inside}
        print(
            "  %-12s %s-%s  %3d sites in %3d functions"
            % (name, hx(lo), hx(hi), len(inside), len(entries))
        )
    print("functions:")
    for f in functions:
        mirror = (
            ("  updateMirror: " + ", ".join(f["update_mirror"]))
            if f["update_mirror"]
            else ""
        )
        print(
            "  %-10s %-44s %3d  %s%s"
            % (
                f["entry"] or "(outside)",
                (f["name"] or "")[:44],
                f["sites"],
                ",".join(f["regions"]),
                mirror,
            )
        )
    if args.json:
        os.makedirs(os.path.dirname(os.path.abspath(args.json)), exist_ok=True)
        with open(args.json, "w") as f:
            json.dump(
                {
                    "tool": "tools/sharc_dev/dspmap.py",
                    "image": args.image,
                    "image_sha256": image_sha256,
                    "profile": prof["name"],
                    "dump": args.dump,
                    "regions": [
                        {"name": name, "lo": hx(lo), "hi": hx(hi), "row_size": size}
                        for lo, hi, name, size in regs
                    ],
                    "sites": sites,
                    "functions": functions,
                },
                f,
                indent=1,
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
