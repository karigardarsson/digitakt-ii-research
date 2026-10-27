"""Compile and execute the dual-native C prototype; never package firmware."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import struct
from pathlib import Path

from native_dual_proof import OUT, PRIVATE, SHA, STACK, Native

from tools.patchkit import pk_sharc
from tools.patchkit.port_resolve import sections_of
from tools.sharc_dev.sharcldr import LoadedMemory

CODE = 0xBF0000
CONTROL = OUT + 0x600
STATE_BYTES = 4 * 118 * 2 + 7 * 4 + 64 * 4
GENERATIONS = 0x200ED000
THUNK = """
.SECTION/PM seg_pmco;
.GLOBAL tw_slot.;
.EXTERN _native_slot;
.GLOBAL tw_guarded.;
.EXTERN twinshot_channel.;
.EXTERN _tw_save;
.EXTERN _tw_stack;
.EXTERN _tw_done;
.NOCOMPRESS;
tw_slot.:
    R1 = R8;
    JUMP _native_slot;
// Same interrupt-safe stack-switch ordering as the existing Plaits shim.
// The native arm callee additionally requires I13 preserved by this boundary.
tw_guarded.:
    I3 = _tw_save;
    R0 = I6;
    DM(I3, M6) = R0;
    R0 = I7;
    DM(I3, M6) = R0;
    R0 = L6;
    DM(I3, M6) = R0;
    R0 = L7;
    DM(I3, M6) = R0;
    R0 = I13;
    DM(I3, M6) = R0;
    R0 = M12;
    DM(I3, M6) = R0;
    L6 = 0;
    L7 = 0;
    R0 = _tw_stack;
    R1 = 4092;
    R0 = R0 + R1;
    I7 = R0;
    R2 = I6;
    CJUMP twinshot_channel. (DB);
    DM(I7, M7) = R2;
    DM(I7, M7) = tw_guarded_back - 1;
tw_guarded_back:
    I3 = _tw_save;
    R0 = DM(I3, M6);
    I6 = R0;
    R0 = DM(I3, M6);
    I7 = R0;
    R0 = DM(I3, M6);
    L6 = R0;
    R0 = DM(I3, M6);
    L7 = R0;
    R0 = DM(I3, M6);
    I13 = R0;
    R0 = DM(I3, M6);
    M12 = R0;
    JUMP _tw_done;
"""


def build(memory):
    c = Path(__file__).with_name("dual_native.c").read_text()
    lifecycle = Path(__file__).with_name("lifecycle.s").read_text()
    obj = pk_sharc.merge([pk_sharc.assemble(THUNK + lifecycle), pk_sharc.compile_c(c)])
    externs = {"_native_slot": 0x1C3F78, "tw_load.": 0x1C4E70,
               "tw_arm.": 0x1C4EAF, "tw_pitch.": 0x1C4AFE, "tw_sample.": 0x1C4ECF,
               "_tw_save": 0x200EB000, "_tw_stack": 0x200EC000, "_tw_done": 0x1C6B06,
               "_tw_generations": GENERATIONS, "tw_generations.": GENERATIONS,
               "_tw_header_body": 0x1C3FE8,
               "_tw_header_return": 0x1C400A}
    linked = pk_sharc.link(obj, CODE, externs)
    pk_sharc.verify(obj, linked, externs)
    blocks = [dict(b) for b in memory.blocks]
    blocks.append({"target_address": 0x28000000 + 2 * CODE,
                   "byte_count": len(linked.code_boot), "fill": False,
                   "payload_offset": len(memory.data), "payload_len": len(linked.code_boot),
                   "index": len(blocks)})
    data = memory.data + linked.code_boot
    for site, target, size in ((0x1C3FE5, "_tw_header_begin", 6),
                               (0x1C4006, "_tw_header_end", 8)):
        jump = pk_sharc.assemble(".SECTION/PM seg_pmco; .NOCOMPRESS; JUMP _target;")
        patch = pk_sharc.link(jump, site, {"_target": linked.symbols[target]}).code_boot
        patch += bytes(size - len(patch))  # unreachable short NOP padding
        blocks.append({"target_address": 0x28000000 + 2 * site, "byte_count": size,
                       "fill": False, "payload_offset": len(data), "payload_len": size,
                       "index": len(blocks)})
        data += patch
    return LoadedMemory(data, blocks), linked


def control(n, channel=0, slots=(7, 31), ratios=(1, 2), mix=0.5,
            reverse=(0, 1), loop=(1, 1), trig=0, release=0,
            note=60, decay=127, filter=64):
    tunes = [64 + 12 * math.log2(r) for r in ratios]
    buf = struct.pack("<2i3f7i3f", *slots, *tunes, mix, *reverse, *loop,
                      trig, release, channel, note, decay, filter)
    for k, value in enumerate(buf):
        n.put(CONTROL + k, value, 1)


def run(candidate):
    raw = candidate.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == SHA
    memory = LoadedMemory.from_stream(sections_of(candidate)[7])
    memory, linked = build(memory)
    # Native end-of-sample fade uses RECIPS. The runner deliberately leaves
    # that unknown unless its documented approximate reciprocal mode is opted
    # into. Record that evidence limitation; no native call is mocked/skipped.
    n = Native(memory, approx_recips=True)
    for a in range(GENERATIONS, GENERATIONS + 0x401 * 4, 4):
        n.put(a, 0)
    n.load_slots()
    # Private control/state are diagnostic allocations, not a production plan.
    state = PRIVATE
    states = [state + j * STATE_BYTES for j in range(3)]
    for a in range(state - 16, state + STATE_BYTES * 3 + 16, 4):
        n.put(a, 0 if state <= a < state + STATE_BYTES * 3 else 0xA5A5A5A5)
    stock_before = n.data(0x2412CC, 32 * 0x1D8)
    # Known register sentinels expose the extra ABI obligations introduced by
    # calling native routines (unlike Plaits' self-contained C wrapper).
    protected = ("I8", "I9", "I10", "I11", "I13", "I14", "I15",
                 "M0", "M8", "M9", "M10", "M11", "M12", "B3", "MODE1")
    sentinels = {name: 0x100 + k for k, name in enumerate(protected)}
    sentinels["MODE1"] = 0  # normal scalar caller, not an invented SIMD mode
    out = OUT + 0x800
    costs, peaks, abi = [], [], set()
    cases = [
        {"trig": 1}, {}, {"slots": (31, 7)},
        {"slots": (31, 7), "trig": 1}, {"release": 1},
        {"trig": 1, "release": 1},
        {"slots": (0x401, 7), "trig": 1},
        {"trig": 1, "ratios": (0.5, 4), "reverse": (1, 0)},
        {"ratios": (0.5, 4), "reverse": (1, 0)},
    ]
    from tools.sharc_dev import sharc_trace as st
    for block, cfg in enumerate(cases):
        rendered = []
        for j, current in enumerate(states):
            before = [n.data(s, STATE_BYTES) for s in states]
            control(n, mix=(0, 1, 0.5)[j], **cfg)
            # Reset the sentinels only, never any native record or cursor.
            n.call(linked.symbols["tw_guarded."],
                   {"R4": current, "R8": CONTROL, "R12": out, **sentinels}, bound=40000)
            for name, expected in (("I6", STACK), ("I7", STACK - 8), ("L6", 0), ("L7", 0)):
                assert n.runner.state.uregs[st.UREG_CODES[name]].value == expected, name
            for name, value in sentinels.items():
                got = n.runner.state.uregs.get(st.UREG_CODES[name])
                if not isinstance(got, st.Const) or got.value & 0xFFFFFFFF != value:
                    abi.add(name)
            samples = [struct.unpack("<f", struct.pack("<I", n.get(out + 4 * k)))[0] for k in range(32)]
            assert all(map(math.isfinite, samples))
            if block == 6 and j == 0:
                assert not any(samples), "invalid slot did not silence its layer"
            else:
                assert any(samples), (block, j, "unexpected silence")
            for k, other in enumerate(states):
                if k != j:
                    assert n.data(other, STATE_BYTES) == before[k], "state leaked between instances"
            if block == 2:
                assert n.get(current + 944 + 4) == 7, "untriggered slot edit reloaded the active sample"
            if block in (4, 5):
                assert n.get(current + 0x1BC, 1) == int(block == 5), "release/trig precedence"
            rendered.append(samples)
            costs.append(n.cost[-1][1])
            peaks.append(max(map(abs, samples)))
        expected = [struct.unpack('<f', struct.pack('<f', 0.5 * a + 0.5 * b))[0]
                    for a, b in zip(rendered[0], rendered[1], strict=True)]
        assert rendered[2] == expected, (block, "DSP mixer does not match isolated layer outputs")
    assert n.data(state - 16, 16) == b"\xa5" * 16
    assert n.data(state + STATE_BYTES * 3, 16) == b"\xa5" * 16
    assert n.data(0x2412CC, 32 * 0x1D8) == stock_before, "stock voice records changed"
    assert not abi, ("unpreserved native-call ABI", sorted(abi))
    entry = linked.symbols["tw_guarded."]

    def one(**cfg):
        control(n, mix=0.5, **cfg)
        n.call(entry, {"R4": state, "R8": CONTROL, "R12": out, **sentinels}, bound=40000)
        return [n.get(out + 4 * k) for k in range(32)]

    # Exercise real end/wrap branches, not just the loop flag or steady PCM.
    loop_cases = []
    for reverse, near in (((0, 0), 2044), ((1, 1), 4)):
        one(trig=1, reverse=reverse, ratios=(1, 1))
        for rec in (state, state + 472):
            phase = near << 31
            n.put(rec + 0x1B0, phase)
            n.put(rec + 0x1B4, phase >> 32)
        one(reverse=reverse, ratios=(1, 1))
        phases = [n.q31(rec + 0x1B0) / (1 << 31) for rec in (state, state + 472)]
        assert all(0 <= p < 2048 for p in phases), (reverse, phases)
        assert phases == ([28, 28] if not reverse[0] else [2020, 2020]), (
            phases, [n.q31(rec + 0x1A8) for rec in (state, state + 472)],
            [n.q31(state + x) / (1 << 31) for x in (0x190, 0x198, 0x1A0)],
            n.data(state + 0x1B8, 8).hex(), n.data(CONTROL, 60).hex())
        # Reposition only the test cursor so release reaches the end in one
        # bounded block. All wrapping/ending instructions are actual firmware.
        for rec in (state, state + 472):
            phase = near << 31
            n.put(rec + 0x1B0, phase)
            n.put(rec + 0x1B4, phase >> 32)
        for _ in range(3):
            ended = one(reverse=reverse, ratios=(1, 1), release=1)
        assert not any(ended), (reverse, "release/end did not silence output")
        assert n.get(state + 0x1B8, 1) == n.get(state + 472 + 0x1B8, 1) == 0
        loop_cases.append({"reverse": reverse, "wrapped_phases_frames": phases,
                           "release_end_silent": True})
    one(trig=1)
    # Actual native header writer, not a host edit of private voice state.
    n.call(0x1C3FE5, {"R4": 7, "R8": 0xFFFFFC00, "R12": 0xFFFFFC00}, (0, 0, 0))
    one()
    assert n.get(state) == n.get(state + 0x1B8, 1) == 0, "cleared slot kept a private reader alive"
    assert n.get(state + 472 + 0x1B8, 1) == 1, "clearing layer 1 killed layer 2"
    n.call(0x1C3FE5, {"R4": 7, "R8": 0x70000, "R12": 0x74000}, (4096, 48000, 1))
    one()
    assert n.get(state) == 0, "slot reload unexpectedly resumed the old cursor"
    one(trig=1)
    assert n.get(state) != 0 and n.get(state + 0x1B8, 1) == 1
    # Actual native reset + identical restoration wholly between blocks.
    layer2_before = n.q31(state + 472 + 0x1B0)
    n.call(0x1C3FE5, {"R4": 7, "R8": 0xFFFFFC00, "R12": 0xFFFFFC00}, (0, 0, 0))
    n.call(0x1C3FE5, {"R4": 7, "R8": 0x70000, "R12": 0x74000}, (4096, 48000, 1))
    one()
    assert n.get(state) == n.get(state + 0x1B8, 1) == 0
    assert n.get(state + 472 + 0x1B8, 1) == 1
    assert n.q31(state + 472 + 0x1B0) != layer2_before
    one()
    assert n.get(state) == 0, "invalidated reader resumed without a trigger"
    one(trig=1)
    assert n.get(state) != 0 and n.get(state + 0x1B8, 1) == 1
    lifetime_counterexample = {"result": "FIXED_EMULATOR",
                              "reset_and_same_header_restore_between_blocks": True,
                              "old_private_cursor_continues": False,
                              "other_layer_continues": True,
                              "new_trigger_rearms": True}
    assert n.data(state - 16, 16) == n.data(state + STATE_BYTES * 3, 16) == b"\xa5" * 16
    assert n.data(0x2412CC, 32 * 0x1D8) == stock_before
    assert candidate.read_bytes() == raw
    return {"status": "CORE_AND_BETWEEN_BLOCK_LIFETIME_PASS", "candidate_sha256": SHA,
            "evidence": "compiled custom DSP calls actual native sample setup/render; generated input only",
            "reciprocal_emulation": "documented approx_recips mode; not silicon-exact reciprocal proof",
            "prototype_code_bytes": len(linked.code_boot), "state_bytes_per_channel": STATE_BYTES,
            "prototype_code_sha256": hashlib.sha256(linked.code_boot).hexdigest(),
            "source_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                              for p in (Path(__file__), Path(__file__).with_name("dual_native.c"),
                                        Path(__file__).with_name("lifecycle.s"))},
            "instructions_per_channel_block": costs, "peak": peaks,
            "compiled_instructions_redecoded": linked.checked,
            "isolated_layers_vs_mix": "bit exact", "instances": 3, "control_sequences": len(cases),
            "invalid_slot_fallback": "PASS", "release_and_retrigger": "PASS",
            "stock_voice_records": "unchanged", "native_abi_requires_preservation": sorted(abi),
            "native_loop_boundary_tests": loop_cases,
            "observed_slot_clear_restore_retrigger": "PASS",
            "lifetime_counterexample": lifetime_counterexample,
            "unresolved_lifetime": "interrupt-interleaving test pending",
            "firmware_written": False, "hardware": "UNTESTED"}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--candidate", type=Path, required=True)
    p.add_argument("--report", type=Path, required=True)
    a = p.parse_args()
    report = run(a.candidate)
    a.report.parent.mkdir(parents=True, exist_ok=True)
    a.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
