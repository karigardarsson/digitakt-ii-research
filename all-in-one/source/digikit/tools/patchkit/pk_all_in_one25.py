"""One private Plaits25 + Musical WAVE V4/Zoom + Kari UI 2.1 composition.

The Plaits-free source image and frozen DV source are authoritative. Patchkit
constructs the new Plaits delta against stock separately because its ports are
stock-hash keyed. Only the existing guarded DV integration is composed with
that delta; no previous 14-engine image contributes firmware bytes.
"""

from __future__ import annotations

import argparse
import json
import struct
from pathlib import Path

from tools.patchkit import pk_integrate_dv_zoom as dv
from tools.patchkit import pk_link

ROOT = Path(__file__).resolve().parents[2]
UPSTREAM = "e48d0a2a83f055c549391173247cea02c571927f"
UI_BASE = 0x47EA0000
DV_BASE = 0x47EC0000
VT_SLOT = 0x401EDC98 + 45 * 4
STOCK_WIDGET = 0x40069B8E


def symbol(plan, suffix):
    matches = [v for k, v in plan["symbols"].items() if k.endswith(":" + suffix)]
    if len(matches) != 1:
        raise RuntimeError(f"ambiguous/missing plan symbol: {suffix}")
    return matches[0]


def append(main, address, code):
    segments, end = dv.parsed_segments(main)
    for _src, size, dest, bss in segments:
        if address < dest + max(size, bss) and dest < address + len(code):
            raise RuntimeError("UI overlaps a loaded segment")
    return (
        bytearray(main[:end])
        + struct.pack(">III", len(code), address, 0)
        + code
        + struct.pack(">I", 0xFFFFFFFF)
    )


def prepare(host: Path, dv_root: Path, seed: Path, plan_path: Path):
    pack, manifest, source = dv.load_v11(dv_root, musical=True)
    # This validates the actual decoded source MAIN, loader, payload, source
    # hash, and all V4 symbols, not just a suggestive filename.
    dv.V11_EXP = DV_BASE
    code, symbols = dv.extract_musical_v4_payload(host, pack)
    plan = json.loads(plan_path.read_text())
    stock = ROOT / "firmware/Digitakt_II_OS1.17.syx"
    if dv.sha(stock.read_bytes()) != plan["base"]["syx_sha256"]:
        raise RuntimeError("Plaits seed base is not our stock source")
    expected_modules = {
        "core/expansion",
        "core/machines",
        "core/dsp-inline",
        "core/params",
        "machines/plaits",
        "machines/plaits-48k",
    }
    if {m.split("@")[0] for m in plan["modules"]} != expected_modules:
        raise RuntimeError(
            "unexpected modules: no splash, ARP or other extras permitted"
        )
    _, _, ss = pack.decode_all(stock)
    _, _, hs = pack.decode_all(host)
    _, _, ps = pack.decode_all(seed)
    for sid in (2, 4, 5, 7, 8):
        if (
            hs[sid]["plain"] != ss[sid]["plain"]
            or hs[sid]["stored"] != ss[sid]["stored"]
        ):
            raise RuntimeError(f"host contains a non-DV modification in section {sid}")
    for sid in (2, 4, 5, 8):
        if (
            ps[sid]["plain"] != ss[sid]["plain"]
            or ps[sid]["stored"] != ss[sid]["stored"]
        ):
            raise RuntimeError(f"Plaits seed modified protected section {sid}")
    original = ps[3]["plain"]
    if dv.at(original, dv.INTRO_SCENES, 80) != dv.STOCK_INTRO_SCENES:
        raise RuntimeError("new seed lacks original Elektron splash")
    loader = dv.at(original, dv.PK_LOADER_SOURCE, dv.PK_LOADER_BYTES)
    main, writes = dv.apply_v51(original, pack, manifest, loader)
    writes.append(dv.relocate_pk_loader(main, manifest, loader))
    writes += dv.apply_musical_v4(main, symbols)
    main, segment = dv.append_v11_segment(main, code)
    writes.append(segment)
    dv.PF_TABLE = symbol(plan, "pf_table")
    writes += dv.apply_active_musical_wave_descriptors(main, symbols)
    writes += dv.apply_v11_hooks(main, symbols)

    # No allocator guesses: all seed placements are included in the plan.
    for w in plan["writes"]:
        if w["cpu"] == "cf" and w["kind"] == "place":
            for addr, length in ((DV_BASE, len(code)), (UI_BASE, 0x10000)):
                if addr < w["addr"] + w["len"] and w["addr"] < addr + length:
                    raise RuntimeError(f"private segment overlaps {w['what']}")
    cf = ROOT / "modules/machines/plaits/src/cf"
    sources = [cf / f for f in ("controls_ui.s", "model_short.s", "controls_ui.c")]
    externs = {
        "plaits_stock_widget": STOCK_WIDGET,
        "plaits_pixel": 0x401124FC,
        "plaits_fill": 0x401131AE,
        "plaits_text": 0x40113DC8,
        "plaits_effective_value": 0x4006AA32,
        "plaits_dx7_short": symbol(plan, "plaits_dx7_short"),
    }
    obj, linked, isa = pk_link.build(sources, UI_BASE, externs)
    if linked.bss_size or any(s["kind"] in ("data", "bss") for s in obj["sections"]):
        raise RuntimeError("UI must not own mutable/persistent data")
    ui_code = linked.image + bytes(-len(linked.image) % 4)
    if len(ui_code) > 0x10000:
        raise RuntimeError("UI exceeds its allocation")
    if dv.at(main, VT_SLOT, 4) != struct.pack(">I", STOCK_WIDGET):
        raise RuntimeError("unexpected source widget pointer")
    dv.put(main, VT_SLOT, struct.pack(">I", linked.symbols["plaits_ui_widget"]))
    main = append(main, UI_BASE, ui_code)
    model_source = dv.source_for_loaded_address(main, dv.PF_TABLE + 275 * 60)
    if struct.unpack(">4I", dv.at(main, model_source + 4, 16)) != (26, 0, 0x1800, 0):
        raise RuntimeError("active MODEL is not upstream's 25-position parameter")
    # Only the three WAVE rows' max and formatter fields change in the active
    # table. All other stock/Plaits descriptor bytes must remain as generated.
    for row in range(280):
        addr = dv.PF_TABLE + row * 60
        before = dv.at(original, dv.source_for_loaded_address(original, addr), 60)
        after = dv.at(main, dv.source_for_loaded_address(main, addr), 60)
        allowed = (
            set(range(12, 16)) | set(range(52, 56))
            if row in dv.LFO_WAVE_ROWS
            else set()
        )
        if any(
            a != b and i not in allowed
            for i, (a, b) in enumerate(zip(before, after, strict=True))
        ):
            raise RuntimeError(f"unexpected descriptor change: row {row}")
    if dv.at(main, dv.INTRO_SCENES, 80) != dv.STOCK_INTRO_SCENES:
        raise RuntimeError("intro changed during composition")
    report = {
        "upstream": UPSTREAM,
        "host": {
            "path": str(host),
            "sha256": dv.sha(host.read_bytes()),
            "plaits_free_sections": [2, 4, 5, 7, 8],
        },
        "dv_source": {"path": str(source), "sha256": dv.sha(source.read_bytes())},
        "seed": {
            "path": str(seed),
            "sha256": dv.sha(seed.read_bytes()),
            "modules": plan["modules"],
        },
        "dv": {
            "address": DV_BASE,
            "bytes": len(code),
            "sha256": dv.sha(code),
            "symbols": symbols,
        },
        "ui": {
            "address": UI_BASE,
            "bytes": len(ui_code),
            "symbols": linked.symbols,
            "isa": isa,
            "toolchain": obj["toolchain"],
            "sources": {
                str(p.relative_to(ROOT)): dv.sha(p.read_bytes()) for p in sources
            },
        },
        "pf_table": dv.PF_TABLE,
        "writes": writes,
        "regions": plan["regions"],
        "original_splash": True,
        "stock_parameter_changes_only_wave_max_formatter": True,
        "legacy_model_compatibility": "intentionally not supported; one upstream type-11 identity",
    }
    return pack, bytes(main), report


def main():
    ap = argparse.ArgumentParser(__doc__)
    for arg in ("host", "dv-root", "seed", "plan", "output", "report"):
        ap.add_argument("--" + arg, type=Path, required=True)
    a = ap.parse_args()
    if a.output.exists() or a.report.exists():
        raise RuntimeError("output/report exists: preserve previous artifacts")
    pack, main_bytes, report = prepare(
        a.host.resolve(), a.dv_root.resolve(), a.seed.resolve(), a.plan.resolve()
    )
    report["candidate"] = dv.package(
        a.seed.resolve(), a.output.resolve(), main_bytes, pack
    )
    _, _, before = pack.decode_all(a.seed)
    _, _, after = pack.decode_all(a.output)
    for sid in (2, 4, 5, 7, 8):
        if (
            before[sid]["stored"] != after[sid]["stored"]
            or before[sid]["plain"] != after[sid]["plain"]
        ):
            raise RuntimeError(f"non-MAIN section altered in composition: {sid}")
    report["section_preservation"] = {
        str(s): "stored/plain identical to seed" for s in (2, 4, 5, 7, 8)
    }
    report["main_sha256"] = dv.sha(main_bytes)
    a.report.parent.mkdir(parents=True, exist_ok=True)
    a.report.write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            {"candidate": report["candidate"], "regions": report["regions"]}, indent=2
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
