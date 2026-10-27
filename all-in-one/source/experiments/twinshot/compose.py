"""Compose the tested Twinshot module seed with the exact latest DV/Zoom/UI.

No old 14-model image contributes Plaits bytes. Preserve the last complete
image's self-contained corrected UI, and check all allocation boundaries.
"""
from __future__ import annotations

import argparse
import copy
import json
import struct
from pathlib import Path

from tools.patchkit import pk_integrate_dv_zoom as dv

SHA = "b9ee18b521d1dc5bd39b9e882dfa7ce4ab8bdfa9be692d63182748a95a5be740"
UI, DV = 0x47EA0000, 0x47EC0000
VT = 0x401EDC98 + 45 * 4


def symbol(plan, name):
    xs = [v for k, v in plan["symbols"].items() if k.endswith(":" + name)]
    assert len(xs) == 1, name
    return xs[0]


def main():
    ap = argparse.ArgumentParser(__doc__)
    for name in ("baseline", "baseline-report", "old-plan", "seed", "plan", "dv-root", "output", "report"):
        ap.add_argument("--" + name, type=Path, required=True)
    a = ap.parse_args()
    for name, value in vars(a).items():
        setattr(a, name, value.resolve())
    assert dv.sha(a.baseline.read_bytes()) == SHA
    oldreport = json.loads(a.baseline_report.read_text())
    oldplan = json.loads(a.old_plan.read_text())
    plan = json.loads(a.plan.read_text())
    expected = {"core/expansion", "core/machines", "core/params", "core/dsp-inline",
                "machines/plaits", "machines/plaits-48k", "machines/twinshot"}
    assert {x.split("@")[0] for x in plan["modules"]} == expected
    pack, manifest, _ = dv.load_v11(a.dv_root, musical=True)
    _, _, old = pack.decode_all(a.baseline)
    _, _, seed = pack.decode_all(a.seed)
    for sid in (2, 4, 5, 8):
        assert seed[sid]["stored"] == old[sid]["stored"]
    before = seed[3]["plain"]
    assert dv.at(before, dv.INTRO_SCENES, 80) == dv.STOCK_INTRO_SCENES
    dv.V11_EXP = DV
    code = dv.at(old[3]["plain"], dv.source_for_loaded_address(old[3]["plain"], DV),
                 dv.MUSICAL_PAYLOAD_BYTES)
    symbols = {k: DV + v - dv.MUSICAL_V4_ORIGINAL_EXP for k, v in dv.MUSICAL_V4_SYMBOLS.items()}
    assert symbols == oldreport["dv"]["symbols"]
    assert dv.sha(code) == oldreport["dv"]["sha256"]
    loader = dv.at(before, dv.PK_LOADER_SOURCE, dv.PK_LOADER_BYTES)
    mainbytes, writes = dv.apply_v51(before, pack, manifest, loader)
    writes.append(dv.relocate_pk_loader(mainbytes, manifest, loader))
    writes += dv.apply_musical_v4(mainbytes, symbols)
    mainbytes, segment = dv.append_v11_segment(mainbytes, code)
    writes.append(segment)
    dv.PF_TABLE = symbol(plan, "pf_table")
    writes += dv.apply_active_musical_wave_descriptors(mainbytes, symbols)
    writes += dv.apply_v11_hooks(mainbytes, symbols)
    oldmain = old[3]["plain"]
    ui = bytearray(dv.at(oldmain, dv.source_for_loaded_address(oldmain, UI), oldreport["ui"]["bytes"]))
    oldtarget = symbol(oldplan, "plaits_dx7_short")
    newtarget = symbol(plan, "plaits_dx7_short")
    operand = struct.pack(">I", oldtarget)
    # The newest DX7-label fix uses its local model_short table, unlike the
    # earlier UI revision. It contains no reference to the moved seed helper.
    assert ui.count(operand) == 0
    pos = None
    for write in plan["writes"]:
        if write["cpu"] == "cf" and write["kind"] == "place":
            for addr, size in ((DV, len(code)), (UI, len(ui))):
                assert not (addr < write["addr"] + write["len"] and write["addr"] < addr + size)
    segments, end = dv.parsed_segments(mainbytes)
    for _, size, dest, bss in segments:
        assert not (dest + max(size, bss) > UI and dest < UI + len(ui))
    mainbytes = bytearray(mainbytes[:end]) + struct.pack(">III", len(ui), UI, 0) + ui + struct.pack(">I", 0xFFFFFFFF)
    assert dv.at(mainbytes, VT, 4) == struct.pack(">I", 0x40069B8E)
    dv.put(mainbytes, VT, struct.pack(">I", symbol(plan, "twinshot_widget")))
    assert dv.at(mainbytes, dv.INTRO_SCENES, 80) == dv.STOCK_INTRO_SCENES
    # Every pre-existing descriptor has the same data as the new seed except
    # the three explicitly approved WAVE max/formatter changes.
    for row in range(286):
        address = dv.PF_TABLE + row * 60
        x = dv.at(before, dv.source_for_loaded_address(before, address), 60)
        y = dv.at(mainbytes, dv.source_for_loaded_address(mainbytes, address), 60)
        allowed = set(range(12, 16)) | set(range(52, 56)) if row in dv.LFO_WAVE_ROWS else set()
        assert all(x[i] == y[i] or i in allowed for i in range(60)), row
    report = copy.deepcopy(oldreport)
    report.update({"baseline": {"path": str(a.baseline), "sha256": SHA},
                   "seed": {"path": str(a.seed), "sha256": dv.sha(a.seed.read_bytes()), "modules": plan["modules"]},
                   "pf_table": dv.PF_TABLE, "regions": plan["regions"], "writes": writes,
                   "ui_relocation": {"operand_offset": pos, "old": oldtarget, "new": newtarget,
                                     "required": False, "reason": "corrected self-contained local name table"},
                   "twinshot_ui_chain": {"entry": symbol(plan, "twinshot_widget"),
                                          "previous": oldreport["ui"]["symbols"]["plaits_ui_widget"]},
                   "hardware": "UNTESTED"})
    report["candidate"] = dv.package(a.seed, a.output, bytes(mainbytes), pack)
    _, _, final = pack.decode_all(a.output)
    for sid in (2, 4, 5, 7, 8):
        assert seed[sid]["stored"] == final[sid]["stored"]
    report["main_sha256"] = dv.sha(bytes(mainbytes))
    report["section_preservation"] = {str(s): "stored identical to Twinshot seed" for s in (2, 4, 5, 7, 8)}
    a.report.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"candidate": report["candidate"], "regions": plan["regions"]}, indent=2))


if __name__ == "__main__":
    main()
