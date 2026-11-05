"""One fixed, stock-input Sample Editing Pack; derived from the DT2 Windows builder.

File operations only. No MIDI, USB, network, flashing, compiler or AIO dependency.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile

from resource_seal import RESOURCE_HASHES

STOCK_SHA256 = '26c22f6652625ac2cfd47f7ee970d388ed8b6427dae3563c0a6a2f2d334350d5'
OUTPUT_SHA256 = '880d810f036bdc56a5aac0fb85d249366aa8678fbdd7435b1fd95a7ed4a1df06'
OUTPUT_NAME = 'DT2_OS117_SAMPLE_EDITING_RELEASE_CANDIDATE.syx'
MAIN = 'section_3_MAIN_OS.bin'
MAIN_BASE = 0x40000400
SECTION_FILES = {MAIN,'section_2_bootstrap.bin','section_4_updater.raw',
                 'section_5_meta.raw','section_7_blob.bin','section_8__.bin'}
MODULES = [('lab',0x47ee0000),('smart',0x47f80000),('background',0x47fb0000),
           ('overflow',0x47fb1000),('zoom',0x47fd0000),('credits',0x47ed0000),('cf-init',0x47fa0000)]

class BuildError(Exception):
    """A failure that can be shown directly to the user."""

def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1 << 20),b''):
            digest.update(chunk)
    return digest.hexdigest()

def resource_root() -> Path:
    return Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parent))

def checked_resources() -> tuple[Path,dict]:
    root = resource_root()
    for relative,expected in RESOURCE_HASHES.items():
        p = root/relative
        if not p.is_file() or sha256_file(p) != expected:
            raise BuildError('The builder resources failed verification. Extract the original package again.')
    try:
        manifest = json.loads((root/'assets/manifest.json').read_text(encoding='utf-8'))
    except (ValueError,OSError) as exc:
        raise BuildError('The bundled manifest cannot be read.') from exc
    return root,manifest

def apply_pack(stock_main: bytes, root: Path, manifest: dict) -> bytes:
    m = manifest
    if (m['format'],m['device'],m['os'],m['stock_sha256'],m['output_sha256']) != (
            'dt2-sample-editing-cfade-1','Digitakt II','1.17',STOCK_SHA256,OUTPUT_SHA256):
        raise BuildError('The bundled patch identity is invalid.')
    if len(stock_main) != m['stock_main_bytes'] or sha(stock_main) != m['stock_main_sha256']:
        raise BuildError('The stock MAIN section is not the verified OS 1.17 image.')
    if len(m['patches']) != 39 or [(v['name'],v['base']) for v in m['modules']] != MODULES:
        raise BuildError('The pack is incomplete or has an unexpected module.')
    result = bytearray(stock_main)
    ranges = []
    for item in m['patches']:
        off = item['address']-MAIN_BASE
        new = bytes.fromhex(item['after'])
        end = off+len(new)
        if off < 0 or end > len(stock_main) or not new or len(new) != item['bytes']:
            raise BuildError('A patch exceeds its owned stock region.')
        if any(not (end <= a or off >= b) for a,b in ranges):
            raise BuildError('Patch regions overlap.')
        if sha(stock_main[off:end]) != item['before_sha256']:
            raise BuildError('The stock firmware does not match a required patch site.')
        ranges.append((off,end))
        result[off:end] = new
    allocated = []
    if m['admitted_range'] != [0x47e8b000,0x47fe0000]:
        raise BuildError('The module allocation range is invalid.')
    for item in m['modules']:
        if item['file'] != item['name']+'.bin':
            raise BuildError('A module resource name is invalid.')
        raw = (root/'assets'/item['file']).read_bytes()
        lo,n,bss = item['base'],len(raw),item['bss']
        hi = lo+n+bss
        if n != item['bytes'] or sha(raw) != item['sha256'] or not n or n%4 or bss < 0 or bss%4:
            raise BuildError('A custom module failed validation.')
        if not 0x47e8b000 <= lo < hi <= 0x47fe0000 or any(not(hi<=a or lo>=b) for a,b in allocated):
            raise BuildError('Custom modules exceed or overlap their reserved memory.')
        allocated.append((lo,hi))
        result += struct.pack('>III',n,lo,bss)+raw
    result += b'\xff'*4
    if len(result) != m['main_bytes'] or sha(result) != m['main_sha256']:
        raise BuildError('The integrated MAIN does not match the validated Sample Editing + CFADE candidate.')
    return bytes(result)

def apply_dsp(stock: bytes,root: Path) -> bytes:
    recipe=json.loads((root/'assets/dsp-recipe.json').read_text(encoding='utf-8'))
    if sha(stock)!=recipe['stock_sha256']:
        raise BuildError('The stock DSP section does not match OS 1.17.')
    assets={}
    for name,digest in recipe['assets'].items():
        if Path(name).name!=name:
            raise BuildError('Invalid DSP resource name.')
        raw=(root/'assets'/name).read_bytes()
        if sha(raw)!=digest:raise BuildError('A DSP resource failed verification.')
        assets[name]=raw
    out=bytearray()
    for op in recipe['operations']:
        if set(op)=={'literal'}:
            out+=bytes.fromhex(op['literal'])
        else:
            if set(op)=={'stock_offset','bytes'}:
                raw=stock;offset=op['stock_offset']
            elif set(op)=={'asset','offset','bytes'} and op['asset'] in assets:
                raw=assets[op['asset']];offset=op['offset']
            else:raise BuildError('Invalid DSP composition operation.')
            n=op['bytes']
            if not isinstance(offset,int) or not isinstance(n,int) or offset<0 or n<=0 or offset+n>len(raw):
                raise BuildError('DSP copy exceeds its owned source.')
            out+=raw[offset:offset+n]
        if len(out)>recipe['output_bytes']:raise BuildError('DSP composition exceeds its verified size.')
    if len(out)!=recipe['output_bytes'] or sha(out)!=recipe['output_sha256']:
        raise BuildError('DSP output does not match the validated CFADE candidate.')
    return bytes(out)

def run_tool(tool: Path,*args) -> str:
    flags = 0x08000000 if os.name == 'nt' else 0
    try:
        run = subprocess.run([str(tool),*map(str,args)],stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT,text=True,encoding='utf-8',
                             errors='replace',creationflags=flags,timeout=300)
    except (OSError,subprocess.TimeoutExpired) as exc:
        raise BuildError('The bundled firmware tool could not complete. No firmware was saved.') from exc
    if run.returncode:
        raise BuildError('Firmware processing failed. No firmware was saved.\n'+run.stdout[-1200:])
    return run.stdout

def extract(tool: Path,syx: Path,folder: Path) -> dict[str,Path]:
    run_tool(tool,'-i',syx,'-o',folder)
    found = {p.name:p for p in folder.iterdir() if p.is_file()}
    if set(found) != SECTION_FILES:
        raise BuildError('Expected exactly six Digitakt II firmware sections.')
    return found

def verify_sections(files: dict[str,Path], manifest: dict, kind: str):
    if set(manifest['sections']) != SECTION_FILES:
        raise BuildError('The section manifest is incomplete.')
    for name,path in files.items():
        if sha256_file(path) != manifest['sections'][name][kind+'_sha256']:
            raise BuildError('A firmware section failed verification: '+name)

def commit_output(candidate: Path,output: Path):
    """Staged same-volume commit; refuse existing paths, including a late race."""
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='wb',delete=False,dir=output.parent,
                                         prefix='.dt2-verified-',suffix='.tmp') as handle:
            temporary = Path(handle.name)
            with candidate.open('rb') as src:
                shutil.copyfileobj(src,handle,1<<20)
            handle.flush()
            os.fsync(handle.fileno())
        if sha256_file(temporary) != OUTPUT_SHA256:
            raise BuildError('The staged output failed its integrity check.')
        if os.name == 'nt':
            os.rename(temporary,output)  # Windows rename never replaces an existing destination.
        else:
            os.link(temporary,output)
            temporary.unlink()
        temporary = None
    except FileExistsError as exc:
        raise BuildError('The output already exists. Choose a new filename.') from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)

def build_firmware(stock_path: str|Path, output_path: str|Path, progress=None) -> dict:
    stock,output = Path(stock_path).resolve(),Path(output_path).resolve()
    tell = progress or (lambda message: None)
    try:
        if not stock.is_file() or stock.suffix.lower() != '.syx':
            raise BuildError('Choose your official Digitakt II OS 1.17 .syx file.')
        if output.suffix.lower() != '.syx':
            raise BuildError('The output filename must end with .syx.')
        if stock == output:
            raise BuildError('Choose an output filename different from your stock input.')
        if output.exists():
            raise BuildError('The output already exists. Choose a new filename.')
        if not output.parent.is_dir():
            raise BuildError('The output folder does not exist.')
        tell('Checking your stock OS 1.17 firmware…')
        if sha256_file(stock) != STOCK_SHA256:
            raise BuildError('Input rejected: this is not the verified stock Digitakt II OS 1.17 file. Other versions, modified, corrupt and previously built firmware are unsupported.')
        root,m = checked_resources()
        tool = root/'native/elektron-firmware-tool.exe'
        with tempfile.TemporaryDirectory(prefix='.dt2-sample-editing-',dir=output.parent) as folder:
            work = Path(folder)
            # Freeze the checked input against external changes during a build.
            local_stock = work/'stock.syx'
            shutil.copyfile(stock,local_stock)
            if sha256_file(local_stock) != STOCK_SHA256:
                raise BuildError('The input changed while being read. Retry with a stable local copy.')
            tell('Reading and verifying all six stock sections…')
            original = extract(tool,local_stock,work/'stock-sections')
            verify_sections(original,m,'stock')
            integrated = apply_pack(original[MAIN].read_bytes(),root,m)
            (work/'main.bin').write_bytes(integrated)
            (work/'dsp.bin').write_bytes(apply_dsp(original['section_7_blob.bin'].read_bytes(),root))
            tell('Integrating Universal Zoom + SmartSlice + SampleLAB + CFADE…')
            candidate = work/'candidate.syx'
            run_tool(tool,'-i',local_stock,'-c','3',work/'main.bin','-c','7',work/'dsp.bin','-l','0','-o',candidate)
            if candidate.stat().st_size != m['output_bytes'] or sha256_file(candidate) != OUTPUT_SHA256:
                raise BuildError('The output does not exactly match the validated CFADE firmware. No firmware was saved.')
            tell('Verifying container integrity and every output section…')
            summary = run_tool(tool,'-i',candidate)
            if not all(t in summary for t in ('device    : Digitakt II (0x14)',
                    'version   : 1.17','container : ELE3','checksums : ok')):
                raise BuildError('The output failed its container integrity check.')
            final = extract(tool,candidate,work/'final-sections')
            verify_sections(final,m,'output')
            for name in SECTION_FILES-{MAIN,'section_7_blob.bin'}:
                if original[name].read_bytes() != final[name].read_bytes():
                    raise BuildError('A stock section changed unexpectedly.')
            tell('Saving the verified firmware…')
            commit_output(candidate,output)
        return dict(status='PASS',path=str(output),sha256=OUTPUT_SHA256,
                    bytes=m['output_bytes'],stock_sha256=STOCK_SHA256,
                    main_sha256=m['main_sha256'],sections=6,modules=[v[0] for v in MODULES],
                    validated_candidate_byte_exact=True,non_MAIN_non_DSP_exact_stock=True,hardware_tested=True)
    except OSError as exc:
        raise BuildError('A file could not be read or written. Check the selected folder and available space.') from exc
