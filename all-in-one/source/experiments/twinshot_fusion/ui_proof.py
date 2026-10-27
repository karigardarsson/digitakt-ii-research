"""Native Fusion page/formatters and exact preserved V1 page A/B."""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
from pathlib import Path

from patchkit import test_all_in_one25 as ui

from tools.patchkit import pk_cfcall


def main():
    ap = argparse.ArgumentParser(__doc__)
    for name in ("plan", "v1", "v1-report", "report"):
        ap.add_argument("--" + name, type=Path, required=True)
    a = ap.parse_args()
    plan = json.loads(a.plan.read_text())

    def sym(name):
        return next(v for k, v in plan["symbols"].items() if k.endswith(":" + name))

    pokes, page, sound, _ = ui.fixture.__wrapped__()
    pokes[sound + 0xA2] = b"\x0d"
    values = {25: 0x4000, 26: 0x100, 27: 0x100, 28: 7,
              31: 0x4000, 32: 0x7F00, 33: 1055, 34: 0x7F00}
    for idx, val in values.items():
        pokes[sound + 0x14 + 2 * idx] = struct.pack(">H", val)
    buf = 0x47F06000
    pokes[buf - 4] = b"GUAR" + bytes(32) + b"DEND"
    specs = [("fusion_mode_fmt", v << 8, s) for v, s in enumerate(("BLND", "RING", "DUCK", "MRPH"))]
    specs += [("fusion_sync_fmt", 0, "OFF"), ("fusion_sync_fmt", 256, "ON"),
              ("twinshot_sample_fmt", 7, "F:7"), ("twinshot_sample_fmt", 1055, "R:31")]
    calls = [ui.clear(), ui.page_call(page)] + [
        {"pc": sym(name), "args": [value, buf], "reads": [(buf - 4, 40)], "max": 100000}
        for name, value, _ in specs]
    pk_cfcall._built = True
    machine = pk_cfcall.CfMachine("Digitakt_II_OS1.17.syx", timeout=60)
    results = ui.checked(machine.run(pokes, calls))
    pixels = ui.bits(results[1])
    assert any(pixels)
    ui.save_bitmap(pixels, "fusion-src-sync-ring")
    for result, (_, _, expected) in zip(results[2:], specs, strict=True):
        data = result.reads[0]
        assert data[:4] == b"GUAR" and data[-4:] == b"DEND"
        assert data[4:-4].split(b"\0", 1)[0].decode() == expected
    current_image, current_report = ui.CANDIDATE, ui.REPORT
    ui.CANDIDATE, ui.REPORT = a.v1, a.v1_report
    old_pokes, old_page, old_sound, _ = ui.fixture.__wrapped__()
    ui.CANDIDATE, ui.REPORT = current_image, current_report
    current_pages, old_pages = [], []
    for edits, target, track_sound, pages in ((pokes, page, sound, current_pages),
                                              (old_pokes, old_page, old_sound, old_pages)):
        edits[track_sound + 0xA2] = b"\x0c"
        for idx, val in {**values, 26: 0x4000, 27: 0x7F00}.items():
            edits[track_sound + 0x14 + 2 * idx] = struct.pack(">H", val)
        pages.append(ui.bits(ui.checked(machine.run(edits, [ui.clear(), ui.page_call(target)]))[-1]))
    assert current_pages == old_pages, "Fusion changed V1 SRC pixels"
    a.report.write_text(json.dumps({"status": "PASS", "image_sha256": hashlib.sha256(current_image.read_bytes()).hexdigest(),
        "native_fusion_src": "PASS", "guarded_labels": [s for _, _, s in specs],
        "v1_src_vs_hardware_reference": "bit exact", "hardware": "UNTESTED"}, indent=2) + "\n")
    print("Fusion native SRC / guarded labels / V1 pixel A-B PASS")


if __name__ == "__main__":
    main()
