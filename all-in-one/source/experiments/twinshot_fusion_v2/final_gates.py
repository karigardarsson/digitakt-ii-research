"""Final-image integrity, evidence binding and preserved-reference gates."""
import argparse
import hashlib
import json
import struct
import xml.etree.ElementTree as ET
from pathlib import Path

from tools.patchkit import pk_encoding_gate, port_resolve
from tools.patchkit import pk_integrate_dv_zoom as dv
from tools.sharc_dev import sharc_bootcheck
from tools.sharc_dev.sharcldr import LoadedMemory

HERE = Path(__file__).resolve().parent


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def read(p):
    return json.loads(p.read_text())


def main():
    ap = argparse.ArgumentParser(__doc__)
    for name in ("work", "source", "baseline"):
        ap.add_argument("--" + name, type=Path, required=True)
    a = ap.parse_args()
    w, baseline = a.work.resolve(), a.baseline.resolve()
    image, planfile = w / "final-integrated.syx", w / "final-seed.plan.json"
    current, old = port_resolve.sections_of(image), port_resolve.sections_of(baseline)
    expected = sha(image)
    assert sha(baseline) == "d57bbd459229df8ed69fac91d281da17369b8be57e144acddb05e031b7c04fac"
    plan, build = read(planfile), read(w / "final-build-report.json")
    assert build["candidate"]["sha256"] == expected
    assert all(x["result"] == "PASS" for x in build["candidate"]["bootstrap"].values())
    integrity = build["candidate"]["integrity"]
    assert integrity["packet_checksums"] == f'{integrity["message_count"]}/{integrity["message_count"]}'
    assert integrity["file_sha256"] == expected
    assert not pk_encoding_gate.check_image(image, planfile)
    stock = port_resolve.sections_of(a.source / "firmware/Digitakt_II_OS1.17.syx")
    boot = sharc_bootcheck.check(current[7])
    assert boot.ok
    assert not [f for f in sharc_bootcheck.check_against_stock(stock[7], current[7]) if f.level == "error"]
    for sid in (2, 4, 5, 8):
        assert current[sid] == old[sid] == stock[sid]
    dsp_evidence = []
    for stem in ("final-seed",):
        p = w / (stem + ".syx")
        assert current[7] == port_resolve.sections_of(p)[7]
        dsp_evidence.append({"image": str(p), "sha256": sha(p)})
    for name, stem in (("final-release-proof.json", "final-seed"),
                       ("final-installed-proof.json", "final-seed"),
                       ("dsp-proof/dsp-proof.json", "final-seed"),
                       ("full-frame/frame-lifecycle.json", "final-integrated")):
        report = read(w / name)
        assert report["status"] == "PASS"
        assert report.get("image_sha256", report.get("sha256")) == sha(w / (stem + ".syx"))
    controls, ui, pcm = [read(w / name) for name in
                         ("final-control-proof.json", "final-ui-proof.json", "plaits-regression.json")]
    assert controls["status"] == ui["status"] == pcm["status"] == "PASS"
    cf_reference = w.parent / "final-integrated.syx"
    cf_sha = sha(cf_reference)
    assert current[3] == port_resolve.sections_of(cf_reference)[3]
    assert controls["storage"]["image_sha256"] == ui["image_sha256"] == cf_sha
    assert all(r["sha256"] == cf_sha for r in controls["start_lfo"].values())
    event_parameters = read(w / "event-parameters.json")
    assert event_parameters["status"] == "PASS" and event_parameters["candidate_sha256"] == expected
    assert len(pcm["plaits_25_models"]) == 25 and pcm["baseline_sha256"] == sha(baseline)
    # Reuse the completed 25-model PCM sweep. The final isolated correction
    # lives only in Fusion: check every other SHARC patch/allocation/hash AND
    # its actual loaded bytes, not just a module list or source comparison.
    pcm_image = w.parent / "final-integrated.syx"
    pcm_plan = read(w.parent / "final-seed.plan.json")
    assert pcm["image_sha256"] == sha(pcm_image)
    before = LoadedMemory.from_stream(port_resolve.sections_of(pcm_image)[7])
    after = LoadedMemory.from_stream(current[7])
    preserved_dsp = []
    for write in plan["writes"]:
        if write["cpu"] != "sharc" or write["owner"].startswith("machines/twinshot-fusion@"):
            continue
        prior = next(x for x in pcm_plan["writes"] if x["owner"] == write["owner"] and
                     x["what"] == write["what"] and x["addr"] == write["addr"])
        assert write == prior, (write["owner"],write["what"])
        if write["kind"] == "place":
            lo, hi = write["addr"], write["addr"]+write["len"]
            for block in after.blocks:
                low, high = max(lo,block["target_address"]),min(hi,block["target_address"]+block["byte_count"])
                if low < high:
                    assert before.read(low,high-low) == after.read(low,high-low)
        preserved_dsp.append({"owner":write["owner"],"what":write["what"]})
    suites = list(ET.parse(w / "integration-regression.xml").iter("testsuite"))
    assert sum(int(s.attrib["tests"]) for s in suites) == 7
    assert all(not int(s.attrib["failures"]) and not int(s.attrib["errors"]) for s in suites)
    assert read(w / "audio/audio-report.json")["status"] == "PASS"
    assert dv.at(current[3], dv.INTRO_SCENES, 80) == dv.STOCK_INTRO_SCENES
    pf = next(v for k, v in plan["symbols"].items() if k.endswith(":pf_table"))
    for row in (79, 89, 99):
        src = dv.source_for_loaded_address(current[3], pf + row * 60)
        assert struct.unpack(">I", dv.at(current[3], src + 12, 4))[0] == 0xF00
    baseline_build = read(baseline.parent / "build-report.json")
    for addr, size in ((0x47EA0000, baseline_build["ui"]["bytes"]),
                       (0x47EC0000, dv.MUSICAL_PAYLOAD_BYTES)):
        assert dv.at(current[3], dv.source_for_loaded_address(current[3], addr), size) == \
               dv.at(old[3], dv.source_for_loaded_address(old[3], addr), size)
    assert {x.split("@")[0] for x in plan["modules"]} == {
        "core/expansion","core/machines","core/params","core/dsp-inline",
        "machines/plaits","machines/plaits-48k","machines/twinshot","machines/twinshot-fusion"}
    previous = read(baseline.parent / "offline-gates.json")
    preserved = [{"path":str(baseline),"sha256":sha(baseline)}]
    preserved += previous["older_baselines_preserved"]
    twin = baseline.parent.parent / "twinshot-all-in-one-v1/DT2_OS117_ALL_IN_ONE_TWINSHOT_V1_CANDIDATE.syx"
    preserved += [{"path":str(twin),"sha256":"30c80fe71f935b1271a7ab33e112038b06e37b1332ce842fb16e74cc902eafba"}]
    for item in preserved:
        assert sha(Path(item["path"])) == item["sha256"], item
    preserved_sources = {}
    for path, digest in previous["source_sha256"].items():
        if "/experiments/twinshot/" in path or "/experiments/twinshot_fusion/" in path:
            assert sha(Path(path)) == digest, path
            preserved_sources[path] = digest
    for name in ("twinshot-dsp.dko.json", "twinshot-cf.dko.json"):
        p = HERE.parent / "twinshot/build" / name
        preserved_sources[str(p)] = sha(p)
    sources = [p for p in HERE.rglob("*") if p.is_file() and p.suffix in (".c",".h",".s",".py",".toml")]
    sources += [a.source / "modules/core/machines/types.toml",a.source / "devices/ports/dt2-1.17.toml"]
    for used, capacity in plan["regions"].values():
        assert used <= capacity
    report = {"status":"PASS","image":str(image),"sha256":expected,"bytes":image.stat().st_size,
        "encoding_violations":0,"sharc_bootcheck":"PASS","stock_sections":[2,4,5,8],
        "stock_startup":True,"wave_rows_0_15":[79,89,99],"exact_DV_WAVE_Zoom_DX7_UI":True,
        "modules":plan["modules"],"regions":plan["regions"],"dsp_evidence_images":dsp_evidence,
        "preserved_artifacts":preserved,"preserved_v1_sources":preserved_sources,
        "source_sha256":{str(p.resolve()):sha(p) for p in sorted(sources)},
        "pcm_tested_image_sha256":sha(pcm_image),"non_fusion_dsp_unchanged":preserved_dsp,
        "cf_tested_image_sha256":cf_sha,"CF_MAIN_byte_identical_to_UI_control_WAVE_Zoom_tests":True,
        "hardware":"V2 UNTESTED; V1 user-tested; intermittent physical STOP cause not conclusively isolated",
        "unverified":["physical transport/reentrancy and intermittent STOP","full Project filesystem/power-cycle",
                      "sample collect/purge/remapping and secondary-sample portability","silicon CPU/deadline/full track load"]}
    (w / "final-offline-gates.json").write_text(json.dumps(report,indent=2)+"\n")
    print(json.dumps({k:report[k] for k in ("status","sha256","bytes","encoding_violations","regions")},indent=2))


if __name__ == "__main__":
    main()
