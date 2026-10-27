"""Execute the actual vector-191 smoothing, split, LFO and reconstruction tail."""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
from pathlib import Path

from effective_selector_proof import call
from native_event_proof import caller

from tools.emulator.snapread import Snapshot
from tools.patchkit import pk_cfcall, port_resolve
from tools.patchkit import pk_integrate_dv_zoom as dv


def run(image, state, tools, stop=0x4002E9A0, machine_type=14, destination=29):
    main = port_resolve.sections_of(image)[3]
    code, symbols = caller(tools)
    pokes = {0x40000400: main[:dv.STOCK_MAIN_END - 0x40000400], 0x47F20000: code,
             stop: struct.pack(">HI", 0x4EF9, symbols["tw_frame_fixture_done"]),
             0x47DB461C: bytes(16 * 12), 0x409664F8: bytes(4)}
    for src, size, dest, bss in dv.parsed_segments(main)[0]:
        pokes[dest] = dv.at(main, src + 12, size) + bytes(bss)
    # One all-track raw/control fixture, not fake effective writes mid-vector.
    # Native Sound copy and smoothing perform all SRAM initialization.
    # MAIN replacement resets the native LFO function tables' initialized-data
    # words; rebuild them through the real startup initializers, as the frozen
    # WAVE fixture does, rather than trusting the cached pre-overlay pointers.
    calls, expected = [call(0x401391EC, []), call(0x40139240, [])], []
    snap = Snapshot(str(state))
    template = snap.read(snap.u32(0x80004704) + 0x34, 0x450)
    for t in range(16):
        s = 0x47000000 + t * 0x500
        source = bytearray(template)
        source[0xA2] = machine_type if t == 3 else 0
        for idx, value in {25: 0x4000, 26: 0x4000, 27: 0x7F00,
                           28: 7, 29: 0x2000, 30: 0x5000, 31: 0x4000, 32: 0x4000, 33: 31, 34: 0x4000}.items():
            struct.pack_into(">h", source, 0x14 + 2 * idx, value)
        if t == 3:
            if machine_type in (13, 14):
                for idx, value in {26: 0x100, 27: 0x300}.items():
                    struct.pack_into(">h", source, 0x14 + 2 * idx, value)
            for idx, value in {1: 0x5000, 2: 0x0B00, 3: 0x4000, 4: destination << 8,
                               5: 1 << 8, 6: 0, 7: 0, 8: 0x6000}.items():
                struct.pack_into(">h", source, 0x14 + 2 * idx, value)
        pokes[s] = source
        calls.append(call(0x4002D438, [s, t]))
    pokes[0x4700F000] = struct.pack(">I", 0x40000000)
    calls.append(call(0x40136388, [0x44E66E78 + 3 * 0x78, 0x4700F000, 4]))
    prefix_count = len(calls)
    for k, (typ, slot) in enumerate([(machine_type, s) for s in (0, 7, 1023, 1024, 1031, 2047)] + [(0, 1031)]):
        src = 0x47010000 + k * 4
        pokes[src] = bytes((typ, 0)) + struct.pack(">H", slot)
        calls.extend([call(0x40136388, [0x80003CD0 + 3 * 154, src, 1]),
                      call(0x40136388, [0x80003340 + 0x5A + 3 * 142, src + 2, 2]),
                      call(symbols["tw_frame_fixture"], [],
                           [(0x80005B50 + 3 * 142 + 0x5A, 14),
                            (0x80005B50 + 3 * 142, 142),
                            (0x44E66E78 + 3 * 0x78, 40)], bound=1000000)])
        expected.append((typ, slot))
    pk_cfcall._built = True
    machine = pk_cfcall.CfMachine("Digitakt_II_OS1.17.syx", state=state, timeout=120)
    all_results = machine.run(pokes, calls)
    for index, result in enumerate(all_results):
        assert result.ok and not result.clobbered, (index, calls[index], hex(result.pc),
                                                    result.error, result.clobbered,
                                                    [hex(x) for x in result.d], [hex(x) for x in result.a])
    results = all_results[prefix_count:]
    observed = []
    for k, (typ, slot) in enumerate(expected):
        data = results[3 * k + 2].reads[0]
        actual = struct.unpack_from(">H", data)[0]
        if typ == machine_type:
            assert actual == slot, (typ, slot, actual, data.hex())
            assert struct.unpack_from(">H", data, 2 * (destination - 28))[0] != {29: 0x2000, 30: 0x5000, 31: 0x4000, 32:0x4000,34:0x4000}[destination], (
                "LFO did not reach new destination", slot, stop,
                [part.hex() for part in results[3 * k + 2].reads])
        # The stock comparison uses a native bank LFO here, so its packed slot
        # may exceed 1023. Stock regression is a separate matched-V1 test,
        # not the old TUNE2-only fixture's unrelated range assertion.
        if typ == machine_type:
            other = 30 if destination == 29 else 29
            assert struct.unpack_from(">H", data, 2 * (other - 28))[0] == {29: 0x2000, 30: 0x5000}[other], data.hex()
        observed.append({"type": typ, "input": slot, "after_native_lfo_reconstruction": actual,
                         "destination":destination,"modulated_raw":struct.unpack_from(">H",data,2*(destination-28))[0],
                         "lfo_modulated_tune2": struct.unpack_from(">H", data, 6)[0]})
    return {"status": "PASS", "sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
            "route": "0x4002e8c0..0x4002e9a0, native smoother/repack/LFO/modulation/reconstruction",
            "observations": observed, "peripheral_or_hardware": "not executed"}


if __name__ == "__main__":
    ap = argparse.ArgumentParser(__doc__)
    ap.add_argument("--stop", type=lambda s: int(s, 0), default=0x4002E9A0)
    for name in ("image", "state", "tools", "report"):
        ap.add_argument("--" + name, type=Path, required=True)
    a = ap.parse_args()
    report = run(a.image, a.state, a.tools, a.stop)
    a.report.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
