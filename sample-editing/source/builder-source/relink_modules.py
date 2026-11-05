"""Reproduce all six approved custom payloads from their captured link objects.

No stock firmware or compiler required. Uses the original patchkit linker.
This is object-to-payload reproduction, not a claim of clean source compilation.
"""
from pathlib import Path
import hashlib
import json
from tools.patchkit import pk_link

def main():
    root=Path(__file__).resolve().parent
    manifest=json.loads((root/'assets/manifest.json').read_text())
    for module in manifest['modules']:
        name=module['name'];objects=root/'firmware-source/objects'
        obj=json.loads((objects/(name+'.dko.json')).read_text())
        meta=json.loads((objects/(name+'.link.json')).read_text())
        linked=pk_link.link(obj,module['base'],meta['symbols'])
        raw=bytearray(linked.image+bytes(-len(linked.image)%4))
        if name in ('lab','smart'):
            offset=linked.symbols['sl_detect']-linked.base
            target=0x47fb0000 if name=='lab' else 0x47fb1476
            raw[offset:offset+6]=b'\x4e\xf9'+target.to_bytes(4,'big')
        assert raw==(root/'assets'/module['file']).read_bytes(),name
        assert hashlib.sha256(raw).hexdigest()==module['sha256'],name
        assert (linked.bss_size+3)//4*4==module['bss'],name
        print('PASS',name,len(raw),'bytes; exact approved payload')

if __name__=='__main__':main()
