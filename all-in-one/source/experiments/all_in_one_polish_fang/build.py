"""Isolated visual/FANG work. Immutable Repair4 inputs, never a release bypass."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import re
import tomllib

from tools.patchkit import pk_link,pk_build,patchkit
from tools.patchkit import pk_integrate_dv_zoom as dv

HERE=Path(__file__).resolve().parent
DV=HERE.parents[1]
HOST=DV.parent/'plaits-va-run02/out/spacejam-hopt2-playable'
SRC=HOST/'out/all-in-one-plaits25-e48/source'
REF=DV/'out/twinshot-fus2-repair4'
WORK=DV/'out/all-in-one-polish-fang-r4-work'
R4=DV/'out/twinshot-fus2-repair4-work'
SHA='66f022bf5640afb49c2dc5b02ec9b0cb9a8b3def30c84536e92154a29a9078fd'

def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def sym(p,n):return next(v for k,v in p['symbols'].items() if k.endswith(':'+n))

def fang():
    """Private catalog: keep all existing sources/objects and registry15 intact."""
    stage=WORK/'catalog';stage.mkdir(exist_ok=True)
    oldstage=HERE.parent/'twinshot_fusion_v2/build/apply-root'
    root=stage/'devices/ports';root.mkdir(parents=True,exist_ok=True)
    (root/'dt2-1.17.toml').write_bytes((oldstage/'devices/ports/dt2-1.17.toml').read_bytes())
    modules=[];provenance={}
    for original in sorted((oldstage/'modules').rglob('module.toml')):
        name=original.parent.relative_to(oldstage/'modules').as_posix()
        dest=stage/'modules'/name/'module.toml';dest.parent.mkdir(parents=True,exist_ok=True)
        text=original.read_text();provenance[name]=digest(original)
        if name=='core/machines':
            assert text.count('to = "fusion2_typearg"')==1
            text=text.replace('to = "fusion2_typearg"','to = "fang_typearg"')
            registry=next(r for r in tomllib.loads(text)['registry'] if r.get('index_table'))
            typepath=original.parent/registry['index_table']
            types=typepath.read_text()
            assert 'machines/chimera' not in types and 'machines/sy-fang' not in types
            types=types.replace('[dn2]','"machines/chimera:chimera" = 15\n"machines/sy-fang:fang" = 16\n\n[dn2]',1)
            (dest.parent/'types.toml').write_text(types)
            text=text.replace(json.dumps(registry['index_table']),json.dumps(str(dest.parent/'types.toml')))
        if name=='machines/twinshot-fusion':
            for old,new in (('_tf_level_decode','_fang_level_decode'),('fusion2_split_predicate','fang_split')):
                assert text.count('to = "'+old+'"')==1
                text=text.replace('to = "'+old+'"','to = "'+new+'"')
            assert text.count('value = "fusion2_param"')==1
            text=text.replace('value = "fusion2_param"','value = "fang_param"')
        dest.write_text(text);modules.append(name)
    exe=SRC/'out/native/target/nogen/release/patchkit';assert exe.exists()
    patchkit.binary=lambda build=True:exe
    build=WORK/'objects';build.mkdir(exist_ok=True)
    objects={'fang-dsp.dko.json':pk_build.sharc_object([HERE/'dsp/entry.s',HERE/'dsp/fang.c']),
             'fang-cf.dko.json':pk_build.cf_object([HERE/'controls.s'])}
    for name,obj in objects.items():(build/name).write_text(pk_build.canonical(obj))
    manifest=(HERE/'module.toml').read_text()
    for path in ('dsp/entry.s','dsp/fang.c','controls.s'):
        manifest=manifest.replace('"'+path+'"',json.dumps(str(HERE/path)))
    for name in objects:
        manifest=manifest.replace('"build/'+name+'"',json.dumps(str(build/name)))
    dest=stage/'modules/machines/sy-fang/module.toml';dest.parent.mkdir(parents=True,exist_ok=True);dest.write_text(manifest)
    # Real object rebuild comparison. Never permit stale-object bypass.
    freshness=pk_build.build_module(dest.parent,check=True)
    modules.append('machines/sy-fang')
    command=[str(exe),'--root',str(stage),'apply','--firmware',str(SRC/'firmware/Digitakt_II_OS1.17.syx'),
             '--modules',','.join(modules),'-o',str(WORK/'fang-seed.syx'),'--plan',str(WORK/'fang-seed.plan.json')]
    r=subprocess.run(command,text=True,capture_output=True)
    (WORK/'fang-build.stdout.txt').write_text(r.stdout);(WORK/'fang-build.stderr.txt').write_text(r.stderr)
    assert r.returncode==0,(r.returncode,r.stdout[-5000:],r.stderr[-5000:])
    plan=json.loads((WORK/'fang-seed.plan.json').read_text())
    assert sym(plan,'machines.fang.index')==16
    assert sym(plan,'params.fang_wave.index')==314 and sym(plan,'params.fang_bite.index')==320
    (WORK/'fang-source.json').write_text(json.dumps(dict(status='BUILT',base_catalog_sha256=provenance,
        source_sha256={p.name:digest(p) for p in HERE.iterdir() if p.is_file()},
        objects={n:digest(build/n) for n in objects},freshness=freshness,regions=plan['regions']),indent=2)+'\n')
    print('FANG seed built; complete composition and runtime gates remain',flush=True)

def compose():
    base=REF/'DT2_OS117_ALL_IN_ONE_FUS2_REPAIR4_CANDIDATE.syx';assert digest(base)==SHA
    plan=json.loads((WORK/'fang-seed.plan.json').read_text())
    previous=json.loads((R4/'build-05.json').read_text())
    pack,manifest,_=dv.load_v11(DV,musical=True)
    _,_,old=pack.decode_all(base);_,_,seed=pack.decode_all(WORK/'fang-seed.syx')
    for sid in (2,4,5,8):assert seed[sid]['stored']==old[sid]['stored']
    main=seed[3]['plain'];loader=dv.at(main,dv.PK_LOADER_SOURCE,dv.PK_LOADER_BYTES)
    main,writes=dv.apply_v51(main,pack,manifest,loader)
    writes.append(dv.relocate_pk_loader(main,manifest,loader))
    symbols=previous['dv']['symbols'];dv.V11_EXP=0x47ec0000
    payload=dv.at(old[3]['plain'],dv.source_for_loaded_address(old[3]['plain'],0x47ec0000),dv.MUSICAL_PAYLOAD_BYTES)
    assert dv.sha(payload)==previous['dv']['sha256']
    writes+=dv.apply_musical_v4(main,symbols)
    main,segment=dv.append_v11_segment(main,payload);writes.append(segment)
    dv.PF_TABLE=sym(plan,'pf_table')
    writes+=dv.apply_active_musical_wave_descriptors(main,symbols)
    writes+=dv.apply_v11_hooks(main,symbols)
    oldui=dv.at(old[3]['plain'],dv.source_for_loaded_address(old[3]['plain'],0x47ea0000),previous['ui']['bytes'])
    _,end=dv.parsed_segments(main)
    main=bytearray(main[:end])+struct.pack('>III',len(oldui),0x47ea0000,0)+oldui+struct.pack('>I',0xffffffff)
    for typ in (14,16):
        addr=sym(plan,'mf_names')+typ*12+8
        dv.put(main,dv.source_for_loaded_address(main,addr),bytes(4))
    externs={'panel_previous':sym(plan,'fusion_widget'),'panel_fill':0x401131ae,'panel_text':0x40113dc8,'panel_effective':0x4006978e,
        'fusion2_recovery_fmt':sym(plan,'fusion2_recovery_fmt'),'fusion2_auto_fmt':sym(plan,'fusion2_auto_fmt')}
    obj,linked,isa=pk_link.build([HERE/'panel.c'],0x47eb0000,externs)
    code=linked.image+bytes(-len(linked.image)%4)
    segments,end=dv.parsed_segments(main)
    for _,n,d,b in segments:assert not (d+max(n,b)>0x47eb0000 and d<0x47eb0000+len(code))
    for w in plan['writes']:
        if w['cpu']=='cf' and w['kind']=='place':
            for start,data in ((0x47ea0000,oldui),(0x47eb0000,code),(0x47ec0000,payload)):
                assert not (start<w['addr']+w['len'] and w['addr']<start+len(data))
    dv.put(main,0x401edd4c,struct.pack('>I',linked.symbols['panel_draw']))
    for row,fn in ((308,'panel_recovery_short'),(309,'panel_auto_short')):
        address=dv.PF_TABLE+row*60+52
        dv.put(main,dv.source_for_loaded_address(main,address),struct.pack('>I',linked.symbols[fn]))
    main[end:]=struct.pack('>III',len(code),0x47eb0000,0)+code+struct.pack('>I',0xffffffff)
    assert dv.at(main,dv.INTRO_SCENES,80)==dv.STOCK_INTRO_SCENES
    output=WORK/'complete-polished.syx';assert not output.exists()
    report=dict(previous);report['candidate']=dv.package(WORK/'fang-seed.syx',output,bytes(main),pack)
    _,_,final=pack.decode_all(output)
    for sid in (2,4,5,7,8):assert final[sid]['stored']==seed[sid]['stored'],sid
    report.update(pf_table=dv.PF_TABLE,regions=plan['regions'],writes=writes,
        seed=dict(path=str(WORK/'fang-seed.syx'),sha256=digest(WORK/'fang-seed.syx'),modules=plan['modules']),
        panel=dict(symbols=linked.symbols,bytes=len(code),toolchain=obj['toolchain'],isa=isa,
                   source_sha256=digest(HERE/'panel.c'),base_sha256=SHA),hardware='UNTESTED')
    (WORK/'polished-build.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({'status':'BUILT_NOT_RELEASE_GATED','candidate':report['candidate'],'regions':plan['regions']},indent=2),flush=True)

def polish():
    """Finish only the reviewed CF graphics; retain the complete DSP byte-for-byte."""
    original=WORK/'complete-internal.syx';output=WORK/'complete-polished.syx'
    assert not output.exists()
    report=json.loads((WORK/'complete-build.json').read_text());assert digest(original)==report['candidate']['sha256']
    plan=json.loads((WORK/'fang-seed.plan.json').read_text())
    pack,_,_=dv.load_v11(DV,musical=True);_,_,before=pack.decode_all(original)
    main=bytearray(before[3]['plain']);segments,end=dv.parsed_segments(main)
    assert segments[-1][2]==0x47eb0000 and sum(s[2]==0x47eb0000 for s in segments)==1
    header=segments[-1][0]
    externs={'panel_previous':sym(plan,'fusion_widget'),'panel_fill':0x401131ae,'panel_text':0x40113dc8,'panel_effective':0x4006978e,
        'fusion2_recovery_fmt':sym(plan,'fusion2_recovery_fmt'),'fusion2_auto_fmt':sym(plan,'fusion2_auto_fmt')}
    obj,linked,isa=pk_link.build([HERE/'panel.c'],0x47eb0000,externs)
    assert not linked.bss_size and all(s['kind'] not in ('data','bss') for s in obj['sections'])
    code=linked.image+bytes(-len(linked.image)%4);assert len(code)<0x10000
    vt=0x401edd4c;assert dv.at(main,vt,4)==struct.pack('>I',report['panel']['symbols']['panel_draw'])
    dv.put(main,vt,struct.pack('>I',linked.symbols['panel_draw']))
    changes=[(vt,4)]
    for row,fn,old in ((308,'panel_recovery_short','fusion2_recovery_fmt'),(309,'panel_auto_short','fusion2_auto_fmt')):
        address=report['pf_table']+row*60+52;source=dv.source_for_loaded_address(main,address)
        assert dv.at(main,source,4)==struct.pack('>I',sym(plan,old))
        dv.put(main,source,struct.pack('>I',linked.symbols[fn]));changes.append((source,4))
    allowed={a-dv.MAIN_BASE+i for a,n in changes for i in range(n)}
    assert all(main[i]==before[3]['plain'][i] or i in allowed for i in range(header-dv.MAIN_BASE))
    main[header-dv.MAIN_BASE:]=struct.pack('>III',len(code),0x47eb0000,0)+code+struct.pack('>I',0xffffffff)
    report['candidate']=dv.package(original,output,bytes(main),pack)
    _,_,after=pack.decode_all(output)
    for sid in (2,4,5,7,8):assert before[sid]['stored']==after[sid]['stored'],sid
    report['panel']=dict(symbols=linked.symbols,bytes=len(code),toolchain=obj['toolchain'],isa=isa,
        source_sha256=digest(HERE/'panel.c'),base_sha256=SHA,graphics_only_from_sha256=digest(original),
        changed_existing_words=changes,time_formatters='FUS2 aliases308/309 only, <=4 chars, rounded0.1s above99ms')
    (WORK/'polished-build.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(dict(status='CF_POLISH_BUILT_RUNTIME_GATES_REQUIRED',candidate=report['candidate'],panel=report['panel']),indent=2),flush=True)

def main():
    ap=argparse.ArgumentParser(__doc__);ap.add_argument('phase',choices=['tools','ui','fang','compose','polish']);a=ap.parse_args()
    WORK.mkdir(exist_ok=True)
    if a.phase=='fang':return fang()
    if a.phase=='compose':return compose()
    if a.phase=='polish':return polish()
    if a.phase=='tools':
        from tools.patchkit import pk_toolchains
        for name in ('gcc','as','ld'):
            p=pk_toolchains.m68k_bin(name);print(name,p,flush=True)
            if p:print(subprocess.check_output([str(p),'--version'],text=True).splitlines()[0],flush=True)
        return
    base=REF/'DT2_OS117_ALL_IN_ONE_FUS2_REPAIR4_CANDIDATE.syx'
    assert digest(base)==SHA
    plan=json.loads((R4/'seed-05.plan.json').read_text())
    pack,_,_=dv.load_v11(DV,musical=True)
    _,_,sections=pack.decode_all(base)
    before=sections[3]['plain'];main=bytearray(before)
    externs={'panel_previous':sym(plan,'fusion_widget'),'panel_fill':0x401131ae,'panel_text':0x40113dc8,'panel_effective':0x4006978e,
        'fusion2_recovery_fmt':sym(plan,'fusion2_recovery_fmt'),'fusion2_auto_fmt':sym(plan,'fusion2_auto_fmt')}
    obj,linked,isa=pk_link.build([HERE/'panel.c'],0x47eb0000,externs)
    assert not linked.bss_size
    assert all(s['kind'] not in ('data','bss') for s in obj['sections'])
    code=linked.image+bytes(-len(linked.image)%4)
    assert len(code)<0x10000
    segments,end=dv.parsed_segments(main)
    for _,n,d,b in segments:assert not (d+max(n,b)>0x47eb0000 and d<0x47eb0000+len(code))
    vt=0x401edd4c;assert dv.at(main,vt,4)==struct.pack('>I',externs['panel_previous'])
    dv.put(main,vt,struct.pack('>I',linked.symbols['panel_draw']))
    main[end:]=struct.pack('>III',len(code),0x47eb0000,0)+code+struct.pack('>I',0xffffffff)
    assert all(main[i]==before[i] or vt-0x40000400<=i<vt-0x40000400+4 for i in range(end))
    output=WORK/'ui-internal.syx'
    assert not output.exists(),'internal output exists; preserve it'
    report=json.loads((R4/'build-05.json').read_text())
    report['candidate']=dv.package(base,output,bytes(main),pack)
    report['panel']={'toolchain':obj['toolchain'],'isa':isa,'bytes':len(code),'symbols':linked.symbols,
        'source_sha256':digest(HERE/'panel.c'),'base_sha256':SHA}
    _,_,after=pack.decode_all(output)
    for sid in (2,4,5,7,8):assert sections[sid]['stored']==after[sid]['stored'],sid
    (WORK/'ui-build.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report['panel'],indent=2),flush=True)

if __name__=='__main__':main()
