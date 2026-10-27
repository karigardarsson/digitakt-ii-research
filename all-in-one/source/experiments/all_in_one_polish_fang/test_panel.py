"""Final-image CF framebuffer execution; actual native widget, not a sketch."""
import json
import hashlib
import struct
from pathlib import Path
from patchkit import test_all_in_one25 as u
from tools.patchkit import pk_cfcall

HERE=Path(__file__).resolve().parent
DV=HERE.parents[1];WORK=DV/'out/all-in-one-polish-fang-r4-work';R4=DV/'out/twinshot-fus2-repair4-work'

def main():
    pk_cfcall._built=True
    p,view,sound,_=u.fixture.__wrapped__()
    image,report=u.CANDIDATE,u.REPORT
    u.CANDIDATE=R4/'integrated-05.syx';u.REPORT=R4/'build-05.json'
    prior,ov,os,_=u.fixture.__wrapped__();u.CANDIDATE=image;u.REPORT=report
    m=pk_cfcall.CfMachine('Digitakt_II_OS1.17.syx',timeout=120)
    panel=json.loads(report.read_text())['panel']['symbols']
    buf=0x47f19000
    f_pokes={**p,buf-4:b'GUAR'+bytes(8)+b'DEND'}
    formats=[]
    for short,full in (('panel_recovery_short','fusion2_recovery_fmt'),('panel_auto_short','fusion2_auto_fmt')):
        for raw in (-32768,-1,0,1,128,8192,16384,32512,32767):
            rr=u.checked(m.run(f_pokes,[{'pc':panel[n],'args':[raw,buf],'reads':[(buf-4,16)]} for n in (short,full)]))
            for r in rr:assert r.reads[0][:4]==b'GUAR' and r.reads[0][-4:]==b'DEND'
            small,large=[r.reads[0][4:12].split(b'\0')[0].decode() for r in rr]
            if large=='OFF':expected='OFF'
            else:
                ms=int(large[:-2]);expected=str(ms)+'ms' if ms<100 else str((ms+50)//100//10)+'.'+str((ms+50)//100%10)+'s'
            assert small==expected and len(small)<=4,(raw,small,large)
            formats.append(dict(raw=raw,short=small,full=large))
    def render(base,v,s,t,page=0,values=None):
        edits={**base,s+0xa2:bytes([t]),v+0x90:struct.pack('>I',page)}
        for k,raw in (values or {}).items():edits[s+0x14+2*k]=struct.pack('>H',raw)
        rows=u.checked(m.run(edits,[u.clear(),{**u.page_call(v),'reads':[(u.PIXELS-16,1056),(s,0x450)]}]))
        for addr,data in edits.items():
            if s<=addr<s+0x450:assert rows[-1].reads[1][addr-s:addr-s+len(data)]==data
        return u.bits(rows[-1]),rows[-1].insns
    enc_only=bool(__import__('os').environ.get('PANEL_ENCODER_ONLY'))
    previous=[]
    for typ in (() if enc_only or __import__('os').environ.get('PANEL_FOCUS') else range(7)):
        assert render(p,view,sound,typ)[0]==render(prior,ov,os,typ)[0],typ
        previous.append(typ)
    models=[]
    for model in (() if enc_only or __import__('os').environ.get('PANEL_FOCUS') else range(25)):
        values={25:16384,26:model<<8,27:16384,31:16384,32:16384,33:16384,34:32512}
        a,_=render(p,view,sound,11,values=values);b,_=render(prior,ov,os,11,values=values)
        assert a==b,('Plaits changed',model)
        models.append(model)
        if model in (0,3,18,21,24):u.save_bitmap(a,'PLAITS-'+str(model))
    cases=[]
    for typ in (() if enc_only else (12,13,14)):
        for mode in range(5 if typ==14 else 4 if typ==13 else 1):
            for page in range(2 if typ==14 else 1):
                values={25:16384,26:256,27:mode<<8 if typ!=12 else 22000,28:7,29:8000,30:23000,
                        31:16384,32:16256,33:1055,34:16256}
                a,cost=render(p,view,sound,typ,page,values)
                b,_=render(prior,ov,os,typ,page,values)
                u.save_bitmap(a,'M'+str(typ)+'-mode'+str(mode)+'-page'+str(page+1))
                u.save_bitmap(b,'REF-M'+str(typ)+'-mode'+str(mode)+'-page'+str(page+1))
                if typ!=14 or not page or mode:
                    assert a!=b,(typ,mode,page,'expected changed parameter glyph')
                else:assert a==b,'BLEND page2 has only unchanged native START controls'
                assert a==render(p,view,sound,typ,page,values)[0]
                u.save_bitmap(a,'M'+str(typ)+'-mode'+str(mode)+'-page'+str(page+1))
                cases.append(dict(type=typ,mode=mode,page=page,instructions=cost))
    for value in (() if enc_only else (0,16384,32512)):
        values={25:16384,26:value,27:value,28:0,29:value,31:value,32:value,33:value,34:value}
        a,cost=render(p,view,sound,16,values=values)
        u.save_bitmap(a,'FANG-'+str(value));cases.append(dict(type=16,raw=value,instructions=cost))
    raster=WORK/'final-panel-raster.json'
    if not enc_only:
        raster.write_text(json.dumps(dict(image_sha256=hashlib.sha256(image.read_bytes()).hexdigest(),
            previous=previous,models=models,cases=cases),indent=2)+'\n')
    else:
        saved=json.loads(raster.read_text());assert saved['image_sha256']==hashlib.sha256(image.read_bytes()).hexdigest()
        previous,models,cases=saved['previous'],saved['models'],saved['cases']
    encoder=[]
    for typ,indices in ((12,(28,25,26,27,33,31,32,34)),(13,(28,25,26,27,33,31,32,34)),(16,(25,26,27,29,31,32,33,34))):
        for knob,index in enumerate(indices):
            event=0x47f08000
            edits={**p,sound+0xa2:bytes([typ]),view+0x90:bytes(4),
                event:struct.pack('>5IB',0x401f10c4,0,0,knob+1,1,0)}
            initial={25:16384,26:256,27:256,28:30,29:16384,30:0,31:16384,32:16384,33:30 if typ!=16 else 16384,34:16384}
            if typ==13:initial[26]=0  # SYNC is already at its maximum at256.
            for k,raw in initial.items():edits[sound+0x14+2*k]=struct.pack('>H',raw)
            results=u.checked(m.run(edits,[{'pc':0x400184ea,'args':[view,event],'max':400000,
                'reads':[(sound+0x14+50,20)]} for _ in range(64)]))
            actual=struct.unpack('>10H',results[-1].reads[0])
            changed=[k for k in initial if actual[k-25]!=initial[k]]
            assert changed==[index],(typ,knob,index,changed,actual)
            encoder.append(dict(type=typ,knob=knob,mirror=index,after=actual[index-25]))
    r=dict(status='PASS',image_sha256=hashlib.sha256(image.read_bytes()).hexdigest(),
        stock_framebuffers_exact=previous,plaits_framebuffers_exact=models,
        new_captures=cases,native_encoder_mapping=encoder,guarded_time_formatters=formats,bitmap_guards_registers_immutable_sound=True,
        tier='executed complete native CF page from loaded final MAIN; hardware untested')
    (WORK/'panel-test.json').write_text(json.dumps(r,indent=2)+'\n');print(json.dumps(r,indent=2),flush=True)

if __name__=='__main__':main()
