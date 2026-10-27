"""Native tuning raster oracle and ordinary/fast discrete progression."""
import argparse
import hashlib
import json
import os
import struct
import subprocess
import tempfile
from pathlib import Path
from patchkit import test_all_in_one25 as u
from tools.patchkit import pk_cfcall


def main():
    ap=argparse.ArgumentParser(__doc__)
    for n in ("plan","tools","out"):ap.add_argument("--"+n,type=Path,required=True)
    a=ap.parse_args();a.out.mkdir(parents=True,exist_ok=True)
    plan=json.loads(a.plan.read_text());sym=lambda s:next(v for k,v in plan["symbols"].items() if k.endswith(":"+s))
    pk_cfcall._built=True;m=pk_cfcall.CfMachine("Digitakt_II_OS1.17.syx",timeout=120)
    p,v,s,_=u.fixture.__wrapped__();base={**p,s+0xa2:bytes([14]),v+0x90:bytes(4)}
    for i,value in {25:0x4000,26:0,27:0,28:7,31:0x4000,32:0,33:31,34:0}.items():base[s+0x14+2*i]=struct.pack(">H",value)
    prefix=str(a.tools.resolve()/"usr/bin/m68k-linux-gnu-")
    env={**os.environ,"LD_LIBRARY_PATH":str(a.tools.resolve()/"usr/lib/x86_64-linux-gnu")}
    with tempfile.TemporaryDirectory(prefix="fus2-r4-ui-") as tmp:
        o,e,b=[Path(tmp)/n for n in ("stub.o","stub.elf","stub.bin")]
        subprocess.run([prefix+"as","-mcfv4e","-o",str(o),str(Path(__file__).with_name("repair4_ui_fixture.s"))],env=env,check=True)
        subprocess.run([prefix+"ld.bfd","-Ttext=0x47f20000","-e","repair4_capture","-o",str(e),str(o)],env=env,check=True)
        subprocess.run([prefix+"objcopy","-O","binary",str(e),str(b)],env=env,check=True)
        stub=b.read_bytes()
    tuning=[]
    def pixel(bits,x,y):return bool(struct.unpack_from(">I",bits,x*8+(y//32)*4)[0]&(0x80000000>>(y%32)))
    for raw in (0x400,0x3400,0x4000,0x4780,0x7c00):
        pp={**base,s+0x14+50:struct.pack(">H",raw)}
        cap={**pp,sym("fusion_widget"):bytes.fromhex("4ef947f20000"),0x47f20000:stub,0x47f10000:bytes(44)}
        cr=u.checked(m.run(cap,[u.clear(),{**u.page_call(v),"reads":[(0x47f10000,44)]}]))[-1]
        args=list(struct.unpack(">11I",cr.reads[0]));assert args[4]==301
        actual=u.bits(u.checked(m.run(pp,[u.clear(),u.page_call(v)]))[-1])
        args[4]=248
        expected=u.bits(u.checked(m.run(pp,[u.clear(),{"pc":0x47ea0000,"args":args,
            "reads":[(u.PIXELS-16,1056)],"max":400000}]))[-1])
        x,y=args[2:4]
        assert all(pixel(actual,xx,yy)==pixel(expected,xx,yy) for xx in range(x-3,x+22) for yy in range(y+7,y+25)),("native TUNA differs",raw,args)
        u.save_bitmap(actual,"TUNA-native-"+str(raw))
        tuning.append({"raw":raw,"native_args":args,"glyph":"pixel-exact native code248"})
    rows=[]
    for knob,index,initial,step in ((0,28,30,1),(2,26,256,256),(3,27,512,256),(4,33,30,1)):
        for delta in (-32,-8,-3,-1,1,3,8,32):
            event=0x47f08000
            edits={**base,s+0x14+2*index:struct.pack(">H",initial),
                event:struct.pack(">5IB",0x401f10c4,0,0,knob+1,delta&0xffffffff,0)}
            rr=u.checked(m.run(edits,[{"pc":0x400184ea,"args":[v,event],"max":400000,
                "reads":[(s+0x14+2*index,2)]} for _ in range(64)]))
            seq=[initial]+[struct.unpack(">H",r.reads[0])[0] for r in rr]
            changes=[b-a for a,b in zip(seq,seq[1:])]
            assert any(changes),(knob,delta)
            if step==256 or abs(delta)<=8:assert max(map(abs,changes))<=step,(knob,delta,changes)
            else:assert max(map(abs,changes))>1,"fast sample browsing lost"
            assert all(0<=x<=({26:512,27:1024}.get(index,2047)) for x in seq)
            rows.append({"knob":knob,"delta":delta,"sequence":seq,"max_increment":max(map(abs,changes))})
    report={"status":"PASS","image_sha256":hashlib.sha256(u.CANDIDATE.read_bytes()).hexdigest(),
        "tuning_native_rasters":tuning,"discrete_progression":rows,
        "tier":"actual native CF renderer/encoder/setter; hardware encoder feel remains UNTESTED"}
    (a.out/"ui-extra.json").write_text(json.dumps(report,indent=2)+"\n")
    print("Native tuning pixels and discrete/fast stepping PASS",flush=True)


if __name__=="__main__":main()
