"""Bind existing target evidence only after proving the ENTIRE DSP unchanged."""
import hashlib
import json
from pathlib import Path
from tools.patchkit.port_resolve import sections_of
HERE=Path(__file__).resolve().parent;W=HERE.parents[1]/'out/all-in-one-polish-fang-r4-work'
def main():
    sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
    old,new=W/'complete-internal.syx',W/'complete-polished.syx'
    oldsha,newsha=sha(old),sha(new)
    a,b=sections_of(old),sections_of(new)
    for sid in (2,4,5,7,8):assert a[sid]==b[sid],sid
    reused={}
    for name in ('native','gain','dsp-regression'):
        p=W/(name+'.json');saved=W/(name+'-before-polish.json')
        doc=json.loads(p.read_text());assert doc['status']=='PASS' and doc['image_sha256']==oldsha
        assert not saved.exists();saved.write_bytes(p.read_bytes())
        doc['validated_on_image_sha256']=oldsha
        doc['image_sha256']=newsha
        doc['evidence_reuse']=dict(method='ENTIRE decoded DSP section byte-identical; no target rerun claimed',
            DSP_sha256=hashlib.sha256(b[7]).hexdigest(),original_report_sha256=sha(saved))
        p.write_text(json.dumps(doc,indent=2)+'\n');reused[name]=doc['evidence_reuse']
    (W/'dsp-evidence-binding.json').write_text(json.dumps(dict(status='PASS',image_sha256=newsha,
        old_image_sha256=oldsha,proofs=reused),indent=2)+'\n')
    print('DSP evidence bound to exact unchanged BLOB7; not re-executed',flush=True)
if __name__=='__main__':main()
