"""Final-image native FUS2 UI: contextual descriptors, values, routing and bounds.

No DSP algorithms or user data are changed. Run from pinned source with tests
import root, as ui_checks.py does. CF fixtures do not prove physical hardware.
"""
import argparse
import hashlib
import json
import struct
from pathlib import Path

from patchkit import test_all_in_one25 as ui
from tools.patchkit import pk_cfcall, port_resolve
from tools.patchkit import pk_integrate_dv_zoom as dv


def main():
    ap=argparse.ArgumentParser(__doc__)
    for n in ("plan","reference","reference-report","report"):
        ap.add_argument("--"+n,type=Path,required=True)
    a=ap.parse_args()
    plan=json.loads(a.plan.read_text())
    def sym(n):
        return next(v for k,v in plan["symbols"].items() if k.endswith(":"+n))
    pk_cfcall._built=True
    m=pk_cfcall.CfMachine("Digitakt_II_OS1.17.syx",timeout=120)
    pokes,view,sound,_=ui.fixture.__wrapped__()
    current,report=ui.CANDIDATE,ui.REPORT
    ui.CANDIDATE,ui.REPORT=a.reference,a.reference_report
    prior,oldview,oldsound,_=ui.fixture.__wrapped__()
    ui.CANDIDATE,ui.REPORT=current,report
    mainbytes=port_resolve.sections_of(current)[3]
    def mem(addr,n):
        return dv.at(mainbytes,dv.source_for_loaded_address(mainbytes,addr),n)
    def desc(code):
        return struct.unpack(">15I",mem(sym("pf_table")+60*code,60))
    def text(addr):
        return mem(addr,80).split(b"\0",1)[0].decode("ascii")
    aliases={301:248,302:296,303:296,304:296,305:296,306:296,
             307:298,308:298,309:298,310:295,311:292,312:292,313:292}
    for alias,original in aliases.items():
        assert desc(alias)[:10]==desc(original)[:10],(alias,original,desc(alias),desc(original))
    assert sym("params.first")+sym("params.count")==314
    assert mem(sym("mf_names")+14*12+8,4)==bytes(4)
    previous=[]
    for typ in (*range(7),11,12,13):
        frames=[]
        for base,v,s in ((pokes,view,sound),(prior,oldview,oldsound)):
            edits={**base,s+0xA2:bytes([typ]),v+0x90:bytes(4)}
            frames.append(ui.bits(ui.checked(m.run(edits,[ui.clear(),ui.page_call(v)]))[-1]))
        assert frames[0]==frames[1],typ
        previous.append(typ)
    print("Stock/Plaits/TWIN/FUSE framebuffers exact versus Repair1",flush=True)
    def setup(mode,sync,page):
        edits={**pokes,sound+0xA2:bytes([14]),view+0x90:struct.pack(">I",page)}
        for idx,value in {25:0x4000,26:sync,27:mode<<8,28:7,29:0x2000,30:0x5400,
                          31:0x4000,32:0x4000,33:1055,34:0x3000}.items():
            edits[sound+0x14+2*idx]=struct.pack(">H",value)
        return edits
    def codes(mode,sync,page):
        return ([294,301,311 if sync<128 else 312 if sync<384 else 313,293,297,295 if sync<128 else 0 if sync<384 else 310,
                 302+mode,0,10] if page==0 else [299,300,[0,307,308,309,308][mode],0,0,0,0,0,10])
    def pixel(bits,x,y):
        return bool(struct.unpack_from(">I",bits,x*8+4*(y//32))[0] & (0x80000000>>(y%32)))
    pages=[]
    for mode in range(5):
        for sync in (0,256,512):
            for page in (0,1):
                edits=setup(mode,sync,page)
                edits[0x47F0B000]=b"%s\0"
                calls=[{"pc":sym("fusion2_param"),"args":[view,k]} for k in range(9)]
                calls += [ui.clear(),{**ui.page_call(view),"reads":[(ui.PIXELS-16,1056),(sound,0x450)]}]
                # Independent caption raster: draw the real descriptor's short
                # name directly with the native text API, NOT our widget.
                calls += [ui.clear()]
                for k,c in enumerate(codes(mode,sync,page)[:8]):
                    if c:
                        # Native generic caller passes d5-1, not d5 (4006b244).
                        x,y=24+26*(k%4),27 if k<4 else 0
                        calls.append({"pc":0x40113DC8,"args":[ui.BMP,0x44D2E70C,x+9,y,2,0x47F0B000,desc(c)[12]],
                                      "reads":[(ui.PIXELS-16,1056)]})
                result=ui.checked(m.run(edits,calls))
                display=result[10]
                observed=[r.d0 for r in result[:9]]
                assert observed==codes(mode,sync,page),(mode,sync,page,observed)
                expected_sound=bytearray(display.reads[1])
                for addr,b in edits.items():
                    if sound<=addr<sound+0x450:
                        assert expected_sound[addr-sound:addr-sound+len(b)]==b,"draw changed Sound"
                labels=[text(desc(c)[12]) if c else "" for c in observed[:8]]
                assert "SHAP" not in labels
                drawn,expected_labels=ui.bits(display),ui.bits(result[-1])
                for k,c in enumerate(observed[:8]):
                    if c:
                        x,y=24+26*(k%4),27 if k<4 else 0
                        assert all(pixel(drawn,xx,yy)==pixel(expected_labels,xx,yy)
                                   for xx in range(x-2,x+21) for yy in range(y,y+5)),(mode,sync,page,k,"caption is not parameter identity")
                if sync==0:
                    ui.save_bitmap(drawn,f"FUS2-{mode}-page{page+1}")
                pages.append({"mode":mode,"sync":sync,"page":page,"codes":observed,"labels":labels})
    print("Thirty mode/sync/page contexts, LEVEL and immutable Sound PASS",flush=True)
    buf=0x47F08000
    fmtp={**setup(0,0,0),buf-4:b"GUAR"+bytes(16)+b"DEND"}
    specs=[]
    for raw in (-32768,-1,0,1,127,128,255,256,383,384,512,32767):
        specs.append((sym("fusion2_sync_fmt"),raw,"OFF" if raw<128 else "MATCH" if raw<384 else "REL"))
        specs.append((sym("fusion2_sync_info_fmt"),raw,"Independent" if raw<128 else "Fit to A" if raw<384 else "Fit+transpose"))
        specs.append((sym("fusion2_mode_fmt"),raw,["BLND","RING","DUCK","MRPH","GATE"][max(0,min(4,raw>>8))]))
    for raw in sorted(set([-32768,-1,0,1,16255,16256,32511,32512,32767]+list(range(0,32513,256)))):
        v=max(0,min(32512,raw))
        specs.append((sym("fusion2_percent_fmt"),raw,str((v*100+16256)//32512)+"%"))
    results=ui.checked(m.run(fmtp,[{"pc":fn,"args":[v,buf],"reads":[(buf-4,24)]} for fn,v,_ in specs]))
    for row,(_,raw,expected) in zip(results,specs,strict=True):
        b=row.reads[0]
        assert b[:4]==b"GUAR" and b[-4:]==b"DEND"
        assert b[4:-4].split(b"\0",1)[0].decode()==expected,(raw,b,expected)
    names=[]
    times=[]
    for fn,scale in (("fusion2_recovery_fmt",1.99),("fusion2_auto_fmt",8)):
        raws=(-32768,-1,0,1,*range(256,32513,256),32512,32767)
        rr=ui.checked(m.run(fmtp,[{"pc":sym(fn),"args":[raw,buf],"reads":[(buf-4,24)]} for raw in raws]))
        for raw,r in zip(raws,rr,strict=True):
            data=r.reads[0];assert data[:4]==b"GUAR" and data[-4:]==b"DEND"
            value=data[4:-4].split(b"\0",1)[0].decode()
            if fn=="fusion2_auto_fmt" and raw<=0:assert value=="OFF"
            else:
                expected=10+1000*scale*(max(0,min(32512,raw))/32512)**2
                assert value.endswith("ms") and abs(int(value[:-2])-expected)<=(2.6 if scale<2 else 17), (fn,raw,value,expected)
            assert len(value)<=6
            times.append((fn,raw,value))
    for code in range(292,314):
        d=desc(code)
        name,short=text(d[10]),text(d[12])
        values=ui.checked(m.run(fmtp,[{"pc":d[13],"args":[raw,buf],"reads":[(buf-4,24)]}
                                    for raw in (d[2],d[4],d[3])]))
        labels=[]
        for r in values:
            b=r.reads[0]
            assert b[:4]==b"GUAR" and b[-4:]==b"DEND",code
            value=b[4:-4].split(b"\0",1)[0].decode()
            assert len(value)<=6,(code,value)
            # Native INFO's four-character track prefix and five-pixel text
            # leave21 readable body characters.26 only prevents heap underflow.
            assert len(name)+1+len(value)<=21,(code,name,value)
            labels.append(value)
        assert 0<len(short)<=4,(code,short)
        names.append({"code":code,"name":name,"short":short,"min_default_max":labels})
    # The grid's ParameterSet formatter is NOT the64-byte header route.
    # Guard an8-byte value destination; full INFO explanations must never be
    # installed in the descriptor and reach this pre-widget native consumer.
    small={**setup(0,0,0),buf-4:b"GUAR"+bytes(8)+b"DEND"}
    grid_calls=[{"pc":desc(code)[13],"args":[raw,buf],"reads":[(buf-4,16)]}
        for code in range(292,314) for raw in (desc(code)[2],desc(code)[3])]
    for r in ui.checked(m.run(small,grid_calls)):
        assert r.reads[0][:4]==b"GUAR" and r.reads[0][-4:]==b"DEND","grid formatter escaped8 bytes"
    print("Formatters, sample/direction/tuning endpoints and help length/guards PASS",flush=True)
    # Native callbacks, not host parameter writes. Fresh contexts keep changing
    # FUSE/SYNC encoders from contaminating the next control's intended role.
    event,data=0x47F09000,0x47F0A000
    encoder_rows=[]
    for mode in range(5):
        for page in (0,1):
            for knob in range(9):
                for delta in (-32,32):
                    code=codes(mode,0,page)[knob]
                    expected=desc(code)[1] if code and code!=10 else None
                    edits=setup(mode,0,page)
                    edits[event]=struct.pack(">5IB",0x401F10C4,0,0,knob+1,delta&0xffffffff,1)
                    calls=[{"pc":0x4013AB84,"args":[data,sound,0x450]}]
                    calls += [{"pc":0x400184EA,"args":[view,event],"max":400000} for _ in range(40)]
                    calls[-1]["reads"]=[(sound,0x450),(data,0x450)]
                    result=ui.checked(m.run(edits,calls))[-1]
                    changed=[i for i,(x,y) in enumerate(zip(*result.reads)) if x!=y]
                    allowed=set() if expected is None else {0x14+2*expected,0x15+2*expected}
                    assert set(changed)<=allowed,(mode,page,knob,code,changed)
                    if expected is not None:
                        raw=struct.unpack_from(">H",result.reads[0],0x14+2*expected)[0]
                        assert desc(code)[2]<=raw<=desc(code)[3],(code,raw)
                    encoder_rows.append({"mode":mode,"page":page,"knob":knob,"delta":delta,
                                         "code":code,"mirror":expected,"changed":changed})
    report={"status":"PASS","image_sha256":hashlib.sha256(current.read_bytes()).hexdigest(),
        "reference_sha256":hashlib.sha256(a.reference.read_bytes()).hexdigest(),
        "prior_machine_framebuffers_exact":previous,"contexts":pages,"descriptors":names,
        "guarded_formatter_cases":len(specs)+len(times),"time_readouts":times,"encoder_cases":encoder_rows,
        "small_grid_formatter_guard_cases":len(grid_calls),
        "caption_rasters":"native descriptor label reference, all30 contexts; no value-only captions",
        "aliases":"same first40 descriptor bytes as original physical fields; no new storage",
        "tier":"native bounded CF UI/input; not physical hardware or natural audio scheduling"}
    a.report.write_text(json.dumps(report,indent=2)+"\n")
    print(f"UI PASS: {len(encoder_rows)} encoder/boundary cases",flush=True)


if __name__=="__main__":
    main()
