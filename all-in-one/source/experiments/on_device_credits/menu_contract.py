"""Read the narrow native submenu and base-view contract."""
import struct,re,subprocess
from pathlib import Path
from tools.patchkit import pk_toolchains
R=Path(__file__).resolve().parents[2];W=R/'out/on-device-credits-research';raw=(W/'main.bin').read_bytes()
objdump=pk_toolchains.m68k_bin('objdump')
refs=[m.start()+0x40000400 for m in re.finditer(re.escape(struct.pack('>I',0x4010a204)),raw)]
print('SYSTEM CTOR CALLS',list(map(hex,refs)))
for a,b in ((0x40113b40,0x40113d60),):
 text=subprocess.check_output([str(objdump),'-D','-b','binary','-m','m68k:isa-c:emac','--adjust-vma=0x40000400',
     '--start-address='+hex(a),'--stop-address='+hex(b),str(W/'main.bin')],text=True)
 (W/(hex(a)+'.txt')).write_text(text);print(text)
for needle in (0x4023acb1,0x40257ff1):
 refs=[m.start()+0x40000400 for m in re.finditer(re.escape(struct.pack('>I',needle)),raw)]
 for ptr in refs:
  print('RTTI',hex(ptr),raw[ptr-0x40000400-16:ptr-0x40000400+28].hex())
  rtti=ptr-4
  refs2=[m.start()+0x40000400 for m in re.finditer(re.escape(struct.pack('>I',rtti)),raw)]
  print('VT_REFS',[(hex(p),raw[p-0x40000400-4:p-0x40000400+48].hex()) for p in refs2])
