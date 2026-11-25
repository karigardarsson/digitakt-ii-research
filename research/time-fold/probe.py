"""Check the Time Fold host engine at 48 kHz."""
import argparse
import ctypes as ct
import json
from pathlib import Path

import numpy as np

RATE = 48_000
BLOCK = 32


class Controls(ct.Structure):
    _fields_ = [(name, ct.c_float) for name in
                ("fold", "nest", "memory", "clock", "drift")]


class Spectrum(ct.Structure):
    _fields_ = [("sine", ct.c_float * 8), ("cosine", ct.c_float * 8)]


class Voice(ct.Structure):
    _fields_ = (
        [(name, ct.c_float) for name in ("phase", "increment", "clock", "subclock",
         "instability", "drift", "accent", "depth", "nest", "memory", "pivot", "subpivot")]
        + [("maps", (ct.c_float * 33) * 2)]
        + [(name, Spectrum) for name in ("spectrum", "target", "goal", "motion")]
        + [(name, ct.c_uint) for name in ("page", "stage", "inverse", "valid",
                                         "initial_delay", "has_history")]
    )


def load(library):
    lib = ct.CDLL(str(library.resolve()))
    lib.fold_reset.argtypes = [ct.POINTER(Voice)]
    lib.fold_reset.restype = None
    lib.fold_render.argtypes = [ct.POINTER(Voice), ct.POINTER(Controls),
                                ct.c_float, ct.POINTER(ct.c_float)]
    lib.fold_render.restype = None
    return lib


def render(lib, seconds, hz, control, voice=None, observe=None):
    if voice is None:
        voice = Voice()
    blocks = round(seconds * RATE / BLOCK)
    signal = np.empty(blocks * BLOCK, dtype=np.float32)
    out = (ct.c_float * BLOCK)()
    for b in range(blocks):
        time = b * BLOCK / RATE
        lib.fold_render(voice, Controls(*control(time)), hz(time) / RATE, out)
        signal[b * BLOCK:(b + 1) * BLOCK] = out
        if observe is not None:
            observe(voice)
    return signal


def rms(signal):
    return float(np.sqrt(np.mean(signal.astype(np.float64) ** 2)))


def probe(lib):
    constant = lambda value: lambda _: value
    f0 = 187.5  # Integer cycles in each 512-sample map interval.
    neutral = render(lib, 1, constant(f0), constant((0, 0, 0, 0, 0)))
    reference = .65 * np.sin(2 * np.pi * f0 * np.arange(len(neutral)) / RATE)
    neutral_error = float(np.max(np.abs(neutral - reference)))
    assert neutral_error < 2e-5

    def history(memory):
        close = constant((.70, .96, memory, .15, 0))
        a, b = Voice(), Voice()
        render(lib, .192, constant(f0), constant((.92, .96, memory, .15, 0)), a)
        render(lib, .192, constant(f0), constant((.12, .96, memory, .15, 0)), b)
        displaced = render(lib, .256, constant(f0), close, a)
        original = render(lib, .256, constant(f0), close, b)
        assert a.phase == b.phase
        return {"early_rms": rms((displaced - original)[1024:3072]),
                "late_rms": rms((displaced - original)[8192:12288])}

    feedback, control = history(1), history(0)
    assert feedback["early_rms"] > .005
    assert control["early_rms"] < 1e-7
    assert 0 < feedback["late_rms"] < feedback["early_rms"]

    low, high, inverse_error = float("inf"), 0.0, 0.0

    def observe(voice):
        nonlocal low, high, inverse_error
        if voice.stage:
            return
        phase = np.linspace(0, 1, 33)
        mapping = np.array(voice.maps[voice.page])
        slope = np.diff(mapping) * 32
        assert mapping[0] == 0 and mapping[-1] == 1 and slope.min() > 0
        low, high = min(low, slope.min()), max(high, slope.max())
        test = np.linspace(0, 1, 129)
        reverse = np.interp(test, mapping, phase)
        inverse_error = max(inverse_error, np.max(np.abs(np.interp(reverse, phase, mapping) - test)))

    rng = np.random.default_rng(117)
    controls = rng.random((128, 5)) * [2, 1, 1, 1, 1] - [1, 0, 0, 0, 0]
    sweep = render(lib, 5.5, constant(1273), lambda t: controls[int(t * 91) % 128],
                   observe=observe)
    assert np.isfinite(sweep).all() and np.max(np.abs(sweep)) < .91
    assert inverse_error < 1e-12

    voices = Voice(), Voice()
    last, clock_steps = [], []

    def clocks(voice):
        if voice.stage == 1:
            point = voice.clock, voice.subclock
            if last:
                a, b = last[-1]
                clock_steps.append(((point[0] - a) % 1, (point[1] - b) % 1))
            last.append(point)

    stable = render(lib, 1.75, constant(f0), constant((1, .75, .80, 1, 0)), voices[0], clocks)
    unstable = render(lib, 1.75, constant(f0), constant((1, .75, .80, 1, 1)), voices[1])
    ratios = [b / a for a, b in clock_steps]
    assert max(abs(r - 2.5) for r in ratios) < 1e-4 and voices[0].phase == voices[1].phase
    divergence = rms((stable - unstable)[RATE // 2:])
    assert divergence > .02 and np.isfinite(unstable).all()

    def spectral_test(hz):
        audio = render(lib, 2, constant(hz), constant((1, 1, .78, 0, 0)))
        window = np.hanning(RATE)
        mag = np.abs(np.fft.rfft(audio[-RATE:] * window))
        active = np.nonzero(mag > mag.max() * .01)[0]
        harmonics = [int(f) for f in np.round(active / hz) if f > 0]
        assert hz - 2 <= np.argmax(mag) <= hz + 2
        assert all(min(abs(f - n * hz) for n in range(1, 9)) <= 4 for f in active)
        return {"fundamental_hz": int(np.argmax(mag)),
                "harmonics_above_minus_40_db": sorted(set(harmonics)),
                "peak": float(np.max(np.abs(audio)))}

    return {"neutral_peak_error": neutral_error,
            "feedback_same_controls": feedback, "memoryless_same_controls": control,
            "phase_map_slope_min_max": [float(low), float(high)],
            "polyline_inverse_error": float(inverse_error),
            "automated_peak": float(np.max(np.abs(sweep))),
            "observed_unperturbed_clock_ratio_min_max": [min(ratios), max(ratios)],
            "drift_difference_rms_at_matched_carrier_phase": divergence,
            "low_register": spectral_test(250), "high_register": spectral_test(7200)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("library", type=Path, help="Compiled host engine shared library")
    args = parser.parse_args()
    print(json.dumps(probe(load(args.library)), indent=2))
