"""Build Fusion and a mechanically extended CF transport; preserve V1 sources/objects."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
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
    transport = transport.replace(second, "    beq.s 6f\n    cmpi.l #13,%d0\n    beq.s 7f\n    cmpi.l #14,%d0\n    bne.s 2f\n7:  move.w -4(%a2),-4(%a3)\n    move.w -2(%a2),-2(%a3)\n6:  move.w (%a2),(%a3)")
    # Generated build input, two explicit mechanical substitutions only.
    path = build / "transport.s"
    path.write_text(transport)
    objects = {"fusion-dsp.dko.json": pk_build.sharc_object([HERE / "entry.s", HERE / "conversions.s", HERE / "adapter.c"]),
               "fusion-cf.dko.json": pk_build.cf_object([HERE / "controls.s", HERE / "pages.s"]),
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
    # Slots 8/9 belong only to the new type's start controls. Its native bank
    # split/reconstruction predicate is bypassed; no stock slot is repurposed.
    root = Path.cwd() / "out/all-in-one-plaits25-e48/source/modules/core/params"
    params = (root / "module.toml").read_text()
    formula = '"25 + $slot + ($slot >> 2) * 2",'
    assert params.count(formula) == 1
    params = params.replace(formula, '"25 + $slot + ($slot >> 2) * 2 - ($slot >> 3) * 8",')
    for prefix in ("src/", "build/"):
        params = params.replace('"' + prefix, '"' + str(root) + '/' + prefix)
    overlay_params = HERE / "modules/core/params/module.toml"
    overlay_params.parent.mkdir(parents=True, exist_ok=True)
    overlay_params.write_text(params)
    machines_root = root.parent / "machines"
    machines = (machines_root / "module.toml").read_text()
    assert machines.count('to = "mf_typearg"') == 1
    machines = machines.replace('to = "mf_typearg"', 'to = "fusion2_typearg"')
    for prefix in ("src/", "build/"):
        machines = machines.replace('"' + prefix, '"' + str(machines_root) + '/' + prefix)
    overlay_machines = HERE / "modules/core/machines/module.toml"
    overlay_machines.parent.mkdir(parents=True, exist_ok=True)
    overlay_machines.write_text(machines)
    (overlay_machines.parent / "types.toml").write_text((machines_root / "types.toml").read_text())
    # Library::get takes the FIRST matching id: --modules-dir adds modules,
    # it does not override core modules in --root/modules. V2's old invocation
    # silently used original core/params (START mapped to filter 37/38) and
    # original core/machines (START typearg wrapper never installed).
    # Stage one unambiguous private library, keeping pinned originals intact.
    stage = build / "apply-root"
    ports = stage / "devices/ports"
    ports.mkdir(parents=True, exist_ok=True)
    source_root = root.parents[2]
    for port in (source_root / "devices/ports").glob("*.toml"):
        content=port.read_text()
        if port.name=="dt2-1.17.toml":
            # New bounded call-site owner, not the shared WAVE/Zoom hook.
            original=bytes.fromhex("4eb940111cda")
            content+='\n[[anchor]]\nname = "fusion2.encoder_delta"\ncpu = "cf"\nkind = "code"\naddr = 0x4006cc62\nlen = 6\nsha256 = "'+hashlib.sha256(original).hexdigest()+'"\nmark = "D"\nrule = "FUS2 native discrete accumulator return"\n'
            # Six-byte native multi-op at SW1c2654, immediately before the
            # raw SRC LEV store. Pin pristine bytes, not a guessed code shape.
            content+='\n[[anchor]]\nname = "fusion2.level_decode"\ncpu = "sharc"\nkind = "code"\naddr = 0x28384ca8\npm = 0x1c2654\nlen = 6\nsha256 = "43243e87b9033c6ff0d2db16acda05a8a70fa82ee1b1ee9b2e58f27b155f2e00"\nmark = "D"\nrule = "Native SRC field9 gain consumer, type14 ownership isolation"\n'
        (ports / port.name).write_text(content)
    staged = {}
    ids = ("core/expansion", "core/machines", "core/params", "core/dsp-inline",
           "machines/plaits", "machines/plaits-48k", "machines/twinshot", "machines/twinshot-fusion")
    for module_id in ids:
        local = HERE / "modules" / module_id / "module.toml"
        original = local if local.exists() else source_root / "modules" / module_id / "module.toml"
        text = original.read_text()
        if module_id=="machines/plaits":
            # Reuse the established header-only ABI with one hook owner.
            # FUS2's wrapper falls through to the ORIGINAL DX7 wrapper for
            # everything but its SYNC codes. Never change the pinned source.
            assert text.count('to = "dx7_header_text"')==1
            text=text.replace('to = "dx7_header_text"','to = "fusion2_header_text"')
        # Resolve file-valued strings only; semantic names/expressions remain
        # unchanged. Rust Path::join accepts these absolute paths.
        def absolute_file(match):
            value = match.group(1)
            if not value.endswith((".s", ".c", ".h", ".json", ".toml", ".bin")):
                return match.group(0)
            candidate = original.parent / value
            return json.dumps(str(candidate.resolve())) if candidate.is_file() else match.group(0)
        text = re.sub(r'"([^"\n]+)"', absolute_file, text)
        dest = stage / "modules" / module_id / "module.toml"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text)
        staged[module_id] = {"source": str(original), "sha256": hashlib.sha256(text.encode()).hexdigest()}
    report = {"status": "BUILT", "transport_substitutions": 2, "v1_sources_unchanged": True,
              "cf_toolchain": versions, "objects": {n: hashlib.sha256((build / n).read_bytes()).hexdigest() for n in objects},
              "sources": {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
                          for p in HERE.rglob("*") if p.is_file() and p.suffix in (".c", ".h", ".s", ".py", ".toml")},
              "v1_dsp_object_sha256": hashlib.sha256((old / "build/twinshot-dsp.dko.json").read_bytes()).hexdigest(),
              "dsp_toolchain": objects["fusion-dsp.dko.json"]["toolchain"], "firmware_written": False}
    report.update({"apply_root": str(stage), "unambiguous_modules": staged})
    a.report.parent.mkdir(parents=True, exist_ok=True)
    a.report.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"status": "BUILT", "transport_substitutions": 2}))


if __name__ == "__main__":
    main()
