"""Repair3/4 host listening comparisons and time-domain transfer measurements.

Shared compiled C, generated PCM and host interpolation/lifecycle model. NOT
native sampler recordings or hardware evidence. Old SRC LEV double-consumption
is modeled as relative shape^2, separately measured in the real block/AMP probe.
No per-variant loudness normalization hides that defect.
"""
import argparse
import ctypes as ct
import hashlib
import json
import zipfile
from pathlib import Path
import numpy as np
from sidechain_music import Key,library,fixtures,metrics,rms
from audio_proof import SR,playback,wav

HERE=Path(__file__).resolve().parent


def run(engine,x,z,length,mode,depth,response,sync,retrigger,old=False):
    fn,_=engine
    y=np.zeros((length,2),np.float32);trace=[];positions=[]
    # Phrase-aligned repeated note-ons are distinct from a held continuous note.
    interval=SR//2 if retrigger else length
    interval=interval//32*32
    rate=1 if not sync else min(4,max(.25,len(z)/len(x)))
    for start in range(0,length,interval):
        span=min(interval,length-start)
        loop=not old and (mode in (2,4) or mode==3 and response>0)
        aa=playback(x,span,loop=loop);bb=playback(z,span,rate,loop=True)
        env=[ct.c_float(),ct.c_float()];dc=[ct.c_float(),ct.c_float()];key=[Key(),Key()]
        sweep=0
        for p in range(0,span,32):
            if response>0:sweep=min(1,sweep+(32/SR)/(.01+8*response*response))
            else:sweep=0
            for ch in (0,1):
                a,b=[np.ascontiguousarray(v[p:p+32,ch]) for v in (aa,bb)]
                out=np.zeros(32,np.float32)
                fn(a.ctypes.data,b.ctypes.data,out.ctypes.data,mode,depth,response,sweep,
                   ct.byref(env[ch]),ct.byref(dc[ch]),ct.byref(key[ch]))
                y[start+p:start+p+32,ch]=out*(response*response if old else 1)
            trace.append(env[0].value);positions.append(sweep)
    return y,np.asarray(trace),np.asarray(positions)


def main():
    ap=argparse.ArgumentParser(__doc__)
    for n in ("reference","out"):ap.add_argument("--"+n,type=Path,required=True)
    a=ap.parse_args();a.out.mkdir(parents=True,exist_ok=True)
    ref=a.reference/"DT2_OS117_ALL_IN_ONE_FUS2_DSP_REPAIR3_CANDIDATE.syx"
    digest=hashlib.sha256(ref.read_bytes()).hexdigest()
    assert digest=="19072b97513ae919af4db40d5ef83c989bc5eeff0ea93048ee9539bfdb9f4e79"
    manifest=json.loads((a.reference/"DELIVERY-MANIFEST.json").read_text())
    archive=a.reference/"IMPLEMENTATION-SNAPSHOT.zip"
    assert hashlib.sha256(archive.read_bytes()).hexdigest()==manifest["files"][archive.name]
    with zipfile.ZipFile(archive) as z:frozen=z.read("twinshot_fusion_v2/fusion_process.h")
    header=a.out/"frozen-r3.h";header.write_bytes(frozen)
    old=library(header,a.out/"frozen-r3.so");new=library(HERE/"fusion_process.h",a.out/"repair4.so")
    records=[];reels=[];cases,_=fixtures();length=SR*2
    for name,x,z,_ in cases:
        for sync in (0,1):
            bb=playback(z,length,1 if not sync else min(4,max(.25,len(z)/len(x))),loop=True)
            for repeat in (False,True):
                for mode,label in ((2,"DUCK"),(4,"GATE")):
                    for response in (0,.5,1):
                        for depth in (0,.5,1):
                            yy,tt,_=run(new,x,z,length,mode,depth,response,sync,repeat)
                            if depth==0:
                                # B restarts on actual note-ons, not at its continuous phase.
                                span=(SR//2)//32*32 if repeat else length
                                expected=np.concatenate([playback(z,min(span,length-p),
                                    1 if not sync else min(4,max(.25,len(z)/len(x))),loop=True)
                                    for p in range(0,length,span)])
                                assert np.array_equal(yy,expected)
                            records.append({"case":name,"sync":sync,"retrigger":repeat,"mode":label,
                                "response":response,"depth":depth,**metrics(yy,bb,tt)})
                    if not sync and not repeat:
                        before,_,_=run(old,x,z,length,mode,1,.5,sync,repeat,old=True)
                        after,_,_=run(new,x,z,length,mode,1,.5,sync,repeat)
                        if name in ("01_Kick_Synth","02_Rhythm_Ambient"):
                            file=f"{name}_{label}_R3_then_R4.wav"
                            wav(a.out/file,np.concatenate((before,np.zeros((SR//4,2),np.float32),after)))
                            reels.append({"file":file,"order":["Repair3 model","Repair4 model"],
                                "sha256":hashlib.sha256((a.out/file).read_bytes()).hexdigest()})
        print(name+" continuous/retrigger SYNC OFF/ON complete",flush=True)
    # Controlled recovery: same pulse peak and same B; measure decay time, not
    # average output volume. The 5ms detector hold is included in observations.
    decay=[]
    pulse=np.zeros((SR*3,2),np.float32);pulse[:4800]=.15
    carrier=np.full_like(pulse,.2)
    from sidechain_music import render
    for response in (0,.5,1):
        yy,trace=render(new,pulse,carrier,4,1,response)
        tail=trace[150:];hit=np.flatnonzero(tail<.01)
        measured=float(hit[0]*32/SR)
        nominal=.01+1.99*response*response
        assert abs(measured-(nominal+.005))<.04,(response,measured,nominal)
        decay.append({"response":response,"seconds_to_1pct":measured,"nominal":nominal,
                      "same_peak_before_release":float(np.max(yy))})
    assert decay[0]["same_peak_before_release"]==decay[1]["same_peak_before_release"]==decay[2]["same_peak_before_release"]
    # Contrast A and B by independent exact-bin tones: spectral coefficients
    # show an A-to-B transition, not merely changing total RMS/loudness.
    t=np.arange(SR//5)/SR
    x=np.repeat((.2*np.sin(2*np.pi*240*t))[:,None],2,axis=1).astype(np.float32)
    t=np.arange(SR)/SR
    z=np.repeat((.2*np.sin(2*np.pi*960*t))[:,None],2,axis=1).astype(np.float32)
    before,_,_=run(old,x,z,SR*2,3,0,.35,0,False,old=True)
    after,_,pos=run(new,x,z,SR*2,3,0,.35,0,False)
    spectrum=[]
    for begin in (0,.4,1.2):
        start=round(begin*SR);span=4800;tt=np.arange(start,start+span)/SR
        coeff=[float(2*np.dot(after[start:start+span,0],np.sin(2*np.pi*f*tt))/span) for f in (240,960)]
        spectrum.append({"seconds":begin,"A240":coeff[0],"B960":coeff[1]})
    assert spectrum[0]["A240"]>.17 and spectrum[0]["B960"]<.03
    assert abs(spectrum[-1]["A240"])<1e-6 and spectrum[-1]["B960"]>.199
    file="05_MORPH_A240_to_B960_R3_then_R4.wav"
    wav(a.out/file,np.concatenate((before,np.zeros((SR//4,2),np.float32),after)))
    reels.append({"file":file,"order":["Repair3 model","Repair4 model"],"sha256":hashlib.sha256((a.out/file).read_bytes()).hexdigest()})
    # Continuous percussion must produce dynamic gain even with SYNC OFF.
    for mode in ("DUCK","GATE"):
        rows=[r for r in records if r["case"]=="02_Rhythm_Ambient" and not r["sync"]
            and r["mode"]==mode and r["depth"]==1 and r["response"]==0]
        assert all(r["nonstatic_residual"]>.15 for r in rows),rows
    report={"status":"PASS","reference_sha256":digest,
        "baseline_header_sha256":hashlib.sha256(frozen).hexdigest(),
        "current_header_sha256":hashlib.sha256((HERE/"fusion_process.h").read_bytes()).hexdigest(),
        "matrix":records,"recovery":decay,"morph_spectral_transition":spectrum,"reels":reels,
        "tier":"host shared compiled C/lifecycle model, generated PCM; NOT native or hardware audio",
        "old_gain_model":"relative response^2, separately measured complete native AMP path; no per-variant normalization"}
    (a.out/"music-report.json").write_text(json.dumps(report,indent=2)+"\n")
    print("Repair4 music/decay/spectral gates PASS",flush=True)


if __name__=="__main__":main()
