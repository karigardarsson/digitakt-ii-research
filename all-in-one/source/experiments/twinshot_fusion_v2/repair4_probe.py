"""Bounded hardware-observation probes, frozen Repair3, no candidate writes."""
import argparse
import hashlib
import json
import struct
from pathlib import Path


def ui(a):
    from patchkit import test_all_in_one25 as u
    from tools.patchkit import pk_cfcall
    pk_cfcall._built=True
    p,v,s,_=u.fixture.__wrapped__()
    m=pk_cfcall.CfMachine("Digitakt_II_OS1.17.syx",timeout=120)
    rows=[]
    for knob,idx,initial in ((0,28,20),(2,26,256),(3,27,512),(4,33,20),(6,32,16000)):
        for delta in (-8,-3,-2,-1,1,2,3,8):
            edits={**p,s+0xa2:bytes([14]),v+0x90:bytes(4)}
            for i,value in {25:0x4000,26:0,27:512,28:7,31:0x4000,32:0,33:31,34:0}.items():
                edits[s+0x14+2*i]=struct.pack(">H",value)
            edits[s+0x14+2*idx]=struct.pack(">H",initial)
            e=0x47f08000
            edits[e]=struct.pack(">5IB",0x401f10c4,0,0,knob+1,delta&0xffffffff,0)
            rr=u.checked(m.run(edits,[{"pc":0x400184ea,"args":[v,e],"max":400000,
                "reads":[(s+0x14+2*idx,2)]} for _ in range(16)]))
            sequence=[struct.unpack(">H",r.reads[0])[0] for r in rr]
            actual=sequence[-1]
            rows.append({"knob":knob,"index":idx,"delta":delta,"before":initial,"after":actual,
                "change":actual-initial,"sequence":sequence})
    print(json.dumps(rows,indent=2),flush=True)
    return {"rows":rows,"tier":"actual native SRC encoder events, not panel timing or hardware"}


def end(a):
    import numpy as np
    from native_dual_proof import TABLE
    from voice_proof import Voice
    v=Voice(a.image,a.plan,1256);v.n.put(TABLE+7*20+8,512)
    rows=[]
    for k in range(64):
        y=v.render(typ=14,trig=int(k==0),reset=int(k==0),mode=2,mix=32512,shape=0,sync=0)
        b=np.frombuffer(v.n.data(v.state+1100,128),dtype="<f4")
        if k in (0,6,7,15,31,63):
            rows.append({"block":k,"A_active":v.n.get(v.state+0x1b8,1),
                "B_active":v.n.get(v.state+472+0x1b8,1),
                "difference_from_DRY_B":float(np.max(np.abs(y-b)))})
    assert rows[-1]["A_active"]==0 and rows[-1]["B_active"]==1
    assert rows[-1]["difference_from_DRY_B"]<1e-6
    print(json.dumps(rows,indent=2),flush=True)
    return {"rows":rows,"conclusion":"DEP becomes inaudible after one-shot A while B keeps looping",
        "tier":"actual compiled Repair3/native sampler with short generated resident A and longer B; not user's hardware samples"}


if __name__=="__main__":
    ap=argparse.ArgumentParser(__doc__);ap.add_argument("action",choices=("ui","end"))
    for n in ("image","plan","report"):ap.add_argument("--"+n,type=Path,required=True)
    a=ap.parse_args();r=ui(a) if a.action=="ui" else end(a)
    r["image_sha256"]=hashlib.sha256(a.image.read_bytes()).hexdigest()
    a.report.parent.mkdir(parents=True,exist_ok=True)
    a.report.write_text(json.dumps(r,indent=2)+"\n")
