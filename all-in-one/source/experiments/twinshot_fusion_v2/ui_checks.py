"""Native two-page routing, label guards and prior-machine framebuffer A/B."""
import argparse
import hashlib
import json
import struct
from pathlib import Path

from patchkit import test_all_in_one25 as ui

from tools.patchkit import pk_cfcall


def main():
    ap = argparse.ArgumentParser(__doc__)
    for name in ("plan", "baseline", "baseline-report", "report"):
        ap.add_argument("--" + name, type=Path, required=True)
    a = ap.parse_args()
    plan = json.loads(a.plan.read_text())
    def sym(name):
        return next(v for k, v in plan["symbols"].items() if k.endswith(":" + name))
    pk_cfcall._built = True
    m = pk_cfcall.CfMachine("Digitakt_II_OS1.17.syx", timeout=90)
    current, current_report = ui.CANDIDATE, ui.REPORT
    pokes, page, sound, _ = ui.fixture.__wrapped__()
    ui.CANDIDATE, ui.REPORT = a.baseline, a.baseline_report
    old_pokes, old_page, old_sound, _ = ui.fixture.__wrapped__()
    ui.CANDIDATE, ui.REPORT = current, current_report
    previous = []
    for typ in (*range(7), 11, 12, 13):
        frames = []
        for p, view, snd in ((pokes, page, sound), (old_pokes, old_page, old_sound)):
            edits = {**p, snd + 0xA2: bytes([typ]), view + 0x90: bytes(4)}
            frames.append(ui.bits(ui.checked(m.run(edits, [ui.clear(), ui.page_call(view)]))[-1]))
        assert frames[0] == frames[1], (typ, "prior SRC page changed")
        previous.append(typ)
    pokes[sound + 0xA2] = bytes([14])
    for idx, val in {25:0x4000,26:0x200,27:0x400,28:7,29:0x2000,30:0x5400,
                     31:0x4000,32:0x4000,33:1055,34:0x3000}.items():
        pokes[sound + 0x14 + 2 * idx] = struct.pack(">H", val)
    pages = []
    for index in (0, 1):
        rows = ui.checked(m.run(pokes, [
            {"pc":0x4006B01E,"args":[page]},
            {"pc":0x400691E2,"args":[page,index],"reads":[(page+0x90,4)]},
            ui.clear(),ui.page_call(page),
            *[{"pc":sym("fusion2_param"),"args":[page,k]} for k in range(8)]]))
        assert rows[0].d0 == 2
        assert struct.unpack(">I", rows[1].reads[0])[0] == index
        codes = [r.d0 for r in rows[4:]]
        expected = ([248]+[sym("params.fusion2_"+s+".index") for s in
                              ("sync","mode","sample1","tune2","depth","sample2","shape")]
                    if index == 0 else [sym("params.fusion2_"+s+".index") for s in
                                        ("start1","start2","shape")]+[0]*5)
        assert codes == expected, (codes, expected)
        ui.save_bitmap(ui.bits(rows[3]), f"FUS2-page-{index+1}")
        pages.append(codes)
    buf = 0x47F06000
    pokes[buf-4] = b"GUAR"+bytes(16)+b"DEND"
    specs = [("fusion2_sync_fmt",k<<8,name) for k,name in enumerate(("OFF","VARI","RELA"))]
    specs += [("fusion2_mode_fmt",k<<8,name) for k,name in enumerate(("BLND","RING","DUCK","MRPH","GATE"))]
    rows = ui.checked(m.run(pokes,[{"pc":sym(fn),"args":[raw,buf],"reads":[(buf-4,24)]} for fn,raw,_ in specs]))
    for row,(_,_,expected) in zip(rows,specs,strict=True):
        b = row.reads[0]
        assert b[:4] == b"GUAR" and b[-4:] == b"DEND"
        assert b[4:-4].split(b"\0",1)[0].decode() == expected
    report = {"status":"PASS","image_sha256":hashlib.sha256(current.read_bytes()).hexdigest(),
              "prior_SRC_framebuffers_exact_vs_hardware_V1":previous,"native_pages":pages,
              "native_count_and_set_page":"PASS","guarded_labels":[s[2] for s in specs],
              "physical_controls_display":"UNTESTED"}
    a.report.write_text(json.dumps(report,indent=2)+"\n")
    print("Native 2-page UI, routing, guarded labels and stock/TWIN/FUSE/Plaits page A/B PASS",flush=True)


if __name__ == "__main__":
    main()
