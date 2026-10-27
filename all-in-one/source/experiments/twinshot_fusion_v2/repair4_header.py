"""Actual shared INFO hook: long header vs short grid, DX7/stock fallback."""
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
    for key in ("plan","tools","report"):ap.add_argument("--"+key,type=Path,required=True)
    a=ap.parse_args();plan=json.loads(a.plan.read_text())
    sym=lambda s:next(v for k,v in plan["symbols"].items() if k.endswith(":"+s))
    p,v,s,_=u.fixture.__wrapped__();pk_cfcall._built=True
    prefix=str(a.tools.resolve()/"usr/bin/m68k-linux-gnu-")
    env={**os.environ,"LD_LIBRARY_PATH":str(a.tools.resolve()/"usr/lib/x86_64-linux-gnu")}
    with tempfile.TemporaryDirectory(prefix="fus2-info-") as tmp:
        obj,elf,bin=[Path(tmp)/n for n in ("a.o","a.elf","a.bin")]
        subprocess.run([prefix+"as","-mcfv4e","-o",str(obj),str(Path(__file__).with_name("repair4_header_fixture.s"))],env=env,check=True)
        subprocess.run([prefix+"ld.bfd","-Ttext=0x47f20000","-e","repair4_header","-o",str(elf),str(obj)],env=env,check=True)
        subprocess.run([prefix+"objcopy","-O","binary",str(elf),str(bin)],env=env,check=True)
        code=bin.read_bytes();names=subprocess.check_output([prefix+"nm","-g",str(elf)],env=env,text=True)
        done=next(int(line.split()[0],16) for line in names.splitlines() if line.endswith(" repair4_header_done"))
    buf=0x4293b818
    base={**p,0x47f20000:code,0x4006ce98:struct.pack(">HI",0x4ef9,done),
        buf-4:b"GUAR"+bytes(64)+b"DEND",0x47f10100:bytes(8),s+0xa2:bytes([14])}
    m=pk_cfcall.CfMachine("Digitakt_II_OS1.17.syx",timeout=120)
    def call(code,raw):return {"pc":0x47f20000,"args":[v,code,raw],"reads":[(buf-4,72),(0x47f10100,8)],"max":100000}
    rows=[]
    for code in (292,311,312,313):
        for raw in (-1,0,127,128,256,383,384,512,32767):
            r=u.checked(m.run(base,[call(code,raw)]))[0]
            data=r.reads[0];value=data[4:-4].split(b"\0",1)[0].decode()
            expected="Independent" if raw<128 else "Fit to A" if raw<384 else "Fit+transpose"
            assert value==expected,(code,raw,value)
            assert data[:4]==b"GUAR" and data[-4:]==b"DEND"
            assert struct.unpack(">2I",r.reads[1])==(code,v)
            assert len("B sync")+1+len(value)<=21
            rows.append({"code":code,"raw":raw,"header":value})
    # Execute exactly the ORIGINAL DX7 wrapper as a paired reference. Only
    # this test RAM copy of the hook target changes, never the firmware image.
    fallback=[]
    harm=sym("params.plaits_harm.index")
    for model in (0,18,19,20,21):
        pp={**base,s+0xa2:bytes([11]),s+0x14+26*2:struct.pack(">H",model<<8)}
        for raw in (0,16384,32512):
            current=u.checked(m.run(pp,[call(harm,raw)]))[0]
            reference=u.checked(m.run({**pp,0x4006ce92:struct.pack(">HI",0x4ef9,sym("dx7_header_text"))},[call(harm,raw)]))[0]
            assert current.reads==reference.reads,(model,raw)
            fallback.append({"model":model,"raw":raw,"value":current.reads[0][4:-4].split(b"\0",1)[0].decode()})
    for code in (10,248,293):
        current=u.checked(m.run(base,[call(code,16384)]))[0]
        reference=u.checked(m.run({**base,0x4006ce92:struct.pack(">HI",0x4ef9,sym("dx7_header_text"))},[call(code,16384)]))[0]
        assert current.reads==reference.reads,code
    report={"status":"PASS","image_sha256":hashlib.sha256(u.CANDIDATE.read_bytes()).hexdigest(),
        "SYNC_header_cases":rows,"DX7_and_nonDX7_reference":fallback,"stock_and_FUSEmode_fallback":[10,248,293],
        "native64_byte_header_buffer_guards":True,"D2_A2_and_cdecl_preserved":True,
        "tier":"actual native header hook/formatter with reconstructed caller; scheduled panel separately"}
    a.report.write_text(json.dumps(report,indent=2)+"\n");print("Shared header full text, guards, DX7/stock fallback PASS",flush=True)


if __name__=="__main__":main()
