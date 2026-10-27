"""Native STOP setter/packing slice. No natural panel/ISR scheduling claim."""
import argparse
import hashlib
import json
import os
import struct
import subprocess
import tempfile
from pathlib import Path

from tools.patchkit import pk_cfcall, port_resolve
from tools.patchkit import pk_integrate_dv_zoom as dv


def run(image, tools):
    main = port_resolve.sections_of(image)[3]
    env = dict(os.environ, LD_LIBRARY_PATH=str(tools.resolve() / "usr/lib/x86_64-linux-gnu"))
    prefix = str(tools.resolve() / "usr/bin/m68k-linux-gnu-")
    with tempfile.TemporaryDirectory(prefix="fusion-stop-") as temp:
        obj, elf, binary = [Path(temp) / s for s in ("caller.o", "caller.elf", "caller.bin")]
        subprocess.run([prefix + "as", "-mcfv4e", "-o", str(obj), str(Path(__file__).with_name("stop_fixture.s"))], env=env, check=True)
        subprocess.run([prefix + "ld.bfd", "-Ttext=0x47f20000", "-e", "fusion_stop_fixture", "-o", str(elf), str(obj)], env=env, check=True)
        subprocess.run([prefix + "objcopy", "-O", "binary", str(elf), str(binary)], env=env, check=True)
        syms = subprocess.check_output([prefix + "nm", "-g", str(elf)], env=env, text=True)
        symbols = {s.split()[2]: int(s.split()[0], 16) for s in syms.splitlines() if len(s.split()) == 3}
        pokes = {0x40000400: main[:dv.STOCK_MAIN_END - 0x40000400], 0x47F20000: binary.read_bytes(),
                 0x4002EACC: struct.pack(">HI", 0x4EF9, symbols["fusion_stop_done"])}
    for src, size, dest, bss in dv.parsed_segments(main)[0]:
        pokes[dest] = dv.at(main, src + 12, size) + bytes(bss)
    pk_cfcall._built = True
    calls = [{"pc": symbols["fusion_stop_fixture"], "args": [event],
              "reads": [(0x8000536A, 10)], "max": 100000} for event in (2, 0, 2, 2)]
    rows = pk_cfcall.CfMachine("Digitakt_II_OS1.17.syx", timeout=60).run(pokes, calls)
    masks = []
    for event, row in zip((2, 0, 2, 2), rows, strict=True):
        assert row.ok and not row.clobbered, (row.error, row.clobbered)
        values = struct.unpack(">5H", row.reads[0])
        assert values == ((0, 65535, 0, 65535, 65535) if event == 2 else (0, 0, 0, 0, 0)), values
        masks.append(values)
    return {"status": "PASS", "image_sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
            "stock_transport_entry": "0x400d7ae6 calls 0x4002d754(2) at 0x400d7b28",
            "executed": "0x4002d754 -> 0x4002e9fe..0x4002eacc",
            "frame_masks_22_24_26_28_2a": masks,
            "tier": "native CF instruction slices, reconstructed registers; no natural transport/ISR or hardware proof"}


if __name__ == "__main__":
    ap = argparse.ArgumentParser(__doc__)
    for name in ("image", "tools", "report"):
        ap.add_argument("--" + name, type=Path, required=True)
    a = ap.parse_args()
    r = run(a.image, a.tools)
    a.report.write_text(json.dumps(r, indent=2) + "\n")
    print(json.dumps(r, indent=2))
