"""A/B installed 25-model Plaits PCM and stock sampler against the last image.

Use the pinned source root and its existing tests/support fixtures. No baseline
report is overwritten. Two bounded blocks per model, both stereo buses.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from patchkit.test_all_in_one25_dsp import armed
from patchkit.test_dsp_inline import active_voice_pokes
from support.dsp_inline_image import image_memory, render_voice
from support.plaits_rig import CALLER_RET, ImageRig


def run(image, planfile, baseline, oldplanfile):
    plan = json.loads(planfile.read_text())
    oldplan = json.loads(oldplanfile.read_text())
    cases = []
    for model in range(25):
        rigs = [ImageRig(p, doc) for p, doc in ((baseline, oldplan), (image, plan))]
        for rig in rigs:
            armed(rig, 10, model)
        digest = hashlib.sha256()
        for block in range(2):
            for rig in rigs:
                for voice in (20, 21):
                    rig.poke_byte(0x24F0CC + voice, int(block == 0))
            for voice in (20, 21):
                a, b = (rig.block(voice) for rig in rigs)
                assert a["final_pc_sw"] == b["final_pc_sw"] == CALLER_RET
                assert a["out"] == b["out"], (model, block, voice)
                digest.update(bytes.fromhex("".join(f"{w:08x}" for w in b["out"])))
        cases.append({"model": model, "two_blocks_stereo_bit_exact": True, "pcm_sha256": digest.hexdigest()})
        print(f"Plaits {model}: exact", flush=True)
    mems = [image_memory(p) for p in (baseline, image)]
    for voice in (4, 29):
        a, b = [render_voice(mem, voice, active_voice_pokes(voice)) for mem in mems]
        for field in ("out", "record", "regs"):
            assert a[field] == b[field], (voice, field)
    return {"status": "PASS", "image_sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
            "baseline_sha256": hashlib.sha256(baseline.read_bytes()).hexdigest(),
            "plaits_25_models": cases, "stock_active_sampler_pcm_state_registers": "bit exact",
            "hardware": "UNTESTED", "real_time_deadline": "not measured by this bounded function-call test"}


if __name__ == "__main__":
    ap = argparse.ArgumentParser(__doc__)
    for name in ("image", "plan", "baseline", "old-plan", "report"):
        ap.add_argument("--" + name, type=Path, required=True)
    a = ap.parse_args()
    result = run(a.image, a.plan, a.baseline, a.old_plan)
    a.report.write_text(json.dumps(result, indent=2) + "\n")
    print("25-model and stock PCM comparison PASS")
