"""Focused final-image control, p-lock, conversion, START LFO and STOP gates."""
import argparse
import hashlib
import json
from pathlib import Path

from control_proof_v2 import run as storage_run, sym
from event_proof_v2 import run as event_run
from frame_proof_v2 import run as frame_run
from stop_proof import run as stop_run


def main():
    ap = argparse.ArgumentParser(__doc__)
    for name in ("image", "plan", "state", "tools", "report"):
        ap.add_argument("--"+name, type=Path, required=True)
    a = ap.parse_args()
    plan = json.loads(a.plan.read_text())
    sha = hashlib.sha256(a.image.read_bytes()).hexdigest()
    selectors = tuple(range(26,35))
    report = {"image_sha256":sha}
    report["storage"] = storage_run(a.image,a.plan,a.state,a.tools)
    print("Native slots and Sound/Pattern converters PASS",flush=True)
    report["events"] = event_run(a.image,a.state,a.tools,expected_sha=sha,
        selectors=selectors,machine_type=14,smoother=sym(plan,"twinshot_smoother"),
        defaults=(0x100,0x300,7,0x2000,0x5000,0x4000,0x4000,31,0x4000),
        lanes=({0:0x200,2:0},{0:0x400,1:0x100},{0:1036,2:43},
               {0:0x6000,1:0x2000},{0:0x2000,2:0x6000},{0:0x3807,1:0x4101},
               {0:0x7f00,1:0x1000},{0:31,1:1031},{0:0x2000,2:0x6000}),
        discrete_selectors=(26,27,28,33))
    print("All nine new controls native pattern/vector events PASS",flush=True)
    report["start_lfo"] = {str(k):frame_run(a.image,a.state,a.tools,destination=k) for k in (29,30,32,34)}
    print("START, amount and response LFO paths and slot reconstruction PASS",flush=True)
    report["stop"] = stop_run(a.image,a.tools)
    report["status"] = "PASS"
    a.report.write_text(json.dumps(report,indent=2)+"\n")
    print("Control gates PASS",flush=True)


if __name__ == "__main__":
    main()
