"""INFO-only correction must not spill long descriptions into knob glyphs."""
import argparse
import hashlib
import json
import struct
from pathlib import Path
from patchkit import test_all_in_one25 as u
from tools.patchkit import pk_cfcall


def main():
    ap=argparse.ArgumentParser(__doc__)
    for key in ("reference","reference-report","report"):ap.add_argument("--"+key,type=Path,required=True)
    a=ap.parse_args();pk_cfcall._built=True
    image,build=u.CANDIDATE,u.REPORT
    new=u.fixture.__wrapped__()
    u.CANDIDATE,u.REPORT=a.reference,a.reference_report
    old=u.fixture.__wrapped__()
    u.CANDIDATE,u.REPORT=image,build
    m=pk_cfcall.CfMachine("Digitakt_II_OS1.17.syx",timeout=120)
    for sync in (0,256,512):
        frames=[]
        for p,v,s,_ in (new,old):
            edits={**p,v+0x90:bytes(4),s+0xa2:bytes([14])}
            for idx,value in {25:0x4780,26:sync,27:1024,28:7,31:0x3400,32:32512,33:31,34:32512}.items():
                edits[s+0x14+2*idx]=struct.pack(">H",value)
            frames.append(u.bits(u.checked(m.run(edits,[u.clear(),u.page_call(v)]))[-1]))
        assert frames[0]==frames[1],("INFO text leaked into SRC grid",sync)
    r={"status":"PASS","image_sha256":hashlib.sha256(image.read_bytes()).hexdigest(),
       "reference_sha256":hashlib.sha256(a.reference.read_bytes()).hexdigest(),
       "SYNC_grid_pixel_exact":[0,256,512],"tier":"actual native CF renderers; not hardware"}
    a.report.write_text(json.dumps(r,indent=2)+"\n");print("INFO-only SRC grid pixel-exact PASS",flush=True)


if __name__=="__main__":main()
