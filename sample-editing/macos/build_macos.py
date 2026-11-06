"""Build and qualify a native Mac app using existing preferred source and assets."""
from pathlib import Path
import argparse,hashlib,json,os,platform,shutil,subprocess,sys,urllib.request,zipfile,io
HERE=Path(__file__).resolve().parent;PACK=HERE.parent
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def run(*args,env=None):subprocess.run([str(a) for a in args],check=True,env=env)
def official_stock(url,work):
    if not url:return None
    if not url.startswith('https://'):raise ValueError('Stock input URL must use HTTPS')
    with urllib.request.urlopen(url,timeout=90) as r:raw=r.read(32*1024*1024+1)
    if len(raw)>32*1024*1024:raise ValueError('Stock download exceeds limit')
    expected='26c22f6652625ac2cfd47f7ee970d388ed8b6427dae3563c0a6a2f2d334350d5'
    if raw.startswith(b'PK'):
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            candidates=[z.read(n) for n in z.namelist() if n.lower().endswith('.syx') and z.getinfo(n).file_size<8*1024*1024]
        raw=next((b for b in candidates if hashlib.sha256(b).hexdigest()==expected),b'')
    if hashlib.sha256(raw).hexdigest()!=expected:raise ValueError('Stock download is not exact official OS1.17')
    p=work/'private-stock-input.syx';p.write_bytes(raw);return p
def main():
    a=argparse.ArgumentParser();a.add_argument('--output',type=Path,required=True);a.add_argument('--stock-url',default='');args=a.parse_args()
    assert sys.platform=='darwin','Build this app on a real macOS host'
    arch=platform.machine();assert arch in ('arm64','x86_64');out=args.output.resolve();out.mkdir(parents=True,exist_ok=False)
    work=out/'work';work.mkdir();stage=work/'builder-source'
    shutil.copytree(PACK/'source/builder-source',stage,ignore=shutil.ignore_patterns('native','__pycache__','*.pyc','dist','packaging-work'))
    native=stage/'native';native.mkdir();tool=native/'elektron-firmware-tool'
    encoder=work/'encoder';shutil.copytree(stage/'vendor/elektron-firmware-tool',encoder)
    env=os.environ.copy();env['MACOSX_DEPLOYMENT_TARGET']='15.0'
    run('make','-C',encoder,'-j2','CC=clang','CFLAGS=-O2 -mmacosx-version-min=15.0',env=env)
    shutil.copyfile(encoder/'elektron-firmware-tool',tool);tool.chmod(0o755)
    from PyInstaller.utils.osx import sign_binary
    sign_binary(str(tool))
    hashes={p.relative_to(stage).as_posix():sha(p) for folder in ('assets','native') for p in sorted((stage/folder).rglob('*')) if p.is_file()}
    (stage/'resource_seal.py').write_text('"""Generated native macOS resource seal."""\nRESOURCE_HASHES = '+repr(hashes)+'\n')
    shutil.copyfile(HERE/'launcher.py',stage/'launcher.py');env['DT2_MACOS_STAGE']=str(stage)
    run(sys.executable,'-m','PyInstaller','--noconfirm','--distpath',out/'dist','--workpath',work/'pyinstaller',HERE/'DT2-Sample-Editing-Mac.spec',env=env)
    bundle=out/'dist/DT2 Sample Editing Builder.app';stock=official_stock(args.stock_url,work)
    from verify_macos import verify
    reports=out/'reports';result=verify(bundle,stage,reports,stock)
    product=work/('DT2-Sample-Editing-Pack-macOS-'+arch);product.mkdir()
    run('/usr/bin/ditto',bundle,product/bundle.name)
    # Preferred source and notices accompany the app, never private test input/output.
    source=product/'source';source.mkdir();shutil.copytree(stage,source/'builder-source',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    shutil.copytree(HERE,product/'macos',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    shutil.copyfile(PACK/'source/test_builder.py',source/'test_builder.py')
    shutil.copytree(stage/'licenses',product/'licenses')
    for name in ('LICENSE','NOTICE','THIRD_PARTY_NOTICES.txt'):shutil.copyfile(stage/name,product/name)
    shutil.copyfile(HERE/'README.md',product/'README.md')
    shutil.copyfile(reports/'validation.json',product/'VALIDATION.json')
    files=[p for p in product.rglob('*') if p.is_file()]
    assert not any(p.suffix.lower() in ('.syx','.raw','.wav','.pcm','.state','.overlay','.exe','.o') for p in files)
    publish=out/'publish';publish.mkdir();archive=publish/(product.name+'.zip')
    run('/usr/bin/ditto','-c','-k','--sequesterRsrc','--keepParent',product,archive)
    metadata=dict(status='PACKAGED_AND_GUI_TESTED',architecture=arch,tested_macOS=platform.mac_ver()[0],
                  source_commit=os.environ.get('GITHUB_SHA','local'),python=platform.python_version(),
                  archive=archive.name,bytes=archive.stat().st_size,sha256=sha(archive),
                  exact_stock_build=result['exact_stock_build'],firmware_sha256=result['firmware_expected_sha256'],
                  notarized=False,minimum_macOS='15.0',native_tool_sha256=result['native_tool_sha256'])
    (publish/'BUILD.json').write_text(json.dumps(metadata,indent=2)+'\n')
    shutil.copyfile(reports/'validation.json',publish/'VALIDATION.json')
    (publish/'SHA256SUMS.txt').write_text(sha(archive)+'  '+archive.name+'\n')
    print(json.dumps(metadata,indent=2),flush=True)
if __name__=='__main__':main()
