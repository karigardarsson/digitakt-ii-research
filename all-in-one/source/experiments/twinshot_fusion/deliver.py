"""Promote exactly one fully checked image, reports and generated audition WAVs."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text())


def main():
    ap = argparse.ArgumentParser(__doc__)
    for name in ("work", "audio", "destination", "v1"):
        ap.add_argument("--" + name, type=Path, required=True)
    a = ap.parse_args()
    for name, value in vars(a).items():
        setattr(a, name, value.resolve())
    assert not a.destination.exists(), "never overwrite a delivered folder"
    work, audio = a.work, a.audio
    gates, controls, ui, dsp, pcm = (read(work / p) for p in
        ("offline-gates.json", "control-verification.json", "ui-verification.json", "installed-dsp.json", "plaits-regression.json"))
    assert all(r["status"] == "PASS" for r in (gates, controls, ui, dsp, pcm))
    image = work / "integrated-validation.syx"
    expected = sha(image)
    assert expected == gates["sha256"] == controls["sha256"] == ui["image_sha256"]
    assert dsp["sha256"] == gates["dsp_matches_tested_seed"]
    assert pcm["image_sha256"] == gates["plaits_25_pcm_tested_seed"]["sha256"]
    assert len(pcm["plaits_25_models"]) == 25
    assert sha(a.v1) == gates["v1_hardware_reference_preserved"]
    assert read(audio / "audio-report.json")["status"] == "PASS"
    suites = list(ET.parse(work / "integration-regression.xml").iter("testsuite"))
    assert sum(int(s.attrib["tests"]) for s in suites) == 7
    assert all(not int(s.attrib["failures"]) and not int(s.attrib["errors"]) for s in suites)
    build = read(work / "build-report.json")
    assert build["candidate"]["sha256"] == expected
    assert all(x["result"] == "PASS" for x in build["candidate"]["bootstrap"].values())
    assert build["candidate"]["integrity"]["packet_checksums"] == "16277/16277"
    a.destination.mkdir()
    target = a.destination / "DT2_OS117_ALL_IN_ONE_TWINSHOT_FUSION_V1_CANDIDATE.syx"
    shutil.copyfile(image, target)
    assert sha(target) == expected
    reports = ("build-report.json", "offline-gates.json", "control-verification.json", "ui-verification.json",
               "installed-dsp.json", "plaits-regression.json", "integration-regression.xml", "module-build.json",
               "validated-seed.plan.json")
    for name in reports:
        shutil.copyfile(work / name, a.destination / name)
    (a.destination / "audio").mkdir()
    for p in sorted(audio.glob("*.wav")) + [audio / "audio-report.json"]:
        shutil.copyfile(p, a.destination / "audio" / p.name)
    manifest = {"candidate": {"file": target.name, "sha256": expected, "bytes": target.stat().st_size},
        "validated_work_image": str(image), "v1_hardware_reference": {"path": str(a.v1), "sha256": sha(a.v1)},
        "hardware": "UNTESTED Fusion; V1 technical operation reported by user",
        "audio_evidence": "generated inputs, shared host C kernel and linear resampling; not firmware or hardware recording",
        "files": {str(p.relative_to(a.destination)): sha(p) for p in sorted(a.destination.rglob("*")) if p.is_file()},
        "no_flash_push_publication": True}
    (a.destination / "DELIVERY-MANIFEST.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest["candidate"], indent=2))


if __name__ == "__main__":
    main()
