"""Native System-menu constructor/drawing experiment; current image read-only."""
import os,json,struct
from pathlib import Path
R=Path(__file__).resolve().parents[2];W=R/'out/on-device-credits-research'
os.environ['AIO25_CANDIDATE']=str(R/'out/all-in-one-polish-fang-r4-work/complete-polished.syx')
os.environ['AIO25_REPORT']=str(R/'out/all-in-one-polish-fang-r4-work/polished-build.json')
from tools.patchkit import pk_cfcall
from patchkit import test_all_in_one25 as u
pk_cfcall._built=True
p,view,sound,_=u.fixture.__wrapped__()
m=pk_cfcall.CfMachine('Digitakt_II_OS1.17.syx',timeout=60)
obj=0x47f20000
p[obj-16]=b'\xa5'*16+bytes(512)+b'\x5a'*16
rows=m.run(p,[{'pc':0x4010a204,'args':[obj],'max':2000000,'reads':[(obj-16,544)]}])
for r in rows:
 print('CTOR',r.ok,hex(r.pc),r.error,r.clobbered,r.insns,flush=True)
 if r.ok:
  data=r.reads[0][16:-16];print('FIELDS',[(hex(i),hex(struct.unpack_from('>I',data,i)[0])) for i in range(0,len(data),4) if any(data[i:i+4])],flush=True)
main=(W/'main.bin').read_bytes()
for address in (0x4021b744,0x401edc98):
 print('VTABLE',hex(address),[(i,hex(a)) for i,a in enumerate(struct.unpack_from('>42I',main,address-0x40000400))],flush=True)
for needle in (b'St15_Sp_counted_ptrIP4View',b'16VerticalMenuView',b'19VerticalMenuView',b'VerticalMenuView'):
 start=0
 while True:
  i=main.find(needle,start)
  if i<0:break
  print('TYPE',hex(i+0x40000400),main[i:i+120].split(b'\0')[0],flush=True);start=i+len(needle)
