"""Modified firmware derived from digikit; Credits-only private composition.

digikit by Em D - https://github.com/ElekLabs/digikit
AGPL-3.0-or-later plus original NOTICE. Not endorsed by Em D or Elektron.
"""
import hashlib,json,struct
from pathlib import Path
from tools.patchkit import pk_link
from tools.patchkit import pk_integrate_dv_zoom as dv
HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
WORK=ROOT/'out/on-device-credits-work'
BASE=ROOT/'out/all-in-one-polish-fang-r4-work/complete-polished.syx'
BASE_SHA='32de4571df62b4baa6012afc33396a5d3166519cda124383866ea6acb66c92a9'
ADDRESS=0x47ed0000
HOOK=0x4010a67e
EXTERNS=dict(alloc=0x4011ebd0,str_ctor=0x401e7e7a,str_dtor=0x401e63ca,
    menu_ctor=0x40119f6c,item_ctor=0x40115b90,menu_append=0x4011527a,
    pair_drop=0x40197d26,menu_show=0x4011a592,fill=0x401131ae,
    text=0x40113dc8,dirty=0x4011b126,memset=0x401363d0)
def sha(data):return hashlib.sha256(data).hexdigest()
def compile_ui():
    assert sha(BASE.read_bytes())==BASE_SHA
    WORK.mkdir(parents=True,exist_ok=True)
    obj,linked,isa=pk_link.build([HERE/'credits.c',HERE/'hook.s'],ADDRESS,EXTERNS)
    code=linked.image+bytes(-len(linked.image)%4)
    (WORK/'credits.bin').write_bytes(code)
    report=dict(symbols=linked.symbols,bytes=len(code),bss=linked.bss_size,isa=isa,
        object=obj,base_sha256=BASE_SHA,source_sha256={p.name:sha(p.read_bytes()) for p in HERE.iterdir() if p.is_file()})
    (WORK/'linked.json').write_text(json.dumps(report,indent=2)+'\n')
    return code,report
def compose():
    code,report=compile_ui()
    previous=json.loads((ROOT/'out/all-in-one-polish-fang-r4-work/polished-build.json').read_text())
    report={**previous,'credits':report}
    module=report['credits']
    pack,_,_=dv.load_v11(ROOT,musical=True)
    _,_,sections=pack.decode_all(BASE)
    main=bytearray(sections[3]['plain'])
    assert dv.at(main,HOOK,6).hex()=='4cef7c7c0008'
    assert dv.at(main,dv.INTRO_SCENES,80)==dv.STOCK_INTRO_SCENES
    segments,end=dv.parsed_segments(main)
    for _,n,d,b in segments:assert not (d+n+b>ADDRESS and d<ADDRESS+len(code)+module['bss'])
    # Includes fixed patchkit regions which can precede appended loader records.
    plan=json.loads((ROOT/'out/all-in-one-polish-fang-r4-work/fang-seed.plan.json').read_text())
    for w in plan['writes']:
        if w['cpu']=='cf' and w['kind']=='place':
            assert not (w['addr']<ADDRESS+len(code)+module['bss'] and ADDRESS<w['addr']+w['len'])
    dv.put(main,HOOK,b'\x4e\xf9'+struct.pack('>I',module['symbols']['credits_ctor_tail']))
    main[end:]=struct.pack('>III',len(code),ADDRESS,module['bss'])+code+struct.pack('>I',0xffffffff)
    output=WORK/'DT2_OS117_ALL_IN_ONE_ON_DEVICE_CREDITS_CANDIDATE.syx'
    assert not output.exists(),'Preserve previous image; never silently overwrite a candidate'
    report['candidate']=dv.package(BASE,output,bytes(main),pack)
    _,_,final=pack.decode_all(output)
    assert final[3]['plain']==bytes(main)
    for sid in sections:
        if sid!=3:assert final[sid]['stored']==sections[sid]['stored'],sid
    before=sections[3]['plain']
    fixed=bytearray(main[:end]);fixed[HOOK-dv.MAIN_BASE:HOOK-dv.MAIN_BASE+6]=dv.at(before,HOOK,6)
    assert fixed==before[:end]
    assert dv.at(main,dv.INTRO_SCENES,80)==dv.STOCK_INTRO_SCENES
    report['all_non_main_sections_stored_identical']=True
    report['existing_main_except_6_byte_tail_hook_identical']=True
    report['additional_intro_frames']=0
    (WORK/'build.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report['candidate'],indent=2))
if __name__=='__main__':
    import sys
    if '--compile-only' in sys.argv:compile_ui()
    else:compose()
