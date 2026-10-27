"""Bounded actual level-hook execution, all tracks/types, native stack ABI.

Separate from complete native init/frame/output tests; explicit register/frame
fixtures do not establish natural transport or hardware behavior.
"""
import argparse
import hashlib
import json
from pathlib import Path
from native_dual_proof import Native,STACK
from tools.patchkit.port_resolve import sections_of
from tools.sharc_dev.sharcldr import LoadedMemory
from tools.sharc_dev import sharc_trace as st


def main():
    ap=argparse.ArgumentParser(__doc__)
    for k in ("image","report"):ap.add_argument("--"+k,type=Path,required=True)
    a=ap.parse_args(); n=Native(LoadedMemory.from_stream(sections_of(a.image)[7]))
    count=0
    for typ in (*range(15),255):
        for track in range(16):
            for raw in (0,16256,32512):
                frame=0x2558dc
                n.put(frame+0x94+2*track,typ,2)
                n.put(frame+0xda+0x60*track+18,raw,2)
                before=n.data(frame,0x73c)
                regs={"R0":0x1234,"R1":0x2345,"R2":0x3456,"R8":raw,"R10":0x4567,
                    "I4":0x5678,"M4":track,"I7":STACK-200,
                    "B7":STACK-1024,"L7":2048,"MODE1":0x11800}
                n.call(0x1c2654,regs,stop=0x1c2657,bound=100)
                for name,value in regs.items():
                    expected=32512 if typ==14 and name=="R8" else 0 if name=="R10" else value
                    actual=n.runner.state.uregs[st.UREG_CODES[name]].value
                    assert actual==expected,(typ,track,raw,name,actual,expected)
                assert n.get(STACK-56)==0x4567,"displaced parallel store changed"
                assert n.data(frame,0x73c)==before,"wire/frame was mutated"
                count+=1
    r={"status":"PASS","sha256":hashlib.sha256(a.image.read_bytes()).hexdigest(),
       "cases":count,"types":[*range(15),255],"all16tracks":True,
       "raw_frame_unchanged":True,"stack_and_register_preservation":True,
       "max_instructions":max(x[1] for x in n.cost),
       "tier":"native hook/tail instructions, explicit register/frame fixtures; not natural playback"}
    a.report.write_text(json.dumps(r,indent=2)+"\n");print(json.dumps(r),flush=True)


if __name__=="__main__":main()
