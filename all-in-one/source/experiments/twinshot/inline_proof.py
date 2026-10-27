"""Execute the installed machine through the real inline sampler dispatcher."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import struct
from pathlib import Path

from native_dual_proof import OUT, REC, RET, STACK, Native

from tools.patchkit.port_resolve import sections_of
from tools.sharc_dev.sharcldr import LoadedMemory


def sym(plan, name):
    return next(v for k, v in plan["symbols"].items()
                if k == "machines/twinshot@0.1.0:" + name)


def run(image, planfile):
    plan = json.loads(planfile.read_text())
    n = Native(LoadedMemory.from_stream(sections_of(image)[7]), approx_recips=True)
    n.load_slots()
    states, gens = sym(plan, "tw_states."), sym(plan, "tw_generations.")
    observations = []
    protected = ("I8", "I9", "I10", "I11", "I13", "I14", "I15",
                 "M0", "M8", "M9", "M10", "M11", "M12", "B3", "MODE1")
    sentinels = {name: 0x100 + k for k, name in enumerate(protected)}
    sentinels["MODE1"] = 0x1011800  # native circular-stack configuration
    circular = {"B6": STACK - 0x400, "B7": STACK - 0x400, "L6": 0x800, "L7": 0x800}

    def hw(index, value):
        n.put(0x2558DC + 2 * index, value, 2)

    def render(voice, *, trig=0, release=0, slot1=7, slot2=1055,
               tune1=0x4000, tune2=0x4C00, mix=0x4000, decay=0x7F00, filter=0x4000,
               reset_mask=0):
        track = voice // 2
        n.put(0x255970 + 2 * track, 12, 2)
        hw(1 + track, 60 << 8)
        hw(0x15, reset_mask)
        for offset, value in zip((0, 1, 2, 3, 6, 7, 8, 9),
                                 (tune1, filter, decay, slot1, tune2, mix, slot2, 0x7F00), strict=True):
            hw(0x6D + 0x30 * track + offset, value)
        n.put(0x24F0CC + voice, trig, 1)
        n.put(0x24F0EC + voice, release, 1)
        n.call(0x1C4ECF, {"R4": 0x2412CC + voice * REC, "R8": OUT, "R12": 32,
                         **sentinels, **circular}, bound=50000)
        from tools.sharc_dev import sharc_trace as st
        for name, value in sentinels.items():
            got = n.runner.state.uregs[st.UREG_CODES[name]]
            assert isinstance(got, st.Const) and got.value & 0xFFFFFFFF == value, name
        # Whole native renderer returns through RFRAME to the supplied caller,
        # unlike the isolated guarded C entry which returns before RFRAME.
        for name, value in {**circular, "I6": STACK + 0x10000, "I7": STACK}.items():
            assert n.runner.state.uregs[st.UREG_CODES[name]].value == value, name
        xs = [struct.unpack("<f", struct.pack("<I", n.get(OUT + k * 4)))[0] for k in range(32)]
        assert all(map(math.isfinite, xs))
        return xs

    # The installed entry reads the actual frame/latches, including the right
    # bus and encoded reverse bit, rather than calling C with invented controls.
    stereo = [render(v, trig=1) for v in (0, 1)]
    assert all(any(xs) for xs in stereo) and stereo[0] != stereo[1]
    for v in (0, 1):
        s = states + v * 1228
        assert n.get(s) != n.get(s + REC)
        assert n.get(s + 0x1BB, 1) == 0 and n.get(s + REC + 0x1BB, 1) == 1
        assert abs(n.q31(s + 0x1A8) / (1 << 31) - 0.5) < 1e-6
        assert abs(n.q31(s + REC + 0x1A8) / (1 << 31) + 1.0) < 1e-6
    observations.append("native dispatcher -> frame adapter -> distinct stereo native readers PASS")

    # Controls alter only layer one; mix endpoints isolate each stream.
    dry = render(0, trig=1, mix=0, slot2=31)
    low = render(0, trig=1, mix=0, slot2=31, filter=0)
    high = render(0, trig=1, mix=0, slot2=31, filter=0x7F00)
    assert dry != low != high
    render(0, trig=1, mix=0, decay=0)
    start = struct.unpack("<f", n.data(states + 964, 4))[0]
    for _ in range(8):
        render(0, mix=0, decay=0)
    end = struct.unpack("<f", n.data(states + 964, 4))[0]
    assert 0 < end < start < 1, (start, end)
    observations.append("layer-one filter bypass/LP/HP and decay monotonicity PASS")

    render(2, trig=1)
    for v in (0, 1):
        render(v, trig=1)
        assert not any(render(v, reset_mask=1)), "native track reset did not silence private readers"
        assert n.get(states + v * 1228 + 944) == 0
        assert not any(render(v)), "private cursor resumed after reset without a new trigger"
        assert any(render(v, trig=1)), "new trigger did not recover after reset"
        assert any(render(v, trig=1, reset_mask=1)), "simultaneous reset/trigger failed to rearm"
    assert any(render(2, reset_mask=1)), "reset leaked to another track"
    observations.append("native per-track playback reset: stereo retirement / no stale resume / new trig / other track PASS")

    render(0, trig=1)
    n.call(0x1C3FE5, {"R4": 7, "R8": 0xFFFFFC00, "R12": 0xFFFFFC00}, (0, 0, 0))
    n.call(0x1C3FE5, {"R4": 7, "R8": 0x70000, "R12": 0x74000}, (4096, 48000, 1))
    assert n.get(gens + 7 * 4) == 4
    render(0)
    assert n.get(states) == n.get(states + 0x1B8, 1) == 0
    assert n.get(states + REC + 0x1B8, 1) == 1
    render(0, trig=1)
    assert n.get(states) != 0
    observations.append("installed native loader hooks: ABA invalidation / independent layer / retrigger PASS")
    # Interrupt interleaving model: pause the actual installed renderer in its
    # private native sampler, run the actual loader on a separate interrupt
    # stack, retain its DM writes, then resume the exact suspended CPU state.
    # This tests the software race guard, NOT SEC priority/timing on silicon.
    render(0, trig=1)
    n.put(0x24F0CC, 0, 1)
    n.call(0x1C4ECF, {"R4": 0x2412CC, "R8": OUT, "R12": 32, **sentinels, **circular},
           bound=50000, stop=0x1C4F81)
    suspended = n.runner
    for left, right, args in ((0xFFFFFC00, 0xFFFFFC00, (0, 0, 0)),
                               (0x70000, 0x74000, (4096, 48000, 1))):
        n.call(0x1C3FE5, {"R4": 7, "R8": left, "R12": right}, args, stack=0x2B0000)
    suspended.state.overlay = dict(n.runner.state.overlay)
    n.runner = suspended
    n.runner.breakpoints = frozenset((RET,))
    result = n.runner.run(50000)
    assert result.final_pc_sw == RET, result.halt.to_json()
    assert n.get(states) == n.get(states + 0x1B8, 1) == 0
    assert not any(n.data(states + 972, 128)), "mixed-generation layer output escaped"
    assert n.get(states + REC + 0x1B8, 1) == 1
    observations.append("modeled loader interrupt during native render: affected output discarded PASS")
    return {"status": "PASS", "image_sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
            "observations": observations, "decay_envelope": [start, end],
            "native_entry": "0x1c4ecf", "reciprocal": "approx_recips; not silicon exact",
            "hardware": "UNTESTED", "max_instructions_channel_block": max(x[1] for x in n.cost)}


if __name__ == "__main__":
    ap = argparse.ArgumentParser(__doc__)
    for name in ("image", "plan", "report"):
        ap.add_argument("--" + name, type=Path, required=True)
    a = ap.parse_args()
    report = run(a.image, a.plan)
    a.report.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
