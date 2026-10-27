"""Bounded native menu inventory; avoid shadowing Python's inspect module."""
import re,struct,json,hashlib
from pathlib import Path
from tools.patchkit import port_resolve
R=Path(__file__).resolve().parents[2]
candidate=R/'out/FLASH_THIS_DT2_SY_FANG_POLISH/DT2_OS117_ALL_IN_ONE_SY_FANG_POLISH_CANDIDATE.syx'
assert hashlib.sha256(candidate.read_bytes()).hexdigest()=='32de4571df62b4baa6012afc33396a5d3166519cda124383866ea6acb66c92a9'
main=port_resolve.sections_of(candidate)[3]
rows=[]
for match in re.finditer(rb'[ -~]{4,}\x00',main[:0x300000]):
    s=match[0][:-1].decode()
    if len(s)<100 and 'EUl' not in s and re.search(r'(SettingsMenu|System.*Menu|About|CREDITS|^SETTINGS$|^SYSTEM$|^System$|VERSION|^OS Upgrade$|^OS UPGRADE$|Personalize|^GLOBAL$)',s):
        addr=0x40000400+match.start()
        refs=[hex(0x40000400+m.start()) for m in re.finditer(re.escape(struct.pack('>I',addr)),main[:0x310000])]
        rows.append(dict(address=hex(addr),text=s,refs=refs[:30]))
print(json.dumps(rows,indent=2))
out=R/'out/on-device-credits-research';out.mkdir(exist_ok=True)
(out/'main.bin').write_bytes(main)
(out/'menu-refs.json').write_text(json.dumps(rows,indent=2)+'\n')
from tools.patchkit import pk_toolchains
import subprocess
objdump=pk_toolchains.m68k_bin('objdump');assert objdump
for a,b in ((0x4010a1c6,0x4010a300),(0x4010a68a,0x4010a830),(0x40115b90,0x40115c90),(0x4011527a,0x4011531a)):
 text=subprocess.check_output([str(objdump),'-D','-b','binary','-m','m68k:isa-c:emac','--adjust-vma=0x40000400',
     '--start-address='+hex(a),'--stop-address='+hex(b),str(out/'main.bin')],text=True)
 (out/(hex(a)+'.txt')).write_text(text);print(text)
print('MENU LABELS',[(hex(0x40000400+m.start()+0x246000),m[0][:-1].decode()) for m in re.finditer(rb'[ -~]{4,}\x00',main[0x246000:0x246800])])
