"""Read-only final-image FANG SRC page/navigation and ownership check."""
import hashlib
import json
import os
import struct
import sys
from pathlib import Path

HERE=Path(__file__).resolve().parent
DV=HERE.parents[1]
HOST=DV.parent/'plaits-va-run02/out/spacejam-hopt2-playable'
WORK=DV/'out/fang-src-page-check-20261027'
IMAGE=DV/'out/on-device-credits-work/DT2_OS117_ALL_IN_ONE_ON_DEVICE_CREDITS_CANDIDATE.syx'
os.environ['AIO25_CANDIDATE']=str(IMAGE)
os.environ['AIO25_REPORT']=str(DV/'out/on-device-credits-work/build.json')
os.environ['AIO25_UI_OUT']=str(WORK)
from patchkit import test_all_in_one25 as u
from tools.patchkit import pk_cfcall,pk_integrate_dv_zoom as dv,port_resolve

def main():
    WORK.mkdir(exist_ok=True)
    digest=hashlib.sha256(IMAGE.read_bytes()).hexdigest()
    assert digest=='bbc534fae45fd4044625edba07c12d4be8b24d8997ccab313e6af0dfccd43626'
    plan=json.loads((DV/'out/all-in-one-polish-fang-r4-work/fang-seed.plan.json').read_text())
    sym=lambda n:next(v for k,v in plan['symbols'].items() if k.endswith(':'+n))
    main=port_resolve.sections_of(IMAGE)[3]
    def loaded(a,n):return dv.at(main,dv.source_for_loaded_address(main,a),n)
    rows=sym('machines.rows');first=sym('machines.first')
    flag_address=rows+(16-first)*68+64
    assert struct.unpack('>I',loaded(flag_address,4))[0]==1
    hooks={}
    for pc,name in ((0x4006b01e,'mo_count'),(0x400691e2,'mo_setpage')):
        assert dv.at(main,pc,6)==b'\x4e\xf9'+struct.pack('>I',sym(name))
        hooks[hex(pc)]=dict(target=hex(sym(name)),bytes=dv.at(main,pc,8).hex())
    p,view,sound,_=u.fixture.__wrapped__();pk_cfcall._built=True
    machine=pk_cfcall.CfMachine('Digitakt_II_OS1.17.syx',timeout=90)
    event=0x47f08000
    if '--controls-only' in sys.argv:
        result=json.loads((WORK/'report.json').read_text());assert result['image_sha256']==digest
        controls=[]
        for knob,index in enumerate((25,26,27,29,31,32,33,34)):
            values={25:16384,26:16384,27:16384,28:7,29:16384,30:0,31:16384,32:16384,33:16384,34:16384}
            edits={**p,sound+0xa2:bytes([16]),view+0x90:bytes(4),
                   event:struct.pack('>5IB',0x401f10c4,0,0,knob+1,1,0)}
            for k,raw in values.items():edits[sound+0x14+2*k]=struct.pack('>H',raw)
            rr=u.checked(machine.run(edits,[{'pc':0x400184ea,'args':[view,event],'max':400000,
                'reads':[(sound+0x14+50,20)]} for _ in range(64)]))
            actual=struct.unpack('>10H',rr[-1].reads[0])
            assert [k for k in values if actual[k-25]!=values[k]]==[index]
            controls.append(dict(knob=knob,mirror=index,value=actual[index-25]))
        from test_controls import storage,res_modulation
        state=HOST/'out/cache/pk_cfcall/Digitakt_II_OS1.17.state'
        result.update(native_encoder_mapping=controls,
            serialization=storage(IMAGE,state),
            modulation=res_modulation(IMAGE,state,DV/'out/dt2-dv-offline/tools/root'))
        (WORK/'report.json').write_text(json.dumps(result,indent=2)+'\n')
        print(json.dumps(result,indent=2),flush=True);return
    edits={**p,sound+0xa2:bytes([16]),view+0x90:bytes(4),
        event:struct.pack('>5I',0,0,0,2,0x11)}
    for k,raw in {25:16384,26:16384,27:16384,28:0,29:16384,31:16384,32:16384,33:0,34:16384}.items():
        edits[sound+0x14+2*k]=struct.pack('>H',raw)
    expected=[248,314,315,316,317,318,319,320]
    cases=[]
    for requested in (0,1,2,0xffffffff):
        calls=[{'pc':0x400691e2,'args':[view,requested],'reads':[(view+0x90,4)]},
            {'pc':0x4006b01e,'args':[view],'reads':[(view+0x7c,24)]},
            *[{'pc':sym('fang_param'),'args':[view,k]} for k in range(8)],
            u.clear(),{**u.page_call(view),'reads':[(u.PIXELS-16,1056),(sound,0x450)]}]
        rr=u.checked(machine.run(edits,calls))
        assert rr[0].reads[0]==bytes(4) and rr[1].d0==1
        assert [r.d0 for r in rr[2:10]]==expected
        for a,b in edits.items():
            if sound<=a<sound+0x450:assert rr[-1].reads[1][a-sound:a-sound+len(b)]==b
        bits=u.bits(rr[-1]);u.save_bitmap(bits,'FANG-request-'+str(requested))
        cases.append(dict(request=requested,index=0,count=rr[1].d0,codes=expected,
                          framebuffer_sha256=hashlib.sha256(bits).hexdigest()))
    assert len({r['framebuffer_sha256'] for r in cases})==1
    # Complete press/release, with stock two-page positive control. Native
    # 4006d754 cycles on release, not merely on the press callback.
    navigation=[]
    for typ in (0,14,16):
        nav={**edits,sound+0xa2:bytes([typ]),view+0x17:bytes(1),view+0x88:struct.pack('>I',2),
             view+0x8c:bytes(1),view+0x190:bytes(1),0x4099d0fd:bytes(1),
             event+32:struct.pack('>6I',0,0,0,2,0x10,0)}
        calls=[]
        for _ in range(5):
            calls += [{'pc':0x400181fe,'args':[view,event],'max':500000},
                      {'pc':0x400181fe,'args':[view,event+32],'max':500000,'reads':[(view+0x90,4)]}]
        rr=u.checked(machine.run(nav,calls))
        indices=[struct.unpack('>I',r.reads[0])[0] for r in rr[1::2]]
        assert indices==([0]*5 if typ==16 else [1,0,1,0,1]),(typ,indices)
        navigation.append(dict(type=typ,indices_after_five_SRC_taps=indices))
    isolation=[]
    for typ in (*range(7),11,12,13,14,16):
        rr=u.checked(machine.run({**p,sound+0xa2:bytes([typ]),view+0x90:bytes(4)},[
            {'pc':0x4006b01e,'args':[view]},
            {'pc':0x400691e2,'args':[view,1],'reads':[(view+0x90,4)]}]))
        count,index=rr[0].d0,struct.unpack('>I',rr[1].reads[0])[0]
        if typ in (11,12,13,16):assert (count,index)==(1,0),(typ,count,index)
        elif typ==14:assert (count,index)==(2,1),(typ,count,index)
        isolation.append(dict(type=typ,count=count,index_after_request1=index))
    result=dict(status='PASS',image_sha256=digest,flag_address=hex(flag_address),one_page=1,hooks=hooks,
        page_cases=cases,native_press_release_navigation=navigation,isolation=isolation,
        scope='Final-image native CF functions using cached UI state; no physical panel claim',
        firmware_changed=False)
    (WORK/'report.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2),flush=True)

if __name__=='__main__':main()
