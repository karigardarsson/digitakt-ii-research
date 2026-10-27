"""Existing native gain consumer: type16 BITE isolation and stock fall-through."""
import hashlib
import json
from pathlib import Path
from native_dual_proof import Native,STACK
from tools.patchkit.port_resolve import sections_of
from tools.sharc_dev.sharcldr import LoadedMemory
from tools.sharc_dev import sharc_trace as st
HERE=Path(__file__).resolve().parent;W=HERE.parents[1]/'out/all-in-one-polish-fang-r4-work'

def main():
    image=W/'complete-polished.syx';n=Native(LoadedMemory.from_stream(sections_of(image)[7]));count=0
    for typ in (*range(17),255):
        for track in range(16):
            for raw in (0,16256,32512):
                n.put(0x2558dc+0x94+2*track,typ,2)
                before=n.data(0x2558dc,0x73c)
                regs=dict(R0=0x1234,R1=0x2345,R2=0x3456,R8=raw,R10=0x4567,I4=0x5678,M4=track,
                          I7=STACK-200,B7=STACK-1024,L7=2048,MODE1=0x11800)
                n.call(0x1c2654,regs,stop=0x1c2657,bound=200)
                for k,v in regs.items():
                    expected=32512 if typ in (14,16) and k=='R8' else 0 if k=='R10' else v
                    assert n.runner.state.uregs[st.UREG_CODES[k]].value==expected,(typ,track,raw,k)
                assert n.get(STACK-56)==0x4567
                assert n.data(0x2558dc,0x73c)==before
                count+=1
    r=dict(status='PASS',image_sha256=hashlib.sha256(image.read_bytes()).hexdigest(),cases=count,
           stock_and_Repair4_gain_unchanged=True,raw_frame_unchanged=True,max_instructions=max(x[1] for x in n.cost))
    (W/'gain.json').write_text(json.dumps(r,indent=2)+'\n');print(json.dumps(r),flush=True)

if __name__=='__main__':main()
