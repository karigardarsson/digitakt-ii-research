"""Matched V1/V2 shared-C auditions; generated sources, not native/hardware PCM."""
import argparse
import ctypes
import hashlib
import json
import subprocess
from pathlib import Path

import numpy as np
from audio_proof import SR, playback, source, wav

HERE = Path(__file__).resolve().parent

class Key(ctypes.Structure):
    _fields_ = [("peak", ctypes.c_float), ("hold", ctypes.c_int)]


def library(header, path):
    subprocess.run(["cc", "-x", "c", "-O2", "-shared", "-fPIC", str(header), "-o", str(path)], check=True)
    lib = ctypes.CDLL(str(path.resolve()))
    lib.fusion_process.argtypes = [ctypes.c_void_p] * 3 + [ctypes.c_int, ctypes.c_float, ctypes.c_float] + [ctypes.c_void_p] * 2
    if hasattr(lib, "fusion_process_v2"):
        lib.fusion_process_v2.argtypes = [ctypes.c_void_p] * 3 + [ctypes.c_int] + [ctypes.c_float] * 3 + [ctypes.c_void_p] * 2
    if hasattr(lib, "fusion_process_v3"):
        lib.fusion_process_v3.argtypes = [ctypes.c_void_p] * 3 + [ctypes.c_int] + [ctypes.c_float] * 3 + [ctypes.c_void_p] * 3
    return lib


def render(lib, a, b, *, version=2, mode=0, mix=.5, shape=0, span=SR):
    output = np.zeros_like(a)
    ducks = [ctypes.c_float(0), ctypes.c_float(0)]
    dcs = [ctypes.c_float(0), ctypes.c_float(0)]
    keys = [Key(), Key()]
    for p in range(0, len(a), 32):
        sweep = min(1, (p + 32) / (SR * (.01 + 8 * shape * shape))) if shape > 0 else 0
        for channel in (0, 1):
            aa, bb = [np.ascontiguousarray(x[p:p + 32, channel]) for x in (a, b)]
            y = np.zeros(32, np.float32)
            args = (aa.ctypes.data, bb.ctypes.data, y.ctypes.data, mode, mix)
            state = (ctypes.byref(ducks[channel]), ctypes.byref(dcs[channel]))
            if version == 1:
                lib.fusion_process(*args, min(1, (p + 32) / span), *state)
            elif hasattr(lib, "fusion_process_v3"):
                lib.fusion_process_v3(*args, shape, sweep, *state, ctypes.byref(keys[channel]))
            else:
                lib.fusion_process_v2(*args, shape, sweep, *state)
            output[p:p + 32, channel] = y
    assert np.isfinite(output).all() and np.max(np.abs(output)) <= 1.00001
    return output


def run(out):
    out.mkdir(parents=True, exist_ok=True)
    old = library(HERE.parent / "twinshot_fusion/fusion_process.h", out / "v1.so")
    new = library(HERE / "fusion_process.h", out / "v2.so")
    cases = []
    for name, ka, kb, da, db in (("piano_reverse", "piano", "piano", 1.5, 1.5),
                                 ("vocal_texture", "vowel", "air", 1.0, 2.0),
                                 ("transient_pad", "kick", "air", .3, 2.0)):
        x, y = source(ka, da), source(kb, db)
        if name == "piano_reverse":
            y = y[::-1].copy()
        size = 2 * SR
        a, b = playback(x, size), playback(y, size, loop=True)
        configs = [
            ("v1_blend", old, dict(version=1)),
            ("v2_blend", new, {}),
            ("v1_ring_full", old, dict(version=1, mode=1, mix=1)),
            ("v2_ring_clean", new, dict(mode=1, mix=0, shape=.5)),
            ("v2_ring_half", new, dict(mode=1, mix=.5, shape=.5)),
            ("v2_ring_full", new, dict(mode=1, mix=1, shape=.5)),
            ("v1_duck", old, dict(version=1, mode=2, mix=1)),
            ("v2_duck_fast", new, dict(mode=2, mix=1, shape=.1)),
            ("v2_duck_slow", new, dict(mode=2, mix=1, shape=.8)),
            ("v2_gate", new, dict(mode=4, mix=1, shape=.1)),
            ("v1_auto_morph", old, dict(version=1, mode=3, mix=1)),
            ("v2_morph_a", new, dict(mode=3, mix=0)),
            ("v2_morph_mid", new, dict(mode=3, mix=.5)),
            ("v2_morph_b", new, dict(mode=3, mix=1)),
            ("v2_morph_sweep", new, dict(mode=3, mix=0, shape=.35)),
        ]
        frames, entries = [], []
        for label, lib, cfg in configs:
            z = render(lib, a, b, span=len(x), **cfg)
            file = out / f"{name}_{label}.wav"
            wav(file, z)
            entries.append({"file": file.name, "sha256": hashlib.sha256(file.read_bytes()).hexdigest(),
                            "peak": float(np.max(np.abs(z))), "config": cfg})
            frames += [z, np.zeros((SR // 4, 2), np.float32)]
        assert np.array_equal(render(old, a, b, version=1), render(new, a, b))
        rate = min(4, max(.25, len(y) / len(x)))
        sync_old = render(old, a, playback(y, size, rate, loop=True), version=1)
        sync_new = render(new, a, playback(y, size, rate * 2, loop=True))
        for label, z in (("v1_vari_tuneb_plus12_ignored", sync_old),
                          ("v2_rela_tuneb_plus12", sync_new)):
            file = out / f"{name}_{label}.wav"
            wav(file, z)
            entries.append({"file": file.name, "sha256": hashlib.sha256(file.read_bytes()).hexdigest(),
                            "preview_rate": rate if "v1" in label else rate * 2})
            frames += [z, np.zeros((SR // 4, 2), np.float32)]
        assert not np.array_equal(sync_old, sync_new)
        # Same sample, independent opposite-direction offset: 35% from the
        # reverse edge is approximately 65% through the original source.
        cropped = y[round(.35 * (len(y) - 141)):]
        starts = render(new, a, playback(cropped, size, loop=True))
        wav(out / f"{name}_v2_start_b_offset35.wav", starts)
        reel = out / f"{name}_V1_V2_reel.wav"
        wav(reel, np.concatenate(frames))
        cases.append({"case": name, "reel": reel.name, "order": entries})
    # Deterministic sidechain depth/recovery metric, not a listening judgment.
    size = SR
    t = np.arange(size) / SR
    a = np.stack([np.where(t < .1, .1, 0)] * 2, axis=1).astype(np.float32)
    b = np.stack([.4 * np.sin(2 * np.pi * 220 * t)] * 2, axis=1).astype(np.float32)
    duck = render(new, a, b, mode=2, mix=1, shape=.1)
    slow = render(new, a, b, mode=2, mix=1, shape=.8)
    gate = render(new, a, b, mode=4, mix=1, shape=.1)
    def rms(x):
        return float(np.sqrt(np.mean(x * x)))
    suppression = rms(duck[1000:4000]) / rms(b[1000:4000])
    assert suppression < .001, suppression
    # Repair3 calibrates recovery to closure, not the old 4.6x time constant.
    assert rms(duck[10000:20000]) > rms(slow[10000:20000]) + .1 * rms(b[10000:20000])
    assert rms(gate[1000:4000]) > .9 * rms(b[1000:4000])
    report = {"status": "PASS", "cases": cases, "full_duck_rms_ratio_under_A": suppression,
              "current_kernel_sha256":hashlib.sha256((HERE/"fusion_process.h").read_bytes()).hexdigest(),
              "recovery_and_inverse_gate": "PASS", "normalization": "none per render; common source levels",
              "evidence": "original frozen V1 C vs V2 shared C; generated stereo sources + host interpolation, NOT hardware or native PCM"}
    (out / "audio-report.json").write_text(json.dumps(report, indent=2) + "\n")
    print("V1/V2 matched audio comparisons, depth, recovery and inverse gate PASS", flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(__doc__)
    ap.add_argument("--out", type=Path, required=True)
    run(ap.parse_args().out)
