"""Verify the actual extracted public ZIP, source build and wrong-device gate."""
from pathlib import Path
import json,hashlib,zipfile,tempfile,subprocess,sys,os,re
H=Path(__file__).resolve().parent;R=H.parent
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def main():
 delivery=json.loads((H/'DELIVERY.json').read_text(encoding='utf8'));archive=Path(delivery['Windows_bundle']['path']);assert sha(archive)==delivery['Windows_bundle']['sha256']
 checks=[]
 with tempfile.TemporaryDirectory(prefix='.rp-',dir=R) as tmp:
  base=Path(tmp)
  with zipfile.ZipFile(archive) as z:
   assert z.testzip() is None
   assert all(n.startswith('DT2-Sample-Editing-Pack/') and '..' not in Path(n).parts for n in z.namelist());z.extractall(base)
  p=base/'DT2-Sample-Editing-Pack';s=p/'source';inv=json.loads((s/'SOURCE-MANIFEST.json').read_text(encoding='utf8'))
  for name,m in inv.items():assert sha(s/name)==m['sha256'] and (s/name).stat().st_size==m['bytes'],name
  files={f.relative_to(p).as_posix():f for f in p.rglob('*') if f.is_file()};listed={}
  for line in (p/'SHA256SUMS.txt').read_text(encoding='utf8').splitlines():
   digest,name=line.split('  ',1);assert sha(files[name])==digest,name;listed[name]=digest
  assert set(files)==set(listed)|{'SHA256SUMS.txt'}
  assert not any(Path(n).suffix.lower() in {'.syx','.state','.raw','.wav','.pcm','.overlay','.dmp','.pyc'} for n in files)
  for f in [p/'README.md',p/'HARDWARE_ACCEPTANCE.md',p/'TEST_REPORT.md',*list((p/'docs').glob('*.md')),*list((p/'media/reddit').glob('*.md'))]:
   for target in re.findall(r'\]\(([^)]+)\)',f.read_text(encoding='utf8')):
    if '://' not in target:assert (f.parent/target.split('#')[0]).is_file(),(f,target)
  env=os.environ.copy();env['PYTHONPATH']='';env.pop('PYTHONHOME',None);stock=R/'.digikit-work/plaits-va-run02/firmware/Digitakt_II_OS1.17.syx';env['DT2_TEST_STOCK']=str(stock)
  def run(label,args,expected=0):
   result=subprocess.run([str(a) for a in args],cwd=s,env=env,capture_output=True,text=True,timeout=180)
   assert result.returncode==expected,(label,result.returncode,result.stdout,result.stderr)
   checks.append(dict(name=label,status='PASS',stdout=result.stdout,stderr=result.stderr))
  run('independent-source-18-tests',[sys.executable,s/'test_builder.py'])
  run('independent-source-GUI-checks',[sys.executable,s/'test_gui.py'])
  run('independent-seven-object-relinks',[sys.executable,s/'builder-source/relink_modules.py'])
  out=base/'actual source output.syx';report=base/'source-build.json'
  run('actual-extracted-source-stock-input',[sys.executable,s/'builder-source/builder_gui.py','--build',stock,'--output',out,'--report',report])
  assert sha(out)==delivery['firmware']['sha256'] and out.read_bytes()==Path(delivery['firmware']['path']).read_bytes()
  source_result=json.loads(report.read_text(encoding='utf8'));assert source_result['status']=='PASS'
  exe=p/'DT2-Sample-Editing-Builder.exe';assert sha(exe)==delivery['Windows_EXE']['sha256']
  reject=base/'reject.syx';badreport=base/'wrong-device.json'
  run('actual-Syntakt-OS1.41-rejected',[exe,'--build',R/'release_stage/modular_test/Syntakt_OS1.41.syx','--output',reject,'--report',badreport],1)
  assert not reject.exists() and json.loads(badreport.read_text(encoding='utf8'))['status']=='REJECTED'

 result=dict(status='PASS',public_archive_sha256=sha(archive),files_verified=len(files),source_inventory_exact=True,distribution_inventory_exact=True,relative_document_links=True,no_proprietary_firmware_or_PCM_in_bundle=True,independent_checks=checks,source_output=source_result,all_source_frozen_and_native_outputs_exact=True)
 path=H/'validation/distribution-tests.json';path.write_text(json.dumps(result,indent=2,ensure_ascii=False)+'\n',encoding='utf8')
 delivery.update(status='HARDWARE_ACCEPTED_RELEASE_PREPARED_NOT_PUBLISHED',final_distribution_validation=dict(path=str(path.resolve()),sha256=sha(path)))
 (H/'DELIVERY.json').write_text(json.dumps(delivery,indent=2,ensure_ascii=False)+'\n',encoding='utf8')
 (H/'SHA256SUMS.txt').write_text(''.join(delivery[k]['sha256']+'  '+os.path.relpath(delivery[k]['path'],H).replace('\\','/')+'\n' for k in ('firmware','Windows_bundle','Windows_EXE'))+sha(H/'DELIVERY.json')+'  DELIVERY.json\n',encoding='utf8')
 print('PASS actual final ZIP:',len(files),'file hashes/CRC/links; source18 tests/seven relinks/exact stock-output; real wrong-device rejection',flush=True)
if __name__=='__main__':main()
