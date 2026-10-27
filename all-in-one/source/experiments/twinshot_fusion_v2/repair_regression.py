"""Failed-image counterexamples and linked-image FUS2 repair assertions.

Run from the pinned source with its tests import root. Audio event delivery is
observed at a terminal emitter, not conflated with natural RTOS/DSP playback.
"""
import argparse
import hashlib
import json
import os
import struct
import subprocess
import tempfile
from pathlib import Path

from patchkit import test_all_in_one25 as ui
from tools.patchkit import pk_cfcall, port_resolve
from tools.patchkit import pk_integrate_dv_zoom as dv


def fixture(tools):
    prefix = tools.resolve() / "usr/bin/m68k-linux-gnu-"
    env = dict(os.environ, LD_LIBRARY_PATH=str(tools.resolve() / "usr/lib/x86_64-linux-gnu"))
    with tempfile.TemporaryDirectory(prefix="fus2-repair-") as tmp:
        obj, elf, binary = (Path(tmp) / x for x in ("fixture.o", "fixture.elf", "fixture.bin"))
        subprocess.run([str(prefix) + "as", "-mcfv4e", "-o", str(obj),
                        str(Path(__file__).with_name("repair_fixture.s"))], env=env, check=True)
        subprocess.run([str(prefix) + "ld.bfd", "-Ttext=0x47f22000", "-e", "repair_event_sink",
                        "-o", str(elf), str(obj)], env=env, check=True)
        subprocess.run([str(prefix) + "objcopy", "-O", "binary", str(elf), str(binary)], env=env, check=True)
        return binary.read_bytes()


def main():
    ap = argparse.ArgumentParser(__doc__)
    for name in ("plan", "tools", "report"):
        ap.add_argument("--" + name, type=Path, required=True)
    ap.add_argument("--expect-failed", action="store_true")
    a = ap.parse_args()
    pk_cfcall._built = True
    plan = json.loads(a.plan.read_text())
    mainbytes = port_resolve.sections_of(ui.CANDIDATE)[3]
    def sym(name):
        return next(v for k, v in plan["symbols"].items() if k.endswith(":" + name))
    table = sym("pf_table")
    mappings = {r: struct.unpack(">I", dv.at(mainbytes, dv.source_for_loaded_address(mainbytes, table + r * 60 + 4), 4))[0]
                for r in (299, 300)}
    hook = next(h for h in plan["hooks"] if h["anchor"] == "cf.machines.typearg.0")
    wrapper_installed = hook["to"] == sym("fusion2_typearg")
    assert mappings == ({299:37, 300:38} if a.expect_failed else {299:29, 300:30}), mappings
    assert wrapper_installed != a.expect_failed
    hint = struct.unpack(">I", dv.at(mainbytes, dv.source_for_loaded_address(mainbytes, sym("mf_names")+14*12+8), 4))[0]
    assert hint == (0x40242C9E if a.expect_failed else 0), hex(hint)
    pokes, page, sound, _ = ui.fixture.__wrapped__()
    if not a.expect_failed:
        level = ui.checked(pk_cfcall.CfMachine("Digitakt_II_OS1.17.syx", timeout=90).run(
            {**pokes, sound+0xA2:bytes([14]), page+0x90:struct.pack(">I",1)},
            [{"pc":sym("fusion2_param"),"args":[page,8]}]))
        assert level[0].d0 == 10, level[0].d0
    code = fixture(a.tools)
    pokes.update({0x47F22000: code, 0x40139B54: bytes.fromhex("4ef947f22000"),
                  0x47F0C000: bytes(68)})
    rows = []
    for typ in (0, 6, 11, 12, 13, 14):
        edits = {**pokes, sound + 0xA2: bytes([typ])}
        calls = [{"pc": 0x4002D438, "args": [sound, 0]},
                 {"pc": 0x4002DCEE, "args": [0]},
                 {"pc": 0x400DA3A2, "args": [29, typ, 0]},
                 {"pc": 0x400DA3A2, "args": [30, typ, 0]}]
        for note in (60, 67):
            calls += [{"pc":0x401200F2, "args":[0,note,100,64,0,-1,-1,-1,-1], "max":300000,
                       "reads":[(0x47F0C000,68)]},
                      {"pc":0x401204EC, "args":[0,note,64], "max":300000,
                       "reads":[(0x47F0C000,68)]}]
        res = ui.checked(pk_cfcall.CfMachine("Digitakt_II_OS1.17.syx", timeout=90).run(edits, calls))
        counts = [struct.unpack_from(">I", r.reads[0])[0] for r in res[4:]]
        actions = [struct.unpack_from(">I", r.reads[0], 4+12)[0] for r in res[4:]]
        suppressed = a.expect_failed and typ == 14
        assert counts == ([0,0,0,0] if suppressed else [1,2,3,4]), (typ, counts)
        assert res[1].d0 == int(not suppressed), (typ, res[1].d0)
        if not suppressed:
            assert actions == [1,2,1,2], (typ, actions)
        if typ == 14 and not a.expect_failed:
            assert [r.d0 for r in res[2:4]] == [299,300]
        rows.append({"type":typ, "audio_predicate":res[1].d0,
                     "typearg_29_30":[r.d0 for r in res[2:4]],
                     "audio_emitter_counts":counts,"actions":actions})
    report = {"status":"PASS", "expect_failed":a.expect_failed,
              "image_sha256":hashlib.sha256(ui.CANDIDATE.read_bytes()).hexdigest(),
              "actual_descriptor_mirrors":mappings, "typearg_wrapper_installed":wrapper_installed,
              "type14_header_hint":hint,
              "native_audition_producer":rows,
              "tier":"native note dispatch and terminal event capture; not natural scheduling or hardware"}
    a.report.parent.mkdir(parents=True,exist_ok=True)
    a.report.write_text(json.dumps(report,indent=2)+"\n")
    print(json.dumps(report,indent=2),flush=True)


if __name__ == "__main__":
    main()
