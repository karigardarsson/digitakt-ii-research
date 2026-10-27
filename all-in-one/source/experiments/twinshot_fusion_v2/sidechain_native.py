"""Fresh repaired DSP: multi-block native playback/ABI and frozen R2 differential.

Resident generated PCM and explicit frames, not natural transport or hardware.
Never write the key state to force a result; compare its actual history to C.
"""
import argparse
import ctypes as ct
import hashlib
import json
import struct
from pathlib import Path
import numpy as np
from native_dual_proof import TABLE, PCM
from voice_proof import Voice
from sidechain_music import Key, library

HERE=Path(__file__).resolve().parent


def floats(n,p,count):
    return np.frombuffer(n.data(p,count*4),dtype="<f4").copy()


def main():
    ap=argparse.ArgumentParser(__doc__)
    for name in ("image","plan","reference","reference-plan","out"):
        ap.add_argument("--"+name,type=Path,required=True)
    a=ap.parse_args();a.out.mkdir(parents=True,exist_ok=True)
    digest=hashlib.sha256(a.image.read_bytes()).hexdigest()
    assert hashlib.sha256(a.reference.read_bytes()).hexdigest()=="b6f4c96e41e5432d7135a2d1563fe461700fe23c8b60dc6d1d81c5fa8249bee1"
    v=Voice(a.image,a.plan,1256)
    fn,repaired=library(HERE/"fusion_process.h",a.out/"kernel.so")
    assert repaired
    # Short native one-shot A actually ends; looped B remains active.
    v.n.put(TABLE+7*20+8,512)
    rows=[];sync_trace={}
    for mode in (2,4):
        for response in (0,0x3f80,0x7f00):
            for sync in (0,1,2):
                env,dc,key=ct.c_float(0),ct.c_float(0),Key()
                trace=[];ended=0;error=0
                # DEP min/mid/max changes during the same actual voice history.
                for k in range(24):
                    depth=(0,0x3f80,0x7f00)[min(2,k//3)]
                    y=v.render(typ=14,trig=int(k==0),reset=int(k==0),
                        mode=mode,mix=depth,shape=response,sync=sync,tune2=0x4700)
                    x,b=[floats(v.n,v.state+p,32) for p in (972,1100)]
                    expected=np.zeros(32,np.float32)
                    fn(x.ctypes.data,b.ctypes.data,expected.ctypes.data,mode,
                        depth/32512,response/32512,0,ct.byref(env),ct.byref(dc),ct.byref(key))
                    err=float(np.max(np.abs(y-expected)));error=max(error,err)
                    assert np.allclose(y,expected,atol=2e-6,rtol=2e-5),(mode,response,sync,k,err)
                    actual=floats(v.n,v.state+1228,1)[0]
                    assert abs(actual-env.value)<2e-5,(actual,env.value)
                    assert abs(floats(v.n,v.state+1248,1)[0]-key.peak)<2e-6
                    assert v.n.get(v.state+1252)==key.hold
                    if k>=8:
                        assert not np.any(x),"ended native A left stale PCM"
                        ended+=1
                    if not depth:
                        assert np.array_equal(y,b)
                    trace.append(float(actual))
                sync_trace[(mode,response,sync)]=trace
                rows.append({"mode":mode,"response":response,"sync":sync,"blocks":24,
                    "ended_A_blocks":ended,"max_abs_C_error":error,"key_tail":trace[-1]})
        print(f"mode {mode} native history complete",flush=True)
    for mode in (2,4):
        for response in (0,0x3f80,0x7f00):
            for sync in (1,2):
                assert np.allclose(sync_trace[mode,response,0],sync_trace[mode,response,sync],atol=1e-7)
    # Silent A closes gate; duck DEP100 is exactly dry B. Test both native sides.
    for ch in (0,1):
        base=PCM+v.n.get(TABLE+7*20+ch*4)
        for p in range(0,528,2):v.n.put(base+p,0,2)
    silent=[]
    for ch in (0,1):
        for mode in (2,4):
            y=v.render(typ=14,voice=ch,trig=1,reset=1,mode=mode,mix=0x7f00,shape=0)
            b=floats(v.n,v.state+ch*1256+1100,32)
            assert np.array_equal(y,b) if mode==2 else not np.any(y)
            silent.append((ch,mode))
    # Real frozen firmware, identical histories: other type14 modes unchanged.
    new=Voice(a.image,a.plan,1256);old=Voice(a.reference,a.reference_plan,1248)
    unchanged=[]
    for mode in (0,1,3):
        for sync,mix,response,tune in ((0,0,0,0x4000),(0,0x3f80,0x3f80,0x4700),
                                     (1,0x7f00,0x7f00,0x4c00),(2,0x5000,0x100,0x3900)):
            for ch in (0,1):
                cfg=dict(typ=14,voice=ch,mode=mode,sync=sync,mix=mix,shape=response,tune2=tune)
                for k in range(2):
                    nx=new.render(trig=int(k==0),reset=int(k==0),**cfg)
                    ox=old.render(trig=int(k==0),reset=int(k==0),**cfg)
                    assert np.array_equal(nx,ox),(cfg,k,nx.tolist(),ox.tolist())
                unchanged.append(cfg)
    report={"status":"PASS","sha256":digest,"reference_sha256":hashlib.sha256(a.reference.read_bytes()).hexdigest(),
        "native_histories":rows,"SYNC_independent_key":"identical actual histories OFF/MATCH/REL",
        "silent_A_stereo":silent,"unchanged_BLND_RING_MRPH_native_PCM":unchanged,
        "max_instructions_channel_call":max(c for _,c in v.n.cost),
        "tier":"actual compiled DSP/native sampler, resident generated PCM, reconstructed frames; NOT hardware deadlines or natural transport"}
    (a.out/"sidechain-native.json").write_text(json.dumps(report,indent=2)+"\n")
    print(json.dumps({"status":"PASS","histories":len(rows),"unchanged_cases":len(unchanged)},indent=2),flush=True)


if __name__=="__main__":main()
