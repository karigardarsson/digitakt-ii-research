"""Compiled V2 native playback vs shared-C processing and explicit controls."""
import argparse
import ctypes
import hashlib
import json
import struct
import subprocess
from pathlib import Path

import numpy as np
from native_dual_proof import REC
from voice_proof import Voice

HERE = Path(__file__).resolve().parent

class Key(ctypes.Structure):
    _fields_ = [("peak", ctypes.c_float), ("hold", ctypes.c_int)]


def f32(n, p):
    return struct.unpack("<f", n.data(p, 4))[0]


def run(image, plan, out):
    out.mkdir(parents=True, exist_ok=True)
    so = out / "kernel.so"
    subprocess.run(["cc", "-x", "c", "-std=c99", "-O2", "-shared", "-fPIC",
                    str(HERE / "fusion_process.h"), "-o", str(so)], check=True)
    lib = ctypes.CDLL(str(so.resolve()))
    lib.fusion_process_v3.argtypes = [ctypes.c_void_p] * 3 + [ctypes.c_int] + [ctypes.c_float] * 3 + [ctypes.c_void_p] * 3
    v = Voice(image, plan, 1260)
    kernels = []
    for mode in range(5):
        for mix in (0, 0x2000, 0x4000, 0x7F00):
            for side in (0, 1):
                xs = v.render(typ=14, voice=side, trig=1, mode=mode, mix=mix, shape=0)
                p = v.state + side * 1260
                a, b = [np.frombuffer(v.n.data(p + offset, 128), dtype="<f4").copy() for offset in (972, 1100)]
                expected = np.zeros(32, np.float32)
                duck, dc = ctypes.c_float(0), ctypes.c_float(0)
                key = Key()
                lib.fusion_process_v3(a.ctypes.data, b.ctypes.data, expected.ctypes.data,
                                      mode, mix / 32512, 0, 0, ctypes.byref(duck), ctypes.byref(dc), ctypes.byref(key))
                assert np.allclose(xs, expected, atol=2e-6, rtol=2e-5), (mode, mix, side, xs, expected)
                assert np.max(np.abs(xs)) <= 1.00001
                if mode == 3 and mix in (0, 0x7F00):
                    assert np.array_equal(xs, a if mix == 0 else b)
                kernels.append((mode, mix, side))
    # Native start windows change only the chosen layer, not a guessed host
    # interpolation cursor. At zero offset BLEND remains the legacy bypass.
    starts = []
    for reverse in (0, 1):
        for control in (0, 0x4000, 0x7F00):
            v.render(typ=14, trig=1, reverse=reverse, start2=control)
            bp = v.n.q31(v.state + REC + 0x1B0)
            ap = v.n.q31(v.state + 0x1B0)
            starts.append({"reverse_b": reverse, "start_b_raw": control,
                           "a_phase_q31": ap, "b_phase_q31": bp})
            assert v.n.get(v.state + REC + 0x1B8, 1) == 1
    assert len({x["a_phase_q31"] for x in starts}) == 1, starts
    for reverse in (0, 1):
        assert len({x["b_phase_q31"] for x in starts if x["reverse_b"] == reverse}) == 3, starts
    v.render(typ=14, trig=1, start1=0x5000)
    assert v.n.q31(v.state + 0x1B0) != starts[0]["a_phase_q31"]
    # Relative tuning remains effective under RELA, unlike legacy VARI.
    syncs = []
    for sync in (0, 1, 2):
        steps = []
        for tune in (0x4000, 0x4C00):
            v.render(typ=14, trig=1, sync=sync, tune2=tune)
            steps.append(v.n.q31(v.state + REC + 0x1A8))
        if sync == 1:
            assert steps[0] == steps[1]
        else:
            assert abs(steps[1] / steps[0] - 2) < 2e-5, (sync, steps)
        syncs.append({"sync": sync, "tune_b_steps": steps})
    cropped_syncs = []
    for reverse in (0, 1):
        for sa, sb in ((0,0x4000),(0x5000,0),(0x7F00,0x7F00)):
            v.render(typ=14,trig=1,sync=1,start1=sa,start2=sb,reverse=reverse)
            frames_a, frames_b = [v.n.get(v.state + offset + 0x188) for offset in (0,REC)]
            span_a = frames_a - (sa/32512)*(frames_a-141)
            span_b = frames_b - (sb/32512)*(frames_b-141)
            target = min(4,max(.25,span_b/span_a))
            ratio = abs(v.n.q31(v.state+REC+0x1A8)/v.n.q31(v.state+0x1A8))
            assert abs(ratio-target)<3e-5,(sa,sb,ratio,target)
            assert v.n.get(v.state+REC+0x184)==48000
            cropped_syncs.append({"reverse_b":reverse,"starts":[sa,sb],"ratio":ratio,"expected":target})
    # Manual/auto morph, bounded voice cost. Shape zero really is stationary.
    for shape in (0, 0x100, 0x7F00):
        v.render(typ=14, trig=1, mode=3, mix=0x2000, shape=shape)
        before = f32(v.n, v.state + 1244)
        v.render(typ=14, mode=3, mix=0x2000, shape=shape)
        after = f32(v.n, v.state + 1244)
        assert after == 0 if shape == 0 else after > before
    report = {"status": "PASS", "sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
              "native_vs_shared_kernel_cases": len(kernels), "native_start_windows": starts,
              "sync_relative_tune": syncs, "cropped_duration_sync":cropped_syncs,"manual_auto_morph": "PASS",
              "max_interpreted_instructions_per_call": max(cost for _, cost in v.n.cost),
              "silicon_timing_or_hardware": "UNTESTED"}
    (out / "dsp-proof.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(__doc__)
    for name in ("image", "plan", "out"):
        ap.add_argument("--" + name, type=Path, required=True)
    a = ap.parse_args()
    run(a.image, a.plan, a.out)
