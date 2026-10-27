"""Promote one gated changed image, guide, private source snapshot and evidence."""
import argparse
import hashlib
import json
import shutil
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    ap = argparse.ArgumentParser(__doc__)
    ap.add_argument("--work", type=Path, required=True)
    ap.add_argument("--destination", type=Path, required=True)
    a = ap.parse_args()
    work, dest = a.work.resolve(), a.destination.resolve()
    assert not dest.exists(), "never overwrite a delivered folder"
    gates = json.loads((work / "final-offline-gates.json").read_text())
    assert gates["status"] == "PASS"
    image = work / "final-integrated.syx"
    expected = sha(image)
    assert expected == gates["sha256"]
    for path, digest in gates["source_sha256"].items():
        assert sha(Path(path)) == digest, path
    assert json.loads((work / "audio/audio-report.json").read_text())["status"] == "PASS"
    dest.mkdir()
    target = dest / "DT2_OS117_ALL_IN_ONE_SAMPLE_FUSION_V2_CANDIDATE.syx"
    shutil.copyfile(image, target)
    assert sha(target) == expected
    reports = ("final-build-report.json","final-offline-gates.json","final-control-proof.json",
               "final-ui-proof.json","final-release-proof.json","final-installed-proof.json",
               "plaits-regression.json","integration-regression.xml","module-build.json",
               "final-seed.plan.json","event-parameters.json",
               "dsp-proof/dsp-proof.json","full-frame/native-init.json","full-frame/frame-lifecycle.json")
    for name in reports:
        p = dest / "reports" / name
        p.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(work / name, p)
    shutil.copyfile(HERE / "USER-GUIDE.md", dest / "USER-GUIDE.md")
    (dest / "audio").mkdir()
    for p in sorted((work / "audio").glob("*.wav")) + [work / "audio/audio-report.json"]:
        shutil.copyfile(p, dest / "audio" / p.name)
    ui = work.parent / "final-ui"
    if ui.exists():
        (dest / "ui").mkdir()
        for p in sorted(ui.glob("FUS2-page-*.png")):
            shutil.copyfile(p, dest / "ui" / p.name)
    # Firmware-free implementation snapshot. Full DigiKit/toolchain and the
    # preserved original build inputs remain dependencies, identified by hash.
    with zipfile.ZipFile(dest / "IMPLEMENTATION-SNAPSHOT.zip", "w", zipfile.ZIP_DEFLATED) as z:
        for directory in (HERE, HERE.parent / "twinshot", HERE.parent / "twinshot_fusion"):
            for p in sorted(directory.rglob("*")):
                rel = p.relative_to(directory)
                if (not p.is_file() or p.suffix not in (".py",".c",".h",".s",".toml",".md")
                        or "build" in rel.parts or "__pycache__" in rel.parts
                        or (directory == HERE and str(rel).startswith("modules/core/"))):
                    continue
                z.write(p, str(Path(directory.name) / rel))
    provenance = {"hardware_reference_sha256":"d57bbd459229df8ed69fac91d281da17369b8be57e144acddb05e031b7c04fac",
        "upstream":"e48d0a2a83f055c549391173247cea02c571927f",
        "host_head":"dcda19a5ab63561d75e677d0b8dac45c27bcd00b",
        "private_overlays":gates["source_sha256"],"preserved_inputs":gates["preserved_v1_sources"],
        "not_a_standalone_sdk_or_firmware_source_distribution":True}
    (dest / "SOURCE-PROVENANCE.json").write_text(json.dumps(provenance,indent=2)+"\n")
    manifest = {"candidate":{"file":target.name,"sha256":expected,"bytes":target.stat().st_size},
        "validated_image":str(image),"status":"OFFLINE GATES PASS; V2 HARDWARE UNTESTED",
        "physical_stop_root_cause":"not conclusively isolated; reproduced release-lifetime defect corrected",
        "evidence_binding":"CF MAIN exact to UI/control/7-test image; non-Fusion DSP exact to 25-model PCM image; Fusion/full-frame tests on final DSP",
        "preserved_artifacts":gates["preserved_artifacts"],"no_flash_publish_push_pr_agents":True,
        "files":{str(p.relative_to(dest)):sha(p) for p in sorted(dest.rglob("*")) if p.is_file()}}
    (dest / "DELIVERY-MANIFEST.json").write_text(json.dumps(manifest,indent=2)+"\n")
    assert len(list(dest.glob("*.syx"))) == 1
    print(json.dumps(manifest["candidate"],indent=2))


if __name__ == "__main__":
    main()
