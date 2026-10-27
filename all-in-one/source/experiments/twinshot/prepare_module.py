"""Build only Twinshot; extend the pinned port through existing rule resolvers.

Run from the retained DigiKit import root. Offline, no firmware packaging.
The local GNU assembler is recorded as such, never represented as the absent
Mac m68k-elf GCC. Existing Plaits/DV objects are not rebuilt or substituted.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import struct
import subprocess
import tomllib
from pathlib import Path
from types import SimpleNamespace

from tools.patchkit import patchkit, pk_build, pk_link, port_resolve

HERE = Path(__file__).resolve().parent


def main():
    ap = argparse.ArgumentParser(__doc__)
    for name in ("source", "tools", "patchkit", "report"):
        ap.add_argument("--" + name, type=Path, required=True)
    a = ap.parse_args()
    root, tools, exe = a.source.resolve(), a.tools.resolve(), a.patchkit.resolve()
    env = dict(os.environ)
    env["LD_LIBRARY_PATH"] = str(tools / "usr/lib/x86_64-linux-gnu")
    os.environ.update({"LD_LIBRARY_PATH": env["LD_LIBRARY_PATH"]})
    bins = {n: tools / f"usr/bin/m68k-linux-gnu-{n}" for n in ("as", "ld.bfd")}
    versions = {n: subprocess.check_output([str(p), "--version"], env=env,
                                          text=True).splitlines()[0] for n, p in bins.items()}
    versions["asflags"] = "-mcfv4e"
    versions["gcc"] = "not used: assembly-only ColdFire object"
    versions.update({n + "_sha256": hashlib.sha256(p.read_bytes()).hexdigest()
                     for n, p in bins.items()})
    # Scoped dependency injection into existing object/ISA tooling. The actual
    # assembler/linker and retained checker identities are in the report.
    pk_link._tool = lambda n: bins["ld.bfd" if n == "ld" else n]
    pk_link.ASFLAGS = ["-mcfv4e"]
    pk_link.toolchain_version = lambda: versions
    patchkit.binary = lambda build=True: exe
    dsp = pk_build.sharc_object([HERE / "entry.s", HERE / "lifecycle.s", HERE / "adapter.c"])
    cf = pk_build.cf_object([HERE / "controls.s"])
    out = HERE / "build"
    out.mkdir(exist_ok=True)
    for name, obj in (("twinshot-dsp.dko.json", dsp), ("twinshot-cf.dko.json", cf)):
        (out / name).write_text(pk_build.canonical(obj))
    portfile = root / "devices/ports/dt2-1.17.toml"
    port = tomllib.loads(portfile.read_text())
    stock = root / "firmware/Digitakt_II_OS1.17.syx"
    assert hashlib.sha256(stock.read_bytes()).hexdigest() == port["port"]["syx_sha256"]
    sections = port_resolve.sections_of(stock)
    dspfacts = port_resolve.Dsp(sections[7], "dt2-1.17")
    cffacts = SimpleNamespace(base=0x40000400, main=sections[3])
    anchors = [x for x in port["anchor"] if not x["name"].startswith("twinshot.")]
    manifestfile = HERE / "modules/machines/twinshot/module.toml"
    manifest = tomllib.loads(manifestfile.read_text())
    new = []
    for rule in manifest["anchor_rule"]:
        resolve = port_resolve.resolve_cf_anchor if rule["cpu"] == "cf" else port_resolve.resolve_anchor
        found = resolve(cffacts if rule["cpu"] == "cf" else dspfacts, rule)
        if rule["name"] in ("twinshot.smoother", "twinshot.slot_mask"):
            # cf_words finds the unique instruction signature but labels any
            # word match as data. This signature is the inspected PEA/JSR/
            # ADDQ/CLR sequence; qualify it as code for the hook validator.
            assert found["addr"] == {"twinshot.smoother": 0x4002E8C0,
                                      "twinshot.slot_mask": 0x4002E8E8}[rule["name"]]
            assert found["len"] == 16
            found["kind"] = "code"
        row = {**found, "name": rule["name"], "cpu": rule["cpu"], "mark": "D",
               "rule": "machines/twinshot:" + rule["kind"]}
        new.append(row)
    # These 1.17 native entries were executed by the retained dual-sampler
    # proof. Anchor their bytes, not unverified numeric extern aliases.
    for name, pc in {"slot": 0x1C3F78, "load": 0x1C4E70, "arm": 0x1C4EAF,
                     "pitch": 0x1C4AFE, "sample": 0x1C4ECF,
                     "header_return": 0x1C400A}.items():
        addr = dspfacts.byte_addr(pc)
        new.append({"name": "twinshot.native." + name, "cpu": "sharc", "kind": "code",
                    "addr": addr, "pm": pc, "len": 6,
                    "sha256": port_resolve.sha(dspfacts.stream.read(addr, 6)),
                    "mark": "D", "rule": "machines/twinshot:executed-1.17-native-contract"})
    table = next(x["addr"] for x in anchors if x["name"] == "cf.params.t1")
    fmt = struct.unpack_from(">I", sections[3], table - 0x40000400 + 251 * 60 + 52)[0]
    assert fmt == 0x400E188C
    new.append({"name": "twinshot.native.sample_fmt", "cpu": "cf", "kind": "code",
                "addr": fmt, "len": 6,
                "sha256": port_resolve.sha(sections[3][fmt - 0x40000400:fmt - 0x40000400 + 6]),
                "mark": "D", "rule": "machines/twinshot:stock-sample-descriptor-formatter"})
    portfile.write_text(port_resolve.render(port["port"], port["region"], anchors + new))
    report = {"status": "BUILT_AND_RESOLVED", "source": str(root), "new_anchors": new,
              "objects": {n: hashlib.sha256((out / n).read_bytes()).hexdigest()
                          for n in ("twinshot-dsp.dko.json", "twinshot-cf.dko.json")},
              "sources": {str(p.relative_to(HERE)): hashlib.sha256(p.read_bytes()).hexdigest()
                          for p in [*HERE.iterdir(), manifestfile] if p.suffix in (".c", ".s", ".toml", ".py")},
              "cf_toolchain": versions, "checker_sha256": hashlib.sha256(exe.read_bytes()).hexdigest(),
              "dsp_toolchain": dsp["toolchain"], "firmware_written": False}
    a.report.parent.mkdir(parents=True, exist_ok=True)
    a.report.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
