"""Bounded diagnostic of conversion ABI values in an installed seed."""
import argparse
import json
import struct
from pathlib import Path

from native_dual_proof import Native

from tools.patchkit.port_resolve import sections_of
from tools.sharc_dev import sharc_trace as st
from tools.sharc_dev.sharcldr import LoadedMemory

ap = argparse.ArgumentParser(__doc__)
ap.add_argument("--image", type=Path, required=True)
ap.add_argument("--plan", type=Path, required=True)
a = ap.parse_args()
plan = json.loads(a.plan.read_text())
n = Native(LoadedMemory.from_stream(sections_of(a.image)[7]), approx_recips=True)
for name, values in (("fusion_float.", (16, 2048, 48000)),
                     ("fusion_bits.", (0x46400000, 0x45000000))):
    pc = next(v for k, v in plan["symbols"].items() if k.endswith(":" + name))
    for x in values:
        n.call(pc, {"R4": x, "MODE1": 0x1011800})
        result = n.runner.state.uregs[st.UREG_CODES["R0"]].value
        print(name, hex(x), hex(result), struct.unpack("<f", struct.pack("<I", result))[0])
