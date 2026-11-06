"""Native packaged-app, dependency, resource and file-safety qualification."""
from pathlib import Path
import argparse,hashlib,json,os,platform,subprocess,sys,tempfile

def run(args,env=None,expected=0):
    c=subprocess.run([str(a) for a in args],capture_output=True,text=True,env=env,timeout=300)
    assert c.returncode==expected,(Path(str(args[0])).name,c.returncode,c.stdout[-1500:],c.stderr[-1500:])
    return c

def verify(bundle,stage,reports,stock=None):
    assert sys.platform=='darwin'
    reports.mkdir(parents=True,exist_ok=True);sys.path.insert(0,str(stage));import builder_core as core
    resources,manifest=core.checked_resources();tool=core.firmware_tool(resources)
    run([tool,'--help']);run(['/usr/bin/codesign','--verify','--deep','--strict',bundle])
    arch=platform.machine();assert arch in ('arm64','x86_64')
    deps=[];macho_magics={b'\xcf\xfa\xed\xfe',b'\xfe\xed\xfa\xcf',b'\xca\xfe\xba\xbe',b'\xbe\xba\xfe\xca'}
    for p in bundle.rglob('*'):
        if p.is_symlink():assert p.resolve().is_relative_to(bundle.resolve()),p.name
        if not p.is_file() or p.is_symlink():continue
        with p.open('rb') as f:magic=f.read(4)
        if magic not in macho_magics:continue
        arches=run(['/usr/bin/lipo','-archs',p]).stdout.strip();assert arch in arches.split(),(p.name,arches)
        libraries=[]
        for line in run(['/usr/bin/otool','-L',p]).stdout.splitlines()[1:]:
            lib=line.strip().split(' (',1)[0];assert lib.startswith(('@','/System/Library/','/usr/lib/')),(p.name,lib);libraries.append(lib)
        deps.append(dict(path=p.relative_to(bundle).as_posix(),architectures=arches,libraries=libraries))
    env=os.environ.copy();env['PATH']='/usr/bin:/bin:/usr/sbin:/sbin'
    for k in ('PYTHONPATH','PYTHONHOME','VIRTUAL_ENV','TCL_LIBRARY','TK_LIBRARY','DYLD_LIBRARY_PATH'):env.pop(k,None)
    cases=[]
    with tempfile.TemporaryDirectory(prefix='Mac app Kári ') as tmp:
        tmp=Path(tmp);relocated=tmp/bundle.name;run(['/usr/bin/ditto',bundle,relocated])
        exe=relocated/'Contents/MacOS/DT2 Sample Editing Builder';gui_report=tmp/'gui.json'
        run([exe,'--self-test',gui_report],env);gui=json.loads(gui_report.read_text());assert gui['native_architecture']==arch and gui['frozen']
        (reports/'packaged-gui.json').write_text(json.dumps(gui,indent=2)+'\n')
        bad=tmp/'Wrong stock á.syx';bad.write_bytes(b'not Elektron stock')
        # Exercise the native parser, independently of the Python input hash gate.
        native=subprocess.run([str(tool),'-i',str(bad)],capture_output=True,timeout=30);assert native.returncode!=0
        def reject(label,source,output,expected=1):
            report=tmp/(label+'.json');run([exe,'--build',source,'--output',output,'--report',report],env,expected)
            m=json.loads(report.read_text());assert m['status']=='REJECTED';cases.append(dict(name=label,status='PASS'))
        out=tmp/'new.syx';reject('incorrect-stock',bad,out);assert not out.exists()
        existing=tmp/'existing.syx';existing.write_bytes(b'keep-existing')
        reject('existing-output',bad,existing);assert existing.read_bytes()==b'keep-existing'
        reject('same-input-output',bad,bad);assert bad.read_bytes()==b'not Elektron stock'
        reject('missing-input',tmp/'missing.syx',out)
        reject('wrong-output-extension',bad,tmp/'wrong.txt')
        reject('missing-output-parent',bad,tmp/'missing/new.syx')
        # Actual macOS atomic output behavior, using non-firmware test data.
        fixture=tmp/'fixture.data';fixture.write_bytes(b'file safety fixture')
        original_sha=core.OUTPUT_SHA256
        try:
            core.OUTPUT_SHA256=core.sha(fixture.read_bytes());core.commit_output(fixture,out)
            assert out.read_bytes()==fixture.read_bytes()
            try:core.commit_output(fixture,existing);raise AssertionError('overwrite allowed')
            except core.BuildError:pass
            assert existing.read_bytes()==b'keep-existing'
            symlink=tmp/'dangling.syx';symlink.symlink_to(tmp/'absent-target')
            try:core.commit_output(fixture,symlink);raise AssertionError('symlink replaced')
            except core.BuildError:pass
            assert symlink.is_symlink()
        finally:core.OUTPUT_SHA256=original_sha
        original=tool.read_bytes()
        try:
            tool.write_bytes(original+b'tampered')
            try:core.checked_resources();raise AssertionError('native tamper allowed')
            except core.BuildError:pass
        finally:tool.write_bytes(original)
        exact=dict(status='NOT_TESTED',reason='No official stock OS1.17 URL supplied; proprietary firmware is never uploaded as an artifact.')
        if stock:
            stock=Path(stock);assert core.sha256_file(stock)==core.STOCK_SHA256
            output=tmp/'exact accepted output.syx';report=tmp/'exact.json'
            run([exe,'--build',stock,'--output',output,'--report',report],env)
            result=json.loads(report.read_text());assert result['status']=='PASS' and core.sha256_file(output)==core.OUTPUT_SHA256
            exact=dict(status='PASS',output_sha256=core.sha256_file(output),bytes=output.stat().st_size)
            env_test=os.environ.copy();env_test['DT2_BUILDER_SOURCE']=str(stage);env_test['DT2_TEST_STOCK']=str(stock)
            tests=Path(__file__).resolve().parents[1]/'source/test_builder.py'
            run([sys.executable,tests],env_test)
    result=dict(status='PASS',platform=platform.platform(),architecture=arch,python=platform.python_version(),
                packaged_GUI=gui,native_tool_sha256=core.sha256_file(tool),system_only_PATH=True,
                codesign_deep_verify=True,external_non_system_dependencies=False,dependencies=deps,
                rejection_cases=cases,synthetic_atomic_output_and_symlink_protection=True,
                native_parser_rejects_invalid_data=True,resource_tamper_rejected=True,exact_stock_build=exact,
                signature='ad-hoc; no Developer ID or Apple notarization',firmware_expected_sha256=core.OUTPUT_SHA256)
    (reports/'validation.json').write_text(json.dumps(result,indent=2)+'\n')
    return result

if __name__=='__main__':
    a=argparse.ArgumentParser();a.add_argument('--bundle',type=Path,required=True);a.add_argument('--stage',type=Path,required=True);a.add_argument('--reports',type=Path,required=True);a.add_argument('--stock',type=Path);args=a.parse_args()
    verify(args.bundle,args.stage,args.reports,args.stock)
