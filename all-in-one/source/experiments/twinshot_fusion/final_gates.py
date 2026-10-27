"""Final package, placement and baseline preservation gates; never flash."""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
from pathlib import Path

from tools.patchkit import pk_encoding_gate, port_resolve
from tools.patchkit import pk_integrate_dv_zoom as dv
from tools.sharc_dev import sharc_bootcheck
from tools.sharc_dev.sharcldr import LoadedMemory

HERE = Path(__file__).resolve().parent


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    ap = argparse.ArgumentParser(__doc__)
    for name in ("image", "plan", "seed", "source", "v1", "v1-gates", "pcm-reference", "pcm-plan", "report"):
        ap.add_argument("--" + name, type=Path, required=True)
    a = ap.parse_args()
    current, seed = port_resolve.sections_of(a.image), port_resolve.sections_of(a.seed)
    assert current[7] == seed[7], "composition changed the tested DSP"
    violations = pk_encoding_gate.check_image(a.image, a.plan)
    assert not violations, [pk_encoding_gate.format_violation(v) for v in violations]
    stock = port_resolve.sections_of(a.source / "firmware/Digitakt_II_OS1.17.syx")
    boot = sharc_bootcheck.check(current[7])
    assert boot.ok
    assert not [f for f in sharc_bootcheck.check_against_stock(stock[7], current[7]) if f.level == "error"]
    for section in (2, 4, 5, 8):
        assert stock[section] == current[section]
    assert dv.at(current[3], dv.INTRO_SCENES, 80) == dv.STOCK_INTRO_SCENES
    previous = json.loads(a.v1_gates.read_text())
    v1_sha = "30c80fe71f935b1271a7ab33e112038b06e37b1332ce842fb16e74cc902eafba"
    assert sha(a.v1) == v1_sha
    for item in previous["preserved_baselines"]:
        assert sha(Path(item["path"])) == item["sha256"]
    preserved_sources = []
    for path, expected in previous["source_sha256"].items():
        p = Path(path)
        if p.suffix in (".c", ".s", ".toml") and "experiments/twinshot/" in path:
            assert sha(p) == expected, path
            preserved_sources.append(path)
    plan = json.loads(a.plan.read_text())
    pcm_plan = json.loads(a.pcm_plan.read_text())
    before = LoadedMemory.from_stream(port_resolve.sections_of(a.pcm_reference)[7])
    after = LoadedMemory.from_stream(current[7])
    preserved_dsp = []
    for write in plan["writes"]:
        if write["cpu"] != "sharc" or write["kind"] != "place" or write["owner"].startswith("machines/twinshot-fusion@"):
            continue
        old = next(w for w in pcm_plan["writes"] if w["owner"] == write["owner"] and w["what"] == write["what"])
        assert write == old, (write["owner"], write["what"])
        low, high = write["addr"], write["addr"] + write["len"]
        for block in after.blocks:
            lo, hi = max(low, block["target_address"]), min(high, block["target_address"] + block["byte_count"])
            if lo < hi:
                assert before.read(lo, hi - lo) == after.read(lo, hi - lo)
        preserved_dsp.append({"owner": write["owner"], "what": write["what"], "allocation_hash_and_loaded_bytes_equal": True})
    pf = next(v for k, v in plan["symbols"].items() if k.endswith(":pf_table"))
    for row in (79, 89, 99):
        src = dv.source_for_loaded_address(current[3], pf + row * 60)
        assert struct.unpack(">I", dv.at(current[3], src + 12, 4))[0] == 0xF00
    for name, bound in (("params.fusion_sync.index", 0x100), ("params.fusion_mode.index", 0x300),
                         ("params.fusion_sample1.index", 2047), ("params.fusion_sample2.index", 2047)):
        row = next(v for k, v in plan["symbols"].items() if k.endswith(":" + name))
        src = dv.source_for_loaded_address(current[3], pf + row * 60)
        assert struct.unpack(">I", dv.at(current[3], src + 12, 4))[0] == bound
    for item in plan["regions"].values():
        assert item[0] <= item[1]
    sources = [p for p in HERE.rglob("*") if p.is_file() and p.suffix in (".c", ".h", ".s", ".py", ".toml")]
    sources += [a.source / "modules/core/machines/types.toml", a.source / "devices/ports/dt2-1.17.toml"]
    report = {"status": "PASS", "image": str(a.image.resolve()), "sha256": sha(a.image),
        "bytes": a.image.stat().st_size, "dsp_bytes": len(current[7]), "dsp_matches_tested_seed": sha(a.seed),
        "encoding_violations": 0, "sharc_bootcheck": "PASS", "stock_sections": [2, 4, 5, 8],
        "stock_startup": True, "wave_rows_0_15": [79, 89, 99], "regions": plan["regions"],
        "v1_hardware_reference_preserved": v1_sha, "v1_dsp_sources_unchanged": preserved_sources,
        "older_baselines_preserved": previous["preserved_baselines"],
        "plaits_25_pcm_tested_seed": {"sha256": sha(a.pcm_reference), "non_fusion_dsp": preserved_dsp},
        "source_sha256": {str(p.resolve()): sha(p) for p in sorted(sources)},
        "hardware": "UNTESTED", "project_filesystem_remapping_powercycle": "unverified",
        "silicon_cpu_budget": "unmeasured; no full-polyphony guarantee"}
    a.report.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({k: report[k] for k in ("status", "sha256", "bytes", "regions", "encoding_violations")}))


if __name__ == "__main__":
    main()
