"""Focused, serial Repair3 final-image validation with retained command logs."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

HERE=Path(__file__).resolve().parent
DV=HERE.parents[1]
HOST=DV.parent/"plaits-va-run02/out/spacejam-hopt2-playable"
SRC=HOST/"out/all-in-one-plaits25-e48/source"
W=DV/"out/twinshot-fus2-dsp-repair3-work"
PY=HOST/".venv/bin/python"
IMAGE=W/"final-integrated.syx"
PLAN=W/"final-seed.plan.json"
ENV={**os.environ,"PYTHONPATH":":".join(map(str,(HOST,HERE.parent/"twinshot",HERE.parent/"twinshot_fusion",
    DV/"experiments/dt2_dv_offline",DV/".venv/lib/python3.12/site-packages")))}
RESUME=None
ACTIVE=True


def run(name,argv,cwd=HOST,env=ENV):
    global ACTIVE
    if not ACTIVE:
        if name==RESUME:ACTIVE=True
        print("RETAIN existing phase "+name,flush=True)
        return
    print("START "+name,flush=True)
    with (W/(name+".log")).open("w") as f:
        result=subprocess.run(list(map(str,argv)),cwd=cwd,env=env,stdout=f,stderr=subprocess.STDOUT)
    if result.returncode:
        print((W/(name+".log")).read_text()[-6000:],flush=True)
        raise SystemExit(result.returncode)
    print("PASS "+name,flush=True)


def main():
    global RESUME,ACTIVE
    ap=argparse.ArgumentParser(__doc__)
    ap.add_argument("--resume-after",choices=("native-frame-0","native-frame-2","native-frame-4","controls"))
    args=ap.parse_args();RESUME=args.resume_after;ACTIVE=RESUME is None
    # Final release gates independently bind every retained report to IMAGE.
    run("encoding",[PY,"-m","tools.patchkit.pk_encoding_gate",IMAGE,PLAN])
    run("final-installed",[PY,HERE/"installed_proof.py","--image",IMAGE,"--plan",PLAN,
        "--v1",DV/"out/twinshot-all-in-one-v1/DT2_OS117_ALL_IN_ONE_TWINSHOT_V1_CANDIDATE.syx",
        "--report",W/"final-installed-proof.json"])
    run("final-release",[PY,HERE/"voice_proof.py","--image",IMAGE,"--plan",PLAN,
        "--baseline",DV/"out/twinshot-sample-fusion-v1/DT2_OS117_ALL_IN_ONE_TWINSHOT_FUSION_V1_CANDIDATE.syx",
        "--oldplan",DV/"out/twinshot-sample-fusion-v1/validated-seed.plan.json",
        "--report",W/"final-release-proof.json"])
    run("native-init",[PY,HERE/"frame_lifecycle_proof.py","init","--image",IMAGE,"--plan",PLAN,
        "--out",W/"full-frame"])
    for mode in (0,2,4):
        run("native-frame-"+str(mode),[PY,HERE/"frame_lifecycle_proof.py","frames","--image",IMAGE,
            "--plan",PLAN,"--out",W/"full-frame","--mode",mode])
    run("controls",[PY,HERE/"repair_controls.py","--image",IMAGE,"--plan",PLAN,
        "--state",HOST/"out/cache/pk_cfcall/Digitakt_II_OS1.17.state",
        "--tools",DV/"out/dt2-dv-offline/tools/root","--report",W/"final-control-proof.json"])
    env={**ENV,"PYTHONPATH":str(SRC)+":"+str(SRC/"tests")+":"+ENV["PYTHONPATH"],
        "AIO25_CANDIDATE":str(IMAGE),"AIO25_REPORT":str(W/"final-build-report.json"),"AIO25_UI_OUT":str(W/"ui")}
    code="from tools.patchkit import pk_cfcall; pk_cfcall._built=True; import pytest; raise SystemExit(pytest.main("+repr([
        "-q","tests/patchkit/test_all_in_one25.py","tests/patchkit/test_all_in_one25_musical.py",
        "--junitxml="+str(W/"integration-regression.xml")])+"))"
    run("integration",[PY,"-c",code],SRC,env)
    print("FOCUSED FINAL IMAGE SUITE PASS",flush=True)


if __name__=="__main__":main()
