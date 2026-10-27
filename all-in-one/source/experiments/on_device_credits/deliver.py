"""Local source/notices/provenance handoff after final-image gates only.

digikit by Em D - https://github.com/ElekLabs/digikit
Modified firmware derived from DigiKit. Original NOTICE applies.
"""
import hashlib,json,shutil,zipfile,subprocess,sys,xml.etree.ElementTree as ET
from pathlib import Path
from build import HERE,ROOT,WORK,BASE,BASE_SHA
HOST=ROOT.parent/'plaits-va-run02/out/spacejam-hopt2-playable'
RELEASE=HOST/'release/DT2-All-in-One-v0.3.0-community-beta-polish'
DEST=ROOT/'out/DT2_ALL_IN_ONE_ON_DEVICE_CREDITS'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def git_head():
    # Windows-created worktrees have an absolute Windows gitdir. WSL git must
    # be pointed at the same directory explicitly; do not alter the .git file.
    path=(ROOT/'.git').read_text().strip().removeprefix('gitdir: ')
    if len(path)>2 and path[1]==':':path='/mnt/'+path[0].lower()+path[2:]
    return subprocess.check_output(['git','--git-dir='+path,'--work-tree='+str(ROOT),
        'rev-parse','HEAD'],text=True).strip()
def main():
    image=WORK/'DT2_OS117_ALL_IN_ONE_ON_DEVICE_CREDITS_CANDIDATE.syx'
    gate=json.loads((WORK/'final-gate.json').read_text())
    assert gate['status']=='OFFLINE_GATES_PASS_HARDWARE_UNTESTED'
    assert gate['image_sha256']==sha(image) and sha(BASE)==BASE_SHA
    for name in ('credits.c','hook.s','build.py','test_credits.py','verify.py','NOTICE','LICENSE'):
        assert gate['source_freshness'][name]==sha(HERE/name),name
    suite=list(ET.parse(WORK/'integration.xml').iter('testsuite'))
    assert sum(int(x.attrib['tests']) for x in suite)==7
    assert all(not int(x.attrib['failures']) and not int(x.attrib['errors']) for x in suite)
    for name,digest in gate['new_reports'].items():assert sha(WORK/name)==digest
    head=git_head()
    if DEST.exists():
        assert '--resume-incomplete' in sys.argv and not (DEST/'MANIFEST.json').exists()
        assert sha(DEST/'FLASH_THIS'/image.name)==sha(image)
        previous_overlay=DEST/'source/Current-Integration-Source-Overlay.zip'
        if previous_overlay.exists():
            saved=WORK/'source-overlay-first-pass.zip'
            if not saved.exists():shutil.copyfile(previous_overlay,saved)
    else:
        (DEST/'FLASH_THIS').mkdir(parents=True)
        (DEST/'source').mkdir();(DEST/'evidence').mkdir();(DEST/'previews').mkdir()
    shutil.copyfile(image,DEST/'FLASH_THIS'/image.name)
    for name in ('NOTICE','LICENSE','README.md','SOURCE.md'):shutil.copyfile(HERE/name,DEST/name)
    prior=RELEASE/'DT2-All-in-One-Corresponding-Source-r2.zip'
    original=json.loads((RELEASE/'source-stage.json').read_text())
    assert sha(prior)==original['source_zip_sha256']
    shutil.copyfile(prior,DEST/'source'/prior.name)
    for name in ('THIRD_PARTY_NOTICES.md',):shutil.copyfile(RELEASE/name,DEST/name)
    entries={}
    allowed={'.py','.c','.h','.s','.S','.toml','.md','.sh','.ps1'}
    folders=('on_device_credits','all_in_one_polish_fang','twinshot','twinshot_fusion','twinshot_fusion_v2','dt2_dv_offline')
    with zipfile.ZipFile(DEST/'source/Current-Integration-Source-Overlay.zip','w',zipfile.ZIP_DEFLATED) as z:
        for folder in folders:
            for path in (ROOT/'experiments'/folder).rglob('*'):
                if not path.is_file() or path.is_symlink():continue
                rel=path.relative_to(ROOT)
                if any(n in rel.parts for n in ('build','out','__pycache__','.venv','node_modules','target')):continue
                if path.suffix not in allowed and path.name not in ('NOTICE','LICENSE'):continue
                name=rel.as_posix();z.write(path,name);entries[name]=sha(path)
        for name in ('pk_integrate_dv_zoom.py','pk_all_in_one25.py','pk_plaits_ui.py'):
            path=HOST/'tools/patchkit'/name
            if path.is_file():
                key='host-tools/tools/patchkit/'+name;z.write(path,key);entries[key]=sha(path)
        path=ROOT/'docs/findings/dt2-on-device-credits.md'
        z.write(path,'docs/findings/dt2-on-device-credits.md');entries['docs/findings/dt2-on-device-credits.md']=sha(path)
        for name in ('NOTICE','LICENSE'):z.write(HERE/name,name)
    # Build reports are local evidence, never a Git/public firmware export.
    for name in ('build.json','final-gate.json','credits-final-test.json','integration.xml','integration.log','credits-final.log'):
        shutil.copyfile(WORK/name,DEST/'evidence'/name)
    baseline=ROOT/'out/all-in-one-polish-fang-r4-work'
    shutil.copyfile(baseline/'final-gate.json',DEST/'evidence/input-baseline-final-gate.json')
    shutil.copyfile(baseline/'polished-build.json',DEST/'evidence/input-baseline-build.json')
    for name in ('credits-1-6x.png','credits-2-6x.png','credits-3-6x.png','system-credits-entry-6x.png'):
        shutil.copyfile(WORK/'previews'/name,DEST/'previews'/name)
    assert len(list((DEST/'FLASH_THIS').glob('*.syx')))==1
    assert sha(DEST/'FLASH_THIS'/image.name)==gate['image_sha256']
    result=dict(status=gate['status'],candidate_sha256=gate['image_sha256'],base_sha256=BASE_SHA,
        source_git_head=head,
        local_uncommitted_ui_delta=True,source_overlay_sha256=entries,
        package_files={p.relative_to(DEST).as_posix():sha(p) for p in DEST.rglob('*') if p.is_file()})
    (DEST/'MANIFEST.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(dict(folder=str(DEST),file=image.name,sha256=gate['image_sha256'],
        source_overlay_files=len(entries),status=gate['status']),indent=2),flush=True)
if __name__=='__main__':main()
