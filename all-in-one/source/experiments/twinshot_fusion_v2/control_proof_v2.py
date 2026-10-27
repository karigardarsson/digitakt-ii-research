"""Installed native p-lock delivery and Sound-v3 payload conversion gates."""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
from pathlib import Path

from effective_selector_proof import call, checked
from native_event_proof import run as event_run

from tools.patchkit import pk_cfcall, port_resolve
from tools.patchkit import pk_integrate_dv_zoom as dv


def sym(plan, name):
    return next(v for k, v in plan["symbols"].items() if k.endswith(":" + name))


def run(image, planfile, state, tools, machine_type=14):
    sha = hashlib.sha256(image.read_bytes()).hexdigest()
    plan = json.loads(planfile.read_text())
    events = event_run(image, state, tools, expected_sha=sha, selectors=(28, 33),
                       machine_type=machine_type, smoother=sym(plan, "twinshot_smoother"))
    main = port_resolve.sections_of(image)[3]
    pokes = {0x40000400: main[:dv.STOCK_MAIN_END - 0x40000400]}
    for src, size, dest, bss in dv.parsed_segments(main)[0]:
        pokes[dest] = dv.at(main, src + 12, size) + bytes(bss)
    a, b, c = 0x47000000, 0x47001000, 0x47002000
    pokes.update({a: bytes(0x450), b: bytes(0x460), c: bytes(0x450)})
    pk_cfcall._built = True
    m = pk_cfcall.CfMachine("Digitakt_II_OS1.17.syx", state=state, timeout=120)
    # Same native converters as the prior WAVE test; execute the integrated
    # type bounds and descriptor code, not a host pack/unpack approximation.
    results = checked(m.run(pokes, [call(0x400DBA94, [b, a, 1]),
                                    call(0x400DB3B2, [c, b], [(c, 0x450)])]))
    canonical = results[-1].reads[0]
    cases = []
    for slot1, slot2 in ((0, 0), (7, 31), (1031, 1055), (1023, 2047), (2047, 1023)):
        source = bytearray(canonical)
        source[0xA2] = machine_type
        values = {25: 0x4101, 26: 0x2000, 27: 0x6000, 28: slot1,
                  31: 0x3807, 32: 0x4200, 33: slot2, 34: 0x7F00,
                  5: 15 << 8, 13: 10 << 8, 21: 9 << 8}
        if machine_type in (13, 14):
            values.update({26: 0x100, 27: 0x300})
        if machine_type == 14:
            values.update({26: 0x200, 27: 0x400, 29: 0x5400, 30: 0x2000, 34: 0x4300})
        for idx, value in values.items():
            struct.pack_into(">H", source, 0x14 + 2 * idx, value)
        result = checked(m.run({**pokes, a: bytes(source)}, [
            call(0x400DBA94, [b, a, 1], [(b, 0x455)]),
            call(0x400DB3B2, [c, b], [(c, 0x450)])]))
        restored = result[-1].reads[0]
        assert restored == source, (slot1, slot2, [(i, x, y) for i, (x, y) in enumerate(zip(source, restored, strict=True)) if x != y][:20])
        cases.append({"sample1": slot1, "sample2": slot2, "full_sound_bytes_equal": True})
    inverse = struct.unpack_from(">77I", main, 0x40213B64 - 0x40000400)
    locks = bytearray(b"\xff" * (80 * 258))
    indices = (25, 26, 27, 28, 29, 30, 31, 32, 33, 34)
    for lane, index in enumerate(indices):
        offset = lane * 258
        locks[offset:offset + 2] = bytes((inverse.index(index), 3))
        for step, value in ((0, 2047 if index in (28, 33) else 0x4101),
                            (1, 1031 if index in (28, 33) else 0x3807), (127, 7)):
            struct.pack_into(">h", locks, offset + 2 + 2 * step, value)
    src, live, saved = 0x47004000, 0x4700A000, 0x47010000
    lock_results = checked(m.run({**pokes, src: locks, live: bytes(0x5200), saved: bytes(80 * 258)}, [
        call(0x400DC60A, [live, src]),
        call(0x400DC804, [saved, live], [(saved, 80 * 258)])]))
    stored = lock_results[-1].reads[0]
    assert stored[:10 * 258] == locks[:10 * 258], "native p-lock conversion changed active lanes"
    assert all(stored[i * 258:i * 258 + 2] == b"\xff\xff" for i in range(10, 80))
    return {"status": "PASS", "image_sha256": sha, "native_pattern_events": events,
            "sound_v3": cases, "native_pattern_lock_roundtrip": {"indices": indices,
                "all_128_steps_in_10_lanes": "bit exact", "unused_lanes": "preserved"},
            "file_media_project_power_cycle": "hardware untested; converters are not full file I/O"}


if __name__ == "__main__":
    ap = argparse.ArgumentParser(__doc__)
    for name in ("image", "plan", "state", "tools", "report"):
        ap.add_argument("--" + name, type=Path, required=True)
    a = ap.parse_args()
    report = run(a.image, a.plan, a.state, a.tools)
    a.report.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
