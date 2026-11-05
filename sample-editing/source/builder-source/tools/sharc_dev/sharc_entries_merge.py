#!/usr/bin/env python3
"""Merge interpreter stretch starts into sharc_rsgen.py's entries and coverage.

The generator turns an --entries PC into a block only when --coverage has a
line for it (the line gives the MODE1 values to specialise on). So a PC that
the interpreter was entered at in a dn2_replay option-26 profile
(PREFIX.entries.tsv, "pc count") and that the generated library has no
BLOCKS entry for needs two things: an entries line and a coverage line
(PREFIX.coverage.tsv, "pc mode1 known count"). Both are added; coverage
lines the base already has for a PC are kept as they are.

    sharc_entries_merge.py --gen GEN --profile PREFIX \\
        --coverage cov6.txt --entries ent9.txt --out-dir DIR [--min-starts N]

writes DIR/cov.txt and DIR/ent.txt, and prints the counts.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

BLOCK_RE = re.compile(r"\(0x([0-9a-f]+), blocks_\d+::b_")


def block_starts(gen: Path) -> set[int]:
    """PCs with a BLOCKS entry in the generated library's image.rs."""
    text = (gen / "image.rs").read_text()
    return {int(x, 16) for x in BLOCK_RE.findall(text)}


def pc_set(path: Path) -> set[int]:
    out = set()
    for line in path.read_text().splitlines():
        parts = line.split()
        if parts:
            out.add(int(parts[0], 0))
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--gen", required=True, help="generated dir whose BLOCKS to keep")
    p.add_argument("--profile", required=True, help="option-26 profile prefix")
    p.add_argument("--coverage", required=True, help="base coverage file")
    p.add_argument("--entries", required=True, help="base entries file")
    p.add_argument("--out-dir", required=True)
    p.add_argument("--min-starts", type=int, default=1)
    a = p.parse_args(argv)

    have = block_starts(Path(a.gen))
    base_entries = Path(a.entries).read_text().splitlines()
    base_cov = Path(a.coverage).read_text().splitlines()
    cov_pcs = pc_set(Path(a.coverage))
    ent_pcs = pc_set(Path(a.entries))

    prof = Path(a.profile)
    starts: dict[int, int] = {}
    for line in Path(f"{prof}.entries.tsv").read_text().splitlines():
        parts = line.split()
        if len(parts) == 2 and int(parts[1]) >= a.min_starts:
            starts[int(parts[0], 0)] = int(parts[1])
    new = {pc: n for pc, n in starts.items() if pc not in have}
    prof_cov: dict[int, list[str]] = {}
    for line in Path(f"{prof}.coverage.tsv").read_text().splitlines():
        parts = line.split()
        if len(parts) == 4 and int(parts[0], 0) in new:
            prof_cov.setdefault(int(parts[0], 0), []).append(line)

    add_ent = [pc for pc in sorted(new) if pc not in ent_pcs]
    add_cov = [pc for pc in sorted(new) if pc not in cov_pcs and pc in prof_cov]
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    ent_lines = base_entries + ["%#x %d" % (pc, new[pc]) for pc in add_ent]
    cov_lines = base_cov + [ln for pc in add_cov for ln in prof_cov[pc]]
    (out / "ent.txt").write_text("\n".join(ent_lines) + "\n")
    (out / "cov.txt").write_text("\n".join(cov_lines) + "\n")
    print(
        "stretch-start PCs %d, without BLOCKS entry %d, entries added %d, "
        "coverage PCs added %d, no profile coverage %d"
        % (
            len(starts),
            len(new),
            len(add_ent),
            len(add_cov),
            sum(1 for pc in new if pc not in prof_cov),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
