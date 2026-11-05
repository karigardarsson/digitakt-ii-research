#!/usr/bin/env python3
"""One round of the gated-entry search for an AOT core.

With the AOT core on and a model gate that sends a block entry to the
interpreter (a pending bank swap after a MODE1 write, a core timer about to
tick), the interpreter runs on through the rest of that block: a generated
region has entries only at its block starts, so the next instruction is not
one. `coupled-run --profile-execution` therefore records, besides the PCs where
an interpreter run starts, the PC after the first instruction of a run that
began at a block entry (packages/emulator/sharc/src/lib.rs, `diag_step`). Making those PCs
block starts lets the run end after one instruction. The new starts can be gated
the same way, so the search repeats until no frequent new PC appears.

    uv run python -m tools.sharc_dev.aot_entry_iter --base DIR --out DIR2 [FILE:SCENARIO ...]

DIR holds prof-fx, prof-fx.entries and prof-fx.trans (as tools/sharc_dev/aot_fxprof.py
writes them); DIR2 gets the merged profile of the scenarios run with the core
built from DIR's entries, and the entries not in DIR are printed with their
counts. Then regenerate the core with DIR2's entries and rebuild
(docs/design/aot-mask-free.md section 8). Output is firmware-derived: keep it
under out/.
"""

from __future__ import annotations

import argparse
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
from tools.sharc_dev import aot_fxprof  # noqa: E402

# The DN2 scenarios of the fxprof profile (out/native/dn2-audio/PROVENANCE.txt).
SCENARIOS = [
    "tests/scenarios/dn2.json:trig1-taps",
    "tests/scenarios/dn2.json:kb-chord16",
    *(
        f"tests/scenarios/dn2-trackfx.json:tf-{fx}-127"
        for fx in ("br", "srr", "over", "del", "rev", "chr")
    ),
    "tests/scenarios/dn2-sendfx.json:sf-del-fb-d127",
    "tests/scenarios/dn2-sendfx.json:sf-rev-decay-u20",
    "tests/scenarios/dn2-sendfx.json:sf-chr-depth-d127",
    "tests/scenarios/dn2-sendfx.json:sf-mst-thr0",
]


# The DT2 scenario (one-sample card, tools/emulator/dt2_sample_check.py).
DT2_SCENARIOS = ["tests/scenarios/dt2.json:dt2-sample-trig"]


def read_entries(path: pathlib.Path) -> dict[str, int]:
    """prof-fx.entries: `pc count` lines."""
    rows = (line.split() for line in path.read_text().splitlines())
    return {r[0]: int(r[1]) for r in rows if len(r) == 2}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("names", nargs="*", metavar="FILE:SCENARIO")
    p.add_argument("--base", type=pathlib.Path, required=True)
    p.add_argument("--out", type=pathlib.Path, required=True)
    p.add_argument("--device", default="dn2", choices=["dn2", "dt2"])
    p.add_argument("--min-count", type=int, default=1000)
    a = p.parse_args(argv)
    names = a.names or (DT2_SCENARIOS if a.device == "dt2" else SCENARIOS)
    dt2 = ["--device", "dt2", "--dt2-card"] if a.device == "dt2" else []
    status = aot_fxprof.main(
        [
            "--out", str(a.out), *dt2,
            "--base-cov", str(a.base / "prof-fx"),
            "--base-entries", str(a.base / "prof-fx.entries"),
            "--base-trans", str(a.base / "prof-fx.trans"),
            *names,
        ]
    )  # fmt: skip
    if status:
        return status
    before = read_entries(a.base / "prof-fx.entries")
    after = read_entries(a.out / "prof-fx.entries")
    new = sorted(
        ((pc, n) for pc, n in after.items() if pc not in before), key=lambda x: -x[1]
    )
    frequent = [x for x in new if x[1] >= a.min_count]
    print(f"{len(new)} new entries, {len(frequent)} with count >= {a.min_count}")
    for pc, n in new[:20]:
        print(f"  {pc} {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
