"""Bounded original FANG core oracle; not native AMP, bus or silicon proof."""
import ctypes as c
import hashlib
import itertools
import json
from pathlib import Path
import subprocess
import numpy as np

HERE=Path(__file__).resolve().parent
OUT=HERE.parents[1]/'out/all-in-one-polish-fang-r4-work'
class Frame(c.Structure):
    _fields_=[(n,c.c_float) for n in ('pitch','tune','wave','cut','res','env','dec','glide','bite','velocity')]+[(n,c.c_int) for n in ('active','trigger','release','reset')]

def main():
    OUT.mkdir(exist_ok=True)
    so=OUT/'fang.so'
    subprocess.run(['cc','-std=c99','-O2','-shared','-fPIC','-Wall','-Wextra','-Werror','-DFANG_HOST',str(HERE/'dsp/fang.c'),'-o',str(so)],check=True)
    lib=c.CDLL(str(so));lib.fang_track.argtypes=[c.c_int,c.POINTER(Frame),c.c_void_p]
    pcm=np.zeros(32,np.float32)
    def render(f,n=1,track=0):
        xs=[]
        for _ in range(n):
            lib.fang_track(track,c.byref(f),pcm.ctypes.data);xs.append(pcm.copy());f.trigger=0;f.reset=0
        return np.concatenate(xs)
    def frame(**k):
        values=dict(pitch=60*256,tune=16384,wave=0,cut=26000,res=12000,env=18000,dec=22000,glide=0,bite=14000,velocity=25600,active=1,trigger=1,release=0,reset=1)
        values.update(k);return Frame(**values)
    peaks=[]
    for cut,res,env,bite in itertools.product((0,16256,32512),repeat=4):
        f=frame(cut=cut,res=res,env=env,bite=bite)
        x=render(f,180)
        assert np.isfinite(x).all() and abs(x).max()<1.5,(cut,res,env,bite,abs(x).max())
        peaks.append(float(abs(x).max()))
    def freq(note):
        f=frame(pitch=note*256,res=0,env=0,wave=0,cut=32512,bite=0)
        x=render(f,900)[4800:]
        spec=abs(np.fft.rfft(x*np.hanning(len(x))))
        k=int(np.argmax(spec[20:])+20)
        shift=0.5*(np.log(spec[k-1])-np.log(spec[k+1]))/(np.log(spec[k-1])-2*np.log(spec[k])+np.log(spec[k+1]))
        return (k+shift)*48000/len(x)
    hz=[freq(n) for n in (48,60,72)]
    assert all(abs(h-e)/e<.01 for h,e in zip(hz,(130.8128,261.6256,523.2511)))
    f=frame();a=render(f,30);b=render(frame(),30);assert np.array_equal(a,b)
    results={}
    for key in ('wave','cut','res','env','dec','bite'):
        low=render(frame(**{key:0}),180);high=render(frame(**{key:32512}),180)
        delta=float(np.sqrt(np.mean((low-high)**2)));assert delta>1e-4,(key,delta)
        results[key]=delta
    f=frame(glide=32512);render(f,40);f.pitch=72*256;f.trigger=1;render(f)
    states=(c.c_float*(16*11)).in_dll(lib,'fang_states')
    start=states[2];assert start<523.2511/48000
    render(f,1000);end=states[2];assert end>start
    f.active=0;assert not np.any(render(f))
    f=frame();assert np.any(render(f))
    f.reset=1;f.trigger=0;assert not np.any(render(f))
    assert not np.any(render(f));f.trigger=1;assert np.any(render(f))
    render(frame(pitch=72*256),track=1);other=bytes(states)[44:88]
    render(frame(),30);assert bytes(states)[44:88]==other
    r=dict(status='PASS',source_sha256=hashlib.sha256((HERE/'dsp/fang.c').read_bytes()).hexdigest(),
        extreme_combinations=len(peaks),max_peak=max(peaks),measured_frequencies_hz=hz,
        control_delta_rms=results,glide_frequency_progress=[start,end],repeatability=True,
        inactive_retrigger_track_isolation=True,bytes_per_track=44,
        tier='host core, not native AMP / complete machine / hardware')
    (OUT/'audio-host.json').write_text(json.dumps(r,indent=2)+'\n');print(json.dumps(r,indent=2),flush=True)

if __name__=='__main__':main()
