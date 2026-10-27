"""Small serial verification entry points; retained full stdout and traceback."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
HERE=Path(__file__).resolve().parent;DV=HERE.parents[1]
HOST=DV.parent/'plaits-va-run02/out/spacejam-hopt2-playable'
SRC=HOST/'out/all-in-one-plaits25-e48/source';WORK=DV/'out/all-in-one-polish-fang-r4-work'

def main():
    ap=argparse.ArgumentParser(__doc__);ap.add_argument('phase',choices=['panel','controls','integration','native','gain','preserve','dsp','all','finish','final-cf','mix','deliver']);a=ap.parse_args()
    if a.phase in ('all','finish','final-cf'):
        if a.phase=='all':subprocess.run([sys.executable,str(HERE/'test_audio.py')],check=True)
        phases=('controls','native','gain','preserve','dsp','integration','panel') if a.phase=='all' else ('preserve','dsp','integration','panel') if a.phase=='finish' else ('controls','preserve','integration','panel')
        for phase in phases:
            print('START '+phase,flush=True)
            subprocess.run([sys.executable,str(HERE/'verify.py'),phase],check=True)
        print('All focused final-image gates PASS',flush=True);return
    env={**os.environ,'PYTHONPATH':':'.join(map(str,(SRC,SRC/'tests',HOST,HERE.parent/'twinshot',HERE.parent/'twinshot_fusion_v2',DV/'experiments/dt2_dv_offline',DV/'.venv/lib/python3.12/site-packages'))),
         'AIO25_CANDIDATE':str(WORK/'complete-polished.syx'),'AIO25_REPORT':str(WORK/'polished-build.json'),
         'AIO25_UI_OUT':str(WORK/'final-screens')}
    if a.phase in ('mix','deliver'):
        cmd=[str(HOST/'.venv/bin/python'),str(HERE/('probe_mix.py' if a.phase=='mix' else 'deliver.py'))]
    elif a.phase in ('panel','controls','gain','preserve','dsp'):
        cmd=[str(HOST/'.venv/bin/python'),str(HERE/('test_'+a.phase+'.py'))]
    elif a.phase=='native':
        cmd=[str(HOST/'.venv/bin/python'),str(HERE/'test_native.py'),'--image',str(WORK/'complete-polished.syx')]
    else:
        cmd=[str(HOST/'.venv/bin/python'),'-c',"from tools.patchkit import pk_cfcall; pk_cfcall._built=True; import pytest; raise SystemExit(pytest.main(['-q','tests/patchkit/test_all_in_one25.py','tests/patchkit/test_all_in_one25_musical.py','--junitxml="+str(WORK/'integration.xml')+"']))"]
    with (WORK/(a.phase+'.log')).open('w') as f:r=subprocess.run(cmd,cwd=SRC,env=env,stdout=f,stderr=subprocess.STDOUT)
    print((WORK/(a.phase+'.log')).read_text()[-12000:],flush=True)
    raise SystemExit(r.returncode)

if __name__=='__main__':main()
