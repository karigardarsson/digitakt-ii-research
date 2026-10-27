"""Repair4 release gates and one-candidate packaging; no hardware operations."""
import argparse
import hashlib
import json
import shutil
import struct
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from tools.patchkit import pk_encoding_gate,port_resolve
from tools.patchkit import pk_integrate_dv_zoom as dv
from tools.sharc_dev import sharc_bootcheck
from tools.sharc_dev.sharcldr import LoadedMemory

HERE=Path(__file__).resolve().parent
DV=HERE.parents[1]
HOST=DV.parent/"plaits-va-run02/out/spacejam-hopt2-playable"
SRC=HOST/"out/all-in-one-plaits25-e48/source"
W=DV/"out/twinshot-fus2-repair4-work"
R=DV/"out/twinshot-fus2-dsp-repair3"
REF="19072b97513ae919af4db40d5ef83c989bc5eeff0ea93048ee9539bfdb9f4e79"

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def read(p):return json.loads(p.read_text())
def sym(p,s):return next(v for k,v in p["symbols"].items() if k.endswith(":"+s))


def main():
    ap=argparse.ArgumentParser(__doc__);ap.add_argument("--destination",type=Path)
    ap.add_argument("--static-only",action="store_true");a=ap.parse_args()
    image=W/"integrated-05.syx";planfile=W/"seed-05.plan.json";digest=sha(image)
    CF=W/"cf05"
    dsp_image=W/"integrated-03.syx";dsp_digest=sha(dsp_image)
    prior_image=R/"DT2_OS117_ALL_IN_ONE_FUS2_DSP_REPAIR3_CANDIDATE.syx"
    assert sha(prior_image)==REF and digest!=REF
    prior=read(R/"SOURCE-PROVENANCE.json")
    for name,value in read(R/"DELIVERY-MANIFEST.json")["files"].items():assert sha(R/name)==value,name
    for item in prior["preserved_artifacts"]:assert sha(Path(item["path"]))==item["sha256"],item
    for path,value in prior["preserved_sources"].items():assert sha(Path(path))==value,path
    now,old,seed,stock=[port_resolve.sections_of(p) for p in (image,prior_image,W/"seed-05.syx",SRC/"firmware/Digitakt_II_OS1.17.syx")]
    assert now[7]==port_resolve.sections_of(dsp_image)[7],"no reuse if one DSP byte differs"
    assert set(now)==set(old)==set(stock)
    for sid in (2,4,5,8):assert now[sid]==old[sid]==stock[sid],sid
    assert now[3]!=old[3] and now[7]!=old[7] and now[7]==seed[7]
    assert not pk_encoding_gate.check_image(image,planfile)
    assert sharc_bootcheck.check(now[7]).ok
    assert not [f for f in sharc_bootcheck.check_against_stock(stock[7],now[7]) if f.level=="error"]
    plan=read(planfile);oldplan=read(R/"reports/final-seed.plan.json")
    build=read(W/"build-05.json");module=read(W/"module-build-05.json")
    assert build["candidate"]["sha256"]==digest
    integrity=build["candidate"]["integrity"]
    assert integrity["file_sha256"]==digest
    assert integrity["packet_checksums"]==f'{integrity["message_count"]}/{integrity["message_count"]}'
    assert all(r["result"]=="PASS" for r in build["candidate"]["bootstrap"].values())
    for name in ("fusion_process.h","fusion_native.c","adapter.c","entry.s","conversions.s","controls.s","pages.s"):
        assert module["sources"][str(HERE/name)]==sha(HERE/name),name
    for name,value in module["objects"].items():assert sha(HERE/"build"/name)==value,name
    stage=HERE/"build/apply-root"
    for name,record in module["unambiguous_modules"].items():
        assert sha(stage/"modules"/name/"module.toml")==record["sha256"],name
    assert plan["modules"]==oldplan["modules"]
    assert not any("arp" in x.lower() or "splash" in x.lower() for x in plan["modules"])
    assert all(used<=cap for used,cap in plan["regions"].values())

    # Every non-Fusion loaded DSP byte is preserved, except the deliberately
    # pinned native SRC-LEV hook and the TWO Fusion registry PC relocations.
    # Never allow an entire shared table/firmware section as an exclusion.
    before,after=LoadedMemory.from_stream(old[7]),LoadedMemory.from_stream(now[7])
    ranges=[(x["addr"],x["addr"]+x["len"]) for p in (plan,oldplan) for x in p["writes"]
        if x["cpu"]=="sharc" and x["owner"].startswith("machines/twinshot-fusion@")]
    table=sym(plan,"inline_voices.rows");oldtable=sym(oldplan,"inline_voices.rows")
    assert table==oldtable
    dispatch=[]
    for row,typ in ((2,13),(3,14)):
        addr=table+row*8
        assert struct.unpack("<II",before.read(addr,8))==(typ,sym(oldplan,"_tf_render"))
        assert struct.unpack("<II",after.read(addr,8))==(typ,sym(plan,"_tf_render"))
        ranges.append((addr+4,addr+8));dispatch.append({"type":typ,"address":addr+4,"pc":sym(plan,"_tf_render")})
    kept_bytes=0
    for block in before.blocks:
        spans=[(block["target_address"],block["target_address"]+block["byte_count"])]
        for start,end in ranges:
            spans=[piece for lo,hi in spans for piece in ((lo,min(hi,start)),(max(lo,end),hi)) if piece[0]<piece[1]]
        for lo,hi in spans:
            for start in range(lo,hi,32768):
                size=min(32768,hi-start)
                assert before.read(start,size)==after.read(start,size),(hex(start),size)
                kept_bytes+=size
    level=next(x for x in plan["writes"] if x["what"]=="hook fusion2.level_decode+0")
    assert level["addr"]==0x28384ca8 and level["len"]==6
    assert level["old_sha256"]=="43243e87b9033c6ff0d2db16acda05a8a70fa82ee1b1ee9b2e58f27b155f2e00"
    assert hashlib.sha256(before.read(level["addr"],6)).hexdigest()==level["old_sha256"]
    assert after.read(level["addr"],6).hex()==level["new_hex"]

    def cf(main,addr,n):return dv.at(main,dv.source_for_loaded_address(main,addr),n)
    oldbuild=read(R/"reports/final-build-report.json")
    for addr,size in ((0x47ec0000,dv.MUSICAL_PAYLOAD_BYTES),(0x47ea0000,oldbuild["ui"]["bytes"])):
        assert cf(now[3],addr,size)==cf(old[3],addr,size),(hex(addr),size)
    assert dv.at(now[3],dv.INTRO_SCENES,80)==dv.STOCK_INTRO_SCENES
    for row in range(311):
        assert cf(now[3],sym(plan,"pf_table")+60*row,40)==cf(old[3],sym(oldplan,"pf_table")+60*row,40),row
    header=next(x for x in plan["writes"] if x["cpu"]=="cf" and x["addr"]==0x4006ce92)
    assert header["owner"]=="machines/plaits@0.1.0" and header["len"]==6
    assert header["new_hex"]==struct.pack(">HI",0x4ef9,sym(plan,"fusion2_header_text")).hex()
    assert sum(x["cpu"]=="cf" and x["addr"]==0x4006ce92 for x in plan["writes"])==1
    for row in (311,312,313):
        d=struct.unpack(">15I",cf(now[3],sym(plan,"pf_table")+60*row,60))
        assert d[13]==sym(plan,"fusion2_sync_fmt"),"full INFO must never reach grid formatter"
    with zipfile.ZipFile(R/"IMPLEMENTATION-SNAPSHOT.zip") as z:
        assert z.read("twinshot_fusion_v2/fusion_process.h")==(HERE/"fusion_process.h").read_bytes()

    if a.static_only:
        assert not a.destination
        r={"status":"STATIC GATES PASS; runtime gates not asserted","sha256":digest,
            "DSP_byte_exact_to_runtime_test_image":dsp_digest,"non_Fusion_DSP_preserved_bytes":kept_bytes,
            "explicit_dispatch_relocations":dispatch,"stock_sections_startup_DV_Zoom_Plaits_UI":"PASS"}
        (W/"static-final.json").write_text(json.dumps(r,indent=2)+"\n");print(json.dumps(r),flush=True);return

    fresh={}
    for name,field in (("decode-final.json","sha256"),("dsp-final/dsp-proof.json","sha256"),
        ("installed-final.json","sha256"),("release-final.json","image_sha256"),
        ("native-final-03.json","sha256"),("full-frame-03/native-init.json","image_sha256"),
        ("full-frame-03/frame-lifecycle-mode2.json","sha256"),("full-frame-03/frame-lifecycle-mode3.json","sha256")):
        r=read(W/name);assert r["status"]=="PASS" and r[field]==dsp_digest,name;fresh[name]=sha(W/name)
    cf_reports={}
    for name,field in (("controls-final.json","image_sha256"),("wire-final.json","sha256"),
        ("ui-final.json","image_sha256"),("ui-extra-final/ui-extra.json","image_sha256"),("info-grid.json","image_sha256"),
        ("header-final.json","image_sha256")):
        r=read(CF/name);assert r["status"]=="PASS" and r[field]==digest,name;cf_reports[name]=sha(CF/name)
    baseline_gain=read(W/"full-frame/level-02.json")
    assert not baseline_gain["response_independent_DEP0"]
    gain=read(W/"full-frame-03/level-03.json")
    assert gain["sha256"]==dsp_digest and gain["response_independent_DEP0"]
    native=read(W/"native-final-03.json");assert len(native["live_DEP_changes"])==8
    assert read(W/"decode-final.json")["cases"]==768
    assert len(read(CF/"wire-final.json")["rows"])==27
    music=read(W/"music/music-report.json")
    assert music["status"]=="PASS" and music["reference_sha256"]==REF and len(music["matrix"])==288
    assert music["current_header_sha256"]==sha(HERE/"fusion_process.h")
    for reel in music["reels"]:assert sha(W/"music"/reel["file"])==reel["sha256"]
    panel=read(CF/"panel-final/panel-report.json")
    assert panel["status"]=="COMPLETED" and panel["image_sha256"]==digest
    assert all("SYNC "+s+" help" in [r["label"] for r in panel["events"]] for s in ("OFF","MATCH","REL"))
    suites=list(ET.parse(CF/"integration-final.xml").iter("testsuite"))
    assert sum(int(s.attrib["tests"]) for s in suites)==7
    assert all(not int(s.attrib["failures"]) and not int(s.attrib["errors"]) for s in suites)
    files=[p for root in (HERE,HERE.parent/"twinshot",HERE.parent/"twinshot_fusion") for p in root.rglob("*")
        if p.is_file() and not {"build","__pycache__"}.intersection(p.relative_to(root).parts)
        and p.suffix in (".c",".h",".s",".py",".toml",".md")]
    files+=list((HERE/"build").glob("*.dko.json"))
    files+=[SRC/"devices/ports/dt2-1.17.toml",SRC/"modules/core/machines/types.toml"]
    integration_files=[stage/"devices/ports/dt2-1.17.toml"]+[stage/"modules"/n/"module.toml" for n in module["unambiguous_modules"]]
    report={"status":"PASS","sha256":digest,"bytes":image.stat().st_size,"reference_sha256":REF,
        "host_head":prior["host_head"],"upstream":prior["upstream"],
        "changes":["type14-only native SRC LEV ownership isolation; unchanged raw response field",
            "participating A key/AUTO native looping; guarded live rearm; MORPH-only clock",
            "native tuning glyphs, bounded discrete steps, contextual safe INFO and time readouts"],
        "source_sha256":{str(p):sha(p) for p in sorted(files)},"modules":plan["modules"],"regions":plan["regions"],
        "staged_integration_sha256":{str(p.relative_to(stage)):sha(p) for p in integration_files},
        "voice_bytes":1260,"extra_state_bytes_vs_Repair3":128,
        "non_Fusion_loaded_DSP_bytes_preserved":kept_bytes,"explicit_shared_dispatch_relocations":dispatch,
        "new_native_hook":level,"single_owner_header_chain":header,"old_processing_kernel_source_byte_exact":True,
        "original_saved_metadata_rows_preserved":311,"stock_sections_preserved":[2,4,5,8],
        "Musical_WAVE_and_Zoom_payload_exact":True,"Plaits25_DX7_UI_payload_exact":True,
        "original_Elektron_intro_exact":True,"native_DSP_reports":fresh,"fresh_CF_reports":cf_reports,
        "DSP_runtime_evidence_binding":{"image_sha256":dsp_digest,"entire_BLOB7_byte_exact_to_final":True,
            "final_difference":"INFO-only CF text/formatting; native output frame ABI retested on final"},
        "response_gain_full_native":"DEP0 post-AMP byte-identical across raw0/mid/max; pre-fix fails",
        "music_report_sha256":sha(W/"music/music-report.json"),"native_panel_report_sha256":sha(CF/"panel-final/panel-report.json"),
        "container_HMAC_packet_boot_encoding":"PASS","prior_plaits25_PCM":prior["prior_plaits25_PCM"],
        "preserved_artifacts":prior["preserved_artifacts"]+[{"path":str(prior_image),"sha256":REF,"evidence":"USER evaluated; DSP/control failures reported"}],
        "preserved_sources":prior["preserved_sources"],"hardware":"Repair4 UNTESTED",
        "limits":["generated host audio is shared-C/lifecycle modeling, not native or physical recordings",
            "native frame events are reconstructed; no coupled natural transport/sequencer/CF/SHARC musical proof",
            "no silicon full-track/polyphony deadline or physical encoder-feel proof",
            "Sound/Pattern conversion tested offline; full Project filesystem/power-cycle/collect/remap remains untested",
            "saved FUS2 sounds can be louder after unintended SRC-level attenuation is removed; begin with low VOL"]}
    (W/"repair4-offline-gates.json").write_text(json.dumps(report,indent=2)+"\n")
    print(json.dumps({k:report[k] for k in ("status","sha256","bytes","regions")},indent=2),flush=True)
    if not a.destination:return
    dest=a.destination.resolve();assert not dest.exists(),"never overwrite a delivery";dest.mkdir()
    target=dest/"DT2_OS117_ALL_IN_ONE_FUS2_REPAIR4_CANDIDATE.syx"
    shutil.copyfile(image,target);assert sha(target)==digest
    for p in W.rglob("*"):
        rel=p.relative_to(W)
        if not p.is_file() or p.suffix not in (".json",".xml",".log",".wav",".png"):continue
        if p.suffix==".wav" and rel.parts[0]!="music":continue
        q=dest/"audio"/p.name if p.suffix==".wav" else dest/"reports"/rel
        q.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,q)
    shutil.copyfile(HERE/"USER-GUIDE.md",dest/"READ-FIRST.md")
    with zipfile.ZipFile(dest/"IMPLEMENTATION-SNAPSHOT.zip","w",zipfile.ZIP_DEFLATED) as z:
        for p in files:
            root=next((r for r in (HERE,HERE.parent/"twinshot",HERE.parent/"twinshot_fusion") if p.is_relative_to(r)),None)
            if root:z.write(p,str(Path(root.name)/p.relative_to(root)))
        for p in (HOST/"docs/findings/twinshot-sample-fusion-v2.md",DV.parent/"plaits-va-run02/docs/DT2_ECOSYSTEM_MAP.md"):
            z.write(p,"findings/"+p.name)
        for p in integration_files:z.write(p,str(Path("integration")/p.relative_to(stage)))
    (dest/"SOURCE-PROVENANCE.json").write_text(json.dumps(report,indent=2)+"\n")
    manifest={"status":"OFFLINE GATES PASS; HARDWARE UNTESTED","candidate":{"file":target.name,"sha256":digest,"bytes":target.stat().st_size},
        "reference_sha256":REF,"no_flash_push_publish_PR_agents":True,
        "files":{str(p.relative_to(dest)):sha(p) for p in sorted(dest.rglob("*")) if p.is_file()}}
    (dest/"DELIVERY-MANIFEST.json").write_text(json.dumps(manifest,indent=2)+"\n")
    assert len(list(dest.glob("*.syx")))==1;print(json.dumps(manifest["candidate"],indent=2),flush=True)


if __name__=="__main__":main()
