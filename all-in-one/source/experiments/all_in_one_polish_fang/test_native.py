"""Execute the installed FANG inline entry through the existing native rig."""
import argparse
import ctypes as c
import hashlib
import json
import struct
from pathlib import Path
import numpy as np
from native_dual_proof import Native,REC,STACK,OUT
from tools.patchkit.port_resolve import sections_of
from tools.sharc_dev.sharcldr import LoadedMemory
from tools.sharc_dev import sharc_trace as st

HERE=Path(__file__).resolve().parent
WORK=HERE.parents[1]/'out/all-in-one-polish-fang-r4-work'
class Frame(c.Structure):
    _fields_=[(n,c.c_float) for n in ('pitch','tune','wave','cut','res','env','dec','glide','bite','velocity')]+[(n,c.c_int) for n in ('active','trigger','release','reset')]

def main():
    ap=argparse.ArgumentParser(__doc__);ap.add_argument('--image',type=Path,default=WORK/'fang-seed.syx');a=ap.parse_args()
    plan=json.loads((WORK/'fang-seed.plan.json').read_text())
    sym=lambda name:next(v for k,v in plan['symbols'].items() if k.endswith(':'+name))
    n=Native(LoadedMemory.from_stream(sections_of(a.image)[7]),approx_recips=True)
    cstack=sym('_fang_stack')
    for i in range(1024):n.put(cstack+4*i,0xa5a5a5a5)
    lib=c.CDLL(str(WORK/'fang.so'));lib.fang_track.argtypes=[c.c_int,c.POINTER(Frame),c.c_void_p]
    protected={k:0x150+i for i,k in enumerate(('I8','I9','I10','I11','I13','I14','I15','M0','M8','M9','M10','M11','M12','B3'))}
    circular=dict(B6=STACK-0x400,B7=STACK-0x400,L6=0x800,L7=0x800,MODE1=0x1011800)
    def block(track=0,pitch=60,wave=0,cut=16384,res=18432,env=20480,dec=14336,glide=0,bite=16384,trig=0,active=1,reset=0,release=0):
        voice=track*2
        values=(16384,wave,cut,0,res,0,env,dec,glide,bite)
        n.put(0x255970+2*track,16,2);n.put(0x2558dc+2*(1+track),pitch*256,2)
        n.put(0x2558dc+2*(0x1a+track),25600,2);n.put(0x2558dc+0x2a,reset<<track,2)
        for i,v in enumerate(values):n.put(0x2558dc+2*(0x6d+0x30*track+i),v,2)
        n.put(0x2412cc+voice*REC+0x1b8,active,1)
        n.put(0x24f0cc+voice,trig,1);n.put(0x24f0ec+voice,release,1)
        n.call(0x1c4ecf,{'R4':0x2412cc+voice*REC,'R8':OUT,'R12':32,**protected,**circular},bound=200000)
        for k,v in {**protected,**circular,'I6':STACK+0x10000,'I7':STACK}.items():
            assert n.runner.state.uregs[st.UREG_CODES[k]].value==v,k
        out=np.frombuffer(n.data(OUT,128),dtype='<f4').copy()
        assert np.isfinite(out).all() and abs(out).max()<1.5
        f=Frame(pitch*256,16384,wave,cut,res,env,dec,glide,bite,25600,active,trig,release,reset)
        expected=np.zeros(32,np.float32);lib.fang_track(track,c.byref(f),expected.ctypes.data)
        assert np.allclose(out,expected,atol=5e-5,rtol=1e-3),(out.tolist(),expected.tolist())
        return out
    rows=[]
    for k in range(10):
        x=block(trig=int(k==0),reset=int(k==0));assert np.any(x)
        rows.append(float(abs(x).max()))
    for wave,res in ((0,0),(32512,32512),(16256,32512)):
        block(wave=wave,res=res,trig=1)
    for control in ('wave','cut','res','env','dec','glide','bite'):
        for raw in (0,32512):block(trig=1,**{control:raw})
    block(pitch=72,trig=1,glide=32512);block(pitch=72,glide=32512,release=1)
    other_before=n.data(sym('fang_states.')+44,44)
    block(track=1,pitch=48,trig=1);other=n.data(sym('fang_states.')+44,44);assert other!=other_before
    assert not np.any(block(active=0));assert np.any(block(trig=1))
    assert not np.any(block(reset=1))
    assert not np.any(block()),'stale ACTIVE rearmed after STOP/reset without a note'
    assert np.any(block(trig=1))
    assert n.data(sym('fang_states.')+44,44)==other
    # Odd bus must copy the immediate previous even output, no second envelope tick.
    before=n.data(sym('fang_states.'),704)
    source=n.data(OUT,128)
    for i in range(32):n.put(OUT-128+4*i,struct.unpack_from('<I',source,4*i)[0])
    n.put(0x255970,16,2)
    n.call(0x1c4ecf,{'R4':0x2412cc+REC,'R8':OUT,'R12':32,**protected,**circular},bound=10000)
    assert n.data(OUT,128)==source and n.data(sym('fang_states.'),704)==before
    assert n.data(cstack,32)==b'\xa5'*32,'private C stack lower guard'
    first=next(i for i in range(1024) if n.get(cstack+4*i)!=0xa5a5a5a5)
    r=dict(status='PASS',image_sha256=hashlib.sha256(a.image.read_bytes()).hexdigest(),
        tier='native renderer -> registered type16 callback -> compiled C -> native return; synthetic frame/ACTIVE',
        compared_host_blocks=len(n.cost)-1,peaks_first_ten=rows,
        max_native_renderer_instructions=max(x[1] for x in n.cost),
        private_C_stack_exercised_bytes=4096-first*4,private_stack_lower_guard='PASS',
        callee_registers_and_circular_stack='PASS',odd_bus_state_unchanged='PASS',
        reset_retrigger_release_other_track='PASS',hardware='UNTESTED')
    (WORK/'native.json').write_text(json.dumps(r,indent=2)+'\n');print(json.dumps(r,indent=2),flush=True)

if __name__=='__main__':main()
