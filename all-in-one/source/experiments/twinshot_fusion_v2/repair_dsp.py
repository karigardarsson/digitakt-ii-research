"""Reuse a hash-bound unchanged DSP init, then rerun native frame/AMP playback."""
import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

from tools.patchkit.port_resolve import sections_of


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    ap = argparse.ArgumentParser(__doc__)
    for name in ("image","plan","previous","out"):
        ap.add_argument("--"+name,type=Path,required=True)
    a = ap.parse_args()
    old = a.previous/"final-integrated.syx"
    assert sha(old)=="70f19ec949041d87e88d5f950fc748fea3dadeca70dd3c792d72bb540a0c96b6"
    dsp = sections_of(a.image)[7]
    assert dsp == sections_of(old)[7]
    original = json.loads((a.previous/"full-frame/native-init.json").read_text())
    assert original["status"]=="PASS" and original["image_sha256"]==sha(old)
    a.out.mkdir(parents=True,exist_ok=True)
    state = a.previous/"full-frame/native-init.snapshot"
    shutil.copyfile(state,a.out/"native-init.snapshot")
    report = {"status":"PASS","image_sha256":sha(a.image),
        "reused_init_image_sha256":sha(old),"DSP_byte_identical":True,
        "DSP_sha256":hashlib.sha256(dsp).hexdigest(),"state_sha256":sha(state),
        "instructions":original["instructions"],
        "tier":"reused native DSP init; no fresh init or coupled hardware claim"}
    (a.out/"native-init.json").write_text(json.dumps(report,indent=2)+"\n")
    subprocess.run([sys.executable,str(Path(__file__).with_name("frame_lifecycle_proof.py")),
        "frames","--image",str(a.image),"--plan",str(a.plan),"--out",str(a.out)],check=True)


if __name__=="__main__":
    main()
