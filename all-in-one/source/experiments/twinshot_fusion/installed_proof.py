"""Installed Fusion DSP, native lifecycle, stereo and V1 differential checks."""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
from pathlib import Path

import numpy as np
from native_dual_proof import OUT, REC, STACK, Native

from tools.patchkit.port_resolve import sections_of
from tools.sharc_dev import sharc_trace as st
from tools.sharc_dev.sharcldr import LoadedMemory


def symbol(plan, name):
    return next(v for k, v in plan["symbols"].items() if k.endswith(":" + name))


def run(image, planpath, v1):
    plan = json.loads(planpath.read_text())
    n = Native(LoadedMemory.from_stream(sections_of(image)[7]), approx_recips=True)
    old = Native(LoadedMemory.from_stream(sections_of(v1)[7]), approx_recips=True)
    for engine in (n, old):
        engine.load_slots()
    state = symbol(plan, "tf_states.")

    def render(engine, voice=0, typ=13, *, trig=0, release=0, reset=0,
               sync=0, mode=0, mix=0x4000, slot1=7, slot2=31,
               tune1=0x4000, tune2=0x4000, filter=0x4000, decay=0x7F00):
        track = voice // 2
        engine.put(0x255970 + 2 * track, typ, 2)
        engine.put(0x2558DC + 2 * (1 + track), 60 << 8, 2)
        engine.put(0x2558DC + 0x2A, reset, 2)
        values = (tune1, sync if typ == 13 else filter, mode if typ == 13 else decay,
                  slot1, tune2, mix, slot2, 0x7F00)
        for offset, value in zip((0, 1, 2, 3, 6, 7, 8, 9), values, strict=True):
            engine.put(0x2558DC + 2 * (0x6D + 0x30 * track + offset), value, 2)
        engine.put(0x24F0CC + voice, trig, 1)
        engine.put(0x24F0EC + voice, release, 1)
        protected = {name: 0x100 + k for k, name in enumerate(("I8", "I9", "I10", "I11", "I13", "I14", "I15", "M12", "B3"))}
        circular = {"B6": STACK - 0x400, "B7": STACK - 0x400, "L6": 0x800, "L7": 0x800,
                    "MODE1": 0x1011800}
        engine.call(0x1C4ECF, {"R4": 0x2412CC + voice * REC, "R8": OUT, "R12": 32,
                               **protected, **circular}, bound=80000)
        for name, value in {**protected, **circular, "I6": STACK + 0x10000, "I7": STACK}.items():
            assert engine.runner.state.uregs[st.UREG_CODES[name]].value == value, name
        xs = np.frombuffer(engine.data(OUT, 128), dtype="<f4").copy()
        assert np.isfinite(xs).all() and np.max(np.abs(xs)) <= 1.00001
        return xs

    # Both machines remain selectable. Exact V1 hardware-reference output,
    # not newly generated expected waveforms; include active control changes.
    differential = []
    for k, cfg in enumerate(({}, {"slot2": 1055, "tune2": 0x4C00},
                              {"mix": 0}, {"mix": 0x7F00},
                              {"filter": 0, "decay": 0x2000},
                              {"filter": 0x7F00, "slot1": 1031})):
        for v in (0, 1):
            got, reference = (render(e, v, 12, trig=1, **cfg) for e in (n, old))
            assert np.array_equal(got, reference), (k, v, got, reference)
            assert np.array_equal(render(n, v, 12, **cfg), render(old, v, 12, **cfg))
        differential.append(k)
    # Fusion BLEND/SYNC OFF is also V1's bypass/no-added-decay layering.
    fresh_v1 = Native(LoadedMemory.from_stream(sections_of(v1)[7]), approx_recips=True)
    fresh_v1.load_slots()
    for v in (0, 1):
        got, expected = render(n, v, trig=1), render(fresh_v1, v, 12, trig=1)
        assert np.array_equal(got, expected), (v, got, expected)

    outputs = []
    for mode in range(4):
        for v in (0, 1):
            xs = render(n, v, trig=1, mode=mode << 8, mix=0x7F00)
            outputs.append(hashlib.sha256(xs.tobytes()).hexdigest())
            if mode:
                s = state + v * 1240
                a = np.frombuffer(n.data(s + 972, 128), dtype="<f4")
                b = np.frombuffer(n.data(s + 1100, 128), dtype="<f4")
                phase = struct.unpack("<f", n.data(s + 1232, 4))[0]
                duck = np.float32(0)
                dc = np.float32(0)
                expected = []
                for x, y in zip(a, b, strict=True):
                    target = min(np.float32(1), np.float32(4) * abs(x))
                    duck = np.float32(duck + np.float32(.01 if target > duck else .001) * np.float32(target - duck))
                    if mode == 1:
                        product = np.float32(x * y)
                        dc = np.float32(dc + np.float32(.002) * np.float32(product - dc))
                        product = np.float32(product - dc)
                        z = np.float32(2) * product / np.float32(1 + np.float32(2) * abs(product))
                    elif mode == 2:
                        z = np.float32(.5) * np.float32(x + np.float32(y * np.float32(1 - duck)))
                    else:
                        z = np.float32(1 - phase) * x + np.float32(phase) * y
                    expected.append(z)
                assert np.allclose(xs, expected, atol=2e-6, rtol=2e-5), (mode, v, xs, expected)
    assert len(set(outputs)) == 8, "modes or stereo sides collapsed"

    syncs = []
    for length, rate, reverse in ((9216, 48000, 0), (2048, 44100, 1),
                                  (65536, 48000, 0), (512, 48000, 1)):
        n.put(0x257810 + 31 * 20 + 8, length)
        n.put(0x257810 + 31 * 20 + 12, rate)
        render(n, trig=1, sync=256, slot2=31 + reverse * 1024, tune2=0x7C00)
        step_a = n.q31(state + 0x1A8) / (1 << 31)
        step_b = n.q31(state + REC + 0x1A8) / (1 << 31)
        wanted_ratio = (length / 4096) * (48000 / rate)
        bounded_rate = rate * min(4, max(.25, wanted_ratio))
        expected = step_a * bounded_rate / 48000 * (-1 if reverse else 1)
        assert abs(step_b - expected) < 2e-5, (length, rate, step_a, step_b, expected,
                                              n.data(state + REC + 0x184, 84).hex())
        assert n.get(state + REC + 0x184) == rate
        syncs.append({"length": length, "sample_rate": rate, "reverse": reverse,
                      "steps": [step_a, step_b], "rate_clamped": wanted_ratio < .25 or wanted_ratio > 4})
    n.put(0x257810 + 31 * 20 + 8, 4096)
    n.put(0x257810 + 31 * 20 + 12, 48000)
    for reverse in (0, 1):
        render(n, trig=1, mode=768, slot1=7 + reverse * 1024)
        phase = struct.unpack("<f", n.data(state + 1232, 4))[0]
        assert 0 < phase < .1, (reverse, phase, n.q31(state + 0x1B0), n.get(state + 0x188),
                                n.data(state + 0x184, 84).hex())
    render(n, trig=1, mode=768)
    for _ in range(70):
        render(n, mode=768)
    assert struct.unpack("<f", n.data(state + 1232, 4))[0] > .95
    loop_cases = []
    for reverse, near, wrapped in ((0, 2044, 28), (1, 4, 2020)):
        cfg = {"slot2": 31 + reverse * 1024, "mix": 0x7F00}
        render(n, trig=1, **cfg)
        phase = near << 31
        n.put(state + REC + 0x1B0, phase)
        n.put(state + REC + 0x1B4, phase >> 32)
        render(n, **cfg)
        actual = n.q31(state + REC + 0x1B0) / (1 << 31)
        assert actual == wrapped, (reverse, actual)
        n.put(state + REC + 0x1B0, phase)
        n.put(state + REC + 0x1B4, phase >> 32)
        for _ in range(3):
            ended = render(n, release=1, **cfg)
        assert not any(ended) and n.get(state + REC + 0x1B8, 1) == 0
        assert any(render(n, trig=1, **cfg)), "natural note-on did not recover after release"
        loop_cases.append({"reverse": reverse, "held_wrap_phase": actual, "release_end_silent": True})
    render(n, 2, trig=1)
    render(n, trig=1)
    n.call(0x1C3FE5, {"R4": 7, "R8": 0xFFFFFC00, "R12": 0xFFFFFC00}, (0, 0, 0))
    n.call(0x1C3FE5, {"R4": 7, "R8": 0x70000, "R12": 0x74000}, (4096, 48000, 1))
    render(n)
    assert n.get(state) == n.get(state + 0x1B8, 1) == 0
    assert n.get(state + REC + 0x1B8, 1) == 1
    render(n, trig=1)
    for v in (0, 1):
        assert not any(render(n, v, reset=1, mode=512)), "reset left private audio"
        assert not any(render(n, v, mode=512)), "stale resume"
        assert any(render(n, v, trig=1, reset=1)), "reset/trigger recovery"
    assert any(render(n, 2, reset=1)), "reset leaked to another track"
    return {"status": "PASS", "sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
            "v1_reference_sha256": hashlib.sha256(v1.read_bytes()).hexdigest(),
            "v1_differential_cases_stereo_two_blocks": differential,
            "fusion_blend_off_vs_v1": "bit exact", "four_modes_stereo_kernel_vs_native": "PASS",
            "sync_cases": syncs, "morph_first_pass_forward_reverse": "PASS",
            "native_loop_release_cases": loop_cases,
            "native_release_retrigger_aba_reset_other_track": "PASS",
            "circular_stack_and_native_abi": "PASS", "max_instructions_channel_block": max(x[1] for x in n.cost),
            "hardware": "UNTESTED", "cpu_utilization": "not inferred from interpreter instruction counts"}


if __name__ == "__main__":
    ap = argparse.ArgumentParser(__doc__)
    for name in ("image", "plan", "v1", "report"):
        ap.add_argument("--" + name, type=Path, required=True)
    a = ap.parse_args()
    report = run(a.image, a.plan, a.v1)
    a.report.parent.mkdir(parents=True, exist_ok=True)
    a.report.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
