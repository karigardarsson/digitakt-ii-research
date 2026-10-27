"""Bounded compiled fusion/native sampler proof, no firmware packaging."""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
from pathlib import Path

from compiled_dual_proof import CONTROL, THUNK
from native_dual_proof import PRIVATE, Native

from tools.patchkit import pk_sharc
from tools.patchkit.port_resolve import sections_of
from tools.sharc_dev.sharcldr import LoadedMemory

HERE = Path(__file__).resolve().parent
V1 = "30c80fe71f935b1271a7ab33e112038b06e37b1332ce842fb16e74cc902eafba"


def run(image):
    assert hashlib.sha256(image.read_bytes()).hexdigest() == V1
    src = '#include "fusion_native.c"\nvoid fusion_test(FusionChannel *s, TwinControl *c, float *out) { int *x = (int *)(c + 1); fusion_channel(s, c, out, x[0], x[1]); }'
    obj = pk_sharc.merge([pk_sharc.assemble(THUNK.replace("twinshot_channel.", "fusion_test.") + (HERE / "conversions.s").read_text()),
                          pk_sharc.compile_c(src, include_dirs=[HERE])])
    ext = {"_native_slot": 0x1C3F78, "tw_load.": 0x1C4E70, "tw_arm.": 0x1C4EAF,
           "tw_pitch.": 0x1C4AFE, "tw_sample.": 0x1C4ECF, "_tw_save": 0x200EB000,
           "_tw_stack": 0x200EC000, "_tw_done": 0x1C6B06, "tw_generations.": 0x200ED000}
    # Isolated linker has no production DM allocator: assign each declared
    # data symbol explicitly in a guarded diagnostic-only window.
    for k, name in enumerate(obj.data_symbols):
        ext[name] = 0x200EF000 + 4 * k
    linked = pk_sharc.link(obj, 0xBF0000, ext)
    memory = LoadedMemory.from_stream(sections_of(image)[7])
    blocks = [dict(b) for b in memory.blocks] + [{"target_address": 0x28000000 + 2 * 0xBF0000,
        "byte_count": len(linked.code_boot), "fill": False, "payload_offset": len(memory.data),
        "payload_len": len(linked.code_boot), "index": len(memory.blocks)}]
    n = Native(LoadedMemory(memory.data + linked.code_boot, blocks), approx_recips=True)
    n.load_slots()
    for p in range(0x200ED000, 0x200F0000, 4):
        n.put(p, 0)
    for p in range(PRIVATE - 16, PRIVATE + 1240 * 4 + 16, 4):
        n.put(p, 0 if PRIVATE <= p < PRIVATE + 1240 * 4 else 0xA5A5A5A5)
    # Header B has 2.25x as many frames, but resident generated PCM is only
    # read near its start in this bounded proof. No fake native routine.
    n.put(0x257810 + 31 * 20 + 8, 9216)
    observations = []
    for sync, mode, state in ((0, 0, PRIVATE), (1, 0, PRIVATE + 1240),
                               (1, 1, PRIVATE + 2480), (1, 3, PRIVATE + 3720)):
        control = struct.pack("<2i3f7i3f2i", 7, 31, 64., 76., .5, 0, 0, 0, 1,
                              1, 0, 0, 60., 127., 64., sync, mode)
        for k, b in enumerate(control):
            n.put(CONTROL + k, b, 1)
        output = 0x200F2000  # outside four private records, control and save/stack
        n.call(linked.symbols["tw_guarded."], {"R4": state, "R8": CONTROL, "R12": output}, bound=60000)
        step_a = n.q31(state + 0x1A8) / (1 << 31)
        step_b = n.q31(state + 472 + 0x1A8) / (1 << 31)
        assert abs(step_a - 0.5) < 1e-6, (sync, mode, step_a, step_b)
        assert abs(step_b - (1.125 if sync else 1.)) < 1e-5, (sync, mode, step_b)
        assert n.get(state + 472 + 0x184) == 48000, "temporary rate leaked into native metadata"
        xs = [struct.unpack("<f", n.data(output + 4 * k, 4))[0] for k in range(32)]
        assert any(xs) and max(map(abs, xs)) <= 1
        observations.append({"sync": sync, "mode": mode, "steps": [step_a, step_b],
                             "native_phase_steps_to_first_boundary": [2048 / step_a, 4608 / step_b],
                             "pcm_sha256": hashlib.sha256(n.data(output, 128)).hexdigest()})
    assert len({x["pcm_sha256"] for x in observations}) == 4
    assert n.data(PRIVATE - 16, 16) == n.data(PRIVATE + 1240 * 4, 16) == b"\xa5" * 16
    return {"status": "PASS", "evidence": "compiled SHARC C + actual native sampler, approximate reciprocal mode",
            "source_image_sha256": V1, "code_bytes": len(linked.code_boot),
            "instructions_redecoded": linked.checked, "observations": observations,
            "max_instructions_channel_block": max(x[1] for x in n.cost), "hardware": "UNTESTED"}


if __name__ == "__main__":
    ap = argparse.ArgumentParser(__doc__)
    ap.add_argument("--image", type=Path, required=True)
    ap.add_argument("--report", type=Path, required=True)
    a = ap.parse_args()
    report = run(a.image)
    a.report.parent.mkdir(parents=True, exist_ok=True)
    a.report.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
