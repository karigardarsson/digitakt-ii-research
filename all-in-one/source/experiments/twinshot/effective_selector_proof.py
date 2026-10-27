"""Stored Pattern locks -> native step list -> effective raw selector proof.

Runs actual latest MAIN instructions on the existing cached native ColdFire
fixture. No direct writes of lock-effective values, no guessed lock flag, no
device/project access and no firmware output. Not a clock-driven sequencer run.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
from pathlib import Path

from tools.patchkit import pk_cfcall, port_resolve
from tools.patchkit import pk_integrate_dv_zoom as integration

SHA = "b9ee18b521d1dc5bd39b9e882dfa7ce4ab8bdfa9be692d63182748a95a5be740"
PATTERN, SOUND = 0x47000000, 0x47F08000
STORE, LIST = 0x47F10000, 0x47F18000
RAW, SMOOTH, FIXED = 0x80003340, 0x80005B50, 0x8000DD40
LOCKS = PATTERN + 0x17E50
TRACK = 3


def words(*xs):
    return struct.pack(">" + "I" * len(xs), *(x & 0xFFFFFFFF for x in xs))


def checked(results):
    for r in results:
        assert r.ok and not r.clobbered, (hex(r.pc), r.error, r.clobbered)
    return results


def call(pc, args, reads=(), bound=300000):
    return {"pc": pc, "args": args, "reads": list(reads), "max": bound}


def run(candidate, state):
    raw_file = candidate.read_bytes()
    assert hashlib.sha256(raw_file).hexdigest() == SHA
    main = port_resolve.sections_of(candidate)[3]
    # The conversion is read from the actual instructions' table, not a
    # historical parameter-number assumption.
    ids = struct.unpack_from(">77I", main, 0x40213B64 - 0x40000400)
    stored_ids = {idx: ids.index(idx) for idx in (25, 28, 31, 32)}
    # The harness itself is unchanged. Reuse its retained executable explicitly
    # (there is no cargo on this WSL PATH); record identity, do not claim rebuild.
    assert pk_cfcall.EXAMPLE.is_file(), "retained native cf_call missing"
    harness_sha = hashlib.sha256(pk_cfcall.EXAMPLE.read_bytes()).hexdigest()
    pk_cfcall._built = True
    native = pk_cfcall.CfMachine("Digitakt_II_OS1.17.syx", state=state, timeout=120)
    # Copy native MAIN code; no custom DSP or hook installed. Function calls
    # below do not execute module CF expansion paths.
    pokes = {0x40000400: main[:integration.STOCK_MAIN_END - 0x40000400],
             PATTERN: bytes(0x1DB8C), SOUND: bytes(0x450),
             STORE: b"\xff" * (80 * 0x102), LIST: bytes(0x300),
             0x47DB461C: bytes(16 * 12)}
    segments, _ = integration.parsed_segments(main)
    for src, size, dest, bss in segments:
        pokes[dest] = integration.at(main, src + 12, size) + bytes(bss)
    # Initialize every track with distinctive raw values, preserving neighbors
    # as canaries. Persistent Sound holds unlocked values, never lock values.
    persistent = bytearray(0x450)
    for idx in range(71):
        struct.pack_into(">h", persistent, 0x14 + 2 * idx, (idx + 1) * 17)
    for idx, value in {25: 7, 28: 31, 31: 0x4000, 32: 0x4000}.items():
        struct.pack_into(">h", persistent, 0x14 + 2 * idx, value)
    pokes[SOUND] = bytes(persistent)
    # SRAM must be initialized by guest code; the native harness deliberately
    # restricts host pokes to SDRAM. Use the existing memset/memcpy routines.
    init_calls = [call(0x401363D0, [FIXED, 0, 0x1320])]
    init_calls.extend(call(0x40136388, [RAW + 0x22 + t * 0x8E, SOUND + 0x14, 0x8E])
                      for t in range(16))
    # Legal on-disk lock lanes: adjacent locked/unlocked steps, endpoints and
    # independent pitch-like lanes, plus another track's sample lock.
    fixtures = [
        (TRACK, 25, {0: 12, 2: 43, 3: 1023, 127: 7}),
        (TRACK, 28, {0: 31, 1: 7, 3: 1023, 127: 43}),
        (TRACK, 31, {0: 0x4800, 2: 0x3800}),
        (TRACK, 32, {1: 0x5000, 2: 0x3000}),
        (TRACK + 1, 25, {0: 99}),
    ]
    storage = bytearray(pokes[STORE])
    for lane, (track, idx, vals) in enumerate(fixtures):
        off = lane * 0x102
        storage[off:off + 2] = bytes((stored_ids[idx], track))
        for step, value in vals.items():
            struct.pack_into(">h", storage, off + 2 + step * 2, value)
    pokes[STORE] = bytes(storage)
    calls = [call(0x400DC60A, [LOCKS, STORE], [(LOCKS + 0x50A0, 16 * 71)])]
    sequence = (0, 1, 2, 3, 4, 0, 127, 4)
    for step in sequence:
        calls.extend([
            # This is the native dirty-lock restore used at vector 191 +e588.
            call(0x400D9210, [TRACK, RAW, SOUND]),
            # This is the real step-list builder called at 0x40120300.
            call(0x400D6916, [TRACK, LIST, LOCKS, SOUND + 0x14, step, step, 0],
                 [(LIST, 0x100)]),
            # This is the real event-list consumer at 0x4002e358 / e5d2.
            call(0x400D9368, [TRACK, LIST, RAW]),
            call(0x400D9406, [RAW], [
                (RAW + 0x22, 16 * 0x8E), (SOUND, 0x450),
                (SMOOTH + TRACK * 0x8E + 0x22, 71 * 2),
                (0x47DB461C + TRACK * 12, 12)]),
        ])
    results = checked(native.run(pokes, init_calls + calls))[len(init_calls):]
    assert results[0].d0 == 1
    index_map = results[0].reads[0]
    for lane, (track, idx, _) in enumerate(fixtures):
        assert index_map[track * 71 + idx] == lane
    observed = []
    for n, step in enumerate(sequence):
        listing = results[2 + 4 * n].reads[0]
        count = struct.unpack_from(">I", listing, 8)[0]
        entries = [struct.unpack_from(">hh", listing, 20 + k * 8) for k in range(count)]
        row, sound_after, smooth, dirty = results[4 + 4 * n].reads
        expected = bytearray(persistent[0x14:0xA2])
        for track, idx, vals in fixtures:
            if track == TRACK and step in vals:
                struct.pack_into(">h", expected, 2 * idx, vals[step])
        actual = row[TRACK * 0x8E:(TRACK + 1) * 0x8E]
        assert actual == expected, (step, entries, actual.hex(), expected.hex())
        for t in range(16):
            if t != TRACK:
                assert row[t * 0x8E:(t + 1) * 0x8E] == persistent[0x14:0xA2], t
        assert sound_after == persistent, "native lock delivery changed persistent Sound"
        observed.append({"step": step, "native_lock_list": entries,
                         "effective_slots": [struct.unpack_from(">h", actual, 2 * i)[0] for i in (25, 28)],
                         "smoothed_slots": [struct.unpack_from(">h", smooth, 2 * i)[0] for i in (25, 28)],
                         "dirty_words": list(struct.unpack(">3I", dirty))})
    assert candidate.read_bytes() == raw_file
    return {"status": "PASS", "candidate_sha256": SHA, "stored_ids": stored_ids,
            "retained_cf_call_sha256": harness_sha,
            "evidence": "native ColdFire calls over generated legal Pattern lock storage; not clock-driven sequencer",
            "sequence": observed, "other_tracks_unchanged": True, "persistent_sound_unchanged": True,
            "stale_lock_restore": "PASS", "native_instructions": sum(r.insns for r in results),
            "firmware_written": False, "hardware": "UNTESTED"}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--candidate", type=Path, required=True)
    p.add_argument("--state", type=Path, required=True)
    p.add_argument("--report", type=Path, required=True)
    a = p.parse_args()
    report = run(a.candidate, a.state)
    a.report.parent.mkdir(parents=True, exist_ok=True)
    a.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
