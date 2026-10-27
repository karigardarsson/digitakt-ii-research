"""Promote exactly one image only after all focused final-image gates pass."""
import hashlib
import json
from pathlib import Path
import shutil
import xml.etree.ElementTree as ET
from tools.patchkit import pk_build,patchkit
HERE=Path(__file__).resolve().parent;DV=HERE.parents[1]
W=DV/'out/all-in-one-polish-fang-r4-work';HOST=DV.parent/'plaits-va-run02/out/spacejam-hopt2-playable'
SRC=HOST/'out/all-in-one-plaits25-e48/source'
def main():
    sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
    image=W/'complete-polished.syx';digest=sha(image);reports={}
    build=json.loads((W/'polished-build.json').read_text())
    assert build['candidate']['sha256']==digest and build['panel']['source_sha256']==sha(HERE/'panel.c')
    for name in ('controls','native','gain','preservation','dsp-regression','panel-test','mix-value-probe'):
        r=json.loads((W/(name+'.json')).read_text());assert r['status']=='PASS' and r['image_sha256']==digest,name
        reports[name]=sha(W/(name+'.json'))
    panel=json.loads((W/'panel-test.json').read_text())
    assert panel['stock_framebuffers_exact']==list(range(7)) and panel['plaits_framebuffers_exact']==list(range(25))
    assert len(panel['native_encoder_mapping'])==24
    host=json.loads((W/'audio-host.json').read_text());assert host['status']=='PASS'
    assert host['source_sha256']==sha(HERE/'dsp/fang.c')
    reports['audio-host']=sha(W/'audio-host.json')
    suites=list(ET.parse(W/'integration.xml').iter('testsuite'))
    assert sum(int(s.attrib['tests']) for s in suites)==7
    assert all(not int(s.attrib['failures']) and not int(s.attrib['errors']) for s in suites)
    patchkit.binary=lambda build=True:SRC/'out/native/target/nogen/release/patchkit'
    current=pk_build.build_module(W/'catalog/modules/machines/sy-fang',check=True)
    assert len(current)==2
    baseline=DV/'out/twinshot-fus2-repair4/DT2_OS117_ALL_IN_ONE_FUS2_REPAIR4_CANDIDATE.syx'
    assert sha(baseline)=='66f022bf5640afb49c2dc5b02ec9b0cb9a8b3def30c84536e92154a29a9078fd'
    assert sha(SRC/'firmware/Digitakt_II_OS1.17.syx')=='26c22f6652625ac2cfd47f7ee970d388ed8b6427dae3563c0a6a2f2d334350d5'
    destination=DV/'out/FLASH_THIS_DT2_SY_FANG_POLISH'
    assert not destination.exists(),'stop on any existing delivery folder; never delete or overwrite'
    destination.mkdir()
    file=destination/'DT2_OS117_ALL_IN_ONE_SY_FANG_POLISH_CANDIDATE.syx'
    shutil.copyfile(image,file)
    assert sha(file)==digest and list(destination.iterdir())==[file]
    result=dict(status='OFFLINE_GATES_PASS_HARDWARE_UNTESTED',sha256=digest,file=str(file),file_count=1,
                report_hashes=reports,final_freshness=current,
                source_hashes={str(p.relative_to(HERE)):sha(p) for p in HERE.rglob('*')
                    if p.is_file() and p.suffix in ('.py','.c','.s','.toml','.md')})
    (W/'final-gate.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2),flush=True)
if __name__=='__main__':main()
