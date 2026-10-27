"""Assemble research-only ColdFire guest code with unpacked local binutils."""

import hashlib
import os
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
OUT = ROOT / 'out/dt2-dv-offline'
TOOL = OUT / 'tools/root/usr/bin'
LIB = OUT / 'tools/root/usr/lib/x86_64-linux-gnu'


def build() -> dict:
    if not (TOOL / 'm68k-linux-gnu-as').is_file():
        raise RuntimeError('isolated m68k binutils missing under out/dt2-dv-offline/tools')
    env = {**os.environ, 'LD_LIBRARY_PATH': str(LIB)}
    results = {}
    for name, address, entry in (
            ('native_dv', 0x10004000, 'dv_native'),
            ('native_trampoline', 0x10003100, 'dv_dispatch'),
            ('native_gate', 0x10003300, 'dv_gate')):
        source = HERE / f'{name}.s'
        obj = OUT / f'{name}.o'
        elf = OUT / f'{name}.elf'
        binary = OUT / f'{name}.bin'
        subprocess.run([str(TOOL / 'm68k-linux-gnu-as'), '-mcpu=5475',
                        str(source), '-o', str(obj)], check=True, env=env)
        subprocess.run([str(TOOL / 'm68k-linux-gnu-ld'),
                        f'-Ttext=0x{address:08x}', '-e', entry,
                        '-o', str(elf), str(obj)], check=True, env=env)
        subprocess.run([str(TOOL / 'm68k-linux-gnu-objcopy'), '-O', 'binary',
                        '-j', '.text', str(elf), str(binary)],
                       check=True, env=env)
        code = binary.read_bytes()
        if not code or len(code) > 0x1000:
            raise RuntimeError(f'{name} exceeds guest code slot')
        results[name] = {'address': address, 'source_sha256':
                         hashlib.sha256(source.read_bytes()).hexdigest(),
                         'binary_sha256': hashlib.sha256(code).hexdigest(),
                         'size': len(code), 'path': str(binary)}
        if name == 'native_dv':
            symbols = subprocess.run([str(TOOL / 'm68k-linux-gnu-nm'),
                                      str(elf)], check=True, env=env,
                                     capture_output=True, text=True).stdout
            results[name]['phase_bin'] = int(next(
                line.split()[0] for line in symbols.splitlines()
                if line.endswith(' phase_bin')), 16)
    return results


if __name__ == '__main__':
    import json
    print(json.dumps(build(), indent=2))
