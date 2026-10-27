"""Add the Plaits SRC graphics to the immutable combined Musical WAVE V4 image.

Uses the existing reset-loader segment chain and existing container verification.
Only two old MAIN words change: SourcePageView slot 45 and MODEL's formatter.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
from pathlib import Path

from tools.patchkit import pk_integrate_dv_zoom as integration
from tools.patchkit import pk_link

ROOT = Path(__file__).resolve().parents[2]
BASE_SHA = "2d55bd4bbd0edb9df9e39cb5ad8d45ceb893b049afe41927a1ba8db9078686dd"
UI_BASE = 0x47EA0000
VT_SLOT = 0x401EDC98 + 45 * 4
STOCK_WIDGET = 0x40069B8E
OLD_FORMATTER = 0x47E908D4
SOURCES = [
    ROOT / "modules/machines/plaits/src/cf" / n
    for n in ("model_fmt.s", "controls_ui.s", "controls_ui.c")
]
EXTERNS = {
    "plaits_stock_widget": STOCK_WIDGET,
    "plaits_pixel": 0x401124FC,
    "plaits_fill": 0x401131AE,
    "plaits_text": 0x40113DC8,
}


def prepare(base: Path, dv_root: Path):
    if integration.sha(base.read_bytes()) != BASE_SHA:
        raise RuntimeError("UI baseline SHA mismatch")
    pack, _manifest, _source = integration.load_v11(dv_root, musical=True)
    _, _, sections = pack.decode_all(base)
    original = sections[3]["plain"]
    main = bytearray(original)
    segments, end = integration.parsed_segments(original)
    if end + 4 != len(original):
        raise RuntimeError("unexpected trailing MAIN data")
    if (
        integration.at(main, integration.INTRO_SCENES, 80)
        != integration.STOCK_INTRO_SCENES
    ):
        raise RuntimeError("baseline lacks stock intro")
    obj, linked, isa = pk_link.build(SOURCES, UI_BASE, EXTERNS)
    if linked.bss_size or any(s["kind"] in ("data", "bss") for s in obj["sections"]):
        raise RuntimeError("renderer must not own mutable persistent data")
    code = linked.image + bytes(-len(linked.image) % 4)
    if UI_BASE + len(code) > 0x47EB0000:
        raise RuntimeError("UI allocation exceeds its 64 KiB bound")
    for _src, size, dest, bss in segments:
        if dest + max(size, bss) > UI_BASE and dest < UI_BASE + len(code):
            raise RuntimeError("UI segment collides with an existing allocation")
    if integration.at(main, VT_SLOT, 4) != struct.pack(">I", STOCK_WIDGET):
        raise RuntimeError("unexpected SourcePageView slot 45")
    model_row = integration.PF_TABLE + 275 * 60
    source_row = integration.source_for_loaded_address(main, model_row)
    if integration.at(main, source_row + 4, 16) != struct.pack(">4I", 26, 0, 0x7F00, 0):
        raise RuntimeError("MODEL slot/range/default provenance mismatch")
    if integration.at(main, source_row + 0x34, 4) != struct.pack(">I", OLD_FORMATTER):
        raise RuntimeError("MODEL formatter provenance mismatch")
    writes = [
        (VT_SLOT, linked.symbols["plaits_ui_widget"]),
        (source_row + 0x34, linked.symbols["plaits_model_fmt"]),
    ]
    for address, value in writes:
        integration.put(main, address, struct.pack(">I", value))
    main[end:] = (
        struct.pack(">III", len(code), UI_BASE, 0)
        + code
        + struct.pack(">I", 0xFFFFFFFF)
    )
    allowed = {a - integration.MAIN_BASE + i for a, _ in writes for i in range(4)}
    changed = {i for i in range(end) if main[i] != original[i]}
    if not changed <= allowed:
        raise RuntimeError("unexpected existing MAIN change")
    report = {
        "baseline_sha256": BASE_SHA,
        "source_sha256": {
            str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in SOURCES
        },
        "object_source_sha256": obj["source_sha256"],
        "toolchain": obj["toolchain"],
        "isa_gate": isa,
        "segment": {"address": hex(UI_BASE), "bytes": len(code), "bss": 0},
        "writes": [
            {
                "address": hex(a),
                "old": integration.at(original, a, 4).hex(),
                "new": hex(v),
            }
            for a, v in writes
        ],
        "symbols": {
            k: hex(v) for k, v in linked.symbols.items() if k.startswith("plaits_")
        },
        "original_splash": True,
        "existing_segments_preserved": len(segments),
    }
    return pack, original, bytes(main), linked, report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--base", type=Path, required=True)
    p.add_argument("--dv-root", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--report", type=Path, required=True)
    a = p.parse_args()
    if a.output.exists() or a.report.exists():
        raise SystemExit("output/report exists; refusing overwrite")
    pack, _old, new, _linked, report = prepare(a.base.resolve(), a.dv_root.resolve())
    report["candidate"] = integration.package(
        a.base.resolve(), a.output.resolve(), new, pack
    )
    _, _, before = pack.decode_all(a.base.resolve())
    _, _, after = pack.decode_all(a.output.resolve())
    report["section_preservation"] = {}
    for sid in before:
        if sid == 3:
            continue
        if any(before[sid][k] != after[sid][k] for k in ("stored", "plain", "dest")):
            raise RuntimeError(f"protected section {sid} changed")
        report["section_preservation"][str(sid)] = {
            "stored_and_plain_identical": True,
            "plain_sha256": before[sid]["plain_sha256"],
        }
    report["evidence"] = (
        "ColdFire ISA gate + package/section verification; physical UI UNTESTED"
    )
    a.report.parent.mkdir(parents=True, exist_ok=True)
    a.report.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
