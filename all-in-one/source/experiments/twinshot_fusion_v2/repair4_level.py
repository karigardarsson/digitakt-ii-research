"""Full native block/AMP ownership probe: DEP=0 must be response-independent.

Generated resident samples and reconstructed CF frames, not physical hardware.
Never bypass native gain/active gates. Reuse the image-bound real-init snapshot.
"""
import argparse
import hashlib
import json
import struct
from pathlib import Path
import numpy as np
from frame_lifecycle_proof import Native,Snapshot,sections_of,sharc,sharc_symbols,h,sr,layout,LoadedMemory


def main():
    ap=argparse.ArgumentParser(__doc__)
    for n in ("image","plan","init","report"):ap.add_argument("--"+n,type=Path,required=True)
    a=ap.parse_args(); sha=hashlib.sha256(a.image.read_bytes()).hexdigest()
    assert json.loads(a.init.with_name("native-init.json").read_text())["image_sha256"]==sha
    memory=LoadedMemory.from_stream(sections_of(a.image)[7])
    img=sharc.load("dt2-1.17")
    profile=sharc_symbols.resolve(img,device="dt2",reference=img)
    layout._PROFILE_CACHE["dt2-1.17"]=profile
    snap=Snapshot("out/runs/patchkit/run-plaits-lfo-probe/cards/f5c765a4acfc/ready/digi-audio-m5.snap")
    source=snap.read(0x80005348,2748)
    plan=json.loads(a.plan.read_text())
    p=next(v for k,v in plan["symbols"].items() if k.endswith(":tf_states."))
    rows=[]
    for response in (0,16256,32512):
        n=Native(memory,approx_recips=True); n.runner=sr.load_snapshot(str(a.init),memory)
        n.load_slots(); runner=n.runner; runner.state.approx_recips=True
        history=[]
        for block in range(5):
            tx=bytearray(source)
            def put(off,val):struct.pack_into(">H",tx,off,val)
            for off,val in ((0x22,int(block==0)),(0x24,0),(0x26,int(block==0)),(0x28,0),
                            (0x2a,0),(2,60<<8),(0x34,100<<8),(0x94,14)):put(off,val)
            for j,val in enumerate((0x4000,0,2<<8,7,0,0,0x4000,0,31,response)):put(0xda+2*j,val)
            h.write_dma_transfer(runner.state,"dt2-1.17",bytes(tx))
            runner=h.drive_dma_completion(runner,"dt2-1.17").fresh_call(profile.block_handler)
            runner.breakpoints=frozenset(); result=runner.run(300000)
            assert result.halt.reason=="return without followed call",result.halt.to_json()
            n.runner=runner
            after=np.frombuffer(n.data(n.get(0x24ef2c),128),dtype="<f4").copy()
            before=np.frombuffer(n.data(p+1100,128),dtype="<f4").copy()
            history.append({"block":block,"post_amp":after.tolist(),"private_B":before.tolist()})
            print("response",response,"block",block,"postAMP",float(np.max(np.abs(after))),"B",float(np.max(np.abs(before))),flush=True)
        rows.append({"response":response,"history":history})
    # Save measurements even if the isolation assertion fails.
    report={"sha256":sha,"rows":rows,"tier":"real native init/frame/block/AMP, generated resident PCM, reconstructed CF frames"}
    reference=rows[-1]["history"]
    report["response_independent_DEP0"]=all(np.array_equal(r["history"][-1]["post_amp"],reference[-1]["post_amp"]) for r in rows)
    a.report.write_text(json.dumps(report,indent=2)+"\n")
    print("response independent:",report["response_independent_DEP0"],flush=True)


if __name__=="__main__":main()
