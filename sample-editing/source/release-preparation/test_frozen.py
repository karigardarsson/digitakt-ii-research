"""Run the actual frozen EXE with only itself beside it and system-only PATH."""
from pathlib import Path
import hashlib
import json
import os
import shutil
import subprocess
import tempfile

HERE=Path(__file__).resolve().parent;ROOT=HERE.parent
STOCK=ROOT/'.digikit-work/dv-offline-v1/out/smart-overflow-recovery-20261030/sample-lab/stock-fixture.syx'
REFERENCE=ROOT/'DT2_SAMPLE_EDITING_FINAL_POLISH_20261105/HARDWARE_TEST/DT2_OS117_SAMPLE_EDITING_RELEASE_CANDIDATE.syx'
SHA='880d810f036bdc56a5aac0fb85d249366aa8678fbdd7435b1fd95a7ed4a1df06'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
    destination=HERE/'validation';destination.mkdir(exist_ok=True)
    env=os.environ.copy()
    system_root=Path(os.environ.get('SystemRoot',r'C:\Windows'))
    assert (system_root/'System32/kernel32.dll').is_file()
    env['SystemRoot']=str(system_root)
    env['PATH']=str(system_root/'System32')+';'+str(system_root)
    for key in ['PYTHONPATH','PYTHONHOME','VIRTUAL_ENV']:env.pop(key,None)
    cases=[]
    with tempfile.TemporaryDirectory(prefix='isolated Windows Kári ',dir=HERE/'inspection') as folder:
        base=Path(folder);exe=base/'Sample Editing Builder.exe'
        shutil.copyfile(HERE/'Windows/DT2-Sample-Editing-Builder.exe',exe)
        stock=base/'My Stock OS 1.17.syx';shutil.copyfile(STOCK,stock)
        out=base/'My Sample Editing Pack.syx'
        def run(label,source,output,expected):
            report=base/(label+'.json')
            completed=subprocess.run([exe,'--build',source,'--output',output,'--report',report],cwd=base,env=env,timeout=180)
            assert completed.returncode==expected,(label,completed.returncode,report.read_text() if report.exists() else None)
            data=json.loads(report.read_text(encoding='utf-8'))
            assert data['status']==('PASS' if expected==0 else 'REJECTED'),data
            cases.append(dict(name=label,exit_code=completed.returncode,result=data))
            return data
        run('clean-frozen-build',stock,out,0)
        assert out.read_bytes()==REFERENCE.read_bytes() and sha(out)==SHA
        # Keep one generated result privately outside the distributable folder.
        generated=HERE/'work/from-frozen-final.syx';assert not generated.exists()
        shutil.copyfile(out,generated)
        run('existing-output',stock,out,1);assert sha(out)==SHA
        run('same-input-output',stock,stock,1)
        run('already-modded',out,base/'reject-modded.syx',1)
        altered=base/'corrupt.syx';raw=bytearray(stock.read_bytes());raw[len(raw)//2]^=1;altered.write_bytes(raw)
        run('corrupt-stock',altered,base/'reject-corrupt.syx',1)
        truncated=base/'truncated.syx';truncated.write_bytes(stock.read_bytes()[:1000])
        run('truncated-stock',truncated,base/'reject-truncated.syx',1)
        other=base/'other-version-device.syx';other.write_bytes(b'\xf0\x00\x20\x3c\x06other OS\xf7')
        run('wrong-version-device-data',other,base/'reject-other.syx',1)
        run('missing-input',base/'missing.syx',base/'reject-missing.syx',1)
        run('missing-output-folder',stock,base/'not-here/out.syx',1)
        run('wrong-output-extension',stock,base/'wrong.txt',1)
        assert not list(base.glob('reject-*')) and not list(base.glob('.dt2-*'))
        assert sha(stock)==sha(STOCK)
    result=dict(status='PASS',exe_sha256=sha(HERE/'Windows/DT2-Sample-Editing-Builder.exe'),
                output_sha256=SHA,generated_output='work/from-frozen-final.syx',
                frozen_no_python_or_developer_PATH=True,windows_version=os.sys.getwindowsversion().build,
                spaces_and_accented_path=True,cases=cases,
                limitation='This host is Windows 11 x64. Clean VM, Windows 10, ARM64, antivirus/SmartScreen acceptance and non-Latin paths are untested.')
    (destination/'frozen-tests.json').write_text(json.dumps(result,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    print('PASS frozen standalone: exact approved output; nine rejection/file-safety cases.')
if __name__=='__main__':main()
