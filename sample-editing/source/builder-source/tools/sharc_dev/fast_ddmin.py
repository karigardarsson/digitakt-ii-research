#!/usr/bin/env python3
"""Shrink the set of fast-tier regions that reproduces a DSP halt.

A run-time selected fast tier (`fast-auto:2,0+follow`, ...) halts the DSP
where the interpreter does not. This finds a small set of entry PCs that is
still enough: it logs the entries the run arms (`SHARC_FAST_LOG`), then runs
again with only a subset allowed (`SHARC_FAST_ONLY`) and applies delta
debugging (ddmin) until the halt needs every PC left.

    uv run python -m tools.sharc_dev.fast_ddmin [--config fast-auto:2,0+follow]
        [--scenario idle] [--seconds 0.1] [--match fork]

A run "fails" when its stderr contains `DSP halted` (and --match). The result
is the minimal PC list; run it once more with `SHARC_FAST_ONLY=<list>
SHARC_FAST_LOG=1` for the log of what ran last. Needs DIGI_EMU_SYX like fastcov.
"""

from __future__ import annotations

import argparse
import pathlib
import re
import subprocess

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent.parent
from tools.devtools import fastcov  # noqa: E402
from tools.emulator import coupled  # noqa: E402


def run(binary, env, scenario, seconds, work: pathlib.Path) -> str:
    argv = coupled.run_args(
        binary,
        coupled.inputs(),
        [
            "--scenario",
            scenario,
            "--seconds",
            str(seconds),
            "--out",
            str(work / "r.json"),
        ],
    )
    done = subprocess.run(
        argv,
        cwd=ROOT,
        env=coupled.run_env(env),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        check=False,
    )
    return done.stderr.decode(errors="replace")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--config", default="fast-auto:2,0+follow")
    ap.add_argument("--scenario", default="idle")
    ap.add_argument("--seconds", type=float, default=0.1)
    ap.add_argument("--match", default="fork")
    ap.add_argument("--no-build", action="store_true")
    args = ap.parse_args()
    binary = coupled.ensure_binary(args.no_build)
    work = ROOT / "out" / "native" / "ddmin"
    work.mkdir(parents=True, exist_ok=True)
    _, env = fastcov.parse_config(args.config)

    def fails(text: str) -> bool:
        return "DSP halted" in text and args.match in text

    text = run(
        binary, {**env, "SHARC_FAST_LOG": "1"}, args.scenario, args.seconds, work
    )
    if not fails(text):
        print(
            "the full run does not fail:",
            [l for l in text.splitlines() if "halt" in l][:2],  # noqa: E741
        )
        return 1
    pcs = []
    for line in text.splitlines():
        m = re.match(r"fast: (0x[0-9a-f]+) (armed|followed)$", line)
        if m and m[1] not in pcs:
            pcs.append(m[1])
    print(f"{len(pcs)} entries armed; shrinking")
    only = work / "only.txt"

    def test(subset: list[str]) -> bool:
        only.write_text(",".join(subset))
        out = run(
            binary,
            {**env, "SHARC_FAST_ONLY": f"@{only}"},
            args.scenario,
            args.seconds,
            work,
        )
        return fails(out)

    n = 2
    while len(pcs) >= 2:
        size = max(len(pcs) // n, 1)
        chunks = [pcs[i : i + size] for i in range(0, len(pcs), size)]
        reduced = False
        for i, chunk in enumerate(chunks):
            # Try the chunk alone, then everything but the chunk.
            rest = [p for j, c in enumerate(chunks) if j != i for p in c]
            for cand in (chunk, rest):
                if 0 < len(cand) < len(pcs) and test(cand):
                    pcs, n, reduced = cand, max(n - 1, 2) if cand is rest else 2, True
                    print(f"  {len(pcs)} left")
                    break
            if reduced:
                break
        if not reduced:
            if n >= len(pcs):
                break
            n = min(n * 2, len(pcs))
    print("minimal set:", ",".join(pcs))
    only.write_text(",".join(pcs))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
