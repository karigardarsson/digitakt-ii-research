"""Deterministic Repair2/repair sidechain music and gain comparisons.

Shared compiled C, generated sources and host interpolation: NOT hardware or
native sampler audio. Native DSP tests separately bind this kernel to firmware.
"""
import argparse
import ctypes as ct
import hashlib
import json
import subprocess
import zipfile
from pathlib import Path
import numpy as np
from audio_proof import SR, source, playback, wav

HERE=Path(__file__).resolve().parent


class Key(ct.Structure):
    _fields_=[("peak",ct.c_float),("hold",ct.c_int)]


def library(header,so):
    subprocess.run(["cc","-x","c","-O2","-shared","-fPIC",str(header),"-o",str(so)],check=True)
    lib=ct.CDLL(str(so.resolve()))
    repaired=hasattr(lib,"fusion_process_v3")
    fn=lib.fusion_process_v3 if repaired else lib.fusion_process_v2
    fn.argtypes=[ct.c_void_p]*3+[ct.c_int]+[ct.c_float]*3+[ct.c_void_p]*2+([ct.c_void_p] if repaired else [])
    return fn,repaired


def render(engine,a,b,mode,depth,response):
    fn,repaired=engine
    n=len(a)
    assert n%32==0 and b.shape==a.shape
    y=np.zeros_like(a)
    env=[ct.c_float(0),ct.c_float(0)]
    dc=[ct.c_float(0),ct.c_float(0)]
    key=[Key(),Key()]
    trace=[]
    for p in range(0,n,32):
        for ch in (0,1):
            x,z=[np.ascontiguousarray(k[p:p+32,ch]) for k in (a,b)]
            out=np.zeros(32,np.float32)
            args=[x.ctypes.data,z.ctypes.data,out.ctypes.data,mode,depth,response,0,
                  ct.byref(env[ch]),ct.byref(dc[ch])]
            if repaired:
                args.append(ct.byref(key[ch]))
            fn(*args)
            y[p:p+32,ch]=out
        trace.append(env[0].value)
    assert np.isfinite(y).all() and np.max(np.abs(y))<=1.00001
    return y,np.asarray(trace)


def rms(x):
    return float(np.sqrt(np.mean(np.square(x,dtype=np.float64))))


def fixtures():
    length=SR*3
    t=np.arange(length)/SR
    synth=np.stack([.17*np.sin(2*np.pi*110*t)+.08*np.sin(2*np.pi*165*t),
                    .16*np.sin(2*np.pi*110.2*t)+.07*np.sin(2*np.pi*220*t)],axis=1).astype(np.float32)
    kick=source("kick",.2)*.25
    rhythm=np.zeros((SR*2,2),np.float32)
    for p,g in ((0,1),(.4,.8),(.8,1),(1.2,.65),(1.6,.9)):
        hit=source("kick",.12)*(.25*g)
        j=round(p*SR); rhythm[j:j+len(hit)]+=hit
    piano=source("piano",.8)*.25
    return [("01_Kick_Synth",kick,synth,1.0),
            ("02_Rhythm_Ambient",rhythm,source("air",2.5)*.25,1.0),
            ("03_Same_Sample_Tuned",piano,piano,2**(7/12)),
            ("04_Unequal_Lengths",source("vowel",.45)*.25,source("metal",.09)*.25,1.0)],length


def metrics(y,b,trace):
    scale=float(np.sum(y*b,dtype=np.float64)/max(1e-12,np.sum(b*b,dtype=np.float64)))
    return {"rms":rms(y),"peak":float(np.max(np.abs(y))),
            "best_static_gain":scale,"nonstatic_residual":rms(y-scale*b)/max(1e-9,rms(b)),
            "key_min":float(np.min(trace)),"key_max":float(np.max(trace))}


def main():
    ap=argparse.ArgumentParser(__doc__)
    ap.add_argument("--reference",type=Path,required=True)
    ap.add_argument("--out",type=Path,required=True)
    ap.add_argument("--baseline-only",action="store_true")
    a=ap.parse_args();a.out.mkdir(parents=True,exist_ok=True)
    reference_sha=hashlib.sha256((a.reference/"DT2_OS117_ALL_IN_ONE_FUS2_UI_REPAIR2_CANDIDATE.syx").read_bytes()).hexdigest()
    assert reference_sha=="b6f4c96e41e5432d7135a2d1563fe461700fe23c8b60dc6d1d81c5fa8249bee1"
    manifest=json.loads((a.reference/"DELIVERY-MANIFEST.json").read_text())
    assert hashlib.sha256((a.reference/"IMPLEMENTATION-SNAPSHOT.zip").read_bytes()).hexdigest()==manifest["files"]["IMPLEMENTATION-SNAPSHOT.zip"]
    with zipfile.ZipFile(a.reference/"IMPLEMENTATION-SNAPSHOT.zip") as z:
        frozen=z.read("twinshot_fusion_v2/fusion_process.h")
    baseline_header=a.out/"repair2-process.h";baseline_header.write_bytes(frozen)
    old=library(baseline_header,a.out/"repair2.so")
    new=old if a.baseline_only else library(HERE/"fusion_process.h",a.out/"repair3.so")
    records=[];reels=[]
    controls=[]
    cases,length=fixtures()
    for name,x,z,tune in cases:
        aa=playback(x,length)
        for sync in (0,1):
            rate=tune if not sync else min(4,max(.25,len(z)/len(x)))
            bb=playback(z,length,rate,loop=True)
            for mode,label in ((2,"DUCK"),(4,"GATE")):
                for depth in (0,.5,1):
                    for response in (0,.5,1):
                        before,et=render(old,aa,bb,mode,depth,response)
                        after,nt=render(new,aa,bb,mode,depth,response)
                        bm,nm=metrics(before,bb,et),metrics(after,bb,nt)
                        if depth==0:
                            assert np.array_equal(before,bb) and np.array_equal(after,bb)
                        if depth==1 and response==.5:
                            filename=f"{name}_{label}_SYNC_{'ON' if sync else 'OFF'}_Repair2_vs_Repair3.wav"
                            if not a.baseline_only:
                                wav(a.out/filename,np.concatenate((bb,np.zeros((SR//4,2),np.float32),
                                    before,np.zeros((SR//4,2),np.float32),after)))
                                reels.append({"file":filename,"order":["dry B","Repair2","Repair3"],
                                    "sha256":hashlib.sha256((a.out/filename).read_bytes()).hexdigest()})
                        records.append({"case":name,"sync":sync,"mode":label,"depth":depth,
                            "response":response,"repair2":bm,"repair3":nm})
                if not a.baseline_only and name=="02_Rhythm_Ambient" and not sync:
                    parts=[]
                    for response in (0,.5,1):
                        for depth in (0,.5,1):
                            yy,_=render(new,aa,bb,mode,depth,response)
                            parts.extend((yy,np.zeros((SR//4,2),np.float32)))
                    filename=f"{name}_{label}_DEP_RCVR_GRID.wav"
                    wav(a.out/filename,np.concatenate(parts))
                    controls.append({"file":filename,"order":"RCVR 0/50/100; within each DEP 0/50/100",
                        "sha256":hashlib.sha256((a.out/filename).read_bytes()).hexdigest()})
        print(name+" matrix complete",flush=True)
    # Actual key decay, independent of a carrier's phase and B amplitude.
    decay=[]
    for response in (0,.5,1):
        size=SR*11;aa=np.zeros((size,2),np.float32);aa[:SR//10]=.15
        bb=np.full_like(aa,.2)
        row={"response":response,"nominal_seconds":.01+1.99*response*response}
        for name,eng in (("repair2",old),("repair3",new)):
            yy,tt=render(eng,aa,bb,4,1,response)
            tail=tt[SR//10//32:]
            hits=np.flatnonzero(tail<.01)
            row[name+"_seconds_to_1pct"]=float(hits[0]*32/SR) if len(hits) else None
        decay.append(row)
        if not a.baseline_only:
            assert abs(row["repair3_seconds_to_1pct"]-(row["nominal_seconds"]+.005))<.04, row
            assert row["repair3_seconds_to_1pct"]<row["repair2_seconds_to_1pct"],row
    # No sidechain repair is allowed to alter clean balance/ring/morph output.
    unchanged=[]
    rng=np.random.default_rng(73)
    aa=rng.uniform(-.3,.3,(3200,2)).astype(np.float32)
    bb=rng.uniform(-.3,.3,(3200,2)).astype(np.float32)
    for mode in (0,1,3):
        for depth in (0,.5,1):
            for response in (0,.5,1):
                before,_=render(old,aa,bb,mode,depth,response)
                after,_=render(new,aa,bb,mode,depth,response)
                assert np.array_equal(before,after),(mode,depth,response)
                unchanged.append((mode,depth,response))
    if not a.baseline_only:
        # Rhythmic-key transfer must be time-varying, not a static B gain.
        for mode in ("DUCK","GATE"):
            rows=[r for r in records if r["case"]=="02_Rhythm_Ambient" and
                  r["mode"]==mode and r["depth"]==1 and r["response"]==0]
            assert len(rows)==2 and all(r["repair3"]["nonstatic_residual"]>.15 for r in rows),rows
        # Above the floor, scaling the same key does not flatten its envelope.
        aa=playback(cases[0][1],length);bb=playback(cases[0][2],length,loop=True)
        normal,_=render(new,aa,bb,4,1,0)
        quiet,_=render(new,aa*.1,bb,4,1,0)
        assert np.allclose(normal,quiet,atol=2e-7)
        silent,_=render(new,np.zeros_like(aa),bb,4,1,0)
        assert not np.any(silent)
    report={"status":"BASELINE_MEASURED" if a.baseline_only else "PASS",
        "reference_sha256":reference_sha,
        "baseline_header_sha256":hashlib.sha256(frozen).hexdigest(),
        "current_header_sha256":hashlib.sha256((HERE/"fusion_process.h").read_bytes()).hexdigest(),
        "matrix":records,"decay":decay,"reels":reels,"control_reels":controls,
        "BLEND_RING_MORPH_bit_exact_cases":unchanged,
        "source_level":"common generated PCM, no per-render normalization",
        "tier":"compiled shared C with generated stereo sources/host playback; NOT native or hardware audio"}
    (a.out/"music-report.json").write_text(json.dumps(report,indent=2)+"\n")
    print(json.dumps({"status":report["status"],"decay":decay,"cases":len(records)},indent=2),flush=True)


if __name__=="__main__":
    main()
