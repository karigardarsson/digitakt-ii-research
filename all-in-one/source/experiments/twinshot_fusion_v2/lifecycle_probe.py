"""Bounded V1 lifecycle isolation; actual native release and block epilogue.

No transport observation is inferred from manual flag writes. This separates
the SDK's claimed release lifetime from the instructions that implement it.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from native_dual_proof import Native

from tools.patchkit.port_resolve import sections_of
from tools.sharc_dev.sharcldr import LoadedMemory


def run(image):
    assert hashlib.sha256(image.read_bytes()).hexdigest() == "d57bbd459229df8ed69fac91d281da17369b8be57e144acddb05e031b7c04fac"
    n = Native(LoadedMemory.from_stream(sections_of(image)[7]), approx_recips=True)
    pending, released = 0x24F0AC, 0x24F0EC
    for address in range(pending, pending + 0xC0, 4):
        n.put(address, 0)
    n.call(0x1C6056, {"R4": 0x2412C8, "R8": 0})
    before = n.data(released, 32).hex()
    assert n.get(released, 1) == 1
    # Exact block epilogue, entered with the pointer assigned at 1c6fb4.
    # Stop before unrelated register restoration. No sampler/flag code patched.
    n.call(0x1C7182, {"I12": pending, "MODE1": 0x11800}, stop=0x1C71A9, bound=500)
    after = n.data(released, 32).hex()
    return {"image_sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
            "release_native": "0x1c6056", "epilogue_native": "0x1c7182..0x1c71a9",
            "before": before, "after": after,
            "release_survives_block": n.get(released, 1) == 1,
            "tier": "actual native instruction slices, reconstructed entry; not natural transport or hardware"}


if __name__ == "__main__":
    ap = argparse.ArgumentParser(__doc__)
    ap.add_argument("--image", type=Path, required=True)
    ap.add_argument("--report", type=Path, required=True)
    a = ap.parse_args()
    result = run(a.image)
    a.report.parent.mkdir(parents=True, exist_ok=True)
    a.report.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
