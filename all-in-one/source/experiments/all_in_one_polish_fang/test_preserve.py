"""Final loaded bytes, old object identities, narrowly enumerated relocations."""
import hashlib
import json
import struct
import tomllib
from pathlib import Path
from tools.patchkit import pk_encoding_gate,port_resolve
from tools.patchkit import pk_integrate_dv_zoom as dv
from tools.sharc_dev import sharc_bootcheck
from tools.sharc_dev.sharcldr import LoadedMemory
HERE=Path(__file__).resolve().parent;DV=HERE.parents[1]
W=DV/'out/all-in-one-polish-fang-r4-work';R4=DV/'out/twinshot-fus2-repair4-work'
HOST=DV.parent/'plaits-va-run02/out/spacejam-hopt2-playable';SRC=HOST/'out/all-in-one-plaits25-e48/source'

def main():
    image=W/'complete-polished.syx';base=DV/'out/twinshot-fus2-repair4/DT2_OS117_ALL_IN_ONE_FUS2_REPAIR4_CANDIDATE.syx'
    sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
    assert sha(base)=='66f022bf5640afb49c2dc5b02ec9b0cb9a8b3def30c84536e92154a29a9078fd'
    old,new,stock=[port_resolve.sections_of(p) for p in (base,image,SRC/'firmware/Digitakt_II_OS1.17.syx')]
    oldplan=json.loads((R4/'seed-05.plan.json').read_text());plan=json.loads((W/'fang-seed.plan.json').read_text())
    build=json.loads((W/'polished-build.json').read_text())
    for sid in (2,4,5,8):assert old[sid]==new[sid]==stock[sid],sid
    encoding=dict(status='PASS',image_sha256=sha(image),plan_sha256=sha(W/'fang-seed.plan.json'),
        gate_sha256=sha(Path(pk_encoding_gate.__file__)),proven_sha256=sha(pk_encoding_gate.PROVEN),violations=0)
    if (W/'final-encoding.json').exists():assert json.loads((W/'final-encoding.json').read_text())==encoding
    else:
        issues=pk_encoding_gate.check_image(image,W/'fang-seed.plan.json')
        assert not issues,issues[:8]
        (W/'final-encoding.json').write_text(json.dumps(encoding,indent=2)+'\n')
    boot=sharc_bootcheck.check(new[7]);assert boot.ok
    assert not [r for r in sharc_bootcheck.check_against_stock(stock[7],new[7]) if r.level=='error']
    assert all(u<=c for u,c in plan['regions'].values())
    assert all(v['result']=='PASS' for v in build['candidate']['bootstrap'].values())
    assert build['candidate']['sha256']==sha(image)
    before,after=[LoadedMemory.from_stream(x[7]) for x in (old,new)]
    preserved=[]
    for name in ('core/dsp-inline','machines/plaits-48k','machines/twinshot','machines/twinshot-fusion'):
        module=HERE.parent/'twinshot_fusion_v2/build/apply-root/modules'/name/'module.toml'
        objentry=next(o for o in tomllib.loads(module.read_text())['object'] if o['place']=='dsp.l2')
        objfile=module.parent/objentry['path'];obj=json.loads(objfile.read_text())
        owner=name+'@'
        placements=[next(x for x in p['writes'] if x['cpu']=='sharc' and x['what']=='object in dsp.l2' and x['owner'].startswith(owner)) for p in (oldplan,plan)]
        a,b=placements;assert a['len']==b['len']
        sections={}
        for sec in obj['sections']:
            if sec['kind']=='text':sections[sec['name']]=0;continue
            candidates=[s for s in obj['symbols'] if s['section']==sec['name'] and a['owner']+':'+s['name'] in oldplan['symbols']]
            assert candidates,(name,sec['name'],'no linked section-base evidence')
            s=candidates[0];key=a['owner']+':'+s['name']
            start=oldplan['symbols'][key]-s['value'];offset=start-a['addr']
            assert plan['symbols'][key]-s['value']-b['addr']==offset,(name,sec['name'],'layout changed')
            sections[sec['name']]=offset
        allowed=set()
        for rel in obj['relocs']:
            sec=next(s for s in obj['sections'] if s['name']==rel['section'])
            off=sections[sec['name']]+rel['offset']*(2 if sec['kind']=='text' else 1)
            allowed.update(range(off,off+(6 if sec['kind']=='text' else 4)))
        def span(mem,addr,size):
            pieces=[]
            for k in range(0,size,256):
                n=min(256,size-k);part=mem.read(addr+k,n)
                if part is None:
                    parts=[mem.read(addr+k+i,1) for i in range(n)]
                    assert all(p is not None for p in parts),('unmapped non-BSS',hex(addr+k))
                    part=b''.join(parts)
                pieces.append(part)
            return b''.join(pieces)
        changed=set();compared=0
        for sec in obj['sections']:
            # BSS has allocation/zeroing semantics but no loaded image bytes.
            # Never manufacture bytes to compare across that boot-stream hole.
            if sec['kind']=='bss':continue
            off=sections[sec['name']];size=sec['size']
            x=span(before,a['addr']+off,size);y=span(after,b['addr']+off,size)
            changed.update(off+i for i,(p,q) in enumerate(zip(x,y)) if p!=q);compared+=size
        assert changed<=allowed,(name,sorted(changed-allowed)[:20])
        preserved.append(dict(module=name,object_sha256=sha(objfile),bytes=a['len'],loaded_bytes_compared=compared,
            relocated_from=a['addr'],relocated_to=b['addr'],changed_only_recorded_relocation_instructions=len(changed)))
    def read(main,addr,n):
        # Stock descriptor strings remain directly resident in MAIN flash;
        # only relocated/private pointers belong to reset-loader payloads.
        source=addr if dv.MAIN_BASE<=addr<dv.STOCK_MAIN_END else dv.source_for_loaded_address(main,addr)
        return dv.at(main,source,n)
    oldtable=next(v for k,v in oldplan['symbols'].items() if k.endswith(':pf_table'))
    table=build['pf_table']
    for row in range(314):
        a=struct.unpack('>15I',read(old[3],oldtable+60*row,60));b=struct.unpack('>15I',read(new[3],table+60*row,60))
        assert a[:10]==b[:10],('existing parameter metadata',row)
        for field in (10,12):
            if a[field] and b[field]:assert read(old[3],a[field],80).split(b'\0')[0]==read(new[3],b[field],80).split(b'\0')[0],row
            else:assert a[field]==b[field],row
    for addr,size in ((0x47ec0000,dv.MUSICAL_PAYLOAD_BYTES),(0x47ea0000,build['ui']['bytes'])):
        assert read(old[3],addr,size)==read(new[3],addr,size),hex(addr)
    assert dv.at(new[3],dv.INTRO_SCENES,80)==dv.STOCK_INTRO_SCENES
    r=dict(status='PASS',image_sha256=sha(image),preserved_DSP_objects=preserved,
        stock_sections=[2,4,5,8],existing314_parameter_metadata_and_names='exact',
        DV_WAVE_Zoom_and_Plaits_UI='exact',encoding_gate='PASS',SHARC_bootcheck='PASS',regions=plan['regions'])
    (W/'preservation.json').write_text(json.dumps(r,indent=2)+'\n');print(json.dumps(r,indent=2),flush=True)

if __name__=='__main__':main()
