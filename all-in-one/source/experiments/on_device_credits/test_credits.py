"""Bounded actual native constructor, shared ownership, events and framebuffer."""
import os,json,struct,hashlib
from pathlib import Path
from build import ROOT,WORK,BASE,ADDRESS,HOOK
os.environ.setdefault('AIO25_CANDIDATE',str(BASE))
os.environ.setdefault('AIO25_REPORT',str(ROOT/'out/all-in-one-polish-fang-r4-work/polished-build.json'))
os.environ['AIO25_UI_OUT']=str(WORK/'previews')
from patchkit import test_all_in_one25 as u
from tools.patchkit import pk_cfcall
pk_cfcall._built=True
syms=(json.loads((WORK/'build.json').read_text())['credits'] if os.environ.get('CREDITS_FINAL')=='1'
    else json.loads((WORK/'linked.json').read_text()))['symbols']
p,view,sound,_=u.fixture.__wrapped__()
unmodified_pokes=dict(p)
if os.environ.get('CREDITS_FINAL')!='1':
    link=json.loads((WORK/'linked.json').read_text())
    p[ADDRESS]=(WORK/'credits.bin').read_bytes()+bytes(link['bss'])
    p[HOOK]=b'\x4e\xf9'+struct.pack('>I',syms['credits_ctor_tail'])
m=pk_cfcall.CfMachine('Digitakt_II_OS1.17.syx',timeout=60)
history=[]
def run(edits,calls):
    # Each invocation restores cached state; replay the bounded prefix, not
    # a fictitious persistent process. Calls within one request share memory.
    p.update(edits);history.extend(calls)
    results=m.run(p,history)[-len(calls):]
    for r in results:
        print('CALL',r.ok,hex(r.pc),r.error,r.clobbered,r.insns,flush=True)
        if not r.ok:
            print('REGS',list(map(hex,r.d+r.a)),flush=True)
            print('READS',[x.hex() for x in r.reads],flush=True)
    return u.checked(results)
def words(b):return struct.unpack('>'+str(len(b)//4)+'I',b)
obj=0x47f20000;event=0x47f21000;temp=0x47f22000
p[obj-16]=b'\xa5'*16+bytes(512)+b'\x5a'*16
r=run(p,[{'pc':0x4010a204,'args':[obj],'max':2000000,
           'reads':[(obj-16,544),(view+44,4)]}])[-1]
assert r.reads[0][:16]==b'\xa5'*16 and r.reads[0][-16:]==b'\x5a'*16
fields=words(r.reads[0][16:-16]);begin,end=fields[1:3]
print('MENU',list(map(hex,fields[:20])),'controller',r.reads[1].hex(),flush=True)
assert end-begin==40,'All four native System items plus Credits'
controller=words(r.reads[1])[0];assert controller
r=run({},[{'pc':0x4013ab84,'args':[temp,begin,end-begin],'reads':[(temp,end-begin),(controller,48)]}])[-1]
items=words(r.reads[0]);print('ITEMS',list(map(hex,items)),'CONTROLLER',r.reads[1].hex(),flush=True)
initial_controller=r.reads[1]
# Parent attaches to the actual cached native UI controller, as native onShow does.
edits={event:struct.pack('>5I',0,0,0,10,16)}
# Move native menu selection four entries, then dispatch native YES.
vt=(ROOT/'out/on-device-credits-research/main.bin').read_bytes()
table=words(vt[0x21dcfc-0x400:0x21dcfc-0x400+148]);print('VTMENU',list(map(hex,table)),flush=True)
calls=[{'pc':0x4011c2a6,'args':[controller,obj+56,0],'max':2000000,'reads':[(controller,48)]},
       {'pc':table[6],'args':[obj,4],'max':200000}]
calls += [{'pc':0x4011a986,'args':[obj+56,event],'max':2000000,'reads':[(obj+144,8),(controller,48),(0x47fdfc00,1024)]}]
opening=run(edits,calls);system_controller=opening[0].reads[0];r=opening[-1]
print('CHILD',r.reads[0].hex(),'CTRL',r.reads[1].hex(),flush=True)
# weak_ptr slot contains raw View and the control block for our new page.
child,control=words(r.reads[0]);assert child and control
reads=[(child-56,508),(child,80),(control,16)]
r=run({},[{'pc':0x4013ab84,'args':[temp,child-56,508],'reads':reads}])[-1]
print('CHILD_FIELDS',[(i,hex(x)) for i,x in enumerate(words(r.reads[0])) if x],flush=True)
child_vtable=words(r.reads[0])[14]
r=run({},[{'pc':0x4013ab84,'args':[temp,child_vtable+16,4],'reads':[(temp,4)]}])[-1]
draw=words(r.reads[0])[0];assert draw==syms['credits_draw']
captures=[]
for page in range(3):
    if page:run({event+32:struct.pack('>5I',0,0,0,14,1)},[{'pc':0x4011a986,'args':[child,event+32],'max':500000}])
    r=run({},[u.clear(),{'pc':draw,'args':[child,u.BMP],'max':500000,
        'reads':[(u.PIXELS-16,1056),(child-56,56)]}])[-1]
    image=u.bits(r);u.save_bitmap(image,'credits-'+str(page+1));captures.append(image)
    print('PAGEFIELDS',r.reads[1].hex(),flush=True)
assert len(set(captures))==3
def footer(data):return b''.join(data[x*8:x*8+1] for x in range(128))
assert len(set(map(footer,captures)))==1,'Footer fully identical on every page'
assert any(footer(captures[0]))
def title_prefix(data):return b''.join(data[x*8+7:x*8+8] for x in range(60))
assert len(set(map(title_prefix,captures)))==1 and any(title_prefix(captures[0]))
for i in range(3):
    r=run({},[u.clear(),{'pc':draw,'args':[child,u.BMP],'max':500000,'reads':[(u.PIXELS-16,1056)]}])[-1]
    assert u.bits(r)==captures[2],'Complete page redraw is byte-identical'
r=run({event+64:struct.pack('>5I',0,0,0,12,16)},[{'pc':0x4011a986,'args':[child,event+64],'max':2000000,'reads':[(child+48,1),(controller,48)]}])[-1]
assert r.reads[0]==b'\x01','Native NO marks child closed'
r=run({},[{'pc':0x4011c36c,'args':[controller],'max':2000000,'reads':[(obj+144,8),(controller,48),(control,16)]}])[-1]
print('CLOSED',r.reads[0].hex(),r.reads[1].hex(),r.reads[2].hex(),flush=True)
assert words(r.reads[0])==(0,0),'Native child close callback releases parent weak reference'
assert words(r.reads[1])[5:8]==words(system_controller)[5:8],'Native controller returns to System'
# Repeated open, close, native destructor + weak-reference cleanup, then draw
# the real System view again. No mocked navigation or overwritten stock code.
repeat=[]
for _ in range(8):
    repeat += [
        {'pc':0x4011a986,'args':[obj+56,event],'max':2000000,'reads':[(obj+144,8)]},
        # The weak child pointer is dynamic; native System close cascades to it.
        # Instead, invoke NO via the native controller's event dispatcher below.
    ]
# Replay with deterministic allocated child pointers observed for each cycle.
for i in range(8):
    r=run({},[repeat[i]])[-1];again,cb=words(r.reads[0]);assert again and cb
    r=run({},[{'pc':0x4011a986,'args':[again,event+64],'max':500000},
        {'pc':0x4011c36c,'args':[controller],'max':2000000,
         'reads':[(obj+144,8),(controller,48)]}])[-1]
    assert words(r.reads[0])==(0,0)
    assert words(r.reads[1])[5:8]==words(system_controller)[5:8]
r=run({},[u.clear(),{'pc':0x4011a494,'args':[obj+56,u.BMP],'max':1000000,
    'reads':[(u.PIXELS-16,1056),(obj-16,544),(sound,0x450)]}])[-1]
u.save_bitmap(u.bits(r),'system-credits-entry')
assert r.reads[1][:16]==b'\xa5'*16 and r.reads[1][-16:]==b'\x5a'*16
# Compare native System item objects against the same current baseline with
# the hook absent. The four original callbacks/metadata must be byte-exact.
stock={**unmodified_pokes,HOOK:bytes.fromhex('4cef7c7c0008'),obj:bytes(512)}
stock_reads=u.checked(m.run(stock,[{'pc':0x4010a204,'args':[obj],'max':2000000,
    'reads':[(items[i],84) for i in (0,2,4,6)]+[(obj+4,8)]}]))[-1].reads
stock_items=stock_reads[:4]
old_begin,old_end=words(stock_reads[4]);assert old_end-old_begin==32
new_items=run({},[{'pc':0x4013ab84,'args':[temp,obj,4],
    'reads':[(items[i],84) for i in (0,2,4,6)]}])[-1].reads
assert stock_items==new_items,'All native System MenuItems remain byte-identical'
result=dict(status='PASS',candidate_sha256=hashlib.sha256(u.CANDIDATE.read_bytes()).hexdigest(),
    final_image=os.environ.get('CREDITS_FINAL')=='1',native_system_items_preserved=4,
    opened_via_native_yes=True,pages_executed=3,native_no_back_cleanup_cycles=9,
    controller_restored=True,stock_items_byte_identical=True,callee_saved_and_stack='PASS',
    bitmap_and_object_guards='PASS',hardware_tested=False,
    page3_repeat_draws_byte_identical=3,header_prefix_footer_stable=True,
    fixture='cached OS1.17 native state; actual image MAIN/segments; nested System/child controller stack')
(WORK/('credits-final-test.json' if result['final_image'] else 'credits-prototype-test.json')).write_text(json.dumps(result,indent=2)+'\n')
print('FINISHED',flush=True)
