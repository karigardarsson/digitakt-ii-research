"""Compose one Fusion candidate, preserving exact V1 DV/Zoom and corrected UI."""
from __future__ import annotations

import argparse
import copy
import json
import struct
from pathlib import Path

from tools.patchkit import pk_integrate_dv_zoom as dv

V1 = "d57bbd459229df8ed69fac91d281da17369b8be57e144acddb05e031b7c04fac"
UI, DV, VT = 0x47EA0000, 0x47EC0000, 0x401EDC98 + 45 * 4


def symbol(plan, name):
    xs = [v for k, v in plan["symbols"].items() if k.endswith(":" + name)]
    assert len(xs) == 1, name
    return xs[0]


def main():
    ap = argparse.ArgumentParser(__doc__)
    for name in ("baseline", "baseline-report", "seed", "plan", "dv-root", "output", "report"):
        ap.add_argument("--" + name, type=Path, required=True)
    a = ap.parse_args()
    for name, value in vars(a).items():
        setattr(a, name, value.resolve())
    assert not a.output.exists(), "never overwrite a delivered candidate"
    assert dv.sha(a.baseline.read_bytes()) == V1
    previous = json.loads(a.baseline_report.read_text())
    plan = json.loads(a.plan.read_text())
    expected = {"core/expansion", "core/machines", "core/params", "core/dsp-inline",
                "machines/plaits", "machines/plaits-48k", "machines/twinshot", "machines/twinshot-fusion"}
    assert {x.split("@")[0] for x in plan["modules"]} == expected
    pack, manifest, _ = dv.load_v11(a.dv_root, musical=True)
    _, _, old = pack.decode_all(a.baseline)
    _, _, seed = pack.decode_all(a.seed)
    for sid in (2, 4, 5, 8):
        assert seed[sid]["stored"] == old[sid]["stored"]
    before = seed[3]["plain"]
    dv.V11_EXP = DV
    code = dv.at(old[3]["plain"], dv.source_for_loaded_address(old[3]["plain"], DV), dv.MUSICAL_PAYLOAD_BYTES)
    symbols = previous["dv"]["symbols"]
    assert dv.sha(code) == previous["dv"]["sha256"]
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
    ui = dv.at(oldmain, dv.source_for_loaded_address(oldmain, UI), previous["ui"]["bytes"])
    for write in plan["writes"]:
        if write["cpu"] == "cf" and write["kind"] == "place":
            for addr, size in ((DV, len(code)), (UI, len(ui))):
                assert not (addr < write["addr"] + write["len"] and write["addr"] < addr + size)
    segments, end = dv.parsed_segments(mainbytes)
    for _, size, dest, bss in segments:
        assert not (dest + max(size, bss) > UI and dest < UI + len(ui))
    mainbytes = bytearray(mainbytes[:end]) + struct.pack(">III", len(ui), UI, 0) + ui + struct.pack(">I", 0xFFFFFFFF)
    assert dv.at(mainbytes, VT, 4) == struct.pack(">I", 0x40069B8E)
    dv.put(mainbytes, VT, struct.pack(">I", symbol(plan, "fusion_widget")))
    # SourcePageView's native header allocates (26 - title_len - hint_len)
    # spaces. The long FUS2 title plus its inherited "Y:Slice Menu" makes
    # that negative and throws basic_string::_S_create on real SRC cycling.
    # FUS2 is a two-page control machine, not a Slice editor. Remove only its
    # inherited hint in the ACTIVE relocated table; retain its full title.
    hint_addr = symbol(plan, "mf_names") + 14 * 12 + 8
    hint_src = dv.source_for_loaded_address(mainbytes, hint_addr)
    assert dv.at(mainbytes, hint_src, 4) == struct.pack(">I", 0x40242C9E)
    dv.put(mainbytes, hint_src, bytes(4))
    writes.append({"kind":"FUS2 native header hint", "loaded_address":hint_addr,
                   "source_address":hint_src,"old":"40242c9e","new":"00000000",
                   "reason":"prevent negative native header padding / Slice-only hint is inapplicable"})
    assert dv.at(mainbytes, dv.INTRO_SCENES, 80) == dv.STOCK_INTRO_SCENES
    for row in range(symbol(plan,"params.first")+symbol(plan,"params.count")):
        address = dv.PF_TABLE + row * 60
        x = dv.at(before, dv.source_for_loaded_address(before, address), 60)
        y = dv.at(mainbytes, dv.source_for_loaded_address(mainbytes, address), 60)
        allowed = set(range(12, 16)) | set(range(52, 56)) if row in dv.LFO_WAVE_ROWS else set()
        assert all(x[i] == y[i] or i in allowed for i in range(60)), row
    report = copy.deepcopy(previous)
    report.update({"baseline": {"path": str(a.baseline), "sha256": V1, "evidence": "USER HARDWARE-TESTED V1, musical limitations reported"},
                   "seed": {"path": str(a.seed), "sha256": dv.sha(a.seed.read_bytes()), "modules": plan["modules"]},
                   "pf_table": dv.PF_TABLE, "regions": plan["regions"], "writes": writes,
                   "fusion": {"type": 13, "modes": ["BLEND", "RING", "DUCK", "MORPH"],
                              "sync": "optional B-to-A rate sync, 0.25..4x, B follows A tuning",
                              "ui_chain": [symbol(plan, "fusion_widget"), symbol(plan, "twinshot_widget"), UI]},
                   "hardware": "UNTESTED"})
    a.output.parent.mkdir(parents=True, exist_ok=True)
    report["fusion_v2"] = {"type": 14, "pages": 2,
        "layout": [["SMPA", "TUNA", "SYNC", "FUSE", "SMPB", "TUNB/TRAN/blank", "BAL/WET/DEP/POS", "blank"],
                   ["ST.A", "ST.B", "blank/DRY/RCVR/AUTO"]],
        "modes": ["BLND", "RING", "DUCK", "MRPH", "GATE"],
        "sync": ["OFF independent", "VARI legacy duration match", "RELA duration ratio + relative B transpose"],
        "pitch_preserving_stretch": False, "legacy_type13_saved_meanings": "preserved",
        "note_off": "owned release state until next trig; native byte is one-block only",
        "starts": "offset from playback edge, normalized within native minimum-length-safe window"}
    report["fusion_v2"]["native_slice_hint"] = "FUS2-only null; native header length exception repaired"
    report["fusion_v2"]["ui_aliases"] = "context-specific descriptors; original saved fields and numeric IDs preserved"
    report["candidate"] = dv.package(a.seed, a.output, bytes(mainbytes), pack)
    _, _, final = pack.decode_all(a.output)
    for sid in (2, 4, 5, 7, 8):
        assert seed[sid]["stored"] == final[sid]["stored"]
    report["main_sha256"] = dv.sha(bytes(mainbytes))
    report["section_preservation"] = {str(s): "stored identical to gated Fusion seed" for s in (2, 4, 5, 7, 8)}
    a.report.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"candidate": report["candidate"], "regions": plan["regions"]}, indent=2))


if __name__ == "__main__":
    main()
