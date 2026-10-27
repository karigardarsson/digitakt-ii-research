"""Actual Repair4 DSP: held interaction, live entry, recovery gaps and ownership.

Generated resident PCM and explicit frames; not natural transport/hardware.
"""
import argparse
import hashlib
import json
import struct
from pathlib import Path
import numpy as np
from native_dual_proof import TABLE,PCM,REC
from voice_proof import Voice


def raw(v,offset):return np.frombuffer(v.n.data(v.state+offset,128),dtype="<f4").copy()


def main():
    ap=argparse.ArgumentParser(__doc__)
    ap.add_argument("--quick",action="store_true",help="focused final-image matrix; retain full prototype matrix separately")
    for n in ("image","plan","report"):ap.add_argument("--"+n,type=Path,required=True)
    a=ap.parse_args();v=Voice(a.image,a.plan,1260)
    v.n.put(TABLE+7*20+8,1024)
    # A repeats a burst followed by actual silence, not a constant DC key.
    for side in (0,1):
        address=PCM+v.n.get(TABLE+7*20+4*side)
        for i in range(128,520):v.n.put(address+2*i,0,2)
    matrix=[]
    for mode in (2,4):
        for sync in (0,1):
            for response in (0,16256,32512):
                for depth in (0,16256,32512):
                    if a.quick and (sync or (response,depth) not in ((0,0),(0,32512),(32512,32512))):continue
                    gains=[];env=[]
                    for k in range(32):
                        y=v.render(typ=14,trig=int(k==0),reset=int(k==0),mode=mode,
                            sync=sync,shape=response,mix=depth)
                        b=raw(v,1100)
                        if not depth:assert np.array_equal(y,b)
                        gains.append(float(np.sum(y*b)/max(1e-12,np.sum(b*b))))
                        env.append(struct.unpack("<f",v.n.data(v.state+1228,4))[0])
                    assert v.n.get(v.state+0x1b8,1)==v.n.get(v.state+REC+0x1b8,1)==1
                    if depth==32512 and response==0:
                        assert max(gains)-min(gains)>.1,(mode,sync,gains)
                    matrix.append({"mode":mode,"sync":sync,"response":response,"depth":depth,
                        "gain":gains,"envelope":env,"both_layers_still_active":True})
        print("held native mode "+str(mode)+" PASS",flush=True)
    # User's musical report did not distinguish held DEP edits from note-ons.
    # Exercise BOTH, including SYNC OFF and independent short A/looped B.
    edits=[]
    for mode in (2,4):
        for sync in (0,1):
            for repeat in (False,True):
                changes=[]
                for k in range(48):
                    depth=(0,16256,32512)[k//16]
                    y=v.render(typ=14,mode=mode,sync=sync,mix=depth,shape=0,
                        trig=int(k==0 or repeat and k%16==0),reset=int(k==0))
                    b=raw(v,1100)
                    difference=float(np.max(np.abs(y-b)))
                    if not depth:assert np.array_equal(y,b)
                    changes.append(difference)
                assert max(changes[16:32])>1e-4 and max(changes[32:])>1e-4,(mode,sync,repeat)
                edits.append({"mode":mode,"sync":sync,"retrigger_each_DEP_change":repeat,
                    "DEP0_exact":True,"mid_max_difference":max(changes[16:32]),
                    "full_max_difference":max(changes[32:])})
    # Live MODE entry must recover an ended A, without touching B's cursor.
    for k in range(24):v.render(typ=14,mode=0,trig=int(k==0),reset=int(k==0))
    assert v.n.get(v.state+0x1b8,1)==0 and v.n.get(v.state+REC+0x1b8,1)==1
    before=v.n.q31(v.state+REC+0x1b0)
    v.render(typ=14,mode=2,shape=0)
    assert v.n.get(v.state+0x1b8,1)==1
    assert v.n.q31(v.state+REC+0x1b0)!=before
    v.render(typ=14,mode=2,shape=0)
    assert np.max(np.abs(raw(v,972)))>0
    # A release latch prohibits any later MODE change from rearming the key.
    v.render(typ=14,mode=0,release=1)
    for _ in range(24):v.render(typ=14,mode=0)
    v.render(typ=14,mode=4)
    assert v.n.get(v.state+0x1b8,1)==0
    # Metadata invalidation is never a permission to restart a stale record.
    for k in range(24):v.render(typ=14,mode=0,trig=int(k==0),reset=int(k==0))
    v.n.put(TABLE+7*20+8,2048)
    v.render(typ=14,mode=2)
    assert v.n.get(v.state+0x1b8,1)==0
    v.n.put(TABLE+7*20+8,1024)
    # A clock must not run invisibly in another FUSE mode. An ordinary live
    # entry to MORPH starts a transition even after short A has already ended.
    for k in range(32):v.render(typ=14,mode=0,shape=128,trig=int(k==0),reset=int(k==0))
    assert struct.unpack("<f",v.n.data(v.state+1244,4))[0]==0
    v.render(typ=14,mode=3,mix=0,shape=128)
    entry_sweep=struct.unpack("<f",v.n.data(v.state+1244,4))[0]
    assert 0<entry_sweep<.1,entry_sweep
    # Auto fade actually retains short A through the transition. Fast endpoint
    # is B exactly; slow AUTO must not lose A before its nominal duration.
    morph=[]
    for shape in (0,128,32512):
        for k in range(32):
            y=v.render(typ=14,mode=3,mix=0,shape=shape,trig=int(k==0),reset=int(k==0))
        active=v.n.get(v.state+0x1b8,1)
        sweep=struct.unpack("<f",v.n.data(v.state+1244,4))[0]
        if not shape:assert active==0 and sweep==0
        elif shape==128:assert active==1 and sweep==1 and np.array_equal(y,raw(v,1100))
        else:assert active==1 and 0<sweep<.01
        morph.append({"auto_raw":shape,"A_active_after_32_blocks":active,"sweep":sweep})
    report={"status":"PASS","sha256":hashlib.sha256(a.image.read_bytes()).hexdigest(),
        "matrix":matrix,"live_DEP_changes":edits,"live_entry_rearm":"PASS, valid metadata only, B not retriggered",
        "release_and_loader_prohibit_rearm":"PASS","morph":morph,
        "AUTO_clock_other_modes_zero_and_live_MORPH_entry":entry_sweep,"quick":a.quick,
        "max_instructions":max(c for _,c in v.n.cost),
        "tier":"native compiled DSP and resident generated sample playback; hardware and natural transport UNTESTED"}
    a.report.write_text(json.dumps(report,indent=2)+"\n")
    print("Repair4 native held/lifecycle/auto proof PASS",flush=True)


if __name__=="__main__":main()
