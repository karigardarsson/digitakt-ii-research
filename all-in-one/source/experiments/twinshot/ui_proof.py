"""Draw the installed Twinshot page on the retained real UI objects."""
from __future__ import annotations

import argparse
import json
import struct
from pathlib import Path

from patchkit import test_all_in_one25 as ui

from tools.patchkit import pk_cfcall


def main():
    ap = argparse.ArgumentParser(__doc__)
    ap.add_argument("--plan", type=Path, required=True)
    ap.add_argument("--report", type=Path, required=True)
    a = ap.parse_args()
    plan = json.loads(a.plan.read_text())
    def sym(name):
        return next(v for k, v in plan["symbols"].items() if k.endswith(":" + name))
    pokes, page, sound, _ = ui.fixture.__wrapped__()
    pokes[sound + 0xA2] = b"\x0c"
    controls = {25: 0x4000, 26: 0x4000, 27: 0x7F00, 28: 7,
                31: 0x4000, 32: 0x4000, 33: 1055, 34: 0x7F00}
    for index, value in controls.items():
        pokes[sound + 0x14 + 2 * index] = struct.pack(">H", value)
    buf = 0x47F06000
    pokes[buf - 4] = b"GUAR" + bytes(64) + b"DEND"
    calls = [ui.clear(), ui.page_call(page)]
    for value in (0, 7, 1023, 1024, 1031, 2047):
        calls.append({"pc": sym("twinshot_sample_fmt"), "args": [value, buf],
                      "reads": [(buf - 4, 72)], "max": 100000})
    pk_cfcall._built = True
    results = ui.checked(pk_cfcall.CfMachine("Digitakt_II_OS1.17.syx", timeout=60).run(pokes, calls))
    bitmap = ui.bits(results[1])
    assert any(bitmap)
    reference_pokes = {**pokes, 0x401EDC98 + 45 * 4: struct.pack(">I", 0x47EA0000)}
    reference = ui.checked(pk_cfcall.CfMachine("Digitakt_II_OS1.17.syx", timeout=60).run(
        reference_pokes, [ui.clear(), ui.page_call(page)]))
    before = ui.bits(reference[-1])
    changed = []
    for x in range(128):
        for y in range(64):
            shift = 31 - y % 32
            off = x * 8 + (y // 32) * 4
            if ((struct.unpack_from(">I", bitmap, off)[0] ^
                 struct.unpack_from(">I", before, off)[0]) >> shift) & 1:
                changed.append((x, y))
    outside = [(x, y) for x, y in changed if not
               ((94 <= x < 128 and 32 <= y < 64) or (68 <= x < 96 and 0 <= y < 32))]
    assert not outside, ("pixels changed outside the two sample cells", outside[:30])
    ui.save_bitmap(bitmap, "twinshot-installed-src")
    labels = []
    for value, result in zip((0, 7, 1023, 1024, 1031, 2047), results[2:], strict=True):
        data = result.reads[0]
        assert data[:4] == b"GUAR" and data[-4:] == b"DEND"
        text = data[4:-4].split(b"\0", 1)[0].decode()
        assert text.startswith("R:" if value & 1024 else "F:")
        labels.append({"raw": value, "label": text})
    report = {"status": "PASS", "native_src_page": "0x40016eac", "type": 12,
              "controls": controls, "direction_formatter": labels,
              "changed_sample_cell_pixels": len(changed), "all_other_pixels_bit_exact": True,
              "ui_callee_registers_and_bitmap_guards": "PASS", "hardware": "UNTESTED"}
    a.report.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
