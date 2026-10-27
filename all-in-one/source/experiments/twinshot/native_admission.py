"""Bounded TWINSHOT admission checks; never changes or emits firmware.

Run with the existing digikit environment/import root. The slot records are
synthetic inputs to the actual retained-image slot-reader instructions. This
checks address resolution, not playback, p-lock timing, or sample lifetime.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
from pathlib import Path

from tools.patchkit.port_resolve import sections_of
from tools.sharc_dev import sharc_run as sr
from tools.sharc_dev import sharc_trace as st
from tools.sharc_dev.sharcldr import LoadedMemory

CANDIDATE_SHA = "2d55bd4bbd0edb9df9e39cb5ad8d45ceb893b049afe41927a1ba8db9078686dd"
STOCK_DSP_SHA = "0f514a12a2255f5c081e292c47f1f29462003177658da4bbae0a22fd737fffa2"
SY_SHA = "266a5a3022ff565bf470173147b7f3005b7da8ef80724b6271836818ac2b5828"
TABLE = 0x257810
SAMPLE_BASE = 0x8422B9C8
READER = 0x1C3F78
STACK = 0x2A0000
OUT = 0x2E0000


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_word(state: st.State, address: int) -> int:
    value = st._dm_read(state, address, 4)
    if value is None or not isinstance(value, st.Const):
        raise AssertionError(f"non-concrete word at {address:#x}: {value}")
    return value.value & 0xFFFFFFFF


def table_bytes(state: st.State) -> bytes:
    return b"".join(struct.pack("<I", read_word(state, TABLE + n * 4))
                    for n in range(0x401 * 5))


def run(candidate: Path, stock_blob: Path, syntakt: Path) -> dict:
    candidate_before = candidate.read_bytes()
    assert digest(candidate_before) == CANDIDATE_SHA, "candidate identity changed"
    stock_data = stock_blob.read_bytes()
    assert digest(stock_data) == STOCK_DSP_SHA, "stock DSP identity changed"
    sy_data = syntakt.read_bytes()
    assert digest(sy_data) == SY_SHA, "Syntakt reference identity changed"

    sections = sections_of(candidate)
    memory = LoadedMemory.from_stream(sections[7])
    stock = LoadedMemory.from_stream(stock_data)
    # Verify the actual selected image, rather than borrowing the stock DB's
    # addresses without checking them. The custom hook at 0x1c4f15 is outside
    # these spans; the native sample renderer body remains the reference.
    spans = {
        "slot_reader_and_writer": (0x1C3F78, 0x1C400F),
        "native_sample_setup": (0x1C4914, 0x1C4ECF),
        "native_sample_render_body": (0x1C4F81, 0x1C5576),
        "native_load_branch": (0x1C3329, 0x1C33BC),
    }
    native = {}
    for name, (lo, hi) in spans.items():
        actual = memory.read_sw(lo, (hi - lo) * 2)
        expected = stock.read_sw(lo, (hi - lo) * 2)
        assert actual and actual == expected, f"native code changed: {name}"
        native[name] = {"start_sw": hex(lo), "end_sw_exclusive": hex(hi),
                        "sha256": digest(actual)}

    runner = sr.Runner(memory, READER, explicit_memory_model=True,
                       regs={"I6": STACK, "I7": STACK - 8})
    records = {
        7: (0x20, 0x10020, 1024, 48000, 1),
        1023: (0x20020, 0x20020, 2048, 44100, 0),
    }
    for slot, record in records.items():
        for offset, word in enumerate(record):
            st._dm_write(runner.state, TABLE + slot * 20 + offset * 4, 4, st.Const(word))
    original_table = table_bytes(runner.state)
    observed = {}
    # Reuse the guest state across alternating lookups; this tests that the
    # second lookup neither rewrites the table nor leaks the preceding result.
    sequence = (7, 1023, 7, 1023, 0x401, 0xFFFFFFFF)
    for slot in sequence:
        current = runner.fresh_call(READER, regs={"R4": slot, "R1": OUT})
        result = current.run(max_steps=500)
        assert result.halt.reason == "return without followed call", result.halt
        values = tuple(read_word(current.state, OUT + 4 * n) for n in range(5))
        if slot in records:
            left, right, length, rate, stereo = records[slot]
            expected = (SAMPLE_BASE + left, SAMPLE_BASE + right, length // 2, rate, stereo)
        else:
            expected = tuple(read_word(current.state, 0x25C914 + 4 * n) for n in range(5))
        assert values == expected, (slot, values, expected)
        assert table_bytes(current.state) == original_table, "slot reader rewrote table"
        observed[str(slot)] = {"values": list(values), "instructions": result.instructions}
        runner = current

    # The Syntakt CPU2 entry tables are data read from the actual 1.40A input.
    # Its raw CPU2 image uses the 0x40000400 address origin; these are metadata,
    # never copied code. A ColdFire entry point is not a SHARC implementation.
    sy_sections = sections_of(syntakt)
    cpu2 = sy_sections[7]
    render = struct.unpack_from(">12I", cpu2, 0x400148F0 - 0x40000400)
    update = struct.unpack_from(">12I", cpu2, 0x40014920 - 0x40000400)
    assert all(0x40000400 <= pc < 0x40000400 + len(cpu2) for pc in render + update)
    assert render[11] == 0x40009A8A and update[11] == 0x400097DC

    assert candidate.read_bytes() == candidate_before
    assert syntakt.read_bytes() == sy_data
    return {
        "admission_status": "PASS",
        "feature_status": "NOT_IMPLEMENTED",
        "evidence": "retained-image SHARC execution with synthetic slot metadata; no playback claim",
        "candidate_sha256": CANDIDATE_SHA,
        "candidate_dsp_sha256": digest(sections[7]),
        "native_code_spans_unchanged": native,
        "slot_reader_sequence": list(sequence),
        "slot_reader_cases": observed,
        "slot_table_unchanged": True,
        "syntakt_140a": {"sha256": SY_SHA, "cpu2_sha256": digest(cpu2),
                          "engine_11_render": hex(render[11]),
                          "engine_11_update": hex(update[11])},
        "unestablished": [
            "trigger-effective second-slot value before smoothing, including sequencer p-locks",
            "integrated independent stereo stream setup, release/retrigger and sample lifetime",
            "sample-2 loop policy tied to the effective DT2 AMP state",
            "target CPU cost and end-to-end machine/persistence regressions",
        ],
        "firmware_written": False,
        "hardware": "UNTESTED",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--stock-blob", type=Path, required=True)
    parser.add_argument("--syntakt", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    report = run(args.candidate, args.stock_blob, args.syntakt)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
