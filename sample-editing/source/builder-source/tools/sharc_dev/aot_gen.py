#!/usr/bin/env python3
"""Generate a firmware's block library (AOT core) and register it by DSP image.

    uv run python -m tools.sharc_dev.aot_gen dt2 --card-image IMG [--extra 110000000]
    mise run aot-gen -- dt2 --card-image IMG

The cheap, single-cycle version of tools/sharc_dev/sharc_dn2_aot.py:
  1. an interpreter profile of a trig run from the device's ready snapshot
     (`sharc_live` with DSP_PROFILE, as tools/emulator/ready.py makes it),
  2. `tools/sharc_dev/sharc_rsgen.py` on that profile (core and block code; the model-safe
     DN2 flags),
  3. the gen directory is entered in out/native/gen-index.json under the
     SHA-256 of the packed DSP image, which is how tools/emulator/native_emu.py and
     tools/emulator/coupled.py pick the core to build the player against
     (`digi_env.generated_dir(image=...)`). The first player build against it
     compiles `sharc-gen` (about 5 min) into its own target dir
     (tools/emulator/native_target.py), never into prod.

Output is firmware-derived: out/native/<device>-audio/gen.
"""

from __future__ import annotations

import argparse
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
from tools import python_env  # noqa: E402
from tools.emulator import digi_env, ready  # noqa: E402
from tools.sharc_dev import sharc_dn2_aot  # noqa: E402

MODEL_FLAGS = ["--model-safe", "--explicit-memory-model", "0", "--chain"]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("device", choices=["dt2"], help="devices without a registered core")
    p.add_argument("--card-image", type=pathlib.Path, required=True)
    p.add_argument("--extra", default="110000000", help="CF instructions after ready")
    p.add_argument(
        "--cycles",
        type=int,
        default=2,
        help="profile cycles: 0 is interpreter-only, each later one replays with the "
        "previous cycle's blocks on (that run records the block transitions)",
    )
    a = p.parse_args(argv)
    cfg = ready.DEVICES[a.device]
    syx = digi_env.firmware_for(a.device)
    out = ROOT / "out" / "native" / f"{a.device}-audio"
    out.mkdir(parents=True, exist_ok=True)

    done = subprocess.run(
        [sys.executable, "-m", "tools.emulator.ready", a.device, "--syx", str(syx),
         "--card-image", str(a.card_image)],
        cwd=ROOT, env=python_env(), capture_output=True, text=True, check=True,
    )  # fmt: skip
    ready_dir = pathlib.Path(
        next(
            line.partition("=")[2]
            for line in done.stdout.splitlines()
            if line.startswith("ready_dir=")
        )
    )
    examples = ready.build_examples()
    prof = out / "prof"
    profiles = [prof]
    run = out / "run"
    run.mkdir(exist_ok=True)
    for name in ("snap", "snap.dsp"):  # sharc_live restores in place: work on copies
        (run / f"w.{name}").write_bytes(
            (ready_dir / f"digi-audio-m5.{name}").read_bytes()
        )
    image = ready_dir / "digi-audio-dn2-image.bin"
    env = {
        **os.environ,
        **{k: v for k, v in cfg["env"].items() if k != "SAVE_QUIET_FRAMES"},
        "NOTE_EVENTS": "trig",
        "SHARC_FAST_REGIONS": "",
        "SHARC_AOT": "0",
        "SHARC_BLOCKTRANS": "",
        "CARD_IMAGE": str(a.card_image),
        "CF_SNAPSHOT": str(run / "w.snap"),
        "DSP_PROFILE": str(prof),
    }
    subprocess.run(
        [str(examples / "sharc_live"), str(syx), str(ready_dir.parent.parent / "cold-idle.dsp"),
         str(image), str(run / "w.wav"), a.extra],
        cwd=ROOT, env=env, check=True, stdout=subprocess.DEVNULL,
    )  # fmt: skip
    gen = out / "gen"

    def rsgen(base: pathlib.Path) -> None:
        subprocess.run(
            [sys.executable, "-m", "tools.sharc_dev.sharc_rsgen", cfg["image"],
             "--coverage", str(base), "--entries", f"{base}.entries",
             "--transitions", f"{base}.trans", *MODEL_FLAGS,
             "--work", str(out / "work"), "--out", str(gen)],
            cwd=ROOT, env=python_env(), check=True,
        )  # fmt: skip

    rsgen(prof)
    for cycle in range(1, a.cycles):
        # Replay with the blocks built so far; only that run sees the block
        # transitions. rsgen then gets every cycle's profile, merged.
        examples = ready.build_examples(gen)
        this = out / f"prof-c{cycle}"
        for name in ("snap", "snap.dsp"):
            (run / f"w.{name}").write_bytes(
                (ready_dir / f"digi-audio-m5.{name}").read_bytes()
            )
        subprocess.run(
            [str(examples / "sharc_live"), str(syx), str(ready_dir.parent.parent / "cold-idle.dsp"),
             str(image), str(run / "w.wav"), a.extra],
            cwd=ROOT, check=True, stdout=subprocess.DEVNULL,
            env={**env, "SHARC_AOT": "1", "DSP_PROFILE": str(this)},
        )  # fmt: skip
        profiles.append(this)
        merged = out / f"prof-merged-c{cycle}"
        for suffix in sharc_dn2_aot.PROFILE_KINDS:
            merged.with_name(merged.name + suffix).write_text(
                sharc_dn2_aot.merge_kind(
                    [pathlib.Path(str(c) + suffix).read_text() for c in profiles],
                    suffix,
                )
            )
        rsgen(merged)
    digi_env.register_gen(image, gen)
    print(f"gen {gen} registered for {digi_env.GEN_INDEX}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
