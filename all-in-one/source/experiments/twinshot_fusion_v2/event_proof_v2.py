"""Native pattern trigger producer -> real vector-191 lock consumer.

Generated stored locks and stock initialized pattern/kit templates. Interrupts
and clock scheduling are not reproduced; the real producer creates the event.
Only a caller frame and an exit breakpoint jump are supplied by the fixture.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import struct
import subprocess
import tempfile
from pathlib import Path

from effective_selector_proof import SHA, call, checked, words

from tools.emulator.snapread import Snapshot
from tools.patchkit import pk_cfcall, port_resolve
from tools.patchkit import pk_integrate_dv_zoom as integration

PAT, KIT, STORE, EVENT, LIST = 0x47000000, 0x47F30000, 0x47F10000, 0x47F18000, 0x47F18400
TEXT, TRACK = 0x47F20000, 3
RAW = 0x80003340


def caller(tool_root):
    tool_root = tool_root.resolve()
    env = dict(os.environ)
    env["LD_LIBRARY_PATH"] = str(tool_root / "usr/lib/x86_64-linux-gnu")
    prefix = tool_root / "usr/bin/m68k-linux-gnu-"
    with tempfile.TemporaryDirectory(prefix="twinshot-cf-") as d:
        obj, elf, binary = (Path(d) / s for s in ("caller.o", "caller.elf", "caller.bin"))
        subprocess.run([str(prefix) + "as", "-mcfv4e", "-o", str(obj),
                        str(Path(__file__).parent.parent / "twinshot/vector_fixture.s")], check=True, env=env)
        subprocess.run([str(prefix) + "ld.bfd", f"-Ttext={TEXT:#x}", "-e", "tw_vector_fixture",
                        "-o", str(elf), str(obj)], check=True, env=env)
        subprocess.run([str(prefix) + "objcopy", "-O", "binary", str(elf), str(binary)], check=True, env=env)
        symbols = subprocess.check_output([str(prefix) + "nm", "-g", str(elf)], env=env, text=True)
        names = {line.split()[2]: int(line.split()[0], 16) for line in symbols.splitlines()
                 if len(line.split()) == 3 and line.split()[1].upper() == "T"}
        return binary.read_bytes(), names


def run(candidate, state, tool_root, *, expected_sha=SHA, selectors=(25, 28),
        machine_type=None, smoother=0x400D9406, defaults=(7, 31), lanes=None, discrete_selectors=None):
    if discrete_selectors is None:
        discrete_selectors = selectors
    original = candidate.read_bytes()
    assert hashlib.sha256(original).hexdigest() == expected_sha
    sections = port_resolve.sections_of(candidate)
    main = sections[3]
    snapshot = Snapshot(str(state))
    source_pattern = snapshot.u32(0x4316501C)
    source_kit = snapshot.u32(0x80004704)
    pattern = bytearray(snapshot.read(source_pattern, 0x1DB8C))
    kit = bytearray(snapshot.read(source_kit, 0x5574))
    persistent_sound = bytearray(kit[0x34 + TRACK * 0x450:0x34 + (TRACK + 1) * 0x450])
    if machine_type is not None:
        persistent_sound[0xA2] = machine_type
    for idx, value in zip(selectors, defaults, strict=True):
        struct.pack_into(">h", persistent_sound, 0x14 + idx * 2, value)
    kit[0x34 + TRACK * 0x450:0x34 + (TRACK + 1) * 0x450] = persistent_sound
    sound = KIT + 0x34 + TRACK * 0x450
    table = struct.unpack_from(">77I", main, 0x40213B64 - 0x40000400)
    storage = bytearray(b"\xff" * (80 * 258))
    if lanes is None:
        lanes = ({0: 1036, 2: 43}, {0: 31, 1: 1031}) if machine_type else ({0: 12, 2: 43}, {0: 31, 1: 7})
    for lane, (idx, values) in enumerate(zip(selectors, lanes, strict=True)):
        offset = lane * 258
        storage[offset:offset + 2] = bytes((table.index(idx), TRACK))
        for step, value in values.items():
            struct.pack_into(">h", storage, offset + 2 + 2 * step, value)
    # Only documented step flags and native lock-presence index are changed in
    # the copied initialized track. The producer derives all event flags itself.
    for step in range(4):
        struct.pack_into(">H", pattern, TRACK * 0x6A5 + 2 * step, 1)
        pattern[0x1D360 + TRACK * 128 + step] = int(step != 3)
    code, symbols = caller(tool_root)
    pokes = {0x40000400: main[:integration.STOCK_MAIN_END - 0x40000400],
             PAT: bytes(pattern), KIT: bytes(kit), STORE: bytes(storage),
             EVENT: bytes(0x100), EVENT + 0x54: words(LIST), LIST: bytes(0x300),
             TEXT: code, 0x47F19000: bytes(0x40), 0x47DB461C: bytes(16 * 12),
             0x4002E5DC: struct.pack(">HI", 0x4EF9, symbols["tw_vector_fixture_done"])}
    segments, _ = integration.parsed_segments(main)
    for src, size, dest, bss in segments:
        pokes[dest] = integration.at(main, src + 12, size) + bytes(bss)
    calls = [call(0x400DC60A, [PAT + 0x17E50, STORE]),
             call(0x4002D438, [sound, TRACK])]
    sequence = (0, 1, 2, 3, 0, 3)
    for step in sequence:
        calls.extend([
            # Caller-provided reusable event/list is an explicitly supported
            # branch. Native code fills event, time, note, Sound and lock list.
            call(0x400D6982, [TRACK, PAT, KIT, step, EVENT, 0, 0],
                 [(EVENT, 0x6C), (LIST, 0x100)]),
            call(symbols["tw_vector_fixture"], [TRACK, EVENT],
                 [(RAW + 0x22 + TRACK * 0x8E, 0x8E), (sound, 0x450), (0x47F19020, 4)]),
            call(smoother, [RAW], [(0x80005B50 + 0x22 + TRACK * 0x8E, 0x8E)]),
        ])
    pk_cfcall._built = True  # explicit reuse; no harness/Rust source was changed
    machine = pk_cfcall.CfMachine("Digitakt_II_OS1.17.syx", state=state, timeout=120)
    results = checked(machine.run(pokes, calls))
    observations = []
    for n, step in enumerate(sequence):
        produced, consumed, smoothed = results[2 + n * 3:5 + n * 3]
        assert produced.d0 == EVENT, (step, "native producer rejected step", produced.d0)
        event, locks = produced.reads
        assert struct.unpack_from(">I", event, 0x54)[0] == LIST
        flags = struct.unpack_from(">I", event, 0x38)[0]
        assert flags & 0x400, (step, "native event lacks trigger flag", hex(flags))
        count = struct.unpack_from(">I", locks, 8)[0]
        entries = [struct.unpack_from(">hh", locks, 20 + 8 * k) for k in range(count)]
        row, sound_after, mask = consumed.reads
        slots = [struct.unpack_from(">h", row, 2 * idx)[0] for idx in selectors]
        expected = [values.get(step, default) for values, default in zip(lanes, defaults, strict=True)]
        assert slots == expected, (step, slots, expected)
        if machine_type:
            assert smoothed.d0 == 0x80005B50, "lost smoother return pointer needed by vector 191"
            final_slots = [struct.unpack_from(">h", smoothed.reads[0], 2 * idx)[0] for idx in selectors]
            assert all(final_slots[k] == slots[k] for k, idx in enumerate(selectors) if idx in discrete_selectors), (step, "discrete adapter lost effective locks", final_slots, slots)
        assert sound_after == persistent_sound, "event modified persistent Sound"
        assert struct.unpack(">I", mask)[0] == 1 << TRACK
        observations.append({"step": step, "producer_flags": hex(flags),
                             "native_lock_list": entries, "effective_slots": slots,
                             "vector_trigger_mask": 1 << TRACK})
    assert candidate.read_bytes() == original
    return {"status": "PASS", "candidate_sha256": expected_sha,
            "native_producer": "0x400d6982", "vector_region": "0x4002e4a0..0x4002e5dc",
            "sequencer_clock": "not emulated", "observations": observations,
            "persistent_sound": "unchanged", "firmware_written": False,
            "caller_code_sha256": hashlib.sha256(code).hexdigest()}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("candidate", "state", "tool-root", "report"):
        p.add_argument("--" + name, type=Path, required=True)
    a = p.parse_args()
    report = run(a.candidate, a.state, a.tool_root)
    a.report.parent.mkdir(parents=True, exist_ok=True)
    a.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
