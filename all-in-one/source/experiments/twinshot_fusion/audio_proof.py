"""Reproducible music-first host previews; shared C kernel, NOT hardware audio."""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import math
import subprocess
import wave
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
SR = 48000


def source(kind, seconds):
    t = np.arange(round(seconds * SR)) / SR
    if kind == "kick":
        x = np.sin(2 * np.pi * (48 * t + 22 * (1 - np.exp(-t * 45)))) * np.exp(-t * 15)
        x += 0.12 * np.random.default_rng(7).normal(size=len(t)) * np.exp(-t * 100)
    elif kind == "metal":
        x = sum(np.sin(2 * np.pi * f * t) / (k + 2) for k, f in enumerate((173, 391, 719, 1121))) * np.exp(-t * 3)
    elif kind == "vowel":
        x = sum(np.sin(2 * np.pi * 110 * k * t) * (0.6 * np.exp(-((110 * k - 700) / 220) ** 2)
                 + 0.4 * np.exp(-((110 * k - 1400) / 330) ** 2)) for k in range(1, 24))
        x *= np.sin(np.pi * t / seconds) ** 2
    elif kind == "piano":
        x = sum(np.sin(2 * np.pi * 261.63 * k * (1 + k * k * 0.0001) * t) / k ** 1.5 for k in range(1, 10))
        x *= np.exp(-t * 8) * (1 - np.exp(-t * 1000))
    elif kind == "air":
        noise = np.random.default_rng(11).normal(size=len(t))
        x = np.convolve(noise, np.ones(12) / 12, mode="same") * np.sin(np.pi * t / seconds) ** 2
        x += 0.12 * np.sin(2 * np.pi * 67 * t)
    else:
        x = np.sin(2 * np.pi * (331 * t + 110 * t * t)) * np.exp(-t * 12)
        x *= (np.sin(2 * np.pi * 37 * t) > -0.1)
    x *= 0.7 / max(1e-9, np.max(np.abs(x)))
    # Different right-side phase/envelope, without mixing the channels.
    right = np.roll(x, 13) * (0.88 + 0.12 * np.cos(2 * np.pi * t))
    return np.ascontiguousarray(np.stack((x, right), axis=1), dtype=np.float32)


def playback(x, size, rate=1, loop=False):
    pos = np.arange(size) * rate
    if loop:
        pos %= len(x)
    return np.stack([np.interp(pos, np.arange(len(x)), x[:, ch], left=0, right=0) for ch in range(2)], axis=1).astype(np.float32)


def process(lib, a, b, mode, mix, span):
    output = np.zeros_like(a)
    ducks = [ctypes.c_float(0), ctypes.c_float(0)]
    dc = [ctypes.c_float(0), ctypes.c_float(0)]
    for p in range(0, len(a), 32):
        for ch in (0, 1):
            aa, bb = (np.ascontiguousarray(x[p:p + 32, ch]) for x in (a, b))
            out = np.zeros(32, np.float32)
            lib.fusion_process(aa.ctypes.data, bb.ctypes.data, out.ctypes.data, mode, mix,
                               min(1, (p + 32) / span), ctypes.byref(ducks[ch]), ctypes.byref(dc[ch]))
            output[p:p + 32, ch] = out
    return output


def wav(path, audio):
    assert np.isfinite(audio).all() and np.max(np.abs(audio)) <= 1.00001
    with wave.open(str(path), "wb") as f:
        f.setparams((2, 2, SR, 0, "NONE", "not compressed"))
        f.writeframes(np.round(np.clip(audio, -1, 1) * 32767).astype("<i2").tobytes())


def main():
    ap = argparse.ArgumentParser(__doc__)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    so = a.out / "fusion-preview.so"
    subprocess.run(["cc", "-x", "c", "-std=c99", "-O2", "-shared", "-fPIC",
                    str(HERE / "fusion_process.h"), "-o", str(so)], check=True)
    lib = ctypes.CDLL(str(so.resolve()))
    lib.fusion_process.argtypes = [ctypes.c_void_p] * 3 + [ctypes.c_int, ctypes.c_float, ctypes.c_float, ctypes.c_void_p, ctypes.c_void_p]
    cases = []
    for name, first, second, duration_a, duration_b in (
        ("01_percussion", "kick", "metal", 0.4, 0.9),
        ("02_vocal_piano", "vowel", "piano", 0.9, 0.4),
        ("03_texture", "air", "grain", 0.9, 0.4),
    ):
        x, y = source(first, duration_a), source(second, duration_b)
        size = 48000
        aa = playback(x, size)
        bb = playback(y, size, loop=True)
        ss = playback(y, size, len(y) / len(x), loop=True)
        variants = {"blend": (bb, 0, 0.5), "sync": (ss, 0, 0.5),
                    "ring": (bb, 1, 1), "sync_ring": (ss, 1, 1),
                    "duck": (bb, 2, 1), "morph": (ss, 3, 1)}
        renders = []
        base = None
        for label, (body, mode, mix) in variants.items():
            audio = process(lib, aa, body, mode, mix, len(x))
            if base is None:
                base = audio.copy()
            difference = float(np.sqrt(np.mean((audio - base) ** 2)))
            if label != "blend":
                assert difference > 0.005, (name, label, difference)
            filename = f"{name}_{label}.wav"
            wav(a.out / filename, audio)
            renders.append({"file": filename, "peak": float(np.max(np.abs(audio))),
                            "rms_difference_from_blend": difference,
                            "sha256": hashlib.sha256((a.out / filename).read_bytes()).hexdigest()})
        # Audition reel: same phrase/level, no separate normalization per mode.
        reel = np.concatenate([np.concatenate((process(lib, aa, body, mode, mix, len(x)),
                                                np.zeros((SR // 4, 2), np.float32)))
                               for body, mode, mix in variants.values()])
        wav(a.out / f"{name}_comparison.wav", reel)
        cases.append({"pair": name, "duration_seconds": [duration_a, duration_b],
                      "reel_order": list(variants), "renders": renders})
    # Known ring products generate sum/difference bands, not just another mix.
    t = np.arange(48000) / SR
    aa = np.repeat((0.6 * np.sin(2 * math.pi * 270 * t))[:, None], 2, axis=1).astype(np.float32)
    bb = np.repeat((0.6 * np.sin(2 * math.pi * 173 * t))[:, None], 2, axis=1).astype(np.float32)
    ring = process(lib, aa, bb, 1, 1, SR)
    spectrum = np.abs(np.fft.rfft(ring[:, 0]))
    assert min(spectrum[97], spectrum[443]) > 1000
    dc_test = process(lib, np.ones((SR, 2), np.float32), np.ones((SR, 2), np.float32), 1, 1, SR)
    assert np.max(np.abs(dc_test[-1024:])) < 0.0001, "correlated inputs retained DC"
    # Worst correlated normalized inputs cannot exceed unity in any mode.
    for sign in (-1, 1):
        for mode in range(4):
            q = process(lib, np.ones((32, 2), np.float32), np.full((32, 2), sign, np.float32), mode, 1, 32)
            assert np.max(np.abs(q)) <= 1
    report = {"status": "PASS", "evidence": "host float32 C kernel, linear-resampled generated inputs; not native firmware or listening proof",
              "ring_sum_difference_hz": [97, 443], "headroom_unit_inputs": "PASS",
              "pairs": cases, "musical_value": "requires Kari audition; no listening judgment claimed"}
    (a.out / "audio-report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"status": "PASS", "pairs": len(cases), "files": 21,
                      "order": list(variants), "hardware": "UNTESTED"}))


if __name__ == "__main__":
    main()
