"""Build Fusion and a mechanically extended CF transport; preserve V1 sources/objects."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path

from tools.patchkit import patchkit, pk_build, pk_link

HERE = Path(__file__).resolve().parent


def main():
    ap = argparse.ArgumentParser(__doc__)
    for name in ("tools", "patchkit", "report"):
        ap.add_argument("--" + name, type=Path, required=True)
    a = ap.parse_args()
    tools, exe = a.tools.resolve(), a.patchkit.resolve()
    os.environ["LD_LIBRARY_PATH"] = str(tools / "usr/lib/x86_64-linux-gnu")
    bins = {n: tools / f"usr/bin/m68k-linux-gnu-{n}" for n in ("as", "ld.bfd")}
    versions = {n: subprocess.check_output([str(p), "--version"], text=True).splitlines()[0]
                for n, p in bins.items()}
    versions.update({n + "_sha256": hashlib.sha256(p.read_bytes()).hexdigest() for n, p in bins.items()})
    versions.update({"asflags": "-mcfv4e", "gcc": "not used: assembly-only ColdFire object"})
    pk_link._tool = lambda n: bins["ld.bfd" if n == "ld" else n]
    pk_link.ASFLAGS = ["-mcfv4e"]
    pk_link.toolchain_version = lambda: versions
    patchkit.binary = lambda build=True: exe
    build = HERE / "build"
    build.mkdir(exist_ok=True)
    old = HERE.parent / "twinshot"
    transport = (old / "controls.s").read_text()
    first = "    beq.s 8f\n    andi.l #0x700,%d1"
    second = "    bne.s 2f\n    move.w (%a2),(%a3)"
    assert transport.count(first) == transport.count(second) == 1
    transport = transport.replace(first, "    beq.s 8f\n    cmpi.l #13,%d0\n    beq.s 8f\n    andi.l #0x700,%d1")
    transport = transport.replace(second, "    beq.s 6f\n    cmpi.l #13,%d0\n    bne.s 2f\n    move.w -4(%a2),-4(%a3)\n    move.w -2(%a2),-2(%a3)\n6:  move.w (%a2),(%a3)")
    # Generated build input, two explicit mechanical substitutions only.
    path = build / "transport.s"
    path.write_text(transport)
    objects = {"fusion-dsp.dko.json": pk_build.sharc_object([HERE / "entry.s", HERE / "conversions.s", HERE / "adapter.c"]),
               "fusion-cf.dko.json": pk_build.cf_object([HERE / "controls.s"]),
               "transport-cf.dko.json": pk_build.cf_object([path])}
    for name, obj in objects.items():
        (build / name).write_text(pk_build.canonical(obj))
    # Overlay the manifest only, not V1 DSP/source/object. Keep one owner of
    # the existing smoother/mask/header hooks and a shared epoch table.
    original_manifest = old / "modules/machines/twinshot/module.toml"
    manifest = original_manifest.read_text()
    for rel in ("build/twinshot-dsp.dko.json", "entry.s", "lifecycle.s", "adapter.c", "controls.s"):
        manifest = manifest.replace('"../../../' + rel + '"', json.dumps(str(old / rel)))
    manifest = manifest.replace('"../../../build/twinshot-cf.dko.json"', json.dumps(str(build / "transport-cf.dko.json")))
    overlay = HERE / "modules/machines/twinshot/module.toml"
    overlay.parent.mkdir(parents=True, exist_ok=True)
    overlay.write_text(manifest)
    report = {"status": "BUILT", "transport_substitutions": 2, "v1_sources_unchanged": True,
              "cf_toolchain": versions, "objects": {n: hashlib.sha256((build / n).read_bytes()).hexdigest() for n in objects},
              "sources": {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
                          for p in HERE.rglob("*") if p.is_file() and p.suffix in (".c", ".h", ".s", ".py", ".toml")},
              "v1_dsp_object_sha256": hashlib.sha256((old / "build/twinshot-dsp.dko.json").read_bytes()).hexdigest(),
              "dsp_toolchain": objects["fusion-dsp.dko.json"]["toolchain"], "firmware_written": False}
    a.report.parent.mkdir(parents=True, exist_ok=True)
    a.report.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"status": "BUILT", "transport_substitutions": 2}))


if __name__ == "__main__":
    main()
