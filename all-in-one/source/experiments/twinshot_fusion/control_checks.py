"""Reuse native vector, LFO and payload converters for both preserved/new types."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import control_proof
import frame_proof
import native_event_proof


def main():
    ap = argparse.ArgumentParser(__doc__)
    for name in ("image", "plan", "state", "tools", "report"):
        ap.add_argument("--" + name, type=Path, required=True)
    a = ap.parse_args()
    sha = hashlib.sha256(a.image.read_bytes()).hexdigest()
    plan = json.loads(a.plan.read_text())
    smoother = next(v for k, v in plan["symbols"].items() if k.endswith(":twinshot_smoother"))
    cases = {}
    for typ in (12, 13):
        cases[str(typ)] = {"controls_and_storage": control_proof.run(a.image, a.plan, a.state, a.tools, machine_type=typ),
                           "native_lfo_frame": frame_proof.run(a.image, a.state, a.tools, machine_type=typ)}
    cases["fusion_discrete_locks"] = native_event_proof.run(
        a.image, a.state, a.tools, expected_sha=sha, machine_type=13, smoother=smoother,
        selectors=(26, 27, 28, 33), defaults=(0, 0, 7, 31),
        lanes=({0: 256, 2: 256}, {0: 768, 1: 256}, {0: 1036, 2: 43}, {0: 31, 1: 1031}))
    a.report.write_text(json.dumps({"status": "PASS", "sha256": sha, "cases": cases,
        "filesystem_project_reload": "not executed; native payload converters only",
        "hardware": "UNTESTED"}, indent=2) + "\n")
    print("Native controls / consecutive locks / LFO / Sound-v3 / Pattern conversion PASS (types 12,13)")


if __name__ == "__main__":
    main()
