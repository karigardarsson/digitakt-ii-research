"""Reuse actual Pattern/vector, LFO and Sound converters for the new owner."""
import json
import hashlib
from pathlib import Path
import struct
from event_proof_v2 import run as events
from native_event_proof import caller
from tools.patchkit import pk_cfcall,port_resolve
from tools.patchkit import pk_integrate_dv_zoom as dv
from tools.emulator.snapread import Snapshot

def res_modulation(image,state,tools):
    # Reuse the stock full smoother/LFO/reconstruction caller. FANG has no
    # sample-selector control: unlike TWIN, do not inject a different unexposed
    # raw sample slot and pretend the discrete TWIN adapter must copy it.
    main=port_resolve.sections_of(image)[3];code,symbols=caller(tools)
    p={0x40000400:main[:dv.STOCK_MAIN_END-0x40000400],0x47f20000:code,
       0x4002e9a0:struct.pack('>HI',0x4ef9,symbols['tw_frame_fixture_done']),
       0x47db461c:bytes(16*12),0x409664f8:bytes(4)}
    for src,size,dest,bss in dv.parsed_segments(main)[0]:p[dest]=dv.at(main,src+12,size)+bytes(bss)
    snap=Snapshot(str(state));template=snap.read(snap.u32(0x80004704)+0x34,0x450)
    calls=[{'pc':0x401391ec,'args':[]},{'pc':0x40139240,'args':[]}]
    for t in range(16):
        s=bytearray(template);s[0xa2]=16 if t==3 else 0
        values={25:16384,26:12000,27:16000,28:7,29:8192,30:20480,31:18000,32:12000,33:0,34:16384}
        if t==3:values.update({1:20480,2:2816,3:16384,4:29<<8,5:256,6:0,7:0,8:24576})
        for k,v in values.items():struct.pack_into('>H',s,0x14+2*k,v)
        addr=0x47000000+t*0x500;p[addr]=bytes(s);calls.append({'pc':0x4002d438,'args':[addr,t]})
    p[0x4700f000]=struct.pack('>I',0x40000000)
    calls.extend([{'pc':0x40136388,'args':[0x44e66e78+3*0x78,0x4700f000,4]},
        {'pc':symbols['tw_frame_fixture'],'args':[],'max':1000000,
         'reads':[(0x80005b50+3*142+0x5a,14)]}])
    pk_cfcall._built=True;rr=pk_cfcall.CfMachine('Digitakt_II_OS1.17.syx',state=state,timeout=120).run(p,calls)
    assert all(r.ok and not r.clobbered for r in rr)
    fields=struct.unpack('>7H',rr[-1].reads[0]);assert fields[0]==7 and fields[1]!=8192 and fields[2]==20480,fields
    return dict(status='PASS',native_effective_mirrors_28_to_34=fields,sample_selector_unchanged=True,res_modulated=True)

def storage(image,state):
    main=port_resolve.sections_of(image)[3]
    p={0x40000400:main[:dv.STOCK_MAIN_END-0x40000400]}
    for src,n,d,b in dv.parsed_segments(main)[0]:p[d]=dv.at(main,src+12,n)+bytes(b)
    m=pk_cfcall.CfMachine('Digitakt_II_OS1.17.syx',state=state,timeout=120)
    def check(rows):
        assert all(r.ok and not r.clobbered for r in rows)
        return rows
    a,b,c=0x47000000,0x47001000,0x47002000
    base={**p,a:bytes(0x450),b:bytes(0x460),c:bytes(0x450)}
    rows=check(m.run(base,[{'pc':0x400dba94,'args':[b,a,1]},
                          {'pc':0x400db3b2,'args':[c,b],'reads':[(c,0x450)]}]))
    canonical=rows[-1].reads[0];cases=[]
    for raw in (0,16384,32512):
        sound=bytearray(canonical);sound[0xa2]=16
        for k,value in {25:16384,26:raw,27:raw,28:7,29:raw,30:0,31:raw,32:raw,33:raw,34:raw}.items():
            struct.pack_into('>H',sound,0x14+2*k,value)
        rows=check(m.run({**base,a:bytes(sound)},[{'pc':0x400dba94,'args':[b,a,1]},
            {'pc':0x400db3b2,'args':[c,b],'reads':[(c,0x450)]}]))
        assert rows[-1].reads[0]==sound,('Sound roundtrip',raw)
        cases.append(raw)
    inverse=struct.unpack_from('>77I',main,0x40213b64-0x40000400)
    locks=bytearray(b'\xff'*(80*258))
    for lane,index in enumerate((25,26,27,29,31,32,33,34)):
        locks[lane*258:lane*258+2]=bytes((inverse.index(index),3))
        for step,value in ((0,0),(1,16384),(127,32512)):
            struct.pack_into('>h',locks,lane*258+2+2*step,value)
    src,live,saved=0x47004000,0x4700a000,0x47010000
    rows=check(m.run({**p,src:bytes(locks),live:bytes(0x5200),saved:bytes(80*258)},[
        {'pc':0x400dc60a,'args':[live,src]},
        {'pc':0x400dc804,'args':[saved,live],'reads':[(saved,80*258)]}]))
    stored=rows[-1].reads[0]
    assert stored[:8*258]==locks[:8*258],[(i,a,b) for i,(a,b) in enumerate(zip(stored[:8*258],locks[:8*258])) if a!=b][:8]
    assert all(stored[i*258:i*258+2]==b'\xff\xff' for i in range(8,80))
    return dict(status='PASS',full_Sound_roundtrip_raws=cases,all128steps_eight_lanes_exact=True,
                tier='native Sound-v3/Pattern payload converters, not full filesystem or Song scheduling')

HERE=Path(__file__).resolve().parent;DV=HERE.parents[1]
WORK=DV/'out/all-in-one-polish-fang-r4-work';HOST=DV.parent/'plaits-va-run02/out/spacejam-hopt2-playable'

def main():
    image=WORK/'complete-polished.syx';planfile=WORK/'fang-seed.plan.json'
    plan=json.loads(planfile.read_text());sha=hashlib.sha256(image.read_bytes()).hexdigest()
    state=HOST/'out/cache/pk_cfcall/Digitakt_II_OS1.17.state';tools=DV/'out/dt2-dv-offline/tools/root'
    smoother=next(v for k,v in plan['symbols'].items() if k.endswith(':twinshot_smoother'))
    r=dict(image_sha256=sha)
    # Actual stock mirror/type lookup entry, derived from its verified hook
    # return (entry+0x0a+8). The physical sample lane must resolve to no control.
    from patchkit import test_all_in_one25 as ui
    pp,view,sound,_=ui.fixture.__wrapped__()
    entry=next(v for k,v in plan['symbols'].items() if k.endswith(':hook.cf.machines.typearg.0.return'))-0x12
    pk_cfcall._built=True;machine=pk_cfcall.CfMachine('Digitakt_II_OS1.17.syx',timeout=120)
    lookup=machine.run(pp,[{'pc':entry,'args':[idx,16,0]} for idx in (28,29)])
    assert all(x.ok and not x.clobbered for x in lookup)
    assert [x.d0 for x in lookup]==[0,316],[(hex(entry),x.d0,x.error) for x in lookup]
    r['native_sample_lookup']=0;r['native_RES_lookup']=316
    selectors=(25,26,27,29,31,32,33,34)
    r['events']=events(image,state,tools,expected_sha=sha,selectors=selectors,machine_type=16,smoother=smoother,
        defaults=(16384,0,16384,18432,20480,14336,0,16384),
        lanes=tuple({0:0x1000,1:0x6000,2:0x4000} for _ in selectors),discrete_selectors=())
    print('FANG native sequencer/p-lock producer and vector PASS',flush=True)
    (WORK/'events.json').write_text(json.dumps(r,indent=2)+'\n')
    r['res_lfo']=res_modulation(image,state,tools)
    print('FANG RES LFO/sample-selector ownership PASS',flush=True)
    r['storage']=storage(image,state)
    print('FANG native Sound/Pattern payload conversion PASS',flush=True)
    r['status']='PASS';r['tier']='actual native CF producer/consumer/converters, not project filesystem or hardware'
    (WORK/'controls.json').write_text(json.dumps(r,indent=2)+'\n')

if __name__=='__main__':main()
