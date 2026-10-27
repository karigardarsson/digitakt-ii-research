"""Repair3 gates: fresh changed-DSP evidence, byte-bound unchanged CF evidence.

No firmware derivation is published; one new folder only after all gates pass.
"""
import argparse
import hashlib
import json
import shutil
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from tools.patchkit import pk_encoding_gate, port_resolve
from tools.sharc_dev import sharc_bootcheck
from tools.sharc_dev.sharcldr import LoadedMemory

HERE=Path(__file__).resolve().parent
DV=HERE.parents[1]
HOST=DV.parent/"plaits-va-run02/out/spacejam-hopt2-playable"
SRC=HOST/"out/all-in-one-plaits25-e48/source"
W=DV/"out/twinshot-fus2-dsp-repair3-work"
R=DV/"out/twinshot-fus2-ui-repair2"
REF="b6f4c96e41e5432d7135a2d1563fe461700fe23c8b60dc6d1d81c5fa8249bee1"


def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def read(p):return json.loads(p.read_text())


def main():
    ap=argparse.ArgumentParser(__doc__)
    ap.add_argument("--destination",type=Path)
    a=ap.parse_args()
    image=W/"final-integrated.syx";digest=sha(image)
    prior_image=R/"DT2_OS117_ALL_IN_ONE_FUS2_UI_REPAIR2_CANDIDATE.syx"
    assert sha(prior_image)==REF and digest!=REF
    prior=read(R/"SOURCE-PROVENANCE.json")
    for path,value in read(R/"DELIVERY-MANIFEST.json")["files"].items():
        assert sha(R/path)==value,path
    for item in prior["preserved_artifacts"]:
        assert sha(Path(item["path"]))==item["sha256"],item
    for p,v in prior["preserved_sources"].items():assert sha(Path(p))==v,p
    now,old,seed,stock=[port_resolve.sections_of(p) for p in (image,prior_image,W/"final-seed.syx",
        SRC/"firmware/Digitakt_II_OS1.17.syx")]
    assert set(now)==set(old)==set(stock)
    for sid in now:
        if sid!=7:assert now[sid]==old[sid],sid
    for sid in (2,4,5,8):assert now[sid]==stock[sid],sid
    assert now[7]==seed[7] and now[7]!=old[7]
    assert not pk_encoding_gate.check_image(image,W/"final-seed.plan.json")
    assert sharc_bootcheck.check(now[7]).ok
    assert not [f for f in sharc_bootcheck.check_against_stock(stock[7],now[7]) if f.level=="error"]
    build=read(W/"final-build-report.json")
    assert build["candidate"]["sha256"]==digest
    integrity=build["candidate"]["integrity"]
    assert integrity["file_sha256"]==digest
    assert integrity["packet_checksums"]==f'{integrity["message_count"]}/{integrity["message_count"]}'
    assert all(b["result"]=="PASS" for b in build["candidate"]["bootstrap"].values())
    plan=read(W/"final-seed.plan.json");oldplan=read(R/"reports/final-seed.plan.json")
    module=read(W/"module-build.json")
    assert module["status"]=="BUILT"
    for name in ("fusion_process.h","fusion_native.c","adapter.c","entry.s","conversions.s","controls.s","pages.s"):
        assert module["sources"][str(HERE/name)]==sha(HERE/name),name
    for name,value in module["objects"].items():assert sha(HERE/"build"/name)==value,name
    assert plan["modules"]==oldplan["modules"]
    assert not any("arp" in x.lower() or "splash" in x.lower() for x in plan["modules"])
    assert all(used<=capacity for used,capacity in plan["regions"].values())
    before,after=LoadedMemory.from_stream(old[7]),LoadedMemory.from_stream(now[7])
    changed_ranges=[(x["addr"],x["addr"]+x["len"]) for p in (plan,oldplan) for x in p["writes"]
        if x["cpu"]=="sharc" and x["kind"]=="place" and x["owner"].startswith("machines/twinshot-fusion@")]
    stock_dsp_bytes=0
    for block in after.blocks:
        spans=[(block["target_address"],block["target_address"]+block["byte_count"])]
        for start,end in changed_ranges:
            spans=[part for lo,hi in spans for part in ((lo,min(hi,start)),(max(lo,end),hi)) if part[0]<part[1]]
        for lo,hi in spans:
            for start in range(lo,hi,32768):
                size=min(32768,hi-start)
                assert before.read(start,size)==after.read(start,size),(hex(start),size)
                stock_dsp_bytes+=size
    kept=[]
    for write in plan["writes"]:
        if write["cpu"]!="sharc" or write["owner"].startswith("machines/twinshot-fusion@"):
            continue
        assert write in oldplan["writes"],write
        if write["kind"]=="place":
            lo,hi=write["addr"],write["addr"]+write["len"]
            for block in after.blocks:
                start=max(lo,block["target_address"])
                end=min(hi,block["target_address"]+block["byte_count"])
                if start<end:assert before.read(start,end-start)==after.read(start,end-start),write
        kept.append(write)
    # All changed playback paths must have fresh reports bound to this image.
    fresh={}
    for name,field in (("sidechain-native/sidechain-native.json","sha256"),
        ("final-installed-proof.json","sha256"),("final-release-proof.json","image_sha256"),
        ("full-frame/native-init.json","image_sha256"),("full-frame/frame-lifecycle.json","sha256"),
        ("full-frame/frame-lifecycle-mode2.json","sha256"),("full-frame/frame-lifecycle-mode4.json","sha256"),
        ("final-control-proof.json","image_sha256")):
        report=read(W/name)
        assert report["status"]=="PASS" and report[field]==digest,name
        fresh[name]=sha(W/name)
    kernels=read(W/"seed-dsp-proof/dsp-proof.json")
    assert kernels["status"]=="PASS" and kernels["sha256"]==sha(W/"final-seed.syx")
    assert kernels["native_vs_shared_kernel_cases"]==40
    music=read(W/"music/music-report.json")
    assert music["status"]=="PASS" and music["reference_sha256"]==REF and len(music["matrix"])==144
    assert music["current_header_sha256"]==sha(HERE/"fusion_process.h")
    with zipfile.ZipFile(R/"IMPLEMENTATION-SNAPSHOT.zip") as z:
        assert hashlib.sha256(z.read("twinshot_fusion_v2/fusion_process.h")).hexdigest()==music["baseline_header_sha256"]
        for rel in ("controls.s","pages.s","conversions.s","modules/machines/twinshot-fusion/module.toml"):
            assert z.read("twinshot_fusion_v2/"+rel)==(HERE/rel).read_bytes(),rel
        # The entire legacy type13 processing function is byte-preserved.
        assert z.read("twinshot_fusion_v2/fusion_process.h").split(b"void fusion_process(")[1]==(HERE/"fusion_process.h").read_bytes().split(b"void fusion_process(")[1]
    for reel in music["reels"]+music["control_reels"]:
        assert sha(W/"music"/reel["file"])==reel["sha256"]
    assert len(music["reels"])==16 and len(music["control_reels"])==2
    suites=list(ET.parse(W/"integration-regression.xml").iter("testsuite"))
    assert sum(int(s.attrib["tests"]) for s in suites)==7
    assert all(not int(s.attrib["failures"]) and not int(s.attrib["errors"]) for s in suites)
    # Reuse only explicitly byte-identical CF; never reuse old changed-DSP PASS.
    ui=read(R/"reports/final-ui-proof.json");panel=read(R/"reports/panel-final/panel-report.json")
    assert ui["status"]=="PASS" and ui["image_sha256"]==REF
    assert len(ui["encoder_cases"])==180 and len(ui["contexts"])==30
    assert panel["status"]=="COMPLETED" and panel["image_sha256"]==REF
    files=[p for d in (HERE,HERE.parent/"twinshot",HERE.parent/"twinshot_fusion") for p in d.rglob("*")
        if p.is_file() and not {"build","__pycache__"}.intersection(p.relative_to(d).parts)
        and p.suffix in (".c",".h",".s",".py",".toml",".md")]
    files+=list((HERE/"build").glob("*.dko.json"))
    files+=[SRC/"devices/ports/dt2-1.17.toml",SRC/"modules/core/machines/types.toml"]
    report={"status":"PASS","sha256":digest,"bytes":image.stat().st_size,"reference_sha256":REF,
        "host_head":prior["host_head"],"upstream":prior["upstream"],
        "source_sha256":{str(p):sha(p) for p in sorted(files)},
        "changed":"FUS2 DUCK/GATE envelope; peak-since-trigger, hold, useful recovery. No UI/storage change.",
        "non_DSP_sections_byte_exact_vs_Repair2":[s for s in now if s!=7],"MAIN_UI_exact":True,
        "unchanged_CF_evidence":{"binding":"entire MAIN byte-identical to Repair2, not rerun",
            "native_UI_180_encoders":sha(R/"reports/final-ui-proof.json"),
            "scheduled_panel":sha(R/"reports/panel-final/panel-report.json")},
        "fresh_native_reports":fresh,"seed_kernel_evidence":{"sha256":sha(W/"seed-dsp-proof/dsp-proof.json"),
            "binding":"actual final BLOB7 identical to tested seed"},
        "frame0_run_note":"all11 events/assertions completed and PASS report written; operator terminated host PID1387 during completion, exit TERM retained, not a firmware stall observation",
        "music_report_sha256":sha(W/"music/music-report.json"),
        "non_Fusion_DSP_patches_byte_bound":kept,"prior_plaits25_PCM":prior["plaits_25_PCM_evidence"],
        "all_loaded_DSP_bytes_outside_Fusion_allocation_exact":stock_dsp_bytes,
        "container_HMAC_packet_boot_encoding":"PASS","stock_startup_DV_WAVE0_15_Zoom_DX7_UI":"MAIN exact",
        "regions":plan["regions"],"modules":plan["modules"],"voice_bytes":1256,"extra_state_bytes":256,
        "preserved_artifacts":prior["preserved_artifacts"]+[{"path":str(prior_image),"sha256":REF}],
        "preserved_sources":prior["preserved_sources"],
        "hardware":"REPAIR3 UNTESTED; USER reports ineffective envelope interaction on Repair2",
        "limits":["host audition audio is generated shared C, not native/hardware recording",
            "native DSP instruction counts do not establish silicon timing/polyphony",
            "no coupled natural sequencer/CF/SHARC audio or full-load proof",
            "full Project filesystem/power-cycle/sample collect/remap untested",
            "saved raw values unchanged; corrected DUCK/GATE response intentionally sounds different"]}
    (W/"dsp-repair-offline-gates.json").write_text(json.dumps(report,indent=2)+"\n")
    print(json.dumps({k:report[k] for k in ("status","sha256","bytes","regions")},indent=2),flush=True)
    if not a.destination:return
    dest=a.destination.resolve();assert not dest.exists(),"never overwrite a delivery"
    dest.mkdir()
    target=dest/"DT2_OS117_ALL_IN_ONE_FUS2_DSP_REPAIR3_CANDIDATE.syx"
    shutil.copyfile(image,target);assert sha(target)==digest
    for p in W.rglob("*"):
        if not p.is_file() or p.suffix not in (".json",".xml",".log",".wav",".png"):continue
        rel=p.relative_to(W)
        if rel.parts[0]=="baseline-music":continue
        q=dest/("audio" if rel.parts[0]=="music" and p.suffix==".wav" else "reports")/rel
        if rel.parts[0]=="music" and p.suffix==".wav":q=dest/"audio"/p.name
        q.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,q)
    shutil.copyfile(HERE/"DSP-REPAIR3-NOTES.md",dest/"READ-FIRST.md")
    with zipfile.ZipFile(dest/"IMPLEMENTATION-SNAPSHOT.zip","w",zipfile.ZIP_DEFLATED) as z:
        for p in files:
            base=next((d for d in (HERE,HERE.parent/"twinshot",HERE.parent/"twinshot_fusion") if p.is_relative_to(d)),None)
            if base is not None and "build" not in p.relative_to(base).parts:
                z.write(p,str(Path(base.name)/p.relative_to(base)))
    (dest/"SOURCE-PROVENANCE.json").write_text(json.dumps(report,indent=2)+"\n")
    manifest={"status":"OFFLINE GATES PASS; HARDWARE UNTESTED","candidate":{"file":target.name,"sha256":digest,
        "bytes":target.stat().st_size},"reference_sha256":REF,"no_flash_push_publish_PR_agents":True,
        "files":{str(p.relative_to(dest)):sha(p) for p in sorted(dest.rglob("*")) if p.is_file()}}
    (dest/"DELIVERY-MANIFEST.json").write_text(json.dumps(manifest,indent=2)+"\n")
    assert len(list(dest.glob("*.syx")))==1
    print(json.dumps(manifest["candidate"],indent=2),flush=True)


if __name__=="__main__":main()
