"""Serial focused Repair4 verification, image-bound reports and retained logs.

Keep high-memory native and CF runners serial; no additional agents/process
investigation streams. Phases permit resuming completed expensive evidence.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess

HERE=Path(__file__).resolve().parent
DV=HERE.parents[1]
HOST=DV.parent/"plaits-va-run02/out/spacejam-hopt2-playable"
SRC=HOST/"out/all-in-one-plaits25-e48/source"
W=DV/"out/twinshot-fus2-repair4-work"
PY=HOST/".venv/bin/python"
IMAGE=W/"integrated-03.syx"
PLAN=W/"seed-03.plan.json"
TOOLS=DV/"out/dt2-dv-offline/tools/root"
ENV={**os.environ,"PYTHONPATH":":".join(map(str,(HOST,HERE.parent/"twinshot",HERE.parent/"twinshot_fusion",
    DV/"experiments/dt2_dv_offline",DV/".venv/lib/python3.12/site-packages")))}


def run(name,args,cwd=HOST,env=ENV):
    print("START "+name,flush=True)
    log=W/(name+".log")
    with log.open("w") as f:r=subprocess.run(list(map(str,args)),cwd=cwd,env=env,stdout=f,stderr=subprocess.STDOUT)
    if r.returncode:
        print(log.read_text()[-7000:],flush=True);raise SystemExit(r.returncode)
    print("PASS "+name,flush=True)


def main():
    global W,IMAGE,PLAN
    ap=argparse.ArgumentParser(__doc__)
    ap.add_argument("phase",choices=("dsp","cf","frames","panel","header"))
    ap.add_argument("--revision",choices=("03","04","05"),default="03");a=ap.parse_args()
    build=W/("build-"+a.revision+".json")
    if a.revision!="03":
        assert a.phase in ("cf","panel","header"),"reuse only byte-proven identical DSP03 evidence"
        IMAGE=W/("integrated-"+a.revision+".syx");PLAN=W/("seed-"+a.revision+".plan.json")
        W=W/("cf"+a.revision);W.mkdir(exist_ok=True)
    args=["--image",IMAGE,"--plan",PLAN]
    if a.phase=="header":
        env={**ENV,"PYTHONPATH":str(SRC)+":"+str(SRC/"tests")+":"+ENV["PYTHONPATH"],
            "AIO25_CANDIDATE":str(IMAGE),"AIO25_REPORT":str(build),"AIO25_UI_OUT":str(W/"ui-final")}
        run("header-05",[PY,HERE/"repair4_header.py","--plan",PLAN,"--tools",TOOLS,
            "--report",W/"header-final.json"],SRC,env)
    if a.phase=="dsp":
        run("encoding-03",[PY,"-m","tools.patchkit.pk_encoding_gate",IMAGE,PLAN])
        run("decode-03",[PY,HERE/"repair4_decode.py","--image",IMAGE,"--report",W/"decode-final.json"])
        run("kernels-03",[PY,HERE/"dsp_proof.py",*args,"--out",W/"dsp-final"])
        run("installed-03",[PY,HERE/"installed_proof.py",*args,"--v1",
            DV/"out/twinshot-all-in-one-v1/DT2_OS117_ALL_IN_ONE_TWINSHOT_V1_CANDIDATE.syx",
            "--report",W/"installed-final.json"])
        run("release-03",[PY,HERE/"voice_proof.py",*args,"--baseline",
            DV/"out/twinshot-sample-fusion-v1/DT2_OS117_ALL_IN_ONE_TWINSHOT_FUSION_V1_CANDIDATE.syx",
            "--oldplan",DV/"out/twinshot-sample-fusion-v1/validated-seed.plan.json","--report",W/"release-final.json"])
    if a.phase=="cf":
        run("wire-03",[PY,HERE/"repair4_wire.py","--image",IMAGE,"--state",HOST/"out/cache/pk_cfcall/Digitakt_II_OS1.17.state",
            "--tools",TOOLS,"--report",W/"wire-final.json"])
        run("controls-03",[PY,HERE/"repair_controls.py",*args,"--state",HOST/"out/cache/pk_cfcall/Digitakt_II_OS1.17.state",
            "--tools",TOOLS,"--report",W/"controls-final.json"])
        env={**ENV,"PYTHONPATH":str(SRC)+":"+str(SRC/"tests")+":"+ENV["PYTHONPATH"],
            "AIO25_CANDIDATE":str(IMAGE),"AIO25_REPORT":str(build),"AIO25_UI_OUT":str(W/"ui-final")}
        run("ui-03",[PY,HERE/"ui_functional.py","--plan",PLAN,"--reference",
            DV/"out/twinshot-fus2-dsp-repair3/DT2_OS117_ALL_IN_ONE_FUS2_DSP_REPAIR3_CANDIDATE.syx",
            "--reference-report",DV/"out/twinshot-fus2-dsp-repair3/reports/final-build-report.json",
            "--report",W/"ui-final.json"],SRC,env)
        run("ui-extra-03",[PY,HERE/"repair4_ui_extra.py","--plan",PLAN,"--tools",TOOLS,"--out",W/"ui-extra-final"],SRC,env)
        if a.revision!="03":
            run("info-grid-04",[PY,HERE/"repair4_info_grid.py","--reference",W.parent/"integrated-03.syx",
                "--reference-report",W.parent/"build-03.json","--report",W/"info-grid.json"],SRC,env)
        if a.revision=="05":
            run("header-05",[PY,HERE/"repair4_header.py","--plan",PLAN,"--tools",TOOLS,
                "--report",W/"header-final.json"],SRC,env)
        code="from tools.patchkit import pk_cfcall; pk_cfcall._built=True; import pytest; raise SystemExit(pytest.main("+repr([
            "-q","tests/patchkit/test_all_in_one25.py","tests/patchkit/test_all_in_one25_musical.py",
            "--junitxml="+str(W/"integration-final.xml")])+"))"
        run("integration-03",[PY,"-c",code],SRC,env)
    if a.phase=="frames":
        for mode in (2,3):
            run("frame-03-mode"+str(mode),[PY,HERE/"frame_lifecycle_proof.py","frames",*args,
                "--out",W/"full-frame-03","--mode",mode])
    if a.phase=="panel":
        run("panel-03",[PY,HERE/"panel_probe.py",*args,"--out",W/"panel-final","--cold","--context-ui",
            "--state",HOST/"out/runs/patchkit/run-plaits-lfo-probe/cards/f5c765a4acfc/ready/digi-audio-m5.snap"])
        r=json.loads((W/"panel-final/panel-report.json").read_text());assert r["status"]=="COMPLETED",r.get("error")
    print("Repair4 "+a.phase+" phase PASS",flush=True)


if __name__=="__main__":main()
