"""Repair-specific final-image gates and single, never-overwritten delivery.

Run from the retained host. Old DSP evidence is explicitly reused only after
byte/hash binding; changed CF paths require fresh reports from this image.
"""
import argparse
import hashlib
import json
import shutil
import struct
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from tools.patchkit import pk_encoding_gate, port_resolve
from tools.patchkit import pk_integrate_dv_zoom as dv
from tools.sharc_dev import sharc_bootcheck
from tools.sharc_dev.sharcldr import LoadedMemory

HERE = Path(__file__).resolve().parent
FAILED = "70f19ec949041d87e88d5f950fc748fea3dadeca70dd3c792d72bb540a0c96b6"
V1 = "d57bbd459229df8ed69fac91d281da17369b8be57e144acddb05e031b7c04fac"


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def read(p):
    return json.loads(p.read_text())


def main():
    ap = argparse.ArgumentParser(__doc__)
    for name in ("work","source","previous","baseline"):
        ap.add_argument("--"+name,type=Path,required=True)
    ap.add_argument("--destination",type=Path)
    ap.add_argument("--ui-reference",type=Path,help="preserved Repair1 for a UI-only repair")
    a = ap.parse_args()
    w, oldwork = a.work.resolve(), a.previous.resolve()
    image, planfile = w/"final-integrated.syx", w/"final-seed.plan.json"
    digest = sha(image)
    assert digest != FAILED and sha(oldwork/"final-integrated.syx")==FAILED
    assert sha(a.baseline)==V1
    now, failed, stock = [port_resolve.sections_of(p) for p in
        (image,oldwork/"final-integrated.syx",a.source/"firmware/Digitakt_II_OS1.17.syx")]
    plan, build = read(planfile), read(w/"final-build-report.json")
    assert build["candidate"]["sha256"]==digest
    integ = build["candidate"]["integrity"]
    assert integ["file_sha256"]==digest
    assert integ["packet_checksums"]==f'{integ["message_count"]}/{integ["message_count"]}'
    assert all(r["result"]=="PASS" for r in build["candidate"]["bootstrap"].values())
    assert not pk_encoding_gate.check_image(image,planfile)
    assert sharc_bootcheck.check(now[7]).ok
    assert not [f for f in sharc_bootcheck.check_against_stock(stock[7],now[7]) if f.level=="error"]
    for sid in (2,4,5,8):
        assert now[sid]==failed[sid]==stock[sid]
    assert now[7]==failed[7]==port_resolve.sections_of(w/"final-seed.syx")[7]
    reference_sha = "134bd7d2cf1db6bf941e4eac7d1db88a61fcf76a105dcefcbcc9c3c8c55e53f7"
    if a.ui_reference:
        assert sha(a.ui_reference)==reference_sha and digest!=reference_sha
        assert now[7]==port_resolve.sections_of(a.ui_reference)[7]
        reference_delivery=read(a.ui_reference.parent/"DELIVERY-MANIFEST.json")
        for path,value in reference_delivery["files"].items():
            assert sha(a.ui_reference.parent/path)==value,path
        reused_frame=read(a.ui_reference.parent/"reports/full-frame/frame-lifecycle.json")
        assert reused_frame["status"]=="PASS" and reused_frame["sha256"]==reference_sha
    else:
        failure = read(w/"failed-native-audition.json")
        assert failure["status"]=="PASS" and failure["image_sha256"]==FAILED and failure["expect_failed"]
        underflow = read(w/"panel-failed-padding-counterexample/panel-report.json")
        assert underflow["image_sha256"]==FAILED and underflow["header_padding_count"]==0xffffffff
    fresh = {}
    fresh_specs=[("final-native-audition.json","image_sha256"),
                         ("final-control-proof.json","image_sha256"),
                         ("final-ui-proof.json","image_sha256")]
    if not a.ui_reference:
        fresh_specs.append(("full-frame/frame-lifecycle.json","sha256"))
    for name, field in fresh_specs:
        r = read(w/name)
        assert r["status"]=="PASS" and r[field]==digest, name
        fresh[name] = sha(w/name)
    panel = read(w/"panel-final/panel-report.json")
    assert panel["status"]=="COMPLETED" and panel["image_sha256"]==digest
    assert set(panel["page2_changed_mirrors"])==({"29","30"} if a.ui_reference else {"29","30","34"})
    assert panel["page2_loop_progress"]>0
    assert [r["source_page"] for r in panel["events"] if r["label"] in
            ("SRC first","SRC return first","SRC page2 again","SRC return")]==[1,0,1,0]
    assert all(r["status"]["cf"].get("error") is None and
               r["status"]["cf"]["pc"]!=0x4013ABC2 for r in panel["events"])
    if a.ui_reference:
        ui=read(w/"final-ui-proof.json")
        assert ui["reference_sha256"]==reference_sha
        assert len(ui["encoder_cases"])==180 and len(ui["contexts"])==30
        assert "native descriptor label reference" in ui["caption_rasters"]
        assert ui["guarded_formatter_cases"]>=150
        for name in ("BLND","RING","DUCK","MRPH","GATE"):
            for page in (1,2):
                assert any(r["label"]==f"{name} page{page}" for r in panel["events"])
        encoder_count=180
    else:
        burst = read(w/"final-encoder-bursts.json")
        assert burst["image_sha256"]==digest and len(burst["rows"])==16
        assert all(r["ok"] and not r["error"] and not r["clobbered"] for r in burst["rows"])
        expected_mirrors = ([25,26,27,28,31,32,33,34],[29,30,34,None,None,None,None,None])
        for row in burst["rows"]:
            index = expected_mirrors[row["page"]][row["knob"]]
            changed = set(row["sound_changed_bytes"])
            assert changed <= (set() if index is None else {0x14+index*2,0x15+index*2}), row
            if index is not None:
                assert changed, row
        encoder_count=16
    suites = list(ET.parse(w/"integration-regression.xml").iter("testsuite"))
    assert sum(int(s.attrib["tests"]) for s in suites)==7
    assert all(not int(s.attrib["failures"]) and not int(s.attrib["errors"]) for s in suites)
    # Reuse exact-DSP tests only, not the failed V2's insufficient UI coverage.
    dsp_evidence = {}
    for name in ("final-release-proof.json","final-installed-proof.json","dsp-proof/dsp-proof.json"):
        r = read(oldwork/name)
        assert r["status"]=="PASS"
        assert r.get("image_sha256",r.get("sha256"))==sha(oldwork/"final-seed.syx")
        assert now[7]==port_resolve.sections_of(oldwork/"final-seed.syx")[7]
        dsp_evidence[str(oldwork/name)]={"sha256":sha(oldwork/name),"binding":"BLOB7 byte-identical"}
    pcm = read(oldwork/"plaits-regression.json")
    pcm_image = oldwork.parent/"final-integrated.syx"
    assert pcm["status"]=="PASS" and pcm["image_sha256"]==sha(pcm_image)
    assert pcm["baseline_sha256"]==V1 and len(pcm["plaits_25_models"])==25
    prior_plan = read(oldwork.parent/"final-seed.plan.json")
    before, after = LoadedMemory.from_stream(port_resolve.sections_of(pcm_image)[7]), LoadedMemory.from_stream(now[7])
    preserved = []
    for write in plan["writes"]:
        if write["cpu"]!="sharc" or write["owner"].startswith("machines/twinshot-fusion@"):
            continue
        prior = next(x for x in prior_plan["writes"] if x["owner"]==write["owner"] and
                     x["what"]==write["what"] and x["addr"]==write["addr"])
        assert write==prior, write
        if write["kind"]=="place":
            lo,hi = write["addr"],write["addr"]+write["len"]
            for block in after.blocks:
                low,high=max(lo,block["target_address"]),min(hi,block["target_address"]+block["byte_count"])
                if low<high:
                    assert before.read(low,high-low)==after.read(low,high-low)
        preserved.append({"owner":write["owner"],"what":write["what"]})
    oldmain = port_resolve.sections_of(a.baseline)[3]
    oldbuild = read(a.baseline.parent/"build-report.json")
    for addr,size in ((0x47EA0000,oldbuild["ui"]["bytes"]),(0x47EC0000,dv.MUSICAL_PAYLOAD_BYTES)):
        assert dv.at(now[3],dv.source_for_loaded_address(now[3],addr),size)==dv.at(oldmain,dv.source_for_loaded_address(oldmain,addr),size)
    assert dv.at(now[3],dv.INTRO_SCENES,80)==dv.STOCK_INTRO_SCENES
    def sym(n):
        return next(v for k,v in plan["symbols"].items() if k.endswith(":"+n))
    for row in (79,89,99):
        assert struct.unpack(">I",dv.at(now[3],dv.source_for_loaded_address(now[3],sym("pf_table")+60*row)+12,4))[0]==0xF00
    assert {x.split("@")[0] for x in plan["modules"]}=={
        "core/expansion","core/machines","core/params","core/dsp-inline",
        "machines/plaits","machines/plaits-48k","machines/twinshot","machines/twinshot-fusion"}
    assert all(used<=capacity for used,capacity in plan["regions"].values())
    previous = read(oldwork/"final-offline-gates.json")
    artifacts = previous["preserved_artifacts"]+[{"path":str(oldwork/"final-integrated.syx"),"sha256":FAILED}]
    artifacts.append({"path":str(oldwork.parent.parent/"twinshot-sample-fusion-v2/DT2_OS117_ALL_IN_ONE_SAMPLE_FUSION_V2_CANDIDATE.syx"),"sha256":FAILED})
    if a.ui_reference:
        artifacts.append({"path":str(a.ui_reference.resolve()),"sha256":reference_sha})
    for item in artifacts:
        assert sha(Path(item["path"]))==item["sha256"], item
    for path,value in previous["preserved_v1_sources"].items():
        assert sha(Path(path))==value,path
    paths = [p for p in HERE.rglob("*") if p.is_file() and p.suffix in
             (".c",".h",".s",".py",".toml",".md") and "build" not in p.relative_to(HERE).parts]
    paths += list((HERE/"build").glob("*.dko.json"))
    paths += [a.source/"modules/core/machines/types.toml",a.source/"devices/ports/dt2-1.17.toml"]
    report = {"status":"PASS","sha256":digest,"bytes":image.stat().st_size,
        "upstream":"e48d0a2a83f055c549391173247cea02c571927f",
        "host_head":"dcda19a5ab63561d75e677d0b8dac45c27bcd00b",
        "source_sha256":{str(p.resolve()):sha(p) for p in sorted(paths)},
        "encoding_violations":0,"SHARC_bootcheck":"PASS","stock_sections":[2,4,5,8],
        "DSP_BLOB7_exact_vs_failed_V2":True,"reused_DSP_evidence":dsp_evidence,
        "plaits_25_PCM_evidence":{"path":str(oldwork/"plaits-regression.json"),
            "sha256":sha(oldwork/"plaits-regression.json"),"non_Fusion_loaded_patches_exact":preserved},
        "fresh_CF_and_frame_reports":fresh,"native_scheduled_page2":"PASS",
        "native_encoder_bursts":encoder_count,"seven_combined_tests":"PASS",
        "DV_WAVE_Zoom_DX7_UI_byte_exact":True,"stock_startup":True,
        "preserved_artifacts":artifacts,"preserved_sources":previous["preserved_v1_sources"],
        "regions":plan["regions"],"modules":plan["modules"],
        "hardware":"repair UNTESTED; failed V2 and working V1 evidence kept separate",
        "limits":["no coupled natural RTOS/CF/SHARC audio proof",
                  "physical initial sound, page cycling, encoder operation, transport and deadlines need testing",
                  "full Project filesystem/power-cycle and sample collect/remap portability untested"]}
    if a.ui_reference:
        report.update({"UI_reference_sha256":reference_sha,"DSP_exact_vs_hardware_evaluated_Repair1":True,
            "reused_native_frame_evidence":{"path":str(a.ui_reference.parent/"reports/full-frame/frame-lifecycle.json"),
                "sha256":sha(a.ui_reference.parent/"reports/full-frame/frame-lifecycle.json"),
                "binding":"BLOB7 exact; not a fresh frame run"},
            "UI_caption_rasters":"PASS all30 mode/sync/page contexts",
            "hardware":"UI repair UNTESTED; Repair1 user-evaluated with audible controls and UI usability problems"})
    (w/("ui-offline-gates.json" if a.ui_reference else "repair-offline-gates.json")).write_text(json.dumps(report,indent=2)+"\n")
    print(json.dumps({k:report[k] for k in ("status","sha256","bytes","regions")},indent=2),flush=True)
    if not a.destination:
        return
    dest = a.destination.resolve()
    assert not dest.exists(),"never overwrite any delivered folder"
    for path,value in report["source_sha256"].items():
        assert sha(Path(path))==value
    dest.mkdir()
    target = dest/("DT2_OS117_ALL_IN_ONE_FUS2_UI_REPAIR2_CANDIDATE.syx" if a.ui_reference else "DT2_OS117_ALL_IN_ONE_SAMPLE_FUSION_V2_REPAIR1_CANDIDATE.syx")
    shutil.copyfile(image,target)
    assert sha(target)==digest
    for pattern in ("*.json","*.xml","panel-final/*.json","panel-final/*.png","full-frame/*.json","ui/*.png"):
        for p in w.glob(pattern):
            if p.name not in ("repair-offline-gates.json","ui-offline-gates.json") and not (p.name.startswith("final-") or
                    p.parent.name in ("panel-final","full-frame","ui") or p.name in
                    ("integration-regression.xml","validated-module-build.json","module-build.json")):
                continue
            q = dest/"reports"/p.relative_to(w)
            q.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(p,q)
    for p in (() if a.ui_reference else (w/"failed-native-audition.json",w/"panel-failed-padding-counterexample/panel-report.json")):
        q=dest/"reports/counterexamples"/p.parent.name/p.name
        q.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(p,q)
    shutil.copyfile(HERE/("UI-REPAIR2-NOTES.md" if a.ui_reference else "REPAIR1-NOTES.md"),dest/"READ-FIRST.md")
    with zipfile.ZipFile(dest/"IMPLEMENTATION-SNAPSHOT.zip","w",zipfile.ZIP_DEFLATED) as z:
        for directory in (HERE,HERE.parent/"twinshot",HERE.parent/"twinshot_fusion"):
            for p in sorted(directory.rglob("*")):
                rel=p.relative_to(directory)
                if p.is_file() and "build" not in rel.parts and "__pycache__" not in rel.parts and p.suffix in (".py",".c",".h",".s",".toml",".md"):
                    z.write(p,str(Path(directory.name)/rel))
    (dest/"SOURCE-PROVENANCE.json").write_text(json.dumps(report,indent=2)+"\n")
    manifest={"candidate":{"file":target.name,"sha256":digest,"bytes":target.stat().st_size},
        "status":"OFFLINE REPAIR GATES PASS; HARDWARE UNTESTED",
        "replaces_failed_image_sha256":FAILED,"working_V1_preserved_sha256":V1,
        "no_flash_push_publish_PR_agents":True,
        "files":{str(p.relative_to(dest)):sha(p) for p in sorted(dest.rglob("*")) if p.is_file()}}
    if a.ui_reference:
        manifest.update({"reference_image_sha256":reference_sha,
            "scope":"FUS2 UI/control repair; DSP byte-identical to Repair1"})
    (dest/"DELIVERY-MANIFEST.json").write_text(json.dumps(manifest,indent=2)+"\n")
    assert len(list(dest.glob("*.syx")))==1
    print(json.dumps(manifest["candidate"],indent=2),flush=True)


if __name__=="__main__":
    main()
