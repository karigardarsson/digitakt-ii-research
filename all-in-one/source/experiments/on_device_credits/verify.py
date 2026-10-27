"""Final package and unchanged-DSP gates; no speculative full-engine rerun."""
import hashlib,json,os,subprocess,sys
from pathlib import Path
from build import HERE,ROOT,WORK,BASE,BASE_SHA,ADDRESS,HOOK,compile_ui
from tools.patchkit import pk_integrate_dv_zoom as dv,pk_cfcall
from tools.sharc_dev import sharc_bootcheck
HOST=ROOT.parent/'plaits-va-run02/out/spacejam-hopt2-playable'
SRC=HOST/'out/all-in-one-plaits25-e48/source'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
    report=json.loads((WORK/'build.json').read_text())
    image=WORK/'DT2_OS117_ALL_IN_ONE_ON_DEVICE_CREDITS_CANDIDATE.syx'
    assert sha(BASE)==BASE_SHA and sha(image)==report['candidate']['sha256']
    code,fresh=compile_ui()
    pack,_,_=dv.load_v11(ROOT,musical=True)
    _,_,old=pack.decode_all(BASE);_,_,new=pack.decode_all(image)
    assert dv.at(new[3]['plain'],dv.source_for_loaded_address(new[3]['plain'],ADDRESS),len(code))==code
    assert fresh['symbols']==report['credits']['symbols'] and fresh['bss']==0
    protected=[]
    for sid in old:
        if sid!=3:
            assert old[sid]['stored']==new[sid]['stored'] and old[sid]['plain']==new[sid]['plain']
            protected.append(sid)
    segments,end=dv.parsed_segments(old[3]['plain']);last=dv.parsed_segments(new[3]['plain'])[0][-1]
    fixed=bytearray(new[3]['plain'][:end]);dv.put(fixed,HOOK,dv.at(old[3]['plain'],HOOK,6))
    assert bytes(fixed)==old[3]['plain'][:end]
    assert last[1:]==(len(code),ADDRESS,0) and last[0]%4==0 and len(code)%4==0
    assert dv.at(new[3]['plain'],dv.INTRO_SCENES,80)==dv.STOCK_INTRO_SCENES
    assert sharc_bootcheck.check(new[7]['plain']).ok
    for name in ('NOTICE','LICENSE'):assert (HERE/name).read_bytes()==(SRC/name).read_bytes()
    pk_cfcall._built=True
    env={**os.environ,'PYTHONPATH':':'.join(map(str,(SRC,SRC/'tests',HOST,
        ROOT/'experiments/dt2_dv_offline',ROOT/'.venv/lib/python3.12/site-packages'))),
        'AIO25_CANDIDATE':str(image),'AIO25_REPORT':str(WORK/'build.json'),'CREDITS_FINAL':'1',
        'AIO25_UI_OUT':str(WORK/'regression-previews')}
    with (WORK/'credits-final.log').open('w') as log:
        subprocess.run([sys.executable,str(HERE/'test_credits.py')],env=env,stdout=log,stderr=subprocess.STDOUT,check=True)
    with (WORK/'integration.log').open('w') as log:
        command="from tools.patchkit import pk_cfcall; pk_cfcall._built=True; import pytest; raise SystemExit(pytest.main(['-q','tests/patchkit/test_all_in_one25.py','tests/patchkit/test_all_in_one25_musical.py','--junitxml="+str(WORK/'integration.xml')+"']))"
        subprocess.run([sys.executable,'-c',command],cwd=SRC,env=env,stdout=log,stderr=subprocess.STDOUT,check=True)
    # Bind previously completed native/PCM results only by exact image identity
    # of every existing executable byte, not by changing old report hashes.
    prior=ROOT/'out/all-in-one-polish-fang-r4-work'
    inherited={}
    for name in ('native','controls','gain','preservation','dsp-regression','panel-test','mix-value-probe'):
        path=prior/(name+'.json');r=json.loads(path.read_text())
        assert r['status']=='PASS' and r['image_sha256']==BASE_SHA
        inherited[name]={'original_image_sha256':BASE_SHA,'report_sha256':sha(path),
            'reuse_basis':'All BLOB7 and all preexisting MAIN/segments except System ctor tail are byte-identical'}
    result=dict(status='OFFLINE_GATES_PASS_HARDWARE_UNTESTED',image_sha256=sha(image),
        preserved_sections=protected,all_prior_main_except_system_tail_identical=True,
        payload_bytes=len(code),payload_bss=0,source_freshness=fresh['source_sha256'],
        original_notice_sha256=sha(HERE/'NOTICE'),original_license_sha256=sha(HERE/'LICENSE'),
        stock_intro_callbacks_and_durations_exact=True,additional_startup_frames=0,
        public_beta_extra_frames_removed=150,public_beta_seconds_at_30fps=5,
        actual_wallclock_boot_measurement=False,reused_baseline_reports=inherited,
        new_reports={n:sha(WORK/n) for n in ('credits-final-test.json','integration.xml')})
    (WORK/'final-gate.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2),flush=True)
if __name__=='__main__':main()
