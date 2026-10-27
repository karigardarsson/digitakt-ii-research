"""Final offline integrity/provenance gate. Never flash or publish."""
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
    for name in ("image", "plan", "seed", "source", "pcm-reference", "pcm-plan", "report"):
        ap.add_argument("--" + name, type=Path, required=True)
    a = ap.parse_args()
    current, seed = port_resolve.sections_of(a.image), port_resolve.sections_of(a.seed)
    assert current[7] == seed[7], "composition changed gated DSP bytes"
    errors = pk_encoding_gate.check_image(a.image, a.plan)
    assert not errors, [pk_encoding_gate.format_violation(e) for e in errors]
    stock = port_resolve.sections_of(a.source / "firmware/Digitakt_II_OS1.17.syx")
    boot = sharc_bootcheck.check(current[7])
    assert boot.ok, [str(x) for x in boot.errors]
    assert not [x for x in sharc_bootcheck.check_against_stock(stock[7], current[7]) if x.level == "error"]
    for section in (2, 4, 5, 8):
        assert current[section] == stock[section], section
    plan = json.loads(a.plan.read_text())
    oldplan = json.loads(a.pcm_plan.read_text())
    owners = ("machines/plaits-48k@", "core/dsp-inline@")
    kept = []
    before = LoadedMemory.from_stream(port_resolve.sections_of(a.pcm_reference)[7])
    after = LoadedMemory.from_stream(current[7])
    for write in plan["writes"]:
        if write["cpu"] != "sharc" or write["kind"] != "place" or not write["owner"].startswith(owners):
            continue
        if write["what"].startswith("registry"):
            # The second registry row names the changed Twinshot code. The
            # first Plaits row and the dispatcher executable must be unchanged.
            assert before.read(write["addr"], 8) == after.read(write["addr"], 8)
            continue
        old = next(w for w in oldplan["writes"] if w["owner"] == write["owner"] and w["what"] == write["what"])
        assert write == old, (write["owner"], write["what"])
        low, high = write["addr"], write["addr"] + write["len"]
        count = 0
        for block in after.blocks:
            lo, hi = max(low, block["target_address"]), min(high, block["target_address"] + block["byte_count"])
            if lo < hi:
                assert before.read(lo, hi - lo) == after.read(lo, hi - lo)
                count += hi - lo
        kept.append({"owner": write["owner"], "what": write["what"], "loaded_bytes_equal": count,
                     "allocation_and_linked_image_hash_equal": True})
    mainbytes = current[3]
    assert dv.at(mainbytes, dv.INTRO_SCENES, 80) == dv.STOCK_INTRO_SCENES
    pf = next(v for k, v in plan["symbols"].items() if k.endswith(":pf_table"))
    wave_rows = []
    for row in (79, 89, 99):
        address = dv.source_for_loaded_address(mainbytes, pf + row * 60)
        assert struct.unpack(">I", dv.at(mainbytes, address + 12, 4))[0] == 0xF00
        wave_rows.append(row)
    root = HERE.parents[1]
    baselines = {
        root / "out/dt2-dv-offline/private-musical-wave-v2/DT2_OS117_MUSICAL_WAVE_V2_PRIVATE.syx":
            "58aeec44e25a90364ba7ccf50f0870cec91104db3065b5107b10851cf5610aab",
        root / "out/dt2-dv-offline/private-musical-wave-v3/DT2_OS117_MUSICAL_WAVE_V3_PRIVATE.syx":
            "16d5845dc799c6e5e542969ae54bcb728bb0152ebeb4d1594209e176eda02d40",
        root / "out/dt2-dv-offline/private-musical-wave-v4/DT2_OS117_MUSICAL_WAVE_V4_PRIVATE.syx":
            "850ad57a7035dbb93a001737aedfe302c8bb1c4fbcd0d75a297b826ac8e73122",
        a.source.parents[2] / "out/FLASH_THIS_PLAITS25_DX7_UI_FIX/dt2-all-in-one-plaits25-ui21-dx7-label-fix-1.17.syx":
            "b9ee18b521d1dc5bd39b9e882dfa7ce4ab8bdfa9be692d63182748a95a5be740",
    }
    preserved = []
    for path, expected in baselines.items():
        assert sha(path) == expected, path
        preserved.append({"path": str(path.resolve()), "sha256": expected})
    sources = [p for p in HERE.rglob("*") if p.is_file() and p.suffix in (".c", ".s", ".toml", ".py")]
    sources += [a.source / "modules/core/machines/types.toml", a.source / "devices/ports/dt2-1.17.toml"]
    report = {"status": "PASS", "image_sha256": sha(a.image), "image_bytes": a.image.stat().st_size,
              "encoding_violations": 0, "sharc_bootcheck": "PASS", "dsp_bytes": len(current[7]),
              "protected_sections_equal_stock": [2, 4, 5, 8], "original_elektron_splash": True,
              "wave_0_15_active_descriptor_rows": wave_rows,
              "plaits_pcm_reference": {"image": str(a.pcm_reference.resolve()), "sha256": sha(a.pcm_reference),
                                       "all_25_models_pcm_ab": "prior PASS, actual loaded code/data parity verified here"},
              "preserved_plaits_sdk_ranges": kept, "regions": plan["regions"],
              "preserved_baselines": preserved,
              "source_sha256": {str(p.resolve()): sha(p) for p in sorted(sources)},
              "hardware": "UNTESTED", "filesystem_project_reload": "not hardware proven",
              "silicon_cpu_headroom": "not measured; no full-polyphony guarantee"}
    a.report.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({k: report[k] for k in ("status", "image_sha256", "encoding_violations", "sharc_bootcheck", "regions")}, indent=2))


if __name__ == "__main__":
    main()
