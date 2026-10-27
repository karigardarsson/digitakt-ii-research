"""Differential release-pulse regression using compiled native Fusion voices."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from native_dual_proof import OUT, REC, STACK, Native

from tools.patchkit.port_resolve import sections_of
from tools.sharc_dev import sharc_trace as st
from tools.sharc_dev.sharcldr import LoadedMemory


def symbol(plan, name):
    return next(v for k, v in plan["symbols"].items() if k.endswith(":" + name))


class Voice:
    def __init__(self, image, plan, size):
        self.n = Native(LoadedMemory.from_stream(sections_of(image)[7]), approx_recips=True)
        self.n.load_slots()
        self.state = symbol(json.loads(plan.read_text()), "tf_states.")
        self.size = size

    def render(self, *, voice=0, typ=13, trig=0, release=0, reset=0, reverse=0,
               reverse1=0, mode=0, mix=0x7F00, shape=0x4000, start1=0, start2=0,
               sync=0, tune1=0x4000, tune2=0x4000):
        n = self.n
        track = voice // 2
        n.put(0x255970 + track * 2, typ, 2)
        n.put(0x2558DC + 2 * (1 + track), 60 << 8, 2)
        n.put(0x2558DC + 0x2A, reset, 2)
        for off, raw in enumerate((tune1, sync << 8, mode << 8, 7 + reverse1 * 1024,
                                   start1, start2, tune2, mix, 31 + reverse * 1024, shape)):
            n.put(0x2558DC + 2 * (0x6D + 0x30 * track + off), raw, 2)
        n.put(0x24F0CC + voice, trig, 1)
        if release:
            n.call(0x1C6056, {"R4": 0x2412C8, "R8": voice})
        protected = {name: 0x100 + k for k, name in enumerate(("I8", "I9", "I10", "I11", "I13", "I14", "I15", "M12", "B3"))}
        circular = {"B6": STACK - 0x400, "B7": STACK - 0x400, "L6": 0x800, "L7": 0x800, "MODE1": 0x1011800}
        n.call(0x1C4ECF, {"R4": 0x2412CC + voice * REC, "R8": OUT, "R12": 32,
                          **protected, **circular}, bound=80000)
        for name, value in {**protected, **circular, "I6": STACK + 0x10000, "I7": STACK}.items():
            assert n.runner.state.uregs[st.UREG_CODES[name]].value == value, name
        xs = np.frombuffer(n.data(OUT, 128), dtype="<f4").copy()
        # Real block epilogue removes one-block native flags.
        n.call(0x1C7182, {"I12": 0x24F0AC, "MODE1": 0x11800}, stop=0x1C71AC, bound=500)
        assert np.isfinite(xs).all()
        return xs


def run(image, plan, baseline, oldplan):
    assert hashlib.sha256(baseline.read_bytes()).hexdigest() == "d57bbd459229df8ed69fac91d281da17369b8be57e144acddb05e031b7c04fac"
    cases = []
    for reverse in (0, 1):
        for mode in range(4):
            # Compare identical histories. Release intentionally changes the
            # old/new decimator history; don't call that a held-audio regression.
            new, old = Voice(image, plan, 1260), Voice(baseline, oldplan, 1240)
            for voice in (0, 1):
                cfg = dict(voice=voice, reverse=reverse, mode=mode)
                nx, ox = new.render(trig=1, **cfg), old.render(trig=1, **cfg)
                assert np.array_equal(nx, ox), (cfg, nx.tolist(), ox.tolist())
                assert np.array_equal(new.render(**cfg), old.render(**cfg))
            cfg = dict(reverse=reverse, mode=mode)
            # Move just far enough before the boundary that one release block
            # cannot complete. Without a private latch, the next block loops.
            phase = (100 if reverse else 1950) << 31
            for e in (new, old):
                p = e.state + REC + 0x1B0
                e.n.put(p, phase)
                e.n.put(p + 4, phase >> 32)
                e.render(release=1, **cfg)
            for _ in range(8):
                new.render(**cfg)
                old.render(**cfg)
            assert new.n.get(new.state + REC + 0x1B8, 1) == 0, (reverse, mode, new.n.get(new.state + 1240), new.n.q31(new.state + REC + 0x1B0) / (1 << 31), new.n.data(new.state + REC + 0x184, 84).hex())
            assert old.n.get(old.state + REC + 0x1B8, 1) == 1, "V1 defect no longer reproduced"
            assert any(new.render(trig=1, **cfg))
            assert new.n.get(new.state + 1240) == 0
            cases.append({"reverse": reverse, "mode": mode, "v1_reloops": True, "v2_ends": True})
    # Track reset and repeated reset are idempotent, another track is isolated.
    new.render(voice=2, trig=1)
    other = new.n.data(new.state + 2 * new.size, new.size)
    new.render(reset=1)
    assert not any(new.render(reset=1))
    assert new.n.data(new.state + 2 * new.size, new.size) == other
    assert any(new.render(trig=1))
    v2_cases = []
    for reverse in (0, 1):
        for mode in range(5):
            cfg = dict(typ=14, reverse=reverse, mode=mode)
            new.render(trig=1, **cfg)
            phase = (100 if reverse else 1950) << 31
            p = new.state + REC + 0x1B0
            new.n.put(p, phase)
            new.n.put(p + 4, phase >> 32)
            new.render(release=1, **cfg)
            for _ in range(8):
                new.render(**cfg)
            assert new.n.get(new.state + REC + 0x1B8, 1) == 0, cfg
            assert any(new.render(trig=1, **cfg)), cfg
            v2_cases.append(cfg)
    return {"status": "PASS", "image_sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
            "release_cases": cases, "v2_release_cases": v2_cases,
            "held_pcm_vs_v1": "bit exact, stereo, 4 legacy modes, both directions",
            "circular_stack_and_callee_registers": "PASS",
            "retrigger_reset_other_track": "PASS", "tier": "compiled DSP + actual native release/epilogue; transport scheduling not reproduced"}


if __name__ == "__main__":
    ap = argparse.ArgumentParser(__doc__)
    for name in ("image", "plan", "baseline", "oldplan", "report"):
        ap.add_argument("--" + name, type=Path, required=True)
    a = ap.parse_args()
    r = run(a.image, a.plan, a.baseline, a.oldplan)
    a.report.write_text(json.dumps(r, indent=2) + "\n")
    print(json.dumps(r, indent=2))
