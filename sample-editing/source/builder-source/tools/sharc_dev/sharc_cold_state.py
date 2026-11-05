"""A cold-start DSP state blob from the flattened section 7 loader image.

No captured state: the last FIRST entry of the boot stream, the reference
reset values and the public HWR register reset table (the setup
`tools/sharc_dev/sharc_reset_check.py` uses for its cold start). The output is the
canonical blob `Engine::import` takes (memory is the packed image, so the
overlay is empty), optionally wrapped as a DT2DSP01 sidecar for
`coupled-run --dsp-state` / `sharc_live`.

    uv run python -m tools.sharc_dev.sharc_cold_state dn2-1.11 OUT.bin [--sidecar]

Firmware-derived: keep OUT outside the repository.
"""

from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

from tools.sharc_dev import sharc_diff as sd
from tools.sharc_dev import sharc_run as sr
from tools.sharc_dev import sharc_trace as st
from tools.sharc_dev import sharc_transpile_run as nr
from tools.sharc_dev import sharcldr
from tools.sharc_dev.sharc_mmr_reset import parse_reset_tables

HWR = Path(__file__).resolve().parent.parent.parent / "out/refs/adsp-2156x-hwr/all.txt"


def cold_blob(image: str) -> tuple[bytes, int]:
    return cold_blob_for_memory(sr._load_image_memory(image))


def cold_blob_for_memory(data: sharcldr.LoadedMemory) -> tuple[bytes, int]:
    """`cold_blob` for a loader image already in memory (for example a .syx's section 7)."""
    entries = sharcldr.entry_points(data.blocks)
    if not entries:
        raise SystemExit("loader has no FIRST entry")
    runner = sr.Runner(data, entries[-1], approx_recips=True)
    state = runner.state
    state.bank_model = True
    state.stack_model = True
    state.software_interrupts = True
    state.core_timer = True
    resets = parse_reset_tables(HWR.read_text())
    state.mmrs.update({a: st.Const(v) for a, (v, _) in resets.items()})
    fields = sd.export_state(state, page_hash=False)
    fields["memory_ranges"] = nr._overlay_ranges(state.overlay)
    return sd.pack_state(fields), entries[-1]


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("image")
    p.add_argument("out", type=Path)
    p.add_argument("--sidecar", action="store_true", help="wrap as DT2DSP01")
    p.add_argument("--clock", type=int, default=0, help="sidecar clock base")
    a = p.parse_args(argv)
    blob, entry = cold_blob(a.image)
    if a.sidecar:
        blob = b"DT2DSP01" + struct.pack("<QQ", a.clock, 0) + blob
    a.out.write_bytes(blob)
    print(f"{a.out}: {len(blob)} bytes, entry sw {entry:#x}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
