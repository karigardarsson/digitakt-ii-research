"""Final accepted-status packaging; no firmware compilation or modification."""
from pathlib import Path
import json,hashlib,shutil,zipfile
H=Path(__file__).resolve().parent;R=H.parent;OLD=R/'DT2_SAMPLE_EDITING_FINAL_POLISH_20261105';P=H/'release/DT2-Sample-Editing-Pack'
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def main():
 prior=json.loads((OLD/'DELIVERY.json').read_text(encoding='utf8'));firmware=Path(prior['firmware']['path']);digest=prior['firmware']['sha256']
 assert digest=='880d810f036bdc56a5aac0fb85d249366aa8678fbdd7435b1fd95a7ed4a1df06' and sha(firmware)==digest==sha(H/'work/from-frozen-final.syx')
 assert sha(Path(prior['Windows_bundle']['path']))==prior['Windows_bundle']['sha256']
 protected=json.loads((OLD/'validation/protected-baselines.json').read_text())['unchanged']
 protected[str(firmware.relative_to(R))]=digest;protected[str(Path(prior['Windows_bundle']['path']).relative_to(R))]=prior['Windows_bundle']['sha256']
 for name,value in protected.items():assert sha(R/name)==value,name
 (H/'validation/protected-baselines.json').write_text(json.dumps(dict(status='PASS',unchanged=protected),indent=2)+'\n')
 assert not P.exists();shutil.copytree(Path(prior['folder']),P,ignore=shutil.ignore_patterns('source','SHA256SUMS.txt','TEST_REPORT.md','DT2-Sample-Editing-Builder.exe'))
 exe=H/'Windows/DT2-Sample-Editing-Builder.exe';shutil.copyfile(exe,P/exe.name);shutil.copyfile(H/'TEST_REPORT.md',P/'TEST_REPORT.md')
 accepted=dict(date='2026-11-05',firmware_sha256=digest,status='USER_HARDWARE_ACCEPTED',reported_by='Kári',report='H is now good and hardware testing passes. GO to remove the builder acceptance warning and finish the final release ZIP. Keep firmware unchanged. Do not flash or publish.',independently_observed_by_agent=False)
 (H/'validation/hardware-acceptance.json').write_text(json.dumps(accepted,indent=2,ensure_ascii=False)+'\n',encoding='utf8')
 (P/'HARDWARE_ACCEPTANCE.md').write_text('# Hardware acceptance recorded\n\nKári accepted the existing Sample Editing Pack firmware on2026-11-05:\n\n`'+digest+'`\n\nUser report: H is now good and hardware testing passes. Final local release\npackaging and removal of the builder warning were authorized. This is the\nuser\'s hardware report, not an agent-observed test or an invented per-item\nchecklist. The firmware is unchanged. No flashing or publication was authorized\nor performed. Historical offline/pending reports remain in source/release-evidence\nwith their original identities; this acceptance record supersedes their release\nstatus.\n',encoding='utf8')
 p=P/'README.md';s=p.read_text(encoding='utf8').replace('**Release preparation candidate. Final hardware acceptance is pending.**','**Final local release package. Hardware accepted by Kári on2026-11-05.**')
 start=s.index('This update preserves');end=s.index('Optional file-only',start)
 s=s[:start]+('Kári has accepted this exact firmware on hardware. This final packaging step\nonly removes the builder acceptance warning; firmware and generation logic are\nunchanged. The existing default firmware filename is retained. The Ready log,\nlarger explanatory text, all four features and source/license notices remain.\n\n')+s[end:]
 s=s.replace('[Hardware acceptance](HARDWARE_ACCEPTANCE.md): remaining physical checks.','[Hardware acceptance](HARDWARE_ACCEPTANCE.md): dated user acceptance record.')
 s=s.replace('That source/unchanged-code\nclaim does not replace physical save/reload or stock-rollback acceptance.','Hardware acceptance is recorded separately;\nno new storage format or rollback behavior was introduced during final packaging.')
 p.write_text(s,encoding='utf8')
 p=P/'docs/HOW_TO.md';s=p.read_text(encoding='utf8').replace("Release candidate: final acceptance on Kári's Digitakt II is still required.","Hardware accepted by Kári on2026-11-05; firmware hash is in the release README.")
 s=s.replace('Confirm the physical feel during hardware acceptance.','The revised H response was accepted by Kári on hardware.')
 s=s.replace('The complete physical SRC-menu route needs final hardware confirmation; no\nunverified menu-list screenshot is included.','No full SRC menu-list screenshot is included; the capture set covers the\nnative dialogs and resulting markers.')
 s=s.replace('Repeated native LENGTH 64 requests after weak/failed detection remain\npart of final physical acceptance, including sparse and very short samples.','For future regression checks, repeat native LENGTH 64 requests after weak/failed\ndetection, including sparse and very short samples.');p.write_text(s,encoding='utf8')
 p=P/'docs/CAPTURE_NOTES.md';s=p.read_text(encoding='utf8');s=s.replace('Physical encoder feel, recording, playback, CFADE sound and final release\nacceptance remain hardware checks.','Kári reports that H feels good and hardware testing passes on2026-11-05.\nThe hardware acceptance record is separate from these emulator captures.');p.write_text(s,encoding='utf8')
 for p in (P/'media/reddit').glob('*.gif'):assert sha(p)==sha(Path(prior['folder'])/'media/reddit'/p.name)
 for p in (P/'docs/images').rglob('*.png'):assert sha(p)==sha(Path(prior['folder'])/'docs/images'/p.relative_to(P/'docs/images'))
 S=P/'source';S.mkdir();shutil.copytree(H/'builder-source',S/'builder-source',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
 for name in ('test_builder.py','test_gui.py'):shutil.copyfile(H/name,S/name)
 proof=S/'release-evidence';proof.mkdir();shutil.copytree(Path(prior['folder'])/'source/release-evidence',proof/'accepted-firmware-history')
 shutil.copyfile(OLD/'TEST_REPORT.md',proof/'H-FIX-HISTORICAL-TEST_REPORT.md')
 for p in (H/'validation').glob('*.json'):shutil.copyfile(p,proof/p.name)
 prep=S/'release-preparation';prep.mkdir()
 for name in ('test_frozen.py','package_release.py','audit_release.py'):shutil.copyfile(H/name,prep/name)
 (prep/'README.txt').write_text('Finalization scripts require original checkout paths and user-owned stock. No firmware is supplied. End-user EXE and portable builder source are independent of the workspace. Historical native proof remains under release-evidence.\n')
 inv={p.relative_to(S).as_posix():dict(bytes=p.stat().st_size,sha256=sha(p)) for p in sorted(S.rglob('*')) if p.is_file()};(S/'SOURCE-MANIFEST.json').write_text(json.dumps(inv,indent=2,ensure_ascii=False)+'\n',encoding='utf8')
 files=[p for p in sorted(P.rglob('*')) if p.is_file()];assert not any(p.suffix.lower() in {'.syx','.state','.overlay','.raw','.wav','.pcm','.dmp','.pyc'} for p in files)
 (P/'SHA256SUMS.txt').write_text(''.join(sha(p)+'  '+p.relative_to(P).as_posix()+'\n' for p in files),encoding='utf8')
 archive=H/'DT2-Sample-Editing-Pack-Windows-x64.zip';assert not archive.exists()
 with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED,compresslevel=9) as z:
  for p in sorted(P.rglob('*')):
   if p.is_file():
    info=zipfile.ZipInfo('DT2-Sample-Editing-Pack/'+p.relative_to(P).as_posix(),(2026,11,5,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED;info.external_attr=0o100644<<16;z.writestr(info,p.read_bytes())
 def entry(p):return dict(path=str(p.resolve()),bytes=p.stat().st_size,sha256=sha(p))
 delivery=dict(status='FINAL_PACKAGE_PENDING_AUDIT',firmware=entry(firmware),Windows_bundle=entry(archive),Windows_EXE=entry(P/exe.name),folder=str(P.resolve()),hardware_tested=True,hardware_acceptance=accepted,firmware_unchanged=True,no_flash_or_publication=True)
 (H/'DELIVERY.json').write_text(json.dumps(delivery,indent=2,ensure_ascii=False)+'\n',encoding='utf8');print('FINAL ZIP',sha(archive))
if __name__=='__main__':main()
