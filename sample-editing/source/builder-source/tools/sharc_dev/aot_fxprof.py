#!/usr/bin/env python3
"""DSP profiles from coupled scenario runs, merged into an existing AOT profile.

    uv run python -m tools.sharc_dev.aot_fxprof --out DIR \
        tests/scenarios/dn2-trackfx.json:tf-br-127 tests/scenarios/dn2-sendfx.json:sf-mst-thr0 \
        [--base-cov F --base-entries F --base-trans F]

The AOT cores are generated from interpreter profiles (coverage, entries,
transitions, the inputs of tools/sharc_dev/sharc_rsgen.py). A plain trig run never
reaches the code that only runs with an effect on, so that code stays
interpreted. This runs scenarios that set the effect parameters through
`coupled-run --profile-execution` (the Engine's option-26 maps) twice each:

  cycle 0  interpreter only (no fast regions, no blocks): coverage and entries
  cycle 1  the registered core and fast tier on: block transitions, as
           tools/sharc_dev/aot_gen.py's later cycles do

(cycle 0 only with --interp-cycle) and writes DIR/prof-fx{,.entries,.trans}: all runs, plus the base files when
given, merged with sharc_dn2_aot.merge_kind (summed per key). Feed them to
`tools/sharc_dev/sharc_rsgen.py --coverage/--entries/--transitions`. Output is
firmware-derived: keep it under out/.
"""

from __future__ import annotations

import argparse
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
from tools.emulator import coupled  # noqa: E402
from tools.sharc_dev import sharc_dn2_aot  # noqa: E402

INTERP_ENV = {
    "SHARC_FAST_REGIONS": "",
    "SHARC_BLOCKTRANS": "",
    "SHARC_AOT": "0",
    "DIGI_EMU_FAST": "0",
}
# profile map name (coupled-run) -> rsgen file suffix
MAPS = {"coverage": "", "entries": ".entries", "transitions": ".trans"}


# Set by --dt2-card: the sample card's ready inputs and its `--card-image` argument.
CARD: tuple[dict[str, pathlib.Path], list[str]] | None = None


def run(scenarios: str, name: str, cycle: int, out: pathlib.Path, device: str) -> dict:
    """One profiled run; returns {suffix: text}."""
    report = out / f"c{cycle}" / name / "report.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    binary = coupled.ensure_binary(True, device)
    ins, card = CARD or (coupled.inputs(device), [])
    argv = coupled.run_args(
        binary,
        ins,
        [*card, "--scenarios", str(pathlib.Path(scenarios).resolve()), "--scenario", name,
         "--out", str(report), "--profile-execution"],
    )  # fmt: skip
    env = {**os.environ, **digi_env_fast(), **(INTERP_ENV if cycle == 0 else {})}
    with coupled.benchlock("shared", f"fxprof {name} c{cycle}"):
        subprocess.run(argv, cwd=ROOT, env=env, check=True, stdout=subprocess.DEVNULL)
    prof = report.with_name(f"{report.stem}-execution-profile")
    return {
        suffix: (prof / f"{name.replace('.', '_')}.{kind}.tsv").read_text()
        for kind, suffix in MAPS.items()
    }


def digi_env_fast() -> dict[str, str]:
    return coupled.run_env({})


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("names", nargs="+", metavar="FILE:SCENARIO")
    p.add_argument("--out", type=pathlib.Path, required=True)
    p.add_argument("--device", default="dn2", choices=["dn2", "dt2"])
    p.add_argument(
        "--interp-cycle",
        action="store_true",
        help="also run cycle 0 (interpreter only; minutes per scenario). Not needed "
        "for FX-only code: the default-config run interprets it anyway",
    )
    p.add_argument("--base-cov", type=pathlib.Path)
    p.add_argument("--base-entries", type=pathlib.Path)
    p.add_argument("--base-trans", type=pathlib.Path)
    p.add_argument(
        "--dt2-card",
        action="store_true",
        help="DT2: run on the one-sample card of tools/emulator/dt2_sample_check.py",
    )
    a = p.parse_args(argv)
    if a.dt2_card:
        from tools.emulator import dt2_sample_check

        global CARD
        CARD = dt2_sample_check.card_inputs()
    a.out.mkdir(parents=True, exist_ok=True)
    texts: dict[str, list[str]] = {s: [] for s in MAPS.values()}
    for base, suffix in (
        (a.base_cov, ""),
        (a.base_entries, ".entries"),
        (a.base_trans, ".trans"),
    ):
        if base:
            texts[suffix].append(base.read_text())
    for spec in a.names:
        file, _, name = spec.rpartition(":")
        for cycle in (0, 1) if a.interp_cycle else (1,):
            got = run(file, name, cycle, a.out, a.device)
            for suffix, text in got.items():
                texts[suffix].append(text)
            print(f"{name} c{cycle}: " + ", ".join(
                f"{s or '.cov'} {len(t.splitlines())} rows" for s, t in got.items()
            ), flush=True)  # fmt: skip
    for suffix, items in texts.items():
        (a.out / f"prof-fx{suffix}").write_text(sharc_dn2_aot.merge_kind(items, suffix))
    print(f"merged profile: {a.out}/prof-fx[.entries|.trans]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
