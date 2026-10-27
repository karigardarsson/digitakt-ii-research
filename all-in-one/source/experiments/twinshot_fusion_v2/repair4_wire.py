"""Native actual outgoing frame, beyond earlier pre-packing stop fixtures."""
import argparse
import hashlib
import json
import os
import struct
import subprocess
import tempfile
from pathlib import Path
from tools.emulator.snapread import Snapshot
from tools.patchkit import pk_cfcall,port_resolve
from tools.patchkit import pk_integrate_dv_zoom as dv


def main():
    ap=argparse.ArgumentParser(__doc__)
    for n in ("image","state","tools","report"):ap.add_argument("--"+n,type=Path,required=True)
    a=ap.parse_args();main=port_resolve.sections_of(a.image)[3]
    prefix=str(a.tools.resolve()/"usr/bin/m68k-linux-gnu-")
    env={**os.environ,"LD_LIBRARY_PATH":str(a.tools.resolve()/"usr/lib/x86_64-linux-gnu")}
    with tempfile.TemporaryDirectory(prefix="fus2-wire-") as tmp:
        obj,elf,bin=[Path(tmp)/n for n in ("a.o","a.elf","a.bin")]
        subprocess.run([prefix+"as","-mcfv4e","-o",str(obj),str(Path(__file__).with_name("repair4_frame_fixture.s"))],env=env,check=True)
        subprocess.run([prefix+"ld.bfd","-Ttext=0x47f20000","-e","repair4_frame","-o",str(elf),str(obj)],env=env,check=True)
        subprocess.run([prefix+"objcopy","-O","binary",str(elf),str(bin)],env=env,check=True)
        code=bin.read_bytes()
        names=subprocess.check_output([prefix+"nm","-g",str(elf)],env=env,text=True)
        done=next(int(line.split()[0],16) for line in names.splitlines() if line.endswith(" repair4_done"))
    p={0x40000400:main[:dv.STOCK_MAIN_END-0x40000400],0x47f20000:code,
       0x4002ebe4:struct.pack(">HI",0x4ef9,done),0x47db461c:bytes(16*12),0x409664f8:bytes(4)}
    for src,n,dest,bss in dv.parsed_segments(main)[0]:p[dest]=dv.at(main,src+12,n)+bytes(bss)
    snap=Snapshot(str(a.state));template=snap.read(snap.u32(0x80004704)+0x34,0x450)
    pk_cfcall._built=True;m=pk_cfcall.CfMachine("Digitakt_II_OS1.17.syx",state=a.state,timeout=300)
    rows=[]
    for sync in (0,256,512):
        for mode in (512,768,1024):
            for depth in (0,16256,32512):
                pp=dict(p);calls=[{"pc":0x401391ec,"args":[]},{"pc":0x40139240,"args":[]}]
                for track in range(16):
                    sound=bytearray(template);sound[0xa2]=14 if not track else 0
                    values={25:16384,26:sync,27:mode,28:7,29:0,30:0,31:16384,32:depth,33:31,34:16256,8:0}
                    for idx,value in values.items():struct.pack_into(">H",sound,0x14+2*idx,value)
                    addr=0x47000000+track*0x500;pp[addr]=sound
                    calls.append({"pc":0x4002d438,"args":[addr,track]})
                calls.append({"pc":0x47f20000,"args":[],"max":2000000,
                    "reads":[(0x80005348+0xda,20),(0x80005348+0x94,2)]})
                rr=m.run(pp,calls)
                assert all(r.ok and not r.clobbered for r in rr),[(r.error,r.clobbered) for r in rr if not r.ok or r.clobbered]
                fields=struct.unpack(">10H",rr[-1].reads[0]);kind=struct.unpack(">H",rr[-1].reads[1])[0]
                assert fields[1]==sync and fields[2]==mode and fields[7]==depth,(sync,mode,depth,fields)
                assert fields[3]==7 and fields[8]==31 and fields[9]==16256,fields
                assert kind==14,("outgoing type",kind)
                rows.append({"sync":sync,"mode":mode,"depth":depth,"outgoing":fields,"kind":kind})
    a.report.write_text(json.dumps({"status":"PASS","sha256":hashlib.sha256(a.image.read_bytes()).hexdigest(),
        "rows":rows,"tier":"native Sound copy/smoother/LFO/reconstruction/actual outgoing20-byte copy, synthetic caller/masks; no peripheral or hardware"},indent=2)+"\n")
    print("All27 real outgoing frame combinations PASS",flush=True)


if __name__=="__main__":main()
