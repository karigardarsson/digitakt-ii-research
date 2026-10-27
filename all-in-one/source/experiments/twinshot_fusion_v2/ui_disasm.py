"""Bounded read-only actual MAIN disassembly for UI callback contracts."""
import argparse
import os
import subprocess
import tempfile
from pathlib import Path
from tools.patchkit.port_resolve import sections_of

ap=argparse.ArgumentParser(__doc__)
ap.add_argument("--image",type=Path,required=True)
ap.add_argument("--tools",type=Path,required=True)
ap.add_argument("--start",type=lambda x:int(x,0),required=True)
ap.add_argument("--end",type=lambda x:int(x,0),required=True)
a=ap.parse_args()
env=dict(os.environ,LD_LIBRARY_PATH=str(a.tools.resolve()/"usr/lib/x86_64-linux-gnu"))
with tempfile.TemporaryDirectory(prefix="fus2-ui-disasm-") as tmp:
    p=Path(tmp)/"main.bin"
    p.write_bytes(sections_of(a.image)[3])
    subprocess.run([str(a.tools.resolve()/"usr/bin/m68k-linux-gnu-objdump"),"-D","-b","binary",
        "-m","m68k:isa-c","--adjust-vma=0x40000400",f"--start-address={a.start}",
        f"--stop-address={a.end}",str(p)],env=env,check=True)
