"""One native value-path observation, using the existing CF fixture."""
import json
import struct
import subprocess
import hashlib
from pathlib import Path
from patchkit import test_all_in_one25 as u
from tools.patchkit import pk_cfcall,pk_link
HERE=Path(__file__).resolve().parent;W=HERE.parents[1]/'out/all-in-one-polish-fang-r4-work'
def main():
    subprocess.run([str(pk_link._tool('gcc')),*pk_link.CFLAGS,'-S','-o',str(W/'panel-before-mix.s'),str(HERE/'panel.c')],check=True)
    p,v,s,report=u.fixture.__wrapped__();pk_cfcall._built=True
    plan=json.loads((W/'fang-seed.plan.json').read_text())
    from build import sym as lookup
    _,stub,_=pk_link.build([HERE/'mix_capture.s'],0x47f20000,{'panel_original':report['panel']['symbols']['panel_draw'],
        'previous_original':lookup(plan,'fusion_widget')})
    edits={**p,s+0xa2:bytes([12]),v+0x90:bytes(4),s+0x14+64:struct.pack('>H',16384),
        0x401edd4c:struct.pack('>I',stub.symbols['mix_capture']),0x47f20000:stub.image,0x47f10000:bytes(44)}
    m=pk_cfcall.CfMachine('Digitakt_II_OS1.17.syx',timeout=120)
    rr=u.checked(m.run(edits,[u.clear(),{**u.page_call(v),'reads':[(0x47f10000,44)]},
        {'pc':0x4006978e,'args':[v,284,-1]}]))
    args=list(struct.unpack('>11I',rr[1].reads[0]));print(json.dumps(dict(actual_widget_args=args,
        incoming_raw=args[5],native_effective_value=rr[2].d0,stored_sound_word=16384)),flush=True)
    _,probes,_=pk_link.build([HERE/'mix_capture.s'],0x47f20000,{'panel_original':report['panel']['symbols']['panel_draw'],
        'previous_original':lookup(plan,'fusion_widget')})
    _,panel,_=pk_link.build([HERE/'panel.c'],0x47f30000,{'panel_previous':probes.symbols['probe_previous'],
        'panel_fill':probes.symbols['probe_fill'],'panel_text':probes.symbols['probe_text'],'panel_effective':0x4006978e,
        'fusion2_recovery_fmt':lookup(plan,'fusion2_recovery_fmt'),'fusion2_auto_fmt':lookup(plan,'fusion2_auto_fmt')})
    direct={**p,0x47f20000:probes.image,0x47f30000:panel.image,0x47f10100:bytes(40)}
    cases=[]
    for raw in (0,16256,16384,32512):
        actual=list(args);actual[5]=raw
        trace=u.checked(m.run(direct,[u.clear(),{'pc':panel.symbols['panel_draw'],'args':actual,
            'reads':[(0x47f10100,40)]}]))[-1]
        observed=list(struct.unpack('>10I',trace.reads[0]))
        assert observed[1:3]==[raw,raw] and observed[8:10]==[raw>>8,raw>>8],(raw,observed)
        cases.append(dict(raw=raw,text_argument=observed[8],a6_before_previous=observed[1],a6_after_previous=observed[2]))
    result=dict(status='PASS',image_sha256=hashlib.sha256(u.CANDIDATE.read_bytes()).hexdigest(),
        actual_widget_args=args,incoming=args[5],effective=rr[2].d0,cases=cases,
        evidence='Actual native page arguments plus virtual-only linked draw trace; no firmware instrumentation')
    (W/'mix-value-probe.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result),flush=True)
if __name__=='__main__':main()
