"""Bounded Repair2 ended-A/sidechain probe, not a synthetic parameter-write proof.

Hypothesis: native inactive sampler leaves the last per-layer PCM untouched.
Shorten resident fixture A to256 frames; observe actual active state, raw A,
detector and output across the boundary. No user projects or firmware writes.
"""
import argparse
import json
import struct
from pathlib import Path
import numpy as np
from native_dual_proof import TABLE
from voice_proof import Voice

ap=argparse.ArgumentParser(__doc__)
for n in ("image","plan","report"):
    ap.add_argument("--"+n,type=Path,required=True)
a=ap.parse_args()
v=Voice(a.image,a.plan,1248)
v.n.put(TABLE+7*20+8,512)
rows=[]
for k in range(20):
    y=v.render(typ=14,trig=int(k==0),mode=4,mix=0x7f00,shape=0)
    raw=np.frombuffer(v.n.data(v.state+972,128),dtype="<f4")
    env=struct.unpack("<f",v.n.data(v.state+1228,4))[0]
    row={"block":k,"active_A":v.n.get(v.state+0x1b8,1),
        "frames_A":v.n.get(v.state+0x188),"phase_A":v.n.q31(v.state+0x1b0)/(1<<31),
        "A_peak":float(np.max(np.abs(raw))),"output_peak":float(np.max(np.abs(y))),"detector":env}
    rows.append(row)
    print(row,flush=True)
a.report.parent.mkdir(parents=True,exist_ok=True)
a.report.write_text(json.dumps({"observations":rows,"tier":"compiled native playback with resident synthetic PCM"},indent=2)+"\n")
